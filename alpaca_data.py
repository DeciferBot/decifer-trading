# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpaca_data.py                            ║
# ║   Single responsibility: fetch market data from Alpaca REST.║
# ║                                                              ║
# ║   Provides:                                                  ║
# ║     fetch_bars            — historical OHLCV (SIP)          ║
# ║     fetch_snapshots       — live price + 1d change          ║
# ║     fetch_snapshots_batched — chunked richer snapshots      ║
# ║     get_all_tradable_equities — enumerate US equities       ║
# ║                                                              ║
# ║   Used by: signals, universe_committed, universe_promoter   ║
# ║   No streaming, no trading logic.                           ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

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
        api_key = CONFIG.get("alpaca_api_key", "")
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
    unit = "".join(c for c in period if c.isalpha()).lower()
    n = int(n_str) if n_str else 1

    if unit in ("y", "yr"):
        delta = timedelta(days=n * 366)  # covers leap years
    elif unit in ("mo", "month"):
        delta = timedelta(days=n * 32)
    else:  # "d" or bare number
        delta = timedelta(days=n + 2)  # +2 buffer for weekends

    return datetime.now(UTC) - delta


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_bars(symbol: str, period: str = "60d", interval: str = "1d") -> pd.DataFrame | None:
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
            "1d": TimeFrame.Day,
            "1wk": TimeFrame.Week,
            "1h": TimeFrame.Hour,
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "1m": TimeFrame.Minute,
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
        df = bars.df

        if df is None or df.empty:
            return None

        # Single-symbol response has MultiIndex (symbol, timestamp) — drop symbol level
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)

        # Alpaca returns lowercase columns; rename to canonical capitalised form
        rename = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
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


