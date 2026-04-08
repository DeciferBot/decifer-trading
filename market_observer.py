# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  market_observer.py                        ║
# ║   Cross-asset observation layer.                            ║
# ║   Fetches raw market data across equities, bonds,           ║
# ║   commodities, and FX. No regime labels. No rules.          ║
# ║   Pure observations that the intelligence layer reasons     ║
# ║   about freely.                                             ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Single responsibility: fetch and package cross-asset market observations.

Outputs a MarketObservation dataclass containing:
  - Per-asset snapshot: price, daily change, 5d change, MA context
  - Rolling 5-day return correlations between assets
  - Sector ETF performance relative to SPY
  - VIX level and trend

The intelligence layer (market_intelligence.py) reads this and reasons
freely — no regime labels are assigned here.

Cache: observations are cached for intelligence_cache_minutes (config).
Refreshed automatically when cache expires or on explicit invalidation.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

from alpaca_data import fetch_bars
from config import CONFIG

log = logging.getLogger("decifer.market_observer")

# ── Universe ──────────────────────────────────────────────────────────────────
# Instruments to observe. Labels are passed to the LLM as context.
# No hardcoded relationships — the LLM reasons about these freely.

UNIVERSE: dict[str, str] = {
    # Equity indices
    "SPY":  "US large cap equity",
    "QQQ":  "US tech / growth equity",
    "IWM":  "US small cap equity",
    # Sector ETFs (relative performance reveals rotation)
    "XLK":  "technology sector",
    "XLF":  "financials sector",
    "XLE":  "energy sector",
    "XLV":  "healthcare sector",
    "XLI":  "industrials sector",
    "XLU":  "utilities sector",
    "XLP":  "consumer staples sector",
    # Risk / macro instruments
    "GLD":  "gold",
    "USO":  "crude oil",
    "TLT":  "long-duration US treasuries",
    "HYG":  "high yield credit",
    "LQD":  "investment grade credit",
    # FX (via ETFs — accessible on Alpaca)
    "UUP":  "US dollar index",
    "FXY":  "Japanese yen",
    "FXF":  "Swiss franc",
    "FXA":  "Australian dollar",
}

SECTOR_ETFS = {"XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP"}

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class AssetSnapshot:
    symbol:     str
    label:      str
    price:      float
    change_1d:  float   # today's % change
    change_5d:  float   # 5-session % change
    above_ma20: bool
    above_ma50: bool


