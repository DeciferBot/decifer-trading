"""
rotation_observability.py — Write-ahead JSONL for rotation policy data needs.

Writes two files to data/rotation_observability/:
  margin_blocks.jsonl    — one record per exposure block event
  position_snapshots.jsonl — book snapshot at each block moment

Safety rules:
  - stdlib only (no config, no orders_core, no runtime modules)
  - never raises — every function is fully wrapped in try/except
  - thread-safe via per-file locks
  - tolerates missing output directory (creates it on first write)

These files are diagnostic only. They do not influence execution.
The rotation shadow report reads them to improve block-time book reconstruction.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

UTC = timezone.utc

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_OBS_DIR  = os.path.join(_DATA_DIR, "rotation_observability")

_BLOCKS_PATH    = os.path.join(_OBS_DIR, "margin_blocks.jsonl")
_SNAPSHOTS_PATH = os.path.join(_OBS_DIR, "position_snapshots.jsonl")

_MAX_BYTES = 50 * 1_048_576  # 50 MB — matches other JSONL rotation limits

_blocks_lock    = threading.Lock()
_snapshots_lock = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────────────────


def _ensure_dir() -> None:
    os.makedirs(_OBS_DIR, exist_ok=True)


def _rotate_if_needed(path: str) -> None:
    """Rename path → path.bak when file exceeds _MAX_BYTES."""
    try:
        if os.path.getsize(path) >= _MAX_BYTES:
            os.replace(path, path + ".bak")
    except OSError:
        pass


def _slim_position(pos: dict) -> dict:
    """Extract only the fields needed by the rotation shadow report."""
    keep = (
        "symbol", "score", "qty", "entry", "open_time",
        "trade_type", "direction", "notional", "pnl",
    )
    return {k: pos[k] for k in keep if k in pos}


# ── public API ────────────────────────────────────────────────────────────────


def write_margin_block(
    *,
    symbol: str,
    candidate_score: int,
    direction: str,
    exp_code: str,
    exp_reason: str,
    estimated_notional: float,
    portfolio_value: float,
    open_position_count: int,
    max_positions: int,
    max_alloc_pct: float,
    max_single_pct: float,
    active_trades: dict | None = None,
) -> None:
    """
    Append one record to margin_blocks.jsonl and optionally snapshot the book.

    estimated_notional = portfolio_value * max_single_pct — an upper-bound
    estimate, NOT the sizing engine output (which runs after this check).
    The notional_is_estimate flag communicates this to readers.

    active_trades is accepted as a snapshot hint; passing None skips the
    position_snapshots write without error.
    """
    try:
        _ensure_dir()
        ts = datetime.now(UTC).isoformat()
        record = {
            "ts":                    ts,
            "symbol":                symbol,
            "candidate_score":       candidate_score,
            "direction":             direction,
            "exp_code":              exp_code,
            "exp_reason":            exp_reason,
            "estimated_notional":    round(estimated_notional, 2),
            "notional_is_estimate":  True,
            "portfolio_value":       round(portfolio_value, 2),
            "open_position_count":   open_position_count,
            "max_positions":         max_positions,
            "max_alloc_pct":         max_alloc_pct,
            "max_single_pct":        max_single_pct,
        }
        with _blocks_lock:
            _rotate_if_needed(_BLOCKS_PATH)
            with open(_BLOCKS_PATH, "a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass  # observability must never affect the execution path

    if active_trades is not None:
        write_position_snapshot(
            trigger=f"margin_block:{symbol}",
            active_trades=active_trades,
        )


def write_position_snapshot(
    *,
    trigger: str,
    active_trades: dict,
) -> None:
    """
    Append a slim book snapshot to position_snapshots.jsonl.

    Each record carries a UTC timestamp and the trigger label so the
    rotation shadow report can match snapshots to specific block events.
    """
    try:
        _ensure_dir()
        ts = datetime.now(UTC).isoformat()
        positions = {sym: _slim_position(pos) for sym, pos in active_trades.items()
                     if isinstance(pos, dict) and pos.get("status") not in ("RESERVED",)}
        record = {
            "ts":        ts,
            "trigger":   trigger,
            "positions": positions,
        }
        with _snapshots_lock:
            _rotate_if_needed(_SNAPSHOTS_PATH)
            with open(_SNAPSHOTS_PATH, "a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass
