"""Tests for audit_candle_gate.py — trade journal candle gate audit logic."""
import json
import os
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from audit_candle_gate import run_audit


def _write_trades(path: str, records: list) -> None:
    with open(path, "w") as f:
        json.dump(records, f)


def _make_open(symbol="AAPL", candle_gate=None) -> dict:
    record = {
        "timestamp": "2026-03-31T21:00:00+00:00",
        "action": "OPEN",
        "symbol": symbol,
        "direction": "LONG",
        "qty": 10,
        "entry_price": 100.0,
        "score": 30,
    }
    if candle_gate is not None:
        record["candle_gate"] = candle_gate
    return record


class TestAuditFlags:

    def test_audit_flags_trades_without_candle_gate_field(self, tmp_path):
        """Trades with no candle_gate key must be flagged as UNKNOWN."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        _write_trades(str(trades_file), [_make_open("AAPL")])  # no candle_gate key

        result = run_audit(str(trades_file), str(audit_file))

        assert result["flagged_unknown"] == 1
        assert result["valid"] == 0
        assert result["flagged_anomaly"] == 0
        assert result["flagged_trades"][0]["_audit_flag"] == "UNKNOWN"

    def test_audit_clean_trade_not_flagged(self, tmp_path):
        """Trade with candle_gate='PASS' must not be flagged."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        _write_trades(str(trades_file), [_make_open("AAPL", candle_gate="PASS")])

        result = run_audit(str(trades_file), str(audit_file))

        assert result["valid"] == 1
        assert result["flagged_unknown"] == 0
        assert result["flagged_anomaly"] == 0
        assert result["flagged_trades"] == []

    def test_audit_blocked_on_open_is_anomaly(self, tmp_path):
        """Trade with candle_gate='BLOCKED' on an OPEN record is an ANOMALY."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        _write_trades(str(trades_file), [_make_open("TSLA", candle_gate="BLOCKED")])

        result = run_audit(str(trades_file), str(audit_file))

        assert result["flagged_anomaly"] == 1
        assert result["flagged_trades"][0]["_audit_flag"] == "ANOMALY"

    def test_audit_skipped_not_flagged(self, tmp_path):
        """Trade with candle_gate='SKIPPED' (MTF blocked first) is noted but not flagged."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        _write_trades(str(trades_file), [_make_open("MSFT", candle_gate="SKIPPED")])

        result = run_audit(str(trades_file), str(audit_file))

        assert result["skipped"] == 1
        assert result["flagged_unknown"] == 0
        assert result["flagged_anomaly"] == 0

    def test_audit_ignores_close_records(self, tmp_path):
        """CLOSE records are not audited — only OPEN records count."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        close_record = {**_make_open("AAPL"), "action": "CLOSE"}  # no candle_gate
        _write_trades(str(trades_file), [close_record])

        result = run_audit(str(trades_file), str(audit_file))

        assert result["total_open"] == 0
        assert result["flagged_unknown"] == 0


class TestAuditOutput:

    def test_audit_writes_output_file(self, tmp_path):
        """run_audit() must write a JSON file to the output path."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        _write_trades(str(trades_file), [_make_open("AAPL")])

        run_audit(str(trades_file), str(audit_file))

        assert audit_file.exists()
        with open(str(audit_file)) as f:
            data = json.load(f)
        assert "flagged_trades" in data
        assert "total_open" in data

    def test_audit_output_includes_timestamp(self, tmp_path):
        """Audit output file must include an audit_timestamp field."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        _write_trades(str(trades_file), [])

        run_audit(str(trades_file), str(audit_file))

        with open(str(audit_file)) as f:
            data = json.load(f)
        assert "audit_timestamp" in data

    def test_audit_handles_missing_trade_log(self, tmp_path):
        """If trades.json doesn't exist, audit returns zero counts without crashing."""
        audit_file = tmp_path / "audit.json"
        missing    = tmp_path / "nonexistent.json"

        result = run_audit(str(missing), str(audit_file))

        assert result["total_open"] == 0
        assert result["flagged_unknown"] == 0

    def test_audit_mixed_trades_counts_correctly(self, tmp_path):
        """Mixed batch: 2 unknown, 1 valid, 1 anomaly, 1 skipped."""
        trades_file = tmp_path / "trades.json"
        audit_file  = tmp_path / "audit.json"
        records = [
            _make_open("A"),                           # UNKNOWN (no field)
            _make_open("B"),                           # UNKNOWN (no field)
            _make_open("C", candle_gate="PASS"),       # valid
            _make_open("D", candle_gate="BLOCKED"),    # ANOMALY
            _make_open("E", candle_gate="SKIPPED"),    # skipped
            {**_make_open("F"), "action": "CLOSE"},    # ignored
        ]
        _write_trades(str(trades_file), records)

        result = run_audit(str(trades_file), str(audit_file))

        assert result["total_open"] == 5
        assert result["flagged_unknown"] == 2
        assert result["valid"] == 1
        assert result["flagged_anomaly"] == 1
        assert result["skipped"] == 1
        assert len(result["flagged_trades"]) == 3  # 2 unknown + 1 anomaly
