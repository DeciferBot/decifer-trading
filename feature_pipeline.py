# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  feature_pipeline.py                        ║
# ║   Pure feature computation — no IO, no side effects.         ║
# ║                                                              ║
# ║   Contract:                                                  ║
# ║   • All functions are pure: same input → same output         ║
# ║   • No disk reads or writes                                  ║
# ║   • Raises FeatureError (not silent NaN) on bad input        ║
# ║   • run() is the single entry point for the full feature set ║
# ╚══════════════════════════════════════════════════════════════╝

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("decifer.feature_pipeline")


class FeatureError(Exception):
    """Raised when feature computation fails due to bad or missing input."""


# ── Individual Indicators ─────────────────────────────────────────


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Average True Range.

    Note: tr[0] is explicitly set to (high[0] - low[0]) to avoid the
    np.roll wrap-around on the first bar, which would incorrectly compare
    bar 0's high/low against the last close in the array.
    """
    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - np.roll(close, 1)),
            np.abs(low - np.roll(close, 1)),
        ),
    )
    tr[0] = high[0] - low[0]
    return pd.Series(tr).rolling(period).mean().values


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Relative Strength Index.

    Returns 50 (neutral) when both avg_gain and avg_loss are zero — i.e., a
    perfectly flat price series (halted stock, micro-cap, etc.). The naive
    formula produces ~99 in this case via 100 - 100/(1+0), which is wrong.
    """
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).rolling(period).mean().values
    avg_loss = pd.Series(loss).rolling(period).mean().values

    flat = (avg_gain == 0) & (avg_loss == 0)

    with np.errstate(divide="ignore", invalid="ignore"):
        # rs=100 when avg_loss==0 → RSI≈99 for a pure uptrend (correct)
        # Only the flat case (both==0) is overridden to 50 below
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)

    result = 100 - (100 / (1 + rs))
    result[flat] = 50.0  # flat price (no gain, no loss) → neutral RSI, not ~99
    return result


def mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Money Flow Index (volume-weighted RSI).

    Logs a warning if more than 10% of bars have zero volume, since those
    bars contribute mf=0 regardless of price action and silently suppress
    the index.
    """
    zero_vol = (volume == 0).sum()
    if zero_vol > len(volume) * 0.10:
        log.warning(
            f"MFI: {zero_vol}/{len(volume)} bars have zero volume — "
            "those bars contribute mf=0 regardless of price action"
        )

    tp = (high + low + close) / 3
    mf_raw = tp * volume
    delta = np.diff(tp, prepend=tp[0])
    pos_mf = np.where(delta > 0, mf_raw, 0.0)
    neg_mf = np.where(delta < 0, mf_raw, 0.0)
    pos_sum = pd.Series(pos_mf).rolling(period).sum().values
    neg_sum = pd.Series(neg_mf).rolling(period).sum().values

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(neg_sum > 0, pos_sum / neg_sum, 100.0)

    return 100 - (100 / (1 + ratio))


def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """
    VWAP approximated as cumulative(tp × volume) / cumulative(volume).

    Safe denominator: zeros in cum_v are replaced with 1.0 before division,
    preventing numpy 0/0 RuntimeWarning. The np.where mask still selects
    the close fallback for those positions, so values are correct.

    Logs a warning if all bars have zero volume (data quality issue, not
    a code issue).
    """
    tp = (high + low + close) / 3
    cum_tpv = np.cumsum(tp * volume)
    cum_v = np.cumsum(volume)
    cum_v_safe = np.where(cum_v > 0, cum_v, 1.0)
    result = np.where(cum_v > 0, cum_tpv / cum_v_safe, close)

    if (cum_v == 0).all():
        log.warning(
            "VWAP: all bars have zero cumulative volume — VWAP equals close price throughout (data quality issue)"
        )

    return result


def regime_label(df: pd.DataFrame) -> pd.Series:
    """
    Label each bar with a market regime for ML training targets.

    Raises FeatureError if 'return_1' is missing — do not silently substitute
    zeros, which would force every bar into CHOPPY/UNKNOWN.
    """
    if "return_1" not in df.columns:
        raise FeatureError(
            "'return_1' column is required for regime labelling. Compute returns before calling regime_label()."
        )

    ret_20 = df["close"].pct_change(20)
    vol_20 = df["return_1"].rolling(20).std()

    conditions = [
        (ret_20 > 0.03) & (vol_20 < 0.02),
        (ret_20 < -0.03) & (vol_20 < 0.02),
        (vol_20 >= 0.03),
        (ret_20.abs() <= 0.03) & (vol_20 < 0.03),
    ]
    labels = ["TRENDING_UP", "TRENDING_DOWN", "CAPITULATION", "RANGE_BOUND"]
    return pd.Series(np.select(conditions, labels, default="UNKNOWN"), index=df.index)


# ── Pipeline Entry Point ──────────────────────────────────────────


def run(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the full ML feature set from a clean OHLCV DataFrame.

    Pure function — no IO. Raises FeatureError on any computation failure
    so that partial or corrupt feature sets are never written to disk.

    Input must have columns: open, high, low, close, volume.
    Returns a new DataFrame (copy of input + feature columns).
    """
    if df is None or df.empty or len(df) < 50:
        return df

    try:
        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)

        out = df.copy()

        # Returns
        out["return_1"] = out["close"].pct_change(1)
        out["return_5"] = out["close"].pct_change(5)
        out["return_10"] = out["close"].pct_change(10)

        # Volatility
        out["atr_14"] = atr(h, l, c, 14)
        out["volatility_20"] = out["return_1"].rolling(20).std()

        # Trend
        out["ema_9"] = pd.Series(c).ewm(span=9).mean().values
        out["ema_21"] = pd.Series(c).ewm(span=21).mean().values
        out["ema_50"] = pd.Series(c).ewm(span=50).mean().values
        out["ema_trend"] = np.where(out["ema_9"] > out["ema_21"], 1, -1)

        # Momentum
        out["rsi_14"] = rsi(c, 14)
        out["mfi_14"] = mfi(h, l, c, v, 14)

        # Volume
        out["vol_sma_20"] = pd.Series(v).rolling(20).mean().values
        out["vol_ratio"] = np.where(out["vol_sma_20"] > 0, v / out["vol_sma_20"], 1.0)

        # Bollinger Bands
        sma20 = pd.Series(c).rolling(20).mean()
        std20 = pd.Series(c).rolling(20).std()
        out["bb_upper"] = (sma20 + 2 * std20).values
        out["bb_lower"] = (sma20 - 2 * std20).values
        bb_range = out["bb_upper"] - out["bb_lower"]
        out["bb_position"] = np.where(
            bb_range > 0,
            (c - out["bb_lower"].values) / bb_range.values,
            0.5,
        )

        # VWAP
        out["vwap"] = vwap(h, l, c, v)
        out["vwap_dist"] = np.where(
            out["vwap"] > 0,
            (c - out["vwap"]) / out["vwap"] * 100,
            0,
        )

        # Regime (depends on return_1 computed above)
        out["regime"] = regime_label(out)

        return out

    except FeatureError:
        raise  # preserve original message
    except Exception as exc:
        raise FeatureError(f"Feature computation failed: {exc}") from exc
