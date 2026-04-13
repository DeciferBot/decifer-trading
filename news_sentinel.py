# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  news_sentinel.py                     ║
# ║   IBKR news feed + materiality analysis + Claude deep-read. ║
# ║   Real-time Alpaca/Benzinga feed is handled by              ║
# ║   alpaca_news.AlpacaNewsStream (primary source).            ║
# ║   This module owns: IBKR news, assess_materiality,          ║
# ║   deep_read_trigger, and the NewsSentinel polling class.    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from config import CONFIG
from news import BEARISH_STRONG, BULLISH_STRONG, claude_sentiment, keyword_score
from news_infrastructure import HeadlineDeduplicator, SymbolCooldown, headline_hash

log = logging.getLogger("decifer.sentinel")

# ═══════════════════════════════════════════════════════════════
# SHARED INFRASTRUCTURE INSTANCES
# ═══════════════════════════════════════════════════════════════
_dedup = HeadlineDeduplicator(max_size=5000)
_cooldown = SymbolCooldown(cooldown_minutes=CONFIG.get("sentinel_cooldown_minutes", 10))
_headline_history: deque = deque(maxlen=200)  # recent triggers for dashboard

# ═══════════════════════════════════════════════════════════════
# MATERIALITY THRESHOLDS — what qualifies as "material news"
# ═══════════════════════════════════════════════════════════════
# A headline must score at least this high (absolute) on keyword scoring
# to be considered material enough to interrupt the scan cycle.
KEYWORD_THRESHOLD = CONFIG.get("sentinel_keyword_threshold", 3)
# Or if Claude rates confidence >= this, it's material regardless
CLAUDE_CONFIDENCE_THRESHOLD = CONFIG.get("sentinel_claude_confidence", 7)


# Thin compatibility aliases — used internally below
def _headline_hash(headline: str) -> str:
    return headline_hash(headline)


def _is_on_cooldown(symbol: str) -> bool:
    return _cooldown.is_on_cooldown(symbol)


def _set_cooldown(symbol: str) -> None:
    _cooldown.set_cooldown(symbol)


# ═══════════════════════════════════════════════════════════════
# SOURCE: IBKR NEWS API (via ib_async)
# ═══════════════════════════════════════════════════════════════
def fetch_ibkr_news(ib, symbol: str) -> list[dict]:
    """
    Fetch news from IBKR's built-in news feed for a given symbol.
    Uses ib_async reqHistoricalNews / reqNewsArticle.
    Returns list of {title, published, link, age_hours, symbol}.
    """
    if ib is None or not ib.isConnected():
        return []

    try:
        from ib_async import Stock

        contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)

        now = datetime.now(UTC)
        start = (now - timedelta(hours=4)).strftime("%Y%m%d-%H:%M:%S")
        end = now.strftime("%Y%m%d-%H:%M:%S")

        # Request historical news headlines (last 4 hours)
        headlines = ib.reqHistoricalNews(
            contract.conId,
            providerCodes="BZ+FLY+DJ+MT+BRF",  # Benzinga, FlyOnTheWall, DowJones, MT, Briefing
            startDateTime=start,
            endDateTime=end,
            totalResults=10,
        )

        articles = []
        for item in headlines or []:
            title = getattr(item, "headline", "") or ""
            pub_time = getattr(item, "time", None)

            age_hours = 0.5
            if pub_time:
                try:
                    if isinstance(pub_time, datetime):
                        age_hours = (now - pub_time.replace(tzinfo=UTC)).total_seconds() / 3600
                    else:
                        pub_dt = datetime.strptime(str(pub_time)[:19], "%Y%m%d %H:%M:%S").replace(tzinfo=UTC)
                        age_hours = (now - pub_dt).total_seconds() / 3600
                except Exception:
                    pass

            if title:
                articles.append(
                    {
                        "title": title,
                        "published": str(pub_time) if pub_time else "",
                        "link": "",
                        "age_hours": round(age_hours, 1),
                        "symbol": symbol,
                        "source": "ibkr",
                    }
                )

        return articles
    except Exception as e:
        log.debug(f"IBKR news error for {symbol}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# CORE: SCAN IBKR NEWS FOR A BATCH OF SYMBOLS
# ═══════════════════════════════════════════════════════════════
def scan_all_sources(symbols: list[str], ib=None) -> list[dict]:
    """
    Parallel IBKR news fetch for a batch of symbols.
    Returns only NEW headlines (not seen before).
    Yahoo RSS and Finviz removed — replaced by AlpacaNewsStream.
    """
    all_articles = []

    if ib is None:
        return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_ibkr_news, ib, sym): sym for sym in symbols}
        for future in as_completed(futures):
            try:
                all_articles.extend(future.result())
            except Exception as e:
                log.debug(f"Sentinel IBKR fetch error: {e}")

    new_articles = []
    for art in all_articles:
        if _dedup.add_if_new(art["title"]):
            new_articles.append(art)

    return new_articles


