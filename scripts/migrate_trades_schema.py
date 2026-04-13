#!/usr/bin/env python3
"""
migrate_trades_schema.py — Normalise trades.json to a single canonical schema.

What this does:
  - Adds missing fields with null/empty defaults (no fabrication)
  - Derives hold_minutes and pnl_pct where calculable from existing data
  - Marks legacy trades (ibkr_activity_statement, ibkr_backfill) with legacy=True
  - Preserves all existing field values unchanged
  - Writes a backup before touching the file

What this does NOT do:
  - Fabricate score, signal_scores, score_breakdown, regime, vix, or agents
  - Remove any existing fields
  - Change any existing field values

Usage:
    python3 scripts/migrate_trades_schema.py          # dry-run (prints summary)
    python3 scripts/migrate_trades_schema.py --apply  # writes to disk
"""

from __future__ import annotations
import json
import os
import shutil
import sys
from datetime import datetime, timezone

ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES = os.path.join(ROOT, "data", "trades.json")
BACKUP = os.path.join(ROOT, "data", "trades.json.pre_migration_bak")

# Legacy sources — trades from these lack signal data
LEGACY_SOURCES = {"ibkr_activity_statement", "ibkr_backfill"}

# Canonical field set from learning.py:log_trade()
# Value is the default used when field is absent.
CANONICAL = {
    "timestamp":           None,
    "action":              None,
    "symbol":              None,
    "direction":           "LONG",
    "qty":                 None,
    "entry_price":         None,
    "exit_price":          None,
    "sl":                  None,
    "tp":                  None,
    "score":               None,    # never fabricate — null means unknown
    "entry_score":         None,
    "setup_type":          None,
    "reasoning":           None,
    "regime":              None,
    "vix":                 None,
    "pnl":                 None,
    "pnl_pct":             None,
    "exit_reason":         None,
    "hold_minutes":        None,
    "agents":              {},
    "signal_scores":       {},
    "score_breakdown":     {},
    "ic_weights_at_entry": None,
    "ic_weighted_score":   None,
    "candle_gate":         "UNKNOWN",
    "tranche_id":          None,
    "parent_trade_id":     None,
    "pattern_id":          None,
    "advice_id":           "",
    "trade_type":          None,
    "conviction":          None,
    "entry_thesis":        None,
    "legacy":              False,   # will be overridden for legacy sources
}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _derive_hold_minutes(trade: dict) -> int | None:
    """Compute hold_minutes from whatever time fields exist."""
    # New schema: open_time (set on active trade) vs close timestamp
    # Old schema: entry_time + exit_time both present
    entry_str = (trade.get("entry_time")
                 or trade.get("open_time")
                 or trade.get("time"))
    exit_str  = (trade.get("exit_time")
                 or trade.get("close_time"))
    if not entry_str or not exit_str:
        return None
    entry_dt = _parse_dt(entry_str)
    exit_dt  = _parse_dt(exit_str)
    if entry_dt is None or exit_dt is None:
        return None
    # Make both tz-aware or both naive before subtracting
    if entry_dt.tzinfo is None and exit_dt.tzinfo is not None:
        exit_dt = exit_dt.replace(tzinfo=None)
    elif entry_dt.tzinfo is not None and exit_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=None)
    diff = exit_dt - entry_dt
    minutes = int(diff.total_seconds() / 60)
    return minutes if minutes >= 0 else None


def _derive_pnl_pct(trade: dict) -> float | None:
    """Compute pnl_pct from pnl / (entry_price * qty) if both present."""
    pnl         = trade.get("pnl")
    entry_price = trade.get("entry_price")
    qty         = trade.get("qty")
    if pnl is None or not entry_price or not qty:
        return None
    invested = abs(float(entry_price)) * abs(float(qty))
    if invested == 0:
        return None
    return round(float(pnl) / invested * 100, 4)


def migrate_trade(trade: dict) -> dict:
    """Return a new trade dict with all canonical fields present."""
    result = {}

    # 1. Start with canonical defaults
    for field, default in CANONICAL.items():
        # Copy mutable defaults safely
        if isinstance(default, dict):
            result[field] = dict(default)
        elif isinstance(default, list):
            result[field] = list(default)
        else:
            result[field] = default

    # 2. Overlay all existing values (preserves everything already there)
    result.update(trade)

    # 3. Derive hold_minutes if still missing
    if result.get("hold_minutes") is None:
        hm = _derive_hold_minutes(trade)
        if hm is not None:
            result["hold_minutes"] = hm

    # 4. Derive pnl_pct if still missing
    if result.get("pnl_pct") is None:
        pp = _derive_pnl_pct(trade)
        if pp is not None:
            result["pnl_pct"] = pp

    # 5. Mark legacy trades
    src = trade.get("source", "")
    if src in LEGACY_SOURCES:
        result["legacy"] = True

    return result


def main(apply: bool = False) -> None:
    with open(TRADES) as f:
        trades = json.load(f)

    original_count = len(trades)
    migrated       = [migrate_trade(t) for t in trades]

    # Stats
    legacy_count   = sum(1 for t in migrated if t.get("legacy"))
    hold_derived   = sum(
        1 for o, m in zip(trades, migrated)
        if o.get("hold_minutes") is None and m.get("hold_minutes") is not None
    )
    pct_derived    = sum(
        1 for o, m in zip(trades, migrated)
        if o.get("pnl_pct") is None and m.get("pnl_pct") is not None
    )

    # Verify all trades now have the full canonical set
    missing_any = []
    for i, t in enumerate(migrated):
        missing = [f for f in CANONICAL if f not in t]
        if missing:
            missing_any.append((i, t.get("symbol"), missing))

    print(f"Trades processed   : {original_count}")
    print(f"Legacy flagged     : {legacy_count}  (source in {sorted(LEGACY_SOURCES)})")
    print(f"hold_minutes derived: {hold_derived}")
    print(f"pnl_pct derived    : {pct_derived}")
    print(f"Still missing fields: {len(missing_any)}")
    if missing_any:
        for idx, sym, fields in missing_any[:5]:
            print(f"  [{idx}] {sym}: {fields}")

    if not apply:
        print("\nDry run — pass --apply to write changes.")
        return

    # Backup
    shutil.copy2(TRADES, BACKUP)
    print(f"\nBackup written to: {BACKUP}")

    # Write
    with open(TRADES, "w") as f:
        json.dump(migrated, f, indent=2, default=str)
    print(f"trades.json updated ({original_count} trades).")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
