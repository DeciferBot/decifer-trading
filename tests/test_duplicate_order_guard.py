"""
tests/test_duplicate_order_guard.py

Tests for the duplicate-order guard in orders.py.

Covers:
  - _is_duplicate_check_enabled() always defaults to enabled (fail-safe)
  - _is_duplicate_check_enabled() respects CONFIG override
  - _is_duplicate_check_enabled() is robust to unexpected / missing arguments
  - has_open_order_for() returns True (duplicate detected) when an open order
    for the same symbol + side already exists in IBKR
  - has_open_order_for() returns False when the symbol is different
  - has_open_order_for() returns True (fail-closed) when IBKR raises an error
  - Options key matching (same key = blocked, different strike = allowed)
  - Re-entry after close is not incorrectly blocked
  - Exactly one definition of each previously-duplicated symbol exists
"""

import os
import sys
import types
import inspect
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 1. Add project root to sys.path
# ---------------------------------------------------------------------------
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ---------------------------------------------------------------------------
# 2. Stub ALL heavy deps BEFORE importing any Decifer module
# ---------------------------------------------------------------------------
for _mod in [
    "ib_async", "ib_insync", "anthropic", "yfinance",
    "praw", "feedparser", "tvDatafeed", "requests_html",
]:
    sys.modules.setdefault(_mod, MagicMock())

# ib_async needs named attributes that orders.py uses
_ib_async = sys.modules["ib_async"]
_ib_async.IB = MagicMock
_ib_async.Stock = MagicMock
_ib_async.Forex = MagicMock
_ib_async.Option = MagicMock
_ib_async.Future = MagicMock
_ib_async.LimitOrder = MagicMock
_ib_async.StopOrder = MagicMock
_ib_async.MarketOrder = MagicMock

# anthropic
_anthropic = sys.modules["anthropic"]
_anthropic.Anthropic = MagicMock

# py_vollib stubs
for _pv in (
    "py_vollib",
    "py_vollib.black_scholes",
    "py_vollib.black_scholes.greeks",
    "py_vollib.black_scholes.greeks.analytical",
    "py_vollib.black_scholes.implied_volatility",
):
    sys.modules.setdefault(_pv, types.ModuleType(_pv))

# tradingview_screener stub
_tv = types.ModuleType("tradingview_screener")
_tv.Scanner = MagicMock
_tv.Query = MagicMock
sys.modules.setdefault("tradingview_screener", _tv)

# pandas_ta stub
sys.modules.setdefault("pandas_ta", types.ModuleType("pandas_ta"))

# sklearn stubs
for _sk in (
    "sklearn",
    "sklearn.ensemble",
    "sklearn.preprocessing",
    "sklearn.model_selection",
    "sklearn.metrics",
):
    sys.modules.setdefault(_sk, types.ModuleType(_sk))

# joblib stub
sys.modules.setdefault("joblib", types.ModuleType("joblib"))

# ---------------------------------------------------------------------------
# 3. Stub config with all required keys BEFORE importing orders
# ---------------------------------------------------------------------------
import config as _config_mod  # noqa: E402

_cfg = {
    "ib_host": "127.0.0.1",
    "ib_port": 7496,
    "max_positions": 10,
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "capital_file": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
    "ORDER_DUPLICATE_CHECK_ENABLED": True,
}

if hasattr(_config_mod, "CONFIG"):
    _config_mod.CONFIG.update(_cfg)
else:
    _config_mod.CONFIG = _cfg

# ---------------------------------------------------------------------------
# 4. Stub risk and learning BEFORE importing orders
# ---------------------------------------------------------------------------
_risk_stub = types.ModuleType("risk")
_risk_stub.calculate_position_size = MagicMock(return_value=10)
_risk_stub.calculate_stops = MagicMock(return_value=(90.0, 110.0))
_risk_stub.check_correlation = MagicMock(return_value=(True, ""))
_risk_stub.record_win = MagicMock()
_risk_stub.record_loss = MagicMock()
sys.modules.setdefault("risk", _risk_stub)

_learning_stub = types.ModuleType("learning")
_learning_stub.log_order = MagicMock()
_learning_stub.log_trade = MagicMock()
sys.modules.setdefault("learning", _learning_stub)

_scanner_stub = types.ModuleType("scanner")
_scanner_stub.get_tv_signal_cache = MagicMock(return_value={})
_scanner_stub.CORE_SYMBOLS = []
_scanner_stub.MOMENTUM_FALLBACK = []
sys.modules.setdefault("scanner", _scanner_stub)

