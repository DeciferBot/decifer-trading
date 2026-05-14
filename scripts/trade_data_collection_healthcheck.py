#!/usr/bin/env python3
"""
trade_data_collection_healthcheck.py

Daily healthcheck for the trade evidence collection pipeline.

Reads (never modified):
  data/trade_events.jsonl                   — ORDER_FILLED, POSITION_CLOSED events
  data/ml/entry_trade_snapshots.jsonl       — canonical entry snapshots
  data/ml/closed_trade_training_ledger.jsonl — canonical closed records
  data/ml/*.jsonl (quarantine files)        — quarantine stats

Writes:
  data/audits/trade_data_collection_healthcheck.json  — machine-readable verdict
  docs/trade_data_collection_healthcheck.md           — human-readable report

Verdicts:
  HEALTHY   — all ORDER_FILLED today → entry snapshots, all POSITION_CLOSED → closed records,
              zero duplicate canonical records, zero critical quarantine
  DEGRADED  — non-critical quarantine (empty signal_scores, UNKNOWN regime), duplicate canonical
              records exist, or minor missing optional fields
  BROKEN    — ORDER_FILLED today but zero entry snapshots, OR POSITION_CLOSED today but zero
              closed records, OR fresh (today) duplicate canonical records

Exit codes: 0=HEALTHY, 1=DEGRADED, 2=BROKEN

Usage:
  python scripts/trade_data_collection_healthcheck.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# ── Paths ──────────────────────────────────────────────────────────────────────
_ML_DIR    = _REPO / "data" / "ml"
_AUDIT_DIR = _REPO / "data" / "audits"
_DOCS_DIR  = _REPO / "docs"

_EVENTS_FILE        = _REPO / "data" / "trade_events.jsonl"
_SNAPSHOTS_FILE     = _ML_DIR / "entry_trade_snapshots.jsonl"
_CLOSED_FILE        = _ML_DIR / "closed_trade_training_ledger.jsonl"

_QUARANTINE_ENTRY   = _ML_DIR / "quarantine_entry_snapshots.jsonl"
_QUARANTINE_CLOSED  = _ML_DIR / "quarantine_closed_records.jsonl"
_QUARANTINE_MISSING_ENTRY   = _ML_DIR / "quarantine_missing_entry_snapshot.jsonl"
_QUARANTINE_MISSING_OUTCOME = _ML_DIR / "quarantine_missing_outcome.jsonl"
_QUARANTINE_SCHEMA  = _ML_DIR / "quarantine_schema_invalid.jsonl"
_QUARANTINE_DUP     = _ML_DIR / "quarantine_duplicate_trade_id.jsonl"

_REBUILT_FILE       = _ML_DIR / "closed_trade_training_ledger.rebuilt.jsonl"

_HEALTHCHECK_JSON = _AUDIT_DIR / "trade_data_collection_healthcheck.json"
_HEALTHCHECK_MD   = _DOCS_DIR / "trade_data_collection_healthcheck.md"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                try:
                    rows.append(json.loads(stripped))
                except json.JSONDecodeError:
                    pass
    return rows


def _is_today(ts_str: str, today_date: str) -> bool:
    """Check if an ISO timestamp belongs to the given date (YYYY-MM-DD)."""
    if not ts_str:
        return False
    return str(ts_str)[:10] == today_date


def _latest_ts(records: list[dict], ts_field: str = "ts_written") -> str:
    """Return the latest timestamp string from a list of records."""
    ts_values = [r.get(ts_field, "") for r in records if r.get(ts_field)]
    return max(ts_values) if ts_values else ""


def _count_file_lines(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def _file_age_hours(path: Path) -> float | None:
    """Hours since last modification, or None if file doesn't exist."""
    if not path.exists():
        return None
    mtime = os.path.getmtime(path)
    now = datetime.now(UTC).timestamp()
    return (now - mtime) / 3600.0


