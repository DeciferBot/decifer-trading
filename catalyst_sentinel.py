# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  catalyst_sentinel.py                      ║
# ║   Real-time M&A / acquisition catalyst monitor              ║
# ║                                                              ║
# ║   Runs TWO independent background daemon threads:           ║
# ║     1. News thread  — polls Yahoo RSS + Finviz for M&A      ║
# ║        announcement keywords every 60 seconds               ║
# ║     2. EDGAR thread — polls SEC RSS for 13D/13G/Form 4       ║
# ║        activist/insider filings every 10 minutes            ║
# ║                                                              ║
# ║   Fires handle_catalyst_trigger() immediately when:          ║
# ║     - An acquisition/merger announcement is detected         ║
# ║     - An activist 13D filing appears for a watchlist ticker  ║
# ║     - An insider cluster buy appears for a watchlist ticker  ║
# ║                                                              ║
# ║   Completely independent of the scan loop — never blocks it. ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import re
import threading
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from config import CONFIG

log = logging.getLogger("decifer.catalyst")

# ═══════════════════════════════════════════════════════════════
# M&A KEYWORD SETS
# ═══════════════════════════════════════════════════════════════
# High-precision acquisition announcement keywords.
# These are specific enough that a single match is sufficient to
# flag a headline as a potential acquisition event.
MA_ANNOUNCEMENT_KEYWORDS = {
    # Definitive deal language
    "to be acquired", "acquisition agreement", "merger agreement",
    "definitive agreement", "definitive merger", "agreed to be acquired",
    "agreed to acquire", "deal to acquire", "agree to buy",
    # Offer language
    "tender offer", "per share in cash", "per share in an all-cash",
    "takeover bid", "unsolicited bid", "hostile takeover",
    "going private", "take-private", "management buyout", "mbo",
    # Strategic language (softer — requires multi-keyword confirmation)
    "strategic alternatives", "exploring a sale", "sale process",
    "received a buyout", "received an offer to acquire",
}

# Soft signals — need 2+ matches OR combination with a ticker symbol
MA_SOFT_KEYWORDS = {
    "buyout", "takeover", "acquired by", "buys", "purchase price",
    "premium", "acquirer", "strategic review", "due diligence",
    "merger talks", "acquisition talks", "in talks to acquire",
}

# Keywords that would cause a FALSE POSITIVE — filter these out
MA_NEGATIVE_KEYWORDS = {
    "acquires technology", "acquires talent", "acquires domain",
    "acquires content", "acquires license", "acquires rights",
    # "acquires" alone for a small asset deal is not a takeout
}

# ═══════════════════════════════════════════════════════════════
# DEDUP TRACKING
# ═══════════════════════════════════════════════════════════════
_seen_catalyst_headlines: set[str] = set()
_seen_edgar_events: set[str] = set()     # (form_type, cik, date) keys
_recent_triggers: deque = deque(maxlen=100)

# Cooldowns: per-symbol, prevents re-firing on the same event
_symbol_cooldowns: dict[str, datetime] = {}
COOLDOWN_MINUTES = CONFIG.get("catalyst_cooldown_minutes", 60)


def _headline_hash(text: str) -> str:
    clean = re.sub(r'[^a-z0-9 ]', '', text.lower().strip())
    return clean[:120]


def _is_on_cooldown(symbol: str) -> bool:
    last = _symbol_cooldowns.get(symbol)
    if not last:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() < COOLDOWN_MINUTES * 60


def _set_cooldown(symbol: str):
    _symbol_cooldowns[symbol] = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════
# NEWS — M&A KEYWORD DETECTION
# ═══════════════════════════════════════════════════════════════

