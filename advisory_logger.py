"""
advisory_logger.py — Sprint 6B Live Read-Only Advisory Logger.

Reads data/intelligence/advisory_report.json and appends a single record
to data/intelligence/advisory_runtime_log.jsonl.

Rules (all hard):
- Read advisory_report.json only — no other live data
- Write advisory_runtime_log.jsonl only — never touches any file used by the bot
- No production module imports (scanner, bot_trading, market_intelligence, etc.)
- No live API calls
- No broker calls
- No .env inspection
- No LLM calls
- No raw news
- No broad intraday scanning
- Does not mutate any caller-provided object
- Returns None on any failure — never raises
- live_output_changed = false

Usage (called from bot_trading.py hook only when flag true):
    from advisory_logger import log_advisory_context
    log_advisory_context(candidates=scored_symbols, regime=regime_name)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))
_ADVISORY_REPORT_PATH = os.path.join(_BASE, "data", "intelligence", "advisory_report.json")
_RUNTIME_LOG_PATH     = os.path.join(_BASE, "data", "intelligence", "advisory_runtime_log.jsonl")

# Advisory report is considered stale if older than this many seconds
_ADVISORY_MAX_AGE_SECONDS = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _read_advisory_report() -> tuple[dict | None, bool, str]:
    """
    Load advisory_report.json.

    Returns: (data, is_fresh, warning_message)
    data is None if file is missing or invalid.
    """
    if not os.path.isfile(_ADVISORY_REPORT_PATH):
        return None, False, f"advisory_report.json not found at {_ADVISORY_REPORT_PATH}"

    try:
        stat = os.stat(_ADVISORY_REPORT_PATH)
        age_seconds = datetime.now(timezone.utc).timestamp() - stat.st_mtime
        is_fresh = age_seconds < _ADVISORY_MAX_AGE_SECONDS
    except OSError:
        is_fresh = False

    try:
        with open(_ADVISORY_REPORT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, False, "advisory_report.json is not a JSON object"
        return data, is_fresh, ""
    except json.JSONDecodeError as e:
        return None, False, f"advisory_report.json parse error: {e}"
    except OSError as e:
        return None, False, f"advisory_report.json read error: {e}"


def _match_candidates(
    candidate_symbols: list[str],
    advisory_report:   dict,
) -> list[dict]:
    """
    Find advisory records matching the current candidate set.
    Returns a list of lightweight match records.
    Does not mutate candidate_symbols or advisory_report.
    """
    ca: list[dict] = advisory_report.get("candidate_advisory") or []
    ca_by_symbol: dict[str, dict] = {r.get("symbol", ""): r for r in ca if r.get("symbol")}

    matches: list[dict] = []
    for sym in candidate_symbols:
        rec = ca_by_symbol.get(sym)
        if rec:
            matches.append({
                "symbol":            sym,
                "in_current":        rec.get("in_current", True),
                "in_shadow":         rec.get("in_shadow", False),
                "advisory_status":   rec.get("advisory_status"),
                "advisory_reason":   rec.get("advisory_reason"),
                "current_route":     rec.get("current_route"),
                "shadow_route":      rec.get("shadow_route"),
                "route_disagreement": rec.get("route_disagreement", False),
                "source_labels":     rec.get("source_labels") or [],
                "reason_to_care":    rec.get("reason_to_care"),
                "quota_group":       rec.get("quota_group"),
                "theme_state":       rec.get("theme_state"),
                "thesis_status":     rec.get("thesis_status"),
                "executable":        False,
                "order_instruction": None,
            })
        else:
            # Symbol present in live scan but not in advisory report
            matches.append({
                "symbol":            sym,
                "in_current":        True,
                "in_shadow":         False,
                "advisory_status":   "advisory_unresolved",
                "advisory_reason":   "symbol_not_in_advisory_report",
                "current_route":     None,
                "shadow_route":      None,
                "route_disagreement": False,
                "source_labels":     [],
                "reason_to_care":    None,
                "quota_group":       None,
                "theme_state":       None,
                "thesis_status":     None,
                "executable":        False,
                "order_instruction": None,
            })
    return matches


def _build_log_record(
    advisory_report:     dict | None,
    report_available:    bool,
    report_fresh:        bool,
    candidate_matches:   list[dict],
    regime:              str | None,
    warning:             str,
) -> dict[str, Any]:
    """Construct the JSONL log record. All safety flags hardcoded."""
    now = datetime.now(timezone.utc).isoformat()

    adv_summary: dict = {}
    route_disagreements: list = []
    unsupported_current: dict = {}
    missing_shadow: dict = {}
    report_warnings: list = []

    if advisory_report is not None:
        adv_summary          = advisory_report.get("advisory_summary") or {}
        rd                   = advisory_report.get("route_disagreements") or {}
        route_disagreements  = rd.get("disagreements") or []
        unsupported_current  = advisory_report.get("unsupported_current_candidates") or {}
        missing_shadow       = advisory_report.get("missing_shadow_candidates") or {}
        report_warnings      = advisory_report.get("warnings") or []

    warnings: list[str] = []
    if warning:
        warnings.append(warning)
    if not report_fresh and report_available:
        warnings.append("advisory_report.json may be stale (>24h)")

    return {
        "timestamp":                   now,
        "mode":                        "live_read_only_advisory",
        "source_file":                 _ADVISORY_REPORT_PATH,
        "regime":                      regime,
        "advisory_report_available":   report_available,
        "advisory_report_fresh":       report_fresh,
        "advisory_summary":            adv_summary,
        "candidate_matches":           candidate_matches,
        "candidate_matches_count":     len(candidate_matches),
        "route_disagreements_summary": {
            "total": len(route_disagreements),
            "in_current_candidates": sum(
                1 for d in route_disagreements
                if d.get("symbol") in {m["symbol"] for m in candidate_matches}
            ),
        },
        "unsupported_current_candidates": {
            "total": unsupported_current.get("total", 0),
        },
        "missing_shadow_candidates": {
            "total":   missing_shadow.get("total", 0),
            "symbols": missing_shadow.get("symbols") or [],
        },
        "warnings":                    warnings + report_warnings[:5],  # cap report warnings
        # Safety invariants — all hardcoded, never read from .env or config
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


def _append_log(record: dict) -> None:
    """Append a single record to the advisory JSONL log."""
    os.makedirs(os.path.dirname(_RUNTIME_LOG_PATH), exist_ok=True)
    with open(_RUNTIME_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def log_advisory_context(
    candidates: list[str] | list[dict] | None = None,
    regime:     str | None = None,
) -> None:
    """
    Read advisory_report.json and append one record to advisory_runtime_log.jsonl.

    Called from bot_trading.py hook only when intelligence_first_advisory_enabled=True.

    Parameters
    ----------
    candidates : list of symbol strings or scored-candidate dicts
        The current candidate set AFTER scoring. Not mutated.
    regime : str
        Current regime name string. Not mutated.

    Returns None on success or any failure. Never raises.
    Does not change any state in the caller.
    """
    try:
        # Normalise candidates to symbol list — never mutate the input
        if candidates is None:
            symbol_list: list[str] = []
        elif candidates and isinstance(candidates[0], dict):
            # list of scored-candidate dicts — extract symbol field
            symbol_list = [c.get("symbol", "") for c in candidates if c.get("symbol")]
        else:
            symbol_list = [str(s) for s in candidates if s]

        # Load advisory report
        report_data, is_fresh, warning = _read_advisory_report()
        report_available = report_data is not None

        # Match candidates against advisory records
        matches = _match_candidates(symbol_list, report_data or {})

        # Build and write the log record
        record = _build_log_record(
            advisory_report=report_data,
            report_available=report_available,
            report_fresh=is_fresh,
            candidate_matches=matches,
            regime=regime,
            warning=warning,
        )
        _append_log(record)

    except Exception as _exc:  # noqa: BLE001
        # Advisory failure must never affect live bot.
        # Log to stderr only — no reraise, no bot state change.
        try:
            import sys as _sys
            _sys.stderr.write(
                f"[ADVISORY_LOGGER] Non-critical advisory logging failure: {_exc}\n"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point (demo mode — not live production)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Running advisory_logger in demo mode...")
    log_advisory_context(
        candidates=["NVDA", "AAPL", "MSFT", "TSLA"],
        regime="BULL_TRENDING",
    )
    # Read and display the last log record
    try:
        with open(_RUNTIME_LOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        if lines:
            last = json.loads(lines[-1])
            print(f"  advisory_report_available: {last['advisory_report_available']}")
            print(f"  advisory_report_fresh:     {last['advisory_report_fresh']}")
            print(f"  candidate_matches_count:   {last['candidate_matches_count']}")
            print(f"  advisory_only:             {last['advisory_only']}")
            print(f"  production_decision_changed: {last['production_decision_changed']}")
            print(f"  live_output_changed:       {last['live_output_changed']}")
    except Exception as e:
        print(f"  Error reading log: {e}")
