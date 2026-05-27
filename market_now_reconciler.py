# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  market_now_reconciler.py                  ║
# ║   Helper for market_now_builder.py — reconciles price       ║
# ║   drivers with the customer Event Tape into Market Map      ║
# ║   sections.                                                 ║
# ║   Sprint M11A — Customer Event Tape                          ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
market_now_reconciler.py — reconcile price evidence + event evidence + themes.

Customer-only. Imported only by market_now_builder.py and tests.

Boundaries (enforced by scripts/verify_customer_event_tape_safety.py):
  - No imports from execution, order, broker, PM, universe, or handoff modules.
  - No second Market Map publisher (sole consumer is market_now_builder).
  - No customer-payload fields outside the Sprint M11A approved allowlist.

Output: a dict of Market Map sections, customer-safe:
  market_mood, what_changed, key_events, sectors, themes, radar,
  watch_next, known_conflicts, section_freshness, source_notes
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from customer_event_tape import compute_freshness_status, load_customer_event_tape

log = logging.getLogger("decifer.market_now.reconciler")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_RECENT_EVENT_WINDOW_H = 4.0     # what counts as "recent" for sections
_WHAT_CHANGED_WINDOW_H = 0.5     # what counts as "just happened" for what_changed
_MAX_KEY_EVENTS = 6
_MAX_RADAR_ITEMS = 8
_MAX_WHAT_CHANGED = 6
_MAX_KNOWN_CONFLICTS = 5

_BASE = os.path.dirname(os.path.abspath(__file__))
_TAPE_REL_PATH = "data/intelligence/customer_event_tape.json"

# Human-readable labels for raw driver IDs — keeps customer copy clean
_DRIVER_LABELS: dict[str, str] = {
    "ai_capex_growth":           "AI capital spending cycle expanding",
    "ai_compute_demand":         "AI compute demand rising",
    "yields_rising":             "Bond yields rising",
    "yields_falling":            "Bond yields falling",
    "oil_supply_shock":          "Oil supply shock",
    "geopolitical_risk_rising":  "Geopolitical risk elevated",
    "geopolitical_risk_falling": "Geopolitical risk easing",
    "credit_stress_easing":      "Credit conditions easing",
    "risk_on_rotation":          "Risk-on rotation underway",
    "gold_safe_haven_bid":       "Safe-haven demand for gold",
    "small_cap_risk_on":         "Small-cap stocks outperforming large-caps",
    "futures_risk_on":           "Futures signalling risk-on",
    "futures_risk_off":          "Futures signalling risk-off",
}

