# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders.py                                  ║
# ║   Order execution — limit orders, OCO brackets, exits        ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import threading
from datetime import datetime, timezone, time as dtime
import zoneinfo
from ib_async import IB, Stock, Forex, Option, Future
from ib_async import LimitOrder, StopOrder, MarketOrder
from config import CONFIG
from risk import (calculate_position_size, calculate_stops, check_correlation,
                  record_win, record_loss, check_combined_exposure,
                  check_sector_concentration)
from learning import log_order
from scanner import get_tv_signal_cache

# Per-symbol lock registry to close TOCTOU gap between openOrders check and submission
_symbol_locks: dict = {}
_symbol_locks_mutex = threading.Lock()


def _get_symbol_lock(symbol: str) -> threading.Lock:
    """Return a per-symbol lock, creating it if necessary."""
    with _symbol_locks_mutex:
        if symbol not in _symbol_locks:
            _symbol_locks[symbol] = threading.Lock()
        return _symbol_locks[symbol]

log = logging.getLogger("decifer.orders")

# File paths derived from config (patchable in tests)
TRADES_FILE: str = CONFIG.get("trade_log", "/tmp/trades.json")
ORDERS_FILE: str = CONFIG.get("order_log", "/tmp/orders.json")

# In-memory position tracker (source of truth = IBKR, this is a cache)
active_trades: dict = {}
open_trades = active_trades  # backward-compat alias (both names point to same dict)

# In-memory open-order tracker keyed by symbol (updated by execute_buy/sell)
open_orders: dict = {}

# Default: duplicate check is enabled. Set ORDER_DUPLICATE_CHECK_ENABLED=False
# in config to disable (e.g. for paper trading environments or unit tests).
# Fail-safe orientation: when in doubt, the check is ON.
ORDER_DUPLICATE_CHECK_ENABLED_DEFAULT = True

# ── Thread-safe guard for active_trades dictionary ─────────────────────────
# Audit (prop-014): execute_buy/execute_sell run on the main async event loop,
# reconcile_with_ibkr and update_positions_from_ibkr run from background threads.
# Both contexts read+write active_trades. An RLock is safe from both threaded and
# async-sync contexts; the lock scope is narrowed to ONLY the dictionary
# read/modify/write lines — never around broker network calls — so a slow
# reconcile never blocks a live order submission.
_trades_lock = threading.RLock()

# ── Re-entrancy guard for flatten_all ────────────────────────────────────────
# flatten_all is called from the kill-switch and drawdown threads. If two callers
# race (e.g. drawdown fires while kill-switch is already running), only the first
# should proceed — the second must bail immediately to avoid double-closing and
# conflicting cancellation orders.
_flatten_lock = threading.Lock()
_flatten_in_progress: bool = False

# ── flatten_all order-book wait constants ─────────────────────────────────────
# After reqGlobalCancel, IBKR processes cancellations asynchronously.  We poll
# until the book is clear (or we time out) before placing closing orders.
_GLOBAL_CANCEL_WAIT_SECS: float = 5.0   # maximum wait before we proceed anyway
_GLOBAL_CANCEL_POLL_INTERVAL: float = 0.5  # seconds between each openOrders poll


def _safe_set_trade(key: str, value: dict):
    """Thread-safe write to active_trades dict."""
    with _trades_lock:
        active_trades[key] = value


def _safe_update_trade(key: str, updates: dict):
    """Thread-safe partial update of an active_trades entry."""
    with _trades_lock:
        if key in active_trades:
            active_trades[key].update(updates)


def _safe_del_trade(key: str):
    """Thread-safe delete from active_trades dict."""
    with _trades_lock:
        active_trades.pop(key, None)


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


# ── Market hours guard ─────────────────────────────────────────────────────
_ET = zoneinfo.ZoneInfo("America/New_York")

def is_options_market_open() -> bool:
    """Options trade 9:30 AM – 4:00 PM ET, Mon–Fri only."""
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    t = now_et.time()
    return dtime(9, 30) <= t < dtime(16, 0)

# ── Emergency IB connection (separate from main scanner) ──────
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
    """Query IBKR directly for a live open order (used as belt-and-suspenders check).

    Returns True if a matching order exists, or True on any IBKR error (fail-closed).
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
                if trade_symbol == symbol:
                    return True
        return False
    except Exception as e:
        log.error(
            f"_check_ibkr_open_order: IBKR openTrades() failed for {symbol} — "
            f"failing closed (skipping order): {e}"
        )
        return True


def get_contract(symbol: str, instrument: str = "stock"):
    """Build the correct IBKR contract for any instrument type."""
    if instrument == "fx" or len(symbol) == 6 and symbol.isalpha():
        base = symbol[:3]
        quote = symbol[3:]
        return Forex(symbol, currency=quote, localSymbol=f"{base}.{quote}")
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
        if mkt and mkt > 0 and not (hasattr(mkt, '__float__') and float(mkt) != float(mkt)):
            return float(mkt)
        last = ticker.last
        if last and last > 0:
            return float(last)
        close = ticker.close
        if close and close > 0:
            return float(close)
    except Exception:
        pass
    return fallback


def _get_yf_price(symbol: str) -> float:
    """
    Quick yfinance price fetch for a single symbol.
    Used by 3-way validation — NOT for scanning/scoring.
    Returns 0 if unavailable.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
        if price and price > 0:
            return round(float(price), 4)
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
    sec = getattr(contract, 'secType', '')
    if sec and sec.upper() in ('OPT', 'FOP'):
        return True
    # Check class name (ib_async uses Option subclass for portfolio items)
    if type(contract).__name__ in ('Option', 'FuturesOption'):
        return True
    # Check option-specific attributes — if strike > 0 AND right is set, it's an option
    strike = getattr(contract, 'strike', 0)
    right = getattr(contract, 'right', '')
    if strike and strike > 0 and right and right in ('C', 'P', 'CALL', 'PUT'):
        return True
    return False


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
        raw_exp = str(getattr(c, 'lastTradeDateOrContractMonth', ''))
        if len(raw_exp) == 8 and raw_exp.isdigit():
            expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
        else:
            expiry_str = raw_exp
        right_raw = getattr(c, 'right', 'C')
        right = "C" if right_raw in ("C", "CALL") else "P"
        strike = getattr(c, 'strike', 0)
        return f"{c.symbol}_{right}_{strike}_{expiry_str}"
    return c.symbol


