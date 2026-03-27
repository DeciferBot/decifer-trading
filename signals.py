# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  signals.py                           ║
# ║   Lean 9-dimension signal engine — Wall Street alpha        ║
# ║                                                              ║
# ║   Architecture: ONE indicator per dimension.                 ║
# ║   No redundant oscillators. Every signal measures something  ║
# ║   different. Clean scores that differentiate, not confuse.   ║
# ║                                                              ║
# ║   Dimensions:                                                ║
# ║     1. TREND      — EMA alignment + ADX strength             ║
# ║     2. MOMENTUM   — MFI (volume-weighted RSI)                ║
# ║     3. SQUEEZE    — BB inside Keltner = coiled spring         ║
# ║     4. FLOW       — VWAP position + OBV divergence            ║
# ║     5. BREAKOUT   — Donchian channel breach + volume          ║
# ║     6. CONFLUENCE — Multi-timeframe agreement                 ║
# ║     7. NEWS       — Yahoo RSS keyword + Claude sentiment      ║
# ║     8. SOCIAL     — Reddit mention velocity + VADER           ║
# ║     9. REVERSION  — Variance Ratio + OU half-life + z-score    ║
# ╚══════════════════════════════════════════════════════════════╝

import time as _time
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
    symbol, news_score, social_score = args
    try:
        return fetch_multi_timeframe(symbol, news_score=news_score, social_score=social_score)
    except Exception:
        return None

log = logging.getLogger("decifer.signals")

# Suppress noisy yfinance warnings (ETF fundamentals 404s, Invalid Crumb 401s)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


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


def fetch_multi_timeframe(symbol: str, news_score: int = 0, social_score: int = 0) -> dict | None:
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
                                        news_score=news_score, social_score=social_score)

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


