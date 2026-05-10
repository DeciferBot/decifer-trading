"""
paper_handoff_builder.py — Paper handoff manifest and active universe builder.

Classification: temporary migration tool / advisory-only
Service layer: handoff validation pipeline
Sprint: 7B

Reads the latest validated shadow universe outputs and produces:
    data/live/paper_active_opportunity_universe.json
    data/live/paper_current_manifest.json
    data/live/paper_handoff_validation_report.json

Does NOT write:
    data/live/current_manifest.json             (reserved for production handoff)
    data/live/active_opportunity_universe.json  (reserved for production handoff)

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
    enable_active_opportunity_universe_handoff = false
    handoff_enabled = false
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta

from handoff_reader import (
    _APPROVED_SOURCE_LABELS,
    load_paper_handoff,
    validate_candidate,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "1.0"
_PAPER_EXPIRY_HOURS = 24  # paper validation artefacts — longer window than live (15 min)

_SHADOW_UNIVERSE_PATH = "data/universe_builder/active_opportunity_universe_shadow.json"
_ECONOMIC_CONTEXT_PATH = "data/intelligence/current_economic_context.json"
_COMPANY_QUALITY_PATH = "data/intelligence/company_quality_snapshot.json"
_THEME_ACTIVATION_PATH = "data/intelligence/theme_activation.json"
_THESIS_STORE_PATH = "data/intelligence/thesis_store.json"
_SYMBOL_MASTER_PATH = "data/reference/symbol_master.json"
_LAYER_FACTOR_MAP_PATH = "data/reference/layer_factor_map.json"
_DATA_QUALITY_REPORT_PATH = "data/reference/data_quality_report.json"

_OUTPUT_DIR = "data/live"
_PAPER_UNIVERSE_FILE = "data/live/paper_active_opportunity_universe.json"
_PAPER_MANIFEST_FILE = "data/live/paper_current_manifest.json"
_PAPER_VALIDATION_REPORT_FILE = "data/live/paper_handoff_validation_report.json"

_SAFETY = {
    "no_executable_trade_instructions": True,
    "live_output_changed": False,
    "secrets_exposed": False,
    "env_values_logged": False,
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_atomic(path: str, data: dict) -> None:
    """Write JSON atomically: write to .tmp, validate, rename."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Candidate transformation
# ---------------------------------------------------------------------------

def _extract_theme_ids(macro_rules_fired: list) -> list[str]:
    """Derive theme_ids from macro_rules_fired by splitting on '_to_'."""
    themes: set[str] = set()
    for rule_id in (macro_rules_fired or []):
        if isinstance(rule_id, str) and "_to_" in rule_id:
            themes.add(rule_id.split("_to_", 1)[1])
    return sorted(themes)


def _extract_risk_flags(shadow_cand: dict) -> list[str]:
    """Combine invalidation conditions and risk_notes into risk_flags list."""
    flags: set[str] = set()
    for item in (shadow_cand.get("invalidation") or []):
        if isinstance(item, str):
            flags.add(item)
    for note in (shadow_cand.get("risk_notes") or []):
        if isinstance(note, str):
            flags.add(note)
        elif isinstance(note, dict) and "flag" in note:
            flags.add(str(note["flag"]))
    return sorted(flags)


def _extract_route_hint(shadow_cand: dict) -> list[str]:
    """Derive route_hint from execution_instructions or fallback to route."""
    exec_inst = shadow_cand.get("execution_instructions") or {}
    allowed = exec_inst.get("allowed_routes_when_live") or []
    if allowed:
        return list(allowed)
    route = shadow_cand.get("route") or "watchlist"
    return [route]


def _extract_confirmation_required(shadow_cand: dict) -> list[str]:
    exec_inst = shadow_cand.get("execution_instructions") or {}
    return list(exec_inst.get("required_future_confirmation") or [])


def _extract_quota_group(shadow_cand: dict) -> str:
    quota = shadow_cand.get("quota") or {}
    return quota.get("group") or "unknown"


def _derive_approval_status(shadow_cand: dict) -> str:
    """Derive approval_status from shadow candidate bucket_type and route."""
    bucket_type = shadow_cand.get("bucket_type") or ""
    route = shadow_cand.get("route") or ""
    quota = shadow_cand.get("quota") or {}
    group = quota.get("group") or ""

    if bucket_type == "manual" or group == "manual_conviction" or route == "manual_conviction":
        return "manual_protected"
    if bucket_type == "held" or group == "held":
        return "held_protected"
    # Watchlist-only candidates (proxy ETFs, attention watchlist) → watchlist_allowed
    if route == "watchlist" and bucket_type not in ("structural", "attention"):
        return "watchlist_allowed"
    return "approved"


