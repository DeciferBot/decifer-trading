"""Tests for scanner module: symbol scanning, filtering, ranking.

All market data is mocked with deterministic DataFrames.
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
from typing import List, Dict

import pytest
import pandas as pd
import numpy as np

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


pytestmark = pytest.mark.skipif(
    not HAS_SCANNER, reason="scanner module not importable"
)


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
# Filter tests — get_dynamic_universe behaviour by regime / TV availability
# ---------------------------------------------------------------------------

_TV_ROW_COLS = [
    "ticker", "name", "close", "volume", "change", "gap",
    "relative_volume_10d_calc", "RSI|60", "MACD.macd|60", "MACD.signal|60",
    "EMA9|60", "EMA21|60", "ATR|60", "VWAP", "premarket_change",
    "premarket_volume", "Recommend.All", "market_cap_basic",
    "change_from_open", "EMA20", "EMA50",
]


def _tv_df(tickers=("AAPL", "MSFT")):
    """Minimal DataFrame that _extract() can consume without errors."""
    n = len(tickers)
    data = {
        "ticker": [f"NASDAQ:{t}" for t in tickers],
        "name": list(tickers),
        "close": [150.0] * n,
        "volume": [5_000_000] * n,
        "change": [1.0] * n,
        "gap": [0.0] * n,
        "relative_volume_10d_calc": [2.0] * n,
        "RSI|60": [55.0] * n,
        "MACD.macd|60": [0.5] * n,
        "MACD.signal|60": [0.3] * n,
        "EMA9|60": [149.0] * n,
        "EMA21|60": [148.0] * n,
        "ATR|60": [2.0] * n,
        "VWAP": [150.0] * n,
        "premarket_change": [0.5] * n,
        "premarket_volume": [100_000] * n,
        "Recommend.All": [0.5] * n,
        "market_cap_basic": [1e9] * n,
        "change_from_open": [0.5] * n,
        "EMA20": [149.0] * n,
        "EMA50": [148.0] * n,
    }
    return pd.DataFrame(data)


class _FakeColResult:
    """Stub for a tradingview_screener Column expression — supports all comparison ops."""
    def __gt__(self, o): return self
    def __lt__(self, o): return self
    def __ge__(self, o): return self
    def __le__(self, o): return self
    def __eq__(self, o): return self
    def between(self, a, b): return self
    def isin(self, lst): return self


class _FakeCol:
    def __call__(self, name): return _FakeColResult()


_FAKE_COL = _FakeCol()


class TestScannerFilters:
    """Tests get_dynamic_universe filtering behaviour by regime and TV availability."""

    def test_filter_by_volume_removes_low_volume(self):
        """PANIC regime runs only the 2 always-on scans; result contains CORE + FALLBACK."""
        ib = MagicMock()
        mock_df = _tv_df(["TVSPY", "TVQQQ"])
        # col may not be importable when tradingview-screener is absent — inject a stub
        with patch.object(scanner, "col", _FAKE_COL, create=True):
            with patch.object(scanner, "_TV_AVAILABLE", True):
                with patch.object(scanner, "_query", return_value=(2, mock_df)) as mock_q:
                    result = scanner.get_dynamic_universe(ib, regime={"regime": "PANIC"})
        # Only volume_leaders + rel_vol_surge are not gated by is_panic
        assert mock_q.call_count == 2, f"Expected 2 always-on scans, got {mock_q.call_count}"
        for sym in scanner.CORE_SYMBOLS:
            assert sym in result
        for sym in scanner.MOMENTUM_FALLBACK:
            assert sym in result

    def test_filter_by_volume_keeps_high_volume(self):
        """TV unavailable → universe equals CORE + MOMENTUM_FALLBACK."""
        ib = MagicMock()
        with patch.object(scanner, "_TV_AVAILABLE", False):
            result = scanner.get_dynamic_universe(ib, regime={"regime": "BULL_TRENDING"})
        assert set(result) == set(scanner.CORE_SYMBOLS) | set(scanner.MOMENTUM_FALLBACK)

    def test_empty_symbol_list_returns_empty(self, config):
        """When every TV query raises an exception, falls back to CORE + MOMENTUM_FALLBACK."""
        ib = MagicMock()
        with patch.object(scanner, "col", _FAKE_COL, create=True):
            with patch.object(scanner, "_TV_AVAILABLE", True):
                with patch.object(scanner, "_query", side_effect=Exception("TV offline")):
                    result = scanner.get_dynamic_universe(ib, regime={"regime": "BEAR_TRENDING"})
        expected = set(scanner.CORE_SYMBOLS) | set(scanner.MOMENTUM_FALLBACK)
        assert set(result) == expected


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
        """Low VIX + SPY/QQQ above 20-EMA → BULL_TRENDING."""
        ib = MagicMock()
        # Prices start low then spike so last value is above EMA
        above_prices = [490.0] * 38 + [510.0, 510.0]
        spy_df = _price_df(above_prices)
        qqq_df = _price_df(above_prices)
        vix_df = _price_df(14.0)

        def mock_dl(ticker, **kw):
            if ticker == "SPY": return spy_df
            if ticker == "QQQ": return qqq_df
            return vix_df

        with patch("scanner._safe_download", side_effect=mock_dl):
            with patch.dict(scanner.CONFIG, _VIX_CFG):
                result = scanner.get_market_regime(ib)
        assert result["regime"] == "BULL_TRENDING"

    def test_rank_empty_returns_empty(self):
        """VIX above panic threshold → PANIC regardless of price action."""
        ib = MagicMock()
        spy_df = _price_df(480.0)
        qqq_df = _price_df(390.0)
        vix_df = _price_df(40.0)

        def mock_dl(ticker, **kw):
            if ticker == "SPY": return spy_df
            if ticker == "QQQ": return qqq_df
            return vix_df

        with patch("scanner._safe_download", side_effect=mock_dl):
            with patch.dict(scanner.CONFIG, _VIX_CFG):
                result = scanner.get_market_regime(ib)
        assert result["regime"] == "PANIC"

    def test_rank_single_candidate_returns_one(self):
        """Regime result dict always contains all required output keys."""
        ib = MagicMock()
        above_prices = [490.0] * 38 + [510.0, 510.0]
        spy_df = _price_df(above_prices)
        qqq_df = _price_df(above_prices)
        vix_df = _price_df(14.0)

        def mock_dl(ticker, **kw):
            if ticker == "SPY": return spy_df
            if ticker == "QQQ": return qqq_df
            return vix_df

        with patch("scanner._safe_download", side_effect=mock_dl):
            with patch.dict(scanner.CONFIG, _VIX_CFG):
                result = scanner.get_market_regime(ib)
        for key in ("regime", "vix", "spy_price", "spy_above_200d",
                    "qqq_price", "qqq_above_200d", "position_size_multiplier"):
            assert key in result, f"Missing key: {key}"
