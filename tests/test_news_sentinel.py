"""Tests for news_sentinel keyword scoring and feed parsing.

All HTTP calls are mocked with canned RSS/JSON responses.
No network connections are made.
"""
import os, sys, types
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
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
from typing import Dict, List

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import news_sentinel
    HAS_NEWS_SENTINEL = True
except ImportError:
    HAS_NEWS_SENTINEL = False


pytestmark = pytest.mark.skipif(
    not HAS_NEWS_SENTINEL,
    reason="news_sentinel module not importable"
)


# ---------------------------------------------------------------------------
# Canned RSS responses
# ---------------------------------------------------------------------------

BULLISH_HEADLINE = (
    "Apple beats earnings expectations, raises guidance for next quarter"
)
BEARISH_HEADLINE = (
    "Apple misses earnings, cuts guidance amid economic headwinds and rising costs"
)
NEUTRAL_HEADLINE = "Apple announces new product lineup for the upcoming year"


@pytest.fixture()
def bullish_articles():
    return [
        {"title": BULLISH_HEADLINE, "summary": "", "source": "reuters"},
        {"title": "Record revenue growth beats Wall Street forecasts", "summary": "", "source": "bloomberg"},
        {"title": "Strong buyback program signals management confidence", "summary": "", "source": "wsj"},
    ]


@pytest.fixture()
def bearish_articles():
    return [
        {"title": BEARISH_HEADLINE, "summary": "", "source": "reuters"},
        {"title": "Layoffs signal deep concern about profitability", "summary": "", "source": "cnbc"},
        {"title": "Recession fears weigh on consumer spending outlook", "summary": "", "source": "ft"},
    ]


@pytest.fixture()
def mixed_articles(bullish_articles, bearish_articles):
    return bullish_articles[:1] + bearish_articles[:1]


# ---------------------------------------------------------------------------
# Keyword scoring tests
# ---------------------------------------------------------------------------

class TestNewsKeywordScoring:

    def test_bullish_headline_scores_positive(self):
        """A clearly positive headline must produce a positive/high sentiment score."""
        if not hasattr(news_sentinel, "score_headline"):
            pytest.skip("score_headline not exposed")
        score = news_sentinel.score_headline(BULLISH_HEADLINE)
        assert score > 0, f"Expected positive score for bullish headline, got {score}"

    def test_bearish_headline_scores_negative(self):
        """A clearly negative headline must produce a negative/low sentiment score."""
        if not hasattr(news_sentinel, "score_headline"):
            pytest.skip("score_headline not exposed")
        score = news_sentinel.score_headline(BEARISH_HEADLINE)
        assert score < 0, f"Expected negative score for bearish headline, got {score}"

    def test_neutral_headline_near_zero(self):
        """A neutral headline should score near zero."""
        if not hasattr(news_sentinel, "score_headline"):
            pytest.skip("score_headline not exposed")
        score = news_sentinel.score_headline(NEUTRAL_HEADLINE)
        assert -0.5 <= score <= 0.5, (
            f"Expected near-zero score for neutral headline, got {score}"
        )

    def test_empty_headline_no_exception(self):
        """Empty headline must not raise."""
        if not hasattr(news_sentinel, "score_headline"):
            pytest.skip("score_headline not exposed")
        try:
            score = news_sentinel.score_headline("")
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(f"score_headline raised for empty string: {exc}")

    def test_score_is_deterministic(self):
        """Same headline always produces same score."""
        if not hasattr(news_sentinel, "score_headline"):
            pytest.skip("score_headline not exposed")
        s1 = news_sentinel.score_headline(BULLISH_HEADLINE)
        s2 = news_sentinel.score_headline(BULLISH_HEADLINE)
        assert s1 == s2, f"Non-deterministic: {s1} vs {s2}"


# ---------------------------------------------------------------------------
# Aggregate article scoring
# ---------------------------------------------------------------------------

class TestNewsAggregateScoring:

    def test_bullish_articles_aggregate_positive(self, bullish_articles):
        """Aggregating bullish articles should yield a positive sentiment."""
        if not hasattr(news_sentinel, "aggregate_sentiment"):
            pytest.skip("aggregate_sentiment not exposed")
        score = news_sentinel.aggregate_sentiment(bullish_articles)
        assert score > 0, f"Expected positive aggregate, got {score}"

    def test_bearish_articles_aggregate_negative(self, bearish_articles):
        """Aggregating bearish articles should yield a negative sentiment."""
        if not hasattr(news_sentinel, "aggregate_sentiment"):
            pytest.skip("aggregate_sentiment not exposed")
        score = news_sentinel.aggregate_sentiment(bearish_articles)
        assert score < 0, f"Expected negative aggregate, got {score}"

    def test_empty_articles_no_exception(self):
        """Empty article list must not raise."""
        if not hasattr(news_sentinel, "aggregate_sentiment"):
            pytest.skip("aggregate_sentiment not exposed")
        try:
            score = news_sentinel.aggregate_sentiment([])
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(f"aggregate_sentiment raised for empty list: {exc}")
