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
from datetime import UTC, date

import requests

from config import CONFIG

log = logging.getLogger("decifer.alphavantage")

_BASE_URL = "https://www.alphavantage.co/query"
_RATE_LIMIT_PATH = os.path.join(os.path.dirname(__file__), "data", "av_rate_limit.json")

# Aggressive caching — 25 calls/day free tier cannot support per-scan fetches.
# Yahoo RSS handles freshness; AV enriches quality every 4 hours.
_NEWS_TTL = 4 * 3600  # seconds — successful result TTL
_NEWS_ERROR_TTL = 30 * 60  # seconds — error result TTL (retry after 30 min)
_EARNINGS_TTL = 4 * 3600  # seconds

_news_cache: dict[str, tuple[dict, float]] = {}  # cache_key → (result, monotonic_time)
_earnings_cache: tuple[dict | None, float] = (None, 0.0)


# ── API keys (multi-key rotation) ─────────────────────────────────────────────


def _api_keys() -> list[str]:
    """
    Return all configured AV API keys.
    Supports two formats in .env / CONFIG:
      - Comma-separated:  ALPHA_VANTAGE_KEY=KEY1,KEY2,KEY3
      - Indexed:          ALPHA_VANTAGE_KEY_1=KEY1
                          ALPHA_VANTAGE_KEY_2=KEY2  (up to _9)
    """
    raw = CONFIG.get("alpha_vantage_key") or os.environ.get("ALPHA_VANTAGE_KEY", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    for i in range(1, 10):
        k = os.environ.get(f"ALPHA_VANTAGE_KEY_{i}", "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def _api_key() -> str:
    """Return the first configured key (backwards-compat helper)."""
    keys = _api_keys()
    return keys[0] if keys else ""


# ── Rate limiter ───────────────────────────────────────────────────────────────


def _consume_call() -> str:
    """
    Pick the next available API key and consume one call from its daily budget.
    Rotates through all configured keys in order; returns the chosen key string,
    or '' if every key is exhausted today.
    Fails open — tracking errors never block data fetches.
    """
    keys = _api_keys()
    if not keys:
        return ""

    today = date.today().isoformat()
    try:
        try:
            with open(_RATE_LIMIT_PATH) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}

        if state.get("date") != today:
            state = {"date": today}

        limit = CONFIG.get("alpha_vantage_daily_limit", 25)
        for key in keys:
            kid = key[-8:]  # last 8 chars as per-key ID
            count = state.get(kid, 0)
            if count < limit:
                state[kid] = count + 1
                with open(_RATE_LIMIT_PATH, "w") as f:
                    json.dump(state, f)
                log.debug("AV key ...%s: %d/%d calls today", kid, count + 1, limit)
                return key

        total = sum(state.get(k[-8:], 0) for k in keys)
        log.debug("AV: all %d key(s) exhausted (%d calls today)", len(keys), total)
        return ""
    except Exception as exc:
        log.debug("AV rate limit tracking error (fail-open): %s", exc)
        return keys[0]


def get_calls_today() -> int:
    """Return total AV API calls made today across all keys."""
    today = date.today().isoformat()
    try:
        with open(_RATE_LIMIT_PATH) as f:
            state = json.load(f)
        if state.get("date") != today:
            return 0
        keys = _api_keys()
        return sum(state.get(k[-8:], 0) for k in keys) if keys else state.get("count", 0)
    except Exception:
        return 0


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
    batch = tickers[:50]
    cache_key = ",".join(sorted(t.upper() for t in batch))
    now = time.monotonic()

    cached_result, cached_at = _news_cache.get(cache_key, (None, 0.0))
    if cached_result is not None:
        ttl = _NEWS_ERROR_TTL if not cached_result else _NEWS_TTL
        if now - cached_at < ttl:
            log.debug("AV news: cache hit (%d tickers)", len(cached_result))
            return cached_result

    key = _consume_call()
    if not key:
        return {}

    ticker_str = ",".join(t.upper() for t in batch)
    url = f"{_BASE_URL}?function=NEWS_SENTIMENT&tickers={ticker_str}&apikey={key}&sort=LATEST&limit=200"

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Decifer/2.0"})
        status = resp.status_code
        data = resp.json() if status == 200 else {}
        resp.close()
        if status != 200:
            log.warning("AV NEWS_SENTIMENT HTTP %d", status)
            return {}

        # AV returns rate-limit messages as JSON keys rather than HTTP errors
        if "Note" in data or "Information" in data:
            msg = (data.get("Note") or data.get("Information", ""))[:150]
            log.warning("AV API message: %s", msg)
            # Cache the empty result so we don't burn another API call on the
            # next scan. Use a 30-min TTL so it retries later rather than
            # hammering AV every 15 minutes with calls that also fail.
            _news_cache[cache_key] = ({}, now)
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
                relevance = float(ts.get("relevance_score") or 0)
                sent_score = float(ts.get("ticker_sentiment_score") or 0)
                sent_label = ts.get("ticker_sentiment_label", "Neutral")

                if sym not in agg:
                    agg[sym] = {
                        "weighted_sum": 0.0,
                        "weight_total": 0.0,
                        "count": 0,
                        "topics": set(),
                        "labels": [],
                    }
                agg[sym]["weighted_sum"] += sent_score * relevance
                agg[sym]["weight_total"] += relevance
                agg[sym]["count"] += 1
                agg[sym]["topics"].update(topics)
                agg[sym]["labels"].append(sent_label)

        result: dict[str, dict] = {}
        for sym, a in agg.items():
            avg_sent = a["weighted_sum"] / a["weight_total"] if a["weight_total"] > 0 else 0.0
            avg_relevance = a["weight_total"] / a["count"] if a["count"] > 0 else 0.0
            dominant_label = Counter(a["labels"]).most_common(1)[0][0] if a["labels"] else "Neutral"
            result[sym] = {
                "sentiment_score": round(avg_sent, 4),
                "sentiment_label": dominant_label,
                "relevance": round(avg_relevance, 4),
                "article_count": a["count"],
                "topics": sorted(a["topics"]),
            }

        log.info(
            "AV news sentiment: %d/%d tickers with coverage (%d articles in feed)",
            len(result),
            len(batch),
            len(feed),
        )
        _news_cache[cache_key] = (result, now)
        return result

    except Exception as exc:
        log.error("AV get_news_sentiment error: %s", exc)
        return {}


# Cache for raw article feed (separate from sentiment cache)
_articles_cache: tuple[list | None, float] = (None, 0.0)
_ARTICLES_TTL = 4 * 60 * 60  # 4 hours — free key = 25 req/day, conserve calls


def get_news_articles(tickers: list[str], limit: int = 50) -> list[dict]:
    """
    Fetch news articles with images from Alpha Vantage NEWS_SENTIMENT feed.

    Returns list of dicts:
      {
        "headline":   str,
        "summary":    str,
        "url":        str,
        "source":     str,
        "image_url":  str,   # banner_image from AV — actual article photo
        "symbols":    list[str],
        "sentiment":  str,   # "BULLISH" | "BEARISH" | "NEUTRAL"
        "age_hours":  float,
        "created_ts": int,   # ms epoch
      }

    Returns [] when: no key, rate limit hit, or API error.
    """
    if not tickers:
        return []
    global _articles_cache
    cached_articles, cached_at = _articles_cache
    now_mono = time.monotonic()
    if cached_articles is not None and now_mono - cached_at < _ARTICLES_TTL:
        log.debug("AV articles: cache hit (%d articles)", len(cached_articles))
        return cached_articles

    key = _consume_call()
    if not key:
        return cached_articles or []

    batch = tickers[:15]  # AV NEWS_SENTIMENT rejects > ~20 tickers; cap at 15
    ticker_str = ",".join(t.upper() for t in batch)
    url = f"{_BASE_URL}?function=NEWS_SENTIMENT&tickers={ticker_str}&apikey={key}&sort=LATEST&limit={limit}"

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Decifer/2.0"})
        status = resp.status_code
        data = resp.json() if status == 200 else {}
        resp.close()

        if status != 200:
            log.warning("AV articles HTTP %d", status)
            return cached_articles or []

        if "Note" in data or "Information" in data:
            msg = (data.get("Note") or data.get("Information", ""))[:150]
            log.warning("AV API message (articles): %s", msg)
            # Cache empty list so repeated dashboard fetches don't burn API
            # calls on errors. 30-min TTL so it retries after a cooldown.
            _articles_cache = ([], time.monotonic())
            return cached_articles or []

        from datetime import datetime

        now_utc = datetime.now(UTC)

        articles = []
        for art in data.get("feed", []):
            headline = (art.get("title") or "").strip()
            if not headline:
                continue

            image_url = (art.get("banner_image") or "").strip()

            # Parse published time: "20240101T120000"
            time_str = art.get("time_published", "")
            age_hours = 0.0
            created_ts = 0
            try:
                dt = datetime.strptime(time_str, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
                age_hours = (now_utc - dt).total_seconds() / 3600
                created_ts = int(dt.timestamp() * 1000)
            except Exception:
                pass

            # Derive dominant sentiment from overall score
            score = float(art.get("overall_sentiment_score") or 0)
            if score >= 0.15:
                sentiment = "BULLISH"
            elif score <= -0.15:
                sentiment = "BEARISH"
            else:
                sentiment = "NEUTRAL"

            # Collect symbols mentioned
            symbols = [
                (ts.get("ticker") or "").upper()
                for ts in art.get("ticker_sentiment", [])
                if (ts.get("ticker") or "").strip()
            ]

            # Derive a 0–10 news_score from the AV sentiment magnitude.
            # AV overall_sentiment_score typically ranges ±0.15–0.5; scale so
            # 0.15 → ~3, 0.35 → ~7, 0.5+ → 10.
            news_score = min(10, round(abs(score) * 20))

            articles.append(
                {
                    "headline": headline,
                    "summary": (art.get("summary") or "").strip(),
                    "url": (art.get("url") or "").strip(),
                    "source": (art.get("source") or "").strip(),
                    "image_url": image_url,
                    "symbols": symbols,
                    "sentiment": sentiment,
                    "age_hours": round(age_hours, 2),
                    "created_ts": created_ts,
                    "news_score": news_score,
                    "catalyst": "",
                }
            )

        log.info(
            "AV news articles: %d articles fetched (%d with images)",
            len(articles),
            sum(1 for a in articles if a["image_url"]),
        )
        _articles_cache = (articles, now_mono)
        return articles

    except Exception as exc:
        log.error("AV get_news_articles error: %s", exc)
        return cached_articles or []


# ── Earnings calendar ──────────────────────────────────────────────────────────


def get_sector_performance() -> dict[str, dict]:
    """
    Fetch sector performance across multiple timeframes using Alpha Vantage SECTOR function.

    Returns {timeframe: {sector_name: "+X.XX%"}} for timeframes:
      "1D", "5D", "1M", "3M", "YTD"

    Cached daily to data/sector_performance.json — one call per trading day.
    Returns {} when: no API key configured, rate limit exhausted, or AV returns an error.
    """
    import os as _os

    _cache_path = _os.path.join(_os.path.dirname(__file__), "data", "sector_performance.json")
    today = date.today().isoformat()

    # Try daily cache first
    try:
        with open(_cache_path) as f:
            cached = json.load(f)
        if cached.get("date") == today and cached.get("data"):
            log.debug("AV sector performance: cache hit for %s", today)
            return cached["data"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    key = _consume_call()
    if not key:
        return {}

    url = f"{_BASE_URL}?function=SECTOR&apikey={key}"

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Decifer/2.0"})
        status = resp.status_code
        data = resp.json() if status == 200 else {}
        resp.close()
        if status != 200:
            log.warning("AV SECTOR HTTP %d", status)
            return {}

        if "Note" in data or "Information" in data:
            msg = (data.get("Note") or data.get("Information", ""))[:150]
            log.warning("AV API message: %s", msg)
            return {}

        # AV SECTOR returns keys like "Rank A: Real-Time Performance", "Rank B: 1 Day Performance", etc.
        timeframe_map = {
            "Rank B: 1 Day Performance": "1D",
            "Rank C: 5 Day Performance": "5D",
            "Rank D: 1 Month Performance": "1M",
            "Rank E: 3 Month Performance": "3M",
            "Rank F: Year-to-Date (YTD) Performance": "YTD",
        }
        result: dict[str, dict] = {}
        for av_key, tf_label in timeframe_map.items():
            if av_key in data:
                result[tf_label] = data[av_key]

        if result:
            try:
                with open(_cache_path, "w") as f:
                    json.dump({"date": today, "data": result}, f)
            except Exception:
                pass
            log.info("AV sector performance: fetched %d timeframes", len(result))

        return result

    except Exception as exc:
        log.error("AV get_sector_performance error: %s", exc)
        return {}


def get_earnings_calendar(horizon_months: int = 3) -> dict[str, str]:
    """
    Fetch the upcoming earnings calendar for all US stocks (1 API call).

    Returns {SYMBOL: "YYYY-MM-DD"} — nearest upcoming earnings date per symbol.
    One call covers the entire US equity universe. Results cached for 4 hours.

    Returns {} when: no API key configured, rate limit exhausted, or AV returns an error.
    """
    global _earnings_cache
    now = time.monotonic()
    cached_result, cached_at = _earnings_cache
    if cached_result is not None and now - cached_at < _EARNINGS_TTL:
        log.debug("AV earnings: cache hit (%d symbols)", len(cached_result))
        return cached_result

    key = _consume_call()
    if not key:
        return {}

    horizon_str = f"{min(horizon_months, 3)}month"
    url = f"{_BASE_URL}?function=EARNINGS_CALENDAR&horizon={horizon_str}&apikey={key}"

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Decifer/2.0"})
        status = resp.status_code
        text = resp.text if status == 200 else ""
        resp.close()
        if status != 200:
            log.warning("AV EARNINGS_CALENDAR HTTP %d", status)
            return {}

        # AV returns CSV for this endpoint
        reader = csv.DictReader(io.StringIO(text))
        result: dict[str, str] = {}
        for row in reader:
            sym = (row.get("symbol") or "").strip().upper()
            report_date = (row.get("reportDate") or "").strip()
            # Symbols with multiple upcoming dates: keep the earliest
            if sym and report_date and (sym not in result or report_date < result[sym]):
                result[sym] = report_date

        log.info("AV earnings calendar: %d upcoming earnings (horizon=%s)", len(result), horizon_str)
        _earnings_cache = (result, now)
        return result

    except Exception as exc:
        log.error("AV get_earnings_calendar error: %s", exc)
        return {}
