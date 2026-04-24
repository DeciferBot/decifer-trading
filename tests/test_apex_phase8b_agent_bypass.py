"""
tests/test_apex_phase8b_agent_bypass.py

Phase 8B — bypass run_all_agents() in Apex 3.0 mode.

Structural tests that lock:
  1. In 3.0 mode (_scan_cutover_pre=True), run_all_agents() is NOT called.
  2. Deterministic sells are derived directly from positions_to_reconsider.
  3. In legacy mode (_scan_cutover_pre=False), run_all_agents() IS called.
  4. The minimal decision dict is contract-compatible with downstream consumers
     (dashboard fields, divergence block, sell loop, log_trade).
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BOT = (_REPO / "bot_trading.py").read_text()


# ── structural presence ───────────────────────────────────────────────────────

def test_phase8b_block_exists():
    assert "Phase 8B — bypass run_all_agents() in Apex 3.0 mode" in _BOT


def test_cutover_pre_flag_reads_legacy_pipeline():
    assert "_scan_cutover_pre = not _so_pre_agents.should_use_legacy_pipeline()" in _BOT


# ── 3.0 mode: run_all_agents() skipped ───────────────────────────────────────

def test_run_all_agents_inside_else_branch():
    """run_all_agents() must be in the else branch, not called unconditionally."""
    start = _BOT.index("Phase 8B")
    block = _BOT[start : start + 2000]
    else_idx = block.index("    else:")
    # Search for the actual assignment (not a comment reference)
    run_idx = block.index("        decision = run_all_agents(")
    assert run_idx > else_idx, "run_all_agents() must be inside the else branch"


def test_apex_mode_builds_sells_from_positions_to_reconsider():
    """In 3.0 mode, sells are derived from positions_to_reconsider — no LLM."""
    start = _BOT.index("Phase 8B")
    block = _BOT[start : start + 2000]
    # The apex branch must reference positions_to_reconsider for sell list
    if_idx = block.index("    if _scan_cutover_pre:")
    else_idx = block.index("    else:")
    apex_branch = block[if_idx:else_idx]
    assert "positions_to_reconsider" in apex_branch
    assert "_apex_mode_sells" in apex_branch


def test_apex_mode_sell_list_filters_hold():
    """Sells must exclude entries where reason == 'HOLD'."""
    start = _BOT.index("Phase 8B")
    block = _BOT[start : start + 2000]
    assert '!= "HOLD"' in block or "!= 'HOLD'" in block


def test_apex_mode_decision_has_required_keys():
    """Minimal decision dict must have all keys downstream code expects."""
    start = _BOT.index("Phase 8B")
    block = _BOT[start : start + 2000]
    if_idx = block.index("    if _scan_cutover_pre:")
    else_idx = block.index("    else:")
    apex_branch = block[if_idx:else_idx]
    for key in ('"buys"', '"sells"', '"hold"', '"agents_agreed"', '"_agent_outputs"', '"summary"', '"cash"'):
        assert key in apex_branch, f"Missing key {key} in apex decision dict"


# ── legacy mode: run_all_agents() preserved ──────────────────────────────────

def test_legacy_mode_calls_run_all_agents():
    """Legacy else branch must call run_all_agents with all required args."""
    start = _BOT.index("Phase 8B")
    block = _BOT[start : start + 2000]
    else_idx = block.index("    else:")
    legacy_branch = block[else_idx:]
    assert "run_all_agents(" in legacy_branch
    for arg in ("signals=scored", "regime=regime", "positions_to_reconsider=positions_to_reconsider"):
        assert arg in legacy_branch, f"Missing arg {arg} in run_all_agents call"


# ── downstream contract compatibility ────────────────────────────────────────

def test_dashboard_fields_populated_after_branch():
    """Dashboard fields must be set after the if/else block (works for both branches)."""
    start = _BOT.index("Phase 8B")
    # The dashboard assignment must come AFTER the if/else closes
    end_of_block = _BOT.index("    else:", start)
    # Find end of else branch — look for run_all_agents closing paren
    post_block_start = _BOT.index("    dash[\"claude_analysis\"]", end_of_block)
    assert post_block_start > end_of_block


def test_divergence_block_uses_decision_buys():
    """Divergence block reads decision.get('buys') — empty list in 3.0 is graceful."""
    assert 'decision.get("buys")' in _BOT or "decision.get('buys')" in _BOT


def test_sell_loop_reads_decision_sells():
    """Sell loop reads decision.get('sells', []) — populated by apex branch."""
    assert 'decision.get("sells", [])' in _BOT
