# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders.py                                  ║
# ║   Order execution — limit orders, OCO brackets, exits        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import threading
from datetime import datetime, timezone, time as dtime
from typing import Optional, Tuple
import zoneinfo
from ib_async import IB, Stock, Forex, Option, Future
from ib_async import LimitOrder, StopOrder, MarketOrder
from config import CONFIG
from risk import (calculate_position_size, calculate_stops, check_correlation,
                  record_win, record_loss, check_combined_exposure,
                  check_sector_concentration)
from learning import log_order
from scanner import get_tv_signal_cache

# ── Shared state (all mutable state lives in orders_state) ───────────────────
from orders_state import (
    log,
    TRADES_FILE, ORDERS_FILE,
    active_trades, open_trades, open_orders, recently_closed,
    ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT,
    _trades_lock, _flatten_lock, _flatten_in_progress,
    _symbol_locks, _symbol_locks_mutex, _get_symbol_lock,
    _safe_set_trade, _safe_update_trade, _safe_del_trade,
    _is_recently_closed,
)

# ── Duplicate order guards (reads orders_state) ───────────────────────────────
from orders_guards import (
    _is_duplicate_check_enabled,
    has_open_order_for,
    _check_ibkr_open_order,
)

# ── Contract/price utilities (pure, no shared state) ─────────────────────────
from orders_contracts import (
    _ET,
    _emergency_ib, _emergency_lock, _get_emergency_ib,
    _cancel_ibkr_order_by_id,
    is_options_market_open,
    get_contract,
    _get_ibkr_price, _get_ibkr_bid_ask, _get_alpaca_price,
    _is_option_contract, _ibkr_item_to_key,
    _validate_position_price,
)

# ── Position tracking and reconciliation ─────────────────────────────────────
from orders_portfolio import (
    _GLOBAL_CANCEL_WAIT_SECS, _GLOBAL_CANCEL_POLL_INTERVAL,
    _flatten_in_progress,
    flatten_all, _wait_for_order_book_clear, _flatten_all_inner,
    close_position,
    reconcile_with_ibkr,
    update_positions_from_ibkr,
    update_position_prices,
    get_open_positions,
)

# ── Options execution ─────────────────────────────────────────────────────────
from orders_options import (
    _option_sell_attempts, _MAX_OPTION_SELL_RETRIES, _OPTION_SELL_COOLDOWN,
    _pending_option_exits,
    execute_buy_option, execute_sell_option, flush_pending_option_exits,
    update_tranche_status, update_trailing_stops,
    # Options attempt ledger (session-scoped DAY-order dedup)
    _OPTIONS_LEDGER_PATH, _load_options_ledger, _save_options_ledger,
    _options_attempted_today, _record_options_attempt, _options_ledger,
)


# ── Core order execution ──────────────────────────────────────────────────────
from orders_core import execute_buy, execute_short, execute_sell


# ── Order lifecycle utilities ─────────────────────────────────────────────────

def cancel_order_by_id(ib, order_id) -> bool:
    """
    Cancel an open IBKR order by orderId.
    Returns True if the order was found and cancellation was requested.
    Callers should call sync_orders_from_ibkr() and update open_trades
    after a successful cancel.
    """
    for t in ib.openTrades():
        if t.order.orderId == order_id:
            ib.cancelOrder(t.order)
            ib.sleep(1)
            return True
    return False
