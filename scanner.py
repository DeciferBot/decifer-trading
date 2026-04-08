# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  scanner.py                                 ║
# ║   Dynamic universe via TradingView Screener library          ║
# ║   github.com/shner-elmo/TradingView-Screener                 ║
# ║                                                              ║
# ║   3,000+ fields · RSI/MACD/EMA on any timeframe             ║
# ║   Pre-market/gap scans · No API key · Free                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import concurrent.futures as _cf
import logging
from ib_async import IB
from config import CONFIG
from signals import _safe_download

try:
    from tradingview_screener import Query, col
    _TV_AVAILABLE = True
except ImportError:
    _TV_AVAILABLE = False
    logging.getLogger("decifer.scanner").warning(
        "tradingview-screener not installed — run: pip install tradingview-screener  "
        "Falling back to core + momentum symbols only."
    )

log = logging.getLogger("decifer.scanner")


def _regime_download(symbol: str, period: str = "5d", interval: str = "1h",
                     auto_adjust: bool = True, **_ignored):
    """Download bars for regime detection.

    Priority: Alpaca (paid, reliable) → yfinance (fallback only).
    Module-level so tests can patch scanner._regime_download.
    """
    # Layer 1: Alpaca — primary source
    try:
        from alpaca_data import fetch_bars
        df = fetch_bars(symbol, period=period, interval=interval)
        if df is not None and len(df) > 0:
            return df
    except Exception as _e:
        log.debug(f"_regime_download Alpaca {symbol} failed: {_e}")

    # Layer 2: yfinance — fallback only
    import yfinance as _yf, time as _t
    for attempt in range(3):
        try:
            df = _yf.Ticker(symbol).history(period=period, interval=interval,
                                            auto_adjust=auto_adjust)
            if df is not None and len(df) > 0:
                return df
        except Exception as _e:
            log.debug(f"_regime_download yf {symbol} attempt {attempt+1} failed: {_e}")
        if attempt < 2:
            _t.sleep(2)
    return None

# ── Symbols always included regardless of scanner results ──────
CORE_SYMBOLS = [
    # Macro ETFs (regime detection)
    "SPY", "QQQ", "IWM", "VXX",
    # Volatility
    "UVXY", "SVXY",
    # Inverse ETFs (short exposure)
    "SPXS", "SQQQ",
    # Crypto proxies
    "IBIT", "BITO", "MSTR",
    # Commodities
    "GLD", "SLV", "USO", "COPX",
]

# ── Momentum watchlist — always scored as a floor ─────────────
MOMENTUM_FALLBACK = [
    "AAPL", "NVDA", "TSLA", "AMZN", "MSFT", "META", "GOOGL",
    "AMD",  "ORCL", "NFLX", "CRM",  "SHOP", "SNOW", "PLTR",
    "WDC",  "MU",   "NBIS", "OSCR", "ASTS", "HIMS", "ALAB",
]

# ── TV signal cache — populated each scan cycle ───────────────
# signals.py reads this to skip re-fetching fields already computed here.
# Keys are plain tickers (e.g. "NVDA"). Values are indicator dicts.
_tv_cache: dict[str, dict] = {}

def get_tv_signal_cache() -> dict[str, dict]:
    """
    Return the most recent TradingView indicator snapshot.
    signals.py calls this to reuse pre-fetched RSI/MACD/ATR values
    instead of re-downloading them from yfinance.
    """
    return _tv_cache

# ── Field set fetched on every query ──────────────────────────
_COLS = [
    "name",
    "close",
    "volume",
    "change",                       # daily % change
    "gap",                          # gap from prior close %
    "relative_volume_10d_calc",     # rel. volume vs 10d avg
    "RSI|60",                       # 1h RSI
    "MACD.macd|60",                 # 1h MACD line
    "MACD.signal|60",               # 1h MACD signal
    "EMA9|60",                      # 1h EMA9
    "EMA21|60",                     # 1h EMA21
    "ATR|60",                       # 1h ATR (position sizing)
    "VWAP",                         # intraday VWAP
    "premarket_change",             # pre-market % change
    "premarket_volume",             # pre-market volume
    "Recommend.All",                # TV composite signal (-1 → +1)
    "market_cap_basic",
    "change_from_open",             # Intraday % change from open (short scanner)
    "EMA20",                        # 20-period EMA (breakdown scanner)
    "EMA50",                        # 50-period EMA (breakdown scanner)
]

