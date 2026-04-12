# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  fmp_client.py                             ║
# ║   Financial Modeling Prep API client.                        ║
# ║   Provides economic calendar, earnings with estimates,       ║
# ║   and analyst rating changes for overnight research.         ║
# ║                                                              ║
# ║   Free tier: 250 calls/day — no credit card required.       ║
# ║   Sign up: https://financialmodelingprep.com/register       ║
# ║   Set env var: FMP_API_KEY                                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import time as _time
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import requests

from config import CONFIG

log = logging.getLogger("decifer.fmp")

_BASE = "https://financialmodelingprep.com/api"
_CACHE_TTL = 4 * 3600   # 4 hours — same cadence as Alpha Vantage

# ── In-memory cache (key → (data, fetched_at)) ───────────────────────────────
_cache: dict[str, tuple[object, float]] = {}


def _api_key() -> str:
    return CONFIG.get("fmp_api_key", "") or os.environ.get("FMP_API_KEY", "")


def is_available() -> bool:
    return bool(_api_key())


def _get(endpoint: str, params: dict, version: str = "v3") -> list | dict | None:
    """
    Make a GET request to the FMP API.
    Returns parsed JSON or None on any failure.
    Rate limit: 250 calls/day on free tier — callers must cache aggressively.
    """
    key = _api_key()
    if not key:
        log.debug("fmp_client: FMP_API_KEY not set — skipping")
        return None

    cache_key = f"{version}/{endpoint}?{json.dumps(params, sort_keys=True)}"
    cached, fetched_at = _cache.get(cache_key, (None, 0.0))
    if cached is not None and (_time.time() - fetched_at) < _CACHE_TTL:
        return cached

    url = f"{_BASE}/{version}/{endpoint}"
    try:
        resp = requests.get(url, params={**params, "apikey": key}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # FMP returns {"Error Message": "..."} on bad key / limit exceeded
        if isinstance(data, dict) and "Error Message" in data:
            log.warning("fmp_client: API error — %s", data["Error Message"])
            return None
        _cache[cache_key] = (data, _time.time())
        return data
    except requests.exceptions.HTTPError as exc:
        log.warning("fmp_client: HTTP %s for %s", exc.response.status_code, endpoint)
        return None
    except Exception as exc:
        log.debug("fmp_client: request failed — %s", exc)
        return None


# ── Economic Calendar ─────────────────────────────────────────────────────────

def get_economic_calendar(days_ahead: int = 7) -> list[dict]:
    """
    Fetch upcoming economic events for the next `days_ahead` days.

    Returns list of dicts, each with:
        date (str YYYY-MM-DD), event (str), country (str),
        impact (str: "High" | "Medium" | "Low"),
        estimate (float|None), previous (float|None), actual (float|None),
        unit (str)

    Filtered to US events only. Sorted by date ascending.
    """
    today  = date.today()
    end_dt = today + timedelta(days=days_ahead)
    raw = _get(
        "economic_calendar",
        {"from": str(today), "to": str(end_dt)},
    )
    if not raw or not isinstance(raw, list):
        return []

    events = []
    for item in raw:
        country = (item.get("country") or "").upper()
        if country not in ("US", "USD", ""):
            continue
        impact = item.get("impact") or ""
        # Normalise impact — FMP uses "High", "Medium", "Low", "None"
        if impact.lower() not in ("high", "medium", "low"):
            continue  # skip non-event entries
        try:
            events.append({
                "date":     (item.get("date") or "")[:10],
                "event":    item.get("event") or item.get("name") or "",
                "country":  country,
                "impact":   impact.capitalize(),
                "estimate": _safe_float(item.get("estimate")),
                "previous": _safe_float(item.get("previous")),
                "actual":   _safe_float(item.get("actual")),
                "unit":     item.get("unit") or "",
            })
        except Exception:
            continue

    return sorted(events, key=lambda x: x["date"])


# ── Earnings Calendar ─────────────────────────────────────────────────────────

def get_earnings_calendar(symbols: list[str] | None = None,
                          days_ahead: int = 5) -> list[dict]:
    """
    Fetch upcoming earnings for the next `days_ahead` days.

    If `symbols` is provided, filters to that universe.
    Returns list of dicts, each with:
        date (str YYYY-MM-DD), symbol (str), timing (str: "BMO"|"AMC"|""),
        eps_est (float|None), eps_prior (float|None),
        revenue_est (float|None), revenue_prior (float|None)

    Sorted by date ascending.
    """
    today  = date.today()
    end_dt = today + timedelta(days=days_ahead)
    raw = _get(
        "earning_calendar",
        {"from": str(today), "to": str(end_dt)},
    )
    if not raw or not isinstance(raw, list):
        return []

    sym_set = {s.upper() for s in symbols} if symbols else None

    results = []
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if sym_set and sym not in sym_set:
            continue

        # FMP time field: "bmo" = before market open, "amc" = after market close
        timing_raw = (item.get("time") or "").lower()
        timing = "BMO" if "bmo" in timing_raw else ("AMC" if "amc" in timing_raw else "")

        results.append({
            "date":          (item.get("date") or "")[:10],
            "symbol":        sym,
            "timing":        timing,
            "eps_est":       _safe_float(item.get("epsEstimated")),
            "eps_prior":     _safe_float(item.get("eps")),
            "revenue_est":   _safe_float(item.get("revenueEstimated")),
            "revenue_prior": _safe_float(item.get("revenue")),
        })

    return sorted(results, key=lambda x: x["date"])


# ── Analyst Upgrades / Downgrades ─────────────────────────────────────────────

def get_analyst_changes(symbols: list[str] | None = None,
                        hours_back: int = 24) -> list[dict]:
    """
    Fetch analyst upgrades/downgrades published in the last `hours_back` hours.

    If `symbols` is provided, filters to that universe.
    Returns list of dicts, each with:
        symbol (str), action (str: "upgrade"|"downgrade"|"init"|"reiterated"),
        from_grade (str), to_grade (str), firm (str),
        published_date (str ISO)

    Returns empty list if endpoint unavailable (premium-only on some plans).
    """
    raw = _get("upgrades-downgrades-rss-feed", {"page": 0}, version="v4")
    if not raw or not isinstance(raw, list):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    sym_set = {s.upper() for s in symbols} if symbols else None

    results = []
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if sym_set and sym not in sym_set:
            continue

        pub_str = item.get("publishedDate") or ""
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if pub_dt < cutoff:
            continue

        action = (item.get("action") or "").lower()

        results.append({
            "symbol":        sym,
            "action":        action,
            "from_grade":    item.get("previousGrade") or "",
            "to_grade":      item.get("newGrade") or "",
            "firm":          item.get("gradingCompany") or "",
            "published_date": pub_str,
        })

    return sorted(results, key=lambda x: x["published_date"], reverse=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
