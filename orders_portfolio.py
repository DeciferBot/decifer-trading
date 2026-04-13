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

from datetime import UTC, datetime

from ib_async import IB, LimitOrder, MarketOrder, StopOrder

from config import CONFIG
from learning import log_order
from orders_contracts import (
    _cancel_ibkr_order_by_id,
    _get_emergency_ib,
    _get_ibkr_price,
    _ibkr_item_to_key,
    _is_option_contract,
    _validate_position_price,
    get_contract,
    is_equities_extended_hours,
)
from orders_state import (
    _flatten_lock,
    _load_positions_file,
    _safe_del_trade,
    _safe_set_trade,
    _safe_update_trade,
    _save_positions_file,
    _trades_lock,
    active_trades,
    log,
)
from trade_store import ledger_lookup as _ledger_lookup
from trade_store import restore as _ts_restore

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
            log.critical(f"🚨 FLATTEN ABORTED — {len(stranded)} position(s) NOT closed. Manual intervention required:")
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
            symbols_to_stop = [info.get("symbol", key.split("_")[0]) for key, info in active_trades.items()]
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

                contract = _FlatOpt(
                    sym, info["expiry_ibkr"], info["strike"], info["right"], exchange="SMART", currency="USD"
                )
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


def close_position(ib_unused, trade_key: str) -> str | None:
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

    # Guard: IBKR cancels MKT orders outside 4 AM–8 PM ET extended hours.
    # FX trades 24/5 — skip the hours check. Options have their own 9:30–4 PM gate.
    _stored_instrument = active_trades.get(trade_key, {}).get("instrument", "stock")
    _is_fx_trade = _stored_instrument == "fx"
    if not _is_fx_trade and not is_equities_extended_hours():
        import zoneinfo as _zi

        _now_et = datetime.now(_zi.ZoneInfo("America/New_York")).strftime("%H:%M ET")
        log.warning(
            f"close_position {trade_key}: market closed ({_now_et}) — deferring until extended hours open (4 AM–8 PM ET)"
        )
        return None

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
    # Also handles FX by reconstructing the pair from base + currency.
    if not target:
        for item in portfolio_items:
            if item.position == 0:
                continue
            c = item.contract
            if c.secType == "STK" and c.symbol.upper() == trade_key:
                target = item
                break
            if c.secType == "CASH":
                _pair = (getattr(c, "symbol", "") + getattr(c, "currency", "")).upper()
                if _pair == trade_key:
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
    is_fx = target.contract.secType == "CASH"
    instrument = "option" if is_option else ("fx" if is_fx else "stock")

    # 2) Cancel related open orders for this symbol
    try:
        for t in eib.trades():
            if t.contract.symbol == sym and t.orderStatus.status in ("Submitted", "PreSubmitted"):
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
    # FX (secType CASH) uses IDEALPRO — don't override with SMART.
    # The contract from ib.portfolio() already has the correct exchange.
    if contract.secType != "CASH":
        contract.exchange = "SMART"
    try:
        eib.qualifyContracts(contract)
    except Exception:
        pass  # Proceed with exchange='SMART' even if qualify fails

    order = MarketOrder(action, qty, account=CONFIG["active_account"], outsideRth=True)
    close_trade = eib.placeOrder(contract, order)
    eib.sleep(0.3)

    # Log the close order
    log_order(
        {
            "order_id": close_trade.order.orderId,
            "symbol": sym,
            "side": action,
            "order_type": "MKT",
            "qty": qty,
            "price": mkt,
            "status": "SUBMITTED",
            "instrument": instrument,
            "role": "close",
            "reason": "Manual close from dashboard",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    detail = f"{action} {qty} {sym} {'OPT' if is_option else ''} MKT (mkt=${mkt:.2f})"
    log.warning(f"📤 INSTANT close: {detail}")

    # 4) Remove from bot tracker — try composite key first, then plain symbol
    tracker_key = _ibkr_item_to_key(target)
    if tracker_key in active_trades:
        del active_trades[tracker_key]
    elif trade_key in active_trades:
        del active_trades[trade_key]
    _save_positions_file()

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
    log.info("Reconciling positions with IBKR (3-way price validation)...")
    saved_positions = _load_positions_file()
    if saved_positions:
        log.info(f"Loaded {len(saved_positions)} saved position(s) from disk for metadata restore")

    def _find_saved(key: str, sym: str, instrument: str) -> dict:
        """
        Three-tier metadata recovery:
          1. positions.json (exact key, then symbol+instrument scan)
          2. metadata_ledger.json (durable, crash-safe, never bulk-rewritten)
        """
        # Tier 1: positions.json exact key
        if key in saved_positions:
            return saved_positions[key]
        # Tier 1b: positions.json symbol+instrument scan
        for v in saved_positions.values():
            if (
                v.get("symbol") == sym and v.get("instrument") == instrument and v.get("trade_type")
            ):  # must have real decision metadata
                return v
        # Tier 2: metadata ledger (survives crashes / positions.json corruption)
        ledger_hit = _ledger_lookup(key, sym, instrument)
        if ledger_hit:
            log.info(
                f"Reconcile {key}: metadata recovered from ledger (trade_type={ledger_hit.get('trade_type', '?')})"
            )
            return ledger_hit
        return {}

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

        # FX (CASH) positions may not appear in ib.portfolio() if
        # reqAccountUpdates hasn't delivered updatePortfolio callbacks yet.
        # Augment ibkr_keys with ib.positions() so FX entries are not
        # incorrectly purged as "closed while bot was down".
        try:
            for pos in ib.positions():
                if pos.position != 0:
                    ibkr_keys.add(_ibkr_item_to_key(pos))
        except Exception:
            pass

        # ── Step 3: detect positions closed while bot was down ────────────────
        # In our store but not in IBKR → SL/TP was triggered or manually closed.
        keys_to_remove = []
        closed_while_down = []  # collect (advice_id, exit_price, pnl) to close Opus loop after lock
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
                            f"Position {key} in our store but not in IBKR — was closed while bot was down, removing"
                        )
                        keys_to_remove.append(key)
                        if trade.get("advice_id"):
                            closed_while_down.append(
                                {
                                    "advice_id": trade["advice_id"],
                                    "exit_price": float(trade.get("current") or trade.get("entry", 0)),
                                    "pnl": float(trade.get("pnl", 0.0)),
                                }
                            )

        for key in keys_to_remove:
            _safe_del_trade(key)

        # Close Opus learning loop for positions that were filled/stopped while the bot was offline
        for item in closed_while_down:
            try:
                from trade_advisor import record_outcome as _record_outcome

                _record_outcome(
                    advice_id=item["advice_id"],
                    exit_price=item["exit_price"],
                    pnl=item["pnl"],
                    exit_reason="closed_while_bot_down",
                )
            except Exception as _e:
                log.debug(f"advisor record_outcome (offline close) failed: {_e}")

        # ── Step 4: process IBKR portfolio items ──────────────────────────────
        reconciled_count = 0
        failed_count = 0
        for item in portfolio_items:
            if item.position == 0:
                continue

            # Per-item try/except: one bad position must NOT kill the entire loop
            try:
                key = _ibkr_item_to_key(item)
                is_fx = getattr(item.contract, "secType", "") == "CASH"
                # FX: use reconstructed pair (e.g. "EURUSD"), not base currency ("EUR")
                sym = key if is_fx else item.contract.symbol
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
                        log.warning(
                            f"Reconcile {key}: no validated price ({src_desc}) — using entry ${ibkr_entry:.2f} as current"
                        )
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

                    # Reattach SL bracket order ID if we didn't carry one.
                    # FX skipped: _reattach_sl_order matches by contract.symbol
                    # which is base currency ("EUR") not the pair ("EURUSD"),
                    # so it never finds existing SL orders and would create duplicates.
                    if not is_fx and not active_trades[key].get("sl_order_id"):
                        close_action = "BUY" if stored_direction == "SHORT" else "SELL"
                        _reattach_sl_order(
                            ib, key, sym, stored_qty, active_trades[key].get("sl", 0), close_action, is_option=is_option
                        )

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
                        sl = round(
                            ibkr_entry * (1.02 if not is_option else (1 + CONFIG.get("options_stop_loss", 0.50))), 4
                        )
                        tp = round(
                            ibkr_entry * (0.94 if not is_option else (1 - CONFIG.get("options_profit_target", 1.00))), 4
                        )
                        pnl = round((ibkr_entry - validated_price) * qty * mult, 2)
                    else:
                        sl = round(
                            ibkr_entry * (0.98 if not is_option else (1 - CONFIG.get("options_stop_loss", 0.50))), 4
                        )
                        tp = round(
                            ibkr_entry * (1.06 if not is_option else (1 + CONFIG.get("options_profit_target", 1.00))), 4
                        )
                        pnl = round((validated_price - ibkr_entry) * qty * mult, 2)

                    {
                        "symbol": sym,
                        "instrument": "option" if is_option else ("fx" if is_fx else "stock"),
                        "entry": ibkr_entry,
                        "current": round(validated_price, 4),
                        "qty": qty,
                        "sl": sl,
                        "tp": tp,
                        "direction": direction,
                        "score": 0,
                        "reasoning": "External position — not opened by this bot session",
                        "trade_type": "UNKNOWN",
                        "conviction": 0.0,
                        "entry_regime": "UNKNOWN",
                        "metadata_status": "MISSING",
                        "pnl": pnl,
                        "status": "ACTIVE",
                        "_price_sources": src_desc,
                    }
                    if is_option:
                        c = item.contract
                        raw_exp = str(c.lastTradeDateOrContractMonth)
                        if len(raw_exp) == 8 and raw_exp.isdigit():
                            expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                        else:
                            expiry_str = raw_exp
                        right = "C" if c.right in ("C", "CALL") else "P"

                        log.info(
                            f"Option {key} in IBKR but not tracked — adding ({direction} {qty} contracts, premium ${ibkr_entry:.2f}, validated ${validated_price:.2f} via {src_desc})"
                        )
                        # Options P&L: per-share premium × qty × 100 (contract multiplier)
                        if direction == "SHORT":
                            pnl = round((ibkr_entry - validated_price) * qty * 100, 2)
                        else:
                            pnl = round((validated_price - ibkr_entry) * qty * 100, 2)
                        _saved = _find_saved(key, sym, "option")
                        new_entry = {
                            "symbol": sym,
                            "instrument": "option",
                            "right": right,
                            "strike": c.strike,
                            "expiry_str": expiry_str,
                            "expiry_ibkr": raw_exp,
                            "dte": 0,
                            "contracts": qty,
                            "entry_premium": ibkr_entry,
                            "current_premium": validated_price,
                            "entry": ibkr_entry,
                            "current": round(validated_price, 4),
                            "qty": qty,
                            "sl": round(ibkr_entry * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
                            "tp": round(ibkr_entry * (1 + CONFIG.get("options_profit_target", 1.00)), 4),
                            "direction": direction,
                            "score": 0,
                            "trade_type": "UNKNOWN",
                            "conviction": 0.0,
                            "entry_regime": "UNKNOWN",
                            "metadata_status": "MISSING",
                            "reasoning": "Reconciled from IBKR on startup — metadata not found",
                            "pnl": pnl,
                            "status": "ACTIVE",
                            "_price_sources": src_desc,
                        }
                        if _saved:
                            log.info(
                                f"Reconcile {key}: restoring metadata (trade_type={_saved.get('trade_type', '?')})"
                            )
                            new_entry["trade_type"] = _saved.get("trade_type", "SCALP")
                            new_entry["reasoning"] = _saved.get("reasoning", new_entry["reasoning"])
                            new_entry["signal_scores"] = _saved.get("signal_scores", {})
                            new_entry["agent_outputs"] = _saved.get("agent_outputs", {})
                            new_entry["entry_score"] = _saved.get("entry_score", 0)
                            new_entry["open_time"] = _saved.get("open_time")
                            new_entry["atr"] = _saved.get("atr", 0)
                            new_entry["conviction"] = _saved.get("conviction", 0.0)
                            new_entry["entry_regime"] = _saved.get("entry_regime", "UNKNOWN")
                            new_entry["entry_thesis"] = _saved.get("entry_thesis", "")
                            new_entry["pattern_id"] = _saved.get("pattern_id", "")
                            new_entry["high_water_mark"] = _saved.get("high_water_mark", ibkr_entry)
                            new_entry["ic_weights_at_entry"] = _saved.get("ic_weights_at_entry")
                            new_entry["advice_id"] = _saved.get("advice_id", "")
                            if _saved.get("score", 0) > 0:
                                new_entry["score"] = _saved["score"]
                            new_entry["_metadata_restored"] = True
                        _safe_set_trade(key, new_entry)
                    elif is_fx:
                        # FX position — tighter SL/TP (0.5%/1.5%) and 4-decimal precision
                        if direction == "SHORT":
                            sl = round(ibkr_entry * 1.005, 4)
                            tp = round(ibkr_entry * 0.985, 4)
                        else:
                            sl = round(ibkr_entry * 0.995, 4)
                            tp = round(ibkr_entry * 1.015, 4)
                        log.info(
                            f"FX position {key} in IBKR but not tracked — adding ({direction} {qty} units @ {ibkr_entry:.4f}, validated {validated_price:.4f} via {src_desc})"
                        )
                        if direction == "SHORT":
                            pnl = round((ibkr_entry - validated_price) * qty, 2)
                        else:
                            pnl = round((validated_price - ibkr_entry) * qty, 2)
                        _saved = _find_saved(key, sym, "fx")
                        new_entry = {
                            "symbol": sym,
                            "instrument": "fx",
                            "entry": ibkr_entry,
                            "current": round(validated_price, 4),
                            "qty": qty,
                            "sl": sl,
                            "tp": tp,
                            "score": 0,
                            "trade_type": "UNKNOWN",
                            "conviction": 0.0,
                            "entry_regime": "UNKNOWN",
                            "metadata_status": "MISSING",
                            "reasoning": "Reconciled from IBKR on startup — metadata not found",
                            "direction": direction,
                            "pnl": pnl,
                            "status": "ACTIVE",
                            "_price_sources": src_desc,
                        }
                        if _saved:
                            log.info(
                                f"Reconcile {key}: restoring FX metadata (trade_type={_saved.get('trade_type', '?')})"
                            )
                            new_entry["trade_type"] = _saved.get("trade_type", "SCALP")
                            new_entry["reasoning"] = _saved.get("reasoning", new_entry["reasoning"])
                            new_entry["signal_scores"] = _saved.get("signal_scores", {})
                            new_entry["agent_outputs"] = _saved.get("agent_outputs", {})
                            new_entry["entry_score"] = _saved.get("entry_score", 0)
                            new_entry["open_time"] = _saved.get("open_time")
                            new_entry["atr"] = _saved.get("atr", 0)
                            new_entry["conviction"] = _saved.get("conviction", 0.0)
                            new_entry["entry_regime"] = _saved.get("entry_regime", "UNKNOWN")
                            new_entry["entry_thesis"] = _saved.get("entry_thesis", "")
                            new_entry["pattern_id"] = _saved.get("pattern_id", "")
                            new_entry["tranche_mode"] = _saved.get("tranche_mode", False)
                            new_entry["t1_qty"] = _saved.get("t1_qty")
                            new_entry["t2_qty"] = _saved.get("t2_qty")
                            new_entry["t1_status"] = _saved.get("t1_status")
                            new_entry["t1_order_id"] = _saved.get("t1_order_id")
                            new_entry["high_water_mark"] = _saved.get("high_water_mark", ibkr_entry)
                            new_entry["ic_weights_at_entry"] = _saved.get("ic_weights_at_entry")
                            new_entry["advice_id"] = _saved.get("advice_id", "")
                            if _saved.get("score", 0) > 0:
                                new_entry["score"] = _saved["score"]
                            new_entry["_metadata_restored"] = True
                        _safe_set_trade(key, new_entry)
                    else:
                        # Stock position
                        if direction == "SHORT":
                            sl = round(ibkr_entry * 1.02, 2)
                            tp = round(ibkr_entry * 0.94, 2)
                        else:
                            sl = round(ibkr_entry * 0.98, 2)
                            tp = round(ibkr_entry * 1.06, 2)
                        log.info(
                            f"Position {key} in IBKR but not tracked — adding ({direction} {qty} shares @ ${ibkr_entry:.2f}, validated price ${validated_price:.2f} via {src_desc})"
                        )
                        if direction == "SHORT":
                            pnl = round((ibkr_entry - validated_price) * qty, 2)
                        else:
                            pnl = round((validated_price - ibkr_entry) * qty, 2)
                        _saved = _find_saved(key, sym, "stock")
                        new_entry = {
                            "symbol": sym,
                            "instrument": "stock",
                            "entry": ibkr_entry,
                            "current": round(validated_price, 4),
                            "qty": qty,
                            "sl": sl,
                            "tp": tp,
                            "score": 0,
                            "trade_type": "UNKNOWN",
                            "conviction": 0.0,
                            "entry_regime": "UNKNOWN",
                            "metadata_status": "MISSING",
                            "reasoning": "Reconciled from IBKR on startup — metadata not found",
                            "direction": direction,
                            "pnl": pnl,
                            "status": "ACTIVE",
                            "_price_sources": src_desc,
                        }
                        if _saved:
                            log.info(
                                f"Reconcile {key}: restoring metadata (trade_type={_saved.get('trade_type', '?')})"
                            )
                            new_entry["trade_type"] = _saved.get("trade_type", "SCALP")
                            new_entry["reasoning"] = _saved.get("reasoning", new_entry["reasoning"])
                            new_entry["signal_scores"] = _saved.get("signal_scores", {})
                            new_entry["agent_outputs"] = _saved.get("agent_outputs", {})
                            new_entry["entry_score"] = _saved.get("entry_score", 0)
                            new_entry["open_time"] = _saved.get("open_time")
                            new_entry["atr"] = _saved.get("atr", 0)
                            new_entry["conviction"] = _saved.get("conviction", 0.0)
                            new_entry["entry_regime"] = _saved.get("entry_regime", "UNKNOWN")
                            new_entry["entry_thesis"] = _saved.get("entry_thesis", "")
                            new_entry["pattern_id"] = _saved.get("pattern_id", "")
                            new_entry["tranche_mode"] = _saved.get("tranche_mode", False)
                            new_entry["t1_qty"] = _saved.get("t1_qty")
                            new_entry["t2_qty"] = _saved.get("t2_qty")
                            new_entry["t1_status"] = _saved.get("t1_status")
                            new_entry["t1_order_id"] = _saved.get("t1_order_id")
                            new_entry["high_water_mark"] = _saved.get("high_water_mark", ibkr_entry)
                            new_entry["ic_weights_at_entry"] = _saved.get("ic_weights_at_entry")
                            new_entry["advice_id"] = _saved.get("advice_id", "")
                            if _saved.get("score", 0) > 0:
                                new_entry["score"] = _saved["score"]
                            new_entry["_metadata_restored"] = True
                        _safe_set_trade(key, new_entry)

                    if not is_option and not is_fx:
                        close_action = "BUY" if direction == "SHORT" else "SELL"
                        _reattach_sl_order(ib, key, sym, qty, sl, close_action)

                reconciled_count += 1

            except Exception as item_err:
                failed_count += 1
                item_sym = getattr(getattr(item, "contract", None), "symbol", "???")
                log.error(
                    f"Reconciliation failed for {item_sym}: {item_err} — skipping, continuing with remaining positions"
                )

        # ── Step 5: reconcile FX positions only visible via ib.positions() ──
        # ib.portfolio() sometimes omits CASH positions when reqAccountUpdates
        # callbacks haven't arrived yet.  ib.positions() is populated from a
        # separate subscription and is more reliable for FX.  Any FX key that
        # appeared in ib.positions() but NOT in portfolio_items (and is not
        # already tracked) needs to be added to active_trades here.
        _portfolio_keys = {_ibkr_item_to_key(it) for it in portfolio_items if it.position != 0}
        try:
            for pos in ib.positions():
                if pos.position == 0:
                    continue
                _pk = _ibkr_item_to_key(pos)
                if _pk in _portfolio_keys or _pk in active_trades:
                    continue  # already handled above
                if getattr(pos.contract, "secType", "") != "CASH":
                    continue  # only FX needs this fallback
                try:
                    sym = _pk  # reconstructed pair e.g. "EURUSD"
                    direction = "SHORT" if pos.position < 0 else "LONG"
                    qty = abs(int(pos.position))
                    ibkr_entry = round(float(pos.avgCost), 4)
                    # Position objects lack marketPrice — use price validation
                    validated_price, src_desc = _validate_position_price(sym, 0, ibkr_entry)
                    if validated_price <= 0:
                        validated_price = ibkr_entry
                        src_desc = "no market price — using entry"
                    if direction == "SHORT":
                        sl = round(ibkr_entry * 1.005, 4)
                        tp = round(ibkr_entry * 0.985, 4)
                        pnl = round((ibkr_entry - validated_price) * qty, 2)
                    else:
                        sl = round(ibkr_entry * 0.995, 4)
                        tp = round(ibkr_entry * 1.015, 4)
                        pnl = round((validated_price - ibkr_entry) * qty, 2)
                    log.warning(
                        f"FX position {_pk} found in ib.positions() but not ib.portfolio() — adding ({direction} {qty} units @ {ibkr_entry:.4f})"
                    )
                    _saved = _find_saved(_pk, sym, "fx")
                    new_entry = {
                        "symbol": sym,
                        "instrument": "fx",
                        "entry": ibkr_entry,
                        "current": round(validated_price, 4),
                        "qty": qty,
                        "sl": sl,
                        "tp": tp,
                        "score": 0,
                        "trade_type": "UNKNOWN",
                        "conviction": 0.0,
                        "entry_regime": "UNKNOWN",
                        "metadata_status": "MISSING",
                        "reasoning": "Reconciled from IBKR ib.positions() — FX not in portfolio()",
                        "direction": direction,
                        "pnl": pnl,
                        "status": "ACTIVE",
                        "_price_sources": src_desc,
                    }
                    if _saved:
                        log.info(f"Reconcile {_pk}: restoring FX metadata (trade_type={_saved.get('trade_type', '?')})")
                        for _fld in (
                            "trade_type",
                            "reasoning",
                            "signal_scores",
                            "agent_outputs",
                            "entry_score",
                            "open_time",
                            "atr",
                            "conviction",
                            "entry_regime",
                            "entry_thesis",
                            "pattern_id",
                            "tranche_mode",
                            "t1_qty",
                            "t2_qty",
                            "t1_status",
                            "t1_order_id",
                            "high_water_mark",
                            "ic_weights_at_entry",
                            "advice_id",
                        ):
                            if _saved.get(_fld) is not None:
                                new_entry[_fld] = _saved[_fld]
                        if _saved.get("score", 0) > 0:
                            new_entry["score"] = _saved["score"]
                        new_entry["_metadata_restored"] = True
                    _safe_set_trade(_pk, new_entry)
                    reconciled_count += 1
                except Exception as fx_err:
                    failed_count += 1
                    log.error(f"FX positions() reconcile failed for {_pk}: {fx_err}")
        except Exception:
            pass  # ib.positions() unavailable — non-fatal

        log.info(
            f"Reconciliation complete. Tracking {len(active_trades)} positions. (processed={reconciled_count}, failed={failed_count})"
        )
        _save_positions_file()

    except Exception as e:
        log.error(f"Reconciliation error: {e}")


def _reattach_sl_order(
    ib: IB, key: str, sym: str, qty: int, sl: float, close_action: str, is_option: bool = False
) -> None:
    """
    Find an existing SL order in IBKR openTrades and reattach its ID, or submit
    a new stop if none exists. Options are skipped (no stock-style bracket).
    """
    if is_option:
        return
    try:
        sl_id = None
        for open_trade in ib.openTrades():
            if open_trade.contract.symbol != sym or open_trade.orderStatus.status not in ("Submitted", "PreSubmitted"):
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
            new_sl = StopOrder(close_action, qty, sl, account=CONFIG["active_account"], tif="GTC", outsideRth=True)
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

        # FX (CASH) positions may not appear in ib.portfolio() if
        # reqAccountUpdates hasn't delivered updatePortfolio callbacks yet.
        # Build a fallback set from ib.positions() so FX entries are not
        # incorrectly treated as stale or orphaned.
        _positions_keys: set = set()
        try:
            for pos in ib.positions():
                if pos.position != 0:
                    _positions_keys.add(_ibkr_item_to_key(pos))
        except Exception:
            pass

        # Remove positions no longer in IBKR (closed externally via SL/TP/manual)
        stale_keys = []
        with _trades_lock:
            stale_keys = [
                k
                for k in active_trades
                if k not in price_map and k not in _positions_keys and active_trades[k].get("status") != "PENDING"
            ]
            for k in stale_keys:
                log.warning(f"Position {k} no longer in IBKR portfolio — removing from tracker")
                del active_trades[k]
        if stale_keys:
            _save_positions_file()

        # ── Orphaned PENDING detection ────────────────────────────────────────
        # A PENDING entry with no active FillWatcher and past orphan_timeout_mins
        # is unmanaged (e.g. watcher aborted on disconnect). Cancel at IBKR and remove.
        from fill_watcher import _active_watchers
        from fill_watcher import _watchers_lock as _fw_lock

        _orphan_mins = CONFIG.get("fill_watcher", {}).get("orphan_timeout_mins", 5)

        with _trades_lock:
            _pending_keys = [k for k in active_trades if active_trades[k].get("status") == "PENDING"]

        for _key in _pending_keys:
            _trade_instrument = active_trades.get(_key, {}).get("instrument", "stock")

            if _trade_instrument == "option":
                # Options don't use FillWatcher — use a longer per-session timeout so
                # DAY orders get cleaned up if the bot misses the IBKR cancellation callback.
                # Default 480 min (8 h) covers a full extended-hours session.
                _effective_timeout = CONFIG.get("fill_watcher", {}).get("option_orphan_timeout_mins", 480)
            elif _key in price_map or _key in _positions_keys:
                # Order has already filled and IBKR shows an active position — not orphaned.
                # Checks both ib.portfolio() and ib.positions() because FX (CASH)
                # positions may only appear in the latter.
                with _trades_lock:
                    if _key in active_trades:
                        active_trades[_key]["status"] = "ACTIVE"
                _src = "portfolio" if _key in price_map else "positions"
                log.info(f"PENDING {_key} found in IBKR {_src} — marking ACTIVE (fill confirmed)")
                continue
            else:
                with _fw_lock:
                    _has_watcher = _key in _active_watchers
                if _has_watcher:
                    continue
                # No watcher — check if the entry order is still live at IBKR.
                # This prevents premature purging of watcherless orders (e.g. shorts,
                # FX entries) whose entry is still pending in the IBKR order book.
                _oid_check = active_trades.get(_key, {}).get("order_id")
                if _oid_check:
                    _order_still_live = False
                    try:
                        for _ot in ib.openTrades():
                            if _ot.order.orderId == _oid_check:
                                _order_still_live = True
                                break
                    except Exception:
                        _order_still_live = True  # fail-closed: assume live
                    if _order_still_live:
                        continue
                _effective_timeout = _orphan_mins

            with _trades_lock:
                _trade = active_trades.get(_key)
            if _trade is None:
                continue

            _open_time_str = _trade.get("open_time")
            try:
                _open_dt = datetime.fromisoformat(_open_time_str)
                _age_mins = (datetime.now(UTC) - _open_dt).total_seconds() / 60
            except (ValueError, TypeError):
                _age_mins = _effective_timeout + 1  # treat unparseable timestamp as timed-out

            if _age_mins < _effective_timeout:
                continue

            _oid = _trade.get("order_id")
            log.warning(
                f"Orphaned PENDING order {_key} order #{_oid} (age={_age_mins:.1f} min, no FillWatcher) — cancelling"
            )
            if _oid:
                _cancel_ibkr_order_by_id(ib, _oid)
            _safe_del_trade(_key)

        # Re-add positions that IBKR has but tracker is missing.
        # Metadata rescue: before writing a bare-minimum stub, try to salvage the
        # original decision record from positions.json.  Key-format mismatches
        # (e.g. a stock key "NBIS" vs option key "NBIS_C_157.5_2026-04-24") are the
        # most common cause — the position IS on disk, just under the wrong key.
        # IBKR_RECONCILE_FIELDS are the only fields we overwrite; everything else
        # (trade_type, conviction, reasoning, signal_scores, entry_regime, …) is
        # preserved verbatim from the saved record.  Without this the whole training-
        # data corpus is corrupted every time the bot drops and re-adds a position.
        from trade_store import restore as _restore_positions

        def _find_saved_metadata(sym: str, instrument: str) -> dict:
            """
            Scan positions.json for the best metadata match for this symbol+instrument.
            Returns the saved dict (possibly stale keys) or {} if nothing useful found.
            """
            try:
                saved = _restore_positions()
                # Exact key match first (already covered by `ibkr_key not in active_trades`
                # but positions.json may have the entry under the old key).
                for _saved_key, saved_val in saved.items():
                    if (
                        saved_val.get("symbol") == sym
                        and saved_val.get("instrument") == instrument
                        and saved_val.get("trade_type")
                    ):  # has real metadata
                        return saved_val
            except Exception:
                pass
            return {}

        for ibkr_key, item in price_map.items():
            if ibkr_key not in active_trades:
                try:
                    is_opt = _is_option_contract(item.contract)
                    is_fx = getattr(item.contract, "secType", "") == "CASH"
                    # FX: use reconstructed pair symbol (e.g. "EURUSD"), not base ("EUR")
                    sym = ibkr_key if is_fx else item.contract.symbol
                    direction = "SHORT" if item.position < 0 else "LONG"
                    qty = abs(int(item.position))
                    ibkr_mkt = float(item.marketPrice)
                    instrument = "option" if is_opt else ("fx" if is_fx else "stock")

                    # Attempt to recover the original trade metadata from disk.
                    saved_meta = _find_saved_metadata(sym, instrument)
                    metadata_restored = bool(saved_meta)

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

                        # Build the authoritative IBKR structural fields.
                        ibkr_fields = {
                            "symbol": sym,
                            "instrument": "option",
                            "right": right,
                            "strike": c.strike,
                            "expiry_str": expiry_str,
                            "expiry_ibkr": raw_exp,
                            "dte": 0,
                            "contracts": qty,
                            "entry_premium": entry,
                            "current_premium": validated,
                            "entry": entry,
                            "current": validated,
                            "qty": qty,
                            "sl": round(entry * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
                            "tp": round(entry * (1 + CONFIG.get("options_profit_target", 1.00)), 4),
                            "direction": direction,
                            "pnl": pnl,
                            "status": "ACTIVE",
                        }

                        if metadata_restored:
                            # Merge: start from saved record, overlay IBKR structural fields.
                            new_entry = {**saved_meta, **ibkr_fields}
                            log.warning(
                                f"Re-added missing option {ibkr_key} from IBKR — "
                                f"metadata RESTORED from disk (trade_type={saved_meta.get('trade_type', '?')})"
                            )
                        else:
                            # Genuine orphan: no saved record at all.
                            new_entry = {
                                **ibkr_fields,
                                "score": 0,
                                "trade_type": "UNKNOWN",
                                "conviction": 0.0,
                                "entry_regime": "UNKNOWN",
                                "metadata_status": "MISSING",
                                "reasoning": "Re-synced from IBKR — original metadata not found",
                            }
                            log.warning(
                                f"Re-added missing option {ibkr_key} from IBKR — "
                                f"NO metadata found; trade_type/conviction unknown"
                            )
                        _safe_set_trade(ibkr_key, new_entry)

                    else:
                        entry = round(float(item.averageCost), 4)
                        validated = ibkr_mkt if ibkr_mkt > 0 else entry
                        _prec = 4 if is_fx else 2
                        if direction == "SHORT":
                            sl = round(entry * (1.005 if is_fx else 1.02), _prec)
                            tp = round(entry * (0.985 if is_fx else 0.94), _prec)
                            pnl = round((entry - validated) * qty, 2)
                        else:
                            sl = round(entry * (0.995 if is_fx else 0.98), _prec)
                            tp = round(entry * (1.015 if is_fx else 1.06), _prec)
                            pnl = round((validated - entry) * qty, 2)

                        ibkr_fields = {
                            "symbol": sym,
                            "instrument": instrument,
                            "entry": entry,
                            "current": round(validated, 4),
                            "qty": qty,
                            "sl": sl,
                            "tp": tp,
                            "direction": direction,
                            "pnl": pnl,
                            "status": "ACTIVE",
                        }

                        _re_add_label = "fx" if is_fx else "stock"
                        if metadata_restored:
                            new_entry = {**saved_meta, **ibkr_fields}
                            log.warning(
                                f"Re-added missing {_re_add_label} {ibkr_key} from IBKR — "
                                f"metadata RESTORED from disk (trade_type={saved_meta.get('trade_type', '?')})"
                            )
                        else:
                            new_entry = {
                                **ibkr_fields,
                                "score": 0,
                                "trade_type": "UNKNOWN",
                                "conviction": 0.0,
                                "entry_regime": "UNKNOWN",
                                "metadata_status": "MISSING",
                                "reasoning": "Re-synced from IBKR — original metadata not found",
                            }
                            log.warning(
                                f"Re-added missing {_re_add_label} {ibkr_key} from IBKR — "
                                f"NO metadata found; trade_type/conviction unknown"
                            )
                        _safe_set_trade(ibkr_key, new_entry)

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

            # FX positions may be absent from ib.portfolio() — fetch live
            # price directly via reqTickers so the dashboard stays current.
            if ibkr_price == 0 and trade.get("instrument") == "fx":
                try:
                    _fx_contract = get_contract(sym, "fx")
                    ib.qualifyContracts(_fx_contract)
                    ibkr_price = _get_ibkr_price(ib, _fx_contract, fallback=0)
                except Exception as _fx_err:
                    log.debug(f"FX price fetch for {sym}: {_fx_err}")

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
                log.warning(
                    f"No validated price for {key}: {src_desc} — keeping previous ${trade.get('current', 0):.2f}"
                )

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
