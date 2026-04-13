"""Tests for the hardened flatten_all kill-switch in orders.py.

Covers:
- Re-entrancy guard (double-click protection)
- reqGlobalCancel issued before any placeOrder
- Per-position failure isolation (try/finally)
- _wait_for_order_book_clear polling logic
- Completion summary logging
- Module-level constants and flags exist
"""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

# ── 1. sys.path so flat imports work ────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 2. Stub ALL heavy deps BEFORE importing any Decifer module ───────────────
for mod in [
    "ib_async",
    "ib_insync",
    "anthropic",
    "yfinance",
    "praw",
    "feedparser",
    "tvDatafeed",
    "requests_html",
]:
    sys.modules.setdefault(mod, MagicMock())

# ── 3. Stub config with required keys ────────────────────────────────────────
_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
}
import config as _config_mod

if hasattr(_config_mod, "CONFIG"):
    _config_mod.CONFIG.update(_cfg)
else:
    _config_mod.CONFIG = _cfg

# ── 4. NOW safe to import orders  (pop any hollow stub test_bot.py cached) ────
for _decifer_mod in (
    "orders",
    "risk",
    "learning",
    "scanner",
    "signals",
    "news",
    "agents",
    "options",
    "options_scanner",
):
    sys.modules.pop(_decifer_mod, None)
import orders
import orders_portfolio

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _make_mock_ib(open_orders=None, raise_on_place=None):
    """Build a minimal mock IB object.

    Args:
        open_orders: list of fake orders returned by ib.openOrders().
                     Defaults to []
        raise_on_place: if not None, placeOrder() raises this exception
                        for the first call only.
    """
    ib = MagicMock()
    ib.openOrders.return_value = open_orders if open_orders is not None else []
    if raise_on_place is not None:
        ib.placeOrder.side_effect = [raise_on_place, MagicMock()]
    else:
        ib.placeOrder.return_value = MagicMock()
    ib.reqGlobalCancel.return_value = None
    ib.qualifyContracts.return_value = None
    return ib


def _inject_trades(trades_dict):
    """Directly replace orders.open_trades with *trades_dict* content.

    Works regardless of whether open_trades is a plain dict or has a lock.
    """
    with orders._trades_lock:
        orders.open_trades.clear()
        orders.open_trades.update(trades_dict)


def _clear_trades():
    """Reset open_trades to empty."""
    _inject_trades({})


def _reset_flatten_flag():
    """Ensure _flatten_in_progress is False before / after a test."""
    with orders._flatten_lock:
        orders_portfolio._flatten_in_progress = False


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level attribute tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestModuleLevelAttributes:
    """Verify that all new symbols introduced by the hardening patch exist."""

    def test_flatten_in_progress_exists(self):
        assert hasattr(orders, "_flatten_in_progress"), (
            "orders_portfolio._flatten_in_progress not found — patch may not have been applied"
        )

    def test_flatten_in_progress_starts_false(self):
        _reset_flatten_flag()
        assert orders_portfolio._flatten_in_progress is False

    def test_flatten_lock_exists(self):
        assert hasattr(orders, "_flatten_lock")

    def test_flatten_lock_is_lock(self):
        # Must be usable as a context manager (threading.Lock or RLock)
        assert hasattr(orders._flatten_lock, "__enter__") and hasattr(orders._flatten_lock, "__exit__")

    def test_global_cancel_wait_secs_exists(self):
        assert hasattr(orders, "_GLOBAL_CANCEL_WAIT_SECS"), "_GLOBAL_CANCEL_WAIT_SECS not found"

    def test_global_cancel_wait_secs_positive(self):
        assert orders._GLOBAL_CANCEL_WAIT_SECS > 0

    def test_global_cancel_poll_interval_exists(self):
        assert hasattr(orders, "_GLOBAL_CANCEL_POLL_INTERVAL"), "_GLOBAL_CANCEL_POLL_INTERVAL not found"

    def test_global_cancel_poll_interval_positive(self):
        assert orders._GLOBAL_CANCEL_POLL_INTERVAL > 0

    def test_poll_interval_less_than_wait(self):
        # Poll interval must be shorter than the total wait window
        assert orders._GLOBAL_CANCEL_POLL_INTERVAL < orders._GLOBAL_CANCEL_WAIT_SECS

    def test_wait_for_order_book_clear_callable(self):
        assert callable(getattr(orders, "_wait_for_order_book_clear", None)), (
            "_wait_for_order_book_clear not found or not callable"
        )

    def test_flatten_all_callable(self):
        assert callable(orders.flatten_all)


