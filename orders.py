# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders.py                                  ║
# ║   Facade — re-exports from the split orders_* modules        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""Facade module re-exporting the split ``orders_*`` submodules.

The ``_om = sys.modules.get('orders', sys.modules[__name__])`` rebind
pattern in ``orders_core.py`` (and similar patterns elsewhere) depends on
these names existing as attributes of this module at runtime. Every name
listed in ``__all__`` below is load-bearing — do NOT remove these imports
in a lint cleanup. Ruff's F401 respects ``__all__`` and will not flag them.

Historical note: a bulk Ruff lint pass on 2026-04-13 stripped these imports
as "unused", which crashed portfolio review and the full execute_buy /
execute_short / execute_sell path with ``AttributeError: module 'orders'
has no attribute 'CONFIG'``. See commit message for ``409964c`` area.
"""

from __future__ import annotations

import threading

from ib_async import IB, Forex, Future, LimitOrder, MarketOrder, Option, Stock, StopOrder

from bot_ibkr import cancel_with_reason
from config import CONFIG
from learning import log_order
from orders_contracts import (
    _cancel_ibkr_order_by_id,
    _get_alpaca_price,
    _get_ibkr_bid_ask,
    _get_ibkr_price,
    _ibkr_item_to_key,
    _is_option_contract,
    _validate_position_price,
    get_contract,
    is_equities_extended_hours,
    is_options_market_open,
)
from orders_core import execute_buy, execute_sell, execute_short
from orders_guards import (
    _check_ibkr_open_order,
    _is_duplicate_check_enabled,
    has_open_order_for,
)
from orders_options import (
    _MAX_OPTION_SELL_RETRIES,
    _OPTION_SELL_COOLDOWN,
    _option_sell_attempts,
    _pending_option_exits,
    execute_buy_option,
    execute_sell_option,
    flush_pending_option_exits,
    update_tranche_status,
    update_trailing_stops,
)
from orders_portfolio import (
    _GLOBAL_CANCEL_POLL_INTERVAL,
    _GLOBAL_CANCEL_WAIT_SECS,
    _wait_for_order_book_clear,
    close_position,
    flatten_all,
    get_open_positions,
    reconcile_with_ibkr,
    update_position_prices,
    update_positions_from_ibkr,
)
from orders_state import (
    ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT,
    ORDERS_FILE,
    TRADES_FILE,
    _flatten_in_progress,
    _flatten_lock,
    _get_symbol_lock,
    _is_recently_closed,
    _safe_del_trade,
    _safe_set_trade,
    _safe_update_trade,
    _symbol_locks,
    _trades_lock,
    active_trades,
    log,
    open_orders,
    open_trades,
    recently_closed,
)
from position_sizing import calculate_stops
from risk import (
    calculate_position_size,
    check_combined_exposure,
    check_correlation,
    check_sector_concentration,
    record_loss,
    record_win,
)


def cancel_order_by_id(ib, order_id) -> bool:
    """Cancel an open IBKR order by orderId.

    Returns True if the order was found and cancellation was requested.
    Callers should call ``sync_orders_from_ibkr()`` and update ``open_trades``
    after a successful cancel.
    """
    for t in ib.openTrades():
        if t.order.orderId == order_id:
            cancel_with_reason(ib, t.order, "manual cancel by order ID (dashboard)")
            ib.sleep(1)
            return True
    return False


# Public API — every name below is load-bearing (referenced by runtime
# rebind pattern in orders_core.py / orders_contracts.py, or by tests that
# @patch('orders.*'). DO NOT prune without verifying callers first.
__all__ = [
    "CONFIG",
    "IB",
    "LimitOrder",
    "MarketOrder",
    "Option",
    "Forex",
    "Future",
    "Stock",
    "StopOrder",
    "ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT",
    "ORDERS_FILE",
    "TRADES_FILE",
    "_GLOBAL_CANCEL_POLL_INTERVAL",
    "_GLOBAL_CANCEL_WAIT_SECS",
    "_MAX_OPTION_SELL_RETRIES",
    "_OPTION_SELL_COOLDOWN",
    "_cancel_ibkr_order_by_id",
    "_check_ibkr_open_order",
    "_flatten_in_progress",
    "_flatten_lock",
    "_get_alpaca_price",
    "_get_ibkr_bid_ask",
    "_get_ibkr_price",
    "_get_symbol_lock",
    "_ibkr_item_to_key",
    "_is_duplicate_check_enabled",
    "_is_option_contract",
    "_is_recently_closed",
    "_option_sell_attempts",
    "_pending_option_exits",
    "_safe_del_trade",
    "_safe_set_trade",
    "_safe_update_trade",
    "_symbol_locks",
    "_trades_lock",
    "_validate_position_price",
    "_wait_for_order_book_clear",
    "active_trades",
    "threading",
    "calculate_position_size",
    "calculate_stops",
    "cancel_order_by_id",
    "check_combined_exposure",
    "check_correlation",
    "check_sector_concentration",
    "close_position",
    "execute_buy",
    "execute_buy_option",
    "execute_sell",
    "execute_sell_option",
    "execute_short",
    "flatten_all",
    "flush_pending_option_exits",
    "get_contract",
    "get_open_positions",
    "has_open_order_for",
    "is_equities_extended_hours",
    "is_options_market_open",
    "log",
    "log_order",
    "open_orders",
    "open_trades",
    "recently_closed",
    "reconcile_with_ibkr",
    "record_loss",
    "record_win",
    "update_position_prices",
    "update_positions_from_ibkr",
    "update_tranche_status",
    "update_trailing_stops",
]
