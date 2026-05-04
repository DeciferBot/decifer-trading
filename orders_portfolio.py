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

from datetime import UTC, date, datetime

from ib_async import IB, LimitOrder, MarketOrder

from bot_ibkr import cancel_with_reason
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
    is_options_market_open,
)
from orders_state import (
    _flatten_lock,
    _load_positions_file,
    _recently_closed_lock,
    _safe_del_trade,
    _safe_set_trade,
    _safe_update_trade,
    _save_positions_file,
    _trades_lock,
    active_trades,
    log,
    recently_closed,
)
import orders_state as _orders_state

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
        except Exception as _e:
            log.warning(f"_wait_for_order_book_clear: openOrders() failed ({_e}), treating as clear")
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
                except Exception as _e:
                    log.warning(f"FLATTEN: qualifyContracts failed for {sym} option ({_e}), proceeding with SMART")
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
            try:
                from event_log import append_close as _el_flat
                _flat_price = float(info.get("current") or info.get("entry") or 0.0)
                _flat_tid = info.get("trade_id") or f"{sym}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
                _el_flat(_flat_tid, sym,
                         exit_price=_flat_price,
                         pnl=0.0,
                         exit_reason="flatten_all",
                         hold_minutes=0)
            except Exception:
                pass  # emergency path — never let logging block execution
            closed += 1
        except Exception as e:
            log.error(f"🚨 FLATTEN failed for {sym}: {e}")

    log.warning(f"🚨 FLATTEN ALL complete — {closed} orders placed, tracker cleared")


def _close_position_record(
    key: str,
    exit_price: float,
    exit_reason: str,
    pnl: float = 0.0,
    hold_minutes: int = 0,
) -> None:
    """Single exit point for all position closes.

    Writes POSITION_CLOSED to event_log, writes training record, removes from
    active_trades, and saves positions file — in that order. No caller may delete
    directly from active_trades without going through here.
    """
    _t = active_trades.get(key, {})
    if not _t:
        return
    _sym = _t.get("symbol", key.split("|")[0])
    _tid = _t.get("trade_id", key)
    try:
        from event_log import append_close as _el
        _el(_tid, _sym, exit_price=exit_price, pnl=pnl,
            exit_reason=exit_reason, hold_minutes=hold_minutes)
    except Exception as _e:
        log.warning("_close_position_record: event_log write failed for %s: %s", key, _e)
    try:
        from training_store import append as _ts
        _now_ts = datetime.now(UTC).isoformat()
        _ts({
            "trade_id": _tid,
            "symbol": _sym,
            "direction": _t.get("direction", "LONG"),
            "trade_type": _t.get("trade_type", "INTRADAY"),
            "instrument": _t.get("instrument", "stock"),
            "fill_price": float(_t.get("entry", 0.0)),
            "intended_price": float(_t.get("intended_price") or _t.get("entry") or 0.0),
            "exit_price": exit_price,
            "pnl": pnl,
            "hold_minutes": hold_minutes,
            "exit_reason": exit_reason,
            "regime": _t.get("entry_regime") or _t.get("regime", "UNKNOWN"),
            "signal_scores": _t.get("signal_scores") or {},
            "conviction": float(_t.get("conviction") or 0),
            "score": int(_t.get("score") or 0),
            "entry_thesis": _t.get("entry_thesis", ""),
            "ts_fill": _t.get("open_time") or _now_ts,
            "ts_close": _now_ts,
        })
    except Exception as _e:
        log.warning("_close_position_record: training_store write failed for %s: %s", key, _e)
    with _trades_lock:
        active_trades.pop(key, None)
    _save_positions_file()


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
                    cancel_with_reason(eib, t.order, f"cancel open order for {sym} on position close")
                    log.info(f"Close {trade_key}: Cancelled order {t.order.orderId}")
                except Exception as _e:
                    log.warning(f"Close {trade_key}: Could not cancel order {t.order.orderId}: {_e}")
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
    except Exception as _e:
        log.warning(f"Close {trade_key}: qualifyContracts failed ({_e}), proceeding with SMART")

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
    from bot_state import clog as _clog; _clog("TRADE", f"📤 INSTANT close: {detail}")

    # 4) Mark EXITING — the deferred handler in update_positions_from_ibkr() will
    #    confirm the fill from IBKR and write POSITION_CLOSED via _close_position_record().
    #    Never delete directly here: that would silently drop the closed trade from
    #    the event_log and dashboard.
    tracker_key = _ibkr_item_to_key(target)
    _active_key = tracker_key if tracker_key in active_trades else (trade_key if trade_key in active_trades else None)
    if _active_key:
        with _trades_lock:
            active_trades[_active_key]["status"] = "EXITING"
            active_trades[_active_key]["close_order_id"] = close_trade.order.orderId
            active_trades[_active_key]["pending_exit_reason"] = "manual_close"
        _save_positions_file()
    else:
        log.warning("close_position %s: key not found in active_trades — position may already be gone", sym)

    return detail


def _build_ibkr_execution_index(ib: IB) -> dict[str, list]:
    """Return {symbol: [Fill, ...] newest-first} from today's IBKR executions.

    One blocking reqExecutions() call per reconcile cycle. Returns {} on any
    failure so callers degrade gracefully to estimated prices.
    """
    try:
        fills = ib.reqExecutions()
        if not isinstance(fills, list):
            return {}
        index: dict[str, list] = {}
        for fill in fills:
            sym = getattr(fill.contract, "symbol", None)
            if sym:
                index.setdefault(sym, []).append(fill)
        for sym in index:
            index[sym].sort(key=lambda f: getattr(f.execution, "time", ""), reverse=True)
        return index
    except Exception as _e:
        log.debug("Reconcile: reqExecutions failed (non-fatal): %s", _e)
        return {}


def _resolve_cwd_exit_price(trade: dict, exec_index: dict) -> tuple[float, str]:
    """Return (exit_price, source) for a position closed while the bot was down.

    Tries the IBKR execution index first (real fill); falls back to the last
    polled market price stored in the position dict.
    IBKR execution sides: "SLD" = sold (LONG exit), "BOT" = bought (SHORT exit).
    """
    is_short = trade.get("direction", "LONG") == "SHORT"
    exit_side = "BOT" if is_short else "SLD"
    for fill in exec_index.get(trade.get("symbol", ""), []):
        if getattr(fill.execution, "side", "") == exit_side:
            avg = getattr(fill.execution, "avgPrice", None)
            if avg:
                return float(avg), "ibkr_fill"
    return float(trade.get("current") or trade.get("entry", 0)), "estimated"


def _open_date_before_today(open_time: str, today: date) -> bool:
    try:
        return datetime.fromisoformat(open_time.replace("Z", "+00:00")).date() < today
    except Exception:
        return False


def _force_exit_stale_intraday(ib: IB, key: str, sym: str, pos: dict) -> None:
    """Cancel any stale bracket/close orders then force-exit at market."""
    for oid_field in ("close_order_id", "sl_order_id", "tp_order_id"):
        oid = pos.get(oid_field)
        if oid:
            try:
                _cancel_ibkr_order_by_id(ib, oid)
            except Exception:
                pass
    _safe_update_trade(key, {"status": "ACTIVE"})
    try:
        from orders_core import execute_sell as _execute_sell
        _execute_sell(ib, sym, reason="stale_intraday_force_exit")
    except Exception as _se:
        log.error("_force_exit_stale_intraday %s: execute_sell failed — %s", sym, _se)


