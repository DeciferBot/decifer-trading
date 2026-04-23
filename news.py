# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  news.py                              ║
# ║   News sentiment engine — Yahoo RSS + keyword scoring       ║
# ║   + Claude deep read for high-scoring symbols               ║
# ║                                                              ║
# ║   Two-tier system:                                           ║
# ║     Tier 1: Fast keyword scoring (all symbols, ~0ms each)    ║
# ║     Tier 2: Claude sentiment analysis (top scorers only)     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests

from config import CONFIG

log = logging.getLogger("decifer.news")

# ── NEWS CACHE (avoid refetching within scan window) ───────
_news_cache = {}  # {symbol: {"data": {...}, "fetched_at": datetime}}
_CACHE_TTL_MIN = 15  # Cache results for 15 minutes (sentinel handles real-time news)

# ── ALPACA REAL-TIME ARTICLE CACHE ──────────────────────────
# Populated by AlpacaNewsStream._process_article() as articles arrive.
# batch_news_sentiment() merges these with Yahoo RSS before scoring —
# gives zero-latency Benzinga headlines in the dimension score.
_ALPACA_ARTICLE_CACHE: dict[str, list[dict]] = {}  # {symbol: [{"title": str, "age_hours": float}]}
_ALPACA_CACHE_TTL_MIN = 30  # Alpaca articles expire after 30 minutes


def push_alpaca_article(symbol: str, headline: str, age_hours: float = 0.0) -> None:
    """
    Called by AlpacaNewsStream to push a real-time Benzinga article into
    the scoring cache.  Thread-safe via GIL (dict insert + slice).
    Keeps the 10 most recent articles per symbol.
    """
    entry = {"title": headline, "age_hours": round(age_hours, 2)}
    bucket = _ALPACA_ARTICLE_CACHE.setdefault(symbol, [])
    bucket.insert(0, entry)
    _ALPACA_ARTICLE_CACHE[symbol] = bucket[:10]

# ── SENTIMENT KEYWORD DICTIONARIES ───────────────────────────
# Curated for financial news. Weighted: strong words = 2, normal = 1.
BULLISH_STRONG = {
    "surges",
    "soars",
    "skyrockets",
    "beats",
    "smashes",
    "crushes",
    "blowout",
    "record high",
    "all-time high",
    "breakout",
    "moonshot",
    "massive growth",
    "blows past",
    "exceeds expectations",
    "upgrades",
    "strong buy",
    "outperform",
    "bullish",
    "raises guidance",
    "raised guidance",
    "accelerating",
    "blockbuster",
    "doubles",
    "triples",
    "rockets",
    # ── M&A / acquisition announcement keywords ──────────────────────────────
    # Added to ensure the News Sentinel flags acquisition headlines as CRITICAL
    # without waiting for Claude confidence scoring.
    "to be acquired",
    "acquisition agreement",
    "merger agreement",
    "definitive agreement",
    "agreed to be acquired",
    "agreed to acquire",
    "tender offer",
    "per share in cash",
    "takeover bid",
    "going private",
    "take-private",
    "management buyout",
}

BULLISH_NORMAL = {
    "rises",
    "gains",
    "climbs",
    "rallies",
    "advances",
    "jumps",
    "up",
    "higher",
    "positive",
    "growth",
    "profit",
    "revenue beat",
    "earnings beat",
    "buy",
    "upgrade",
    "upbeat",
    "optimistic",
    "boost",
    "expansion",
    "recovery",
    "rebounds",
    "lifts",
    "improves",
    "tops",
    "raised",
    "dividend",
    "buyback",
    "repurchase",
    "acquisition",
    "partnership",
    "deal",
    "contract",
    "approval",
    "fda approval",
    "launched",
    "innovation",
    "breakthrough",
    "momentum",
    "demand",
    "overweight",
    "price target raised",
    "initiated",
    "accumulate",
}

BEARISH_STRONG = {
    "crashes",
    "plunges",
    "tanks",
    "collapses",
    "plummets",
    "cratering",
    "bankruptcy",
    "default",
    "fraud",
    "sec investigation",
    "delisted",
    "massive loss",
    "warns",
    "guidance cut",
    "slashes",
    "downgrades",
    "strong sell",
    "underperform",
    "bearish",
    "recall",
    "lawsuit",
    "indictment",
    "scandal",
    "misses badly",
    "catastrophic",
    "freefall",
}

