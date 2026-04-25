"""
Pass 1 Robustness — targeted regression tests for Weekend Robustness Audit fixes.

Covers:
  - market_intelligence._APEX_SYSTEM_PROMPT: entry floor and divergence_flags note
  - market_intelligence._format_review_line: pnl_pct=None guard
  - market_intelligence._fallback_decision: ERROR log emission
  - apex_orchestrator: zero-entries log at WARNING level
"""
import logging
import pytest
from unittest.mock import patch


# ── 1: System prompt contains the entry floor rule ────────────────────────────

def test_system_prompt_contains_entry_floor():
    from market_intelligence import _APEX_SYSTEM_PROMPT
    assert "ENTRY FLOOR RULE" in _APEX_SYSTEM_PROMPT, (
        "System prompt must contain ENTRY FLOOR RULE section"
    )


def test_system_prompt_entry_floor_references_score_threshold():
    from market_intelligence import _APEX_SYSTEM_PROMPT
    assert "score" in _APEX_SYSTEM_PROMPT.lower() and "35" in _APEX_SYSTEM_PROMPT, (
        "Entry floor rule must reference a score threshold (35)"
    )


def test_system_prompt_fear_elevated_not_avoid_mandate():
    from market_intelligence import _APEX_SYSTEM_PROMPT
    # Must explicitly say FEAR_ELEVATED is NOT a veto/mandate
    prompt_lower = _APEX_SYSTEM_PROMPT.lower()
    assert "not" in prompt_lower and "fear_elevated" in prompt_lower, (
        "System prompt must clarify FEAR_ELEVATED is not an AVOID mandate"
    )


# ── 2: System prompt contains divergence_flags clarification ──────────────────

def test_system_prompt_divergence_flags_not_stock_veto():
    from market_intelligence import _APEX_SYSTEM_PROMPT
    assert "divergence_flags" in _APEX_SYSTEM_PROMPT, (
        "System prompt must mention divergence_flags"
    )
    # The clarification must state they don't veto the stock trade
    assert "NOT veto" in _APEX_SYSTEM_PROMPT or "do NOT veto" in _APEX_SYSTEM_PROMPT, (
        "System prompt must explicitly say divergence_flags do NOT veto the stock trade"
    )


# ── 3: _format_review_line handles pnl_pct=None without TypeError ─────────────

def test_format_review_line_pnl_none_does_not_raise():
    from market_intelligence import _format_review_line
    p = {
        "symbol": "AAPL",
        "trade_type": "INTRADAY",
        "direction": "LONG",
        "pnl_pct": None,
        "days_held": 1,
        "flagged_reason": "drawdown",
        "entry_conviction_band": "MEDIUM",
        "current_conviction_band": "LOW",
        "earnings_days_away": 30,
    }
    result = _format_review_line(p)
    assert "n/a" in result, "_format_review_line must render pnl_pct=None as 'n/a'"


def test_format_review_line_pnl_valid_renders_percentage():
    from market_intelligence import _format_review_line
    p = {
        "symbol": "TSLA",
        "trade_type": "SWING",
        "direction": "SHORT",
        "pnl_pct": -0.0415,
        "days_held": 2,
        "flagged_reason": "drawdown",
        "entry_conviction_band": "HIGH",
        "current_conviction_band": "MEDIUM",
        "earnings_days_away": 10,
    }
    result = _format_review_line(p)
    assert "-4.15%" in result, "_format_review_line must render valid pnl_pct as percentage"


# ── 4: _fallback_decision emits ERROR log ─────────────────────────────────────

def test_fallback_decision_logs_error(caplog):
    from market_intelligence import _fallback_decision
    apex_input = {"scan_ts": "2026-04-25T09:00:00", "track_b": []}
    with caplog.at_level(logging.ERROR, logger="decifer.intelligence"):
        _fallback_decision(apex_input, reason="test_reason")
    assert any(
        "FALLBACK DECISION" in r.message and "test_reason" in r.message
        for r in caplog.records
    ), "_fallback_decision must emit ERROR log containing 'FALLBACK DECISION' and reason"


def test_fallback_decision_empty_reason_still_logs(caplog):
    from market_intelligence import _fallback_decision
    apex_input = {"scan_ts": "2026-04-25T09:00:00", "track_b": []}
    with caplog.at_level(logging.ERROR, logger="decifer.intelligence"):
        _fallback_decision(apex_input)
    assert any("FALLBACK DECISION" in r.message for r in caplog.records), (
        "_fallback_decision must emit ERROR log even with no reason"
    )


def test_fallback_decision_returns_empty_new_entries():
    from market_intelligence import _fallback_decision
    apex_input = {"scan_ts": "2026-04-25T09:00:00", "track_b": []}
    result = _fallback_decision(apex_input, reason="parse_error")
    assert result["new_entries"] == [], "fallback must return empty new_entries"
    assert result["market_read"].startswith("[fallback]"), "market_read must have [fallback] prefix"


# ── 5: apex_orchestrator zero-entries log is at WARNING level ─────────────────

def test_apex_orchestrator_zero_entries_logs_at_warning(caplog):
    """
    When candidates are presented but Apex returns zero new_entries, the
    observability log must fire at WARNING (not INFO) level.
    """
    import apex_orchestrator

    fake_decision = {
        "new_entries": [],
        "portfolio_actions": [],
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "test market read",
        "scan_ts": "2026-04-25T09:00:00",
    }
    fake_apex_input = {
        "trigger_type": "SCAN_CYCLE",
        "track_a": {"candidates": [{"symbol": "AAPL", "score": 40}]},
        "track_b": [],
    }

    with patch("market_intelligence.apex_call", return_value=fake_decision), \
         patch("guardrails.filter_semantic_violations", return_value=fake_decision), \
         patch("apex_orchestrator._summarise_dispatch", return_value=([], [])), \
         caplog.at_level(logging.WARNING, logger="decifer.apex_orchestrator"):
        apex_orchestrator._run_apex_pipeline(
            apex_input=fake_apex_input,
            execute=False,
        )

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("zero entries" in m for m in warning_msgs), (
        f"Expected WARNING-level 'zero entries' log, got: {warning_msgs}"
    )
