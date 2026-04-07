"""
test_tranche_exits.py — Tests for dual-tranche exit strategy.

Covers:
1. Tranche size arithmetic (odd + even qty)
2. T1 limit order placed at entry + 1.5×ATR with t1_qty
3. T2 stop placed when T1 order is no longer in IBKR open trades
4. Two log_trade OPEN calls with distinct tranche_ids
5. log_trade CLOSE called for T1 with correct fields
6. execute_sell works correctly on a T2-only position
7. tranche_mode=False preserves legacy TP qty (qty//3) and t1_status="N/A"
"""

from __future__ import annotations
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# Project root on path (conftest already handles this, but safe to repeat)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Evict any hollow module stubs planted by other test files so we import the real orders
for _mod in ("orders", "risk", "scanner", "signals", "news", "agents"):
    sys.modules.pop(_mod, None)

import orders
import orders_state
import orders_options


# ── Helpers ────────────────────────────────────────────────────────────────────

_BASE_CONFIG = {
    "active_account":        "DU_TEST",
    "atr_stop_multiplier":   1.5,
    "atr_trail_multiplier":  2.0,
    "min_reward_risk_ratio": 1.5,
    "risk_pct_per_trade":    0.01,
    "risk_per_trade":        0.01,
    "max_positions":         10,
    "max_single_position":   0.10,
    "max_position_size":     0.30,
    "correlation_threshold": 0.75,
    "max_sector_exposure":   0.50,
    "consecutive_loss_pause": 8,
    "high_conviction_score": 30,
    "fill_watcher":          {"enabled": False},
    "max_portfolio_allocation": 1.0,
}

_REGIME = {"regime": "BULL_TRENDING", "vix": 14.0}


def _make_ib(entry_price=100.0):
    """Return a minimal IB mock that assigns sequential orderIds and accepts placeOrder."""
    call_count = [0]

    def place_order(contract, order):
        call_count[0] += 1
        t = MagicMock()
        t.order = MagicMock()
        t.order.orderId = call_count[0] * 100
        # Give the order the attrs that orders.py reads back
        if hasattr(order, "totalQuantity"):
            t.order.totalQuantity = order.totalQuantity
        if hasattr(order, "lmtPrice"):
            t.order.lmtPrice = order.lmtPrice
        if hasattr(order, "auxPrice"):
            t.order.auxPrice = order.auxPrice
        t.orderStatus = MagicMock()
        t.orderStatus.status = "Submitted"
        return t

    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.qualifyContracts.return_value = [MagicMock()]
    ib.placeOrder.side_effect = place_order
    ib.openTrades.return_value = []
    ib.openOrders.return_value = []
    ib.portfolio.return_value = []
    ib.sleep = MagicMock()
    return ib


def _run_execute_buy(ib, qty, tranche_mode=True, atr=1.0, price=100.0):
    """Invoke execute_buy with standard mocks. Returns success bool."""
    with patch("orders.CONFIG", _BASE_CONFIG), \
         patch("orders.calculate_position_size", return_value=qty), \
         patch("orders.calculate_stops", return_value=(price - atr * 1.5, price + atr * 3.375)), \
         patch("orders.get_contract", return_value=MagicMock()), \
         patch("orders._get_ibkr_price", return_value=price), \
         patch("orders.get_tv_signal_cache", return_value={}), \
         patch("orders.check_correlation", return_value=(True, "")), \
         patch("orders.check_combined_exposure", return_value=(True, "")), \
         patch("orders.check_sector_concentration", return_value=(True, "")), \
         patch("orders._is_duplicate_check_enabled", return_value=False), \
         patch("orders.log_order"), \
         patch("learning.log_trade"):
        return orders.execute_buy(
            ib, "AAPL", price, atr, 25, 100_000, _REGIME,
            tranche_mode=tranche_mode,
        )


# ── Test 1 & 2: Tranche size arithmetic ────────────────────────────────────────

class TestTrancheSizeCalc:

    def test_odd_qty_split(self):
        """qty=101: t1=50, t2=51, sum=101."""
        t1 = 101 // 2
        t2 = 101 - t1
        assert t1 == 50
        assert t2 == 51
        assert t1 + t2 == 101

    def test_even_qty_split(self):
        """qty=100: t1=50, t2=50, sum=100."""
        t1 = 100 // 2
        t2 = 100 - t1
        assert t1 == 50
        assert t2 == 50
        assert t1 + t2 == 100

    def test_qty_2_splits_evenly(self):
        """Minimum valid tranche qty=2: t1=1, t2=1."""
        t1 = 2 // 2
        t2 = 2 - t1
        assert t1 == 1
        assert t2 == 1

    def test_qty_1_falls_back_to_legacy(self):
        """qty=1 is too small — execute_buy falls back to tranche_mode=False."""
        orders_state.active_trades.clear()
        ib = _make_ib()
        result = _run_execute_buy(ib, qty=1, tranche_mode=True)
        assert result is True
        # In legacy mode, t1_status should be "N/A"
        assert orders_state.active_trades.get("AAPL", {}).get("t1_status") == "N/A"
        orders_state.active_trades.clear()


