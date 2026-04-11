"""
Unit tests for the Alpaca real-time order guards in orders_core.execute_buy
and orders_core.execute_short.

Two guards run at the very start of every order submission, before any IBKR
call, risk check, or lock acquisition:

  HALT_CACHE gate — blocks order if Alpaca stream reports symbol is halted.
  QUOTE_CACHE spread gate — blocks order if live bid/ask spread exceeds the
                            configured maximum (default 0.3%).

Critical edge case: when the Alpaca stream is NOT running (keys not set, or
stream not yet started), both caches are empty.  The guards must PASS
(not block) in this state so paper trading continues without Alpaca.

  HALT_CACHE empty  → is_halted() returns False → gate passes
  QUOTE_CACHE empty → get_spread_pct() returns None →  spread check skipped
"""

import logging
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Project root ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub every heavy dep that orders_core imports at module level ─────────────
for _m in ("ib_async", "ib_insync", "anthropic", "praw", "feedparser",
           "tradingview_screener", "cvxpy",
           "alpaca", "alpaca.data", "alpaca.data.historical",
           "alpaca.data.live", "alpaca.data.enums", "alpaca.data.timeframe",
           "alpaca.data.requests"):
    sys.modules.setdefault(_m, types.ModuleType(_m))

_col = types.ModuleType("colorama")
_col.Fore  = types.SimpleNamespace(YELLOW="", GREEN="", CYAN="", RED="", WHITE="",
                                    MAGENTA="", RESET="")
_col.Style = types.SimpleNamespace(RESET_ALL="", BRIGHT="")
_col.init  = lambda **kw: None
sys.modules.setdefault("colorama", _col)

# ── Import alpaca_stream singletons (real module) ────────────────────────────
# We import the real alpaca_stream so we can manipulate the caches directly.
if "alpaca_stream" in sys.modules and not hasattr(sys.modules["alpaca_stream"], "__file__"):
    del sys.modules["alpaca_stream"]
from alpaca_stream import HALT_CACHE, QUOTE_CACHE, _HaltCache, _QuoteCache


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_fresh_halt_cache() -> _HaltCache:
    c = _HaltCache()
    return c


def _make_fresh_quote_cache() -> _QuoteCache:
    c = _QuoteCache()
    return c


# ══════════════════════════════════════════════════════════════════════════════
# Tests: HALT_CACHE guard behaviour
#
# Strategy: patch alpaca_stream.HALT_CACHE with a controlled instance, then
# call the guard logic directly (extracted as a pure function test).  We test
# the guard's contract — not execute_buy's full lifecycle.
# ══════════════════════════════════════════════════════════════════════════════

class TestHaltCacheGuardContract:
    """
    Verify the HALT_CACHE guard contract, isolated from the rest of execute_buy.
    """

    def test_is_halted_false_for_empty_cache(self):
        """Empty cache → no halts known → gate must pass."""
        c = _make_fresh_halt_cache()
        assert c.is_halted("AAPL") is False

    def test_is_halted_true_after_halt_update(self):
        """Any non-T status → symbol is halted → gate must block."""
        c = _make_fresh_halt_cache()
        c.update("AAPL", "H")
        assert c.is_halted("AAPL") is True

    def test_is_halted_false_after_resume(self):
        """T status → trading resumed → gate must pass again."""
        c = _make_fresh_halt_cache()
        c.update("AAPL", "H")
        c.update("AAPL", "T")
        assert c.is_halted("AAPL") is False

    def test_halt_does_not_cross_contaminate_symbols(self):
        c = _make_fresh_halt_cache()
        c.update("AAPL", "H")
        assert c.is_halted("TSLA") is False

    def test_guard_logic_blocks_when_halted(self):
        """
        Simulate the guard logic from execute_buy:
            if HALT_CACHE.is_halted(symbol): return False
        """
        c = _make_fresh_halt_cache()
        c.update("GMBL", "H")

        # Guard inline
        result = c.is_halted("GMBL")
        assert result is True, "Guard must block a halted symbol"

    def test_guard_logic_passes_when_not_halted(self):
        c = _make_fresh_halt_cache()
        result = c.is_halted("SPY")   # never marked halted
        assert result is False, "Guard must not block a non-halted symbol"


# ══════════════════════════════════════════════════════════════════════════════
# Tests: QUOTE_CACHE spread gate contract
# ══════════════════════════════════════════════════════════════════════════════

