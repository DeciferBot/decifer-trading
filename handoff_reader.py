"""
handoff_reader.py — Production-runtime candidate reader contract for Intelligence-First handoff.

Classification: production runtime candidate
Service layer: handoff reader / live bot boundary
Sprint: 7B (initial), 7E (load_production_handoff added)

Reads and validates a handoff manifest and active universe file.
All functions are pure file-read/validation. No side effects except
an optional structured validation log entry.

No broker calls, no LLM calls, no provider ingestion, no raw news, no broad scan.
No import of: bot_trading, scanner, orders_core, guardrails, bot_ibkr,
market_intelligence, provider_fetch_tester, backtest_intelligence,
advisory_reporter, advisory_log_reviewer.

Public API:
    read_manifest(path)                   -> dict
    validate_manifest(manifest)           -> dict
    read_active_universe(manifest)        -> dict
    validate_active_universe(universe)    -> dict
    validate_candidate(candidate)         -> dict
    build_handoff_validation_result(...)  -> dict
    load_paper_handoff(manifest_path)     -> dict
    load_production_handoff(manifest_path) -> dict   [Sprint 7E]
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "1.0"

# Source labels approved for paper handoff candidates.
# Includes all labels produced by the shadow universe pipeline.
_APPROVED_SOURCE_LABELS: frozenset[str] = frozenset({
    "intelligence_first_static_rule",
    "reference_data_approved_theme",
    "coverage_gap_review",
    "thematic_roster",
    "committed_universe",
    "committed_universe_read_only",
    "dynamic_add",
    "favourites",
    "favourites_manual_conviction",
    "economic_intelligence",
    "legacy_theme_tracker_read_only",
    "overnight_research_read_only",
    "tier_b_daily_promoted",
})

_VALID_APPROVAL_STATUSES: frozenset[str] = frozenset({
    "approved",
    "manual_protected",
    "held_protected",
    "watchlist_allowed",
})

_VALID_ROUTES: frozenset[str] = frozenset({
    "position",
    "swing",
    "intraday_swing",
    "watchlist",
    "manual_conviction",
    "held",
})

_MANIFEST_REQUIRED_TOP_KEYS: list[str] = [
    "schema_version", "published_at", "expires_at", "validation_status",
    "handoff_mode", "handoff_enabled", "active_universe_file",
    "economic_context_file", "source_snapshot_versions", "publisher",
    "warnings", "no_executable_trade_instructions",
    "live_output_changed", "secrets_exposed", "env_values_logged",
]

_UNIVERSE_REQUIRED_TOP_KEYS: list[str] = [
    "schema_version", "generated_at", "expires_at", "mode",
    "source_shadow_file", "source_files", "validation_status",
    "universe_summary", "candidates", "warnings",
    "no_executable_trade_instructions", "live_output_changed",
    "secrets_exposed", "env_values_logged",
]

_CANDIDATE_REQUIRED_FIELDS: list[str] = [
    "symbol", "route", "reason_to_care", "source_labels",
    "approval_status", "risk_flags",
]

_SAFETY_MUST_BE_FALSE: frozenset[str] = frozenset({
    "live_output_changed", "secrets_exposed", "env_values_logged",
})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO 8601 UTC timestamp. Returns None on failure."""
    if not ts:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
    ):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_expired(expires_at_str: str) -> bool:
    """Return True if expires_at is in the past or unparseable (fail closed)."""
    dt = _parse_iso(expires_at_str)
    if dt is None:
        return True
    return dt < _now_utc()


def _check_safety_flags(obj: dict, errors: list[str]) -> None:
    """Append errors if any invariant safety flags have wrong values."""
    for flag in _SAFETY_MUST_BE_FALSE:
        if obj.get(flag) is not False:
            errors.append(
                f"safety_invariant_breach: {flag} must be false, "
                f"got {obj.get(flag)!r}"
            )
    if obj.get("no_executable_trade_instructions") is not True:
        errors.append(
            f"safety_invariant_breach: no_executable_trade_instructions must be true, "
            f"got {obj.get('no_executable_trade_instructions')!r}"
        )


