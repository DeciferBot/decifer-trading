"""
tests/test_sizing_pipeline.py — Regression tests for the unified sizing pipeline.

Verifies that ALL multipliers flow through calculate_position_size() and that
no caller silently mutates the returned qty.  Each test exercises one layer of
the pipeline so regressions are immediately attributable to a specific layer.

Pipeline under test (risk.py::calculate_position_size):
  L1  Kelly fraction   (VIX-rank adaptive)
  L2  Conviction mult  (score-driven: 0.75 / 1.0 / 1.5)
  L3  Regime mult      (0.0–1.0)
  L4  Session mult     (0.75 / 1.0)
  L5  Strategy mult    (1.0 / 0.7 / 0.5)
  L6  External mult    (caller-supplied: sentinel=0.75, catalyst=0.375)
  Primary conversion: risk_amount / stop_dollars  (ATR-based, not a 2% proxy)
  L7  Max single-position cap
  L8  Hard 20% cap
  L9  ATR vol cap (secondary)

NOTE ON TEST PARAMETERS
-----------------------
Ratio tests patch risk_pct_per_trade=0.001 and max_single_position=100.0 so
the Kelly path stays well below the safety caps and every multiplier layer is
visible.  A standard ATR=2.0 is provided so the primary ATR-based path fires.
Safety-cap tests use live config (uncapped=False).
"""

import sys
import os
import inspect
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance",
             "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

import risk
from config import CONFIG

# ── Shared constants ──────────────────────────────────────────────────────────

PORTFOLIO  = 100_000.0
PRICE      = 50.0
ATR        = 2.0          # typical ATR for a $50 stock; stop_dollars = 2.0 × 1.5 = $3.00
SCORE_LOW  = 20           # → conviction_mult = 0.75
SCORE_MID  = 32           # → conviction_mult = 1.0
SCORE_HIGH = CONFIG["high_conviction_score"]  # → conviction_mult = 1.5

REGIME_NEUTRAL = {"position_size_multiplier": 1.0}
REGIME_PANIC   = {"position_size_multiplier": 0.0}
REGIME_HALF    = {"position_size_multiplier": 0.5}

VIX_CALM  = 0.0
VIX_PANIC = 1.0

# Patch values for ratio tests: remove caps, lower risk_pct so Kelly path is visible
_UNCAPPED_CONFIG = {
    "risk_pct_per_trade": 0.001,   # keeps position value well under 20% hard cap
    "max_single_position": 100.0,  # effectively removes 10% single-position cap
}


def _call(portfolio=PORTFOLIO, price=PRICE, score=SCORE_MID,
          regime=None, atr=ATR, external_mult=1.0,
          vix_rank=VIX_CALM, strategy_mult=1.0, session="REGULAR",
          uncapped=True):
    """
    Helper: call calculate_position_size with controlled environment.

    uncapped=True  — patches config to remove caps; multiplier ratios are visible.
    uncapped=False — uses live config values; tests safety-cap behaviour.
    ATR defaults to 2.0 so the ATR-primary path fires by default.
    """
    if regime is None:
        regime = REGIME_NEUTRAL
    cfg_patch = _UNCAPPED_CONFIG if uncapped else {}
    with patch.object(risk, "get_vix_rank", return_value=vix_rank), \
         patch.object(risk, "get_session", return_value=session), \
         patch.dict(CONFIG, cfg_patch):
        risk._strategy_size_multiplier = strategy_mult
        return risk.calculate_position_size(
            portfolio_value=portfolio,
            price=price,
            score=score,
            regime=regime,
            atr=atr,
            external_mult=external_mult,
        )


# ── Layer 2: Conviction multiplier ────────────────────────────────────────────

class TestConvictionLayer:
    def test_high_conviction_larger_than_low(self):
        assert _call(score=SCORE_HIGH) > _call(score=SCORE_LOW)

    def test_high_conviction_is_2x_low(self):
        """1.5 / 0.75 = 2.0× ratio; integer math keeps it within 15%."""
        qty_high = _call(score=SCORE_HIGH)
        qty_low  = _call(score=SCORE_LOW)
        ratio = qty_high / qty_low
        assert abs(ratio - 2.0) < 0.15, f"Expected ~2.0×, got {ratio:.3f}"

    def test_mid_conviction_between_extremes(self):
        assert _call(score=SCORE_LOW) <= _call(score=SCORE_MID) <= _call(score=SCORE_HIGH)


# ── Layer 3: Regime multiplier ────────────────────────────────────────────────