# ═══════════════════════════════════════════════════════════════
# MATERIALITY FILTER — is this headline worth triggering for?
# ═══════════════════════════════════════════════════════════════
def assess_materiality(articles: list[dict]) -> list[dict]:
    """
    Score new headlines and return only those that are "material" —
    strong enough sentiment to warrant an immediate interrupt.

    Returns list of trigger events:
    {symbol, headlines, keyword_result, direction, urgency, source}
    """
    # Group articles by symbol
    by_symbol: dict[str, list[dict]] = {}
    for art in articles:
        sym = art["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(art)

    triggers = []

    for sym, arts in by_symbol.items():
        # Skip if on cooldown
        if _is_on_cooldown(sym):
            continue

        # Only consider recent news (< 2 hours old)
        recent = [a for a in arts if a["age_hours"] < 2]
        if not recent:
            continue

        headlines = [a["title"] for a in recent]
        kw = keyword_score(headlines)

        # ── MATERIALITY CHECK ──────────────────────────────────
        is_material = False
        urgency = "MODERATE"

        # Check 1: Strong keyword score (absolute)
        if abs(kw["score"]) >= KEYWORD_THRESHOLD:
            is_material = True
            if abs(kw["score"]) >= 6:
                urgency = "CRITICAL"

        # Check 2: Contains strong keywords (even if net score is low)
        headline_text = " ".join(headlines).lower()
        has_strong_bull = any(kw in headline_text for kw in BULLISH_STRONG)
        has_strong_bear = any(kw in headline_text for kw in BEARISH_STRONG)
        if has_strong_bull or has_strong_bear:
            is_material = True
            urgency = "HIGH"

        # Check 3: Multiple headlines about the same symbol = event
        if len(recent) >= 3:
            is_material = True  # Cluster of news = something is happening

        if not is_material:
            continue

        # Determine direction from keyword scoring
        if kw["score"] > 0:
            direction = "BULLISH"
        elif kw["score"] < 0:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        sources = list(set(a["source"] for a in recent))

        triggers.append(
            {
                "symbol": sym,
                "headlines": headlines[:5],
                "headline_count": len(headlines),
                "keyword_score": kw["score"],
                "keyword_hits": kw["keywords"][:8],
                "direction": direction,
                "urgency": urgency,
                "sources": sources,
                "age_hours": min(a["age_hours"] for a in recent),
                "triggered_at": datetime.now(UTC).isoformat(),
            }
        )

    # Sort by urgency then keyword score strength
    urgency_rank = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2}
    triggers.sort(key=lambda t: (urgency_rank.get(t["urgency"], 3), -abs(t["keyword_score"])))

    return triggers


# ═══════════════════════════════════════════════════════════════
# CLAUDE DEEP-READ FOR CONFIRMED TRIGGERS
# ═══════════════════════════════════════════════════════════════
def deep_read_trigger(trigger: dict) -> dict:
    """
    Run Claude sentiment analysis on a confirmed trigger.
    Returns the trigger enriched with Claude's analysis.
    """
    headlines = trigger["headlines"]
    sym = trigger["symbol"]

    claude_result = claude_sentiment(sym, headlines, trigger["direction"])

    trigger["claude_sentiment"] = claude_result.get("sentiment", "NEUTRAL")
    trigger["claude_confidence"] = claude_result.get("confidence", 0)
    trigger["claude_catalyst"] = claude_result.get("summary", "")

    # ── Upgrade/downgrade urgency based on Claude confidence ──
    if claude_result.get("confidence", 0) >= CLAUDE_CONFIDENCE_THRESHOLD:
        if trigger["urgency"] == "MODERATE":
            trigger["urgency"] = "HIGH"
    elif claude_result.get("confidence", 0) <= 3 and trigger["urgency"] != "CRITICAL":
        trigger["urgency"] = "LOW"

    return trigger


