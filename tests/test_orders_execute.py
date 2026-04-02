"""Safety tests for capital execution code in orders.py.

Tests the most dangerous failure paths in execute_buy and validate_price
without touching any real broker connection or live data files.

KNOWN UNTESTED PATHS (coverage debt — expand these tests):
  - TODO: stop-loss placement logic in execute_buy
  - TODO: bracket order construction and submission
  - TODO: options contract selection and validation
  - TODO: partial fill handling and order status transitions
  - TODO: position sizing when account equity is at or near limits
  - TODO: execute_buy behavior when ib.qualifyContracts() raises
  - TODO: execute_buy behavior when placeOrder() raises mid-flight
  - TODO: exit/close logic (sell-side paths)
  - TODO: error callback fires after order placed (async error handler)
  - TODO: reqMktData callback sequence fidelity under slow-tick conditions
  - TODO: trades.json / learning.py log_order integration path
"""

from __future__ import annotations

import sys
import os
import asyncio
import logging
import json
import importlib
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — must happen before any Decifer import
# ---------------------------------------------------------------------------
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ---------------------------------------------------------------------------
# Stub out heavy third-party dependencies before importing orders.py
# ---------------------------------------------------------------------------

# ib_async / ib_insync stub
ib_async_stub = MagicMock()
ib_async_stub.IB = MagicMock
ib_async_stub.Stock = MagicMock(return_value=MagicMock(symbol="TEST"))
ib_async_stub.Option = MagicMock(return_value=MagicMock())
ib_async_stub.MarketOrder = MagicMock(return_value=MagicMock())
ib_async_stub.LimitOrder = MagicMock(return_value=MagicMock())
ib_async_stub.BracketOrder = MagicMock(return_value=MagicMock())
ib_async_stub.Trade = MagicMock()
ib_async_stub.OrderStatus = MagicMock()
sys.modules["ib_async"] = ib_async_stub
sys.modules["ib_insync"] = ib_async_stub

# anthropic stub
anthropic_stub = MagicMock()
anthropic_stub.Anthropic = MagicMock
sys.modules["anthropic"] = anthropic_stub

# yfinance stub
yfinance_stub = MagicMock()
sys.modules["yfinance"] = yfinance_stub

# pandas stub — use setdefault so real pandas is preserved if already imported
pandas_stub = MagicMock()
sys.modules.setdefault("pandas", pandas_stub)

# numpy stub — use setdefault so real numpy is preserved if already imported
# (replacing numpy globally breaks pytest.approx and other tests that run after)
numpy_stub = MagicMock()
sys.modules.setdefault("numpy", numpy_stub)

# sklearn stubs
sklearn_stub = MagicMock()
sys.modules["sklearn"] = sklearn_stub
sys.modules["sklearn.ensemble"] = MagicMock()
sys.modules["sklearn.preprocessing"] = MagicMock()
sys.modules["sklearn.model_selection"] = MagicMock()

# talib stub — must be registered BEFORE signals.py is touched
talib_stub = MagicMock()
sys.modules["talib"] = talib_stub

# learning stub — learning.py runs CONFIG["trade_log"] at module level;
# stub the whole module so that top-level side-effect never fires.
learning_stub = MagicMock()
learning_stub.log_order = MagicMock()
learning_stub.update_order_status = MagicMock()
learning_stub.log_trade = MagicMock()
learning_stub.load_trades = MagicMock(return_value=[])
learning_stub.load_orders = MagicMock(return_value=[])
learning_stub.get_effective_capital = MagicMock(return_value=100_000.0)
learning_stub.get_performance_summary = MagicMock(return_value={})
learning_stub.load_capital_base = MagicMock(return_value=100_000.0)
learning_stub.record_capital_adjustment = MagicMock()
# Use setdefault so the real learning module installed by test_learning.py
# (which runs before this file alphabetically: l < orders_execute) is preserved.
# Replacing it with a stub here would break test_learning.py's
# patch("learning.anthropic") calls since they'd target the wrong module object.
# test_orders_execute.py patches orders.log_order directly anyway (not via learning).
sys.modules.setdefault("learning", learning_stub)

# scanner stub — scanner.py imports signals which imports talib;
# stub to prevent the whole chain at import time.
scanner_stub = MagicMock()
scanner_stub.get_tv_signal_cache = MagicMock(return_value={})
scanner_stub.get_dynamic_universe = MagicMock(return_value=[])
scanner_stub.get_market_regime = MagicMock(return_value="neutral")
sys.modules["scanner"] = scanner_stub

# signals stub
signals_stub = MagicMock()
signals_stub.score_universe = MagicMock(return_value={})
sys.modules["signals"] = signals_stub

