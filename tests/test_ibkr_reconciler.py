"""Tests for ibkr_reconciler — IBKR fill matching and reconciled record building."""
from __future__ import annotations

import json
import math
import time
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build fake IBKR Fill objects
# ---------------------------------------------------------------------------

def _make_fill(symbol: str, side: str, avg_price: float, order_id: int = 0,
               exec_time: str = "20260430 14:30:22") -> MagicMock:
    fill = MagicMock()
    fill.contract.symbol = symbol
    fill.execution.side = side
    fill.execution.avgPrice = avg_price
    fill.execution.orderId = order_id
    fill.execution.time = exec_time
    return fill


def _make_ib(fills: list) -> MagicMock:
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.reqExecutions.return_value = fills
    return ib


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def event_log_file(tmp_path):
    """Write a minimal trade_events.jsonl with one open + one closed trade."""
    f = tmp_path / "trade_events.jsonl"
    records = [
        {
            "ts": "2026-04-30T14:30:22.000000+00:00",
            "event": "ORDER_INTENT",
            "trade_id": "AAPL_20260430_143022_001",
            "symbol": "AAPL",
            "direction": "LONG",
            "trade_type": "INTRADAY",
            "instrument": "stock",
            "intended_price": 185.00,
            "qty": 10,
            "sl": 183.0,
            "tp": 188.0,
            "regime": "TRENDING_UP",
            "signal_scores": {"directional": 7.5},
            "conviction": 0.8,
            "reasoning": "breakout above resistance",
            "score": 72.0,
        },
        {
            "ts": "2026-04-30T14:30:25.000000+00:00",
            "event": "ORDER_FILLED",
            "trade_id": "AAPL_20260430_143022_001",
            "symbol": "AAPL",
            "fill_price": 185.10,
            "fill_qty": 10,
            "order_id": 1001,
        },
        {
            "ts": "2026-04-30T15:45:00.000000+00:00",
            "event": "POSITION_CLOSED",
            "trade_id": "AAPL_20260430_143022_001",
            "symbol": "AAPL",
            "exit_price": 188.00,
            "pnl": 29.00,
            "exit_reason": "tp_hit",
            "hold_minutes": 74,
        },
    ]
    with open(f, "w") as fp:
        for r in records:
            fp.write(json.dumps(r) + "\n")
    return f


@pytest.fixture()
def out_file(tmp_path):
    return tmp_path / "reconciled_trades.jsonl"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_exact_match(event_log_file, out_file):
    """IBKR fill with matching order_id produces ibkr_match='exact', uses IBKR prices."""
    entry_fill = _make_fill("AAPL", "BOT", 185.10, order_id=1001, exec_time="20260430 14:30:25")
    exit_fill = _make_fill("AAPL", "SLD", 188.05, order_id=1002, exec_time="20260430 15:45:02")
    ib = _make_ib([entry_fill, exit_fill])

    import ibkr_reconciler
    with (
        patch.object(ibkr_reconciler, "_EVENTS_FILE", event_log_file),
        patch.object(ibkr_reconciler, "_OUT_FILE", out_file),
        patch("ibkr_reconciler._load_commission_index", return_value={}),
        patch("ibkr_reconciler._cache_ts", 0.0),
    ):
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        records = ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")

    assert len(records) == 1
    r = records[0]
    assert r["ibkr_match"] == "exact"
    assert r["entry_price"] == pytest.approx(185.10)
    assert r["exit_price"] == pytest.approx(188.05)
    assert r["symbol"] == "AAPL"
    assert r["direction"] == "LONG"
    assert r["score"] == pytest.approx(72.0)
    assert r["regime"] == "TRENDING_UP"
    assert r["exit_reason"] == "tp_hit"


def test_fuzzy_match(event_log_file, out_file):
    """Fill with order_id=0 in event_log still matches by symbol+side+time."""
    # Patch ORDER_FILLED to have order_id=0
    records = json.loads(event_log_file.read_text().splitlines()[1])
    records["order_id"] = 0
    lines = event_log_file.read_text().splitlines()
    lines[1] = json.dumps(records)
    event_log_file.write_text("\n".join(lines) + "\n")

    entry_fill = _make_fill("AAPL", "BOT", 185.12, order_id=9999, exec_time="20260430 14:30:26")
    exit_fill = _make_fill("AAPL", "SLD", 187.90, order_id=9998, exec_time="20260430 15:44:58")
    ib = _make_ib([entry_fill, exit_fill])

    import ibkr_reconciler
    with (
        patch.object(ibkr_reconciler, "_EVENTS_FILE", event_log_file),
        patch.object(ibkr_reconciler, "_OUT_FILE", out_file),
        patch("ibkr_reconciler._load_commission_index", return_value={}),
    ):
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        records = ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")

    assert len(records) == 1
    r = records[0]
    assert r["ibkr_match"] == "fuzzy"
    assert r["entry_price"] == pytest.approx(185.12)
    assert r["exit_price"] == pytest.approx(187.90)


