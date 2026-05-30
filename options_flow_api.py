# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_flow_api.py                       ║
# ║   Product API v1 — Options Flow Screen blueprint            ║
# ║   Layer: SAAS_OUTPUT — no execution imports                 ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
options_flow_api.py — Product B: Options Flow Screen blueprint.

Routes
──────
  GET  /v1/options/universe-scan  — unusual flow across full TTG universe
                                    (precomputed; refreshes when stale > 30 min)
  POST /v1/options/scan           — on-demand scan of up to 50 custom symbols

  GET  /api/options/feed          — rolling event feed (legacy / internal)
  GET  /api/options/leaderboard   — ranked leaderboard (legacy / internal)
  GET  /api/options/symbol/<tkr>  — per-symbol detail (legacy / internal)

No execution imports. No broker state.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from v1_auth import require_api_key

log = logging.getLogger("decifer.options_flow_api")

bp = Blueprint("options_flow", __name__)

# Module-level import so tests can patch options_flow_api.scan_symbols
try:
    from options_flow_scanner import scan_symbols
except ImportError:
    scan_symbols = None  # type: ignore[assignment]

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_EVENTS_PATH = str(_BASE_DIR / "data" / "options_flow" / "live_events.json")
_LEADERBOARD_PATH = _BASE_DIR / "data" / "options_flow" / "leaderboard.json"

_DEFAULT_FEED_LIMIT = 100
_MAX_FEED_LIMIT = 500
_UNIVERSE_SCAN_TTL = 1800  # 30 minutes before triggering a fresh scan
_MAX_CUSTOM_SYMBOLS = 50


def _load_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("options_flow_api: could not read %s — %s", path, exc)
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ok(data: dict, status: int = 200) -> Response:
    r = jsonify(data)
    r.status_code = status
    return r


def _unavailable(msg: str) -> Response:
    return _ok({"status": "unavailable", "message": msg, "ts": _now_iso()}, 503)


@bp.route("/api/options/feed", methods=["GET", "OPTIONS"])
def options_feed() -> Response:
    """Rolling feed of detected unusual flow events, newest first.

    Query params:
        limit   — max events to return (default 100, max 500)
        side    — filter by CALL | PUT | MIXED
        signal  — filter by SWEEP | CLUSTER | CROSS_EXPIRY
    """
    if request.method == "OPTIONS":
        return Response(status=204)

    data = _load_json(_EVENTS_PATH)
    if data is None:
        return _unavailable("Options flow data not yet available. Stream may be starting.")

    events = data.get("events", [])

    side_filter = request.args.get("side", "").upper()
    signal_filter = request.args.get("signal", "").upper()
    if side_filter:
        events = [e for e in events if e.get("side") == side_filter]
    if signal_filter:
        events = [e for e in events if e.get("signal_type") == signal_filter]

    try:
        limit = min(int(request.args.get("limit", _DEFAULT_FEED_LIMIT)), _MAX_FEED_LIMIT)
    except (ValueError, TypeError):
        limit = _DEFAULT_FEED_LIMIT

    return _ok({
        "status": "ok",
        "ts": data.get("ts", _now_iso()),
        "total": data.get("count", 0),
        "returned": min(limit, len(events)),
        "events": events[:limit],
    })


@bp.route("/api/options/leaderboard", methods=["GET", "OPTIONS"])
def options_leaderboard() -> Response:
    """Leaderboard of symbols ranked by unusual flow score, highest first.

    Query params:
        limit   — max symbols to return (default 50)
        driver  — filter to symbols tagged with this driver ID
    """
    if request.method == "OPTIONS":
        return Response(status=204)

    data = _load_json(_LEADERBOARD_PATH)
    if data is None:
        return _unavailable("Leaderboard not yet available. Stream may be starting.")

    rows = data.get("leaderboard", [])

    driver_filter = request.args.get("driver", "").lower()
    if driver_filter:
        rows = [r for r in rows if driver_filter in (r.get("driver_tags") or [])]

    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    return _ok({
        "status": "ok",
        "ts": data.get("ts", _now_iso()),
        "total": data.get("count", 0),
        "returned": min(limit, len(rows)),
        "leaderboard": rows[:limit],
    })


