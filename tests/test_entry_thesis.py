"""Tests for _build_entry_thesis() — GAP-003: falsifiable entry thesis."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orders_core import _build_entry_thesis

# ── Content checks ────────────────────────────────────────────────────────────


def test_scalp_thesis_contains_time_and_pnl():
    t = _build_entry_thesis("SCALP", "AAPL", "LONG", 0.75, 40, "BULL")
    assert "SCALP" in t
    assert "LONG" in t
    assert "AAPL" in t
    assert "wrong_if" in t
    # Phase 4 Change 15: scalp_max_hold_minutes raised 60→90; scalp_min_pnl_pct 0.3%→0.0%
    assert "90min" in t   # was "60min" before Phase 4
    assert "0.0%" in t    # was "0.3%" before Phase 4
    assert "BULL" in t


def test_swing_thesis_mentions_regime_shift():
    t = _build_entry_thesis("SWING", "NVDA", "LONG", 0.65, 35, "TRENDING_UP")
    assert "SWING" in t
    assert "regime" in t.lower()
    assert "TRENDING_UP" in t


def test_hold_thesis_mentions_polarity_flip():
    t = _build_entry_thesis("HOLD", "GLD", "LONG", 0.90, 45, "BULL")
    assert "HOLD" in t
    assert "BULL" in t
    assert "BEAR" in t  # must name both polarities in the falsifiable condition


def test_unknown_trade_type_falls_back():
    t = _build_entry_thesis("", "SPY", "LONG", 0.50, 30, "NEUTRAL")
    # empty trade_type defaults to SCALP behaviour
    assert "wrong_if" in t


def test_conviction_and_score_in_thesis():
    t = _build_entry_thesis("SWING", "TSLA", "SHORT", 0.72, 38, "BEAR")
    assert "0.72" in t
    assert "38" in t


def test_short_direction_stored():
    t = _build_entry_thesis("SCALP", "TSLA", "SHORT", 0.60, 32, "BEAR")
    assert "SHORT" in t


def test_entry_regime_unknown_stored():
    t = _build_entry_thesis("SWING", "MSFT", "LONG", 0.55, 28, "UNKNOWN")
    assert "UNKNOWN" in t
