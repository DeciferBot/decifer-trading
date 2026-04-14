# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  pdt_rule.py                                ║
# ║   Pattern Day Trader rule enforcement                        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
PDT day-trade counting extracted from risk.py.

Stateless — reads from IBKR account values or falls back to local
orders.json. No shared globals; no rebinding required.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

import pytz

from config import CONFIG

log = logging.getLogger("decifer.risk")

EST = pytz.timezone("America/New_York")


def _count_day_trades_remaining_local() -> int:
    """
    Count day trades used in last 5 trading days from orders.json.
    A day trade = same symbol with role 'open' and role 'close' on the same EST calendar date.
    Returns remaining = max_day_trades - used (floored at 0).
    """
    max_dt = CONFIG.get("pdt", {}).get("max_day_trades", 3)
    orders_path = os.path.join(os.path.dirname(__file__), "data", "orders.json")
    try:
        with open(orders_path) as f:
            orders = json.load(f)
    except Exception:
        return max_dt  # Can't read — don't block on uncertainty

    cutoff = datetime.now(EST) - timedelta(days=7)
    opens_by_date: defaultdict = defaultdict(set)
    closes_by_date: defaultdict = defaultdict(set)

    for o in orders:
        if o.get("status") != "FILLED":
            continue
        ts_str = o.get("timestamp", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str).astimezone(EST)
        except Exception:
            continue
        if ts < cutoff:
            continue
        date_key = ts.date()
        symbol = o.get("symbol", "")
        if o.get("role") == "open":
            opens_by_date[date_key].add(symbol)
        elif o.get("role") == "close":
            closes_by_date[date_key].add(symbol)

    # A day trade occurred on any date where the same symbol was both opened and closed
    day_trade_dates = sorted(d for d in opens_by_date if opens_by_date[d] & closes_by_date[d])
    used = len(day_trade_dates[-5:]) if day_trade_dates else 0
    return max(0, max_dt - used)


def _get_day_trades_remaining(ib, account: str) -> int | None:
    """
    Return how many day trades remain in the rolling 5-day window.
    Primary: IBKR's DayTradesRemaining account value tag.
    Fallback: local count from orders.json.
    Returns None only if count is genuinely indeterminate.
    """
    if ib is not None:
        try:
            vals = ib.accountValues(account)
            for v in vals:
                if v.tag == "DayTradesRemaining":
                    val = v.value
                    if val not in ("", "Unlimited", None):
                        return int(float(val))
        except Exception as e:
            log.warning(f"Could not fetch DayTradesRemaining from IBKR: {e}")
    return _count_day_trades_remaining_local()