def _find_duplicates(records: list[dict]) -> list[str]:
    """Return list of trade_ids that appear more than once."""
    seen: dict[str, int] = {}
    for r in records:
        tid = str(r.get("trade_id") or "")
        if tid:
            seen[tid] = seen.get(tid, 0) + 1
    return [tid for tid, count in seen.items() if count > 1]


# ── Core analysis ──────────────────────────────────────────────────────────────

def run_healthcheck(today_date: str) -> dict:
    now_ts = datetime.now(UTC).isoformat()

    # ── Load all data ──────────────────────────────────────────────────────────
    events       = _load_jsonl(_EVENTS_FILE)
    snapshots    = _load_jsonl(_SNAPSHOTS_FILE)
    closed       = _load_jsonl(_CLOSED_FILE)

    q_entry      = _load_jsonl(_QUARANTINE_ENTRY)
    q_closed_rec = _load_jsonl(_QUARANTINE_CLOSED)
    q_missing_entry   = _load_jsonl(_QUARANTINE_MISSING_ENTRY)
    q_missing_outcome = _load_jsonl(_QUARANTINE_MISSING_OUTCOME)
    q_schema     = _load_jsonl(_QUARANTINE_SCHEMA)
    q_dup        = _load_jsonl(_QUARANTINE_DUP)

    rebuilt      = _load_jsonl(_REBUILT_FILE)

    # ── Today's events ─────────────────────────────────────────────────────────
    filled_today = [
        e for e in events
        if e.get("event") == "ORDER_FILLED" and _is_today(e.get("ts", ""), today_date)
    ]
    closed_today_events = [
        e for e in events
        if e.get("event") == "POSITION_CLOSED" and _is_today(e.get("ts", ""), today_date)
    ]
    filled_today_ids  = {str(e.get("trade_id", "")) for e in filled_today if e.get("trade_id")}
    closed_today_ids  = {str(e.get("trade_id", "")) for e in closed_today_events if e.get("trade_id")}

    # ── Today's canonical writes ───────────────────────────────────────────────
    snapshots_today = [
        s for s in snapshots if _is_today(s.get("ts_written", ""), today_date)
    ]
    closed_today_records = [
        c for c in closed if _is_today(c.get("ts_outcome_written", ""), today_date)
    ]
    snapshot_ids     = {str(s.get("trade_id", "")) for s in snapshots if s.get("trade_id")}
    closed_ids       = {str(c.get("trade_id", "")) for c in closed if c.get("trade_id")}
    snapshot_ids_today = {str(s.get("trade_id", "")) for s in snapshots_today if s.get("trade_id")}
    closed_ids_today   = {str(c.get("trade_id", "")) for c in closed_today_records if c.get("trade_id")}

    # ── Coverage gaps ──────────────────────────────────────────────────────────
    filled_missing_snapshot = filled_today_ids - snapshot_ids          # filled today, never got snapshot
    closed_missing_record   = closed_today_ids - closed_ids            # closed today, never got closed record
    filled_missing_today    = filled_today_ids - snapshot_ids_today    # filled today, no snapshot today

    # ── Duplicate detection ────────────────────────────────────────────────────
    dup_snapshot_ids  = _find_duplicates(snapshots)
    dup_closed_ids    = _find_duplicates(closed)

    # Fresh duplicates = written today
    def _fresh_dups(dup_ids: list[str], records: list[dict], ts_field: str) -> list[str]:
        result = []
        for tid in dup_ids:
            recs = [r for r in records if str(r.get("trade_id", "")) == tid]
            if any(_is_today(r.get(ts_field, ""), today_date) for r in recs):
                result.append(tid)
        return result

    fresh_dup_snapshots = _fresh_dups(dup_snapshot_ids, snapshots, "ts_written")
    fresh_dup_closed    = _fresh_dups(dup_closed_ids, closed, "ts_outcome_written")

    # ── Quarantine stats ───────────────────────────────────────────────────────
    q_today_dup = [
        r for r in q_dup
        if _is_today(r.get("quarantine_ts", ""), today_date)
    ]

    # ── Schema quality ─────────────────────────────────────────────────────────
    empty_signal_scores_snapshots = [
        s for s in snapshots if not s.get("signal_scores")
    ]
    unknown_regime_snapshots = [
        s for s in snapshots if s.get("regime") == "UNKNOWN"
    ]
    unknown_regime_today = [
        s for s in snapshots_today if s.get("regime") == "UNKNOWN"
    ]

    # ── File staleness (24h threshold) ─────────────────────────────────────────
    stale_files = []
    for label, path in [("entry_snapshots", _SNAPSHOTS_FILE), ("closed_ledger", _CLOSED_FILE)]:
        age = _file_age_hours(path)
        if age is not None and age > 24:
            stale_files.append({"file": label, "age_hours": round(age, 1)})

    # ── Latest write timestamps ────────────────────────────────────────────────
    latest_snapshot_ts  = _latest_ts(snapshots)
    latest_closed_ts    = _latest_ts(closed, "ts_outcome_written")

    # ── Verdict logic ──────────────────────────────────────────────────────────
    broken_reasons: list[str] = []
    degraded_reasons: list[str] = []

    # Hard rule: ORDER_FILLED today but zero entry snapshots today
    if filled_today and not snapshots_today:
        broken_reasons.append(
            f"ORDER_FILLED events today ({len(filled_today)}) but zero entry snapshots written today"
        )

    # Hard rule: POSITION_CLOSED today but zero closed records today
    if closed_today_events and not closed_today_records:
        broken_reasons.append(
            f"POSITION_CLOSED events today ({len(closed_today_events)}) but zero closed records written today"
        )

    # Coverage gaps for today
    if filled_missing_snapshot:
        broken_reasons.append(
            f"{len(filled_missing_snapshot)} trade_ids filled today have no entry snapshot at all: {sorted(filled_missing_snapshot)[:5]}"
        )
    if closed_missing_record:
        broken_reasons.append(
            f"{len(closed_missing_record)} trade_ids closed today have no closed record: {sorted(closed_missing_record)[:5]}"
        )

    # Fresh duplicates in canonical files → BROKEN
    if fresh_dup_snapshots:
        broken_reasons.append(
            f"Fresh duplicate trade_ids in entry_snapshots today: {fresh_dup_snapshots[:5]}"
        )
    if fresh_dup_closed:
        broken_reasons.append(
            f"Fresh duplicate trade_ids in closed_ledger today: {fresh_dup_closed[:5]}"
        )

    # Stale duplicates in canonical files → DEGRADED (at least)
    if dup_snapshot_ids and not fresh_dup_snapshots:
        degraded_reasons.append(
            f"Existing duplicate trade_ids in entry_snapshots (not fresh): {dup_snapshot_ids[:5]}"
        )
    if dup_closed_ids and not fresh_dup_closed:
        degraded_reasons.append(
            f"Existing duplicate trade_ids in closed_ledger (not fresh): {dup_closed_ids[:5]}"
        )

    # Schema quality issues → DEGRADED
    if empty_signal_scores_snapshots:
        degraded_reasons.append(
            f"{len(empty_signal_scores_snapshots)} entry snapshots have empty signal_scores"
        )
    if unknown_regime_today:
        degraded_reasons.append(
            f"{len(unknown_regime_today)} snapshots written today have regime=UNKNOWN"
        )
    if q_schema:
        degraded_reasons.append(
            f"{len(q_schema)} schema-invalid records in quarantine"
        )
    if q_missing_entry:
        degraded_reasons.append(
            f"{len(q_missing_entry)} records in quarantine_missing_entry_snapshot"
        )

    # Determine final verdict
    if broken_reasons:
        verdict = "BROKEN"
        exit_code = 2
    elif degraded_reasons:
        verdict = "DEGRADED"
        exit_code = 1
    else:
        verdict = "HEALTHY"
        exit_code = 0

    # ── Assemble result ────────────────────────────────────────────────────────
    result = {
        "healthcheck_ts": now_ts,
        "check_date": today_date,
        "verdict": verdict,
        "exit_code": exit_code,
        "broken_reasons": broken_reasons,
        "degraded_reasons": degraded_reasons,
        "metrics": {
            "order_filled_today": len(filled_today),
            "position_closed_today": len(closed_today_events),
            "entry_snapshots_today": len(snapshots_today),
            "closed_records_today": len(closed_today_records),
            "total_entry_snapshots": len(snapshots),
            "total_closed_records": len(closed),
            "total_rebuilt_records": len(rebuilt),
            "filled_missing_snapshot_today": len(filled_missing_snapshot),
            "closed_missing_record_today": len(closed_missing_record),
            "duplicate_snapshot_ids": len(dup_snapshot_ids),
            "duplicate_closed_ids": len(dup_closed_ids),
            "fresh_dup_snapshots": len(fresh_dup_snapshots),
            "fresh_dup_closed": len(fresh_dup_closed),
            "empty_signal_scores_total": len(empty_signal_scores_snapshots),
            "unknown_regime_total": len(unknown_regime_snapshots),
            "unknown_regime_today": len(unknown_regime_today),
            "quarantine_entry": len(q_entry),
            "quarantine_closed": len(q_closed_rec),
            "quarantine_missing_entry": len(q_missing_entry),
            "quarantine_missing_outcome": len(q_missing_outcome),
            "quarantine_schema_invalid": len(q_schema),
            "quarantine_duplicate": len(q_dup),
            "quarantine_duplicate_today": len(q_today_dup),
            "stale_files": stale_files,
            "latest_entry_snapshot_ts": latest_snapshot_ts,
            "latest_closed_record_ts": latest_closed_ts,
        },
        "file_status": {
            "entry_snapshots": str(_SNAPSHOTS_FILE.relative_to(_REPO)),
            "entry_snapshots_exists": _SNAPSHOTS_FILE.exists(),
            "entry_snapshots_lines": _count_file_lines(_SNAPSHOTS_FILE),
            "closed_ledger": str(_CLOSED_FILE.relative_to(_REPO)),
            "closed_ledger_exists": _CLOSED_FILE.exists(),
            "closed_ledger_lines": _count_file_lines(_CLOSED_FILE),
            "rebuilt_ledger": str(_REBUILT_FILE.relative_to(_REPO)),
            "rebuilt_ledger_exists": _REBUILT_FILE.exists(),
            "rebuilt_ledger_lines": _count_file_lines(_REBUILT_FILE),
        },
    }

    return result


