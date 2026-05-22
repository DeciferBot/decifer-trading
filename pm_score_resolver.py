"""
pm_score_resolver.py — Multi-source score resolution for the PME.

Single responsibility: given a symbol, its entry score, and the current-cycle
candidate_scores dict, return the best available (current_score, score_source).

Score sources (highest to lowest priority):
  1. CYCLE_CANDIDATES   — scores from the current scan pipeline.all_scored
  2. PM_SCORE_CACHE     — last known score persisted from a previous scan cycle
                         (data/pm_engine/score_cache.json)
  3. ENTRY_SCORE_FALLBACK — entry score; score_delta forced to 0

When a symbol falls through to ENTRY_SCORE_FALLBACK the caller should mark
data_quality = "DEGRADED_SCORE". The thesis will be THESIS_INTACT_DEGRADED
rather than THESIS_INTACT so degraded-data positions are distinguishable.
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
from typing import Any

log = logging.getLogger(__name__)

_CACHE_FILE = pathlib.Path("data/pm_engine/score_cache.json")
_lock   = threading.Lock()
_mem: dict[str, dict] = {}      # {symbol: {"score": float, "ts": str, "source": str}}
_loaded = False


# ── Public API ────────────────────────────────────────────────────────────────

def update_cache(
    candidate_scores: dict[str, float],
    source: str = "scan_cycle",
) -> None:
    """
    Persist fresh scores from the current cycle into the in-memory and
    on-disk score cache. No-op if candidate_scores is empty.
    """
    global _loaded
    if not candidate_scores:
        return
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _lock:
        for sym, score in candidate_scores.items():
            _mem[sym] = {"score": float(score), "ts": ts, "source": source}
        _loaded = True
    _persist()


def resolve(
    symbol: str,
    entry_score: float,
    candidate_scores: dict[str, float],
) -> tuple[float, str]:
    """
    Return (current_score, score_source).

    score_source is one of:
      "CYCLE_CANDIDATES"     — score came from the current scan cycle
      "PM_SCORE_CACHE"       — score came from the persistent cache
      "ENTRY_SCORE_FALLBACK" — no current score available; using entry_score
    """
    if symbol in candidate_scores:
        return float(candidate_scores[symbol]), "CYCLE_CANDIDATES"
    cached = _get_cached(symbol)
    if cached is not None:
        return cached, "PM_SCORE_CACHE"
    return float(entry_score) if entry_score else 0.0, "ENTRY_SCORE_FALLBACK"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_cached(symbol: str) -> float | None:
    _ensure_loaded()
    with _lock:
        entry = _mem.get(symbol)
    return float(entry["score"]) if entry else None


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            with _lock:
                _mem.update(data)
        _loaded = True
    except Exception as exc:
        log.debug("pm_score_resolver: cache load failed: %s", exc)


def _persist() -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            snapshot = dict(_mem)
        _CACHE_FILE.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except Exception as exc:
        log.debug("pm_score_resolver: cache persist failed: %s", exc)
