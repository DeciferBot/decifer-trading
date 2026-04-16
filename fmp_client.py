# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  fmp_client.py                             ║
# ║   Financial Modeling Prep API client.                        ║
# ║   Provides economic calendar, earnings with estimates,       ║
# ║   and analyst rating changes for overnight research.         ║
# ║                                                              ║
# ║   Premium tier: 750 calls/min — full fundamentals,          ║
# ║   analyst grades, insider trades, congressional trades,     ║
# ║   DCF, news, ETF holdings, 30 years history.                ║
# ║   Set env var: FMP_API_KEY                                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
#
# API base: https://financialmodelingprep.com/stable/
# (Migrated from legacy /api/v3/ and /api/v4/ — those return HTTP 403 for new keys)

from __future__ import annotations

import json
import logging
import os
import time as _time
from datetime import UTC, date, datetime, timedelta

import requests

from config import CONFIG

log = logging.getLogger("decifer.fmp")

_BASE = "https://financialmodelingprep.com/stable"
_CACHE_TTL = 4 * 3600  # 4 hours — same cadence as Alpha Vantage

# ── Tiered cache TTLs (premium: 750 calls/min — refresh aggressively) ────────
_TTL_ANALYST      = 30 * 60       # 30 min — analyst ratings update intraday
_TTL_NEWS         = 15 * 60       # 15 min — breaking news window
_TTL_INSIDER      = 2 * 3600      # 2h — Form 4 filings
_TTL_CONGRESS     = 6 * 3600      # 6h — congressional disclosures
_TTL_FUNDAMENTALS = 24 * 3600     # 24h — quarterly data

# ── In-memory cache (key → (data, fetched_at)) ───────────────────────────────
_cache: dict[str, tuple[object, float]] = {}


def _api_key() -> str:
    return CONFIG.get("fmp_api_key", "") or os.environ.get("FMP_API_KEY", "")


def is_available() -> bool:
    return bool(_api_key())


def _get(endpoint: str, params: dict, version: str = "", ttl: float | None = None) -> list | dict | None:
    """
    Make a GET request to the FMP stable API.
    Returns parsed JSON or None on any failure.
    Premium: 750 calls/min. ttl overrides global _CACHE_TTL.
    version param retained for call-site compatibility but ignored — all calls use /stable/.
    """
    key = _api_key()
    if not key:
        log.debug("fmp_client: FMP_API_KEY not set — skipping")
        return None

    cache_key = f"{endpoint}?{json.dumps(params, sort_keys=True)}"
    cached, fetched_at = _cache.get(cache_key, (None, 0.0))
    effective_ttl = ttl if ttl is not None else _CACHE_TTL
    if cached is not None and (_time.time() - fetched_at) < effective_ttl:
        return cached

    url = f"{_BASE}/{endpoint}"
    try:
        resp = requests.get(url, params={**params, "apikey": key}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # FMP returns {"Error Message": "..."} on bad key / limit exceeded
        if isinstance(data, dict) and "Error Message" in data:
            log.warning("fmp_client: API error — %s", data["Error Message"])
            return None
        _cache[cache_key] = (data, _time.time())
        return data
    except requests.exceptions.HTTPError as exc:
        log.warning("fmp_client: HTTP %s for %s", exc.response.status_code, endpoint)
        return None
    except Exception as exc:
        log.debug("fmp_client: request failed — %s", exc)
        return None


# ── Economic Calendar ─────────────────────────────────────────────────────────


def get_economic_calendar(days_ahead: int = 7) -> list[dict]:
    """
    Fetch upcoming economic events for the next `days_ahead` days.

    Returns list of dicts, each with:
        date (str YYYY-MM-DD), event (str), country (str),
        impact (str: "High" | "Medium" | "Low"),
        estimate (float|None), previous (float|None), actual (float|None),
        unit (str)

    Filtered to US events only. Sorted by date ascending.
    """
    today = date.today()
    end_dt = today + timedelta(days=days_ahead)
    raw = _get(
        "economic-calendar",
        {"from": str(today), "to": str(end_dt)},
        ttl=_TTL_ANALYST,
    )
    if not raw or not isinstance(raw, list):
        return []

    events = []
    for item in raw:
        country = (item.get("country") or "").upper()
        if country not in ("US", "USD", ""):
            continue
        impact = item.get("impact") or ""
        # Normalise impact — FMP uses "High", "Medium", "Low", "None"
        if impact.lower() not in ("high", "medium", "low"):
            continue  # skip non-event entries
        try:
            events.append(
                {
                    "date": (item.get("date") or "")[:10],
                    "event": item.get("event") or item.get("name") or "",
                    "country": country,
                    "impact": impact.capitalize(),
                    "estimate": _safe_float(item.get("estimate")),
                    "previous": _safe_float(item.get("previous")),
                    "actual": _safe_float(item.get("actual")),
                    "unit": item.get("unit") or "",
                }
            )
        except Exception:
            continue

    return sorted(events, key=lambda x: x["date"])


# ── Earnings Calendar ─────────────────────────────────────────────────────────


def get_earnings_calendar(symbols: list[str] | None = None, days_ahead: int = 5) -> list[dict]:
    """
    Fetch upcoming earnings for the next `days_ahead` days.

    If `symbols` is provided, filters to that universe.
    Returns list of dicts, each with:
        date (str YYYY-MM-DD), symbol (str), timing (str: "BMO"|"AMC"|""),
        eps_est (float|None), eps_prior (float|None),
        revenue_est (float|None), revenue_prior (float|None)

    Sorted by date ascending.
    """
    today = date.today()
    end_dt = today + timedelta(days=days_ahead)
    raw = _get(
        "earning-calendar",
        {"from": str(today), "to": str(end_dt)},
        ttl=_TTL_ANALYST,
    )
    if not raw or not isinstance(raw, list):
        return []

    sym_set = {s.upper() for s in symbols} if symbols else None

    results = []
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if sym_set and sym not in sym_set:
            continue

        # FMP time field: "bmo" = before market open, "amc" = after market close
        timing_raw = (item.get("time") or "").lower()
        timing = "BMO" if "bmo" in timing_raw else ("AMC" if "amc" in timing_raw else "")

        results.append(
            {
                "date": (item.get("date") or "")[:10],
                "symbol": sym,
                "timing": timing,
                "eps_est": _safe_float(item.get("epsEstimated")),
                "eps_prior": _safe_float(item.get("eps")),
                "revenue_est": _safe_float(item.get("revenueEstimated")),
                "revenue_prior": _safe_float(item.get("revenue")),
            }
        )

    return sorted(results, key=lambda x: x["date"])


# ── Analyst Upgrades / Downgrades ─────────────────────────────────────────────


def get_analyst_changes(symbols: list[str] | None = None, hours_back: int = 24) -> list[dict]:
    """
    Fetch analyst upgrades/downgrades published in the last `hours_back` hours.

    If `symbols` is provided, filters to that universe.
    Returns list of dicts, each with:
        symbol (str), action (str: "upgrade"|"downgrade"|"init"|"reiterated"),
        from_grade (str), to_grade (str), firm (str),
        published_date (str ISO)

    Returns empty list if endpoint unavailable (premium-only on some plans).
    """
    raw = _get("upgrades-downgrades-rss-feed", {"page": 0}, ttl=_TTL_ANALYST)
    if not raw or not isinstance(raw, list):
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=hours_back)
    sym_set = {s.upper() for s in symbols} if symbols else None

    results = []
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if sym_set and sym not in sym_set:
            continue

        pub_str = item.get("publishedDate") or ""
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=UTC)
        except Exception:
            continue

        if pub_dt < cutoff:
            continue

        action = (item.get("action") or "").lower()

        results.append(
            {
                "symbol": sym,
                "action": action,
                "from_grade": item.get("previousGrade") or "",
                "to_grade": item.get("newGrade") or "",
                "firm": item.get("gradingCompany") or "",
                "published_date": pub_str,
            }
        )

    return sorted(results, key=lambda x: x["published_date"], reverse=True)


