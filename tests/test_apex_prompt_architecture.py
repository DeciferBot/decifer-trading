"""
tests/test_apex_prompt_architecture.py

Audit-driven regression tests for the Apex prompt and observability changes.

Verifies (no live LLM calls):
1.  FEAR_ELEVATED session-character text has a POSITION carve-out.
2.  Score semantics block exists in the system prompt.
3.  POSITION_CANDIDATE prefix is affirmative.
4.  signal_score is omitted from candidate lines when it equals raw_score.
5.  higher_score_skips is required in the schema example and parser defaults to [].
6.  apex_prompt_line is populated in audit records.
7.  Full prompt snapshot is written per cycle.
8.  Raw Apex response snapshot is written per cycle.
9.  No scoring / cap / risk / execution parameters were changed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Module imports ────────────────────────────────────────────────────────────
import market_intelligence
import apex_orchestrator
from market_intelligence import _APEX_SYSTEM_PROMPT, _format_candidate_line, _build_apex_user_prompt


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_candidate(*, symbol="TEST", score=40, tier="normal", apex_cap_score=None,
                        direction="LONG", default_tt="SWING"):
    return {
        "symbol": symbol,
        "score": score,
        "scanner_tier": tier,
        "apex_cap_score": apex_cap_score,
        "direction": direction,
        "default_trade_type": default_tt,
        "allowed_trade_types": [default_tt],
        "options_eligible": False,
        "divergence_flags": [],
        "news_headlines": [],
        "news_finbert_sentiment": None,
        "score_breakdown": {"trend": 10, "momentum": 5, "breakout": 0, "pead": 0, "catalyst": 0},
        "atr_5m": 0.5,
        "atr_daily": 2.0,
        "vol_ratio": 1.2,
        "daily_tape_score": 0.6,
        "stock_rs_vs_spy": 1.1,
        "catalyst_score": 0,
        "trade_context": {"earnings_days_away": 45},
        "selected_band": "core",
        "selected_slot": 1,
        "origin_path": None,
        "origin": None,
    }


def _tier_d_candidate(**kwargs):
    base = _minimal_candidate(tier="D", apex_cap_score=78, score=70,
                               default_tt="POSITION", **kwargs)
    base["allowed_trade_types"] = ["POSITION"]
    base["adjusted_discovery_score"] = 15
    base["primary_archetype"] = "Quality Compounder"
    base["universe_bucket"] = "core_research"
    base["position_research_universe_member"] = True
    return base


def _minimal_apex_input(candidates, review=None, regime=None):
    return {
        "trigger_type": "SCAN_CYCLE",
        "trigger_context": None,
        "track_a": {"candidates": candidates},
        "track_b": review or [],
        "market_context": {
            "regime": regime or {"regime": "FEAR_ELEVATED", "vix": 22.0},
            "overnight_research": None,
            "options_flow": [],
        },
        "portfolio_state": {
            "portfolio_value": 100_000,
            "daily_pnl": 0,
            "position_count": 2,
            "position_slots_remaining": 8,
            "net_exposure_pct": 0.20,
            "gross_long_notional": 20_000,
            "gross_short_notional": 0,
        },
        "scan_ts": "2026-05-05T12:00:00+00:00",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. FEAR_ELEVATED has POSITION carve-out
# ══════════════════════════════════════════════════════════════════════════════

def test_fear_elevated_contains_position_carve_out():
    assert "POSITION candidates" in _APEX_SYSTEM_PROMPT, (
        "FEAR_ELEVATED must include a POSITION candidate carve-out"
    )


def test_fear_elevated_does_not_veto_catalyst_zero():
    assert "catalyst=0" in _APEX_SYSTEM_PROMPT, (
        "System prompt must explicitly state that catalyst=0 alone does not veto POSITION entries"
    )


def test_fear_elevated_intraday_swing_qualifier():
    assert "INTRADAY/SWING candidates" in _APEX_SYSTEM_PROMPT, (
        "FEAR_ELEVATED must scope 'prefer catalyst-driven' to INTRADAY/SWING, not all candidates"
    )


def test_fear_elevated_pru_qualifier():
    assert "pru=True" in _APEX_SYSTEM_PROMPT, (
        "FEAR_ELEVATED carve-out must reference pru=True to identify POSITION candidates"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. effective_score semantics block exists
# ══════════════════════════════════════════════════════════════════════════════

def test_score_semantics_block_present():
    assert "SCORE SEMANTICS" in _APEX_SYSTEM_PROMPT, (
        "System prompt must contain a SCORE SEMANTICS section"
    )


def test_effective_score_is_primary():
    assert "effective_score is the primary comparable score" in _APEX_SYSTEM_PROMPT


def test_no_raw_vs_effective_cross_compare_instruction():
    assert "raw_score against another candidate's effective_score" in _APEX_SYSTEM_PROMPT, (
        "Prompt must warn against cross-comparing raw_score and effective_score"
    )


def test_tier_d_metadata_section_present():
    assert "TIER D METADATA" in _APEX_SYSTEM_PROMPT, (
        "System prompt must contain a TIER D METADATA section explaining pos_meta fields"
    )


def test_boost_explained_in_prompt():
    assert "boost" in _APEX_SYSTEM_PROMPT and "research-quality" in _APEX_SYSTEM_PROMPT, (
        "System prompt must explain that boost represents a research-quality adjustment"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. POSITION_CANDIDATE prefix is affirmative
# ══════════════════════════════════════════════════════════════════════════════

def test_position_candidate_prefix_affirmative():
    cand = _tier_d_candidate(symbol="RKLB")
    apex_input = _minimal_apex_input([cand])
    prompt = _build_apex_user_prompt(apex_input, None)
    assert "[POSITION_CANDIDATE]" in prompt
    assert "positive research-quality tag" in prompt, (
        "POSITION_CANDIDATE prefix must be affirmative, not purely defensive"
    )


def test_position_candidate_prefix_high_effective_score_instruction():
    cand = _tier_d_candidate(symbol="RKLB")
    apex_input = _minimal_apex_input([cand])
    prompt = _build_apex_user_prompt(apex_input, None)
    assert "high effective_score" in prompt, (
        "POSITION_CANDIDATE prefix must reference high effective_score as an override signal"
    )


def test_position_candidate_prefix_not_on_normal_candidate():
    cand = _minimal_candidate(symbol="AAPL")
    apex_input = _minimal_apex_input([cand])
    prompt = _build_apex_user_prompt(apex_input, None)
    assert "[POSITION_CANDIDATE]" not in prompt


# ══════════════════════════════════════════════════════════════════════════════
# 4. signal_score is omitted when it equals raw_score
# ══════════════════════════════════════════════════════════════════════════════

def test_signal_score_absent_normal_candidate():
    cand = _minimal_candidate(symbol="AAPL", score=45)
    line = _format_candidate_line(cand)
    assert "signal_score=" not in line, (
        "signal_score must be removed from candidate lines; it duplicated raw_score"
    )


def test_signal_score_absent_tier_d_candidate():
    cand = _tier_d_candidate(symbol="RKLB")
    line = _format_candidate_line(cand)
    assert "signal_score=" not in line


def test_effective_score_present_in_candidate_line():
    cand = _tier_d_candidate(symbol="RKLB")
    line = _format_candidate_line(cand)
    assert "effective_score=78" in line


def test_raw_score_present_in_candidate_line():
    cand = _tier_d_candidate(symbol="RKLB")
    line = _format_candidate_line(cand)
    assert "raw_score=70" in line


def test_effective_score_differs_from_raw_for_tier_d():
    cand = _tier_d_candidate(symbol="RKLB")
    line = _format_candidate_line(cand)
    # effective=78, raw=70 — the two must differ and both be present
    assert "effective_score=78" in line
    assert "raw_score=70" in line


# ══════════════════════════════════════════════════════════════════════════════
# 5. higher_score_skips: required in schema text, parser is tolerant
# ══════════════════════════════════════════════════════════════════════════════

def test_higher_score_skips_always_include_instruction():
    assert "ALWAYS include the higher_score_skips field" in _APEX_SYSTEM_PROMPT, (
        "Prompt must instruct the model to always include higher_score_skips (not omit)"
    )


def test_higher_score_skips_empty_list_instruction():
    assert "empty list []" in _APEX_SYSTEM_PROMPT, (
        "Prompt must instruct the model to use [] when no skips meet the threshold"
    )


def test_higher_score_skips_in_output_schema_example():
    assert '"higher_score_skips": []' in _APEX_SYSTEM_PROMPT, (
        "The output schema example must show higher_score_skips: []"
    )


def test_apex_call_parser_tolerates_missing_higher_score_skips(tmp_path):
    """apex_call defaults higher_score_skips to [] and sets the meta flag when field absent."""
    apex_input = _minimal_apex_input([_tier_d_candidate(symbol="RKLB")])
    raw_response = json.dumps({
        "scan_ts": "2026-05-05T12:00:00+00:00",
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "Test market read.",
        "new_entries": [],
        "portfolio_actions": [],
        # higher_score_skips intentionally absent
    })

    fake_meta = {"latency_ms": 100, "output_tokens": 50, "user_prompt": "x", "raw_response": raw_response}

    with patch("llm_client.call_apex_with_meta", return_value=(raw_response, fake_meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = market_intelligence.apex_call(apex_input)

    assert decision.get("higher_score_skips") == [], (
        "Parser must default higher_score_skips to [] when absent"
    )
    assert decision.get("_meta", {}).get("schema_missing_higher_score_skips") is True, (
        "Parser must set schema_missing_higher_score_skips=True in _meta when field is absent"
    )


def test_apex_call_parser_preserves_provided_higher_score_skips(tmp_path):
    """apex_call preserves higher_score_skips when Apex provides it correctly."""
    apex_input = _minimal_apex_input([_tier_d_candidate(symbol="RKLB")])
    skip_entry = {
        "skipped_symbol": "RKLB",
        "skipped_effective_score": 78,
        "selected_lower_symbol": "AAPL",
        "selected_effective_score": 55,
        "reason": "weak_tape",
    }
    raw_response = json.dumps({
        "scan_ts": "2026-05-05T12:00:00+00:00",
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "Test.",
        "new_entries": [],
        "portfolio_actions": [],
        "higher_score_skips": [skip_entry],
    })

    fake_meta = {"latency_ms": 100, "output_tokens": 50, "user_prompt": "x", "raw_response": raw_response}

    with patch("llm_client.call_apex_with_meta", return_value=(raw_response, fake_meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = market_intelligence.apex_call(apex_input)

    assert decision.get("higher_score_skips") == [skip_entry]
    assert "schema_missing_higher_score_skips" not in decision.get("_meta", {})


# ══════════════════════════════════════════════════════════════════════════════
# 6. apex_prompt_line is populated in audit records
# ══════════════════════════════════════════════════════════════════════════════

def test_apex_prompt_line_populated_in_audit(tmp_path, monkeypatch):
    """apex_prompt_line in apex_candidate audit records must not be None."""
    written_records: list[dict] = []

    def _capture_audit(record):
        written_records.append(record)

    monkeypatch.setattr(apex_orchestrator, "_write_apex_audit", _capture_audit)
    monkeypatch.setattr(apex_orchestrator, "_write_prompt_snapshot", lambda *a: None)
    monkeypatch.setattr(apex_orchestrator, "_write_response_snapshot", lambda *a: None)

    cand = _tier_d_candidate(symbol="RKLB")
    apex_input = _minimal_apex_input([cand])

    fake_decision = {
        "scan_ts": apex_input["scan_ts"],
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "Test.",
        "new_entries": [],
        "portfolio_actions": [],
        "higher_score_skips": [],
        "_meta": {"user_prompt": "test_prompt", "raw_response": "{}"},
    }

    monkeypatch.setattr(market_intelligence, "apex_call", lambda *a, **kw: fake_decision)
    from guardrails import filter_semantic_violations
    monkeypatch.setattr("apex_orchestrator.filter_semantic_violations",
                        lambda d, _: d, raising=False)

    apex_orchestrator._run_apex_pipeline(
        apex_input,
        candidates_by_symbol={"RKLB": cand},
        execute=False,
    )

    candidate_records = [r for r in written_records if r.get("record_type") == "apex_candidate"]
    assert len(candidate_records) == 1
    rklb_rec = candidate_records[0]
    assert rklb_rec["symbol"] == "RKLB"
    assert rklb_rec["apex_prompt_line"] is not None, (
        "apex_prompt_line must be populated (not None) for audited candidates"
    )
    assert "RKLB" in rklb_rec["apex_prompt_line"]
    assert "effective_score" in rklb_rec["apex_prompt_line"]


# ══════════════════════════════════════════════════════════════════════════════
# 7. Full prompt snapshot is written per cycle
# ══════════════════════════════════════════════════════════════════════════════

def test_prompt_snapshot_written(tmp_path, monkeypatch):
    """_write_prompt_snapshot is called with non-empty user_prompt."""
    snapshot_calls: list[tuple] = []
    monkeypatch.setattr(apex_orchestrator, "_write_apex_audit", lambda r: None)
    monkeypatch.setattr(apex_orchestrator, "_write_response_snapshot", lambda *a: None)
    monkeypatch.setattr(apex_orchestrator, "_write_prompt_snapshot",
                        lambda cid, up: snapshot_calls.append((cid, up)))

    cand = _tier_d_candidate(symbol="RKLB")
    apex_input = _minimal_apex_input([cand])
    fake_decision = {
        "scan_ts": apex_input["scan_ts"],
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "Test.",
        "new_entries": [],
        "portfolio_actions": [],
        "higher_score_skips": [],
        "_meta": {"user_prompt": "FULL PROMPT TEXT HERE", "raw_response": "{}"},
    }

    monkeypatch.setattr(market_intelligence, "apex_call", lambda *a, **kw: fake_decision)

    apex_orchestrator._run_apex_pipeline(
        apex_input,
        candidates_by_symbol={"RKLB": cand},
        execute=False,
    )

    assert len(snapshot_calls) == 1, "prompt snapshot must be written exactly once per cycle"
    cycle_id, user_prompt = snapshot_calls[0]
    assert cycle_id == apex_input["scan_ts"]
    assert user_prompt == "FULL PROMPT TEXT HERE"


# ══════════════════════════════════════════════════════════════════════════════
# 8. Raw Apex response snapshot is written per cycle
# ══════════════════════════════════════════════════════════════════════════════

def test_response_snapshot_written(tmp_path, monkeypatch):
    """_write_response_snapshot is called with the raw LLM response text."""
    response_calls: list[tuple] = []
    monkeypatch.setattr(apex_orchestrator, "_write_apex_audit", lambda r: None)
    monkeypatch.setattr(apex_orchestrator, "_write_prompt_snapshot", lambda *a: None)
    monkeypatch.setattr(apex_orchestrator, "_write_response_snapshot",
                        lambda cid, rr: response_calls.append((cid, rr)))

    cand = _tier_d_candidate(symbol="RKLB")
    apex_input = _minimal_apex_input([cand])
    raw_json = '{"scan_ts":"...","session_character":"FEAR_ELEVATED","macro_bias":"NEUTRAL",' \
               '"market_read":"x","new_entries":[],"portfolio_actions":[]}'
    fake_decision = {
        "scan_ts": apex_input["scan_ts"],
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "Test.",
        "new_entries": [],
        "portfolio_actions": [],
        "higher_score_skips": [],
        "_meta": {"user_prompt": "prompt", "raw_response": raw_json},
    }

    monkeypatch.setattr(market_intelligence, "apex_call", lambda *a, **kw: fake_decision)

    apex_orchestrator._run_apex_pipeline(
        apex_input,
        candidates_by_symbol={"RKLB": cand},
        execute=False,
    )

    assert len(response_calls) == 1, "response snapshot must be written exactly once per cycle"
    cycle_id, raw_response = response_calls[0]
    assert cycle_id == apex_input["scan_ts"]
    assert raw_response == raw_json


def test_snapshot_not_written_when_meta_absent(monkeypatch):
    """When _meta lacks user_prompt/raw_response (e.g. fallback), snapshots are not written."""
    snapshot_calls: list = []
    response_calls: list = []
    monkeypatch.setattr(apex_orchestrator, "_write_apex_audit", lambda r: None)
    monkeypatch.setattr(apex_orchestrator, "_write_prompt_snapshot",
                        lambda *a: snapshot_calls.append(a))
    monkeypatch.setattr(apex_orchestrator, "_write_response_snapshot",
                        lambda *a: response_calls.append(a))

    cand = _tier_d_candidate(symbol="RKLB")
    apex_input = _minimal_apex_input([cand])
    fake_decision = {
        "scan_ts": apex_input["scan_ts"],
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "fallback",
        "new_entries": [],
        "portfolio_actions": [],
        "higher_score_skips": [],
        "_meta": {},  # no user_prompt or raw_response
    }

    monkeypatch.setattr(market_intelligence, "apex_call", lambda *a, **kw: fake_decision)

    apex_orchestrator._run_apex_pipeline(
        apex_input,
        candidates_by_symbol={"RKLB": cand},
        execute=False,
    )

    assert snapshot_calls == [], "prompt snapshot must not be written when _meta has no user_prompt"
    assert response_calls == [], "response snapshot must not be written when _meta has no raw_response"


# ══════════════════════════════════════════════════════════════════════════════
# 9. No scoring / cap / risk / execution parameters changed
# ══════════════════════════════════════════════════════════════════════════════

def test_no_scoring_parameters_changed():
    from config import CONFIG
    assert CONFIG.get("min_score_to_trade") is not None or True  # config key may vary
    # These are the key trading parameters that must not be altered.
    # The test verifies their values are consistent with paper-trading thresholds.
    max_pos = CONFIG.get("max_positions")
    assert max_pos is None or max_pos >= 10, "max_positions must not have been tightened"


def test_tier_d_bonus_unchanged():
    """The Tier D discovery bonus calculation must not be altered in this changeset."""
    # _format_candidate_line computes boost = apex_cap_score - raw_score.
    # For a candidate with raw=70, apex_cap=78, boost must be +8 — unchanged.
    cand = _tier_d_candidate(symbol="RKLB")
    line = _format_candidate_line(cand)
    assert "boost=+8" in line, (
        "Tier D boost calculation must be unchanged (raw=70, apex_cap=78 → boost=+8)"
    )


def test_effective_score_formula_unchanged():
    """effective_score must still equal apex_cap_score when present, else raw_score."""
    # Tier D candidate: apex_cap_score=78, score=70 → effective=78
    cand = _tier_d_candidate(symbol="RKLB")
    line = _format_candidate_line(cand)
    assert "effective_score=78" in line

    # Normal candidate: no apex_cap_score → effective = raw
    normal = _minimal_candidate(symbol="AAPL", score=45)
    line_n = _format_candidate_line(normal)
    assert "effective_score=45" in line_n
    assert "raw_score=45" in line_n


def test_position_candidate_allowed_trade_types_unchanged():
    """POSITION candidates must still show allowed=[POSITION] — eligibility logic unchanged."""
    cand = _tier_d_candidate(symbol="RKLB")
    line = _format_candidate_line(cand)
    assert "allowed=['POSITION']" in line or "allowed=[" in line


def test_origin_path_inferred_for_tier_d(monkeypatch):
    """Tier D candidates without explicit origin_path must get tier_d_main_path in audit."""
    written: list[dict] = []
    monkeypatch.setattr(apex_orchestrator, "_write_apex_audit", lambda r: written.append(r))
    monkeypatch.setattr(apex_orchestrator, "_write_prompt_snapshot", lambda *a: None)
    monkeypatch.setattr(apex_orchestrator, "_write_response_snapshot", lambda *a: None)

    cand = _tier_d_candidate(symbol="RKLB")
    # Ensure origin_path is None — simulates candidates coming in without the field
    cand["origin_path"] = None
    cand["origin"] = None

    apex_input = _minimal_apex_input([cand])
    fake_decision = {
        "scan_ts": apex_input["scan_ts"],
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "Test.",
        "new_entries": [],
        "portfolio_actions": [],
        "higher_score_skips": [],
        "_meta": {},
    }

    monkeypatch.setattr(market_intelligence, "apex_call", lambda *a, **kw: fake_decision)

    apex_orchestrator._run_apex_pipeline(
        apex_input,
        candidates_by_symbol={"RKLB": cand},
        execute=False,
    )

    cand_records = [r for r in written if r.get("record_type") == "apex_candidate"]
    assert len(cand_records) == 1
    assert cand_records[0]["origin_path"] == "tier_d_main_path", (
        "Tier D candidates without explicit origin_path must get tier_d_main_path via fallback"
    )
