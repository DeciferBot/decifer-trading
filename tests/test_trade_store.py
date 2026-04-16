"""
Tests for trade_store.py — persistent position ledger.

Covers:
  - persist(): atomic write, RESERVED/no-instrument filtering, overwrite, empty snapshot
  - restore(): missing file, corrupt JSON, valid data, bad-record skip, round-trip
  - ledger_write() / ledger_lookup(): write+lookup, first-write-wins, missing key,
    UNKNOWN trade_type skip
"""

from __future__ import annotations

import json
import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path — must happen before any Decifer import
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Stub heavy external deps BEFORE importing any Decifer module
# ---------------------------------------------------------------------------
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser"]:
    sys.modules.setdefault(_mod, MagicMock())

# ---------------------------------------------------------------------------
# Ensure config.CONFIG has the keys trade_store.py needs at import time
# ---------------------------------------------------------------------------
import config as _config_mod  # noqa: E402  (after path manipulation)

_required_cfg = {
    "positions_file": "data/positions.json",
    "metadata_ledger_file": "data/metadata_ledger.json",
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _required_cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _required_cfg

# Now safe to import trade_store
import trade_store  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _valid_pos(**overrides) -> dict:
    """Return a minimal valid position dict that passes schemas.validate_position."""
    base = {
        "symbol": "AAPL",
        "instrument": "stock",
        "entry": 150.0,
        "qty": 10,
        "status": "ACTIVE",
        "direction": "LONG",
    }
    base.update(overrides)
    return base


def _valid_ledger_pos(**overrides) -> dict:
    """Return a minimal position dict suitable for ledger_write."""
    base = {
        "symbol": "AAPL",
        "instrument": "stock",
        "direction": "LONG",
        "entry": 150.0,
        "qty": 10,
        "trade_type": "BREAKOUT",
        "open_time": "2026-04-16T10:00:00",
    }
    base.update(overrides)
    return base


# ===========================================================================
# TestPersist
# ===========================================================================

class TestPersist:

    def test_persist_writes_file(self, tmp_path):
        """persist() with a valid snapshot creates the positions file with correct content."""
        positions_file = tmp_path / "positions.json"
        snapshot = {"AAPL": _valid_pos()}

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            trade_store.persist(snapshot)

        assert positions_file.exists()
        data = json.loads(positions_file.read_text())
        assert "AAPL" in data
        assert data["AAPL"]["symbol"] == "AAPL"
        assert data["AAPL"]["entry"] == 150.0

    def test_persist_excludes_reserved(self, tmp_path):
        """RESERVED entries are filtered out before writing."""
        positions_file = tmp_path / "positions.json"
        snapshot = {
            "AAPL": _valid_pos(),
            "MSFT": _valid_pos(symbol="MSFT", status="RESERVED"),
        }

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            trade_store.persist(snapshot)

        data = json.loads(positions_file.read_text())
        assert "AAPL" in data
        assert "MSFT" not in data

    def test_persist_excludes_missing_instrument(self, tmp_path):
        """Entries without an 'instrument' key are filtered out."""
        positions_file = tmp_path / "positions.json"
        no_instrument = {k: v for k, v in _valid_pos().items() if k != "instrument"}
        snapshot = {
            "AAPL": _valid_pos(),
            "GOOG": no_instrument,
        }

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            trade_store.persist(snapshot)

        data = json.loads(positions_file.read_text())
        assert "AAPL" in data
        assert "GOOG" not in data

    def test_persist_overwrites_existing_file(self, tmp_path):
        """A second persist() call replaces the previous file contents."""
        positions_file = tmp_path / "positions.json"
        first_snapshot = {"AAPL": _valid_pos()}
        second_snapshot = {"MSFT": _valid_pos(symbol="MSFT")}

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            trade_store.persist(first_snapshot)
            trade_store.persist(second_snapshot)

        data = json.loads(positions_file.read_text())
        assert "AAPL" not in data
        assert "MSFT" in data

    def test_persist_empty_snapshot_writes_empty_dict(self, tmp_path):
        """persist({}) writes a file containing an empty JSON object."""
        positions_file = tmp_path / "positions.json"

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            trade_store.persist({})

        data = json.loads(positions_file.read_text())
        assert data == {}


# ===========================================================================
# TestRestore
# ===========================================================================

class TestRestore:

    def test_restore_missing_file_returns_empty(self, tmp_path):
        """restore() returns {} when positions file does not exist."""
        positions_file = tmp_path / "positions.json"
        # File intentionally not created

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            result = trade_store.restore()

        assert result == {}

    def test_restore_corrupt_json_returns_empty(self, tmp_path, caplog):
        """restore() returns {} on bad JSON and logs an ERROR."""
        positions_file = tmp_path / "positions.json"
        positions_file.write_text("{ this is not valid json !!!")

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            with caplog.at_level(logging.ERROR, logger="decifer.trade_store"):
                result = trade_store.restore()

        assert result == {}
        error_messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert any("restore" in m or "positions" in m.lower() for m in error_messages)

    def test_restore_valid_positions_returned(self, tmp_path):
        """restore() returns a dict of all valid position records."""
        positions_file = tmp_path / "positions.json"
        snapshot = {
            "AAPL": _valid_pos(),
            "MSFT": _valid_pos(symbol="MSFT", entry=300.0, qty=5),
        }
        positions_file.write_text(json.dumps(snapshot))

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            result = trade_store.restore()

        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert result["AAPL"]["entry"] == 150.0
        assert result["MSFT"]["entry"] == 300.0

    def test_restore_skips_bad_records_logs_warning(self, tmp_path, caplog):
        """
        One bad record (missing 'symbol') is skipped with a WARNING;
        the remaining valid record is returned.
        """
        positions_file = tmp_path / "positions.json"
        bad_pos = {k: v for k, v in _valid_pos().items() if k != "symbol"}
        snapshot = {
            "AAPL": _valid_pos(),
            "BAD": bad_pos,
        }
        positions_file.write_text(json.dumps(snapshot))

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            with caplog.at_level(logging.WARNING, logger="decifer.trade_store"):
                result = trade_store.restore()

        assert "AAPL" in result
        assert "BAD" not in result
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("BAD" in m for m in warning_messages)

    def test_restore_roundtrip(self, tmp_path):
        """persist() then restore() yields the same data."""
        positions_file = tmp_path / "positions.json"
        original = {
            "AAPL": _valid_pos(),
            "TSLA": _valid_pos(symbol="TSLA", entry=200.0, qty=3, direction="SHORT"),
        }

        with patch.object(trade_store, "_POSITIONS_FILE", positions_file):
            trade_store.persist(original)
            result = trade_store.restore()

        assert set(result.keys()) == {"AAPL", "TSLA"}
        assert result["AAPL"]["entry"] == 150.0
        assert result["TSLA"]["direction"] == "SHORT"


# ===========================================================================
# TestLedger
# ===========================================================================

class TestLedger:

    def test_ledger_write_and_lookup(self, tmp_path):
        """ledger_write() records a position; ledger_lookup() retrieves it by key."""
        ledger_file = tmp_path / "metadata_ledger.json"
        position = _valid_ledger_pos()

        with patch.object(trade_store, "_LEDGER_FILE", ledger_file):
            trade_store.ledger_write("AAPL-1", position)
            result = trade_store.ledger_lookup("AAPL-1")

        assert result["symbol"] == "AAPL"
        assert result["instrument"] == "stock"
        assert result["trade_type"] == "BREAKOUT"
        assert result["direction"] == "LONG"

    def test_ledger_first_write_wins(self, tmp_path):
        """A second write for the same key is silently ignored; original value is kept."""
        ledger_file = tmp_path / "metadata_ledger.json"
        first = _valid_ledger_pos(entry=150.0)
        second = _valid_ledger_pos(entry=999.0)

        with patch.object(trade_store, "_LEDGER_FILE", ledger_file):
            trade_store.ledger_write("AAPL-1", first)
            trade_store.ledger_write("AAPL-1", second)
            result = trade_store.ledger_lookup("AAPL-1")

        assert result["entry"] == 150.0

    def test_ledger_lookup_missing_key_returns_empty(self, tmp_path):
        """ledger_lookup() returns {} when the key has never been written."""
        ledger_file = tmp_path / "metadata_ledger.json"

        with patch.object(trade_store, "_LEDGER_FILE", ledger_file):
            result = trade_store.ledger_lookup("NONEXISTENT-KEY")

        assert result == {}

    def test_ledger_skips_unknown_trade_type(self, tmp_path):
        """A position with trade_type='UNKNOWN' is not written to the ledger."""
        ledger_file = tmp_path / "metadata_ledger.json"
        position = _valid_ledger_pos(trade_type="UNKNOWN")

        with patch.object(trade_store, "_LEDGER_FILE", ledger_file):
            trade_store.ledger_write("AAPL-UNKNOWN", position)
            result = trade_store.ledger_lookup("AAPL-UNKNOWN")

        assert result == {}
        # Ledger file either doesn't exist or doesn't contain the key
        if ledger_file.exists():
            data = json.loads(ledger_file.read_text())
            assert "AAPL-UNKNOWN" not in data
