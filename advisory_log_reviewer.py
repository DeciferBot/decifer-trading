"""
advisory_log_reviewer.py — Sprint 6C Evidence Review.

Reads data/intelligence/advisory_runtime_log.jsonl and produces
data/intelligence/advisory_log_review.json.

Rules (all hard):
- Reads advisory_runtime_log.jsonl only — no other live data
- Writes advisory_log_review.json only — no bot files, no production files
- No production module imports (scanner, bot_trading, market_intelligence, etc.)
- No live API calls
- No broker calls
- No .env inspection
- No LLM calls
- No raw news
- No broad intraday scanning
- Returns None on any failure — never raises
- live_output_changed = false

Decision gate values:
- insufficient_live_observation  — below minimum threshold (< 10 records AND < 3 sessions)
- advisory_safe_continue_logging — above threshold, all safety invariants hold, pattern unremarkable
- advisory_ready_for_handoff_design — above threshold, invariants hold, overlap patterns informative
- advisory_needs_fix             — any safety invariant violated (any executable=true, any live_output_changed=true, etc.)

Minimum threshold: 10 records OR 3 distinct sessions (UTC date used as session proxy).

Usage:
    python3 advisory_log_reviewer.py
Output: data/intelligence/advisory_log_review.json
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_LOG_PATH = os.path.join(_BASE, "data", "intelligence", "advisory_runtime_log.jsonl")
_REVIEW_OUTPUT_PATH = os.path.join(_BASE, "data", "intelligence", "advisory_log_review.json")

# Minimum thresholds for advisory observation
_MIN_RECORDS   = 10
_MIN_SESSIONS  = 3

# Safety flag names that must be False in every record
_MUST_BE_FALSE = [
    "executable",
    "production_decision_changed",
    "apex_input_changed",
    "scanner_output_changed",
    "order_logic_changed",
    "risk_logic_changed",
    "broker_called",
    "llm_called",
    "live_api_called",
    "env_inspected",
    "raw_news_used",
    "broad_intraday_scan_used",
    "live_output_changed",
]

# Safety flag names that must be True in every record
_MUST_BE_TRUE = [
    "advisory_only",
]

# Candidate-level fields that must always be False
_CANDIDATE_MUST_BE_FALSE = ["executable"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_records(path: str) -> tuple[list[dict], list[str]]:
    """
    Load all JSONL records from path.

    Returns (records, parse_warnings).
    Never raises. Returns ([], [warning]) on file-not-found.
    """
    if not os.path.isfile(path):
        return [], [f"advisory_runtime_log.jsonl not found at {path}"]

    records: list[dict] = []
    parse_warnings: list[str] = []

    try:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        records.append(rec)
                    else:
                        parse_warnings.append(f"line {lineno}: not a JSON object, skipped")
                except json.JSONDecodeError as e:
                    parse_warnings.append(f"line {lineno}: parse error ({e}), skipped")
    except OSError as e:
        return [], [f"advisory_runtime_log.jsonl read error: {e}"]

    return records, parse_warnings


def _extract_session_keys(records: list[dict]) -> list[str]:
    """
    Extract distinct session keys from records.
    Uses UTC date (YYYY-MM-DD) as session proxy.
    """
    sessions: set[str] = set()
    for rec in records:
        ts = rec.get("timestamp") or ""
        if ts:
            # ISO format: 2026-05-06T06:12:18.611578+00:00
            try:
                date_str = ts[:10]  # YYYY-MM-DD
                # basic sanity check
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
                sessions.add(date_str)
            except ValueError:
                sessions.add(ts[:10] if len(ts) >= 10 else ts)
    return sorted(sessions)


def _check_safety_invariants(records: list[dict]) -> dict[str, Any]:
    """
    Check all safety flags across all records.

    Returns a dict with:
    - advisory_only_all_records: bool
    - non_executable_all_records: bool
    - production_decision_changed_count: int
    - apex_input_changed_count: int
    - violations: list[str]  — human-readable violation messages
    - all_invariants_hold: bool
    """
    violations: list[str] = []

    # Aggregate counts for must-be-false flags
    flag_violation_counts: dict[str, int] = {k: 0 for k in _MUST_BE_FALSE}
    advisory_only_violations = 0

    production_decision_changed_count = 0
    apex_input_changed_count = 0

    for i, rec in enumerate(records):
        # Check must-be-true flags
        for flag in _MUST_BE_TRUE:
            val = rec.get(flag)
            if val is not True:
                advisory_only_violations += 1
                violations.append(
                    f"record {i}: {flag} = {val!r} (expected true)"
                )

        # Check must-be-false flags
        for flag in _MUST_BE_FALSE:
            val = rec.get(flag)
            if val is not False:
                flag_violation_counts[flag] += 1
                violations.append(
                    f"record {i}: {flag} = {val!r} (expected false)"
                )

        # Specific counters
        if rec.get("production_decision_changed") is True:
            production_decision_changed_count += 1
        if rec.get("apex_input_changed") is True:
            apex_input_changed_count += 1

        # Check candidate-level executable flags
        for cand in (rec.get("candidate_matches") or []):
            if isinstance(cand, dict) and cand.get("executable") is not False:
                sym = cand.get("symbol", "?")
                violations.append(
                    f"record {i}: candidate '{sym}' executable = {cand.get('executable')!r} (expected false)"
                )

    advisory_only_all = advisory_only_violations == 0
    non_exec_all = flag_violation_counts.get("executable", 0) == 0

    return {
        "advisory_only_all_records":         advisory_only_all,
        "non_executable_all_records":        non_exec_all,
        "production_decision_changed_count": production_decision_changed_count,
        "apex_input_changed_count":          apex_input_changed_count,
        "flag_violation_counts":             {k: v for k, v in flag_violation_counts.items() if v > 0},
        "violations":                        violations,
        "all_invariants_hold":               len(violations) == 0,
    }


def _compute_rates(records: list[dict]) -> dict[str, Any]:
    """
    Compute availability and freshness rates and regime distribution.
    """
    n = len(records)
    if n == 0:
        return {
            "advisory_report_available_rate": None,
            "advisory_report_fresh_rate":     None,
            "regime_distribution":            {},
        }

    available_count = sum(1 for r in records if r.get("advisory_report_available") is True)
    fresh_count     = sum(1 for r in records if r.get("advisory_report_fresh") is True)

    regime_dist: dict[str, int] = defaultdict(int)
    for rec in records:
        regime = rec.get("regime") or "unknown"
        regime_dist[regime] += 1

    return {
        "advisory_report_available_rate": round(available_count / n, 4),
        "advisory_report_fresh_rate":     round(fresh_count / n, 4),
        "regime_distribution":            dict(sorted(regime_dist.items())),
    }


def _compute_candidate_overlap(records: list[dict]) -> dict[str, Any]:
    """
    Analyse candidate match patterns across all records.
    """
    n = len(records)
    if n == 0:
        return {
            "total_candidate_evaluations": 0,
            "mean_candidates_per_record":  None,
            "advisory_status_totals":      {},
            "symbols_most_frequent":       [],
        }

    total_evals = 0
    status_totals: dict[str, int] = defaultdict(int)
    symbol_counts: dict[str, int] = defaultdict(int)

    for rec in records:
        matches = rec.get("candidate_matches") or []
        total_evals += len(matches)
        for m in matches:
            if not isinstance(m, dict):
                continue
            status = m.get("advisory_status") or "unknown"
            status_totals[status] += 1
            sym = m.get("symbol")
            if sym:
                symbol_counts[sym] += 1

    # Top 10 most frequent symbols
    sorted_syms = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)

    return {
        "total_candidate_evaluations": total_evals,
        "mean_candidates_per_record":  round(total_evals / n, 2),
        "advisory_status_totals":      dict(sorted(status_totals.items())),
        "symbols_most_frequent":       [{"symbol": s, "count": c} for s, c in sorted_syms[:10]],
    }


def _determine_decision_gate(
    records:               list[dict],
    sessions:              list[str],
    safety:                dict[str, Any],
    parse_warnings:        list[str],
) -> tuple[str, list[str]]:
    """
    Determine decision_gate value and reasons list.

    Returns (gate_value, reasons).
    """
    n = len(records)

    # Safety violation → always advisory_needs_fix
    if not safety["all_invariants_hold"]:
        reasons = ["Safety invariant violation detected — see safety_analysis.violations"]
        reasons += safety["violations"][:10]  # cap
        return "advisory_needs_fix", reasons

    # Below minimum threshold
    below_records  = n < _MIN_RECORDS
    below_sessions = len(sessions) < _MIN_SESSIONS

    if below_records and below_sessions:
        reasons = [
            f"Insufficient observation: {n} records (need {_MIN_RECORDS}) "
            f"and {len(sessions)} sessions (need {_MIN_SESSIONS})"
        ]
        return "insufficient_live_observation", reasons

    # Above threshold — evaluate patterns
    # If advisory report was available and candidate overlap is informative → ready for handoff design
    avail_rate = None
    for rec in records:
        if rec.get("advisory_report_available") is True:
            avail_rate = True
            break

    # Check if there are route disagreements or interesting patterns in the records
    has_disagreements = any(
        (rec.get("route_disagreements_summary") or {}).get("in_current_candidates", 0) > 0
        for rec in records
    )

    if avail_rate and has_disagreements and n >= _MIN_RECORDS:
        reasons = [
            f"{n} records across {len(sessions)} sessions observed",
            "Advisory report available and route disagreements logged",
            "Overlap patterns sufficient for handoff design review",
        ]
        return "advisory_ready_for_handoff_design", reasons

    reasons = [
        f"{n} records across {len(sessions)} sessions observed",
        "All safety invariants hold — safe to continue logging",
    ]
    return "advisory_safe_continue_logging", reasons


def _build_review(
    records:        list[dict],
    sessions:       list[str],
    safety:         dict[str, Any],
    rates:          dict[str, Any],
    candidate_analysis: dict[str, Any],
    decision_gate:  str,
    gate_reasons:   list[str],
    parse_warnings: list[str],
) -> dict[str, Any]:
    """Construct the full advisory_log_review.json output."""
    now = datetime.now(timezone.utc).isoformat()
    n = len(records)

    review_summary: dict[str, Any] = {
        "records_read":                      n,
        "sessions_detected":                 len(sessions),
        "session_keys":                      sessions,
        "advisory_report_available_rate":    rates["advisory_report_available_rate"],
        "advisory_report_fresh_rate":        rates["advisory_report_fresh_rate"],
        "advisory_only_all_records":         safety["advisory_only_all_records"],
        "non_executable_all_records":        safety["non_executable_all_records"],
        "production_decision_changed_count": safety["production_decision_changed_count"],
        "apex_input_changed_count":          safety["apex_input_changed_count"],
        "regime_distribution":               rates["regime_distribution"],
    }

    return {
        "schema_version":    "6C.1",
        "generated_at":      now,
        "mode":              "evidence_review_only",
        "source_file":       _RUNTIME_LOG_PATH,
        "review_summary":    review_summary,
        "candidate_overlap_analysis": candidate_analysis,
        "safety_analysis":   {
            "all_invariants_hold":               safety["all_invariants_hold"],
            "advisory_only_all_records":         safety["advisory_only_all_records"],
            "non_executable_all_records":        safety["non_executable_all_records"],
            "production_decision_changed_count": safety["production_decision_changed_count"],
            "apex_input_changed_count":          safety["apex_input_changed_count"],
            "flag_violation_counts":             safety["flag_violation_counts"],
            "violations":                        safety["violations"],
        },
        "decision_gate":     decision_gate,
        "gate_reasons":      gate_reasons,
        "warnings":          parse_warnings,
        "minimum_threshold": {
            "min_records":  _MIN_RECORDS,
            "min_sessions": _MIN_SESSIONS,
            "records_met":  n >= _MIN_RECORDS,
            "sessions_met": len(sessions) >= _MIN_SESSIONS,
        },
        # Safety invariants — hardcoded, never read from .env
        "advisory_only":               True,
        "executable":                  False,
        "order_instruction":           None,
        "production_decision_changed": False,
        "apex_input_changed":          False,
        "scanner_output_changed":      False,
        "order_logic_changed":         False,
        "risk_logic_changed":          False,
        "broker_called":               False,
        "llm_called":                  False,
        "live_api_called":             False,
        "env_inspected":               False,
        "raw_news_used":               False,
        "broad_intraday_scan_used":    False,
        "live_output_changed":         False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_review() -> dict[str, Any] | None:
    """
    Read advisory_runtime_log.jsonl, analyse it, and write advisory_log_review.json.

    Returns the review dict on success, or None on any unrecoverable failure.
    Never raises.
    """
    try:
        records, parse_warnings = _load_records(_RUNTIME_LOG_PATH)
        sessions  = _extract_session_keys(records)
        safety    = _check_safety_invariants(records)
        rates     = _compute_rates(records)
        candidate_analysis = _compute_candidate_overlap(records)
        decision_gate, gate_reasons = _determine_decision_gate(
            records, sessions, safety, parse_warnings
        )
        review = _build_review(
            records=records,
            sessions=sessions,
            safety=safety,
            rates=rates,
            candidate_analysis=candidate_analysis,
            decision_gate=decision_gate,
            gate_reasons=gate_reasons,
            parse_warnings=parse_warnings,
        )
        # Write output
        os.makedirs(os.path.dirname(_REVIEW_OUTPUT_PATH), exist_ok=True)
        with open(_REVIEW_OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(review, f, indent=2)

        return review

    except Exception as exc:  # noqa: BLE001
        try:
            import sys as _sys
            _sys.stderr.write(f"[ADVISORY_LOG_REVIEWER] Failed: {exc}\n")
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Running advisory_log_reviewer...")
    review = run_review()
    if review:
        print(f"  records_read:     {review['review_summary']['records_read']}")
        print(f"  sessions:         {review['review_summary']['sessions_detected']}")
        print(f"  decision_gate:    {review['decision_gate']}")
        print(f"  invariants_hold:  {review['safety_analysis']['all_invariants_hold']}")
        print(f"  live_output_changed: {review['live_output_changed']}")
        print(f"Output: {_REVIEW_OUTPUT_PATH}")
    else:
        print("  ERROR: review failed — see stderr")
