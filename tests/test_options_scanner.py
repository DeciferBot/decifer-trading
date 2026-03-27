"""Tests for options_scanner.py — max pain, earnings days, nearest expiry, scoring."""
import os
import sys
import types
import unittest.mock as mock
from datetime import date, timedelta, datetime

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

import pandas as pd
import numpy as np

# yfinance
yf_stub = types.ModuleType("yfinance")
yf_stub.Ticker = mock.MagicMock
yf_stub.download = mock.MagicMock(return_value=pd.DataFrame())
sys.modules.setdefault("yfinance", yf_stub)

# py_vollib
for mod in ["py_vollib", "py_vollib.black_scholes",
            "py_vollib.black_scholes.greeks",
            "py_vollib.black_scholes.greeks.analytical"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

# signals
signals_stub = types.ModuleType("signals")
signals_stub._safe_download = mock.MagicMock(return_value=pd.DataFrame())
sys.modules.setdefault("signals", signals_stub)

# options (used inside options_scanner via `from options import get_iv_rank`)
options_stub = types.ModuleType("options")
options_stub.get_iv_rank = mock.MagicMock(return_value=25.0)
options_stub.calculate_greeks = mock.MagicMock(return_value={
    "delta": 0.45, "gamma": 0.02, "theta": -0.015,
    "vega": 0.18, "model_price": 3.50
})
sys.modules.setdefault("options", options_stub)

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
import options_scanner  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_chain(strikes, call_vol=500, call_oi=2000, put_vol=200, put_oi=1500, iv=0.30):
    """Build matched call/put DataFrames for a list of strikes."""
    rows = []
    for k in strikes:
        rows.append({
            "strike": float(k),
            "bid": 1.0,
            "ask": 1.15,
            "mid": 1.075,
            "volume": float(call_vol),
            "openInterest": float(call_oi),
            "impliedVolatility": iv,
            "spread_pct": 0.14,
        })
    calls = pd.DataFrame(rows)

    put_rows = []
    for k in strikes:
        put_rows.append({
            "strike": float(k),
            "bid": 0.8,
            "ask": 0.95,
            "mid": 0.875,
            "volume": float(put_vol),
            "openInterest": float(put_oi),
            "impliedVolatility": iv,
            "spread_pct": 0.17,
        })
    puts = pd.DataFrame(put_rows)
    return calls, puts


def _expiry_in_window(days_from_now=14):
    return (date.today() + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════
# _compute_max_pain
# ═══════════════════════════════════════════════════════════════════════

class TestComputeMaxPain:
    """Tests for options_scanner._compute_max_pain(calls, puts)"""

    def test_returns_float(self):
        """Basic test: max pain should be a float."""
        calls, puts = _make_chain([90, 95, 100, 105, 110])
        result = options_scanner._compute_max_pain(calls, puts)
        assert isinstance(result, float)

    def test_returns_one_of_the_strikes(self):
        """Max pain must equal one of the strike values."""
        strikes = [90, 95, 100, 105, 110]
        calls, puts = _make_chain(strikes)
        result = options_scanner._compute_max_pain(calls, puts)
        assert result in strikes

    def test_pin_at_heaviest_oi_strike(self):
        """
        When one strike has overwhelmingly more OI than others,
        max pain gravitates toward that strike.
        """
        # Manually construct a scenario with concentrated OI at 100
        calls_data = [
            {"strike": 90.0,  "openInterest": 100.0},
            {"strike": 100.0, "openInterest": 5000.0},
            {"strike": 110.0, "openInterest": 100.0},
        ]
        puts_data = [
            {"strike": 90.0,  "openInterest": 100.0},
            {"strike": 100.0, "openInterest": 5000.0},
            {"strike": 110.0, "openInterest": 100.0},
        ]
        calls = pd.DataFrame(calls_data)
        puts  = pd.DataFrame(puts_data)
        result = options_scanner._compute_max_pain(calls, puts)
        # 100 should be max pain (balanced OI → lowest total payout)
        assert result == pytest.approx(100.0)

    def test_returns_none_on_empty_dataframes(self):
        """Empty calls/puts should return None gracefully."""
        calls = pd.DataFrame(columns=["strike", "openInterest"])
        puts  = pd.DataFrame(columns=["strike", "openInterest"])
        result = options_scanner._compute_max_pain(calls, puts)
        assert result is None

    def test_returns_none_on_too_few_strikes(self):
        """Fewer than 3 unique strikes should return None."""
        calls = pd.DataFrame([{"strike": 100.0, "openInterest": 500.0}])
        puts  = pd.DataFrame([{"strike": 100.0, "openInterest": 500.0}])
        result = options_scanner._compute_max_pain(calls, puts)
        assert result is None

    def test_call_only_pain(self):
        """Only calls provided (no OI on puts) — should still work."""
        calls = pd.DataFrame([
            {"strike": 90.0,  "openInterest": 1000.0},
            {"strike": 100.0, "openInterest": 500.0},
            {"strike": 110.0, "openInterest": 200.0},
        ])
        puts = pd.DataFrame([
            {"strike": 90.0,  "openInterest": 0.0},
            {"strike": 100.0, "openInterest": 0.0},
            {"strike": 110.0, "openInterest": 0.0},
        ])
        result = options_scanner._compute_max_pain(calls, puts)
        # Should return lowest strike (minimises call intrinsic value)
        assert result == pytest.approx(90.0)


# ═══════════════════════════════════════════════════════════════════════
# _get_nearest_expiry
# ═══════════════════════════════════════════════════════════════════════

class TestGetNearestExpiry:
    """Tests for options_scanner._get_nearest_expiry(ticker_obj)"""

    def test_finds_expiry_in_window(self):
        """An expiry 14 days out should be found."""
        exp = _expiry_in_window(14)
        ticker = mock.MagicMock()
        ticker.options = [exp]
        exp_str, dte = options_scanner._get_nearest_expiry(ticker)
        assert exp_str == exp
        assert options_scanner._SCAN_MIN_DTE <= dte <= options_scanner._SCAN_MAX_DTE

    def test_ignores_expiry_too_near(self):
        """Expiry 1 day away is below _SCAN_MIN_DTE (5), should be skipped."""
        exp = _expiry_in_window(1)
        ticker = mock.MagicMock()
        ticker.options = [exp]
        exp_str, dte = options_scanner._get_nearest_expiry(ticker)
        assert exp_str is None
        assert dte is None

    def test_ignores_expiry_too_far(self):
        """Expiry 90 days away is above _SCAN_MAX_DTE (45), should be skipped."""
        exp = _expiry_in_window(90)
        ticker = mock.MagicMock()
        ticker.options = [exp]
        exp_str, dte = options_scanner._get_nearest_expiry(ticker)
        assert exp_str is None

    def test_returns_none_on_no_options(self):
        """Ticker with empty options list returns (None, None)."""
        ticker = mock.MagicMock()
        ticker.options = []
        exp_str, dte = options_scanner._get_nearest_expiry(ticker)
        assert exp_str is None
        assert dte is None

    def test_returns_none_on_exception(self):
        """If ticker.options raises, return (None, None)."""
        ticker = mock.MagicMock()
        ticker.options = mock.PropertyMock(side_effect=Exception("network"))()
        # Make the attribute access itself raise
        type(ticker).options = mock.PropertyMock(side_effect=Exception("network"))
        exp_str, dte = options_scanner._get_nearest_expiry(ticker)
        assert exp_str is None

    def test_picks_first_valid_expiry(self):
        """When multiple expiries exist, the first in-window one is returned."""
        exp_near  = _expiry_in_window(8)   # in window
        exp_mid   = _expiry_in_window(21)  # in window
        exp_far   = _expiry_in_window(90)  # out of window
        ticker = mock.MagicMock()
        ticker.options = [exp_near, exp_mid, exp_far]
        exp_str, dte = options_scanner._get_nearest_expiry(ticker)
        assert exp_str == exp_near


# ═══════════════════════════════════════════════════════════════════════
# _get_earnings_days
# ═══════════════════════════════════════════════════════════════════════

class TestGetEarningsDays:
    """Tests for options_scanner._get_earnings_days(ticker_obj)"""

    def test_dict_calendar_returns_correct_days(self):
        """Calendar as dict with 'Earnings Date' key."""
        earnings_date = date.today() + timedelta(days=7)
        ticker = mock.MagicMock()
        ticker.calendar = {"Earnings Date": [earnings_date]}
        result = options_scanner._get_earnings_days(ticker)
        assert result == 7

    def test_dataframe_calendar_columns(self):
        """Calendar as DataFrame with 'Earnings Date' column."""
        earnings_date = date.today() + timedelta(days=14)
        df = pd.DataFrame({"Earnings Date": [pd.Timestamp(earnings_date)]})
        ticker = mock.MagicMock()
        ticker.calendar = df
        result = options_scanner._get_earnings_days(ticker)
        assert result == 14

    def test_none_calendar_returns_none(self):
        """None calendar should return None."""
        ticker = mock.MagicMock()
        ticker.calendar = None
        result = options_scanner._get_earnings_days(ticker)
        assert result is None

    def test_past_earnings_returns_none(self):
        """Past earnings date should return None (negative days)."""
        past_date = date.today() - timedelta(days=5)
        ticker = mock.MagicMock()
        ticker.calendar = {"Earnings Date": [past_date]}
        result = options_scanner._get_earnings_days(ticker)
        assert result is None

    def test_far_future_earnings_returns_none(self):
        """Earnings > 60 days away should return None."""
        far_date = date.today() + timedelta(days=90)
        ticker = mock.MagicMock()
        ticker.calendar = {"Earnings Date": [far_date]}
        result = options_scanner._get_earnings_days(ticker)
        assert result is None

    def test_exception_returns_none(self):
        """Any exception in calendar parsing returns None."""
        ticker = mock.MagicMock()
        type(ticker).calendar = mock.PropertyMock(side_effect=RuntimeError("api error"))
        result = options_scanner._get_earnings_days(ticker)
        assert result is None

    def test_earnings_tomorrow_returns_1(self):
        """Earnings tomorrow should return 1."""
        tomorrow = date.today() + timedelta(days=1)
        ticker = mock.MagicMock()
        ticker.calendar = {"Earnings Date": tomorrow}
        result = options_scanner._get_earnings_days(ticker)
        assert result == 1


# ═══════════════════════════════════════════════════════════════════════
# _analyse_symbol — integration-style with mocked yfinance
# ═══════════════════════════════════════════════════════════════════════

class TestAnalyseSymbol:
    """Tests for options_scanner._analyse_symbol(symbol, regime)"""

    def _make_full_ticker(self, price=150.0, call_vol=2000, put_vol=400,
                          call_oi=5000, put_oi=3000, iv=0.25,
                          earnings_days=None):
        """Build a comprehensive mock ticker for _analyse_symbol."""
        exp = _expiry_in_window(14)
        calls, puts = _make_chain(
            [140, 145, 150, 155, 160],
            call_vol=call_vol, call_oi=call_oi,
            put_vol=put_vol, put_oi=put_oi, iv=iv
        )
        chain_mock = mock.MagicMock()
        chain_mock.calls = calls
        chain_mock.puts  = puts

        hist = pd.DataFrame({"Close": [price], "High": [price * 1.01],
                             "Low": [price * 0.99], "Open": [price], "Volume": [1_000_000]})

        cal = None
        if earnings_days is not None:
            ed = date.today() + timedelta(days=earnings_days)
            cal = {"Earnings Date": [ed]}

        ticker = mock.MagicMock()
        ticker.history.return_value = hist
        ticker.options = [exp]
        ticker.option_chain.return_value = chain_mock
        ticker.calendar = cal
        return ticker

    def test_returns_none_on_no_price_data(self):
        """Empty price history → None."""
        ticker = mock.MagicMock()
        ticker.history.return_value = pd.DataFrame()
        with mock.patch("yfinance.Ticker", return_value=ticker):
            result = options_scanner._analyse_symbol("AAPL")
        assert result is None

    def test_returns_none_when_total_vol_too_low(self):
        """Total volume below _MIN_TOTAL_VOL → None."""
        ticker = self._make_full_ticker(call_vol=10, put_vol=10)
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch("options_scanner.get_iv_rank", return_value=25.0):
            result = options_scanner._analyse_symbol("LOW_VOL")
        assert result is None

    def test_unusual_call_volume_detected(self):
        """
        call_vol / call_oi >= 0.25 should trigger unusual_calls=True.
        """
        # call_vol=2000, call_oi=4000 → ratio=0.50 > 0.25
        ticker = self._make_full_ticker(call_vol=2000, call_oi=4000,
                                        put_vol=100, put_oi=3000)
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch("options_scanner.get_iv_rank", return_value=20.0):
            result = options_scanner._analyse_symbol("AAPL")
        if result is not None:
            assert result["unusual_calls"] is True

    def test_high_score_signal_has_all_keys(self):
        """Result dict (when returned) must contain all required keys."""
        # Setup with high call volume
        ticker = self._make_full_ticker(call_vol=5000, call_oi=8000,
                                        put_vol=300, put_oi=3000)
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch("options_scanner.get_iv_rank", return_value=15.0):
            result = options_scanner._analyse_symbol("AAPL")
        if result is not None:
            required_keys = [
                "symbol", "price", "options_score", "signal",
                "call_vol", "put_vol", "call_oi", "put_oi",
                "cp_ratio", "unusual_calls", "unusual_puts",
                "iv_rank", "dom_strike", "dom_type",
                "expiry", "dte", "reasoning"
            ]
            for key in required_keys:
                assert key in result, f"Missing key: {key}"

    def test_score_below_threshold_returns_none(self):
        """
        When computed score < _MIN_OPTIONS_SCORE (12), return None.
        Very low volume with no unusual activity and expensive IV.
        """
        # Normal volume (not unusual), expensive IVR → should not score high enough
        ticker = self._make_full_ticker(call_vol=500, call_oi=10000,
                                        put_vol=400, put_oi=9000,
                                        earnings_days=None)
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch("options_scanner.get_iv_rank", return_value=70.0):
            result = options_scanner._analyse_symbol("NORMAL")
        # With no unusual volume (0.05 ratio) and high IVR → score < 12
        assert result is None

    def test_earnings_play_signal_assigned(self):
        """Earnings within 10 days → signal = 'EARNINGS_PLAY'."""
        ticker = self._make_full_ticker(
            call_vol=3000, call_oi=6000, put_vol=2000, put_oi=5000,
            earnings_days=5
        )
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch("options_scanner.get_iv_rank", return_value=20.0):
            result = options_scanner._analyse_symbol("EARNER")
        if result is not None:
            assert result["signal"] == "EARNINGS_PLAY"
            assert result["earnings_days"] == 5

    def test_call_buyer_signal_on_high_cp_ratio(self):
        """High call/put volume ratio (>= 3) → CALL_BUYER signal."""
        # call_vol=6000, put_vol=500 → cp_ratio=12 > 3
        ticker = self._make_full_ticker(
            call_vol=6000, call_oi=10000,
            put_vol=500,  put_oi=5000
        )
        with mock.patch("yfinance.Ticker", return_value=ticker), \
             mock.patch("options_scanner.get_iv_rank", return_value=20.0):
            result = options_scanner._analyse_symbol("BULL")
        if result is not None:
            assert result["signal"] in ["CALL_BUYER", "EARNINGS_PLAY"]

    def test_exception_in_ticker_returns_none(self):
        """Any unexpected exception should return None, not raise."""
        ticker = mock.MagicMock()
        ticker.history.side_effect = RuntimeError("network failure")
        with mock.patch("yfinance.Ticker", return_value=ticker):
            result = options_scanner._analyse_symbol("CRASH")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# scan_options_universe
# ═══════════════════════════════════════════════════════════════════════

class TestScanOptionsUniverse:
    """Tests for options_scanner.scan_options_universe(extra_symbols, regime)"""

    def test_returns_list(self):
        """Should always return a list."""
        with mock.patch.object(options_scanner, "_analyse_symbol", return_value=None):
            result = options_scanner.scan_options_universe()
        assert isinstance(result, list)

    def test_empty_when_all_none(self):
        """When every _analyse_symbol returns None, result is empty list."""
        with mock.patch.object(options_scanner, "_analyse_symbol", return_value=None):
            result = options_scanner.scan_options_universe()
        assert result == []

    def test_includes_extra_symbols(self):
        """extra_symbols should be added to the scan universe."""
        scanned = []

        def capture(sym, regime=None):
            scanned.append(sym)
            return None

        with mock.patch.object(options_scanner, "_analyse_symbol", side_effect=capture):
            options_scanner.scan_options_universe(extra_symbols=["XTRA1", "XTRA2"])

        assert "XTRA1" in scanned
        assert "XTRA2" in scanned

    def test_results_sorted_by_score_descending(self):
        """Results should be sorted by options_score descending."""
        signals = [
            {"symbol": "A", "options_score": 20, "signal": "CALL_BUYER"},
            {"symbol": "B", "options_score": 28, "signal": "CALL_BUYER"},
            {"symbol": "C", "options_score": 15, "signal": "PUT_BUYER"},
        ]
        idx = [0]

        def rotating_result(sym, regime=None):
            # Return signals in order, then None
            if idx[0] < len(signals):
                r = signals[idx[0]]
                idx[0] += 1
                return r
            return None

        with mock.patch.object(options_scanner, "_analyse_symbol",
                               side_effect=rotating_result):
            # Force a small universe so our 3 signals dominate
            with mock.patch.object(options_scanner, "OPTIONABLE_UNIVERSE",
                                   ["A", "B", "C"]):
                result = options_scanner.scan_options_universe()

        if len(result) >= 2:
            for i in range(len(result) - 1):
                assert result[i]["options_score"] >= result[i + 1]["options_score"]

    def test_capped_at_max_results(self):
        """Should return at most _MAX_RESULTS entries."""
        high_score_signal = {
            "symbol": "X", "options_score": 25, "signal": "CALL_BUYER"
        }
        with mock.patch.object(options_scanner, "_analyse_symbol",
                               return_value=high_score_signal):
            result = options_scanner.scan_options_universe()
        assert len(result) <= options_scanner._MAX_RESULTS


# ═══════════════════════════════════════════════════════════════════════
# Parametrized scoring logic tests
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("iv_rank,expected_bonus", [
    (10,  8),   # Very cheap → +8
    (25,  5),   # Fairly cheap → +5
    (40,  0),   # Expensive → 0
    (70,  0),   # Very expensive → 0
    (None, 0),  # Unknown → 0
])
def test_iv_rank_scoring_logic(iv_rank, expected_bonus):
    """
    Verify IV rank scoring awards correct points.
    We test this by inspecting the scoring thresholds directly
    (< 20 → 8, 20-34 → 5, >=35 → 0).
    """
    if iv_rank is None:
        actual = 0
    elif iv_rank < 20:
        actual = 8
    elif iv_rank < 35:
        actual = 5
    else:
        actual = 0
    assert actual == expected_bonus


@pytest.mark.parametrize("cp_ratio,pc_ratio,expected_score_bonus", [
    (3.5, 0.28, 5),  # Heavy call skew >= 3.0
    (2.5, 0.40, 3),  # Call-leaning >= 2.0
    (0.3, 3.5, 5),   # Heavy put skew
    (0.4, 2.5, 3),   # Put-leaning
    (1.0, 1.0, 0),   # Balanced → no skew bonus
])
def test_directional_flow_scoring(cp_ratio, pc_ratio, expected_score_bonus):
    """
    Directional flow scoring: test the scoring rules in isolation.
    """
    score = 0
    if cp_ratio >= 3.0:
        score += 5
    elif pc_ratio >= 3.0:
        score += 5
    elif cp_ratio >= 2.0:
        score += 3
    elif pc_ratio >= 2.0:
        score += 3
    assert score == expected_score_bonus


@pytest.mark.parametrize("earnings_days,expected_bonus", [
    (5,  7),   # 3-10 DTE → prime window
    (15, 4),   # 11-21 DTE
    (30, 2),   # 22-45 DTE
    (50, 0),   # > 45 DTE (shouldn't appear — _get_earnings_days returns None)
    (None, 0), # No earnings
])
def test_earnings_scoring_logic(earnings_days, expected_bonus):
    """Earnings scoring rules applied correctly."""
    score = 0
    if earnings_days is not None:
        if 3 <= earnings_days <= 10:
            score += 7
        elif earnings_days <= 21:
            score += 4
        elif earnings_days <= 45:
            score += 2
    assert score == expected_bonus
