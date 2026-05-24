# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  market_now_builder.py                     ║
# ║   SaaS-safe Market Now payload builder                      ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
market_now_builder.py — Build a SaaS-safe "Market Now" intelligence snapshot.

Reads exclusively from the intelligence pipeline's persisted artefacts:
  data/intelligence/live_driver_state.json     — active macro drivers
  data/intelligence/theme_activation.json      — theme states
  data/live/current_manifest.json              — pipeline freshness / regime
  data/apex_conversation_log.jsonl             — last Apex market read (optional)

Produces a SaaSIntelligencePayload with no raw prices, no broker state,
no execution signals, and no internal scores.

This module must NOT import from any execution module.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from saas_intelligence_output import SaaSIntelligencePayload, validate_customer_payload

log = logging.getLogger("decifer.market_now")

_BASE = os.path.dirname(os.path.abspath(__file__))

# Maximum age (hours) before a key artefact is considered stale and triggers
# a degraded response. Matches saas_intelligence_output._FRESHNESS_WINDOW_HOURS.
_ARTIFACT_FRESHNESS_HOURS: float = 6.0

# Key artefacts whose freshness is checked before building the payload.
# Relative to _BASE. Do not expose these paths in customer-facing output.
_KEY_ARTIFACTS: tuple[str, ...] = (
    "data/live/current_manifest.json",
    "data/intelligence/live_driver_state.json",
)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_json(rel: str) -> Any:
    path = os.path.join(_BASE, rel)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_jsonl_tail(rel: str, n: int = 30) -> list[dict]:
    path = os.path.join(_BASE, rel)
    if not os.path.exists(path):
        return []
    lines: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                try:
                    lines.append(json.loads(raw))
                except Exception:
                    pass
    return lines[-n:]


# ---------------------------------------------------------------------------
# Driver label mapping (plain English for customers)
# ---------------------------------------------------------------------------

_DRIVER_LABELS: dict[str, str] = {
    "ai_capex_growth":            "AI capital spending cycle expanding",
    "ai_compute_demand":          "AI compute demand rising",
    "yields_rising":              "Bond yields rising — headwind for growth",
    "yields_falling":             "Bond yields falling — tailwind for growth",
    "oil_supply_shock":           "Oil supply shock active",
    "geopolitical_risk_rising":   "Geopolitical risk elevated",
    "credit_stress_easing":       "Credit conditions easing",
    "risk_on_rotation":           "Risk-on rotation underway",
    "gold_safe_haven_bid":        "Safe-haven demand for gold elevated",
    "small_cap_risk_on":          "Small-cap stocks outperforming large-caps",
    "smh_tactical_weakness":      "Semiconductor sector under near-term pressure",
}

_REGIME_LABELS: dict[str, str] = {
    "TRENDING_UP":   "Trending up",
    "TRENDING_DOWN": "Trending down",
    "BEAR_TRENDING": "Trending down",
    "CHOPPY":        "Choppy — no clear direction",
    "PANIC":         "Market in panic — extreme volatility",
    "RANGE_BOUND":   "Range-bound",
    "UNKNOWN":       "Assessing market conditions",
}


def _driver_label(key: str) -> str:
    return _DRIVER_LABELS.get(key, key.replace("_", " ").title())


def _regime_label(regime: str) -> str:
    return _REGIME_LABELS.get(regime, regime.replace("_", " ").title())


def _theme_name(theme_id: str) -> str:
    return theme_id.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Intelligence artefact loaders
# ---------------------------------------------------------------------------

def _load_drivers() -> tuple[list[str], list[str]]:
    """Returns (active_driver_labels, risk_notes_from_blocked_conditions)."""
    try:
        ld = _read_json("data/intelligence/live_driver_state.json")
        labels = [_driver_label(k) for k in ld.get("active_drivers", [])]
        risk_notes = [
            f"Market condition blocked: {c.replace('_', ' ')}"
            for c in ld.get("blocked_conditions", [])
        ]
        return labels, risk_notes
    except Exception as exc:
        log.debug("_load_drivers: %s", exc)
        return [], []


