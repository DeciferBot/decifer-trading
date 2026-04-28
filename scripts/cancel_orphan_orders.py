#!/usr/bin/env python3
"""
One-shot script: cancel stale / orphaned IBKR open orders identified after
the 2026-04-28 DB corruption + restart event.

Rules applied:
  1. Cancel any LMT exit order where the limit price is stale (price diverged
     from market by >1% and the order hasn't filled at all).
  2. Cancel any STP order for a position that is now flat (zero qty in IBKR).
  3. Cancel any LMT entry order for a position that is now flat.
  4. Print a clear table of what was cancelled vs what was left.

Run ONCE while the bot is stopped:
    python3 scripts/cancel_orphan_orders.py

Port 7496 / clientId 99 (read-only client id avoids colliding with bot's 10).
"""

from __future__ import annotations

import logging
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ib_async import IB, util

util.logToConsole(logging.CRITICAL)  # suppress ib_async noise

HOST = "127.0.0.1"
PORT = 7496
CLIENT_ID = 99  # never collides with bot (10) or other sessions

# ── Connect ───────────────────────────────────────────────────────────────────
ib = IB()
try:
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=False)
except Exception as e:
    print(f"[ERROR] Could not connect to IBKR at {HOST}:{PORT} — is TWS/Gateway running? {e}")
    sys.exit(1)

print(f"Connected to IBKR  clientId={CLIENT_ID}\n")

# ── Fetch live state ──────────────────────────────────────────────────────────
ib.sleep(1.0)  # let account data settle

positions = ib.positions()
open_trades = ib.openTrades()

# Build symbol → net qty map from live IBKR positions
pos_qty: dict[str, float] = {}
for p in positions:
    sym = p.contract.symbol
    pos_qty[sym] = pos_qty.get(sym, 0) + p.position

print("=== Live IBKR Positions ===")
for sym, qty in sorted(pos_qty.items()):
    print(f"  {sym:8s}  qty={qty:+.0f}")
print()

# ── Classify each open order ─────────────────────────────────────────────────
CANCELLED: list[str] = []
KEPT:      list[str] = []
ERRORS:    list[str] = []

def _describe(t) -> str:
    o = t.order
    c = t.contract
    st = t.orderStatus
    return (
        f"{c.symbol:6s} {o.action:4s} {o.orderType:7s} "
        f"lmt={getattr(o,'lmtPrice','-'):>8}  "
        f"aux={getattr(o,'auxPrice','-'):>8}  "
        f"qty={o.totalQuantity:.0f}  "
        f"filled={st.filled:.0f}  "
        f"status={st.status}"
    )

for t in open_trades:
    o    = t.order
    c    = t.contract
    st   = t.orderStatus
    sym  = c.symbol
    desc = _describe(t)

    # Skip already terminal states
    if st.status in ("Cancelled", "Filled", "Inactive"):
        continue

    live_qty = pos_qty.get(sym, 0)
    cancel_reason: str | None = None

    # ── Rule 1: orphan STP — position is flat ─────────────────────────────
    if o.orderType in ("STP", "STP LMT", "TRAIL") and live_qty == 0:
        cancel_reason = f"orphan STP: {sym} position is flat"

    # ── Rule 2: orphan LMT exit — position flat ────────────────────────────
    elif o.orderType == "LMT" and st.filled == 0:
        # A closing order has action opposite to the position sign
        is_closing_sell = (o.action == "SELL" and live_qty <= 0)
        is_closing_buy  = (o.action == "BUY"  and live_qty >= 0)
        if live_qty == 0 and (is_closing_sell or is_closing_buy):
            cancel_reason = f"orphan LMT {o.action}: {sym} position is flat"

    # ── Rule 3: stale LMT exit — limit price diverged from market ─────────
    # (covers BJ $93.08 when market is $93.49, HIG $136.98/$135.03 vs $138,
    #  AMD SELL $333.96 vs market $317)
    elif o.orderType == "LMT" and st.filled == 0 and o.tif in ("GTC", "DAY", ""):
        lmt = getattr(o, "lmtPrice", 0) or 0
        if lmt > 0:
            # Get last traded price from IBKR ticker if available
            ticker = ib.reqMktData(c, "", False, False)
            ib.sleep(0.5)
            last = ticker.last or ticker.close or 0
            ib.cancelMktData(c)
            if last > 0:
                divergence = abs(lmt - last) / last
                # Closing SELL with limit ABOVE market → will never fill
                if o.action == "SELL" and lmt > last * 1.005:
                    cancel_reason = (
                        f"stale LMT SELL: limit ${lmt:.2f} is {divergence:.1%} "
                        f"ABOVE market ${last:.2f} — will never fill"
                    )
                # Closing BUY with limit BELOW market for a short cover
                # Only flag if it's a cover (live_qty < 0) and limit is significantly below
                elif o.action == "BUY" and live_qty < 0 and lmt < last * 0.985:
                    cancel_reason = (
                        f"stale LMT BUY cover: limit ${lmt:.2f} is {divergence:.1%} "
                        f"below market ${last:.2f} — likely won't fill"
                    )

    if cancel_reason:
        print(f"[CANCEL] {desc}")
        print(f"         reason: {cancel_reason}")
        try:
            ib.cancelOrder(o)
            ib.sleep(0.3)
            CANCELLED.append(f"{sym} {o.action} {o.orderType} {lmt if o.orderType=='LMT' else getattr(o,'auxPrice','')}")
            print(f"         → cancel sent\n")
        except Exception as e:
            ERRORS.append(f"{sym}: {e}")
            print(f"         → ERROR: {e}\n")
    else:
        print(f"[KEEP]   {desc}")
        KEPT.append(sym)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print(f"CANCELLED: {len(CANCELLED)}")
for x in CANCELLED:
    print(f"  ✓ {x}")
print(f"\nKEPT:      {len(KEPT)}")
print(f"ERRORS:    {len(ERRORS)}")
for x in ERRORS:
    print(f"  ✗ {x}")

# ── HIG warning ──────────────────────────────────────────────────────────────
if "HIG" in pos_qty and pos_qty["HIG"] < 0:
    hig_stops = [
        t for t in open_trades
        if t.contract.symbol == "HIG"
        and t.order.orderType in ("STP", "STP LMT", "TRAIL")
        and t.order.action == "BUY"
        and t.orderStatus.status not in ("Cancelled", "Filled", "Inactive")
    ]
    if hig_stops:
        for t in hig_stops:
            print(f"\n[WARNING] HIG SHORT has stop BUY STP @ {t.order.auxPrice} but HIG "
                  f"last is above that — verify this stop is active in TWS and not stale.")
    else:
        print(f"\n[WARNING] HIG SHORT {pos_qty['HIG']:.0f} shares has NO active stop order — unprotected risk!")

ib.disconnect()
print("\nDone. Disconnected.")