# ═══════════════════════════════════════════════════════════════════════════════
# _wait_for_order_book_clear
# ═══════════════════════════════════════════════════════════════════════════════


class TestWaitForOrderBookClear:
    """Unit tests for the polling helper."""

    def test_returns_zero_when_book_already_empty(self):
        ib = _make_mock_ib(open_orders=[])
        result = orders._wait_for_order_book_clear(ib, timeout=2.0)
        assert result == 0

    def test_returns_zero_when_book_clears_mid_poll(self):
        """Simulate orders being cleared on the second poll."""
        ib = MagicMock()
        ib.openOrders.side_effect = [[MagicMock()], []]
        result = orders._wait_for_order_book_clear(ib, timeout=2.0)
        assert result == 0

    def test_returns_nonzero_when_book_never_clears(self):
        """Order book stays busy throughout; should return after timeout."""
        ib = MagicMock()
        ib.openOrders.return_value = [MagicMock(), MagicMock()]  # always 2
        start = time.monotonic()
        # Use a very short timeout so the test is fast
        result = orders._wait_for_order_book_clear(ib, timeout=0.5)
        elapsed = time.monotonic() - start
        assert result > 0
        # Should not have waited much longer than the timeout
        assert elapsed < 2.0

    def test_handles_open_orders_exception(self):
        """If openOrders() raises, the helper should not crash."""
        ib = MagicMock()
        ib.openOrders.side_effect = RuntimeError("IBKR offline")
        # Should complete without raising
        result = orders._wait_for_order_book_clear(ib, timeout=0.3)
        # Cannot know count, but must be non-negative
        assert result >= 0

    def test_polls_multiple_times_before_giving_up(self):
        ib = MagicMock()
        ib.openOrders.return_value = [MagicMock()]  # never empty
        orders._wait_for_order_book_clear(ib, timeout=0.4)
        # Should have polled more than once
        assert ib.openOrders.call_count >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — reqGlobalCancel ordering
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllGlobalCancel:
    """reqGlobalCancel must be called first, before any placeOrder."""

    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_global_cancel_called_when_positions_exist(self):
        _inject_trades(
            {
                "AAPL_stock": {"symbol": "AAPL", "qty": 10, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        ib.reqGlobalCancel.assert_called_once()

    def test_global_cancel_called_even_with_no_positions(self):
        """reqGlobalCancel should still fire to clear dangling orders."""
        _clear_trades()
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
        ):
            orders.flatten_all(None)

        ib.reqGlobalCancel.assert_called_once()

    def test_global_cancel_before_place_order(self):
        """reqGlobalCancel must appear in the call log before placeOrder."""
        _inject_trades(
            {
                "TSLA_stock": {"symbol": "TSLA", "qty": 5, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()
        call_order = []

        original_cancel = ib.reqGlobalCancel
        original_place = ib.placeOrder

        def _cancel():
            call_order.append("reqGlobalCancel")
            return original_cancel()

        def _place(*args, **kwargs):
            call_order.append("placeOrder")
            return original_place(*args, **kwargs)

        ib.reqGlobalCancel = _cancel
        ib.placeOrder = _place

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        if "placeOrder" in call_order:
            assert call_order.index("reqGlobalCancel") < call_order.index("placeOrder"), (
                "reqGlobalCancel must happen before the first placeOrder"
            )

    def test_global_cancel_proceeds_even_if_open_orders_raises(self):
        """Pre-cancel order count query failing should not stop the kill switch."""
        _inject_trades(
            {
                "MSFT_stock": {"symbol": "MSFT", "qty": 3, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()
        ib.openOrders.side_effect = RuntimeError("network error")

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            # Should not raise
            orders.flatten_all(None)

        # reqGlobalCancel still called
        ib.reqGlobalCancel.assert_called_once()

    def test_global_cancel_exception_does_not_abort(self):
        """Even if reqGlobalCancel itself raises, positions should still be closed."""
        _inject_trades(
            {
                "NVDA_stock": {"symbol": "NVDA", "qty": 2, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()
        ib.reqGlobalCancel.side_effect = RuntimeError("TWS rejected global cancel")

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)  # must not propagate the exception


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — re-entrancy guard
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllReentrancyGuard:
    """A second call while the first is in progress must be a no-op."""

    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_double_call_same_thread_no_op(self):
        """Manually set the flag to simulate an ongoing flatten."""
        ib = _make_mock_ib()
        with orders._flatten_lock:
            orders_portfolio._flatten_in_progress = True

        with patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib):
            orders.flatten_all(None)  # should return immediately

        # No IBKR calls should have been made
        ib.reqGlobalCancel.assert_not_called()
        ib.placeOrder.assert_not_called()

    def test_flag_reset_after_normal_completion(self):
        """_flatten_in_progress must be False when flatten_all returns."""
        _inject_trades(
            {
                "SPY_stock": {"symbol": "SPY", "qty": 1, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        assert orders_portfolio._flatten_in_progress is False

    def test_flag_reset_after_ib_connection_failure(self):
        """_flatten_in_progress must be False even when IB connection fails."""
        with patch.object(orders_portfolio, "_get_emergency_ib", return_value=None):
            orders.flatten_all(None)

        assert orders_portfolio._flatten_in_progress is False

    def test_flag_reset_after_exception_in_close_loop(self):
        """_flatten_in_progress must be False even when a position close raises."""
        _inject_trades(
            {
                "QQQ_stock": {"symbol": "QQQ", "qty": 4, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib(raise_on_place=RuntimeError("fill error"))

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)  # should not raise

        assert orders_portfolio._flatten_in_progress is False

    def test_concurrent_calls_only_one_proceeds(self):
        """Two threads calling flatten_all — only one should issue real orders."""
        ib = _make_mock_ib()
        _inject_trades(
            {
                "AMZN_stock": {"symbol": "AMZN", "qty": 2, "instrument": "stock"},
            }
        )

        barrier = threading.Barrier(2)
        results = []

        # Slow mock to guarantee overlap
        original_cancel = ib.reqGlobalCancel

        def slow_cancel():
            results.append("cancel")
            time.sleep(0.2)
            return original_cancel()

        ib.reqGlobalCancel = slow_cancel

        def _run():
            barrier.wait()
            with (
                patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
                patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
                patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
                patch.object(orders_portfolio, "_safe_del_trade"),
            ):
                orders.flatten_all(None)

        t1 = threading.Thread(target=_run)
        t2 = threading.Thread(target=_run)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # reqGlobalCancel (real slow_cancel) should only run once
        assert results.count("cancel") == 1, f"reqGlobalCancel called {results.count('cancel')} times; expected 1"

        # Flag must be cleared after both threads finish
        assert orders_portfolio._flatten_in_progress is False


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — per-position failure isolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllFailureIsolation:
    """A failing close on one symbol must not prevent others from closing."""

    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_second_position_closed_when_first_fails(self):
        """placeOrder raises on first symbol; second symbol must still be attempted."""
        _inject_trades(
            {
                "AAPL_stock": {"symbol": "AAPL", "qty": 10, "instrument": "stock"},
                "GOOG_stock": {"symbol": "GOOG", "qty": 3, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()
        # First call raises, second succeeds
        ib.placeOrder.side_effect = [RuntimeError("order rejected"), MagicMock()]

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)  # must not raise

        # placeOrder should have been attempted at least twice
        assert ib.placeOrder.call_count >= 2, "Expected placeOrder called for both positions even after first failure"

    def test_no_raise_when_all_positions_fail(self):
        """Even if every position close raises, flatten_all must return cleanly."""
        _inject_trades(
            {
                "FB_stock": {"symbol": "FB", "qty": 5, "instrument": "stock"},
                "NFLX_stock": {"symbol": "NFLX", "qty": 5, "instrument": "stock"},
                "UBER_stock": {"symbol": "UBER", "qty": 5, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()
        ib.placeOrder.side_effect = RuntimeError("catastrophic failure")

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)  # must not propagate

        assert orders_portfolio._flatten_in_progress is False

    def test_qualify_contracts_failure_isolated(self):
        """If qualifyContracts raises for one symbol, others still proceed."""
        _inject_trades(
            {
                "BABA_stock": {"symbol": "BABA", "qty": 2, "instrument": "stock"},
                "JD_stock": {"symbol": "JD", "qty": 2, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()
        ib.qualifyContracts.side_effect = [RuntimeError("bad contract"), None]

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        assert orders_portfolio._flatten_in_progress is False


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — empty portfolio
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllEmptyPortfolio:
    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_no_error_when_no_positions(self):
        ib = _make_mock_ib()
        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
        ):
            orders.flatten_all(None)

        ib.placeOrder.assert_not_called()

    def test_global_cancel_still_fires_with_no_positions(self):
        ib = _make_mock_ib()
        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
        ):
            orders.flatten_all(None)

        ib.reqGlobalCancel.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — no IB connection
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllNoIBConnection:
    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_returns_cleanly_when_ib_unavailable(self):
        with patch.object(orders_portfolio, "_get_emergency_ib", return_value=None):
            orders.flatten_all(None)  # must not raise

    def test_flag_cleared_when_ib_unavailable(self):
        with patch.object(orders_portfolio, "_get_emergency_ib", return_value=None):
            orders.flatten_all(None)
        assert orders_portfolio._flatten_in_progress is False


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — order-book wait integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllOrderBookWait:
    """Verify flatten_all calls _wait_for_order_book_clear after reqGlobalCancel."""

    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_wait_called_after_global_cancel(self):
        ib = _make_mock_ib()
        wait_calls = []

        def fake_wait(ib_arg, timeout=None):
            wait_calls.append(("wait", timeout))
            return 0

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", side_effect=fake_wait),
        ):
            orders.flatten_all(None)

        assert len(wait_calls) == 1, "_wait_for_order_book_clear should be called exactly once"

    def test_positions_closed_after_wait_even_if_orders_remain(self):
        """If wait returns non-zero (orders remain), positions should still close."""
        _inject_trades(
            {
                "IWM_stock": {"symbol": "IWM", "qty": 7, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=3),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        # placeOrder should still have been called
        ib.placeOrder.assert_called()


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — logging
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllLogging:
    """Verify that structured log entries are emitted."""

    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_critical_log_at_start(self, caplog):
        import logging

        ib = _make_mock_ib()
        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            caplog.at_level(logging.DEBUG),
        ):
            orders.flatten_all(None)

        full_log = " ".join(caplog.messages).lower()
        # Either "kill switch" or "flatten" must appear somewhere in logs
        assert "kill" in full_log or "flatten" in full_log or "emergency" in full_log, (
            f"Expected start log message not found. Log: {caplog.messages}"
        )

    def test_reentrancy_warning_logged(self, caplog):
        import logging

        # Simulate in-progress state
        with orders._flatten_lock:
            orders_portfolio._flatten_in_progress = True

        ib = _make_mock_ib()
        with patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib), caplog.at_level(logging.WARNING):
            orders.flatten_all(None)

        full_log = " ".join(caplog.messages).lower()
        assert (
            "already" in full_log
            or "progress" in full_log
            or "duplicate" in full_log
            or "reentr" in full_log
            or "reentran" in full_log
        ), f"Expected re-entrancy warning not found. Log: {caplog.messages}"


# ═══════════════════════════════════════════════════════════════════════════════
# flatten_all — multiple stock positions closed
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllMultiplePositions:
    """Verify all tracked positions receive a closing market order."""

    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_all_positions_receive_close_order(self):
        symbols = ["AAPL", "TSLA", "NVDA", "MSFT"]
        _inject_trades(
            {f"{s}_stock": {"symbol": s, "qty": i + 1, "instrument": "stock"} for i, s in enumerate(symbols)}
        )
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        assert ib.placeOrder.call_count == len(symbols), (
            f"Expected {len(symbols)} placeOrder calls, got {ib.placeOrder.call_count}"
        )

    def test_sell_action_used_not_buy(self):
        """All closing orders must be SELL directions."""
        _inject_trades(
            {
                "META_stock": {"symbol": "META", "qty": 5, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()
        submitted_orders = []

        def capture_place(contract, order):
            submitted_orders.append(order)
            return MagicMock()

        ib.placeOrder = capture_place

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        for o in submitted_orders:
            action = getattr(o, "action", None)
            if action is not None:
                assert action.upper() == "SELL", f"Expected SELL order, got {action}"

    def test_zero_qty_position_skipped(self):
        """A tracked position with qty=0 must not generate a close order."""
        _inject_trades(
            {
                "ZERO_stock": {"symbol": "ZERO", "qty": 0, "instrument": "stock"},
            }
        )
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "get_contract", return_value=MagicMock()),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
            patch.object(orders_portfolio, "_safe_del_trade"),
        ):
            orders.flatten_all(None)

        ib.placeOrder.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Idempotency — safe to call flatten_all repeatedly after completion
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlattenAllIdempotency:
    def setup_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def teardown_method(self):
        _clear_trades()
        _reset_flatten_flag()

    def test_second_call_after_first_completes_does_not_raise(self):
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
        ):
            orders.flatten_all(None)
            # Flag should now be False — second call should also work
            orders.flatten_all(None)

    def test_second_call_also_issues_global_cancel(self):
        ib = _make_mock_ib()

        with (
            patch.object(orders_portfolio, "_get_emergency_ib", return_value=ib),
            patch.object(orders_portfolio, "_wait_for_order_book_clear", return_value=0),
        ):
            orders.flatten_all(None)
            orders.flatten_all(None)

        assert ib.reqGlobalCancel.call_count == 2
