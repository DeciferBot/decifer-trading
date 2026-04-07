# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpaca_stream.py                          ║
# ║   Single responsibility: maintain a live bar cache from      ║
# ║   Alpaca WebSocket. Written by AlpacaBarStream, read by      ║
# ║   signals.py fetch_multi_timeframe().                        ║
# ║                                                              ║
# ║   Nothing else lives here. No trading logic. No signals.     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import threading
import logging
import pandas as pd

log = logging.getLogger("decifer.alpaca_stream")

# Maximum 1-minute bars kept per symbol (~3 trading days at 1m = ~1170 bars)
_MAX_1M_BARS = 1200


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
            row = pd.DataFrame([{
                'Open':   bar['open'],
                'High':   bar['high'],
                'Low':    bar['low'],
                'Close':  bar['close'],
                'Volume': bar['volume'],
                'vwap':   bar.get('vwap'),
            }], index=pd.to_datetime([bar['timestamp']]))

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
            'Open':   'first',
            'High':   'max',
            'Low':    'min',
            'Close':  'last',
            'Volume': 'sum',
        }
        if 'vwap' in df.columns and df['vwap'].notna().any():
            # Volume-weighted average of per-minute VWAPs
            agg['vwap'] = 'mean'

        df_5m = df.resample('5min').agg(agg).dropna(subset=['Close'])
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


class AlpacaBarStream:
    """
    Subscribes to Alpaca WebSocket 1-minute bar stream for a list of symbols.
    Updates BAR_CACHE on each bar event.

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
        """Start streaming 1-minute bars for symbols. Non-blocking."""
        if self._running:
            log.debug("AlpacaBarStream: already running")
            return
        if not symbols:
            log.warning("AlpacaBarStream: no symbols provided — stream not started")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            args=(list(symbols),),
            daemon=True,
            name="alpaca-bar-stream",
        )
        self._thread.start()
        log.info(f"AlpacaBarStream: started for {len(symbols)} symbols")

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
        Update the subscription list. Restarts the stream with new symbols.
        Called when the active universe changes between scan cycles.
        """
        if self._running:
            self.stop()
        self.start(symbols)

    def _run(self, symbols: list[str]) -> None:
        """Background thread: connect to Alpaca WebSocket and stream bars."""
        from config import CONFIG
        api_key    = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")

        if not api_key or not secret_key:
            log.warning("AlpacaBarStream: ALPACA_API_KEY / ALPACA_SECRET_KEY not set — streaming disabled")
            self._running = False
            return

        try:
            from alpaca.data.live import StockDataStream

            self._stream = StockDataStream(api_key, secret_key)

            async def on_bar(bar) -> None:
                BAR_CACHE.update(bar.symbol, {
                    'timestamp': bar.timestamp,
                    'open':      bar.open,
                    'high':      bar.high,
                    'low':       bar.low,
                    'close':     bar.close,
                    'volume':    bar.volume,
                    'vwap':      getattr(bar, 'vwap', None),
                })
                log.debug(f"AlpacaBarStream: {bar.symbol} bar close={bar.close:.2f}")

            self._stream.subscribe_bars(on_bar, *symbols)
            self._stream.run()  # blocks until stop() is called

        except ImportError:
            log.error("AlpacaBarStream: alpaca-py not installed — run: pip3 install alpaca-py")
            self._running = False
        except Exception as exc:
            log.error(f"AlpacaBarStream: stream failed — {exc}")
            self._running = False