def _result(
    ok: bool,
    errors: list[str],
    warnings: list[str],
    fail_closed_reason: str | None = None,
    **extra: Any,
) -> dict:
    out: dict[str, Any] = {
        "ok": ok,
        "errors": list(errors),
        "warnings": list(warnings),
        "fail_closed_reason": fail_closed_reason,
    }
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_manifest(path: str) -> dict:
    """
    Read and parse a manifest file from disk.

    Returns: {"ok": bool, "manifest": dict|None, "error": str|None}
    Fail closed on: file missing, invalid JSON, non-dict root.
    """
    if not os.path.exists(path):
        return {"ok": False, "manifest": None, "error": f"manifest_missing: {path}"}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return {"ok": False, "manifest": None, "error": f"manifest_read_error: {exc}"}

    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "manifest": None, "error": f"manifest_invalid_json: {exc}"}

    if not isinstance(manifest, dict):
        return {
            "ok": False, "manifest": None,
            "error": "manifest_invalid_json: root element is not a JSON object",
        }
    return {"ok": True, "manifest": manifest, "error": None}


def validate_manifest(manifest: dict) -> dict:
    """
    Validate a parsed manifest dict for schema, freshness, and safety invariants.

    Returns: {"ok": bool, "errors": [], "warnings": [], "fail_closed_reason": str|None}
    Fail closed on any required field missing, expired, validation_status!=pass,
    or safety flag violation.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required fields
    for key in _MANIFEST_REQUIRED_TOP_KEYS:
        if key not in manifest:
            errors.append(f"manifest_schema_invalid: missing required field '{key}'")

    if errors:
        return _result(False, errors, warnings, fail_closed_reason="manifest_schema_invalid")

    # validation_status must be "pass"
    vs = manifest.get("validation_status")
    if vs != "pass":
        errors.append(f"manifest_validation_not_pass: validation_status={vs!r}")

    # handoff_mode must be a recognised value
    hm = manifest.get("handoff_mode")
    if hm not in ("paper", "live", "shadow"):
        errors.append(f"manifest_invalid_handoff_mode: {hm!r}")

    # expires_at freshness
    if _is_expired(manifest.get("expires_at", "")):
        errors.append(f"manifest_expired: expires_at={manifest.get('expires_at')!r}")

    # Safety flags
    _check_safety_flags(manifest, errors)

    # active_universe_file must be non-empty
    auf = manifest.get("active_universe_file")
    if not auf:
        errors.append("manifest_schema_invalid: active_universe_file is empty or null")

    # economic_context_file advisory warning
    if not manifest.get("economic_context_file"):
        warnings.append("manifest_warning: economic_context_file is missing or null")

    ok = len(errors) == 0
    reason = errors[0] if errors else None
    return _result(ok, errors, warnings, fail_closed_reason=reason)


def read_active_universe(manifest: dict) -> dict:
    """
    Read the active universe file referenced by the manifest.
    Only reads the path explicitly stated in manifest["active_universe_file"].
    Never searches for alternate files or falls back to scanner discovery.

    Returns: {"ok": bool, "universe": dict|None, "path": str, "error": str|None}
    """
    path = manifest.get("active_universe_file", "")
    if not path:
        return {
            "ok": False, "universe": None, "path": "",
            "error": "active_universe_missing: active_universe_file not in manifest",
        }

    if not os.path.exists(path):
        return {
            "ok": False, "universe": None, "path": path,
            "error": f"active_universe_missing: {path}",
        }

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return {
            "ok": False, "universe": None, "path": path,
            "error": f"active_universe_read_error: {exc}",
        }

    try:
        universe = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "ok": False, "universe": None, "path": path,
            "error": f"active_universe_invalid_json: {exc}",
        }

    if not isinstance(universe, dict):
        return {
            "ok": False, "universe": None, "path": path,
            "error": "active_universe_invalid_json: root element is not a JSON object",
        }

    return {"ok": True, "universe": universe, "path": path, "error": None}


def validate_active_universe(active_universe: dict) -> dict:
    """
    Validate a parsed active universe dict for schema, freshness, and safety invariants.

    Returns: {"ok": bool, "errors": [], "warnings": [], "fail_closed_reason": str|None}
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required top-level fields
    for key in _UNIVERSE_REQUIRED_TOP_KEYS:
        if key not in active_universe:
            errors.append(f"active_universe_schema_invalid: missing required field '{key}'")

    if errors:
        return _result(False, errors, warnings, fail_closed_reason="active_universe_schema_invalid")

    # expires_at freshness
    if _is_expired(active_universe.get("expires_at", "")):
        errors.append(
            f"active_universe_expired: expires_at={active_universe.get('expires_at')!r}"
        )

    # validation_status must be pass or warning
    vs = active_universe.get("validation_status")
    if vs not in ("pass", "warning"):
        errors.append(
            f"active_universe_schema_invalid: validation_status={vs!r} "
            f"must be 'pass' or 'warning'"
        )

    # Safety flags
    _check_safety_flags(active_universe, errors)

    # candidates must be a non-empty list
    candidates = active_universe.get("candidates")
    if not isinstance(candidates, list):
        errors.append("active_universe_schema_invalid: candidates must be a list")
    elif len(candidates) == 0:
        errors.append("active_universe_candidate_count_zero: no candidates in active universe")

    ok = len(errors) == 0
    reason = errors[0] if errors else None
    return _result(ok, errors, warnings, fail_closed_reason=reason)