def compute_confluence(sig_5m: dict, sig_1d: dict | None, sig_1w: dict | None,
                       news_score: int = 0, social_score: int = 0) -> dict:
    """
    Decifer 2.0 — 9-dimension scoring engine.

    Each dimension scores 0-10, total max 90, capped at 50.
    Bonus points for candlestick confirmation.

    Multi-Timeframe Alignment Gate (NEW):
      Before scoring, checks if daily/weekly trends support the 5m direction.
      - "hard" mode: returns score=0 + HOLD signal if misaligned
      - "soft" mode: deducts mtf_penalty_points from final score
      - "off" mode:  legacy behaviour (Dimension 6 only)

    Dimensions:
      1. TREND (0-10)      — EMA alignment × ADX gating
      2. MOMENTUM (0-10)   — MFI + RSI slope
      3. SQUEEZE (0-10)    — BB/Keltner compression → breakout potential
      4. FLOW (0-10)       — VWAP position + OBV confirmation
      5. BREAKOUT (0-10)   — Donchian channel breach + volume
      6. MTF (0-10)        — Multi-timeframe agreement
      7. NEWS (0-10)       — Yahoo RSS keyword + Claude sentiment
      8. SOCIAL (0-10)     — Reddit mention velocity + VADER
      9. REVERSION (0-10)  — Variance Ratio + OU half-life + z-score
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
            "reversion_score": 0,
            "variance_ratio": 0,
            "ou_halflife": 0,
            "zscore": 0,
            "adf_pvalue": 1.0,
        }

    signals = [sig_5m["signal"]]
    if sig_1d: signals.append(sig_1d["signal"])
    if sig_1w: signals.append(sig_1w["signal"])

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

    # ── 1. TREND (0-10) — EMA alignment quality × ADX strength ──
    # Score measures how cleanly aligned the EMAs are, regardless of
    # which direction they point. Direction comes from which way.
    adx  = sig_5m.get("adx", 0)
    adx_mult = 1.0 if adx > 25 else 0.7 if adx > 20 else 0.4

    base_trend = 0
    trend_dir = 0
    if sig_5m["bull_aligned"]:
        base_trend = 8
        trend_dir = +1
        if sig_5m["macd_accel"] > 0:
            base_trend = 10  # MACD confirms the alignment
    elif sig_5m["bear_aligned"]:
        base_trend = 8
        trend_dir = -1
        if sig_5m["macd_accel"] < 0:
            base_trend = 10  # MACD confirms the alignment
    elif "BUY" in sig_5m["signal"] or "SELL" in sig_5m["signal"]:
        base_trend = 4  # Signal without full alignment
        trend_dir = +1 if "BUY" in sig_5m["signal"] else -1
    trend_pts = int(base_trend * adx_mult)
    score += trend_pts
    dim_directions.append((trend_dir, trend_pts))

    # ── 2. MOMENTUM (0-10) — MFI distance from 50 (symmetric) ──
    # MFI > 65 and MFI < 35 both score 10. The distance from the
    # neutral 50 line measures directional pressure strength.
    # Direction = which side of 50.
    mfi = sig_5m.get("mfi", 50)
    rs  = sig_5m.get("rsi_slope", 0)

    mfi_dist = abs(mfi - 50)        # 0-50 range
    rsi_confirms = (mfi > 50 and rs > 0) or (mfi < 50 and rs < 0)

    momentum = 0
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
    squeeze_score = min(squeeze_score, 10)
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
    flow_score = min(flow_score, 10)
    score += flow_score
    dim_directions.append((flow_dir, flow_score))

    # ── 5. BREAKOUT (0-10) — Donchian channel breach (symmetric) ──
    # Donchian high break and low break score identically.
    # Volume confirmation applies to both.
    donch = sig_5m.get("donch_breakout", 0)
    vr    = sig_5m.get("vol_ratio", 0)

    breakout_score = 0
    breakout_dir = 0
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
    breakout_score = min(breakout_score, 10)
    score += breakout_score
    dim_directions.append((breakout_dir, breakout_score))

    # ── 6. MULTI-TIMEFRAME CONFLUENCE (0-10) ────────────
    total_tf = len(signals)
    agree    = max(buy_signals, sell_signals)
    mtf_score = int((agree / total_tf) * 10)
    score += mtf_score
    # MTF direction = majority of timeframes
    mtf_dir = +1 if buy_signals > sell_signals else (-1 if sell_signals > buy_signals else 0)
    dim_directions.append((mtf_dir, mtf_score))

    # ── 7. NEWS SENTIMENT (0-10) ────────────────────────
    # news_score is pre-computed by news.py (keyword + Claude two-tier)
    ns = min(10, max(0, news_score))
    score += ns
    # News direction is embedded in the score sign from news.py
    # (positive = bullish news, negative = bearish) — but here we get
    # abs value, so direction comes from the raw news_score sign
    dim_directions.append((+1 if news_score > 0 else (-1 if news_score < 0 else 0), ns))

    # ── 8. SOCIAL SENTIMENT (0-10) ────────────────────
    # social_score from social_sentiment.py (Reddit mention velocity + VADER)
    ss = min(10, max(0, social_score))
    score += ss
    dim_directions.append((+1 if social_score > 0 else (-1 if social_score < 0 else 0), ss))

    # ── 9. REVERSION (0-10) — mean-reversion tendency ──────
    # Composite of Hurst exponent + OU half-life + z-score.
    # Fires in ranging/choppy markets where TREND and MOMENTUM score low.
    # Uses daily data when available (more stable for Hurst/OU).
    # Z-score provides direction; Hurst + OU provide conviction.
    #
    # SAFETY: Hurst must confirm mean-reversion (H < 0.50) before
    # z-score counts. Without this, we'd catch falling knives.

    # Prefer daily data for VR/OU/ADF (more stable), fall back to 5m
    _rev_sig = sig_1d if sig_1d is not None else sig_5m
    _vr = _rev_sig.get("variance_ratio", 1.0)
    _ou_hl = _rev_sig.get("ou_halflife", 999.0)
    _adf_p = _rev_sig.get("adf_pvalue", 1.0)
    _zscore = sig_5m.get("zscore", 0.0)  # Z-score always from 5m (current price)

    reversion_score = 0

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
    rev_score_capped = min(reversion_score, 10)
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
        # Reversion metrics (for dashboard + agent consumption)
        "reversion_score": min(reversion_score, 10),
        "variance_ratio": round(_vr, 3),
        "ou_halflife": round(_ou_hl, 1),
        "zscore":     round(_zscore, 2),
        "adf_pvalue": round(_adf_p, 4),
    }


def score_universe(symbols: list, regime: str = "UNKNOWN",
                   news_data: dict = None, social_data: dict = None) -> list:
    """
    Score all symbols in the universe.
    Returns only those above the minimum score threshold, sorted by score.

    news_data: optional {symbol: news_sentiment_dict} from news.py
    social_data: optional {symbol: social_sentiment_dict} from social_sentiment.py
    """
    if news_data is None:
        news_data = {}
    if social_data is None:
        social_data = {}

    # Regime-specific thresholds, scaled relative to config's min_score_to_trade.
    # This way paper trading config (min_score=18) automatically loosens all thresholds.
    base = CONFIG["min_score_to_trade"]
    regime_thresholds = {
        "BULL_TRENDING": base,           # Full threshold in bull (was hardcoded 28)
        "BEAR_TRENDING": max(15, base - 3),  # Slightly lower for bear setups
        "CHOPPY":        max(12, base - 6),  # Much lower for choppy — need data from all regimes
        "PANIC":         99,             # Still block trades in panic
        "UNKNOWN":       max(15, base - 3),
    }
    threshold = regime_thresholds.get(regime, base)

    # ── PARALLEL SCORING via ProcessPoolExecutor ────────────────
    # Each worker is a separate process with its own yfinance globals,
    # completely avoiding the thread-safety bug (GitHub issue #2557).
    # Falls back to sequential if multiprocessing fails (e.g. fork issues).
    results = []
    args_list = [
        (sym,
         news_data.get(sym, {}).get("news_score", 0),
         int(social_data.get(sym, {}).get("social_score", 0)))
        for sym in symbols
    ]

    try:
        pool = _get_score_pool()
        futures = {pool.submit(_fetch_one_process, args): args[0] for args in args_list}
        for future in as_completed(futures, timeout=300):
            sym = futures[future]
            try:
                data = future.result(timeout=60)
                if data and data["score"] >= threshold:
                    if sym in news_data:
                        data["news"] = news_data[sym]
                    results.append(data)
            except Exception:
                pass
    except Exception as e:
        # Fallback: sequential scoring if process pool fails
        logging.warning(f"Process pool failed ({e}), falling back to sequential scoring")
        for sym, ns, ss in args_list:
            try:
                data = fetch_multi_timeframe(sym, news_score=ns, social_score=ss)
                if data and data["score"] >= threshold:
                    if sym in news_data:
                        data["news"] = news_data[sym]
                    results.append(data)
            except Exception:
                pass

    return sorted(results, key=lambda x: x["score"], reverse=True)
