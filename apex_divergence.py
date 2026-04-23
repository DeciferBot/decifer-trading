"""
apex_divergence.py — Phase 7C.1 read-only shadow comparison.

Single responsibility: produce comparable, side-by-side records of the legacy
dispatcher's intended actions and the Apex path's dry-run actions for one
scan cycle (or one interrupt), and classify the divergences.

─── READ-ONLY INSTRUMENTATION ───
This module is instrumentation. It does NOT:
  - call any execute_* function
  - mutate orders_state.active_trades
  - submit IBKR orders
  - influence sizing, stops, or any live decision path
The legacy decision is captured by serializing already-computed inputs
(signal list, portfolio actions, forced exits) against the same helpers the
live dispatcher uses (calculate_position_size, calculate_stops, CONVICTION_MULT)
so the mirror matches what the legacy path would dispatch — without touching
the dispatcher itself.

No imports from orders_core, orders_state, bot_ibkr, or any module that places
orders. The only code this module talks to is pure-math helpers.

Public surface:
    mirror_legacy_decision(...)        → LegacyDecisionMirror
    mirror_apex_decision(result, ...)  → ApexDecisionMirror
    classify(legacy, apex)             → list[DivergenceEvent]
    write_divergence_record(...)       → None   (append to jsonl)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from config import CONFIG

log = logging.getLogger("decifer.apex_divergence")

# ── Severity + category taxonomy (Phase 7B A.2) ──────────────────────────────

SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"

_CATEGORY_SEVERITY: dict[str, str] = {
    "AGREE": SEVERITY_LOW,
    "SIZING_DRIFT": SEVERITY_LOW,
    "STOP_DRIFT": SEVERITY_LOW,
    "INSTRUMENT_DIVERGENCE": SEVERITY_MEDIUM,
    "ENTRY_MISS_APEX": SEVERITY_MEDIUM,
    "ENTRY_MISS_LEGACY": SEVERITY_MEDIUM,
    "DIRECTION_CONFLICT": SEVERITY_HIGH,
    "PM_EXIT_CONFLICT": SEVERITY_HIGH,
    "SCHEMA_REJECT": SEVERITY_HIGH,
    "APEX_FALLBACK": SEVERITY_HIGH,
}

# Tolerances
_NOTIONAL_DRIFT_LOW = 0.20   # <= 20% → AGREE on sizing axis
_NOTIONAL_DRIFT_MED = 0.50   # 20-50% → SIZING_DRIFT
_STOP_DRIFT_ATR = 1.0        # > 1.0 ATR units → STOP_DRIFT


# ── Record shapes (simple dict — TypedDicts deferred to schemas.py later) ────

@dataclass
class DivergenceEvent:
    category: str
    severity: str
    symbol: str | None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "symbol": self.symbol,
            "detail": self.detail,
        }


# ── Legacy mirror (read-only serializer) ─────────────────────────────────────

def mirror_legacy_decision(
    *,
    cycle_id: str,
    trigger_type: str,
    new_entries: list[dict] | None = None,
    portfolio_actions: list[dict] | None = None,
    forced_exits: list[dict] | None = None,
    payloads_by_symbol: dict[str, dict] | None = None,
) -> dict:
    """
    Build a LegacyDecisionMirror from the same inputs the legacy dispatcher
    was about to act on.

    Parameters are pre-computed values the caller already has — this function
    does NOT re-run any pipeline. It only normalizes shape so `classify()` can
    compare the legacy and Apex sides.

    new_entries items should have: symbol, direction, trade_type, instrument,
    qty (if known), notional (if known), stop_loss, take_profit, conviction_mult,
    atr_used, score.
    Missing fields are preserved as None.

    portfolio_actions items: symbol, action, trim_pct, reasoning_tag.
    forced_exits items: symbol, reason.
    """
    return {
        "side": "legacy",
        "cycle_id": cycle_id,
        "trigger_type": trigger_type,
        "ts": datetime.now(UTC).isoformat(),
        "new_entries": [_norm_entry(e) for e in (new_entries or [])],
        "portfolio_actions": [_norm_pm(a) for a in (portfolio_actions or [])],
        "forced_exits": [_norm_forced(x) for x in (forced_exits or [])],
        "payloads_digest": _digest_payloads(payloads_by_symbol or {}),
    }


def mirror_apex_decision(
    *,
    cycle_id: str,
    trigger_type: str,
    pipeline_result: dict,
    candidates_by_symbol: dict[str, dict] | None = None,
) -> dict:
    """
    Build an ApexDecisionMirror from `apex_orchestrator._run_apex_pipeline`'s
    return value. No LLM call here — just normalization.

    pipeline_result shape (from apex_orchestrator):
        {"decision": ApexDecision|None, "would_dispatch": [...], "rejected": [...], "note": str}

    The "note" string surfaces APEX_FALLBACK / SCHEMA_REJECT markers.
    """
    decision = pipeline_result.get("decision") or {}
    new_entries_apex = []
    for entry in decision.get("new_entries") or []:
        sym = entry.get("symbol")
        payload = (candidates_by_symbol or {}).get(sym) or {}
        new_entries_apex.append(_apex_entry_to_mirror(entry, payload))

    portfolio_actions = [_norm_pm(a) for a in (decision.get("portfolio_actions") or [])]
    forced_exits = [_norm_forced(x) for x in (pipeline_result.get("forced_exits") or [])]

    note = pipeline_result.get("note", "")
    fallback = bool(decision.get("_fallback")) or "apex_call_error" in note
    schema_reject = "schema" in note or "validate" in note

    return {
        "side": "apex",
        "cycle_id": cycle_id,
        "trigger_type": trigger_type,
        "ts": datetime.now(UTC).isoformat(),
        "new_entries": new_entries_apex,
        "portfolio_actions": portfolio_actions,
        "forced_exits": forced_exits,
        "fallback": fallback,
        "schema_reject": schema_reject,
        "note": note,
        "payloads_digest": _digest_payloads(candidates_by_symbol or {}),
    }


# ── Classifier ───────────────────────────────────────────────────────────────

def classify(legacy: dict, apex: dict) -> list[DivergenceEvent]:
    """
    Compare a LegacyDecisionMirror and an ApexDecisionMirror.

    Returns a list of DivergenceEvent, one per per-symbol comparison.
    Pure function — no I/O, no side effects.
    """
    events: list[DivergenceEvent] = []

    # Apex-wide failure modes first — these make per-entry comparison noisy.
    if apex.get("fallback"):
        events.append(DivergenceEvent(
            category="APEX_FALLBACK",
            severity=_CATEGORY_SEVERITY["APEX_FALLBACK"],
            symbol=None,
            detail={"note": apex.get("note", "")},
        ))
    if apex.get("schema_reject"):
        events.append(DivergenceEvent(
            category="SCHEMA_REJECT",
            severity=_CATEGORY_SEVERITY["SCHEMA_REJECT"],
            symbol=None,
            detail={"note": apex.get("note", "")},
        ))

    # Track A — new entries, keyed by symbol.
    legacy_entries = {e["symbol"]: e for e in legacy.get("new_entries") or [] if e.get("symbol")}
    apex_entries = {e["symbol"]: e for e in apex.get("new_entries") or [] if e.get("symbol")}

    all_symbols = set(legacy_entries) | set(apex_entries)
    for sym in sorted(all_symbols):
        L = legacy_entries.get(sym)
        A = apex_entries.get(sym)
        if L and not A:
            events.append(DivergenceEvent(
                "ENTRY_MISS_APEX", _CATEGORY_SEVERITY["ENTRY_MISS_APEX"], sym,
                {"legacy": L},
            ))
            continue
        if A and not L:
            events.append(DivergenceEvent(
                "ENTRY_MISS_LEGACY", _CATEGORY_SEVERITY["ENTRY_MISS_LEGACY"], sym,
                {"apex": A},
            ))
            continue

        # Both sides present — compare.
        ev = _compare_entry(sym, L, A)
        events.extend(ev)

    # Track B — portfolio actions, keyed by symbol.
    legacy_pm = {a["symbol"]: a for a in legacy.get("portfolio_actions") or [] if a.get("symbol")}
    apex_pm = {a["symbol"]: a for a in apex.get("portfolio_actions") or [] if a.get("symbol")}
    for sym in sorted(set(legacy_pm) | set(apex_pm)):
        L = legacy_pm.get(sym) or {"action": "HOLD"}
        A = apex_pm.get(sym) or {"action": "HOLD"}
        if _pm_conflict(L.get("action"), A.get("action")):
            events.append(DivergenceEvent(
                "PM_EXIT_CONFLICT", _CATEGORY_SEVERITY["PM_EXIT_CONFLICT"], sym,
                {"legacy": L, "apex": A},
            ))

    # If nothing recorded, emit a single AGREE event so metrics see the cycle.
    if not events:
        events.append(DivergenceEvent("AGREE", SEVERITY_LOW, None, {}))
    return events


# ── Writer ───────────────────────────────────────────────────────────────────

_DIVERGENCE_LOG_PATH = os.path.join(
    CONFIG.get("data_dir", "data"), "apex_divergence_log.jsonl"
)


def write_divergence_record(
    *,
    legacy_mirror: dict,
    apex_mirror: dict,
    events: list[DivergenceEvent] | None = None,
    path: str | None = None,
) -> None:
    """Append one DivergenceRecord JSON line. Read-only side effect (file append)."""
    path = path or _DIVERGENCE_LOG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "cycle_id": legacy_mirror.get("cycle_id") or apex_mirror.get("cycle_id"),
            "trigger_type": legacy_mirror.get("trigger_type") or apex_mirror.get("trigger_type"),
            "legacy": legacy_mirror,
            "apex": apex_mirror,
            "events": [e.to_dict() for e in (events or [])],
        }
        with open(path, "a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        log.warning("apex_divergence: write failed — %s", e)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _norm_entry(e: dict) -> dict:
    return {
        "symbol": e.get("symbol"),
        "direction": e.get("direction"),
        "trade_type": e.get("trade_type"),
        "instrument": e.get("instrument"),
        "qty": e.get("qty"),
        "notional": e.get("notional"),
        "stop_loss": e.get("stop_loss") or e.get("sl"),
        "take_profit": e.get("take_profit") or e.get("tp"),
        "conviction_mult": e.get("conviction_mult"),
        "atr_used": e.get("atr_used"),
        "score": e.get("score"),
    }


def _apex_entry_to_mirror(entry: dict, payload: dict) -> dict:
    """Convert an ApexDecision new_entries item into the mirror shape.

    No sizing is done — this is instrumentation. If the caller wants sizing
    numbers for the Apex side, it can extend this later with a call to
    calculate_position_size (pure math). Phase 7C.1 keeps it minimal to
    avoid hidden coupling.
    """
    return {
        "symbol": entry.get("symbol"),
        "direction": entry.get("direction"),
        "trade_type": entry.get("trade_type"),
        "instrument": entry.get("instrument"),
        # If a caller has pre-computed these (e.g. via dispatch(execute=False)
        # dry-run), preserve them; otherwise they remain None. The mirror never
        # runs sizing itself.
        "qty": entry.get("qty"),
        "notional": entry.get("notional"),
        "stop_loss": entry.get("stop_loss") or entry.get("sl"),
        "take_profit": entry.get("take_profit") or entry.get("tp"),
        "conviction_mult": entry.get("conviction_mult"),
        "conviction": entry.get("conviction"),
        "atr_used": (
            payload.get("atr_daily")
            if entry.get("trade_type") in ("SWING", "POSITION")
            else payload.get("atr_5m")
        ),
        "score": payload.get("score"),
    }


def _norm_pm(a: dict) -> dict:
    return {
        "symbol": a.get("symbol"),
        "action": (a.get("action") or "HOLD").upper(),
        "trim_pct": a.get("trim_pct"),
        "reasoning_tag": a.get("reasoning_tag"),
    }


def _norm_forced(x: dict) -> dict:
    if isinstance(x, tuple) and len(x) == 2:
        return {"symbol": x[0], "reason": x[1]}
    return {"symbol": x.get("symbol"), "reason": x.get("reason")}


def _digest_payloads(payloads: dict[str, dict]) -> str:
    if not payloads:
        return ""
    try:
        serialized = json.dumps(
            {s: {"score": p.get("score"), "direction": p.get("direction"), "price": p.get("price")}
             for s, p in payloads.items()},
            sort_keys=True, default=str,
        )
    except Exception:
        serialized = str(sorted(payloads.keys()))
    return hashlib.sha1(serialized.encode()).hexdigest()[:16]


def _compare_entry(sym: str, L: dict, A: dict) -> list[DivergenceEvent]:
    events: list[DivergenceEvent] = []

    # Direction flip = HIGH severity.
    if L.get("direction") and A.get("direction") and L["direction"] != A["direction"]:
        events.append(DivergenceEvent(
            "DIRECTION_CONFLICT", _CATEGORY_SEVERITY["DIRECTION_CONFLICT"], sym,
            {"legacy_dir": L["direction"], "apex_dir": A["direction"]},
        ))
        return events  # Further comparison meaningless when directions disagree.

    # Instrument divergence (stock vs call/put).
    L_inst = (L.get("instrument") or "").lower()
    A_inst = (A.get("instrument") or "").lower()
    if L_inst and A_inst and L_inst != A_inst:
        events.append(DivergenceEvent(
            "INSTRUMENT_DIVERGENCE", _CATEGORY_SEVERITY["INSTRUMENT_DIVERGENCE"], sym,
            {"legacy_instrument": L_inst, "apex_instrument": A_inst},
        ))

    # Sizing drift — only when both sides report notional.
    L_n = L.get("notional")
    A_n = A.get("notional")
    if L_n and A_n and L_n > 0:
        drift = abs(A_n - L_n) / L_n
        if drift > _NOTIONAL_DRIFT_LOW:
            cat = "SIZING_DRIFT" if drift <= _NOTIONAL_DRIFT_MED else "SIZING_DRIFT"
            events.append(DivergenceEvent(
                cat, _CATEGORY_SEVERITY[cat], sym,
                {"legacy_notional": L_n, "apex_notional": A_n, "drift_pct": round(drift, 3)},
            ))

    # Stop drift — require ATR unit.
    atr = L.get("atr_used") or A.get("atr_used")
    L_sl = L.get("stop_loss")
    A_sl = A.get("stop_loss")
    if atr and L_sl and A_sl and atr > 0:
        delta_atr = abs(A_sl - L_sl) / atr
        if delta_atr > _STOP_DRIFT_ATR:
            events.append(DivergenceEvent(
                "STOP_DRIFT", _CATEGORY_SEVERITY["STOP_DRIFT"], sym,
                {"legacy_sl": L_sl, "apex_sl": A_sl, "atr": atr, "delta_atr": round(delta_atr, 2)},
            ))

    if not events:
        events.append(DivergenceEvent("AGREE", SEVERITY_LOW, sym, {}))
    return events


def _pm_conflict(legacy_action: str | None, apex_action: str | None) -> bool:
    """True when the two sides disagree on portfolio action in a material way."""
    L = (legacy_action or "HOLD").upper()
    A = (apex_action or "HOLD").upper()
    if L == A:
        return False
    # HOLD vs TRIM is a soft disagreement — not flagged PM_EXIT_CONFLICT.
    soft = {frozenset({"HOLD", "TRIM"})}
    if frozenset({L, A}) in soft:
        return False
    return True
