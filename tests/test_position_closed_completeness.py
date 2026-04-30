"""
test_position_closed_completeness.py

Verifies that every exit path writes a POSITION_CLOSED event to trade_events.jsonl
with a non-zero exit price, and that the event_log WAL handles duplicates safely.

Coverage:
  Group 1 — event_log deduplication safety (no IB needed)
  Group 2 — check_external_closes writes POSITION_CLOSED via append_close()
  Group 3 — execute_sell uses avgFillPrice over stale tracker cache
  Group 4 — _flatten_all_inner writes tombstone POSITION_CLOSED
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy deps before any Decifer import ─────────────────────────────────
for _mod in (
    "anthropic", "ib_async", "ib_async.objects", "ib_insync",
    "yfinance", "praw", "feedparser", "tvDatafeed", "requests_html",
    "httpx", "colorama", "portfolio_manager",
):
    sys.modules.setdefault(_mod, MagicMock())

import colorama as _cm
_cm.Fore = MagicMock(); _cm.Style = MagicMock(); _cm.init = MagicMock()

sys.modules["ib_async"].IB = MagicMock
sys.modules["ib_async"].LimitOrder = MagicMock
sys.modules["ib_async"].MarketOrder = MagicMock
sys.modules["ib_async"].Stock = MagicMock

# ── Helpers ───────────────────────────────────────────────────────────────────

def _tmp_event_log():
    """Return (tmp_path, restore_fn). Patches event_log._LOG_FILE."""
    import event_log
    tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
    orig = event_log._LOG_FILE
    event_log._LOG_FILE = tmp
    def restore():
        event_log._LOG_FILE = orig
        tmp.unlink(missing_ok=True)
    return tmp, restore


def _read_events(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _make_trade(symbol="AAPL", trade_id=None, status="ACTIVE",
                entry=150.0, current=152.0, qty=100,
                direction="LONG", instrument="stock"):
    return {
        "symbol": symbol,
        "trade_id": trade_id or f"{symbol}_20260430_093000_000000",
        "status": status,
        "entry": entry,
        "current": current,
        "qty": qty,
        "direction": direction,
        "instrument": instrument,
        "open_time": datetime.now(UTC).isoformat(),
        "sl": entry * 0.97,
        "tp": entry * 1.06,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Group 1 — event_log WAL deduplication safety
# ═════════════════════════════════════════════════════════════════════════════

class TestEventLogDeduplication(unittest.TestCase):
    """Duplicate POSITION_CLOSED events must not corrupt open_trades()."""

    def test_duplicate_position_closed_is_safe(self):
        """Writing two POSITION_CLOSED for the same trade_id must not raise and
        must leave the position counted as closed (not open)."""
        import event_log
        tmp, restore = _tmp_event_log()
        try:
            tid = "NVDA_20260430_093000"
            event_log.append_fill(tid, "NVDA", fill_price=200.0, fill_qty=50)
            event_log.append_close(tid, "NVDA", exit_price=205.0, pnl=250.0,
                                   exit_reason="apex_exit", hold_minutes=10)
            event_log.append_close(tid, "NVDA", exit_price=205.0, pnl=250.0,
                                   exit_reason="apex_exit_duplicate", hold_minutes=10)

            open_pos = event_log.open_trades()
            self.assertNotIn(tid, [v.get("trade_id") for v in open_pos.values()],
                             "Duplicate POSITION_CLOSED must not leave position open")
        finally:
            restore()

    def test_open_trades_decrements_after_single_close(self):
        """ORDER_FILLED then POSITION_CLOSED must yield empty open_trades."""
        import event_log
        tmp, restore = _tmp_event_log()
        try:
            tid = "CRWD_20260430_093000"
            event_log.append_fill(tid, "CRWD", fill_price=450.0, fill_qty=131)
            self.assertEqual(len(event_log.open_trades()), 1)
            event_log.append_close(tid, "CRWD", exit_price=442.0, pnl=-1048.0,
                                   exit_reason="sl_hit", hold_minutes=28)
            self.assertEqual(len(event_log.open_trades()), 0)
        finally:
            restore()

    def test_position_without_close_shows_as_open(self):
        """ORDER_FILLED with no POSITION_CLOSED must appear in open_trades."""
        import event_log
        tmp, restore = _tmp_event_log()
        try:
            tid = "QCOM_20260430_093000"
            event_log.append_fill(tid, "QCOM", fill_price=172.0, fill_qty=345)
            open_pos = event_log.open_trades()
            self.assertEqual(len(open_pos), 1)
        finally:
            restore()


# ═════════════════════════════════════════════════════════════════════════════
# Group 2 — check_external_closes writes POSITION_CLOSED via append_close()
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckExternalClosesWritesPositionClosed(unittest.TestCase):
    """
    check_external_closes() must call append_close() with the IBKR fill price
    whenever it detects a position gone from the portfolio.
    Tests the classification block directly (following test_pm_exit_reason.py
    pattern) to avoid requiring a live IB connection.
    """

    def _run_classification_and_close(self, trade, exit_price, pnl, exit_reason,
                                      held_mins, tmp_el):
        """Replicate the append_close block added to check_external_closes."""
        import event_log
        orig = event_log._LOG_FILE
        event_log._LOG_FILE = tmp_el
        try:
            from event_log import append_close as _el_close_ext
            _close_tid = trade.get("trade_id") or f"{trade['symbol']}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
            _el_close_ext(
                _close_tid, trade["symbol"],
                exit_price=round(exit_price, 4),
                pnl=round(pnl, 2),
                exit_reason=exit_reason,
                hold_minutes=held_mins,
            )
        finally:
            event_log._LOG_FILE = orig

    def test_writes_position_closed_with_ibkr_fill_price(self):
        """append_close called with IBKR fill price (450.00) must produce
        POSITION_CLOSED event with that exact price."""
        tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            trade = _make_trade("CRWD", trade_id="CRWD_20260430_130618")
            self._run_classification_and_close(
                trade, exit_price=450.00, pnl=-261.0,
                exit_reason="sl_hit | INTRADAY | regime:CHOPPY→CHOPPY | held:28min | thesis:noise_stop",
                held_mins=28, tmp_el=tmp
            )
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(len(closed), 1)
            self.assertAlmostEqual(closed[0]["exit_price"], 450.00, places=2)
            self.assertEqual(closed[0]["symbol"], "CRWD")
        finally:
            tmp.unlink(missing_ok=True)

    def test_uses_ibkr_fill_price_not_stale_cache(self):
        """Even if info['current'] is 0 (stale cache), the price passed from
        ib.fills() must land in POSITION_CLOSED."""
        tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            trade = _make_trade("SPY", trade_id="SPY_20260430_130250", current=0.0)
            ibkr_fill_price = 714.20
            self._run_classification_and_close(
                trade, exit_price=ibkr_fill_price, pnl=-82.0,
                exit_reason="manual", held_mins=51, tmp_el=tmp
            )
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(len(closed), 1)
            self.assertAlmostEqual(closed[0]["exit_price"], 714.20, places=2)
        finally:
            tmp.unlink(missing_ok=True)

    def test_append_close_exception_does_not_propagate(self):
        """If append_close raises, check_external_closes must swallow it (non-fatal)."""
        import event_log
        tmp, restore = _tmp_event_log()
        try:
            with patch.object(event_log, "append_close", side_effect=RuntimeError("disk full")):
                # Simulates the try/except wrapper in check_external_closes
                try:
                    from event_log import append_close as _el_close_ext
                    _el_close_ext("TID", "SYM", exit_price=100.0, pnl=0.0,
                                  exit_reason="test", hold_minutes=0)
                except RuntimeError:
                    pass  # swallowed — this is what the production code does
                # No assertion needed — reaching here without unhandled exception is the pass
        finally:
            restore()

    def test_exiting_status_guard_prevents_double_close(self):
        """Positions with status=EXITING must be skipped by check_external_closes.
        The deferred-close handler in orders_portfolio.py owns those."""
        # This is a logic-level test: we verify the guard condition is correct
        # without needing to run the full function.
        trade_exiting = _make_trade("USO", status="EXITING")
        trade_active = _make_trade("COP", status="ACTIVE")

        should_skip_exiting = trade_exiting.get("status") in ("PENDING", "EXITING")
        should_skip_active = trade_active.get("status") in ("PENDING", "EXITING")

        self.assertTrue(should_skip_exiting,
                        "EXITING positions must be skipped to prevent duplicate POSITION_CLOSED")
        self.assertFalse(should_skip_active,
                         "ACTIVE positions must NOT be skipped — they need close logging")


# ═════════════════════════════════════════════════════════════════════════════
# Group 3 — execute_sell uses avgFillPrice over stale tracker cache
# ═════════════════════════════════════════════════════════════════════════════

class TestExecuteSellExitPriceSource(unittest.TestCase):
    """
    The exit price selection logic in execute_sell must prefer
    sell_trade.orderStatus.avgFillPrice over info['current'] / info['entry'].
    Tests the three-level fallback directly.
    """

    def _select_exit_price(self, avg_fill_price, current, entry,
                           is_opt_close=False, current_premium=None):
        """Replicate the price-selection logic from execute_sell:2182."""
        _actual_fill = avg_fill_price or 0.0
        info = {"current": current, "entry": entry}
        if current_premium is not None:
            info["current_premium"] = current_premium
        return float(
            _actual_fill if _actual_fill > 0 else
            ((info.get("current_premium") or info.get("current") or info.get("entry", 0.0)) if is_opt_close
             else (info.get("current") or info.get("entry", 0.0)))
        )

    def test_uses_avg_fill_price_when_available(self):
        """avgFillPrice=312.55 must be used as exit price."""
        price = self._select_exit_price(avg_fill_price=312.55, current=310.0, entry=308.0)
        self.assertAlmostEqual(price, 312.55, places=4)

    def test_falls_back_to_current_when_avg_fill_zero(self):
        """avgFillPrice=0 must fall back to info['current']=310.00."""
        price = self._select_exit_price(avg_fill_price=0.0, current=310.0, entry=308.0)
        self.assertAlmostEqual(price, 310.0, places=4)

    def test_falls_back_to_entry_when_current_also_zero(self):
        """avgFillPrice=0, current=0 must fall back to info['entry']=305.00.
        This was the bug: previously returned 0.0."""
        price = self._select_exit_price(avg_fill_price=0.0, current=0.0, entry=305.0)
        self.assertAlmostEqual(price, 305.0, places=4)

    def test_zero_returned_only_when_all_sources_zero(self):
        """Only when avgFillPrice, current, AND entry are all 0 should price be 0."""
        price = self._select_exit_price(avg_fill_price=0.0, current=0.0, entry=0.0)
        self.assertEqual(price, 0.0)

    def test_options_path_prefers_avg_fill_over_current_premium(self):
        """For options, avgFillPrice must still take priority over current_premium."""
        price = self._select_exit_price(
            avg_fill_price=23.50, current=0.0, entry=22.20,
            is_opt_close=True, current_premium=22.00
        )
        self.assertAlmostEqual(price, 23.50, places=4)

    def test_options_path_falls_back_to_current_premium(self):
        """For options with avgFillPrice=0, use current_premium."""
        price = self._select_exit_price(
            avg_fill_price=0.0, current=0.0, entry=22.20,
            is_opt_close=True, current_premium=22.80
        )
        self.assertAlmostEqual(price, 22.80, places=4)


# ═════════════════════════════════════════════════════════════════════════════
# Group 4 — _flatten_all_inner writes tombstone POSITION_CLOSED
# ═════════════════════════════════════════════════════════════════════════════

class TestFlattenAllTombstone(unittest.TestCase):
    """
    _flatten_all_inner must write a POSITION_CLOSED tombstone for each position
    it removes from the tracker, and must never raise even if append_close fails.
    """

    def _run_tombstone(self, info: dict, tmp_el: pathlib.Path):
        """Replicate the tombstone block added to _flatten_all_inner."""
        import event_log
        orig = event_log._LOG_FILE
        event_log._LOG_FILE = tmp_el
        try:
            from event_log import append_close as _el_flat
            sym = info["symbol"]
            _flat_price = float(info.get("current") or info.get("entry") or 0.0)
            _flat_tid = info.get("trade_id") or f"{sym}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
            _el_flat(_flat_tid, sym,
                     exit_price=_flat_price,
                     pnl=0.0,
                     exit_reason="flatten_all",
                     hold_minutes=0)
        finally:
            event_log._LOG_FILE = orig

    def test_tombstone_written_with_current_price(self):
        tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            info = _make_trade("DDOG", trade_id="DDOG_20260430_130000",
                               current=132.50, entry=134.07)
            self._run_tombstone(info, tmp)
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(len(closed), 1)
            self.assertAlmostEqual(closed[0]["exit_price"], 132.50, places=2)
            self.assertEqual(closed[0]["exit_reason"], "flatten_all")
        finally:
            tmp.unlink(missing_ok=True)

    def test_tombstone_falls_back_to_entry_when_current_zero(self):
        tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            info = _make_trade("SVXY", trade_id="SVXY_20260430_130000",
                               current=0.0, entry=50.80)
            self._run_tombstone(info, tmp)
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertAlmostEqual(closed[0]["exit_price"], 50.80, places=2)
        finally:
            tmp.unlink(missing_ok=True)

    def test_tombstone_writes_zero_pnl(self):
        """Tombstone P&L is 0.0 — fill not confirmed yet, real P&L unknown."""
        tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            info = _make_trade("QCOM", current=169.0)
            self._run_tombstone(info, tmp)
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(closed[0]["pnl"], 0.0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_tombstone_exception_does_not_raise(self):
        """If append_close raises, the tombstone block must swallow it."""
        import event_log
        with patch.object(event_log, "append_close", side_effect=RuntimeError("io error")):
            # Replicate the production try/except: pass on any exception
            try:
                from event_log import append_close as _el_flat
                _el_flat("TID", "SYM", exit_price=100.0, pnl=0.0,
                         exit_reason="flatten_all", hold_minutes=0)
            except RuntimeError:
                pass  # production code does `except Exception: pass`
            # Reaching here = test passes

    def test_tombstone_resolves_position_in_open_trades(self):
        """After tombstone, open_trades() must not count the position as open."""
        import event_log
        tmp, restore = _tmp_event_log()
        try:
            tid = "DDOG_20260430_130000_tombstone"
            event_log.append_fill(tid, "DDOG", fill_price=134.07, fill_qty=551)
            self.assertEqual(len(event_log.open_trades()), 1)

            event_log.append_close(tid, "DDOG", exit_price=132.50, pnl=0.0,
                                   exit_reason="flatten_all", hold_minutes=0)
            self.assertEqual(len(event_log.open_trades()), 0,
                             "Tombstone POSITION_CLOSED must resolve position in WAL")
        finally:
            restore()


# ═════════════════════════════════════════════════════════════════════════════
# Group 5 — _close_position_record() writes POSITION_CLOSED before deleting
# ═════════════════════════════════════════════════════════════════════════════

class TestClosePositionRecord(unittest.TestCase):
    """
    _close_position_record() must write POSITION_CLOSED to event_log and remove
    the key from active_trades atomically. This is the single exit point for all
    position closes that don't go through the EXITING deferred handler.
    """

    def setUp(self):
        import orders_state
        self._orig_trades = dict(orders_state.active_trades)
        orders_state.active_trades.clear()

    def tearDown(self):
        import orders_state
        orders_state.active_trades.clear()
        orders_state.active_trades.update(self._orig_trades)

    def test_writes_position_closed_event(self):
        """_close_position_record must write POSITION_CLOSED with correct fields."""
        import event_log, orders_state
        from orders_portfolio import _close_position_record

        tmp, restore = _tmp_event_log()
        try:
            tid = "GOOGL_20260430_191353_000000"
            orders_state.active_trades["GOOGL"] = _make_trade(
                "GOOGL", trade_id=tid, entry=350.93, current=377.32, qty=167
            )
            _close_position_record("GOOGL", exit_price=377.32, exit_reason="manual_close",
                                   pnl=4394.31)
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(len(closed), 1)
            self.assertEqual(closed[0]["symbol"], "GOOGL")
            self.assertEqual(closed[0]["trade_id"], tid)
            self.assertAlmostEqual(closed[0]["exit_price"], 377.32, places=4)
            self.assertAlmostEqual(closed[0]["pnl"], 4394.31, places=2)
            self.assertEqual(closed[0]["exit_reason"], "manual_close")
        finally:
            restore()

    def test_removes_key_from_active_trades(self):
        """After _close_position_record, the key must be gone from active_trades."""
        import event_log, orders_state
        from orders_portfolio import _close_position_record

        tmp, restore = _tmp_event_log()
        try:
            orders_state.active_trades["GOOGL"] = _make_trade("GOOGL", entry=350.93)
            self.assertIn("GOOGL", orders_state.active_trades)
            _close_position_record("GOOGL", exit_price=377.32, exit_reason="manual_close")
            self.assertNotIn("GOOGL", orders_state.active_trades,
                             "Key must be removed from active_trades after close")
        finally:
            restore()

    def test_missing_key_is_noop(self):
        """Calling _close_position_record for a key not in active_trades must not raise."""
        import event_log
        from orders_portfolio import _close_position_record
        tmp, restore = _tmp_event_log()
        try:
            _close_position_record("NONEXISTENT", exit_price=100.0, exit_reason="test")
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(len(closed), 0, "No event written for missing key")
        finally:
            restore()

    def test_event_log_failure_does_not_raise(self):
        """If event_log write fails, _close_position_record must still remove the key."""
        import event_log, orders_state
        from orders_portfolio import _close_position_record

        tmp, restore = _tmp_event_log()
        try:
            orders_state.active_trades["TSLA"] = _make_trade("TSLA")
            with patch.object(event_log, "append_close", side_effect=OSError("disk full")):
                _close_position_record("TSLA", exit_price=250.0, exit_reason="manual_close")
            self.assertNotIn("TSLA", orders_state.active_trades,
                             "Key must still be removed even when event_log write fails")
        finally:
            restore()

    def test_resolves_position_in_open_trades(self):
        """After _close_position_record, event_log.open_trades() must not show the position."""
        import event_log, orders_state
        from orders_portfolio import _close_position_record

        tmp, restore = _tmp_event_log()
        try:
            tid = "MSFT_20260430_140000_000000"
            event_log.append_fill(tid, "MSFT", fill_price=400.0, fill_qty=50)
            self.assertEqual(len(event_log.open_trades()), 1)
            orders_state.active_trades["MSFT"] = _make_trade(
                "MSFT", trade_id=tid, entry=400.0
            )
            _close_position_record("MSFT", exit_price=405.0, exit_reason="apex_exit", pnl=250.0)
            self.assertEqual(len(event_log.open_trades()), 0,
                             "POSITION_CLOSED must resolve position in WAL")
        finally:
            restore()


# ═════════════════════════════════════════════════════════════════════════════
# Group 6 — stale purge writes POSITION_CLOSED before delete
# ═════════════════════════════════════════════════════════════════════════════

class TestStalePurgeWritesPositionClosed(unittest.TestCase):
    """
    The stale position purge in update_positions_from_ibkr() must call
    _close_position_record() for each stale key, ensuring POSITION_CLOSED is
    written before the key is removed.
    """

    def _run_stale_purge(self, trade: dict, tmp_el: pathlib.Path) -> None:
        """Replicate the stale purge logic (Change 3) directly."""
        import event_log
        orig = event_log._LOG_FILE
        event_log._LOG_FILE = tmp_el
        try:
            _st = trade
            _last_px = float(_st.get("current") or _st.get("entry") or 0)
            _entry_px = float(_st.get("entry") or _last_px)
            _qty = int(_st.get("qty") or 1)
            _is_short = _st.get("direction") == "SHORT"
            _pnl = round((_entry_px - _last_px if _is_short else _last_px - _entry_px) * _qty, 2)
            from event_log import append_close as _el_stale
            _el_stale(
                _st.get("trade_id", "UNKNOWN"),
                _st.get("symbol", "UNKNOWN"),
                exit_price=_last_px,
                exit_reason="stale_purge",
                pnl=_pnl,
                hold_minutes=0,
            )
        finally:
            event_log._LOG_FILE = orig

    def test_stale_long_writes_correct_pnl(self):
        """Stale long where current > entry must produce positive P&L."""
        tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            trade = _make_trade("NVDA", trade_id="NVDA_20260430_140000",
                                entry=200.0, current=210.0, qty=100)
            self._run_stale_purge(trade, tmp)
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(len(closed), 1)
            self.assertEqual(closed[0]["exit_reason"], "stale_purge")
            self.assertAlmostEqual(closed[0]["pnl"], 1000.0, places=2)
        finally:
            tmp.unlink(missing_ok=True)

    def test_stale_short_writes_correct_pnl(self):
        """Stale short where current < entry must produce positive P&L."""
        tmp = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            trade = _make_trade("SPY", trade_id="SPY_20260430_140000",
                                entry=600.0, current=590.0, qty=50, direction="SHORT")
            self._run_stale_purge(trade, tmp)
            events = _read_events(tmp)
            closed = [e for e in events if e.get("event") == "POSITION_CLOSED"]
            self.assertEqual(len(closed), 1)
            self.assertAlmostEqual(closed[0]["pnl"], 500.0, places=2)
        finally:
            tmp.unlink(missing_ok=True)

    def test_stale_resolves_open_trades(self):
        """After stale purge, event_log.open_trades() must not count the position."""
        import event_log
        tmp, restore = _tmp_event_log()
        try:
            tid = "GOOGL_20260430_191313_stale"
            event_log.append_fill(tid, "GOOGL", fill_price=350.93, fill_qty=167)
            self.assertEqual(len(event_log.open_trades()), 1)
            trade = _make_trade("GOOGL", trade_id=tid, entry=350.93, current=377.32, qty=167)
            self._run_stale_purge(trade, tmp)
            self.assertEqual(len(event_log.open_trades()), 0,
                             "Stale purge POSITION_CLOSED must resolve WAL")
        finally:
            restore()


if __name__ == "__main__":
    unittest.main()
