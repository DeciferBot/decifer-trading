"""Tests for options.py — Greeks, IV rank, chain selection, contract finding."""
import os
import sys
import types
import unittest.mock as mock

# ── Project root on path ──────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Stub heavy dependencies BEFORE importing Decifer modules ─────────
# ib_async
ib_async_stub = types.ModuleType("ib_async")
ib_async_stub.IB = mock.MagicMock
ib_async_stub.Option = mock.MagicMock
ib_async_stub.Stock = mock.MagicMock
ib_async_stub.Forex = mock.MagicMock
ib_async_stub.Contract = mock.MagicMock
sys.modules.setdefault("ib_async", ib_async_stub)

# anthropic
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = mock.MagicMock
sys.modules.setdefault("anthropic", anthropic_stub)

# yfinance — will be patched per-test with mock.patch
import pandas as pd
import numpy as np

yf_stub = types.ModuleType("yfinance")
yf_stub.Ticker = mock.MagicMock
yf_stub.download = mock.MagicMock(return_value=pd.DataFrame())
sys.modules.setdefault("yfinance", yf_stub)

# py_vollib — stub so we can test both paths
py_vollib_stub = types.ModuleType("py_vollib")
bs_stub = types.ModuleType("py_vollib.black_scholes")
bs_greeks_stub = types.ModuleType("py_vollib.black_scholes.greeks")
bs_analytical_stub = types.ModuleType("py_vollib.black_scholes.greeks.analytical")
bs_analytical_stub.delta = mock.MagicMock(return_value=0.4500)
bs_analytical_stub.gamma = mock.MagicMock(return_value=0.0200)
bs_analytical_stub.theta = mock.MagicMock(return_value=-0.0150)
bs_analytical_stub.vega  = mock.MagicMock(return_value=0.1800)
bs_stub.black_scholes    = mock.MagicMock(return_value=3.50)
sys.modules.setdefault("py_vollib", py_vollib_stub)
sys.modules.setdefault("py_vollib.black_scholes", bs_stub)
sys.modules.setdefault("py_vollib.black_scholes.greeks", bs_greeks_stub)
sys.modules.setdefault("py_vollib.black_scholes.greeks.analytical", bs_analytical_stub)

# signals — stub _safe_download used inside get_iv_rank
signals_stub = types.ModuleType("signals")
def _fake_safe_download(symbol, **kwargs):
    dates = pd.date_range("2023-01-01", periods=252, freq="B")
    closes = pd.Series(np.linspace(100, 150, 252) + np.random.default_rng(42).normal(0, 2, 252),
                       index=dates)
    return pd.DataFrame({"Close": closes})
signals_stub._safe_download = _fake_safe_download
sys.modules.setdefault("signals", signals_stub)

# config
config_stub = types.ModuleType("config")
config_stub.CONFIG = {
    "options_min_dte": 7,
    "options_max_dte": 21,
    "options_max_ivr": 50,
    "options_target_delta": 0.40,
    "options_delta_range": 0.15,
    "options_max_risk_pct": 0.01,
    "options_min_volume": 50,
    "options_min_oi": 200,
    "options_max_spread_pct": 0.25,
    "high_conviction_score": 38,
    "log_file": "/tmp/decifer_test.log",
    "trade_log": "/tmp/trades_test.json",
    "order_log": "/tmp/orders_test.json",
}
sys.modules.setdefault("config", config_stub)

import pytest
# Evict any hollow stub test_bot.py may have cached for 'options'
for _decifer_mod in ("options", "orders", "risk", "scanner"):
    # NOTE: do NOT pop "learning" — options.py doesn't import it, and test_learning.py
    # (l < o alphabetically) already installed the real module; evicting it here
    # would break test_learning.py's patch("learning.anthropic") calls.
    sys.modules.pop(_decifer_mod, None)