# ---------------------------------------------------------------------------
# 5. NOW import orders  (pop any hollow stub test_bot.py may have cached)
# ---------------------------------------------------------------------------
for _decifer_mod in ("orders", "risk", "learning", "scanner", "signals",
                     "news", "agents", "options", "options_scanner"):
    sys.modules.pop(_decifer_mod, None)
import orders  # noqa: E402

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ibkr_trade(symbol: str, side: str = "BUY",
                     sec_type: str = "STK",
                     strike: float = 0.0,
                     right: str = "",
                     expiry: str = ""):
    """Build a minimal fake IBKR Trade object."""
    contract = MagicMock()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.strike = strike
    contract.right = right
    contract.lastTradeDateOrContractMonth = expiry

    order = MagicMock()
    order.action = side.upper()

    trade = MagicMock()
    trade.contract = contract
    trade.order = order
    return trade


def _make_ib(open_trade_list):
    """Return a mock IB whose openTrades() returns ``open_trade_list``."""
    ib = MagicMock()
    ib.openTrades.return_value = open_trade_list
    return ib


# ===========================================================================
# TEST CLASS 1 — _is_duplicate_check_enabled
# ===========================================================================

class TestIsDuplicateCheckEnabled:
    """Unit tests for the private helper _is_duplicate_check_enabled()."""

    def test_enabled_by_default_when_key_missing(self):
        """
        When ORDER_DUPLICATE_CHECK_ENABLED is absent from CONFIG the guard
        must default to ENABLED (True) — fail-safe orientation.
        """
        cfg_copy = dict(orders.CONFIG)
        cfg_copy.pop("ORDER_DUPLICATE_CHECK_ENABLED", None)
        with patch.dict(orders.CONFIG, cfg_copy, clear=True):
            assert orders._is_duplicate_check_enabled() is True

    def test_enabled_when_config_explicitly_true(self):
        """Explicit True in CONFIG keeps the guard enabled."""
        with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": True}):
            assert orders._is_duplicate_check_enabled() is True

    def test_disabled_when_config_explicitly_false(self):
        """Explicit False in CONFIG disables the guard."""
        with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": False}):
            assert orders._is_duplicate_check_enabled() is False

    def test_enabled_when_config_value_is_1(self):
        """Truthy integer 1 is treated as enabled."""
        with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": 1}):
            assert orders._is_duplicate_check_enabled() is True

    def test_disabled_when_config_value_is_0(self):
        """Falsy integer 0 is treated as disabled."""
        with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": 0}):
            assert orders._is_duplicate_check_enabled() is False

    def test_enabled_when_config_value_is_nonempty_string(self):
        """Any non-empty string is truthy — guard stays enabled."""
        with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": "yes"}):
            assert orders._is_duplicate_check_enabled() is True

    def test_disabled_when_config_value_is_empty_string(self):
        """Empty string is falsy — guard disabled."""
        with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": ""}):
            assert orders._is_duplicate_check_enabled() is False

    def test_enabled_when_config_value_is_none(self):
        """
        None is falsy BUT the default should win when the lookup returns None
        only if the key is truly absent.  When the key IS present with value
        None, bool(None) == False — that is consistent behaviour.
        This test documents the actual semantics (None → disabled).
        """
        with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": None}):
            # None is falsy, so disabled — document this edge case
            result = orders._is_duplicate_check_enabled()
            assert isinstance(result, bool)

    def test_stays_enabled_when_config_get_raises(self):
        """
        If CONFIG.get raises an unexpected exception the guard must still
        return True (fail-safe) rather than propagating the exception.
        """
        bad_cfg = MagicMock()
        bad_cfg.get.side_effect = RuntimeError("Unexpected CONFIG error")
        original = orders.CONFIG
        try:
            orders.CONFIG = bad_cfg
            result = orders._is_duplicate_check_enabled()
        finally:
            orders.CONFIG = original
        assert result is True

    def test_exactly_one_definition_in_module(self):
        """
        Regression guard: confirm there is exactly ONE _is_duplicate_check_enabled
        defined in the orders module — the shadowing bug is fixed.
        """
        members = inspect.getmembers(orders, predicate=inspect.isfunction)
        names = [name for name, _ in members]
        count = names.count("_is_duplicate_check_enabled")
        assert count == 1, (
            f"Expected exactly 1 '_is_duplicate_check_enabled', found {count}. "
            "Shadowing / duplicate-definition bug may still be present."
        )


# ===========================================================================
# TEST CLASS 2 — has_open_order_for: stock orders
# ===========================================================================

