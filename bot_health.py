"""
bot_health.py — 7-stage pipeline health report for /api/health endpoint.

Pipeline funnel:
  Stage 1: Intelligence Pipeline  (manual → intelligence/*.json)
  Stage 2: Universe Builders      (launchd weekly/daily → universe files)
  Stage 3: Handoff Publisher      (launchd 10min → live/ + heartbeats/)
  Stage 4: Bot Core               (always-on → IBKR + Alpaca + process)
  Stage 5: Scan Engine            (scan cycle → Apex + signal funnel)
  Stage 6: Execution              (positions + bracket integrity + disk)
  Stage 7: IC & Validation        (ic_weights + quality + gates)

Stages 4 and 5 are critical — if they fail, verdict = NOT TRADING.
Stages 1-3 stale → DEGRADED (data quality issue, bot still runs).

Always returns a complete JSON structure. Never raises.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime

from config import CONFIG

_DATA_DIR = CONFIG.get("data_dir", "data")
if not os.path.isabs(_DATA_DIR):
    _DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), _DATA_DIR)

_LATENCY_RING_SIZE = 20
_FUNNEL_LOG = os.path.join(_DATA_DIR, "tier_d_funnel.jsonl")


def _fmt_age(s: float) -> str:
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s / 60)}m"
    if s < 86400:
        return f"{int(s / 3600)}h"
    return f"{int(s / 86400)}d"


def _file_age(rel_path: str, sla_s: float, label: str) -> dict:
    """Check file age vs SLA. Returns status: ok | warn | stale | missing."""
    path = os.path.join(_DATA_DIR, rel_path) if not os.path.isabs(rel_path) else rel_path
    if not os.path.exists(path):
        return {"label": label, "present": False, "age_s": None, "age_human": "absent",
                "sla_s": sla_s, "sla_human": _fmt_age(sla_s), "status": "missing"}
    age_s = time.time() - os.path.getmtime(path)
    if age_s <= sla_s:
        status = "ok"
    elif age_s <= sla_s * 1.5:
        status = "warn"
    else:
        status = "stale"
    return {"label": label, "present": True, "age_s": round(age_s, 0),
            "age_human": _fmt_age(age_s), "sla_s": sla_s, "sla_human": _fmt_age(sla_s),
            "status": status}


def _agg_status(artifacts: list[dict]) -> str:
    statuses = [a.get("status", "missing") for a in artifacts]
    if any(s == "stale" for s in statuses):
        return "stale"
    if any(s == "missing" for s in statuses):
        return "missing"
    if any(s == "warn" for s in statuses):
        return "warn"
    return "ok"


# ── Stage builders ────────────────────────────────────────────────────────────

def _stage_intelligence() -> dict:
    """Stage 1 — Intelligence Pipeline (manual-only refresh)."""
    SLA = 86_400  # 24h
    artifacts = [
        _file_age("intelligence/daily_economic_state.json",     SLA, "Economic state"),
        _file_age("intelligence/current_economic_context.json", SLA, "Economic context"),
        _file_age("intelligence/theme_activation.json",         SLA, "Theme activation"),
        _file_age("intelligence/thesis_store.json",             SLA, "Thesis store"),
    ]
    return {
        "name": "Intelligence",
        "label": "Stage 1 — Intelligence Pipeline",
        "description": "Manual: run_intelligence_pipeline.py  ·  no scheduler",
        "status": _agg_status(artifacts),
        "critical": False,
        "artifacts": artifacts,
    }


def _stage_universe() -> dict:
    """Stage 2 — Universe Builders (launchd weekly + daily)."""
    artifacts = [
        _file_age("committed_universe.json",         7 * 86_400,  "Committed universe"),
        _file_age("daily_promoted.json",             18 * 3600,   "Daily promoted"),
        _file_age("position_research_universe.json", 8 * 86_400,  "Position research universe"),
    ]
    heartbeats = [
        _file_age("heartbeats/universe_committed_worker.json", 7 * 86_400, "Committed worker heartbeat"),
        _file_age("heartbeats/universe_promoter_worker.json",  18 * 3600,  "Promoter worker heartbeat"),
    ]
    return {
        "name": "Universe",
        "label": "Stage 2 — Universe Builders",
        "description": "launchd: universe-committed (weekly)  +  universe-promoter (daily 2×)",
        "status": _agg_status(artifacts),
        "critical": False,
        "artifacts": artifacts,
        "heartbeats": heartbeats,
    }


def _stage_handoff() -> dict:
    """Stage 3 — Handoff Publisher (launchd every 10 min)."""
    artifacts = [
        _file_age("heartbeats/handoff_publisher.json",     10 * 60, "Publisher heartbeat"),
        _file_age("live/active_opportunity_universe.json", 15 * 60, "Active opportunity universe"),
        _file_age("live/current_manifest.json",            15 * 60, "Current manifest"),
    ]
    handoff_enabled = bool(CONFIG.get("enable_active_opportunity_universe_handoff", False))
    raw_status = _agg_status(artifacts)
    return {
        "name": "Handoff",
        "label": "Stage 3 — Handoff Publisher",
        "description": "launchd: handoff-publisher  ·  every 10 min",
        "status": raw_status if handoff_enabled else "ok",
        "handoff_enabled": handoff_enabled,
        "critical": False,
        "artifacts": artifacts,
    }


def _stage_bot_core(dash: dict) -> dict:
    """Stage 4 — Bot Core: IBKR + Alpaca + process liveness. CRITICAL."""
    import bot_state as _bs

    ibkr_connected = not dash.get("ibkr_disconnected", False)
    bot_status = dash.get("status", "unknown")
    paused = dash.get("paused", False)
    killed = dash.get("killed", False)

    alpaca_running = False
    try:
        if _bs._bar_stream is not None:
            alpaca_running = bool(getattr(_bs._bar_stream, "_running", False))
    except Exception:
        pass

    account_value_age_s: float | None = None
    if _bs.account_values_updated_at is not None:
        account_value_age_s = round(time.time() - _bs.account_values_updated_at, 1)
    account_stale = account_value_age_s is not None and account_value_age_s > 300

    if killed or not ibkr_connected:
        status = "stale"
    elif account_stale:
        status = "warn"
    elif paused or not alpaca_running:
        status = "warn"
    else:
        status = "ok"

    return {
        "name": "Bot Core",
        "label": "Stage 4 — Bot Core",
        "description": "bot.py: IBKR connection  +  Alpaca stream  +  process health",
        "status": status,
        "critical": True,
        "ibkr_connected": ibkr_connected,
        "alpaca_running": alpaca_running,
        "bot_status": bot_status,
        "paused": paused,
        "killed": killed,
        "account_value_age_s": account_value_age_s,
        "account_stale": account_stale,
        "hot_reload_count": dash.get("hot_reload_count", 0),
    }


def _read_funnel() -> dict:
    """Read last scan cycle attrition from tier_d_funnel.jsonl."""
    if not os.path.exists(_FUNNEL_LOG):
        return {"available": False}
    try:
        with open(_FUNNEL_LOG, errors="replace") as fh:
            fh.seek(0, 2)
            file_size = fh.tell()
            fh.seek(max(0, file_size - 32_768))
            lines = fh.readlines()
        last_pipeline: dict[int, dict] = {}
        last_dispatch: dict | None = None
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            stage = rec.get("stage")
            if stage == "pipeline" and isinstance(rec.get("step"), int):
                last_pipeline[rec["step"]] = rec
            elif stage == "dispatch":
                last_dispatch = rec
        if not last_pipeline:
            return {"available": False}
        universe_in = (last_pipeline.get(1) or {}).get("in")
        last_out = None
        for step in sorted(last_pipeline.keys(), reverse=True):
            v = (last_pipeline.get(step) or {}).get("out")
            if v is not None:
                last_out = v
                break
        return {
            "available": True,
            "universe_in": universe_in,
            "after_pipeline": last_out,
            "dispatched": (last_dispatch or {}).get("dispatched"),
            "filled": (last_dispatch or {}).get("filled"),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


def _stage_scan_engine(dash: dict) -> dict:
    """Stage 5 — Scan Engine: signal scoring + Apex synthesizer. CRITICAL."""
    last_scan = dash.get("last_scan")
    last_scan_age_s: float | None = None
    if last_scan:
        try:
            _ts = datetime.fromisoformat(str(last_scan).replace("Z", "+00:00"))
            last_scan_age_s = round((datetime.now(UTC) - _ts).total_seconds(), 1)
        except Exception:
            pass

    durations = dash.get("scan_durations") or []
    last_duration_s: float | None = None
    if durations:
        vals = [d.get("duration_s") for d in durations if d.get("duration_s") is not None]
        if vals:
            last_duration_s = round(vals[-1], 1)

    consecutive_zero = 0
    try:
        import bot_trading as _bt
        consecutive_zero = getattr(_bt, "_consecutive_zero_scored", 0)
    except Exception:
        pass

    latencies = dash.get("apex_call_latencies") or []
    errors_1h = dash.get("apex_errors_1h") or 0
    avg_latency_s: float | None = None
    if latencies:
        vals = [e.get("latency_s") for e in latencies if e.get("latency_s") is not None]
        if vals:
            avg_latency_s = round(sum(vals) / len(vals), 2)

    funnel = _read_funnel()

    if last_scan_age_s is None:
        status = "missing"
    elif last_scan_age_s > 900:  # 15 min
        status = "stale"
    elif last_scan_age_s > 600 or consecutive_zero >= 2 or errors_1h > 2:
        status = "warn"
    else:
        status = "ok"

    return {
        "name": "Scan Engine",
        "label": "Stage 5 — Scan Engine",
        "description": "bot_trading.py: signal scoring  +  apex_orchestrator.py: Apex synthesizer",
        "status": status,
        "critical": True,
        "scan_count": dash.get("scan_count", 0),
        "last_scan_age_s": last_scan_age_s,
        "last_duration_s": last_duration_s,
        "consecutive_zero_scored": consecutive_zero,
        "apex_avg_latency_s": avg_latency_s,
        "apex_errors_1h": errors_1h,
        "funnel": funnel,
    }


def _stage_execution() -> dict:
    """Stage 6 — Execution: positions, bracket integrity, disk, fail sentinels."""
    bracket_gaps: list[str] = []
    stuck_count = 0
    position_count = 0

    try:
        from orders_state import active_trades, _trades_lock
        with _trades_lock:
            snapshot = dict(active_trades)
        position_count = len(snapshot)
        for symbol, trade in snapshot.items():
            if trade.get("status") in ("ACTIVE", "TRIMMING"):
                if not trade.get("sl_order_id") and not trade.get("tp_order_id"):
                    bracket_gaps.append(symbol)
    except Exception:
        pass

    try:
        from event_log import pending_orders
        now_utc = datetime.now(UTC)
        for order in (pending_orders() or []):
            ts_str = order.get("ts") or ""
            if ts_str:
                try:
                    _ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if (now_utc - _ts).total_seconds() > 300:
                        stuck_count += 1
                except Exception:
                    pass
    except Exception:
        pass

    disk_free_gb: float | None = None
    disk_free_pct: float | None = None
    try:
        _stat = os.statvfs(_DATA_DIR if os.path.isdir(_DATA_DIR) else ".")
        disk_free_gb = round(_stat.f_bavail * _stat.f_frsize / 1_073_741_824, 2)
        disk_total = _stat.f_blocks * _stat.f_frsize / 1_073_741_824
        if disk_total > 0:
            disk_free_pct = round(disk_free_gb / disk_total * 100, 1)
    except Exception:
        pass

    fail_sentinels = 0
    try:
        fail_sentinels = sum(1 for f in os.listdir(".") if f.startswith(".fail_"))
    except Exception:
        pass

    has_issues = bool(bracket_gaps) or stuck_count > 0 or fail_sentinels > 0 or (disk_free_pct is not None and disk_free_pct < 10)
    status = "warn" if has_issues else "ok"

    return {
        "name": "Execution",
        "label": "Stage 6 — Execution",
        "description": "orders_core.py: positions  +  bracket integrity  +  system resources",
        "status": status,
        "critical": False,
        "position_count": position_count,
        "bracket_gaps": bracket_gaps,
        "bracket_gap_count": len(bracket_gaps),
        "stuck_intent_count": stuck_count,
        "disk_free_gb": disk_free_gb,
        "disk_free_pct": disk_free_pct,
        "fail_sentinel_count": fail_sentinels,
    }


def _stage_validation() -> dict:
    """Stage 7 — IC & Validation: IC quality, readiness gates, phase status."""
    ic_weights_check = _file_age("ic_weights.json", 14 * 86_400, "IC weights")

    ic_health: dict = {}
    live_readiness: dict = {}
    phase: dict = {}

    try:
        from ic_validator import get_ic_health
        _ih = get_ic_health()
        ic_health = {
            "quality": _ih.quality,
            "mean_positive_ic": _ih.mean_positive_ic,
            "n_positive_dims": _ih.n_positive_dims,
            "n_records": _ih.n_records,
        }
    except Exception as e:
        ic_health = {"quality": "ERROR", "error": str(e)}

    try:
        from ic_validator import check_live_readiness
        _lr = check_live_readiness()
        live_readiness = {
            "sample_gate_passed": _lr.sample_gate_passed,
            "ic_gate_passed": _lr.ic_gate_passed,
            "sharpe_gate_passed": _lr.sharpe_gate_passed,
            "ready_for_live": _lr.ready_for_live,
            "failures": _lr.failures,
        }
    except Exception as e:
        live_readiness = {"error": str(e)}

    try:
        from phase_gate import get_status
        phase = get_status().as_dict()
    except Exception as e:
        phase = {"error": str(e)}

    q = ic_health.get("quality", "NO_SIGNAL")
    quality_ok = q in ("STRONG", "MODERATE")
    gates_ok = (live_readiness.get("sample_gate_passed") and
                live_readiness.get("ic_gate_passed") and
                live_readiness.get("sharpe_gate_passed"))

    if ic_weights_check["status"] in ("stale", "missing") or not quality_ok or not gates_ok:
        status = "warn"
    else:
        status = "ok"

    return {
        "name": "Validation",
        "label": "Stage 7 — IC & Validation",
        "description": "ic_validator.py: IC quality  +  phase gate status",
        "status": status,
        "critical": False,
        "ic_weights_check": ic_weights_check,
        "ic": ic_health,
        "gates": live_readiness,
        "phase": phase,
    }


# ── Verdict ───────────────────────────────────────────────────────────────────

def _trading_verdict(stages: list[dict]) -> dict:
    by_name = {s["name"]: s for s in stages}
    bc = by_name.get("Bot Core", {})
    se = by_name.get("Scan Engine", {})

    if bc.get("killed"):
        return {"verdict": "NOT TRADING", "reason": "Bot process has been killed"}
    if not bc.get("ibkr_connected", True):
        return {"verdict": "NOT TRADING", "reason": "IBKR disconnected"}
    if bc.get("status") in ("stale", "missing"):
        return {"verdict": "NOT TRADING", "reason": f"Bot Core failure ({bc.get('status')})"}
    if se.get("status") in ("stale", "missing"):
        return {"verdict": "NOT TRADING", "reason": f"Scan Engine failure ({se.get('status')})"}

    stale = [s["name"] for s in stages if s.get("status") in ("stale", "missing") and not s.get("critical")]
    if stale:
        return {"verdict": "DEGRADED", "reason": f"Stale pipeline data: {', '.join(stale)}"}

    warned = [s["name"] for s in stages if s.get("status") == "warn"]
    if warned:
        return {"verdict": "DEGRADED", "reason": f"Warnings in: {', '.join(warned)}"}

    return {"verdict": "TRADING", "reason": "All pipeline stages healthy"}


# ── Public API ────────────────────────────────────────────────────────────────

def build_health_report() -> dict:
    """
    Returns {"ts": ..., "stages": [...7 stages...], "verdict": {...}}.
    Always returns a complete structure. Never raises.
    """
    try:
        import bot_state as _bs
        _dash = _bs.dash
    except Exception:
        _dash = {}

    ts = datetime.now(UTC).isoformat()

    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            name = getattr(fn, "__name__", "unknown").replace("_stage_", "").title()
            return {"name": name, "label": name, "status": "missing",
                    "critical": False, "error": str(e), "artifacts": []}

    stages = [
        _safe(_stage_intelligence),
        _safe(_stage_universe),
        _safe(_stage_handoff),
        _safe(_stage_bot_core, _dash),
        _safe(_stage_scan_engine, _dash),
        _safe(_stage_execution),
        _safe(_stage_validation),
    ]

    return {"ts": ts, "stages": stages, "verdict": _trading_verdict(stages)}