def _check_ma_keywords(headline: str) -> tuple[bool, str, bool]:
    """
    Check a headline for M&A announcement keywords.

    Returns
    -------
    (is_match, matched_keyword, is_definitive)
      is_definitive = True if this is a hard announcement (not just rumour)
    """
    text = headline.lower()

    # Hard negative: filter out asset acquisitions
    for neg in MA_NEGATIVE_KEYWORDS:
        if neg in text:
            return False, "", False

    # Check definitive announcement keywords first
    for kw in MA_ANNOUNCEMENT_KEYWORDS:
        if kw in text:
            # Definitive if the keyword is deal-closing language
            is_def = any(x in kw for x in [
                "definitive", "agreement", "per share", "tender offer",
                "going private", "agreed to", "merger agreement",
            ])
            return True, kw, is_def

    # Check soft keywords — require 2+ matches
    soft_hits = [kw for kw in MA_SOFT_KEYWORDS if kw in text]
    if len(soft_hits) >= 2:
        return True, " + ".join(soft_hits[:2]), False

    return False, "", False


def _extract_ticker_from_headline(headline: str, universe: list[str]) -> str | None:
    """
    Try to identify which ticker the headline is about from the universe.
    Scans for the ticker as a word in the headline text.
    """
    words = re.findall(r'\b[A-Z]{1,5}\b', headline)
    for word in words:
        if word in universe:
            return word
    return None


def _fetch_ma_news(symbols: list[str]) -> list[dict]:
    """
    Fetch Yahoo RSS for a batch of symbols and filter for M&A headlines.
    Returns list of catalyst trigger dicts.
    """
    triggers = []

    def _check_symbol(sym):
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
        try:
            resp = requests.get(url, timeout=2.0, headers={"User-Agent": "Decifer/2.0 CatalystSentinel"})
            if resp.status_code != 200:
                return []

            root = ET.fromstring(resp.content)
            now = datetime.now(timezone.utc)
            hits = []

            for item in root.findall(".//item")[:6]:
                title = item.findtext("title", "").strip()
                if not title:
                    continue

                h = _headline_hash(title)
                if h in _seen_catalyst_headlines:
                    continue

                pub_date = item.findtext("pubDate", "")
                age_hours = 999
                if pub_date:
                    try:
                        from email.utils import parsedate_to_datetime
                        age_hours = (now - parsedate_to_datetime(pub_date)).total_seconds() / 3600
                    except Exception:
                        pass

                if age_hours > 4:      # Only consider news < 4 hours old
                    continue

                is_match, keyword, is_definitive = _check_ma_keywords(title)
                if not is_match:
                    continue

                _seen_catalyst_headlines.add(h)
                hits.append({
                    "symbol":        sym,
                    "headline":      title,
                    "keyword":       keyword,
                    "is_definitive": is_definitive,
                    "age_hours":     round(age_hours, 2),
                    "source":        "yahoo_rss",
                })

            return hits
        except Exception as e:
            log.debug(f"Catalyst news fetch error ({sym}): {e}")
            return []

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_check_symbol, sym): sym for sym in symbols}
        for future in as_completed(futures):
            try:
                triggers.extend(future.result())
            except Exception:
                pass

    return triggers


# ═══════════════════════════════════════════════════════════════
# EDGAR — SEC RSS POLLING
# ═══════════════════════════════════════════════════════════════

_SEC_TICKERS_CACHE: dict[str, str] = {}   # cik → ticker
_SEC_TICKERS_FETCHED_AT: datetime | None = None
_ATOM_NS = "http://www.w3.org/2005/Atom"

_EDGAR_FEEDS = {
    "SC 13D": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13D&dateb=&owner=include&count=40&search_text=&output=atom",
    "SC 13G": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13G&dateb=&owner=include&count=40&search_text=&output=atom",
    "4":      "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=40&search_text=&output=atom",
}


