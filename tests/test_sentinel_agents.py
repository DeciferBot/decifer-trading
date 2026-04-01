"""Tests for sentinel_agents.py — 3-agent news-triggered trade pipeline."""
import os
import sys
import types
import json
import pytest
from unittest.mock import MagicMock, patch

# ── path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── stub heavy deps BEFORE importing sentinel_agents ────────────────────────
_anthropic_stub = MagicMock()
sys.modules.setdefault("anthropic", _anthropic_stub)
sys.modules.setdefault("ib_async", MagicMock())

# stub config
config_mod = types.ModuleType("config")
config_mod.CONFIG = {
    "anthropic_api_key": "test-key",
    "claude_model": "claude-sonnet-4-6",  # must match real config.py
    "max_positions": 5,
    "risk_pct_per_trade": 0.02,
    "daily_loss_limit": 0.05,
    "min_score": 60,
    "log_file": "/tmp/test_decifer.log",
    "trade_log": "/tmp/test_trades.json",
    "order_log": "/tmp/test_orders.json",
}
sys.modules.setdefault("config", config_mod)

# Ensure we get the real module, not any stub left by test_bot.py
sys.modules.pop("sentinel_agents", None)

# ── now import ───────────────────────────────────────────────────────────────
import sentinel_agents as sa


# ════════════════════════════════════════════════════════════════════════════
# FIXTURES & HELPERS
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def bullish_trigger():
    return {
        "symbol": "NVDA",
        "headlines": ["NVIDIA crushes earnings, raises guidance", "Record data-center revenue"],
        "keyword_score": 5,
        "direction": "BULLISH",
        "urgency": "HIGH",
        "claude_sentiment": "BULLISH",
        "claude_confidence": 8,
        "claude_catalyst": "Blowout earnings beat",
        "sources": ["Yahoo Finance", "FinViz"],
    }

@pytest.fixture
def bearish_trigger():
    return {
        "symbol": "AAPL",
        "headlines": ["Apple cuts iPhone production amid demand slump"],
        "keyword_score": -4,
        "direction": "BEARISH",
        "urgency": "MODERATE",
        "claude_sentiment": "BEARISH",
        "claude_confidence": 6,
        "claude_catalyst": "Demand deterioration",
        "sources": ["Reuters"],
    }

@pytest.fixture
def open_positions():
    return [
        {"symbol": "MSFT", "qty": 10, "entry": 300.0, "current": 310.0, "pnl": 100.0},
        {"symbol": "GOOGL", "qty": 5, "entry": 140.0, "current": 142.0, "pnl": 10.0},
    ]

@pytest.fixture
def regime_trending():
    return {"regime": "TRENDING", "vix": 15.0, "spy_trend": "UP"}

def _mock_claude_response(text):
    """Create a mock Claude API response returning the given text."""
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=text)]
    return mock_resp


# ════════════════════════════════════════════════════════════════════════════
# _call_claude
# ════════════════════════════════════════════════════════════════════════════

class TestCallClaude:

    def test_returns_text_on_success(self):
        """_call_claude should return stripped text from the API response."""
        expected = "BULLISH catalyst confirmed"
        mock_resp = _mock_claude_response(f"  {expected}  ")
        with patch.object(sa.client, "messages") as mock_msgs:
            mock_msgs.create.return_value = mock_resp
            result = sa._call_claude("system", "user msg", max_tokens=200)
        assert result == expected

    def test_returns_empty_string_on_exception(self):
        """_call_claude should return '' and not raise on API error."""
        with patch.object(sa.client, "messages") as mock_msgs:
            mock_msgs.create.side_effect = Exception("API rate limit")
            result = sa._call_claude("system", "user msg")
        assert result == ""

    def test_passes_correct_model_and_tokens(self):
        """_call_claude should pass model from CONFIG and requested max_tokens."""
        mock_resp = _mock_claude_response("ok")
        with patch.object(sa.client, "messages") as mock_msgs:
            mock_msgs.create.return_value = mock_resp
            sa._call_claude("sys", "usr", max_tokens=777)
            call_kwargs = mock_msgs.create.call_args
        assert call_kwargs.kwargs.get("max_tokens") == 777
        assert call_kwargs.kwargs.get("model") == config_mod.CONFIG["claude_model"]


# ════════════════════════════════════════════════════════════════════════════
# agent_catalyst
# ════════════════════════════════════════════════════════════════════════════

