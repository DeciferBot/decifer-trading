"""Tests for signals.get_regime_threshold().

Covers:
- BULL_TRENDING uses base threshold unchanged
- BEAR_TRENDING applies negative offset with floor
- CHOPPY applies larger negative offset with floor
- PANIC returns configured panic value (blocks all trades)
- UNKNOWN treated same as BEAR_TRENDING
- Offsets and floors are config-driven, not hardcoded
- Unknown regime name falls back to base
"""
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


class TestRegimeThresholdDefaults:
    """Default config values (as shipped in config.py)."""

    def test_bull_trending_uses_base(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        assert get_regime_threshold("BULL_TRENDING") == 20

    def test_bear_trending_applies_offset(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_offset", -3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_min", 15)
        assert get_regime_threshold("BEAR_TRENDING") == 17  # max(15, 20-3)

    def test_choppy_applies_offset(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_choppy_offset", -6)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_choppy_min", 12)
        assert get_regime_threshold("CHOPPY") == 14  # max(12, 20-6)

    def test_panic_blocks_all(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_panic", 99)
        assert get_regime_threshold("PANIC") == 99

    def test_unknown_treated_like_bear(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_offset", -3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_min", 15)
        assert get_regime_threshold("UNKNOWN") == get_regime_threshold("BEAR_TRENDING")

    def test_unrecognised_regime_falls_back_to_base(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 22)
        assert get_regime_threshold("SIDEWAYS_MOON") == 22


class TestRegimeThresholdConfigurable:
    """Offsets and floors drive the threshold — not hardcoded magic numbers."""

    def test_bear_offset_configurable(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_offset", -5)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_min", 10)
        assert get_regime_threshold("BEAR_TRENDING") == 15  # max(10, 20-5)

    def test_choppy_offset_configurable(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 20)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_choppy_offset", -10)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_choppy_min", 8)
        assert get_regime_threshold("CHOPPY") == 10  # max(8, 20-10)

    def test_panic_threshold_configurable(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_panic", 50)
        assert get_regime_threshold("PANIC") == 50

    def test_bear_floor_respected(self, monkeypatch):
        """When base - offset goes below the floor, floor wins."""
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 16)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_offset", -3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_min", 15)
        # 16 - 3 = 13, but floor is 15 → should return 15
        assert get_regime_threshold("BEAR_TRENDING") == 15

    def test_choppy_floor_respected(self, monkeypatch):
        """When base - offset goes below the floor, floor wins."""
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 16)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_choppy_offset", -6)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_choppy_min", 12)
        # 16 - 6 = 10, but floor is 12 → should return 12
        assert get_regime_threshold("CHOPPY") == 12

    def test_zero_offset_leaves_threshold_unchanged(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 25)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_offset", 0)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_min", 0)
        assert get_regime_threshold("BEAR_TRENDING") == 25

    def test_scales_with_paper_vs_live_base(self, monkeypatch):
        """Paper (base=18) and live (base=28) both scale correctly."""
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_offset", -3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_threshold_bear_min", 15)

        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 18)  # paper
        paper_threshold = get_regime_threshold("BEAR_TRENDING")

        monkeypatch.setitem(_config_mod.CONFIG, "min_score_to_trade", 28)  # live
        live_threshold = get_regime_threshold("BEAR_TRENDING")

        assert live_threshold > paper_threshold
        assert live_threshold == 25   # max(15, 28-3)
        assert paper_threshold == 15  # max(15, 18-3)
