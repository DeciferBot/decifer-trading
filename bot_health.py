"""
bot_health.py — 7-stage pipeline health report for /api/health endpoint.

Pipeline funnel:
  Stage 1: Intelligence Pipeline  (daily pre-market → intelligence/*.json)
  Stage 2: Universe Builders      (launchd weekly/daily → universe files)
  Stage 3: Handoff Publisher      (daily pre-market → live/ session-valid to 22:00 UTC)
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
    """Stage 1 — Intelligence Pipeline (pre-market daily refresh, session-valid all day)."""
    SLA = 8 * 3600  # once-daily run; alert only if overdue, not after 1h
    artifacts = [
        _file_age("intelligence/live_driver_state.json",      SLA, "Market Map (live drivers)"),
        _file_age("intelligence/economic_candidate_feed.json", SLA, "Economic candidate feed"),
        _file_age("intelligence/theme_activation.json",        SLA, "Theme activation"),
    ]

    market_map: dict = {}
    candidate_count: int | None = None
    themes: dict = {}

    try:
        with open(os.path.join(_DATA_DIR, "intelligence/live_driver_state.json")) as f:
            ld = json.load(f)
        market_map = {
            "active_drivers": ld.get("active_drivers", []),
            "blocked_conditions": ld.get("blocked_conditions", []),
            "mode": ld.get("mode", ""),
            "evidence": ld.get("evidence", {}),
        }
    except Exception:
        pass

    try:
        with open(os.path.join(_DATA_DIR, "intelligence/economic_candidate_feed.json")) as f:
            cf = json.load(f)
        candidate_count = len(cf.get("candidates", []))
    except Exception:
        pass

    try:
        with open(os.path.join(_DATA_DIR, "intelligence/theme_activation.json")) as f:
            ta = json.load(f)
        summary = ta.get("activation_summary", {})
        themes = {
            "activated": summary.get("activated", 0),
            "total_themes": summary.get("total_themes", 0),
            "dormant": summary.get("dormant", 0),
        }
    except Exception:
        pass

    return {
        "name": "Intelligence",
        "label": "Stage 1 — Intelligence Pipeline",
        "description": "run_intelligence_pipeline.py — market data → drivers → candidates → themes",
        "status": _agg_status(artifacts),
        "critical": False,
        "artifacts": artifacts,
        "market_map": market_map,
        "candidate_count": candidate_count,
        "themes": themes,
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

    # Content validation — file age proves the job ran; symbol count proves it produced real data.
    universe_symbol_count: int | None = None
    universe_refreshed_at: str | None = None
    try:
        with open(os.path.join(_DATA_DIR, "committed_universe.json")) as f:
            _u = json.load(f)
        universe_symbol_count = _u.get("count") or len(_u.get("symbols", []))
        universe_refreshed_at = _u.get("refreshed_at")
    except Exception:
        pass

    # Downgrade status if the file is fresh but the universe is suspiciously small.
    agg = _agg_status(artifacts)
    if universe_symbol_count is not None and universe_symbol_count < 100 and agg == "ok":
        agg = "warn"

    return {
        "name": "Universe",
        "label": "Stage 2 — Universe Builders",
        "description": "launchd: universe-committed (weekly)  +  universe-promoter (daily 2×)",
        "status": agg,
        "critical": False,
        "artifacts": artifacts,
        "heartbeats": heartbeats,
        "universe_symbol_count": universe_symbol_count,
        "universe_refreshed_at": universe_refreshed_at,
    }


def _stage_handoff() -> dict:
    """Stage 3 — Handoff Publisher (written once at pre-market, session-valid to 22:00 UTC)."""
    SLA = 8 * 3600  # manifest expires_at = 22:00 UTC; alert only if overdue
    artifacts = [
        _file_age("live/active_opportunity_universe.json", SLA, "Active opportunity universe"),
        _file_age("live/current_manifest.json",            SLA, "Handoff manifest"),
    ]
    handoff_enabled = bool(CONFIG.get("enable_active_opportunity_universe_handoff", False))

    universe_summary: dict = {}
    handoff_candidate_count: int | None = None
    try:
        with open(os.path.join(_DATA_DIR, "live/active_opportunity_universe.json")) as f:
            uu = json.load(f)
        universe_summary = uu.get("universe_summary", {})
        # Content proof: a fresh file with zero candidates is a silent failure.
        handoff_candidate_count = universe_summary.get("total_candidates")
    except Exception:
        pass

    raw_status = _agg_status(artifacts)
    # Downgrade if file is fresh but contains no candidates (silent pipeline failure).
    if (handoff_candidate_count is not None and handoff_candidate_count == 0
            and raw_status == "ok" and handoff_enabled):
        raw_status = "warn"
    return {
        "name": "Handoff",
        "label": "Stage 3 — Handoff Publisher",
        "description": "run_intelligence_pipeline.py — universe_builder → live handoff files",
        "status": raw_status if handoff_enabled else "ok",
        "handoff_enabled": handoff_enabled,
        "critical": False,
        "artifacts": artifacts,
        "universe_summary": universe_summary,
        "handoff_candidate_count": handoff_candidate_count,
    }


def _stage_bot_core(dash: dict) -> dict:
    """Stage 4 — Bot Core: IBKR + Alpaca + process liveness. CRITICAL."""
    import bot_state as _bs

    ibkr_connected = not dash.get("ibkr_disconnected", False)
    bot_status = dash.get("status", "unknown")
    paused = dash.get("paused", False)
    killed = dash.get("killed", False)

    alpaca_running = False
    alpaca_last_bar_age_s: float | None = None
    alpaca_stream_age_s: float | None = None
    # Primary: _running flag + _last_bar_received_at (detects silent WebSocket drops).
    # Override: if QUOTE_CACHE for an anchor symbol (SPY/QQQ) has a quote within 30 s,
    # treat the stream as running regardless of the _running flag.  The flag is
    # transiently False during update_symbols() stop/start cycles and produces false
    # WARNs even when data is actively flowing.
    _ALPACA_FRESHNESS_S = 30
    _STREAM_ANCHORS = ("SPY", "QQQ")
    try:
        if _bs._bar_stream is not None:
            alpaca_running = bool(getattr(_bs._bar_stream, "_running", False))
            _lbr = getattr(_bs._bar_stream, "_last_bar_received_at", None)
            if _lbr is not None:
                alpaca_last_bar_age_s = round(time.time() - _lbr, 1)
    except Exception:
        pass
    try:
        from alpaca_stream import QUOTE_CACHE
        _now = time.time()
        for _anchor in _STREAM_ANCHORS:
            _q = QUOTE_CACHE.get(_anchor)
            if _q and _q.get("ts"):
                _age = _now - _q["ts"]
                if alpaca_stream_age_s is None or _age < alpaca_stream_age_s:
                    alpaca_stream_age_s = round(_age, 1)
        if alpaca_stream_age_s is not None and alpaca_stream_age_s <= _ALPACA_FRESHNESS_S:
            alpaca_running = True  # data is flowing — override transient _running=False
    except Exception:
        pass

    account_value_age_s: float | None = None
    if _bs.account_values_updated_at is not None:
        account_value_age_s = round(time.time() - _bs.account_values_updated_at, 1)
    account_stale = account_value_age_s is not None and account_value_age_s > 300

    # Alpaca stream is "stale" if _running=True but no bar received in >10 min —
    # this catches silent WebSocket drops that don't raise an exception.
    alpaca_data_stale = (
        alpaca_running
        and alpaca_last_bar_age_s is not None
        and alpaca_last_bar_age_s > 600
    )

    if killed or not ibkr_connected:
        status = "stale"
    elif account_stale:
        status = "warn"
    elif paused or not alpaca_running or alpaca_data_stale:
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
        "alpaca_last_bar_age_s": alpaca_last_bar_age_s,
        "alpaca_stream_age_s": alpaca_stream_age_s,
        "alpaca_data_stale": alpaca_data_stale,
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


def _parse_last_scan_age(last_scan: str) -> float | None:
    """Parse last_scan to age in seconds. Handles both ISO and bare HH:MM:SS formats."""
    if not last_scan:
        return None
    s = str(last_scan)
    # Try full ISO format first
    try:
        _ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return round((datetime.now(UTC) - _ts).total_seconds(), 1)
    except Exception:
        pass
    # bot_trading.py stores last_scan as bare "HH:MM:SS" in ET timezone
    try:
        import zoneinfo as _zi
        _ET = _zi.ZoneInfo("America/New_York")
        _now_et = datetime.now(_ET)
        _h, _m, _sec = (int(x) for x in s.split(":"))
        _ts = datetime(_now_et.year, _now_et.month, _now_et.day, _h, _m, _sec, tzinfo=_ET)
        age = round((_now_et - _ts).total_seconds(), 1)
        # Handle midnight wrap (scan was just before midnight, now just after)
        if age < 0:
            age += 86400
        return age
    except Exception:
        return None


def _stage_scan_engine(dash: dict) -> dict:
    """Stage 5 — Scan Engine: signal scoring + Apex synthesizer. CRITICAL."""
    last_scan = dash.get("last_scan")
    last_scan_age_s = _parse_last_scan_age(last_scan)

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
                    age_s = (now_utc - _ts).total_seconds()
                    # >5 min = stuck (not just slow fill); <7 days = recent enough to be live.
                    # Older records are historical artifacts from pre-migration order cycles —
                    # they have no matching ORDER_FILLED but are not active at-risk orders.
                    if 300 < age_s < 7 * 86_400:
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

    has_issues = bool(bracket_gaps) or stuck_count > 0 or fail_sentinels > 0
    status = "warn" if has_issues else "ok"
    disk_low = disk_free_pct is not None and disk_free_pct < 10

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
        "disk_low": disk_low,
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
        # Include raw_ic per-dimension so the dashboard can show which signals are
        # carrying weight and which are dragging the mean down — a single aggregate
        # score is uninterpretable without knowing which dimensions are failing.
        ic_health = {
            "quality": _ih.quality,
            "mean_positive_ic": _ih.mean_positive_ic,
            "n_positive_dims": _ih.n_positive_dims,
            "n_records": _ih.n_records,
            "raw_ic": _ih.raw_ic,
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
    if len(warned) > 1:
        return {"verdict": "WARN", "reason": f"Warnings in: {', '.join(warned)}"}
    if len(warned) == 1:
        return {"verdict": "TRADING", "reason": "All critical systems healthy", "note": f"1 advisory: {warned[0]}"}

    return {"verdict": "TRADING", "reason": "All pipeline stages healthy"}


# ── Data connections ──────────────────────────────────────────────────────────

def _data_connections() -> list[dict]:
    """Check configured status of each data source. Never makes live API calls."""
    sources: list[dict] = []

    # IBKR — use bot_state disconnect flag
    try:
        import bot_state as _bs
        ibkr_ok = not _bs.dash.get("ibkr_disconnected", True)
        sources.append({
            "name": "IBKR",
            "status": "ok" if ibkr_ok else "error",
            "detail": "TWS connected" if ibkr_ok else "TWS disconnected",
        })
    except Exception:
        sources.append({"name": "IBKR", "status": "unknown", "detail": "bot not running"})

    # Alpaca — key configured + stream state
    try:
        alpaca_key = CONFIG.get("alpaca_api_key", "")
        alpaca_secret = CONFIG.get("alpaca_secret_key", "")
        key_ok = bool(alpaca_key and alpaca_secret)
        stream_running = False
        try:
            import bot_state as _bs2
            if _bs2._bar_stream is not None:
                stream_running = bool(getattr(_bs2._bar_stream, "_running", False))
        except Exception:
            pass
        if not key_ok:
            status, detail = "error", "no API key"
        elif not stream_running:
            status, detail = "warn", "key OK — stream down"
        else:
            status, detail = "ok", "streaming"
        sources.append({"name": "Alpaca", "status": status, "detail": detail})
    except Exception:
        sources.append({"name": "Alpaca", "status": "unknown", "detail": "check failed"})

    # FMP
    try:
        from fmp_client import is_available as _fmp_ok
        ok = _fmp_ok()
        sources.append({
            "name": "FMP",
            "status": "ok" if ok else "error",
            "detail": "API key configured" if ok else "FMP_API_KEY not set",
        })
    except Exception:
        sources.append({"name": "FMP", "status": "unknown", "detail": "check failed"})

    # Alpha Vantage
    try:
        import alpha_vantage_client as _av
        keys = _av._api_keys()
        key_ok = bool(keys)
        calls_today = _av.get_calls_today() if key_ok else 0
        limit = CONFIG.get("alpha_vantage_daily_limit", 25) * max(len(keys), 1)
        exhausted = key_ok and calls_today >= limit
        if not key_ok:
            status, detail = "error", "no API key"
        elif exhausted:
            status, detail = "warn", f"budget exhausted ({calls_today}/{limit} calls)"
        else:
            status, detail = "ok", f"{calls_today}/{limit} calls today"
        sources.append({"name": "Alpha Vantage", "status": status, "detail": detail})
    except Exception:
        sources.append({"name": "Alpha Vantage", "status": "unknown", "detail": "check failed"})

    return sources


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

    connections = []
    try:
        connections = _data_connections()
    except Exception:
        pass

    return {"ts": ts, "stages": stages, "verdict": _trading_verdict(stages), "connections": connections}