# ═══════════════════════════════════════════════════════════════
# SENTINEL LOOP — the main monitoring thread
# ═══════════════════════════════════════════════════════════════
class NewsSentinel:
    """
    Runs as a background thread, continuously monitoring news
    for the tracked universe. When material news is detected,
    fires a callback to the bot for immediate action.

    Usage:
        sentinel = NewsSentinel(
            get_universe_fn=lambda: [...symbols...],
            on_trigger_fn=handle_news_trigger,
            ib=ib
        )
        sentinel.start()
    """

    def __init__(self, get_universe_fn, on_trigger_fn, ib=None, poll_interval: int | None = None):
        """
        get_universe_fn: callable returning list of symbols to monitor
        on_trigger_fn:   callable(trigger_dict) — called when material news fires
        ib:              ib_async IB instance for IBKR news (optional)
        poll_interval:   seconds between polls (default from config)
        """
        self.get_universe = get_universe_fn
        self.on_trigger = on_trigger_fn
        self.ib = ib
        self.poll_interval = poll_interval or CONFIG.get("sentinel_poll_seconds", 45)
        self._running = False
        self._thread = None
        self._trigger_count = 0
        self._last_poll = None
        self._paused = False

        # Stats for dashboard
        self.stats = {
            "status": "stopped",
            "polls": 0,
            "triggers_fired": 0,
            "last_poll": None,
            "last_trigger": None,
            "symbols_monitored": 0,
            "headlines_seen": 0,
            "recent_triggers": [],
        }

    def start(self):
        """Start the sentinel in a background daemon thread."""
        if self._running:
            log.warning("Sentinel already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="NewsSentinel")
        self._thread.start()
        self.stats["status"] = "running"
        log.info(f"📡 News Sentinel started | poll every {self.poll_interval}s")

    def stop(self):
        """Stop the sentinel gracefully."""
        self._running = False
        self.stats["status"] = "stopped"
        log.info("📡 News Sentinel stopped")

    def pause(self):
        self._paused = True
        self.stats["status"] = "paused"

    def resume(self):
        self._paused = False
        self.stats["status"] = "running"

    def _loop(self):
        """Main polling loop."""
        while self._running:
            try:
                if self._paused:
                    time.sleep(5)
                    continue

                # Get current universe to monitor
                universe = self.get_universe()
                self.stats["symbols_monitored"] = len(universe)

                if not universe:
                    time.sleep(self.poll_interval)
                    continue

                # ── Split universe into batches (8 symbols per poll) ──
                # Rotate through the universe so we cover everything
                # but don't overwhelm the news APIs
                batch_size = CONFIG.get("sentinel_batch_size", 10)
                poll_num = self.stats["polls"]
                start_idx = (poll_num * batch_size) % max(1, len(universe))
                batch = universe[start_idx : start_idx + batch_size]

                # If batch wraps around, grab from beginning too
                if len(batch) < batch_size:
                    batch += universe[: batch_size - len(batch)]

                # Deduplicate
                batch = list(dict.fromkeys(batch))

                # ── Scan all sources ──────────────────────────────
                new_articles = scan_all_sources(batch, ib=self.ib)

                self.stats["headlines_seen"] = len(_dedup)
                self.stats["polls"] += 1
                self.stats["last_poll"] = datetime.now().strftime("%H:%M:%S")
                self._last_poll = datetime.now()

                if not new_articles:
                    time.sleep(self.poll_interval)
                    continue

                log.debug(f"Sentinel: {len(new_articles)} new headlines from batch {batch[:5]}...")

                # ── Assess materiality ────────────────────────────
                triggers = assess_materiality(new_articles)

                if not triggers:
                    time.sleep(self.poll_interval)
                    continue

                # ── Deep-read confirmed triggers with Claude ──────
                for trigger in triggers:
                    if trigger["urgency"] == "LOW":
                        continue  # Skip low-urgency without Claude call

                    trigger = deep_read_trigger(trigger)

                    # Final gate: Claude must confirm materiality
                    if trigger.get("claude_confidence", 0) < 4 and trigger["urgency"] != "CRITICAL":
                        log.info(
                            f"Sentinel: {trigger['symbol']} — Claude confidence too low "
                            f"({trigger.get('claude_confidence', 0)}), skipping"
                        )
                        continue

                    # ── FIRE TRIGGER ──────────────────────────────
                    _set_cooldown(trigger["symbol"])
                    self._trigger_count += 1
                    self.stats["triggers_fired"] = self._trigger_count
                    self.stats["last_trigger"] = {
                        "symbol": trigger["symbol"],
                        "direction": trigger["direction"],
                        "urgency": trigger["urgency"],
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "catalyst": trigger.get("claude_catalyst", trigger["headlines"][0][:60]),
                    }

                    # Keep recent triggers for dashboard
                    self.stats["recent_triggers"].insert(0, self.stats["last_trigger"])
                    self.stats["recent_triggers"] = self.stats["recent_triggers"][:20]

                    # Record in headline history
                    _headline_history.appendleft(
                        {
                            "symbol": trigger["symbol"],
                            "headline": trigger["headlines"][0] if trigger["headlines"] else "",
                            "direction": trigger["direction"],
                            "urgency": trigger["urgency"],
                            "time": datetime.now().strftime("%H:%M:%S"),
                        }
                    )

                    log.info(
                        f"🚨 SENTINEL TRIGGER: {trigger['symbol']} | "
                        f"{trigger['direction']} | urgency={trigger['urgency']} | "
                        f"kw_score={trigger['keyword_score']} | "
                        f"claude={trigger.get('claude_sentiment', '?')}({trigger.get('claude_confidence', 0)}) | "
                        f"catalyst: {trigger.get('claude_catalyst', trigger['headlines'][0][:60])}"
                    )

                    # Fire the callback to bot.py
                    try:
                        self.on_trigger(trigger)
                    except Exception as e:
                        log.error(f"Sentinel trigger callback error: {e}")

            except Exception as e:
                log.error(f"Sentinel loop error: {e}")

            time.sleep(self.poll_interval)


# ═══════════════════════════════════════════════════════════════
# UTILITY: Get headline history for dashboard
# ═══════════════════════════════════════════════════════════════
def get_sentinel_history() -> list[dict]:
    """Return recent sentinel trigger history for dashboard display."""
    return list(_headline_history)
