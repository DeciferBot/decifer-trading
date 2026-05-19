"""
bot_health.py — Read-only health report aggregator for the /api/health endpoint.

Single public function:
    build_health_report() -> dict

Reads from existing sources (bot_state.dash, event_log, ic_validator,
phase_gate, orders_state, disk stats).  Never submits orders, never mutates
positions, never raises — all exceptions are caught and surface as degraded
status fields so the dashboard always gets a complete JSON blob.

Seven domains mirroring the plan:
  infrastructure  — IBKR, Alpaca stream, data feed tier, account value age
  scan            — last scan age, consecutive zero-scored, duration, universe size
  apex            — per-track call status, latency stats, error count
  funnel          — last scan cycle stage-by-stage attrition from tier_d_funnel.jsonl
  positions       — bracket gaps, reconciliation, stuck pending orders
  resources       — disk free, JSONL write freshness, .fail_* sentinels
  readiness       — IC quality, live-readiness gates, phase gate status
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from config import CONFIG

_DATA_DIR = CONFIG.get("data_dir", "data")

# JSONL log paths whose write freshness we track
_WATCHED_LOGS = {
    "tier_d_funnel": os.path.join(_DATA_DIR, "tier_d_funnel.jsonl"),
    "apex_shadow": os.path.join(_DATA_DIR, "apex_shadow_log.jsonl"),
    "apex_audit": os.path.join(_DATA_DIR, "apex_decision_audit.jsonl"),
    "event_log": os.path.join(_DATA_DIR, "trade_events.jsonl"),
    "reconciled": os.path.join(_DATA_DIR, "reconciled_trades.jsonl"),
}

_FAIL_SENTINEL_GLOB = ".fail_"
_LATENCY_RING_SIZE = 20


# ── Domain builders ───────────────────────────────────────────────────────────

def _infrastructure(dash: dict) -> dict:
    """IBKR connectivity, Alpaca stream, data feed tier, account value staleness."""
    import bot_state as _bs

    ibkr_connected = not dash.get("ibkr_disconnected", False)
    account_value_age_s: float | None = None
    if _bs.account_values_updated_at is not None:
        account_value_age_s = round(time.time() - _bs.account_values_updated_at, 1)

    # Alpaca stream status: check if the stream object is live
    alpaca_stream_ok: bool | None = None
    try:
        if _bs._bar_stream is not None:
            alpaca_stream_ok = getattr(_bs._bar_stream, "_connected", None)
            if alpaca_stream_ok is None:
                alpaca_stream_ok = getattr(_bs._bar_stream, "running", None)
    except Exception:
        pass

    # Data feed tier: infer from recent signal logs — last logged source
    data_feed_tier = "unknown"
    try:
        import alpaca_data as _ad
        # _ad exposes _last_source_used if we add it; otherwise leave as unknown
        data_feed_tier = getattr(_ad, "_last_source_used", "unknown")
    except Exception:
        pass

    reconnects_today = 0
    try:
        _logs = dash.get("logs") or []
        for _l in _logs[:500]:
            if "reconnect" in (_l.get("msg") or "").lower():
                reconnects_today += 1
    except Exception:
        pass

    # Stale threshold from bot_ibkr config
    stale_warn_s = 240
    stale_hard_s = 300
    account_stale = False
    if account_value_age_s is not None and account_value_age_s > stale_warn_s:
        account_stale = True

    return {
        "ibkr_connected": ibkr_connected,
        "account_value_age_s": account_value_age_s,
        "account_stale": account_stale,
        "account_stale_warn_threshold_s": stale_warn_s,
        "alpaca_stream_ok": alpaca_stream_ok,
        "data_feed_tier": data_feed_tier,
        "reconnects_today": reconnects_today,
        "bot_status": dash.get("status", "unknown"),
        "paused": dash.get("paused", False),
        "killed": dash.get("killed", False),
        "hot_reload_count": dash.get("hot_reload_count", 0),
    }


def _scan(dash: dict) -> dict:
    """Last scan age, consecutive zero-scored scans, duration ring, universe size."""
    last_scan = dash.get("last_scan")
    last_scan_age_s: float | None = None
    if last_scan:
        try:
            _ts = datetime.fromisoformat(str(last_scan).replace("Z", "+00:00"))
            last_scan_age_s = round((datetime.now(UTC) - _ts).total_seconds(), 1)
        except Exception:
            pass

    durations = dash.get("scan_durations") or []
    avg_duration_s: float | None = None
    last_duration_s: float | None = None
    if durations:
        _vals = [d.get("duration_s") for d in durations if d.get("duration_s") is not None]
        if _vals:
            avg_duration_s = round(sum(_vals) / len(_vals), 1)
            last_duration_s = round(_vals[-1], 1)

    # consecutive zero-scored: stored in bot_trading module-level counter
    consecutive_zero = 0
    try:
        import bot_trading as _bt
        consecutive_zero = getattr(_bt, "_consecutive_zero_scored", 0)
    except Exception:
        pass

    # universe size from last scan cycle entry
    last_universe_size: int | None = None
    if durations:
        last_universe_size = durations[-1].get("candidates")

    return {
        "scan_count": dash.get("scan_count", 0),
        "last_scan_age_s": last_scan_age_s,
        "consecutive_zero_scored": consecutive_zero,
        "consecutive_zero_threshold": 3,
        "avg_duration_s": avg_duration_s,
        "last_duration_s": last_duration_s,
        "last_universe_size": last_universe_size,
        "scanning": dash.get("scanning", False),
        "next_scan_seconds": dash.get("next_scan_seconds", 0),
    }


def _apex(dash: dict) -> dict:
    """Track A / Track B / Shadow call status, latency stats, error count."""
    latencies = dash.get("apex_call_latencies") or []
    errors_1h = dash.get("apex_errors_1h") or 0

    # Compute per-track last status and latency
    tracks: dict[str, dict] = {}
    for entry in reversed(latencies):
        track = entry.get("track", "SCAN_CYCLE")
        if track not in tracks:
            tracks[track] = {
                "last_ok": entry.get("ok"),
                "last_latency_s": entry.get("latency_s"),
                "last_ts": entry.get("ts"),
            }

    all_latency_vals = [e.get("latency_s") for e in latencies if e.get("latency_s") is not None]
    avg_latency_s: float | None = None
    p95_latency_s: float | None = None
    if all_latency_vals:
        avg_latency_s = round(sum(all_latency_vals) / len(all_latency_vals), 2)
        sorted_vals = sorted(all_latency_vals)
        p95_idx = max(0, int(len(sorted_vals) * 0.95) - 1)
        p95_latency_s = round(sorted_vals[p95_idx], 2)

    recent_ok_rate: float | None = None
    if latencies:
        ok_count = sum(1 for e in latencies if e.get("ok"))
        recent_ok_rate = round(ok_count / len(latencies), 3)

    return {
        "tracks": tracks,
        "avg_latency_s": avg_latency_s,
        "p95_latency_s": p95_latency_s,
        "recent_ok_rate": recent_ok_rate,
        "errors_1h": errors_1h,
        "call_history": latencies[-_LATENCY_RING_SIZE:],
    }


def _funnel() -> dict:
    """Last scan cycle attrition from tier_d_funnel.jsonl."""
    path = _WATCHED_LOGS["tier_d_funnel"]
    if not os.path.exists(path):
        return {"available": False}

    # Read last ~200 lines to find the most recent pipeline stage set
    last_pipeline: dict[int, dict] = {}
    last_apex_cap: dict | None = None
    last_dispatch: dict | None = None
    try:
        with open(path, errors="replace") as fh:
            # Seek from end for efficiency on large files
            fh.seek(0, 2)
            file_size = fh.tell()
            read_size = min(file_size, 32_768)  # last 32KB
            fh.seek(max(0, file_size - read_size))
            lines = fh.readlines()

        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            stage = rec.get("stage")
            if stage == "pipeline":
                step = rec.get("step")
                if isinstance(step, int):
                    last_pipeline[step] = rec
            elif stage == "apex_cap":
                last_apex_cap = rec
            elif stage == "dispatch":
                last_dispatch = rec
    except Exception as e:
        return {"available": False, "error": str(e)}

    if not last_pipeline:
        return {"available": False}

    attrition = {
        step: {
            "in": r.get("in"),
            "out": r.get("out"),
            "blocked": r.get("blocked"),
            "reason": r.get("reason"),
        }
        for step, r in sorted(last_pipeline.items())
    }

    top_blocks: list[dict] = []
    try:
        _block_counts: dict[str, int] = {}
        for r in last_pipeline.values():
            for sym, reason in (r.get("block_reasons") or {}).items():
                _block_counts[reason] = _block_counts.get(reason, 0) + 1
        top_blocks = sorted(
            [{"reason": k, "count": v} for k, v in _block_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:5]
    except Exception:
        pass

    universe_in = (attrition.get(1) or {}).get("in")
    last_out = None
    for step in sorted(attrition.keys(), reverse=True):
        candidate = (attrition.get(step) or {}).get("out")
        if candidate is not None:
            last_out = candidate
            break

    apex_cap_hit = None
    if last_apex_cap:
        apex_cap_hit = {
            "cap": last_apex_cap.get("cap"),
            "before": last_apex_cap.get("before"),
            "after": last_apex_cap.get("after"),
            "killed": (last_apex_cap.get("before") or 0) - (last_apex_cap.get("after") or 0),
        }

    dispatch_summary = None
    if last_dispatch:
        dispatch_summary = {
            "dispatched": last_dispatch.get("dispatched"),
            "filled": last_dispatch.get("filled"),
            "rejected": last_dispatch.get("rejected"),
        }

    return {
        "available": True,
        "universe_in": universe_in,
        "after_pipeline": last_out,
        "attrition": attrition,
        "top_blocks": top_blocks,
        "apex_cap": apex_cap_hit,
        "dispatch": dispatch_summary,
    }


def _positions() -> dict:
    """Bracket gaps, reconciliation mismatches, stuck pending intents."""
    bracket_gaps: list[str] = []
    unmatched_count = 0
    stuck_intents: list[dict] = []
    position_count = 0

    try:
        from orders_state import active_trades, _trades_lock
        with _trades_lock:
            snapshot = dict(active_trades)
        position_count = len(snapshot)
        for symbol, trade in snapshot.items():
            if trade.get("status") in ("ACTIVE", "TRIMMING"):
                sl_id = trade.get("sl_order_id")
                tp_id = trade.get("tp_order_id")
                # If both are missing, flag as bracket gap
                if not sl_id and not tp_id:
                    bracket_gaps.append(symbol)
    except Exception:
        pass

    try:
        from event_log import pending_orders
        now_utc = datetime.now(UTC)
        _stuck_threshold_s = 300  # 5 minutes
        for order in pending_orders():
            ts_str = order.get("ts") or ""
            if ts_str:
                try:
                    _ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_s = (now_utc - _ts).total_seconds()
                    if age_s > _stuck_threshold_s:
                        stuck_intents.append({
                            "symbol": order.get("symbol"),
                            "trade_id": order.get("trade_id"),
                            "age_s": round(age_s, 0),
                        })
                except Exception:
                    pass
    except Exception:
        pass

    try:
        from ibkr_reconciler import load_reconciled
        from datetime import date
        _today = date.today().isoformat()
        _records = load_reconciled(_today)
        unmatched_count = sum(1 for r in _records if not r.get("reconciled", True))
    except Exception:
        pass

    return {
        "position_count": position_count,
        "bracket_gaps": bracket_gaps,
        "bracket_gap_count": len(bracket_gaps),
        "unmatched_count": unmatched_count,
        "stuck_pending_intents": stuck_intents,
        "stuck_intent_count": len(stuck_intents),
    }


def _resources() -> dict:
    """Disk free, JSONL write freshness, .fail_* sentinel files."""
    disk_free_gb: float | None = None
    disk_free_pct: float | None = None
    try:
        _stat = os.statvfs(_DATA_DIR if os.path.isdir(_DATA_DIR) else ".")
        disk_free_gb = round(_stat.f_bavail * _stat.f_frsize / 1_073_741_824, 2)
        disk_total_gb = _stat.f_blocks * _stat.f_frsize / 1_073_741_824
        if disk_total_gb > 0:
            disk_free_pct = round(disk_free_gb / disk_total_gb * 100, 1)
    except Exception:
        pass

    log_freshness: dict[str, Any] = {}
    _now = time.time()
    for name, path in _WATCHED_LOGS.items():
        if os.path.exists(path):
            age_s = round(_now - os.path.getmtime(path), 0)
            size_mb = round(os.path.getsize(path) / 1_048_576, 2)
            log_freshness[name] = {"age_s": age_s, "size_mb": size_mb, "present": True}
        else:
            log_freshness[name] = {"age_s": None, "size_mb": 0, "present": False}

    fail_sentinels: list[str] = []
    try:
        for fname in os.listdir("."):
            if fname.startswith(_FAIL_SENTINEL_GLOB):
                fail_sentinels.append(fname)
    except Exception:
        pass

    return {
        "disk_free_gb": disk_free_gb,
        "disk_free_pct": disk_free_pct,
        "disk_low": disk_free_pct is not None and disk_free_pct < 10,
        "log_freshness": log_freshness,
        "fail_sentinel_count": len(fail_sentinels),
        "fail_sentinels": fail_sentinels,
    }


def _readiness() -> dict:
    """IC quality, live-readiness gates, phase gate status."""
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
            "using_equal_weights": _ih.using_equal_weights,
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
            "n_valid_records": _lr.n_valid_records,
            "walkforward_sharpe": _lr.walkforward_sharpe,
        }
    except Exception as e:
        live_readiness = {"error": str(e)}

    try:
        from phase_gate import get_status
        _ps = get_status()
        phase = _ps.as_dict()
    except Exception as e:
        phase = {"error": str(e)}

    return {
        "ic": ic_health,
        "gates": live_readiness,
        "phase": phase,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def build_health_report() -> dict:
    """
    Aggregate all seven health domains into a single dict suitable for JSON
    serialisation.  Always returns a complete structure — never raises.
    """
    try:
        import bot_state as _bs
        _dash = _bs.dash
    except Exception:
        _dash = {}

    ts = datetime.now(UTC).isoformat()

    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"error": str(e)}

    return {
        "ts": ts,
        "infrastructure": _safe(_infrastructure, _dash),
        "scan": _safe(_scan, _dash),
        "apex": _safe(_apex, _dash),
        "funnel": _safe(_funnel),
        "positions": _safe(_positions),
        "resources": _safe(_resources),
        "readiness": _safe(_readiness),
    }
