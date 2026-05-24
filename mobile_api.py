"""
mobile_api.py — Read-only mobile API adapter for Decifer Trading.

Composes sanitised, product-facing JSON from existing intelligence outputs
and bot state. No execution logic, no admin controls, no raw diagnostics.

All public functions are pure reads — no side effects, no mutations.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("decifer.mobile")

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _read_json(rel: str) -> Any:
    with open(os.path.join(_BASE, rel), encoding="utf-8") as f:
        return json.load(f)


def _read_jsonl_tail(rel: str, n: int = 200) -> list[dict]:
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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Label mappings ────────────────────────────────────────────────────────────

_DRIVER_LABELS: dict[str, str] = {
    "ai_capex_growth": "AI capex cycle expanding",
    "ai_compute_demand": "AI compute demand rising",
    "yields_rising": "Bond yields rising",
    "yields_falling": "Bond yields falling",
    "oil_supply_shock": "Oil supply shock active",
    "geopolitical_risk_rising": "Geopolitical risk elevated",
    "credit_stress_easing": "Credit conditions easing",
    "risk_on_rotation": "Risk-on rotation active",
    "gold_safe_haven_bid": "Gold safe-haven bid",
    "small_cap_risk_on": "Small caps outperforming large caps",
    "smh_tactical_weakness": "Chip sector under near-term pressure",
}

_REGIME_MOODS: dict[str, tuple[str, str]] = {
    "TRENDING_UP":   ("Trending up",       "bull"),
    "TRENDING_DOWN": ("Trending down",     "bear"),
    "BEAR_TRENDING": ("Trending down",     "bear"),
    "CHOPPY":        ("Choppy",            "neutral"),
    "PANIC":         ("Market panic",      "panic"),
    "UNKNOWN":       ("Assessing",         "neutral"),
}

_SESSION_LABELS: dict[str, str] = {
    "OPEN":    "Market open",
    "PRE":     "Pre-market",
    "POST":    "After-hours",
    "CLOSED":  "Market closed",
    "WEEKEND": "Weekend",
    "HOLIDAY": "Market holiday",
    "UNKNOWN": "Checking market hours",
}

_SESSION_CHAR_LABELS: dict[str, str] = {
    "MOMENTUM_BULL":  "Momentum rally",
    "FEAR_ELEVATED":  "Fear elevated",
    "RISK_ON":        "Risk-on",
    "RISK_OFF":       "Risk-off",
    "RANGE_BOUND":    "Range-bound",
    "TREND_DAY":      "Trend day",
    "REVERSAL":       "Reversal in play",
}


def _driver_label(key: str) -> str:
    return _DRIVER_LABELS.get(key, key.replace("_", " ").title())


def _regime_mood(regime: str) -> tuple[str, str]:
    return _REGIME_MOODS.get(regime, ("Assessing", "neutral"))


def _session_label(session: str) -> str:
    return _SESSION_LABELS.get(session, session)


_THEME_NAMES: dict[str, str] = {
    "ai_capex_growth":          "AI Capex Cycle",
    "ai_compute_demand":        "AI Compute Demand",
    "ai_compute_infrastructure":"AI Infrastructure",
    "data_centre_power":        "Data Centre Power",
    "memory_storage":           "Memory & Storage",
    "semiconductors":           "Semiconductors",
    "smh_tactical_weakness":    "Chip Sector Under Pressure",
    "yields_rising":            "Rising Yields",
    "yields_falling":           "Falling Yields",
    "risk_on_rotation":         "Risk-On Rotation",
    "gold_safe_haven_bid":      "Gold Safe-Haven Bid",
    "gold_precious_metals":     "Gold & Precious Metals",
    "credit_stress_easing":     "Credit Conditions Easing",
    "small_cap_risk_on":        "Small-Cap Risk-On",
    "oil_supply_shock":         "Oil Supply Shock",
    "geopolitical_risk_rising": "Geopolitical Risk",
    "software_cloud":           "Software & Cloud",
    "cybersecurity":            "Cybersecurity",
    "mega_cap_platforms":       "Mega-Cap Platforms",
    "consumer_discretionary":   "Consumer Discretionary",
    "travel_leisure":           "Travel & Leisure",
    "defensive_healthcare":     "Defensive Healthcare",
    "biotech":                  "Biotech",
    "regional_banks":           "Regional Banks",
    "infrastructure_reshoring": "Infrastructure & Reshoring",
    "copper_electrification":   "Copper & Electrification",
    "reits":                    "REITs",
    "reits_falling_yield":      "REITs — Rate Sensitive",
}


def _theme_name(theme_id: str) -> str:
    return _THEME_NAMES.get(theme_id, theme_id.replace("_", " ").title())


# Internal → plain-English replacements for Apex-generated free text.
# Apex uses these uppercase terms in market_read / rationale / key_risk fields.
_TEXT_CLEANUPS: list[tuple[str, str]] = [
    ("TRENDING_UP",   "trending up"),
    ("TRENDING_DOWN", "trending down"),
    ("BEAR_TRENDING", "trending down"),
    ("CHOPPY",        "choppy"),
    ("PANIC",         "market panic"),
    ("MOMENTUM_BULL", "momentum rally"),
    ("FEAR_ELEVATED", "fear elevated"),
    ("RISK_ON",       "risk-on"),
    ("RISK_OFF",      "risk-off"),
    ("TRENDING_UP regime", "trending-up regime"),
    ("DAR=pre-mkt",   "directional bias not yet confirmed"),
    ("DAR=",          ""),   # remove any remaining DAR= tokens
]


def _clean_text(text: str) -> str:
    """Replace internal terminology in Apex-generated free text."""
    for raw, replacement in _TEXT_CLEANUPS:
        text = text.replace(raw, replacement)
    return text


def _session_char_label(char: str) -> str:
    return _SESSION_CHAR_LABELS.get(char, char.replace("_", " ").title())


# ── Data loaders (read-only, gracefully degrade) ──────────────────────────────

def _load_driver_data() -> tuple[list[dict], list[str]]:
    """Returns (active_driver_cards, blocked_condition_keys)."""
    try:
        ld = _read_json("intelligence/live_driver_state.json")
        ev = ld.get("evidence", {})
        drivers = [
            {"key": k, "label": _driver_label(k), "evidence": ev.get(f"{k}_reason", "")}
            for k in ld.get("active_drivers", [])
        ]
        return drivers, ld.get("blocked_conditions", [])
    except Exception:
        return [], []


def _load_candidate_count() -> int:
    try:
        return len(_read_json("intelligence/economic_candidate_feed.json").get("candidates", []))
    except Exception:
        return 0


def _load_theme_data() -> tuple[list[dict], int]:
    """Returns (active_themes, dormant_count)."""
    try:
        ta = _read_json("intelligence/theme_activation.json")
        active, dormant = [], 0
        for t in ta.get("themes", []):
            state = t.get("state", "dormant")
            entry = {
                "theme_id": t.get("theme_id", ""),
                "name": _theme_name(t.get("theme_id", "")),
                "state": state,
                "direction": t.get("direction", ""),
                "confidence": t.get("confidence", 0),
                "summary": (t.get("reason") or "").split(",")[0].strip(),
                "candidate_count": t.get("candidate_count", 0),
            }
            if state in ("activated", "strengthening"):
                active.append(entry)
            else:
                dormant += 1
        return active, dormant
    except Exception:
        return [], 0


def _load_last_apex() -> dict:
    """Returns sanitised fields from the most recent Apex synthesis entry."""
    # apex_conversation_log.jsonl holds the full ApexDecision records including
    # market_read, new_entries, macro_bias, and session_character.
    try:
        records = _read_jsonl_tail("apex_conversation_log.jsonl", n=20)
        # Most recent entry with market_read wins (Track A/B/Shadow all land here)
        for rec in reversed(records):
            if rec.get("market_read"):
                return {
                    "market_read": rec.get("market_read", ""),
                    "session_char": _session_char_label(rec.get("session_character", "")),
                    "macro_bias": rec.get("macro_bias", ""),
                    "new_entries": rec.get("new_entries", []),
                }
    except Exception:
        pass
    return {}


def _load_pm_by_symbol() -> dict[str, dict]:
    try:
        records = _read_jsonl_tail("pm_engine/decisions.jsonl", n=400)
        by_sym: dict[str, dict] = {}
        for rec in records:
            sym = rec.get("symbol", "")
            if sym:
                by_sym[sym] = rec  # last record wins
        return by_sym
    except Exception:
        return {}


def _pm_status_label(action: str, thesis: str) -> str:
    _map = {
        "THESIS_INTACT":        "Signal intact",
        "THESIS_STRENGTHENING": "Signal strengthening",
        "INTACT_DEGRADED":      "Monitoring",
        "THESIS_DECAYING":      "Signal weakening — watching closely",
        "THESIS_BROKEN":        "Reviewing — setup has changed",
        "PLAYED_OUT":           "Position completed",
    }
    if thesis in _map:
        return _map[thesis]
    if action == "TRIM":
        return "Reducing exposure"
    if action == "FULL_EXIT":
        return "Exiting"
    if action in ("ADD", "DCA"):
        return "Adding — conviction rising"
    return "Monitoring"


def _shape_position(pos: dict, pm: dict) -> dict:
    entry = float(pos.get("entry") or 0)
    current = float(pos.get("current") or entry)
    direction = pos.get("direction", "LONG")
    pnl_pct: float | None = None
    if entry > 0:
        raw = (current - entry) / entry
        pnl_pct = round((raw if direction == "LONG" else -raw) * 100, 2)

    thesis = pos.get("entry_thesis") or pos.get("reasoning") or ""
    pm_status = _pm_status_label(pm.get("action_type", ""), pm.get("thesis_status", ""))
    score_delta = pm.get("score_delta")
    if score_delta is not None and abs(score_delta) >= 5:
        direction = "strengthened" if score_delta > 0 else "weakened"
        change_hint = f"Signal has {direction} since entry."
    else:
        change_hint = ""

    return {
        "symbol": pos.get("symbol", ""),
        "direction": direction,
        "trade_type": pos.get("trade_type", ""),
        "conviction": pos.get("conviction", ""),
        "entry_price": round(entry, 2),
        "current_price": round(current, 2),
        "pnl_pct": pnl_pct,
        "pnl_direction": "up" if (pnl_pct or 0) >= 0 else "down",
        "thesis": (thesis[:280] + "…") if len(thesis) > 280 else thesis,
        "pm_status": pm_status,
        "change_hint": change_hint,
    }


def _bot_status_label(dash: dict) -> str:
    if dash.get("paused"):
        return "Paused"
    session = str(dash.get("session") or "UNKNOWN")
    if session in ("CLOSED", "WEEKEND", "HOLIDAY"):
        return f"Monitoring — {_session_label(session).lower()}"
    if session == "PRE":
        return "Pre-market analysis active"
    return "Live"


# ── Public payload builders ───────────────────────────────────────────────────

def build_now_payload(dash: dict) -> dict:
    """What is happening right now."""
    regime = dict(dash.get("regime") or {})
    mood_label, tone = _regime_mood(regime.get("regime", "UNKNOWN"))
    session = str(dash.get("session") or "UNKNOWN")
    active_drivers, blocked = _load_driver_data()
    positions = list(dash.get("positions") or [])
    pv = float(dash.get("portfolio_value") or 0)
    vix = regime.get("vix")
    spy = regime.get("spy_price")

    return {
        "ts": _now_iso(),
        "market_mood": {
            "label": mood_label,
            "tone": tone,
            "session": _session_label(session),
            "is_open": session in ("OPEN", "PRE"),
            "vix": round(float(vix), 1) if vix else None,
            "spy_price": round(float(spy), 2) if spy else None,
        },
        "main_forces": active_drivers,
        "blocked_conditions": blocked,
        "last_scan": str(dash.get("last_scan") or ""),
        "watching_count": _load_candidate_count(),
        "positions_count": len(positions),
        "portfolio_value": round(pv, 2),
        "daily_pnl": round(float(dash.get("daily_pnl") or 0), 2),
        "bot_status": _bot_status_label(dash),
    }


def build_why_payload() -> dict:
    """Why is it happening — drivers, theme transmission, Apex read."""
    active_drivers, blocked = _load_driver_data()
    themes_active, dormant_count = _load_theme_data()
    apex = _load_last_apex()

    if themes_active:
        names = ", ".join(t["name"] for t in themes_active[:3])
        n = len(themes_active)
        summary = f"{n} theme{'s' if n != 1 else ''} active: {names}."
    else:
        summary = "No themes currently activated by the market map."

    # Annotate blocked drivers as headwinds
    headwinds = [
        {"key": k, "label": _driver_label(k), "impact": "headwind"}
        for k in blocked
    ]

    return {
        "ts": _now_iso(),
        "macro_drivers": active_drivers,
        "headwinds": headwinds,
        "themes_active": themes_active,
        "dormant_theme_count": dormant_count,
        "transmission_summary": summary,
        "market_read": _clean_text(apex.get("market_read", "")),
        "session_character": apex.get("session_char", ""),
        "macro_bias": apex.get("macro_bias", ""),
    }


def build_alpha_payload() -> dict:
    """Where opportunity may be forming."""
    # Intelligence candidates
    under_review: list[dict] = []
    try:
        cf = _read_json("intelligence/economic_candidate_feed.json")
        for c in cf.get("candidates", [])[:20]:
            conf = c.get("confidence", 0)
            hints = c.get("route_hint") or []
            status = (
                "In focus" if ("position" in hints or "swing" in hints)
                else "On the radar"
            )
            under_review.append({
                "symbol": c.get("symbol", ""),
                "theme": _theme_name(c.get("theme", "")),
                "reason_to_care": c.get("reason_to_care") or c.get("reason", ""),
                "entry_status": status,
                "confidence": "high" if conf >= 0.7 else "medium" if conf >= 0.4 else "low",
                "risk_flags": (c.get("risk_flags") or [])[:2],
            })
    except Exception:
        pass

    # Latest Apex entries — already in plain English
    apex = _load_last_apex()
    apex_entries: list[dict] = []
    for e in apex.get("new_entries", []):
        apex_entries.append({
            "symbol": e.get("symbol", ""),
            "direction": e.get("direction", "LONG"),
            "trade_type": e.get("trade_type", ""),
            "conviction": e.get("conviction", ""),
            "rationale": _clean_text(e.get("rationale", "")),
            "key_risk": _clean_text(e.get("key_risk", "")),
        })

    return {
        "ts": _now_iso(),
        "under_review": under_review,
        "apex_last_cycle": apex_entries,
        "blocked_conditions": [],
    }


def build_portfolio_payload(dash: dict) -> dict:
    """What Decifer holds and why."""
    positions_raw = list(dash.get("positions") or [])
    pv = float(dash.get("portfolio_value") or 0)
    daily_pnl = float(dash.get("daily_pnl") or 0)
    pm_by_sym = _load_pm_by_symbol()

    positions = [
        _shape_position(p, pm_by_sym.get(p.get("symbol", ""), {}))
        for p in positions_raw
    ]

    return {
        "ts": _now_iso(),
        "portfolio_summary": {
            "portfolio_value": round(pv, 2),
            "daily_pnl": round(daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl / pv * 100, 2) if pv > 0 else 0,
            "open_count": len(positions),
        },
        "positions": positions,
    }
