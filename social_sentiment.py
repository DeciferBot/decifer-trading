# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  social_sentiment.py                     ║
# ║   Reddit + ApeWisdom + VADER sentiment analysis              ║
# ║   Tracks mention VELOCITY (acceleration, not raw count)       ║
# ║   Plugs into signals.py as 8th dimension for confluence       ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import re
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

try:
    import requests
except ImportError:
    raise ImportError("requests library required. pip install requests") from None

try:
    from nltk.sentiment import SentimentIntensityAnalyzer

    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

log = logging.getLogger("decifer.sentiment")

# ═══════════════════════════════════════════════════════════════
# FINANCE-SPECIFIC SENTIMENT LEXICON
# These override default VADER scores for trading context
# ═══════════════════════════════════════════════════════════════
FINANCE_LEXICON = {
    # Bullish signals (strongly positive in trading context)
    "moon": 3.0,
    "mooning": 3.0,
    "moonshot": 3.0,
    "diamond hands": 2.5,
    "diamond": 2.0,
    "rocket": 2.5,
    "squeeze": 2.0,
    "short squeeze": 3.0,
    "breakout": 2.0,
    "bull": 2.0,
    "bullish": 2.0,
    "bullrun": 2.5,
    "bull run": 2.5,
    "pump": 1.5,
    "upside": 1.5,
    "recovery": 1.5,
    "reversal": 1.5,
    "bounce": 1.5,
    "momentum": 1.5,
    "surge": 2.0,
    "spike": 1.5,
    "rip": 2.0,
    "lambo": 2.5,
    "tendies": 2.0,
    "gains": 2.0,
    "profitable": 1.5,
    "profit": 1.0,
    "rally": 2.0,
    "rebound": 1.5,
    "oversold": 1.5,
    "support": 1.0,
    "strong support": 1.5,
    "resistance": 1.0,
    "catalyst": 1.5,
    "accumulation": 1.5,
    "institutional": 1.5,
    "insiders buying": 2.0,
    "insider buying": 2.0,
    "buyback": 1.5,
    "beat estimates": 1.5,
    "beat earnings": 1.5,
    "earnings beat": 1.5,
    # Bearish signals (negative in trading context)
    "bear": -2.0,
    "bearish": -2.0,
    "bearrun": -2.5,
    "bear run": -2.5,
    "dump": -2.0,
    "dumping": -2.0,
    "panic selling": -2.0,
    "panic sell": -2.0,
    "crash": -2.5,
    "crashing": -2.5,
    "collapse": -3.0,
    "bankruptcy": -3.0,
    "bankrupt": -3.0,  # Context dependent but often bullish for longs
    "short": -1.5,
    "shorting": -2.0,
    "short ladder": -2.5,
    "naked short": -2.5,
    "bag holder": -2.5,
    "bagholder": -2.5,
    "rekt": -2.5,
    "liquidation": -2.0,
    "liquidated": -2.0,
    "margin call": -2.0,
    "insolvency": -3.0,
    "dilution": -1.5,
    "diluted": -1.5,
    "downtrend": -2.0,
    "downside": -1.5,
    "sell-off": -2.0,
    "selloff": -2.0,
    "sell": -0.5,
    "selling": -0.5,  # Can be bearish or neutral depending on context
    "death cross": -2.0,
    "overbought": -1.5,
    "oversupply": -1.5,
    "overvalued": -1.5,
    "miss estimates": -1.5,
    "miss earnings": -1.5,
    "earnings miss": -1.5,
    "guidance cut": -2.0,
    "forecast cut": -2.0,
    "warning": -1.5,
    "recall": -2.0,
    "lawsuit": -1.5,
    "fraud": -3.0,
    "scandal": -2.5,
    "investigation": -2.0,
    "sec investigation": -2.5,
}