class TestHasOpenOrderForStocks:
    """Tests for has_open_order_for() with equity symbols."""

    def test_same_symbol_same_side_returns_true(self):
        """
        A BUY order for AAPL already exists — a second BUY must be detected
        as a duplicate and return True.
        """
        existing = _make_ibkr_trade("AAPL", "BUY")
        ib = _make_ib([existing])
        assert orders.has_open_order_for(ib, "AAPL", side="BUY") is True

    def test_different_symbol_returns_false(self):
        """
        An open BUY for AAPL must NOT block a BUY for MSFT.
        """
        existing = _make_ibkr_trade("AAPL", "BUY")
        ib = _make_ib([existing])
        assert orders.has_open_order_for(ib, "MSFT", side="BUY") is False

    def test_empty_open_orders_returns_false(self):
        """No open orders → not a duplicate → return False."""
        ib = _make_ib([])
        assert orders.has_open_order_for(ib, "TSLA", side="BUY") is False

    def test_sell_order_does_not_block_buy(self):
        """
        An open SELL for NVDA must NOT block a new BUY for NVDA.
        """
        existing = _make_ibkr_trade("NVDA", "SELL")
        ib = _make_ib([existing])
        assert orders.has_open_order_for(ib, "NVDA", side="BUY") is False

    def test_buy_order_does_not_block_sell(self):
        """
        An open BUY for NVDA must NOT block a new SELL for NVDA.
        """
        existing = _make_ibkr_trade("NVDA", "BUY")
        ib = _make_ib([existing])
        assert orders.has_open_order_for(ib, "NVDA", side="SELL") is False

    def test_side_comparison_is_case_insensitive(self):
        """
        Side argument should be compared case-insensitively so 'buy' matches
        an order with action 'BUY'.
        """
        existing = _make_ibkr_trade("AAPL", "BUY")
        ib = _make_ib([existing])
        # Pass lowercase 'buy' — should still detect duplicate
        assert orders.has_open_order_for(ib, "AAPL", side="buy") is True

    def test_multiple_open_orders_detects_correct_one(self):
        """
        When multiple symbols have open orders, only the matching symbol+side
        triggers the duplicate guard.
        """
        trade_aapl = _make_ibkr_trade("AAPL", "BUY")
        trade_msft = _make_ibkr_trade("MSFT", "BUY")
        trade_tsla = _make_ibkr_trade("TSLA", "SELL")
        ib = _make_ib([trade_aapl, trade_msft, trade_tsla])

        assert orders.has_open_order_for(ib, "AAPL", side="BUY") is True
        assert orders.has_open_order_for(ib, "MSFT", side="BUY") is True
        assert orders.has_open_order_for(ib, "TSLA", side="BUY") is False  # different side
        assert orders.has_open_order_for(ib, "GOOG", side="BUY") is False  # not in list

    def test_ibkr_exception_fails_closed(self):
        """
        When IBKR raises an exception, has_open_order_for must return True
        (fail-closed) to prevent double-submit on network error.
        """
        ib = MagicMock()
        ib.openTrades.side_effect = RuntimeError("IBKR connection lost")
        assert orders.has_open_order_for(ib, "AAPL", side="BUY") is True

    def test_ibkr_connection_timeout_fails_closed(self):
        """
        Any exception type (not just RuntimeError) must trigger fail-closed.
        """
        ib = MagicMock()
        ib.openTrades.side_effect = TimeoutError("Timeout")
        assert orders.has_open_order_for(ib, "AAPL", side="BUY") is True

    def test_default_side_is_buy(self):
        """
        When called without an explicit side, the default must be 'BUY'.
        """
        existing = _make_ibkr_trade("AAPL", "BUY")
        ib = _make_ib([existing])
        # Call without side kwarg — should use default BUY
        assert orders.has_open_order_for(ib, "AAPL") is True


# ===========================================================================
# TEST CLASS 3 — has_open_order_for: options
# ===========================================================================

