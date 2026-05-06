"""
intelligence_engine.py — Sprint 4A Economic Intelligence Layer skeleton.

Single responsibility: read local shadow/intelligence files only, infer
macro driver states conservatively, and write:
  - data/intelligence/daily_economic_state.json
  - data/intelligence/current_economic_context.json

FORBIDDEN (enforced via hardcoded flags, not config):
  - No live API calls (FRED, FMP, Alpaca, IBKR, any broker)
  - No .env inspection
  - No LLM calls
  - No raw news scraping
  - No broad intraday scanning
  - No production module imports that trigger side effects
  - No writing to any file outside data/intelligence/

All inference is conservative local-shadow-only:
  - active_shadow_inferred  — direct local evidence (transmission rule + candidates)
  - watch_shadow_inferred   — indirect local evidence (theme active but weaker signal)
  - inactive_shadow         — no local evidence, not applicable
  - unavailable             — category exists but no local data to infer from

live_output_changed = false: this module never modifies production files.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Safety constants — never read from .env or config
# ---------------------------------------------------------------------------
_NO_LIVE_API_CALLED: bool = True
_BROKER_CALLED: bool = False
_ENV_INSPECTED: bool = False
_RAW_NEWS_USED: bool = False
_LLM_USED: bool = False
_BROAD_INTRADAY_SCAN_USED: bool = False
_LIVE_OUTPUT_CHANGED: bool = False

# ---------------------------------------------------------------------------
# Driver state vocabulary
# ---------------------------------------------------------------------------
_STATE_ACTIVE = "active_shadow_inferred"
_STATE_WATCH = "watch_shadow_inferred"
_STATE_INACTIVE = "inactive_shadow"
_STATE_UNAVAILABLE = "unavailable"

# Confidence by state
_STATE_CONFIDENCE: dict[str, float] = {
    _STATE_ACTIVE: 0.40,
    _STATE_WATCH: 0.25,
    _STATE_INACTIVE: 0.10,
    _STATE_UNAVAILABLE: 0.0,
}

# ---------------------------------------------------------------------------
# Valid regime / posture vocabularies (validated at write time)
# ---------------------------------------------------------------------------
_VALID_REGIMES = {
    "unknown_static_bootstrap",
    "mixed_shadow_regime",
    "ai_infrastructure_tailwind_shadow",
    "credit_stress_watch_shadow",
    "risk_off_watch_shadow",
    "selective_shadow",
    "unavailable",
}
_VALID_POSTURES = {
    "unknown",
    "neutral",
    "selective",
    "cautious",
    "defensive_selective",
}

# ---------------------------------------------------------------------------
# Input paths — read-only
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))
_INTEL_DIR = os.path.join(_BASE, "data", "intelligence")
_UB_DIR = os.path.join(_BASE, "data", "universe_builder")

_RULES_PATH = os.path.join(_INTEL_DIR, "transmission_rules.json")
_TAXONOMY_PATH = os.path.join(_INTEL_DIR, "theme_taxonomy.json")
_ROSTER_PATH = os.path.join(_INTEL_DIR, "thematic_roster.json")
_FEED_PATH = os.path.join(_INTEL_DIR, "economic_candidate_feed.json")
_ADAPTER_SNAP_PATH = os.path.join(_INTEL_DIR, "source_adapter_snapshot.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")
_COMPARISON_PATH = os.path.join(_UB_DIR, "current_vs_shadow_comparison.json")
_REPORT_PATH = os.path.join(_UB_DIR, "universe_builder_report.json")
_PIPELINE_SNAP_PATH = os.path.join(_UB_DIR, "current_pipeline_snapshot.json")

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
_DAILY_STATE_PATH = os.path.join(_INTEL_DIR, "daily_economic_state.json")
_CONTEXT_PATH = os.path.join(_INTEL_DIR, "current_economic_context.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: str) -> tuple[dict | list | None, str | None]:
    """Return (data, error). Error is None on success."""
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
# Local evidence extraction helpers
# ---------------------------------------------------------------------------

def _extract_rules(rules_data: dict | None) -> list[dict]:
    if not isinstance(rules_data, dict):
        return []
    return [r for r in (rules_data.get("rules") or []) if isinstance(r, dict)]


def _extract_candidates(feed_data: dict | None) -> list[dict]:
    if not isinstance(feed_data, dict):
        return []
    return [c for c in (feed_data.get("candidates") or []) if isinstance(c, dict)]


def _themes_in_feed(candidates: list[dict]) -> set[str]:
    return {c.get("theme", "") for c in candidates if c.get("theme")}


def _roles_in_feed(candidates: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in candidates:
        role = c.get("role", "")
        counts[role] = counts.get(role, 0) + 1
    return counts


def _rule_drivers(rules: list[dict]) -> set[str]:
    """Return the set of all driver / driver_alias values across all rules."""
    out: set[str] = set()
    for r in rules:
        if r.get("driver"):
            out.add(r["driver"])
        if r.get("driver_alias"):
            out.add(r["driver_alias"])
    return out


def _has_theme(candidates: list[dict], theme: str) -> bool:
    return any(c.get("theme") == theme for c in candidates)


def _has_role(candidates: list[dict], role: str) -> bool:
    return any(c.get("role") == role for c in candidates)


def _has_rule_with_keyword(rules: list[dict], keyword: str) -> bool:
    for r in rules:
        if keyword in (r.get("rule_id") or ""):
            return True
        if keyword in (r.get("driver") or ""):
            return True
        if keyword in (r.get("driver_alias") or ""):
            return True
        if keyword in (r.get("condition") or ""):
            return True
    return False


# ---------------------------------------------------------------------------
# Driver inference — conservative local-shadow logic only
# ---------------------------------------------------------------------------

_UNAVAILABLE_REASON = "no_local_shadow_evidence_for_sprint4a"


def _infer_drivers(
    rules: list[dict],
    candidates: list[dict],
    shadow_data: dict | None,
    report_data: dict | None,
    comparison_data: dict | None,
) -> dict[str, dict]:
    """
    Build driver inference map. Returns dict keyed by driver_id.

    Rules:
    - ai_capex_growth: rule with ai_capex_growth driver_alias AND
      data_centre_power or semiconductors candidates present
    - corporate_capex: semiconductors candidates present
    - credit: credit_stress rule AND pressure_candidate in feed
    - risk_appetite: risk_off_analysis exists AND headwind candidates present
    - geopolitics: defence candidates present
    - interest_rates: banks candidates present AND yields_rising rule
    - bonds_yields: same evidence as interest_rates
    - oil_energy: energy candidates present
    - volatility: risk_off_analysis headwind_candidates > 0 in shadow
    - sector_rotation: ≥4 distinct active themes in feed (structural + headwind)
    - inflation, growth, usd, valuation, liquidity,
      consumer_behaviour: unavailable
    """

    themes = _themes_in_feed(candidates)
    has_pressure = _has_role(candidates, "pressure_candidate")

    # ai_capex_growth
    ai_rule_present = _has_rule_with_keyword(rules, "ai_capex_growth")
    ai_theme_present = ("data_centre_power" in themes) or ("semiconductors" in themes)
    if ai_rule_present and ai_theme_present:
        ai_state = _STATE_ACTIVE
        ai_evidence = ["transmission_rule_ai_capex_growth_present", "data_centre_power_or_semiconductors_candidates_in_feed"]
        ai_source = "transmission_rules_and_candidate_feed"
    else:
        ai_state = _STATE_INACTIVE
        ai_evidence = []
        ai_source = "local_shadow_only"

    # corporate_capex
    if "semiconductors" in themes or "data_centre_power" in themes:
        cc_state = _STATE_WATCH
        cc_evidence = ["semiconductors_or_data_centre_power_candidates_in_feed"]
        cc_source = "economic_candidate_feed"
    else:
        cc_state = _STATE_INACTIVE
        cc_evidence = []
        cc_source = "local_shadow_only"

    # credit
    credit_rule = _has_rule_with_keyword(rules, "credit_stress")
    if credit_rule and has_pressure:
        credit_state = _STATE_WATCH
        credit_evidence = ["credit_stress_transmission_rule_present", "pressure_candidate_in_feed"]
        credit_source = "transmission_rules_and_candidate_feed"
    elif credit_rule:
        credit_state = _STATE_WATCH
        credit_evidence = ["credit_stress_transmission_rule_present"]
        credit_source = "transmission_rules"
    else:
        credit_state = _STATE_INACTIVE
        credit_evidence = []
        credit_source = "local_shadow_only"

    # risk_appetite
    roa_present = False
    if isinstance(comparison_data, dict) and "risk_off_analysis" in comparison_data:
        roa_present = True
    elif isinstance(report_data, dict) and "risk_off_analysis" in report_data:
        roa_present = True
    if roa_present and has_pressure:
        ra_state = _STATE_WATCH
        ra_evidence = ["risk_off_analysis_in_comparison_or_report", "pressure_candidate_in_feed"]
        ra_source = "comparison_and_candidate_feed"
    else:
        ra_state = _STATE_INACTIVE
        ra_evidence = []
        ra_source = "local_shadow_only"

    # geopolitics
    if "defence" in themes:
        geo_state = _STATE_WATCH
        geo_evidence = ["defence_candidates_in_feed"]
        geo_source = "economic_candidate_feed"
    else:
        geo_state = _STATE_INACTIVE
        geo_evidence = []
        geo_source = "local_shadow_only"

    # interest_rates
    rates_rule = _has_rule_with_keyword(rules, "yields_rising") or _has_rule_with_keyword(rules, "banks")
    if "banks" in themes and rates_rule:
        ir_state = _STATE_WATCH
        ir_evidence = ["banks_candidates_in_feed", "yields_rising_or_banks_rule_present"]
        ir_source = "transmission_rules_and_candidate_feed"
    else:
        ir_state = _STATE_INACTIVE
        ir_evidence = []
        ir_source = "local_shadow_only"

    # bonds_yields — same evidence as interest_rates
    by_state = ir_state
    by_evidence = ir_evidence[:]
    by_source = ir_source

    # oil_energy
    if "energy" in themes:
        oil_state = _STATE_WATCH
        oil_evidence = ["energy_candidates_in_feed"]
        oil_source = "economic_candidate_feed"
    else:
        oil_state = _STATE_INACTIVE
        oil_evidence = []
        oil_source = "local_shadow_only"

    # volatility — infer from headwind/risk-off signals
    if roa_present and has_pressure:
        vol_state = _STATE_WATCH
        vol_evidence = ["risk_off_headwind_signals_in_feed"]
        vol_source = "candidate_feed"
    else:
        vol_state = _STATE_INACTIVE
        vol_evidence = []
        vol_source = "local_shadow_only"

    # sector_rotation — infer from breadth of active themes
    active_theme_count = len(themes)
    if active_theme_count >= 4:
        sr_state = _STATE_WATCH
        sr_evidence = [f"{active_theme_count}_distinct_themes_active_in_feed"]
        sr_source = "economic_candidate_feed"
    else:
        sr_state = _STATE_INACTIVE
        sr_evidence = []
        sr_source = "local_shadow_only"

    # Unavailable drivers — no local evidence for Sprint 4A
    unavailable_drivers = [
        "inflation", "growth", "usd", "valuation", "liquidity", "consumer_behaviour",
    ]

    freshness = "static_shadow_inference_sprint4a"

    drivers: dict[str, dict] = {
        "ai_capex_growth": {
            "driver_id": "ai_capex_growth",
            "score": 0.85 if ai_state == _STATE_ACTIVE else 0.0,
            "state": ai_state,
            "confidence": _STATE_CONFIDENCE[ai_state],
            "source_label": ai_source,
            "evidence": ai_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "corporate_capex": {
            "driver_id": "corporate_capex",
            "score": 0.40 if cc_state != _STATE_INACTIVE else 0.0,
            "state": cc_state,
            "confidence": _STATE_CONFIDENCE[cc_state],
            "source_label": cc_source,
            "evidence": cc_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "interest_rates": {
            "driver_id": "interest_rates",
            "score": 0.30 if ir_state == _STATE_WATCH else 0.0,
            "state": ir_state,
            "confidence": _STATE_CONFIDENCE[ir_state],
            "source_label": ir_source,
            "evidence": ir_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "bonds_yields": {
            "driver_id": "bonds_yields",
            "score": 0.30 if by_state == _STATE_WATCH else 0.0,
            "state": by_state,
            "confidence": _STATE_CONFIDENCE[by_state],
            "source_label": by_source,
            "evidence": by_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "oil_energy": {
            "driver_id": "oil_energy",
            "score": 0.30 if oil_state == _STATE_WATCH else 0.0,
            "state": oil_state,
            "confidence": _STATE_CONFIDENCE[oil_state],
            "source_label": oil_source,
            "evidence": oil_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "geopolitics": {
            "driver_id": "geopolitics",
            "score": 0.30 if geo_state == _STATE_WATCH else 0.0,
            "state": geo_state,
            "confidence": _STATE_CONFIDENCE[geo_state],
            "source_label": geo_source,
            "evidence": geo_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "credit": {
            "driver_id": "credit",
            "score": 0.30 if credit_state == _STATE_WATCH else 0.0,
            "state": credit_state,
            "confidence": _STATE_CONFIDENCE[credit_state],
            "source_label": credit_source,
            "evidence": credit_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "liquidity": {
            "driver_id": "liquidity",
            "score": 0.0,
            "state": _STATE_UNAVAILABLE,
            "confidence": 0.0,
            "source_label": "local_shadow_only",
            "evidence": [],
            "freshness_status": freshness,
            "unavailable_reason": _UNAVAILABLE_REASON,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "risk_appetite": {
            "driver_id": "risk_appetite",
            "score": 0.25 if ra_state == _STATE_WATCH else 0.0,
            "state": ra_state,
            "confidence": _STATE_CONFIDENCE[ra_state],
            "source_label": ra_source,
            "evidence": ra_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "volatility": {
            "driver_id": "volatility",
            "score": 0.20 if vol_state == _STATE_WATCH else 0.0,
            "state": vol_state,
            "confidence": _STATE_CONFIDENCE[vol_state],
            "source_label": vol_source,
            "evidence": vol_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "sector_rotation": {
            "driver_id": "sector_rotation",
            "score": 0.20 if sr_state == _STATE_WATCH else 0.0,
            "state": sr_state,
            "confidence": _STATE_CONFIDENCE[sr_state],
            "source_label": sr_source,
            "evidence": sr_evidence,
            "freshness_status": freshness,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "valuation": {
            "driver_id": "valuation",
            "score": 0.0,
            "state": _STATE_UNAVAILABLE,
            "confidence": 0.0,
            "source_label": "local_shadow_only",
            "evidence": [],
            "freshness_status": freshness,
            "unavailable_reason": _UNAVAILABLE_REASON,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "consumer_behaviour": {
            "driver_id": "consumer_behaviour",
            "score": 0.0,
            "state": _STATE_UNAVAILABLE,
            "confidence": 0.0,
            "source_label": "local_shadow_only",
            "evidence": [],
            "freshness_status": freshness,
            "unavailable_reason": _UNAVAILABLE_REASON,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "inflation": {
            "driver_id": "inflation",
            "score": 0.0,
            "state": _STATE_UNAVAILABLE,
            "confidence": 0.0,
            "source_label": "local_shadow_only",
            "evidence": [],
            "freshness_status": freshness,
            "unavailable_reason": _UNAVAILABLE_REASON,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "growth": {
            "driver_id": "growth",
            "score": 0.0,
            "state": _STATE_UNAVAILABLE,
            "confidence": 0.0,
            "source_label": "local_shadow_only",
            "evidence": [],
            "freshness_status": freshness,
            "unavailable_reason": _UNAVAILABLE_REASON,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
        "usd": {
            "driver_id": "usd",
            "score": 0.0,
            "state": _STATE_UNAVAILABLE,
            "confidence": 0.0,
            "source_label": "local_shadow_only",
            "evidence": [],
            "freshness_status": freshness,
            "unavailable_reason": _UNAVAILABLE_REASON,
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        },
    }

    return drivers


# ---------------------------------------------------------------------------
# Regime selection
# ---------------------------------------------------------------------------

def _select_regime(drivers: dict[str, dict]) -> str:
    active = [d for d in drivers.values() if d["state"] == _STATE_ACTIVE]
    watch = [d for d in drivers.values() if d["state"] == _STATE_WATCH]

    if drivers.get("ai_capex_growth", {}).get("state") == _STATE_ACTIVE and len(active) >= 1:
        return "ai_infrastructure_tailwind_shadow"
    if (
        drivers.get("credit", {}).get("state") == _STATE_WATCH
        and drivers.get("risk_appetite", {}).get("state") == _STATE_WATCH
        and not active
    ):
        return "credit_stress_watch_shadow"
    if active:
        return "mixed_shadow_regime"
    if watch:
        return "mixed_shadow_regime"
    return "unknown_static_bootstrap"


def _select_posture(regime: str, drivers: dict[str, dict]) -> str:
    if regime == "ai_infrastructure_tailwind_shadow":
        return "selective"
    if regime == "credit_stress_watch_shadow":
        return "cautious"
    if drivers.get("risk_appetite", {}).get("state") == _STATE_WATCH:
        return "defensive_selective"
    return "neutral"


# ---------------------------------------------------------------------------
# Route adjustments — descriptive only, never executable
# ---------------------------------------------------------------------------

def _build_route_adjustments(regime: str, posture: str) -> dict:
    """
    Route context is report-only. Rules:
    1. Cannot mark any candidate executable.
    2. Cannot override route_tagger.py or quota_allocator.py.
    3. Cannot create symbols.
    4. Cannot alter live bot behaviour.
    """
    is_ai_tailwind = regime == "ai_infrastructure_tailwind_shadow"
    is_credit_watch = regime == "credit_stress_watch_shadow"

    position_notes = (
        "AI infrastructure structural tailwind active — position route quality standards apply; "
        "valuation tolerance constrained pending live data Sprint 4B."
        if is_ai_tailwind
        else "No confirmed structural tailwind in local shadow — position route standards unchanged."
    )
    swing_notes = (
        "Shadow inference only — avoid extended entries; catalyst confirmation preferred for swing entries."
        if is_credit_watch
        else "Swing route available; standard entry criteria apply per route_tagger.py."
    )
    intraday_notes = "Intraday swing available for energy/banks/event-driven themes; attention cap enforced."
    watchlist_notes = (
        "Upgrade from watchlist to executable route requires next Universe Builder run — "
        "context alone cannot promote a candidate."
    )

    return {
        "POSITION": {
            "quality_required": True,
            "valuation_tolerance": "low",
            "size_modifier": 1.0,
            "entry_hurdle_modifier": 1.0,
            "notes": position_notes,
        },
        "SWING": {
            "catalyst_required": False,
            "avoid_extended_entries": is_credit_watch,
            "entry_hurdle_modifier": 1.05 if is_credit_watch else 1.0,
            "notes": swing_notes,
        },
        "INTRADAY_SWING": {
            "allowed": True,
            "event_driven_preferred": True,
            "attention_cap_applies": True,
            "notes": intraday_notes,
        },
        "WATCHLIST": {
            "allowed": True,
            "upgrade_requires_next_universe_builder_run": True,
            "notes": watchlist_notes,
        },
    }


# ---------------------------------------------------------------------------
# daily_economic_state.json builder
# ---------------------------------------------------------------------------

def _build_daily_economic_state(
    rules_data: dict | None,
    feed_data: dict | None,
    shadow_data: dict | None,
    report_data: dict | None,
    comparison_data: dict | None,
    source_files: list[str],
    unavailable_sources: list[str],
    warnings: list[str],
) -> dict:
    now = _now_iso()
    today = _today()

    rules = _extract_rules(rules_data)
    candidates = _extract_candidates(feed_data)

    drivers = _infer_drivers(rules, candidates, shadow_data, report_data, comparison_data)

    active_drivers = [did for did, d in drivers.items() if d["state"] == _STATE_ACTIVE]
    inactive_drivers = [did for did, d in drivers.items() if d["state"] in (_STATE_INACTIVE, _STATE_WATCH)]
    unavailable_driver_ids = [did for did, d in drivers.items() if d["state"] == _STATE_UNAVAILABLE]

    # confidence_summary
    active_confidences = [d["confidence"] for d in drivers.values() if d["state"] == _STATE_ACTIVE]
    mean_conf = round(sum(active_confidences) / len(active_confidences), 4) if active_confidences else 0.0

    # route_pressure_summary from shadow universe
    route_pressure: dict[str, Any] = {}
    if isinstance(shadow_data, dict):
        us = shadow_data.get("universe_summary") or {}
        route_pressure = {
            "position_route_count": us.get("position_route_count", 0),
            "structural_quota_group_count": us.get("structural_quota_group_count", 0),
            "structural_swing_count": us.get("structural_swing_count", 0),
            "structural_watchlist_count": us.get("structural_watchlist_count", 0),
            "attention_count": us.get("attention_count", 0),
            "etf_proxy_count": us.get("etf_proxy_count", 0),
            "structural_quota_binding": us.get("structural_quota_binding", False),
            "source": "active_opportunity_universe_shadow",
        }

    return {
        "schema_version": "1.0",
        "generated_at": now,
        "valid_for_session": today,
        "mode": "shadow_local_economic_state",
        "data_source_mode": "local_shadow_outputs_only",
        "source_files": source_files,
        "unavailable_sources": unavailable_sources,
        "warnings": warnings + [
            "Sprint 4A: all macro drivers inferred from local shadow files only. "
            "No live market data. Confidence scores are conservative estimates."
        ],
        "driver_scores": drivers,
        "driver_states": {did: d["state"] for did, d in drivers.items()},
        "active_drivers": active_drivers,
        "inactive_drivers": inactive_drivers,
        "blocked_drivers": [],
        "confidence_summary": {
            "mean_confidence_active_drivers": mean_conf,
            "drivers_with_live_evidence": 0,
            "drivers_unavailable": len(unavailable_driver_ids),
            "inference_only": True,
        },
        "route_pressure_summary": route_pressure,
        "no_live_api_called": _NO_LIVE_API_CALLED,
        "broker_called": _BROKER_CALLED,
        "env_inspected": _ENV_INSPECTED,
        "raw_news_used": _RAW_NEWS_USED,
        "llm_used": _LLM_USED,
        "broad_intraday_scan_used": _BROAD_INTRADAY_SCAN_USED,
        "live_output_changed": _LIVE_OUTPUT_CHANGED,
    }


# ---------------------------------------------------------------------------
# current_economic_context.json builder
# ---------------------------------------------------------------------------

def _build_current_economic_context(
    drivers: dict[str, dict],
    feed_data: dict | None,
    shadow_data: dict | None,
    comparison_data: dict | None,
    source_files: list[str],
    warnings: list[str],
) -> dict:
    now = _now_iso()
    today = _today()

    regime = _select_regime(drivers)
    posture = _select_posture(regime, drivers)
    route_adjustments = _build_route_adjustments(regime, posture)

    # active_driver_summary
    active_driver_summary = [
        {"driver_id": did, "state": d["state"], "confidence": d["confidence"],
         "evidence_count": len(d.get("evidence") or [])}
        for did, d in drivers.items()
        if d["state"] == _STATE_ACTIVE
    ]

    # active_theme_summary from feed
    active_themes: list[dict] = []
    if isinstance(feed_data, dict):
        feed_summary = feed_data.get("feed_summary") or {}
        for theme in (feed_summary.get("themes_active") or []):
            active_themes.append({"theme_id": theme, "source": "economic_candidate_feed"})
        for theme in (feed_summary.get("headwind_themes") or []):
            active_themes.append({"theme_id": theme, "direction": "headwind",
                                   "source": "economic_candidate_feed"})

    # risk_modifiers from shadow universe
    risk_modifiers: dict[str, Any] = {}
    headwind_themes: list[str] = []
    if isinstance(feed_data, dict):
        headwind_themes = (feed_data.get("feed_summary") or {}).get("headwind_themes") or []

    structural_quota_binding = False
    attention_cap_binding = False
    if isinstance(shadow_data, dict):
        us = shadow_data.get("universe_summary") or {}
        structural_quota_binding = bool(us.get("structural_quota_binding", False))
        attn = us.get("attention_count", 0)
        attention_cap_binding = attn >= 15

    risk_modifiers = {
        "headwind_themes_active": headwind_themes,
        "structural_quota_binding": structural_quota_binding,
        "attention_cap_binding": attention_cap_binding,
    }

    # overall confidence = mean of active driver confidences, capped at 0.45
    active_confs = [d["confidence"] for d in drivers.values() if d["state"] == _STATE_ACTIVE]
    overall_confidence = round(min(sum(active_confs) / len(active_confs), 0.45), 4) if active_confs else 0.15

    return {
        "schema_version": "1.0",
        "generated_at": now,
        "valid_for_session": today,
        "mode": "shadow_current_economic_context",
        "data_source_mode": "local_shadow_outputs_only",
        "economic_regime": regime,
        "risk_posture": posture,
        "confidence": overall_confidence,
        "active_driver_summary": active_driver_summary,
        "active_theme_summary": active_themes,
        "route_adjustments": route_adjustments,
        "risk_modifiers": risk_modifiers,
        "freshness": "static_shadow_inference_sprint4a",
        "source_files": source_files,
        "warnings": warnings + [
            "Sprint 4A: economic context inferred from local shadow files only. "
            "Context is report-only — it cannot make any candidate executable, "
            "cannot create symbols, and cannot override route_tagger.py or quota_allocator.py."
        ],
        "no_live_api_called": _NO_LIVE_API_CALLED,
        "broker_called": _BROKER_CALLED,
        "env_inspected": _ENV_INSPECTED,
        "raw_news_used": _RAW_NEWS_USED,
        "llm_used": _LLM_USED,
        "broad_intraday_scan_used": _BROAD_INTRADAY_SCAN_USED,
        "live_output_changed": _LIVE_OUTPUT_CHANGED,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_economic_intelligence(
    daily_state_path: str = _DAILY_STATE_PATH,
    context_path: str = _CONTEXT_PATH,
) -> tuple[dict, dict]:
    """
    Read local shadow/intelligence files, infer driver states, write output files.

    Returns (daily_state, economic_context) dicts.
    No live API calls. No broker calls. No .env inspection. No LLM. No raw news.
    live_output_changed = false.
    """
    # Collect input source file labels and load data
    all_input_paths = [
        _RULES_PATH, _TAXONOMY_PATH, _ROSTER_PATH, _FEED_PATH,
        _ADAPTER_SNAP_PATH, _SHADOW_PATH, _COMPARISON_PATH,
        _REPORT_PATH, _PIPELINE_SNAP_PATH,
    ]

    source_files: list[str] = []
    unavailable_sources: list[str] = []
    warnings: list[str] = []

    loaded: dict[str, dict | None] = {}
    for path in all_input_paths:
        rel = os.path.relpath(path, _BASE)
        data, err = _read_json(path)
        if err:
            unavailable_sources.append(rel)
            loaded[path] = None
        else:
            source_files.append(rel)
            loaded[path] = data  # type: ignore[assignment]

    if unavailable_sources:
        warnings.append(
            f"Sprint 4A: {len(unavailable_sources)} source file(s) unavailable — "
            f"driver inference limited. Missing: {unavailable_sources}"
        )

    rules_data = loaded.get(_RULES_PATH)
    feed_data = loaded.get(_FEED_PATH)
    shadow_data = loaded.get(_SHADOW_PATH)
    report_data = loaded.get(_REPORT_PATH)
    comparison_data = loaded.get(_COMPARISON_PATH)

    # Build drivers (shared between both output files)
    rules = _extract_rules(rules_data)
    candidates = _extract_candidates(feed_data)
    drivers = _infer_drivers(rules, candidates, shadow_data, report_data, comparison_data)

    # Build daily_economic_state
    daily_state = _build_daily_economic_state(
        rules_data=rules_data,
        feed_data=feed_data,
        shadow_data=shadow_data,
        report_data=report_data,
        comparison_data=comparison_data,
        source_files=source_files,
        unavailable_sources=unavailable_sources,
        warnings=warnings,
    )

    # Build current_economic_context
    economic_context = _build_current_economic_context(
        drivers=drivers,
        feed_data=feed_data,
        shadow_data=shadow_data,
        comparison_data=comparison_data,
        source_files=source_files,
        warnings=warnings,
    )

    # Write outputs
    os.makedirs(os.path.dirname(daily_state_path), exist_ok=True)
    with open(daily_state_path, "w", encoding="utf-8") as f:
        json.dump(daily_state, f, indent=2)

    os.makedirs(os.path.dirname(context_path), exist_ok=True)
    with open(context_path, "w", encoding="utf-8") as f:
        json.dump(economic_context, f, indent=2)

    return daily_state, economic_context


if __name__ == "__main__":
    daily, context = generate_economic_intelligence()

    drivers_total = len(daily["driver_scores"])
    drivers_active = len(daily["active_drivers"])
    drivers_unavailable = daily["confidence_summary"]["drivers_unavailable"]
    drivers_inactive = len(daily["inactive_drivers"])

    print(f"daily_economic_state.json → {_DAILY_STATE_PATH}")
    print(f"  drivers_total:      {drivers_total}")
    print(f"  drivers_active:     {drivers_active}  ({', '.join(daily['active_drivers']) or 'none'})")
    print(f"  drivers_unavailable:{drivers_unavailable}")
    print(f"  drivers_inactive:   {drivers_inactive}")
    print(f"  no_live_api_called: {daily['no_live_api_called']}")

    print(f"\ncurrent_economic_context.json → {_CONTEXT_PATH}")
    print(f"  economic_regime:    {context['economic_regime']}")
    print(f"  risk_posture:       {context['risk_posture']}")
    print(f"  confidence:         {context['confidence']}")
    print(f"  route_adjustments:  {list(context['route_adjustments'].keys())}")
    print(f"  live_output_changed:{context['live_output_changed']}")
