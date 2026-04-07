# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpaca_data.py                            ║
# ║   Single responsibility: fetch historical OHLCV bars from   ║
# ║   Alpaca REST API (SIP feed, split-adjusted).               ║
# ║                                                              ║
# ║   Used by: signals._safe_download                           ║
# ║   Nothing else lives here. No streaming, no trading logic.  ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import threading
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import CONFIG

log = logging.getLogger("decifer.alpaca_data")


# ── Lazy client singleton ─────────────────────────────────────────────────────
# Created once on first call. Reused for all subsequent requests.
# Thread-safe: double-checked lock guards initialisation.

_client = None
_client_lock = threading.Lock()


def _get_client():
    """Return a cached StockHistoricalDataClient, or None if keys not set."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        api_key    = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")
        if not api_key or not secret_key:
            return None
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            _client = StockHistoricalDataClient(api_key, secret_key)
            log.debug("alpaca_data: StockHistoricalDataClient initialised")
        except ImportError:
            log.debug("alpaca_data: alpaca-py not installed — pip install alpaca-py")
        except Exception as exc:
            log.debug(f"alpaca_data: client init failed — {exc}")
    return _client


# ── Period → start date ───────────────────────────────────────────────────────

def _period_to_start(period: str) -> datetime:
    """Convert yfinance-style period string to a UTC start datetime."""
    n_str = "".join(c for c in period if c.isdigit())
    unit  = "".join(c for c in period if c.isalpha()).lower()
    n = int(n_str) if n_str else 1

    if unit in ("y", "yr"):
        delta = timedelta(days=n * 366)   # covers leap years
    elif unit in ("mo", "month"):
        delta = timedelta(days=n * 32)
    else:                                  # "d" or bare number
        delta = timedelta(days=n + 2)      # +2 buffer for weekends

    return datetime.now(timezone.utc) - delta


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_bars(symbol: str, period: str = "60d",
               interval: str = "1d") -> pd.DataFrame | None:
    """
    Fetch historical OHLCV bars for one symbol from Alpaca REST.

    Uses the SIP consolidated tape (all US exchanges) and split-adjusted
    prices — same quality as what Algo Trader Plus streams in real time.

    Args:
        symbol:   Ticker e.g. "AAPL"
        period:   Lookback window in yfinance notation: "5d", "60d", "6mo", "1y"
        interval: Bar size: "1d" (daily), "1wk" (weekly), "1h", "5m", "1m"

    Returns:
        DataFrame with columns [Open, High, Low, Close, Volume] and UTC
        DatetimeIndex, or None if Alpaca is unavailable or request fails.
    """
    client = _get_client()
    if client is None:
        return None

    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf_map = {
            "1d":  TimeFrame.Day,
            "1wk": TimeFrame.Week,
            "1h":  TimeFrame.Hour,
            "5m":  TimeFrame(5,  TimeFrameUnit.Minute),
            "1m":  TimeFrame.Minute,
        }
        tf = tf_map.get(interval)
        if tf is None:
            log.debug(f"fetch_bars: unsupported interval '{interval}'")
            return None

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=_period_to_start(period),
            feed="sip",
            adjustment="split",
        )
        bars = client.get_stock_bars(request)
        df   = bars.df

        if df is None or df.empty:
            return None

        # Single-symbol response has MultiIndex (symbol, timestamp) — drop symbol level
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)

        # Alpaca returns lowercase columns; rename to canonical capitalised form
        rename = {
            "open":   "Open",
            "high":   "High",
            "low":    "Low",
            "close":  "Close",
            "volume": "Volume",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # Return only canonical OHLCV columns (drop Alpaca extras: trade_count, vwap)
        cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        return df[cols]

    except ImportError:
        log.debug("fetch_bars: alpaca-py not installed")
        return None
    except Exception as exc:
        log.debug(f"fetch_bars: {symbol} {interval}/{period} failed — {exc}")
        return None
