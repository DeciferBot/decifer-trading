"""Tests for the Regime-Gated Signal Router.

Covers:
1. get_market_regime_vix() — VIX fetch → two-state classification
2. _regime_multipliers()   — correct multipliers per regime; flag disables routing
3. compute_confluence()    — weights shift per regime; flag disables cleanly
"""
import os
import sys
import types
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config as _config_mod

# Remove stale stub if another test replaced signals with a bare stub
if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]

import signals as _signals_mod  # capture module reference for patch.object (see below)
from signals import get_market_regime_vix, _regime_multipliers, compute_confluence
# NOTE: other test files (test_signals.py, test_signal_dispatch.py) replace
# sys.modules["signals"] at collection time with a new module object, so
# patch("signals._safe_download") would target the wrong module at runtime.
# patch.object(_signals_mod, ...) always targets THIS module, whose __dict__
# is also get_market_regime_vix.__globals__, guaranteeing the mock is seen.


# ── Helpers ───────────────────────────────────────────────────────────────────

def _vix_df(value: float) -> pd.DataFrame:
    """Minimal single-row DataFrame mimicking a yfinance VIX download."""
    return pd.DataFrame({"Close": [value]})


def _minimal_sig(direction: str = "bull") -> dict:
    """
    Return a minimal sig_5m dict sufficient for compute_confluence to run
    without errors.  'direction' controls whether TREND fires bullish or bearish.
    """
    bull = direction == "bull"
    return {
        "signal":          "BUY" if bull else "SELL",
        "bull_aligned":    bull,
        "bear_aligned":    not bull,
        "macd_accel":      1 if bull else -1,
        "macd_hist":       0.01,
        "adx":             30,
        "mfi":             70 if bull else 30,
        "rsi_slope":       1 if bull else -1,
        "squeeze_on":      False,
        "squeeze_intensity": 0,
        "bb_position":     0.7 if bull else 0.3,
        "vwap_dist":       0.5 if bull else -0.5,
        "obv_slope":       1 if bull else -1,
        "donch_breakout":  1 if bull else -1,
        "vol_ratio":       2.0,
        "candle_bull":     0,
        "candle_bear":     0,
        "zscore":          -2.0 if bull else 2.0,
        "variance_ratio":  0.5,
        "ou_halflife":     4.0,
        "adf_pvalue":      0.01,
        "price":           100.0,
        "atr":             1.5,
        "ema9":            101.0 if bull else 99.0,
        "ema21":           100.0,
        "ema50":           99.0 if bull else 101.0,
    }


def _minimal_sig_1d() -> dict:
    """Minimal daily sig for compute_confluence (MTF gate needs it)."""
    return {
        "signal":       "BUY",
        "bull_aligned": True,
        "bear_aligned": False,
        "adx":          28,
        "macd_hist":    0.02,
        "mfi":          60,
        "rsi_slope":    1,
        "squeeze_on":   False,
        "squeeze_intensity": 0,
        "bb_position":  0.6,
        "vwap_dist":    0.2,
        "obv_slope":    1,
        "donch_breakout": 1,
        "vol_ratio":    1.5,
        "candle_bull":  0,
        "candle_bear":  0,
        "zscore":       -1.0,
        "variance_ratio": 0.7,
        "ou_halflife":  15.0,
        "adf_pvalue":   0.08,
        "price":        100.0,
        "atr":          1.5,
    }


# ── 1. get_market_regime_vix() ────────────────────────────────────────────────

class TestGetMarketRegimeVix:

    def test_low_vix_returns_momentum(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 20)
        with patch.object(_signals_mod, "_safe_download", return_value=_vix_df(14.5)), \
             patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df):
            result = get_market_regime_vix()
        assert result["regime"] == "momentum"
        assert result["vix"] == 14.5

    def test_high_vix_returns_mean_reversion(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 20)
        with patch.object(_signals_mod, "_safe_download", return_value=_vix_df(28.0)), \
             patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df):
            result = get_market_regime_vix()
        assert result["regime"] == "mean_reversion"
        assert result["vix"] == 28.0

    def test_vix_exactly_at_threshold_returns_mean_reversion(self, monkeypatch):
        """VIX == threshold is NOT low-vol — boundary belongs to mean_reversion."""
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 20)
        with patch.object(_signals_mod, "_safe_download", return_value=_vix_df(20.0)), \
             patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df):
            result = get_market_regime_vix()
        assert result["regime"] == "mean_reversion"

    def test_threshold_is_configurable(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 25)
        with patch.object(_signals_mod, "_safe_download", return_value=_vix_df(24.9)), \
             patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df):
            result = get_market_regime_vix()
        assert result["regime"] == "momentum"

    def test_fetch_failure_defaults_to_momentum(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 20)
        with patch.object(_signals_mod, "_safe_download", side_effect=Exception("network error")):
            result = get_market_regime_vix()
        assert result["regime"] == "momentum"
        assert result["source"] == "fallback"

    def test_empty_data_defaults_to_momentum(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 20)
        with patch.object(_signals_mod, "_safe_download", return_value=None), \
             patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df):
            result = get_market_regime_vix()
        assert result["regime"] == "momentum"
        assert result["source"] == "fallback"