def reset_stale_exits(ib: IB) -> list[str]:
    """Per-scan-cycle self-correction for stuck EXITING positions.

    Three failure modes handled:
      1. Order vanished — close_order_id not in IBKR open orders (cancelled/expired/never filed).
      2. Order live but permanently unfillable — SELL limit >1% above market, or
         BUY limit >1% below market.  The 1% threshold is far above the 0.2% taker
         offset used by execute_sell, so it only fires when the limit is clearly wrong.
      3. Pre-market GTC limit stranded at regular session open — extended-hours limits
         never become MKT orders on their own.  At regular session open, cancel the GTC
         limit and reset to ACTIVE so execute_sell places a proper MKT order immediately.

    All cases: cancel the order (if present), reset status to ACTIVE.
    execute_sell re-attempts on the same or next scan cycle.
    """
    try:
        live_trades = {t.order.orderId: t for t in ib.openTrades()}
    except Exception as e:
        log.warning("reset_stale_exits: failed to fetch IBKR open orders — %s", e)
        return []

    regular_session = is_options_market_open()  # True 9:30–16:00 ET

    reset: list[str] = []
    with _trades_lock:
        for key, pos in list(active_trades.items()):
            if pos.get("status") != "EXITING":
                continue
            close_oid = pos.get("close_order_id")
            sym = pos.get("symbol", key)

            # ── Case 1: order is gone from IBKR ─────────────────────────────
            if not close_oid or close_oid not in live_trades:
                log.warning(
                    "reset_stale_exits: %s close order #%s not in IBKR — resetting to ACTIVE",
                    sym, close_oid,
                )
                _safe_update_trade(key, {"status": "ACTIVE", "close_order_id": None})
                reset.append(sym)
                continue

            ibkr_trade = live_trades[close_oid]
            order = ibkr_trade.order
            if order.orderType not in ("LMT", "LMT LMT"):
                continue

            current_price = pos.get("current", 0)

            # ── Case 3: regular session open — GTC limit should be MKT ──────
            # Extended-hours limits are never auto-converted to MKT on session
            # open.  Cancel the stale GTC and reset so execute_sell fires MKT.
            if regular_session and getattr(order, "tif", "").upper() in ("GTC", ""):
                log.warning(
                    "reset_stale_exits: %s close order #%s is a GTC LMT ($%.2f) "
                    "but regular session is open — upgrading to MKT",
                    sym, close_oid, order.lmtPrice,
                )
                try:
                    _cancel_ibkr_order_by_id(ib, close_oid)
                except Exception as _ce:
                    log.error("reset_stale_exits: cancel #%s failed — %s", close_oid, _ce)
                _safe_update_trade(key, {"status": "ACTIVE", "close_order_id": None})
                reset.append(sym)
                continue

            # ── Case 2: order is live but limit is on the wrong side of market ─
            if current_price <= 0:
                continue
            lmt = order.lmtPrice
            action = order.action  # "SELL" or "BUY"
            unfillable = (
                (action == "SELL" and lmt > current_price * 1.01)
                or (action == "BUY" and lmt < current_price * 0.99)
            )
            if unfillable:
                log.warning(
                    "reset_stale_exits: %s close order #%s is unfillable "
                    "(%s LMT $%.2f vs current $%.2f) — cancelling and resetting to ACTIVE",
                    sym, close_oid, action, lmt, current_price,
                )
                try:
                    _cancel_ibkr_order_by_id(ib, close_oid)
                except Exception as _ce:
                    log.error("reset_stale_exits: cancel #%s failed — %s", close_oid, _ce)
                _safe_update_trade(key, {"status": "ACTIVE", "close_order_id": None})
                reset.append(sym)

    return reset


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
    _orders_state._reconcile_in_progress = True
    saved_positions = _load_positions_file()
    if saved_positions:
        log.info(f"Loaded {len(saved_positions)} saved position(s) from disk for metadata restore")

    def _find_saved(key: str, sym: str, instrument: str) -> dict:
        """
        Metadata recovery via event_log (JSONL write-ahead log):
          Primary: event_log.open_trades() — trade_id-keyed dict with ORDER_INTENT
                   metadata merged in (trade_type, conviction, regime, signal_scores).
          Fallback: positions.json cache (for the window between IBKR fill and
                    the next event_log write — extremely rare).
        """
        # Primary: event_log confirmed fills (ORDER_FILLED with no POSITION_CLOSED)
        try:
            from event_log import open_trades as _el_open
            el_trades = _el_open()
            for v in el_trades.values():
                if v.get("symbol") == sym and v.get("trade_type") and v["trade_type"] != "UNKNOWN":
                    return v
        except Exception as _el_err:
            log.warning("_find_saved %s: event_log read failed: %s", key, _el_err)
        # Fallback: positions.json exact key
        if key in saved_positions:
            hit = saved_positions[key]
            if hit.get("trade_type") and hit["trade_type"] != "UNKNOWN":
                return hit
        # Fallback: positions.json symbol+instrument scan
        for v in saved_positions.values():
            if (
                v.get("symbol") == sym and v.get("instrument") == instrument
                and v.get("trade_type") and v["trade_type"] != "UNKNOWN"
            ):
                return v
        # Last resort: scan all ORDER_INTENT records in event_log including those for
        # fully-closed prior legs. Covers multi-leg re-entries where the latest leg's
        # intent was recorded, filled, and closed before this reconcile ran.
        try:
            from event_log import last_intent_for_symbol as _last_intent
            _intent = _last_intent(sym)
            if _intent.get("trade_type") and _intent["trade_type"] != "UNKNOWN":
                return _intent
        except Exception as _li_err:
            log.debug("_find_saved %s: last_intent_for_symbol failed: %s", key, _li_err)
        return {}

    try:
        # ── Step 1: restore our own position ledger ───────────────────────────
        # Always load from positions.json (symbol-keyed, stable across restarts).
        # event_log is used ONLY inside _find_saved() for metadata recovery.
        # Never update active_trades directly from event_log.open_trades() —
        # that function keys by trade_id strings, not symbol strings, and injecting
        # trade_id keys into a symbol-keyed dict breaks all subsequent reconciliation.
        if saved_positions:
            with _trades_lock:
                active_trades.update(saved_positions)
            log.info(f"Restored {len(saved_positions)} position(s) from positions.json.")

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
        except Exception as _e:
            log.warning(f"Reconcile: ib.positions() failed ({_e}) — FX positions may be misidentified as closed")

        # ── Step 3: detect positions closed while bot was down ────────────────
        # In our store but not in IBKR → SL/TP was triggered or manually closed.
        #
        # Safety guard: if IBKR returned 0 items but we have stored positions,
        # this is almost certainly an account-data timing issue (TWS hasn't
        # pushed updatePortfolio callbacks yet), NOT that every position closed.
        # Purging everything in this case would destroy our entire position state.
        # Skip the purge and let the next reconcile cycle handle it correctly.
        keys_to_remove = []
        closed_while_down = []  # collect (advice_id, exit_price, pnl) to close Opus loop after lock
        trades_closed_while_down = []  # collect full trade dicts for CLOSE log_trade calls
        _stored_non_pending = [
            k for k, v in active_trades.items()
            if v.get("status") != "PENDING"
        ]
        _n_stored = len(_stored_non_pending)
        _n_ibkr = len(ibkr_keys)
        # Coverage ratio: what fraction of stored non-pending positions did IBKR confirm?
        # If IBKR confirms < min_ibkr_coverage_to_sweep of what we track, TWS callbacks
        # are likely still arriving (partial delivery) — block the sweep to avoid
        # fabricating phantom closes with stale estimated exit prices.
        # PENDING cancellation is exempt: it cross-checks ib.openTrades() independently.
        _min_coverage = CONFIG.get("min_ibkr_coverage_to_sweep", 0.5)
        _coverage = _n_ibkr / _n_stored if _n_stored > 0 else 1.0
        _block_cwd_sweep = False
        if not ibkr_keys and _stored_non_pending:
            _block_cwd_sweep = True
            log.error(
                "Reconcile: IBKR returned 0 portfolio positions but we have %d stored position(s) "
                "— skipping closed-while-down purge (likely account data not ready). "
                "Positions will be re-checked on next reconcile cycle.",
                _n_stored,
            )
        elif ibkr_keys and _n_stored > 1 and _coverage < _min_coverage:
            _block_cwd_sweep = True
            log.error(
                "Reconcile: IBKR confirmed %d/%d stored position(s) (%.0f%% < %.0f%% threshold) — "
                "partial TWS data delivery suspected, skipping closed-while-down purge. "
                "Positions will be re-checked on next reconcile cycle.",
                _n_ibkr, _n_stored, _coverage * 100, _min_coverage * 100,
            )
        # Build execution index before acquiring the lock — one network call,
        # used to resolve actual fill prices for closed-while-down positions.
        _exec_index = _build_ibkr_execution_index(ib) if not _block_cwd_sweep else {}
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
                            except Exception as _e:
                                log.warning(f"Reconcile: openTrades() check failed for PENDING {key} order #{order_id} ({_e}) — assuming still live")
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
                    elif not _block_cwd_sweep:
                        _cwd_px, _cwd_src = _resolve_cwd_exit_price(trade, _exec_index)
                        if _cwd_src == "estimated":
                            log.warning(
                                "Position %s closed while bot was down — no IBKR fill found, "
                                "using estimated exit price %.4f (last polled market price)",
                                key, _cwd_px,
                            )
                        else:
                            log.info(
                                "Position %s closed while bot was down — exit price %.4f from IBKR fill",
                                key, _cwd_px,
                            )
                        keys_to_remove.append(key)
                        _trade_copy = dict(trade)
                        _trade_copy["_cwd_exit_px"] = _cwd_px
                        _trade_copy["_cwd_exit_src"] = _cwd_src
                        # Recover trade_type for positions that were stored as UNKNOWN —
                        # look up the original ORDER_INTENT so CLOSE records carry correct metadata.
                        if _trade_copy.get("trade_type") in (None, "", "UNKNOWN"):
                            try:
                                from event_log import get_intent as _get_intent, last_intent_for_symbol as _last_intent
                                _tid = _trade_copy.get("trade_id", "")
                                _recovered = _get_intent(_tid) if _tid else {}
                                if not _recovered.get("trade_type"):
                                    _recovered = _last_intent(_trade_copy.get("symbol", ""))
                                if _recovered.get("trade_type") and _recovered["trade_type"] != "UNKNOWN":
                                    _trade_copy["trade_type"] = _recovered["trade_type"]
                            except Exception:
                                pass
                        trades_closed_while_down.append(_trade_copy)
                        if trade.get("advice_id"):
                            closed_while_down.append(
                                {
                                    "advice_id": trade["advice_id"],
                                    "exit_price": _cwd_px,
                                    "pnl": float(trade.get("pnl", 0.0)),
                                }
                            )

        for key in keys_to_remove:
            _safe_del_trade(key)

        # Write CLOSE log records for positions that vanished while the bot was down.
        # Without this, trades.json shows them as permanently open (ghost entries).
        for _t in trades_closed_while_down:
            try:
                from learning import log_trade as _log_trade
                _exit_px = _t.pop("_cwd_exit_px", float(_t.get("current") or _t.get("entry", 0)))
                _exit_src = _t.pop("_cwd_exit_src", "estimated")
                _entry_px = float(_t.get("entry", 0))
                _qty = int(_t.get("qty", 1))
                _is_short = _t.get("direction", "LONG") == "SHORT"
                _pnl = round(((_entry_px - _exit_px) if _is_short else (_exit_px - _entry_px)) * _qty, 2)
                _pnl_pct = round(_pnl / (_entry_px * _qty), 4) if _entry_px * _qty else 0
                _log_trade(
                    trade=_t,
                    agent_outputs={},
                    regime={},
                    action="CLOSE",
                    outcome={
                        "exit_price": _exit_px,
                        "exit_price_source": _exit_src,
                        "pnl": _pnl,
                        "pnl_pct": _pnl_pct,
                        "reason": "closed_while_bot_down",
                    },
                )
                # Also write POSITION_CLOSED to event_log so open_trades() does not
                # re-surface this position as open on the next reconcile pass.
                _tid = _t.get("trade_id", "")
                if _tid:
                    try:
                        from event_log import append_close as _el_close
                        _open_mins = 0
                        _ot = _t.get("open_time") or _t.get("ts") or ""
                        if _ot:
                            try:
                                _dt = datetime.fromisoformat(_ot.replace("Z", "+00:00"))
                                _open_mins = int((datetime.now(UTC) - _dt).total_seconds() / 60)
                            except Exception:
                                pass
                        _el_close(
                            trade_id=_tid,
                            symbol=_t.get("symbol", "?"),
                            exit_price=_exit_px,
                            pnl=_pnl,
                            exit_reason="closed_while_bot_down",
                            hold_minutes=_open_mins,
                        )
                    except Exception as _el_err:
                        log.warning("Reconcile: failed to write event_log POSITION_CLOSED for %s: %s", _t.get("symbol", "?"), _el_err)
            except Exception as _cwd_err:
                log.warning("Reconcile: failed to write CLOSE log for %s: %s", _t.get("symbol", "?"), _cwd_err)

        # (trade_advisor learning loop removed — deterministic sizing owns stops)

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
                elif is_fx:
                    # FX: IBKR is authoritative (Alpaca/TV don't carry forex)
                    if ibkr_price_for_validation > 0:
                        validated_price = ibkr_price_for_validation
                        src_desc = f"IBKR_FX=${ibkr_price_for_validation:.4f}"
                    else:
                        validated_price = ibkr_entry
                        src_desc = "IBKR returned no FX price — using entry"
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
                    ibkr_qty = abs(int(item.position))
                    stored_qty = active_trades[key].get("qty", ibkr_qty)
                    # Reconcile qty: partial fills or tranche closes mean IBKR holds
                    # a different count than tracked. Trust IBKR as ground truth for
                    # both options and stocks.
                    if ibkr_qty != stored_qty:
                        log.warning(
                            f"Reconcile {key}: qty mismatch — tracked={stored_qty}, "
                            f"IBKR={ibkr_qty}. Correcting to {ibkr_qty}."
                        )
                        stored_qty = ibkr_qty
                        with _trades_lock:
                            active_trades[key]["qty"] = ibkr_qty
                            if is_option:
                                active_trades[key]["contracts"] = ibkr_qty
                        # Write POSITION_QTY_CORRECTED so open_trades() replay returns
                        # the right qty after a restart even if positions.json is stale.
                        _qc_tid = active_trades.get(key, {}).get("trade_id", "")
                        if _qc_tid:
                            try:
                                from event_log import append_qty_correction as _el_qc
                                _el_qc(_qc_tid, sym, corrected_qty=ibkr_qty)
                            except Exception as _qc_err:
                                log.warning("Reconcile %s: POSITION_QTY_CORRECTED write failed: %s", key, _qc_err)
                    # Detect phantom/corrupted entry prices: if the stored entry
                    # deviates >50% from IBKR's averageCost the metadata is stale
                    # (e.g. a test placeholder like $100 for a $269 stock).
                    # Correct the entry so PnL calculations and SL/TP logic are valid.
                    if not is_option and not is_fx and ibkr_entry > 0 and stored_entry > 0:
                        _entry_deviation = abs(stored_entry - ibkr_entry) / ibkr_entry
                        if _entry_deviation > 0.50:
                            log.warning(
                                f"Reconcile {key}: stored entry ${stored_entry:.2f} deviates "
                                f"{_entry_deviation:.0%} from IBKR avgCost ${ibkr_entry:.2f} "
                                f"— phantom/stale metadata detected, correcting entry"
                            )
                            stored_entry = ibkr_entry
                            with _trades_lock:
                                active_trades[key]["entry"] = ibkr_entry
                    mult = 100 if is_option else 1
                    if stored_direction == "SHORT":
                        pnl = round((stored_entry - validated_price) * stored_qty * mult, 2)
                    else:
                        pnl = round((validated_price - stored_entry) * stored_qty * mult, 2)
                    with _trades_lock:
                        active_trades[key]["current"] = round(validated_price, 4)
                        active_trades[key]["pnl"] = pnl
                        active_trades[key]["realized_pnl"] = round(float(getattr(item, "realizedPNL", 0) or 0), 2)
                        # Preserve EXITING status: a close order is already live in IBKR.
                        # Overwriting to ACTIVE bypasses execute_sell's dedup guard, causing
                        # a new exit order every scan cycle until price moves to fill them all.
                        if active_trades[key].get("status") != "EXITING":
                            active_trades[key]["status"] = "ACTIVE"
                        active_trades[key]["_price_sources"] = src_desc
                        if is_option:
                            active_trades[key]["current_premium"] = round(validated_price, 4)
                        # Store IBKR's live price as a reference so price_updater can
                        # detect when Alpaca streaming quotes have drifted from IBKR.
                        if ibkr_price > 0:
                            active_trades[key]["ibkr_last"] = round(ibkr_price, 4)
                    log.debug(f"Reconcile {key}: price updated to ${validated_price:.4f} via {src_desc}")

                    # ── Metadata recovery for known positions with UNKNOWN trade_type ──
                    # Positions loaded from positions.json with UNKNOWN (e.g. after a
                    # migration or crash that cleared the store) get a second chance here.
                    stored_tt = active_trades[key].get("trade_type", "")
                    if not stored_tt or stored_tt == "UNKNOWN":
                        instrument_type = "option" if is_option else ("fx" if is_fx else "stock")
                        _saved = _find_saved(key, sym, instrument_type)
                        # Guard: only recover metadata when the saved intent's direction
                        # matches the live IBKR direction.  A direction mismatch means the
                        # UNKNOWN was set because this is an orphaned position from a flipped
                        # trade (e.g. a long TP overshoot leaving a net short).  Recovering
                        # trade_type in that case silently legitimises the orphan and prevents
                        # guardrails from force-exiting it.
                        _saved_dir = (_saved.get("direction") or "LONG").upper() if _saved else ""
                        _live_dir  = (active_trades[key].get("direction") or "LONG").upper()
                        _dir_ok    = (_saved_dir == _live_dir)
                        if not _dir_ok and _saved:
                            log.warning(
                                f"Reconcile {key}: metadata recovery BLOCKED — saved direction "
                                f"{_saved_dir!r} != live direction {_live_dir!r}. "
                                f"Keeping trade_type=UNKNOWN so guardrails force-exits this orphan."
                            )
                        if _saved and _saved.get("trade_type") and _saved["trade_type"] != "UNKNOWN" and _dir_ok:
                            log.info(
                                f"Reconcile {key}: late metadata recovery (trade_type={_saved.get('trade_type', '?')})"
                            )
                            with _trades_lock:
                                for _mf in (
                                    "trade_type", "conviction", "reasoning", "signal_scores",
                                    "agent_outputs", "entry_score", "open_time", "atr",
                                    "entry_regime", "entry_thesis", "pattern_id", "setup_type",
                                    "ic_weights_at_entry", "advice_id", "high_water_mark",
                                    "tranche_mode", "t1_qty", "t2_qty", "t1_status", "t1_order_id",
                                ):
                                    if _mf in _saved:
                                        active_trades[key][_mf] = _saved[_mf]
                                if active_trades[key].get("entry_regime", "UNKNOWN") == "UNKNOWN" and _saved.get("regime"):
                                    active_trades[key]["entry_regime"] = _saved["regime"]
                                if _saved.get("score", 0) > 0:
                                    active_trades[key]["score"] = _saved["score"]
                                active_trades[key].pop("metadata_status", None)
                                active_trades[key]["_metadata_restored"] = True
                            _save_positions_file()

                    # sl_order_id reattachment is handled by bracket_health.audit_bracket_orders()
                    # each scan cycle — no need to do it here at reconcile time.

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
                            ibkr_entry * (1.02 if not is_option else (1 + CONFIG.get("options_stop_loss", 0.20))), 4
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
                        try:
                            _exp_d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                            _dte_calc = (_exp_d - date.today()).days
                        except Exception as _e:
                            log.warning(f"DTE calc failed for {key} expiry '{expiry_str}': {_e} — defaulting to 0")
                            _dte_calc = 0
                        new_entry = {
                            "symbol": sym,
                            "instrument": "option",
                            "right": right,
                            "strike": c.strike,
                            "expiry_str": expiry_str,
                            "expiry_ibkr": raw_exp,
                            "dte": _dte_calc,
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
                            new_entry["entry_regime"] = _saved.get("entry_regime") or _saved.get("regime") or "UNKNOWN"
                            new_entry["entry_thesis"] = _saved.get("entry_thesis", "")
                            new_entry["pattern_id"] = _saved.get("pattern_id", "")
                            new_entry["high_water_mark"] = _saved.get("high_water_mark", ibkr_entry)
                            new_entry["ic_weights_at_entry"] = _saved.get("ic_weights_at_entry")
                            new_entry["advice_id"] = _saved.get("advice_id", "")
                            if _saved.get("score", 0) > 0:
                                new_entry["score"] = _saved["score"]
                            new_entry["_metadata_restored"] = True
                        try:
                            from orders_options import _option_exit_blacklist as _opt_blacklist
                            if key in _opt_blacklist:
                                log.warning(
                                    "Reconcile: %s is on option exit blacklist — skipping re-add to active_trades",
                                    key,
                                )
                                continue
                        except Exception:
                            pass
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
                            new_entry["entry_regime"] = _saved.get("entry_regime") or _saved.get("regime") or "UNKNOWN"
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
                            if _saved.get("sl"):
                                new_entry["sl"] = _saved["sl"]
                            if _saved.get("tp"):
                                new_entry["tp"] = _saved["tp"]
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
                            "reasoning": "Unknown position — not opened by this bot session. Will be force-exited.",
                            "direction": direction,
                            "pnl": pnl,
                            "status": "ACTIVE",
                            "_price_sources": src_desc,
                        }
                        _saved = _find_saved(key, sym, "stock")
                        if _saved:
                            log.info(
                                f"Reconcile {key}: restoring stock metadata (trade_type={_saved.get('trade_type', '?')})"
                            )
                            for _mf in (
                                "trade_type", "conviction", "reasoning", "signal_scores",
                                "agent_outputs", "entry_score", "open_time", "atr",
                                "entry_regime", "entry_thesis", "pattern_id", "setup_type",
                                "ic_weights_at_entry", "advice_id", "high_water_mark",
                                "tranche_mode", "t1_qty", "t2_qty", "t1_status", "t1_order_id",
                            ):
                                if _saved.get(_mf) is not None:
                                    new_entry[_mf] = _saved[_mf]
                            if new_entry.get("entry_regime", "UNKNOWN") == "UNKNOWN" and _saved.get("regime"):
                                new_entry["entry_regime"] = _saved["regime"]
                            if _saved.get("score", 0) > 0:
                                new_entry["score"] = _saved["score"]
                            if _saved.get("sl"):
                                new_entry["sl"] = _saved["sl"]
                            if _saved.get("tp"):
                                new_entry["tp"] = _saved["tp"]
                            new_entry["_metadata_restored"] = True
                        _safe_set_trade(key, new_entry)
                        # Write ORDER_FILLED to event log so crash recovery can reconstruct
                        # this position without needing positions.json. Guard: skip if the
                        # event log already has an open record for this symbol (handles the
                        # case where the bot restarts multiple times before the position closes).
                        _ext_tid = new_entry.get("trade_id", "")
                        if not _ext_tid:
                            # Try to recover the original trade_id from event_log before
                            # generating a new EXT id — a new id breaks the ORDER_INTENT →
                            # ORDER_FILLED → POSITION_CLOSED chain and corrupts ML records.
                            try:
                                from event_log import last_intent_for_symbol as _li
                                _recovered = _li(sym)
                                if _recovered.get("trade_id"):
                                    _ext_tid = _recovered["trade_id"]
                                    log.info(
                                        "Reconcile: recovered trade_id %s for %s from event_log",
                                        _ext_tid, sym,
                                    )
                            except Exception:
                                pass
                        if not _ext_tid:
                            _ext_tid = f"{sym}_EXT_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
                            log.warning(
                                "Reconcile: no trade_id found for %s — generating EXT id %s",
                                sym, _ext_tid,
                            )
                        new_entry["trade_id"] = _ext_tid
                        with _trades_lock:
                            if key in active_trades:
                                active_trades[key]["trade_id"] = _ext_tid
                        _el_already_open = False
                        try:
                            from event_log import open_trades as _el_check
                            _el_already_open = any(
                                v.get("symbol") == sym for v in _el_check().values()
                            )
                        except Exception:
                            pass
                        if not _el_already_open:
                            try:
                                from event_log import append_fill as _el_fill_ext
                                _el_fill_ext(_ext_tid, sym, fill_price=ibkr_entry, fill_qty=qty)
                            except Exception as _ef_err:
                                log.warning("Reconcile: ORDER_FILLED write failed for external stock %s: %s", sym, _ef_err)
                        # bracket_health.audit_bracket_orders() will reattach any existing
                        # SL order on the next scan cycle.

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
                    # FX: IBKR is authoritative (Alpaca/TV don't carry forex).
                    # Position objects lack marketPrice so fall back to entry.
                    validated_price = ibkr_entry
                    src_desc = "IBKR_FX entry (no marketPrice on Position object)"
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
                        if new_entry.get("entry_regime", "UNKNOWN") == "UNKNOWN" and _saved.get("regime"):
                            new_entry["entry_regime"] = _saved["regime"]
                        if _saved.get("score", 0) > 0:
                            new_entry["score"] = _saved["score"]
                        new_entry["_metadata_restored"] = True
                    _safe_set_trade(_pk, new_entry)
                    reconciled_count += 1
                except Exception as fx_err:
                    failed_count += 1
                    log.error(f"FX positions() reconcile failed for {_pk}: {fx_err}")
        except Exception as pos_err:
            log.warning(f"ib.positions() unavailable during FX reconciliation: {pos_err}")

        log.info(
            f"Reconciliation complete. Tracking {len(active_trades)} positions. (processed={reconciled_count}, failed={failed_count})"
        )

        # ── Step 5.5: force-exit stale INTRADAY/SCALP positions ──────────────
        # IBKR is the source of truth. If a stock INTRADAY/SCALP position still
        # exists here with an open_time from a prior trading day, EOD flat failed.
        # Exit at market immediately — no Apex, no debate.
        _today = date.today()
        with _trades_lock:
            _stale = [
                (k, v.get("symbol", k), dict(v))
                for k, v in active_trades.items()
                if (v.get("trade_type") or "").upper() in ("INTRADAY", "SCALP")
                and v.get("instrument", "stock") == "stock"
                and _open_date_before_today(v.get("open_time", ""), _today)
            ]
        for _k, _sym, _pos in _stale:
            log.warning(
                "Reconcile: stale INTRADAY %s open since %s — forcing market exit",
                _sym, (_pos.get("open_time") or "?")[:10],
            )
            _force_exit_stale_intraday(ib, _k, _sym, _pos)

        # ── Step 6: orphan alerting ───────────────────────────────────────────
        # Any position that ended up with trade_type UNKNOWN after all lookup
        # tiers means the bot made this trade but lost the metadata on restart.
        # Write to data/orphaned_positions.json so the gap is visible and
        # actionable (dashboard card).
        try:
            # Diagnostic snapshot: log trade_type for all positions at this point so we
            # can correlate with any "_safe_set_trade overwrite" warnings above.
            with _trades_lock:
                _tt_snapshot = {k: v.get("trade_type", "UNKNOWN") for k, v in active_trades.items()}
            log.debug("Reconcile Step 6 trade_type snapshot: %s", _tt_snapshot)

            import json as _json
            from pathlib import Path as _Path
            _orphan_file = _Path(CONFIG.get("positions_file", "data/positions.json")).parent / "orphaned_positions.json"
            _now_str = datetime.now(UTC).isoformat()
            _existing_orphans: dict = {}
            if _orphan_file.exists():
                try:
                    _existing_orphans = _json.loads(_orphan_file.read_text())
                except Exception:
                    _existing_orphans = {}
            _new_orphans: dict = {}
            with _trades_lock:
                for _ok, _ov in active_trades.items():
                    if _ov.get("trade_type", "UNKNOWN") in ("UNKNOWN", "", None):
                        _new_orphans[_ok] = {
                            "symbol": _ov.get("symbol", _ok),
                            "instrument": _ov.get("instrument", "stock"),
                            "direction": _ov.get("direction", "?"),
                            "qty": _ov.get("qty", 0),
                            "entry": _ov.get("entry", 0),
                            "detected_at": _existing_orphans.get(_ok, {}).get("detected_at", _now_str),
                            "reason": "metadata_lost_on_restart",
                        }
            if _new_orphans != _existing_orphans:
                _orphan_file.write_text(_json.dumps(_new_orphans, indent=2, default=str))
            if _new_orphans:
                log.warning(
                    "Reconcile: %d position(s) have UNKNOWN trade_type — metadata was lost on a prior restart. "
                    "Keys: %s. See data/orphaned_positions.json.",
                    len(_new_orphans),
                    list(_new_orphans.keys()),
                )
            elif _orphan_file.exists() and not _new_orphans:
                _orphan_file.unlink()
        except Exception as _oe:
            log.warning(f"Reconcile: orphan alerting failed: {_oe}")

    except Exception as e:
        log.error(f"Reconciliation error: {e}")
    finally:
        _orders_state._reconcile_in_progress = False
        _save_positions_file()  # always persist — runs even if reconcile errors partway through



