# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  training_store.py                          ║
# ║   Append-only ML training data store                         ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Append-only JSONL store for closed trade records.

One record per closed trade, written exactly once after exit is confirmed by
IBKR.  Records are never modified after writing.

Record composition
------------------
Each record combines three data sources:
  - ORDER_INTENT fields  : signal_scores, trade_type, conviction, regime,
                           reasoning, score — the "why we entered"
  - ORDER_FILLED fields  : fill_price, fill_qty — "what actually happened"
  - Close fields         : exit_price, pnl, hold_minutes, exit_reason — "outcome"

Both intended_price and fill_price are preserved.  Slippage is a training
feature, not noise to be discarded.

Schema enforcement
------------------
Required fields are checked at write time.  A missing field raises ValueError
immediately — fail at write, not at ML training time two weeks later.

Replaces
--------
  data/trades.json          (JSON array, O(n) full rewrites on every close)
  closed_trades DB table    (SQLite table that was never read)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from config import CONFIG

log = logging.getLogger("decifer.training_store")

_STORE_FILE = Path(CONFIG.get("training_records", "data/training_records.jsonl"))
_lock = threading.Lock()

# Every record written to the training store must have these fields.
# Enforced at write time so gaps are caught immediately, not at training time.
_REQUIRED_FIELDS = frozenset({
    "trade_id",
    "symbol",
    "direction",
    "trade_type",
    "fill_price",
    "exit_price",
    "pnl",
    "hold_minutes",
    "exit_reason",
    "regime",
    "signal_scores",
    "conviction",
    "score",
    "ts_fill",
    "ts_close",
})


# ── Write ─────────────────────────────────────────────────────────────────────


def append(record: dict) -> None:
    """
    Write one closed-trade record to the training store.

    Raises ValueError immediately if any required field is absent.
    This is intentional: a missing field is a bug in the calling code and must
    not silently produce an incomplete training record.
    """
    missing = _REQUIRED_FIELDS - record.keys()
    if missing:
        raise ValueError(
            f"training_store.append: missing required fields: {sorted(missing)}"
        )

    out = dict(record)
    out.setdefault("ts_written", datetime.now(UTC).isoformat())

    _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(out, default=str) + "\n"
    with _lock:
        with open(_STORE_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    log.debug("training_store: wrote %s trade_id=%s pnl=%.2f",
              record.get("symbol"), record.get("trade_id"), record.get("pnl", 0))


# ── Read ──────────────────────────────────────────────────────────────────────


def load(symbol: str | None = None, limit: int = 0) -> list[dict]:
    """
    Load training records, optionally filtered by symbol.

    Args:
        symbol: If given, only return records for this symbol.
        limit:  If > 0, return only the most recent N records.

    A partial last line from a crash is silently skipped.
    """
    if not _STORE_FILE.exists():
        return []

    records: list[dict] = []
    with open(_STORE_FILE, encoding="utf-8") as f:
        lines = f.readlines()

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
            if symbol is None or rec.get("symbol") == symbol:
                records.append(rec)
        except json.JSONDecodeError:
            is_last = lineno == len(lines)
            if not is_last:
                log.warning(
                    "training_store: corrupt record at line %d — skipping", lineno
                )

    if limit > 0:
        return records[-limit:]
    return records


def count() -> int:
    """
    Return total number of closed-trade records.
    Used by phase_gate to evaluate IC and ML activation gates.
    """
    if not _STORE_FILE.exists():
        return 0
    n = 0
    with open(_STORE_FILE, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    json.loads(line)
                    n += 1
                except json.JSONDecodeError:
                    pass
    return n


def last(n: int = 1) -> list[dict]:
    """Return the most recent n records."""
    return load(limit=n)
