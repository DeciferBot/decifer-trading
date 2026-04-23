"""
tests/test_apex_phase7_7c9_deletion_readiness.py

Phase 7C.9 — deletion-readiness guards.

A pre-cutover canary. Enumerates every legacy file, function, and flag-gated
call site that Phase 7 will eventually retire. Each test asserts the artifact
is STILL PRESENT today.

Canonical deletion targets (as documented in the master plan + CLAUDE.md):
  - trade_advisor.py            — entire file deleted at Phase 7
  - portfolio_manager.run_portfolio_review() — Opus call, deleted at Phase 7
  - portfolio_manager._parse_actions()       — regex parser, deleted at Phase 7
  - sentinel_agents.run_sentinel_pipeline()  — 3-agent orchestrator, deleted
  - sentinel_agents.agent_catalyst()         — deleted
  - sentinel_agents.agent_instant_decision() — deleted
  - news.claude_sentiment()                  — deleted (FinBERT-only)
  - agents.agent_technical / agent_trading_analyst /
    agent_risk_manager / agent_final_decision — deleted
  - bot_trading.py _synthesize_trade_card()  — deleted
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("rel_path", [
    "trade_advisor.py",
    "portfolio_manager.py",
    "sentinel_agents.py",
    "news.py",
    "agents.py",
    "bot_trading.py",
    "bot_sentinel.py",
    "market_intelligence.py",
    "signal_dispatcher.py",
    "apex_orchestrator.py",
    "apex_divergence.py",
    "safety_overlay.py",
    "llm_client.py",
])
def test_legacy_file_still_present(rel_path: str):
    p = _REPO / rel_path
    assert p.exists(), f"{rel_path} must still exist before Phase 7 cutover"
    assert p.stat().st_size > 0


@pytest.mark.parametrize("rel_path, needle", [
    ("trade_advisor.py", "def advise_trade"),
    ("trade_advisor.py", "def _formula_advice"),
    ("portfolio_manager.py", "def run_portfolio_review"),
    ("portfolio_manager.py", "def _parse_actions"),
    ("sentinel_agents.py", "def run_sentinel_pipeline"),
    ("sentinel_agents.py", "def agent_catalyst"),
    ("sentinel_agents.py", "def agent_instant_decision"),
    ("sentinel_agents.py", "def build_news_trigger_payload"),
    ("agents.py", "def agent_technical"),
    ("agents.py", "def agent_trading_analyst"),
    ("agents.py", "def agent_risk_manager"),
    ("agents.py", "def agent_final_decision"),
    ("bot_trading.py", "def _synthesize_trade_card"),
])
def test_legacy_symbol_still_defined(rel_path: str, needle: str):
    text = (_REPO / rel_path).read_text()
    assert needle in text, (
        f"{needle!r} no longer present in {rel_path} — "
        "did someone delete Phase 7 cutover scaffolding early?"
    )


def test_bot_trading_still_calls_run_portfolio_review_under_legacy_flag():
    text = (_REPO / "bot_trading.py").read_text()
    assert "pm_legacy_opus_review_enabled" in text
    assert "run_portfolio_review" in text


def test_bot_sentinel_still_calls_run_sentinel_pipeline_under_legacy_flag():
    text = (_REPO / "bot_sentinel.py").read_text()
    assert "sentinel_legacy_pipeline_enabled" in text
    assert "run_sentinel_pipeline" in text


def test_signal_dispatcher_still_routes_advise_trade_under_gate():
    text = (_REPO / "signal_dispatcher.py").read_text()
    assert "trade_advisor_enabled" in text
    assert "advise_trade" in text
    assert "_formula_advice" in text


def test_all_phase8_cutover_flag_defaults():
    """Phase 8 cutover complete — all six flags at their post-cutover values.
    Legacy pipeline off, Apex owns all execute paths."""
    from safety_overlay import (
        finbert_materiality_gate_enabled,
        pm_legacy_opus_review_enabled,
        sentinel_legacy_pipeline_enabled,
        should_run_apex_shadow,
        should_use_legacy_pipeline,
        trade_advisor_enabled,
    )
    assert should_use_legacy_pipeline() is False
    assert should_run_apex_shadow() is True
    assert pm_legacy_opus_review_enabled() is False
    assert sentinel_legacy_pipeline_enabled() is False
    assert trade_advisor_enabled() is False
    assert finbert_materiality_gate_enabled() is True


def test_apex_divergence_still_forbids_order_layer_imports():
    import ast
    src = (_REPO / "apex_divergence.py").read_text()
    tree = ast.parse(src)
    banned = {"orders_core", "orders_state", "bot_ibkr", "orders_portfolio"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, (
                    f"apex_divergence.py imports banned runtime module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            assert mod not in banned, (
                f"apex_divergence.py imports banned runtime module {node.module}"
            )


def test_cutover_execute_guard_in_bot_trading_still_depends_on_legacy_flag():
    text = (_REPO / "bot_trading.py").read_text()
    assert "not _so_cut.should_use_legacy_pipeline()" in text


def test_cutover_execute_guard_in_bot_sentinel_still_depends_on_legacy_flag():
    text = (_REPO / "bot_sentinel.py").read_text()
    assert "not _so_s.should_use_legacy_pipeline()" in text


def test_deletion_manifest_docstring_present():
    this_file = Path(__file__).read_text()
    for key in (
        "trade_advisor.py",
        "run_portfolio_review",
        "run_sentinel_pipeline",
        "claude_sentiment",
        "agent_technical",
        "_synthesize_trade_card",
    ):
        assert key in this_file, f"deletion manifest missing {key}"
