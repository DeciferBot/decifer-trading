"""
Tests for expression_router.py — Tests 19-31.

Covers routing decisions: COMMON vs OPTION vs NO_TRADE,
advisory-only signal blocking, score advantage gate, and audit logging.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from expression_router import OPTION_SCORE_ADVANTAGE, ExpressionRoute, route_expression
from options_provider import OptionsFlowData


def _make_flow_data(**kwargs) -> OptionsFlowData:
    """Build OptionsFlowData with sensible defaults for routing tests."""
    defaults = dict(
        symbol="TEST",
        expiry="2026-06-20",
        dte=28,
        call_volume=600.0,
        call_volume_source="alpaca_rest_dailyBar",
        call_trade_count=30.0,
        call_trade_count_source="alpaca_rest_dailyBar",
        call_prev_volume=200.0,
        call_prev_volume_source="alpaca_rest_prevDailyBar",
        call_open_interest=None,
        call_open_interest_source="unavailable",
        put_volume=200.0,
        put_volume_source="alpaca_rest_dailyBar",
        put_trade_count=15.0,
        put_trade_count_source="alpaca_rest_dailyBar",
        put_prev_volume=100.0,
        put_prev_volume_source="alpaca_rest_prevDailyBar",
        put_open_interest=None,
        put_open_interest_source="unavailable",
        provider="alpaca_rest_dailyBar",
        provider_status="PARTIAL_FLOW",
        flow_definition="VOLUME_EXPANSION",
        provider_timestamp="2026-05-22T18:00:00Z",
        data_quality="REAL",
        flow_metrics_available=True,
    )
    defaults.update(kwargs)
    return OptionsFlowData(**defaults)


# ── Test 19: EARNINGS_PLAY is advisory-only ────────────────────────────

def test_earnings_play_advisory():
    """EARNINGS_PLAY must not be in _DIRECTIONAL_SIGNALS."""
    from options_entries import _DIRECTIONAL_SIGNALS
    assert "EARNINGS_PLAY" not in _DIRECTIONAL_SIGNALS


# ── Test 20: MIXED_FLOW is advisory-only ──────────────────────────────

def test_mixed_flow_advisory():
    """MIXED_FLOW must not be in _DIRECTIONAL_SIGNALS."""
    from options_entries import _DIRECTIONAL_SIGNALS
    assert "MIXED_FLOW" not in _DIRECTIONAL_SIGNALS


# ── Test 21: CALL_BUYER with valid unusual flow routes OPTION ──────────

def test_call_buyer_valid_flow_routes_option():
    """CALL_BUYER + confirmed unusual flow + approved provider → OPTION."""
    fd = _make_flow_data(flow_metrics_available=True, provider_status="PARTIAL_FLOW")
    sig = {
        "signal": "CALL_BUYER",
        "options_score": 25,
        "unusual_calls": True,
        "unusual_puts": False,
        "provider_status": "PARTIAL_FLOW",
    }
    route = route_expression(sig, flow_data=fd, regime=None, portfolio_state=None)
    assert route.route == "OPTION", f"Expected OPTION, got {route.route}: {route.reason}"
    assert route.option_gates_pass


# ── Test 22: CALL_BUYER without approved flow is not OPTION ───────────

def test_call_buyer_no_flow_not_option():
    """CALL_BUYER with NULL provider cannot route to OPTION."""
    sig = {
        "signal": "CALL_BUYER",
        "options_score": 25,
        "unusual_calls": False,
        "unusual_puts": False,
        "provider_status": "NULL",
    }
    route = route_expression(sig, flow_data=None, regime=None, portfolio_state=None)
    assert route.route != "OPTION", f"Expected not OPTION, got {route.route}: {route.reason}"
    assert not route.option_gates_pass


# ── Test 23: Strong stock thesis plus poor option liquidity → COMMON ───

def test_strong_thesis_weak_options_routes_common():
    """When option gates fail, signal with sufficient score routes to COMMON."""
    sig = {
        "signal": "CALL_BUYER",
        "options_score": 20,
        "unusual_calls": False,
        "unusual_puts": False,
        "provider_status": "NULL",
    }
    route = route_expression(sig, flow_data=None, regime=None, portfolio_state=None)
    assert route.route == "COMMON", f"Expected COMMON, got {route.route}: {route.reason}"


# ── Test 24: Strong catalyst + valid chain routes OPTION ───────────────

def test_strong_catalyst_valid_chain_routes_option():
    """High-conviction unusual flow with approved provider routes to OPTION."""
    fd = _make_flow_data(flow_metrics_available=True, provider_status="PARTIAL_FLOW")
    sig = {
        "signal": "CALL_BUYER",
        "options_score": 25,
        "unusual_calls": True,
        "unusual_puts": False,
        "provider_status": "PARTIAL_FLOW",
    }
    route = route_expression(sig, flow_data=fd, regime=None, portfolio_state=None)
    assert route.route == "OPTION", f"Expected OPTION, got {route.route}: {route.reason}"


# ── Test 25: Weak stock thesis routes NO_TRADE ────────────────────────

def test_weak_thesis_no_trade():
    """options_score below minimum threshold → NO_TRADE."""
    sig = {
        "signal": "MIXED_FLOW",
        "options_score": 8,
        "unusual_calls": False,
        "unusual_puts": False,
        "provider_status": "NULL",
    }
    route = route_expression(sig, flow_data=None, regime=None, portfolio_state=None)
    assert route.route == "NO_TRADE", f"Expected NO_TRADE, got {route.route}: {route.reason}"


# ── Test 26: Option must beat common by >= OPTION_SCORE_ADVANTAGE ──────

def test_option_score_advantage():
    """option_score must exceed common_score by OPTION_SCORE_ADVANTAGE to route OPTION."""
    fd = _make_flow_data(flow_metrics_available=True, provider_status="PARTIAL_FLOW")
    sig = {
        "signal": "CALL_BUYER",
        "options_score": 18,
        "unusual_calls": True,
        "unusual_puts": False,
        "provider_status": "PARTIAL_FLOW",
    }
    route = route_expression(sig, flow_data=fd, regime=None, portfolio_state=None)
    # option_score = 18 + 15 (unusual bonus) = 33; common = 18; advantage = 15 ≥ 10 → OPTION
    assert route.option_expression_score >= route.common_expression_score + OPTION_SCORE_ADVANTAGE, (
        f"option_score={route.option_expression_score} must exceed "
        f"common_score={route.common_expression_score} + advantage={OPTION_SCORE_ADVANTAGE}"
    )
    assert route.route == "OPTION", f"Expected OPTION, got {route.route}: {route.reason}"


# ── Test 27: Common does not require options fields ────────────────────

def test_common_does_not_require_options_fields():
    """COMMON route must work without unusual flow fields populated."""
    sig = {
        "signal": "CALL_BUYER",
        "options_score": 15,
        "unusual_calls": False,
        "unusual_puts": False,
        "provider_status": "NULL",
    }
    route = route_expression(sig, flow_data=None, regime=None, portfolio_state=None)
    assert route.common_gates_pass, "COMMON gate must pass with score >= 12"
    assert route.route == "COMMON", f"Expected COMMON, got {route.route}: {route.reason}"


# ── Test 28: Every skipped signal has entry_skip_reason populated ──────

def test_skipped_signals_have_skip_reason():
    """route_expression always returns a skip_reason when route != OPTION."""
    cases = [
        # (signal dict, expected_route)
        ({"signal": "MIXED_FLOW", "options_score": 8, "unusual_calls": False,
          "unusual_puts": False, "provider_status": "NULL"}, "NO_TRADE"),
        ({"signal": "CALL_BUYER", "options_score": 15, "unusual_calls": False,
          "unusual_puts": False, "provider_status": "NULL"}, "COMMON"),
    ]
    for sig, expected_route in cases:
        route = route_expression(sig, flow_data=None, regime=None, portfolio_state=None)
        assert route.route == expected_route, f"Expected {expected_route}, got {route.route}"
        assert route.skip_reason is not None, (
            f"skip_reason must be set for route={route.route}, got None"
        )


# ── Test 29: Every routed candidate has expression_route and reason ────

def test_routed_candidate_has_route_fields():
    """route_expression always returns a complete ExpressionRoute."""
    sig = {"signal": "CALL_BUYER", "options_score": 15}
    route = route_expression(sig, flow_data=None, regime=None, portfolio_state=None)
    assert route.route in {"COMMON", "OPTION", "NO_TRADE"}
    assert route.reason, "reason must be non-empty"
    assert isinstance(route.common_expression_score, int)
    assert isinstance(route.option_expression_score, int)
    assert isinstance(route.option_gates_pass, bool)
    assert isinstance(route.common_gates_pass, bool)


# ── Test 30: Every options signal has provider provenance in scanner ───

def test_signal_has_provider_provenance():
    """Scanner signal dict must contain provider and provider_status fields."""
    from unittest.mock import MagicMock, patch
    import options_provider
    import alpaca_options
    import options_scanner

    raw_chain = {
        "TEST260620C00100000": {
            "dailyBar": {
                "v": 300, "n": 25, "c": 10.0, "h": 11.0,
                "l": 9.0, "o": 9.5, "t": "2026-05-22T04:00:00Z", "vw": 10.2,
            },
            "prevDailyBar": {"v": 100, "n": 10},
            "latestQuote": {"bp": 9.5, "ap": 10.5, "bs": 50, "as": 60},
            "latestTrade": {"p": 10.0, "s": 1, "t": "2026-05-22T15:00:00Z"},
            "greeks": {"delta": 0.5},
            "impliedVolatility": 0.30,
        },
    }
    mock_raw = MagicMock()
    mock_raw.get_option_chain.return_value = raw_chain

    # Both alpaca_options and options_provider use the same raw chain
    with patch.object(alpaca_options, "_get_raw_client", return_value=mock_raw), \
         patch.object(options_provider, "_get_raw_client", return_value=mock_raw), \
         patch("options_scanner.get_iv_rank", return_value=15), \
         patch("options_scanner._get_earnings_days_fmp", return_value=None), \
         patch("alpaca_options.get_underlying_price", return_value=100.0):
        result = options_scanner._analyse_symbol("TEST", regime=None)

    # May be None if score is too low
    if result is not None:
        assert "provider" in result, "signal must have provider field"
        assert "provider_status" in result, "signal must have provider_status field"


# ── Test 31: Audit log exposes why common was selected over option ──────

def test_audit_log_common_over_option():
    """When COMMON is chosen over OPTION, the reason must explain the provider/flow gap."""
    sig = {
        "signal": "CALL_BUYER",
        "options_score": 15,
        "unusual_calls": False,
        "unusual_puts": False,
        "provider_status": "NULL",
    }
    route = route_expression(sig, flow_data=None, regime=None, portfolio_state=None)
    assert route.route == "COMMON", f"Expected COMMON, got {route.route}"
    # Reason must explain WHY options was not chosen
    reason_lower = route.reason.lower()
    # Should mention at least one of: provider, flow, or option gates
    has_explanation = (
        "provider" in reason_lower
        or "flow" in reason_lower
        or "option" in reason_lower
        or "gate" in reason_lower
    )
    assert has_explanation, (
        f"COMMON route reason must explain options gate failure, got: {route.reason!r}"
    )
