#!/usr/bin/env python3
"""
One-shot backfill: write missing/corrected POSITION_CLOSED events.

Run with DRY_RUN=True (default) to preview, then DRY_RUN=False to commit.

Covers:
  1. Zero-price corrections (SPY, SVXY, DDOG) — append new POSITION_CLOSED
     with correct IBKR-confirmed exit price. Old records have exit_reason=
     "manual_repair" and are already filtered out of the dashboard; new records
     will appear with accurate prices.

  2. Orphaned ORDER_FILLED records (no POSITION_CLOSED) for positions whose
     close is confirmed by IBKR screenshot or by a mismatched-trade_id close
     event already in the log.

Skipped (still open — opened after-hours 16:19 ET today):
  AAPL_20260430_161908, AMAT_20260430_161923, AMD_20260430_161913,
  GS_20260430_161937, OXY_20260430_161930

Skipped (ambiguous — need Amit IBKR confirmation):
  ALAB (2nd, 14:57), AMZN_C_260.0, CFG, SNX, COIN, COP
"""

import json
import os
import sys
from datetime import datetime, timezone

DRY_RUN = False  # Set to False to actually write

TRADE_EVENTS_LOG = os.path.join(os.path.dirname(__file__), "..", "data", "trade_events.jsonl")

UTC = timezone.utc


