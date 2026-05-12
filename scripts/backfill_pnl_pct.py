#!/usr/bin/env python3
"""
scripts/backfill_pnl_pct.py — one-time backfill of pnl_pct into existing
training_records.jsonl records that are missing it.

RUN ONCE only.  After this, all new close paths write pnl_pct directly.

pnl_pct = pnl / (fill_price * qty)

Two resolution methods (tried in order):
  Method A — direct:  fill_price and qty both present
  Method B — derived: fill_price, exit_price, pnl, and direction present;
             qty is inferred as round(pnl / price_diff) then discarded.
             Only pnl_pct is written — qty is NOT added to the record.

Records where neither method applies are left unchanged (pnl_pct absent).
Completely unrecoverable are printed in the summary so humans can decide.

Safety:
  - Reads training_records.jsonl → writes to a temp file → os.replace (atomic)
  - The ONLY fields added are pnl_pct (float) and pnl_pct_source ('direct'|'derived')
  - No existing field is modified or removed
  - Re-running is idempotent: records that already have pnl_pct are skipped
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("decifer.backfill_pnl_pct")

_BASE = Path(__file__).resolve().parent.parent
_STORE = _BASE / "data" / "training_records.jsonl"


def _derive_pnl_pct_direct(r: dict) -> float | None:
    """Method A: fill_price and qty are both present."""
    fp = r.get("fill_price")
    qty = r.get("qty")
    pnl = r.get("pnl")
    if fp is None or qty is None or pnl is None:
        return None
    try:
        fp, qty, pnl = float(fp), float(qty), float(pnl)
    except (TypeError, ValueError):
        return None
    denom = fp * qty
    if not denom or not (denom > 0):
        return None
    return round(pnl / denom, 6)


def _derive_pnl_pct_from_prices(r: dict) -> float | None:
    """
    Method B: derive qty from pnl / price_diff, then compute pnl_pct.

    Only used when qty is absent.  The derived qty is used solely to compute
    pnl_pct and is not written back to the record.
    """
    fp = r.get("fill_price")
    xp = r.get("exit_price")
    pnl = r.get("pnl")
    if fp is None or xp is None or pnl is None:
        return None
    try:
        fp, xp, pnl = float(fp), float(xp), float(pnl)
    except (TypeError, ValueError):
        return None
    direction = (r.get("direction") or "LONG").upper()
    price_diff = (fp - xp) if direction == "SHORT" else (xp - fp)
    if abs(price_diff) < 1e-6 or abs(pnl) < 1e-6:
        return None
    derived_qty = round(pnl / price_diff)
    if derived_qty <= 0:
        return None
    denom = fp * derived_qty
    if not denom:
        return None
    return round(pnl / denom, 6)


def backfill(store_path: Path = _STORE, dry_run: bool = False) -> dict:
    """
    Backfill pnl_pct into records that are missing it.

    Returns a summary dict:
      patched_direct   — fixed via Method A
      patched_derived  — fixed via Method B
      already_present  — skipped (already had pnl_pct)
      unrecoverable    — cannot compute (logged at WARNING)
      total            — total records read
    """
    if not store_path.exists():
        log.warning("backfill_pnl_pct: store not found at %s", store_path)
        return {}

    records: list[dict] = []
    with open(store_path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                log.warning("backfill_pnl_pct: bad JSON at line %d — preserving as-is", lineno)
                records.append({"_raw_line": stripped})

    stats = {"patched_direct": 0, "patched_derived": 0,
             "already_present": 0, "unrecoverable": 0, "total": len(records)}

    for r in records:
        if "_raw_line" in r:
            continue
        if r.get("pnl_pct") is not None:
            stats["already_present"] += 1
            continue

        # Method A
        val = _derive_pnl_pct_direct(r)
        if val is not None:
            r["pnl_pct"] = val
            r["pnl_pct_source"] = "direct"
            stats["patched_direct"] += 1
            continue

        # Method B
        val = _derive_pnl_pct_from_prices(r)
        if val is not None:
            r["pnl_pct"] = val
            r["pnl_pct_source"] = "derived"
            stats["patched_derived"] += 1
            continue

        stats["unrecoverable"] += 1
        log.warning(
            "backfill_pnl_pct: cannot compute pnl_pct for trade_id=%s symbol=%s "
            "fill_price=%s qty=%s exit_price=%s pnl=%s",
            r.get("trade_id"), r.get("symbol"),
            r.get("fill_price"), r.get("qty"),
            r.get("exit_price"), r.get("pnl"),
        )

    if dry_run:
        return stats

    # Atomic write
    dir_ = store_path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in records:
                if "_raw_line" in r:
                    fh.write(r["_raw_line"] + "\n")
                else:
                    fh.write(json.dumps(r, default=str) + "\n")
        os.replace(tmp, store_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return stats


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill pnl_pct into training_records.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    parser.add_argument("--store", default=str(_STORE), help="Path to training_records.jsonl")
    args = parser.parse_args()

    stats = backfill(Path(args.store), dry_run=args.dry_run)
    if not stats:
        print("Nothing to do (store not found).")
        return

    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{mode}pnl_pct backfill complete")
    print(f"  Total records:    {stats['total']}")
    print(f"  Already present:  {stats['already_present']}")
    print(f"  Patched (direct): {stats['patched_direct']}")
    print(f"  Patched (derived):{stats['patched_derived']}")
    print(f"  Unrecoverable:    {stats['unrecoverable']}")
    if not args.dry_run and (stats['patched_direct'] + stats['patched_derived']) > 0:
        print(f"\n  Written to: {args.store}")


if __name__ == "__main__":
    main()