BEARISH_NORMAL = {
    "falls",
    "drops",
    "declines",
    "slips",
    "slides",
    "dips",
    "tumbles",
    "down",
    "lower",
    "negative",
    "loss",
    "deficit",
    "revenue miss",
    "earnings miss",
    "sell",
    "downgrade",
    "cuts",
    "layoffs",
    "restructuring",
    "debt",
    "dilution",
    "offering",
    "secondary",
    "concern",
    "risk",
    "headwinds",
    "weak",
    "disappointing",
    "below expectations",
    "misses",
    "underweight",
    "price target cut",
    "overvalued",
    "expensive",
    "slowdown",
    "contraction",
    "recession",
    "tariff",
    "sanctions",
}

# Pre-compute: separate single-word and multi-word keywords for fast matching
_BULL_STRONG_SINGLE = {kw for kw in BULLISH_STRONG if " " not in kw}
_BULL_STRONG_MULTI = {kw for kw in BULLISH_STRONG if " " in kw}
_BULL_NORMAL_SINGLE = {kw for kw in BULLISH_NORMAL if " " not in kw}
_BULL_NORMAL_MULTI = {kw for kw in BULLISH_NORMAL if " " in kw}
_BEAR_STRONG_SINGLE = {kw for kw in BEARISH_STRONG if " " not in kw}
_BEAR_STRONG_MULTI = {kw for kw in BEARISH_STRONG if " " in kw}
_BEAR_NORMAL_SINGLE = {kw for kw in BEARISH_NORMAL if " " not in kw}
_BEAR_NORMAL_MULTI = {kw for kw in BEARISH_NORMAL if " " in kw}


def fetch_yahoo_rss(symbol: str, max_articles: int = 10) -> list[dict]:
    """
    Fetch recent news from Yahoo Finance RSS for a given symbol.
    Returns list of {title, published, link, age_hours}.
    """
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    try:
        resp = requests.get(url, timeout=3, headers={"User-Agent": "Decifer/2.0 (Trading Bot)"})
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)
        articles = []
        now = datetime.now(UTC)

        for item in root.findall(".//item")[:max_articles]:
            title = item.findtext("title", "").strip()
            pub_date = item.findtext("pubDate", "")
            link = item.findtext("link", "")

            # Parse publication date
            age_hours = 999
            if pub_date:
                try:
                    # Yahoo RSS uses RFC 822 format
                    pub_dt = parsedate_to_datetime(pub_date)
                    age_hours = (now - pub_dt).total_seconds() / 3600
                except Exception:
                    pass

            if title:
                articles.append(
                    {
                        "title": title,
                        "published": pub_date,
                        "link": link,
                        "age_hours": round(age_hours, 1),
                    }
                )

        return articles

    except Exception as e:
        log.debug(f"Yahoo RSS error for {symbol}: {e}")
        return []


def keyword_score(headlines: list[str]) -> dict:
    """
    Fast keyword sentiment scoring using set intersection for single-word
    keywords and substring search only for multi-word phrases.
    Returns {score: -10 to +10, bull_hits: int, bear_hits: int, keywords: list}.
    """
    bull_pts = 0
    bear_pts = 0
    matched_keywords = []

    for headline in headlines:
        h = headline.lower()
        words = set(h.split())

        # Single-word matches via set intersection (O(min(n,m)))
        for kw in words & _BULL_STRONG_SINGLE:
            bull_pts += 2
            matched_keywords.append(f"+{kw}")
        for kw in words & _BULL_NORMAL_SINGLE:
            bull_pts += 1
            matched_keywords.append(f"+{kw}")
        for kw in words & _BEAR_STRONG_SINGLE:
            bear_pts += 2
            matched_keywords.append(f"-{kw}")
        for kw in words & _BEAR_NORMAL_SINGLE:
            bear_pts += 1
            matched_keywords.append(f"-{kw}")

        # Multi-word phrases need substring search (fewer keywords)
        for kw in _BULL_STRONG_MULTI:
            if kw in h:
                bull_pts += 2
                matched_keywords.append(f"+{kw}")
        for kw in _BULL_NORMAL_MULTI:
            if kw in h:
                bull_pts += 1
                matched_keywords.append(f"+{kw}")
        for kw in _BEAR_STRONG_MULTI:
            if kw in h:
                bear_pts += 2
                matched_keywords.append(f"-{kw}")
        for kw in _BEAR_NORMAL_MULTI:
            if kw in h:
                bear_pts += 1
                matched_keywords.append(f"-{kw}")

    raw_score = bull_pts - bear_pts
    if raw_score > 0:
        score = min(10, raw_score)
    elif raw_score < 0:
        score = max(-10, raw_score)
    else:
        score = 0

    return {
        "score": score,
        "bull_hits": bull_pts,
        "bear_hits": bear_pts,
        "keywords": matched_keywords[:10],
    }