def load_events():
    events = []
    with open(TRADE_EVENTS_LOG, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                events.append(json.loads(stripped))
    return events


def already_has_close(events, trade_id):
    return any(
        e["event"] == "POSITION_CLOSED" and e.get("trade_id") == trade_id
        for e in events
    )


def write_close(trade_id, symbol, exit_price, pnl, exit_reason, hold_minutes=0, note=""):
    rec = {
        "ts": datetime.now(UTC).isoformat(),
        "event": "POSITION_CLOSED",
        "trade_id": trade_id,
        "symbol": symbol,
        "exit_price": round(exit_price, 4),
        "pnl": round(pnl, 2),
        "exit_reason": exit_reason,
        "hold_minutes": hold_minutes,
    }
    tag = f"[DRY_RUN] " if DRY_RUN else ""
    print(f"  {tag}WRITE: {symbol} tid={trade_id[:55]}")
    print(f"         exit_price={exit_price}, pnl={pnl:.2f}, reason={exit_reason}{', note=' + note if note else ''}")
    if not DRY_RUN:
        with open(TRADE_EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())


def pnl_stock(entry, exit_p, qty):
    return round((exit_p - entry) * qty, 2)


def pnl_option(entry, exit_p, qty):
    return round((exit_p - entry) * qty * 100, 2)


def main():
    events = load_events()

    print(f"=== Backfill POSITION_CLOSED (DRY_RUN={DRY_RUN}) ===")
    print(f"Loaded {len(events)} events from {TRADE_EVENTS_LOG}")
    print()

    # ──────────────────────────────────────────────────────────────────────────
    # Part 1: Zero-price corrections
    # These already have POSITION_CLOSED with exit_reason="manual_repair" and
    # exit_price=0. Append new records with correct prices — dashboard will show
    # the new ones (the old ones are filtered by exit_reason="manual_repair").
    # We append regardless of existing close because the old one has exit_price=0.
    # ──────────────────────────────────────────────────────────────────────────
    print("── Part 1: Zero-price corrections (IBKR-confirmed prices) ──")

    zero_price_fixes = [
        # (trade_id, symbol, entry_fill, qty, exit_price_ibkr, is_option)
        ("SPY_20260430_130004_675802",  "SPY",  715.202, 83,   714.20,  False),
        ("SVXY_20260430_130018_575743", "SVXY", 50.805, 1170,  50.625,  False),
        ("DDOG_20260430_131903_282256", "DDOG", 134.0793, 108, 132.50,  False),
    ]

    for trade_id, symbol, entry, qty, exit_p, is_opt in zero_price_fixes:
        # Check if a non-manual_repair POSITION_CLOSED already exists
        already_corrected = any(
            e["event"] == "POSITION_CLOSED"
            and e.get("trade_id") == trade_id
            and e.get("exit_reason") != "manual_repair"
            for e in events
        )
        if already_corrected:
            print(f"  SKIP (correction already written): {symbol} {trade_id[:50]}")
            continue
        calc_pnl = pnl_option(entry, exit_p, qty) if is_opt else pnl_stock(entry, exit_p, qty)
        write_close(
            trade_id, symbol,
            exit_price=exit_p,
            pnl=calc_pnl,
            exit_reason="external_close",
            hold_minutes=0,
            note="IBKR-confirmed price, correcting prior manual_repair zero-price record",
        )

    print()

    # ──────────────────────────────────────────────────────────────────────────
    # Part 2: Orphaned ORDER_FILLED — write missing POSITION_CLOSED
    # ──────────────────────────────────────────────────────────────────────────
    print("── Part 2: Orphaned ORDER_FILLED — confirmed closed positions ──")

    confirmed_closes = [
        # (trade_id, symbol, entry_fill, fill_qty, exit_price, is_option, hold_min, source)
        (
            "NVDA_20260430_135423_397265", "NVDA",
            204.295, 280, 200.21, False, 60,
            "IBKR_screenshot",
        ),
        (
            "ALAB_20260430_135432_680829", "ALAB",
            188.7786, 301, 185.00, False, 60,
            "IBKR_screenshot",
        ),
        (
            "JPM_20260430_134634_713088", "JPM",
            310.8753, 188, 312.55, False, 55,
            "IBKR_screenshot (two JPM trades today; this is the first)",
        ),
        (
            "AMZN_C_267.5_2026-05-15_20260430_133517_673723",
            "AMZN_C_267.5_2026-05-15",
            6.6554, 35, 3.56, True, 60,
            "IBKR_screenshot",
        ),
        (
            "MU_20260429_090656_022940", "MU",
            524.6589, 112, 523.53, False, 1480,
            "mismatched_close_event(MU_20260430_124029_205861 in log)",
        ),
        (
            "QQQ_20260430_135447_949134", "QQQ",
            659.6614, 88, 662.315, False, 70,
            "mismatched_close_event(QQQ_20260430_145727_644068 in log)",
        ),
        (
            "USO_20260430_134614_455875", "USO",
            147.4789, 395, 147.95, False, 65,
            "mismatched_close_event(USO_20260430_145051_171341 in log)",
        ),
    ]

    for trade_id, symbol, entry, qty, exit_p, is_opt, hold_m, source in confirmed_closes:
        if already_has_close(events, trade_id):
            print(f"  SKIP (already closed): {symbol} {trade_id[:50]}")
            continue
        calc_pnl = pnl_option(entry, exit_p, qty) if is_opt else pnl_stock(entry, exit_p, qty)
        write_close(
            trade_id, symbol,
            exit_price=exit_p,
            pnl=calc_pnl,
            exit_reason="external_close",
            hold_minutes=hold_m,
            note=f"source={source}",
        )

    print()

    # ──────────────────────────────────────────────────────────────────────────
    # Part 3: Skipped — report for Amit to verify in IBKR
    # ──────────────────────────────────────────────────────────────────────────
    print("── Part 3: Skipped — verify these in IBKR before backfilling ──")

    skipped = [
        ("AAPL_20260430_161908_144548", "AAPL",  "opened 16:19 ET — likely still open"),
        ("AMAT_20260430_161923_354061", "AMAT",  "opened 16:19 ET — likely still open"),
        ("AMD_20260430_161913_341788",  "AMD",   "opened 16:19 ET — likely still open"),
        ("GS_20260430_161937_180223",   "GS",    "opened 16:19 ET — likely still open"),
        ("OXY_20260430_161930_657012",  "OXY",   "opened 16:19 ET — likely still open"),
        ("ALAB_20260430_145720_399032", "ALAB",  "2nd ALAB trade — no confirmed close"),
        ("AMZN_C_260.0_2026-05-22_20260430_144429_556587", "AMZN_C_260.0", "options — need IBKR exit price"),
        ("CFG_20260429_134828_113248",  "CFG",   "opened Apr 29 — no close evidence"),
        ("SNX_20260429_172800_821400",  "SNX",   "opened Apr 29 17:28 — no close evidence"),
        ("COIN_20260430_133557_253673", "COIN",  "opened today 13:35 — no close evidence"),
        ("COP_20260430_134548_950647",  "COP",   "opened today 13:45 — no close evidence"),
    ]
    for trade_id, symbol, reason in skipped:
        print(f"  SKIP: {symbol:8s} — {reason}")

    print()
    if DRY_RUN:
        print("DRY RUN complete — no data written.")
        print("Set DRY_RUN = False at the top of this script and re-run to commit.")
    else:
        print("Backfill complete. Verify with:")
        print("  python3 -c \"from event_log import open_trades; print(len(open_trades()), 'open')\"")


if __name__ == "__main__":
    main()