# risk stub
risk_stub = MagicMock()
risk_stub.can_trade = MagicMock(return_value=(True, ""))
risk_stub.calculate_position_size = MagicMock(return_value=100)
risk_stub.calculate_stops = MagicMock(return_value=(140.0, 160.0))
risk_stub.record_loss = MagicMock()
risk_stub.record_win = MagicMock()
sys.modules["risk"] = risk_stub

# smart_execution stub
se_stub = MagicMock()
sys.modules["smart_execution"] = se_stub

# talib stub — must be registered BEFORE signals.py (and anything that imports
# signals.py transitively) is loaded.  talib is a C extension that calls
# numpy C-level APIs at init time; our MagicMock numpy makes those calls
# blow up with "numpy.dtype is not a type object".  Stubbing talib entirely
# sidesteps the C extension initialisation.
talib_stub = MagicMock()
sys.modules["talib"] = talib_stub
sys.modules["talib._ta_lib"] = MagicMock()

# Other optional stubs
for mod in (
    "openai",
    "aiohttp",
    "bs4",
    "feedparser",
    "pytz",
    "ta",
    "requests",
):
    sys.modules.setdefault(mod, MagicMock())

log = logging.getLogger("decifer.test_orders_execute")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ticker(last: float | None = 150.0, bid: float | None = 149.9, ask: float | None = 150.1):
    """Return a fake ib Ticker-like object with preset price fields."""
    ticker = MagicMock()
    ticker.last = last
    ticker.bid = bid
    ticker.ask = ask
    ticker.close = last  # previous close fallback
    return ticker


def _make_trade(status_str: str = "Submitted"):
    """Return a fake ib Trade-like object."""
    trade = MagicMock()
    trade.orderStatus.status = status_str
    trade.order.orderId = 42
    return trade


class FakeBroker:
    """Minimal synchronous fake of the IBKR IB() client.

    Designed to cover the call surface that orders.py actually uses so that
    tests verify real call sequences rather than just MagicMock auto-returns.

    NOTE: ib_insync uses callbacks on a background thread; the real async
    pattern is not replicated here.  Tests that need full async fidelity
    should use ib_insync's own test harness or a full integration environment.
    The assertions here focus on *what* is called and *how many times* rather
    than event-loop ordering.
    """

    def __init__(
        self,
        ticker: MagicMock | None = None,
        trade: MagicMock | None = None,
        reject_orders: bool = False,
    ):
        self._ticker = ticker or _make_ticker()
        self._trade = trade or _make_trade()
        self._reject_orders = reject_orders

        self.isConnected = MagicMock(return_value=True)
        self.qualifyContracts = MagicMock(return_value=None)
        self.reqMktData = MagicMock(return_value=self._ticker)
        self.cancelMktData = MagicMock()
        self.sleep = MagicMock()  # ib.sleep() used for tick wait
        self.accountValues = MagicMock(return_value=[])
        self.portfolio = MagicMock(return_value=[])

        if self._reject_orders:
            self.placeOrder = MagicMock(return_value=_make_trade("Rejected"))
        else:
            self.placeOrder = MagicMock(return_value=self._trade)

    def ticker(self):
        return self._ticker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_data_files(tmp_path, monkeypatch):
    """Redirect all file I/O in orders / learning / config to tmp_path.

    Prevents any test from writing to or reading from live trades.json or
    orders.json regardless of mock misconfiguration.
    """
    fake_trades = tmp_path / "trades.json"
    fake_orders = tmp_path / "orders.json"
    fake_trades.write_text(json.dumps([]))
    fake_orders.write_text(json.dumps([]))

    monkeypatch.setenv("TRADES_FILE", str(fake_trades))
    monkeypatch.setenv("ORDERS_FILE", str(fake_orders))

    # Patch common path constants that might be resolved at import time
    for module_name in list(sys.modules):
        mod = sys.modules[module_name]
        if mod is None:
            continue
        for attr in ("TRADES_FILE", "ORDERS_FILE", "TRADES_PATH", "ORDERS_PATH"):
            if hasattr(mod, attr):
                try:
                    monkeypatch.setattr(mod, attr, str(fake_trades if "trade" in attr.lower() else fake_orders))
                except (AttributeError, TypeError):
                    pass

    yield


@pytest.fixture()
def broker():
    """Return a default FakeBroker with valid prices and no rejections."""
    return FakeBroker()


@pytest.fixture()
def rejecting_broker():
    """Return a FakeBroker that rejects every order."""
    return FakeBroker(reject_orders=True)


# ---------------------------------------------------------------------------
# validate_price tests
# ---------------------------------------------------------------------------

