"""
handoff_publisher.py — Production handoff publisher (scheduled worker).

Classification: production runtime candidate / scheduled worker
Service layer: Handoff / Live manifest generation
Sprint: 7F / 7G.1

Reads the validated Intelligence-First shadow universe and publishes:

    data/live/active_opportunity_universe.json
    data/live/current_manifest.json
    data/live/handoff_publisher_report.json
    data/heartbeats/handoff_publisher.json

Publication mode in Sprint 7F: validation_only
handoff_enabled: false
enable_active_opportunity_universe_handoff: false

The live bot does NOT consume these files while
enable_active_opportunity_universe_handoff is False.

Atomic write policy:
    1. Write .tmp file
    2. Validate temp contents
    3. os.replace() to final path
    4. Never overwrite last valid file with invalid output
    5. Write .fail_{timestamp}.json diagnostic on any validation failure
    6. Update heartbeat only after a fully successful publish cycle

Safety contract (all hardcoded — never from .env or config):
    live_output_changed = false
    no_executable_trade_instructions = true
    secrets_exposed = false
    env_values_logged = false
    broker_called = false
    trading_api_called = false
    llm_called = false
    raw_news_used = false
    broad_intraday_scan_used = false

No imports of: bot_trading, scanner, orders_core, guardrails, bot_ibkr,
market_intelligence, apex_orchestrator, advisory_reporter, advisory_log_reviewer,
provider_fetch_tester, backtest_intelligence.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version and publication constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "1.0"
_PUBLICATION_MODE = "validation_only"
_HANDOFF_MODE = "production_validation"
_UNIVERSE_MODE = "production_handoff_universe"

_EXPIRY_HOURS = 15  # minutes expressed as hours — 15 min = 0.25 h
_EXPIRY_MINUTES = 15

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SHADOW_UNIVERSE_PATH = "data/universe_builder/active_opportunity_universe_shadow.json"
_ECONOMIC_CONTEXT_PATH = "data/intelligence/current_economic_context.json"
_THEME_ACTIVATION_PATH = "data/intelligence/theme_activation.json"
_THESIS_STORE_PATH = "data/intelligence/thesis_store.json"
_SYMBOL_MASTER_PATH = "data/reference/symbol_master.json"
_LAYER_FACTOR_MAP_PATH = "data/reference/layer_factor_map.json"
_DATA_QUALITY_REPORT_PATH = "data/reference/data_quality_report.json"
_PAPER_VALIDATION_REPORT_PATH = "data/live/paper_handoff_validation_report.json"
_PAPER_COMPARISON_REPORT_PATH = "data/live/paper_handoff_comparison_report.json"

_OUTPUT_UNIVERSE = "data/live/active_opportunity_universe.json"
_OUTPUT_MANIFEST = "data/live/current_manifest.json"
_OUTPUT_REPORT = "data/live/handoff_publisher_report.json"
_OUTPUT_HEARTBEAT = "data/heartbeats/handoff_publisher.json"
_OUTPUT_RUN_LOG = "data/live/publisher_run_log.jsonl"

# ---------------------------------------------------------------------------
# Safety block — hardcoded, never from .env or config
# ---------------------------------------------------------------------------

_SAFETY = {
    "production_candidate_source_changed": False,
    "scanner_output_changed": False,
    "apex_input_changed": False,
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
    "source_labels", "theme_ids", "risk_flags",
    "confirmation_required", "approval_status", "quota_group",
    "freshness_status", "executable", "order_instruction",
    "live_output_changed",
)

_VALID_ROUTES = {
    "position", "swing", "intraday_swing", "watchlist",
    "manual_conviction", "do_not_touch", "etf_proxy",
    "attention", "structural",
}

_VALID_APPROVAL_STATUSES = {
    "approved", "manual_protected", "held_protected",
    "watchlist_allowed", "conditional",
}

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _quota_policy_version() -> str:
    """Return the current quota policy version string from quota_allocator."""
    try:
        from quota_allocator import QUOTA_POLICY_VERSION
        return QUOTA_POLICY_VERSION
    except Exception:
        return "unknown"


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: str) -> tuple[dict | None, str | None]:
    if not os.path.exists(path):
        return None, f"file not found: {path}"
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


def _write_atomic(path: str, data: dict) -> None:
    """Write JSON atomically: tmp → validate readable → os.replace."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        # verify the tmp is valid JSON before replacing
        with open(tmp, "r", encoding="utf-8") as fh:
            json.load(fh)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _write_fail_diagnostic(reason: str, context: dict) -> str:
    """Write a fail diagnostic JSON alongside the publisher report dir."""
    stamp = _ts(_now_utc()).replace(":", "").replace("-", "")
    fail_path = f"data/live/.fail_{stamp}.json"
    try:
        os.makedirs("data/live", exist_ok=True)
        with open(fail_path, "w", encoding="utf-8") as fh:
            json.dump({"fail_closed_reason": reason, "context": context,
                       "generated_at": _ts(_now_utc())}, fh, indent=2)
    except OSError:
        pass
    return fail_path


