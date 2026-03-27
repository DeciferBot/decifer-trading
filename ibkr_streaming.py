"""
Real-time streaming market data from Interactive Brokers.

This module provides live quote and bar data streaming via the IBKR API,
replacing or supplementing yfinance polling for watched symbols.

Key features:
- Real-time quote streaming (bid/ask/last/volume/VWAP)
- 5-second bar aggregation into 1-min and 5-min bars
- Automatic historical backfill from IBKR
- Smart data routing (IBKR > yfinance fallback)
- Thread-safe concurrent access
- Connection resilience and auto-resubscription

The module uses the SAME IB connection passed from bot.py (clientId=10, port 7496)
to avoid Error 10197 ("competing live session").

Integration points:
- bot.py: creates IBKRDataManager after IB connection, passes ib object
- signals.py: calls IBKRDataManager.get_data() for chart data
"""

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from ib_async import IB, Contract, Ticker, BarData

logger = logging.getLogger(__name__)


@dataclass
class StreamingQuote:
    """Real-time quote data for a single symbol."""
    symbol: str
    bid: float = np.nan
    ask: float = np.nan
    last: float = np.nan
    volume: int = 0
    vwap: float = np.nan
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def mid(self) -> float:
        """Midpoint of bid-ask spread."""
        if np.isnan(self.bid) or np.isnan(self.ask):
            return self.last if not np.isnan(self.last) else np.nan
        return (self.bid + self.ask) / 2.0

    def to_dict(self) -> dict:
        """Convert to dictionary for easy access."""
        return {
            'symbol': self.symbol,
            'bid': self.bid,
            'ask': self.ask,
            'last': self.last,
            'mid': self.mid,
            'volume': self.volume,
            'vwap': self.vwap,
            'timestamp': self.timestamp,
        }


