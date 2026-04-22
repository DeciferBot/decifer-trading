# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  trade_log.py                               ║
# ║   Permanent, append-only trade and signal event log          ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
SQLite WAL-backed event log.  Single source of truth for every trade and signal.

Design guarantees
-----------------
- SQLite WAL mode + synchronous=FULL: every committed row survives a crash.
- Append-only: rows are never updated or deleted.  State is always derivable
  by replaying events in seq order.
- Thread-safe: a module-level lock serialises all writes; WAL mode allows
  concurrent reads without blocking writers.

Two tables
----------
trade_events  — one row per lifecycle event (ORDER_INTENT → POSITION_CLOSED)
signal_scores — one row per symbol per scan cycle

Usage
-----
    from trade_log import append_event, close_trade, open_trades, append_signal

    # Write-ahead before order touches IBKR:
    trade_id = make_trade_id(symbol)
    append_event("ORDER_INTENT", trade_id, symbol, direction="LONG", ...)

    # When position closes:
    close_trade(trade_id, symbol, exit_price=195.0, pnl=432.0, exit_reason="TP")

    # On startup — rebuild active trades:
    active = open_trades()   # dict keyed by trade_id

    # After each scan cycle:
    append_signal(scan_id, symbol, score, direction, regime, breakdown)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from config import CONFIG

log = logging.getLogger("decifer.trade_log")

_DB_PATH = Path(CONFIG.get("trade_log_db", "data/decifer.db"))

_db_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    event    TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    payload  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_te_trade_id ON trade_events(trade_id);
CREATE INDEX IF NOT EXISTS idx_te_symbol   ON trade_events(symbol);
CREATE INDEX IF NOT EXISTS idx_te_event    ON trade_events(event);

