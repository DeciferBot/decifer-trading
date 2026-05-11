"""
tests/test_market_read_json_extraction.py

Regression tests for the market_read JSON extraction bug fixed in market_intelligence.apex_call.

Root cause (2026-05-11):
    apex_call() used raw.rfind("}") to locate the end of the Apex JSON response.
    When Claude appended trailing content (commentary, a second JSON block, etc.)
    after the primary decision object, rfind("}") picked up the LAST "}" in the
    full response.  The resulting slice contained valid JSON PLUS extra text, causing
    json.loads() to raise "Extra data: line X column 1".  _fallback_decision() was
    called, new_entries was suppressed, and FLOOR_RULE_VIOLATION was logged.

    Fix: replace rfind("}")+json.loads() with json.JSONDecoder().raw_decode(raw, start),
    which extracts exactly the first complete JSON object and ignores trailing content.

Tests cover requirements A–G from the fix spec:
    A. valid market_read parses cleanly
    B. double-serialized JSON is detected and handled
    C. concatenated JSON raises a structured validation error  ← now fixed: parses first obj
    D. JSONL-style multiple objects are handled (first is used)
    E. Apex receives a dict/object, not a raw malformed string
    F. FLOOR_RULE_VIOLATION is not triggered when market_read is valid + high-score candidates
    G. malformed market_read produces a clear fail-closed log, not a vague fallback
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

import market_intelligence
from market_intelligence import _fallback_decision, apex_call


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _minimal_apex_input():
    return {
        "trigger_type": "SCAN_CYCLE",
        "trigger_context": None,
        "track_a": {
            "candidates": [
                {
                    "symbol": "IWM",
                    "score": 68,
                    "direction": "LONG",
                    "scanner_tier": "normal",
                    "apex_cap_score": 68,
                    "default_trade_type": "SWING",
                    "allowed_trade_types": ["SWING"],
                    "options_eligible": False,
                    "divergence_flags": [],
                    "news_headlines": [],
                    "news_finbert_sentiment": None,
                    "score_breakdown": {"trend": 10, "momentum": 10},
                    "atr_5m": 0.5,
                    "atr_daily": 2.0,
                    "vol_ratio": 1.2,
                    "daily_tape_score": 0.6,
                    "stock_rs_vs_spy": 1.1,
                    "catalyst_score": 0,
                    "trade_context": {"earnings_days_away": 90},
                    "selected_band": "core",
                    "selected_slot": 1,
                    "origin_path": None,
                    "origin": None,
                }
            ]
        },
        "track_b": [],
        "market_context": {
            "regime": {"regime": "FEAR_ELEVATED", "vix": 17.0},
            "overnight_research": None,
            "options_flow": [],
        },
        "portfolio_state": {
            "portfolio_value": 1_000_000,
            "daily_pnl": 0,
            "position_count": 5,
            "position_slots_remaining": 95,
            "net_exposure_pct": 0.05,
            "gross_long_notional": 50_000,
            "gross_short_notional": 0,
        },
        "scan_ts": "2026-05-08T21:07:00+00:00",
    }


def _valid_apex_response(**overrides) -> str:
    """Minimal schema-valid Apex response JSON string."""
    obj = {
        "scan_ts": "2026-05-08T21:07:00+00:00",
        "session_character": "FEAR_ELEVATED",
        "macro_bias": "NEUTRAL",
        "market_read": "Market is cautious but momentum names are holding.",
        "new_entries": [
            {
                "symbol": "IWM",
                "trade_type": "SWING",
                "direction": "LONG",
                "conviction": "HIGH",
                "instrument": "stock",
                "direction_flipped": False,
                "counter_argument": "Short-term overbought.",
                "key_risk": "Rate surprise.",
                "reasoning": "IWM reclaiming 200d MA with momentum.",
            }
        ],
        "portfolio_actions": [],
        "higher_score_skips": [],
    }
    obj.update(overrides)
    return json.dumps(obj)


_FAKE_META = {"latency_ms": 100, "output_tokens": 200,
              "user_prompt": "x", "raw_response": ""}


# ── A. Valid response parses cleanly ──────────────────────────────────────────

def test_valid_response_parses_cleanly():
    """Requirement A: a well-formed Apex JSON response produces a real decision."""
    raw = _valid_apex_response()
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    assert decision.get("new_entries"), "Valid response must produce entries"
    assert decision["new_entries"][0]["symbol"] == "IWM"
    assert "[fallback]" not in (decision.get("market_read") or "")


# ── B. Double-serialized JSON is detected ─────────────────────────────────────

def test_double_serialized_json_triggers_fallback():
    """Requirement B: double-serialized JSON (json string inside json string) is detected.

    If Claude returns json.dumps(json.dumps(obj)) — a quoted JSON string —
    raw.find("{") returns -1 (no leading brace) so raw_decode raises ValueError
    and we get a structured fallback, not a silent parse of garbage.
    """
    obj = _valid_apex_response()
    raw = json.dumps(obj)  # now the response is a JSON *string*, not an object
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    # raw starts with '"', so find("{") returns -1 → ValueError → fallback
    assert decision.get("new_entries") == [], "Double-serialized response must fall back to empty entries"
    assert "[fallback]" in decision.get("market_read", ""), "Fallback market_read must carry [fallback] prefix"


# ── C. Concatenated JSON: first object is used, not rejected ─────────────────

def test_concatenated_json_uses_first_object():
    """Requirement C (updated): concatenated JSON no longer raises — raw_decode
    extracts the first object and ignores the trailing block.

    This IS the exact failure pattern from the May 8 FLOOR_RULE_VIOLATIONs:
    Claude output a valid decision JSON followed by a second JSON blob.
    rfind("}") grabbed the last "}" and json.loads() got "Extra data".
    raw_decode correctly ignores the trailing block.
    """
    first = _valid_apex_response()
    trailing = '\n{"note": "extra commentary from model", "value": 42}'
    raw = first + trailing
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    # Must parse the first object successfully — NOT fall back
    assert decision.get("new_entries"), (
        "Concatenated response must parse first JSON object, not fall back; "
        "this was the root cause of the May 8 FLOOR_RULE_VIOLATION events"
    )
    assert decision["new_entries"][0]["symbol"] == "IWM"
    assert "[fallback]" not in (decision.get("market_read") or "")


def test_concatenated_json_with_text_commentary_uses_first_object():
    """Concatenated pattern: JSON + plain text + another JSON block."""
    first = _valid_apex_response()
    trailing = "\n\nHere is my analysis:\nThe market looked strong today.\n{\"extra\": true}"
    raw = first + trailing
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    assert decision.get("new_entries"), "Text+JSON suffix must not prevent parsing the first object"
    assert "[fallback]" not in (decision.get("market_read") or "")


# ── D. JSONL-style multiple objects: first is used ────────────────────────────

def test_jsonl_multiple_objects_uses_first():
    """Requirement D: JSONL-style response (two JSON objects on separate lines) uses the first."""
    first = _valid_apex_response()
    second_line = json.dumps({"session_character": "MOMENTUM_BULL", "new_entries": [], "portfolio_actions": []})
    raw = first + "\n" + second_line
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    # Must use the FIRST object (FEAR_ELEVATED with IWM entry), not the second
    assert decision.get("session_character") == "FEAR_ELEVATED"
    assert decision.get("new_entries"), "First JSONL object must be used"


# ── E. Apex receives a dict, not a raw string ─────────────────────────────────

def test_apex_decision_is_dict_not_string():
    """Requirement E: apex_call always returns a dict (parsed object), never a raw string."""
    raw = _valid_apex_response()
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    assert isinstance(decision, dict), "apex_call must always return a dict"
    assert isinstance(decision.get("new_entries"), list), "new_entries must be a list, not a string"


def test_apex_fallback_is_also_dict():
    """Fallback decisions (on any error) must also be dicts, not strings."""
    raw = "not json at all {{ garbage"
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    assert isinstance(decision, dict), "Fallback must be a dict"
    assert isinstance(decision.get("new_entries"), list)
    assert decision["new_entries"] == []


# ── F. No FLOOR_RULE_VIOLATION when market_read valid + high-score candidates ─

def test_no_floor_rule_violation_when_valid_response_with_entries(caplog):
    """Requirement F: valid market_read + high-score candidates + entries → no FLOOR_RULE_VIOLATION."""
    raw = _valid_apex_response()
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None), \
         caplog.at_level(logging.ERROR):
        decision = apex_call(_minimal_apex_input())

    assert "FLOOR_RULE_VIOLATION" not in caplog.text
    assert "parse_error" not in caplog.text
    assert decision.get("new_entries"), "Valid response must not suppress entries"


def test_no_parse_error_logged_for_concatenated_response(caplog):
    """The fixed concatenated-JSON case must not log parse_error."""
    first = _valid_apex_response()
    raw = first + '\n{"trailing": "block"}'
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None), \
         caplog.at_level(logging.ERROR):
        decision = apex_call(_minimal_apex_input())

    assert "parse_error" not in caplog.text, (
        "Concatenated JSON must parse cleanly — no error log expected"
    )
    assert "JSON parse failed" not in caplog.text


# ── G. Malformed market_read produces structured fail-closed log ───────────────

def test_truly_invalid_response_logs_structured_error(caplog):
    """Requirement G: a genuinely unparseable response logs stage, cycle_id, candidates, and raw[:500]."""
    raw = "This is not JSON at all. No braces whatsoever."
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None), \
         caplog.at_level(logging.ERROR):
        decision = apex_call(_minimal_apex_input())

    # Must fall back closed
    assert decision["new_entries"] == [], "Unparseable response must suppress entries"
    assert "[fallback]" in decision.get("market_read", "")

    # Must log structured error with required fields
    error_text = caplog.text
    assert "JSON parse failed" in error_text, "Error log must say 'JSON parse failed'"
    assert "SCAN_CYCLE" in error_text, "Error log must include stage (trigger_type)"
    assert "2026-05-08T21:07" in error_text, "Error log must include cycle_id (scan_ts)"
    assert "candidates=1" in error_text, "Error log must include candidate count"


def test_truly_invalid_response_fallback_has_parse_error_reason():
    """market_read on invalid-JSON fallback must carry parse_error: prefix."""
    raw = "}}not json{{"
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    mr = decision.get("market_read", "")
    assert mr.startswith("[fallback]"), f"market_read must start with [fallback], got: {mr!r}"
    assert "parse_error" in mr, f"market_read must contain parse_error, got: {mr!r}"


# ── Regression: rfind behaviour that caused the May 8 failures ────────────────

@pytest.mark.parametrize("suffix", [
    # All patterns observed or logically derived from "Extra data: line X column 1"
    '\n{"note": "trailing commentary"}',
    '\n\n{"session_character": "MOMENTUM_BULL", "new_entries": []}',
    '\nHere is my reasoning:\n{"internal": "thoughts"}',
    "\n\nThe above JSON represents my analysis.\n{\"meta\": true}",
])
def test_trailing_content_variants_all_parse_correctly(suffix):
    """All trailing-content variants that would have caused Extra data failures before the fix."""
    raw = _valid_apex_response() + suffix
    meta = {**_FAKE_META, "raw_response": raw}

    with patch("llm_client.call_apex_with_meta", return_value=(raw, meta)), \
         patch("market_intelligence._build_session_context", return_value=None):
        decision = apex_call(_minimal_apex_input())

    suffix_label = repr(suffix)[:60]
    assert decision.get("new_entries"), (
        f"Trailing content {suffix_label} must not suppress entries"
    )
    assert "[fallback]" not in (decision.get("market_read") or ""), (
        f"Trailing content {suffix_label} must not trigger fallback"
    )
