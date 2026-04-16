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

# File written by _edgar_runner (via signals/edgar_monitor.py) — rolling 7-day window.
# _edgar_monitor reads from here instead of polling SEC directly.
_EDGAR_FILE = CATALYST_DIR / "edgar_events.json"


# ── M&A keyword lists ──────────────────────────────────────────────────────────
# Inlined from catalyst_sentinel — CatalystEngine is self-contained; no shared
# module-level state with the retired CatalystSentinel process.

MA_ANNOUNCEMENT_KEYWORDS = {
    # Definitive deal language
    "to be acquired", "acquisition agreement", "merger agreement",
    "definitive agreement", "definitive merger", "agreed to be acquired",
    "agreed to acquire", "deal to acquire", "agree to buy",
    # Offer language
    "tender offer", "per share in cash", "per share in an all-cash",
    "takeover bid", "unsolicited bid", "hostile takeover",
    "going private", "take-private", "management buyout", "mbo",
    # Strategic language
    "strategic alternatives", "exploring a sale", "sale process",
    "received a buyout", "received an offer to acquire",
}
MA_SOFT_KEYWORDS = {
    "buyout", "takeover", "acquired by", "buys", "purchase price",
    "premium", "acquirer", "strategic review", "due diligence",
    "merger talks", "acquisition talks", "in talks to acquire",
}
MA_NEGATIVE_KEYWORDS = {
    "acquires technology", "acquires talent", "acquires domain",
    "acquires content", "acquires license", "acquires rights",
}


def _check_ma_keywords(headline: str) -> tuple[bool, str, bool]:
    """
    Check a headline for M&A keywords.
    Returns (is_match, matched_keyword, is_definitive).
    """
    text = headline.lower()
    for neg in MA_NEGATIVE_KEYWORDS:
        if neg in text:
            return False, "", False
    for kw in MA_ANNOUNCEMENT_KEYWORDS:
        if kw in text:
            is_def = any(
                x in kw
                for x in ["definitive", "agreement", "per share", "tender offer",
                           "going private", "agreed to", "merger agreement"]
            )
            return True, kw, is_def
    soft_hits = [kw for kw in MA_SOFT_KEYWORDS if kw in text]
    if len(soft_hits) >= 2:
        return True, " + ".join(soft_hits[:2]), False
    return False, "", False


def _build_news_trigger(news_hit: dict) -> dict:
    """Build a standardised catalyst trigger dict from a Yahoo RSS news hit."""
    return {
        "symbol":            news_hit["symbol"],
        "trigger_type":      "ma_announcement",
        "headlines":         [news_hit["headline"]],
        "keyword":           news_hit["keyword"],
        "is_definitive":     news_hit["is_definitive"],
        "direction":         "BULLISH",
        "urgency":           "CRITICAL" if news_hit["is_definitive"] else "HIGH",
        "age_hours":         news_hit["age_hours"],
        "source":            news_hit["source"],
        "claude_sentiment":  "BULLISH",
        "claude_confidence": 8 if news_hit["is_definitive"] else 5,
        "claude_catalyst":   f"M&A signal: {news_hit['keyword']}",
        "triggered_at":      datetime.now(_UTC).isoformat(),
    }


def _build_edgar_trigger(edgar_event: dict) -> dict:
    """Build a standardised catalyst trigger dict from a resolved EDGAR event."""
    form    = edgar_event["form_type"]
    ticker  = edgar_event.get("ticker") or ""
    company = edgar_event.get("company_name", "")

    if form == "SC 13D":
        urgency, confidence = "HIGH", 6
        catalyst = f"Activist investor SC 13D filed: {company}"
    elif form == "SC 13G":
        urgency, confidence = "MODERATE", 4
        catalyst = f"Passive investor SC 13G (>5% stake): {company}"
    else:  # Form 4
        urgency, confidence = "MODERATE", 3
        catalyst = f"Insider Form 4 filing: {company}"

    return {
        "symbol":            ticker,
        "trigger_type":      f"edgar_{form.lower().replace(' ', '')}",
        "headlines":         [edgar_event["title"]],
        "keyword":           form,
        "is_definitive":     False,
        "direction":         "BULLISH",
        "urgency":           urgency,
        "age_hours":         0,
        "source":            "sec_edgar",
        "edgar_link":        edgar_event.get("link", ""),
        "claude_sentiment":  "BULLISH",
        "claude_confidence": confidence,
        "claude_catalyst":   catalyst,
        "triggered_at":      datetime.now(_UTC).isoformat(),
    }


