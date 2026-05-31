# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  intelligence_api.py                       ║
# ║   Intelligence Cloud API — DigitalOcean SaaS surface        ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
intelligence_api.py — Decifer Intelligence Cloud API.

Runtime classification: saas_output_runtime
Deployment target:      DigitalOcean (DECIFER_RUNTIME_MODE=intelligence_cloud)
Execution:              UNCONDITIONALLY BLOCKED — assert_execution_allowed() raises on
                        every mutation function when runtime_mode=intelligence_cloud.

Endpoints
─────────
  GET /health                — liveness / readiness check
  GET /api/market-now        — SaaS-safe Market Now payload (validated)
  GET /api/mobile/now        — Operational snapshot, broker state stripped
  GET /api/mobile/why        — Macro drivers and theme transmission
  GET /api/mobile/alpha      — Intelligence candidates, last Apex read
  GET /api/mobile/portfolio  — Intelligence-only placeholder (no broker data)

No mutation routes.
No broker state routes.
No execution routes.
No raw score or order ID routes.

Start (development):
  DECIFER_RUNTIME_MODE=intelligence_cloud python3 intelligence_api.py

Start (production — via gunicorn):
  DECIFER_RUNTIME_MODE=intelligence_cloud \
  gunicorn intelligence_api:app --bind 0.0.0.0:8000 --workers 2 --timeout 30

The app reads env vars at import time. Set DECIFER_RUNTIME_MODE=intelligence_cloud
before starting — the import of runtime_config enforces this.
"""
from __future__ import annotations

import json as _json
import logging
import os
import time
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

# runtime_config is read at import time — ensures intelligence_cloud mode is enforced
# before any route handler runs.
import runtime_config
from market_now_builder import get_market_now_dict
from market_data_provider import get_movers, get_news, get_tape
from mobile_api import (
    build_alpha_payload,
    build_now_payload,
    build_why_payload,
)
from saas_intelligence_output import SaaSPayloadValidationError, validate_customer_payload
from theme_graph_api import bp as theme_graph_bp
from v1_api import v1_bp
from options_flow_api import bp as options_flow_bp
from v1_drivers_api import v1_drivers_bp

log = logging.getLogger("decifer.intelligence_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
app.register_blueprint(theme_graph_bp)
app.register_blueprint(v1_bp)
app.register_blueprint(options_flow_bp)
app.register_blueprint(v1_drivers_bp)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Start-up guard: fail loudly if accidentally started outside intelligence_cloud
# ---------------------------------------------------------------------------

_RUNTIME_MODE = runtime_config.runtime_mode

if _RUNTIME_MODE != runtime_config.MODE_INTELLIGENCE_CLOUD:
    log.warning(
        "intelligence_api started with DECIFER_RUNTIME_MODE=%r — "
        "expected 'intelligence_cloud'. Execution guards are still active but "
        "this service is intended for intelligence_cloud deployments only.",
        _RUNTIME_MODE,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CORS_ORIGIN = os.environ.get("INTELLIGENCE_API_CORS_ORIGIN", "*")
_CORS_METHODS = "GET, OPTIONS"
_CORS_HEADERS = "Content-Type, Cache-Control, Origin, Accept"
_CORS_MAX_AGE = "3600"
_API_VERSION = "1.0"


def _json_response(data: dict, status: int = 200) -> Response:
    r = jsonify(data)
    r.status_code = status
    r.headers["X-Decifer-Runtime-Mode"] = _RUNTIME_MODE
    r.headers["X-Decifer-API-Version"] = _API_VERSION
    return r


@app.after_request
def _apply_cors(response: Response) -> Response:
    """Add CORS and cache headers to every response.

    CORS headers allow browser fetches from Vercel (simple GET — no preflight).
    Cache-Control: no-store prevents browsers from caching intelligence payloads.
    """
    response.headers["Access-Control-Allow-Origin"] = _CORS_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
    response.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS
    response.headers["Access-Control-Max-Age"] = _CORS_MAX_AGE
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _error(message: str, status: int = 500) -> Response:
    return _json_response({"status": "error", "message": message}, status=status)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# Execution-sensitive fields that must never appear in mobile/now cloud response.
_BROKER_STATE_FIELDS = frozenset({
    "portfolio_value",
    "daily_pnl",
})


def _strip_broker_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove broker-state fields from a mobile/now payload before cloud response."""
    return {k: v for k, v in payload.items() if k not in _BROKER_STATE_FIELDS}


# ---------------------------------------------------------------------------
# Health freshness helper (Sprint M6)
# ---------------------------------------------------------------------------

# Thresholds in hours: ≤ ok_hours → "ok", ok_hours < x ≤ stale_hours → "degraded", > stale_hours → "stale"
_HEALTH_FRESHNESS_OK_HOURS: float = 2.0
_HEALTH_FRESHNESS_STALE_HOURS: float = 6.0

# Key artefacts tracked for freshness. Labels are exposed in warnings — never expose paths.
_HEALTH_ARTIFACTS: dict[str, str] = {
    "Market pipeline manifest":  "data/live/current_manifest.json",
    "Market drivers data":       "data/intelligence/live_driver_state.json",
    "Theme activation data":     "data/intelligence/theme_activation.json",
}


