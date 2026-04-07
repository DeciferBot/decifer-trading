#!/usr/bin/env python3
"""
Backfill ic_weights_at_entry, raw_ic_at_entry, ic_using_equal_weights,
and ic_weighted_score onto live trades that already have signal_scores.

IBKR-imported trades (no signal_scores) are left untouched — they were a
one-time historical data recovery and have no signal linkage.

Run with --dry-run to preview without writing.
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Resolve paths relative to repo root (one level up from scripts/)
_REPO = Path(__file__).parent.parent
TRADES_PATH  = _REPO / "data" / "trades.json"
IC_HIST_PATH = _REPO / "data" / "ic_weights_history.jsonl"

sys.path.insert(0, str(_REPO))
from ic_calculator import DIMENSIONS, EQUAL_WEIGHTS  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_utc(ts_str: str):
    """Parse an ISO-8601 UTC timestamp to a naive UTC datetime."""
    try:
        return datetime.fromisoformat(ts_str).replace(tzinfo=None)
    except Exception:
        return None


def load_ic_history(path: Path) -> list[dict]:
    """Load ic_weights_history.jsonl sorted ascending by updated timestamp."""
    history = []
    if not path.exists():
        return history
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rec["_updated_utc"] = _parse_utc(rec.get("updated", ""))
                if rec["_updated_utc"] is not None:
                    history.append(rec)
            except Exception:
                continue
    history.sort(key=lambda r: r["_updated_utc"])
    return history


def find_ic_snapshot(entry_utc, ic_history):
    """Return the most recent IC snapshot whose updated <= entry_utc."""
    lo, hi = 0, len(ic_history)
    while lo < hi:
        mid = (lo + hi) // 2
        if ic_history[mid]["_updated_utc"] <= entry_utc:
            lo = mid + 1
        else:
            hi = mid
    idx = lo - 1
    return ic_history[idx] if idx >= 0 else None


def build_ic_fields(snapshot):
    """Build the 3 IC metadata fields from a snapshot (or equal-weight fallback)."""
    if snapshot is None:
        return {
            "ic_weights_at_entry":   dict(EQUAL_WEIGHTS),
            "raw_ic_at_entry":       {d: None for d in DIMENSIONS},
            "ic_using_equal_weights": True,
        }
    return {
        "ic_weights_at_entry":   snapshot["weights"],
        "raw_ic_at_entry":       snapshot["raw_ic"],
        "ic_using_equal_weights": snapshot.get("using_equal_weights", True),
    }


def compute_ic_weighted_score(signal_scores, ic_weights):
    """Weighted sum of signal_scores by ic_weights across all DIMENSIONS."""
    if not signal_scores:
        return None
    return sum(
        float(signal_scores.get(d, 0.0)) * ic_weights.get(d, EQUAL_WEIGHTS[d])
        for d in DIMENSIONS
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def backfill(dry_run: bool = False):
    with open(TRADES_PATH) as f:
        trades = json.load(f)

    ic_history = load_ic_history(IC_HIST_PATH)

    results = []
    stats: Counter = Counter()

    for trade in trades:
        t = dict(trade)
        sig_scores = t.get("signal_scores")

        # Skip trades with no signal data (IBKR historical import)
        if not sig_scores:
            stats["skipped_no_signal"] += 1
            results.append(t)
            continue

        # Resolve timestamp
        ts_str = t.get("timestamp") or t.get("entry_time") or t.get("time")
        entry_utc = _parse_utc(ts_str) if ts_str else None

        if entry_utc is None:
            stats["skipped_no_timestamp"] += 1
            results.append(t)
            continue

        # Look up IC snapshot
        snapshot = find_ic_snapshot(entry_utc, ic_history)
        ic_fields = build_ic_fields(snapshot)

        # Compute weighted score
        ic_fields["ic_weighted_score"] = compute_ic_weighted_score(
            sig_scores, ic_fields["ic_weights_at_entry"]
        )

        if dry_run:
            status = "equal_weights" if ic_fields["ic_using_equal_weights"] else "real_ic"
            print(
                f"  {t.get('symbol', '?'):8s}  ts={ts_str[:19]}  "
                f"ic_weighted_score={ic_fields['ic_weighted_score']:.3f}  [{status}]"
            )

        t.update(ic_fields)
        stats["enriched"] += 1
        if snapshot is None:
            stats["fallback_equal_weights"] += 1

        results.append(t)

    print(f"\nSummary:")
    print(f"  Total trades:              {len(trades)}")
    print(f"  Enriched with IC fields:   {stats['enriched']}")
    print(f"    Using equal-weight fallback (no prior snapshot): {stats['fallback_equal_weights']}")
    print(f"  Skipped (no signal_scores): {stats['skipped_no_signal']}")
    print(f"  Skipped (no timestamp):     {stats['skipped_no_timestamp']}")
    print(f"  IC history snapshots loaded: {len(ic_history)}")

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
        print("=== DRY RUN — IC backfill preview ===")
    backfill(dry_run=dry_run)
