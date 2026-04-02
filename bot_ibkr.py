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
from datetime import datetime, timezone

from config import CONFIG
import bot_state
from bot_state import dash, _subscription_registry, _reconnect_lock, clog

log = logging.getLogger("decifer.bot")

# Ensure reconnect/heartbeat keys exist — they may be absent in minimal test configs
# that are registered before config.py fully loads (e.g. test_bot.py's fake config).
CONFIG.setdefault("heartbeat_interval_secs",  1200)
CONFIG.setdefault("reconnect_max_attempts",      10)
CONFIG.setdefault("reconnect_max_wait_secs",     60)
CONFIG.setdefault("reconnect_base_wait_secs",     1)
CONFIG.setdefault("reconnect_alert_webhook",      "")


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
                ib.reqPnL(account)
                log.info(f"  ✔ Re-subscribed PnL for account {account}")
            elif sub_type == "ticker":
                from ib_async import Stock
                contract = Stock(key, "SMART", "USD")
                ib.reqMktData(contract, "", False, False)
                log.info(f"  ✔ Re-subscribed market data for {key}")
            else:
                log.warning(f"  ⚠ Unknown subscription type '{sub_type}' for key '{key}' — skipped")
        except Exception as exc:
            log.error(f"  ✗ Failed to restore subscription '{key}': {exc}")


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
    max_wait     = CONFIG.get("reconnect_max_wait_secs", 60)
    base_wait    = CONFIG.get("reconnect_base_wait_secs", 1)
    host         = CONFIG.get("ibkr_host", "127.0.0.1")
    port         = CONFIG.get("ibkr_port", 7497)
    client_id    = CONFIG.get("ibkr_client_id", 1)

    wait = base_wait
    for attempt in range(1, max_attempts + 1):
        log.warning(
            f"IBKR reconnect attempt {attempt}/{max_attempts} "
            f"(waiting {wait}s before connect)…"
        )
        dash["status"] = f"reconnecting ({attempt}/{max_attempts})"
        time.sleep(wait)

        try:
            ib = bot_state.ib  # read at call time for test-patch support
            ib.connect(host, port, clientId=client_id, readonly=False)
            log.info(f"✔ IBKR reconnected on attempt {attempt}.")
            dash["status"] = "connected"
            dash["ibkr_disconnected"] = False
            # Look up via bot module so patch.object(bot, "_restore_subscriptions") works
            _bot = sys.modules.get("bot")
            restore_fn = getattr(_bot, "_restore_subscriptions", _restore_subscriptions) if _bot else _restore_subscriptions
            restore_fn()
            break
        except Exception as exc:
            log.error(f"Reconnect attempt {attempt} failed: {exc}")
            wait = min(wait * 2, max_wait)
    else:
        # All attempts exhausted — look up via bot module for patch.object compatibility
        _bot = sys.modules.get("bot")
        alert_fn = getattr(_bot, "_send_reconnect_exhausted_alert", _send_reconnect_exhausted_alert) if _bot else _send_reconnect_exhausted_alert
        alert_fn(max_attempts)

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
    tick     = 60
    elapsed  = 0

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


# ── Connect / subscribe ───────────────────────────────────────────────────────

def connect_ibkr() -> bool:
    from orders import reconcile_with_ibkr
    ib = bot_state.ib
    try:
        if ib.isConnected():
            return True
        ib.connect(CONFIG["ibkr_host"], CONFIG["ibkr_port"],
                   clientId=CONFIG["ibkr_client_id"], readonly=False)
        ib.reqMarketDataType(3)
        if _on_disconnected not in ib.disconnectedEvent:
            ib.disconnectedEvent += _on_disconnected
        ht = threading.Thread(target=_heartbeat_worker, name="ibkr-heartbeat", daemon=True)
        ht.start()
        _register_subscription("__pnl__", {"type": "pnl", "account": CONFIG.get("active_account", "")})
        clog("INFO", f"IBKR connected — port {CONFIG['ibkr_port']} | Account: {CONFIG.get('active_account', '')} | Market data: DELAYED (free)")
        reconcile_with_ibkr(ib)
        dash["status"] = "running"
        return True
    except Exception as e:
        clog("ERROR", f"IBKR connection failed: {e}")
        return False


