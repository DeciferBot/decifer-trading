"""
tests/test_apex_phase8a_execute_path.py

Phase 8A.1 — Apex execute path implementation tests.

Exercise the new execute=True code path added to
apex_orchestrator._run_apex_pipeline. Every test stubs signal_dispatcher
and market_intelligence.apex_call — no real LLM call, no real order call.
"""

from __future__ import annotations

import pytest

import apex_orchestrator
import market_intelligence
import signal_dispatcher


@pytest.fixture
def stub_apex_call(monkeypatch):
    """Factory: set the Apex decision apex_call will return."""
    def _set(decision):
        monkeypatch.setattr(
            market_intelligence, "apex_call",
            lambda *_a, **_kw: decision,
        )
    return _set


@pytest.fixture
def capture_dispatch(monkeypatch):
    captured = {"dispatch": [], "forced": []}

    def _dispatch(decision, candidates_by_symbol, active_trades, **kw):
        captured["dispatch"].append({
            "decision": decision,
            "candidates": candidates_by_symbol,
            "active": active_trades,
            "kw": kw,
        })
        return {
            "new_entries": [
                {"symbol": e.get("symbol"), "executed": bool(kw.get("execute"))}
                for e in (decision.get("new_entries") or [])
            ],
            "portfolio_actions": [
                {"symbol": a.get("symbol"), "action": a.get("action"),
                 "executed": bool(kw.get("execute"))}
                for a in (decision.get("portfolio_actions") or [])
            ],
            "forced_exits": [],
            "errors": [],
        }

    def _forced(symbol, reason, **kw):
        captured["forced"].append({"symbol": symbol, "reason": reason, "kw": kw})
        return {"symbol": symbol, "reason": reason, "executed": True}

    monkeypatch.setattr(signal_dispatcher, "dispatch", _dispatch)
    monkeypatch.setattr(signal_dispatcher, "dispatch_forced_exit", _forced)
    return captured


def test_execute_false_remains_shadow_shape(stub_apex_call, capture_dispatch):
    stub_apex_call({"new_entries": [], "portfolio_actions": []})
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=False,
    )
    assert result["note"] == "shadow"
    assert "dispatch_report" not in result
    assert capture_dispatch["dispatch"] == []
    assert capture_dispatch["forced"] == []


def test_execute_true_empty_decision_dispatches_nothing(stub_apex_call, capture_dispatch):
    stub_apex_call({"new_entries": [], "portfolio_actions": []})
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
        active_trades={}, ib=None, portfolio_value=100_000.0,
        regime={"regime": "TRENDING_UP"},
    )
    assert result["note"] == "executed"
    assert result["dispatch_report"]["new_entries"] == []
    assert len(capture_dispatch["dispatch"]) == 1
    assert capture_dispatch["dispatch"][0]["kw"]["execute"] is True


def test_execute_true_long_entry_forwarded_to_dispatch(stub_apex_call, capture_dispatch):
    stub_apex_call({
        "new_entries": [{"symbol": "AAPL", "direction": "LONG",
                         "trade_type": "INTRADAY", "conviction": "HIGH",
                         "instrument": "stock", "rationale": "r"}],
        "portfolio_actions": [],
    })
    candidates = {"AAPL": {
        "price": 150.0, "score": 40, "atr_5m": 1.5, "atr_daily": 3.0,
        "allowed_trade_types": ["INTRADAY", "SWING"], "options_eligible": True,
    }}
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, candidates, execute=True,
        active_trades={}, ib=object(), portfolio_value=100_000.0,
        regime={"regime": "TRENDING_UP"},
    )
    assert result["note"] == "executed"
    disp = capture_dispatch["dispatch"][0]
    assert disp["decision"]["new_entries"][0]["symbol"] == "AAPL"
    assert disp["candidates"] == candidates
    assert result["dispatch_report"]["new_entries"][0]["executed"] is True


