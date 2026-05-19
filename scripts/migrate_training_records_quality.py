#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  migrate_training_records_quality.py       ║
# ║   Surgical migration: mark obviously degraded legacy        ║
# ║   training records as ml_eligible=False.                    ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Surgical one-time migration for existing data/training_records.jsonl.

Purpose
-------
Records written before the metadata quarantine system (2026-05-19) do not have
ml_eligible / ic_eligible / metadata_quality / metadata_loss fields.

This script adds those fields to records that are OBVIOUSLY degraded:

  Criterion 1: trade_type is "UNKNOWN" or blank
  Criterion 2: exit_reason is "unknown_trade_type"
  Criterion 3: trade_id contains "_EXT_" (anchored by orphan reconcile path)

These three criteria are definitive — there is no ambiguity.

Records that do not meet any criterion are left unchanged.  "Obvious quality
cannot be inferred" records (e.g. trade_type=INTRADAY but signal_scores={})
are NOT touched.  Amit's instruction: do not broadly rewrite where quality
cannot be inferred.

Idempotent
----------
Records that already have ml_eligible set are not modified.

Output
------
  Dry-run  (default): prints the report only; writes nothing.
  --apply:  rewrites data/training_records.jsonl atomically.

Usage
-----
  python3 scripts/migrate_training_records_quality.py
  python3 scripts/migrate_training_records_quality.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

from config import CONFIG  # noqa: E402

_STORE_FILE = Path(CONFIG.get("training_records", "data/training_records.jsonl"))
_DEGRADED_QUALITY = {
    "metadata_quality":  "degraded_metadata_loss",
    "ml_eligible":       False,
    "ic_eligible":       False,
    "metadata_loss":     True,
    "training_eligible": False,
}


def _is_obviously_degraded(rec: dict) -> tuple[bool, str]:
    """
    Return (degraded: bool, reason: str).

    Only applies the three definitive criteria.  Returns ("", False) when
    quality cannot be inferred with certainty.
    """
    tt = (rec.get("trade_type") or "").upper()
    if tt in ("UNKNOWN", ""):
        return True, "trade_type_unknown_or_blank"

    er = (rec.get("exit_reason") or "").lower()
    if er == "unknown_trade_type":
        return True, "exit_reason_unknown_trade_type"

    tid = (rec.get("trade_id") or "").lower()
    if "_ext_" in tid:
        return True, "trade_id_contains_ext"

    return False, ""


def _load_records() -> list[dict]:
    if not _STORE_FILE.exists():
        print(f"No training records file found at {_STORE_FILE}")
        return []
    records = []
    with open(_STORE_FILE, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                print(f"  WARNING: skipping corrupt line {lineno}")
    return records


def _migrate(records: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply quality flags to obviously degraded records.

    Returns (migrated_records, report_counts).
    """
    out: list[dict] = []
    counts = {
        "total": len(records),
        "already_tagged": 0,
        "newly_degraded": 0,
        "left_unchanged": 0,
        "by_reason": {},
    }

    for rec in records:
        # Already has quality tag — idempotent, skip.
        if "ml_eligible" in rec:
            counts["already_tagged"] += 1
            out.append(rec)
            continue

        degraded, reason = _is_obviously_degraded(rec)
        if degraded:
            updated = dict(rec)
            updated.update(_DEGRADED_QUALITY)
            out.append(updated)
            counts["newly_degraded"] += 1
            counts["by_reason"][reason] = counts["by_reason"].get(reason, 0) + 1
        else:
            out.append(rec)
            counts["left_unchanged"] += 1

    return out, counts


def _print_report(counts: dict) -> None:
    print("\n── Migration Report ─────────────────────────────────────────────────")
    print(f"  Total records:        {counts['total']}")
    print(f"  Already tagged:       {counts['already_tagged']}  (idempotent — skipped)")
    print(f"  Newly degraded:       {counts['newly_degraded']}  (ml_eligible=False added)")
    print(f"  Left unchanged:       {counts['left_unchanged']}  (quality cannot be inferred)")
    if counts["by_reason"]:
        print("  Degraded by reason:")
        for r, n in sorted(counts["by_reason"].items()):
            print(f"    {r}: {n}")
    print("─────────────────────────────────────────────────────────────────────\n")

    eligible_after = counts["total"] - counts["newly_degraded"] - counts["already_tagged"]
    # already_tagged could be either eligible or not — we can't know from counts alone.
    # Just report the newly affected delta.
    if counts["newly_degraded"] > 0:
        print(
            f"  After migration: {counts['newly_degraded']} record(s) will be excluded from "
            f"ML/IC eligibility (count_eligible() will decrease accordingly).\n"
        )
    else:
        print("  No new degraded records found — file is already clean.\n")


def _write_atomic(records: list[dict]) -> None:
    """Write records to a temp file then rename atomically."""
    _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_STORE_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _STORE_FILE)
        print(f"  Written: {_STORE_FILE}")
    except Exception:
        os.unlink(tmp_path)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mark obviously degraded training records as ml_eligible=False."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the migrated file. Without this flag, prints a dry-run report only.",
    )
    args = parser.parse_args()

    print(f"\nReading {_STORE_FILE} ...")
    records = _load_records()
    if not records:
        print("Nothing to do.")
        return

    migrated, counts = _migrate(records)
    _print_report(counts)

    if counts["newly_degraded"] == 0:
        print("No changes needed.")
        return

    if args.apply:
        print("Applying migration ...")
        _write_atomic(migrated)
        print("Done.")
    else:
        print("DRY RUN — pass --apply to write changes.")
        print(f"Re-run: python3 scripts/{Path(__file__).name} --apply\n")


if __name__ == "__main__":
    main()
