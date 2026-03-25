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
from risk import calculate_position_size, calculate_stops, check_correlation, record_win, record_loss
from learning import log_order

log = logging.getLogger("decifer.orders")

# In-memory position tracker (source of truth = IBKR, this is a cache)
open_trades: dict = {}

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


def execute_buy(ib: IB, symbol: str, price: float, atr: float,
                score: int, portfolio_value: float, regime: dict,
                reasoning: str = "") -> bool:
    """
    Place a buy order with full OCO bracket.
    Entry: Limit order at IBKR real-time price (yfinance price is only a fallback)
    Stop loss: Stop order (placed immediately)
    Take profit: Limit order (placed immediately)
    Returns True if order placed successfully.
    """
    if symbol in open_trades:
        log.warning(f"Already in {symbol} — skipping buy")
        return False

    if len(open_trades) >= CONFIG["max_positions"]:
        log.warning(f"Max positions ({CONFIG['max_positions']}) reached — skipping {symbol}")
        return False

    # Correlation check
    ok, reason = check_correlation(symbol, list(open_trades.values()))
    if not ok:
        log.warning(f"Correlation block for {symbol}: {reason}")
        return False

    try:
        contract = get_contract(symbol)
        ib.qualifyContracts(contract)

        # ── GET REAL-TIME IBKR PRICE — this is the execution price ──
        # yfinance is for scanning/scoring only; IBKR is source of truth for orders
        yf_price = price  # save original for logging
        ibkr_price = _get_ibkr_price(ib, contract, fallback=0)

        if ibkr_price > 0:
            price = ibkr_price
            if yf_price > 0:
                deviation = abs(yf_price - ibkr_price) / ibkr_price
                if deviation > 0.50:
                    # >50% divergence = one of the prices is garbage (data contamination)
                    log.error(
                        f"PRICE CONTAMINATION {symbol}: yfinance=${yf_price:.2f} vs IBKR=${ibkr_price:.2f} "
                        f"({deviation:.0%} divergence) — aborting trade to protect capital"
                    )
                    return False
                elif deviation > 0.10:
                    log.warning(
                        f"Price divergence {symbol}: yfinance=${yf_price:.2f} vs IBKR=${ibkr_price:.2f} "
                        f"({deviation:.0%}) — using IBKR price"
                    )
        else:
            # No IBKR price = no market data = do NOT trade this symbol
            log.error(f"No IBKR market data for {symbol} — aborting (yfinance price not trusted for execution)")
            return False

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

        # Validate R:R
        reward = tp - price
        risk   = price - sl
        if risk <= 0 or (reward / risk) < CONFIG["min_reward_risk_ratio"]:
            log.warning(f"Poor R:R on {symbol}: reward={reward:.2f} risk={risk:.2f} — skipping")
            return False

        account = CONFIG["active_account"]

        # Entry: limit order at current price (fill within scan cycle)
        # outsideRth=True allows pre-market (4am) and after-hours (8pm) fills
        limit_price = round(price * 1.002, 2)
        entry_order = LimitOrder("BUY", qty, limit_price,
                                 account=account, tif="DAY", outsideRth=True)
        trade = ib.placeOrder(contract, entry_order)
        ib.sleep(1)

        parent_id = trade.order.orderId

        # Log the entry order
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

        # Stop loss — outsideRth so it fires in extended hours too
        sl_order = StopOrder("SELL", qty, sl, account=account, tif="GTC", outsideRth=True)
        sl_order.parentId = parent_id
        sl_order.transmit = False
        sl_trade = ib.placeOrder(contract, sl_order)

        # Log stop loss order
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

        # Take profit (partial — first tier)
        tp_qty = max(1, qty // 3)
        tp_order = LimitOrder("SELL", tp_qty, tp, account=account, tif="GTC", outsideRth=True)
        tp_order.parentId = parent_id
        tp_order.transmit = True
        tp_trade = ib.placeOrder(contract, tp_order)

        # Log take profit order
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

        # Wait briefly for IBKR to confirm the order isn't immediately rejected
        ib.sleep(0.5)
        order_status = trade.orderStatus.status
        if order_status in ('Cancelled', 'Inactive', 'ApiCancelled', 'ValidationError'):
            log.error(f"Order immediately rejected by IBKR for {symbol}: {order_status} — not tracking")
            return False

        open_trades[symbol] = {
            "symbol":    symbol,
            "entry":     price,
            "current":   price,
            "qty":       qty,
            "sl":        sl,
            "tp":        tp,
            "score":     score,
            "reasoning": reasoning,
            "direction": "LONG",
            "pnl":       0.0,
            "status":    "PENDING",   # Submitted to IBKR but not yet filled
            "order_id":  parent_id,
        }

        log.info(f"✅ BUY {symbol} qty={qty} @ ${price:.2f} | SL=${sl:.2f} TP=${tp:.2f} | R:R={reward/risk:.1f}")
        return True

    except Exception as e:
        log.error(f"Buy failed {symbol}: {e}")
        return False


def execute_sell(ib: IB, symbol: str, reason: str = "Agent signal") -> bool:
    """
    Close an existing position at market.
    Returns True if order placed.
    """
    if symbol not in open_trades:
        log.warning(f"No open position in {symbol} — skipping sell")
        return False

    info = open_trades[symbol]

    try:
        contract = get_contract(symbol)
        ib.qualifyContracts(contract)

        # Get real-time IBKR price for accurate logging/P&L
        ibkr_price = _get_ibkr_price(ib, contract, fallback=info["current"])
        if ibkr_price > 0:
            info["current"] = ibkr_price

        sell_order = MarketOrder("SELL", info["qty"], account=CONFIG["active_account"])
        sell_order.outsideRth = True
        sell_trade = ib.placeOrder(contract, sell_order)
        ib.sleep(1)

        # Log the sell/close order
        log_order({
            "order_id":   sell_trade.order.orderId,
            "symbol":     symbol,
            "side":       "SELL",
            "order_type": "MKT",
            "qty":        info["qty"],
            "price":      info["current"],
            "status":     "SUBMITTED",
            "instrument": "stock",
            "role":       "close",
            "reason":     reason,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        pnl = (info["current"] - info["entry"]) * info["qty"]
        if pnl >= 0:
            record_win()
        else:
            record_loss()

        log.info(f"{'✅' if pnl >= 0 else '❌'} SELL {symbol} | P&L ${pnl:+.2f} | Reason: {reason}")
        del open_trades[symbol]
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
    # Use emergency connection for instant execution; fall back to main if unavailable
    eib = _get_emergency_ib()
    if not eib:
        log.warning("🚨 Emergency IB unavailable — falling back to main connection")
        eib = ib_fallback
    if not eib:
        log.error("🚨 FLATTEN ALL FAILED — no IB connection available")
        return

    log.warning("🚨 FLATTEN ALL — closing all positions immediately")

    # 1) Cancel ALL open orders FIRST (stops, TPs, pending buys)
    try:
        open_orders = eib.openOrders()
        for order in open_orders:
            try:
                eib.cancelOrder(order)
                log.warning(f"🚨 FLATTEN: Cancelled order {order.orderId}")
            except Exception as e:
                log.error(f"🚨 Cancel order failed: {e}")
        if open_orders:
            eib.sleep(1)  # Give IBKR time to process cancellations
    except Exception as e:
        log.error(f"🚨 Could not cancel open orders: {e}")

    # 2) Close everything in IBKR's actual portfolio (source of truth)
    closed = 0
    try:
        portfolio_items = eib.portfolio(CONFIG["active_account"])
        for item in portfolio_items:
            pos = item.position
            if pos == 0:
                continue
            sym = item.contract.symbol
            mkt = float(item.marketPrice)
            try:
                action = "SELL" if pos > 0 else "BUY"
                qty = abs(int(pos))

                # CRITICAL: Portfolio contracts lack exchange='SMART' which IBKR
                # requires for order routing. Must set it explicitly.
                # Also qualifyContracts to resolve stale fields (right='0', etc.)
                contract = item.contract
                contract.exchange = "SMART"
                try:
                    eib.qualifyContracts(contract)
                except Exception:
                    pass  # Proceed with exchange='SMART' even if qualify fails

                # Use aggressive LIMIT orders — IBKR rejects MarketOrders outside RTH.
                # For SELL: limit 2% below market price (willing to sell cheap to get out)
                # For BUY (closing shorts): limit 2% above market price
                if mkt > 0:
                    if action == "SELL":
                        limit_price = round(mkt * 0.98, 2)
                    else:
                        limit_price = round(mkt * 1.02, 2)
                else:
                    # Fallback: use averageCost if marketPrice unavailable
                    avg = float(item.averageCost)
                    if action == "SELL":
                        limit_price = round(avg * 0.95, 2)
                    else:
                        limit_price = round(avg * 1.05, 2)

                order = LimitOrder(action, qty, limit_price,
                                   account=CONFIG["active_account"],
                                   tif="GTC", outsideRth=True)
                flat_trade = eib.placeOrder(contract, order)
                eib.sleep(0.5)

                # Log emergency flatten order
                log_order({
                    "order_id":   flat_trade.order.orderId,
                    "symbol":     sym,
                    "side":       action,
                    "order_type": "LMT",
                    "qty":        qty,
                    "price":      limit_price,
                    "status":     "SUBMITTED",
                    "instrument": "stock",
                    "role":       "emergency_flatten",
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                })

                log.warning(f"🚨 FLATTEN: {action} {qty} {sym} LIMIT @${limit_price:.2f} (mkt=${mkt:.2f}) outsideRth=True")
                closed += 1
            except Exception as e:
                log.error(f"🚨 FLATTEN failed for {sym}: {e}")
    except Exception as e:
        log.error(f"🚨 Could not read IBKR portfolio: {e}")

    # 3) Clear bot's internal tracker
    for symbol in list(open_trades.keys()):
        del open_trades[symbol]

    log.warning(f"🚨 FLATTEN ALL complete — {closed} limit orders placed, tracker cleared")


def close_position(ib_unused, symbol: str) -> str | None:
    """
    Close a single position by symbol IMMEDIATELY via emergency IB connection.
    Uses aggressive limit orders for after-hours compatibility.
    Also cancels any related open orders (stops, TPs) for that symbol.
    Returns a description string on success, None if position not found.

    NOTE: ib_unused param kept for API compatibility but is IGNORED.
    This function uses its own dedicated IB connection (clientId=11)
    so it can execute instantly even while a scan is running.
    """
    symbol = symbol.upper().strip()
    eib = _get_emergency_ib()
    if not eib:
        log.error(f"Close {symbol}: No emergency IB connection available")
        return None

    # 1) Find the position in IBKR portfolio
    try:
        portfolio_items = eib.portfolio(CONFIG["active_account"])
    except Exception as e:
        log.error(f"Close {symbol}: Could not read IBKR portfolio: {e}")
        return None

    target = None
    for item in portfolio_items:
        if item.contract.symbol == symbol and item.position != 0:
            target = item
            break

    if not target:
        log.warning(f"Close {symbol}: Position not found in IBKR portfolio")
        return None

    pos = target.position
    mkt = float(target.marketPrice)
    action = "SELL" if pos > 0 else "BUY"
    qty = abs(int(pos))

    # 2) Cancel related open orders for this symbol
    try:
        for t in eib.trades():
            if t.contract.symbol == symbol and t.orderStatus.status in ('Submitted', 'PreSubmitted'):
                try:
                    eib.cancelOrder(t.order)
                    log.info(f"Close {symbol}: Cancelled order {t.order.orderId}")
                except Exception:
                    pass
        eib.sleep(0.3)
    except Exception as e:
        log.warning(f"Close {symbol}: Error cancelling related orders: {e}")

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
        "symbol":     symbol,
        "side":       action,
        "order_type": "LMT",
        "qty":        qty,
        "price":      limit_price,
        "status":     "SUBMITTED",
        "instrument": "stock",
        "role":       "close",
        "reason":     "Manual close from dashboard",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    })

    detail = f"{action} {qty} {symbol} LIMIT @${limit_price:.2f} (mkt=${mkt:.2f})"
    log.warning(f"📤 INSTANT close: {detail}")

    # 4) Remove from bot tracker
    if symbol in open_trades:
        del open_trades[symbol]

    return detail


def reconcile_with_ibkr(ib: IB):
    """
    On startup or reconnect: sync bot's position tracker with IBKR reality.
    Uses ib.portfolio() which includes marketPrice and unrealizedPNL.
    IBKR is always the source of truth.
    """
    log.info("Reconciling positions with IBKR...")
    try:
        # portfolio() returns PortfolioItem with marketPrice + unrealizedPNL
        # positions() only returns avgCost — never use it for reconciliation
        portfolio_items = ib.portfolio(CONFIG["active_account"])

        ibkr_syms = {item.contract.symbol for item in portfolio_items if item.position != 0}

        # Remove from tracker if IBKR doesn't have it
        for sym in list(open_trades.keys()):
            if sym not in ibkr_syms:
                log.warning(f"Position {sym} in bot memory but not in IBKR — removing")
                del open_trades[sym]

        # Add to tracker if IBKR has it but we don't
        for item in portfolio_items:
            sym = item.contract.symbol
            if item.position == 0:
                continue
            if sym not in open_trades:
                log.info(f"Position {sym} in IBKR but not tracked — adding to tracker")
                open_trades[sym] = {
                    "symbol":    sym,
                    "entry":     round(float(item.averageCost), 4),
                    "current":   round(float(item.marketPrice), 4),
                    "qty":       int(item.position),
                    "sl":        round(float(item.averageCost) * 0.98, 2),
                    "tp":        round(float(item.averageCost) * 1.06, 2),
                    "score":     0,
                    "reasoning": "Reconciled from IBKR on startup",
                    "direction": "LONG",
                    "pnl":       round(float(item.unrealizedPNL), 2),
                    "status":    "ACTIVE",    # Confirmed in IBKR
                }
            else:
                # Update prices for existing tracked positions
                open_trades[sym]["current"] = round(float(item.marketPrice), 4)
                open_trades[sym]["pnl"]     = round(float(item.unrealizedPNL), 2)
                # Mark as ACTIVE — IBKR confirms this position exists
                open_trades[sym]["status"]  = "ACTIVE"

        log.info(f"Reconciliation complete. Tracking {len(open_trades)} positions.")

    except Exception as e:
        log.error(f"Reconciliation error: {e}")


def update_positions_from_ibkr(ib: IB):
    """
    Refresh current price and P&L for all tracked positions from IBKR portfolio data.
    Called on every scan so dashboard always shows live P&L even when no symbols score.
    IBKR is ALWAYS the source of truth — this sets the _ibkr_updated flag to prevent
    yfinance from overwriting with bad data.
    """
    try:
        portfolio_items = ib.portfolio(CONFIG["active_account"])
        price_map = {item.contract.symbol: item for item in portfolio_items if item.position != 0}

        # Clear all _ibkr_updated flags from previous scan
        for trade in open_trades.values():
            trade.pop("_ibkr_updated", None)

        for sym, trade in open_trades.items():
            if sym in price_map:
                item = price_map[sym]
                mkt_price = float(item.marketPrice)
                # IBKR returns -1 or 0 when no market data subscription
                if mkt_price > 0:
                    trade["current"] = round(mkt_price, 4)
                    trade["pnl"]     = round(float(item.unrealizedPNL), 2)
                    trade["_ibkr_updated"] = True
                else:
                    log.warning(f"IBKR returned invalid marketPrice for {sym}: {mkt_price}")
    except Exception as e:
        log.warning(f"Position price update error: {e}")


def update_position_prices(signals: list):
    """
    Fallback price update from yfinance signals — ONLY used for positions
    that weren't updated by update_positions_from_ibkr (IBKR is always source of truth).
    Includes sanity check to reject obviously wrong prices.
    """
    price_map = {s["symbol"]: s["price"] for s in signals}
    for sym, trade in open_trades.items():
        if sym in price_map:
            new_price = price_map[sym]
            # Skip if IBKR already gave us a recent price (set by update_positions_from_ibkr)
            if trade.get("_ibkr_updated"):
                continue
            # Sanity check: reject prices that deviate >30% from entry (likely bad data)
            entry = trade.get("entry", 0)
            if entry > 0 and abs(new_price - entry) / entry > 0.30:
                log.warning(f"Rejecting bad yfinance price for {sym}: ${new_price:.2f} vs entry ${entry:.2f}")
                continue
            trade["current"] = new_price
            if trade["direction"] == "LONG":
                trade["pnl"] = (trade["current"] - trade["entry"]) * trade["qty"]
            else:
                trade["pnl"] = (trade["entry"] - trade["current"]) * trade["qty"]


def get_open_positions() -> list:
    """Return list of open positions for dashboard and agent consumption."""
    return list(open_trades.values())


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

    if opt_key in open_trades:
        log.warning(f"Already holding {opt_key} — skipping")
        return False

    if len(open_trades) >= CONFIG["max_positions"]:
        log.warning(f"Max positions reached — skipping options trade {symbol}")
        return False

    n_contracts = contract_info["contracts"]
    mid_price   = contract_info["mid"]
    # Limit price slightly above mid to improve fill probability
    limit_price = round(mid_price * 1.01, 2)

    try:
        option_contract = Option(
            symbol,
            contract_info["expiry_ibkr"],
            contract_info["strike"],
            contract_info["right"],
            "SMART", "USD"
        )
        ib.qualifyContracts(option_contract)
        account = CONFIG["active_account"]

        entry_order = LimitOrder("BUY", n_contracts, limit_price,
                                 account=account, tif="DAY", outsideRth=True)
        trade = ib.placeOrder(option_contract, entry_order)
        ib.sleep(1)

        # Check if IBKR immediately rejected the order
        order_status = trade.orderStatus.status
        if order_status in ('Cancelled', 'Inactive', 'ApiCancelled', 'ValidationError'):
            log.error(f"Option order immediately rejected by IBKR for {opt_key}: {order_status}")
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

        open_trades[opt_key] = {
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
        log.error(f"Option buy failed {symbol}: {e}")
        return False


def execute_sell_option(ib: IB, opt_key: str, reason: str = "signal") -> bool:
    """
    Close an open options position at market.
    opt_key format: SYMBOL_RIGHT_STRIKE_EXPIRY  (e.g. NVDA_C_180_2026-04-01)
    Returns True if order placed.
    """
    # Options only trade during regular market hours (9:30–16:00 ET)
    if not is_options_market_open():
        now_et = datetime.now(_ET)
        log.warning(f"Options market closed ({now_et.strftime('%H:%M ET')}) — cannot sell {opt_key}")
        return False

    if opt_key not in open_trades:
        log.warning(f"No open options position {opt_key}")
        return False

    pos = open_trades[opt_key]
    if pos.get("instrument") != "option":
        log.warning(f"{opt_key} is not an options position")
        return False

    try:
        option_contract = Option(
            pos["symbol"],
            pos["expiry_ibkr"],
            pos["strike"],
            pos["right"],
            "SMART", "USD"
        )
        ib.qualifyContracts(option_contract)
        sell_order = MarketOrder("SELL", pos["contracts"],
                                 account=CONFIG["active_account"])
        sell_order.outsideRth = True
        opt_sell_trade = ib.placeOrder(option_contract, sell_order)
        ib.sleep(1)

        # Log the option sell order
        log_order({
            "order_id":   opt_sell_trade.order.orderId,
            "symbol":     pos["symbol"],
            "side":       "SELL",
            "order_type": "MKT",
            "qty":        pos["contracts"],
            "price":      pos.get("current_premium", 0),
            "status":     "SUBMITTED",
            "instrument": "option",
            "right":      pos["right"],
            "strike":     pos["strike"],
            "expiry":     pos["expiry_str"],
            "role":       "close",
            "reason":     reason,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        entry   = pos["entry_premium"]
        current = pos.get("current_premium", entry)
        pnl     = (current - entry) * pos["contracts"] * 100

        if pnl >= 0:
            record_win()
        else:
            record_loss()

        log.info(
            f"{'✅' if pnl >= 0 else '❌'} SELL {pos['right']} {pos['symbol']} "
            f"${pos['strike']:.0f} | P&L ${pnl:+.2f} | {reason}"
        )
        del open_trades[opt_key]
        return True

    except Exception as e:
        log.error(f"Option sell failed {opt_key}: {e}")
        return False
