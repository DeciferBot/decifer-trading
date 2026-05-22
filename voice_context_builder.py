"""
voice_context_builder.py — Read-only context collection for voice queries.

All functions are safe to call from any thread. None raises — on any file
error the relevant slice returns an empty/default value. Context is bounded:
large files are truncated before return.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("decifer.voice.context")

_REPO = os.path.dirname(os.path.abspath(__file__))


def _p(*parts: str) -> str:
    return os.path.join(_REPO, *parts)


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _read_jsonl_tail(path: str, n: int = 30) -> list[dict]:
    """Read last n lines of a JSONL file without loading the whole file."""
    try:
        with open(path) as f:
            lines = f.readlines()
        result = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
    except Exception:
        return []


def _age_str(ts_str: Optional[str]) -> str:
    if not ts_str or ts_str in ("unknown", "None"):
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return "unknown"
        age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        if age_s < 60:
            return "just now"
        if age_s < 3600:
            return f"{int(age_s / 60)}m ago"
        if age_s < 86400:
            return f"{int(age_s / 3600)}h ago"
        return f"{int(age_s / 86400)}d ago"
    except Exception:
        return "unknown"


# ─── Context slice builders ───────────────────────────────────────────────────

def get_positions_context() -> dict:
    """Return current open positions keyed by symbol."""
    try:
        raw = _read_json(_p("data", "positions.json"), {})
        if not isinstance(raw, dict):
            return {}
        result = {}
        for sym, pos in raw.items():
            entry = pos.get("entry") or 0
            current = pos.get("current") or 0
            pnl_pct = round((current - entry) / entry * 100, 2) if entry else 0
            result[sym.upper()] = {
                "symbol": pos.get("symbol", sym),
                "direction": pos.get("direction", "LONG"),
                "qty": pos.get("qty", 0),
                "entry": entry,
                "current": current,
                "pnl": pos.get("pnl", 0),
                "pnl_pct": pnl_pct,
                "conviction": pos.get("conviction"),
                "entry_regime": pos.get("entry_regime"),
                "trade_type": pos.get("trade_type"),
                "entry_thesis": (pos.get("entry_thesis") or "")[:400],
                "reasoning": (pos.get("reasoning") or "")[:300],
                "setup_type": pos.get("setup_type"),
                "score": pos.get("score") or pos.get("entry_score"),
                "open_time": pos.get("open_time"),
                "sl": pos.get("sl"),
                "tp": pos.get("tp"),
                "status": pos.get("status", "OPEN"),
                "high_water_mark": pos.get("high_water_mark"),
                "signal_scores": pos.get("signal_scores") or {},
            }
        return result
    except Exception as e:
        log.debug("get_positions_context error: %s", e)
        return {}


def get_recent_trades_context(n: int = 10) -> list[dict]:
    """Return last n closed trades from trades.json, most recent first."""
    try:
        raw = _read_json(_p("data", "trades.json"), [])
        if not isinstance(raw, list):
            return []
        closed = [t for t in raw if t.get("exit_price") or t.get("action") == "CLOSE"]
        closed.sort(
            key=lambda t: t.get("exit_time") or t.get("timestamp") or "",
            reverse=True,
        )
        result = []
        for t in closed[:n]:
            result.append({
                "symbol": t.get("symbol"),
                "direction": t.get("direction"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "pnl": t.get("pnl"),
                "pnl_pct": t.get("pnl_pct"),
                "exit_reason": t.get("exit_reason"),
                "exit_time": t.get("exit_time") or t.get("timestamp"),
                "hold_minutes": t.get("hold_minutes"),
                "reasoning": (t.get("reasoning") or "")[:300],
                "entry_thesis": (t.get("entry_thesis") or "")[:300],
                "regime": t.get("regime"),
                "score": t.get("score") or t.get("entry_score"),
                "trade_type": t.get("trade_type"),
                "setup_type": t.get("setup_type"),
            })
        return result
    except Exception as e:
        log.debug("get_recent_trades_context error: %s", e)
        return []


def get_pm_decisions_context(n: int = 20) -> list[dict]:
    """Return last n PM engine decisions."""
    try:
        raw = _read_jsonl_tail(_p("data", "pm_engine", "decisions.jsonl"), n)
        result = []
        for d in raw:
            result.append({
                "ts": d.get("ts"),
                "symbol": d.get("symbol"),
                "action_type": d.get("action_type"),
                "thesis_status": d.get("thesis_status"),
                "rationale": (d.get("rationale") or "")[:200],
                "score_delta": d.get("score_delta"),
                "unrealised_pnl_pct": d.get("unrealised_pnl_pct"),
                "final_status": d.get("final_status"),
                "safety_blocked": d.get("safety_blocked"),
                "safety_block_reason": d.get("safety_block_reason"),
            })
        return result
    except Exception as e:
        log.debug("get_pm_decisions_context error: %s", e)
        return []


def get_apex_candidate_decisions(n: int = 60) -> list[dict]:
    """
    Return last n per-symbol Apex candidate decisions from apex_decision_audit.jsonl.
    record_type == "apex_candidate" contains per-symbol apex_decision + apex_reason.
    """
    try:
        raw = _read_jsonl_tail(_p("data", "apex_decision_audit.jsonl"), 200)
        candidates = [r for r in raw if r.get("record_type") == "apex_candidate"]
        result = []
        for r in candidates[-n:]:
            result.append({
                "ts": r.get("ts"),
                "symbol": r.get("symbol"),
                "apex_decision": r.get("apex_decision"),
                "apex_reason": (r.get("apex_reason_if_available") or "")[:300],
                "raw_score": r.get("raw_score"),
                "origin_path": r.get("origin_path"),
            })
        return result
    except Exception as e:
        log.debug("get_apex_candidate_decisions error: %s", e)
        return []


def get_apex_aggregate_context(n: int = 5) -> list[dict]:
    """Return last n Apex aggregate (cycle-level) summaries."""
    try:
        raw = _read_jsonl_tail(_p("data", "apex_decision_audit.jsonl"), 200)
        aggregates = [r for r in raw if r.get("record_type") == "aggregate"]
        result = []
        for r in aggregates[-n:]:
            result.append({
                "ts": r.get("ts"),
                "cycle_id": r.get("cycle_id"),
                "total_candidates": r.get("total_candidates_sent_to_apex", 0),
                "new_entries_count": r.get("apex_new_entries_count", 0),
                "new_entries_symbols": r.get("apex_new_entries_symbols", []),
                "blocked_count": r.get("blocked_count", 0),
                "order_intent_count": r.get("order_intent_count", 0),
            })
        return result
    except Exception as e:
        log.debug("get_apex_aggregate_context error: %s", e)
        return []


def get_live_universe_context() -> dict:
    """Return summary of the active opportunity universe."""
    try:
        data = _read_json(_p("data", "live", "active_opportunity_universe.json"), {})
        manifest = _read_json(_p("data", "live", "current_manifest.json"), {})

        generated_at = data.get("generated_at", "")
        stale = False
        stale_hours = None
        try:
            gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600
            stale = age_h > 4
            stale_hours = round(age_h, 1)
        except Exception:
            pass

        summary = data.get("universe_summary", {})

        # Collect top position candidates
        top_candidates: list[dict] = []
        for bucket_key in ("candidates", "position_candidates", "swing_candidates"):
            bucket = data.get(bucket_key, [])
            if isinstance(bucket, list):
                for c in bucket[:8]:
                    top_candidates.append({
                        "symbol": c.get("symbol"),
                        "reason": (c.get("reason_to_care") or c.get("reason") or "")[:100],
                        "route": c.get("route"),
                        "confidence": c.get("confidence"),
                    })
                if top_candidates:
                    break

        return {
            "generated_at": generated_at,
            "age_str": _age_str(generated_at),
            "stale": stale,
            "stale_hours": stale_hours,
            "total_candidates": summary.get("total_candidates", 0),
            "position_candidates": summary.get("position_candidates", 0),
            "swing_candidates": summary.get("swing_candidates", 0),
            "watchlist_candidates": summary.get("watchlist_candidates", 0),
            "handoff_enabled": manifest.get("handoff_enabled", False),
            "validation_status": manifest.get("validation_status"),
            "top_candidates": top_candidates,
        }
    except Exception as e:
        log.debug("get_live_universe_context error: %s", e)
        return {}


def get_driver_state_context() -> dict:
    """Return live market driver state from intelligence pipeline."""
    try:
        data = _read_json(_p("data", "intelligence", "live_driver_state.json"), {})
        return {
            "generated_at": data.get("generated_at"),
            "age_str": _age_str(data.get("generated_at")),
            "active_drivers": data.get("active_drivers", []),
            "blocked_conditions": data.get("blocked_conditions", []),
            "mode": data.get("mode"),
        }
    except Exception as e:
        log.debug("get_driver_state_context error: %s", e)
        return {}


def get_theme_context() -> dict:
    """Return active/dormant/crowded theme lists from theme_activation.json."""
    try:
        data = _read_json(_p("data", "intelligence", "theme_activation.json"), {})
        themes_raw = data.get("themes", {})

        activated, crowded, dormant = [], [], []

        # themes is a list of dicts or a dict of dicts depending on version
        if isinstance(themes_raw, list):
            items = themes_raw
        elif isinstance(themes_raw, dict):
            items = list(themes_raw.values())
        else:
            items = []

        for t in items:
            state = t.get("state", "")
            tid = t.get("theme_id") or t.get("id") or ""
            if state == "activated":
                activated.append(tid)
            elif state == "crowded":
                crowded.append(tid)
            elif state == "dormant":
                dormant.append(tid)

        return {
            "generated_at": data.get("generated_at"),
            "age_str": _age_str(data.get("generated_at")),
            "mode": data.get("mode"),
            "activated": activated,
            "crowded": crowded,
            "dormant": dormant,
        }
    except Exception as e:
        log.debug("get_theme_context error: %s", e)
        return {}


def get_recent_signals_context(n: int = 10) -> list[dict]:
    """Return top-n highest-scored signals from the most recent signal log entries."""
    try:
        raw = _read_jsonl_tail(_p("data", "signals_log.jsonl"), 200)
        raw.sort(key=lambda x: x.get("score", 0), reverse=True)
        result = []
        for e in raw[:n]:
            result.append({
                "symbol": e.get("symbol"),
                "score": e.get("score"),
                "regime": e.get("regime"),
                "ts": e.get("ts"),
                "price": e.get("price"),
            })
        return result
    except Exception as e:
        log.debug("get_recent_signals_context error: %s", e)
        return []


def get_training_summary_context() -> dict:
    """Return summary stats from recent training records."""
    try:
        records = _read_jsonl_tail(_p("data", "training_records.jsonl"), 50)
        if not records:
            return {"count": 0}

        eligible = [r for r in records if r.get("ml_eligible")]
        pnls = [r["pnl"] for r in records if isinstance(r.get("pnl"), (int, float))]
        winners = sum(1 for p in pnls if p > 0)
        losers = sum(1 for p in pnls if p < 0)
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0

        exit_reasons: dict[str, int] = {}
        for r in records:
            reason = r.get("exit_reason", "unknown")
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        return {
            "recent_record_count": len(records),
            "ml_eligible_count": len(eligible),
            "winners": winners,
            "losers": losers,
            "avg_pnl_recent": round(avg_pnl, 2),
            "exit_reasons": exit_reasons,
        }
    except Exception as e:
        log.debug("get_training_summary_context error: %s", e)
        return {}


def build_full_context(dash: dict) -> dict:
    """
    Assemble all context slices into one dict. Called once per voice query.
    Never raises. If a slice fails it is omitted or returns its default.
    """
    return {
        "dash": {
            "portfolio_value": dash.get("portfolio_value", 0),
            "daily_pnl": dash.get("daily_pnl", 0),
            "session": dash.get("session", "UNKNOWN"),
            "regime": dash.get("regime", {}),
            "scanning": dash.get("scanning", False),
            "paused": dash.get("paused", False),
            "killed": dash.get("killed", False),
            "scan_count": dash.get("scan_count", 0),
            "last_scan": str(dash.get("last_scan") or "unknown"),
            "ibkr_disconnected": dash.get("ibkr_disconnected", False),
            "claude_analysis": (dash.get("claude_analysis") or "")[:600],
            "apex_errors_1h": dash.get("apex_errors_1h", 0),
        },
        "positions": get_positions_context(),
        "recent_trades": get_recent_trades_context(10),
        "pm_decisions": get_pm_decisions_context(20),
        "apex_candidates": get_apex_candidate_decisions(80),
        "apex_aggregates": get_apex_aggregate_context(5),
        "live_universe": get_live_universe_context(),
        "driver_state": get_driver_state_context(),
        "themes": get_theme_context(),
        "recent_signals": get_recent_signals_context(10),
        "training_summary": get_training_summary_context(),
    }