def validate_candidate(candidate: dict) -> dict:
    """
    Validate a single candidate from the active universe.

    Fail closed on: missing required field, empty source_labels, executable=True,
    non-null order_instruction, unapproved approval_status, unapproved source_label,
    invalid route, live_output_changed=True.

    Returns: {"ok": bool, "symbol": str, "errors": [], "warnings": [], "fail_closed_reason": str|None}
    """
    errors: list[str] = []
    warnings: list[str] = []
    symbol = candidate.get("symbol", "UNKNOWN")

    # Required fields
    for field in _CANDIDATE_REQUIRED_FIELDS:
        val = candidate.get(field)
        if val is None:
            errors.append(f"candidate_missing_{field}: symbol={symbol!r}")
        elif field == "source_labels":
            if not isinstance(val, list) or len(val) == 0:
                errors.append(f"candidate_source_labels_empty: symbol={symbol!r}")
        elif field == "risk_flags":
            if not isinstance(val, list):
                errors.append(f"candidate_risk_flags_not_list: symbol={symbol!r}")

    # route or route_hint must be present
    has_route = bool(candidate.get("route"))
    has_hint = bool(candidate.get("route_hint"))
    if not has_route and not has_hint:
        errors.append(f"candidate_missing_route_and_route_hint: symbol={symbol!r}")
    elif has_route and candidate["route"] not in _VALID_ROUTES:
        errors.append(
            f"candidate_invalid_route: symbol={symbol!r} route={candidate['route']!r}"
        )

    # executable must be False (not missing, not True)
    if candidate.get("executable") is True:
        errors.append(f"candidate_executable_true: symbol={symbol!r}")

    # order_instruction must be None/null
    if candidate.get("order_instruction") is not None:
        errors.append(f"candidate_order_instruction_not_null: symbol={symbol!r}")

    # approval_status
    approval_status = candidate.get("approval_status")
    if approval_status not in _VALID_APPROVAL_STATUSES:
        errors.append(
            f"candidate_unapproved_approval_status: symbol={symbol!r} "
            f"approval_status={approval_status!r}"
        )

    # source_labels: all labels must be approved
    source_labels = candidate.get("source_labels") or []
    if isinstance(source_labels, list) and source_labels:
        unapproved = [lbl for lbl in source_labels if lbl not in _APPROVED_SOURCE_LABELS]
        if unapproved:
            errors.append(
                f"candidate_unapproved_source_label: symbol={symbol!r} "
                f"unapproved={unapproved!r}"
            )

    # live_output_changed must be False
    if candidate.get("live_output_changed") is not False:
        errors.append(f"candidate_live_output_changed_true: symbol={symbol!r}")

    ok = len(errors) == 0
    reason = errors[0] if errors else None
    return _result(ok, errors, warnings, fail_closed_reason=reason, symbol=symbol)


