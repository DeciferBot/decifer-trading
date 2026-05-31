# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  conviction_cache.py                       ║
# ║   Intelligence Layer — conviction score cache manager        ║
# ╚══════════════════════════════════════════════════════════════╝
"""
conviction_cache.py — Manages conviction score caching and refresh scheduling.

Design principles:
  - Always serves immediately from cache (never blocks a request on a live FMP call)
  - Background thread refreshes stale scores without blocking the API
  - One stale-check per call to get() — refresh fires at most once per TTL window
  - Targeted rescore API for event-triggered recalibration (news, driver change, etc.)

Cache file: data/intelligence/conviction/scores.json
TTL: 30 minutes for price-sensitive dims, 24h for valuation/analyst
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("decifer.conviction_cache")

_BASE_DIR  = Path(os.path.dirname(os.path.abspath(__file__)))

# Prefer the project data dir; fall back to /tmp if the volume is read-only
# (the intelligence container mounts /app/data:ro in production)
def _resolve_cache_dir() -> Path:
    preferred = _BASE_DIR / "data" / "intelligence" / "conviction"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        test = preferred / ".write_test"
        test.write_text("ok")
        test.unlink()
        return preferred
    except OSError:
        fallback = Path("/tmp/decifer_conviction")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

_CACHE_DIR  = _resolve_cache_dir()
_CACHE_FILE = _CACHE_DIR / "scores.json"

_FULL_TTL   = 1800   # 30 minutes — price data changes meaningfully
_VALUATION_TTL = 86400  # 24 hours — DCF/analyst doesn't move intraday

_MAX_WORKERS = 4     # parallel FMP calls — conservative to avoid FMP rate limits

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_scores: dict[str, dict] = {}       # symbol -> ConvictionScore.to_dict()
_scores_ts: dict[str, float] = {}   # symbol -> unix timestamp of last score
_lock = threading.Lock()
_refresh_in_flight: set[str] = set()
_refresh_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_from_disk() -> None:
    global _scores, _scores_ts
    if not _CACHE_FILE.exists():
        return
    try:
        saved = json.loads(_CACHE_FILE.read_text())
        with _lock:
            for sym, entry in saved.get("scores", {}).items():
                _scores[sym] = entry
                ts_str = entry.get("ts", "")
                try:
                    _scores_ts[sym] = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    _scores_ts[sym] = 0.0
        log.info("conviction_cache: loaded %d scores from disk", len(_scores))
    except Exception as exc:
        log.warning("conviction_cache: disk load failed — %s", exc)


def _save_to_disk() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            payload = {
                "saved_at": datetime.now(UTC).isoformat(),
                "total": len(_scores),
                "scores": dict(_scores),
            }
        _CACHE_FILE.write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        log.warning("conviction_cache: disk save failed — %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get(symbol: str) -> dict | None:
    """
    Return the cached ConvictionScore dict for symbol, or None if not yet scored.
    Triggers a background refresh if the score is stale.
    """
    sym = symbol.upper()
    _ensure_loaded()

    with _lock:
        score = _scores.get(sym)
        ts = _scores_ts.get(sym, 0.0)

    if score is None:
        _trigger_rescore([sym], reason="cache_miss")
        return None

    age = time.time() - ts
    if age > _FULL_TTL:
        _trigger_rescore([sym], reason="stale")

    return score


def get_all() -> dict[str, dict]:
    """Return all cached scores. Stale scores are still returned; background refresh fires."""
    _ensure_loaded()
    with _lock:
        return dict(_scores)


def trigger_rescore(symbols: list[str], reason: str = "manual") -> None:
    """
    Public entry point for event-triggered rescoring.
    Called by news.py, live_driver_resolver.py, catalyst_engine.py on material events.
    Non-blocking — fires background thread.
    """
    _trigger_rescore([s.upper() for s in symbols], reason=reason)


def refresh_all(symbols: list[str]) -> None:
    """
    Synchronous full-universe rescore. Called at scheduled rescore times.
    Blocks until all symbols are scored. Updates cache and persists to disk.
    """
    from conviction_engine import fetch_price_changes, fetch_analyst_changes, score_symbol

    log.info("conviction_cache: full rescore starting for %d symbols", len(symbols))
    t0 = time.time()

    # Batch-fetch shared data once
    price_changes    = fetch_price_changes(symbols)
    analyst_changes  = fetch_analyst_changes(symbols)

    results: dict[str, dict] = {}

    def _score_one(sym: str) -> tuple[str, dict | None]:
        try:
            cs = score_symbol(sym, price_changes=price_changes,
                              analyst_changes=analyst_changes)
            return sym, cs.to_dict()
        except Exception as exc:
            log.warning("conviction_cache: score_symbol(%s) failed — %s", sym, exc)
            return sym, None

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_score_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym, result = future.result()
            if result is not None:
                results[sym] = result

    # Apply relative tiers — rank all symbols by composite, tier by percentile.
    # Conviction is relative: top 20% = HIGH regardless of absolute market level.
    _apply_relative_tiers(results)

    now = time.time()
    with _lock:
        for sym, entry in results.items():
            _scores[sym] = entry
            _scores_ts[sym] = now

    _save_to_disk()
    elapsed = time.time() - t0
    log.info("conviction_cache: full rescore complete — %d scored in %.1fs", len(results), elapsed)


def _apply_relative_tiers(results: dict[str, dict]) -> None:
    """
    Re-tier all scored symbols by percentile rank within the batch.
    Mutates results in place — adds 'tier' (relative) and 'absolute_tier'.
    """
    from conviction_engine import tier_from_percentile
    ranked = sorted(results.items(), key=lambda x: -(x[1].get("composite") or 0))
    total = len(ranked)
    for rank, (sym, entry) in enumerate(ranked, start=1):
        entry["absolute_tier"] = entry.get("tier", "DORMANT")
        entry["tier"] = tier_from_percentile(rank, total)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_loaded = False
_load_lock = threading.Lock()


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    with _load_lock:
        if not _loaded:
            _load_from_disk()
            _loaded = True


def _trigger_rescore(symbols: list[str], reason: str) -> None:
    """Fire background rescore for symbols not already in flight."""
    to_rescore = []
    with _refresh_lock:
        for sym in symbols:
            if sym not in _refresh_in_flight:
                _refresh_in_flight.add(sym)
                to_rescore.append(sym)

    if not to_rescore:
        return

    def _run():
        from conviction_engine import fetch_price_changes, fetch_analyst_changes, score_symbol
        log.debug("conviction_cache: background rescore %s symbols — reason=%s",
                  len(to_rescore), reason)
        try:
            price_changes   = fetch_price_changes(to_rescore)
            analyst_changes = fetch_analyst_changes(to_rescore)

            for sym in to_rescore:
                try:
                    cs = score_symbol(sym, price_changes=price_changes,
                                      analyst_changes=analyst_changes)
                    with _lock:
                        _scores[sym] = cs.to_dict()
                        _scores_ts[sym] = time.time()
                except Exception as exc:
                    log.warning("conviction_cache: bg rescore %s failed — %s", sym, exc)

            _save_to_disk()
        finally:
            with _refresh_lock:
                for sym in to_rescore:
                    _refresh_in_flight.discard(sym)

    threading.Thread(target=_run, daemon=True).start()
