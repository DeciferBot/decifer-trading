# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  fill_watcher.py                            ║
# ║   Background order fill watcher — chases limit price         ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Per-symbol background thread that monitors a PENDING limit order after
placement and adjusts the price in small steps to chase a fill.

Supports both BUY and SELL entries:
  BUY  → chases price UP   (raises limit toward market ask)
  SELL → chases price DOWN  (lowers limit toward market bid)

State machine:
    PLACED → [initial_wait] → WATCHING → [interval] → ADJUSTING → WATCHING (loop)
                                    ↓ fill detected             ↓ ceiling or max_attempts
                                  FILLED                      CANCELLED

Started by execute_buy() and execute_short() in orders_core.py.
Stopped (externally) by stop_watcher() — called from execute_sell() and
_flatten_all_inner() before those functions cancel/close the position.
"""

import logging
import threading
import time
from datetime import UTC, datetime

from ib_async import LimitOrder, MarketOrder

from bot_ibkr import cancel_with_reason
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
    Watches a single PENDING limit order and adjusts its price to chase a fill.

    Supports both BUY and SELL entries. For BUY orders the limit is chased
    upward; for SELL orders (short entries) it is chased downward.

    Parameters
    ----------
    ib          : IB connection (main connection, clientId=10)
    symbol      : Ticker symbol (e.g. "AAPL", "USDJPY")
    order_id    : orderId of the entry leg (parent of the bracket)
    entry_trade : ib_async Trade object returned by ib.placeOrder() for the entry leg
    original_limit : Limit price at time of placement
    contract    : Already-qualified ib_async Contract object
    qty         : Number of shares / units
    side        : "BUY" or "SELL" — determines chase direction
    instrument  : "stock", "fx", etc. — used for audit logging
    """

    def __init__(
        self,
        ib,
        symbol: str,
        order_id: int,
        entry_trade,
        original_limit: float,
        contract,
        qty: int,
        watcher_params: dict | None = None,
        side: str = "BUY",
        instrument: str = "stock",
    ):
        self._ib = ib
        self._symbol = symbol
        self._order_id = order_id
        self._entry_trade = entry_trade
        self._original_limit = original_limit
        self._contract = contract
        self._qty = qty
        self._stop_event = threading.Event()
        self._watcher_params = watcher_params  # per-trade overrides from execution_agent
        self._side = side.upper()
        self._instrument = instrument
        # +1 for BUY (chase up), -1 for SELL (chase down)
        self._chase_sign = 1 if self._side == "BUY" else -1

    # ── Public entry point ─────────────────────────────────────────────────────

    def run(self) -> None:
        """Thread entry point. Runs entirely within a daemon thread."""
        _static = CONFIG.get("fill_watcher", {})
        if not _static.get("enabled", True):
            return
        # Use per-trade params injected by execution_agent; fall back to static CONFIG
        cfg = self._watcher_params if self._watcher_params else _static

        initial_wait = float(cfg.get("initial_wait_secs", 30))
        interval = float(cfg.get("interval_secs", 20))
        max_attempts = int(cfg.get("max_attempts", 3))
        step_pct = float(cfg.get("step_pct", 0.002))
        max_chase = float(cfg.get("max_chase_pct", 0.01))

        # BUY: ceiling above original (chase up).  SELL: floor below original (chase down).
        price_boundary = round(self._original_limit * (1 + self._chase_sign * max_chase), 2)
        current_limit = self._original_limit
        attempts = 0

        self._log_audit(
            "fill_watcher_started", original_limit=self._original_limit, price_boundary=price_boundary, side=self._side
        )
        _boundary_label = "ceiling" if self._side == "BUY" else "floor"
        log.info(
            f"FillWatcher started: {self._symbol} {self._side} limit=${self._original_limit:.2f} "
            f"{_boundary_label}=${price_boundary:.2f} max_attempts={max_attempts}"
        )

        # Phase 1: initial wait — give the order a chance to fill naturally
        _interruptible_sleep(initial_wait, self._stop_event)
        if self._stop_event.is_set():
            return

        # Phase 2: watch + adjust loop
        while attempts < max_attempts:
            if not self._ib.isConnected():
                self._log_audit("fill_watcher_aborted", reason="IBKR_DISCONNECTED", attempts=attempts)
                log.warning(f"FillWatcher: {self._symbol} aborted — IBKR disconnected")
                self._remove_from_registry()  # BUG FIX: registry was not cleaned on disconnect abort
                return

            if self._is_filled():
                self._log_audit("fill_watcher_filled", attempts=attempts, fill_limit=current_limit)
                log.info(f"FillWatcher: {self._symbol} filled after {attempts} adjustment(s)")
                self._remove_from_registry()
                return

            next_limit = round(current_limit * (1 + self._chase_sign * step_pct), 2)

            # BUY: next > ceiling means we've chased too high.
            # SELL: next < floor means we've chased too low.
            passed_boundary = (next_limit - price_boundary) * self._chase_sign > 0
            if passed_boundary:
                self._log_audit(
                    "fill_watcher_boundary_reached",
                    current_limit=current_limit,
                    price_boundary=price_boundary,
                    attempts=attempts,
                    side=self._side,
                )
                log.warning(
                    f"FillWatcher: {self._symbol} price {_boundary_label} ${price_boundary:.2f} reached "
                    f"— cancelling unfilled order"
                )
                self._cancel_order("price_ceiling_reached")
                return

            success = self._adjust_price(next_limit)
            if success:
                attempts += 1
                current_limit = next_limit
                self._log_audit("fill_watcher_adjusted", new_limit=next_limit, attempt=attempts)
            else:
                self._log_audit("fill_watcher_adjust_failed", attempted_limit=next_limit, attempt=attempts)
                log.warning(f"FillWatcher: {self._symbol} price adjustment failed — aborting loop")
                break

            _interruptible_sleep(interval, self._stop_event)
            if self._stop_event.is_set():
                return

        # Loop exited — do one final fill check before cancelling
        if self._is_filled():
            self._log_audit("fill_watcher_filled_late", attempts=attempts, fill_limit=current_limit)
            log.info(f"FillWatcher: {self._symbol} filled (detected post-loop) after {attempts} adjustment(s)")
            self._remove_from_registry()
            return

        self._log_audit("fill_watcher_max_attempts", attempts=attempts, final_limit=current_limit)
        log.warning(
            f"FillWatcher: {self._symbol} max attempts ({attempts}) exhausted at ${current_limit:.2f}"
        )
        _use_fallback = (
            CONFIG.get("fill_watcher", {}).get("market_fallback_on_max_attempts", False)
            and self._instrument == "stock"
        )
        if _use_fallback:
            self._fallback_to_market(current_limit)
        else:
            self._cancel_order("max_attempts_exhausted")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _is_filled(self) -> bool:
        """Return True if the order is no longer PENDING (filled, cancelled, or gone)."""
        # Lazy import to avoid circular dependency (orders imports fill_watcher)
        from orders_state import _trades_lock, active_trades

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
            return all(t.order.orderId != self._order_id for t in self._ib.openTrades())

        except Exception as exc:
            log.debug(f"FillWatcher._is_filled: {self._symbol} check error ({exc}) — assuming not filled")
            return False

    def _adjust_price(self, new_limit: float) -> bool:
        """Modify the live entry order to *new_limit*. Returns True on success."""

        try:
            if not self._ib.isConnected():
                return False

            # In ib_async, calling placeOrder with an existing orderId modifies the live order
            modified_order = LimitOrder(
                self._side,
                self._qty,
                new_limit,
                account=CONFIG["active_account"],
                tif="GTC",
                outsideRth=True,
            )
            modified_order.orderId = self._order_id
            modified_order.transmit = True

            self._ib.placeOrder(self._contract, modified_order)
            self._ib.sleep(0.3)

            log.info(f"FillWatcher: {self._symbol} limit adjusted ${self._original_limit:.2f} → ${new_limit:.2f}")
            return True

        except Exception as exc:
            log.error(f"FillWatcher._adjust_price: {self._symbol} failed: {exc}")
            return False

    def _cancel_order(self, reason: str) -> None:
        """Cancel the entry order and clean up all related state."""
        from orders_state import _safe_del_trade

        try:
            if self._ib.isConnected():
                try:
                    cancel_with_reason(self._ib, self._entry_trade.order, f"fill_watcher cancel: {reason}")
                    self._ib.sleep(0.5)
                except Exception as exc:
                    log.warning(f"FillWatcher: cancelOrder for {self._symbol} raised: {exc}")

                # Belt-and-suspenders: also cancel bracket children if still live
                try:
                    for t in self._ib.openTrades():
                        if getattr(t.order, "parentId", None) == self._order_id:
                            cancel_with_reason(self._ib, t.order, f"fill_watcher bracket child cancel: {reason}")
                except Exception as _bc_exc:
                    log.warning(
                        "FillWatcher._cancel_order: bracket child cancel failed for %s "
                        "(children may still be active): %s",
                        self._symbol, _bc_exc,
                    )

        except Exception as exc:
            log.error(f"FillWatcher._cancel_order: IBKR calls failed for {self._symbol}: {exc}")

        # Clean up position tracker
        try:
            _safe_del_trade(self._symbol)
        except Exception as exc:
            log.error(f"FillWatcher._cancel_order: _safe_del_trade failed for {self._symbol}: {exc}")

        # Log cancellation to orders.json
        try:
            log_order(
                {
                    "order_id": self._order_id,
                    "symbol": self._symbol,
                    "side": self._side,
                    "order_type": "LMT",
                    "qty": self._qty,
                    "price": self._original_limit,
                    "status": "CANCELLED",
                    "instrument": self._instrument,
                    "reason": reason,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as exc:
            log.error(f"FillWatcher._cancel_order: log_order failed for {self._symbol}: {exc}")

        self._log_audit("fill_watcher_cancelled", reason=reason)
        self._remove_from_registry()
        log.warning(f"FillWatcher: {self._symbol} order #{self._order_id} CANCELLED ({reason})")

    def _fallback_to_market(self, final_limit: float) -> None:
        """
        Convert an unexecuted limit entry to a market order after max chase attempts.
        Used in paper trading to guarantee a fill rather than losing the data point.
        Cancels the stale limit order (and any bracket children), then places a
        standalone market order. The portfolio manager handles exit from there.
        live: disable via market_fallback_on_max_attempts=False in config.
        """
        try:
            if self._ib.isConnected():
                # Cancel the stale limit order
                try:
                    cancel_with_reason(self._ib, self._entry_trade.order, f"fallback to market order for {self._symbol}")
                    self._ib.sleep(0.5)
                except Exception as exc:
                    log.warning(f"FillWatcher._fallback_to_market: cancelOrder for {self._symbol} raised: {exc}")

                # Cancel any bracket children (SL/TP) attached to this parent
                try:
                    for t in self._ib.openTrades():
                        if getattr(t.order, "parentId", None) == self._order_id:
                            cancel_with_reason(self._ib, t.order, f"fallback to market: bracket child cancel for {self._symbol}")
                except Exception as _bc_exc:
                    log.warning(
                        "FillWatcher._fallback_to_market: bracket child cancel failed for %s "
                        "(children may still be active): %s",
                        self._symbol, _bc_exc,
                    )

                # Place standalone order — MKT during regular session, LMT during extended hours.
                # IBKR queues MarketOrder outside 9:30 AM–4 PM ET; use an aggressive limit instead.
                from orders_contracts import is_options_market_open

                if is_options_market_open():
                    fallback_order = MarketOrder(self._side, self._qty, account=CONFIG["active_account"])
                    fallback_order.outsideRth = True
                else:
                    # Aggressive limit: +0.3% for BUY (chase ask), -0.3% for SELL (hit bid)
                    _sign = 1 if self._side == "BUY" else -1
                    _lmt = round(final_limit * (1 + _sign * 0.003), 2)
                    fallback_order = LimitOrder(self._side, self._qty, _lmt, account=CONFIG["active_account"])
                    fallback_order.outsideRth = True
                    fallback_order.tif = "GTC"
                    log.info(
                        f"FillWatcher._fallback: {self._symbol} extended-hours — "
                        f"using LimitOrder {self._side} @ ${_lmt:.2f} instead of MarketOrder"
                    )
                mkt_trade = self._ib.placeOrder(self._contract, fallback_order)
                self._ib.sleep(1.0)

                new_oid = mkt_trade.order.orderId
                if not new_oid:
                    # IBKR hasn't assigned an ID yet — not a rejection, just slow.
                    # Log with 0 so audit trail is complete; position stays tracked by symbol.
                    log.warning(f"FillWatcher._fallback_to_market: {self._symbol} MKT order has no orderId yet (will reconcile via sync)")
                log_order(
                    {
                        "order_id": new_oid,
                        "symbol": self._symbol,
                        "side": self._side,
                        "order_type": "MKT" if is_options_market_open() else "LMT",
                        "qty": self._qty,
                        "price": final_limit,
                        "status": "SUBMITTED",
                        "instrument": self._instrument,
                        "reason": "market_fallback_after_max_attempts",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
                self._log_audit("fill_watcher_market_fallback", final_limit=final_limit, new_order_id=new_oid)
                log.info(
                    f"FillWatcher: {self._symbol} converted to MKT order #{new_oid} after max chase attempts"
                )
            else:
                # IBKR disconnected — fall back to cancel path
                log.warning(f"FillWatcher._fallback_to_market: {self._symbol} — IBKR disconnected, cancelling instead")
                self._cancel_order("max_attempts_exhausted_disconnected")
                return
        except Exception as exc:
            log.error(f"FillWatcher._fallback_to_market: {self._symbol} failed: {exc} — falling back to cancel")
            self._cancel_order("max_attempts_exhausted")
            return

        # Clean up state (position tracker stays — market order is still pending a fill)
        self._remove_from_registry()

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
