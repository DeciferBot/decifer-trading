"""
Unit tests for signals._safe_download() — Alpaca→yfinance priority routing.

The migration made Alpaca REST the primary bar data source.  These tests
confirm the three-layer contract:

  Layer 1 — Alpaca REST (alpaca_data.fetch_bars):
      If fetch_bars returns a non-empty DataFrame, _safe_download returns it.
      yfinance is NOT called.

  Layer 2 — yfinance fallback:
      If fetch_bars returns None, _safe_download falls back to
      yf.Ticker(symbol).history(**kwargs) with up to 3 attempts.

  Layer 3 — total failure:
      If both layers fail, _safe_download returns None without raising.

No network calls are made — both alpaca_data.fetch_bars and
yf.Ticker().history() are mocked at the signals module boundary.
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Project root ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy deps that signals.py imports at module level ───────────────────
for _mod_name in (
    "ib_async",
    "ib_insync",
    "anthropic",
    "praw",
    "feedparser",
    "alpaca",
    "alpaca.data",
    "alpaca.data.historical",
    "alpaca.data.live",
    "alpaca.data.enums",
    "alpaca.data.timeframe",
    "alpaca.data.requests",
):
    sys.modules.setdefault(_mod_name, types.ModuleType(_mod_name))

# Stub colorama
_col = types.ModuleType("colorama")
_col.Fore = types.SimpleNamespace(YELLOW="", GREEN="", CYAN="", RED="", WHITE="", MAGENTA="", RESET="")
_col.Style = types.SimpleNamespace(RESET_ALL="", BRIGHT="")
_col.init = lambda **kw: None
sys.modules.setdefault("colorama", _col)

# ── Import the real signals module ─────────────────────────────────────────────
# Remove any stub planted by other test files (test_signal_pipeline.py,
# test_bot.py) so we get the real implementation.
if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]

import signals as _signals_mod

# ── Helpers ────────────────────────────────────────────────────────────────────


def _ohlcv(n=60) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with canonical columns."""
    idx = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame(
        {
            "Open": close + np.random.randn(n) * 0.2,
            "High": close + np.abs(np.random.randn(n) * 0.3),
            "Low": close - np.abs(np.random.randn(n) * 0.3),
            "Close": close,
            "Volume": np.random.randint(1_000_000, 10_000_000, n).astype(float),
        },
        index=idx,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 1: Alpaca REST is primary
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDownloadAlpacaPrimary:
    def test_alpaca_result_returned_when_successful(self):
        """When fetch_bars returns data, _safe_download returns it unchanged."""
        alpaca_df = _ohlcv(40)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")

        assert result is alpaca_df
        mock_fetch.assert_called_once_with("AAPL", period="60d", interval="1d")

    def test_yfinance_not_called_when_alpaca_succeeds(self):
        """Alpaca success must short-circuit — yfinance must never be called."""
        alpaca_df = _ohlcv(40)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df):
            with patch.object(_signals_mod.yf.Ticker("AAPL"), "history") as mock_yf:
                _signals_mod._safe_download("AAPL", period="60d", interval="1d")
        # We cannot easily assert on yf.Ticker().history since Ticker is constructed
        # inside _safe_download. Instead we mock at the yf module level:
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df):
            with patch.object(_signals_mod, "yf") as mock_yf_mod:
                _signals_mod._safe_download("AAPL", period="60d", interval="1d")
        mock_yf_mod.Ticker.assert_not_called()

    def test_alpaca_called_with_correct_period_and_interval(self):
        """_safe_download must forward period and interval to fetch_bars."""
        alpaca_df = _ohlcv(20)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            _signals_mod._safe_download("SPY", period="5d", interval="5m", progress=False, auto_adjust=True)
        mock_fetch.assert_called_once_with("SPY", period="5d", interval="5m")


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 2: yfinance fallback when Alpaca returns None
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDownloadYfinanceFallback:
    def test_falls_back_when_alpaca_returns_none(self):
        """When fetch_bars returns None, must fall through to yfinance."""
        yf_df = _ohlcv(60)
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = yf_df

        with patch("alpaca_data.fetch_bars", return_value=None), patch.object(_signals_mod, "yf") as mock_yf:
            mock_yf.Ticker.return_value = fake_ticker
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d", progress=False, auto_adjust=True)

        assert result is yf_df
        mock_yf.Ticker.assert_called_with("AAPL")
        fake_ticker.history.assert_called_once()

    def test_falls_back_when_alpaca_raises(self):
        """If fetch_bars raises any exception, must fall back silently."""
        yf_df = _ohlcv(60)
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = yf_df

        with patch("alpaca_data.fetch_bars", side_effect=RuntimeError("Alpaca API down")):
            with patch.object(_signals_mod, "yf") as mock_yf:
                mock_yf.Ticker.return_value = fake_ticker
                result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")

        assert result is yf_df

    def test_falls_back_when_alpaca_returns_empty_df(self):
        """An empty DataFrame from fetch_bars should also trigger fallback."""
        empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        yf_df = _ohlcv(60)
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = yf_df

        # _safe_download checks `len(df) > 0` — empty df fails the check
        with patch("alpaca_data.fetch_bars", return_value=empty_df):
            with patch.object(_signals_mod, "yf") as mock_yf:
                mock_yf.Ticker.return_value = fake_ticker
                result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")

        assert result is yf_df

    def test_progress_kwarg_stripped_before_yfinance_call(self):
        """yfinance .history() does not accept progress= — must be stripped."""
        yf_df = _ohlcv(60)
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = yf_df

        with patch("alpaca_data.fetch_bars", return_value=None), patch.object(_signals_mod, "yf") as mock_yf:
            mock_yf.Ticker.return_value = fake_ticker
            _signals_mod._safe_download("AAPL", period="60d", interval="1d", progress=False, auto_adjust=True)

        # progress= must not appear in the yfinance call kwargs
        call_kwargs = fake_ticker.history.call_args[1]
        assert "progress" not in call_kwargs, "'progress' kwarg leaked into yfinance call — will raise TypeError"


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 3: total failure — both sources return None
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDownloadTotalFailure:
    def test_returns_none_when_both_sources_fail(self):
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = None

        with patch("alpaca_data.fetch_bars", return_value=None), patch.object(_signals_mod, "yf") as mock_yf:
            mock_yf.Ticker.return_value = fake_ticker
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")

        assert result is None

    def test_returns_none_when_yfinance_raises(self):
        fake_ticker = MagicMock()
        fake_ticker.history.side_effect = Exception("network timeout")

        with patch("alpaca_data.fetch_bars", return_value=None), patch.object(_signals_mod, "yf") as mock_yf:
            mock_yf.Ticker.return_value = fake_ticker
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")

        assert result is None

    def test_does_not_raise_on_any_failure(self):
        """_safe_download must never propagate an exception to callers."""
        with patch("alpaca_data.fetch_bars", side_effect=Exception("fatal")):
            with patch.object(_signals_mod, "yf") as mock_yf:
                mock_yf.Ticker.side_effect = Exception("also fatal")
                try:
                    result = _signals_mod._safe_download("AAPL")
                except Exception as exc:
                    pytest.fail(f"_safe_download raised unexpectedly: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# 5m interval: Alpaca REST path (Layer 1 of fetch_multi_timeframe is BAR_CACHE,
# but _safe_download is Layer 3 of the 5m hierarchy — must still work)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDownload5mInterval:
    def test_5m_interval_forwarded_to_alpaca(self):
        """5m is a valid Alpaca interval — must reach fetch_bars."""
        alpaca_df = _ohlcv(50)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            result = _signals_mod._safe_download("AAPL", period="5d", interval="5m")
        mock_fetch.assert_called_once_with("AAPL", period="5d", interval="5m")
        assert result is alpaca_df

    def test_1d_interval_forwarded_to_alpaca(self):
        alpaca_df = _ohlcv(60)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            _signals_mod._safe_download("SPY", period="60d", interval="1d")
        mock_fetch.assert_called_once_with("SPY", period="60d", interval="1d")

    def test_1wk_interval_forwarded_to_alpaca(self):
        alpaca_df = _ohlcv(52)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            _signals_mod._safe_download("QQQ", period="1y", interval="1wk")
        mock_fetch.assert_called_once_with("QQQ", period="1y", interval="1wk")
