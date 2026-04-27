"""
tests/test_apex_scan_cycle.py

Regression tests for Apex scan-cycle Track A (live execute).

Post-Decifer-3.0: the legacy buy loop is deleted. bot_trading.run_scan()
always routes Track A through apex_orchestrator._run_apex_pipeline(execute=True).
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BOT = (_REPO / "bot_trading.py").read_text()


def test_track_a_calls_apex_orchestrator():
    assert "apex_orchestrator as _aorch_track_a" in _BOT


def test_track_a_run_apex_pipeline_execute_true():
    assert "_aorch_track_a._run_apex_pipeline(" in _BOT
    # After cleanup the call is unconditional and uses execute=True
    idx = _BOT.index("_aorch_track_a._run_apex_pipeline(")
    region = _BOT[idx: idx + 300]
    assert "execute=True" in region


def test_track_a_wires_forced_exits_and_active_trades():
    idx = _BOT.index("_aorch_track_a._run_apex_pipeline(")
    region = _BOT[idx: idx + 300]
    assert "forced_exits=_cut_forced" in region
    assert "active_trades=_cut_active" in region


def test_track_a_uses_guardrails():
    assert "filter_candidates as _fc_track_a" in _BOT
    assert "screen_open_positions as _screen_track_a" in _BOT
    assert "flag_positions_for_review as _flag_track_a" in _BOT


def test_legacy_buy_loop_absent():
    """Legacy buy loop was deleted — sentinel string must not exist."""
    assert "_all_buys = decision.get(\"buys\", [])" not in _BOT
    assert "should_use_legacy_pipeline" not in _BOT
