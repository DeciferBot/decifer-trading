"""Tests for fill_watcher.py — per-order background fill chaser."""

import sys
import threading
import time
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Module-level mocks — ib_async and anthropic must exist before any import
for _mod_name in ("ib_async", "anthropic"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# Provide LimitOrder as a simple mock constructor so fill_watcher can import it
_ib_async_mock = sys.modules["ib_async"]
_ib_async_mock.LimitOrder = MagicMock(return_value=MagicMock())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ib(connected=True, open_trades=None):
    """Return a minimal IB mock."""
    ib = MagicMock()
    ib.isConnected.return_value = connected
    ib.openTrades.return_value = open_trades or []
    ib.placeOrder.return_value = MagicMock()
    ib.cancelOrder.return_value = None
    ib.sleep = MagicMock()
    return ib


def _make_watcher(
    ib=None, symbol="AAPL", order_id=42, original_limit=100.0, qty=10, active_trades=None, trades_lock=None
):
    """Construct a FillWatcher with mocked dependencies."""
    import fill_watcher as fw

    ib = ib or _make_ib()
    entry_trade = MagicMock()
    entry_trade.order.orderId = order_id
    contract = MagicMock()

    watcher = fw.FillWatcher(
        ib=ib,
        symbol=symbol,
        order_id=order_id,
        entry_trade=entry_trade,
        original_limit=original_limit,
        contract=contract,
        qty=qty,
    )
    return watcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStopWatcher:
    def test_sets_stop_event_and_removes_from_registry(self):
        import fill_watcher as fw

        watcher = _make_watcher()
        with fw._watchers_lock:
            fw._active_watchers["AAPL"] = watcher

        fw.stop_watcher("AAPL")

        assert watcher._stop_event.is_set()
        with fw._watchers_lock:
            assert "AAPL" not in fw._active_watchers

    def test_noop_when_symbol_not_registered(self):
        import fill_watcher as fw

        # Should not raise
        fw.stop_watcher("NONEXISTENT")


class TestDisabledConfig:
    def test_exits_immediately_if_disabled(self):
        """When fill_watcher.enabled=False, run() returns without any IBKR calls."""

        ib = _make_ib()
        watcher = _make_watcher(ib=ib)

        cfg_patch = {"fill_watcher": {"enabled": False}}
        with patch("fill_watcher.CONFIG", cfg_patch):
            watcher.run()

        ib.isConnected.assert_not_called()
        ib.openTrades.assert_not_called()


class TestFillDuringInitialWait:
    def test_exits_cleanly_if_fill_detected_during_initial_wait(self):
        """If _is_filled() returns True after the initial wait, run() logs filled and exits."""

        ib = _make_ib(open_trades=[])  # no open trades → _is_filled returns True via IBKR
        watcher = _make_watcher(ib=ib)

        # Patch _interruptible_sleep to be instant and _is_filled to return True
        with (
            patch("fill_watcher._interruptible_sleep"),
            patch.object(watcher, "_is_filled", return_value=True),
            patch.object(watcher, "_cancel_order") as mock_cancel,
            patch.object(watcher, "_log_audit") as mock_audit,
            patch(
                "fill_watcher.CONFIG",
                {
                    "fill_watcher": {
                        "enabled": True,
                        "initial_wait_secs": 0,
                        "max_attempts": 3,
                        "interval_secs": 0,
                        "step_pct": 0.002,
                        "max_chase_pct": 0.01,
                    }
                },
            ),
        ):
            watcher.run()

        mock_cancel.assert_not_called()
        # Filled event should have been logged
        fill_events = [c.args[0] for c in mock_audit.call_args_list if "filled" in c.args[0]]
        assert fill_events, "Expected a fill_watcher_filled audit event"


class TestPriceAdjustmentAndFill:
    def test_adjusts_price_then_detects_fill(self):
        """run() calls _adjust_price once then detects fill on second check."""

        ib = _make_ib()
        watcher = _make_watcher(ib=ib, original_limit=100.0)

        # First call to _is_filled: not filled; second: filled
        fill_calls = iter([False, True])

        with (
            patch("fill_watcher._interruptible_sleep"),
            patch.object(watcher, "_is_filled", side_effect=fill_calls),
            patch.object(watcher, "_adjust_price", return_value=True) as mock_adjust,
            patch.object(watcher, "_cancel_order") as mock_cancel,
            patch.object(watcher, "_log_audit"),
            patch(
                "fill_watcher.CONFIG",
                {
                    "fill_watcher": {
                        "enabled": True,
                        "initial_wait_secs": 0,
                        "max_attempts": 3,
                        "interval_secs": 0,
                        "step_pct": 0.002,
                        "max_chase_pct": 0.01,
                    }
                },
            ),
        ):
            watcher.run()

        mock_adjust.assert_called_once()
        new_limit = mock_adjust.call_args[0][0]
        assert new_limit == pytest.approx(100.0 * 1.002, rel=1e-4)
        mock_cancel.assert_not_called()


class TestMaxAttemptsExhausted:
    def test_cancels_when_max_attempts_exhausted(self):
        """After max_attempts adjustments without a fill, _cancel_order is called."""

        ib = _make_ib()
        watcher = _make_watcher(ib=ib)

        with (
            patch("fill_watcher._interruptible_sleep"),
            patch.object(watcher, "_is_filled", return_value=False),
            patch.object(watcher, "_adjust_price", return_value=True),
            patch.object(watcher, "_cancel_order") as mock_cancel,
            patch.object(watcher, "_log_audit"),
            patch(
                "fill_watcher.CONFIG",
                {
                    "fill_watcher": {
                        "enabled": True,
                        "initial_wait_secs": 0,
                        "max_attempts": 2,
                        "interval_secs": 0,
                        "step_pct": 0.002,
                        "max_chase_pct": 0.10,
                    }
                },
            ),
        ):
            watcher.run()

        mock_cancel.assert_called_once_with("max_attempts_exhausted")


class TestCeilingBreach:
    def test_cancels_when_next_step_exceeds_ceiling(self):
        """If the next adjustment would exceed max_chase_pct, cancel is called."""

        ib = _make_ib()
        # original_limit=100, step_pct=0.01, max_chase_pct=0.005 → first step hits ceiling
        watcher = _make_watcher(ib=ib, original_limit=100.0)

        with (
            patch("fill_watcher._interruptible_sleep"),
            patch.object(watcher, "_is_filled", return_value=False),
            patch.object(watcher, "_adjust_price", return_value=True) as mock_adjust,
            patch.object(watcher, "_cancel_order") as mock_cancel,
            patch.object(watcher, "_log_audit"),
            patch(
                "fill_watcher.CONFIG",
                {
                    "fill_watcher": {
                        "enabled": True,
                        "initial_wait_secs": 0,
                        "max_attempts": 5,
                        "interval_secs": 0,
                        "step_pct": 0.01,
                        "max_chase_pct": 0.005,
                    }
                },
            ),
        ):
            watcher.run()

        mock_cancel.assert_called_once_with("price_ceiling_reached")
        mock_adjust.assert_not_called()  # Cancelled before any adjustment


class TestIBKRDisconnection:
    def test_aborts_cleanly_when_ibkr_disconnects(self):
        """If ib.isConnected() returns False during the loop, run() aborts without cancelling."""

        ib = _make_ib(connected=False)
        watcher = _make_watcher(ib=ib)

        with (
            patch("fill_watcher._interruptible_sleep"),
            patch.object(watcher, "_cancel_order") as mock_cancel,
            patch.object(watcher, "_log_audit") as mock_audit,
            patch(
                "fill_watcher.CONFIG",
                {
                    "fill_watcher": {
                        "enabled": True,
                        "initial_wait_secs": 0,
                        "max_attempts": 3,
                        "interval_secs": 0,
                        "step_pct": 0.002,
                        "max_chase_pct": 0.01,
                    }
                },
            ),
        ):
            watcher.run()

        mock_cancel.assert_not_called()
        abort_events = [c.args[0] for c in mock_audit.call_args_list if c.args[0] == "fill_watcher_aborted"]
        assert abort_events, "Expected fill_watcher_aborted audit event"


class TestStopEventInterruptsSleep:
    def test_stop_event_causes_run_to_exit_quickly(self):
        """stop_watcher() causes the running thread to exit within ~1 s."""
        import fill_watcher as fw

        ib = _make_ib()
        watcher = _make_watcher(ib=ib, symbol="MSFT")

        with fw._watchers_lock:
            fw._active_watchers["MSFT"] = watcher

        with patch(
            "fill_watcher.CONFIG",
            {
                "fill_watcher": {
                    "enabled": True,
                    "initial_wait_secs": 60,  # long wait
                    "max_attempts": 3,
                    "interval_secs": 60,
                    "step_pct": 0.002,
                    "max_chase_pct": 0.01,
                }
            },
        ):
            t = threading.Thread(target=watcher.run, daemon=True)
            t.start()
            time.sleep(0.1)  # let thread reach the sleep
            fw.stop_watcher("MSFT")
            t.join(timeout=2.0)

        assert not t.is_alive(), "FillWatcher thread should have exited within 2 s"


class TestAdjustPriceFailure:
    def test_adjust_failure_breaks_loop_and_cancels(self):
        """If _adjust_price returns False, the loop breaks and _cancel_order is called."""

        ib = _make_ib()
        watcher = _make_watcher(ib=ib)

        with (
            patch("fill_watcher._interruptible_sleep"),
            patch.object(watcher, "_is_filled", return_value=False),
            patch.object(watcher, "_adjust_price", return_value=False),
            patch.object(watcher, "_cancel_order") as mock_cancel,
            patch.object(watcher, "_log_audit"),
            patch(
                "fill_watcher.CONFIG",
                {
                    "fill_watcher": {
                        "enabled": True,
                        "initial_wait_secs": 0,
                        "max_attempts": 3,
                        "interval_secs": 0,
                        "step_pct": 0.002,
                        "max_chase_pct": 0.10,
                    }
                },
            ),
        ):
            watcher.run()

        # Loop breaks after failed adjust — max_attempts_exhausted cancel fires
        mock_cancel.assert_called_once_with("max_attempts_exhausted")


# ---------------------------------------------------------------------------
# Orphaned PENDING detection (update_positions_from_ibkr scan-cycle backstop)
# ---------------------------------------------------------------------------


class TestOrphanedPendingDetection:
    """update_positions_from_ibkr should cancel and remove watcherless PENDING orders past timeout."""

    def _setup_orders_module(self):
        """Ensure the real orders module is loaded, not a stub.

        If the real module is already in sys.modules (identified by having
        execute_buy), return it directly rather than evicting and reimporting.
        Evicting creates a *new* module object and breaks @patch targets in
        test_orders_core.py, which bound the name at its own import time.
        """
        existing = sys.modules.get("orders")
        if existing is not None and hasattr(existing, "execute_buy"):
            return existing
        for mod in ("orders", "risk", "scanner", "signals", "news", "agents"):
            sys.modules.pop(mod, None)
        import orders as _o

        return _o

    def test_orphan_check_cancels_watcherless_pending_past_timeout(self):
        """A PENDING order with no watcher and age > orphan_timeout_mins is cancelled."""
        from datetime import datetime, timedelta

        import fill_watcher as fw

        orders = self._setup_orders_module()

        ib = _make_ib(connected=True)
        ib.portfolio.return_value = []

        # Seed a PENDING entry that is 10 minutes old — well past the 5-min default
        stale_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        orders.active_trades.clear()
        orders.active_trades["TSLA"] = {
            "status": "PENDING",
            "order_id": 99,
            "symbol": "TSLA",
            "open_time": stale_time,
        }

        # No watcher running
        with fw._watchers_lock:
            fw._active_watchers.pop("TSLA", None)

        cfg_patch = {"active_account": "TEST", "fill_watcher": {"orphan_timeout_mins": 5}}
        with patch("orders.CONFIG", cfg_patch):
            orders.update_positions_from_ibkr(ib)

        assert "TSLA" not in orders.active_trades
        ib.cancelOrder.assert_called_once()

    def test_orphan_check_leaves_pending_alone_when_watcher_active(self):
        """A PENDING order with an active FillWatcher must not be cancelled."""
        from datetime import datetime, timedelta

        import fill_watcher as fw

        orders = self._setup_orders_module()

        ib = _make_ib(connected=True)
        ib.portfolio.return_value = []

        stale_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        orders.active_trades.clear()
        orders.active_trades["TSLA"] = {
            "status": "PENDING",
            "order_id": 99,
            "symbol": "TSLA",
            "open_time": stale_time,
        }

        # Register a mock watcher — order is being managed
        mock_watcher = MagicMock()
        with fw._watchers_lock:
            fw._active_watchers["TSLA"] = mock_watcher

        cfg_patch = {"active_account": "TEST", "fill_watcher": {"orphan_timeout_mins": 5}}
        try:
            with patch("orders.CONFIG", cfg_patch):
                orders.update_positions_from_ibkr(ib)

            assert "TSLA" in orders.active_trades
            ib.cancelOrder.assert_not_called()
        finally:
            with fw._watchers_lock:
                fw._active_watchers.pop("TSLA", None)
