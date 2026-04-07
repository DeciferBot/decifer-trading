# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpha_vantage_client.py                    ║
# ║   Alpha Vantage API client                                   ║
# ║                                                              ║
# ║   Responsibilities:                                          ║
# ║     get_news_sentiment(tickers)  → structured NLP per ticker ║
# ║     get_earnings_calendar()      → upcoming earnings dates   ║
# ║                                                              ║
# ║   Rate limiting: tracks daily call count in                  ║
# ║     data/av_rate_limit.json (default: 25 calls/day free).    ║
# ║   Caching: in-memory, 4-hour TTL per endpoint.               ║
# ║   Returns {} silently on: no key, budget exhausted, error.   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from collections import Counter
from datetime import date

import requests

from config import CONFIG

log = logging.getLogger("decifer.alphavantage")

_BASE_URL = "https://www.alphavantage.co/query"
_RATE_LIMIT_PATH = os.path.join(os.path.dirname(__file__), "data", "av_rate_limit.json")

# Aggressive caching — 25 calls/day free tier cannot support per-scan fetches.
# Yahoo RSS handles freshness; AV enriches quality every 4 hours.
_NEWS_TTL     = 4 * 3600   # seconds
_EARNINGS_TTL = 4 * 3600   # seconds

_news_cache: dict[str, tuple[dict, float]] = {}  # cache_key → (result, monotonic_time)
_earnings_cache: tuple[dict | None, float] = (None, 0.0)


# ── Rate limiter ───────────────────────────────────────────────────────────────

def _consume_call() -> bool:
    """
    Consume one API call from today's budget.
    Returns True if the call may proceed; False if budget is exhausted.
    Fails open — tracking errors never block data fetches.
    """
    today = date.today().isoformat()
    try:
        try:
            with open(_RATE_LIMIT_PATH) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {"date": today, "count": 0}

        if state.get("date") != today:
            state = {"date": today, "count": 0}

        limit = CONFIG.get("alpha_vantage_daily_limit", 25)
        if state["count"] >= limit:
            log.debug("AV rate limit: %d/%d calls used today — skipping", state["count"], limit)
            return False

        state["count"] += 1
        with open(_RATE_LIMIT_PATH, "w") as f:
            json.dump(state, f)

        log.debug("AV rate limit: %d/%d calls used today", state["count"], limit)
        return True
    except Exception as exc:
        log.debug("AV rate limit tracking error (fail-open): %s", exc)
        return True


def get_calls_today() -> int:
    """Return the number of AV API calls made today (for dashboard display)."""
    today = date.today().isoformat()
    try:
        with open(_RATE_LIMIT_PATH) as f:
            state = json.load(f)
        return state["count"] if state.get("date") == today else 0
    except Exception:
        return 0


# ── API key ────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    return CONFIG.get("alpha_vantage_key") or os.environ.get("ALPHA_VANTAGE_KEY", "")


# ── News sentiment ─────────────────────────────────────────────────────────────

