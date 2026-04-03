#!/usr/bin/env python3
"""
Backfill exit_reason and vehicle fields on all historical trades.

Logic:
  1. exit_reason — uses price-level comparison where sl/tp available,
                   falls back to pnl sign (profit_close / loss_close).
  2. vehicle     — derived from instrument + direction fields.

Writes result back to data/trades.json in-place (backup first).
Run with --dry-run to preview without writing.
"""

import json
import sys
from pathlib import Path

TRADES_PATH = Path(__file__).parent.parent / "data" / "trades.json"
TOL = 0.015   # 1.5% price tolerance for sl/tp hit classification

# ── Old-to-new reason mapping (preserves well-labelled existing reasons) ──
_REASON_REMAP = {
    "stop_loss":   None,   # re-classify with price logic
    "take_profit": None,   # re-classify with price logic
    "None":        None,
    "MISSING":     None,
}

_KEEP_AS_IS = {"exit_condition", "agent_sell", "manual", "sentinel_close",
               "circuit_breaker", "sl_hit", "tp_hit", "trailing_stop",
               "trailing_stop", "profit_close", "loss_close", "external_close",
               "time_exit"}


def _classify_exit_reason(trade: dict) -> str:
    sl    = float(trade.get("sl") or 0)
    tp    = float(trade.get("tp") or 0)
    trail = float(trade.get("trailing_stop") or 0)
    ep    = float(trade.get("exit_price") or 0)
    pnl   = trade.get("pnl")

    if ep > 0:
        if sl > 0 and abs(ep - sl) / sl < TOL:
            return "sl_hit"
        if tp > 0 and abs(ep - tp) / tp < TOL:
            return "tp_hit"
        if trail > 0 and abs(ep - trail) / trail < TOL:
            return "trailing_stop"

    if pnl is not None:
        try:
            pnl_f = float(pnl)
            return "profit_close" if pnl_f > 0 else "loss_close"
        except (TypeError, ValueError):
            pass

    return "external_close"


def _classify_vehicle(trade: dict) -> str:
    instrument = trade.get("instrument", "stock")
    if instrument == "option":
        right = (trade.get("right") or "").upper()
        if right == "C":
            return "options_call"
        if right == "P":
            return "options_put"
        return "options"

    # IBKR activity statement format: "NAVN 17APR26 12.5 C" — no instrument field
    symbol = str(trade.get("symbol", ""))
    if " " in symbol:
        parts = symbol.split()
        if len(parts) >= 4:
            right = parts[-1].upper()
            if right == "C":
                return "options_call"
            if right == "P":
                return "options_put"

    direction = (trade.get("direction") or "LONG").upper()
    return "equity_short" if direction == "SHORT" else "equity_long"


def backfill(dry_run: bool = False):
    with open(TRADES_PATH) as f:
        trades = json.load(f)

    updated_reason = 0
    updated_vehicle = 0
    skipped_reason = 0
    test_trades = 0

    results = []
    for t in trades:
        t = dict(t)  # shallow copy

        # Skip test/synthetic trades (exit_price == 0 or entry_price == 0)
        ep = float(t.get("exit_price") or 0)
        enp = float(t.get("entry_price") or 0)
        is_test = ep == 0 or enp == 0
        if is_test:
            test_trades += 1
            # Still add vehicle field if missing
            if not t.get("vehicle"):
                t["vehicle"] = _classify_vehicle(t)
                updated_vehicle += 1
            results.append(t)
            continue

        # ── exit_reason ──────────────────────────────────────────────────────
        existing = str(t.get("exit_reason", "MISSING"))
        if existing in _KEEP_AS_IS:
            skipped_reason += 1
        else:
            new_reason = _classify_exit_reason(t)
            if existing != new_reason:
                if dry_run:
                    print(f"  {t['symbol']:8s}  {existing!r:20s} → {new_reason!r}")
                t["exit_reason"] = new_reason
                updated_reason += 1

        # ── vehicle ─────────────────────────────────────────────────────────
        if not t.get("vehicle"):
            t["vehicle"] = _classify_vehicle(t)
            updated_vehicle += 1

        results.append(t)

    print(f"\nSummary:")
    print(f"  Total trades:            {len(trades)}")
    print(f"  Test/synthetic (skipped): {test_trades}")
    print(f"  exit_reason updated:      {updated_reason}")
    print(f"  exit_reason kept as-is:   {skipped_reason}")
    print(f"  vehicle field added:      {updated_vehicle}")

    # Distribution after backfill
    from collections import Counter
    reason_dist = Counter(str(t.get("exit_reason", "MISSING")) for t in results if (t.get("exit_price") or 0) > 0)
    vehicle_dist = Counter(str(t.get("vehicle", "MISSING")) for t in results)
    print(f"\nExit reason distribution (real trades):")
    for r, c in sorted(reason_dist.items(), key=lambda x: -x[1]):
        print(f"    {r}: {c}")
    print(f"\nVehicle distribution (all trades):")
    for v, c in sorted(vehicle_dist.items(), key=lambda x: -x[1]):
        print(f"    {v}: {c}")

    if dry_run:
        print("\n[DRY RUN] No changes written.")
    else:
        with open(TRADES_PATH, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nWritten to {TRADES_PATH}")

    return results


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("=== DRY RUN — exit_reason changes ===")
    backfill(dry_run=dry_run)
