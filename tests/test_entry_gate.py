#!/usr/bin/env python3
"""
Unit tests for entry_gate.py and trade_context.py.

Covers:
  - INTRADAY gate: signal age, rel volume, HOD no-man's land, VWAP, spread, dead window penalty
  - SWING gate: catalyst requirement, earnings proximity, analyst headwind, short float
  - POSITION gate: revenue growth, sector uptrend, regime, earnings proximity
  - classify_trade_type: hierarchy (POSITION > SWING > INTRADAY > REJECT)
  - validate_entry: effective score with dead-window penalty
"""

import os
import sys
import unittest

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
    """Minimal valid POSITION context — all three primary conditions met."""
    defaults = dict(
        symbol="NVDA",
        direction="LONG",
        current_price=800.0,
        earnings_days_away=35,
        analyst_consensus="BUY",
        revenue_growth_yoy=25.0,
        revenue_growth_qoq=6.0,
        revenue_decelerating=False,
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

    def test_stale_signal_rejected(self):
        ctx = _intraday_ctx(signal_age_minutes=20.0)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_earnings_same_day_rejected(self):
        ctx = _intraday_ctx(earnings_days_away=0)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("earnings same day", reason)

    def test_very_low_volume_hard_rejected(self):
        ctx = _intraday_ctx(rel_volume=0.6)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("hard floor", reason)

    def test_moderate_low_volume_penalised(self):
        ctx = _intraday_ctx(rel_volume=0.9)
        ok, reason, penalty = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)
        self.assertGreater(penalty, 0)

    def test_hod_noman_land_rejected(self):
        # -2% is between -4% and -1% → no-man's land
        ctx = _intraday_ctx(hod_distance_pct=-2.0)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("no-man's land", reason)

    def test_at_hod_passes(self):
        # -0.5% is above the -1% upper bound → fine
        ctx = _intraday_ctx(hod_distance_pct=-0.5)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_well_below_hod_passes(self):
        # -6% is below the -4% lower bound → fine (deep pullback entry)
        ctx = _intraday_ctx(hod_distance_pct=-6.0)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)

    def test_long_below_vwap_rejected(self):
        ctx = _intraday_ctx(vwap_distance_pct=-2.0)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("wrong side", reason)

    def test_short_above_vwap_rejected(self):
        ctx = _intraday_ctx(vwap_distance_pct=2.0)
        ok, reason, _ = _validate_intraday("SHORT", ctx)
        self.assertFalse(ok)
        self.assertIn("wrong side", reason)

    def test_dead_window_adds_penalty(self):
        ctx = _intraday_ctx(in_dead_window=True)
        ok, reason, penalty = _validate_intraday("LONG", ctx)
        self.assertTrue(ok)
        self.assertEqual(penalty, 8)

    def test_wide_spread_hard_rejected(self):
        ctx = _intraday_ctx(bid_ask_spread_pct=1.2)
        ok, _, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)

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
        # With no data, gate should pass (unknown ≠ fail) with no penalty
        self.assertIsInstance(ok, bool)


# ── SWING gate tests ──────────────────────────────────────────────────────────

class TestSwingGate(unittest.TestCase):

    def test_valid_swing_earnings_catalyst_passes(self):
        ctx = _swing_ctx()
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)
        self.assertIn("earnings", reason.lower())

    def test_no_catalyst_rejected(self):
        ctx = _swing_ctx(
            catalyst_type="none",
            catalyst_score=5.0,
            recent_upgrade=False,
            sector_days_since_breakout=15,  # > 10 day limit
        )
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("no qualifying catalyst", reason.lower())

    def test_analyst_upgrade_qualifies(self):
        ctx = _swing_ctx(catalyst_type="none", catalyst_score=5.0, recent_upgrade=True)
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)
        self.assertIn("upgrade", reason.lower())

    def test_earnings_too_close_rejected(self):
        ctx = _swing_ctx(earnings_days_away=2)
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("earnings", reason)

    def test_sell_consensus_long_rejected(self):
        ctx = _swing_ctx(analyst_consensus="SELL")
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("SELL", reason)

    def test_high_short_float_no_squeeze_rejected(self):
        ctx = _swing_ctx(short_float_pct=35.0, catalyst_type="earnings_beat")
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("short float", reason)

    def test_panic_regime_rejected(self):
        ctx = _swing_ctx(regime="PANIC")
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("panic", reason.lower())

    def test_sector_rotation_qualifies(self):
        ctx = _swing_ctx(
            catalyst_type="none",
            catalyst_score=5.0,
            recent_upgrade=False,
            sector_above_50d=True,
            sector_days_since_breakout=7,  # within 10-day window
        )
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)
        self.assertIn("sector rotation", reason.lower())


# ── POSITION gate tests ───────────────────────────────────────────────────────

