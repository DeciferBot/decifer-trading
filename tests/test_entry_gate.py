#!/usr/bin/env python3
"""
Unit tests for entry_gate.py and trade_context.py.

Covers:
  - INTRADAY gate: market closed, earnings same day, 2-of-3 signal gate, SHORT flow+squeeze
  - SWING gate: PANIC regime, earnings < 5 days, news-alone block, SHORT bearish-regime gate
  - POSITION gate: hostile regime, earnings < 5 days, LONG-only, equity-only
  - classify_trade_type: hierarchy (POSITION > SWING > INTRADAY), INTRADAY fallback (no REJECT)
  - validate_entry: score=0 SWING/POSITION block, concurrent INTRADAY limit
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


# ── Signal score helpers ──────────────────────────────────────────────────────
# score_breakdown with 2 of 3 required signals ≥ 5 (flow + squeeze).
# Used to pass the INTRADAY 2-of-3 gate in tests that test other conditions.
_PASSING_BD: dict = {"flow": 6, "squeeze": 6, "momentum": 3}   # 2-of-3 OK (flow+squeeze)
_WEAK_BD: dict    = {"flow": 2, "squeeze": 2, "momentum": 7}   # only 1-of-3 ≥5 (momentum only → REJECT)
_SHORT_BD: dict   = {"flow": 6, "squeeze": 6, "momentum": 3}   # SHORT needs flow+squeeze both ≥5


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
        catalyst_score=5.0,   # 0–10 scale (catalyst_engine native)
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
        ok, reason, penalty = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)
        self.assertEqual(penalty, 0)

    def test_stale_signal_passes(self):
        # Stale signal no longer blocked — label only
        ctx = _intraday_ctx(signal_age_minutes=20.0)
        ok, reason, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)

    def test_earnings_same_day_rejected(self):
        # Hard block fires before signal gate — no score_breakdown needed
        ctx = _intraday_ctx(earnings_days_away=0)
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("earnings same day", reason)

    def test_low_volume_passes(self):
        # Volume no longer a gate — note only
        ctx = _intraday_ctx(rel_volume=0.6)
        ok, _, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)

    def test_hod_noman_land_passes(self):
        # HOD no-man's land no longer blocked
        ctx = _intraday_ctx(hod_distance_pct=-2.0)
        ok, _, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)

    def test_at_hod_passes(self):
        ctx = _intraday_ctx(hod_distance_pct=-0.5)
        ok, _, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)

    def test_below_vwap_long_passes(self):
        # VWAP position no longer a gate
        ctx = _intraday_ctx(vwap_distance_pct=-2.0)
        ok, _, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)

    def test_above_vwap_short_passes(self):
        # VWAP position no longer a gate; SHORT requires flow+squeeze both ≥5
        ctx = _intraday_ctx(vwap_distance_pct=2.0)
        ok, _, _ = _validate_intraday("SHORT", ctx, score_breakdown=_SHORT_BD)
        self.assertTrue(ok)

    def test_close_window_hard_rejects_intraday(self):
        # Hard block fires before signal gate — no score_breakdown needed
        ctx = _intraday_ctx(time_of_day_window="CLOSE")
        ok, reason, _ = _validate_intraday("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("market closed", reason)

    def test_dead_window_no_penalty(self):
        # Dead window no longer adds a penalty
        ctx = _intraday_ctx(in_dead_window=True)
        ok, reason, penalty = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)
        self.assertEqual(penalty, 0)

    def test_wide_spread_passes(self):
        # Spread no longer a gate
        ctx = _intraday_ctx(bid_ask_spread_pct=1.2)
        ok, _, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
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
        ok, reason, penalty = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertIsInstance(ok, bool)

    # ── Change 2: 2-of-3 signal gate ─────────────────────────────────────────

    def test_2of3_gate_passes_with_flow_and_squeeze(self):
        """flow≥5 AND squeeze≥5 → 2 of 3 → PASS."""
        ctx = _intraday_ctx()
        ok, _, _ = _validate_intraday("LONG", ctx, score_breakdown={"flow": 6, "squeeze": 5, "momentum": 2})
        self.assertTrue(ok)

    def test_2of3_gate_passes_with_flow_and_momentum(self):
        """flow≥5 AND momentum≥5 → 2 of 3 → PASS."""
        ctx = _intraday_ctx()
        ok, _, _ = _validate_intraday("LONG", ctx, score_breakdown={"flow": 6, "squeeze": 2, "momentum": 5})
        self.assertTrue(ok)

    def test_2of3_gate_rejects_only_momentum(self):
        """Only momentum≥5, flow=2, squeeze=2 → 1 of 3 → REJECT."""
        ctx = _intraday_ctx()
        ok, reason, _ = _validate_intraday("LONG", ctx, score_breakdown=_WEAK_BD)
        self.assertFalse(ok)
        self.assertIn("2-of-3", reason)

    def test_2of3_gate_rejects_no_signals(self):
        """All dimensions = 0 → 0 of 3 → REJECT."""
        ctx = _intraday_ctx()
        ok, reason, _ = _validate_intraday("LONG", ctx, score_breakdown={})
        self.assertFalse(ok)
        self.assertIn("2-of-3", reason)

    # ── Change 3: INTRADAY SHORT requires flow AND squeeze ────────────────────

    def test_short_requires_flow_and_squeeze(self):
        """SHORT with squeeze+momentum ≥5 (2-of-3 passes) but flow<5 → REJECT by SHORT gate."""
        ctx = _intraday_ctx()
        # flow=2 (< 5), squeeze=6 (≥5), momentum=8 (≥5) → 2-of-3 OK but flow missing for SHORT
        ok, reason, _ = _validate_intraday("SHORT", ctx, score_breakdown={"flow": 2, "squeeze": 6, "momentum": 8})
        self.assertFalse(ok)
        self.assertIn("SHORT", reason)

    def test_short_with_flow_and_squeeze_passes(self):
        """SHORT with flow≥5 AND squeeze≥5 → PASS."""
        ctx = _intraday_ctx()
        ok, _, _ = _validate_intraday("SHORT", ctx, score_breakdown={"flow": 6, "squeeze": 6, "momentum": 1})
        self.assertTrue(ok)

    def test_short_missing_squeeze_rejected(self):
        """SHORT with flow≥5 but squeeze<5 → REJECT."""
        ctx = _intraday_ctx()
        ok, reason, _ = _validate_intraday("SHORT", ctx, score_breakdown={"flow": 7, "squeeze": 3, "momentum": 5})
        self.assertFalse(ok)
        self.assertIn("SHORT", reason)


# ── SWING gate tests ──────────────────────────────────────────────────────────

class TestSwingGate(unittest.TestCase):

    def test_valid_swing_passes(self):
        # catalyst_type="earnings_beat" is a structural catalyst → passes news-alone gate
        ctx = _swing_ctx()
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    def test_low_catalyst_score_rejected(self):
        # catalyst_score below swing_min_catalyst_score (5.0) must be rejected
        ctx = _swing_ctx(
            catalyst_type="none",
            catalyst_score=1.5,
            recent_upgrade=False,
            sector_days_since_breakout=15,
        )
        ok, reason, _ = _validate_swing("LONG", ctx)
        self.assertFalse(ok)
        self.assertIn("catalyst score", reason)

    def test_sell_consensus_long_passes(self):
        # Analyst consensus no longer a gate; catalyst_type="earnings_beat" is structural
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

    # ── Change 6: news-alone SWING block ─────────────────────────────────────
    # NOTE: production config has swing_news_alone_blocks=False (SHADOW MODE) because
    # catalyst fields are 0% populated in historical data. Gate logic is tested here
    # by temporarily enabling the flag. Shadow mode test verifies no-reject behaviour.

    def test_news_alone_swing_blocked(self):
        """No structural catalyst + catalyst_type='news' → REJECT when flag enabled (news IC=-0.253)."""
        from config import CONFIG
        ctx = _swing_ctx(
            catalyst_type="news",
            catalyst_score=6.0,      # above score floor — still blocked by news-alone gate
            recent_upgrade=False,
            insider_net_sentiment=None,
            congressional_sentiment=None,
        )
        # Temporarily enable the gate (production default is False / shadow mode)
        original = CONFIG.get("entry_gate", {}).get("swing_news_alone_blocks", False)
        CONFIG["entry_gate"]["swing_news_alone_blocks"] = True
        try:
            ok, reason, _ = _validate_swing("LONG", ctx)
        finally:
            CONFIG["entry_gate"]["swing_news_alone_blocks"] = original
        self.assertFalse(ok)
        self.assertIn("news-alone", reason)

    def test_news_alone_swing_shadow_mode_does_not_block(self):
        """Shadow mode (swing_news_alone_blocks=False): news-alone SWING is NOT rejected."""
        from config import CONFIG
        ctx = _swing_ctx(
            catalyst_type="news",
            catalyst_score=6.0,
            recent_upgrade=False,
            insider_net_sentiment=None,
            congressional_sentiment=None,
        )
        # Ensure shadow mode is active (default production setting)
        original = CONFIG.get("entry_gate", {}).get("swing_news_alone_blocks", False)
        CONFIG["entry_gate"]["swing_news_alone_blocks"] = False
        try:
            ok, reason, _ = _validate_swing("LONG", ctx)
        finally:
            CONFIG["entry_gate"]["swing_news_alone_blocks"] = original
        # Shadow mode: gate logs but does NOT reject
        self.assertTrue(ok)

    def test_news_with_recent_upgrade_passes(self):
        """news catalyst_type BUT recent_upgrade=True is a structural catalyst → PASS."""
        ctx = _swing_ctx(
            catalyst_type="news",
            catalyst_score=6.0,
            recent_upgrade=True,
        )
        ok, _, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    def test_none_catalyst_with_insider_buying_passes(self):
        """catalyst_type=None BUT insider buying → structural catalyst present → PASS."""
        ctx = _swing_ctx(
            catalyst_type="none",
            catalyst_score=6.0,
            recent_upgrade=False,
            insider_net_sentiment="BUYING",
        )
        ok, _, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    def test_earnings_catalyst_type_passes_news_gate(self):
        """catalyst_type='earnings' is structural → news-alone gate passes."""
        ctx = _swing_ctx(catalyst_type="earnings", catalyst_score=6.0)
        ok, _, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)

    # ── Change 7: SWING SHORT bearish-regime gate ─────────────────────────────

    def test_swing_short_blocked_in_trending_up(self):
        """SWING SHORT in TRENDING_UP regime → REJECT."""
        ctx = _swing_ctx(regime="TRENDING_UP")
        ok, reason, _ = _validate_swing("SHORT", ctx)
        self.assertFalse(ok)
        self.assertIn("SHORT", reason)
        self.assertIn("TRENDING_UP", reason)

    def test_swing_short_blocked_in_range_bound(self):
        """SWING SHORT in RANGE_BOUND → REJECT (not in bearish set)."""
        ctx = _swing_ctx(regime="RANGE_BOUND")
        ok, reason, _ = _validate_swing("SHORT", ctx)
        self.assertFalse(ok)

    def test_swing_short_allowed_in_trending_down(self):
        """SWING SHORT in TRENDING_DOWN → PASS."""
        ctx = _swing_ctx(catalyst_type="earnings", catalyst_score=6.0,
                         regime="TRENDING_DOWN")
        ok, _, _ = _validate_swing("SHORT", ctx)
        self.assertTrue(ok)

    def test_swing_short_allowed_in_capitulation(self):
        """SWING SHORT in CAPITULATION → PASS."""
        ctx = _swing_ctx(catalyst_type="earnings", catalyst_score=6.0,
                         regime="CAPITULATION")
        ok, _, _ = _validate_swing("SHORT", ctx)
        self.assertTrue(ok)

    def test_swing_long_not_affected_by_short_gate(self):
        """SWING LONG in TRENDING_UP → not blocked by short regime gate."""
        ctx = _swing_ctx(regime="TRENDING_UP")
        ok, _, _ = _validate_swing("LONG", ctx)
        self.assertTrue(ok)


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

    # ── Change 8: POSITION LONG-only and equity-only ──────────────────────────

    def test_position_short_rejected(self):
        """POSITION SHORT → downgrade to SWING (position_long_only=True)."""
        ctx = _position_ctx()
        ok, reason = _validate_position("SHORT", ctx)
        self.assertFalse(ok)
        self.assertIn("position_long_only", reason)
        self.assertIn("downgrade to SWING", reason)

    def test_position_call_rejected(self):
        """POSITION call option → downgrade to SWING (position_equity_only=True)."""
        ctx = _position_ctx()
        ok, reason = _validate_position("LONG", ctx, instrument="call")
        self.assertFalse(ok)
        self.assertIn("position_equity_only", reason)

    def test_position_put_rejected(self):
        """POSITION put option → downgrade to SWING."""
        ctx = _position_ctx()
        ok, reason = _validate_position("LONG", ctx, instrument="put")
        self.assertFalse(ok)
        self.assertIn("position_equity_only", reason)

    def test_position_stock_long_passes_new_gates(self):
        """POSITION LONG equity is unchanged — still qualifies via Path B."""
        ctx = _position_ctx()
        ok, reason = _validate_position("LONG", ctx, instrument="stock")
        self.assertTrue(ok)

    def test_position_common_instrument_passes(self):
        """POSITION with instrument='COMMON' (stock) passes equity-only gate."""
        ctx = _position_ctx()
        ok, _ = _validate_position("LONG", ctx, instrument="COMMON")
        self.assertTrue(ok)


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
        # No opus_trade_type → neither INTRADAY nor SWING sub-gate runs
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

    # ── Change 5: block score=0 SWING/POSITION entries ───────────────────────

    def test_score_zero_swing_rejected(self):
        """score=0 with opus_trade_type=SWING → REJECT (no signal basis)."""
        ctx = _swing_ctx()
        allowed, trade_type, reason, _ = validate_entry(
            "LONG", ctx, score=0, opus_trade_type="SWING"
        )
        self.assertFalse(allowed)
        self.assertEqual(trade_type, "REJECT")
        self.assertIn("score=0", reason)

    def test_score_zero_position_rejected(self):
        """score=0 with opus_trade_type=POSITION → REJECT."""
        ctx = _position_ctx()
        allowed, trade_type, reason, _ = validate_entry(
            "LONG", ctx, score=0, opus_trade_type="POSITION"
        )
        self.assertFalse(allowed)
        self.assertEqual(trade_type, "REJECT")
        self.assertIn("score=0", reason)

    def test_score_zero_intraday_not_blocked_by_score_zero_gate(self):
        """score=0 INTRADAY is not blocked by the SWING/POSITION score=0 gate."""
        ctx = _intraday_ctx()
        # No opus_trade_type — SWING/POSITION score=0 gate does not apply.
        # Rejection (if any) is from min_score floor, not the specific SWING/POSITION gate.
        allowed, _, reason, _ = validate_entry("LONG", ctx, score=0)
        # Must NOT contain the specific SWING/POSITION gate tag
        self.assertNotIn("score_zero_swing_position", reason)

    # ── Change 4: max concurrent INTRADAY ────────────────────────────────────

    def test_intraday_concurrent_limit_blocks_entry(self):
        """open_intraday_count ≥ 2 → REJECT new INTRADAY entries."""
        ctx = _intraday_ctx()
        allowed, trade_type, reason, _ = validate_entry(
            "LONG", ctx, score=45, opus_trade_type="INTRADAY",
            score_breakdown=_PASSING_BD, open_intraday_count=2,
        )
        self.assertFalse(allowed)
        self.assertEqual(trade_type, "REJECT")
        self.assertIn("intraday_max_concurrent", reason)

    def test_intraday_concurrent_at_limit_minus_one_passes(self):
        """open_intraday_count = 1 (below limit of 2) → gate passes."""
        ctx = _intraday_ctx()
        allowed, _, _, _ = validate_entry(
            "LONG", ctx, score=45, opus_trade_type="INTRADAY",
            score_breakdown=_PASSING_BD, open_intraday_count=1,
        )
        self.assertTrue(allowed)


# ── Session-aware rel_vol tests (volume no longer gates) ─────────────────────

class TestSessionAwareRelVolThresholds(unittest.TestCase):
    """Volume no longer gates trades — all rel_vol values pass INTRADAY."""

    def test_after_hours_low_volume_passes(self):
        """Volume is not a gate — any rel_vol passes INTRADAY (given valid signals)."""
        ctx = _intraday_ctx(rel_volume=0.2)
        with patch("risk.get_session", return_value="AFTER_HOURS"):
            ok, reason, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)

    def test_regular_session_low_volume_passes(self):
        """Volume is not a gate — low rel_vol no longer hard-fails."""
        ctx = _intraday_ctx(rel_volume=0.6)
        with patch("risk.get_session", return_value="PRIME_AM"):
            ok, reason, _ = _validate_intraday("LONG", ctx, score_breakdown=_PASSING_BD)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()


# ── Entry thesis tests (absorbed from test_entry_thesis.py) ──────────────────
# _build_entry_thesis() produces a falsifiable entry thesis string stored in
# each trade record. Tests confirm the output contains required fields.

from orders_core import _build_entry_thesis


def test_scalp_thesis_contains_time_and_pnl():
    t = _build_entry_thesis("SCALP", "AAPL", "LONG", 0.75, 40, "BULL")
    assert "SCALP" in t
    assert "LONG" in t
    assert "AAPL" in t
    assert "wrong_if" in t
    assert "90min" in t
    assert "0.0%" in t
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
    assert "BEAR" in t


def test_unknown_trade_type_falls_back():
    t = _build_entry_thesis("", "SPY", "LONG", 0.50, 30, "NEUTRAL")
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
