"""
tests/test_apex_phase8a_pm_trackb_execute.py

Phase 8A.3 — PM Track B execute wiring lock-in.

The PM Track B cutover branch in bot_trading.py has been structurally
present since Phase 6D. This test locks in the contract that its execute
flag depends on USE_LEGACY_PIPELINE — i.e., PM live dispatch only fires
when both PM_LEGACY_OPUS_REVIEW_ENABLED is False AND
USE_LEGACY_PIPELINE is False.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BOT = (_REPO / "bot_trading.py").read_text()


def test_pm_cutover_branch_exists():
    assert "PM legacy Opus review disabled — invoking Apex Track B cutover branch" in _BOT


def test_pm_cutover_execute_gated_on_legacy_pipeline_flag():
    # _pm_cutover_execute = (not _so_cut.should_use_legacy_pipeline())
    assert "_pm_cutover_execute = (not _so_cut.should_use_legacy_pipeline())" in _BOT


def test_pm_cutover_dispatch_uses_execute_flag():
    # dispatch() must be called with execute=_pm_cutover_execute
    assert "execute=_pm_cutover_execute" in _BOT


def test_pm_cutover_fail_safe_is_execute_false():
    # On exception reading the flag, _pm_cutover_execute = False → no live orders
    idx = _BOT.index("_pm_cutover_execute = (not _so_cut.should_use_legacy_pipeline())")
    region = _BOT[idx : idx + 400]
    assert "_pm_cutover_execute = False" in region


def test_pm_legacy_flag_default_still_true():
    from safety_overlay import pm_legacy_opus_review_enabled
    assert pm_legacy_opus_review_enabled() is True