class TestPositionGate(unittest.TestCase):

    def test_valid_position_passes(self):
        ctx = _position_ctx()
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertTrue(ok)
        self.assertIn("rev_growth_yoy", reason)

    def test_insufficient_revenue_growth_rejected(self):
        ctx = _position_ctx(revenue_growth_yoy=8.0)
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("revenue growth", reason.lower())

    def test_missing_revenue_data_rejected(self):
        ctx = _position_ctx(revenue_growth_yoy=None)
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("unavailable", reason.lower())

    def test_revenue_decelerating_rejected(self):
        ctx = _position_ctx(revenue_decelerating=True)
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("decelerating", reason)

    def test_sector_below_50d_rejected(self):
        ctx = _position_ctx(sector_above_50d=False)
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("below 50-day", reason)

    def test_sector_data_missing_rejected(self):
        ctx = _position_ctx(sector_above_50d=None)
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("unavailable", reason.lower())

    def test_sector_underperforming_spy_rejected(self):
        ctx = _position_ctx(sector_3m_vs_spy=2.0)  # below 5% threshold
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("outperformance threshold", reason)

    def test_panic_regime_rejected(self):
        ctx = _position_ctx(regime="PANIC")
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("hostile regime", reason)

    def test_bear_trending_regime_rejected(self):
        ctx = _position_ctx(regime="BEAR_TRENDING")
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("hostile regime", reason)

    def test_earnings_too_close_rejected(self):
        ctx = _position_ctx(earnings_days_away=20)
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("30-day gate", reason)

    def test_sell_consensus_long_rejected(self):
        ctx = _position_ctx(analyst_consensus="STRONG_SELL")
        ok, reason, _ = _validate_position("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("STRONG_SELL", reason)


# ── Hierarchy tests ───────────────────────────────────────────────────────────

class TestClassifyHierarchy(unittest.TestCase):

    def test_position_wins_when_all_conditions_met(self):
        ctx = _position_ctx()
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=40)
        self.assertEqual(trade_type, "POSITION")

    def test_swing_when_position_fails(self):
        # Revenue growth too low for POSITION → falls to SWING
        ctx = _swing_ctx(
            revenue_growth_yoy=5.0,
            sector_above_50d=True,
            sector_3m_vs_spy=8.0,
        )
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=35)
        self.assertEqual(trade_type, "SWING")

    def test_intraday_when_swing_fails(self):
        # No catalyst → SWING fails → falls to INTRADAY
        ctx = _intraday_ctx(
            catalyst_type="none",
            catalyst_score=5.0,
            recent_upgrade=False,
            earnings_days_away=10,
        )
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=35)
        self.assertEqual(trade_type, "INTRADAY")

    def test_reject_when_all_fail(self):
        # No catalyst, stale signal → all types fail
        ctx = _intraday_ctx(
            signal_age_minutes=30.0,
            catalyst_type="none",
            catalyst_score=5.0,
            recent_upgrade=False,
            revenue_growth_yoy=5.0,
        )
        trade_type, reason, _ = classify_trade_type("LONG", ctx, score=35)
        self.assertEqual(trade_type, "REJECT")
        self.assertIn("REJECT", reason)


# ── validate_entry tests ──────────────────────────────────────────────────────

class TestValidateEntry(unittest.TestCase):

    def test_approved_intraday_returns_true(self):
        ctx = _intraday_ctx()
        allowed, trade_type, reason, eff_score = validate_entry("LONG", ctx, score=35)
        self.assertTrue(allowed)
        self.assertEqual(trade_type, "INTRADAY")
        self.assertEqual(eff_score, 35)

    def test_dead_window_penalty_reduces_effective_score(self):
        ctx = _intraday_ctx(in_dead_window=True)
        allowed, trade_type, reason, eff_score = validate_entry("LONG", ctx, score=20)
        # 20 - 8 penalty = 12, below default min_score=14 → rejected
        self.assertFalse(allowed)
        self.assertIn("effective score", reason)

    def test_dead_window_with_high_score_passes(self):
        ctx = _intraday_ctx(in_dead_window=True)
        allowed, trade_type, reason, eff_score = validate_entry("LONG", ctx, score=30)
        # 30 - 8 = 22 ≥ 14 → passes
        self.assertTrue(allowed)
        self.assertEqual(eff_score, 22)

    def test_reject_returns_false(self):
        ctx = _intraday_ctx(signal_age_minutes=30.0, catalyst_type="none",
                            catalyst_score=1.0, recent_upgrade=False,
                            revenue_growth_yoy=3.0)
        allowed, trade_type, _, _ = validate_entry("LONG", ctx, score=35)
        self.assertFalse(allowed)
        self.assertEqual(trade_type, "REJECT")

    def test_position_approved_with_full_context(self):
        ctx = _position_ctx()
        allowed, trade_type, reason, eff_score = validate_entry("LONG", ctx, score=40)
        self.assertTrue(allowed)
        self.assertEqual(trade_type, "POSITION")


if __name__ == "__main__":
    unittest.main()