# ── Base filter applied to every query ────────────────────────
if _TV_AVAILABLE:
    _BASE = [
        col("exchange").isin(["NYSE", "NASDAQ", "AMEX"]),
        col("type") == "stock",
        col("market_cap_basic") > 100_000_000,  # $100M+ market cap
        col("volume") > 500_000,                # minimum liquidity
        col("close").between(2.0, 500.0),       # no pennies, no extreme prices
    ]
else:
    _BASE = []


def _query(extra_filters: list, sort_by: str, ascending: bool, limit: int):
    """Run a single TV screener query, return a DataFrame."""
    return (
        Query()
        .select(*_COLS)
        .where(*(_BASE + extra_filters))
        .order_by(sort_by, ascending=ascending)
        .limit(limit)
        .get_scanner_data()
    )


def _extract(df, source_name: str, symbols: set) -> int:
    """
    Pull tickers from a DataFrame into the symbols set.
    Also populate the TV signal cache with indicator values.
    """
    global _tv_cache
    added = 0
    for row in df.to_dict('records'):
        raw = row.get("ticker", "")
        sym = raw.split(":")[-1] if ":" in raw else str(row.get("name", ""))
        if not sym or len(sym) > 6 or not sym.replace(".", "").isalpha():
            continue
        symbols.add(sym)
        added += 1
        # Cache every indicator field TV returned for this symbol
        _tv_cache[sym] = {
            "tv_close":         row.get("close"),
            "tv_volume":        row.get("volume"),
            "tv_change":        row.get("change"),
            "tv_gap":           row.get("gap"),
            "tv_rel_vol":       row.get("relative_volume_10d_calc"),
            "tv_rsi_1h":        row.get("RSI|60"),
            "tv_macd_1h":       row.get("MACD.macd|60"),
            "tv_macd_sig_1h":   row.get("MACD.signal|60"),
            "tv_ema9_1h":       row.get("EMA9|60"),
            "tv_ema21_1h":      row.get("EMA21|60"),
            "tv_atr_1h":        row.get("ATR|60"),
            "tv_vwap":          row.get("VWAP"),
            "tv_pm_change":     row.get("premarket_change"),
            "tv_pm_volume":     row.get("premarket_volume"),
            "tv_recommend":     row.get("Recommend.All"),
            "tv_market_cap":    row.get("market_cap_basic"),
            "tv_change_open":   row.get("change_from_open"),
            "tv_ema20":         row.get("EMA20"),
            "tv_ema50":         row.get("EMA50"),
            "tv_source":        source_name,
        }
    return added


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
        tickers = ["SPY"] + list(_SECTOR_ETFS.keys())
        data = _regime_download(",".join(tickers), period="1mo", interval="1d")
        if data is None or data.empty:
            return {"available": False}

        # Compute 5-day return per ticker
        import pandas as _pd
        if isinstance(data.columns, _pd.MultiIndex):
            closes = data["Close"] if "Close" in data.columns.get_level_values(0) else data.iloc[:, 0]
        else:
            closes = data[["Close"]] if "Close" in data.columns else data

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
        sector_rs = {
            etf: returns[etf] - spy_ret
            for etf in _SECTOR_ETFS
            if etf in returns
        }

        if len(sector_rs) < 6:
            return {"available": False}

        ranked = sorted(sector_rs.items(), key=lambda x: x[1], reverse=True)
        leaders  = [etf for etf, _ in ranked[:3]]
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
            "bias":      bias,
            "leaders":   leaders,
            "laggards":  laggards,
            "ranked":    ranked,
            "spy_5d_ret": round(spy_ret, 2),
        }

        _sector_bias_cache = result
        _sector_bias_ts = now

        log.info(
            "Sector rotation: leaders=%s laggards=%s (SPY 5d=%.1f%%)",
            leaders, laggards, spy_ret,
        )
        return result

    except Exception as exc:
        log.debug("get_sector_rotation_bias error: %s", exc)
        return {"available": False}


def get_dynamic_universe(ib: IB, regime: dict = None) -> list[str]:
    """
    Build a dynamic universe using TradingView Screener.

    Runs ten targeted queries every scan cycle:
      ALWAYS:
        1. Volume leaders        — most active by raw volume
        2. Relative vol surge    — rel_vol > 1.5×, momentum confirmation
      DIRECTIONAL (regime-weighted):
        3. Momentum longs        — RSI 1h 45–68, MACD positive
        4. Momentum shorts       — RSI 1h 32–55, MACD negative
      SHORT-CANDIDATE PIPELINE (roadmap #02 — all non-PANIC regimes):
        5. Breakdown             — price below EMA20/50, bearish alignment
        6. Volume distribution   — heavy selling on 2x+ volume
        7. Bearish momentum      — RSI < 40, MACD bearish crossover
        8. Intraday breakdown    — down > 3% from open
      CATALYST:
        9. Gap & go + gap-down   — overnight gaps > 3%
       10. Pre-market movers     — pm_change > 3%

    Regime scaling:
      BULL_TRENDING → long scans full, short/breakdown scans reduced
      BEAR_TRENDING → short/breakdown scans full, long scans reduced
      CHOPPY        → breakdown + distribution scans run (fixes old blind spot)
      PANIC         → core + fallback only

    The `ib` parameter is retained for API compatibility.
    """
    global _tv_cache
    _tv_cache = {}   # Fresh cache each scan cycle

    symbols: set[str] = set(CORE_SYMBOLS)

    # ── Sector rotation: add leading sector ETFs to universe ───
    sector_bias = get_sector_rotation_bias()
    if sector_bias.get("available"):
        for etf in sector_bias.get("leaders", []):
            symbols.add(etf)
            log.debug("Sector rotation: adding leader %s to universe", etf)

    regime_name = (regime or {}).get("regime", "UNKNOWN")
    is_panic    = regime_name == "PANIC"
    is_bull     = regime_name == "BULL_TRENDING"
    is_bear     = regime_name == "BEAR_TRENDING"
    is_choppy   = regime_name in ("CHOPPY", "UNKNOWN")

    total_from_tv = 0

    if not _TV_AVAILABLE:
        log.warning("tradingview-screener not available — skipping TV scans. Install with: pip install tradingview-screener")
        symbols.update(MOMENTUM_FALLBACK)
        log.info(f"Universe (fallback): {len(symbols)} symbols")
        return list(symbols)

    # ── Build query list (regime-conditional) ─────────────────────────────────
    # Each entry: (name, _query kwargs).  Queries are independent — run in parallel.
    # _extract() modifies shared state so it runs serially after all fetches.
    _scan_tasks: list[tuple[str, dict]] = []

    # 1. Volume leaders (always)
    _scan_tasks.append(("volume_leaders", dict(
        extra_filters=[col("volume") > 1_000_000],
        sort_by="volume", ascending=False, limit=30,
    )))

    # 2. Relative volume surge (always)
    _scan_tasks.append(("rel_vol_surge", dict(
        extra_filters=[col("relative_volume_10d_calc") > 1.5, col("volume") > 300_000],
        sort_by="relative_volume_10d_calc", ascending=False, limit=25,
    )))

    # 3. Momentum longs — skip in CHOPPY/PANIC
    if not is_panic and not is_choppy:
        long_limit = 25 if is_bull else 12
        _scan_tasks.append(("momentum_long", dict(
            extra_filters=[
                col("RSI|60").between(45, 68),
                col("MACD.macd|60") > col("MACD.signal|60"),
                col("relative_volume_10d_calc") > 1.2,
            ],
            sort_by="relative_volume_10d_calc", ascending=False, limit=long_limit,
        )))

    if not is_panic:
        # 4. Momentum shorts
        short_limit = 25 if is_bear else 12
        _scan_tasks.append(("momentum_short", dict(
            extra_filters=[
                col("RSI|60").between(32, 55),
                col("MACD.macd|60") < col("MACD.signal|60"),
                col("relative_volume_10d_calc") > 1.2,
            ],
            sort_by="relative_volume_10d_calc", ascending=False, limit=short_limit,
        )))

        # 5. Breakdown — below EMA20/50 (all non-PANIC regimes, including CHOPPY)
        breakdown_limit = 25 if is_bear else (15 if is_choppy else 10)
        _scan_tasks.append(("breakdown", dict(
            extra_filters=[
                col("close") < col("EMA20"),
                col("close") < col("EMA50"),
                col("change") < -1.0,
                col("relative_volume_10d_calc") > 1.2,
            ],
            sort_by="change", ascending=True, limit=breakdown_limit,
        )))

        # 6. Volume distribution — heavy selling
        dist_limit = 20 if is_bear else 10
        _scan_tasks.append(("volume_distribution", dict(
            extra_filters=[
                col("relative_volume_10d_calc") > 2.0,
                col("change") < -2.0,
            ],
            sort_by="relative_volume_10d_calc", ascending=False, limit=dist_limit,
        )))

        # 7. Bearish momentum
        bear_mom_limit = 20 if is_bear else (12 if is_choppy else 8)
        _scan_tasks.append(("bearish_momentum", dict(
            extra_filters=[
                col("RSI|60") < 40,
                col("MACD.macd|60") < col("MACD.signal|60"),
                col("change") < -0.5,
                col("relative_volume_10d_calc") > 1.0,
            ],
            sort_by="RSI|60", ascending=True, limit=bear_mom_limit,
        )))

        # 8. Intraday breakdown
        _scan_tasks.append(("intraday_breakdown", dict(
            extra_filters=[col("change_from_open") < -3.0, col("volume") > 500_000],
            sort_by="change_from_open", ascending=True, limit=15,
        )))

        # 9a. Gap up — catalyst longs
        _scan_tasks.append(("gap_go", dict(
            extra_filters=[col("gap").between(3.0, 50.0), col("volume") > 1_000_000],
            sort_by="gap", ascending=False, limit=15,
        )))

        # 9b. Gap down — short candidates
        _scan_tasks.append(("gap_down", dict(
            extra_filters=[col("gap").between(-50.0, -3.0), col("volume") > 1_000_000],
            sort_by="gap", ascending=True, limit=10,
        )))

        # 10. Pre-market movers
        _scan_tasks.append(("premarket_movers", dict(
            extra_filters=[
                col("premarket_change").between(3.0, 200.0),
                col("premarket_volume") > 50_000,
            ],
            sort_by="premarket_change", ascending=False, limit=15,
        )))

    # ── Execute all queries in parallel, extract serially ────────────────────
    def _run_scan_task(task):
        name, kwargs = task
        try:
            _, df = _query(**kwargs)
            return name, df
        except Exception as exc:
            log.warning("TV %s scan failed: %s", name, exc)
            return name, None

    workers = min(len(_scan_tasks), 8)
    with _cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for name, df in pool.map(_run_scan_task, _scan_tasks):
            if df is not None:
                total_from_tv += _extract(df, name, symbols)
                log.debug("TV scan (%s): %d rows", name, len(df))

    if total_from_tv == 0:
        log.warning("All TradingView scans returned zero results — running on core + fallback only")

    # ── Momentum fallback (floor — always included) ───────────
    symbols.update(MOMENTUM_FALLBACK)

    log.info(
        f"Universe: {len(symbols)} symbols | "
        f"{total_from_tv} TV hits | "
        f"regime={regime_name}"
    )
    return list(symbols)


_last_good_regime: dict | None = None   # Cache last valid regime for bad-data fallback


def get_market_regime(ib: IB) -> dict:
    """
    Classify current market regime using SPY, QQQ, and VIX.
    Returns regime dict used by all agents.
    Includes sanity checks to reject corrupt/stale price data.
    """
    global _last_good_regime

    def _flat(df):
        """Flatten multi-level columns from newer yfinance."""
        if df is not None and hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df

    # Use module-level _regime_download so tests can patch scanner._regime_download.
    import sys as _sys
    _dl = _sys.modules[__name__]._regime_download

    try:
        spy = _dl("SPY",  period="5d", interval="1h", auto_adjust=True)
        qqq = _dl("QQQ",  period="5d", interval="1h", auto_adjust=True)

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
        qqq_sane = 50  < qqq_price_now < 1500
        vix_sane = 5   < vix_now < 100

        if not spy_sane or not qqq_sane or not vix_sane:
            bad_parts = []
            if not spy_sane: bad_parts.append(f"SPY=${spy_price_now:.2f}")
            if not qqq_sane: bad_parts.append(f"QQQ=${qqq_price_now:.2f}")
            if not vix_sane: bad_parts.append(f"VIX={vix_now:.2f}")
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
                    "regime": "UNKNOWN", "vix": 0, "vix_1h_change": 0,
                    "spy_price": 0, "spy_above_200d": False,
                    "qqq_price": 0, "qqq_above_200d": False,
                    "position_size_multiplier": 0.5,
                    "regime_router": "unknown",
                }

        vix_prev      = float(vix_close.iloc[-2]) if vix_close is not None and len(vix_close) > 1 else vix_now
        vix_1h_change = (vix_now - vix_prev) / vix_prev if vix_prev > 0 else 0

        # ── 200-DAY DAILY MA — more reliable trend signal than 20h EMA ──
        # The 20h EMA (~2.5 trading days) flipped on intraday noise, causing
        # BULL↔CHOPPY oscillation mid-trend. The 200d daily MA is the standard
        # institutional benchmark and is slow enough to reflect genuine regime.
        # Fallback: use short-term EMA if daily fetch fails.
        spy_above_200d = False
        qqq_above_200d = False
        spy_200d_ma    = None
        qqq_200d_ma    = None
        try:
            spy_daily = _regime_download("SPY", period="1y", interval="1d", auto_adjust=True)
            qqq_daily = _regime_download("QQQ", period="1y", interval="1d", auto_adjust=True)
            if spy_daily is not None and len(spy_daily) >= 50:
                spy_d_close   = spy_daily["Close"].squeeze().dropna()
                spy_200d_ma   = float(spy_d_close.rolling(min(200, len(spy_d_close))).mean().iloc[-1])
                spy_above_200d = spy_price_now > spy_200d_ma
            else:
                # Daily data unavailable — fail safe: assume not above 200d MA
                # (20h EMA on 1h bars ≠ 200d MA; conservative False is correct)
                spy_above_200d = False
                log.warning("SPY daily data unavailable for 200d MA — defaulting spy_above_200d=False")
            if qqq_daily is not None and len(qqq_daily) >= 50:
                qqq_d_close   = qqq_daily["Close"].squeeze().dropna()
                qqq_200d_ma   = float(qqq_d_close.rolling(min(200, len(qqq_d_close))).mean().iloc[-1])
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
        breadth_pct  = None
        breadth_cfg  = CONFIG.get("breadth_regime", {})
        if breadth_cfg.get("enabled", True):
            try:
                _bt = breadth_cfg.get("ticker", "^MMTH")
                _bd = _regime_download(_bt, period="5d", interval="1d", auto_adjust=True)
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
        _breadth_confirms_bull = (breadth_pct is None or breadth_pct > _bull_min)
        _breadth_confirms_bear = (breadth_pct is None or breadth_pct < _bear_max)

        if vix_now > CONFIG["vix_panic_min"] or vix_1h_change > CONFIG["vix_spike_pct"]:
            regime = "PANIC"
        elif (vix_now < CONFIG["vix_bull_max"] and spy_above_200d and qqq_above_200d
              and _breadth_confirms_bull):
            regime = "BULL_TRENDING"
        elif (not spy_above_200d and not qqq_above_200d and vix_now > CONFIG["vix_choppy_max"]
              and _breadth_confirms_bear):
            regime = "BEAR_TRENDING"
        else:
            regime = "CHOPPY"

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
        dxy_trend    = "unknown"
        credit_stress = False
        credit_spread = None
        if CONFIG.get("cross_asset_regime_enabled", True):
            try:
                _dxy = _regime_download("DX-Y.NYB", period="5d", interval="1d", auto_adjust=True)
                if _dxy is not None and len(_dxy) >= 3:
                    _dxy_c = _dxy["Close"].squeeze().dropna()
                    _dxy_3d = float(_dxy_c.iloc[-1]) - float(_dxy_c.iloc[-3])
                    dxy_trend = "rising" if _dxy_3d > 0.2 else ("falling" if _dxy_3d < -0.2 else "flat")
            except Exception as _de:
                log.debug("DXY fetch failed: %s", _de)

            try:
                _hyg = _regime_download("HYG", period="5d", interval="1d", auto_adjust=True)
                _lqd = _regime_download("LQD", period="5d", interval="1d", auto_adjust=True)
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
        _roc_threshold   = CONFIG.get("regime_router_roc_override_pct",   0.4)
        _spy_intraday_pct = 0.0
        _spy_roc_30m      = 0.0
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
                _spy5_close   = _spy5["Close"].squeeze().dropna()
                _spy5_now     = float(_spy5_close.iloc[-1])
                _spy5_open    = float(_spy5_close.iloc[0])   # today's first bar
                _spy5_30m_ago = float(_spy5_close.iloc[max(-7, -len(_spy5_close))])  # ~30 min back
                _spy_intraday_pct = (_spy5_now - _spy5_open)    / _spy5_open    * 100
                _spy_roc_30m      = (_spy5_now - _spy5_30m_ago) / _spy5_30m_ago * 100
        except Exception as _re:
            log.debug("Rally override SPY fetch failed (%s) — skipping override", _re)

        _override_to = None
        if abs(_spy_intraday_pct) > _rally_threshold:
            _override_to = "momentum" if _spy_intraday_pct > 0 else "mean_reversion"
            _trigger     = f"open-to-now {_spy_intraday_pct:+.1f}%"
        elif abs(_spy_roc_30m) > _roc_threshold:
            _override_to = "momentum" if _spy_roc_30m > 0 else "mean_reversion"
            _trigger     = f"30m ROC {_spy_roc_30m:+.2f}%"

        if _override_to and _regime_router != _override_to:
            log.info(
                "RALLY OVERRIDE (%s): router overridden %s → %s",
                _trigger, _regime_router, _override_to,
            )
            _regime_router = _override_to

        result = {
            "regime":                   regime,
            "vix":                      round(vix_now, 2),
            "vix_1h_change":            round(vix_1h_change * 100, 2),
            "spy_price":                round(spy_price_now, 2),
            "spy_above_200d":           spy_above_200d,
            "spy_200d_ma":              round(spy_200d_ma, 2) if spy_200d_ma else None,
            "qqq_price":                round(qqq_price_now, 2),
            "qqq_above_200d":           qqq_above_200d,
            "qqq_200d_ma":              round(qqq_200d_ma, 2) if qqq_200d_ma else None,
            "breadth_pct":              round(breadth_pct, 1) if breadth_pct is not None else None,
            "position_size_multiplier": _regime_size_mult(regime),
            "regime_router":            _regime_router,
            "dxy_trend":                dxy_trend,
            "credit_stress":            credit_stress,
            "credit_spread":            credit_spread,
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
        }