def _validate_option_market_price(
    mkt_price: float,
    sym: str,
    right: str,
    strike: float,
    entry: float,
    context: str = "",
) -> float:
    """
    Validate an IBKR marketPrice value for an option position.

    Returns the price (rounded to 4dp) when valid, or 0 when it should be
    rejected.  Callers treat 0 as "keep previous price".

    Validation strategy — two layers:

    Layer 1 — structural bounds (options theory, no threshold needed):
      CALL premium < underlying_price   (can never cost more than buying the stock)
      PUT  premium < strike_price       (maximum put value = strike when underlying → 0)
      These bounds are violated only if IBKR returns the stock price instead of the
      option premium — a known paper-account quirk for illiquid / deeply OTM options.

    Layer 2 — 20× entry heuristic (fallback when underlying price is unavailable):
      If the Alpaca price fetch fails we still need a safety net.  An option moving
      20× from its recorded entry in a single monitoring interval is implausible for
      normal ETF options; reject to avoid poisoning position P&L and agent context.
    """
    if mkt_price <= 0:
        return 0

    tag = f" [{context}]" if context else ""

    # ── Layer 1: structural bounds ────────────────────────────────────────────
    underlying: float | None = None
    try:
        from alpaca_options import get_underlying_price as _get_ul

        underlying = _get_ul(sym)
    except Exception:
        pass

    if underlying and underlying > 0:
        if right == "C" and mkt_price >= underlying:
            log.warning(
                f"Option price rejected{tag} {sym} CALL: IBKR ${mkt_price:.2f} ≥ "
                f"underlying ${underlying:.2f} — structurally impossible for a call premium"
            )
            return 0
        if right == "P" and strike > 0 and mkt_price >= strike:
            log.warning(
                f"Option price rejected{tag} {sym} PUT: IBKR ${mkt_price:.2f} ≥ "
                f"strike ${strike:.2f} — structurally impossible for a put premium"
            )
            return 0
        # Passed structural check — price is valid
        return round(mkt_price, 4)

    # ── Layer 2: 20× entry heuristic (underlying fetch failed) ───────────────
    if entry > 0 and mkt_price > entry * 20:
        log.warning(
            f"Option price suspect{tag} {sym}: IBKR ${mkt_price:.2f} is "
            f"{mkt_price / entry:.0f}× entry ${entry:.4f} — "
            f"underlying unavailable; rejecting on 20× fallback"
        )
        return 0

    return round(mkt_price, 4)


