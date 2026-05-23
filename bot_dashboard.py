#!/usr/bin/env python3
"""
bot_dashboard.py — HTTP dashboard handler for the Decifer trading bot.

Covers: DashHandler(BaseHTTPRequestHandler) and start_dashboard().
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import bot_state
import ibkr_reconciler
import schemas
from bot_account import get_account_details
from bot_ibkr import sync_orders_from_ibkr
from bot_state import clog, dash
from bot_trading import run_scan
from config import CONFIG
from orders import cancel_order_by_id
from orders_portfolio import flatten_all, get_open_positions

log = logging.getLogger("decifer.bot")

# ── News API cache ────────────────────────────────────────────────────────────
import time as _time
from datetime import UTC

_news_payload_cache: dict = {"data": None, "fetched_at": 0.0}
_NEWS_CACHE_TTL = 60  # seconds

_catalyst_payload_cache: dict = {"data": None, "fetched_at": 0.0}
_CATALYST_CACHE_TTL = 30  # seconds

# ── Intelligence pipeline trigger state ──────────────────────────────────────
_intel_pipeline_lock = threading.Lock()
_intel_pipeline_state: dict = {"running": False, "triggered_at": None, "error": None}

# ── Macro event classifier cache ──────────────────────────────────────────────
_macro_cache: dict = {}  # headline_hash → list of macro classifications

# ── Sector cache ──────────────────────────────────────────────────────────────
_sector_cache: dict[str, str] = {}  # symbol → raw FMP sector name, permanent in-process


def _fetch_alpaca_news() -> list[dict]:
    """
    Fetch recent news from Alpaca/Benzinga via direct REST API.
    Uses requests instead of the SDK (avoids pydantic version conflicts).
    Benzinga articles consistently include large CDN images.
    """
    import requests as _req

    api_key = CONFIG.get("alpaca_api_key", "") or os.environ.get("ALPACA_API_KEY", "")
    secret_key = CONFIG.get("alpaca_secret_key", "") or os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        return []
    try:
        from datetime import datetime, timedelta

        start = (datetime.now(UTC) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = _req.get(
            "https://data.alpaca.markets/v1beta1/news",
            params={"limit": 50, "sort": "desc", "start": start, "include_content": "false"},
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key},
            timeout=10,
        )
        if resp.status_code != 200:
            log.debug("Alpaca news HTTP %d", resp.status_code)
            return []

        now_utc = datetime.now(UTC)
        result = []
        for art in resp.json().get("news", []):
            headline = (art.get("headline") or "").strip()
            if not headline:
                continue

            # Pick best image: large > small > thumb
            # Skip Benzinga branding/logo images — they are not article-specific content.
            # BZ serves generic images via /sites/all/ or /sites/default/ paths (imagecache CDN).
            # Article-specific photos live under /files/images/story/ — those are kept.
            _BZ_SKIP = (
                "/sites/",           # catches /sites/all/ AND /sites/default/ BZ CDN paths
                "/imagecache/",      # Benzinga's resized-image CDN; never an original article photo
                "benzinga-logo",
                "benzinga_logo",
                "bz-logo",
                "bz_logo",
                "bz-icon",
                "bz_icon",
                "/logo.",
                "/logos/",
                "default_image",
                "placeholder",
            )
            image_url = None
            for size_pref in ("large", "small", "thumb"):
                for img in art.get("images") or []:
                    u = img.get("url") or ""
                    if img.get("size") == size_pref and u and not any(p in u.lower() for p in _BZ_SKIP):
                        image_url = u
                        break
                if image_url:
                    break

            # Parse created_at
            age_hours, created_ts = 0.0, 0
            ts_str = art.get("created_at", "")
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age_hours = (now_utc - dt).total_seconds() / 3600
                created_ts = int(dt.timestamp() * 1000)
            except Exception:
                pass

            result.append(
                {
                    "headline": headline,
                    "summary": (art.get("summary") or "").strip(),
                    "url": (art.get("url") or "").strip(),
                    "source": (art.get("source") or "").strip(),
                    "author": (art.get("author") or "").strip(),
                    "symbols": [s.upper() for s in (art.get("symbols") or [])],
                    "image_url": image_url,
                    "age_hours": round(age_hours, 2),
                    "created_ts": created_ts,
                    "sentiment": "NEUTRAL",
                    "news_score": 0,
                    "catalyst": "",
                }
            )

        log.info("Alpaca news: %d articles (%d with images)", len(result), sum(1 for a in result if a["image_url"]))
        return result
    except Exception as exc:
        log.debug("Alpaca news fetch failed: %s", exc)
        return []


_calendar_enrichment_cache: dict = {}  # keyed by hash of event names


def _get_enriched_calendar(cal_events: list) -> list:
    """Return cal_events annotated with signal/why/affects, cached in memory."""
    if not cal_events:
        return cal_events
    cache_key = hash(tuple(ev.get("event", "") for ev in cal_events))
    if cache_key in _calendar_enrichment_cache:
        return _calendar_enrichment_cache[cache_key]
    # Only enrich if any event is missing annotations
    if all(ev.get("signal") for ev in cal_events):
        return cal_events
    try:
        from overnight_research import enrich_calendar_events
        enriched = enrich_calendar_events(cal_events)
        _calendar_enrichment_cache[cache_key] = enriched
        return enriched
    except Exception as exc:
        log.debug("Live calendar enrichment failed: %s", exc)
        return cal_events


_MACRO_TYPES = {
    "FOMC": ("FED", "#ff6b35"),
    "CPI": ("CPI", "#ff4444"),
    "GDP": ("GDP", "#ff8c00"),
    "JOBS": ("JOBS", "#ffd700"),
    "WAR": ("WAR", "#cc0000"),
    "GEOPOLITICAL": ("GEO", "#e05c00"),
    "LEGISLATION": ("LAW", "#7b2fff"),
    "TARIFF": ("TARIFF", "#ff6b35"),
    "CREDIT": ("CREDIT", "#cc0000"),
    "OTHER_MACRO": ("MACRO", "#888"),
}


def _enrich_sectors(articles: list) -> None:
    """
    Add sector field to articles using FMP company profile (cached per symbol).
    Only fetches symbols not already in _sector_cache; FMP has its own 4h cache
    so repeated in-process lookups are free.
    """
    from fmp_client import get_company_profile as get_company_metadata

    new_syms = {
        sym
        for a in articles
        for sym in (a.get("symbols") or [])
        if sym and sym not in _sector_cache
    }

    if new_syms:
        def _fetch(sym: str) -> tuple[str, str]:
            try:
                meta = get_company_metadata(sym)
                return sym, (meta or {}).get("sector") or ""
            except Exception:
                return sym, ""

        with ThreadPoolExecutor(max_workers=8) as ex:
            for sym, sector in ex.map(_fetch, list(new_syms)[:20]):
                _sector_cache[sym] = sector

    for a in articles:
        for sym in (a.get("symbols") or []):
            s = _sector_cache.get(sym, "")
            if s:
                a["sector"] = s
                break


def _enrich_macro_events(articles: list) -> None:
    """
    Use Sonnet to identify macro market-moving events in the article feed.
    Adds macro_event, macro_type, macro_label, macro_color, macro_impact,
    macro_direction, macro_implication fields to qualifying articles.
    Results cached by article fingerprint — won't call API if articles unchanged.
    """
    if not articles:
        return

    headlines = [a.get("headline", "") for a in articles[:30]]
    cache_key = hash(tuple(headlines))
    if cache_key in _macro_cache:
        classifications = _macro_cache[cache_key]
    else:
        try:
            import anthropic as _ant

            items = [
                {"i": i, "h": h, "s": (articles[i].get("summary") or "")[:120]} for i, h in enumerate(headlines) if h
            ]
            prompt = (
                "You are a macro market analyst. Identify which of these news headlines represent "
                "MACRO MARKET-MOVING events — events likely to move S&P 500 or Nasdaq by ≥0.3%.\n\n"
                "Macro events include: Fed/FOMC decisions, CPI/PPI/PCE prints, GDP data, non-farm "
                "payrolls, major wars or military escalations, new trade tariffs/sanctions, landmark "
                "legislation (tax, regulation), credit crises, central bank policy changes.\n\n"
                "NOT macro: routine earnings of individual companies, typical M&A, product launches, "
                "analyst upgrades/downgrades.\n\n"
                "Headlines:\n"
                + json.dumps(items, indent=2)
                + "\n\nReturn a JSON array for qualifying macro events only:\n"
                '[{"i":<index>,"type":"FOMC|CPI|GDP|JOBS|WAR|GEOPOLITICAL|LEGISLATION|TARIFF|CREDIT|OTHER_MACRO",'
                '"impact":<1-10>,"direction":"BULLISH|BEARISH|NEUTRAL|MIXED",'
                '"implication":"<one sentence: what this means for markets>"}]\n'
                "Return [] if none qualify. JSON only, no other text."
            )
            api_key = CONFIG.get("anthropic_api_key") or ""
            if not api_key or api_key == "YOUR_API_KEY_HERE":
                # Fallback: read directly from .env (bypasses empty-string env var override)
                try:
                    from dotenv import dotenv_values as _dv

                    api_key = _dv(os.path.join(os.path.dirname(__file__), ".env")).get("ANTHROPIC_API_KEY", "")
                except Exception:
                    pass
            if not api_key:
                log.warning("Macro classifier: no Anthropic API key available — check ANTHROPIC_API_KEY in .env")
                return
            client = _ant.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            classifications = json.loads(raw)
            if classifications:  # only cache non-empty — empty result allows retry on next cycle
                _macro_cache[cache_key] = classifications
            log.info("Macro classifier: %d macro events found in %d articles", len(classifications), len(articles))
        except Exception as exc:
            log.warning("Macro event classification error: %s", exc)
            return

    for item in classifications or []:
        idx = item.get("i")
        if not isinstance(idx, int) or not (0 <= idx < len(articles)):
            continue
        mtype = item.get("type", "OTHER_MACRO")
        label, color = _MACRO_TYPES.get(mtype, ("MACRO", "#888"))
        articles[idx].update(
            {
                "macro_event": True,
                "macro_type": mtype,
                "macro_label": label,
                "macro_color": color,
                "macro_impact": int(item.get("impact", 5)),
                "macro_direction": item.get("direction", "NEUTRAL"),
                "macro_implication": item.get("implication", ""),
            }
        )


def _get_catalyst_payload() -> dict:
    """
    Build the /api/catalyst payload from chief-decifer/state/internal/catalyst/.
    Reads the most recent candidates file + edgar_events.json.
    Cached for 30 seconds.

    Always returns a valid dict with keys: candidates, edgar_events, date_str,
    total_candidates. File read failures are logged at WARNING; bad individual
    records are skipped. Never raises.
    """
    import time as _time_mod

    now = _time_mod.time()
    if _catalyst_payload_cache["data"] and now - _catalyst_payload_cache["fetched_at"] < _CATALYST_CACHE_TTL:
        return _catalyst_payload_cache["data"]

    from config import CATALYST_DIR

    candidates: list = []
    date_str: str = ""
    edgar_events: list = []

    if CATALYST_DIR.exists():
        files = sorted(CATALYST_DIR.glob("candidates_*.json"), reverse=True)
        if files:
            try:
                raw = json.loads(files[0].read_text())
                _ver = raw.get("_schema_version")
                if _ver is not None and _ver != 1:
                    log.warning("[dashboard][_get_catalyst_payload] unrecognised _schema_version=%s in %s — processing anyway", _ver, files[0].name)
                _raw_candidates = raw.get("candidates", [])
                candidates = []
                for _c in _raw_candidates:
                    try:
                        schemas.validate_catalyst_record(_c)
                        candidates.append(_c)
                    except ValueError as _ve:
                        log.warning("[dashboard][_get_catalyst_payload] skipping bad candidate record: %s", _ve)
                date_str = raw.get("date", files[0].stem.replace("candidates_", ""))
            except Exception as e:
                log.warning("[dashboard][_get_catalyst_payload] failed to read %s: %s", files[0].name, e)

        edgar_file = CATALYST_DIR / "edgar_events.json"
        if edgar_file.exists():
            try:
                edgar_events = json.loads(edgar_file.read_text())
            except Exception as e:
                log.warning("[dashboard][_get_catalyst_payload] failed to read edgar_events.json: %s", e)

    payload = {
        "candidates": sorted(candidates, key=lambda c: c.get("catalyst_score", 0), reverse=True)[:15],
        "edgar_events": edgar_events[:40],
        "date_str": date_str,
        "total_candidates": len(candidates),
    }
    _catalyst_payload_cache["data"] = payload
    _catalyst_payload_cache["fetched_at"] = now
    return payload


_CALENDAR_META = {
    "FOMC": {
        "impact": 9,
        "direction": "NEUTRAL",
        "implication": "Federal Reserve rate decision — expect elevated volatility around 14:00 ET. Risk-off positioning likely pre-announcement.",
        "headline": "FOMC Rate Decision Today — Federal Reserve Policy Announcement",
    },
    "CPI": {
        "impact": 8,
        "direction": "NEUTRAL",
        "implication": "Consumer Price Index release — inflation data directly drives rate expectations and bond/equity repricing.",
        "headline": "CPI Inflation Data Release Today — Markets Pricing Rate Path Impact",
    },
    "NFP": {
        "impact": 8,
        "direction": "NEUTRAL",
        "implication": "Non-Farm Payrolls release — labor market strength shapes Fed policy outlook for the next meeting.",
        "headline": "Non-Farm Payrolls Report Today — Labor Market Data Drives Fed Expectations",
    },
}


def _calendar_macro_fallback(articles: list) -> None:
    """
    Fallback: if Sonnet tagged zero macro events, check macro_calendar for a
    scheduled FOMC/CPI/NFP event within 24h and inject a synthetic article so
    the macro strip is never blank on high-impact days.
    """
    if any(a.get("macro_event") for a in articles):
        return  # LLM already found something — no fallback needed

    try:
        from macro_calendar import get_next_event, hours_to_next_event

        hours = hours_to_next_event()
        if hours is None or not (0 <= hours <= 24):
            return

        event = get_next_event()
        if not event:
            return

        etype = event["type"]
        meta = _CALENDAR_META.get(etype, _CALENDAR_META["FOMC"])
        label, color = _MACRO_TYPES.get(etype, ("MACRO", "#888"))
        h_str = f"{hours:.1f}h" if hours >= 1 else f"{int(hours * 60)}min"

        synthetic = {
            "headline": meta["headline"],
            "summary": f"{meta['implication']} — {h_str} away.",
            "url": "",
            "source": "macro_calendar",
            "author": "",
            "symbols": [],
            "image_url": None,
            "age_hours": 0.0,
            "created_ts": 0,
            "sentiment": "NEUTRAL",
            "news_score": 0,
            "catalyst": "",
            "macro_event": True,
            "macro_type": etype,
            "macro_label": label,
            "macro_color": color,
            "macro_impact": meta["impact"],
            "macro_direction": meta["direction"],
            "macro_implication": meta["implication"],
        }
        articles.insert(0, synthetic)
        log.info("Macro fallback: injected %s calendar event (%.1fh away)", etype, hours)
    except Exception as exc:
        log.debug("Macro calendar fallback error: %s", exc)


def _get_held_symbols() -> set[str]:
    """Return symbols with currently open positions from the live position state.

    Uses get_open_positions() — the in-memory active_trades dict reconciled
    against IBKR at startup — which is the single authoritative source for live
    position state.  data/positions.json is a crash-fallback that may be stale
    between restarts and is NOT a reliable source for this function.

    Returns an empty set on any error so the caller (news card tagging) degrades
    gracefully rather than crashing the dashboard.
    """
    try:
        positions = get_open_positions()
        return {
            v["symbol"]
            for v in positions.values()
            if v.get("symbol") and int(v.get("qty", 0)) != 0
        }
    except Exception:
        return set()


def _tag_top_news_cards(articles: list, held_symbols: set[str]) -> None:
    """
    Mark the top 8 news articles with top_story=True for strip display.
    Held-symbol articles ranked first, then by news_score descending.
    Only runs when no macro_event articles exist.
    """
    if any(a.get("macro_event") for a in articles):
        return
    eligible = [a for a in articles if a.get("headline")]
    eligible.sort(key=lambda a: (
        1 if any(s in held_symbols for s in (a.get("symbols") or [])) else 0,
        a.get("news_score", 0),
    ), reverse=True)
    for a in eligible[:8]:
        a["top_story"] = True


def _get_news_payload() -> dict:
    """
    Build the /api/news payload.
    Source priority:
      1. FMP stock news — article-specific images, reliable, 15-min cache
      2. Alpha Vantage NEWS_SENTIMENT — fallback with banner_image
      3. Alpaca/Benzinga — fallback when both above unavailable
      4. Yahoo RSS — last resort
    Payload cached for 60 seconds.
    """
    now = _time.time()
    if _news_payload_cache["data"] and now - _news_payload_cache["fetched_at"] < _NEWS_CACHE_TTL:
        return _news_payload_cache["data"]

    # ── Primary: FMP (article-specific images, stable API) ─────────────────────
    articles = []
    try:
        from fmp_client import get_fmp_news_articles as _fmp_articles
        from scanner import CORE_SYMBOLS, MOMENTUM_FALLBACK

        open_syms = [p.get("symbol", "") for p in dash.get("positions", [])]
        favs = list(dash.get("favourites", []))
        syms = list(dict.fromkeys([s for s in open_syms if s] + favs + MOMENTUM_FALLBACK + CORE_SYMBOLS))[:50]
        articles = _fmp_articles(syms, limit=50)
    except Exception as _e:
        log.debug("FMP articles fetch failed: %s", _e)

    # ── Secondary: Alpha Vantage ────────────────────────────────────────────────
    if not articles:
        try:
            from alpha_vantage_client import get_news_articles as _av_articles
            from scanner import CORE_SYMBOLS, MOMENTUM_FALLBACK

            open_syms = [p.get("symbol", "") for p in dash.get("positions", [])]
            favs = list(dash.get("favourites", []))
            av_syms = list(dict.fromkeys([s for s in open_syms if s] + favs + MOMENTUM_FALLBACK + CORE_SYMBOLS))[:50]
            articles = _av_articles(av_syms, limit=50)
        except Exception as _e:
            log.debug("AV articles fetch failed: %s", _e)

    # ── Tertiary: Alpaca/Benzinga ───────────────────────────────────────────────
    if not articles:
        articles = _fetch_alpaca_news()
        articles = [a for a in articles if a.get("headline", "").strip()]

    # Enrich with sentiment from the last scan cycle's per-symbol data
    news_data = dash.get("news_data", {})
    for art in articles:
        for sym in art.get("symbols", []):
            nd = news_data.get(sym, {})
            if nd:
                art["sentiment"] = nd.get("claude_sentiment", "NEUTRAL") or "NEUTRAL"
                art["news_score"] = nd.get("news_score", 0)
                art["catalyst"] = nd.get("claude_catalyst", "") or ""
                break

    # Fallback: if Alpaca returned nothing useful, fetch Yahoo RSS directly
    if not articles:
        try:
            from news import batch_news_sentiment
            from scanner import CORE_SYMBOLS, MOMENTUM_FALLBACK

            open_syms = [p.get("symbol", "") for p in dash.get("positions", [])]
            favs = list(dash.get("favourites", []))
            symbols = list(dict.fromkeys([s for s in open_syms if s] + favs + MOMENTUM_FALLBACK + CORE_SYMBOLS))[:30]
            rss_data = batch_news_sentiment(symbols)
            dash["news_data"] = rss_data  # update state so poll() sees it too
            for sym, nd in rss_data.items():
                for hl in (nd.get("headlines") or [])[:2]:
                    if not hl.strip():
                        continue
                    articles.append(
                        {
                            "headline": hl,
                            "summary": nd.get("claude_catalyst", ""),
                            "url": f"https://finance.yahoo.com/quote/{sym}/news/",
                            "source": "Yahoo RSS",
                            "author": "",
                            "symbols": [sym],
                            "image_url": None,
                            "age_hours": nd.get("recency_hours", 0),
                            "created_ts": 0,
                            "sentiment": nd.get("claude_sentiment", "NEUTRAL"),
                            "news_score": nd.get("news_score", 0),
                            "catalyst": nd.get("claude_catalyst", ""),
                        }
                    )
        except Exception as _e:
            log.warning("Yahoo RSS fallback error: %s", _e)

    # Secondary fallback: surface last scan's headlines if RSS also failed
    if not articles and news_data:
        for sym, nd in news_data.items():
            for hl in (nd.get("headlines") or [])[:2]:
                if not hl.strip():
                    continue
                articles.append(
                    {
                        "headline": hl,
                        "summary": nd.get("claude_catalyst", ""),
                        "url": f"https://finance.yahoo.com/quote/{sym}/news/",
                        "source": "Yahoo RSS",
                        "author": "",
                        "symbols": [sym],
                        "image_url": None,
                        "age_hours": nd.get("recency_hours", 0),
                        "created_ts": 0,
                        "sentiment": nd.get("claude_sentiment", "NEUTRAL"),
                        "news_score": nd.get("news_score", 0),
                        "catalyst": nd.get("claude_catalyst", ""),
                    }
                )

    # Fill in article images via og:image scraping
    _enrich_images(articles)

    # Enrich articles with sector tags from FMP (cached per symbol, permanent in-process)
    try:
        _enrich_sectors(articles)
    except Exception as _se:
        log.warning("sector enrich skipped: %s", _se)

    # Identify macro market-moving events via Sonnet (cached by content hash)
    # Evict stale cache entries older than 30 articles to prevent empty-list lock-in
    if len(_macro_cache) > 30:
        _macro_cache.clear()
    _enrich_macro_events(articles)

    # If Sonnet found nothing, inject a calendar-backed synthetic macro article
    # so the macro strip always surfaces scheduled FOMC/CPI/NFP events.
    _calendar_macro_fallback(articles)

    # Tier-3 fallback: both above found nothing — promote top news as strip cards,
    # boosting articles that mention currently held positions.
    _tag_top_news_cards(articles, _get_held_symbols())

    payload = {
        "articles": articles,
        "sentinel_triggers": list(dash.get("sentinel_triggers", [])),
        "catalyst_triggers": list(dash.get("catalyst_triggers", [])),
    }
    _news_payload_cache["data"] = payload
    _news_payload_cache["fetched_at"] = now
    return payload


def _stock_logo(symbol: str) -> str:
    """
    Return a company logo URL for the given ticker.
    Uses Financial Modeling Prep image-stock API — free, no key, ticker-based.
    Works for all US equities and major ETFs.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return ""
    return f"https://financialmodelingprep.com/image-stock/{sym}.png"


