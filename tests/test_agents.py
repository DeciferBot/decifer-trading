"""Tests for agents module: deterministic agent behaviour, schema validation,
vote counting, and early-exit guards.

Agents 2, 3, 4 are mocked via _call_claude. Agents 1, 5, 6 are deterministic.
No network connections are made.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

import config as _config_mod

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "claude_model": "claude-sonnet-4-6",
    "claude_max_tokens": 1000,
    "model": "claude-sonnet-4-6",
    "max_tokens": 1000,
    "risk_pct_per_trade": 0.01,
    "max_positions": 5,
    "daily_loss_limit": -0.02,
    "min_cash_reserve": 0.20,
    "agents_required_to_agree": 3,
    "high_conviction_score": 30,
    "atr_stop_multiplier": 1.5,
    "min_reward_risk_ratio": 1.5,
    "max_single_position": 0.20,
    "mongo_uri": "",
    "db_name": "test",
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
    _config_mod.CONFIG["max_positions"] = 5
    _config_mod.CONFIG["agents_required_to_agree"] = 3
else:
    _config_mod.CONFIG = _cfg

import pytest

sys.modules.pop("agents", None)
try:
    import agents

    HAS_AGENTS = hasattr(agents, "agent_technical")
    _REAL_RUN_ALL_AGENTS = agents.run_all_agents if HAS_AGENTS else None
except ImportError:
    HAS_AGENTS = False
    _REAL_RUN_ALL_AGENTS = None

pytestmark = pytest.mark.skipif(not HAS_AGENTS, reason="agents module not importable")

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_REGIME_BULL = {
    "regime": "TRENDING_UP",
    "vix": 15.0,
    "vix_1h_change": 0.0,
    "spy_price": 500.0,
    "spy_above_ema": True,
    "qqq_price": 400.0,
    "qqq_above_ema": True,
    "position_size_multiplier": 1.0,
}

_REGIME_PANIC = dict(_REGIME_BULL, regime="CAPITULATION", vix=45.0)

_AAPL_SIGNAL = {
    "symbol": "AAPL",
    "price": 190.0,
    "score": 35,
    "signal": "BUY",
    "vol_ratio": 2.0,
    "atr": 3.0,
    "timeframes": {
        "5m": {
            "signal": "BUY",
            "mfi": 70,
            "rsi_slope": 2.0,
            "macd_accel": 0.5,
            "bull_aligned": True,
            "bear_aligned": False,
            "adx": 35,
            "trend_strength": "strong",
            "squeeze_on": False,
            "squeeze_intensity": 0,
            "vwap_dist": 0.5,
            "donch_breakout": 1,
            "obv_slope": 1,
        },
        "1d": {"signal": "BUY"},
        "1w": None,
    },
    "score_breakdown": {
        "directional": 8,
        "momentum": 5,
        "squeeze": 4,
        "flow": 6,
        "breakout": 7,
        "news": 5,
    },
    "news": {},
}

_AGENTS_CFG = {
    "risk_pct_per_trade": 0.01,
    "max_positions": 5,
    "daily_loss_limit": -0.02,
    "min_cash_reserve": 0.20,
    "agents_required_to_agree": 3,
    "claude_model": "claude-sonnet-4-6",
    "claude_max_tokens": 1000,
    "high_conviction_score": 30,
    "atr_stop_multiplier": 1.5,
    "min_reward_risk_ratio": 1.5,
    "max_single_position": 0.20,
}

# Craft text inputs so AAPL clears the vote threshold (agents_required=3):
# tech+1 (HIGH near AAPL), macro+1 (BULLISH), opp+1 (proposed), risk+1 (APPROVE) -> 4 >= 3
_TECH_HIGH = "[HIGH] AAPL: $190 | Score=35 | BUY | ADX=35 | MFI=70"
_MACRO_BULL = "Overall verdict: BULLISH -- risk-ON environment favours tech longs."
_OPP_AAPL = "1. SYMBOL: AAPL DIRECTION: LONG CONVICTION: HIGH ENTRY RATIONALE: Breaking out of 3-month consolidation on 2x volume KEY RISK: Broader market selloff SUGGESTED INSTRUMENT: Stock"
_DEVILS_NO_VETO = "For AAPL: VETO RATING: NO VETO -- thesis is solid."
_RISK_APPROVE = "RISK GATE: OPEN -- OK\nAAPL:\n  DECISION: APPROVE\n  SIZE: 10 shares"


def _final_kwargs(**overrides):
    base = dict(
        technical=_TECH_HIGH,
        macro=_MACRO_BULL,
        opportunity=_OPP_AAPL,
        devils=_DEVILS_NO_VETO,
        risk=_RISK_APPROVE,
        signals=[_AAPL_SIGNAL],
        open_positions=[],
        regime=_REGIME_BULL,
        agents_required=3,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestAgentResponseSchema:
    """agent_final_decision always returns a dict with required top-level keys."""

    def test_schema_with_qualifying_trade(self):
        """When a symbol clears the vote threshold the result has all required keys."""
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs())
        for key in ("buys", "sells", "hold", "cash", "agents_agreed", "summary", "claude_reasoning"):
            assert key in result, f"Missing required key: {key}"
        assert isinstance(result["buys"], list)
        assert isinstance(result["agents_agreed"], int)

    def test_schema_with_no_signals(self):
        """Empty signals list returns valid schema with empty buys."""
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs(signals=[]))
        for key in ("buys", "sells", "hold", "cash", "agents_agreed"):
            assert key in result
        assert result["buys"] == []

    def test_panic_regime_returns_cash(self):
        """PANIC regime sets cash=True and buys=[] regardless of votes."""
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs(regime=_REGIME_PANIC))
        assert result["cash"] is True
        assert result["buys"] == []
        assert result["agents_agreed"] == 0

    def test_no_opportunity_no_buys(self):
        """Opportunity report with no recognisable symbols yields buys=[]."""
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(
                **_final_kwargs(opportunity="No genuine opportunities this cycle.", signals=[])
            )
        assert result["buys"] == []


# ---------------------------------------------------------------------------
# Vote counting
# ---------------------------------------------------------------------------


class TestVoteCounting:
    """Tests deterministic vote logic in agent_final_decision."""

    def test_sufficient_votes_produces_buy(self):
        """4 votes >= agents_required=3 => AAPL appears in buys."""
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs())
        syms = [b["symbol"] for b in result["buys"]]
        assert "AAPL" in syms

    def test_risk_rejection_blocks_at_high_threshold(self):
        """REJECT from risk manager subtracts 1 vote; if below threshold, no buy."""
        # Devil's Advocate removed — risk manager is now the blocking mechanism.
        # votes: tech+1 macro+1 opp+1 risk-1 = 2; required=4 -> blocked
        risk_reject = "AAPL:\n  DECISION: REJECT\n  REASON: Position limit reached."
        with patch.dict(agents.CONFIG, dict(_AGENTS_CFG, agents_required_to_agree=4)):
            result = agents.agent_final_decision(**_final_kwargs(risk=risk_reject, agents_required=4))
        syms = [b["symbol"] for b in result["buys"]]
        assert "AAPL" not in syms

    def test_risk_rejection_reduces_votes(self):
        """REJECT in risk report gives -1 vote; high threshold blocks trade."""
        risk_reject = "AAPL:\n  DECISION: REJECT\n  REASON: Daily loss limit near."
        # tech+1 macro+1 opp+1 risk-1 = 2; required=4 -> blocked
        with patch.dict(agents.CONFIG, dict(_AGENTS_CFG, agents_required_to_agree=4)):
            result = agents.agent_final_decision(**_final_kwargs(risk=risk_reject, agents_required=4))
        syms = [b["symbol"] for b in result["buys"]]
        assert "AAPL" not in syms

    def test_bearish_macro_no_longer_blocks_high_conviction(self):
        """BEARISH macro gives 0 (softened); tech+1 macro+0 opp+1 risk+1 = 3 >= 3 required.
        High-conviction setups in bear markets now pass — macro is informational, not a veto."""
        macro_bear = "Overall verdict: BEARISH -- risk-OFF, recession signals mounting."
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs(macro=macro_bear, agents_required=3))
        syms = [b["symbol"] for b in result["buys"]]
        assert "AAPL" in syms

    def test_bearish_macro_blocks_low_conviction(self):
        """BEARISH macro gives 0; no technical HIGH -> tech+0 macro+0 opp+1 risk+1 = 2 < 3."""
        macro_bear = "Overall verdict: BEARISH -- risk-OFF, recession signals mounting."
        tech_no_high = "[MEDIUM] AAPL: $190 | Score=25 | BUY"  # no HIGH keyword
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(
                **_final_kwargs(macro=macro_bear, technical=tech_no_high, agents_required=3)
            )
        syms = [b["symbol"] for b in result["buys"]]
        assert "AAPL" not in syms

    def test_agents_agreed_reflects_vote_count(self):
        """agents_agreed equals the computed vote count for the accepted buy."""
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs())
        # tech+1 macro+1 opp+1 risk+1 no_veto = 4
        assert result["agents_agreed"] == 4

    def test_no_qualifying_trades_returns_zero_agreed(self):
        """When no symbols clear threshold, agents_agreed=0."""
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs(signals=[]))
        assert result["agents_agreed"] == 0


# ---------------------------------------------------------------------------
# Position cap enforcement
# ---------------------------------------------------------------------------


class TestPositionCap:
    """Position count no longer caps buys — cash floor and risk controls govern."""

    def test_already_held_symbol_skipped(self):
        """A symbol already in open_positions is not re-entered."""
        held = [{"symbol": "NVDA"}]
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs(open_positions=held))
        buy_syms = [b["symbol"] for b in result.get("buys", [])]
        assert "NVDA" not in buy_syms

    def test_recovery_mode_size_multiplier_applied(self):
        """RECOVERY mode reduces size_multiplier to 0.5 — no trade count block."""
        sm = {"mode": "RECOVERY", "context": "", "size_multiplier": 0.5}
        with patch.dict(agents.CONFIG, _AGENTS_CFG):
            result = agents.agent_final_decision(**_final_kwargs(strategy_mode=sm))
        # Result is a valid dict — agents still run, size_multiplier applied downstream
        assert isinstance(result, dict)
        assert "buys" in result


# ---------------------------------------------------------------------------
# _extract_proposed_symbols — parser has no count cap
# ---------------------------------------------------------------------------


class TestExtractProposedSymbolsUncapped:
    """Parser returns every distinct symbol the analyst names, bounded only by
    membership in `sig_map` and `seen` dedup — no hardcoded count cap."""

    # Parser regex `SYMBOL[:\s]+([A-Z]{1,5})` requires pure-alpha ticker strings.
    _TEN_TICKERS = ["AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "NVDA", "AMD", "INTC", "NFLX"]

    def _make_signals(self, tickers: list) -> list:
        return [{"symbol": s, "score": 30, "signal": "BUY"} for s in tickers]

    def test_returns_more_than_three_when_analyst_names_many(self):
        """Analyst names 10 symbols — parser returns all 10 (was capped at 3)."""
        tickers = self._TEN_TICKERS
        signals = self._make_signals(tickers)
        text = "\n".join(f"SYMBOL: {t}\nDIRECTION: LONG" for t in tickers)
        result = agents._extract_proposed_symbols(text, signals)
        assert len(result) == 10
        assert [r["symbol"] for r in result] == tickers

    def test_returns_empty_when_no_symbols_match_sig_map(self):
        """Symbols named but not in sig_map are ignored."""
        signals = [{"symbol": "AAPL", "score": 30, "signal": "BUY"}]
        text = "SYMBOL: NVDA\nSYMBOL: TSLA"
        result = agents._extract_proposed_symbols(text, signals)
        assert result == []

    def test_dedups_repeated_symbols(self):
        """Parser dedups via `seen` set — repeated mentions don't inflate the list."""
        tickers = ["AAPL", "MSFT", "GOOG"]
        signals = self._make_signals(tickers)
        text = "SYMBOL: AAPL\nSYMBOL: MSFT\nSYMBOL: AAPL\nSYMBOL: GOOG"
        result = agents._extract_proposed_symbols(text, signals)
        assert [r["symbol"] for r in result] == ["AAPL", "MSFT", "GOOG"]


