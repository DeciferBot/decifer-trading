# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  fx_signals.py                              ║
# ║   FX pair scoring — momentum + macro overlay                 ║
# ║                                                              ║
# ║   Scores EUR/USD, GBP/USD, USD/JPY, AUD/USD on two          ║
# ║   uncorrelated axes:                                         ║
# ║     • Momentum (0-25) — EMA alignment + RSI on 1h bars      ║
# ║     • Macro (0-25)    — DXY direction + credit stress align  ║
# ║   Total scaled to 0-50 to match equity signal schema.        ║
# ║                                                              ║
# ║   Data: yfinance (free, EURUSD=X format). No vol/float.      ║
# ║   Contract: IBKR Forex (already supported in orders_contracts)║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
from datetime import datetime, time

import pytz

log = logging.getLogger("decifer.fx_signals")

_EST = pytz.timezone("America/New_York")

# ── FX pair config: symbol → (yfinance_ticker, direction_convention) ─────────
# direction_convention: "base_up" means score is LONG when base currency rises
# e.g. EURUSD "base_up" → LONG if EUR strengthening vs USD
# USDJPY "base_down" → LONG if USD strengthening (JPY falls), so same as base_up
FX_PAIRS: dict[str, dict] = {
    "EURUSD": {"yf_ticker": "EURUSD=X", "pip_size": 0.0001, "name": "Euro/USD"},
    "GBPUSD": {"yf_ticker": "GBPUSD=X", "pip_size": 0.0001, "name": "Sterling/USD"},
    "USDJPY": {"yf_ticker": "USDJPY=X", "pip_size": 0.01,   "name": "USD/Yen"},
    "AUDUSD": {"yf_ticker": "AUDUSD=X", "pip_size": 0.0001, "name": "Aussie/USD"},
}

# FX trades 23h/5d but avoid low-liquidity windows
_FX_AVOID_HOURS = [  # ET: avoid midnight–6am (thin tape, wide spreads)
    (time(0, 0), time(6, 0)),
]


def _is_fx_session() -> bool:
    """Return True when FX liquidity is acceptable (avoid 12am-6am ET)."""
    now_et = datetime.now(_EST).time()
    for start, end in _FX_AVOID_HOURS:
        if start <= now_et < end:
            return False
    return True


