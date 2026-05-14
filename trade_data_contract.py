# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  trade_data_contract.py                     ║
# ║   Canonical trade evidence ledgers for ML data collection    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Append-only evidence ledgers for the trade evidence chain.

Writes two canonical files under data/ml/:
  entry_trade_snapshots.jsonl        — one record per filled trade, at entry time
  closed_trade_training_ledger.jsonl — one record per closed trade, joined from entry snapshot

All writes are fail-soft: errors are logged and quarantined; they never block order execution.
All writers are idempotent: duplicate trade_id → quarantine, not silent overwrite.

NO imports from broker execution, order, risk, sizing, or Apex modules.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from config import CONFIG

log = logging.getLogger("decifer.trade_data_contract")

# ── Schema version ─────────────────────────────────────────────────────────────
SCHEMA_VERSION = "1.0"

# ── Canonical ledger paths ─────────────────────────────────────────────────────
_ML_DIR = Path(CONFIG.get("ml_data_dir", "data/ml"))

ENTRY_SNAPSHOT_FILE        = _ML_DIR / "entry_trade_snapshots.jsonl"
CLOSED_LEDGER_FILE         = _ML_DIR / "closed_trade_training_ledger.jsonl"
QUARANTINE_ENTRY_FILE      = _ML_DIR / "quarantine_entry_snapshots.jsonl"
QUARANTINE_CLOSED_FILE     = _ML_DIR / "quarantine_closed_records.jsonl"
QUARANTINE_MISSING_ENTRY   = _ML_DIR / "quarantine_missing_entry_snapshot.jsonl"
QUARANTINE_MISSING_OUTCOME = _ML_DIR / "quarantine_missing_outcome.jsonl"
QUARANTINE_SCHEMA_INVALID  = _ML_DIR / "quarantine_schema_invalid.jsonl"
QUARANTINE_DUPLICATE_ID    = _ML_DIR / "quarantine_duplicate_trade_id.jsonl"

# ── Required field sets ────────────────────────────────────────────────────────
_ENTRY_SNAPSHOT_REQUIRED = frozenset({
    "schema_version",
    "trade_id",
    "symbol",
    "direction",
    "instrument",
    "trade_type",
    "fill_price",
    "fill_qty",
    "entry_price_source",
    "fill_confirmed",
    "regime",
    "signal_scores",
    "conviction",
    "score",
    "ts_fill",
    "ts_written",
})

_CLOSED_RECORD_REQUIRED = frozenset({
    "schema_version",
    "trade_id",
    "symbol",
    "direction",
    "instrument",
    "trade_type",
    "fill_price",
    "fill_qty",
    "entry_price_source",
    "fill_confirmed",
    "regime",
    "signal_scores",
    "conviction",
    "score",
    "ts_fill",
    "ts_written",
    "exit_price",
    "ts_exit",
    "hold_minutes",
    "realised_pnl",
    "pnl_pct",
    "exit_reason",
    "win_loss_label",
    "ts_outcome_written",
})

# Fields that must NEVER appear in an entry snapshot (exit-time data only).
_ENTRY_FORBIDDEN_FIELDS = frozenset({
    "exit_price", "realised_pnl", "pnl", "pnl_pct",
    "win_loss_label", "hold_minutes", "ts_exit", "ts_outcome_written",
})

# ── Thread lock for all data/ml/ writes ───────────────────────────────────────
# Separate from event_log._lock — different file family, no coupling.
_lock = threading.Lock()


# ── Low-level I/O ─────────────────────────────────────────────────────────────

