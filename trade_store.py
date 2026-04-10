# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  trade_store.py                             ║
# ║   Persistent position ledger — bot's source of truth        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Single responsibility: atomically persist and restore active_trades.

data/positions.json is the authoritative record of every open position
including all decision metadata (trade_type, conviction, regime, signal
scores, agent outputs, entry thesis, pattern_id, SL/TP levels, etc.).

IBKR is consulted only AFTER this store is loaded, and only to reconcile:
  - current market price
  - unrealised P&L (derived from that price)
  - fill status (was a SL/TP triggered while bot was down?)
  - qty (were there partial fills while bot was offline?)

Nothing IBKR returns ever overwrites the stored metadata fields.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from config import CONFIG

log = logging.getLogger("decifer.trade_store")

_POSITIONS_FILE = Path(CONFIG.get("positions_file", "data/positions.json"))

# Fields that IBKR is allowed to update at reconciliation time.
# Everything NOT in this set is owned by the bot and never overwritten.
IBKR_RECONCILE_FIELDS = frozenset({
    "current",
    "current_premium",
    "pnl",
    "_price_sources",
    "status",           # IBKR can confirm ACTIVE from PENDING after fill
})

# Fields that, if changed via _safe_update_trade, warrant a disk persist.
# Price/pnl ticks are excluded — those are transient and IBKR re-provides them.
STRUCTURAL_UPDATE_KEYS = frozenset({
    "sl",
    "tp",
    "sl_order_id",
    "tp_order_id",
    "t1_order_id",
    "t2_sl_order_id",
    "t1_status",
    "status",
    "qty",
})


def persist(snapshot: dict) -> None:
    """
    Atomically write a snapshot of active_trades to disk.
    Uses write-to-tmp + os.replace so a crash mid-write never corrupts the file.
    RESERVED placeholder entries (no instrument field) are excluded — they are
    transient concurrency guards, not real positions.
    """
    _POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        k: v for k, v in snapshot.items()
        if isinstance(v, dict) and v.get("status") != "RESERVED" and "instrument" in v
    }
    tmp = _POSITIONS_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(clean, indent=2, default=str))
        os.replace(str(tmp), str(_POSITIONS_FILE))
    except Exception as e:
        log.error(f"trade_store: failed to persist positions: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def restore() -> dict:
    """
    Load active_trades snapshot from disk.
    Returns an empty dict if the file is missing or corrupt.
    """
    try:
        if _POSITIONS_FILE.exists():
            raw = _POSITIONS_FILE.read_text().strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    log.info(
                        f"trade_store: restored {len(data)} open position(s) "
                        f"from {_POSITIONS_FILE}"
                    )
                    return data
    except Exception as e:
        log.error(f"trade_store: failed to restore positions: {e}")
    return {}
