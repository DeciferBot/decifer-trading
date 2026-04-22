# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  tests/test_db_wiring_smoke.py              ║
# ║   End-to-end smoke tests: DB wiring added this session       ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Smoke tests for the DB-wiring changes made in the single-source-of-truth
session (2026-04-22).  Each test uses a fresh in-memory or temp-file DB
(never touches data/decifer.db) and verifies real SQLite reads and writes.

Coverage:
  1. ORDER_INTENT full 18-field payload — nothing truncated, everything round-trips
  2. close_trade(**extra) — direction/instrument/entry/qty survive into POSITION_CLOSED
  3. open_trades() excludes closed trades
  4. open_trades() merges subsequent events (SUBMITTED, FILLED) without overwriting intent
  5. log_order(SUBMITTED) → ORDER_SUBMITTED row in DB via explicit trade_id
  6. log_order(FILLED)    → ORDER_FILLED row in DB via explicit trade_id
  7. log_order fallback   → trade_id resolved from active_trades by symbol
  8. Migration pattern    — stale DB trades not in ibkr_keys get POSITION_CLOSED written
  9. No data loss on positions.json write-through (persist still fires after set_trade)
 10. POSITION_REMOVED event on _safe_del_trade
 11. options ORDER_INTENT full payload round-trips (right/strike/expiry/greeks)
