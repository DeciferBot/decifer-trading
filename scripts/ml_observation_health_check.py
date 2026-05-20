#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  scripts/ml_observation_health_check.py    ║
# ║   Sprint 3.5 — Observation file health check + canary       ║
# ║                                                              ║
# ║   Reads:   data/ml/ml_observations.jsonl                     ║
# ║   Writes:  data/ml/ml_observation_health_summary.json        ║
# ║            docs/ml_observation_health_check_report.md        ║
# ║                                                              ║
# ║   Offline only — NEVER imported by the live trading bot.     ║
# ║   No model training. No model loading. No predictions.       ║
# ║   No score changes. No execution. No order routing.          ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Offline health check for data/ml/ml_observations.jsonl.

Usage:
    python3 scripts/ml_observation_health_check.py [--canary] [--verbose]

Canary mode exits with code 1 if any integrity invariant is violated:
  - Invalid JSON lines in the file
  - Any record has live_score_unchanged != true
  - Any record has ml_score_influence_enabled != false
  - Any record is missing observation_id, scan_id, symbol, or base_score
  - Duplicate observation_id within the same scan_id
  - ranking_position missing where ranking_total exists

Missing signal_scores is reported as a warning, not a canary failure, because
the pipeline legitimately emits some candidates without a full score_breakdown.

Layer classification
--------------------
  Runtime layer  : NONE — offline script only
  Execution layer: NONE — no orders, positions, or scores touched
  Training layer : NONE — no model fit, no predict
  Dependencies   : stdlib only (json, logging, os, sys, argparse, pathlib, datetime)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("decifer.ml_observation_health_check")

SCHEMA_VERSION = "sprint35_v1"

# Missing signal_scores above this fraction triggers a log.warning (not a canary failure).
MISSING_SIGNAL_SCORES_WARN_THRESHOLD: float = 0.20

_REPO_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent


# ── Path resolution ────────────────────────────────────────────────────────────

def _obs_path() -> Path:
    return _REPO_ROOT / "data" / "ml" / "ml_observations.jsonl"


def _summary_path() -> Path:
    return _REPO_ROOT / "data" / "ml" / "ml_observation_health_summary.json"


def _report_path() -> Path:
    return _REPO_ROOT / "docs" / "ml_observation_health_check_report.md"


# ── JSONL loader ───────────────────────────────────────────────────────────────

def _load_observations(path: Path) -> tuple[list[dict], int]:
    """
    Load observations from a JSONL file.
    Returns (records, invalid_line_count).
    Blank lines are skipped silently.
    """
    if not path.exists():
        return [], 0
    records: list[dict] = []
    invalid_count = 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                invalid_count += 1
                log.warning("Invalid JSON at line %d of %s", lineno, path.name)
    return records, invalid_count


# ── Integrity helpers ──────────────────────────────────────────────────────────

def _find_duplicate_obs_ids(records: list[dict]) -> list[str]:
    """
    Return observation_ids that appear more than once within the same scan_id.
    Duplicates across different scan_ids are not flagged — they indicate the same
    symbol scored in two separate scan cycles, which is correct behaviour.
    """
    scan_obs: dict[str, list[str]] = defaultdict(list)
    for rec in records:
        scan_id = rec.get("scan_id") or "__no_scan_id__"
        obs_id = rec.get("observation_id") or ""
        if obs_id:
            scan_obs[scan_id].append(obs_id)

    dupes: list[str] = []
    for obs_ids in scan_obs.values():
        seen: set[str] = set()
        for obs_id in obs_ids:
            if obs_id in seen:
                dupes.append(obs_id)
            seen.add(obs_id)
    return dupes


def _top_exclusion_reasons(records: list[dict], top_n: int = 5) -> list[dict]:
    """Return the top-N most frequent exclusion_reason values as [{reason, count}]."""
    counts: dict[str, int] = defaultdict(int)
    for rec in records:
        reason = rec.get("exclusion_reason") or "none"
        counts[reason] += 1
    sorted_reasons = sorted(counts.items(), key=lambda x: -x[1])
    return [{"reason": r, "count": c} for r, c in sorted_reasons[:top_n]]


