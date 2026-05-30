# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  v1_auth.py                                ║
# ║   Product API v1 — authentication and rate limiting         ║
# ║   Layer: SAAS_OUTPUT — no execution imports                 ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
v1_auth.py — API key authentication and per-key rate limiting for Product API v1.

Auth:
  Reads X-API-Key header (or ?api_key= query param as fallback).
  Valid keys are read from the INTELLIGENCE_API_KEYS env var (comma-separated).
  If the env var is unset AND runtime_mode is not intelligence_cloud, dev mode
  is active — all requests pass (for local development only).
  If the env var is unset in intelligence_cloud mode, all requests are rejected.

Rate limiting:
  Token bucket per API key — 100 requests per 60 seconds.
  Refill is continuous (not batch). Returns 429 with Retry-After on exhaustion.
"""
from __future__ import annotations

import os
import threading
import time
from functools import wraps
from typing import Callable

from flask import request, jsonify, Response

# ---------------------------------------------------------------------------
# Key registry
# ---------------------------------------------------------------------------

_raw_keys = os.environ.get("INTELLIGENCE_API_KEYS", "").strip()
_API_KEYS: frozenset[str] = frozenset(k.strip() for k in _raw_keys.split(",") if k.strip())

_runtime_mode = os.environ.get("DECIFER_RUNTIME_MODE", "").strip()
_DEV_BYPASS = (not _API_KEYS) and (_runtime_mode != "intelligence_cloud")

# ---------------------------------------------------------------------------
# Rate limiter — token bucket, in-memory, per API key
# ---------------------------------------------------------------------------

_RATE_LIMIT_MAX = 100       # max tokens (= max burst)
_RATE_LIMIT_PERIOD = 60.0   # seconds over which _RATE_LIMIT_MAX tokens refill

_buckets: dict[str, dict] = {}
_buckets_lock = threading.Lock()


def _consume_token(api_key: str) -> tuple[bool, float]:
    """
    Attempt to consume one token for api_key.
    Returns (allowed: bool, retry_after_seconds: float).
    """
    now = time.monotonic()
    refill_rate = _RATE_LIMIT_MAX / _RATE_LIMIT_PERIOD

    with _buckets_lock:
        if api_key not in _buckets:
            _buckets[api_key] = {"tokens": float(_RATE_LIMIT_MAX), "last_refill": now}

        bucket = _buckets[api_key]
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(_RATE_LIMIT_MAX, bucket["tokens"] + elapsed * refill_rate)
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True, 0.0
        else:
            retry_after = (1.0 - bucket["tokens"]) / refill_rate
            return False, retry_after


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _auth_error(message: str, status: int, retry_after: float = 0.0) -> Response:
    r = jsonify({"error": {"code": status, "message": message}})
    r.status_code = status
    if retry_after > 0:
        r.headers["Retry-After"] = str(int(retry_after) + 1)
    return r


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def require_api_key(f: Callable) -> Callable:
    """
    Decorator: enforce API key auth and rate limiting.
    Apply to every v1 route that requires authentication.
    """
    @wraps(f)
    def _wrapped(*args, **kwargs):
        if _DEV_BYPASS:
            return f(*args, **kwargs)

        key = (
            request.headers.get("X-API-Key")
            or request.args.get("api_key", "")
        ).strip()

        if not key:
            return _auth_error("Missing API key. Set X-API-Key header.", 401)
        if key not in _API_KEYS:
            return _auth_error("Invalid API key.", 401)

        allowed, retry_after = _consume_token(key)
        if not allowed:
            return _auth_error(
                f"Rate limit exceeded. Retry after {int(retry_after) + 1}s.",
                429,
                retry_after,
            )

        return f(*args, **kwargs)

    return _wrapped
