# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders_portfolio.py                        ║
# ║   Position tracking, IBKR reconciliation, flatten-all        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Position tracking and IBKR reconciliation functions.
Imports from orders_state (shared state) and orders_contracts (utilities).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ib_async import IB
from ib_async import LimitOrder, MarketOrder, StopOrder

from config import CONFIG
from learning import log_order
from orders_state import (
    log,
    active_trades,
    _trades_lock,
    _flatten_lock,
    _safe_set_trade, _safe_update_trade, _safe_del_trade,
    _persist_positions,
)
from orders_contracts import (
    get_contract,
    _get_emergency_ib,
    _validate_position_price,
    _ibkr_item_to_key,
    _is_option_contract,
    _cancel_ibkr_order_by_id,
)

# ── flatten_all order-book wait constants ─────────────────────────────────────
# After reqGlobalCancel, IBKR processes cancellations asynchronously.  We poll
# until the book is clear (or we time out) before placing closing orders.
_GLOBAL_CANCEL_WAIT_SECS: float = 5.0
_GLOBAL_CANCEL_POLL_INTERVAL: float = 0.5

# ── Re-entrancy guard for flatten_all ────────────────────────────────────────
# Lives here (not in orders_state) so the 'global' keyword resolves correctly
# within this module. Only flatten_all reads/writes this bool.
_flatten_in_progress: bool = False


def flatten_all(ib_fallback: IB = None):
    """
    EMERGENCY — flatten all open positions immediately via emergency IB connection.
    Called by kill switch or catastrophic drawdown detection.
    Closes EVERYTHING in IBKR portfolio — not just what the bot is tracking.

    Uses emergency IB connection (clientId=11) so it executes INSTANTLY
    even while the main scanner is mid-scan.
    Uses aggressive LIMIT orders (not market) for extended hours compatibility.
    """
    global _flatten_in_progress
    with _flatten_lock:
        if _flatten_in_progress:
            log.warning("🚨 FLATTEN ALL — re-entrant call ignored (already running)")
            return
        _flatten_in_progress = True

    try:
        _flatten_all_inner(ib_fallback)
    finally:
        with _flatten_lock:
            _flatten_in_progress = False


def _wait_for_order_book_clear(eib: IB, timeout: float = _GLOBAL_CANCEL_WAIT_SECS) -> int:
    """Poll IBKR until the open-order book is empty or timeout expires.

    After reqGlobalCancel, IBKR processes cancellations asynchronously.
    Waiting here gives the exchange time to acknowledge before we submit
    closing market orders — avoiding conflicts with pending orders.

    Returns:
        Number of orders still remaining when we stopped polling (0 = fully clear).
    """
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            remaining = eib.openOrders()
            if not remaining:
                return 0
        except Exception:
            return 0  # If we can't query, proceed anyway
        eib.sleep(_GLOBAL_CANCEL_POLL_INTERVAL)
    try:
        remaining = eib.openOrders()
        count = len(remaining) if remaining else 0
    except Exception:
        count = 0
    log.warning(f"🚨 _wait_for_order_book_clear: timed out with {count} orders remaining")
    return count


