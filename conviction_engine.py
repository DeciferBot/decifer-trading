# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  conviction_engine.py                      ║
# ║   Intelligence Layer — multi-dimensional conviction scoring  ║
# ║   Layer: INTELLIGENCE — no execution imports                 ║
# ╚══════════════════════════════════════════════════════════════╝
"""
conviction_engine.py — Structural conviction scoring for universe symbols.

Conviction answers: how much do we believe this name will perform over
the next 1–10 days given everything we know about it structurally?

It is NOT about intraday entry timing (5m bars, MFI, VWAP). That is the
signal engine's job. Conviction uses daily and multi-day data only.

Phase 1+2 dimensions:
  D1 — Analyst consensus + price target        max 38 pts
  D2 — Price momentum 1D/5D vs SPY             max 20 pts
  D3 — Valuation (DCF, P/E, revenue growth)    max 23 pts
  D4 — Distance from 52W/ATH highs (corrected) max 12 pts
  D5 — Macro theme + driver state              max 25 pts
  D6 — News and catalyst (customer_event_tape) max 12 pts
  D7 — Options flow (api_cache flow files)     max 12 pts  (ASYMMETRIC put penalty)
  D8 — Peer network alignment (TTG bucket)     max  8 pts
  D9 — Counter-thesis (structural conflicts)   max  3 pts  (-10 penalty)

Composite score: raw_sum / enabled_max * 100, clamped 0–100.

Tiers:
  HIGH       >= 65  — strong multi-dimensional case
  MEDIUM     45–64  — building conviction
  WATCHLIST  25–44  — thesis exists, thin support
  DORMANT    <  25  — no active case
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests as _requests

log = logging.getLogger("decifer.conviction")

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = _BASE_DIR / "data" / "intelligence"
_FMP_BASE = "https://financialmodelingprep.com/stable"
_FMP_KEY  = os.environ.get("FMP_API_KEY", "")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DimensionScore:
    raw_pts: int
    max_pts: int
    signal: str          # short human-readable summary of what fired

    def to_dict(self) -> dict:
        return {"raw_pts": self.raw_pts, "max_pts": self.max_pts, "signal": self.signal}


@dataclass
class ConvictionScore:
    symbol: str
    composite: int       # 0–100
    tier: str            # HIGH / MEDIUM / WATCHLIST / DORMANT
    dimensions: dict     # dim_id -> DimensionScore.to_dict()
    ts: str              # ISO timestamp of when scored

    def to_dict(self) -> dict:
        return {
            "symbol":     self.symbol,
            "composite":  self.composite,
            "tier":       self.tier,
            "dimensions": self.dimensions,
            "ts":         self.ts,
        }


# ---------------------------------------------------------------------------
# FMP helpers
# ---------------------------------------------------------------------------

def _fmp(endpoint: str, params: dict, timeout: int = 8) -> list | dict | None:
    """Single FMP stable GET. Returns parsed JSON or None on failure."""
    key = _FMP_KEY or os.environ.get("FMP_API_KEY", "")
    if not key:
        return None
    try:
        resp = _requests.get(
            f"{_FMP_BASE}/{endpoint}",
            params={**params, "apikey": key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.debug("conviction_engine: FMP %s failed — %s", endpoint, exc)
        return None


def _first(raw) -> dict:
    """Return first element if list, or dict as-is, or {}."""
    if isinstance(raw, list):
        return raw[0] if raw else {}
    return raw if isinstance(raw, dict) else {}


def _f(val) -> float | None:
    """Safe float conversion."""
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Static data readers (intelligence files — no FMP)
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _driver_state() -> tuple[set[str], list[str]]:
    """Returns (active_driver_ids, blocked_condition_ids)."""
    ds = _read_json(_DATA_DIR / "live_driver_state.json")
    return set(ds.get("active_drivers", [])), ds.get("blocked_conditions", [])


def _theme_activation() -> dict[str, str]:
    """Returns {theme_id: state} — active/dormant/crowded/headwind."""
    raw = _read_json(_DATA_DIR / "theme_activation.json")
    result = {}
    for item in raw.get("themes", raw.get("activated_themes", [])):
        tid = item.get("theme_id") or item.get("id")
        state = item.get("state") or item.get("status") or "dormant"
        if tid:
            result[tid] = state
    return result


def _exposures_for(symbol: str) -> list[dict]:
    """Return active TTG exposures for symbol, sorted by confidence desc."""
    raw = _read_json(_DATA_DIR / "theme_graph" / "symbol_exposures.json")
    return sorted(
        [e for e in raw.get("exposures", [])
         if e.get("symbol", "").upper() == symbol.upper()
         and e.get("status") == "active"],
        key=lambda e: -(e.get("confidence") or 0),
    )


def _counter_thesis_for(driver_id: str) -> list[dict]:
    """Return structural conflicts for a driver from counter_thesis_cache."""
    raw = _read_json(_DATA_DIR / "counter_thesis_cache.json")
    return [c for c in raw.get("structural_conflicts", [])
            if c.get("driver_id") == driver_id]


def _thesis_divergence_for(symbol: str) -> dict | None:
    """Return thesis divergence record for symbol if present."""
    raw = _read_json(_DATA_DIR / "thesis_divergence.json")
    for item in raw.get("detail", []):
        if item.get("symbol", "").upper() == symbol.upper():
            return item
    return None


# ---------------------------------------------------------------------------
# D1 — Analyst consensus + price target  (max 38 pts)
# ---------------------------------------------------------------------------
# Scoring:
#   Consensus:   STRONG_BUY=20, BUY=12, HOLD=5, SELL=0, STRONG_SELL=-5
#   Upside to PT: ≥20%=+10, 10-20%=+6, 0-10%=+3, <0%=-5, ≥-20%=-10
#   Recent change (30d): upgrade=+8, downgrade=-10
#   Grade trend (improving vs deteriorating): +4 / -4
# Max achievable: 20+10+8+4 = 42  (clamped to 38 declared MAX)

def _score_analyst(symbol: str, recent_changes: list[dict]) -> DimensionScore:
    MAX = 38
    pts = 0
    signals = []

    consensus_raw = _fmp("grades-consensus", {"symbol": symbol})
    item = _first(consensus_raw)
    consensus_str = (item.get("consensus") or "").strip().upper().replace(" ", "_")
    consensus_map = {
        "STRONG_BUY": 20, "STRONGBUY": 20,
        "BUY": 12,
        "HOLD": 5, "NEUTRAL": 5,
        "SELL": 0,
        "STRONG_SELL": -5, "UNDERPERFORM": -3,
    }
    c_pts = consensus_map.get(consensus_str, 5)
    pts += c_pts
    signals.append(f"consensus={consensus_str or 'UNKNOWN'}")

    # Price target upside
    pt_raw = _fmp("price-target-consensus", {"symbol": symbol})
    pt_item = _first(pt_raw)
    pt = _f(pt_item.get("targetConsensus") or pt_item.get("targetMedian"))
    price_raw = _fmp("quote-short", {"symbol": symbol})
    price_item = _first(price_raw)
    current_price = _f(price_item.get("price"))

    if pt and current_price and current_price > 0:
        upside = (pt - current_price) / current_price * 100
        if upside >= 20:
            pts += 10; signals.append(f"upside={upside:.0f}%→+10")
        elif upside >= 10:
            pts += 6;  signals.append(f"upside={upside:.0f}%→+6")
        elif upside >= 0:
            pts += 3;  signals.append(f"upside={upside:.0f}%→+3")
        elif upside >= -20:
            pts -= 5;  signals.append(f"upside={upside:.0f}%→-5")
        else:
            pts -= 10; signals.append(f"upside={upside:.0f}%→-10")

    # Recent analyst changes (pre-fetched batch)
    sym_changes = [c for c in recent_changes if c.get("symbol") == symbol.upper()]
    upgrades   = sum(1 for c in sym_changes if c.get("action") == "upgrade")
    downgrades = sum(1 for c in sym_changes if c.get("action") == "downgrade")
    if upgrades:
        pts += 8; signals.append(f"+{upgrades}upgrade(30d)")
    if downgrades:
        pts -= 10 * downgrades; signals.append(f"-{downgrades}downgrade(30d)")

    # Grade trend: compare strong_buy+buy share now vs 90 days ago via grades history
    try:
        grades_raw = _fmp("analyst-grades", {"symbol": symbol, "limit": 6})
        if isinstance(grades_raw, list) and len(grades_raw) >= 2:
            def _bull_share(g: dict) -> float:
                sb = int(g.get("strongBuy") or g.get("strongBuys") or 0)
                b  = int(g.get("buy")       or g.get("buys")       or 0)
                h  = int(g.get("hold")      or g.get("holds")      or 0)
                s  = int(g.get("sell")      or g.get("sells")      or 0)
                ss = int(g.get("strongSell") or g.get("strongSells") or 0)
                total = sb + b + h + s + ss
                return (sb + b) / total if total > 0 else 0.5
            recent_share = _bull_share(grades_raw[0])
            older_share  = sum(_bull_share(g) for g in grades_raw[1:]) / (len(grades_raw) - 1)
            delta = recent_share - older_share
            if delta >= 0.08:
                pts += 4; signals.append(f"grade_trend=improving({delta:+.0%})→+4")
            elif delta <= -0.08:
                pts -= 4; signals.append(f"grade_trend=deteriorating({delta:+.0%})→-4")
    except Exception:
        pass

    pts = max(-38, min(MAX, pts))
    return DimensionScore(raw_pts=pts, max_pts=MAX, signal="; ".join(signals) or "no analyst data")


# ---------------------------------------------------------------------------
# D2 — Price momentum 1D/5D vs SPY  (max 20 pts)
# ---------------------------------------------------------------------------
# Corrected per trader Q&A:
#   5D relative to SPY: ≥5%=+15, ≥2%=+10, ≥0.5%=+5, neutral=0, <-1%=-8, <-3%=-15
#   1D relative bonus: ≥2%=+5, ≤-2%=-5
# Max: 15+5 = 20

def _score_momentum(symbol: str, price_changes: dict[str, float]) -> DimensionScore:
    MAX = 20
    pts = 0
    signals = []

    spy_5d = price_changes.get("SPY")
    sym_5d = price_changes.get(symbol.upper())
    spy_1d = price_changes.get("SPY_1D")
    sym_1d = price_changes.get(f"{symbol.upper()}_1D")

    if sym_5d is not None and spy_5d is not None:
        rel5 = sym_5d - spy_5d
        if rel5 >= 5.0:
            pts += 15; signals.append(f"5d_rel={rel5:+.1f}%→+15")
        elif rel5 >= 2.0:
            pts += 10; signals.append(f"5d_rel={rel5:+.1f}%→+10")
        elif rel5 >= 0.5:
            pts += 5;  signals.append(f"5d_rel={rel5:+.1f}%→+5")
        elif rel5 > -1.0:
            signals.append(f"5d_rel={rel5:+.1f}%→0")
        elif rel5 > -3.0:
            pts -= 8;  signals.append(f"5d_rel={rel5:+.1f}%→-8")
        else:
            pts -= 15; signals.append(f"5d_rel={rel5:+.1f}%→-15")

    if sym_1d is not None and spy_1d is not None:
        rel1 = sym_1d - spy_1d
        if rel1 >= 2.0:
            pts += 5;  signals.append(f"1d_rel={rel1:+.1f}%→+5")
        elif rel1 <= -2.0:
            pts -= 5;  signals.append(f"1d_rel={rel1:+.1f}%→-5")

    pts = max(-20, min(MAX, pts))
    return DimensionScore(raw_pts=pts, max_pts=MAX,
                          signal="; ".join(signals) or "no price data")


# ---------------------------------------------------------------------------
# D3 — Valuation: DCF, P/E vs sector, revenue growth  (max 23 pts)
# ---------------------------------------------------------------------------
# DCF upside:    ≥20%=+15, ≥10%=+10, ≥0%=+5, -20% to 0=-10, <-20%=-18
# Revenue growth:≥30%=+8, ≥15%=+4, ≥0%=0, negative=-8
# Sector-relative P/E bonus/penalty (if DCF unavailable or PE data available):
#   PE < 0.75x sector median: +3 (cheaper than peers)
#   PE > 2.0x sector median:  -3 (expensive vs peers)
# Max: 15+8 = 23

# Sector median forward P/E by TTG theme bucket — updated annually, good enough
# for a directional signal. Deliberately conservative (mid-cycle estimates).
_SECTOR_PE: dict[str, float] = {
    "ai_infrastructure":      35.0,
    "ai_compute":             32.0,
    "semiconductors":         28.0,
    "cloud_software":         30.0,
    "cybersecurity":          35.0,
    "defence":                20.0,
    "energy":                 14.0,
    "gold_precious_metals":   18.0,
    "healthcare":             20.0,
    "biotech":                25.0,
    "consumer_discretionary": 22.0,
    "financials":             12.0,
    "industrials":            20.0,
    "reits":                  35.0,   # price/FFO proxy
    "crypto_infrastructure":  40.0,
    "default":                22.0,
}


def _sector_median_pe(theme_id: str) -> float:
    """Return sector median forward P/E for a TTG theme_id."""
    raw = _read_json(_DATA_DIR / "theme_graph" / "theme_nodes.json")
    nodes = {n.get("id", ""): n for n in raw.get("nodes", [])}
    bucket = (nodes.get(theme_id, {}).get("sector_bucket") or "").lower().replace(" ", "_")
    return _SECTOR_PE.get(bucket) or _SECTOR_PE.get(theme_id) or _SECTOR_PE["default"]


def _score_valuation(symbol: str) -> DimensionScore:
    MAX = 23
    pts = 0
    signals = []

    dcf_raw = _fmp("discounted-cash-flow", {"symbol": symbol})
    dcf_item = _first(dcf_raw)
    dcf   = _f(dcf_item.get("dcf") or dcf_item.get("dcfValue"))
    price = _f(dcf_item.get("stockPrice") or dcf_item.get("Stock Price") or dcf_item.get("price"))

    if dcf and price and price > 0:
        upside = (dcf - price) / price * 100
        if upside >= 20:
            pts += 15; signals.append(f"DCF_upside={upside:.0f}%→+15")
        elif upside >= 10:
            pts += 10; signals.append(f"DCF_upside={upside:.0f}%→+10")
        elif upside >= 0:
            pts += 5;  signals.append(f"DCF_upside={upside:.0f}%→+5")
        elif upside >= -20:
            pts -= 10; signals.append(f"DCF_upside={upside:.0f}%→-10")
        else:
            pts -= 18; signals.append(f"DCF_upside={upside:.0f}%→-18")

    growth_raw = _fmp("financial-growth", {"symbol": symbol, "period": "annual", "limit": 1})
    g_item = _first(growth_raw)
    rev_growth_raw = g_item.get("revenueGrowth") or g_item.get("revenue_growth")
    rev_growth = _f(rev_growth_raw)
    if rev_growth is not None:
        rev_pct = rev_growth * 100 if abs(rev_growth) <= 5 else rev_growth
        if rev_pct >= 30:
            pts += 8; signals.append(f"rev_growth={rev_pct:.0f}%→+8")
        elif rev_pct >= 15:
            pts += 4; signals.append(f"rev_growth={rev_pct:.0f}%→+4")
        elif rev_pct >= 0:
            signals.append(f"rev_growth={rev_pct:.0f}%→0")
        else:
            pts -= 8; signals.append(f"rev_growth={rev_pct:.0f}%→-8")

    # Sector-relative P/E: cheap vs peers = modest positive, expensive = modest negative
    try:
        km_raw = _fmp("key-metrics-ttm", {"symbol": symbol})
        km_item = _first(km_raw)
        pe_ttm = _f(km_item.get("peRatioTTM") or km_item.get("priceEarningsRatioTTM"))
        if pe_ttm and pe_ttm > 0:
            exposures = _exposures_for(symbol)
            theme_id  = exposures[0].get("theme_id", "") if exposures else ""
            median_pe = _sector_median_pe(theme_id)
            ratio = pe_ttm / median_pe
            if ratio < 0.75:
                pts += 3; signals.append(f"PE={pe_ttm:.0f}x<0.75×sector({median_pe:.0f}x)→+3")
            elif ratio > 2.0:
                pts -= 3; signals.append(f"PE={pe_ttm:.0f}x>2×sector({median_pe:.0f}x)→-3")
            else:
                signals.append(f"PE={pe_ttm:.0f}x(sector={median_pe:.0f}x)→0")
    except Exception:
        pass

    pts = max(-23, min(MAX, pts))
    return DimensionScore(raw_pts=pts, max_pts=MAX,
                          signal="; ".join(signals) or "no valuation data")


# ---------------------------------------------------------------------------
# D4 — Distance from highs  (max 12 pts)
# ---------------------------------------------------------------------------
# CORRECTED: near 52W/ATH high = BULLISH (institutional sponsorship intact)
# Far below = downtrend = BEARISH
#
#   Within 5% of 52W high (consolidating near top): +8
#   New 52W/ATH high (breakout, zero overhead supply): +12
#   5–15% below 52W high: +3
#   15–30% below 52W high: -5
#   30%+ below 52W high: -12

def _score_distance_from_highs(symbol: str) -> DimensionScore:
    MAX = 12
    pts = 0
    signals = []

    # Use stable/quote which includes yearHigh (52W high) — single fast call.
    # historical-price-full is not available on this FMP tier.
    quote_raw = _fmp("quote", {"symbol": symbol})
    item      = _first(quote_raw)
    current   = _f(item.get("price"))
    high_52w  = _f(item.get("yearHigh"))

    if current is None or high_52w is None or high_52w == 0:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="no price data")

    pct_from_52w = (current - high_52w) / high_52w * 100  # negative = below

    if pct_from_52w >= -2:
        pts = 12; signals.append(f"at_52W_high({current:.0f}/{high_52w:.0f})→+12")
    elif pct_from_52w >= -15:
        pts = 3; signals.append(f"{pct_from_52w:.0f}%_from_52W→+3")
    elif pct_from_52w >= -30:
        pts = -5; signals.append(f"{pct_from_52w:.0f}%_from_52W→-5")
    else:
        pts = -12; signals.append(f"{pct_from_52w:.0f}%_from_52W→-12")

    return DimensionScore(raw_pts=pts, max_pts=MAX, signal="; ".join(signals))


# ---------------------------------------------------------------------------
# D5 — Macro theme + driver state  (max 25 pts)
# ---------------------------------------------------------------------------
# driver_active + exposure_type:
#   direct_beneficiary=+20, supply_chain=+15, etf_basket=+10, weak=+5
# driver inactive: 0
# evidence_basis bonus: company_profile/filing=+5
# theme state penalty: crowded=-5, headwind=-15
# Max: 20 + 5 = 25

def _score_macro_theme(symbol: str) -> DimensionScore:
    MAX = 25
    pts = 0
    signals = []

    active_drivers, _ = _driver_state()
    theme_states      = _theme_activation()
    exposures         = _exposures_for(symbol)

    if not exposures:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="not in TTG")

    primary = exposures[0]
    driver_id     = primary.get("driver_id", "")
    exposure_type = primary.get("exposure_type", "")
    evidence      = primary.get("evidence_basis", "")
    theme_id      = primary.get("theme_id", "")

    driver_active = driver_id in active_drivers
    if driver_active:
        exp_pts = {"direct_beneficiary": 20, "supply_chain": 15,
                   "etf_basket": 10}.get(exposure_type, 5)
        pts += exp_pts
        signals.append(f"driver={driver_id}(active)_{exposure_type}→+{exp_pts}")
    else:
        signals.append(f"driver={driver_id}(inactive)")

    if evidence in ("company_profile", "filing", "curated_reference", "official_source"):
        pts += 5; signals.append("strong_evidence→+5")

    theme_state = theme_states.get(theme_id, "")
    if theme_state == "headwind":
        pts -= 15; signals.append("theme=headwind→-15")
    elif theme_state == "crowded":
        pts -= 5;  signals.append("theme=crowded→-5")

    pts = max(-25, min(MAX, pts))
    return DimensionScore(raw_pts=pts, max_pts=MAX, signal="; ".join(signals))


# ---------------------------------------------------------------------------
# Batch price fetch (shared across all symbols in a universe rescore)
# ---------------------------------------------------------------------------

def fetch_price_changes(symbols: list[str]) -> dict[str, float]:
    """
    Fetch 1D and 5D price-change % for all symbols + SPY in one FMP call.
    Returns flat dict:  {SYMBOL: 5d_pct, SYMBOL_1D: 1d_pct, SPY: spy_5d, SPY_1D: spy_1d}
    """
    all_syms = list({s.upper() for s in symbols} | {"SPY"})
    key = _FMP_KEY or os.environ.get("FMP_API_KEY", "")
    if not key:
        return {}
    try:
        resp = _requests.get(
            f"{_FMP_BASE}/stock-price-change",
            params={"symbol": ",".join(all_syms), "apikey": key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("conviction_engine: price-change fetch failed — %s", exc)
        return {}

    result: dict[str, float] = {}
    for item in (data if isinstance(data, list) else []):
        sym = (item.get("symbol") or "").upper()
        d5  = _f(item.get("5D"))
        d1  = _f(item.get("1D"))
        if sym and d5 is not None:
            result[sym] = d5
        if sym and d1 is not None:
            result[f"{sym}_1D"] = d1
    return result


def fetch_analyst_changes(symbols: list[str]) -> list[dict]:
    """
    Fetch recent analyst upgrades/downgrades for universe symbols (last 30 days).
    Single FMP RSS feed call, filtered to our symbol set.
    """
    sym_set = {s.upper() for s in symbols}
    raw = _fmp("upgrades-downgrades-rss-feed", {"page": 0})
    if not isinstance(raw, list):
        return []

    cutoff = datetime.now(UTC) - timedelta(days=30)
    results = []
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if sym not in sym_set:
            continue
        pub_str = item.get("publishedDate") or ""
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=UTC)
            if pub_dt < cutoff:
                continue
        except Exception:
            continue
        action = (item.get("action") or "").lower()
        results.append({
            "symbol": sym,
            "action": action,
            "firm": item.get("gradingCompany") or "",
            "published_date": pub_str,
        })
    return results


# ---------------------------------------------------------------------------
# D6 — News and catalyst  (max 12 pts)
# ---------------------------------------------------------------------------
# Reads customer_event_tape.json. Matches symbol via tickers_first_order /
# tickers_second_order, or by theme_id appearing in themes_strengthened /
# themes_weakened.
#
# Materiality mapping: "high" → 0.9, "medium" → 0.5, "low" → 0.2
# Direction: positive = theme_id in themes_strengthened, or event_family
#   "earnings" and symbol in tickers_first_order; negative = theme_id in
#   themes_weakened, or event_family "geopolitics" and symbol in
#   tickers_first_order with no strengthened match.
# Age windows keyed on source_published_at.
#   positive event (materiality >= 0.7) within 24h: +12
#   positive event within 72h: +7
#   positive event within 7d: +3
#   no material news: 0
#   negative event within 24h: -12
#   negative event within 72h: -7

_MATERIALITY_MAP = {"high": 0.9, "medium": 0.5, "low": 0.2}


def _event_age_hours(event: dict) -> float | None:
    """Return age of event in hours from now, or None if unparseable."""
    pub_str = event.get("source_published_at") or event.get("ingested_at") or ""
    try:
        pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - pub_dt
        return delta.total_seconds() / 3600
    except Exception:
        return None


def _event_direction_for_symbol(event: dict, symbol: str, theme_id: str) -> str:
    """
    Returns "positive", "negative", or "neutral" for the given symbol/theme.

    Logic priority:
    1. theme_id in themes_strengthened → positive
    2. theme_id in themes_weakened → negative
    3. symbol in tickers_first_order AND event_family is "earnings" → positive
    4. symbol in tickers_first_order AND event_family is "geopolitics" → negative
    5. fallback: neutral
    """
    themes_strengthened = event.get("themes_strengthened") or []
    themes_weakened     = event.get("themes_weakened") or []

    if theme_id and theme_id in themes_strengthened:
        return "positive"
    if theme_id and theme_id in themes_weakened:
        return "negative"

    sym_upper  = symbol.upper()
    first_ord  = [t.upper() for t in (event.get("tickers_first_order") or [])]

    if sym_upper in first_ord:
        family = (event.get("event_family") or "").lower()
        if family == "earnings":
            return "positive"
        if family == "geopolitics":
            return "negative"

    return "neutral"


def _catalyst_score_for(symbol: str) -> float | None:
    """Return today's catalyst_score for symbol from catalyst candidates file, or None."""
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    try:
        from config import CATALYST_DIR
        path = CATALYST_DIR / f"candidates_{today}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        candidates = payload.get("candidates", [])
        for c in candidates:
            if (c.get("ticker") or "").upper() == symbol.upper():
                return _f(c.get("catalyst_score"))
    except Exception:
        pass
    return None


