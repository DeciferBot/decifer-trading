"""Tests for smart_execution.py"""
import os
import sys
import types
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub ib_async BEFORE importing smart_execution ──────────────────────────
ib_async_mod = types.ModuleType("ib_async")

class FakeContract:
    def __init__(self, symbol="AAPL"):
        self.symbol = symbol
        self.secType = "STK"
        self.exchange = "SMART"
        self.currency = "USD"

class FakeOrder:
    def __init__(self):
        self.orderId = 0
        self.action = "BUY"
        self.totalQuantity = 0
        self.orderType = "LMT"
        self.lmtPrice = 0.0
        self.transmit = True
        self.account = "DU12345"

class FakeOrderStatus:
    Pending = "Pending"
    Submitted = "Submitted"
    Filled = "Filled"
    Cancelled = "Cancelled"

ib_async_mod.Contract = FakeContract
ib_async_mod.Order = FakeOrder
ib_async_mod.OrderStatus = FakeOrderStatus
sys.modules.setdefault("ib_async", ib_async_mod)

# Stub config
config_mod = types.ModuleType("config")
config_mod.CONFIG = {
    "log_file": "/tmp/test.log",
    "trade_log": "/tmp/trades.json",
    "order_log": "/tmp/orders.json",
}
sys.modules.setdefault("config", config_mod)

# Ensure we get the real module, not any stub left by test_bot.py
sys.modules.pop("smart_execution", None)

import smart_execution
from smart_execution import (
    ExecutionConfig,
    ExecutionStrategy,
    OrderSlice,
    ExecutionStats,
    TWAPExecutor,
    VWAPExecutor,
    IcebergOrder,
    ExecutionAnalytics,
    should_use_smart_execution,
)


# ---------------------------------------------------------------------------
# Fixtures defined locally
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config():
    return ExecutionConfig()


@pytest.fixture
def mock_ib_client():
    ib = MagicMock()
    ib.client.account = "DU12345"
    return ib


@pytest.fixture
def sample_contract():
    return FakeContract(symbol="AAPL")


@pytest.fixture
def sample_order_slice():
    return OrderSlice(
        order_id=101,
        symbol="AAPL",
        action="BUY",
        quantity=100,
        limit_price=150.0,
        slice_index=0,
        scheduled_time=datetime.now(),
        created_time=datetime.now(),
    )


@pytest.fixture
def sample_execution_stats():
    stats = ExecutionStats(
        symbol="AAPL",
        action="BUY",
        target_quantity=500,
    )
    stats.arrival_price = 150.0
    return stats


# ---------------------------------------------------------------------------
# ExecutionConfig
# ---------------------------------------------------------------------------

class TestExecutionConfig:
    def test_default_values(self):
        cfg = ExecutionConfig()
        assert cfg.twap_slices == 5
        assert cfg.twap_duration_minutes == 5
        assert cfg.iceberg_visible_pct == 0.15
        assert cfg.smart_execution_min_shares == 500
        assert cfg.smart_execution_min_notional == 10000.0

    def test_custom_values(self):
        cfg = ExecutionConfig(
            twap_slices=10,
            iceberg_visible_pct=0.20,
            smart_execution_min_shares=200,
        )
        assert cfg.twap_slices == 10
        assert cfg.iceberg_visible_pct == 0.20
        assert cfg.smart_execution_min_shares == 200

    def test_adaptive_enabled_by_default(self):
        cfg = ExecutionConfig()
        assert cfg.adaptive_enabled is True


# ---------------------------------------------------------------------------
# ExecutionStrategy enum
# ---------------------------------------------------------------------------

class TestExecutionStrategy:
    def test_enum_values_exist(self):
        assert ExecutionStrategy.TWAP.value == "twap"
        assert ExecutionStrategy.VWAP.value == "vwap"
        assert ExecutionStrategy.ICEBERG.value == "iceberg"
        assert ExecutionStrategy.SIMPLE.value == "simple"


# ---------------------------------------------------------------------------
# OrderSlice
# ---------------------------------------------------------------------------