CREATE TABLE IF NOT EXISTS signal_scores (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    scan_id   TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    score     INTEGER,
    direction TEXT,
    regime    TEXT,
    breakdown TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ss_symbol_ts ON signal_scores(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_ss_scan_id   ON signal_scores(scan_id);
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    with _db_lock:
        if _conn is not None:
            return _conn
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        c.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")
        c.executescript(_SCHEMA)
        c.commit()
        _conn = c
        log.info("trade_log: opened %s (WAL mode, synchronous=FULL)", _DB_PATH)
    return _conn


# ── Trade ID ─────────────────────────────────────────────────────────────────


def make_trade_id(symbol: str) -> str:
    """Generate a unique trade ID: {SYMBOL}_{YYYYMMDD_HHMMSS_ffffff}."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    return f"{symbol}_{ts}"


# ── Event append ─────────────────────────────────────────────────────────────


def append_event(event: str, trade_id: str, symbol: str, **payload) -> None:
    """
    Append one trade lifecycle event to trade_events table.

    event must be one of:
      ORDER_INTENT, ORDER_SUBMITTED, ORDER_FILLED,
      POSITION_UPDATED, T1_FILLED, POSITION_CLOSED

    Thread-safe.  Never raises — errors are logged and swallowed so a
    logging failure never kills a live order path.
    """
    ts = datetime.now(UTC).isoformat()
    row = json.dumps(payload, default=str)
    with _db_lock:
        try:
            c = _get_conn()
            c.execute(
                "INSERT INTO trade_events (ts, event, trade_id, symbol, payload) VALUES (?,?,?,?,?)",
                (ts, event, trade_id, symbol, row),
            )
            c.commit()
        except Exception as e:
            log.error("trade_log.append_event failed (%s %s): %s", event, trade_id, e)


# ── Convenience wrappers ──────────────────────────────────────────────────────


def close_trade(trade_id: str, symbol: str, exit_price: float, pnl: float,
                exit_reason: str, **extra) -> None:
    """Append POSITION_CLOSED.  After this, open_trades() will not return this trade."""
    append_event(
        "POSITION_CLOSED", trade_id, symbol,
        exit_price=exit_price, pnl=round(pnl, 2), exit_reason=exit_reason,
        **extra,
    )


def submit_order(trade_id: str, symbol: str, order_id: int, qty: int, limit_price: float, side: str) -> None:
    """Append ORDER_SUBMITTED after IBKR accepts the order."""
    append_event(
        "ORDER_SUBMITTED", trade_id, symbol,
        order_id=order_id, qty=qty, limit_price=limit_price, side=side,
    )


def record_fill(trade_id: str, symbol: str, fill_price: float, qty: int, order_id: int) -> None:
    """Append ORDER_FILLED when IBKR confirms the fill."""
    append_event(
        "ORDER_FILLED", trade_id, symbol,
        fill_price=fill_price, qty=qty, order_id=order_id,
    )


# ── Replay ───────────────────────────────────────────────────────────────────


def open_trades() -> dict[str, dict]:
    """
    Replay trade_events to find all ORDER_INTENT records with no matching
    POSITION_CLOSED.  Returns a dict keyed by trade_id; each value is the
    merged payload across all events for that trade (later events win on
    conflicting keys, except the ORDER_INTENT fields are preserved as-is).

    This is the authoritative startup recovery source.  On restart:
      1. Call open_trades() to get the set of trades the bot placed.
      2. Cross-reference with IBKR via reconcile_with_ibkr() for current price.
      3. positions.json is a cache; this is the truth.
    """
    try:
        c = _get_conn()
        rows = c.execute(
            "SELECT trade_id, event, symbol, payload FROM trade_events ORDER BY seq"
        ).fetchall()
    except Exception as e:
        log.error("trade_log.open_trades: query failed: %s", e)
        return {}

    intents: dict[str, dict] = {}
    closed: set[str] = set()

    for trade_id, event, symbol, payload_str in rows:
        try:
            payload = json.loads(payload_str)
        except Exception:
            log.warning("trade_log.open_trades: bad JSON for trade_id=%s event=%s — skipping", trade_id, event)
            continue

        if event == "ORDER_INTENT":
            intents[trade_id] = {"trade_id": trade_id, "symbol": symbol, **payload}
        elif event == "POSITION_CLOSED":
            closed.add(trade_id)
        elif trade_id in intents:
            # Merge subsequent events (fills, updates) into the state.
            # Preserve ORDER_INTENT fields — only add new keys or fill nulls.
            for k, v in payload.items():
                if k not in intents[trade_id] or intents[trade_id][k] is None:
                    intents[trade_id][k] = v

    return {tid: state for tid, state in intents.items() if tid not in closed}


def get_trade(trade_id: str) -> list[dict]:
    """Return all events for a trade in sequence order (for debugging / audit)."""
    try:
        c = _get_conn()
        rows = c.execute(
            "SELECT seq, ts, event, payload FROM trade_events WHERE trade_id=? ORDER BY seq",
            (trade_id,),
        ).fetchall()
        result = []
        for seq, ts, event, payload_str in rows:
            try:
                payload = json.loads(payload_str)
            except Exception:
                payload = {"_raw": payload_str}
            result.append({"seq": seq, "ts": ts, "event": event, **payload})
        return result
    except Exception as e:
        log.error("trade_log.get_trade failed for %s: %s", trade_id, e)
        return []


# ── Signal scores ─────────────────────────────────────────────────────────────


def append_signal(
    scan_id: str,
    symbol: str,
    score: int,
    direction: str,
    regime: str,
    breakdown: dict,
) -> None:
    """
    Write one signal record to signal_scores table.
    Called once per symbol per scan cycle.  Allows IC scoring to join
    signal scores to trade outcomes via (scan_id, symbol).
    """
    ts = datetime.now(UTC).isoformat()
    breakdown_str = json.dumps(breakdown, default=str)
    with _db_lock:
        try:
            c = _get_conn()
            c.execute(
                "INSERT INTO signal_scores (ts, scan_id, symbol, score, direction, regime, breakdown)"
                " VALUES (?,?,?,?,?,?,?)",
                (ts, scan_id, symbol, score, direction, regime, breakdown_str),
            )
            c.commit()
        except Exception as e:
            log.error("trade_log.append_signal failed (%s): %s", symbol, e)