def _build_ibkr_price_map(ib: IB) -> tuple[dict, set]:
    """Return (price_map, positions_keys) from IBKR portfolio/positions calls."""
    portfolio_items = ib.portfolio(CONFIG["active_account"])
    price_map = {_ibkr_item_to_key(item): item for item in portfolio_items if item.position != 0}

    positions_keys: set = set()
    try:
        for pos in ib.positions():
            if pos.position != 0:
                positions_keys.add(_ibkr_item_to_key(pos))
    except Exception as _pos_err:
        log.warning(f"ib.positions() fallback failed: {_pos_err}")

    return price_map, positions_keys


def _purge_closed_positions(ib: IB, price_map: dict, positions_keys: set) -> None:
    """Close positions no longer in IBKR (closed externally via SL/TP/manual)."""
    # Safety gate: empty IBKR response is more likely a data delivery failure than
    # every position being simultaneously closed — skip purge to protect tracker.
    _active_non_pending = sum(
        1 for v in active_trades.values()
        if v.get("status") not in ("PENDING", "RESERVED") and v.get("instrument") != "fx"
    )
    if _active_non_pending > 0 and not price_map and not positions_keys:
        log.warning(
            f"IBKR returned empty portfolio AND empty positions — skipping stale purge "
            f"to protect {_active_non_pending} tracked position(s) from false deletion"
        )
        return

    exec_index = _build_ibkr_execution_index(ib)

    def _detect_exit_reason(trade: dict, sym_fills: list) -> str:
        is_short = trade.get("direction", "LONG") == "SHORT"
        exit_side = "BOT" if is_short else "SLD"
        tp_oid = trade.get("tp_order_id")
        sl_oid = trade.get("sl_order_id")
        for fill in sym_fills:
            if getattr(fill.execution, "side", "") != exit_side:
                continue
            foid = getattr(fill.execution, "orderId", None)
            if tp_oid and foid and int(foid) == int(tp_oid):
                return "tp_hit"
            if sl_oid and foid and int(foid) == int(sl_oid):
                return "sl_hit"
            return "bracket_exit"
        return "stale_purge"

    stale_keys = []
    with _trades_lock:
        for k in active_trades:
            if k in price_map or k in positions_keys:
                continue
            if active_trades[k].get("status") in ("PENDING", "EXITING"):
                continue
            # FX can temporarily vanish from IBKR callbacks — never auto-purge.
            if active_trades[k].get("instrument") == "fx":
                log.debug(f"Keeping FX position {k} — FX exempt from stale-position purge")
                continue
            stale_keys.append(k)
        for k in stale_keys:
            _st = active_trades.get(k, {})
            _sym_k = _st.get("symbol", k.split("|")[0])
            _exit_px, _exit_src = _resolve_cwd_exit_price(_st, exec_index)
            _exit_reason = _detect_exit_reason(_st, exec_index.get(_sym_k, []))
            _entry_px = float(_st.get("entry") or _exit_px)
            _qty = int(_st.get("qty") or 1)
            _is_short = _st.get("direction") == "SHORT"
            _pnl = round((_entry_px - _exit_px if _is_short else _exit_px - _entry_px) * _qty, 2)
            log.warning(
                "Position %s no longer in IBKR — recording close "
                "(reason=%s exit=%.4f src=%s pnl=%.2f)",
                k, _exit_reason, _exit_px, _exit_src, _pnl,
            )
            _close_position_record(k, exit_price=_exit_px, exit_reason=_exit_reason, pnl=_pnl)