# ── 2. _regime_multipliers() ─────────────────────────────────────────────────

class TestRegimeMultipliers:

    MOMENTUM_DIMS = ("trend", "momentum", "squeeze", "flow", "breakout", "mtf")
    NEUTRAL_DIMS  = ("news", "social")
    REVERSION_DIM = "reversion"

    def test_momentum_regime_upweights_momentum_dims(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)
        mults = _regime_multipliers("momentum")
        for dim in self.MOMENTUM_DIMS:
            assert mults[dim] == 1.3, f"{dim} should be 1.3 in momentum regime"

    def test_momentum_regime_downweights_reversion(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)
        mults = _regime_multipliers("momentum")
        assert mults[self.REVERSION_DIM] == 0.7

    def test_mean_reversion_regime_upweights_reversion(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)
        mults = _regime_multipliers("mean_reversion")
        assert mults[self.REVERSION_DIM] == 1.3

    def test_mean_reversion_regime_downweights_momentum_dims(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)
        mults = _regime_multipliers("mean_reversion")
        for dim in self.MOMENTUM_DIMS:
            assert mults[dim] == 0.7, f"{dim} should be 0.7 in mean_reversion regime"

    def test_news_and_social_always_neutral(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        for regime in ("momentum", "mean_reversion", "unknown"):
            mults = _regime_multipliers(regime)
            for dim in self.NEUTRAL_DIMS:
                assert mults[dim] == 1.0, f"{dim} mult should be 1.0 in {regime}"

    def test_config_flag_disables_routing(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", False)
        for regime in ("momentum", "mean_reversion"):
            mults = _regime_multipliers(regime)
            assert all(v == 1.0 for v in mults.values()), \
                f"All multipliers should be 1.0 when routing disabled (regime={regime})"

    def test_unknown_regime_returns_all_ones(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        mults = _regime_multipliers("unknown")
        assert all(v == 1.0 for v in mults.values())

    def test_multipliers_are_configurable(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.5)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.5)
        mults_m = _regime_multipliers("momentum")
        mults_r = _regime_multipliers("mean_reversion")
        assert mults_m["trend"] == 1.5
        assert mults_m["reversion"] == 0.5
        assert mults_r["reversion"] == 1.5
        assert mults_r["trend"] == 0.5


# ── 3. compute_confluence() with regime routing ───────────────────────────────

class TestRegimeRoutingInConfluence:

    def _score(self, regime_router: str, monkeypatch,
               enabled: bool = True) -> dict:
        """Run compute_confluence with mtf_gate off for clean isolation."""
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", enabled)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", False)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)
        sig5  = _minimal_sig("bull")
        sig1d = _minimal_sig_1d()
        return compute_confluence(sig5, sig1d, None,
                                  news_score=5, social_score=5,
                                  regime_router=regime_router)

    def test_momentum_regime_scores_higher_than_neutral_for_trending_setup(self, monkeypatch):
        """A strong momentum setup should score higher with momentum routing active."""
        result_mom     = self._score("momentum",  monkeypatch)
        result_neutral = self._score("unknown",   monkeypatch)
        # Trending/momentum setup — multiplied dimensions all fire, score should increase
        assert result_mom["score"] >= result_neutral["score"]

    def test_mean_reversion_regime_scores_higher_reversion_component(self, monkeypatch):
        """
        In mean_reversion regime, the reversion dimension weight is 1.3×.
        A setup with strong reversion (adf_p=0.01, vr=0.5, ou_hl=4, z=2) should
        have reversion contribute more points than in the neutral case.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", False)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)
        sig5  = _minimal_sig("bull")
        sig1d = _minimal_sig_1d()
        # Force strong reversion signal on daily
        sig1d["adf_pvalue"]     = 0.01
        sig1d["variance_ratio"] = 0.5
        sig1d["ou_halflife"]    = 4.0
        sig5["zscore"] = -2.5

        result_mr  = compute_confluence(sig5, sig1d, None, regime_router="mean_reversion")
        result_neu = compute_confluence(sig5, sig1d, None, regime_router="unknown")

        assert result_mr["score_breakdown"]["reversion"] >= result_neu["score_breakdown"]["reversion"]

    def test_routing_disabled_identical_regardless_of_regime_router(self, monkeypatch):
        """With regime_routing_enabled=False, routing label must not change scores."""
        result_mom = self._score("momentum",      monkeypatch, enabled=False)
        result_rev = self._score("mean_reversion", monkeypatch, enabled=False)
        assert result_mom["score"] == result_rev["score"]
        assert result_mom["score_breakdown"] == result_rev["score_breakdown"]

    def test_regime_router_key_in_return_dict(self, monkeypatch):
        """compute_confluence must echo the regime_router back in its return value."""
        result = self._score("mean_reversion", monkeypatch)
        assert result.get("regime_router") == "mean_reversion"

    def test_score_still_capped_at_50(self, monkeypatch):
        """1.3× multiplier on all momentum dims must not push score above 50."""
        result = self._score("momentum", monkeypatch)
        assert result["score"] <= 50


# ── 4. PANIC/momentum inconsistency and state distribution ───────────────────

class TestPanicMomentumInconsistency:
    """
    Documents the interaction gap between the 2-state VIX router and the
    5-state regime classifier, and validates multiplier math properties.
    """

    def test_momentum_fallback_bias_on_vix_failure(self, monkeypatch):
        """
        When VIX fetch fails, get_market_regime_vix() ALWAYS returns 'momentum',
        never 'mean_reversion'. Documents the asymmetric fallback bias.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 20)
        with patch.object(_signals_mod, "_safe_download",
                          side_effect=Exception("timeout")):
            result = get_market_regime_vix()

        assert result["regime"] == "momentum", (
            "Fallback bias regression: VIX fetch failure must return 'momentum' "
            "(documents asymmetric bias)"
        )
        assert result["source"] == "fallback"
        assert result["vix"] is None

    def test_vix_boundary_at_default_threshold(self, monkeypatch):
        """
        VIX < 20 → 'momentum', VIX >= 20 → 'mean_reversion' at default threshold=20.
        Documents the distribution imbalance (typical calm-market VIX 12-18 is
        always 'momentum').
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_vix_threshold", 20)

        calm_vix_values = [12.0, 14.5, 16.0, 18.0, 19.9]
        high_vix_values = [20.0, 22.0, 25.0, 30.0, 45.0]

        for vix in calm_vix_values:
            with patch.object(_signals_mod, "_safe_download",
                              return_value=_vix_df(vix)), \
                 patch.object(_signals_mod, "_flatten_columns",
                              side_effect=lambda df: df):
                result = get_market_regime_vix()
            assert result["regime"] == "momentum", (
                f"VIX={vix} should be 'momentum' with threshold=20"
            )

        for vix in high_vix_values:
            with patch.object(_signals_mod, "_safe_download",
                              return_value=_vix_df(vix)), \
                 patch.object(_signals_mod, "_flatten_columns",
                              side_effect=lambda df: df):
                result = get_market_regime_vix()
            assert result["regime"] == "mean_reversion", (
                f"VIX={vix} should be 'mean_reversion' with threshold=20"
            )

    def test_regime_multipliers_cover_all_nine_dimensions(self, monkeypatch):
        """
        _regime_multipliers() must return all 9 dimension keys with positive
        values for both routing regimes and the unknown fallback. A missing
        key would cause a KeyError in compute_confluence.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        _all_dims = {"trend", "momentum", "squeeze", "flow", "breakout",
                     "mtf", "news", "social", "reversion"}

        for regime in ("momentum", "mean_reversion", "unknown"):
            mults = _regime_multipliers(regime)
            assert set(mults.keys()) == _all_dims, (
                f"Regime '{regime}' missing dimensions: "
                f"{_all_dims - set(mults.keys())}"
            )
            for dim, val in mults.items():
                assert val > 0, (
                    f"Multiplier for '{dim}' in '{regime}' must be positive, got {val}"
                )

    def test_trend_effective_weight_exceeds_reversion_in_momentum_regime(self, monkeypatch):
        """
        In 'momentum' regime with equal IC weights (1/9 each):
        effective_trend (1.3/9) must exceed effective_reversion (0.7/9).
        Documents that combined suppression ordering is correct.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)

        mults = _regime_multipliers("momentum")
        ic_weight = 1.0 / 9  # equal weight for all dims

        effective_trend     = ic_weight * mults["trend"]
        effective_reversion = ic_weight * mults["reversion"]

        assert effective_trend > effective_reversion, (
            f"trend ({effective_trend:.4f}) should > reversion ({effective_reversion:.4f}) "
            "in momentum regime"
        )
