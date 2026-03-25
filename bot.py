#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  bot.py                                     ║
# ║   Main orchestrator — runs everything                        ║
# ║                                                              ║
# ║   Usage:                                                     ║
# ║     export ANTHROPIC_API_KEY="sk-ant-..."                    ║
# ║     python3 bot.py                                           ║
# ║                                                              ║
# ║   Dashboard: http://localhost:8080                           ║
# ╚══════════════════════════════════════════════════════════════╝

import sys
import os
import json
import importlib
import hashlib
import time
import logging
import threading
import schedule

# Suppress noisy yfinance HTTP errors globally (401 Invalid Crumb, 404 No fundamentals)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from colorama import Fore, Style, init as colorama_init

from ib_async import IB

from config import CONFIG
from scanner import get_dynamic_universe, get_market_regime, get_tv_signal_cache
from signals import score_universe, fetch_multi_timeframe
from news import batch_news_sentiment
from agents import run_all_agents
from orders import execute_buy, execute_sell, flatten_all, reconcile_with_ibkr, get_open_positions, update_position_prices, update_positions_from_ibkr, execute_buy_option, execute_sell_option
from options import find_best_contract, check_options_exits
from options_scanner import scan_options_universe
from risk import can_trade, get_session, get_scan_interval, reset_daily_state
from learning import log_trade, load_trades, load_orders, get_performance_summary, run_weekly_review, TRADE_LOG_FILE, get_effective_capital, record_capital_adjustment
from dashboard import DASHBOARD_HTML

EQUITY_FILE = "equity_history.json"
PROMPTS_FILE = "prompt_versions.json"

colorama_init()

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["log_file"]),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("decifer.bot")

# ── Colours for terminal ───────────────────────────────────────
COLORS = {
    "TRADE":    Fore.YELLOW,
    "SIGNAL":   Fore.GREEN,
    "ANALYSIS": Fore.CYAN,
    "ERROR":    Fore.RED,
    "INFO":     Fore.WHITE,
    "RISK":     Fore.RED,
    "SCAN":     Fore.MAGENTA,
}

# ── Live dashboard state ────────────────────────────────────────
dash = {
    "status":               "starting",
    "account":              CONFIG["active_account"],
    "portfolio_value":      0.0,
    "daily_pnl":            0.0,
    "session":              "UNKNOWN",
    "scan_count":           0,
    "last_scan":            None,
    "scanning":             False,
    "next_scan_seconds":    0,
    "scan_interval_seconds": 300,
    "regime":               {"regime": "UNKNOWN", "vix": 0, "spy_price": 0},
    "positions":            [],
    "trades":               [],
    "all_trades":           [],
    "logs":                 [],
    "claude_analysis":      "Waiting for first scan...",
    "agent_outputs":        {},
    "agent_conversation":   [],
    "last_agents_agreed":   None,
    "agents_required":      CONFIG["agents_required_to_agree"],
    "performance":          {},
    "equity_history":       [],
    "paused":               False,
    "killed":               False,
    "favourites":           [],
    "hot_reload_count":     0,
    "last_reload":          None,
    "last_reload_files":    [],
    "news_data":            {},
    "all_orders":           [],
    "recent_orders":        [],
}

# ── Load persistent equity history ────────────────────────────
FAVOURITES_FILE = "favourites.json"
SETTINGS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "settings_override.json")