def _resolve_exiting_positions(ib: IB, price_map: dict, positions_keys: set) -> None:
    """Finalise EXITING positions whose close order has filled (gone from IBKR)."""
    exiting_keys = []
    with _trades_lock:
        for k, v in active_trades.items():
            if (v.get("status") == "EXITING"
                    and v.get("close_order_id")
                    and k not in price_map
                    and k not in positions_keys):
                exiting_keys.append(k)

    if not exiting_keys:
        return

    exec_index = _build_ibkr_execution_index(ib)
    now_ts = datetime.now(UTC).isoformat()

    for k in exiting_keys:
        _t = active_trades.get(k, {})
        if not _t:
            continue
        _sym = _t.get("symbol", k.split("|")[0])
        _exit_px, _exit_src = _resolve_cwd_exit_price(_t, exec_index)
        _entry_px = float(_t.get("entry", 0))
        _qty = int(_t.get("qty", 1))
        _is_short = _t.get("direction", "LONG") == "SHORT"
        _pnl = round((_entry_px - _exit_px if _is_short else _exit_px - _entry_px) * _qty, 2)
        _pnl_pct = round(_pnl / (_entry_px * _qty), 4) if _entry_px * _qty else 0
        _exit_reason = _t.get("pending_exit_reason", "ext_hours_limit_filled")
        _trade_id = _t.get("trade_id", k)
        log.info(
            "EXITING position %s (close order #%s) gone from IBKR — "
            "writing deferred CLOSE (exit=%.4f src=%s pnl=%.2f)",
            k, _t.get("close_order_id"), _exit_px, _exit_src, _pnl,
        )
        try:
            from event_log import append_close as _el_close_dfr
            _el_close_dfr(_trade_id, _sym, exit_price=_exit_px, pnl=_pnl,
                          exit_reason=_exit_reason, hold_minutes=0)
        except Exception as _e:
            log.warning("Deferred CLOSE event_log write failed for %s: %s", k, _e)
        try:
            from training_store import append as _ts_dfr
            _ts_dfr({
                "trade_id": _trade_id,
                "symbol": _sym,
                "direction": _t.get("direction", "LONG"),
                "trade_type": _t.get("trade_type") or "INTRADAY",
                "instrument": _t.get("instrument", "stock"),
                "fill_price": float(_t.get("entry", 0.0)),
                "intended_price": float(_t.get("intended_price") or _t.get("entry", 0.0)),
                "exit_price": _exit_px,
                "pnl": _pnl,
                "hold_minutes": 0,
                "exit_reason": _exit_reason,
                "regime": _t.get("entry_regime") or _t.get("regime", "UNKNOWN"),
                "signal_scores": _t.get("signal_scores") or {},
                "conviction": float(_t.get("conviction") or 0.0),
                "score": float(_t.get("score") or _t.get("entry_score") or 0.0),
                "ts_fill": _t.get("open_time") or now_ts,
                "ts_close": now_ts,
                "qty": _qty,
                "sl": float(_t.get("sl") or 0.0),
                "tp": float(_t.get("tp") or 0.0),
                "setup_type": _t.get("setup_type", ""),
                "pattern_id": _t.get("pattern_id", ""),
                "atr": float(_t.get("atr") or 0.0),
            })
        except Exception as _e:
            log.warning("Deferred CLOSE training_store write failed for %s: %s", k, _e)
        try:
            from learning import log_trade as _log_trade_ex
            _log_trade_ex(
                trade=_t, agent_outputs={}, regime={}, action="CLOSE",
                outcome={
                    "exit_price": _exit_px, "pnl": _pnl,
                    "pnl_pct": _pnl_pct, "reason": _exit_reason,
                },
            )
        except Exception as _e:
            log.warning("Deferred CLOSE log_trade failed for %s: %s", k, _e)
        with _trades_lock:
            with _recently_closed_lock:
                recently_closed[_sym] = now_ts
            active_trades.pop(k, None)

    _save_positions_file()