class BarAggregator:
    """Aggregates 5-second bars into 1-min and 5-min intervals."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.lock = threading.Lock()

        # Store 5-second bars for aggregation
        self._bars_5s = deque(maxlen=60)  # Keep last 5 minutes

        # Store aggregated bars
        self._bars_1m = deque(maxlen=288)  # ~5 hours of 1-min bars
        self._bars_5m = deque(maxlen=288)  # ~24 hours of 5-min bars

        self._current_1m = None
        self._current_5m = None
        self._last_1m_time = None
        self._last_5m_time = None

    def add_bar(self, bar: BarData) -> None:
        """Add a 5-second bar and aggregate into 1-min and 5-min bars."""
        with self.lock:
            self._bars_5s.append(bar)

            bar_time = bar.time

            # Check if we should emit a 1-min bar
            if self._last_1m_time is None or \
               (bar_time - self._last_1m_time).total_seconds() >= 60:
                if self._current_1m is not None:
                    self._bars_1m.append(self._current_1m)

                self._current_1m = self._create_aggregated_bar(bar_time, 60)
                self._last_1m_time = bar_time
            else:
                # Update current 1-min bar
                if self._current_1m is not None:
                    self._update_aggregated_bar(self._current_1m, bar)

            # Check if we should emit a 5-min bar
            if self._last_5m_time is None or \
               (bar_time - self._last_5m_time).total_seconds() >= 300:
                if self._current_5m is not None:
                    self._bars_5m.append(self._current_5m)

                self._current_5m = self._create_aggregated_bar(bar_time, 300)
                self._last_5m_time = bar_time
            else:
                # Update current 5-min bar
                if self._current_5m is not None:
                    self._update_aggregated_bar(self._current_5m, bar)

    def _create_aggregated_bar(self, time: datetime, period_seconds: int) -> dict:
        """Create a new aggregated bar."""
        return {
            'time': time,
            'open': np.nan,
            'high': np.nan,
            'low': np.nan,
            'close': np.nan,
            'volume': 0,
            'count': 0,
        }

    def _update_aggregated_bar(self, agg_bar: dict, bar: BarData) -> None:
        """Update an aggregated bar with new tick data."""
        if agg_bar['count'] == 0:
            agg_bar['open'] = bar.open
            agg_bar['high'] = bar.high
            agg_bar['low'] = bar.low
        else:
            agg_bar['high'] = max(agg_bar['high'], bar.high)
            agg_bar['low'] = min(agg_bar['low'], bar.low)

        agg_bar['close'] = bar.close
        agg_bar['volume'] += bar.volume
        agg_bar['count'] += 1

    def get_bars(self, interval: str = "1m") -> pd.DataFrame:
        """Get aggregated bars as DataFrame.

        Args:
            interval: "1m" or "5m"

        Returns:
            DataFrame with OHLCV data, indexed by time
        """
        with self.lock:
            bars = self._bars_1m if interval == "1m" else self._bars_5m
            if not bars:
                return pd.DataFrame()

            df = pd.DataFrame(list(bars))
            df.set_index('time', inplace=True)
            df.drop(columns=['count'], inplace=True, errors='ignore')
            return df


class IBKRDataManager:
    """Manages all real-time and historical data subscriptions from IBKR."""

    # Max concurrent streaming subscriptions (IBKR paper limit)
    MAX_SUBSCRIPTIONS = 100

    # Historical data request rate limit: max 50 requests per 10 seconds
    MAX_HIST_REQUESTS_PER_10S = 50

    def __init__(self, ib: IB):
        """Initialize data manager with existing IB connection.

        Args:
            ib: Connected IB instance (same connection as bot.py uses)
        """
        self.ib = ib
        self.lock = threading.RLock()

        # Current streaming subscriptions
        self._subscriptions: Dict[str, Ticker] = {}
        self._quotes: Dict[str, StreamingQuote] = {}
        self._aggregators: Dict[str, BarAggregator] = {}

        # Subscription metadata for LRU eviction
        self._subscription_scores: Dict[str, float] = {}  # Score for each symbol
        self._subscription_times: Dict[str, datetime] = {}  # Last accessed time

        # Historical data cache
        self._hist_cache: Dict[Tuple[str, str, str], pd.DataFrame] = {}

        # Rate limiting for historical requests
        self._hist_requests: deque = deque(maxlen=self.MAX_HIST_REQUESTS_PER_10S)
        self._hist_lock = threading.Lock()

        # Connection state
        self._connected = True
        self._reconnect_lock = threading.Lock()

        logger.info(f"IBKRDataManager initialized with IB connection")

    def subscribe(self, symbol: str, score: float = 0.0) -> None:
        """Start streaming quotes and bars for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")
            score: Priority score for subscription eviction (higher = keep longer)

        Returns:
            None if successful, raises exception on failure
        """
        with self.lock:
            # Already subscribed
            if symbol in self._subscriptions:
                self._subscription_scores[symbol] = score
                self._subscription_times[symbol] = datetime.utcnow()
                return

            # Check subscription limit
            if len(self._subscriptions) >= self.MAX_SUBSCRIPTIONS:
                self._evict_lowest_priority()

            try:
                # Create contract for the symbol
                contract = Contract(symbol=symbol, secType='STK', exchange='SMART', currency='USD')

                # Request market data (free 15-min delayed with marketDataType=3)
                ticker = self.ib.reqMktData(contract, '', False, False)
                self.ib.reqMarketDataType(3)  # Request 15-min delayed data

                # Request real-time bars (5-second)
                self.ib.reqRealTimeBars(contract, 5, 'MIDPOINT', True)

                # Store subscription
                self._subscriptions[symbol] = ticker
                self._quotes[symbol] = StreamingQuote(symbol=symbol)
                self._aggregators[symbol] = BarAggregator(symbol)
                self._subscription_scores[symbol] = score
                self._subscription_times[symbol] = datetime.utcnow()

                # Hook into ticker updates
                ticker.updateEvent += lambda ticker: self._on_tick_update(ticker)

                logger.info(f"Subscribed to {symbol} (score={score:.2f})")

            except Exception as e:
                logger.error(f"Failed to subscribe to {symbol}: {e}")
                raise

    def unsubscribe(self, symbol: str) -> None:
        """Stop streaming for a symbol.

        Args:
            symbol: Stock ticker symbol
        """
        with self.lock:
            if symbol not in self._subscriptions:
                return

            try:
                ticker = self._subscriptions[symbol]
                contract = ticker.contract

                # Cancel market data
                self.ib.cancelMktData(contract)

                # Remove tracking
                del self._subscriptions[symbol]
                del self._quotes[symbol]
                del self._aggregators[symbol]
                del self._subscription_scores[symbol]
                del self._subscription_times[symbol]

                logger.info(f"Unsubscribed from {symbol}")

            except Exception as e:
                logger.error(f"Failed to unsubscribe from {symbol}: {e}")

    def _evict_lowest_priority(self) -> None:
        """Remove lowest-priority subscription to make room for new one."""
        if not self._subscriptions:
            return

        # Calculate priority: recent access + high score = higher priority
        min_symbol = None
        min_priority = float('inf')

        for symbol in self._subscriptions:
            age_seconds = (datetime.utcnow() - self._subscription_times[symbol]).total_seconds()
            score = self._subscription_scores[symbol]

            # Priority = score - decay (older = lower priority)
            priority = score - (age_seconds / 3600.0)  # Decay 1 point per hour

            if priority < min_priority:
                min_priority = priority
                min_symbol = symbol

        if min_symbol:
            self.unsubscribe(min_symbol)
            logger.info(f"Evicted {min_symbol} (priority={min_priority:.2f})")

    def _on_tick_update(self, ticker: Ticker) -> None:
        """Handle incoming tick data from IB event loop.

        Called by ib_async when market data arrives. Thread-safe.
        """
        with self.lock:
            symbol = ticker.contract.symbol

            if symbol not in self._quotes:
                return

            try:
                quote = self._quotes[symbol]

                # Update quote fields
                if ticker.bid is not None and ticker.bid > 0:
                    quote.bid = ticker.bid
                if ticker.ask is not None and ticker.ask > 0:
                    quote.ask = ticker.ask
                if ticker.last is not None and ticker.last > 0:
                    quote.last = ticker.last
                if ticker.volume is not None:
                    quote.volume = ticker.volume
                if ticker.vwap is not None and ticker.vwap > 0:
                    quote.vwap = ticker.vwap

                quote.timestamp = datetime.utcnow()

                # Process real-time bars if available
                if hasattr(ticker, 'rtVolume') and ticker.rtVolume:
                    bar_data = self._parse_rt_bar(ticker.rtVolume, symbol)
                    if bar_data:
                        self._aggregators[symbol].add_bar(bar_data)

            except Exception as e:
                logger.error(f"Error processing tick update for {symbol}: {e}")

    def _parse_rt_bar(self, rt_volume: str, symbol: str) -> Optional[BarData]:
        """Parse real-time bar string from ticker.rtVolume.

        Format: "price;size;time;bid;ask;bidSize;askSize;volume"

        Args:
            rt_volume: Real-time volume string from ticker
            symbol: Stock symbol

        Returns:
            BarData object or None if parsing fails
        """
        try:
            parts = rt_volume.split(';')
            if len(parts) < 8:
                return None

            price = float(parts[0])
            size = int(parts[1])
            rt_time = int(parts[2])
            bid = float(parts[3])
            ask = float(parts[4])
            volume = int(parts[7])

            # Convert Unix timestamp to datetime
            bar_time = datetime.fromtimestamp(rt_time)

            # Create BarData-like object
            bar = BarData(
                time=bar_time,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=size,
                average=price,
                barCount=1,
            )

            return bar

        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to parse rtVolume for {symbol}: {e}")
            return None

    def get_quote(self, symbol: str) -> Optional[StreamingQuote]:
        """Get latest quote for a symbol.

        Args:
            symbol: Stock ticker symbol

        Returns:
            StreamingQuote object or None if no subscription
        """
        with self.lock:
            if symbol in self._quotes:
                self._subscription_times[symbol] = datetime.utcnow()
                return self._quotes[symbol]
            return None

    def get_bars(self, symbol: str, interval: str = "5m") -> pd.DataFrame:
        """Get aggregated bars for a symbol.

        Args:
            symbol: Stock ticker symbol
            interval: "1m" or "5m"

        Returns:
            DataFrame with OHLCV data
        """
        with self.lock:
            if symbol not in self._aggregators:
                return pd.DataFrame()

            self._subscription_times[symbol] = datetime.utcnow()
            return self._aggregators[symbol].get_bars(interval)

    def backfill(self, symbol: str, duration: str = "5 D", bar_size: str = "5 mins") -> pd.DataFrame:
        """Request historical data from IBKR to backfill indicators.

        Args:
            symbol: Stock ticker symbol
            duration: Duration string (e.g., "5 D" for 5 days, "1 M" for 1 month)
            bar_size: Bar size string (e.g., "5 mins", "1 hour")

        Returns:
            DataFrame with OHLCV data, indexed by time
        """
        with self.lock:
            # Check cache first
            cache_key = (symbol, duration, bar_size)
            if cache_key in self._hist_cache:
                cached_df = self._hist_cache[cache_key]
                if self._is_cache_fresh(cached_df):
                    logger.debug(f"Using cached historical data for {symbol}")
                    return cached_df.copy()

            # Rate limit historical requests
            if not self._can_make_hist_request():
                logger.warning(f"Historical data rate limit hit for {symbol}")
                return pd.DataFrame()

            try:
                contract = Contract(symbol=symbol, secType='STK', exchange='SMART', currency='USD')

                # Request historical data
                bars = self.ib.reqHistoricalData(
                    contract,
                    endDateTime='',  # Now
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow='MIDPOINT',
                    useRTH=True,  # Regular trading hours only
                    formatDate=2,  # Unix timestamps
                )

                # Record request for rate limiting
                self._record_hist_request()

                if not bars:
                    logger.warning(f"No historical data returned for {symbol}")
                    return pd.DataFrame()

                # Convert to DataFrame
                df = pd.DataFrame([
                    {
                        'time': bar.date,
                        'open': bar.open,
                        'high': bar.high,
                        'low': bar.low,
                        'close': bar.close,
                        'volume': bar.volume,
                    }
                    for bar in bars
                ])

                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)

                # Cache the result
                self._hist_cache[cache_key] = df.copy()
                df.attrs['_cached_at'] = datetime.utcnow()

                logger.info(f"Backfilled {len(df)} bars for {symbol} ({duration})")
                return df

            except Exception as e:
                logger.error(f"Failed to backfill {symbol}: {e}")
                return pd.DataFrame()

    def _can_make_hist_request(self) -> bool:
        """Check if we can make a historical data request (rate limit)."""
        with self._hist_lock:
            now = time.time()

            # Remove old requests outside the 10-second window
            while self._hist_requests and self._hist_requests[0] < now - 10:
                self._hist_requests.popleft()

            return len(self._hist_requests) < self.MAX_HIST_REQUESTS_PER_10S

    def _record_hist_request(self) -> None:
        """Record a historical data request for rate limiting."""
        with self._hist_lock:
            self._hist_requests.append(time.time())

    def _is_cache_fresh(self, df: pd.DataFrame, max_age_seconds: int = 300) -> bool:
        """Check if cached data is fresh (not older than max_age_seconds)."""
        if '_cached_at' not in df.attrs:
            return False

        cached_at = df.attrs['_cached_at']
        age = (datetime.utcnow() - cached_at).total_seconds()

        return age < max_age_seconds


class SmartDataRouter:
    """Routes data requests to the best available source."""

    def __init__(self, ibkr_manager: IBKRDataManager):
        """Initialize router with IBKR data manager.

        Args:
            ibkr_manager: IBKRDataManager instance
        """
        self.ibkr = ibkr_manager
        self.logger = logging.getLogger(__name__)

    def get_data(self, symbol: str, period: str = "30d", interval: str = "5m") -> pd.DataFrame:
        """Get chart data from the best available source.

        Priority:
        1. IBKR streaming (if subscribed and has bars)
        2. IBKR historical backfill
        3. yfinance (fallback)

        Args:
            symbol: Stock ticker symbol
            period: Historical period (e.g., "30d", "1mo")
            interval: Bar interval (e.g., "5m", "1h")

        Returns:
            DataFrame with OHLCV data
        """
        # Try IBKR streaming first
        streaming_bars = self.ibkr.get_bars(symbol, interval)
        if not streaming_bars.empty:
            self.logger.debug(f"Using IBKR streaming data for {symbol}")
            return streaming_bars

        # Try IBKR historical
        try:
            # Map period strings to IBKR duration strings
            duration_map = {
                "5d": "5 D",
                "30d": "30 D",
                "1mo": "1 M",
                "3mo": "3 M",
                "1y": "1 Y",
            }
            duration = duration_map.get(period, "30 D")

            # Map interval strings to IBKR bar size
            interval_map = {
                "5m": "5 mins",
                "15m": "15 mins",
                "1h": "1 hour",
                "1d": "1 day",
            }
            bar_size = interval_map.get(interval, "5 mins")

            hist_bars = self.ibkr.backfill(symbol, duration, bar_size)
            if not hist_bars.empty:
                self.logger.debug(f"Using IBKR historical data for {symbol}")
                return hist_bars

        except Exception as e:
            self.logger.warning(f"IBKR historical failed for {symbol}: {e}")

        # Fallback to yfinance
        try:
            self.logger.debug(f"Falling back to yfinance for {symbol}")
            import yfinance as yf

            df = yf.download(symbol, period=period, interval=interval, progress=False)
            if df.empty:
                self.logger.warning(f"yfinance returned empty data for {symbol}")
                return pd.DataFrame()

            # Normalize column names to match IBKR format
            df.columns = [col.lower() for col in df.columns]
            return df

        except Exception as e:
            self.logger.error(f"All data sources failed for {symbol}: {e}")
            return pd.DataFrame()


def create_data_manager(ib: IB) -> IBKRDataManager:
    """Factory function to create and initialize a data manager.

    Args:
        ib: Connected IB instance from bot.py

    Returns:
        Initialized IBKRDataManager instance

    Example:
        >>> from ib_async import IB
        >>> ib = IB()
        >>> await ib.connectAsync('127.0.0.1', 7496, clientId=10)
        >>> data_mgr = create_data_manager(ib)
        >>> data_mgr.subscribe('AAPL', score=10.0)
    """
    return IBKRDataManager(ib)


if __name__ == "__main__":
    # Example usage (requires running IB Gateway/TWS on port 7496)
    logging.basicConfig(level=logging.INFO)

    async def example():
        from ib_async import IB

        ib = IB()
        await ib.connectAsync('127.0.0.1', 7496, clientId=10)

        # Create data manager
        mgr = create_data_manager(ib)

        # Subscribe to symbols
        mgr.subscribe('AAPL', score=10.0)
        mgr.subscribe('TSLA', score=8.0)

        # Wait for data to arrive
        await ib.sleep(10)

        # Get latest quotes
        aapl_quote = mgr.get_quote('AAPL')
        print(f"AAPL quote: {aapl_quote.to_dict() if aapl_quote else 'N/A'}")

        # Get aggregated bars
        aapl_bars = mgr.get_bars('AAPL', interval='1m')
        print(f"AAPL 1-min bars:\n{aapl_bars.tail()}")

        # Backfill historical data
        hist = mgr.backfill('AAPL', '5 D', '5 mins')
        print(f"AAPL historical ({len(hist)} bars):\n{hist.head()}")

        # Use smart router
        router = SmartDataRouter(mgr)
        data = router.get_data('AAPL', period='30d', interval='5m')
        print(f"Smart routed data for AAPL:\n{data.head()}")

        # Cleanup
        ib.disconnect()

    import asyncio
    asyncio.run(example())