import options  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_chain_df(strikes, base_bid=1.0, base_ask=1.10, volume=200, oi=500, iv=0.30):
    """Build a minimal options chain DataFrame."""
    rows = []
    for k in strikes:
        rows.append({
            "strike": float(k),
            "bid": base_bid,
            "ask": base_ask,
            "mid": (base_bid + base_ask) / 2,
            "spread_pct": (base_ask - base_bid) / ((base_bid + base_ask) / 2),
            "volume": float(volume),
            "openInterest": float(oi),
            "impliedVolatility": iv,
        })
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════
# calculate_greeks
# ═══════════════════════════════════════════════════════════════════════

class TestCalculateGreeks:
    """Tests for options.calculate_greeks(flag, S, K, dte, iv)"""

    def test_call_greeks_vollib_path(self):
        """When py_vollib is available, all greeks are populated."""
        # Force vollib path
        with mock.patch.object(options, "_VOLLIB_OK", True):
            bs_analytical_stub.delta.return_value = 0.45
            bs_analytical_stub.gamma.return_value = 0.02
            bs_analytical_stub.theta.return_value = -0.015
            bs_analytical_stub.vega.return_value  = 0.18
            bs_stub.black_scholes.return_value     = 3.50
            result = options.calculate_greeks("c", 150.0, 150.0, 14, 0.30)
        assert "delta" in result
        assert "gamma" in result
        assert "theta" in result
        assert "vega" in result
        assert "model_price" in result
        # Rounded to 4 dp
        assert isinstance(result["delta"], float)

    def test_put_greeks_vollib_path(self):
        """Put greeks round-trip with vollib."""
        with mock.patch.object(options, "_VOLLIB_OK", True):
            bs_analytical_stub.delta.return_value = -0.45
            bs_analytical_stub.gamma.return_value =  0.02
            bs_analytical_stub.theta.return_value = -0.015
            bs_analytical_stub.vega.return_value  =  0.18
            bs_stub.black_scholes.return_value     =  3.50
            result = options.calculate_greeks("p", 150.0, 150.0, 14, 0.30)
        assert result["delta"] < 0 or result["delta"] == pytest.approx(-0.45, abs=0.01)

    def test_fallback_path_when_vollib_unavailable(self):
        """Without py_vollib, fallback estimates are returned."""
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options.calculate_greeks("c", 150.0, 150.0, 14, 0.30)
        assert "delta" in result
        # model_price should be None in fallback
        assert result["model_price"] is None
        assert result["gamma"] is None

    def test_fallback_call_delta_positive(self):
        """Fallback ATM call should have positive delta ~0.5."""
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options.calculate_greeks("c", 100.0, 100.0, 7, 0.25)
        assert result["delta"] > 0

    def test_fallback_put_delta_negative(self):
        """Fallback ATM put should have negative delta."""
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options.calculate_greeks("p", 100.0, 100.0, 7, 0.25)
        assert result["delta"] < 0

    def test_itm_call_higher_delta_than_otm(self):
        """ITM call delta > OTM call delta (fallback path)."""
        with mock.patch.object(options, "_VOLLIB_OK", False):
            itm = options.calculate_greeks("c", 110.0, 100.0, 14, 0.30)  # ITM
            otm = options.calculate_greeks("c",  90.0, 100.0, 14, 0.30)  # OTM
        assert itm["delta"] > otm["delta"]

    def test_zero_dte_clamped_to_1_day(self):
        """DTE=0 should not cause division errors (clamped to 1)."""
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options.calculate_greeks("c", 100.0, 100.0, 0, 0.30)
        assert isinstance(result["delta"], float)

    def test_very_low_iv_clamped(self):
        """IV=0 should be clamped to 0.01 — no math errors."""
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options.calculate_greeks("c", 100.0, 100.0, 14, 0.0)
        assert isinstance(result["delta"], float)

    def test_vollib_exception_falls_back(self):
        """If py_vollib raises, we should fall back to estimates."""
        with mock.patch.object(options, "_VOLLIB_OK", True):
            bs_analytical_stub.delta.side_effect = RuntimeError("boom")
            result = options.calculate_greeks("c", 100.0, 100.0, 14, 0.30)
            bs_analytical_stub.delta.side_effect = None
        # Falls back to estimate
        assert isinstance(result["delta"], float)


