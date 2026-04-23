"""
tests/test_apex_phase7_7c8_scan_cycle_cutover_prep.py

Phase 7C.8 — scan-cycle full-cutover prep coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import apex_orchestrator
from apex_orchestrator import build_scan_cycle_apex_input
from signal_dispatcher import dispatch, dispatch_forced_exit

_REPO = Path(__file__).resolve().parent.parent


def test_build_scan_cycle_apex_input_carries_all_sections():
    candidates = [{"symbol": "AAPL", "score": 40, "price": 150.0, "atr_5m": 1.5}]
    review = [{"symbol": "MSFT", "trade_type": "INTRADAY", "direction": "LONG"}]
    portfolio = {"portfolio_value": 100_000, "daily_pnl": 0, "position_count": 1}
    regime = {"regime": "TRENDING_UP"}

    apex_input = build_scan_cycle_apex_input(
        candidates=candidates,
        review_positions=review,
        portfolio_state=portfolio,
        regime=regime,
        overnight_research="market quiet overnight",
    )
    assert apex_input["trigger_type"] == "SCAN_CYCLE"
    assert apex_input["trigger_context"] is None
    assert apex_input["track_a"]["candidates"] == candidates
    assert apex_input["track_b"] == review
    assert apex_input["market_context"]["regime"] == regime
    assert apex_input["market_context"]["overnight_research"] == "market quiet overnight"
    assert apex_input["portfolio_state"] == portfolio
    assert "scan_ts" in apex_input


def test_build_scan_cycle_apex_input_handles_empty_inputs():
    apex_input = build_scan_cycle_apex_input(candidates=[])
    assert apex_input["track_a"]["candidates"] == []
    assert apex_input["track_b"] == []
    assert apex_input["portfolio_state"] == {}


def test_run_apex_pipeline_execute_true_implemented_and_dispatches(monkeypatch):
    """Phase 8A.1 — execute=True now implemented. Replaces the Phase 6
    NotImplementedError safety rail. With an empty decision, no execute_*
    call fires, and the returned note flips to 'executed'."""
    import signal_dispatcher

    calls = {"dispatch": 0, "forced": 0}

    def _stub_dispatch(decision, candidates_by_symbol, active_trades, **kw):
        calls["dispatch"] += 1
        assert kw.get("execute") is True
        return {"new_entries": [], "portfolio_actions": [],
                "forced_exits": [], "errors": []}

    def _stub_forced(symbol, reason, **kw):
        calls["forced"] += 1
        return {"symbol": symbol, "reason": reason, "executed": True}

    monkeypatch.setattr(signal_dispatcher, "dispatch", _stub_dispatch)
    monkeypatch.setattr(signal_dispatcher, "dispatch_forced_exit", _stub_forced)

    # apex_call returns empty decision via _fallback_decision on bad input —
    # this test does not need a real LLM response.
    import market_intelligence
    monkeypatch.setattr(
        market_intelligence, "apex_call",
        lambda *_a, **_kw: {"new_entries": [], "portfolio_actions": []},
    )

    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
        active_trades={}, ib=None, portfolio_value=100_000.0,
        regime={"regime": "TRENDING_UP"}, forced_exits=[("AAPL", "eod_flat")],
    )
    assert result["note"] == "executed"
    assert "dispatch_report" in result
    assert calls["dispatch"] == 1
    assert calls["forced"] == 1


def test_dispatch_mixed_decision_dry_run_shape_and_no_orders(monkeypatch):
    import orders_core
    for name in ("execute_buy", "execute_short", "execute_sell"):
        def _boom(*_a, _name=name, **_kw):
            raise AssertionError(f"{_name} must not be called with execute=False")
        if hasattr(orders_core, name):
            monkeypatch.setattr(orders_core, name, _boom)

    decision = {
        "new_entries": [
            {"symbol": "AAPL", "direction": "LONG", "trade_type": "INTRADAY",
             "instrument": "stock", "conviction": "HIGH", "rationale": "r"},
            {"symbol": "SPXS", "direction": "LONG", "trade_type": "INTRADAY",
             "instrument": "stock", "conviction": "MEDIUM", "rationale": "r"},
            {"symbol": "ZZZ", "trade_type": "AVOID", "direction": None,
             "instrument": None, "conviction": None, "rationale": "passed"},
        ],
        "portfolio_actions": [
            {"symbol": "NVDA", "action": "TRIM", "trim_pct": 25, "reasoning_tag": "soft"},
            {"symbol": "META", "action": "EXIT", "reasoning_tag": "thesis_broken"},
            {"symbol": "TSLA", "action": "HOLD", "reasoning_tag": "intact"},
        ],
    }
    payloads = {
        "AAPL": {"price": 150.0, "score": 40, "atr_5m": 1.5, "atr_daily": 3.0,
                 "score_breakdown": {"trend": 8}},
        "SPXS": {"price": 25.0, "score": 35, "atr_5m": 0.6, "atr_daily": 1.2,
                 "score_breakdown": {}},
    }
    active = {
        "NVDA": {"symbol": "NVDA", "qty": 100, "entry": 500, "current": 510},
        "META": {"symbol": "META", "qty": 50, "entry": 400, "current": 395},
        "TSLA": {"symbol": "TSLA", "qty": 10, "entry": 250, "current": 255},
    }

    report = dispatch(
        decision, payloads, active,
        ib=None, portfolio_value=100_000.0, regime={"regime": "TRENDING_UP"},
        execute=False,
    )
    assert len(report["new_entries"]) == 2
    assert len(report["portfolio_actions"]) == 3
    assert all(r["executed"] is False for r in report["new_entries"])
    assert all(r["executed"] is False for r in report["portfolio_actions"])
    for r in report["new_entries"]:
        for k in ("symbol", "direction", "trade_type", "conviction", "instrument",
                  "qty", "sl", "tp", "executed"):
            assert k in r
    for r in report["portfolio_actions"]:
        for k in ("symbol", "action", "trim_pct", "reasoning_tag", "executed"):
            assert k in r


def test_dispatch_forced_exit_dry_run_is_safe(monkeypatch):
    import orders_core
    if hasattr(orders_core, "execute_sell"):
        monkeypatch.setattr(
            orders_core, "execute_sell",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not run dry")),
        )
    action = dispatch_forced_exit("AAPL", "eod_flat", ib=None, execute=False)
    assert action["action"] == "FORCED_EXIT"
    assert action["executed"] is False
    assert action["reason"] == "eod_flat"


def test_pm_cutover_execute_guard_depends_on_legacy_flag_off():
    text = (_REPO / "bot_trading.py").read_text()
    assert "not _so_cut.should_use_legacy_pipeline()" in text


def test_scan_cycle_shadow_and_divergence_wiring_intact():
    text = (_REPO / "bot_trading.py").read_text()
    assert "should_run_apex_shadow" in text
    assert "build_scan_cycle_apex_input" in text
    assert "_aorch._run_apex_pipeline" in text
    assert "mirror_legacy_decision" in text
    assert "mirror_apex_decision" in text
    assert "write_divergence_record" in text


def test_legacy_pipeline_flag_default_is_true():
    from safety_overlay import should_use_legacy_pipeline
    assert should_use_legacy_pipeline() is True
