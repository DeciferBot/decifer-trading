"""
Phase 7C.1 — unit tests for apex_divergence.classify() and the read-only
mirror serializers.

Covers:
  - AGREE path with no events beyond the AGREE marker
  - Each of the 9 non-AGREE categories
  - Severity assignment
  - PM conflict soft vs hard cases
  - write_divergence_record append path (tmp file)
"""

from __future__ import annotations

import json

import apex_divergence as AD


# ── Helpers ──────────────────────────────────────────────────────────────────

def _leg(entry=None, pm=None, forced=None):
    return AD.mirror_legacy_decision(
        cycle_id="c1",
        trigger_type="SCAN_CYCLE",
        new_entries=entry or [],
        portfolio_actions=pm or [],
        forced_exits=forced or [],
        payloads_by_symbol={},
    )


def _apex(entries=None, pm=None, note="", fallback=False, schema=False):
    decision = {"new_entries": entries or [], "portfolio_actions": pm or []}
    result = {"decision": decision, "would_dispatch": [], "rejected": [], "note": note}
    mirror = AD.mirror_apex_decision(
        cycle_id="c1", trigger_type="SCAN_CYCLE",
        pipeline_result=result, candidates_by_symbol={},
    )
    if fallback:
        mirror["fallback"] = True
    if schema:
        mirror["schema_reject"] = True
    return mirror


# ── AGREE ────────────────────────────────────────────────────────────────────

def test_agree_when_both_empty():
    events = AD.classify(_leg(), _apex())
    assert [e.category for e in events] == ["AGREE"]
    assert events[0].severity == "LOW"


def test_agree_when_identical_entry():
    entry = {"symbol": "AAPL", "direction": "LONG", "trade_type": "SWING",
             "instrument": "stock", "notional": 10000, "stop_loss": 98.0,
             "atr_used": 2.0}
    apex_entry = {"symbol": "AAPL", "direction": "LONG", "trade_type": "SWING",
                  "instrument": "stock"}
    events = AD.classify(_leg([entry]), _apex([apex_entry]))
    # Only per-symbol AGREE marker expected
    cats = [e.category for e in events]
    assert cats == ["AGREE"]


# ── Track A divergence categories ────────────────────────────────────────────

def test_direction_conflict_high():
    L = {"symbol": "AAPL", "direction": "LONG", "trade_type": "INTRADAY"}
    A = {"symbol": "AAPL", "direction": "SHORT", "trade_type": "INTRADAY"}
    events = AD.classify(_leg([L]), _apex([A]))
    cats = [e.category for e in events]
    assert "DIRECTION_CONFLICT" in cats
    hit = [e for e in events if e.category == "DIRECTION_CONFLICT"][0]
    assert hit.severity == "HIGH"
    assert hit.symbol == "AAPL"


def test_instrument_divergence_medium():
    L = {"symbol": "TSLA", "direction": "LONG", "instrument": "stock"}
    A = {"symbol": "TSLA", "direction": "LONG", "instrument": "call"}
    events = AD.classify(_leg([L]), _apex([A]))
    hit = [e for e in events if e.category == "INSTRUMENT_DIVERGENCE"]
    assert hit and hit[0].severity == "MEDIUM"


def test_sizing_drift_low():
    L = {"symbol": "MSFT", "direction": "LONG", "notional": 10000.0}
    A = {"symbol": "MSFT", "direction": "LONG", "notional": 13000.0}  # 30%
    events = AD.classify(_leg([L]), _apex([A]))
    hit = [e for e in events if e.category == "SIZING_DRIFT"]
    assert hit and hit[0].severity == "LOW"
    assert 0.29 < hit[0].detail["drift_pct"] < 0.31


