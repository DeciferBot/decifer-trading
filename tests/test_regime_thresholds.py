"""Tests for signals.get_regime_threshold().

New behaviour (post north-star regime-as-reasoning-input):
- All non-circuit-breaker regimes return the same base threshold.
  Quality filtering is the Opus reasoning layer's job — a uniform bar
  means Opus sees the same candidate quality regardless of regime label.
- PANIC and EXTREME_STRESS return the configured panic value (blocks all trades).
- Unknown regime names fall back to base.
"""
from __future__ import annotations
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config as _config_mod

# Remove stale stub if test_bot.py replaced signals with a bare stub
if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]

from signals import get_regime_threshold


class TestRegimeThresholdFlat:
    """All non-circuit-breaker regimes return base — no regime-specific offsets."""

    def test_trending_up_uses_base(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        assert get_regime_threshold("TRENDING_UP") == 20

    def test_trending_down_uses_base(self, monkeypatch):
        """TRENDING_DOWN no longer gets a lowered threshold — Opus filters instead."""
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        assert get_regime_threshold("TRENDING_DOWN") == 20

    def test_range_bound_uses_base(self, monkeypatch):
        """RANGE_BOUND no longer gets a lowered threshold — Opus filters instead."""
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        assert get_regime_threshold("RANGE_BOUND") == 20

    def test_unknown_uses_base(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        assert get_regime_threshold("UNKNOWN") == 20

    def test_unrecognised_regime_falls_back_to_base(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 22)
        assert get_regime_threshold("SIDEWAYS_MOON") == 22

    def test_all_non_circuit_breaker_regimes_equal(self, monkeypatch):
        """Core invariant: every non-circuit-breaker regime returns the same threshold."""
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 18)
        base = 18
        for regime in ("TRENDING_UP", "TRENDING_DOWN", "RANGE_BOUND", "RELIEF_RALLY", "UNKNOWN",
                       "MOMENTUM_BULL", "FEAR_ELEVATED",
                       "DISTRIBUTION", "TRENDING_BEAR"):
            assert get_regime_threshold(regime) == base, \
                f"Expected {base} for {regime}, got {get_regime_threshold(regime)}"


class TestCircuitBreakerThreshold:
    """PANIC and EXTREME_STRESS block all mechanically-scored signals."""

    def test_capitulation_blocks_all(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_panic", 99)
        assert get_regime_threshold("CAPITULATION") == 99

    def test_extreme_stress_blocks_all(self, monkeypatch):
        """EXTREME_STRESS (new session_character label) also triggers the circuit breaker."""
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_panic", 99)
        assert get_regime_threshold("EXTREME_STRESS") == 99

    def test_capitulation_threshold_configurable(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_panic", 50)
        assert get_regime_threshold("CAPITULATION") == 50

    def test_base_changes_scale_all_non_circuit_breaker(self, monkeypatch):
        """Changing min_score_to_trade scales all non-panic thresholds together."""
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 14)
        assert get_regime_threshold("TRENDING_UP") == 14
        assert get_regime_threshold("RANGE_BOUND") == 14
        assert get_regime_threshold("FEAR_ELEVATED") == 14

        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 28)
        assert get_regime_threshold("TRENDING_UP") == 28
        assert get_regime_threshold("RANGE_BOUND") == 28
