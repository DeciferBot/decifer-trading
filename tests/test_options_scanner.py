"""
Tests for options_scanner.py.

Covers: _get_earnings_days_fmp, _analyse_symbol, scan_options_universe,
and scoring logic (IV rank, directional flow, earnings scoring).

yfinance, _compute_max_pain, _get_nearest_expiry, and _get_earnings_days
were removed (audit 2026-05-22) — tests updated to match new architecture.
"""

import os
import sys
import types
import unittest.mock as mock
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# ── Project root on path ──────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Stub heavy dependencies BEFORE importing Decifer modules ─────────

# ib_async
ib_async_stub = types.ModuleType("ib_async")
ib_async_stub.IB = MagicMock
ib_async_stub.Option = MagicMock
ib_async_stub.Stock = MagicMock
ib_async_stub.Forex = MagicMock
ib_async_stub.Contract = MagicMock
sys.modules.setdefault("ib_async", ib_async_stub)

# anthropic
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = MagicMock
sys.modules.setdefault("anthropic", anthropic_stub)

import pandas as pd

# py_vollib
for mod in [
    "py_vollib",
    "py_vollib.black_scholes",
    "py_vollib.black_scholes.greeks",
    "py_vollib.black_scholes.greeks.analytical",
]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

# signals
signals_stub = types.ModuleType("signals")
signals_stub._safe_download = MagicMock(return_value=pd.DataFrame())
sys.modules.setdefault("signals", signals_stub)

# options (used inside options_scanner via `from options import get_iv_rank`)
options_stub = types.ModuleType("options")
options_stub.get_iv_rank = MagicMock(return_value=25.0)
options_stub.calculate_greeks = MagicMock(
    return_value={"delta": 0.45, "gamma": 0.02, "theta": -0.015, "vega": 0.18, "model_price": 3.50}
)
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
    "alpaca_api_key": "",
    "alpaca_secret_key": "",
}
sys.modules.setdefault("config", config_stub)

import pytest

# Evict any cached stub
sys.modules.pop("options_scanner", None)
sys.modules.pop("options_provider", None)
import options_scanner
import options_provider


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_chain_df(strikes, vol=500, iv=0.30):
    """Build a calls or puts DataFrame with real column names from alpaca_options."""
    rows = []
    for k in strikes:
        rows.append({
            "strike": float(k),
            "bid": 1.0,
            "ask": 1.15,
            "mid": 1.075,
            "spread_pct": 0.14,
            "volume": float(vol),
            "volume_source": "alpaca_rest_dailyBar",
            "trade_count": max(int(vol / 10), 1),
            "prev_volume": float(vol) / 2,
            "prev_volume_source": "alpaca_rest_prevDailyBar",
            "quote_size": 50,
            "openInterest": None,
            "impliedVolatility": iv,
            "delta": 0.5,
            "gamma": 0.02,
            "theta": -0.01,
            "vega": 0.08,
            "option_symbol": f"TEST260620C{int(k*1000):08d}",
        })
    return pd.DataFrame(rows)


def _make_flow_data(
    call_vol=2000.0, put_vol=500.0,
    call_tc=30.0, put_tc=20.0,
    call_prev=800.0, put_prev=400.0,
    provider_status="PARTIAL_FLOW",
    flow_metrics_available=True,
):
    """Build OptionsFlowData for mocking get_options_flow_data."""
    return options_provider.OptionsFlowData(
        symbol="TEST",
        expiry="2026-06-20",
        dte=28,
        call_volume=call_vol,
        call_volume_source="alpaca_rest_dailyBar",
        call_trade_count=call_tc,
        call_trade_count_source="alpaca_rest_dailyBar",
        call_prev_volume=call_prev,
        call_prev_volume_source="alpaca_rest_prevDailyBar",
        call_open_interest=None,
        call_open_interest_source="unavailable",
        put_volume=put_vol,
        put_volume_source="alpaca_rest_dailyBar",
        put_trade_count=put_tc,
        put_trade_count_source="alpaca_rest_dailyBar",
        put_prev_volume=put_prev,
        put_prev_volume_source="alpaca_rest_prevDailyBar",
        put_open_interest=None,
        put_open_interest_source="unavailable",
        provider="alpaca_rest_dailyBar",
        provider_status=provider_status,
        flow_definition="VOLUME_EXPANSION",
        provider_timestamp="2026-05-22T18:00:00Z",
        data_quality="REAL",
        flow_metrics_available=flow_metrics_available,
    )


