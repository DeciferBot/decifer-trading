# ╔══════════════════════════════════════════════════════════════╗
# ║   Example: How to integrate social_sentiment.py into signals.py
# ║   This shows the exact code changes needed.
# ╚══════════════════════════════════════════════════════════════╝

"""
STEP 1: Add import at top of signals.py
"""
# Add to signals.py imports section:
from social_sentiment import get_social_sentiment


"""
STEP 2: Update compute_confluence() signature and docstring
"""
def compute_confluence_UPDATED(sig_5m: dict, sig_1d: dict | None, sig_1w: dict | None,
                               news_score: int = 0, social_score: float = 0) -> dict:
    """
    Decifer 2.0 — 8-dimension scoring engine.

    Each dimension scores 0-10, total max 80, capped at 50.
    Bonus points for candlestick confirmation.

    Dimensions:
      1. TREND (0-10)     — EMA alignment × ADX gating
      2. MOMENTUM (0-10)  — MFI + RSI slope
      3. SQUEEZE (0-10)   — BB/Keltner compression → breakout potential
      4. FLOW (0-10)      — VWAP position + OBV confirmation
      5. BREAKOUT (0-10)  — Donchian channel breach + volume
      6. MTF (0-10)       — Multi-timeframe agreement
      7. NEWS (0-10)      — Yahoo RSS keyword + Claude sentiment
      8. SOCIAL (0-10)    — Reddit mention velocity + VADER sentiment
    """
    # ... existing code ...
    # Around line 568, after NEWS dimension:

    # ── 7. NEWS SENTIMENT (0-10) ────────────────────────
    # news_score is pre-computed by news.py (keyword + Claude two-tier)
    score += min(10, max(0, news_score))

    # ── 8. SOCIAL SENTIMENT (0-10) ──────────────────────
    # social_score is pre-fetched from social_sentiment.py
    # Range: 0-10 (already normalized)
    score += min(10, max(0, int(social_score)))

    # ── BONUS: Candlestick confirmation (+3 max) ────────
    # ... rest of existing code ...


"""
STEP 3: Update fetch_multi_timeframe() to fetch social sentiment
"""
def fetch_multi_timeframe_UPDATED(symbol: str, news_score: int = 0) -> dict | None:
    """
    Fetch 5m, 1d, 1w signals, then compute confluence.
    NOW ALSO: Fetch social sentiment for symbol.
    """
    # ... existing code for fetching technical signals ...

    # BEFORE compute_confluence() call:
    try:
        # Fetch social sentiment (cached, ~5ms typical)
        social_data = get_social_sentiment([symbol])
        social_score = social_data.get(symbol, {}).get("social_score", 0)
        log.debug(f"{symbol} social sentiment: {social_score:.1f}")
    except Exception as e:
        log.warning(f"Social sentiment fetch failed for {symbol}: {e}")
        social_score = 0

    # NOW call compute_confluence with social_score
    confluence = compute_confluence(
        sig_5m, sig_1d, sig_1w,
        news_score=news_score,
        social_score=social_score
    )

    # ... rest of existing code ...


"""
STEP 4: Update score_universe() to support social sentiment
"""
def score_universe_UPDATED(symbols: list, regime: str = "UNKNOWN",
                          news_data: dict = None, social_data: dict = None) -> list:
    """
    Score all symbols in the universe.
    Returns only those above the minimum score threshold, sorted by score.

    Args:
        symbols: List of symbols to score
        regime: Market regime (BULL_TRENDING, BEAR_TRENDING, CHOPPY, PANIC, UNKNOWN)
        news_data: Optional {symbol: news_sentiment_dict} from news.py
        social_data: Optional {symbol: social_sentiment_dict} from social_sentiment.py
    """
    if news_data is None:
        news_data = {}
    if social_data is None:
        social_data = {}

    # ... existing regime threshold logic ...

    # In the processing loop, when calling compute_confluence:
    confluence = compute_confluence(
        sig_5m, sig_1d, sig_1w,
        news_score=news_data.get(sym, {}).get("news_score", 0),
        social_score=social_data.get(sym, {}).get("social_score", 0)
    )

    # ... rest of existing code ...


"""
STEP 5: In bot.py, fetch social data before scanning
"""
def scan_universe_EXAMPLE(symbols: list) -> list:
    """
    Example of how to call score_universe with social sentiment.
    """
    from news import get_news_data  # or your news fetcher
    from social_sentiment import get_social_sentiment

    # Fetch news and social data in parallel if possible
    news_data = get_news_data(symbols)  # or whatever function you have
    social_data = get_social_sentiment(symbols)

    # Call score_universe with both
    results = score_universe(
        symbols,
        regime="BULL_TRENDING",
        news_data=news_data,
        social_data=social_data
    )

    return results


"""
STEP 6: In bot.py initialization, start social sentiment polling
"""
def init_bot_EXAMPLE():
    """
    Example bot initialization including social sentiment polling.
    """
    from social_sentiment import start_sentiment_polling, stop_sentiment_polling

    # Start background polling of Reddit/ApeWisdom every 60 seconds
    start_sentiment_polling(poll_interval=60)

    # Later, on bot shutdown:
    # stop_sentiment_polling()


# ═══════════════════════════════════════════════════════════════
# COMPLETE WORKING EXAMPLE: Testing with mock data
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )

    print("\n" + "="*70)
    print("  Integration Example: Social Sentiment + Signal Scoring")
    print("="*70 + "\n")

    # Simulate fetching social sentiment
    from social_sentiment import get_social_sentiment

    symbols = ["AAPL", "TSLA", "GME", "SPY", "QQQ"]

    print(f"Fetching social sentiment for {len(symbols)} symbols...")
    social_data = get_social_sentiment(symbols)

    print("\nSocial Sentiment Results:")
    print("-" * 70)
    print(f"{'Symbol':<8} | {'Score':<7} | {'Velocity':<8} | {'Sentiment':<10} | {'Posts':<6}")
    print("-" * 70)

    for symbol in symbols:
        if symbol in social_data:
            data = social_data[symbol]
            print(
                f"{symbol:<8} | "
                f"{data['social_score']:<7.1f} | "
                f"{data['mention_velocity']:<8.2f} | "
                f"{data['sentiment']:+.2f}       | "
                f"{len(data['top_posts']):<6}"
            )

    print("-" * 70)

    print("\n✓ Social sentiment data can now be passed to score_universe()")
    print("✓ Each symbol's social_score will contribute to final confluence score")
    print("✓ Background polling keeps cache fresh every 60 seconds")
    print("\n")