def _load_active_themes() -> tuple[list[str], list[dict[str, str]]]:
    """Returns (active_theme_ids, opportunity_explanations)."""
    try:
        ta = _read_json("data/intelligence/theme_activation.json")
        active_ids: list[str] = []
        explanations: list[dict[str, str]] = []
        for t in ta.get("themes", []):
            state = t.get("state", "dormant")
            if state not in ("activated", "strengthening"):
                continue
            tid = t.get("theme_id", "")
            active_ids.append(tid)
            reason_raw = (t.get("reason") or "").split(",")[0].strip()
            explanations.append({
                "theme": _theme_name(tid),
                "explanation": reason_raw or f"{_theme_name(tid)} is active.",
            })
        return active_ids, explanations
    except Exception as exc:
        log.debug("_load_active_themes: %s", exc)
        return [], []


def _load_manifest_regime() -> tuple[str, str, str]:
    """Returns (regime_label, freshness_iso, confidence_label)."""
    try:
        manifest = _read_json("data/live/current_manifest.json")
        regime_raw = manifest.get("market_regime", "UNKNOWN")
        regime_lbl = _regime_label(regime_raw)
        published = manifest.get("published_at", "")
        # Derive confidence from handoff_enabled state
        confidence = "High" if manifest.get("handoff_enabled") else "Low"
        return regime_lbl, published, confidence
    except Exception as exc:
        log.debug("_load_manifest_regime: %s", exc)
        return "Assessing market conditions", datetime.now(UTC).isoformat(), "Low"


def _load_apex_market_read() -> str:
    """Returns the most recent Apex plain-English market read, cleaned for customers."""
    _INTERNAL_TERMS = [
        "TRENDING_UP", "TRENDING_DOWN", "BEAR_TRENDING", "CHOPPY", "PANIC",
        "MOMENTUM_BULL", "FEAR_ELEVATED", "RISK_ON", "RISK_OFF", "RANGE_BOUND",
        "INTRADAY", "SWING", "POSITION", "SCALP", "AVOID",
    ]
    try:
        records = _read_jsonl_tail("data/apex_conversation_log.jsonl", n=30)
        for rec in reversed(records):
            text = rec.get("market_read", "")
            if not text:
                continue
            for term in _INTERNAL_TERMS:
                text = text.replace(term, term.replace("_", " ").lower().title())
            return text.strip()
    except Exception as exc:
        log.debug("_load_apex_market_read: %s", exc)
    return ""


def _build_what_to_watch(drivers: list[str], themes: list[str]) -> list[str]:
    """Derive 'what to watch' items from active context."""
    items: list[str] = []
    driver_keys_to_watch = {
        "AI capital spending cycle expanding": "AI infrastructure spend data and compute capacity announcements",
        "Bond yields rising — headwind for growth": "Upcoming Fed commentary and Treasury auction results",
        "Bond yields falling — tailwind for growth": "Duration-sensitive assets (tech, REITs) for opportunity",
        "Oil supply shock active":  "OPEC+ policy meetings and crude inventory reports",
        "Geopolitical risk elevated": "Defence and energy sector developments",
        "Credit conditions easing": "High-yield spreads for continued improvement",
        "Risk-on rotation underway": "Small-cap relative performance vs large-cap",
    }
    for driver in drivers:
        watch = driver_keys_to_watch.get(driver)
        if watch:
            items.append(watch)
    # Generic items based on theme count
    if len(themes) >= 3:
        items.append("Sector rotation signals as multiple themes are simultaneously active")
    if not items:
        items.append("Macro calendar for upcoming economic data releases")
    return items[:5]  # cap at 5


def _build_source_labels(drivers: list[str], apex_present: bool) -> list[str]:
    labels = ["market_data", "macro_drivers"]
    if drivers:
        labels.append("thematic_intelligence")
    if apex_present:
        labels.append("ai_synthesis")
    return labels


# ---------------------------------------------------------------------------
# Artefact freshness check
# ---------------------------------------------------------------------------

