# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  scanner.py                                 ║
# ║   Dynamic universe via TradingView Screener library          ║
# ║   github.com/shner-elmo/TradingView-Screener                 ║
# ║                                                              ║
# ║   3,000+ fields · RSI/MACD/EMA on any timeframe             ║
# ║   Pre-market/gap scans · No API key · Free                   ║
# ╚══════════════════════════════════════════════════════════════╝

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
]

# ── Base filter applied to every query ────────────────────────
_BASE = [
    col("exchange").isin(["NYSE", "NASDAQ", "AMEX"]),
    col("type") == "stock",
    col("market_cap_basic") > 100_000_000,  # $100M+ market cap
    col("volume") > 500_000,                # minimum liquidity
    col("close").between(2.0, 500.0),       # no pennies, no extreme prices
]


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
    for _, row in df.iterrows():
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
            "tv_source":        source_name,
        }
    return added


def get_dynamic_universe(ib: IB, regime: dict = None) -> list[str]:
    """
    Build a dynamic universe using TradingView Screener.

    Runs six targeted queries every scan cycle:
      1. Volume leaders       — most active by raw volume (always)
      2. Relative vol surge   — rel_vol > 1.5×, momentum confirmation
      3. Momentum longs       — RSI 1h 45–68, MACD positive (BULL bias)
      4. Momentum shorts      — RSI 1h 32–55, MACD negative (BEAR bias)
      5. Gap & go             — gap > 3 %, volume > 1 M
      6. Pre-market movers    — pm_change > 3 %, pm_vol > 50 k

    Queries 3 & 4 are weighted by regime:
      BULL_TRENDING → long scan runs at full size, short scan at half
      BEAR_TRENDING → short scan full, long scan half
      CHOPPY        → only scans 1 & 2 (momentum/volume)
      PANIC         → skip scans 3–6; core + fallback only

    The `ib` parameter is retained for API compatibility (regime detection
    still uses it) but is NOT used here.
    """
    global _tv_cache
    _tv_cache = {}   # Fresh cache each scan cycle

    symbols: set[str] = set(CORE_SYMBOLS)

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

    # ── 1. Volume leaders (always) ─────────────────────────────
    try:
        _, df = _query(
            extra_filters=[col("volume") > 1_000_000],
            sort_by="volume",
            ascending=False,
            limit=30,
        )
        total_from_tv += _extract(df, "volume_leaders", symbols)
        log.debug(f"TV scan 1 (volume_leaders): {len(df)} rows")
    except Exception as e:
        log.warning(f"TV volume_leaders scan failed: {e}")

    # ── 2. Relative volume surge (always) ─────────────────────
    try:
        _, df = _query(
            extra_filters=[
                col("relative_volume_10d_calc") > 1.5,
                col("volume") > 300_000,
            ],
            sort_by="relative_volume_10d_calc",
            ascending=False,
            limit=25,
        )
        total_from_tv += _extract(df, "rel_vol_surge", symbols)
        log.debug(f"TV scan 2 (rel_vol_surge): {len(df)} rows")
    except Exception as e:
        log.warning(f"TV rel_vol_surge scan failed: {e}")

    # ── 3. Momentum longs — RSI trending, MACD positive ───────
    # Run at full size in BULL, half in BEAR, skip in CHOPPY/PANIC
    if not is_panic and not is_choppy:
        long_limit = 25 if is_bull else 12
        try:
            _, df = _query(
                extra_filters=[
                    col("RSI|60").between(45, 68),
                    col("MACD.macd|60") > col("MACD.signal|60"),
                    col("relative_volume_10d_calc") > 1.2,
                ],
                sort_by="relative_volume_10d_calc",
                ascending=False,
                limit=long_limit,
            )
            total_from_tv += _extract(df, "momentum_long", symbols)
            log.debug(f"TV scan 3 (momentum_long limit={long_limit}): {len(df)} rows")
        except Exception as e:
            log.warning(f"TV momentum_long scan failed: {e}")

    # ── 4. Momentum shorts — RSI declining, MACD negative ─────
    # Run at full size in BEAR, half in BULL, skip in CHOPPY/PANIC
    if not is_panic and not is_choppy:
        short_limit = 25 if is_bear else 12
        try:
            _, df = _query(
                extra_filters=[
                    col("RSI|60").between(32, 55),
                    col("MACD.macd|60") < col("MACD.signal|60"),
                    col("relative_volume_10d_calc") > 1.2,
                ],
                sort_by="relative_volume_10d_calc",
                ascending=False,
                limit=short_limit,
            )
            total_from_tv += _extract(df, "momentum_short", symbols)
            log.debug(f"TV scan 4 (momentum_short limit={short_limit}): {len(df)} rows")
        except Exception as e:
            log.warning(f"TV momentum_short scan failed: {e}")

    # ── 5. Gap & go — overnight catalyst plays ────────────────
    if not is_panic:
        try:
            _, df = _query(
                extra_filters=[
                    col("gap").between(3.0, 50.0),     # gapped up > 3 %
                    col("volume") > 1_000_000,
                ],
                sort_by="gap",
                ascending=False,
                limit=15,
            )
            total_from_tv += _extract(df, "gap_go", symbols)
            # Also grab gap-down plays (short candidates)
            _, df2 = _query(
                extra_filters=[
                    col("gap").between(-50.0, -3.0),   # gapped down > 3 %
                    col("volume") > 1_000_000,
                ],
                sort_by="gap",
                ascending=True,
                limit=10,
            )
            total_from_tv += _extract(df2, "gap_down", symbols)
            log.debug(f"TV scan 5 (gap plays): {len(df)+len(df2)} rows")
        except Exception as e:
            log.warning(f"TV gap scan failed: {e}")

    # ── 6. Pre-market movers ───────────────────────────────────
    if not is_panic:
        try:
            _, df = _query(
                extra_filters=[
                    col("premarket_change").between(3.0, 200.0),
                    col("premarket_volume") > 50_000,
                ],
                sort_by="premarket_change",
                ascending=False,
                limit=15,
            )
            total_from_tv += _extract(df, "premarket_movers", symbols)
            log.debug(f"TV scan 6 (premarket_movers): {len(df)} rows")
        except Exception as e:
            log.warning(f"TV premarket scan failed: {e}")

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


