# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  scanner.py                                 ║
# ║                                                              ║
# ║   Three-tier universe assembler:                             ║
# ║     Tier A — inline floor (CORE_SYMBOLS + CORE_EQUITIES)    ║
# ║     Tier B — daily promoted list (universe_promoter)        ║
# ║     Tier C — dynamic per-cycle adds (catalyst/held/etc)     ║
# ║                                                              ║
# ║   Also owns market regime classification.                    ║
# ║   TV screener removed 2026-04-15 — replaced with own Python ║
# ║   screening on Alpaca data. See universe_committed.py +     ║
# ║   universe_promoter.py.                                      ║
# ║                                                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from ib_async import IB

from config import CONFIG

log = logging.getLogger("decifer.scanner")


def _regime_download(symbol: str, period: str = "5d", interval: str = "1h", auto_adjust: bool = True, **_ignored):
    """Download bars for regime detection.

    Priority: Alpaca (equities/ETFs) → FMP (^index symbols) → yfinance (last resort).
    Module-level so tests can patch scanner._regime_download.
    """
    # Layer 1: Alpaca — primary for equities and ETFs
    if not symbol.startswith("^"):
        try:
            from alpaca_data import fetch_bars

            df = fetch_bars(symbol, period=period, interval=interval)
            if df is not None and len(df) > 0:
                return df
        except Exception as _e:
            log.debug(f"_regime_download Alpaca {symbol} failed: {_e}")

    # Layer 2: FMP — for ^index symbols (^VIX, ^MMTH, etc.) that Alpaca doesn't carry
    if symbol.startswith("^"):
        try:
            import fmp_client

            df = fmp_client.get_index_bars(symbol, period=period, interval=interval)
            if df is not None and len(df) > 0:
                return df
        except Exception as _e:
            log.debug(f"_regime_download FMP {symbol} failed: {_e}")

    # Layer 3: yfinance — last resort
    import time as _t

    import yfinance as _yf

    for attempt in range(3):
        try:
            df = _yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=auto_adjust)
            if df is not None and len(df) > 0:
                return df
        except Exception as _e:
            log.debug(f"_regime_download yf {symbol} attempt {attempt + 1} failed: {_e}")
        if attempt < 2:
            _t.sleep(2)
    return None


# ── Symbols always included regardless of scanner results ──────
CORE_SYMBOLS = [
    # Macro ETFs (regime detection)
    "SPY",
    "QQQ",
    "IWM",
    "VXX",
    # Volatility
    "UVXY",
    "SVXY",
    # Inverse ETFs (short exposure)
    "SPXS",
    "SQQQ",
    # Crypto proxies
    "IBIT",
    "BITO",
    "MSTR",
    # Commodities
    "GLD",
    "SLV",
    "USO",
    "COPX",
]

# ── Core equity floor — always scored every cycle ─────────────
# These are the mega-caps and sector leaders that must be visible to the
# scoring engine regardless of what TV's RSI/MACD filters decide to surface.
# Historically named MOMENTUM_FALLBACK and only used when TV was unavailable;
# promoted to the always-on equity floor on 2026-04-15 after the Apr 14 rally
# miss (META/AAPL/NVDA filtered out by TV's RSI<68 gate mid-rally).
# Sector-balanced: 3-5 names per sector so the bot always has
# non-tech candidates in its minimum universe.
CORE_EQUITIES = [
    # Technology (5) — kept intentionally lean; TV scans surface more
    "NVDA", "AAPL", "MSFT", "AMD", "CRM",
    # Communication Services (2)
    "GOOGL", "META",
    # Pharma / Biotech (5)
    "LLY", "ABBV", "MRNA", "BIIB", "REGN",
    # Healthcare devices / managed care (3)
    "UNH", "MDT", "ABT",
    # Consumer Discretionary (5) — TSLA added 2026-04-15 after mega-cap rally miss
    "AMZN", "TSLA", "NKE", "MCD", "TGT",
    # Consumer Staples (3)
    "WMT", "COST", "PG",
    # Energy (4)
    "XOM", "CVX", "OXY", "COP",
    # Industrials (3)
    "CAT", "GE", "HON",
    # Materials (3)
    "FCX", "NEM", "LIN",
    # Financials (3)
    "JPM", "GS", "V",
]

# Backward-compat alias — existing callers still import MOMENTUM_FALLBACK.
# Keep this until all references (bot_dashboard, theme_tracker, tests) migrate.
MOMENTUM_FALLBACK = CORE_EQUITIES


# ── Sector ETF universe for rotation scoring ──────────────────
_SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLRE": "Real Estate",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLC": "Communication Services",
}