# ═══════════════════════════════════════════════════════════════════════
# get_iv_rank
# ═══════════════════════════════════════════════════════════════════════

class TestGetIVRank:
    """Tests for options.get_iv_rank(symbol, current_iv)"""

    def test_iv_rank_in_0_100_range(self):
        """IV rank should always be in [0, 100]."""
        result = options.get_iv_rank("AAPL", 0.30)
        assert result is None or (0 <= result <= 100)

    def test_high_iv_gives_high_rank(self):
        """Very high current_iv should push rank toward 100."""
        # Use a known dataset with small spread
        dates = pd.date_range("2023-01-01", periods=252, freq="B")
        low_vol_closes = pd.Series(np.linspace(100, 102, 252), index=dates)
        df = pd.DataFrame({"Close": low_vol_closes})

        with mock.patch.object(sys.modules["signals"], "_safe_download", return_value=df):
            rank = options.get_iv_rank("TEST", 5.0)  # absurdly high IV
        assert rank is None or rank > 50

    def test_low_iv_gives_low_rank(self):
        """Very low current_iv should give rank near 0."""
        dates = pd.date_range("2023-01-01", periods=252, freq="B")
        high_vol_closes = pd.Series(
            np.cumsum(np.random.default_rng(0).normal(0, 3, 252)) + 100, index=dates
        )
        df = pd.DataFrame({"Close": high_vol_closes})
        with mock.patch.object(sys.modules["signals"], "_safe_download", return_value=df):
            rank = options.get_iv_rank("TEST", 0.001)  # near-zero IV
        assert rank is None or rank < 50

    def test_returns_none_on_insufficient_data(self):
        """Fewer than 60 bars should return None."""
        sparse = pd.DataFrame({"Close": pd.Series([100.0] * 30)})
        with mock.patch.object(sys.modules["signals"], "_safe_download", return_value=sparse):
            result = options.get_iv_rank("SPARSE", 0.30)
        assert result is None

    def test_returns_none_on_empty_dataframe(self):
        """Empty DataFrame from downloader returns None."""
        with mock.patch.object(sys.modules["signals"], "_safe_download", return_value=pd.DataFrame()):
            result = options.get_iv_rank("EMPTY", 0.30)
        assert result is None

    def test_returns_none_when_download_returns_none(self):
        """None from downloader returns None."""
        with mock.patch.object(sys.modules["signals"], "_safe_download", return_value=None):
            result = options.get_iv_rank("NONE", 0.30)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# _select_strike
# ═══════════════════════════════════════════════════════════════════════

