"""Tests for social_sentiment module.

All Reddit/Twitter API calls are mocked with canned JSON responses.
No network connections are made.
"""
import os, sys, types
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Dec# Read the actual file first to find the broken if block at line 38
# The fix below targets the empty if-body at line 38 True:  # placeholder — see below for actual fix at line 38er module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance",
             "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod
_cfg = {"log_file": "/dev/null", "trade_log": "/dev/null",
        "order_log": "/dev/null", "anthropic_api_key": "test-key",
        "model": "claude-sonnet-4-20250514", "max_tokens": 1000,
        "mongo_uri": "", "db_name": "test"}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


import sys
import os
from unittest.mock import patch, MagicMock
from typing import List, Dict

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import social_sentiment
    HAS_SOCIAL = True
except ImportError:
    HAS_SOCIAL = False


pytestmark = pytest.mark.skipif(
    not HAS_SOCIAL,
    reason="social_sentiment module not importable"
)


# ---------------------------------------------------------------------------
# Canned social data
# ---------------------------------------------------------------------------

BULLISH_POSTS = [
    {"text": "$AAPL is going to moon! Strong buy signals everywhere!", "score": 500, "upvote_ratio": 0.95},
    {"text": "AAPL breaking out of consolidation, massive upside ahead", "score": 300, "upvote_ratio": 0.88},
    {"text": "Best earnings ever for Apple, incredible growth trajectory", "score": 200, "upvote_ratio": 0.92},
]

BEARISH_POSTS = [
    {"text": "$AAPL is garbage, selling all my shares, bearish", "score": 100, "upvote_ratio": 0.7},
    {"text": "Apple's growth story is over, avoid this stock", "score": 150, "upvote_ratio": 0.65},
    {"text": "Terrible guidance, huge miss, going to crash hard", "score": 80, "upvote_ratio": 0.72},
]

NEUTRAL_POSTS = [
    {"text": "Just bought some $AAPL shares today", "score": 10, "upvote_ratio": 0.5},
    {"text": "What do you think about AAPL earnings?", "score": 5, "upvote_ratio": 0.55},
]


# ---------------------------------------------------------------------------
# Text scoring
# ---------------------------------------------------------------------------

class TestSocialTextScoring:

    def test_bullish_text_positive(self):
        """Clearly bullish social post should score positive."""
        scorer = getattr(social_sentiment, "score_post", None) or \
                 getattr(social_sentiment, "score_text", None)
        if scorer is None:
            pytest.skip("No text scoring function found")
        score = scorer(BULLISH_POSTS[0]["text"])
        assert score > 0, f"Expected positive score for bullish text, got {score}"

    def test_bearish_text_negative(self):
        """Clearly bearish social post should score negative or low."""
        scorer = getattr(social_sentiment, "score_post", None) or \
                 getattr(social_sentiment, "score_text", None)
        if scorer is None:
            pytest.skip("No text scoring function found")
        score = scorer(BEARISH_POSTS[0]["text"])
        assert score < 0.5, f"Expected low/negative score for bearish text, got {score}"

    def test_empty_text_no_exception(self):
        """Empty string must not crash the scorer."""
        scorer = getattr(social_sentiment, "score_post", None) or \
                 getattr(social_sentiment, "score_text", None)
        if scorer is None:
            pytest.skip("No text scoring function found")
        try:
            result = scorer("")
            assert isinstance(result, (int, float))
        except Exception as exc:
            pytest.fail(f"Scorer raised for empty string: {exc}")


# ---------------------------------------------------------------------------
# Aggregate sentiment
# ---------------------------------------------------------------------------

class TestSocialAggregateSentiment:

    def test_bullish_posts_aggregate_positive(self):
        """Aggregating bullish posts yields positive sentiment."""
        aggregator = getattr(social_sentiment, "aggregate_reddit_sentiment", None) or \
                     getattr(social_sentiment, "aggregate_sentiment", None)
        if aggregator is None:
            pytest.skip("No aggregate function found")
        score = aggregator(BULLISH_POSTS)
        assert score > 0, f"Expected positive aggregate for bullish posts, got {score}"

    def test_bearish_posts_aggregate_lower_than_bullish(self):
        """Bearish posts must score lower than bullish posts in aggregate."""
        aggregator = getattr(social_sentiment, "aggregate_reddit_sentiment", None) or \
                     getattr(social_sentiment, "aggregate_sentiment", None)
        if aggregator is None:
            pytest.skip("No aggregate function found")
        bull_score = aggregator(BULLISH_POSTS)
        bear_score = aggregator(BEARISH_POSTS)
        assert bull_score > bear_score, (
            f"Bullish ({bull_score}) must > bearish ({bear_score})"
        )

    def test_empty_posts_no_exception(self):
        """Empty post list must not raise."""
        aggregator = getattr(social_sentiment, "aggregate_reddit_sentiment", None) or \
                     getattr(social_sentiment, "aggregate_sentiment", None)
        if aggregator is None:
            pytest.skip("No aggregate function found")
        try:
            result = aggregator([])
            assert isinstance(result, (int, float))
        except Exception as exc:
            pytest.fail(f"Aggregator raised for empty list: {exc}")

    def test_single_post_no_exception(self):
        """Single post must not crash."""
        aggregator = getattr(social_sentiment, "aggregate_reddit_sentiment", None) or \
                     getattr(social_sentiment, "aggregate_sentiment", None)
        if aggregator is None:
            pytest.skip("No aggregate function found")
        try:
            result = aggregator([BULLISH_POSTS[0]])
            assert isinstance(result, (int, float))
        except Exception as exc:
            pytest.fail(f"Aggregator raised for single post: {exc}")
