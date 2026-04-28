#!/usr/bin/env python3
"""
Backfill data/training_records.jsonl from data/trades.json.

Runs once. Safe to re-run — deduplicates by trade_id.
"""
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import training_store

SOURCE = Path(__file__).parent.parent / "data" / "trades.json"


def _make_trade_id(t: dict) -> str:
    sym = t.get("symbol", "UNK")
    ts = t.get("entry_time") or t.get("timestamp") or "0"
    ts_clean = ts.replace(" ", "T").replace(":", "").replace("-", "")[:15]
    return f"{sym}_{ts_clean}"


def main() -> None:
    if not SOURCE.exists():
        print(f"Source not found: {SOURCE}")
        sys.exit(1)

    data = json.loads(SOURCE.read_text())
    closed = [
        t for t in data
        if t.get("exit_price") is not None and t.get("exit_time")
    ]
    print(f"Found {len(closed)} closed trades in trades.json")

    # Build set of existing trade_ids to avoid duplicates
    existing = {r.get("trade_id") for r in training_store.load()}
    print(f"Existing records in training_store: {len(existing)}")

    written = 0
    skipped = 0
    failed = 0

    for t in closed:
        trade_id = t.get("trade_id") or _make_trade_id(t)

        if trade_id in existing:
            skipped += 1
            continue

        # Build ISO timestamps
        entry_time = t.get("entry_time") or t.get("timestamp") or ""
        exit_time = t.get("exit_time") or ""
        try:
            ts_fill = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).isoformat()
        except Exception:
            ts_fill = entry_time or datetime.now(UTC).isoformat()
        try:
            ts_close = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).isoformat()
        except Exception:
            ts_close = exit_time or datetime.now(UTC).isoformat()

        record = {
            "trade_id": trade_id,
            "symbol": t.get("symbol", ""),
            "direction": t.get("direction", "LONG"),
            "trade_type": t.get("trade_type") or "INTRADAY",
            "instrument": t.get("vehicle", "stock") or "stock",
            "fill_price": float(t.get("entry_price") or 0.0),
            "intended_price": float(t.get("entry_price") or 0.0),
            "exit_price": float(t.get("exit_price") or 0.0),
            "pnl": float(t.get("pnl") or 0.0),
            "hold_minutes": int(t.get("hold_minutes") or 0),
            "exit_reason": t.get("exit_reason") or "unknown",
            "regime": t.get("regime") or "UNKNOWN",
            "signal_scores": t.get("signal_scores") or {},
            "conviction": float(t.get("conviction") or 0.0),
            "score": float(t.get("score") or t.get("entry_score") or 0.0),
            "ts_fill": ts_fill,
            "ts_close": ts_close,
            # optional extras preserved for ML
            "setup_type": t.get("setup_type"),
            "pattern_id": t.get("pattern_id"),
            "atr": t.get("atr"),
            "score_breakdown": t.get("score_breakdown"),
            "ic_weights_at_entry": t.get("ic_weights_at_entry"),
            "pnl_pct": t.get("pnl_pct"),
        }

        try:
            training_store.append(record)
            existing.add(trade_id)
            written += 1
        except Exception as e:
            print(f"  SKIP {trade_id}: {e}")
            failed += 1

    print(f"\nMigration complete: {written} written, {skipped} skipped (already existed), {failed} failed")
    print(f"Total in training_store: {training_store.count()}")


if __name__ == "__main__":
    main()