def _fetch_og_image(url: str) -> str:
    """
    Fetch the primary story image from an article URL.
    Tries og:image, twitter:image, and twitter:image:src meta tags.
    Returns '' on any failure.
    """
    if not url or not url.startswith("http"):
        return ""
    try:
        import requests as _req

        resp = _req.get(
            url,
            timeout=6,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        html = resp.text[:80_000]
        # Try og:image, twitter:image, twitter:image:src — all are reliable story photos
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I)
            if m:
                img = m.group(1).strip()
                if img.startswith("http"):
                    return img
        return ""
    except Exception:
        return ""


def _enrich_images(articles: list) -> None:
    """
    Fill image_url for articles missing one.
    1. Try og:image from the article URL (parallel, capped at 4 s total).
    2. Fall back to company logo from FMP.
    """
    needs = [
        a
        for a in articles
        if not a.get("image_url")
        and a.get("url", "").startswith("http")
        and "yahoo.com/quote/" not in a.get("url", "")  # generic listing pages — skip
    ]
    if needs:
        try:
            with ThreadPoolExecutor(max_workers=12) as pool:
                futures = {pool.submit(_fetch_og_image, a["url"]): a for a in needs}
                for fut in as_completed(futures, timeout=15):
                    art = futures[fut]
                    try:
                        img = fut.result(timeout=0)
                        if img:
                            art["image_url"] = img
                    except Exception:
                        pass
        except Exception:
            pass  # timeout or pool error — proceed with whatever completed

    # Logo fallback for articles still without an image after og:image scraping
    for a in articles:
        if not a.get("image_url"):
            syms = a.get("symbols", [])
            if syms:
                a["image_url"] = _stock_logo(syms[0])


