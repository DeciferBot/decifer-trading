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

-- Active positions: DB-primary, positions.json is fallback only.
-- Full position state is stored as a JSON blob in `data` so no schema migration
-- is needed when new fields are added to positions.
CREATE TABLE IF NOT EXISTS positions (
    key        TEXT PRIMARY KEY,
    symbol     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pos_symbol ON positions(symbol);

-- Closed trade records: replaces trades.json as the write-primary store.
CREATE TABLE IF NOT EXISTS closed_trades (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    trade_type TEXT,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ct_symbol ON closed_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_ct_ts     ON closed_trades(ts);

-- Pending option exits queued while market is closed.
CREATE TABLE IF NOT EXISTS pending_exits (
    opt_key    TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    queued_at  TEXT NOT NULL
);

-- Audit log: all order, IC, voice, and exception events.
CREATE TABLE IF NOT EXISTS audit_events (
    seq   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT NOT NULL,
    event TEXT NOT NULL,
    data  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ae_event ON audit_events(event);
CREATE INDEX IF NOT EXISTS idx_ae_ts    ON audit_events(ts);
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


def find_order_intent(symbol: str) -> dict:
    """
    Return the payload of the most recent ORDER_INTENT for symbol that has no
    matching POSITION_CLOSED.  Used as Tier 3 in _find_saved() so that metadata
    written to the DB before a crash can be recovered even when positions.json
    and metadata_ledger.json are stale or empty.

    Returns {} if nothing is found.
    """
    try:
        c = _get_conn()
        rows = c.execute(
            "SELECT trade_id, event, payload FROM trade_events "
            "WHERE symbol=? ORDER BY seq",
            (symbol,),
        ).fetchall()
    except Exception as e:
        log.error("trade_log.find_order_intent failed for %s: %s", symbol, e)
        return {}

    intent: dict | None = None
    intent_trade_id: str | None = None
    for trade_id, event, payload_str in rows:
        if event == "ORDER_INTENT":
            try:
                intent = json.loads(payload_str)
                intent_trade_id = trade_id
            except Exception:
                pass
        elif event == "POSITION_CLOSED" and trade_id == intent_trade_id:
            intent = None
            intent_trade_id = None

    if intent and intent.get("trade_type") and intent["trade_type"] != "UNKNOWN":
        return intent
    return {}


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


# ── Positions (DB-primary, positions.json is fallback only) ──────────────────


def upsert_position(key: str, symbol: str, position: dict) -> None:
    """Write or update an active position.  Called on every mutation."""
    ts = datetime.now(UTC).isoformat()
    data = json.dumps(position, default=str)
    with _db_lock:
        try:
            c = _get_conn()
            c.execute(
                "INSERT INTO positions (key, symbol, updated_at, data) VALUES (?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET symbol=excluded.symbol,"
                " updated_at=excluded.updated_at, data=excluded.data",
                (key, symbol, ts, data),
            )
            c.commit()
        except Exception as e:
            log.error("trade_log.upsert_position failed (%s): %s", key, e)


def delete_position(key: str) -> None:
    """Remove a position from the DB when it is closed or cancelled."""
    with _db_lock:
        try:
            c = _get_conn()
            c.execute("DELETE FROM positions WHERE key=?", (key,))
            c.commit()
        except Exception as e:
            log.error("trade_log.delete_position failed (%s): %s", key, e)


def load_positions() -> dict[str, dict]:
    """
    Load all active positions from DB.  Returns a dict keyed by position key.
    Returns {} on any error so the caller can fall back to positions.json.
    """
    try:
        c = _get_conn()
        rows = c.execute("SELECT key, data FROM positions").fetchall()
        result = {}
        for key, data_str in rows:
            try:
                result[key] = json.loads(data_str)
            except Exception:
                log.warning("trade_log.load_positions: bad JSON for key=%s — skipping", key)
        return result
    except Exception as e:
        log.error("trade_log.load_positions failed: %s", e)
        return {}


def replace_all_positions(snapshot: dict[str, dict]) -> None:
    """Atomically replace the entire positions table from a snapshot dict."""
    ts = datetime.now(UTC).isoformat()
    with _db_lock:
        try:
            c = _get_conn()
            c.execute("DELETE FROM positions")
            for key, pos in snapshot.items():
                symbol = pos.get("symbol", key)
                data = json.dumps(pos, default=str)
                c.execute(
                    "INSERT INTO positions (key, symbol, updated_at, data) VALUES (?,?,?,?)",
                    (key, symbol, ts, data),
                )
            c.commit()
        except Exception as e:
            log.error("trade_log.replace_all_positions failed: %s", e)


# ── Closed trades ─────────────────────────────────────────────────────────────


def append_closed_trade(symbol: str, trade_type: str | None, record: dict) -> None:
    """Append a closed trade record.  Replaces writes to trades.json."""
    ts = datetime.now(UTC).isoformat()
    data = json.dumps(record, default=str)
    with _db_lock:
        try:
            c = _get_conn()
            c.execute(
                "INSERT INTO closed_trades (ts, symbol, trade_type, data) VALUES (?,?,?,?)",
                (ts, symbol, trade_type or "UNKNOWN", data),
            )
            c.commit()
        except Exception as e:
            log.error("trade_log.append_closed_trade failed (%s): %s", symbol, e)


def load_closed_trades(symbol: str | None = None, limit: int = 0) -> list[dict]:
    """Load closed trades from DB, optionally filtered by symbol."""
    try:
        c = _get_conn()
        if symbol:
            rows = c.execute(
                "SELECT data FROM closed_trades WHERE symbol=? ORDER BY seq DESC"
                + (f" LIMIT {int(limit)}" if limit else ""),
                (symbol,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT data FROM closed_trades ORDER BY seq DESC"
                + (f" LIMIT {int(limit)}" if limit else "")
            ).fetchall()
        result = []
        for (data_str,) in rows:
            try:
                result.append(json.loads(data_str))
            except Exception:
                pass
        return result
    except Exception as e:
        log.error("trade_log.load_closed_trades failed: %s", e)
        return []


# ── Pending option exits ──────────────────────────────────────────────────────


def upsert_pending_exit(opt_key: str, reason: str) -> None:
    """Queue a pending option exit."""
    ts = datetime.now(UTC).isoformat()
    with _db_lock:
        try:
            c = _get_conn()
            c.execute(
                "INSERT INTO pending_exits (opt_key, reason, queued_at) VALUES (?,?,?)"
                " ON CONFLICT(opt_key) DO UPDATE SET reason=excluded.reason",
                (opt_key, reason, ts),
            )
            c.commit()
        except Exception as e:
            log.error("trade_log.upsert_pending_exit failed (%s): %s", opt_key, e)


def delete_pending_exit(opt_key: str) -> None:
    with _db_lock:
        try:
            c = _get_conn()
            c.execute("DELETE FROM pending_exits WHERE opt_key=?", (opt_key,))
            c.commit()
        except Exception as e:
            log.error("trade_log.delete_pending_exit failed (%s): %s", opt_key, e)


def load_pending_exits() -> dict[str, str]:
    """Return {opt_key: reason} for all pending exits."""
    try:
        c = _get_conn()
        rows = c.execute("SELECT opt_key, reason FROM pending_exits").fetchall()
        return {k: r for k, r in rows}
    except Exception as e:
        log.error("trade_log.load_pending_exits failed: %s", e)
        return {}


# ── Audit events ──────────────────────────────────────────────────────────────


def append_audit(event: str, **fields) -> None:
    """Append an audit event.  Replaces writes to audit_log.jsonl."""
    ts = datetime.now(UTC).isoformat()
    data = json.dumps({"ts": ts, "event": event, **fields}, default=str)
    with _db_lock:
        try:
            c = _get_conn()
            c.execute(
                "INSERT INTO audit_events (ts, event, data) VALUES (?,?,?)",
                (ts, event, data),
            )
            c.commit()
        except Exception as e:
            log.error("trade_log.append_audit failed (%s): %s", event, e)
