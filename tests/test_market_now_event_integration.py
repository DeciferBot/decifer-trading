"""
test_market_now_event_integration.py — Sprint M11A end-to-end.

Covers:
  - build_market_now() loads the Event Tape, reconciles, and returns a payload
    that passes saas_intelligence_output validation
  - With a fresh tape + driver state, payload includes all Sprint M11A sections
  - Ceasefire scenario: known_conflicts surfaced when driver and event disagree
  - Nvidia scenario: known_conflicts surfaced from the event itself
  - Tape failure does not break the Market Map build
  - News hook (news.record_article_for_customer_tape) is fail-soft
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customer_event_tape as cet
import market_now_builder as mnb
from saas_intelligence_output import validate_customer_payload


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_pipeline(tmp_path, monkeypatch):
    """
    Build a synthetic intelligence pipeline in tmp_path: fresh driver state,
    fresh manifest, fresh theme activation, isolated event tape.
    """
    intel = tmp_path / "data" / "intelligence"
    live = tmp_path / "data" / "live"
    intel.mkdir(parents=True)
    live.mkdir(parents=True)

    (intel / "live_driver_state.json").write_text(json.dumps({
        "active_drivers": ["geopolitical_risk_rising", "yields_rising"],
        "blocked_conditions": [],
    }))
    (intel / "theme_activation.json").write_text(json.dumps({
        "themes": [
            {"theme_id": "defence", "state": "activated",
             "reason": "Geopolitical risk rising"},
            {"theme_id": "energy", "state": "strengthening",
             "reason": "Oil supply tight"},
        ],
    }))
    (live / "current_manifest.json").write_text(json.dumps({
        "market_regime": "TRENDING_UP",
        "published_at": datetime.now(UTC).isoformat(),
        "handoff_enabled": True,
    }))

    tape_file = intel / "customer_event_tape.json"
    monkeypatch.setattr(cet, "_TAPE_PATH", str(tape_file))
    monkeypatch.setattr(mnb, "_BASE", str(tmp_path))

    # Also redirect the reconciler's own freshness probe
    import market_now_reconciler as mnr
    monkeypatch.setattr(mnr, "_BASE", str(tmp_path))

    yield tmp_path


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestEndToEnd:

    def test_payload_validates_with_no_events(self, fake_pipeline):
        payload = mnb.get_market_now_dict()
        validate_customer_payload(payload)
        assert payload["market_regime_label"] == "Trending up"
        # M11A sections default to empty containers
        assert payload["key_events"] == []
        assert payload["known_conflicts"] == []

    def test_payload_validates_with_fresh_events(self, fake_pipeline):
        cet.maybe_record_customer_event(
            headline="Microsoft announces $40bn acquisition of cybersecurity platform.",
            symbols=["MSFT", "PANW"],
            source="test",
        )
        payload = mnb.get_market_now_dict()
        validate_customer_payload(payload)
        assert payload["key_events"], "expected key_events from fresh tape"
        assert payload["radar"], "expected radar entries from event symbols"

    def test_known_conflicts_with_ceasefire_and_geo_risk_driver(self, fake_pipeline):
        cet.maybe_record_customer_event(
            headline="US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
            source="test",
        )
        payload = mnb.get_market_now_dict()
        validate_customer_payload(payload)
        # Driver geopolitical_risk_rising vs event de_escalation → conflict
        assert payload["known_conflicts"]

    def test_known_conflicts_with_nvidia_event(self, fake_pipeline):
        cet.maybe_record_customer_event(
            headline="Nvidia beats revenue and raises guidance, but shares fall after hours on margin concern and high expectations.",
            symbols=["NVDA"],
            source="test",
        )
        payload = mnb.get_market_now_dict()
        validate_customer_payload(payload)
        # Event itself flags conflict
        assert any("headline" in c.lower() or "market" in c.lower()
                   for c in payload["known_conflicts"])

    def test_payload_section_freshness_populated(self, fake_pipeline):
        cet.maybe_record_customer_event(
            headline="CPI comes in hotter than expected; yields jump and rate cut odds fall.",
            source="test",
        )
        payload = mnb.get_market_now_dict()
        sf = payload["section_freshness"]
        assert "events" in sf
        assert sf["events"]["status"] == "fresh"
        assert "macro_drivers" in sf

    def test_reconciler_failure_does_not_break_build(self, fake_pipeline):
        # Force the reconciler to raise; build_market_now must still return
        # a valid customer payload (no execution coupling, no crash).
        with patch.object(mnb, "reconcile_market_map", side_effect=RuntimeError("boom")):
            payload = mnb.get_market_now_dict()
        validate_customer_payload(payload)
        # M11A sections fall back to safe empties
        assert payload["key_events"] == []
        assert payload["sectors"] == []

    def test_payload_is_customer_safe_no_internal_paths(self, fake_pipeline):
        cet.maybe_record_customer_event(
            headline="Oil jumps 6 percent after supply disruption and tanker route closure.",
            source="test",
        )
        payload = mnb.get_market_now_dict()
        # Verify no internal artefact names leak into string values
        payload_text = json.dumps(payload)
        for forbidden in (
            "live_driver_state.json", "theme_activation.json",
            "current_manifest.json", "training_records.jsonl",
            "DUP481326",
        ):
            assert forbidden not in payload_text, f"leaked {forbidden!r}"


# ─── Hook fail-soft tests ────────────────────────────────────────────────────

class TestNewsHookFailSoft:

    def test_record_article_does_not_raise_on_classifier_error(self, monkeypatch):
        import news
        # Make the imported maybe_record_customer_event raise
        with patch("customer_event_tape.maybe_record_customer_event",
                   side_effect=RuntimeError("boom")):
            # Should not raise
            news.record_article_for_customer_tape(
                headline="anything",
                symbols=["AAPL"],
                source="test",
            )

    def test_record_article_does_not_raise_on_empty_headline(self):
        import news
        # Should not raise
        news.record_article_for_customer_tape(headline="", symbols=[])
        news.record_article_for_customer_tape(headline=None, symbols=[])

    def test_record_article_with_no_symbols_still_works(self, tmp_path, monkeypatch):
        # Sprint requirement: macro headlines with no symbols must still record
        import news
        tape_file = tmp_path / "tape.json"
        monkeypatch.setattr(cet, "_TAPE_PATH", str(tape_file))

        news.record_article_for_customer_tape(
            headline="US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
            symbols=[],
            source="alpaca_benzinga",
        )

        assert tape_file.exists()
        with open(tape_file) as f:
            data = json.load(f)
        assert data["events"], "expected macro headline to be recorded"