def subscribe_pnl():
    ib = bot_state.ib
    try:
        if bot_state._pnl_subscription is None:
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
    from learning import load_trades, TRADE_LOG_FILE
    from orders import _is_option_contract

    ib = bot_state.ib
    try:
        existing = load_trades()
        existing_ids = set()
        existing_fuzzy = []
        for t in existing:
            eid = t.get("exec_id") or f"{t.get('symbol')}-{t.get('exit_time')}"
            existing_ids.add(eid)
            existing_ids.add(f"{t.get('symbol')}-{t.get('timestamp','')}")
            if t.get("order_id"):
                existing_ids.add(f"order-{t['order_id']}")
            eq  = t.get("qty") or t.get("shares") or t.get("total_shares") or 0
            ets = t.get("exit_time") or t.get("timestamp") or ""
            ep  = float(t.get("exit_price") or t.get("avg_price") or 0)
            if ets:
                existing_fuzzy.append((t.get("symbol", ""), eq, ets, ep))

        fills = ib.fills()
        if not fills:
            return

        order_groups = defaultdict(lambda: {
            "sym": "", "side": "", "order_id": None,
            "exec_ids": [], "total_shares": 0.0,
            "value": 0.0, "total_pnl": 0.0,
            "latest_time": "", "earliest_time": ""
        })

        opt_order_groups = defaultdict(lambda: {
            "sym": "", "underlying": "", "side": "", "order_id": None,
            "exec_ids": [], "total_contracts": 0.0,
            "value": 0.0, "total_pnl": 0.0,
            "latest_time": "", "earliest_time": "",
            "right": "", "strike": 0.0, "expiry": "",
        })

        for fill in fills:
            try:
                is_opt    = _is_option_contract(fill.contract)
                underlying = fill.contract.symbol
                side       = fill.execution.side.upper()
                price      = float(fill.execution.price)
                shares     = float(fill.execution.shares)
                etime      = fill.execution.time.strftime("%Y-%m-%d %H:%M:%S") if fill.execution.time else ""
                eid        = fill.execution.execId
                order_id   = fill.execution.orderId

                pnl = 0.0
                cr  = fill.commissionReport
                if cr is not None:
                    raw = getattr(cr, 'realizedPNL', None)
                    if raw is not None:
                        try:
                            raw_f = float(raw)
                            if not math.isnan(raw_f) and raw_f != 0.0:
                                pnl = raw_f
                        except (ValueError, TypeError):
                            pass

                if is_opt:
                    right     = getattr(fill.contract, 'right', '') or ''
                    strike    = getattr(fill.contract, 'strike', 0) or 0
                    raw_exp   = str(getattr(fill.contract, 'lastTradeDateOrContractMonth', ''))
                    if len(raw_exp) == 8 and raw_exp.isdigit():
                        expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                    else:
                        expiry_str = raw_exp
                    opt_sym = f"{underlying}_{right}_{strike}_{expiry_str}"

                    key = (opt_sym, order_id, side)
                    g = opt_order_groups[key]
                    g["sym"]             = opt_sym
                    g["underlying"]      = underlying
                    g["side"]            = side
                    g["order_id"]        = order_id
                    g["exec_ids"].append(eid)
                    g["total_contracts"] += shares
                    g["value"]           += price * shares
                    g["total_pnl"]       += pnl
                    g["right"]           = right
                    g["strike"]          = strike
                    g["expiry"]          = expiry_str
                    if not g["latest_time"] or etime > g["latest_time"]:
                        g["latest_time"] = etime
                    if not g["earliest_time"] or etime < g["earliest_time"]:
                        g["earliest_time"] = etime
                else:
                    sym = underlying
                    key = (sym, order_id, side)
                    g = order_groups[key]
                    g["sym"]          = sym
                    g["side"]         = side
                    g["order_id"]     = order_id
                    g["exec_ids"].append(eid)
                    g["total_shares"] += shares
                    g["value"]        += price * shares
                    g["total_pnl"]    += pnl
                    if not g["latest_time"] or etime > g["latest_time"]:
                        g["latest_time"] = etime
                    if not g["earliest_time"] or etime < g["earliest_time"]:
                        g["earliest_time"] = etime
            except Exception:
                continue

        buy_orders  = defaultdict(list)
        sell_orders = defaultdict(list)

        for (sym, order_id, side), g in order_groups.items():
            total_shares = g["total_shares"]
            if total_shares == 0:
                continue
            avg_price = g["value"] / total_shares
            order_rec = {
                "order_id":      order_id,
                "exec_ids":      g["exec_ids"],
                "avg_price":     round(avg_price, 4),
                "total_shares":  total_shares,
                "total_pnl":     g["total_pnl"],
                "time":          g["latest_time"],
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

                sell_qty   = int(sell["total_shares"])
                sell_ts    = sell["time"]
                sell_price = float(sell.get("avg_price") or 0)
                for (ex_sym, ex_qty, ex_ts, ex_price) in existing_fuzzy:
                    if ex_sym == sym and ex_qty == sell_qty:
                        price_match = (
                            ex_price == 0 or sell_price == 0
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
                    if buy["time"] <= sell["time"]:
                        matching_buy = buy
                        break

                if not matching_buy:
                    continue

                entry_price = matching_buy["avg_price"]
                entry_time  = matching_buy["time"]

                pnl = sell["total_pnl"]
                if pnl == 0.0:
                    pnl = round((sell["avg_price"] - entry_price) * sell["total_shares"], 2)
                if pnl == 0.0:
                    continue

                try:
                    entry_dt  = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt   = datetime.strptime(sell["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                trade = {
                    "symbol":       sym,
                    "action":       "BUY",
                    "direction":    "LONG",
                    "entry_price":  entry_price,
                    "exit_price":   sell["avg_price"],
                    "qty":          int(sell["total_shares"]),
                    "shares":       int(sell["total_shares"]),
                    "pnl":          round(pnl, 2),
                    "entry_time":   entry_time,
                    "exit_time":    sell["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason":  "stop_loss" if pnl < 0 else "take_profit",
                    "regime":       "UNKNOWN",
                    "vix":          0.0,
                    "score":        0,
                    "order_id":     sell["order_id"],
                    "exec_id":      sell["exec_ids"][0],
                    "timestamp":    sell["time"].replace(" ", "T"),
                    "reasoning":    "Backfilled from IBKR execution history on startup.",
                    "source":       "ibkr_backfill"
                }
                new_trades.append(trade)
                existing_ids.add(order_key)
                for eid in sell["exec_ids"]:
                    existing_ids.add(eid)

        # ── Process SHORT positions ───────────────────────────────────────────
        for sym, b_orders in buy_orders.items():
            for buy_cover in sorted(b_orders, key=lambda b: b["time"]):
                order_key = f"order-{buy_cover['order_id']}"
                already = (
                    order_key in existing_ids
                    or any(eid in existing_ids for eid in buy_cover["exec_ids"])
                )
                if already:
                    continue

                cover_qty   = int(buy_cover["total_shares"])
                cover_ts    = buy_cover["time"]
                cover_price = float(buy_cover.get("avg_price") or 0)
                for (ex_sym, ex_qty, ex_ts, ex_price) in existing_fuzzy:
                    if ex_sym == sym and ex_qty == cover_qty:
                        price_match = (
                            ex_price == 0 or cover_price == 0
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
                for sell_entry in sorted(sell_orders.get(sym, []),
                                         key=lambda s: s["time"], reverse=True):
                    sek = f"order-{sell_entry['order_id']}"
                    if sell_entry["time"] <= buy_cover["time"] and sek not in existing_ids:
                        matching_short_entry = sell_entry
                        break

                if not matching_short_entry:
                    continue

                entry_price = matching_short_entry["avg_price"]
                entry_time  = matching_short_entry["time"]

                pnl = buy_cover["total_pnl"]
                if pnl == 0.0:
                    pnl = round((entry_price - buy_cover["avg_price"]) * buy_cover["total_shares"], 2)
                if pnl == 0.0:
                    continue

                try:
                    entry_dt  = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt   = datetime.strptime(buy_cover["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                trade = {
                    "symbol":       sym,
                    "action":       "SELL",
                    "direction":    "SHORT",
                    "entry_price":  entry_price,
                    "exit_price":   buy_cover["avg_price"],
                    "qty":          int(buy_cover["total_shares"]),
                    "shares":       int(buy_cover["total_shares"]),
                    "pnl":          round(pnl, 2),
                    "entry_time":   entry_time,
                    "exit_time":    buy_cover["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason":  "stop_loss" if pnl < 0 else "take_profit",
                    "regime":       "UNKNOWN",
                    "vix":          0.0,
                    "score":        0,
                    "order_id":     buy_cover["order_id"],
                    "exec_id":      buy_cover["exec_ids"][0],
                    "timestamp":    buy_cover["time"].replace(" ", "T"),
                    "reasoning":    "Backfilled from IBKR execution history on startup.",
                    "source":       "ibkr_backfill"
                }
                new_trades.append(trade)
                existing_ids.add(order_key)
                for eid in buy_cover["exec_ids"]:
                    existing_ids.add(eid)
                existing_ids.add(f"order-{matching_short_entry['order_id']}")

        # ── Process OPTIONS trades ────────────────────────────────────────────
        opt_buy_orders  = defaultdict(list)
        opt_sell_orders = defaultdict(list)
        for (opt_sym, order_id, side), g in opt_order_groups.items():
            total = g["total_contracts"]
            if total == 0:
                continue
            avg_premium = g["value"] / total
            order_rec = {
                "order_id":        order_id,
                "exec_ids":        g["exec_ids"],
                "avg_price":       round(avg_premium, 4),
                "total_contracts": total,
                "total_pnl":       g["total_pnl"],
                "time":            g["latest_time"],
                "earliest_time":   g["earliest_time"],
                "right":           g["right"],
                "strike":          g["strike"],
                "expiry":          g["expiry"],
                "underlying":      g["underlying"],
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

                sell_qty   = int(sell["total_contracts"])
                sell_ts    = sell["time"]
                sell_price = float(sell.get("avg_price") or 0)
                for (ex_sym, ex_qty, ex_ts, ex_price) in existing_fuzzy:
                    if (ex_sym == opt_sym or ex_sym == sell["underlying"]) and ex_qty == sell_qty:
                        price_match = (
                            ex_price == 0 or sell_price == 0
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
                entry_time    = matching_buy["time"]

                pnl = sell["total_pnl"]
                if pnl == 0.0:
                    pnl = round((sell["avg_price"] - entry_premium) * sell["total_contracts"] * 100, 2)
                if pnl == 0.0:
                    continue

                try:
                    entry_dt  = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt   = datetime.strptime(sell["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                trade = {
                    "symbol":       sell["underlying"],
                    "action":       "BUY",
                    "direction":    "LONG",
                    "instrument":   "option",
                    "right":        sell["right"],
                    "strike":       sell["strike"],
                    "expiry":       sell["expiry"],
                    "entry_price":  entry_premium,
                    "exit_price":   sell["avg_price"],
                    "qty":          int(sell["total_contracts"]),
                    "shares":       int(sell["total_contracts"]),
                    "pnl":          round(pnl, 2),
                    "entry_time":   entry_time,
                    "exit_time":    sell["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason":  "stop_loss" if pnl < 0 else "take_profit",
                    "regime":       "UNKNOWN",
                    "vix":          0.0,
                    "score":        0,
                    "order_id":     sell["order_id"],
                    "exec_id":      sell["exec_ids"][0],
                    "timestamp":    sell["time"].replace(" ", "T"),
                    "reasoning":    "Backfilled from IBKR execution history on startup.",
                    "source":       "ibkr_backfill"
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
            ts  = t.get("timestamp") or t.get("exit_time") or ""
            ep  = t.get("entry_price") or 0

            is_dupe = False
            for i, (s_sym, s_qty, s_ts, s_ep, s_idx) in enumerate(seen):
                if s_sym != sym or not ts or not s_ts:
                    continue
                qty_match   = (s_qty == qty) if qty and s_qty else True
                price_match = (abs(ep - s_ep) / max(s_ep, 0.01) < 0.02) if ep and s_ep else False
                if not qty_match and not price_match:
                    continue
                try:
                    t1 = datetime.fromisoformat(s_ts.replace(" ", "T"))
                    t2 = datetime.fromisoformat(ts.replace(" ", "T"))
                    if abs((t2 - t1).total_seconds()) < 300:
                        existing_rec = deduped[s_idx]
                        existing_pnl = abs(existing_rec.get("pnl") or 0)
                        new_pnl      = abs(t.get("pnl") or 0)
                        existing_oid = existing_rec.get("order_id")
                        new_oid      = t.get("order_id")
                        should_replace = (
                            (new_oid and not existing_oid)
                            or (new_pnl > existing_pnl and not (existing_oid and not new_oid))
                        )
                        if should_replace:
                            deduped[s_idx] = t
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
    from learning import log_order as _log_order, load_orders as _load_orders
    ib = bot_state.ib
    try:
        orders = _load_orders()

        for t in ib.trades():
            contract    = t.contract
            order       = t.order
            ibkr_status = (t.orderStatus.status or "").upper()
            sec_type    = getattr(contract, 'secType', 'STK')
            instrument  = "option" if sec_type == "OPT" else "stock"

            if ibkr_status in ("FILLED",):
                mapped_status = "FILLED"
            elif ibkr_status in ("CANCELLED", "APICANCELED", "APICANCELLED"):
                mapped_status = "CANCELLED"
            elif ibkr_status in ("INACTIVE",):
                mapped_status = "CANCELLED"
            elif ibkr_status in ("SUBMITTED", "PRESUBMITTED", "PENDINGSUBMIT"):
                mapped_status = "SUBMITTED"
            else:
                mapped_status = ibkr_status

            _fp        = t.orderStatus.avgFillPrice
            fill_price = float(_fp) if (_fp is not None and _fp > 0) else 0
            filled_qty = int(t.orderStatus.filled) if t.orderStatus.filled else 0

            if not order.orderId:  # IBKR hasn't assigned an ID yet — skip to avoid order_id=0 accumulation
                continue

            _log_order({
                "order_id":   order.orderId,
                "symbol":     contract.symbol,
                "side":       order.action,
                "order_type": order.orderType,
                "qty":        int(order.totalQuantity),
                "price":      float(order.lmtPrice) if order.lmtPrice and abs(float(order.lmtPrice)) < 1e10 else (float(order.auxPrice) if order.auxPrice and abs(float(order.auxPrice)) < 1e10 else 0),
                "status":     mapped_status,
                "instrument": instrument,
                "filled_qty": filled_qty,
                "fill_price": fill_price if fill_price > 0 else None,
                "source":     "ibkr_sync",
            })

            if mapped_status == "FILLED" and fill_price > 0 and order.action == "BUY" and instrument == "stock":
                from orders import _safe_update_trade
                sym       = contract.symbol
                total_qty = int(order.totalQuantity)
                updates   = {"entry": fill_price, "status": "FILLED"}
                if 0 < filled_qty < total_qty:
                    updates["qty"] = filled_qty
                    clog("WARNING", f"Partial fill: {sym} ordered {total_qty} filled {filled_qty} @ ${fill_price:.2f} — tracker qty adjusted")
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

        orders  = _load_orders()
        changed = False
        for o in orders:
            oid    = o.get("order_id")
            status = (o.get("status") or "").upper()
            if not oid or status not in ("SUBMITTED", "PRESUBMITTED", "PENDING"):
                continue
            if oid in ibkr_open_ids:
                continue
            if oid in ibkr_known_ids:
                continue
            o["status"] = "CANCELLED"
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
        contract    = trade.contract
        order       = trade.order
        ibkr_status = (trade.orderStatus.status or "").upper()
        sec_type    = getattr(contract, 'secType', 'STK')
        instrument  = "option" if sec_type == "OPT" else "stock"

        if ibkr_status in ("FILLED",):
            mapped_status = "FILLED"
        elif ibkr_status in ("CANCELLED", "APICANCELED", "APICANCELLED", "INACTIVE"):
            mapped_status = "CANCELLED"
        elif ibkr_status in ("SUBMITTED", "PRESUBMITTED", "PENDINGSUBMIT"):
            mapped_status = "SUBMITTED"
        else:
            mapped_status = ibkr_status

        _fp        = trade.orderStatus.avgFillPrice
        fill_price = float(_fp) if (_fp is not None and _fp > 0) else 0
        filled_qty = int(trade.orderStatus.filled) if trade.orderStatus.filled else 0

        _log_order({
            "order_id":   order.orderId,
            "symbol":     contract.symbol,
            "side":       order.action,
            "order_type": order.orderType,
            "qty":        int(order.totalQuantity),
            "price":      float(order.lmtPrice) if order.lmtPrice and abs(float(order.lmtPrice)) < 1e10 else (float(order.auxPrice) if order.auxPrice and abs(float(order.auxPrice)) < 1e10 else 0),
            "status":     mapped_status,
            "instrument": instrument,
            "filled_qty": filled_qty,
            "fill_price": fill_price if fill_price > 0 else None,
            "source":     "ibkr_event",
        })

        if mapped_status == "FILLED" and fill_price > 0 and order.action == "BUY" and instrument == "stock":
            from orders import _safe_update_trade
            sym       = contract.symbol
            total_qty = int(order.totalQuantity)
            updates   = {"entry": fill_price, "status": "FILLED"}
            if 0 < filled_qty < total_qty:
                updates["qty"] = filled_qty
                clog("WARNING", f"Partial fill event: {sym} ordered {total_qty} filled {filled_qty} @ ${fill_price:.2f} — tracker qty adjusted")
            _safe_update_trade(sym, updates)

    except Exception as e:
        clog("ERROR", f"Order status event error: {e}")
