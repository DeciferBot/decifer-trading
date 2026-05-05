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
import threading
from datetime import UTC, datetime
from typing import Any

from config import CONFIG

log = logging.getLogger("decifer.apex_orchestrator")

_shadow_log_lock = threading.Lock()

_SHADOW_LOG_PATH = os.path.join(
    CONFIG.get("data_dir", "data"), "apex_shadow_log.jsonl"
)

_AUDIT_LOG_PATH = os.path.join(
    CONFIG.get("data_dir", "data"), "apex_decision_audit.jsonl"
)
_PROMPT_SNAPSHOT_PATH = os.path.join(
    CONFIG.get("data_dir", "data"), "apex_prompt_snapshot.jsonl"
)
_RESPONSE_SNAPSHOT_PATH = os.path.join(
    CONFIG.get("data_dir", "data"), "apex_response_snapshot.jsonl"
)
_audit_log_lock = threading.Lock()
_snapshot_lock  = threading.Lock()


def _write_apex_audit(record: dict) -> None:
    """Append one JSON line to apex_decision_audit.jsonl. Non-critical — never raises."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_AUDIT_LOG_PATH)), exist_ok=True)
        with _audit_log_lock, open(_AUDIT_LOG_PATH, "a") as _fh:
            _fh.write(json.dumps(record, default=str) + "\n")
    except Exception as _e:
        log.debug("apex_orchestrator: audit log write failed — %s", _e)


def _write_prompt_snapshot(cycle_id: str, user_prompt: str) -> None:
    """Write one full Apex user-prompt per cycle to apex_prompt_snapshot.jsonl."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_PROMPT_SNAPSHOT_PATH)), exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "cycle_id": cycle_id,
            "user_prompt": user_prompt,
        }
        with _snapshot_lock, open(_PROMPT_SNAPSHOT_PATH, "a") as _fh:
            _fh.write(json.dumps(record, default=str) + "\n")
    except Exception as _e:
        log.debug("apex_orchestrator: prompt snapshot write failed — %s", _e)