def test_unmatched_fallback(event_log_file, out_file):
    """IBKR offline (returns empty) → event_log data, ibkr_match='unmatched'."""
    ib = MagicMock()
    ib.isConnected.return_value = False  # simulate offline

    import ibkr_reconciler
    with (
        patch.object(ibkr_reconciler, "_EVENTS_FILE", event_log_file),
        patch.object(ibkr_reconciler, "_OUT_FILE", out_file),
        patch("ibkr_reconciler._load_commission_index", return_value={}),
    ):
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        records = ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")

    assert len(records) == 1
    r = records[0]
    assert r["ibkr_match"] == "unmatched"
    assert r["reconciled"] is False
    # Falls back to event_log fill_price
    assert r["entry_price"] == pytest.approx(185.10)
    assert r["exit_price"] == pytest.approx(188.00)


def test_no_duplicate_writes(event_log_file, out_file):
    """Calling reconcile_closes twice does not write duplicate records."""
    entry_fill = _make_fill("AAPL", "BOT", 185.10, order_id=1001)
    exit_fill = _make_fill("AAPL", "SLD", 188.05, order_id=1002)
    ib = _make_ib([entry_fill, exit_fill])

    import ibkr_reconciler
    kwargs = dict(
        _EVENTS_FILE=event_log_file,
        _OUT_FILE=out_file,
    )
    with (
        patch.object(ibkr_reconciler, "_EVENTS_FILE", event_log_file),
        patch.object(ibkr_reconciler, "_OUT_FILE", out_file),
        patch("ibkr_reconciler._load_commission_index", return_value={}),
    ):
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")
        # Invalidate cache to force a second write attempt
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")

    written = [json.loads(l) for l in out_file.read_text().splitlines() if l.strip()]
    trade_ids = [r["trade_id"] for r in written]
    assert len(trade_ids) == len(set(trade_ids)), "Duplicate trade_id written to reconciled_trades.jsonl"


def test_commission_applied(event_log_file, out_file):
    """Commission from orders.json reduces realized_pnl_net."""
    entry_fill = _make_fill("AAPL", "BOT", 185.10, order_id=1001, exec_time="20260430 14:30:25")
    exit_fill = _make_fill("AAPL", "SLD", 188.05, order_id=1002, exec_time="20260430 15:45:02")
    ib = _make_ib([entry_fill, exit_fill])

    commission_idx = {
        1001: {"commission": 0.65, "realized_pnl": None},
        1002: {"commission": 0.65, "realized_pnl": 29.50},
    }

    import ibkr_reconciler
    with (
        patch.object(ibkr_reconciler, "_EVENTS_FILE", event_log_file),
        patch.object(ibkr_reconciler, "_OUT_FILE", out_file),
        patch("ibkr_reconciler._load_commission_index", return_value=commission_idx),
    ):
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        records = ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")

    r = records[0]
    assert r["commission_entry"] == pytest.approx(0.65)
    assert r["commission_exit"] == pytest.approx(0.65)
    # realized_pnl_net = ibkr_gross (29.50) - total_commission (1.30)
    assert r["realized_pnl_net"] == pytest.approx(29.50 - 1.30)


def test_cache_hit(event_log_file, out_file):
    """Second call within 60 s returns cached data without calling reqExecutions again."""
    ib = _make_ib([])

    import ibkr_reconciler
    with (
        patch.object(ibkr_reconciler, "_EVENTS_FILE", event_log_file),
        patch.object(ibkr_reconciler, "_OUT_FILE", out_file),
        patch("ibkr_reconciler._load_commission_index", return_value={}),
    ):
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")
        call_count_after_first = ib.reqExecutions.call_count
        ibkr_reconciler.reconcile_closes(ib, cutover_date="2026-04-30")

    # reqExecutions should not have been called a second time
    assert ib.reqExecutions.call_count == call_count_after_first


def test_manual_repair_excluded(tmp_path):
    """Trades with exit_reason='manual_repair' are not included in reconciled output."""
    f = tmp_path / "trade_events.jsonl"
    records = [
        {"ts": "2026-04-30T14:00:00+00:00", "event": "ORDER_INTENT",
         "trade_id": "X_001", "symbol": "X", "direction": "LONG",
         "trade_type": "INTRADAY", "instrument": "stock",
         "intended_price": 10.0, "qty": 1, "sl": 9.0, "tp": 11.0,
         "regime": "UNKNOWN", "signal_scores": {}, "conviction": 0.5,
         "reasoning": "", "score": 30.0},
        {"ts": "2026-04-30T14:00:01+00:00", "event": "ORDER_FILLED",
         "trade_id": "X_001", "symbol": "X",
         "fill_price": 10.0, "fill_qty": 1, "order_id": 0},
        {"ts": "2026-04-30T15:00:00+00:00", "event": "POSITION_CLOSED",
         "trade_id": "X_001", "symbol": "X",
         "exit_price": 10.5, "pnl": 0.5, "exit_reason": "manual_repair",
         "hold_minutes": 60},
    ]
    with open(f, "w") as fp:
        for r in records:
            fp.write(json.dumps(r) + "\n")

    out = tmp_path / "reconciled.jsonl"
    import ibkr_reconciler
    with (
        patch.object(ibkr_reconciler, "_EVENTS_FILE", f),
        patch.object(ibkr_reconciler, "_OUT_FILE", out),
        patch("ibkr_reconciler._load_commission_index", return_value={}),
    ):
        ibkr_reconciler._cache_ts = 0.0
        ibkr_reconciler._cache = []
        records = ibkr_reconciler.reconcile_closes(MagicMock(isConnected=lambda: False), cutover_date="2026-04-30")

    assert records == []
