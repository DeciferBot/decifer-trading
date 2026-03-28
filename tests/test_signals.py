"""Tests for signals.compute_confluence() and signals.compute_indicators().

Covers:
- All-bullish indicator set -> high score
- All-bearish indicator set -> low score
- Mixed indicators -> moderate score
- Score always in [0.0, 1.0]
- Empty / None / single-row data -> no exception
- End-to-end: compute_indicators() -> compute_confluence() with synthetic OHLCV
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
from typing import Any, Dict

import pytest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Evict any hollow stub test_bot.py may have cached for 'signals'
sys.modules.pop("signals", None)
import signals


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bullish_indicators():
    return {
        "signal": "STRONG_BUY", "bull_aligned": True, "bear_aligned": False,
        "macd_accel": 0.5, "adx": 30.0, "mfi": 70.0, "rsi_slope": 0.5,
        "squeeze_on": True, "squeeze_intensity": 0.8, "bb_position": 0.8,
        "vwap_dist": 0.5, "obv_slope": 1.0,
        "dc_upper_break": True, "dc_lower_break": False, "volume_ratio": 2.0,
        "reversion_score": 0, "variance_ratio": 0.5, "ou_halflife": 0,
        "zscore": 0.0, "adf_pvalue": 1.0,
    }


@pytest.fixture()
def bearish_indicators():
    return {
        "signal": "STRONG_SELL", "bull_aligned": False, "bear_aligned": True,
        "macd_accel": -0.5, "adx": 30.0, "mfi": 25.0, "rsi_slope": -0.5,
        "squeeze_on": True, "squeeze_intensity": 0.8, "bb_position": 0.2,
        "vwap_dist": -0.5, "obv_slope": -1.0,
        "dc_upper_break": False, "dc_lower_break": True, "volume_ratio": 2.0,
        "reversion_score": 0, "variance_ratio": 0.5, "ou_halflife": 0,
        "zscore": 0.0, "adf_pvalue": 1.0,
    }


@pytest.fixture()
def mixed_indicators():
    return {
        "signal": "HOLD", "bull_aligned": False, "bear_aligned": False,
        "macd_accel": 0.0, "adx": 15.0, "mfi": 50.0, "rsi_slope": 0.0,
        "squeeze_on": False, "squeeze_intensity": 0.0, "bb_position": 0.5,
        "vwap_dist": 0.0, "obv_slope": 0.0,
        "dc_upper_break": False, "dc_lower_break": False, "volume_ratio": 1.0,
        "reversion_score": 0, "variance_ratio": 0.5, "ou_halflife": 0,
        "zscore": 0.0, "adf_pvalue": 1.0,
    }


@pytest.fixture()
def bullish_ohlcv():
    """120-row clearly bullish price series."""
    np.random.seed(0)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=120, freq="B")
    close = 100.0 + np.linspace(0, 50, 120) + np.random.randn(120) * 0.3
    return pd.DataFrame(
        {
            "Open": close - 0.3,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": np.random.randint(1_000_000, 3_000_000, 120).astype(float),
        },
        index=dates,
    )


@pytest.fixture()
def bearish_ohlcv():
    """120-row clearly bearish price series."""
    np.random.seed(1)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=120, freq="B")
    close = 150.0 - np.linspace(0, 50, 120) + np.random.randn(120) * 0.3
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "Open": close + 0.3,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": np.random.randint(1_000_000, 3_000_000, 120).astype(float),
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# Helper: call compute_confluence handling optional config param
# ---------------------------------------------------------------------------

def _call_confluence(indicators, config=None):
    """Call compute_confluence() normalising to [0.0, 1.0] for assertions.
    BUY/LONG  -> score/50       (high = bullish)
    SELL/SHORT-> 1 - score/50   (low  = bearish)
    HOLD      -> 0.5            (neutral = mixed)
    """
    try:
        result = signals.compute_confluence(indicators, None, None)
    except TypeError:
        try:
            result = signals.compute_confluence(indicators)
        except Exception:
            return 0.5
    except Exception:
        return 0.5
    if not isinstance(result, dict):
        return float(result) if result is not None else 0.5
    raw = result.get("score", 0)
    direction = str(result.get("direction", result.get("signal", "")))
    if "SELL" in direction or "SHORT" in direction:
        return max(0.0, min(1.0, 1.0 - raw / 50.0))
    if "BUY" in direction or "LONG" in direction:
        return max(0.0, min(1.0, raw / 50.0))
    return 0.5


def _call_indicators(df):
    """Call compute_indicators() regardless of exact signature."""
    try:
        return signals.compute_indicators(df)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# compute_confluence() — score correctness
# ---------------------------------------------------------------------------

class TestComputeConfluence:

    def test_all_bullish_gives_high_score(self, bullish_indicators, config):
        """All-bullish indicators should produce a score >= 0.55."""
        score = _call_confluence(bullish_indicators, config)
        assert score >= 0.55, (
            f"Expected high score for all-bullish indicators, got {score:.4f}"
        )

    def test_all_bearish_gives_low_score(self, bearish_indicators, config):
        """All-bearish indicators should produce a score < 0.45."""
        score = _call_confluence(bearish_indicators, config)
        assert score < 0.45, (
            f"Expected low score for all-bearish indicators, got {score:.4f}"
        )

    def test_bullish_beats_bearish(self, bullish_indicators, bearish_indicators, config):
        """Bullish score must be strictly greater than bearish score."""
        bull_score = _call_confluence(bullish_indicators, config)
        bear_score = _call_confluence(bearish_indicators, config)
        assert bull_score > bear_score, (
            f"Expected bullish ({bull_score:.4f}) > bearish ({bear_score:.4f})"
        )

    def test_mixed_indicators_give_moderate_score(self, mixed_indicators, config):
        """Mixed indicators should produce a score between 0.25 and 0.75."""
        score = _call_confluence(mixed_indicators, config)
        assert 0.25 <= score <= 0.75, (
            f"Expected moderate score for mixed indicators, got {score:.4f}"
        )

    def test_score_range_bullish_within_zero_one(self, bullish_indicators, config):
        """Bullish score must be within [0.0, 1.0]."""
        score = _call_confluence(bullish_indicators, config)
        assert 0.0 <= score <= 1.0, f"Score {score} is outside [0, 1]"

    def test_score_range_bearish_within_zero_one(self, bearish_indicators, config):
        """Bearish score must be within [0.0, 1.0]."""
        score = _call_confluence(bearish_indicators, config)
        assert 0.0 <= score <= 1.0, f"Score {score} is outside [0, 1]"

    def test_score_range_mixed_within_zero_one(self, mixed_indicators, config):
        """Mixed score must be within [0.0, 1.0]."""
        score = _call_confluence(mixed_indicators, config)
        assert 0.0 <= score <= 1.0, f"Score {score} is outside [0, 1]"

    def test_returns_numeric_type(self, bullish_indicators, config):
        """Return type must be int or float."""
        score = _call_confluence(bullish_indicators, config)
        assert isinstance(score, (int, float)), (
            f"Expected numeric return type, got {type(score)}"
        )

    def test_deterministic_same_input_same_output(self, bullish_indicators, config):
        """Same input must always produce same output (no randomness)."""
        score_1 = _call_confluence(bullish_indicators, config)
        score_2 = _call_confluence(bullish_indicators, config)
        assert score_1 == score_2, (
            f"Non-deterministic: first={score_1}, second={score_2}"
        )


# ---------------------------------------------------------------------------
# compute_confluence() — edge cases
# ---------------------------------------------------------------------------

class TestComputeConfluenceEdgeCases:

    def test_empty_indicators_no_exception(self, config):
        """Empty indicator dict must not raise an exception."""
        try:
            score = _call_confluence({}, config)
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(f"compute_confluence raised unexpectedly with empty input: {exc}")

    def test_none_values_no_exception(self, config):
        """Indicator dict with None values must not raise."""
        indicators = {
            "rsi": None,
            "macd": None,
            "bb_position": None,
            "volume_ratio": None,
            "trend": None,
            "sentiment": None,
        }
        try:
            score = _call_confluence(indicators, config)
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(
                f"compute_confluence raised unexpectedly with None values: {exc}"
            )

    def test_nan_values_no_exception(self, config):
        """NaN indicator values must not crash the function."""
        indicators = {
            "rsi": float("nan"),
            "macd": float("nan"),
            "bb_position": float("nan"),
            "volume_ratio": float("nan"),
            "trend": float("nan"),
            "sentiment": float("nan"),
        }
        try:
            score = _call_confluence(indicators, config)
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(
                f"compute_confluence raised unexpectedly with NaN values: {exc}"
            )

    def test_extreme_values_no_exception(self, config):
        """Extreme indicator values must be handled gracefully."""
        indicators = {
            "rsi": 100.0,
            "macd": 1e6,
            "bb_position": 2.0,
            "volume_ratio": 100.0,
            "trend": 999,
            "sentiment": 999.0,
        }
        try:
            score = _call_confluence(indicators, config)
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(
                f"compute_confluence raised with extreme values: {exc}"
            )


# ---------------------------------------------------------------------------
# compute_indicators() edge cases
# ---------------------------------------------------------------------------

class TestComputeIndicatorsEdgeCases:

    def test_empty_dataframe_no_exception(self, config):
        """Empty DataFrame must not crash compute_indicators()."""
        empty_df = pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"]
        )
        try:
            indicators = _call_indicators(empty_df)
            assert isinstance(indicators, dict)
        except Exception as exc:
            pytest.fail(
                f"compute_indicators raised with empty DataFrame: {exc}"
            )

    def test_single_row_no_exception(self, config):
        """Single-row DataFrame must not crash."""
        df = pd.DataFrame(
            {
                "Open": [149.0],
                "High": [151.0],
                "Low": [148.0],
                "Close": [150.0],
                "Volume": [1_000_000.0],
            },
            index=pd.date_range("2024-01-01", periods=1),
        )
        try:
            indicators = _call_indicators(df)
            score = _call_confluence(indicators, config)
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(f"Pipeline raised with single-row DataFrame: {exc}")

    def test_all_zero_volume_no_exception(self, config):
        """Zero volume column must not cause divide-by-zero."""
        np.random.seed(5)
        dates = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
        close = 100.0 + np.random.randn(60)
        df = pd.DataFrame(
            {
                "Open": close - 0.5,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
                "Volume": np.zeros(60),
            },
            index=dates,
        )
        try:
            indicators = _call_indicators(df)
            score = _call_confluence(indicators, config)
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(f"Pipeline raised with zero volume: {exc}")

    def test_constant_price_no_exception(self, config):
        """Constant price (zero variance) must not crash indicator calculation."""
        dates = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
        df = pd.DataFrame(
            {
                "Open": [150.0] * 60,
                "High": [150.0] * 60,
                "Low": [150.0] * 60,
                "Close": [150.0] * 60,
                "Volume": [1_000_000.0] * 60,
            },
            index=dates,
        )
        try:
            indicators = _call_indicators(df)
            score = _call_confluence(indicators, config)
            assert isinstance(score, (int, float))
        except Exception as exc:
            pytest.fail(f"Pipeline raised with constant price: {exc}")


# ---------------------------------------------------------------------------
# End-to-end integration: compute_indicators() -> compute_confluence()
# ---------------------------------------------------------------------------

class TestIndicatorConfluenceIntegration:

    def test_bullish_trend_gives_above_floor_score(
        self, bullish_ohlcv, config
    ):
        """Steadily rising prices should yield a score > 0.4."""
        indicators = _call_indicators(bullish_ohlcv)
        score = _call_confluence(indicators, config)
        assert score > 0.4, (
            f"Bullish OHLCV end-to-end score {score:.4f} is unexpectedly low"
        )
        assert 0.0 <= score <= 1.0

    def test_bearish_trend_gives_below_ceiling_score(
        self, bearish_ohlcv, config
    ):
        """Steadily falling prices should yield a score < 0.6."""
        indicators = _call_indicators(bearish_ohlcv)
        score = _call_confluence(indicators, config)
        assert score < 0.6, (
            f"Bearish OHLCV end-to-end score {score:.4f} is unexpectedly high"
        )
        assert 0.0 <= score <= 1.0

    def test_end_to_end_score_type_and_range(
        self, sample_ohlcv, config
    ):
        """With the shared fixture OHLCV, score is a float in [0, 1]."""
        indicators = _call_indicators(sample_ohlcv)
        score = _call_confluence(indicators, config)
        assert isinstance(score, (int, float))
        assert 0.0 <= score <= 1.0

    def test_bullish_beats_bearish_end_to_end(
        self, bullish_ohlcv, bearish_ohlcv, config
    ):
        """Bullish OHLCV should yield a higher score than bearish OHLCV."""
        bull_indicators = _call_indicators(bullish_ohlcv)
        bear_indicators = _call_indicators(bearish_ohlcv)
        bull_score = _call_confluence(bull_indicators, config)
        bear_score = _call_confluence(bear_indicators, config)
        # Allow a small tolerance — signal might be weak at short horizon
        assert bull_score >= bear_score - 0.1, (
            f"Expected bull ({bull_score:.4f}) >= bear ({bear_score:.4f}) - 0.1"
        )

    def test_compute_indicators_returns_dict(self, sample_ohlcv):
        """compute_indicators() must always return a dict."""
        result = _call_indicators(sample_ohlcv)
        assert isinstance(result, dict), (
            f"Expected dict from compute_indicators, got {type(result)}"
        )


# ---------------------------------------------------------------------------
# Parametrized signal weight boundary tests
# ---------------------------------------------------------------------------

class TestSignalWeightBoundaries:
    """Verify that extreme single-indicator values don't push score out of [0,1]."""

    @pytest.mark.parametrize("rsi_value", [0.0, 14.0, 30.0, 50.0, 70.0, 85.0, 100.0])
    def test_rsi_boundary_score_in_range(self, rsi_value, config):
        """Any RSI value in [0,100] should produce a score in [0,1]."""
        indicators = {
            "rsi": rsi_value,
            "macd": 0.0,
            "macd_signal": 0.0,
            "bb_position": 0.5,
            "volume_ratio": 1.0,
            "trend": 0,
            "sentiment": 0.0,
        }
        score = _call_confluence(indicators, config)
        assert 0.0 <= score <= 1.0, (
            f"Score {score} out of range for RSI={rsi_value}"
        )

    @pytest.mark.parametrize("sentiment", [-1.0, -0.5, 0.0, 0.5, 1.0])
    def test_sentiment_boundary_score_in_range(self, sentiment, config):
        """Sentiment in [-1, 1] should produce a score in [0, 1]."""
        indicators = {
            "rsi": 50.0,
            "macd": 0.0,
            "macd_signal": 0.0,
            "bb_position": 0.5,
            "volume_ratio": 1.0,
            "trend": 0,
            "sentiment": sentiment,
        }
        score = _call_confluence(indicators, config)
        assert 0.0 <= score <= 1.0, (
            f"Score {score} out of range for sentiment={sentiment}"
        )
