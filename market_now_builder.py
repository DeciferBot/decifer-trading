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
  data/intelligence/customer_event_tape.json   — fresh customer-safe events
                                                  (Sprint M11A — reconciled
                                                   with price drivers below)

Produces a SaaSIntelligencePayload with no raw prices, no broker state,
no execution signals, and no internal scores.

Sprint M11A — Market Map sections (market_mood, what_changed, key_events,
sectors, themes, radar, watch_next, known_conflicts, section_freshness,
source_notes) are produced by market_now_reconciler.reconcile_market_map().

This module must NOT import from any execution module.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from market_now_reconciler import (
    get_event_tape_freshness,
    reconcile_market_map,
)
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
    "geopolitical_risk_falling":  "Geopolitical risk easing (peace pricing)",
    "credit_stress_easing":       "Credit conditions easing",
    "risk_on_rotation":           "Risk-on rotation underway",
    "gold_safe_haven_bid":        "Safe-haven demand for gold elevated",
    "small_cap_risk_on":          "Small-cap stocks outperforming large-caps",
    "smh_tactical_weakness":      "Semiconductor sector under near-term pressure",
}

_REGIME_LABELS: dict[str, str] = {
    "TRENDING_UP":    "Risk-on — equities trending higher",
    "TRENDING_DOWN":  "Risk-off — equities declining",
    "BEAR_TRENDING":  "Risk-off — bear market in progress",
    "RELIEF_RALLY":   "Risk-on — bounce underway",
    "CHOPPY":         "Neutral — choppy, no clear direction",
    "PANIC":          "Risk-off — extreme volatility",
    "RANGE_BOUND":    "Neutral — markets consolidating",
    "CAPITULATION":   "Risk-off — extreme fear, capitulation",
    "UNKNOWN":        "Assessing market conditions",
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


def _load_active_themes() -> tuple[list[str], list[dict[str, str]], dict[str, str]]:
    """Returns (active_theme_ids, opportunity_explanations, theme_states_by_id)."""
    try:
        ta = _read_json("data/intelligence/theme_activation.json")
        active_ids: list[str] = []
        explanations: list[dict[str, str]] = []
        states: dict[str, str] = {}
        for t in ta.get("themes", []):
            state = t.get("state", "dormant")
            tid = t.get("theme_id", "")
            if tid:
                states[tid] = state
            if state not in ("activated", "strengthening"):
                continue
            active_ids.append(tid)
            reason_raw = (t.get("reason") or "").split(",")[0].strip()
            explanations.append({
                "theme": _theme_name(tid),
                "explanation": reason_raw or f"{_theme_name(tid)} is active.",
            })
        return active_ids, explanations, states
    except Exception as exc:
        log.debug("_load_active_themes: %s", exc)
        return [], [], {}


_UNIVERSE_BUCKET_SUFFIXES = (
    "_direct_beneficiary", "_second_order_beneficiary", "_etf_proxy",
    "_headwind_candidate", "_pressure_candidate",
)


def _bucket_to_theme(bucket_id: str) -> str:
    """Strip bucket suffix to recover the theme ID."""
    for s in _UNIVERSE_BUCKET_SUFFIXES:
        if bucket_id.endswith(s):
            return bucket_id[: -len(s)]
    return bucket_id


def _load_universe_snapshot() -> list[dict]:
    """Load and project customer-safe items from active opportunity universe.

    Projects only: symbol, company_name, theme_id, why_connected, transmission.
    Strips all execution, order, risk, and broker fields before returning.
    Returns [] if the file is absent or unreadable (fail-closed).
    """
    try:
        raw = _read_json("data/live/active_opportunity_universe.json")
        items: list[dict] = []
        for c in raw.get("candidates", []):
            if c.get("route") == "manual_conviction":
                continue
            why = c.get("why_this_symbol", "")
            if not why:
                continue
            theme_id = _bucket_to_theme(c.get("bucket_id", ""))
            # Skip entries whose bucket_id did not map to a clean theme ID
            if not theme_id or theme_id.startswith("tier_"):
                continue
            items.append({
                "symbol": c.get("symbol", ""),
                "company_name": c.get("company_name"),
                "theme_id": theme_id,
                "why_connected": why,
                "transmission": c.get("transmission_direction", "tailwind"),
            })
        return items[:50]
    except Exception as exc:
        log.debug("_load_universe_snapshot: %s", exc)
        return []


def _load_blocked_conditions() -> list[str]:
    """Returns the active blocked_conditions list from live_driver_state."""
    try:
        ld = _read_json("data/intelligence/live_driver_state.json")
        return list(ld.get("blocked_conditions", []))
    except Exception:
        return []


def _load_active_driver_keys() -> list[str]:
    """Returns the raw active_driver keys (not labels) for reconciler use."""
    try:
        ld = _read_json("data/intelligence/live_driver_state.json")
        return list(ld.get("active_drivers", []))
    except Exception:
        return []


def _load_manifest_regime() -> tuple[str, str, str]:
    """Returns (regime_label, freshness_iso, confidence_label)."""
    try:
        manifest = _read_json("data/live/current_manifest.json")
        regime_raw = manifest.get("market_regime") or "UNKNOWN"
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

    Sprint M11A: reconciles the customer Event Tape with price drivers to
    publish Market Map sections (market_mood, what_changed, key_events,
    sectors, themes, radar, watch_next, known_conflicts, section_freshness,
    source_notes). If price drivers and events conflict, the conflict is
    published in `known_conflicts` rather than hidden.

    If key artefacts are missing or stale, returns a degraded payload with
    plain-language messaging ("market intelligence temporarily limited") and
    a fresh timestamp so the customer output remains valid and honest. The
    Event Tape, if fresh independently, will still be surfaced — see
    section_freshness for per-section status.

    Raises SaaSPayloadValidationError if the assembled payload violates
    the customer-safe field allowlist (defensive check — should not happen
    unless saas_intelligence_output.py was modified incorrectly).

    Never calls any broker, never reads execution state, never performs
    live market data requests.
    """
    degraded, degraded_warnings = _is_degraded()

    # Always load whatever we can — even when degraded, fresh events may exist.
    drivers, risk_notes_from_blocked = _load_drivers()
    active_driver_keys = _load_active_driver_keys()
    blocked_conditions = _load_blocked_conditions()
    active_theme_ids, opportunity_explanations, theme_states = _load_active_themes()
    regime_label, manifest_published_at, confidence = _load_manifest_regime()
    apex_read = _load_apex_market_read()
    universe_snapshot = _load_universe_snapshot()

    # Reconcile price + event evidence into Market Map sections
    try:
        sections = reconcile_market_map(
            active_drivers=active_driver_keys,
            blocked_conditions=blocked_conditions,
            active_theme_ids=active_theme_ids,
            theme_states=theme_states,
            regime_label=regime_label,
            apex_read=apex_read,
            manifest_published_at=manifest_published_at,
            confidence_label=confidence,
        )
    except Exception as exc:
        log.warning("reconcile_market_map failed (%s) — serving without M11A sections.", exc)
        sections = {
            "market_mood": regime_label or "Assessing market conditions",
            "what_changed": [],
            "key_events": [],
            "sectors": [],
            "themes": [],
            "radar": [],
            "watch_next": [],
            "known_conflicts": [],
            "section_freshness": {},
            "source_notes": [],
        }

    if degraded:
        log.info(
            "build_market_now: serving degraded payload — %s",
            "; ".join(degraded_warnings),
        )
        # If the Event Tape is independently fresh, note that the price view
        # is stale while events remain live ("fresh event detected, price
        # confirmation pending").
        tape_state = get_event_tape_freshness()
        if tape_state.get("status") == "fresh" and sections.get("key_events"):
            summary = (
                "Latest market view is stale, but fresh event headlines are "
                "available. Scenario-style explanation is possible — do not "
                "treat as a live price confirmation."
            )
            confidence_label = "Degraded (events fresh, price view stale)"
        else:
            summary = (
                "Market intelligence is temporarily limited. "
                "Analysis will resume when fresh data becomes available."
            )
            confidence_label = "Insufficient data"

        risk_notes = list(risk_notes_from_blocked)
        risk_notes.extend(_to_customer_risk_notes(degraded_warnings))

        payload = SaaSIntelligencePayload(
            market_regime_label=regime_label or "Assessing market conditions",
            plain_english_summary=summary,
            key_drivers=drivers,
            active_themes=active_theme_ids,
            opportunity_explanations=opportunity_explanations,
            risk_notes=risk_notes,
            what_to_watch=sections.get("watch_next") or [
                "Check back shortly for updated market analysis.",
            ],
            freshness_timestamp=datetime.now(UTC).isoformat(),
            confidence_label=confidence_label,
            source_category_labels=_build_source_labels(drivers, bool(apex_read)),
            data_entitlement_note=(
                "Market intelligence powered by Decifer. "
                "Not financial advice. For informational purposes only."
            ),
            # Sprint M11A — Market Map sections (degraded but honest)
            market_mood=sections.get("market_mood", ""),
            what_changed=sections.get("what_changed", []),
            key_events=sections.get("key_events", []),
            sectors=sections.get("sectors", []),
            themes=sections.get("themes", []),
            radar=sections.get("radar", []),
            watch_next=sections.get("watch_next", []),
            known_conflicts=sections.get("known_conflicts", []),
            section_freshness=sections.get("section_freshness", {}),
            source_notes=sections.get("source_notes", []),
            # Sprint M11C — customer-safe universe snapshot
            universe_snapshot=universe_snapshot,
        )
        validate_customer_payload(payload.to_dict())
        return payload

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

    what_to_watch = sections.get("watch_next") or _build_what_to_watch(drivers, active_theme_ids)
    source_labels = _build_source_labels(drivers, bool(apex_read))

    # freshness_timestamp = when THIS payload was built, not the source manifest.
    # Per-source freshness is exposed via section_freshness so customers see
    # both "payload built now" and "macro_drivers last updated 2.3h ago".
    payload = SaaSIntelligencePayload(
        market_regime_label=regime_label,
        plain_english_summary=summary,
        key_drivers=drivers,
        active_themes=active_theme_ids,
        opportunity_explanations=opportunity_explanations,
        risk_notes=risk_notes_from_blocked,
        what_to_watch=what_to_watch,
        freshness_timestamp=datetime.now(UTC).isoformat(),
        confidence_label=confidence,
        source_category_labels=source_labels,
        data_entitlement_note=(
            "Market intelligence powered by Decifer. "
            "Not financial advice. For informational purposes only."
        ),
        # Sprint M11A — Market Map sections
        market_mood=sections.get("market_mood", ""),
        what_changed=sections.get("what_changed", []),
        key_events=sections.get("key_events", []),
        sectors=sections.get("sectors", []),
        themes=sections.get("themes", []),
        radar=sections.get("radar", []),
        watch_next=sections.get("watch_next", []),
        known_conflicts=sections.get("known_conflicts", []),
        section_freshness=sections.get("section_freshness", {}),
        source_notes=sections.get("source_notes", []),
        # Sprint M11C — customer-safe universe snapshot
        universe_snapshot=universe_snapshot,
    )

    # Defensive: validate the assembled payload against the allowlist
    validate_customer_payload(payload.to_dict())

    return payload


def _to_customer_risk_notes(warnings: list[str]) -> list[str]:
    """Sanitise degraded warnings before exposing them to customers."""
    out: list[str] = []
    for w in warnings:
        # Warnings already use category labels (no file paths), but strip any
        # leaked file-name fragments defensively.
        cleaned = w.replace(".json", "").replace(".jsonl", "")
        out.append(cleaned)
    return out


def get_market_now_dict() -> dict:
    """Return the Market Now payload as a plain dict (JSON-serialisable)."""
    return build_market_now().to_dict()
