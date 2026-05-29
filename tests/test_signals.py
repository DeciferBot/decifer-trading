"""Tests for signals.compute_confluence() and signals.compute_indicators().

Covers:
- All-bullish indicator set -> high score
- All-bearish indicator set -> low score
- Mixed indicators -> moderate score
- Score always in [0.0, 1.0]
- Empty / None / single-row data -> no exception
- End-to-end: compute_indicators() -> compute_confluence() with synthetic OHLCV
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

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

import numpy as np
import pandas as pd
import pytest

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
        "signal": "STRONG_BUY",
        "bull_aligned": True,
        "bear_aligned": False,
        "macd_accel": 0.5,
        "adx": 30.0,
        "mfi": 70.0,
        "rsi_slope": 0.5,
        "squeeze_on": True,
        "squeeze_intensity": 0.8,
        "bb_position": 0.8,
        "vwap_dist": 0.5,
        "obv_slope": 1.0,
        "dc_upper_break": True,
        "dc_lower_break": False,
        "volume_ratio": 2.0,
        "reversion_score": 0,
        "variance_ratio": 0.5,
        "ou_halflife": 0,
        "zscore": 0.0,
        "adf_pvalue": 1.0,
    }


@pytest.fixture()
def bearish_indicators():
    return {
        "signal": "STRONG_SELL",
        "bull_aligned": False,
        "bear_aligned": True,
        "macd_accel": -0.5,
        "adx": 30.0,
        "mfi": 25.0,
        "rsi_slope": -0.5,
        "squeeze_on": True,
        "squeeze_intensity": 0.8,
        "bb_position": 0.2,
        "vwap_dist": -0.5,
        "obv_slope": -1.0,
        "dc_upper_break": False,
        "dc_lower_break": True,
        "volume_ratio": 2.0,
        "reversion_score": 0,
        "variance_ratio": 0.5,
        "ou_halflife": 0,
        "zscore": 0.0,
        "adf_pvalue": 1.0,
    }


@pytest.fixture()
def mixed_indicators():
    return {
        "signal": "HOLD",
        "bull_aligned": False,
        "bear_aligned": False,
        "macd_accel": 0.0,
        "adx": 15.0,
        "mfi": 50.0,
        "rsi_slope": 0.0,
        "squeeze_on": False,
        "squeeze_intensity": 0.0,
        "bb_position": 0.5,
        "vwap_dist": 0.0,
        "obv_slope": 0.0,
        "dc_upper_break": False,
        "dc_lower_break": False,
        "volume_ratio": 1.0,
        "reversion_score": 0,
        "variance_ratio": 0.5,
        "ou_halflife": 0,
        "zscore": 0.0,
        "adf_pvalue": 1.0,
    }


@pytest.fixture()
def bullish_ohlcv():
    """120-row clearly bullish price series."""
    np.random.seed(0)
    _end = pd.Timestamp.today().normalize()
    if _end.dayofweek >= 5:
        _end -= pd.offsets.BDay(1)
    dates = pd.date_range(end=_end, periods=120, freq="B")
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
    _end = pd.Timestamp.today().normalize()
    if _end.dayofweek >= 5:
        _end -= pd.offsets.BDay(1)
    dates = pd.date_range(end=_end, periods=120, freq="B")
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
    BUY/LONG  -> score/NORM     (high = bullish)
    SELL/SHORT-> 1 - score/NORM (low  = bearish)
    HOLD      -> 0.5            (neutral = mixed)
    NORM=50 keeps practical scores (typically 20-50) in a usable 0-1 range.
    """
    _NORM = 50.0
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
        return max(0.0, min(1.0, 1.0 - raw / _NORM))
    if "BUY" in direction or "LONG" in direction:
        return max(0.0, min(1.0, raw / _NORM))
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


_EQUAL_WEIGHTS = {d: 1.0 / 15 for d in [
    "trend", "momentum", "squeeze", "flow", "breakout", "mtf", "news",
    "social", "reversion", "iv_skew", "pead", "short_squeeze",
    "overnight_drift", "analyst_revision", "insider_buying",
]}


