# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  signals.py                           ║
# ║   10-dimension alpha signal engine — genuine edge focused   ║
# ║                                                              ║
# ║   Architecture: ONE indicator per dimension.                 ║
# ║   No redundant oscillators. Every signal measures something  ║
# ║   different. Clean scores that differentiate, not confuse.   ║
# ║                                                              ║
# ║   Dimensions:                                                ║
# ║     1.  DIRECTIONAL   — EMA alignment × ADX + TF vote        ║
# ║     2.  MOMENTUM      — MFI (volume-weighted RSI)            ║
# ║     3.  SQUEEZE       — BB inside Keltner = coiled spring     ║
# ║     4.  FLOW          — VWAP position + OBV divergence        ║
# ║     5.  BREAKOUT      — Donchian channel breach + volume      ║
# ║     6.  PEAD          — Post-Earnings Announcement Drift      ║
# ║     7.  NEWS          — Yahoo RSS keyword + Claude sentiment  ║
# ║     8.  SHORT_SQUEEZE — High short float + volume surge       ║
# ║     9.  REVERSION     — Variance Ratio + OU half-life + z     ║
# ║     10. OVERNIGHT_DRIFT — 90-day close-to-open statistics     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import time as _time
from datetime import datetime, timezone
import zoneinfo as _zoneinfo
import yfinance as yf
import pandas as pd
import numpy as np
import logging
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import multiprocessing as _mp
try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False
try:
    from statsmodels.tsa.stattools import adfuller as _adfuller
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False
from config import CONFIG

# ── REGIME SIGNAL ROUTER ─────────────────────────────────────────────────────

def get_market_regime_vix() -> dict:
    """
    Fetch ^VIX and classify into the two-state signal-routing regime.

    LOW_VOL  (VIX < regime_router_vix_threshold)  → "momentum"
    HIGH_VOL (VIX >= regime_router_vix_threshold) → "mean_reversion"

    Returns {"regime": str, "vix": float|None, "source": str}
    """
    threshold = CONFIG.get("regime_router_vix_threshold", 20)
    try:
        raw = _safe_download("^VIX", period="2d", interval="1h", progress=False, auto_adjust=True)
        raw = _flatten_columns(raw)
        if raw is None or len(raw) == 0:
            log.warning("get_market_regime_vix: no VIX data — defaulting to momentum")
            return {"regime": "momentum", "vix": None, "source": "fallback"}
        vix_now = float(raw["Close"].iloc[-1])
        regime = "momentum" if vix_now < threshold else "mean_reversion"
        log.info(f"Regime router: {regime} (VIX={vix_now:.2f}, threshold={threshold})")
        return {"regime": regime, "vix": round(vix_now, 2), "source": "^VIX"}
    except Exception as e:
        log.warning(f"get_market_regime_vix: VIX fetch failed ({e}) — defaulting to momentum")
        return {"regime": "momentum", "vix": None, "source": "fallback"}


def _regime_multipliers(regime_router: str) -> dict:
    """
    Return per-dimension score multipliers for the routing regime.

    "momentum":       DIRECTIONAL/MOMENTUM/SQUEEZE/FLOW/BREAKOUT x 1.3, REVERSION x 0.7
    "mean_reversion": same dims x 0.7, REVERSION x 1.3
    "neutral":        all 1.0 — VIX and Hurst signals disagreed, no tilt warranted
    All other values (or regime_routing_enabled=False): all multipliers = 1.0

    NEWS and SOCIAL are regime-neutral (fundamental/event-driven).
    """
    _all_ones = {
        "trend":    1.0, "momentum": 1.0, "squeeze": 1.0,
        "flow":     1.0, "breakout": 1.0, "mtf":     1.0,
        "news":     1.0, "social":   1.0, "reversion": 1.0,
    }

    if not CONFIG.get("regime_routing_enabled", True):
        return _all_ones

    mom_up   = CONFIG.get("regime_router_momentum_mult",  1.3)
    rev_down = CONFIG.get("regime_router_reversion_mult", 0.7)

    if regime_router == "momentum":
        return {
            "trend":    mom_up,  "momentum": mom_up,  "squeeze": mom_up,
            "flow":     mom_up,  "breakout": mom_up,  "mtf":     mom_up,
            "news":     1.0,     "social":   1.0,     "reversion": rev_down,
        }
    if regime_router == "mean_reversion":
        return {
            "trend":    rev_down, "momentum": rev_down, "squeeze": rev_down,
            "flow":     rev_down, "breakout": rev_down, "mtf":     rev_down,
            "news":     1.0,      "social":   1.0,      "reversion": mom_up,
        }
    return _all_ones


# ── HURST DFA REGIME SIGNAL ──────────────────────────────────────────────────
# Hurst exponent of SPY (market-level) using Detrended Fluctuation Analysis.
# Used as a second input to the Layer 2 signal router alongside VIX.
# Ship disabled (hurst_regime.enabled = False); enable after historical validation.
# See chief-decifer/state/specs/spec-regime-architecture.md Step 2.

_hurst_spy_cache:    dict | None     = None
_hurst_spy_cache_ts: datetime | None = None