def _build_health_freshness() -> dict[str, Any]:
    """
    Read artefact timestamps and return freshness fields for /health.
    Never exposes internal file paths, secrets, broker details, or stack traces.
    """
    now = time.time()
    warnings: list[str] = []
    ages: list[float] = []

    # latest_pipeline_artifact_timestamp = most recent mtime across tracked artefacts
    latest_mtime: float | None = None
    for label, rel in _HEALTH_ARTIFACTS.items():
        path = os.path.join(_BASE_DIR, rel)
        if not os.path.exists(path):
            warnings.append(f"{label} not available")
            continue
        try:
            mt = os.path.getmtime(path)
            age_h = (now - mt) / 3600
            ages.append(age_h)
            if latest_mtime is None or mt > latest_mtime:
                latest_mtime = mt
            if age_h > _HEALTH_FRESHNESS_STALE_HOURS:
                warnings.append(f"{label} is stale ({age_h:.1f}h old)")
        except Exception:
            warnings.append(f"{label} timestamp unreadable")

    # latest_market_now_timestamp from manifest published_at (informational)
    latest_market_now: str = "unknown"
    try:
        manifest_path = os.path.join(_BASE_DIR, "data/live/current_manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                manifest_data = _json.load(f)
            pa = manifest_data.get("published_at", "")
            if pa:
                latest_market_now = str(pa)
    except Exception:
        pass

    # latest_pipeline_artifact_timestamp
    latest_pipeline: str = "unknown"
    if latest_mtime is not None:
        latest_pipeline = datetime.fromtimestamp(latest_mtime, tz=UTC).isoformat()

    # data_freshness_status based on manifest mtime (primary freshness indicator)
    manifest_path = os.path.join(_BASE_DIR, "data/live/current_manifest.json")
    if not os.path.exists(manifest_path):
        freshness_status = "stale"
    else:
        try:
            manifest_age_h = (now - os.path.getmtime(manifest_path)) / 3600
            if manifest_age_h <= _HEALTH_FRESHNESS_OK_HOURS:
                freshness_status = "ok"
            elif manifest_age_h <= _HEALTH_FRESHNESS_STALE_HOURS:
                freshness_status = "degraded"
            else:
                freshness_status = "stale"
        except Exception:
            freshness_status = "stale"

    return {
        "data_freshness_status": freshness_status,
        "latest_market_now_timestamp": latest_market_now,
        "latest_pipeline_artifact_timestamp": latest_pipeline,
        "degraded_artifact_warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health() -> Response:
    """
    Liveness and readiness check. Returns 200 if the service is running.

    Reports runtime mode, execution guard state, customer output mode,
    data freshness status, and artifact timestamps.

    Does not expose secrets, provider keys, broker state, internal file paths,
    stack traces, or private runtime internals.
    """
    execution_blocked = not runtime_config.is_execution_enabled()
    freshness = _build_health_freshness()
    return _json_response({
        "status": "ok",
        "service": "decifer-intelligence-api",
        "runtime_mode": _RUNTIME_MODE,
        "execution_blocked": execution_blocked,
        "customer_output_mode": runtime_config.customer_output_mode,
        "data_freshness_status": freshness["data_freshness_status"],
        "latest_market_now_timestamp": freshness["latest_market_now_timestamp"],
        "latest_pipeline_artifact_timestamp": freshness["latest_pipeline_artifact_timestamp"],
        "degraded_artifact_warnings": freshness["degraded_artifact_warnings"],
        "ts": _now_iso(),
    })


@app.route("/api/market-now", methods=["GET", "OPTIONS"])
def market_now() -> Response:
    """
    SaaS-safe Market Now intelligence payload.

    Validated against saas_intelligence_output._ALLOWED_FIELDS before response.
    Fails closed if validation fails — returns 503 rather than leaking
    unexpected fields.

    Includes: market_regime_label, plain_english_summary, key_drivers,
              active_themes, opportunity_explanations, risk_notes,
              what_to_watch, freshness_timestamp, confidence_label,
              source_category_labels, data_entitlement_note.

    Excludes: all broker state, raw prices, execution signals, internal scores.
    """
    if request.method == "OPTIONS":
        return Response(status=204)

    try:
        payload = get_market_now_dict()
    except Exception as exc:
        log.error("/api/market-now: builder failed — %s", exc)
        return _error("Intelligence data temporarily unavailable. Retry in 60 seconds.", 503)

    try:
        validate_customer_payload(payload)
    except SaaSPayloadValidationError as exc:
        log.error("/api/market-now: payload validation failed — %s", exc)
        return _error("Internal payload validation failed. This is a bug.", 503)

    return _json_response({
        "status": "ok",
        "generated_at": _now_iso(),
        **payload,
    })


@app.route("/api/mobile/now")
def mobile_now() -> Response:
    """
    Operational market snapshot — intelligence-only, broker state stripped.

    Includes: market mood, session, active drivers, candidate count, bot status.
    Excludes: portfolio_value, daily_pnl (broker state — not available in cloud).
    """
    try:
        raw = build_now_payload({})  # empty dash — no broker state in cloud
        safe = _strip_broker_fields(raw)
        safe["cloud_mode"] = True
        safe["note"] = "Portfolio metrics not available in intelligence cloud mode."
        return _json_response(safe)
    except Exception as exc:
        log.error("/api/mobile/now: %s", exc)
        return _error("Market snapshot temporarily unavailable.", 503)


@app.route("/api/mobile/why")
def mobile_why() -> Response:
    """
    Macro drivers and theme transmission — pure intelligence, no broker state.

    Reads: live_driver_state.json, theme_activation.json, apex_conversation_log.jsonl.
    """
    try:
        return _json_response(build_why_payload())
    except Exception as exc:
        log.error("/api/mobile/why: %s", exc)
        return _error("Driver intelligence temporarily unavailable.", 503)


@app.route("/api/mobile/alpha")
def mobile_alpha() -> Response:
    """
    Intelligence candidates and last Apex market read.

    Reads: economic_candidate_feed.json, apex_conversation_log.jsonl.
    No prices, no scores, no execution signals.
    """
    try:
        return _json_response(build_alpha_payload())
    except Exception as exc:
        log.error("/api/mobile/alpha: %s", exc)
        return _error("Alpha intelligence temporarily unavailable.", 503)


@app.route("/api/mobile/portfolio")
def mobile_portfolio() -> Response:
    """
    Portfolio route — intelligence-only placeholder in cloud mode.

    Portfolio data (positions, account value, P&L) is only available on the
    execution node (Mac paper bot). This cloud deployment provides intelligence
    only.  No broker state is available or returned.
    """
    return _json_response({
        "ts": _now_iso(),
        "status": "intelligence_cloud",
        "note": (
            "Live portfolio data is available on the execution node only. "
            "This cloud deployment provides market intelligence, not execution state."
        ),
        "positions": [],
        "portfolio_summary": {
            "intelligence_cloud_mode": True,
            "positions_available": False,
        },
    })


# ---------------------------------------------------------------------------
# Intelligence universe endpoint — full operational symbol roster
# ---------------------------------------------------------------------------

# Intelligence reference files are baked into /app/intelligence_ref/ in the Docker image
# (outside the /app/data/ named-volume mount) so they remain accessible in production.
# Fall back to data/intelligence/ for local dev where no volume overlay exists.
_INTELLIGENCE_REF_DIR = os.path.join(_BASE_DIR, "intelligence_ref")
_INTELLIGENCE_DATA_DIR = os.path.join(_BASE_DIR, "data", "intelligence")
_ROSTER_PATH = os.path.join(
    _INTELLIGENCE_REF_DIR if os.path.isdir(_INTELLIGENCE_REF_DIR) else _INTELLIGENCE_DATA_DIR,
    "thematic_roster.json",
)
_TAXONOMY_PATH = os.path.join(
    _INTELLIGENCE_REF_DIR if os.path.isdir(_INTELLIGENCE_REF_DIR) else _INTELLIGENCE_DATA_DIR,
    "theme_taxonomy.json",
)


def _load_roster_universe() -> list[dict[str, str]]:
    """Return a clean projection of the operational symbol roster.

    Reads thematic_roster.json (symbols) and theme_taxonomy.json (theme names).
    Returns only: symbol, theme_id, theme_label, role.
    All other roster fields (route_bias, max_candidates, notes, activation_drivers,
    risk_flags, etc.) are intentionally excluded — they reveal operational strategy.
    Returns [] on any read failure (fail-closed).
    """
    try:
        with open(_ROSTER_PATH, encoding="utf-8") as f:
            roster = _json.load(f)
        with open(_TAXONOMY_PATH, encoding="utf-8") as f:
            taxonomy = _json.load(f)
    except Exception as exc:
        log.warning("_load_roster_universe: could not read roster files — %s", exc)
        return []

    _reg_themes = _load_label_registry().get("themes", {})
    theme_names: dict[str, str] = {
        t["theme_id"]: _reg_themes.get(t["theme_id"]) or t["name"]
        for t in taxonomy.get("themes", [])
        if isinstance(t, dict) and "theme_id" in t and "name" in t
    }

    seen: set[str] = set()
    items: list[dict[str, str]] = []
    for entry in roster.get("rosters", []):
        if not isinstance(entry, dict):
            continue
        theme_id = entry.get("theme_id", "")
        theme_label = theme_names.get(theme_id, theme_id)
        for symbol in entry.get("core_symbols", []):
            if isinstance(symbol, str) and symbol not in seen:
                seen.add(symbol)
                items.append({"symbol": symbol, "theme_id": theme_id,
                               "theme_label": theme_label, "role": "core"})
        for symbol in entry.get("etf_proxies", []):
            if isinstance(symbol, str) and symbol not in seen:
                seen.add(symbol)
                items.append({"symbol": symbol, "theme_id": theme_id,
                               "theme_label": theme_label, "role": "etf_proxy"})
    return items


@app.route("/api/intelligence/universe", methods=["GET", "OPTIONS"])
def intelligence_universe() -> Response:
    """Full Decifer intelligence universe — all curated symbols across 23 operational themes.

    Returns symbol, theme_id, theme_label, and role (core | etf_proxy) for every
    symbol in the operational roster. Used by the mobile app to filter earnings
    and analyst moves to Decifer-tracked names.

    Safe fields only — no route_bias, max_candidates, notes, activation_drivers,
    risk_flags, or any execution/broker data.
    """
    if request.method == "OPTIONS":
        return Response(status=204)

    universe = _load_roster_universe()
    payload = {
        "theme_graph_universe": universe,
        "total": len(universe),
        "ts": _now_iso(),
    }
    return _json_response(payload)


# ---------------------------------------------------------------------------
# Label registry — single source of truth for human-readable labels
# ---------------------------------------------------------------------------

_LABEL_REGISTRY_PATH = Path(__file__).parent / "data" / "intelligence" / "label_registry.json"
_label_registry_cache: dict | None = None

def _load_label_registry() -> dict:
    global _label_registry_cache
    if _label_registry_cache is None:
        try:
            _label_registry_cache = _json.loads(_LABEL_REGISTRY_PATH.read_text())
        except Exception as exc:
            log.warning("label_registry load failed: %s", exc)
            _label_registry_cache = {}
    return _label_registry_cache


@app.route("/api/labels", methods=["GET", "OPTIONS"])
def label_registry() -> Response:
    """Single source of truth for all human-readable labels (themes, drivers, buckets).

    All products — map, mobile, and any future surface — should read from this
    endpoint rather than hardcoding labels locally.
    """
    if request.method == "OPTIONS":
        return Response(status=204)
    registry = _load_label_registry()
    return _json_response({**registry, "ts": _now_iso()})


# ---------------------------------------------------------------------------
# Market data endpoints — generic FMP data for customer surfaces
# Shadow mode: these endpoints exist but mobile is not yet wired to them.
# ---------------------------------------------------------------------------

@app.route("/api/market-data/movers", methods=["GET", "OPTIONS"])
def market_data_movers() -> Response:
    """Top 5 gainers and losers. FMP-backed, 5-min cache. Generic market view."""
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        return _json_response(get_movers())
    except Exception as exc:
        log.error("/api/market-data/movers: %s", exc)
        return _error("Market movers temporarily unavailable.", 503)


@app.route("/api/market-data/news", methods=["GET", "OPTIONS"])
def market_data_news() -> Response:
    """Up to 15 deduplicated news items. FMP-backed, 5-min cache."""
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        return _json_response(get_news())
    except Exception as exc:
        log.error("/api/market-data/news: %s", exc)
        return _error("Market news temporarily unavailable.", 503)


@app.route("/api/market-data/tape", methods=["GET", "OPTIONS"])
def market_data_tape() -> Response:
    """ETF tape (SPY/QQQ/IWM/TLT/GLD/USO/UUP) + VIX. FMP-backed, 5-min cache."""
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        return _json_response(get_tape())
    except Exception as exc:
        log.error("/api/market-data/tape: %s", exc)
        return _error("Market tape temporarily unavailable.", 503)


# ---------------------------------------------------------------------------
# Counter-Thesis Intelligence
# ---------------------------------------------------------------------------

@app.route("/api/counter-thesis", methods=["GET", "OPTIONS"])
def counter_thesis() -> Response:
    """
    Structural counter-thesis analysis for active drivers.

    Returns curated counter-thesis claims verified against FMP fundamental data.
    View-only intelligence — not connected to execution or scoring.

    Query params:
        fmp=false   — skip FMP verification, return library entries only (fast)
    """
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        from counter_thesis_engine import (
            build_counter_thesis_dict,
            load_cached_counter_thesis,
        )
        use_fmp = request.args.get("fmp", "true").lower() != "false"
        if use_fmp:
            # Serve from cache (written by scheduled FMP refresh). Fast path.
            # Falls back to fmp=false (curated) if cache is missing or stale.
            data = load_cached_counter_thesis() or build_counter_thesis_dict(use_fmp=False)
        else:
            data = build_counter_thesis_dict(use_fmp=False)
        return _json_response(data)
    except Exception as exc:
        log.error("/api/counter-thesis: %s", exc)
        return _error("Counter-thesis analysis temporarily unavailable.", 503)


# ---------------------------------------------------------------------------
# Conviction system endpoints
# ---------------------------------------------------------------------------

@app.route("/api/conviction/universe", methods=["GET", "OPTIONS"])
def conviction_universe_route() -> Response:
    """
    All symbols with their conviction score, tier, zone, and top dimension signal.

    Reads from conviction_cache.get_all() and conviction_universe.load().
    Returns tradeable_count (READY zone) and waiting_count (WAITING_ROOM zone).
    """
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        from conviction_cache import get_all as _cc_get_all
        from conviction_universe import load as _cu_load
        cache_entries = _cc_get_all()
        universe_meta = _cu_load()
        symbols = []
        tradeable_count = 0
        waiting_count = 0
        for entry in cache_entries.values():
            zone = entry.get("zone", "") if isinstance(entry, dict) else ""
            if zone == "READY":
                tradeable_count += 1
            elif zone == "WAITING_ROOM":
                waiting_count += 1
            dims = entry.get("dimensions", {})
            dims_summary = {k: v.get("raw_pts") if isinstance(v, dict) else v
                            for k, v in dims.items()} if isinstance(dims, dict) else {}
            symbols.append({
                "symbol": entry.get("symbol"),
                "composite": entry.get("composite"),
                "tier": entry.get("tier"),
                "zone": zone,
                "dimensions_summary": dims_summary,
                "ts": entry.get("ts"),
            })
        return _json_response({
            "symbols": symbols,
            "tradeable_count": tradeable_count,
            "waiting_count": waiting_count,
            "ts": _now_iso(),
        })
    except Exception as exc:
        log.error("/api/conviction/universe: %s", exc)
        return _error("Conviction universe temporarily unavailable.", 503)


@app.route("/api/conviction/symbol/<ticker>", methods=["GET", "OPTIONS"])
def conviction_symbol(ticker: str) -> Response:
    """
    Full conviction score for one symbol including all dimension breakdowns.

    404 if symbol is not in the conviction cache.
    """
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        from conviction_cache import get as _cc_get
        entry = _cc_get(ticker.upper())
        if entry is None:
            return _error(f"Symbol {ticker.upper()} not in conviction cache.", 404)
        return _json_response({
            "symbol": entry.get("symbol"),
            "composite": entry.get("composite"),
            "tier": entry.get("tier"),
            "zone": entry.get("zone"),
            "dimensions": entry.get("dimensions", {}),
            "ts": entry.get("ts"),
        })
    except Exception as exc:
        log.error("/api/conviction/symbol/%s: %s", ticker, exc)
        return _error("Conviction score temporarily unavailable.", 503)


@app.route("/api/conviction/waiting-room", methods=["GET", "OPTIONS"])
def conviction_waiting_room() -> Response:
    """
    Symbols in WAITING_ROOM zone with their scores and weakest dimension.
    """
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        from conviction_cache import get_all as _cc_get_all
        cache_entries = _cc_get_all()
        waiting = []
        for entry in cache_entries:
            if entry.get("zone") != "WAITING_ROOM":
                continue
            dims = entry.get("dimensions", {})
            weakest_dim = None
            weakest_pts = None
            if isinstance(dims, dict):
                for dim, val in dims.items():
                    pts = val.get("raw_pts") if isinstance(val, dict) else val
                    if pts is not None and (weakest_pts is None or pts < weakest_pts):
                        weakest_pts = pts
                        weakest_dim = dim
            waiting.append({
                "symbol": entry.get("symbol"),
                "composite": entry.get("composite"),
                "tier": entry.get("tier"),
                "zone": entry.get("zone"),
                "weakest_dimension": weakest_dim,
                "weakest_dimension_pts": weakest_pts,
                "ts": entry.get("ts"),
            })
        return _json_response({
            "symbols": waiting,
            "count": len(waiting),
            "ts": _now_iso(),
        })
    except Exception as exc:
        log.error("/api/conviction/waiting-room: %s", exc)
        return _error("Conviction waiting room temporarily unavailable.", 503)


@app.route("/api/conviction/rotation-flags", methods=["GET", "OPTIONS"])
def conviction_rotation_flags() -> Response:
    """
    Symbols flagged for rotation out with reason.

    Reads from conviction_universe.get_rotation_flags().
    """
    if request.method == "OPTIONS":
        return Response(status=204)
    try:
        from conviction_universe import get_rotation_flags as _cu_flags
        flags = _cu_flags()
        return _json_response({
            "flags": flags,
            "count": len(flags),
            "ts": _now_iso(),
        })
    except Exception as exc:
        log.error("/api/conviction/rotation-flags: %s", exc)
        return _error("Conviction rotation flags temporarily unavailable.", 503)


# ---------------------------------------------------------------------------
# Root view — HTML landing page for intelligence.decifertrading.com
# ---------------------------------------------------------------------------

_COUNTER_THESIS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Decifer Intelligence — Structural Analysis</title>
<style>
  :root {
    --bg: #0a0a0a;
    --surface: #111111;
    --surface2: #181818;
    --border: #222222;
    --text: #e8e8e8;
    --muted: #777;
    --orange: #e87d2e;
    --orange-dim: #b85f1e;
    --green: #2ecc71;
    --red: #e74c3c;
    --yellow: #f1c40f;
    --blue: #3498db;
    --radius: 8px;
    --font: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', sans-serif;
    --mono: 'SF Mono', 'Fira Code', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    line-height: 1.6;
    min-height: 100vh;
  }
  header {
    border-bottom: 1px solid var(--border);
    padding: 18px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    background: var(--bg);
    z-index: 10;
  }
  .logo {
    font-size: 13px;
    font-weight: 600;
    color: var(--orange);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .logo span { color: var(--muted); font-weight: 400; }
  .header-right {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 12px;
    color: var(--muted);
  }
  .status-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  main { max-width: 900px; margin: 0 auto; padding: 32px 24px 64px; }
  .page-title {
    font-size: 22px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 6px;
  }
  .page-subtitle {
    color: var(--muted);
    font-size: 13px;
    margin-bottom: 32px;
  }
  .section-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 14px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .section-label::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }
  .active-drivers {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 32px;
  }
  .driver-chip {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    color: var(--orange);
    font-weight: 500;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 16px;
    overflow: hidden;
    transition: border-color 0.15s;
  }
  .card:hover { border-color: #333; }
  .card-header {
    padding: 18px 20px 14px;
    cursor: pointer;
    user-select: none;
  }
  .card-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
  }
  .card-theme {
    font-size: 11px;
    color: var(--orange);
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 6px;
  }
  .card-claim {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    line-height: 1.4;
  }
  .badge {
    flex-shrink: 0;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 9px;
    border-radius: 4px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .badge-verified { background: rgba(231,76,60,0.15); color: #e74c3c; border: 1px solid rgba(231,76,60,0.3); }
  .badge-partial { background: rgba(241,196,15,0.12); color: #f1c40f; border: 1px solid rgba(241,196,15,0.25); }
  .badge-unverified { background: rgba(119,119,119,0.12); color: #777; border: 1px solid rgba(119,119,119,0.2); }
  .verdict-line {
    font-size: 12px;
    color: var(--muted);
    margin-top: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .confidence-bar {
    height: 3px;
    background: var(--border);
    border-radius: 2px;
    width: 60px;
    overflow: hidden;
  }
  .confidence-fill {
    height: 100%;
    border-radius: 2px;
    background: var(--orange);
  }
  .card-body {
    padding: 0 20px 20px;
    display: none;
  }
  .card-body.open { display: block; }
  .card-divider {
    height: 1px;
    background: var(--border);
    margin: 0 0 18px;
  }
  .plain-english {
    font-size: 14px;
    color: #ccc;
    line-height: 1.7;
    margin-bottom: 18px;
  }
  .evidence-section {
    margin-bottom: 18px;
  }
  .evidence-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 10px;
  }
  .evidence-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px;
  }
  .evidence-item {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 14px;
  }
  .evidence-symbol {
    font-size: 12px;
    font-weight: 700;
    color: var(--text);
    font-family: var(--mono);
    margin-bottom: 2px;
  }
  .evidence-metric {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .evidence-value {
    font-size: 18px;
    font-weight: 700;
    font-family: var(--mono);
    margin-bottom: 4px;
  }
  .evidence-value.supports { color: var(--green); }
  .evidence-value.counter { color: var(--red); }
  .evidence-value.neutral { color: var(--text); }
  .evidence-interp {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.4;
  }
  .two-col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-bottom: 16px;
  }
  @media (max-width: 600px) { .two-col { grid-template-columns: 1fr; } }
  .bull-box {
    background: rgba(46,204,113,0.05);
    border: 1px solid rgba(46,204,113,0.15);
    border-radius: 6px;
    padding: 14px;
  }
  .bull-box-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--green);
    margin-bottom: 6px;
  }
  .bull-box-text { font-size: 13px; color: #bbb; line-height: 1.6; }
  .source-box {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px;
  }
  .source-box-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .source-box-text { font-size: 13px; color: #999; }
  .no-evidence {
    color: var(--muted);
    font-size: 13px;
    font-style: italic;
    padding: 10px 0;
  }
  .dormant-section { opacity: 0.55; }
  .dormant-section .section-label { color: #555; }
  .toggle-icon {
    font-size: 12px;
    color: var(--muted);
    transition: transform 0.2s;
    margin-left: 8px;
  }
  .toggle-icon.open { transform: rotate(180deg); }
  .meta-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 28px;
    padding: 12px 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .meta-bar .sep { color: var(--border); }
  .loading {
    text-align: center;
    padding: 80px 0;
    color: var(--muted);
    font-size: 14px;
  }
  .loading-dot { animation: pulse 1s infinite; }
  footer {
    border-top: 1px solid var(--border);
    padding: 16px 24px;
    font-size: 11px;
    color: #444;
    text-align: center;
  }
  .api-link {
    color: var(--orange-dim);
    text-decoration: none;
    font-family: var(--mono);
    font-size: 11px;
  }
  .api-link:hover { color: var(--orange); }
</style>
</head>
<body>
<header>
  <div class="logo">Decifer <span>/ Intelligence</span></div>
  <div class="header-right">
    <div class="status-dot"></div>
    <span id="freshness-label">Loading...</span>
    <span class="sep">|</span>
    <a href="/api/counter-thesis?fmp=false" class="api-link">/api/counter-thesis</a>
  </div>
</header>

<main>
  <div class="page-title">Structural Analysis</div>
  <div class="page-subtitle">Counter-thesis intelligence for active market drivers — verified against FMP fundamental data</div>

  <div id="loading" class="loading">
    <span class="loading-dot">Fetching intelligence...</span>
  </div>

  <div id="content" style="display:none">
    <div class="section-label">Active Market Drivers</div>
    <div id="active-drivers" class="active-drivers"></div>

    <div id="meta-bar" class="meta-bar"></div>

    <div class="section-label">Active Structural Conflicts</div>
    <div id="structural-conflicts"></div>

    <div class="dormant-section" style="margin-top:40px">
      <div class="section-label">Dormant (Driver Not Active)</div>
      <div id="dormant-conflicts"></div>
    </div>
  </div>

  <div id="error-state" style="display:none; text-align:center; padding:80px 0; color:#555;">
    Intelligence API unavailable. Check server status at <a href="/health" style="color:#e87d2e">/health</a>
  </div>
</main>

<footer>
  View-only intelligence &nbsp;·&nbsp; Not connected to execution or scoring &nbsp;·&nbsp;
  <a href="/api/counter-thesis" class="api-link">/api/counter-thesis</a> &nbsp;|&nbsp;
  <a href="/health" class="api-link">/health</a>
</footer>

<script>
const STATUS_LABELS = {
  verified: ['VERIFIED', 'badge-verified'],
  partial: ['PARTIAL', 'badge-partial'],
  unverified: ['UNVERIFIED', 'badge-unverified'],
  refuted: ['REFUTED', 'badge-unverified'],
};

function formatTs(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleString('en-US', {timeZone:'America/New_York', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) + ' ET';
  } catch { return iso; }
}

function formatDriverId(id) {
  return id.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
}

function confidenceColor(c) {
  if (c >= 0.7) return '#e74c3c';
  if (c >= 0.45) return '#f1c40f';
  return '#555';
}

function renderEvidence(evidence) {
  if (!evidence || !evidence.length) {
    return '<div class="no-evidence">No FMP data available — claim not yet quantitatively verified.</div>';
  }
  const items = evidence.map(e => {
    const cls = e.supports_thesis === false ? 'counter' : e.supports_thesis === true ? 'supports' : 'neutral';
    const val = e.value !== null ? `${e.value}${e.unit}` : 'N/A';
    return `<div class="evidence-item">
      <div class="evidence-symbol">${e.symbol}</div>
      <div class="evidence-metric">${e.metric}</div>
      <div class="evidence-value ${cls}">${val}</div>
      <div class="evidence-interp">${e.interpretation}</div>
    </div>`;
  }).join('');
  return `<div class="evidence-grid">${items}</div>`;
}

function renderCard(item, idx) {
  const [badgeText, badgeClass] = STATUS_LABELS[item.verification_status] || ['UNKNOWN', 'badge-unverified'];
  const confPct = Math.round(item.confidence * 100);
  const bodyId = `body-${idx}`;

  return `<div class="card">
    <div class="card-header" onclick="toggleCard('${bodyId}', this)">
      <div class="card-top">
        <div>
          <div class="card-theme">${item.theme_label}</div>
          <div class="card-claim">${item.claim}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
          <span class="badge ${badgeClass}">${badgeText}</span>
          <span class="toggle-icon" id="icon-${bodyId}">▼</span>
        </div>
      </div>
      <div class="verdict-line">
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${confPct}%;background:${confidenceColor(item.confidence)}"></div>
        </div>
        <span>${confPct}% confidence</span>
        <span style="margin-left:4px">·</span>
        <span>${item.verdict_summary}</span>
      </div>
    </div>
    <div class="card-body" id="${bodyId}">
      <div class="card-divider"></div>
      <div class="plain-english">${item.plain_english}</div>

      ${item.evidence && item.evidence.length ? `
      <div class="evidence-section">
        <div class="evidence-label">FMP Verification Data</div>
        ${renderEvidence(item.evidence)}
      </div>` : ''}

      <div class="two-col">
        <div class="bull-box">
          <div class="bull-box-label">Bull Counter</div>
          <div class="bull-box-text">${item.bull_counter || 'No bull counter defined.'}</div>
        </div>
        <div class="source-box">
          <div class="source-box-label">Source</div>
          <div class="source-box-text">${item.source_attribution}</div>
        </div>
      </div>
    </div>
  </div>`;
}

function toggleCard(bodyId, header) {
  const body = document.getElementById(bodyId);
  const icon = document.getElementById('icon-' + bodyId);
  const isOpen = body.classList.contains('open');
  body.classList.toggle('open', !isOpen);
  icon.classList.toggle('open', !isOpen);
}

async function loadData() {
  try {
    const r = await fetch('/api/counter-thesis');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    document.getElementById('loading').style.display = 'none';
    document.getElementById('content').style.display = 'block';

    // Active drivers
    const driversEl = document.getElementById('active-drivers');
    if (data.active_drivers && data.active_drivers.length) {
      driversEl.innerHTML = data.active_drivers.map(d =>
        `<span class="driver-chip">${formatDriverId(d)}</span>`
      ).join('');
    } else {
      driversEl.innerHTML = '<span style="color:#555;font-size:13px">No active drivers detected</span>';
    }

    // Meta bar
    document.getElementById('meta-bar').innerHTML = `
      <span>Generated: ${formatTs(data.generated_at)}</span>
      <span class="sep">·</span>
      <span>Data: <strong style="color:${data.data_freshness === 'live' ? '#2ecc71' : data.data_freshness === 'curated' ? '#aaa' : '#f1c40f'}">${data.data_freshness}</strong></span>
      <span class="sep">·</span>
      <span>${(data.structural_conflicts || []).length} active conflicts · ${(data.dormant_conflicts || []).length} dormant</span>
      <span class="sep">·</span>
      <span style="color:#555">${data.note || ''}</span>
    `;
    document.getElementById('freshness-label').textContent = `Data: ${data.data_freshness}`;

    // Structural conflicts
    const scEl = document.getElementById('structural-conflicts');
    if (data.structural_conflicts && data.structural_conflicts.length) {
      scEl.innerHTML = data.structural_conflicts.map((item, i) => renderCard(item, i)).join('');
    } else {
      scEl.innerHTML = '<div style="color:#555;font-size:13px;padding:20px 0">No structural conflicts for currently active drivers.</div>';
    }

    // Dormant
    const dcEl = document.getElementById('dormant-conflicts');
    if (data.dormant_conflicts && data.dormant_conflicts.length) {
      dcEl.innerHTML = data.dormant_conflicts.map((item, i) => renderCard(item, 1000 + i)).join('');
    }

  } catch (err) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('error-state').style.display = 'block';
    console.error('Counter-thesis fetch failed:', err);
  }
}

loadData();
</script>
</body>
</html>"""


@app.route("/", methods=["GET"])
def root_view() -> Response:
    """
    HTML landing page at intelligence.decifertrading.com/

    Renders the counter-thesis structural analysis view.
    Fetches /api/counter-thesis client-side to keep the page fast.
    """
    return Response(_COUNTER_THESIS_HTML, status=200, mimetype="text/html")


@app.route("/view", methods=["GET"])
def view_redirect() -> Response:
    """Alias for /"""
    return Response(_COUNTER_THESIS_HTML, status=200, mimetype="text/html")


# ---------------------------------------------------------------------------
# Catch-all for undefined routes — never expose 404 with stack traces
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_exc: Exception) -> Response:
    return _json_response({"status": "not_found", "message": "Route not found."}, 404)


@app.errorhandler(405)
def method_not_allowed(_exc: Exception) -> Response:
    return _json_response({"status": "error", "message": "Method not allowed. All routes are GET-only."}, 405)


@app.errorhandler(500)
def internal_error(exc: Exception) -> Response:
    log.error("Unhandled exception: %s", exc)
    return _json_response({"status": "error", "message": "Internal server error."}, 500)


# ---------------------------------------------------------------------------
# Startup conviction cache warm-up
# ---------------------------------------------------------------------------

def _warmup_conviction_cache() -> None:
    """Background warm-up: rescore full TTG universe on startup."""
    import threading
    def _run():
        try:
            import conviction_cache, conviction_engine
            from pathlib import Path
            import json as _j
            data_dir = Path(__file__).parent / "data" / "intelligence"
            raw = _j.loads((data_dir / "theme_graph" / "symbol_exposures.json").read_text())
            symbols = list({
                e.get("symbol","").upper() for e in raw.get("exposures",[])
                if e.get("status") == "active" and e.get("symbol")
            })
            if symbols:
                import logging
                logging.getLogger("decifer.conviction").info(
                    "Startup warm-up: rescoring %d symbols", len(symbols))
                conviction_cache.refresh_all(symbols)
        except Exception as exc:
            import logging
            logging.getLogger("decifer.conviction").warning("Warm-up failed: %s", exc)
    threading.Thread(target=_run, daemon=True, name="conviction-warmup").start()


_warmup_conviction_cache()


# ---------------------------------------------------------------------------
# Macro Event Layer routes
# ---------------------------------------------------------------------------

@app.route("/api/intelligence/macro-events", methods=["GET", "OPTIONS"])
def macro_events() -> Response:
    """
    Recent structured macro events from the macro event layer.

    Query params:
      hours   — how many hours back to fetch (default 24, max 336)
      driver  — filter to events implicating a specific driver_id
      domain  — filter to events affecting a specific domain
    """
    if request.method == "OPTIONS":
        return _apply_cors(_json_response({}))
    try:
        from macro_event_layer import get_recent_events, get_events_for_driver

        hours_raw = request.args.get("hours", "24")
        try:
            hours = min(float(hours_raw), 336.0)
        except (ValueError, TypeError):
            hours = 24.0

        driver_filter = request.args.get("driver", "").strip() or None
        domain_filter = request.args.get("domain", "").strip() or None

        if driver_filter:
            events = get_events_for_driver(driver_filter)
        else:
            events = get_recent_events(within_hours=hours)

        if domain_filter:
            events = [e for e in events if domain_filter in (e.get("affected_domains") or [])]

        safe_events = []
        for ev in events:
            safe_events.append({
                "event_id": ev.get("event_id", ""),
                "recorded_at": ev.get("recorded_at", ""),
                "published_at": ev.get("published_at", ""),
                "expires_at": ev.get("expires_at", ""),
                "source": ev.get("source", ""),
                "headline": ev.get("headline", ""),
                "event_type": ev.get("event_type", ""),
                "event_summary": ev.get("event_summary", ""),
                "direction_of_risk": ev.get("direction_of_risk", "neutral"),
                "drivers_implicated": ev.get("drivers_implicated", []),
                "theme_impacts": ev.get("theme_impacts", []),
                "affected_domains": ev.get("affected_domains", []),
                "price_confirmation_signals": ev.get("price_confirmation_signals", []),
                "confidence": ev.get("confidence", 0.5),
            })

        return _apply_cors(_json_response({
            "macro_events": safe_events,
            "count": len(safe_events),
            "filter_hours": hours,
            "filter_driver": driver_filter,
            "filter_domain": domain_filter,
        }))
    except Exception as exc:
        log.warning("macro_events route failed: %s", exc)
        return _apply_cors(_json_response({"macro_events": [], "count": 0}))


@app.route("/api/intelligence/macro-context", methods=["GET", "OPTIONS"])
def macro_context_route() -> Response:
    """
    Structured macro context summary: drivers backed by events, active domains,
    risk direction. Consumed by the driver resolver and Ask Decifer.
    """
    if request.method == "OPTIONS":
        return _apply_cors(_json_response({}))
    try:
        from macro_event_layer import get_active_context
        ctx = get_active_context()
        safe = {
            "drivers_with_event_backing": {
                drv: [
                    {
                        "event_id": e.get("event_id", ""),
                        "event_type": e.get("event_type", ""),
                        "event_summary": e.get("event_summary", ""),
                        "direction_of_risk": e.get("direction_of_risk", "neutral"),
                        "confidence": e.get("confidence", 0.5),
                        "recorded_at": e.get("recorded_at", ""),
                    }
                    for e in evs
                ]
                for drv, evs in ctx.get("drivers_with_event_backing", {}).items()
            },
            "active_domains": ctx.get("active_domains", []),
            "risk_direction": ctx.get("risk_direction", "neutral"),
            "event_count": len(ctx.get("events", [])),
            "generated_at": ctx.get("generated_at", ""),
        }
        return _apply_cors(_json_response(safe))
    except Exception as exc:
        log.warning("macro_context route failed: %s", exc)
        return _apply_cors(_json_response({"drivers_with_event_backing": {}, "event_count": 0}))


# ---------------------------------------------------------------------------
# Entry point (development only — production uses gunicorn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true")
    log.info("Starting Decifer Intelligence API on port %d (runtime_mode=%s)", port, _RUNTIME_MODE)
    app.run(host="0.0.0.0", port=port, debug=debug)
