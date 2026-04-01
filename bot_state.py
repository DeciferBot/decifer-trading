#!/usr/bin/env python3
"""
bot_state.py — Shared mutable state for the Decifer trading bot.

All other bot_* modules import from here.  This module imports nothing
from other bot_* modules, keeping the dependency graph a clean DAG.

    config  (leaf)
        ↑
    bot_state  (this file — imports only config + stdlib + 3rd-party)
        ↑
    bot_ibkr / bot_account / bot_trading / bot_sentinel / bot_dashboard
        ↑
    bot  (orchestrator)
"""

import logging
import os
import threading
from datetime import datetime

from colorama import Fore, Style
from ib_async import IB

from config import CONFIG

# ── Logger ───────────────────────────────────────────────────────────────────
log = logging.getLogger("decifer.bot")

# ── Colours for terminal ─────────────────────────────────────────────────────
COLORS = {
    "TRADE":    Fore.YELLOW,
    "SIGNAL":   Fore.GREEN,
    "ANALYSIS": Fore.CYAN,
    "ERROR":    Fore.RED,
    "INFO":     Fore.WHITE,
    "RISK":     Fore.RED,
    "SCAN":     Fore.MAGENTA,
}

# ── Live dashboard state ─────────────────────────────────────────────────────
dash = {
    "status":                "starting",
    "account":               CONFIG.get("active_account", ""),
    "portfolio_value":       0.0,
    "daily_pnl":             0.0,
    "session":               "UNKNOWN",
    "scan_count":            0,
    "last_scan":             None,
    "scanning":              False,
    "next_scan_seconds":     0,
    "scan_interval_seconds": 300,
    "regime":                {"regime": "UNKNOWN", "vix": 0, "spy_price": 0},
    "positions":             [],
    "trades":                [],
    "all_trades":            [],
    "logs":                  [],
    "claude_analysis":       "Waiting for first scan...",
    "agent_outputs":         {},
    "agent_conversation":    [],
    "last_agents_agreed":    None,
    "agents_required":       CONFIG["agents_required_to_agree"],
    "performance":           {},
    "equity_history":        [],
    "paused":                False,
    "killed":                False,
    "ibkr_disconnected":     False,
    "favourites":            [],
    "hot_reload_count":      0,
    "last_reload":           None,
    "last_reload_files":     [],
    "news_data":             {},
    "all_orders":            [],
    "recent_orders":         [],
    # ── News Sentinel state ────────────────────────────────────────────────
    "sentinel_status":           "stopped",
    "sentinel_stats":            {},
    "sentinel_triggers":         [],
    "sentinel_themes":           {},
    # ── Catalyst Sentinel state ────────────────────────────────────────────
    "catalyst_triggers":         [],
    "catalyst_sentinel_stats":   {},
}

# ── Persistent file paths ─────────────────────────────────────────────────────
EQUITY_FILE  = "equity_history.json"
PROMPTS_FILE = "prompt_versions.json"

# ── IBKR connection object (single instance shared by all modules) ────────────
ib = IB()

# ── Reconnect / heartbeat state ───────────────────────────────────────────────
_reconnect_lock:        threading.Lock          = threading.Lock()
_reconnecting:          bool                    = False
_subscription_registry: dict                    = {}
_heartbeat_thread:      threading.Thread | None = None
_pnl_subscription                               = None

# ── Trading loop counters ─────────────────────────────────────────────────────
scan_count         = 0
last_sunday_review = None

# ── News Sentinel state ───────────────────────────────────────────────────────
_sentinel                  = None
_sentinel_trades_this_hour = 0
_sentinel_hour_start       = None

# ── Catalyst Sentinel state ───────────────────────────────────────────────────
_catalyst_sentinel     = None
_catalyst_trades_today = 0
_catalyst_trade_date   = ""


# ── Utility: coloured terminal log ───────────────────────────────────────────
def clog(type_: str, msg: str):
    """Coloured terminal log + dashboard log."""
    color = COLORS.get(type_, Fore.WHITE)
    print(f"{color}[{type_}]{Style.RESET_ALL}  {msg}")
    log.info(f"[{type_}] {msg}")
    dash["logs"].insert(0, {
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": type_,
        "msg":  msg,
    })
    if len(dash["logs"]) > 500:
        dash["logs"] = dash["logs"][:500]