def compute_hurst_dfa(series) -> float:
    """
    Estimate the Hurst exponent using Detrended Fluctuation Analysis (DFA-1).

    DFA is substantially more reliable than R/S Hurst on short windows. Uses
    log returns internally: integrates mean-subtracted log returns into a
    profile, then measures how fluctuation F(n) scales with window size n.
    Slope of log F(n) vs log n gives H.

      H > 0.55 → persistent / trending series  (momentum edge)
      H < 0.45 → anti-persistent / mean-reverting (reversion edge)
      H ≈ 0.50 → random walk, no structural edge

    Returns 0.5 (neutral) if the series is too short (<20 pts) or errors.
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if len(arr) < 20:
        return 0.5
    try:
        # Step 1: log returns → profile (cumulative sum of mean-subtracted returns)
        log_ret = np.diff(np.log(arr))
        if len(log_ret) < 16:
            return 0.5
        profile = np.cumsum(log_ret - log_ret.mean())
        N = len(profile)

        # Step 2: log-spaced window sizes from 4 to N//4
        n_max = max(4, N // 4)
        scales = np.unique(
            np.round(np.logspace(np.log10(4), np.log10(n_max), 12)).astype(int)
        )
        scales = scales[(scales >= 4) & (scales <= n_max)]
        if len(scales) < 3:
            return 0.5

        # Step 3: for each window size, detrend segments and measure RMS fluctuation
        fluctuations = []
        valid_scales = []
        x_cache: dict = {}  # cache np.arange(n) for each n
        for n in scales:
            n_segs = N // n
            if n_segs < 2:
                continue
            if n not in x_cache:
                x_cache[n] = np.arange(n, dtype=float)
            x_t = x_cache[n]
            seg_var = []
            for i in range(n_segs):
                seg = profile[i * n: (i + 1) * n]
                coef = np.polyfit(x_t, seg, 1)
                residuals = seg - np.polyval(coef, x_t)
                seg_var.append(np.mean(residuals ** 2))
            if seg_var:
                fluctuations.append(np.sqrt(np.mean(seg_var)))
                valid_scales.append(int(n))

        if len(fluctuations) < 3:
            return 0.5

        # Step 4: fit log F(n) ~ H * log(n) → slope is H
        log_n = np.log(np.array(valid_scales, dtype=float))
        log_f = np.log(np.array(fluctuations, dtype=float))
        if not np.all(np.isfinite(log_f)):
            return 0.5
        h, _ = np.polyfit(log_n, log_f, 1)
        return float(np.clip(h, 0.0, 1.0))
    except Exception:
        return 0.5


def get_hurst_regime_spy() -> dict:
    """
    Compute the Hurst exponent of SPY daily closes and classify into a
    routing regime. Used as the second input to the Layer 2 signal router.

    Config block: config["hurst_regime"] — must have enabled=True to use.
    Cached for cache_ttl_seconds (default: 3600).

    Returns:
      {"regime": "trending"|"reverting"|"neutral"|"unknown",
       "hurst": float|None, "source": str, "lookback_days": int}

    Regimes:
      "trending"  H > trending_threshold (0.55) → momentum edge
      "reverting" H < reverting_threshold (0.45) → reversion edge
      "neutral"   between thresholds             → no structural edge
      "unknown"   data error / disabled          → safe fallback
    """
    global _hurst_spy_cache, _hurst_spy_cache_ts

    cfg      = CONFIG.get("hurst_regime", {})
    ttl      = cfg.get("cache_ttl_seconds", 3600)
    lookback = cfg.get("lookback_days", 63)
    hi_thr   = cfg.get("trending_threshold",  0.55)
    lo_thr   = cfg.get("reverting_threshold", 0.45)
    now      = datetime.now(timezone.utc)

    _et_tz        = _zoneinfo.ZoneInfo("America/New_York")
    _cache_day    = _hurst_spy_cache_ts.astimezone(_et_tz).date() if _hurst_spy_cache_ts else None
    _today_et     = now.astimezone(_et_tz).date()
    if (_hurst_spy_cache is not None and _hurst_spy_cache_ts is not None
            and (now - _hurst_spy_cache_ts).total_seconds() < ttl
            and _cache_day == _today_et):
        return _hurst_spy_cache

    try:
        raw = _safe_download("SPY", period=f"{lookback + 10}d", interval="1d",
                             progress=False, auto_adjust=True)
        raw = _flatten_columns(raw)
        if raw is None or len(raw) < 20:
            log.warning("get_hurst_regime_spy: insufficient SPY data — returning unknown")
            return {"regime": "unknown", "hurst": None, "source": "fallback",
                    "lookback_days": lookback}

        prices = raw["Close"].dropna().values[-lookback:]
        h = compute_hurst_dfa(prices)

        if h > hi_thr:
            regime = "trending"
        elif h < lo_thr:
            regime = "reverting"
        else:
            regime = "neutral"

        result = {"regime": regime, "hurst": round(h, 3),
                  "source": "SPY_DFA", "lookback_days": len(prices)}
        log.info(f"Hurst regime: {regime} (H={h:.3f}, lookback={len(prices)}d, "
                 f"trending>{hi_thr}, reverting<{lo_thr})")
        _hurst_spy_cache    = result
        _hurst_spy_cache_ts = now
        return result
    except Exception as e:
        log.warning(f"get_hurst_regime_spy: error ({e}) — returning unknown")
        return {"regime": "unknown", "hurst": None, "source": "fallback",
                "lookback_days": lookback}


def _resolve_regime_router(vix_regime: str, hurst_regime: str = "unknown") -> str:
    """
    Combine VIX and Hurst regime signals via consensus rule.

    Multipliers fire only when BOTH signals agree:
      vix=momentum        + hurst=trending  → "momentum"
      vix=mean_reversion  + hurst=reverting → "mean_reversion"
      any disagreement or hurst neutral     → "neutral" (all mults = 1.0)

    When hurst_regime is "unknown" (Hurst disabled or fetch failed),
    returns vix_regime unchanged — preserving the existing binary VIX-only
    behaviour with zero change to routing outcomes.
    """
    if hurst_regime == "unknown":
        return vix_regime
    if vix_regime == "momentum" and hurst_regime == "trending":
        return "momentum"
    if vix_regime == "mean_reversion" and hurst_regime == "reverting":
        return "mean_reversion"
    # Signals disagree, or hurst is "neutral" → equal-weight fallback
    return "neutral"


# ── PROCESS POOL for score_universe() ───────────────────────────
# yfinance.download() is NOT thread-safe (GitHub issue #2557): concurrent
# threads share a global dict (_DFS) causing cross-symbol data contamination.
# multiprocessing.Pool sidesteps this entirely — each worker is a separate
# process with its own memory space, so yfinance globals never collide.
# This cuts score_universe() from ~180-240s (sequential) to ~30-60s (parallel).
_SCORE_POOL = None
_SCORE_WORKERS = min(6, max(2, (_mp.cpu_count() or 4) - 1))


def _get_score_pool():
    """Lazily create a reusable process pool for scoring."""
    global _SCORE_POOL
    if _SCORE_POOL is None:
        _SCORE_POOL = ProcessPoolExecutor(max_workers=_SCORE_WORKERS)
    return _SCORE_POOL


def _fetch_one_process(args):
    """
    Top-level function for ProcessPoolExecutor (must be picklable).
    Each process gets its own yfinance globals — no contamination.
    """
    symbol, news_score, social_score, regime_router = args
    try:
        return fetch_multi_timeframe(symbol, news_score=news_score, social_score=social_score,
                                     regime_router=regime_router)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger("decifer.signals").debug(f"_fetch_one_process failed for {symbol}: {exc}")
        return None

log = logging.getLogger("decifer.signals")

# Suppress noisy yfinance warnings (ETF fundamentals 404s, Invalid Crumb 401s)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# ── Module-level caches for new alpha dimensions ─────────────────────────────
# These live in the worker process memory (multiprocessing). TTL is enforced by
# checking time.time() against the cache timestamp.
import time as _cache_time
_PEAD_CACHE: dict = {}          # symbol → (earnings_df, timestamp)
_SHORT_FLOAT_CACHE: dict = {}   # symbol → (short_float_pct, timestamp)
_PEAD_CACHE_TTL = 6 * 3600      # 6 hours (earnings data changes quarterly)
_SHORT_FLOAT_CACHE_TTL = 4 * 3600  # 4 hours (short float updates daily)


def _safe_download(symbol: str, **kwargs) -> pd.DataFrame | None:
    """Download with retry + session refresh on yfinance auth failures."""
    for attempt in range(3):
        try:
            df = yf.download(symbol, **kwargs)
            if df is not None and len(df) > 0:
                return df
        except Exception:
            pass
        # On retry: clear yfinance cache/session to fix Invalid Crumb errors
        if attempt < 2:
            try:
                yf.cache.clear()
            except Exception:
                pass
            _time.sleep(1)
    return None


def _flatten_columns(df):
    """Flatten multi-level columns from yfinance (e.g. ('Close','AAPL') → 'Close').
    Also deduplicates columns to prevent squeeze() returning DataFrames."""
    if df is not None and hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
        # Remove duplicate columns (keep first)
        df = df.loc[:, ~df.columns.duplicated()]
    return df


def fetch_multi_timeframe(symbol: str, news_score: int = 0, social_score: int = 0,
                          regime_router: str = "unknown") -> dict | None:
    """
    Fetch data across 3 timeframes for confluence scoring.
    Weekly → Daily → 5-minute
    Returns None if insufficient data.
    """
    try:
        # 5-minute (primary trading timeframe)
        df_5m = _flatten_columns(_safe_download(symbol, period="5d",  interval="5m",  progress=False, auto_adjust=True))
        # Daily (trend confirmation)
        df_1d = _flatten_columns(_safe_download(symbol, period="60d", interval="1d",  progress=False, auto_adjust=True))
        # Weekly (big picture)
        df_1w = _flatten_columns(_safe_download(symbol, period="1y",  interval="1wk", progress=False, auto_adjust=True))

        if df_5m is None or len(df_5m) < 30:
            return None
        if df_1d is None or len(df_1d) < 20:
            return None

        sig_5m = compute_indicators(df_5m, symbol, "5m")
        sig_1d = compute_indicators(df_1d, symbol, "1d")
        sig_1w = compute_indicators(df_1w, symbol, "1w") if df_1w is not None and len(df_1w) >= 10 else None

        if not sig_5m:
            return None

        # ── PRICE CROSS-VALIDATION — catch data contamination ──────
        # If daily price and 5m price differ by more than 50%, data is corrupt.
        # This catches yfinance returning wrong data (options premiums, adjusted errors, etc.)
        if sig_1d is not None:
            price_5m = sig_5m["price"]
            price_1d = sig_1d["price"]
            if price_1d > 0 and price_5m > 0:
                ratio = abs(price_5m - price_1d) / max(price_5m, price_1d)
                if ratio > 0.50:
                    log.warning(
                        f"DATA CONTAMINATION {symbol}: 5m=${price_5m:.2f} vs 1d=${price_1d:.2f} "
                        f"({ratio:.0%} divergence) — rejecting symbol"
                    )
                    return None

        # Multi-timeframe confluence score (with news + social as 7th/8th dimensions)
        confluence = compute_confluence(sig_5m, sig_1d, sig_1w,
                                        news_score=news_score, social_score=social_score,
                                        regime_router=regime_router)

        return {
            "symbol":       symbol,
            "price":        sig_5m["price"],
            "signal":       confluence["signal"],
            "direction":    confluence["direction"],
            "score":        confluence["score"],
            "timeframes":   {
                "5m":  sig_5m,
                "1d":  sig_1d,
                "1w":  sig_1w,
            },
            "atr":          sig_5m["atr"],
            "vol_ratio":    sig_5m["vol_ratio"],
            # MTF alignment gate results (for dashboard + logging)
            "mtf_gate":       confluence.get("mtf_gate", "PASS"),
            "mtf_conflict":   confluence.get("mtf_conflict", ""),
            "mtf_daily_trend": confluence.get("mtf_daily_trend", "N/A"),
            # Per-dimension score breakdown (for IC calculator + feedback loop)
            "score_breakdown":     confluence.get("score_breakdown", {}),
            "disabled_dimensions": confluence.get("disabled_dimensions", []),
            # Regime router state (for logging / dashboard)
            "regime_router":  regime_router,
        }

    except Exception as e:
        log.warning(f"Signal error {symbol}: {e}")
        return None


def compute_indicators(df: pd.DataFrame, symbol: str, tf: str) -> dict | None:
    """
    Compute the Decifer 2.0 indicator set — lean, non-redundant, alpha-focused.

    6 dimensions, each measuring something DIFFERENT:
      1. TREND:     EMA alignment (9/21/50) + ADX strength
      2. MOMENTUM:  MFI (volume-weighted RSI — strictly better than plain RSI)
      3. SQUEEZE:   Bollinger Band width vs Keltner Channel width
      4. FLOW:      VWAP position (intraday) + OBV slope (all timeframes)
      5. BREAKOUT:  Donchian Channel (high/low breakout detection)
      6. MACD:      Histogram acceleration (timing, not trend)
    """
    try:
        def _col(df, name, fallback=None):
            """Extract a column as a 1-D numeric Series, handling multi-index/dupes."""
            if name not in df.columns:
                return fallback
            col = df[name]
            if hasattr(col, 'columns'):  # Got DataFrame instead of Series (duplicate cols)
                col = col.iloc[:, 0]
            if hasattr(col, 'squeeze'):
                col = col.squeeze()
            # Ensure we have a proper 1-D numeric Series
            if isinstance(col, pd.DataFrame):
                col = col.iloc[:, 0]
            return pd.to_numeric(col, errors='coerce')

        close  = _col(df, "Close")
        volume = _col(df, "Volume", fallback=close * 0)
        high   = _col(df, "High",   fallback=close)
        low    = _col(df, "Low",    fallback=close)
        open_  = _col(df, "Open",   fallback=close)

        # Ensure all series are numeric and same length
        min_len = min(len(close), len(volume), len(high), len(low), len(open_))
        if min_len < 30:
            return None
        close  = close.iloc[-min_len:]
        volume = volume.iloc[-min_len:]
        high   = high.iloc[-min_len:]
        low    = low.iloc[-min_len:]
        open_  = open_.iloc[-min_len:]

        if len(close) < 30:
            return None

        # ── 1. TREND — EMA alignment ────────────────────────
        ema_fast  = close.ewm(span=CONFIG["ema_fast"],  adjust=False).mean()
        ema_slow  = close.ewm(span=CONFIG["ema_slow"],  adjust=False).mean()
        ema_trend = close.ewm(span=CONFIG["ema_trend"], adjust=False).mean()

        # Full trend alignment
        ef = float(ema_fast.iloc[-1])
        es = float(ema_slow.iloc[-1])
        et = float(ema_trend.iloc[-1])
        p  = float(close.iloc[-1])

        bull_aligned = ef > es > et
        bear_aligned = ef < es < et

        # ── 2. MOMENTUM — MFI + RSI slope ───────────────────
        # RSI (kept for slope calculation, but MFI is the primary momentum gauge)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(CONFIG["rsi_period"]).mean()
        loss  = (-delta.clip(upper=0)).rolling(CONFIG["rsi_period"]).mean()
        rsi   = 100 - (100 / (1 + gain / loss.replace(0, 1e-9)))
        rsi_val   = float(rsi.iloc[-1])
        rsi_slope = float(rsi.diff(3).iloc[-1])

        # ── 3. MACD — timing signal ─────────────────────────
        macd      = close.ewm(span=CONFIG["macd_fast"], adjust=False).mean() - \
                    close.ewm(span=CONFIG["macd_slow"], adjust=False).mean()
        macd_sig  = macd.ewm(span=CONFIG["macd_signal"], adjust=False).mean()
        macd_hist = macd - macd_sig
        macd_accel = float(macd_hist.diff(2).iloc[-1])

        # ── 4. ATR — volatility baseline ────────────────────
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(CONFIG["atr_period"]).mean().iloc[-1])

        # ── 5. VOLUME — ratio to 20-day average ────────────
        avg_vol = volume.rolling(20).mean()
        vol_ratio = float(volume.iloc[-1] / avg_vol.iloc[-1]) if avg_vol.iloc[-1] > 0 else 0

        # ── DEFAULTS for TA-Lib indicators ──────────────────
        adx_val = 0.0; trend_strength = "WEAK"
        mfi_val = 50.0; obv_slope = 0.0
        bb_upper = p; bb_lower = p; bb_mid = p; bb_width = 0.0; bb_pos = 0.5
        kc_upper = p; kc_lower = p; squeeze_on = False; squeeze_intensity = 0.0
        vwap_val = p; vwap_dist = 0.0
        donch_high = p; donch_low = p; donch_mid = p
        donch_breakout = 0  # +1 = high breakout, -1 = low breakout, 0 = inside
        candle_bull = 0; candle_bear = 0

        # ── TA-LIB INDICATORS (the ones that matter) ───────
        if TALIB_AVAILABLE and len(close) >= 30:
            try:
                c = close.values.astype(float)
                h = high.values.astype(float)
                l = low.values.astype(float)
                v = volume.values.astype(float)
                o = open_.values.astype(float)

                # ADX — trend strength (the gatekeeper)
                adx_arr = talib.ADX(h, l, c, timeperiod=14)
                adx_val = float(adx_arr[-1]) if not np.isnan(adx_arr[-1]) else 0.0
                trend_strength = "STRONG" if adx_val > 25 else "MODERATE" if adx_val > 20 else "WEAK"

                # MFI — volume-weighted RSI (replaces RSI, Stoch, Williams, CCI, UltOsc)
                if v.sum() > 0:
                    mfi_arr = talib.MFI(h, l, c, v, timeperiod=14)
                    mfi_val = float(mfi_arr[-1]) if not np.isnan(mfi_arr[-1]) else 50.0

                # OBV slope — volume confirming price direction
                if v.sum() > 0:
                    obv_arr = talib.OBV(c, v)
                    if len(obv_arr) >= 5:
                        obv_slope = float(obv_arr[-1] - obv_arr[-5])

                # Bollinger Bands — for squeeze detection
                upper, mid, lower = talib.BBANDS(c, timeperiod=20, nbdevup=2, nbdevdn=2)
                if not (np.isnan(upper[-1]) or np.isnan(lower[-1])):
                    bb_upper = float(upper[-1])
                    bb_lower = float(lower[-1])
                    bb_mid   = float(mid[-1])
                    bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
                    bb_pos   = (c[-1] - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5

                # Candlestick patterns — only the high-reliability ones
                patterns_bull = [
                    talib.CDLHAMMER(o, h, l, c),
                    talib.CDLMORNINGSTAR(o, h, l, c),
                    talib.CDLENGULFING(o, h, l, c),
                    talib.CDL3WHITESOLDIERS(o, h, l, c),
                ]
                patterns_bear = [
                    talib.CDLSHOOTINGSTAR(o, h, l, c),
                    talib.CDLEVENINGSTAR(o, h, l, c),
                    talib.CDLENGULFING(o, h, l, c),
                    talib.CDL3BLACKCROWS(o, h, l, c),
                ]
                candle_bull = sum(1 for pat in patterns_bull if pat[-1] > 0)
                candle_bear = sum(1 for pat in patterns_bear if pat[-1] < 0)

            except Exception as e:
                log.debug(f"TA-Lib partial error {symbol} {tf}: {e}")

        # ── KELTNER CHANNELS — for squeeze detection ────────
        # KC = EMA(20) ± ATR(10) × multiplier
        kc_mult = CONFIG.get("keltner_multiplier", 1.5)
        kc_period = CONFIG.get("keltner_period", 20)
        kc_atr_period = CONFIG.get("keltner_atr_period", 10)

        kc_ema = close.ewm(span=kc_period, adjust=False).mean()
        kc_atr = tr.rolling(kc_atr_period).mean()

        kc_upper = float(kc_ema.iloc[-1] + kc_mult * kc_atr.iloc[-1])
        kc_lower = float(kc_ema.iloc[-1] - kc_mult * kc_atr.iloc[-1])

        # SQUEEZE: BB inside KC = volatility compressed = spring loaded
        squeeze_on = (bb_lower > kc_lower) and (bb_upper < kc_upper)

        # Squeeze intensity: how tight the squeeze is (0 = loose, 1 = max compression)
        kc_width = kc_upper - kc_lower
        if kc_width > 0 and bb_width > 0:
            squeeze_intensity = max(0.0, 1.0 - (bb_upper - bb_lower) / kc_width)
        else:
            squeeze_intensity = 0.0

        # ── VWAP — institutional anchor (intraday only) ─────
        # VWAP = cumulative(price × volume) / cumulative(volume)
        if tf == "5m" and volume.sum() > 0:
            typical_price = (high + low + close) / 3
            cum_tp_vol = (typical_price * volume).cumsum()
            cum_vol = volume.cumsum()
            vwap_series = cum_tp_vol / cum_vol.replace(0, 1e-9)
            vwap_val = float(vwap_series.iloc[-1])
            # Distance from VWAP as % of price — positive = above VWAP (bullish)
            vwap_dist = ((p - vwap_val) / vwap_val) * 100 if vwap_val > 0 else 0.0
        else:
            vwap_val = p
            vwap_dist = 0.0

        # ── DONCHIAN CHANNELS — breakout detection ──────────
        donch_period = CONFIG.get("donchian_period", 20)
        if len(high) >= donch_period:
            donch_high = float(high.rolling(donch_period).max().iloc[-1])
            donch_low  = float(low.rolling(donch_period).min().iloc[-1])
            donch_mid  = (donch_high + donch_low) / 2

            # Breakout detection: price closing above/below channel
            if p >= donch_high:
                donch_breakout = 1   # Bullish breakout
            elif p <= donch_low:
                donch_breakout = -1  # Bearish breakout
            else:
                donch_breakout = 0   # Inside channel

        # ── MEAN-REVERSION METRICS ─────────────────────────
        # Three sub-metrics for the REVERSION dimension:
        #   1. Variance Ratio — is this series trending or mean-reverting?
        #   2. OU half-life — how fast does it revert?
        #   3. Z-score — how far is price from its mean (and which direction)?
        #
        # These use daily data when available (more stable), falling back to
        # whatever timeframe we're computing on. We need 40+ bars minimum.

        vr_val = 1.0         # Default: random walk (no edge)
        ou_halflife = 999.0   # Default: no reversion detected
        zscore_val = 0.0      # Default: at the mean

        # Use the close series we already have (could be 5m, 1d, or 1w)
        _rev_series = close.dropna()
        _rev_len = len(_rev_series)

        # 1. VARIANCE RATIO (Lo-MacKinlay, k=5)
        #    VR < 1 = mean-reverting (returns reverse), VR > 1 = trending (returns persist)
        #    Calibrated via Monte Carlo on 60-bar windows:
        #      Random walk median = 0.905, std = 0.279
        #      OU theta=0.2: 95th pct = 1.026 (strong MR never exceeds this)
        #      Trending AR=0.3: 5th pct = 0.871 (mild trend rarely below this)
        #    Thresholds set conservatively to avoid false positives.
        if _rev_len >= 20:
            try:
                _prices = _rev_series.values.astype(float)
                _prices = _prices[_prices > 0]
                _k = 5
                if len(_prices) >= _k + 10:
                    _log_p = np.log(_prices)
                    _ret_1 = np.diff(_log_p)
                    _ret_k = _log_p[_k:] - _log_p[:-_k]
                    _var_1 = np.var(_ret_1, ddof=1)
                    _var_k = np.var(_ret_k, ddof=1)
                    if _var_1 > 1e-12:
                        vr_val = float(_var_k / (_k * _var_1))
                        vr_val = max(0.01, min(vr_val, 10.0))  # Clip extremes
            except Exception:
                vr_val = 1.0  # Fall back to random walk

        # 2. ORNSTEIN-UHLENBECK HALF-LIFE (Ernie Chan method)
        #    Regress y(t)-y(t-1) against y(t-1). Half-life = -ln(2)/slope
        if _rev_len >= 40:
            try:
                _y = _rev_series.values.astype(float)
                _y_lag = _y[:-1]
                _y_diff = np.diff(_y)
                # OLS: y_diff = alpha + beta * y_lag
                # beta < 0 indicates mean reversion
                _X = np.column_stack([np.ones(len(_y_lag)), _y_lag])
                _beta = np.linalg.lstsq(_X, _y_diff, rcond=None)[0]
                if _beta[1] < -1e-8:  # Negative slope = mean-reverting
                    ou_halflife = float(-np.log(2) / _beta[1])
                    ou_halflife = max(0.5, min(ou_halflife, 999.0))
                else:
                    ou_halflife = 999.0  # Not mean-reverting
            except Exception:
                ou_halflife = 999.0

        # 3. Z-SCORE of price vs 20-period SMA
        #    Positive z = price above mean (SHORT bias for reversion)
        #    Negative z = price below mean (LONG bias for reversion)
        if _rev_len >= 20:
            try:
                _sma20 = float(_rev_series.rolling(20).mean().iloc[-1])
                _std20 = float(_rev_series.rolling(20).std().iloc[-1])
                if _std20 > 1e-8:
                    zscore_val = float((p - _sma20) / _std20)
                    zscore_val = max(-5.0, min(zscore_val, 5.0))  # Clip extremes
            except Exception:
                zscore_val = 0.0

        # 4. ADF TEST — statistical gatekeeper for mean-reversion
        #    p < 0.05 = reject random walk hypothesis (series is stationary/mean-reverting)
        #    This is the ONLY gate that controls whether REVERSION dimension scores.
        #    VR and OU are noisy on 60-bar windows; ADF provides calibrated p-values.
        #    Monte Carlo validated: ~7.7% FP rate on random walks, 75% TP on strong OU.
        adf_pvalue = 1.0  # Default: fail to reject (not mean-reverting)
        if STATSMODELS_AVAILABLE and _rev_len >= 20:
            try:
                _adf_result = _adfuller(_rev_series.values, maxlag=5, autolag='AIC')
                adf_pvalue = float(_adf_result[1])
            except Exception:
                adf_pvalue = 1.0

        # ── OVERNIGHT DRIFT STATS (1d timeframe only) ────────
        # Computed here from raw close/open series already in memory.
        # Stored in sig_1d and consumed by score_overnight_drift() in
        # compute_confluence(). No extra API calls required.
        overnight_mean_return = 0.0
        overnight_sharpe = 0.0
        overnight_n_days = 0
        if tf == "1d" and len(close) >= 30:
            try:
                _o = open_.values.astype(float)
                _c = close.values.astype(float)
                # overnight[t] = open[t] / close[t-1] - 1
                _denom = np.where(_c[:-1] > 0, _c[:-1], 1e-9)
                _ov_ret = (_o[1:] / _denom) - 1
                _ov_ret = _ov_ret[-90:]  # Last 90 days max
                if len(_ov_ret) >= 20:
                    overnight_mean_return = float(np.mean(_ov_ret))
                    _ov_std = float(np.std(_ov_ret, ddof=1))
                    overnight_n_days = len(_ov_ret)
                    if _ov_std > 1e-8:
                        overnight_sharpe = float(overnight_mean_return / _ov_std * np.sqrt(252))
            except Exception:
                overnight_mean_return = 0.0

        # ── SIGNAL CLASSIFICATION ────────────────────────────
        h_val = float(macd_hist.iloc[-1])

        if bull_aligned and mfi_val > 55 and h_val > 0 and macd_accel > 0 and vol_ratio >= CONFIG["volume_surge_multiplier"]:
            signal = "STRONG_BUY"
        elif bull_aligned and mfi_val > 50 and h_val > 0:
            signal = "BUY"
        elif bull_aligned and mfi_val > 45:
            signal = "WEAK_BUY"
        elif bear_aligned and mfi_val < 45 and h_val < 0 and macd_accel < 0 and vol_ratio >= CONFIG["volume_surge_multiplier"]:
            signal = "STRONG_SELL"
        elif bear_aligned and mfi_val < 50 and h_val < 0:
            signal = "SELL"
        elif bear_aligned and mfi_val < 55:
            signal = "WEAK_SELL"
        # Squeeze breakout signals — fire even without full EMA alignment
        elif squeeze_on and donch_breakout == 1 and vol_ratio >= 1.2:
            signal = "BUY"
        elif squeeze_on and donch_breakout == -1 and vol_ratio >= 1.2:
            signal = "SELL"
        else:
            signal = "HOLD"

        return {
            "symbol":           symbol,
            "timeframe":        tf,
            "price":            round(p, 4),
            # Trend
            "ema_fast":         round(ef, 4),
            "ema_slow":         round(es, 4),
            "ema_trend":        round(et, 4),
            "bull_aligned":     bull_aligned,
            "bear_aligned":     bear_aligned,
            "adx":              round(adx_val, 1),
            "trend_strength":   trend_strength,
            # Momentum
            "mfi":              round(mfi_val, 1),
            "rsi":              round(rsi_val, 2),
            "rsi_slope":        round(rsi_slope, 2),
            # Timing
            "macd_hist":        round(h_val, 6),
            "macd_accel":       round(macd_accel, 6),
            # Volatility
            "atr":              round(atr, 4),
            "vol_ratio":        round(vol_ratio, 2),
            # Squeeze
            "bb_position":      round(bb_pos, 2),
            "bb_width":         round(bb_width, 4),
            "squeeze_on":       squeeze_on,
            "squeeze_intensity": round(squeeze_intensity, 2),
            # Flow
            "vwap":             round(vwap_val, 4),
            "vwap_dist":        round(vwap_dist, 2),
            "obv_slope":        round(obv_slope, 0),
            # Breakout
            "donch_high":       round(donch_high, 4),
            "donch_low":        round(donch_low, 4),
            "donch_breakout":   donch_breakout,
            # Candlestick (high-reliability only)
            "candle_bull":      candle_bull,
            "candle_bear":      candle_bear,
            # Mean Reversion
            "variance_ratio":   round(vr_val, 3),
            "ou_halflife":      round(ou_halflife, 1),
            "zscore":           round(zscore_val, 2),
            "adf_pvalue":       round(adf_pvalue, 4),
            # Overnight drift (populated for 1d timeframe only; 0 for others)
            "overnight_mean":   round(overnight_mean_return, 6),
            "overnight_sharpe": round(overnight_sharpe, 3),
            "overnight_n_days": overnight_n_days,
            # Signal
            "signal":           signal,
        }

    except Exception as e:
        log.warning(f"Indicator compute error {symbol} {tf}: {e}")
        return None


def timeframe_alignment_check(sig_5m: dict, sig_1d: dict | None, sig_1w: dict | None) -> dict:
    """
    Multi-Timeframe Alignment Gate — checks whether higher timeframes
    support the 5m signal direction.

    Returns:
        {
            "aligned":          bool,   # True if higher TFs support 5m direction
            "daily_trend":      str,    # "BULL" | "BEAR" | "NEUTRAL"
            "weekly_trend":     str,    # "BULL" | "BEAR" | "NEUTRAL" | "N/A"
            "daily_confirms":   bool,   # Daily agrees with 5m direction
            "weekly_confirms":  bool,   # Weekly agrees with 5m direction
            "gate_applies":     bool,   # Whether the gate should fire (daily ADX strong enough)
            "conflict":         str,    # Human-readable conflict description
        }

    Gate logic:
        - If 5m says BUY but daily trend is bearish → conflict
        - If 5m says SELL but daily trend is bullish → conflict
        - Daily ADX must exceed mtf_adx_min_for_gate for the gate to apply
          (weak/trendless daily data shouldn't block trades)
        - Weekly is optional (mtf_require_weekly config flag)
    """
    result = {
        "aligned": True,
        "daily_trend": "NEUTRAL",
        "weekly_trend": "N/A",
        "daily_confirms": True,
        "weekly_confirms": True,
        "gate_applies": False,
        "conflict": "",
    }

    if sig_1d is None:
        return result  # No daily data → can't gate, allow trade

    # ── Determine 5m direction ──────────────────────────────────
    sig_5m_signal = sig_5m.get("signal", "HOLD")
    if "BUY" in sig_5m_signal:
        direction_5m = "BULL"
    elif "SELL" in sig_5m_signal:
        direction_5m = "BEAR"
    else:
        return result  # HOLD signal → no entry to gate

    # ── Determine daily trend ───────────────────────────────────
    # Uses EMA alignment (same logic as compute_indicators) + MACD direction
    daily_bull = sig_1d.get("bull_aligned", False)
    daily_bear = sig_1d.get("bear_aligned", False)
    daily_adx = sig_1d.get("adx", 0)
    daily_macd = sig_1d.get("macd_hist", 0)

    # Composite daily trend: EMA alignment is primary, MACD confirms
    if daily_bull:
        result["daily_trend"] = "BULL"
    elif daily_bear:
        result["daily_trend"] = "BEAR"
    else:
        # No EMA alignment — use MACD as tiebreaker
        if daily_macd > 0:
            result["daily_trend"] = "LEAN_BULL"
        elif daily_macd < 0:
            result["daily_trend"] = "LEAN_BEAR"
        else:
            result["daily_trend"] = "NEUTRAL"

    # ── Should the gate fire? ───────────────────────────────────
    adx_min = CONFIG.get("mtf_adx_min_for_gate", 20)
    if daily_adx >= adx_min:
        result["gate_applies"] = True

    # ── Check daily confirmation ────────────────────────────────
    if result["gate_applies"]:
        if direction_5m == "BULL" and result["daily_trend"] in ("BEAR",):
            result["daily_confirms"] = False
            result["conflict"] = (
                f"5m={sig_5m_signal} but daily trend BEARISH "
                f"(EMA: {daily_bear}, ADX: {daily_adx:.0f}, MACD: {daily_macd:.4f})"
            )
        elif direction_5m == "BEAR" and result["daily_trend"] in ("BULL",):
            result["daily_confirms"] = False
            result["conflict"] = (
                f"5m={sig_5m_signal} but daily trend BULLISH "
                f"(EMA: {daily_bull}, ADX: {daily_adx:.0f}, MACD: {daily_macd:.4f})"
            )
        # Note: LEAN_BULL/LEAN_BEAR and NEUTRAL don't trigger the gate —
        # only clear EMA-aligned trends block opposing entries.

    # ── Check weekly confirmation (optional) ────────────────────
    if sig_1w is not None and CONFIG.get("mtf_require_weekly", False):
        weekly_bull = sig_1w.get("bull_aligned", False)
        weekly_bear = sig_1w.get("bear_aligned", False)

        if weekly_bull:
            result["weekly_trend"] = "BULL"
        elif weekly_bear:
            result["weekly_trend"] = "BEAR"
        else:
            result["weekly_trend"] = "NEUTRAL"

        if direction_5m == "BULL" and weekly_bear:
            result["weekly_confirms"] = False
            if not result["conflict"]:
                result["conflict"] = f"5m={sig_5m_signal} but weekly trend BEARISH"
        elif direction_5m == "BEAR" and weekly_bull:
            result["weekly_confirms"] = False
            if not result["conflict"]:
                result["conflict"] = f"5m={sig_5m_signal} but weekly trend BULLISH"

    # ── Final alignment verdict ─────────────────────────────────
    result["aligned"] = result["daily_confirms"] and result["weekly_confirms"]

    return result


# ══════════════════════════════════════════════════════════════════════════════
# NEW ALPHA DIMENSION SCORING FUNCTIONS
# Each returns (score: int 0-10, direction: int +1/-1/0)
# ══════════════════════════════════════════════════════════════════════════════

def score_directional(sig_5m: dict, sig_1d: dict | None, sig_1w: dict | None) -> tuple:
    """
    DIRECTIONAL — replaces the old separate TREND + MTF dimensions.

    Merges EMA alignment quality (ADX-gated) with multi-timeframe consensus
    into a single dimension, eliminating correlated IC weight splitting.

    Sub-components:
      A. EMA alignment × ADX gate  (0-5 pts)
      B. MACD acceleration          (0-2 pts)
      C. Timeframe agreement vote   (0-3 pts)
    Max = 10.
    Direction = majority vote across timeframes, EMA tiebreak.
    """
    # ── Component A: EMA alignment quality × ADX ──────────────────
    adx = sig_5m.get("adx", 0)
    adx_mult = 1.25 if adx > 25 else 1.0 if adx > 20 else 0.7

    bull = sig_5m.get("bull_aligned", False)
    bear = sig_5m.get("bear_aligned", False)
    sig_str = sig_5m.get("signal", "HOLD")

    if bull or bear:
        base = 4
    elif "BUY" in sig_str or "SELL" in sig_str:
        base = 2
    else:
        base = 0

    a_pts = min(5, int(round(base * adx_mult)))

    # ── Component B: MACD acceleration ────────────────────────────
    macd_accel = sig_5m.get("macd_accel", 0)
    dir_guess = +1 if (bull or "BUY" in sig_str) else (-1 if (bear or "SELL" in sig_str) else 0)

    if dir_guess == +1:
        b_pts = 2 if macd_accel > 0 else (1 if macd_accel > -0.001 else 0)
    elif dir_guess == -1:
        b_pts = 2 if macd_accel < 0 else (1 if macd_accel < 0.001 else 0)
    else:
        b_pts = 0

    # ── Component C: Timeframe agreement vote ─────────────────────
    tfs = [sig_5m]
    if sig_1d: tfs.append(sig_1d)
    if sig_1w: tfs.append(sig_1w)
    total_tfs = len(tfs)

    buys  = sum(1 for s in tfs if "BUY"  in s.get("signal", ""))
    sells = sum(1 for s in tfs if "SELL" in s.get("signal", ""))
    agree = max(buys, sells)
    agree_ratio = agree / total_tfs if total_tfs > 0 else 0

    c_pts = 3 if agree_ratio >= 1.0 else (2 if agree_ratio >= 0.67 else (1 if agree_ratio >= 0.5 else 0))

    score = min(10, a_pts + b_pts + c_pts)

    # Direction: majority timeframe vote, tiebreak from EMA
    if buys > sells:
        direction = +1
    elif sells > buys:
        direction = -1
    elif bull:
        direction = +1
    elif bear:
        direction = -1
    else:
        direction = 0

    return (score, direction)


def score_pead(symbol: str, sig_1d: dict | None, vol_ratio: float = 0.0) -> tuple:
    """
    PEAD — Post-Earnings Announcement Drift.

    One of the most documented behavioral finance anomalies: analysts
    systematically underreact to earnings surprises, causing drift that
    continues 20-60 days post-announcement.

    Score formula:
      surprise_tier (0-6 pts) x recency_decay (linear 0-1)
      + price_momentum_pts (0-2 pts)
      + volume_confirmation (0-2 pts)

    Direction: LONG only (anomaly is reliably long-side; short-side PEAD
    requires higher conviction and separate validation).
    """
    try:
        now = _cache_time.time()
        if symbol in _PEAD_CACHE:
            cached_df, cached_ts = _PEAD_CACHE[symbol]
            if now - cached_ts < _PEAD_CACHE_TTL:
                earnings_df = cached_df
            else:
                earnings_df = None
        else:
            earnings_df = None

        if earnings_df is None:
            try:
                ticker = yf.Ticker(symbol)
                earnings_df = ticker.get_earnings_dates(limit=8)
                _PEAD_CACHE[symbol] = (earnings_df, now)
            except Exception:
                return (0, 0)

        if earnings_df is None or len(earnings_df) == 0:
            return (0, 0)

        # Find most recent past earnings with a known surprise
        today = pd.Timestamp.now(tz="UTC").normalize()
        surprise_col = None
        for col in earnings_df.columns:
            if "surprise" in col.lower() or "Surprise" in col:
                surprise_col = col
                break
        if surprise_col is None:
            return (0, 0)

        past = earnings_df[earnings_df.index <= today].dropna(subset=[surprise_col])
        if len(past) == 0:
            return (0, 0)

        latest = past.iloc[0]  # Most recent (index is sorted descending)
        surprise_pct = float(latest[surprise_col])

        if surprise_pct < 3.0:  # Below noise threshold
            return (0, 0)

        # Recency decay (linear: 1.0 at day 0 → 0.0 at day 60)
        earnings_date = latest.name
        if hasattr(earnings_date, 'tz_localize') and earnings_date.tzinfo is None:
            earnings_date = earnings_date.tz_localize("UTC")
        days_since = (today - earnings_date).days
        if days_since > 60 or days_since < 0:
            return (0, 0)
        decay = max(0.0, 1.0 - (days_since / 60.0))

        # Surprise tier (0-6 pts)
        if surprise_pct >= 20:   surprise_pts = 6
        elif surprise_pct >= 10: surprise_pts = 5
        elif surprise_pct >= 7:  surprise_pts = 4
        elif surprise_pct >= 5:  surprise_pts = 3
        else:                    surprise_pts = 2  # >= 3%

        # Price momentum confirmation (0-2 pts) — is drift actually happening?
        mom_pts = 0
        if sig_1d is not None:
            # bull_aligned = price is above key EMAs = drift in progress
            if sig_1d.get("bull_aligned", False):
                mom_pts = 2
            elif "BUY" in sig_1d.get("signal", ""):
                mom_pts = 1

        # Volume confirmation (0-2 pts)
        vol_pts = 2 if vol_ratio >= 2.0 else (1 if vol_ratio >= 1.5 else 0)

        raw = (surprise_pts * decay) + mom_pts + vol_pts
        score = int(round(min(10, raw)))
        direction = +1 if score > 0 else 0

        return (score, direction)

    except Exception:
        return (0, 0)


def _fetch_short_float(symbol: str) -> float | None:
    """
    Scrape short float % from finviz.com/quote.ashx?t=SYMBOL.
    Returns float (e.g. 15.2 for 15.2%) or None on failure.
    Cached per symbol with 4-hour TTL.
    """
    import requests as _requests

    now = _cache_time.time()
    if symbol in _SHORT_FLOAT_CACHE:
        cached_val, cached_ts = _SHORT_FLOAT_CACHE[symbol]
        if now - cached_ts < _SHORT_FLOAT_CACHE_TTL:
            return cached_val

    try:
        url = f"https://finviz.com/quote.ashx?t={symbol}"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Decifer/1.0)"}
        resp = _requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return None

        html = resp.text
        # Find "Short Float" label in fundamentals table
        idx = html.find("Short Float")
        if idx == -1:
            return None
        # The value is in the next <td> tag
        td_start = html.find("<td", idx + 1)
        td_content_start = html.find(">", td_start) + 1
        td_content_end = html.find("<", td_content_start)
        raw_val = html[td_content_start:td_content_end].strip().rstrip("%")
        if raw_val in ("", "-", "N/A"):
            return None
        val = float(raw_val)
        _SHORT_FLOAT_CACHE[symbol] = (val, now)
        return val
    except Exception:
        return None


def score_short_squeeze(symbol: str, sig_5m: dict) -> tuple:
    """
    SHORT_SQUEEZE — High short float + volume surge + price vs resistance.

    Shorts forced to cover when: high short interest + volume surge drives
    price above their stop levels (Donchian resistance). Asymmetric upside.

    Score components:
      A. Short float tier       (0-4 pts)
      B. Volume surge           (0-3 pts)
      C. Price vs Donchian high (0-3 pts)
    Direction: LONG only.
    """
    short_float = _fetch_short_float(symbol)
    if short_float is None:
        return (0, 0)

    # Component A: Short float tier (0-4 pts)
    if short_float >= 30:   sf_pts = 4
    elif short_float >= 20: sf_pts = 3
    elif short_float >= 15: sf_pts = 2
    elif short_float >= 10: sf_pts = 1
    else:
        return (0, 0)   # Below 10% — squeeze unlikely

    # Component B: Volume surge (0-3 pts)
    vol_ratio = sig_5m.get("vol_ratio", 0)
    vol_pts = 3 if vol_ratio >= 3.0 else (2 if vol_ratio >= 2.0 else (1 if vol_ratio >= 1.5 else 0))

    # Component C: Price vs Donchian high (0-3 pts)
    donch_high = sig_5m.get("donch_high", 0)
    price = sig_5m.get("price", 0)
    if donch_high > 0 and price > 0:
        pct = (price - donch_high) / donch_high
        resist_pts = 3 if pct >= 0 else (2 if pct >= -0.02 else (1 if pct >= -0.05 else 0))
    else:
        resist_pts = 0

    raw = sf_pts + vol_pts + resist_pts
    score = min(10, raw)
    direction = +1 if score > 0 else 0
    return (score, direction)


def score_overnight_drift(sig_1d: dict | None) -> tuple:
    """
    OVERNIGHT_DRIFT — 90-day close-to-open return statistics.

    One of the most persistent market anomalies: equity risk premium
    accrues disproportionately overnight. Per-symbol stats are computed
    inside compute_indicators() for the 1d timeframe and stored in sig_1d.

    Score = mean_overnight_return_tier x Sharpe_consistency_multiplier x 2
    Direction: +1 if positive drift, -1 if negative drift.
    """
    if sig_1d is None:
        return (0, 0)

    mean_ov   = sig_1d.get("overnight_mean", 0.0)
    sharpe    = sig_1d.get("overnight_sharpe", 0.0)
    n_days    = sig_1d.get("overnight_n_days", 0)

    if n_days < 20:
        return (0, 0)

    abs_mean = abs(mean_ov)

    # Mean return tier (0-5 pts)
    if abs_mean >= 0.0015:  mean_pts = 5
    elif abs_mean >= 0.0010: mean_pts = 4
    elif abs_mean >= 0.0006: mean_pts = 3
    elif abs_mean >= 0.0003: mean_pts = 2
    elif abs_mean >= 0.0001: mean_pts = 1
    else:
        return (0, 0)   # No discernible edge

    # Sharpe consistency multiplier
    abs_sharpe = abs(sharpe)
    sharpe_mult = 1.0 if abs_sharpe >= 1.5 else (0.8 if abs_sharpe >= 1.0 else
                  (0.5 if abs_sharpe >= 0.5 else 0.2))

    raw = mean_pts * sharpe_mult * 2
    score = int(round(min(10, raw)))
    direction = +1 if mean_ov > 0 else (-1 if mean_ov < -0.0001 else 0)
    return (score, direction)


# ══════════════════════════════════════════════════════════════════════════════


def compute_confluence(sig_5m: dict, sig_1d: dict | None, sig_1w: dict | None,
                       news_score: int = 0, social_score: int = 0,
                       regime_router: str = "unknown") -> dict:
    """
    Decifer 2.0 — 10-dimension scoring engine (alpha-pipeline-v2).

    Each dimension scores 0-10, total max 100, capped at 50.
    Bonus points for candlestick confirmation.

    Multi-Timeframe Alignment Gate (NEW):
      Before scoring, checks if daily/weekly trends support the 5m direction.
      - "hard" mode: returns score=0 + HOLD signal if misaligned
      - "soft" mode: deducts mtf_penalty_points from final score
      - "off" mode:  legacy behaviour (Dimension 6 only)

    Dimensions (alpha-pipeline-v2):
      1.  DIRECTIONAL (0-10)    — EMA alignment × ADX + timeframe vote
      2.  MOMENTUM (0-10)       — MFI + RSI slope
      3.  SQUEEZE (0-10)        — BB/Keltner compression → breakout potential
      4.  FLOW (0-10)           — VWAP position + OBV confirmation
      5.  BREAKOUT (0-10)       — Donchian channel breach + volume
      6.  PEAD (0-10)           — Post-Earnings Announcement Drift
      7.  NEWS (0-10)           — Yahoo RSS keyword + Claude sentiment
      8.  SHORT_SQUEEZE (0-10)  — High short float + volume surge
      9.  REVERSION (0-10)      — Variance Ratio + OU half-life + z-score
      10. OVERNIGHT_DRIFT (0-10)— 90-day close-to-open statistics
    """
    # ── MULTI-TIMEFRAME ALIGNMENT GATE ─────────────────────────
    # Run alignment check BEFORE scoring to short-circuit on hard gate
    gate_mode = CONFIG.get("mtf_gate_mode", "off")
    mtf_alignment = timeframe_alignment_check(sig_5m, sig_1d, sig_1w)

    if gate_mode == "hard" and not mtf_alignment["aligned"] and mtf_alignment["gate_applies"]:
        # Hard gate: block the trade entirely — return zero score + HOLD
        log.info(
            f"MTF GATE BLOCKED {sig_5m.get('symbol','?')}: {mtf_alignment['conflict']}"
        )
        return {
            "signal":     "HOLD",
            "direction":  "NEUTRAL",
            "score":      0,
            "buy_count":  0,
            "sell_count": 0,
            "tf_count":   1,
            "mtf_gate":   "BLOCKED",
            "mtf_conflict": mtf_alignment["conflict"],
            "candle_gate": "SKIPPED",
            "reversion_score": 0,
            "variance_ratio": 0,
            "ou_halflife": 0,
            "zscore": 0,
            "adf_pvalue": 1.0,
            "score_breakdown": {
                "trend": 0, "momentum": 0, "squeeze": 0, "flow": 0,
                "breakout": 0, "mtf": 0, "news": 0, "social": 0, "reversion": 0,
            },
            "disabled_dimensions": [],
        }

    signals = [sig_5m["signal"]]
    if sig_1d: signals.append(sig_1d["signal"])
    if sig_1w: signals.append(sig_1w["signal"])
    total_tf = len(signals)

    buy_signals  = sum(1 for s in signals if "BUY"  in s)
    sell_signals = sum(1 for s in signals if "SELL" in s)
    strong_buy   = sum(1 for s in signals if s == "STRONG_BUY")
    strong_sell  = sum(1 for s in signals if s == "STRONG_SELL")

    score = 0

    # ════════════════════════════════════════════════════════════════
    # DIRECTION-AGNOSTIC SCORING (Roadmap #01)
    #
    # Each dimension scores QUALITY of setup (0-10) independently of
    # direction. A clean bearish breakdown scores the same as the
    # equivalent bullish setup. Direction is tracked separately via
    # dim_directions[] and resolved by weighted majority vote at the end.
    #
    # dim_directions: list of (direction, weight) tuples
    #   direction: +1 = long, -1 = short, 0 = neutral
    #   weight: the dimension's score (higher score = more influence)
    # ════════════════════════════════════════════════════════════════
    dim_directions = []  # [(direction, weight), ...]
    disabled_dimensions = []  # track which flags were off, for diagnostics

    # ── Dimension flags — read once, guard each section ───────────
    _flags = CONFIG.get("dimension_flags", {})
    def _enabled(name: str) -> bool:
        on = bool(_flags.get(name, True))  # bool() coerces int 0/1; str "False" is truthy — flags must be Python bool False
        if not on:
            disabled_dimensions.append(name)
        return on

    # ── Regime-gated score multipliers ────────────────────────────
    # Apply a scalar multiplier to each dimension's contribution based on
    # the two-state VIX routing regime. Multiplier = 1.0 when routing is
    # disabled (config flag) or regime is unknown — zero-cost no-op.
    _rmult = _regime_multipliers(regime_router)

    # ── 1. DIRECTIONAL (0-10) — EMA alignment × ADX + timeframe vote ──
    # Merges the old TREND (EMA+ADX) and MTF (timeframe consensus) dimensions.
    # Eliminates correlated IC weight splitting. See score_directional().
    trend_pts = 0
    trend_dir = 0
    if _enabled("trend"):
        trend_pts, trend_dir = score_directional(sig_5m, sig_1d, sig_1w)
        trend_pts = int(round(trend_pts * _rmult.get("trend", 1.0)))
        score += trend_pts
        dim_directions.append((trend_dir, trend_pts))

    # ── 2. MOMENTUM (0-10) — MFI distance from 50 (symmetric) ──
    # MFI > 65 and MFI < 35 both score 10. The distance from the
    # neutral 50 line measures directional pressure strength.
    # Direction = which side of 50.
    mfi = sig_5m.get("mfi", 50)
    rs  = sig_5m.get("rsi_slope", 0)

    momentum = 0
    mom_dir = 0
    if _enabled("momentum"):
        mfi_dist = abs(mfi - 50)        # 0-50 range
        rsi_confirms = (mfi > 50 and rs > 0) or (mfi < 50 and rs < 0)

        if mfi_dist > 15 and rsi_confirms:
            momentum = 10   # Strong directional pressure + RSI confirming
        elif mfi_dist > 15:
            momentum = 8    # Strong pressure, RSI not confirming
        elif mfi_dist > 5 and rsi_confirms:
            momentum = 8    # Moderate pressure + RSI confirming
        elif mfi_dist > 5:
            momentum = 5    # Moderate pressure
        elif mfi_dist > 0:
            momentum = 2    # Weak but non-neutral
        mom_dir = +1 if mfi > 50 else (-1 if mfi < 50 else 0)
        momentum = int(round(momentum * _rmult["momentum"]))
        score += momentum
        dim_directions.append((mom_dir, momentum))

    # ── 3. SQUEEZE (0-10) — coiled spring detection (symmetric) ──
    # Squeeze scoring is already direction-agnostic (measures compression).
    # Direction comes from BB position: >0.5 = bullish breakout, <0.5 = bearish.
    squeeze_on = sig_5m.get("squeeze_on", False)
    squeeze_int = sig_5m.get("squeeze_intensity", 0)
    bb_pos = sig_5m.get("bb_position", 0.5)

    squeeze_score = 0
    squeeze_dir = 0
    if _enabled("squeeze"):
        if squeeze_on:
            squeeze_score = 4 + int(squeeze_int * 4)  # 4-8 based on tightness
            # BB position shows which direction the breakout is going
            if bb_pos > 0.7:
                squeeze_score = 10
                squeeze_dir = +1
            elif bb_pos < 0.3:
                squeeze_score = 10
                squeeze_dir = -1
            else:
                squeeze_dir = +1 if bb_pos > 0.5 else -1
        else:
            # Not in squeeze — BB position measures room to move
            bb_dist = abs(bb_pos - 0.5)
            if 0.1 < bb_dist < 0.3:
                squeeze_score = 3   # Healthy position, room to run
            squeeze_dir = +1 if bb_pos > 0.5 else (-1 if bb_pos < 0.5 else 0)
        squeeze_score = int(round(min(squeeze_score, 10) * _rmult["squeeze"]))
        score += squeeze_score
        dim_directions.append((squeeze_dir, squeeze_score))

    # ── 4. FLOW (0-10) — VWAP + OBV (symmetric) ──
    # Score measures the STRENGTH of institutional flow, not its direction.
    # VWAP distance from price = strength; OBV slope = confirmation.
    # Direction = above/below VWAP + OBV slope.
    vwap_d  = sig_5m.get("vwap_dist", 0)
    obv_s   = sig_5m.get("obv_slope", 0)

    flow_score = 0
    flow_dir = 0
    if _enabled("flow"):
        abs_vwap = abs(vwap_d)
        if abs_vwap > 0.3:
            flow_score += 4   # Solidly away from VWAP
        elif abs_vwap > 0:
            flow_score += 2   # Slightly away
        elif abs_vwap > -0.01:  # essentially at VWAP
            flow_score += 1

        # OBV confirms direction
        if abs(obv_s) > 0:
            flow_score += 4
        # Divergence penalty: VWAP and OBV disagree
        vwap_dir = +1 if vwap_d > 0 else (-1 if vwap_d < 0 else 0)
        obv_dir  = +1 if obv_s > 0 else (-1 if obv_s < 0 else 0)
        if vwap_dir != 0 and obv_dir != 0 and vwap_dir != obv_dir:
            flow_score = max(0, flow_score - 3)   # Penalise divergence

        # Flow direction: majority of VWAP + OBV
        if vwap_dir == obv_dir:
            flow_dir = vwap_dir
        elif abs(vwap_d) > 0.2:
            flow_dir = vwap_dir  # Strong VWAP signal wins
        else:
            flow_dir = obv_dir   # Near VWAP — OBV wins
        flow_score = int(round(min(flow_score, 10) * _rmult["flow"]))
        score += flow_score
        dim_directions.append((flow_dir, flow_score))

    # ── 5. BREAKOUT (0-10) — Donchian channel breach (symmetric) ──
    # Donchian high break and low break score identically.
    # Volume confirmation applies to both.
    donch = sig_5m.get("donch_breakout")
    if donch is None:
        donch = (1 if sig_5m.get("dc_upper_break") else (-1 if sig_5m.get("dc_lower_break") else 0))
    vr = sig_5m.get("vol_ratio")
    if vr is None:
        vr = sig_5m.get("volume_ratio", 0)

    breakout_score = 0
    breakout_dir = 0
    if _enabled("breakout"):
        if donch != 0:  # Channel breach in either direction
            breakout_score = 6
            breakout_dir = donch  # +1 for high break, -1 for low break
            if vr >= 2.0:
                breakout_score = 10
            elif vr >= 1.5:
                breakout_score = 8
        else:
            # No channel break — volume alone is directionally neutral
            if vr >= 2.0:
                breakout_score = 4
            elif vr >= 1.5:
                breakout_score = 2
        breakout_score = int(round(min(breakout_score, 10) * _rmult["breakout"]))
        score += breakout_score
        dim_directions.append((breakout_dir, breakout_score))

    # ── 6. MTF (0-10) — Multi-TimeFrame alignment ─────────────────────
    # Scores based on how many higher timeframes confirm the 5m direction.
    # No daily data → 0 pts (cannot confirm). Both daily+weekly confirm → 10 pts.
    mtf_score = 0
    mtf_dir = 0
    if _enabled("mtf"):
        if sig_1d is not None:
            d_bull = sig_1d.get("bull_aligned", False)
            d_bear = sig_1d.get("bear_aligned", False)
            if d_bull:
                mtf_score = 8
                mtf_dir = +1
            elif d_bear:
                mtf_score = 8
                mtf_dir = -1
            if sig_1w is not None:
                w_bull = sig_1w.get("bull_aligned", False)
                w_bear = sig_1w.get("bear_aligned", False)
                if (w_bull and d_bull) or (w_bear and d_bear):
                    mtf_score = 10  # Full weekly+daily confirmation
        mtf_score = int(round(min(mtf_score, 10) * _rmult.get("mtf", 1.0)))
        score += mtf_score
        dim_directions.append((mtf_dir, mtf_score))

    # ── 7. NEWS SENTIMENT (0-10) ────────────────────────
    # news_score is pre-computed by news.py (keyword + Claude two-tier)
    ns = 0
    if _enabled("news"):
        ns = int(round(min(10, max(0, news_score)) * _rmult["news"]))
        score += ns
        # News direction is embedded in the score sign from news.py
        # (positive = bullish news, negative = bearish) — but here we get
        # abs value, so direction comes from the raw news_score sign
        dim_directions.append((+1 if news_score > 0 else (-1 if news_score < 0 else 0), ns))

    # ── 8. SOCIAL (0-10) — Social sentiment score ─────────────────────
    social_pts = 0
    social_dir = 0
    if _enabled("social"):
        social_pts = int(round(min(10, max(0, social_score)) * _rmult.get("social", 1.0)))
        score += social_pts
        social_dir = +1 if social_score > 0 else (-1 if social_score < 0 else 0)
        dim_directions.append((social_dir, social_pts))

    # ── 9. REVERSION (0-10) — mean-reversion tendency ──────
    # Composite of Variance Ratio (VR) + OU half-life + z-score,
    # gated by ADF test (p < 0.05). Fires in ranging/choppy markets
    # where TREND and MOMENTUM score low.
    # Uses daily data when available (more stable for VR/OU/ADF).
    # ADF is the primary quality gate; VR and OU provide conviction;
    # z-score provides direction.
    #
    # Note: R/S Hurst exponent was evaluated and rejected — unreliable
    # on windows < 500 bars. Replaced by Variance Ratio (Lo-MacKinlay).
    # See roadmap/04-mean-reversion-dimension.md for full rationale.

    # Prefer daily data for VR/OU/ADF (more stable), fall back to 5m
    _rev_sig = sig_1d if sig_1d is not None else sig_5m
    _vr = _rev_sig.get("variance_ratio", 1.0)
    _ou_hl = _rev_sig.get("ou_halflife", 999.0)
    _adf_p = _rev_sig.get("adf_pvalue", 1.0)
    _zscore = sig_5m.get("zscore", 0.0)  # Z-score always from 5m (current price)

    reversion_score = 0
    rev_score_capped = 0
    rev_dir = 0

    if _enabled("reversion"):
        # ── ADF GATE — the only thing that matters first ──────
        # ADF p < 0.05 = statistically significant evidence of mean-reversion.
        # Without this gate, VR and OU produce ~32% false positives on 60-bar
        # random walks. With ADF gate: ~7.7% FP, 75% TP on strong OU.
        # If ADF fails (p >= 0.05), entire REVERSION dimension scores 0.
        if _adf_p < 0.05:
            # Sub-metric 1: Variance Ratio (0-3 pts)
            # VR < 1 = mean-reverting returns. Calibrated on 60-bar Monte Carlo.
            vr_pts = 0
            if _vr < 0.55:
                vr_pts = 3       # Strong mean-reversion (OU theta ≈ 0.3)
            elif _vr < 0.70:
                vr_pts = 2       # Moderate mean-reversion (OU theta ≈ 0.2)
            elif _vr < 0.80:
                vr_pts = 1       # Weak signal

            # Sub-metric 2: OU half-life (0-4 pts)
            # Shorter half-life = faster reversion = more tradeable
            ou_pts = 0
            if _ou_hl < 5:
                ou_pts = 4       # Reverts in < 5 periods — very tradeable
            elif _ou_hl < 10:
                ou_pts = 3
            elif _ou_hl < 20:
                ou_pts = 2
            elif _ou_hl < 40:
                ou_pts = 1

            # Sub-metric 3: Z-score magnitude (0-3 pts)
            # How far price has deviated from its 20-period mean
            _abs_z = abs(_zscore)
            zscore_pts = 0
            if _abs_z > 2.5:
                zscore_pts = 3   # Extreme deviation — high reversion probability
            elif _abs_z > 2.0:
                zscore_pts = 2
            elif _abs_z > 1.5:
                zscore_pts = 1

            reversion_score = vr_pts + ou_pts + zscore_pts
        rev_score_capped = int(round(min(reversion_score, 10) * _rmult["reversion"]))
        score += rev_score_capped
        # Reversion direction: z-score tells us which way to trade.
        # Positive z = price above mean → SHORT (fade it)
        # Negative z = price below mean → LONG (fade it)
        rev_dir = -1 if _zscore > 0.5 else (+1 if _zscore < -0.5 else 0)
        dim_directions.append((rev_dir, rev_score_capped))

    # ── BONUS: Candlestick confirmation (+3 max) ────────
    # Direction-agnostic: both bull and bear candles add bonus points.
    # Direction already captured in dim_directions.
    cb = sig_5m.get("candle_bull", 0)
    cd = sig_5m.get("candle_bear", 0)
    candle_bonus = 0
    if cb > 0 or cd > 0:
        candle_bonus = min(max(cb, cd), 3)
        score += candle_bonus
        candle_dir = +1 if cb > cd else (-1 if cd > cb else 0)
        dim_directions.append((candle_dir, candle_bonus))

    # ── IC-WEIGHTED COMPOSITE ─────────────────────────────────────────────
    # Replace the static equal-weight additive sum with a rolling IC-weighted
    # composite.  Weight_i = normalised Spearman IC between dimension i and
    # 5-day forward return (recomputed weekly via update_ic_weights()).
    #
    # Under equal weights (1/9 per dim) the result is IDENTICAL to the prior
    # equal-weight sum, so this is a fully backward-compatible no-op at
    # system startup before any IC data has accumulated.
    #
    # Scaling factor = _N (number of dimensions) ensures that with equal
    # weights: sum(1/N * N * d_i) = sum(d_i) — same 0-50 scale as before.
    try:
        from ic_calculator import get_current_weights as _get_ic_weights, DIMENSIONS as _IC_DIMS
        _icw = _get_ic_weights()
        _N_DIMS = len(_IC_DIMS)
        _ic_breakdown = {
            "trend":     trend_pts,
            "momentum":  momentum,
            "squeeze":   squeeze_score,
            "flow":      flow_score,
            "breakout":  breakout_score,
            "mtf":       mtf_score,
            "news":      ns,
            "social":    social_pts,
            "reversion": rev_score_capped,
        }
        _ic_sum = sum(
            _icw.get(k, 1.0 / _N_DIMS) * _N_DIMS * v
            for k, v in _ic_breakdown.items()
        )
        # candle_bonus is a non-dimension extra (0-3); add it on top of the
        # weighted composite so candlestick confirmation still lifts the score.
        score = int(round(_ic_sum)) + candle_bonus
    except Exception:
        pass  # keep the incrementally-accumulated score if IC module unavailable

    # ── SOFT GATE: MTF penalty (applied before cap) ──────
    mtf_gate_status = "PASS"
    mtf_conflict_msg = ""
    if gate_mode == "soft" and not mtf_alignment["aligned"] and mtf_alignment["gate_applies"]:
        penalty = CONFIG.get("mtf_penalty_points", 8)
        score = max(0, score - penalty)
        mtf_gate_status = "PENALISED"
        mtf_conflict_msg = mtf_alignment["conflict"]
        log.info(
            f"MTF GATE PENALTY {sig_5m.get('symbol','?')}: -{penalty}pts → {score}/50 | "
            f"{mtf_alignment['conflict']}"
        )

    # Cap at 50
    score = min(score, 50)

    # ── DIRECTION: Weighted majority vote of all dimensions ──────
    # Each dimension casts a vote (+1 long, -1 short) weighted by its
    # score. Higher-scoring dimensions have more influence on direction.
    # This replaces the old buy_signals/sell_signals count which was
    # biased toward the signal classification (itself asymmetric).
    weighted_sum = sum(d * w for d, w in dim_directions)

    # Determine direction from weighted vote
    if weighted_sum > 2:
        direction = "LONG"
    elif weighted_sum < -2:
        direction = "SHORT"
    else:
        # Tie or near-zero — fall back to timeframe signals
        if buy_signals > sell_signals:
            direction = "LONG"
        elif sell_signals > buy_signals:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

    # Signal strength from score + direction
    if direction == "LONG":
        if strong_buy >= 2 or (strong_buy >= 1 and buy_signals == total_tf):
            final_signal = "STRONG_BUY"
        else:
            final_signal = "BUY"
    elif direction == "SHORT":
        if strong_sell >= 2 or (strong_sell >= 1 and sell_signals == total_tf):
            final_signal = "STRONG_SELL"
        else:
            final_signal = "SELL"
    else:
        final_signal = "HOLD"

    # ── CANDLESTICK CONFIRMATION GATE ───────────────────────
    # If candle_required is True, any directional entry (BUY/SELL)
    # without a confirming candlestick pattern is downgraded to HOLD.
    # candle_bonus is 0 when neither candle_bull nor candle_bear fired.
    candle_gate_status = "PASS"
    if CONFIG.get("candle_required", False) and candle_bonus == 0:
        if "BUY" in final_signal or "SELL" in final_signal:
            candle_gate_status = "BLOCKED"
            final_signal = "HOLD"
            direction = "NEUTRAL"
            log.info(
                f"CANDLE GATE BLOCKED {sig_5m.get('symbol', '?')}: "
                f"no confirming candle (bull={cb}, bear={cd})"
            )

    return {
        "signal":     final_signal,
        "direction":  direction,
        "score":      score,
        "buy_count":  buy_signals,
        "sell_count": sell_signals,
        "tf_count":   total_tf,
        # Direction-agnostic dimension vote (roadmap #01)
        "direction_weighted_sum": round(weighted_sum, 1),
        # Multi-timeframe alignment gate results
        "mtf_gate":       mtf_gate_status,
        "mtf_conflict":   mtf_conflict_msg,
        "mtf_daily_trend": mtf_alignment["daily_trend"],
        "mtf_weekly_trend": mtf_alignment.get("weekly_trend", "N/A"),
        # Candlestick confirmation gate
        "candle_gate":    candle_gate_status,
        # Reversion metrics (for dashboard + agent consumption)
        "reversion_score": min(reversion_score, 10),
        "variance_ratio": round(_vr, 3),
        "ou_halflife": round(_ou_hl, 1),
        "zscore":     round(_zscore, 2),
        "adf_pvalue": round(_adf_p, 4),
        # Per-dimension score breakdown (for trade logging / IC feedback loop)
        # Keys must exactly match ic_calculator.DIMENSIONS for IC computation.
        "score_breakdown": {
            "trend":     trend_pts,
            "momentum":  momentum,
            "squeeze":   squeeze_score,
            "flow":      flow_score,
            "breakout":  breakout_score,
            "mtf":       mtf_score,
            "news":      ns,
            "social":    social_pts,
            "reversion": rev_score_capped,
        },
        # Dimensions that were zeroed by a False flag (for diagnostics / dashboard)
        "disabled_dimensions": disabled_dimensions,
        # Regime routing state that produced these scores
        "regime_router":       regime_router,
    }


def get_regime_threshold(regime: str) -> int:
    """
    Return the minimum score threshold for the given market regime.

    Thresholds are relative to config's min_score_to_trade (base) so that
    paper trading vs live configs automatically scale together.
    Offsets and floors are configurable in config.py.

    Returns an int score threshold (0-99).
    """
    base         = CONFIG["min_score_to_trade"]
    bear_offset  = CONFIG.get("regime_threshold_bear_offset",   -3)
    choppy_offset= CONFIG.get("regime_threshold_choppy_offset", -6)
    panic        = CONFIG.get("regime_threshold_panic",          99)
    bear_min     = CONFIG.get("regime_threshold_bear_min",       15)
    choppy_min   = CONFIG.get("regime_threshold_choppy_min",     12)

    thresholds = {
        "BULL_TRENDING": base,
        "BEAR_TRENDING": max(bear_min,   base + bear_offset),
        "CHOPPY":        max(choppy_min, base + choppy_offset),
        "PANIC":         panic,
        "UNKNOWN":       max(bear_min,   base + bear_offset),
    }
    return thresholds.get(regime, base)


def score_universe(symbols: list, regime: str = "UNKNOWN",
                   news_data: dict = None, social_data: dict = None,
                   regime_router: str | None = None) -> tuple:
    """
    Score all symbols in the universe.

    Returns a tuple: (above_threshold, all_scored)
      above_threshold — list of symbols whose score >= regime threshold, sorted
                        descending. Use this for trading decisions.
      all_scored      — list of ALL scored symbols regardless of threshold, sorted
                        descending. Use this for IC logging and analysis.

    news_data:     optional {symbol: news_sentiment_dict} from news.py
    social_data:   optional {symbol: social_sentiment_dict} from social_sentiment.py
    regime_router: two-state routing regime ("momentum"|"mean_reversion"|"unknown").
                   If None and regime_routing_enabled is True, fetches ^VIX to compute
                   it. Pass the value from bot.py to avoid a duplicate VIX fetch.
    """
    if news_data is None:
        news_data = {}
    if social_data is None:
        social_data = {}

    threshold = get_regime_threshold(regime)

    # ── Determine routing regime (VIX + optional Hurst consensus) ──
    if regime_router is None:
        if CONFIG.get("regime_routing_enabled", True):
            vix_result  = get_market_regime_vix()
            _vix_r      = vix_result["regime"]
            _hurst_r    = "unknown"
            if CONFIG.get("hurst_regime", {}).get("enabled", False):
                _hurst_r = get_hurst_regime_spy().get("regime", "unknown")
            regime_router = _resolve_regime_router(_vix_r, _hurst_r)
            log.info(f"score_universe regime router: {regime_router} "
                     f"(VIX={vix_result.get('vix')}, hurst={_hurst_r})")
        else:
            regime_router = "unknown"

    # ── PARALLEL SCORING via ProcessPoolExecutor ────────────────
    # Each worker is a separate process with its own yfinance globals,
    # completely avoiding the thread-safety bug (GitHub issue #2557).
    # Falls back to sequential if multiprocessing fails (e.g. fork issues).
    all_results = []
    failures = 0
    args_list = [
        (sym,
         news_data.get(sym, {}).get("news_score", 0),
         int(social_data.get(sym, {}).get("social_score", 0)),
         regime_router)
        for sym in symbols
    ]

    try:
        pool = _get_score_pool()
        futures = {pool.submit(_fetch_one_process, args): args[0] for args in args_list}
        for future in as_completed(futures, timeout=300):
            sym = futures[future]
            try:
                data = future.result(timeout=60)
                if data:
                    if sym in news_data:
                        data["news"] = news_data[sym]
                    all_results.append(data)
                else:
                    failures += 1
            except Exception:
                failures += 1
    except Exception as e:
        # Fallback: sequential scoring if process pool fails
        logging.warning(f"Process pool failed ({e}), falling back to sequential scoring")
        for sym, ns, ss, rr in args_list:
            try:
                data = fetch_multi_timeframe(sym, news_score=ns, social_score=ss,
                                             regime_router=rr)
                if data:
                    if sym in news_data:
                        data["news"] = news_data[sym]
                    all_results.append(data)
                else:
                    failures += 1
            except Exception:
                failures += 1

    total = len(symbols)
    if total > 0 and failures / total > 0.8:
        logging.critical(
            f"score_universe: {failures}/{total} symbols failed data fetch "
            f"— aborting scan cycle to prevent low-confidence orders"
        )
        return [], []

    all_sorted = sorted(all_results, key=lambda x: x["score"], reverse=True)
    above_threshold = [r for r in all_sorted if r["score"] >= threshold]
    return above_threshold, all_sorted
