"""
tests/test_vix_kelly.py — Unit tests for VIX-rank adaptive Kelly fraction
and ATR volatility cap in calculate_position_size().

All tests are pure unit tests: no network calls, no IBKR dependency.
get_vix_rank() is patched to inject VIX rank values.
"""

import sys
import os
import pytest
from unittest.mock import patch

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
import risk


# ── Helpers ───────────────────────────────────────────────────────────────────

_BASE_KELLY     = CONFIG["vix_kelly"]["base_kelly"]      # 0.50
_MAX_REDUCTION  = CONFIG["vix_kelly"]["max_reduction"]   # 0.80
_REGIME_NEUTRAL = {"position_size_multiplier": 1.0}


def _expected_kelly(vix_rank: float) -> float:
    """Reference formula: base_kelly * (1 - vix_rank * max_reduction), floored at 0.05."""
    raw = _BASE_KELLY * (1.0 - vix_rank * _MAX_REDUCTION)
    return max(0.05, min(1.0, raw))


# ── Test 1: Low VIX rank → larger Kelly fraction ──────────────────────────────

class TestLowVixRankLargerFraction:
    def test_low_rank_fraction(self):
        """VIX rank 0.1 (calm market) should produce a fraction near base_kelly."""
        with patch.object(risk, "get_vix_rank", return_value=0.1):
            frac, vix_rank = risk.get_kelly_fraction()
        assert vix_rank == pytest.approx(0.1)
        assert frac == pytest.approx(_expected_kelly(0.1), rel=1e-6)
        # At rank 0.1: 0.50 * (1 - 0.1*0.8) = 0.50 * 0.92 = 0.46
        assert frac > 0.40


# ── Test 2: High VIX rank → smaller Kelly fraction ────────────────────────────

class TestHighVixRankSmallerFraction:
    def test_high_rank_smaller_than_low(self):
        """VIX rank 0.9 should produce a meaningfully smaller fraction than rank 0.1."""
        with patch.object(risk, "get_vix_rank", return_value=0.1):
            frac_low, _ = risk.get_kelly_fraction()
        with patch.object(risk, "get_vix_rank", return_value=0.9):
            frac_high, _ = risk.get_kelly_fraction()
        assert frac_high < frac_low

    def test_max_rank_approaches_floor(self):
        """At rank 1.0, fraction should equal base_kelly*(1-max_reduction)."""
        with patch.object(risk, "get_vix_rank", return_value=1.0):
            frac, _ = risk.get_kelly_fraction()
        expected = _expected_kelly(1.0)   # 0.50*(1-0.80) = 0.10
        assert frac == pytest.approx(expected, rel=1e-6)
        assert frac < 0.15

    def test_zero_rank_equals_base_kelly(self):
        """At rank 0.0, fraction should equal base_kelly exactly."""
        with patch.object(risk, "get_vix_rank", return_value=0.0):
            frac, _ = risk.get_kelly_fraction()
        assert frac == pytest.approx(_BASE_KELLY, rel=1e-6)


# ── Test 3: ATR cap wins when more conservative than Kelly ────────────────────

class TestAtrCapWins:
    def test_atr_cap_reduces_qty(self):
        """
        With a small ATR (tight vol), atr_capped_qty should be less than the
        Kelly-sized qty, and the function should return the ATR-capped value.
        """
        portfolio   = 100_000.0
        price       = 50.0
        score       = 30   # conviction_mult = 1.0
        # Force neutral Kelly (rank 0.0 → kelly = base_kelly = 0.5)
        with patch.object(risk, "get_vix_rank", return_value=0.0):
            # Small ATR → tight ATR cap
            # atr_vol_target_pct = 0.01 → target = $1,000
            # atr = $5.00 → atr_capped_qty = 1000/5 = 200
            small_atr = 5.0
            qty = risk.calculate_position_size(portfolio, price, score, _REGIME_NEUTRAL, atr=small_atr)

        # atr_capped_qty = int(100000 * 0.01 / 5.0) = 200
        atr_expected = int((portfolio * CONFIG["atr_vol_target_pct"]) / small_atr)
        assert qty == atr_expected, (
            f"Expected ATR-capped qty {atr_expected}, got {qty}"
        )


# ── Test 4: Kelly path wins when ATR cap is loose ─────────────────────────────

class TestKellyWins:
    def test_large_atr_does_not_further_restrict(self):
        """
        With a large ATR (wide vol), atr_capped_qty is loose (many shares allowed),
        so the Kelly path determines final qty.
        """
        portfolio = 100_000.0
        price     = 50.0
        score     = 30

        with patch.object(risk, "get_vix_rank", return_value=0.0):
            # Large ATR → ATR cap is very generous
            # atr_vol_target_pct=0.01 → target=$1,000; atr=0.01 → cap=100,000 shares
            large_atr = 0.01
            qty_with_atr = risk.calculate_position_size(
                portfolio, price, score, _REGIME_NEUTRAL, atr=large_atr
            )
            qty_no_atr = risk.calculate_position_size(
                portfolio, price, score, _REGIME_NEUTRAL, atr=0.0
            )

        # With a very loose ATR cap, sizing should match the no-ATR path
        assert qty_with_atr == qty_no_atr, (
            f"Expected Kelly path to dominate: qty_with_atr={qty_with_atr}, "
            f"qty_no_atr={qty_no_atr}"
        )
