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
import threading
from pathlib import Path

from config import CONFIG

log = logging.getLogger("decifer.trade_store")

_POSITIONS_FILE = Path(CONFIG.get("positions_file", "data/positions.json"))
_persist_lock = threading.Lock()

# Fields that IBKR is allowed to update at reconciliation time.
# Everything NOT in this set is owned by the bot and never overwritten.
IBKR_RECONCILE_FIELDS = frozenset(
    {
        "current",
        "current_premium",
        "pnl",
        "_price_sources",
        "status",  # IBKR can confirm ACTIVE from PENDING after fill
    }
)

# Fields that, if changed via _safe_update_trade, warrant a disk persist.
# Price/pnl ticks are excluded — those are transient and IBKR re-provides them.
STRUCTURAL_UPDATE_KEYS = frozenset(
    {
        "sl",
        "tp",
        "sl_order_id",
        "tp_order_id",
        "t1_order_id",
        "t2_sl_order_id",
        "t1_status",
        "status",
        "qty",
    }
)


def persist(snapshot: dict) -> None:
    """
    Atomically write a snapshot of active_trades to disk.
    Uses write-to-tmp + os.replace so a crash mid-write never corrupts the file.
    RESERVED placeholder entries (no instrument field) are excluded — they are
    transient concurrency guards, not real positions.
    """
    _POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        k: v for k, v in snapshot.items() if isinstance(v, dict) and v.get("status") != "RESERVED" and "instrument" in v
    }
    with _persist_lock:
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


# ── Metadata ledger ──────────────────────────────────────────────────────────
# Separate append-style file that stores decision metadata the instant a trade
# is opened.  positions.json can be corrupted/truncated by a hard crash —
# the ledger survives because it is written once and never rewritten in bulk.
_LEDGER_FILE = Path(CONFIG.get("metadata_ledger_file", "data/metadata_ledger.json"))
_ledger_lock = threading.Lock()

# Fields captured in the ledger (superset of DECISION_METADATA_FIELDS in orders_state)
_LEDGER_FIELDS = frozenset(
    {
        "symbol",
        "instrument",
        "direction",
        "entry",
        "qty",
        "trade_type",
        "conviction",
        "reasoning",
        "signal_scores",
        "agent_outputs",
        "entry_regime",
        "entry_thesis",
        "entry_score",
        "ic_weights_at_entry",
        "pattern_id",
        "setup_type",
        "advice_id",
        "open_time",
        "atr",
        "high_water_mark",
        "right",
        "strike",
        "expiry_str",
        "tranche_mode",
        "t1_qty",
        "t2_qty",
        "sl",
        "tp",
    }
)


def ledger_write(key: str, position: dict) -> None:
    """
    Persist decision metadata for one position to the ledger file.
    Called once at trade entry.  Existing entries for the same key are
    never overwritten — first write wins, matching the immutability rule.
    """
    trade_type = position.get("trade_type")
    if not trade_type or trade_type == "UNKNOWN":
        return  # nothing worth ledgering

    entry = {f: position[f] for f in _LEDGER_FIELDS if f in position}
    entry["_ledgered_at"] = position.get("open_time", "")

    _LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _ledger_lock:
        try:
            ledger = {}
            if _LEDGER_FILE.exists():
                raw = _LEDGER_FILE.read_text().strip()
                if raw:
                    ledger = json.loads(raw)
            if key in ledger:
                log.debug(f"ledger: {key} already recorded — skipping (first write wins)")
                return
            ledger[key] = entry
            tmp = _LEDGER_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(ledger, indent=2, default=str))
            os.replace(str(tmp), str(_LEDGER_FILE))
            log.info(f"ledger: recorded metadata for {key} (trade_type={trade_type})")
        except Exception as e:
            log.error(f"ledger: failed to write {key}: {e}")


def ledger_lookup(key: str, symbol: str = "", instrument: str = "") -> dict:
    """
    Retrieve metadata from the ledger.  Exact key first, then symbol+instrument scan.
    Returns empty dict if not found.
    """
    try:
        if not _LEDGER_FILE.exists():
            return {}
        raw = _LEDGER_FILE.read_text().strip()
        if not raw:
            return {}
        ledger = json.loads(raw)
        if key in ledger:
            return ledger[key]
        if symbol and instrument:
            for v in ledger.values():
                if (
                    v.get("symbol") == symbol
                    and v.get("instrument") == instrument
                    and v.get("trade_type")
                    and v["trade_type"] != "UNKNOWN"
                ):
                    return v
        return {}
    except Exception as e:
        log.error(f"ledger: lookup failed for {key}: {e}")
        return {}


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
                    log.info(f"trade_store: restored {len(data)} open position(s) from {_POSITIONS_FILE}")
                    return data
    except Exception as e:
        log.error(f"trade_store: failed to restore positions: {e}")
    return {}
