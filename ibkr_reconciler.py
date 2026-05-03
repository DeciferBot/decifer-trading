# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ibkr_reconciler.py                        ║
# ║   IBKR-authoritative trade records with Decifer metadata    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Merges IBKR execution records with Decifer's event_log metadata.

IBKR   = ground truth for fill prices, commission, realized P&L.
event_log = ground truth for trade circumstance (score, regime, signals, thesis).

Result is written to data/reconciled_trades.jsonl and cached for 60 seconds.
Falls back to event_log data if IBKR is unavailable (ibkr_match='unmatched').
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from config import CONFIG

log = logging.getLogger("decifer.ibkr_reconciler")

_OUT_FILE = Path(CONFIG.get("reconciled_trades_log", "data/reconciled_trades.jsonl"))
_EVENTS_FILE = Path(CONFIG.get("trade_events_log", "data/trade_events.jsonl"))

# ±10-minute window covers IBKR local-time vs UTC skew on paper accounts
_FUZZY_WINDOW_S = 600

_cache: list[dict] = []
_cache_ts: float = 0.0


# ── Public API ────────────────────────────────────────────────────────────────


def reconcile_closes(ib, cutover_date: str) -> list[dict]:
    """Return reconciled closed-trade records for all closes on or after cutover_date.

    Cached for 60 seconds. Falls back to event_log-only records when IBKR
    is offline, marking them ibkr_match='unmatched'.
    """
    global _cache, _cache_ts
    if time.monotonic() - _cache_ts < 60:
        return _cache

    intents, fills, closes = _load_event_log()

    relevant = {
        tid: close for tid, close in closes.items()
        if (close.get("ts") or "")[:10] >= cutover_date
        and close.get("exit_reason") != "manual_repair"
    }
    if not relevant:
        _cache = []
        _cache_ts = time.monotonic()
        return _cache

    ibkr_fills = _fetch_ibkr_fills(ib)
    commission_idx = _load_commission_index()
    written_ids = _load_written_ids()

    records: list[dict] = []
    new_records: list[dict] = []
    for tid, close in relevant.items():
        rec = _build_record(
            tid,
            intents.get(tid, {}),
            fills.get(tid, {}),
            close,
            ibkr_fills,
            commission_idx,
        )
        records.append(rec)
        if tid not in written_ids:
            new_records.append(rec)

    if new_records:
        _write_records(new_records)

    _cache = records
    _cache_ts = time.monotonic()
    return records


def load_reconciled(since_date: str) -> list[dict]:
    """Read reconciled_trades.jsonl and return records on or after since_date."""
    if not _OUT_FILE.exists():
        return []
    out = []
    for line in _OUT_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if (rec.get("exit_time") or "")[:10] >= since_date:
                out.append(rec)
        except json.JSONDecodeError:
            pass
    return out


def invalidate_cache() -> None:
    """Force next reconcile_closes() call to re-query IBKR."""
    global _cache_ts
    _cache_ts = 0.0


# ── Internal ──────────────────────────────────────────────────────────────────


def _load_event_log() -> tuple[dict, dict, dict]:
    intents: dict[str, dict] = {}
    fills: dict[str, dict] = {}
    closes: dict[str, dict] = {}
    if not _EVENTS_FILE.exists():
        return intents, fills, closes
    for line in _EVENTS_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        tid = rec.get("trade_id")
        if not tid:
            continue
        event = rec.get("event")
        if event == "ORDER_INTENT":
            intents[tid] = rec
        elif event == "ORDER_FILLED":
            fills[tid] = rec
        elif event == "POSITION_CLOSED":
            closes[tid] = rec
    return intents, fills, closes


def _fetch_ibkr_fills(ib) -> list:
    """Return all known fills: reqExecutions (last 24h) + Flex Web Service (last 365 days).

    reqExecutions is real-time but limited to 24 hours.  Flex covers historical
    gaps so _find_fill() can match positions closed more than a day ago with real
    timestamps and prices instead of falling back to event_log estimates.
    """
    fills: list = []

    # Layer 1: reqExecutions — authoritative for the last 24 hours
    try:
        if ib is not None and ib.isConnected():
            result = ib.reqExecutions()
            if isinstance(result, list):
                fills.extend(result)
    except Exception as exc:
        log.debug("reqExecutions failed (non-fatal): %s", exc)

    # Layer 2: Flex Web Service — up to 365 days, fills the historical gap
    token = CONFIG.get("ibkr_flex_token", "")
    query_id = CONFIG.get("ibkr_flex_trades_query_id", "")
    if token and query_id:
        try:
            from bot_account import fetch_flex_trades
            flex_fills = fetch_flex_trades(token, query_id)
            fills.extend(flex_fills)
            log.debug("Flex trades fetched: %d fills", len(flex_fills))
        except Exception as exc:
            log.debug("Flex trades fetch failed (non-fatal): %s", exc)

    return fills


def _load_commission_index() -> dict[int, dict]:
    """Return {order_id: {commission, realized_pnl}} from orders.json."""
    try:
        from learning import load_orders
        idx: dict[int, dict] = {}
        for o in load_orders():
            oid = o.get("order_id")
            if oid:
                c = _safe_float(o.get("commission"))
                r = _safe_float(o.get("realized_pnl"))
                if c is not None or r is not None:
                    idx[int(oid)] = {"commission": c, "realized_pnl": r}
        return idx
    except Exception:
        return {}


def _load_written_ids() -> set[str]:
    if not _OUT_FILE.exists():
        return set()
    ids: set[str] = set()
    for line in _OUT_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            tid = rec.get("trade_id")
            if tid:
                ids.add(tid)
        except json.JSONDecodeError:
            pass
    return ids


def _write_records(records: list[dict]) -> None:
    _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUT_FILE, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _build_record(
    tid: str,
    intent: dict,
    fill: dict,
    close: dict,
    ibkr_fills: list,
    commission_idx: dict[int, dict],
) -> dict:
    symbol = close.get("symbol") or intent.get("symbol", "")
    direction = intent.get("direction", "LONG")
    entry_order_id = int(fill.get("order_id") or 0)

    # Sides: LONG entry=BOT/exit=SLD, SHORT entry=SLD/exit=BOT
    entry_side = "BOT" if direction == "LONG" else "SLD"
    exit_side = "SLD" if direction == "LONG" else "BOT"

    entry_fill_obj = _find_fill(ibkr_fills, symbol, entry_side, fill.get("ts") or intent.get("ts"), entry_order_id)
    exit_fill_obj = _find_fill(ibkr_fills, symbol, exit_side, close.get("ts"), 0)

    ibkr_match = "unmatched"
    if entry_fill_obj is not None or exit_fill_obj is not None:
        ibkr_match = "exact" if entry_order_id and entry_fill_obj is not None else "fuzzy"

    # Fill prices: IBKR authoritative, event_log fallback
    entry_price = _avg_price(entry_fill_obj) or _safe_float(fill.get("fill_price")) or _safe_float(intent.get("intended_price")) or 0.0
    exit_price = _avg_price(exit_fill_obj) or _safe_float(close.get("exit_price")) or 0.0
    qty = int(fill.get("fill_qty") or intent.get("qty") or 0)

    # Commission from orders.json (keyed by entry order_id)
    comm_entry = None
    comm_exit = None
    ibkr_realized_pnl = None
    if entry_order_id and entry_order_id in commission_idx:
        comm_entry = commission_idx[entry_order_id].get("commission")
    if exit_fill_obj is not None:
        exit_oid = int(getattr(exit_fill_obj.execution, "orderId", 0) or 0)
        if exit_oid and exit_oid in commission_idx:
            comm_exit = commission_idx[exit_oid].get("commission")
            ibkr_realized_pnl = commission_idx[exit_oid].get("realized_pnl")

    total_commission = _sum_safe(comm_entry, comm_exit)
    if ibkr_realized_pnl is not None:
        pnl_gross = ibkr_realized_pnl
        pnl_net = ibkr_realized_pnl - (total_commission or 0.0)
    else:
        pnl_gross = _safe_float(close.get("pnl")) or round((exit_price - entry_price) * qty * (1 if direction == "LONG" else -1), 2)
        pnl_net = pnl_gross - (total_commission or 0.0) if total_commission else pnl_gross

    return {
        "trade_id": tid,
        "symbol": symbol,
        "direction": direction,
        "trade_type": intent.get("trade_type", "INTRADAY"),
        "instrument": intent.get("instrument", "stock"),
        # Dashboard-compatible fields
        "timestamp": close.get("ts", ""),
        "exit_time": close.get("ts", ""),
        "action": "CLOSE",
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": qty,
        "pnl": pnl_net,
        "exit_reason": close.get("exit_reason", ""),
        "hold_minutes": close.get("hold_minutes", 0),
        "score": intent.get("score", 0),
        # IBKR ground truth
        "commission_entry": comm_entry,
        "commission_exit": comm_exit,
        "realized_pnl_gross": pnl_gross,
        "realized_pnl_net": pnl_net,
        # Decifer metadata
        "regime": intent.get("regime", ""),
        "signal_scores": intent.get("signal_scores", {}),
        "ic_weights_at_entry": intent.get("ic_weights_at_entry", {}),
        "entry_thesis": intent.get("entry_thesis") or intent.get("reasoning", ""),
        "conviction": intent.get("conviction", 0.0),
        "setup_type": intent.get("setup_type", ""),
        # Reconciliation metadata
        "reconciled": ibkr_match != "unmatched",
        "reconciled_at": datetime.now(UTC).isoformat(),
        "ibkr_match": ibkr_match,
    }


def _find_fill(ibkr_fills: list, symbol: str, side: str, ref_ts: str | None, prefer_order_id: int = 0):
    """Return the IBKR fill for (symbol, side) closest to ref_ts.

    Prefers exact order_id match when prefer_order_id != 0.
    Falls back to closest time match within _FUZZY_WINDOW_S.
    """
    ref_dt = _parse_iso(ref_ts) if ref_ts else None
    best = None
    best_delta = float("inf")

    for fill in ibkr_fills:
        if getattr(fill.contract, "symbol", "") != symbol:
            continue
        if getattr(fill.execution, "side", "") != side:
            continue
        if prefer_order_id and int(getattr(fill.execution, "orderId", 0) or 0) == prefer_order_id:
            return fill  # exact match wins immediately
        fill_dt = _parse_ibkr_time(getattr(fill.execution, "time", ""))
        if ref_dt is None or fill_dt is None:
            if best is None:
                best = fill
            continue
        delta = abs((ref_dt.replace(tzinfo=None) - fill_dt.replace(tzinfo=None)).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best = fill

    return best if best_delta < _FUZZY_WINDOW_S else None


def _avg_price(fill_obj) -> float | None:
    if fill_obj is None:
        return None
    v = getattr(fill_obj.execution, "avgPrice", None)
    return _safe_float(v)


def _parse_ibkr_time(t: str) -> datetime | None:
    """Parse IBKR execution time string ('20260430 14:30:22 ...')."""
    if not t:
        return None
    part = t[:15].strip()  # '20260430 14:30:'
    for fmt in ("%Y%m%d %H:%M:%S", "%Y%m%d  %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(t[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            pass
    return None


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _sum_safe(a, b) -> float | None:
    if a is None and b is None:
        return None
    return (a or 0.0) + (b or 0.0)