# ── Individual stocks added when their sector ETF leads ────────
# When XLV is a top-3 sector, these healthcare names join the
# scoring universe — not just the ETF wrapper.
_SECTOR_STOCKS: dict[str, list[str]] = {
    "XLK": ["NVDA", "AAPL", "MSFT", "AMD", "CRM", "ORCL"],
    "XLF": ["JPM", "GS", "MS", "V", "MA", "BAC"],
    "XLE": ["XOM", "CVX", "OXY", "COP", "SLB"],
    "XLV": ["LLY", "ABBV", "MRNA", "UNH", "MDT", "ABT", "BIIB", "REGN"],
    "XLI": ["CAT", "GE", "HON", "BA", "UPS"],
    "XLY": ["AMZN", "TSLA", "NKE", "MCD", "TGT", "SBUX"],
    "XLP": ["WMT", "COST", "PG", "KO", "PEP"],
    "XLRE": ["PLD", "AMT", "SPG", "EQIX"],
    "XLB": ["FCX", "NEM", "LIN", "APD"],
    "XLU": ["NEE", "DUK", "SO", "AEP"],
    "XLC": ["GOOGL", "META", "NFLX", "DIS", "T"],
}

_sector_bias_cache: dict | None = None
_sector_bias_ts: float = 0.0
_SECTOR_BIAS_TTL = 3600.0  # 1-hour cache — sector RS is slow-moving


def get_sector_rotation_bias() -> dict:
    """
    Score all 11 SPDR sector ETFs by relative strength vs SPY (5d return).

    Returns a dict with:
      "bias":     {ETF: multiplier}  — top 3 sectors → 1.5, bottom 3 → 0.5, rest → 1.0
      "leaders":  [ETF, ...]         — top 3 sector ETFs by RS
      "laggards": [ETF, ...]         — bottom 3 sector ETFs by RS
      "ranked":   [(ETF, rs_pct), ...] — full ranking, best first
      "available": bool

    Returns {"available": False} on any error (non-blocking).
    """
    import time as _time

    global _sector_bias_cache, _sector_bias_ts

    now = _time.monotonic()
    if _sector_bias_cache is not None and now - _sector_bias_ts < _SECTOR_BIAS_TTL:
        return _sector_bias_cache

    if not CONFIG.get("sector_rotation_enabled", True):
        return {"available": False}

    try:
        tickers = ["SPY", *list(_SECTOR_ETFS.keys())]
        data = _regime_download(",".join(tickers), period="1mo", interval="1d")
        if data is None or data.empty:
            return {"available": False}

        # Compute 5-day return per ticker
        import pandas as _pd

        if isinstance(data.columns, _pd.MultiIndex):
            data["Close"] if "Close" in data.columns.get_level_values(0) else data.iloc[:, 0]
        else:
            data[["Close"]] if "Close" in data.columns else data

        # yfinance with multiple tickers returns MultiIndex columns — handle both
        returns: dict[str, float] = {}
        for sym in tickers:
            try:
                if isinstance(data.columns, _pd.MultiIndex):
                    col_data = data["Close"][sym].dropna()
                else:
                    col_data = data[sym].dropna() if sym in data.columns else None
                if col_data is not None and len(col_data) >= 6:
                    returns[sym] = float((col_data.iloc[-1] / col_data.iloc[-6]) - 1) * 100
            except Exception:
                continue

        spy_ret = returns.get("SPY", 0.0)
        sector_rs = {etf: returns[etf] - spy_ret for etf in _SECTOR_ETFS if etf in returns}

        if len(sector_rs) < 6:
            return {"available": False}

        ranked = sorted(sector_rs.items(), key=lambda x: x[1], reverse=True)
        leaders = [etf for etf, _ in ranked[:3]]
        laggards = [etf for etf, _ in ranked[-3:]]

        bias = {}
        for etf, _ in ranked:
            if etf in leaders:
                bias[etf] = 1.5
            elif etf in laggards:
                bias[etf] = 0.5
            else:
                bias[etf] = 1.0

        result = {
            "available": True,
            "bias": bias,
            "leaders": leaders,
            "laggards": laggards,
            "ranked": ranked,
            "spy_5d_ret": round(spy_ret, 2),
        }

        _sector_bias_cache = result
        _sector_bias_ts = now

        log.info(
            "Sector rotation: leaders=%s laggards=%s (SPY 5d=%.1f%%)",
            leaders,
            laggards,
            spy_ret,
        )
        return result

    except Exception as exc:
        log.debug("get_sector_rotation_bias error: %s", exc)
        return {"available": False}


# ── Position Research Universe (Tier D) cache ─────────────────────────────────
# Populated on the first get_position_research_universe() call, then refreshed
# only when the PRU file changes (mtime-based invalidation).
# Exported so signal_pipeline.py and signal_dispatcher.py can read metadata.

_POSITION_RESEARCH_SYMBOLS: frozenset = frozenset()
_POSITION_RESEARCH_META: dict = {}  # ticker → full metadata dict
_pru_file_mtime: float = -1.0       # os.path.getmtime() at last load; -1 = never loaded
_pru_loaded_at: str = ""            # ISO timestamp of last cache load
_pru_built_at: str = ""             # built_at from the PRU file
_pru_symbol_count: int = 0          # symbol count at last load


def get_position_research_universe() -> tuple[frozenset, dict]:
    """
    Load Tier D tickers and metadata from position_research_universe.json.

    Uses mtime-based cache invalidation — re-reads the file only when it has
    changed since the last load.  On a cache hit, returns cached values and
    logs at DEBUG.  On a cache miss (first load or file changed), logs at INFO
    with built_at / loaded_at / symbol count and whether it was a refresh.

    Returns (symbol_frozenset, meta_by_ticker_dict).
    Returns (frozenset(), {}) on missing/stale/malformed file — graceful degradation.
    Called by get_dynamic_universe() and signal_dispatcher.dispatch_signals().
    """
    global _POSITION_RESEARCH_SYMBOLS, _POSITION_RESEARCH_META
    global _pru_file_mtime, _pru_loaded_at, _pru_built_at, _pru_symbol_count

    if not CONFIG.get("position_research_universe_enabled", True):
        return frozenset(), {}

    from universe_position import _PRU_PATH, load_position_research_universe

    # ── Mtime check: skip disk read if file unchanged ─────────────────────────
    try:
        current_mtime = os.path.getmtime(_PRU_PATH)
    except OSError:
        current_mtime = 0.0

    if current_mtime == _pru_file_mtime and _pru_file_mtime >= 0:
        log.debug(
            "Tier D: cache reused (built_at=%s loaded_at=%s symbols=%d)",
            _pru_built_at, _pru_loaded_at, _pru_symbol_count,
        )
        return _POSITION_RESEARCH_SYMBOLS, _POSITION_RESEARCH_META

    # ── Cache miss: file changed or first load ────────────────────────────────
    was_refresh = _pru_file_mtime >= 0  # True if we previously had a valid cache
    try:
        tickers, meta_list, built_at_str = load_position_research_universe()
        if tickers:
            _POSITION_RESEARCH_SYMBOLS = frozenset(tickers)
            _POSITION_RESEARCH_META = {
                m["ticker"]: m for m in meta_list if isinstance(m, dict) and "ticker" in m
            }
        else:
            _POSITION_RESEARCH_SYMBOLS = frozenset()
            _POSITION_RESEARCH_META = {}
        _pru_file_mtime = current_mtime
        _pru_loaded_at = datetime.now(UTC).isoformat()
        _pru_built_at = built_at_str
        _pru_symbol_count = len(tickers)

        action = "REFRESHED (file changed)" if was_refresh else "loaded"
        log.info(
            "Tier D: cache %s — built_at=%s loaded_at=%s symbols=%d",
            action, _pru_built_at, _pru_loaded_at, _pru_symbol_count,
        )
    except Exception as exc:
        log.warning("Tier D: load failed — %s — continuing without Tier D", exc)
        _POSITION_RESEARCH_SYMBOLS = frozenset()
        _POSITION_RESEARCH_META = {}

    return _POSITION_RESEARCH_SYMBOLS, _POSITION_RESEARCH_META


