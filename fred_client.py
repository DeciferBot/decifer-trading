# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  fred_client.py                            ║
# ║   Federal Reserve Economic Data (FRED) API client.          ║
# ║                                                              ║
# ║   Provides:                                                  ║
# ║     get_upcoming_releases(days)  → economic calendar        ║
# ║     get_macro_snapshot()         → recent key indicators    ║
# ║                                                              ║
# ║   Free API — no rate limit for reasonable use.              ║
# ║   Sign up: https://fred.stlouisfed.org/docs/api/api_key.html║
# ║   Set env var: FRED_API_KEY                                  ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import os
import time as _time
from datetime import date, timedelta

import requests

from config import CONFIG

log = logging.getLogger("decifer.fred")

_BASE = "https://api.stlouisfed.org/fred"
_CACHE_TTL = 6 * 3600  # 6 hours

_cache: dict[str, tuple[object, float]] = {}

# ── High-impact FRED release IDs → display names + impact tier ───────────────
# Verified via FRED API: GET /fred/release?release_id=<id>
# FOMC dates are NOT tracked here — macro_calendar.py owns FOMC/CPI/NFP with
# hardcoded BLS/Fed calendar dates (more reliable than FRED release cadence).
_HIGH_IMPACT_RELEASES: dict[int, dict] = {
    10: {"name": "CPI", "impact": "High"},  # Consumer Price Index
    46: {"name": "PPI", "impact": "High"},  # Producer Price Index (was 11=Employment Cost Index, wrong)
    50: {"name": "Employment Situation (NFP)", "impact": "High"},  # Employment Situation
    53: {"name": "GDP", "impact": "High"},  # Gross Domestic Product
    54: {
        "name": "PCE / Personal Income",
        "impact": "High",
    },  # Personal Income and Outlays (was 20=H.4.1 Fed Balance Sheet, wrong)
    9: {"name": "Retail Sales", "impact": "High"},  # Advance Monthly Retail Sales (was 15=G.5 FX Rates, wrong)
    180: {
        "name": "Jobless Claims",
        "impact": "Medium",
    },  # Unemployment Insurance Weekly Claims (was 175=not found, wrong)
    51: {"name": "Trade Balance", "impact": "Medium"},  # U.S. International Trade in Goods and Services (was 25=wrong)
}

# ── Key macro series for the snapshot ────────────────────────────────────────
_MACRO_SERIES: list[dict] = [
    {"id": "CPIAUCSL", "name": "CPI YoY", "transform": "pc1", "unit": "%"},
    {"id": "UNRATE", "name": "Unemployment Rate", "transform": "lin", "unit": "%"},
    {"id": "FEDFUNDS", "name": "Fed Funds Rate", "transform": "lin", "unit": "%"},
    {"id": "T10Y2Y", "name": "10Y-2Y Spread", "transform": "lin", "unit": "%"},
    {"id": "DGS10", "name": "10Y Treasury", "transform": "lin", "unit": "%"},
    {"id": "DCOILWTICO", "name": "WTI Crude", "transform": "lin", "unit": "$/bbl"},
]


def _api_key() -> str:
    return CONFIG.get("fred_api_key", "") or os.environ.get("FRED_API_KEY", "")


def is_available() -> bool:
    return bool(_api_key())


def _get(endpoint: str, params: dict) -> dict | None:
    key = _api_key()
    if not key:
        log.debug("fred_client: FRED_API_KEY not set")
        return None

    import json

    cache_key = f"{endpoint}?{json.dumps(params, sort_keys=True)}"
    cached, fetched_at = _cache.get(cache_key, (None, 0.0))
    if cached is not None and (_time.time() - fetched_at) < _CACHE_TTL:
        return cached

    try:
        resp = requests.get(
            f"{_BASE}/{endpoint}",
            params={**params, "api_key": key, "file_type": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _cache[cache_key] = (data, _time.time())
        return data
    except requests.exceptions.HTTPError as exc:
        log.warning("fred_client: HTTP %s for %s", exc.response.status_code, endpoint)
        return None
    except Exception as exc:
        log.debug("fred_client: request failed — %s", exc)
        return None


# ── Upcoming economic releases ────────────────────────────────────────────────


def get_upcoming_releases(days_ahead: int = 7) -> list[dict]:
    """
    Return scheduled economic release dates in the next `days_ahead` days.

    FRED's releases/dates endpoint supports future dates when
    include_release_dates_with_no_data=true is set.

    Returns list of dicts: {date (str), release_id (int), name (str), impact (str)}
    Filtered to high/medium impact releases only. Sorted by date.
    """
    today = date.today()
    end_dt = today + timedelta(days=days_ahead)

    data = _get(
        "releases/dates",
        {
            "realtime_start": str(today),
            "realtime_end": str(end_dt),
            "include_release_dates_with_no_data": "true",
            "sort_order": "asc",
            "limit": 1000,
        },
    )
    if not data or "release_dates" not in data:
        return []

    results = []
    for entry in data["release_dates"]:
        rid = entry.get("release_id")
        if rid not in _HIGH_IMPACT_RELEASES:
            continue
        d_str = (entry.get("date") or "")[:10]
        if not d_str:
            continue
        meta = _HIGH_IMPACT_RELEASES[rid]
        results.append(
            {
                "date": d_str,
                "release_id": rid,
                "name": meta["name"],
                "impact": meta["impact"],
            }
        )

    return sorted(results, key=lambda x: x["date"])


# ── Recent macro snapshot ─────────────────────────────────────────────────────


def get_macro_snapshot() -> list[dict]:
    """
    Fetch the most recent observation for each key macro series.

    Returns list of dicts: {name (str), value (float), date (str), unit (str)}
    Skips any series that fails. Returns [] if FRED unavailable.
    """
    results = []
    for series in _MACRO_SERIES:
        try:
            data = _get(
                "series/observations",
                {
                    "series_id": series["id"],
                    "sort_order": "desc",
                    "limit": 2,  # latest + one prior for context
                    "units": series["transform"],
                    "observation_start": str(date.today() - timedelta(days=180)),
                },
            )
            if not data or not data.get("observations"):
                continue
            obs = [o for o in data["observations"] if o.get("value") not in (".", None, "")]
            if not obs:
                continue
            latest = obs[0]
            prior = obs[1] if len(obs) > 1 else None
            try:
                val = float(latest["value"])
            except (ValueError, TypeError):
                continue
            entry: dict = {
                "name": series["name"],
                "value": val,
                "date": latest.get("date", ""),
                "unit": series["unit"],
            }
            if prior:
                try:
                    entry["prior"] = float(prior["value"])
                except (ValueError, TypeError):
                    pass
            results.append(entry)
        except Exception as exc:
            log.debug("fred_client: series %s failed — %s", series["id"], exc)
            continue

    return results