def _score_news_catalyst(symbol: str, theme_id: str) -> DimensionScore:
    MAX = 12

    tape_path = _DATA_DIR / "customer_event_tape.json"
    raw = _read_json(tape_path)
    events = raw.get("events", [])

    sym_upper = symbol.upper()

    best_positive_pts = 0
    worst_negative_pts = 0
    tape_hit = False

    for event in events:
        materiality_str = (event.get("materiality") or "low").lower()
        materiality = _MATERIALITY_MAP.get(materiality_str, 0.2)
        if materiality < 0.7:
            continue

        first_ord  = [t.upper() for t in (event.get("tickers_first_order")  or [])]
        second_ord = [t.upper() for t in (event.get("tickers_second_order") or [])]
        themes_str = (event.get("themes_strengthened") or []) + (event.get("themes_weakened") or [])

        symbol_touched = (
            sym_upper in first_ord
            or sym_upper in second_ord
            or (theme_id and theme_id in themes_str)
        )
        if not symbol_touched:
            continue

        age_h = _event_age_hours(event)
        if age_h is None or age_h > 7 * 24:
            continue

        tape_hit = True
        direction = _event_direction_for_symbol(event, symbol, theme_id)

        if direction == "positive":
            pts = 12 if age_h <= 24 else (7 if age_h <= 72 else 3)
            best_positive_pts = max(best_positive_pts, pts)
        elif direction == "negative":
            pts = -12 if age_h <= 24 else (-7 if age_h <= 72 else 0)
            worst_negative_pts = min(worst_negative_pts, pts)

    # FMP news fallback: if symbol not in tape, check FMP stock news (last 3 days)
    if not tape_hit:
        try:
            news_raw = _fmp("stock_news", {"tickers": sym_upper, "limit": 5})
            if isinstance(news_raw, list) and news_raw:
                for article in news_raw:
                    age_h = _event_age_hours({
                        "source_published_at": article.get("publishedDate", ""),
                    })
                    if age_h is None or age_h > 72:
                        continue
                    # Sentiment field from FMP: "Positive", "Negative", "Neutral"
                    sentiment = (article.get("sentiment") or "").lower()
                    if sentiment == "positive":
                        pts = 6 if age_h <= 24 else 3
                        best_positive_pts = max(best_positive_pts, pts)
                    elif sentiment == "negative":
                        pts = -6 if age_h <= 24 else -3
                        worst_negative_pts = min(worst_negative_pts, pts)
        except Exception:
            pass

    # Catalyst engine score (from today's candidates file) — treat as high signal
    catalyst_score = _catalyst_score_for(sym_upper)
    if catalyst_score is not None and catalyst_score >= 7.0:
        # catalyst_score 7–8.5 = moderate catalyst, 8.5+ = high conviction event
        if catalyst_score >= 8.5:
            best_positive_pts = max(best_positive_pts, 10)
        else:
            best_positive_pts = max(best_positive_pts, 6)

    combined = max(-MAX, min(MAX, best_positive_pts + worst_negative_pts))
    source = "tape" if tape_hit else ("fmp_news" if (best_positive_pts or worst_negative_pts) else "no_news")
    if catalyst_score and catalyst_score >= 7.0:
        source += f"+catalyst({catalyst_score:.1f})"

    if combined == 0:
        signal = f"no_material_news({source})"
    elif combined > 0:
        signal = f"positive({source})→+{combined}"
    else:
        signal = f"negative({source})→{combined}"

    return DimensionScore(raw_pts=combined, max_pts=MAX, signal=signal)


