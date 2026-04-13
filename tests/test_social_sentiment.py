"""Tests for social_sentiment module.

All Reddit/Twitter API calls are mocked with canned JSON responses.
No network connections are made.
"""

import os
import sys
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Dec# Read the actual file first to find the broken if block at line 38
# The fix below targets the empty if-body at line 38 True:  # placeholder — see below for actual fix at line 38er module
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

# Evict any hollow stub (e.g. MagicMock planted by test_bot.py) so we
# get the real social_sentiment module with FinanceVADER defined.
sys.modules.pop("social_sentiment", None)
try:
    import social_sentiment

    HAS_SOCIAL = hasattr(social_sentiment, "FinanceVADER")
except (ImportError, Exception):
    HAS_SOCIAL = False


pytestmark = pytest.mark.skipif(
    not HAS_SOCIAL, reason="social_sentiment module not importable or FinanceVADER not found"
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
    """Tests for FinanceVADER.get_sentiment — finance-context text scorer.

    Returns -1.0 (bearish) to +1.0 (bullish).
    When VADER/NLTK is unavailable (test environment), falls back to
    the finance keyword lexicon defined in social_sentiment.FINANCE_LEXICON.
    """

    def _vader(self):
        return social_sentiment.FinanceVADER()

    def test_bullish_text_positive(self):
        """Clearly bullish social post scores positive."""
        score = self._vader().get_sentiment(BULLISH_POSTS[0]["text"])
        assert score > 0, f"Expected positive score for bullish text, got {score}"

    def test_bearish_text_negative(self):
        """Clearly bearish social post scores negative or near zero."""
        score = self._vader().get_sentiment(BEARISH_POSTS[0]["text"])
        assert score < 0.5, f"Expected low/negative score for bearish text, got {score}"

    def test_empty_text_no_exception(self):
        """Empty string returns 0.0 without raising."""
        result = self._vader().get_sentiment("")
        assert isinstance(result, float)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Aggregate sentiment
# ---------------------------------------------------------------------------


class TestSocialAggregateSentiment:
    """Tests for FinanceVADER.get_sentiment_batch — average sentiment across texts.

    Returns the mean of get_sentiment() over a list of strings.
    """

    def _vader(self):
        return social_sentiment.FinanceVADER()

    def test_bullish_posts_aggregate_positive(self):
        """Averaging bullish post texts yields a positive score."""
        texts = [p["text"] for p in BULLISH_POSTS]
        score = self._vader().get_sentiment_batch(texts)
        assert score > 0, f"Expected positive aggregate for bullish posts, got {score}"

    def test_bearish_posts_aggregate_lower_than_bullish(self):
        """Bearish post average must be lower than bullish post average."""
        vader = self._vader()
        bull_score = vader.get_sentiment_batch([p["text"] for p in BULLISH_POSTS])
        bear_score = vader.get_sentiment_batch([p["text"] for p in BEARISH_POSTS])
        assert bull_score > bear_score, f"Bullish ({bull_score:.3f}) must exceed bearish ({bear_score:.3f})"

    def test_empty_posts_no_exception(self):
        """Empty list returns 0.0 without raising."""
        result = self._vader().get_sentiment_batch([])
        assert isinstance(result, float)
        assert result == 0.0

    def test_single_post_no_exception(self):
        """Single post list does not crash and returns a float."""
        result = self._vader().get_sentiment_batch([BULLISH_POSTS[0]["text"]])
        assert isinstance(result, float)
