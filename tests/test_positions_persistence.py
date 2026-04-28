"""Tests for orders_state.py position persistence functions.

Covers:
  - _save_positions_file() / _load_positions_file() roundtrip and edge cases
  - _is_recently_closed() cooldown logic
  - cleanup_recently_closed() eviction logic

Does NOT duplicate tests already in test_orders.py:
  - _persist_positions() failure creates flag file
  - _persist_positions() success does not create flag file
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Module-level mocks — must exist before orders_state imports
# ---------------------------------------------------------------------------
for _mod_name in ("ib_async", "anthropic"):
    sys.modules.setdefault(_mod_name, MagicMock())

# Stub config with all keys orders_state reads
_CONFIG_STUB = {
    "trade_log": "/tmp/test_trades.json",
    "order_log": "/tmp/test_orders.json",
    "positions_file": "/tmp/test_positions.json",
    "reentry_cooldown_minutes": 30,
}
_config_mod = MagicMock()
_config_mod.CONFIG = _CONFIG_STUB
sys.modules.setdefault("config", _config_mod)

# Now safe to import orders_state
import orders_state  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_POSITION = {
    "symbol": "AAPL",
    "instrument": "stock",
    "entry": 150.0,
    "qty": 10,
    "status": "ACTIVE",
    "direction": "LONG",
}

RESERVED_POSITION = {
    "symbol": "TSLA",
    "instrument": "stock",
    "entry": 200.0,
    "qty": 5,
    "status": "RESERVED",
    "direction": "LONG",
}


# ---------------------------------------------------------------------------
# TestSaveLoadPositionsFile
# ---------------------------------------------------------------------------


class TestSaveLoadPositionsFile:
    """Tests for _save_positions_file() and _load_positions_file()."""

    def test_save_creates_file(self, tmp_path):
        """Saving non-empty active_trades should create POSITIONS_FILE on disk."""
        pos_file = str(tmp_path / "positions.json")
        active = {"AAPL": dict(VALID_POSITION)}

        with (
            patch.object(orders_state, "POSITIONS_FILE", pos_file),
            patch.object(orders_state, "active_trades", active),
        ):
            orders_state._save_positions_file()

        assert Path(pos_file).exists()

    def test_save_load_roundtrip(self, tmp_path):
        """Data written by _save_positions_file() is returned intact by _load_positions_file()."""
        pos_file = str(tmp_path / "positions.json")
        active = {"AAPL": dict(VALID_POSITION)}

        with (
            patch.object(orders_state, "POSITIONS_FILE", pos_file),
            patch.object(orders_state, "active_trades", active),
        ):
            orders_state._save_positions_file()

        with patch.object(orders_state, "POSITIONS_FILE", pos_file):
            result = orders_state._load_positions_file()

        assert result == active

    def test_save_excludes_reserved(self, tmp_path):
        """RESERVED entries must not appear in the written file."""
        pos_file = str(tmp_path / "positions.json")
        active = {
            "AAPL": dict(VALID_POSITION),
            "TSLA": dict(RESERVED_POSITION),
        }

        with (
            patch.object(orders_state, "POSITIONS_FILE", pos_file),
            patch.object(orders_state, "active_trades", active),
        ):
            orders_state._save_positions_file()

        with patch.object(orders_state, "POSITIONS_FILE", pos_file):
            result = orders_state._load_positions_file()

        assert "AAPL" in result
        assert "TSLA" not in result

    def test_load_missing_file_returns_empty(self, tmp_path):
        """_load_positions_file() returns {} when neither DB nor file has data."""
        pos_file = str(tmp_path / "does_not_exist.json")

        with patch.object(orders_state, "POSITIONS_FILE", pos_file):
            result = orders_state._load_positions_file()

        assert result == {}

    def test_load_corrupt_json_returns_empty_and_logs(self, tmp_path, caplog):
        """Corrupt JSON in POSITIONS_FILE returns {} and logs a WARNING."""
        pos_file = tmp_path / "positions.json"
        pos_file.write_text("{ this is not valid json !!!")

        with (
            patch.object(orders_state, "POSITIONS_FILE", str(pos_file)),
            caplog.at_level(logging.WARNING, logger="decifer.orders"),
        ):
            result = orders_state._load_positions_file()

        assert result == {}
        assert any("_load_positions_file" in r.message for r in caplog.records)

    def test_load_non_dict_json_returns_empty(self, tmp_path):
        """JSON that is valid but not a dict (e.g. a list) must return {}."""
        pos_file = tmp_path / "positions.json"
        pos_file.write_text(json.dumps([{"symbol": "AAPL"}]))

        with patch.object(orders_state, "POSITIONS_FILE", str(pos_file)):
            result = orders_state._load_positions_file()

        assert result == {}

    def test_save_creates_parent_dirs(self, tmp_path):
        """_save_positions_file() must create missing parent directories without crashing."""
        pos_file = str(tmp_path / "nested" / "deep" / "positions.json")
        active = {"AAPL": dict(VALID_POSITION)}

        with (
            patch.object(orders_state, "POSITIONS_FILE", pos_file),
            patch.object(orders_state, "active_trades", active),
        ):
            # Should not raise
            orders_state._save_positions_file()

        assert Path(pos_file).exists()


# ---------------------------------------------------------------------------
# TestIsRecentlyClosed
# ---------------------------------------------------------------------------


class TestIsRecentlyClosed:
    """Tests for _is_recently_closed()."""

    def test_not_in_recently_closed_returns_false(self):
        """Symbol absent from recently_closed dict returns False."""
        with (
            patch.object(orders_state, "recently_closed", {}),
            patch.dict(orders_state.CONFIG, {"reentry_cooldown_minutes": 30}),
        ):
            result = orders_state._is_recently_closed("AAPL")

        assert result is False

    def test_within_cooldown_returns_true(self):
        """Symbol closed 1 minute ago with a 30-minute cooldown returns True."""
        closed_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        rc = {"AAPL": closed_at}

        with (
            patch.object(orders_state, "recently_closed", rc),
            patch.dict(orders_state.CONFIG, {"reentry_cooldown_minutes": 30}),
        ):
            result = orders_state._is_recently_closed("AAPL")

        assert result is True

    def test_past_cooldown_returns_false(self):
        """Symbol closed 60 minutes ago with a 30-minute cooldown returns False."""
        closed_at = (datetime.now(UTC) - timedelta(minutes=60)).isoformat()
        rc = {"AAPL": closed_at}

        with (
            patch.object(orders_state, "recently_closed", rc),
            patch.dict(orders_state.CONFIG, {"reentry_cooldown_minutes": 30}),
        ):
            result = orders_state._is_recently_closed("AAPL")

        assert result is False


# ---------------------------------------------------------------------------
# TestCleanupRecentlyClosed
# ---------------------------------------------------------------------------


class TestCleanupRecentlyClosed:
    """Tests for cleanup_recently_closed()."""

    def test_removes_entries_past_2x_cooldown(self):
        """Entries older than 2× cooldown_minutes are removed; count is returned."""
        # 2× 30 min = 60 min; use 121 min to be safely past the window
        old_ts = (datetime.now(UTC) - timedelta(minutes=121)).isoformat()
        rc = {"AAPL": old_ts}

        with (
            patch.object(orders_state, "recently_closed", rc),
            patch.dict(orders_state.CONFIG, {"reentry_cooldown_minutes": 30}),
        ):
            removed = orders_state.cleanup_recently_closed()
            remaining = dict(orders_state.recently_closed)

        assert removed == 1
        assert "AAPL" not in remaining

    def test_keeps_entries_within_2x_cooldown(self):
        """Entries younger than 2× cooldown_minutes are preserved."""
        # 2× 30 min = 60 min; use 50 min to stay within the window
        recent_ts = (datetime.now(UTC) - timedelta(minutes=50)).isoformat()
        rc = {"AAPL": recent_ts}

        with (
            patch.object(orders_state, "recently_closed", rc),
            patch.dict(orders_state.CONFIG, {"reentry_cooldown_minutes": 30}),
        ):
            removed = orders_state.cleanup_recently_closed()
            remaining = dict(orders_state.recently_closed)

        assert removed == 0
        assert "AAPL" in remaining

    def test_empty_dict_returns_zero(self):
        """cleanup_recently_closed() on an empty dict returns 0 without error."""
        with (
            patch.object(orders_state, "recently_closed", {}),
            patch.dict(orders_state.CONFIG, {"reentry_cooldown_minutes": 30}),
        ):
            removed = orders_state.cleanup_recently_closed()

        assert removed == 0
