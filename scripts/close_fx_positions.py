#!/usr/bin/env python3
"""Close all FX (CASH) positions via a dedicated IBKR connection."""

import sys
sys.path.insert(0, ".")

from ib_async import IB, MarketOrder

ACCOUNT = "DUP481326"
HOST = "127.0.0.1"
PORT = 7496
CLIENT_ID = 99

def main():
    ib = IB()
    print(f"Connecting to IBKR {HOST}:{PORT} ...")
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=10)
    print(f"Connected. Account: {ACCOUNT}")

    # Step 1: Request ALL open orders (across all client IDs)
    print("\n--- Step 1: Cancel ALL FX orders (all clients) ---")
    ib.reqAllOpenOrders()
    ib.sleep(2)

    cancelled = 0
    for t in ib.openTrades():
        if t.contract.secType == "CASH" and t.orderStatus.status in ("Submitted", "PreSubmitted", "Inactive"):
            pair = getattr(t.contract, "localSymbol", t.contract.symbol)
            print(f"  Cancelling #{t.order.orderId} {pair} {t.order.action} {t.order.totalQuantity} (status={t.orderStatus.status})")
            try:
                ib.cancelOrder(t.order)
                cancelled += 1
            except Exception as e:
                print(f"    Failed: {e}")

    if cancelled:
        print(f"\n  Sent {cancelled} cancel requests, waiting...")
        ib.sleep(5)

        # Verify cancellations
        still_open = 0
        for t in ib.openTrades():
            if t.contract.secType == "CASH" and t.orderStatus.status in ("Submitted", "PreSubmitted"):
                still_open += 1
                print(f"  Still open: #{t.order.orderId} {t.order.action} {t.order.totalQuantity}")
        if still_open:
            print(f"\n  WARNING: {still_open} orders still open. Waiting more...")
            ib.sleep(5)
    else:
        print("  No open FX orders found.")

    # Step 2: Global cancel as nuclear option
    print("\n--- Step 2: Requesting global cancel for safety ---")
    ib.reqGlobalCancel()
    ib.sleep(3)

    # Step 3: Find FX positions
    print("\n--- Step 3: Finding FX positions ---")
    positions = ib.positions()
    fx_items = [p for p in positions if p.contract.secType == "CASH" and p.position != 0]

    if not fx_items:
        portfolio = ib.portfolio(ACCOUNT)
        fx_items = [item for item in portfolio if item.contract.secType == "CASH" and item.position != 0]

    if not fx_items:
        print("No FX positions found.")
        _clean_positions_json()
        ib.disconnect()
        return

    for item in fx_items:
        pair = item.contract.symbol + item.contract.currency
        print(f"  {pair}: position={item.position}")

    # Step 4: Close each position
    print("\n--- Step 4: Closing FX positions ---")
    for item in fx_items:
        c = item.contract
        pair = c.symbol + c.currency
        pos = item.position
        action = "SELL" if pos > 0 else "BUY"
        qty = abs(int(pos))

        print(f"\n  Closing {pair}: {action} {qty}")
        ib.qualifyContracts(c)
        order = MarketOrder(action, qty, account=ACCOUNT, tif="GTC")
        trade = ib.placeOrder(c, order)

        # Wait up to 10s for fill
        for _ in range(10):
            ib.sleep(1)
            if trade.orderStatus.status == "Filled":
                break

        status = trade.orderStatus.status
        filled = trade.orderStatus.filled
        print(f"  → {status} (filled={filled})")

    # Step 5: Final report
    print("\n" + "="*50)
    print("Final positions check:")
    ib.sleep(2)
    positions = ib.positions()
    any_fx = False
    for p in positions:
        if p.contract.secType == "CASH" and p.position != 0:
            pair = p.contract.symbol + p.contract.currency
            print(f"  {pair}: {p.position} (STILL OPEN)")
            any_fx = True
    if not any_fx:
        print("  All FX positions closed.")

    _clean_positions_json()
    ib.disconnect()
    print("\nDone.")


def _clean_positions_json():
    try:
        import json
        with open("data/positions.json") as f:
            pos_data = json.load(f)
        fx_keys = [k for k, v in pos_data.items() if v.get("instrument") == "fx"]
        if fx_keys:
            for k in fx_keys:
                del pos_data[k]
                print(f"  Removed {k} from positions.json")
            with open("data/positions.json", "w") as f:
                json.dump(pos_data, f, indent=2, default=str)
    except Exception as e:
        print(f"  Warning: could not clean positions.json: {e}")


if __name__ == "__main__":
    main()