# ── GICS Sector → ETF map ─────────────────────────────────────────────────────
# FMP returns sector names from company profile. Map to liquid SPDR sector ETFs.
# FMP sometimes uses "Healthcare" (no space) — both forms handled in get_company_sector().

GICS_ETF_MAP: dict[str, str] = {
    "Technology":              "XLK",
    "Health Care":             "XLV",
    "Healthcare":              "XLV",   # FMP alternate spelling
    "Financials":              "XLF",
    "Financial Services":      "XLF",   # FMP alternate
    "Consumer Discretionary":  "XLY",
    "Consumer Cyclical":       "XLY",   # FMP alternate
    "Consumer Staples":        "XLP",
    "Consumer Defensive":      "XLP",   # FMP alternate
    "Energy":                  "XLE",
    "Industrials":             "XLI",
    "Materials":               "XLB",
    "Basic Materials":         "XLB",   # FMP alternate
    "Real Estate":             "XLRE",
    "Utilities":               "XLU",
    "Communication Services":  "XLC",
}


# ── Analyst Consensus ─────────────────────────────────────────────────────────


def get_analyst_consensus(symbol: str) -> dict | None:
    """
    Fetch analyst consensus rating for a symbol.

    Returns dict with:
        symbol (str)
        consensus (str): "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL"
        target_high (float|None)
        target_low (float|None)
        target_consensus (float|None)
        target_median (float|None)
        last_updated (str ISO date)

    Returns None if endpoint unavailable (premium) or no data.
    """
    raw = _get("grades-consensus", {"symbol": symbol.upper()}, ttl=_TTL_ANALYST)
    if not raw:
        return None

    # stable endpoint returns a dict or list
    item = raw if isinstance(raw, dict) else (raw[0] if isinstance(raw, list) and raw else None)
    if not item:
        return None

    consensus_raw = (item.get("consensus") or item.get("recommendation") or "").upper()
    # Normalise FMP's various strings to our canonical 5-value set
    consensus_map = {
        "STRONG BUY":  "STRONG_BUY",
        "STRONGBUY":   "STRONG_BUY",
        "STRONG_BUY":  "STRONG_BUY",
        "BUY":         "BUY",
        "HOLD":        "HOLD",
        "NEUTRAL":     "HOLD",
        "SELL":        "SELL",
        "UNDERPERFORM": "SELL",
        "STRONG SELL": "STRONG_SELL",
        "STRONGSELL":  "STRONG_SELL",
        "STRONG_SELL": "STRONG_SELL",
    }
    consensus = consensus_map.get(consensus_raw, "HOLD")

    return {
        "symbol":           symbol.upper(),
        "consensus":        consensus,
        "target_high":      _safe_float(item.get("priceTargetHigh") or item.get("targetHigh")),
        "target_low":       _safe_float(item.get("priceTargetLow") or item.get("targetLow")),
        "target_consensus": _safe_float(item.get("priceTargetAverage") or item.get("targetConsensus")),
        "target_median":    _safe_float(item.get("priceTargetMedian") or item.get("targetMedian")),
        "last_updated":     (item.get("lastUpdated") or item.get("date") or "")[:10],
    }


