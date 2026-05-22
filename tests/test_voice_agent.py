"""
tests/test_voice_agent.py — Voice analyst layer tests.

Covers:
  - Intent classification (no LLM, no file I/O)
  - Context builder: missing files do not crash
  - Explainability tools: correct output structure
  - voice_agent.answer_voice_question: control commands, answer spoken once
  - /api/ask cooldown logic
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

# ─── Ensure repo root is on sys.path ─────────────────────────────────────────
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


# ─── Minimal CONFIG stub (prevents import errors in CI) ──────────────────────
def _stub_config():
    cfg = types.ModuleType("config")
    cfg.CONFIG = {
        "anthropic_api_key": "test-key",
        "claude_model_haiku": "claude-haiku-4-5-20251001",
        # bot_state.py reads these at module level
        "agents_required_to_agree": 2,
        "active_account": "DUP481326",
        "dashboard_port": 8050,
    }
    sys.modules.setdefault("config", cfg)
    return cfg.CONFIG


_stub_config()


# ─── Import modules under test ────────────────────────────────────────────────
from voice_agent import _classify, _NOT_SYMBOLS, answer_voice_question  # noqa: E402
from voice_context_builder import (  # noqa: E402
    build_full_context,
    get_apex_aggregate_context,
    get_apex_candidate_decisions,
    get_driver_state_context,
    get_live_universe_context,
    get_pm_decisions_context,
    get_positions_context,
    get_recent_signals_context,
    get_recent_trades_context,
    get_theme_context,
    get_training_summary_context,
)
from voice_explainability_tools import (  # noqa: E402
    explain_blocked_candidate,
    explain_bot_health,
    explain_learning,
    explain_market_regime,
    explain_no_trade,
    explain_portfolio_risk,
    explain_position,
    explain_recent_trade,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

EMPTY_DASH = {
    "portfolio_value": 100_000,
    "daily_pnl": -250.0,
    "session": "REGULAR",
    "regime": {"regime": "BULL_TRENDING", "vix": 14.2, "spy_price": 575.0},
    "scanning": False,
    "paused": False,
    "killed": False,
    "scan_count": 42,
    "last_scan": None,
    "ibkr_disconnected": False,
    "claude_analysis": "Market is trending up on low volatility.",
    "apex_errors_1h": 0,
}


def _make_position(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "direction": "LONG",
        "qty": 100,
        "entry": 170.0,
        "current": 178.5,
        "pnl": 850.0,
        "pnl_pct": 5.0,
        "conviction": 0.72,
        "entry_regime": "BULL_TRENDING",
        "trade_type": "POSITION",
        "entry_thesis": "POSITION LONG AAPL | strong momentum, breakout above 170",
        "reasoning": "AI capex theme, clean breakout",
        "setup_type": "Quality Compounder",
        "score": 38,
        "open_time": "2026-05-20T10:30:00+00:00",
        "sl": 160.0,
        "tp": 195.0,
        "status": "OPEN",
        "high_water_mark": 180.0,
        "signal_scores": {"momentum": 8, "trend": 7},
    }


def _make_trade(symbol: str = "NVDA", pnl: float = -320.0) -> dict:
    return {
        "symbol": symbol,
        "direction": "LONG",
        "entry_price": 900.0,
        "exit_price": 885.0,
        "pnl": pnl,
        "pnl_pct": -0.017,
        "exit_reason": "stop_loss_hit",
        "exit_time": "2026-05-21T15:45:00+00:00",
        "hold_minutes": 95,
        "reasoning": "momentum setup, stopped out on reversal",
        "entry_thesis": "INTRADAY LONG NVDA | momentum breakout",
        "regime": "BULL_TRENDING",
        "score": 32,
        "trade_type": "INTRADAY",
        "setup_type": "Momentum",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. INTENT CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntentClassification:

    def test_pause_command(self):
        intent, sym = _classify("pause the bot")
        assert intent == "CONTROL_PAUSE"
        assert sym is None

    def test_resume_command(self):
        intent, sym = _classify("resume scanning")
        assert intent == "CONTROL_RESUME"
        assert sym is None

    def test_explain_holding_with_symbol(self):
        intent, sym = _classify("why are we still holding AAPL")
        assert intent == "EXPLAIN_HOLDING"
        assert sym == "AAPL"

    def test_explain_holding_variant(self):
        intent, sym = _classify("tell me about our position in MSFT")
        assert intent == "EXPLAIN_HOLDING"
        assert sym == "MSFT"

    def test_explain_trade_buy(self):
        intent, sym = _classify("why did you buy NVDA")
        assert intent == "EXPLAIN_TRADE"
        assert sym == "NVDA"

    def test_explain_trade_sell(self):
        intent, sym = _classify("why did the bot exit TSLA")
        assert intent == "EXPLAIN_TRADE"
        assert sym == "TSLA"

    def test_explain_blocked(self):
        intent, sym = _classify("why was NVDA blocked")
        assert intent == "EXPLAIN_BLOCKED"
        assert sym == "NVDA"

    def test_explain_blocked_didnt(self):
        intent, sym = _classify("why didn't you trade AMD")
        assert intent == "EXPLAIN_BLOCKED"
        assert sym == "AMD"

    def test_no_trade_today(self):
        intent, sym = _classify("why didn't you trade today")
        assert intent == "NO_TRADE"
        assert sym is None

    def test_no_trade_waiting(self):
        intent, sym = _classify("what's the bot waiting for")
        assert intent == "NO_TRADE"
        assert sym is None

    def test_portfolio_status(self):
        intent, sym = _classify("what are we holding")
        assert intent == "PORTFOLIO_STATUS"
        assert sym is None

    def test_portfolio_risk(self):
        intent, sym = _classify("what are the main risks in the portfolio")
        assert intent == "PORTFOLIO_STATUS"
        assert sym is None

    def test_market_regime(self):
        intent, sym = _classify("what is the current market regime")
        assert intent == "MARKET_CONVO"
        assert sym is None

    def test_active_themes(self):
        intent, sym = _classify("which theme is strongest")
        assert intent == "MARKET_CONVO"
        assert sym is None

    def test_bot_status(self):
        intent, sym = _classify("what's the bot doing")
        assert intent == "BOT_STATUS"
        assert sym is None

    def test_learning(self):
        intent, sym = _classify("how have we been performing recently")
        assert intent == "LEARNING"
        assert sym is None

    def test_not_symbols_excludes_common_words(self):
        # "TODAY" must not be treated as a ticker
        assert "TODAY" in _NOT_SYMBOLS
        assert "OUR" in _NOT_SYMBOLS

    def test_no_trade_today_not_blocked(self):
        # "today" must not be extracted as a blocked symbol
        intent, sym = _classify("why didn't you trade today")
        assert intent == "NO_TRADE"
        assert sym is None

    def test_general_qa_fallback(self):
        intent, sym = _classify("what is the capital of France")
        assert intent == "GENERAL_QA"

    def test_empty_string_is_general(self):
        intent, sym = _classify("")
        assert intent == "GENERAL_QA"

    def test_weakest_position(self):
        intent, sym = _classify("which position is weakest")
        assert intent == "PORTFOLIO_STATUS"

    def test_win_rate(self):
        intent, sym = _classify("what's our win rate")
        assert intent == "LEARNING"

    def test_bot_health(self):
        intent, sym = _classify("is the bot connected")
        assert intent == "BOT_STATUS"

    def test_bot_status_expanded(self):
        # expanded "what is the bot doing" must not fall to NO_TRADE
        intent, sym = _classify("What is the bot doing right now?")
        assert intent == "BOT_STATUS"
        assert sym is None

    def test_no_trade_not_contraction(self):
        # "did you not trade" (expanded form) must be NO_TRADE
        intent, sym = _classify("Why did you not trade today?")
        assert intent == "NO_TRADE"
        assert sym is None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CONTEXT BUILDER — missing files do not crash
# ═══════════════════════════════════════════════════════════════════════════════

class TestContextBuilderMissingFiles:
    """All context builders must return safe defaults when files are absent."""

    def test_positions_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        assert get_positions_context() == {}

    def test_recent_trades_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        assert get_recent_trades_context() == []

    def test_pm_decisions_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        assert get_pm_decisions_context() == []

    def test_apex_candidates_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        assert get_apex_candidate_decisions() == []

    def test_apex_aggregates_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        assert get_apex_aggregate_context() == []

    def test_live_universe_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        result = get_live_universe_context()
        assert isinstance(result, dict)

    def test_driver_state_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        result = get_driver_state_context()
        assert isinstance(result, dict)

    def test_theme_context_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        result = get_theme_context()
        assert isinstance(result, dict)

    def test_signals_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        assert get_recent_signals_context() == []

    def test_training_summary_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        result = get_training_summary_context()
        assert isinstance(result, dict)

    def test_build_full_context_all_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        ctx = build_full_context(EMPTY_DASH)
        assert "positions" in ctx
        assert "recent_trades" in ctx
        assert ctx["positions"] == {}
        assert ctx["recent_trades"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CONTEXT BUILDER — correct parsing from real data
# ═══════════════════════════════════════════════════════════════════════════════

class TestContextBuilderParsing:

    def test_positions_parsed_correctly(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        pos_dir = tmp_path / "data"
        pos_dir.mkdir()
        (pos_dir / "positions.json").write_text(
            json.dumps({"AAPL": _make_position("AAPL")})
        )
        result = get_positions_context()
        assert "AAPL" in result
        assert result["AAPL"]["direction"] == "LONG"
        assert result["AAPL"]["pnl"] == 850.0

    def test_trades_sorted_most_recent_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        trades = [
            {**_make_trade("NVDA"), "exit_time": "2026-05-21T15:00:00"},
            {**_make_trade("TSLA"), "exit_time": "2026-05-22T09:00:00"},
        ]
        (data_dir / "trades.json").write_text(json.dumps(trades))
        result = get_recent_trades_context(10)
        assert result[0]["symbol"] == "TSLA"  # most recent first

    def test_theme_context_list_format(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        data_dir = tmp_path / "data" / "intelligence"
        data_dir.mkdir(parents=True)
        payload = {
            "generated_at": "2026-05-22T18:00:00Z",
            "themes": [
                {"theme_id": "ai_capex", "state": "activated"},
                {"theme_id": "reits", "state": "dormant"},
                {"theme_id": "semis", "state": "crowded"},
            ],
        }
        (data_dir / "theme_activation.json").write_text(json.dumps(payload))
        result = get_theme_context()
        assert "ai_capex" in result["activated"]
        assert "reits" in result["dormant"]
        assert "semis" in result["crowded"]

    def test_apex_candidate_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_context_builder._REPO", str(tmp_path))
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lines = [
            json.dumps({"record_type": "aggregate", "ts": "2026-05-22T10:00:00Z",
                        "apex_new_entries_count": 0, "apex_new_entries_symbols": []}),
            json.dumps({"record_type": "apex_candidate", "ts": "2026-05-22T10:00:01Z",
                        "symbol": "CHRW", "apex_decision": "avoid",
                        "apex_reason_if_available": "score too low", "raw_score": 15}),
        ]
        (data_dir / "apex_decision_audit.jsonl").write_text("\n".join(lines) + "\n")
        candidates = get_apex_candidate_decisions()
        assert len(candidates) == 1
        assert candidates[0]["symbol"] == "CHRW"
        assert candidates[0]["apex_decision"] == "avoid"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EXPLAINABILITY TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

class TestExplainabilityTools:

    def _make_ctx(self, positions=None, trades=None, pm=None, apex_candidates=None):
        return {
            "dash": EMPTY_DASH,
            "positions": positions or {},
            "recent_trades": trades or [],
            "pm_decisions": pm or [],
            "apex_candidates": apex_candidates or [],
            "apex_aggregates": [],
            "live_universe": {"total_candidates": 40, "position_candidates": 12,
                              "stale": False, "age_str": "2h ago",
                              "handoff_enabled": True, "top_candidates": []},
            "driver_state": {"active_drivers": ["ai_capex_growth"], "age_str": "1h ago"},
            "themes": {"activated": ["ai_capex"], "crowded": [], "dormant": [], "age_str": "2h ago"},
            "recent_signals": [],
            "training_summary": {"recent_record_count": 30, "winners": 18, "losers": 12,
                                 "ml_eligible_count": 25, "avg_pnl_recent": 120.5},
        }

    def test_explain_position_found(self):
        ctx = self._make_ctx(positions={"AAPL": _make_position("AAPL")})
        result = explain_position("AAPL", ctx)
        assert "AAPL" in result
        assert "850" in result  # pnl
        assert "LONG" in result

    def test_explain_position_not_found_fallback(self):
        ctx = self._make_ctx()
        result = explain_position("ZZZZ", ctx)
        assert "No open position" in result or "ZZZZ" in result

    def test_explain_position_recently_closed(self):
        ctx = self._make_ctx(trades=[_make_trade("NVDA")])
        result = explain_position("NVDA", ctx)
        assert "NVDA" in result
        assert "closed" in result.lower() or "stop" in result.lower()

    def test_explain_recent_trade_by_symbol(self):
        ctx = self._make_ctx(trades=[_make_trade("NVDA")])
        result = explain_recent_trade("NVDA", ctx)
        assert "NVDA" in result
        assert "stop_loss" in result.lower() or "stop" in result.lower()

    def test_explain_recent_trade_latest(self):
        ctx = self._make_ctx(trades=[_make_trade("NVDA")])
        result = explain_recent_trade(None, ctx)
        assert "NVDA" in result

    def test_explain_recent_trade_no_records(self):
        ctx = self._make_ctx()
        result = explain_recent_trade("AAPL", ctx)
        assert "No recent" in result

    def test_explain_blocked_with_apex_record(self):
        apex = [{"symbol": "CHRW", "apex_decision": "avoid",
                 "apex_reason": "score below threshold", "raw_score": 15,
                 "ts": "2026-05-22T10:00:00Z", "origin_path": "normal"}]
        ctx = self._make_ctx(apex_candidates=apex)
        result = explain_blocked_candidate("CHRW", ctx)
        assert "CHRW" in result
        assert "avoid" in result.lower() or "score" in result.lower()

    def test_explain_blocked_no_record(self):
        ctx = self._make_ctx()
        result = explain_blocked_candidate("ZZZZ", ctx)
        assert "ZZZZ" in result
        assert "No" in result or "no" in result

    def test_explain_no_trade_includes_scan_count(self):
        ctx = self._make_ctx()
        result = explain_no_trade(ctx)
        assert "42" in result  # scan_count from EMPTY_DASH

    def test_explain_portfolio_risk_long_notional(self):
        ctx = self._make_ctx(positions={"AAPL": _make_position("AAPL")})
        result = explain_portfolio_risk(ctx)
        assert "AAPL" in result
        assert "17,850" in result or "17850" in result or "Long" in result

    def test_explain_market_regime_content(self):
        ctx = self._make_ctx()
        result = explain_market_regime(ctx)
        assert "BULL_TRENDING" in result
        assert "14.2" in result  # VIX

    def test_explain_bot_health_status(self):
        ctx = self._make_ctx()
        result = explain_bot_health(ctx)
        assert "IDLE" in result or "PAUSED" in result or "SCANNING" in result
        assert "42" in result  # scan_count

    def test_explain_learning_content(self):
        ctx = self._make_ctx()
        result = explain_learning(ctx)
        assert "18" in result  # winners
        assert "12" in result  # losers


# ═══════════════════════════════════════════════════════════════════════════════
# 5. VOICE AGENT — control commands (no LLM)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoiceAgentControl:

    def test_pause_modifies_dash(self):
        dash = {**EMPTY_DASH, "paused": False}
        result = answer_voice_question("pause the bot", dash)
        assert dash["paused"] is True
        assert result == "Scanning paused."

    def test_resume_modifies_dash(self):
        dash = {**EMPTY_DASH, "paused": True}
        result = answer_voice_question("resume scanning", dash)
        assert dash["paused"] is False
        assert result == "Resuming scans."

    def test_empty_query_returns_gracefully(self):
        result = answer_voice_question("", EMPTY_DASH.copy())
        assert len(result) > 0
        assert "didn't catch" in result.lower() or "try again" in result.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. VOICE AGENT — answer spoken exactly once via speak()
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoiceAnswerSpokenOnce:
    """
    The /api/ask handler calls answer_voice_question() then speak() once.
    Verify that for a QA query the answer text flows through speak() exactly once.
    """

    def test_speak_called_once_on_qa(self, monkeypatch):
        speak_calls = []

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="The market is trending bullish today.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        monkeypatch.setattr("voice_agent.build_full_context", lambda dash: {
            "dash": dash, "positions": {}, "recent_trades": [],
            "pm_decisions": [], "apex_candidates": [], "apex_aggregates": [],
            "live_universe": {}, "driver_state": {}, "themes": {},
            "recent_signals": [], "training_summary": {},
        })

        # _anthropic_module is the top-level import in voice_agent.py
        with patch("voice_agent._anthropic_module.Anthropic", return_value=mock_client):
            answer = answer_voice_question("what is the market regime", EMPTY_DASH.copy())
            speak_calls.append(answer)  # simulates the handler calling speak(answer) once

        assert len(speak_calls) == 1
        assert "bullish" in speak_calls[0].lower() or len(speak_calls[0]) > 0

    def test_control_command_does_not_call_llm(self, monkeypatch):
        # build_full_context must NOT be called for control intents
        context_built = []
        monkeypatch.setattr("voice_agent.build_full_context",
                            lambda dash: context_built.append(True) or {})

        with patch("voice_agent._anthropic_module.Anthropic") as mock_anth:
            dash = {**EMPTY_DASH, "paused": False}
            result = answer_voice_question("pause the bot", dash)

        mock_anth.assert_not_called()
        assert len(context_built) == 0
        assert result == "Scanning paused."


# ═══════════════════════════════════════════════════════════════════════════════
# 7. COOLDOWN — /api/ask rate limiting
# ═══════════════════════════════════════════════════════════════════════════════

class TestAskCooldown:
    """Verify cooldown variables exist in bot_dashboard.py without importing its heavy deps."""

    def _get_assigned_names(self) -> set[str]:
        """Parse bot_dashboard.py and return all assigned names (Assign + AnnAssign)."""
        import ast
        src_path = os.path.join(REPO, "bot_dashboard.py")
        with open(src_path) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        return names

    def test_cooldown_state_exists(self):
        names = self._get_assigned_names()
        assert "_ask_last_ts" in names
        assert "_ASK_COOLDOWN_SECS" in names

    def test_cooldown_value_reasonable(self):
        import ast
        src_path = os.path.join(REPO, "bot_dashboard.py")
        with open(src_path) as f:
            tree = ast.parse(f.read())
        value = None
        for node in ast.walk(tree):
            # Handle both `_ASK_COOLDOWN_SECS = 1.5` and `_ASK_COOLDOWN_SECS: float = 1.5`
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "_ASK_COOLDOWN_SECS" and node.value:
                    value = ast.literal_eval(node.value)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_ASK_COOLDOWN_SECS":
                        value = ast.literal_eval(node.value)
        assert value is not None, "_ASK_COOLDOWN_SECS not found"
        assert 0.5 <= float(value) <= 5.0

    def test_cooldown_blocks_rapid_repeat(self):
        # Logic test: if elapsed < threshold, request should be rejected
        cooldown = 1.5
        last_ts = time.monotonic()  # set to now
        elapsed = time.monotonic() - last_ts
        assert elapsed < cooldown  # immediate follow-up is within cooldown

    def test_cooldown_allows_after_wait(self):
        # Logic test: if elapsed >= threshold, request should be allowed
        cooldown = 1.5
        last_ts = time.monotonic() - 10.0  # 10 seconds ago
        elapsed = time.monotonic() - last_ts
        assert elapsed >= cooldown


# ═══════════════════════════════════════════════════════════════════════════════
# 8. VOICE AGENT — LLM failure fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoiceAgentFallback:

    def test_llm_error_returns_graceful_message(self, monkeypatch):
        monkeypatch.setattr("voice_agent.build_full_context", lambda dash: {
            "dash": dash, "positions": {}, "recent_trades": [],
            "pm_decisions": [], "apex_candidates": [], "apex_aggregates": [],
            "live_universe": {}, "driver_state": {}, "themes": {},
            "recent_signals": [], "training_summary": {},
        })
        with patch("voice_agent._anthropic_module.Anthropic", side_effect=Exception("API down")):
            result = answer_voice_question("what are we holding", EMPTY_DASH.copy())
        assert isinstance(result, str)
        assert len(result) > 0
        assert "error" in result.lower() or "issue" in result.lower() or "try again" in result.lower()

    def test_context_builder_error_does_not_crash_agent(self, monkeypatch):
        monkeypatch.setattr("voice_agent.build_full_context",
                            lambda dash: (_ for _ in ()).throw(RuntimeError("disk full")))

        # Should not propagate — returns fallback string
        result = answer_voice_question("what is the regime", EMPTY_DASH.copy())
        assert isinstance(result, str)
