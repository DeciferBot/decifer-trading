"""
tests/test_apex_phase8a_scan_cycle_cutover.py

Phase 8A.2 — scan-cycle Track A cutover branch in bot_trading.py.

Structural tests only. The full run_scan() path is too integration-heavy
to exercise live in a unit test; these tests lock the contract that the
cutover branch exists, is gated on should_use_legacy_pipeline(), and
routes to _run_apex_pipeline(execute=True).
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BOT = (_REPO / "bot_trading.py").read_text()


def test_cutover_branch_exists_in_bot_trading():
    assert "Phase 8A.2 — Scan-cycle Track A cutover branch" in _BOT


def test_cutover_branch_gated_on_legacy_flag_off():
    assert "_scan_cutover = not _so_track_a_cut.should_use_legacy_pipeline()" in _BOT


def test_cutover_branch_calls_run_apex_pipeline_execute_true():
    # The branch must call _run_apex_pipeline with execute=True
    assert "_aorch_track_a._run_apex_pipeline(" in _BOT
    # Grab the cutover block region and assert execute=True + forced_exits wired
    start = _BOT.index("Phase 8A.2")
    end = _BOT.index("_all_buys = decision.get(\"buys\", [])", start)
    block = _BOT[start:end]
    assert "execute=True" in block
    assert "forced_exits=_cut_forced" in block
    assert "active_trades=_cut_active" in block
    assert "ib=ib" in block
    assert "portfolio_value=pv" in block


def test_cutover_branch_returns_before_legacy_loop():
    # Ensure the cutover branch returns early so legacy loop does not execute
    start = _BOT.index("Phase 8A.2")
    end = _BOT.index("_all_buys = decision.get(\"buys\", [])", start)
    block = _BOT[start:end]
    # There must be a `return` inside the if _scan_cutover: block, before
    # _all_buys is reached.
    assert "return" in block
    assert "Scan #{bot_state.scan_count} complete (apex cutover)" in block


def test_cutover_branch_uses_filter_candidates_and_screen_open_positions():
    start = _BOT.index("Phase 8A.2")
    end = _BOT.index("_all_buys = decision.get(\"buys\", [])", start)
    block = _BOT[start:end]
    assert "filter_candidates as _fc_track_a" in block
    assert "screen_open_positions as _screen_track_a" in block
    assert "flag_positions_for_review as _flag_track_a" in block


def test_legacy_pipeline_default_still_true():
    """Safety net: this test should start failing the moment someone flips
    USE_LEGACY_PIPELINE's default to False. That is the real cutover trigger
    and must be a deliberate, reviewed change."""
    from safety_overlay import should_use_legacy_pipeline
    assert should_use_legacy_pipeline() is True