def _resolve_trimming_positions(ib: IB) -> None:
    """Finalise TRIMMING positions: confirm fill or revert to ACTIVE if cancelled."""
    trimming_keys = []
    with _trades_lock:
        for k, v in active_trades.items():
            if v.get("status") == "TRIMMING" and v.get("pending_trim_order_id"):
                trimming_keys.append(k)

    if not trimming_keys:
        return

    open_order_ids = {t.order.orderId for t in ib.openTrades()}
    exec_index = _build_ibkr_execution_index(ib)

    for k in trimming_keys:
        _t = active_trades.get(k, {})
        if not _t:
            continue
        _sym = _t.get("symbol", k.split("|")[0])
        _trim_oid = _t.get("pending_trim_order_id")
        _trim_qty = int(_t.get("pending_trim_qty") or 0)
        _trim_reason = _t.get("pending_trim_reason", "deferred_trim")
        _trim_tid = _t.get("trade_id", "")

        if _trim_oid in open_order_ids:
            continue  # still pending — nothing to do this cycle

        _trim_fill = next(
            (f for f in exec_index.get(_sym, []) if getattr(f.execution, "orderId", None) == _trim_oid),
            None,
        )

        if _trim_fill:
            _fill_px = float(getattr(_trim_fill.execution, "price", 0) or 0)
            _actual_filled = int(
                getattr(_trim_fill.execution, "cumQty", 0)
                or getattr(_trim_fill.execution, "shares", 0)
                or _trim_qty
            )
            _remaining = int(_t.get("qty", _trim_qty)) - _actual_filled
            _safe_update_trade(k, {
                "qty": max(0, _remaining),
                "status": "ACTIVE",
                "pending_trim_order_id": None,
                "pending_trim_qty": None,
                "pending_trim_reason": None,
            })
            if _trim_tid:
                try:
                    from event_log import append_trim as _el_trim_dfr
                    _el_trim_dfr(_trim_tid, _sym, qty_sold=_actual_filled,
                                 remaining_qty=max(0, _remaining),
                                 exit_price=_fill_px, exit_reason=_trim_reason)
                except Exception as _elt:
                    log.warning("Deferred TRIM event_log write failed for %s: %s", k, _elt)
            _sl_p = float(_t.get("sl") or 0)
            _tp_p = float(_t.get("tp") or 0)
            _direction = _t.get("direction", "LONG")
            _account = CONFIG.get("active_account", "")
            if _sl_p and _tp_p and _remaining > 0:
                try:
                    from ib_async import StopLimitOrder as _SLO, LimitOrder as _LO
                    _oca = f"decifer_{_sym}_{_trim_oid}_deferred_trim"
                    _sl_lmt = round(_sl_p * 0.99 if _direction == "LONG" else _sl_p * 1.01, 2)
                    _ba = "SELL" if _direction == "LONG" else "BUY"
                    _new_sl = _SLO(_ba, _remaining, _sl_p, _sl_lmt, account=_account, tif="GTC", outsideRth=True)
                    _new_sl.ocaGroup = _oca
                    _new_sl.ocaType = 1
                    _new_tp = _LO(_ba, _remaining, _tp_p, account=_account, tif="GTC", outsideRth=True)
                    _new_tp.ocaGroup = _oca
                    _new_tp.ocaType = 1
                    _ct = get_contract(_sym)
                    _sl_t = ib.placeOrder(_ct, _new_sl)
                    _tp_t = ib.placeOrder(_ct, _new_tp)
                    ib.sleep(0.5)
                    _safe_update_trade(k, {
                        "sl_order_id": _sl_t.order.orderId,
                        "tp_order_id": _tp_t.order.orderId,
                    })
                    log.info(
                        "Deferred TRIM %s: confirmed fill px=%.4f sold=%d remaining=%d "
                        "new bracket SL#%d @ %.2f TP#%d @ %.2f",
                        _sym, _fill_px, _trim_qty, _remaining,
                        _sl_t.order.orderId, _sl_p, _tp_t.order.orderId, _tp_p,
                    )
                except Exception as _be:
                    log.error("Deferred TRIM %s: bracket placement failed — %s", _sym, _be)
            else:
                log.info(
                    "Deferred TRIM %s: fill confirmed px=%.4f sold=%d remaining=%d "
                    "(no bracket — sl or tp missing)",
                    _sym, _fill_px, _trim_qty, _remaining,
                )
        else:
            log.warning(
                "Deferred TRIM %s: order #%s not in open trades or fills — "
                "trim SELL cancelled, reverting to ACTIVE at unchanged qty",
                _sym, _trim_oid,
            )
            _safe_update_trade(k, {
                "status": "ACTIVE",
                "pending_trim_order_id": None,
                "pending_trim_qty": None,
                "pending_trim_reason": None,
            })

    _save_positions_file()


