"""
apex_orchestrator.py — Phase 6 shadow-only Apex pipeline runner.

Single responsibility: build an ApexInput from the same inputs the legacy
pipeline consumed, call market_intelligence.apex_call(), run the guardrails
semantic filter, and LOG what the Apex path would have dispatched. This
module does NOT submit orders, does NOT call any execute_* function, and
does NOT mutate positions. Its only side effect is an append to a JSONL
audit file so the Apex and legacy decisions can be compared offline.

Public surface (called by bot_trading and bot_sentinel when
safety_overlay.should_run_apex_shadow() is True):

    _run_apex_pipeline(apex_input, candidates_by_symbol, *, execute=False)
        → {"decision": ApexDecision, "would_dispatch": [...], "rejected": [...]}

    build_scan_cycle_apex_input(candidates, review_positions, portfolio_state,
                                regime, overnight_research=None)
        → ApexInput dict for trigger_type="SCAN_CYCLE"

    log_shadow_result(trigger_type, result, trigger_context=None) → None
        Appends one JSON line to data/apex_shadow_log.jsonl.

Constraints:
- execute defaults to False. A caller that sets execute=True opts into live
  dispatch — Phase 6 does NOT wire any such caller. That hook exists for
  Phase 7 tests and the eventual cutover.
- If any step fails the function logs and returns a safe empty result. It
  never raises into the caller.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from config import CONFIG

log = logging.getLogger("decifer.apex_orchestrator")

_SHADOW_LOG_PATH = os.path.join(
    CONFIG.get("data_dir", "data"), "apex_shadow_log.jsonl"
)


# ── Input builders ───────────────────────────────────────────────────────────

def build_scan_cycle_apex_input(
    candidates: list[dict],
    review_positions: list[dict] | None = None,
    portfolio_state: dict | None = None,
    regime: dict | None = None,
    overnight_research: str | None = None,
) -> dict:
    """Build a SCAN_CYCLE ApexInput dict from guardrails-filtered inputs."""
    return {
        "trigger_type": "SCAN_CYCLE",
        "trigger_context": None,
        "track_a": {"candidates": list(candidates or [])},
        "track_b": list(review_positions or []),
        "market_context": {
            "regime": regime or {},
            "overnight_research": overnight_research,
        },
        "portfolio_state": portfolio_state or {},
        "scan_ts": datetime.now(UTC).isoformat(),
    }


# ── Shadow log ───────────────────────────────────────────────────────────────

def log_shadow_result(
    trigger_type: str,
    result: dict,
    trigger_context: dict | None = None,
) -> None:
    """Append one JSON line capturing the Apex shadow outcome."""
    try:
        os.makedirs(os.path.dirname(_SHADOW_LOG_PATH), exist_ok=True)
        # Phase 7C.3: surface apex _meta (latency, tokens, model) at the top
        # level of the shadow record so the roll-up doesn't have to dig into
        # the decision dict.
        _decision = result.get("decision") or {}
        _apex_meta = _decision.get("_meta") if isinstance(_decision, dict) else None
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "trigger_type": trigger_type,
            "trigger_context": trigger_context,
            "decision": _decision,
            "would_dispatch": result.get("would_dispatch") or [],
            "rejected": result.get("rejected") or [],
            "note": result.get("note", ""),
            "apex_meta": _apex_meta or {},
        }
        with open(_SHADOW_LOG_PATH, "a") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log.warning("apex_orchestrator: shadow log write failed — %s", e)


# ── Pipeline runner ──────────────────────────────────────────────────────────

def _summarise_dispatch(
    decision: dict,
    candidates_by_symbol: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """
    Convert an ApexDecision into (would_dispatch, rejected) lists.

    would_dispatch: entries that would be routed to execute_buy / execute_short
                    (instrument, direction, symbol, qty proxy from payload).
    rejected      : entries with trade_type == "AVOID" or unrecognized.

    No sizing is performed here. Phase 6 is shadow-only; the actual qty will
    be computed by the real dispatcher at cutover via CONVICTION_MULT + ATR.
    """
    would: list[dict] = []
    rejected: list[dict] = []
    for entry in decision.get("new_entries") or []:
        sym = entry.get("symbol")
        payload = candidates_by_symbol.get(sym) or {}
        if entry.get("trade_type") == "AVOID":
            rejected.append({
                "symbol": sym,
                "reason": "AVOID",
                "rationale": entry.get("rationale"),
            })
            continue
        would.append({
            "symbol": sym,
            "direction": entry.get("direction"),
            "trade_type": entry.get("trade_type"),
            "conviction": entry.get("conviction"),
            "instrument": entry.get("instrument"),
            "score": payload.get("score"),
            "price": payload.get("price"),
            "atr_5m": payload.get("atr_5m"),
            "atr_daily": payload.get("atr_daily"),
            "rationale": entry.get("rationale"),
        })
    for act in decision.get("portfolio_actions") or []:
        would.append({
            "symbol": act.get("symbol"),
            "action": act.get("action"),
            "trim_pct": act.get("trim_pct"),
            "reasoning_tag": act.get("reasoning_tag"),
        })
    return would, rejected


def _run_apex_pipeline(
    apex_input: dict,
    candidates_by_symbol: dict[str, dict] | None = None,
    *,
    execute: bool = False,
    active_trades: dict | None = None,
    ib: Any = None,
    portfolio_value: float = 0.0,
    regime: dict | None = None,
    forced_exits: list | None = None,
) -> dict:
    """
    Run the Apex path end-to-end.

    Shadow mode (execute=False, default):
      1. market_intelligence.apex_call(apex_input) — returns ApexDecision
         (apex_call already runs validate_apex_decision_schema internally
          and returns _fallback_decision on any failure; never raises).
      2. guardrails.filter_semantic_violations(decision, candidates_by_symbol)
         — removes per-entry violations.
      3. Build (would_dispatch, rejected) summary.
      4. Return {"decision", "would_dispatch", "rejected", "note": "shadow"}.

    Execute mode (execute=True, Phase 8A cutover):
      After shadow steps 1–3, additionally call
      signal_dispatcher.dispatch(decision, candidates_by_symbol, active_trades,
      ib=ib, portfolio_value=portfolio_value, regime=regime, execute=True)
      to submit live orders, then dispatch any forced_exits via
      signal_dispatcher.dispatch_forced_exit. Returns the shadow shape plus
      "dispatch_report" (the dispatch() return dict) and "note": "executed".

    Caller responsibility: execute=True requires a live ib client and real
    active_trades / portfolio_value. No caller in Phase 6/7 passes execute=True
    — the scan-cycle cutover branch added in Phase 8A.2 is the first.
    """
    candidates_by_symbol = candidates_by_symbol or {}
    empty: dict[str, Any] = {
        "decision": None,
        "would_dispatch": [],
        "rejected": [],
        "note": "",
    }

    try:
        from market_intelligence import apex_call
    except Exception as e:
        log.error("apex_orchestrator: apex_call import failed — %s", e)
        empty["note"] = f"import_error:{e}"
        return empty

    try:
        decision = apex_call(apex_input)
    except Exception as e:
        log.error("apex_orchestrator: apex_call raised — %s", e)
        empty["note"] = f"apex_call_error:{e}"
        return empty

    try:
        from guardrails import filter_semantic_violations
        decision = filter_semantic_violations(decision, candidates_by_symbol)
    except Exception as e:
        log.warning("apex_orchestrator: semantic filter error (non-fatal) — %s", e)

    would, rejected = _summarise_dispatch(decision, candidates_by_symbol)

    # Observability: when candidates were presented but Apex returned zero entries,
    # log the count and market_read so Monday diagnosis is immediate.
    _cand_count = len((apex_input.get("track_a") or {}).get("candidates") or [])
    _entry_count = len(decision.get("new_entries") or [])
    if _cand_count > 0 and _entry_count == 0:
        _ttype = apex_input.get("trigger_type", "?")
        _mread = (decision.get("market_read") or "")[:200]
        log.warning(
            "apex: zero entries — trigger=%s candidates=%d market_read=%r",
            _ttype, _cand_count, _mread,
        )

    result: dict[str, Any] = {
        "decision": decision,
        "would_dispatch": would,
        "rejected": rejected,
        "note": "shadow",
    }

    if not execute:
        return result

    # ── Phase 8A execute path ──────────────────────────────────────────────
    # Submit orders via signal_dispatcher.dispatch. Swallow dispatch errors
    # so the caller (scan-cycle orchestrator) never sees a raise — a crash
    # here would orphan the Apex decision without a legacy fallback.
    dispatch_report: dict[str, Any] = {
        "new_entries": [], "portfolio_actions": [], "forced_exits": [], "errors": [],
    }
    try:
        from signal_dispatcher import dispatch as _sd_dispatch
        dispatch_report = _sd_dispatch(
            decision or {},
            candidates_by_symbol=candidates_by_symbol,
            active_trades=active_trades or {},
            ib=ib,
            portfolio_value=portfolio_value,
            regime=regime or {},
            execute=True,
        )
    except Exception as e:
        log.error("apex_orchestrator: dispatch(execute=True) raised — %s", e)
        dispatch_report["errors"].append(f"dispatch_error:{e}")

    # Forced exits (from guardrails.screen_open_positions) are separate from
    # the Apex decision — dispatch each via dispatch_forced_exit.
    forced_report: list[dict] = []
    if forced_exits:
        try:
            from signal_dispatcher import dispatch_forced_exit as _sd_forced
            for entry in forced_exits:
                sym, reason = (entry[0], entry[1]) if isinstance(entry, tuple) else (
                    entry.get("symbol"), entry.get("reason"),
                )
                if not sym:
                    continue
                try:
                    forced_report.append(_sd_forced(
                        symbol=sym, reason=reason or "forced_exit",
                        ib=ib, execute=True,
                    ))
                except Exception as e:
                    log.error("apex_orchestrator: forced exit %s failed — %s", sym, e)
                    dispatch_report["errors"].append(f"forced_exit_error:{sym}:{e}")
        except Exception as e:
            log.error("apex_orchestrator: dispatch_forced_exit import failed — %s", e)
            dispatch_report["errors"].append(f"forced_exit_import:{e}")

    dispatch_report["forced_exits"] = (dispatch_report.get("forced_exits") or []) + forced_report
    result["dispatch_report"] = dispatch_report
    result["note"] = "executed"
    return result
