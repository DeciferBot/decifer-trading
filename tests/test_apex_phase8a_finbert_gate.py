"""
tests/test_apex_phase8a_finbert_gate.py

Phase 8A.5 — FinBERT materiality gate wiring lock-in.

When FINBERT_MATERIALITY_GATE_ENABLED is True, news_sentinel.py must read
finbert_confidence instead of claude_confidence for the ≥4 materiality gate.
Default (False) preserves the legacy Claude-confidence gate.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_NS = (_REPO / "news_sentinel.py").read_text()


def test_materiality_gate_reads_safety_overlay_flag():
    assert "from safety_overlay import finbert_materiality_gate_enabled" in _NS
    assert "_use_finbert = finbert_materiality_gate_enabled()" in _NS


def test_materiality_gate_key_chosen_by_flag():
    assert (
        '_conf_key = "finbert_confidence" if _use_finbert else "claude_confidence"'
        in _NS
    )


def test_materiality_gate_threshold_unchanged():
    # The threshold stays at <4 with CRITICAL override, regardless of which
    # confidence field is read.
    assert '_conf_val < 4 and trigger["urgency"] != "CRITICAL"' in _NS


def test_finbert_gate_flag_default_false():
    from safety_overlay import finbert_materiality_gate_enabled
    assert finbert_materiality_gate_enabled() is False