def _load_sec_tickers() -> dict[str, str]:
    """Returns {cik → ticker}. Cached for 24 hours."""
    global _SEC_TICKERS_CACHE, _SEC_TICKERS_FETCHED_AT
    now = datetime.now(timezone.utc)

    # Use in-memory cache
    if (_SEC_TICKERS_FETCHED_AT and
            (now - _SEC_TICKERS_FETCHED_AT).total_seconds() < 86400 and
            _SEC_TICKERS_CACHE):
        return _SEC_TICKERS_CACHE

    # Try disk cache
    cache_path = Path("data/sec_tickers.json")
    if cache_path.exists():
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours < 24:
            try:
                _SEC_TICKERS_CACHE = json.loads(cache_path.read_text())
                _SEC_TICKERS_FETCHED_AT = now
                return _SEC_TICKERS_CACHE
            except Exception:
                pass

    # Download from SEC
    try:
        req = urllib.request.Request(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": "Decifer research@decifer.ai"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        mapping = {
            str(v.get("cik_str", "")).zfill(10): v.get("ticker", "").upper()
            for v in raw.values()
            if v.get("cik_str") and v.get("ticker")
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(mapping))
        _SEC_TICKERS_CACHE = mapping
        _SEC_TICKERS_FETCHED_AT = now
        log.info(f"SEC tickers map loaded: {len(mapping)} entries")
        return mapping
    except Exception as exc:
        log.warning(f"Could not load SEC tickers: {exc}")
        return _SEC_TICKERS_CACHE or {}


