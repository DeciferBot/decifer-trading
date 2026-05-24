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

import logging
import os
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from flask import Flask, Response, jsonify

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
_API_VERSION = "1.0"


def _json_response(data: dict, status: int = 200) -> Response:
    r = jsonify(data)
    r.status_code = status
    r.headers["X-Decifer-Runtime-Mode"] = _RUNTIME_MODE
    r.headers["X-Decifer-API-Version"] = _API_VERSION
    r.headers["Access-Control-Allow-Origin"] = _CORS_ORIGIN
    return r


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
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health() -> Response:
    """
    Liveness check. Returns 200 if the service is running.
    Also confirms execution is blocked (intelligence_cloud invariant).
    """
    execution_blocked = not runtime_config.is_execution_enabled()
    return _json_response({
        "status": "ok",
        "service": "decifer-intelligence-api",
        "runtime_mode": _RUNTIME_MODE,
        "execution_blocked": execution_blocked,
        "ts": _now_iso(),
    })


@app.route("/api/market-now")
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