@bp.route("/api/options/symbol/<ticker>", methods=["GET", "OPTIONS"])
def options_symbol(ticker: str) -> Response:
    """Per-symbol options flow detail — all recent events for one underlying.

    Path params:
        ticker  — underlying symbol (e.g. NVDA)
    """
    if request.method == "OPTIONS":
        return Response(status=204)

    ticker = ticker.upper().strip()
    if not ticker or not ticker.isalpha() or len(ticker) > 6:
        return _ok({"status": "error", "message": "Invalid ticker."}, 400)

    data = _load_json(_EVENTS_PATH)
    if data is None:
        return _unavailable("Options flow data not yet available.")

    events = [e for e in data.get("events", []) if e.get("underlying") == ticker]

    leaderboard_data = _load_json(_LEADERBOARD_PATH)
    summary = None
    if leaderboard_data:
        for row in leaderboard_data.get("leaderboard", []):
            if row.get("underlying") == ticker:
                summary = row
                break

    return _ok({
        "status": "ok",
        "ts": _now_iso(),
        "underlying": ticker,
        "event_count": len(events),
        "summary": summary,
        "events": events,
    })


# ---------------------------------------------------------------------------
# Product API v1 — authenticated options flow routes
# ---------------------------------------------------------------------------

def _leaderboard_stale() -> bool:
    """True if precomputed leaderboard is missing or older than _UNIVERSE_SCAN_TTL."""
    path = _LEADERBOARD_PATH
    if isinstance(path, str):
        path = Path(path)
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > _UNIVERSE_SCAN_TTL


def _load_leaderboard() -> dict | None:
    path = _LEADERBOARD_PATH
    if isinstance(path, str):
        path = Path(path)
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


@bp.route("/v1/options/universe-scan", methods=["GET"])
@require_api_key
def v1_universe_scan() -> Response:
    """
    Unusual options flow across the full TTG universe (125 active symbols).

    Returns precomputed leaderboard. If stale (> 30 min), triggers a fresh
    background scan. Callers always get the last known result immediately —
    never a blocking wait.
    """
    stale = _leaderboard_stale()
    data = _load_leaderboard()

    if stale:
        # Trigger fresh scan in background — don't block the response
        import threading
        def _bg_scan():
            try:
                from options_flow_scanner import scan_universe
                scan_universe(write=True)
            except Exception as exc:
                log.warning("v1_universe_scan: background scan failed — %s", exc)
        threading.Thread(target=_bg_scan, daemon=True).start()

    if data is None:
        return _ok({
            "status": "scanning",
            "message": "First scan in progress. Retry in 60 seconds.",
            "ts": _now_iso(),
        }, 202)

    return _ok({
        "status": "ok",
        "stale": stale,
        **data,
    })


@bp.route("/v1/options/scan", methods=["POST"])
@require_api_key
def v1_custom_scan() -> Response:
    """
    On-demand options flow scan for up to 50 custom symbols.

    Request body (JSON):
      { "symbols": ["NVDA", "AAPL", ...], "min_dte": 7, "max_dte": 45 }

    Returns ranked results for symbols with available flow data.
    Symbols with no Alpaca options data are silently omitted.
    """
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return _ok({"error": {"code": 400, "message": "Invalid JSON body."}}, 400)

    raw_symbols = body.get("symbols", [])
    if not isinstance(raw_symbols, list) or not raw_symbols:
        return _ok({"error": {"code": 400, "message": "symbols must be a non-empty list."}}, 400)

    symbols = [str(s).upper().strip() for s in raw_symbols if str(s).strip().isalpha()][:_MAX_CUSTOM_SYMBOLS]
    if not symbols:
        return _ok({"error": {"code": 400, "message": "No valid ticker symbols provided."}}, 400)

    if scan_symbols is None:
        log.error("v1_custom_scan: options_flow_scanner unavailable")
        return _ok({"error": {"code": 503, "message": "Scanner unavailable."}}, 503)

    rows = scan_symbols(symbols)

    return _ok({
        "status": "ok",
        "ts": _now_iso(),
        "requested": len(symbols),
        "returned": len(rows),
        "unusual_count": sum(1 for r in rows if r["unusual"]),
        "oi_available": False,
        "oi_note": (
            "Open interest unavailable from current provider (Alpaca). "
            "Signal uses day-over-day volume expansion only."
        ),
        "results": rows,
    })