class TestSpreadGateContract:
    """
    Verify the spread gate logic from execute_buy:

        spread = QUOTE_CACHE.get_spread_pct(symbol)
        max_spread = CONFIG.get("max_spread_pct", 0.003)
        if spread is not None and spread > max_spread:
            return False
    """

    _MAX_SPREAD = 0.003   # matches CONFIG default

    def _gate(self, spread_pct, max_spread=None):
        """Return True if the gate would block (i.e. order aborted)."""
        ms = max_spread if max_spread is not None else self._MAX_SPREAD
        return spread_pct is not None and spread_pct > ms

    def test_none_spread_passes_gate(self):
        """Empty cache returns None → guard condition is False → gate passes."""
        assert self._gate(None) is False

    def test_zero_spread_passes_gate(self):
        """Zero spread (internal crossing, e.g. index) passes."""
        assert self._gate(0.0) is False

    def test_spread_below_threshold_passes(self):
        """0.1% spread on a liquid name is well under the 0.3% gate."""
        assert self._gate(0.001) is False

    def test_spread_at_threshold_passes(self):
        """Spread exactly equal to max_spread must NOT block (strict >)."""
        assert self._gate(self._MAX_SPREAD) is False

    def test_spread_above_threshold_blocked(self):
        """Spread 0.4% > 0.3% threshold → gate blocks."""
        assert self._gate(0.004) is True

    def test_large_spread_blocked(self):
        """Very wide spread (illiquid stock) must be blocked."""
        assert self._gate(0.05) is True

    def test_custom_max_spread_respected(self):
        """Gate respects the configured max_spread_pct."""
        # Widen to 1% — a 0.4% spread should now pass
        assert self._gate(0.004, max_spread=0.01) is False
        # Tighten to 0.1% — a 0.2% spread should now block
        assert self._gate(0.002, max_spread=0.001) is True


# ══════════════════════════════════════════════════════════════════════════════
# Tests: QUOTE_CACHE.get_spread_pct returns None when cache is empty
# (critical for paper trading without Alpaca stream running)
# ══════════════════════════════════════════════════════════════════════════════

class TestQuoteCacheEmptyState:

    def test_empty_cache_returns_none_spread(self):
        """Without a running stream, cache is empty → spread_pct is None."""
        c = _make_fresh_quote_cache()
        assert c.get_spread_pct("AAPL") is None

    def test_empty_cache_get_returns_none(self):
        c = _make_fresh_quote_cache()
        assert c.get("AAPL") is None

    def test_none_spread_means_gate_does_not_block(self):
        """Verify the guard condition evaluates correctly for None."""
        c = _make_fresh_quote_cache()
        spread = c.get_spread_pct("AAPL")
        # Inline the guard check
        max_spread = 0.003
        gate_blocks = spread is not None and spread > max_spread
        assert gate_blocks is False, \
            "Empty QUOTE_CACHE must not block orders — paper trading depends on this"

    def test_populated_cache_returns_spread(self):
        c = _make_fresh_quote_cache()
        c.update("AAPL", bid=149.9, ask=150.1)
        sp = c.get_spread_pct("AAPL")
        assert sp is not None
        assert sp > 0

    def test_tight_spread_does_not_block(self):
        """SIP mid-cap spread (~0.1%) must not block execution."""
        c = _make_fresh_quote_cache()
        c.update("AAPL", bid=149.925, ask=150.075)
        sp = c.get_spread_pct("AAPL")
        gate_blocks = sp is not None and sp > 0.003
        assert gate_blocks is False

    def test_wide_spread_blocks(self):
        """2% spread must trigger the gate."""
        c = _make_fresh_quote_cache()
        c.update("JUNK", bid=49.0, ask=51.0)   # ~4% spread
        sp = c.get_spread_pct("JUNK")
        gate_blocks = sp is not None and sp > 0.003
        assert gate_blocks is True


# ══════════════════════════════════════════════════════════════════════════════
# Tests: execute_buy halt gate integration
#
# We call the real execute_buy with HALT_CACHE patched to return True for the
# target symbol.  The function must return False immediately (guard short-circuit)
# without ever reaching the IBKR layer.
# ══════════════════════════════════════════════════════════════════════════════