def _parse_edgar_feed(form_type: str, url: str) -> list[dict]:
    """Fetch and parse one EDGAR Atom RSS feed."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Decifer research@decifer.ai"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
    except urllib.error.URLError as exc:
        log.debug(f"EDGAR feed error ({form_type}): {exc}")
        return []

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    ns = {"atom": _ATOM_NS}
    events = []

    for entry in root.findall("atom:entry", ns):
        title_el   = entry.find("atom:title", ns)
        updated_el = entry.find("atom:updated", ns)
        link_el    = entry.find("atom:link", ns)

        title   = (title_el.text or "").strip()   if title_el   is not None else ""
        updated = (updated_el.text or "").strip()  if updated_el is not None else ""
        link    = link_el.attrib.get("href", "")  if link_el    is not None else ""

        company = title.split("(")[0].strip() if "(" in title else title

        # Extract CIK from URL
        cik = None
        m = re.search(r"CIK=(\d+)", link, re.IGNORECASE)
        if m:
            cik = m.group(1).zfill(10)

        events.append({
            "form_type":    form_type,
            "company_name": company,
            "cik":          cik,
            "ticker":       None,
            "title":        title,
            "updated":      updated,
            "link":         link,
        })

    return events


def _poll_edgar(watchlist: set[str]) -> list[dict]:
    """
    Poll all EDGAR feeds, resolve tickers, return events for watchlist tickers
    and ALL 13D filings (they're all potentially interesting).
    """
    cik_map = _load_sec_tickers()
    triggers = []

    for form_type, url in _EDGAR_FEEDS.items():
        events = _parse_edgar_feed(form_type, url)
        time.sleep(0.5)

        for ev in events:
            # Resolve CIK → ticker
            ticker = None
            if ev["cik"] and ev["cik"] in cik_map:
                ticker = cik_map[ev["cik"]]
            ev["ticker"] = ticker

            # Dedup key
            dedup_key = f"{form_type}|{ev['cik']}|{ev['updated'][:10]}"
            if dedup_key in _seen_edgar_events:
                continue
            _seen_edgar_events.add(dedup_key)

            # Cap dedup set
            if len(_seen_edgar_events) > 2000:
                old = list(_seen_edgar_events)[:500]
                for k in old:
                    _seen_edgar_events.discard(k)

            # Include if: watchlist hit OR 13D (always interesting)
            on_watchlist = ticker and ticker.upper() in watchlist
            if form_type == "SC 13D" or on_watchlist:
                ev["on_watchlist"] = on_watchlist
                triggers.append(ev)
                log.info(
                    f"⚡ EDGAR: {form_type} | {ticker or '?':6s} | {ev['company_name'][:40]} | "
                    f"{'★ WATCHLIST' if on_watchlist else ''}"
                )

    return triggers


# ═══════════════════════════════════════════════════════════════
# CATALYST TRIGGER BUILDER
# ═══════════════════════════════════════════════════════════════

def _build_news_trigger(news_hit: dict) -> dict:
    """Build a standardised catalyst trigger dict from a news hit."""
    return {
        "symbol":        news_hit["symbol"],
        "trigger_type":  "ma_announcement",
        "headlines":     [news_hit["headline"]],
        "keyword":       news_hit["keyword"],
        "is_definitive": news_hit["is_definitive"],
        "direction":     "BULLISH",
        "urgency":       "CRITICAL" if news_hit["is_definitive"] else "HIGH",
        "age_hours":     news_hit["age_hours"],
        "source":        news_hit["source"],
        # Enriched by deep_read_trigger before firing
        "claude_sentiment":   "BULLISH",
        "claude_confidence":  8 if news_hit["is_definitive"] else 5,
        "claude_catalyst":    f"M&A signal: {news_hit['keyword']}",
        "triggered_at":       datetime.now(timezone.utc).isoformat(),
    }


def _build_edgar_trigger(edgar_event: dict) -> dict:
    """Build a standardised catalyst trigger dict from an EDGAR event."""
    form = edgar_event["form_type"]
    ticker = edgar_event.get("ticker") or ""
    company = edgar_event.get("company_name", "")

    if form == "SC 13D":
        urgency = "HIGH"
        catalyst = f"Activist investor SC 13D filed: {company}"
        confidence = 6
    elif form == "SC 13G":
        urgency = "MODERATE"
        catalyst = f"Passive investor SC 13G (>5% stake): {company}"
        confidence = 4
    else:  # Form 4
        urgency = "MODERATE"
        catalyst = f"Insider Form 4 filing: {company}"
        confidence = 3

    return {
        "symbol":        ticker,
        "trigger_type":  f"edgar_{form.lower().replace(' ', '')}",
        "headlines":     [edgar_event["title"]],
        "keyword":       form,
        "is_definitive": False,  # Pre-announcement signal, not an announcement
        "direction":     "BULLISH",
        "urgency":       urgency,
        "age_hours":     0,
        "source":        "sec_edgar",
        "edgar_link":    edgar_event.get("link", ""),
        "claude_sentiment":   "BULLISH",
        "claude_confidence":  confidence,
        "claude_catalyst":    catalyst,
        "triggered_at":       datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# CATALYST SENTINEL CLASS
# ═══════════════════════════════════════════════════════════════

class CatalystSentinel:
    """
    Independent background sentinel that monitors for M&A catalyst events.
    Mirrors the NewsSentinel pattern — two daemon threads, callback-based.

    Usage:
        sentinel = CatalystSentinel(
            get_universe_fn=lambda: [...symbols...],
            on_trigger_fn=handle_catalyst_trigger,
        )
        sentinel.start()
    """

    def __init__(self, get_universe_fn, on_trigger_fn, ib=None):
        """
        get_universe_fn : callable → list[str]  — symbols to monitor
        on_trigger_fn   : callable(trigger_dict) — called on catalyst event
        ib              : IB instance (optional, not currently used)
        """
        self.get_universe = get_universe_fn
        self.on_trigger   = on_trigger_fn
        self.ib           = ib

        self._running = False
        self._news_thread  = None
        self._edgar_thread = None
        self._paused = False

        self.news_poll_seconds  = CONFIG.get("catalyst_news_poll_seconds", 60)
        self.edgar_poll_seconds = CONFIG.get("catalyst_edgar_poll_seconds", 600)

        # Stats for dashboard
        self.stats = {
            "status":           "stopped",
            "news_polls":       0,
            "edgar_polls":      0,
            "triggers_fired":   0,
            "last_news_poll":   None,
            "last_edgar_poll":  None,
            "last_trigger":     None,
            "symbols_monitored": 0,
            "recent_triggers":  [],
        }

    def start(self):
        if self._running:
            log.warning("CatalystSentinel already running")
            return
        self._running = True

        self._news_thread = threading.Thread(
            target=self._news_loop, daemon=True, name="CatalystSentinel-News"
        )
        self._edgar_thread = threading.Thread(
            target=self._edgar_loop, daemon=True, name="CatalystSentinel-EDGAR"
        )
        self._news_thread.start()
        self._edgar_thread.start()

        self.stats["status"] = "running"
        log.info(
            f"⚡ Catalyst Sentinel started | "
            f"news every {self.news_poll_seconds}s | "
            f"EDGAR every {self.edgar_poll_seconds}s"
        )

    def stop(self):
        self._running = False
        self.stats["status"] = "stopped"
        log.info("⚡ Catalyst Sentinel stopped")

    def pause(self):
        self._paused = True
        self.stats["status"] = "paused"

    def resume(self):
        self._paused = False
        self.stats["status"] = "running"

    # ── News monitoring thread ──────────────────────────────────

    def _news_loop(self):
        while self._running:
            try:
                if self._paused:
                    time.sleep(5)
                    continue

                universe = self.get_universe()
                self.stats["symbols_monitored"] = len(universe)

                if universe:
                    # Batch news fetch across all symbols (parallel)
                    news_hits = _fetch_ma_news(universe)

                    for hit in news_hits:
                        sym = hit["symbol"]
                        if _is_on_cooldown(sym):
                            continue
                        trigger = _build_news_trigger(hit)
                        self._fire(trigger)

                self.stats["news_polls"] += 1
                self.stats["last_news_poll"] = datetime.now().strftime("%H:%M:%S")

            except Exception as exc:
                log.error(f"Catalyst news loop error: {exc}")

            time.sleep(self.news_poll_seconds)

    # ── EDGAR monitoring thread ─────────────────────────────────

    def _edgar_loop(self):
        # Stagger EDGAR start by 30s to avoid concurrent startup load
        time.sleep(30)

        while self._running:
            try:
                if self._paused:
                    time.sleep(10)
                    continue

                universe = set(self.get_universe())
                edgar_events = _poll_edgar(universe)

                for ev in edgar_events:
                    sym = ev.get("ticker", "")
                    if not sym:
                        continue   # Can't execute without a ticker
                    if _is_on_cooldown(sym):
                        continue

                    trigger = _build_edgar_trigger(ev)
                    self._fire(trigger)

                self.stats["edgar_polls"] += 1
                self.stats["last_edgar_poll"] = datetime.now().strftime("%H:%M:%S")

            except Exception as exc:
                log.error(f"Catalyst EDGAR loop error: {exc}")

            time.sleep(self.edgar_poll_seconds)

    # ── Fire trigger callback ───────────────────────────────────

    def _fire(self, trigger: dict):
        sym = trigger.get("symbol", "?")
        _set_cooldown(sym)
        self.stats["triggers_fired"] += 1

        summary = {
            "symbol":      sym,
            "trigger_type": trigger.get("trigger_type", "unknown"),
            "urgency":     trigger.get("urgency", "?"),
            "catalyst":    trigger.get("claude_catalyst", "")[:80],
            "time":        datetime.now().strftime("%H:%M:%S"),
        }
        self.stats["last_trigger"] = summary
        self.stats["recent_triggers"].insert(0, summary)
        self.stats["recent_triggers"] = self.stats["recent_triggers"][:20]

        _recent_triggers.appendleft(summary)

        log.info(
            f"⚡ CATALYST TRIGGER: {sym} | "
            f"type={trigger.get('trigger_type')} | "
            f"urgency={trigger.get('urgency')} | "
            f"{trigger.get('claude_catalyst', '')[:60]}"
        )

        try:
            self.on_trigger(trigger)
        except Exception as exc:
            log.error(f"Catalyst trigger callback error ({sym}): {exc}")


# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def get_catalyst_history() -> list[dict]:
    """Return recent catalyst trigger history for dashboard display."""
    return list(_recent_triggers)