# ── Test 3: T1 limit placed at entry + 1.5×ATR ────────────────────────────────

class TestT1LimitPlacement:

    def setup_method(self):
        orders_state.active_trades.clear()

    def teardown_method(self):
        orders_state.active_trades.clear()

    def test_t1_limit_price_is_entry_plus_1_5_atr(self):
        """T1 TP LimitOrder is constructed with price = entry + 1.5*ATR and qty = t1_qty."""
        ib = _make_ib()
        price, atr = 100.0, 1.0
        mock_limit = MagicMock(side_effect=lambda *a, **kw: MagicMock(
            totalQuantity=a[1], lmtPrice=a[2],
            parentId=None, transmit=False,
        ))

        with patch.object(orders, "LimitOrder", mock_limit), \
             patch.object(orders, "StopOrder", MagicMock(return_value=MagicMock(parentId=None, transmit=False))):
            result = _run_execute_buy(ib, qty=100, tranche_mode=True, atr=atr, price=price)

        assert result is True

        # LimitOrder calls: [0] = entry BUY, [1] = T1 TP SELL
        assert mock_limit.call_count >= 2
        tp_args = mock_limit.call_args_list[1][0]   # positional args of 2nd call
        expected_tp_price = round(price + atr * 1.5, 2)  # 101.5
        assert tp_args[1] == 50          # qty = t1_qty = 100 // 2
        assert tp_args[2] == pytest.approx(expected_tp_price, abs=0.01)

    def test_t1_qty_is_half_of_total(self):
        """T1 TP LimitOrder qty = qty // 2 = 50 for total qty=100."""
        ib = _make_ib()
        mock_limit = MagicMock(side_effect=lambda *a, **kw: MagicMock(
            totalQuantity=a[1], lmtPrice=a[2],
            parentId=None, transmit=False,
        ))

        with patch.object(orders, "LimitOrder", mock_limit), \
             patch.object(orders, "StopOrder", MagicMock(return_value=MagicMock(parentId=None, transmit=False))):
            result = _run_execute_buy(ib, qty=100, tranche_mode=True)

        assert result is True
        tp_args = mock_limit.call_args_list[1][0]
        assert tp_args[1] == 50  # t1_qty = 100 // 2

    def test_active_trades_has_tranche_fields(self):
        """active_trades entry contains all required tranche metadata."""
        ib = _make_ib()
        _run_execute_buy(ib, qty=100, tranche_mode=True)

        trade = orders.active_trades.get("AAPL", {})
        assert trade.get("tranche_mode") is True
        assert trade.get("t1_qty") == 50
        assert trade.get("t2_qty") == 50
        assert trade.get("t1_status") == "OPEN"
        assert trade.get("t1_order_id") is not None
        assert trade.get("t2_sl_order_id") is None  # not yet set


# ── Test 4: T2 stop placed when T1 order disappears ───────────────────────────

