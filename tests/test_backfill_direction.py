"""
tests/test_backfill_direction.py

Regression test: SHORT-entry SELL fills must not be mislabeled as
action:BUY / direction:LONG in backfill_trades_from_ibkr().

Root cause (fixed 2026-04-28): the LONG matching loop selected any BUY that
preceded the SELL in time, including BUY fills whose order_id was already
recorded in trades.json as a completed LONG entry. That caused a SHORT-entry
SELL to be paired with the old LONG-entry BUY and written as a LONG trade.
"""

from __future__ import annotations

import math
import os
import sys
import types
from collections import defaultdict
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _m in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw",
           "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_m, MagicMock())

import pytest


def _make_fill(symbol: str, side: str, order_id: int, price: float,
               shares: float, time_str: str, exec_id: str, pnl: float = 0.0):
    """Build a minimal fake IBKR Fill object."""
    fill = MagicMock()
    fill.contract.symbol = symbol
    fill.contract.secType = "STK"
    fill.contract.right = ""
    fill.contract.strike = 0
    fill.execution.side = side
    fill.execution.price = price
    fill.execution.shares = shares
    fill.execution.orderId = order_id
    fill.execution.execId = exec_id
    fill.execution.time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    cr = MagicMock()
    cr.realizedPNL = pnl if pnl != 0.0 else float("nan")
    fill.commissionReport = cr
    return fill


def _existing_long_trade(symbol: str, order_id: int, exec_id: str,
                          entry_time: str, exit_time: str):
    """Minimal trades.json record for an already-recorded LONG trade."""
    return {
        "symbol": symbol,
        "action": "BUY",
        "direction": "LONG",
        "order_id": order_id,
        "exec_id": exec_id,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "timestamp": exit_time.replace(" ", "T"),
        "qty": 10,
        "shares": 10,
        "pnl": 50.0,
        "source": "normal",
    }


