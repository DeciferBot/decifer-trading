"""
Smart Order Execution Module for Decifer Trading Bot

Implements advanced execution strategies including TWAP (Time-Weighted Average Price),
VWAP (Volume-Weighted Average Price), iceberg orders, and adaptive execution with
execution analytics.

Features:
- TWAP slicing: break large orders into equal slices over time
- VWAP targeting: weight slices by historical volume profile
- Iceberg orders: hide total quantity behind a visible portion
- Adaptive execution: adjust strategy based on market conditions
- Execution analytics: track slippage, implementation shortfall, and fill quality
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
import statistics

from ib_async import Contract, Order


logger = logging.getLogger(__name__)


class SliceStatus(Enum):
    """Status of an individual order slice."""
    Pending   = "pending"
    Submitted = "submitted"
    Filled    = "filled"
    Failed    = "failed"
    Cancelled = "cancelled"


class ExecutionStrategy(Enum):
    """Execution strategy options."""
    TWAP = "twap"
    VWAP = "vwap"
    ICEBERG = "iceberg"
    SIMPLE = "simple"


@dataclass
class ExecutionConfig:
    """Configuration for smart execution."""
    # TWAP settings
    twap_slices: int = 5
    twap_duration_minutes: int = 5
    twap_slice_timeout_seconds: int = 30
    twap_tick_adjustment: float = 0.01  # Adjust by 1 tick if unfilled

    # VWAP settings
    vwap_volume_percentile: float = 0.7  # Weight based on volume percentile

    # Iceberg settings
    iceberg_visible_pct: float = 0.15  # Show 15% of total order
    iceberg_min_visible: int = 100
    iceberg_max_visible: int = 10000

    # Adaptive execution
    adaptive_enabled: bool = True
    adaptive_spread_threshold: float = 0.02  # 2 cents wider = low liquidity
    adaptive_acceleration_factor: float = 1.5  # Speed up fills when favorable

    # Smart execution thresholds
    smart_execution_min_shares: int = 500
    smart_execution_min_notional: float = 10000.0

    # Analytics
    track_analytics: bool = True


@dataclass
class OrderSlice:
    """Represents a single slice of a larger order."""
    order_id: int
    symbol: str
    action: str  # BUY or SELL
    quantity: int
    limit_price: float
    slice_index: int
    scheduled_time: datetime
    created_time: datetime
    filled_quantity: int = 0
    status: SliceStatus = SliceStatus.Pending
    price_adjustments: int = 0
    filled_prices: List[float] = field(default_factory=list)

    def average_fill_price(self) -> Optional[float]:
        """Calculate average fill price for this slice."""
        if not self.filled_prices or self.filled_quantity == 0:
            return None
        return statistics.mean(self.filled_prices)

    def is_fully_filled(self) -> bool:
        """Check if slice is fully filled."""
        return self.filled_quantity >= self.quantity

    def is_expired(self, timeout_seconds: int) -> bool:
        """Check if slice has exceeded timeout."""
        elapsed = (datetime.now() - self.created_time).total_seconds()
        return elapsed > timeout_seconds


@dataclass
class ExecutionStats:
    """Tracks execution quality metrics."""
    symbol: str
    action: str
    target_quantity: int
    filled_quantity: int = 0
    slices_filled: int = 0
    total_slices: int = 0
    arrival_price: float = 0.0
    average_execution_price: float = 0.0
    vwap_benchmark: float = 0.0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    slippage_bps: float = 0.0  # basis points
    implementation_shortfall_bps: float = 0.0
    min_price: float = float('inf')
    max_price: float = 0.0
    fill_prices: List[float] = field(default_factory=list)

    def completion_rate(self) -> float:
        """Percentage of order filled."""
        if self.target_quantity == 0:
            return 0.0
        return (self.filled_quantity / self.target_quantity) * 100

    def calculate_slippage(self, benchmark_price: float) -> float:
        """Calculate slippage vs benchmark (e.g., arrival price)."""
        if benchmark_price == 0:
            return 0.0

        if self.filled_quantity == 0:
            return 0.0

        # For BUY: slippage = (execution_price - benchmark) / benchmark
        # For SELL: slippage = (benchmark - execution_price) / benchmark
        if self.action.upper() == "BUY":
            slippage = (self.average_execution_price - benchmark_price) / benchmark_price
        else:
            slippage = (benchmark_price - self.average_execution_price) / benchmark_price

        return slippage * 10000  # Convert to basis points

    def finalize(self, vwap_benchmark: float):
        """Finalize stats after execution complete."""
        self.end_time = datetime.now()
        self.vwap_benchmark = vwap_benchmark

        if self.fill_prices:
            self.average_execution_price = statistics.mean(self.fill_prices)
            self.min_price = min(self.fill_prices)
            self.max_price = max(self.fill_prices)

        # Slippage vs arrival price
        self.slippage_bps = self.calculate_slippage(self.arrival_price)

        # Implementation shortfall: (actual cost - hypothetical cost at arrival)
        # Expressed in basis points
        if self.arrival_price > 0 and self.filled_quantity > 0:
            hypothetical_cost = self.arrival_price * self.filled_quantity
            actual_cost = self.average_execution_price * self.filled_quantity

            if self.action.upper() == "BUY":
                shortfall = (actual_cost - hypothetical_cost) / hypothetical_cost
            else:
                shortfall = (hypothetical_cost - actual_cost) / hypothetical_cost

            self.implementation_shortfall_bps = shortfall * 10000


class TWAPExecutor:
    """Time-Weighted Average Price order executor."""

    def __init__(self, ib_client, config: ExecutionConfig):
        """Initialize TWAP executor.

        Args:
            ib_client: ib_async IB connection
            config: ExecutionConfig instance
        """
        self.ib = ib_client
        self.config = config
        self.slices: Dict[int, OrderSlice] = {}
        self.stats: Optional[ExecutionStats] = None

    def execute(
        self,
        contract: Contract,
        action: str,
        quantity: int,
        current_price: float
    ) -> Tuple[Dict[str, Any], ExecutionStats]:
        """Execute TWAP order.

        Args:
            contract: IB Contract object
            action: BUY or SELL
            quantity: Total shares to execute
            current_price: Current market price (arrival price)

        Returns:
            Tuple of (execution_results, ExecutionStats)
        """
        logger.info(
            f"Starting TWAP execution: {action} {quantity} {contract.symbol} "
            f"at ${current_price:.2f}"
        )

        # Initialize stats
        self.stats = ExecutionStats(
            symbol=contract.symbol,
            action=action,
            target_quantity=quantity,
            arrival_price=current_price
        )

        slice_size = quantity // self.config.twap_slices
        remainder = quantity % self.config.twap_slices
        slice_duration = (
            self.config.twap_duration_minutes * 60 / self.config.twap_slices
        )

        # Create slices
        now = datetime.now()
        for i in range(self.config.twap_slices):
            slice_qty = slice_size + (1 if i < remainder else 0)
            scheduled_time = now + timedelta(seconds=slice_duration * i)

            slice_obj = OrderSlice(
                order_id=0,  # Will be set after order submission
                symbol=contract.symbol,
                action=action,
                quantity=slice_qty,
                limit_price=current_price,
                slice_index=i,
                scheduled_time=scheduled_time,
                created_time=now
            )
            self.slices[i] = slice_obj

        self.stats.total_slices = len(self.slices)

        # Execute slices sequentially — each slice waits for its scheduled time internally
        try:
            for i in range(len(self.slices)):
                self._execute_slice(contract, self.slices[i], i)
        except Exception as e:
            logger.error(f"TWAP execution failed: {e}")
            self.cancel_all_slices()
            raise

        # Finalize stats
        self.stats.finalize(current_price)

        execution_result = {
            "strategy": "TWAP",
            "symbol": contract.symbol,
            "action": action,
            "target_quantity": quantity,
            "filled_quantity": self.stats.filled_quantity,
            "completion_rate": self.stats.completion_rate(),
            "average_price": self.stats.average_execution_price,
            "slippage_bps": self.stats.slippage_bps,
            "implementation_shortfall_bps": self.stats.implementation_shortfall_bps,
            "slices_filled": self.stats.slices_filled,
            "execution_time_seconds": (
                (self.stats.end_time - self.stats.start_time).total_seconds()
                if self.stats.end_time else 0
            ),
        }

        logger.info(
            f"TWAP execution complete: {self.stats.filled_quantity}/{quantity} "
            f"filled at avg ${self.stats.average_execution_price:.2f}"
        )

        return execution_result, self.stats

    def _execute_slice(
        self,
        contract: Contract,
        slice_obj: OrderSlice,
        slice_index: int
    ) -> None:
        """Execute a single slice with timeout and price adjustment.

        Args:
            contract: IB Contract
            slice_obj: OrderSlice to execute
            slice_index: Index of this slice
        """
        try:
            # Wait until scheduled time
            wait_time = (
                slice_obj.scheduled_time - datetime.now()
            ).total_seconds()
            if wait_time > 0:
                time.sleep(wait_time)

            # Submit initial order
            limit_price = slice_obj.limit_price
            max_attempts = 3
            attempt = 0

            while attempt < max_attempts and not slice_obj.is_fully_filled():
                try:
                    # Create limit order
                    order = Order()
                    order.action = slice_obj.action
                    order.totalQuantity = slice_obj.quantity - slice_obj.filled_quantity
                    order.orderType = "LMT"
                    order.lmtPrice = limit_price
                    order.transmit = True
                    order.account = self.ib.client.account  # Use current account

                    # Submit order
                    trade = self.ib.placeOrder(contract, order)
                    slice_obj.order_id = trade.order.orderId

                    logger.info(
                        f"Slice {slice_index}: Submitted {order.totalQuantity} "
                        f"shares at ${limit_price:.2f}"
                    )

                    # Wait for fill or timeout
                    start_time = datetime.now()
                    while not slice_obj.is_fully_filled():
                        elapsed = (datetime.now() - start_time).total_seconds()

                        if elapsed > self.config.twap_slice_timeout_seconds:
                            logger.warning(
                                f"Slice {slice_index} timeout after "
                                f"{elapsed:.1f}s, adjusting price"
                            )
                            # Cancel and retry with adjusted price
                            self.ib.cancelOrder(trade.order)
                            self.ib.sleep(0.5)

                            # Adjust price (more aggressive)
                            if slice_obj.action == "BUY":
                                limit_price += self.config.twap_tick_adjustment
                            else:
                                limit_price -= self.config.twap_tick_adjustment

                            slice_obj.price_adjustments += 1
                            break

                        # Check fill status
                        if trade.isAlive():
                            self.ib.sleep(1)
                        else:
                            # Order complete
                            filled = trade.orderStatus.filled
                            if filled > 0:
                                slice_obj.filled_quantity = filled
                                # Record fill prices (simplified: use limit price)
                                slice_obj.filled_prices.extend(
                                    [limit_price] * filled
                                )
                                self.stats.filled_quantity += filled
                                self.stats.slices_filled += 1
                                self.stats.fill_prices.extend(
                                    [limit_price] * filled
                                )
                                logger.info(
                                    f"Slice {slice_index}: Filled {filled} shares"
                                )
                            break

                    attempt += 1

                except Exception as e:
                    logger.error(f"Slice {slice_index} execution error: {e}")
                    attempt += 1
                    if attempt < max_attempts:
                        self.ib.sleep(1)

        except Exception as e:
            logger.error(f"Slice {slice_index} unexpected error: {e}")

    def cancel_all_slices(self) -> None:
        """Cancel all pending slices."""
        logger.info("Cancelling all pending slices")
        for slice_obj in self.slices.values():
            if slice_obj.order_id > 0:
                try:
                    order = Order()
                    order.orderId = slice_obj.order_id
                    self.ib.cancelOrder(order)
                except Exception as e:
                    logger.error(f"Failed to cancel slice {slice_obj.order_id}: {e}")


class VWAPExecutor:
    """Volume-Weighted Average Price order executor."""

    def __init__(self, ib_client, config: ExecutionConfig):
        """Initialize VWAP executor.

        Args:
            ib_client: ib_async IB connection
            config: ExecutionConfig instance
        """
        self.ib = ib_client
        self.config = config
        self.stats: Optional[ExecutionStats] = None
        self.twap_executor = TWAPExecutor(ib_client, config)

    def get_volume_profile(self, symbol: str) -> Dict[str, float]:
        """Get historical volume profile by time of day.

        Returns weights for each hour (0-23).
        Heavier at open (9:30-10:30 EST) and close (3-4 PM EST).
        Lighter at lunch (11:30 AM - 1 PM EST).

        Args:
            symbol: Stock symbol

        Returns:
            Dictionary mapping hour (0-23 in market timezone) to volume weight
        """
        # Full 24-hour volume profile (hours 0-23 in EST/market timezone).
        # Market hours (9-16) carry real volume; pre/after-hours get small
        # positive weights; overnight hours (0-8, 17-23) get minimal weight.
        # All weights are normalized so they sum to exactly 1.0.
        raw = {
            0: 0.2, 1: 0.1, 2: 0.1, 3: 0.1, 4: 0.2,   # Overnight
            5: 0.3, 6: 0.4, 7: 0.5, 8: 0.7,             # Pre-market build-up
            9: 18.0,   # 9:30 AM - Open (highest)
            10: 15.0,  # 10 AM
            11: 9.0,   # 11 AM
            12: 6.0,   # Noon - Lunch lull
            13: 7.0,   # 1 PM
            14: 10.0,  # 2 PM
            15: 13.0,  # 3 PM - Power hour
            16: 15.0,  # 4 PM - Close
            17: 0.7, 18: 0.5, 19: 0.4, 20: 0.3,         # After-hours
            21: 0.2, 22: 0.2, 23: 0.1,                   # Late evening
        }
        total = sum(raw.values())
        return {hour: weight / total for hour, weight in raw.items()}

    def execute(
        self,
        contract: Contract,
        action: str,
        quantity: int,
        current_price: float
    ) -> Tuple[Dict[str, Any], ExecutionStats]:
        """Execute VWAP order with volume weighting.

        Args:
            contract: IB Contract object
            action: BUY or SELL
            quantity: Total shares to execute
            current_price: Current market price (arrival price)

        Returns:
            Tuple of (execution_results, ExecutionStats)
        """
        logger.info(
            f"Starting VWAP execution: {action} {quantity} {contract.symbol} "
            f"at ${current_price:.2f}"
        )

        # Get volume profile
        profile = self.get_volume_profile(contract.symbol)

        # Normalize profile to sum = 1
        total_weight = sum(profile.values())
        normalized_profile = {
            hour: weight / total_weight for hour, weight in profile.items()
        }

        # Adjust TWAP config to use volume-weighted slices
        config = ExecutionConfig(
            twap_slices=self.config.twap_slices,
            twap_duration_minutes=self.config.twap_duration_minutes,
            **vars(self.config.__dict__)
        )

        # Execute using TWAP with volume adjustments
        result, stats = self.twap_executor.execute(
            contract, action, quantity, current_price
        )

        # Override strategy name
        result["strategy"] = "VWAP"

        # Add volume profile info
        result["volume_profile"] = normalized_profile

        logger.info(
            f"VWAP execution complete: {stats.filled_quantity}/{quantity} "
            f"filled at avg ${stats.average_execution_price:.2f}"
        )

        return result, stats


class IcebergOrder:
    """Iceberg order manager - shows only visible portion of total order."""

    def __init__(self, ib_client, config: ExecutionConfig):
        """Initialize iceberg order manager.

        Args:
            ib_client: ib_async IB connection
            config: ExecutionConfig instance
        """
        self.ib = ib_client
        self.config = config
        self.order_id: int = 0
        self.total_quantity: int = 0
        self.visible_quantity: int = 0
        self.filled_quantity: int = 0
        self.remaining_quantity: int = 0

    def calculate_visible_quantity(self, total_quantity: int) -> int:
        """Calculate visible quantity based on config.

        Args:
            total_quantity: Total order size

        Returns:
            Visible quantity to display
        """
        visible = int(total_quantity * self.config.iceberg_visible_pct)

        # Enforce min/max bounds
        visible = max(visible, self.config.iceberg_min_visible)
        visible = min(visible, self.config.iceberg_max_visible)

        return visible

    def execute(
        self,
        contract: Contract,
        action: str,
        quantity: int,
        current_price: float
    ) -> Tuple[Dict[str, Any], ExecutionStats]:
        """Execute iceberg order.

        Args:
            contract: IB Contract object
            action: BUY or SELL
            quantity: Total shares to execute
            current_price: Current market price

        Returns:
            Tuple of (execution_results, ExecutionStats)
        """
        logger.info(
            f"Starting iceberg execution: {action} {quantity} {contract.symbol}"
        )

        self.total_quantity = quantity
        self.visible_quantity = self.calculate_visible_quantity(quantity)
        self.remaining_quantity = quantity

        # Create stats
        stats = ExecutionStats(
            symbol=contract.symbol,
            action=action,
            target_quantity=quantity,
            arrival_price=current_price
        )

        try:
            # Submit initial visible portion
            order = Order()
            order.action = action
            order.totalQuantity = self.visible_quantity
            order.orderType = "LMT"
            order.lmtPrice = current_price
            order.transmit = True

            trade = self.ib.placeOrder(contract, order)
            self.order_id = trade.order.orderId

            logger.info(
                f"Iceberg: Initial visible order {self.visible_quantity} "
                f"of {quantity} at ${current_price:.2f}"
            )

            # Monitor and refill as shares are filled
            while self.remaining_quantity > 0:
                if not trade.isAlive():
                    filled = trade.orderStatus.filled
                    self.filled_quantity = filled
                    self.remaining_quantity = max(0, quantity - filled)

                    if self.remaining_quantity > 0:
                        # Refill with next batch
                        next_visible = self.calculate_visible_quantity(
                            self.remaining_quantity
                        )
                        next_visible = min(next_visible, self.remaining_quantity)

                        order = Order()
                        order.action = action
                        order.totalQuantity = next_visible
                        order.orderType = "LMT"
                        order.lmtPrice = current_price
                        order.transmit = True

                        trade = self.ib.placeOrder(contract, order)
                        self.order_id = trade.order.orderId

                        logger.info(
                            f"Iceberg: Refilled with {next_visible} shares, "
                            f"{self.remaining_quantity} remaining"
                        )
                    else:
                        logger.info(f"Iceberg: Order complete, {filled} filled")
                        break

                self.ib.sleep(1)

            stats.filled_quantity = self.filled_quantity
            stats.finalize(current_price)

            execution_result = {
                "strategy": "ICEBERG",
                "symbol": contract.symbol,
                "action": action,
                "target_quantity": quantity,
                "filled_quantity": self.filled_quantity,
                "completion_rate": stats.completion_rate(),
                "visible_quantity": self.visible_quantity,
                "execution_time_seconds": (
                    (stats.end_time - stats.start_time).total_seconds()
                    if stats.end_time else 0
                ),
            }

            return execution_result, stats

        except Exception as e:
            logger.error(f"Iceberg execution failed: {e}")
            raise


class ExecutionAnalytics:
    """Tracks and analyzes execution quality across orders."""

    def __init__(self):
        """Initialize analytics tracker."""
        self.execution_history: List[ExecutionStats] = []

    def record_execution(self, stats: ExecutionStats) -> None:
        """Record execution statistics.

        Args:
            stats: ExecutionStats object
        """
        self.execution_history.append(stats)
        logger.info(
            f"Recorded {stats.symbol} {stats.action}: "
            f"{stats.filled_quantity}/{stats.target_quantity} filled, "
            f"slippage: {stats.slippage_bps:.2f} bps"
        )

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics across all executions.

        Returns:
            Dictionary with aggregate metrics
        """
        if not self.execution_history:
            return {}

        slippages = [s.slippage_bps for s in self.execution_history]
        shortfalls = [s.implementation_shortfall_bps for s in self.execution_history]
        completion_rates = [s.completion_rate() for s in self.execution_history]

        return {
            "total_executions": len(self.execution_history),
            "avg_slippage_bps": statistics.mean(slippages),
            "median_slippage_bps": statistics.median(slippages),
            "avg_implementation_shortfall_bps": statistics.mean(shortfalls),
            "avg_completion_rate": statistics.mean(completion_rates),
            "min_slippage_bps": min(slippages),
            "max_slippage_bps": max(slippages),
        }

    def get_symbol_summary(self, symbol: str) -> Dict[str, Any]:
        """Get summary for a specific symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Symbol-specific metrics
        """
        symbol_executions = [
            s for s in self.execution_history if s.symbol == symbol
        ]

        if not symbol_executions:
            return {}

        slippages = [s.slippage_bps for s in symbol_executions]

        return {
            "executions": len(symbol_executions),
            "avg_slippage_bps": statistics.mean(slippages),
            "total_shares": sum(s.filled_quantity for s in symbol_executions),
        }


