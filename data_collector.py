# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  data_collector.py                         ║
# ║   Historical data downloader for ML training data.          ║
# ║                                                              ║
# ║   Sources (all FREE, no API key required):                   ║
# ║     1. yfinance — 5m bars (60 days), 1d bars (max history)  ║
# ║     2. Stooq    — unlimited daily OHLCV via CSV endpoint     ║
# ║     3. Yahoo Finance — fundamental data via yfinance         ║
# ║                                                              ║
# ║   Optional (API key for higher limits):                      ║
# ║     4. Alpha Vantage — 25 calls/day free tier                ║
# ║                                                              ║
# ║   Usage:                                                     ║
# ║     python data_collector.py                   # full run    ║
# ║     python data_collector.py --symbols AAPL TSLA             ║
# ║     python data_collector.py --intraday-only                 ║
# ║     python data_collector.py --daily-only                    ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np

log = logging.getLogger("decifer.data_collector")

# ── PATHS ────────────────────────────────────────────────────────
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = BASE_DIR / "data" / "historical"
INTRADAY_DIR = DATA_DIR / "intraday"
DAILY_DIR = DATA_DIR / "daily"
META_FILE = DATA_DIR / "collection_meta.json"

# ── DEFAULT UNIVERSE ─────────────────────────────────────────────
# Broad coverage across sectors + high-volume names for robust training data
DEFAULT_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "INTC", "CRM",
    # Semis
    "AVGO", "QCOM", "MU", "MRVL", "AMAT",
    # Finance
    "JPM", "BAC", "GS", "MS", "V", "MA",
    # Healthcare
    "UNH", "JNJ", "PFE", "ABBV", "LLY", "MRK",
    # Energy
    "XOM", "CVX", "COP", "SLB", "OXY",
    # Consumer
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD",
    # Industrials
    "CAT", "BA", "GE", "HON", "UPS",
    # ETFs (regime/sector data)
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "GLD", "TLT",
    # Volatility
    "VIX",
    # High-vol meme/momentum (useful for training on extreme regimes)
    "GME", "AMC", "PLTR", "SOFI", "RIVN", "LCID", "NIO",
]


def ensure_dirs():
    """Create data directories if they don't exist."""
    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)


def load_meta() -> dict:
    """Load collection metadata (last download timestamps, counts)."""
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            pass
    return {"last_run": None, "symbols": {}, "total_rows": 0}


def save_meta(meta: dict):
    """Persist collection metadata."""
    meta["last_run"] = datetime.now().isoformat()
    META_FILE.write_text(json.dumps(meta, indent=2))


# ═════════════════════════════════════════════════════════════════
# 1. YFINANCE — Intraday (5m bars, last 60 days)
# ═════════════════════════════════════════════════════════════════

def download_intraday_yf(symbol: str, interval: str = "5m") -> Optional[pd.DataFrame]:
    """
    Download intraday bars from yfinance.
    5m bars: max 60 days lookback.
    1m bars: max 7 days lookback.

    Returns DataFrame or None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        # 5m: "60d" is the max period yfinance allows
        period = "60d" if interval in ("5m", "15m") else "7d"
        df = ticker.history(period=period, interval=interval, prepost=True)

        if df is None or df.empty:
            log.warning(f"[yf-intraday] No data for {symbol} ({interval})")
            return None

        # Standardise columns
        df.index.name = "datetime"
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume"
        })
        df = df[["open", "high", "low", "close", "volume"]]
        df["symbol"] = symbol
        df["source"] = "yfinance"
        df["interval"] = interval

        log.info(f"[yf-intraday] {symbol} {interval}: {len(df)} bars "
                 f"({df.index.min()} → {df.index.max()})")
        return df

    except Exception as e:
        log.error(f"[yf-intraday] {symbol} failed: {e}")
        return None


# ═════════════════════════════════════════════════════════════════
# 2. YFINANCE — Daily (max history, typically 20+ years)
# ═════════════════════════════════════════════════════════════════

def download_daily_yf(symbol: str) -> Optional[pd.DataFrame]:
    """
    Download full daily history from yfinance.
    Typically returns 20+ years for major stocks.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="max", interval="1d")

        if df is None or df.empty:
            log.warning(f"[yf-daily] No data for {symbol}")
            return None

        df.index.name = "date"
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume"
        })
        # Keep dividends/splits if available for adjusted price calc
        keep_cols = ["open", "high", "low", "close", "volume"]
        if "Dividends" in df.columns:
            df = df.rename(columns={"Dividends": "dividends"})
            keep_cols.append("dividends")
        if "Stock Splits" in df.columns:
            df = df.rename(columns={"Stock Splits": "splits"})
            keep_cols.append("splits")

        df = df[keep_cols]
        df["symbol"] = symbol
        df["source"] = "yfinance"

        log.info(f"[yf-daily] {symbol}: {len(df)} bars "
                 f"({df.index.min().date()} → {df.index.max().date()})")
        return df

    except Exception as e:
        log.error(f"[yf-daily] {symbol} failed: {e}")
        return None


