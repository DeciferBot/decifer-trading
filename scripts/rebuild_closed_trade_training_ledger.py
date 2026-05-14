#!/usr/bin/env python3
"""
rebuild_closed_trade_training_ledger.py

Non-destructive rebuild of the canonical closed-trade training ledger from
existing legacy data.

Reads  (read-only, never mutated):
  data/training_records.jsonl        — legacy training store
  data/trade_events.jsonl            — event log (ORDER_INTENT enrichment)
  data/ml/entry_trade_snapshots.jsonl — canonical entry snapshots if already present

Writes (additive / new files only):
  data/ml/closed_trade_training_ledger.rebuilt.jsonl
  data/ml/rebuild_quarantine.jsonl
  docs/closed_trade_training_ledger_rebuild_report.md

Rules:
  - Original files are NEVER modified.
  - Output uses .rebuilt suffix; canonical ledger is never touched.
  - Duplicate trade_ids: first occurrence kept, rest quarantined.
  - Empty signal_scores: written to rebuilt AND quarantine (flagged).
  - regime=UNKNOWN: written to rebuilt (common in legacy; not quarantined).
  - Missing trade_id: quarantine only.
  - All records get rebuilt_from_legacy=True, rebuild_ts, rebuild_source.

Usage:
  python scripts/rebuild_closed_trade_training_ledger.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Resolve repo root so script works from any cwd.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from trade_data_contract import (
    SCHEMA_VERSION,
    _CLOSED_RECORD_REQUIRED,
    _append_jsonl,
    derive_win_loss_label,
)

# ── Paths ──────────────────────────────────────────────────────────────────────
_ML_DIR         = _REPO / "data" / "ml"
_LEGACY_FILE    = _REPO / "data" / "training_records.jsonl"
_EVENTS_FILE    = _REPO / "data" / "trade_events.jsonl"
_SNAPSHOTS_FILE = _ML_DIR / "entry_trade_snapshots.jsonl"
_REBUILT_FILE   = _ML_DIR / "closed_trade_training_ledger.rebuilt.jsonl"
_QUARANTINE     = _ML_DIR / "rebuild_quarantine.jsonl"
_REPORT_MD      = _REPO / "docs" / "closed_trade_training_ledger_rebuild_report.md"


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


def _load_existing_rebuilt_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    for rec in _load_jsonl(path):
        tid = rec.get("trade_id")
        if tid:
            ids.add(str(tid))
    return ids


def _quarantine_record(rec: dict, reason: str, dry_run: bool) -> None:
    q = dict(rec)
    q["quarantine_reason"] = reason
    q["quarantine_ts"] = datetime.now(UTC).isoformat()
    if not dry_run:
        _append_jsonl(_QUARANTINE, q)


def _build_order_intent_index(events: list[dict]) -> dict[str, dict]:
    """Build trade_id → ORDER_INTENT event dict for enrichment."""
    idx: dict[str, dict] = {}
    for ev in events:
        if ev.get("event") == "ORDER_INTENT":
            tid = ev.get("trade_id")
            if tid and tid not in idx:
                idx[tid] = ev
    return idx


def _map_legacy_to_closed(rec: dict, intent_idx: dict[str, dict], now_ts: str) -> dict:
    """Map a legacy training_records.jsonl record to the closed record schema."""
    tid = rec.get("trade_id", "")
    intent = intent_idx.get(tid, {})

    # Prefer intent fields for entry-time data (more reliable for pre-migration records).
    signal_scores = (
        intent.get("signal_scores")
        or rec.get("signal_scores")
        or {}
    )
    regime = (
        intent.get("regime")
        or rec.get("regime")
        or "UNKNOWN"
    )
    conviction = float(
        intent.get("conviction")
        or rec.get("conviction")
        or 0.0
    )
    score = float(
        intent.get("score")
        or rec.get("score")
        or 0.0
    )

    fill_price = float(rec.get("fill_price") or 0.0)
    fill_qty   = int(float(rec.get("qty") or 0))
    realised_pnl = float(rec.get("pnl") or 0.0)
    exit_price   = float(rec.get("exit_price") or 0.0)

    pnl_pct = float(rec.get("pnl_pct") or 0.0)
    if not pnl_pct and fill_price * fill_qty:
        pnl_pct = round(realised_pnl / (fill_price * fill_qty), 4)

    return {
        "schema_version": SCHEMA_VERSION,
        "trade_id": tid,
        "symbol": rec.get("symbol", ""),
        "direction": rec.get("direction", "LONG"),
        "instrument": rec.get("instrument", "stock"),
        "trade_type": rec.get("trade_type") or "INTRADAY",
        "fill_price": fill_price,
        "fill_qty": fill_qty,
        "entry_price_source": "legacy_training_records",
        "fill_confirmed": True,
        "intended_price": float(rec.get("intended_price") or fill_price),
        "sl": float(rec.get("sl") or 0.0),
        "tp": float(rec.get("tp") or 0.0),
        "score": score,
        "conviction": conviction,
        "regime": regime,
        "signal_scores": signal_scores,
        "score_breakdown": rec.get("score_breakdown") or {},
        "session_character": "",
        "sector": "",
        "catalyst": "",
        "candidate_source": "UNKNOWN",
        "handoff_source": [],
        "source_mode": "UNKNOWN",
        "setup_type": rec.get("setup_type", ""),
        "pattern_id": rec.get("pattern_id", ""),
        "atr": float(rec.get("atr") or 0.0),
        "advice_id": "",
        "entry_thesis": rec.get("entry_thesis", ""),
        "ic_weight_snapshot": rec.get("ic_weights_at_entry"),
        "entry_context": None,
        "open_time": rec.get("ts_fill") or rec.get("open_time", ""),
        "ts_fill": rec.get("ts_fill") or "",
        "ts_written": now_ts,
        "missing_field_flags": (
            (["signal_scores"] if not signal_scores else [])
            + (["regime"] if regime == "UNKNOWN" else [])
            + (["candidate_source"])
        ),
        # Outcome fields
        "exit_price": exit_price,
        "ts_exit": rec.get("ts_close") or now_ts,
        "hold_minutes": int(rec.get("hold_minutes") or 0),
        "realised_pnl": realised_pnl,
        "pnl_pct": pnl_pct,
        "exit_reason": rec.get("exit_reason", ""),
        "win_loss_label": derive_win_loss_label(realised_pnl),
        "fees": None,
        "slippage": None,
        "outcome_source": "legacy_training_records",
        "ts_outcome_written": now_ts,
        # Rebuild metadata
        "rebuilt_from_legacy": True,
        "entry_snapshot_available": False,
        "rebuild_ts": now_ts,
        "rebuild_source": "training_records.jsonl",
    }


# ── Main rebuild ───────────────────────────────────────────────────────────────

def rebuild(dry_run: bool = False) -> dict:
    now_ts = datetime.now(UTC).isoformat()
    stats: dict[str, int] = {
        "read": 0,
        "written": 0,
        "skipped_duplicate": 0,
        "quarantined_blank_id": 0,
        "quarantined_invalid": 0,
        "quarantined_empty_signal_scores": 0,
        "unknown_regime": 0,
        "already_in_canonical_snapshots": 0,
    }

    legacy = _load_jsonl(_LEGACY_FILE)
    events = _load_jsonl(_EVENTS_FILE)
    intent_idx = _build_order_intent_index(events)
    existing_rebuilt = _load_existing_rebuilt_ids(_REBUILT_FILE)

    # Track snapshot availability for enrichment hint.
    snapshot_ids: set[str] = set()
    for snap in _load_jsonl(_SNAPSHOTS_FILE):
        tid = snap.get("trade_id")
        if tid:
            snapshot_ids.add(str(tid))

    seen_ids: set[str] = set(existing_rebuilt)

    for rec in legacy:
        stats["read"] += 1
        tid = str(rec.get("trade_id") or "")

        if not tid:
            stats["quarantined_blank_id"] += 1
            _quarantine_record(rec, "blank_trade_id", dry_run)
            continue

        if tid in seen_ids:
            stats["skipped_duplicate"] += 1
            _quarantine_record(
                dict(rec, trade_id=tid),
                "rebuild_duplicate",
                dry_run,
            )
            continue

        closed = _map_legacy_to_closed(rec, intent_idx, now_ts)

        # Mark if a canonical entry snapshot is already available.
        if tid in snapshot_ids:
            closed["entry_snapshot_available"] = True
            stats["already_in_canonical_snapshots"] += 1

        # Validate required fields.
        absent = _CLOSED_RECORD_REQUIRED - closed.keys()
        if absent:
            stats["quarantined_invalid"] += 1
            _quarantine_record(
                dict(closed, missing=sorted(absent)),
                f"missing_required_fields:{sorted(absent)}",
                dry_run,
            )
            continue

        # Data-quality flagging: empty signal_scores → both rebuilt AND quarantine.
        if not closed.get("signal_scores"):
            stats["quarantined_empty_signal_scores"] += 1
            _quarantine_record(closed, "empty_signal_scores", dry_run)
            # Still write to rebuilt (flagged, not excluded).

        if closed.get("regime") == "UNKNOWN":
            stats["unknown_regime"] += 1

        if not dry_run:
            _append_jsonl(_REBUILT_FILE, closed)
        stats["written"] += 1
        seen_ids.add(tid)

    return stats


def _write_report(stats: dict, dry_run: bool) -> None:
    rebuilt_count = stats.get("written", 0)
    _REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    report = f"""# Closed Trade Training Ledger — Rebuild Report

