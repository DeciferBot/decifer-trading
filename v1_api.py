# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  v1_api.py                                 ║
# ║   Product API v1 — Flask blueprint                          ║
# ║   Layer: SAAS_OUTPUT — no execution imports                 ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
v1_api.py — Product API v1 blueprint.

Routes
──────
  GET /v1/health               — public liveness check (no auth)
  GET /v1/universe             — browseable symbol list (authenticated)
  GET /v1/symbol/<ticker>      — Symbol Intelligence Card (authenticated)

Symbol Intelligence Card combines three intelligence lenses:
  1. Theme Transmission Graph  — which macro themes the symbol belongs to,
                                  evidence basis, driver-active status
  2. Intelligence Feed          — whether the symbol is in the live candidate
                                  feed and what role it plays
  3. Options Flow               — real volume expansion signal (Alpaca);
                                  results cached 5 min to file

No execution imports. No broker state. No raw provider data in responses.
All data reads are from intelligence data files only.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, Response

from v1_auth import require_api_key

log = logging.getLogger("decifer.v1_api")

v1_bp = Blueprint("v1", __name__, url_prefix="/v1")

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = _BASE_DIR / "data" / "intelligence"
_CACHE_DIR = _BASE_DIR / "data" / "api_cache"
_CACHE_TTL_SECONDS = 300  # 5 minutes

_DISCLAIMER = (
    "Intelligence data powered by Decifer. "
    "Not financial advice. For informational purposes only."
)

_BLOCKED_RESPONSE_KEYS = frozenset({
    "strike", "expiry", "delta", "gamma", "theta", "vega",
    "open_interest", "iv", "implied_volatility",
    "bid", "ask", "last_price", "raw_price",
    "entry_price", "exit_price", "pnl", "pnl_pct",
    "order_id", "position_size", "qty", "stop_price",
})

# ---------------------------------------------------------------------------
# Data readers — read intelligence files, no execution dependencies
# ---------------------------------------------------------------------------

_nodes_cache: dict | None = None
_nodes_lock = threading.Lock()


def _load_nodes() -> dict[str, dict]:
    """Load theme/bucket node labels from theme_nodes.json. Cached in memory."""
    global _nodes_cache
    if _nodes_cache is not None:
        return _nodes_cache
    with _nodes_lock:
        if _nodes_cache is not None:
            return _nodes_cache
        try:
            raw = json.loads((_DATA_DIR / "theme_graph" / "theme_nodes.json").read_text())
            _nodes_cache = {n["id"]: n for n in raw.get("nodes", [])}
        except Exception as exc:
            log.warning("v1_api: failed to load theme nodes — %s", exc)
            _nodes_cache = {}
    return _nodes_cache


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        log.warning("v1_api: failed to read %s — %s", path, exc)
        return {}


def _read_exposures(ticker: str) -> list[dict]:
    """All active TTG exposures for a ticker."""
    raw = _read_json(_DATA_DIR / "theme_graph" / "symbol_exposures.json")
    return [
        e for e in raw.get("exposures", [])
        if e.get("symbol", "").upper() == ticker.upper()
        and e.get("status") == "active"
    ]


def _read_drivers() -> dict:
    """Current live driver state."""
    return _read_json(_DATA_DIR / "live_driver_state.json")


def _read_candidate_for(ticker: str) -> dict | None:
    """Find this ticker in the current intelligence candidate feed, if present."""
    raw = _read_json(_DATA_DIR / "economic_candidate_feed.json")
    for c in raw.get("candidates", []):
        if c.get("symbol", "").upper() == ticker.upper():
            return c
    return None


# ---------------------------------------------------------------------------
# Options flow cache
# ---------------------------------------------------------------------------

_flow_cache_lock = threading.Lock()


def _options_flow_cached(ticker: str) -> dict | None:
    """
    Return cached options flow for ticker, or fetch fresh if stale/missing.
    Cache TTL: 5 minutes. Stored in data/api_cache/{ticker}_flow.json.
    Returns None if Alpaca is unavailable or fetch fails.
    """
    cache_file = _CACHE_DIR / f"{ticker.upper()}_flow.json"

    with _flow_cache_lock:
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < _CACHE_TTL_SECONDS:
                try:
                    return json.loads(cache_file.read_text())
                except Exception:
                    pass

        result = _fetch_options_flow(ticker)
        if result is not None:
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(result))
            except Exception as exc:
                log.warning("v1_api: cache write failed for %s — %s", ticker, exc)
        return result