class TestAgentCatalyst:

    def test_returns_string(self, bullish_trigger):
        """agent_catalyst should always return a string."""
        with patch.object(sa, "_call_claude", return_value="Material catalyst. BULLISH."):
            result = sa.agent_catalyst(bullish_trigger)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_symbol_in_prompt(self, bullish_trigger):
        """The prompt passed to Claude must mention the symbol."""
        captured = {}
        def fake_call(sys_p, user_p, max_tokens=500):
            captured["user"] = user_p
            return "analysis"
        with patch.object(sa, "_call_claude", side_effect=fake_call):
            sa.agent_catalyst(bullish_trigger)
        assert "NVDA" in captured["user"]

    def test_includes_headlines_in_prompt(self, bullish_trigger):
        """All headlines must appear in the prompt sent to Claude."""
        captured = {}
        def fake_call(sys_p, user_p, max_tokens=500):
            captured["user"] = user_p
            return "ok"
        with patch.object(sa, "_call_claude", side_effect=fake_call):
            sa.agent_catalyst(bullish_trigger)
        for headline in bullish_trigger["headlines"]:
            assert headline in captured["user"]

    def test_with_current_position(self, bullish_trigger):
        """agent_catalyst with an open position should not crash and include position info."""
        pos = {"qty": 10, "entry": 450.0, "current": 470.0, "pnl": 200.0,
               "direction": "LONG", "sl": 440.0, "tp": 500.0}
        captured = {}
        def fake_call(sys_p, user_p, max_tokens=500):
            captured["user"] = user_p
            return "hold position"
        with patch.object(sa, "_call_claude", side_effect=fake_call):
            result = sa.agent_catalyst(bullish_trigger, pos)
        assert "HOLDING" in captured["user"]
        assert result == "hold position"

    def test_no_position_shows_no_current_position(self, bearish_trigger):
        """Without position, prompt should say NO CURRENT POSITION."""
        captured = {}
        def fake_call(sys_p, user_p, max_tokens=500):
            captured["user"] = user_p
            return "bearish"
        with patch.object(sa, "_call_claude", side_effect=fake_call):
            sa.agent_catalyst(bearish_trigger, None)
        assert "NO CURRENT POSITION" in captured["user"]

    def test_empty_headlines_no_crash(self):
        """Trigger with no headlines should not raise."""
        trigger = {"symbol": "TSLA", "headlines": [], "keyword_score": 0,
                   "direction": "NEUTRAL", "urgency": "LOW",
                   "claude_sentiment": "NEUTRAL", "claude_confidence": 0,
                   "claude_catalyst": "", "sources": []}
        with patch.object(sa, "_call_claude", return_value=""):
            result = sa.agent_catalyst(trigger)
        assert isinstance(result, str)


# ════════════════════════════════════════════════════════════════════════════
# agent_risk_gate
# ════════════════════════════════════════════════════════════════════════════

class TestAgentRiskGate:

    def test_returns_string(self, bearish_trigger, open_positions, regime_trending):
        """agent_risk_gate should always return a string."""
        with patch.object(sa, "_call_claude", return_value="DECISION: BLOCK\nREASON: overexposed"):
            result = sa.agent_risk_gate(
                "catalyst analysis", bearish_trigger, open_positions,
                100000.0, -500.0, regime_trending
            )
        assert isinstance(result, str)

    def test_output_includes_portfolio_value(self, bullish_trigger, regime_trending):
        """Portfolio value must appear in the deterministic output."""
        result = sa.agent_risk_gate("report", bullish_trigger, [], 75000.0, 0.0, regime_trending)
        assert "75,000" in result or "75000" in result

    def test_output_shows_position_count(self, bullish_trigger, open_positions, regime_trending):
        """Number of open positions should be reflected in the output."""
        result = sa.agent_risk_gate("cat", bullish_trigger, open_positions,
                                    100000.0, 0.0, regime_trending)
        # max_positions is 5, 2 are open -> 3 remaining
        assert "2" in result or "remaining" in result

    def test_no_positions_no_crash(self, bullish_trigger, regime_trending):
        """Empty positions list should work fine."""
        result = sa.agent_risk_gate("report", bullish_trigger, [], 50000.0, 0.0, regime_trending)
        assert isinstance(result, str)

    def test_daily_pnl_in_output(self, bullish_trigger, regime_trending):
        """Daily P&L should appear in the deterministic risk gate output."""
        result = sa.agent_risk_gate("cat", bullish_trigger, [], 100000.0, -2000.0, regime_trending)
        assert "-2,000" in result or "-2000" in result or "2000" in result


# ════════════════════════════════════════════════════════════════════════════
# agent_instant_decision
# ════════════════════════════════════════════════════════════════════════════

