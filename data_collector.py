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
# ║   Inventor: AMIT CHOPRA                                      ║
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

import raw_store
import feature_pipeline
from raw_store import DataQualityError  # re-export for callers
from feature_pipeline import FeatureError  # re-export for callers

log = logging.getLogger("decifer.data_collector")

# ── PATHS ────────────────────────────────────────────────────────
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# Legacy paths — existing parquets remain readable during migration
DATA_DIR = BASE_DIR / "data" / "historical"
INTRADAY_DIR = DATA_DIR / "intraday"
DAILY_DIR = DATA_DIR / "daily"
META_FILE = DATA_DIR / "collection_meta.json"

# Tiered storage — new writes go here
FEATURES_DIR = BASE_DIR / "data" / "features"
FEATURES_INTRADAY_DIR = FEATURES_DIR / "intraday"
FEATURES_DAILY_DIR = FEATURES_DIR / "daily"

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


def _fetch_with_retry(fn, *args, retries: int = 3, backoff: float = 1.5, label: str = "", **kwargs):
    """Call fn(*args, **kwargs) up to `retries` times with exponential backoff.
    Re-raises the last exception when all attempts are exhausted."""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                log.warning(
                    f"{label} attempt {attempt + 1}/{retries} failed: {exc} "
                    f"— retrying in {wait:.1f}s"
                )
                time.sleep(wait)
    raise last_exc


def ensure_dirs():
    """Create data directories if they don't exist."""
    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    FEATURES_INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    FEATURES_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    raw_store.ensure_dirs()


def load_meta() -> dict:
    """Load collection metadata (last download timestamps, counts)."""
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception as exc:
            log.error(
                f"Failed to parse {META_FILE.name}: {exc} — "
                "resetting metadata to defaults. Previous collection state is lost."
            )
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
        df = _fetch_with_retry(
            ticker.history,
            period=period, interval=interval, prepost=True,
            label=f"[yf-intraday] {symbol}",
        )

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
        df = _fetch_with_retry(
            ticker.history,
            period="max", interval="1d",
            label=f"[yf-daily] {symbol}",
        )

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
        df = _fetch_with_retry(pd.read_csv, url, label=f"[stooq] {symbol}")

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
        df = _fetch_with_retry(pd.read_csv, url, label=f"[alphavantage] {symbol}")

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
# 5. ALPHA VANTAGE — Earnings Calendar (PEAD dimension)
# ═════════════════════════════════════════════════════════════════

def fetch_earnings_calendar(symbol: str, api_key: str = None) -> dict | None:
    """
    Fetch the next earnings date for a symbol from Alpha Vantage.

    Free tier: 25 calls/day — sufficient since earnings dates change quarterly.
    Returns a dict with 'symbol', 'report_date', 'fiscal_end', 'estimate', 'currency'
    or None if unavailable or no API key set.

    Callers: signals.py PEAD dimension caches this via _PEAD_CACHE.
    """
    from config import CONFIG
    key = api_key or CONFIG.get("alpha_vantage_key") or os.environ.get("ALPHA_VANTAGE_KEY")
    if not key:
        return None

    try:
        url = (f"https://www.alphavantage.co/query"
               f"?function=EARNINGS_CALENDAR&symbol={symbol}&horizon=3month&apikey={key}")
        resp = _fetch_with_retry(
            lambda u: __import__('requests').get(u, timeout=10),
            url,
            label=f"[earnings] {symbol}"
        )
        if resp is None or resp.status_code != 200:
            return None

        lines = resp.text.strip().splitlines()
        # Response is CSV: symbol,name,reportDate,fiscalDateEnding,estimate,currency
        if len(lines) < 2:
            return None

        import csv
        reader = csv.DictReader(lines)
        for row in reader:
            if row.get("symbol", "").upper() == symbol.upper():
                return {
                    "symbol":      symbol,
                    "report_date": row.get("reportDate", ""),
                    "fiscal_end":  row.get("fiscalDateEnding", ""),
                    "estimate":    row.get("estimate", ""),
                    "currency":    row.get("currency", "USD"),
                    "source":      "alphavantage",
                }
        return None

    except Exception as e:
        log.debug(f"[earnings] {symbol} failed: {e}")
        return None