Generated: {datetime.now(UTC).isoformat()}
Mode: {"DRY RUN — no files written" if dry_run else "LIVE"}

## Summary

| Metric | Count |
|--------|-------|
| Records read from training_records.jsonl | {stats['read']} |
| Records written to .rebuilt file | {rebuilt_count} |
| Skipped (duplicate trade_id) | {stats['skipped_duplicate']} |
| Quarantined (blank trade_id) | {stats['quarantined_blank_id']} |
| Quarantined (schema invalid) | {stats['quarantined_invalid']} |
| Flagged (empty signal_scores — in rebuilt + quarantine) | {stats['quarantined_empty_signal_scores']} |
| Records with regime=UNKNOWN (in rebuilt) | {stats['unknown_regime']} |
| Records with canonical entry snapshot available | {stats['already_in_canonical_snapshots']} |

## Output Files

| File | Status |
|------|--------|
| `data/ml/closed_trade_training_ledger.rebuilt.jsonl` | {"WRITTEN" if not dry_run else "DRY RUN"} |
| `data/ml/rebuild_quarantine.jsonl` | {"WRITTEN" if not dry_run else "DRY RUN"} |

## Notes

- Source file `data/training_records.jsonl` was NOT modified.
- All rebuilt records have `rebuilt_from_legacy=true`.
- Records with `empty_signal_scores` are written to the rebuilt file (flagged) AND quarantine.
- Records with `regime=UNKNOWN` are written to the rebuilt file without quarantine (common in legacy data).
- `candidate_source` is always `UNKNOWN` for legacy records (field did not exist before this sprint).
- This rebuilt file is for research only. The canonical `closed_trade_training_ledger.jsonl` is populated only by live trades after sprint deployment.
"""
    with open(_REPORT_MD, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild closed trade training ledger from legacy data")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing files")
    args = parser.parse_args()

    print(f"Rebuild starting — source: {_LEGACY_FILE}")
    if args.dry_run:
        print("DRY RUN — no files will be written")

    stats = rebuild(dry_run=args.dry_run)
    _write_report(stats, dry_run=args.dry_run)

    print(f"\nDone. written={stats['written']} quarantined={stats['quarantined_blank_id'] + stats['quarantined_invalid']}")
    if not args.dry_run:
        print(f"Output: {_REBUILT_FILE}")


if __name__ == "__main__":
    main()
