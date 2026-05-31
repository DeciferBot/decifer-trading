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

Phase 1 dimensions (weights sum to 1.00 across enabled dims):
  D1 — Analyst consensus + price target        weight 0.20
  D2 — Price momentum 1D/5D vs SPY             weight 0.18
  D3 — Valuation (DCF, P/E, revenue growth)   weight 0.15
  D4 — Distance from 52W/ATH highs (corrected) weight 0.07
  D5 — Macro theme + driver state              weight 0.13

Phase 2 adds: D6 news/catalyst, D7 options flow, D8 peer network, D9 counter-thesis.

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
# Max achievable: 20+10+8 = 38

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
# D3 — Valuation: DCF, P/E, revenue growth  (max 23 pts)
# ---------------------------------------------------------------------------
# DCF upside: ≥20%=+15, ≥10%=+10, ≥0%=+5, -20% to 0=-10, <-20%=-18
# Revenue growth: ≥30%=+8, ≥15%=+4, ≥0%=0, negative=-8
# Max: 15+8 = 23

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

    hist_raw = _fmp("historical-price-full", {"symbol": symbol, "serietype": "line"})
    h_item = _first(hist_raw) if isinstance(hist_raw, dict) else {}
    historical = h_item.get("historical", hist_raw if isinstance(hist_raw, list) else [])

    if not historical:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="no price history")

    closes = [_f(h.get("close") or h.get("adjClose")) for h in historical
              if _f(h.get("close") or h.get("adjClose")) is not None]
    if not closes:
        return DimensionScore(raw_pts=0, max_pts=MAX, signal="no price history")

    current = closes[0]
    year_closes = closes[:252]   # ~1 year of trading days
    high_52w = max(year_closes) if year_closes else current
    ath = max(closes)

    pct_from_52w = (current - high_52w) / high_52w * 100  # negative = below

    if pct_from_52w >= -2:                        # at or making new 52W high
        if current >= ath * 0.99:                 # also at/near ATH
            pts = 12; signals.append("new_ATH→+12")
        else:
            pts = 8; signals.append("near_52W_high→+8")
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
# Composite scorer
# ---------------------------------------------------------------------------

# Phase 1 enabled dimensions and their max_pts
_PHASE1_DIMS = {
    "analyst":   38,
    "momentum":  20,
    "valuation": 23,
    "highs":     12,
    "macro":     25,
}
_PHASE1_MAX = sum(_PHASE1_DIMS.values())   # 118


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
    Score a single symbol across all Phase 1 conviction dimensions.

    price_changes and analyst_changes are batch-fetched at universe level
    and passed in to avoid redundant API calls.
    """
    if price_changes is None:
        price_changes = fetch_price_changes([symbol])
    if analyst_changes is None:
        analyst_changes = fetch_analyst_changes([symbol])

    d1 = _score_analyst(symbol, analyst_changes)
    d2 = _score_momentum(symbol, price_changes)
    d3 = _score_valuation(symbol)
    d4 = _score_distance_from_highs(symbol)
    d5 = _score_macro_theme(symbol)

    raw_sum = d1.raw_pts + d2.raw_pts + d3.raw_pts + d4.raw_pts + d5.raw_pts
    # Normalise against Phase 1 max. Clamp numerator at 0 so negatives don't
    # produce a negative composite — floor is 0.
    composite = round(max(0, raw_sum) / _PHASE1_MAX * 100)
    composite = min(composite, 100)

    dims = {
        "analyst":   d1.to_dict(),
        "momentum":  d2.to_dict(),
        "valuation": d3.to_dict(),
        "highs":     d4.to_dict(),
        "macro":     d5.to_dict(),
    }

    return ConvictionScore(
        symbol=symbol.upper(),
        composite=composite,
        tier=_tier(composite),
        dimensions=dims,
        ts=datetime.now(UTC).isoformat(),
    )