def get_news_sentiment(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch structured news sentiment for a batch of tickers (1 API call).

    Returns {TICKER: {
        "sentiment_score":  float,    # relevance-weighted avg: -1 (bearish) to +1 (bullish)
        "sentiment_label":  str,      # dominant label: "Bullish" | "Somewhat-Bullish" |
                                      #   "Neutral" | "Somewhat-Bearish" | "Bearish"
        "relevance":        float,    # avg relevance score 0-1 (how well articles cover ticker)
        "article_count":    int,      # number of articles covering this ticker
        "topics":           list[str] # e.g. ["Earnings", "M&A", "IPO", "Technology"]
    }}

    Returns {} when: no API key configured, rate limit exhausted, or AV returns an error.
    """
    if not tickers:
        return {}
    key = _api_key()
    if not key:
        return {}

    batch = tickers[:50]
    cache_key = ",".join(sorted(t.upper() for t in batch))
    now = time.monotonic()

    cached_result, cached_at = _news_cache.get(cache_key, (None, 0.0))
    if cached_result is not None and now - cached_at < _NEWS_TTL:
        log.debug("AV news: cache hit (%d tickers)", len(cached_result))
        return cached_result

    if not _consume_call():
        return {}

    ticker_str = ",".join(t.upper() for t in batch)
    url = (f"{_BASE_URL}?function=NEWS_SENTIMENT"
           f"&tickers={ticker_str}&apikey={key}&sort=LATEST&limit=200")

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Decifer/2.0"})
        if resp.status_code != 200:
            log.warning("AV NEWS_SENTIMENT HTTP %d", resp.status_code)
            return {}

        data = resp.json()

        # AV returns rate-limit messages as JSON keys rather than HTTP errors
        if "Note" in data or "Information" in data:
            msg = (data.get("Note") or data.get("Information", ""))[:150]
            log.warning("AV API message: %s", msg)
            return {}

        feed = data.get("feed", [])
        ticker_upper = {t.upper() for t in batch}

        # Aggregate per ticker: relevance-weighted average sentiment across articles
        agg: dict[str, dict] = {}
        for article in feed:
            topics = [t.get("topic", "") for t in article.get("topics", [])]
            for ts in article.get("ticker_sentiment", []):
                sym = (ts.get("ticker") or "").upper()
                if sym not in ticker_upper:
                    continue
                relevance  = float(ts.get("relevance_score")  or 0)
                sent_score = float(ts.get("ticker_sentiment_score") or 0)
                sent_label = ts.get("ticker_sentiment_label", "Neutral")

                if sym not in agg:
                    agg[sym] = {
                        "weighted_sum": 0.0, "weight_total": 0.0,
                        "count": 0, "topics": set(), "labels": [],
                    }
                agg[sym]["weighted_sum"] += sent_score * relevance
                agg[sym]["weight_total"] += relevance
                agg[sym]["count"]        += 1
                agg[sym]["topics"].update(topics)
                agg[sym]["labels"].append(sent_label)

        result: dict[str, dict] = {}
        for sym, a in agg.items():
            avg_sent      = a["weighted_sum"] / a["weight_total"] if a["weight_total"] > 0 else 0.0
            avg_relevance = a["weight_total"] / a["count"]        if a["count"] > 0        else 0.0
            dominant_label = Counter(a["labels"]).most_common(1)[0][0] if a["labels"] else "Neutral"
            result[sym] = {
                "sentiment_score": round(avg_sent, 4),
                "sentiment_label": dominant_label,
                "relevance":       round(avg_relevance, 4),
                "article_count":   a["count"],
                "topics":          sorted(a["topics"]),
            }

        log.info(
            "AV news sentiment: %d/%d tickers with coverage (%d articles in feed)",
            len(result), len(batch), len(feed),
        )
        _news_cache[cache_key] = (result, now)
        return result

    except Exception as exc:
        log.error("AV get_news_sentiment error: %s", exc)
        return {}


# ── Earnings calendar ──────────────────────────────────────────────────────────

def get_earnings_calendar(horizon_months: int = 3) -> dict[str, str]:
    """
    Fetch the upcoming earnings calendar for all US stocks (1 API call).

    Returns {SYMBOL: "YYYY-MM-DD"} — nearest upcoming earnings date per symbol.
    One call covers the entire US equity universe. Results cached for 4 hours.

    Returns {} when: no API key configured, rate limit exhausted, or AV returns an error.
    """
    global _earnings_cache
    key = _api_key()
    if not key:
        return {}

    now = time.monotonic()
    cached_result, cached_at = _earnings_cache
    if cached_result is not None and now - cached_at < _EARNINGS_TTL:
        log.debug("AV earnings: cache hit (%d symbols)", len(cached_result))
        return cached_result

    if not _consume_call():
        return {}

    horizon_str = f"{min(horizon_months, 3)}month"
    url = f"{_BASE_URL}?function=EARNINGS_CALENDAR&horizon={horizon_str}&apikey={key}"

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Decifer/2.0"})
        if resp.status_code != 200:
            log.warning("AV EARNINGS_CALENDAR HTTP %d", resp.status_code)
            return {}

        # AV returns CSV for this endpoint
        reader = csv.DictReader(io.StringIO(resp.text))
        result: dict[str, str] = {}
        for row in reader:
            sym         = (row.get("symbol")     or "").strip().upper()
            report_date = (row.get("reportDate") or "").strip()
            if sym and report_date:
                # Symbols with multiple upcoming dates: keep the earliest
                if sym not in result or report_date < result[sym]:
                    result[sym] = report_date

        log.info("AV earnings calendar: %d upcoming earnings (horizon=%s)", len(result), horizon_str)
        _earnings_cache = (result, now)
        return result

    except Exception as exc:
        log.error("AV get_earnings_calendar error: %s", exc)
        return {}