def get_market_regime(ib: IB) -> dict:
    """
    Classify current market regime using SPY, QQQ, and VIX.
    Returns regime dict used by all agents.
    """


    def _flat(df):
        """Flatten multi-level columns from newer yfinance."""
        if df is not None and hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df

    try:
        spy = _flat(_safe_download("SPY",  period="5d", interval="1h", progress=False, auto_adjust=True))
        qqq = _flat(_safe_download("QQQ",  period="5d", interval="1h", progress=False, auto_adjust=True))

        vix = None
        for vix_ticker in ["^VIX", "VIX", "VIXY"]:
            try:
                vix = _flat(_safe_download(vix_ticker, period="5d", interval="1h", progress=False, auto_adjust=True))
                if vix is not None and len(vix) > 0:
                    break
            except Exception:
                continue

        if vix is None or len(vix) == 0:
            vix = _flat(_safe_download("VIXY", period="5d", interval="1d", progress=False, auto_adjust=True))

        spy_close = spy["Close"].squeeze()
        qqq_close = qqq["Close"].squeeze()
        vix_close = vix["Close"].squeeze() if vix is not None and len(vix) > 0 else None

        if vix_close is None or len(vix_close) == 0:
            spy_returns = spy_close.pct_change().dropna()
            vix_now = float(spy_returns.std() * 100 * (252**0.5))
        else:
            vix_now = float(vix_close.iloc[-1])

        vix_prev      = float(vix_close.iloc[-2]) if vix_close is not None and len(vix_close) > 1 else vix_now
        vix_1h_change = (vix_now - vix_prev) / vix_prev if vix_prev > 0 else 0

        spy_ema        = float(spy_close.ewm(span=20, adjust=False).mean().iloc[-1])
        qqq_ema        = float(qqq_close.ewm(span=20, adjust=False).mean().iloc[-1])
        spy_trending_up = float(spy_close.iloc[-1]) > spy_ema
        qqq_trending_up = float(qqq_close.iloc[-1]) > qqq_ema

        if vix_now > CONFIG["vix_panic_min"] or vix_1h_change > CONFIG["vix_spike_pct"]:
            regime = "PANIC"
        elif vix_now < CONFIG["vix_bull_max"] and spy_trending_up and qqq_trending_up:
            regime = "BULL_TRENDING"
        elif vix_now > CONFIG["vix_choppy_max"] and not spy_trending_up and not qqq_trending_up:
            regime = "BEAR_TRENDING"
        elif vix_now < CONFIG["vix_choppy_max"] and not spy_trending_up:
            regime = "CHOPPY"
        else:
            regime = "CHOPPY"

        return {
            "regime":                   regime,
            "vix":                      round(vix_now, 2),
            "vix_1h_change":            round(vix_1h_change * 100, 2),
            "spy_price":                round(float(spy_close.iloc[-1]), 2),
            "spy_above_ema":            spy_trending_up,
            "qqq_price":                round(float(qqq_close.iloc[-1]), 2),
            "qqq_above_ema":            qqq_trending_up,
            "position_size_multiplier": _regime_size_mult(regime),
        }

    except Exception as e:
        log.error(f"Regime detection error: {e}")
        return {
            "regime": "UNKNOWN",
            "vix": 0,
            "vix_1h_change": 0,
            "spy_price": 0,
            "spy_above_ema": False,
            "qqq_price": 0,
            "qqq_above_ema": False,
            "position_size_multiplier": 0.5,
        }


def _regime_size_mult(regime: str) -> float:
    return {
        "BULL_TRENDING": 1.0,
        "BEAR_TRENDING": 1.0,
        "CHOPPY":        1.0,
        "PANIC":         0.0,
        "UNKNOWN":       0.75,
    }.get(regime, 0.75)