# ── Output writers ─────────────────────────────────────────────────────────────

def _write_json(result: dict) -> None:
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with open(_HEALTHCHECK_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


def _write_markdown(result: dict) -> None:
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)
    m = result["metrics"]
    fs = result["file_status"]
    verdict = result["verdict"]

    verdict_icon = {"HEALTHY": "✅", "DEGRADED": "⚠️", "BROKEN": "❌"}.get(verdict, "?")

    broken_md = ""
    if result["broken_reasons"]:
        broken_md = "\n### BROKEN — Critical Issues\n\n" + "\n".join(
            f"- {r}" for r in result["broken_reasons"]
        ) + "\n"

    degraded_md = ""
    if result["degraded_reasons"]:
        degraded_md = "\n### DEGRADED — Data Quality Issues\n\n" + "\n".join(
            f"- {r}" for r in result["degraded_reasons"]
        ) + "\n"

    stale_md = ""
    if m["stale_files"]:
        stale_md = "\n**Stale files (>24h since last write):**\n" + "\n".join(
            f"- {s['file']}: {s['age_hours']}h" for s in m["stale_files"]
        ) + "\n"

    report = f"""# Trade Data Collection Healthcheck

Generated: {result['healthcheck_ts']}
Check date: {result['check_date']}

## Verdict: {verdict_icon} {verdict}
{broken_md}{degraded_md}
---

## Today's Pipeline Coverage

| Check | Count |
|-------|-------|
| ORDER_FILLED events today | {m['order_filled_today']} |
| Entry snapshots written today | {m['entry_snapshots_today']} |
| POSITION_CLOSED events today | {m['position_closed_today']} |
| Closed records written today | {m['closed_records_today']} |
| Filled today with no snapshot | {m['filled_missing_snapshot_today']} |
| Closed today with no record | {m['closed_missing_record_today']} |

## Canonical File Totals

| File | Exists | Lines |
|------|--------|-------|
| `{fs['entry_snapshots']}` | {'Yes' if fs['entry_snapshots_exists'] else 'No'} | {fs['entry_snapshots_lines']} |
| `{fs['closed_ledger']}` | {'Yes' if fs['closed_ledger_exists'] else 'No'} | {fs['closed_ledger_lines']} |
| `{fs['rebuilt_ledger']}` | {'Yes' if fs['rebuilt_ledger_exists'] else 'No'} | {fs['rebuilt_ledger_lines']} |

Latest entry snapshot: `{m['latest_entry_snapshot_ts'] or 'none'}`
Latest closed record:  `{m['latest_closed_record_ts'] or 'none'}`
{stale_md}
## Duplicate Detection

| Check | Count |
|-------|-------|
| Duplicate trade_ids in entry_snapshots | {m['duplicate_snapshot_ids']} |
| Duplicate trade_ids in closed_ledger | {m['duplicate_closed_ids']} |
| Fresh duplicates in entry_snapshots (today) | {m['fresh_dup_snapshots']} |
| Fresh duplicates in closed_ledger (today) | {m['fresh_dup_closed']} |

## Data Quality

| Check | Count |
|-------|-------|
| Entry snapshots with empty signal_scores | {m['empty_signal_scores_total']} |
| Entry snapshots with regime=UNKNOWN (total) | {m['unknown_regime_total']} |
| Entry snapshots with regime=UNKNOWN (today) | {m['unknown_regime_today']} |

## Quarantine Files

| File | Total | Today |
|------|-------|-------|
| quarantine_entry_snapshots | {m['quarantine_entry']} | — |
| quarantine_closed_records | {m['quarantine_closed']} | — |
| quarantine_missing_entry_snapshot | {m['quarantine_missing_entry']} | — |
| quarantine_missing_outcome | {m['quarantine_missing_outcome']} | — |
| quarantine_schema_invalid | {m['quarantine_schema_invalid']} | — |
| quarantine_duplicate_trade_id | {m['quarantine_duplicate']} | {m['quarantine_duplicate_today']} |

## Verdict Rules (for reference)

- **HEALTHY**: All ORDER_FILLED → entry snapshots, all POSITION_CLOSED → closed records, zero duplicate canonical records, zero critical quarantine
- **DEGRADED**: Non-critical quarantine (empty signal_scores, UNKNOWN regime), stale duplicate canonical records, or missing optional fields
- **BROKEN**: ORDER_FILLED today but zero entry snapshots today, OR POSITION_CLOSED today but zero closed records today, OR fresh duplicate canonical records
"""
    with open(_HEALTHCHECK_MD, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Trade data collection healthcheck")
    parser.add_argument(
        "--date",
        default=datetime.now(UTC).strftime("%Y-%m-%d"),
        help="Date to check (YYYY-MM-DD), default today UTC",
    )
    args = parser.parse_args()

    print(f"Running healthcheck for date: {args.date}")
    result = run_healthcheck(args.date)

    _write_json(result)
    _write_markdown(result)

    verdict = result["verdict"]
    exit_code = result["exit_code"]
    print(f"\nVerdict: {verdict} (exit {exit_code})")
    print(f"JSON output: {_HEALTHCHECK_JSON}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
