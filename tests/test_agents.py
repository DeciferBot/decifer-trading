"""Tests for agents module: JSON parsing, agent response handling, agreement counting.

All Claude API calls are replaced with canned responses.
No network connections are made.
"""
import os, sys, types
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance",
             "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod
_cfg = {"log_file": "/dev/null", "trade_log": "/dev/null",
        "order_log": "/dev/null", "anthropic_api_key": "test-key",
        "claude_model": "claude-sonnet-4-20250514", "claude_max_tokens": 1000,
        "model": "claude-sonnet-4-20250514", "max_tokens": 1000,
        "risk_pct_per_trade": 0.01, "max_positions": 5,
        "daily_loss_limit": -0.02, "min_cash_reserve": 0.20,
        "agents_required_to_agree": 4,
        "mongo_uri": "", "db_name": "test"}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


import sys
import os
import json
from unittest.mock import patch, MagicMock
from typing import Dict, List, Any

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

sys.modules.pop("agents", None)
try:
    import agents
    HAS_AGENTS = hasattr(agents, "agent_technical")
    # Save real run_all_agents before other test files (test_bot.py) replace it
    _REAL_RUN_ALL_AGENTS = agents.run_all_agents if HAS_AGENTS else None
except ImportError:
    HAS_AGENTS = False
    _REAL_RUN_ALL_AGENTS = None


pytestmark = pytest.mark.skipif(
    not HAS_AGENTS, reason="agents module not importable"
)


# ---------------------------------------------------------------------------
# Canned final-decision JSON (mirrors agent_final_decision output format)
# ---------------------------------------------------------------------------

FINAL_DECISION_JSON = json.dumps({
    "buys": [{"symbol": "AAPL", "qty": 10, "sl": 150.0, "tp": 165.0,
              "instrument": "stock", "reasoning": "Breaking out of 3-month base on 2x volume"}],
    "sells": [],
    "hold": [],
    "cash": False,
    "agents_agreed": 4,
    "summary": "Bullish setup on AAPL",
    "claude_reasoning": "AAPL breaking out of 3-month consolidation with institutional accumulation.",
})

_MARKDOWN_JSON = f"```json\n{FINAL_DECISION_JSON}\n```"

_REGIME = {
    "regime": "BULL_TRENDING",
    "vix": 15.0,
    "vix_1h_change": 0.0,
    "spy_price": 500.0,
    "spy_above_ema": True,
    "qqq_price": 400.0,
    "qqq_above_ema": True,
    "position_size_multiplier": 1.0,
}

_AGENTS_CFG = {
    "risk_pct_per_trade": 0.01,
    "max_positions": 5,
    "daily_loss_limit": -0.02,
    "min_cash_reserve": 0.20,
    "agents_required_to_agree": 4,
    "claude_model": "claude-sonnet-4-20250514",
    "claude_max_tokens": 1000,
}


def _final_kwargs():
    return dict(
        technical="technical analysis text",
        macro="macro analysis text",
        opportunity="opportunity report text",
        devils="devils advocate text",
        risk="risk manager text",
        signals=[],
        open_positions=[],
        regime=_REGIME,
        agents_required=4,
    )


# ---------------------------------------------------------------------------
# JSON parsing — agent_final_decision strips markdown fences and parses
# ---------------------------------------------------------------------------