def _regime_size_mult(regime: str) -> float:
    return {
        "BULL_TRENDING": 1.0,
        "BEAR_TRENDING": 1.0,
        "CHOPPY":        1.0,
        "PANIC":         0.0,
        "UNKNOWN":       0.75,
    }.get(regime, 0.75)


def get_small_cap_universe() -> list[str]:
    """
    Supplemental small / micro cap universe via TradingView Screener.

    Market cap $50M–$2B: less efficient, fewer institutional participants,
    anomalies like PEAD and short squeeze persist longer than in large caps.

    Filters:
      - Market cap $50M–$2B
      - Volume > 200K (minimum liquidity)
      - Price $2–$50 (excludes sub-penny and high-priced names)
      - Relative volume > 1.5× (must have an active catalyst today)
      - TV recommendation > 0.2 (bullish lean — squeeze/drift plays are long-only)

    Returns up to 20 tickers, or [] if TV unavailable.
    Also populates _tv_cache for downstream use.
    """
    if not _TV_AVAILABLE:
        log.debug("get_small_cap_universe: tradingview-screener not available")
        return []

    try:
        _, df = (
            Query()
            .select(*_COLS)
            .where(
                col("exchange").isin(["NYSE", "NASDAQ", "AMEX"]),
                col("type") == "stock",
                col("market_cap_basic").between(50_000_000, 2_000_000_000),
                col("volume") > 200_000,
                col("close").between(2.0, 50.0),
                col("relative_volume_10d_calc") > 1.5,
                col("Recommend.All") > 0.2,
            )
            .order_by("relative_volume_10d_calc", ascending=False)
            .limit(20)
            .get_scanner_data()
        )

        symbols: set[str] = set()
        _extract(df, "small_cap", symbols)
        result = list(symbols)
        log.info(f"Small cap universe: {len(result)} symbols (rel_vol filtered, $50M-$2B)")
        return result

    except Exception as e:
        log.debug(f"get_small_cap_universe failed: {e}")
        return []
