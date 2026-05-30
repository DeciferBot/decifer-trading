# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  v1_drivers_api.py                         ║
# ║   Product API v1 — Market Driver State Feed blueprint       ║
# ║   Layer: SAAS_OUTPUT — no execution imports                 ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
v1_drivers_api.py — Product C: Market Driver State Feed.

Routes
──────
  GET /v1/drivers   — current active macro driver state

Returns the deterministic, rule-based driver state derived from 11+2
real market sensors (ETFs + futures). Updated each time the intelligence
pipeline runs (every scan cycle during market hours).

Stale contract:
  stale=true  when driver data is older than _STALE_THRESHOLD_MINUTES.
  Clients receive last known state + stale flag — never a 503 on stale data.

No LLM. No broker state. No execution fields.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, jsonify, Response

from v1_auth import require_api_key

log = logging.getLogger("decifer.v1_drivers_api")

v1_drivers_bp = Blueprint("v1_drivers", __name__)

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_DRIVER_STATE_PATH = _BASE_DIR / "data" / "intelligence" / "live_driver_state.json"
_THEME_ACTIVATION_PATH = _BASE_DIR / "data" / "intelligence" / "theme_activation.json"
_NODES_PATH = _BASE_DIR / "data" / "intelligence" / "theme_graph" / "theme_nodes.json"

_STALE_THRESHOLD_MINUTES = 30
_DISCLAIMER = (
    "Market driver state powered by Decifer. "
    "Deterministic rules applied to real ETF/futures price data. "
    "Not financial advice. For informational purposes only."
)

# Human-readable labels for driver IDs not in theme_nodes.json
_DRIVER_LABELS: dict[str, str] = {
    "ai_capex_growth": "AI Infrastructure Buildout",
    "ai_compute_demand": "AI Compute Demand",
    "yields_rising": "Rising Yields",
    "yields_falling": "Falling Yields / Mortgage Relief",
    "oil_supply_shock": "Oil Supply Shock",
    "geopolitical_risk_rising": "Geopolitical Risk Rising",
    "geopolitical_risk_falling": "Geopolitical Risk Falling",
    "credit_stress_rising": "Credit Stress Rising",
    "risk_off_rotation": "Risk-Off Rotation",
    "risk_on_rotation": "Risk-On Rotation",
    "gold_safe_haven_bid": "Gold Safe-Haven Bid",
    "credit_stress_easing": "Credit Stress Easing",
    "small_cap_risk_on": "Small-Cap Risk-On",
    "futures_risk_on": "Futures: Risk-On (ES/NQ)",
    "futures_risk_off": "Futures: Risk-Off (ES/NQ)",
    "smh_tactical_weakness": "SMH Tactical Weakness (blocked condition)",
}

# Which sensor values in `evidence` relate to each driver
_DRIVER_EVIDENCE_SENSORS: dict[str, list[str]] = {
    "ai_capex_growth": ["smh_5d_ret"],
    "ai_compute_demand": ["nvda_5d_ret"],
    "yields_rising": ["ief_5d_ret"],
    "yields_falling": ["ief_5d_ret"],
    "oil_supply_shock": ["uso_5d_ret"],
    "geopolitical_risk_rising": ["ita_5d_ret", "spy_5d_ret"],
    "geopolitical_risk_falling": ["ita_5d_ret", "spy_5d_ret", "uso_5d_ret"],
    "credit_stress_rising": ["hyg_5d_ret", "lqd_5d_ret"],
    "risk_off_rotation": ["uvxy_5d_ret", "spy_5d_ret"],
    "risk_on_rotation": ["uvxy_5d_ret", "spy_5d_ret"],
    "gold_safe_haven_bid": ["gld_5d_ret"],
    "credit_stress_easing": ["hyg_5d_ret", "lqd_5d_ret"],
    "small_cap_risk_on": ["iwm_5d_ret", "spy_5d_ret"],
    "futures_risk_on": ["es_5d_ret"],
    "futures_risk_off": ["es_5d_ret"],
}


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        log.warning("v1_drivers_api: failed to read %s — %s", path, exc)
        return {}


def _staleness(path: Path) -> tuple[bool, str | None]:
    """Returns (is_stale, reason_or_None)."""
    if not path.exists():
        return True, "data_file_missing"
    age_minutes = (time.time() - path.stat().st_mtime) / 60
    if age_minutes > _STALE_THRESHOLD_MINUTES:
        return True, f"data_{int(age_minutes)}min_old"
    return False, None


def _build_driver_entry(driver_id: str, evidence: dict) -> dict:
    """Build a single driver dict with label and relevant sensor evidence."""
    label = _DRIVER_LABELS.get(driver_id, driver_id.replace("_", " ").title())
    sensors = _DRIVER_EVIDENCE_SENSORS.get(driver_id, [])
    driver_evidence = {s: evidence.get(s) for s in sensors if s in evidence}
    return {
        "id": driver_id,
        "label": label,
        "evidence": driver_evidence,
    }


def _activated_themes(theme_list: list[dict]) -> list[dict]:
    """Return only activated/headwind themes with safe fields."""
    safe_states = {"activated", "headwind"}
    return [
        {
            "theme_id": t.get("theme_id"),
            "state": t.get("state"),
            "direction": t.get("direction"),
            "confidence": t.get("confidence"),
            "activated_by": t.get("activated_by", []),
            "risk_flags": t.get("risk_flags", []),
        }
        for t in theme_list
        if t.get("state") in safe_states
    ]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@v1_drivers_bp.route("/v1/drivers", methods=["GET"])
@require_api_key
def market_drivers() -> Response:
    """
    Current macro driver state.

    Returns which of 15 deterministic drivers are active right now,
    the evidence (sensor values) behind each, blocked conditions,
    activated themes, and data freshness metadata.

    stale=true when data is older than 30 minutes — clients receive
    the last known state rather than an error.
    """
    driver_state = _read_json(_DRIVER_STATE_PATH)
    theme_data = _read_json(_THEME_ACTIVATION_PATH)

    stale, stale_reason = _staleness(_DRIVER_STATE_PATH)

    active_ids: list[str] = driver_state.get("active_drivers", [])
    blocked: list[str] = driver_state.get("blocked_conditions", [])
    raw_evidence: dict = driver_state.get("evidence", {})

    active_drivers = [_build_driver_entry(d, raw_evidence) for d in active_ids]
    blocked_entries = [_build_driver_entry(b, raw_evidence) for b in blocked]

    themes_activated = _activated_themes(theme_data if isinstance(theme_data, list)
                                         else theme_data.get("themes", []))

    # Futures advisory (separate from core 11 sensors — advisory only)
    futures: dict = {}
    for key in ("es_5d_ret", "nq_5d_ret"):
        if key in raw_evidence:
            futures[key] = raw_evidence[key]
    futures_drivers = [d for d in active_ids if d.startswith("futures_")]

    r = jsonify({
        "api_version": "1",
        "ts": datetime.now(UTC).isoformat(),
        "stale": stale,
        "stale_reason": stale_reason,
        "data_ts": driver_state.get("generated_at"),
        "mode": driver_state.get("mode", "unknown"),
        "active_drivers": active_drivers,
        "active_driver_ids": active_ids,
        "blocked_conditions": blocked_entries,
        "blocked_condition_ids": blocked,
        "futures": {
            **futures,
            "advisory_drivers": futures_drivers,
        },
        "activated_themes": themes_activated,
        "activated_theme_count": len(themes_activated),
        "sensor_count": len(raw_evidence),
        "disclaimer": _DISCLAIMER,
    })
    r.status_code = 200
    return r
