"""
test_customer_event_tape.py — Sprint M11A.

Covers:
  - maybe_record_customer_event writes the tape and is fail-soft
  - Tape file schema is correct
  - get_recent_events / load_customer_event_tape work
  - Macro-only headlines (no symbols) are accepted
  - Multiple events from one headline both recorded
  - Tape file is customer-safe (no banned fields)
  - Compute freshness correctly classifies fresh/stale
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customer_event_tape as cet


@pytest.fixture(autouse=True)
def _isolated_tape(tmp_path, monkeypatch):
    """Redirect the tape file to a temp path for each test."""
    tape_file = tmp_path / "customer_event_tape.json"
    monkeypatch.setattr(cet, "_TAPE_PATH", str(tape_file))
    yield tape_file


# ── Banned keys that must NEVER appear in tape records ──
_BANNED_KEYS = frozenset({
    "position_size", "qty", "quantity", "shares",
    "entry_price", "exit_price", "stop_price",
    "pnl", "unrealized_pnl", "realized_pnl", "cost_basis",
    "account_id", "ibkr_account", "broker_account", "buying_power",
    "account_value", "portfolio_value", "daily_pnl",
    "order_id", "client_order_id", "ibkr_order_id",
    "buy_signal", "sell_signal", "trade_recommendation",
    "raw_score", "signal_score", "ic_weight",
})


def _assert_tape_safe(tape: dict) -> None:
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                assert k not in _BANNED_KEYS, f"banned key {k!r} in tape"
                walk(v)
        elif isinstance(o, list):
            for item in o:
                walk(item)
    walk(tape)


# ---------------------------------------------------------------------------

class TestMaybeRecord:

    def test_writes_event_on_classifiable_headline(self):
        ids = cet.maybe_record_customer_event(
            headline="Oil jumps 6 percent after supply disruption and tanker route closure.",
            source="test",
        )
        assert ids
        tape = cet.load_customer_event_tape()
        assert tape["events"]
        types = {e["event_type"] for e in tape["events"]}
        # A supply-shock headline that also names a tanker-route closure also
        # produces a geopolitics escalation classification — both are correct.
        assert "oil_supply_shock" in types

    def test_no_write_on_unclassifiable(self):
        ids = cet.maybe_record_customer_event(
            headline="Apple opens new store in downtown Sydney.",
            source="test",
        )
        assert ids == []
        tape = cet.load_customer_event_tape()
        assert tape["events"] == []

    def test_no_write_on_empty(self):
        assert cet.maybe_record_customer_event(headline="") == []
        assert cet.maybe_record_customer_event(headline=None) == []  # type: ignore[arg-type]

    def test_fail_soft_on_classifier_error(self):
        # Mock the classifier to raise, verify maybe_record returns []
        with patch.object(cet, "classify_headline", side_effect=RuntimeError("boom")):
            result = cet.maybe_record_customer_event(headline="anything", source="t")
        assert result == []

    def test_macro_headline_with_no_symbols_accepted(self):
        # Sprint requirement: macro/global headlines must enter even without symbols
        ids = cet.maybe_record_customer_event(
            headline="US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
            symbols=[],
            source="test",
        )
        assert ids
        tape = cet.load_customer_event_tape()
        types = {e["event_type"] for e in tape["events"]}
        assert types & {"de_escalation", "oil_risk_premium_unwind"}

    def test_multiple_events_from_one_headline(self):
        ids = cet.maybe_record_customer_event(
            headline="US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
            source="test",
        )
        # Should produce both geo de-escalation AND oil risk unwind
        assert len(ids) >= 2

    def test_tape_caps_max_events(self):
        old = cet._MAX_EVENTS
        try:
            cet._MAX_EVENTS = 3
            for i in range(10):
                cet.maybe_record_customer_event(
                    headline=f"Oil jumps {i} percent after supply disruption.",
                    source="test",
                )
            tape = cet.load_customer_event_tape()
            assert len(tape["events"]) <= 3
        finally:
            cet._MAX_EVENTS = old

    def test_records_include_required_fields(self):
        cet.maybe_record_customer_event(
            headline="Microsoft announces $40bn acquisition of cybersecurity platform.",
            symbols=["MSFT"],
            source="test",
        )
        tape = cet.load_customer_event_tape()
        ev = tape["events"][0]
        for required in (
            "event_id", "event_family", "event_type", "status",
            "title", "summary_plain_english", "source",
            "source_published_at", "ingested_at", "processed_at",
            "valid_until", "freshness_status",
            "affected_channels", "likely_positive_exposures",
            "likely_negative_exposures", "customer_safe",
        ):
            assert required in ev, f"missing {required!r}"

    def test_tape_is_customer_safe(self):
        cet.maybe_record_customer_event(
            headline="Fed cuts rates but warns inflation remains too high and future cuts may be slower.",
            source="test",
        )
        cet.maybe_record_customer_event(
            headline="Oil jumps 6 percent after supply disruption and tanker route closure.",
            source="test",
        )
        tape = cet.load_customer_event_tape()
        _assert_tape_safe(tape)

    def test_tape_persists_to_disk(self, _isolated_tape):
        cet.maybe_record_customer_event(
            headline="CPI comes in hotter than expected; yields jump and rate cut odds fall.",
            source="test",
        )
        assert _isolated_tape.exists()
        with open(_isolated_tape) as f:
            data = json.load(f)
        assert data["schema_version"] == cet._SCHEMA_VERSION
        assert data["events"]


# ---------------------------------------------------------------------------

class TestGetRecentEvents:

    def test_returns_recent_only(self):
        cet.maybe_record_customer_event(
            headline="Regional bank shares fall after deposit pressure and credit losses.",
            source="test",
        )
        recent = cet.get_recent_events(within_hours=4.0)
        assert recent
        very_old = cet.get_recent_events(within_hours=0.0)
        assert very_old == []

    def test_empty_when_no_tape(self, tmp_path, monkeypatch):
        # Redirect to a missing file
        missing = tmp_path / "absent.json"
        monkeypatch.setattr(cet, "_TAPE_PATH", str(missing))
        assert cet.get_recent_events() == []


# ---------------------------------------------------------------------------

class TestFreshnessStatus:

    def test_fresh_when_just_processed(self):
        now = datetime.now(UTC).isoformat()
        valid = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
        assert cet.compute_freshness_status(now, valid) == "fresh"

    def test_stale_when_past_valid_until(self):
        proc = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        valid = (datetime.now(UTC) - timedelta(hours=0.5)).isoformat()
        assert cet.compute_freshness_status(proc, valid) == "stale"

    def test_degraded_in_middle_window(self):
        proc = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
        valid = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
        assert cet.compute_freshness_status(proc, valid) == "degraded"

    def test_unknown_when_unparseable(self):
        assert cet.compute_freshness_status("not a date", None) == "unknown"
        assert cet.compute_freshness_status("", None) == "unknown"
