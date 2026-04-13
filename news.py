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

import anthropic
import requests

from config import CONFIG

# ── MODULE-LEVEL CLAUDE CLIENT (created once, reused) ──────
_claude_client = None


def _get_claude_client():
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    return _claude_client


log = logging.getLogger("decifer.news")

# ── NEWS CACHE (avoid refetching within scan window) ───────
_news_cache = {}  # {symbol: {"data": {...}, "fetched_at": datetime}}
_CACHE_TTL_MIN = 15  # Cache results for 15 minutes (sentinel handles real-time news)

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


def claude_sentiment(symbol: str, headlines: list[str], direction: str = "") -> dict:
    """
    Tier 2: Claude deep sentiment read for symbols that pass keyword threshold.
    Returns {sentiment: BULLISH/BEARISH/NEUTRAL, confidence: 0-10, summary: str}.
    """
    if not headlines:
        return {"sentiment": "NEUTRAL", "confidence": 0, "summary": "No news"}

    headline_text = "\n".join([f"- {h}" for h in headlines[:8]])

    try:
        client = _get_claude_client()
        resp = client.messages.create(
            model=CONFIG["claude_model"],
            max_tokens=150,
            system=(
                "You are a Wall Street news sentiment analyst. "
                "Analyse headlines and output ONLY valid JSON. "
                "No explanation, no markdown, just JSON."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"""Symbol: {symbol}
Current signal direction: {direction}

Recent headlines:
{headline_text}

Output JSON:
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0-10, "catalyst": "one sentence max"}}""",
                }
            ],
        )
        raw = resp.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        result = json.loads(raw)
        return {
            "sentiment": result.get("sentiment", "NEUTRAL"),
            "confidence": min(10, max(0, int(result.get("confidence", 0)))),
            "summary": result.get("catalyst", ""),
        }

    except Exception as e:
        log.debug(f"Claude sentiment error for {symbol}: {e}")
        return {"sentiment": "NEUTRAL", "confidence": 0, "summary": ""}


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

    # Tier 2: Claude deep read (only if keywords are meaningful)
    claude = {"sentiment": "NEUTRAL", "confidence": 0, "summary": ""}
    if abs(kw["score"]) >= keyword_threshold and headlines:
        claude = claude_sentiment(symbol, headlines, direction)

    # Tier 2b: Finnhub company news — supplements Yahoo RSS with a second
    # news feed.  Uses the same keyword_score() pipeline on combined headlines.
    # Only fires when Finnhub key is present AND Yahoo RSS is empty or weak.
    finnhub_contrib = 0
    if (
        CONFIG.get("use_finnhub")
        and CONFIG.get("finnhub_api_key")
        and (not headlines or abs(kw["score"]) < keyword_threshold)
    ):
        try:
            import finnhub_client as _fh

            fh_articles = _fh.get_company_news(symbol, lookback_days=2)
            if fh_articles:
                # Use headline + first 120 chars of summary for richer context
                fh_headlines = [f"{a.get('headline', '')} {a.get('summary', '')[:120]}".strip() for a in fh_articles]
                fh_kw = keyword_score(fh_headlines)
                if abs(fh_kw["score"]) >= keyword_threshold:
                    # Finnhub has a clear signal where Yahoo RSS was ambiguous
                    raw_contrib = fh_kw["score"]
                    finnhub_contrib = max(-2, min(+2, raw_contrib // 2))
                    log.debug(
                        "Finnhub news %s: %d articles, kw_score=%+d → contrib=%+d",
                        symbol,
                        len(fh_articles),
                        fh_kw["score"],
                        finnhub_contrib,
                    )
        except Exception as _e:
            log.debug("Finnhub company news error for %s: %s", symbol, _e)

    # ── Compute final news_score (0-10) ──────────────────────
    news_score = 0

    # Keyword contribution (0-5)
    if direction in ("LONG", ""):
        kw_contrib = max(0, min(5, kw["score"]))
    elif direction == "SHORT":
        kw_contrib = max(0, min(5, -kw["score"]))  # Bearish news = positive for shorts
    else:
        kw_contrib = 0
    news_score += kw_contrib

    # Finnhub contribution (±2, direction-aware, only when keyword signal is weak)
    if finnhub_contrib != 0:
        if direction in ("LONG", ""):
            news_score = max(0, min(10, news_score + finnhub_contrib))
        elif direction == "SHORT":
            news_score = max(0, min(10, news_score - finnhub_contrib))

    # Claude contribution (0-5)
    if claude["sentiment"] != "NEUTRAL":
        sentiment_aligned = (
            (direction == "LONG" and claude["sentiment"] == "BULLISH")
            or (direction == "SHORT" and claude["sentiment"] == "BEARISH")
            or direction == ""
        )
        if sentiment_aligned:
            news_score += min(5, claude["confidence"] // 2)
        else:
            # News contradicts trade direction — penalise
            news_score = max(0, news_score - min(3, claude["confidence"] // 3))

    # Recency boost: fresh news (< 4 hours) gets bonus
    if recency < 2:
        news_score = min(10, news_score + 2)  # Breaking news
    elif recency < 4:
        news_score = min(10, news_score + 1)  # Recent

    # Decay: old news (> 24 hours) gets penalised
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
        "claude_sentiment": claude["sentiment"],
        "claude_confidence": claude["confidence"],
        "claude_catalyst": claude["summary"],
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
        "claude_sentiment": "NEUTRAL",
        "claude_confidence": 0,
        "claude_catalyst": "",
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
        """Fetch RSS and do tier-1 keyword scoring (no Claude call yet)."""
        try:
            articles = fetch_yahoo_rss(sym)
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

    # ── Phase 2: Claude tier-2 for top scorers (max 5) ─────────
    claude_candidates = []
    for sym, (headlines, _recency, kw) in rss_results.items():
        if abs(kw["score"]) >= 2 and headlines:
            claude_candidates.append((sym, headlines, kw["score"]))

    # Sort by keyword score strength, take top 5
    claude_candidates.sort(key=lambda x: abs(x[2]), reverse=True)
    claude_candidates = claude_candidates[:5]

    claude_results = {}
    if claude_candidates:

        def claude_one(sym, headlines, direction):
            try:
                return sym, claude_sentiment(sym, headlines, direction)
            except Exception as e:
                log.debug(f"Claude sentiment error for {sym}: {e}")
                return sym, {"sentiment": "NEUTRAL", "confidence": 0, "summary": ""}

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(claude_one, sym, hls, directions.get(sym, "")): sym for sym, hls, _ in claude_candidates
            }
            for future in as_completed(futures):
                sym, claude_res = future.result()
                claude_results[sym] = claude_res

    # ── Phase 2b: Alpha Vantage Tier 3 — structured NLP from professional sources ──
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
            results[sym] = _empty_sentiment(sym)
            continue

        headlines, recency, kw = rss_results[sym]
        direction = directions.get(sym, "")
        claude = claude_results.get(sym, {"sentiment": "NEUTRAL", "confidence": 0, "summary": ""})
        av = av_results.get(sym.upper(), {})

        # Compute news_score (same logic as get_news_sentiment)
        news_score = 0
        if direction in ("LONG", ""):
            kw_contrib = max(0, min(5, kw["score"]))
        elif direction == "SHORT":
            kw_contrib = max(0, min(5, -kw["score"]))
        else:
            kw_contrib = 0
        news_score += kw_contrib

        if claude["sentiment"] != "NEUTRAL":
            sentiment_aligned = (
                (direction == "LONG" and claude["sentiment"] == "BULLISH")
                or (direction == "SHORT" and claude["sentiment"] == "BEARISH")
                or direction == ""
            )
            if sentiment_aligned:
                news_score += min(5, claude["confidence"] // 2)
            else:
                news_score = max(0, news_score - min(3, claude["confidence"] // 3))

        # ── AV Tier 3 contribution (±3, direction-aware) ───────
        # Only fires when AV has meaningful relevance (>= 0.15) for this ticker.
        # sentiment_score is -1 to +1; scaled by relevance; max contribution ±3.
        av_sentiment_score = av.get("sentiment_score", 0.0)
        av_relevance = av.get("relevance", 0.0)
        av_contrib = 0.0
        if av_relevance >= 0.15 and abs(av_sentiment_score) > 0.05:
            raw = av_sentiment_score * av_relevance * 3.0  # max ±3
            if direction in ("LONG", ""):
                av_contrib = raw
            elif direction == "SHORT":
                av_contrib = -raw  # bearish AV signal = positive for shorts
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
            "claude_sentiment": claude["sentiment"],
            "claude_confidence": claude["confidence"],
            "claude_catalyst": claude.get("summary", ""),
            "av_sentiment_score": round(av_sentiment_score, 4),
            "av_sentiment_label": av.get("sentiment_label", ""),
            "av_relevance": round(av_relevance, 4),
            "av_topics": av.get("topics", []),
            "news_score": min(10, max(0, news_score)),
        }

        results[sym] = result
        _news_cache[sym] = {"data": result, "fetched_at": now}

    return results
