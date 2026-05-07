# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpaca_stream.py                          ║
# ║   Single responsibility: maintain live market-data caches   ║
# ║   from the Alpaca WebSocket (SIP feed).                     ║
# ║                                                             ║
# ║   Four module-level singletons — written by AlpacaBarStream ║
# ║   via WebSocket events, read by signals / orders:           ║
# ║     BAR_CACHE   — 1-minute OHLCV bars (→ 5m aggregation)   ║
# ║     QUOTE_CACHE — real-time bid/ask + spread_pct            ║
# ║     DAILY_BAR_CACHE — intraday running daily bar            ║
# ║     HALT_CACHE — set of symbols whose trading is halted     ║
# ║                                                             ║
# ║   Nothing else lives here. No trading logic. No signals.    ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import threading
import time

import pandas as pd

log = logging.getLogger("decifer.alpaca_stream")

# Maximum 1-minute bars kept per symbol (~3 trading days at 1m = ~1170 bars)
_MAX_1M_BARS = 1200

# Regime anchor symbols — always subscribed regardless of trading universe.
# BAR_CACHE for these symbols is used by get_market_regime() for real-time
# intraday momentum detection (rally/sell-off override of the signal router).
STREAM_ANCHORS: frozenset[str] = frozenset(["SPY", "QQQ"])


# ═══════════════════════════════════════════════════════════════
# BAR CACHE — 1-minute OHLCV, aggregated to 5m on demand
# ═══════════════════════════════════════════════════════════════


class _BarCache:
    """
    Thread-safe in-memory cache of 1-minute OHLCV bars per symbol.

    Written to by AlpacaBarStream on each WebSocket bar event.
    Read from by signals.py via get_5m(symbol).

    Columns stored: Open, High, Low, Close, Volume, vwap (canonical capitalisation).
    Index: UTC DatetimeIndex.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, pd.DataFrame] = {}

    def update(self, symbol: str, bar: dict) -> None:
        """Append one 1-minute bar to the cache. Trims to _MAX_1M_BARS."""
        with self._lock:
            row = pd.DataFrame(
                [
                    {
                        "Open": bar["open"],
                        "High": bar["high"],
                        "Low": bar["low"],
                        "Close": bar["close"],
                        "Volume": bar["volume"],
                        "vwap": bar.get("vwap"),
                    }
                ],
                index=pd.to_datetime([bar["timestamp"]]),
            )

            if symbol not in self._data:
                self._data[symbol] = row
            else:
                existing = self._data[symbol]
                # Drop duplicate timestamp if bar is an update to last bar
                if not existing.empty and existing.index[-1] == row.index[0]:
                    existing = existing.iloc[:-1]
                self._data[symbol] = pd.concat([existing, row])
                if len(self._data[symbol]) > _MAX_1M_BARS:
                    self._data[symbol] = self._data[symbol].iloc[-_MAX_1M_BARS:]

    def get_5m(self, symbol: str) -> pd.DataFrame | None:
        """
        Return 5-minute OHLCV bars for symbol, aggregated from 1-minute cache.
        Returns None if symbol is not in cache or cache has fewer than 5 bars.
        """
        with self._lock:
            df = self._data.get(symbol)
            if df is None or len(df) < 5:
                return None
            df = df.copy()

        # Resample 1m → 5m
        agg = {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
        if "vwap" in df.columns and df["vwap"].notna().any():
            # Volume-weighted average of per-minute VWAPs
            agg["vwap"] = "mean"

        df_5m = df.resample("5min").agg(agg).dropna(subset=["Close"])
        return df_5m if len(df_5m) >= 5 else None

    def symbols(self) -> set:
        """Return set of symbols currently in cache."""
        with self._lock:
            return set(self._data.keys())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# Module-level singleton — imported directly by signals.py
BAR_CACHE = _BarCache()


# ═══════════════════════════════════════════════════════════════
# QUOTE CACHE — real-time bid/ask + spread
# ═══════════════════════════════════════════════════════════════


class _QuoteCache:
    """
    Thread-safe cache of the latest bid/ask quote per symbol.

    Written to by AlpacaBarStream on each WebSocket quote event.
    Read from by orders_core.py before order submission to gate on spread.

    spread_pct = (ask - bid) / mid  — fraction, not percent.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: dict[str, dict] = {}

    def update(self, symbol: str, bid: float, ask: float) -> None:
        """Store the latest quote. Computes spread_pct from bid/ask."""
        if bid <= 0 or ask <= 0 or bid > ask:
            return
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid if mid > 0 else None
        with self._lock:
            self._quotes[symbol] = {
                "bid": bid,
                "ask": ask,
                "spread_pct": spread_pct,
                "ts": time.time(),
            }

    def get_spread_pct(self, symbol: str) -> float | None:
        """Return spread_pct for symbol, or None if no quote on record."""
        with self._lock:
            q = self._quotes.get(symbol)
            return q["spread_pct"] if q else None

    def get(self, symbol: str) -> dict | None:
        """Return full quote dict {bid, ask, spread_pct} or None."""
        with self._lock:
            return dict(self._quotes[symbol]) if symbol in self._quotes else None


