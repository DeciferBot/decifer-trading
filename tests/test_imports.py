"""tests/test_imports.py — Hot-path import smoke test.

Guards against a repeat of the 2026-04-13 Ruff incident where bulk lint
silently stripped load-bearing re-exports from facade modules.  Those
names are only referenced via runtime getattr (``_om = sys.modules.get(
'orders', ...); CONFIG = _om.CONFIG``), so static linters treat them as
unused while the runtime crashes with AttributeError / ImportError the
first time that code branch executes.

Each test function below pins one module's contract.  A failure means a
lint pass (or refactor) removed something a hot path depends on at runtime.
"""

import sys
import types

import pytest

# ---------------------------------------------------------------------------
# conftest.py already registered ib_async / anthropic / yfinance stubs when
# pytest collected this file — no extra setup needed here.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1.  Every hot-path module must be importable without error
# ---------------------------------------------------------------------------

HOT_PATH_MODULES = [
    # Orders subsystem
    "orders",
    "orders_core",
    "orders_contracts",
    "orders_guards",
    "orders_options",
    "orders_portfolio",
    "orders_state",
    # Signal engine
    "signals",
    "signal_pipeline",
    "signal_types",
    "signal_dispatcher",
    # Bot orchestration
    "bot_ibkr",
    "bot_trading",
    "bot_state",
    "bot_hot_reload",
    # IC subsystem
    "ic_calculator",
    # Supporting modules
    "scanner",
    "agents",
    "risk",
    "learning",
    "config",
    "position_sizing",
    "portfolio_manager",
]


@pytest.mark.parametrize("module_name", HOT_PATH_MODULES)
def test_module_importable(module_name):
    """Each hot-path module must import without raising ImportError / AttributeError."""
    mod = sys.modules.get(module_name)
    if mod is None:
        mod = __import__(module_name)
    assert isinstance(mod, types.ModuleType), f"{module_name} did not resolve to a module"


# ---------------------------------------------------------------------------
# 2.  orders.py facade: every name the runtime rebind pattern reads via getattr
#
#     orders_core.py (lines ~143-154, ~892-903, ~1258-1263) does:
#         _om = sys.modules.get('orders', sys.modules[__name__])
#         CONFIG = _om.CONFIG
#         check_correlation = _om.check_correlation   ...etc
#
#     Any name dropped by a future Ruff/lint pass causes AttributeError at
#     the first execute_buy / execute_short / execute_sell call.
# ---------------------------------------------------------------------------

# All names referenced by the _om.* rebind in orders_core.py
_ORDERS_CORE_REBIND = [
    "CONFIG",
    "check_correlation",
    "check_combined_exposure",
    "check_sector_concentration",
    "calculate_position_size",
    "calculate_stops",
    "log_order",
    "_get_alpaca_price",
    "MarketOrder",
    "LimitOrder",
    "StopOrder",
    "_validate_position_price",
    "_get_ibkr_price",
    "record_win",
    "record_loss",
]

# Names referenced in other hot paths (portfolio review, flatten_all, etc.)
_ORDERS_PORTFOLIO_REBIND = [
    "close_position",
    "flatten_all",
    "get_open_positions",
    "reconcile_with_ibkr",
    "update_position_prices",
    "update_positions_from_ibkr",
]

# Names referenced by test patches and the options execution path
_ORDERS_EXTRA = [
    "IB",
    "Forex",
    "Future",
    "Stock",
    "Option",
    "ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT",
    "ORDERS_FILE",
    "TRADES_FILE",
    "_MAX_OPTION_SELL_RETRIES",
    "_OPTION_SELL_COOLDOWN",
    "_cancel_ibkr_order_by_id",
    "_check_ibkr_open_order",
    "_flatten_in_progress",
    "_flatten_lock",
    "_get_ibkr_bid_ask",
    "_get_symbol_lock",
    "_ibkr_item_to_key",
    "_is_duplicate_check_enabled",
    "_is_option_contract",
    "_is_recently_closed",
    "_option_sell_attempts",
    "_pending_option_exits",
    "_safe_del_trade",
    "_safe_update_trade",
    "_trades_lock",
    "_wait_for_order_book_clear",
    "active_trades",
    "cancel_order_by_id",
    "execute_buy",
    "execute_buy_option",
    "execute_sell",
    "execute_sell_option",
    "execute_short",
    "flush_pending_option_exits",
    "get_contract",
    "has_open_order_for",
    "is_equities_extended_hours",
    "is_options_market_open",
    "log",
    "open_orders",
    "open_trades",
    "recently_closed",
    "update_tranche_status",
    "update_trailing_stops",
]

_ALL_ORDERS_ATTRS = _ORDERS_CORE_REBIND + _ORDERS_PORTFOLIO_REBIND + _ORDERS_EXTRA


@pytest.mark.parametrize("attr", _ALL_ORDERS_ATTRS)
def test_orders_facade_attr(attr):
    """orders.py must expose every name the runtime rebind pattern reads."""
    import orders

    assert hasattr(orders, attr), (
        f"orders.{attr} missing — a lint pass may have stripped a load-bearing re-export. "
        "See orders.py __all__ and the historical note in the module docstring."
    )


