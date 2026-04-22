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


# ── Session detection (inline — avoids circular import with risk.py) ─────────


def _volume_session(now_et) -> str:
    """Classify the current trading session for volume-baseline selection.

    Returns one of: "CLOSED", "PRE_MARKET", "REGULAR", "AFTER_HOURS".
    Intentionally minimal — only the boundaries that matter for volume math.
    """
    from datetime import time as _time
    t = now_et.time()
    if t < _time(4, 0):   return "CLOSED"
    if t < _time(9, 30):  return "PRE_MARKET"   # 04:00–09:30 ET (330 min window)
    if t < _time(16, 0):  return "REGULAR"       # 09:30–16:00 ET (390 min window)
    if t < _time(20, 0):  return "AFTER_HOURS"   # 16:00–20:00 ET (240 min window)
    return "CLOSED"


# ── Explicit date-range bar fetcher (extended-hours baseline) ─────────────────


def _fetch_bars_range(
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
    interval: str = "1m",
) -> "pd.DataFrame | None":
    """Fetch OHLCV bars for a specific UTC datetime range.

    Unlike fetch_bars() (which uses period strings), this accepts explicit
    start/end datetimes — needed to pull after-hours / pre-market windows
    without pulling the full day.  Same SIP feed and split-adjustment as
    fetch_bars().
    """
    client = _get_client()
    if client is None:
        return None
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf_map = {
            "1m": TimeFrame.Minute,
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "1h": TimeFrame.Hour,
            "1d": TimeFrame.Day,
        }
        tf = tf_map.get(interval)
        if tf is None:
            log.debug(f"_fetch_bars_range: unsupported interval '{interval}'")
            return None

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start_utc,
            end=end_utc,
            feed="sip",
            adjustment="split",
        )
        bars = client.get_stock_bars(request)
        df = bars.df
        if df is None or df.empty:
            return None

        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)

        rename = {"open": "Open", "high": "High", "low": "Low",
                  "close": "Close", "volume": "Volume"}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        return df[cols]

    except Exception as exc:
        log.debug(f"_fetch_bars_range: {symbol} failed — {exc}")
        return None


def _get_extended_session_baseline(
    symbol: str,
    session: str,
    lookback_days: int = 20,
) -> "float | None":
    """Compute the 20-day average volume for a specific extended-hours session.

    Fetches 30 calendar days of 1-minute bars covering the target session window,
    filters to session hours in ET, groups by date, and returns the mean daily
    volume over the last `lookback_days` non-zero trading days.

    This is one Alpaca call (wide datetime range), not one call per day.
    """
    import pytz

    et = pytz.timezone("America/New_York")
    now_utc = datetime.now(UTC)

    if session == "PRE_MARKET":
        session_start_h, session_end_h = 4, 9    # 04:00–09:30 ET
    else:  # AFTER_HOURS
        session_start_h, session_end_h = 16, 20  # 16:00–20:00 ET

    range_start = (now_utc - timedelta(days=30)).replace(
        hour=session_start_h, minute=0, second=0, microsecond=0
    )
    df = _fetch_bars_range(symbol, range_start, now_utc, interval="1m")
    if df is None or df.empty:
        return None

    # Convert index to ET and filter to session hours only
    df_et = df.copy()
    df_et.index = df_et.index.tz_convert(et)
    mask = (df_et.index.hour >= session_start_h) & (df_et.index.hour < session_end_h)
    df_et = df_et[mask]
    if df_et.empty:
        return None

    # Sum per calendar day, keep last N non-zero days
    daily_vols = df_et["Volume"].groupby(df_et.index.date).sum()
    daily_vols = daily_vols[daily_vols > 0].tail(lookback_days)
    if daily_vols.empty:
        return None

    return float(daily_vols.mean())


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