def _append_jsonl(path: Path, record: dict) -> None:
    """Append one JSON record to *path*, creating parent dirs and fsyncing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def _write_quarantine(path: Path, data: dict, reason: str = "") -> None:
    """Write *data* to quarantine file *path* with an explanatory reason."""
    record = dict(data)
    record["quarantine_reason"] = reason
    record["quarantine_ts"] = datetime.now(UTC).isoformat()
    try:
        _append_jsonl(path, record)
    except Exception as qe:
        log.error("trade_data_contract: quarantine write failed (%s): %s", path.name, qe)


# ── Duplicate detection ────────────────────────────────────────────────────────

def _load_existing_trade_ids(path: Path) -> frozenset:
    """Return all trade_id values already written to *path*.

    Scans line-by-line at write time. For retail-scale ledgers (hundreds of
    records/day) this is fast enough and avoids in-memory state that would be
    lost on process restart.
    """
    ids: set[str] = set()
    if not path.exists():
        return frozenset()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                    tid = rec.get("trade_id")
                    if tid:
                        ids.add(str(tid))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return frozenset(ids)


# ── Entry snapshot ─────────────────────────────────────────────────────────────

def build_entry_snapshot(
    active_trade: dict,
    fill_price: float,
    fill_qty: int,
    entry_price_source: str,
    fill_confirmed: bool,
    order_id: int = 0,
    trade_id: str = "",
) -> dict:
    """Build an entry snapshot dict from the active_trade copy and fill context.

    *active_trade* must be a shallow copy of active_trades[key] taken under
    _trades_lock by the caller. It must not be the live mutable dict.

    Raises ValueError if required fields are missing or forbidden exit-time
    fields are present.
    """
    now_ts = datetime.now(UTC).isoformat()

    # Resolve regime — entry_regime is the structural label stored on the trade.
    regime = active_trade.get("entry_regime") or active_trade.get("regime", "UNKNOWN")
    if isinstance(regime, dict):
        # Defensive: if a raw regime dict was stored, extract the label.
        regime = regime.get("regime", "UNKNOWN")

    # session_character: try active_trade direct, then entry_context dict.
    _ec = active_trade.get("entry_context") or {}
    if isinstance(_ec, str):
        try:
            _ec = json.loads(_ec)
        except Exception:
            _ec = {}
    session_character = (
        active_trade.get("session_character")
        or _ec.get("session_character", "")
    )

    # Sector / catalyst from TradeContext (already serialised onto active_trade).
    sector = _ec.get("sector_etf", "") or ""
    catalyst = _ec.get("catalyst_type", "") or ""

    # Source tracking: populated by signal_dispatcher enrichment.
    _ao = active_trade.get("agent_outputs") or {}
    candidate_source = _ao.get("candidate_source", "UNKNOWN")
    handoff_source = _ao.get("handoff_source_labels") or []

    ts_fill = active_trade.get("open_time") or now_ts
    eff_trade_id = trade_id or active_trade.get("trade_id", "")

    # Track which optional fields are absent for data-quality reporting.
    missing: list[str] = []
    if not active_trade.get("signal_scores"):
        missing.append("signal_scores")
    if regime == "UNKNOWN":
        missing.append("regime")
    if not session_character:
        missing.append("session_character")
    if candidate_source == "UNKNOWN":
        missing.append("candidate_source")
    if not sector:
        missing.append("sector")
    if fill_price <= 0:
        missing.append("fill_price")

    snap = {
        "schema_version": SCHEMA_VERSION,
        "trade_id": eff_trade_id,
        "symbol": active_trade.get("symbol", ""),
        "direction": active_trade.get("direction", "LONG"),
        "instrument": active_trade.get("instrument", "stock"),
        "trade_type": active_trade.get("trade_type") or "INTRADAY",
        "fill_price": float(fill_price),
        "fill_qty": int(fill_qty),
        "entry_price_source": entry_price_source,
        "fill_confirmed": bool(fill_confirmed),
        "intended_price": float(active_trade.get("intended_price") or active_trade.get("entry") or fill_price),
        "order_id": int(order_id),
        "sl": float(active_trade.get("sl") or 0.0),
        "tp": float(active_trade.get("tp") or 0.0),
        "score": float(active_trade.get("score") or active_trade.get("entry_score") or 0.0),
        "conviction": float(active_trade.get("conviction") or 0.0),
        "regime": regime,
        "signal_scores": active_trade.get("signal_scores") or {},
        "score_breakdown": active_trade.get("score_breakdown") or {},
        "session_character": session_character,
        "sector": sector,
        "catalyst": catalyst,
        "candidate_source": candidate_source,
        "handoff_source": handoff_source,
        "source_mode": "UNKNOWN",  # Field does not exist in Decifer execution layer
        "setup_type": active_trade.get("setup_type", ""),
        "pattern_id": active_trade.get("pattern_id", ""),
        "atr": float(active_trade.get("atr") or 0.0),
        "advice_id": active_trade.get("advice_id", ""),
        "entry_thesis": active_trade.get("entry_thesis", ""),
        "ic_weight_snapshot": active_trade.get("ic_weights_at_entry"),
        "entry_context": _ec or None,
        "open_time": active_trade.get("open_time", ""),
        "ts_fill": ts_fill,
        "ts_written": now_ts,
        "missing_field_flags": missing,
    }

    # Guard: reject exit-time fields that must never appear in entry snapshots.
    forbidden = _ENTRY_FORBIDDEN_FIELDS & set(snap.keys())
    if forbidden:
        raise ValueError(
            f"build_entry_snapshot: forbidden exit-time fields present: {sorted(forbidden)}"
        )

    # Guard: all required fields must be present.
    absent = _ENTRY_SNAPSHOT_REQUIRED - snap.keys()
    if absent:
        raise ValueError(f"build_entry_snapshot: missing required fields: {sorted(absent)}")

    return snap


def write_entry_snapshot(
    trade_id: str,
    active_trade_copy: dict,
    fill_price: float,
    fill_qty: int,
    entry_price_source: str,
    fill_confirmed: bool,
    order_id: int = 0,
) -> bool:
    """Write one entry snapshot to the canonical entry ledger.

    Returns True on success, False on any failure. NEVER raises.
    Idempotent: duplicate trade_id goes to quarantine, not the main ledger.
    """
    try:
        snap = build_entry_snapshot(
            active_trade=active_trade_copy,
            fill_price=fill_price,
            fill_qty=fill_qty,
            entry_price_source=entry_price_source,
            fill_confirmed=fill_confirmed,
            order_id=order_id,
            trade_id=trade_id,
        )

        # Hard guards: quarantine-only conditions.
        eff_id = snap.get("trade_id", "")
        if not eff_id:
            _write_quarantine(QUARANTINE_ENTRY_FILE, snap, reason="blank_trade_id")
            log.warning("trade_data_contract: entry snapshot rejected — blank trade_id")
            return False

        direction = snap.get("direction", "")
        if direction not in ("LONG", "SHORT"):
            _write_quarantine(QUARANTINE_ENTRY_FILE, snap, reason=f"invalid_direction:{direction}")
            log.warning("trade_data_contract: entry snapshot rejected — invalid direction '%s'", direction)
            return False

        # Idempotency check.
        existing_ids = _load_existing_trade_ids(ENTRY_SNAPSHOT_FILE)
        if eff_id in existing_ids:
            _write_quarantine(QUARANTINE_DUPLICATE_ID, snap, reason="duplicate_entry_snapshot")
            log.warning(
                "trade_data_contract: entry snapshot duplicate trade_id=%s — quarantined", eff_id
            )
            return False

        # Data-quality flagging: empty signal_scores → write main + quarantine copy.
        if not snap.get("signal_scores"):
            _write_quarantine(QUARANTINE_ENTRY_FILE, snap, reason="empty_signal_scores")
            log.debug(
                "trade_data_contract: entry snapshot trade_id=%s has empty signal_scores — "
                "writing to main AND quarantine",
                eff_id,
            )

        # Data-quality flagging: invalid fill price → also quarantine.
        if fill_price <= 0:
            _write_quarantine(QUARANTINE_ENTRY_FILE, snap, reason="invalid_fill_price")

        _append_jsonl(ENTRY_SNAPSHOT_FILE, snap)
        log.debug(
            "trade_data_contract: entry snapshot written trade_id=%s symbol=%s source=%s confirmed=%s",
            eff_id, snap.get("symbol"), entry_price_source, fill_confirmed,
        )
        return True

    except ValueError as ve:
        log.warning("trade_data_contract: entry snapshot build failed (%s) — quarantining", ve)
        _write_quarantine(QUARANTINE_ENTRY_FILE, active_trade_copy, reason=f"build_error:{ve}")
        return False
    except Exception as e:
        log.error("trade_data_contract: entry snapshot write failed: %s", e)
        return False


# ── Entry snapshot lookup ──────────────────────────────────────────────────────

def _load_entry_snapshot(trade_id: str) -> dict | None:
    """Return the first entry snapshot for *trade_id*, or None if not found."""
    if not ENTRY_SNAPSHOT_FILE.exists():
        return None
    try:
        with open(ENTRY_SNAPSHOT_FILE, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                    if rec.get("trade_id") == trade_id:
                        return rec
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return None


# ── Closed training record ─────────────────────────────────────────────────────

def derive_win_loss_label(realised_pnl: float) -> str:
    """Derive WIN / LOSS / BREAKEVEN from realised P&L."""
    if realised_pnl > 0:
        return "WIN"
    elif realised_pnl < 0:
        return "LOSS"
    return "BREAKEVEN"


def write_closed_record(
    trade_id: str,
    exit_price: float,
    realised_pnl: float | None,
    exit_reason: str,
    hold_minutes: int = 0,
    outcome_source: str = "",
    fees: float | None = None,
    slippage: float | None = None,
) -> bool:
    """Write one closed training record to the canonical closed ledger.

    Loads the matching entry snapshot for *trade_id*, joins realised outcome
    fields, derives win_loss_label, and appends to the closed ledger.

    Returns True on success, False on any failure. NEVER raises.
    Idempotent: duplicate trade_id goes to quarantine, not the main ledger.
    """
    try:
        now_ts = datetime.now(UTC).isoformat()

        # ── 1. Load entry snapshot ─────────────────────────────────────────
        snap = _load_entry_snapshot(trade_id)
        if snap is None:
            _write_quarantine(
                QUARANTINE_MISSING_ENTRY,
                {
                    "trade_id": trade_id,
                    "exit_price": exit_price,
                    "realised_pnl": realised_pnl,
                    "exit_reason": exit_reason,
                    "hold_minutes": hold_minutes,
                    "outcome_source": outcome_source,
                    "ts_outcome_written": now_ts,
                },
                reason="missing_entry_snapshot",
            )
            log.warning(
                "trade_data_contract: no entry snapshot found for trade_id=%s — "
                "closed outcome quarantined",
                trade_id,
            )
            return False

        # ── 2. Validate outcome ────────────────────────────────────────────
        if realised_pnl is None:
            _write_quarantine(
                QUARANTINE_MISSING_OUTCOME,
                dict(snap, exit_price=exit_price, exit_reason=exit_reason,
                     hold_minutes=hold_minutes, outcome_source=outcome_source,
                     ts_outcome_written=now_ts),
                reason="realised_pnl_is_none",
            )
            log.warning("trade_data_contract: realised_pnl is None for trade_id=%s — quarantined", trade_id)
            return False

        # ── 3. Idempotency check ───────────────────────────────────────────
        existing_ids = _load_existing_trade_ids(CLOSED_LEDGER_FILE)
        if trade_id in existing_ids:
            _write_quarantine(QUARANTINE_DUPLICATE_ID, snap, reason="duplicate_closed_record")
            log.warning(
                "trade_data_contract: closed record duplicate trade_id=%s — quarantined", trade_id
            )
            return False

        # ── 4. Build closed record by joining entry snapshot + outcome ─────
        fill_price = float(snap.get("fill_price") or 0.0)
        qty = float(snap.get("fill_qty") or 0)
        pnl_pct = round(float(realised_pnl) / (fill_price * qty), 4) if fill_price * qty else 0.0

        closed = dict(snap)  # inherit all entry snapshot fields
        # Remove ts_written from entry so we can set it fresh for the closed record.
        closed.pop("ts_written", None)

        closed.update({
            "exit_price": float(exit_price),
            "ts_exit": now_ts,
            "hold_minutes": int(hold_minutes),
            "realised_pnl": float(realised_pnl),
            "pnl_pct": pnl_pct,
            "exit_reason": exit_reason,
            "win_loss_label": derive_win_loss_label(float(realised_pnl)),
            "outcome_source": outcome_source,
            "fees": fees,
            "slippage": slippage,
            "ts_outcome_written": now_ts,
            "ts_written": now_ts,
        })

        # ── 5. Schema validation ───────────────────────────────────────────
        absent = _CLOSED_RECORD_REQUIRED - closed.keys()
        if absent:
            _write_quarantine(
                QUARANTINE_SCHEMA_INVALID,
                closed,
                reason=f"missing_required_fields:{sorted(absent)}",
            )
            log.warning(
                "trade_data_contract: closed record schema invalid for trade_id=%s — "
                "missing: %s",
                trade_id, sorted(absent),
            )
            return False

        # ── 6. Append ──────────────────────────────────────────────────────
        _append_jsonl(CLOSED_LEDGER_FILE, closed)
        log.debug(
            "trade_data_contract: closed record written trade_id=%s pnl=%.2f label=%s",
            trade_id, float(realised_pnl), closed["win_loss_label"],
        )
        return True

    except Exception as e:
        log.error("trade_data_contract: closed record write failed trade_id=%s: %s", trade_id, e)
        return False