# ── Price Target ─────────────────────────────────────────────────────────────


def get_price_target(symbol: str, limit: int = 5) -> dict | None:
    """
    Fetch the most recent analyst price targets for a symbol.

    Returns dict with:
        symbol (str)
        latest_pt (float|None)         — most recent individual price target
        pt_consensus (float|None)      — consensus/average across all analysts
        pt_upside_pct (float|None)     — None here; caller computes against live price
        analyst_count (int)
        last_firm (str)
        last_date (str ISO)

    Returns None if unavailable.
    """
    raw = _get("price-target", {"symbol": symbol.upper(), "limit": limit}, ttl=_TTL_ANALYST)
    if not raw or not isinstance(raw, list) or not raw:
        # Fallback: try price-target-summary
        consensus_raw = _get("price-target-summary", {"symbol": symbol.upper()}, ttl=_TTL_ANALYST)
        if consensus_raw:
            item = consensus_raw if isinstance(consensus_raw, dict) else (consensus_raw[0] if consensus_raw else None)
            if item:
                return {
                    "symbol":         symbol.upper(),
                    "latest_pt":      _safe_float(item.get("priceTargetAverage") or item.get("targetConsensus")),
                    "pt_consensus":   _safe_float(item.get("priceTargetAverage") or item.get("targetConsensus")),
                    "pt_upside_pct":  None,
                    "analyst_count":  int(item.get("numberOfAnalysts") or item.get("analysts") or 0),
                    "last_firm":      "",
                    "last_date":      (item.get("lastUpdated") or item.get("date") or "")[:10],
                }
        return None

    pts = [_safe_float(r.get("priceTarget") or r.get("adjPriceTarget")) for r in raw]
    pts = [p for p in pts if p is not None and p > 0]
    if not pts:
        return None

    latest = raw[0]
    return {
        "symbol":        symbol.upper(),
        "latest_pt":     pts[0],
        "pt_consensus":  round(sum(pts) / len(pts), 2),
        "pt_upside_pct": None,   # caller computes: (pt_consensus - price) / price * 100
        "analyst_count": len(pts),
        "last_firm":     latest.get("analystCompany") or latest.get("analyst") or "",
        "last_date":     (latest.get("publishedDate") or latest.get("date") or "")[:10],
    }


# ── Short Interest ────────────────────────────────────────────────────────────


def get_short_interest(symbol: str) -> dict | None:
    """
    Fetch short float percentage for a symbol.

    Tries FMP short-volume endpoint (short volume ratio proxy).
    Note: FMP stable does not expose a direct short-float % endpoint;
    short_float_pct returned here is short_volume / total_volume * 100
    (a daily proxy, NOT the FINRA bi-monthly short float %).

    Returns dict with:
        symbol (str)
        short_float_pct (float|None)    — proxy: short vol / total vol * 100
        short_shares (int|None)
        settlement_date (str)
        source (str)                    — "short_volume_ratio"

    Returns None if unavailable.
    """
    # FMP stable: short-selling/daily-volume (symbol-level short volume)
    raw_sv = _get("short-selling/daily-volume", {"symbol": symbol.upper()}, ttl=_TTL_FUNDAMENTALS)
    if not raw_sv or not isinstance(raw_sv, list) or not raw_sv:
        return None

    item = raw_sv[0]
    short_vol = _safe_float(item.get("shortVolume"))
    total_vol  = _safe_float(item.get("totalVolume"))
    short_pct  = None
    if short_vol is not None and total_vol and total_vol > 0:
        short_pct = round(short_vol / total_vol * 100, 2)

    return {
        "symbol":          symbol.upper(),
        "short_float_pct": short_pct,
        "short_shares":    int(short_vol) if short_vol else None,
        "settlement_date": (item.get("date") or "")[:10],
        "source":          "short_volume_ratio",
    }


# ── Revenue Growth ────────────────────────────────────────────────────────────