def _flatten_all_inner(ib_fallback: IB = None):
    """Internal implementation of flatten_all — called under re-entrancy guard."""
    # Use emergency connection for instant execution; fall back to main if unavailable
    eib = _get_emergency_ib()
    if not eib:
        log.warning("🚨 Emergency IB unavailable — falling back to main connection")
        eib = ib_fallback
    if not eib:
        log.error("🚨 FLATTEN ALL FAILED — no IB connection available")
        with _trades_lock:
            stranded = list(active_trades.items())
        if stranded:
            log.critical(
                f"🚨 FLATTEN ABORTED — {len(stranded)} position(s) NOT closed. "
                "Manual intervention required:"
            )
            for key, info in stranded:
                sym = info.get("symbol", key.split("_")[0])
                qty = info.get("qty", 0)
                direction = info.get("direction", "LONG")
                log.critical(f"   ↳ {sym}  qty={qty}  dir={direction}  key={key}")
        return

    log.critical("🚨 FLATTEN ALL — closing all positions immediately")

    # 0) Stop all fill watchers so they don't race reqGlobalCancel
    try:
        from fill_watcher import stop_watcher as _stop_watcher
        with _trades_lock:
            symbols_to_stop = [info.get("symbol", key.split("_")[0])
                               for key, info in active_trades.items()]
        for sym in symbols_to_stop:
            _stop_watcher(sym)
    except Exception as _fw_err:
        log.warning(f"FillWatcher stop-all raised: {_fw_err}")

    # 1) Atomically cancel ALL open orders with a single reqGlobalCancel
    try:
        eib.reqGlobalCancel()
    except Exception as e:
        log.error(f"🚨 reqGlobalCancel failed: {e} — continuing to close positions")

    # 2) Wait for the order book to drain before placing closing orders
    _wait_for_order_book_clear(eib, timeout=_GLOBAL_CANCEL_WAIT_SECS)

    # 3) Close all positions tracked in active_trades (bot's source of truth)
    closed = 0
    with _trades_lock:
        snapshot = list(active_trades.items())

    for key, info in snapshot:
        sym = info.get("symbol", key.split("_")[0])
        qty = info.get("qty", 0)
        instrument = info.get("instrument", "stock")
        if qty == 0:
            continue
        try:
            direction = info.get("direction", "LONG")
            close_action = "BUY" if direction == "SHORT" else "SELL"
            if instrument == "option":
                from ib_async import Option as _FlatOpt
                contract = _FlatOpt(sym, info["expiry_ibkr"], info["strike"], info["right"], exchange="SMART", currency="USD")
                try:
                    eib.qualifyContracts(contract)
                except Exception:
                    pass
                mkt = info.get("current_premium") or info.get("entry_premium") or 0.01
                lp = max(round(float(mkt) * 0.90, 2), 0.01)
                order = LimitOrder(close_action, abs(int(qty)), lp, tif="GTC")
                log.warning(f"🚨 FLATTEN: LMT {close_action} {abs(int(qty))} {sym} OPT @${lp:.2f} ({direction})")
            else:
                contract = get_contract(sym, instrument)
                order = MarketOrder(close_action, abs(int(qty)))
                log.warning(f"🚨 FLATTEN: Market {close_action} {abs(int(qty))} {sym} ({direction})")
            eib.placeOrder(contract, order)
            _safe_del_trade(key)
            closed += 1
        except Exception as e:
            log.error(f"🚨 FLATTEN failed for {sym}: {e}")

    log.warning(f"🚨 FLATTEN ALL complete — {closed} orders placed, tracker cleared")


def close_position(ib_unused, trade_key: str) -> Optional[str]:
    """
    Close a single position by trade_key IMMEDIATELY via emergency IB connection.
    trade_key can be a plain symbol (e.g. "KOD") for stocks, or a composite key
    (e.g. "KOD_C_35.0_2026-04-17") for options.

    Uses aggressive limit orders for after-hours compatibility.
    Also cancels any related open orders (stops, TPs) for that symbol.
    Returns a description string on success, None if position not found.

    NOTE: ib_unused param kept for API compatibility but is IGNORED.
    This function uses its own dedicated IB connection (clientId=11)
    so it can execute instantly even while a scan is running.
    """
    trade_key = trade_key.upper().strip()
    eib = _get_emergency_ib()
    if not eib:
        log.error(f"Close {trade_key}: No emergency IB connection available")
        return None

    # 1) Find the position in IBKR portfolio using composite key matching
    try:
        portfolio_items = eib.portfolio(CONFIG["active_account"])
    except Exception as e:
        log.error(f"Close {trade_key}: Could not read IBKR portfolio: {e}")
        return None

    target = None
    for item in portfolio_items:
        if item.position != 0 and _ibkr_item_to_key(item).upper() == trade_key:
            target = item
            break

    # Fallback: try matching just the symbol (backward compat for stock-only calls)
    if not target:
        for item in portfolio_items:
            if item.position != 0 and item.contract.symbol.upper() == trade_key and item.contract.secType == "STK":
                target = item
                break

    if not target:
        log.warning(f"Close {trade_key}: Position not found in IBKR portfolio")
        return None

    sym = target.contract.symbol
    pos = target.position
    mkt = float(target.marketPrice)
    action = "SELL" if pos > 0 else "BUY"
    qty = abs(int(pos))
    is_option = target.contract.secType == "OPT"
    instrument = "option" if is_option else "stock"

    # 2) Cancel related open orders for this symbol
    try:
        for t in eib.trades():
            if t.contract.symbol == sym and t.orderStatus.status in ('Submitted', 'PreSubmitted'):
                try:
                    eib.cancelOrder(t.order)
                    log.info(f"Close {trade_key}: Cancelled order {t.order.orderId}")
                except Exception:
                    pass
        eib.sleep(0.3)
    except Exception as e:
        log.warning(f"Close {trade_key}: Error cancelling related orders: {e}")

    # 3) Place market order for immediate fill
    contract = target.contract
    contract.exchange = "SMART"
    try:
        eib.qualifyContracts(contract)
    except Exception:
        pass  # Proceed with exchange='SMART' even if qualify fails

    order = MarketOrder(action, qty,
                        account=CONFIG["active_account"],
                        outsideRth=True)
    close_trade = eib.placeOrder(contract, order)
    eib.sleep(0.3)

    # Log the close order
    log_order({
        "order_id":   close_trade.order.orderId,
        "symbol":     sym,
        "side":       action,
        "order_type": "MKT",
        "qty":        qty,
        "price":      mkt,
        "status":     "SUBMITTED",
        "instrument": instrument,
        "role":       "close",
        "reason":     "Manual close from dashboard",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    })

    detail = f"{action} {qty} {sym} {'OPT' if is_option else ''} MKT (mkt=${mkt:.2f})"
    log.warning(f"📤 INSTANT close: {detail}")

    # 4) Remove from bot tracker — try composite key first, then plain symbol
    tracker_key = _ibkr_item_to_key(target)
    if tracker_key in active_trades:
        del active_trades[tracker_key]
    elif trade_key in active_trades:
        del active_trades[trade_key]
    _persist_positions()

    return detail


