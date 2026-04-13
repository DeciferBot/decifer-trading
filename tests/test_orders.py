"""Tests for orders.py — core order management logic."""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("decifer.tests.test_orders")

# ---------------------------------------------------------------------------
# Module-level mocks (ib_async / anthropic must exist before orders imports)
# ---------------------------------------------------------------------------
for _mod_name in ("ib_async", "anthropic"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orders_module(tmp_path):
    """Import orders with file-IO patched to tmp_path."""
    trades_file = tmp_path / "trades.json"
    trades_file.write_text(json.dumps([]))
    orders_file = tmp_path / "orders.json"
    orders_file.write_text(json.dumps([]))

    with patch("orders.TRADES_FILE", str(trades_file)), patch("orders.ORDERS_FILE", str(orders_file)):
        import orders as _orders

        return _orders, trades_file, orders_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHasOpenOrderFor:
    """Unit tests for orders.has_open_order_for()."""

    def test_returns_false_when_no_open_orders(self, tmp_path):
        """has_open_order_for returns False when open_orders dict is empty."""
        with (
            patch("orders.open_orders", {}),
            patch("orders.TRADES_FILE", str(tmp_path / "trades.json")),
            patch("orders.ORDERS_FILE", str(tmp_path / "orders.json")),
        ):
            import orders

            result = orders.has_open_order_for("AAPL")
            assert result is False

    def test_returns_true_when_symbol_present(self, tmp_path):
        """has_open_order_for returns True when symbol is in open_orders."""
        mock_trade = MagicMock()
        mock_trade.orderStatus.status = "Submitted"
        with (
            patch("orders_guards.open_orders", {"AAPL": mock_trade}),
            patch("orders.TRADES_FILE", str(tmp_path / "trades.json")),
            patch("orders.ORDERS_FILE", str(tmp_path / "orders.json")),
        ):
            import orders

            result = orders.has_open_order_for("AAPL")
            assert result is True

    def test_returns_false_for_different_symbol(self, tmp_path):
        """has_open_order_for returns False when a different symbol has an open order."""
        mock_trade = MagicMock()
        with (
            patch("orders.open_orders", {"TSLA": mock_trade}),
            patch("orders.TRADES_FILE", str(tmp_path / "trades.json")),
            patch("orders.ORDERS_FILE", str(tmp_path / "orders.json")),
        ):
            import orders

            result = orders.has_open_order_for("AAPL")
            assert result is False


class TestIsOptionsMarketOpen:
    """Unit tests for orders.is_options_market_open()."""

    def test_returns_bool(self, tmp_path):
        """is_options_market_open always returns a boolean."""
        with (
            patch("orders.TRADES_FILE", str(tmp_path / "trades.json")),
            patch("orders.ORDERS_FILE", str(tmp_path / "orders.json")),
        ):
            import orders

            result = orders.is_options_market_open()
            assert isinstance(result, bool)


class TestSafeTradeHelpers:
    """Unit tests for the thread-safe trade dict helpers."""

    def test_safe_set_and_del_trade(self, tmp_path):
        """_safe_set_trade and _safe_del_trade correctly mutate active_trades."""
        import orders_state

        with (
            patch("orders.TRADES_FILE", str(tmp_path / "trades.json")),
            patch("orders.ORDERS_FILE", str(tmp_path / "orders.json")),
            patch("orders_state.active_trades", {}) as mock_trades,
        ):
            import orders

            orders._safe_set_trade("AAPL", {"qty": 10})
            assert "AAPL" in orders_state.active_trades
            orders._safe_del_trade("AAPL")
            assert "AAPL" not in orders_state.active_trades

    def test_safe_update_trade_merges(self, tmp_path):
        """_safe_update_trade merges keys into an existing trade entry."""
        initial = {"AAPL": {"qty": 10, "entry": 150.0}}
        import orders_state

        with (
            patch("orders.TRADES_FILE", str(tmp_path / "trades.json")),
            patch("orders.ORDERS_FILE", str(tmp_path / "orders.json")),
            patch("orders_state.active_trades", initial),
        ):
            import orders

            orders._safe_update_trade("AAPL", {"stop": 140.0})
            assert orders_state.active_trades["AAPL"]["stop"] == 140.0
            assert orders_state.active_trades["AAPL"]["qty"] == 10