# ── Health check ───────────────────────────────────────────────────────────────

def run_health_check(
    obs_path: Path | None = None,
    summary_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    """
    Run the full health check on ml_observations.jsonl.

    Writes summary JSON and markdown report to their canonical paths unless
    overridden (override is for tests only).

    Returns the summary dict.
    """
    obs_file = obs_path or _obs_path()
    file_exists = obs_file.exists()
    records, invalid_json_lines = _load_observations(obs_file)
    total = len(records)

    # ── Temporal coverage ─────────────────────────────────────────────────────
    timestamps = sorted(r.get("timestamp_utc") for r in records if r.get("timestamp_utc"))
    date_range = {
        "earliest": timestamps[0] if timestamps else None,
        "latest":   timestamps[-1] if timestamps else None,
    }
    latest_timestamp_utc = timestamps[-1] if timestamps else None

    # ── Identifier coverage ───────────────────────────────────────────────────
    unique_scan_ids = len({r.get("scan_id") for r in records if r.get("scan_id")})
    unique_symbols  = len({r.get("symbol")  for r in records if r.get("symbol")})

    # ── Field completeness ────────────────────────────────────────────────────
    records_with_observation_id = sum(1 for r in records if r.get("observation_id"))
    records_with_scan_id        = sum(1 for r in records if r.get("scan_id"))
    records_with_signal_scores  = sum(1 for r in records if r.get("signal_scores"))
    records_missing_signal_scores = total - records_with_signal_scores
    records_with_ranking_position = sum(
        1 for r in records if r.get("ranking_position") is not None
    )
    records_with_ranking_total = sum(
        1 for r in records if r.get("ranking_total") is not None
    )
    records_with_candidate_source = sum(1 for r in records if r.get("candidate_source"))
    records_with_base_score       = sum(1 for r in records if r.get("base_score") is not None)

    # ── Gate integrity ────────────────────────────────────────────────────────
    records_where_live_score_unchanged_true = sum(
        1 for r in records if r.get("live_score_unchanged") is True
    )
    records_with_ml_observer_enabled_true = sum(
        1 for r in records if r.get("ml_observer_enabled") is True
    )
    records_with_ml_score_influence_enabled_false = sum(
        1 for r in records if r.get("ml_score_influence_enabled") is False
    )

    # ── Duplicate detection ───────────────────────────────────────────────────
    duplicate_observation_ids = _find_duplicate_obs_ids(records)

    # ── Exclusion distribution ────────────────────────────────────────────────
    top_exclusion_reasons = _top_exclusion_reasons(records)

    # ── Sample (last 3 records) ───────────────────────────────────────────────
    sample_latest_records = [
        {k: r.get(k) for k in (
            "symbol", "scan_id", "observation_id", "timestamp_utc",
            "base_score", "direction", "regime", "passed_base_threshold",
            "live_score_unchanged", "ml_observer_enabled",
            "ml_score_influence_enabled", "exclusion_reason",
        )}
        for r in records[-3:]
    ]

    summary = {
        "schema_version":                            SCHEMA_VERSION,
        "checked_at_utc":                            datetime.now(UTC).isoformat(),
        "observation_file":                          str(obs_file),
        "observation_file_exists":                   file_exists,
        "total_observations":                        total,
        "date_range":                                date_range,
        "latest_timestamp_utc":                      latest_timestamp_utc,
        "unique_scan_ids":                           unique_scan_ids,
        "unique_symbols":                            unique_symbols,
        "records_with_observation_id":               records_with_observation_id,
        "records_with_scan_id":                      records_with_scan_id,
        "records_with_signal_scores":                records_with_signal_scores,
        "records_missing_signal_scores":             records_missing_signal_scores,
        "records_with_ranking_position":             records_with_ranking_position,
        "records_with_ranking_total":                records_with_ranking_total,
        "records_with_candidate_source":             records_with_candidate_source,
        "records_with_base_score":                   records_with_base_score,
        "records_where_live_score_unchanged_true":   records_where_live_score_unchanged_true,
        "records_with_ml_observer_enabled_true":     records_with_ml_observer_enabled_true,
        "records_with_ml_score_influence_enabled_false": records_with_ml_score_influence_enabled_false,
        "invalid_json_lines":                        invalid_json_lines,
        "duplicate_observation_ids":                 duplicate_observation_ids,
        "top_exclusion_reasons":                     top_exclusion_reasons,
        "sample_latest_records":                     sample_latest_records,
    }

    # ── Warn on high missing signal_scores rate ───────────────────────────────
    if total > 0 and records_missing_signal_scores / total > MISSING_SIGNAL_SCORES_WARN_THRESHOLD:
        log.warning(
            "ml_observation_health_check: %.0f%% records missing signal_scores "
            "(warn threshold=%.0f%%) — investigate scoring pipeline",
            100 * records_missing_signal_scores / total,
            100 * MISSING_SIGNAL_SCORES_WARN_THRESHOLD,
        )

    # ── Write outputs ─────────────────────────────────────────────────────────
    out_summary = summary_path or _summary_path()
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    with open(out_summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
        f.write("\n")
    log.info("ml_observation_health_check: summary → %s", out_summary)

    out_report = report_path or _report_path()
    out_report.parent.mkdir(parents=True, exist_ok=True)
    _write_markdown_report(summary, out_report)
    log.info("ml_observation_health_check: report  → %s", out_report)

    return summary


def _write_markdown_report(summary: dict, path: Path) -> None:
    """Write a human-readable markdown health report."""
    total = summary["total_observations"]

    def _pct(n: int) -> str:
        return f"{n} / {total}" if total else "0 / 0"

    lines: list[str] = [
        "# ML Observation Health Check Report",
        "",
        f"Generated: {summary['checked_at_utc']}",
        f"Schema: {summary['schema_version']}",
        "",
        "## File Status",
        f"- **observation_file_exists**: {summary['observation_file_exists']}",
        f"- **total_observations**: {total}",
        f"- **invalid_json_lines**: {summary['invalid_json_lines']}",
        "",
        "## Temporal Coverage",
        f"- date_range: {summary['date_range']['earliest']} → {summary['date_range']['latest']}",
        f"- latest_timestamp_utc: {summary['latest_timestamp_utc']}",
        f"- unique_scan_ids: {summary['unique_scan_ids']}",
        f"- unique_symbols: {summary['unique_symbols']}",
        "",
        "## Field Completeness",
        f"- records_with_observation_id: {_pct(summary['records_with_observation_id'])}",
        f"- records_with_scan_id: {_pct(summary['records_with_scan_id'])}",
        f"- records_with_signal_scores: {_pct(summary['records_with_signal_scores'])}",
        f"- records_missing_signal_scores: {summary['records_missing_signal_scores']}",
        f"- records_with_ranking_position: {_pct(summary['records_with_ranking_position'])}",
        f"- records_with_ranking_total: {_pct(summary['records_with_ranking_total'])}",
        f"- records_with_candidate_source: {_pct(summary['records_with_candidate_source'])}",
        f"- records_with_base_score: {_pct(summary['records_with_base_score'])}",
        "",
        "## Gate Integrity",
        f"- records_where_live_score_unchanged_true: {_pct(summary['records_where_live_score_unchanged_true'])}",
        f"- records_with_ml_observer_enabled_true: {_pct(summary['records_with_ml_observer_enabled_true'])}",
        f"- records_with_ml_score_influence_enabled_false: {_pct(summary['records_with_ml_score_influence_enabled_false'])}",
        "",
        "## Integrity Checks",
        f"- duplicate_observation_ids: {len(summary['duplicate_observation_ids'])}",
    ]

    if summary["duplicate_observation_ids"]:
        lines.append(f"  - duplicates: {summary['duplicate_observation_ids']}")

    if summary["top_exclusion_reasons"]:
        lines.append("")
        lines.append("## Top Exclusion Reasons")
        for item in summary["top_exclusion_reasons"]:
            lines.append(f"- {item['reason']}: {item['count']}")

    if summary["sample_latest_records"]:
        lines.append("")
        lines.append("## Sample Latest Records (last 3)")
        for rec in summary["sample_latest_records"]:
            lines.append(
                f"- {rec.get('symbol', '?')} @ {rec.get('timestamp_utc', '?')}: "
                f"score={rec.get('base_score', '?')}, dir={rec.get('direction', '?')}, "
                f"score_unchanged={rec.get('live_score_unchanged', '?')}"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Canary validation ──────────────────────────────────────────────────────────

def run_canary_validation(
    obs_path: Path | None = None,
    missing_signal_scores_threshold: float = MISSING_SIGNAL_SCORES_WARN_THRESHOLD,
) -> tuple[bool, list[str]]:
    """
    Canary validation mode.

    Returns (passed, failures) where passed=True means all invariants hold.
    If the file does not exist yet, canary passes (no evidence = no violation).

    Checks:
      1. Invalid JSON lines
      2. live_score_unchanged != true on any record
      3. ml_score_influence_enabled != false on any record
      4. Missing observation_id, scan_id, symbol, or base_score on any record
      5. Duplicate observation_id within the same scan_id
      6. ranking_position missing where ranking_total exists
      Missing signal_scores is a warning, not a failure.
    """
    obs_file = obs_path or _obs_path()
    failures: list[str] = []

    if not obs_file.exists():
        log.info("ml_observation_health_check canary: file absent — nothing to validate")
        return True, []

    records, invalid_json_lines = _load_observations(obs_file)

    if invalid_json_lines:
        failures.append(f"invalid_json_lines={invalid_json_lines}")

    for i, rec in enumerate(records):
        sym = rec.get("symbol") or f"record[{i}]"

        if rec.get("live_score_unchanged") is not True:
            failures.append(
                f"{sym}: live_score_unchanged != true "
                f"(was {rec.get('live_score_unchanged')!r})"
            )
        if rec.get("ml_score_influence_enabled") is not False:
            failures.append(
                f"{sym}: ml_score_influence_enabled != false "
                f"(was {rec.get('ml_score_influence_enabled')!r})"
            )
        if not rec.get("observation_id"):
            failures.append(f"{sym}: missing observation_id")
        if not rec.get("scan_id"):
            failures.append(f"{sym}: missing scan_id")
        if not rec.get("symbol"):
            failures.append(f"record[{i}]: missing symbol")
        if rec.get("base_score") is None:
            failures.append(f"{sym}: missing base_score")
        if rec.get("ranking_total") is not None and rec.get("ranking_position") is None:
            failures.append(f"{sym}: ranking_position missing where ranking_total exists")

    dupes = _find_duplicate_obs_ids(records)
    if dupes:
        failures.append(f"duplicate_observation_ids_within_scan: {dupes}")

    # Missing signal_scores — advisory warning only
    total = len(records)
    missing = sum(1 for r in records if not r.get("signal_scores"))
    if total > 0 and missing / total > missing_signal_scores_threshold:
        log.warning(
            "ml_observation_health_check canary: %.0f%% records missing signal_scores "
            "(warn threshold=%.0f%%) — not a canary failure, but investigate",
            100 * missing / total,
            100 * missing_signal_scores_threshold,
        )

    passed = not failures
    return passed, failures


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Decifer ML — observation health check (Sprint 3.5). "
            "Validates data/ml/ml_observations.jsonl is being produced correctly."
        )
    )
    p.add_argument(
        "--canary",
        action="store_true",
        help="Canary validation mode: exit 1 if any integrity invariant is violated",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return p


def main() -> int:
    args = _build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.canary:
        passed, failures = run_canary_validation()
        if passed:
            print("CANARY PASS — all observation integrity invariants hold")
            return 0
        print(f"CANARY FAIL — {len(failures)} violation(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    summary = run_health_check()
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
