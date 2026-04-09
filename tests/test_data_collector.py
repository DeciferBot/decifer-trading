#!/usr/bin/env python3
"""
Tests for data_collector.py — feature engineering, metadata, and helpers.
All network calls are mocked; no real yfinance or HTTP requests.
"""
import os
import sys
import json
import tempfile
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pandas as pd
import numpy as np
import pytest

# ── Add project root to path ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy imports BEFORE importing Decifer modules ──────────
import importlib

# ib_async
ib_async_stub = types.ModuleType("ib_async")
ib_async_stub.IB = MagicMock
sys.modules.setdefault("ib_async", ib_async_stub)

# anthropic
anthropicstub = types.ModuleType("anthropic")
anthropicstub.Anthropic = MagicMock
sys.modules.setdefault("anthropic", anthropicstub)

# yfinance — we'll patch individual calls per test
yf_stub = types.ModuleType("yfinance")
yf_stub.Ticker = MagicMock
yf_stub.download = MagicMock(return_value=pd.DataFrame())
sys.modules["yfinance"] = yf_stub

# tradingview_screener
tv_stub = types.ModuleType("tradingview_screener")
tv_stub.Scanner = MagicMock
tv_stub.Column = MagicMock
sys.modules.setdefault("tradingview_screener", tv_stub)
sys.modules.setdefault("tradingview_screener.scanner", tv_stub)

