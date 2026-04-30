#!/usr/bin/env python3
"""
One-shot script: close phantom open positions in data/trades.json.

Compares net-open symbols in trades.json against live IBKR positions and
writes synthetic CLOSE records for any excess OPENs that no longer exist
in IBKR. Does NOT touch training_records.jsonl or trade_events.jsonl.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ib_insync import IB
from learning import _save_trades, load_trades

UTC = timezone.utc
RECONCILE_REASON = "reconciliation_backfill"
TEST_ARTIFACTS = {"CHEAP", "EXPENSIVE"}


def _fetch_ibkr_positions() -> dict:
    """Return {symbol: {qty, avgCost, secType}} from live IBKR."""
    ib = IB()
    ib.connect("127.0.0.1", 7496, clientId=98, timeout=8, readonly=True)
    result = {}
    for p in ib.positions():
        sym = p.contract.symbol
        result[sym] = {
            "qty": abs(p.position),
            "avgCost": p.avgCost,
            "secType": p.contract.secType,
        }
    ib.disconnect()
    return result


def _fetch_alpaca_price(symbol: str) -> float | None:
    """Fetch latest trade price from Alpaca. Returns None on failure."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        from config import CONFIG

        client = StockHistoricalDataClient(
            api_key=CONFIG.get("alpaca_key") or os.getenv("ALPACA_API_KEY"),
            secret_key=CONFIG.get("alpaca_secret") or os.getenv("ALPACA_SECRET_KEY"),
        )
        req = StockLatestTradeRequest(symbol_or_symbols=symbol)
        resp = client.get_stock_latest_trade(req)
        if symbol in resp:
            return float(resp[symbol].price)
    except Exception:
        pass
    return None


def _compute_net_open(trades: list) -> dict:
    """Return {symbol: [(open_record, ...), ...]} for symbols with net open > 0."""
    opens: dict[str, list] = defaultdict(list)
    closes: dict[str, int] = defaultdict(int)
    for t in trades:
        sym = t.get("symbol", "")
        action = t.get("action", "")
        if action in ("OPEN", "BUY"):
            opens[sym].append(t)
        elif action in ("CLOSE", "SELL"):
            closes[sym] += 1
    net: dict[str, list] = {}
    for sym, open_list in opens.items():
        excess = len(open_list) - closes.get(sym, 0)
        if excess > 0:
            # Sort ascending by entry_time — oldest phantoms get closed first
            sorted_opens = sorted(
                open_list,
                key=lambda r: r.get("entry_time") or r.get("timestamp") or "",
            )
            net[sym] = sorted_opens[:excess]
    return net


def _build_close(open_rec: dict, exit_price: float, now_iso: str) -> dict:
    """Build a synthetic CLOSE record mirroring the given OPEN record."""
    entry_price = open_rec.get("entry_price") or exit_price
    qty = open_rec.get("qty") or open_rec.get("shares") or 0
    direction = open_rec.get("direction", "LONG")

    raw_pnl = (exit_price - entry_price) * qty
    pnl = raw_pnl if direction == "LONG" else -raw_pnl
    cost_basis = entry_price * qty
    pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0.0

    entry_time = open_rec.get("entry_time") or open_rec.get("timestamp") or now_iso
    try:
        entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        hold_minutes = int((datetime.now(UTC) - entry_dt).total_seconds() / 60)
    except Exception:
        hold_minutes = 0

    close_rec = dict(open_rec)
    close_rec.update(
        {
            "action": "CLOSE",
            "timestamp": now_iso,
            "exit_time": now_iso,
            "exit_price": exit_price,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "exit_reason": RECONCILE_REASON,
            "hold_minutes": hold_minutes,
        }
    )
    return close_rec


def main() -> None:
    print("=== reconcile_trades_json.py ===\n")

    print("Connecting to IBKR...")
    ibkr = _fetch_ibkr_positions()
    print(f"Live IBKR positions: {len(ibkr)}")
    for sym, info in ibkr.items():
        print(f"  {sym:8s} {info['secType']:3s} qty={info['qty']:.0f} avgCost={info['avgCost']:.2f}")
    print()

    trades = load_trades()
    print(f"trades.json records: {len(trades)}")

    net_open = _compute_net_open(trades)
    print(f"Symbols with net open positions in records: {len(net_open)}\n")

    now_iso = datetime.now(UTC).isoformat()
    synthetic_closes: list[dict] = []
    report_rows: list[tuple] = []

    for sym, phantom_opens in sorted(net_open.items()):
        ibkr_count = 1 if sym in ibkr else 0
        closes_needed = len(phantom_opens)  # already trimmed to excess in _compute_net_open

        if closes_needed == 0:
            continue

        # Determine exit price — Alpaca is primary; ibkr_avgCost only as fallback
        # when secType matches (avoids using OPT avgCost for phantom stock records).
        is_test = sym in TEST_ARTIFACTS
        if is_test:
            exit_price = phantom_opens[0].get("entry_price") or 100.0
            price_source = "test_artifact(zero_pnl)"
        else:
            alpaca_price = _fetch_alpaca_price(sym)
            if alpaca_price:
                exit_price = alpaca_price
                price_source = "alpaca_latest"
            elif sym in ibkr and ibkr[sym]["secType"] == "STK":
                exit_price = ibkr[sym]["avgCost"]
                price_source = "ibkr_avgCost"
            else:
                exit_price = phantom_opens[0].get("entry_price") or 0.0
                price_source = "entry_price_fallback(zero_pnl)"

        for rec in phantom_opens:
            close_rec = _build_close(rec, exit_price, now_iso)
            synthetic_closes.append(close_rec)
            entry_px = rec.get("entry_price") or exit_price
            pnl = close_rec["pnl"]
            report_rows.append((sym, entry_px, exit_price, pnl, price_source))

    if not synthetic_closes:
        print("Nothing to reconcile — trades.json is already in sync with IBKR.")
        return

    # Print report before writing
    print(f"{'Symbol':<10} {'Entry':>8} {'Exit':>8} {'PnL':>10}  Source")
    print("-" * 55)
    for sym, entry, exit_, pnl, src in report_rows:
        print(f"{sym:<10} {entry:>8.2f} {exit_:>8.2f} {pnl:>10.2f}  {src}")
    print(f"\nTotal synthetic CLOSEs to write: {len(synthetic_closes)}")

    confirm = input("\nWrite these CLOSE records to data/trades.json? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted — no changes written.")
        return

    trades.extend(synthetic_closes)
    _save_trades(trades)
    print(f"\nDone. {len(synthetic_closes)} CLOSE records written to data/trades.json.")

    # Verify
    reloaded = load_trades()
    net_after = _compute_net_open(reloaded)
    print(f"Net-open symbols after reconciliation: {len(net_after)}")
    if net_after:
        for sym, recs in sorted(net_after.items()):
            ibkr_present = "✓ in IBKR" if sym in ibkr else "✗ NOT in IBKR"
            print(f"  {sym}: {len(recs)} open — {ibkr_present}")


if __name__ == "__main__":
    main()
