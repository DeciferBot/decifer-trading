# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  signals.py                           ║
# ║   Lean 6-dimension signal engine — Wall Street alpha        ║
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
# ╚══════════════════════════════════════════════════════════════╝

import time as _time
import yfinance as yf
import pandas as pd
import numpy as np
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False
from config import CONFIG

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


def fetch_multi_timeframe(symbol: str, news_score: int = 0) -> dict | None:
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

        # Multi-timeframe confluence score (with news as 7th dimension)
        confluence = compute_confluence(sig_5m, sig_1d, sig_1w, news_score=news_score)

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
            # Signal
            "signal":           signal,
        }

    except Exception as e:
        log.warning(f"Indicator compute error {symbol} {tf}: {e}")
        return None


def compute_confluence(sig_5m: dict, sig_1d: dict | None, sig_1w: dict | None,
                       news_score: int = 0) -> dict:
    """
    Decifer 2.0 — 7-dimension scoring engine.

    Each dimension scores 0-10, total max 70, capped at 50.
    Bonus points for candlestick confirmation.

    Dimensions:
      1. TREND (0-10)     — EMA alignment × ADX gating
      2. MOMENTUM (0-10)  — MFI + RSI slope
      3. SQUEEZE (0-10)   — BB/Keltner compression → breakout potential
      4. FLOW (0-10)      — VWAP position + OBV confirmation
      5. BREAKOUT (0-10)  — Donchian channel breach + volume
      6. MTF (0-10)       — Multi-timeframe agreement
      7. NEWS (0-10)      — Yahoo RSS keyword + Claude sentiment
    """
    signals = [sig_5m["signal"]]
    if sig_1d: signals.append(sig_1d["signal"])
    if sig_1w: signals.append(sig_1w["signal"])

    buy_signals  = sum(1 for s in signals if "BUY"  in s)
    sell_signals = sum(1 for s in signals if "SELL" in s)
    strong_buy   = sum(1 for s in signals if s == "STRONG_BUY")
    strong_sell  = sum(1 for s in signals if s == "STRONG_SELL")

    score = 0

    # ── 1. TREND (0-10) — EMA alignment gated by ADX ────
    adx  = sig_5m.get("adx", 0)
    # ADX multiplier: strong trend gets full credit, weak trend gets partial
    adx_mult = 1.0 if adx > 25 else 0.7 if adx > 20 else 0.4

    base_trend = 0
    if sig_5m["bull_aligned"] or sig_5m["bear_aligned"]:
        base_trend = 8
        # Bonus for MACD confirming trend direction
        ma = sig_5m["macd_accel"]
        if ("BUY" in sig_5m["signal"] and ma > 0) or ("SELL" in sig_5m["signal"] and ma < 0):
            base_trend = 10
    elif "BUY" in sig_5m["signal"] or "SELL" in sig_5m["signal"]:
        base_trend = 4  # Signal without full alignment
    score += int(base_trend * adx_mult)

    # ── 2. MOMENTUM (0-10) — MFI is the single source of truth ──
    mfi = sig_5m.get("mfi", 50)
    rs  = sig_5m.get("rsi_slope", 0)

    momentum = 0
    if "BUY" in sig_5m["signal"]:
        if mfi > 65 and rs > 0:     momentum = 10  # Strong money flow in + rising
        elif mfi > 55 and rs > 0:   momentum = 8
        elif mfi > 50:              momentum = 5
        elif mfi > 40:              momentum = 2   # Weak but possible reversal
    elif "SELL" in sig_5m["signal"]:
        if mfi < 35 and rs < 0:     momentum = 10
        elif mfi < 45 and rs < 0:   momentum = 8
        elif mfi < 50:              momentum = 5
        elif mfi < 60:              momentum = 2
    score += momentum

    # ── 3. SQUEEZE (0-10) — coiled spring detection ─────
    squeeze_on = sig_5m.get("squeeze_on", False)
    squeeze_int = sig_5m.get("squeeze_intensity", 0)
    bb_pos = sig_5m.get("bb_position", 0.5)

    squeeze_score = 0
    if squeeze_on:
        # Squeeze is ON — this is a setup, not a signal by itself
        squeeze_score = 4 + int(squeeze_int * 4)  # 4-8 based on tightness

        # Squeeze + breakout direction = full points
        if ("BUY" in sig_5m["signal"] and bb_pos > 0.7):
            squeeze_score = 10  # Breaking out of squeeze upward
        elif ("SELL" in sig_5m["signal"] and bb_pos < 0.3):
            squeeze_score = 10  # Breaking out of squeeze downward
    else:
        # No squeeze — BB position still useful for extension check
        if "BUY" in sig_5m["signal"] and 0.3 < bb_pos < 0.8:
            squeeze_score = 3   # Healthy position, room to run
        elif "SELL" in sig_5m["signal"] and 0.2 < bb_pos < 0.7:
            squeeze_score = 3
    score += min(squeeze_score, 10)

    # ── 4. FLOW (0-10) — VWAP + OBV institutional tracking ──
    vwap_d  = sig_5m.get("vwap_dist", 0)
    obv_s   = sig_5m.get("obv_slope", 0)

    flow_score = 0
    if "BUY" in sig_5m["signal"]:
        # Above VWAP = institutions supporting the move
        if vwap_d > 0.3:       flow_score += 4   # Solidly above VWAP
        elif vwap_d > 0:       flow_score += 2   # Slightly above
        elif vwap_d > -0.2:    flow_score += 1   # Near VWAP (could reclaim)
        # OBV confirming
        if obv_s > 0:          flow_score += 4   # Volume backing the move
        elif obv_s == 0:       flow_score += 1
        # OBV divergence WARNING: price up but OBV down = distribution
        if vwap_d > 0 and obv_s < 0:
            flow_score = max(0, flow_score - 3)   # Penalise divergence
    elif "SELL" in sig_5m["signal"]:
        if vwap_d < -0.3:      flow_score += 4
        elif vwap_d < 0:       flow_score += 2
        elif vwap_d < 0.2:     flow_score += 1
        if obv_s < 0:          flow_score += 4
        elif obv_s == 0:       flow_score += 1
        if vwap_d < 0 and obv_s > 0:
            flow_score = max(0, flow_score - 3)
    score += min(flow_score, 10)

    # ── 5. BREAKOUT (0-10) — Donchian channel breach ────
    donch = sig_5m.get("donch_breakout", 0)
    vr    = sig_5m.get("vol_ratio", 0)

    breakout_score = 0
    if "BUY" in sig_5m["signal"]:
        if donch == 1:  # Breaking above Donchian high
            breakout_score = 6
            if vr >= 2.0:   breakout_score = 10  # Volume-confirmed breakout
            elif vr >= 1.5: breakout_score = 8
        elif vr >= 2.0:     breakout_score = 4   # High volume without channel break
        elif vr >= 1.5:     breakout_score = 2
    elif "SELL" in sig_5m["signal"]:
        if donch == -1:
            breakout_score = 6
            if vr >= 2.0:   breakout_score = 10
            elif vr >= 1.5: breakout_score = 8
        elif vr >= 2.0:     breakout_score = 4
        elif vr >= 1.5:     breakout_score = 2
    score += min(breakout_score, 10)

    # ── 6. MULTI-TIMEFRAME CONFLUENCE (0-10) ────────────
    total_tf = len(signals)
    agree    = max(buy_signals, sell_signals)
    score   += int((agree / total_tf) * 10)

    # ── 7. NEWS SENTIMENT (0-10) ────────────────────────
    # news_score is pre-computed by news.py (keyword + Claude two-tier)
    score += min(10, max(0, news_score))

    # ── BONUS: Candlestick confirmation (+3 max) ────────
    cb = sig_5m.get("candle_bull", 0)
    cd = sig_5m.get("candle_bear", 0)
    if "BUY" in sig_5m["signal"] and cb > 0:
        score += min(cb, 3)
    elif "SELL" in sig_5m["signal"] and cd > 0:
        score += min(cd, 3)

    # Cap at 50
    score = min(score, 50)

    # Final direction
    if buy_signals > sell_signals:
        direction = "LONG"
        if strong_buy >= 2 or (strong_buy >= 1 and buy_signals == total_tf):
            final_signal = "STRONG_BUY"
        else:
            final_signal = "BUY"
    elif sell_signals > buy_signals:
        direction = "SHORT"
        if strong_sell >= 2 or (strong_sell >= 1 and sell_signals == total_tf):
            final_signal = "STRONG_SELL"
        else:
            final_signal = "SELL"
    else:
        direction = "NEUTRAL"
        final_signal = "HOLD"

    return {
        "signal":     final_signal,
        "direction":  direction,
        "score":      score,
        "buy_count":  buy_signals,
        "sell_count": sell_signals,
        "tf_count":   total_tf,
    }