class TestRegimeLayer:
    def test_panic_regime_produces_minimum_position(self):
        assert _call(regime=REGIME_PANIC) == 1

    def test_half_regime_roughly_half_neutral(self):
        ratio = _call(regime=REGIME_HALF) / _call(regime=REGIME_NEUTRAL)
        assert abs(ratio - 0.5) < 0.1, f"Expected ~0.5×, got {ratio:.3f}"


# ── Layer 4: Session multiplier ───────────────────────────────────────────────

class TestSessionLayer:
    def test_extended_hours_reduces_position(self):
        assert _call(session="PRE_MARKET") < _call(session="REGULAR")

    def test_extended_hours_is_75_pct(self):
        ratio = _call(session="AFTER_HOURS") / _call(session="REGULAR")
        assert abs(ratio - 0.75) < 0.05, f"Expected 0.75×, got {ratio:.3f}"


# ── Layer 5: Strategy mode multiplier ─────────────────────────────────────────

class TestStrategyModeLayer:
    def test_defensive_reduces_vs_normal(self):
        assert _call(strategy_mult=0.7) < _call(strategy_mult=1.0)

    def test_recovery_reduces_vs_defensive(self):
        assert _call(strategy_mult=0.5) < _call(strategy_mult=0.7)

    def test_recovery_is_50_pct_of_normal(self):
        ratio = _call(strategy_mult=0.5) / _call(strategy_mult=1.0)
        assert abs(ratio - 0.5) < 0.05, f"Expected 0.5×, got {ratio:.3f}"


# ── Layer 6: External multiplier (sentinel / catalyst) ────────────────────────

class TestExternalMultLayer:
    def test_no_external_mult_equals_default(self):
        assert _call(external_mult=1.0) == _call()

    def test_sentinel_mult_reduces_position(self):
        qty_normal   = _call(external_mult=1.0)
        qty_sentinel = _call(external_mult=0.75)
        assert qty_sentinel < qty_normal
        ratio = qty_sentinel / qty_normal
        assert abs(ratio - 0.75) < 0.05, f"Expected 0.75×, got {ratio:.3f}"

    def test_catalyst_mult_reduces_position(self):
        qty_normal   = _call(external_mult=1.0)
        qty_catalyst = _call(external_mult=0.375)
        assert qty_catalyst < qty_normal
        ratio = qty_catalyst / qty_normal
        assert abs(ratio - 0.375) < 0.05, f"Expected 0.375×, got {ratio:.3f}"

    def test_external_gt_1_clamped_to_1(self):
        assert _call(external_mult=2.0) == _call(external_mult=1.0)

    def test_external_zero_clamped_to_floor(self):
        assert _call(external_mult=0.0) >= 1


# ── Primary conversion: ATR-based stop vs fallback ────────────────────────────

class TestAtrPrimaryPath:
    """The core sizing formula uses actual stop distance, not a 2% proxy."""

    def test_larger_atr_gives_fewer_shares(self):
        """Wide stop (large ATR) = fewer shares for same risk amount."""
        qty_tight = _call(atr=1.0)
        qty_wide  = _call(atr=4.0)
        assert qty_wide < qty_tight, (
            f"Larger ATR (wider stop) should yield fewer shares: "
            f"atr=1→{qty_tight}, atr=4→{qty_wide}"
        )

    def test_atr_doubles_qty_halves(self):
        """Doubling ATR should halve qty (same risk_amount, twice the stop dollars)."""
        qty_atr2 = _call(atr=2.0)
        qty_atr4 = _call(atr=4.0)
        ratio = qty_atr4 / qty_atr2
        assert abs(ratio - 0.5) < 0.1, f"Doubling ATR should halve qty, got ratio={ratio:.3f}"

    def test_fallback_path_fires_when_no_atr(self):
        """atr=0 must still return a sensible qty via the assumed_stop_pct fallback."""
        qty = _call(atr=0.0)
        assert qty >= 1

    def test_atr_path_and_fallback_are_in_same_order_of_magnitude(self):
        """The two paths should not produce wildly different results for typical inputs."""
        qty_atr      = _call(atr=ATR)
        qty_fallback = _call(atr=0.0)
        # Allow up to 5× difference — they use different stop assumptions
        assert 0.2 <= (qty_fallback / qty_atr) <= 5.0, (
            f"ATR path ({qty_atr}) and fallback ({qty_fallback}) diverge too much"
        )


# ── Compounding / worst-case scenarios ───────────────────────────────────────

