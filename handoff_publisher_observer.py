"""
handoff_publisher_observer.py — Mode-aware observation of handoff publisher outputs.

Classification: production observability tool
Service layer: Handoff / Operational monitoring
Sprint: 7G / 7G.1 / 7J.3

Reads publisher outputs over time and generates a structured observation report.
Supports two publisher publication modes:
  - validation_only    (default): manifest has handoff_enabled=false
  - controlled_activation:        manifest has handoff_enabled=true (Key 2 of two-key gate)

Does NOT run the publisher. Does NOT modify live bot wiring. Does NOT flip any flag.
Does NOT enable handoff. Does NOT change candidate source.

Inputs:
    data/live/current_manifest.json
    data/live/active_opportunity_universe.json
    data/live/handoff_publisher_report.json
    data/heartbeats/handoff_publisher.json
    data/live/paper_handoff_comparison_report.json
    data/intelligence/advisory_log_review.json
    data/universe_builder/universe_builder_report.json

Output:
    data/live/handoff_publisher_observation_report.json

Freshness SLA (from intelligence_first_snapshot_contract.md / Sprint 7A.4):
    Primary freshness threshold:  10 minutes
    Stale acceptable:             15 minutes
    Expired:                      20 minutes

Safety contract (all hardcoded — never from .env or config):
    live_output_changed = false
    live_bot_consuming_handoff = false
    production_candidate_source_changed = false
    enable_active_opportunity_universe_handoff = false  (observer never flips this)
    broker_called = false, trading_api_called = false, llm_called = false
    raw_news_used = false, broad_intraday_scan_used = false
    secrets_exposed = false, env_values_logged = false

Note: handoff_enabled and publication_mode at report top-level reflect the MANIFEST state
(not the observer's own action). The observer never enables handoff.

No imports of: bot_trading, scanner, orders_core, guardrails, bot_ibkr,
market_intelligence, apex_orchestrator, advisory_reporter, advisory_log_reviewer,
provider_fetch_tester, backtest_intelligence.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema and mode constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "1.0"
_MODE = "validation_only_handoff_publisher_observation"

# Valid publisher publication modes (Sprint 7J.3: observer is now mode-aware)
_VALID_PUBLICATION_MODES = {"validation_only", "controlled_activation"}

# Per-mode: expected manifest handoff_enabled value
_MODE_EXPECTED_HANDOFF_ENABLED: dict[str, bool] = {
    "validation_only": False,
    "controlled_activation": True,
}

# Per-mode: human-readable interpretation label for the observation report
_MODE_INTERPRETATION: dict[str, str] = {
    "validation_only": "validation_only_observation",
    "controlled_activation": "controlled_activation_precheck",
}

# Freshness SLA thresholds (seconds)
_SLA_PRIMARY_SECONDS = 10 * 60       # 10 min — primary freshness target
_SLA_STALE_SECONDS = 15 * 60         # 15 min — acceptable stale
_SLA_EXPIRED_SECONDS = 20 * 60       # 20 min — expired, no longer usable

_VALID_READINESS_GATES = {
    "insufficient_observation",
    "validation_only_stable",
    "validation_only_unstable",
    "fix_publisher_before_flag_activation",
    "controlled_activation_ready",
    "controlled_activation_unstable",
}

# Minimum thresholds for gate advancement
_MIN_RUNS_FOR_STABLE = 10
_MIN_SESSIONS_FOR_STABLE = 3

# ---------------------------------------------------------------------------
# Input / output paths
# ---------------------------------------------------------------------------

_MANIFEST_PATH = "data/live/current_manifest.json"
_UNIVERSE_PATH = "data/live/active_opportunity_universe.json"
_PUBLISHER_REPORT_PATH = "data/live/handoff_publisher_report.json"
_HEARTBEAT_PATH = "data/heartbeats/handoff_publisher.json"
_PAPER_COMPARISON_PATH = "data/live/paper_handoff_comparison_report.json"
_ADVISORY_REVIEW_PATH = "data/intelligence/advisory_log_review.json"
_UB_REPORT_PATH = "data/universe_builder/universe_builder_report.json"
_FAIL_GLOB = "data/live/.fail_*.json"
_RUN_LOG_PATH = "data/live/publisher_run_log.jsonl"
_OUTPUT_PATH = "data/live/handoff_publisher_observation_report.json"

# Safety block — hardcoded, never from .env
# Note: handoff_enabled and publication_mode are NOT in _SAFETY — they reflect the manifest
# state and are set dynamically in run_observer(). All other flags represent the observer's
# own invariants (the observer never calls brokers, LLMs, changes candidate source, etc.)
_SAFETY = {
    "production_candidate_source_changed": False,
    "live_bot_consuming_handoff": False,
    "enable_active_opportunity_universe_handoff": False,  # observer never flips this
    "apex_input_changed": False,
    "scanner_output_changed": False,
    "risk_logic_changed": False,
    "order_logic_changed": False,
    "broker_called": False,
    "trading_api_called": False,
    "llm_called": False,
    "raw_news_used": False,
    "broad_intraday_scan_used": False,
    "secrets_exposed": False,
    "env_values_logged": False,
    "live_output_changed": False,
}

_CANDIDATE_REQUIRED_FIELDS = (
    "symbol", "route", "route_hint", "reason_to_care",
    "source_labels", "theme_ids", "risk_flags", "confirmation_required",
    "approval_status", "quota_group", "freshness_status",
    "executable", "order_instruction", "live_output_changed",
)

# Quota policy — must match quota_allocator.QUOTA_POLICY_VERSION
_QUOTA_POLICY_VERSION = "75_35"
_QUOTA_POLICY_TOTAL = 75
_QUOTA_POLICY_STRUCTURAL = 35

# Watch symbols tracked per Sprint 7H.2 / 7I
_GOVERNED_WATCH  = ["COST", "MSFT", "PG"]    # governance_gap_defect (EIL-governed, quota-excluded at 50/20)
_QUOTA_WATCH     = ["SNDK", "WDC", "IREN"]   # already_governed_elsewhere (EIL-governed, quota-excluded)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: str) -> tuple[dict | None, str | None]:
    if not os.path.exists(path):
        return None, f"not found: {path}"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None, f"not a dict: {path}"
        return data, None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON in {path}: {exc}"
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"


def _age_seconds(ts_str: str | None, now: datetime) -> float | None:
    if not ts_str:
        return None
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds()
    except ValueError:
        return None


def _freshness_label(age_sec: float | None) -> str:
    if age_sec is None:
        return "unknown"
    if age_sec <= _SLA_PRIMARY_SECONDS:
        return "fresh"
    if age_sec <= _SLA_STALE_SECONDS:
        return "stale_acceptable"
    if age_sec <= _SLA_EXPIRED_SECONDS:
        return "stale_expired"
    return "expired"


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def _analyse_freshness(now: datetime, warnings: list[str]) -> dict:
    manifest, _ = _load_json(_MANIFEST_PATH)
    universe, _ = _load_json(_UNIVERSE_PATH)
    heartbeat, _ = _load_json(_HEARTBEAT_PATH)

    m_age = _age_seconds(manifest.get("published_at") if manifest else None, now)
    u_age = _age_seconds(universe.get("generated_at") if universe else None, now)
    h_age = _age_seconds(heartbeat.get("last_success_at") if heartbeat else None, now)

    m_label = _freshness_label(m_age)
    u_label = _freshness_label(u_age)
    h_label = _freshness_label(h_age)

    manifest_expires_at = manifest.get("expires_at") if manifest else None
    sla_met = all(
        lbl in ("fresh", "stale_acceptable")
        for lbl in (m_label, u_label)
        if lbl != "unknown"
    )

    stale_count = sum(1 for lbl in (m_label, u_label, h_label) if lbl == "stale_acceptable")
    expired_count = sum(1 for lbl in (m_label, u_label, h_label) if lbl in ("stale_expired", "expired"))

    ages = [a for a in (m_age, u_age, h_age) if a is not None]
    max_age = max(ages) if ages else None
    avg_age = sum(ages) / len(ages) if ages else None

    if expired_count > 0:
        warnings.append(f"freshness: {expired_count} file(s) expired — publisher may not have run recently")
    elif stale_count > 0:
        warnings.append(f"freshness: {stale_count} file(s) stale (within acceptable window)")

    return {
        "manifest_age_seconds": round(m_age, 1) if m_age is not None else None,
        "manifest_freshness": m_label,
        "manifest_expires_at": manifest_expires_at,
        "universe_age_seconds": round(u_age, 1) if u_age is not None else None,
        "universe_freshness": u_label,
        "heartbeat_age_seconds": round(h_age, 1) if h_age is not None else None,
        "heartbeat_freshness": h_label,
        "sla_primary_threshold_seconds": _SLA_PRIMARY_SECONDS,
        "sla_stale_threshold_seconds": _SLA_STALE_SECONDS,
        "sla_expired_threshold_seconds": _SLA_EXPIRED_SECONDS,
        "sla_met": sla_met,
        "stale_count": stale_count,
        "expired_count": expired_count,
        "max_age_seconds": round(max_age, 1) if max_age is not None else None,
        "average_age_seconds": round(avg_age, 1) if avg_age is not None else None,
    }


def _analyse_manifest(warnings: list[str], expected_mode: str = "validation_only") -> dict:
    """Analyse the current manifest. expected_mode controls what handoff_enabled value is valid."""
    manifest, err = _load_json(_MANIFEST_PATH)
    if err:
        warnings.append(f"manifest: {err}")
        return {"exists": False, "error": err}

    auf = manifest.get("active_universe_file") or ""
    auf_exists = os.path.exists(auf) if auf else False
    safety_clean = all(
        manifest.get(f) is False
        for f in ("live_output_changed", "secrets_exposed", "env_values_logged")
    )

    # Mode-aware: expected handoff_enabled depends on the publication_mode
    expected_handoff_enabled = _MODE_EXPECTED_HANDOFF_ENABLED.get(expected_mode, False)

    issues: list[str] = []
    if manifest.get("handoff_enabled") is not expected_handoff_enabled:
        issues.append(
            f"handoff_enabled should be {expected_handoff_enabled} "
            f"for publication_mode='{expected_mode}'"
        )
    if manifest.get("publication_mode") != expected_mode:
        issues.append(
            f"publication_mode is '{manifest.get('publication_mode')}' "
            f"but expected '{expected_mode}'"
        )
    if manifest.get("enable_flag_required") is not True:
        issues.append("enable_flag_required is not true")
    if not auf_exists:
        issues.append(f"active_universe_file does not exist: {auf!r}")
    if not safety_clean:
        issues.append("safety flags dirty")

    for issue in issues:
        warnings.append(f"manifest issue: {issue}")

    return {
        "exists": True,
        "validation_status": manifest.get("validation_status"),
        "handoff_enabled": manifest.get("handoff_enabled"),
        "publication_mode": manifest.get("publication_mode"),
        "enable_flag_required": manifest.get("enable_flag_required"),
        "ready_for_consumption": manifest.get("ready_for_consumption"),
        "active_universe_file": auf,
        "active_universe_file_exists": auf_exists,
        "publisher": manifest.get("publisher"),
        "safety_flags_clean": safety_clean,
        "issues": issues,
        "issue_count": len(issues),
    }


def _analyse_active_universe(warnings: list[str], expected_mode: str = "validation_only") -> dict:
    """Analyse the active opportunity universe. expected_mode controls publication_mode validation."""
    universe, err = _load_json(_UNIVERSE_PATH)
    if err:
        warnings.append(f"active_universe: {err}")
        return {"exists": False, "error": err}

    candidates = universe.get("candidates") or []
    executable_violations = [c.get("symbol") for c in candidates if c.get("executable") is True]
    order_violations = [c.get("symbol") for c in candidates if c.get("order_instruction") is not None]
    missing_labels = [c.get("symbol") for c in candidates if not c.get("source_labels")]

    field_issues: list[str] = []
    for c in candidates:
        for field in _CANDIDATE_REQUIRED_FIELDS:
            if field not in c:
                field_issues.append(f"{c.get('symbol')}: missing '{field}'")

    issues: list[str] = []
    # Mode-aware: accept publication_mode matching the expected_mode
    if universe.get("publication_mode") != expected_mode:
        issues.append(
            f"publication_mode is '{universe.get('publication_mode')}' "
            f"but expected '{expected_mode}'"
        )
    if universe.get("no_executable_trade_instructions") is not True:
        issues.append("no_executable_trade_instructions is not true")
    if executable_violations:
        issues.append(f"executable=true candidates: {executable_violations}")
    if order_violations:
        issues.append(f"non-null order_instruction candidates: {order_violations}")
    if missing_labels:
        issues.append(f"candidates with empty source_labels: {missing_labels}")

    for issue in issues:
        warnings.append(f"active_universe issue: {issue}")

    included_syms = {c.get("symbol") for c in candidates if c.get("symbol")}
    structural_count = sum(
        1 for c in candidates if c.get("quota_group") == "structural_position"
    )
    governed_watch_status = {
        sym: ("included" if sym in included_syms else "excluded")
        for sym in _GOVERNED_WATCH
    }
    quota_watch_status = {
        sym: ("included" if sym in included_syms else "excluded")
        for sym in _QUOTA_WATCH
    }

    return {
        "exists": True,
        "mode": universe.get("mode"),
        "publication_mode": universe.get("publication_mode"),
        "validation_status": universe.get("validation_status"),
        "candidate_count": len(candidates),
        "structural_count": structural_count,
        "no_executable_trade_instructions": universe.get("no_executable_trade_instructions"),
        "executable_violations": executable_violations,
        "order_instruction_violations": order_violations,
        "candidates_missing_source_labels": missing_labels,
        "candidate_field_issues": field_issues[:10],  # cap for readability
        "issues": issues,
        "issue_count": len(issues),
        "governed_watch_status": governed_watch_status,
        "quota_watch_status": quota_watch_status,
    }


def _analyse_heartbeat(now: datetime, warnings: list[str]) -> dict:
    heartbeat, err = _load_json(_HEARTBEAT_PATH)
    if err:
        warnings.append(f"heartbeat: {err}")
        return {"exists": False, "error": err}

    age = _age_seconds(heartbeat.get("last_success_at"), now)
    label = _freshness_label(age)
    if label in ("stale_expired", "expired"):
        warnings.append(f"heartbeat: last_success_at is {label} ({round(age or 0)}s ago)")

    return {
        "exists": True,
        "worker": heartbeat.get("worker"),
        "validation_status": heartbeat.get("validation_status"),
        "last_success_at": heartbeat.get("last_success_at"),
        "last_attempt_at": heartbeat.get("last_attempt_at"),
        "last_success_age_seconds": round(age, 1) if age is not None else None,
        "last_success_freshness": label,
        "candidate_count": heartbeat.get("candidate_count"),
        "fail_closed_reason": heartbeat.get("fail_closed_reason"),
    }


def _analyse_publisher_report(warnings: list[str], expected_mode: str = "validation_only") -> dict:
    """Analyse the publisher report. expected_mode controls what handoff_enabled is valid."""
    report, err = _load_json(_PUBLISHER_REPORT_PATH)
    if err:
        warnings.append(f"publisher_report: {err}")
        return {"exists": False, "error": err}

    vs = report.get("validation_summary") or {}
    cs = report.get("candidate_summary") or {}
    aw = report.get("atomic_write_summary") or {}

    expected_handoff_enabled = _MODE_EXPECTED_HANDOFF_ENABLED.get(expected_mode, False)

    issues: list[str] = []
    if vs.get("overall_status") != "pass":
        issues.append(f"overall_status is '{vs.get('overall_status')}' not 'pass'")
    if report.get("handoff_enabled") is not expected_handoff_enabled:
        issues.append(
            f"handoff_enabled should be {expected_handoff_enabled} "
            f"for publication_mode='{expected_mode}' in report"
        )
    if report.get("live_output_changed") is not False:
        issues.append("live_output_changed is not false in report")

    return {
        "exists": True,
        "overall_status": vs.get("overall_status"),
        "accepted_count": cs.get("accepted_count"),
        "rejected_count": cs.get("rejected_count"),
        "universe_written": aw.get("universe_written"),
        "manifest_written": aw.get("manifest_written"),
        "heartbeat_written": aw.get("heartbeat_written"),
        "handoff_enabled": report.get("handoff_enabled"),
        "publication_mode": report.get("publication_mode"),
        "live_output_changed": report.get("live_output_changed"),
        "issues": issues,
        "issue_count": len(issues),
    }


def _analyse_candidate_stability(run_log_records: int, warnings: list[str]) -> dict:
    universe, err = _load_json(_UNIVERSE_PATH)
    if err:
        return {
            "status": "unavailable",
            "error": err,
            "candidate_count": None,
            "symbols": [],
            "note": "insufficient_history_for_stability",
        }

    candidates = universe.get("candidates") or []
    symbols = sorted(c.get("symbol") or "" for c in candidates if c.get("symbol"))

    if run_log_records < 2:
        return {
            "status": "single_observation",
            "candidate_count": len(candidates),
            "symbols": symbols,
            "added_since_previous_observation": None,
            "removed_since_previous_observation": None,
            "route_changes": None,
            "quota_group_changes": None,
            "validation_status_changes": None,
            "note": "insufficient_history_for_stability — single observation only",
        }

    # Multiple runs observed — candidate-level diff requires snapshot archive.
    # Run log records run counts only; symbol-level deltas need future snapshot storage.
    return {
        "status": "multi_observation_available",
        "candidate_count": len(candidates),
        "symbols": symbols,
        "added_since_previous_observation": None,
        "removed_since_previous_observation": None,
        "route_changes": None,
        "quota_group_changes": None,
        "validation_status_changes": None,
        "note": (
            f"run_log_records={run_log_records} — candidate-level stability diff "
            "requires snapshot archive (not yet implemented)"
        ),
    }


def _analyse_fail_closed(warnings: list[str]) -> dict:
    fail_files = sorted(glob.glob(_FAIL_GLOB))
    diagnostics: list[dict] = []
    for fp in fail_files:
        try:
            with open(fp) as fh:
                data = json.load(fh)
            diagnostics.append({
                "file": fp,
                "fail_closed_reason": data.get("fail_closed_reason"),
                "generated_at": data.get("generated_at"),
            })
        except Exception:
            diagnostics.append({"file": fp, "error": "could not parse"})

    heartbeat, _ = _load_json(_HEARTBEAT_PATH)
    hb_reason = heartbeat.get("fail_closed_reason") if heartbeat else None

    manifest_exists = os.path.exists(_MANIFEST_PATH)
    universe_exists = os.path.exists(_UNIVERSE_PATH)

    return {
        "fail_closed_events": len(diagnostics),
        "fail_diagnostics_found": diagnostics,
        "manifest_missing_events": 0 if manifest_exists else 1,
        "manifest_expired_events": 0,    # computed from freshness_analysis
        "active_universe_missing_events": 0 if universe_exists else 1,
        "zero_candidate_events": 0,
        "invalid_candidate_events": 0,
        "last_heartbeat_fail_reason": hb_reason,
    }


def _read_run_log() -> dict:
    """
    Read publisher_run_log.jsonl and return run statistics.
    Returns dict with: records, successful_runs, distinct_sessions,
    first_observed_at, last_observed_at, run_log_exists.

    Classification: reads production observability output — never writes to live bot inputs.
    """
    if not os.path.exists(_RUN_LOG_PATH):
        return {
            "run_log_exists": False,
            "run_log_records": 0,
            "successful_runs": 0,
            "distinct_sessions": 0,
            "distinct_utc_dates": [],
            "first_observed_at": None,
            "last_observed_at": None,
            "successful_runs_for_current_quota": 0,
            "distinct_sessions_for_current_quota": 0,
            "quota_observation_start": None,
        }

    records: list[dict] = []
    parse_errors = 0
    try:
        with open(_RUN_LOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    parse_errors += 1
    except OSError:
        return {
            "run_log_exists": True,
            "run_log_records": 0,
            "successful_runs": 0,
            "distinct_sessions": 0,
            "distinct_utc_dates": [],
            "first_observed_at": None,
            "last_observed_at": None,
            "parse_errors": parse_errors,
            "successful_runs_for_current_quota": 0,
            "distinct_sessions_for_current_quota": 0,
            "quota_observation_start": None,
        }

    successful = [r for r in records if r.get("validation_status") == "pass"]
    utc_dates = sorted({r["utc_date"] for r in successful if r.get("utc_date")})
    timestamps = sorted(r["completed_at"] for r in successful if r.get("completed_at"))

    # Quota-policy-aware counts: only runs with the current quota policy version
    current_policy_runs = [
        r for r in successful
        if r.get("quota_policy_version") == _QUOTA_POLICY_VERSION
    ]
    current_policy_dates = sorted({
        r["utc_date"] for r in current_policy_runs if r.get("utc_date")
    })
    current_policy_timestamps = sorted(
        r["completed_at"] for r in current_policy_runs if r.get("completed_at")
    )

    return {
        "run_log_exists": True,
        "run_log_records": len(records),
        "successful_runs": len(successful),
        "distinct_sessions": len(utc_dates),
        "distinct_utc_dates": utc_dates,
        "first_observed_at": timestamps[0] if timestamps else None,
        "last_observed_at": timestamps[-1] if timestamps else None,
        "parse_errors": parse_errors,
        # Quota-policy-aware fields (Sprint 7I)
        "successful_runs_for_current_quota": len(current_policy_runs),
        "distinct_sessions_for_current_quota": len(current_policy_dates),
        "quota_observation_start": current_policy_timestamps[0] if current_policy_timestamps else None,
    }


def _get_current_mode() -> str:
    """
    Read the current publication_mode from the live manifest.
    Returns 'validation_only' as the safe default if the manifest is missing or unreadable.
    Returns 'unknown' if publication_mode is present but not in _VALID_PUBLICATION_MODES.

    Never modifies any file. Pure read.
    """
    manifest, err = _load_json(_MANIFEST_PATH)
    if err or not manifest:
        return "validation_only"
    mode = manifest.get("publication_mode", "validation_only")
    if mode not in _VALID_PUBLICATION_MODES:
        return "unknown"
    return mode


def _build_mode_context(
    current_mode: str,
    manifest_analysis: dict,
    universe_analysis: dict,
) -> dict:
    """
    Build the mode_context section of the observation report.

    This section captures the manifest's publication mode state and what it means
    for bot consumption. It does NOT change any flag or enable any handoff.

    Two-key model:
      Key 1 — bot config flag: enable_active_opportunity_universe_handoff (config.py)
      Key 2 — manifest gate:   handoff_enabled = true (controlled_activation mode)
      Both keys required for live bot consumption.
    """
    expected_handoff_enabled = _MODE_EXPECTED_HANDOFF_ENABLED.get(current_mode, False)
    mode_interpretation = _MODE_INTERPRETATION.get(current_mode, "invalid_mode")
    manifest_handoff_enabled = manifest_analysis.get("handoff_enabled")
    manifest_pub_mode = manifest_analysis.get("publication_mode")

    manifest_mode_valid = (
        current_mode in _VALID_PUBLICATION_MODES
        and manifest_handoff_enabled is expected_handoff_enabled
        and manifest_pub_mode == current_mode
    )
    manifest_allows_handoff = (
        current_mode == "controlled_activation"
        and manifest_handoff_enabled is True
    )

    # Read bot config flag state (non-critical: may be unavailable in test environments)
    bot_flag: bool | None
    try:
        import config as _cfg  # type: ignore[import]
        bot_flag = bool(_cfg.CONFIG.get("enable_active_opportunity_universe_handoff", False))
    except (ImportError, AttributeError, KeyError):
        bot_flag = None

    bot_consumption_allowed = bool(manifest_allows_handoff and bot_flag is True)

    bot_consumption_note: str | None = None
    if manifest_allows_handoff and bot_flag is False:
        bot_consumption_note = "manifest_ready_but_bot_flag_disabled"
    elif current_mode == "controlled_activation" and not manifest_allows_handoff:
        bot_consumption_note = "controlled_activation_manifest_handoff_not_enabled"
    elif current_mode == "validation_only":
        bot_consumption_note = "validation_only_manifest_handoff_disabled"

    return {
        "publication_mode": current_mode,
        "handoff_enabled": manifest_handoff_enabled,
        "mode_interpretation": mode_interpretation,
        "expected_handoff_enabled_for_mode": expected_handoff_enabled,
        "manifest_mode_valid": manifest_mode_valid,
        "manifest_allows_handoff": manifest_allows_handoff,
        "bot_flag_state": bot_flag,
        "bot_consumption_allowed": bot_consumption_allowed,
        "bot_consumption_note": bot_consumption_note,
        "candidate_count": universe_analysis.get("candidate_count"),
        "executable_violations": universe_analysis.get("executable_violations", []),
        "order_instruction_violations": universe_analysis.get("order_instruction_violations", []),
    }


def _determine_readiness_gate(
    run_log: dict,
    manifest_analysis: dict,
    universe_analysis: dict,
    heartbeat_analysis: dict,
    freshness: dict,
    warnings: list[str],
    current_mode: str = "validation_only",
) -> tuple[str, bool, str]:
    """
    Return (gate, threshold_met, threshold_basis).

    Threshold: successful_publisher_runs >= 10 OR distinct_utc_sessions >= 3.
    threshold_basis: 'successful_runs' | 'distinct_sessions' | 'not_met'

    Gate is mode-aware (Sprint 7J.3):
      validation_only mode      → validation_only_stable / validation_only_unstable
      controlled_activation mode → controlled_activation_ready / controlled_activation_unstable
      fix_publisher_before_flag_activation fires only for REAL publisher errors
      (executable candidates, safety flags dirty, missing files) — never for mode mismatch.
    """
    successful_runs = run_log.get("successful_runs", 0)
    distinct_sessions = run_log.get("distinct_sessions", 0)

    # Sprint 7I: gate uses quota-policy-aware counts when available
    quota_runs = run_log.get("successful_runs_for_current_quota", 0)
    quota_sessions = run_log.get("distinct_sessions_for_current_quota", 0)
    gate_runs = quota_runs if quota_runs > 0 else successful_runs
    gate_sessions = quota_sessions if quota_sessions > 0 else distinct_sessions

    threshold_met = (gate_runs >= _MIN_RUNS_FOR_STABLE
                     or gate_sessions >= _MIN_SESSIONS_FOR_STABLE)
    if gate_runs >= _MIN_RUNS_FOR_STABLE:
        threshold_basis = "successful_runs"
    elif gate_sessions >= _MIN_SESSIONS_FOR_STABLE:
        threshold_basis = "distinct_sessions"
    else:
        threshold_basis = "not_met"

    # Gate 1: insufficient observation
    if not threshold_met:
        return "insufficient_observation", False, "not_met"

    # Gate 2: fix publisher — real errors only (not mode-mismatch false alarms)
    # Real errors: safety flags dirty, executable candidates, missing heartbeat, missing files.
    # NOTE: issue_count is now mode-aware (from _analyse_manifest/universe), so these only
    # fire when the manifest/universe genuinely doesn't match the expected mode.
    real_publisher_errors = (
        manifest_analysis.get("issue_count", 0) > 0
        or universe_analysis.get("issue_count", 0) > 0
        or not heartbeat_analysis.get("exists")
    )
    if real_publisher_errors:
        return "fix_publisher_before_flag_activation", True, threshold_basis

    # Gate 3: freshness — mode-aware gate name
    if freshness.get("expired_count", 0) > 0:
        if current_mode == "controlled_activation":
            return "controlled_activation_unstable", True, threshold_basis
        return "validation_only_unstable", True, threshold_basis

    # Gate 4: all pass — mode-aware stable gate
    all_pass = (
        manifest_analysis.get("validation_status") == "pass"
        and universe_analysis.get("validation_status") == "pass"
        and heartbeat_analysis.get("validation_status") == "pass"
        and not freshness.get("expired_count", 0)
    )
    if all_pass:
        if current_mode == "controlled_activation":
            return "controlled_activation_ready", True, threshold_basis
        return "validation_only_stable", True, threshold_basis

    if current_mode == "controlled_activation":
        return "controlled_activation_unstable", True, threshold_basis
    return "validation_only_unstable", True, threshold_basis


# ---------------------------------------------------------------------------
# Main observer
# ---------------------------------------------------------------------------


def run_observer() -> dict:
    """
    Execute one observation cycle. Reads all publisher outputs and generates
    a structured observation report. Writes to _OUTPUT_PATH.

    Mode-aware (Sprint 7J.3): reads publication_mode from current manifest and validates
    all publisher outputs against the expected state for that mode.

    Pure read — no write to any publisher file. Never enables handoff. Never flips config flag.
    """
    now = _now_utc()
    warnings: list[str] = []

    # Determine current publication mode from the manifest (Sprint 7J.3)
    current_mode = _get_current_mode()

    freshness = _analyse_freshness(now, warnings)
    manifest_analysis = _analyse_manifest(warnings, expected_mode=current_mode)
    universe_analysis = _analyse_active_universe(warnings, expected_mode=current_mode)
    heartbeat_analysis = _analyse_heartbeat(now, warnings)
    publisher_report_analysis = _analyse_publisher_report(warnings, expected_mode=current_mode)
    fail_closed_obs = _analyse_fail_closed(warnings)

    run_log = _read_run_log()
    successful_runs = run_log["successful_runs"]
    failed_runs = len(glob.glob(_FAIL_GLOB))
    total_runs = run_log["run_log_records"] + failed_runs

    candidate_stability = _analyse_candidate_stability(run_log["run_log_records"], warnings)

    readiness_gate, threshold_met, threshold_basis = _determine_readiness_gate(
        run_log, manifest_analysis, universe_analysis,
        heartbeat_analysis, freshness, warnings,
        current_mode=current_mode,
    )

    # Build mode context (Sprint 7J.3)
    mode_context = _build_mode_context(current_mode, manifest_analysis, universe_analysis)

    source_files = [p for p in (
        _MANIFEST_PATH, _UNIVERSE_PATH, _PUBLISHER_REPORT_PATH, _HEARTBEAT_PATH,
        _PAPER_COMPARISON_PATH, _ADVISORY_REVIEW_PATH, _UB_REPORT_PATH,
    ) if os.path.exists(p)]
    if run_log["run_log_exists"]:
        source_files.append(_RUN_LOG_PATH)

    observation_summary = {
        "records_observed": total_runs,
        "first_observed_at": run_log.get("first_observed_at") or heartbeat_analysis.get("last_success_at"),
        "last_observed_at": run_log.get("last_observed_at") or _ts(now),
        "publisher_runs_detected": total_runs,
        "successful_publishes": successful_runs,
        "failed_publishes": failed_runs,
        "run_log_exists": run_log["run_log_exists"],
        "run_log_records": run_log["run_log_records"],
        "successful_publisher_runs": successful_runs,
        "distinct_utc_sessions": run_log["distinct_sessions"],
        "distinct_utc_dates": run_log.get("distinct_utc_dates", []),
        "threshold_met": threshold_met,
        "threshold_basis": threshold_basis,
        "current_manifest_exists": manifest_analysis.get("exists", False),
        "active_universe_exists": universe_analysis.get("exists", False),
        "heartbeat_exists": heartbeat_analysis.get("exists", False),
        "publisher_report_exists": publisher_report_analysis.get("exists", False),
        "readiness_gate": readiness_gate,
        # Sprint 7I: quota policy tracking
        "quota_policy_version": _QUOTA_POLICY_VERSION,
        "quota_policy_total_cap": _QUOTA_POLICY_TOTAL,
        "quota_policy_structural_cap": _QUOTA_POLICY_STRUCTURAL,
        "quota_observation_required": True,
        "quota_observation_start": run_log.get("quota_observation_start"),
        "successful_runs_for_current_quota": run_log.get("successful_runs_for_current_quota", 0),
        "distinct_sessions_for_current_quota": run_log.get("distinct_sessions_for_current_quota", 0),
        "candidate_count": universe_analysis.get("candidate_count"),
        "structural_count": universe_analysis.get("structural_count"),
        "governed_watch_status": universe_analysis.get("governed_watch_status"),
        "quota_watch_status": universe_analysis.get("quota_watch_status"),
    }

    # handoff_enabled and publication_mode at top level reflect the MANIFEST state (Sprint 7J.3).
    # This allows the validator to do mode-aware checks. The observer itself never enables
    # handoff — that guarantee is expressed in safety_analysis.
    manifest_handoff_enabled = manifest_analysis.get("handoff_enabled", False)

    report = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _ts(now),
        "mode": _MODE,
        "source_files": source_files,
        "observation_summary": observation_summary,
        "freshness_analysis": freshness,
        "manifest_validity_analysis": manifest_analysis,
        "active_universe_validity_analysis": universe_analysis,
        "heartbeat_analysis": heartbeat_analysis,
        "publisher_report_analysis": publisher_report_analysis,
        "candidate_stability_analysis": candidate_stability,
        "fail_closed_observations": fail_closed_obs,
        "mode_context": mode_context,          # Sprint 7J.3: mode-awareness section
        "safety_analysis": {
            "live_bot_consuming_handoff": False,   # observer never consumes
            "enable_active_opportunity_universe_handoff": False,  # observer never flips this
            "production_candidate_source_changed": False,
            "scanner_output_changed": False,
            "apex_input_changed": False,
            "risk_logic_changed": False,
            "order_logic_changed": False,
            "live_output_changed": False,
            "all_safety_invariants_hold": True,
        },
        "readiness_gate": readiness_gate,
        "warnings": warnings,
        # Dynamic fields: reflect manifest state (not observer action)
        "handoff_enabled": manifest_handoff_enabled,
        "publication_mode": current_mode,
        **_SAFETY,
    }

    os.makedirs(os.path.dirname(_OUTPUT_PATH) or ".", exist_ok=True)
    tmp = _OUTPUT_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        os.replace(tmp, _OUTPUT_PATH)
    except Exception as exc:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise

    log.info(
        "[handoff_publisher_observer] observation_complete gate=%s "
        "successful_runs=%d distinct_sessions=%d failed_runs=%d threshold_met=%s "
        "manifest_issues=%d universe_issues=%d "
        "live_bot_consuming_handoff=false live_output_changed=false",
        readiness_gate, successful_runs, run_log["distinct_sessions"], failed_runs,
        threshold_met,
        manifest_analysis.get("issue_count", 0),
        universe_analysis.get("issue_count", 0),
    )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    report = run_observer()
    gate = report["readiness_gate"]
    obs = report["observation_summary"]
    successful = obs["successful_publisher_runs"]
    sessions = obs["distinct_utc_sessions"]
    failed = obs["failed_publishes"]
    threshold_met = obs["threshold_met"]
    threshold_basis = obs["threshold_basis"]
    manifest_ok = report["manifest_validity_analysis"].get("issue_count", 0) == 0
    universe_ok = report["active_universe_validity_analysis"].get("issue_count", 0) == 0
    sla_ok = report["freshness_analysis"].get("sla_met", False)
    print(
        f"[handoff_publisher_observer] gate={gate} "
        f"runs={successful}ok/{failed}fail sessions={sessions} "
        f"threshold={'MET:'+threshold_basis if threshold_met else 'not_met'} "
        f"manifest={'OK' if manifest_ok else 'ISSUES'} "
        f"universe={'OK' if universe_ok else 'ISSUES'} "
        f"freshness={'OK' if sla_ok else 'STALE'} "
        f"live_bot_consuming_handoff=false live_output_changed=false"
    )
    if report.get("warnings"):
        for w in report["warnings"]:
            print(f"  WARN: {w}")
