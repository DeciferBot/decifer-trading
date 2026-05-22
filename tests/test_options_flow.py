"""
Tests for options flow detection logic — Tests 12-18.

Covers volume expansion thresholds, PREV_VOLUME_FLOOR, OI ratio formulas,
and directional signal conditions (CALL_BUYER / PUT_BUYER).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from options_provider import (
    MIN_DAY_OVER_DAY_RATIO,
    MIN_OPEN_INTEREST,
    MIN_SIDE_TRADE_COUNT,
    MIN_SIDE_VOLUME,
    PREV_VOLUME_FLOOR,
    UNUSUAL_VOL_OI_RATIO,
    OptionsFlowData,
)


def _make_flow_data(**kwargs) -> OptionsFlowData:
    """Helper: build OptionsFlowData with sensible defaults."""
    defaults = dict(
        symbol="TEST",
        expiry="2026-06-20",
        dte=28,
        call_volume=0.0,
        call_volume_source="alpaca_rest_dailyBar",
        call_trade_count=0.0,
        call_trade_count_source="alpaca_rest_dailyBar",
        call_prev_volume=0.0,
        call_prev_volume_source="alpaca_rest_prevDailyBar",
        call_open_interest=None,
        call_open_interest_source="unavailable",
        put_volume=0.0,
        put_volume_source="alpaca_rest_dailyBar",
        put_trade_count=0.0,
        put_trade_count_source="alpaca_rest_dailyBar",
        put_prev_volume=0.0,
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


# ── Test 12: OI ratio passes ──────────────────────────────────────────

def test_oi_ratio_passes():
    """OI ratio path: volume=500, OI=1000 → ratio=0.5 ≥ 0.25 threshold."""
    v, oi = 500.0, 1000.0
    ratio = v / oi
    assert oi > 0
    assert ratio >= UNUSUAL_VOL_OI_RATIO, (
        f"Expected ratio {ratio:.2f} >= {UNUSUAL_VOL_OI_RATIO}"
    )


# ── Test 13: OI ratio fails ───────────────────────────────────────────

def test_oi_ratio_fails():
    """OI ratio path: volume=100, OI=1000 → ratio=0.1 < 0.25 threshold."""
    v, oi = 100.0, 1000.0
    ratio = v / oi
    assert not (oi > 0 and ratio >= UNUSUAL_VOL_OI_RATIO), (
        f"Expected ratio {ratio:.2f} < {UNUSUAL_VOL_OI_RATIO}"
    )


# ── Test 14: Volume expansion passes ─────────────────────────────────

def test_volume_expansion_passes():
    """Volume expansion: today=500, prev=100 → ratio=5.0 ≥ 1.75 threshold."""
    today_vol, prev_vol = 500.0, 100.0
    ratio = today_vol / max(prev_vol, PREV_VOLUME_FLOOR)
    assert ratio >= MIN_DAY_OVER_DAY_RATIO, (
        f"Expected ratio {ratio:.2f} >= {MIN_DAY_OVER_DAY_RATIO}"
    )
    # Also verify the MIN_SIDE_VOLUME gate passes
    assert today_vol >= MIN_SIDE_VOLUME


# ── Test 15: Volume expansion fails ──────────────────────────────────

def test_volume_expansion_fails():
    """Volume expansion: today=200, prev=150 → ratio=1.33 < 1.75 threshold."""
    today_vol, prev_vol = 200.0, 150.0
    ratio = today_vol / max(prev_vol, PREV_VOLUME_FLOOR)
    assert ratio < MIN_DAY_OVER_DAY_RATIO, (
        f"Expected ratio {ratio:.2f} < {MIN_DAY_OVER_DAY_RATIO}"
    )


# ── Test 16: PREV_VOLUME_FLOOR prevents false signal on tiny prev ──────

def test_prev_volume_floor():
    """PREV_VOLUME_FLOOR ensures a tiny prev day volume doesn't create a misleading ratio."""
    # Low-volume case: today_vol=100 fails MIN_SIDE_VOLUME gate first
    today_vol, tiny_prev = 100.0, 2.0
    assert today_vol < MIN_SIDE_VOLUME, (
        f"MIN_SIDE_VOLUME={MIN_SIDE_VOLUME} should block low total volume before ratio check"
    )

    # High-volume case with tiny prev: floor constrains the ratio denominator
    today_vol2, tiny_prev2 = 260.0, 5.0
    ratio_raw = today_vol2 / tiny_prev2                          # 52.0 — misleadingly high
    ratio_floor = today_vol2 / max(tiny_prev2, PREV_VOLUME_FLOOR)  # 260/50 = 5.2

    # Floor makes ratio sane (not 52x)
    assert ratio_floor < ratio_raw, "Floor must constrain the ratio"
    assert ratio_floor >= MIN_DAY_OVER_DAY_RATIO, (
        "With floor applied, legitimate expansion should still pass"
    )
    assert today_vol2 >= MIN_SIDE_VOLUME, "Volume floor passes MIN_SIDE_VOLUME gate"


