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
    """
    try:
        from trade_store import persist
        with _trades_lock:
            persist(dict(active_trades))
    except Exception as e:
        log.error(f"trade_store persist failed: {e}")


# ── Thread-safe active_trades accessors ───────────────────────────────────────

def _safe_set_trade(key: str, value: dict) -> None:
    """Thread-safe write to active_trades dict. Persists if not a RESERVED placeholder."""
    with _trades_lock:
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


def _is_recently_closed(symbol: str) -> bool:
    """Return True if symbol was closed within reentry_cooldown_minutes."""
    ts_str = recently_closed.get(symbol)
    if not ts_str:
        return False
    cooldown = CONFIG.get("reentry_cooldown_minutes", 30)
    closed_at = datetime.fromisoformat(ts_str)
    return (datetime.now(timezone.utc) - closed_at).total_seconds() < cooldown * 60