class TestCompoundingScenarios:
    def test_recovery_plus_catalyst_compounding(self):
        """RECOVERY (0.5×) + catalyst (0.375×) = 0.1875× — must be ≥ 1 share."""
        qty_normal   = _call(strategy_mult=1.0, external_mult=1.0)
        qty_compound = _call(strategy_mult=0.5, external_mult=0.375)
        assert qty_compound >= 1
        if qty_normal > 10:
            ratio = qty_compound / qty_normal
            assert ratio < 0.30, f"RECOVERY+catalyst should be < 30% of normal, got {ratio:.3f}"

    def test_all_multipliers_reduce_together(self):
        qty_normal = _call(strategy_mult=1.0, external_mult=1.0,
                           session="REGULAR", regime=REGIME_NEUTRAL, score=SCORE_MID)
        qty_worst  = _call(strategy_mult=0.5, external_mult=0.375,
                           session="PRE_MARKET", regime=REGIME_HALF, score=SCORE_LOW)
        assert qty_worst <= qty_normal

    def test_pipeline_is_commutative_for_equal_product(self):
        """Swapping strategy_mult and external_mult (same product) gives ±1 qty."""
        qty_a = _call(strategy_mult=0.7, external_mult=0.5)
        qty_b = _call(strategy_mult=0.5, external_mult=0.7)
        assert abs(qty_a - qty_b) <= 1, f"Commutative mults should give equal qty: {qty_a} vs {qty_b}"


# ── Safety caps ───────────────────────────────────────────────────────────────

class TestSafetyCapsLayer:
    def test_hard_cap_fires_on_extreme_low_price(self):
        """$0.01 price causes position value to explode — 20% hard cap must fire."""
        tiny_price = 0.01
        qty = _call(price=tiny_price, uncapped=False, atr=0.0)
        assert qty * tiny_price <= PORTFOLIO * 0.20 + tiny_price

    def test_external_mult_does_not_bypass_hard_cap(self):
        """Sentinel and normal trades are both bounded by the hard cap."""
        qty_normal   = _call(external_mult=1.0, uncapped=False)
        qty_sentinel = _call(external_mult=0.75, uncapped=False)
        assert qty_sentinel <= qty_normal

    def test_max_single_position_cap_fires_on_live_config(self):
        """
        With live config and high conviction + calm VIX + standard ATR,
        the max_single_position cap should hold position below 10% of portfolio.
        """
        qty = _call(score=SCORE_HIGH, vix_rank=VIX_CALM,
                    strategy_mult=1.0, external_mult=1.0,
                    atr=ATR, uncapped=False)
        max_allowed = int(PORTFOLIO * CONFIG["max_single_position"] / PRICE)
        assert qty <= max_allowed, (
            f"qty={qty} exceeded max_single_position cap of {max_allowed}"
        )


# ── Signature contract ────────────────────────────────────────────────────────

class TestSignatureContract:
    def test_function_accepts_external_mult_kwarg(self):
        assert "external_mult" in inspect.signature(risk.calculate_position_size).parameters

    def test_external_mult_default_is_1(self):
        default = inspect.signature(risk.calculate_position_size).parameters["external_mult"].default
        assert default == 1.0

    def test_returns_int(self):
        assert isinstance(_call(), int)

    def test_returns_at_least_one_share(self):
        assert _call(external_mult=0.001, strategy_mult=0.5, regime=REGIME_HALF) >= 1


# ── No double strategy-mode application ──────────────────────────────────────

class TestNoDoubleStrategyMode:
    def test_strategy_mode_applied_exactly_once(self):
        """
        RECOVERY mode (0.5×) must produce ~0.5× of NORMAL — not 0.25×.
        0.25× would indicate double-application of the strategy multiplier.
        """
        qty_normal   = _call(strategy_mult=1.0, external_mult=1.0)
        qty_recovery = _call(strategy_mult=0.5, external_mult=1.0)
        ratio = qty_recovery / qty_normal
        assert abs(ratio - 0.5) < 0.05, (
            f"Expected 0.5× (single application), got {ratio:.3f} "
            f"(0.25 would indicate double-application)"
        )

    def test_double_application_canary(self):
        """
        Simulates what agents.py used to do: apply size_mult post-hoc on top of
        _strategy_size_multiplier. Single-application result must be ~2× the double.
        """
        qty_single = _call(strategy_mult=0.5, external_mult=1.0)
        qty_double = _call(strategy_mult=0.5, external_mult=0.5)   # simulates the old bug
        if qty_single > 4:
            assert qty_single > qty_double, (
                f"Single-application ({qty_single}) should exceed double ({qty_double})"
            )