def get_revenue_growth(symbol: str) -> dict | None:
    """
    Compute revenue growth (YoY and QoQ) using FMP's pre-calculated
    income-statement-growth endpoint as primary source.
    Falls back to manual computation from raw income statements.

    Returns dict with:
        symbol (str)
        revenue_growth_yoy (float|None)    — YoY % growth
        revenue_growth_qoq (float|None)    — QoQ % growth
        revenue_deceleration (bool)        — True if QoQ growth < prior QoQ growth
        revenue_growth_positive (bool)     — True if YoY growth > 0
        revenue_latest_qtr (str)           — period of most recent quarter
        revenue_trend (list[float])        — last 4 quarters revenue in $M, newest first

    Returns None if insufficient data.
    """
    # ── Primary: pre-calculated growth rates ─────────────────────────────────
    growth_raw = _get(
        "income-statement-growth",
        {"symbol": symbol.upper(), "period": "quarter", "limit": 3},
    )
    if growth_raw and isinstance(growth_raw, list) and len(growth_raw) >= 2:
        cur  = growth_raw[0]
        prev = growth_raw[1]
        yoy  = _safe_float(cur.get("growthRevenue"))
        if yoy is not None:
            yoy = round(yoy * 100, 2)  # FMP returns decimal e.g. 0.31 → 31%
        qoq_raw = _safe_float(cur.get("growthRevenueQoQ") or cur.get("growthRevenueq"))
        if qoq_raw is None:
            qoq_raw = None
        else:
            qoq_raw = round(qoq_raw * 100, 2)

        # Deceleration: current QoQ growth vs prior period's QoQ growth
        prev_qoq_raw = _safe_float(prev.get("growthRevenueQoQ") or prev.get("growthRevenueq"))
        prev_qoq = round(prev_qoq_raw * 100, 2) if prev_qoq_raw is not None else None
        deceleration = (
            qoq_raw is not None and prev_qoq is not None and qoq_raw < prev_qoq
        )

        if yoy is not None:
            return {
                "symbol":                   symbol.upper(),
                "revenue_growth_yoy":       yoy,
                "revenue_growth_qoq":       qoq_raw,
                "revenue_deceleration":     deceleration,
                "revenue_growth_positive":  (yoy > 0),
                "revenue_latest_qtr":       (cur.get("date") or "")[:10],
                "revenue_trend":            [],  # not available from growth endpoint
            }

    # ── Fallback: manual computation from raw income statements ──────────────
    raw = _get(
        "income-statement",
        {"symbol": symbol.upper(), "period": "quarter", "limit": 5},
    )
    if not raw or not isinstance(raw, list) or len(raw) < 4:
        return None

    revenues = []
    for item in raw[:5]:
        r = _safe_float(item.get("revenue"))
        if r is not None:
            revenues.append((item.get("period") or item.get("date") or "", r))

    if len(revenues) < 4:
        return None

    latest_rev   = revenues[0][1]
    prior_q_rev  = revenues[1][1]
    year_ago_rev = revenues[4][1] if len(revenues) >= 5 else None

    yoy = None
    if year_ago_rev and year_ago_rev != 0:
        yoy = round((latest_rev - year_ago_rev) / abs(year_ago_rev) * 100, 2)

    qoq = None
    if prior_q_rev and prior_q_rev != 0:
        qoq = round((latest_rev - prior_q_rev) / abs(prior_q_rev) * 100, 2)

    prior_qoq = None
    if len(revenues) >= 3 and revenues[2][1] and revenues[2][1] != 0:
        prior_qoq = (revenues[1][1] - revenues[2][1]) / abs(revenues[2][1]) * 100

    deceleration = (
        qoq is not None and prior_qoq is not None and qoq < prior_qoq
    )

    return {
        "symbol":                   symbol.upper(),
        "revenue_growth_yoy":       yoy,
        "revenue_growth_qoq":       qoq,
        "revenue_deceleration":     deceleration,
        "revenue_growth_positive":  (yoy is not None and yoy > 0),
        "revenue_latest_qtr":       revenues[0][0][:10] if revenues else "",
        "revenue_trend":            [round(r / 1e6, 1) for _, r in revenues[:4]],
    }


# ── EPS Acceleration ──────────────────────────────────────────────────────────


def get_eps_acceleration(symbol: str) -> dict | None:
    """
    Determine if EPS growth is accelerating or decelerating over last 4 quarters.

    Returns dict with:
        symbol (str)
        eps_growth_yoy (float|None)         — latest Q EPS vs same Q prior year
        eps_accelerating (bool)             — True if YoY growth rate is improving QoQ
        eps_beat_rate (float|None)          — % of last 4 quarters where actual beat estimate
        eps_trend (list[float])             — last 4 quarters actual EPS, newest first

    Returns None if insufficient data.
    """
    raw = _get("historical-earning-calendar", {"symbol": symbol.upper(), "limit": 8})
    if not raw or not isinstance(raw, list) or len(raw) < 4:
        return None

    records = []
    for item in raw[:8]:
        actual = _safe_float(item.get("eps") or item.get("actual"))
        estimate = _safe_float(item.get("epsEstimated") or item.get("estimated"))
        if actual is not None:
            records.append({
                "date":     (item.get("date") or "")[:10],
                "actual":   actual,
                "estimate": estimate,
                "beat":     (actual > estimate) if estimate is not None else None,
            })

    if len(records) < 4:
        return None

    # YoY EPS growth: records[0] vs records[4] (same quarter last year)
    yoy = None
    if len(records) >= 5 and records[4]["actual"] and records[4]["actual"] != 0:
        yoy = round(
            (records[0]["actual"] - records[4]["actual"]) / abs(records[4]["actual"]) * 100, 2
        )

    # Acceleration: compare YoY rates across two consecutive quarters
    accelerating = False
    if len(records) >= 6 and records[5]["actual"] and records[5]["actual"] != 0 and records[1]["actual"]:
        prior_yoy = (records[1]["actual"] - records[5]["actual"]) / abs(records[5]["actual"]) * 100
        if yoy is not None:
            accelerating = yoy > prior_yoy

    # Beat rate: last 4 quarters
    beats = [r["beat"] for r in records[:4] if r["beat"] is not None]
    beat_rate = round(sum(beats) / len(beats) * 100, 1) if beats else None

    return {
        "symbol":          symbol.upper(),
        "eps_growth_yoy":  yoy,
        "eps_accelerating": accelerating,
        "eps_beat_rate":   beat_rate,
        "eps_trend":       [r["actual"] for r in records[:4]],
    }