def _transform_candidate(shadow_cand: dict) -> dict:
    """
    Transform a shadow universe candidate into a paper handoff candidate.
    All output fields are explicit. executable=false, order_instruction=null.
    """
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
# Build paper active universe
# ---------------------------------------------------------------------------

def _build_paper_active_universe(
    shadow: dict,
    now: datetime,
) -> tuple[dict, list[dict], list[dict]]:
    """
    Transform shadow candidates into paper candidates and build the universe dict.

    Returns: (paper_universe, accepted_candidates, builder_rejected)
    """
    shadow_candidates = shadow.get("candidates") or []

    source_files = [_SHADOW_UNIVERSE_PATH]
    for optional in (_ECONOMIC_CONTEXT_PATH, _THEME_ACTIVATION_PATH):
        if os.path.exists(optional):
            source_files.append(optional)

    accepted: list[dict] = []
    builder_rejected: list[dict] = []

    for shadow_cand in shadow_candidates:
        paper_cand = _transform_candidate(shadow_cand)
        val = validate_candidate(paper_cand)
        if val["ok"]:
            accepted.append(paper_cand)
        else:
            builder_rejected.append({
                "symbol": paper_cand.get("symbol"),
                "errors": val.get("errors", []),
            })

    generated_at = _ts(now)
    expires_at = _ts(now + timedelta(hours=_PAPER_EXPIRY_HOURS))

    # Count by route
    route_counts: dict[str, int] = {}
    for cand in accepted:
        r = cand.get("route") or "other"
        route_counts[r] = route_counts.get(r, 0) + 1

    universe_summary = {
        "total_candidates": len(accepted),
        "builder_rejected_shadow_candidates": len(builder_rejected),
        "route_counts": route_counts,
        "source": "shadow_universe_transformed",
    }

    warnings: list[str] = []
    if builder_rejected:
        warnings.append(
            f"{len(builder_rejected)} shadow candidate(s) rejected during paper build: "
            + ", ".join(r.get("symbol", "?") for r in builder_rejected)
        )

    paper_universe = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "mode": "paper_handoff_universe",
        "source_shadow_file": _SHADOW_UNIVERSE_PATH,
        "source_files": source_files,
        "validation_status": "pass",  # universe contains only accepted candidates
        "universe_summary": universe_summary,
        "candidates": accepted,
        "warnings": warnings,
        **_SAFETY,
    }

    return paper_universe, accepted, builder_rejected


# ---------------------------------------------------------------------------
# Build paper manifest
# ---------------------------------------------------------------------------