def _resolve_orphaned_pending(ib: IB, price_map: dict, positions_keys: set) -> None:
    """Cancel and remove PENDING positions with no FillWatcher that have timed out."""
    from fill_watcher import _active_watchers
    from fill_watcher import _watchers_lock as _fw_lock

    _orphan_mins = CONFIG.get("fill_watcher", {}).get("orphan_timeout_mins", 5)

    with _trades_lock:
        pending_keys = [k for k in active_trades if active_trades[k].get("status") == "PENDING"]

    for _key in pending_keys:
        _trade_instrument = active_trades.get(_key, {}).get("instrument", "stock")
        _effective_timeout = _orphan_mins

        if _trade_instrument == "option" and (_key in price_map or _key in positions_keys):
            _ibkr_item = price_map.get(_key)
            _ibkr_filled_qty = abs(int(_ibkr_item.position)) if _ibkr_item is not None else None
            _opt_fill = round(float(_ibkr_item.averageCost) / 100, 4) if (_ibkr_item and _ibkr_item.averageCost) else None
            with _trades_lock:
                if _key in active_trades:
                    active_trades[_key]["status"] = "ACTIVE"
                    if _ibkr_filled_qty is not None:
                        _tracked_qty = active_trades[_key].get("qty", _ibkr_filled_qty)
                        if _ibkr_filled_qty != _tracked_qty:
                            log.warning(
                                f"PENDING option {_key}: qty mismatch on fill confirmation — "
                                f"tracked={_tracked_qty}, IBKR={_ibkr_filled_qty} (partial fill). "
                                f"Correcting to {_ibkr_filled_qty}."
                            )
                        active_trades[_key]["qty"] = _ibkr_filled_qty
                        active_trades[_key]["contracts"] = _ibkr_filled_qty
                    if _opt_fill and not active_trades[_key].get("_fill_confirmed"):
                        active_trades[_key]["entry"] = _opt_fill
                        active_trades[_key]["entry_premium"] = _opt_fill
                        active_trades[_key]["current_premium"] = _opt_fill
                        active_trades[_key]["high_water_mark"] = _opt_fill
                        active_trades[_key]["current"] = _opt_fill
            _tid = active_trades.get(_key, {}).get("trade_id", "")
            if _tid and _opt_fill and _ibkr_filled_qty and not active_trades.get(_key, {}).get("_fill_confirmed"):
                try:
                    from event_log import append_fill as _el_fill
                    _el_fill(_tid, _key.split("|")[0], fill_price=_opt_fill, fill_qty=_ibkr_filled_qty)
                    with _trades_lock:
                        if _key in active_trades:
                            active_trades[_key]["_fill_confirmed"] = True
                except Exception as _elf_err:
                    log.warning("Reconcile: ORDER_FILLED write failed for option %s: %s", _key, _elf_err)
            _src = "portfolio" if _key in price_map else "positions"
            log.info("PENDING option %s confirmed ACTIVE by IBKR %s — fill=%.4f", _key, _src, _opt_fill or 0)
            continue
        elif _trade_instrument == "option":
            _effective_timeout = CONFIG.get("fill_watcher", {}).get("option_orphan_timeout_mins", 480)
        elif _key in price_map or _key in positions_keys:
            _ibkr_item = price_map.get(_key)
            _actual_fill = round(float(_ibkr_item.averageCost), 4) if (_ibkr_item and _ibkr_item.averageCost) else None
            _actual_qty = abs(int(_ibkr_item.position)) if _ibkr_item else None
            with _trades_lock:
                if _key in active_trades:
                    active_trades[_key]["status"] = "ACTIVE"
                    if _actual_fill and not active_trades[_key].get("_fill_confirmed"):
                        active_trades[_key]["entry"] = _actual_fill
                        active_trades[_key]["high_water_mark"] = _actual_fill
                        active_trades[_key]["current"] = _actual_fill
                    if _actual_qty:
                        active_trades[_key]["qty"] = _actual_qty
            _tid = active_trades.get(_key, {}).get("trade_id", "")
            if _tid and _actual_fill and _actual_qty and not active_trades.get(_key, {}).get("_fill_confirmed"):
                try:
                    from event_log import append_fill as _el_fill
                    _el_fill(_tid, _key.split("|")[0], fill_price=_actual_fill, fill_qty=_actual_qty)
                    with _trades_lock:
                        if _key in active_trades:
                            active_trades[_key]["_fill_confirmed"] = True
                except Exception as _elf_err:
                    log.warning("Reconcile: ORDER_FILLED write failed for %s: %s", _key, _elf_err)
            _src = "portfolio" if _key in price_map else "positions"
            log.info("PENDING %s confirmed ACTIVE by IBKR %s — fill=%.4f", _key, _src, _actual_fill or 0)
            continue
        else:
            with _fw_lock:
                _has_watcher = _key in _active_watchers
            if _has_watcher:
                continue
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

        with _trades_lock:
            _trade = active_trades.get(_key)
        if _trade is None:
            continue

        _open_time_str = _trade.get("open_time")
        try:
            _open_dt = datetime.fromisoformat(_open_time_str)
            _age_mins = (datetime.now(UTC) - _open_dt).total_seconds() / 60
        except (ValueError, TypeError):
            _age_mins = _effective_timeout + 1

        if _age_mins < _effective_timeout:
            continue

        _oid = _trade.get("order_id")
        log.warning(
            f"Orphaned PENDING order {_key} order #{_oid} (age={_age_mins:.1f} min, no FillWatcher) — cancelling"
        )
        if _oid:
            _cancel_ibkr_order_by_id(ib, _oid)
        with _trades_lock:
            recently_closed[_key] = datetime.now(UTC).isoformat()
        _safe_del_trade(_key)


