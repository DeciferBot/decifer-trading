# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders_state.py                            ║
# ║   Shared mutable state for the orders subsystem              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Leaf module — no imports from other orders_* modules.
Owns all shared state and thread-safe accessors.
Everything here is imported by orders_contracts, orders_guards,
orders_core, orders_portfolio, and orders.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from config import CONFIG

log = logging.getLogger("decifer.orders")

# ── File paths (patchable in tests) ───────────────────────────────────────────
TRADES_FILE: str = CONFIG.get("trade_log", "/tmp/trades.json")
ORDERS_FILE: str = CONFIG.get("order_log", "/tmp/orders.json")
POSITIONS_FILE: str = CONFIG.get("positions_file", "data/positions.json")

# ── In-memory position tracker ────────────────────────────────────────────────
# Source of truth = trade_store (data/positions.json).
# IBKR reconciles live price/fill-status on top of this at startup.
active_trades: dict = {}
open_trades = active_trades  # backward-compat alias (both names point to same dict)

# ── In-memory open-order tracker keyed by symbol ─────────────────────────────
open_orders: dict = {}

# ── Post-close cooldown registry ─────────────────────────────────────────────
# symbol → ISO timestamp of close.
# Blocks re-entry for reentry_cooldown_minutes after a position is closed.
recently_closed: dict = {}

# ── Duplicate-check default (flag only — logic lives in orders_guards) ────────
ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT = True

# ── Thread-safe guard for active_trades dictionary ────────────────────────────
# execute_buy/execute_sell run on the main async event loop;
# reconcile_with_ibkr and update_positions_from_ibkr run from background threads.
# RLock is safe from both contexts. Lock scope is narrowed to dict operations only —
# never around broker network calls — so a slow reconcile never blocks a live order.
_trades_lock = threading.RLock()

# ── Re-entrancy guard for flatten_all ────────────────────────────────────────
# If two callers race (e.g. drawdown fires while kill-switch is already running),
# only the first should proceed.
_flatten_lock = threading.Lock()
_flatten_in_progress: bool = False

# ── Per-symbol lock registry ───────────────────────────────────────────────────
# Closes the TOCTOU gap between openOrders check and submission.
_symbol_locks: dict = {}
_symbol_locks_mutex = threading.Lock()


def _get_symbol_lock(symbol: str) -> threading.Lock:
    """Return a per-symbol lock, creating it if necessary."""
    with _symbol_locks_mutex:
        if symbol not in _symbol_locks:
            _symbol_locks[symbol] = threading.Lock()
        return _symbol_locks[symbol]


# ── Persist helper ────────────────────────────────────────────────────────────

def _persist_positions() -> None:
    """
    Write active_trades snapshot to data/positions.json.
    Called after every structural mutation (entry, exit, SL/TP update, status change).
    Price/pnl ticks are NOT persisted — IBKR re-provides them on reconciliation.
    Errors are logged but never raised so a disk problem never kills a live trade.

    Lock discipline: snapshot is taken under the lock, but the actual I/O (persist)
    runs OUTSIDE the lock so a slow disk write never blocks the order execution path.
    """
    try:
        from trade_store import persist
        with _trades_lock:
            snapshot = dict(active_trades)   # O(n) copy, fast — lock scope ends here
        persist(snapshot)                    # I/O outside lock
    except Exception as e:
        log.error(f"trade_store persist failed: {e}")


# ── Decision metadata — these fields are written ONCE at trade entry ──────────
# No function — IBKR reconciliation, re-sync, portfolio updates — may overwrite
# these after they are set.  _safe_set_trade enforces this at the storage layer.
DECISION_METADATA_FIELDS: frozenset = frozenset({
    "trade_type",
    "conviction",
    "reasoning",
    "signal_scores",
    "agent_outputs",
    "entry_regime",
    "entry_thesis",
    "entry_score",
    "ic_weights_at_entry",
    "pattern_id",
    "setup_type",
    "advice_id",
    "open_time",
    "atr",
    "high_water_mark",
})


# ── Thread-safe active_trades accessors ───────────────────────────────────────

