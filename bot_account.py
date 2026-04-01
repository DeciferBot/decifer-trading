#!/usr/bin/env python3
"""
bot_account.py — Account data fetching for the Decifer trading bot.

Covers: P&L subscription, portfolio value snapshot, account details,
news headline aggregation, FX snapshot, and equity history persistence.
"""

import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor

from config import CONFIG
import bot_state
from bot_state import dash, clog, EQUITY_FILE

log = logging.getLogger("decifer.bot")


# ── Equity history persistence ────────────────────────────────────────────────

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


# ── Account data ──────────────────────────────────────────────────────────────

def get_account_data():
    """Fetch portfolio value and daily P&L from IBKR."""
    ib = bot_state.ib
    try:
        vals = ib.accountValues(CONFIG["active_account"])
        pv = 0.0
        for v in vals:
            if v.tag == "NetLiquidation" and v.currency == "USD":
                pv = float(v.value)
                break
        pnl = 0.0
        if bot_state._pnl_subscription is not None:
            daily = bot_state._pnl_subscription.dailyPnL
            if daily is not None and not math.isnan(daily):
                pnl = round(float(daily), 2)
        return pv, pnl
    except Exception as e:
        clog("ERROR", f"Account data error: {e}")
        return dash["portfolio_value"], dash["daily_pnl"]


def get_account_details():
    """Fetch extended account metrics from IBKR for dashboard KPI row."""
    ib = bot_state.ib
    details = {}
    try:
        vals = ib.accountValues(CONFIG["active_account"])
        tag_map = {
            "AvailableFunds":     "available_cash",
            "BuyingPower":        "buying_power",
            "GrossPositionValue": "gross_position_value",
            "MaintMarginReq":     "margin_used",
            "ExcessLiquidity":    "excess_liquidity",
            "TotalCashValue":     "total_cash",
            "UnrealizedPnL":      "unrealized_pnl",
            "RealizedPnL":        "realized_pnl",
            "NetLiquidation":     "net_liquidation",
        }
        for v in vals:
            if v.tag in tag_map and v.currency == "USD":
                try:
                    details[tag_map[v.tag]] = round(float(v.value), 2)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        clog("ERROR", f"Account details error: {e}")
    return details


# ── News & FX helpers ─────────────────────────────────────────────────────────

def get_news_headlines() -> list:
    """Return recent news headlines from cached scan data for agents."""
    try:
        all_headlines = []
        for sym, ndata in dash.get("news_data", {}).items():
            for h in ndata.get("headlines", [])[:3]:
                all_headlines.append(f"[{sym}] {h}")
        return all_headlines[:20]
    except Exception:
        return []


def get_fx_snapshot() -> dict:
    """Get snapshot of key FX pairs."""
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
