"""Tests for ibkr_streaming.py — real-time streaming data module."""
import os
import sys
import math
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Path setup + heavy-dependency stubs BEFORE importing target module
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub ib_async
import types
ib_async_mod = types.ModuleType("ib_async")

class _FakeBarData:
    def __init__(self, time=None, open=100.0, high=101.0, low=99.0, close=100.5,
                 volume=1000, average=100.25, barCount=1):
        self.time = time or datetime(2024, 1, 15, 10, 0, 0)
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.average = average
        self.barCount = barCount

class _FakeEventList(list):
    """A list that supports += for event handler registration."""
    def __iadd__(self, handler):
        self.append(handler)
        return self

class _FakeTicker:
    def __init__(self, symbol="AAPL"):
        self.bid = 149.90
        self.ask = 150.10
        self.last = 150.00
        self.volume = 5000
        self.vwap = 149.95
        self.rtVolume = None
        contract = MagicMock()
        contract.symbol = symbol
        self.contract = contract
        self._handlers = []
        # updateEvent must be a settable attribute (not a property) so that
        # ibkr_streaming.py can do:  ticker.updateEvent += handler
        self.updateEvent = _FakeEventList()

class _FakeIB:
    def __init__(self):
        self._tickers = {}
        self.cancelMktData = MagicMock()
        self.reqMarketDataType = MagicMock()

    def reqMktData(self, contract, *args, **kwargs):
        sym = contract.symbol if hasattr(contract, 'symbol') else "UNKNOWN"
        ticker = _FakeTicker(sym)
        self._tickers[sym] = ticker
        return ticker

    def reqRealTimeBars(self, *args, **kwargs):
        return MagicMock()

ib_async_mod.IB = _FakeIB
ib_async_mod.Contract = MagicMock(side_effect=lambda **kw: MagicMock(**kw))
ib_async_mod.Ticker = _FakeTicker
ib_async_mod.BarData = _FakeBarData
# Add extras needed by other modules (orders.py, options.py, etc.)
ib_async_mod.Stock = MagicMock()
ib_async_mod.Forex = MagicMock()
ib_async_mod.Option = MagicMock()
ib_async_mod.Future = MagicMock()
ib_async_mod.LimitOrder = MagicMock()
ib_async_mod.StopOrder = MagicMock()
ib_async_mod.MarketOrder = MagicMock()
# Force-install our ib_async stub (not setdefault) so earlier MagicMock stubs
# from test_agents.py don't leave BarData/Ticker as auto-generated MagicMocks.
# Also evict ibkr_streaming so it re-imports and picks up our proper BarData.
sys.modules["ib_async"] = ib_async_mod
sys.modules.pop("ibkr_streaming", None)

# Stub anthropic
anthropic_mod = types.ModuleType("anthropic")
anthropic_mod.Anthropic = MagicMock()
sys.modules.setdefault("anthropic", anthropic_mod)

# Stub yfinance
yf_mod = types.ModuleType("yfinance")
yf_mod.download = MagicMock(return_value=pd.DataFrame())
yf_mod.Ticker = MagicMock()
sys.modules.setdefault("yfinance", yf_mod)

# Stub py_vollib
for _mod in ["py_vollib", "py_vollib.black_scholes", "py_vollib.black_scholes.greeks",
             "py_vollib.black_scholes.greeks.analytical", "py_vollib.black_scholes.implied_volatility"]:
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

# Stub tradingview_screener
tv_mod = types.ModuleType("tradingview_screener")
tv_mod.Scanner = MagicMock()
tv_mod.Column = MagicMock()
sys.modules.setdefault("tradingview_screener", tv_mod)

# Stub httpx
httpx_mod = types.ModuleType("httpx")
httpx_mod.get = MagicMock()
sys.modules.setdefault("httpx", httpx_mod)

# Stub feedparser
feedparser_mod = types.ModuleType("feedparser")
feedparser_mod.parse = MagicMock(return_value=MagicMock(entries=[]))
sys.modules.setdefault("feedparser", feedparser_mod)

# Stub praw
praw_mod = types.ModuleType("praw")
praw_mod.Reddit = MagicMock()
sys.modules.setdefault("praw", praw_mod)