# ---------------------------------------------------------------------------
# Publisher run log — append-only, one line per successful cycle
# Classification: production observability output, not live bot input
# ---------------------------------------------------------------------------


def _append_run_log(
    now: datetime,
    candidate_count: int,
    manifest_expires_at: str,
    source_shadow_file: str,
    warnings: list[str],
) -> bool:
    """
    Append one JSON line to publisher_run_log.jsonl after a fully successful cycle.

    Called only after all 4 outputs (universe, manifest, report, heartbeat) are written.
    If append fails: records run_log_write_failed warning, does NOT corrupt manifest.
    Returns True on success, False on failure.

    Classification: production observability output — never read by live bot.
    """
    import uuid
    record = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "worker": "handoff_publisher",
        "completed_at": _ts(now),
        "utc_date": now.strftime("%Y-%m-%d"),
        "validation_status": "pass",
        "publication_mode": _PUBLICATION_MODE,
        "handoff_enabled": False,
        "enable_active_opportunity_universe_handoff": False,
        "active_universe_file": _OUTPUT_UNIVERSE,
        "current_manifest_file": _OUTPUT_MANIFEST,
        "candidate_count": candidate_count,
        "manifest_expires_at": manifest_expires_at,
        "freshness_status": "fresh",
        "source_shadow_file": source_shadow_file,
        "quota_policy_version": _quota_policy_version(),
        "safety_flags": {k: v for k, v in _SAFETY.items()},
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }
    try:
        os.makedirs(os.path.dirname(_OUTPUT_RUN_LOG) or ".", exist_ok=True)
        with open(_OUTPUT_RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except Exception as exc:
        warnings.append(f"run_log_write_failed: {exc}")
        log.warning("[handoff_publisher] run_log_write_failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Candidate transformation (shadow → production handoff shape)
# ---------------------------------------------------------------------------


def _extract_theme_ids(macro_rules_fired: list) -> list[str]:
    themes: set[str] = set()
    for rule_id in (macro_rules_fired or []):
        if isinstance(rule_id, str) and "_to_" in rule_id:
            themes.add(rule_id.split("_to_", 1)[1])
    return sorted(themes)


def _extract_risk_flags(cand: dict) -> list[str]:
    flags: set[str] = set()
    for item in (cand.get("invalidation") or []):
        if isinstance(item, str):
            flags.add(item)
    for note in (cand.get("risk_notes") or []):
        if isinstance(note, str):
            flags.add(note)
        elif isinstance(note, dict) and "flag" in note:
            flags.add(str(note["flag"]))
    return sorted(flags)


def _extract_route_hint(cand: dict) -> list[str]:
    exec_inst = cand.get("execution_instructions") or {}
    allowed = exec_inst.get("allowed_routes_when_live") or []
    if allowed:
        return list(allowed)
    return [cand.get("route") or "watchlist"]


def _extract_confirmation_required(cand: dict) -> list[str]:
    exec_inst = cand.get("execution_instructions") or {}
    return list(exec_inst.get("required_future_confirmation") or [])


def _extract_quota_group(cand: dict) -> str:
    return (cand.get("quota") or {}).get("group") or "unknown"


def _derive_approval_status(cand: dict) -> str:
    bucket_type = cand.get("bucket_type") or ""
    route = cand.get("route") or ""
    group = _extract_quota_group(cand)
    if bucket_type == "manual" or group == "manual_conviction" or route == "manual_conviction":
        return "manual_protected"
    if bucket_type == "held" or group == "held":
        return "held_protected"
    if route == "watchlist" and bucket_type not in ("structural", "attention"):
        return "watchlist_allowed"
    return "approved"


def _transform_candidate(shadow_cand: dict) -> dict:
    return {
        "symbol": shadow_cand.get("symbol") or "",
        "route": shadow_cand.get("route") or "watchlist",
        "route_hint": _extract_route_hint(shadow_cand),
        "reason_to_care": shadow_cand.get("reason_to_care") or "",
        "source_labels": list(shadow_cand.get("source_labels") or []),
        "theme_ids": _extract_theme_ids(shadow_cand.get("macro_rules_fired") or []),
        "risk_flags": _extract_risk_flags(shadow_cand),
        "confirmation_required": _extract_confirmation_required(shadow_cand),
        "approval_status": _derive_approval_status(shadow_cand),
        "quota_group": _extract_quota_group(shadow_cand),
        "freshness_status": "fresh",
        "executable": False,
        "order_instruction": None,
        "live_output_changed": False,
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_shadow_source(shadow: dict) -> list[str]:
    """Return list of validation error strings (empty = OK)."""
    errors: list[str] = []
    candidates = shadow.get("candidates")
    if not isinstance(candidates, list):
        errors.append("shadow universe: candidates is not a list")
        return errors
    if len(candidates) == 0:
        errors.append("shadow universe: no candidates (empty universe)")
        return errors
    for i, c in enumerate(candidates):
        sym = c.get("symbol") or f"idx_{i}"
        if not c.get("symbol"):
            errors.append(f"candidate {i}: missing symbol")
        exec_inst = c.get("execution_instructions") or {}
        if exec_inst.get("executable") is True:
            errors.append(f"candidate {sym}: source executable=true — shadow candidates must not be executable")
        if c.get("live_output_changed") is True:
            errors.append(f"candidate {sym}: source live_output_changed=true")
    return errors


def _validate_output_candidate(c: dict) -> list[str]:
    errors: list[str] = []
    sym = c.get("symbol") or "unknown"
    for field in _CANDIDATE_REQUIRED_FIELDS:
        if field not in c:
            errors.append(f"candidate {sym}: missing field '{field}'")
    if c.get("executable") is True:
        errors.append(f"candidate {sym}: executable must be false")
    if c.get("order_instruction") is not None:
        errors.append(f"candidate {sym}: order_instruction must be null")
    if not c.get("source_labels"):
        errors.append(f"candidate {sym}: source_labels is empty")
    if c.get("live_output_changed") is not False:
        errors.append(f"candidate {sym}: live_output_changed must be false")
    return errors


def _validate_output_universe(data: dict) -> list[str]:
    errors: list[str] = []
    required_top = (
        "schema_version", "generated_at", "expires_at", "mode",
        "publication_mode", "source_shadow_file", "source_files",
        "validation_status", "universe_summary", "candidates", "warnings",
        "no_executable_trade_instructions", "live_output_changed",
        "secrets_exposed", "env_values_logged",
    )
    for k in required_top:
        if k not in data:
            errors.append(f"active_universe: missing required field '{k}'")
    if data.get("mode") != _UNIVERSE_MODE:
        errors.append(f"active_universe: mode must be '{_UNIVERSE_MODE}', got {data.get('mode')!r}")
    if data.get("publication_mode") != _PUBLICATION_MODE:
        errors.append(f"active_universe: publication_mode must be '{_PUBLICATION_MODE}'")
    if data.get("no_executable_trade_instructions") is not True:
        errors.append("active_universe: no_executable_trade_instructions must be true")
    for flag in ("live_output_changed", "secrets_exposed", "env_values_logged"):
        if data.get(flag) is not False:
            errors.append(f"active_universe: {flag} must be false")
    for c in (data.get("candidates") or []):
        errors.extend(_validate_output_candidate(c))
    return errors


def _validate_output_manifest(data: dict, check_universe_exists: bool = False) -> list[str]:
    errors: list[str] = []
    required_top = (
        "schema_version", "published_at", "expires_at", "validation_status",
        "handoff_mode", "publication_mode", "handoff_enabled",
        "enable_flag_required", "active_universe_file", "source_snapshot_versions",
        "publisher", "warnings", "no_executable_trade_instructions",
        "live_output_changed", "secrets_exposed", "env_values_logged",
    )
    for k in required_top:
        if k not in data:
            errors.append(f"manifest: missing required field '{k}'")
    if data.get("handoff_enabled") is not False:
        errors.append(f"manifest: handoff_enabled must be false, got {data.get('handoff_enabled')!r}")
    if data.get("publication_mode") != _PUBLICATION_MODE:
        errors.append(f"manifest: publication_mode must be '{_PUBLICATION_MODE}'")
    if data.get("enable_flag_required") is not True:
        errors.append("manifest: enable_flag_required must be true")
    if data.get("no_executable_trade_instructions") is not True:
        errors.append("manifest: no_executable_trade_instructions must be true")
    for flag in ("live_output_changed", "secrets_exposed", "env_values_logged"):
        if data.get(flag) is not False:
            errors.append(f"manifest: {flag} must be false")
    # Only check file existence after the universe has been written
    if check_universe_exists:
        auf = data.get("active_universe_file") or ""
        if auf and not os.path.exists(auf):
            errors.append(f"manifest: active_universe_file does not exist: {auf!r}")
    return errors


# ---------------------------------------------------------------------------
# Build output documents
# ---------------------------------------------------------------------------


def _build_active_universe(
    shadow: dict,
    candidates: list[dict],
    generated_at: datetime,
    warnings: list[str],
    validation_status: str,
) -> dict:
    expires_at = generated_at + timedelta(minutes=_EXPIRY_MINUTES)
    src_files = shadow.get("source_files") or []
    universe_summary = shadow.get("universe_summary") or {}
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _ts(generated_at),
        "expires_at": _ts(expires_at),
        "mode": _UNIVERSE_MODE,
        "publication_mode": _PUBLICATION_MODE,
        "source_shadow_file": _SHADOW_UNIVERSE_PATH,
        "source_files": src_files,
        "validation_status": validation_status,
        "universe_summary": {
            "total_candidates": len(candidates),
            "shadow_total": universe_summary.get("total_candidates", len(candidates)),
            "structural_candidates": universe_summary.get("structural_candidates", 0),
            "manual_candidates": universe_summary.get("manual_candidates", 0),
            "attention_candidates": universe_summary.get("attention_candidates", 0),
            "etf_proxy_candidates": universe_summary.get("etf_proxy_candidates", 0),
        },
        "candidates": candidates,
        "warnings": warnings,
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }


def _build_manifest(
    generated_at: datetime,
    validation_status: str,
    fail_closed_reason: str | None,
    source_versions: dict,
    warnings: list[str],
) -> dict:
    expires_at = generated_at + timedelta(minutes=_EXPIRY_MINUTES)
    return {
        "schema_version": _SCHEMA_VERSION,
        "published_at": _ts(generated_at),
        "expires_at": _ts(expires_at),
        "validation_status": validation_status,
        "handoff_mode": _HANDOFF_MODE,
        "publication_mode": _PUBLICATION_MODE,
        "handoff_enabled": False,
        "enable_flag_required": True,
        "ready_for_consumption": validation_status == "pass",
        "active_universe_file": _OUTPUT_UNIVERSE,
        "economic_context_file": _ECONOMIC_CONTEXT_PATH if os.path.exists(_ECONOMIC_CONTEXT_PATH) else None,
        "theme_activation_file": _THEME_ACTIVATION_PATH if os.path.exists(_THEME_ACTIVATION_PATH) else None,
        "thesis_store_file": _THESIS_STORE_PATH if os.path.exists(_THESIS_STORE_PATH) else None,
        "symbol_master_file": _SYMBOL_MASTER_PATH if os.path.exists(_SYMBOL_MASTER_PATH) else None,
        "source_snapshot_versions": source_versions,
        "publisher": "handoff_publisher",
        "fail_closed_reason": fail_closed_reason,
        "warnings": warnings,
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }


def _build_publisher_report(
    generated_at: datetime,
    source_files: list[str],
    validation_summary: dict,
    candidate_summary: dict,
    rejected_candidates: list[dict],
    atomic_write_summary: dict,
    heartbeat_summary: dict,
    validation_status: str,
    fail_closed_reason: str | None,
    warnings: list[str],
) -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _ts(generated_at),
        "mode": "handoff_publisher_report",
        "publication_mode": _PUBLICATION_MODE,
        "source_files": source_files,
        "output_files": [_OUTPUT_UNIVERSE, _OUTPUT_MANIFEST, _OUTPUT_HEARTBEAT],
        "validation_summary": validation_summary,
        "candidate_summary": candidate_summary,
        "rejected_candidates": rejected_candidates,
        "atomic_write_summary": atomic_write_summary,
        "heartbeat_summary": heartbeat_summary,
        "safety_flags": {k: v for k, v in _SAFETY.items()},
        "ready_for_consumption": validation_status == "pass",
        "handoff_enabled": False,
        "enable_active_opportunity_universe_handoff_config_state": False,
        **_SAFETY,
    }


def _build_heartbeat(
    generated_at: datetime,
    validation_status: str,
    candidate_count: int,
    fail_closed_reason: str | None,
    last_attempt_at: str,
) -> dict:
    return {
        "worker": "handoff_publisher",
        "last_success_at": _ts(generated_at) if validation_status == "pass" else None,
        "last_attempt_at": last_attempt_at,
        "validation_status": validation_status,
        "active_universe_file": _OUTPUT_UNIVERSE,
        "current_manifest_file": _OUTPUT_MANIFEST,
        "candidate_count": candidate_count,
        "fail_closed_reason": fail_closed_reason,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }


# ---------------------------------------------------------------------------
# Public API — run_publisher()
# ---------------------------------------------------------------------------


def run_publisher() -> dict:
    """
    Execute one publish cycle.

    Returns the publisher report dict (also written to disk).
    Fail-closed: on any validation failure, does not overwrite valid output files.
    """
    now = _now_utc()
    attempt_ts = _ts(now)
    warnings: list[str] = []
    rejected_candidates: list[dict] = []
    fail_closed_reason: str | None = None

    # --- Step 1: Load shadow universe ---
    shadow, err = _load_json(_SHADOW_UNIVERSE_PATH)
    if err:
        fail_closed_reason = f"source_read_error: {err}"
        log.warning("[handoff_publisher] fail_closed: %s", fail_closed_reason)
        return _fail_closed_cycle(now, attempt_ts, fail_closed_reason, warnings)

    # --- Step 2: Validate shadow source ---
    source_errors = _validate_shadow_source(shadow)
    if source_errors:
        fail_closed_reason = f"source_validation_failed: {source_errors[0]}"
        log.warning("[handoff_publisher] fail_closed: %s", fail_closed_reason)
        return _fail_closed_cycle(now, attempt_ts, fail_closed_reason, warnings,
                                  extra_context={"source_errors": source_errors})

    # --- Step 3: Transform candidates ---
    raw_candidates = shadow.get("candidates") or []
    accepted: list[dict] = []
    for shadow_cand in raw_candidates:
        cand = _transform_candidate(shadow_cand)
        cand_errors = _validate_output_candidate(cand)
        if cand_errors:
            rejected_candidates.append({
                "symbol": cand.get("symbol"),
                "errors": cand_errors,
            })
            warnings.append(f"candidate rejected: {cand.get('symbol')} — {cand_errors[0]}")
        else:
            accepted.append(cand)

    if not accepted:
        fail_closed_reason = "zero_accepted_candidates"
        return _fail_closed_cycle(now, attempt_ts, fail_closed_reason, warnings,
                                  extra_context={"rejected_count": len(rejected_candidates)})

    # --- Step 4: Build output documents (in-memory) ---
    source_versions: dict[str, str] = {}
    source_files: list[str] = [_SHADOW_UNIVERSE_PATH]
    for path in (_ECONOMIC_CONTEXT_PATH, _THEME_ACTIVATION_PATH, _THESIS_STORE_PATH,
                 _SYMBOL_MASTER_PATH, _LAYER_FACTOR_MAP_PATH, _DATA_QUALITY_REPORT_PATH):
        if os.path.exists(path):
            source_files.append(path)
            data, _ = _load_json(path)
            if data:
                source_versions[path] = data.get("generated_at") or _ts(now)

    universe_doc = _build_active_universe(shadow, accepted, now, warnings, "pass")
    manifest_doc = _build_manifest(now, "pass", None, source_versions, warnings)

    # --- Step 5a: Validate universe in-memory before writing ---
    universe_errors = _validate_output_universe(universe_doc)
    # Validate manifest structure in-memory (skip file-existence check — universe not written yet)
    manifest_errors = _validate_output_manifest(manifest_doc, check_universe_exists=False)

    all_output_errors = universe_errors + manifest_errors
    if all_output_errors:
        fail_closed_reason = f"output_validation_failed: {all_output_errors[0]}"
        return _fail_closed_cycle(now, attempt_ts, fail_closed_reason, warnings,
                                  extra_context={"output_errors": all_output_errors})

    # --- Step 6: Atomic writes (universe first, then manifest) ---
    atomic_summary: dict = {"universe_written": False, "manifest_written": False,
                            "heartbeat_written": False, "errors": []}
    try:
        _write_atomic(_OUTPUT_UNIVERSE, universe_doc)
        atomic_summary["universe_written"] = True
    except Exception as exc:
        fail_closed_reason = f"universe_write_failed: {exc}"
        return _fail_closed_cycle(now, attempt_ts, fail_closed_reason, warnings)

    # Step 5b: Re-validate manifest now that the universe file exists
    post_write_errors = _validate_output_manifest(manifest_doc, check_universe_exists=True)
    if post_write_errors:
        fail_closed_reason = f"manifest_post_write_validation_failed: {post_write_errors[0]}"
        return _fail_closed_cycle(now, attempt_ts, fail_closed_reason, warnings,
                                  extra_context={"post_write_errors": post_write_errors})

    try:
        _write_atomic(_OUTPUT_MANIFEST, manifest_doc)
        atomic_summary["manifest_written"] = True
    except Exception as exc:
        fail_closed_reason = f"manifest_write_failed: {exc}"
        return _fail_closed_cycle(now, attempt_ts, fail_closed_reason, warnings)

    # --- Step 7: Heartbeat (only on full success) ---
    heartbeat_doc = _build_heartbeat(now, "pass", len(accepted), None, attempt_ts)
    try:
        _write_atomic(_OUTPUT_HEARTBEAT, heartbeat_doc)
        atomic_summary["heartbeat_written"] = True
    except Exception as exc:
        warnings.append(f"heartbeat write failed (non-critical): {exc}")

    # --- Step 7b: Append run log (after all 4 outputs written successfully) ---
    # Failure is non-critical: warns but does not corrupt manifest or prevent report.
    run_log_ok = _append_run_log(
        now, len(accepted), manifest_doc.get("expires_at", ""),
        _SHADOW_UNIVERSE_PATH, warnings,
    )
    atomic_summary["run_log_written"] = run_log_ok

    # --- Step 8: Publisher report ---
    validation_summary = {
        "source_errors": [],
        "output_errors": [],
        "candidate_errors": [r["errors"] for r in rejected_candidates],
        "overall_status": "pass",
    }
    candidate_summary = {
        "shadow_count": len(raw_candidates),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected_candidates),
    }
    heartbeat_summary = {
        "written": atomic_summary["heartbeat_written"],
        "path": _OUTPUT_HEARTBEAT,
    }
    report = _build_publisher_report(
        now, source_files, validation_summary, candidate_summary,
        rejected_candidates, atomic_summary, heartbeat_summary,
        "pass", None, warnings,
    )
    try:
        _write_atomic(_OUTPUT_REPORT, report)
    except Exception as exc:
        warnings.append(f"report write failed (non-critical): {exc}")

    log.info(
        "[handoff_publisher] publish_cycle=success accepted=%d rejected=%d "
        "handoff_enabled=false publication_mode=%s live_output_changed=false",
        len(accepted), len(rejected_candidates), _PUBLICATION_MODE,
    )
    return report