# ---------------------------------------------------------------------------
# D7 — Options flow  (max 12 pts, ASYMMETRIC — put penalty 1.5x)
# ---------------------------------------------------------------------------
# Reads data/api_cache/{SYMBOL}_flow.json if it exists and is < 30 min old.
# Fields: unusual_calls (bool), unusual_puts (bool),
#         call_expansion (float), put_expansion (float)
#
#   unusual_calls = True: +10
#   call_expansion 1.75–2.5x (no unusual flag): +5
#   call_expansion 2.5x+: +8
#   unusual_puts = True: -15  (1.5x asymmetry — informed sellers > buyers)
#   put_expansion dominant (> call_expansion, no unusual flags): -8
#   no unusual flow: 0

_FLOW_MAX_AGE_SECONDS = 30 * 60  # 30 minutes
_API_CACHE_DIR = _BASE_DIR / "data" / "api_cache"


def _score_options_flow(symbol: str) -> DimensionScore:
    MAX = 12

    flow_path = _API_CACHE_DIR / f"{symbol.upper()}_flow.json"
    if not flow_path.exists():
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="no flow data")

    # Age check
    try:
        mtime = flow_path.stat().st_mtime
        age_s = time.time() - mtime
        if age_s > _FLOW_MAX_AGE_SECONDS:
            return DimensionScore(raw_pts=0, max_pts=MAX,
                                  signal=f"flow_data_stale({age_s/60:.0f}m)")
    except Exception:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="flow_stat_error")

    try:
        flow = json.loads(flow_path.read_text())
    except Exception:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="flow_parse_error")

    unusual_calls  = bool(flow.get("unusual_calls", False))
    unusual_puts   = bool(flow.get("unusual_puts", False))
    call_expansion = _f(flow.get("call_expansion")) or 0.0
    put_expansion  = _f(flow.get("put_expansion"))  or 0.0

    pts = 0
    signals = []

    if unusual_puts:
        pts -= 15
        signals.append("unusual_puts→-15")
    elif put_expansion > call_expansion and put_expansion > 0:
        pts -= 8
        signals.append(f"put_dominant({put_expansion:.1f}x)→-8")

    if unusual_calls:
        pts += 10
        signals.append("unusual_calls→+10")
    elif call_expansion >= 2.5:
        pts += 8
        signals.append(f"call_expansion={call_expansion:.1f}x→+8")
    elif call_expansion >= 1.75:
        pts += 5
        signals.append(f"call_expansion={call_expansion:.1f}x→+5")

    if not signals:
        signals.append("no unusual flow")

    pts = max(-15, min(MAX, pts))  # cap at MAX but allow -15 for put asymmetry
    return DimensionScore(raw_pts=pts, max_pts=MAX, signal="; ".join(signals))