# Module-level singleton — imported by orders_core.py
QUOTE_CACHE = _QuoteCache()


# ═══════════════════════════════════════════════════════════════
# DAILY BAR CACHE — intraday running daily OHLCV bar
# ═══════════════════════════════════════════════════════════════


class _DailyBarCache:
    """
    Thread-safe cache of today's running OHLCV bar per symbol.

    Updated by AlpacaBarStream on each WebSocket dailyBar event.
    Provides same-day context (e.g. today's high/low, total volume).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bars: dict[str, dict] = {}

    def update(self, symbol: str, bar: dict) -> None:
        """Replace today's bar for symbol. Alpaca sends full updated bar."""
        with self._lock:
            self._bars[symbol] = {
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close"),
                "volume": bar.get("volume"),
                "timestamp": bar.get("timestamp"),
            }

    def get(self, symbol: str) -> dict | None:
        """Return today's running daily bar dict, or None if not yet received."""
        with self._lock:
            d = self._bars.get(symbol)
            return dict(d) if d else None


# Module-level singleton
DAILY_BAR_CACHE = _DailyBarCache()


# ═══════════════════════════════════════════════════════════════
# HALT CACHE — real-time trading halt / resume status
# ═══════════════════════════════════════════════════════════════


class _HaltCache:
    """
    Thread-safe set of symbols whose trading is currently halted.

    Updated by AlpacaBarStream on each WebSocket tradingStatus event.
    Read from by orders_core.py before any order submission.

    Alpaca status_code "T" = trading (normal). Any other code = halted.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._halted: set[str] = set()

    def update(self, symbol: str, status_code: str) -> None:
        """Mark symbol as halted or trading based on status_code."""
        with self._lock:
            if status_code == "T":
                self._halted.discard(symbol)
            else:
                self._halted.add(symbol)
                log.info(f"HaltCache: {symbol} halted (status_code={status_code!r})")

    def is_halted(self, symbol: str) -> bool:
        """Return True if symbol is currently halted."""
        with self._lock:
            return symbol in self._halted

    def halted_symbols(self) -> set:
        """Return snapshot of all currently halted symbols."""
        with self._lock:
            return set(self._halted)


# Module-level singleton — imported by orders_core.py
HALT_CACHE = _HaltCache()


# ═══════════════════════════════════════════════════════════════
# ALPACA BAR STREAM — WebSocket subscriber + cache writer
# ═══════════════════════════════════════════════════════════════


class AlpacaBarStream:
    """
    Subscribes to Alpaca WebSocket SIP stream for a list of symbols.
    Channels: bars (1m), dailyBars, quotes, tradingStatuses.
    Updates the four module-level singletons on each event.

    Runs in a daemon background thread — does not block the main event loop.

    Usage:
        stream = AlpacaBarStream()
        stream.start(symbols)   # non-blocking
        ...
        stream.stop()
    """

    def __init__(self) -> None:
        self._stream = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self, symbols: list[str]) -> None:
        """Start streaming for symbols. Non-blocking.
        STREAM_ANCHORS (SPY, QQQ) are always included regardless of what is passed.
        """
        if self._running:
            log.debug("AlpacaBarStream: already running")
            return
        full_symbols = list(set(symbols) | STREAM_ANCHORS)
        if not full_symbols:
            log.warning("AlpacaBarStream: no symbols provided — stream not started")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            args=(full_symbols,),
            daemon=True,
            name="alpaca-bar-stream",
        )
        self._thread.start()
        log.info(f"AlpacaBarStream: started for {len(full_symbols)} symbols (anchors: {sorted(STREAM_ANCHORS)})")

    def stop(self) -> None:
        """Stop the stream gracefully."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
        log.info("AlpacaBarStream: stopped")

    def update_symbols(self, symbols: list[str]) -> None:
        """
        Update the subscription list. No-op if the symbol set is unchanged —
        avoids disconnecting the WebSocket every scan when the universe is stable.
        STREAM_ANCHORS are always merged in before the comparison.
        """
        full_symbols = list(set(symbols) | STREAM_ANCHORS)
        if set(full_symbols) == self.symbols():
            log.debug("AlpacaBarStream: universe unchanged — skipping stream restart")
            return
        if self._running:
            old_thread = self._thread
            self.stop()
            if old_thread and old_thread.is_alive():
                old_thread.join(timeout=3.0)
        self.start(full_symbols)

    def symbols(self) -> set:
        """Return the set of symbols currently in BAR_CACHE (proxy for subscriptions)."""
        return BAR_CACHE.symbols()

    def _run(self, symbols: list[str]) -> None:
        """Background thread: connect to Alpaca WebSocket and stream all channels."""
        my_thread = threading.current_thread()
        from config import CONFIG

        api_key = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")

        if not api_key or not secret_key:
            log.warning("AlpacaBarStream: ALPACA_API_KEY / ALPACA_SECRET_KEY not set — streaming disabled")
            self._running = False
            return

        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.live import StockDataStream
        except ImportError:
            log.error("AlpacaBarStream: alpaca-py not installed — run: pip3 install alpaca-py")
            self._running = False
            return

        # ── Handlers defined once — reused across reconnects ─────
        async def on_bar(bar) -> None:
            BAR_CACHE.update(
                bar.symbol,
                {
                    "timestamp": bar.timestamp,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "vwap": getattr(bar, "vwap", None),
                },
            )
            log.debug(f"AlpacaBarStream: {bar.symbol} 1m close={bar.close:.2f}")

        async def on_daily_bar(bar) -> None:
            DAILY_BAR_CACHE.update(
                bar.symbol,
                {
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "timestamp": bar.timestamp,
                },
            )

        async def on_quote(quote) -> None:
            bid = getattr(quote, "bid_price", 0) or 0
            ask = getattr(quote, "ask_price", 0) or 0
            QUOTE_CACHE.update(quote.symbol, bid, ask)

        async def on_status(status) -> None:
            code = getattr(status, "status_code", "T") or "T"
            HALT_CACHE.update(status.symbol, code)

        # ── Reconnect loop with exponential backoff ───────────────
        _BASE_WAIT = 5
        _MAX_WAIT = 300
        _MAX_TRIES = 10
        attempt = 0

        while self._running and attempt < _MAX_TRIES and (self._thread is None or self._thread is my_thread):
            stream_start = time.time()
            try:
                self._stream = StockDataStream(api_key, secret_key, feed=DataFeed.SIP)
                self._stream.subscribe_bars(on_bar, *symbols)
                self._stream.subscribe_daily_bars(on_daily_bar, *symbols)
                self._stream.subscribe_quotes(on_quote, *symbols)
                self._stream.subscribe_trading_statuses(on_status, *symbols)
                self._stream.run()  # blocks until stop() is called or error
            except Exception as exc:
                if not self._running:
                    break  # intentional stop() call — exit cleanly
                try:
                    self._stream.stop()
                except Exception:
                    pass
                alive_secs = time.time() - stream_start
                if alive_secs > 60:
                    attempt = 0  # stream ran healthily — reset failure counter
                attempt += 1
                wait = min(_BASE_WAIT * (2 ** attempt), _MAX_WAIT)
                log.warning(
                    "AlpacaBarStream: stream error (attempt %d/%d), retrying in %.0fs: %s",
                    attempt, _MAX_TRIES, wait, exc,
                )
                time.sleep(wait)

        if self._running:
            log.critical(
                "AlpacaBarStream: max reconnect attempts (%d) reached — stream permanently dead", _MAX_TRIES
            )
        self._running = False