# ── Company Profile / Sector ──────────────────────────────────────────────────


def get_company_sector(symbol: str) -> str | None:
    """
    Return the GICS sector ETF ticker for a symbol (e.g. 'XLK' for AAPL).

    Fetches company profile from FMP and maps sector name to SPDR ETF.
    Returns None if unavailable or unmapped.
    """
    raw = _get("profile", {"symbol": symbol.upper()})
    if not raw:
        return None

    item = raw if isinstance(raw, dict) else (raw[0] if isinstance(raw, list) and raw else None)
    if not item:
        return None

    sector = (item.get("sector") or "").strip()
    return GICS_ETF_MAP.get(sector)


def get_company_profile(symbol: str) -> dict | None:
    """
    Return key company metadata: sector, market cap, description.

    Returns dict with:
        symbol (str)
        sector_etf (str|None)     — mapped GICS ETF e.g. "XLK"
        sector (str)              — raw FMP sector name
        market_cap (float|None)   — in USD
        employees (int|None)
        description (str)
        exchange (str)

    Returns None on failure.
    """
    raw = _get("profile", {"symbol": symbol.upper()})
    if not raw:
        return None

    item = raw if isinstance(raw, dict) else (raw[0] if isinstance(raw, list) and raw else None)
    if not item:
        return None

    sector = (item.get("sector") or "").strip()
    return {
        "symbol":     symbol.upper(),
        "sector_etf": GICS_ETF_MAP.get(sector),
        "sector":     sector,
        "market_cap": _safe_float(item.get("mktCap")),
        "employees":  int(item["fullTimeEmployees"]) if item.get("fullTimeEmployees") else None,
        "description": (item.get("description") or "")[:300],
        "exchange":   item.get("exchangeShortName") or item.get("exchange") or "",
    }


# ── Analyst Grade Distribution ────────────────────────────────────────────────


def get_analyst_grades(symbol: str) -> dict | None:
    """
    Fetch analyst grade distribution (strong buy / buy / hold / sell / strong sell counts).
    Premium endpoint — refreshed every 30 min.

    Returns dict with:
        symbol (str)
        strong_buy (int), buy (int), hold (int), sell (int), strong_sell (int)
        total_analysts (int)
        consensus_score (float|None)  — 1.0 = all strong sell … 5.0 = all strong buy
        last_updated (str)

    Returns None if unavailable.
    """
    raw = _get("grades-consensus", {"symbol": symbol.upper()}, ttl=_TTL_ANALYST)
    if not raw:
        return None

    item = raw if isinstance(raw, dict) else (raw[0] if isinstance(raw, list) and raw else None)
    if not item:
        return None

    strong_buy  = int(item.get("strongBuy")  or item.get("strongBuys")  or 0)
    buy         = int(item.get("buy")        or item.get("buys")         or 0)
    hold        = int(item.get("hold")       or item.get("holds")        or 0)
    sell        = int(item.get("sell")       or item.get("sells")        or 0)
    strong_sell = int(item.get("strongSell") or item.get("strongSells") or 0)
    total       = strong_buy + buy + hold + sell + strong_sell

    consensus_score = None
    if total > 0:
        weighted = strong_buy * 5 + buy * 4 + hold * 3 + sell * 2 + strong_sell * 1
        consensus_score = round(weighted / total, 2)

    return {
        "symbol":          symbol.upper(),
        "strong_buy":      strong_buy,
        "buy":             buy,
        "hold":            hold,
        "sell":            sell,
        "strong_sell":     strong_sell,
        "total_analysts":  total,
        "consensus_score": consensus_score,
        "last_updated":    (item.get("lastUpdated") or item.get("date") or "")[:10],
    }


# ── Insider Trading Sentiment ─────────────────────────────────────────────────


def get_insider_sentiment(symbol: str, days: int = 90) -> dict | None:
    """
    Compute net insider buy/sell sentiment from Form 4 filings in the last N days.

    Returns dict with:
        symbol (str)
        net_sentiment (str)       — "BUYING" | "SELLING" | "NEUTRAL"
        buy_transactions (int)
        sell_transactions (int)
        net_value_usd (float)     — net $ of insider buys minus sells
        last_filing_date (str)

    Returns None if no filings found or endpoint unavailable.
    """
    raw = _get("insider-trading/search", {"symbol": symbol.upper()}, ttl=_TTL_INSIDER)
    if not raw or not isinstance(raw, list):
        return None

    cutoff = datetime.now(UTC) - timedelta(days=days)
    buy_count = sell_count = 0
    buy_value = sell_value = 0.0
    last_date = ""

    for item in raw:
        date_str = (item.get("transactionDate") or item.get("filingDate") or "")[:10]
        if not date_str:
            continue
        try:
            tx_date = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
        except Exception:
            continue
        if tx_date < cutoff:
            continue

        tx_type = (item.get("transactionType") or "").upper()
        shares  = _safe_float(item.get("securitiesTransacted") or item.get("shares"))
        price   = _safe_float(item.get("price") or item.get("securityPrice"))
        value   = (shares or 0.0) * (price or 0.0)

        if any(k in tx_type for k in ("BUY", "PURCHASE", "P-", "P -")):
            buy_count  += 1
            buy_value  += value
        elif any(k in tx_type for k in ("SELL", "SALE", "S-", "S -")):
            sell_count += 1
            sell_value += value

        if not last_date or date_str > last_date:
            last_date = date_str

    if buy_count == 0 and sell_count == 0:
        return None

    net_value = buy_value - sell_value
    if buy_count >= sell_count * 2 or net_value > 500_000:
        sentiment = "BUYING"
    elif sell_count >= buy_count * 2 or net_value < -1_000_000:
        sentiment = "SELLING"
    else:
        sentiment = "NEUTRAL"

    return {
        "symbol":            symbol.upper(),
        "net_sentiment":     sentiment,
        "buy_transactions":  buy_count,
        "sell_transactions": sell_count,
        "net_value_usd":     round(net_value),
        "last_filing_date":  last_date,
    }


