"""
pm_observability.py — Write-ahead JSONL for Portfolio Management diagnostic data.

Migrated from rotation_observability.py. Writes two files to data/pm_engine/:
  margin_blocks.jsonl      — one record per exposure block event
  position_snapshots.jsonl — book snapshot at each block moment

Safety rules (unchanged from predecessor):
  - stdlib only (no config, no orders_core, no runtime modules)
  - never raises — every function is fully wrapped in try/except
  - thread-safe via per-file locks
  - tolerates missing output directory (creates it on first write)

These files are diagnostic only. They do not influence execution.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

UTC = timezone.utc

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_OBS_DIR  = os.path.join(_DATA_DIR, "pm_engine")

_BLOCKS_PATH    = os.path.join(_OBS_DIR, "margin_blocks.jsonl")
_SNAPSHOTS_PATH = os.path.join(_OBS_DIR, "position_snapshots.jsonl")

_MAX_BYTES = 50 * 1_048_576  # 50 MB

_blocks_lock    = threading.Lock()
_snapshots_lock = threading.Lock()


def _ensure_dir() -> None:
    os.makedirs(_OBS_DIR, exist_ok=True)


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.getsize(path) >= _MAX_BYTES:
            os.replace(path, path + ".bak")
    except OSError:
        pass


def _slim_position(pos: dict) -> dict:
    keep = ("symbol", "score", "qty", "entry", "open_time",
            "trade_type", "direction", "notional", "pnl")
    return {k: pos[k] for k in keep if k in pos}


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
        pass

    if active_trades is not None:
        write_position_snapshot(trigger=f"margin_block:{symbol}", active_trades=active_trades)


def write_position_snapshot(*, trigger: str, active_trades: dict) -> None:
    try:
        _ensure_dir()
        ts = datetime.now(UTC).isoformat()
        positions = {
            sym: _slim_position(pos)
            for sym, pos in active_trades.items()
            if isinstance(pos, dict) and pos.get("status") not in ("RESERVED",)
        }
        record = {"ts": ts, "trigger": trigger, "positions": positions}
        with _snapshots_lock:
            _rotate_if_needed(_SNAPSHOTS_PATH)
            with open(_SNAPSHOTS_PATH, "a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass
