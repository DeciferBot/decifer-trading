# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  event_log.py                               ║
# ║   Append-only trade event log                                ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Append-only JSONL write-ahead log.  Single source of truth for open positions.

Three events, three guarantees
-------------------------------
ORDER_INTENT    written before IBKR submission — "I tried to trade"
ORDER_FILLED    written after IBKR confirms fill — "I actually traded"
POSITION_CLOSED written after exit confirmed — "trade is over"

Open position = ORDER_FILLED with no matching POSITION_CLOSED.
Pending order = ORDER_INTENT with no matching ORDER_FILLED (submitted, awaiting confirm).

Crash safety
------------
Every write calls os.fsync().  A process kill mid-write leaves a partial last
line which json.loads() rejects — the line is skipped and all prior records are
intact.  JSONL cannot corrupt the way SQLite WAL can.

Thread safety
-------------
A module-level lock serialises all appends.  Reads are lock-free; the file is
append-only so any snapshot is consistent.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from config import CONFIG

log = logging.getLogger("decifer.event_log")

_LOG_FILE = Path(CONFIG.get("trade_events_log", "data/trade_events.jsonl"))
_lock = threading.Lock()


# ── Internal ──────────────────────────────────────────────────────────────────


def _append(record: dict) -> None:
    """Write one record and fsync.  Caller must hold no locks."""
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with _lock:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def _load_all() -> list[dict]:
    """
    Read every record from the log.  A partial last line (crash artefact) is
    silently skipped — it cannot corrupt earlier records.
    """
    if not _LOG_FILE.exists():
        return []
    records: list[dict] = []
    with open(_LOG_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            is_last = lineno == len(lines)
            if not is_last:
                log.warning("event_log: corrupt record at line %d (not last line) — skipping", lineno)
    return records


# ── Write API ─────────────────────────────────────────────────────────────────


def append_intent(
    trade_id: str,
    symbol: str,
    *,
    direction: str,
    trade_type: str,
    instrument: str = "stock",
    intended_price: float,
    qty: int,
    sl: float,
    tp: float,
    regime: str,
    signal_scores: dict,
    conviction: float,
    reasoning: str,
    score: float = 0.0,
    open_time: str = "",
    **kwargs: object,
) -> None:
    """Write ORDER_INTENT before submitting to IBKR."""
    _append({
        "ts": datetime.now(UTC).isoformat(),
        "event": "ORDER_INTENT",
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "trade_type": trade_type,
        "instrument": instrument,
        "intended_price": intended_price,
        "qty": qty,
        "sl": sl,
        "tp": tp,
        "regime": regime,
        "signal_scores": signal_scores,
        "conviction": conviction,
        "reasoning": reasoning,
        "score": score,
        "open_time": open_time or datetime.now(UTC).isoformat(),
        **{k: v for k, v in kwargs.items() if k not in ("ts", "event")},
    })


def append_fill(
    trade_id: str,
    symbol: str,
    *,
    fill_price: float,
    fill_qty: int,
    order_id: int = 0,
) -> None:
    """Write ORDER_FILLED after IBKR confirms the entry fill."""
    _append({
        "ts": datetime.now(UTC).isoformat(),
        "event": "ORDER_FILLED",
        "trade_id": trade_id,
        "symbol": symbol,
        "fill_price": fill_price,
        "fill_qty": fill_qty,
        "order_id": order_id,
    })


def append_close(
    trade_id: str,
    symbol: str,
    *,
    exit_price: float,
    pnl: float,
    exit_reason: str,
    hold_minutes: int = 0,
) -> None:
    """Write POSITION_CLOSED after IBKR confirms the exit fill."""
    _append({
        "ts": datetime.now(UTC).isoformat(),
        "event": "POSITION_CLOSED",
        "trade_id": trade_id,
        "symbol": symbol,
        "exit_price": exit_price,
        "pnl": pnl,
        "exit_reason": exit_reason,
        "hold_minutes": hold_minutes,
    })


# ── Read API ──────────────────────────────────────────────────────────────────


def open_trades() -> dict[str, dict]:
    """
    Replay the log and return all confirmed-open positions.

    A position is confirmed-open when it has an ORDER_FILLED with no matching
    POSITION_CLOSED.  ORDER_INTENT-only entries (pending, unconfirmed) are NOT
    returned here — see pending_orders().

    Returns a dict keyed by trade_id.  Each value merges the ORDER_INTENT
    payload (metadata: signal_scores, trade_type, conviction, regime, …) with
    the ORDER_FILLED payload (actual fill price and qty).  The canonical entry
    price is fill_price, not intended_price.
    """
    intents: dict[str, dict] = {}
    fills: dict[str, dict] = {}
    closed: set[str] = set()

    for rec in _load_all():
        tid = rec.get("trade_id")
        if not tid:
            continue
        event = rec.get("event")
        if event == "ORDER_INTENT":
            intents[tid] = rec
        elif event == "ORDER_FILLED":
            fills[tid] = rec
        elif event == "POSITION_CLOSED":
            closed.add(tid)

    result: dict[str, dict] = {}
    for tid, fill in fills.items():
        if tid in closed:
            continue
        merged = dict(intents.get(tid, {}))
        merged.update(fill)
        # Canonical entry price = confirmed fill, not intended price.
        merged["entry"] = fill["fill_price"]
        merged["qty"] = fill["fill_qty"]
        result[tid] = merged

    return result


def pending_orders() -> list[dict]:
    """
    Return ORDER_INTENT records with no matching ORDER_FILLED or POSITION_CLOSED.

    These are orders submitted to IBKR but not yet confirmed as filled.
    On startup the bot checks these against IBKR to determine whether they
    filled while the bot was down and need an ORDER_FILLED appended, or were
    cancelled and need no action.
    """
    intents: dict[str, dict] = {}
    filled: set[str] = set()
    closed: set[str] = set()

    for rec in _load_all():
        tid = rec.get("trade_id")
        if not tid:
            continue
        event = rec.get("event")
        if event == "ORDER_INTENT":
            intents[tid] = rec
        elif event == "ORDER_FILLED":
            filled.add(tid)
        elif event == "POSITION_CLOSED":
            closed.add(tid)

    return [v for tid, v in intents.items() if tid not in filled and tid not in closed]


def get_intent(trade_id: str) -> dict:
    """Return the ORDER_INTENT payload for a trade_id, or {} if not found."""
    for rec in _load_all():
        if rec.get("trade_id") == trade_id and rec.get("event") == "ORDER_INTENT":
            return rec
    return {}


def last_intent_for_symbol(symbol: str) -> dict:
    """Return the most recent ORDER_INTENT for a symbol across all trade legs.

    Searches every record in the log — including intents for trades that have
    already been closed — so callers can recover trade_type metadata for positions
    that pre-date the current session (e.g. multi-leg re-entries, external fills).
    Returns {} if no matching intent exists.
    """
    best: dict = {}
    for rec in _load_all():
        if rec.get("event") == "ORDER_INTENT" and rec.get("symbol") == symbol:
            if not best or rec.get("timestamp", "") > best.get("timestamp", ""):
                best = rec
    return best
