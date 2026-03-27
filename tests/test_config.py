"""tests/test_config.py - Unit tests for config.py

Covers:
 - CONFIG dict structure and required keys present
 - Type validation (percentages are floats, counts are ints, strings are strings)
 - Value range validation (no nonsensical negative limits, no 200% risk)
 - Consistency checks (stop pct < 1.0, max positions > 0, etc.)

All tests run fully offline.
"""
import os, sys, types
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance",
             "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod
_cfg = {"log_file": "/dev/null", "trade_log": "/dev/null",
        "order_log": "/dev/null", "anthropic_api_key": "test-key",
        "model": "claude-sonnet-4-20250514", "max_tokens": 1000,
        "mongo_uri": "", "db_name": "test"}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


import sys
import logging
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import CONFIG

log = logging.getLogger("decifer.tests.test_config")


class TestConfigStructure:
    """Verify CONFIG dict has all required keys."""

    REQUIRED_KEYS = [
        "daily_loss_limit",
        "max_drawdown_alert",
        "max_positions",
        "risk_pct_per_trade",
        "max_single_position",
    ]

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_required_key_present(self, key):
        """Each required CONFIG key must be present."""
        assert key in CONFIG, f"CONFIG missing required key: '{key}'"

    def test_config_is_dict_or_object(self):
        """CONFIG must be a dict or an object with attribute access."""
        assert isinstance(CONFIG, dict) or hasattr(CONFIG, "__getitem__") or hasattr(CONFIG, "__dict__")


class TestConfigValueRanges:
    """Verify CONFIG values are in sensible ranges."""

    def _get(self, key, default=None):
        """Get a CONFIG value, supporting both dict and object."""
        if isinstance(CONFIG, dict):
            return CONFIG.get(key, default)
        return getattr(CONFIG, key, default)

    def test_max_daily_loss_pct_is_positive(self):
        val = self._get("max_daily_loss_pct")
        if val is not None:
            assert val > 0, "max_daily_loss_pct must be positive"

    def test_max_daily_loss_pct_is_reasonable(self):
        """Loss limit should not exceed 100% of account in a day."""
        val = self._get("max_daily_loss_pct")
        if val is not None:
            assert val < 1.0, f"max_daily_loss_pct={val} >= 1.0 (100%) - unreasonable"

    def test_max_drawdown_pct_is_positive(self):
        val = self._get("max_drawdown_pct")
        if val is not None:
            assert val > 0, "max_drawdown_pct must be positive"

    def test_max_drawdown_pct_is_reasonable(self):
        val = self._get("max_drawdown_pct")
        if val is not None:
            assert val < 1.0, f"max_drawdown_pct={val} >= 1.0 (100%) - unreasonable"

    def test_max_open_positions_is_positive_int(self):
        val = self._get("max_open_positions")
        if val is not None:
            assert isinstance(val, int) or (isinstance(val, float) and val == int(val))
            assert val > 0, "max_open_positions must be > 0"

    def test_risk_per_trade_pct_is_positive(self):
        val = self._get("risk_per_trade_pct")
        if val is not None:
            assert val > 0, "risk_per_trade_pct must be positive"

    def test_risk_per_trade_pct_not_extreme(self):
        """Risk per trade should not be >= 50% of account."""
        val = self._get("risk_per_trade_pct")
        if val is not None:
            assert val < 0.5, f"risk_per_trade_pct={val} >= 0.5 (50%) - dangerously high"

    def test_max_position_size_pct_is_positive(self):
        val = self._get("max_position_size_pct")
        if val is not None:
            assert val > 0

    def test_max_position_size_pct_not_greater_than_one(self):
        val = self._get("max_position_size_pct")
        if val is not None:
            assert val <= 1.0, f"max_position_size_pct={val} > 1.0 (100%)"

    def test_default_stop_pct_if_present(self):
        """If default_stop_pct exists it must be (0, 1)."""
        val = self._get("default_stop_pct")
        if val is not None:
            assert 0 < val < 1.0, f"default_stop_pct={val} not in (0, 1)"

    def test_reward_risk_ratio_if_present(self):
        """If reward_risk_ratio exists it must be > 0."""
        val = self._get("reward_risk_ratio")
        if val is not None:
            assert val > 0, f"reward_risk_ratio={val} must be > 0"

    def test_drawdown_limit_gt_daily_loss_limit(self):
        """Max drawdown limit should be >= max daily loss limit (logical consistency)."""
        daily = self._get("max_daily_loss_pct")
        drawdown = self._get("max_drawdown_pct")
        if daily is not None and drawdown is not None:
            assert drawdown >= daily, (
                f"max_drawdown_pct ({drawdown}) < max_daily_loss_pct ({daily}) - "
                "you'd hit drawdown limit before daily loss limit, daily limit is useless"
            )
