# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic_decision_writer.py                     ║
# ║   Append-only decision event log for IC / research.         ║
# ║                                                              ║
# ║   Single responsibility: record what happened to every      ║
# ║   scored candidate after it left the signal log.            ║
# ║                                                              ║
# ║   All writes are best-effort — failure never affects        ║
# ║   live execution. No live trading decisions here.           ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime

from config import CONFIG

log = logging.getLogger("decifer.ic_decision_writer")

# ── Path ───────────────────────────────────────────────────────────────────────

_EVENTS_FILE: str = os.path.join(
    CONFIG.get("data_dir", "data"), "ic_decision_events.jsonl"
)

_MAX_BYTES: int = int(CONFIG.get("ic_decision_events_max_mb", 20)) * 1_048_576

_write_lock = threading.Lock()

# ── Valid status values ────────────────────────────────────────────────────────

VALID_STATUSES: frozenset[str] = frozenset({
    "scored",           # logged but no later decision known yet
    "below_threshold",  # scored but did not meet the min score / shortlist threshold
    "passed_to_apex",   # included in the shortlist sent to Apex
    "apex_selected",    # Apex selected the candidate for possible entry
    "apex_rejected",    # shown to Apex but not selected
    "risk_blocked",     # selected but blocked by a pre-entry risk gate
    "executed",         # progressed to ORDER_INTENT / active trade creation
    "order_failed",     # selected but order intent or placement failed
    "unknown",          # status cannot be determined safely
})

# ── Public API ─────────────────────────────────────────────────────────────────


def write_event(
    observation_id: str | None,
    scan_id: str | None,
    symbol: str,
    decision_status: str,
    *,
    session_date: str | None = None,
    ts_utc: str | None = None,
    candidate_source: str | None = None,
    ranking_position: int | None = None,
    ranking_total: int | None = None,
    reason: str | None = None,
    trade_id: str | None = None,
) -> None:
    """
    Append one decision event to data/ic_decision_events.jsonl.

    observation_id is the join key to signals_log.jsonl and
    training_records.jsonl.  It is None for events where the
    originating signals_log record could not be identified.

    decision_status must be one of VALID_STATUSES.  An invalid value
    is written as "unknown" with the bad value recorded in reason.

    This function never raises — all errors are logged at WARNING.
    """
    try:
        _status = decision_status if decision_status in VALID_STATUSES else "unknown"
        if _status == "unknown" and decision_status not in VALID_STATUSES:
            _reason = f"invalid_status:{decision_status!r}; {reason or ''}"
        else:
            _reason = reason

        event = {
            "ts_utc": ts_utc or datetime.now(UTC).isoformat(),
            "observation_id": observation_id,
            "scan_id": scan_id,
            "symbol": symbol,
            "decision_status": _status,
            "session_date": session_date or (scan_id[:8] if scan_id and len(scan_id) >= 8 else None),
            "candidate_source": candidate_source,
            "ranking_position": ranking_position,
            "ranking_total": ranking_total,
            "reason": _reason,
            "trade_id": trade_id,
        }
        _append(event)
    except Exception as exc:
        log.warning("ic_decision_writer.write_event failed for %s/%s: %s", symbol, decision_status, exc)


def write_events_bulk(events: list[dict]) -> None:
    """
    Append multiple events in a single file open.  Each dict must have at
    minimum: symbol, decision_status.  Missing fields default to None.

    Preferred for high-volume writes (e.g. bulk apex_rejected after a scan).
    """
    if not events:
        return
    try:
        _now = datetime.now(UTC).isoformat()
        lines: list[str] = []
        for raw in events:
            status = raw.get("decision_status", "unknown")
            _status = status if status in VALID_STATUSES else "unknown"
            sym = raw.get("symbol", "")
            sid = raw.get("scan_id")
            event = {
                "ts_utc": raw.get("ts_utc") or _now,
                "observation_id": raw.get("observation_id"),
                "scan_id": sid,
                "symbol": sym,
                "decision_status": _status,
                "session_date": raw.get("session_date") or (sid[:8] if sid and len(sid) >= 8 else None),
                "candidate_source": raw.get("candidate_source"),
                "ranking_position": raw.get("ranking_position"),
                "ranking_total": raw.get("ranking_total"),
                "reason": raw.get("reason"),
                "trade_id": raw.get("trade_id"),
            }
            lines.append(json.dumps(event))
        _append_lines(lines)
    except Exception as exc:
        log.warning("ic_decision_writer.write_events_bulk failed (%d events): %s", len(events), exc)


# ── Internal ───────────────────────────────────────────────────────────────────


def _append(event: dict) -> None:
    _append_lines([json.dumps(event)])


def _append_lines(lines: list[str]) -> None:
    if not lines:
        return
    os.makedirs(os.path.dirname(os.path.abspath(_EVENTS_FILE)), exist_ok=True)
    try:
        from utils.log_rotation import rotate_jsonl_if_needed
        rotate_jsonl_if_needed(_EVENTS_FILE, _MAX_BYTES)
    except Exception:
        pass  # rotation failure is non-fatal
    with _write_lock:
        with open(_EVENTS_FILE, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
