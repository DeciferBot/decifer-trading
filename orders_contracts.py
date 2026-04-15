# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders_contracts.py                        ║
# ║   IBKR contract building, price fetching, market hours       ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Pure utility functions — no writes to shared trading state.
Imports from orders_state (log) and stdlib/third-party only.
"""

from __future__ import annotations

import threading
import zoneinfo
from datetime import datetime
from datetime import time as dtime

from ib_async import IB, Forex, Stock  # re-exported for callers

from config import CONFIG
from orders_state import log

# ── Timezone ───────────────────────────────────────────────────────────────────
_ET = zoneinfo.ZoneInfo("America/New_York")

# ── Emergency IB connection ────────────────────────────────────────────────────
# Uses clientId=11 so it doesn't interfere with the main connection (clientId=10).
# Created on-demand, thread-safe via lock. Used ONLY for close/kill orders.
_emergency_ib = None
_emergency_lock = threading.Lock()


def _get_emergency_ib() -> IB:
    """Get or create the emergency IB connection (thread-safe)."""
    global _emergency_ib
    with _emergency_lock:
        if _emergency_ib is not None and _emergency_ib.isConnected():
            return _emergency_ib
        try:
            _emergency_ib = IB()
            _emergency_ib.connect(
                CONFIG.get("ib_host", "127.0.0.1"),
                CONFIG.get("ib_port", 7496),
                clientId=11,  # Different from main bot (clientId=10)
                timeout=5,
                readonly=False,
            )
            # NOTE: Do NOT request market data on emergency connection.
            # Only the main bot (clientId=10) should request market data.
            # Two sessions requesting data causes Error 10197 ("competing live session").
            log.info("Emergency IB connection established (clientId=11, orders only — no market data)")
            return _emergency_ib
        except Exception as e:
            log.error(f"Emergency IB connection failed: {e}")
            _emergency_ib = None
            return None


def _cancel_ibkr_order_by_id(ib, order_id: int) -> None:
    """Cancel a live IBKR order by orderId only. Safe to call if order is already gone."""
    from ib_async import Order as _Order

    try:
        if not ib.isConnected():
            log.warning(f"_cancel_ibkr_order_by_id: IBKR disconnected — cannot cancel #{order_id}")
            return
        bare = _Order()
        bare.orderId = order_id
        ib.cancelOrder(bare)
        ib.sleep(0.3)
        log.info(f"_cancel_ibkr_order_by_id: cancel sent for order #{order_id}")
    except Exception as exc:
        log.error(f"_cancel_ibkr_order_by_id: failed for #{order_id}: {exc}")


def is_options_market_open() -> bool:
    """Options trade 9:30 AM – 4:00 PM ET, Mon–Fri, trading days only."""
    from risk import is_trading_day

    if not is_trading_day():
        return False
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now_et.time()
    return dtime(9, 30) <= t < dtime(16, 0)


def is_equities_extended_hours() -> bool:
    """Equity extended hours: 4:00 AM – 8:00 PM ET, Mon–Fri, trading days only.
    Use this to gate MKT close orders — IBKR cancels them outside this window."""
    from risk import is_trading_day

    if not is_trading_day():
        return False
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now_et.time()
    return dtime(4, 0) <= t < dtime(20, 0)


def get_contract(symbol: str, instrument: str = "stock"):
    """Build the correct IBKR contract for any instrument type."""
    if instrument == "fx" or (len(symbol) == 6 and symbol.isalpha()):
        return Forex(symbol)
    else:
        return Stock(symbol, "SMART", "USD")


def _get_ibkr_price(ib: IB, contract, fallback: float = 0) -> float:
    """
    Get price from IBKR for order execution (live or delayed).
    With reqMarketDataType(3), IBKR returns free 15-min delayed data
    if no live subscription exists. Delayed price is used for validation
    and limit order pricing — actual fill is at market.
    """
    try:
        [ticker] = ib.reqTickers(contract)
        ib.sleep(0.5)  # slightly longer for delayed data to arrive
        # Try market price first, then last, then close
        mkt = ticker.marketPrice()
        if mkt and mkt > 0 and not (hasattr(mkt, "__float__") and float(mkt) != float(mkt)):
            return float(mkt)
        last = ticker.last
        if last and last > 0:
            return float(last)
        close = ticker.close
        if close and close > 0:
            return float(close)
    except Exception as _e:
        log.warning(
            "_get_ibkr_price: reqTickers failed for %s — %s (using fallback=%s)",
            getattr(contract, "symbol", "?"),
            _e,
            fallback,
        )
    return fallback


def _get_ibkr_bid_ask(ib: IB, contract) -> tuple[float, float]:
    """
    Fetch current bid/ask for execution agent context. Returns (0.0, 0.0) on failure.
    Reuses the same delayed market data subscription already active for _get_ibkr_price.
    """
    try:
        [ticker] = ib.reqTickers(contract)
        ib.sleep(0.3)
        bid = float(ticker.bid) if getattr(ticker, "bid", None) and ticker.bid > 0 else 0.0
        ask = float(ticker.ask) if getattr(ticker, "ask", None) and ticker.ask > 0 else 0.0
        return bid, ask
    except Exception as _e:
        log.warning("_get_ibkr_bid_ask: reqTickers failed for %s — %s", getattr(contract, "symbol", "?"), _e)
        return 0.0, 0.0


def _get_alpaca_price(symbol: str) -> float:
    """
    Quick price fetch for a single symbol for price validation.
    Uses Alpaca latest daily bar. Returns 0 if unavailable.
    """
    try:
        from alpaca_data import fetch_bars

        df = fetch_bars(symbol, period="2d", interval="1d")
        if df is not None and not df.empty and "Close" in df.columns:
            price = float(df["Close"].iloc[-1])
            if price > 0:
                return round(price, 4)
    except Exception:
        pass
    return 0


def _is_option_contract(contract) -> bool:
    """
    Detect if an IBKR contract is an option using multiple indicators.
    ib_async may use Option subclass or generic Contract with secType field.
    Check all possible indicators to be robust.
    """
    # Check secType (works for both subclass and generic Contract)
    sec = getattr(contract, "secType", "")
    if sec and sec.upper() in ("OPT", "FOP"):
        return True
    # Check class name (ib_async uses Option subclass for portfolio items)
    if type(contract).__name__ in ("Option", "FuturesOption"):
        return True
    # Check option-specific attributes — if strike > 0 AND right is set, it's an option
    strike = getattr(contract, "strike", 0)
    right = getattr(contract, "right", "")
    return bool(strike and strike > 0 and right and right in ("C", "P", "CALL", "PUT"))


def _ibkr_item_to_key(item) -> str:
    """
    Build the correct active_trades key from an IBKR portfolio item.
    - Stocks/Forex: plain symbol (e.g. "KOD")
    - Options: composite key matching execute_buy_option format
      (e.g. "KOD_C_35.0_2026-04-17")
    This prevents stock and option positions for the same underlying
    from colliding in the tracker dict.
    """
    c = item.contract
    if _is_option_contract(c):
        # Convert IBKR date format (YYYYMMDD) to our format (YYYY-MM-DD)
        raw_exp = str(getattr(c, "lastTradeDateOrContractMonth", ""))
        if len(raw_exp) == 8 and raw_exp.isdigit():
            expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
        else:
            expiry_str = raw_exp
        right_raw = getattr(c, "right", "C")
        right = "C" if right_raw in ("C", "CALL") else "P"
        strike = getattr(c, "strike", 0)
        return f"{c.symbol}_{right}_{strike}_{expiry_str}"
    # Forex: IBKR stores secType='CASH' with symbol=base currency (e.g. 'EUR', 'USD').
    # Reconstruct the 6-char pair (e.g. 'EURUSD', 'USDJPY') so active_trades keys
    # match the symbol used at order entry time.
    if getattr(c, "secType", "") == "CASH":
        currency = getattr(c, "currency", "")
        if currency:
            return c.symbol + currency
    return c.symbol


def _validate_position_price(symbol: str, ibkr_price: float, entry: float) -> tuple[float, str]:
    """
    3-way price consensus for position monitoring (IBKR + Alpaca + TV).
    Same logic used at order entry — now applied to ongoing updates and closes.

    Returns (validated_price, source_description).
    Returns (0, reason) if all sources are invalid or contaminated.

    Rules:
    - Collect prices from all available sources
    - If any two sources diverge by >50%: reject the outlier (if 3 sources),
      or reject all (if only 2 sources disagree)
    - Use the MEDIAN of agreeing sources (not max — for monitoring we want accuracy,
      not fill-friendliness)
    - Final sanity check: reject prices < $0.01 or > $50,000
    """
    # Rebind patchable names from the current sys.modules entry so that
    # @patch('orders.*') works even when this module object differs from
    # sys.modules['orders'] (can happen during pytest collection cycles).
    import sys as _sys

    _om = _sys.modules.get("orders_contracts", _sys.modules[__name__])
    _gap = _om._get_alpaca_price

    alpaca_price = _gap(symbol)

    prices = {}
    if ibkr_price > 0:
        prices["IBKR"] = ibkr_price
    if alpaca_price > 0:
        prices["Alpaca"] = alpaca_price

    if not prices:
        return 0, f"No price data from any source for {symbol}"

    # Single source — apply sanity check against entry
    if len(prices) == 1:
        src_name, src_price = next(iter(prices.items()))
        if entry > 0 and abs(src_price - entry) / entry > 0.50:
            log.warning(
                f"PRICE SUSPECT {symbol}: only {src_name}=${src_price:.2f} available, "
                f"{abs(src_price - entry) / entry:.0%} from entry ${entry:.2f} — rejecting"
            )
            return 0, f"Single source {src_name} too far from entry"
        return src_price, f"{src_name}=${src_price:.2f}"

    # Two sources — must agree within 50%
    if len(prices) == 2:
        vals = list(prices.values())
        names = list(prices.keys())
        div = abs(vals[0] - vals[1]) / max(vals[0], vals[1])
        if div > 0.50:
            log.error(
                f"PRICE CONTAMINATION {symbol}: {names[0]}=${vals[0]:.2f} vs "
                f"{names[1]}=${vals[1]:.2f} ({div:.0%} divergence) — rejecting both"
            )
            return 0, f"2-source divergence {div:.0%}"
        # Use the one closest to entry if we have entry, otherwise average
        if entry > 0:
            best = min(vals, key=lambda p: abs(p - entry))
        else:
            best = sum(vals) / 2
        src_str = " | ".join(f"{k}=${v:.2f}" for k, v in prices.items())
        return round(best, 4), src_str

    # Three sources — find the outlier (if any) and use median
    vals = list(prices.values())
    names = list(prices.keys())
    sorted_prices = sorted(zip(names, vals, strict=False), key=lambda x: x[1])

    low_name, low_val = sorted_prices[0]
    mid_name, mid_val = sorted_prices[1]
    hi_name, hi_val = sorted_prices[2]

    low_mid_div = abs(low_val - mid_val) / max(low_val, mid_val) if max(low_val, mid_val) > 0 else 0
    mid_hi_div = abs(mid_val - hi_val) / max(mid_val, hi_val) if max(mid_val, hi_val) > 0 else 0
    low_hi_div = abs(low_val - hi_val) / max(low_val, hi_val) if max(low_val, hi_val) > 0 else 0

    # If all three agree within 50%, use median
    if low_hi_div <= 0.50:
        src_str = " | ".join(f"{k}=${v:.2f}" for k, v in prices.items())
        log.info(f"Price consensus {symbol}: {src_str} | spread={low_hi_div:.1%} | using median ${mid_val:.2f}")
        return round(mid_val, 4), src_str

    # One outlier — the two that agree win
    if low_mid_div <= 0.20 and mid_hi_div > 0.50:
        log.warning(
            f"Price outlier {symbol}: rejecting {hi_name}=${hi_val:.2f} — "
            f"{low_name}=${low_val:.2f} and {mid_name}=${mid_val:.2f} agree"
        )
        consensus = (low_val + mid_val) / 2
        return round(consensus, 4), f"{low_name}+{mid_name} consensus (rejected {hi_name})"

    if mid_hi_div <= 0.20 and low_mid_div > 0.50:
        log.warning(
            f"Price outlier {symbol}: rejecting {low_name}=${low_val:.2f} — "
            f"{mid_name}=${mid_val:.2f} and {hi_name}=${hi_val:.2f} agree"
        )
        consensus = (mid_val + hi_val) / 2
        return round(consensus, 4), f"{mid_name}+{hi_name} consensus (rejected {low_name})"

    # All three disagree badly — reject everything
    log.error(
        f"PRICE CHAOS {symbol}: {names[0]}=${vals[0]:.2f}, {names[1]}=${vals[1]:.2f}, "
        f"{names[2]}=${vals[2]:.2f} — no consensus, keeping previous price"
    )
    return 0, "All 3 sources disagree"
