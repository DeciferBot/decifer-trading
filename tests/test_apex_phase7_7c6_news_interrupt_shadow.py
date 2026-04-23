"""
tests/test_apex_phase7_7c6_news_interrupt_shadow.py

Phase 7C.6 — NEWS_INTERRUPT shadow dispatch verification.

Verifies the Apex NEWS_INTERRUPT wiring without running any LLM call or
submitting any order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apex_divergence import classify, mirror_apex_decision, mirror_legacy_decision
from sentinel_agents import build_news_trigger_payload

_REPO = Path(__file__).resolve().parent.parent


def test_build_news_trigger_payload_shape():
    trigger = {
        "symbol": "NVDA",
        "headlines": ["NVDA reports blowout Q3; raises guidance"],
        "keyword_score": 6.0,
        "direction": "BULLISH",
        "urgency": "HIGH",
        "claude_sentiment": "BULLISH",
        "claude_confidence": 5,
        "claude_catalyst": "earnings beat",
        "sources": ["Reuters"],
    }
    payload = build_news_trigger_payload(
        trigger=trigger, open_positions=[], portfolio_value=100_000.0,
        daily_pnl=0.0, regime={"regime": "TRENDING_UP"},
    )
    assert payload["trigger_type"] == "NEWS_INTERRUPT"
    ctx = payload["trigger_context"]
    assert ctx["symbol"] == "NVDA"
    assert ctx["urgency"] == "HIGH"
    assert ctx["headline"] == trigger["headlines"][0]
    assert ctx["finbert_confidence"] == 5
    assert "track_a" in payload and "candidates" in payload["track_a"]
    assert payload["track_b"] == []
    assert "portfolio_state" in payload
    assert "scan_ts" in payload


def test_build_news_trigger_payload_with_scored_candidate_populates_track_a():
    trigger = {
        "symbol": "NVDA", "headlines": ["headline"], "keyword_score": 4.0,
        "direction": "BULLISH", "urgency": "MODERATE",
        "claude_sentiment": "NEUTRAL", "claude_confidence": 3,
    }
    scored = {"symbol": "NVDA", "score": 42, "direction": "LONG", "price": 500.0}
    payload = build_news_trigger_payload(
        trigger=trigger, open_positions=[], portfolio_value=100_000.0,
        daily_pnl=0.0, regime={}, scored_candidate=scored,
    )
    cands = payload["track_a"]["candidates"]
    assert len(cands) == 1 and cands[0]["symbol"] == "NVDA"


def test_news_interrupt_mirrors_tag_trigger_type():
    legacy = mirror_legacy_decision(
        cycle_id="news-NVDA-120000",
        trigger_type="NEWS_INTERRUPT",
        new_entries=[{"symbol": "NVDA", "direction": "LONG", "trade_type": "INTRADAY",
                      "instrument": "stock", "qty": 10}],
    )
    apex = mirror_apex_decision(
        cycle_id="news-NVDA-120000",
        trigger_type="NEWS_INTERRUPT",
        pipeline_result={
            "decision": {
                "new_entries": [{"symbol": "NVDA", "direction": "LONG",
                                 "trade_type": "INTRADAY", "instrument": "stock"}],
                "portfolio_actions": [],
            },
            "would_dispatch": [], "rejected": [], "note": "shadow",
        },
        candidates_by_symbol={"NVDA": {"price": 500.0, "score": 42, "atr_5m": 2.0}},
    )
    assert legacy["trigger_type"] == "NEWS_INTERRUPT"
    assert apex["trigger_type"] == "NEWS_INTERRUPT"


def test_news_interrupt_direction_conflict_is_high_severity():
    legacy = mirror_legacy_decision(
        cycle_id="news-X-1", trigger_type="NEWS_INTERRUPT",
        new_entries=[{"symbol": "NVDA", "direction": "LONG", "trade_type": "INTRADAY",
                      "instrument": "stock"}],
    )
    apex = mirror_apex_decision(
        cycle_id="news-X-1", trigger_type="NEWS_INTERRUPT",
        pipeline_result={
            "decision": {
                "new_entries": [{"symbol": "NVDA", "direction": "SHORT",
                                 "trade_type": "INTRADAY", "instrument": "stock"}],
                "portfolio_actions": [],
            },
            "note": "shadow",
        },
    )
    events = classify(legacy, apex)
    cats = [e.category for e in events]
    assert "DIRECTION_CONFLICT" in cats
    assert next(e for e in events if e.category == "DIRECTION_CONFLICT").severity == "HIGH"


def test_news_interrupt_entry_miss_apex_flagged():
    legacy = mirror_legacy_decision(
        cycle_id="news-Y-1", trigger_type="NEWS_INTERRUPT",
        new_entries=[{"symbol": "TSLA", "direction": "LONG", "trade_type": "INTRADAY",
                      "instrument": "stock", "qty": 5}],
    )
    apex = mirror_apex_decision(
        cycle_id="news-Y-1", trigger_type="NEWS_INTERRUPT",
        pipeline_result={"decision": {"new_entries": [], "portfolio_actions": []},
                         "note": "shadow"},
    )
    events = classify(legacy, apex)
    cats = [e.category for e in events]
    assert "ENTRY_MISS_APEX" in cats


def test_bot_sentinel_shadow_and_cutover_branches_still_wired():
    text = (_REPO / "bot_sentinel.py").read_text()
    assert "sentinel_legacy_pipeline_enabled" in text
    assert "should_run_apex_shadow" in text
    assert "build_news_trigger_payload" in text
    assert "_aorch_ni._run_apex_pipeline" in text
    assert "write_divergence_record" in text
    assert "run_sentinel_pipeline" in text


def test_sentinel_legacy_flag_post_cutover_default_is_false():
    """Phase 8 cutover complete: Sentinel legacy pipeline off; Apex NI is live."""
    from safety_overlay import sentinel_legacy_pipeline_enabled
    assert sentinel_legacy_pipeline_enabled() is False


def test_apex_shadow_flag_default_is_true():
    """Phase 8 Step 1: shadow logging stays on."""
    from safety_overlay import should_run_apex_shadow
    assert should_run_apex_shadow() is True
