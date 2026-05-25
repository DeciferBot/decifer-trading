"""
test_market_now_reconciler.py — Sprint M11A.

Covers:
  - known_conflicts fired when price drivers + events disagree
    (geopolitical_risk_rising + de_escalation; geopolitical_risk_rising +
     oil_risk_premium_unwind; oil_supply_shock + de_escalation; etc.)
  - market_mood reflects event tape
  - sectors aggregate tailwind + headwind from events
  - radar surfaces symbols with reason / theme_link / confirmation /
    invalidation, with NO buy/sell/entry/exit/stop/target/position/PnL
  - section_freshness reports per-section status
  - what_changed / watch_next / themes built correctly
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customer_event_tape as cet
import market_now_reconciler as mnr


@pytest.fixture(autouse=True)
def _isolated_tape(tmp_path, monkeypatch):
    tape_file = tmp_path / "customer_event_tape.json"
    monkeypatch.setattr(cet, "_TAPE_PATH", str(tape_file))
    yield tape_file


def _seed(headline: str, symbols=None, source="test"):
    cet.maybe_record_customer_event(
        headline=headline,
        symbols=symbols or [],
        source=source,
    )


# ---------------------------------------------------------------------------

class TestKnownConflicts:

    def test_geo_risk_rising_vs_de_escalation(self):
        _seed("US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.")
        out = mnr.reconcile_market_map(
            active_drivers=["geopolitical_risk_rising"],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        conflicts = out["known_conflicts"]
        assert conflicts, "expected conflict for geopolitical_risk_rising + de_escalation"
        assert any("de-escalation" in c.lower() or "risk premium" in c.lower()
                   for c in conflicts)

    def test_oil_supply_shock_vs_de_escalation(self):
        _seed("US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.")
        out = mnr.reconcile_market_map(
            active_drivers=["oil_supply_shock"],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        assert out["known_conflicts"]

    def test_no_conflict_when_drivers_align(self):
        _seed("Oil jumps 6 percent after supply disruption and tanker route closure.")
        out = mnr.reconcile_market_map(
            active_drivers=["oil_supply_shock", "geopolitical_risk_rising"],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        # drivers and events agree → no conflict
        assert not out["known_conflicts"]

    def test_event_flagged_conflict_surfaces(self):
        _seed("Nvidia beats revenue and raises guidance, but shares fall after hours on margin concern and high expectations.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Trending up",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="Moderate",
        )
        # Event itself carries a known_conflicts entry
        assert out["known_conflicts"]
        assert any("headline" in c.lower() or "market" in c.lower()
                   for c in out["known_conflicts"])


# ---------------------------------------------------------------------------

class TestMarketMood:

    def test_de_escalation_flips_mood_to_risk_on(self):
        _seed("US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        assert "risk-on" in out["market_mood"].lower()

    def test_escalation_flips_mood_to_risk_off(self):
        _seed("Oil jumps 6 percent after supply disruption and tanker route closure.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Trending up",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        assert "risk-off" in out["market_mood"].lower()

    def test_falls_back_to_regime_when_no_events(self):
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Trending up",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        assert out["market_mood"] == "Trending up"


# ---------------------------------------------------------------------------

class TestSectors:

    def test_sectors_include_tailwind_and_headwind(self):
        _seed("US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        names = {s["name"] for s in out["sectors"]}
        moods = {s["mood"] for s in out["sectors"]}
        assert "tailwind" in moods
        assert "headwind" in moods
        # Energy should be a headwind for the unwind event
        assert "energy" in names


# ---------------------------------------------------------------------------

class TestRadarSafety:

    def test_radar_has_no_trade_recommendations(self):
        _seed("Microsoft announces $40bn acquisition of cybersecurity platform.",
              symbols=["MSFT", "PANW"])
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Trending up",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        radar = out["radar"]
        assert radar, "expected radar entries when symbols present"
        for entry in radar:
            keys = set(entry.keys())
            # Must NOT contain anything trade-actionable
            banned = {
                "buy", "sell", "entry", "exit", "stop", "target",
                "position_size", "trade_recommendation",
                "execution_readiness", "account_exposure", "pnl",
            }
            assert not (keys & banned), f"radar entry has banned keys: {keys & banned}"
            # Must contain the customer-safe fields the sprint specified
            assert "symbol" in keys
            assert "reason_to_watch" in keys
            assert "theme_link" in keys
            assert "confirmation_signal" in keys
            assert "invalidation_signal" in keys


# ---------------------------------------------------------------------------

class TestSectionFreshness:

    def test_all_sections_have_status(self):
        _seed("CPI comes in hotter than expected; yields jump and rate cut odds fall.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Trending down",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        sf = out["section_freshness"]
        for sec in ("events", "macro_drivers", "sectors", "themes", "radar", "ask_context"):
            assert sec in sf, f"missing section {sec!r}"
            assert "status" in sf[sec]

    def test_events_freshness_marks_fresh(self):
        _seed("CPI comes in hotter than expected; yields jump and rate cut odds fall.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Trending down",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        assert out["section_freshness"]["events"]["status"] == "fresh"


# ---------------------------------------------------------------------------

class TestWhatChangedAndWatchNext:

    def test_what_changed_lists_recent_events(self):
        _seed("Regional bank shares fall after deposit pressure and credit losses.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        assert out["what_changed"]
        assert any("bank" in s.lower() or "credit" in s.lower()
                   for s in out["what_changed"])

    def test_watch_next_populated_from_signals(self):
        _seed("Regional bank shares fall after deposit pressure and credit losses.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        assert out["watch_next"]


# ---------------------------------------------------------------------------

class TestThemes:

    def test_active_theme_picks_up_event_signal(self):
        _seed("US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=["energy"],
            theme_states={"energy": "active"},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        themes = out["themes"]
        energy = next((t for t in themes if t["theme"] == "energy"), None)
        assert energy is not None
        assert energy.get("event_signal") == "weakening"

    def test_event_only_themes_surface_as_watch(self):
        _seed("US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.")
        out = mnr.reconcile_market_map(
            active_drivers=[],
            blocked_conditions=[],
            active_theme_ids=[],
            theme_states={},
            regime_label="Choppy",
            apex_read="",
            manifest_published_at=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        themes = out["themes"]
        # risk_on_rotation should appear as event-only "watch"
        watch_themes = [t for t in themes if t.get("state") == "watch"]
        assert any(t["theme"] == "risk_on_rotation" for t in watch_themes)