# ---------------------------------------------------------------------------
# D8 — Peer network alignment  (max 8 pts)
# ---------------------------------------------------------------------------
# Finds all OTHER active TTG exposures sharing the same bucket_id as the
# symbol's primary exposure. Uses price_changes (5D returns) for peers.
#
#   >= 75% peers positive 5D: +8
#   50–74% positive: +4
#   25–49% positive: 0
#   < 25% positive: -5
#   < 2 peers found: 0 (insufficient data)

def _score_peer_network(symbol: str, price_changes: dict[str, float]) -> DimensionScore:
    MAX = 8

    exposures = _exposures_for(symbol)
    if not exposures:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="not_in_TTG")

    primary = exposures[0]
    bucket_id = primary.get("bucket_id", "")
    if not bucket_id:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="no_bucket_id")

    # Load all exposures to find peers in same bucket
    raw = _read_json(_DATA_DIR / "theme_graph" / "symbol_exposures.json")
    all_exposures = raw.get("exposures", [])

    sym_upper = symbol.upper()
    peers = [
        e.get("symbol", "").upper()
        for e in all_exposures
        if e.get("bucket_id") == bucket_id
        and e.get("status") == "active"
        and e.get("symbol", "").upper() != sym_upper
    ]

    if len(peers) < 2:
        return DimensionScore(raw_pts=0, max_pts=MAX,
                              signal=f"insufficient_peers({len(peers)})")

    peers_with_data = [(p, price_changes.get(p)) for p in peers
                       if price_changes.get(p) is not None]

    if len(peers_with_data) < 2:
        return DimensionScore(raw_pts=0, max_pts=MAX,
                              signal=f"no_price_data_for_peers")

    positive_count = sum(1 for _, ret in peers_with_data if ret > 0)
    total = len(peers_with_data)
    pct_positive = positive_count / total * 100

    if pct_positive >= 75:
        pts = 8;  signal = f"peers={total},pos%={pct_positive:.0f}→+8"
    elif pct_positive >= 50:
        pts = 4;  signal = f"peers={total},pos%={pct_positive:.0f}→+4"
    elif pct_positive >= 25:
        pts = 0;  signal = f"peers={total},pos%={pct_positive:.0f}→0"
    else:
        pts = -5; signal = f"peers={total},pos%={pct_positive:.0f}→-5"

    return DimensionScore(raw_pts=pts, max_pts=MAX, signal=signal)