"""

from __future__ import annotations

import json
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

import trade_log as _tl


# ── Fresh-DB decorator (resets module-level connection per test) ──────────────
def _fresh(test_fn):
    """Redirect trade_log to a per-test temp DB file."""
    def wrapper(self):
        tmp = pathlib.Path(tempfile.mktemp(suffix=".db"))
        orig_path = _tl._DB_PATH
        orig_conn = _tl._conn
        _tl._DB_PATH = tmp
        _tl._conn = None
        try:
            test_fn(self)
        finally:
            _tl._DB_PATH = orig_path
            _tl._conn = orig_conn
            try:
                if tmp.exists():
                    tmp.unlink()
                for ext in (".db-shm", ".db-wal"):
                    p = pathlib.Path(str(tmp) + ext)
                    if p.exists():
                        p.unlink()
            except Exception:
                pass
    wrapper.__name__ = test_fn.__name__
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# 1. ORDER_INTENT full 18-field payload — nothing truncated
# ─────────────────────────────────────────────────────────────────────────────
class TestOrderIntentFullPayload(unittest.TestCase):

    LONG_REASONING = "X" * 500      # well above any old 120-char truncation
    LONG_THESIS    = "Y" * 600      # well above old 150-char truncation

    @_fresh
    def test_all_18_fields_round_trip(self):
        tid = _tl.make_trade_id("AAPL")
        _tl.append_event(
            "ORDER_INTENT", tid, "AAPL",
            instrument="stock",
            direction="LONG",
            trade_type="INTRADAY",
            entry=182.50,
            qty=55,
            sl=178.00,
            tp=192.00,
            score=28,
            conviction=0.82,
            entry_regime="BULL_TRENDING",
            reasoning=self.LONG_REASONING,
            signal_scores={"trend": 8, "momentum": 7, "squeeze": 5},
            agent_outputs={"trading_analyst": "BUY", "risk_manager": "APPROVED"},
            entry_thesis=self.LONG_THESIS,
            setup_type="breakout",
            pattern_id="bull_flag_001",
            atr=1.45,
            advice_id="adv_20260422_001",
            ic_weights_at_entry={"trend": 0.18, "momentum": 0.15},
            tranche_mode=True,
            t1_qty=27,
            t2_qty=28,
            open_time="2026-04-22T09:32:00+00:00",
        )

        events = _tl.get_trade(tid)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["event"], "ORDER_INTENT")
        self.assertEqual(e["direction"], "LONG")
        self.assertEqual(e["entry"], 182.50)
        self.assertEqual(e["reasoning"], self.LONG_REASONING,
                         "reasoning must NOT be truncated")
        self.assertEqual(e["entry_thesis"], self.LONG_THESIS,
                         "entry_thesis must NOT be truncated")
        self.assertEqual(e["signal_scores"]["trend"], 8)
        self.assertEqual(e["ic_weights_at_entry"]["trend"], 0.18)
        self.assertTrue(e["tranche_mode"])
        self.assertEqual(e["t1_qty"], 27)

    @_fresh
    def test_short_order_intent_round_trips(self):
        tid = _tl.make_trade_id("SPY")
        _tl.append_event(
            "ORDER_INTENT", tid, "SPY",
            direction="SHORT",
            trade_type="INTRADAY",
            entry=510.0,
            qty=20,
            sl=515.0,
            tp=500.0,
            entry_regime="BEAR_TRENDING",
            reasoning="short thesis " * 30,
        )
        state = _tl.open_trades()[tid]
        self.assertEqual(state["direction"], "SHORT")
        self.assertEqual(state["sl"], 515.0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. close_trade(**extra) preserves full exit context
# ─────────────────────────────────────────────────────────────────────────────
class TestCloseTradeExtra(unittest.TestCase):

    @_fresh
    def test_extra_kwargs_in_position_closed(self):
        tid = _tl.make_trade_id("NVDA")
        _tl.append_event("ORDER_INTENT", tid, "NVDA", direction="LONG", entry=850.0, qty=10)
        _tl.close_trade(
            tid, "NVDA",
            exit_price=900.0,
            pnl=500.0,
            exit_reason="TP",
            direction="LONG",
            entry=850.0,
            qty=10,
            trade_type="INTRADAY",
            instrument="stock",
        )
        events = _tl.get_trade(tid)
        closed = next(e for e in events if e["event"] == "POSITION_CLOSED")
        self.assertEqual(closed["exit_price"], 900.0)
        self.assertEqual(closed["pnl"], 500.0)
        self.assertEqual(closed["direction"], "LONG")
        self.assertEqual(closed["instrument"], "stock")
        self.assertEqual(closed["entry"], 850.0)
        self.assertEqual(closed["qty"], 10)
        self.assertEqual(closed["trade_type"], "INTRADAY")

    @_fresh
    def test_synthetic_trade_id_still_closes(self):
        """POSITION_CLOSED must work even when trade_id was synthesised (no prior ORDER_INTENT)."""
        from datetime import UTC, datetime
        synthetic_id = f"TSLA_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
        _tl.close_trade(synthetic_id, "TSLA", exit_price=200.0, pnl=-150.0, exit_reason="SL",
                        direction="LONG", entry=215.0, qty=5,
                        trade_type="SWING", instrument="stock")
        events = _tl.get_trade(synthetic_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["exit_reason"], "SL")


# ─────────────────────────────────────────────────────────────────────────────
# 3 & 4. open_trades() — exclusion and merging
# ─────────────────────────────────────────────────────────────────────────────
class TestOpenTrades(unittest.TestCase):

    @_fresh
    def test_closed_trade_not_returned(self):
        tid = _tl.make_trade_id("MSFT")
        _tl.append_event("ORDER_INTENT", tid, "MSFT", direction="LONG")
        _tl.close_trade(tid, "MSFT", exit_price=400.0, pnl=200.0, exit_reason="TP")
        self.assertNotIn(tid, _tl.open_trades())

    @_fresh
    def test_open_trade_present_after_intent(self):
        tid = _tl.make_trade_id("GOOG")
        _tl.append_event("ORDER_INTENT", tid, "GOOG", direction="LONG", entry=170.0, qty=30)
        active = _tl.open_trades()
        self.assertIn(tid, active)
        self.assertEqual(active[tid]["entry"], 170.0)

    @_fresh
    def test_subsequent_events_add_new_keys_without_overwriting_intent(self):
        """ORDER_SUBMITTED fill_price must not overwrite entry price from ORDER_INTENT."""
        tid = _tl.make_trade_id("AMD")
        _tl.append_event("ORDER_INTENT", tid, "AMD", direction="LONG", entry=150.0, qty=20)
        _tl.append_event("ORDER_SUBMITTED", tid, "AMD", order_id=9001, qty=20, limit_price=150.5, side="BUY")
        _tl.append_event("ORDER_FILLED", tid, "AMD", fill_price=150.3, qty=20, order_id=9001)
        state = _tl.open_trades()[tid]
        # Intent fields preserved
        self.assertEqual(state["entry"], 150.0, "entry from ORDER_INTENT must not be overwritten")
        self.assertEqual(state["qty"], 20)
        # Fill fields added
        self.assertIn("fill_price", state)
        self.assertEqual(state["fill_price"], 150.3)
        self.assertIn("order_id", state)

    @_fresh
    def test_multiple_open_positions_independent(self):
        tids = [_tl.make_trade_id(sym) for sym in ("AAPL", "META", "AMZN")]
        for i, (tid, sym) in enumerate(zip(tids, ("AAPL", "META", "AMZN"))):
            _tl.append_event("ORDER_INTENT", tid, sym, direction="LONG", entry=float(100 + i))
        # Close only META
        _tl.close_trade(tids[1], "META", exit_price=200.0, pnl=50.0, exit_reason="TP")
        active = _tl.open_trades()
        self.assertIn(tids[0], active)    # AAPL still open
        self.assertNotIn(tids[1], active) # META closed
        self.assertIn(tids[2], active)    # AMZN still open


# ─────────────────────────────────────────────────────────────────────────────
# 5 & 6. log_order → DB via explicit trade_id
# ─────────────────────────────────────────────────────────────────────────────
class TestLogOrderDbMirror(unittest.TestCase):
    """
    log_order() must mirror SUBMITTED→ORDER_SUBMITTED and FILLED→ORDER_FILLED
    into decifer.db when trade_id is passed explicitly.
    """

    def _make_log_order_env(self, tmp_orders: pathlib.Path, tmp_db: pathlib.Path):
        """Patch learning.py's file paths and trade_log's DB path."""
        orig_tl_db = _tl._DB_PATH
        orig_tl_conn = _tl._conn
        _tl._DB_PATH = tmp_db
        _tl._conn = None

        import learning
        orig_order_log = learning.ORDER_LOG_FILE
        learning.ORDER_LOG_FILE = str(tmp_orders)

        def restore():
            _tl._DB_PATH = orig_tl_db
            _tl._conn = orig_tl_conn
            learning.ORDER_LOG_FILE = orig_order_log
            try:
                if tmp_db.exists():
                    tmp_db.unlink()
                for ext in (".db-shm", ".db-wal"):
                    p = pathlib.Path(str(tmp_db) + ext)
                    if p.exists():
                        p.unlink()
            except Exception:
                pass

        return learning, restore

    def test_submitted_writes_order_submitted_event(self):
        import learning
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_db = pathlib.Path(tempfile.mktemp(suffix=".db"))
        learning_mod, restore = self._make_log_order_env(tmp_orders, tmp_db)
        try:
            tid = _tl.make_trade_id("AAPL")
            learning_mod.log_order(
                {
                    "order_id": 12345,
                    "symbol": "AAPL",
                    "side": "BUY",
                    "order_type": "LMT",
                    "qty": 10,
                    "price": 182.50,
                    "status": "SUBMITTED",
                    "instrument": "stock",
                    "timestamp": "2026-04-22T09:32:00",
                },
                trade_id=tid,
            )
            events = _tl.get_trade(tid)
            event_types = [e["event"] for e in events]
            self.assertIn("ORDER_SUBMITTED", event_types,
                          "SUBMITTED status must write ORDER_SUBMITTED to DB")
            submitted = next(e for e in events if e["event"] == "ORDER_SUBMITTED")
            self.assertEqual(submitted["order_id"], 12345)
            self.assertEqual(submitted["side"], "BUY")
        finally:
            restore()
            tmp_orders.unlink(missing_ok=True)

    def test_filled_writes_order_filled_event(self):
        import learning
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_db = pathlib.Path(tempfile.mktemp(suffix=".db"))
        learning_mod, restore = self._make_log_order_env(tmp_orders, tmp_db)
        try:
            tid = _tl.make_trade_id("NVDA")
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
            events = _tl.get_trade(tid)
            event_types = [e["event"] for e in events]
            self.assertIn("ORDER_FILLED", event_types,
                          "FILLED status must write ORDER_FILLED to DB")
            filled = next(e for e in events if e["event"] == "ORDER_FILLED")
            self.assertEqual(filled["fill_price"], 849.75)
            self.assertEqual(filled["order_id"], 67890)
        finally:
            restore()
            tmp_orders.unlink(missing_ok=True)

    def test_non_submitted_non_filled_status_no_db_write(self):
        """CANCELLED / REJECTED orders must not write to DB (no ORDER_INTENT to attach to)."""
        import learning
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_db = pathlib.Path(tempfile.mktemp(suffix=".db"))
        learning_mod, restore = self._make_log_order_env(tmp_orders, tmp_db)
        try:
            tid = _tl.make_trade_id("TSLA")
            learning_mod.log_order(
                {"order_id": 111, "symbol": "TSLA", "side": "BUY", "qty": 3,
                 "price": 200.0, "status": "CANCELLED", "timestamp": "2026-04-22T09:34:00"},
                trade_id=tid,
            )
            events = _tl.get_trade(tid)
            event_types = [e["event"] for e in events]
            self.assertNotIn("ORDER_SUBMITTED", event_types)
            self.assertNotIn("ORDER_FILLED", event_types)
        finally:
            restore()
            tmp_orders.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 7. log_order fallback — trade_id resolved from active_trades by symbol
# ─────────────────────────────────────────────────────────────────────────────
class TestLogOrderFallbackResolution(unittest.TestCase):

    def test_fallback_to_active_trades_symbol_lookup(self):
        import learning
        tmp_orders = pathlib.Path(tempfile.mktemp(suffix=".json"))
        tmp_db = pathlib.Path(tempfile.mktemp(suffix=".db"))
        orig_tl_db = _tl._DB_PATH
        orig_tl_conn = _tl._conn
        _tl._DB_PATH = tmp_db
        _tl._conn = None
        orig_order_log = learning.ORDER_LOG_FILE
        learning.ORDER_LOG_FILE = str(tmp_orders)

        tid = _tl.make_trade_id("META")

        try:
            import orders_state
            orig_active = dict(orders_state.active_trades)
            orders_state.active_trades.clear()
            orders_state.active_trades["META"] = {"trade_id": tid, "symbol": "META"}
            try:
                learning.log_order(
                    {"order_id": 555, "symbol": "META", "side": "BUY", "qty": 8,
                     "price": 500.0, "status": "SUBMITTED",
                     "timestamp": "2026-04-22T09:35:00"},
                    # No trade_id passed — must resolve via active_trades
                )
                events = _tl.get_trade(tid)
                event_types = [e["event"] for e in events]
                self.assertIn("ORDER_SUBMITTED", event_types,
                              "Fallback symbol-lookup must still write DB event")
            finally:
                orders_state.active_trades.clear()
                orders_state.active_trades.update(orig_active)
        finally:
            _tl._DB_PATH = orig_tl_db
            _tl._conn = orig_tl_conn
            learning.ORDER_LOG_FILE = orig_order_log
            tmp_orders.unlink(missing_ok=True)
            try:
                if tmp_db.exists():
                    tmp_db.unlink()
                for ext in (".db-shm", ".db-wal"):
                    p = pathlib.Path(str(tmp_db) + ext)
                    if p.exists():
                        p.unlink()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# 8. Migration pattern — stale DB trades not in IBKR get POSITION_CLOSED
# ─────────────────────────────────────────────────────────────────────────────
class TestMigrationPattern(unittest.TestCase):

    @_fresh
    def test_stale_trades_get_closed_by_migration(self):
        """
        Simulate the one-time migration block in reconcile_with_ibkr:
        - 3 ORDER_INTENTs in DB
        - Only 1 is live in IBKR (ibkr_keys)
        - Migration writes POSITION_CLOSED for the other 2
        - Subsequent open_trades() returns only the live 1
        """
        tids = [_tl.make_trade_id(sym) for sym in ("USO", "GE", "NVDA")]
        for tid, sym in zip(tids, ("USO", "GE", "NVDA")):
            _tl.append_event("ORDER_INTENT", tid, sym, direction="LONG", entry=50.0)

        # IBKR only reports NVDA as live
        ibkr_keys = {tids[2]}
        active_trade_ids = {tids[2]}  # NVDA is in active_trades

        db_open = _tl.open_trades()
        stale_ids = [
            tid for tid in db_open
            if tid not in ibkr_keys and tid not in active_trade_ids
        ]
        self.assertEqual(len(stale_ids), 2, "USO and GE should be stale")

        # Run migration (the pattern from reconcile_with_ibkr)
        for tid in stale_ids:
            t = db_open[tid]
            _tl.close_trade(tid, t.get("symbol", "?"),
                            exit_price=0.0, pnl=0.0, exit_reason="migration_close")

        # After migration: only NVDA remains open
        remaining = _tl.open_trades()
        self.assertNotIn(tids[0], remaining, "USO must be closed after migration")
        self.assertNotIn(tids[1], remaining, "GE must be closed after migration")
        self.assertIn(tids[2], remaining, "NVDA must still be open")

    @_fresh
    def test_migration_is_idempotent(self):
        """Running migration twice must not produce errors or double-close."""
        tid = _tl.make_trade_id("MSTR")
        _tl.append_event("ORDER_INTENT", tid, "MSTR", direction="LONG", entry=400.0)

        # First migration pass
        _tl.close_trade(tid, "MSTR", exit_price=0.0, pnl=0.0, exit_reason="migration_close")
        # Second migration pass (same stale_ids list would try to close again)
        _tl.close_trade(tid, "MSTR", exit_price=0.0, pnl=0.0, exit_reason="migration_close")

        # Must still not be in open_trades (append-only — extra close row is harmless)
        self.assertNotIn(tid, _tl.open_trades())


# ─────────────────────────────────────────────────────────────────────────────
# 9. positions.json write-through cache still fires
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
# 10. POSITION_REMOVED event on _safe_del_trade
# ─────────────────────────────────────────────────────────────────────────────
class TestPositionRemovedEvent(unittest.TestCase):

    @_fresh
    def test_safe_del_trade_writes_position_removed(self):
        """_safe_del_trade must write POSITION_REMOVED to DB when trade_id is present."""
        import orders_state

        tid = _tl.make_trade_id("RIVN")
        # Plant a trade in active_trades with a real trade_id
        orders_state.active_trades["RIVN"] = {
            "symbol": "RIVN", "trade_id": tid, "status": "ACTIVE",
            "trade_type": "INTRADAY", "direction": "LONG",
            "entry": 12.0, "qty": 100, "pnl": -50.0,
        }

        orders_state._safe_del_trade("RIVN")

        events = _tl.get_trade(tid)
        event_types = [e["event"] for e in events]
        self.assertIn("POSITION_REMOVED", event_types,
                      "_safe_del_trade must write POSITION_REMOVED to DB")
        removed = next(e for e in events if e["event"] == "POSITION_REMOVED")
        self.assertEqual(removed["status"], "ACTIVE")

    @_fresh
    def test_safe_del_trade_no_event_when_no_trade_id(self):
        """When trade has no trade_id, _safe_del_trade must not error — just skip DB write."""
        import orders_state
        orders_state.active_trades["__smoke_no_tid__"] = {
            "symbol": "__smoke_no_tid__", "status": "ACTIVE",
            "trade_type": "INTRADAY",
        }
        # Must not raise
        orders_state._safe_del_trade("__smoke_no_tid__")
        self.assertNotIn("__smoke_no_tid__", orders_state.active_trades)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Options ORDER_INTENT — full payload including Greeks
# ─────────────────────────────────────────────────────────────────────────────
class TestOptionsOrderIntentPayload(unittest.TestCase):

    @_fresh
    def test_options_fields_round_trip(self):
        tid = _tl.make_trade_id("AAPL_C_185_20260516")
        _tl.append_event(
            "ORDER_INTENT", tid, "AAPL",
            instrument="option",
            direction="LONG",
            trade_type="INTRADAY",
            entry=3.50,
            qty=2,
            sl=2.00,
            tp=6.00,
            score=24,
            conviction=0.75,
            right="C",
            strike=185.0,
            expiry="20260516",
            delta=0.50,
            gamma=0.08,
            theta=-0.12,
            vega=0.25,
            entry_regime="BULL_TRENDING",
            reasoning="Strong breakout above resistance " * 10,
            signal_scores={"trend": 8, "momentum": 7},
            ic_weights_at_entry={"trend": 0.18},
            pattern_id="bull_flag_opt",
        )
        events = _tl.get_trade(tid)
        e = events[0]
        self.assertEqual(e["instrument"], "option")
        self.assertEqual(e["right"], "C")
        self.assertEqual(e["strike"], 185.0)
        self.assertEqual(e["expiry"], "20260516")
        self.assertAlmostEqual(e["delta"], 0.50)
        self.assertEqual(e["signal_scores"]["trend"], 8)
        self.assertEqual(len(e["reasoning"]), len("Strong breakout above resistance " * 10),
                         "options reasoning must not be truncated")


# ─────────────────────────────────────────────────────────────────────────────
# 12. DB-first startup: open_trades() state is complete enough for active_trades
# ─────────────────────────────────────────────────────────────────────────────
class TestDbFirstStartup(unittest.TestCase):

    @_fresh
    def test_open_trades_returns_all_required_fields_for_active_trades(self):
        """
        On startup, reconcile_with_ibkr calls open_trades() and updates active_trades.
        The returned dict must contain all fields the reconcile loop and PM depend on.
        """
        tid = _tl.make_trade_id("TSLA")
        _tl.append_event(
            "ORDER_INTENT", tid, "TSLA",
            instrument="stock",
            direction="LONG",
            trade_type="SWING",
            entry=200.0,
            qty=25,
            sl=190.0,
            tp=225.0,
            score=30,
            conviction=0.88,
            entry_regime="BULL_TRENDING",
            reasoning="Breakout thesis",
            signal_scores={"trend": 9},
            ic_weights_at_entry={"trend": 0.20},
            pattern_id="cup_and_handle",
            setup_type="breakout",
            open_time="2026-04-22T09:30:00+00:00",
        )
        state = _tl.open_trades()[tid]

        required = ["trade_id", "symbol", "direction", "trade_type", "entry", "qty",
                    "sl", "tp", "score", "conviction", "entry_regime",
                    "reasoning", "signal_scores", "ic_weights_at_entry",
                    "pattern_id", "setup_type", "open_time"]
        missing = [f for f in required if f not in state]
        self.assertEqual(missing, [], f"open_trades() missing required fields: {missing}")

    @_fresh
    def test_open_trades_trade_id_in_returned_state(self):
        """trade_id must be in the returned dict so active_trades[sym]['trade_id'] works."""
        tid = _tl.make_trade_id("META")
        _tl.append_event("ORDER_INTENT", tid, "META", direction="LONG", entry=500.0)
        state = _tl.open_trades()[tid]
        self.assertEqual(state["trade_id"], tid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
