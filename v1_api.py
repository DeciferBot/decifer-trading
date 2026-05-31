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
from typing import Optional

import requests as _requests

from flask import Blueprint, jsonify, request, Response

from v1_auth import require_api_key

log = logging.getLogger("decifer.v1_api")

v1_bp = Blueprint("v1", __name__, url_prefix="/v1")

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = _BASE_DIR / "data" / "intelligence"
_CACHE_DIR = _BASE_DIR / "data" / "api_cache"
_CACHE_TTL_SECONDS = 300  # 5 minutes
_MOMENTUM_CACHE_TTL = 1800  # 30 minutes

_DISCLAIMER = (
    "Intelligence data powered by Decifer. "
    "Not financial advice. For informational purposes only."
)

# ---------------------------------------------------------------------------
# Price momentum cache — 5-day return vs SPY via FMP batch-quote
# ---------------------------------------------------------------------------

_momentum_cache: dict[str, float] = {}   # symbol -> 5d relative return vs SPY
_momentum_cache_ts: float = 0.0
_momentum_lock = threading.Lock()
_momentum_refresh_in_flight = False
_MOMENTUM_CACHE_FILE = _CACHE_DIR / "price_momentum.json"
_FMP_BASE = "https://financialmodelingprep.com/stable"


def _fetch_momentum_data(symbols: list[str]) -> dict[str, float]:
    """
    Fetch 5-day price-change percentages for `symbols` (+ SPY as benchmark)
    from FMP's stock-price-change endpoint. Returns a dict symbol -> 5d_pct.
    Returns empty dict on any failure.
    """
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        return {}

    all_symbols = list({s.upper() for s in symbols} | {"SPY"})
    url = f"{_FMP_BASE}/stock-price-change"
    try:
        resp = _requests.get(
            url,
            params={"symbol": ",".join(all_symbols), "apikey": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("v1_api: momentum fetch failed — %s", exc)
        return {}

    result: dict[str, float] = {}
    for item in (data if isinstance(data, list) else []):
        sym = (item.get("symbol") or "").upper()
        pct = item.get("5D")
        if sym and pct is not None:
            try:
                result[sym] = float(pct)
            except (TypeError, ValueError):
                pass
    return result


def _momentum_pts(symbol: str, momentum_data: dict[str, float]) -> int:
    """
    Compute conviction bonus/penalty (-15 to +15) based on 5-day return
    relative to SPY. Returns 0 if data is unavailable.
    """
    sym_ret = momentum_data.get(symbol.upper())
    spy_ret = momentum_data.get("SPY")
    if sym_ret is None or spy_ret is None:
        return 0
    relative = sym_ret - spy_ret
    if relative >= 5.0:
        return 15
    if relative >= 2.0:
        return 10
    if relative >= 0.5:
        return 5
    if relative > -1.0:
        return 0
    if relative > -3.0:
        return -8
    return -15


def _get_momentum_data(symbols: list[str]) -> dict[str, float]:
    """
    Return cached momentum data, triggering a background refresh if stale.
    Always returns immediately — serves cached data while refresh runs.
    """
    global _momentum_cache, _momentum_cache_ts, _momentum_refresh_in_flight

    now = time.time()

    # Load from disk on cold start
    if not _momentum_cache and _MOMENTUM_CACHE_FILE.exists():
        try:
            saved = json.loads(_MOMENTUM_CACHE_FILE.read_text())
            _momentum_cache = saved.get("data", {})
            _momentum_cache_ts = saved.get("ts", 0.0)
        except Exception:
            pass

    cache_age = now - _momentum_cache_ts
    if cache_age < _MOMENTUM_CACHE_TTL:
        return _momentum_cache

    # Stale — trigger background refresh (only one at a time)
    with _momentum_lock:
        if not _momentum_refresh_in_flight:
            _momentum_refresh_in_flight = True

            def _refresh():
                global _momentum_cache, _momentum_cache_ts, _momentum_refresh_in_flight
                try:
                    fresh = _fetch_momentum_data(symbols)
                    if fresh:
                        with _momentum_lock:
                            _momentum_cache = fresh
                            _momentum_cache_ts = time.time()
                        try:
                            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                            _MOMENTUM_CACHE_FILE.write_text(
                                json.dumps({"ts": _momentum_cache_ts, "data": fresh})
                            )
                        except Exception as exc:
                            log.warning("v1_api: momentum cache write failed — %s", exc)
                finally:
                    with _momentum_lock:
                        _momentum_refresh_in_flight = False

            threading.Thread(target=_refresh, daemon=True).start()

    return _momentum_cache

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
    in_feed = candidate is not None
    feed_confidence = float(candidate.get("confidence") or 0) if candidate else 0.0
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

    # Conviction score — from conviction_cache (multi-dimensional engine)
    import conviction_cache as _conv_cache
    cached = _conv_cache.get(ticker)
    if cached:
        conviction_score = cached["composite"]
        conviction_tier  = cached["tier"].lower()
        conviction_breakdown = [
            {"signal": dim_id, "detail": d["signal"],
             "pts": d["raw_pts"], "max_pts": d["max_pts"]}
            for dim_id, d in cached.get("dimensions", {}).items()
        ]
        conviction_ts = cached.get("ts")
    else:
        # Conviction cache not yet populated — trigger background score and return null
        _conv_cache.trigger_rescore([ticker], reason="symbol_card_miss")
        conviction_score = None
        conviction_tier  = "unknown"
        conviction_breakdown = []
        conviction_ts = None

    return {
        "symbol": ticker.upper(),
        "api_version": "1",
        "ts": datetime.now(UTC).isoformat(),
        "conviction_score": conviction_score,
        "conviction_tier": conviction_tier,
        "conviction_breakdown": conviction_breakdown,
        "conviction_scored_at": conviction_ts,
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
            "conviction": conviction_ts,
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
    momentum_pts: int = 0,
) -> dict:
    """
    Compute a 0-100 conviction score from available intelligence signals.

    Scoring:
      - TTG evidence quality: up to 15 pts  (ttg_confidence * 15)
      - Driver active:        +25 pts        (live macro tailwind is on)
      - In intelligence feed: +15 pts        (symbol actively promoted today)
      - Feed confidence:      up to 20 pts   (feed_confidence * 20)
      - Price momentum vs SPY: -15 to +15 pts (5-day relative return)

    Max without momentum: 15 + 25 + 15 + 20 = 75
    Max with outperformance: 90

    Tiers:
      high      >= 70  — driver active + in feed + strong evidence + not lagging
      medium    40-69  — partially confirmed or lagging
      watchlist  < 40  — narrative only, no live signal
    """
    score = round(
        (ttg_confidence * 15)
        + (25 if driver_active else 0)
        + (15 if in_feed else 0)
        + (feed_confidence * 20 if in_feed else 0)
        + momentum_pts
    )
    score = max(0, min(score, 100))

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
    Conviction scores come from conviction_cache (multi-dimensional engine).
    """
    import conviction_cache as _conv_cache

    raw = _read_json(_DATA_DIR / "theme_graph" / "symbol_exposures.json")
    all_exposures = [e for e in raw.get("exposures", []) if e.get("status") == "active"]

    nodes = _load_nodes()
    driver_state = _read_drivers()
    active_drivers = set(driver_state.get("active_drivers", []))

    # Deduplicate: one entry per symbol, highest-confidence exposure as primary
    by_symbol: dict[str, dict] = {}
    for exp in all_exposures:
        sym = exp.get("symbol", "").upper()
        if not sym:
            continue
        existing = by_symbol.get(sym)
        if existing is None or (exp.get("confidence") or 0) > (existing.get("confidence") or 0):
            by_symbol[sym] = exp

    # Load all cached conviction scores — background refresh triggered automatically
    all_conv = _conv_cache.get_all()

    # Trigger a background rescore for any symbol not yet in cache
    unscored = [sym for sym in by_symbol if sym not in all_conv]
    if unscored:
        _conv_cache.trigger_rescore(unscored, reason="universe_miss")

    symbols = []
    for sym, exp in sorted(by_symbol.items()):
        theme_node = nodes.get(exp.get("theme_id", ""), {})
        driver_id  = exp.get("driver_id", "")
        driver_active = driver_id in active_drivers
        cached = all_conv.get(sym)

        symbols.append({
            "symbol":          sym,
            "label":           exp.get("label", sym),
            "theme_id":        exp.get("theme_id"),
            "theme_label":     theme_node.get("label", exp.get("theme_id")),
            "exposure_type":   exp.get("exposure_type"),
            "confidence":      float(exp.get("confidence") or 0),
            "driver_id":       driver_id,
            "driver_active":   driver_active,
            "conviction_score": cached["composite"] if cached else None,
            "conviction_tier":  cached["tier"].lower() if cached else "unknown",
            "conviction_ts":    cached["ts"] if cached else None,
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