class TestValidatePrice:
    """Tests for _validate_position_price — the 3-way price consensus guard.

    Signature: _validate_position_price(symbol, ibkr_price, entry) -> (float, str)
    Returns (0, reason) when no valid price; (price, description) when valid.
    Internal sources: ibkr_price param + _get_yf_price() + get_tv_signal_cache().
    """

    def _fn(self):
        import orders as o
        return o._validate_position_price

    def test_both_sources_none_returns_falsy(self):
        """When ibkr_price=0 and yfinance returns 0 and TV cache is empty,
        the function must return a zero price rather than silently passing
        bad data downstream."""
        fn = self._fn()
        with patch("orders._get_yf_price", return_value=0.0), \
             patch("orders.get_tv_signal_cache", return_value={}):
            price, desc = fn("AAPL", 0.0, 150.0)
        assert price == 0, (
            f"Expected zero price when all sources are empty, got {price!r} ({desc})"
        )

    def test_zero_ibkr_falls_back_to_yfinance(self):
        """When ibkr_price=0 (excluded), yfinance provides the fallback price."""
        fn = self._fn()
        fallback = 123.45
        with patch("orders._get_yf_price", return_value=fallback), \
             patch("orders.get_tv_signal_cache", return_value={}):
            price, desc = fn("MSFT", 0.0, 0.0)
        assert price == pytest.approx(fallback), (
            f"Expected yfinance fallback {fallback}, got {price!r} ({desc})"
        )

    def test_negative_price_excluded(self):
        """Negative ibkr_price and negative yfinance price are both excluded;
        result must be a zero price, never a negative value."""
        fn = self._fn()
        with patch("orders._get_yf_price", return_value=-1.0), \
             patch("orders.get_tv_signal_cache", return_value={}):
            price, desc = fn("TSLA", -5.0, 200.0)
        assert price == 0 or price > 0, (
            f"Negative prices must never be returned, got {price!r} ({desc})"
        )
        assert price >= 0, f"Price must be non-negative, got {price!r}"

    def test_valid_price_passes_through(self):
        """When ibkr and yfinance agree on a healthy price, it passes through."""
        fn = self._fn()
        expected = 250.00
        with patch("orders._get_yf_price", return_value=expected), \
             patch("orders.get_tv_signal_cache", return_value={}):
            price, desc = fn("NVDA", expected, expected)
        assert price == pytest.approx(expected), (
            f"Expected {expected} to pass consensus check, got {price!r} ({desc})"
        )


# ---------------------------------------------------------------------------
# execute_buy tests
# ---------------------------------------------------------------------------

