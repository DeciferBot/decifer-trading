"""
T2-A: Tests for schemas.py — JSON schema validators.

Done criteria: corrupt JSON with a missing key → the validator raises ValueError
with a clear message. Call sites log the bad record, skip it, and continue.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import schemas


# ── _check() internals ─────────────────────────────────────────────────────────

class TestCheckHelper:
    """Unit tests for the internal _check() helper."""

    def test_passes_valid_record(self):
        """No exception when all required fields are present and correct type."""
        schemas._check({"a": "hello", "b": 3.14}, [("a", str), ("b", float)], "test")

    def test_raises_on_missing_field(self):
        """Missing required field raises ValueError with field name."""
        with pytest.raises(ValueError, match="missing required field 'ticker'"):
            schemas._check({"other": 1}, [("ticker", str)], "test record")

    def test_raises_on_wrong_type(self):
        """Wrong type raises ValueError with expected and actual type names."""
        with pytest.raises(ValueError, match="must be str"):
            schemas._check({"ticker": 42}, [("ticker", str)], "test record")

    def test_raises_on_wrong_type_shows_actual(self):
        """Error message includes actual type."""
        with pytest.raises(ValueError, match="int"):
            schemas._check({"ticker": 42}, [("ticker", str)], "test record")

    def test_tuple_of_types_passes_either(self):
        """Union type (int, float) accepts both int and float."""
        schemas._check({"score": 5}, [("score", (int, float))], "test")
        schemas._check({"score": 5.5}, [("score", (int, float))], "test")

    def test_tuple_of_types_rejects_other(self):
        """Union type raises when given a non-matching type."""
        with pytest.raises(ValueError, match="int or float"):
            schemas._check({"score": "high"}, [("score", (int, float))], "test")

    def test_context_appears_in_error(self):
        """Context string is included in the ValueError message."""
        with pytest.raises(ValueError, match="catalyst record"):
            schemas._check({}, [("ticker", str)], "catalyst record")


# ── validate_catalyst_record ───────────────────────────────────────────────────

class TestValidateCatalystRecord:

    def test_valid_record_passes(self):
        schemas.validate_catalyst_record({"ticker": "AAPL", "catalyst_score": 8.5})

    def test_valid_record_int_score_passes(self):
        schemas.validate_catalyst_record({"ticker": "MSFT", "catalyst_score": 7})

    def test_missing_ticker_raises(self):
        with pytest.raises(ValueError, match="ticker"):
            schemas.validate_catalyst_record({"catalyst_score": 8.5})

    def test_missing_score_raises(self):
        with pytest.raises(ValueError, match="catalyst_score"):
            schemas.validate_catalyst_record({"ticker": "AAPL"})

    def test_wrong_ticker_type_raises(self):
        with pytest.raises(ValueError, match="ticker"):
            schemas.validate_catalyst_record({"ticker": 123, "catalyst_score": 8.5})

    def test_wrong_score_type_raises(self):
        with pytest.raises(ValueError, match="catalyst_score"):
            schemas.validate_catalyst_record({"ticker": "AAPL", "catalyst_score": "high"})

    def test_extra_fields_ignored(self):
        """Extra enrichment fields do not cause failures."""
        schemas.validate_catalyst_record({
            "ticker": "TSLA",
            "catalyst_score": 9.0,
            "event_type": "earnings_beat",
            "notes": "Q4 surprise",
        })


# ── validate_position ─────────────────────────────────────────────────────────

class TestValidatePosition:

    def _good(self, **overrides):
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

    def test_valid_position_passes(self):
        schemas.validate_position(self._good())

    def test_int_entry_passes(self):
        schemas.validate_position(self._good(entry=150))

    def test_int_qty_passes(self):
        schemas.validate_position(self._good(qty=10))

    def test_missing_symbol_raises(self):
        rec = self._good()
        del rec["symbol"]
        with pytest.raises(ValueError, match="symbol"):
            schemas.validate_position(rec)

    def test_missing_instrument_raises(self):
        rec = self._good()
        del rec["instrument"]
        with pytest.raises(ValueError, match="instrument"):
            schemas.validate_position(rec)

    def test_missing_entry_raises(self):
        rec = self._good()
        del rec["entry"]
        with pytest.raises(ValueError, match="entry"):
            schemas.validate_position(rec)

    def test_missing_qty_raises(self):
        rec = self._good()
        del rec["qty"]
        with pytest.raises(ValueError, match="qty"):
            schemas.validate_position(rec)

    def test_missing_status_raises(self):
        rec = self._good()
        del rec["status"]
        with pytest.raises(ValueError, match="status"):
            schemas.validate_position(rec)

    def test_missing_direction_raises(self):
        rec = self._good()
        del rec["direction"]
        with pytest.raises(ValueError, match="direction"):
            schemas.validate_position(rec)

    def test_wrong_entry_type_raises(self):
        with pytest.raises(ValueError, match="entry"):
            schemas.validate_position(self._good(entry="150.0"))


# ── validate_trade ────────────────────────────────────────────────────────────

class TestValidateTrade:

    def _good(self, **overrides):
        base = {
            "symbol": "AAPL",
            "score": 7.5,
            "direction": "LONG",
            "pnl": 120.0,
        }
        base.update(overrides)
        return base

    def test_valid_trade_passes(self):
        schemas.validate_trade(self._good())

    def test_int_score_passes(self):
        schemas.validate_trade(self._good(score=8))

    def test_int_pnl_passes(self):
        schemas.validate_trade(self._good(pnl=-50))

    def test_missing_symbol_raises(self):
        rec = self._good()
        del rec["symbol"]
        with pytest.raises(ValueError, match="symbol"):
            schemas.validate_trade(rec)

    def test_missing_score_raises(self):
        rec = self._good()
        del rec["score"]
        with pytest.raises(ValueError, match="score"):
            schemas.validate_trade(rec)

    def test_missing_direction_raises(self):
        rec = self._good()
        del rec["direction"]
        with pytest.raises(ValueError, match="direction"):
            schemas.validate_trade(rec)

    def test_missing_pnl_raises(self):
        rec = self._good()
        del rec["pnl"]
        with pytest.raises(ValueError, match="pnl"):
            schemas.validate_trade(rec)

    def test_wrong_pnl_type_raises(self):
        with pytest.raises(ValueError, match="pnl"):
            schemas.validate_trade(self._good(pnl="big profit"))


# ── validate_signal ───────────────────────────────────────────────────────────

class TestValidateSignal:

    def _good(self, **overrides):
        base = {
            "symbol": "MSFT",
            "score": 18.0,
            "ts": "2026-04-16T10:00:00+00:00",
            "score_breakdown": {"directional": 3, "momentum": 2},
        }
        base.update(overrides)
        return base

    def test_valid_signal_passes(self):
        schemas.validate_signal(self._good())

    def test_int_score_passes(self):
        schemas.validate_signal(self._good(score=20))

    def test_missing_symbol_raises(self):
        rec = self._good()
        del rec["symbol"]
        with pytest.raises(ValueError, match="symbol"):
            schemas.validate_signal(rec)

    def test_missing_score_raises(self):
        rec = self._good()
        del rec["score"]
        with pytest.raises(ValueError, match="score"):
            schemas.validate_signal(rec)

    def test_missing_ts_raises(self):
        rec = self._good()
        del rec["ts"]
        with pytest.raises(ValueError, match="ts"):
            schemas.validate_signal(rec)

    def test_missing_score_breakdown_raises(self):
        rec = self._good()
        del rec["score_breakdown"]
        with pytest.raises(ValueError, match="score_breakdown"):
            schemas.validate_signal(rec)

    def test_wrong_ts_type_raises(self):
        with pytest.raises(ValueError, match="ts"):
            schemas.validate_signal(self._good(ts=1234567890))

    def test_wrong_score_breakdown_type_raises(self):
        with pytest.raises(ValueError, match="score_breakdown"):
            schemas.validate_signal(self._good(score_breakdown="high,low"))

    def test_empty_score_breakdown_still_passes(self):
        """An empty dict satisfies the dict type check — dimension checks are not here."""
        schemas.validate_signal(self._good(score_breakdown={}))


# ── Call-site integration: log + skip pattern ─────────────────────────────────

class TestCallSitePattern:
    """
    Verify the usage pattern documented in schemas.py:
    bad records are logged and skipped, not crashed on.
    """

    def test_bad_records_skipped_good_records_kept(self):
        """Loop over mixed records: bad ones raise ValueError, good ones pass through."""
        records = [
            {"ticker": "AAPL", "catalyst_score": 8.0},   # good
            {"catalyst_score": 9.0},                      # bad — missing ticker
            {"ticker": "TSLA", "catalyst_score": 7.5},   # good
            {"ticker": 123, "catalyst_score": 6.0},       # bad — wrong type
        ]
        passed = []
        for rec in records:
            try:
                schemas.validate_catalyst_record(rec)
                passed.append(rec)
            except ValueError:
                continue

        assert len(passed) == 2
        assert passed[0]["ticker"] == "AAPL"
        assert passed[1]["ticker"] == "TSLA"