def test_execute_true_trim_and_exit_forwarded(stub_apex_call, capture_dispatch):
    stub_apex_call({
        "new_entries": [],
        "portfolio_actions": [
            {"symbol": "NVDA", "action": "TRIM", "trim_pct": 25,
             "reasoning_tag": "soft"},
            {"symbol": "META", "action": "EXIT",
             "reasoning_tag": "thesis_broken"},
        ],
    })
    active = {
        "NVDA": {"symbol": "NVDA", "qty": 100},
        "META": {"symbol": "META", "qty": 50},
    }
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
        active_trades=active, ib=object(), portfolio_value=100_000.0,
        regime={},
    )
    actions = result["dispatch_report"]["portfolio_actions"]
    syms = {a["symbol"] for a in actions}
    assert {"NVDA", "META"} <= syms


def test_execute_true_forced_exits_dispatched(stub_apex_call, capture_dispatch):
    stub_apex_call({"new_entries": [], "portfolio_actions": []})
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
        active_trades={}, ib=object(), portfolio_value=100_000.0,
        regime={}, forced_exits=[("AAPL", "eod_flat"), ("TSLA", "scalp_timeout")],
    )
    assert len(capture_dispatch["forced"]) == 2
    fsyms = [f["symbol"] for f in capture_dispatch["forced"]]
    assert fsyms == ["AAPL", "TSLA"]
    assert all(f["kw"].get("execute") is True for f in capture_dispatch["forced"])
    # forced_exits surfaced in dispatch_report
    assert len(result["dispatch_report"]["forced_exits"]) == 2


def test_execute_true_forced_exits_accept_dict_form(stub_apex_call, capture_dispatch):
    stub_apex_call({"new_entries": [], "portfolio_actions": []})
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
        active_trades={}, ib=object(), portfolio_value=0.0, regime={},
        forced_exits=[{"symbol": "SPXS", "reason": "architecture_violation"}],
    )
    assert capture_dispatch["forced"][0]["symbol"] == "SPXS"
    assert capture_dispatch["forced"][0]["reason"] == "architecture_violation"


def test_execute_true_dispatch_error_is_swallowed(stub_apex_call, monkeypatch):
    stub_apex_call({"new_entries": [], "portfolio_actions": []})

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated dispatch failure")

    monkeypatch.setattr(signal_dispatcher, "dispatch", _boom)
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
        active_trades={}, ib=None, portfolio_value=0.0, regime={},
    )
    # Must not raise; error recorded in report.
    assert result["note"] == "executed"
    errs = result["dispatch_report"]["errors"]
    assert any("dispatch_error" in e for e in errs)


def test_execute_true_forced_exit_error_is_swallowed(stub_apex_call, monkeypatch):
    stub_apex_call({"new_entries": [], "portfolio_actions": []})
    monkeypatch.setattr(
        signal_dispatcher, "dispatch",
        lambda *a, **kw: {"new_entries": [], "portfolio_actions": [],
                          "forced_exits": [], "errors": []},
    )

    def _boom(symbol, reason, **kw):
        raise RuntimeError("simulated forced exit failure")

    monkeypatch.setattr(signal_dispatcher, "dispatch_forced_exit", _boom)
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
        active_trades={}, ib=None, portfolio_value=0.0, regime={},
        forced_exits=[("AAPL", "eod_flat")],
    )
    assert result["note"] == "executed"
    assert any(
        "forced_exit_error" in e
        for e in result["dispatch_report"]["errors"]
    )


def test_execute_true_backward_compatible_signature(stub_apex_call, capture_dispatch):
    """Calling with only (input, candidates, execute=True) — no ib/pv/regime —
    still works; dispatch is called with safe defaults."""
    stub_apex_call({"new_entries": [], "portfolio_actions": []})
    result = apex_orchestrator._run_apex_pipeline(
        {"trigger_type": "SCAN_CYCLE"}, {}, execute=True,
    )
    assert result["note"] == "executed"
    call = capture_dispatch["dispatch"][0]
    assert call["kw"]["ib"] is None
    assert call["kw"]["portfolio_value"] == 0.0