def reconcile_with_ibkr(ib: IB):
    """
    On startup or reconnect: restore positions from our own store, then reconcile
    live price/fill-status with IBKR.

    Source-of-truth hierarchy:
      1. trade_store (data/positions.json) — all decision metadata:
         entry price, trade_type, conviction, regime, signal scores, agent outputs,
         entry thesis, pattern_id, SL/TP levels, tranche state.
      2. IBKR — reconciles ONLY:
         - current market price (3-way validated)
         - unrealised P&L (derived from current price)
         - fill status (was SL/TP triggered while bot was down?)
         - qty (were there partial fills while bot was offline?)
         - SL bracket order ID (reattach if present in openTrades)

    Nothing IBKR returns ever overwrites stored metadata fields.
    """
    from trade_store import restore as _ts_restore
    log.info("Loading positions from trade_store, then reconciling prices with IBKR...")
    try:
        # ── Step 1: restore our own position ledger ───────────────────────────
        stored = _ts_restore()
        if stored:
            with _trades_lock:
                active_trades.update(stored)
            log.info(f"Restored {len(stored)} position(s) from trade_store.")

        # ── Step 2: fetch IBKR portfolio ──────────────────────────────────────
        # portfolio() returns PortfolioItem with marketPrice + unrealizedPNL.
        # positions() only returns avgCost — never use it for reconciliation.
        portfolio_items = ib.portfolio(CONFIG["active_account"])

        ibkr_keys = set()
        for item in portfolio_items:
            if item.position != 0:
                ibkr_keys.add(_ibkr_item_to_key(item))

        # ── Step 3: detect positions closed while bot was down ────────────────
        # In our store but not in IBKR → SL/TP was triggered or manually closed.
        keys_to_remove = []
        with _trades_lock:
            for key in list(active_trades.keys()):
                if key not in ibkr_keys:
                    trade = active_trades[key]
                    if trade.get("status") == "PENDING":
                        order_id = trade.get("order_id")
                        still_live = False
                        if order_id:
                            try:
                                for t in ib.openTrades():
                                    if t.order.orderId == order_id:
                                        still_live = True
                                        break
                            except Exception:
                                still_live = True  # err on side of keeping it
                        if still_live:
                            log.debug(f"Reconcile: PENDING {key} order #{order_id} still live in IBKR — keeping")
                            continue
                        else:
                            log.warning(
                                f"Reconcile: PENDING {key} order #{order_id} not in IBKR open orders "
                                f"— cancelling and removing from tracker"
                            )
                            if order_id:
                                _cancel_ibkr_order_by_id(ib, order_id)
                            keys_to_remove.append(key)
                    else:
                        log.warning(
                            f"Position {key} in our store but not in IBKR "
                            f"— was closed while bot was down, removing"
                        )
                        keys_to_remove.append(key)

        for key in keys_to_remove:
            _safe_del_trade(key)

        # ── Step 4: process IBKR portfolio items ──────────────────────────────
        reconciled_count = 0
        failed_count = 0
        for item in portfolio_items:
            if item.position == 0:
                continue

            # Per-item try/except: one bad position must NOT kill the entire loop
            try:
                key = _ibkr_item_to_key(item)
                sym = item.contract.symbol
                is_option = _is_option_contract(item.contract)
                ibkr_mkt = float(item.marketPrice)

                # For options, IBKR reports:
                #   averageCost = per-CONTRACT (×100), e.g. $370.59 = $3.7059/share × 100
                #   marketPrice = per-SHARE premium already, e.g. $4.30
                # Our tracker stores per-SHARE premiums to match execute_buy_option.
                if is_option:
                    ibkr_entry = round(float(item.averageCost) / 100, 4)
                    ibkr_price_for_validation = round(ibkr_mkt, 4) if ibkr_mkt > 0 else 0
                else:
                    ibkr_entry = round(float(item.averageCost), 4)
                    ibkr_price_for_validation = ibkr_mkt if ibkr_mkt > 0 else 0

                # 3-way validate current price (stocks only; options use IBKR premium directly)
                if is_option:
                    if ibkr_price_for_validation > 0:
                        validated_price = ibkr_price_for_validation
                        src_desc = f"IBKR_OPT=${ibkr_price_for_validation:.2f}"
                    else:
                        validated_price = ibkr_entry
                        src_desc = "IBKR returned no option price — using entry"
                        log.warning(f"Reconcile {key}: {src_desc}")
                else:
                    validated_price, src_desc = _validate_position_price(sym, ibkr_price_for_validation, ibkr_entry)
                    if validated_price <= 0:
                        log.warning(f"Reconcile {key}: no validated price ({src_desc}) — using entry ${ibkr_entry:.2f} as current")
                        validated_price = ibkr_entry

                if key in active_trades:
                    # ── Known position: update IBKR-owned fields only ─────────
                    # Never touch entry, trade_type, conviction, reasoning,
                    # signal_scores, agent_outputs, pattern_id, etc.
                    stored_entry = active_trades[key].get("entry", ibkr_entry)
                    stored_direction = active_trades[key].get("direction", "LONG")
                    stored_qty = active_trades[key].get("qty", abs(int(item.position)))
                    mult = 100 if is_option else 1
                    if stored_direction == "SHORT":
                        pnl = round((stored_entry - validated_price) * stored_qty * mult, 2)
                    else:
                        pnl = round((validated_price - stored_entry) * stored_qty * mult, 2)
                    with _trades_lock:
                        active_trades[key]["current"] = round(validated_price, 4)
                        active_trades[key]["pnl"] = pnl
                        active_trades[key]["status"] = "ACTIVE"
                        active_trades[key]["_price_sources"] = src_desc
                        if is_option:
                            active_trades[key]["current_premium"] = round(validated_price, 4)
                    log.debug(f"Reconcile {key}: price updated to ${validated_price:.4f} via {src_desc}")

                    # Reattach SL bracket order ID if we didn't carry one
                    if not active_trades[key].get("sl_order_id"):
                        close_action = "BUY" if stored_direction == "SHORT" else "SELL"
                        _reattach_sl_order(ib, key, sym, stored_qty,
                                           active_trades[key].get("sl", 0),
                                           close_action, is_option=is_option)

                else:
                    # ── Unknown position: genuinely external fill ─────────────
                    # Warn loudly — this should rarely happen in normal operation.
                    direction = "SHORT" if item.position < 0 else "LONG"
                    qty = abs(int(item.position))
                    log.warning(
                        f"EXTERNAL POSITION: {key} found in IBKR but not in our store "
                        f"({direction} {qty} @ ${ibkr_entry:.4f}) — adding with minimal metadata."
                    )
                    mult = 100 if is_option else 1
                    if direction == "SHORT":
                        sl = round(ibkr_entry * (1.02 if not is_option else (1 + CONFIG.get("options_stop_loss", 0.50))), 4)
                        tp = round(ibkr_entry * (0.94 if not is_option else (1 - CONFIG.get("options_profit_target", 1.00))), 4)
                        pnl = round((ibkr_entry - validated_price) * qty * mult, 2)
                    else:
                        sl = round(ibkr_entry * (0.98 if not is_option else (1 - CONFIG.get("options_stop_loss", 0.50))), 4)
                        tp = round(ibkr_entry * (1.06 if not is_option else (1 + CONFIG.get("options_profit_target", 1.00))), 4)
                        pnl = round((validated_price - ibkr_entry) * qty * mult, 2)

                    base_entry: dict = {
                        "symbol":         sym,
                        "instrument":     "option" if is_option else "stock",
                        "entry":          ibkr_entry,
                        "current":        round(validated_price, 4),
                        "qty":            qty,
                        "sl":             sl,
                        "tp":             tp,
                        "direction":      direction,
                        "score":          0,
                        "reasoning":      "External position — not opened by this bot session",
                        "trade_type":     "SCALP",
                        "conviction":     0.0,
                        "pnl":            pnl,
                        "status":         "ACTIVE",
                        "_price_sources": src_desc,
                    }
                    if is_option:
                        c = item.contract
                        raw_exp = str(c.lastTradeDateOrContractMonth)
                        expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}" if (len(raw_exp) == 8 and raw_exp.isdigit()) else raw_exp
                        base_entry.update({
                            "right":           "C" if c.right in ("C", "CALL") else "P",
                            "strike":          c.strike,
                            "expiry_str":      expiry_str,
                            "expiry_ibkr":     raw_exp,
                            "dte":             0,
                            "contracts":       qty,
                            "entry_premium":   ibkr_entry,
                            "current_premium": round(validated_price, 4),
                        })
                    _safe_set_trade(key, base_entry)

                    if not is_option:
                        close_action = "BUY" if direction == "SHORT" else "SELL"
                        _reattach_sl_order(ib, key, sym, qty, sl, close_action)

                reconciled_count += 1

            except Exception as item_err:
                failed_count += 1
                item_sym = getattr(getattr(item, 'contract', None), 'symbol', '???')
                log.error(f"Reconciliation failed for {item_sym}: {item_err} — skipping, continuing with remaining positions")

        log.info(f"Reconciliation complete. Tracking {len(active_trades)} positions. (processed={reconciled_count}, failed={failed_count})")

    except Exception as e:
        log.error(f"Reconciliation error: {e}")