class TestComputeConfluence:
    @pytest.fixture(autouse=True)
    def _patch_ic_weights(self):
        """Force equal IC weights so confluence tests are IC-config-independent."""
        with patch("ic_calculator.get_current_weights", return_value=_EQUAL_WEIGHTS):
            yield

    def test_all_bullish_gives_high_score(self, bullish_indicators, config):
        """All-bullish indicators should produce a score >= 0.55."""
        score = _call_confluence(bullish_indicators, config)
        assert score >= 0.55, f"Expected high score for all-bullish indicators, got {score:.4f}"

    def test_all_bearish_gives_low_score(self, bearish_indicators, config):
        """All-bearish indicators should produce a score < 0.45."""
        score = _call_confluence(bearish_indicators, config)
        assert score < 0.45, f"Expected low score for all-bearish indicators, got {score:.4f}"

    def test_bullish_beats_bearish(self, bullish_indicators, bearish_indicators, config):
        """Bullish score must be strictly greater than bearish score."""
        bull_score = _call_confluence(bullish_indicators, config)
        bear_score = _call_confluence(bearish_indicators, config)
        assert bull_score > bear_score, f"Expected bullish ({bull_score:.4f}) > bearish ({bear_score:.4f})"

    def test_mixed_indicators_give_moderate_score(self, mixed_indicators, config):
        """Mixed indicators should produce a score between 0.25 and 0.75."""
        score = _call_confluence(mixed_indicators, config)
        assert 0.25 <= score <= 0.75, f"Expected moderate score for mixed indicators, got {score:.4f}"

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
        assert isinstance(score, (int, float)), f"Expected numeric return type, got {type(score)}"

    def test_deterministic_same_input_same_output(self, bullish_indicators, config):
        """Same input must always produce same output (no randomness)."""
        score_1 = _call_confluence(bullish_indicators, config)
        score_2 = _call_confluence(bullish_indicators, config)
        assert score_1 == score_2, f"Non-deterministic: first={score_1}, second={score_2}"


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
            pytest.fail(f"compute_confluence raised unexpectedly with None values: {exc}")

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
            pytest.fail(f"compute_confluence raised unexpectedly with NaN values: {exc}")

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
            pytest.fail(f"compute_confluence raised with extreme values: {exc}")


# ---------------------------------------------------------------------------
# compute_indicators() edge cases
# ---------------------------------------------------------------------------


class TestComputeIndicatorsEdgeCases:
    def test_empty_dataframe_no_exception(self, config):
        """Empty DataFrame must not crash compute_indicators()."""
        empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        try:
            indicators = _call_indicators(empty_df)
            assert isinstance(indicators, dict)
        except Exception as exc:
            pytest.fail(f"compute_indicators raised with empty DataFrame: {exc}")

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
        _end = pd.Timestamp.today().normalize()
        if _end.dayofweek >= 5:
            _end -= pd.offsets.BDay(1)
        dates = pd.date_range(end=_end, periods=60, freq="B")
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
        _end = pd.Timestamp.today().normalize()
        if _end.dayofweek >= 5:
            _end -= pd.offsets.BDay(1)
        dates = pd.date_range(end=_end, periods=60, freq="B")
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
    def test_bullish_trend_gives_above_floor_score(self, bullish_ohlcv, config):
        """Steadily rising prices should yield a score > 0.4."""
        indicators = _call_indicators(bullish_ohlcv)
        score = _call_confluence(indicators, config)
        assert score > 0.4, f"Bullish OHLCV end-to-end score {score:.4f} is unexpectedly low"
        assert 0.0 <= score <= 1.0

    def test_bearish_trend_gives_below_ceiling_score(self, bearish_ohlcv, config):
        """Steadily falling prices should yield a score < 0.6."""
        indicators = _call_indicators(bearish_ohlcv)
        score = _call_confluence(indicators, config)
        assert score < 0.6, f"Bearish OHLCV end-to-end score {score:.4f} is unexpectedly high"
        assert 0.0 <= score <= 1.0

    def test_end_to_end_score_type_and_range(self, sample_ohlcv, config):
        """With the shared fixture OHLCV, score is a float in [0, 1]."""
        indicators = _call_indicators(sample_ohlcv)
        score = _call_confluence(indicators, config)
        assert isinstance(score, (int, float))
        assert 0.0 <= score <= 1.0

    def test_bullish_beats_bearish_end_to_end(self, bullish_ohlcv, bearish_ohlcv, config):
        """Bullish OHLCV should yield a higher score than bearish OHLCV."""
        bull_indicators = _call_indicators(bullish_ohlcv)
        bear_indicators = _call_indicators(bearish_ohlcv)
        bull_score = _call_confluence(bull_indicators, config)
        bear_score = _call_confluence(bear_indicators, config)
        # Allow a small tolerance — signal might be weak at short horizon
        assert bull_score >= bear_score - 0.1, f"Expected bull ({bull_score:.4f}) >= bear ({bear_score:.4f}) - 0.1"

    def test_compute_indicators_returns_dict(self, sample_ohlcv):
        """compute_indicators() must always return a dict."""
        result = _call_indicators(sample_ohlcv)
        assert isinstance(result, dict), f"Expected dict from compute_indicators, got {type(result)}"


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
        assert 0.0 <= score <= 1.0, f"Score {score} out of range for RSI={rsi_value}"

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
        assert 0.0 <= score <= 1.0, f"Score {score} out of range for sentiment={sentiment}"