# ═══════════════════════════════════════════════════════════════
# MENTION VELOCITY TRACKER
# Tracks mention counts over time windows to detect acceleration
# ═══════════════════════════════════════════════════════════════
class MentionVelocityTracker:
    """
    Tracks mention history per ticker to compute velocity (acceleration).
    A stock going 5→50 mentions/hr is a signal; 50 steady is not.
    """

    def __init__(self, max_history: int = 24):
        self.history = defaultdict(lambda: deque(maxlen=max_history))  # (timestamp, count)
        self.lock = threading.Lock()

    def record_mention(self, ticker: str, count: int):
        """Record mention count at current timestamp."""
        with self.lock:
            now = datetime.now(UTC)
            self.history[ticker].append((now, count))

    def get_velocity(self, ticker: str, window_hours: float = 1.0) -> float:
        """
        Compute mention velocity: acceleration of mention rate over time window.
        Returns 0-10 score.

        Calculation:
        - Get mention counts for past N hours
        - Fit trend line to detect acceleration
        - Return 0 (steady) to 10 (rapidly accelerating)
        """
        with self.lock:
            if ticker not in self.history or len(self.history[ticker]) < 2:
                return 0.0

            now = datetime.now(UTC)
            now - timedelta(hours=window_hours)

            # Filter to time window
            window = [(t, c) for t, c in self.history[ticker] if (now - t).total_seconds() <= window_hours * 3600]

            if len(window) < 2:
                return 0.0

            # Simple velocity: compare first and last mention counts
            first_count = window[0][1]
            last_count = window[-1][1]

            if first_count == 0:
                return 0.0

            acceleration_ratio = last_count / max(first_count, 1)

            # Map acceleration to 0-10 scale
            # 1x (no change) = 0
            # 2x = 3
            # 5x = 6
            # 10x+ = 10
            velocity = min(10.0, max(0.0, (acceleration_ratio - 1.0) * 3.0))

            return velocity

    def get_hourly_velocity(self, ticker: str) -> float:
        """Get 1-hour velocity metric."""
        return self.get_velocity(ticker, window_hours=1.0)

    def get_24h_velocity(self, ticker: str) -> float:
        """Get 24-hour velocity metric."""
        return self.get_velocity(ticker, window_hours=24.0)


# ═══════════════════════════════════════════════════════════════
# VADER SENTIMENT ANALYZER WITH FINANCE LEXICON
# ═══════════════════════════════════════════════════════════════
class FinanceVADER:
    """
    Wrapper around NLTK VADER with finance-specific lexicon.
    Falls back to keyword-based sentiment if VADER unavailable.
    """

    def __init__(self):
        self.vader_available = VADER_AVAILABLE
        if self.vader_available:
            try:
                self.analyzer = SentimentIntensityAnalyzer()
                # Update with finance lexicon
                self.analyzer.lexicon.update({word: score for word, score in FINANCE_LEXICON.items()})
            except Exception as e:
                log.warning(f"VADER init failed: {e}, falling back to keyword sentiment")
                self.vader_available = False

    def get_sentiment(self, text: str) -> float:
        """
        Compute sentiment score for text: -1 (bearish) to +1 (bullish).
        """
        if not text or not isinstance(text, str):
            return 0.0

        text_lower = text.lower()

        if self.vader_available:
            try:
                scores = self.analyzer.polarity_scores(text_lower)
                # compound score is already -1 to 1
                return scores.get("compound", 0.0)
            except Exception as e:
                log.debug(f"VADER sentiment error: {e}")

        # Fallback: simple keyword-based sentiment
        return self._keyword_sentiment(text_lower)

    def _keyword_sentiment(self, text: str) -> float:
        """
        Fallback: simple keyword matching for sentiment.
        Returns -1 to 1.
        """
        score = 0.0

        for word, weight in FINANCE_LEXICON.items():
            if word in text:
                score += weight

        # Normalize to -1 to 1
        if score > 0:
            return min(1.0, score / 10.0)
        else:
            return max(-1.0, score / 10.0)

    def get_sentiment_batch(self, texts: list[str]) -> float:
        """
        Compute average sentiment across multiple texts.
        Returns -1 to 1.
        """
        if not texts:
            return 0.0

        scores = [self.get_sentiment(text) for text in texts]
        return sum(scores) / len(scores) if scores else 0.0