# ═════════════════════════════════════════════════════════════════
# 3. STOOQ — Daily OHLCV (unlimited, no API key)
# ═════════════════════════════════════════════════════════════════

def download_daily_stooq(symbol: str) -> Optional[pd.DataFrame]:
    """
    Download daily data from Stooq (Polish financial data provider).
    Free, unlimited, no API key. Good backup/cross-validation source.
    US stocks use format: AAPL.US
    """
    try:
        stooq_sym = f"{symbol}.US"
        url = f"https://stooq.com/q/d/l/?s={stooq_sym}&i=d"
        df = pd.read_csv(url)

        if df is None or df.empty or "Date" not in df.columns:
            log.warning(f"[stooq] No data for {symbol}")
            return None

        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
        df.index.name = "date"
        df.columns = [c.lower() for c in df.columns]
        df["symbol"] = symbol
        df["source"] = "stooq"

        log.info(f"[stooq] {symbol}: {len(df)} bars "
                 f"({df.index.min().date()} → {df.index.max().date()})")
        return df

    except Exception as e:
        log.error(f"[stooq] {symbol} failed: {e}")
        return None


# ═════════════════════════════════════════════════════════════════
# 4. ALPHA VANTAGE — Daily (25 calls/day free, 20yr history)
# ═════════════════════════════════════════════════════════════════

def download_daily_alphavantage(symbol: str, api_key: str = None) -> Optional[pd.DataFrame]:
    """
    Download full daily history from Alpha Vantage.
    Free tier: 25 calls/day. Set ALPHA_VANTAGE_KEY env var or pass api_key.
    Returns None if no API key available.
    """
    key = api_key or os.environ.get("ALPHA_VANTAGE_KEY")
    if not key:
        return None

    try:
        url = (f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
               f"&symbol={symbol}&outputsize=full&apikey={key}&datatype=csv")
        df = pd.read_csv(url)

        if df is None or df.empty or "timestamp" not in df.columns:
            log.warning(f"[alphavantage] No data for {symbol}")
            return None

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        df.index.name = "date"
        df.columns = [c.lower() for c in df.columns]
        df["symbol"] = symbol
        df["source"] = "alphavantage"

        log.info(f"[alphavantage] {symbol}: {len(df)} bars "
                 f"({df.index.min().date()} → {df.index.max().date()})")
        return df

    except Exception as e:
        log.error(f"[alphavantage] {symbol} failed: {e}")
        return None


