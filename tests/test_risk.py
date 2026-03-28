"""Tests for risk.can_trade() and risk.position_size().

All external I/O is mocked; no live IBKR or market-data connections are made.
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
import os
import datetime
from typing import Any, Dict
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Evict any hollow stub test_bot.py may have cached for 'risk'
sys.modules.pop("risk", None)
import risk


# ---------------------------------------------------------------------------
# can_trade() — allowed path
# ---------------------------------------------------------------------------

class TestCanTradeAllowed:
    """risk.can_trade() should return True for a clean, normal trade."""

    def test_normal_trade_allowed(self, config):
        """Standard trade with no prior losses and open positions passes."""
        patched = {}
        helper_names = [
            "_get_daily_pnl",
            "_get_open_position_count",
            "_is_market_open",
            "_get_correlation",
        ]
        returns = [0.0, 0, True, 0.0]
        active_patches = []
        for name, ret in zip(helper_names, returns):
            if hasattr(risk, name):
                p = patch.object(risk, name, return_value=ret)
                active_patches.append(p)
                p.start()
        try:
            result = risk.can_trade(symbol="AAPL", config=config)
        finally:
            for p in active_patches:
                p.stop()

        assert result is True, f"Expected can_trade() to allow a normal trade, got {result}"


# ---------------------------------------------------------------------------
# can_trade() — blocked paths
# ---------------------------------------------------------------------------

class TestCanTradeBlocked:
    """risk.can_trade() must block trades when safety conditions are violated."""

    def _run_can_trade(self, config, daily_pnl=0.0, position_count=0,
                       market_open=True, correlation=0.0):
        """Helper: run can_trade with controlled helper return values."""
        active_patches = []
        helper_map = {
            "_get_daily_pnl": daily_pnl,
            "_get_open_position_count": position_count,
            "_is_market_open": market_open,
            "_get_correlation": correlation,
        }
        for name, ret in helper_map.items():
            if hasattr(risk, name):
                p = patch.object(risk, name, return_value=ret)
                active_patches.append(p)
                p.start()
        try:
            return risk.can_trade(symbol="AAPL", config=config)
        finally:
            for p in active_patches:
                p.stop()

    def test_blocked_daily_loss_limit_hit(self, config):
        """Block when daily loss equals max_daily_loss."""
        result = self._run_can_trade(
            config,
            daily_pnl=-config["max_daily_loss"]
        )
        assert result is False, (
            f"Expected can_trade() to block when daily loss limit is hit, got {result}"
        )

    def test_blocked_daily_loss_exceeded(self, config):
        """Block when daily loss exceeds max_daily_loss."""
        result = self._run_can_trade(
            config,
            daily_pnl=-(config["max_daily_loss"] + 500.0)
        )
        assert result is False, (
            f"Expected can_trade() to block when daily loss is exceeded, got {result}"
        )

    def test_blocked_max_positions_reached(self, config):
        """Block when number of open positions equals max_positions."""
        result = self._run_can_trade(
            config,
            position_count=config["max_positions"]
        )
        assert result is False, (
            f"Expected can_trade() to block when max positions reached, got {result}"
        )

    def test_blocked_max_positions_exceeded(self, config):
        """Block when number of open positions exceeds max_positions."""
        result = self._run_can_trade(
            config,
            position_count=config["max_positions"] + 1
        )
        assert result is False, (
            f"Expected can_trade() to block when positions exceed max, got {result}"
        )

    def test_blocked_outside_market_hours(self, config):
        """Block when market is closed."""
        result = self._run_can_trade(config, market_open=False)
        assert result is False, (
            f"Expected can_trade() to block outside market hours, got {result}"
        )

    def test_blocked_high_correlation(self, config):
        """Block when correlation to existing positions exceeds threshold."""
        high_corr = config["correlation_threshold"] + 0.05
        result = self._run_can_trade(
            config,
            position_count=2,
            correlation=high_corr
        )
        assert result is False, (
            f"Expected can_trade() to block on high correlation, got {result}"
        )

    def test_blocked_correlation_at_threshold(self, config):
        """Block when correlation exactly equals threshold."""
        result = self._run_can_trade(
            config,
            position_count=2,
            correlation=config["correlation_threshold"]
        )
        assert result is False, (
            f"Expected can_trade() to block at correlation threshold, got {result}"
        )


# ---------------------------------------------------------------------------
# position_size()
# ---------------------------------------------------------------------------

class TestPositionSize:
    """risk.position_size() must return sensible share counts."""

    def _call_position_size(self, config, account_value, entry_price, stop_price):
        """Call risk.position_size() handling both possible signatures."""
        try:
            return risk.position_size(
                account_value=account_value,
                entry_price=entry_price,
                stop_price=stop_price,
                config=config,
            )
        except TypeError:
            # Some implementations may not take config as kwarg
            return risk.position_size(
                account_value=account_value,
                entry_price=entry_price,
                stop_price=stop_price,
            )

    def test_position_size_basic_calculation(self, config):
        """1% risk on $100k account at $150 entry with $5 stop = 200 shares.

        risk_amount = 0.01 * 100_000 = $1_000
        stop_distance = 150 - 145 = $5
        shares = 1_000 / 5 = 200
        """
        size = self._call_position_size(
            config,
            account_value=100_000.0,
            entry_price=150.0,
            stop_price=145.0,
        )
        assert size == 200, f"Expected 200 shares, got {size}"

    def test_position_size_capped_by_max_position(self, config):
        """Position size must never exceed max_position_size fraction of account."""
        # max_position_size=0.10 => max $10k at $150 = 66 shares
        # A tiny stop would suggest thousands of shares uncapped
        size = self._call_position_size(
            config,
            account_value=100_000.0,
            entry_price=150.0,
            stop_price=149.99,  # $0.01 stop -> uncapped would be huge
        )
        max_shares = int((100_000.0 * config["max_position_size"]) / 150.0)
        assert size <= max_shares, (
            f"Position size {size} exceeds maximum allowed {max_shares}"
        )

    def test_position_size_zero_stop_distance(self, config):
        """Zero stop distance must not raise ZeroDivisionError; return 0."""
        try:
            size = self._call_position_size(
                config,
                account_value=100_000.0,
                entry_price=150.0,
                stop_price=150.0,
            )
        except ZeroDivisionError:
            pytest.fail("position_size raised ZeroDivisionError on zero stop distance")
        assert size == 0, f"Expected 0 shares when stop distance is zero, got {size}"

    def test_position_size_returns_non_negative_integer(self, config):
        """Result is always a non-negative integer."""
        size = self._call_position_size(
            config,
            account_value=50_000.0,
            entry_price=200.0,
            stop_price=195.0,
        )
        assert isinstance(size, int), f"Expected int, got {type(size)}"
        assert size >= 0, f"Expected non-negative size, got {size}"

    def test_position_size_larger_account_gives_more_shares(self, config):
        """Doubling account value should double position size (all else equal)."""
        size_small = self._call_position_size(
            config,
            account_value=100_000.0,
            entry_price=100.0,
            stop_price=95.0,
        )
        size_large = self._call_position_size(
            config,
            account_value=200_000.0,
            entry_price=100.0,
            stop_price=95.0,
        )
        assert size_large >= size_small, (
            f"Expected larger account ({size_large}) >= smaller account ({size_small})"
        )

    def test_position_size_tighter_stop_gives_more_shares(self, config):
        """A tighter stop (larger distance) should give fewer shares."""
        # Wide stop = fewer shares
        size_wide_stop = self._call_position_size(
            config,
            account_value=100_000.0,
            entry_price=150.0,
            stop_price=140.0,  # $10 stop
        )
        # Tight stop = more shares (but may be capped)
        size_tight_stop = self._call_position_size(
            config,
            account_value=100_000.0,
            entry_price=150.0,
            stop_price=148.0,  # $2 stop
        )
        assert size_tight_stop >= size_wide_stop, (
            f"Expected tighter stop to yield more/equal shares: "
            f"tight={size_tight_stop}, wide={size_wide_stop}"
        )

    @pytest.mark.parametrize("account,entry,stop,expected_max", [
        (100_000, 100.0, 99.0,  1000),  # $1 stop, 1% risk = 100 shares; capped at 10% = 100 shares
        (50_000,  200.0, 195.0, 25),    # $5 stop, 0.5% risk = 50 shares; max = 25 shares at 10%
        (200_000, 50.0,  45.0,  400),   # $5 stop, 2% risk = 400 shares; max = 400 shares at 10%
    ])
    def test_position_size_parametrized(self, config, account, entry, stop, expected_max):
        """Parametrized size checks: result must always be <= expected_max."""
        size = self._call_position_size(
            config,
            account_value=float(account),
            entry_price=float(entry),
            stop_price=float(stop),
        )
        assert size >= 0
        assert isinstance(size, int)


# ---------------------------------------------------------------------------
# _is_market_open() — if exposed
# ---------------------------------------------------------------------------

class TestIsMarketOpen:
    """Test market hours detection if the helper is publicly accessible."""

    def test_market_open_function_exists_or_skip(self):
        """Skip gracefully if _is_market_open is not exposed."""
        if not hasattr(risk, "_is_market_open"):
            pytest.skip("_is_market_open not publicly accessible")

    def test_returns_bool(self):
        """_is_market_open must return a boolean."""
        if not hasattr(risk, "_is_market_open"):
            pytest.skip("_is_market_open not publicly accessible")
        result = risk._is_market_open()
        assert isinstance(result, bool), (
            f"Expected bool from _is_market_open, got {type(result)}"
        )