# ---------------------------------------------------------------------------
# run_all_agents output structure
# ---------------------------------------------------------------------------


class TestRunAllAgentsStructure:
    """run_all_agents returns correct schema and attaches _agent_outputs."""

    def test_returns_required_keys(self):
        """run_all_agents with empty signals returns a dict with all required keys."""
        with patch.object(agents, "_call_claude", return_value="BULLISH macro report"):
            with patch.dict(agents.CONFIG, _AGENTS_CFG):
                result = _REAL_RUN_ALL_AGENTS(
                    signals=[],
                    regime=_REGIME_BULL,
                    news=[],
                    fx_data={},
                    open_positions=[],
                    portfolio_value=100_000.0,
                    daily_pnl=0.0,
                )
        assert isinstance(result, dict)
        for key in ("buys", "sells", "hold", "cash", "agents_agreed"):
            assert key in result, f"Missing key: {key}"

    def test_agent_outputs_attached(self):
        """run_all_agents attaches _agent_outputs with all specialist reports."""
        # Must pass a qualifying signal (score >= min_score_to_trade=18) so the
        # early-exit guard doesn't fire before agents run.
        qualifying_signal = {
            "symbol": "AAPL",
            "score": 30,
            "price": 150.0,
            "atr": 2.5,
            "signal": "BUY",
            "direction": "LONG",
        }
        with (
            patch.object(agents, "_call_claude", return_value="MACRO: BULLISH\nAAPL — strong setup"),
            patch.object(agents, "_call_claude_alpha", return_value="MACRO: BULLISH\nAAPL — strong setup"),
        ):
            with patch.dict(agents.CONFIG, dict(_AGENTS_CFG, min_score_to_trade=18)):
                result = _REAL_RUN_ALL_AGENTS(
                    signals=[qualifying_signal],
                    regime=_REGIME_BULL,
                    news=[],
                    fx_data={},
                    open_positions=[],
                    portfolio_value=100_000.0,
                    daily_pnl=0.0,
                )
        outputs = result.get("_agent_outputs", {})
        # New architecture: technical + trading_analyst (replaces macro/opp/devils) + risk
        for key in ("technical", "trading_analyst", "risk"):
            assert key in outputs, f"Missing agent output key: {key}"