def test_stop_drift_low_above_one_atr():
    L = {"symbol": "NVDA", "direction": "LONG", "stop_loss": 100.0, "atr_used": 2.0}
    A = {"symbol": "NVDA", "direction": "LONG", "stop_loss": 102.5, "atr_used": 2.0}
    events = AD.classify(_leg([L]), _apex([A]))
    hit = [e for e in events if e.category == "STOP_DRIFT"]
    assert hit and hit[0].severity == "LOW"


def test_stop_drift_not_flagged_within_one_atr():
    L = {"symbol": "NVDA", "direction": "LONG", "stop_loss": 100.0, "atr_used": 2.0}
    A = {"symbol": "NVDA", "direction": "LONG", "stop_loss": 101.5, "atr_used": 2.0}
    events = AD.classify(_leg([L]), _apex([A]))
    assert not [e for e in events if e.category == "STOP_DRIFT"]


def test_entry_miss_apex_medium():
    L = {"symbol": "AMD", "direction": "LONG"}
    events = AD.classify(_leg([L]), _apex([]))
    hit = [e for e in events if e.category == "ENTRY_MISS_APEX"]
    assert hit and hit[0].severity == "MEDIUM"


def test_entry_miss_legacy_medium():
    A = {"symbol": "AMD", "direction": "LONG"}
    events = AD.classify(_leg([]), _apex([A]))
    hit = [e for e in events if e.category == "ENTRY_MISS_LEGACY"]
    assert hit and hit[0].severity == "MEDIUM"


# ── Track B PM conflict ──────────────────────────────────────────────────────

def test_pm_exit_conflict_high_exit_vs_hold():
    events = AD.classify(
        _leg(pm=[{"symbol": "X", "action": "EXIT"}]),
        _apex(pm=[{"symbol": "X", "action": "HOLD"}]),
    )
    hit = [e for e in events if e.category == "PM_EXIT_CONFLICT"]
    assert hit and hit[0].severity == "HIGH"


def test_pm_hold_vs_trim_is_soft_no_event():
    events = AD.classify(
        _leg(pm=[{"symbol": "X", "action": "HOLD"}]),
        _apex(pm=[{"symbol": "X", "action": "TRIM", "trim_pct": 25}]),
    )
    assert not [e for e in events if e.category == "PM_EXIT_CONFLICT"]


# ── Apex-wide failures ───────────────────────────────────────────────────────

def test_apex_fallback_high():
    events = AD.classify(_leg(), _apex(fallback=True))
    hit = [e for e in events if e.category == "APEX_FALLBACK"]
    assert hit and hit[0].severity == "HIGH"


def test_schema_reject_high():
    events = AD.classify(_leg(), _apex(schema=True))
    hit = [e for e in events if e.category == "SCHEMA_REJECT"]
    assert hit and hit[0].severity == "HIGH"


# ── Writer ───────────────────────────────────────────────────────────────────

def test_write_divergence_record_appends_jsonl(tmp_path):
    path = tmp_path / "divergence.jsonl"
    legacy = _leg([{"symbol": "A", "direction": "LONG"}])
    apex = _apex([{"symbol": "A", "direction": "LONG"}])
    events = AD.classify(legacy, apex)
    AD.write_divergence_record(
        legacy_mirror=legacy, apex_mirror=apex, events=events, path=str(path),
    )
    AD.write_divergence_record(
        legacy_mirror=legacy, apex_mirror=apex, events=events, path=str(path),
    )
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["legacy"]["side"] == "legacy"
    assert rec["apex"]["side"] == "apex"
    assert rec["events"][0]["category"] in ("AGREE",)  # per-symbol AGREE


# ── Read-only guarantees ─────────────────────────────────────────────────────

def test_module_does_not_import_order_layer():
    """
    Structural check: apex_divergence must not import execution modules.
    We inspect ast imports rather than the raw source so docstrings that
    legitimately describe the read-only guarantee don't trip the check.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(AD))
    forbidden = {"orders_core", "orders_state", "bot_ibkr"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"apex_divergence must not import {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            assert mod not in forbidden, (
                f"apex_divergence must not import from {node.module}"
            )