class TestUpdateTrancheStatus:

    def _make_trade(self, t1_order_id=200, t1_status="OPEN"):
        return {
            "symbol":          "AAPL",
            "instrument":      "stock",
            "status":          "ACTIVE",
            "direction":       "LONG",
            "entry":           100.0,
            "current":         101.5,
            "qty":             100,
            "t1_qty":          50,
            "t2_qty":          50,
            "sl":              98.5,
            "tp":              101.5,
            "atr":             1.0,
            "sl_order_id":     100,
            "t1_order_id":     t1_order_id,
            "t2_sl_order_id":  None,
            "t1_status":       t1_status,
            "tranche_mode":    True,
            "order_id":        99,
            "agent_outputs":   {},
            "score":           25,
            "reasoning":       "test",
            "open_time":       "2026-04-01T09:30:00+00:00",
            "high_water_mark": 101.5,
        }

    def test_t2_stop_placed_when_t1_not_in_open_trades(self):
        """T1 order absent from IBKR open trades → T2 stop placed with correct qty and price."""
        orders_state.active_trades.clear()
        orders_state.active_trades["AAPL"] = self._make_trade(t1_order_id=200)

        new_stop_trade = MagicMock()
        new_stop_trade.order.orderId = 999

        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.openTrades.return_value = []     # T1 order gone (filled)
        ib.placeOrder.return_value = new_stop_trade
        ib.sleep = MagicMock()

        captured_stop_args = []
        mock_stop = MagicMock(side_effect=lambda *a, **kw: captured_stop_args.append(a) or MagicMock(transmit=False))

        with patch("orders_options.get_contract", return_value=MagicMock()), \
             patch("orders_options._cancel_ibkr_order_by_id"), \
             patch("orders_options.CONFIG", {"active_account": "DU_TEST"}), \
             patch.object(orders_options, "StopOrder", mock_stop), \
             patch("learning.log_trade"):
            orders.update_tranche_status(ib)

        assert ib.placeOrder.called
        assert len(captured_stop_args) == 1, "StopOrder should be called once for T2"
        _action, _qty, _price = captured_stop_args[0][:3]
        assert _action == "SELL"
        assert _qty == 50                           # t2_qty
        assert _price == pytest.approx(98.5)        # sl_price

        trade = orders_state.active_trades["AAPL"]
        assert trade["t1_status"] == "FILLED"
        assert trade["t2_sl_order_id"] == 999
        assert trade["sl_order_id"] == 999          # updated for trailing stop
        assert trade["qty"] == 50                   # updated for execute_sell

    def test_no_action_when_t1_still_live(self):
        """T1 order still in IBKR open trades → no changes made."""
        orders_state.active_trades.clear()
        orders_state.active_trades["AAPL"] = self._make_trade(t1_order_id=200)

        live_order = MagicMock()
        live_order.order.orderId = 200

        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.openTrades.return_value = [live_order]

        with patch("orders_options.get_contract", return_value=MagicMock()), \
             patch("learning.log_trade"), \
             patch("orders_options.CONFIG", {"active_account": "DU_TEST"}):
            orders.update_tranche_status(ib)

        assert not ib.placeOrder.called
        assert orders_state.active_trades["AAPL"]["t1_status"] == "OPEN"

    def test_skips_already_filled_tranches(self):
        """Positions with t1_status='FILLED' are skipped."""
        orders_state.active_trades.clear()
        orders_state.active_trades["AAPL"] = self._make_trade(t1_status="FILLED")

        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.openTrades.return_value = []

        with patch("orders_options.CONFIG", {"active_account": "DU_TEST"}), \
             patch("learning.log_trade"):
            orders.update_tranche_status(ib)

        assert not ib.placeOrder.called

    def teardown_method(self):
        orders_state.active_trades.clear()


# ── Test 5: Two log_trade OPEN calls with distinct tranche_ids ─────────────────

class TestJournalAtOpen:

    def setup_method(self):
        orders_state.active_trades.clear()

    def teardown_method(self):
        orders_state.active_trades.clear()

    def test_two_log_trade_open_calls_with_distinct_tranche_ids(self):
        """execute_buy with tranche_mode=True calls log_trade twice: tranche_id=1 and 2."""
        ib = _make_ib()
        logged = []

        with patch("orders.CONFIG", _BASE_CONFIG), \
             patch("orders.calculate_position_size", return_value=100), \
             patch("orders.calculate_stops", return_value=(98.5, 103.375)), \
             patch("orders.get_contract", return_value=MagicMock()), \
             patch("orders._get_ibkr_price", return_value=100.0), \
             patch("orders.get_tv_signal_cache", return_value={}), \
             patch("orders.check_correlation", return_value=(True, "")), \
             patch("orders.check_combined_exposure", return_value=(True, "")), \
             patch("orders.check_sector_concentration", return_value=(True, "")), \
             patch("orders._is_duplicate_check_enabled", return_value=False), \
             patch("orders.log_order"), \
             patch("learning.log_trade", side_effect=lambda **kw: logged.append(kw)):
            orders.execute_buy(
                ib, "AAPL", 100.0, 1.0, 25, 100_000, _REGIME,
                tranche_mode=True,
            )

        open_calls = [c for c in logged if c.get("action") == "OPEN"]
        assert len(open_calls) == 2
        ids = {c["trade"]["tranche_id"] for c in open_calls}
        assert ids == {1, 2}

    def test_single_log_trade_open_call_in_legacy_mode(self):
        """execute_buy with tranche_mode=False calls log_trade once (no tranche_id)."""
        ib = _make_ib()
        logged = []

        with patch("orders.CONFIG", _BASE_CONFIG), \
             patch("orders.calculate_position_size", return_value=99), \
             patch("orders.calculate_stops", return_value=(98.5, 103.375)), \
             patch("orders.get_contract", return_value=MagicMock()), \
             patch("orders._get_ibkr_price", return_value=100.0), \
             patch("orders.get_tv_signal_cache", return_value={}), \
             patch("orders.check_correlation", return_value=(True, "")), \
             patch("orders.check_combined_exposure", return_value=(True, "")), \
             patch("orders.check_sector_concentration", return_value=(True, "")), \
             patch("orders._is_duplicate_check_enabled", return_value=False), \
             patch("orders.log_order"), \
             patch("learning.log_trade", side_effect=lambda **kw: logged.append(kw)):
            orders.execute_buy(
                ib, "AAPL", 100.0, 1.0, 25, 100_000, _REGIME,
                tranche_mode=False,
            )

        open_calls = [c for c in logged if c.get("action") == "OPEN"]
        assert len(open_calls) == 1
        assert open_calls[0]["trade"].get("tranche_id") is None