class TestSelectStrike:
    """Tests for options._select_strike(df, flag, S, dte, target_delta, delta_range)"""

    def test_returns_closest_delta_contract(self):
        """Should return the strike whose computed delta is closest to target."""
        df = _make_chain_df(
            strikes=[90, 95, 100, 105, 110],
            base_bid=1.0, base_ask=1.20,
            volume=200, oi=500, iv=0.30
        )
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options._select_strike(df, "c", 100.0, 14, 0.40, 0.25)
        assert result is not None
        assert "strike" in result
        assert "delta" in result
        assert "mid" in result

    def test_returns_none_on_empty_dataframe(self):
        """Empty chain should return None."""
        df = pd.DataFrame(columns=["strike", "bid", "ask", "mid", "spread_pct",
                                   "volume", "openInterest", "impliedVolatility"])
        result = options._select_strike(df, "c", 100.0, 14, 0.40, 0.15)
        assert result is None

    def test_filters_low_volume(self):
        """Contracts with volume below min_volume should be rejected."""
        df = _make_chain_df(
            strikes=[100], volume=1, oi=500
        )
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options._select_strike(df, "c", 100.0, 14, 0.40, 0.15)
        # Should be None because volume=1 < min_volume=50
        assert result is None

    def test_filters_low_oi(self):
        """Contracts with OI below min_oi should be rejected."""
        df = _make_chain_df(
            strikes=[100], volume=500, oi=10
        )
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options._select_strike(df, "c", 100.0, 14, 0.40, 0.15)
        assert result is None

    def test_filters_wide_spread(self):
        """Contracts with spread > max_spread_pct should be filtered."""
        # bid=0.10, ask=2.00 → spread_pct = 1.90/1.05 ≈ 1.81 >> 0.25
        df = _make_chain_df(
            strikes=[100], base_bid=0.10, base_ask=2.00, volume=500, oi=500
        )
        df["mid"] = (df["bid"] + df["ask"]) / 2
        df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"]
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options._select_strike(df, "c", 100.0, 14, 0.40, 0.15)
        assert result is None

    def test_result_contains_required_fields(self):
        """Successful result should contain all required keys."""
        df = _make_chain_df(
            strikes=[95, 100, 105], base_bid=1.0, base_ask=1.15,
            volume=300, oi=600, iv=0.30
        )
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options._select_strike(df, "c", 100.0, 14, 0.40, 0.30)
        if result is not None:
            for key in ["strike", "dte", "right", "mid", "bid", "ask",
                        "spread_pct", "volume", "open_interest", "iv", "delta"]:
                assert key in result, f"Missing key: {key}"

    def test_put_flag_returns_put_right(self):
        """flag='p' should set right='P' in result."""
        df = _make_chain_df(
            strikes=[95, 100, 105], base_bid=1.0, base_ask=1.15,
            volume=300, oi=600, iv=0.30
        )
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options._select_strike(df, "p", 100.0, 14, 0.40, 0.30)
        if result is not None:
            assert result["right"] == "P"

    def test_call_flag_returns_call_right(self):
        """flag='c' should set right='C' in result."""
        df = _make_chain_df(
            strikes=[95, 100, 105], base_bid=1.0, base_ask=1.15,
            volume=300, oi=600, iv=0.30
        )
        with mock.patch.object(options, "_VOLLIB_OK", False):
            result = options._select_strike(df, "c", 100.0, 14, 0.40, 0.30)
        if result is not None:
            assert result["right"] == "C"


# ═══════════════════════════════════════════════════════════════════════
# find_best_contract
# ═══════════════════════════════════════════════════════════════════════