def get_dynamic_universe(ib: IB, regime: dict | None = None) -> list[str]:
    """
    Build the per-cycle scan universe from four tiers:

      Tier A — inline floor (always scanned):
        CORE_SYMBOLS (15 macro/vol/inverse/crypto/commodity ETFs)
        + CORE_EQUITIES (41 mega-cap equities)

      Tier B — daily promoted list (top 50 from universe_promoter):
        Read from data/daily_promoted.json. Refreshed 16:15 ET + 08:00 ET
        by universe_promoter. Stale files (>18h) are ignored and the bot
        runs on Tier A only.

      Tier C — dynamic per-cycle adds:
        Sector-rotation leaders and their constituent stocks.
        Other Tier C paths (catalyst candidates, held positions, favourites,
        sympathy, news hits) are unioned in by the caller (bot_trading
        union logic) — not this function.

      Tier D — Position Research Universe (additive, shadow mode):
        Fundamental-quality discovery names from the committed Master Universe.
        Bypasses gap/premarket-volume promoter. Read from
        data/position_research_universe.json (built weekly).
        Controlled by position_research_universe_enabled config key.

    Circuit breaker: if VIX is in extreme panic territory, we do not restrict
    the universe here. Risk gating happens downstream (risk.check_risk_conditions,
    PM exit triggers). Keeping the universe wide during capitulation lets the
    scoring engine see the full set of candidates.

    The `ib` parameter is retained for API compatibility.
    """
    # Tier A — always-on floor
    symbols: set[str] = set(CORE_SYMBOLS) | set(CORE_EQUITIES)
    n_core = len(CORE_SYMBOLS)
    n_equities = len(CORE_EQUITIES)

    # Tier B — promoted
    n_promoted = 0
    try:
        from universe_promoter import load_promoted_universe

        promoted = load_promoted_universe()
        if promoted:
            before = len(symbols)
            symbols.update(promoted)
            n_promoted = len(symbols) - before
        else:
            log.warning(
                "Universe: daily_promoted.json missing/stale — running Tier A only. "
                "Check universe_promoter schedule (16:15 ET / 08:00 ET)."
            )
    except Exception as exc:
        log.warning(f"Universe: promoter load failed — {exc}. Running Tier A only.")

    # Tier C — sector rotation leaders (other Tier C paths union'd in by caller)
    n_sector = 0
    sector_bias = get_sector_rotation_bias()
    if sector_bias.get("available"):
        before = len(symbols)
        for etf in sector_bias.get("leaders", []):
            symbols.add(etf)
            for stock in _SECTOR_STOCKS.get(etf, []):
                symbols.add(stock)
        n_sector = len(symbols) - before
        log.info(
            "Sector rotation leaders: %s — added %d new sector stocks",
            sector_bias.get("leaders", []),
            n_sector,
        )

    # Tier D — Position Research Universe (shadow mode, additive)
    n_tier_d = 0
    n_tier_d_new = 0
    if CONFIG.get("position_research_universe_enabled", True):
        tier_d_syms, _meta = get_position_research_universe()
        if tier_d_syms:
            n_tier_d = len(tier_d_syms)
            before = len(symbols)
            symbols.update(tier_d_syms)
            n_tier_d_new = len(symbols) - before
            log.info(
                "Tier D: %d position research names loaded (%d new, %d already in A/B/C)",
                n_tier_d, n_tier_d_new, n_tier_d - n_tier_d_new,
            )
        else:
            log.debug("Tier D: no position research universe available this cycle")

    _vix = (regime or {}).get("vix", 0)
    _vix_1h = (regime or {}).get("vix_1h_change", 0)
    _is_extreme = _vix > CONFIG.get("vix_panic_min", 35) or _vix_1h > CONFIG.get("vix_spike_pct", 0.20)
    if _is_extreme:
        log.warning(
            f"Universe: EXTREME_STRESS flag set — VIX={_vix:.1f} spike={_vix_1h:.1%}. "
            "Universe unchanged; risk gating occurs downstream."
        )

    log.info(
        f"Universe: {len(symbols)} symbols | core={n_core} equities={n_equities} "
        f"promoted={n_promoted} sector+={n_sector} tier_d={n_tier_d} "
        f"| vix={_vix:.1f} extreme={_is_extreme}"
    )
    return list(symbols)


_last_good_regime: dict | None = None  # Cache last valid regime for bad-data fallback


