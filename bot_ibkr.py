#!/usr/bin/env python3
"""
bot_ibkr.py — IBKR connection lifecycle for the Decifer trading bot.

Covers: connect, disconnect handler, auto-reconnect (exponential backoff),
heartbeat, subscription registry, P&L subscription, trade backfill, and
order sync.

NOTE: All functions access `bot_state.ib` at *call time* (not at import time)
so that test patching via `patch.object(bot, "ib", mock)` propagates correctly
through the module shim in bot.py.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

import bot_state
from bot_state import _reconnect_lock, _subscription_registry, clog, dash
from config import CONFIG


def cancel_with_reason(ib, order, reason: str) -> None:
    """Cancel an IBKR order and register a reason so the 202 callback logs it."""
    bot_state._cancel_reasons[order.orderId] = reason
    ib.cancelOrder(order)

log = logging.getLogger("decifer.bot")

# Ensure reconnect/heartbeat keys exist — they may be absent in minimal test configs
# that are registered before config.py fully loads (e.g. test_bot.py's fake config).
CONFIG.setdefault("heartbeat_interval_secs", 1200)
CONFIG.setdefault("reconnect_max_attempts", 10)
CONFIG.setdefault("reconnect_max_wait_secs", 60)
CONFIG.setdefault("reconnect_base_wait_secs", 1)
CONFIG.setdefault("reconnect_alert_webhook", "")


# ── Subscription registry helpers ────────────────────────────────────────────


def _register_subscription(key: str, params: dict) -> None:
    """Record a market-data or PnL subscription so it can be restored after reconnect."""
    _subscription_registry[key] = params


def _unregister_subscription(key: str) -> None:
    """Remove a subscription from the registry."""
    _subscription_registry.pop(key, None)


def _restore_subscriptions() -> None:
    """
    Re-subscribe to all registered market data and PnL feeds after a reconnect.
    Called once the new IB connection is fully established.
    """
    ib = bot_state.ib  # read at call time for test-patch support
    if not _subscription_registry:
        log.info("No subscriptions to restore after reconnect.")
        return

    log.info(f"Restoring {len(_subscription_registry)} subscription(s) after reconnect…")
    for key, params in list(_subscription_registry.items()):
        sub_type = params.get("type")
        try:
            if sub_type == "pnl":
                account = params.get("account", CONFIG.get("active_account", ""))
                bot_state._pnl_subscription = ib.reqPnL(account)
                log.info(f"  ✔ Re-subscribed PnL for account {account}")
            elif sub_type == "account":
                account = params.get("account", CONFIG.get("active_account", ""))
                ib.reqAccountUpdates(account)
                log.info(f"  ✔ Re-subscribed account updates for {account}")
            elif sub_type == "positions":
                ib.reqPositions()
                log.info("  ✔ Re-requested positions")
            elif sub_type == "ticker":
                from ib_async import Stock

                contract = Stock(key, "SMART", "USD")
                ib.reqMktData(contract, "", False, False)
                log.info(f"  ✔ Re-subscribed market data for {key}")
            else:
                log.warning(f"  ⚠ Unknown subscription type '{sub_type}' for key '{key}' — skipped")
        except Exception as exc:
            log.error(f"  ✗ Failed to restore subscription '{key}': {exc}")


# ── ExecId correction helper ─────────────────────────────────────────────────


def _exec_id_prefix(eid: str) -> str:
    """
    Strip IBKR correction suffix to get the base execution ID.
    IBKR sends corrected executions with the same prefix but a new numeric
    suffix (e.g. '0001234.01' → corrected as '0001234.02').  Deduplication
    must use the prefix so that both the original and its correction resolve
    to the same execution record.
    """
    parts = eid.rsplit(".", 1)
    return parts[0] if len(parts) > 1 else eid


# ── IBKR error / message handler ─────────────────────────────────────────────

# Codes that IBKR sends as informational system messages — not real errors.
_INFORMATIONAL_CODES = frozenset(
    {
        2100,
        2101,
        2102,
        2103,
        2104,
        2105,
        2106,
        2107,
        2108,
        2119,
        2158,
        10167,
    }
)


def _on_ibkr_error(req_id: int, error_code: int, error_string: str, contract) -> None:
    """
    Handles all messages from ib.errorEvent.

    Routing by code range:
    - Informational (2104, 2106, 2158 …) → debug-level log only
    - 1100  → TWS lost IB servers — pause order awareness
    - 1101  → reconnected, data LOST — must re-subscribe
    - 1102  → reconnected, data maintained — nothing to do
    - 507   → socket EOF — trigger reconnect
    - 326   → duplicate clientId — fatal, abort bot
    - 200   → ambiguous/unknown contract
    - 201   → order rejected — log advancedOrderRejectJson if present
    - 202   → order cancelled by TWS
    - 154   → order to a halted symbol — mark halted in bot_state
    """
    sym = getattr(contract, "symbol", None) if contract else None
    if not sym and req_id is not None:
        ib = bot_state.ib
        if ib is not None:
            trade = next((t for t in ib.trades() if t.order.orderId == req_id), None)
            if trade:
                sym = getattr(trade.contract, "symbol", None)
    tag = f"[{sym}] " if sym else ""

    if error_code in _INFORMATIONAL_CODES:
        log.debug(f"IBKR info {error_code}: {tag}{error_string}")
        return

    if error_code == 1100:
        log.warning(f"IBKR error 1100: TWS lost IB servers — {error_string}")
        clog("ERROR", "IBKR: TWS lost connection to IB servers — orders paused")
        dash["ibkr_disconnected"] = True
        return

    if error_code == 1101:
        # Reconnected but market data subscriptions were lost — must re-subscribe.
        log.warning("IBKR error 1101: reconnected, data LOST — re-subscribing")
        clog("INFO", "IBKR: reconnected (data lost) — restoring subscriptions")
        dash["ibkr_disconnected"] = False
        try:
            _restore_subscriptions()
        except Exception as exc:
            log.error(f"_restore_subscriptions after 1101 failed: {exc}")
        return

    if error_code == 1102:
        # Reconnected and market data maintained — nothing to restore.
        log.info("IBKR error 1102: reconnected, data maintained — no re-subscribe needed")
        clog("INFO", "IBKR: reconnected (data maintained)")
        dash["ibkr_disconnected"] = False
        return

    if error_code == 507:
        log.error("IBKR error 507: socket EOF — triggering reconnect")
        clog("ERROR", "IBKR: socket EOF — reconnecting")
        _on_disconnected()
        return

    if error_code == 326:
        log.critical("IBKR error 326: duplicate clientId — another session is connected. Aborting bot.")
        clog(
            "ERROR",
            "IBKR FATAL: duplicate clientId 326 — another session already connected. Bot must be restarted with a unique clientId.",
        )
        import os

        os._exit(1)

    if error_code == 200:
        log.error(f"IBKR error 200: {tag}ambiguous/unknown contract — {error_string}")
        clog("ERROR", f"IBKR: ambiguous contract {tag.strip()}: {error_string}")
        return

    if error_code == 201:
        log.error(f"IBKR error 201 (reqId={req_id}): {tag}order rejected — {error_string}")
        clog("ERROR", f"IBKR: order rejected {tag.strip()}: {error_string}")
        return

    if error_code == 202:
        reason = bot_state._cancel_reasons.pop(req_id, None)
        sym_label = tag.strip() or "unknown"
        if reason:
            log.warning(f"IBKR error 202 (reqId={req_id}): {tag}order cancelled — {reason}")
            clog("INFO", f"IBKR: order cancelled [{sym_label}] — {reason}")
        else:
            log.warning(f"IBKR error 202 (reqId={req_id}): {tag}order cancelled — unknown reason (TWS-initiated)")
            clog("INFO", f"IBKR: order cancelled [{sym_label}] — unknown reason (TWS-initiated)")
        return

    if error_code == 154:
        if sym:
            bot_state._halted_symbols.add(sym)
            log.warning(f"IBKR error 154: {sym} is halted — added to halt set, future orders blocked")
            clog("ERROR", f"IBKR: {sym} halted — orders blocked until halt clears")
        return

    if error_code == 354:
        log.debug(f"IBKR error 354: {tag}market data not subscribed (delayed active) — {error_string}")
        return

    # Default: log as warning with full details
    log.warning(f"IBKR error {error_code} (reqId={req_id}): {tag}{error_string}")


# ── Alert & reconnect workers ─────────────────────────────────────────────────


def _send_reconnect_exhausted_alert(attempts: int) -> None:
    """
    Fire an external alert (Slack/Teams webhook) when all reconnect attempts
    are exhausted so the operator is notified even if they are not watching logs.
    """
    webhook = CONFIG.get("reconnect_alert_webhook", "")
    msg = (
        f"🔴 DECIFER IBKR RECONNECT FAILED — "
        f"all {attempts} attempts exhausted. Bot is disconnected and STOPPED. "
        f"Manual restart required."
    )
    dash["status"] = "disconnected — reconnect failed"
    clog("ERROR", msg)

    if not webhook:
        return
    try:
        payload = json.dumps({"text": msg}).encode()
        req = urllib.request.Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        log.info("Reconnect-exhausted alert sent to webhook.")
    except Exception as exc:
        log.error(f"Failed to send reconnect-exhausted alert: {exc}")


def _reconnect_worker() -> None:
    """
    Background thread: attempt to reconnect to IBKR using exponential backoff.

    Delays: 1 s, 2 s, 4 s, 8 s, 16 s, 32 s, 60 s, 60 s … (capped)
    Gives up after CONFIG['reconnect_max_attempts'] failures.
    On success, re-subscribes to all registered feeds.
    """
    max_attempts = CONFIG.get("reconnect_max_attempts", 10)
    max_wait = CONFIG.get("reconnect_max_wait_secs", 60)
    base_wait = CONFIG.get("reconnect_base_wait_secs", 1)
    host = CONFIG.get("ibkr_host", "127.0.0.1")
    port = CONFIG.get("ibkr_port", 7497)
    client_id = CONFIG.get("ibkr_client_id", 1)

    wait = base_wait
    try:
        for attempt in range(1, max_attempts + 1):
            log.warning(f"IBKR reconnect attempt {attempt}/{max_attempts} (waiting {wait}s before connect)…")
            dash["status"] = f"reconnecting ({attempt}/{max_attempts})"
            # Interruptible sleep — dashboard Reconnect button sets this event to skip backoff
            bot_state._manual_reconnect_evt.wait(timeout=wait)
            bot_state._manual_reconnect_evt.clear()

            try:
                ib = bot_state.ib  # read at call time for test-patch support
                ib.connect(host, port, clientId=client_id, readonly=False)
                log.info(f"✔ IBKR reconnected on attempt {attempt}.")
                dash["status"] = "running"
                dash["ibkr_disconnected"] = False
                _restore_subscriptions()
                # Re-fetch today's completed orders that arrived while we were disconnected.
                try:
                    ib.reqCompletedOrders(False)
                except Exception as _rco_exc:
                    log.warning(f"reqCompletedOrders after reconnect failed: {_rco_exc}")
                # Reconcile position state against IBKR after reconnect —
                # orphaned SLs from the disconnect are cleaned by Pass 2 on the next scan cycle.
                try:
                    ib.sleep(2)  # let IBKR push open-order events before reading state
                    from orders_portfolio import reconcile_with_ibkr as _reconcile
                    _reconcile(ib)
                except Exception as _rec_exc:
                    log.warning(f"reconcile_with_ibkr after reconnect failed: {_rec_exc}")
                break
            except Exception as exc:
                log.error(f"Reconnect attempt {attempt} failed: {exc}")
                wait = min(wait * 2, max_wait)
        else:
            # All attempts exhausted
            _send_reconnect_exhausted_alert(max_attempts)
    finally:
        with _reconnect_lock:
            bot_state._reconnecting = False


def _on_disconnected() -> None:
    """
    Callback registered with ib_async's disconnectedEvent.
    Spawns the reconnect worker thread (only one at a time).
    """
    with _reconnect_lock:
        if bot_state._reconnecting:
            log.debug("Disconnect event received but reconnect already in progress — ignoring.")
            return
        bot_state._reconnecting = True

    log.warning("⚠ IBKR connection lost — starting auto-reconnect…")
    dash["status"] = "disconnected"
    dash["ibkr_disconnected"] = True
    t = threading.Thread(target=_reconnect_worker, name="ibkr-reconnect", daemon=True)
    t.start()


def _heartbeat_worker() -> None:
    """
    Background thread: send a lightweight reqCurrentTime() to IBKR every
    CONFIG['heartbeat_interval_secs'] seconds to prevent idle-timeout disconnects.
    """
    import asyncio

    # ib_async's synchronous wrappers require an event loop on the calling thread.
    # Create one for this daemon thread so reqCurrentTime() doesn't raise
    # "There is no current event loop in thread 'ibkr-heartbeat'".
    asyncio.set_event_loop(asyncio.new_event_loop())

    interval = CONFIG.get("heartbeat_interval_secs", 1200)
    tick = 60
    elapsed = 0

    while True:
        time.sleep(tick)
        elapsed += tick
        if elapsed < interval:
            continue
        elapsed = 0
        ib = bot_state.ib
        if not ib.isConnected():
            log.debug("Heartbeat skipped — not connected.")
            continue
        try:
            ib.reqCurrentTime()
            log.debug("IBKR heartbeat sent (reqCurrentTime).")
        except Exception as exc:
            log.warning(f"IBKR heartbeat failed: {exc}")


# ── SL fill event handler ─────────────────────────────────────────────────────


def _on_ibkr_fill(trade, fill) -> None:
    """
    Called by ib.execDetailsEvent for every order fill.
    If the fill matches a known bot stop-loss order, flag the symbol so
    check_external_closes() processes it on the very next scan cycle instead
    of waiting up to 3-5 min for the position to disappear from the portfolio.
    No ib calls here — event callbacks must not block the ib event loop.
    """
    try:
        from orders_state import _trades_lock, open_trades

        oid = getattr(fill.execution, "orderId", None)
        if oid is None:
            return
        with _trades_lock:
            for sym, t in open_trades.items():
                sl_oid = t.get("sl_order_id")
                tp_oid = t.get("tp_order_id")
                if sl_oid and int(sl_oid) == int(oid):
                    bot_state._sl_fill_events.add(sym)
                    log.info(
                        f"[SL-FILL] Stop-loss fill detected for {sym} (orderId={oid}) — flagged for immediate processing"
                    )
                    break
                if tp_oid and int(tp_oid) == int(oid):
                    bot_state._sl_fill_events.add(sym)
                    log.info(
                        f"[TP-FILL] Take-profit fill detected for {sym} (orderId={oid}) — flagged for immediate processing"
                    )
                    break
    except Exception as exc:
        log.warning(f"_on_ibkr_fill error: {exc}")


# ── Account / position callbacks ─────────────────────────────────────────────

# IBKR account-value tags we care about — anything not in this set is silently ignored
# to keep bot_state.account_values lean.
_ACCOUNT_KEYS_OF_INTEREST = frozenset(
    {
        "NetLiquidation",
        "BuyingPower",
        "AvailableFunds",
        "ExcessLiquidity",
        "Cushion",
        "HighestSeverity",
        "DayTradesRemaining",
        "DayTradesRemainingT+1",
        "DayTradesRemainingT+2",
        "DayTradesRemainingT+3",
        "DayTradesRemainingT+4",
        "MaintMarginReq",
        "InitMarginReq",
        "GrossPositionValue",
    }
)


def _on_account_value(account_value) -> None:
    """
    Callback for ib.accountValueEvent (fired by reqAccountUpdates).
    Stores selected account metrics in bot_state.account_values and mirrors
    NetLiquidation into dash['portfolio_value'].
    """
    try:
        tag = account_value.tag
        val = account_value.value

        if tag == "accountReady":
            bot_state._account_ready = val.lower() == "true"
            if not bot_state._account_ready:
                log.warning("IBKR accountReady=false — account data suppressed during server reset")
            return

        if not bot_state._account_ready:
            return

        if tag not in _ACCOUNT_KEYS_OF_INTEREST:
            return

        try:
            numeric = float(val)
        except (ValueError, TypeError):
            numeric = val

        bot_state.account_values[tag] = numeric

        if tag == "NetLiquidation":
            dash["portfolio_value"] = numeric

    except Exception as exc:
        log.warning(f"_on_account_value error: {exc}")


def _on_position(position) -> None:
    """
    Callback for ib.positionEvent (fired by reqPositions).
    Builds bot_state.ibkr_positions keyed by symbol for ground-truth reconciliation.
    """
    try:
        sym = position.contract.symbol
        bot_state.ibkr_positions[sym] = position
    except Exception as exc:
        log.warning(f"_on_position error: {exc}")


# ── Commission report + order-bound callbacks ─────────────────────────────────


def _on_commission_report(trade, fill, report) -> None:
    """
    Fires after a fill is confirmed with its commission details.
    Updates the matching orders.json record with real commission and realized P&L.
    No ib calls here — must not block the event loop.
    """
    try:
        from learning import _save_orders
        from learning import load_orders as _load_orders

        order_id = getattr(fill.execution, "orderId", None)
        if order_id is None:
            return

        commission = getattr(report, "commission", None)
        realized_pnl = getattr(report, "realizedPNL", None)

        import math

        def _safe_float(v):
            if v is None:
                return None
            try:
                f = float(v)
                return None if math.isnan(f) else f
            except (TypeError, ValueError):
                return None

        commission = _safe_float(commission)
        realized_pnl = _safe_float(realized_pnl)

        if commission is None and realized_pnl is None:
            return

        orders = _load_orders()
        changed = False
        for o in orders:
            if o.get("order_id") == order_id:
                if commission is not None:
                    o["commission"] = commission
                if realized_pnl is not None:
                    o["realized_pnl"] = realized_pnl
                changed = True
                break

        if changed:
            _save_orders(orders)

    except Exception as exc:
        log.warning(f"_on_commission_report error: {exc}")


# ── Connect / subscribe ───────────────────────────────────────────────────────


def connect_ibkr() -> bool:
    from orders_portfolio import reconcile_with_ibkr

    ib = bot_state.ib
    try:
        if ib.isConnected():
            return True
        ib.connect(CONFIG["ibkr_host"], CONFIG["ibkr_port"], clientId=CONFIG["ibkr_client_id"], readonly=False)
        ib.reqMarketDataType(3)
        if _on_disconnected not in ib.disconnectedEvent:
            ib.disconnectedEvent += _on_disconnected
        if _on_ibkr_error not in ib.errorEvent:
            ib.errorEvent += _on_ibkr_error
        if _on_ibkr_fill not in ib.execDetailsEvent:
            ib.execDetailsEvent += _on_ibkr_fill
        if _on_order_status_event not in ib.orderStatusEvent:
            ib.orderStatusEvent += _on_order_status_event
        if _on_account_value not in ib.accountValueEvent:
            ib.accountValueEvent += _on_account_value
        if _on_position not in ib.positionEvent:
            ib.positionEvent += _on_position
        if _on_commission_report not in ib.commissionReportEvent:
            ib.commissionReportEvent += _on_commission_report
        ht = threading.Thread(target=_heartbeat_worker, name="ibkr-heartbeat", daemon=True)
        ht.start()
        account = CONFIG.get("active_account", "")
        _register_subscription("__pnl__", {"type": "pnl", "account": account})
        _register_subscription("__account__", {"type": "account", "account": account})
        _register_subscription("__positions__", {"type": "positions"})
        # Note: ib.reqAccountUpdates() intentionally omitted — in ib_async 0.9.x it is
        # a blocking call that waits for a "accountValues done" signal TWS never sends
        # (it streams continuously), causing the main thread to hang forever.
        # accountValueEvent is already registered above and receives values as they arrive.
        #
        # Note: reqAutoOpenOrders(True) intentionally omitted — requires clientId=0
        # and triggers ib_async.orderBoundEvent which does not exist in ib_async 0.9.x,
        # crashing the asyncio thread.
        clog(
            "INFO",
            f"IBKR connected — port {CONFIG['ibkr_port']} | Account: {CONFIG.get('active_account', '')} | Market data: DELAYED (free)",
        )
        reconcile_with_ibkr(ib)
        # Orphan cleanup now handled by Pass 2 in audit_bracket_orders (runs each scan cycle).
        dash["status"] = "running"
        return True
    except Exception as e:
        clog("ERROR", f"IBKR connection failed: {e}")
        return False


def subscribe_pnl():
    ib = bot_state.ib
    try:
        bot_state._pnl_subscription = ib.reqPnL(CONFIG["active_account"])
        clog("INFO", "P&L subscription active")
    except Exception as e:
        clog("ERROR", f"P&L subscription failed: {e}")


# ── Trade backfill ────────────────────────────────────────────────────────────


def backfill_trades_from_ibkr():
    """
    On startup, read IBKR execution history and match buy/sell pairs.
    Write any completed trades not already in trades.json.
    Partial fills for the same order are consolidated using weighted-average price.
    """
    from learning import TRADE_LOG_FILE, load_trades
    from orders_contracts import _is_option_contract

    ib = bot_state.ib
    try:
        existing = load_trades()
        existing_ids = set()
        existing_fuzzy = []
        for t in existing:
            eid = t.get("exec_id") or f"{t.get('symbol')}-{t.get('exit_time')}"
            existing_ids.add(eid)
            existing_ids.add(f"{t.get('symbol')}-{t.get('timestamp', '')}")
            if t.get("order_id"):
                existing_ids.add(f"order-{t['order_id']}")
            eq = t.get("qty") or t.get("shares") or t.get("total_shares") or 0
            ets = t.get("exit_time") or t.get("timestamp") or ""
            ep = float(t.get("exit_price") or t.get("avg_price") or 0)
            if ets:
                existing_fuzzy.append((t.get("symbol", ""), eq, ets, ep))

        fills = ib.fills()
        if not fills:
            return

        order_groups = defaultdict(
            lambda: {
                "sym": "",
                "side": "",
                "order_id": None,
                "exec_ids": [],
                "total_shares": 0.0,
                "value": 0.0,
                "total_pnl": 0.0,
                "latest_time": "",
                "earliest_time": "",
            }
        )

        opt_order_groups = defaultdict(
            lambda: {
                "sym": "",
                "underlying": "",
                "side": "",
                "order_id": None,
                "exec_ids": [],
                "total_contracts": 0.0,
                "value": 0.0,
                "total_pnl": 0.0,
                "latest_time": "",
                "earliest_time": "",
                "right": "",
                "strike": 0.0,
                "expiry": "",
            }
        )

        for fill in fills:
            try:
                is_opt = _is_option_contract(fill.contract)
                underlying = fill.contract.symbol
                side = fill.execution.side.upper()
                price = float(fill.execution.price)
                shares = float(fill.execution.shares)
                etime = fill.execution.time.strftime("%Y-%m-%d %H:%M:%S") if fill.execution.time else ""
                eid = _exec_id_prefix(fill.execution.execId)
                order_id = fill.execution.orderId

                pnl = 0.0
                cr = fill.commissionReport
                if cr is not None:
                    raw = getattr(cr, "realizedPNL", None)
                    if raw is not None:
                        try:
                            raw_f = float(raw)
                            if not math.isnan(raw_f) and raw_f != 0.0:
                                pnl = raw_f
                        except (ValueError, TypeError):
                            pass

                if is_opt:
                    right = getattr(fill.contract, "right", "") or ""
                    strike = getattr(fill.contract, "strike", 0) or 0
                    raw_exp = str(getattr(fill.contract, "lastTradeDateOrContractMonth", ""))
                    if len(raw_exp) == 8 and raw_exp.isdigit():
                        expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                    else:
                        expiry_str = raw_exp
                    opt_sym = f"{underlying}_{right}_{strike}_{expiry_str}"

                    key = (opt_sym, order_id, side)
                    g = opt_order_groups[key]
                    g["sym"] = opt_sym
                    g["underlying"] = underlying
                    g["side"] = side
                    g["order_id"] = order_id
                    g["exec_ids"].append(eid)
                    g["total_contracts"] += shares
                    g["value"] += price * shares
                    g["total_pnl"] += pnl
                    g["right"] = right
                    g["strike"] = strike
                    g["expiry"] = expiry_str
                    if not g["latest_time"] or etime > g["latest_time"]:
                        g["latest_time"] = etime
                    if not g["earliest_time"] or etime < g["earliest_time"]:
                        g["earliest_time"] = etime
                else:
                    sym = underlying
                    key = (sym, order_id, side)
                    g = order_groups[key]
                    g["sym"] = sym
                    g["side"] = side
                    g["order_id"] = order_id
                    g["exec_ids"].append(eid)
                    g["total_shares"] += shares
                    g["value"] += price * shares
                    g["total_pnl"] += pnl
                    if not g["latest_time"] or etime > g["latest_time"]:
                        g["latest_time"] = etime
                    if not g["earliest_time"] or etime < g["earliest_time"]:
                        g["earliest_time"] = etime
            except Exception as fill_exc:
                log.warning(
                    f"backfill: skipping fill (execId={getattr(getattr(fill, 'execution', None), 'execId', '?')}): {fill_exc}"
                )
                continue

        buy_orders = defaultdict(list)
        sell_orders = defaultdict(list)

        for (sym, order_id, side), g in order_groups.items():
            total_shares = g["total_shares"]
            if total_shares == 0:
                continue
            avg_price = g["value"] / total_shares
            order_rec = {
                "order_id": order_id,
                "exec_ids": g["exec_ids"],
                "avg_price": round(avg_price, 4),
                "total_shares": total_shares,
                "total_pnl": g["total_pnl"],
                "time": g["latest_time"],
                "earliest_time": g["earliest_time"],
            }
            if side in ("BOT", "BUY"):
                buy_orders[sym].append(order_rec)
            elif side in ("SLD", "SELL"):
                sell_orders[sym].append(order_rec)

        new_trades = []
        for sym, s_orders in sell_orders.items():
            for sell in s_orders:
                order_key = f"order-{sell['order_id']}"
                already = (
                    order_key in existing_ids
                    or any(eid in existing_ids for eid in sell["exec_ids"])
                    or f"{sym}-{sell['time'].replace(' ', 'T')}" in existing_ids
                )
                if already:
                    continue

                sell_qty = int(sell["total_shares"])
                sell_ts = sell["time"]
                sell_price = float(sell.get("avg_price") or 0)
                for ex_sym, ex_qty, ex_ts, ex_price in existing_fuzzy:
                    if ex_sym == sym and ex_qty == sell_qty:
                        price_match = (
                            ex_price == 0
                            or sell_price == 0
                            or abs(ex_price - sell_price) / max(ex_price, sell_price) < 0.01
                        )
                        if not price_match:
                            continue
                        try:
                            t1 = datetime.strptime(ex_ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                            t2 = datetime.strptime(sell_ts[:19], "%Y-%m-%d %H:%M:%S")
                            if abs((t2 - t1).total_seconds()) < 300:
                                already = True
                                break
                        except Exception:
                            pass
                if already:
                    continue

                matching_buy = None
                for buy in sorted(buy_orders.get(sym, []), key=lambda b: b["time"], reverse=True):
                    if buy["time"] <= sell["time"] and f"order-{buy['order_id']}" not in existing_ids:
                        matching_buy = buy
                        break

                if not matching_buy:
                    continue

                entry_price = matching_buy["avg_price"]
                entry_time = matching_buy["time"]

                pnl = sell["total_pnl"]
                _price_pnl = round((sell["avg_price"] - entry_price) * sell["total_shares"], 2)
                # IBKR realizedPNL uses its own FIFO cost basis, which can differ
                # from entry_price when a position was built up at multiple prices.
                # If the sign contradicts the actual price movement, the IBKR number
                # is unreliable — use price-based P&L instead.
                if pnl != 0.0 and _price_pnl != 0.0 and (pnl < 0) != (_price_pnl < 0):
                    log.warning(
                        "backfill: IBKR realizedPNL sign contradicts price move for %s "
                        "(ibkr=%.2f, price_based=%.2f) — using price-based P&L",
                        sym, pnl, _price_pnl,
                    )
                    pnl = _price_pnl
                if pnl == 0.0:
                    pnl = _price_pnl
                if pnl == 0.0:
                    continue

                try:
                    entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt = datetime.strptime(sell["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                try:
                    import training_store as _ts_bf
                    _recent = _ts_bf.load(symbol=sym, limit=1)
                    _bf_meta = _recent[0] if _recent else {}
                except Exception:
                    _bf_meta = {}
                trade = {
                    "symbol": sym,
                    "action": "BUY",
                    "direction": "LONG",
                    "entry_price": entry_price,
                    "exit_price": sell["avg_price"],
                    "qty": int(sell["total_shares"]),
                    "shares": int(sell["total_shares"]),
                    "pnl": round(pnl, 2),
                    "entry_time": entry_time,
                    "exit_time": sell["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason": "stop_loss" if pnl < 0 else "take_profit",
                    "regime": _bf_meta.get("entry_regime", "UNKNOWN"),
                    "vix": 0.0,
                    "score": _bf_meta.get("entry_score", 0),
                    "trade_type": _bf_meta.get("trade_type") or None,
                    "conviction": _bf_meta.get("conviction", 0.0),
                    "signal_scores": _bf_meta.get("signal_scores", {}),
                    "reasoning": _bf_meta.get("reasoning") or "Backfilled from IBKR execution history on startup.",
                    "order_id": sell["order_id"],
                    "exec_id": sell["exec_ids"][0],
                    "timestamp": sell["time"].replace(" ", "T"),
                    "source": "ibkr_backfill",
                }
                new_trades.append(trade)
                existing_ids.add(order_key)
                for eid in sell["exec_ids"]:
                    existing_ids.add(eid)
                existing_ids.add(f"order-{matching_buy['order_id']}")
                for eid in matching_buy["exec_ids"]:
                    existing_ids.add(eid)

        # ── Process SHORT positions ───────────────────────────────────────────
        for sym, b_orders in buy_orders.items():
            for buy_cover in sorted(b_orders, key=lambda b: b["time"]):
                order_key = f"order-{buy_cover['order_id']}"
                already = order_key in existing_ids or any(eid in existing_ids for eid in buy_cover["exec_ids"])
                if already:
                    continue

                cover_qty = int(buy_cover["total_shares"])
                cover_ts = buy_cover["time"]
                cover_price = float(buy_cover.get("avg_price") or 0)
                for ex_sym, ex_qty, ex_ts, ex_price in existing_fuzzy:
                    if ex_sym == sym and ex_qty == cover_qty:
                        price_match = (
                            ex_price == 0
                            or cover_price == 0
                            or abs(ex_price - cover_price) / max(ex_price, cover_price) < 0.01
                        )
                        if not price_match:
                            continue
                        try:
                            t1 = datetime.strptime(ex_ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                            t2 = datetime.strptime(cover_ts[:19], "%Y-%m-%d %H:%M:%S")
                            if abs((t2 - t1).total_seconds()) < 300:
                                already = True
                                break
                        except Exception:
                            pass
                if already:
                    continue

                matching_short_entry = None
                for sell_entry in sorted(sell_orders.get(sym, []), key=lambda s: s["time"], reverse=True):
                    sek = f"order-{sell_entry['order_id']}"
                    if sell_entry["time"] <= buy_cover["time"] and sek not in existing_ids:
                        matching_short_entry = sell_entry
                        break

                if not matching_short_entry:
                    continue

                entry_price = matching_short_entry["avg_price"]
                entry_time = matching_short_entry["time"]

                pnl = buy_cover["total_pnl"]
                _price_pnl = round((entry_price - buy_cover["avg_price"]) * buy_cover["total_shares"], 2)
                if pnl != 0.0 and _price_pnl != 0.0 and (pnl < 0) != (_price_pnl < 0):
                    log.warning(
                        "backfill: IBKR realizedPNL sign contradicts price move for SHORT %s "
                        "(ibkr=%.2f, price_based=%.2f) — using price-based P&L",
                        sym, pnl, _price_pnl,
                    )
                    pnl = _price_pnl
                if pnl == 0.0:
                    pnl = _price_pnl
                if pnl == 0.0:
                    continue

                try:
                    entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt = datetime.strptime(buy_cover["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                try:
                    import training_store as _ts_bf_s
                    _recent_s = _ts_bf_s.load(symbol=sym, limit=1)
                    _bf_meta_s = _recent_s[0] if _recent_s else {}
                except Exception:
                    _bf_meta_s = {}
                trade = {
                    "symbol": sym,
                    "action": "SELL",
                    "direction": "SHORT",
                    "entry_price": entry_price,
                    "exit_price": buy_cover["avg_price"],
                    "qty": int(buy_cover["total_shares"]),
                    "shares": int(buy_cover["total_shares"]),
                    "pnl": round(pnl, 2),
                    "entry_time": entry_time,
                    "exit_time": buy_cover["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason": "stop_loss" if pnl < 0 else "take_profit",
                    "regime": _bf_meta_s.get("entry_regime", "UNKNOWN"),
                    "vix": 0.0,
                    "score": _bf_meta_s.get("entry_score", 0),
                    "trade_type": _bf_meta_s.get("trade_type") or None,
                    "conviction": _bf_meta_s.get("conviction", 0.0),
                    "signal_scores": _bf_meta_s.get("signal_scores", {}),
                    "reasoning": _bf_meta_s.get("reasoning") or "Backfilled from IBKR execution history on startup.",
                    "order_id": buy_cover["order_id"],
                    "exec_id": buy_cover["exec_ids"][0],
                    "timestamp": buy_cover["time"].replace(" ", "T"),
                    "source": "ibkr_backfill",
                }
                new_trades.append(trade)
                existing_ids.add(order_key)
                for eid in buy_cover["exec_ids"]:
                    existing_ids.add(eid)
                existing_ids.add(f"order-{matching_short_entry['order_id']}")

        # ── Process OPTIONS trades ────────────────────────────────────────────
        opt_buy_orders = defaultdict(list)
        opt_sell_orders = defaultdict(list)
        for (opt_sym, order_id, side), g in opt_order_groups.items():
            total = g["total_contracts"]
            if total == 0:
                continue
            avg_premium = g["value"] / total
            order_rec = {
                "order_id": order_id,
                "exec_ids": g["exec_ids"],
                "avg_price": round(avg_premium, 4),
                "total_contracts": total,
                "total_pnl": g["total_pnl"],
                "time": g["latest_time"],
                "earliest_time": g["earliest_time"],
                "right": g["right"],
                "strike": g["strike"],
                "expiry": g["expiry"],
                "underlying": g["underlying"],
            }
            if side in ("BOT", "BUY"):
                opt_buy_orders[opt_sym].append(order_rec)
            elif side in ("SLD", "SELL"):
                opt_sell_orders[opt_sym].append(order_rec)

        for opt_sym, s_orders in opt_sell_orders.items():
            for sell in s_orders:
                order_key = f"order-{sell['order_id']}"
                already = (
                    order_key in existing_ids
                    or any(eid in existing_ids for eid in sell["exec_ids"])
                    or f"{opt_sym}-{sell['time'].replace(' ', 'T')}" in existing_ids
                )
                if already:
                    continue

                sell_qty = int(sell["total_contracts"])
                sell_ts = sell["time"]
                sell_price = float(sell.get("avg_price") or 0)
                for ex_sym, ex_qty, ex_ts, ex_price in existing_fuzzy:
                    if (ex_sym == opt_sym or ex_sym == sell["underlying"]) and ex_qty == sell_qty:
                        price_match = (
                            ex_price == 0
                            or sell_price == 0
                            or abs(ex_price - sell_price) / max(ex_price, sell_price) < 0.01
                        )
                        if not price_match:
                            continue
                        try:
                            t1 = datetime.strptime(ex_ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                            t2 = datetime.strptime(sell_ts[:19], "%Y-%m-%d %H:%M:%S")
                            if abs((t2 - t1).total_seconds()) < 300:
                                already = True
                                break
                        except Exception:
                            pass
                if already:
                    continue

                matching_buy = None
                for buy in sorted(opt_buy_orders.get(opt_sym, []), key=lambda b: b["time"], reverse=True):
                    if buy["time"] <= sell["time"]:
                        matching_buy = buy
                        break

                if not matching_buy:
                    continue

                entry_premium = matching_buy["avg_price"]
                entry_time = matching_buy["time"]

                pnl = sell["total_pnl"]
                _price_pnl = round((sell["avg_price"] - entry_premium) * sell["total_contracts"] * 100, 2)
                if pnl != 0.0 and _price_pnl != 0.0 and (pnl < 0) != (_price_pnl < 0):
                    log.warning(
                        "backfill: IBKR realizedPNL sign contradicts price move for option %s "
                        "(ibkr=%.2f, price_based=%.2f) — using price-based P&L",
                        sym, pnl, _price_pnl,
                    )
                    pnl = _price_pnl
                if pnl == 0.0:
                    pnl = _price_pnl
                if pnl == 0.0:
                    continue

                try:
                    entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt = datetime.strptime(sell["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                _opt_key_bf = f"{sell['underlying']}_{sell['right']}_{sell['strike']}_{sell['expiry']}"
                try:
                    import training_store as _ts_bf_opt
                    _recent_opt = _ts_bf_opt.load(symbol=sell["underlying"], limit=1)
                    _bf_meta_opt = _recent_opt[0] if _recent_opt else {}
                except Exception:
                    _bf_meta_opt = {}
                trade = {
                    "symbol": sell["underlying"],
                    "action": "BUY",
                    "direction": "LONG",
                    "instrument": "option",
                    "right": sell["right"],
                    "strike": sell["strike"],
                    "expiry": sell["expiry"],
                    "entry_price": entry_premium,
                    "exit_price": sell["avg_price"],
                    "qty": int(sell["total_contracts"]),
                    "shares": int(sell["total_contracts"]),
                    "pnl": round(pnl, 2),
                    "entry_time": entry_time,
                    "exit_time": sell["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason": "stop_loss" if pnl < 0 else "take_profit",
                    "regime": _bf_meta_opt.get("entry_regime", "UNKNOWN"),
                    "vix": 0.0,
                    "score": _bf_meta_opt.get("entry_score", 0),
                    "trade_type": _bf_meta_opt.get("trade_type") or None,
                    "conviction": _bf_meta_opt.get("conviction", 0.0),
                    "signal_scores": _bf_meta_opt.get("signal_scores", {}),
                    "reasoning": _bf_meta_opt.get("reasoning") or "Backfilled from IBKR execution history on startup.",
                    "order_id": sell["order_id"],
                    "exec_id": sell["exec_ids"][0],
                    "timestamp": sell["time"].replace(" ", "T"),
                    "source": "ibkr_backfill",
                }
                new_trades.append(trade)
                existing_ids.add(order_key)
                for eid in sell["exec_ids"]:
                    existing_ids.add(eid)

        if new_trades:
            all_trades = existing + new_trades
        else:
            all_trades = existing

        # ── Deduplicate ───────────────────────────────────────────────────────
        before_count = len(all_trades)
        deduped = []
        seen = []

        all_trades.sort(key=lambda t: t.get("timestamp") or t.get("exit_time") or "")

        for t in all_trades:
            sym = t.get("symbol", "")
            qty = t.get("qty") or t.get("shares") or t.get("total_shares") or 0
            ts = t.get("timestamp") or t.get("exit_time") or ""
            ep = t.get("entry_price") or 0

            is_dupe = False
            for _i, (s_sym, s_qty, s_ts, s_ep, s_idx) in enumerate(seen):
                if s_sym != sym or not ts or not s_ts:
                    continue
                qty_match = (s_qty == qty) if qty and s_qty else True
                price_match = (abs(ep - s_ep) / max(s_ep, 0.01) < 0.02) if ep and s_ep else False
                if not qty_match and not price_match:
                    continue
                try:
                    t1 = datetime.fromisoformat(s_ts.replace(" ", "T"))
                    t2 = datetime.fromisoformat(ts.replace(" ", "T"))
                    if abs((t2 - t1).total_seconds()) < 300:
                        existing_rec = deduped[s_idx]
                        existing_pnl = abs(existing_rec.get("pnl") or 0)
                        new_pnl = abs(t.get("pnl") or 0)
                        existing_oid = existing_rec.get("order_id")
                        new_oid = t.get("order_id")
                        should_replace = (new_oid and not existing_oid) or (
                            new_pnl > existing_pnl and not (existing_oid and not new_oid)
                        )
                        if should_replace:
                            # Preserve metadata from the existing record if the replacement lacks it.
                            merged = dict(existing_rec)
                            merged.update(t)
                            for _mkey in ("trade_type", "conviction", "reasoning", "signal_scores", "entry_regime"):
                                if not t.get(_mkey) and existing_rec.get(_mkey):
                                    merged[_mkey] = existing_rec[_mkey]
                            deduped[s_idx] = merged
                        is_dupe = True
                        break
                except Exception:
                    pass

            if not is_dupe:
                seen.append((sym, qty, ts, ep, len(deduped)))
                deduped.append(t)

        removed = before_count - len(deduped)

        if new_trades or removed > 0:
            with open(TRADE_LOG_FILE, "w") as f:
                json.dump(deduped, f, indent=2)
            if new_trades:
                clog("INFO", f"📋 Backfilled {len(new_trades)} trade(s) from IBKR execution history")
            if removed > 0:
                clog("INFO", f"📋 Deduplication: removed {removed} duplicate trade(s)")
        else:
            clog("INFO", "📋 Trade history up to date — no new backfill needed")

    except Exception as e:
        clog("ERROR", f"Trade backfill error: {e}")


# ── Order sync ────────────────────────────────────────────────────────────────


def sync_orders_from_ibkr():
    """
    Sync order statuses from IBKR into orders.json.
    Three-pass approach: update statuses, log new fills, mark stale as cancelled.
    """
    from learning import load_orders as _load_orders
    from learning import log_order as _log_order

    ib = bot_state.ib
    try:
        orders = _load_orders()

        for t in ib.trades():
            contract = t.contract
            order = t.order
            ibkr_status = (t.orderStatus.status or "").upper()
            sec_type = getattr(contract, "secType", "STK")
            instrument = "option" if sec_type == "OPT" else "stock"

            if ibkr_status in ("FILLED",):
                mapped_status = "FILLED"
            elif ibkr_status in ("CANCELLED", "APICANCELED", "APICANCELLED") or ibkr_status in ("INACTIVE",):
                mapped_status = "CANCELLED"
            elif ibkr_status in ("PENDINGCANCEL",):
                # Order can still fill in this state — do NOT mark as CANCELLED.
                mapped_status = "PENDING_CANCEL"
            elif ibkr_status in ("SUBMITTED", "PRESUBMITTED", "PENDINGSUBMIT"):
                mapped_status = "SUBMITTED"
            else:
                mapped_status = ibkr_status

            _fp = t.orderStatus.avgFillPrice
            fill_price = float(_fp) if (_fp is not None and _fp > 0) else 0
            filled_qty = int(t.orderStatus.filled) if t.orderStatus.filled else 0

            if not order.orderId:  # IBKR hasn't assigned an ID yet — skip to avoid order_id=0 accumulation
                continue

            _log_order(
                {
                    "order_id": order.orderId,
                    "perm_id": getattr(order, "permId", None) or None,
                    "symbol": contract.symbol,
                    "side": order.action,
                    "order_type": order.orderType,
                    "qty": int(order.totalQuantity),
                    "price": float(order.lmtPrice)
                    if order.lmtPrice and abs(float(order.lmtPrice)) < 1e10
                    else (float(order.auxPrice) if order.auxPrice and abs(float(order.auxPrice)) < 1e10 else 0),
                    "status": mapped_status,
                    "instrument": instrument,
                    "filled_qty": filled_qty,
                    "fill_price": fill_price if fill_price > 0 else None,
                    "source": "ibkr_sync",
                }
            )

            if mapped_status == "FILLED" and fill_price > 0 and order.action == "BUY" and instrument == "stock":
                from orders_state import _safe_update_trade

                sym = contract.symbol
                total_qty = int(order.totalQuantity)
                updates = {"entry": fill_price, "status": "FILLED"}
                if 0 < filled_qty < total_qty:
                    updates["qty"] = filled_qty
                    clog(
                        "WARNING",
                        f"Partial fill: {sym} ordered {total_qty} filled {filled_qty} @ ${fill_price:.2f} — tracker qty adjusted",
                    )
                _safe_update_trade(sym, updates)

        ibkr_known_ids = set()
        for t in ib.trades():
            ibkr_known_ids.add(t.order.orderId)
        for fill in ib.fills():
            ibkr_known_ids.add(fill.execution.orderId)
        ibkr_open_ids = set()
        for t in ib.openTrades():
            ibkr_open_ids.add(t.order.orderId)
            ibkr_known_ids.add(t.order.orderId)

        orders = _load_orders()
        changed = False
        for o in orders:
            oid = o.get("order_id")
            status = (o.get("status") or "").upper()
            # PENDING_CANCEL: IBKR cancel request acknowledged but fill still possible.
            # Do not mark as CANCELLED until IBKR confirms with "Cancelled" status.
            if not oid or status not in ("SUBMITTED", "PRESUBMITTED", "PENDING"):
                continue
            if oid in ibkr_open_ids:
                continue
            if oid in ibkr_known_ids:
                continue
            o["status"] = "CANCELLED"
            o["reason"] = "ibkr_sync:not_found_in_ibkr"
            changed = True

        if changed:
            from learning import _save_orders

            _save_orders(orders)

        clog("INFO", f"Order sync complete — {len(orders)} orders tracked")
    except Exception as e:
        clog("ERROR", f"Order sync error: {e}")



def _on_order_status_event(trade):
    """
    Real-time callback: fires whenever an order's status changes in IBKR.
    Updates orders.json immediately so the dashboard reflects fills/cancels live.
    """
    from learning import log_order as _log_order

    try:
        contract = trade.contract
        order = trade.order
        ibkr_status = (trade.orderStatus.status or "").upper()
        sec_type = getattr(contract, "secType", "STK")
        instrument = "option" if sec_type == "OPT" else "stock"

        if ibkr_status in ("FILLED",):
            mapped_status = "FILLED"
        elif ibkr_status in ("CANCELLED", "APICANCELED", "APICANCELLED", "INACTIVE"):
            mapped_status = "CANCELLED"
        elif ibkr_status in ("PENDINGCANCEL",):
            # Order can still fill in this state — do NOT mark as CANCELLED.
            mapped_status = "PENDING_CANCEL"
        elif ibkr_status in ("SUBMITTED", "PRESUBMITTED", "PENDINGSUBMIT"):
            mapped_status = "SUBMITTED"
        else:
            mapped_status = ibkr_status

        _fp = trade.orderStatus.avgFillPrice
        fill_price = float(_fp) if (_fp is not None and _fp > 0) else 0
        filled_qty = int(trade.orderStatus.filled) if trade.orderStatus.filled else 0

        # Capture IBKR cancel/reject reason from the trade log (most recent non-empty entry)
        _cancel_reason = None
        if mapped_status == "CANCELLED":
            _why = getattr(trade.orderStatus, "whyHeld", "") or ""
            if _why:
                _cancel_reason = f"ibkr_event:whyHeld={_why}"
            else:
                for _entry in reversed(getattr(trade, "log", []) or []):
                    _msg = getattr(_entry, "message", "") or ""
                    if _msg:
                        _code = getattr(_entry, "errorCode", 0)
                        _cancel_reason = f"ibkr_event:{_code}:{_msg}"[:200]
                        break
                if not _cancel_reason:
                    _cancel_reason = f"ibkr_event:{ibkr_status}"

        _log_order(
            {
                "order_id": order.orderId,
                "perm_id": getattr(order, "permId", None) or None,
                "symbol": contract.symbol,
                "side": order.action,
                "order_type": order.orderType,
                "qty": int(order.totalQuantity),
                "price": float(order.lmtPrice)
                if order.lmtPrice and abs(float(order.lmtPrice)) < 1e10
                else (float(order.auxPrice) if order.auxPrice and abs(float(order.auxPrice)) < 1e10 else 0),
                "status": mapped_status,
                "instrument": instrument,
                "filled_qty": filled_qty,
                "fill_price": fill_price if fill_price > 0 else None,
                "source": "ibkr_event",
                **({"reason": _cancel_reason} if _cancel_reason else {}),
            }
        )

        if mapped_status == "FILLED" and fill_price > 0 and instrument == "stock":
            from orders_state import _safe_update_trade, _trades_lock, active_trades

            sym = contract.symbol

            if order.action == "BUY":
                # Snapshot pre-update status for voice idempotency guard (same pattern as SHORT path)
                with _trades_lock:
                    _t_pre = dict(active_trades.get(sym, {}))

                # Long entry fill — update tracker and announce
                total_qty = int(order.totalQuantity)
                updates = {"entry": fill_price, "status": "FILLED"}
                if 0 < filled_qty < total_qty:
                    updates["qty"] = filled_qty
                    clog(
                        "WARNING",
                        f"Partial fill event: {sym} ordered {total_qty} filled {filled_qty} @ ${fill_price:.2f} — tracker qty adjusted",
                    )
                _safe_update_trade(sym, updates)

                # Snapshot trade record after update
                with _trades_lock:
                    _t = dict(active_trades.get(sym, {}))

                # Post-fill SL for extended-hours entries (bracket not placed at entry time)
                if _t.get("extended_hours_entry") and _t.get("sl_order_id") is None:
                    _sl_val = _t.get("sl", 0)
                    _fill_qty = filled_qty or int(order.totalQuantity)
                    if _sl_val > 0:
                        def _place_long_sl(_sym=sym, _sl=_sl_val, _qty=_fill_qty):
                            try:
                                import bot_state as _bs
                                from ib_async import StopLimitOrder as _SLO
                                from orders_contracts import get_contract as _gc
                                from orders_state import active_trades as _at
                                _ib = _bs.ib
                                _c = _gc(_sym)
                                _ib.qualifyContracts(_c)
                                _sl_lmt = round(_sl * 0.99, 2)
                                _tid = _at.get(_sym, {}).get("trade_id", "")
                                _sl_ord = _SLO("SELL", _qty, _sl, _sl_lmt,
                                               account=CONFIG["active_account"], tif="GTC", outsideRth=True)
                                _sl_ord.orderRef = f"SL:{_tid}"[:20]
                                _sl_trade = _ib.placeOrder(_c, _sl_ord)
                                _ib.sleep(0.3)
                                _safe_update_trade(_sym, {"sl_order_id": _sl_trade.order.orderId})
                                clog("TRADE", f"[EXT-HRS] SL placed post-fill for {_sym} @ ${_sl:.2f} (#{_sl_trade.order.orderId})")
                            except Exception as _e:
                                clog("ERROR", f"Post-fill SL placement failed for {_sym}: {_e}")
                        threading.Thread(target=_place_long_sl, daemon=True, name=f"ext_sl_{sym}").start()

                # Voice: fires only on first fill (pre-update status is PENDING/SUBMITTED).
                # Guard matches the SHORT path — prevents double-speak if IBKR re-sends FILLED.
                try:
                    from bot_voice import speak_natural as _speak_natural
                    if _t_pre.get("direction") == "LONG" and _t_pre.get("status") in ("PENDING", "SUBMITTED"):
                        _news = (dash.get("news_data") or {}).get(sym, {})
                        _speak_natural(
                            "entry",
                            fallback=f"Long on {sym} filled at {fill_price:.2f}.",
                            symbol=sym,
                            direction="long",
                            score=_t.get("score", 0),
                            reason=_t.get("reasoning", "strong signal")[:200],
                            news=_news.get("claude_catalyst") or "none",
                        )
                except Exception as _ve:
                    clog("WARNING", f"Voice entry alert failed for {sym}: {_ve}")

            elif order.action == "SELL":
                # Snapshot pre-update status for voice idempotency guard.
                # Update status to SUBMITTED immediately so IBKR re-sends of the
                # same Filled event (which arrive 80-150ms apart) don't double-speak.
                with _trades_lock:
                    _t_pre = dict(active_trades.get(sym, {}))
                if _t_pre.get("direction") == "SHORT" and _t_pre.get("status") == "PENDING":
                    _safe_update_trade(sym, {"status": "SUBMITTED"})
                with _trades_lock:
                    _t = dict(active_trades.get(sym, {}))

                # Post-fill SL for extended-hours short entries
                if _t.get("extended_hours_entry") and _t.get("sl_order_id") is None and _t.get("direction") == "SHORT":
                    _sl_val = _t.get("sl", 0)
                    _fill_qty = filled_qty or int(order.totalQuantity)
                    if _sl_val > 0:
                        def _place_short_sl(_sym=sym, _sl=_sl_val, _qty=_fill_qty):
                            try:
                                import bot_state as _bs
                                from ib_async import StopLimitOrder as _SLO
                                from orders_contracts import get_contract as _gc
                                from orders_state import active_trades as _at
                                _ib = _bs.ib
                                _c = _gc(_sym)
                                _ib.qualifyContracts(_c)
                                _sl_lmt = round(_sl * 1.01, 2)
                                _tid = _at.get(_sym, {}).get("trade_id", "")
                                _sl_ord = _SLO("BUY", _qty, _sl, _sl_lmt,
                                               account=CONFIG["active_account"], tif="GTC", outsideRth=True)
                                _sl_ord.orderRef = f"SL:{_tid}"[:20]
                                _sl_trade = _ib.placeOrder(_c, _sl_ord)
                                _ib.sleep(0.3)
                                _safe_update_trade(_sym, {"sl_order_id": _sl_trade.order.orderId})
                                clog("TRADE", f"[EXT-HRS] SL placed post-fill for short {_sym} @ ${_sl:.2f} (#{_sl_trade.order.orderId})")
                            except Exception as _e:
                                clog("ERROR", f"Post-fill short SL placement failed for {_sym}: {_e}")
                        threading.Thread(target=_place_short_sl, daemon=True, name=f"ext_sl_{sym}").start()

                # Voice for short entry fill
                try:
                    from bot_voice import speak_natural as _speak_natural
                    if _t_pre.get("direction") == "SHORT" and _t_pre.get("status") == "PENDING":
                        _news = (dash.get("news_data") or {}).get(sym, {})
                        _speak_natural(
                            "entry",
                            fallback=f"Short on {sym} filled at {fill_price:.2f}.",
                            symbol=sym,
                            direction="short",
                            score=_t.get("score", 0),
                            reason=_t.get("reasoning", "strong signal")[:200],
                            news=_news.get("claude_catalyst") or "none",
                        )
                except Exception as _ve:
                    clog("WARNING", f"Voice short entry alert failed for {sym}: {_ve}")

                # Long exit fill — if position is EXITING, write POSITION_CLOSED.
                # Guard: execute_sell() may have already written it inline (in which case
                # the key is gone from active_trades) or the deferred handler will run next
                # reconcile cycle. Check status=EXITING to avoid double-writes.
                if _t_pre.get("status") == "EXITING" and fill_price > 0:
                    try:
                        from orders_portfolio import _close_position_record
                        _ep = float(_t_pre.get("entry", 0))
                        _q = int(_t_pre.get("qty", 1))
                        _short = _t_pre.get("direction") == "SHORT"
                        _pnl = round((_ep - fill_price if _short else fill_price - _ep) * _q, 2)
                        _reason = _t_pre.get("pending_exit_reason", "sell_filled")
                        _close_position_record(sym, exit_price=fill_price, exit_reason=_reason, pnl=_pnl)
                        clog("TRADE", f"✅ POSITION_CLOSED via callback: {sym} exit={fill_price:.4f} pnl={_pnl:.2f}")
                    except Exception as _ce:
                        clog("WARNING", f"Callback POSITION_CLOSED failed for {sym}: {_ce}")

    except Exception as e:
        clog("ERROR", f"Order status event error: {e}")