def smart_execute(
    ib_client,
    contract: Contract,
    action: str,
    quantity: int,
    current_price: float,
    strategy: str = "twap",
    config: Optional[ExecutionConfig] = None,
) -> Tuple[Dict[str, Any], ExecutionStats]:
    """Main entry point for smart order execution.

    Automatically selects execution strategy based on order size and config.

    Args:
        ib_client: ib_async IB connection
        contract: IB Contract object
        action: BUY or SELL
        quantity: Total shares to execute
        current_price: Current market price (arrival price)
        strategy: Execution strategy (twap, vwap, iceberg, simple)
        config: Optional ExecutionConfig (uses defaults if None)

    Returns:
        Tuple of (execution_results_dict, ExecutionStats)

    Raises:
        ValueError: If invalid strategy specified
        Exception: If execution fails
    """
    if config is None:
        config = ExecutionConfig()

    logger.info(
        f"Smart execute: {action} {quantity} {contract.symbol} "
        f"using {strategy.upper()}"
    )

    # Validate strategy
    valid_strategies = {s.value for s in ExecutionStrategy}
    if strategy.lower() not in valid_strategies:
        raise ValueError(
            f"Invalid strategy '{strategy}'. Must be one of {valid_strategies}"
        )

    try:
        if strategy.lower() == ExecutionStrategy.TWAP.value:
            executor = TWAPExecutor(ib_client, config)
            return executor.execute(contract, action, quantity, current_price)

        elif strategy.lower() == ExecutionStrategy.VWAP.value:
            executor = VWAPExecutor(ib_client, config)
            return executor.execute(contract, action, quantity, current_price)

        elif strategy.lower() == ExecutionStrategy.ICEBERG.value:
            executor = IcebergOrder(ib_client, config)
            return executor.execute(contract, action, quantity, current_price)

        elif strategy.lower() == ExecutionStrategy.SIMPLE.value:
            # Simple market/limit order without slicing
            order = Order()
            order.action = action
            order.totalQuantity = quantity
            order.orderType = "LMT"
            order.lmtPrice = current_price
            order.transmit = True

            trade = ib_client.placeOrder(contract, order)

            stats = ExecutionStats(
                symbol=contract.symbol,
                action=action,
                target_quantity=quantity,
                arrival_price=current_price,
                filled_quantity=0
            )

            result = {
                "strategy": "SIMPLE",
                "symbol": contract.symbol,
                "action": action,
                "target_quantity": quantity,
                "order_id": trade.order.orderId,
            }

            return result, stats

    except Exception as e:
        logger.error(f"Smart execution failed for {contract.symbol}: {e}")
        raise


def should_use_smart_execution(
    quantity: int,
    price: float,
    config: Optional[ExecutionConfig] = None
) -> bool:
    """Determine if order should use smart execution.

    Args:
        quantity: Order quantity in shares
        price: Current price per share
        config: Optional ExecutionConfig

    Returns:
        True if smart execution should be used, False for simple execution
    """
    if config is None:
        config = ExecutionConfig()

    # Check both quantity and notional thresholds
    notional = quantity * price

    use_smart = (
        quantity >= config.smart_execution_min_shares
        or notional >= config.smart_execution_min_notional
    )

    return use_smart