# ── Congressional Trading ─────────────────────────────────────────────────────


def get_congressional_trades(symbol: str, days: int = 90) -> dict | None:
    """
    Fetch recent Senate and House trading activity for a symbol.
    Congressional trading has historically outperformed the market by ~12% annually.

    Returns dict with:
        symbol (str)
        net_sentiment (str)     — "BUYING" | "SELLING" | "NEUTRAL" | "NONE"
        buy_count (int)
        sell_count (int)
        politicians (list[str]) — names of politicians who traded (max 5)
        last_trade_date (str)

    Returns None if no recent congressional trades found.
    """
    senate = _get("senate-trades",    {"symbol": symbol.upper()}, ttl=_TTL_CONGRESS)
    house  = _get("house-trades",     {"symbol": symbol.upper()}, ttl=_TTL_CONGRESS)

    cutoff = datetime.now(UTC) - timedelta(days=days)
    buy_count = sell_count = 0
    politicians: set[str] = set()
    last_date = ""

    all_trades: list[dict] = []
    if senate and isinstance(senate, list):
        all_trades.extend(senate)
    if house and isinstance(house, list):
        all_trades.extend(house)

    if not all_trades:
        return None

    for item in all_trades:
        date_str = (item.get("transactionDate") or item.get("disclosureDate") or "")[:10]
        if not date_str:
            continue
        try:
            tx_date = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
        except Exception:
            continue
        if tx_date < cutoff:
            continue

        tx_type = (item.get("type") or item.get("transactionType") or "").upper()
        first = item.get("firstName") or ""
        last  = item.get("lastName") or item.get("representative") or item.get("senator") or ""
        name  = f"{first} {last}".strip()

        if any(k in tx_type for k in ("PURCHASE", "BUY")):
            buy_count += 1
            politicians.add(name)
        elif any(k in tx_type for k in ("SALE", "SELL")):
            sell_count += 1
            politicians.add(name)

        if not last_date or date_str > last_date:
            last_date = date_str

    if buy_count == 0 and sell_count == 0:
        return {"symbol": symbol.upper(), "net_sentiment": "NONE",
                "buy_count": 0, "sell_count": 0, "politicians": [], "last_trade_date": ""}

    if buy_count > sell_count:
        sentiment = "BUYING"
    elif sell_count > buy_count:
        sentiment = "SELLING"
    else:
        sentiment = "NEUTRAL"

    return {
        "symbol":          symbol.upper(),
        "net_sentiment":   sentiment,
        "buy_count":       buy_count,
        "sell_count":      sell_count,
        "politicians":     sorted(politicians)[:5],
        "last_trade_date": last_date,
    }


# ── Key Financial Metrics (TTM) ───────────────────────────────────────────────


def get_key_metrics_ttm(symbol: str) -> dict | None:
    """
    Fetch trailing 12-month (TTM) key financial metrics: margins, FCF yield, P/E, ROE.

    Returns dict with:
        symbol (str)
        gross_margin (float|None)      — gross margin % (e.g. 45.0 for 45%)
        operating_margin (float|None)  — operating margin %
        net_margin (float|None)        — net profit margin %
        fcf_yield (float|None)         — free cash flow yield %
        pe_ratio (float|None)          — trailing P/E
        roe (float|None)               — return on equity %
        debt_to_equity (float|None)    — D/E ratio

    Returns None if unavailable.
    """
    raw = _get("key-metrics-ttm", {"symbol": symbol.upper()}, ttl=_TTL_FUNDAMENTALS)
    if not raw:
        return None

    item = raw if isinstance(raw, dict) else (raw[0] if isinstance(raw, list) and raw else None)
    if not item:
        return None

    return {
        "symbol":           symbol.upper(),
        "gross_margin":     _safe_pct(item.get("grossProfitMarginTTM") or item.get("grossProfitMargin")),
        "operating_margin": _safe_pct(item.get("operatingProfitMarginTTM") or item.get("operatingProfitMargin")),
        "net_margin":       _safe_pct(item.get("netProfitMarginTTM") or item.get("netProfitMargin")),
        "fcf_yield":        _safe_pct(item.get("fcfYieldTTM") or item.get("freeCashFlowYieldTTM")),
        "pe_ratio":         _safe_float(item.get("peRatioTTM") or item.get("peRatio")),
        "roe":              _safe_pct(item.get("roeTTM") or item.get("returnOnEquityTTM")),
        "debt_to_equity":   _safe_float(item.get("debtToEquityTTM") or item.get("debtToEquity")),
    }


# ── Analyst Estimates ─────────────────────────────────────────────────────────