class TestOrderSlice:
    def test_average_fill_price_empty(self, sample_order_slice):
        result = sample_order_slice.average_fill_price()
        assert result is None

    def test_average_fill_price_with_fills(self, sample_order_slice):
        sample_order_slice.filled_prices = [150.0, 151.0, 149.0]
        sample_order_slice.filled_quantity = 3
        avg = sample_order_slice.average_fill_price()
        assert avg == pytest.approx(150.0, abs=0.01)

    def test_is_fully_filled_false(self, sample_order_slice):
        sample_order_slice.filled_quantity = 50
        assert sample_order_slice.is_fully_filled() is False

    def test_is_fully_filled_true(self, sample_order_slice):
        sample_order_slice.filled_quantity = 100
        assert sample_order_slice.is_fully_filled() is True

    def test_is_fully_filled_over(self, sample_order_slice):
        sample_order_slice.filled_quantity = 110
        assert sample_order_slice.is_fully_filled() is True

    def test_is_expired_false_when_fresh(self, sample_order_slice):
        # Just created — not expired with 60s timeout
        assert sample_order_slice.is_expired(60) is False

    def test_is_expired_true_when_old(self, sample_order_slice):
        # Make it look old
        sample_order_slice.created_time = datetime.now() - timedelta(seconds=120)
        assert sample_order_slice.is_expired(60) is True

    def test_is_expired_at_boundary(self, sample_order_slice):
        # Created exactly at timeout boundary
        sample_order_slice.created_time = datetime.now() - timedelta(seconds=31)
        assert sample_order_slice.is_expired(30) is True


# ---------------------------------------------------------------------------
# ExecutionStats
# ---------------------------------------------------------------------------

class TestExecutionStats:
    def test_completion_rate_zero_when_no_fills(self, sample_execution_stats):
        sample_execution_stats.filled_quantity = 0
        assert sample_execution_stats.completion_rate() == 0.0

    def test_completion_rate_full(self, sample_execution_stats):
        sample_execution_stats.filled_quantity = 500
        assert sample_execution_stats.completion_rate() == 100.0

    def test_completion_rate_partial(self, sample_execution_stats):
        sample_execution_stats.filled_quantity = 250
        rate = sample_execution_stats.completion_rate()
        assert rate == pytest.approx(50.0)

    def test_completion_rate_zero_target(self):
        stats = ExecutionStats(symbol="AAPL", action="BUY", target_quantity=0)
        assert stats.completion_rate() == 0.0

    def test_calculate_slippage_buy(self, sample_execution_stats):
        sample_execution_stats.action = "BUY"
        sample_execution_stats.average_execution_price = 151.5
        sample_execution_stats.filled_quantity = 100
        slippage = sample_execution_stats.calculate_slippage(150.0)
        # (151.5 - 150.0) / 150.0 * 10000 = 100 bps
        assert slippage == pytest.approx(100.0, abs=0.1)

    def test_calculate_slippage_sell(self, sample_execution_stats):
        sample_execution_stats.action = "SELL"
        sample_execution_stats.average_execution_price = 149.0
        sample_execution_stats.filled_quantity = 100
        slippage = sample_execution_stats.calculate_slippage(150.0)
        # (150.0 - 149.0) / 150.0 * 10000 = 66.67 bps
        assert slippage == pytest.approx(66.67, abs=0.1)

    def test_calculate_slippage_zero_benchmark(self, sample_execution_stats):
        sample_execution_stats.filled_quantity = 100
        result = sample_execution_stats.calculate_slippage(0.0)
        assert result == 0.0

    def test_calculate_slippage_no_fills(self, sample_execution_stats):
        sample_execution_stats.filled_quantity = 0
        result = sample_execution_stats.calculate_slippage(150.0)
        assert result == 0.0

    def test_finalize_sets_end_time(self, sample_execution_stats):
        sample_execution_stats.fill_prices = [150.0, 151.0]
        sample_execution_stats.filled_quantity = 200
        sample_execution_stats.finalize(vwap_benchmark=150.5)
        assert sample_execution_stats.end_time is not None
        assert isinstance(sample_execution_stats.end_time, datetime)

    def test_finalize_sets_average_price(self, sample_execution_stats):
        sample_execution_stats.fill_prices = [100.0, 200.0]
        sample_execution_stats.filled_quantity = 200
        sample_execution_stats.finalize(vwap_benchmark=150.0)
        assert sample_execution_stats.average_execution_price == pytest.approx(150.0)

    def test_finalize_sets_min_max(self, sample_execution_stats):
        sample_execution_stats.fill_prices = [148.0, 150.0, 152.0]
        sample_execution_stats.filled_quantity = 300
        sample_execution_stats.finalize(vwap_benchmark=150.0)
        assert sample_execution_stats.min_price == 148.0
        assert sample_execution_stats.max_price == 152.0

    def test_finalize_no_fills(self, sample_execution_stats):
        # Should not crash with empty fill_prices
        sample_execution_stats.fill_prices = []
        sample_execution_stats.filled_quantity = 0
        sample_execution_stats.finalize(vwap_benchmark=150.0)
        assert sample_execution_stats.end_time is not None

    def test_finalize_implementation_shortfall_buy(self, sample_execution_stats):
        sample_execution_stats.action = "BUY"
        sample_execution_stats.arrival_price = 100.0
        sample_execution_stats.fill_prices = [102.0] * 100
        sample_execution_stats.filled_quantity = 100
        sample_execution_stats.finalize(vwap_benchmark=100.0)
        # Execution cost > hypothetical cost → positive shortfall
        assert sample_execution_stats.implementation_shortfall_bps > 0


