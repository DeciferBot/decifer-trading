"""
Unit tests for alpaca_stream.py — live market-data cache layer.

Validates all four module-level singletons:
  BAR_CACHE       — 1-minute OHLCV bars, 5m aggregation
  QUOTE_CACHE     — real-time bid/ask + spread_pct
  DAILY_BAR_CACHE — intraday running daily bar
  HALT_CACHE      — trading halt / resume status

These are the exact objects read by signals.py and orders_core.py.
Any behaviour regression here silently breaks signal generation or
blocks all order execution.

No network connections are made.
"""

import os
import sys
import types
from datetime import datetime, timezone

import pandas as pd
import pytest

# ── Project root ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub alpaca-py (not needed by alpaca_stream directly but avoids side-effects
# if other modules are imported transitively) ─────────────────────────────────
for _stub in ("alpaca", "alpaca.data", "alpaca.data.live", "alpaca.data.enums"):
    sys.modules.setdefault(_stub, types.ModuleType(_stub))

from alpaca_stream import (
    BAR_CACHE,
    QUOTE_CACHE,
    DAILY_BAR_CACHE,
    HALT_CACHE,
    _BarCache,
    _QuoteCache,
    _DailyBarCache,
    _HaltCache,
    _MAX_1M_BARS,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _bar(ts_offset_minutes: int = 0, close: float = 100.0) -> dict:
    """Build a minimal 1-minute bar dict as AlpacaBarStream produces."""
    ts = pd.Timestamp("2026-04-07 10:00:00", tz="UTC") + pd.Timedelta(minutes=ts_offset_minutes)
    return {
        "timestamp": ts,
        "open":   close - 0.1,
        "high":   close + 0.5,
        "low":    close - 0.5,
        "close":  close,
        "volume": 10_000,
        "vwap":   close,
    }


def _fresh_bar_cache() -> _BarCache:
    """Return a clean _BarCache instance for each test."""
    c = _BarCache()
    return c


def _fresh_quote_cache() -> _QuoteCache:
    return _QuoteCache()


def _fresh_halt_cache() -> _HaltCache:
    return _HaltCache()


def _fresh_daily_cache() -> _DailyBarCache:
    return _DailyBarCache()


# ═══════════════════════════════════════════════════════════════════════════════
# BAR_CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class TestBarCacheUpdate:

    def test_update_adds_symbol(self):
        c = _fresh_bar_cache()
        c.update("AAPL", _bar(0))
        assert "AAPL" in c.symbols()

    def test_update_multiple_symbols(self):
        c = _fresh_bar_cache()
        c.update("AAPL", _bar(0))
        c.update("TSLA", _bar(0))
        assert {"AAPL", "TSLA"} == c.symbols()

    def test_update_sequential_bars_accumulate(self):
        c = _fresh_bar_cache()
        for i in range(10):
            c.update("AAPL", _bar(i))
        df = c._data["AAPL"]
        assert len(df) == 10

    def test_update_duplicate_timestamp_replaces_last_bar(self):
        """If a bar arrives with the same timestamp as the previous bar (update),
        it replaces rather than appends — so row count stays the same."""
        c = _fresh_bar_cache()
        c.update("AAPL", _bar(0, close=100.0))
        c.update("AAPL", _bar(0, close=101.0))   # same timestamp
        df = c._data["AAPL"]
        assert len(df) == 1
        assert float(df["Close"].iloc[-1]) == 101.0

    def test_update_trims_to_max_bars(self):
        c = _fresh_bar_cache()
        for i in range(_MAX_1M_BARS + 50):
            c.update("AAPL", _bar(i))
        assert len(c._data["AAPL"]) == _MAX_1M_BARS

    def test_stored_columns_are_canonical(self):
        c = _fresh_bar_cache()
        c.update("AAPL", _bar(0))
        df = c._data["AAPL"]
        for col in ("Open", "High", "Low", "Close", "Volume"):
            assert col in df.columns, f"Expected canonical column '{col}'"


class TestBarCacheGet5m:

    def test_returns_none_with_zero_bars(self):
        c = _fresh_bar_cache()
        assert c.get_5m("AAPL") is None

    def test_returns_none_with_fewer_than_5_bars(self):
        c = _fresh_bar_cache()
        for i in range(4):
            c.update("AAPL", _bar(i))
        assert c.get_5m("AAPL") is None

    def test_returns_dataframe_with_enough_bars(self):
        c = _fresh_bar_cache()
        for i in range(25):   # 5 complete 5-minute periods
            c.update("AAPL", _bar(i))
        result = c.get_5m("AAPL")
        assert result is not None
        assert isinstance(result, pd.DataFrame)

    def test_5m_aggregation_has_canonical_columns(self):
        c = _fresh_bar_cache()
        for i in range(25):
            c.update("AAPL", _bar(i))
        result = c.get_5m("AAPL")
        for col in ("Open", "High", "Low", "Close", "Volume"):
            assert col in result.columns

    def test_5m_close_is_last_1m_close_in_period(self):
        """The 5m bar's Close should equal the last 1m close in that window.

        get_5m() requires at least 5 complete 5-minute periods.  Use 25 bars
        spread across 5 five-minute windows (bars at offsets 0-24 minutes),
        then check the last 5m bar's Close equals the close of the last 1m bar.
        """
        c = _fresh_bar_cache()
        for i in range(25):
            c.update("AAPL", _bar(i, close=100.0 + i))
        result = c.get_5m("AAPL")
        assert result is not None
        # Last 5m window covers bars 20..24; last close = 100 + 24 = 124
        assert float(result["Close"].iloc[-1]) == pytest.approx(124.0)

    def test_5m_high_is_max_of_1m_highs(self):
        """The 5m bar's High is the max of the 1m highs in that window."""
        c = _fresh_bar_cache()
        # 25 bars across 5 five-minute periods
        for i in range(25):
            c.update("AAPL", _bar(i, close=100.0 + i))
        result = c.get_5m("AAPL")
        assert result is not None
        # Last 5m window: bars 20..24. Each bar high = close + 0.5.
        # Max close in that window = 124.0 → max high = 124.5
        assert float(result["High"].iloc[-1]) == pytest.approx(124.5)

    def test_5m_volume_is_sum_of_1m_volumes(self):
        """The 5m bar's Volume is the sum of the 5 constituent 1m volumes."""
        c = _fresh_bar_cache()
        # 25 bars; each has volume=10_000 → last 5m bar sum = 5 * 10_000
        for i in range(25):
            c.update("AAPL", _bar(i))
        result = c.get_5m("AAPL")
        assert result is not None
        assert float(result["Volume"].iloc[-1]) == pytest.approx(5 * 10_000)

    def test_returns_none_for_unknown_symbol(self):
        c = _fresh_bar_cache()
        assert c.get_5m("UNKNOWN") is None

    def test_result_is_not_raw_cache_reference(self):
        """Mutating the returned DataFrame must not alter the cache."""
        c = _fresh_bar_cache()
        for i in range(25):
            c.update("AAPL", _bar(i))
        df1 = c.get_5m("AAPL")
        df1["Close"] = -999
        df2 = c.get_5m("AAPL")
        assert float(df2["Close"].iloc[0]) != -999


class TestBarCacheClear:

    def test_clear_empties_all_symbols(self):
        c = _fresh_bar_cache()
        for sym in ("AAPL", "TSLA", "SPY"):
            for i in range(5):
                c.update(sym, _bar(i))
        c.clear()
        assert c.symbols() == set()

    def test_get_5m_returns_none_after_clear(self):
        c = _fresh_bar_cache()
        for i in range(25):
            c.update("AAPL", _bar(i))
        c.clear()
        assert c.get_5m("AAPL") is None


# ═══════════════════════════════════════════════════════════════════════════════
# QUOTE_CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuoteCacheUpdate:

    def test_stores_valid_quote(self):
        c = _fresh_quote_cache()
        c.update("AAPL", bid=149.9, ask=150.1)
        q = c.get("AAPL")
        assert q is not None
        assert q["bid"] == pytest.approx(149.9)
        assert q["ask"] == pytest.approx(150.1)

    def test_spread_pct_computed_correctly(self):
        """spread_pct = (ask - bid) / mid = (150.1 - 149.9) / 150 ≈ 0.00133"""
        c = _fresh_quote_cache()
        c.update("AAPL", bid=149.9, ask=150.1)
        sp = c.get_spread_pct("AAPL")
        # mid = (149.9 + 150.1) / 2 = 150.0; spread = 0.2 / 150.0 ≈ 0.00133
        assert sp == pytest.approx(0.2 / 150.0, rel=1e-3)

    def test_ignores_zero_bid(self):
        c = _fresh_quote_cache()
        c.update("AAPL", bid=0.0, ask=150.0)
        assert c.get("AAPL") is None

    def test_ignores_zero_ask(self):
        c = _fresh_quote_cache()
        c.update("AAPL", bid=149.0, ask=0.0)
        assert c.get("AAPL") is None

    def test_ignores_inverted_spread(self):
        """bid > ask is invalid — must be rejected."""
        c = _fresh_quote_cache()
        c.update("AAPL", bid=151.0, ask=150.0)
        assert c.get("AAPL") is None

    def test_ignores_negative_bid(self):
        c = _fresh_quote_cache()
        c.update("AAPL", bid=-1.0, ask=150.0)
        assert c.get("AAPL") is None

    def test_update_overwrites_stale_quote(self):
        c = _fresh_quote_cache()
        c.update("AAPL", bid=149.0, ask=150.0)
        c.update("AAPL", bid=150.0, ask=151.0)
        q = c.get("AAPL")
        assert q["bid"] == pytest.approx(150.0)


class TestQuoteCacheGetSpreadPct:

    def test_returns_none_for_unknown_symbol(self):
        c = _fresh_quote_cache()
        assert c.get_spread_pct("UNKNOWN") is None

    def test_spread_pct_is_none_after_invalid_quote(self):
        """After a rejected quote, spread should still be None (no stale data)."""
        c = _fresh_quote_cache()
        c.update("AAPL", bid=0.0, ask=150.0)  # rejected
        assert c.get_spread_pct("AAPL") is None

    def test_tight_spread_below_typical_threshold(self):
        """A 0.1% spread (SIP mid-cap stock) should be well below the 0.3% gate."""
        c = _fresh_quote_cache()
        c.update("AAPL", bid=149.925, ask=150.075)  # ~0.1% spread
        sp = c.get_spread_pct("AAPL")
        assert sp < 0.003, "Tight spread should pass the default 0.3% gate"

    def test_wide_spread_above_typical_threshold(self):
        """A 2% spread should exceed the 0.3% gate."""
        c = _fresh_quote_cache()
        c.update("JUNK", bid=98.0, ask=102.0)  # ~4% spread
        sp = c.get_spread_pct("JUNK")
        assert sp > 0.003, "Wide spread should be caught by the spread gate"


# ═══════════════════════════════════════════════════════════════════════════════
# HALT_CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class TestHaltCache:

    def test_symbol_not_halted_by_default(self):
        c = _fresh_halt_cache()
        assert c.is_halted("AAPL") is False

    def test_non_T_status_marks_halted(self):
        c = _fresh_halt_cache()
        c.update("AAPL", "H")   # H = halted
        assert c.is_halted("AAPL") is True

    def test_T_status_clears_halt(self):
        c = _fresh_halt_cache()
        c.update("AAPL", "H")
        c.update("AAPL", "T")   # T = trading (normal)
        assert c.is_halted("AAPL") is False

    def test_halt_only_affects_named_symbol(self):
        c = _fresh_halt_cache()
        c.update("AAPL", "H")
        assert c.is_halted("TSLA") is False

    def test_multiple_halts(self):
        c = _fresh_halt_cache()
        c.update("AAPL", "H")
        c.update("TSLA", "H")
        assert c.is_halted("AAPL") is True
        assert c.is_halted("TSLA") is True

    def test_halted_symbols_snapshot(self):
        c = _fresh_halt_cache()
        c.update("AAPL", "H")
        c.update("TSLA", "H")
        c.update("SPY",  "T")
        assert c.halted_symbols() == {"AAPL", "TSLA"}

    def test_halted_symbols_returns_copy(self):
        """Mutating the returned set must not affect the cache."""
        c = _fresh_halt_cache()
        c.update("AAPL", "H")
        snap = c.halted_symbols()
        snap.add("FAKE")
        assert "FAKE" not in c.halted_symbols()

    def test_empty_status_code_marks_halted(self):
        """Any non-'T' code including empty string should mark the symbol halted."""
        c = _fresh_halt_cache()
        c.update("AAPL", "")
        assert c.is_halted("AAPL") is True

    def test_none_status_code_marks_halted(self):
        """None status (edge case from Alpaca) should mark halted, not crash."""
        c = _fresh_halt_cache()
        c.update("AAPL", None)
        assert c.is_halted("AAPL") is True


# ═══════════════════════════════════════════════════════════════════════════════
# DAILY_BAR_CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class TestDailyBarCache:

    def _daily(self, open=100.0, high=105.0, low=99.0, close=103.0, volume=1_000_000):
        return {
            "open": open, "high": high, "low": low,
            "close": close, "volume": volume,
            "timestamp": datetime.now(timezone.utc),
        }

    def test_returns_none_before_first_update(self):
        c = _fresh_daily_cache()
        assert c.get("AAPL") is None

    def test_get_returns_stored_bar(self):
        c = _fresh_daily_cache()
        c.update("AAPL", self._daily(close=103.0))
        bar = c.get("AAPL")
        assert bar is not None
        assert bar["close"] == pytest.approx(103.0)

    def test_second_update_overwrites_first(self):
        """Alpaca sends updated daily bar — must replace, not append."""
        c = _fresh_daily_cache()
        c.update("AAPL", self._daily(close=103.0))
        c.update("AAPL", self._daily(close=107.0))
        assert c.get("AAPL")["close"] == pytest.approx(107.0)

    def test_get_returns_copy_not_reference(self):
        """Mutating the returned bar must not corrupt the cache."""
        c = _fresh_daily_cache()
        c.update("AAPL", self._daily(close=103.0))
        bar = c.get("AAPL")
        bar["close"] = -999
        assert c.get("AAPL")["close"] != -999

    def test_all_ohlcv_fields_stored(self):
        c = _fresh_daily_cache()
        c.update("AAPL", self._daily(open=100.0, high=105.0, low=99.0,
                                      close=103.0, volume=1_000_000))
        bar = c.get("AAPL")
        for key in ("open", "high", "low", "close", "volume"):
            assert key in bar

    def test_multiple_symbols_independent(self):
        c = _fresh_daily_cache()
        c.update("AAPL", self._daily(close=103.0))
        c.update("TSLA", self._daily(close=250.0))
        assert c.get("AAPL")["close"] == pytest.approx(103.0)
        assert c.get("TSLA")["close"] == pytest.approx(250.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singletons are live (not fresh instances)
# Smoke-test that the module exports the right types.
# ═══════════════════════════════════════════════════════════════════════════════

class TestModuleSingletons:

    def test_bar_cache_is_bar_cache_type(self):
        assert isinstance(BAR_CACHE, _BarCache)

    def test_quote_cache_is_quote_cache_type(self):
        assert isinstance(QUOTE_CACHE, _QuoteCache)

    def test_daily_bar_cache_is_daily_bar_cache_type(self):
        assert isinstance(DAILY_BAR_CACHE, _DailyBarCache)

    def test_halt_cache_is_halt_cache_type(self):
        assert isinstance(HALT_CACHE, _HaltCache)

    def test_max_1m_bars_is_positive_int(self):
        assert isinstance(_MAX_1M_BARS, int)
        assert _MAX_1M_BARS > 0