def build_handoff_validation_result(
    manifest_path: str,
    universe_path: str,
    manifest_result: dict,
    universe_result: dict,
    candidate_results: list[dict],
) -> dict:
    """
    Assemble the full validation result from component validation results.

    handoff_allowed is always False in Sprint 7B (handoff_enabled=False in manifest).
    This function records structural validation outcomes without enabling handoff.
    """
    manifest_ok = manifest_result.get("ok", False)
    universe_ok = universe_result.get("ok", False)
    accepted = [r for r in candidate_results if r.get("ok")]
    rejected = [r for r in candidate_results if not r.get("ok")]

    fail_reasons: list[str] = []
    if not manifest_ok:
        fail_reasons.append(
            manifest_result.get("fail_closed_reason") or "manifest_invalid"
        )
    if not universe_ok:
        fail_reasons.append(
            universe_result.get("fail_closed_reason") or "active_universe_invalid"
        )
    if rejected:
        fail_reasons.append(f"reader_rejected_candidate_count={len(rejected)}")

    overall_ok = manifest_ok and universe_ok and len(rejected) == 0 and len(candidate_results) > 0

    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "paper_handoff_validation",
        "manifest_path": manifest_path,
        "active_universe_path": universe_path,
        "manifest_validation": {
            "ok": manifest_ok,
            "errors": manifest_result.get("errors", []),
            "warnings": manifest_result.get("warnings", []),
        },
        "active_universe_validation": {
            "ok": universe_ok,
            "errors": universe_result.get("errors", []),
            "warnings": universe_result.get("warnings", []),
        },
        "candidate_validation_summary": {
            "total": len(candidate_results),
            "accepted": len(accepted),
            "rejected": len(rejected),
        },
        "rejected_candidates": [
            {"symbol": r.get("symbol"), "errors": r.get("errors", [])}
            for r in rejected
        ],
        "accepted_candidates_count": len(accepted),
        "rejected_candidates_count": len(rejected),
        "fail_closed_tests": {
            "manifest_ok": manifest_ok,
            "universe_ok": universe_ok,
            "all_candidates_accepted": len(rejected) == 0 and len(candidate_results) > 0,
            "overall_structure_valid": overall_ok,
            "fail_closed_reasons": fail_reasons,
        },
        # Always False in Sprint 7B — handoff_enabled=False in manifest
        "handoff_allowed": False,
        "production_candidate_source_changed": False,
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


def load_paper_handoff(manifest_path: str) -> dict:
    """
    Orchestrate the full paper handoff read-and-validate sequence.

    Steps:
        1. read_manifest
        2. validate_manifest
        3. read_active_universe (from manifest reference only)
        4. validate_active_universe
        5. validate_candidate for each candidate
        6. build_handoff_validation_result

    Returns the full validation result dict.
    Fail closed on any failure — never falls back to scanner discovery.
    Never calls broker, LLM, provider ingestion, raw news, or broad scan.
    """
    # Step 1: Read manifest
    read_res = read_manifest(manifest_path)
    if not read_res["ok"]:
        err = read_res["error"] or "manifest_read_failed"
        return build_handoff_validation_result(
            manifest_path, "",
            _result(False, [err], [], fail_closed_reason=err),
            _result(False, [], [], fail_closed_reason="universe_not_read"),
            [],
        )

    manifest = read_res["manifest"]

    # Step 2: Validate manifest
    manifest_val = validate_manifest(manifest)
    if not manifest_val["ok"]:
        return build_handoff_validation_result(
            manifest_path, "",
            manifest_val,
            _result(False, [], [], fail_closed_reason="universe_not_read_manifest_invalid"),
            [],
        )

    # Step 3: Read active universe (manifest reference only — no fallback)
    univ_read = read_active_universe(manifest)
    universe_path = univ_read.get("path", "")

    if not univ_read["ok"]:
        err = univ_read["error"] or "active_universe_read_failed"
        return build_handoff_validation_result(
            manifest_path, universe_path,
            manifest_val,
            _result(False, [err], [], fail_closed_reason=err),
            [],
        )

    universe = univ_read["universe"]

    # Step 4: Validate active universe
    univ_val = validate_active_universe(universe)

    # Step 5: Validate each candidate (even if universe_val failed, collect candidate errors)
    candidates = universe.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    candidate_results = [validate_candidate(c) for c in candidates]

    # Step 6: Build result
    return build_handoff_validation_result(
        manifest_path, universe_path,
        manifest_val, univ_val, candidate_results,
    )


# ---------------------------------------------------------------------------
# Sprint 7E — Production handoff loader
# ---------------------------------------------------------------------------