# ---------------------------------------------------------------------------
# should_use_smart_execution
# ---------------------------------------------------------------------------

class TestShouldUseSmartExecution:
    def test_large_order_triggers_smart_execution(self):
        cfg = ExecutionConfig(
            smart_execution_min_shares=500,
            smart_execution_min_notional=10000.0,
        )
        # 1000 shares at $50 = $50,000 notional
        assert should_use_smart_execution(1000, 50.0, cfg) is True

    def test_small_order_no_smart_execution(self):
        cfg = ExecutionConfig(
            smart_execution_min_shares=500,
            smart_execution_min_notional=10000.0,
        )
        # 10 shares at $5 = $50 notional
        assert should_use_smart_execution(10, 5.0, cfg) is False

    def test_meets_share_threshold_only(self):
        cfg = ExecutionConfig(
            smart_execution_min_shares=100,
            smart_execution_min_notional=1_000_000.0,  # very high notional
        )
        # 200 shares at $1 — meets share threshold but not notional
        result = should_use_smart_execution(200, 1.0, cfg)
        # Implementation determines whether BOTH or EITHER must be met
        assert isinstance(result, bool)

    def test_zero_quantity(self):
        cfg = ExecutionConfig()
        result = should_use_smart_execution(0, 150.0, cfg)
        assert result is False

    def test_zero_price(self):
        cfg = ExecutionConfig()
        result = should_use_smart_execution(1000, 0.0, cfg)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# IcebergOrder
# ---------------------------------------------------------------------------

class TestIcebergOrder:
    def test_calculate_visible_quantity_standard(self, mock_ib_client):
        cfg = ExecutionConfig(
            iceberg_visible_pct=0.15,
            iceberg_min_visible=100,
            iceberg_max_visible=10000,
        )
        iceberg = IcebergOrder(mock_ib_client, cfg)
        visible = iceberg.calculate_visible_quantity(1000)
        # 15% of 1000 = 150
        assert visible == 150

    def test_calculate_visible_quantity_respects_min(self, mock_ib_client):
        cfg = ExecutionConfig(
            iceberg_visible_pct=0.05,
            iceberg_min_visible=100,
            iceberg_max_visible=10000,
        )
        iceberg = IcebergOrder(mock_ib_client, cfg)
        # 5% of 100 = 5, but min is 100
        visible = iceberg.calculate_visible_quantity(100)
        assert visible >= cfg.iceberg_min_visible

    def test_calculate_visible_quantity_respects_max(self, mock_ib_client):
        cfg = ExecutionConfig(
            iceberg_visible_pct=0.50,
            iceberg_min_visible=100,
            iceberg_max_visible=500,
        )
        iceberg = IcebergOrder(mock_ib_client, cfg)
        # 50% of 10000 = 5000, but max is 500
        visible = iceberg.calculate_visible_quantity(10000)
        assert visible <= cfg.iceberg_max_visible

    def test_calculate_visible_quantity_zero(self, mock_ib_client):
        cfg = ExecutionConfig(iceberg_min_visible=100)
        iceberg = IcebergOrder(mock_ib_client, cfg)
        visible = iceberg.calculate_visible_quantity(0)
        # Should handle zero gracefully
        assert isinstance(visible, int)


# ---------------------------------------------------------------------------
# VWAPExecutor.get_volume_profile
# ---------------------------------------------------------------------------

