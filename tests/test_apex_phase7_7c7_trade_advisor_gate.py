"""
tests/test_apex_phase7_7c7_trade_advisor_gate.py

Phase 7C.7 — trade_advisor cutover verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import signal_dispatcher
import trade_advisor

_REPO = Path(__file__).resolve().parent.parent


def test_trade_advisor_flag_post_cutover_default_is_false():
    """Phase 8 cutover complete: legacy trade_advisor disabled; deterministic sizing only."""
    from safety_overlay import trade_advisor_enabled
    assert trade_advisor_enabled() is False


def test_gated_advisor_calls_advise_trade_when_enabled(monkeypatch):
    called = {"n": 0}

    def _stub_advise(**kwargs):
        called["n"] += 1
        return trade_advisor.TradeAdvice(
            advice_id="x", instrument="COMMON", size_multiplier=1.0,
            profit_target=110.0, stop_loss=95.0, reasoning="stub", source="llm",
        )

    import safety_overlay
    monkeypatch.setattr(safety_overlay, "trade_advisor_enabled", lambda: True)
    monkeypatch.setattr(signal_dispatcher, "advise_trade", _stub_advise)

    advice = signal_dispatcher._advise_trade_gated(
        symbol="AAPL", direction="LONG", entry_price=100.0, atr_5m=1.5,
        atr_daily=3.0, conviction_score=0.8, dimension_scores={},
        rationale="test", regime_context="TRENDING_UP", trade_type="INTRADAY",
    )
    assert called["n"] == 1
    assert advice.source == "llm"


def test_gated_advisor_uses_formula_when_disabled(monkeypatch):
    called = {"n": 0}

    def _boom_advise(**_kw):
        called["n"] += 1
        raise AssertionError("advise_trade must not be called when gate is OFF")

    import safety_overlay
    monkeypatch.setattr(safety_overlay, "trade_advisor_enabled", lambda: False)
    monkeypatch.setattr(signal_dispatcher, "advise_trade", _boom_advise)

    advice = signal_dispatcher._advise_trade_gated(
        symbol="AAPL", direction="LONG", entry_price=100.0, atr_5m=1.5,
    )
    assert called["n"] == 0
    assert advice.source == "formula"
    assert advice.instrument == "COMMON"
    assert advice.stop_loss > 0
    assert advice.profit_target > 0


def test_gated_advisor_defaults_to_enabled_when_flag_read_fails(monkeypatch):
    called = {"n": 0}

    def _stub_advise(**_kw):
        called["n"] += 1
        return trade_advisor.TradeAdvice(
            advice_id="x", instrument="COMMON", size_multiplier=1.0,
            profit_target=110.0, stop_loss=95.0, reasoning="fail-safe path", source="llm",
        )

    import safety_overlay

    def _raise():
        raise RuntimeError("simulated flag-read failure")

    monkeypatch.setattr(safety_overlay, "trade_advisor_enabled", _raise)
    monkeypatch.setattr(signal_dispatcher, "advise_trade", _stub_advise)

    advice = signal_dispatcher._advise_trade_gated(
        symbol="AAPL", direction="LONG", entry_price=100.0, atr_5m=1.5,
    )
    assert called["n"] == 1
    assert advice.source == "llm"


def test_formula_advice_matches_calculate_stops_contract():
    from position_sizing import calculate_stops
    advice = trade_advisor._formula_advice(
        symbol="AAPL", direction="LONG", entry_price=100.0, atr_5m=2.0,
    )
    expected_sl, expected_tp = calculate_stops(100.0, 2.0, "LONG")
    assert advice.stop_loss == expected_sl
    assert advice.profit_target == expected_tp
    assert advice.size_multiplier == 1.0


def test_signal_dispatcher_still_routes_through_gate():
    text = (_REPO / "signal_dispatcher.py").read_text()
    assert "_advise_trade_gated" in text
    assert "trade_advisor_enabled" in text
    assert "advise_trade" in text
    assert "_formula_advice" in text
