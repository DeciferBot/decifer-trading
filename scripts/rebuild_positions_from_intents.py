#!/usr/bin/env python3
"""
Transition script — run ONCE before the next bot restart after applying the
reconcile_with_ibkr fix.

Problem: positions.json has been corrupted by the ghost cycle (it contains
trade_id-keyed entries with no metadata instead of symbol-keyed entries with
full metadata). All currently-open positions are missing from it.

This script rebuilds positions.json from:
  1. event_log ORDER_INTENT records (most recent per symbol) — provides all
     decision metadata: score, trade_type, conviction, signal_scores, etc.
  2. The intended_price from the ORDER_INTENT as the entry price placeholder
     (IBKR reconciliation overwrites this with the real fill price on restart).

Usage:
    python3 scripts/rebuild_positions_from_intents.py           # dry-run (print only)
    python3 scripts/rebuild_positions_from_intents.py --write   # write positions.json

Steps:
    1. Stop the bot.
    2. Ask Amit to verify the printed symbol list against TWS open positions.
    3. Run with --write.
    4. Start the bot.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# Allow running from repo root or scripts/
_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo))

from config import CONFIG
from event_log import pending_orders

_POSITIONS_FILE = Path(CONFIG.get("positions_file", "data/positions.json"))

# Synthetic / test symbols that should never appear in a real positions.json.
_SYNTHETIC_SYMBOLS = {"CHEAP", "EXPENSIVE"}


def _is_synthetic(intent: dict) -> bool:
    sym = intent.get("symbol", "")
    if sym in _SYNTHETIC_SYMBOLS:
        return True
    # Telltale signature of the CHEAP/EXPENSIVE entries.
    if intent.get("conviction") == 0.0 and intent.get("intended_price") == 100.0:
        return True
    return False


def build_positions(dry_run: bool = True) -> dict:
    """Return the rebuilt positions dict (symbol-keyed)."""
    pending = pending_orders()

    # Most-recent intent per symbol: later entries in the JSONL win.
    by_symbol: dict[str, dict] = {}
    for intent in pending:
        sym = intent.get("symbol")
        if not sym:
            continue
        if _is_synthetic(intent):
            continue
        by_symbol[sym] = intent  # last one wins

    positions: dict[str, dict] = {}
    for sym, intent in sorted(by_symbol.items()):
        entry_px = float(intent.get("intended_price") or 0.0)
        positions[sym] = {
            "symbol": sym,
            "direction": intent.get("direction", "LONG"),
            "trade_type": intent.get("trade_type", "UNKNOWN"),
            "instrument": intent.get("instrument", "stock"),
            "entry": entry_px,
            "current": entry_px,
            "pnl": 0.0,
            "qty": int(intent.get("qty") or 0),
            "sl": intent.get("sl"),
            "tp": intent.get("tp"),
            "score": intent.get("score") or 0.0,
            "conviction": intent.get("conviction") or 0.0,
            "signal_scores": intent.get("signal_scores") or {},
            "entry_regime": intent.get("regime") or intent.get("entry_regime") or "",
            "entry_thesis": intent.get("reasoning") or intent.get("entry_thesis") or "",
            "trade_id": intent.get("trade_id") or "",
            "open_time": intent.get("open_time") or intent.get("ts") or datetime.now(UTC).isoformat(),
            "status": "ACTIVE",
        }

    return positions


def main() -> None:
    write = "--write" in sys.argv

    positions = build_positions()

    if not positions:
        print("No pending ORDER_INTENT records found. Nothing to rebuild.")
        print("If positions are open in IBKR, they will be ghosted on restart.")
        return

    print(f"\n{'DRY RUN — ' if not write else ''}Rebuilding positions.json with {len(positions)} symbol(s):\n")
    for sym, pos in sorted(positions.items()):
        print(
            f"  {sym:12s}  {pos['direction']:5s}  {pos['trade_type']:10s}"
            f"  entry={pos['entry']:.2f}  score={pos['score']:.0f}"
            f"  qty={pos['qty']}"
        )

    print()

    if not write:
        print("VERIFY the list above against your open TWS positions.")
        print("Then run with --write to apply.\n")
        skipped = []
        all_pending = pending_orders()
        seen = set()
        for intent in all_pending:
            sym = intent.get("symbol", "")
            if sym and _is_synthetic(intent) and sym not in seen:
                skipped.append(sym)
                seen.add(sym)
        if skipped:
            print(f"Skipped synthetic symbols: {sorted(set(skipped))}")
        return

    # Atomic write.
    dir_name = _POSITIONS_FILE.parent
    dir_name.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, suffix=".tmp") as f:
        json.dump(positions, f, indent=2, default=str)
        tmp_path = f.name
    os.replace(tmp_path, _POSITIONS_FILE)
    print(f"Written: {_POSITIONS_FILE}")
    print("You can now start the bot. IBKR reconciliation will fill in real prices and qty.")


if __name__ == "__main__":
    main()