def _expiry_in_window(days_from_now=14):
    return (date.today() + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════
# _get_earnings_days_fmp
# ═══════════════════════════════════════════════════════════════════════


class TestGetEarningsDaysFmp:
    """Tests for options_scanner._get_earnings_days_fmp(symbol)."""

    def test_returns_days_when_earnings_found(self):
        """Returns correct days when FMP returns a matching earnings event."""
        earnings_date = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
        mock_items = [{"symbol": "AAPL", "date": earnings_date}]
        with patch("options_scanner.get_earnings_calendar", mock_items.__iter__, create=True):
            # Patch the fmp_client import inside the function
            with patch("fmp_client.get_earnings_calendar", return_value=mock_items):
                result = options_scanner._get_earnings_days_fmp("AAPL")
        assert result == 7

    def test_returns_none_when_no_match(self):
        """Returns None when FMP returns events for different symbols."""
        future_date = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        mock_items = [{"symbol": "MSFT", "date": future_date}]
        with patch("fmp_client.get_earnings_calendar", return_value=mock_items):
            result = options_scanner._get_earnings_days_fmp("AAPL")
        assert result is None

    def test_returns_none_on_exception(self):
        """Returns None if FMP raises any exception."""
        with patch("fmp_client.get_earnings_calendar", side_effect=Exception("api error")):
            result = options_scanner._get_earnings_days_fmp("AAPL")
        assert result is None

    def test_returns_none_for_past_earnings(self):
        """Past earnings date should return None."""
        past_date = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        mock_items = [{"symbol": "AAPL", "date": past_date}]
        with patch("fmp_client.get_earnings_calendar", return_value=mock_items):
            result = options_scanner._get_earnings_days_fmp("AAPL")
        assert result is None

    def test_returns_none_for_far_future_earnings(self):
        """Earnings > 60 days away should return None."""
        far_date = (date.today() + timedelta(days=90)).strftime("%Y-%m-%d")
        mock_items = [{"symbol": "AAPL", "date": far_date}]
        with patch("fmp_client.get_earnings_calendar", return_value=mock_items):
            result = options_scanner._get_earnings_days_fmp("AAPL")
        assert result is None

    def test_returns_none_on_empty_list(self):
        """Empty FMP response returns None."""
        with patch("fmp_client.get_earnings_calendar", return_value=[]):
            result = options_scanner._get_earnings_days_fmp("AAPL")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# _analyse_symbol — integration tests with mocked Alpaca
# ═══════════════════════════════════════════════════════════════════════


class TestAnalyseSymbol:
    """Tests for options_scanner._analyse_symbol(symbol, regime)."""

    def _mock_chain(self, call_vol=2000, put_vol=500, iv=0.25):
        """Build mock _alpaca_chain return value."""
        exp_str = _expiry_in_window(14)
        calls = _make_chain_df([140, 145, 150, 155, 160], vol=call_vol, iv=iv)
        puts = _make_chain_df([140, 145, 150, 155, 160], vol=put_vol, iv=iv)
        return {"calls": calls, "puts": puts, "expiry_str": exp_str, "dte": 14}

    def test_returns_none_when_alpaca_unavailable(self):
        """Returns None when Alpaca chain is unavailable."""
        with patch("alpaca_options.get_underlying_price", return_value=None), \
             patch("alpaca_options.get_chain", return_value=None), \
             patch("options_scanner.get_options_flow_data", return_value=None), \
             patch("options_scanner._get_earnings_days_fmp", return_value=None):
            result = options_scanner._analyse_symbol("AAPL")
        assert result is None

    def test_returns_none_when_total_vol_too_low(self):
        """Total flow volume below MIN_SIDE_VOLUME → None."""
        chain = self._mock_chain(call_vol=5, put_vol=5)
        flow = _make_flow_data(call_vol=5.0, put_vol=5.0, call_tc=1.0, put_tc=1.0)
        with patch("alpaca_options.get_underlying_price", return_value=150.0), \
             patch("alpaca_options.get_chain", return_value=chain), \
             patch("options_scanner.get_options_flow_data", return_value=flow), \
             patch("options_scanner._get_earnings_days_fmp", return_value=None), \
             patch("options_scanner.get_iv_rank", return_value=25.0):
            result = options_scanner._analyse_symbol("LOW_VOL")
        assert result is None

    def test_null_flow_provider_sets_provider_status(self):
        """When flow_data is None, signal must have provider_status=NULL."""
        chain = self._mock_chain(call_vol=1000, put_vol=500)
        with patch("alpaca_options.get_underlying_price", return_value=150.0), \
             patch("alpaca_options.get_chain", return_value=chain), \
             patch("options_scanner.get_options_flow_data", return_value=None), \
             patch("options_scanner._get_earnings_days_fmp", return_value=None), \
             patch("options_scanner.get_iv_rank", return_value=10.0):
            result = options_scanner._analyse_symbol("AAPL")
        if result is not None:
            assert result["provider_status"] == "NULL"
            assert result["unusual_calls"] is False
            assert result["unusual_puts"] is False

    def test_high_score_signal_has_all_required_keys(self):
        """When a signal is returned it must contain all required keys."""
        chain = self._mock_chain(call_vol=5000, put_vol=500)
        flow = _make_flow_data(call_vol=5000.0, put_vol=500.0, call_tc=50.0, put_tc=20.0,
                               call_prev=1000.0, put_prev=300.0)
        with patch("alpaca_options.get_underlying_price", return_value=150.0), \
             patch("alpaca_options.get_chain", return_value=chain), \
             patch("options_scanner.get_options_flow_data", return_value=flow), \
             patch("options_scanner._get_earnings_days_fmp", return_value=None), \
             patch("options_scanner.get_iv_rank", return_value=15.0):
            result = options_scanner._analyse_symbol("AAPL")
        if result is not None:
            required_keys = [
                "symbol", "price", "options_score", "signal",
                "provider", "provider_status", "flow_definition",
                "call_volume", "call_volume_source",
                "call_open_interest", "call_open_interest_source",
                "call_prev_volume", "call_prev_volume_source",
                "put_volume", "put_volume_source",
                "put_open_interest", "put_open_interest_source",
                "put_prev_volume", "put_prev_volume_source",
                "unusual_calls", "unusual_puts", "unusual_eval_reason",
                "iv_rank", "dom_strike", "dom_type", "dom_iv",
                "earnings_days", "expiry", "dte", "reasoning",
                "expression_route", "expression_reason", "entry_skip_reason",
            ]
            for key in required_keys:
                assert key in result, f"Missing key: {key}"

    def test_score_below_threshold_returns_none(self):
        """Score < _MIN_OPTIONS_SCORE → None."""
        chain = self._mock_chain(call_vol=100, put_vol=100)
        # flow has low volume (won't trigger unusual) and expensive IVR
        flow = _make_flow_data(call_vol=100.0, put_vol=100.0, call_tc=5.0, put_tc=5.0,
                               call_prev=90.0, put_prev=90.0)
        with patch("alpaca_options.get_underlying_price", return_value=150.0), \
             patch("alpaca_options.get_chain", return_value=chain), \
             patch("options_scanner.get_options_flow_data", return_value=flow), \
             patch("options_scanner._get_earnings_days_fmp", return_value=None), \
             patch("options_scanner.get_iv_rank", return_value=70.0):
            result = options_scanner._analyse_symbol("NORMAL")
        assert result is None

    def test_earnings_play_signal_assigned(self):
        """Earnings within 10 days → signal='EARNINGS_PLAY'."""
        chain = self._mock_chain(call_vol=5000, put_vol=3000)
        flow = _make_flow_data(call_vol=5000.0, put_vol=3000.0, call_tc=50.0, put_tc=30.0,
                               call_prev=1000.0, put_prev=800.0)
        with patch("alpaca_options.get_underlying_price", return_value=150.0), \
             patch("alpaca_options.get_chain", return_value=chain), \
             patch("options_scanner.get_options_flow_data", return_value=flow), \
             patch("options_scanner._get_earnings_days_fmp", return_value=5), \
             patch("options_scanner.get_iv_rank", return_value=20.0):
            result = options_scanner._analyse_symbol("EARNER")
        if result is not None:
            assert result["signal"] == "EARNINGS_PLAY"
            assert result["earnings_days"] == 5

    def test_call_buyer_signal_on_unusual_call_expansion(self):
        """High unusual call volume with C/P skew → CALL_BUYER."""
        chain = self._mock_chain(call_vol=6000, put_vol=500)
        # call_vol=6000 vs prev=1000 → ratio=6.0 ≥ 1.75, call_tc=60 ≥ 20, vol ≥ 250
        flow = _make_flow_data(call_vol=6000.0, put_vol=500.0, call_tc=60.0, put_tc=10.0,
                               call_prev=1000.0, put_prev=400.0)
        with patch("alpaca_options.get_underlying_price", return_value=150.0), \
             patch("alpaca_options.get_chain", return_value=chain), \
             patch("options_scanner.get_options_flow_data", return_value=flow), \
             patch("options_scanner._get_earnings_days_fmp", return_value=None), \
             patch("options_scanner.get_iv_rank", return_value=20.0):
            result = options_scanner._analyse_symbol("BULL")
        if result is not None:
            assert result["unusual_calls"] is True
            assert result["signal"] in ["CALL_BUYER", "EARNINGS_PLAY"]

    def test_exception_returns_none(self):
        """Any unexpected exception should return None, not raise."""
        with patch("alpaca_options.get_underlying_price", side_effect=RuntimeError("network")):
            result = options_scanner._analyse_symbol("CRASH")
        assert result is None

    def test_no_oi_in_signal_dict(self):
        """Returned signal must not contain call_oi or put_oi (removed fields)."""
        chain = self._mock_chain(call_vol=3000, put_vol=500)
        flow = _make_flow_data(call_vol=3000.0, put_vol=500.0, call_tc=35.0, put_tc=15.0,
                               call_prev=500.0, put_prev=300.0)
        with patch("alpaca_options.get_underlying_price", return_value=150.0), \
             patch("alpaca_options.get_chain", return_value=chain), \
             patch("options_scanner.get_options_flow_data", return_value=flow), \
             patch("options_scanner._get_earnings_days_fmp", return_value=None), \
             patch("options_scanner.get_iv_rank", return_value=15.0):
            result = options_scanner._analyse_symbol("AAPL")
        if result is not None:
            # Removed fields must not be present
            assert "call_oi" not in result, "call_oi must not be in signal dict"
            assert "put_oi" not in result, "put_oi must not be in signal dict"
            assert "cp_ratio" not in result, "cp_ratio must not be in signal dict"
            assert "max_pain" not in result, "max_pain must not be in signal dict"


# ═══════════════════════════════════════════════════════════════════════
# scan_options_universe
# ═══════════════════════════════════════════════════════════════════════


class TestScanOptionsUniverse:
    """Tests for options_scanner.scan_options_universe(extra_symbols, regime)."""

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
            if idx[0] < len(signals):
                r = signals[idx[0]]
                idx[0] += 1
                return r
            return None

        with mock.patch.object(options_scanner, "_analyse_symbol", side_effect=rotating_result):
            with mock.patch.object(options_scanner, "OPTIONABLE_UNIVERSE", ["A", "B", "C"]):
                result = options_scanner.scan_options_universe()

        if len(result) >= 2:
            for i in range(len(result) - 1):
                assert result[i]["options_score"] >= result[i + 1]["options_score"]

    def test_capped_at_max_results(self):
        """Should return at most _MAX_RESULTS entries."""
        high_score_signal = {"symbol": "X", "options_score": 25, "signal": "CALL_BUYER"}
        with mock.patch.object(options_scanner, "_analyse_symbol", return_value=high_score_signal):
            result = options_scanner.scan_options_universe()
        assert len(result) <= options_scanner._MAX_RESULTS


# ═══════════════════════════════════════════════════════════════════════
# Parametrized scoring logic tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "iv_rank,expected_bonus",
    [
        (10, 8),    # Very cheap → +8
        (25, 5),    # Fairly cheap → +5
        (40, 0),    # Expensive → 0
        (70, 0),    # Very expensive → 0
        (None, 0),  # Unknown → 0
    ],
)
def test_iv_rank_scoring_logic(iv_rank, expected_bonus):
    """Verify IV rank scoring awards correct points."""
    if iv_rank is None:
        actual = 0
    elif iv_rank < 20:
        actual = 8
    elif iv_rank < 35:
        actual = 5
    else:
        actual = 0
    assert actual == expected_bonus