def _fail_closed_cycle(
    now: datetime,
    attempt_ts: str,
    fail_closed_reason: str,
    warnings: list[str],
    extra_context: dict | None = None,
) -> dict:
    """
    Write publisher report and heartbeat (fail state). Do NOT write universe or manifest.
    """
    heartbeat_doc = _build_heartbeat(now, "fail", 0, fail_closed_reason, attempt_ts)
    try:
        _write_atomic(_OUTPUT_HEARTBEAT, heartbeat_doc)
    except Exception as exc:
        warnings.append(f"heartbeat write failed in fail_closed: {exc}")

    fail_path = _write_fail_diagnostic(fail_closed_reason, extra_context or {})

    report = _build_publisher_report(
        now,
        source_files=[_SHADOW_UNIVERSE_PATH],
        validation_summary={
            "source_errors": [fail_closed_reason],
            "output_errors": [],
            "candidate_errors": [],
            "overall_status": "fail",
        },
        candidate_summary={"shadow_count": 0, "accepted_count": 0, "rejected_count": 0},
        rejected_candidates=[],
        atomic_write_summary={"universe_written": False, "manifest_written": False,
                              "heartbeat_written": True, "errors": [fail_closed_reason]},
        heartbeat_summary={"written": True, "path": _OUTPUT_HEARTBEAT},
        validation_status="fail",
        fail_closed_reason=fail_closed_reason,
        warnings=warnings + [f"fail diagnostic written to {fail_path}"],
    )
    try:
        _write_atomic(_OUTPUT_REPORT, report)
    except Exception:
        pass
    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    report = run_publisher()
    status = report.get("validation_summary", {}).get("overall_status", "unknown")
    count = report.get("candidate_summary", {}).get("accepted_count", 0)
    reason = report.get("fail_closed_reason")
    if status == "pass":
        print(f"[handoff_publisher] SUCCESS: {count} candidates published. "
              f"publication_mode={_PUBLICATION_MODE} handoff_enabled=false live_output_changed=false")
    else:
        print(f"[handoff_publisher] FAIL-CLOSED: {reason}. "
              f"Universe and manifest NOT updated. Heartbeat and report written.")