def _safe_set_trade(key: str, value: dict) -> None:
    """
    Thread-safe write to active_trades dict.  Persists if not a RESERVED placeholder.

    METADATA IMMUTABILITY GUARD
    If an existing entry for `key` already has real decision metadata
    (trade_type is set and not "UNKNOWN"), this function will NEVER overwrite
    those fields — even if the caller passes a new dict without them.
    IBKR reconciliation, re-sync, and all other callers are bound by this rule.

    The only way to write decision metadata is on first entry (when the position
    does not yet exist, or when it currently has trade_type="UNKNOWN").
    """
    with _trades_lock:
        existing = active_trades.get(key)
        existing_has_metadata = (
            existing is not None
            and existing.get("trade_type")
            and existing["trade_type"] != "UNKNOWN"
        )
        if existing_has_metadata:
            # Preserve all decision metadata from the existing record.
            # Only allow IBKR-reconcile fields (price, pnl, status, sources) to update.
            protected = {
                f: existing[f]
                for f in DECISION_METADATA_FIELDS
                if f in existing
            }
            merged = {**value, **protected}
            if merged != value:
                log.debug(
                    f"_safe_set_trade({key}): metadata guard preserved "
                    f"{set(protected) & set(value)} from overwrite"
                )
            active_trades[key] = merged
        else:
            active_trades[key] = value
    if value.get("status") != "RESERVED":
        _persist_positions()


def _safe_update_trade(key: str, updates: dict) -> None:
    """
    Thread-safe partial update of an active_trades entry.
    Persists only when the update touches structural fields (sl, tp, order IDs,
    status, qty). Price/pnl ticks are excluded — too frequent, transient.
    """
    from trade_store import STRUCTURAL_UPDATE_KEYS
    with _trades_lock:
        if key in active_trades:
            active_trades[key].update(updates)
    if updates.keys() & STRUCTURAL_UPDATE_KEYS:
        _persist_positions()


def _safe_del_trade(key: str) -> None:
    """Thread-safe delete from active_trades dict. Always persists."""
    with _trades_lock:
        active_trades.pop(key, None)
    _persist_positions()


def _save_positions_file() -> None:
    """Persist active_trades metadata to disk so it survives bot restarts.
    Atomic write: snapshot under lock, then write outside lock."""
    import json
    import os
    import tempfile
    try:
        with _trades_lock:
            snapshot = {
                k: v for k, v in active_trades.items()
                if v.get("status") != "RESERVED"
            }
        dir_name = os.path.dirname(os.path.abspath(POSITIONS_FILE))
        os.makedirs(dir_name, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, suffix=".tmp") as f:
            json.dump(snapshot, f, default=str)
            tmp_path = f.name
        os.replace(tmp_path, POSITIONS_FILE)
    except Exception as e:
        log.warning(f"_save_positions_file failed: {e}")


def _load_positions_file() -> dict:
    """Load persisted position metadata. Returns empty dict if file missing or corrupt."""
    import json
    import os
    try:
        if not os.path.exists(POSITIONS_FILE):
            return {}
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        log.warning(f"_load_positions_file failed: {e}")
    return {}


def _is_recently_closed(symbol: str) -> bool:
    """Return True if symbol was closed within reentry_cooldown_minutes."""
    ts_str = recently_closed.get(symbol)
    if not ts_str:
        return False
    cooldown = CONFIG.get("reentry_cooldown_minutes", 30)
    closed_at = datetime.fromisoformat(ts_str)
    return (datetime.now(timezone.utc) - closed_at).total_seconds() < cooldown * 60


def cleanup_recently_closed() -> int:
    """
    Evict stale entries from recently_closed that are beyond 2× the cooldown window.
    Called from the main scan loop to prevent unbounded dict growth over long sessions.
    Returns the number of entries removed.
    """
    cooldown_secs = CONFIG.get("reentry_cooldown_minutes", 30) * 60
    cutoff_secs   = cooldown_secs * 2
    now           = datetime.now(timezone.utc)
    expired = [
        sym for sym, ts_str in recently_closed.items()
        if (now - datetime.fromisoformat(ts_str)).total_seconds() > cutoff_secs
    ]
    for sym in expired:
        recently_closed.pop(sym, None)
    if expired:
        log.debug(f"recently_closed: evicted {len(expired)} stale entries {expired}")
    return len(expired)
