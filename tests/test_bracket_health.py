"""
test_bracket_health.py — Regression tests for the FILLED-position bracket bug.

Root cause (2026-05-11 production session):
  BRACKET_AUDIT Pass2 treated status=FILLED positions as "no active position" and
  cancelled their stop-loss orders (VRT #16951, XOM #16956, USO #17063).  Pass1
  never repaired them because it also skipped FILLED positions entirely.

Tests A–D, G–H cover bracket_health.audit_bracket_orders().
Tests E–F cover the ACTIVE→FILLED regression in bot_ibkr._on_order_status_event()
and the ORDER_FILLED orderId=0 guard.
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

# ── Path bootstrap ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps before any Decifer import
for _mod in [
    "ib_async", "ib_insync", "anthropic", "yfinance", "praw",
    "feedparser", "tvDatafeed", "requests_html",
]:
    sys.modules.setdefault(_mod, MagicMock())

# ── Config stub ───────────────────────────────────────────────────────────────
import config as _config_mod
_cfg_defaults = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "positions_file": "/dev/null",
    "trade_events_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "active_account": "DUP481326",
    "bracket_health_enabled": True,
    "atr_trail_multiplier": 1.5,
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg_defaults.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg_defaults


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ibkr_trade(symbol, order_type, action, order_id, ref="", status="Submitted"):
    """Build a minimal mock ib_async Trade object for open-order scanning."""
    t = MagicMock()
    t.contract.symbol = symbol
    t.contract.secType = "STK"
    t.order.orderType = order_type       # "STP LMT", "LMT"
    t.order.action = action              # "SELL", "BUY"
    t.order.orderId = order_id
    t.order.orderRef = ref
    t.order.auxPrice = 95.0             # SL trigger price
    t.order.lmtPrice = 120.0            # TP limit price
    t.orderStatus.status = status
    return t


def _make_position(
    symbol, status, direction="LONG",
    sl_oid=None, tp_oid=None, qty=100,
    trade_id="", instrument="stock",
):
    return {
        "symbol": symbol,
        "status": status,
        "direction": direction,
        "sl_order_id": sl_oid,
        "tp_order_id": tp_oid,
        "qty": qty,
        "entry": 100.0,
        "current": 105.0,
        "sl": 95.0,
        "tp": 120.0,
        "atr": 2.0,
        "instrument": instrument,
        "trade_id": trade_id,
        "_fill_confirmed": True,
    }


def _make_ib(open_trades=None):
    """Build a minimal mock IB object."""
    ib = MagicMock()
    ib.openTrades.return_value = open_trades or []
    placed = MagicMock()
    placed.order.orderId = 99999
    placed.orderStatus.status = "Submitted"
    ib.placeOrder.return_value = placed
    return ib


# ── Tests A–D, G–H: bracket_health.audit_bracket_orders ─────────────────────

class TestBracketAuditFilledPositions:
    """
    A: FILLED position is NOT skipped by Pass1 — the audit loop processes it.
    B: Pass2 does NOT cancel the SL for a FILLED position.
    C: Pass1 repairs a FILLED position whose SL is absent (submits new SL).
    D: Pass1 submits a TP for a FILLED position with tp_order_id=None.
    G: ACTIVE-position SL repair still works (no regression from the fix).
    H: EXITING and CLOSED positions still trigger Pass2 orphan cancellation.
    """

    def _run_audit(self, active_trades_patch, ibkr_open_trades):
        import bracket_health
        ib = _make_ib(ibkr_open_trades)
        # Reset cooldown/grace-period state so tests don't bleed into each other.
        with (
            patch.object(bracket_health, "active_trades", active_trades_patch),
            patch.object(bracket_health, "_retry_ts", {}),
            patch.object(bracket_health, "_sl_place_ts", {}),
            patch("bracket_health._save_positions_file"),
            patch("bracket_health._safe_update_trade"),
        ):
            bracket_health.audit_bracket_orders(ib)
        return ib

    def test_A_filled_position_not_skipped_by_pass1(self):
        """Pass1 loop must enter the audit body for a FILLED position (not continue past it)."""
        pos = _make_position("VRT", "FILLED", sl_oid=None, tp_oid=None, trade_id="VRT_tid")
        trades = {"VRT": pos}
        # VRT has no SL in IBKR — Pass1 should attempt to submit one.
        ib = self._run_audit(trades, ibkr_open_trades=[])
        # placeOrder must have been called (SL submission attempted)
        assert ib.placeOrder.called, "Pass1 must attempt SL submission for FILLED positions"

    def test_B_pass2_does_not_cancel_sl_for_filled_position(self):
        """
        Pass2 must NOT cancel a stop-loss order whose symbol maps to a FILLED position.
        This is the VRT/XOM/USO scenario from the 2026-05-11 production session.
        """
        sl_trade = _make_ibkr_trade(
            "VRT", "STP LMT", "SELL", order_id=16951,
            ref="SL:VRT_20260511_1429",
        )
        pos = _make_position("VRT", "FILLED", sl_oid=16951, trade_id="VRT_tid")
        trades = {"VRT": pos}
        ib = self._run_audit(trades, ibkr_open_trades=[sl_trade])
        assert not ib.cancelOrder.called, (
            "Pass2 must NOT cancel SL orders for FILLED positions — "
            "they have live capital behind them"
        )

    def test_C_pass1_repairs_cancelled_sl_for_filled_position(self):
        """
        If a FILLED position has no SL in IBKR (was cancelled), Pass1 must
        submit a replacement.  VRT lost its SL this way on 2026-05-11.
        """
        # No SL in IBKR for VRT
        pos = _make_position("VRT", "FILLED", sl_oid=16951, tp_oid=None, trade_id="VRT_tid")
        trades = {"VRT": pos}
        ib = self._run_audit(trades, ibkr_open_trades=[])
        assert ib.placeOrder.called, "Pass1 must submit a new SL for a FILLED position with no live SL"

    def test_D_pass1_submits_tp_for_filled_position_with_no_tp(self):
        """
        Pass1 must submit a TP for a FILLED position that has tp_order_id=None.
        SCALP/INTRADAY trade types should receive a TP; the TP branch must
        not be gated on status==ACTIVE.
        """
        # SL is already live in IBKR — only TP is missing
        sl_trade = _make_ibkr_trade(
            "VRT", "STP LMT", "SELL", order_id=99901, ref="SL:VRT_tid",
        )
        pos = _make_position("VRT", "FILLED", sl_oid=99901, tp_oid=None, trade_id="VRT_tid")
        trades = {"VRT": pos}
        ib = self._run_audit(trades, ibkr_open_trades=[sl_trade])
        assert ib.placeOrder.called, "Pass1 must submit TP for a FILLED position with tp_order_id=None"

    def test_G_active_position_sl_repair_still_works(self):
        """
        Regression: existing ACTIVE-position SL repair must not be broken by
        the FILLED-position fix.  ACTIVE position with no live SL → new SL submitted.
        """
        pos = _make_position("IWM", "ACTIVE", sl_oid=None, trade_id="IWM_tid")
        trades = {"IWM": pos}
        ib = self._run_audit(trades, ibkr_open_trades=[])
        assert ib.placeOrder.called, "Pass1 must still repair missing SL for ACTIVE positions"

    def test_H_exiting_position_sl_still_orphaned_by_pass2(self):
        """
        EXITING positions have a close order outstanding — their bracket SLs
        are genuinely orphaned and must still be cancelled by Pass2.
        """
        sl_trade = _make_ibkr_trade(
            "OXY", "STP LMT", "SELL", order_id=17000, ref="SL:OXY_tid",
        )
        # EXITING → not in desired_long → orphan → should be cancelled
        pos = _make_position("OXY", "EXITING", sl_oid=17000, trade_id="OXY_tid")
        trades = {"OXY": pos}
        ib = self._run_audit(trades, ibkr_open_trades=[sl_trade])
        assert ib.cancelOrder.called, "Pass2 must cancel orphaned SLs for EXITING positions"

    def test_H_closed_position_sl_still_orphaned_by_pass2(self):
        """CLOSED positions — same as EXITING, SL must be treated as orphan."""
        sl_trade = _make_ibkr_trade(
            "WMT", "STP LMT", "SELL", order_id=17001, ref="SL:WMT_tid",
        )
        pos = _make_position("WMT", "CLOSED", sl_oid=17001, trade_id="WMT_tid")
        trades = {"WMT": pos}
        ib = self._run_audit(trades, ibkr_open_trades=[sl_trade])
        assert ib.cancelOrder.called, "Pass2 must cancel orphaned SLs for CLOSED positions"


# ── Tests E–F: bot_ibkr status-regression and ORDER_FILLED orderId guard ─────

class TestBotIbkrFillHandling:
    """
    E: _on_order_status_event must NOT overwrite status=ACTIVE → FILLED on
       repeated IBKR fill events.
    F: ORDER_FILLED event write is skipped when order.orderId == 0 to prevent
       corrupt order_id=0 records in trade_events.jsonl.
    """

    def _make_trade_event(self, symbol, ibkr_status, order_id, fill_price, filled_qty, total_qty, action="BUY"):
        order = MagicMock()
        order.orderId = order_id
        order.action = action
        order.totalQuantity = total_qty
        # Set numeric values so _log_order's JSON serialisation doesn't crash.
        order.lmtPrice = 0.0
        order.auxPrice = 0.0
        order.orderType = "LMT"
        trade = MagicMock()
        trade.contract.symbol = symbol
        trade.contract.secType = "STK"
        trade.order = order
        trade.orderStatus.status = ibkr_status
        trade.orderStatus.avgFillPrice = fill_price
        trade.orderStatus.filled = filled_qty
        trade.orderStatus.whyHeld = ""
        trade.log = []
        return trade

    def _run_on_status_event(self, initial_status, order_id, fill_calls_out):
        """Fire _on_order_status_event with a BUY FILLED event and capture updates."""
        import bot_ibkr
        import orders_state

        # Populate orders_state.active_trades in-place so the inner import sees it.
        pos = {
            "status": initial_status,
            "direction": "LONG",
            "trade_id": "IWM_tid",
            "entry": 286.61,
            "_fill_confirmed": False,
        }
        original = dict(orders_state.active_trades)
        orders_state.active_trades.clear()
        orders_state.active_trades["IWM"] = pos

        def _fake_append_fill(*a, **kw):
            fill_calls_out.append(kw)

        trade = self._make_trade_event("IWM", "Filled", order_id, 286.61, 199, 199)

        try:
            with (
                patch("bot_ibkr.dash", {}),
                patch("bot_ibkr.CONFIG", _config_mod.CONFIG),
                patch("event_log.append_fill", side_effect=_fake_append_fill),
                patch("learning.log_order"),   # prevent JSON-serialisation crash in _log_order
                patch("bot_voice.speak_natural"),  # prevent real TTS during tests
            ):
                bot_ibkr._on_order_status_event(trade)
        finally:
            orders_state.active_trades.clear()
            orders_state.active_trades.update(original)

        return pos

    def test_E_no_active_to_filled_regression(self):
        """
        When a FILLED event fires for a symbol already at status=ACTIVE (i.e.
        reconcile has already advanced it), the status must stay ACTIVE.
        """
        fill_calls = []
        pos = self._run_on_status_event("ACTIVE", order_id=16945, fill_calls_out=fill_calls)
        final_status = pos.get("status")
        assert final_status == "ACTIVE", (
            f"status must remain ACTIVE after repeated fill event, got {final_status!r}"
        )

    def test_E_pending_to_filled_transition_still_works(self):
        """
        When status is PENDING (first fill event), _on_order_status_event
        must still set status=FILLED — the guard must not block first transitions.
        """
        fill_calls = []
        pos = self._run_on_status_event("PENDING", order_id=16945, fill_calls_out=fill_calls)
        final_status = pos.get("status")
        assert final_status == "FILLED", (
            f"status must be set to FILLED on first fill from PENDING, got {final_status!r}"
        )

    def test_F_order_filled_write_skipped_when_order_id_zero(self):
        """
        When order.orderId == 0, ORDER_FILLED must NOT be written to event_log.
        Writing order_id=0 corrupts the event log (IWM production bug, 2026-05-11).
        """
        fill_calls = []
        self._run_on_status_event("PENDING", order_id=0, fill_calls_out=fill_calls)
        assert not fill_calls, (
            "append_fill must NOT be called when order.orderId == 0 "
            f"— got calls: {fill_calls}"
        )

    def test_F_order_filled_write_passes_correct_order_id(self):
        """
        When order.orderId is valid (>0), ORDER_FILLED must be written with
        that exact orderId — not 0.
        """
        fill_calls = []
        self._run_on_status_event("PENDING", order_id=16945, fill_calls_out=fill_calls)
        assert fill_calls, "append_fill must be called when orderId > 0"
        assert fill_calls[0].get("order_id") == 16945, (
            f"ORDER_FILLED must record order_id=16945, got {fill_calls[0]!r}"
        )


# ── Tests I–L: Pass 3 — cancel-before-close (HIMS 2026-05-11 regression) ─────

class TestPass3MissedStopCancelBeforeClose:
    """
    Regression tests for the HIMS position-inversion incident (2026-05-11).

    Root cause: Pass 3 placed a close LimitOrder without first cancelling the
    live TP and SL bracket orders, resulting in two simultaneous SELL orders
    that together oversold LONG 2148 into SHORT 2216.

    I: cancelOrder is called for every live bracket leg before placeOrder fires.
    J: The close placeOrder is NOT called if bracket cancel confirmation times out.
    K: When no bracket orders exist, the close order is placed directly (no cancel).
    L: Normal Pass 1 SL repair is unaffected (no regression from the fix).
    """

    def _make_hims_position(self, sl_oid=5002, tp_oid=5001):
        pos = _make_position(
            "HIMS", "ACTIVE",
            sl_oid=sl_oid, tp_oid=tp_oid,
            qty=2148, trade_id="HIMS_20260508_181621_615409",
        )
        pos["current"] = 26.60  # below SL → sl_breached
        pos["sl"] = 26.80
        pos["tp"] = 29.34
        return pos

    def _make_sl_trade(self, order_id=5002):
        return _make_ibkr_trade("HIMS", "STP LMT", "SELL", order_id=order_id,
                                ref="SL:HIMS_20260508_1816")

    def _make_tp_trade(self, order_id=5001):
        t = _make_ibkr_trade("HIMS", "LMT", "SELL", order_id=order_id,
                             ref="TP:HIMS_20260508_1816")
        t.order.lmtPrice = 29.34
        return t

    def _run_audit_pass3(self, pos, open_trades, cancel_side_effect=None):
        import bracket_health
        ib = _make_ib(open_trades)
        placed = MagicMock()
        placed.order.orderId = 99999
        placed.orderStatus.status = "Submitted"
        ib.placeOrder.return_value = placed
        if cancel_side_effect is not None:
            ib.cancelOrder.side_effect = cancel_side_effect
        trades = {"HIMS": pos}
        with (
            patch.object(bracket_health, "active_trades", trades),
            patch.object(bracket_health, "_retry_ts", {}),
            patch.object(bracket_health, "_sl_place_ts", {}),
            patch("bracket_health._save_positions_file"),
            patch("bracket_health._safe_update_trade"),
            patch("bracket_health.is_equities_extended_hours", return_value=True),
            patch("bracket_health.get_contract", return_value=MagicMock()),
            patch("bracket_health._get_ibkr_bid_ask", return_value=(26.55, 26.65)),
        ):
            bracket_health.audit_bracket_orders(ib)
        return ib

    def test_I_cancel_sent_for_all_bracket_legs_before_close(self):
        """
        When a missed stop fires and both SL and TP orders are live, cancelOrder
        must be called for BOTH bracket legs before placeOrder fires for the close.
        This is the exact failure mode from the HIMS 2026-05-11 incident.
        """
        sl_trade = self._make_sl_trade(5002)
        tp_trade = self._make_tp_trade(5001)
        cancelled_before_place: list[int] = []
        place_call_count_at_first_place: list[int] = []

        def _cancel(order):
            cancelled_before_place.append(order.orderId)
            # Simulate IBKR confirming the cancel
            if order.orderId == sl_trade.order.orderId:
                sl_trade.orderStatus.status = "Cancelled"
            elif order.orderId == tp_trade.order.orderId:
                tp_trade.orderStatus.status = "Cancelled"

        pos = self._make_hims_position()
        ib = self._run_audit_pass3(pos, [sl_trade, tp_trade], cancel_side_effect=_cancel)

        assert ib.cancelOrder.call_count >= 2, (
            f"cancelOrder must be called for both bracket legs (SL + TP), "
            f"got {ib.cancelOrder.call_count} call(s)"
        )
        assert ib.placeOrder.called, "close placeOrder must be called after brackets are cancelled"
        # Verify both bracket order IDs were cancelled
        cancelled_ids = {c.args[0].orderId for c in ib.cancelOrder.call_args_list}
        assert 5001 in cancelled_ids, "TP order #5001 must be cancelled"
        assert 5002 in cancelled_ids, "SL order #5002 must be cancelled"
        # Verify cancels preceded the close placeOrder in the call sequence
        cancel_indices = [
            i for i, c in enumerate(ib.mock_calls)
            if c[0] == "cancelOrder"
        ]
        place_indices = [
            i for i, c in enumerate(ib.mock_calls)
            if c[0] == "placeOrder"
        ]
        assert cancel_indices, "cancelOrder must appear in mock_calls"
        assert place_indices, "placeOrder must appear in mock_calls"
        assert max(cancel_indices) < min(place_indices), (
            "All cancelOrder calls must precede the close placeOrder call — "
            f"cancel at positions {cancel_indices}, place at positions {place_indices}"
        )

    def test_J_close_not_submitted_when_cancel_confirmation_times_out(self):
        """
        If bracket cancel confirmation does not arrive within the timeout,
        the close order must NOT be submitted. This prevents the double-SELL
        scenario when IBKR is slow to confirm cancellations.
        """
        sl_trade = self._make_sl_trade(5002)
        tp_trade = self._make_tp_trade(5001)
        # cancelOrder does NOT update status — simulates IBKR cancel timeout
        pos = self._make_hims_position()
        ib = self._run_audit_pass3(pos, [sl_trade, tp_trade], cancel_side_effect=None)

        assert ib.cancelOrder.call_count >= 1, "cancelOrder must still be attempted even when it times out"
        assert not ib.placeOrder.called, (
            "placeOrder for the close MUST NOT be called when bracket cancel "
            "confirmation times out — the position could be inverted otherwise"
        )

    def test_K_close_placed_directly_when_no_bracket_orders_live(self):
        """
        When no bracket orders are live in IBKR (e.g. already cancelled or
        filled by an earlier path), the close order must be placed immediately
        without waiting for any cancel confirmation.
        """
        pos = self._make_hims_position(sl_oid=None, tp_oid=None)
        # No open trades — bracket map is empty
        ib = self._run_audit_pass3(pos, open_trades=[])

        assert not ib.cancelOrder.called, "cancelOrder must not be called when no brackets are live"
        assert ib.placeOrder.called, "close placeOrder must be called when no brackets need cancelling"

    def test_L_pass1_sl_repair_unaffected_by_pass3_fix(self):
        """
        Regression: Pass 1 SL repair for ACTIVE positions must still work.
        The Pass 3 cancel-before-close change must not affect normal bracket management.
        """
        pos = _make_position("IWM", "ACTIVE", sl_oid=None, trade_id="IWM_tid")
        # current > sl so sl_breached is False — Pass 3 must not fire
        pos["current"] = 105.0
        pos["sl"] = 95.0
        trades = {"IWM": pos}
        import bracket_health
        ib = _make_ib([])  # no live brackets
        with (
            patch.object(bracket_health, "active_trades", trades),
            patch.object(bracket_health, "_retry_ts", {}),
            patch.object(bracket_health, "_sl_place_ts", {}),
            patch("bracket_health._save_positions_file"),
            patch("bracket_health._safe_update_trade"),
        ):
            bracket_health.audit_bracket_orders(ib)
        assert ib.placeOrder.called, "Pass 1 must still submit a new SL for ACTIVE positions with no live SL"
        assert not ib.cancelOrder.called, "cancelOrder must not be called during a normal Pass 1 SL repair"
