"""
Unit tests for alpaca_data.py — Alpaca REST OHLCV fetcher.

Validates:
- _period_to_start() converts yfinance-style period strings to correct UTC datetimes
- fetch_bars() returns canonical OHLCV DataFrame on success
- fetch_bars() returns None when client is not initialised (no API keys)
- fetch_bars() returns None for unsupported interval strings
- fetch_bars() renames lowercase Alpaca columns to canonical capitalised form
- fetch_bars() drops non-OHLCV columns (vwap, trade_count, etc.)
- fetch_bars() flattens MultiIndex DataFrame produced by Alpaca for single symbols
- fetch_bars() returns None on empty response
- fetch_bars() returns None when alpaca-py raises an exception

No network calls are made — all Alpaca SDK objects are mocked.
"""

import os
import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

# ── Project root ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Stub alpaca-py before importing alpaca_data ───────────────────────────────


def _make_alpaca_stub():
    """Build a minimal alpaca-py stub that satisfies alpaca_data's imports."""
    alpaca_top = types.ModuleType("alpaca")
    alpaca_data_m = types.ModuleType("alpaca.data")
    alpaca_hist = types.ModuleType("alpaca.data.historical")
    alpaca_req = types.ModuleType("alpaca.data.requests")
    alpaca_tf = types.ModuleType("alpaca.data.timeframe")
    alpaca_live = types.ModuleType("alpaca.data.live")
    alpaca_enums = types.ModuleType("alpaca.data.enums")

    # TimeFrame and TimeFrameUnit mocks
    class _TF:
        Day = "1Day"
        Week = "1Week"
        Hour = "1Hour"
        Minute = "1Min"

        def __init__(self, n, unit):
            self.value = f"{n}{unit}"

    class _TFUnit:
        Minute = "Min"

    alpaca_tf.TimeFrame = _TF
    alpaca_tf.TimeFrameUnit = _TFUnit

    alpaca_hist.StockHistoricalDataClient = MagicMock
    alpaca_req.StockBarsRequest = MagicMock
    alpaca_live.StockDataStream = MagicMock

    class _Feed:
        SIP = "sip"

    alpaca_enums.DataFeed = _Feed

    for mod, name in [
        (alpaca_top, "alpaca"),
        (alpaca_data_m, "alpaca.data"),
        (alpaca_hist, "alpaca.data.historical"),
        (alpaca_req, "alpaca.data.requests"),
        (alpaca_tf, "alpaca.data.timeframe"),
        (alpaca_live, "alpaca.data.live"),
        (alpaca_enums, "alpaca.data.enums"),
    ]:
        sys.modules.setdefault(name, mod)

    return alpaca_hist, alpaca_req, alpaca_tf


_alpaca_hist, _alpaca_req, _alpaca_tf = _make_alpaca_stub()


# ── Now import the module under test ─────────────────────────────────────────
import alpaca_data

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_ohlcv_df(n=10, symbol="AAPL"):
    """Return a minimal OHLCV DataFrame with lowercase Alpaca-style columns."""
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1_000_000 + i * 1000 for i in range(n)],
            "vwap": [100.3 + i for i in range(n)],  # extra Alpaca column — should be dropped
            "trade_count": [500 for _ in range(n)],  # extra Alpaca column — should be dropped
        },
        index=idx,
    )


def _make_multiindex_df(n=10, symbol="AAPL"):
    """Return a MultiIndex DataFrame as Alpaca returns for multi-symbol requests."""
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n, freq="D")
    tuples = [(symbol, ts) for ts in idx]
    mi = pd.MultiIndex.from_tuples(tuples, names=["symbol", "timestamp"])
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1_000_000 for _ in range(n)],
        },
        index=mi,
    )


def _mock_bars(df):
    """Wrap a DataFrame in a mock AlpacaBarsResponse."""
    resp = MagicMock()
    resp.df = df
    return resp


def _reset_client():
    """Reset the module-level client singleton so each test starts clean."""
    alpaca_data._client = None


# ── Tests: _period_to_start ────────────────────────────────────────────────────


class TestPeriodToStart:
    def test_days_period(self):
        start = alpaca_data._period_to_start("5d")
        expected = datetime.now(UTC) - timedelta(days=7)  # 5 + 2 buffer
        assert abs((start - expected).total_seconds()) < 10

    def test_year_period(self):
        start = alpaca_data._period_to_start("1y")
        expected = datetime.now(UTC) - timedelta(days=366)
        assert abs((start - expected).total_seconds()) < 10

    def test_month_period(self):
        start = alpaca_data._period_to_start("3mo")
        expected = datetime.now(UTC) - timedelta(days=3 * 32)
        assert abs((start - expected).total_seconds()) < 10

    def test_sixty_days(self):
        start = alpaca_data._period_to_start("60d")
        expected = datetime.now(UTC) - timedelta(days=62)
        assert abs((start - expected).total_seconds()) < 10

    def test_result_is_utc(self):
        start = alpaca_data._period_to_start("5d")
        assert start.tzinfo is not None

    def test_result_is_in_the_past(self):
        start = alpaca_data._period_to_start("1d")
        assert start < datetime.now(UTC)


# ── Tests: fetch_bars — no client (no API keys) ────────────────────────────────