def fetch_snapshots(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch live price + 1-day change for a batch of symbols via Alpaca snapshots.

    Returns:
        {symbol: {"price": float, "change_1d": float}} for each symbol that
        succeeded. Missing or failed symbols are simply absent from the dict.
        Returns {} if Alpaca is unavailable.
    """
    if not symbols:
        return {}
    client = _get_client()
    if client is None:
        return {}

    try:
        from alpaca.data.requests import StockSnapshotRequest

        request = StockSnapshotRequest(symbol_or_symbols=symbols, feed="sip")
        raw = client.get_stock_snapshot(request)
        result: dict[str, dict] = {}
        for sym, snap in raw.items():
            try:
                price = float(snap.latest_trade.price)
                prev_close = float(snap.previous_daily_bar.close) if snap.previous_daily_bar else None
                change_1d = ((price - prev_close) / prev_close) if prev_close else None
                result[sym] = {"price": price, "change_1d": change_1d}
            except Exception as exc:
                log.debug(f"fetch_snapshots: parse failed for {sym} — {exc}")
        return result
    except ImportError:
        log.debug("fetch_snapshots: alpaca-py not installed")
        return {}
    except Exception as exc:
        log.debug(f"fetch_snapshots: batch request failed — {exc}")
        return {}


# ── Rich batched snapshots (promoter needs volume + prior_close) ─────────────


def fetch_snapshots_batched(symbols: list[str], batch_size: int = 100) -> dict[str, dict]:
    """
    Chunked snapshot fetcher returning rich fields for the universe promoter.

    Returns per symbol:
      price            — latest_trade.price (live or last traded)
      prior_close      — previous_daily_bar.close
      today_open       — daily_bar.open (today's regular-session open, if open)
      today_high       — daily_bar.high
      today_low        — daily_bar.low
      today_volume     — daily_bar.volume (regular-session volume to date; 0 on weekends)
      prev_volume      — previous_daily_bar.volume (last regular session, always populated)
      minute_volume    — minute_bar.volume (last minute bar; premarket if pre-open)
      change_1d        — (price - prior_close) / prior_close
      gap_pct          — (today_open - prior_close) / prior_close if daily_bar else change_1d

    Missing symbols are absent from the returned dict. Batches of `batch_size`
    are used to stay under Alpaca URL-length / payload limits. On any batch
    failure the function logs and continues with the remaining batches — no
    global failure.
    """
    if not symbols:
        return {}
    client = _get_client()
    if client is None:
        return {}

    try:
        from alpaca.data.requests import StockSnapshotRequest
    except ImportError:
        log.debug("fetch_snapshots_batched: alpaca-py not installed")
        return {}

    out: dict[str, dict] = {}
    # Dedupe while preserving order
    seen: set[str] = set()
    unique_syms = [s for s in symbols if not (s in seen or seen.add(s))]

    for i in range(0, len(unique_syms), batch_size):
        chunk = unique_syms[i : i + batch_size]
        try:
            request = StockSnapshotRequest(symbol_or_symbols=chunk, feed="sip")
            raw = client.get_stock_snapshot(request)
        except Exception as exc:
            log.warning(f"fetch_snapshots_batched: batch {i // batch_size} failed — {exc}")
            continue

        for sym, snap in raw.items():
            try:
                price = float(snap.latest_trade.price) if snap.latest_trade else None
                prev_bar = snap.previous_daily_bar
                daily_bar = snap.daily_bar
                min_bar = snap.minute_bar

                prior_close = float(prev_bar.close) if prev_bar else None
                prev_volume = int(prev_bar.volume) if prev_bar else 0
                today_open = float(daily_bar.open) if daily_bar else None
                today_high = float(daily_bar.high) if daily_bar else None
                today_low = float(daily_bar.low) if daily_bar else None
                today_volume = int(daily_bar.volume) if daily_bar else 0
                minute_volume = int(min_bar.volume) if min_bar else 0

                change_1d = None
                gap_pct = None
                if price is not None and prior_close and prior_close > 0:
                    change_1d = (price - prior_close) / prior_close
                if today_open is not None and prior_close and prior_close > 0:
                    gap_pct = (today_open - prior_close) / prior_close
                elif change_1d is not None:
                    gap_pct = change_1d  # fallback: use intraday change as gap proxy

                out[sym] = {
                    "price": price,
                    "prior_close": prior_close,
                    "prev_volume": prev_volume,
                    "today_open": today_open,
                    "today_high": today_high,
                    "today_low": today_low,
                    "today_volume": today_volume,
                    "minute_volume": minute_volume,
                    "change_1d": change_1d,
                    "gap_pct": gap_pct,
                }
            except Exception as exc:
                log.debug(f"fetch_snapshots_batched: parse failed for {sym} — {exc}")

    return out


# ── Tradable equity enumeration (Alpaca assets endpoint) ─────────────────────

_trading_client = None
_trading_client_lock = threading.Lock()


def _get_trading_client():
    """Return a cached TradingClient, or None if keys not set."""
    global _trading_client
    if _trading_client is not None:
        return _trading_client
    with _trading_client_lock:
        if _trading_client is not None:
            return _trading_client
        api_key = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")
        if not api_key or not secret_key:
            return None
        try:
            from alpaca.trading.client import TradingClient

            # paper=True works for asset enumeration regardless of account env
            _trading_client = TradingClient(api_key, secret_key, paper=True)
            log.debug("alpaca_data: TradingClient initialised")
        except ImportError:
            log.debug("alpaca_data: alpaca-py not installed")
        except Exception as exc:
            log.debug(f"alpaca_data: trading client init failed — {exc}")
    return _trading_client


def get_all_tradable_equities() -> list[dict]:
    """
    Enumerate all tradable US equities via Alpaca /v2/assets.

    Filters to: status=ACTIVE, tradable=True, exchange in {NYSE, NASDAQ, AMEX,
    ARCA, BATS}, asset_class=us_equity. Excludes OTC by exchange filter.

    Returns a list of dicts:
      {symbol, name, exchange, marginable, shortable, fractionable, easy_to_borrow}

    Returns [] if Alpaca unavailable. Typical call returns 8000–11000 equities;
    downstream (universe_committed) ranks by dollar volume and keeps top N.
    """
    client = _get_trading_client()
    if client is None:
        return []

    try:
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        request = GetAssetsRequest(
            status=AssetStatus.ACTIVE,
            asset_class=AssetClass.US_EQUITY,
        )
        assets = client.get_all_assets(request)
    except ImportError:
        log.debug("get_all_tradable_equities: alpaca-py not installed")
        return []
    except Exception as exc:
        log.warning(f"get_all_tradable_equities: request failed — {exc}")
        return []

    allowed_exchanges = {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"}
    out: list[dict] = []
    for a in assets:
        try:
            if not getattr(a, "tradable", False):
                continue
            exch = str(getattr(a, "exchange", "")).upper()
            # alpaca-py returns an enum; .value gives the string
            if hasattr(a.exchange, "value"):
                exch = str(a.exchange.value).upper()
            if exch not in allowed_exchanges:
                continue
            sym = str(a.symbol)
            # Skip obvious non-commons: warrants, rights, units (heuristic)
            if any(sym.endswith(suf) for suf in (".WS", ".U", ".R", ".WT")):
                continue
            out.append(
                {
                    "symbol": sym,
                    "name": getattr(a, "name", "") or "",
                    "exchange": exch,
                    "marginable": bool(getattr(a, "marginable", False)),
                    "shortable": bool(getattr(a, "shortable", False)),
                    "fractionable": bool(getattr(a, "fractionable", False)),
                    "easy_to_borrow": bool(getattr(a, "easy_to_borrow", False)),
                }
            )
        except Exception as exc:
            log.debug(f"get_all_tradable_equities: row parse failed — {exc}")
    log.info(f"get_all_tradable_equities: {len(out)} tradable US equities")
    return out