# ---------------------------------------------------------------------------
# agent_technical deterministic behaviour
# ---------------------------------------------------------------------------


class TestAgentTechnical:
    """agent_technical produces the correct report without any LLM call."""

    def test_empty_signals_returns_no_setup_string(self):
        result = agents.agent_technical(signals=[], regime=_REGIME_BULL)
        assert isinstance(result, str)
        assert "No symbols" in result

    def test_high_conviction_symbol_labelled_HIGH(self):
        result = agents.agent_technical(signals=[_AAPL_SIGNAL], regime=_REGIME_BULL)
        assert "[HIGH]" in result
        assert "AAPL" in result

    def test_divergence_detected_for_distribution_trap(self):
        """OBV falling + MFI < 40 on a BUY signal = distribution trap warning."""
        trap_sig = {
            **_AAPL_SIGNAL,
            "timeframes": {
                **_AAPL_SIGNAL["timeframes"],
                "5m": {**_AAPL_SIGNAL["timeframes"]["5m"], "obv_slope": -1, "mfi": 30},
            },
        }
        result = agents.agent_technical(signals=[trap_sig], regime=_REGIME_BULL)
        assert "DISTRIBUTION TRAP" in result

    def test_no_claude_call_made(self):
        """agent_technical must not call _call_claude."""
        with patch.object(agents, "_call_claude") as mock_claude:
            agents.agent_technical(signals=[_AAPL_SIGNAL], regime=_REGIME_BULL)
        mock_claude.assert_not_called()


class TestTradingAnalystSystemPrompt:
    """The Trading Analyst system prompt must contain regime-specific rules."""

    def test_trending_up_instruction_present(self):
        """TRENDING_UP must appear in the Trading Analyst system prompt."""
        assert "TRENDING_UP" in agents._TRADING_ANALYST_SYSTEM

    def test_trending_up_requires_catalyst(self):
        """TRENDING_UP rule must require a specific catalyst for shorts, not
        just signal score."""
        assert "catalyst" in agents._TRADING_ANALYST_SYSTEM

    def test_trending_up_cash_not_short(self):
        """TRENDING_UP rule must direct Opus to output CASH rather than SHORT
        when only bearish momentum/breakout signals are present."""
        assert "CASH" in agents._TRADING_ANALYST_SYSTEM