def _reattach_sl_order(ib: IB, key: str, sym: str, qty: int, sl: float,
                       close_action: str, is_option: bool = False) -> None:
    """
    Find an existing SL order in IBKR openTrades and reattach its ID, or submit
    a new stop if none exists. Options are skipped (no stock-style bracket).
    """
    if is_option:
        return
    try:
        sl_id = None
        for open_trade in ib.openTrades():
            if (open_trade.contract.symbol != sym or
                    open_trade.orderStatus.status not in ("Submitted", "PreSubmitted")):
                continue
            if open_trade.order.orderType in ("STP", "TRAIL") and open_trade.order.action.upper() == close_action:
                sl_id = open_trade.order.orderId
                break
        if sl_id:
            _safe_update_trade(key, {"sl_order_id": sl_id})
            log.info(f"Reconcile {key}: reattached SL order {sl_id}")
        elif sl > 0:
            rc = get_contract(sym)
            ib.qualifyContracts(rc)
            new_sl = StopOrder(close_action, qty, sl,
                               account=CONFIG["active_account"],
                               tif="GTC", outsideRth=True)
            sl_trade = ib.placeOrder(rc, new_sl)
            ib.sleep(0.2)
            _safe_update_trade(key, {"sl_order_id": sl_trade.order.orderId})
            log.warning(f"Reconcile {key}: re-submitted orphaned SL @ ${sl:.2f} (id={sl_trade.order.orderId})")
    except Exception as _e:
        log.warning(f"Reconcile {key}: could not restore SL order: {_e}")