def score_universe(symbols: list, regime: str = "UNKNOWN",
                   news_data: dict = None) -> list:
    """
    Score all symbols in the universe.
    Returns only those above the minimum score threshold, sorted by score.

    news_data: optional {symbol: news_sentiment_dict} from news.py
    """
    if news_data is None:
        news_data = {}

    regime_thresholds = {
        "BULL_TRENDING": 28,
        "BEAR_TRENDING": 25,
        "CHOPPY":        22,
        "PANIC":         99,
        "UNKNOWN":       25,
    }
    threshold = regime_thresholds.get(regime, CONFIG["min_score_to_trade"])

    def _fetch_one(symbol):
        try:
            return fetch_multi_timeframe(symbol, news_score=news_data.get(symbol, {}).get("news_score", 0))
        except Exception:
            return None

    results = []
    # max_workers=1: yfinance.download() is NOT thread-safe (GitHub issue #2557).
    # Concurrent calls share a global dict (_DFS) causing cross-symbol data contamination.
    # Sequential fetching is slower (~3-4 min vs ~1 min) but guarantees clean data.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future_to_sym = {pool.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                data = future.result()
                if data and data["score"] >= threshold:
                    if sym in news_data:
                        data["news"] = news_data[sym]
                    results.append(data)
            except Exception:
                pass

    return sorted(results, key=lambda x: x["score"], reverse=True)