def _fetch_options_flow(ticker: str) -> dict | None:
    """
    Fetch live options flow from Alpaca. Returns a safe dict or None on failure.
    Imports options_provider at call time to keep module import fast.
    """
    try:
        from options_provider import get_options_flow_data, MIN_SIDE_VOLUME, MIN_DAY_OVER_DAY_RATIO, PREV_VOLUME_FLOOR
    except ImportError as exc:
        log.warning("v1_api: options_provider not available — %s", exc)
        return None

    try:
        flow = get_options_flow_data(ticker, min_dte=7, max_dte=45)
    except Exception as exc:
        log.warning("v1_api: options flow fetch failed for %s — %s", ticker, exc)
        return None

    if flow is None or not flow.flow_metrics_available:
        return None

    def _expansion(today: float, prev: float) -> float | None:
        denom = max(prev, PREV_VOLUME_FLOOR)
        return round(today / denom, 2) if today > 0 else None

    call_exp = _expansion(flow.call_volume, flow.call_prev_volume)
    put_exp = _expansion(flow.put_volume, flow.put_prev_volume)

    unusual_calls = (
        flow.call_volume >= MIN_SIDE_VOLUME
        and call_exp is not None
        and call_exp >= MIN_DAY_OVER_DAY_RATIO
    )
    unusual_puts = (
        flow.put_volume >= MIN_SIDE_VOLUME
        and put_exp is not None
        and put_exp >= MIN_DAY_OVER_DAY_RATIO
    )

    return {
        "call_volume": int(flow.call_volume),
        "put_volume": int(flow.put_volume),
        "call_trade_count": int(flow.call_trade_count),
        "call_expansion": call_exp,
        "put_expansion": put_exp,
        "unusual_calls": unusual_calls,
        "unusual_puts": unusual_puts,
        "unusual": unusual_calls or unusual_puts,
        "oi_available": False,
        "oi_note": "Open interest unavailable from current provider. Signal uses volume expansion only.",
        "provider": flow.provider,
        "flow_definition": flow.flow_definition,
        "data_ts": flow.provider_timestamp,
    }


# ---------------------------------------------------------------------------
# Symbol card builder
# ---------------------------------------------------------------------------

