"""
tests/test_apex_migration_guards.py

Regression guards that the Decifer 3.0 (Apex) migration is complete.
Consolidated from four micro-files (2026-05-04):
  - test_apex_pm_trackb.py     (PM Track B — legacy run_portfolio_review gone)
  - test_apex_sentinel_ni.py   (Sentinel NI — legacy run_sentinel_pipeline gone)
  - test_finbert_gate.py       (FinBERT materiality gate wiring)
  - test_apex_scan_cycle.py    (Track A scan cycle — legacy buy loop gone)

These tests read source files as text — no imports of production code needed.
They fail immediately if someone accidentally resurrects a deleted code path.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BOT = (_REPO / "bot_trading.py").read_text()
_SENT = (_REPO / "bot_sentinel.py").read_text()
_NS = (_REPO / "news_sentinel.py").read_text()


# ── PM Track B: legacy run_portfolio_review() must be gone ───────────────────

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


# ── Sentinel NI: legacy run_sentinel_pipeline() must be gone ─────────────────

def test_sentinel_always_uses_apex():
    assert "apex_orchestrator as _aorch_s" in _SENT


def test_sentinel_apex_dispatch_execute_true():
    assert "_apex_dispatch(" in _SENT
    assert "execute=True" in _SENT


def test_sentinel_no_legacy_pipeline_check():
    assert "run_sentinel_pipeline" not in _SENT
    assert "sentinel_legacy_pipeline_enabled" not in _SENT
    assert "should_use_legacy_pipeline" not in _SENT


def test_build_news_trigger_payload_preserved():
    """The Apex payload builder must remain in sentinel_agents."""
    sentinel_text = (_REPO / "sentinel_agents.py").read_text()
    assert "def build_news_trigger_payload(" in sentinel_text
    assert "NEWS_INTERRUPT" in sentinel_text


# ── FinBERT materiality gate wiring ──────────────────────────────────────────

def test_materiality_gate_reads_safety_overlay_flag():
    assert "from safety_overlay import finbert_materiality_gate_enabled" in _NS
    assert "_use_finbert = finbert_materiality_gate_enabled()" in _NS


def test_materiality_gate_key_chosen_by_flag():
    assert (
        '_conf_key = "finbert_confidence" if _use_finbert else "claude_confidence"'
        in _NS
    )


def test_materiality_gate_threshold_unchanged():
    assert '_conf_val < 4 and trigger["urgency"] != "CRITICAL"' in _NS


def test_finbert_gate_flag_post_cutover_default_true():
    """Phase 8 cutover complete: FinBERT materiality gate is the authoritative source."""
    from safety_overlay import finbert_materiality_gate_enabled
    assert finbert_materiality_gate_enabled() is True


# ── Track A scan cycle: legacy buy loop must be gone ─────────────────────────

def test_track_a_calls_apex_orchestrator():
    assert "apex_orchestrator as _aorch_track_a" in _BOT


def test_track_a_run_apex_pipeline_execute_true():
    assert "_aorch_track_a._run_apex_pipeline(" in _BOT
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
