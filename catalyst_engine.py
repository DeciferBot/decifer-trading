# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  catalyst_engine.py                        ║
# ║   Catalyst Intelligence Service                              ║
# ║                                                              ║
# ║   Unified M&A signal layer. Replaces the split between:     ║
# ║     - CatalystSentinel (real-time reactor, Session 1: kept) ║
# ║     - Chief's auto_runner.py (batch scoring pipeline)       ║
# ║                                                              ║
# ║   Session 1: WatchlistStore + 4 scoring runners.            ║
# ║   Session 2: + news/EDGAR monitors + trigger firing.        ║
# ║   Session 3: + score-threshold trigger + IC context fields. ║
# ║                                                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from config import (
    CATALYST_DIR,
    CATALYST_SCREEN_INTERVAL,
    EDGAR_POLL_INTERVAL,
    OPTIONS_ANOMALY_INTERVAL,
    SENTIMENT_SCORER_INTERVAL,
)

log = logging.getLogger("decifer.catalyst_engine")
_UTC = timezone.utc


# ═══════════════════════════════════════════════════════════════
# WATCHLIST STORE
# ═══════════════════════════════════════════════════════════════

class WatchlistStore:
    """
    Thread-safe in-memory store of scored M&A candidates.

    Loaded from today's candidates file on engine startup.
    Refreshed after every scoring runner updates the file.
    Exposes get(ticker) for O(1) enrichment lookups at trigger time.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._candidates: dict[str, dict] = {}   # ticker → candidate dict
        self._loaded_at: datetime | None = None

    def load_from_file(self) -> int:
        """
        Load today's candidates file into memory.
        Returns number of candidates loaded (0 if file doesn't exist yet).
        """
        today = datetime.now(_UTC).strftime("%Y-%m-%d")
        path = CATALYST_DIR / f"candidates_{today}.json"
        if not path.exists():
            log.info("WatchlistStore: no candidates file for today yet — store empty until first screen")
            return 0
        try:
            payload = json.loads(path.read_text())
            candidates = payload.get("candidates", [])
            with self._lock:
                self._candidates = {
                    c["ticker"].upper(): c
                    for c in candidates
                    if c.get("ticker")
                }
                self._loaded_at = datetime.now(_UTC)
            log.info(f"WatchlistStore: loaded {len(self._candidates)} candidates from {path.name}")
            return len(self._candidates)
        except Exception as exc:
            log.error(f"WatchlistStore: failed to load candidates file: {exc}")
            return 0

    def refresh(self) -> None:
        """Reload from file. Called after each runner writes an update."""
        self.load_from_file()

    def get(self, ticker: str) -> dict | None:
        """Return full candidate dict for ticker, or None if not tracked."""
        with self._lock:
            return self._candidates.get(ticker.upper())

    def all_tickers(self) -> list[str]:
        """Return list of all tracked tickers (for options/sentiment runners)."""
        with self._lock:
            return list(self._candidates.keys())

    def count(self) -> int:
        with self._lock:
            return len(self._candidates)

    def snapshot(self) -> list[dict]:
        """Sorted candidate list for stats — highest catalyst_score first."""
        with self._lock:
            return sorted(
                self._candidates.values(),
                key=lambda c: c.get("catalyst_score", 0),
                reverse=True,
            )


# ═══════════════════════════════════════════════════════════════
# SIZE MULTIPLIER
# ═══════════════════════════════════════════════════════════════

def compute_size_multiplier(catalyst_score: float | None) -> float:
    """
    Translate catalyst score into a position size multiplier.
    Applied to the base catalyst_risk_multiplier already set in config.

    Score bands (note: fundamentals-only max is 3.5, so 5.0+ requires
    at least one additional scoring tier):
        < 5.0  → 0.75×  (below threshold — standard sentinel sizing)
        5.0–6.9 → 1.00× (threshold met)
        7.0–8.4 → 1.10× (strong multi-tier conviction)
        ≥ 8.5  → 1.25× (high conviction)
    """
    if catalyst_score is None:
        return 0.75
    if catalyst_score >= 8.5:
        return 1.25
    if catalyst_score >= 7.0:
        return 1.10
    if catalyst_score >= 5.0:
        return 1.00
    return 0.75


# ═══════════════════════════════════════════════════════════════
# CATALYST ENGINE
# ═══════════════════════════════════════════════════════════════

class CatalystEngine:
    """
    Unified Catalyst Intelligence Service.

    Runs four background daemon threads (fundamental screen, EDGAR monitor,
    options anomaly scan, sentiment scorer) and maintains a live in-memory
    WatchlistStore of scored M&A candidates.

    Session 1 — intelligence layer only:
      - WatchlistStore pre-loaded from today's candidates file on startup
      - 4 scoring runners keep scores current throughout the day
      - get_candidate(ticker) available for trigger enrichment

    Session 2 — real-time layer added:
      - news_monitor thread (Yahoo RSS keyword matching)
      - edgar_monitor thread (SEC RSS real-time)
      - CatalystSentinel retired

    Usage:
        engine = CatalystEngine(get_universe_fn=lambda: [...symbols...])
        engine.start()
        ...
        candidate = engine.get_candidate("AAPL")
    """

    def __init__(
        self,
        get_universe_fn: Callable,
        on_trigger_fn: Callable | None = None,
    ) -> None:
        self.get_universe = get_universe_fn
        self.on_trigger = on_trigger_fn          # wired in Session 2
        self.store = WatchlistStore()
        self._running = False
        self._threads: list[threading.Thread] = []

        self.stats: dict = {
            "status": "stopped",
            "candidates": 0,
            "last_screen":    None,
            "last_edgar":     None,
            "last_options":   None,
            "last_sentiment": None,
            "screen_runs":    0,
            "edgar_runs":     0,
            "options_runs":   0,
            "sentiment_runs": 0,
        }

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            log.warning("CatalystEngine already running")
            return

        # Pre-load today's candidates before runners start so get_candidate()
        # returns useful data immediately (not empty until first 4-hour screen).
        loaded = self.store.load_from_file()
        self.stats["candidates"] = loaded

        self._running = True
        self.stats["status"] = "running"

        runner_specs = [
            ("fundamental", self._fundamental_runner, CATALYST_SCREEN_INTERVAL),
            ("edgar",       self._edgar_runner,       EDGAR_POLL_INTERVAL),
            ("options",     self._options_runner,     OPTIONS_ANOMALY_INTERVAL),
            ("sentiment",   self._sentiment_runner,   SENTIMENT_SCORER_INTERVAL),
        ]
        for name, target, interval in runner_specs:
            t = threading.Thread(
                target=target,
                args=(interval,),
                name=f"CatalystEngine:{name}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
            log.info(f"CatalystEngine: {name} runner started (interval={interval}s)")

        log.info(
            f"⚡ CatalystEngine started | "
            f"{loaded} candidates pre-loaded | "
            f"screen={CATALYST_SCREEN_INTERVAL}s "
            f"edgar={EDGAR_POLL_INTERVAL}s "
            f"options={OPTIONS_ANOMALY_INTERVAL}s "
            f"sentiment={SENTIMENT_SCORER_INTERVAL}s"
        )

    def stop(self) -> None:
        self._running = False
        self.stats["status"] = "stopped"
        log.info("CatalystEngine stopped")

    # ── Public API ──────────────────────────────────────────────

    def get_candidate(self, ticker: str) -> dict | None:
        """
        Return the scored candidate dict for ticker, or None if not tracked.

        Called at trigger time to enrich trigger payloads with screener context.
        O(1) in-memory lookup — no file I/O on the hot path.
        """
        return self.store.get(ticker)

    def get_size_multiplier(self, ticker: str) -> float:
        """
        Return the size multiplier for ticker based on its current catalyst score.
        Returns 0.75 (standard sentinel sizing) if ticker is not in watchlist.
        """
        candidate = self.store.get(ticker)
        score = candidate.get("catalyst_score") if candidate else None
        return compute_size_multiplier(score)

    def get_stats(self) -> dict:
        self.stats["candidates"] = self.store.count()
        return self.stats

    # ── Scoring runners ─────────────────────────────────────────

    def _fundamental_runner(self, interval: int) -> None:
        """
        Runs the M&A target fundamental screen every `interval` seconds.
        Scans ~500 S&P 500 tickers + watchlist against 5 criteria.
        Writes candidates_YYYY-MM-DD.json, then refreshes WatchlistStore.
        """
        from signals.catalyst_screen import run_screen

        while self._running:
            try:
                log.info("CatalystEngine [fundamental]: starting screen ...")
                candidates = run_screen()
                self.store.refresh()
                self.stats["candidates"] = self.store.count()
                self.stats["last_screen"] = datetime.now(_UTC).strftime("%H:%M UTC")
                self.stats["screen_runs"] += 1
                log.info(
                    f"CatalystEngine [fundamental]: done — "
                    f"{len(candidates)} candidates | "
                    f"store={self.store.count()}"
                )
            except Exception as exc:
                log.error(f"CatalystEngine [fundamental] error: {exc}", exc_info=True)
            time.sleep(interval)

    def _edgar_runner(self, interval: int) -> None:
        """
        Polls SEC EDGAR RSS feeds (SC 13D / SC 13G / Form 4) every `interval` seconds.
        Merges EDGAR scores into the candidates file, refreshes WatchlistStore.

        Staggered 60s after startup so the fundamental screen runs first and
        provides the watchlist for EDGAR cross-referencing.
        """
        from signals.edgar_monitor import run_edgar_poll, merge_into_candidates

        time.sleep(60)   # wait for first fundamental screen to populate store
        while self._running:
            try:
                log.info("CatalystEngine [edgar]: polling SEC EDGAR ...")
                events = run_edgar_poll(watchlist_tickers=self.store.all_tickers())
                updated = merge_into_candidates(events)
                self.store.refresh()
                self.stats["last_edgar"] = datetime.now(_UTC).strftime("%H:%M UTC")
                self.stats["edgar_runs"] += 1
                log.info(
                    f"CatalystEngine [edgar]: done — "
                    f"{len(events)} events | {updated} candidates updated"
                )
            except Exception as exc:
                log.error(f"CatalystEngine [edgar] error: {exc}", exc_info=True)
            time.sleep(interval)

    def _options_runner(self, interval: int) -> None:
        """
        Scans options chains for anomalies (OTM call dominance, IV spikes, P/C skew)
        across all candidates in the WatchlistStore.
        Merges options scores into the candidates file, refreshes WatchlistStore.

        Staggered 120s after startup — needs candidates from fundamental screen.
        """
        from signals.options_anomaly import (
            run_anomaly_scan,
            merge_into_candidates as merge_options,
        )

        time.sleep(120)
        while self._running:
            try:
                tickers = self.store.all_tickers()
                if not tickers:
                    log.info("CatalystEngine [options]: no candidates yet — skipping")
                    time.sleep(interval)
                    continue
                log.info(f"CatalystEngine [options]: scanning {len(tickers)} tickers ...")
                results = run_anomaly_scan(tickers)
                updated = merge_options(results)
                self.store.refresh()
                self.stats["last_options"] = datetime.now(_UTC).strftime("%H:%M UTC")
                self.stats["options_runs"] += 1
                log.info(f"CatalystEngine [options]: done — {updated} candidates updated")
            except Exception as exc:
                log.error(f"CatalystEngine [options] error: {exc}", exc_info=True)
            time.sleep(interval)

    def _sentiment_runner(self, interval: int) -> None:
        """
        Scores sentiment for all candidates using Yahoo RSS + Finviz + Claude + FinBERT.
        Merges sentiment scores into the candidates file, refreshes WatchlistStore.

        Staggered 180s after startup — needs candidates from fundamental screen.
        """
        from signals.sentiment_scorer import (
            run_sentiment_scan,
            merge_into_candidates as merge_sentiment,
        )

        time.sleep(180)
        while self._running:
            try:
                tickers = self.store.all_tickers()
                if not tickers:
                    log.info("CatalystEngine [sentiment]: no candidates yet — skipping")
                    time.sleep(interval)
                    continue
                log.info(f"CatalystEngine [sentiment]: scoring {len(tickers)} tickers ...")
                results = run_sentiment_scan(tickers)
                updated = merge_sentiment(results)
                self.store.refresh()
                self.stats["last_sentiment"] = datetime.now(_UTC).strftime("%H:%M UTC")
                self.stats["sentiment_runs"] += 1
                log.info(f"CatalystEngine [sentiment]: done — {updated} candidates updated")
            except Exception as exc:
                log.error(f"CatalystEngine [sentiment] error: {exc}", exc_info=True)
            time.sleep(interval)