def _build_symbol_card(ticker: str) -> dict | None:
    """
    Build the full Symbol Intelligence Card. Returns None if ticker not in TTG.
    """
    exposures = _read_exposures(ticker)
    if not exposures:
        return None

    nodes = _load_nodes()
    driver_state = _read_drivers()
    active_drivers = set(driver_state.get("active_drivers", []))
    candidate = _read_candidate_for(ticker)
    flow = _options_flow_cached(ticker)

    # Build themes list — one entry per TTG exposure, sorted by confidence desc
    themes = []
    for exp in sorted(exposures, key=lambda e: -(e.get("confidence") or 0)):
        theme_node = nodes.get(exp.get("theme_id", ""), {})
        bucket_node = nodes.get(exp.get("bucket_id", ""), {})
        driver_id = exp.get("driver_id", "")
        themes.append({
            "theme_id": exp.get("theme_id"),
            "theme_label": theme_node.get("label", exp.get("theme_id")),
            "bucket_id": exp.get("bucket_id"),
            "bucket_label": bucket_node.get("label", exp.get("bucket_id")),
            "exposure_type": exp.get("exposure_type"),
            "confidence": exp.get("confidence"),
            "reason_to_care": exp.get("reason_to_care"),
            "risk_note": exp.get("risk_note"),
            "evidence_basis": exp.get("evidence_basis"),
            "driver_id": driver_id,
            "driver_active": driver_id in active_drivers,
            "last_reviewed": exp.get("last_reviewed"),
        })

    # Intelligence feed section — safe fields only
    intel_feed = None
    if candidate:
        intel_feed = {
            "in_feed": True,
            "role": candidate.get("role"),
            "reason_to_care": candidate.get("reason_to_care"),
            "confidence": candidate.get("confidence"),
            "risk_flags": candidate.get("risk_flags", []),
            "theme": candidate.get("theme"),
            "driver": candidate.get("driver"),
            "feed_ts": candidate.get("generated_at"),
        }

    return {
        "symbol": ticker.upper(),
        "api_version": "1",
        "ts": datetime.now(UTC).isoformat(),
        "themes": themes,
        "intelligence_feed": intel_feed,
        "options_flow": flow,
        "market_context": {
            "active_drivers": sorted(active_drivers),
            "blocked_conditions": driver_state.get("blocked_conditions", []),
            "drivers_ts": driver_state.get("generated_at"),
            "drivers_mode": driver_state.get("mode"),
        },
        "data_freshness": {
            "themes": exposures[0].get("last_reviewed") if exposures else None,
            "options_flow": flow.get("data_ts") if flow else None,
            "drivers": driver_state.get("generated_at"),
            "intelligence_feed": candidate.get("generated_at") if candidate else None,
        },
        "disclaimer": _DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@v1_bp.route("/health", methods=["GET"])
def v1_health() -> Response:
    """Public liveness check for the v1 API."""
    driver_state = _read_drivers()
    return jsonify({
        "status": "ok",
        "api_version": "1",
        "ts": datetime.now(UTC).isoformat(),
        "drivers_mode": driver_state.get("mode", "unknown"),
        "drivers_ts": driver_state.get("generated_at"),
    })


def _conviction_score(
    ttg_confidence: float,
    driver_active: bool,
    in_feed: bool,
    feed_confidence: float,
) -> dict:
    """
    Compute a 0-100 conviction score from available intelligence signals.

    Scoring:
      - TTG evidence quality: up to 40 pts (confidence * 40)
      - Driver active:        +35 pts (live macro tailwind)
      - In intelligence feed: +15 pts
      - Feed confidence:      up to 10 pts (feed_confidence * 10)

    Tiers:
      high      >= 70  — driver active + confirmed in feed + strong evidence
      medium    40-69  — partially confirmed, at least one live signal
      watchlist  < 40  — narrative building, not yet ready
    """
    score = round(
        (ttg_confidence * 40)
        + (35 if driver_active else 0)
        + (15 if in_feed else 0)
        + (feed_confidence * 10 if in_feed else 0)
    )
    score = min(score, 100)

    if score >= 70:
        tier = "high"
    elif score >= 40:
        tier = "medium"
    else:
        tier = "watchlist"

    return {"score": score, "tier": tier}


@v1_bp.route("/universe", methods=["GET"])
@require_api_key
def universe_list() -> Response:
    """
    Browseable Symbol Universe.

    Returns all active TTG symbols with their primary theme, company label,
    exposure type, driver-active status, and Decifer conviction score + tier.
    """
    raw = _read_json(_DATA_DIR / "theme_graph" / "symbol_exposures.json")
    all_exposures = [e for e in raw.get("exposures", []) if e.get("status") == "active"]

    nodes = _load_nodes()
    driver_state = _read_drivers()
    active_drivers = set(driver_state.get("active_drivers", []))

    # Build feed lookup: symbol -> candidate
    feed_raw = _read_json(_DATA_DIR / "economic_candidate_feed.json")
    feed_by_sym: dict[str, dict] = {
        c.get("symbol", "").upper(): c
        for c in feed_raw.get("candidates", [])
        if c.get("symbol")
    }

    # Deduplicate: one entry per symbol, highest-confidence exposure as primary
    by_symbol: dict[str, dict] = {}
    for exp in all_exposures:
        sym = exp.get("symbol", "").upper()
        if not sym:
            continue
        existing = by_symbol.get(sym)
        if existing is None or (exp.get("confidence") or 0) > (existing.get("confidence") or 0):
            by_symbol[sym] = exp

    symbols = []
    for sym, exp in sorted(by_symbol.items()):
        theme_node = nodes.get(exp.get("theme_id", ""), {})
        driver_id = exp.get("driver_id", "")
        driver_active = driver_id in active_drivers
        feed_entry = feed_by_sym.get(sym)
        in_feed = feed_entry is not None
        feed_confidence = float(feed_entry.get("confidence") or 0) if feed_entry else 0.0
        ttg_confidence = float(exp.get("confidence") or 0)

        conviction = _conviction_score(ttg_confidence, driver_active, in_feed, feed_confidence)

        symbols.append({
            "symbol": sym,
            "label": exp.get("label", sym),
            "theme_id": exp.get("theme_id"),
            "theme_label": theme_node.get("label", exp.get("theme_id")),
            "exposure_type": exp.get("exposure_type"),
            "confidence": ttg_confidence,
            "driver_id": driver_id,
            "driver_active": driver_active,
            "in_feed": in_feed,
            "conviction_score": conviction["score"],
            "conviction_tier": conviction["tier"],
        })

    r = jsonify({
        "symbols": symbols,
        "total": len(symbols),
        "ts": datetime.now(UTC).isoformat(),
    })
    r.status_code = 200
    return r


@v1_bp.route("/symbol/<ticker>", methods=["GET"])
@require_api_key
def symbol_card(ticker: str) -> Response:
    """
    Symbol Intelligence Card.

    Returns theme membership, intelligence feed status, and options flow
    for a single ticker. Symbol must be in the Theme Transmission Graph.
    """
    ticker = ticker.upper().strip()
    if not ticker or len(ticker) > 10 or not ticker.isalpha():
        r = jsonify({"error": {"code": 400, "message": "Invalid ticker format."}})
        r.status_code = 400
        return r

    card = _build_symbol_card(ticker)
    if card is None:
        r = jsonify({
            "error": {
                "code": 404,
                "message": f"{ticker} is not in the Theme Transmission Graph.",
            }
        })
        r.status_code = 404
        return r

    r = jsonify(card)
    r.status_code = 200
    return r