def _label_driver(key: str) -> str:
    return _DRIVER_LABELS.get(key, key.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Conflict matrix
# ---------------------------------------------------------------------------
#
# Each rule maps a (price_driver_key, event_type) pair to a plain-English
# explanation. The reconciler emits a known_conflict whenever an active
# driver coexists with a recent event whose direction contradicts it.

_CONFLICT_RULES: dict[tuple[str, str], str] = {
    ("geopolitical_risk_rising", "de_escalation"): (
        "Defence and energy still reflect recent geopolitical risk, but fresh "
        "de-escalation headlines suggest the risk premium may be fading."
    ),
    ("geopolitical_risk_rising", "oil_risk_premium_unwind"): (
        "Price-based geopolitical risk drivers are still active, but oil is "
        "falling on de-escalation or peace hopes — the risk premium may be unwinding."
    ),
    ("oil_supply_shock", "de_escalation"): (
        "Recent oil supply-shock pricing may unwind if peace or de-escalation holds."
    ),
    ("oil_supply_shock", "oil_risk_premium_unwind"): (
        "Oil supply-shock pricing is conflicting with fresh evidence that the oil "
        "risk premium is unwinding."
    ),
    ("yields_rising", "rate_cut"): (
        "Yields have been rising in price action, but the central bank just eased — "
        "watch whether the rate path resets lower."
    ),
    ("yields_rising", "rate_cut_with_hawkish_guidance"): (
        "Yields rising is consistent with the hawkish guidance, even though the "
        "headline policy move was a cut — direction is genuinely conflicted."
    ),
    ("yields_falling", "hot_inflation_print"): (
        "Yields have been falling in price action, but fresh hot inflation data "
        "challenges that — the rate path may reset higher."
    ),
    ("risk_on_rotation", "escalation"): (
        "Risk-on rotation is visible in price action, but fresh geopolitical "
        "escalation may interrupt it."
    ),
    ("risk_off_rotation", "de_escalation"): (
        "Risk-off rotation is visible in price action, but fresh de-escalation "
        "headlines may shift the tone."
    ),
    ("gold_safe_haven_bid", "de_escalation"): (
        "Gold has been bid for safe-haven reasons, but fresh de-escalation may "
        "reduce that demand."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reconcile_market_map(
    *,
    active_drivers: list[str],
    blocked_conditions: list[str],
    active_theme_ids: list[str],
    theme_states: dict[str, str],
    regime_label: str,
    apex_read: str,
    manifest_published_at: str,
    confidence_label: str,
) -> dict[str, Any]:
    """Build the Market Map sections from price + event + theme evidence.

    All arguments are passed by the caller (market_now_builder) — this function
    does not import or call anything from execution, broker, or PM modules.
    """
    tape = load_customer_event_tape()
    recent_events = _filter_recent(tape.get("events", []), _RECENT_EVENT_WINDOW_H)
    just_happened = _filter_recent(tape.get("events", []), _WHAT_CHANGED_WINDOW_H)

    key_events = [_to_customer_event_summary(e) for e in recent_events[:_MAX_KEY_EVENTS]]

    market_mood = _derive_market_mood(
        regime_label=regime_label,
        active_drivers=active_drivers,
        recent_events=recent_events,
    )

    what_changed = _build_what_changed(just_happened, active_drivers, regime_label)
    sectors = _build_sectors(active_drivers, recent_events)
    themes = _build_themes(active_theme_ids, theme_states, recent_events)
    radar = _build_radar(recent_events)
    watch_next = _build_watch_next(active_drivers, recent_events)
    known_conflicts = _detect_known_conflicts(active_drivers, recent_events)
    section_freshness = _build_section_freshness(
        manifest_published_at=manifest_published_at,
        tape=tape,
        recent_events=recent_events,
        active_drivers=active_drivers,
        active_theme_ids=active_theme_ids,
    )

    source_notes = [
        "Driver layer derived from market price evidence.",
        "Event tape derived from real-time news headlines.",
    ]
    if apex_read:
        source_notes.append("Plain-English market read synthesized by Apex.")
    if blocked_conditions:
        source_notes.append(
            "One or more market conditions are flagged as risk-elevated."
        )

    return {
        "market_mood": market_mood,
        "what_changed": what_changed,
        "key_events": key_events,
        "sectors": sectors,
        "themes": themes,
        "radar": radar,
        "watch_next": watch_next,
        "known_conflicts": known_conflicts,
        "section_freshness": section_freshness,
        "source_notes": source_notes,
    }


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _derive_market_mood(
    *,
    regime_label: str,
    active_drivers: list[str],
    recent_events: list[dict[str, Any]],
) -> str:
    """Return a short customer-friendly mood label."""
    # Event signal overrides regime label where strong.
    event_types = {e.get("event_type") for e in recent_events}

    if "escalation" in event_types or "oil_supply_shock" in event_types:
        return "Risk-off — fresh geopolitical or supply pressure"
    if "de_escalation" in event_types or "oil_risk_premium_unwind" in event_types:
        return "Risk-on — fresh de-escalation or risk-premium unwind"
    if "hot_inflation_print" in event_types:
        return "Risk-off — inflation surprise pressuring rates"
    if "rate_cut" in event_types and "rate_cut_with_hawkish_guidance" not in event_types:
        return "Risk-on — central bank easing"
    if "rate_cut_with_hawkish_guidance" in event_types:
        return "Mixed — easing headline, hawkish guidance"
    if "bank_or_credit_stress" in event_types:
        return "Risk-off — credit or banking stress"

    # Use regime label when it contains real signal
    _generic = {"assessing market conditions", "unknown", ""}
    if regime_label and regime_label.lower() not in _generic:
        return regime_label

    # Regime is unknown — derive mood from active driver set.
    # _RISK_OFF contains only signals that depress the BROAD market (rate/futures
    # shocks, risk-off rotation).  geopolitical_risk_rising and oil_supply_shock
    # are SECTOR catalysts (defence/energy bid) that coexist with bull markets;
    # including them here caused spurious "mixed" labels when VIX is <18 and
    # SPY is above its 200d MA.
    _RISK_ON = {"risk_on_rotation", "small_cap_risk_on", "futures_risk_on",
                "credit_stress_easing", "yields_falling", "gold_safe_haven_bid",
                "ai_capex_growth", "ai_compute_demand"}
    _RISK_OFF = {"yields_rising", "futures_risk_off"}
    driver_set = set(active_drivers)
    on_count = len(driver_set & _RISK_ON)
    off_count = len(driver_set & _RISK_OFF)
    if on_count > 0 and off_count == 0:
        return "Risk-on — broad market tailwinds active"
    if on_count > 0 and off_count > 0:
        return "Mixed — risk-on momentum with active headwinds"
    if off_count > 0 and on_count == 0:
        return "Risk-off — headwinds dominating"
    return "Assessing market conditions"


def _build_what_changed(
    just_happened: list[dict[str, Any]],
    active_drivers: list[str],
    regime_label: str,
) -> list[str]:
    """Return short customer-facing 'what changed' bullets."""
    items: list[str] = []
    for ev in just_happened[:_MAX_WHAT_CHANGED]:
        title = ev.get("title", "")
        family = ev.get("event_family", "")
        if title and family:
            items.append(f"[{family}] {title}")
    if not items:
        # Nothing fresh — surface driver state with human-readable labels
        if active_drivers:
            labels = [_label_driver(d) for d in active_drivers[:3]]
            items.append(
                "No fresh event headlines in the last 30 minutes — "
                f"market driven by: {', '.join(labels)}."
            )
        else:
            items.append(
                "No fresh event headlines in the last 30 minutes."
            )
    return items


def _build_sectors(
    active_drivers: list[str],
    recent_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate sector-level mood across drivers and events."""
    sector_state: dict[str, dict[str, Any]] = {}

    for ev in recent_events:
        for s in ev.get("sectors_positive", []):
            entry = sector_state.setdefault(
                s, {"name": s, "mood": "tailwind", "events": [], "reasons": []}
            )
            entry["events"].append(ev.get("title", ""))
            entry["reasons"].append(
                f"{ev.get('event_type', 'event')} → positive read-through"
            )
            entry["mood"] = "tailwind"
        for s in ev.get("sectors_negative", []):
            entry = sector_state.setdefault(
                s, {"name": s, "mood": "headwind", "events": [], "reasons": []}
            )
            entry["events"].append(ev.get("title", ""))
            entry["reasons"].append(
                f"{ev.get('event_type', 'event')} → negative read-through"
            )
            # If we already had a tailwind, the conflict becomes mixed
            if entry["mood"] == "tailwind":
                entry["mood"] = "mixed"
            elif entry["mood"] not in ("mixed",):
                entry["mood"] = "headwind"

    # Compact dict-list output. Drop internal fields, keep customer-safe shape.
    out: list[dict[str, Any]] = []
    for name, st in sector_state.items():
        out.append({
            "name": name,
            "mood": st["mood"],
            "reasons": _dedupe(st["reasons"])[:3],
            "from_events": _dedupe(st["events"])[:3],
        })
    return out


def _build_themes(
    active_theme_ids: list[str],
    theme_states: dict[str, str],
    recent_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine active theme state with event-driven strengthen/weaken signals."""
    out: list[dict[str, Any]] = []
    event_strengthen: set[str] = set()
    event_weaken: set[str] = set()
    why_strengthen: dict[str, list[str]] = {}
    why_weaken: dict[str, list[str]] = {}

    for ev in recent_events:
        for tid in ev.get("themes_strengthened", []):
            event_strengthen.add(tid)
            why_strengthen.setdefault(tid, []).append(ev.get("title", ""))
        for tid in ev.get("themes_weakened", []):
            event_weaken.add(tid)
            why_weaken.setdefault(tid, []).append(ev.get("title", ""))

    seen: set[str] = set()
    for tid in active_theme_ids:
        seen.add(tid)
        state = theme_states.get(tid, "active")
        entry: dict[str, Any] = {
            "theme": tid,
            "state": state,
        }
        if tid in event_strengthen:
            entry["event_signal"] = "strengthening"
            entry["from_events"] = _dedupe(why_strengthen.get(tid, []))[:2]
        elif tid in event_weaken:
            entry["event_signal"] = "weakening"
            entry["from_events"] = _dedupe(why_weaken.get(tid, []))[:2]
        out.append(entry)

    # Surface event-driven themes that aren't in the active list yet
    for tid in event_strengthen - seen:
        seen.add(tid)
        out.append({
            "theme": tid,
            "state": "watch",
            "event_signal": "strengthening",
            "from_events": _dedupe(why_strengthen.get(tid, []))[:2],
        })
    for tid in event_weaken - seen:
        seen.add(tid)
        out.append({
            "theme": tid,
            "state": "watch",
            "event_signal": "weakening",
            "from_events": _dedupe(why_weaken.get(tid, []))[:2],
        })

    # Include all remaining themes (dormant, crowded, headwind) for Theme Map completeness
    for tid, state in theme_states.items():
        if tid in seen:
            continue
        out.append({
            "theme": tid,
            "state": state,
            "from_events": [],
        })

    return out


def _build_radar(recent_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Surface symbols on the radar with reason, theme link, and signals.

    Strictly customer-intelligence — no buy/sell, no entry/exit, no stop/target,
    no position size, no trade recommendation, no execution readiness, no
    account exposure, no P&L.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ev in recent_events:
        for sym in ev.get("tickers_first_order", []):
            if not sym or sym in seen:
                continue
            seen.add(sym)
            theme_link = (ev.get("themes_strengthened") or
                          ev.get("themes_weakened") or [None])[0]
            confirmation = (ev.get("confirmation_signals") or [""])[0]
            invalidation = (ev.get("invalidation_signals") or [""])[0]
            out.append({
                "symbol": sym,
                "reason_to_watch": ev.get("title", "")[:200],
                "theme_link": theme_link,
                "confirmation_signal": confirmation,
                "invalidation_signal": invalidation,
            })
            if len(out) >= _MAX_RADAR_ITEMS:
                return out
    return out


def _build_watch_next(
    active_drivers: list[str],
    recent_events: list[dict[str, Any]],
) -> list[str]:
    """Return short customer-facing watch-next bullets."""
    items: list[str] = []
    for ev in recent_events[:3]:
        sigs = ev.get("confirmation_signals", []) + ev.get("invalidation_signals", [])
        for s in sigs[:2]:
            if s and s not in items:
                items.append(s)
    if not items:
        if active_drivers:
            items.append(
                "Follow-through on the current macro drivers and any fresh headlines."
            )
        else:
            items.append("Macro calendar for upcoming data releases.")
    return items[:6]


def _detect_known_conflicts(
    active_drivers: list[str],
    recent_events: list[dict[str, Any]],
) -> list[str]:
    """Return plain-English conflicts between price drivers and event evidence."""
    out: list[str] = []
    event_types_present = {e.get("event_type") for e in recent_events}
    for driver in active_drivers:
        for et in event_types_present:
            msg = _CONFLICT_RULES.get((driver, et))
            if msg and msg not in out:
                out.append(msg)

    # Surface event-flagged conflicts (e.g. earnings beat but stock falls)
    for ev in recent_events:
        for c in ev.get("known_conflicts", []):
            if c and c not in out:
                out.append(c)

    return out[:_MAX_KNOWN_CONFLICTS]


def _build_section_freshness(
    *,
    manifest_published_at: str,
    tape: dict[str, Any],
    recent_events: list[dict[str, Any]],
    active_drivers: list[str],
    active_theme_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return per-section freshness fields (no internal paths)."""
    now = datetime.now(UTC)

    def _freshness(processed_at: str, valid_until: str | None = None) -> dict[str, Any]:
        status = compute_freshness_status(processed_at, valid_until)
        age_h = None
        try:
            p = datetime.fromisoformat(processed_at)
            if p.tzinfo is None:
                p = p.replace(tzinfo=UTC)
            age_h = round((now - p).total_seconds() / 3600.0, 2)
        except Exception:
            age_h = None
        return {
            "status": status,
            "age_hours": age_h,
            "processed_at": processed_at or None,
        }

    # Events freshness — derived from the most-recent recent event
    events_proc = ""
    events_valid = None
    if recent_events:
        most_recent = max(
            recent_events,
            key=lambda e: e.get("processed_at", ""),
        )
        events_proc = most_recent.get("processed_at", "")
        events_valid = most_recent.get("valid_until")

    return {
        "events": _freshness(events_proc, events_valid) if events_proc
                 else {"status": "unknown", "age_hours": None, "processed_at": None},
        "macro_drivers": _freshness(manifest_published_at)
                 if manifest_published_at
                 else {"status": "unknown", "age_hours": None, "processed_at": None},
        "sectors": _freshness(manifest_published_at)
                 if manifest_published_at
                 else {"status": "unknown", "age_hours": None, "processed_at": None},
        "themes": _freshness(manifest_published_at)
                 if manifest_published_at
                 else {"status": "unknown", "age_hours": None, "processed_at": None},
        "radar": _freshness(events_proc, events_valid) if events_proc
                 else {"status": "unknown", "age_hours": None, "processed_at": None},
        "ask_context": {
            "status": "unknown",
            "age_hours": None,
            "processed_at": None,
            "note": "Ask grounding is deferred to a follow-up sprint.",
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_recent(events: list[dict[str, Any]], hours: float) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    out: list[dict[str, Any]] = []
    for ev in events:
        try:
            p = datetime.fromisoformat(ev.get("processed_at", ""))
            if p.tzinfo is None:
                p = p.replace(tzinfo=UTC)
            if p >= cutoff:
                out.append(ev)
        except Exception:
            continue
    return out


def _to_customer_event_summary(ev: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, customer-safe view of a tape event.

    Only fields appropriate for the customer Market Map. No internal IDs,
    no raw provider payloads.
    """
    return {
        "event_id": ev.get("event_id", ""),
        "event_family": ev.get("event_family", ""),
        "event_type": ev.get("event_type", ""),
        "status": ev.get("status", "reported"),
        "title": ev.get("title", ""),
        "summary_plain_english": ev.get("summary_plain_english", ""),
        "likely_positive_exposures": list(ev.get("likely_positive_exposures", [])),
        "likely_negative_exposures": list(ev.get("likely_negative_exposures", [])),
        "affected_channels": list(ev.get("affected_channels", [])),
        "confirmation_signals": list(ev.get("confirmation_signals", [])),
        "invalidation_signals": list(ev.get("invalidation_signals", [])),
        "freshness_status": ev.get("freshness_status", "unknown"),
        "processed_at": ev.get("processed_at", ""),
        "source_confidence": ev.get("source_confidence", "medium"),
        "materiality": ev.get("materiality", "medium"),
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def get_event_tape_freshness() -> dict[str, Any]:
    """Helper for market_now_builder._is_degraded() — returns tape file freshness."""
    path = os.path.join(_BASE, _TAPE_REL_PATH)
    if not os.path.exists(path):
        return {"status": "missing", "age_hours": None}
    try:
        age_h = (datetime.now(UTC).timestamp() - os.path.getmtime(path)) / 3600.0
        if age_h <= _RECENT_EVENT_WINDOW_H:
            status = "fresh"
        elif age_h <= 12.0:
            status = "degraded"
        else:
            status = "stale"
        return {"status": status, "age_hours": round(age_h, 2)}
    except Exception:
        return {"status": "unknown", "age_hours": None}
