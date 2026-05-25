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
from typing import Any

from flask import Flask, Response, jsonify, request

# runtime_config is read at import time — ensures intelligence_cloud mode is enforced
# before any route handler runs.
import runtime_config
from market_now_builder import get_market_now_dict
from mobile_api import (
    build_alpha_payload,
    build_now_payload,
    build_why_payload,
)
from saas_intelligence_output import SaaSPayloadValidationError, validate_customer_payload

log = logging.getLogger("decifer.intelligence_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)

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
    """Add CORS headers to every response — required for browser fetches from Vercel."""
    response.headers["Access-Control-Allow-Origin"] = _CORS_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
    response.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS
    response.headers["Access-Control-Max-Age"] = _CORS_MAX_AGE
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
# Entry point (development only — production uses gunicorn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true")
    log.info("Starting Decifer Intelligence API on port %d (runtime_mode=%s)", port, _RUNTIME_MODE)
    app.run(host="0.0.0.0", port=port, debug=debug)
