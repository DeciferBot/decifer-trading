# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  finnhub_client.py                         ║
# ║   Thin Finnhub REST client with rolling rate limiter.        ║
# ║                                                              ║
# ║   Free tier coverage (verified):                             ║
# ║     /quote          — real-time stock quotes (US equities)   ║
# ║     /company-news   — news articles per symbol + date range  ║
# ║                                                              ║
# ║   NOT on free tier (403):                                    ║
# ║     /news-sentiment, /stock/short-interest, index quotes     ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import requests

from config import CONFIG

log = logging.getLogger("decifer.finnhub")

_BASE = "https://finnhub.io/api/v1"
_CALL_TIMES: list[float] = []  # rolling window of call timestamps
_RATE_LIMIT = 55  # stay below the 60/min free tier cap


def _get(endpoint: str, params: dict | None = None) -> dict | list | None:
    """
    GET a Finnhub endpoint.  Returns parsed JSON or None on any failure.
    Enforces a 55 calls/minute rolling window to stay within the free tier.
    Short-circuits immediately if no API key is configured.
    """
    api_key = CONFIG.get("finnhub_api_key", "")
    if not api_key:
        return None

    # ── Rolling rate limiter ───────────────────────────────────
    global _CALL_TIMES
    now = time.monotonic()
    _CALL_TIMES = [t for t in _CALL_TIMES if now - t < 60]
    if len(_CALL_TIMES) >= _RATE_LIMIT:
        wait = 60 - (now - _CALL_TIMES[0]) + 0.1
        if wait > 0:
            time.sleep(wait)
        _CALL_TIMES = [t for t in _CALL_TIMES if time.monotonic() - t < 60]

    _CALL_TIMES.append(time.monotonic())

    try:
        p = {"token": api_key}
        if params:
            p.update(params)
        resp = requests.get(f"{_BASE}/{endpoint}", params=p, timeout=5)
        if resp.status_code == 429:
            log.warning("Finnhub rate limit hit — backing off 10 s")
            time.sleep(10)
            return None
        if resp.status_code == 403:
            log.debug(f"Finnhub /{endpoint} → 403 (not on free tier)")
            return None
        if resp.status_code != 200:
            log.debug(f"Finnhub /{endpoint} → HTTP {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        log.debug(f"Finnhub /{endpoint} error: {e}")
        return None


# ── Public helpers ────────────────────────────────────────────────────────────


def get_quote(symbol: str) -> dict | None:
    """
    Real-time quote for a US stock.
    Returns {c: current, d: change, dp: change%, h: high, l: low, o: open, pc: prev_close}
    or None on failure.
    NOTE: Index symbols (^VIX) require a paid subscription — use VIXY or yfinance for VIX.
    """
    return _get("quote", {"symbol": symbol})


def get_company_news(symbol: str, lookback_days: int = 2) -> list[dict]:
    """
    Fetch recent news articles for a symbol (FREE on Finnhub free tier).
    Returns list of {headline, summary, datetime (unix), source, url}
    sorted newest-first.  Returns [] on any failure.

    lookback_days: how many calendar days back to fetch (default: 2)
    """
    today = date.today()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    to_date = today.isoformat()
    data = _get("company-news", {"symbol": symbol, "from": from_date, "to": to_date})
    if not isinstance(data, list):
        return []
    # Sort newest first, cap at 20 articles
    data.sort(key=lambda x: x.get("datetime", 0), reverse=True)
    return data[:20]


def is_available() -> bool:
    """Return True if a Finnhub API key is configured."""
    return bool(CONFIG.get("finnhub_api_key", ""))
