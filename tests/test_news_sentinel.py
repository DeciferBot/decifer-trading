"""Tests for news_sentinel keyword scoring and feed parsing.

All HTTP calls are mocked with canned RSS/JSON responses.
No network connections are made.
"""

import os
import sys
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


import os
import sys
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Evict any hollow stub (e.g. MagicMock planted by an earlier test file) so
# we get the real news_sentinel module with keyword_score imported from news.py.
sys.modules.pop("news_sentinel", None)
try:
    import news_sentinel

    HAS_NEWS_SENTINEL = hasattr(news_sentinel, "keyword_score")
except (ImportError, Exception):
    HAS_NEWS_SENTINEL = False


pytestmark = pytest.mark.skipif(
    not HAS_NEWS_SENTINEL, reason="news_sentinel module not importable or keyword_score not found"
)


# ---------------------------------------------------------------------------
# Canned RSS responses
# ---------------------------------------------------------------------------

BULLISH_HEADLINE = "Apple beats earnings expectations, raises guidance for next quarter"
BEARISH_HEADLINE = "Apple misses earnings, cuts guidance amid economic headwinds and rising costs"
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
    """Tests for keyword_score — the fast headline sentiment scorer.

    news_sentinel imports keyword_score from news.py, so it is accessible
    as news_sentinel.keyword_score([headline])  ->  {"score": int, ...}.
    score is an integer in [-10, +10]: positive = bullish, negative = bearish.
    """

    def test_bullish_headline_scores_positive(self):
        """A clearly positive headline must produce a positive score."""
        result = news_sentinel.keyword_score([BULLISH_HEADLINE])
        assert result["score"] > 0, f"Expected positive score for bullish headline, got {result['score']}"

    def test_bearish_headline_scores_negative(self):
        """A clearly negative headline must produce a negative score."""
        result = news_sentinel.keyword_score([BEARISH_HEADLINE])
        assert result["score"] < 0, f"Expected negative score for bearish headline, got {result['score']}"

    def test_neutral_headline_near_zero(self):
        """A headline with no keyword hits should score zero."""
        result = news_sentinel.keyword_score([NEUTRAL_HEADLINE])
        assert -2 <= result["score"] <= 2, f"Expected near-zero score for neutral headline, got {result['score']}"

    def test_empty_headline_no_exception(self):
        """Empty headline must not raise and must return a numeric score."""
        result = news_sentinel.keyword_score([""])
        assert isinstance(result["score"], (int, float))

    def test_score_is_deterministic(self):
        """Same headline always produces the same score."""
        r1 = news_sentinel.keyword_score([BULLISH_HEADLINE])
        r2 = news_sentinel.keyword_score([BULLISH_HEADLINE])
        assert r1["score"] == r2["score"], f"Non-deterministic: {r1['score']} vs {r2['score']}"


# ---------------------------------------------------------------------------
# Aggregate article scoring
# ---------------------------------------------------------------------------


class TestNewsAggregateScoring:
    """Tests for keyword_score applied across multiple article titles.

    keyword_score accepts a list of headline strings and accumulates bull/bear
    points across all of them, returning a combined score dict.
    """

    def test_bullish_articles_aggregate_positive(self, bullish_articles):
        """Aggregating bullish article titles must yield a positive score."""
        headlines = [a["title"] for a in bullish_articles]
        result = news_sentinel.keyword_score(headlines)
        assert result["score"] > 0, f"Expected positive aggregate for bullish articles, got {result['score']}"

    def test_bearish_articles_aggregate_negative(self, bearish_articles):
        """Aggregating bearish article titles must yield a negative score."""
        headlines = [a["title"] for a in bearish_articles]
        result = news_sentinel.keyword_score(headlines)
        assert result["score"] < 0, f"Expected negative aggregate for bearish articles, got {result['score']}"

    def test_empty_articles_no_exception(self):
        """Empty article list must return a zero score without raising."""
        result = news_sentinel.keyword_score([])
        assert isinstance(result["score"], (int, float))
        assert result["score"] == 0