class TestExecuteBuyHaltGate:
    """
    Integration tests: patch alpaca_stream module attributes directly so that
    execute_buy's lazy `from alpaca_stream import HALT_CACHE, QUOTE_CACHE` picks
    up the patched objects.  Never pop orders_core from sys.modules — that
    would break orders_core's own rebind pattern (`_sys.modules[__name__]`).
    """

    def test_halted_symbol_returns_false(self):
        """execute_buy must return False immediately when the symbol is halted."""
        halted = _make_fresh_halt_cache()
        halted.update("HALT_ME", "H")
        empty_quotes = _make_fresh_quote_cache()

        import alpaca_stream as _ast
        original_halt  = _ast.HALT_CACHE
        original_quote = _ast.QUOTE_CACHE
        _ast.HALT_CACHE  = halted
        _ast.QUOTE_CACHE = empty_quotes
        try:
            from orders_core import execute_buy
            result = execute_buy(
                ib=MagicMock(), symbol="HALT_ME",
                price=100.0, atr=1.5, score=25,
                portfolio_value=100_000, regime={"regime": "TRENDING_UP"},
            )
            assert result is False, "Halted symbol must be blocked by halt gate"
        finally:
            _ast.HALT_CACHE  = original_halt
            _ast.QUOTE_CACHE = original_quote

    def test_non_halted_symbol_not_blocked_by_halt_gate(self, caplog):
        """A non-halted symbol must not be blocked by the halt gate."""
        empty_halt  = _make_fresh_halt_cache()
        empty_quote = _make_fresh_quote_cache()

        import alpaca_stream as _ast
        original_halt  = _ast.HALT_CACHE
        original_quote = _ast.QUOTE_CACHE
        _ast.HALT_CACHE  = empty_halt
        _ast.QUOTE_CACHE = empty_quote
        try:
            from orders_core import execute_buy
            with caplog.at_level(logging.WARNING, logger="decifer.orders"):
                # Attempt a buy; it will fail downstream (no IBKR), but
                # the halt gate warning must NOT appear.
                execute_buy(
                    ib=MagicMock(), symbol="AAPL",
                    price=100.0, atr=1.5, score=25,
                    portfolio_value=100_000, regime={"regime": "TRENDING_UP"},
                )
            halt_msgs = [r.message for r in caplog.records
                         if "trading halted" in r.message.lower()]
            assert not halt_msgs, \
                f"Halt gate fired on non-halted symbol: {halt_msgs}"
        finally:
            _ast.HALT_CACHE  = original_halt
            _ast.QUOTE_CACHE = original_quote


class TestExecuteBuySpreadGate:

    def test_wide_spread_returns_false(self):
        """execute_buy must return False immediately when spread exceeds the gate."""
        empty_halt  = _make_fresh_halt_cache()
        wide_quotes = _make_fresh_quote_cache()
        wide_quotes.update("WIDE", bid=48.0, ask=52.0)   # 8% spread — clearly blocked

        import alpaca_stream as _ast
        original_halt  = _ast.HALT_CACHE
        original_quote = _ast.QUOTE_CACHE
        _ast.HALT_CACHE  = empty_halt
        _ast.QUOTE_CACHE = wide_quotes
        try:
            from orders_core import execute_buy
            result = execute_buy(
                ib=MagicMock(), symbol="WIDE",
                price=50.0, atr=1.0, score=25,
                portfolio_value=100_000, regime={"regime": "TRENDING_UP"},
            )
            assert result is False, "Wide spread must be blocked by spread gate"
        finally:
            _ast.HALT_CACHE  = original_halt
            _ast.QUOTE_CACHE = original_quote

    def test_empty_quote_cache_does_not_block(self, caplog):
        """
        Critical regression guard: when QUOTE_CACHE is empty (stream not running),
        execute_buy must NOT be blocked by the spread gate.

        This is the primary paper-trading safety: the bot must trade normally
        even when the Alpaca stream hasn't connected yet.
        """
        empty_halt  = _make_fresh_halt_cache()
        empty_quote = _make_fresh_quote_cache()   # no quotes stored

        import alpaca_stream as _ast
        original_halt  = _ast.HALT_CACHE
        original_quote = _ast.QUOTE_CACHE
        _ast.HALT_CACHE  = empty_halt
        _ast.QUOTE_CACHE = empty_quote
        try:
            from orders_core import execute_buy
            with caplog.at_level(logging.WARNING, logger="decifer.orders"):
                execute_buy(
                    ib=MagicMock(), symbol="AAPL",
                    price=100.0, atr=1.5, score=25,
                    portfolio_value=100_000, regime={"regime": "TRENDING_UP"},
                )
            spread_blocked_msgs = [r.message for r in caplog.records
                                   if "spread" in r.message.lower()
                                   and "aborting" in r.message.lower()]
            assert not spread_blocked_msgs, (
                "Spread gate fired on EMPTY cache — this blocks ALL orders when "
                f"Alpaca stream is not running: {spread_blocked_msgs}"
            )
        finally:
            _ast.HALT_CACHE  = original_halt
            _ast.QUOTE_CACHE = original_quote
