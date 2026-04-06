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
            "tv_change_open":   row.get("change_from_open"),
            "tv_ema20":         row.get("EMA20"),
            "tv_ema50":         row.get("EMA50"),
            "tv_source":        source_name,
        }
    return added


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
    # Run at full size in BEAR, half in BULL/CHOPPY, skip in PANIC only
    if not is_panic:
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

    # ── 5. BREAKDOWN — price below key EMAs, bearish alignment ──
    # Runs in ALL non-PANIC regimes (including CHOPPY — this is the fix
    # for the structural bullish bias: agents need to SEE short candidates)
    if not is_panic:
        breakdown_limit = 25 if is_bear else (15 if is_choppy else 10)
        try:
            _, df = _query(
                extra_filters=[
                    col("close") < col("EMA20"),
                    col("close") < col("EMA50"),
                    col("change") < -1.0,            # Down at least 1% today
                    col("relative_volume_10d_calc") > 1.2,
                ],
                sort_by="change",
                ascending=True,                      # Biggest losers first
                limit=breakdown_limit,
            )
            total_from_tv += _extract(df, "breakdown", symbols)
            log.debug(f"TV scan 5a (breakdown limit={breakdown_limit}): {len(df)} rows")
        except Exception as e:
            log.warning(f"TV breakdown scan failed: {e}")

    # ── 6. VOLUME DISTRIBUTION — heavy selling on high volume ────
    if not is_panic:
        dist_limit = 20 if is_bear else 10
        try:
            _, df = _query(
                extra_filters=[
                    col("relative_volume_10d_calc") > 2.0,  # 2x avg volume
                    col("change") < -2.0,                   # Down > 2%
                ],
                sort_by="relative_volume_10d_calc",
                ascending=False,
                limit=dist_limit,
            )
            total_from_tv += _extract(df, "volume_distribution", symbols)
            log.debug(f"TV scan 6a (volume_distribution limit={dist_limit}): {len(df)} rows")
        except Exception as e:
            log.warning(f"TV volume_distribution scan failed: {e}")

    # ── 7. BEARISH MOMENTUM — RSI oversold + MACD bearish crossover ──
    if not is_panic:
        bear_mom_limit = 20 if is_bear else (12 if is_choppy else 8)
        try:
            _, df = _query(
                extra_filters=[
                    col("RSI|60") < 40,                    # 1h RSI below 40
                    col("MACD.macd|60") < col("MACD.signal|60"),  # MACD bearish
                    col("change") < -0.5,                  # Confirming price action
                    col("relative_volume_10d_calc") > 1.0,
                ],
                sort_by="RSI|60",
                ascending=True,                            # Most oversold first
                limit=bear_mom_limit,
            )
            total_from_tv += _extract(df, "bearish_momentum", symbols)
            log.debug(f"TV scan 7a (bearish_momentum limit={bear_mom_limit}): {len(df)} rows")
        except Exception as e:
            log.warning(f"TV bearish_momentum scan failed: {e}")

    # ── 8. INTRADAY BREAKDOWN — large drop from open ────────────
    if not is_panic:
        try:
            _, df = _query(
                extra_filters=[
                    col("change_from_open") < -3.0,        # Down > 3% from today's open
                    col("volume") > 500_000,
                ],
                sort_by="change_from_open",
                ascending=True,
                limit=15,
            )
            total_from_tv += _extract(df, "intraday_breakdown", symbols)
            log.debug(f"TV scan 8a (intraday_breakdown): {len(df)} rows")
        except Exception as e:
            log.warning(f"TV intraday_breakdown scan failed: {e}")

    # ── 9. Gap & go — overnight catalyst plays ────────────────
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


_last_good_regime: dict | None = None   # Cache last valid regime for bad-data fallback


def get_market_regime(ib: IB) -> dict:
    """
    Classify current market regime using SPY, QQQ, and VIX.
    Returns regime dict used by all agents.
    Includes sanity checks to reject corrupt/stale price data.
    """
    global _last_good_regime
    import sys as _sys; _sm = _sys.modules.get('scanner', _sys.modules[__name__])
    _safe_download = _sm._safe_download  # noqa: F841 — rebind so @patch('scanner._safe_download') takes effect

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
            spy_daily = _flat(_safe_download("SPY", period="1y", interval="1d",
                                             progress=False, auto_adjust=True))
            qqq_daily = _flat(_safe_download("QQQ", period="1y", interval="1d",
                                             progress=False, auto_adjust=True))
            if spy_daily is not None and len(spy_daily) >= 50:
                spy_d_close   = spy_daily["Close"].squeeze().dropna()
                spy_200d_ma   = float(spy_d_close.rolling(min(200, len(spy_d_close))).mean().iloc[-1])
                spy_above_200d = spy_price_now > spy_200d_ma
            else:
                # Fallback: short-term EMA on hourly bars
                spy_above_200d = spy_price_now > float(spy_close.ewm(span=20, adjust=False).mean().iloc[-1])
            if qqq_daily is not None and len(qqq_daily) >= 50:
                qqq_d_close   = qqq_daily["Close"].squeeze().dropna()
                qqq_200d_ma   = float(qqq_d_close.rolling(min(200, len(qqq_d_close))).mean().iloc[-1])
                qqq_above_200d = qqq_price_now > qqq_200d_ma
            else:
                qqq_above_200d = qqq_price_now > float(qqq_close.ewm(span=20, adjust=False).mean().iloc[-1])
        except Exception as _daily_err:
            log.warning(f"200d MA fetch failed ({_daily_err}) — falling back to 20h EMA")
            spy_above_200d = spy_price_now > float(spy_close.ewm(span=20, adjust=False).mean().iloc[-1])
            qqq_above_200d = qqq_price_now > float(qqq_close.ewm(span=20, adjust=False).mean().iloc[-1])

        # ── MARKET BREADTH (^MMTH: % of S&P 500 above their 200d MA) ────
        # Breadth confirms whether a trend has broad participation or is
        # driven by a handful of mega-caps. A SPY above its 200d MA with
        # MMTH < 40% is a narrow-leader rally, not a genuine bull regime.
        breadth_pct  = None
        breadth_cfg  = CONFIG.get("breadth_regime", {})
        if breadth_cfg.get("enabled", True):
            try:
                _bt = breadth_cfg.get("ticker", "^MMTH")
                _bd = _flat(_safe_download(_bt, period="5d", interval="1d",
                                           progress=False, auto_adjust=True))
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
