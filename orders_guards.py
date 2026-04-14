# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders_guards.py                           ║
# ║   Duplicate order detection — reads state, never writes it   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Reads open_orders (from orders_state) and live IBKR order book to
detect duplicate orders before submission. No writes to shared state.
"""

from __future__ import annotations

from ib_async import IB

from config import CONFIG
from orders_state import ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT, log, open_orders


def _is_duplicate_check_enabled() -> bool:
    """
    Return True if the open-order duplicate guard is active.

    Reads ``ORDER_DUPLICATE_CHECK_ENABLED`` from CONFIG, defaulting to
    ``ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT`` (True).  Any truthy value
    enables the guard; any falsy value disables it.  Missing or unexpected
    argument types are handled gracefully — the guard stays ENABLED.
    """
    try:
        raw = CONFIG.get("ORDER_DUPLICATE_CHECK_ENABLED", ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT)
        return bool(raw)
    except Exception:
        # If CONFIG lookup itself raises for any reason, default to enabled.
        return True


def has_open_order_for(ib_or_symbol, symbol=None, side="BUY", option_key=None):
    """Return True if there is a live open order for the given symbol.

    Supports two calling conventions:

    1. ``has_open_order_for(symbol)`` — fast dict-only lookup via the
       in-memory ``open_orders`` dict (updated by execute_buy/execute_sell).

    2. ``has_open_order_for(ib, symbol, side="BUY", option_key=None)`` —
       IBKR-backed check: queries the broker directly for live open orders.
       Returns True (fail-closed) on any IBKR error.
    """
    if symbol is None:
        # Calling convention 1: has_open_order_for(symbol)
        return ib_or_symbol in open_orders
    else:
        # Calling convention 2: has_open_order_for(ib, symbol, side=..., option_key=...)
        return _check_ibkr_open_order(ib_or_symbol, symbol, side=side, option_key=option_key)


def _check_ibkr_open_order(
    ib: IB,
    symbol: str,
    side: str = "BUY",
    option_key: str | None = None,
) -> bool:
    """Query IBKR directly for a live open order or open position.

    For FX pairs, also checks ib.portfolio() for an existing position in the
    same direction — the entry SELL may have already filled (removing it from
    openTrades) while active_trades was cleared by the orphan cleaner.

    Returns True if a matching order/position exists, or True on any IBKR error
    (fail-closed).
    """
    try:
        open_trades_ibkr = ib.openTrades()
        for trade in open_trades_ibkr:
            contract = trade.contract
            order = trade.order
            action = getattr(order, "action", "").upper()
            if action != side.upper():
                continue
            trade_symbol = getattr(contract, "symbol", "")
            if option_key is not None:
                raw_exp = str(getattr(contract, "lastTradeDateOrContractMonth", ""))
                if len(raw_exp) == 8 and raw_exp.isdigit():
                    expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                else:
                    expiry_str = raw_exp
                right_raw = getattr(contract, "right", "C")
                right = "C" if right_raw in ("C", "CALL") else "P"
                strike = getattr(contract, "strike", 0)
                ibkr_key = f"{trade_symbol}_{right}_{strike}_{expiry_str}"
                if ibkr_key == option_key:
                    return True
            else:
                # For FX pairs (6-char like "EURUSD"), IBKR reports just the base
                # currency as trade_symbol (e.g. "EUR" or "USD"). Match either form.
                ibkr_base = symbol[:3] if (len(symbol) == 6 and symbol.isalpha()) else symbol
                if trade_symbol in (symbol, ibkr_base):
                    return True

        # ── Portfolio position check (belt-and-suspenders) ────────────────────
        # If active_trades lost track of a position (orphan cleaner purge, bot
        # restart before reconciliation), the system could re-enter. Guard against
        # re-entry by checking ib.portfolio() for ALL non-option instruments.
        if option_key is None:
            try:
                from config import CONFIG as _CFG

                portfolio_items = ib.portfolio(_CFG.get("active_account", ""))
                is_fx = len(symbol) == 6 and symbol.isalpha()
                ibkr_base = symbol[:3] if is_fx else symbol
                # SELL side → we already hold a SHORT (position < 0); block re-entry.
                # BUY  side → we already hold a LONG  (position > 0); block re-entry.
                position_sign = -1 if side.upper() == "SELL" else 1
                for item in portfolio_items:
                    c = item.contract
                    item_sym = getattr(c, "symbol", "")
                    item_sec = getattr(c, "secType", "")
                    if is_fx:
                        item_ccy = getattr(c, "currency", "")
                        item_pair = item_sym + item_ccy
                        match = (item_pair == symbol or item_sym == ibkr_base) and item_sec == "CASH"
                    else:
                        match = item_sym == symbol and item_sec == "STK"
                    if match and (item.position * position_sign) > 0:
                        log.warning(
                            f"_check_ibkr_open_order: {symbol} position already open "
                            f"(qty={item.position}) — blocking duplicate {side}"
                        )
                        return True
            except Exception as _pos_e:
                log.debug(f"_check_ibkr_open_order: portfolio check failed for {symbol}: {_pos_e}")

        return False
    except Exception as e:
        log.error(
            f"_check_ibkr_open_order: IBKR openTrades() failed for {symbol} — failing closed (skipping order): {e}"
        )
        return True