# ---------------------------------------------------------------------------
# 3.  orders_contracts.py self-rebind: _get_alpaca_price
#
#     _validate_position_price() does:
#         _om = sys.modules.get("orders_contracts", sys.modules[__name__])
#         _gap  = _om._get_alpaca_price
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("attr", ["_get_alpaca_price"])
def test_orders_contracts_self_rebind(attr):
    """orders_contracts must expose _get_alpaca_price for its own rebind."""
    import orders_contracts

    assert hasattr(orders_contracts, attr), (
        f"orders_contracts.{attr} missing — _validate_position_price() will crash at runtime."
    )


# ---------------------------------------------------------------------------
# 4.  bot.py re-exports for the sys.modules.get("bot") rebind
#
#     bot_trading.py:1039  _bot_mod.check_and_reload()
#     bot_dashboard.py     _bot.DASHBOARD_HTML, _bot.save_favourites,
#                          _bot.save_settings_overrides, _bot._sync_dash_from_config
#     bot_hot_reload.py    _file_hash, _file_hashes, _init_hashes
# ---------------------------------------------------------------------------

_BOT_REEXPORTS = [
    "check_and_reload",    # bot_trading.py:1039
    "_file_hash",          # bot_hot_reload internal; exported for tests
    "_file_hashes",        # tests mutate this dict directly
    "_init_hashes",        # used on startup
    "DASHBOARD_HTML",      # bot_dashboard.py:429
    "save_favourites",     # bot_dashboard.py:1010
    "save_settings_overrides",  # bot_dashboard.py:1035
    "_sync_dash_from_config",   # bot_dashboard.py:1036
]


@pytest.mark.parametrize("attr", _BOT_REEXPORTS)
def test_bot_reexports(attr):
    """bot.py must expose every name accessed via sys.modules.get('bot')."""
    import bot  # noqa: F401 (side effect: registers itself in sys.modules["bot"])

    _bot = sys.modules.get("bot")
    assert _bot is not None, "bot module not registered in sys.modules['bot']"
    assert hasattr(_bot, attr), (
        f"bot.{attr} missing — a Ruff/lint pass may have stripped a load-bearing re-export "
        "from the bot_hot_reload block in bot.py (lines ~180-185)."
    )


# ---------------------------------------------------------------------------
# 5.  ic_calculator.py shim: every symbol imported by production callers
#
#     signals.py:          DIMENSIONS, get_current_weights
#     orders_core.py:      get_current_weights
#     signal_pipeline.py:  get_system_ic_health, get_short_quality_score
#     bot_trading.py:      compare_live_vs_historical_ic, update_ic_weights
#     position_sizing.py:  get_short_quality_score
#     bot_dashboard.py:    multiple names via `from ic_calculator import (...)`
#     learning.py:         DIMENSIONS, EQUAL_WEIGHTS
#     orders_options.py:   get_current_weights
# ---------------------------------------------------------------------------

_IC_CALCULATOR_SYMBOLS = [
    # Constants
    "DIMENSIONS",
    "EQUAL_WEIGHTS",
    "IC_HISTORY_FILE",
    "IC_LIVE_FILE",
    "IC_WEIGHTS_FILE",
    "_LIVE_IC_REPORT_FILE",
    # Core
    "compute_rolling_ic",
    "normalize_ic_weights",
    # Storage / weights
    "get_current_weights",
    "update_ic_weights",
    "get_ic_weight_history",
    # Monitoring
    "get_system_ic_health",
    "get_short_quality_score",
    "check_ic_divergence",
    # Live IC
    "compare_live_vs_historical_ic",
    "update_live_ic",
    "compute_live_trade_ic",
    "get_live_ic_progress",
]


@pytest.mark.parametrize("attr", _IC_CALCULATOR_SYMBOLS)
def test_ic_calculator_shim(attr):
    """ic_calculator.py shim must re-export every symbol production code imports from it."""
    import ic_calculator

    assert hasattr(ic_calculator, attr), (
        f"ic_calculator.{attr} missing — the ic/ package split may have left a gap in the shim. "
        "Add the missing re-export to ic_calculator.py."
    )


# ---------------------------------------------------------------------------
# 6.  Rebind simulation: import orders first, then orders_core, and verify
#     the rebind pattern resolves correctly at call time
#
#     This is the exact sequence that happens when bot_trading.py calls
#     execute_buy() for the first time after a fresh bot start.
# ---------------------------------------------------------------------------

def test_orders_core_rebind_resolves():
    """The _om rebind in execute_buy must resolve all attributes without AttributeError."""
    # Ensure orders facade is imported first (as in production)
    import orders  # noqa: F401
    import orders_core  # noqa: F401

    _om = sys.modules.get("orders", sys.modules["orders_core"])

    failures = []
    for attr in _ORDERS_CORE_REBIND:
        try:
            getattr(_om, attr)
        except AttributeError:
            failures.append(attr)

    assert not failures, (
        f"The orders rebind pattern would crash at runtime for: {failures}. "
        "These names are missing from orders.py facade."
    )


def test_orders_contracts_rebind_resolves():
    """The _om rebind in _validate_position_price must resolve _get_alpaca_price."""
    import orders_contracts  # noqa: F401

    _om = sys.modules.get("orders_contracts", sys.modules["orders_contracts"])

    for attr in ["_get_alpaca_price"]:
        try:
            getattr(_om, attr)
        except AttributeError as exc:
            pytest.fail(
                f"orders_contracts rebind would crash: {exc}. "
                "Ensure orders_contracts.py imports _get_alpaca_price."
            )