# ── Test 17: CALL_BUYER requires unusual_calls + call/put skew ─────────

def test_call_buyer_requires_unusual_calls_and_skew():
    """CALL_BUYER signal requires: unusual calls, sufficient call volume, and C/P skew."""
    call_vol, put_vol = 600.0, 200.0    # C/P = 3.0 — skew passes
    call_tc, put_tc = 25.0, 15.0
    call_prev, put_prev = 200.0, 180.0

    unusual_calls = (
        call_vol >= MIN_SIDE_VOLUME
        and call_tc >= MIN_SIDE_TRADE_COUNT
        and call_vol / max(call_prev, PREV_VOLUME_FLOOR) >= MIN_DAY_OVER_DAY_RATIO
    )
    unusual_puts = (
        put_vol >= MIN_SIDE_VOLUME
        and put_tc >= MIN_SIDE_TRADE_COUNT
        and put_vol / max(put_prev, PREV_VOLUME_FLOOR) >= MIN_DAY_OVER_DAY_RATIO
    )
    skew_ok = call_vol >= 1.5 * max(put_vol, 1)

    assert unusual_calls, "calls should be unusual (vol=600 ≥ 250, tc=25 ≥ 20, ratio=3.0 ≥ 1.75)"
    assert not unusual_puts, "puts should not be unusual (put_vol=200 < MIN_SIDE_VOLUME=250)"
    assert skew_ok, "call skew should pass (600 ≥ 1.5 × 200)"
    # CALL_BUYER requires both gates
    assert unusual_calls and skew_ok


# ── Test 18: PUT_BUYER requires unusual_puts + put/call skew ──────────

def test_put_buyer_requires_unusual_puts_and_skew():
    """PUT_BUYER signal requires: unusual puts, sufficient put volume, and P/C skew."""
    call_vol, put_vol = 100.0, 700.0    # P/C skew
    call_tc, put_tc = 10.0, 30.0
    call_prev, put_prev = 90.0, 200.0

    unusual_calls = (
        call_vol >= MIN_SIDE_VOLUME
        and call_tc >= MIN_SIDE_TRADE_COUNT
        and call_vol / max(call_prev, PREV_VOLUME_FLOOR) >= MIN_DAY_OVER_DAY_RATIO
    )
    unusual_puts = (
        put_vol >= MIN_SIDE_VOLUME
        and put_tc >= MIN_SIDE_TRADE_COUNT
        and put_vol / max(put_prev, PREV_VOLUME_FLOOR) >= MIN_DAY_OVER_DAY_RATIO
    )
    skew_ok = put_vol >= 1.5 * max(call_vol, 1)

    assert not unusual_calls, "calls not unusual (call_vol=100 < MIN_SIDE_VOLUME=250)"
    assert unusual_puts, "puts should be unusual (vol=700 ≥ 250, tc=30 ≥ 20, ratio=3.5 ≥ 1.75)"
    assert skew_ok, "put skew should pass (700 ≥ 1.5 × 100)"
    assert unusual_puts and skew_ok