def _production_result(
    manifest_path: str,
    universe_path: str,
    handoff_allowed: bool,
    fail_closed_reason: str | None,
    accepted_candidates: list[dict],
    rejected_candidates: list[dict] | None = None,
) -> dict:
    """Build a production handoff result dict."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "production_handoff",
        "manifest_path": manifest_path,
        "active_universe_path": universe_path,
        "handoff_allowed": handoff_allowed,
        "fail_closed_reason": fail_closed_reason,
        "accepted_candidates": accepted_candidates,
        "rejected_candidates": rejected_candidates or [],
        "accepted_candidate_count": len(accepted_candidates),
        "rejected_candidate_count": len(rejected_candidates or []),
        "scanner_fallback_attempted": False,
        "apex_input_changed": False,
        "risk_logic_changed": False,
        "order_logic_changed": False,
        "live_output_changed": False,
    }


def load_production_handoff(manifest_path: str) -> dict:
    """
    Load and validate the production handoff manifest and active universe.

    Unlike load_paper_handoff(), this function sets handoff_allowed=True when:
    - manifest["handoff_enabled"] is True
    - manifest passes all validation checks (not expired, validation_status=pass, etc.)
    - active universe passes all validation checks
    - at least one candidate passes per-candidate validation

    accepted_candidates contains the original candidate dicts (not validation wrappers),
    suitable for governance map construction by the caller.

    Fail closed on any failure. Never falls back to scanner discovery.
    Never calls broker, LLM, provider ingestion, raw news, or broad scan.

    Returns a production result dict with:
        handoff_allowed: bool
        fail_closed_reason: str | None
        accepted_candidates: list[dict]  (original candidate dicts)
        rejected_candidates: list[dict]  (original candidate dicts that failed)
        scanner_fallback_attempted: False (invariant)
    """
    # Step 1: Read manifest
    read_res = read_manifest(manifest_path)
    if not read_res["ok"]:
        err = read_res["error"] or "manifest_read_failed"
        return _production_result(
            manifest_path, "", False,
            fail_closed_reason=err,
            accepted_candidates=[],
        )

    manifest = read_res["manifest"]

    # Check handoff_enabled before full validation for a precise fail reason
    if not manifest.get("handoff_enabled", False):
        return _production_result(
            manifest_path, "", False,
            fail_closed_reason="handoff_disabled_in_manifest",
            accepted_candidates=[],
        )

    # Step 2: Validate manifest
    manifest_val = validate_manifest(manifest)
    if not manifest_val["ok"]:
        reason = manifest_val.get("fail_closed_reason") or "manifest_invalid"
        return _production_result(
            manifest_path, "", False,
            fail_closed_reason=reason,
            accepted_candidates=[],
        )

    # Step 3: Read active universe (manifest reference only — no fallback)
    univ_read = read_active_universe(manifest)
    universe_path = univ_read.get("path", "")
    if not univ_read["ok"]:
        err = univ_read["error"] or "active_universe_read_failed"
        return _production_result(
            manifest_path, universe_path, False,
            fail_closed_reason=err,
            accepted_candidates=[],
        )

    universe = univ_read["universe"]

    # Step 4: Validate active universe
    univ_val = validate_active_universe(universe)
    if not univ_val["ok"]:
        reason = univ_val.get("fail_closed_reason") or "active_universe_invalid"
        return _production_result(
            manifest_path, universe_path, False,
            fail_closed_reason=reason,
            accepted_candidates=[],
        )

    # Step 5: Validate each candidate; keep original dicts paired with results
    candidates = universe.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []

    accepted_originals: list[dict] = []
    rejected_originals: list[dict] = []
    for c in candidates:
        r = validate_candidate(c)
        if r.get("ok"):
            accepted_originals.append(c)
        else:
            rejected_originals.append(c)

    # Zero accepted candidates → fail closed
    if not accepted_originals:
        return _production_result(
            manifest_path, universe_path, False,
            fail_closed_reason="zero_accepted_candidates",
            accepted_candidates=[],
            rejected_candidates=rejected_originals,
        )

    # All checks passed — handoff_allowed=True
    return _production_result(
        manifest_path, universe_path, True,
        fail_closed_reason=None,
        accepted_candidates=accepted_originals,
        rejected_candidates=rejected_originals,
    )
