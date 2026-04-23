"""
tests/test_apex_phase7_7c5_pm_trackb_shadow.py

Phase 7C.5 — PM Track B shadow dispatch verification.

Verifies that the Apex Track B (portfolio_actions) path produces a correct
dry-run dispatch report when called with execute=False, matching the shape
and semantics the PM cutover branch in bot_trading.py relies on. No orders
are submitted. No flags are flipped. No live behavior is changed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from signal_dispatcher import dispatch

_REPO = Path(__file__).resolve().parent.parent


def _decision(portfolio_actions=None, new_entries=None):
    return {
        "new_entries": new_entries or [],
        "portfolio_actions": portfolio_actions or [],
    }


def _active(sym: str, qty: int = 100) -> dict:
    return {sym: {"symbol": sym, "qty": qty, "entry": 100.0, "current": 105.0}}


def test_dispatch_trackb_hold_is_report_only():
    decision = _decision(portfolio_actions=[
        {"symbol": "AAPL", "action": "HOLD", "reasoning_tag": "thesis_intact"},
    ])
    report = dispatch(decision, {}, _active("AAPL"), execute=False)
    rows = report["portfolio_actions"]
    assert len(rows) == 1
    assert rows[0]["action"] == "HOLD"
    assert rows[0]["executed"] is False
    assert report["errors"] == []


def test_dispatch_trackb_trim_preserves_trim_pct_dry_run():
    decision = _decision(portfolio_actions=[
        {"symbol": "AAPL", "action": "TRIM", "trim_pct": 50,
         "reasoning_tag": "score_weakening"},
    ])
    report = dispatch(decision, {}, _active("AAPL"), execute=False)
    row = report["portfolio_actions"][0]
    assert row["action"] == "TRIM"
    assert row["trim_pct"] == 50
    assert row["executed"] is False
    assert row["reasoning_tag"] == "score_weakening"


def test_dispatch_trackb_exit_dry_run_is_not_executed():
    decision = _decision(portfolio_actions=[
        {"symbol": "AAPL", "action": "EXIT", "reasoning_tag": "thesis_broken"},
    ])
    report = dispatch(decision, {}, _active("AAPL"), execute=False)
    row = report["portfolio_actions"][0]
    assert row["action"] == "EXIT"
    assert row["executed"] is False


def test_dispatch_trackb_execute_false_does_not_call_execute_sell(monkeypatch):
    called = {"n": 0}

    def _boom(*_args, **_kw):
        called["n"] += 1
        raise AssertionError("execute_sell must not be invoked when execute=False")

    import orders_core
    monkeypatch.setattr(orders_core, "execute_sell", _boom, raising=True)

    decision = _decision(portfolio_actions=[
        {"symbol": "AAPL", "action": "EXIT", "reasoning_tag": "x"},
        {"symbol": "MSFT", "action": "TRIM", "trim_pct": 25, "reasoning_tag": "y"},
    ])
    report = dispatch(decision, {}, {**_active("AAPL"), **_active("MSFT")}, execute=False)
    assert called["n"] == 0
    assert len(report["portfolio_actions"]) == 2
    assert all(r["executed"] is False for r in report["portfolio_actions"])


def test_dispatch_trackb_missing_symbol_is_skipped():
    decision = _decision(portfolio_actions=[
        {"symbol": None, "action": "EXIT", "reasoning_tag": "broken"},
    ])
    report = dispatch(decision, {}, {}, execute=False)
    assert len(report["portfolio_actions"]) == 1
    assert report["portfolio_actions"][0]["executed"] is False


def test_pm_cutover_branch_still_wired_in_bot_trading():
    text = (_REPO / "bot_trading.py").read_text()
    assert "pm_legacy_opus_review_enabled" in text
    assert "_aorch_pm._run_apex_pipeline" in text
    assert "TRACK_B_PM" in text
    assert "run_portfolio_review" in text


def test_pm_legacy_flag_default_is_true():
    from safety_overlay import pm_legacy_opus_review_enabled
    assert pm_legacy_opus_review_enabled() is True


def test_trackb_report_row_keys_match_pm_loop_expectation():
    decision = _decision(portfolio_actions=[
        {"symbol": "AAPL", "action": "TRIM", "trim_pct": 25, "reasoning_tag": "soft"},
    ])
    report = dispatch(decision, {}, _active("AAPL"), execute=False)
    row = report["portfolio_actions"][0]
    for k in ("symbol", "action", "trim_pct", "reasoning_tag"):
        assert k in row, f"PM cutover loop expects '{k}' in portfolio_actions row"