def get_market_regime(ib: IB) -> dict:
    """
    Classify current market regime using SPY, QQQ, and VIX.
    Returns regime dict used by all agents.
    Includes sanity checks to reject corrupt/stale price data.
    """
    global _last_good_regime

    def _flat(df):
        """Flatten multi-level columns from newer yfinance."""
        if df is not None and hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df

    # Use module-level _regime_download so tests can patch scanner._regime_download.
    import sys as _sys

    _dl = _sys.modules[__name__]._regime_download

    try:
        spy = _dl("SPY", period="5d", interval="1h", auto_adjust=True)
        qqq = _dl("QQQ", period="5d", interval="1h", auto_adjust=True)

        vix = None
        for vix_ticker in ["^VIX", "VIX", "VIXY"]:
            vix = _dl(vix_ticker, period="5d", interval="1h", auto_adjust=True)
            if vix is not None and len(vix) > 0:
                break

        if vix is None or len(vix) == 0:
            vix = _dl("VIXY", period="5d", interval="1d", auto_adjust=True)

        if spy is None or len(spy) == 0:
            log.warning("get_market_regime: SPY 1h fetch returned None — using last good regime")
            if _last_good_regime:
                return _last_good_regime
            raise ValueError("SPY data unavailable and no cached regime")

        if qqq is None or len(qqq) == 0:
            log.warning("get_market_regime: QQQ 1h fetch returned None — using last good regime")
            if _last_good_regime:
                return _last_good_regime
            raise ValueError("QQQ data unavailable and no cached regime")

        spy_close = spy["Close"].squeeze()
        qqq_close = qqq["Close"].squeeze()
        vix_close = vix["Close"].squeeze() if vix is not None and len(vix) > 0 else None

        if vix_close is None or len(vix_close) == 0:
            spy_returns = spy_close.pct_change().dropna()
            vix_now = float(spy_returns.std() * 100 * (252**0.5))
        else:
            vix_now = float(vix_close.iloc[-1])

        spy_price_now = float(spy_close.iloc[-1])
        qqq_price_now = float(qqq_close.iloc[-1])

        # ── SANITY CHECKS — reject obviously corrupt data ──────────
        # SPY trades ~$400-800, QQQ ~$300-600, VIX ~8-80 in normal/stressed markets.
        # Anything wildly outside these bands is bad data, not a real move.
        spy_sane = 100 < spy_price_now < 2000
        qqq_sane = 50 < qqq_price_now < 1500
        vix_sane = 5 < vix_now < 100

        if not spy_sane or not qqq_sane or not vix_sane:
            bad_parts = []
            if not spy_sane:
                bad_parts.append(f"SPY=${spy_price_now:.2f}")
            if not qqq_sane:
                bad_parts.append(f"QQQ=${qqq_price_now:.2f}")
            if not vix_sane:
                bad_parts.append(f"VIX={vix_now:.2f}")
            log.error(
                f"REGIME DATA SANITY FAIL: {', '.join(bad_parts)} — "
                f"values outside plausible range, rejecting corrupt data"
            )
            if _last_good_regime:
                log.warning(f"Falling back to last known good regime: {_last_good_regime['regime']}")
                return _last_good_regime
            else:
                log.warning("No previous good regime cached — returning UNKNOWN")
                return {
                    "regime": "UNKNOWN",
                    "vix": 0,
                    "vix_1h_change": 0,
                    "vix_change_1d": 0.0,
                    "spy_price": 0,
                    "spy_above_200d": False,
                    "qqq_price": 0,
                    "qqq_above_200d": False,
                    "position_size_multiplier": 0.5,
                    "regime_router": "unknown",
                }

        vix_prev = float(vix_close.iloc[-2]) if vix_close is not None and len(vix_close) > 1 else vix_now
        vix_1h_change = (vix_now - vix_prev) / vix_prev if vix_prev > 0 else 0

        # Daily VIX change: yesterday's last 1h bar vs today's current level.
        # Used by dashboard to verify session_character labelling.
        vix_change_1d = 0.0
        try:
            if vix_close is not None and len(vix_close) >= 2:
                _today = vix_close.index[-1].date()
                _prev_day_bars = vix_close[[d.date() < _today for d in vix_close.index]]
                if len(_prev_day_bars) > 0:
                    vix_change_1d = round(vix_now - float(_prev_day_bars.iloc[-1]), 2)
        except Exception:
            pass

        # ── 200-DAY DAILY MA — more reliable trend signal than 20h EMA ──
        # The 20h EMA (~2.5 trading days) flipped on intraday noise, causing
        # TRENDING_UP↔RANGE_BOUND oscillation mid-trend. The 200d daily MA is the standard
        # institutional benchmark and is slow enough to reflect genuine regime.
        # Fallback: use short-term EMA if daily fetch fails.
        spy_above_200d = False
        qqq_above_200d = False
        spy_200d_ma = None
        qqq_200d_ma = None
        try:
            spy_daily = _dl("SPY", period="1y", interval="1d", auto_adjust=True)
            qqq_daily = _dl("QQQ", period="1y", interval="1d", auto_adjust=True)
            if spy_daily is not None and len(spy_daily) >= 50:
                spy_d_close = spy_daily["Close"].squeeze().dropna()
                spy_200d_ma = float(spy_d_close.rolling(min(200, len(spy_d_close))).mean().iloc[-1])
                spy_above_200d = spy_price_now > spy_200d_ma
            else:
                # Daily data unavailable — fail safe: assume not above 200d MA
                # (20h EMA on 1h bars ≠ 200d MA; conservative False is correct)
                spy_above_200d = False
                log.warning("SPY daily data unavailable for 200d MA — defaulting spy_above_200d=False")
            if qqq_daily is not None and len(qqq_daily) >= 50:
                qqq_d_close = qqq_daily["Close"].squeeze().dropna()
                qqq_200d_ma = float(qqq_d_close.rolling(min(200, len(qqq_d_close))).mean().iloc[-1])
                qqq_above_200d = qqq_price_now > qqq_200d_ma
            else:
                qqq_above_200d = False
                log.warning("QQQ daily data unavailable for 200d MA — defaulting qqq_above_200d=False")
        except Exception as _daily_err:
            log.warning(f"200d MA fetch failed ({_daily_err}) — defaulting both above_200d flags to False")
            spy_above_200d = False
            qqq_above_200d = False

        # ── MARKET BREADTH (^MMTH: % of S&P 500 above their 200d MA) ────
        # Breadth confirms whether a trend has broad participation or is
        # driven by a handful of mega-caps. A SPY above its 200d MA with
        # MMTH < 40% is a narrow-leader rally, not a genuine bull regime.
        breadth_pct = None
        breadth_cfg = CONFIG.get("breadth_regime", {})
        if breadth_cfg.get("enabled", True):
            try:
                _bt = breadth_cfg.get("ticker", "^MMTH")
                _bd = _dl(_bt, period="5d", interval="1d", auto_adjust=True)
                if _bd is not None and len(_bd) > 0:
                    breadth_pct = float(_bd["Close"].squeeze().dropna().iloc[-1])
            except Exception as _be:
                log.debug(f"Breadth fetch failed ({_be}) — proceeding without breadth")

        # ── REGIME CLASSIFICATION ─────────────────────────────────────────
        # Three-factor classification:
        #   Factor 1: VIX level (fear / implied vol)
        #   Factor 2: SPY + QQQ vs 200d daily MA (structural trend)
        #   Factor 3: Market breadth (participation confirmation)
        _bull_min = breadth_cfg.get("bull_min", 55.0)
        _bear_max = breadth_cfg.get("bear_max", 40.0)
        _breadth_confirms_bull = breadth_pct is None or breadth_pct > _bull_min
        _breadth_confirms_bear = breadth_pct is None or breadth_pct < _bear_max

        if vix_now > CONFIG["vix_panic_min"] or vix_1h_change > CONFIG["vix_spike_pct"]:
            regime = "CAPITULATION"
        elif vix_now < CONFIG["vix_bull_max"] and spy_above_200d and qqq_above_200d and _breadth_confirms_bull:
            regime = "TRENDING_UP"
        elif (
            not spy_above_200d and not qqq_above_200d and vix_now > CONFIG["vix_choppy_max"] and _breadth_confirms_bear
        ):
            regime = "TRENDING_DOWN"
        elif not spy_above_200d and not qqq_above_200d and vix_1h_change < -0.05:
            # Both below 200d MA (structural bear) but VIX falling — intraday bounce within downtrend
            regime = "RELIEF_RALLY"
        else:
            regime = "RANGE_BOUND"

        # ── SIGNAL ROUTING REGIME — uses VIX already fetched above ──────────
        # Avoids a duplicate VIX fetch in score_universe(). The routing regime
        # determines dimension score multipliers (momentum vs mean_reversion tilt).
        # Passed through signal_pipeline → score_universe via regime["regime_router"].
        _vix_routing_threshold = CONFIG.get("regime_router_vix_threshold", 20)
        _regime_router = "momentum" if vix_now < _vix_routing_threshold else "mean_reversion"

        # ── DXY + CREDIT SPREAD — cross-asset risk-off early warning ─────────
        # DXY rising = dollar strengthening = risk-off (money flees to safety).
        # Credit spread (HYG yield vs LQD yield) widening = corporate stress.
        # These signals can detect risk-off transitions 1-2 days before VIX spikes.
        dxy_trend = "unknown"
        credit_stress = False
        credit_spread = None
        if CONFIG.get("cross_asset_regime_enabled", True):
            try:
                # UUP (Invesco DB USD Index ETF) tracks DXY — Alpaca handles ETFs reliably
                _dxy = _dl("UUP", period="5d", interval="1d", auto_adjust=True)
                if _dxy is not None and len(_dxy) >= 3:
                    _dxy_c = _dxy["Close"].squeeze().dropna()
                    _dxy_pct = float(_dxy_c.iloc[-1]) / float(_dxy_c.iloc[-3]) - 1
                    dxy_trend = "rising" if _dxy_pct > 0.002 else ("falling" if _dxy_pct < -0.002 else "flat")
            except Exception as _de:
                log.debug("DXY fetch failed: %s", _de)

            try:
                _hyg = _dl("HYG", period="5d", interval="1d", auto_adjust=True)
                _lqd = _dl("LQD", period="5d", interval="1d", auto_adjust=True)
                if _hyg is not None and _lqd is not None and len(_hyg) >= 3 and len(_lqd) >= 3:
                    _hyg_c = _hyg["Close"].squeeze().dropna()
                    _lqd_c = _lqd["Close"].squeeze().dropna()
                    # Spread proxy: HYG price drop relative to LQD price drop
                    # When HYG falls faster than LQD, high-yield spreads widening
                    _hyg_ret = float(_hyg_c.iloc[-1] / _hyg_c.iloc[-3]) - 1
                    _lqd_ret = float(_lqd_c.iloc[-1] / _lqd_c.iloc[-3]) - 1
                    credit_spread = round((_lqd_ret - _hyg_ret) * 100, 2)  # positive = stress
                    _stress_threshold = CONFIG.get("credit_stress_threshold", 0.4)
                    credit_stress = credit_spread > _stress_threshold
            except Exception as _ce:
                log.debug("Credit spread fetch failed: %s", _ce)

        # Override regime router if cross-asset signals show stress despite low VIX
        if credit_stress and _regime_router == "momentum":
            log.info(
                "CROSS-ASSET OVERRIDE: credit stress (spread=%.2f%%) overrides momentum router → mean_reversion",
                credit_spread or 0,
            )
            _regime_router = "mean_reversion"

        # Override regime router based on strong intraday SPY directional move.
        # Fires after the credit stress override so a genuine risk-off crash (credit
        # stress + big down day) still uses mean_reversion weighting.
        #
        # Data source priority for intraday SPY bars (fastest → slowest):
        #   1. BAR_CACHE (Alpaca WebSocket stream) — live 1m bars → 5m aggregation.
        #      SPY is a STREAM_ANCHOR, always subscribed, so this is near-instantaneous.
        #   2. Alpaca REST fetch_bars("SPY", period="1d", interval="5m") — last 5m bar
        #      is ≤4 min stale. Used when stream is cold (bot just started).
        # No yfinance.
        #
        # Two signals, either triggers the override:
        #   1. Open-to-now > rally_override_pct%  (sustained intraday trend)
        #   2. 30m ROC > roc_override_pct%        (fast acceleration, e.g. +0.5% in 30 min)
        _rally_threshold = CONFIG.get("regime_router_rally_override_pct", 1.5)
        _roc_threshold = CONFIG.get("regime_router_roc_override_pct", 0.4)
        _spy_intraday_pct = 0.0
        _spy_roc_30m = 0.0
        try:
            # Layer 1: live stream cache (SPY always subscribed as STREAM_ANCHOR)
            _spy5 = None
            try:
                from alpaca_stream import BAR_CACHE

                _spy5 = BAR_CACHE.get_5m("SPY")
            except Exception:
                pass

            # Layer 2: Alpaca REST (stream cold — bot just started)
            if _spy5 is None or len(_spy5) < 2:
                from alpaca_data import fetch_bars as _fetch_bars

                _spy5 = _fetch_bars("SPY", period="1d", interval="5m")

            if _spy5 is not None and len(_spy5) >= 2:
                _spy5_close = _spy5["Close"].squeeze().dropna()
                _spy5_now = float(_spy5_close.iloc[-1])
                _spy5_open = float(_spy5_close.iloc[0])  # today's first bar
                _spy5_30m_ago = float(_spy5_close.iloc[max(-7, -len(_spy5_close))])  # ~30 min back
                _spy_intraday_pct = (_spy5_now - _spy5_open) / _spy5_open * 100
                _spy_roc_30m = (_spy5_now - _spy5_30m_ago) / _spy5_30m_ago * 100
        except Exception as _re:
            log.debug("Rally override SPY fetch failed (%s) — skipping override", _re)

        _override_to = None
        if abs(_spy_intraday_pct) > _rally_threshold:
            _override_to = "momentum" if _spy_intraday_pct > 0 else "mean_reversion"
            _trigger = f"open-to-now {_spy_intraday_pct:+.1f}%"
        elif abs(_spy_roc_30m) > _roc_threshold:
            _override_to = "momentum" if _spy_roc_30m > 0 else "mean_reversion"
            _trigger = f"30m ROC {_spy_roc_30m:+.2f}%"

        if _override_to and _regime_router != _override_to:
            log.info(
                "RALLY OVERRIDE (%s): router overridden %s → %s",
                _trigger,
                _regime_router,
                _override_to,
            )
            _regime_router = _override_to

        # ── DAILY TAPE METRICS ────────────────────────────────────────────
        # SPY/QQQ/IWM % vs prior close — tells agents what the tape is doing
        # TODAY, independent of structural regime.
        spy_chg_1d = qqq_chg_1d = iwm_chg_1d = 0.0
        try:
            from alpaca_data import fetch_snapshots as _fetch_snaps
            _snaps = _fetch_snaps(["SPY", "QQQ", "IWM"])
            spy_chg_1d = float(_snaps.get("SPY", {}).get("change_1d", 0.0) or 0.0) * 100
            qqq_chg_1d = float(_snaps.get("QQQ", {}).get("change_1d", 0.0) or 0.0) * 100
            iwm_chg_1d = float(_snaps.get("IWM", {}).get("change_1d", 0.0) or 0.0) * 100
        except Exception as _te:
            log.warning(f"get_market_regime: tape fetch failed ({_te}) — defaulting chg fields to 0.0")

        # 3-day slope from already-fetched 5d/1h data (no extra API call)
        spy_slope_3d = qqq_slope_3d = 0.0
        try:
            if len(spy_close) >= 19:  # ~19 1h bars per 3 trading days (6.5h/day)
                spy_slope_3d = (spy_close.iloc[-1] - spy_close.iloc[-19]) / spy_close.iloc[-19] * 100
            if len(qqq_close) >= 19:
                qqq_slope_3d = (qqq_close.iloc[-1] - qqq_close.iloc[-19]) / qqq_close.iloc[-19] * 100
        except Exception as _se:
            log.debug(f"get_market_regime: slope computation failed ({_se})")

        tape_context = _build_tape_context(spy_chg_1d, qqq_chg_1d, iwm_chg_1d, spy_slope_3d, qqq_slope_3d)

        result = {
            "regime": regime,
            "vix": round(vix_now, 2),
            "vix_1h_change": round(vix_1h_change, 4),
            "vix_change_1d": vix_change_1d,
            "spy_price": round(spy_price_now, 2),
            "spy_above_200d": spy_above_200d,
            "spy_200d_ma": round(spy_200d_ma, 2) if spy_200d_ma else None,
            "qqq_price": round(qqq_price_now, 2),
            "qqq_above_200d": qqq_above_200d,
            "qqq_200d_ma": round(qqq_200d_ma, 2) if qqq_200d_ma else None,
            "breadth_pct": round(breadth_pct, 1) if breadth_pct is not None else None,
            "position_size_multiplier": _regime_size_mult(regime),
            "regime_router": _regime_router,
            "dxy_trend": dxy_trend,
            "credit_stress": credit_stress,
            "credit_spread": credit_spread,
            "spy_chg_1d": round(spy_chg_1d, 2),
            "qqq_chg_1d": round(qqq_chg_1d, 2),
            "iwm_chg_1d": round(iwm_chg_1d, 2),
            "spy_slope_3d": round(spy_slope_3d, 2),
            "qqq_slope_3d": round(qqq_slope_3d, 2),
            "tape_context": tape_context,
        }

        # Cache this as last known good regime
        _last_good_regime = result
        return result

    except Exception as e:
        log.error(f"Regime detection error: {e}")
        if _last_good_regime:
            log.warning(f"Falling back to last known good regime: {_last_good_regime['regime']}")
            return _last_good_regime
        return {
            "regime": "UNKNOWN",
            "vix": 0,
            "vix_1h_change": 0,
            "vix_change_1d": 0.0,
            "spy_price": 0,
            "spy_above_200d": False,
            "spy_200d_ma": None,
            "qqq_price": 0,
            "qqq_above_200d": False,
            "qqq_200d_ma": None,
            "breadth_pct": None,
            "position_size_multiplier": 0.5,
            "regime_router": "unknown",
            "dxy_trend": "unknown",
            "credit_stress": False,
            "credit_spread": None,
            "spy_chg_1d": 0.0,
            "qqq_chg_1d": 0.0,
            "iwm_chg_1d": 0.0,
            "spy_slope_3d": 0.0,
            "qqq_slope_3d": 0.0,
            "tape_context": {"prose": "tape data unavailable", "description": "unknown", "slope_description": "unknown", "spy_chg_1d": 0.0, "qqq_chg_1d": 0.0, "iwm_chg_1d": 0.0, "spy_slope_3d": 0.0, "qqq_slope_3d": 0.0},
        }