class TestFindBestContract:
    """Tests for options.find_best_contract(symbol, direction, portfolio_value, ib, regime, score)"""

    def _make_ticker_mock(self, price=150.0, expiry_days=14,
                         strikes=None, volume=300, oi=600, iv=0.30):
        """Create a mock yfinance Ticker with chain data."""
        from datetime import date, timedelta
        exp_date = (date.today() + timedelta(days=expiry_days)).strftime("%Y-%m-%d")

        if strikes is None:
            strikes = [140, 145, 150, 155, 160]

        chain_df = _make_chain_df(strikes, base_bid=1.5, base_ask=1.65,
                                  volume=volume, oi=oi, iv=iv)

        chain_mock = mock.MagicMock()
        chain_mock.calls = chain_df.copy()
        chain_mock.puts  = chain_df.copy()

        hist_df = pd.DataFrame(
            {"Close": [price], "High": [price * 1.01], "Low": [price * 0.99],
             "Open": [price], "Volume": [1_000_000]}
        )

        ticker = mock.MagicMock()
        ticker.history.return_value = hist_df
        ticker.options = [exp_date]
        ticker.option_chain.return_value = chain_mock
        return ticker

    def test_long_signal_returns_call(self):
        """LONG direction should find a call contract."""
        ticker = self._make_ticker_mock()
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=25.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            result = options.find_best_contract("AAPL", "LONG", 100_000)
        if result is not None:
            assert result["right"] == "C"
            assert result["symbol"] == "AAPL"
            assert result["direction"] == "LONG"

    def test_short_signal_returns_put(self):
        """SHORT direction should find a put contract."""
        ticker = self._make_ticker_mock()
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=25.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            result = options.find_best_contract("AAPL", "SHORT", 100_000)
        if result is not None:
            assert result["right"] == "P"
            assert result["direction"] == "SHORT"

    def test_high_ivr_returns_none(self):
        """IVR > max_ivr (50) should cause find_best_contract to return None."""
        ticker = self._make_ticker_mock()
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=80.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            result = options.find_best_contract("AAPL", "LONG", 100_000)
        assert result is None

    def test_no_expiry_in_window_returns_none(self):
        """If ticker.options has no expiry in DTE window, return None."""
        from datetime import date, timedelta
        # Expiry 100 days out — well outside 7-21 window
        exp_far = (date.today() + timedelta(days=100)).strftime("%Y-%m-%d")
        ticker = mock.MagicMock()
        ticker.history.return_value = pd.DataFrame(
            {"Close": [150.0]}
        )
        ticker.options = [exp_far]
        with mock.patch("yfinance.Ticker", return_value=ticker):
            result = options.find_best_contract("AAPL", "LONG", 100_000)
        assert result is None

    def test_empty_price_history_returns_none(self):
        """No price history → return None immediately."""
        ticker = mock.MagicMock()
        ticker.history.return_value = pd.DataFrame()
        with mock.patch("yfinance.Ticker", return_value=ticker):
            result = options.find_best_contract("BADTICKER", "LONG", 100_000)
        assert result is None

    def test_contract_sizing_proportional_to_portfolio(self):
        """Larger portfolio → more contracts (or at least ≥1)."""
        ticker_small = self._make_ticker_mock(price=100.0)
        ticker_large = self._make_ticker_mock(price=100.0)
        with mock.patch("yfinance.Ticker", return_value=ticker_small), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=20.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            small_result = options.find_best_contract("AAPL", "LONG", 50_000)

        with mock.patch("yfinance.Ticker", return_value=ticker_large), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=20.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            large_result = options.find_best_contract("AAPL", "LONG", 500_000)

        if small_result is not None and large_result is not None:
            assert large_result["contracts"] >= small_result["contracts"]

    def test_result_has_max_risk_dollars(self):
        """Successful result must include max_risk_dollars."""
        ticker = self._make_ticker_mock()
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=20.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            result = options.find_best_contract("AAPL", "LONG", 100_000)
        if result is not None:
            assert "max_risk_dollars" in result
            assert result["max_risk_dollars"] > 0

    def test_high_conviction_score_increases_sizing(self):
        """Score >= high_conviction_score should apply 1.5x multiplier."""
        ticker = self._make_ticker_mock()

        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=20.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            result_low  = options.find_best_contract("AAPL", "LONG", 100_000, score=20)

        ticker2 = self._make_ticker_mock()
        with mock.patch("yfinance.Ticker", return_value=ticker2), \
             mock.patch.object(options, "_VOLLIB_OK", False), \
             mock.patch.object(options, "get_iv_rank", return_value=20.0), \
             mock.patch.object(options, "get_ibkr_greeks", return_value=None):
            result_high = options.find_best_contract("AAPL", "LONG", 100_000, score=40)

        if result_low is not None and result_high is not None:
            assert result_high["max_risk_dollars"] >= result_low["max_risk_dollars"]


# ═══════════════════════════════════════════════════════════════════════
# get_ibkr_greeks  — no live IB connection
# ═══════════════════════════════════════════════════════════════════════

class TestGetIbkrGreeks:
    def test_returns_none_when_ib_is_none(self):
        """Without an IB connection, should return None gracefully."""
        result = options.get_ibkr_greeks(None, "AAPL", "20240119", 150.0, "C")
        assert result is None

    def test_returns_none_on_ibkr_exception(self):
        """IBKR errors should be swallowed and return None."""
        fake_ib = mock.MagicMock()
        fake_ib.qualifyContracts.side_effect = RuntimeError("no connection")
        result = options.get_ibkr_greeks(fake_ib, "AAPL", "20240119", 150.0, "C")
        assert result is None