# ═══════════════════════════════════════════════════════════════
# REDDIT API FETCHER
# No authentication required, Reddit's JSON endpoint is public
# ═══════════════════════════════════════════════════════════════
class RedditFetcher:
    """
    Fetch Reddit posts from public subreddits via JSON endpoint.
    Rate limit: 100 req/min per Reddit TOS.
    """

    BASE_URL = "https://www.reddit.com"
    SUBREDDITS = ["wallstreetbets", "stocks", "options", "investing", "pennystocks"]

    def __init__(self, rate_limit_per_min: int = 100):
        self.rate_limit_per_min = rate_limit_per_min
        self.request_times = deque(maxlen=rate_limit_per_min)
        self.lock = threading.Lock()

    def _check_rate_limit(self):
        """Enforce rate limiting: max N requests per minute."""
        with self.lock:
            now = time.time()
            # Remove old timestamps outside 1-minute window
            while self.request_times and now - self.request_times[0] > 60:
                self.request_times.popleft()

            if len(self.request_times) >= self.rate_limit_per_min:
                sleep_time = 60 - (now - self.request_times[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)

            self.request_times.append(now)

    def fetch_subreddit(self, subreddit: str, limit: int = 100) -> list[dict]:
        """
        Fetch posts from a subreddit.
        Returns list of {title, author, score, url, timestamp, text}.
        """
        self._check_rate_limit()

        url = f"{self.BASE_URL}/r/{subreddit}/new.json"
        headers = {"User-Agent": "Decifer/2.0 SocialSentiment (trading bot)"}

        try:
            resp = requests.get(url, headers=headers, timeout=5, params={"limit": limit})
            if resp.status_code != 200:
                log.warning(f"Reddit fetch failed for r/{subreddit}: {resp.status_code}")
                return []

            data = resp.json()
            posts = []

            for item in data.get("data", {}).get("children", []):
                if item["kind"] != "t3":  # Skip non-post items
                    continue

                post = item["data"]
                posts.append(
                    {
                        "title": post.get("title", ""),
                        "author": post.get("author", ""),
                        "score": post.get("score", 0),
                        "url": post.get("url", ""),
                        "timestamp": post.get("created_utc", 0),
                        "text": post.get("selftext", ""),
                        "subreddit": post.get("subreddit", subreddit),
                    }
                )

            return posts

        except Exception as e:
            log.error(f"Reddit fetch error for r/{subreddit}: {e}")
            return []

    def fetch_all_subreddits(self, limit: int = 50) -> list[dict]:
        """Fetch from all monitored subreddits."""
        all_posts = []
        for subreddit in self.SUBREDDITS:
            posts = self.fetch_subreddit(subreddit, limit=limit)
            all_posts.extend(posts)
        return all_posts


# ═══════════════════════════════════════════════════════════════
# APEWISDOM API FETCHER
# Returns real-time ticker mention counts from Reddit
# ═══════════════════════════════════════════════════════════════
class ApeWisdomFetcher:
    """
    Fetch ticker mentions from ApeWisdom API.
    Pre-computed aggregation of Reddit mentions across many subreddits.
    No API key required.
    """

    BASE_URL = "https://apewisdom.io/api/v1.0"

    def fetch_trending(self, limit: int = 50) -> dict[str, dict]:
        """
        Fetch trending tickers with mention counts.
        Returns {ticker: {mentions: int, rank: int, ...}}
        """
        url = f"{self.BASE_URL}/filter/all-stocks"
        headers = {"User-Agent": "Decifer/2.0 SocialSentiment"}

        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code != 200:
                log.warning(f"ApeWisdom fetch failed: {resp.status_code}")
                return {}

            data = resp.json()
            trending = {}

            for idx, item in enumerate(data.get("data", [])[:limit]):
                ticker = item.get("symbol", "").upper()
                if not ticker:
                    continue

                trending[ticker] = {
                    "mentions": item.get("count", 0),
                    "rank": idx + 1,
                    "sentiment": item.get("sentiment", 0),  # Some versions include this
                    "change_24h": item.get("percent_change", 0),
                }

            return trending

        except Exception as e:
            log.error(f"ApeWisdom fetch error: {e}")
            return {}


# ═══════════════════════════════════════════════════════════════
# SOCIAL SENTIMENT TRACKER — Main Engine
# Maintains state across polling cycles, computes scores
# ═══════════════════════════════════════════════════════════════
class SocialSentimentTracker:
    """
    Main social sentiment engine.
    Maintains caches, velocity tracking, and returns consolidated sentiment scores.

    Returns dict like:
    {
        "AAPL": {
            "social_score": 7.2,      # 0-10 combined score
            "mention_velocity": 3.2,  # 0-10 acceleration metric
            "sentiment": 0.68,        # -1 to 1
            "mentions_1h": 45,
            "mentions_24h": 200,
            "top_posts": [{"title": "...", "score": 100, ...}],
            "timestamp": "2026-03-26 14:30:00 UTC"
        }
    }
    """

    def __init__(self, cache_ttl_seconds: int = 300, background_poll_interval: int = 60):
        self.cache_ttl = cache_ttl_seconds
        self.poll_interval = background_poll_interval

        self.reddit = RedditFetcher()
        self.apewisdom = ApeWisdomFetcher()
        self.vader = FinanceVADER()
        self.velocity_tracker = MentionVelocityTracker()

        # Cache
        self._cache = {}
        self._cache_time = {}
        self._lock = threading.Lock()

        # Mention dedup per polling cycle
        self._mention_cache = defaultdict(lambda: {"count_1h": 0, "count_24h": 0, "posts": []})
        self._mention_cache_time = {}

        # Background polling thread
        self._polling_thread = None
        self._running = False

        # Stats
        self.stats = {
            "reddit_posts_fetched": 0,
            "apewisdom_lookups": 0,
            "unique_tickers": 0,
            "last_poll": None,
            "polls_completed": 0,
            "errors": 0,
        }

    def start_background_polling(self):
        """Start background thread that polls sources every N seconds."""
        if self._running:
            log.warning("Polling already running")
            return

        self._running = True
        self._polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._polling_thread.start()
        log.info("Social sentiment background polling started")

    def stop_background_polling(self):
        """Stop background polling thread."""
        self._running = False
        if self._polling_thread:
            self._polling_thread.join(timeout=5)
        log.info("Social sentiment background polling stopped")

    def _polling_loop(self):
        """Background thread that periodically updates mention cache."""
        while self._running:
            try:
                self._update_mention_cache()
                self.stats["polls_completed"] += 1
                self.stats["last_poll"] = datetime.now(UTC).isoformat()
            except Exception as e:
                log.error(f"Polling loop error: {e}")
                self.stats["errors"] += 1

            time.sleep(self.poll_interval)

    def _update_mention_cache(self):
        """Fetch and cache mention counts and posts from all sources."""
        with self._lock:
            now = time.time()

            # Fetch Reddit posts
            reddit_posts = self.reddit.fetch_all_subreddits(limit=50)
            self.stats["reddit_posts_fetched"] = len(reddit_posts)

            # Extract tickers and accumulate mentions
            new_mentions = defaultdict(lambda: {"count": 0, "posts": []})

            for post in reddit_posts:
                title_lower = post["title"].lower()
                text_lower = post["text"].lower()
                combined = f"{title_lower} {text_lower}"

                # Extract ticker symbols (simple regex: $SYMBOL or SYMBOL surrounded by word boundaries)
                # More sophisticated: look for 1-5 uppercase letters
                tickers = re.findall(
                    r"\$([A-Z]{1,5})\b|\b([A-Z]{1,5})\b(?=.*(?:stock|ticker|shares|shares|calls|puts|puts|options|buy|sell))",
                    combined,
                )

                for match in tickers:
                    ticker = match[0] if match[0] else match[1]
                    ticker = ticker.upper()

                    if len(ticker) < 2 or len(ticker) > 5:
                        continue

                    new_mentions[ticker]["count"] += 1
                    new_mentions[ticker]["posts"].append(
                        {
                            "title": post["title"],
                            "author": post["author"],
                            "score": post["score"],
                            "subreddit": post["subreddit"],
                            "url": post["url"],
                        }
                    )

            # Also fetch from ApeWisdom for additional signals
            ape_trending = self.apewisdom.fetch_trending(limit=50)
            self.stats["apewisdom_lookups"] += 1

            for ticker, ape_data in ape_trending.items():
                if ticker not in new_mentions:
                    new_mentions[ticker]["count"] = ape_data.get("mentions", 0)
                else:
                    # Blend Reddit direct count with ApeWisdom count (ApeWisdom has broader coverage)
                    reddit_count = new_mentions[ticker]["count"]
                    ape_count = ape_data.get("mentions", 0)
                    # Use ApeWisdom as authoritative for volume
                    new_mentions[ticker]["count"] = max(reddit_count, ape_count)

            # Record history and compute velocity
            for ticker, data in new_mentions.items():
                self.velocity_tracker.record_mention(ticker, data["count"])

            # Update cache
            self._mention_cache = new_mentions
            self._mention_cache_time = now

            self.stats["unique_tickers"] = len(new_mentions)

    def _is_cache_valid(self) -> bool:
        """Check if cached mention data is still fresh."""
        if not self._mention_cache_time:
            return False
        return (time.time() - self._mention_cache_time) < self.cache_ttl

    def get_social_sentiment(self, symbols: list[str], force_refresh: bool = False) -> dict:
        """
        Compute social sentiment for a list of symbols.
        Returns dict of {symbol: sentiment_data}.
        """
        symbols = [s.upper() for s in symbols]

        # Refresh cache if stale or forced
        if force_refresh or not self._is_cache_valid():
            self._update_mention_cache()

        result = {}

        with self._lock:
            for symbol in symbols:
                mention_data = self._mention_cache.get(symbol, {"count": 0, "posts": []})

                mention_count = mention_data.get("count", 0)
                posts = mention_data.get("posts", [])

                # Sentiment from post titles and text
                post_texts = [p.get("title", "") for p in posts]
                sentiment = self.vader.get_sentiment_batch(post_texts)

                # Mention velocity
                velocity_1h = self.velocity_tracker.get_hourly_velocity(symbol)
                self.velocity_tracker.get_24h_velocity(symbol)

                # Composite social_score: blend of mention count, velocity, and sentiment
                # Formula: (mention_count_norm * 0.3) + (velocity * 0.4) + (sentiment_norm * 0.3)
                # Mention count: 0-10 (log scale, >100 mentions = 10)
                mention_score = min(10.0, (mention_count / 10.0) if mention_count > 0 else 0.0)
                # Sentiment: -1 to 1, normalize to 0-10
                sentiment_score = (sentiment + 1.0) / 2.0 * 10.0

                # Combine
                social_score = mention_score * 0.3 + velocity_1h * 0.4 + sentiment_score * 0.3

                # Top posts (sorted by score)
                top_posts = sorted(posts, key=lambda p: p.get("score", 0), reverse=True)[:5]

                result[symbol] = {
                    "social_score": round(social_score, 2),
                    "mention_velocity": round(velocity_1h, 2),
                    "sentiment": round(sentiment, 2),
                    "mentions_1h": mention_count,  # Simplified: using total count
                    "mentions_24h": mention_count * 2,  # Estimate (in real scenario, track separately)
                    "top_posts": top_posts,
                    "timestamp": datetime.now(UTC).isoformat(),
                }

        return result

    def get_trending(self, limit: int = 10) -> dict:
        """Get top trending tickers by social_score."""
        # Refresh cache
        if not self._is_cache_valid():
            self._update_mention_cache()

        with self._lock:
            tickers = list(self._mention_cache.keys())

        # Get sentiment for all
        sentiment = self.get_social_sentiment(tickers)

        # Sort by social_score
        sorted_tickers = sorted(sentiment.items(), key=lambda x: x[1]["social_score"], reverse=True)

        return {ticker: data for ticker, data in sorted_tickers[:limit]}


# ═══════════════════════════════════════════════════════════════
# GLOBAL SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════
_tracker_instance = None
_tracker_lock = threading.Lock()


def get_sentiment_tracker() -> SocialSentimentTracker:
    """Get or create global sentiment tracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        with _tracker_lock:
            if _tracker_instance is None:
                _tracker_instance = SocialSentimentTracker()
    return _tracker_instance


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════
def get_social_sentiment(symbols: list[str]) -> dict:
    """
    Main entry point: get social sentiment for symbols.
    Returns dict: {symbol: {social_score, sentiment, mentions_1h, ...}}

    Usage:
        result = get_social_sentiment(["AAPL", "GME", "TSLA"])
        print(result["AAPL"]["social_score"])
    """
    tracker = get_sentiment_tracker()
    return tracker.get_social_sentiment(symbols)


def start_sentiment_polling(poll_interval: int = 60):
    """Start background polling of Reddit and ApeWisdom."""
    tracker = get_sentiment_tracker()
    tracker.poll_interval = poll_interval
    tracker.start_background_polling()


def stop_sentiment_polling():
    """Stop background polling."""
    tracker = get_sentiment_tracker()
    tracker.stop_background_polling()


def get_trending_sentiment(limit: int = 10) -> dict:
    """Get top trending tickers by social sentiment."""
    tracker = get_sentiment_tracker()
    return tracker.get_trending(limit=limit)


# ═══════════════════════════════════════════════════════════════
# CLI / TESTING
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    print("\n" + "=" * 70)
    print("  DECIFER 2.0 — Social Sentiment Module")
    print("  Reddit + ApeWisdom + VADER Sentiment Analysis")
    print("=" * 70)

    # Parse arguments
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
        print(f"\nFetching sentiment for: {symbols}")
        result = get_social_sentiment(symbols)

        for symbol, data in result.items():
            print(f"\n{symbol}:")
            print(f"  Social Score:       {data['social_score']}/10.0")
            print(f"  Sentiment:          {data['sentiment']:+.2f}")
            print(f"  Mention Velocity:   {data['mention_velocity']:.2f}")
            print(f"  Mentions (1h):      {data['mentions_1h']}")
            print(f"  Sentiment Basis:    {len(data['top_posts'])} posts")
            if data["top_posts"]:
                print(f"  Top Post:           {data['top_posts'][0]['title'][:60]}...")
    else:
        # Default: fetch trending tickers
        print("\nFetching trending tickers from Reddit + ApeWisdom...")
        tracker = get_sentiment_tracker()

        # Quick fetch
        tracker._update_mention_cache()

        trending = tracker.get_trending(limit=15)
        print(f"\nTop {len(trending)} trending tickers:\n")

        for idx, (symbol, data) in enumerate(trending.items(), 1):
            print(
                f"{idx:2d}. {symbol:6s} | Score: {data['social_score']:5.1f}/10.0 | "
                f"Sentiment: {data['sentiment']:+.2f} | Velocity: {data['mention_velocity']:5.1f} | "
                f"Posts: {len(data['top_posts'])}"
            )

        print(f"\nTimestamp: {datetime.now(UTC).isoformat()}")
        print(f"Cache TTL: {tracker.cache_ttl}s")
        print(f"Stats: {tracker.stats}")

    print("\n")
