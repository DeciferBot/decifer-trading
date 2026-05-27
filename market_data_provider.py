# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  market_data_provider.py                   ║
# ║   Generic market data for the intelligence cloud API        ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
market_data_provider.py — Generic (non-universe-filtered) market data.

Fetches movers, news, and tape from FMP. Results cached to disk for 5 minutes
so the intelligence API serves a consistent snapshot rather than hammering FMP
on every request. Fail-soft: returns empty payload on any FMP failure.

Layer:    data_connector
Used by:  intelligence_api.py only
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import fmp_client

log = logging.getLogger("decifer.market_data_provider")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_TTL = 300  # 5 min disk cache

_CACHE_PATHS: dict[str, str] = {
    "movers": os.path.join(_BASE_DIR, "data/intelligence/market_movers_cache.json"),
    "news":   os.path.join(_BASE_DIR, "data/intelligence/market_news_cache.json"),
    "tape":   os.path.join(_BASE_DIR, "data/intelligence/market_tape_cache.json"),
}

_ETF_TAPE = [
    ("SPY", "S&P 500",    "equity"),
    ("QQQ", "Nasdaq",     "equity"),
    ("IWM", "Small Caps", "equity"),
    ("TLT", "Bonds",      "rates"),
    ("GLD", "Gold",       "safe_haven"),
    ("USO", "Oil",        "commodity"),
    ("UUP", "US Dollar",  "dollar"),
]

_SKIP_SITES: frozenset[str] = frozenset({
    "youtube.com", "youtu.be", "rumble.com", "odysee.com", "tiktok.com", "vimeo.com",
})