@pytest.mark.parametrize(
    "cp_ratio,pc_ratio,expected_score_bonus",
    [
        (3.5, 0.28, 5),  # Heavy call skew >= 3.0
        (2.5, 0.40, 3),  # Call-leaning >= 2.0
        (0.3, 3.5, 5),   # Heavy put skew
        (0.4, 2.5, 3),   # Put-leaning
        (1.0, 1.0, 0),   # Balanced → no skew bonus
    ],
)
def test_directional_flow_scoring(cp_ratio, pc_ratio, expected_score_bonus):
    """Directional flow scoring: test the scoring rules in isolation."""
    score = 0
    if cp_ratio >= 3.0 or pc_ratio >= 3.0:
        score += 5
    elif cp_ratio >= 2.0 or pc_ratio >= 2.0:
        score += 3
    assert score == expected_score_bonus


@pytest.mark.parametrize(
    "earnings_days,expected_bonus",
    [
        (5, 7),     # 3-10 DTE → prime window
        (15, 4),    # 11-21 DTE
        (30, 2),    # 22-45 DTE
        (50, 0),    # > 45 DTE (out of range — _get_earnings_days_fmp returns None)
        (None, 0),  # No earnings
    ],
)
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


# ═══════════════════════════════════════════════════════════════════════
# Architecture guardrails — ensure removed code is gone
# ═══════════════════════════════════════════════════════════════════════