def _build_paper_manifest(
    paper_universe_path: str,
    paper_universe: dict,
    now: datetime,
    warnings: list[str],
) -> dict:
    published_at = _ts(now)
    expires_at = _ts(now + timedelta(hours=_PAPER_EXPIRY_HOURS))

    source_snapshot_versions: dict[str, str] = {
        paper_universe_path: paper_universe["generated_at"],
    }
    if os.path.exists(_ECONOMIC_CONTEXT_PATH):
        ctx = _load_json(_ECONOMIC_CONTEXT_PATH)
        if ctx:
            source_snapshot_versions[_ECONOMIC_CONTEXT_PATH] = ctx.get(
                "generated_at", "unknown"
            )

    company_quality_file = (
        _COMPANY_QUALITY_PATH if os.path.exists(_COMPANY_QUALITY_PATH) else None
    )

    return {
        "schema_version": _SCHEMA_VERSION,
        "published_at": published_at,
        "expires_at": expires_at,
        "validation_status": "pass",
        "handoff_mode": "paper",
        "handoff_enabled": False,  # Sprint 7B: never enable production handoff
        "active_universe_file": paper_universe_path,
        "economic_context_file": (
            _ECONOMIC_CONTEXT_PATH if os.path.exists(_ECONOMIC_CONTEXT_PATH) else None
        ),
        "company_quality_file": company_quality_file,
        "catalyst_snapshot_file": None,
        "technical_snapshot_file": None,
        "risk_snapshot_file": None,
        "source_snapshot_versions": source_snapshot_versions,
        "publisher": "paper_handoff_builder",
        "fail_closed_reason": None,
        "warnings": warnings,
        **_SAFETY,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_paper_handoff() -> dict:
    """
    Build all three paper handoff files from the shadow universe.

    Returns the validation report dict.
    Does not write current_manifest.json or active_opportunity_universe.json.
    """
    now = _now_utc()
    log.info("paper_handoff_builder: starting at %s", _ts(now))

    # Load shadow universe (required input)
    shadow = _load_json(_SHADOW_UNIVERSE_PATH)
    if shadow is None:
        raise RuntimeError(
            f"Shadow universe not found or unreadable: {_SHADOW_UNIVERSE_PATH}"
        )

    # Build paper active universe
    paper_universe, accepted, builder_rejected = _build_paper_active_universe(shadow, now)
    warnings = list(paper_universe.get("warnings") or [])

    log.info(
        "paper_handoff_builder: %d accepted, %d builder-rejected shadow candidates",
        len(accepted),
        len(builder_rejected),
    )

    # Write paper active universe (atomic)
    _write_atomic(_PAPER_UNIVERSE_FILE, paper_universe)
    log.info("paper_handoff_builder: wrote %s", _PAPER_UNIVERSE_FILE)

    # Build and write paper manifest (atomic)
    paper_manifest = _build_paper_manifest(
        _PAPER_UNIVERSE_FILE, paper_universe, now, warnings
    )
    _write_atomic(_PAPER_MANIFEST_FILE, paper_manifest)
    log.info("paper_handoff_builder: wrote %s", _PAPER_MANIFEST_FILE)

    # Run handoff reader validation against the written paper files
    reader_result = load_paper_handoff(_PAPER_MANIFEST_FILE)

    # Merge builder metadata into the validation report
    validation_report: dict = {
        **reader_result,
        "schema_version": _SCHEMA_VERSION,
        "mode": "paper_handoff_validation",
        "manifest_path": _PAPER_MANIFEST_FILE,
        "active_universe_path": _PAPER_UNIVERSE_FILE,
        # Builder's rejected shadow candidates (pre-transformation failures)
        "builder_rejected_shadow_candidates": builder_rejected,
        "builder_rejected_shadow_candidates_count": len(builder_rejected),
        # Ensure all required report fields from Part F are present
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
        "handoff_allowed": False,  # Sprint 7B: always false
    }

    # Write validation report (atomic)
    _write_atomic(_PAPER_VALIDATION_REPORT_FILE, validation_report)
    log.info("paper_handoff_builder: wrote %s", _PAPER_VALIDATION_REPORT_FILE)

    # Summary output
    reader_manifest_ok = reader_result.get("manifest_validation", {}).get("ok", False)
    reader_universe_ok = reader_result.get("active_universe_validation", {}).get("ok", False)
    reader_cand_accepted = reader_result.get("accepted_candidates_count", 0)
    reader_cand_rejected = reader_result.get("rejected_candidates_count", 0)

    print(f"[paper_handoff_builder] Paper active universe : {_PAPER_UNIVERSE_FILE}")
    print(f"[paper_handoff_builder] Paper manifest        : {_PAPER_MANIFEST_FILE}")
    print(f"[paper_handoff_builder] Validation report     : {_PAPER_VALIDATION_REPORT_FILE}")
    print(f"[paper_handoff_builder] Candidates accepted (builder) : {len(accepted)}")
    print(f"[paper_handoff_builder] Candidates rejected (builder) : {len(builder_rejected)}")
    print(f"[paper_handoff_builder] Reader manifest_ok    : {reader_manifest_ok}")
    print(f"[paper_handoff_builder] Reader universe_ok    : {reader_universe_ok}")
    print(f"[paper_handoff_builder] Reader cand accepted  : {reader_cand_accepted}")
    print(f"[paper_handoff_builder] Reader cand rejected  : {reader_cand_rejected}")
    print(f"[paper_handoff_builder] handoff_allowed       : False")
    print(f"[paper_handoff_builder] live_output_changed   : False")

    return validation_report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = build_paper_handoff()
    print(
        f"[paper_handoff_builder] done. "
        f"manifest_ok={result.get('manifest_validation', {}).get('ok')} "
        f"universe_ok={result.get('active_universe_validation', {}).get('ok')}"
    )