class TestAgentInstantDecision:

    def _make_valid_json(self, symbol="NVDA", action="BUY", confidence=8):
        return json.dumps({
            "action": action,
            "symbol": symbol,
            "qty": 10,
            "sl": 440.0,
            "tp": 510.0,
            "instrument": "stock",
            "inverse_symbol": None,
            "urgency": "HIGH",
            "confidence": confidence,
            "reasoning": "Strong earnings beat catalyst.",
            "catalyst": "Blowout earnings",
            "trigger_type": "news_sentinel",
        })

    def test_returns_dict_on_valid_json(self, bullish_trigger):
        """Valid JSON from Claude should be parsed into a dict."""
        raw = self._make_valid_json(symbol="NVDA", action="BUY")
        with patch.object(sa, "_call_claude", return_value=raw):
            result = sa.agent_instant_decision("catalyst", "risk", bullish_trigger)
        assert isinstance(result, dict)
        assert result["action"] == "BUY"
        assert result["symbol"] == "NVDA"

    def test_returns_skip_on_json_parse_error(self, bullish_trigger):
        """Unparseable Claude response should return a safe SKIP decision."""
        with patch.object(sa, "_call_claude", return_value="not valid json at all"):
            result = sa.agent_instant_decision("catalyst", "risk", bullish_trigger)
        assert result["action"] == "SKIP"
        assert result["symbol"] == "NVDA"
        assert result["qty"] == 0

    def test_skip_decision_has_all_required_fields(self, bullish_trigger):
        """SKIP fallback must include all required fields for downstream safety."""
        with patch.object(sa, "_call_claude", return_value="{invalid"):
            result = sa.agent_instant_decision("cat", "risk", bullish_trigger)
        for field in ["action", "symbol", "qty", "sl", "tp", "instrument",
                      "confidence", "reasoning", "trigger_type"]:
            assert field in result, f"Missing field: {field}"

    def test_handles_markdown_wrapped_json(self, bullish_trigger):
        """Claude sometimes wraps JSON in ```json ... ``` — should still parse."""
        raw = "```json\n" + self._make_valid_json("NVDA", "BUY", 7) + "\n```"
        with patch.object(sa, "_call_claude", return_value=raw):
            result = sa.agent_instant_decision("cat", "risk", bullish_trigger)
        assert result["action"] == "BUY"
        assert result["confidence"] == 7

    def test_sets_default_fields_if_missing(self, bullish_trigger):
        """Partial JSON (missing some fields) should have defaults injected."""
        partial = json.dumps({"action": "HOLD", "symbol": "NVDA"})
        with patch.object(sa, "_call_claude", return_value=partial):
            result = sa.agent_instant_decision("cat", "risk", bullish_trigger)
        assert result.get("qty") == 0
        assert result.get("instrument") == "stock"
        assert result.get("trigger_type") == "news_sentinel"

    def test_trigger_type_always_news_sentinel(self, bearish_trigger):
        """trigger_type must always be 'news_sentinel'."""
        raw = self._make_valid_json("AAPL", "SELL", 6)
        with patch.object(sa, "_call_claude", return_value=raw):
            result = sa.agent_instant_decision("cat", "risk", bearish_trigger)
        assert result["trigger_type"] == "news_sentinel"

    @pytest.mark.parametrize("action", ["BUY", "SELL", "HOLD", "SKIP"])
    def test_all_valid_actions_accepted(self, bullish_trigger, action):
        """All four valid action types should parse correctly."""
        raw = self._make_valid_json("NVDA", action, 5)
        with patch.object(sa, "_call_claude", return_value=raw):
            result = sa.agent_instant_decision("cat", "risk", bullish_trigger)
        assert result["action"] == action


# ════════════════════════════════════════════════════════════════════════════
# run_sentinel_pipeline
# ════════════════════════════════════════════════════════════════════════════