def get_analyst_estimates(symbol: str) -> dict | None:
    """
    Fetch consensus analyst EPS and revenue estimates for the next quarter.
    Premium endpoint — refreshed every 30 min.

    Returns dict with:
        symbol (str)
        next_eps_estimate (float|None)     — consensus EPS for next quarter
        next_revenue_estimate (float|None) — consensus revenue estimate
        eps_est_high (float|None)
        eps_est_low (float|None)
        num_analysts (int|None)
        period_date (str)

    Returns None if unavailable.
    """
    raw = _get(
        "analyst-estimates",
        {"symbol": symbol.upper(), "period": "quarter", "limit": 1},
        ttl=_TTL_ANALYST,
    )
    if not raw or not isinstance(raw, list) or not raw:
        return None

    item = raw[0]
    return {
        "symbol":                symbol.upper(),
        "next_eps_estimate":     _safe_float(item.get("estimatedEpsAvg") or item.get("epsEstimated")),
        "next_revenue_estimate": _safe_float(item.get("estimatedRevenueAvg") or item.get("revenueEstimated")),
        "eps_est_high":          _safe_float(item.get("estimatedEpsHigh")),
        "eps_est_low":           _safe_float(item.get("estimatedEpsLow")),
        "num_analysts":          int(item.get("numberAnalystEstimatedEps") or 0) or None,
        "period_date":           (item.get("date") or "")[:10],
    }


# ── DCF Valuation ─────────────────────────────────────────────────────────────


def get_dcf_value(symbol: str) -> dict | None:
    """
    Fetch FMP's DCF-implied intrinsic value per share.
    Useful for POSITION entries — provides margin-of-safety check.

    Returns dict with:
        symbol (str)
        dcf_value (float|None)   — intrinsic value per share from DCF model
        stock_price (float|None) — price used in model (may be slightly lagged)
        upside_pct (float|None)  — (dcf_value - stock_price) / stock_price * 100

    Returns None if unavailable (model requires full fundamental data).
    """
    raw = _get(
        "discounted-cash-flow",
        {"symbol": symbol.upper()},
        ttl=_TTL_FUNDAMENTALS,
    )
    if not raw:
        return None

    item = raw if isinstance(raw, dict) else (raw[0] if isinstance(raw, list) and raw else None)
    if not item:
        return None

    dcf   = _safe_float(item.get("dcf") or item.get("dcfValue") or item.get("intrinsicValue"))
    price = _safe_float(item.get("stockPrice") or item.get("price"))
    upside = None
    if dcf and price and price > 0:
        upside = round((dcf - price) / price * 100, 2)

    return {
        "symbol":      symbol.upper(),
        "dcf_value":   dcf,
        "stock_price": price,
        "upside_pct":  upside,
    }


# ── Recent Stock News ─────────────────────────────────────────────────────────


def get_stock_news(symbol: str, limit: int = 5) -> list[dict]:
    """
    Fetch the most recent news articles for a symbol.
    Refreshed every 15 min — used by catalyst engine and Opus context.

    Returns list of dicts with:
        title (str), published_date (str), url (str), sentiment (str), source (str)

    Returns empty list on failure.
    """
    raw = _get("news/stock", {"symbols": symbol.upper(), "limit": limit}, ttl=_TTL_NEWS)
    if not raw or not isinstance(raw, list):
        return []

    results = []
    for item in raw[:limit]:
        results.append({
            "title":          (item.get("title") or "")[:200],
            "published_date": (item.get("publishedDate") or "")[:10],
            "url":            item.get("url") or "",
            "sentiment":      item.get("sentiment") or "",
            "source":         item.get("site") or "",
        })
    return results


_fmp_articles_cache: tuple = (None, 0.0)  # (articles, mono_time)
_FMP_ARTICLES_TTL = 15 * 60  # 15 min — matches _TTL_NEWS


def get_fmp_news_articles(symbols: list[str], limit: int = 50) -> list[dict]:
    """
    Batch news fetch from FMP for the dashboard news feed.

    Returns list of dicts matching the dashboard article schema:
      headline, summary, url, source, author, symbols, image_url,
      sentiment, age_hours, created_ts, news_score, catalyst

    FMP returns article-specific images on every article — no logo fallback needed.
    Cached for 15 min. Returns [] on failure.
    """
    import time as _t

    global _fmp_articles_cache
    cached, cached_at = _fmp_articles_cache
    if cached is not None and _t.monotonic() - cached_at < _FMP_ARTICLES_TTL:
        return cached

    if not symbols:
        return []

    sym_str = ",".join(s.upper() for s in symbols[:50])
    raw = _get("news/stock", {"symbols": sym_str, "limit": limit}, ttl=0)  # ttl=0 — use module cache above
    if not raw or not isinstance(raw, list):
        return []

    now_utc = datetime.now(UTC)
    articles = []
    for item in raw[:limit]:
        headline = (item.get("title") or "").strip()
        if not headline:
            continue

        # Parse publishedDate: "YYYY-MM-DD HH:MM:SS" or ISO
        age_hours, created_ts = 0.0, 0
        ts_str = item.get("publishedDate") or ""
        try:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    dt = datetime.strptime(ts_str[:19], fmt).replace(tzinfo=UTC)
                    age_hours = (now_utc - dt).total_seconds() / 3600
                    created_ts = int(dt.timestamp() * 1000)
                    break
                except ValueError:
                    continue
        except Exception:
            pass

        raw_sent = (item.get("sentiment") or "").upper()
        sentiment = raw_sent if raw_sent in ("BULLISH", "BEARISH", "NEUTRAL") else "NEUTRAL"
        news_score = {"BULLISH": 6, "BEARISH": 6, "NEUTRAL": 0}.get(sentiment, 0)

        syms = []
        raw_sym = item.get("symbol") or ""
        if isinstance(raw_sym, str) and raw_sym.strip():
            syms = [raw_sym.strip().upper()]
        elif isinstance(raw_sym, list):
            syms = [s.upper() for s in raw_sym if s]

        articles.append({
            "headline":   headline,
            "summary":    (item.get("text") or "").strip()[:300],
            "url":        (item.get("url") or "").strip(),
            "source":     (item.get("site") or "").strip(),
            "author":     (item.get("author") or "").strip(),
            "symbols":    syms,
            "image_url":  (item.get("image") or "").strip() or None,
            "sentiment":  sentiment,
            "age_hours":  round(age_hours, 2),
            "created_ts": created_ts,
            "news_score": news_score,
            "catalyst":   "",
        })

    import logging as _log
    _log.getLogger("decifer.fmp").info(
        "FMP news articles: %d fetched (%d with images)",
        len(articles),
        sum(1 for a in articles if a["image_url"]),
    )
    _fmp_articles_cache = (articles, _t.monotonic())
    return articles


