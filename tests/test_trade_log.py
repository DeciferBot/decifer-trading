# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  tests/test_trade_log.py                    ║
# ║   Tests for the SQLite WAL-backed trade and signal log       ║
# ╚══════════════════════════════════════════════════════════════╝
"""Tests for trade_log.py — append-only SQLite event log."""

from __future__ import annotations

import pathlib
import tempfile
import threading
import unittest

import trade_log as _tl


def _fresh(test_fn):
    """Decorator: give each test a fresh DB by resetting the module globals."""
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
            if tmp.exists():
                tmp.unlink()
    wrapper.__name__ = test_fn.__name__
    return wrapper


class TestMakeTradeId(unittest.TestCase):
    def test_format(self):
        tid = _tl.make_trade_id("AAPL")
        assert tid.startswith("AAPL_"), tid
        parts = tid.split("_")
        assert len(parts) == 4  # AAPL, YYYYMMDD, HHMMSS, ffffff

    def test_unique(self):
        ids = {_tl.make_trade_id("AAPL") for _ in range(20)}
        assert len(ids) == 20


class TestAppendEvent(unittest.TestCase):
    @_fresh
    def test_basic_append(self):
        tid = _tl.make_trade_id("MSFT")
        _tl.append_event("ORDER_INTENT", tid, "MSFT", direction="LONG", score=30)
        events = _tl.get_trade(tid)
        assert len(events) == 1
        assert events[0]["event"] == "ORDER_INTENT"
        assert events[0]["direction"] == "LONG"

    @_fresh
    def test_append_never_raises(self):
        # Corrupt the connection so all writes fail — should swallow silently
        _tl._get_conn()  # initialise
        _tl._conn.close()
        _tl.append_event("ORDER_INTENT", "BAD_ID", "X", score=1)  # must not raise


class TestOpenTrades(unittest.TestCase):
    @_fresh
    def test_open_after_intent(self):
        tid = _tl.make_trade_id("NVDA")
        _tl.append_event("ORDER_INTENT", tid, "NVDA", direction="LONG")
        active = _tl.open_trades()
        assert tid in active
        assert active[tid]["symbol"] == "NVDA"

    @_fresh
    def test_closed_not_in_open(self):
        tid = _tl.make_trade_id("NVDA")
        _tl.append_event("ORDER_INTENT", tid, "NVDA", direction="LONG")
        _tl.close_trade(tid, "NVDA", 200.0, 50.0, "TP")
        assert tid not in _tl.open_trades()

    @_fresh
    def test_multiple_symbols_independent(self):
        t1 = _tl.make_trade_id("AAPL")
        t2 = _tl.make_trade_id("GOOG")
        _tl.append_event("ORDER_INTENT", t1, "AAPL", direction="LONG")
        _tl.append_event("ORDER_INTENT", t2, "GOOG", direction="SHORT")
        _tl.close_trade(t1, "AAPL", 180.0, -10.0, "SL")
        active = _tl.open_trades()
        assert t1 not in active
        assert t2 in active

    @_fresh
    def test_later_events_merge_into_state(self):
        tid = _tl.make_trade_id("TSLA")
        _tl.append_event("ORDER_INTENT", tid, "TSLA", direction="LONG", qty=10)
        _tl.append_event("ORDER_FILLED", tid, "TSLA", fill_price=250.0)
        state = _tl.open_trades()[tid]
        # ORDER_FILLED adds fill_price (new key)
        assert state["fill_price"] == 250.0
        # ORDER_INTENT fields preserved
        assert state["qty"] == 10

    @_fresh
    def test_intent_fields_not_overwritten_by_later_events(self):
        tid = _tl.make_trade_id("AMD")
        _tl.append_event("ORDER_INTENT", tid, "AMD", direction="LONG", qty=5)
        _tl.append_event("POSITION_UPDATED", tid, "AMD", qty=99)  # should not overwrite
        state = _tl.open_trades()[tid]
        assert state["qty"] == 5  # original intent preserved


class TestCloseTrade(unittest.TestCase):
    @_fresh
    def test_close_trade_appends_position_closed(self):
        tid = _tl.make_trade_id("SPY")
        _tl.append_event("ORDER_INTENT", tid, "SPY", direction="LONG")
        _tl.close_trade(tid, "SPY", 500.0, 100.0, "TP")
        events = _tl.get_trade(tid)
        events_by_type = {e["event"]: e for e in events}
        assert "POSITION_CLOSED" in events_by_type
        closed = events_by_type["POSITION_CLOSED"]
        assert closed["exit_price"] == 500.0
        assert closed["pnl"] == 100.0
        assert closed["exit_reason"] == "TP"


class TestAppendSignal(unittest.TestCase):
    @_fresh
    def test_signal_written_to_db(self):
        _tl.append_signal("scan_001", "AAPL", 30, "LONG", "BULL_TRENDING", {"trend": 8, "momentum": 6})
        conn = _tl._get_conn()
        rows = conn.execute("SELECT * FROM signal_scores WHERE scan_id='scan_001'").fetchall()
        assert len(rows) == 1
        assert rows[0][3] == "AAPL"  # symbol column

    @_fresh
    def test_multiple_signals_same_scan(self):
        for sym in ("AAPL", "MSFT", "NVDA"):
            _tl.append_signal("scan_002", sym, 25, "LONG", "CHOPPY", {"trend": 5})
        conn = _tl._get_conn()
        count = conn.execute("SELECT COUNT(*) FROM signal_scores WHERE scan_id='scan_002'").fetchone()[0]
        assert count == 3


class TestThreadSafety(unittest.TestCase):
    @_fresh
    def test_concurrent_appends_no_corruption(self):
        errors = []
        tids = []
        lock = threading.Lock()

        def worker(i):
            try:
                tid = _tl.make_trade_id(f"SYM{i}")
                _tl.append_event("ORDER_INTENT", tid, f"SYM{i}", score=i)
                with lock:
                    tids.append(tid)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent writes: {errors}"
        active = _tl.open_trades()
        for tid in tids:
            assert tid in active


if __name__ == "__main__":
    unittest.main()
