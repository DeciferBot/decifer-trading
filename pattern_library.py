# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  pattern_library.py                        ║
# ║   Empirical pattern store. Rules that emerge from           ║
# ║   experience, not from anticipation.                        ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Single responsibility: record trade outcomes against market observations
and surface the most relevant historical patterns to the intelligence layer.

How it works:
  1. At trade entry: record_entry() stores the market observation fingerprint
     alongside the signal context and trade_type assigned by the intelligence layer.
  2. At trade close: record_outcome() attaches the P&L and exit reason.
  3. At intelligence call: get_relevant_patterns() retrieves the N most similar
     historical observations so Opus can reason: "last N times the market looked
     like this, here is what worked and what did not."

Similarity: cosine similarity on a compact numeric fingerprint derived from
the observation (asset changes, VIX level, sector rotation). No hardcoded
relationship rules — the intelligence layer discovers what matters.

Storage: data/pattern_library.json  (append-only log; never rewritten in full)
"""

from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("decifer.pattern_library")

LIBRARY_PATH = Path("data/pattern_library.json")
_lock = threading.Lock()

# ── Fingerprint ───────────────────────────────────────────────────────────────
# A compact numeric vector derived from a MarketObservation.
# Each element is one observable fact. Order is fixed — never change it,
# as it would break similarity comparisons against stored entries.

_FINGERPRINT_KEYS = [
    # Equity direction
    "SPY_1d", "QQQ_1d", "IWM_1d",
    # Macro instruments
    "GLD_1d", "USO_1d", "TLT_1d", "HYG_1d",
    # FX
    "UUP_1d", "FXY_1d",
    # VIX
    "vix_level", "vix_change",
    # Sector rotation (vs SPY)
    "XLK_rel", "XLF_rel", "XLE_rel", "XLV_rel", "XLI_rel", "XLU_rel", "XLP_rel",
    # MA context (binary: 1 = above, 0 = below)
    "SPY_above_ma20", "SPY_above_ma50",
    "QQQ_above_ma20", "TLT_above_ma20",
]


def _build_fingerprint(observation) -> list[float]:
    """
    Extract a numeric vector from a MarketObservation.
    Missing values default to 0.0 — partial observations are still usable.
    """
    fp: list[float] = []
    assets      = observation.assets if observation else {}
    sector_rel  = observation.sector_vs_spy if observation else {}

    for key in _FINGERPRINT_KEYS:
        if key == "vix_level":
            fp.append(float(observation.vix) if observation else 0.0)
        elif key == "vix_change":
            fp.append(float(observation.vix_change_1d) if observation else 0.0)
        elif "_rel" in key:
            sym = key.replace("_rel", "")
            fp.append(float(sector_rel.get(sym, 0.0)))
        elif key.endswith("_above_ma20") or key.endswith("_above_ma50"):
            parts = key.rsplit("_above_", 1)
            sym   = parts[0]
            field = "above_ma20" if "ma20" in key else "above_ma50"
            snap  = assets.get(sym)
            fp.append(1.0 if (snap and getattr(snap, field, False)) else 0.0)
        else:
            # e.g. "SPY_1d" → assets["SPY"].change_1d
            sym, attr = key.rsplit("_", 1)
            snap = assets.get(sym)
            fp.append(float(getattr(snap, f"change_{attr}", 0.0)) if snap else 0.0)

    return fp


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Storage helpers ───────────────────────────────────────────────────────────

def _load() -> dict:
    if LIBRARY_PATH.exists():
        try:
            return json.loads(LIBRARY_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_PATH.write_text(json.dumps(data, indent=2))


# ── Public API ────────────────────────────────────────────────────────────────

def record_entry(
    observation,            # MarketObservation from market_observer
    symbol:       str,
    direction:    str,
    trade_type:   str,      # SCALP | SWING | HOLD
    conviction:   float,
    market_read:  str,      # intelligence layer's free-form interpretation
    signal_score: float,
) -> str:
    """
    Record a trade entry against the current market observation.
    Returns pattern_id — store this on the position for outcome linkage.
    """
    pattern_id = str(uuid.uuid4())[:8]

    entry = {
        "pattern_id":    pattern_id,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "symbol":        symbol,
        "direction":     direction,
        "trade_type":    trade_type,
        "conviction":    conviction,
        "signal_score":  signal_score,
        "market_read":   market_read[:500] if market_read else "",
        "fingerprint":   _build_fingerprint(observation),
        # Outcome fields — filled by record_outcome()
        "pnl":           None,
        "pnl_pct":       None,
        "exit_reason":   None,
        "outcome_at":    None,
    }

    with _lock:
        data = _load()
        data[pattern_id] = entry
        _save(data)

    log.debug(f"pattern_library: recorded entry {pattern_id} {symbol} {trade_type}")
    return pattern_id


def record_outcome(
    pattern_id: str,
    pnl:        float,
    pnl_pct:    float,
    exit_reason: str,
) -> None:
    """
    Attach trade outcome to a pattern entry.
    Called from the learning/close loop.
    """
    if not pattern_id:
        return
    try:
        with _lock:
            data = _load()
            if pattern_id not in data:
                return
            data[pattern_id].update({
                "pnl":        pnl,
                "pnl_pct":   pnl_pct,
                "exit_reason": exit_reason,
                "outcome_at": datetime.now(timezone.utc).isoformat(),
            })
            _save(data)
        log.debug(f"pattern_library: outcome recorded {pattern_id} pnl={pnl:.2f}")
    except Exception as exc:
        log.warning(f"pattern_library: record_outcome failed {pattern_id}: {exc}")


def get_relevant_patterns(
    observation,
    n: int = 20,
) -> list[dict]:
    """
    Return the N most similar completed patterns to the current observation.
    Only returns patterns that have an outcome (pnl is not None).

    The intelligence layer includes these in its prompt so Opus can reason:
    "last N times the market looked like this, here is what happened."
    """
    if observation is None:
        return []

    current_fp = _build_fingerprint(observation)

    try:
        with _lock:
            data = _load()

        completed = [
            r for r in data.values()
            if r.get("pnl") is not None and r.get("fingerprint")
        ]

        if not completed:
            return []

        scored = []
        for r in completed:
            sim = _cosine_similarity(current_fp, r["fingerprint"])
            scored.append((sim, r))

        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:n]]

    except Exception as exc:
        log.warning(f"pattern_library: get_relevant_patterns failed: {exc}")
        return []


def get_thesis_performance(min_samples: int = 3) -> list[dict]:
    """
    Aggregate completed patterns by (trade_type, thesis_class) and return
    win rate and avg PnL percentage for each combination with at least
    min_samples records.

    thesis_class is extracted from structured exit_reason strings that contain
    a 'thesis:...' token (e.g. 'sl_hit | SCALP | ... | thesis:noise_stop').
    Falls back to the raw exit_reason if no thesis token is present.

    Used by the intelligence layer to feed historical reasoning quality back
    into the classification prompt — closing the entry→exit feedback loop.
    """
    import re

    try:
        with _lock:
            data = _load()

        completed = [r for r in data.values() if r.get("pnl") is not None]
        if not completed:
            return []

        groups: dict[tuple, list] = {}
        for r in completed:
            tt          = r.get("trade_type", "UNKNOWN")
            exit_reason = r.get("exit_reason") or ""
            m           = re.search(r"thesis:(\w+)", exit_reason)
            thesis_cls  = m.group(1) if m else (exit_reason[:30] or "unknown")
            key = (tt, thesis_cls)
            groups.setdefault(key, []).append(r)

        result = []
        for (tt, thesis_cls), records in groups.items():
            if len(records) < min_samples:
                continue
            wins        = [r for r in records if (r.get("pnl") or 0) > 0]
            avg_pnl_pct = sum(r.get("pnl_pct") or 0.0 for r in records) / len(records) * 100
            result.append({
                "trade_type":   tt,
                "thesis_class": thesis_cls,
                "count":        len(records),
                "win_rate":     round(len(wins) / len(records), 2),
                "avg_pnl_pct":  round(avg_pnl_pct, 2),
            })

        result.sort(key=lambda x: -x["count"])
        return result

    except Exception as exc:
        log.warning(f"pattern_library: get_thesis_performance failed: {exc}")
        return []


def get_summary_stats() -> dict:
    """
    High-level stats for dashboard / logging.
    Returns: total, completed, win_rate by trade_type.
    """
    try:
        with _lock:
            data = _load()

        total     = len(data)
        completed = [r for r in data.values() if r.get("pnl") is not None]
        wins      = [r for r in completed if (r.get("pnl") or 0) > 0]

        by_type: dict[str, dict] = {}
        for r in completed:
            tt = r.get("trade_type", "UNKNOWN")
            if tt not in by_type:
                by_type[tt] = {"count": 0, "wins": 0}
            by_type[tt]["count"] += 1
            if (r.get("pnl") or 0) > 0:
                by_type[tt]["wins"] += 1

        return {
            "total":     total,
            "completed": len(completed),
            "win_rate":  round(len(wins) / len(completed), 3) if completed else None,
            "by_type":   by_type,
        }
    except Exception as exc:
        log.warning(f"pattern_library: get_summary_stats failed: {exc}")
        return {}