# Stub sklearn / joblib
for _m in ["sklearn", "sklearn.ensemble", "sklearn.preprocessing",
           "sklearn.model_selection", "joblib"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

# Now import the target
from ibkr_streaming import StreamingQuote, BarAggregator, IBKRDataManager


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def make_bar(t=None, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000):
    return _FakeBarData(
        time=t or datetime(2024, 1, 15, 10, 0, 0),
        open=open, high=high, low=low, close=close,
        volume=volume,
    )


# ===========================================================================
# StreamingQuote tests
# ===========================================================================

class TestStreamingQuote:

    def test_mid_with_valid_bid_ask(self):
        q = StreamingQuote(symbol="AAPL", bid=100.0, ask=102.0)
        assert q.mid == pytest.approx(101.0)

    def test_mid_falls_back_to_last_when_bid_nan(self):
        q = StreamingQuote(symbol="AAPL", bid=float('nan'), ask=float('nan'), last=99.5)
        assert q.mid == pytest.approx(99.5)

    def test_mid_is_nan_when_all_nan(self):
        q = StreamingQuote(symbol="AAPL")
        assert math.isnan(q.mid)

    def test_to_dict_contains_all_keys(self):
        q = StreamingQuote(symbol="MSFT", bid=200.0, ask=200.5, last=200.25, volume=1000)
        d = q.to_dict()
        for key in ('symbol', 'bid', 'ask', 'last', 'mid', 'volume', 'vwap', 'timestamp'):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_symbol_correct(self):
        q = StreamingQuote(symbol="TSLA", bid=250.0, ask=251.0)
        assert q.to_dict()['symbol'] == "TSLA"

    def test_mid_equal_bid_ask_spread(self):
        """mid should be exactly (bid+ask)/2."""
        q = StreamingQuote(symbol="SPY", bid=450.00, ask=450.10)
        assert q.mid == pytest.approx(450.05)

    def test_default_volume_zero(self):
        q = StreamingQuote(symbol="AAPL")
        assert q.volume == 0

    def test_timestamp_is_datetime(self):
        q = StreamingQuote(symbol="AAPL")
        assert isinstance(q.timestamp, datetime)


# ===========================================================================
# BarAggregator tests
# ===========================================================================

class TestBarAggregator:

    def test_empty_aggregator_returns_empty_df(self):
        agg = BarAggregator("AAPL")
        df = agg.get_bars("1m")
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_empty_5m_returns_empty_df(self):
        agg = BarAggregator("AAPL")
        df = agg.get_bars("5m")
        assert df.empty

    def test_add_single_bar_recorded(self):
        agg = BarAggregator("AAPL")
        bar = make_bar(t=datetime(2024, 1, 15, 10, 0, 0))
        agg.add_bar(bar)
        # First bar seeds current_1m but doesn't emit until new minute
        # add a second bar 90 seconds later to flush
        bar2 = make_bar(t=datetime(2024, 1, 15, 10, 1, 30))
        agg.add_bar(bar2)
        df = agg.get_bars("1m")
        assert not df.empty

    def test_aggregated_bar_has_ohlcv_columns(self):
        agg = BarAggregator("AAPL")
        bar1 = make_bar(t=datetime(2024, 1, 15, 10, 0, 0), open=100, high=102, low=99, close=101, volume=500)
        bar2 = make_bar(t=datetime(2024, 1, 15, 10, 1, 30), open=101, high=103, low=100, close=102, volume=600)
        agg.add_bar(bar1)
        agg.add_bar(bar2)
        df = agg.get_bars("1m")
        for col in ('open', 'high', 'low', 'close', 'volume'):
            assert col in df.columns, f"Missing column: {col}"

    def test_high_is_max_across_bars(self):
        agg = BarAggregator("AAPL")
        t0 = datetime(2024, 1, 15, 10, 0, 0)
        # Two bars within same minute, then flush
        agg.add_bar(make_bar(t=t0, high=102))
        agg.add_bar(make_bar(t=t0 + timedelta(seconds=5), high=105))
        # Flush with bar in new minute
        agg.add_bar(make_bar(t=t0 + timedelta(seconds=70), high=100))
        df = agg.get_bars("1m")
        assert not df.empty
        assert df['high'].iloc[0] == pytest.approx(105.0)

    def test_low_is_min_across_bars(self):
        agg = BarAggregator("AAPL")
        t0 = datetime(2024, 1, 15, 10, 0, 0)
        agg.add_bar(make_bar(t=t0, low=98))
        agg.add_bar(make_bar(t=t0 + timedelta(seconds=5), low=95))
        agg.add_bar(make_bar(t=t0 + timedelta(seconds=70), low=100))
        df = agg.get_bars("1m")
        assert not df.empty
        assert df['low'].iloc[0] == pytest.approx(95.0)

    def test_volume_is_summed(self):
        agg = BarAggregator("AAPL")
        t0 = datetime(2024, 1, 15, 10, 0, 0)
        agg.add_bar(make_bar(t=t0, volume=300))
        agg.add_bar(make_bar(t=t0 + timedelta(seconds=5), volume=400))
        agg.add_bar(make_bar(t=t0 + timedelta(seconds=70), volume=100))
        df = agg.get_bars("1m")
        assert df['volume'].iloc[0] == pytest.approx(700)

    def test_5m_bar_flushed_after_300s(self):
        agg = BarAggregator("AAPL")
        t0 = datetime(2024, 1, 15, 10, 0, 0)
        agg.add_bar(make_bar(t=t0))
        agg.add_bar(make_bar(t=t0 + timedelta(seconds=310)))
        df = agg.get_bars("5m")
        assert not df.empty

    def test_thread_safety(self):
        """Concurrent adds should not raise."""
        agg = BarAggregator("AAPL")
        errors = []

        def worker(offset):
            try:
                for i in range(20):
                    t = datetime(2024, 1, 15, 10, 0, 0) + timedelta(seconds=offset * 20 + i)
                    agg.add_bar(make_bar(t=t))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ===========================================================================
# IBKRDataManager tests
# ===========================================================================

class TestIBKRDataManager:

    def _make_manager(self):
        ib = _FakeIB()
        return IBKRDataManager(ib), ib

    # --- subscribe / unsubscribe -------------------------------------------

    def test_subscribe_creates_quote_entry(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL", score=5.0)
        assert "AAPL" in mgr._quotes
        assert isinstance(mgr._quotes["AAPL"], StreamingQuote)

    def test_subscribe_creates_aggregator(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("MSFT", score=3.0)
        assert "MSFT" in mgr._aggregators
        assert isinstance(mgr._aggregators["MSFT"], BarAggregator)

    def test_subscribe_stores_score(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("GOOGL", score=7.5)
        assert mgr._subscription_scores["GOOGL"] == pytest.approx(7.5)

    def test_double_subscribe_updates_score(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL", score=3.0)
        mgr.subscribe("AAPL", score=9.0)  # second call — already subscribed
        assert mgr._subscription_scores["AAPL"] == pytest.approx(9.0)
        assert len([s for s in mgr._subscriptions if s == "AAPL"]) == 1

    def test_unsubscribe_removes_entries(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL")
        mgr.unsubscribe("AAPL")
        assert "AAPL" not in mgr._subscriptions
        assert "AAPL" not in mgr._quotes
        assert "AAPL" not in mgr._aggregators

    def test_unsubscribe_unknown_symbol_no_error(self):
        mgr, _ = self._make_manager()
        mgr.unsubscribe("NONEXISTENT")  # should not raise

    def test_subscribe_calls_ibkr_reqMktData(self):
        ib = _FakeIB()
        ib.reqMktData = MagicMock(side_effect=ib.reqMktData)
        mgr = IBKRDataManager(ib)
        mgr.subscribe("TSLA")
        ib.reqMktData.assert_called_once()

    # --- get_quote ----------------------------------------------------------

    def test_get_quote_returns_streaming_quote(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL")
        q = mgr.get_quote("AAPL")
        assert isinstance(q, StreamingQuote)
        assert q.symbol == "AAPL"

    def test_get_quote_unknown_symbol_returns_none(self):
        mgr, _ = self._make_manager()
        result = mgr.get_quote("ZZZZZ")
        assert result is None

    # --- eviction -----------------------------------------------------------

    def test_eviction_on_max_subscriptions(self):
        """When MAX_SUBSCRIPTIONS is hit, the oldest/lowest priority is evicted."""
        ib = _FakeIB()
        mgr = IBKRDataManager(ib)
        mgr.MAX_SUBSCRIPTIONS = 3  # small limit for test

        mgr.subscribe("AAA", score=1.0)
        mgr.subscribe("BBB", score=2.0)
        mgr.subscribe("CCC", score=3.0)
        assert len(mgr._subscriptions) == 3

        mgr.subscribe("DDD", score=10.0)
        # One subscription should have been evicted
        assert len(mgr._subscriptions) == 3
        assert "DDD" in mgr._subscriptions  # high-score new entry kept

    # --- _on_tick_update ---------------------------------------------------

    def test_tick_update_updates_bid_ask(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL")

        fake_ticker = _FakeTicker("AAPL")
        fake_ticker.bid = 148.50
        fake_ticker.ask = 148.60
        fake_ticker.last = 148.55
        fake_ticker.volume = 2000
        fake_ticker.vwap = 148.52

        mgr._on_tick_update(fake_ticker)

        q = mgr._quotes["AAPL"]
        assert q.bid == pytest.approx(148.50)
        assert q.ask == pytest.approx(148.60)
        assert q.last == pytest.approx(148.55)

    def test_tick_update_ignores_zero_bid(self):
        """Zero or negative bid/ask should not overwrite existing value."""
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL")
        mgr._quotes["AAPL"].bid = 150.0  # existing value

        fake_ticker = _FakeTicker("AAPL")
        fake_ticker.bid = 0.0  # invalid
        fake_ticker.ask = float('nan')
        fake_ticker.last = float('nan')
        fake_ticker.volume = None
        fake_ticker.vwap = 0.0

        mgr._on_tick_update(fake_ticker)
        assert mgr._quotes["AAPL"].bid == pytest.approx(150.0)

    def test_tick_update_unknown_symbol_no_error(self):
        mgr, _ = self._make_manager()
        fake_ticker = _FakeTicker("NOPE")
        mgr._on_tick_update(fake_ticker)  # should not raise

    # --- _parse_rt_bar -----------------------------------------------------

    def test_parse_rt_bar_valid_string(self):
        mgr, _ = self._make_manager()
        ts = int(datetime(2024, 1, 15, 10, 0, 0).timestamp())
        rt_vol = f"150.25;100;{ts};150.20;150.30;50;60;5000"
        bar = mgr._parse_rt_bar(rt_vol, "AAPL")
        assert bar is not None
        assert bar.open == pytest.approx(150.25)
        assert bar.volume == 100

    def test_parse_rt_bar_too_few_parts_returns_none(self):
        mgr, _ = self._make_manager()
        bar = mgr._parse_rt_bar("150;100;123", "AAPL")  # only 3 parts
        assert bar is None

    def test_parse_rt_bar_invalid_string_returns_none(self):
        mgr, _ = self._make_manager()
        bar = mgr._parse_rt_bar("not;valid;data;here;x;y;z;w", "AAPL")
        assert bar is None

    def test_parse_rt_bar_empty_string_returns_none(self):
        mgr, _ = self._make_manager()
        bar = mgr._parse_rt_bar("", "AAPL")
        assert bar is None

    # --- get_bars (via aggregator) -----------------------------------------

    def test_get_bars_returns_dataframe(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL")
        df = mgr._aggregators["AAPL"].get_bars("1m")
        assert isinstance(df, pd.DataFrame)

    def test_get_bars_unknown_symbol_returns_empty(self):
        mgr, _ = self._make_manager()
        if hasattr(mgr, 'get_bars'):
            result = mgr.get_bars("ZZZZ", "1m")
            assert result is None or (isinstance(result, pd.DataFrame) and result.empty)
        else:
            # access via aggregator missing symbol
            assert "ZZZZ" not in mgr._aggregators

    # --- Multiple subscriptions --------------------------------------------

    def test_multiple_symbols_independent_quotes(self):
        mgr, _ = self._make_manager()
        mgr.subscribe("AAPL")
        mgr.subscribe("MSFT")

        t_aapl = _FakeTicker("AAPL")
        t_aapl.bid = 150.0
        t_aapl.ask = 150.1
        t_aapl.last = 150.05
        t_aapl.volume = 1000
        t_aapl.vwap = 150.02

        t_msft = _FakeTicker("MSFT")
        t_msft.bid = 380.0
        t_msft.ask = 380.2
        t_msft.last = 380.1
        t_msft.volume = 2000
        t_msft.vwap = 380.05

        mgr._on_tick_update(t_aapl)
        mgr._on_tick_update(t_msft)

        assert mgr._quotes["AAPL"].bid == pytest.approx(150.0)
        assert mgr._quotes["MSFT"].bid == pytest.approx(380.0)

    def test_subscription_count_tracked(self):
        mgr, _ = self._make_manager()
        for sym in ["AAPL", "MSFT", "GOOGL", "AMZN"]:
            mgr.subscribe(sym)
        assert len(mgr._subscriptions) == 4