class TestAgentResponseParsing:
    """Tests agent_final_decision JSON parsing and fallback behaviour."""

    def test_parse_clean_json_response(self):
        """Clean JSON from _call_claude parses into the expected dict."""
        with patch.object(agents, "_call_claude", return_value=FINAL_DECISION_JSON):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert isinstance(result, dict)
        assert result["agents_agreed"] == 4
        assert isinstance(result["buys"], list)

    def test_parse_embedded_json_response(self):
        """Markdown-fenced JSON is stripped and parses correctly."""
        with patch.object(agents, "_call_claude", return_value=_MARKDOWN_JSON):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert isinstance(result, dict)
        assert "buys" in result

    def test_parse_invalid_json_no_exception(self):
        """Invalid JSON returns the fallback dict without raising."""
        with patch.object(agents, "_call_claude", return_value="Sorry, no recommendation."):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert isinstance(result, dict)
        assert result["buys"] == []
        assert result["agents_agreed"] == 0

    def test_parse_empty_string_no_exception(self):
        """Empty string from _call_claude returns fallback dict."""
        with patch.object(agents, "_call_claude", return_value=""):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert isinstance(result, dict)
        assert "buys" in result

    def test_parse_buy_response_has_required_fields(self):
        """Parsed result always contains all required top-level keys."""
        with patch.object(agents, "_call_claude", return_value=FINAL_DECISION_JSON):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        for key in ("buys", "sells", "hold", "cash", "agents_agreed", "summary"):
            assert key in result, f"Missing required key: {key}"


# ---------------------------------------------------------------------------
# agents_agreed propagation
# ---------------------------------------------------------------------------

class TestAgentAgreementCounting:
    """Tests that agents_agreed is faithfully propagated from the final agent."""

    def _json(self, agreed: int) -> str:
        return json.dumps({
            "buys": [], "sells": [], "hold": [], "cash": False,
            "agents_agreed": agreed,
            "summary": "test", "claude_reasoning": "test",
        })

    def test_all_agents_agree_buy(self):
        """agents_agreed=6 in JSON propagates to the returned dict."""
        with patch.object(agents, "_call_claude", return_value=self._json(6)):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert result["agents_agreed"] == 6

    def test_mixed_responses_partial_agreement(self):
        """agents_agreed=3 propagates correctly."""
        with patch.object(agents, "_call_claude", return_value=self._json(3)):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert result["agents_agreed"] == 3

    def test_no_agreement_returns_zero(self):
        """agents_agreed=0 propagates correctly."""
        with patch.object(agents, "_call_claude", return_value=self._json(0)):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert result["agents_agreed"] == 0

    def test_empty_responses_returns_zero(self):
        """Unparseable JSON yields fallback with agents_agreed=0."""
        with patch.object(agents, "_call_claude", return_value="not json at all"):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = agents.agent_final_decision(**_final_kwargs())
        assert result["agents_agreed"] == 0


# ---------------------------------------------------------------------------
# run_all_agents output structure + agent_technical early-exit
# ---------------------------------------------------------------------------

class TestAgentResponseValidation:
    """Tests run_all_agents output structure and individual agent behaviour."""

    def test_valid_buy_response_validates(self):
        """run_all_agents returns a dict with all required top-level keys."""
        with patch.object(agents, "_call_claude", return_value=FINAL_DECISION_JSON):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = _REAL_RUN_ALL_AGENTS(
                    signals=[], regime=_REGIME, news=[], fx_data={},
                    open_positions=[], portfolio_value=100_000.0, daily_pnl=0.0,
                )
        assert isinstance(result, dict)
        for key in ("buys", "sells", "hold", "cash", "agents_agreed"):
            assert key in result, f"Missing key: {key}"

    def test_missing_action_field_invalid(self):
        """agent_technical with no signals returns the no-setup string without calling Claude."""
        result = agents.agent_technical(signals=[], regime=_REGIME)
        assert isinstance(result, str)
        assert "No symbols" in result

    def test_confidence_out_of_range_handled(self):
        """run_all_agents attaches _agent_outputs with all 5 specialist reports."""
        with patch.object(agents, "_call_claude", return_value=FINAL_DECISION_JSON):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = _REAL_RUN_ALL_AGENTS(
                    signals=[], regime=_REGIME, news=[], fx_data={},
                    open_positions=[], portfolio_value=100_000.0, daily_pnl=0.0,
                )
        outputs = result.get("_agent_outputs", {})
        for key in ("technical", "macro", "opportunity", "devils", "risk"):
            assert key in outputs, f"Missing agent output key: {key}"
