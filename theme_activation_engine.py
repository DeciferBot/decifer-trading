"""
theme_activation_engine.py — Sprint 4B Theme Activation Layer.

Single responsibility: consume daily_economic_state.json (Sprint 4A),
transmission_rules.json, theme_taxonomy.json, thematic_roster.json,
economic_candidate_feed.json, and shadow universe/comparison files to
determine which themes are activated, with what confidence and direction.

Writes:
    data/intelligence/theme_activation.json

FORBIDDEN (hardcoded constants — never read from .env or config):
    - No live API calls
    - No broker calls
    - No .env inspection
    - No LLM calls
    - No raw news scraping
    - No broad intraday scanning
    - No production module side effects
    - live_output_changed = false
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Safety constants — hardcoded, never configurable
# ---------------------------------------------------------------------------
_NO_LIVE_API_CALLED: bool = True
_BROKER_CALLED: bool = False
_ENV_INSPECTED: bool = False
_RAW_NEWS_USED: bool = False
_LLM_USED: bool = False
_BROAD_INTRADAY_SCAN_USED: bool = False
_LIVE_OUTPUT_CHANGED: bool = False

# ---------------------------------------------------------------------------
# Theme state vocabulary
# ---------------------------------------------------------------------------
_STATE_ACTIVATED = "activated"
_STATE_STRENGTHENING = "strengthening"
_STATE_WATCHLIST = "watchlist"
_STATE_WEAKENING = "weakening"
_STATE_CROWDED = "crowded"
_STATE_INVALIDATED = "invalidated"
_STATE_DORMANT = "dormant"

_VALID_THEME_STATES = {
    _STATE_ACTIVATED, _STATE_STRENGTHENING, _STATE_WATCHLIST,
    _STATE_WEAKENING, _STATE_CROWDED, _STATE_INVALIDATED, _STATE_DORMANT,
}

# Confidence by state
_STATE_CONFIDENCE: dict[str, float] = {
    _STATE_ACTIVATED: 0.45,
    _STATE_STRENGTHENING: 0.40,
    _STATE_WATCHLIST: 0.28,
    _STATE_WEAKENING: 0.22,
    _STATE_CROWDED: 0.30,
    _STATE_INVALIDATED: 0.05,
    _STATE_DORMANT: 0.0,
}

# ---------------------------------------------------------------------------
# Driver state vocabulary (from Sprint 4A daily_economic_state)
# ---------------------------------------------------------------------------
_DRIVER_ACTIVE = "active_shadow_inferred"
_DRIVER_WATCH = "watch_shadow_inferred"
_DRIVER_INACTIVE = "inactive_shadow"
_DRIVER_UNAVAILABLE = "unavailable"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))
_INTEL_DIR = os.path.join(_BASE, "data", "intelligence")
_UB_DIR = os.path.join(_BASE, "data", "universe_builder")

_RULES_PATH = os.path.join(_INTEL_DIR, "transmission_rules.json")
_TAXONOMY_PATH = os.path.join(_INTEL_DIR, "theme_taxonomy.json")
_ROSTER_PATH = os.path.join(_INTEL_DIR, "thematic_roster.json")
_FEED_PATH = os.path.join(_INTEL_DIR, "economic_candidate_feed.json")
_DAILY_STATE_PATH = os.path.join(_INTEL_DIR, "daily_economic_state.json")
_CONTEXT_PATH = os.path.join(_INTEL_DIR, "current_economic_context.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")

_OUTPUT_PATH = os.path.join(_INTEL_DIR, "theme_activation.json")


def _read_json(path: str) -> tuple[Any, str | None]:
    if not os.path.exists(path):
        return None, f"not_found:{path}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, f"parse_error:{path}:{e}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Build per-theme records
# ---------------------------------------------------------------------------

def _build_themes(
    rules_data: dict | None,
    taxonomy_data: dict | None,
    roster_data: dict | None,
    feed_data: dict | None,
    daily_state_data: dict | None,
    shadow_data: dict | None,
) -> list[dict]:
    # ── Transmission rules → theme targets
    rules: list[dict] = []
    if isinstance(rules_data, dict):
        rules = [r for r in (rules_data.get("rules") or []) if isinstance(r, dict)]

    theme_rules: dict[str, list[dict]] = {}
    for rule in rules:
        for target in (rule.get("affected_targets") or []):
            theme_rules.setdefault(target, []).append(rule)

    # ── Driver states from Sprint 4A
    driver_states: dict[str, str] = {}
    if isinstance(daily_state_data, dict):
        ds = daily_state_data.get("driver_states")
        if isinstance(ds, dict):
            driver_states = ds

    # ── Candidate evidence from feed
    feed_by_theme: dict[str, list[dict]] = {}
    if isinstance(feed_data, dict):
        for c in (feed_data.get("candidates") or []):
            if isinstance(c, dict) and c.get("theme"):
                feed_by_theme.setdefault(c["theme"], []).append(c)

    # ── Shadow universe candidates by theme (source_labels check)
    shadow_by_theme: dict[str, int] = {}
    if isinstance(shadow_data, dict):
        for c in (shadow_data.get("candidates") or []):
            if isinstance(c, dict):
                # Theme is embedded in reason_to_care or source_labels — use macro_rules_fired
                for src in (c.get("source_labels") or []):
                    if "intelligence_first" in src or "economic_intelligence" in src:
                        # Attribute symbol to themes via feed cross-ref below
                        pass
        # Cross-reference: count shadow candidates whose symbols appear in feed per theme
        shadow_syms: set[str] = {
            c.get("symbol", "") for c in (shadow_data.get("candidates") or [])
            if isinstance(c, dict)
        }
        for theme_id, cands in feed_by_theme.items():
            count = sum(1 for c in cands if c.get("symbol", "") in shadow_syms)
            shadow_by_theme[theme_id] = count

    # ── Quota pressure from shadow universe summary
    quota_pressure_data: dict = {}
    if isinstance(shadow_data, dict):
        qpd = shadow_data.get("quota_pressure_diagnostics") or {}
        sp = qpd.get("structural_position") or {}
        if sp:
            quota_pressure_data = {
                "structural_position_demand": sp.get("demand_total", 0),
                "structural_position_capacity": sp.get("capacity", 20),
                "structural_position_accepted": sp.get("accepted", 0),
                "structural_position_overflow": sp.get("overflow", 0),
                "structural_quota_binding": sp.get("binding", False),
            }

    # ── Roster metadata
    roster_meta: dict[str, dict] = {}
    if isinstance(roster_data, dict):
        for r in (roster_data.get("rosters") or []):
            if isinstance(r, dict) and r.get("theme_id"):
                roster_meta[r["theme_id"]] = r

    # ── Taxonomy themes list
    taxonomy_themes: list[dict] = []
    if isinstance(taxonomy_data, dict):
        taxonomy_themes = [t for t in (taxonomy_data.get("themes") or []) if isinstance(t, dict)]

    freshness = "static_shadow_inference_sprint4b"
    result: list[dict] = []

    for theme in taxonomy_themes:
        theme_id = theme.get("theme_id", "")
        if not theme_id:
            continue

        rules_for_theme = theme_rules.get(theme_id, [])
        cands_in_feed = feed_by_theme.get(theme_id, [])
        cand_count = len(cands_in_feed)
        cands_in_shadow = shadow_by_theme.get(theme_id, 0)
        cands_excluded = max(0, cand_count - cands_in_shadow)

        roster = roster_meta.get(theme_id, {})
        is_headwind_roster = roster.get("headwind_roster", False)

        # Determine direction from rules
        rule_directions = [r.get("output_type", "") for r in rules_for_theme]
        if any("headwind" in d for d in rule_directions) or is_headwind_roster:
            direction = "headwind"
        elif any("tailwind" in d for d in rule_directions):
            direction = "tailwind"
        else:
            direction = "neutral"

        # Collect driver states for rules targeting this theme
        active_drivers: list[str] = []
        rules_fired: list[str] = []
        activated_by: list[str] = []
        weakened_by: list[str] = []
        evidence: list[str] = []

        best_driver_state = _DRIVER_INACTIVE

        for rule in rules_for_theme:
            rule_id = rule.get("rule_id", "")
            if rule_id:
                rules_fired.append(rule_id)

            # Match driver state — try alias first, then canonical
            driver_alias = rule.get("driver_alias", "")
            driver_canonical = rule.get("driver", "")

            state = (
                driver_states.get(driver_alias)
                or driver_states.get(driver_canonical)
                or _DRIVER_INACTIVE
            )

            if state == _DRIVER_ACTIVE:
                if best_driver_state != _DRIVER_ACTIVE:
                    best_driver_state = _DRIVER_ACTIVE
                driver_id = driver_alias or driver_canonical
                if driver_id and driver_id not in active_drivers:
                    active_drivers.append(driver_id)
                if rule_id:
                    activated_by.append(rule_id)
                evidence.append(
                    f"driver_{driver_id}_active_via_rule_{rule_id}"
                )
            elif state == _DRIVER_WATCH:
                if best_driver_state == _DRIVER_INACTIVE:
                    best_driver_state = _DRIVER_WATCH
                driver_id = driver_alias or driver_canonical
                if driver_id and driver_id not in active_drivers:
                    active_drivers.append(driver_id)
                evidence.append(
                    f"driver_{driver_id}_watch_via_rule_{rule_id}"
                )

        if cand_count > 0:
            evidence.append(f"{cand_count}_candidates_in_economic_feed")
        if cands_in_shadow > 0:
            evidence.append(f"{cands_in_shadow}_candidates_in_shadow_universe")

        # Check if quota overflow (structural binding + candidates excluded)
        is_crowded = (
            quota_pressure_data.get("structural_quota_binding", False)
            and cands_excluded > 0
            and direction == "tailwind"
        )

        # Check for pressure_candidates (headwind)
        has_pressure = any(c.get("role") == "pressure_candidate" for c in cands_in_feed)
        if has_pressure:
            evidence.append("pressure_candidate_present_in_feed")

        # ── Determine theme state
        if direction == "headwind":
            if best_driver_state in (_DRIVER_ACTIVE, _DRIVER_WATCH) or has_pressure:
                theme_state = _STATE_WEAKENING
                weakened_by = [r.get("rule_id", "") for r in rules_for_theme if r.get("rule_id")]
            elif cand_count > 0:
                theme_state = _STATE_WATCHLIST
            else:
                theme_state = _STATE_DORMANT
        elif is_crowded:
            theme_state = _STATE_CROWDED
        elif best_driver_state == _DRIVER_ACTIVE and cand_count > 0:
            # Active driver + candidates → activated; if strong rule count → strengthening
            if len(rules_fired) >= 2:
                theme_state = _STATE_STRENGTHENING
            else:
                theme_state = _STATE_ACTIVATED
        elif best_driver_state == _DRIVER_WATCH and cand_count > 0:
            theme_state = _STATE_WATCHLIST
        elif cand_count > 0:
            # Candidates present but driver unknown → watchlist
            theme_state = _STATE_WATCHLIST
        else:
            theme_state = _STATE_DORMANT

        # Evidence limited if no evidence from driver states
        evidence_limited = len(active_drivers) == 0 and best_driver_state == _DRIVER_INACTIVE

        confidence = _STATE_CONFIDENCE[theme_state]
        if evidence_limited:
            confidence = round(confidence * 0.5, 4)
            evidence.append("evidence_limited_no_active_driver_state")

        # Pull metadata from taxonomy
        tax_confirmation = theme.get("confirmation_requirements") or []
        tax_risk_flags = theme.get("risk_flags") or []
        tax_invalidation = theme.get("invalidation_examples") or []
        tax_horizon = theme.get("typical_horizon", "")

        # Quota pressure summary for this theme
        theme_quota_pressure: dict = {}
        if quota_pressure_data:
            theme_quota_pressure = {
                "structural_quota_binding": quota_pressure_data.get("structural_quota_binding", False),
                "overflow": quota_pressure_data.get("structural_position_overflow", 0),
            }

        result.append({
            "theme_id": theme_id,
            "state": theme_state,
            "previous_state": None,        # set by thesis_store on subsequent runs
            "activated_by": activated_by,
            "weakened_by": weakened_by,
            "invalidated_by": [],
            "active_drivers": active_drivers,
            "transmission_rules_fired": rules_fired,
            "direction": direction,
            "confidence": confidence,
            "horizon": tax_horizon,
            "reason": (
                f"Theme {theme_id} direction={direction}, state={theme_state}, "
                f"driver_state={best_driver_state}, candidates={cand_count}"
            ),
            "confirmation_requirements": tax_confirmation,
            "risk_flags": tax_risk_flags,
            "invalidation_rules": tax_invalidation,
            "freshness_status": freshness,
            "route_bias": roster.get("route_bias", ""),
            "candidate_count": cand_count,
            "candidates_in_shadow_count": cands_in_shadow,
            "candidates_excluded_count": cands_excluded,
            "quota_pressure": theme_quota_pressure,
            "evidence": evidence,
            "evidence_limited": evidence_limited,
            "source_label": "intelligence_first_static_rule",
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        })

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_theme_activation(output_path: str = _OUTPUT_PATH) -> dict:
    """
    Read local files, compute theme activation states, write theme_activation.json.
    No live API calls. No broker. No .env. No LLM. No raw news.
    live_output_changed = false.
    """
    input_paths = [
        _RULES_PATH, _TAXONOMY_PATH, _ROSTER_PATH, _FEED_PATH,
        _DAILY_STATE_PATH, _CONTEXT_PATH, _SHADOW_PATH,
    ]

    source_files: list[str] = []
    unavailable_sources: list[str] = []
    loaded: dict[str, Any] = {}

    for path in input_paths:
        rel = os.path.relpath(path, _BASE)
        data, err = _read_json(path)
        if err:
            unavailable_sources.append(rel)
            loaded[path] = None
        else:
            source_files.append(rel)
            loaded[path] = data

    themes = _build_themes(
        rules_data=loaded.get(_RULES_PATH),
        taxonomy_data=loaded.get(_TAXONOMY_PATH),
        roster_data=loaded.get(_ROSTER_PATH),
        feed_data=loaded.get(_FEED_PATH),
        daily_state_data=loaded.get(_DAILY_STATE_PATH),
        shadow_data=loaded.get(_SHADOW_PATH),
    )

    # activation_summary
    by_state: dict[str, int] = {}
    for t in themes:
        by_state[t["state"]] = by_state.get(t["state"], 0) + 1

    low_confidence_count = sum(1 for t in themes if t["confidence"] < 0.20)
    evidence_limited_count = sum(1 for t in themes if t.get("evidence_limited", False))

    activation_summary: dict[str, Any] = {
        "total_themes": len(themes),
        "activated": by_state.get(_STATE_ACTIVATED, 0),
        "strengthening": by_state.get(_STATE_STRENGTHENING, 0),
        "watchlist": by_state.get(_STATE_WATCHLIST, 0),
        "weakening": by_state.get(_STATE_WEAKENING, 0),
        "crowded": by_state.get(_STATE_CROWDED, 0),
        "invalidated": by_state.get(_STATE_INVALIDATED, 0),
        "dormant": by_state.get(_STATE_DORMANT, 0),
        "low_confidence_count": low_confidence_count,
        "evidence_limited_count": evidence_limited_count,
        "no_live_api_called": _NO_LIVE_API_CALLED,
        "live_output_changed": _LIVE_OUTPUT_CHANGED,
    }

    warnings: list[str] = []
    if unavailable_sources:
        warnings.append(
            f"Sprint 4B: {len(unavailable_sources)} source file(s) unavailable. "
            f"Missing: {unavailable_sources}"
        )
    warnings.append(
        "Sprint 4B: theme activation inferred from local shadow files only. "
        "No live market data. Confidence scores are conservative estimates. "
        "Theme activation cannot create symbols or executable candidates."
    )

    output: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": _now_iso(),
        "valid_for_session": _today(),
        "mode": "shadow_theme_activation",
        "data_source_mode": "local_shadow_outputs_only",
        "source_files": source_files,
        "unavailable_sources": unavailable_sources,
        "warnings": warnings,
        "activation_summary": activation_summary,
        "themes": themes,
        "no_live_api_called": _NO_LIVE_API_CALLED,
        "broker_called": _BROKER_CALLED,
        "env_inspected": _ENV_INSPECTED,
        "raw_news_used": _RAW_NEWS_USED,
        "llm_used": _LLM_USED,
        "broad_intraday_scan_used": _BROAD_INTRADAY_SCAN_USED,
        "live_output_changed": _LIVE_OUTPUT_CHANGED,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    return output


if __name__ == "__main__":
    result = generate_theme_activation()
    s = result["activation_summary"]
    print(f"theme_activation.json → {_OUTPUT_PATH}")
    print(f"  total_themes:      {s['total_themes']}")
    print(f"  activated:         {s['activated']}")
    print(f"  strengthening:     {s['strengthening']}")
    print(f"  watchlist:         {s['watchlist']}")
    print(f"  weakening:         {s['weakening']}")
    print(f"  crowded:           {s['crowded']}")
    print(f"  invalidated:       {s['invalidated']}")
    print(f"  dormant:           {s['dormant']}")
    print(f"  low_confidence:    {s['low_confidence_count']}")
    print(f"  evidence_limited:  {s['evidence_limited_count']}")
    print(f"  live_output_changed: {result['live_output_changed']}")