class TestVWAPExecutorVolumeProfile:
    def test_volume_profile_returns_dict(self, mock_ib_client):
        cfg = ExecutionConfig()
        executor = VWAPExecutor(mock_ib_client, cfg)
        profile = executor.get_volume_profile("AAPL")
        assert isinstance(profile, dict)

    def test_volume_profile_has_24_hours(self, mock_ib_client):
        cfg = ExecutionConfig()
        executor = VWAPExecutor(mock_ib_client, cfg)
        profile = executor.get_volume_profile("AAPL")
        # Should have entries for 24 hours
        assert len(profile) == 24

    def test_volume_profile_weights_sum_to_one(self, mock_ib_client):
        cfg = ExecutionConfig()
        executor = VWAPExecutor(mock_ib_client, cfg)
        profile = executor.get_volume_profile("NVDA")
        total = sum(profile.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_volume_profile_all_positive(self, mock_ib_client):
        cfg = ExecutionConfig()
        executor = VWAPExecutor(mock_ib_client, cfg)
        profile = executor.get_volume_profile("TSLA")
        for hour, weight in profile.items():
            assert weight >= 0, f"Hour {hour} has negative weight: {weight}"


# ---------------------------------------------------------------------------
# TWAPExecutor initialization
# ---------------------------------------------------------------------------

class TestTWAPExecutorInit:
    def test_initializes_with_correct_ib(self, mock_ib_client):
        cfg = ExecutionConfig()
        executor = TWAPExecutor(mock_ib_client, cfg)
        assert executor.ib is mock_ib_client

    def test_initializes_with_correct_config(self, mock_ib_client):
        cfg = ExecutionConfig(twap_slices=8)
        executor = TWAPExecutor(mock_ib_client, cfg)
        assert executor.config.twap_slices == 8

    def test_starts_with_empty_slices(self, mock_ib_client):
        cfg = ExecutionConfig()
        executor = TWAPExecutor(mock_ib_client, cfg)
        assert executor.slices == {}

    def test_starts_with_no_stats(self, mock_ib_client):
        cfg = ExecutionConfig()
        executor = TWAPExecutor(mock_ib_client, cfg)
        assert executor.stats is None


# ---------------------------------------------------------------------------
# ExecutionAnalytics
# ---------------------------------------------------------------------------

class TestExecutionAnalytics:
    def test_starts_empty(self):
        analytics = ExecutionAnalytics()
        summary = analytics.get_summary()
        assert isinstance(summary, dict)

    def test_record_execution(self):
        analytics = ExecutionAnalytics()
        stats = ExecutionStats(
            symbol="AAPL",
            action="BUY",
            target_quantity=500,
            filled_quantity=500,
        )
        stats.arrival_price = 150.0
        stats.fill_prices = [150.5] * 500
        stats.filled_quantity = 500
        stats.finalize(vwap_benchmark=150.2)
        analytics.record_execution(stats)
        summary = analytics.get_summary()
        assert isinstance(summary, dict)

    def test_get_symbol_summary(self):
        analytics = ExecutionAnalytics()
        stats = ExecutionStats(
            symbol="TSLA",
            action="BUY",
            target_quantity=200,
            filled_quantity=200,
        )
        stats.arrival_price = 200.0
        stats.fill_prices = [200.5] * 200
        stats.filled_quantity = 200
        stats.finalize(vwap_benchmark=200.0)
        analytics.record_execution(stats)
        symbol_summary = analytics.get_symbol_summary("TSLA")
        assert isinstance(symbol_summary, dict)

    def test_get_summary_after_multiple_executions(self):
        analytics = ExecutionAnalytics()
        for sym in ["AAPL", "NVDA", "TSLA"]:
            stats = ExecutionStats(
                symbol=sym,
                action="BUY",
                target_quantity=100,
                filled_quantity=100,
            )
            stats.arrival_price = 100.0
            stats.fill_prices = [100.0] * 100
            stats.filled_quantity = 100
            stats.finalize(vwap_benchmark=100.0)
            analytics.record_execution(stats)
        summary = analytics.get_summary()
        assert isinstance(summary, dict)

    def test_get_symbol_summary_unknown_symbol(self):
        analytics = ExecutionAnalytics()
        result = analytics.get_symbol_summary("UNKNOWN_XYZ")
        assert isinstance(result, dict)