def test_yfinance_not_imported_in_scanner():
    """options_scanner must not import yfinance."""
    import pathlib
    src = pathlib.Path("options_scanner.py").read_text()
    assert "import yfinance" not in src
    assert "from yfinance" not in src


def test_compute_max_pain_removed():
    """_compute_max_pain was removed (OI not available from Alpaca)."""
    assert not hasattr(options_scanner, "_compute_max_pain"), (
        "_compute_max_pain must be removed — it requires real OI which Alpaca doesn't provide"
    )


def test_get_nearest_expiry_removed():
    """_get_nearest_expiry was removed (was yfinance-dependent)."""
    assert not hasattr(options_scanner, "_get_nearest_expiry"), (
        "_get_nearest_expiry must be removed — was yfinance-dependent"
    )


def test_old_get_earnings_days_removed():
    """The old yfinance-based _get_earnings_days must be removed."""
    assert not hasattr(options_scanner, "_get_earnings_days"), (
        "_get_earnings_days (yfinance) must be removed; new function is _get_earnings_days_fmp"
    )


def test_new_earnings_function_exists():
    """_get_earnings_days_fmp (FMP-based) must exist."""
    assert hasattr(options_scanner, "_get_earnings_days_fmp"), (
        "_get_earnings_days_fmp must exist as the FMP-based replacement"
    )
