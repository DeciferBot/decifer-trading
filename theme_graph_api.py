# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  theme_graph_api.py                        ║
# ║   Theme Transmission Graph — Flask blueprint                ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
theme_graph_api.py — Customer-facing Theme Transmission Graph API.

Flask Blueprint registered at /api/intelligence. Provides read-only customer
endpoints over the Theme Transmission Graph (theme_graph.py). All responses
are validated through saas_intelligence_output before returning.

Layer: SAAS_OUTPUT — must NOT import any execution module.

Routes
──────
  GET /api/intelligence/themes                 — list all 10 themes
  GET /api/intelligence/themes/<theme_id>      — full theme detail + symbols
  GET /api/intelligence/search?q=<query>       — search themes and symbols
  GET /api/intelligence/symbol/<ticker>        — CustomerSymbolCard for a ticker

No mutation routes. No broker state. No execution fields.
"""
from __future__ import annotations

import logging
from typing import Any

from datetime import UTC, datetime

from flask import Blueprint, jsonify, request, Response

from saas_intelligence_output import SaaSPayloadValidationError, validate_customer_payload
import theme_graph as _ttg

log = logging.getLogger("decifer.theme_graph_api")

bp = Blueprint("theme_graph", __name__)

_DISCLAIMER = (
    "Theme intelligence powered by Decifer. "
    "This is not financial advice. For informational purposes only."
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validation_envelope(ttg_fields: dict) -> dict:
    """Build a minimal complete dict that satisfies validate_customer_payload's required fields."""
    return {
        **ttg_fields,
        "data_entitlement_note": _DISCLAIMER,
        "freshness_timestamp": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(data: dict, status: int = 200) -> Response:
    r = jsonify(data)
    r.status_code = status
    return r


def _err(message: str, status: int = 400) -> Response:
    return _ok({"status": "error", "message": message}, status=status)


def _safe_validate(payload: dict) -> dict:
    """
    Run customer payload validation. On failure, log and re-raise so the
    route handler can return an appropriate error rather than silently returning bad data.
    """
    try:
        validate_customer_payload(payload)
    except SaaSPayloadValidationError as exc:
        log.error("TTG payload failed customer validation: %s", exc)
        raise
    return payload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/api/intelligence/themes", methods=["GET"])
def list_themes() -> Response:
    """List all 10 transmission themes with driver-activation status."""
    themes = _ttg.get_themes_list()
    payload: dict[str, Any] = {
        "theme_graph_themes": themes,
        "disclaimer": _DISCLAIMER,
        "total": len(themes),
    }
    try:
        _safe_validate(_validation_envelope({"theme_graph_themes": themes}))
    except SaaSPayloadValidationError:
        return _err("Payload validation error — contact support", status=500)
    return _ok(payload)


@bp.route("/api/intelligence/themes/<theme_id>", methods=["GET"])
def theme_detail(theme_id: str) -> Response:
    """Full theme detail: description, buckets, and evidence-gated symbols."""
    detail = _ttg.get_theme_detail(theme_id)
    if detail is None:
        return _err(f"Theme not found: {theme_id}", status=404)
    payload: dict[str, Any] = {
        "theme_graph_buckets": detail.get("buckets", []),
        "theme_graph_themes": [
            {
                "theme_id": detail["theme_id"],
                "label": detail["label"],
                "plain_english_description": detail["plain_english_description"],
                "status": detail["status"],
                "driver_ids": detail["driver_ids"],
                "driver_active": detail["driver_active"],
                "risk_note": detail["risk_note"],
            }
        ],
        "theme_graph_reason_path": [
            {"symbol": s["symbol"], "reason_path": s["reason_path"]}
            for s in detail.get("symbols", [])
        ],
        "disclaimer": _DISCLAIMER,
        "symbol_count": len(detail.get("symbols", [])),
    }
    try:
        _safe_validate(_validation_envelope({
            "theme_graph_buckets": payload["theme_graph_buckets"],
            "theme_graph_themes": payload["theme_graph_themes"],
            "theme_graph_reason_path": payload["theme_graph_reason_path"],
        }))
    except SaaSPayloadValidationError:
        return _err("Payload validation error — contact support", status=500)
    # Attach symbols after validation (they carry their own validated fields)
    payload["symbols"] = detail.get("symbols", [])
    return _ok(payload)


@bp.route("/api/intelligence/search", methods=["GET"])
def search_themes() -> Response:
    """Search themes and evidence-gated active symbols. Requires ?q= query param."""
    q = request.args.get("q", "").strip()
    if not q:
        return _err("Missing query parameter: q", status=400)
    if len(q) > 200:
        return _err("Query too long (max 200 characters)", status=400)

    results = _ttg.search(q)
    payload: dict[str, Any] = {
        "theme_graph_search_results": {
            "query": q,
            "themes": results["themes"],
            "symbols": results["symbols"],
            "total": results["total"],
        },
        "disclaimer": _DISCLAIMER,
    }
    try:
        _safe_validate(_validation_envelope({"theme_graph_search_results": payload["theme_graph_search_results"]}))
    except SaaSPayloadValidationError:
        return _err("Payload validation error — contact support", status=500)
    return _ok(payload)


@bp.route("/api/intelligence/symbol/<ticker>", methods=["GET"])
def symbol_card(ticker: str) -> Response:
    """CustomerSymbolCard for a single ticker. Returns reason path and evidence basis."""
    ticker = ticker.upper().strip()
    if not ticker or len(ticker) > 10:
        return _err("Invalid ticker", status=400)

    card = _ttg.get_symbol_card(ticker)
    if card is None:
        return _err(f"Symbol not found or not evidence-gated: {ticker}", status=404)

    payload: dict[str, Any] = {
        "theme_graph_symbol_card": card,
        "theme_graph_reason_path": [{"symbol": card["symbol"], "reason_path": card["reason_path"]}],
        "disclaimer": _DISCLAIMER,
    }
    try:
        _safe_validate(_validation_envelope({
            "theme_graph_symbol_card": payload["theme_graph_symbol_card"],
            "theme_graph_reason_path": payload["theme_graph_reason_path"],
        }))
    except SaaSPayloadValidationError:
        return _err("Payload validation error — contact support", status=500)
    return _ok(payload)
