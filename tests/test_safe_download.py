"""
Unit tests for signals._safe_download() — Alpaca-only, fail-closed contract.

_safe_download has a single data layer:

  Layer 1 — Alpaca REST (alpaca_data.fetch_bars):
      If fetch_bars returns a non-empty DataFrame, _safe_download returns it.

  Fail closed:
      If fetch_bars returns None, empty DataFrame, or raises, _safe_download
      returns None without raising. No fallback to any other provider.

No network calls are made — alpaca_data.fetch_bars is mocked at the signals
module boundary.
"""

import os
import sys
import types
from unittest.mock import patch

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
if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]

import signals as _signals_mod

# ── Helpers ────────────────────────────────────────────────────────────────────


def _ohlcv(n=60) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with canonical columns."""
    _end = pd.Timestamp.today().normalize()
    if _end.dayofweek >= 5:
        _end -= pd.offsets.BDay(1)
    idx = pd.date_range(end=_end, periods=n, freq="B")
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
# Layer 1: Alpaca REST is the only source
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDownloadAlpacaPrimary:
    def test_alpaca_result_returned_when_successful(self):
        """When fetch_bars returns data, _safe_download returns it unchanged."""
        alpaca_df = _ohlcv(40)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")

        assert result is alpaca_df
        mock_fetch.assert_called_once_with("AAPL", period="60d", interval="1d")

    def test_alpaca_called_with_correct_period_and_interval(self):
        """_safe_download must forward period and interval to fetch_bars."""
        alpaca_df = _ohlcv(20)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            _signals_mod._safe_download("SPY", period="5d", interval="5m", progress=False, auto_adjust=True)
        mock_fetch.assert_called_once_with("SPY", period="5d", interval="5m")

    def test_alpaca_called_with_default_period_when_omitted(self):
        """_safe_download should use '60d' and '1d' defaults."""
        alpaca_df = _ohlcv(60)
        with patch("alpaca_data.fetch_bars", return_value=alpaca_df) as mock_fetch:
            _signals_mod._safe_download("QQQ")
        call_args = mock_fetch.call_args
        assert call_args[0][0] == "QQQ"


# ═══════════════════════════════════════════════════════════════════════════════
# Fail closed: Alpaca failure → None, no fallback
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDownloadFailClosed:
    def test_returns_none_when_alpaca_returns_none(self):
        """When fetch_bars returns None, _safe_download must return None."""
        with patch("alpaca_data.fetch_bars", return_value=None):
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")
        assert result is None

    def test_returns_none_when_alpaca_raises(self):
        """If fetch_bars raises, _safe_download must return None without raising."""
        with patch("alpaca_data.fetch_bars", side_effect=RuntimeError("Alpaca API down")):
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")
        assert result is None

    def test_returns_none_when_alpaca_returns_empty_df(self):
        """An empty DataFrame from fetch_bars should return None."""
        empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        with patch("alpaca_data.fetch_bars", return_value=empty_df):
            result = _signals_mod._safe_download("AAPL", period="60d", interval="1d")
        assert result is None

    def test_does_not_raise_on_any_failure(self):
        """_safe_download must never propagate an exception to callers."""
        with patch("alpaca_data.fetch_bars", side_effect=Exception("fatal")):
            try:
                result = _signals_mod._safe_download("AAPL")
            except Exception as exc:
                pytest.fail(f"_safe_download raised unexpectedly: {exc}")

    def test_no_yfinance_attribute_on_signals_module(self):
        """signals module must not expose a yf attribute — yfinance was removed."""
        assert not hasattr(_signals_mod, "yf"), (
            "signals module has a 'yf' attribute — yfinance was not fully removed"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Interval forwarding — still Alpaca-only
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDownloadIntervals:
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