def _validate_position_price(symbol: str, ibkr_price: float, entry: float) -> tuple[float, str]:
    """
    3-way price consensus for position monitoring (IBKR + yfinance + TV).
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
    tv_cache = get_tv_signal_cache()
    tv_data = tv_cache.get(symbol) if tv_cache else None
    tv_close = float(tv_data.get("tv_close")) if tv_data and tv_data.get("tv_close") else 0

    yf_price = _get_yf_price(symbol)

    prices = {}
    if ibkr_price > 0:
        prices["IBKR"] = ibkr_price
    if yf_price > 0:
        prices["yfinance"] = yf_price
    if tv_close > 0:
        prices["TV"] = tv_close

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
    sorted_prices = sorted(zip(names, vals), key=lambda x: x[1])

    # Check all pairwise divergences
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
        # High value is the outlier
        log.warning(f"Price outlier {symbol}: rejecting {hi_name}=${hi_val:.2f} — "
                     f"{low_name}=${low_val:.2f} and {mid_name}=${mid_val:.2f} agree")
        consensus = (low_val + mid_val) / 2
        return round(consensus, 4), f"{low_name}+{mid_name} consensus (rejected {hi_name})"

    if mid_hi_div <= 0.20 and low_mid_div > 0.50:
        # Low value is the outlier
        log.warning(f"Price outlier {symbol}: rejecting {low_name}=${low_val:.2f} — "
                     f"{mid_name}=${mid_val:.2f} and {hi_name}=${hi_val:.2f} agree")
        consensus = (mid_val + hi_val) / 2
        return round(consensus, 4), f"{mid_name}+{hi_name} consensus (rejected {low_name})"

    # All three disagree badly — reject everything
    log.error(f"PRICE CHAOS {symbol}: {names[0]}=${vals[0]:.2f}, {names[1]}=${vals[1]:.2f}, "
              f"{names[2]}=${vals[2]:.2f} — no consensus, keeping previous price")
    return 0, "All 3 sources disagree"


def execute_buy(ib: IB, symbol: str, price: float, atr: float,
                score: int, portfolio_value: float, regime: dict,
                reasoning: str = "",
                signal_scores: dict = None,
                agent_outputs: dict = None,
                open_time: str = None,
                tranche_mode: bool = True) -> bool:
    """
    Place a buy order with full OCO bracket.
    Entry: Limit order at IBKR real-time price (yfinance price is only a fallback)
    Stop loss: Stop order (placed immediately)
    Take profit: Limit order (placed immediately)
    Returns True if order placed successfully.
    """
    # ── Guard: per-symbol lock closes TOCTOU gap between check and submission ──
    sym_lock = _get_symbol_lock(symbol)
    with sym_lock:
        # ── Guard: check active_trades under lock (prop-003/014) ──────────
        with _trades_lock:
            if symbol in active_trades:
                log.warning(f"Already in {symbol} — skipping buy")
                return False
            if len(active_trades) >= CONFIG["max_positions"]:
                log.warning(f"Max positions ({CONFIG['max_positions']}) reached — skipping {symbol}")
                return False
            # Correlation check
            ok, reason = check_correlation(symbol, list(active_trades.values()))
            if not ok:
                log.warning(f"Correlation block for {symbol}: {reason}")
                return False

            # ── FIX #1+3: Cross-instrument + combined exposure check ──
            # Estimate new position value for the exposure check
            est_value = portfolio_value * CONFIG.get("risk_pct_per_trade", 0.03) * 50  # rough max
            exp_ok, exp_reason = check_combined_exposure(
                symbol, est_value, list(active_trades.values()),
                portfolio_value, instrument="stock"
            )
            if not exp_ok:
                log.warning(f"Combined exposure block for {symbol}: {exp_reason}")
                return False

            # ── FIX #2: Sector concentration check ────────────────────
            sec_ok, sec_reason = check_sector_concentration(
                symbol, list(active_trades.values()),
                portfolio_value, regime.get("regime", "NORMAL")
            )
            if not sec_ok:
                log.warning(f"Sector block for {symbol}: {sec_reason}")
                return False

            # ── Reserve slot — closes TOCTOU gap between check and submission ──
            # A second execute_buy thread for the same symbol will now see this entry
            # and exit early. Replaced with the full entry after order placement.
            active_trades[symbol] = {"status": "RESERVED", "symbol": symbol}

        # ── Duplicate open-order guard (prop-duplicate) ────────────────
        # Ask IBKR directly whether a BUY order for this symbol is already live.
        # This catches restarts mid-session or rapid double-scan firings.
        if _is_duplicate_check_enabled():
            if has_open_order_for(symbol) or _check_ibkr_open_order(ib, symbol, side="BUY"):
                log.warning(
                    f"Skipping duplicate order for {symbol} — open order already exists"
                )
                _safe_del_trade(symbol)  # release reservation
                return False

    try:
        contract = get_contract(symbol)
        ib.qualifyContracts(contract)

        # ── GET REAL-TIME IBKR PRICE — this is the execution price ──
        # yfinance is for scanning/scoring only; IBKR is source of truth for orders
        yf_price = price  # save original for logging
        ibkr_price = _get_ibkr_price(ib, contract, fallback=0)

        # ── MULTI-SOURCE PRICE VALIDATION ──
        # Collect prices from all available sources (IBKR may be 15-min delayed).
        # Use the freshest/highest for the limit order so it actually fills.
        tv_cache = get_tv_signal_cache()
        tv_data = tv_cache.get(symbol) if tv_cache else None
        tv_close = float(tv_data.get("tv_close")) if tv_data and tv_data.get("tv_close") else 0

        prices = {}
        if ibkr_price > 0:
            prices["IBKR"] = ibkr_price
        if yf_price > 0:
            prices["yfinance"] = yf_price
        if tv_close > 0:
            prices["TV"] = tv_close

        if not prices:
            log.error(f"No price data available for {symbol} from any source — aborting")
            return False

        # CONTAMINATION CHECK: if any two sources diverge by >50%, abort
        price_vals = list(prices.values())
        for i in range(len(price_vals)):
            for j in range(i + 1, len(price_vals)):
                div = abs(price_vals[i] - price_vals[j]) / max(price_vals[i], price_vals[j])
                if div > 0.50:
                    log.error(
                        f"PRICE CONTAMINATION {symbol}: sources={prices} "
                        f"({div:.0%} max divergence) — aborting trade to protect capital"
                    )
                    return False

        # Use the HIGHEST price from sources that agree within 10%.
        # IBKR delayed data can be 15 min stale — yfinance/TV are more current.
        # Bidding at the highest confirmed price ensures the limit order can fill.
        best_price = max(price_vals)
        price = best_price

        # Log which sources contributed
        src_str = " | ".join(f"{k}=${v:.2f}" for k, v in prices.items())
        if len(prices) > 1:
            spread = (max(price_vals) - min(price_vals)) / max(price_vals)
            log.info(f"Price consensus {symbol}: {src_str} | spread={spread:.1%} | using ${price:.2f}")
        else:
            src_name = list(prices.keys())[0]
            log.warning(f"Single price source for {symbol}: {src_name}=${price:.2f}")

        # ── PRICE SANITY CHECK — catch data pipeline contamination ──
        # Reject obviously broken prices that would produce absurd position sizes.
        # Stocks under $1 are penny stocks; stocks over $10,000 are likely errors.
        if price < 1.0:
            log.error(f"Price too low for {symbol}: ${price:.2f} — likely data contamination, aborting")
            return False
        if price > 10000:
            log.error(f"Price too high for {symbol}: ${price:.2f} — likely data contamination, aborting")
            return False

        # Now calculate sizing and stops with the IBKR-sourced price
        qty = calculate_position_size(portfolio_value, price, score, regime)

        # ── HARD CAPS — last line of defense against contaminated data ──
        # Max 5,000 shares per order (prevents 10,000+ share orders from bad prices)
        MAX_SHARES = 5000
        if qty > MAX_SHARES:
            log.warning(f"Qty {qty} exceeds hard cap {MAX_SHARES} for {symbol} @ ${price:.2f} — capping")
            qty = MAX_SHARES
        # Max order value = 20% of portfolio (stricter than max_single_position for safety)
        max_order_value = portfolio_value * 0.20
        if qty * price > max_order_value:
            old_qty = qty
            qty = max(1, int(max_order_value / price))
            log.warning(f"Order value ${old_qty * price:,.0f} exceeds 20% cap ${max_order_value:,.0f} for {symbol} — reduced qty {old_qty}→{qty}")

        sl, tp = calculate_stops(price, atr, "LONG")

        # Validate R:R — skip in tranche mode (T2 open-ended upside lifts combined R:R above threshold)
        reward = tp - price
        risk   = price - sl
        if not tranche_mode:
            if risk <= 0 or (reward / risk) < CONFIG["min_reward_risk_ratio"]:
                log.warning(f"Poor R:R on {symbol}: reward={reward:.2f} risk={risk:.2f} — skipping")
                return False

        account = CONFIG["active_account"]

        # ── Tranche sizing ────────────────────────────────────────
        # Guard: need at least 2 shares to split into two tranches
        if tranche_mode and qty < 2:
            log.warning(f"[TRANCHE] qty={qty} too small for dual-tranche — falling back to legacy for {symbol}")
            tranche_mode = False

        if tranche_mode:
            t1_qty = qty // 2
            t2_qty = qty - t1_qty          # handles odd qty — T2 gets the extra share
            tp     = round(price + atr * CONFIG["atr_stop_multiplier"], 2)  # T1 target: +1.5×ATR
            tp_qty = t1_qty
        else:
            tp_qty = qty if qty < 3 else max(1, qty // 3)
            t1_qty = tp_qty
            t2_qty = qty - tp_qty

        # ── ATOMIC BRACKET ORDER ──────────────────────────────────
        # All 3 legs (entry + SL + TP) are submitted as one atomic bracket.
        # Parent transmit=False prevents it from filling before children are attached.
        # The final child has transmit=True which transmits the entire group together.
        # This prevents the "parent already filled" rejection that kills child orders.
        limit_price = round(price * 1.002, 2)

        # Leg 1: Entry (parent) — DO NOT transmit yet
        entry_order = LimitOrder("BUY", qty, limit_price,
                                 account=account, tif="DAY", outsideRth=True)
        entry_order.transmit = False
        trade = ib.placeOrder(contract, entry_order)
        ib.sleep(0.2)  # brief pause for IBKR to assign orderId

        parent_id = trade.order.orderId

        # Leg 2: Stop loss — attached to parent, DO NOT transmit yet
        sl_order = StopOrder("SELL", qty, sl, account=account, tif="GTC", outsideRth=True)
        sl_order.parentId = parent_id
        sl_order.transmit = False
        sl_trade = ib.placeOrder(contract, sl_order)
        ib.sleep(0.1)
        _sl_order_id = sl_trade.order.orderId  # captured for trailing stop modifications

        # Leg 3: Take profit — attached to parent, transmit=True sends ALL 3 legs together
        tp_order = LimitOrder("SELL", tp_qty, tp, account=account, tif="GTC", outsideRth=True)
        tp_order.parentId = parent_id
        tp_order.transmit = True
        tp_trade = ib.placeOrder(contract, tp_order)

        # Wait for IBKR to process the full bracket
        ib.sleep(1.5)

        # Log all 3 orders
        log_order({
            "order_id":   parent_id,
            "symbol":     symbol,
            "side":       "BUY",
            "order_type": "LMT",
            "qty":        qty,
            "price":      limit_price,
            "status":     "SUBMITTED",
            "instrument": "stock",
            "direction":  "LONG",
            "sl":         sl,
            "tp":         tp,
            "score":      score,
            "reasoning":  reasoning,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })
        log_order({
            "order_id":   sl_trade.order.orderId,
            "parent_id":  parent_id,
            "symbol":     symbol,
            "side":       "SELL",
            "order_type": "STP",
            "qty":        qty,
            "price":      sl,
            "status":     "SUBMITTED",
            "instrument": "stock",
            "role":       "stop_loss",
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })
        log_order({
            "order_id":   tp_trade.order.orderId,
            "parent_id":  parent_id,
            "symbol":     symbol,
            "side":       "SELL",
            "order_type": "LMT",
            "qty":        tp_qty,
            "price":      tp,
            "status":     "SUBMITTED",
            "instrument": "stock",
            "role":       "take_profit",
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        # ── VERIFY BRACKET — fallback if children got rejected ────
        # (duplicate-check guard runs before this block, so we are inside sym_lock)
        # (duplicate-check guard runs before this block, so we are inside sym_lock)
        # Even with atomic submission, edge cases (connectivity blips, race conditions)
        # can cause child orders to go Inactive. If that happens, cancel the broken
        # children and place standalone SL/TP orders (no parentId).
        order_status = trade.orderStatus.status
        if order_status in ('Cancelled', 'Inactive', 'ApiCancelled', 'ValidationError'):
            log.error(f"Entry order immediately rejected by IBKR for {symbol}: {order_status} — not tracking")
            return False

        sl_status = sl_trade.orderStatus.status
        tp_status = tp_trade.orderStatus.status

        if sl_status in ('Inactive', 'Cancelled', 'ApiCancelled') or tp_status in ('Inactive', 'Cancelled', 'ApiCancelled'):
            log.warning(
                f"Bracket child rejected for {symbol} (SL={sl_status}, TP={tp_status}) "
                f"— placing standalone SL/TP orders as fallback"
            )
            # Cancel ALL bracket children before placing OCA replacement.
            # Always cancel the original SL even if it wasn't rejected — placing a new
            # standalone OCA SL while the original is still live would create two active
            # stop losses on the same position.
            try:
                ib.cancelOrder(sl_trade.order)
                if tp_status in ('Inactive', 'Cancelled', 'ApiCancelled'):
                    ib.cancelOrder(tp_trade.order)
                ib.sleep(0.5)
            except Exception:
                pass

            # Place standalone SL + TP as OCA group (one-cancels-all)
            # so if TP fills, SL is auto-cancelled and vice versa
            oca_group = f"decifer_{symbol}_{parent_id}"

            try:
                standalone_sl = StopOrder("SELL", qty, sl, account=account, tif="GTC", outsideRth=True)
                standalone_sl.ocaGroup = oca_group
                standalone_sl.ocaType = 1  # Cancel remaining on fill
                standalone_sl.transmit = True
                sl_trade2 = ib.placeOrder(contract, standalone_sl)
                ib.sleep(0.3)
                _sl_order_id = sl_trade2.order.orderId  # update to standalone order
                log.info(f"Standalone SL placed for {symbol} @ ${sl:.2f} OCA={oca_group} (orderId={_sl_order_id})")
            except Exception as e:
                log.error(f"CRITICAL: Failed to place standalone SL for {symbol}: {e}")

            try:
                standalone_tp = LimitOrder("SELL", tp_qty, tp, account=account, tif="GTC", outsideRth=True)
                standalone_tp.ocaGroup = oca_group
                standalone_tp.ocaType = 1  # Cancel remaining on fill
                standalone_tp.transmit = True
                tp_trade2 = ib.placeOrder(contract, standalone_tp)
                ib.sleep(0.3)
                log.info(f"Standalone TP placed for {symbol} @ ${tp:.2f} OCA={oca_group} (orderId={tp_trade2.order.orderId})")
                # Update t1_order_id to the standalone TP so update_tranche_status tracks it
                if tranche_mode:
                    with _trades_lock:
                        if symbol in active_trades:
                            active_trades[symbol]["t1_order_id"] = tp_trade2.order.orderId
            except Exception as e:
                log.error(f"CRITICAL: Failed to place standalone TP for {symbol}: {e}")

        # ── Record position under lock (prop-003/014) ────────────────
        # Ghost position fix (prop-010): wrap in try/finally so that if any
        # error occurs between order submission and trade logging, we always
        # record the trade as FAILED rather than silently losing track of it.
        try:
            _open_time = open_time or datetime.now(timezone.utc).isoformat()
            with _trades_lock:
                active_trades[symbol] = {
                    "symbol":           symbol,
                    "instrument":       "stock",
                    "entry":            price,
                    "current":          price,
                    "qty":              qty,
                    "sl":               sl,
                    "tp":               tp,
                    "score":            score,
                    "reasoning":        reasoning,
                    "direction":        "LONG",
                    "pnl":              0.0,
                    "status":           "PENDING",   # Submitted to IBKR but not yet filled
                    "order_id":         parent_id,
                    "open_time":        _open_time,
                    "signal_scores":    signal_scores or {},
                    "agent_outputs":    agent_outputs or {},
                    "atr":              atr,
                    "sl_order_id":      _sl_order_id,
                    "high_water_mark":  price,
                    # ── Tranche tracking ──────────────────────────────────
                    "tranche_mode":     tranche_mode,
                    "t1_qty":           t1_qty,
                    "t2_qty":           t2_qty,
                    "t1_status":        "OPEN" if tranche_mode else "N/A",
                    "t1_order_id":      tp_trade.order.orderId if tranche_mode else None,
                    "t2_sl_order_id":   None,  # set by update_tranche_status after T1 fills
                }
            # Log OPEN record to trades.json for feedback loop
            from learning import log_trade
            if tranche_mode:
                log_trade(
                    trade={**active_trades[symbol], "qty": t1_qty,
                           "tranche_id": 1, "parent_trade_id": parent_id},
                    agent_outputs=agent_outputs or {},
                    regime=regime,
                    action="OPEN",
                )
                log_trade(
                    trade={**active_trades[symbol], "qty": t2_qty,
                           "tranche_id": 2, "parent_trade_id": parent_id},
                    agent_outputs=agent_outputs or {},
                    regime=regime,
                    action="OPEN",
                )
            else:
                log_trade(
                    trade=active_trades[symbol],
                    agent_outputs=agent_outputs or {},
                    regime=regime,
                    action="OPEN",
                )
        except Exception as record_err:
            # Ghost position safety: order was submitted but we failed to record it
            log.error(f"GHOST POSITION RISK {symbol}: order submitted (id={parent_id}) but "
                       f"failed to record in tracker: {record_err}")
            raise

        _rr = (tp - price) / (price - sl) if (price - sl) > 0 else 0
        _tranche_tag = f" [T1={t1_qty}/T2={t2_qty}]" if tranche_mode else ""
        log.info(f"✅ BUY {symbol} qty={qty}{_tranche_tag} @ ${price:.2f} | SL=${sl:.2f} TP=${tp:.2f} | R:R={_rr:.1f}")

        # ── Start fill watcher for this order ────────────────────────────────
        if CONFIG.get("fill_watcher", {}).get("enabled", True):
            from fill_watcher import FillWatcher, _active_watchers, _watchers_lock
            if symbol not in _active_watchers:   # guard: should never be True, but free to check
                watcher = FillWatcher(
                    ib=ib,
                    symbol=symbol,
                    order_id=parent_id,
                    entry_trade=trade,
                    original_limit=limit_price,
                    contract=contract,
                    qty=qty,
                )
                with _watchers_lock:
                    _active_watchers[symbol] = watcher
                t = threading.Thread(target=watcher.run,
                                     name=f"fill_watcher_{symbol}", daemon=True)
                t.start()

        return True

    except Exception as e:
        _safe_del_trade(symbol)  # clean up any reservation or partial entry if order failed
        log.error(f"Buy failed {symbol}: {e}")
        return False


def execute_sell(ib: IB, symbol: str, reason: str = "Agent signal") -> bool:
    """
    Close an existing position at market.
    Returns True if order placed.
    """
    with _trades_lock:
        if symbol not in active_trades:
            log.warning(f"No open position in {symbol} — skipping sell")
            return False
        info = active_trades[symbol]

    # Stop any active fill watcher so it doesn't race the sell
    from fill_watcher import stop_watcher as _stop_watcher
    _stop_watcher(symbol)

    try:
        contract = get_contract(symbol)
        ib.qualifyContracts(contract)

        # 3-way price validation for accurate exit P&L logging
        ibkr_price = _get_ibkr_price(ib, contract, fallback=0)
        entry = info.get("entry", 0)
        validated_price, src_desc = _validate_position_price(symbol, ibkr_price, entry)
        if validated_price > 0:
            info["current"] = validated_price
            log.info(f"Exit price {symbol}: ${validated_price:.2f} ({src_desc})")
        elif ibkr_price > 0:
            # Fallback: if validation failed but IBKR has something, use it for logging
            # (the market order will execute at actual market price regardless)
            info["current"] = ibkr_price
            log.warning(f"Exit price {symbol}: using unvalidated IBKR ${ibkr_price:.2f} — validation failed: {src_desc}")

        # Direction-aware close: LONG positions close with SELL, SHORT positions close with BUY
        direction = info.get("direction", "LONG")
        close_action = "BUY" if direction == "SHORT" else "SELL"
        close_order = MarketOrder(close_action, info["qty"], account=CONFIG["active_account"])
        close_order.outsideRth = True
        sell_trade = ib.placeOrder(contract, close_order)
        ib.sleep(1)

        # Log the close order
        log_order({
            "order_id":   sell_trade.order.orderId,
            "symbol":     symbol,
            "side":       close_action,
            "order_type": "MKT",
            "qty":        info["qty"],
            "price":      info["current"],
            "status":     "SUBMITTED",
            "instrument": "stock",
            "role":       "close",
            "reason":     reason,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        if direction == "SHORT":
            pnl = (info["entry"] - info["current"]) * info["qty"]  # SHORT profits when price drops
        else:
            pnl = (info["current"] - info["entry"]) * info["qty"]
        if pnl >= 0:
            record_win()
        else:
            record_loss()

        log.info(f"{'✅' if pnl >= 0 else '❌'} CLOSE {direction} {symbol} ({close_action}) | P&L ${pnl:+.2f} | Reason: {reason}")
        with _trades_lock:
            del active_trades[symbol]
        return True

    except Exception as e:
        log.error(f"Sell failed {symbol}: {e}")
        return False


def flatten_all(ib_fallback: IB = None):
    """
    EMERGENCY — flatten all open positions immediately via emergency IB connection.
    Called by kill switch or catastrophic drawdown detection.
    Closes EVERYTHING in IBKR portfolio — not just what the bot is tracking.

    Uses emergency IB connection (clientId=11) so it executes INSTANTLY
    even while the main scanner is mid-scan.
    Uses aggressive LIMIT orders (not market) for extended hours compatibility.
    """
    global _flatten_in_progress
    with _flatten_lock:
        if _flatten_in_progress:
            log.warning("🚨 FLATTEN ALL — re-entrant call ignored (already running)")
            return
        _flatten_in_progress = True

    try:
        _flatten_all_inner(ib_fallback)
    finally:
        with _flatten_lock:
            _flatten_in_progress = False


def _wait_for_order_book_clear(eib: IB, timeout: float = _GLOBAL_CANCEL_WAIT_SECS) -> int:
    """Poll IBKR until the open-order book is empty or timeout expires.

    After reqGlobalCancel, IBKR processes cancellations asynchronously.
    Waiting here gives the exchange time to acknowledge before we submit
    closing market orders — avoiding conflicts with pending orders.

    Returns:
        Number of orders still remaining when we stopped polling (0 = fully clear).
    """
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            remaining = eib.openOrders()
            if not remaining:
                return 0
        except Exception:
            return 0  # If we can't query, proceed anyway
        eib.sleep(_GLOBAL_CANCEL_POLL_INTERVAL)
    try:
        remaining = eib.openOrders()
        count = len(remaining) if remaining else 0
    except Exception:
        count = 0
    log.warning(f"🚨 _wait_for_order_book_clear: timed out with {count} orders remaining")
    return count


def _flatten_all_inner(ib_fallback: IB = None):
    """Internal implementation of flatten_all — called under re-entrancy guard."""
    # Use emergency connection for instant execution; fall back to main if unavailable
    eib = _get_emergency_ib()
    if not eib:
        log.warning("🚨 Emergency IB unavailable — falling back to main connection")
        eib = ib_fallback
    if not eib:
        log.error("🚨 FLATTEN ALL FAILED — no IB connection available")
        with _trades_lock:
            stranded = list(active_trades.items())
        if stranded:
            log.critical(
                f"🚨 FLATTEN ABORTED — {len(stranded)} position(s) NOT closed. "
                "Manual intervention required:"
            )
            for key, info in stranded:
                sym = info.get("symbol", key.split("_")[0])
                qty = info.get("qty", 0)
                direction = info.get("direction", "LONG")
                log.critical(f"   ↳ {sym}  qty={qty}  dir={direction}  key={key}")
        return

    log.critical("🚨 FLATTEN ALL — closing all positions immediately")

    # 0) Stop all fill watchers so they don't race reqGlobalCancel
    try:
        from fill_watcher import stop_watcher as _stop_watcher
        with _trades_lock:
            symbols_to_stop = [info.get("symbol", key.split("_")[0])
                               for key, info in active_trades.items()]
        for sym in symbols_to_stop:
            _stop_watcher(sym)
    except Exception as _fw_err:
        log.warning(f"FillWatcher stop-all raised: {_fw_err}")

    # 1) Atomically cancel ALL open orders with a single reqGlobalCancel
    try:
        eib.reqGlobalCancel()
    except Exception as e:
        log.error(f"🚨 reqGlobalCancel failed: {e} — continuing to close positions")

    # 2) Wait for the order book to drain before placing closing orders
    _wait_for_order_book_clear(eib, timeout=_GLOBAL_CANCEL_WAIT_SECS)

    # 3) Close all positions tracked in active_trades (bot's source of truth)
    closed = 0
    with _trades_lock:
        snapshot = list(active_trades.items())

    for key, info in snapshot:
        sym = info.get("symbol", key.split("_")[0])
        qty = info.get("qty", 0)
        instrument = info.get("instrument", "stock")
        if qty == 0:
            continue
        try:
            direction = info.get("direction", "LONG")
            close_action = "BUY" if direction == "SHORT" else "SELL"
            if instrument == "option":
                from ib_async import Option as _FlatOpt
                contract = _FlatOpt(sym, info["expiry_ibkr"], info["strike"], info["right"], exchange="SMART", currency="USD")
                try:
                    eib.qualifyContracts(contract)
                except Exception:
                    pass
                mkt = info.get("current_premium") or info.get("entry_premium") or 0.01
                lp = max(round(float(mkt) * 0.90, 2), 0.01)
                order = LimitOrder(close_action, abs(int(qty)), lp, tif="GTC")
                log.warning(f"🚨 FLATTEN: LMT {close_action} {abs(int(qty))} {sym} OPT @${lp:.2f} ({direction})")
            else:
                contract = get_contract(sym, instrument)
                order = MarketOrder(close_action, abs(int(qty)))
                log.warning(f"🚨 FLATTEN: Market {close_action} {abs(int(qty))} {sym} ({direction})")
            eib.placeOrder(contract, order)
            _safe_del_trade(key)
            closed += 1
        except Exception as e:
            log.error(f"🚨 FLATTEN failed for {sym}: {e}")

    log.warning(f"🚨 FLATTEN ALL complete — {closed} orders placed, tracker cleared")


def close_position(ib_unused, trade_key: str) -> str | None:
    """
    Close a single position by trade_key IMMEDIATELY via emergency IB connection.
    trade_key can be a plain symbol (e.g. "KOD") for stocks, or a composite key
    (e.g. "KOD_C_35.0_2026-04-17") for options.

    Uses aggressive limit orders for after-hours compatibility.
    Also cancels any related open orders (stops, TPs) for that symbol.
    Returns a description string on success, None if position not found.

    NOTE: ib_unused param kept for API compatibility but is IGNORED.
    This function uses its own dedicated IB connection (clientId=11)
    so it can execute instantly even while a scan is running.
    """
    trade_key = trade_key.upper().strip()
    eib = _get_emergency_ib()
    if not eib:
        log.error(f"Close {trade_key}: No emergency IB connection available")
        return None

    # 1) Find the position in IBKR portfolio using composite key matching
    try:
        portfolio_items = eib.portfolio(CONFIG["active_account"])
    except Exception as e:
        log.error(f"Close {trade_key}: Could not read IBKR portfolio: {e}")
        return None

    target = None
    for item in portfolio_items:
        if item.position != 0 and _ibkr_item_to_key(item).upper() == trade_key:
            target = item
            break

    # Fallback: try matching just the symbol (backward compat for stock-only calls)
    if not target:
        for item in portfolio_items:
            if item.position != 0 and item.contract.symbol.upper() == trade_key and item.contract.secType == "STK":
                target = item
                break

    if not target:
        log.warning(f"Close {trade_key}: Position not found in IBKR portfolio")
        return None

    sym = target.contract.symbol
    pos = target.position
    mkt = float(target.marketPrice)
    action = "SELL" if pos > 0 else "BUY"
    qty = abs(int(pos))
    is_option = target.contract.secType == "OPT"
    instrument = "option" if is_option else "stock"

    # 2) Cancel related open orders for this symbol
    try:
        for t in eib.trades():
            if t.contract.symbol == sym and t.orderStatus.status in ('Submitted', 'PreSubmitted'):
                try:
                    eib.cancelOrder(t.order)
                    log.info(f"Close {trade_key}: Cancelled order {t.order.orderId}")
                except Exception:
                    pass
        eib.sleep(0.3)
    except Exception as e:
        log.warning(f"Close {trade_key}: Error cancelling related orders: {e}")

    # 3) Place aggressive limit order
    contract = target.contract
    contract.exchange = "SMART"
    try:
        eib.qualifyContracts(contract)
    except Exception:
        pass  # Proceed with exchange='SMART' even if qualify fails

    if mkt > 0:
        if action == "SELL":
            limit_price = round(mkt * 0.98, 2)  # 2% below for fast fill
        else:
            limit_price = round(mkt * 1.02, 2)  # 2% above for fast fill
    else:
        avg = float(target.averageCost)
        if action == "SELL":
            limit_price = round(avg * 0.95, 2)
        else:
            limit_price = round(avg * 1.05, 2)

    order = LimitOrder(action, qty, limit_price,
                       account=CONFIG["active_account"],
                       tif="GTC", outsideRth=True)
    close_trade = eib.placeOrder(contract, order)
    eib.sleep(0.3)

    # Log the close order
    log_order({
        "order_id":   close_trade.order.orderId,
        "symbol":     sym,
        "side":       action,
        "order_type": "LMT",
        "qty":        qty,
        "price":      limit_price,
        "status":     "SUBMITTED",
        "instrument": instrument,
        "role":       "close",
        "reason":     "Manual close from dashboard",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    })

    detail = f"{action} {qty} {sym} {'OPT' if is_option else ''} LIMIT @${limit_price:.2f} (mkt=${mkt:.2f})"
    log.warning(f"📤 INSTANT close: {detail}")

    # 4) Remove from bot tracker — try composite key first, then plain symbol
    tracker_key = _ibkr_item_to_key(target)
    if tracker_key in active_trades:
        del active_trades[tracker_key]
    elif trade_key in active_trades:
        del active_trades[trade_key]

    return detail


def reconcile_with_ibkr(ib: IB):
    """
    On startup or reconnect: sync bot's position tracker with IBKR reality.
    Uses ib.portfolio() which includes marketPrice and unrealizedPNL.
    All prices are cross-checked via 3-way validation (IBKR + yfinance + TV)
    to prevent stale/bad IBKR data from corrupting the tracker.

    IMPORTANT: Uses composite keys (symbol for stocks, symbol_right_strike_expiry
    for options) so stock and option positions for the same underlying never collide.
    """
    log.info("Reconciling positions with IBKR (3-way price validation)...")
    try:
        # portfolio() returns PortfolioItem with marketPrice + unrealizedPNL
        # positions() only returns avgCost — never use it for reconciliation
        portfolio_items = ib.portfolio(CONFIG["active_account"])

        # Build set of IBKR keys using composite keys (stock vs option safe)
        ibkr_keys = set()
        for item in portfolio_items:
            if item.position != 0:
                ibkr_keys.add(_ibkr_item_to_key(item))

        # Remove from tracker if IBKR doesn't have it (under lock — prop-014)
        # PENDING entries need special handling: they're not in portfolio yet (unfilled),
        # so check ib.openTrades() before deciding whether to remove them.
        with _trades_lock:
            for key in list(active_trades.keys()):
                if key not in ibkr_keys:
                    trade = active_trades[key]
                    if trade.get("status") == "PENDING":
                        order_id = trade.get("order_id")
                        still_live = False
                        if order_id:
                            try:
                                for t in ib.openTrades():
                                    if t.order.orderId == order_id:
                                        still_live = True
                                        break
                            except Exception:
                                still_live = True  # err on side of keeping it
                        if still_live:
                            log.debug(f"Reconcile: PENDING {key} order #{order_id} still live in IBKR — keeping")
                            continue
                        else:
                            log.warning(
                                f"Reconcile: PENDING {key} order #{order_id} not in IBKR open orders "
                                f"— cancelling and removing from tracker"
                            )
                            if order_id:
                                _cancel_ibkr_order_by_id(ib, order_id)
                            del active_trades[key]
                    else:
                        log.warning(f"Position {key} in bot memory but not in IBKR — removing")
                        del active_trades[key]

        # Add to tracker if IBKR has it but we don't
        reconciled_count = 0
        failed_count = 0
        for item in portfolio_items:
            if item.position == 0:
                continue

            # Per-item try/except: one bad position must NOT kill the entire loop
            try:
                key = _ibkr_item_to_key(item)
                sym = item.contract.symbol
                is_option = _is_option_contract(item.contract)

                ibkr_mkt = float(item.marketPrice)

                # For options, IBKR reports:
                #   averageCost = per-CONTRACT (×100), e.g. $370.59 = $3.7059/share × 100
                #   marketPrice = per-SHARE premium already, e.g. $4.30
                # Our tracker stores per-SHARE premiums to match execute_buy_option.
                if is_option:
                    entry = round(float(item.averageCost) / 100, 4)  # convert per-contract to per-share
                    ibkr_price_for_validation = round(ibkr_mkt, 4) if ibkr_mkt > 0 else 0  # already per-share
                else:
                    entry = round(float(item.averageCost), 4)
                    ibkr_price_for_validation = ibkr_mkt if ibkr_mkt > 0 else 0

                # 3-way validate — for options, use per-share premium values
                # Skip yfinance/TV cross-check for options (they return stock price, not premium)
                if is_option:
                    # Options: trust IBKR premium directly (yfinance/TV don't have option prices)
                    if ibkr_price_for_validation > 0:
                        validated_price = ibkr_price_for_validation
                        src_desc = f"IBKR_OPT=${ibkr_price_for_validation:.2f}"
                    else:
                        validated_price = entry
                        src_desc = "IBKR returned no option price — using entry"
                        log.warning(f"Reconcile {key}: {src_desc}")
                else:
                    validated_price, src_desc = _validate_position_price(sym, ibkr_price_for_validation, entry)
                    if validated_price <= 0:
                        log.warning(f"Reconcile {key}: no validated price ({src_desc}) — using entry ${entry:.2f} as current")
                        validated_price = entry

                if key not in active_trades:
                    direction = "SHORT" if item.position < 0 else "LONG"
                    qty = abs(int(item.position))

                    if is_option:
                        # Build option-specific entry matching execute_buy_option format
                        c = item.contract
                        raw_exp = str(c.lastTradeDateOrContractMonth)
                        if len(raw_exp) == 8 and raw_exp.isdigit():
                            expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                        else:
                            expiry_str = raw_exp
                        right = "C" if c.right in ("C", "CALL") else "P"

                        log.info(f"Option {key} in IBKR but not tracked — adding ({direction} {qty} contracts, premium ${entry:.2f}, validated ${validated_price:.2f} via {src_desc})")
                        # Options P&L: per-share premium × qty × 100 (contract multiplier)
                        if direction == "SHORT":
                            pnl = round((entry - validated_price) * qty * 100, 2)
                        else:
                            pnl = round((validated_price - entry) * qty * 100, 2)
                        _safe_set_trade(key, {
                            "symbol":          sym,
                            "instrument":      "option",
                            "right":           right,
                            "strike":          c.strike,
                            "expiry_str":      expiry_str,
                            "expiry_ibkr":     raw_exp,
                            "dte":             0,  # Unknown at reconciliation
                            "contracts":       qty,
                            "entry_premium":   entry,
                            "current_premium": validated_price,
                            "entry":           entry,
                            "current":         round(validated_price, 4),
                            "qty":             qty,
                            "sl":              round(entry * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
                            "tp":              round(entry * (1 + CONFIG.get("options_profit_target", 1.00)), 4),
                            "direction":       direction,
                            "score":           0,
                            "reasoning":       "Reconciled from IBKR on startup",
                            "pnl":             pnl,
                            "status":          "ACTIVE",
                            "_price_sources":  src_desc,
                        })
                    else:
                        # Stock position
                        if direction == "SHORT":
                            sl = round(entry * 1.02, 2)
                            tp = round(entry * 0.94, 2)
                        else:
                            sl = round(entry * 0.98, 2)
                            tp = round(entry * 1.06, 2)
                        log.info(f"Position {key} in IBKR but not tracked — adding ({direction} {qty} shares @ ${entry:.2f}, validated price ${validated_price:.2f} via {src_desc})")
                        if direction == "SHORT":
                            pnl = round((entry - validated_price) * qty, 2)
                        else:
                            pnl = round((validated_price - entry) * qty, 2)
                        _safe_set_trade(key, {
                            "symbol":    sym,
                            "instrument": "stock",
                            "entry":     entry,
                            "current":   round(validated_price, 4),
                            "qty":       qty,
                            "sl":        sl,
                            "tp":        tp,
                            "score":     0,
                            "reasoning": "Reconciled from IBKR on startup",
                            "direction": direction,
                            "pnl":       pnl,
                            "status":    "ACTIVE",
                            "_price_sources": src_desc,
                        })
                else:
                    # Update prices for existing tracked positions (prop-014: under lock)
                    mult = 100 if is_option else 1
                    with _trades_lock:
                        if key in active_trades:
                            active_trades[key]["current"] = round(validated_price, 4)
                            direction = active_trades[key].get("direction", "LONG")
                            qty = active_trades[key]["qty"]
                            if direction == "SHORT":
                                active_trades[key]["pnl"] = round((entry - validated_price) * qty * mult, 2)
                            else:
                                active_trades[key]["pnl"] = round((validated_price - entry) * qty * mult, 2)
                            active_trades[key]["status"]  = "ACTIVE"
                            active_trades[key]["_price_sources"] = src_desc
                            if is_option:
                                active_trades[key]["current_premium"] = round(validated_price, 4)

                reconciled_count += 1

            except Exception as item_err:
                failed_count += 1
                item_sym = getattr(getattr(item, 'contract', None), 'symbol', '???')
                log.error(f"Reconciliation failed for {item_sym}: {item_err} — skipping, continuing with remaining positions")

        log.info(f"Reconciliation complete. Tracking {len(active_trades)} positions. (processed={reconciled_count}, failed={failed_count})")

    except Exception as e:
        log.error(f"Reconciliation error: {e}")


def update_positions_from_ibkr(ib: IB):
    """
    Refresh current price and P&L for all tracked positions using 3-way price
    validation (IBKR + yfinance + TV). Called on every scan so dashboard always
    shows live P&L even when no symbols score.

    Uses composite keys to match IBKR portfolio items to the correct active_trades
    entry (preventing stock/option collision). Stock prices are 3-way validated;
    option premiums use IBKR only (yfinance/TV don't have option pricing).
    """
    try:
        portfolio_items = ib.portfolio(CONFIG["active_account"])
        # Build price map keyed by composite key (stock vs option safe)
        price_map = {}
        for item in portfolio_items:
            if item.position != 0:
                price_map[_ibkr_item_to_key(item)] = item

        # Remove positions no longer in IBKR (closed externally via SL/TP/manual)
        with _trades_lock:
            stale_keys = [k for k in active_trades if k not in price_map and active_trades[k].get("status") != "PENDING"]
            for k in stale_keys:
                log.warning(f"Position {k} no longer in IBKR portfolio — removing from tracker")
                del active_trades[k]

        # ── Orphaned PENDING detection ────────────────────────────────────────
        # A PENDING entry with no active FillWatcher and past orphan_timeout_mins
        # is unmanaged (e.g. watcher aborted on disconnect). Cancel at IBKR and remove.
        from fill_watcher import _active_watchers, _watchers_lock as _fw_lock
        _orphan_mins = CONFIG.get("fill_watcher", {}).get("orphan_timeout_mins", 5)

        with _trades_lock:
            _pending_keys = [k for k in active_trades if active_trades[k].get("status") == "PENDING"]

        for _key in _pending_keys:
            with _fw_lock:
                _has_watcher = _key in _active_watchers
            if _has_watcher:
                continue

            with _trades_lock:
                _trade = active_trades.get(_key)
            if _trade is None:
                continue

            _open_time_str = _trade.get("open_time")
            try:
                _open_dt = datetime.fromisoformat(_open_time_str)
                _age_mins = (datetime.now(timezone.utc) - _open_dt).total_seconds() / 60
            except (ValueError, TypeError):
                _age_mins = _orphan_mins + 1  # treat unparseable timestamp as timed-out

            if _age_mins < _orphan_mins:
                continue

            _oid = _trade.get("order_id")
            log.warning(
                f"Orphaned PENDING order {_key} order #{_oid} "
                f"(age={_age_mins:.1f} min, no FillWatcher) — cancelling"
            )
            if _oid:
                _cancel_ibkr_order_by_id(ib, _oid)
            _safe_del_trade(_key)

        # Re-add positions that IBKR has but tracker is missing
        # (lightweight reconciliation — catches positions lost by failed sells,
        #  partial startup reconciliation, or any other tracker/IBKR desync)
        for ibkr_key, item in price_map.items():
            if ibkr_key not in active_trades:
                try:
                    is_opt = _is_option_contract(item.contract)
                    sym = item.contract.symbol
                    direction = "SHORT" if item.position < 0 else "LONG"
                    qty = abs(int(item.position))
                    ibkr_mkt = float(item.marketPrice)

                    if is_opt:
                        entry = round(float(item.averageCost) / 100, 4)
                        validated = round(ibkr_mkt, 4) if ibkr_mkt > 0 else entry
                        c = item.contract
                        raw_exp = str(c.lastTradeDateOrContractMonth)
                        if len(raw_exp) == 8 and raw_exp.isdigit():
                            expiry_str = f"{raw_exp[:4]}-{raw_exp[4:6]}-{raw_exp[6:]}"
                        else:
                            expiry_str = raw_exp
                        right = "C" if c.right in ("C", "CALL") else "P"
                        mult = 100
                        if direction == "SHORT":
                            pnl = round((entry - validated) * qty * mult, 2)
                        else:
                            pnl = round((validated - entry) * qty * mult, 2)
                        _safe_set_trade(ibkr_key, {
                            "symbol": sym, "instrument": "option",
                            "right": right, "strike": c.strike,
                            "expiry_str": expiry_str, "expiry_ibkr": raw_exp,
                            "dte": 0, "contracts": qty,
                            "entry_premium": entry, "current_premium": validated,
                            "entry": entry, "current": validated,
                            "qty": qty,
                            "sl": round(entry * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
                            "tp": round(entry * (1 + CONFIG.get("options_profit_target", 1.00)), 4),
                            "direction": direction, "score": 0,
                            "reasoning": "Re-synced from IBKR (was missing from tracker)",
                            "pnl": pnl, "status": "ACTIVE",
                        })
                        log.warning(f"Re-added missing option {ibkr_key} from IBKR ({direction} {qty}x, premium ${entry:.4f})")
                    else:
                        entry = round(float(item.averageCost), 4)
                        validated = ibkr_mkt if ibkr_mkt > 0 else entry
                        if direction == "SHORT":
                            sl = round(entry * 1.02, 2); tp = round(entry * 0.94, 2)
                            pnl = round((entry - validated) * qty, 2)
                        else:
                            sl = round(entry * 0.98, 2); tp = round(entry * 1.06, 2)
                            pnl = round((validated - entry) * qty, 2)
                        _safe_set_trade(ibkr_key, {
                            "symbol": sym, "instrument": "stock",
                            "entry": entry, "current": round(validated, 4),
                            "qty": qty, "sl": sl, "tp": tp, "score": 0,
                            "reasoning": "Re-synced from IBKR (was missing from tracker)",
                            "direction": direction, "pnl": pnl, "status": "ACTIVE",
                        })
                        log.warning(f"Re-added missing stock {ibkr_key} from IBKR ({direction} {qty} @ ${entry:.2f})")
                except Exception as readd_err:
                    log.error(f"Failed to re-add {ibkr_key}: {readd_err}")

        with _trades_lock:
            trades_snapshot = dict(active_trades)
        for key, trade in trades_snapshot.items():
            is_option = trade.get("instrument") == "option"
            sym = trade.get("symbol", key)
            entry = trade.get("entry", 0)

            ibkr_price = 0
            if key in price_map:
                item = price_map[key]
                mkt_price = float(item.marketPrice)
                if mkt_price > 0:
                    if is_option:
                        # IBKR marketPrice for options is already per-share premium
                        ibkr_price = round(mkt_price, 4)
                    else:
                        ibkr_price = mkt_price

            # Options: trust IBKR premium (yfinance/TV return stock price, not premium)
            if is_option:
                if ibkr_price > 0:
                    validated_price = ibkr_price
                    src_desc = f"IBKR_OPT=${ibkr_price:.2f}"
                else:
                    log.warning(f"No IBKR price for option {key} — keeping previous ${trade.get('current', 0):.2f}")
                    continue
            else:
                validated_price, src_desc = _validate_position_price(sym, ibkr_price, entry)

            if validated_price > 0:
                trade["current"] = round(validated_price, 4)
                if is_option:
                    trade["current_premium"] = round(validated_price, 4)
                # Recalculate P&L from validated price
                # Options: per-share premium × qty × 100 (contract multiplier)
                mult = 100 if is_option else 1
                direction = trade.get("direction", "LONG")
                if direction == "SHORT":
                    trade["pnl"] = round((entry - validated_price) * trade["qty"] * mult, 2)
                else:
                    trade["pnl"] = round((validated_price - entry) * trade["qty"] * mult, 2)
                trade["_price_sources"] = src_desc
            else:
                log.warning(f"No validated price for {key}: {src_desc} — keeping previous ${trade.get('current', 0):.2f}")

    except Exception as e:
        log.warning(f"Position price update error: {e}")


def update_position_prices(signals: list):
    """
    DEPRECATED — kept for backward compatibility but now a no-op.
    3-way validation is handled entirely by update_positions_from_ibkr().
    """
    pass  # All price validation now happens in update_positions_from_ibkr via _validate_position_price


def get_open_positions() -> list:
    """Return list of open positions for dashboard and agent consumption.
    Injects '_trade_key' into each position so the dashboard close button
    can send the correct composite key (stock vs option safe).
    """
    with _trades_lock:
        snapshot = list(active_trades.items())
    result = []
    for key, trade in snapshot:
        pos = dict(trade)
        pos["_trade_key"] = key
        result.append(pos)
    return result


# ── Options execution ──────────────────────────────────────────────────

def execute_buy_option(ib: IB, contract_info: dict,
                       portfolio_value: float, reasoning: str = "") -> bool:
    """
    Buy an options contract (call or put).
    contract_info is the dict returned by options.find_best_contract().
    Entry is a limit order at the mid price.
    Returns True if order placed successfully.
    """
    symbol    = contract_info["symbol"]
    opt_key   = f"{symbol}_{contract_info['right']}_{contract_info['strike']}_{contract_info['expiry_str']}"

    # Options only trade during regular market hours (9:30–16:00 ET)
    if not is_options_market_open():
        now_et = datetime.now(_ET)
        log.warning(f"Options market closed ({now_et.strftime('%H:%M ET')}) — skipping {opt_key}")
        return False

    with _trades_lock:
        if opt_key in active_trades:
            log.warning(f"Already holding {opt_key} — skipping")
            return False

        if len(active_trades) >= CONFIG["max_positions"]:
            log.warning(f"Max positions reached — skipping options trade {symbol}")
            return False

        # ── FIX #1+3: Cross-instrument + combined exposure check ──────
        n_contracts = contract_info["contracts"]
        mid_price   = contract_info["mid"]
        est_option_value = n_contracts * mid_price * 100  # total premium outlay

        exp_ok, exp_reason = check_combined_exposure(
            symbol, est_option_value, list(active_trades.values()),
            portfolio_value, instrument="option"
        )
        if not exp_ok:
            log.warning(f"Combined exposure block for {symbol} options: {exp_reason}")
            return False

        # ── FIX #2: Sector concentration check ────────────────────────
        sec_ok, sec_reason = check_sector_concentration(
            symbol, list(active_trades.values()),
            portfolio_value  # regime not passed to execute_buy_option, default NORMAL
        )
        if not sec_ok:
            log.warning(f"Sector block for {symbol} options: {sec_reason}")
            return False

        # ── Reserve slot — closes TOCTOU gap between check and submission ──
        active_trades[opt_key] = {"status": "RESERVED", "symbol": symbol, "instrument": "option"}

    # Limit price slightly above mid to improve fill probability
    limit_price = round(mid_price * 1.01, 2)

    try:
        option_contract = Option(
            symbol,
            contract_info["expiry_ibkr"],
            contract_info["strike"],
            contract_info["right"],
            exchange="SMART",
            currency="USD",
        )
        ib.qualifyContracts(option_contract)
        account = CONFIG["active_account"]

        # Options only trade during regular hours — outsideRth must be False
        entry_order = LimitOrder("BUY", n_contracts, limit_price,
                                 account=account, tif="DAY", outsideRth=False)
        trade = ib.placeOrder(option_contract, entry_order)
        ib.sleep(1)

        # Check if IBKR immediately rejected the order
        order_status = trade.orderStatus.status
        if order_status in ('Cancelled', 'Inactive', 'ApiCancelled', 'ValidationError'):
            log.error(f"Option order immediately rejected by IBKR for {opt_key}: {order_status}")
            _safe_del_trade(opt_key)  # release reservation
            return False

        # Log the option order
        log_order({
            "order_id":   trade.order.orderId,
            "symbol":     symbol,
            "side":       "BUY",
            "order_type": "LMT",
            "qty":        n_contracts,
            "price":      limit_price,
            "status":     "SUBMITTED",
            "instrument": "option",
            "right":      contract_info["right"],
            "strike":     contract_info["strike"],
            "expiry":     contract_info["expiry_str"],
            "reasoning":  reasoning,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        active_trades[opt_key] = {
            "symbol":          symbol,
            "instrument":      "option",
            "right":           contract_info["right"],
            "strike":          contract_info["strike"],
            "expiry_str":      contract_info["expiry_str"],
            "expiry_ibkr":     contract_info["expiry_ibkr"],
            "dte":             contract_info["dte"],
            "contracts":       n_contracts,
            "entry_premium":   mid_price,
            "current_premium": mid_price,
            "entry":           mid_price,          # unified field for dashboard
            "current":         mid_price,
            "qty":             n_contracts,
            "sl":              round(mid_price * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
            "tp":              round(mid_price * (1 + CONFIG.get("options_profit_target", 0.75)), 4),
            "delta":           contract_info.get("delta"),
            "theta":           contract_info.get("theta"),
            "iv":              contract_info.get("iv"),
            "iv_rank":         contract_info.get("iv_rank"),
            "underlying_price": contract_info.get("underlying_price"),
            "pnl":             0.0,
            "score":           0,
            "direction":       "LONG",
            "reasoning":       reasoning,
            "status":          "PENDING",
            "order_id":        trade.order.orderId,
        }

        log.info(
            f"✅ BUY {contract_info['right']} {symbol} "
            f"${contract_info['strike']:.0f} exp={contract_info['expiry_str']} "
            f"x{n_contracts} @ ${limit_price:.2f} mid "
            f"| delta={contract_info.get('delta'):.3f} "
            f"| IVR={contract_info.get('iv_rank')}%"
        )
        return True

    except Exception as e:
        _safe_del_trade(opt_key)  # clean up reservation if order failed
        log.error(f"Option buy failed {symbol}: {e}")
        return False


_option_sell_attempts: dict = {}   # opt_key → {"count": int, "last_try": datetime}
_MAX_OPTION_SELL_RETRIES = 3       # after this many failures, pause retries for cooldown
_OPTION_SELL_COOLDOWN = 600        # seconds (10 min) before retrying after max failures


def execute_sell_option(ib: IB, opt_key: str, reason: str = "signal") -> bool:
    """
    Close an open options position using a limit order at the current bid.
    IBKR (especially paper) rejects MKT orders on illiquid options, so we use
    an aggressive LMT at the bid price (or mid if bid unavailable).
    opt_key format: SYMBOL_RIGHT_STRIKE_EXPIRY  (e.g. NVDA_C_180_2026-04-01)
    Returns True if order filled.
    """
    # Options only trade during regular market hours (9:30–16:00 ET)
    if not is_options_market_open():
        now_et = datetime.now(_ET)
        log.warning(f"Options market closed ({now_et.strftime('%H:%M ET')}) — cannot sell {opt_key}")
        return False

    if opt_key not in active_trades:
        log.warning(f"No open options position {opt_key}")
        return False

    pos = active_trades[opt_key]
    if pos.get("instrument") != "option":
        log.warning(f"{opt_key} is not an options position")
        return False

    # ── Retry gating: don't spam IBKR with the same failing order ──
    attempts = _option_sell_attempts.get(opt_key, {"count": 0, "last_try": datetime.min})
    if attempts["count"] >= _MAX_OPTION_SELL_RETRIES:
        elapsed = (datetime.now(timezone.utc) - attempts["last_try"]).total_seconds()
        if elapsed < _OPTION_SELL_COOLDOWN:
            log.warning(f"Option sell for {opt_key} failed {attempts['count']}x — "
                        f"cooling down ({int(_OPTION_SELL_COOLDOWN - elapsed)}s remaining)")
            return False
        # Cooldown expired — reset counter and retry
        attempts["count"] = 0

    try:
        option_contract = Option(
            pos["symbol"],
            pos["expiry_ibkr"],
            pos["strike"],
            pos["right"],
            exchange="SMART",
            currency="USD",
        )
        ib.qualifyContracts(option_contract)

        # ── Get current bid for limit price ──
        # IBKR paper accounts reject MKT orders on many options.
        # Use LMT at the bid (aggressive sell) to ensure fills.
        ticker = ib.reqMktData(option_contract, '', False, False)
        ib.sleep(2)  # allow quote data to arrive

        bid = getattr(ticker, 'bid', None)
        ask = getattr(ticker, 'ask', None)
        last = getattr(ticker, 'last', None)

        # Determine limit price: mid → bid → last → current_premium
        import math as _m
        _bid_ok = bid is not None and not _m.isnan(bid) and bid > 0
        _ask_ok = ask is not None and not _m.isnan(ask) and ask > 0
        if _bid_ok and _ask_ok:
            limit_price = round((bid + ask) / 2, 2)
        elif _bid_ok:
            limit_price = round(bid, 2)
        elif last and not _m.isnan(last) and last > 0:
            limit_price = round(last * 0.97, 2)  # 3% below last as safety
        else:
            limit_price = round(pos.get("current_premium", 0.01) * 0.95, 2)

        # Floor at $0.01 — can't send a zero-price limit
        limit_price = max(limit_price, 0.01)

        ib.cancelMktData(option_contract)

        sell_order = LimitOrder("SELL", pos["contracts"], limit_price,
                                account=CONFIG["active_account"],
                                tif="DAY")
        sell_order.outsideRth = False
        opt_sell_trade = ib.placeOrder(option_contract, sell_order)

        log.info(f"Option LMT sell placed: {opt_key} x{pos['contracts']} @ ${limit_price:.2f} "
                 f"(bid={bid}, ask={ask})")

        # Wait for fill confirmation — options can take a moment
        max_wait = 15  # seconds
        for _ in range(max_wait * 2):
            ib.sleep(0.5)
            status = opt_sell_trade.orderStatus.status
            if status in ("Filled", "Cancelled", "Inactive", "ApiCancelled"):
                break

        order_status = opt_sell_trade.orderStatus.status
        if order_status != "Filled":
            # Track failed attempt
            attempts["count"] += 1
            attempts["last_try"] = datetime.now(timezone.utc)
            _option_sell_attempts[opt_key] = attempts
            log.error(f"Option sell for {opt_key} not filled — status={order_status}, "
                      f"limit=${limit_price:.2f}. Attempt {attempts['count']}/{_MAX_OPTION_SELL_RETRIES}. "
                      f"Keeping position in tracker (IBKR still holds it).")
            # Cancel the unfilled order so it doesn't linger
            try:
                ib.cancelOrder(opt_sell_trade.order)
            except Exception:
                pass
            return False

        # Success — clear retry counter
        _option_sell_attempts.pop(opt_key, None)

        # Get actual fill price from the trade object
        fill_price = opt_sell_trade.orderStatus.avgFillPrice or limit_price

        # Log the option sell order
        log_order({
            "order_id":   opt_sell_trade.order.orderId,
            "symbol":     pos["symbol"],
            "side":       "SELL",
            "order_type": "LMT",
            "qty":        pos["contracts"],
            "price":      limit_price,
            "status":     "FILLED",
            "instrument": "option",
            "right":      pos["right"],
            "strike":     pos["strike"],
            "expiry":     pos["expiry_str"],
            "fill_price": fill_price,
            "role":       "close",
            "reason":     reason,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        entry   = pos["entry_premium"]
        current = fill_price  # use actual fill price, not stale current_premium
        pnl     = (current - entry) * pos["contracts"] * 100

        # ── Check commission report for IBKR realizedPNL (most accurate) ──
        try:
            import math as _math
            _fills = ib.fills()
            opt_sell_fills = [
                f for f in _fills
                if f.contract.symbol == pos["symbol"]
                and f.execution.side.upper() in ("SLD", "SELL")
                and _is_option_contract(f.contract)
            ]
            for f in opt_sell_fills:
                cr = f.commissionReport
                if cr is not None:
                    raw = getattr(cr, 'realizedPNL', None)
                    if raw is not None:
                        raw_f = float(raw)
                        if not _math.isnan(raw_f) and raw_f != 0.0:
                            pnl = raw_f
                            break
        except Exception:
            pass  # fall back to fill-based P&L

        if pnl >= 0:
            record_win()
        else:
            record_loss()

        # ── Log to trade history (trades.json) ──
        from learning import log_trade
        log_trade(
            trade=pos,
            agent_outputs={},
            regime={"regime": "UNKNOWN", "vix": 0.0},
            action="CLOSE",
            outcome={
                "exit_price": round(current, 4),
                "pnl":        round(pnl, 2),
                "reason":     reason,
            }
        )

        log.info(
            f"{'✅' if pnl >= 0 else '❌'} SELL {pos['right']} {pos['symbol']} "
            f"${pos['strike']:.0f} | P&L ${pnl:+.2f} | {reason}"
        )
        del active_trades[opt_key]
        return True

    except Exception as e:
        log.error(f"Option sell failed {opt_key}: {e}")
        return False


# ── DUAL-TRANCHE STATUS ───────────────────────────────────────────────────────

def update_tranche_status(ib: IB) -> None:
    """
    Called each scan cycle after update_positions_from_ibkr(), before update_trailing_stops().

    For positions with tranche_mode=True and t1_status="OPEN":
    - Checks whether the T1 limit order (t1_order_id) has been filled by querying
      IBKR open trades. If the order ID is no longer live, T1 has filled.
    - On T1 fill: logs partial close, cancels full-qty bracket SL, places standalone
      T2 stop for t2_qty, updates active_trades to reflect T2-only position.
    """
    if not ib.isConnected():
        log.warning("[TRANCHE] IBKR disconnected — skipping tranche status update")
        return

    with _trades_lock:
        snapshot = list(active_trades.items())

    try:
        live_order_ids = {t.order.orderId for t in ib.openTrades()}
    except Exception as e:
        log.error(f"[TRANCHE] Failed to fetch open trades from IBKR: {e}")
        return

    for symbol, trade in snapshot:
        try:
            if not trade.get("tranche_mode"):
                continue
            if trade.get("t1_status") != "OPEN":
                continue
            if trade.get("instrument") != "stock":
                continue
            if trade.get("status") != "ACTIVE":
                continue

            t1_order_id = trade.get("t1_order_id")
            if t1_order_id is None or t1_order_id in live_order_ids:
                continue  # T1 still live — nothing to do

            # ── T1 HAS FILLED ──────────────────────────────────────────────────
            log.info(f"[TRANCHE] T1 filled for {symbol} (order #{t1_order_id})")

            entry    = trade["entry"]
            t1_qty   = trade["t1_qty"]
            t2_qty   = trade["t2_qty"]
            tp_t1    = trade["tp"]       # tp was set to entry + 1.5×ATR at entry time
            sl_price = trade["sl"]

            t1_pnl = round((tp_t1 - entry) * t1_qty, 2)
            from learning import log_trade
            log_trade(
                trade={**trade, "qty": t1_qty,
                       "tranche_id": 1, "parent_trade_id": trade.get("order_id")},
                agent_outputs=trade.get("agent_outputs", {}),
                regime={"regime": "UNKNOWN", "vix": 0.0},
                action="CLOSE",
                outcome={"exit_price": tp_t1, "pnl": t1_pnl, "reason": "tranche_1_tp"},
            )

            # Cancel full-qty bracket SL (T2 needs its own standalone stop)
            old_sl_id = trade.get("sl_order_id")
            if old_sl_id:
                _cancel_ibkr_order_by_id(ib, old_sl_id)
                ib.sleep(0.3)

            # Place standalone T2 stop at current sl_price (will be trailed by update_trailing_stops)
            contract = get_contract(symbol)
            t2_stop = StopOrder(
                "SELL", t2_qty, sl_price,
                account=CONFIG["active_account"],
                tif="GTC", outsideRth=True,
            )
            t2_stop.transmit = True
            t2_stop_trade = ib.placeOrder(contract, t2_stop)
            ib.sleep(0.5)
            new_id = t2_stop_trade.order.orderId

            # Update active_trades: switch to T2-only state
            with _trades_lock:
                if symbol in active_trades:
                    active_trades[symbol]["t1_status"]      = "FILLED"
                    active_trades[symbol]["t2_sl_order_id"] = new_id
                    active_trades[symbol]["sl_order_id"]    = new_id   # trailing stop reads this
                    active_trades[symbol]["qty"]            = t2_qty   # execute_sell reads this

            log.info(
                f"[TRANCHE] {symbol} T1 ✅ P&L ${t1_pnl:+.2f} — "
                f"T2 stop placed: qty={t2_qty} @ ${sl_price:.2f} orderId={new_id}"
            )

        except Exception as exc:
            log.error(f"[TRANCHE] update_tranche_status failed for {symbol}: {exc}")


# ── ATR TRAILING STOP ─────────────────────────────────────────────────────────

def update_trailing_stops(ib: IB) -> None:
    """
    Called each scan cycle after update_positions_from_ibkr().
    For every ACTIVE stock position that has a tracked sl_order_id, check whether
    the high-water mark has advanced and, if the resulting trailing stop would be
    higher (LONG) / lower (SHORT) than the current stop, modify the live IBKR
    stop order and update the tracker.

    Trail formula:
      LONG:  new_sl = high_water_mark - (atr_trail_multiplier × atr)
      SHORT: new_sl = low_water_mark  + (atr_trail_multiplier × atr)

    The trail only beats the initial stop (1.5 × ATR) once price has moved
    ~0.5 ATR in favour, so no separate activation threshold is needed.
    """
    if not CONFIG.get("trailing_stop_enabled", True):
        return

    trail_mult = CONFIG.get("atr_trail_multiplier", 2.0)

    with _trades_lock:
        snapshot = list(active_trades.items())

    for symbol, trade in snapshot:
        try:
            if trade.get("instrument") != "stock":
                continue
            if trade.get("status") != "ACTIVE":
                continue
            # Tranche guard: while T1 is still open, the bracket SL covers both tranches
            # and is intentionally kept static. Only trail once T1 fills and T2 gets its
            # own standalone stop (update_tranche_status updates sl_order_id and qty).
            if trade.get("tranche_mode") and trade.get("t1_status") == "OPEN":
                continue
            sl_order_id = trade.get("sl_order_id")
            if not sl_order_id:
                continue

            atr      = trade.get("atr")
            if not atr or atr <= 0:
                continue

            direction = trade.get("direction", "LONG")
            current   = trade.get("current", trade["entry"])
            hwm       = trade.get("high_water_mark", trade["entry"])
            old_sl    = trade["sl"]
            qty       = trade["qty"]

            if direction == "LONG":
                new_hwm = max(hwm, current)
                new_sl  = round(new_hwm - trail_mult * atr, 2)
                if new_sl <= old_sl:
                    continue  # no improvement — keep existing stop
            else:  # SHORT
                new_hwm = min(hwm, current)
                new_sl  = round(new_hwm + trail_mult * atr, 2)
                if new_sl >= old_sl:
                    continue  # no improvement — keep existing stop

            if not ib.isConnected():
                log.warning("[TRAIL] IBKR disconnected — skipping trailing stop update")
                return

            contract = get_contract(symbol)
            modified_stop = StopOrder(
                "SELL", qty, new_sl,
                account=CONFIG["active_account"],
                tif="GTC",
                outsideRth=True,
            )
            modified_stop.orderId = sl_order_id
            modified_stop.transmit = True
            ib.placeOrder(contract, modified_stop)
            ib.sleep(0.1)

            with _trades_lock:
                if symbol in active_trades:
                    active_trades[symbol]["sl"]               = new_sl
                    active_trades[symbol]["high_water_mark"]  = new_hwm

            log.info(
                f"[TRAIL] {symbol} {'▲' if direction == 'LONG' else '▼'} "
                f"stop {old_sl:.2f} → {new_sl:.2f}  hwm={new_hwm:.2f}"
            )

        except Exception as exc:
            log.error(f"[TRAIL] {symbol} trailing stop update failed: {exc}")
            continue