# ═════════════════════════════════════════════════════════════════
# ENRICHMENT — Add technical features for ML training
# ═════════════════════════════════════════════════════════════════

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add commonly used ML training features to OHLCV data.
    These match the dimensions Decifer's signal engine uses.
    """
    if df is None or df.empty or len(df) < 50:
        return df

    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    v = df["volume"].values.astype(float)

    # Returns
    df["return_1"] = df["close"].pct_change(1)
    df["return_5"] = df["close"].pct_change(5)
    df["return_10"] = df["close"].pct_change(10)

    # Volatility
    df["atr_14"] = _atr(h, l, c, 14)
    df["volatility_20"] = df["return_1"].rolling(20).std()

    # Trend
    df["ema_9"] = pd.Series(c).ewm(span=9).mean().values
    df["ema_21"] = pd.Series(c).ewm(span=21).mean().values
    df["ema_50"] = pd.Series(c).ewm(span=50).mean().values
    df["ema_trend"] = np.where(df["ema_9"] > df["ema_21"], 1, -1)

    # Momentum
    df["rsi_14"] = _rsi(c, 14)
    df["mfi_14"] = _mfi(h, l, c, v, 14)

    # Volume
    df["vol_sma_20"] = pd.Series(v).rolling(20).mean().values
    df["vol_ratio"] = np.where(df["vol_sma_20"] > 0, v / df["vol_sma_20"], 1.0)

    # Bollinger Bands
    sma20 = pd.Series(c).rolling(20).mean()
    std20 = pd.Series(c).rolling(20).std()
    df["bb_upper"] = (sma20 + 2 * std20).values
    df["bb_lower"] = (sma20 - 2 * std20).values
    bb_range = df["bb_upper"] - df["bb_lower"]
    df["bb_position"] = np.where(bb_range > 0, (c - df["bb_lower"].values) / bb_range.values, 0.5)

    # VWAP (intraday only — approximated for daily as typical price × volume cumsum)
    tp = (h + l + c) / 3
    cum_tpv = np.cumsum(tp * v)
    cum_v = np.cumsum(v)
    df["vwap"] = np.where(cum_v > 0, cum_tpv / cum_v, c)
    df["vwap_dist"] = np.where(df["vwap"] > 0, (c - df["vwap"]) / df["vwap"] * 100, 0)

    # Regime labels (for ML target classification)
    df["regime"] = _label_regime(df)

    return df


def _atr(high, low, close, period=14):
    """Average True Range."""
    tr = np.maximum(high - low,
                    np.maximum(abs(high - np.roll(close, 1)),
                               abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = pd.Series(tr).rolling(period).mean().values
    return atr


def _rsi(close, period=14):
    """Relative Strength Index."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(period).mean().values
    avg_loss = pd.Series(loss).rolling(period).mean().values
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    return 100 - (100 / (1 + rs))


def _mfi(high, low, close, volume, period=14):
    """Money Flow Index (volume-weighted RSI)."""
    tp = (high + low + close) / 3
    mf = tp * volume
    delta = np.diff(tp, prepend=tp[0])
    pos_mf = np.where(delta > 0, mf, 0)
    neg_mf = np.where(delta < 0, mf, 0)
    pos_sum = pd.Series(pos_mf).rolling(period).sum().values
    neg_sum = pd.Series(neg_mf).rolling(period).sum().values
    ratio = np.where(neg_sum > 0, pos_sum / neg_sum, 100)
    return 100 - (100 / (1 + ratio))


def _label_regime(df: pd.DataFrame) -> pd.Series:
    """
    Label each bar with a market regime for ML training targets.
    Uses simple lookback return + volatility bucketing.
    """
    ret_20 = df["close"].pct_change(20)
    vol_20 = df["return_1"].rolling(20).std() if "return_1" in df.columns else pd.Series(0, index=df.index)

    conditions = [
        (ret_20 > 0.03) & (vol_20 < 0.02),    # Bull trending
        (ret_20 < -0.03) & (vol_20 < 0.02),    # Bear trending
        (vol_20 >= 0.03),                        # High volatility / panic
        (ret_20.abs() <= 0.03) & (vol_20 < 0.03),  # Choppy / range-bound
    ]
    labels = ["BULL_TRENDING", "BEAR_TRENDING", "PANIC", "CHOPPY"]
    return pd.Series(np.select(conditions, labels, default="UNKNOWN"), index=df.index)


# ═════════════════════════════════════════════════════════════════
# ORCHESTRATOR — Run full collection pipeline
# ═════════════════════════════════════════════════════════════════

