"""Tests for scanner module: symbol scanning, filtering, ranking.

All market data is mocked with deterministic DataFrames.
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
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Evict any hollow stub (e.g. MagicMock planted by test_bot.py) so we get the
# real scanner module with actual filter_by_volume / rank_candidates functions.
sys.modules.pop("scanner", None)
try:
    import scanner

    # Confirm it's the real module (a MagicMock stub would have no __file__)
    HAS_SCANNER = hasattr(scanner, "__file__") and scanner.__file__ is not None
except (ImportError, Exception):
    HAS_SCANNER = False


pytestmark = pytest.mark.skipif(not HAS_SCANNER, reason="scanner module not importable")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_symbols():
    return ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"]


@pytest.fixture()
def high_volume_data():
    """OHLCV with above-average volume."""
    np.random.seed(10)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
    close = 150.0 + np.random.randn(60)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 2.0,
            "Low": close - 2.0,
            "Close": close,
            "Volume": np.random.randint(5_000_000, 10_000_000, 60).astype(float),
        },
        index=dates,
    )


@pytest.fixture()
def low_volume_data():
    """OHLCV with very low volume (should be filtered out)."""
    np.random.seed(11)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
    close = 150.0 + np.random.randn(60)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.random.randint(1_000, 5_000, 60).astype(float),
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# get_dynamic_universe — Tier-A floor + promoted-list behaviour
# ---------------------------------------------------------------------------


class TestScannerFilters:
    """Tests get_dynamic_universe Tier-A floor behaviour."""

    def test_universe_includes_core_floor(self):
        """Tier-A floor (CORE_SYMBOLS + CORE_EQUITIES) is always present in the universe."""
        ib = MagicMock()
        # Force promoter to return empty so we exercise pure Tier-A.
        with patch("universe_promoter.load_promoted_universe", return_value=[]):
            result = scanner.get_dynamic_universe(ib, regime={"regime": "TRENDING_UP", "vix": 15.0})
        for sym in scanner.CORE_SYMBOLS:
            assert sym in result
        for sym in scanner.CORE_EQUITIES:
            assert sym in result

    def test_universe_under_extreme_vix_unchanged(self):
        """EXTREME_STRESS no longer prunes the universe — risk gating happens downstream."""
        ib = MagicMock()
        extreme_regime = {"regime": "PANIC", "vix": 40.0, "vix_1h_change": 0.05}
        with patch("universe_promoter.load_promoted_universe", return_value=[]):
            result = scanner.get_dynamic_universe(ib, regime=extreme_regime)
        for sym in scanner.CORE_SYMBOLS:
            assert sym in result
        for sym in scanner.CORE_EQUITIES:
            assert sym in result

    def test_promoted_symbols_unioned_into_universe(self):
        """Tier-B promoted symbols are added on top of Tier-A floor."""
        ib = MagicMock()
        promoted = ["ZZZA", "ZZZB", "ZZZC"]
        with patch("universe_promoter.load_promoted_universe", return_value=promoted):
            result = scanner.get_dynamic_universe(ib, regime={"regime": "TRENDING_UP", "vix": 15.0})
        for sym in promoted:
            assert sym in result


# ---------------------------------------------------------------------------
# Ranking tests — get_market_regime regime classification
# ---------------------------------------------------------------------------

_VIX_CFG = {
    "vix_panic_min": 35,
    "vix_spike_pct": 0.15,
    "vix_bull_max": 18,
    "vix_choppy_max": 25,
    # Disable breadth in unit tests — ^MMTH is not mocked
    "breadth_regime": {"enabled": False},
}


def _price_df(prices, n=40):
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="h")
    if isinstance(prices, (int, float)):
        prices = [float(prices)] * n
    return pd.DataFrame({"Close": list(prices)}, index=dates)


class TestScannerRanking:
    """Tests get_market_regime regime classification via mocked _safe_download."""

    def test_rank_candidates_returns_sorted_list(self):
        """Low VIX + SPY/QQQ above 200d MA → TRENDING_UP (renamed from BULL_TRENDING)."""
        scanner._last_good_regime = None  # clear state from prior tests
        ib = MagicMock()
        # Intraday prices: last value 510 is above the 200d MA (490)
        above_prices = [490.0] * 38 + [510.0, 510.0]
        spy_df = _price_df(above_prices)
        qqq_df = _price_df(above_prices)
        vix_df = _price_df(14.0)
        # Daily DataFrame: 200 bars all at 490 → 200d MA = 490, current 510 > 490
        daily_dates = pd.date_range(end=pd.Timestamp.today(), periods=200, freq="D")
        daily_df = pd.DataFrame({"Close": [490.0] * 200}, index=daily_dates)

        def mock_dl(ticker, **kw):
            if kw.get("interval") == "1d":
                if ticker in ("SPY", "QQQ"):
                    return daily_df
                return pd.DataFrame()
            if ticker == "SPY":
                return spy_df
            if ticker == "QQQ":
                return qqq_df
            return vix_df

        with patch("scanner._regime_download", side_effect=mock_dl), patch.dict(scanner.CONFIG, _VIX_CFG):
            result = scanner.get_market_regime(ib)
        assert result["regime"] == "TRENDING_UP"

    def test_rank_empty_returns_empty(self):
        """VIX above panic threshold → CAPITULATION (renamed from PANIC) regardless of price action."""
        ib = MagicMock()
        spy_df = _price_df(480.0)
        qqq_df = _price_df(390.0)
        vix_df = _price_df(40.0)

        def mock_dl(ticker, **kw):
            if ticker == "SPY":
                return spy_df
            if ticker == "QQQ":
                return qqq_df
            return vix_df

        with patch("scanner._regime_download", side_effect=mock_dl), patch.dict(scanner.CONFIG, _VIX_CFG):
            result = scanner.get_market_regime(ib)
        assert result["regime"] == "CAPITULATION"

    def test_rank_single_candidate_returns_one(self):
        """Regime result dict always contains all required output keys."""
        ib = MagicMock()
        above_prices = [490.0] * 38 + [510.0, 510.0]
        spy_df = _price_df(above_prices)
        qqq_df = _price_df(above_prices)
        vix_df = _price_df(14.0)
        daily_dates = pd.date_range(end=pd.Timestamp.today(), periods=200, freq="D")
        daily_df = pd.DataFrame({"Close": [490.0] * 200}, index=daily_dates)

        def mock_dl(ticker, **kw):
            if kw.get("interval") == "1d":
                if ticker in ("SPY", "QQQ"):
                    return daily_df
                return pd.DataFrame()
            if ticker == "SPY":
                return spy_df
            if ticker == "QQQ":
                return qqq_df
            return vix_df

        with patch("scanner._regime_download", side_effect=mock_dl), patch.dict(scanner.CONFIG, _VIX_CFG):
            result = scanner.get_market_regime(ib)
        for key in (
            "regime",
            "vix",
            "spy_price",
            "spy_above_200d",
            "qqq_price",
            "qqq_above_200d",
            "position_size_multiplier",
        ):
            assert key in result, f"Missing key: {key}"