class TestRunSentinelPipeline:

    def _make_decision(self, symbol, action="BUY"):
        return {
            "action": action, "symbol": symbol, "qty": 10,
            "sl": 440.0, "tp": 510.0, "instrument": "stock",
            "confidence": 8, "reasoning": "Test", "trigger_type": "news_sentinel",
        }

    def test_pipeline_returns_decision_dict(self, bullish_trigger, open_positions, regime_trending):
        """Pipeline should return a complete decision dict."""
        decision = self._make_decision("NVDA")
        with patch.object(sa, "agent_catalyst", return_value="catalyst report"), \
             patch.object(sa, "agent_risk_gate", return_value="risk report"), \
             patch.object(sa, "agent_instant_decision", return_value=decision):
            result = sa.run_sentinel_pipeline(
                bullish_trigger, open_positions, 100000.0, -500.0, regime_trending
            )
        assert result["action"] == "BUY"
        assert result["symbol"] == "NVDA"

    def test_pipeline_attaches_sentinel_outputs(self, bullish_trigger, open_positions, regime_trending):
        """Result must include _sentinel_outputs with catalyst and risk_gate text."""
        decision = self._make_decision("NVDA")
        with patch.object(sa, "agent_catalyst", return_value="cat_text"), \
             patch.object(sa, "agent_risk_gate", return_value="risk_text"), \
             patch.object(sa, "agent_instant_decision", return_value=decision):
            result = sa.run_sentinel_pipeline(
                bullish_trigger, open_positions, 100000.0, 0.0, regime_trending
            )
        assert "_sentinel_outputs" in result
        assert result["_sentinel_outputs"]["catalyst"] == "cat_text"
        assert result["_sentinel_outputs"]["risk_gate"] == "risk_text"

    def test_pipeline_attaches_trigger(self, bullish_trigger, open_positions, regime_trending):
        """Result must include the original _trigger for logging."""
        decision = self._make_decision("NVDA")
        with patch.object(sa, "agent_catalyst", return_value="c"), \
             patch.object(sa, "agent_risk_gate", return_value="r"), \
             patch.object(sa, "agent_instant_decision", return_value=decision):
            result = sa.run_sentinel_pipeline(
                bullish_trigger, open_positions, 100000.0, 0.0, regime_trending
            )
        assert "_trigger" in result
        assert result["_trigger"]["symbol"] == "NVDA"

    def test_pipeline_finds_current_position(self, bullish_trigger, regime_trending):
        """Pipeline must pass the matching open position to agent_catalyst."""
        positions = [
            {"symbol": "NVDA", "qty": 5, "entry": 450.0, "current": 470.0, "pnl": 100.0},
            {"symbol": "MSFT", "qty": 10, "entry": 300.0, "current": 305.0, "pnl": 50.0},
        ]
        decision = self._make_decision("NVDA")
        captured = {}
        def fake_catalyst(trigger, current_pos=None):
            captured["pos"] = current_pos
            return "cat"
        with patch.object(sa, "agent_catalyst", side_effect=fake_catalyst), \
             patch.object(sa, "agent_risk_gate", return_value="risk"), \
             patch.object(sa, "agent_instant_decision", return_value=decision):
            sa.run_sentinel_pipeline(
                bullish_trigger, positions, 100000.0, 0.0, regime_trending
            )
        assert captured["pos"] is not None
        assert captured["pos"]["symbol"] == "NVDA"

    def test_pipeline_passes_none_position_when_not_holding(self, bearish_trigger, regime_trending):
        """When we don't hold the symbol, current_pos should be None."""
        positions = [{"symbol": "MSFT", "qty": 10, "entry": 300.0}]
        decision = self._make_decision("AAPL", "SKIP")
        captured = {}
        def fake_catalyst(trigger, current_pos=None):
            captured["pos"] = current_pos
            return "cat"
        with patch.object(sa, "agent_catalyst", side_effect=fake_catalyst), \
             patch.object(sa, "agent_risk_gate", return_value="r"), \
             patch.object(sa, "agent_instant_decision", return_value=decision):
            sa.run_sentinel_pipeline(
                bearish_trigger, positions, 100000.0, 0.0, regime_trending
            )
        assert captured["pos"] is None

    def test_pipeline_calls_all_three_agents(self, bullish_trigger, open_positions, regime_trending):
        """All three agents must be called exactly once."""
        decision = self._make_decision("NVDA")
        with patch.object(sa, "agent_catalyst", return_value="c") as m1, \
             patch.object(sa, "agent_risk_gate", return_value="r") as m2, \
             patch.object(sa, "agent_instant_decision", return_value=decision) as m3:
            sa.run_sentinel_pipeline(
                bullish_trigger, open_positions, 100000.0, 0.0, regime_trending
            )
        m1.assert_called_once()
        m2.assert_called_once()
        m3.assert_called_once()

    def test_pipeline_empty_positions(self, bullish_trigger, regime_trending):
        """Pipeline with no open positions should not crash."""
        decision = self._make_decision("NVDA")
        with patch.object(sa, "agent_catalyst", return_value="c"), \
             patch.object(sa, "agent_risk_gate", return_value="r"), \
             patch.object(sa, "agent_instant_decision", return_value=decision):
            result = sa.run_sentinel_pipeline(
                bullish_trigger, [], 50000.0, 0.0, regime_trending
            )
        assert result is not None
        assert result["action"] == "BUY"