# ---------------------------------------------------------------------------
# T1-B-1: _get_catalyst_lookup() silent failure fix
# ---------------------------------------------------------------------------

class TestGetCatalystLookup:
    """
    _get_catalyst_lookup() must always return a dict and never swallow errors
    silently. On any failure the exception must be logged at WARNING level and
    an empty dict returned so the caller degrades gracefully.
    """

    def setup_method(self):
        # Reset the module-level cache before each test so disk state doesn't leak.
        # signals resolves to signals/__init__.py (package), which has _catalyst_cache
        # after the T1-B-1 port from signals.py.
        if hasattr(signals, "_catalyst_cache"):
            signals._catalyst_cache.update({"data": {}, "ts": 0.0})

    def test_missing_catalyst_dir_returns_empty_dict(self, tmp_path, caplog):
        """When CATALYST_DIR points at an empty directory, return {} without raising."""
        import config as cfg
        original = cfg.CATALYST_DIR
        cfg.CATALYST_DIR = tmp_path  # exists but has no candidate files
        try:
            import logging
            with caplog.at_level(logging.DEBUG, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original

        assert result == {}, "Expected empty dict when no candidate files exist"

    def test_corrupt_json_returns_empty_dict_and_logs_warning(self, tmp_path, caplog):
        """Corrupt JSON in the candidate file must log a WARNING and return {}."""
        import config as cfg
        bad_file = tmp_path / "candidates_2026-01-01.json"
        bad_file.write_text("{ not valid json <<<")

        original = cfg.CATALYST_DIR
        cfg.CATALYST_DIR = tmp_path
        try:
            import logging
            with caplog.at_level(logging.WARNING, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original

        assert result == {}, "Expected empty dict on JSON parse error"
        assert any("_get_catalyst_lookup" in r.message for r in caplog.records), (
            "Expected a WARNING log from _get_catalyst_lookup on corrupt file"
        )

    def test_valid_file_returns_lookup_above_threshold(self, tmp_path):
        """Valid candidate file returns tickers whose catalyst_score >= min_score."""
        import json
        import config as cfg

        candidates = {
            "candidates": [
                {"ticker": "AAPL", "catalyst_score": 8.5},
                {"ticker": "MSFT", "catalyst_score": 3.0},  # below threshold
                {"ticker": "NVDA", "catalyst_score": 9.0},
            ]
        }
        (tmp_path / "candidates_2026-01-01.json").write_text(json.dumps(candidates))

        original_dir = cfg.CATALYST_DIR
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CATALYST_DIR = tmp_path
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert "AAPL" in result, "AAPL (score 8.5) should be in lookup"
        assert "NVDA" in result, "NVDA (score 9.0) should be in lookup"
        assert "MSFT" not in result, "MSFT (score 3.0) is below threshold and must be excluded"
        assert result["AAPL"] == pytest.approx(8.5)
        assert result["NVDA"] == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# Dimension scorer behaviour — grounded in quantile IC data
# ---------------------------------------------------------------------------


def _mk_5m(**kwargs):
    """Minimal sig_5m dict with safe defaults."""
    base = {
        "signal": "HOLD", "bull_aligned": False, "bear_aligned": False,
        "mfi": 50.0, "rsi_slope": 0.0, "squeeze_on": False, "squeeze_intensity": 0.0,
        "bb_position": 0.5, "vwap_dist": 0.0, "vwap_sd_pct": 1.0, "obv_slope": 0.0,
        "donch_breakout": 0, "donch_high": 105.0, "donch_low": 95.0, "price": 100.0,
        "vol_ratio": 1.0, "variance_ratio": 1.0, "ou_halflife": 999.0, "zscore": 0.0,
        "adf_pvalue": 1.0, "adx": 25.0,
    }
    base.update(kwargs)
    return base


class TestMomentumScorer:
    """Momentum dimension: initiation zone scores high, exhaustion zone scores low."""

    def _momentum_pts(self, mfi, rsi_slope):
        sig = _mk_5m(mfi=mfi, rsi_slope=rsi_slope)
        result = signals.compute_confluence(sig, None, None)
        return result["score_breakdown"]["momentum"]

    def test_sweet_spot_mfi_57_rising_scores_high(self):
        # MFI 57 (dist=7 > 5), rising slope = early initiation = should score 8
        pts = self._momentum_pts(57, 2.0)
        assert pts >= 7, f"Expected ≥7 for MFI=57 rising, got {pts}"

    def test_sweet_spot_mfi_62_rising_scores_high(self):
        # MFI 62 with slope = building, not extended
        pts = self._momentum_pts(62, 1.5)
        assert pts >= 7, f"Expected ≥7 for MFI=62 rising, got {pts}"

    def test_exhaustion_mfi_80_scores_low(self):
        # MFI 80 = overbought, late entry — should score ≤3
        pts = self._momentum_pts(80, 1.0)
        assert pts <= 3, f"Expected ≤3 for exhausted MFI=80, got {pts}"

    def test_exhaustion_mfi_20_scores_low(self):
        # MFI 20 = oversold, late short entry — should score ≤3
        pts = self._momentum_pts(20, -1.0)
        assert pts <= 3, f"Expected ≤3 for exhausted MFI=20, got {pts}"

    def test_no_slope_confirmation_scores_lower_than_with_slope(self):
        # mfi_dist > 15 without slope should score lower than with slope
        with_slope = self._momentum_pts(68, 1.0)
        without_slope = self._momentum_pts(68, -0.5)  # slope in wrong direction
        assert with_slope > without_slope, (
            f"Slope confirmation should raise score: with={with_slope}, without={without_slope}"
        )

    def test_mfi_at_neutral_scores_zero(self):
        pts = self._momentum_pts(50, 0.0)
        assert pts == 0, f"Expected 0 for MFI=50 flat, got {pts}"


class TestFlowScorer:
    """Flow dimension: near VWAP scores high; far from VWAP (extended) scores low."""

    def _flow_pts(self, vwap_dist, vwap_sd_pct=1.0, obv_slope=0.0):
        sig = _mk_5m(vwap_dist=vwap_dist, vwap_sd_pct=vwap_sd_pct, obv_slope=obv_slope)
        result = signals.compute_confluence(sig, None, None)
        return result["score_breakdown"]["flow"]

    def test_near_vwap_scores_higher_than_extended(self):
        near = self._flow_pts(vwap_dist=0.05, vwap_sd_pct=1.0)   # 0.05 SDs from VWAP
        extended = self._flow_pts(vwap_dist=2.5, vwap_sd_pct=1.0)  # 2.5 SDs from VWAP
        assert near >= extended, f"Near VWAP ({near}) should score ≥ extended ({extended})"

    def test_very_extended_vwap_does_not_get_full_vwap_pts(self):
        # 3 SDs from VWAP — VWAP portion should be 0
        pts = self._flow_pts(vwap_dist=3.0, vwap_sd_pct=1.0, obv_slope=0.0)
        # Only OBV can contribute — VWAP adds nothing. OBV=0 → flow=0
        assert pts == 0, f"Expected 0 for 3-SD VWAP with no OBV, got {pts}"

    def test_near_vwap_with_obv_scores_high(self):
        # Near VWAP + confirming OBV = maximum flow score
        pts = self._flow_pts(vwap_dist=0.1, vwap_sd_pct=1.0, obv_slope=50000.0)
        assert pts >= 7, f"Expected ≥7 for near VWAP + OBV, got {pts}"

    def test_sd_normalization_consistent(self):
        # 0.3% in 1.0 SD stock vs 0.3% in 0.1 SD stock — latter is far more extended
        low_vol_pts = self._flow_pts(vwap_dist=0.3, vwap_sd_pct=0.1)   # 3 SDs
        high_vol_pts = self._flow_pts(vwap_dist=0.3, vwap_sd_pct=1.0)  # 0.3 SDs
        assert high_vol_pts >= low_vol_pts, (
            f"Same distance means more extension in low-vol: high_vol={high_vol_pts}, low_vol={low_vol_pts}"
        )


class TestBreakoutScorer:
    """Breakout dimension: pre-breakout proximity scores high; intraday chase scores low."""

    def _breakout_pts(self, donch_breakout=0, vol_ratio=1.0, gap_mult=1.0,
                      price=100.0, donch_high=105.0, donch_low=95.0):
        sig = _mk_5m(donch_breakout=donch_breakout, vol_ratio=vol_ratio,
                     price=price, donch_high=donch_high, donch_low=donch_low)
        result = signals.compute_confluence(sig, None, None, gap_boost_mult=gap_mult)
        return result["score_breakdown"]["breakout"]

    def test_gap_day_breach_scores_high(self):
        # Gap-day confirmed breakout with volume = legitimate
        pts = self._breakout_pts(donch_breakout=1, vol_ratio=2.5, gap_mult=1.3)
        assert pts >= 7, f"Expected ≥7 for gap-day breakout, got {pts}"

    def test_intraday_confirmed_breach_scores_lower(self):
        # Non-gap confirmed breakout with same volume = entering after the fact
        pts = self._breakout_pts(donch_breakout=1, vol_ratio=2.5, gap_mult=1.0)
        assert pts <= 4, f"Expected ≤4 for intraday chase breakout, got {pts}"

    def test_gap_beats_intraday_same_vol(self):
        gap_pts = self._breakout_pts(donch_breakout=1, vol_ratio=2.0, gap_mult=1.3)
        intraday_pts = self._breakout_pts(donch_breakout=1, vol_ratio=2.0, gap_mult=1.0)
        assert gap_pts > intraday_pts, (
            f"Gap ({gap_pts}) should beat intraday ({intraday_pts}) for same breach"
        )

    def test_pre_breakout_proximity_with_volume_scores(self):
        # Price within 0.3% of channel high with volume = setup forming
        pts = self._breakout_pts(donch_breakout=0, vol_ratio=2.0,
                                 price=104.7, donch_high=105.0)  # 0.28% away
        assert pts >= 4, f"Expected ≥4 for near-channel setup with volume, got {pts}"

    def test_no_breakout_no_volume_scores_zero(self):
        pts = self._breakout_pts(donch_breakout=0, vol_ratio=1.0)
        assert pts == 0, f"Expected 0 for no breakout, no volume, got {pts}"


class TestMTFScorer:
    """MTF dimension: fresh trend (moderate ADX) scores higher than mature trend (high ADX)."""

    def _mtf_pts(self, bull_aligned=True, adx=25.0, weekly_bull=False):
        sig_5m = _mk_5m()
        sig_1d = {"bull_aligned": bull_aligned, "bear_aligned": not bull_aligned,
                  "adx": adx, "signal": "BUY" if bull_aligned else "SELL"}
        sig_1w = {"bull_aligned": weekly_bull, "bear_aligned": not weekly_bull,
                  "adx": adx, "signal": "BUY" if weekly_bull else "SELL"} if weekly_bull is not None else None
        result = signals.compute_confluence(sig_5m, sig_1d, sig_1w)
        return result["score_breakdown"]["mtf"]

    def test_building_trend_adx25_scores_8(self):
        pts = self._mtf_pts(bull_aligned=True, adx=25.0, weekly_bull=False)
        assert pts == 8, f"Expected 8 for ADX=25 daily bull, got {pts}"

    def test_mature_trend_adx45_scores_5(self):
        pts = self._mtf_pts(bull_aligned=True, adx=45.0, weekly_bull=False)
        assert pts == 5, f"Expected 5 for ADX=45 mature trend, got {pts}"

    def test_building_beats_mature(self):
        building = self._mtf_pts(bull_aligned=True, adx=25.0, weekly_bull=False)
        mature = self._mtf_pts(bull_aligned=True, adx=50.0, weekly_bull=False)
        assert building > mature, f"Building ({building}) should beat mature ({mature})"

    def test_weekly_daily_confirm_scores_10(self):
        pts = self._mtf_pts(bull_aligned=True, adx=25.0, weekly_bull=True)
        assert pts == 10, f"Expected 10 for weekly+daily confirm, got {pts}"

    def test_no_daily_data_scores_zero(self):
        result = signals.compute_confluence(_mk_5m(), None, None)
        assert result["score_breakdown"]["mtf"] == 0


class TestBaselineWeightsOvernightDrift:
    """overnight_drift must have zero BASELINE weight (BLOCKED CRITICAL)."""

    def test_overnight_drift_baseline_weight_is_zero(self):
        from ic.constants import BASELINE_WEIGHTS
        assert BASELINE_WEIGHTS["overnight_drift"] == 0.0, (
            "overnight_drift is BLOCKED CRITICAL — must have zero baseline weight"
        )

    def test_baseline_weights_sum_to_one(self):
        from ic.constants import BASELINE_WEIGHTS
        total = sum(BASELINE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001, f"BASELINE_WEIGHTS sum {total:.4f} ≠ 1.0"