def load_favourites() -> list:
    try:
        if os.path.exists(FAVOURITES_FILE):
            with open(FAVOURITES_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []

def save_favourites(favs: list):
    with open(FAVOURITES_FILE, 'w') as f:
        json.dump(favs, f)

# ── Settings persistence ─────────────────────────────────────
# Dashboard settings are saved to a JSON file so they survive restarts.
# On startup, any overrides in the file are applied on top of config.py defaults.

# Keys that the dashboard is allowed to persist (prevent writing connection/system keys)
_DASHBOARD_SETTINGS_KEYS = {
    "risk_pct_per_trade", "daily_loss_limit", "max_positions",
    "min_cash_reserve", "max_single_position",
    "min_score_to_trade", "high_conviction_score", "agents_required_to_agree",
    "options_max_risk_pct", "options_max_ivr", "options_target_delta",
}

def load_settings_overrides():
    """Load persisted settings overrides and apply them to CONFIG."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                overrides = json.load(f)
            applied = []
            for key, val in overrides.items():
                if key in CONFIG and key in _DASHBOARD_SETTINGS_KEYS:
                    CONFIG[key] = val
                    applied.append(key)
            if applied:
                clog("INFO", f"⚙️ Loaded saved settings: {', '.join(applied)}")
    except Exception as e:
        clog("ERROR", f"Failed to load settings overrides: {e}")

def save_settings_overrides(settings: dict):
    """Persist dashboard settings to disk."""
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        # Merge with existing overrides (don't lose keys not in this request)
        existing = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                existing = json.load(f)
        for key, val in settings.items():
            if key in _DASHBOARD_SETTINGS_KEYS:
                existing[key] = val
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        clog("ERROR", f"Failed to save settings: {e}")

# ── HOT RELOAD SYSTEM ─────────────────────────────────────────────────
# Watches all .py files and reloads modules when they change.
# Bot keeps running — positions, state, and IBKR connection are preserved.

WATCHED_MODULES = {
    "signals":   "signals",
    "scanner":   "scanner",
    "agents":    "agents",
    "risk":      "risk",
    "orders":    "orders",
    "learning":  "learning",
    "dashboard": "dashboard",
}

_file_hashes = {}

def _file_hash(path: str) -> str:
    """Return MD5 hash of file contents."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""

def _init_hashes():
    """Record initial file hashes on startup."""
    base = os.path.dirname(os.path.abspath(__file__))
    for name in list(WATCHED_MODULES.keys()) + ["bot", "config"]:
        path = os.path.join(base, f"{name}.py")
        _file_hashes[name] = _file_hash(path)

def check_and_reload():
    """
    Check all watched files for changes.
    If changed: reload the module, update the dashboard HTML, log the reload.
    Called at the start of every scan — zero overhead when nothing changed.
    """
    global DASHBOARD_HTML
    base    = os.path.dirname(os.path.abspath(__file__))
    changed = []

    for mod_name, import_name in WATCHED_MODULES.items():
        path    = os.path.join(base, f"{mod_name}.py")
        current = _file_hash(path)
        if current and current != _file_hashes.get(mod_name, ""):
            try:
                if import_name in sys.modules:
                    importlib.reload(sys.modules[import_name])
                    _file_hashes[mod_name] = current
                    changed.append(mod_name)
                    clog("INFO", f"🔄 Hot reload: {mod_name}.py updated and reloaded")
            except Exception as e:
                clog("ERROR", f"Hot reload failed for {mod_name}: {e}")

    # Special case: dashboard.py — update the HTML served to browser
    dash_path    = os.path.join(base, "dashboard.py")
    dash_current = _file_hash(dash_path)
    if dash_current and dash_current != _file_hashes.get("dashboard", ""):
        try:
            import dashboard as _dash
            importlib.reload(_dash)
            DASHBOARD_HTML = _dash.DASHBOARD_HTML
            _file_hashes["dashboard"] = dash_current
            changed.append("dashboard")
            clog("INFO", "🔄 Hot reload: dashboard.py updated — refresh browser to see changes")
        except Exception as e:
            clog("ERROR", f"Hot reload failed for dashboard: {e}")

    # Special case: config.py — reload config and apply new settings
    config_path    = os.path.join(base, "config.py")
    config_current = _file_hash(config_path)
    if config_current and config_current != _file_hashes.get("config", ""):
        try:
            import config as _config
            importlib.reload(_config)
            CONFIG.update(_config.CONFIG)
            _file_hashes["config"] = config_current
            changed.append("config")
            clog("INFO", "🔄 Hot reload: config.py updated — new settings active immediately")
        except Exception as e:
            clog("ERROR", f"Hot reload failed for config: {e}")

    if changed:
        dash["hot_reload_count"] = dash.get("hot_reload_count", 0) + 1
        dash["last_reload"]      = datetime.now().strftime("%H:%M:%S")
        dash["last_reload_files"] = changed

    return changed


def backfill_trades_from_ibkr():
    """
    On startup, read IBKR execution history and match buy/sell pairs.
    Write any completed trades not already in trades.json.
    Partial fills (lots) for the same order are consolidated into one record
    using weighted-average price so they count as a single trade.
    """
    try:
        import math
        from collections import defaultdict

        existing = load_trades()
        existing_ids = set()
        # Build a list of (symbol, qty, timestamp) for fuzzy dedup matching
        existing_fuzzy = []
        for t in existing:
            eid = t.get("exec_id") or f"{t.get('symbol')}-{t.get('exit_time')}"
            existing_ids.add(eid)
            existing_ids.add(f"{t.get('symbol')}-{t.get('timestamp','')}")
            # Also index by order_id so we never double-log the same order
            if t.get("order_id"):
                existing_ids.add(f"order-{t['order_id']}")
            # Fuzzy match: (symbol, qty, exit_time) for catching re-created dupes
            eq = t.get("qty") or t.get("shares") or t.get("total_shares") or 0
            ets = t.get("exit_time") or t.get("timestamp") or ""
            if ets:
                existing_fuzzy.append((t.get("symbol",""), eq, ets))

        fills = ib.fills()
        if not fills:
            return

        # ── Group fills by (symbol, orderId, side) ──────────────────────
        # Each unique (symbol, orderId) = one discrete order, regardless of
        # how many partial fill executions IBKR fired for it.
        order_groups = defaultdict(lambda: {
            "sym": "", "side": "", "order_id": None,
            "exec_ids": [], "total_shares": 0.0,
            "value": 0.0,          # sum(price * shares) for weighted avg
            "total_pnl": 0.0,
            "latest_time": "",
            "earliest_time": ""
        })

        for fill in fills:
            try:
                # CRITICAL: Only process stock fills — options have tiny premiums
                # and huge "qty" that corrupt P&L calculations when mixed with stocks.
                sec_type = getattr(fill.contract, 'secType', 'STK')
                if sec_type != 'STK':
                    continue

                sym      = fill.contract.symbol
                side     = fill.execution.side.upper()
                price    = float(fill.execution.price)
                shares   = float(fill.execution.shares)
                etime    = fill.execution.time.strftime("%Y-%m-%d %H:%M:%S") if fill.execution.time else ""
                eid      = fill.execution.execId
                order_id = fill.execution.orderId   # same for all partial fills of one order

                pnl = 0.0
                cr = fill.commissionReport
                if cr is not None:
                    raw = getattr(cr, 'realizedPNL', None)
                    if raw is not None:
                        try:
                            raw_f = float(raw)
                            if not math.isnan(raw_f) and raw_f != 0.0:
                                pnl = raw_f
                        except (ValueError, TypeError):
                            pass

                key = (sym, order_id, side)
                g = order_groups[key]
                g["sym"]          = sym
                g["side"]         = side
                g["order_id"]     = order_id
                g["exec_ids"].append(eid)
                g["total_shares"] += shares
                g["value"]        += price * shares   # for weighted avg
                g["total_pnl"]    += pnl
                if not g["latest_time"] or etime > g["latest_time"]:
                    g["latest_time"] = etime
                if not g["earliest_time"] or etime < g["earliest_time"]:
                    g["earliest_time"] = etime
            except Exception:
                continue

        # Build consolidated buy/sell maps keyed by symbol
        # Each entry = one order (already aggregated across lots)
        buy_orders  = defaultdict(list)
        sell_orders = defaultdict(list)

        for (sym, order_id, side), g in order_groups.items():
            total_shares = g["total_shares"]
            if total_shares == 0:
                continue
            avg_price = g["value"] / total_shares
            order_rec = {
                "order_id":    order_id,
                "exec_ids":    g["exec_ids"],
                "avg_price":   round(avg_price, 4),
                "total_shares": total_shares,
                "total_pnl":   g["total_pnl"],
                "time":        g["latest_time"],
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
                # Skip if any exec_id OR the order_id is already logged
                already = (
                    order_key in existing_ids
                    or any(eid in existing_ids for eid in sell["exec_ids"])
                    or f"{sym}-{sell['time'].replace(' ', 'T')}" in existing_ids
                )
                if already:
                    continue

                # Fuzzy dedup: skip if an existing trade matches (symbol, qty, ~time)
                sell_qty = int(sell["total_shares"])
                sell_ts  = sell["time"]
                for (ex_sym, ex_qty, ex_ts) in existing_fuzzy:
                    if ex_sym == sym and ex_qty == sell_qty:
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

                # Find the most recent matching buy order that happened before this sell
                matching_buy = None
                for buy in sorted(buy_orders.get(sym, []), key=lambda b: b["time"], reverse=True):
                    if buy["time"] <= sell["time"]:
                        matching_buy = buy
                        break

                entry_price = matching_buy["avg_price"] if matching_buy else sell["avg_price"]
                entry_time  = matching_buy["time"]      if matching_buy else sell["time"]

                # No matching buy = either a short entry (SLD=open, BOT=close) or
                # history is older than fill window.  Either way skip — the short
                # loop below will handle covers with a prior SLD.
                if not matching_buy:
                    continue

                # Use summed commission P&L if available, else calculate from avg prices
                pnl = sell["total_pnl"]
                if pnl == 0.0:
                    pnl = round((sell["avg_price"] - entry_price) * sell["total_shares"], 2)
                if pnl == 0.0:
                    continue

                # Calculate hold time in minutes
                try:
                    entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt  = datetime.strptime(sell["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                trade = {
                    "symbol":      sym,
                    "action":      "BUY",
                    "direction":   "LONG",
                    "entry_price": entry_price,
                    "exit_price":  sell["avg_price"],
                    "qty":         int(sell["total_shares"]),
                    "shares":      int(sell["total_shares"]),
                    "pnl":         round(pnl, 2),
                    "entry_time":  entry_time,
                    "exit_time":   sell["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason": "stop_loss" if pnl < 0 else "take_profit",
                    "regime":      "UNKNOWN",
                    "vix":         0.0,
                    "score":       0,
                    "order_id":    sell["order_id"],
                    "exec_id":     sell["exec_ids"][0],   # primary exec_id for legacy compat
                    "timestamp":   sell["time"].replace(" ", "T"),
                    "reasoning":   "Backfilled from IBKR execution history on startup.",
                    "source":      "ibkr_backfill"
                }
                new_trades.append(trade)
                existing_ids.add(order_key)
                for eid in sell["exec_ids"]:
                    existing_ids.add(eid)

        # ── Process SHORT positions (BOT=cover close, SLD=short entry) ───────
        # For a short trade IBKR fires: SLD to open, BOT to cover.
        # The long loop above only looks at SLD fills → it skips SLD entries that
        # have no prior BOT.  This loop finds BOT (cover) fills that have a prior
        # unused SLD (short entry) and creates a properly-directed SHORT record.
        for sym, b_orders in buy_orders.items():
            for buy_cover in sorted(b_orders, key=lambda b: b["time"]):
                order_key = f"order-{buy_cover['order_id']}"
                already = (
                    order_key in existing_ids
                    or any(eid in existing_ids for eid in buy_cover["exec_ids"])
                )
                if already:
                    continue

                # Fuzzy dedup for shorts too
                cover_qty = int(buy_cover["total_shares"])
                cover_ts  = buy_cover["time"]
                for (ex_sym, ex_qty, ex_ts) in existing_fuzzy:
                    if ex_sym == sym and ex_qty == cover_qty:
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

                # Find the most recent SLD (short entry) before this BOT cover
                # that was NOT already used as a long-close in the loop above.
                matching_short_entry = None
                for sell_entry in sorted(sell_orders.get(sym, []),
                                         key=lambda s: s["time"], reverse=True):
                    sek = f"order-{sell_entry['order_id']}"
                    if sell_entry["time"] <= buy_cover["time"] and sek not in existing_ids:
                        matching_short_entry = sell_entry
                        break

                if not matching_short_entry:
                    # No prior unused SLD → regular long open (not a cover); skip
                    continue

                entry_price = matching_short_entry["avg_price"]
                entry_time  = matching_short_entry["time"]

                # IBKR assigns realizedPNL to the closing fill (BOT for shorts)
                pnl = buy_cover["total_pnl"]
                if pnl == 0.0:
                    # Fallback: (entry - exit) × shares  (short wins when price drops)
                    pnl = round((entry_price - buy_cover["avg_price"]) * buy_cover["total_shares"], 2)
                if pnl == 0.0:
                    continue

                # Calculate hold time in minutes
                try:
                    entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                    exit_dt  = datetime.strptime(buy_cover["time"], "%Y-%m-%d %H:%M:%S")
                    hold_mins = int((exit_dt - entry_dt).total_seconds() / 60)
                except Exception:
                    hold_mins = 0

                trade = {
                    "symbol":      sym,
                    "action":      "SELL",
                    "direction":   "SHORT",
                    "entry_price": entry_price,
                    "exit_price":  buy_cover["avg_price"],
                    "qty":         int(buy_cover["total_shares"]),
                    "shares":      int(buy_cover["total_shares"]),
                    "pnl":         round(pnl, 2),
                    "entry_time":  entry_time,
                    "exit_time":   buy_cover["time"],
                    "hold_minutes": hold_mins,
                    "exit_reason": "stop_loss" if pnl < 0 else "take_profit",
                    "regime":      "UNKNOWN",
                    "vix":         0.0,
                    "score":       0,
                    "order_id":    buy_cover["order_id"],
                    "exec_id":     buy_cover["exec_ids"][0],
                    "timestamp":   buy_cover["time"].replace(" ", "T"),
                    "reasoning":   "Backfilled from IBKR execution history on startup.",
                    "source":      "ibkr_backfill"
                }
                new_trades.append(trade)
                existing_ids.add(order_key)
                for eid in buy_cover["exec_ids"]:
                    existing_ids.add(eid)
                # Mark the SLD entry as consumed so it isn't matched again
                existing_ids.add(f"order-{matching_short_entry['order_id']}")

        if new_trades:
            all_trades = existing + new_trades
        else:
            all_trades = existing

        # ── Deduplicate: merge trades with same (symbol, qty) within 5 min ──
        # IBKR sometimes fires multiple order IDs for what the trader sees as one trade.
        # Also catches learning.py CLOSE + backfill creating duplicate records.
        # Match on (symbol, qty, time proximity) — direction is NOT required to match
        # because different sources may label the same trade differently.
        before_count = len(all_trades)
        deduped = []
        seen = []  # list of (symbol, qty, timestamp, index_in_deduped)

        # Sort by timestamp so we process chronologically
        all_trades.sort(key=lambda t: t.get("timestamp") or t.get("exit_time") or "")

        for t in all_trades:
            sym  = t.get("symbol", "")
            qty  = t.get("qty") or t.get("shares") or t.get("total_shares") or 0
            ts   = t.get("timestamp") or t.get("exit_time") or ""
            ep   = t.get("entry_price") or 0

            is_dupe = False
            for i, (s_sym, s_qty, s_ts, s_ep, s_idx) in enumerate(seen):
                if s_sym != sym or not ts or not s_ts:
                    continue
                # Match: same symbol + same qty + within 5 min
                # OR: same symbol + similar entry price + within 5 min (covers qty mismatches)
                qty_match = (s_qty == qty) if qty and s_qty else True
                price_match = (abs(ep - s_ep) / max(s_ep, 0.01) < 0.02) if ep and s_ep else False
                if not qty_match and not price_match:
                    continue
                try:
                    t1 = datetime.fromisoformat(s_ts.replace(" ", "T"))
                    t2 = datetime.fromisoformat(ts.replace(" ", "T"))
                    if abs((t2 - t1).total_seconds()) < 300:  # within 5 minutes
                        # Keep the record with: order_id > no order_id, then better P&L
                        existing_rec = deduped[s_idx]
                        existing_pnl = abs(existing_rec.get("pnl") or 0)
                        new_pnl      = abs(t.get("pnl") or 0)
                        existing_oid = existing_rec.get("order_id")
                        new_oid      = t.get("order_id")
                        # Prefer: has order_id, then higher abs(pnl), then more fields
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


def sync_orders_from_ibkr():
    """
    Sync order statuses from IBKR into orders.json.
    Called at startup AND on every scan cycle so the dashboard stays current.

    Three-pass approach:
      1) Update existing orders whose status changed (SUBMITTED → FILLED/CANCELLED)
      2) Log any new open trades not yet in orders.json
      3) Log any new fills not yet in orders.json
    """
    from learning import log_order as _log_order, load_orders as _load_orders
    try:
        orders = _load_orders()
        order_ids_in_file = {o.get("order_id") for o in orders if o.get("order_id")}

        # ── Pass 1: Update statuses of ALL trades IBKR knows about ──
        # ib.trades() returns both open and completed trades from this session
        for t in ib.trades():
            contract = t.contract
            order = t.order
            ibkr_status = (t.orderStatus.status or "").upper()
            sec_type = getattr(contract, 'secType', 'STK')
            instrument = "option" if sec_type == "OPT" else "stock"

            # Map IBKR statuses to our simplified set
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

            fill_price = float(t.orderStatus.avgFillPrice) if t.orderStatus.avgFillPrice else 0
            filled_qty = int(t.orderStatus.filled) if t.orderStatus.filled else 0

            _log_order({
                "order_id":    order.orderId,
                "symbol":      contract.symbol,
                "side":        order.action,
                "order_type":  order.orderType,
                "qty":         int(order.totalQuantity),
                "price":       float(order.lmtPrice) if order.lmtPrice and abs(float(order.lmtPrice)) < 1e10 else (float(order.auxPrice) if order.auxPrice and abs(float(order.auxPrice)) < 1e10 else 0),
                "status":      mapped_status,
                "instrument":  instrument,
                "filled_qty":  filled_qty,
                "fill_price":  fill_price if fill_price > 0 else None,
                "source":      "ibkr_sync",
            })

        # ── Pass 2: Mark stale SUBMITTED orders as CANCELLED ──
        # An order is stale if it's SUBMITTED in our file but IBKR doesn't have it
        # as an open order. ib.trades() only covers the current session, so we also
        # check ib.openTrades() which is the definitive list of live pending orders.
        ibkr_known_ids = set()
        # Current session trades (open + completed)
        for t in ib.trades():
            ibkr_known_ids.add(t.order.orderId)
        # Fills from current session
        for fill in ib.fills():
            ibkr_known_ids.add(fill.execution.orderId)
        # Currently open/pending orders (survives across sessions)
        ibkr_open_ids = set()
        for t in ib.openTrades():
            ibkr_open_ids.add(t.order.orderId)
            ibkr_known_ids.add(t.order.orderId)

        # Reload after pass 1 updates
        orders = _load_orders()
        changed = False
        for o in orders:
            oid = o.get("order_id")
            status = (o.get("status") or "").upper()
            if not oid or status not in ("SUBMITTED", "PRESUBMITTED", "PENDING"):
                continue
            # If IBKR knows about it from this session AND it's still open, keep it
            if oid in ibkr_open_ids:
                continue
            # If IBKR knows about it from this session (completed/cancelled), pass 1 handled it
            if oid in ibkr_known_ids:
                continue
            # IBKR doesn't know about this order at all — it's from a prior session
            # and is no longer pending. Mark as cancelled.
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
        contract = trade.contract
        order = trade.order
        ibkr_status = (trade.orderStatus.status or "").upper()
        sec_type = getattr(contract, 'secType', 'STK')
        instrument = "option" if sec_type == "OPT" else "stock"

        if ibkr_status in ("FILLED",):
            mapped_status = "FILLED"
        elif ibkr_status in ("CANCELLED", "APICANCELED", "APICANCELLED", "INACTIVE"):
            mapped_status = "CANCELLED"
        elif ibkr_status in ("SUBMITTED", "PRESUBMITTED", "PENDINGSUBMIT"):
            mapped_status = "SUBMITTED"
        else:
            mapped_status = ibkr_status

        fill_price = float(trade.orderStatus.avgFillPrice) if trade.orderStatus.avgFillPrice else 0

        _log_order({
            "order_id":    order.orderId,
            "symbol":      contract.symbol,
            "side":        order.action,
            "order_type":  order.orderType,
            "qty":         int(order.totalQuantity),
            "price":       float(order.lmtPrice) if order.lmtPrice and abs(float(order.lmtPrice)) < 1e10 else (float(order.auxPrice) if order.auxPrice and abs(float(order.auxPrice)) < 1e10 else 0),
            "status":      mapped_status,
            "instrument":  instrument,
            "filled_qty":  int(trade.orderStatus.filled) if trade.orderStatus.filled else 0,
            "fill_price":  fill_price if fill_price > 0 else None,
            "source":      "ibkr_event",
        })
    except Exception as e:
        clog("ERROR", f"Order status event error: {e}")


def load_equity_history() -> list:
    try:
        if os.path.exists(EQUITY_FILE):
            with open(EQUITY_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []

def save_equity_history(history: list):
    try:
        with open(EQUITY_FILE, 'w') as f:
            json.dump(history[-2000:], f)
    except Exception as e:
        log.error(f"Failed to save equity history: {e}")

# ── IB connection ──────────────────────────────────────────────
ib = IB()


def clog(type_: str, msg: str):
    """Coloured terminal log + dashboard log."""
    color = COLORS.get(type_, Fore.WHITE)
    print(f"{color}[{type_}]{Style.RESET_ALL}  {msg}")
    log.info(f"[{type_}] {msg}")
    dash["logs"].insert(0, {
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": type_,
        "msg":  msg
    })
    if len(dash["logs"]) > 500:
        dash["logs"] = dash["logs"][:500]


# ── IBKR connection ────────────────────────────────────────────
def connect_ibkr() -> bool:
    try:
        if ib.isConnected():
            return True
        ib.connect(CONFIG["ibkr_host"], CONFIG["ibkr_port"],
                   clientId=CONFIG["ibkr_client_id"], timeout=15)
        # Request delayed market data (type 3) — FREE, no subscription needed.
        # Fixes Error 10089 ("Requested market data requires additional subscription").
        # IBKR will try live first; if no subscription, falls back to 15-min delayed.
        # Delayed price is used for order validation only — actual fill is at market.
        ib.reqMarketDataType(3)
        clog("INFO", f"IBKR connected — port {CONFIG['ibkr_port']} | Account: {CONFIG['active_account']} | Market data: DELAYED (free)")
        reconcile_with_ibkr(ib)
        dash["status"] = "running"
        return True
    except Exception as e:
        clog("ERROR", f"IBKR connection failed: {e}")
        return False


# Live P&L subscription
_pnl_subscription = None

def subscribe_pnl():
    global _pnl_subscription
    try:
        if _pnl_subscription is None:
            _pnl_subscription = ib.reqPnL(CONFIG["active_account"])
            clog("INFO", "P&L subscription active")
    except Exception as e:
        clog("ERROR", f"P&L subscription failed: {e}")

def get_account_data():
    """Fetch portfolio value and daily P&L from IBKR."""
    try:
        vals = ib.accountValues(CONFIG["active_account"])
        pv = 0.0
        for v in vals:
            if v.tag == "NetLiquidation" and v.currency == "USD":
                pv = float(v.value)
                break
        pnl = 0.0
        if _pnl_subscription is not None:
            import math
            daily = _pnl_subscription.dailyPnL
            if daily is not None and not math.isnan(daily):
                pnl = round(float(daily), 2)
        return pv, pnl
    except Exception as e:
        clog("ERROR", f"Account data error: {e}")
        return dash["portfolio_value"], dash["daily_pnl"]


def get_news_headlines() -> list:
    """Return recent news headlines from cached scan data for agents."""
    try:
        # Headlines are now pulled from news.py during scan and stored in dash
        all_headlines = []
        for sym, ndata in dash.get("news_data", {}).items():
            for h in ndata.get("headlines", [])[:3]:
                all_headlines.append(f"[{sym}] {h}")
        return all_headlines[:20]
    except Exception:
        return []


def get_fx_snapshot() -> dict:
    """Get snapshot of key FX pairs."""
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf
    pairs = {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
             "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X"}
    result = {}

    def fetch_pair(name, ticker):
        try:
            from signals import _safe_download
            data = _safe_download(ticker, period="1d", interval="1h", progress=False, auto_adjust=True)
            if data is not None and len(data) > 1:
                price = float(data["Close"].squeeze().iloc[-1])
                prev  = float(data["Close"].squeeze().iloc[-2])
                return name, {
                    "price":      round(price, 5),
                    "change_pct": round((price - prev) / prev * 100, 3)
                }
        except Exception:
            pass
        return name, None

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_pair, name, ticker) for name, ticker in pairs.items()]
        for future in futures:
            name, data = future.result()
            if data:
                result[name] = data
    return result


# ── Detect positions closed by IBKR (stop loss / take profit) ────────
def check_external_closes(regime: dict):
    """
    Compare bot's open_trades tracker against IBKR actual positions.
    If a position exists in our tracker but not in IBKR, it was closed
    externally (stop loss hit, take profit hit, or manual close).
    Log it properly so Trade History tab shows it.
    """
    from orders import open_trades
    from learning import log_trade, load_trades

    try:
        # Use portfolio() — same source as reconcile, includes position=0 settled items
        portfolio_items = ib.portfolio(CONFIG["active_account"])
        ibkr_syms = {item.contract.symbol for item in portfolio_items if item.position != 0}

        # Build a lookup of realizedPNL from portfolio for settled positions
        realized_pnl_map = {}
        for item in portfolio_items:
            sym = item.contract.symbol
            rpnl = getattr(item, 'realizedPNL', None)
            if rpnl is not None:
                try:
                    realized_pnl_map[sym] = float(rpnl)
                except (ValueError, TypeError):
                    pass

        for sym in list(open_trades.keys()):
            if sym not in ibkr_syms:
                trade = open_trades[sym]

                # ── CRITICAL: Skip PENDING orders that never filled ──
                # If status is PENDING, the buy order was placed but never
                # confirmed by IBKR. Not being in portfolio just means it
                # didn't fill — NOT that a position was "closed externally".
                if trade.get("status") == "PENDING":
                    clog("INFO", f"Removing unfilled order from tracker: {sym} (never filled)")
                    del open_trades[sym]
                    continue

                # Get real exit price from IBKR fills (most accurate)
                exit_price = None
                try:
                    import math as _math
                    fills = ib.fills()
                    sell_fills = [
                        f for f in fills
                        if f.contract.symbol == sym
                        and f.execution.side.upper() in ("SLD", "SELL")
                    ]
                    if sell_fills:
                        # Use the most recent sell fill
                        sell_fills.sort(key=lambda f: f.execution.time or datetime.min)
                        exit_price = float(sell_fills[-1].execution.price)
                except Exception:
                    pass

                # Fall back to deriving exit from realizedPNL if fills unavailable
                if exit_price is None and sym in realized_pnl_map:
                    rpnl = realized_pnl_map[sym]
                    qty  = trade["qty"]
                    if qty and not _math.isnan(rpnl) and rpnl != 0.0:
                        exit_price = round(trade["entry"] + rpnl / qty, 4)

                # If no fills AND no realizedPNL, this position was never
                # actually held — remove silently, don't log a fake trade.
                if exit_price is None:
                    clog("INFO", f"No fill evidence for {sym} — removing from tracker (not logging as trade)")
                    del open_trades[sym]
                    continue

                # Prefer realizedPNL from IBKR over recalculated value
                is_short = trade.get("direction", "LONG") == "SHORT"
                if sym in realized_pnl_map and realized_pnl_map[sym] != 0.0:
                    import math as _math
                    rpnl = realized_pnl_map[sym]
                    if not _math.isnan(rpnl):
                        pnl = rpnl
                    else:
                        # Direction-aware fallback
                        if is_short:
                            pnl = (trade["entry"] - exit_price) * trade["qty"]
                        else:
                            pnl = (exit_price - trade["entry"]) * trade["qty"]
                else:
                    # Direction-aware P&L: short profits when price falls
                    if is_short:
                        pnl = (trade["entry"] - exit_price) * trade["qty"]
                    else:
                        pnl = (exit_price - trade["entry"]) * trade["qty"]

                exit_reason = "stop_loss" if pnl < 0 else "take_profit"

                clog("TRADE", f"External close detected: {sym} | Exit ${exit_price:.2f} | P&L ${pnl:+.2f} | {exit_reason}")

                # Log to trade history
                log_trade(
                    trade=trade,
                    agent_outputs={},
                    regime=regime,
                    action="CLOSE",
                    outcome={
                        "exit_price": round(exit_price, 2),
                        "pnl":        round(pnl, 2),
                        "reason":     exit_reason,
                    }
                )

                # Add to dashboard recent trades
                dash["trades"].insert(0, {
                    "side":   "SELL",
                    "symbol": sym,
                    "price":  str(round(exit_price, 2)),
                    "time":   datetime.now().strftime("%H:%M:%S"),
                    "pnl":    round(pnl, 2),
                })

                # Update performance metrics
                from learning import get_performance_summary, load_trades as lt
                dash["all_trades"]  = lt()
                dash["performance"] = get_performance_summary(lt())

                # Remove from tracker
                del open_trades[sym]
                dash["positions"] = list(open_trades.values())

                if pnl >= 0:
                    from risk import record_win
                    record_win()
                else:
                    from risk import record_loss
                    record_loss(source="external")  # Don't extend pause for stop-loss hits

    except Exception as e:
        clog("ERROR", f"External close check error: {e}")


def check_options_positions():
    """
    Monitor open options positions for profit target, stop loss, and DTE exits.
    Called every scan cycle when options_enabled is True.
    """
    from orders import open_trades
    if not CONFIG.get("options_enabled"):
        return
    try:
        opts = {k: v for k, v in open_trades.items() if v.get("instrument") == "option"}
        if not opts:
            return
        to_exit = check_options_exits(opts, ib)
        for opt_key in to_exit:
            clog("TRADE", f"Closing options position: {opt_key}")
            execute_sell_option(ib, opt_key, reason="exit_condition")
            dash["positions"] = get_open_positions()
    except Exception as e:
        clog("ERROR", f"Options position check error: {e}")


# ── Main scan ──────────────────────────────────────────────────
scan_count = 0
last_sunday_review = None


def _check_kill():
    """Check if kill switch was activated. Abort scan if so.
    Note: flatten_all now executes immediately from the HTTP handler
    via emergency IB connection — this just stops the scan."""
    if dash.get("killed"):
        dash["scanning"] = False
        return True
    return False


def _process_close_queue():
    """Process individual position close requests (safe to call from main thread)."""
    close_queue = dash.pop("_close_queue", [])
    for sym in close_queue:
        try:
            from orders import close_position
            result = close_position(ib, sym)
            if result:
                clog("TRADE", f"✅ Close order placed for {sym}: {result}")
                dash["positions"] = get_open_positions()
            else:
                clog("ERROR", f"❌ Could not close {sym} — not found in portfolio")
        except Exception as e:
            clog("ERROR", f"❌ Close {sym} failed: {e}")


def run_scan():
    global scan_count, last_sunday_review

    if _check_kill():
        return

    if dash["paused"]:
        clog("INFO", "Bot is paused — skipping scan")
        return

    # ── Hot reload check ──────────────────────────────────────
    check_and_reload()

    scan_count += 1
    dash["scan_count"]  = scan_count
    dash["last_scan"]   = datetime.now().strftime("%H:%M:%S")
    dash["scanning"]    = True
    dash["session"]     = get_session()

    # Reset recent orders sidebar for fresh scan display (all_orders stays for Orders tab)
    dash["recent_orders"] = []
    dash["trades"]        = []
    dash["_scan_start"]   = datetime.now().isoformat()

    clog("SCAN", f"Scan #{scan_count} started | Session: {dash['session']}")

    # ── Reconnect if needed ─────────────────────────────────
    if not ib.isConnected():
        clog("ERROR", "IBKR disconnected — attempting reconnect...")
        if not connect_ibkr():
            clog("ERROR", "Reconnect failed — skipping scan")
            dash["scanning"] = False
            return

    # ── Account data ────────────────────────────────────────
    pv, pnl = get_account_data()
    dash["portfolio_value"] = pv
    dash["daily_pnl"]       = pnl
    clog("INFO", f"Portfolio: ${pv:,.2f} | DayP&L: ${pnl:+,.2f} | Positions: {len(get_open_positions())}")

    # ── Check options exits (profit target / stop loss / DTE) ────────
    check_options_positions()

    # ── Refresh position prices from IBKR (always live, even when 0 symbols score) ──
    update_positions_from_ibkr(ib)
    dash["positions"] = get_open_positions()

    # ── Regime detection ────────────────────────────────────
    clog("INFO", "Detecting market regime...")
    regime = get_market_regime(ib)
    dash["regime"] = regime
    clog("INFO", f"Regime: {regime['regime']} | VIX: {regime['vix']} | SPY: ${regime['spy_price']}")

    # ── Detect externally closed positions (stop loss / take profit) ──
    check_external_closes(regime)

    # ── Can we trade? ────────────────────────────────────────
    tradeable, reason = can_trade(pv, pnl, regime, get_open_positions())
    if not tradeable:
        clog("RISK", f"Trading suspended: {reason}")
        dash["claude_analysis"] = f"Trading suspended: {reason}"
        dash["scanning"] = False
        return

    # ── Dynamic universe ─────────────────────────────────────
    clog("SCAN", "Building dynamic universe from TradingView screener...")
    universe = get_dynamic_universe(ib, regime)
    # Add user favourites to universe
    favs = dash.get("favourites", [])
    if favs:
        before = len(universe)
        universe = list(set(universe + favs))
        new_count = len(universe) - before
        clog("INFO", f"Favourites: {len(favs)} tickers ({new_count} new additions to universe)")
    clog("INFO", f"Universe: {len(universe)} symbols to score")

    # ── TV PRE-FILTER — use free TradingView data to cut universe before yfinance ──
    # TV screener already gave us RSI, MACD, EMA, VWAP, rel_vol, Recommend.All
    # for every symbol. Use these to eliminate dead-weight BEFORE the expensive
    # yfinance multi-timeframe fetch (which is sequential to avoid thread-safety bugs).
    # Goal: 97 symbols → ~10-15 high-potential candidates → yfinance deep-scores only those.
    tv_cache = get_tv_signal_cache()
    if tv_cache:
        pre_universe = len(universe)
        ranked = []
        for sym in universe:
            tv = tv_cache.get(sym)
            if not tv:
                continue  # No TV data = skip (CORE_SYMBOLS without TV hits)

            close   = tv.get("tv_close")
            rec     = tv.get("tv_recommend")
            rel_vol = tv.get("tv_rel_vol")
            rsi     = tv.get("tv_rsi_1h")
            ema9    = tv.get("tv_ema9_1h")
            ema21   = tv.get("tv_ema21_1h")
            macd    = tv.get("tv_macd_1h")
            macd_s  = tv.get("tv_macd_sig_1h")
            change  = tv.get("tv_change")
            vwap    = tv.get("tv_vwap")

            # ── HARD KILLS — no edge, don't waste yfinance calls ──
            if close is None or close <= 0:
                continue
            if rec is None or abs(rec) < 0.1:
                continue  # Dead neutral — TV sees no directional signal
            if rel_vol is None or rel_vol < 1.0:
                continue  # Below-average volume — no institutional interest
            if rsi is not None and 42 < rsi < 58:
                continue  # RSI dead zone — no momentum either way
            if change is not None and abs(change) < 0.3:
                continue  # Flat — less than 0.3% move today

            # ── EMA ALIGNMENT CHECK — need some trend structure ──
            ema_aligned = False
            if ema9 is not None and ema21 is not None and ema9 != 0 and ema21 != 0:
                ema_spread = abs(ema9 - ema21) / max(ema9, ema21)
                if ema_spread > 0.001:  # EMAs at least 0.1% apart
                    ema_aligned = True

            # ── MACD THRUST CHECK — need some acceleration ──
            macd_thrust = False
            if macd is not None and macd_s is not None:
                if abs(macd - macd_s) > 0.01:  # MACD and signal not equal
                    macd_thrust = True

            # Need at least one of: EMA alignment OR MACD thrust
            if not ema_aligned and not macd_thrust:
                continue

            # ── RANK SCORE — strongest signal × most unusual volume ──
            # |Recommend.All| ranges 0-1, rel_vol typically 1-10+
            rank_score = abs(rec) * rel_vol
            # Bonus for VWAP confirmation (price on the right side of VWAP)
            if vwap and close and vwap > 0:
                if (rec > 0 and close > vwap) or (rec < 0 and close < vwap):
                    rank_score *= 1.3  # 30% bonus for VWAP alignment

            ranked.append((sym, rank_score))

        # Sort by rank score, take top 15
        ranked.sort(key=lambda x: x[1], reverse=True)
        universe = [sym for sym, _ in ranked[:15]]

        clog("SCAN", f"TV pre-filter: {pre_universe} → {len(universe)} symbols "
             f"(top by |signal| × rel_vol, VWAP-confirmed)")

    # ── Fetch news sentiment for universe ─────────────────────
    clog("SCAN", "Fetching news sentiment (Yahoo RSS + keyword scoring)...")
    try:
        news_sentiment = batch_news_sentiment(universe[:50])  # Top 50 to limit RSS calls
        dash["news_data"] = news_sentiment
        news_with_signal = sum(1 for v in news_sentiment.values() if v.get("news_score", 0) > 0)
        clog("INFO", f"News: {len(news_sentiment)} symbols scanned, {news_with_signal} with sentiment signal")
    except Exception as e:
        clog("ERROR", f"News sentiment error: {e}")
        news_sentiment = {}
        dash["news_data"] = {}

    # ── Score universe ────────────────────────────────────────
    clog("SCAN", "Scoring universe on 7 dimensions...")
    scored = score_universe(universe, regime.get("regime", "UNKNOWN"), news_data=news_sentiment)
    regime_name = regime.get('regime','UNKNOWN')
    regime_thresholds = {"BULL_TRENDING":28,"BEAR_TRENDING":25,"CHOPPY":22,"PANIC":99,"UNKNOWN":25}
    used_threshold = regime_thresholds.get(regime_name, CONFIG['min_score_to_trade'])
    clog("INFO", f"Scored: {len(scored)} symbols above threshold ({used_threshold}/50) [{regime_name}]")

    # ── Update existing position prices ──────────────────────
    update_position_prices(scored)

    # ── KILL CHECK + process close queue ─────────────────────────
    if _check_kill():
        return
    _process_close_queue()

    # ── Fetch supporting data ─────────────────────────────────
    news   = get_news_headlines()
    fx     = get_fx_snapshot()

    # ── Options flow scan ────────────────────────────────────
    # Scan dedicated optionable universe + top TV hits for unusual
    # volume, IV rank sweeps, earnings plays, and call/put skew.
    options_signals = []
    if CONFIG.get("options_enabled"):
        try:
            clog("ANALYSIS", "Scanning options flow (unusual vol, IV rank, earnings)...")
            top_scored_syms = [s["symbol"] for s in scored[:20]]
            options_signals = scan_options_universe(
                extra_symbols=top_scored_syms,
                regime=regime
            )
            clog("ANALYSIS", f"Options scan: {len(options_signals)} notable setups found")
        except Exception as _opts_err:
            clog("ERROR", f"Options scanner error: {_opts_err}")

    # ── KILL CHECK + process close queue ─────────────────────────
    if _check_kill():
        return
    _process_close_queue()

    # ── Run all 6 agents ──────────────────────────────────────
    clog("ANALYSIS", "Running 6-agent analysis pipeline...")
    open_pos = get_open_positions()

    decision = run_all_agents(
        signals=scored,
        regime=regime,
        news=news,
        fx_data=fx,
        open_positions=open_pos,
        portfolio_value=pv,
        daily_pnl=pnl,
        options_signals=options_signals
    )

    # ── Update dashboard with agent outputs ──────────────────
    dash["claude_analysis"]    = decision.get("claude_reasoning", decision.get("summary", ""))
    dash["agent_outputs"]      = decision.get("_agent_outputs", {})
    dash["last_agents_agreed"] = decision.get("agents_agreed", 0)

    # ── Build live agent conversation log ─────────────────────
    now_str = datetime.now().strftime("%H:%M:%S")
    agent_convo = []
    agent_names = [
        ("technical",   "Technical Analyst",  "Analyses price action, volume, and all 7 indicator dimensions"),
        ("macro",       "Macro Analyst",      "Assesses market regime, VIX, cross-asset dynamics, and news flow"),
        ("opportunity", "Opportunity Finder",  "Synthesises technical + macro to find the top 3 trades"),
        ("devils",      "Devil's Advocate",    "Argues against every proposed trade to protect capital"),
        ("risk",        "Risk Manager",        "Sizes positions and flags portfolio-level risk"),
    ]
    outputs = decision.get("_agent_outputs", {})
    for key, name, role_desc in agent_names:
        raw = outputs.get(key, "")
        if raw:
            agent_convo.append({
                "agent":   name,
                "role":    role_desc,
                "time":    now_str,
                "output":  raw[:800],
            })
    # Final Decision Maker summary
    agent_convo.append({
        "agent":  "Final Decision Maker",
        "role":   "Synthesises all 5 reports into executable trade instructions",
        "time":   now_str,
        "output": decision.get("claude_reasoning", decision.get("summary", "No reasoning provided")),
    })
    dash["agent_conversation"] = agent_convo

    clog("ANALYSIS", f"Agents agreed: {decision.get('agents_agreed',0)}/6 | {decision.get('summary','')}")

    # ── KILL CHECK — abort all trading if kill switch was hit during scan ──
    if dash.get("killed"):
        clog("RISK", "🚨 Kill switch active — skipping all trade execution")
        dash["scanning"] = False
        return

    # ── Go to cash if instructed ──────────────────────────────
    if decision.get("cash"):
        clog("RISK", "Agents instructed: go to cash — flattening all positions")
        flatten_all(ib)
        dash["scanning"] = False
        return

    # ── Execute sells ─────────────────────────────────────────
    from orders import open_trades as _open_trades
    for sym in decision.get("sells", []):
        clog("TRADE", f"Selling {sym} on agent signal")
        pos = next((p for p in open_pos if p["symbol"] == sym), None)
        exit_price = pos["current"] if pos else 0
        execute_sell(ib, sym, reason="Agent sell signal")
        dash["trades"].insert(0, {
            "side": "SELL", "symbol": sym,
            "price": str(exit_price),
            "time": datetime.now().strftime("%H:%M:%S")
        })
        # Log to trade history so it appears in Trade History tab
        if pos:
            pnl = (exit_price - pos["entry"]) * pos["qty"] if pos.get("direction","LONG") == "LONG" else (pos["entry"] - exit_price) * pos["qty"]
            from learning import log_trade as _log_trade
            _log_trade(
                trade=pos,
                agent_outputs=decision.get("_agent_outputs", {}),
                regime=regime,
                action="CLOSE",
                outcome={
                    "exit_price": round(exit_price, 4),
                    "pnl":        round(pnl, 2),
                    "reason":     "agent_sell",
                }
            )

    # ── Execute buys ──────────────────────────────────────────
    if dash.get("killed"):
        clog("RISK", "🚨 Kill switch active — skipping buy execution")
        dash["scanning"] = False
        return

    # Re-check can_trade() — scan + agents can take 15-30 min,
    # market may have closed or risk state may have changed since initial check
    tradeable_now, reason_now = can_trade(pv, pnl, regime, get_open_positions())
    if not tradeable_now:
        clog("RISK", f"Trading suspended before buy execution: {reason_now}")
        # Still complete the scan (positions updated, agents ran) — just skip buys
        dash["scanning"] = False
        return

    for buy in decision.get("buys", []):
        sym      = buy.get("symbol") if isinstance(buy, dict) else buy
        qty_hint = buy.get("qty")    if isinstance(buy, dict) else None
        reason   = buy.get("reasoning", "") if isinstance(buy, dict) else ""

        sig = next((s for s in scored if s["symbol"] == sym), None)

        # Agent recommended a symbol not in scored list — fetch its data directly
        if not sig:
            clog("INFO", f"{sym} not in scored list — fetching signal data for agent-recommended symbol")
            for _attempt in range(3):
                try:
                    raw = fetch_multi_timeframe(sym)
                    if raw:
                        raw["score"] = max(raw.get("score", 0), 30)
                        sig = raw
                        break
                    time.sleep(2)
                except Exception:
                    time.sleep(2)
            if not sig:
                clog("INFO", f"No signal data for {sym} after 3 attempts — skipping")
                continue

        clog("TRADE", f"Buying {sym} | Score={sig['score']}/50 | {reason[:80]}")

        # ── Options path ──────────────────────────────────────────────
        options_taken = False
        if (CONFIG.get("options_enabled") and
                sig["score"] >= CONFIG.get("options_min_score", 42)):
            direction = "LONG" if sig.get("direction", "LONG") == "LONG" else "SHORT"
            clog("TRADE", f"Score {sig['score']} qualifies for options — evaluating {sym} {direction}")
            try:
                contract_info = find_best_contract(sym, direction, pv, ib, regime)
                if contract_info:
                    opt_success = execute_buy_option(ib, contract_info, pv, reasoning=reason)
                    if opt_success:
                        options_taken = True
                        dash["trades"].insert(0, {
                            "side": f"BUY {contract_info['right']} OPT",
                            "symbol": f"{sym} ${contract_info['strike']:.0f} {contract_info['expiry_str']}",
                            "price": str(contract_info["mid"]),
                            "time": datetime.now().strftime("%H:%M:%S")
                        })
                        clog("TRADE", f"Options trade taken for {sym} — skipping stock buy")
                else:
                    clog("INFO", f"No suitable options contract for {sym} — falling through to stock buy")
            except Exception as _opt_err:
                clog("ERROR", f"Options evaluation failed for {sym}: {_opt_err} — falling through to stock buy")

        # ── Stock path (fallback or when options_enabled=False) ──────
        if not options_taken:
            success = execute_buy(
                ib=ib,
                symbol=sym,
                price=sig["price"],
                atr=sig["atr"],
                score=sig["score"],
                portfolio_value=pv,
                regime=regime,
                reasoning=reason
            )
            if success:
                dash["trades"].insert(0, {
                    "side": "BUY", "symbol": sym,
                    "price": str(sig["price"]),
                    "time": datetime.now().strftime("%H:%M:%S")
                })

    # ── Update dashboard positions ────────────────────────────
    dash["positions"] = get_open_positions()
    # Dedup dashboard trades list — collapse same symbol+side within 60 seconds
    # so partial fills of the same order show as one row, not many
    _seen_dash = {}
    _deduped = []
    for _t in dash["trades"]:
        _key = f"{_t.get('side','')}-{_t.get('symbol','')}-{_t.get('time','')[:5]}"
        if _key not in _seen_dash:
            _seen_dash[_key] = True
            _deduped.append(_t)
    dash["trades"] = _deduped[:200]

    # ── Sync order statuses from IBKR (fills, cancels, etc.) ──
    sync_orders_from_ibkr()

    # ── Update performance metrics ────────────────────────────
    all_trades = load_trades()
    dash["all_trades"]  = all_trades
    dash["all_orders"] = load_orders()
    # Recent orders sidebar: only orders from current scan
    _scan_start = dash.get("_scan_start")
    if _scan_start:
        dash["recent_orders"] = [o for o in dash["all_orders"] if (o.get("timestamp") or "") >= _scan_start]
    else:
        dash["recent_orders"] = dash["all_orders"]
    dash["performance"] = get_performance_summary(all_trades)

    # Total P&L = portfolio value - starting capital ($1M)
    dash["performance"]["total_pnl"] = round(dash.get("portfolio_value", 0) - get_effective_capital(), 2)

    # ── Equity history ────────────────────────────────────────
    dash["equity_history"].append({
        "date":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        "value": pv
    })
    if len(dash["equity_history"]) > 2000:
        dash["equity_history"] = dash["equity_history"][-2000:]
    save_equity_history(dash["equity_history"])

    # ── Weekly review (Sunday) ────────────────────────────────
    today = datetime.now().weekday()  # 6 = Sunday
    if today == 6 and last_sunday_review != datetime.now().date():
        clog("ANALYSIS", "Running weekly performance review...")
        review = run_weekly_review()
        clog("ANALYSIS", f"Weekly review: {review[:200]}...")
        last_sunday_review = datetime.now().date()

    dash["scanning"] = False
    clog("SCAN", f"Scan #{scan_count} complete")


# ── Scan countdown ─────────────────────────────────────────────
def countdown_tick():
    """Update next_scan_seconds every second for dashboard progress bar."""
    while True:
        time.sleep(1)
        if dash["next_scan_seconds"] > 0:
            dash["next_scan_seconds"] -= 1
        dash["scan_interval_seconds"] = get_scan_interval()


# ── Dashboard server ───────────────────────────────────────────
class DashHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
        elif self.path == "/api/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # Include current settings so dashboard form can show live values
            state = dict(dash)
            # Total P&L = NetLiquidation - effective capital (starting + deposits - withdrawals)
            eff_cap = get_effective_capital()
            state["effective_capital"] = eff_cap
            if state.get("performance"):
                state["performance"] = dict(state["performance"])
                state["performance"]["total_pnl"] = round(state.get("portfolio_value", 0) - eff_cap, 2)
            state["settings"] = {
                "risk_pct_per_trade":       CONFIG.get("risk_pct_per_trade", 0.04),
                "daily_loss_limit":         CONFIG.get("daily_loss_limit", 0.06),
                "max_positions":            CONFIG.get("max_positions", 12),
                "min_cash_reserve":         CONFIG.get("min_cash_reserve", 0.10),
                "max_single_position":      CONFIG.get("max_single_position", 0.15),
                "min_score_to_trade":       CONFIG.get("min_score_to_trade", 28),
                "high_conviction_score":    CONFIG.get("high_conviction_score", 38),
                "agents_required_to_agree": CONFIG.get("agents_required_to_agree", 3),
                "options_max_risk_pct":     CONFIG.get("options_max_risk_pct", 0.025),
                "options_max_ivr":          CONFIG.get("options_max_ivr", 65),
                "options_target_delta":     CONFIG.get("options_target_delta", 0.50),
            }
            self.wfile.write(json.dumps(state).encode())
        elif self.path == "/api/favourites":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"favourites": dash.get("favourites", [])}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/kill":
            dash["killed"] = True
            clog("RISK", "🚨 KILL SWITCH — executing FLATTEN ALL immediately...")
            # Execute immediately via emergency IB connection (separate clientId)
            try:
                flatten_all(ib)  # Uses emergency connection internally; ib is fallback
                clog("RISK", "🚨 FLATTEN ALL complete")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "detail": "All positions flattened"}).encode())
            except Exception as e:
                clog("ERROR", f"🚨 FLATTEN ALL failed: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/close":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            symbol = body.get("symbol", "").upper().strip()
            if not symbol:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No symbol provided"}).encode())
            else:
                # Execute immediately via emergency IB connection (no queuing!)
                clog("TRADE", f"📤 Closing {symbol} immediately...")
                try:
                    from orders import close_position
                    result = close_position(ib, symbol)
                    if result:
                        clog("TRADE", f"✅ {result}")
                        dash["positions"] = get_open_positions()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "detail": result}).encode())
                    else:
                        clog("ERROR", f"❌ {symbol} not found in portfolio")
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": False, "error": f"{symbol} not found in portfolio"}).encode())
                except Exception as e:
                    clog("ERROR", f"❌ Close {symbol} failed: {e}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/pause":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            dash["paused"] = body.get("paused", not dash["paused"])
            self.send_response(200)
            self.end_headers()
            clog("INFO", f"Bot {'paused' if dash['paused'] else 'resumed'} via dashboard")
        elif self.path == "/api/favourites":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            favs   = [s.upper().strip() for s in body.get("favourites", []) if s.strip()]
            dash["favourites"] = favs
            save_favourites(favs)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "favourites": favs}).encode())
            clog("INFO", f"Favourites updated: {favs}")
        elif self.path == "/api/scan":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            clog("INFO", "⚡ Force scan triggered via dashboard")
            threading.Thread(target=run_scan, daemon=True).start()
        elif self.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            # Apply settings directly to CONFIG (live update, no restart needed)
            applied = []
            for key, val in body.items():
                if key in CONFIG:
                    CONFIG[key] = val
                    applied.append(key)
            # Persist to disk so settings survive restarts
            save_settings_overrides(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "applied": applied}).encode())
            clog("INFO", f"⚙️ Settings applied & saved via dashboard: {', '.join(applied)}")
        elif self.path == "/api/capital-adjustment":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            amount = float(body.get("amount", 0))
            note = body.get("note", "")
            if amount != 0:
                record_capital_adjustment(amount, note)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "effective_capital": get_effective_capital()}).encode())
        elif self.path == "/api/restart":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            clog("INFO", "🔄 Restart requested via dashboard")
            # Restart in background — replace current process with fresh one
            import subprocess, sys, os
            def do_restart():
                import time
                time.sleep(1)
                ib.disconnect()
                os.execv(sys.executable, [sys.executable] + sys.argv)
            threading.Thread(target=do_restart, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # Suppress default HTTP logs


def start_dashboard():
    server = HTTPServer(("", CONFIG["dashboard_port"]), DashHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    clog("INFO", f"Dashboard live → http://localhost:{CONFIG['dashboard_port']}")


# ── Entry point ────────────────────────────────────────────────
def main():
    print(f"""
{Fore.YELLOW}
  ██████╗ ███████╗ ██████╗██╗███████╗███████╗██████╗
  ██╔══██╗██╔════╝██╔════╝██║██╔════╝██╔════╝██╔══██╗
  ██║  ██║█████╗  ██║     ██║█████╗  █████╗  ██████╔╝
  ██║  ██║██╔══╝  ██║     ██║██╔══╝  ██╔══╝  ██╔══██╗
  ██████╔╝███████╗╚██████╗██║██║     ███████╗██║  ██║
  ╚═════╝ ╚══════╝ ╚═════╝╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝
{Style.RESET_ALL}
  {Fore.WHITE}<>  Autonomous AI Trading System  v3.0{Style.RESET_ALL}
  {Fore.WHITE}Account : {Fore.YELLOW}{CONFIG['active_account']}{Style.RESET_ALL}
  {Fore.WHITE}Agents  : {Fore.YELLOW}6 Claude agents | {CONFIG['agents_required_to_agree']}/6 required to trade{Style.RESET_ALL}
  {Fore.WHITE}Risk    : {Fore.YELLOW}{int(CONFIG['risk_pct_per_trade']*100)}% per trade | {int(CONFIG['daily_loss_limit']*100)}% daily limit{Style.RESET_ALL}
  {Fore.WHITE}Dashboard: {Fore.CYAN}http://localhost:{CONFIG['dashboard_port']}{Style.RESET_ALL}
""")

    # API key check
    if CONFIG["anthropic_api_key"] == "YOUR_API_KEY_HERE":
        print(f"{Fore.RED}ERROR: Set ANTHROPIC_API_KEY environment variable.{Style.RESET_ALL}")
        print(f"  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    # Start dashboard
    start_dashboard()

    # Connect to IBKR
    if not connect_ibkr():
        print(f"{Fore.RED}ERROR: Could not connect to IBKR on port {CONFIG['ibkr_port']}.{Style.RESET_ALL}")
        print(f"  Make sure TWS is running with API enabled on port {CONFIG['ibkr_port']}")
        sys.exit(1)

    # Reset daily risk state — only once per calendar day
    pv, _ = get_account_data()
    today = datetime.now().date()
    if not hasattr(main, '_last_reset_date') or main._last_reset_date != today:
        reset_daily_state(pv)
        main._last_reset_date = today

    # Start countdown thread
    threading.Thread(target=countdown_tick, daemon=True).start()

    # Subscribe to live P&L
    subscribe_pnl()
    ib.sleep(3)  # Wait for first P&L update to arrive from IBKR

    # Register real-time order status listener
    ib.orderStatusEvent += _on_order_status_event

    # Backfill trade history from IBKR execution records
    ib.sleep(2)  # Ensure commissionReports are linked to fills before backfill
    backfill_trades_from_ibkr()
    sync_orders_from_ibkr()

    # Initialise hot reload file hashes
    _init_hashes()

    # Load persistent data
    load_settings_overrides()   # Apply saved dashboard settings on top of config.py defaults
    dash["favourites"]     = load_favourites()
    dash["equity_history"] = load_equity_history()
    dash["all_trades"]     = load_trades()
    dash["all_orders"]     = load_orders()
    dash["performance"]    = get_performance_summary(dash["all_trades"])

    dash["status"] = "running"
    run_scan()

    # Schedule subsequent scans dynamically based on session
    def scheduled_scan():
        run_scan()
        # Reschedule with fresh interval
        interval = get_scan_interval()
        dash["next_scan_seconds"] = interval
        schedule.clear("scan")
        schedule.every(interval).seconds.do(scheduled_scan).tag("scan")

    interval = get_scan_interval()
    dash["next_scan_seconds"] = interval
    schedule.every(interval).seconds.do(scheduled_scan).tag("scan")

    clog("INFO", f"<> Decifer running. Dashboard → http://localhost:{CONFIG['dashboard_port']}")
    clog("INFO", "Press Ctrl+C to stop.")

    try:
        while True:
            # ── Kill switch check (runs on main thread for ib_insync safety) ──
            _check_kill()

            # ── Process individual position close requests ──
            _process_close_queue()

            schedule.run_pending()
            ib.sleep(1)
    except KeyboardInterrupt:
        dash["status"] = "stopped"
        clog("INFO", "<> Decifer stopped.")
        ib.disconnect()


if __name__ == "__main__":
    main()
