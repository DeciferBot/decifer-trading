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
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer

import bot_state
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

# ── Macro event classifier cache ──────────────────────────────────────────────
_macro_cache: dict = {}  # headline_hash → list of macro classifications


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
            # Skip Benzinga branding/logo images — they are not article-specific content
            _BZ_SKIP = (
                "/sites/default/",
                "benzinga-logo",
                "bz-logo",
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


def _get_news_payload() -> dict:
    """
    Build the /api/news payload.
    Source priority:
      1. Alpha Vantage NEWS_SENTIMENT — real article images (banner_image), paid key
      2. Alpaca/Benzinga — fallback when AV unavailable
      3. Yahoo RSS — last resort
    Payload cached for 60 seconds.
    """
    now = _time.time()
    if _news_payload_cache["data"] and now - _news_payload_cache["fetched_at"] < _NEWS_CACHE_TTL:
        return _news_payload_cache["data"]

    # ── Primary: Alpha Vantage (has banner_image on every article) ──────────────
    articles = []
    try:
        from alpha_vantage_client import get_news_articles as _av_articles
        from scanner import CORE_SYMBOLS, MOMENTUM_FALLBACK

        open_syms = [p.get("symbol", "") for p in dash.get("positions", [])]
        favs = list(dash.get("favourites", []))
        av_syms = list(dict.fromkeys([s for s in open_syms if s] + favs + MOMENTUM_FALLBACK + CORE_SYMBOLS))[:50]
        articles = _av_articles(av_syms, limit=50)
    except Exception as _e:
        log.debug("AV articles fetch failed: %s", _e)

    # ── Secondary: Alpaca/Benzinga ──────────────────────────────────────────────
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

    # Identify macro market-moving events via Sonnet (cached by content hash)
    # Evict stale cache entries older than 30 articles to prevent empty-list lock-in
    if len(_macro_cache) > 30:
        _macro_cache.clear()
    _enrich_macro_events(articles)

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
    """Fetch og:image from an article URL. Returns '' on any failure."""
    if not url or not url.startswith("http"):
        return ""
    try:
        import requests as _req

        resp = _req.get(
            url,
            timeout=3,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Decifer/2.0)"},
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        # Match both attribute orderings of <meta property="og:image" content="...">
        for pat in (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        ):
            m = re.search(pat, resp.text[:50_000], re.I)
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
        and "yahoo.com/quote" not in a.get("url", "")  # generic pages — skip
    ]
    if needs:
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_fetch_og_image, a["url"]): a for a in needs}
                for fut in as_completed(futures, timeout=4):
                    art = futures[fut]
                    try:
                        img = fut.result(timeout=0)
                        if img:
                            art["image_url"] = img
                    except Exception:
                        pass
        except Exception:
            pass  # timeout or pool error — proceed with whatever completed

    # No logo fallback — articles without images show the gradient ticker placeholder
    # in the frontend, which looks cleaner than a corporate logo thumbnail.


class DashHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            _bot = sys.modules.get("bot")
            html = (_bot.DASHBOARD_HTML if _bot else "") or ""
            self.wfile.write(html.encode())
        elif self.path == "/api/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # Include current settings so dashboard form can show live values
            state = dict(dash)
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
            # Last decision — for trade card on home page
            try:
                import os as _os

                _ld_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "last_decision.json")
                if _os.path.exists(_ld_path):
                    with open(_ld_path) as _ldf:
                        state["last_decision"] = json.load(_ldf)
                else:
                    state["last_decision"] = None
            except Exception:
                state["last_decision"] = None
            # Decision history — last 50 entries for trade card navigation
            try:
                _hist_path = _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)), "data", "decision_history.jsonl"
                )
                if _os.path.exists(_hist_path):
                    with open(_hist_path) as _hf:
                        _lines = [l.strip() for l in _hf if l.strip()]
                        state["decision_history"] = [json.loads(l) for l in _lines[-50:]]
                else:
                    state["decision_history"] = []
            except Exception:
                state["decision_history"] = []
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
                "sentinel_max_trades_per_hour": CONFIG["sentinel_max_trades_per_hour"],
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
            self.send_header("Access-Control-Allow-Origin", "*")
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
                    except Exception:
                        pass
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
        elif self.path == "/api/alpha_decay":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                from portfolio import get_aggregate_summary

                summary = get_aggregate_summary(bot_state.ib)
                # Enrich with trade_type/conviction/entry_regime from bot tracker
                from orders import get_open_positions as _get_ops

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
            self.send_header("Access-Control-Allow-Origin", "*")
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
            if not img_url or not img_url.startswith("http"):
                self.send_response(400)
                self.end_headers()
                return
            try:
                r = _req.get(
                    img_url,
                    timeout=5,
                    allow_redirects=True,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                    },
                )
                ct = r.headers.get("content-type", "image/jpeg")
                if r.status_code == 200 and "image" in ct:
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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                payload = _get_news_payload()
            except Exception as exc:
                log.warning("news API error: %s", exc)
                payload = {"articles": [], "sentinel_triggers": [], "catalyst_triggers": [], "error": str(exc)}
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/api/alpha-gate":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
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
            self.send_header("Access-Control-Allow-Origin", "*")
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
            self.send_header("Access-Control-Allow-Origin", "*")
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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                from overnight_research import load_overnight_notes

                notes = load_overnight_notes()
                payload = {"notes": notes, "available": bool(notes)}
            except Exception as exc:
                payload = {"notes": "", "available": False, "error": str(exc)}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/prices":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                import time as _t

                from price_updater import get_live_prices

                payload = {"ts": int(_t.time()), "prices": get_live_prices()}
            except Exception as exc:
                log.warning("prices API error: %s", exc)
                payload = {"ts": 0, "prices": {}}
            self.wfile.write(json.dumps(payload).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        ib = bot_state.ib
        if self.path == "/api/reconnect":
            import bot_state as _bs

            _bs._manual_reconnect_evt.set()  # wake the retry loop in main()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
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
                    from orders import close_position

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
                        from orders import open_trades

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
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            question = (body.get("question") or "").strip()
            if not question:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No question provided"}).encode())
            else:
                try:
                    from bot_voice import answer_voice_query

                    answer = answer_voice_query(question, dash)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "answer": answer}).encode())
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

    def log_message(self, *args):
        pass  # Suppress default HTTP logs


def start_dashboard():
    server = HTTPServer(("", CONFIG["dashboard_port"]), DashHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    clog("INFO", f"Dashboard live → http://localhost:{CONFIG['dashboard_port']}")