# ---------------------------------------------------------------------------
# D9 — Counter-thesis  (max 3 pts positive, -10 penalty)
# ---------------------------------------------------------------------------
# Reads counter_thesis_cache.json structural_conflicts for driver_id.
# Reads thesis_divergence.json for thesis_intact status of symbol.
#
# Penalty weights by verification_status (cumulative across conflicts):
#   verified:   -8 pts each
#   partial:    -4 pts each
#   unverified: -2 pts each  (only if confidence >= 0.4)
#   refuted:    skip (positive data point — handled via thesis_intact)
#
# Divergence takes priority:
#   thesis_intact=False:  -8 (price action already contradicting thesis)
#   thesis_intact=True + no conflicts: +3

# Penalty per conflict by verification_status
_CONFLICT_PENALTY: dict[str, int] = {
    "verified":   8,
    "partial":    4,
    "unverified": 2,
}


def _score_counter_thesis(symbol: str, driver_id: str) -> DimensionScore:
    MAX = 3

    conflicts = _counter_thesis_for(driver_id) if driver_id else []
    divergence = _thesis_divergence_for(symbol)

    # thesis_intact can be True, False, or None (data_unavailable)
    thesis_intact: bool | None = None
    if divergence is not None:
        raw_intact = divergence.get("thesis_intact")
        if raw_intact is True:
            thesis_intact = True
        elif raw_intact is False:
            thesis_intact = False

    # thesis_intact=False takes priority regardless of structural conflicts
    if thesis_intact is False:
        return DimensionScore(raw_pts=-8, max_pts=MAX,
                              signal="thesis_intact=False→-8")

    # Compute penalty weighted by verification_status
    penalty = 0
    active_conflicts = []
    for c in conflicts:
        status = c.get("verification_status", "unverified")
        conf   = float(c.get("confidence") or 0)
        if status == "refuted":
            continue
        if status == "unverified" and conf < 0.4:
            continue
        pts = _CONFLICT_PENALTY.get(status, 0)
        penalty += pts
        active_conflicts.append(f"{status}({c.get('id','?')})")

    if not active_conflicts:
        if thesis_intact is True:
            return DimensionScore(raw_pts=3, max_pts=MAX,
                                  signal="no_conflicts+thesis_intact→+3")
        return DimensionScore(raw_pts=0, max_pts=MAX,
                              signal="no_conflicts,no_divergence_data→0")

    raw_pts = -min(penalty, 15)   # cap at -15 so one dimension can't dominate
    signal  = f"conflicts={','.join(active_conflicts)}→{raw_pts}"
    return DimensionScore(raw_pts=raw_pts, max_pts=MAX, signal=signal)


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------