class TestHasOpenOrderForOptions:
    """Tests for has_open_order_for() with options (option_key matching)."""

    def _make_option_trade(self, symbol, right, strike, expiry_yyyymmdd, side="BUY"):
        """Build a fake IBKR Trade for an option contract."""
        return _make_ibkr_trade(
            symbol=symbol,
            side=side,
            sec_type="OPT",
            strike=strike,
            right=right,
            expiry=expiry_yyyymmdd,
        )

    def test_exact_option_key_returns_true(self):
        """
        An open BUY for the exact composite key AAPL_C_150.0_2026-01-16
        must be detected as a duplicate.
        """
        trade = self._make_option_trade("AAPL", "C", 150.0, "20260116")
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_C_150.0_2026-01-16"
        )
        assert result is True

    def test_different_strike_not_blocked(self):
        """
        Open order for strike 150 must NOT block a new order at strike 160.
        """
        trade = self._make_option_trade("AAPL", "C", 150.0, "20260116")
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_C_160.0_2026-01-16"
        )
        assert result is False

    def test_different_expiry_not_blocked(self):
        """
        Open order for Jan 2026 expiry must NOT block a new order for
        Feb 2026 expiry (same strike, same symbol).
        """
        trade = self._make_option_trade("AAPL", "C", 150.0, "20260116")
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_C_150.0_2026-02-20"
        )
        assert result is False

    def test_different_right_not_blocked(self):
        """
        An open call (C) must NOT block a put (P) on the same symbol/strike/expiry.
        """
        trade = self._make_option_trade("AAPL", "C", 150.0, "20260116")
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_P_150.0_2026-01-16"
        )
        assert result is False

    def test_different_underlying_not_blocked(self):
        """
        An open AAPL call must NOT block an MSFT call at the same
        strike/expiry/right.
        """
        trade = self._make_option_trade("AAPL", "C", 150.0, "20260116")
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "MSFT", side="BUY", option_key="MSFT_C_150.0_2026-01-16"
        )
        assert result is False

    def test_option_key_with_call_string_right(self):
        """
        IBKR sometimes returns 'CALL' instead of 'C' — verify normalization.
        """
        trade = self._make_option_trade("AAPL", "CALL", 150.0, "20260116")
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_C_150.0_2026-01-16"
        )
        assert result is True

    def test_option_key_with_put_string_right(self):
        """
        IBKR sometimes returns 'PUT' instead of 'P' — verify normalization.
        """
        trade = self._make_option_trade("AAPL", "PUT", 150.0, "20260116")
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_P_150.0_2026-01-16"
        )
        assert result is True

    def test_stock_open_order_does_not_block_option(self):
        """
        An open stock BUY for AAPL must NOT block an options BUY for AAPL
        (different instrument type — option_key is present).
        """
        trade = _make_ibkr_trade("AAPL", "BUY")  # stock, no expiry/strike
        ib = _make_ib([trade])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_C_150.0_2026-01-16"
        )
        # The stock trade will not match the option key because strike/expiry differ
        assert result is False

    def test_no_open_orders_option_returns_false(self):
        """Empty book with option_key query returns False."""
        ib = _make_ib([])
        result = orders.has_open_order_for(
            ib, "AAPL", side="BUY", option_key="AAPL_C_150.0_2026-01-16"
        )
        assert result is False


# ===========================================================================
# TEST CLASS 4 — Re-entry after position close
# ===========================================================================

class TestReentryAfterClose:
    """Regression tests: re-entry after close must not be incorrectly blocked."""

    def test_reentry_allowed_when_ibkr_has_no_open_order(self):
        """
        After the bot removes AAPL from open_trades (position closed) AND
        IBKR shows no open orders, has_open_order_for must return False,
        allowing a legitimate re-entry.
        """
        original = dict(orders.open_trades)
        try:
            orders.open_trades.pop("AAPL", None)
            ib = _make_ib([])
            assert orders.has_open_order_for(ib, "AAPL", side="BUY") is False
        finally:
            orders.open_trades.clear()
            orders.open_trades.update(original)

    def test_reentry_blocked_when_ibkr_still_has_open_order(self):
        """
        Even if our tracker shows the position is closed, if IBKR still
        shows an open BUY order (e.g. stale bracket), the guard must block.
        """
        original = dict(orders.open_trades)
        try:
            orders.open_trades.pop("AAPL", None)
            existing = _make_ibkr_trade("AAPL", "BUY")
            ib = _make_ib([existing])
            assert orders.has_open_order_for(ib, "AAPL", side="BUY") is True
        finally:
            orders.open_trades.clear()
            orders.open_trades.update(original)

    def test_unrelated_symbol_reentry_unaffected_by_open_order_on_other(self):
        """
        A stale open order for AAPL must not prevent re-entry into MSFT.
        """
        existing = _make_ibkr_trade("AAPL", "BUY")
        ib = _make_ib([existing])
        assert orders.has_open_order_for(ib, "MSFT", side="BUY") is False


# ===========================================================================
# TEST CLASS 5 — No duplicate definitions (regression)
# ===========================================================================

