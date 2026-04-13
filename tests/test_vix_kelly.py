"""
tests/test_vix_kelly.py — Unit tests for VIX-rank adaptive Kelly fraction
and ATR volatility cap in calculate_position_size().

All tests are pure unit tests: no network calls, no IBKR dependency.
get_vix_rank() is patched to inject VIX rank values.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG

# Evict any hollow stub that test_reconnect.py may have installed for 'risk'
sys.modules.pop("risk", None)
import risk

# ── Helpers ───────────────────────────────────────────────────────────────────

_VIX_KELLY = CONFIG.get("vix_kelly", {"base_kelly": 0.50, "max_reduction": 0.80})
_BASE_KELLY = _VIX_KELLY["base_kelly"]  # 0.50
_MAX_REDUCTION = _VIX_KELLY["max_reduction"]  # 0.80
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
        expected = _expected_kelly(1.0)  # 0.50*(1-0.80) = 0.10
        assert frac == pytest.approx(expected, rel=1e-6)
        assert frac < 0.15

    def test_zero_rank_equals_base_kelly(self):
        """At rank 0.0, fraction should equal base_kelly exactly."""
        with patch.object(risk, "get_vix_rank", return_value=0.0):
            frac, _ = risk.get_kelly_fraction()
        assert frac == pytest.approx(_BASE_KELLY, rel=1e-6)


# ── Test 3: ATR is now in the PRIMARY formula — larger ATR → fewer shares ─────
#
# Previously ATR was only the secondary vol cap.  It is now in the denominator
# of the primary formula (risk_amount / stop_dollars).  Larger ATR = wider stop
# = fewer shares for the same risk budget.


class TestAtrPrimaryEffect:
    def test_larger_atr_gives_fewer_shares(self):
        """
        Doubling ATR doubles the stop distance, halving shares (same risk budget).
        Primary formula: qty = risk_amount / (atr × atr_stop_multiplier)
        """
        portfolio = 100_000.0
        price = 50.0
        score = 30

        with patch.object(risk, "get_vix_rank", return_value=0.0):
            qty_tight = risk.calculate_position_size(portfolio, price, score, _REGIME_NEUTRAL, atr=2.0)
            qty_wide = risk.calculate_position_size(portfolio, price, score, _REGIME_NEUTRAL, atr=4.0)

        assert qty_wide < qty_tight, f"Wider ATR stop should yield fewer shares: atr=2→{qty_tight}, atr=4→{qty_wide}"
        # Doubling ATR halves qty (within integer-truncation tolerance)
        ratio = qty_wide / qty_tight
        assert abs(ratio - 0.5) < 0.1, f"Doubling ATR should halve qty, got ratio={ratio:.3f}"

    def test_no_atr_fallback_returns_sensible_qty(self):
        """
        atr=0 triggers the assumed_stop_pct fallback.  Result should be a
        positive integer in a reasonable range (not zero, not thousands).
        """
        portfolio = 100_000.0
        price = 50.0
        score = 30

        with patch.object(risk, "get_vix_rank", return_value=0.0):
            qty = risk.calculate_position_size(portfolio, price, score, _REGIME_NEUTRAL, atr=0.0)

        assert qty >= 1
        # Fallback assumed_stop=4%: position ≈ risk_amount/0.04/price
        # = 100000*0.005*0.5 / 0.04 / 50 = 125 shares; check within 2×
        assert qty <= 300, f"Fallback qty {qty} seems unreasonably large"


# ── Test 4: Secondary ATR vol cap (belt-and-suspenders) ──────────────────────
#
# With ATR-based primary sizing, the secondary ATR vol cap (Layer 9) rarely
# fires.  It is kept as an emergency guard for corrupted data.


class TestAtrVolCapSecondary:
    def test_secondary_cap_does_not_fire_under_normal_conditions(self):
        """
        At typical ATR (2.0), the primary formula already produces conservative
        sizing; the secondary vol cap should not further reduce qty.
        """
        portfolio = 100_000.0
        price = 50.0
        score = 30
        atr = 2.0

        secondary_cap = int((portfolio * CONFIG["atr_vol_target_pct"]) / atr)  # 500 shares

        with patch.object(risk, "get_vix_rank", return_value=0.0):
            qty = risk.calculate_position_size(portfolio, price, score, _REGIME_NEUTRAL, atr=atr)

        # Primary path should give fewer shares than the secondary cap
        assert qty <= secondary_cap, f"qty={qty} should not exceed secondary vol cap={secondary_cap}"
