#!/usr/bin/env python3
"""
recover_pattern_ids.py — Retroactive pattern_id restoration for active positions.

Restores pattern_id to positions that lost it via IBKR re-sync ONLY when there is
exactly one unambiguous candidate in the pattern_library. Skips any case with 0 or
multiple candidates (too risky — wrong linkage corrupts the learning dataset).

Run ONCE after a bot restart (positions.json is the authoritative source on startup).
Safe to run while the bot is not running; do NOT run while the bot is live.

Usage:
    python3 scripts/recover_pattern_ids.py [--dry-run]
"""
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

POSITIONS_FILE = Path("data/positions.json")
PATTERN_FILE   = Path("data/pattern_library.json")
DRY_RUN        = "--dry-run" in sys.argv


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"ERROR loading {path}: {e}")
        return {}


def save_json(path: Path, data: dict) -> None:
    import tempfile
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def base_symbol(key: str) -> str:
    """Strip option suffix: WOLF_C_24.0_2026-05-01 → WOLF."""
    return key.split("_")[0]


def main() -> None:
    positions = load_json(POSITIONS_FILE)
    patterns  = load_json(PATTERN_FILE)

    if not positions:
        print("No active positions. Nothing to do.")
        return
    if not patterns:
        print("Pattern library is empty. Nothing to do.")
        return

    # Index patterns by symbol for fast lookup
    # Only consider patterns without an outcome (pnl is None) — these are the open ones
    patterns_by_symbol: dict[str, list[tuple[str, dict]]] = {}
    for pid, pat in patterns.items():
        if pat.get("pnl") is not None:
            continue  # already completed — skip
        sym = pat.get("symbol", "")
        if sym not in patterns_by_symbol:
            patterns_by_symbol[sym] = []
        patterns_by_symbol[sym].append((pid, pat))

    restored = 0
    skipped_ambiguous = 0
    skipped_no_match  = 0
    already_ok        = 0

    for pos_key, pos in positions.items():
        sym = base_symbol(pos_key)

        if pos.get("pattern_id"):
            already_ok += 1
            continue  # already has a pattern_id — do not touch

        if pos.get("trade_type") and pos["trade_type"] not in ("UNKNOWN", None):
            pass  # has trade metadata but lost pattern_id — still try to recover

        candidates = patterns_by_symbol.get(sym, [])

        if len(candidates) == 0:
            print(f"  UNRECOVERABLE  {pos_key}: no pattern_library entries for '{sym}'")
            skipped_no_match += 1

        elif len(candidates) > 1:
            # Sort by timestamp descending — most recent first for the log
            candidates.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
            print(f"  AMBIGUOUS      {pos_key}: {len(candidates)} candidates for '{sym}' "
                  f"(most recent: {candidates[0][0]} @ {candidates[0][1].get('timestamp','?')[:16]})")
            skipped_ambiguous += 1

        else:
            pid, pat = candidates[0]
            print(f"  RESTORING      {pos_key}: pattern_id={pid} "
                  f"(recorded {pat.get('timestamp','?')[:16]})")
            if not DRY_RUN:
                positions[pos_key]["pattern_id"] = pid
            restored += 1

    print()
    print(f"Summary: {restored} restored, {skipped_ambiguous} ambiguous (skipped), "
          f"{skipped_no_match} unrecoverable, {already_ok} already had pattern_id")

    if DRY_RUN:
        print("DRY RUN — no changes written.")
        return

    if restored > 0:
        save_json(POSITIONS_FILE, positions)
        print(f"Saved {POSITIONS_FILE}")
    else:
        print("No changes needed.")


if __name__ == "__main__":
    # Always run from the project root
    os.chdir(Path(__file__).parent.parent)
    main()