def _compute_fx_indicators(df) -> dict | None:
    """
    Compute EMA, RSI, MACD from a yfinance 1h OHLCV DataFrame.
    Returns indicator dict or None if data insufficient.
    """
    if df is None or len(df) < 30:
        return None

    try:
        import pandas as pd
        import numpy as np

        close = df["Close"].squeeze().dropna()
        if len(close) < 30:
            return None

        # EMA alignment
        ema9  = close.ewm(span=9,  adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        price    = float(close.iloc[-1])
        e9, e21, e50 = float(ema9.iloc[-1]), float(ema21.iloc[-1]), float(ema50.iloc[-1])

        bull_aligned = e9 > e21 > e50
        bear_aligned = e9 < e21 < e50

        # RSI(14)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = float(100 - 100 / (1 + rs.iloc[-1]))
        import math as _math
        if _math.isnan(rsi):
            rsi = 50.0  # pure uptrend or downtrend — treat as neutral for scoring
        rsi_slope = float((rs.iloc[-1] - rs.iloc[-3]) if len(rs) >= 4 else 0)

        # MACD(12,26,9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        macd_sig = macd.ewm(span=9, adjust=False).mean()
        macd_bull = float(macd.iloc[-1]) > float(macd_sig.iloc[-1])

        # ATR (for SL/TP sizing)
        high = df["High"].squeeze().dropna()
        low  = df["Low"].squeeze().dropna()
        atr_vals = (high - low).rolling(14).mean()
        atr = float(atr_vals.iloc[-1]) if len(atr_vals) >= 14 else float(high.iloc[-1] - low.iloc[-1])

        return {
            "price":        price,
            "bull_aligned": bull_aligned,
            "bear_aligned": bear_aligned,
            "rsi":          rsi,
            "rsi_slope":    rsi_slope,
            "macd_bull":    macd_bull,
            "atr":          atr,
        }

    except Exception as exc:
        log.debug("_compute_fx_indicators error: %s", exc)
        return None


def score_fx_pair(symbol: str, regime: dict) -> dict | None:
    """
    Score a single FX pair on two dimensions (Momentum + Macro).

    Returns a scored dict compatible with equity signal_pipeline output,
    or None if data is unavailable or the FX session is inactive.
    """
    if not _is_fx_session():
        return None

    pair_cfg = FX_PAIRS.get(symbol)
    if pair_cfg is None:
        log.warning("score_fx_pair: unknown pair %s", symbol)
        return None

    try:
        import yfinance as yf
        df = yf.Ticker(pair_cfg["yf_ticker"]).history(period="5d", interval="1h", auto_adjust=True)
        if df is None or df.empty:
            return None
        # Flatten multi-level columns from newer yfinance
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
    except Exception as exc:
        log.debug("score_fx_pair %s data fetch error: %s", symbol, exc)
        return None

    ind = _compute_fx_indicators(df)
    if ind is None:
        return None

    # ── Dimension 1: MOMENTUM (0-25) ──────────────────────────────
    # EMA alignment + RSI + MACD
    mom_score = 0
    if ind["bull_aligned"]:
        mom_score += 10   # Full trend alignment
    elif ind["rsi"] > 55 and ind["macd_bull"]:
        mom_score += 6    # Partial bullish
    elif ind["rsi"] > 50:
        mom_score += 3    # Weak bias

    if ind["bear_aligned"]:
        mom_score = -10   # Bearish alignment (used for direction, score abs below)
    elif ind["rsi"] < 45 and not ind["macd_bull"]:
        mom_score = -6

    # Normalise direction and score
    mom_dir   = 1 if mom_score > 0 else (-1 if mom_score < 0 else 0)
    mom_pts   = min(25, abs(mom_score) + (3 if abs(ind["rsi_slope"]) > 0.5 else 0))

    # ── Dimension 2: MACRO ALIGNMENT (0-25) ───────────────────────
    # DXY direction: USD pairs and non-USD pairs respond opposite
    macro_pts = 0
    macro_dir = mom_dir  # default to momentum direction
    dxy_trend = regime.get("dxy_trend", "unknown")
    credit_stress = regime.get("credit_stress", False)

    # USD base pairs (USDJPY): DXY rising = bullish
    # USD quote pairs (EURUSD, GBPUSD, AUDUSD): DXY rising = bearish (USD up = pair down)
    is_usd_base = symbol.startswith("USD")

    if dxy_trend == "rising":
        dxy_aligned = mom_dir == (1 if is_usd_base else -1)
    elif dxy_trend == "falling":
        dxy_aligned = mom_dir == (-1 if is_usd_base else 1)
    else:
        dxy_aligned = False  # unknown DXY trend — no macro contribution

    if dxy_aligned:
        macro_pts += 15
    if credit_stress:
        # Risk-off: USD safe-haven bid (bullish USDJPY, bearish EURUSD)
        risk_off_dir = 1 if is_usd_base else -1
        if mom_dir == risk_off_dir:
            macro_pts += 10

    macro_pts = min(25, macro_pts)

    # ── Composite score ────────────────────────────────────────────
    total = mom_pts + macro_pts   # 0-50

    direction = "LONG" if mom_dir > 0 else ("SHORT" if mom_dir < 0 else "NEUTRAL")
    if total < 10:
        direction = "NEUTRAL"

    signal = "BUY" if direction == "LONG" else ("SELL" if direction == "SHORT" else "HOLD")
    if total >= 35:
        signal = "STRONG_" + signal.replace("HOLD", "BUY")

    return {
        "symbol":           symbol,
        "price":            round(ind["price"], 6),
        "score":            total,
        "direction":        direction,
        "signal":           signal,
        "instrument":       "fx",
        "atr":              round(ind["atr"], 6),
        "atr_daily":        round(ind["atr"] * 3.5, 6),  # proxy: 1h ATR × sqrt(3.5 sessions)
        "vol_ratio":        1.0,           # no volume concept for FX
        "score_breakdown":  {
            "fx_momentum": mom_pts,
            "fx_macro":    macro_pts,
        },
        "disabled_dimensions": [],
        "regime_router":    regime.get("regime_router", "unknown"),
        "universe_track":   "fx",
    }


def score_fx_universe(regime: dict) -> list[dict]:
    """
    Score all configured FX pairs. Returns list of scored dicts above threshold.
    Called from signal_pipeline after equity scoring.
    """
    from config import CONFIG
    if not CONFIG.get("fx_enabled", False):
        return []

    pairs = CONFIG.get("fx_pairs", list(FX_PAIRS.keys()))
    min_score = CONFIG.get("fx_min_score", 20)  # lower bar: only 2 dims, max 50

    results = []
    for symbol in pairs:
        try:
            scored = score_fx_pair(symbol, regime)
            if scored and scored["score"] >= min_score and scored["direction"] != "NEUTRAL":
                results.append(scored)
                log.info(
                    "FX %s: score=%d direction=%s (momentum=%d macro=%d)",
                    symbol, scored["score"], scored["direction"],
                    scored["score_breakdown"].get("fx_momentum", 0),
                    scored["score_breakdown"].get("fx_macro", 0),
                )
        except Exception as exc:
            log.debug("score_fx_universe %s error: %s", symbol, exc)

    return results