def _write_response_snapshot(cycle_id: str, raw_response: str) -> None:
    """Write the raw Apex LLM response (pre-parse) per cycle to apex_response_snapshot.jsonl."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_RESPONSE_SNAPSHOT_PATH)), exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "cycle_id": cycle_id,
            "raw_response": raw_response,
        }
        with _snapshot_lock, open(_RESPONSE_SNAPSHOT_PATH, "a") as _fh:
            _fh.write(json.dumps(record, default=str) + "\n")
    except Exception as _e:
        log.debug("apex_orchestrator: response snapshot write failed — %s", _e)


# ── Input builders ───────────────────────────────────────────────────────────

def build_scan_cycle_apex_input(
    candidates: list[dict],
    review_positions: list[dict] | None = None,
    portfolio_state: dict | None = None,
    regime: dict | None = None,
    overnight_research: str | None = None,
    options_flow: list[dict] | None = None,
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
            "options_flow": list(options_flow or []),
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
        with _shadow_log_lock, open(_SHADOW_LOG_PATH, "a") as fh:
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

    _pre_call_cands = (apex_input.get("track_a") or {}).get("candidates") or []
    _floor_eligible = [c.get("symbol") for c in _pre_call_cands if (c.get("score") or 0) >= 35]
    if len(_floor_eligible) >= 3:
        log.info("apex: pre-call floor rule eligible (≥35): %s", _floor_eligible)

    try:
        decision = apex_call(apex_input)
    except Exception as e:
        import traceback as _tb
        log.error("apex_orchestrator: apex_call raised — %s\n%s", e, _tb.format_exc())
        empty["note"] = f"apex_call_error:{e}"
        return empty

    try:
        from guardrails import filter_semantic_violations
        decision = filter_semantic_violations(decision, candidates_by_symbol)
    except Exception as e:
        log.warning("apex_orchestrator: semantic filter error (non-fatal) — %s", e)

    would, rejected = _summarise_dispatch(decision, candidates_by_symbol)

    # ── Prompt and response snapshots ────────────────────────────────────
    _apex_meta_for_snap = decision.get("_meta") or {}
    _snap_cycle_id = apex_input.get("scan_ts") or decision.get("scan_ts") or ""
    _snap_user_prompt = _apex_meta_for_snap.get("user_prompt")
    _snap_raw_response = _apex_meta_for_snap.get("raw_response")
    if _snap_user_prompt:
        _write_prompt_snapshot(_snap_cycle_id, _snap_user_prompt)
    if _snap_raw_response:
        _write_response_snapshot(_snap_cycle_id, _snap_raw_response)

    # ── Apex decision audit — per-candidate records ───────────────────────
    try:
        _audit_cycle_id = apex_input.get("scan_ts") or decision.get("scan_ts")
        _audit_now_ts   = datetime.now(UTC).isoformat()
        _non_avoid = [
            e for e in (decision.get("new_entries") or [])
            if (e.get("trade_type") or "").upper() != "AVOID" and e.get("symbol")
        ]
        _avoid_syms = {
            e.get("symbol")
            for e in (decision.get("new_entries") or [])
            if (e.get("trade_type") or "").upper() == "AVOID" and e.get("symbol")
        }
        _selected_map: dict[str, dict] = {}
        for _rank, _entry in enumerate(_non_avoid, start=1):
            _s = _entry.get("symbol")
            if _s:
                _selected_map[_s] = {"rank": _rank, "rationale": _entry.get("rationale")}

        # Build per-symbol formatted prompt lines for apex_prompt_line field.
        try:
            from market_intelligence import _format_candidate_line as _fmt_cand_line
            _prompt_line_map: dict[str, str] = {
                c["symbol"]: _fmt_cand_line(c)
                for c in (apex_input.get("track_a") or {}).get("candidates") or []
                if c.get("symbol")
            }
        except Exception as _pline_exc:
            log.debug("apex_orchestrator: prompt_line_map build failed — %s", _pline_exc)
            _prompt_line_map = {}

        for _cand in (apex_input.get("track_a") or {}).get("candidates") or []:
            _s = _cand.get("symbol")
            if not _s:
                continue
            if _s in _selected_map:
                _apex_dec, _apex_rank, _apex_rsn = (
                    "selected", _selected_map[_s]["rank"], _selected_map[_s]["rationale"]
                )
            elif _s in _avoid_syms:
                _apex_dec, _apex_rank, _apex_rsn = (
                    "avoid", None,
                    next((e.get("rationale") for e in (decision.get("new_entries") or [])
                          if e.get("symbol") == _s), None)
                )
            else:
                _apex_dec, _apex_rank, _apex_rsn = "not_selected_or_not_returned", None, None

            _cand_tier = _cand.get("scanner_tier")
            _cand_origin = (
                _cand.get("origin_path")
                or (_cand.get("origin"))
                or ("tier_d_main_path" if _cand_tier == "D" else "normal_path")
            )
            _write_apex_audit({
                "ts":                        _audit_now_ts,
                "record_type":               "apex_candidate",
                "cycle_id":                  _audit_cycle_id,
                "symbol":                    _s,
                "scanner_tier":              _cand_tier,
                "origin_path":               _cand_origin,
                "pru":                       _cand.get("position_research_universe_member"),
                "selected_band":             _cand.get("selected_band"),
                "selected_slot":             _cand.get("selected_slot"),
                "raw_score":                 _cand.get("score"),
                "adjusted_discovery_score":  _cand.get("adjusted_discovery_score"),
                "primary_archetype":         _cand.get("primary_archetype"),
                "universe_bucket":           _cand.get("universe_bucket"),
                "apex_cap_score":            _cand.get("apex_cap_score"),
                "apex_prompt_line":          _prompt_line_map.get(_s),
                "apex_decision":             _apex_dec,
                "apex_rank_if_available":    _apex_rank,
                "apex_reason_if_available":  _apex_rsn,
            })
    except Exception as _audit_exc:
        log.warning("apex_orchestrator: apex_candidate audit failed — %s", _audit_exc)

    # ── Apex decision audit — high_score_skip records ─────────────────────
    # Written for every not-selected candidate whose effective_score exceeds
    # at least one selected candidate's effective score.
    try:
        _REASON_CATEGORY_KEYWORDS: dict[str, list[str]] = {
            "portfolio_fit":     ["portfolio", "correlation", "overlap", "already hold", "diversif", "concentration"],
            "catalyst_quality":  ["catalyst", "news", "event", "headline", "earnings", "fundamental"],
            "weak_tape":         ["tape", "momentum", "dar", "pre-market", "pre-mkt", "intraday"],
            "volatility_risk":   ["volatil", "atr", "risk", "beta", "gap risk"],
            "sector_overlap":    ["sector", "semiconductor", "tech", "same sector"],
            "execution_quality": ["liquidity", "spread", "execution", "fill", "thin"],
        }

        def _classify_reason(reason_text: str) -> str:
            if not reason_text:
                return "no_reason_provided"
            _r = reason_text.lower()
            for _cat, _keywords in _REASON_CATEGORY_KEYWORDS.items():
                if any(_kw in _r for _kw in _keywords):
                    return _cat
            return "other"

        # Build lookup: skipped_symbol → Apex-provided explanation entry
        _apex_skip_map: dict[str, dict] = {}
        for _exp in (decision.get("higher_score_skips") or []):
            _ss = _exp.get("skipped_symbol")
            if _ss and _ss not in _apex_skip_map:
                _apex_skip_map[_ss] = _exp

        _sel_effective: dict[str, int] = {}
        for _cand in (apex_input.get("track_a") or {}).get("candidates") or []:
            _s = _cand.get("symbol")
            if not _s or _s not in _selected_map:
                continue
            _v = _cand.get("apex_cap_score")
            _sel_effective[_s] = int(round(_v)) if _v is not None else (_cand.get("score") or 0)

        for _cand in (apex_input.get("track_a") or {}).get("candidates") or []:
            _s = _cand.get("symbol")
            if not _s or _s in _selected_map:
                continue
            _v = _cand.get("apex_cap_score")
            _cand_eff = int(round(_v)) if _v is not None else (_cand.get("score") or 0)
            _lower = {sym: eff for sym, eff in _sel_effective.items() if eff < _cand_eff}
            if not _lower:
                continue

            _apex_exp = _apex_skip_map.get(_s)
            _apex_skip_reason = (_apex_exp.get("reason") if _apex_exp else None)
            _apex_mentioned = bool(_apex_exp) or any(
                _s in (info.get("rationale") or "")
                for info in _selected_map.values()
            )
            _lower_sym = max(_lower, key=_lower.get)  # highest-eff among the lower-selected group
            _lower_eff = _lower[_lower_sym]
            _score_gap = _cand_eff - _lower_eff

            # Prefer Apex-supplied selected_lower_symbol when available
            _sel_lower_sym = (_apex_exp.get("selected_lower_symbol") if _apex_exp else None) or _lower_sym
            _sel_lower_eff = (_apex_exp.get("selected_effective_score") if _apex_exp else None) or _lower_eff

            _cand_tier = _cand.get("scanner_tier")
            _cand_origin = (
                _cand.get("origin_path")
                or (_cand.get("origin"))
                or ("tier_d_main_path" if _cand_tier == "D" else "normal_path")
            )
            _write_apex_audit({
                "ts":                           _audit_now_ts,
                "record_type":                  "high_score_skip",
                "cycle_id":                     _audit_cycle_id,
                "symbol":                       _s,
                "effective_score":              _cand_eff,
                "raw_score":                    _cand.get("score"),
                "scanner_tier":                 _cand_tier,
                "origin_path":                  _cand_origin,
                "selected_band":                _cand.get("selected_band"),
                "selected_slot":                _cand.get("selected_slot"),
                "higher_than_selected_symbols": list(_lower.keys()),
                "highest_lower_selected_score": max(_lower.values()),
                "apex_mentioned":               _apex_mentioned,
                "apex_reason_if_any":           next(
                    (e.get("rationale") for e in (decision.get("new_entries") or [])
                     if e.get("symbol") == _s), None
                ),
                "apex_skip_reason":             _apex_skip_reason,
                "selected_lower_symbol":        _sel_lower_sym,
                "selected_lower_score":         _sel_lower_eff,
                "score_gap":                    _score_gap,
                "reason_category":              _classify_reason(_apex_skip_reason) if _apex_skip_reason else "no_reason_provided",
                "suspected_reason":             (
                    "apex_acknowledged_skip" if _apex_mentioned else "qualitative_preference"
                ),
                "existing_position":            _s in (active_trades or {}),
                "exposure_conflict":            None,
                "correlation_conflict":         None,
                "sector_conflict":              None,
                "option_ok":                    _cand.get("options_eligible"),
                "liquidity_ok":                 None,
            })
    except Exception as _hss_exc:
        log.warning("apex_orchestrator: high_score_skip audit failed — %s", _hss_exc)

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
        # Floor rule (CLAUDE.md): ≥3 candidates ≥35 with Apex returning zero entries
        # is a violation that must be visible as an ERROR, not a warning.
        _all_cands = (apex_input.get("track_a") or {}).get("candidates") or []
        _high_score_count = sum(1 for c in _all_cands if (c.get("score") or 0) >= 35)
        if _high_score_count >= 3:
            log.error(
                "apex: FLOOR_RULE_VIOLATION — %d candidates scored ≥35 but Apex returned "
                "zero entries (trigger=%s). market_read=%r",
                _high_score_count, _ttype, _mread,
            )
            try:
                from learning import _append_audit_event
                _append_audit_event(
                    "FLOOR_RULE_VIOLATION",
                    high_score_candidates=_high_score_count,
                    trigger_type=_ttype,
                    market_read=_mread,
                )
            except Exception as _ae:
                log.debug("apex: floor rule audit write failed — %s", _ae)

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
        # ── Apex decision audit — aggregate record ────────────────────────
        try:
            _agg_cands    = (apex_input.get("track_a") or {}).get("candidates") or []
            _agg_entries  = [
                e for e in (decision.get("new_entries") or [])
                if (e.get("trade_type") or "").upper() != "AVOID" and e.get("symbol")
            ]
            _agg_td_cands = [c for c in _agg_cands if c.get("scanner_tier") == "D"]
            _agg_dr       = dispatch_report.get("new_entries") or []
            _agg_executed = [r for r in _agg_dr if r.get("executed")]
            _agg_blocked  = [r for r in _agg_dr if not r.get("executed")]

            def _is_td(sym: str) -> bool:
                return (candidates_by_symbol.get(sym) or {}).get("scanner_tier") == "D"

            _write_apex_audit({
                "ts":                              datetime.now(UTC).isoformat(),
                "record_type":                     "aggregate",
                "cycle_id":                        apex_input.get("scan_ts") or decision.get("scan_ts"),
                "total_candidates_sent_to_apex":   len(_agg_cands),
                "tier_d_candidates_sent_to_apex":  len(_agg_td_cands),
                "normal_candidates_sent_to_apex":  len(_agg_cands) - len(_agg_td_cands),
                "tier_d_recovered_sent_to_apex":   sum(
                    1 for c in _agg_td_cands
                    if c.get("selected_band") is not None
                    and (c.get("apex_cap_score") or 0) > (c.get("score") or 0)
                ),
                "apex_new_entries_count":          len(_agg_entries),
                "apex_new_entries_symbols":        [e.get("symbol") for e in _agg_entries],
                "tier_d_new_entries_count":        sum(1 for e in _agg_entries if _is_td(e.get("symbol", ""))),
                "normal_new_entries_count":        sum(1 for e in _agg_entries if not _is_td(e.get("symbol", ""))),
                "order_intent_count":              len(_agg_executed),
                "tier_d_order_intent_count":       sum(1 for r in _agg_executed if _is_td(r.get("symbol", ""))),
                "normal_order_intent_count":       sum(1 for r in _agg_executed if not _is_td(r.get("symbol", ""))),
                "blocked_count":                   len(_agg_blocked),
                "tier_d_blocked_count":            sum(1 for r in _agg_blocked if _is_td(r.get("symbol", ""))),
                "normal_blocked_count":            sum(1 for r in _agg_blocked if not _is_td(r.get("symbol", ""))),
            })
        except Exception as _agg_exc:
            log.warning("apex_orchestrator: aggregate audit failed — %s", _agg_exc)
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