def _is_degraded() -> tuple[bool, list[str]]:
    """
    Check whether key pipeline artefacts are missing or older than
    _ARTIFACT_FRESHNESS_HOURS.

    Returns (is_degraded, list_of_plain_english_warnings).
    Warnings use category labels only — never internal file paths.
    """
    _LABELS = {
        "data/live/current_manifest.json":            "Market pipeline manifest",
        "data/intelligence/live_driver_state.json":   "Market drivers data",
    }
    now = time.time()
    warnings: list[str] = []
    for rel in _KEY_ARTIFACTS:
        label = _LABELS.get(rel, rel.split("/")[-1])
        path = os.path.join(_BASE, rel)
        if not os.path.exists(path):
            warnings.append(f"{label} not available")
            continue
        try:
            age_h = (now - os.path.getmtime(path)) / 3600
            if age_h > _ARTIFACT_FRESHNESS_HOURS:
                warnings.append(f"{label} is stale ({age_h:.1f}h old)")
        except Exception as exc:
            warnings.append(f"{label} unreadable")
            log.debug("_is_degraded: %s — %s", rel, exc)
    return bool(warnings), warnings


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_market_now() -> SaaSIntelligencePayload:
    """
    Build and return a validated SaaSIntelligencePayload from persisted
    intelligence artefacts.

    If key artefacts are missing or stale, returns a degraded payload with
    plain-language messaging ("market intelligence temporarily limited") and
    a fresh timestamp so the customer output remains valid and honest.

    Raises SaaSPayloadValidationError if the assembled payload violates
    the customer-safe field allowlist (defensive check — should not happen
    unless saas_intelligence_output.py was modified incorrectly).

    Never calls any broker, never reads execution state, never performs
    live market data requests.
    """
    degraded, degraded_warnings = _is_degraded()
    if degraded:
        log.info("build_market_now: serving degraded payload — %s", "; ".join(degraded_warnings))
        payload = SaaSIntelligencePayload(
            market_regime_label="Assessing market conditions",
            plain_english_summary=(
                "Market intelligence is temporarily limited. "
                "Analysis will resume when fresh data becomes available."
            ),
            key_drivers=[],
            active_themes=[],
            opportunity_explanations=[],
            risk_notes=[],
            what_to_watch=["Check back shortly for updated market analysis."],
            freshness_timestamp=datetime.now(UTC).isoformat(),
            confidence_label="Insufficient data",
            source_category_labels=["market_data"],
            data_entitlement_note=(
                "Market intelligence powered by Decifer. "
                "Not financial advice. For informational purposes only."
            ),
        )
        validate_customer_payload(payload.to_dict())
        return payload

    drivers, risk_notes_from_blocked = _load_drivers()
    active_theme_ids, opportunity_explanations = _load_active_themes()
    regime_label, freshness, confidence = _load_manifest_regime()
    apex_read = _load_apex_market_read()

    # Plain-English summary: prefer Apex synthesis; fall back to driver summary
    if apex_read:
        summary = apex_read
    elif drivers:
        driver_str = ", ".join(drivers[:3])
        summary = (
            f"The market is currently {regime_label.lower()}. "
            f"Key drivers: {driver_str}. "
            f"{len(active_theme_ids)} investment theme(s) active."
        )
    else:
        summary = (
            f"The market is currently {regime_label.lower()}. "
            "Intelligence pipeline is gathering data."
        )

    what_to_watch = _build_what_to_watch(drivers, active_theme_ids)
    source_labels = _build_source_labels(drivers, bool(apex_read))

    payload = SaaSIntelligencePayload(
        market_regime_label=regime_label,
        plain_english_summary=summary,
        key_drivers=drivers,
        active_themes=active_theme_ids,
        opportunity_explanations=opportunity_explanations,
        risk_notes=risk_notes_from_blocked,
        what_to_watch=what_to_watch,
        freshness_timestamp=freshness or datetime.now(UTC).isoformat(),
        confidence_label=confidence,
        source_category_labels=source_labels,
        data_entitlement_note=(
            "Market intelligence powered by Decifer. "
            "Not financial advice. For informational purposes only."
        ),
    )

    # Defensive: validate the assembled payload against the allowlist
    validate_customer_payload(payload.to_dict())

    return payload


def get_market_now_dict() -> dict:
    """Return the Market Now payload as a plain dict (JSON-serialisable)."""
    return build_market_now().to_dict()