# ── Real-time trade data helpers ──────────────────────────────────────────────
_TRADE_EVENTS_LOG = CONFIG.get("trade_events_log", "data/trade_events.jsonl")

import training_store as _training_store

_TR_CACHE: list = []
_TR_CACHE_MTIME: float = 0.0

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _run_intelligence_pipeline() -> None:
    """Run run_intelligence_pipeline.py as a subprocess. Called in a daemon thread."""
    import time as _t
    with _intel_pipeline_lock:
        _intel_pipeline_state["running"] = True
        _intel_pipeline_state["triggered_at"] = _t.time()
        _intel_pipeline_state["error"] = None
    try:
        result = subprocess.run(
            [sys.executable, "run_intelligence_pipeline.py"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[:500]
            with _intel_pipeline_lock:
                _intel_pipeline_state["error"] = err
            clog("ERROR", f"Intelligence pipeline failed (rc={result.returncode}): {err[:200]}")
        else:
            clog("INFO", "✓ Intelligence pipeline completed successfully")
    except subprocess.TimeoutExpired:
        with _intel_pipeline_lock:
            _intel_pipeline_state["error"] = "timeout after 600s"
        clog("ERROR", "Intelligence pipeline timed out after 600s")
    except Exception as exc:
        with _intel_pipeline_lock:
            _intel_pipeline_state["error"] = str(exc)
        clog("ERROR", f"Intelligence pipeline error: {exc}")
    finally:
        with _intel_pipeline_lock:
            _intel_pipeline_state["running"] = False


def _load_training_records() -> list:
    """Load training_records.jsonl with mtime-based cache.

    Normalises field names to match the reconciler convention used by the
    dashboard frontend: fill_price → entry_price, ts_close → timestamp.
    """
    global _TR_CACHE, _TR_CACHE_MTIME
    try:
        path = _training_store._STORE_FILE
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if mtime == _TR_CACHE_MTIME:
            return _TR_CACHE
        raw = _training_store.load()
        normalised = []
        for r in raw:
            rec = dict(r)
            rec.setdefault("entry_price", r.get("fill_price"))
            rec.setdefault("timestamp", r.get("ts_close", ""))
            normalised.append(rec)
        _TR_CACHE = normalised
        _TR_CACHE_MTIME = mtime
    except Exception:
        pass
    return _TR_CACHE


def _todays_closed_trades_from_events() -> list:
    """Today's closed trades from trade_events.jsonl — written with fsync on close, zero lag.

    Joins POSITION_CLOSED with its ORDER_INTENT for entry metadata.
    Returns dicts compatible with renderTodaysTrades() (action='CLOSE', exit_price set).
    """
    today = _time.strftime("%Y-%m-%d", _time.gmtime())
    if not os.path.exists(_TRADE_EVENTS_LOG):
        return []
    intents: dict = {}
    fills: dict = {}
    results = []
    try:
        with open(_TRADE_EVENTS_LOG, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        event = rec.get("event")
        tid = rec.get("trade_id")
        if not tid:
            continue
        if event == "ORDER_INTENT":
            intents[tid] = rec
        elif event == "ORDER_FILLED":
            fills[tid] = rec
        elif event == "POSITION_CLOSED" and rec.get("ts", "")[:10] == today and rec.get("exit_reason") != "manual_repair":
            intent = intents.get(tid, {})
            fill = fills.get(tid, {})
            entry_price = fill.get("fill_price") or intent.get("intended_price") or 0
            results.append({
                "timestamp": rec.get("ts", ""),
                "exit_time": rec.get("ts", ""),
                "symbol": rec.get("symbol") or intent.get("symbol", ""),
                "direction": intent.get("direction", "LONG"),
                "trade_type": intent.get("trade_type", "INTRADAY"),
                "instrument": intent.get("instrument", "stock"),
                "action": "CLOSE",
                "entry_price": entry_price,
                "exit_price": rec.get("exit_price"),
                "pnl": rec.get("pnl"),
                "exit_reason": rec.get("exit_reason"),
                "hold_minutes": rec.get("hold_minutes", 0),
                "score": intent.get("score", 0),
                "qty": fill.get("fill_qty") or intent.get("qty") or 0,
                "reasoning": intent.get("reasoning", ""),
                "entry_thesis": intent.get("entry_thesis", ""),
                "score_breakdown": intent.get("signal_scores", {}),
            })
    return results


# Cooldown for /api/ask — prevents accidental repeated wake-word submissions
_ask_last_ts: float = 0.0
_ASK_COOLDOWN_SECS: float = 1.5

# Mobile Ask rate limiting — separate from dashboard ask, stricter
_mobile_ask_last_ts: float = 0.0
_MOBILE_ASK_COOLDOWN_SECS: float = 10.0  # 10 s per-request floor
_mobile_ask_window_start: float = 0.0
_mobile_ask_window_count: int = 0
_MOBILE_ASK_MAX_PER_5MIN: int = 20  # hard cap: 20 asks per 5-minute window


class DashHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                from pathlib import Path
                html = (Path(__file__).parent / "static" / "dashboard.html").read_text()
            except Exception:
                _bot = sys.modules.get("bot")
                html = (_bot.DASHBOARD_HTML if _bot else "") or ""
            try:
                import importlib.util
                from pathlib import Path as _Path
                _vpath = _Path(__file__).parent / "version.py"
                _spec = importlib.util.spec_from_file_location("_ver", _vpath)
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                html = html.replace("__DECIFER_VERSION__", _mod.__version__)
            except Exception:
                html = html.replace("__DECIFER_VERSION__", "?")
            if self._is_remote_request():
                html = html.replace("<body>", '<body class="remote-mode">', 1)
            self.wfile.write(html.encode())
        elif self.path == "/api/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            # Include current settings so dashboard form can show live values
            state = dict(dash)
            # Always use live active_trades — reconcile updates active_trades but
            # not dash["positions"], so dash can lag until the next scan cycle.
            try:
                from orders_portfolio import get_open_positions as _gop
                live_positions = _gop()
                # Supplement with any ORDER_FILLED-without-POSITION_CLOSED entries not
                # yet reconciled into active_trades (fills that arrived between scan cycles).
                import re as _re
                from datetime import datetime as _fdt, timezone as _ftz
                from event_log import open_trades as _open_trades

                def _opt_underlying(sym: str) -> str:
                    """Extract underlying ticker from an option contract symbol like AMZN_C_267.5_2026-05-15."""
                    m = _re.match(r'^([A-Z]+)_[CP]_', sym or "")
                    return m.group(1) if m else sym

                _SCAN_TTL = CONFIG.get("scan_interval_seconds", 300)
                active_symbols = {p.get("symbol") for p in live_positions}
                # Also include underlyings so options keyed by underlying don't leak through
                active_underlyings = {_opt_underlying(s) for s in active_symbols}

                try:
                    from price_updater import get_live_prices as _glp
                    _live_px = _glp()
                except Exception:
                    _live_px = {}

                for tid, ev_pos in _open_trades().items():
                    ev_sym = ev_pos.get("symbol", "")
                    # Skip if already tracked in active_trades (by symbol or underlying)
                    if ev_sym in active_symbols or _opt_underlying(ev_sym) in active_underlyings:
                        continue
                    # Skip fills older than one scan cycle — reconcile has run, position is gone
                    fill_ts_str = ev_pos.get("ts") or ev_pos.get("fill_time") or ""
                    if fill_ts_str:
                        try:
                            _fill_age = (_fdt.now(_ftz.utc) - _fdt.fromisoformat(fill_ts_str.replace("Z", "+00:00"))).total_seconds()
                            if _fill_age > _SCAN_TTL:
                                continue
                        except Exception:
                            pass
                    # Populate current price from live price cache
                    _underlying_sym = _opt_underlying(ev_sym)
                    _px = _live_px.get(_underlying_sym) or _live_px.get(ev_sym) or {}
                    _current = _px.get("mid") or 0
                    live_positions.append({
                        "symbol": ev_sym,
                        "trade_id": tid,
                        "direction": ev_pos.get("direction", "LONG"),
                        "trade_type": ev_pos.get("trade_type", "INTRADAY"),
                        "instrument": ev_pos.get("instrument", "stock"),
                        "entry": ev_pos.get("entry") or ev_pos.get("fill_price") or ev_pos.get("intended_price") or 0,
                        "qty": ev_pos.get("qty") or ev_pos.get("fill_qty") or 0,
                        "current": _current,
                        "status": "FILLED",
                        "sl": ev_pos.get("sl"),
                        "tp": ev_pos.get("tp"),
                        "score": ev_pos.get("score", 0),
                        "_pending_reconciliation": True,
                    })
                state["positions"] = live_positions
            except Exception:
                pass
            # Today's trades: IBKR fills as ground truth + event_log metadata.
            # Falls back to event_log-only when IBKR is offline (ibkr_match='unmatched').
            # Historical trades (pre-today) come from training_records.jsonl — the
            # authoritative append-only store that replaced trades.json (2026-04-28).
            try:
                today = _time.strftime("%Y-%m-%d", _time.gmtime())
                today_trades = ibkr_reconciler.reconcile_closes(bot_state.ib, cutover_date=today)
                cached = _load_training_records()
                hist = [t for t in cached if (t.get("timestamp") or t.get("ts_close") or "")[:10] != today]
                state["all_trades"] = hist + today_trades
            except Exception:
                pass
            # Total P&L = NetLiquidation - effective capital (starting + deposits - withdrawals)
            from learning import get_effective_capital

            eff_cap = get_effective_capital()
            state["effective_capital"] = eff_cap
            # Extended account metrics for KPI row
            state["account_details"] = get_account_details()
            if state.get("performance"):
                state["performance"] = dict(state["performance"])
                state["performance"]["total_pnl"] = round(state.get("portfolio_value", 0) - eff_cap, 2)
            # Directional skew (roadmap #07)
            try:
                regime_name = (state.get("regime") or {}).get("regime", "UNKNOWN")
                from learning import get_directional_skew

                state["skew"] = {
                    "48h": get_directional_skew(window_hours=48, regime=regime_name),
                    "7d": get_directional_skew(window_hours=168, regime=regime_name),
                }
            except Exception:
                state["skew"] = None
            # Last decision + decision history — derived from trade_events.jsonl ORDER_INTENT.
            # _write_last_decision() was a dead function post-Apex 3.0; ORDER_INTENT is
            # the authoritative write-ahead record for every executed entry.
            try:
                import os as _os
                from datetime import datetime as _dte, timezone as _dtz

                _te_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "trade_events.jsonl")
                _intents: list[dict] = []
                if _os.path.exists(_te_path):
                    with open(_te_path) as _tef:
                        for _tel in _tef:
                            _tel = _tel.strip()
                            if not _tel:
                                continue
                            try:
                                _tev = json.loads(_tel)
                                if _tev.get("event") == "ORDER_INTENT":
                                    _intents.append(_tev)
                            except Exception:
                                pass
                # Map ORDER_INTENT → trade card schema (newest first)
                def _intent_to_card(ev: dict) -> dict:
                    _ts = ev.get("ts", "")
                    try:
                        _dt = _dte.fromisoformat(_ts.replace("Z", "+00:00"))
                        _ts_fmt = _dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
                    except Exception:
                        _ts_fmt = _ts
                    return {
                        "symbol": ev.get("symbol", ""),
                        "direction": ev.get("direction", "LONG"),
                        "trade_type": ev.get("trade_type", ""),
                        "conviction": ev.get("conviction"),
                        "score": ev.get("score"),
                        "price": ev.get("intended_price", 0),
                        "stop_loss": ev.get("sl", 0),
                        "take_profit": ev.get("tp", 0),
                        "thesis": ev.get("reasoning") or ev.get("entry_thesis") or "",
                        "edge_why_now": ev.get("entry_thesis", ""),
                        "risk": "",
                        "price_targets": {},
                        "timestamp": _ts_fmt,
                        "signal_scores": ev.get("signal_scores", {}),
                    }
                _cards = [_intent_to_card(e) for e in reversed(_intents)]
                state["last_decision"] = _cards[0] if _cards else None
                state["decision_history"] = _cards[:50]
            except Exception:
                state["last_decision"] = None
                state["decision_history"] = []
            # Session trades — reconstruct today's ORDER_INTENT entries from trade_events.jsonl.
            # Supplements dash["trades"] (in-memory, resets on bot restart) so the panel
            # stays populated across restarts. Uses UTC date of most recent entry as the
            # session date (avoids server timezone vs UTC timestamp mismatch).
            try:
                _session_trades: list[dict] = []
                if _intents:
                    # Most recent intent's UTC date is the session date
                    _latest_ts = _intents[-1].get("ts", "")
                    _session_date = _latest_ts[:10]  # "YYYY-MM-DD" in UTC
                    for _si in reversed(_intents):  # newest first
                        _si_ts = _si.get("ts", "")
                        if not _si_ts.startswith(_session_date):
                            continue
                        try:
                            _si_dt = _dte.fromisoformat(_si_ts.replace("Z", "+00:00"))
                            _si_time = _si_dt.astimezone().strftime("%H:%M:%S")
                        except Exception:
                            _si_time = _si_ts[11:19]
                        _si_dir = (_si.get("direction") or "LONG").upper()
                        _session_trades.append({
                            "side": "BUY" if _si_dir == "LONG" else "SHORT",
                            "symbol": _si.get("symbol", ""),
                            "price": str(round(float(_si.get("intended_price") or 0), 2)),
                            "time": _si_time,
                            "reason": _si.get("reasoning") or "",
                            "trade_type": _si.get("trade_type") or "",
                            "conviction": _si.get("conviction"),
                        })
                if _session_trades:
                    state["trades"] = _session_trades
            except Exception:
                pass  # fall back to in-memory dash["trades"]
            # Apex conversation history — last 30 scan cycles for Apex tab browsing
            try:
                _alog_path = _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)), "data", "apex_conversation_log.jsonl"
                )
                if _os.path.exists(_alog_path):
                    with open(_alog_path) as _alf:
                        _alines = [l.strip() for l in _alf if l.strip()]
                        state["apex_conversation_history"] = [json.loads(l) for l in _alines[-30:]]
                else:
                    state["apex_conversation_history"] = []
            except Exception:
                state["apex_conversation_history"] = []
            state["settings"] = {
                "risk_pct_per_trade": CONFIG["risk_pct_per_trade"],
                "daily_loss_limit": CONFIG["daily_loss_limit"],
                "max_positions": CONFIG["max_positions"],
                "min_cash_reserve": CONFIG["min_cash_reserve"],
                "max_single_position": CONFIG["max_single_position"],
                "min_score_to_trade": CONFIG["min_score_to_trade"],
                "high_conviction_score": CONFIG["high_conviction_score"],
                "agents_required_to_agree": CONFIG["agents_required_to_agree"],
                "options_min_score": CONFIG["options_min_score"],
                "options_max_risk_pct": CONFIG["options_max_risk_pct"],
                "options_max_ivr": CONFIG["options_max_ivr"],
                "options_target_delta": CONFIG["options_target_delta"],
                "options_delta_range": CONFIG["options_delta_range"],
                "options_dte_min": CONFIG["iv_skew"]["dte_min"],
                "options_dte_max": CONFIG["iv_skew"]["dte_max"],
                # Sentinel settings
                "sentinel_enabled": CONFIG["sentinel_enabled"],
                "sentinel_poll_seconds": CONFIG["sentinel_poll_seconds"],
                "sentinel_cooldown_minutes": CONFIG["sentinel_cooldown_minutes"],
                "sentinel_risk_multiplier": CONFIG["sentinel_risk_multiplier"],
                "sentinel_keyword_threshold": CONFIG["sentinel_keyword_threshold"],
                "sentinel_min_confidence": CONFIG["sentinel_min_confidence"],
                "sentinel_use_ibkr": CONFIG["sentinel_use_ibkr"],
                "sentinel_use_finviz": CONFIG["sentinel_use_finviz"],
            }
            try:
                from risk import get_consecutive_losses, get_pause_until, get_strategy_mode, get_strategy_mode_params

                state["consecutive_losses"] = get_consecutive_losses()
                state["consecutive_loss_pause"] = CONFIG["consecutive_loss_pause"]
                state["strategy_mode"] = get_strategy_mode()
                state["strategy_mode_params"] = get_strategy_mode_params()
                state["pause_until"] = get_pause_until()
            except Exception:
                state["consecutive_losses"] = 0
                state["consecutive_loss_pause"] = None
                state["strategy_mode"] = None
                state["strategy_mode_params"] = None
                state["pause_until"] = None
            state["active_dimensions"] = [k for k, v in CONFIG["dimension_flags"].items() if v]
            self.wfile.write(json.dumps(state).encode())
        elif self.path == "/api/favourites":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"favourites": dash.get("favourites", [])}).encode())
        elif self.path == "/api/ic_weights":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import json as _json
                import os as _os

                from ic_calculator import (
                    get_current_weights,
                    get_ic_weight_history,
                )

                weights = get_current_weights()
                history = get_ic_weight_history(last_n=4)
                # Read raw_ic and metadata from cache file if available
                _wf = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "ic_weights.json")
                raw_ic = {}
                updated = None
                n_records = 0
                using_equal = True
                if _os.path.exists(_wf):
                    try:
                        with open(_wf) as _f:
                            _d = _json.load(_f)
                        raw_ic = _d.get("raw_ic", {})
                        updated = _d.get("updated")
                        n_records = _d.get("n_records", 0)
                        using_equal = _d.get("using_equal_weights", True)
                    except Exception as e:
                        log.warning("[dashboard][/api/ic-weights] failed to read ic_weights.json: %s", e)
                payload = {
                    "weights": weights,
                    "raw_ic": raw_ic,
                    "updated": updated,
                    "n_records": n_records,
                    "using_equal_weights": using_equal,
                    "history": history,
                }
            except Exception as exc:
                log.warning("ic_weights error: %s", exc)
                payload = {"error": str(exc), "weights": {}, "history": []}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/analytics":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from analytics import get_analytics
                payload = get_analytics()
            except Exception as exc:
                log.warning("analytics error: %s", exc)
                payload = {"error": str(exc)}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path.startswith("/api/analytics/explain"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                force = qs.get("force", ["0"])[0].lower() in ("1", "true", "yes")
                from analytics import explain_analytics
                payload = explain_analytics(force=force)
            except Exception as exc:
                log.warning("analytics/explain error: %s", exc)
                payload = {"error": str(exc)}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/alpha_decay":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from alpha_decay import get_alpha_decay_stats

                stats = get_alpha_decay_stats()
            except Exception as exc:
                log.warning("alpha_decay error: %s", exc)
                stats = {"error": str(exc), "trade_count": 0, "horizons": [], "groups": {}, "optimal_horizon": None}
            self.wfile.write(json.dumps(stats).encode())
        elif self.path == "/api/portfolio":
            # Multi-account aggregated position view
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from portfolio import get_aggregate_summary

                summary = get_aggregate_summary(bot_state.ib)
                # Enrich with trade_type/conviction/entry_regime from bot tracker
                from orders_portfolio import get_open_positions as _get_ops

                _bot_pos = {p.get("symbol", "").upper(): p for p in (_get_ops() or [])}
                for pos in summary.get("positions", {}).values():
                    bp = _bot_pos.get(pos.get("symbol", "").upper(), {})
                    pos["trade_type"] = bp.get("trade_type", "")
                    pos["conviction"] = bp.get("conviction", 0.0)
                    pos["entry_regime"] = bp.get("entry_regime", "")
            except Exception as exc:
                log.warning("Portfolio aggregation error: %s", exc)
                summary = {"accounts": [], "positions": {}, "totals": {}, "error": str(exc)}
            self.wfile.write(json.dumps(summary).encode())
        elif self.path == "/api/thesis-performance":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from pattern_library import get_thesis_performance

                rows = get_thesis_performance(min_samples=3)
                payload = {"rows": rows}
            except Exception as exc:
                log.warning("thesis_performance error: %s", exc)
                payload = {"error": str(exc), "rows": []}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path.startswith("/api/img-proxy"):
            from urllib.parse import parse_qs as _parse_qs
            from urllib.parse import unquote as _unquote
            from urllib.parse import urlparse as _urlparse

            import requests as _req

            qs = _parse_qs(_urlparse(self.path).query)
            img_url = _unquote((qs.get("url") or [""])[0])
            ref_url = _unquote((qs.get("ref") or [""])[0])
            if not img_url or not img_url.startswith("http"):
                self.send_response(400)
                self.end_headers()
                return
            try:
                # Use the article page URL as Referer when available — CDNs validate that
                # images are loaded from the article page domain, not the CDN's own domain.
                # Fall back to the image CDN's origin for logo/direct-CDN URLs with no article.
                _parsed_img = _urlparse(img_url)
                if ref_url and ref_url.startswith("http"):
                    _referer = ref_url
                else:
                    _referer = f"{_parsed_img.scheme}://{_parsed_img.netloc}/"
                r = _req.get(
                    img_url,
                    timeout=5,
                    allow_redirects=True,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                        "Referer": _referer,
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                ct = r.headers.get("content-type", "image/jpeg")
                # Accept image/* and octet-stream (some CDNs use generic binary CT for images)
                if r.status_code == 200 and ("image" in ct or "octet-stream" in ct):
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.send_header("Content-Length", str(len(r.content)))
                    self.end_headers()
                    self.wfile.write(r.content)
                else:
                    self.send_response(404)
                    self.end_headers()
            except Exception:
                self.send_response(404)
                self.end_headers()
        elif self.path.startswith("/api/article-proxy"):
            from urllib.parse import parse_qs as _parse_qs
            from urllib.parse import unquote as _unquote
            from urllib.parse import urlparse as _urlparse

            import requests as _req

            qs = _parse_qs(_urlparse(self.path).query)
            art_url = _unquote((qs.get("url") or [""])[0])
            if not art_url or not art_url.startswith("http"):
                self.send_response(400)
                self.end_headers()
                return
            try:
                r = _req.get(
                    art_url,
                    timeout=10,
                    allow_redirects=True,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                    },
                )
                if r.status_code == 200:
                    dark_css = (
                        "<style>"
                        "html,body{background:#0d0d14!important;color:#d8d8e0!important;"
                        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif!important;"
                        "margin:0;padding:16px!important;font-size:14px!important;line-height:1.7!important}"
                        "a{color:#ff8c00!important}img{max-width:100%!important;height:auto!important}"
                        "h1,h2,h3{color:#fff!important;font-weight:700!important}"
                        "nav,header,footer,aside,[class*='ad'],[id*='ad'],[class*='sidebar'],"
                        "[class*='related'],[class*='newsletter'],[class*='subscribe']"
                        "{display:none!important}"
                        "</style>"
                    )
                    html = r.text
                    if "<head" in html:
                        html = html.replace("<head>", "<head>" + dark_css, 1)
                    elif "<HEAD" in html:
                        html = html.replace("<HEAD>", "<HEAD>" + dark_css, 1)
                    else:
                        html = dark_css + html
                    content = html.encode("utf-8", errors="replace")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self.send_response(r.status_code)
                    self.end_headers()
            except Exception as e:
                log.warning("article-proxy error: %s", e)
                self.send_response(502)
                self.end_headers()
        elif self.path == "/api/news":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                payload = _get_news_payload()
            except Exception as exc:
                log.warning("news API error: %s", exc)
                payload = {"articles": [], "sentinel_triggers": [], "catalyst_triggers": [], "error": str(exc)}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/catalyst":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                payload = _get_catalyst_payload()
            except Exception as exc:
                log.warning("catalyst API error: %s", exc)
                payload = {"candidates": [], "edgar_events": [], "date_str": "", "total_candidates": 0, "error": str(exc)}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/alpha-gate":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from phase_gate import get_status

                status = get_status()
                payload = status.as_dict() if hasattr(status, "as_dict") else vars(status)
            except Exception as exc:
                log.warning("alpha_gate error: %s", exc)
                payload = {"error": str(exc)}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/sectors":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from alpha_vantage_client import get_sector_performance
                from scanner import _SECTOR_ETFS, get_sector_rotation_bias

                bias = get_sector_rotation_bias()
                av_perf = get_sector_performance()
                payload = {
                    "leaders": [
                        {"etf": etf, "name": _SECTOR_ETFS.get(etf, etf), "rs_5d": round(rs, 2)}
                        for etf, rs in bias.get("ranked", [])[:3]
                    ]
                    if bias.get("available")
                    else [],
                    "laggards": [
                        {"etf": etf, "name": _SECTOR_ETFS.get(etf, etf), "rs_5d": round(rs, 2)}
                        for etf, rs in bias.get("ranked", [])[-3:][::-1]
                    ]
                    if bias.get("available")
                    else [],
                    "ranked": [
                        {"etf": etf, "name": _SECTOR_ETFS.get(etf, etf), "rs_5d": round(rs, 2)}
                        for etf, rs in bias.get("ranked", [])
                    ]
                    if bias.get("available")
                    else [],
                    "available": bias.get("available", False),
                    "av_performance": av_perf,
                    "updated": dash.get("last_scan"),
                }
            except Exception as exc:
                log.warning("sectors error: %s", exc)
                payload = {
                    "error": str(exc),
                    "leaders": [],
                    "laggards": [],
                    "ranked": [],
                    "available": False,
                    "av_performance": {},
                }
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/dimensions":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "dimensions": [
                    {"key": "trend", "label": "Trend", "description": "EMA alignment and slope across timeframes"},
                    {"key": "momentum", "label": "Momentum", "description": "RSI, MACD, rate-of-change"},
                    {
                        "key": "squeeze",
                        "label": "Squeeze",
                        "description": "Bollinger/Keltner squeeze and volatility contraction",
                    },
                    {"key": "flow", "label": "Flow", "description": "Volume flow, OBV, accumulation/distribution"},
                    {
                        "key": "breakout",
                        "label": "Breakout",
                        "description": "Price levels, ATR breakout, range expansion",
                    },
                    {"key": "mtf", "label": "MTF", "description": "Multi-timeframe alignment (1m/5m/15m/1h/1d)"},
                    {"key": "news", "label": "News", "description": "News sentiment and catalyst scoring"},
                    {"key": "social", "label": "Social", "description": "Social signal and short-squeeze screening"},
                    {
                        "key": "reversion",
                        "label": "Reversion",
                        "description": "Mean-reversion opportunity (RSI extremes, Bollinger bands)",
                    },
                ]
            }
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/overnight-notes":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import time as _t
                import zoneinfo as _zi
                from datetime import datetime as _dt
                from overnight_research import JSON_PATH, load_overnight_notes

                payload: dict = {"available": False, "notes": "", "data": None, "calendar": [], "earnings": []}
                if os.path.exists(JSON_PATH):
                    weekday = _dt.now(_zi.ZoneInfo("America/New_York")).weekday()
                    max_age = 80 * 3600 if weekday in (0, 6) else 20 * 3600
                    with open(JSON_PATH) as _jf:
                        _file_data = json.load(_jf)
                    # Always expose calendar/earnings regardless of file age
                    payload["calendar"] = _get_enriched_calendar(_file_data.get("calendar", []))
                    payload["earnings"] = _file_data.get("earnings", [])
                    if _t.time() - os.path.getmtime(JSON_PATH) <= max_age:
                        payload["data"] = _file_data
                        payload["available"] = True
                notes = load_overnight_notes()
                if notes:
                    payload["notes"] = notes
                    payload["available"] = True
            except Exception as exc:
                payload = {"notes": "", "available": False, "data": None, "error": str(exc)}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/prices":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import time as _t

                from price_updater import get_live_prices

                payload = {"ts": int(_t.time()), "prices": get_live_prices()}
            except Exception as exc:
                log.warning("prices API error: %s", exc)
                payload = {"ts": 0, "prices": {}}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from bot_health import build_health_report
                payload = build_health_report()
                with _intel_pipeline_lock:
                    payload["pipeline_running"] = _intel_pipeline_state["running"]
                    payload["pipeline_error"] = _intel_pipeline_state["error"]
            except Exception as exc:
                log.warning("[dashboard][/api/health] error: %s", exc)
                payload = {"error": str(exc), "ts": ""}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/intelligence":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import time as _t
                from datetime import UTC, datetime as _dt
                _base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
                def _read_json(rel):
                    p = os.path.join(_base, rel)
                    with open(p) as _f:
                        return json.load(_f)
                market_map: dict = {}
                candidates: list = []
                themes: list = []
                theme_summary: dict = {}
                universe_summary: dict = {}
                quota_summary: dict = {}
                try:
                    ld = _read_json("intelligence/live_driver_state.json")
                    market_map = {
                        "active_drivers": ld.get("active_drivers", []),
                        "blocked_conditions": ld.get("blocked_conditions", []),
                        "mode": ld.get("mode", ""),
                        "evidence": ld.get("evidence", {}),
                    }
                except Exception:
                    pass
                try:
                    cf = _read_json("intelligence/economic_candidate_feed.json")
                    candidates = cf.get("candidates", [])
                except Exception:
                    pass
                try:
                    ta = _read_json("intelligence/theme_activation.json")
                    themes = ta.get("themes", [])
                    s = ta.get("activation_summary", {})
                    theme_summary = {"activated": s.get("activated", 0), "total_themes": s.get("total_themes", 0)}
                except Exception:
                    pass
                try:
                    uu = _read_json("live/active_opportunity_universe.json")
                    universe_summary = uu.get("universe_summary", {})
                    quota_summary = uu.get("quota_summary", {})
                except Exception:
                    pass
                payload = {
                    "ts": _dt.now(UTC).isoformat(),
                    "market_map": market_map,
                    "candidates": candidates,
                    "themes": themes,
                    "theme_summary": theme_summary,
                    "universe_summary": universe_summary,
                    "quota_summary": quota_summary,
                }
            except Exception as exc:
                log.warning("[dashboard][/api/intelligence] error: %s", exc)
                payload = {"available": False, "error": str(exc), "ts": ""}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/pm":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                _pm_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "data", "pm_engine", "decisions.jsonl",
                )
                _pm_records: list = []
                if os.path.exists(_pm_path):
                    with open(_pm_path, encoding="utf-8") as _pmf:
                        for _line in _pmf:
                            _line = _line.strip()
                            if _line:
                                try:
                                    _pm_records.append(json.loads(_line))
                                except Exception:
                                    pass
                payload = {
                    "decisions": list(reversed(_pm_records[-100:])),
                    "total": len(_pm_records),
                    "config": {
                        "enabled":             bool(CONFIG.get("ENABLE_PM_ENGINE", False)),
                        "max_actions_per_day": int(CONFIG.get("PM_MAX_ACTIONS_PER_DAY", 3)),
                        "max_action_nlv_pct":  float(CONFIG.get("PM_MAX_ACTION_NLV_PCT", 0.02)),
                        "min_hold_hours":      float(CONFIG.get("PM_MIN_HOLD_HOURS", 4.0)),
                        "cooldown_hours":      float(CONFIG.get("PM_COOLDOWN_HOURS", 2.0)),
                        "oversize_threshold":  float(CONFIG.get("PM_OVERSIZE_THRESHOLD", 0.06)),
                    },
                }
            except Exception as exc:
                log.warning("[dashboard][/api/pm] error: %s", exc)
                payload = {"decisions": [], "total": 0, "error": str(exc)}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/pm_outcomes":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import pm_outcome_tracker as _pot
                _pot_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "data", "pm_engine", "outcomes.jsonl",
                )
                import pathlib as _pl
                payload = _pot.get_summary(_pl.Path(_pot_path))
            except Exception as exc:
                log.warning("[dashboard][/api/pm_outcomes] error: %s", exc)
                payload = {"total": 0, "by_action": {}, "quality_counts": {}, "recent": [], "error": str(exc)}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/rotation":
            # Retired — rotation_live_v1 migrated to Portfolio Management Engine.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"retired": True, "use": "/api/pm"}).encode())
        # ── Mobile intelligence companion (read-only) ─────────────────────────
        elif self.path == "/mobile":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                from pathlib import Path
                html = (Path(__file__).parent / "static" / "mobile.html").read_text()
            except Exception:
                html = "<html><body><p>Mobile interface not available.</p></body></html>"
            self.wfile.write(html.encode())
        elif self.path == "/api/mobile/now":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import mobile_api as _mob
                payload = _mob.build_now_payload(dict(dash))
            except Exception as exc:
                log.warning("[mobile][/now] error: %s", exc)
                payload = {"error": "unavailable", "ts": ""}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/mobile/why":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import mobile_api as _mob
                payload = _mob.build_why_payload()
            except Exception as exc:
                log.warning("[mobile][/why] error: %s", exc)
                payload = {"error": "unavailable", "ts": ""}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/mobile/alpha":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import mobile_api as _mob
                payload = _mob.build_alpha_payload()
            except Exception as exc:
                log.warning("[mobile][/alpha] error: %s", exc)
                payload = {"error": "unavailable", "ts": ""}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/mobile/portfolio":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                import mobile_api as _mob
                payload = _mob.build_portfolio_payload(dict(dash))
            except Exception as exc:
                log.warning("[mobile][/portfolio] error: %s", exc)
                payload = {"error": "unavailable", "ts": ""}
            self.wfile.write(json.dumps(payload, default=str).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def _is_remote_request(self) -> bool:
        """True when the request arrived via Cloudflare Tunnel (not localhost)."""
        return bool(
            self.headers.get("CF-Connecting-IP")
            or self.headers.get("X-Forwarded-For")
        )

    def do_POST(self):
        # /api/mobile/ask — read-only LLM Q&A for mobile surface.
        # read_only=True blocks control intents (pause/resume) that would mutate state.
        # Stricter rate limit than dashboard ask: 10 s cooldown + 20/5 min window cap.
        if self.path == "/api/mobile/ask":
            global _mobile_ask_last_ts, _mobile_ask_window_start, _mobile_ask_window_count
            import time as _time
            now = _time.monotonic()
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            question = (body.get("question") or "").strip()
            if not question:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No question provided"}).encode())
                return
            # Per-request cooldown
            if now - _mobile_ask_last_ts < _MOBILE_ASK_COOLDOWN_SECS:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "cooldown"}).encode())
                return
            # Sliding-window cap: reset counter if window has expired
            if now - _mobile_ask_window_start >= 300:
                _mobile_ask_window_start = now
                _mobile_ask_window_count = 0
            if _mobile_ask_window_count >= _MOBILE_ASK_MAX_PER_5MIN:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "rate limit"}).encode())
                return
            _mobile_ask_last_ts = now
            _mobile_ask_window_count += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                from voice_agent import answer_voice_question
                answer = answer_voice_question(question, dash, read_only=True)
                self.wfile.write(json.dumps({"ok": True, "answer": answer}).encode())
            except Exception as exc:
                self.wfile.write(json.dumps({"ok": False, "error": str(exc)}).encode())
            return

        if self._is_remote_request():
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "write operations not available remotely"}).encode())
            return
        ib = bot_state.ib
        if self.path == "/api/reconnect":
            import bot_state as _bs
            from bot_ibkr import _on_disconnected

            _bs._manual_reconnect_evt.set()  # wake startup loop or skip backoff in running worker
            # If the auto-reconnect worker has already exhausted attempts (or never ran),
            # spawn a fresh one now so the button actually does something.
            if not _bs.ib.isConnected() and not _bs._reconnecting:
                _on_disconnected()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "msg": "Reconnect attempt triggered"}).encode())
        elif self.path == "/api/kill":
            dash["killed"] = True
            clog("RISK", "🚨 KILL SWITCH — executing FLATTEN ALL immediately...")
            # Execute immediately via emergency IB connection (separate clientId)
            try:
                flatten_all(ib)  # Uses emergency connection internally; ib is fallback
                clog("RISK", "🚨 FLATTEN ALL complete")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "detail": "All positions flattened"}).encode())
            except Exception as e:
                clog("ERROR", f"🚨 FLATTEN ALL failed: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/close":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            symbol = body.get("symbol", "").upper().strip()
            if not symbol:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No symbol provided"}).encode())
            else:
                # Execute immediately via emergency IB connection (no queuing!)
                clog("TRADE", f"📤 Closing {symbol} immediately...")
                try:
                    from orders_portfolio import close_position

                    result = close_position(ib, symbol)
                    if result:
                        clog("TRADE", f"✅ {result}")
                        dash["positions"] = get_open_positions()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "detail": result}).encode())
                    else:
                        clog("ERROR", f"❌ {symbol} not found in portfolio")
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"ok": False, "error": f"{symbol} not found in portfolio"}).encode()
                        )
                except Exception as e:
                    clog("ERROR", f"❌ Close {symbol} failed: {e}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/cancel-order":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            order_id = body.get("order_id")
            if not order_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No order_id provided"}).encode())
            else:
                try:
                    cancelled = cancel_order_by_id(ib, order_id)
                    if cancelled:
                        clog("TRADE", f"❌ Cancelled order #{order_id} via dashboard")
                    if cancelled:
                        # Update orders.json
                        from learning import update_order_status

                        update_order_status(order_id, "CANCELLED")
                        sync_orders_from_ibkr()
                        # Remove pending entry from open_trades tracker
                        from orders_state import open_trades

                        cancelled_keys = [
                            k
                            for k, v in open_trades.items()
                            if v.get("order_id") == order_id and v.get("status") == "PENDING"
                        ]
                        for k in cancelled_keys:
                            clog("TRADE", f"Removed cancelled pending order {k} from tracker")
                            del open_trades[k]
                        dash["positions"] = get_open_positions()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "detail": f"Order #{order_id} cancelled"}).encode())
                    else:
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"ok": False, "error": f"Order #{order_id} not found in open orders"}).encode()
                        )
                except Exception as e:
                    clog("ERROR", f"Cancel order #{order_id} failed: {e}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/purge-ghost":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            symbol = body.get("symbol", "").upper().strip()
            if not symbol:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No symbol"}).encode())
            else:
                from orders_portfolio import active_trades, _trades_lock, _save_positions_file
                removed = []
                with _trades_lock:
                    keys = [k for k, v in active_trades.items() if v.get("symbol") == symbol or k == symbol]
                    for k in keys:
                        del active_trades[k]
                        removed.append(k)
                if removed:
                    _save_positions_file()
                    dash["positions"] = get_open_positions()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "removed": removed}).encode())
                clog("INFO", f"Ghost purge {symbol}: removed keys {removed}")
        elif self.path == "/api/update-sl":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            symbol = body.get("symbol", "").upper().strip()
            new_sl = body.get("sl")
            new_tp = body.get("tp")
            if not symbol or new_sl is None:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "symbol and sl required"}).encode())
            else:
                from orders_portfolio import active_trades, _trades_lock, _safe_update_trade, _save_positions_file
                updates = {"sl": round(float(new_sl), 4)}
                if new_tp is not None:
                    updates["tp"] = round(float(new_tp), 4)
                matched = []
                with _trades_lock:
                    keys = [k for k, v in active_trades.items() if v.get("symbol") == symbol or k == symbol]
                for k in keys:
                    _safe_update_trade(k, updates)
                    matched.append(k)
                if matched:
                    _save_positions_file()
                    dash["positions"] = get_open_positions()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "updated": matched, "sl": updates["sl"]}).encode())
                clog("INFO", f"SL updated {symbol}: sl={updates['sl']} tp={updates.get('tp')} keys={matched}")
        elif self.path == "/api/pause":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            dash["paused"] = body.get("paused", not dash["paused"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "paused": dash["paused"]}).encode())
            clog("INFO", f"Bot {'paused' if dash['paused'] else 'resumed'} via dashboard")
        elif self.path == "/api/favourites":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            favs = [s.upper().strip() for s in body.get("favourites", []) if s.strip()]
            dash["favourites"] = favs
            _bot = sys.modules.get("bot")
            if _bot:
                _bot.save_favourites(favs)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "favourites": favs}).encode())
            clog("INFO", f"Favourites updated: {favs}")
        elif self.path == "/api/scan":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            clog("INFO", "⚡ Force scan triggered via dashboard")
            threading.Thread(target=run_scan, daemon=True).start()
        elif self.path == "/api/trigger/intelligence-pipeline":
            with _intel_pipeline_lock:
                already_running = _intel_pipeline_state["running"]
            if already_running:
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "msg": "Pipeline already running"}).encode())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "msg": "Intelligence pipeline triggered"}).encode())
                clog("INFO", "▶ Intelligence pipeline triggered via dashboard")
                threading.Thread(target=_run_intelligence_pipeline, daemon=True).start()
        elif self.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            # Apply settings directly to CONFIG (live update, no restart needed)
            applied = []
            for key, val in body.items():
                if key in CONFIG:
                    CONFIG[key] = val
                    applied.append(key)
            # Persist to disk so settings survive restarts
            _bot = sys.modules.get("bot")
            if _bot:
                _bot.save_settings_overrides(body)
                _bot._sync_dash_from_config()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "applied": applied}).encode())
            clog("INFO", f"⚙️ Settings applied & saved via dashboard: {', '.join(applied)}")
        elif self.path == "/api/capital-adjustment":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            amount = float(body.get("amount", 0))
            note = body.get("note", "")
            if amount != 0:
                from learning import record_capital_adjustment

                record_capital_adjustment(amount, note)
            from learning import get_effective_capital

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "effective_capital": get_effective_capital()}).encode())
        elif self.path == "/api/ask":
            import time as _time

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            question = (body.get("question") or "").strip()
            if not question:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No question provided"}).encode())
            elif _time.monotonic() - _ask_last_ts < _ASK_COOLDOWN_SECS:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "cooldown"}).encode())
            else:
                _ask_last_ts = _time.monotonic()
                try:
                    from voice_agent import answer_voice_question
                    from bot_voice import speak

                    answer = answer_voice_question(question, dash)
                    speak(answer)

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "ok": True,
                        "answer": answer,
                    }).encode())
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/restart":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            clog("INFO", "🔄 Restart requested via dashboard")
            # Restart in background — replace current process with fresh one
            import os as _os
            import sys as _sys

            def do_restart():
                import time

                time.sleep(1)
                ib.disconnect()
                _os.execv(_sys.executable, [_sys.executable, *_sys.argv])

            threading.Thread(target=do_restart, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def end_headers(self):
        """Inject CORS headers on every response.

        Mobile routes (/mobile, /api/mobile/*) are restricted to the mobile
        subdomain origin. All other routes keep the existing wildcard to avoid
        breaking dashboard behaviour.
        """
        _MOBILE_ORIGIN = "https://mobile.decifertrading.com"
        _path = getattr(self, "path", "") or ""
        is_mobile_route = _path == "/mobile" or _path.startswith("/api/mobile/")
        if is_mobile_route:
            req_origin = self.headers.get("Origin", "")
            allow_origin = req_origin if req_origin == _MOBILE_ORIGIN else _MOBILE_ORIGIN
        else:
            allow_origin = "*"
        self.send_header("Access-Control-Allow-Origin", allow_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass  # Suppress default HTTP logs


def _pnl_refresh_loop():
    """Refresh daily P&L from IBKR's reqPnL subscription every 5 minutes."""
    import math as _math
    while True:
        threading.Event().wait(300)
        try:
            from bot_account import get_account_data as _gad
            _, pnl = _gad()
            if pnl is not None and not _math.isnan(pnl):
                dash["daily_pnl"] = pnl
        except Exception:
            pass


def start_dashboard():
    server = ThreadingHTTPServer(("127.0.0.1", CONFIG["dashboard_port"]), DashHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_pnl_refresh_loop, daemon=True, name="pnl-refresh").start()
    clog("INFO", f"Dashboard live → http://localhost:{CONFIG['dashboard_port']}")