def get_news_sentiment(symbol: str, direction: str = "", keyword_threshold: int = 2) -> dict:
    """
    Two-tier news sentiment for a single symbol.

    Tier 1: Keyword scoring (always runs, ~0ms)
    Tier 2: Claude deep read (only if |keyword_score| >= threshold)

    Returns:
    {
        "symbol": str,
        "headlines": list[str],
        "headline_count": int,
        "recency_hours": float,       # age of most recent headline
        "keyword_score": int,         # -10 to +10
        "keyword_hits": list[str],
        "claude_sentiment": str,      # BULLISH/BEARISH/NEUTRAL
        "claude_confidence": int,     # 0-10
        "claude_catalyst": str,       # one-line summary
        "news_score": int,            # final 0-10 score for the dimension
    }
    """
    articles = fetch_yahoo_rss(symbol)
    headlines = [a["title"] for a in articles]
    recency = min([a["age_hours"] for a in articles]) if articles else 999

    # Tier 1: Keyword scoring
    kw = keyword_score(headlines)

    # ── Compute final news_score (0-10) — keyword + recency only ──
    news_score = 0

    # Keyword contribution (0-5)
    if direction in ("LONG", ""):
        kw_contrib = max(0, min(5, kw["score"]))
    elif direction == "SHORT":
        kw_contrib = max(0, min(5, -kw["score"]))
    else:
        kw_contrib = 0
    news_score += kw_contrib

    # Recency boost
    if recency < 2:
        news_score = min(10, news_score + 2)
    elif recency < 4:
        news_score = min(10, news_score + 1)

    # Decay
    if recency > 24:
        news_score = max(0, news_score - 2)
    elif recency > 12:
        news_score = max(0, news_score - 1)

    return {
        "symbol": symbol,
        "headlines": headlines[:5],
        "headline_count": len(headlines),
        "recency_hours": round(recency, 1),
        "keyword_score": kw["score"],
        "keyword_hits": kw["keywords"],
        "news_finbert_sentiment": None,
        "news_finbert_confidence": None,
        "news_score": min(10, max(0, news_score)),
    }


def _empty_sentiment(symbol: str) -> dict:
    """Return a neutral/empty sentiment result."""
    return {
        "symbol": symbol,
        "headlines": [],
        "headline_count": 0,
        "recency_hours": 999,
        "keyword_score": 0,
        "keyword_hits": [],
        "news_finbert_sentiment": None,
        "news_finbert_confidence": None,
        "av_sentiment_score": 0.0,
        "av_sentiment_label": "",
        "av_relevance": 0.0,
        "av_topics": [],
        "news_score": 0,
    }


