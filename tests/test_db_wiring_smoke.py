# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  tests/test_db_wiring_smoke.py              ║
# ║   Smoke tests: event_log + orders_state wiring               ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Smoke tests for the JSONL persistence layer (event_log + orders_state).
trade_log.py and trade_store.py were deleted in the 2026-04-28 migration.

Coverage:
  1. log_order(FILLED) → ORDER_FILLED written to event_log JSONL
  2. log_order(SUBMITTED) → silently ignored, no event_log write
  3. log_order(CANCELLED) → silently ignored, no event_log write
  4. log_order fallback → trade_id resolved from active_trades by symbol
  5. _safe_set_trade must still call _persist_positions (write-through cache)
  6. _safe_del_trade removes key from active_trades
  7. _safe_del_trade with no trade_id → no error
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

# ── Stub heavy deps before any Decifer import ─────────────────────────────────
for _mod in ("anthropic", "ib_async", "ib_async.objects"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.modules["ib_async"].IB = MagicMock
sys.modules["ib_async"].LimitOrder = MagicMock
sys.modules["ib_async"].MarketOrder = MagicMock
sys.modules["ib_async"].Stock = MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# 1–3. log_order → event_log JSONL via explicit trade_id
# ─────────────────────────────────────────────────────────────────────────────
class TestLogOrderDbMirror(unittest.TestCase):
    """
    log_order() must write ORDER_FILLED to event_log JSONL (not DB) when
    status=FILLED and trade_id is passed explicitly.
    SUBMITTED and CANCELLED statuses are silently ignored.
    """

    def _make_log_order_env(self, tmp_orders: pathlib.Path, tmp_el: pathlib.Path):
        """Patch learning.py's order log and event_log's file path."""
        import event_log
        import learning

        orig_order_log = learning.ORDER_LOG_FILE
        orig_el_file = event_log._LOG_FILE
        learning.ORDER_LOG_FILE = str(tmp_orders)
        event_log._LOG_FILE = tmp_el

        def restore():
            learning.ORDER_LOG_FILE = orig_order_log
            event_log._LOG_FILE = orig_el_file
            tmp_el.unlink(missing_ok=True)

        return learning, restore

    def test_filled_writes_order_filled_to_event_log(self):
        import json as _json
        import event_log
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_el = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        learning_mod, restore = self._make_log_order_env(tmp_orders, tmp_el)
        try:
            tid = "NVDA_20260422_093300"
            learning_mod.log_order(
                {
                    "order_id": 67890,
                    "symbol": "NVDA",
                    "side": "BUY",
                    "order_type": "LMT",
                    "qty": 5,
                    "price": 850.0,
                    "fill_price": 849.75,
                    "status": "FILLED",
                    "instrument": "stock",
                    "timestamp": "2026-04-22T09:33:00",
                },
                trade_id=tid,
            )
            records = [_json.loads(l) for l in tmp_el.read_text().splitlines() if l.strip()]
            event_types = [r["event"] for r in records]
            self.assertIn("ORDER_FILLED", event_types,
                          "FILLED status must write ORDER_FILLED to event_log")
            filled = next(r for r in records if r["event"] == "ORDER_FILLED")
            self.assertEqual(filled["fill_price"], 849.75)
            self.assertEqual(filled["order_id"], 67890)
            self.assertEqual(filled["trade_id"], tid)
        finally:
            restore()
            tmp_orders.unlink(missing_ok=True)

    def test_submitted_status_does_not_write_to_event_log(self):
        """SUBMITTED status is not tracked — no event_log write."""
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_el = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        learning_mod, restore = self._make_log_order_env(tmp_orders, tmp_el)
        try:
            tid = "AAPL_20260422_093200"
            learning_mod.log_order(
                {"order_id": 12345, "symbol": "AAPL", "side": "BUY", "qty": 10,
                 "price": 182.50, "status": "SUBMITTED", "timestamp": "2026-04-22T09:32:00"},
                trade_id=tid,
            )
            self.assertFalse(tmp_el.exists(), "SUBMITTED must not create event_log entries")
        finally:
            restore()
            tmp_orders.unlink(missing_ok=True)

    def test_cancelled_status_does_not_write_to_event_log(self):
        """CANCELLED status must not write to event_log."""
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_el = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        learning_mod, restore = self._make_log_order_env(tmp_orders, tmp_el)
        try:
            tid = "TSLA_20260422_093400"
            learning_mod.log_order(
                {"order_id": 111, "symbol": "TSLA", "side": "BUY", "qty": 3,
                 "price": 200.0, "status": "CANCELLED", "timestamp": "2026-04-22T09:34:00"},
                trade_id=tid,
            )
            self.assertFalse(tmp_el.exists(), "CANCELLED must not create event_log entries")
        finally:
            restore()
            tmp_orders.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4. log_order fallback — trade_id resolved from active_trades by symbol
# ─────────────────────────────────────────────────────────────────────────────
class TestLogOrderFallbackResolution(unittest.TestCase):

    def test_filled_fallback_resolves_trade_id_from_active_trades(self):
        """When trade_id is not passed, FILLED status resolves it from active_trades."""
        import json as _json
        import event_log
        import learning
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_el = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
        orig_order_log = learning.ORDER_LOG_FILE
        orig_el_file = event_log._LOG_FILE
        learning.ORDER_LOG_FILE = str(tmp_orders)
        event_log._LOG_FILE = tmp_el

        tid = "META_20260422_093500"
        try:
            import orders_state
            orig_active = dict(orders_state.active_trades)
            orders_state.active_trades.clear()
            orders_state.active_trades["META"] = {"trade_id": tid, "symbol": "META"}
            try:
                learning.log_order(
                    {"order_id": 555, "symbol": "META", "side": "BUY", "qty": 8,
                     "price": 500.0, "fill_price": 499.50, "status": "FILLED",
                     "timestamp": "2026-04-22T09:35:00"},
                    # No trade_id passed — must resolve via active_trades
                )
                records = [_json.loads(l) for l in tmp_el.read_text().splitlines() if l.strip()]
                event_types = [r["event"] for r in records]
                self.assertIn("ORDER_FILLED", event_types,
                              "Fallback symbol-lookup must still write ORDER_FILLED to event_log")
            finally:
                orders_state.active_trades.clear()
                orders_state.active_trades.update(orig_active)
        finally:
            learning.ORDER_LOG_FILE = orig_order_log
            event_log._LOG_FILE = orig_el_file
            tmp_orders.unlink(missing_ok=True)
            tmp_el.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 5. positions.json write-through cache still fires
# ─────────────────────────────────────────────────────────────────────────────
class TestPositionsJsonWriteThrough(unittest.TestCase):

    def test_safe_set_trade_persists_to_disk(self):
        """_safe_set_trade must still call _persist_positions (write-through cache)."""
        import orders_state

        persist_called = threading.Event()
        orig_persist = orders_state._persist_positions

        def mock_persist():
            persist_called.set()
            orig_persist()

        with patch.object(orders_state, "_persist_positions", side_effect=mock_persist):
            orders_state._safe_set_trade("__smoke_test_AAPL__", {
                "symbol": "AAPL", "trade_type": "INTRADAY", "direction": "LONG",
                "entry": 182.0, "qty": 10, "status": "ACTIVE",
                "trade_id": "AAPL_20260422_smoke",
            })

        self.assertTrue(persist_called.is_set(),
                        "_persist_positions must be called after _safe_set_trade")

        # Cleanup
        orders_state.active_trades.pop("__smoke_test_AAPL__", None)


# ─────────────────────────────────────────────────────────────────────────────
# 6–7. _safe_del_trade
# ─────────────────────────────────────────────────────────────────────────────
class TestPositionRemovedEvent(unittest.TestCase):

    def test_safe_del_trade_removes_from_active_trades(self):
        """_safe_del_trade must remove the key from active_trades."""
        import orders_state

        orders_state.active_trades["RIVN"] = {
            "symbol": "RIVN", "trade_id": "RIVN_20260422", "status": "ACTIVE",
            "trade_type": "INTRADAY", "direction": "LONG",
            "entry": 12.0, "qty": 100, "pnl": -50.0,
        }

        orders_state._safe_del_trade("RIVN")

        self.assertNotIn("RIVN", orders_state.active_trades,
                         "_safe_del_trade must remove the key from active_trades")

    def test_safe_del_trade_no_event_when_no_trade_id(self):
        """When trade has no trade_id, _safe_del_trade must not error — just remove."""
        import orders_state
        orders_state.active_trades["__smoke_no_tid__"] = {
            "symbol": "__smoke_no_tid__", "status": "ACTIVE",
            "trade_type": "INTRADAY",
        }
        # Must not raise
        orders_state._safe_del_trade("__smoke_no_tid__")
        self.assertNotIn("__smoke_no_tid__", orders_state.active_trades)


if __name__ == "__main__":
    unittest.main(verbosity=2)