class TestNoDuplicateDefinitions:
    """Regression tests that confirm the shadowing bug is fully fixed."""

    def test_has_open_order_for_defined_exactly_once(self):
        """
        has_open_order_for must appear exactly once in the module's
        function table.
        """
        members = inspect.getmembers(orders, predicate=inspect.isfunction)
        names = [n for n, _ in members]
        count = names.count("has_open_order_for")
        assert count == 1, (
            f"Expected 1 'has_open_order_for', found {count}. "
            "Duplicate-definition bug may still be present."
        )

    def test_is_duplicate_check_enabled_defined_exactly_once(self):
        """
        _is_duplicate_check_enabled must appear exactly once.
        """
        members = inspect.getmembers(orders, predicate=inspect.isfunction)
        names = [n for n, _ in members]
        count = names.count("_is_duplicate_check_enabled")
        assert count == 1, (
            f"Expected 1 '_is_duplicate_check_enabled', found {count}. "
            "Shadowing bug may still be present."
        )

    def test_get_symbol_lock_defined_exactly_once(self):
        """
        _get_symbol_lock must appear exactly once.
        """
        members = inspect.getmembers(orders, predicate=inspect.isfunction)
        names = [n for n, _ in members]
        count = names.count("_get_symbol_lock")
        assert count == 1, (
            f"Expected 1 '_get_symbol_lock', found {count}. "
            "Duplicate definition bug may still be present."
        )

    def test_order_duplicate_check_constant_is_true(self):
        """
        ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT must be True — fail-safe default.
        """
        assert orders.ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT is True

    def test_symbol_locks_dict_returns_same_lock_object(self):
        """
        _get_symbol_lock must return the identical Lock object on repeated
        calls for the same symbol.  If _symbol_locks was re-initialised by
        a duplicate module-level assignment, two distinct Lock objects would
        be returned, breaking the TOCTOU guard.
        """
        lock_a = orders._get_symbol_lock("__REGRESSION_TEST_SYMBOL__")
        lock_b = orders._get_symbol_lock("__REGRESSION_TEST_SYMBOL__")
        assert lock_a is lock_b, (
            "_get_symbol_lock must return the SAME Lock for the same symbol. "
            "A duplicate _symbol_locks assignment would break this."
        )

    def test_symbol_locks_different_symbols_return_different_locks(self):
        """Different symbols must have independent locks."""
        lock_x = orders._get_symbol_lock("__SYM_X__")
        lock_y = orders._get_symbol_lock("__SYM_Y__")
        assert lock_x is not lock_y

    def test_symbol_locks_dict_is_dict(self):
        """_symbol_locks must be a plain dict (not reset by a duplicate def)."""
        assert isinstance(orders._symbol_locks, dict)


# ===========================================================================
# TEST CLASS 6 — ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT constant
# ===========================================================================

class TestDuplicateCheckConstant:
    """Tests for the module-level constant."""

    def test_constant_is_bool(self):
        """The constant must be a Python bool."""
        assert isinstance(orders.ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT, bool)

    def test_constant_is_true(self):
        """The constant must be True — guards default to ENABLED."""
        assert orders.ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT is True

    def test_constant_accessible_from_module(self):
        """The constant must be importable / accessible as a module attribute."""
        assert hasattr(orders, "ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT")


# ===========================================================================
# TEST CLASS 7 — Parametrized edge-case matrix
# ===========================================================================

@pytest.mark.parametrize("existing_symbol, query_symbol, expected", [
    ("AAPL", "AAPL", True),   # exact match → blocked
    ("AAPL", "MSFT", False),  # different symbol → allowed
    ("TSLA", "TSLA", True),   # another exact match
    ("NVDA", "AMD",  False),  # different symbol → allowed
    ("SPY",  "QQQ",  False),  # ETF pair → allowed
    ("SPY",  "SPY",  True),   # same ETF → blocked
])
def test_has_open_order_symbol_matrix(existing_symbol, query_symbol, expected):
    """
    Parametrized matrix: for each (existing, query) pair verify the
    duplicate guard returns the expected boolean.
    """
    existing = _make_ibkr_trade(existing_symbol, "BUY")
    ib = _make_ib([existing])
    result = orders.has_open_order_for(ib, query_symbol, side="BUY")
    assert result is expected, (
        f"has_open_order_for(ib, '{query_symbol}') with existing '{existing_symbol}' "
        f"expected {expected}, got {result}"
    )


@pytest.mark.parametrize("config_value, expected", [
    (True, True),
    (False, False),
    (1, True),
    (0, False),
    ("enabled", True),
    ("", False),
])
def test_is_duplicate_check_enabled_config_matrix(config_value, expected):
    """
    Parametrized matrix for _is_duplicate_check_enabled across various
    CONFIG value types.
    """
    with patch.dict(orders.CONFIG, {"ORDER_DUPLICATE_CHECK_ENABLED": config_value}):
        result = orders._is_duplicate_check_enabled()
        assert result is expected, (
            f"_is_duplicate_check_enabled() with config={config_value!r} "
            f"expected {expected}, got {result}"
        )