def get_intraday_hod(symbol: str) -> float | None:
    """
    Return the high-of-day for symbol from market open (09:30 ET) to now.

    Uses 1-minute bars. Returns the max(High) seen so far today.
    Returns None if Alpaca unavailable or bars empty.

    Used by TradeContext to compute hod_distance_pct at entry time.
    """
    try:
        from datetime import date as _date
        import pytz

        et = pytz.timezone("America/New_York")
        today = _date.today().isoformat()

        # fetch_bars with 1m interval for today — period "1d" gives ~1 trading day
        df = fetch_bars(symbol, period="1d", interval="1m")
        if df is None or df.empty:
            return None

        # Filter to today only (handles cases where 1d includes yesterday's close)
        df.index = pd.to_datetime(df.index, utc=True)
        today_bars = df[df.index.date == pd.Timestamp(today).date()]
        if today_bars.empty:
            today_bars = df  # fallback to all returned bars

        return float(today_bars["High"].max())
    except Exception as exc:
        log.debug(f"get_intraday_hod: {symbol} failed — {exc}")
        return None


def get_sector_etf_context(etf_ticker: str) -> dict | None:
    """
    Compute sector ETF context for POSITION trade classification.

    Returns dict with:
        etf (str)
        above_50d (bool)              — closing price > 50-day SMA
        return_3m_vs_spy (float)      — ETF 3-month return minus SPY 3-month return (pct)
        days_since_breakout (int|None)— trading days since ETF last crossed above 50-day MA
                                        None if already above for entire 90d window

    Returns None if data unavailable.
    Used by TradeContext for POSITION and SWING sector condition checks.
    """
    try:
        # Fetch 90 trading days (~4.5 months) of daily bars for ETF and SPY
        etf_df = fetch_bars(etf_ticker, period="6mo", interval="1d")
        spy_df = fetch_bars("SPY",       period="6mo", interval="1d")

        if etf_df is None or etf_df.empty or len(etf_df) < 50:
            return None

        # 50-day SMA
        close = etf_df["Close"]
        sma50 = close.rolling(50).mean()
        above_50d = bool(close.iloc[-1] > sma50.iloc[-1])

        # 3-month return (≈63 trading days)
        lookback = min(63, len(close) - 1)
        etf_3m = (close.iloc[-1] / close.iloc[-lookback] - 1) * 100

        spy_3m = 0.0
        if spy_df is not None and not spy_df.empty and len(spy_df) >= lookback:
            spy_close = spy_df["Close"]
            spy_3m = (spy_close.iloc[-1] / spy_close.iloc[-lookback] - 1) * 100

        return_vs_spy = round(etf_3m - spy_3m, 2)

        # Days since last 50d crossover (when price crossed from below to above)
        days_since_breakout = None
        if above_50d and len(close) >= 50:
            # Walk back to find when it crossed above
            above_series = close > sma50
            for i in range(1, min(90, len(above_series))):
                idx = -(i + 1)
                if not above_series.iloc[idx]:
                    days_since_breakout = i
                    break

        return {
            "etf":                etf_ticker,
            "above_50d":          above_50d,
            "return_3m_vs_spy":   return_vs_spy,
            "days_since_breakout": days_since_breakout,
        }
    except Exception as exc:
        log.debug(f"get_sector_etf_context: {etf_ticker} failed — {exc}")
        return None


def get_intraday_vwap(symbol: str) -> float | None:
    """
    Compute session VWAP from today's 1-minute bars.
    VWAP = sum(typical_price * volume) / sum(volume)
    where typical_price = (High + Low + Close) / 3

    Returns VWAP as float, or None if unavailable.
    Used by TradeContext.build_context() as fast-path VWAP when the
    signal engine hasn't passed one in.
    """
    try:
        df = fetch_bars(symbol, period="1d", interval="1m")
        if df is None or df.empty:
            return None

        import pandas as pd
        df.index = pd.to_datetime(df.index, utc=True)

        from datetime import date as _date
        today_bars = df[df.index.date == _date.today()]
        if today_bars.empty:
            today_bars = df  # fallback

        typical = (today_bars["High"] + today_bars["Low"] + today_bars["Close"]) / 3
        vol = today_bars["Volume"]
        total_vol = vol.sum()
        if total_vol == 0:
            return None
        vwap = float((typical * vol).sum() / total_vol)
        return round(vwap, 4)
    except Exception as exc:
        log.debug("get_intraday_vwap: %s failed — %s", symbol, exc)
        return None


