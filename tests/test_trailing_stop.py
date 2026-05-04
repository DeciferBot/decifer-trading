"""Tests for update_trailing_stops() in orders_options.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path bootstrap + module mocks (must happen before any project import)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

for _mod in ("ib_async", "anthropic"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_ib_mock = sys.modules["ib_async"]
_ib_mock.IB = MagicMock
_ib_mock.Stock = MagicMock(return_value=MagicMock())
_ib_mock.LimitOrder = MagicMock(return_value=MagicMock())
_ib_mock.StopOrder = MagicMock(return_value=MagicMock())
_ib_mock.StopLimitOrder = MagicMock(return_value=MagicMock())
_ib_mock.MarketOrder = MagicMock(return_value=MagicMock())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ib(connected=True):
    ib = MagicMock()
    ib.isConnected.return_value = connected
    mod_trade = MagicMock()
    mod_trade.orderStatus.status = "Submitted"
    ib.placeOrder.return_value = mod_trade
    ib.sleep = MagicMock()
    return ib


def _active_long(
    symbol="AAPL", entry=100.0, current=100.0, sl=98.5, atr=1.0, sl_order_id=99, hwm=None, status="ACTIVE"
):
    return {
        "symbol": symbol,
        "instrument": "stock",
        "direction": "LONG",
        "entry": entry,
        "current": current,
        "sl": sl,
        "tp": 103.375,
        "qty": 10,
        "atr": atr,
        "sl_order_id": sl_order_id,
        "high_water_mark": hwm if hwm is not None else entry,
        "status": status,
    }


def _active_short(
    symbol="TSLA", entry=200.0, current=200.0, sl=202.0, atr=1.0, sl_order_id=88, hwm=None, status="ACTIVE"
):
    return {
        "symbol": symbol,
        "instrument": "stock",
        "direction": "SHORT",
        "entry": entry,
        "current": current,
        "sl": sl,
        "tp": 196.625,
        "qty": 5,
        "atr": atr,
        "sl_order_id": 88,
        "high_water_mark": hwm if hwm is not None else entry,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTrailingStopLong:
    def setup_method(self):
        import orders_state

        orders_state.active_trades.clear()

    def teardown_method(self):
        import orders_state

        orders_state.active_trades.clear()

    def test_raises_stop_on_new_high(self):
        """When current > entry by >0.5 ATR, trailing stop should beat original stop."""
        import orders
        import orders_state

        trade = _active_long(entry=100.0, current=101.5, sl=98.5, atr=1.0)
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib()
        with (
            patch("orders_options.get_contract", return_value=MagicMock()),
            patch(
                "orders_options.CONFIG",
                {
                    "trailing_stop_enabled": True,
                    "atr_trail_multiplier": 2.0,
                    "active_account": "DU123",
                },
            ),
        ):
            orders.update_trailing_stops(ib)

        # hwm=101.5, new_sl = 101.5 - 2.0*1.0 = 99.5  >  old_sl=98.5  → should update
        assert orders_state.active_trades["AAPL"]["sl"] == 99.5
        assert orders_state.active_trades["AAPL"]["high_water_mark"] == 101.5
        assert ib.placeOrder.called

    def test_does_not_lower_stop(self):
        """When current price retreats below hwm, stop must not move down."""
        import orders
        import orders_state

        trade = _active_long(entry=100.0, current=99.0, sl=98.5, atr=1.0, hwm=101.5)
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib()
        with (
            patch("orders_options.get_contract", return_value=MagicMock()),
            patch(
                "orders_options.CONFIG",
                {
                    "trailing_stop_enabled": True,
                    "atr_trail_multiplier": 2.0,
                    "active_account": "DU123",
                },
            ),
        ):
            orders.update_trailing_stops(ib)

        # current < hwm so no new high; new_sl would be 101.5-2=99.5 > old_sl=98.5
        # BUT since current < hwm, new_hwm = max(101.5, 99.0) = 101.5 — stop still advances
        # To truly test "does not lower", set current so trail gives a LOWER stop than existing
        # Reset: hwm=101.5, current=100.0, old_sl already at 99.5 (trail already moved up)
        orders_state.active_trades.clear()
        trade2 = _active_long(entry=100.0, current=100.0, sl=99.5, atr=1.0, hwm=101.5)
        orders_state.active_trades["AAPL"] = trade2
        ib2 = _make_ib()
        with (
            patch("orders_options.get_contract", return_value=MagicMock()),
            patch(
                "orders_options.CONFIG",
                {
                    "trailing_stop_enabled": True,
                    "atr_trail_multiplier": 2.0,
                    "active_account": "DU123",
                },
            ),
        ):
            orders.update_trailing_stops(ib2)

        # new_hwm = max(101.5, 100.0) = 101.5; new_sl = 99.5 = old_sl → no update
        assert orders_state.active_trades["AAPL"]["sl"] == 99.5
        assert not ib2.placeOrder.called

    def test_no_update_when_price_unchanged(self):
        """Price at entry — trailing stop (2 ATR) is behind original stop (1.5 ATR), skip."""
        import orders
        import orders_state

        trade = _active_long(entry=100.0, current=100.0, sl=98.5, atr=1.0)
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib()
        with (
            patch("orders_options.get_contract", return_value=MagicMock()),
            patch(
                "orders_options.CONFIG",
                {
                    "trailing_stop_enabled": True,
                    "atr_trail_multiplier": 2.0,
                    "active_account": "DU123",
                },
            ),
        ):
            orders.update_trailing_stops(ib)

        # new_sl = 100.0 - 2.0 = 98.0 < old_sl=98.5 → skip
        assert orders_state.active_trades["AAPL"]["sl"] == 98.5
        assert not ib.placeOrder.called


class TestTrailingStopShort:
    def setup_method(self):
        import orders_state

        orders_state.active_trades.clear()

    def teardown_method(self):
        import orders_state

        orders_state.active_trades.clear()

    def test_lowers_stop_on_new_low(self):
        """SHORT: when current < entry, trailing stop should move down."""
        import orders
        import orders_state

        trade = _active_short(entry=200.0, current=198.0, sl=202.0, atr=1.0)
        orders_state.active_trades["TSLA"] = trade

        ib = _make_ib()
        with (
            patch("orders_options.get_contract", return_value=MagicMock()),
            patch(
                "orders_options.CONFIG",
                {
                    "trailing_stop_enabled": True,
                    "atr_trail_multiplier": 2.0,
                    "active_account": "DU123",
                },
            ),
        ):
            orders.update_trailing_stops(ib)

        # new_hwm = min(200.0, 198.0) = 198.0; new_sl = 198.0 + 2.0 = 200.0 < old_sl=202.0
        assert orders_state.active_trades["TSLA"]["sl"] == 200.0
        assert orders_state.active_trades["TSLA"]["high_water_mark"] == 198.0
        assert ib.placeOrder.called


class TestTrailingStopSkips:
    def setup_method(self):
        import orders_state

        orders_state.active_trades.clear()

    def teardown_method(self):
        import orders_state

        orders_state.active_trades.clear()

    def test_skips_options(self):
        """Options positions must never be touched by trailing stop logic."""
        import orders
        import orders_state

        trade = _active_long()
        trade["instrument"] = "option"
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib()
        with patch("orders_options.CONFIG", {"trailing_stop_enabled": True, "atr_trail_multiplier": 2.0}):
            orders.update_trailing_stops(ib)

        assert not ib.placeOrder.called

    def test_skips_pending(self):
        """PENDING positions (not yet filled) must not be trailed."""
        import orders
        import orders_state

        trade = _active_long(status="PENDING", current=105.0)
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib()
        with patch("orders_options.CONFIG", {"trailing_stop_enabled": True, "atr_trail_multiplier": 2.0}):
            orders.update_trailing_stops(ib)

        assert not ib.placeOrder.called

    def test_skips_when_no_sl_order_id(self):
        """Positions without sl_order_id (e.g. externally opened) are skipped."""
        import orders
        import orders_state

        trade = _active_long(current=105.0)
        trade.pop("sl_order_id")
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib()
        with (
            patch("orders_options.get_contract", return_value=MagicMock()),
            patch("orders_options.CONFIG", {"trailing_stop_enabled": True, "atr_trail_multiplier": 2.0}),
        ):
            orders.update_trailing_stops(ib)

        assert not ib.placeOrder.called

    def test_skips_when_disabled(self):
        """trailing_stop_enabled=False must disable all trailing stop updates."""
        import orders
        import orders_state

        trade = _active_long(current=105.0)
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib()
        with patch("orders_options.CONFIG", {"trailing_stop_enabled": False, "atr_trail_multiplier": 2.0}):
            orders.update_trailing_stops(ib)

        assert not ib.placeOrder.called

    def test_skips_when_ibkr_disconnected(self):
        """If IBKR is disconnected mid-loop, skip rather than crash."""
        import orders
        import orders_state

        trade = _active_long(current=105.0)
        orders_state.active_trades["AAPL"] = trade

        ib = _make_ib(connected=False)
        with (
            patch("orders_options.get_contract", return_value=MagicMock()),
            patch(
                "orders_options.CONFIG",
                {
                    "trailing_stop_enabled": True,
                    "atr_trail_multiplier": 2.0,
                    "active_account": "DU123",
                },
            ),
        ):
            orders.update_trailing_stops(ib)

        assert not ib.placeOrder.called
