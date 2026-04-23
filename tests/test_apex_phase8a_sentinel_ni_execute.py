"""
tests/test_apex_phase8a_sentinel_ni_execute.py

Phase 8A.4 — Sentinel NEWS_INTERRUPT execute wiring lock-in.

The Sentinel cutover branch in bot_sentinel.py has been structurally
present since Phase 6D. This test locks in the contract that the NI
live dispatch only fires when both SENTINEL_LEGACY_PIPELINE_ENABLED
is False AND USE_LEGACY_PIPELINE is False.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SENT = (_REPO / "bot_sentinel.py").read_text()


def test_sentinel_cutover_branch_exists():
    assert "legacy disabled — invoking Apex NEWS_INTERRUPT cutover branch" in _SENT


def test_sentinel_cutover_execute_gated_on_legacy_pipeline_flag():
    assert "_s_execute = not _so_s.should_use_legacy_pipeline()" in _SENT


def test_sentinel_cutover_dispatch_uses_execute_flag():
    assert "execute=_s_execute" in _SENT


def test_sentinel_legacy_flag_default_still_true():
    from safety_overlay import sentinel_legacy_pipeline_enabled
    assert sentinel_legacy_pipeline_enabled() is True