# ── Test 6: log_trade CLOSE called for T1 ─────────────────────────────────────

class TestJournalAtT1Close:

    def teardown_method(self):
        orders_state.active_trades.clear()

    def test_log_trade_close_for_t1_on_fill(self):
        """update_tranche_status logs CLOSE with tranche_id=1 and reason='tranche_1_tp'."""
        orders_state.active_trades.clear()
        orders_state.active_trades["AAPL"] = {
            "symbol": "AAPL", "instrument": "stock", "status": "ACTIVE",
            "direction": "LONG", "entry": 100.0, "current": 101.5,
            "qty": 100, "t1_qty": 50, "t2_qty": 50,
            "sl": 98.5, "tp": 101.5, "atr": 1.0,
            "sl_order_id": 100, "t1_order_id": 200,
            "t2_sl_order_id": None, "t1_status": "OPEN",
            "tranche_mode": True, "order_id": 99,
            "agent_outputs": {}, "score": 25, "reasoning": "test",
            "open_time": "2026-04-01T09:30:00+00:00",
            "high_water_mark": 101.5,
        }

        new_stop = MagicMock()
        new_stop.order.orderId = 999

        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.openTrades.return_value = []   # T1 filled
        ib.placeOrder.return_value = new_stop
        ib.sleep = MagicMock()

        close_calls = []

        with patch("orders_options.get_contract", return_value=MagicMock()), \
             patch("orders_options._cancel_ibkr_order_by_id"), \
             patch("orders_options.CONFIG", {"active_account": "DU_TEST"}), \
             patch("learning.log_trade", side_effect=lambda **kw: close_calls.append(kw)):
            orders.update_tranche_status(ib)

        assert len(close_calls) == 1
        c = close_calls[0]
        assert c["action"] == "CLOSE"
        assert c["trade"]["tranche_id"] == 1
        assert c["outcome"]["reason"] == "tranche_1_tp"
        assert c["outcome"]["exit_price"] == pytest.approx(101.5)
        assert c["outcome"]["pnl"] == pytest.approx((101.5 - 100.0) * 50)


# ── Test 7: Legacy mode (tranche_mode=False) ───────────────────────────────────

class TestLegacyMode:

    def setup_method(self):
        orders_state.active_trades.clear()

    def teardown_method(self):
        orders_state.active_trades.clear()

    def test_legacy_tp_qty_is_qty_over_3(self):
        """tranche_mode=False: TP qty = qty//3, t1_status='N/A'."""
        ib = _make_ib()
        mock_limit = MagicMock(side_effect=lambda *a, **kw: MagicMock(
            totalQuantity=a[1], lmtPrice=a[2],
            parentId=None, transmit=False,
        ))

        with patch("orders.CONFIG", _BASE_CONFIG), \
             patch("orders.calculate_position_size", return_value=99), \
             patch("orders.calculate_stops", return_value=(98.5, 103.375)), \
             patch("orders.get_contract", return_value=MagicMock()), \
             patch("orders._get_ibkr_price", return_value=100.0), \
             patch("orders.get_tv_signal_cache", return_value={}), \
             patch("orders.check_correlation", return_value=(True, "")), \
             patch("orders.check_combined_exposure", return_value=(True, "")), \
             patch("orders.check_sector_concentration", return_value=(True, "")), \
             patch("orders._is_duplicate_check_enabled", return_value=False), \
             patch("orders.log_order"), \
             patch("learning.log_trade"), \
             patch.object(orders, "LimitOrder", mock_limit), \
             patch.object(orders, "StopOrder", MagicMock(return_value=MagicMock(parentId=None, transmit=False))):
            result = orders.execute_buy(
                ib, "AAPL", 100.0, 1.0, 25, 100_000, _REGIME,
                tranche_mode=False,
            )

        assert result is True
        # LimitOrder calls: [0] = entry BUY, [1] = TP SELL with qty//3
        tp_args = mock_limit.call_args_list[1][0]
        assert tp_args[1] == 33   # 99 // 3 = 33

        trade = orders_state.active_trades.get("AAPL", {})
        assert trade.get("tranche_mode") is False
        assert trade.get("t1_status") == "N/A"