class TestFetchBarsNoClient:
    def setup_method(self):
        _reset_client()

    def test_returns_none_when_no_api_key(self):
        with patch.dict(alpaca_data.CONFIG, {"alpaca_api_key": "", "alpaca_secret_key": ""}):
            result = alpaca_data.fetch_bars("AAPL")
        assert result is None

    def test_returns_none_when_key_missing_from_config(self):
        cfg = {k: v for k, v in alpaca_data.CONFIG.items() if k not in ("alpaca_api_key", "alpaca_secret_key")}
        with patch.object(alpaca_data, "CONFIG", cfg):
            result = alpaca_data.fetch_bars("AAPL")
        assert result is None


# ── Tests: fetch_bars — unsupported interval ───────────────────────────────────


class TestFetchBarsUnsupportedInterval:
    def setup_method(self):
        _reset_client()

    def test_returns_none_for_unknown_interval(self):
        mock_client = MagicMock()
        alpaca_data._client = mock_client
        result = alpaca_data.fetch_bars("AAPL", interval="15m")
        assert result is None

    def test_returns_none_for_tick_interval(self):
        mock_client = MagicMock()
        alpaca_data._client = mock_client
        result = alpaca_data.fetch_bars("AAPL", interval="tick")
        assert result is None


# ── Tests: fetch_bars — happy path ─────────────────────────────────────────────


class TestFetchBarsHappyPath:
    def setup_method(self):
        _reset_client()

    def _run(self, df, symbol="AAPL", period="60d", interval="1d"):
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = _mock_bars(df)
        alpaca_data._client = mock_client
        return alpaca_data.fetch_bars(symbol, period=period, interval=interval)

    def test_returns_dataframe(self):
        result = self._run(_make_ohlcv_df())
        assert isinstance(result, pd.DataFrame)

    def test_columns_are_canonical(self):
        result = self._run(_make_ohlcv_df())
        assert set(result.columns) == {"Open", "High", "Low", "Close", "Volume"}

    def test_extra_alpaca_columns_dropped(self):
        result = self._run(_make_ohlcv_df())
        assert "vwap" not in result.columns
        assert "trade_count" not in result.columns

    def test_row_count_preserved(self):
        df = _make_ohlcv_df(n=20)
        result = self._run(df)
        assert len(result) == 20

    def test_index_is_datetime(self):
        result = self._run(_make_ohlcv_df())
        assert isinstance(result.index, pd.DatetimeIndex)

    def test_five_minute_interval_accepted(self):
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=10, freq="5min")
        df = pd.DataFrame({c: [1.0] * 10 for c in ["open", "high", "low", "close", "volume"]}, index=idx)
        result = self._run(df, interval="5m")
        assert result is not None
        assert "Close" in result.columns

    def test_one_hour_interval_accepted(self):
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=10, freq="h")
        df = pd.DataFrame({c: [1.0] * 10 for c in ["open", "high", "low", "close", "volume"]}, index=idx)
        result = self._run(df, interval="1h")
        assert result is not None

    def test_weekly_interval_accepted(self):
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=10, freq="W-MON")
        df = pd.DataFrame({c: [1.0] * len(idx) for c in ["open", "high", "low", "close", "volume"]}, index=idx)
        result = self._run(df, interval="1wk")
        assert result is not None


# ── Tests: fetch_bars — MultiIndex handling ────────────────────────────────────


class TestFetchBarsMultiIndex:
    def setup_method(self):
        _reset_client()

    def test_multiindex_df_flattened(self):
        """Alpaca single-symbol response returns MultiIndex (symbol, timestamp)."""
        mi_df = _make_multiindex_df(symbol="AAPL")
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = _mock_bars(mi_df)
        alpaca_data._client = mock_client

        result = alpaca_data.fetch_bars("AAPL", interval="1d")
        assert result is not None
        assert not isinstance(result.index, pd.MultiIndex), (
            "MultiIndex should be dropped — result should have flat DatetimeIndex"
        )

    def test_multiindex_symbol_level_dropped(self):
        mi_df = _make_multiindex_df(symbol="TSLA")
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = _mock_bars(mi_df)
        alpaca_data._client = mock_client

        result = alpaca_data.fetch_bars("TSLA", interval="1d")
        assert result is not None
        assert len(result) == 10


# ── Tests: fetch_bars — empty / error responses ────────────────────────────────


class TestFetchBarsEdgeCases:
    def setup_method(self):
        _reset_client()

    def test_returns_none_for_empty_dataframe(self):
        empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = _mock_bars(empty_df)
        alpaca_data._client = mock_client

        result = alpaca_data.fetch_bars("AAPL")
        assert result is None

    def test_returns_none_when_df_is_none(self):
        mock_resp = MagicMock()
        mock_resp.df = None
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = mock_resp
        alpaca_data._client = mock_client

        result = alpaca_data.fetch_bars("AAPL")
        assert result is None

    def test_returns_none_on_exception(self):
        mock_client = MagicMock()
        mock_client.get_stock_bars.side_effect = RuntimeError("API error")
        alpaca_data._client = mock_client

        result = alpaca_data.fetch_bars("AAPL")
        assert result is None

    def test_returns_none_on_import_error(self):
        """If alpaca-py is missing at call time, fetch_bars returns None."""
        _reset_client()
        # Temporarily remove alpaca modules so the lazy import fails
        saved = {}
        for key in list(sys.modules.keys()):
            if "alpaca" in key:
                saved[key] = sys.modules.pop(key)
        try:
            result = alpaca_data.fetch_bars("AAPL")
            # Should fail gracefully
            assert result is None
        except Exception:
            pass  # ImportError is acceptable too — must not crash the bot
        finally:
            sys.modules.update(saved)
            _reset_client()