# ── Shares Float ──────────────────────────────────────────────────────────────


def get_shares_float(symbol: str) -> dict | None:
    """
    Fetch float shares and outstanding shares.
    Useful for short-squeeze analysis and position-size context.

    Returns dict with:
        symbol (str)
        free_float_pct (float|None)   — % of shares freely tradeable
        float_shares (int|None)       — number of float shares
        outstanding_shares (int|None)

    Returns None if unavailable.
    """
    raw = _get("shares-float", {"symbol": symbol.upper()}, ttl=_TTL_FUNDAMENTALS)
    if not raw:
        return None

    item = raw if isinstance(raw, dict) else (raw[0] if isinstance(raw, list) and raw else None)
    if not item:
        return None

    float_sh  = _safe_float(item.get("floatShares"))
    outstanding = _safe_float(item.get("outstandingShares") or item.get("sharesOutstanding"))
    free_float_pct = None
    if float_sh and outstanding and outstanding > 0:
        free_float_pct = round(float_sh / outstanding * 100, 2)

    return {
        "symbol":             symbol.upper(),
        "free_float_pct":     free_float_pct,
        "float_shares":       int(float_sh)      if float_sh      else None,
        "outstanding_shares": int(outstanding)   if outstanding   else None,
    }


# ── Institutional Ownership ───────────────────────────────────────────────────


def get_institutional_ownership(symbol: str) -> dict | None:
    """
    Fetch institutional ownership percentage and QoQ change.
    Uses FMP institutional-ownership endpoint (form 13F filings).

    Returns dict with:
        symbol (str)
        ownership_pct (float|None)    — % of shares held by institutions (0–100)
        ownership_change (float|None) — QoQ change in percentage points
        num_holders (int|None)        — number of institutional holders
        last_date (str)               — filing date of most recent data

    Returns None if unavailable.
    """
    raw = _get(
        "institutional-ownership/symbol-positions-summary",
        {"symbol": symbol.upper()},
        ttl=_TTL_FUNDAMENTALS,
    )
    if not raw or not isinstance(raw, list) or not raw:
        return None

    # FMP returns list sorted newest first, each entry is one quarter's aggregate
    current = raw[0] if len(raw) > 0 else None
    prior   = raw[1] if len(raw) > 1 else None

    if not current:
        return None

    own_pct = _safe_pct(
        current.get("ownershipPercent") or current.get("institutionalOwnershipPercentage")
    )

    # QoQ change
    change = None
    if prior and own_pct is not None:
        prior_pct = _safe_pct(
            prior.get("ownershipPercent") or prior.get("institutionalOwnershipPercentage")
        )
        if prior_pct is not None:
            change = round(own_pct - prior_pct, 2)

    num_holders = int(current.get("numberOfHolders") or current.get("holders") or 0) or None

    return {
        "symbol":           symbol.upper(),
        "ownership_pct":    own_pct,
        "ownership_change": change,
        "num_holders":      num_holders,
        "last_date":        (current.get("date") or current.get("reportingDate") or "")[:10],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def warm_fundamentals_cache(symbols: list[str]) -> None:
    """
    Pre-populate the in-memory FMP cache for a universe of symbols.

    Call once at session open so that score_universe() and trade_context.py
    find cached revenue growth and EPS data without blocking per-symbol calls.
    Uses _TTL_FUNDAMENTALS (24h) — each symbol is fetched at most once per day.
    Failures per symbol are silently skipped; partial warming is fine.
    """
    if not is_available():
        return
    for sym in symbols:
        try:
            get_revenue_growth(sym)
        except Exception:
            pass
        try:
            get_eps_acceleration(sym)
        except Exception:
            pass
        try:
            get_key_metrics_ttm(sym)
        except Exception:
            pass


def _safe_pct(val) -> float | None:
    """
    Convert FMP decimal fraction (0.35) to percentage (35.0).
    FMP returns margin/yield fields as decimals when |value| ≤ 10.
    Returns None if val is invalid.
    """
    f = _safe_float(val)
    if f is None:
        return None
    # Decimal fraction (e.g. 0.459 → 45.9%)
    if -10.0 <= f <= 10.0:
        return round(f * 100, 2)
    # Already a percentage (e.g. 45.9 — rare but some FMP fields)
    return round(f, 2)
