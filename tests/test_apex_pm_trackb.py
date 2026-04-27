"""
tests/test_apex_pm_trackb.py

Regression tests for Apex PM Track B (TRIM/EXIT/HOLD review).

Post-Decifer-3.0: legacy run_portfolio_review() is gone. The PM review path
in bot_trading.py always calls apex_orchestrator._run_apex_pipeline via
signal_dispatcher.dispatch with execute=True.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BOT = (_REPO / "bot_trading.py").read_text()


def test_pm_block_always_executes():
    """PM Track B dispatches unconditionally — no legacy flag check."""
    assert "_pm_cutover_execute = True" in _BOT


def test_pm_block_calls_apex_orchestrator():
    assert "apex_orchestrator as _aorch_pm" in _BOT


def test_pm_block_uses_execute_true():
    assert "execute=_pm_cutover_execute" in _BOT


def test_pm_block_no_legacy_flag_check():
    """Legacy flag accessors must not appear in the PM block."""
    assert "pm_legacy_opus_review_enabled" not in _BOT
    assert "should_use_legacy_pipeline" not in _BOT


def test_run_portfolio_review_deleted():
    """run_portfolio_review was removed — must not exist in portfolio_manager."""
    pm_text = (_REPO / "portfolio_manager.py").read_text()
    assert "def run_portfolio_review" not in pm_text