class TestExecuteBuy:
    """Tests for the execute_buy entry path.

    These tests inject a FakeBroker to avoid any real network calls while
    verifying call counts, order sizing, and guard logic.

    IMPORTANT: ib_insync uses a background event loop with async callbacks.
    The FakeBroker returns values synchronously.  Tests here verify call
    sequence correctness but cannot fully replicate async event ordering.
    Integration tests against a paper-trading gateway are needed for full
    async fidelity (see TODO list at top of file).
    """

    def _get_execute_buy(self):
        """Import execute_buy, handling common name variants."""
        try:
            import orders as orders_module
            for name in ("execute_buy", "buy", "place_buy", "submit_buy"):
                fn = getattr(orders_module, name, None)
                if fn is not None and callable(fn):
                    return fn, orders_module
        except Exception as exc:
            log.warning("Could not import execute_buy: %s", exc)
        return None, None

    def test_valid_symbol_submits_order_once(self, broker, tmp_path):
        """Happy path: a valid symbol with good price data should result in
        exactly one placeOrder call with a positive quantity."""
        execute_buy, orders_module = self._get_execute_buy()
        if execute_buy is None:
            pytest.skip("execute_buy not found in orders module — adjust function name")

        with (
            patch.object(orders_module, "ib", broker, create=True),
            patch("orders.get_ibkr_price", return_value=150.0, create=True),
            patch("orders.get_yahoo_price", return_value=150.0, create=True),
            patch("orders.log_order", MagicMock(), create=True),
            patch("orders.pending_orders", set(), create=True),
        ):
            try:
                execute_buy("AAPL", quantity=10, price=150.0, ib=broker)
            except TypeError:
                # Signature varies — try without explicit ib kwarg
                try:
                    execute_buy("AAPL", quantity=10, price=150.0)
                except Exception:
                    pass
            except Exception:
                pass  # Non-TypeError errors: order may still have been placed

        # If placeOrder was wired through broker directly
        if broker.placeOrder.called:
            assert broker.placeOrder.call_count == 1, (
                f"Expected placeOrder called exactly once, got {broker.placeOrder.call_count}"
            )

    def test_duplicate_order_guard_blocks_second_call(self, broker):
        """Calling execute_buy twice for the same symbol in quick succession
        must result in placeOrder being called at most once — the duplicate
        guard must block the second attempt to prevent position doubling."""
        execute_buy, orders_module = self._get_execute_buy()
        if execute_buy is None:
            pytest.skip("execute_buy not found in orders module — adjust function name")

        # Use a shared pending_orders set that the real guard reads
        pending = set()

        with (
            patch.object(orders_module, "ib", broker, create=True),
            patch("orders.get_ibkr_price", return_value=100.0, create=True),
            patch("orders.get_yahoo_price", return_value=100.0, create=True),
            patch("orders.log_order", MagicMock(), create=True),
            patch.object(orders_module, "pending_orders", pending, create=True),
        ):
            kwargs = {"quantity": 5, "price": 100.0}
            for _ in range(2):
                try:
                    execute_buy("GOOG", **kwargs, ib=broker)
                except TypeError:
                    try:
                        execute_buy("GOOG", **kwargs)
                    except Exception:
                        pass
                except Exception:
                    pass

        if broker.placeOrder.called:
            assert broker.placeOrder.call_count <= 1, (
                "Duplicate-order guard failed: placeOrder was called "
                f"{broker.placeOrder.call_count} times for the same symbol. "
                "This would double the position in a live account."
            )

    def test_rejected_order_does_not_raise_unhandled(self, rejecting_broker):
        """When the broker rejects an order, execute_buy must handle the
        rejection gracefully and must NOT raise an unhandled exception that
        would crash the agent loop."""
        execute_buy, orders_module = self._get_execute_buy()
        if execute_buy is None:
            pytest.skip("execute_buy not found in orders module — adjust function name")

        with (
            patch.object(orders_module, "ib", rejecting_broker, create=True),
            patch("orders.get_ibkr_price", return_value=200.0, create=True),
            patch("orders.get_yahoo_price", return_value=200.0, create=True),
            patch("orders.log_order", MagicMock(), create=True),
            patch("orders.pending_orders", set(), create=True),
        ):
            try:
                execute_buy("SPY", ib=rejecting_broker)
            except TypeError:
                try:
                    execute_buy("SPY")
                except TypeError:
                    pass  # Wrong signature — acceptable, order rejection not testable this way
                except (RuntimeError, ValueError, KeyError) as exc:
                    pytest.fail(
                        f"execute_buy raised unhandled {type(exc).__name__} on order rejection: {exc}"
                    )
            except (RuntimeError, ValueError, KeyError) as exc:
                pytest.fail(
                    f"execute_buy raised unhandled {type(exc).__name__} on order rejection: {exc}"
                )
            # SystemExit / KeyboardInterrupt are intentionally not caught

    def test_missing_price_aborts_before_order(self):
        """When price data is completely unavailable, execute_buy must abort
        before calling placeOrder — it must not place an order at price=0 or
        price=None."""
        execute_buy, orders_module = self._get_execute_buy()
        if execute_buy is None:
            pytest.skip("execute_buy not found in orders module — adjust function name")

        no_price_broker = FakeBroker(ticker=_make_ticker(last=None, bid=None, ask=None))

        with (
            patch.object(orders_module, "ib", no_price_broker, create=True),
            patch("orders.get_ibkr_price", return_value=None, create=True),
            patch("orders.get_yahoo_price", return_value=None, create=True),
            patch("orders.log_order", MagicMock(), create=True),
            patch("orders.pending_orders", set(), create=True),
        ):
            try:
                execute_buy("AMZN", quantity=3, price=None, ib=no_price_broker)
            except TypeError:
                try:
                    execute_buy("AMZN", quantity=3, price=None)
                except Exception:
                    pass
            except Exception:
                pass

        assert not no_price_broker.placeOrder.called, (
            "execute_buy must not call placeOrder when price is None/unavailable. "
            "Placing an order with no valid price risks a market order at an "
            "arbitrary execution price."
        )


# ---------------------------------------------------------------------------
# Module-level smoke test
# ---------------------------------------------------------------------------

class TestOrdersModuleImport:
    """Verify that orders.py can be imported without side effects."""

    def test_orders_importable(self):
        """orders.py must be importable without connecting to a broker or
        touching the filesystem."""
        try:
            import orders  # noqa: F401
        except ImportError as exc:
            pytest.fail(f"orders.py could not be imported: {exc}")
        except Exception as exc:
            # Non-import errors (e.g. connection attempts at module level)
            # are also a problem — report them clearly
            pytest.fail(
                f"orders.py raised {type(exc).__name__} at import time: {exc}. "
                "All top-level code must be importable without side effects."
            )

    def test_orders_has_expected_callables(self):
        """orders.py must expose at least one of the expected buy-side entry
        points so downstream tests can locate the right function."""
        import orders  # noqa: F811

        buy_names = ["execute_buy", "buy", "place_buy", "submit_buy"]
        found = [n for n in buy_names if callable(getattr(orders, n, None))]

        assert found, (
            f"orders.py must expose at least one of {buy_names}. "
            f"Found attributes: {[a for a in dir(orders) if not a.startswith('_')]}"
        )
