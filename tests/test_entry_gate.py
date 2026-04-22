#!/usr/bin/env python3
"""
Unit tests for entry_gate.py and trade_context.py.

Covers:
  - INTRADAY gate: only hard blocks — market closed and earnings same day
  - SWING gate: only hard blocks — PANIC regime and earnings < 5 days
  - POSITION gate: only hard blocks — hostile regime and earnings < 30 days
  - classify_trade_type: hierarchy (POSITION > SWING > INTRADAY), INTRADAY fallback (no REJECT)
  - validate_entry: approved unless hard-blocked
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade_context import TradeContext
from entry_gate import (
    _validate_intraday,
    _validate_swing,
    _validate_position,
    classify_trade_type,
    validate_entry,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _intraday_ctx(**kwargs) -> TradeContext:
    """Minimal valid INTRADAY context — all hard gates pass by default."""
    defaults = dict(
        symbol="AAPL",
        direction="LONG",
        current_price=100.0,
        signal_age_minutes=5.0,
        rel_volume=1.5,
        vwap=99.0,
        vwap_distance_pct=1.0,
        hod=101.0,
        hod_distance_pct=-1.0,   # just outside no-man's land upper bound
        bid_ask_spread_pct=0.2,
        in_dead_window=False,
        earnings_days_away=10,
        regime="TRENDING_UP",
    )
    defaults.update(kwargs)
    return TradeContext(**defaults)


def _swing_ctx(**kwargs) -> TradeContext:
    """Minimal valid SWING context — earnings beat catalyst present."""
    defaults = dict(
        symbol="HIMS",
        direction="LONG",
        current_price=20.0,
        earnings_days_away=10,
        analyst_consensus="BUY",
        short_float_pct=12.0,
        catalyst_type="earnings_beat",
        catalyst_score=45.0,
        regime="TRENDING_UP",
        recent_upgrade=False,
        recent_downgrade=False,
        sector_above_50d=True,
        sector_days_since_breakout=5,
        sector_3m_vs_spy=8.0,
        sector_etf="XLK",
    )
    defaults.update(kwargs)
    return TradeContext(**defaults)


def _position_ctx(**kwargs) -> TradeContext:
    """
    Minimal valid POSITION context — passes the two-path checklist.

    Path B (growth): revenue_growth_yoy=25 > 20, not decelerating, eps_accelerating=True.
    Supporting (2/4): sector above 50d + outperforming SPY (signal 1), analyst BUY (signal 2).
    """
    defaults = dict(
        symbol="NVDA",
        direction="LONG",
        current_price=800.0,
        earnings_days_away=35,
        analyst_consensus="BUY",
        revenue_growth_yoy=25.0,
        revenue_growth_qoq=6.0,
        revenue_decelerating=False,
        eps_accelerating=True,
        sector_above_50d=True,
        sector_3m_vs_spy=9.0,
        sector_etf="XLK",
        regime="TRENDING_UP",
    )
    defaults.update(kwargs)
    return TradeContext(**defaults)


# ── INTRADAY gate tests ───────────────────────────────────────────────────────

class TestIntradayGate(unittest.TestCase):

    def test_valid_intraday_passes(self):
        ctx = _intraday_ctx()
        ok, reason, penalty = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)
        self.assertEqual(penalty, 0)

    def test_stale_signal_passes(self):
        # Stale signal no longer blocked — label only
        ctx = _intraday_ctx(signal_age_minutes=20.0)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_earnings_same_day_rejected(self):
        ctx = _intraday_ctx(earnings_days_away=0)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("earnings same day", reason)

    def test_low_volume_passes(self):
        # Volume no longer a gate — note only
        ctx = _intraday_ctx(rel_volume=0.6)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_hod_noman_land_passes(self):
        # HOD no-man's land no longer blocked
        ctx = _intraday_ctx(hod_distance_pct=-2.0)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_at_hod_passes(self):
        ctx = _intraday_ctx(hod_distance_pct=-0.5)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_below_vwap_long_passes(self):
        # VWAP position no longer a gate
        ctx = _intraday_ctx(vwap_distance_pct=-2.0)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_above_vwap_short_passes(self):
        # VWAP position no longer a gate
        ctx = _intraday_ctx(vwap_distance_pct=2.0)
        ok, _, _ = _validate_intraday("SHORT", ctx)
        self.assertTrue(ok)

    def test_close_window_hard_rejects_intraday(self):
        ctx = _intraday_ctx(time_of_day_window="CLOSE")
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("market closed", reason)

    def test_dead_window_no_penalty(self):
        # Dead window no longer adds a penalty
        ctx = _intraday_ctx(in_dead_window=True)
        ok, reason, penalty = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)
        self.assertEqual(penalty, 0)

    def test_wide_spread_passes(self):
        # Spread no longer a gate
        ctx = _intraday_ctx(bid_ask_spread_pct=1.2)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_none_fields_do_not_crash(self):
        """Gate must not raise on None values — degrades gracefully."""
        ctx = _intraday_ctx(
            signal_age_minutes=None,
            rel_volume=None,
            vwap_distance_pct=None,
            hod_distance_pct=None,
            bid_ask_spread_pct=None,
            earnings_days_away=None,
        )
        ok, reason, penalty = _validate_intraday("LONG", ctx)
        self.assertIsInstance(ok, bool)


# ── SWING gate tests ──────────────────────────────────────────────────────────

class TestSwingGate(unittest.TestCase):

    def test_valid_swing_passes(self):
        ctx = _swing_ctx()
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    def test_no_catalyst_still_passes(self):
        # Catalyst no longer required — SWING is a label, not a gate
        ctx = _swing_ctx(
            catalyst_type="none",
            catalyst_score=5.0,
            recent_upgrade=False,
            sector_days_since_breakout=15,
        )
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    def test_sell_consensus_long_passes(self):
        # Analyst consensus no longer a gate
        ctx = _swing_ctx(analyst_consensus="SELL")
        ok, _, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    def test_high_short_float_passes(self):
        # Short float no longer a gate
        ctx = _swing_ctx(short_float_pct=35.0, catalyst_type="earnings_beat")
        ok, _, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    def test_earnings_too_close_rejected(self):
        ctx = _swing_ctx(earnings_days_away=2)
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("earnings", reason)

    def test_panic_regime_rejected(self):
        ctx = _swing_ctx(regime="PANIC")
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("panic", reason.lower())

    def test_close_window_routes_to_swing(self):
        # Market closed → entry_gate returns SWING for any overnight/post-close entry
        ctx = _swing_ctx(time_of_day_window="CLOSE", earnings_days_away=10)
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=33)
        self.assertEqual(trade_type, "SWING")


# ── POSITION gate tests ───────────────────────────────────────────────────────

class TestPositionGate(unittest.TestCase):

    def test_valid_position_passes(self):
        ctx = _position_ctx()
        ok, reason = _validate_position("LONG", ctx)
        self.assertTrue(ok)

    def test_path_a_value_stock_passes(self):
        # Path A: profitable company with valuation gap — revenue can be modest
        ctx = _position_ctx(
            revenue_growth_yoy=8.0,
            fcf_yield=2.5,
            dcf_upside_pct=20.0,
            eps_accelerating=False,   # Path A doesn't require EPS acceleration
        )
        ok, reason = _validate_position("LONG", ctx)
        self.assertTrue(ok)
        self.assertIn("value/quality", reason)

    def test_sector_below_50d_passes_with_200d(self):
        # Sector below 50d is not a hard block — stock above 200d MA compensates
        ctx = _position_ctx(sector_above_50d=False, sector_3m_vs_spy=2.0,
                            stock_above_200d=True)
        ok, reason = _validate_position("LONG", ctx)
        self.assertTrue(ok)

    def test_sell_consensus_passes_with_other_signals(self):
        # Analyst consensus is supporting evidence, not a hard gate.
        # Without BUY consensus, need 2 other signals (sector + upgrade here).
        ctx = _position_ctx(analyst_consensus="STRONG_SELL", recent_upgrade=True)
        ok, reason = _validate_position("LONG", ctx)
        self.assertTrue(ok)

    def test_panic_regime_rejected(self):
        ctx = _position_ctx(regime="PANIC")
        ok, reason = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("hostile regime", reason)

    def test_bear_trending_regime_rejected(self):
        ctx = _position_ctx(regime="BEAR_TRENDING")
        ok, reason = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("hostile regime", reason)

    def test_earnings_too_close_rejected(self):
        # Binary event gate: earnings within 5 days blocks POSITION
        ctx = _position_ctx(earnings_days_away=3)
        ok, reason = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("binary event", reason)

    def test_neither_path_qualifies_downgrades(self):
        # No FCF (Path A fails), low revenue growth (Path B fails) → downgrade to SWING
        ctx = _position_ctx(
            revenue_growth_yoy=5.0,
            fcf_yield=None,
            dcf_upside_pct=None,
            eps_accelerating=False,
        )
        ok, reason = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("downgrade to SWING", reason)

    def test_insufficient_supporting_signals_downgrades(self):
        # Path B qualifies but only 1 supporting signal → downgrade
        ctx = _position_ctx(
            sector_above_50d=False,
            sector_3m_vs_spy=0.0,
            stock_above_200d=False,
            analyst_consensus="HOLD",
            recent_upgrade=False,
            insider_net_sentiment="NEUTRAL",
        )
        ok, reason = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("downgrade to SWING", reason)


# ── Hierarchy tests ───────────────────────────────────────────────────────────

class TestClassifyHierarchy(unittest.TestCase):

    def test_normal_market_hours_returns_intraday(self):
        # entry_gate returns INTRADAY as neutral default — Opus owns classification
        ctx = _intraday_ctx()
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=40)
        self.assertEqual(trade_type, "INTRADAY")

    def test_earnings_same_day_rejected(self):
        ctx = _intraday_ctx(earnings_days_away=0)
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=35)
        self.assertEqual(trade_type, "REJECT")
        self.assertIn("earnings same day", reason)

    def test_market_closed_returns_swing(self):
        # Closed market → SWING to allow overnight/post-close entries
        ctx = _intraday_ctx(time_of_day_window="CLOSE", earnings_days_away=10)
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=35)
        self.assertEqual(trade_type, "SWING")

    def test_market_closed_near_earnings_rejected(self):
        # Closed market + earnings < 5d → block
        ctx = _intraday_ctx(time_of_day_window="CLOSE", earnings_days_away=3)
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=35)
        self.assertEqual(trade_type, "REJECT")


# ── validate_entry tests ──────────────────────────────────────────────────────

class TestValidateEntry(unittest.TestCase):

    def test_approved_entry_returns_true(self):
        ctx = _intraday_ctx()
        allowed, trade_type, reason, eff_score = validate_entry("LONG", ctx, score=35)
        self.assertTrue(allowed)
        self.assertIn(trade_type, ("INTRADAY", "SWING", "POSITION"))
        self.assertEqual(eff_score, 35)

    def test_dead_window_no_penalty(self):
        # Dead window no longer penalises — effective score equals raw score
        ctx = _intraday_ctx(in_dead_window=True)
        allowed, trade_type, reason, eff_score = validate_entry("LONG", ctx, score=16)
        self.assertTrue(allowed)
        self.assertEqual(eff_score, 16)

    def test_formerly_rejected_signal_now_approved(self):
        # Stale signal + no catalyst used to REJECT — now INTRADAY fallback
        ctx = _intraday_ctx(signal_age_minutes=30.0, catalyst_type="none",
                            catalyst_score=1.0, recent_upgrade=False,
                            revenue_growth_yoy=3.0)
        allowed, trade_type, _, _ = validate_entry("LONG", ctx, score=35)
        self.assertTrue(allowed)
        self.assertIn(trade_type, ("INTRADAY", "SWING", "POSITION"))

    def test_position_context_approved(self):
        # entry_gate returns INTRADAY as neutral default — Opus owns POSITION labeling
        ctx = _position_ctx()
        allowed, trade_type, reason, eff_score = validate_entry("LONG", ctx, score=40)
        self.assertTrue(allowed)
        self.assertIn(trade_type, ("INTRADAY", "SWING", "POSITION"))


# ── Session-aware rel_vol tests (volume no longer gates) ─────────────────────

class TestSessionAwareRelVolThresholds(unittest.TestCase):
    """Volume no longer gates trades — all rel_vol values pass INTRADAY."""

    def test_after_hours_low_volume_passes(self):
        """Volume is not a gate — any rel_vol passes INTRADAY."""
        ctx = _intraday_ctx(rel_volume=0.2)
        with patch("risk.get_session", return_value="AFTER_HOURS"):
            ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_regular_session_low_volume_passes(self):
        """Volume is not a gate — low rel_vol no longer hard-fails."""
        ctx = _intraday_ctx(rel_volume=0.6)
        with patch("risk.get_session", return_value="PRIME_AM"):
            ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