# ═════════════════════════════════════════════════════════════════
# ENRICHMENT — Add technical features for ML training
# ═════════════════════════════════════════════════════════════════

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add ML training features to OHLCV data.
    Backward-compatible wrapper around feature_pipeline.run().
    Direct callers (tests, legacy code) continue to work unchanged.
    """
    return feature_pipeline.run(df)


# ── Internal indicator shims — kept for test backward compatibility ──

def _atr(high, low, close, period=14):
    """Shim: delegates to feature_pipeline.atr()."""
    return feature_pipeline.atr(high, low, close, period)


def _rsi(close, period=14):
    """Shim: delegates to feature_pipeline.rsi()."""
    return feature_pipeline.rsi(close, period)


def _mfi(high, low, close, volume, period=14):
    """Shim: delegates to feature_pipeline.mfi()."""
    return feature_pipeline.mfi(high, low, close, volume, period)


def _label_regime(df: pd.DataFrame) -> pd.Series:
    """Shim: delegates to feature_pipeline.regime_label()."""
    return feature_pipeline.regime_label(df)


# ═════════════════════════════════════════════════════════════════
# ORCHESTRATOR — Run full collection pipeline
# ═════════════════════════════════════════════════════════════════

def _write_features(df: pd.DataFrame, symbol: str, timeframe: str) -> int:
    """
    Compute features and write to the features store.
    Returns the number of rows written, or 0 on failure.
    Raises FeatureError so the caller can decide whether to continue or abort.
    """
    feat_dir = FEATURES_INTRADAY_DIR if timeframe == "5m" else FEATURES_DAILY_DIR
    feat_path = feat_dir / f"{symbol}_{timeframe}.parquet"

    enriched = feature_pipeline.run(df)  # raises FeatureError on bad input

    # Append to existing features file if present
    if feat_path.exists():
        existing = pd.read_parquet(feat_path)
        enriched = pd.concat([existing, enriched])
        enriched = enriched[~enriched.index.duplicated(keep="last")]
        enriched = enriched.sort_index()

    enriched.to_parquet(feat_path)
    return len(enriched)


def collect_all(symbols: list = None, intraday: bool = True, daily: bool = True,
                add_ml_features: bool = True) -> dict:
    """
    Main entry point: download historical data for all symbols.

    Pipeline per symbol:
      1. Download raw OHLCV from provider
      2. Validate and write to data/raw/  (raises on failure — no silent pass)
      3. Compute features via feature_pipeline.run()
      4. Write enriched data to data/features/

    Args:
        symbols: List of tickers (default: DEFAULT_UNIVERSE)
        intraday: Download 5m bars (last 60 days)
        daily: Download daily bars (max history)
        add_ml_features: Compute and store ML features (default: True)

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
                try:
                    raw_store.write(sym, "5m", df_5m)
                    raw_df = raw_store.read(sym, "5m")
                    if add_ml_features:
                        n = _write_features(raw_df, sym, "5m")
                    else:
                        n = len(raw_df)
                    results["intraday"][sym] = n
                    total_rows += n
                except (DataQualityError, FeatureError) as exc:
                    log.error(f"[{sym}] intraday collection failed: {exc}")
                    results["errors"].append(f"{sym}/5m")

            time.sleep(0.5)

        # ── Daily ──
        if daily:
            df_daily = download_daily_yf(sym)
            if df_daily is None or df_daily.empty:
                df_daily = download_daily_stooq(sym)

            if df_daily is not None and not df_daily.empty:
                try:
                    raw_store.write(sym, "1d", df_daily)
                    raw_df = raw_store.read(sym, "1d")
                    if add_ml_features:
                        n = _write_features(raw_df, sym, "1d")
                    else:
                        n = len(raw_df)
                    results["daily"][sym] = n
                    total_rows += n
                except (DataQualityError, FeatureError) as exc:
                    log.error(f"[{sym}] daily collection failed: {exc}")
                    results["errors"].append(f"{sym}/1d")
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
        "data_dir": str(FEATURES_DIR),
    }

    log.info(
        f"Collection complete: {summary['symbols_processed']} symbols, "
        f"{total_rows:,} total rows, {len(results['errors'])} errors"
    )
    return summary


def get_training_dataset(symbols: list = None, interval: str = "1d") -> pd.DataFrame:
    """
    Load feature-enriched data as a single DataFrame ready for ML training.
    Reads from data/features/ (tiered store). Falls back to data/historical/
    for symbols not yet migrated to the new store.

    Args:
        symbols: Filter to specific symbols (default: all available)
        interval: "5m" for intraday, "1d" for daily

    Returns:
        Combined DataFrame with all features.
    """
    feat_dir = FEATURES_INTRADAY_DIR if interval != "1d" else FEATURES_DAILY_DIR
    legacy_dir = INTRADAY_DIR if interval != "1d" else DAILY_DIR
    suffix = f"_{interval}.parquet"

    frames = []

    # Prefer features/ store; fall back to historical/ for unmigrated symbols
    for f in sorted(feat_dir.glob(f"*{suffix}")):
        sym = f.stem[: -len(f"_{interval}")]
        if symbols and sym not in symbols:
            continue
        try:
            frames.append(pd.read_parquet(f))
        except Exception as e:
            log.warning(f"Failed to load features/{f.name}: {e}")

    # Legacy fallback — symbols not yet in features/ store
    migrated = {f.stem[: -len(f"_{interval}")] for f in feat_dir.glob(f"*{suffix}")}
    remaining = set(symbols or []) - migrated if symbols else set()
    for sym in remaining:
        legacy_path = legacy_dir / f"{sym}{suffix}"
        if legacy_path.exists():
            try:
                frames.append(pd.read_parquet(legacy_path))
                log.debug(f"[{sym}] loaded from legacy historical/ store")
            except Exception as e:
                log.warning(f"Failed to load legacy {legacy_path.name}: {e}")

    if not frames:
        log.warning(
            "No training data found in features/ or historical/. "
            "Run collect_all() first."
        )
        return pd.DataFrame()

    combined = pd.concat(frames)
    log.info(
        f"Training dataset: {len(combined):,} rows, "
        f"{combined['symbol'].nunique() if 'symbol' in combined.columns else '?'} symbols"
    )
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