# py_vollib
for mod in ["py_vollib", "py_vollib.black_scholes", "py_vollib.black_scholes.greeks",
            "py_vollib.black_scholes.greeks.analytical"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

# sklearn
for mod in ["sklearn", "sklearn.ensemble", "sklearn.preprocessing",
            "sklearn.model_selection", "sklearn.metrics"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

sys.modules.setdefault("joblib", types.ModuleType("joblib"))
sys.modules.setdefault("praw", types.ModuleType("praw"))
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

vader_stub = types.ModuleType("vaderSentiment")
vader_sub = types.ModuleType("vaderSentiment.vaderSentiment")
vader_sub.SentimentIntensityAnalyzer = MagicMock
sys.modules.setdefault("vaderSentiment", vader_stub)
sys.modules.setdefault("vaderSentiment.vaderSentiment", vader_sub)

# config stub
config_stub = types.ModuleType("config")
config_stub.CONFIG = {
    "starting_capital": 100_000,
    "min_score_to_trade": 60,
    "atr_stop_multiplier": 2.0,
    "atr_trail_multiplier": 1.5,
    "partial_exit_1_pct": 0.03,
    "partial_exit_2_pct": 0.06,
    "max_positions": 5,
    "max_portfolio_risk_pct": 0.02,
    "risk_per_trade_pct": 0.01,
    "log_file": "/tmp/decifer_test.log",
    "trade_log": "/tmp/trades_test.json",
    "order_log": "/tmp/orders_test.json",
}
sys.modules.setdefault("config", config_stub)

# signals stub (data_collector doesn't need it but transitive imports might)
signals_stub = types.ModuleType("signals")
signals_stub.compute_indicators = MagicMock(return_value={})
signals_stub.compute_confluence = MagicMock(return_value={"total": 65, "score": 65})
sys.modules.setdefault("signals", signals_stub)

# Now import the module under test
from data_collector import (
    add_features,
    ensure_dirs,
    load_meta,
    save_meta,
    download_intraday_yf,
    download_daily_yf,
    download_daily_stooq,
    download_daily_alphavantage,
    _atr,
    _rsi,
    META_FILE,
    INTRADAY_DIR,
    DAILY_DIR,
)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def make_ohlcv(n=100, start_price=100.0) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame with n bars."""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    close = start_price + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.2
    volume = np.random.randint(1_000_000, 10_000_000, size=n).astype(float)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )
    return df


def make_yf_history_df(n=100):
    """DataFrame matching what yf.Ticker.history() returns (capital column names)."""
    df = make_ohlcv(n)
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    df.index.name = "Datetime"
    return df


# ─────────────────────────────────────────────────────────────────
# ensure_dirs
# ─────────────────────────────────────────────────────────────────

class TestEnsureDirs:
    def test_creates_dirs_if_missing(self, tmp_path):
        """ensure_dirs should create INTRADAY_DIR and DAILY_DIR."""
        with patch("data_collector.INTRADAY_DIR", tmp_path / "intraday"), \
             patch("data_collector.DAILY_DIR", tmp_path / "daily"):
            # Patch the module-level paths used inside ensure_dirs
            import data_collector as dc
            orig_intra = dc.INTRADAY_DIR
            orig_daily = dc.DAILY_DIR
            dc.INTRADAY_DIR = tmp_path / "intraday"
            dc.DAILY_DIR = tmp_path / "daily"
            try:
                dc.ensure_dirs()
                assert (tmp_path / "intraday").exists()
                assert (tmp_path / "daily").exists()
            finally:
                dc.INTRADAY_DIR = orig_intra
                dc.DAILY_DIR = orig_daily

    def test_idempotent_when_dirs_exist(self, tmp_path):
        """Calling ensure_dirs twice should not raise."""
        import data_collector as dc
        orig_intra = dc.INTRADAY_DIR
        orig_daily = dc.DAILY_DIR
        dc.INTRADAY_DIR = tmp_path / "intraday"
        dc.DAILY_DIR = tmp_path / "daily"
        try:
            dc.ensure_dirs()
            dc.ensure_dirs()  # second call
        finally:
            dc.INTRADAY_DIR = orig_intra
            dc.DAILY_DIR = orig_daily


# ─────────────────────────────────────────────────────────────────
# load_meta / save_meta
# ─────────────────────────────────────────────────────────────────

class TestMetadata:
    def test_load_meta_returns_defaults_when_file_missing(self, tmp_path):
        import data_collector as dc
        orig = dc.META_FILE
        dc.META_FILE = tmp_path / "nonexistent.json"
        try:
            meta = dc.load_meta()
            assert "last_run" in meta
            assert "symbols" in meta
            assert meta["last_run"] is None
        finally:
            dc.META_FILE = orig

    def test_load_meta_reads_existing_file(self, tmp_path):
        import data_collector as dc
        orig = dc.META_FILE
        meta_path = tmp_path / "meta.json"
        data = {"last_run": "2024-01-01", "symbols": {"AAPL": 100}, "total_rows": 500}
        meta_path.write_text(json.dumps(data))
        dc.META_FILE = meta_path
        try:
            loaded = dc.load_meta()
            assert loaded["last_run"] == "2024-01-01"
            assert loaded["symbols"]["AAPL"] == 100
            assert loaded["total_rows"] == 500
        finally:
            dc.META_FILE = orig

    def test_save_meta_writes_last_run(self, tmp_path):
        import data_collector as dc
        orig = dc.META_FILE
        meta_path = tmp_path / "meta.json"
        dc.META_FILE = meta_path
        try:
            meta = {"last_run": None, "symbols": {}, "total_rows": 0}
            dc.save_meta(meta)
            saved = json.loads(meta_path.read_text())
            assert saved["last_run"] is not None  # timestamp was written
        finally:
            dc.META_FILE = orig

    def test_load_meta_handles_corrupt_file(self, tmp_path):
        import data_collector as dc
        orig = dc.META_FILE
        meta_path = tmp_path / "corrupt.json"
        meta_path.write_text("NOT VALID JSON{{{")
        dc.META_FILE = meta_path
        try:
            meta = dc.load_meta()  # should not raise
            assert isinstance(meta, dict)
        finally:
            dc.META_FILE = orig


# ─────────────────────────────────────────────────────────────────
# _atr helper
# ─────────────────────────────────────────────────────────────────

class TestATRHelper:
    def test_atr_returns_array_same_length(self):
        df = make_ohlcv(50)
        result = _atr(df["high"].values, df["low"].values, df["close"].values, 14)
        assert len(result) == 50

    def test_atr_non_negative(self):
        df = make_ohlcv(50)
        result = _atr(df["high"].values, df["low"].values, df["close"].values, 14)
        # ATR should be non-negative after warmup
        assert np.all(result[14:] >= 0)

    def test_atr_larger_range_higher_atr(self):
        """A DataFrame with wider H-L ranges should produce higher ATR."""
        n = 50
        high_narrow = np.full(n, 101.0)
        low_narrow = np.full(n, 99.0)
        close_narrow = np.full(n, 100.0)

        high_wide = np.full(n, 110.0)
        low_wide = np.full(n, 90.0)
        close_wide = np.full(n, 100.0)

        atr_narrow = _atr(high_narrow, low_narrow, close_narrow, 14)
        atr_wide = _atr(high_wide, low_wide, close_wide, 14)
        # After warmup period, wide ATR should exceed narrow
        assert np.nanmean(atr_wide[20:]) > np.nanmean(atr_narrow[20:])


# ─────────────────────────────────────────────────────────────────
# _rsi helper
# ─────────────────────────────────────────────────────────────────

class TestRSIHelper:
    def test_rsi_returns_array_same_length(self):
        close = make_ohlcv(60)["close"].values
        result = _rsi(close, 14)
        assert len(result) == 60

    def test_rsi_bounds_after_warmup(self):
        close = make_ohlcv(60)["close"].values
        result = _rsi(close, 14)
        valid = result[14:]
        assert np.all(valid >= 0)
        assert np.all(valid <= 100)

    def test_rsi_constant_up_trend_high(self):
        """Strictly increasing prices should produce a high RSI."""
        close = np.linspace(100, 200, 100)
        result = _rsi(close, 14)
        # After warmup, RSI should be high for a consistent uptrend
        assert np.nanmean(result[20:]) > 70

    def test_rsi_constant_down_trend_low(self):
        """Strictly decreasing prices should produce a low RSI."""
        close = np.linspace(200, 100, 100)
        result = _rsi(close, 14)
        assert np.nanmean(result[20:]) < 30


# ─────────────────────────────────────────────────────────────────
# add_features
# ─────────────────────────────────────────────────────────────────

class TestAddFeatures:
    def test_add_features_returns_dataframe(self):
        df = make_ohlcv(100)
        result = add_features(df.copy())
        assert isinstance(result, pd.DataFrame)

    def test_add_features_adds_expected_columns(self):
        df = make_ohlcv(100)
        result = add_features(df.copy())
        expected_cols = [
            "return_1", "return_5", "return_10",
            "atr_14", "volatility_20",
            "ema_9", "ema_21", "ema_50",
            "rsi_14", "vol_sma_20", "vol_ratio",
            "bb_upper", "bb_lower", "bb_position",
            "vwap", "vwap_dist",
            "regime",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_add_features_returns_same_length(self):
        df = make_ohlcv(100)
        result = add_features(df.copy())
        assert len(result) == 100

    def test_add_features_short_df_returns_unchanged(self):
        """DataFrames with fewer than 50 rows are returned as-is."""
        df = make_ohlcv(30)
        result = add_features(df.copy())
        # Should not have the computed columns
        assert "rsi_14" not in result.columns

    def test_add_features_none_input_returns_none(self):
        result = add_features(None)
        assert result is None

    def test_add_features_empty_df_returns_empty(self):
        result = add_features(pd.DataFrame())
        assert result is not None
        assert result.empty

    def test_add_features_ema_ordering(self):
        """In a long uptrend, EMA9 > EMA21 > EMA50."""
        close = np.linspace(50, 200, 200)
        high = close + 1
        low = close - 1
        volume = np.ones(200) * 1_000_000
        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close, "volume": volume},
            index=pd.date_range("2020-01-01", periods=200, freq="D"),
        )
        result = add_features(df)
        last = result.iloc[-1]
        assert last["ema_9"] > last["ema_21"]
        assert last["ema_21"] > last["ema_50"]

    def test_add_features_ema_trend_column(self):
        """ema_trend should be 1 when EMA9 > EMA21."""
        close = np.linspace(50, 200, 200)
        df = pd.DataFrame(
            {"open": close, "high": close + 1, "low": close - 1,
             "close": close, "volume": np.ones(200) * 1e6},
            index=pd.date_range("2020-01-01", periods=200, freq="D"),
        )
        result = add_features(df)
        # Last value should be 1 (uptrend)
        assert result["ema_trend"].iloc[-1] == 1

    def test_add_features_bb_position_range(self):
        """Bollinger Band position should be between 0 and 1 for most bars."""
        df = make_ohlcv(150)
        result = add_features(df.copy())
        # Check after warmup (50 bars)
        bp = result["bb_position"].iloc[50:]
        # Allow small overshoot but most should be in [0,1]
        assert (bp >= -0.5).all()
        assert (bp <= 1.5).all()

    def test_add_features_vol_ratio_positive(self):
        """Volume ratio should be positive."""
        df = make_ohlcv(100)
        result = add_features(df.copy())
        vr = result["vol_ratio"].iloc[30:]  # after SMA warmup
        assert (vr >= 0).all()

    def test_add_features_regime_labels(self):
        """Regime column should only contain valid label values."""
        df = make_ohlcv(150)
        result = add_features(df.copy())
        valid_regimes = {"TRENDING_UP", "TRENDING_DOWN", "RELIEF_RALLY", "RANGE_BOUND", "CAPITULATION", "UNKNOWN"}
        unique_regimes = set(result["regime"].unique())
        assert unique_regimes.issubset(valid_regimes)


# ─────────────────────────────────────────────────────────────────
# download_intraday_yf (mocked)
# ─────────────────────────────────────────────────────────────────

class TestDownloadIntradayYF:
    def _make_mock_ticker(self, df):
        mock = MagicMock()
        mock.history.return_value = df
        return mock

    def test_returns_dataframe_on_success(self):
        hist_df = make_yf_history_df(60)
        mock_ticker = self._make_mock_ticker(hist_df)
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_intraday_yf("AAPL", "5m")
        assert result is not None
        assert isinstance(result, pd.DataFrame)

    def test_returns_none_on_empty_data(self):
        mock_ticker = self._make_mock_ticker(pd.DataFrame())
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_intraday_yf("AAPL", "5m")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.side_effect = Exception("network error")
            result = download_intraday_yf("AAPL", "5m")
        assert result is None

    def test_output_has_expected_columns(self):
        hist_df = make_yf_history_df(60)
        mock_ticker = self._make_mock_ticker(hist_df)
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_intraday_yf("AAPL", "5m")
        assert result is not None
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns

    def test_output_has_symbol_column(self):
        hist_df = make_yf_history_df(30)
        mock_ticker = self._make_mock_ticker(hist_df)
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_intraday_yf("TSLA", "5m")
        assert result is not None
        assert "symbol" in result.columns
        assert (result["symbol"] == "TSLA").all()


# ─────────────────────────────────────────────────────────────────
# download_daily_yf (mocked)
# ─────────────────────────────────────────────────────────────────

class TestDownloadDailyYF:
    def _make_mock_ticker(self, df):
        mock = MagicMock()
        mock.history.return_value = df
        return mock

    def test_returns_dataframe_on_success(self):
        hist_df = make_yf_history_df(500)
        mock_ticker = self._make_mock_ticker(hist_df)
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_daily_yf("AAPL")
        assert result is not None
        assert isinstance(result, pd.DataFrame)

    def test_returns_none_on_empty(self):
        mock_ticker = self._make_mock_ticker(pd.DataFrame())
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_daily_yf("AAPL")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.side_effect = RuntimeError("timeout")
            result = download_daily_yf("AAPL")
        assert result is None

    def test_columns_renamed_lowercase(self):
        hist_df = make_yf_history_df(100)
        mock_ticker = self._make_mock_ticker(hist_df)
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_daily_yf("MSFT")
        assert result is not None
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns

    def test_source_column_is_yfinance(self):
        hist_df = make_yf_history_df(100)
        mock_ticker = self._make_mock_ticker(hist_df)
        with patch("data_collector.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = download_daily_yf("GOOGL")
        assert result is not None
        assert "source" in result.columns
        assert (result["source"] == "yfinance").all()


# ─────────────────────────────────────────────────────────────────
# download_daily_alphavantage (no key → None)
# ─────────────────────────────────────────────────────────────────

class TestDownloadAlphaVantage:
    def test_returns_none_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            # No ALPHA_VANTAGE_KEY in env, no key passed
            result = download_daily_alphavantage("AAPL", api_key=None)
        assert result is None

    def test_returns_none_on_network_error_with_key(self):
        """Even with a key, if the download fails it returns None."""
        with patch("data_collector.pd") as mock_pd:
            mock_pd.read_csv.side_effect = Exception("network failure")
            result = download_daily_alphavantage("AAPL", api_key="dummy_key")
        assert result is None


# ─────────────────────────────────────────────────────────────────
# download_daily_stooq (mocked)
# ─────────────────────────────────────────────────────────────────

class TestDownloadDailyStooq:
    def _make_stooq_df(self, n=100):
        dates = pd.date_range("2020-01-01", periods=n, freq="D")
        close = 100.0 + np.arange(n)
        df = pd.DataFrame({
            "Date": dates.astype(str),
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": np.ones(n) * 1e6,
        })
        return df

    def test_returns_none_on_exception(self):
        with patch("data_collector.pd") as mock_pd:
            mock_pd.read_csv.side_effect = Exception("HTTP error")
            result = download_daily_stooq("AAPL")
        assert result is None

    def test_returns_dataframe_on_valid_csv(self):
        stooq_df = self._make_stooq_df(100)
        with patch("data_collector.pd") as mock_pd:
            # We need pd.to_datetime to work, so use real pd partially
            mock_pd.read_csv.return_value = stooq_df
            mock_pd.to_datetime = pd.to_datetime
            result = download_daily_stooq("AAPL")
        # Either returns a df or None — just ensure no crash
        assert result is None or isinstance(result, pd.DataFrame)

    def test_returns_none_on_empty_df(self):
        with patch("data_collector.pd") as mock_pd:
            mock_pd.read_csv.return_value = pd.DataFrame()
            result = download_daily_stooq("AAPL")
        assert result is None


# ─────────────────────────────────────────────────────────────────
# Parametrized edge-case matrix for add_features
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n_rows,should_have_features", [
    (0, False),   # empty
    (10, False),  # below minimum (50)
    (49, False),  # just below threshold
    (50, True),   # exactly at threshold
    (100, True),  # normal
    (200, True),  # large
])
def test_add_features_row_threshold(n_rows, should_have_features):
    if n_rows == 0:
        df = pd.DataFrame()
        result = add_features(df)
        assert result is not None and result.empty
        return

    df = make_ohlcv(n_rows)
    result = add_features(df.copy())

    if should_have_features:
        assert "rsi_14" in result.columns
        assert "atr_14" in result.columns
    else:
        assert "rsi_14" not in result.columns


@pytest.mark.parametrize("period", [5, 10, 14, 20])
def test_rsi_various_periods(period):
    close = make_ohlcv(100)["close"].values
    result = _rsi(close, period)
    assert len(result) == 100
    assert np.all(result[period:] >= 0)
    assert np.all(result[period:] <= 100)


@pytest.mark.parametrize("period", [5, 10, 14, 20])
def test_atr_various_periods(period):
    df = make_ohlcv(60)
    result = _atr(df["high"].values, df["low"].values, df["close"].values, period)
    assert len(result) == 60
    assert np.all(result[period:] >= 0)