_SYMBOL_THEME: dict[str, str] = {
    "NVDA": "AI Infrastructure", "AMD": "AI Infrastructure", "AVGO": "AI Infrastructure",
    "TSM": "AI Infrastructure", "SMCI": "AI Infrastructure", "DELL": "AI Infrastructure",
    "MSFT": "AI Infrastructure", "GOOGL": "AI Infrastructure", "META": "AI Infrastructure",
    "AMZN": "AI Infrastructure", "AAPL": "Tech",
    "LMT": "Defence", "RTX": "Defence", "NOC": "Defence", "BA": "Defence", "GD": "Defence",
    "PLTR": "Defence", "CACI": "Defence",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "OXY": "Energy",
    "GLD": "Gold", "GDX": "Gold", "NEM": "Gold",
    "LLY": "Healthcare", "NVO": "Healthcare", "UNH": "Healthcare",
    "TSLA": "EV & Autos", "F": "Autos", "GM": "Autos",
    "JPM": "Financials", "GS": "Financials", "MS": "Financials", "BAC": "Financials",
    "MSTR": "Digital Assets", "COIN": "Digital Assets",
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_cache(key: str) -> dict[str, Any] | None:
    path = _CACHE_PATHS[key]
    if not os.path.exists(path):
        return None
    try:
        if time.time() - os.path.getmtime(path) > _TTL:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(key: str, data: dict[str, Any]) -> None:
    try:
        with open(_CACHE_PATHS[key], "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:
        log.warning("cache write failed for %s: %s", key, exc)


def _safe_pct(price: float | None, change: float | None) -> float | None:
    if not price or change is None:
        return None
    prev = price - change
    return round((change / prev) * 100, 2) if prev else None


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

def get_movers() -> dict[str, Any]:
    """Return top 5 gainers and losers from FMP, min price $5."""
    if (cached := _load_cache("movers")):
        return cached

    def _to_mover(r: dict) -> dict[str, Any] | None:
        try:
            pct = r.get("changesPercentage", 0)
            if isinstance(pct, str):
                pct = float(pct.replace("%", ""))
            price = float(r.get("price", 0))
            return None if price < 5 else {
                "symbol": r["symbol"],
                "name": r.get("name", r["symbol"]),
                "price": price,
                "changePct": round(float(pct), 2),
                "logoUrl": f"https://images.financialmodelingprep.com/symbol/{r['symbol']}.png",
            }
        except (KeyError, ValueError, TypeError):
            return None

    gainers = [m for r in (fmp_client._get("biggest-gainers", {}, ttl=_TTL) or []) if (m := _to_mover(r))][:5]
    losers  = [m for r in (fmp_client._get("biggest-losers",  {}, ttl=_TTL) or []) if (m := _to_mover(r))][:5]

    result: dict[str, Any] = {"gainers": gainers, "losers": losers, "ts": _now_iso(), "source": "intelligence_api"}
    _save_cache("movers", result)
    return result


def get_news() -> dict[str, Any]:
    """Return up to 15 deduplicated news items (stock + general), max 24h old."""
    if (cached := _load_cache("news")):
        return cached

    now = time.time()

    def _parse(n: dict) -> dict[str, Any] | None:
        if not n.get("title"):
            return None
        site = n.get("site") or n.get("publisher") or ""
        if site in _SKIP_SITES:
            return None
        pub_str = (n.get("publishedDate") or "").replace(" ", "T")
        if pub_str and not pub_str.endswith("Z"):
            pub_str += "Z"
        try:
            minutes_ago = max(0, int((now - datetime.fromisoformat(pub_str.replace("Z", "+00:00")).timestamp()) / 60))
        except (ValueError, TypeError):
            minutes_ago = 9999
        if minutes_ago > 1440:
            return None
        source = site.replace("www.", "").rsplit(".", 1)[0] if "." in site else site
        sym = (n.get("symbol") or "").upper() or None
        return {
            "title": n["title"].strip(),
            "summary": (n.get("text") or "").strip()[:180],
            "source": source,
            "minutesAgo": minutes_ago,
            "symbol": sym,
            "themeLabel": _SYMBOL_THEME.get(sym) if sym else None,
            "logoUrl": f"https://images.financialmodelingprep.com/symbol/{sym}.png" if sym else None,
        }

    raw: list[dict] = []
    raw.extend(fmp_client._get("news/stock-latest",   {"limit": 20}, ttl=_TTL) or [])
    raw.extend(fmp_client._get("news/general-latest", {"limit": 10}, ttl=_TTL) or [])

    seen: set[str] = set()
    news: list[dict] = []
    for item in sorted(filter(None, (_parse(n) for n in raw)), key=lambda x: x["minutesAgo"]):
        key = item["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            news.append(item)
        if len(news) >= 15:
            break

    result: dict[str, Any] = {"news": news, "ts": _now_iso(), "source": "intelligence_api"}
    _save_cache("news", result)
    return result


def get_tape() -> dict[str, Any]:
    """Return ETF tape (SPY/QQQ/IWM/TLT/GLD/USO/UUP) + VIX level."""
    if (cached := _load_cache("tape")):
        return cached

    etf_syms = ",".join(s for s, _, _ in _ETF_TAPE)
    etf_raw  = fmp_client._get("batch-quote-short", {"symbols": etf_syms}, ttl=_TTL)
    vix_raw  = fmp_client._get("quote/%5EVIX", {}, ttl=_TTL)

    lookup: dict[str, dict] = {r["symbol"]: r for r in (etf_raw or [])} if etf_raw else {}
    tape: list[dict] = []
    for sym, label, tape_type in _ETF_TAPE:
        q = lookup.get(sym)
        tape.append({
            "sym": sym, "label": label, "type": tape_type,
            "changePct": _safe_pct(q.get("price") if q else None, q.get("change") if q else None),
            "level": q.get("price") if q else None,
        })

    vix_q = (vix_raw[0] if isinstance(vix_raw, list) else vix_raw) if vix_raw else None
    tape.append({
        "sym": "VIX", "label": "VIX", "type": "vol",
        "changePct": _safe_pct(vix_q.get("price") if vix_q else None, vix_q.get("change") if vix_q else None),
        "level": round(float(vix_q["price"]), 2) if vix_q and vix_q.get("price") else None,
    })

    result: dict[str, Any] = {"tape": tape, "ts": _now_iso(), "source": "intelligence_api"}
    _save_cache("tape", result)
    return result
