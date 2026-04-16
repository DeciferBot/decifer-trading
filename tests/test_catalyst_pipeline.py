"""Tests for signals._get_catalyst_lookup() — catalyst data pipeline.

Covers record validation (bad/missing fields), _schema_version handling,
and cache behaviour. Does NOT duplicate tests already in TestGetCatalystLookup
in test_signals.py (missing dir, corrupt JSON, threshold filtering).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


import json
import logging

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Evict any hollow stub that test_bot.py may have cached for 'signals'
sys.modules.pop("signals", None)
import signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_candidates(path, candidates, schema_version=None):
    """Write a candidates_*.json file under *path* with the given list."""
    payload = {"candidates": candidates}
    if schema_version is not None:
        payload["_schema_version"] = schema_version
    (path / "candidates_2026-01-01.json").write_text(json.dumps(payload))


def _patch_catalyst_dir(cfg, tmp_path):
    """Context manager helper — returns original so caller can restore."""
    original = cfg.CATALYST_DIR
    cfg.CATALYST_DIR = tmp_path
    return original


# ---------------------------------------------------------------------------
# TestCatalystRecordValidation
# ---------------------------------------------------------------------------

class TestCatalystRecordValidation:
    """
    validate_catalyst_record() is called per record inside _get_catalyst_lookup().
    Bad records must be skipped with a WARNING; valid records in the same file
    must still be returned.
    """

    def setup_method(self):
        if hasattr(signals, "_catalyst_cache"):
            signals._catalyst_cache.update({"data": {}, "ts": 0.0})

    def test_bad_record_missing_ticker_skipped_logs_warning(self, tmp_path, caplog):
        """A record without 'ticker' is skipped; a WARNING is logged; the valid
        sibling record is still returned."""
        import config as cfg

        good = {"ticker": "AAPL", "catalyst_score": 8.5}
        bad = {"catalyst_score": 9.0}  # missing ticker
        _write_candidates(tmp_path, [bad, good])

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            with caplog.at_level(logging.WARNING, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert "AAPL" in result, "Good record should be returned"
        assert result["AAPL"] == pytest.approx(8.5)
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("skipping bad record" in m for m in warning_messages), (
            "Expected a 'skipping bad record' WARNING for the record missing ticker"
        )

    def test_bad_record_missing_score_skipped_logs_warning(self, tmp_path, caplog):
        """A record without 'catalyst_score' is skipped with a WARNING."""
        import config as cfg

        bad = {"ticker": "TSLA"}  # missing catalyst_score
        _write_candidates(tmp_path, [bad])

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            with caplog.at_level(logging.WARNING, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert result == {}, "All records invalid — expected empty dict"
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("skipping bad record" in m for m in warning_messages), (
            "Expected a 'skipping bad record' WARNING for the record missing catalyst_score"
        )

    def test_all_records_bad_returns_empty_dict(self, tmp_path, caplog):
        """When every record in the file is invalid, return {} (not an exception)."""
        import config as cfg

        bad_records = [
            {"catalyst_score": 9.0},          # missing ticker
            {"ticker": "GOOG"},               # missing catalyst_score
            {"name": "no required fields"},   # missing both
        ]
        _write_candidates(tmp_path, bad_records)

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            with caplog.at_level(logging.WARNING, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert result == {}, "Expected empty dict when all records are invalid"
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len([m for m in warning_messages if "skipping bad record" in m]) == 3, (
            "Expected one 'skipping bad record' WARNING per invalid record (3 total)"
        )


# ---------------------------------------------------------------------------
# TestSchemaVersion
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    """
    _schema_version handling: version 1 (or absent) is silent; any other value
    produces a WARNING but data is still processed.
    """

    def setup_method(self):
        if hasattr(signals, "_catalyst_cache"):
            signals._catalyst_cache.update({"data": {}, "ts": 0.0})

    def test_schema_version_1_no_warning(self, tmp_path, caplog):
        """_schema_version: 1 must not produce any version-related WARNING."""
        import config as cfg

        _write_candidates(tmp_path, [{"ticker": "AAPL", "catalyst_score": 8.0}], schema_version=1)

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            with caplog.at_level(logging.WARNING, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert "AAPL" in result, "Data should be processed with schema_version=1"
        version_warnings = [
            r.message for r in caplog.records
            if r.levelno >= logging.WARNING and "_schema_version" in r.message
        ]
        assert version_warnings == [], (
            f"Expected no _schema_version WARNING for version 1, got: {version_warnings}"
        )

    def test_schema_version_unknown_logs_warning_still_processes(self, tmp_path, caplog):
        """An unrecognised _schema_version (e.g. 99) logs a WARNING but data is
        still returned correctly."""
        import config as cfg

        _write_candidates(tmp_path, [{"ticker": "NVDA", "catalyst_score": 9.5}], schema_version=99)

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            with caplog.at_level(logging.WARNING, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert "NVDA" in result, "Data should still be processed despite unknown schema version"
        assert result["NVDA"] == pytest.approx(9.5)
        version_warnings = [
            r.message for r in caplog.records
            if r.levelno >= logging.WARNING and "_schema_version" in r.message
        ]
        assert version_warnings, (
            "Expected a WARNING mentioning _schema_version when version=99"
        )
        assert any("99" in m for m in version_warnings), (
            "WARNING message should include the offending version number (99)"
        )

    def test_missing_schema_version_no_warning(self, tmp_path, caplog):
        """Files without a _schema_version key must not produce any version
        WARNING — absence of the key is valid."""
        import config as cfg

        # _write_candidates with schema_version=None omits the key entirely
        _write_candidates(tmp_path, [{"ticker": "MSFT", "catalyst_score": 7.5}], schema_version=None)

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            with caplog.at_level(logging.WARNING, logger="decifer.signals"):
                result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert "MSFT" in result, "Data should be processed when _schema_version is absent"
        version_warnings = [
            r.message for r in caplog.records
            if r.levelno >= logging.WARNING and "_schema_version" in r.message
        ]
        assert version_warnings == [], (
            f"Expected no _schema_version WARNING when key is absent, got: {version_warnings}"
        )


# ---------------------------------------------------------------------------
# TestCatalystCache
# ---------------------------------------------------------------------------

class TestCatalystCache:
    """
    _get_catalyst_lookup() caches results for _CATALYST_CACHE_TTL seconds.
    Within the TTL, disk is not re-read. After cache reset, disk is read again.
    """

    def setup_method(self):
        if hasattr(signals, "_catalyst_cache"):
            signals._catalyst_cache.update({"data": {}, "ts": 0.0})

    def test_cache_hit_avoids_disk_read(self, tmp_path):
        """After a successful read, deleting the file and calling again within
        the TTL must still return the cached data without raising."""
        import config as cfg

        candidate_file = tmp_path / "candidates_2026-01-01.json"
        _write_candidates(tmp_path, [{"ticker": "AAPL", "catalyst_score": 8.0}])

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            # First call — populates cache from disk
            first_result = signals._get_catalyst_lookup()
            assert "AAPL" in first_result, "First call should return data from disk"

            # Remove the file so that any disk read would fail / return {}
            candidate_file.unlink()

            # Second call — must hit cache, not disk
            second_result = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        assert second_result == first_result, (
            "Second call should return cached data identical to first call"
        )
        assert "AAPL" in second_result, "Cached data must be returned after file deletion"

    def test_cache_reset_forces_disk_read(self, tmp_path):
        """Resetting _catalyst_cache forces the next call to read from disk."""
        import config as cfg

        _write_candidates(tmp_path, [{"ticker": "GOOG", "catalyst_score": 8.0}])

        original_dir = _patch_catalyst_dir(cfg, tmp_path)
        original_min = cfg.CONFIG.get("catalyst_signal_min_score", 7.0)
        cfg.CONFIG["catalyst_signal_min_score"] = 7.0
        try:
            # First call — populates cache
            first = signals._get_catalyst_lookup()
            assert "GOOG" in first

            # Overwrite the file with different data
            (tmp_path / "candidates_2026-01-02.json").write_text(
                json.dumps({"candidates": [{"ticker": "META", "catalyst_score": 9.0}]})
            )

            # Reset cache — ts=0 forces re-read on next call
            signals._catalyst_cache.update({"data": {}, "ts": 0.0})

            # Second call — must read fresh data from disk
            second = signals._get_catalyst_lookup()
        finally:
            cfg.CATALYST_DIR = original_dir
            cfg.CONFIG["catalyst_signal_min_score"] = original_min

        # The most-recent file (candidates_2026-01-02) is sorted first
        assert "META" in second, (
            "After cache reset, disk is re-read and newer file data must be returned"
        )
