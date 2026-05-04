"""
Tests for event_log.py and training_store.py.

All tests use tmp_path to avoid touching any real data files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _intent(trade_id="MDT_001", symbol="MDT", **kw):
    defaults = dict(
        direction="SHORT", trade_type="INTRADAY", instrument="stock",
        intended_price=82.22, qty=715, sl=83.87, tp=77.29,
        regime="BEAR_TRENDING", signal_scores={"momentum": 8, "flow": 6},
        conviction=0.8, reasoning="test", score=42.0, open_time="2026-04-28T12:00:00+00:00",
    )
    defaults.update(kw)
    return dict(trade_id=trade_id, symbol=symbol, **defaults)


def _fill(trade_id="MDT_001", symbol="MDT", **kw):
    defaults = dict(fill_price=82.18, fill_qty=715, order_id=4421)
    defaults.update(kw)
    return dict(trade_id=trade_id, symbol=symbol, **defaults)


def _close(trade_id="MDT_001", symbol="MDT", **kw):
    defaults = dict(exit_price=82.04, pnl=128.70, exit_reason="take_profit", hold_minutes=170)
    defaults.update(kw)
    return dict(trade_id=trade_id, symbol=symbol, **defaults)


def _training_record(**kw):
    base = dict(
        trade_id="MDT_001", symbol="MDT", direction="SHORT", trade_type="INTRADAY",
        fill_price=82.18, exit_price=82.04, pnl=128.70, hold_minutes=170,
        exit_reason="take_profit", regime="BEAR_TRENDING",
        signal_scores={"momentum": 8}, conviction=0.8, score=42.0,
        ts_fill="2026-04-28T12:05:00+00:00", ts_close="2026-04-28T14:50:00+00:00",
    )
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# event_log tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEventLogAppend:
    def test_creates_file_on_first_write(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        assert (tmp_path / "events.jsonl").exists()

    @pytest.mark.smoke
    def test_each_write_is_one_line(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        event_log.append_fill(**_fill())
        lines = (tmp_path / "events.jsonl").read_text().splitlines()
        assert len(lines) == 2

    def test_intent_record_fields(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent(trade_id="X_001", symbol="X"))
        rec = json.loads((tmp_path / "events.jsonl").read_text().strip())
        assert rec["event"] == "ORDER_INTENT"
        assert rec["trade_id"] == "X_001"
        assert rec["symbol"] == "X"
        assert rec["trade_type"] == "INTRADAY"
        assert rec["direction"] == "SHORT"

    def test_fill_record_fields(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_fill(**_fill(fill_price=82.18, fill_qty=715))
        rec = json.loads((tmp_path / "events.jsonl").read_text().strip())
        assert rec["event"] == "ORDER_FILLED"
        assert rec["fill_price"] == 82.18
        assert rec["fill_qty"] == 715

    def test_close_record_fields(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_close(**_close(pnl=128.70, exit_reason="take_profit"))
        rec = json.loads((tmp_path / "events.jsonl").read_text().strip())
        assert rec["event"] == "POSITION_CLOSED"
        assert rec["pnl"] == 128.70
        assert rec["exit_reason"] == "take_profit"


class TestEventLogOpenTrades:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        assert event_log.open_trades() == {}

    def test_intent_only_not_returned(self, tmp_path, monkeypatch):
        """ORDER_INTENT without ORDER_FILLED is pending, not open."""
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        assert event_log.open_trades() == {}

    def test_filled_without_close_is_open(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        event_log.append_fill(**_fill())
        result = event_log.open_trades()
        assert "MDT_001" in result

    def test_fill_price_is_canonical_entry(self, tmp_path, monkeypatch):
        """Confirmed fill price, not intended price, is the entry."""
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent(intended_price=82.22))
        event_log.append_fill(**_fill(fill_price=82.18))
        result = event_log.open_trades()
        assert result["MDT_001"]["entry"] == 82.18
        assert result["MDT_001"]["intended_price"] == 82.22

    def test_intent_metadata_preserved_in_open_trades(self, tmp_path, monkeypatch):
        """Signal scores, trade_type, conviction come from ORDER_INTENT."""
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent(trade_type="POSITION", conviction=0.9))
        event_log.append_fill(**_fill())
        result = event_log.open_trades()
        assert result["MDT_001"]["trade_type"] == "POSITION"
        assert result["MDT_001"]["conviction"] == 0.9

    def test_closed_trade_not_returned(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        event_log.append_fill(**_fill())
        event_log.append_close(**_close())
        assert event_log.open_trades() == {}

    def test_multiple_trades_correct_open_count(self, tmp_path, monkeypatch):
        """3 intents + 3 fills + 2 closes → exactly 1 open."""
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        for i in range(3):
            tid = f"SYM_{i:03d}"
            event_log.append_intent(**_intent(trade_id=tid, symbol="SYM"))
            event_log.append_fill(**_fill(trade_id=tid, symbol="SYM"))
        for i in range(2):
            event_log.append_close(**_close(trade_id=f"SYM_{i:03d}", symbol="SYM"))
        result = event_log.open_trades()
        assert len(result) == 1
        assert "SYM_002" in result

    def test_two_open_different_symbols(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent(trade_id="MDT_001", symbol="MDT"))
        event_log.append_fill(**_fill(trade_id="MDT_001", symbol="MDT"))
        event_log.append_intent(**_intent(trade_id="LIN_001", symbol="LIN"))
        event_log.append_fill(**_fill(trade_id="LIN_001", symbol="LIN"))
        result = event_log.open_trades()
        assert set(result.keys()) == {"MDT_001", "LIN_001"}


class TestEventLogCrashSafety:
    @pytest.mark.smoke
    def test_partial_last_line_is_skipped(self, tmp_path, monkeypatch):
        """A partial last line (crash artefact) must not corrupt earlier records."""
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        event_log.append_fill(**_fill())
        # Simulate crash: append a partial JSON line
        with open(tmp_path / "events.jsonl", "a") as f:
            f.write('{"event": "POSITION_CLOSED", "trade_id": "MDT_001"')  # no closing }
        result = event_log.open_trades()
        # Partial last line is skipped — trade is still open
        assert "MDT_001" in result

    def test_empty_file_returns_empty(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        (tmp_path / "events.jsonl").write_text("")
        assert event_log.open_trades() == {}
        assert event_log.pending_orders() == []


class TestEventLogPendingOrders:
    def test_intent_without_fill_is_pending(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        pending = event_log.pending_orders()
        assert len(pending) == 1
        assert pending[0]["trade_id"] == "MDT_001"

    def test_filled_intent_not_pending(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        event_log.append_fill(**_fill())
        assert event_log.pending_orders() == []

    def test_closed_intent_not_pending(self, tmp_path, monkeypatch):
        import event_log
        monkeypatch.setattr(event_log, "_LOG_FILE", tmp_path / "events.jsonl")
        event_log.append_intent(**_intent())
        event_log.append_fill(**_fill())
        event_log.append_close(**_close())
        assert event_log.pending_orders() == []


# ══════════════════════════════════════════════════════════════════════════════
# training_store tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTrainingStoreAppend:
    def test_creates_file_on_first_write(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        training_store.append(_training_record())
        assert (tmp_path / "training.jsonl").exists()

    def test_each_record_is_one_line(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        training_store.append(_training_record(trade_id="A"))
        training_store.append(_training_record(trade_id="B"))
        lines = (tmp_path / "training.jsonl").read_text().splitlines()
        assert len(lines) == 2

    def test_missing_required_field_raises(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        rec = _training_record()
        del rec["pnl"]
        with pytest.raises(ValueError, match="pnl"):
            training_store.append(rec)

    def test_file_not_created_on_failed_write(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        rec = _training_record()
        del rec["fill_price"]
        with pytest.raises(ValueError):
            training_store.append(rec)
        assert not (tmp_path / "training.jsonl").exists()

    def test_both_prices_preserved(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        rec = _training_record(intended_price=82.22, fill_price=82.18)
        training_store.append(rec)
        written = json.loads((tmp_path / "training.jsonl").read_text())
        assert written["intended_price"] == 82.22
        assert written["fill_price"] == 82.18

    def test_ts_written_added_automatically(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        training_store.append(_training_record())
        written = json.loads((tmp_path / "training.jsonl").read_text())
        assert "ts_written" in written


class TestTrainingStoreLoad:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        assert training_store.load() == []

    def test_load_all_records(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        training_store.append(_training_record(trade_id="A", symbol="AAPL"))
        training_store.append(_training_record(trade_id="B", symbol="MSFT"))
        assert len(training_store.load()) == 2

    def test_filter_by_symbol(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        training_store.append(_training_record(trade_id="A", symbol="AAPL"))
        training_store.append(_training_record(trade_id="B", symbol="MSFT"))
        result = training_store.load(symbol="AAPL")
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    def test_limit(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        for i in range(5):
            training_store.append(_training_record(trade_id=f"T{i}"))
        result = training_store.load(limit=3)
        assert len(result) == 3
        assert result[-1]["trade_id"] == "T4"

    def test_partial_last_line_skipped(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        training_store.append(_training_record(trade_id="A"))
        with open(tmp_path / "training.jsonl", "a") as f:
            f.write('{"trade_id": "B", "symbol": "X"')  # partial
        assert len(training_store.load()) == 1


class TestTrainingStoreCount:
    def test_count_zero_when_no_file(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        assert training_store.count() == 0

    def test_count_matches_appends(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        for i in range(7):
            training_store.append(_training_record(trade_id=f"T{i}"))
        assert training_store.count() == 7

    def test_count_ignores_partial_last_line(self, tmp_path, monkeypatch):
        import training_store
        monkeypatch.setattr(training_store, "_STORE_FILE", tmp_path / "training.jsonl")
        training_store.append(_training_record(trade_id="A"))
        training_store.append(_training_record(trade_id="B"))
        with open(tmp_path / "training.jsonl", "a") as f:
            f.write('{"partial":')
        assert training_store.count() == 2


# ── get_ts() robust timestamp helper ─────────────────────────────────────────

class TestGetTs:
    def test_reads_ts_field(self):
        import event_log
        assert event_log.get_ts({"ts": "2026-05-04T17:20:00+00:00"}) == "2026-05-04T17:20:00+00:00"

    def test_falls_back_to_timestamp(self):
        import event_log
        assert event_log.get_ts({"timestamp": "2026-05-04T17:20:00+00:00"}) == "2026-05-04T17:20:00+00:00"

    def test_falls_back_to_created_at(self):
        import event_log
        assert event_log.get_ts({"created_at": "2026-05-04T17:20:00+00:00"}) == "2026-05-04T17:20:00+00:00"

    def test_ts_wins_over_timestamp(self):
        import event_log
        r = {"ts": "A", "timestamp": "B"}
        assert event_log.get_ts(r) == "A"

    def test_returns_empty_string_when_absent(self):
        import event_log
        assert event_log.get_ts({}) == ""
        assert event_log.get_ts({"event": "ORDER_INTENT"}) == ""