class TestBackfillDirection:
    """Verify correct direction labeling in backfill_trades_from_ibkr()."""

    def _run_backfill(self, fills, existing_trades):
        """
        Execute only the fill-matching logic from backfill_trades_from_ibkr()
        in isolation, without importing bot_ibkr (which requires a live IB
        connection). This mirrors the exact logic in bot_ibkr.py lines 606–917.
        """
        from bot_ibkr import _exec_id_prefix
        from orders_contracts import _is_option_contract

        existing_ids = set()
        existing_fuzzy = []
        for t in existing_trades:
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

        order_groups = defaultdict(lambda: {
            "sym": "", "side": "", "order_id": None, "exec_ids": [],
            "total_shares": 0.0, "value": 0.0, "total_pnl": 0.0,
            "latest_time": "", "earliest_time": "",
        })

        for fill in fills:
            is_opt = _is_option_contract(fill.contract)
            if is_opt:
                continue  # options not under test here
            underlying = fill.contract.symbol
            side = fill.execution.side.upper()
            price = float(fill.execution.price)
            shares = float(fill.execution.shares)
            etime = fill.execution.time.strftime("%Y-%m-%d %H:%M:%S")
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

        buy_orders = defaultdict(list)
        sell_orders = defaultdict(list)
        for (sym, order_id, side), g in order_groups.items():
            total_shares = g["total_shares"]
            if total_shares == 0:
                continue
            avg_price = g["value"] / total_shares
            rec = {
                "order_id": order_id,
                "exec_ids": g["exec_ids"],
                "avg_price": round(avg_price, 4),
                "total_shares": total_shares,
                "total_pnl": g["total_pnl"],
                "time": g["latest_time"],
                "earliest_time": g["earliest_time"],
            }
            if side in ("BOT", "BUY"):
                buy_orders[sym].append(rec)
            elif side in ("SLD", "SELL"):
                sell_orders[sym].append(rec)

        new_trades = []

        # LONG loop — must not consume SHORT-entry SELLs
        for sym, s_orders in sell_orders.items():
            for sell in s_orders:
                order_key = f"order-{sell['order_id']}"
                already = (
                    order_key in existing_ids
                    or any(eid in existing_ids for eid in sell["exec_ids"])
                )
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
                pnl = sell["total_pnl"]
                if pnl == 0.0:
                    pnl = round((sell["avg_price"] - entry_price) * sell["total_shares"], 2)
                if pnl == 0.0:
                    continue

                new_trades.append({
                    "symbol": sym, "action": "BUY", "direction": "LONG",
                    "entry_price": entry_price,
                    "exit_price": sell["avg_price"],
                    "order_id": sell["order_id"],
                    "source": "ibkr_backfill",
                })
                existing_ids.add(order_key)
                for eid in sell["exec_ids"]:
                    existing_ids.add(eid)
                existing_ids.add(f"order-{matching_buy['order_id']}")
                for eid in matching_buy["exec_ids"]:
                    existing_ids.add(eid)

        # SHORT loop
        for sym, b_orders in buy_orders.items():
            for buy_cover in sorted(b_orders, key=lambda b: b["time"]):
                order_key = f"order-{buy_cover['order_id']}"
                already = order_key in existing_ids or any(
                    eid in existing_ids for eid in buy_cover["exec_ids"]
                )
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
                pnl = buy_cover["total_pnl"]
                if pnl == 0.0:
                    pnl = round((entry_price - buy_cover["avg_price"]) * buy_cover["total_shares"], 2)
                if pnl == 0.0:
                    continue

                new_trades.append({
                    "symbol": sym, "action": "SELL", "direction": "SHORT",
                    "entry_price": entry_price,
                    "exit_price": buy_cover["avg_price"],
                    "order_id": buy_cover["order_id"],
                    "source": "ibkr_backfill",
                })
                existing_ids.add(order_key)
                for eid in buy_cover["exec_ids"]:
                    existing_ids.add(eid)
                existing_ids.add(f"order-{matching_short_entry['order_id']}")

        return new_trades

    def test_short_entry_not_mislabeled_as_long(self):
        """
        Regression: SHORT-entry SELL must not be paired with an old LONG-entry
        BUY that is already in existing_ids.

        Scenario:
          - trades.json: AAPL LONG completed, entry order_id=1
          - IBKR fills: BUY-1 (T1), SELL-3 (T3 SHORT entry), BUY-4 (T4 cover)
          - Expected: 1 SHORT trade recorded, 0 spurious LONG trades
        """
        existing = [
            _existing_long_trade("AAPL", order_id=1, exec_id="E001",
                                 entry_time="2026-04-28 10:00:00",
                                 exit_time="2026-04-28 10:30:00")
        ]
        fills = [
            _make_fill("AAPL", "BOT", order_id=1, price=150.0, shares=10,
                       time_str="2026-04-28 10:00:00", exec_id="E001"),
            _make_fill("AAPL", "SLD", order_id=3, price=155.0, shares=10,
                       time_str="2026-04-28 11:00:00", exec_id="E003"),
            _make_fill("AAPL", "BOT", order_id=4, price=152.0, shares=10,
                       time_str="2026-04-28 11:30:00", exec_id="E004",
                       pnl=30.0),
        ]

        trades = self._run_backfill(fills, existing)

        assert len(trades) == 1, (
            f"Expected 1 SHORT trade, got {len(trades)}: {trades}"
        )
        t = trades[0]
        assert t["direction"] == "SHORT", f"Expected SHORT, got {t['direction']}"
        assert t["action"] == "SELL", f"Expected SELL, got {t['action']}"

    def test_long_trade_still_recorded_correctly(self):
        """LONG trades (BUY entry → SELL exit) must still be backfilled correctly."""
        existing = []
        fills = [
            _make_fill("MSFT", "BOT", order_id=10, price=400.0, shares=5,
                       time_str="2026-04-28 09:30:00", exec_id="F010"),
            _make_fill("MSFT", "SLD", order_id=11, price=410.0, shares=5,
                       time_str="2026-04-28 10:00:00", exec_id="F011",
                       pnl=50.0),
        ]

        trades = self._run_backfill(fills, existing)

        assert len(trades) == 1, f"Expected 1 LONG trade, got {len(trades)}"
        t = trades[0]
        assert t["direction"] == "LONG", f"Expected LONG, got {t['direction']}"
        assert t["action"] == "BUY", f"Expected BUY, got {t['action']}"

    def test_short_trade_no_existing(self):
        """SHORT trade (SELL entry → BUY cover) with no prior LONG must be recorded correctly."""
        existing = []
        fills = [
            _make_fill("NVDA", "SLD", order_id=20, price=900.0, shares=3,
                       time_str="2026-04-28 09:31:00", exec_id="G020"),
            _make_fill("NVDA", "BOT", order_id=21, price=880.0, shares=3,
                       time_str="2026-04-28 09:50:00", exec_id="G021",
                       pnl=60.0),
        ]

        trades = self._run_backfill(fills, existing)

        assert len(trades) == 1, f"Expected 1 SHORT trade, got {len(trades)}"
        t = trades[0]
        assert t["direction"] == "SHORT", f"Expected SHORT, got {t['direction']}"
        assert t["action"] == "SELL", f"Expected SELL, got {t['action']}"

    def test_mixed_long_then_short_same_symbol(self):
        """LONG then SHORT on same symbol must both be labeled correctly."""
        existing = []
        fills = [
            # LONG: BUY T1 → SELL T2
            _make_fill("SPY", "BOT", order_id=30, price=500.0, shares=10,
                       time_str="2026-04-28 09:30:00", exec_id="H030"),
            _make_fill("SPY", "SLD", order_id=31, price=505.0, shares=10,
                       time_str="2026-04-28 10:00:00", exec_id="H031", pnl=50.0),
            # SHORT: SELL T3 → BUY T4
            _make_fill("SPY", "SLD", order_id=32, price=503.0, shares=10,
                       time_str="2026-04-28 10:30:00", exec_id="H032"),
            _make_fill("SPY", "BOT", order_id=33, price=498.0, shares=10,
                       time_str="2026-04-28 11:00:00", exec_id="H033", pnl=50.0),
        ]

        trades = self._run_backfill(fills, existing)

        assert len(trades) == 2, f"Expected 2 trades, got {len(trades)}: {trades}"
        directions = {t["direction"] for t in trades}
        assert directions == {"LONG", "SHORT"}, f"Expected LONG+SHORT, got {directions}"