def _build_congressional_trigger(symbol: str, data: dict) -> dict:
    """Build a standardised catalyst trigger from congressional trade data."""
    sentiment = data.get("net_sentiment", "BUYING")
    politicians = ", ".join(data.get("politicians", [])[:3]) or "unknown"
    buy_count = data.get("buy_count", 0)
    direction = "BULLISH" if sentiment == "BUYING" else "BEARISH"
    catalyst = (
        f"Congressional {sentiment.lower()}: {buy_count} purchase(s) by {politicians}"
    )
    return {
        "symbol":            symbol.upper(),
        "trigger_type":      "congressional_trade",
        "headlines":         [catalyst],
        "keyword":           "congressional_trade",
        "is_definitive":     False,
        "direction":         direction,
        "urgency":           "MODERATE",
        "age_hours":         0,
        "source":            "fmp_congressional",
        "claude_sentiment":  direction,
        "claude_confidence": 5,
        "claude_catalyst":   catalyst,
        "triggered_at":      datetime.now(_UTC).isoformat(),
    }


def _trigger_confidence(score: float | None) -> str:
    """Translate catalyst_score into a confidence tier for IC logging."""
    if score is None:  return "speculative"
    if score >= 8.5:   return "high"
    if score >= 6.5:   return "medium"
    if score >= 5.0:   return "low"
    return "speculative"
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
        self._first_seen: dict[str, str] = {}    # ticker → ISO datetime of first load

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
            now_iso = datetime.now(_UTC).isoformat()
            with self._lock:
                self._candidates = {
                    c["ticker"].upper(): c
                    for c in candidates
                    if c.get("ticker")
                }
                self._loaded_at = datetime.now(_UTC)
                # Populate first_seen only for tickers not yet tracked
                for ticker in self._candidates:
                    if ticker not in self._first_seen:
                        self._first_seen[ticker] = now_iso
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

    def days_in_screener(self, ticker: str) -> int:
        """Days since ticker first appeared in the WatchlistStore this session."""
        first = self._first_seen.get(ticker.upper())
        if not first:
            return 0
        try:
            delta = datetime.now(_UTC) - datetime.fromisoformat(first)
            return max(0, delta.days)
        except Exception:
            return 0

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
    Applied on top of catalyst_risk_multiplier in handle_catalyst_trigger.

    For news/EDGAR triggers: score never reduces sizing — only boosts it.
    Partial scores (fundamentals-only max = 3.5) stay at 1.0× baseline.
    Score-threshold trigger (Session 3) gates on ≥ 5.0.

        None / < 5.0  → 1.00× (baseline — no change from current sentinel)
        5.0 – 6.9     → 1.00× (threshold met, screener_context passed to agents)
        7.0 – 8.4     → 1.10× (strong multi-tier conviction)
        ≥ 8.5         → 1.25× (high conviction)
    """
    if catalyst_score is None or catalyst_score < 7.0:
        return 1.00
    if catalyst_score >= 8.5:
        return 1.25
    return 1.10


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
        from config import CONFIG
        from news_infrastructure import HeadlineDeduplicator, SymbolCooldown

        self.get_universe = get_universe_fn
        self.on_trigger = on_trigger_fn
        self.store = WatchlistStore()
        self._running = False
        self._threads: list[threading.Thread] = []

        # Real-time monitor dedup — instance-level so engine is self-contained
        self._headline_dedup = HeadlineDeduplicator(max_size=5000)
        self._seen_edgar_events: set[str] = set()
        self._threshold_fired: set[str] = set()   # tickers that fired score-threshold trigger this session
        self._cooldown = SymbolCooldown(
            cooldown_minutes=CONFIG.get("catalyst_cooldown_minutes", 60)
        )

        self.stats: dict = {
            "status": "stopped",
            "candidates": 0,
            "last_screen":         None,
            "last_edgar":          None,
            "last_options":        None,
            "last_sentiment":      None,
            "last_news_poll":      None,
            "last_edgar_monitor":  None,
            "last_trigger":        None,
            "screen_runs":         0,
            "edgar_runs":          0,
            "options_runs":        0,
            "sentiment_runs":      0,
            "news_polls":            0,
            "edgar_monitor_polls":   0,
            "score_threshold_runs":  0,
            "last_score_threshold":  None,
            "triggers_fired":        0,
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

        from config import CONFIG

        news_interval  = CONFIG.get("catalyst_news_poll_seconds", 60)
        edgar_interval = CONFIG.get("catalyst_edgar_poll_seconds", 600)

        score_threshold_interval = CONFIG.get("catalyst_score_threshold_interval", 300)

        runner_specs = [
            ("fundamental",      self._fundamental_runner,      CATALYST_SCREEN_INTERVAL),
            ("edgar_scorer",     self._edgar_runner,            EDGAR_POLL_INTERVAL),
            ("options",          self._options_runner,          OPTIONS_ANOMALY_INTERVAL),
            ("sentiment",        self._sentiment_runner,        SENTIMENT_SCORER_INTERVAL),
            ("news_monitor",     self._news_monitor,            news_interval),
            ("edgar_monitor",    self._edgar_monitor,           edgar_interval),
            ("congressional",    self._congressional_monitor,   21600),  # 6h — data is lagged
            ("score_threshold",  self._score_threshold_checker, score_threshold_interval),
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
            log.info(f"CatalystEngine: {name} started (interval={interval}s)")

        log.info(
            f"⚡ CatalystEngine started | "
            f"{loaded} candidates pre-loaded | "
            f"screen={CATALYST_SCREEN_INTERVAL}s "
            f"edgar={EDGAR_POLL_INTERVAL}s "
            f"options={OPTIONS_ANOMALY_INTERVAL}s "
            f"sentiment={SENTIMENT_SCORER_INTERVAL}s | "
            f"news={news_interval}s edgar_rt={edgar_interval}s"
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
        Returns 1.0 (baseline — no change) if ticker is not in watchlist.
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

    # ── Real-time monitors (Session 2) ──────────────────────────

    def _fire(self, trigger: dict) -> None:
        """
        Enrich trigger with WatchlistStore context + size_multiplier, fire callback.
        O(1) in-memory lookup — no file I/O on the hot path.
        """
        if not self.on_trigger:
            return

        sym = trigger.get("symbol", "")
        self._cooldown.set_cooldown(sym)

        candidate = self.store.get(sym) if sym else None
        if candidate:
            catalyst_score = candidate.get("catalyst_score", 0)
            trigger["screener_context"] = {
                "catalyst_score":        catalyst_score,
                "fundamental_score":     candidate.get("fundamental_score", 0),
                "options_anomaly_score": candidate.get("options_anomaly_score", 0),
                "edgar_score":           candidate.get("edgar_score", 0),
                "sentiment_score":       candidate.get("sentiment_score", 0.0),
                "all_tiers_scored": (
                    candidate.get("options_anomaly_score", 0) > 0
                    and candidate.get("edgar_score", 0) > 0
                    and candidate.get("sentiment_score", 0.0) > 0
                ),
                "flags": candidate.get("flags", []),
            }
            trigger["size_multiplier"] = compute_size_multiplier(catalyst_score)
        else:
            catalyst_score = None
            trigger["size_multiplier"] = 1.0  # not pre-identified — baseline sizing

        # ── IC context — persisted into the trade record via agent_outputs ───
        # Consumed by _execute_trigger_buy → execute_buy → log_order.
        # _announcement_lag_hours and _trigger_type_detail are set by each
        # monitor before calling _fire() and popped here (not passed downstream).
        trigger["ic_context"] = {
            "trigger_source":          trigger.get("trigger_type", "unknown"),
            "catalyst_score_at_entry": catalyst_score,
            "size_multiplier_applied": trigger["size_multiplier"],
            "days_in_screener":        self.store.days_in_screener(sym),
            "trigger_confidence":      _trigger_confidence(catalyst_score),
            "announcement_lag_hours":  trigger.pop("_announcement_lag_hours", None),
            "trigger_type_detail":     trigger.pop("_trigger_type_detail",
                                                   trigger.get("trigger_type", "")),
        }

        self.stats["triggers_fired"] += 1
        self.stats["last_trigger"] = {
            "symbol":             sym,
            "type":               trigger.get("trigger_type"),
            "urgency":            trigger.get("urgency"),
            "size_multiplier":    trigger["size_multiplier"],
            "has_screener_context": "screener_context" in trigger,
            "catalyst_score":     trigger.get("screener_context", {}).get("catalyst_score"),
            "time":               datetime.now(_UTC).strftime("%H:%M UTC"),
        }

        log.info(
            f"⚡ CATALYST ENGINE FIRE: {sym} | "
            f"type={trigger.get('trigger_type')} | "
            f"urgency={trigger.get('urgency')} | "
            f"size_mult={trigger['size_multiplier']}x | "
            f"screener={'score=' + str(trigger['screener_context']['catalyst_score']) if 'screener_context' in trigger else 'not tracked'}"
        )

        from risk import is_trading_day
        if not is_trading_day():
            log.info(f"Catalyst trigger for {sym} — not a trading day, skipping execution")
            return

        try:
            self.on_trigger(trigger)
        except Exception as exc:
            log.error(f"CatalystEngine trigger callback error ({sym}): {exc}")

    def _fetch_news(self, symbols: list[str]) -> list[dict]:
        """
        Poll Yahoo RSS for M&A keywords across universe symbols.
        Uses instance-level headline dedup — no shared state with CatalystSentinel.
        """
        import xml.etree.ElementTree as ET
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from email.utils import parsedate_to_datetime

        import requests

        now = datetime.now(_UTC)

        def _check_symbol(sym: str) -> list[dict]:
            url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
            try:
                resp = requests.get(
                    url, timeout=2.0,
                    headers={"User-Agent": "Decifer/2.0 CatalystEngine"},
                )
                if resp.status_code != 200:
                    return []
                root = ET.fromstring(resp.content)
                hits = []
                for item in root.findall(".//item")[:6]:
                    title = item.findtext("title", "").strip()
                    if not title:
                        continue
                    if not self._headline_dedup.add_if_new(title):
                        continue
                    pub_date = item.findtext("pubDate", "")
                    age_hours = 999.0
                    if pub_date:
                        try:
                            age_hours = (now - parsedate_to_datetime(pub_date)).total_seconds() / 3600
                        except Exception:
                            pass
                    if age_hours > 4:
                        continue
                    is_match, keyword, is_definitive = _check_ma_keywords(title)
                    if not is_match:
                        continue
                    hits.append({
                        "symbol":       sym,
                        "headline":     title,
                        "keyword":      keyword,
                        "is_definitive": is_definitive,
                        "age_hours":    round(age_hours, 2),
                        "source":       "yahoo_rss",
                    })
                return hits
            except Exception as exc:
                log.debug(f"CatalystEngine news fetch ({sym}): {exc}")
                return []

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_check_symbol, sym): sym for sym in symbols}
            for future in as_completed(futures):
                try:
                    results.extend(future.result())
                except Exception:
                    pass
        return results

    def _load_edgar_events_file(self) -> list[dict]:
        """
        Read the rolling EDGAR events file written by _edgar_runner.
        Returns empty list if the file doesn't exist yet or is unreadable.
        """
        try:
            return json.loads(_EDGAR_FILE.read_text())
        except Exception:
            return []

    def _news_monitor(self, interval: int) -> None:
        """
        Polls Yahoo RSS every `interval` seconds for M&A announcement keywords.
        Fires enriched triggers immediately on match.
        Skips polling outside trading days to avoid unnecessary HTTP calls.
        """
        from risk import is_trading_day

        while self._running:
            try:
                if not is_trading_day():
                    time.sleep(3600)  # check once per hour on weekends / holidays
                    continue
                universe = self.get_universe()
                if universe:
                    hits = self._fetch_news(universe)
                    for hit in hits:
                        sym = hit["symbol"]
                        if self._cooldown.is_on_cooldown(sym):
                            continue
                        trigger = _build_news_trigger(hit)
                        trigger["_trigger_type_detail"]    = f"keyword:{hit['keyword']}"
                        trigger["_announcement_lag_hours"] = hit.get("age_hours")
                        self._fire(trigger)
                self.stats["news_polls"] += 1
                self.stats["last_news_poll"] = datetime.now(_UTC).strftime("%H:%M UTC")
            except Exception as exc:
                log.error(f"CatalystEngine [news_monitor] error: {exc}", exc_info=True)
            time.sleep(interval)

    def _edgar_monitor(self, interval: int) -> None:
        """
        Fires enriched triggers for watchlist hits and SC 13D activist filings.

        Reads from edgar_events.json written by _edgar_runner — avoids duplicate
        SEC RSS polling. Only events from the last 24 hours are processed so that
        a fresh bot restart doesn't re-fire week-old triggers. Instance-level
        _seen_edgar_events dedup prevents duplicate fires within a session.

        Staggered 45s after startup so _edgar_runner has had a chance to write
        at least one event file before we try to read it.
        Skips polling outside trading days.
        """
        from datetime import timedelta
        from risk import is_trading_day
        time.sleep(45)
        while self._running:
            try:
                if not is_trading_day():
                    time.sleep(3600)
                    continue

                watchlist = set(self.store.all_tickers())
                cutoff = (datetime.now(_UTC) - timedelta(hours=24)).isoformat()
                all_events = self._load_edgar_events_file()

                for ev in all_events:
                    # Skip events older than 24 hours — prevents stale trigger replay on restart
                    if (ev.get("updated") or "") < cutoff:
                        continue

                    form_type = ev.get("form_type", "")
                    ticker = (ev.get("ticker") or "").upper()

                    # Dedup — consistent key with _edgar_runner's own dedup
                    dedup_key = f"{form_type}|{ev.get('cik')}|{(ev.get('updated') or '')[:10]}"
                    if dedup_key in self._seen_edgar_events:
                        continue
                    self._seen_edgar_events.add(dedup_key)
                    if len(self._seen_edgar_events) > 2000:
                        stale = list(self._seen_edgar_events)[:500]
                        for k in stale:
                            self._seen_edgar_events.discard(k)

                    if not ticker:
                        continue

                    on_watchlist = ticker in watchlist
                    if form_type != "SC 13D" and not on_watchlist:
                        continue  # 13G and Form 4 only interesting for pre-identified targets

                    if self._cooldown.is_on_cooldown(ticker):
                        continue

                    ev["on_watchlist"] = on_watchlist
                    ev["ticker"] = ticker  # ensure uppercase for downstream consumers
                    trigger = _build_edgar_trigger(ev)
                    trigger["_trigger_type_detail"] = (
                        f"{ev.get('form_type', '')}:{ev.get('company_name', '')}"
                    )
                    self._fire(trigger)

                self.stats["edgar_monitor_polls"] += 1
                self.stats["last_edgar_monitor"] = datetime.now(_UTC).strftime("%H:%M UTC")
            except Exception as exc:
                log.error(f"CatalystEngine [edgar_monitor] error: {exc}", exc_info=True)
            time.sleep(interval)

    def _congressional_monitor(self, interval: int) -> None:
        """
        Fires catalyst triggers when politicians buy or sell watchlist symbols.

        Congressional trading has historically outperformed by ~12% annually.
        Polls FMP get_congressional_trades() for each watchlist symbol every 6h.
        Only fires for activity within the last 30 days (congressional disclosure
        has up to a 45-day lag — we surface it once the filing appears in FMP).

        Dedup key: congressional|{symbol}|{last_trade_date}
        Skips polling outside trading days.
        """
        from datetime import timedelta
        from risk import is_trading_day
        time.sleep(120)  # stagger 2 min after startup
        while self._running:
            try:
                if not is_trading_day():
                    time.sleep(interval)
                    continue

                from fmp_client import get_congressional_trades as _fmp_congress
                from fmp_client import is_available as _fmp_ok

                if not _fmp_ok():
                    time.sleep(interval)
                    continue

                watchlist = list(self.store.all_tickers())
                cutoff_days = 30
                for ticker in watchlist:
                    if not self._running:
                        break
                    try:
                        data = _fmp_congress(ticker, days=cutoff_days)
                        if not data or data.get("net_sentiment") == "NONE":
                            continue

                        last_date = data.get("last_trade_date", "")
                        dedup_key = f"congressional|{ticker}|{last_date}"
                        if dedup_key in self._seen_edgar_events:
                            continue

                        if self._cooldown.is_on_cooldown(ticker):
                            continue

                        self._seen_edgar_events.add(dedup_key)
                        trigger = _build_congressional_trigger(ticker, data)
                        trigger["_trigger_type_detail"] = (
                            f"congressional_{data.get('net_sentiment', '').lower()}"
                            f"|buy={data.get('buy_count', 0)}"
                            f"|sell={data.get('sell_count', 0)}"
                        )
                        self._fire(trigger)
                        log.info(
                            "CatalystEngine [congressional] %s %s buy=%d sell=%d politicians=%s",
                            ticker, data.get("net_sentiment"),
                            data.get("buy_count", 0), data.get("sell_count", 0),
                            data.get("politicians", []),
                        )
                    except Exception as _sym_exc:
                        log.debug("CatalystEngine [congressional] %s error: %s", ticker, _sym_exc)

                self.stats["last_edgar_monitor"] = datetime.now(_UTC).strftime("%H:%M UTC")
            except Exception as exc:
                log.error(f"CatalystEngine [congressional_monitor] error: {exc}", exc_info=True)
            time.sleep(interval)

    def _score_threshold_checker(self, interval: int) -> None:
        """
        Fires a score-threshold trigger when a watchlist candidate first crosses
        catalyst_score ≥ 5.0, without requiring a news headline or EDGAR filing.

        This gives agents early visibility on high-conviction screener candidates
        before any news breaks, generating IC training data for pure-screener setups.

        Dedup: once a ticker fires, it's added to _threshold_fired and won't fire
        again this session, even if the score keeps rising. Bot restart resets the
        session state. The cooldown gate still applies — a ticker on cooldown from a
        news trigger won't get a second threshold trigger within the cooldown window.

        Staggered 300s after startup so the fundamental screen has had time to run.
        """
        from risk import is_trading_day

        time.sleep(300)  # wait for first fundamental screen to populate the store
        while self._running:
            try:
                if not is_trading_day():
                    time.sleep(3600)
                    continue

                threshold = 5.0
                fired_count = 0
                for candidate in self.store.snapshot():
                    score = candidate.get("catalyst_score", 0)
                    ticker = (candidate.get("ticker") or "").upper()
                    if not ticker or score < threshold:
                        continue
                    if ticker in self._threshold_fired:
                        continue
                    if self._cooldown.is_on_cooldown(ticker):
                        continue

                    self._threshold_fired.add(ticker)
                    fired_count += 1
                    trigger = {
                        "symbol":            ticker,
                        "trigger_type":      "score_threshold",
                        "headlines":         [],
                        "direction":         "BULLISH",
                        "urgency":           "HIGH" if score >= 7.0 else "MODERATE",
                        "source":            "catalyst_engine",
                        "claude_sentiment":  "BULLISH",
                        "claude_confidence": min(10, int(score)),
                        "claude_catalyst":   (
                            f"Score-threshold trigger: catalyst_score={score:.1f} ≥ {threshold}"
                        ),
                        "triggered_at":      datetime.now(_UTC).isoformat(),
                        # Consumed by _fire() to build ic_context
                        "_trigger_type_detail":    f"score_threshold:{score:.1f}",
                        "_announcement_lag_hours": None,
                    }
                    log.info(
                        f"⚡ CATALYST score-threshold: {ticker} | "
                        f"score={score:.1f} ≥ {threshold} | "
                        f"urgency={trigger['urgency']}"
                    )
                    self._fire(trigger)

                if fired_count:
                    self.stats["score_threshold_runs"] += fired_count
                    self.stats["last_score_threshold"] = datetime.now(_UTC).strftime("%H:%M UTC")
            except Exception as exc:
                log.error(f"CatalystEngine [score_threshold] error: {exc}", exc_info=True)
            time.sleep(interval)