def get_relative_volume(symbol: str) -> float | None:
    """
    Compute today's relative volume vs the 20-day session-matched baseline.

    Session dispatch:
      REGULAR    — existing formula: today_vol / (avg_daily_vol × elapsed/390)
      AFTER_HOURS — today's AH vol / (20-day AH avg × elapsed/240)
      PRE_MARKET  — today's PM vol / (20-day PM avg × elapsed/330)
      CLOSED      — returns None

    The extended-hours baseline is computed from the SAME session window in
    prior days, so 1.0× means "typical after-hours activity for this symbol"
    rather than "0.05× of a regular session day."

    Returns float (e.g. 1.5 means 1.5× expected), or None if unavailable.
    Used by TradeContext.build_context() to populate rel_volume.
    """
    try:
        import pytz
        from datetime import date as _date

        et = pytz.timezone("America/New_York")
        now_et = pd.Timestamp.now(tz=et)
        session = _volume_session(now_et)

        if session == "CLOSED":
            return None

        # ── Regular session — existing logic unchanged ─────────────────────────
        if session == "REGULAR":
            intraday = fetch_bars(symbol, period="1d", interval="1m")
            if intraday is None or intraday.empty:
                return None
            intraday.index = pd.to_datetime(intraday.index, utc=True)
            today_bars = intraday[intraday.index.date == _date.today()]
            if today_bars.empty:
                today_bars = intraday
            today_vol = float(today_bars["Volume"].sum())
            if today_vol == 0:
                return None
            daily = fetch_bars(symbol, period="30d", interval="1d")
            if daily is None or len(daily) < 5:
                return None
            avg_daily_vol = float(daily["Volume"].iloc[:-1].mean())  # exclude today
            if avg_daily_vol == 0:
                return None
            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            elapsed = max(1.0, (now_et - market_open).total_seconds() / 60.0)
            elapsed = min(elapsed, 390.0)
            expected_vol = avg_daily_vol * (elapsed / 390.0)
            if expected_vol == 0:
                return None
            return round(today_vol / expected_vol, 2)

        # ── Extended-hours path ────────────────────────────────────────────────
        if session == "PRE_MARKET":
            session_start_h, window_min = 4, 330    # 04:00–09:30 ET
        else:  # AFTER_HOURS
            session_start_h, window_min = 16, 240   # 16:00–20:00 ET

        # Today's volume so far in this session
        session_open_et = now_et.replace(
            hour=session_start_h, minute=0, second=0, microsecond=0
        )
        session_start_utc = session_open_et.astimezone(UTC)
        today_bars_df = _fetch_bars_range(
            symbol, session_start_utc, datetime.now(UTC), interval="1m"
        )
        if today_bars_df is None or today_bars_df.empty:
            return None
        today_vol = float(today_bars_df["Volume"].sum())
        if today_vol == 0:
            return None

        # 20-day session-matched baseline
        baseline = _get_extended_session_baseline(symbol, session)
        if not baseline:
            return None

        # Elapsed minutes into session — require ≥5 min to avoid noise at open
        elapsed = (now_et - session_open_et).total_seconds() / 60.0
        if elapsed < 5:
            return None
        elapsed = min(elapsed, float(window_min))

        expected_vol = baseline * (elapsed / float(window_min))
        if expected_vol == 0:
            return None
        return round(today_vol / expected_vol, 2)

    except Exception as exc:
        log.debug("get_relative_volume: %s failed — %s", symbol, exc)
        return None


def get_52wk_high(symbol: str) -> float | None:
    """
    Return the 52-week high (max daily High over last 252 trading days).
    Used by TradeContext to compute ath_distance_pct.
    Returns None if unavailable.
    """
    try:
        df = fetch_bars(symbol, period="1y", interval="1d")
        if df is None or df.empty:
            return None
        return float(df["High"].max())
    except Exception as exc:
        log.debug("get_52wk_high: %s failed — %s", symbol, exc)
        return None


def get_stock_above_200d(symbol: str) -> bool | None:
    """
    Return True if the stock's latest close is above its 200-day SMA.
    Used by TradeContext to flag long-term trend alignment for POSITION trades.
    Returns None if insufficient data.
    """
    try:
        df = fetch_bars(symbol, period="1y", interval="1d")
        if df is None or df.empty or len(df) < 200:
            return None
        close = df["Close"]
        sma200 = close.rolling(200).mean()
        return bool(close.iloc[-1] > sma200.iloc[-1])
    except Exception as exc:
        log.debug("get_stock_above_200d: %s failed — %s", symbol, exc)
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