# All enabled dimensions and their max_pts (Phase 1 + Phase 2)
_ALL_DIMS = {
    "analyst":       38,
    "momentum":      20,
    "valuation":     23,
    "highs":         12,
    "macro":         25,
    "news_catalyst": 12,
    "options_flow":  12,
    "peer_network":   8,
    "counter_thesis": 3,
}
_ALL_DIMS_MAX = sum(_ALL_DIMS.values())   # 153

# Keep legacy name as alias for any callers that referenced it
_PHASE1_DIMS = {k: _ALL_DIMS[k] for k in ("analyst", "momentum", "valuation", "highs", "macro")}
_PHASE1_MAX = sum(_PHASE1_DIMS.values())  # 118


def _tier(score: int) -> str:
    if score >= 65: return "HIGH"
    if score >= 45: return "MEDIUM"
    if score >= 25: return "WATCHLIST"
    return "DORMANT"


def score_symbol(
    symbol: str,
    price_changes: dict[str, float] | None = None,
    analyst_changes: list[dict] | None = None,
) -> ConvictionScore:
    """
    Score a single symbol across all Phase 1+2 conviction dimensions.

    price_changes and analyst_changes are batch-fetched at universe level
    and passed in to avoid redundant API calls.
    """
    if price_changes is None:
        price_changes = fetch_price_changes([symbol])
    if analyst_changes is None:
        analyst_changes = fetch_analyst_changes([symbol])

    # Resolve primary TTG exposure once for D5/D6/D9
    exposures = _exposures_for(symbol)
    primary_exposure = exposures[0] if exposures else {}
    theme_id  = primary_exposure.get("theme_id", "")
    driver_id = primary_exposure.get("driver_id", "")

    # Phase 1
    d1 = _score_analyst(symbol, analyst_changes)
    d2 = _score_momentum(symbol, price_changes)
    d3 = _score_valuation(symbol)
    d4 = _score_distance_from_highs(symbol)
    d5 = _score_macro_theme(symbol)

    # Phase 2
    d6 = _score_news_catalyst(symbol, theme_id)
    d7 = _score_options_flow(symbol)
    d8 = _score_peer_network(symbol, price_changes)
    d9 = _score_counter_thesis(symbol, driver_id)

    raw_sum = (d1.raw_pts + d2.raw_pts + d3.raw_pts + d4.raw_pts + d5.raw_pts
               + d6.raw_pts + d7.raw_pts + d8.raw_pts + d9.raw_pts)

    # Normalise against all-dims max. Clamp numerator at 0 so negatives don't
    # produce a negative composite — floor is 0.
    composite = round(max(0, raw_sum) / _ALL_DIMS_MAX * 100)
    composite = min(composite, 100)

    dims = {
        "analyst":       d1.to_dict(),
        "momentum":      d2.to_dict(),
        "valuation":     d3.to_dict(),
        "highs":         d4.to_dict(),
        "macro":         d5.to_dict(),
        "news_catalyst": d6.to_dict(),
        "options_flow":  d7.to_dict(),
        "peer_network":  d8.to_dict(),
        "counter_thesis":d9.to_dict(),
    }

    return ConvictionScore(
        symbol=symbol.upper(),
        composite=composite,
        tier=_tier(composite),
        dimensions=dims,
        ts=datetime.now(UTC).isoformat(),
    )