def _readd_missing_positions(ib: IB, price_map: dict) -> None:
    """Re-add positions that IBKR holds but the tracker is missing, recovering metadata."""

    def _find_saved_metadata(sym: str, instrument: str) -> dict:
        """Scan event_log and positions.json for metadata for this symbol+instrument."""
        try:
            from event_log import open_trades as _el_open2
            for v in _el_open2().values():
                if v.get("symbol") == sym and v.get("trade_type") and v["trade_type"] != "UNKNOWN":
                    return v
        except Exception:
            pass
        try:
            from event_log import pending_orders as _el_pending2
            for intent in reversed(_el_pending2()):
                if (intent.get("symbol") == sym
                        and intent.get("trade_type")
                        and intent["trade_type"] != "UNKNOWN"):
                    return intent
        except Exception:
            pass
        try:
            saved = _load_positions_file()
            for saved_val in saved.values():
                if (saved_val.get("symbol") == sym
                        and saved_val.get("instrument") == instrument
                        and saved_val.get("trade_type")):
                    return saved_val
        except Exception:
            pass
        try:
            from event_log import last_intent_for_symbol as _last_intent2
            intent = _last_intent2(sym)
            if intent.get("trade_type") and intent["trade_type"] != "UNKNOWN":
                return intent
        except Exception:
            pass
        return {}

    for ibkr_key, item in price_map.items():
        if ibkr_key in active_trades:
            continue
        try:
            is_opt = _is_option_contract(item.contract)
            is_fx = getattr(item.contract, "secType", "") == "CASH"
            sym = ibkr_key if is_fx else item.contract.symbol
            direction = "SHORT" if item.position < 0 else "LONG"
            qty = abs(int(item.position))
            ibkr_mkt = float(item.marketPrice)
            instrument = "option" if is_opt else ("fx" if is_fx else "stock")

            saved_meta = _find_saved_metadata(sym, instrument)
            metadata_restored = bool(saved_meta)

            if is_opt:
                entry = round(float(item.averageCost) / 100, 4)
                c = item.contract
                raw_exp = str(c.lastTradeDateOrContractMonth)
                _right = "C" if c.right in ("C", "CALL") else "P"
                _strike = float(c.strike)
                _validated_mkt = _validate_option_market_price(
                    ibkr_mkt, sym, _right, _strike, entry, context="re-sync"
                )
                validated = _validated_mkt if _validated_mkt > 0 else entry
                expiry_str = (f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                              if len(raw_exp) == 8 and raw_exp.isdigit() else raw_exp)
                mult = 100
                pnl = round((entry - validated if direction == "SHORT" else validated - entry) * qty * mult, 2)
                try:
                    _dte_calc2 = (datetime.strptime(expiry_str, "%Y-%m-%d").date() - date.today()).days
                except Exception:
                    _dte_calc2 = 0
                ibkr_fields = {
                    "symbol": sym, "instrument": "option", "right": _right,
                    "strike": c.strike, "expiry_str": expiry_str, "expiry_ibkr": raw_exp,
                    "dte": _dte_calc2, "contracts": qty, "entry_premium": entry,
                    "current_premium": validated, "entry": entry, "current": validated,
                    "qty": qty,
                    "sl": round(entry * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
                    "tp": round(entry * (1 + CONFIG.get("options_profit_target", 1.00)), 4),
                    "direction": direction, "pnl": pnl, "status": "ACTIVE",
                }
                if metadata_restored:
                    new_entry = {**saved_meta, **ibkr_fields}
                    log.warning(
                        f"Re-added missing option {ibkr_key} from IBKR — "
                        f"metadata RESTORED from disk (trade_type={saved_meta.get('trade_type', '?')})"
                    )
                else:
                    new_entry = {
                        **ibkr_fields, "score": 0, "trade_type": "UNKNOWN",
                        "conviction": 0.0, "entry_regime": "UNKNOWN",
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
                    "symbol": sym, "instrument": instrument,
                    "entry": entry, "current": round(validated, 4),
                    "qty": qty, "sl": sl, "tp": tp,
                    "direction": direction, "pnl": pnl, "status": "ACTIVE",
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
                        **ibkr_fields, "score": 0, "trade_type": "UNKNOWN",
                        "conviction": 0.0, "entry_regime": "UNKNOWN",
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


def _refresh_position_prices(ib: IB, price_map: dict) -> None:
    """Update current price and P&L for all tracked positions using 3-way validation."""
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
                    ibkr_price = _validate_option_market_price(
                        mkt_price, sym, trade.get("right", ""),
                        float(trade.get("strike", 0) or 0), entry, context=key,
                    )
                else:
                    ibkr_price = mkt_price

        if ibkr_price == 0 and trade.get("instrument") == "fx":
            try:
                _fx_contract = get_contract(sym, "fx")
                ib.qualifyContracts(_fx_contract)
                ibkr_price = _get_ibkr_price(ib, _fx_contract, fallback=0)
            except Exception as _fx_err:
                log.debug(f"FX price fetch for {sym}: {_fx_err}")

        if is_option:
            if ibkr_price > 0:
                validated_price = ibkr_price
                src_desc = f"IBKR_OPT=${ibkr_price:.2f}"
            else:
                stored = trade.get("current", 0)
                if entry > 0 and stored > entry * 20:
                    log.warning(
                        f"Option {key}: stored current ${stored:.2f} looks like "
                        f"underlying price (>{20}× entry ${entry:.4f}) — resetting to entry"
                    )
                    trade["current"] = entry
                    trade["current_premium"] = entry
                    trade["pnl"] = 0.0
                else:
                    log.warning(f"No IBKR price for option {key} — keeping previous ${stored:.2f}")
                continue
        elif trade.get("instrument") == "fx":
            if ibkr_price > 0:
                validated_price = ibkr_price
                src_desc = f"IBKR_FX=${ibkr_price:.4f}"
            else:
                log.warning(f"No IBKR price for FX {key} — keeping previous ${trade.get('current', 0):.4f}")
                continue
        else:
            validated_price, src_desc = _validate_position_price(sym, ibkr_price, entry)

        if validated_price > 0:
            trade["current"] = round(validated_price, 4)
            if is_option:
                trade["current_premium"] = round(validated_price, 4)
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


def update_positions_from_ibkr(ib: IB):
    """
    Refresh current price and P&L for all tracked positions. Called on every scan.

    Delegates each responsibility to a focused helper so no cross-scope variable
    collisions are possible:
      1. _build_ibkr_price_map      — portfolio + positions from IBKR
      2. _purge_closed_positions    — positions closed externally (SL/TP/manual)
      3. _resolve_exiting_positions — deferred CLOSE for filled EXITING orders
      4. _resolve_trimming_positions — deferred TRIM resolution
      5. _resolve_orphaned_pending  — cancel timed-out PENDING with no FillWatcher
      6. _readd_missing_positions   — re-add positions IBKR has that tracker lost
      7. _refresh_position_prices   — IBKR market price → 3-way validation → P&L
    """
    try:
        price_map, positions_keys = _build_ibkr_price_map(ib)
        _purge_closed_positions(ib, price_map, positions_keys)
        _resolve_exiting_positions(ib, price_map, positions_keys)
        _resolve_trimming_positions(ib)
        _resolve_orphaned_pending(ib, price_map, positions_keys)
        _readd_missing_positions(ib, price_map)
        _refresh_position_prices(ib, price_map)
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