@dataclass
class MarketObservation:
    timestamp:      str
    assets:         dict[str, AssetSnapshot]        # symbol → snapshot
    correlations:   dict[str, dict[str, float]]     # 5d rolling return correlations
    sector_vs_spy:  dict[str, float]                # sector ETF 5d return minus SPY 5d return
    vix:            float
    vix_change_1d:  float
    vix_5d_avg:     float
    fetch_errors:   list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Compact text block ready for inclusion in an LLM prompt."""
        lines = [
            f"Market observation at {self.timestamp} UTC",
            f"VIX: {self.vix:.1f}  (1d change: {self.vix_change_1d:+.1f}, 5d avg: {self.vix_5d_avg:.1f})",
            "",
            "Asset snapshots (1d% / 5d% / above MA20 / above MA50):",
        ]
        for sym, s in self.assets.items():
            ma = f"MA20:{'Y' if s.above_ma20 else 'N'} MA50:{'Y' if s.above_ma50 else 'N'}"
            lines.append(
                f"  {sym:<5} {s.label:<32} "
                f"${s.price:>8.2f}  {s.change_1d:>+6.2f}%  {s.change_5d:>+6.2f}%  {ma}"
            )

        lines += ["", "Sector performance vs SPY (5d relative return):"]
        for sym, rel in sorted(self.sector_vs_spy.items(), key=lambda x: -x[1]):
            bar = "▲" if rel > 0 else "▼"
            lines.append(f"  {sym:<5} {bar} {rel:>+5.2f}%  ({UNIVERSE.get(sym, '')})")

        lines += ["", "Notable correlations (5d returns, |corr| > 0.6):"]
        seen = set()
        for s1, row in self.correlations.items():
            for s2, corr in row.items():
                pair = tuple(sorted([s1, s2]))
                if s1 != s2 and abs(corr) > 0.6 and pair not in seen:
                    seen.add(pair)
                    lines.append(f"  {s1}/{s2}: {corr:>+.2f}")

        if self.fetch_errors:
            lines += ["", f"Data gaps (fetch failed): {', '.join(self.fetch_errors)}"]

        return "\n".join(lines)


# ── Cache ─────────────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cached_obs:  Optional[MarketObservation] = None
_cache_time:  Optional[datetime]          = None


def _cache_valid() -> bool:
    if _cached_obs is None or _cache_time is None:
        return False
    ttl = timedelta(minutes=CONFIG.get("intelligence_cache_minutes", 30))
    return (datetime.now(timezone.utc) - _cache_time) < ttl


def invalidate_cache() -> None:
    """Force next call to re-fetch. Call on session open or significant market move."""
    global _cached_obs, _cache_time
    with _cache_lock:
        _cached_obs = None
        _cache_time = None


# ── VIX fetch ─────────────────────────────────────────────────────────────────

def _fetch_vix() -> tuple[float, float, float]:
    """
    Returns (current_vix, 1d_change, 5d_avg).
    Falls back to (0, 0, 0) if unavailable.
    VIX is not available via Alpaca stock API; yfinance is the fallback source.
    """
    try:
        import yfinance as yf
        df = yf.download("^VIX", period="10d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 2:
            return 0.0, 0.0, 0.0
        closes = df["Close"].dropna()
        current  = float(closes.iloc[-1])
        prev     = float(closes.iloc[-2])
        change   = current - prev
        avg5     = float(closes.tail(5).mean())
        return current, change, avg5
    except Exception as exc:
        log.debug(f"market_observer: VIX fetch failed — {exc}")
        return 0.0, 0.0, 0.0


# ── Core fetch ────────────────────────────────────────────────────────────────

def _fetch_all_bars() -> dict[str, pd.DataFrame]:
    """Fetch 30d daily bars for every symbol in UNIVERSE. Returns {symbol: df}."""
    results: dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        df = fetch_bars(sym, period="30d", interval="1d")
        if df is not None and len(df) >= 5:
            results[sym] = df
    return results


def _build_snapshot(sym: str, df: pd.DataFrame) -> AssetSnapshot:
    closes = df["Close"].dropna()
    price    = float(closes.iloc[-1])
    change_1 = float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100) if len(closes) >= 2 else 0.0
    change_5 = float((closes.iloc[-1] / closes.iloc[-6] - 1) * 100) if len(closes) >= 6 else change_1
    ma20     = float(closes.tail(20).mean()) if len(closes) >= 20 else price
    ma50     = float(closes.tail(50).mean()) if len(closes) >= 50 else price
    return AssetSnapshot(
        symbol=sym, label=UNIVERSE[sym],
        price=price, change_1d=change_1, change_5d=change_5,
        above_ma20=(price > ma20), above_ma50=(price > ma50),
    )


def _compute_correlations(bars: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    """5-session return correlation matrix across all fetched symbols."""
    returns: dict[str, pd.Series] = {}
    for sym, df in bars.items():
        closes = df["Close"].dropna()
        if len(closes) >= 6:
            returns[sym] = closes.pct_change().tail(5).dropna()

    if len(returns) < 2:
        return {}

    combined = pd.DataFrame(returns).dropna()
    if combined.empty or len(combined) < 2:
        return {}

    corr_matrix = combined.corr()
    result: dict[str, dict[str, float]] = {}
    for s1 in corr_matrix.index:
        result[str(s1)] = {
            str(s2): round(float(v), 3)
            for s2, v in corr_matrix[s1].items()
        }
    return result


def _compute_sector_vs_spy(
    bars: dict[str, pd.DataFrame],
    assets: dict[str, AssetSnapshot],
) -> dict[str, float]:
    """Sector ETF 5d return minus SPY 5d return — shows rotation."""
    spy_5d = assets.get("SPY", None)
    if spy_5d is None:
        return {}
    spy_ret = spy_5d.change_5d
    return {
        sym: round(assets[sym].change_5d - spy_ret, 3)
        for sym in SECTOR_ETFS
        if sym in assets
    }


# ── Public API ────────────────────────────────────────────────────────────────

def get_market_observation(force_refresh: bool = False) -> MarketObservation:
    """
    Return a MarketObservation. Cached for intelligence_cache_minutes.

    Args:
        force_refresh: bypass cache and re-fetch all data

    Returns:
        MarketObservation — always succeeds; missing instruments logged in
        fetch_errors and observation is built from whatever data was available.
    """
    global _cached_obs, _cache_time

    with _cache_lock:
        if not force_refresh and _cache_valid():
            return _cached_obs

    log.info("market_observer: refreshing cross-asset observations")

    bars   = _fetch_all_bars()
    errors = [sym for sym in UNIVERSE if sym not in bars]
    if errors:
        log.debug(f"market_observer: no data for {errors}")

    assets = {sym: _build_snapshot(sym, df) for sym, df in bars.items()}
    correlations   = _compute_correlations(bars)
    sector_vs_spy  = _compute_sector_vs_spy(bars, assets)
    vix, vix_1d, vix_avg5 = _fetch_vix()

    obs = MarketObservation(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        assets=assets,
        correlations=correlations,
        sector_vs_spy=sector_vs_spy,
        vix=vix,
        vix_change_1d=vix_1d,
        vix_5d_avg=vix_avg5,
        fetch_errors=errors,
    )

    with _cache_lock:
        _cached_obs = obs
        _cache_time = datetime.now(timezone.utc)

    log.info(
        f"market_observer: {len(assets)} assets, "
        f"VIX={vix:.1f}, {len(errors)} fetch errors"
    )
    return obs