def batch_news_sentiment(symbols: list[str], directions: dict[str, str] | None = None) -> dict[str, dict]:
    """
    Fetch news sentiment for a batch of symbols — PARALLEL with caching.
    Returns {symbol: news_sentiment_dict}.

    - RSS fetches run in parallel (10 workers)
    - Results cached for 5 minutes to avoid redundant HTTP calls
    - Claude tier 2 calls limited to max 5 per batch to control latency
    """
    if directions is None:
        directions = {}

    now = datetime.now(UTC)
    results = {}
    to_fetch = []

    # ── Check cache first ──────────────────────────────────────
    for symbol in symbols:
        cached = _news_cache.get(symbol)
        if cached:
            age_min = (now - cached["fetched_at"]).total_seconds() / 60
            if age_min < _CACHE_TTL_MIN:
                results[symbol] = cached["data"]
                continue
        to_fetch.append(symbol)

    if not to_fetch:
        log.info(f"News: all {len(symbols)} symbols served from cache")
        return results

    log.info(f"News: fetching {len(to_fetch)} symbols ({len(symbols) - len(to_fetch)} cached)")

    # ── Phase 1: Parallel RSS fetch + keyword scoring ──────────
    def fetch_one(sym):
        """Fetch RSS and do tier-1 keyword scoring (no Claude call yet).
        Alpaca real-time articles (from push cache) are prepended so the
        dimension score reflects breaking news with zero polling lag.
        """
        try:
            articles = fetch_yahoo_rss(sym)

            # Merge Alpaca real-time articles (freshest first, already age-tagged)
            alpaca_articles = _ALPACA_ARTICLE_CACHE.get(sym, [])
            now_dt = datetime.now(UTC)
            fresh_alpaca = [
                a for a in alpaca_articles
                if a["age_hours"] < _ALPACA_CACHE_TTL_MIN / 60
            ]
            if fresh_alpaca:
                articles = fresh_alpaca + articles
                log.debug("news(%s): merged %d Alpaca articles with RSS", sym, len(fresh_alpaca))

            headlines = [a["title"] for a in articles]
            recency = min([a["age_hours"] for a in articles]) if articles else 999
            kw = keyword_score(headlines)
            return sym, articles, headlines, recency, kw
        except Exception as e:
            log.debug(f"RSS fetch error for {sym}: {e}")
            return sym, [], [], 999, {"score": 0, "bull_hits": 0, "bear_hits": 0, "keywords": []}

    rss_results = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_one, sym): sym for sym in to_fetch}
        for future in as_completed(futures):
            sym, _articles, headlines, recency, kw = future.result()
            rss_results[sym] = (headlines, recency, kw)

    # ── Phase 2: Alpha Vantage Tier 2 — structured NLP from professional sources ──
    # One call covers the whole batch. Cached 4 hours (25 call/day free tier).
    # AV aggregates Reuters, Bloomberg, AP etc. — higher quality than Yahoo RSS keywords.
    av_results: dict[str, dict] = {}
    try:
        from alpha_vantage_client import get_news_sentiment as _av_news

        av_results = _av_news(to_fetch)
        hits = sum(1 for v in av_results.values() if abs(v.get("sentiment_score", 0)) > 0.15)
        if av_results:
            log.info("AV news tier: %d/%d symbols with meaningful sentiment", hits, len(to_fetch))
    except Exception as _av_e:
        log.debug("AV news tier skipped: %s", _av_e)

    # ── Phase 3: Assemble final scores ─────────────────────────
    for sym in to_fetch:
        if sym not in rss_results:
            # BC-6: RSS fetch failed (network error, timeout, etc.).
            # Zeroing Dimension 7 for every symbol in a failed batch biases scoring
            # against all names that cycle. If a stale cache entry exists, serve it
            # with a staleness flag rather than returning empty scores. Stale data
            # preserves the signal's prior information; zero discards it entirely.
            stale = _news_cache.get(sym)
            if stale:
                stale_data = dict(stale["data"])
                stale_data["stale"] = True  # downstream can flag this in IC audit
                results[sym] = stale_data
                age_min = (now - stale["fetched_at"]).total_seconds() / 60
                log.debug("BC-6: %s RSS failed — serving stale cache (%.0fm old)", sym, age_min)
            else:
                results[sym] = _empty_sentiment(sym)
            continue

        headlines, recency, kw = rss_results[sym]
        direction = directions.get(sym, "")
        av = av_results.get(sym.upper(), {})

        # Compute news_score: keyword + recency + AV (no Claude)
        news_score = 0
        if direction in ("LONG", ""):
            kw_contrib = max(0, min(5, kw["score"]))
        elif direction == "SHORT":
            kw_contrib = max(0, min(5, -kw["score"]))
        else:
            kw_contrib = 0
        news_score += kw_contrib

        # ── AV Tier 2 contribution (±3, direction-aware) ───────
        av_sentiment_score = av.get("sentiment_score", 0.0)
        av_relevance = av.get("relevance", 0.0)
        av_contrib = 0.0
        if av_relevance >= 0.15 and abs(av_sentiment_score) > 0.05:
            raw = av_sentiment_score * av_relevance * 3.0  # max ±3
            if direction in ("LONG", ""):
                av_contrib = raw
            elif direction == "SHORT":
                av_contrib = -raw
        news_score = max(0, min(10, news_score + av_contrib))

        if recency < 2:
            news_score = min(10, news_score + 2)
        elif recency < 4:
            news_score = min(10, news_score + 1)
        if recency > 24:
            news_score = max(0, news_score - 2)
        elif recency > 12:
            news_score = max(0, news_score - 1)

        result = {
            "symbol": sym,
            "headlines": headlines[:5],
            "headline_count": len(headlines),
            "recency_hours": round(recency, 1),
            "keyword_score": kw["score"],
            "keyword_hits": kw["keywords"],
            "news_finbert_sentiment": None,
            "news_finbert_confidence": None,
            "av_sentiment_score": round(av_sentiment_score, 4),
            "av_sentiment_label": av.get("sentiment_label", ""),
            "av_relevance": round(av_relevance, 4),
            "av_topics": av.get("topics", []),
            "news_score": min(10, max(0, news_score)),
        }

        results[sym] = result
        _news_cache[sym] = {"data": result, "fetched_at": now}

    return results