def collect_all(symbols: list = None, intraday: bool = True, daily: bool = True,
                add_ml_features: bool = True) -> dict:
    """
    Main entry point: download historical data for all symbols.

    Args:
        symbols: List of tickers (default: DEFAULT_UNIVERSE)
        intraday: Download 5m bars (last 60 days)
        daily: Download daily bars (max history)
        add_ml_features: Compute technical features for ML training

    Returns:
        Summary dict with counts and paths.
    """
    ensure_dirs()
    meta = load_meta()
    symbols = symbols or DEFAULT_UNIVERSE

    total_rows = 0
    results = {"intraday": {}, "daily": {}, "errors": []}

    for i, sym in enumerate(symbols):
        log.info(f"[{i+1}/{len(symbols)}] Collecting {sym}...")

        # ── Intraday (5m) ──
        if intraday:
            df_5m = download_intraday_yf(sym, "5m")
            if df_5m is not None and not df_5m.empty:
                if add_ml_features:
                    df_5m = add_features(df_5m)
                path = INTRADAY_DIR / f"{sym}_5m.parquet"
                # Append to existing data if present
                if path.exists():
                    try:
                        existing = pd.read_parquet(path)
                        df_5m = pd.concat([existing, df_5m])
                        df_5m = df_5m[~df_5m.index.duplicated(keep="last")]
                        df_5m = df_5m.sort_index()
                    except Exception:
                        pass
                df_5m.to_parquet(path)
                results["intraday"][sym] = len(df_5m)
                total_rows += len(df_5m)

            # Brief pause to avoid yfinance rate limits
            time.sleep(0.5)

        # ── Daily ──
        if daily:
            # Primary: yfinance
            df_daily = download_daily_yf(sym)

            # Backup: Stooq (if yfinance fails or for cross-validation)
            if df_daily is None or df_daily.empty:
                df_daily = download_daily_stooq(sym)

            if df_daily is not None and not df_daily.empty:
                if add_ml_features:
                    df_daily = add_features(df_daily)
                path = DAILY_DIR / f"{sym}_1d.parquet"
                # Append
                if path.exists():
                    try:
                        existing = pd.read_parquet(path)
                        df_daily = pd.concat([existing, df_daily])
                        df_daily = df_daily[~df_daily.index.duplicated(keep="last")]
                        df_daily = df_daily.sort_index()
                    except Exception:
                        pass
                df_daily.to_parquet(path)
                results["daily"][sym] = len(df_daily)
                total_rows += len(df_daily)
            else:
                results["errors"].append(sym)

            time.sleep(0.3)

        # Update per-symbol metadata
        meta["symbols"][sym] = {
            "last_collected": datetime.now().isoformat(),
            "intraday_bars": results["intraday"].get(sym, 0),
            "daily_bars": results["daily"].get(sym, 0),
        }

    meta["total_rows"] = total_rows
    save_meta(meta)

    summary = {
        "symbols_processed": len(symbols),
        "intraday_symbols": len(results["intraday"]),
        "daily_symbols": len(results["daily"]),
        "total_rows": total_rows,
        "errors": results["errors"],
        "data_dir": str(DATA_DIR),
    }

    log.info(f"Collection complete: {summary['symbols_processed']} symbols, "
             f"{total_rows:,} total rows, {len(results['errors'])} errors")
    return summary


def get_training_dataset(symbols: list = None, interval: str = "1d") -> pd.DataFrame:
    """
    Load collected data as a single DataFrame ready for ML training.

    Args:
        symbols: Filter to specific symbols (default: all available)
        interval: "5m" for intraday, "1d" for daily

    Returns:
        Combined DataFrame with all features.
    """
    data_dir = INTRADAY_DIR if interval != "1d" else DAILY_DIR
    suffix = "_5m.parquet" if interval != "1d" else "_1d.parquet"

    frames = []
    for f in data_dir.glob(f"*{suffix}"):
        sym = f.stem.replace(suffix.replace(".parquet", ""), "")
        if symbols and sym not in symbols:
            continue
        try:
            df = pd.read_parquet(f)
            frames.append(df)
        except Exception as e:
            log.warning(f"Failed to load {f}: {e}")

    if not frames:
        log.warning("No training data found. Run collect_all() first.")
        return pd.DataFrame()

    combined = pd.concat(frames)
    log.info(f"Training dataset: {len(combined):,} rows, "
             f"{combined['symbol'].nunique()} symbols")
    return combined


# ═════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Decifer Historical Data Collector")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to download")
    parser.add_argument("--intraday-only", action="store_true", help="Only download intraday data")
    parser.add_argument("--daily-only", action="store_true", help="Only download daily data")
    parser.add_argument("--no-features", action="store_true", help="Skip ML feature computation")
    args = parser.parse_args()

    intraday = not args.daily_only
    daily = not args.intraday_only

    result = collect_all(
        symbols=args.symbols,
        intraday=intraday,
        daily=daily,
        add_ml_features=not args.no_features,
    )

    print(f"\n{'='*60}")
    print(f"  COLLECTION COMPLETE")
    print(f"  Symbols: {result['symbols_processed']}")
    print(f"  Intraday: {result['intraday_symbols']} symbols with 5m data")
    print(f"  Daily: {result['daily_symbols']} symbols with daily data")
    print(f"  Total rows: {result['total_rows']:,}")
    print(f"  Data dir: {result['data_dir']}")
    if result["errors"]:
        print(f"  Errors: {', '.join(result['errors'])}")
    print(f"{'='*60}")