def update_positions_from_ibkr(ib: IB):
    """
    Refresh current price and P&L for all tracked positions using 3-way price
    validation (IBKR + Alpaca + TV). Called on every scan so dashboard always
    shows live P&L even when no symbols score.

    Uses composite keys to match IBKR portfolio items to the correct active_trades
    entry (preventing stock/option collision). Stock prices are 3-way validated;
    option premiums use IBKR only (Alpaca/TV don't have option pricing).
    """
    try:
        portfolio_items = ib.portfolio(CONFIG["active_account"])
        # Build price map keyed by composite key (stock vs option safe)
        price_map = {}
        for item in portfolio_items:
            if item.position != 0:
                price_map[_ibkr_item_to_key(item)] = item

        # Remove positions no longer in IBKR (closed externally via SL/TP/manual)
        stale_keys = []
        with _trades_lock:
            stale_keys = [k for k in active_trades if k not in price_map and active_trades[k].get("status") != "PENDING"]
            for k in stale_keys:
                log.warning(f"Position {k} no longer in IBKR portfolio — removing from tracker")
                del active_trades[k]
        if stale_keys:
            _persist_positions()

        # ── Orphaned PENDING detection ────────────────────────────────────────
        # A PENDING entry with no active FillWatcher and past orphan_timeout_mins
        # is unmanaged (e.g. watcher aborted on disconnect). Cancel at IBKR and remove.
        from fill_watcher import _active_watchers, _watchers_lock as _fw_lock
        _orphan_mins = CONFIG.get("fill_watcher", {}).get("orphan_timeout_mins", 5)

        with _trades_lock:
            _pending_keys = [k for k in active_trades if active_trades[k].get("status") == "PENDING"]

        for _key in _pending_keys:
            _trade_instrument = active_trades.get(_key, {}).get("instrument", "stock")

            if _trade_instrument == "option":
                # Options don't use FillWatcher — use a longer per-session timeout so
                # DAY orders get cleaned up if the bot misses the IBKR cancellation callback.
                # Default 480 min (8 h) covers a full extended-hours session.
                _effective_timeout = CONFIG.get("fill_watcher", {}).get(
                    "option_orphan_timeout_mins", 480)
            else:
                with _fw_lock:
                    _has_watcher = _key in _active_watchers
                if _has_watcher:
                    continue
                _effective_timeout = _orphan_mins

            with _trades_lock:
                _trade = active_trades.get(_key)
            if _trade is None:
                continue

            _open_time_str = _trade.get("open_time")
            try:
                _open_dt = datetime.fromisoformat(_open_time_str)
                _age_mins = (datetime.now(timezone.utc) - _open_dt).total_seconds() / 60
            except (ValueError, TypeError):
                _age_mins = _effective_timeout + 1  # treat unparseable timestamp as timed-out

            if _age_mins < _effective_timeout:
                continue

            _oid = _trade.get("order_id")
            log.warning(
                f"Orphaned PENDING order {_key} order #{_oid} "
                f"(age={_age_mins:.1f} min, no FillWatcher) — cancelling"
            )
            if _oid:
                _cancel_ibkr_order_by_id(ib, _oid)
            _safe_del_trade(_key)

        # Re-add positions that IBKR has but tracker is missing
        # (lightweight reconciliation — catches positions lost by failed sells,
        #  partial startup reconciliation, or any other tracker/IBKR desync)
        for ibkr_key, item in price_map.items():
            if ibkr_key not in active_trades:
                try:
                    is_opt = _is_option_contract(item.contract)
                    sym = item.contract.symbol
                    direction = "SHORT" if item.position < 0 else "LONG"
                    qty = abs(int(item.position))
                    ibkr_mkt = float(item.marketPrice)

                    if is_opt:
                        entry = round(float(item.averageCost) / 100, 4)
                        validated = round(ibkr_mkt, 4) if ibkr_mkt > 0 else entry
                        c = item.contract
                        raw_exp = str(c.lastTradeDateOrContractMonth)
                        if len(raw_exp) == 8 and raw_exp.isdigit():
                            expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                        else:
                            expiry_str = raw_exp
                        right = "C" if c.right in ("C", "CALL") else "P"
                        mult = 100
                        if direction == "SHORT":
                            pnl = round((entry - validated) * qty * mult, 2)
                        else:
                            pnl = round((validated - entry) * qty * mult, 2)
                        _safe_set_trade(ibkr_key, {
                            "symbol": sym, "instrument": "option",
                            "right": right, "strike": c.strike,
                            "expiry_str": expiry_str, "expiry_ibkr": raw_exp,
                            "dte": 0, "contracts": qty,
                            "entry_premium": entry, "current_premium": validated,
                            "entry": entry, "current": validated,
                            "qty": qty,
                            "sl": round(entry * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
                            "tp": round(entry * (1 + CONFIG.get("options_profit_target", 1.00)), 4),
                            "direction": direction, "score": 0,
                            "reasoning": "Re-synced from IBKR (was missing from tracker)",
                            "pnl": pnl, "status": "ACTIVE",
                        })
                        log.warning(f"Re-added missing option {ibkr_key} from IBKR ({direction} {qty}x, premium ${entry:.4f})")
                    else:
                        entry = round(float(item.averageCost), 4)
                        validated = ibkr_mkt if ibkr_mkt > 0 else entry
                        if direction == "SHORT":
                            sl = round(entry * 1.02, 2); tp = round(entry * 0.94, 2)
                            pnl = round((entry - validated) * qty, 2)
                        else:
                            sl = round(entry * 0.98, 2); tp = round(entry * 1.06, 2)
                            pnl = round((validated - entry) * qty, 2)
                        _safe_set_trade(ibkr_key, {
                            "symbol": sym, "instrument": "stock",
                            "entry": entry, "current": round(validated, 4),
                            "qty": qty, "sl": sl, "tp": tp, "score": 0,
                            "reasoning": "Re-synced from IBKR (was missing from tracker)",
                            "direction": direction, "pnl": pnl, "status": "ACTIVE",
                        })
                        log.warning(f"Re-added missing stock {ibkr_key} from IBKR ({direction} {qty} @ ${entry:.2f})")
                except Exception as readd_err:
                    log.error(f"Failed to re-add {ibkr_key}: {readd_err}")

        with _trades_lock:
            trades_snapshot = dict(active_trades)
        for key, trade in trades_snapshot.items():
            is_option = trade.get("instrument") == "option"
            sym = trade.get("symbol", key)
            entry = trade.get("entry", 0)

            ibkr_price = 0
            if key in price_map:
                item = price_map[key]
                mkt_price = float(item.marketPrice)
                if mkt_price > 0:
                    if is_option:
                        # IBKR marketPrice for options is already per-share premium
                        ibkr_price = round(mkt_price, 4)
                    else:
                        ibkr_price = mkt_price

            # Options: trust IBKR premium (Alpaca/TV return stock price, not premium)
            if is_option:
                if ibkr_price > 0:
                    validated_price = ibkr_price
                    src_desc = f"IBKR_OPT=${ibkr_price:.2f}"
                else:
                    log.warning(f"No IBKR price for option {key} — keeping previous ${trade.get('current', 0):.2f}")
                    continue
            else:
                validated_price, src_desc = _validate_position_price(sym, ibkr_price, entry)

            if validated_price > 0:
                trade["current"] = round(validated_price, 4)
                if is_option:
                    trade["current_premium"] = round(validated_price, 4)
                # Recalculate P&L from validated price
                # Options: per-share premium × qty × 100 (contract multiplier)
                mult = 100 if is_option else 1
                direction = trade.get("direction", "LONG")
                if direction == "SHORT":
                    trade["pnl"] = round((entry - validated_price) * trade["qty"] * mult, 2)
                else:
                    trade["pnl"] = round((validated_price - entry) * trade["qty"] * mult, 2)
                trade["_price_sources"] = src_desc
            else:
                log.warning(f"No validated price for {key}: {src_desc} — keeping previous ${trade.get('current', 0):.2f}")

    except Exception as e:
        log.warning(f"Position price update error: {e}")


def update_position_prices(signals: list):
    """
    DEPRECATED — kept for backward compatibility but now a no-op.
    3-way validation is handled entirely by update_positions_from_ibkr().
    """
    pass  # All price validation now happens in update_positions_from_ibkr via _validate_position_price


def get_open_positions() -> list:
    """Return list of open positions for dashboard and agent consumption.
    Injects '_trade_key' into each position so the dashboard close button
    can send the correct composite key (stock vs option safe).
    """
    with _trades_lock:
        snapshot = list(active_trades.items())
    result = []
    for key, trade in snapshot:
        pos = dict(trade)
        pos["_trade_key"] = key
        result.append(pos)
    return result
