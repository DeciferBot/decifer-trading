# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  fill_watcher.py                            ║
# ║   Background order fill watcher — chases limit price         ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Per-symbol background thread that monitors a PENDING buy limit order after
placement and adjusts the price upward in small steps to chase a fill.

State machine:
    PLACED → [initial_wait] → WATCHING → [interval] → ADJUSTING → WATCHING (loop)
                                    ↓ fill detected             ↓ ceiling or max_attempts
                                  FILLED                      CANCELLED

Started by execute_buy() in orders.py.
Stopped (externally) by stop_watcher() — called from execute_sell() and
_flatten_all_inner() before those functions cancel/close the position.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from ib_async import LimitOrder
from config import CONFIG
from learning import _append_audit_event, log_order

log = logging.getLogger("decifer.fill_watcher")

# ── Module-level watcher registry ─────────────────────────────────────────────
# Maps symbol → active FillWatcher so external callers can stop a watcher.
_active_watchers: dict[str, "FillWatcher"] = {}
_watchers_lock = threading.Lock()


def stop_watcher(symbol: str) -> None:
    """Signal the watcher for *symbol* to exit on its next sleep tick (within 0.5 s).

    Safe to call even if no watcher is running for the symbol.
    """
    with _watchers_lock:
        watcher = _active_watchers.pop(symbol, None)
    if watcher:
        watcher._stop_event.set()
        log.debug(f"FillWatcher: stop signal sent for {symbol}")


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> None:
    """Sleep for *seconds*, waking every 0.5 s to check *stop_event*."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return
        time.sleep(min(0.5, deadline - time.monotonic()))


# ── FillWatcher ────────────────────────────────────────────────────────────────

class FillWatcher:
    """
    Watches a single PENDING limit buy order and adjusts its price to chase a fill.

    Parameters
    ----------
    ib          : IB connection from execute_buy (main connection, clientId=10)
    symbol      : Ticker symbol (e.g. "AAPL")
    order_id    : orderId of the entry leg (parent of the bracket)
    entry_trade : ib_async Trade object returned by ib.placeOrder() for the entry leg
    original_limit : Limit price at time of placement
    contract    : Already-qualified ib_async Contract object
    qty         : Number of shares
    """

    def __init__(self, ib, symbol: str, order_id: int, entry_trade,
                 original_limit: float, contract, qty: int,
                 watcher_params: dict = None):
        self._ib             = ib
        self._symbol         = symbol
        self._order_id       = order_id
        self._entry_trade    = entry_trade
        self._original_limit = original_limit
        self._contract       = contract
        self._qty            = qty
        self._stop_event     = threading.Event()
        self._watcher_params = watcher_params  # per-trade overrides from execution_agent

    # ── Public entry point ─────────────────────────────────────────────────────

    def run(self) -> None:
        """Thread entry point. Runs entirely within a daemon thread."""
        _static = CONFIG.get("fill_watcher", {})
        if not _static.get("enabled", True):
            return
        # Use per-trade params injected by execution_agent; fall back to static CONFIG
        cfg = self._watcher_params if self._watcher_params else _static

        initial_wait = float(cfg.get("initial_wait_secs", 30))
        interval     = float(cfg.get("interval_secs", 20))
        max_attempts = int(cfg.get("max_attempts", 3))
        step_pct     = float(cfg.get("step_pct", 0.002))
        max_chase    = float(cfg.get("max_chase_pct", 0.01))

        max_limit     = round(self._original_limit * (1 + max_chase), 2)
        current_limit = self._original_limit
        attempts      = 0

        self._log_audit("fill_watcher_started",
                        original_limit=self._original_limit, max_limit=max_limit)
        log.info(f"FillWatcher started: {self._symbol} limit=${self._original_limit:.2f} "
                 f"ceiling=${max_limit:.2f} max_attempts={max_attempts}")

        # Phase 1: initial wait — give the order a chance to fill naturally
        _interruptible_sleep(initial_wait, self._stop_event)
        if self._stop_event.is_set():
            return

        # Phase 2: watch + adjust loop
        while attempts < max_attempts:
            if not self._ib.isConnected():
                self._log_audit("fill_watcher_aborted",
                                reason="IBKR_DISCONNECTED", attempts=attempts)
                log.warning(f"FillWatcher: {self._symbol} aborted — IBKR disconnected")
                return

            if self._is_filled():
                self._log_audit("fill_watcher_filled",
                                attempts=attempts, fill_limit=current_limit)
                log.info(f"FillWatcher: {self._symbol} filled after {attempts} adjustment(s)")
                self._remove_from_registry()
                return

            next_limit = round(current_limit * (1 + step_pct), 2)

            if next_limit > max_limit:
                self._log_audit("fill_watcher_ceiling_reached",
                                current_limit=current_limit, ceiling=max_limit,
                                attempts=attempts)
                log.warning(f"FillWatcher: {self._symbol} price ceiling ${max_limit:.2f} reached "
                            f"— cancelling unfilled order")
                self._cancel_order("price_ceiling_reached")
                return

            success = self._adjust_price(next_limit)
            if success:
                attempts += 1
                current_limit = next_limit
                self._log_audit("fill_watcher_adjusted",
                                new_limit=next_limit, attempt=attempts)
            else:
                self._log_audit("fill_watcher_adjust_failed",
                                attempted_limit=next_limit, attempt=attempts)
                log.warning(f"FillWatcher: {self._symbol} price adjustment failed — aborting loop")
                break

            _interruptible_sleep(interval, self._stop_event)
            if self._stop_event.is_set():
                return

        # Loop exited — do one final fill check before cancelling
        if self._is_filled():
            self._log_audit("fill_watcher_filled_late",
                            attempts=attempts, fill_limit=current_limit)
            log.info(f"FillWatcher: {self._symbol} filled (detected post-loop) after {attempts} adjustment(s)")
            self._remove_from_registry()
            return

        self._log_audit("fill_watcher_max_attempts",
                        attempts=attempts, final_limit=current_limit)
        log.warning(f"FillWatcher: {self._symbol} max attempts ({attempts}) exhausted "
                    f"at ${current_limit:.2f} — cancelling")
        self._cancel_order("max_attempts_exhausted")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _is_filled(self) -> bool:
        """Return True if the order is no longer PENDING (filled, cancelled, or gone)."""
        # Lazy import to avoid circular dependency (orders imports fill_watcher)
        from orders import active_trades, _trades_lock

        try:
            # Layer 1: check in-memory tracker (fastest, no IBKR call)
            with _trades_lock:
                entry = active_trades.get(self._symbol)

            if entry is None:
                # Removed from tracker — order resolved by some other path
                return True
            if entry.get("status") == "ACTIVE":
                # Main loop confirmed fill via IBKR portfolio
                return True

            # Layer 2: direct IBKR open-orders check
            for t in self._ib.openTrades():
                if t.order.orderId == self._order_id:
                    return False   # still live in IBKR — not yet filled
            # Parent orderId gone from open orders → filled or cancelled
            return True

        except Exception as exc:
            log.debug(f"FillWatcher._is_filled: {self._symbol} check error ({exc}) — assuming not filled")
            return False

    def _adjust_price(self, new_limit: float) -> bool:
        """Modify the live entry order to *new_limit*. Returns True on success."""
        from orders import active_trades, _trades_lock

        try:
            if not self._ib.isConnected():
                return False

            # In ib_async, calling placeOrder with an existing orderId modifies the live order
            modified_order = LimitOrder(
                "BUY", self._qty, new_limit,
                account=CONFIG["active_account"],
                tif="DAY",
                outsideRth=True,
            )
            modified_order.orderId   = self._order_id
            modified_order.transmit  = True

            self._ib.placeOrder(self._contract, modified_order)
            self._ib.sleep(0.3)

            log.info(f"FillWatcher: {self._symbol} limit adjusted "
                     f"${self._original_limit:.2f} → ${new_limit:.2f}")
            return True

        except Exception as exc:
            log.error(f"FillWatcher._adjust_price: {self._symbol} failed: {exc}")
            return False

    def _cancel_order(self, reason: str) -> None:
        """Cancel the entry order and clean up all related state."""
        from orders import active_trades, _trades_lock, _safe_del_trade

        try:
            if self._ib.isConnected():
                try:
                    self._ib.cancelOrder(self._entry_trade.order)
                    self._ib.sleep(0.5)
                except Exception as exc:
                    log.warning(f"FillWatcher: cancelOrder for {self._symbol} raised: {exc}")

                # Belt-and-suspenders: also cancel bracket children if still live
                try:
                    for t in self._ib.openTrades():
                        if getattr(t.order, "parentId", None) == self._order_id:
                            self._ib.cancelOrder(t.order)
                except Exception:
                    pass

        except Exception as exc:
            log.error(f"FillWatcher._cancel_order: IBKR calls failed for {self._symbol}: {exc}")

        # Clean up position tracker
        try:
            _safe_del_trade(self._symbol)
        except Exception as exc:
            log.error(f"FillWatcher._cancel_order: _safe_del_trade failed for {self._symbol}: {exc}")

        # Log cancellation to orders.json
        try:
            log_order({
                "order_id":   self._order_id,
                "symbol":     self._symbol,
                "side":       "BUY",
                "order_type": "LMT",
                "qty":        self._qty,
                "price":      self._original_limit,
                "status":     "CANCELLED",
                "instrument": "stock",
                "reason":     reason,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            log.error(f"FillWatcher._cancel_order: log_order failed for {self._symbol}: {exc}")

        self._log_audit("fill_watcher_cancelled", reason=reason)
        self._remove_from_registry()
        log.warning(f"FillWatcher: {self._symbol} order #{self._order_id} CANCELLED ({reason})")

    def _remove_from_registry(self) -> None:
        """Remove self from _active_watchers (idempotent)."""
        with _watchers_lock:
            _active_watchers.pop(self._symbol, None)

    def _log_audit(self, event_type: str, **fields) -> None:
        """Write one event to audit_log.jsonl."""
        try:
            _append_audit_event(
                event_type,
                symbol=self._symbol,
                order_id=self._order_id,
                **fields,
            )
        except Exception as exc:
            log.debug(f"FillWatcher._log_audit: {exc}")