def _build_tape_context(
    spy_chg: float,
    qqq_chg: float,
    iwm_chg: float,
    spy_slope_3d: float,
    qqq_slope_3d: float,
) -> dict:
    """Build a TapeContext dict describing today's market tape."""
    # Direction description
    if qqq_chg < -1.5 and spy_chg < -1.0:
        desc = "growth/tech-led selloff"
    elif spy_chg < -1.0 and qqq_chg > spy_chg:
        desc = "broad selloff, defensive rotation"
    elif spy_chg > 1.0 and qqq_chg > 1.0:
        desc = "broad rally"
    elif spy_chg > 1.0 and qqq_chg < spy_chg:
        desc = "value/defensive-led rally, growth lagging"
    elif spy_chg < -0.5 or qqq_chg < -0.5:
        desc = "soft tape, mild selling"
    elif spy_chg > 0.5 or qqq_chg > 0.5:
        desc = "positive tape"
    else:
        desc = "mixed tape"

    if iwm_chg < -1.5:
        desc += ", risk-off breadth"

    # 3-day slope context (use average of SPY and QQQ slopes)
    avg_slope = (spy_slope_3d + qqq_slope_3d) / 2
    if avg_slope > 2.0:
        slope_desc = f"after {avg_slope:.1f}% 3-day run — potential pullback"
    elif avg_slope < -2.0:
        slope_desc = f"extending {avg_slope:.1f}% 3-day decline"
    else:
        slope_desc = "in a flat 3-day range"

    return {
        "spy_chg_1d": round(spy_chg, 2),
        "qqq_chg_1d": round(qqq_chg, 2),
        "iwm_chg_1d": round(iwm_chg, 2),
        "spy_slope_3d": round(spy_slope_3d, 2),
        "qqq_slope_3d": round(qqq_slope_3d, 2),
        "description": desc,
        "slope_description": slope_desc,
        "prose": (
            f"SPY {spy_chg:+.1f}%, QQQ {qqq_chg:+.1f}%, IWM {iwm_chg:+.1f}% today"
            f" — {desc}, {slope_desc}"
        ),
    }


def _regime_size_mult(regime: str) -> float:
    return {
        "TRENDING_UP": 1.0,
        "TRENDING_DOWN": 1.0,
        "RELIEF_RALLY": 1.0,
        "RANGE_BOUND": 1.0,
        "CAPITULATION": 0.0,
        "UNKNOWN": 0.75,
    }.get(regime, 0.75)


# get_small_cap_universe() was removed 2026-04-15 as part of the TV rip.
# Small-cap coverage now comes from universe_committed.json (ranked by dollar
# volume, naturally includes $50M–$2B names meeting the liquidity floor) and
# universe_promoter surfaces the movers each day.
