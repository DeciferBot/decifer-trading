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
    _get_ibkr_price, _get_ibkr_bid_ask, _get_yf_price,
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


def execute_buy(ib: IB, symbol: str, price: float, atr: float,
                score: int, portfolio_value: float, regime: dict,
                reasoning: str = "",
                signal_scores: dict = None,
                agent_outputs: dict = None,
                open_time: str = None,
                candle_gate: str = None,
                tranche_mode: bool = True) -> bool:
    """
    Place a buy order with full OCO bracket.
    Entry: Limit order at IBKR real-time price (yfinance price is only a fallback)
    Stop loss: Stop order (placed immediately)
    Take profit: Limit order (placed immediately)
    Returns True if order placed successfully.
    """
    # Rebind patchable names from the current sys.modules entry so that
    # @patch('orders.*') works even when this module object differs from
    # sys.modules['orders'] (can happen during pytest collection cycles).
    import sys as _sys; _om = _sys.modules.get('orders', _sys.modules[__name__])
    CONFIG = _om.CONFIG                                      # noqa: F841
    check_correlation = _om.check_correlation                # noqa: F841
    check_combined_exposure = _om.check_combined_exposure    # noqa: F841
    check_sector_concentration = _om.check_sector_concentration  # noqa: F841
    calculate_position_size = _om.calculate_position_size    # noqa: F841
    calculate_stops = _om.calculate_stops                    # noqa: F841
    log_order = _om.log_order                                # noqa: F841
    get_tv_signal_cache = _om.get_tv_signal_cache            # noqa: F841
    _get_yf_price = _om._get_yf_price                        # noqa: F841
    MarketOrder = _om.MarketOrder                            # noqa: F841
    LimitOrder  = _om.LimitOrder                             # noqa: F841
    StopOrder   = _om.StopOrder                              # noqa: F841

    # ── Guard: per-symbol lock closes TOCTOU gap between check and submission ──
    sym_lock = _get_symbol_lock(symbol)
    with sym_lock:
        # ── Guard: check active_trades under lock (prop-003/014) ──────────
        with _trades_lock:
            if symbol in active_trades:
                if active_trades[symbol].get("status") == "EXITING":
                    log.info(f"Skipping {symbol} — exit in flight")
                    return False
                log.warning(f"Already in {symbol} — skipping buy")
                return False
            if _is_recently_closed(symbol):
                cooldown = CONFIG.get("reentry_cooldown_minutes", 30)
                log.info(f"Skipping {symbol} — re-entry cooldown ({cooldown} min after recent close)")
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
            # Estimate new position value for the exposure check.
            # Use max_single_position (10%) as the upper bound — the actual
            # order is capped to 20% of portfolio downstream, so 10% is
            # a safe conservative estimate. The previous formula (risk_pct * 50)
            # produced 1.5× portfolio, which permanently broke this check.
            est_value = portfolio_value * CONFIG.get("max_single_position", 0.10)
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
        ibkr_bid, ibkr_ask = _get_ibkr_bid_ask(ib, contract)

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
        qty = calculate_position_size(portfolio_value, price, score, regime, atr=atr)

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

        # ── Execution Agent — decide HOW to fill this trade ──────────────────
        from execution_agent import get_execution_plan

        _et_now    = datetime.now(zoneinfo.ZoneInfo("America/New_York")).strftime("%H:%M")
        _tv_vol    = float(tv_data.get("tv_rel_vol") or 1.0) if tv_data else 1.0
        _tv_vwap   = float(tv_data.get("tv_vwap") or 0)      if tv_data else 0.0
        _vwap_dist = ((price - _tv_vwap) / _tv_vwap * 100)   if _tv_vwap > 0 else 0.0
        _spread    = ((ibkr_ask - ibkr_bid) / ibkr_ask * 100) if ibkr_ask > 0 else 0.0

        exec_plan = get_execution_plan(
            symbol=symbol, direction="LONG", size=qty,
            conviction_score=score, bid=ibkr_bid, ask=ibkr_ask,
            spread_pct=_spread, rel_volume=_tv_vol,
            vwap_dist_pct=_vwap_dist, time_of_day_str=_et_now,
            regime_name=regime.get("regime", "UNKNOWN"),
        )

        # ── ATOMIC BRACKET ORDER ──────────────────────────────────
        # All 3 legs (entry + SL + TP) are submitted as one atomic bracket.
        # Parent transmit=False prevents it from filling before children are attached.
        # The final child has transmit=True which transmits the entire group together.
        # This prevents the "parent already filled" rejection that kills child orders.
        limit_price = round(price * 1.002, 2)

        # Leg 1: Entry (parent) — order type chosen by execution agent
        if exec_plan.order_type == "MKT":
            entry_order = MarketOrder("BUY", qty, account=account, tif="DAY", outsideRth=True)
        elif exec_plan.order_type == "MIDPOINT":
            _midprice = round((ibkr_bid + ibkr_ask) / 2, 2) if ibkr_bid > 0 and ibkr_ask > 0 else limit_price
            entry_order = LimitOrder("BUY", qty, _midprice,
                                     account=account, tif="DAY", outsideRth=True)
        else:  # "LIMIT" (default)
            _effective_limit = exec_plan.limit_price if exec_plan.limit_price > 0 else limit_price
            entry_order = LimitOrder("BUY", qty, _effective_limit,
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
            "score":       score,
            "reasoning":   reasoning,
            "candle_gate": candle_gate or "UNKNOWN",
            "timestamp":   datetime.now(timezone.utc).isoformat(),
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

        _child_reject = ('Inactive', 'Cancelled', 'ApiCancelled', 'ValidationError')
        if sl_status in _child_reject or tp_status in _child_reject:
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
                sl2_status = sl_trade2.orderStatus.status
                if sl2_status in _child_reject:
                    log.error(
                        f"CRITICAL: Standalone SL also rejected for {symbol} "
                        f"({sl2_status}) — position has NO stop loss, sl_order_id unchanged"
                    )
                else:
                    _sl_order_id = sl_trade2.order.orderId  # update to standalone order
                    log.info(f"Standalone SL placed for {symbol} @ ${sl:.2f} OCA={oca_group} (orderId={_sl_order_id})")
                log_order({
                    "order_id":   _sl_order_id,
                    "parent_id":  parent_id,
                    "symbol":     symbol,
                    "side":       "SELL",
                    "order_type": "STP",
                    "qty":        qty,
                    "price":      sl,
                    "status":     "SUBMITTED",
                    "instrument": "stock",
                    "role":       "stop_loss_standalone",
                    "oca_group":  oca_group,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                })
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
                log_order({
                    "order_id":   tp_trade2.order.orderId,
                    "parent_id":  parent_id,
                    "symbol":     symbol,
                    "side":       "SELL",
                    "order_type": "LMT",
                    "qty":        tp_qty,
                    "price":      tp,
                    "status":     "SUBMITTED",
                    "instrument": "stock",
                    "role":       "take_profit_standalone",
                    "oca_group":  oca_group,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                })
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
            try:
                from ic_calculator import get_current_weights as _get_icw
                _icw_at_entry = _get_icw()
            except Exception:
                _icw_at_entry = None
            with _trades_lock:
                active_trades[symbol] = {
                    "symbol":              symbol,
                    "instrument":          "stock",
                    "entry":               price,
                    "current":             price,
                    "qty":                 qty,
                    "sl":                  sl,
                    "tp":                  tp,
                    "score":               score,
                    "entry_score":         score,   # immutable snapshot for portfolio manager
                    "reasoning":           reasoning,
                    "direction":           "LONG",
                    "pnl":                 0.0,
                    "status":              "PENDING",   # Submitted to IBKR but not yet filled
                    "order_id":            parent_id,
                    "open_time":           _open_time,
                    "signal_scores":       signal_scores or {},
                    "ic_weights_at_entry": _icw_at_entry,
                    "agent_outputs":       agent_outputs or {},
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
                    watcher_params=exec_plan.fill_watcher_params,
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


def execute_short(ib: IB, symbol: str, price: float, atr: float,
                  score: int, portfolio_value: float, regime: dict,
                  reasoning: str = "",
                  signal_scores: dict = None,
                  agent_outputs: dict = None,
                  open_time: str = None,
                  candle_gate: str = None) -> bool:
    """
    Place a short-sell order with OCO bracket (sell-to-open + buy-to-cover SL + TP).
    Entry: Limit order at IBKR real-time price.
    Stop loss: Stop order ABOVE entry (buy to cover if price rises).
    Take profit: Limit order BELOW entry (buy to cover when price falls).
    Returns True if order placed successfully.
    """
    import sys as _sys; _om = _sys.modules.get('orders', _sys.modules[__name__])
    CONFIG = _om.CONFIG                                      # noqa: F841
    check_correlation = _om.check_correlation                # noqa: F841
    check_combined_exposure = _om.check_combined_exposure    # noqa: F841
    check_sector_concentration = _om.check_sector_concentration  # noqa: F841
    calculate_position_size = _om.calculate_position_size    # noqa: F841
    calculate_stops = _om.calculate_stops                    # noqa: F841
    log_order = _om.log_order                                # noqa: F841
    get_tv_signal_cache = _om.get_tv_signal_cache            # noqa: F841
    _get_yf_price = _om._get_yf_price                        # noqa: F841
    MarketOrder = _om.MarketOrder                            # noqa: F841
    LimitOrder  = _om.LimitOrder                             # noqa: F841
    StopOrder   = _om.StopOrder                              # noqa: F841

    sym_lock = _get_symbol_lock(symbol)
    with sym_lock:
        with _trades_lock:
            if symbol in active_trades:
                if active_trades[symbol].get("status") == "EXITING":
                    log.info(f"Skipping {symbol} — exit in flight")
                    return False
                log.warning(f"Already in {symbol} — skipping short")
                return False
            if _is_recently_closed(symbol):
                cooldown = CONFIG.get("reentry_cooldown_minutes", 30)
                log.info(f"Skipping {symbol} — re-entry cooldown ({cooldown} min after recent close)")
                return False
            if len(active_trades) >= CONFIG["max_positions"]:
                log.warning(f"Max positions ({CONFIG['max_positions']}) reached — skipping {symbol}")
                return False
            ok, reason = check_correlation(symbol, list(active_trades.values()))
            if not ok:
                log.warning(f"Correlation block for {symbol}: {reason}")
                return False
            est_value = portfolio_value * CONFIG.get("max_single_position", 0.10)
            exp_ok, exp_reason = check_combined_exposure(
                symbol, est_value, list(active_trades.values()),
                portfolio_value, instrument="stock"
            )
            if not exp_ok:
                log.warning(f"Combined exposure block for {symbol}: {exp_reason}")
                return False
            sec_ok, sec_reason = check_sector_concentration(
                symbol, list(active_trades.values()),
                portfolio_value, regime.get("regime", "NORMAL")
            )
            if not sec_ok:
                log.warning(f"Sector block for {symbol}: {sec_reason}")
                return False
            active_trades[symbol] = {"status": "RESERVED", "symbol": symbol}

        if _is_duplicate_check_enabled():
            if has_open_order_for(symbol) or _check_ibkr_open_order(ib, symbol, side="SELL"):
                log.warning(f"Skipping duplicate short order for {symbol} — open order already exists")
                _safe_del_trade(symbol)
                return False

    try:
        contract = get_contract(symbol)
        ib.qualifyContracts(contract)

        yf_price = price
        ibkr_price = _get_ibkr_price(ib, contract, fallback=0)
        ibkr_bid, ibkr_ask = _get_ibkr_bid_ask(ib, contract)

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
            log.error(f"No price data for {symbol} — aborting short")
            _safe_del_trade(symbol)
            return False

        price_vals = list(prices.values())
        for i in range(len(price_vals)):
            for j in range(i + 1, len(price_vals)):
                div = abs(price_vals[i] - price_vals[j]) / max(price_vals[i], price_vals[j])
                if div > 0.50:
                    log.error(f"PRICE CONTAMINATION {symbol}: {prices} — aborting short")
                    _safe_del_trade(symbol)
                    return False

        # For shorts, use the LOWEST confirmed price (best short entry)
        best_price = min(price_vals)
        price = best_price

        if price < 1.0 or price > 10000:
            log.error(f"Price out of range for short {symbol}: ${price:.2f} — aborting")
            _safe_del_trade(symbol)
            return False

        qty = calculate_position_size(portfolio_value, price, score, regime, atr=atr)
        MAX_SHARES = 5000
        if qty > MAX_SHARES:
            qty = MAX_SHARES
        max_order_value = portfolio_value * 0.20
        if qty * price > max_order_value:
            qty = max(1, int(max_order_value / price))

        sl, tp = calculate_stops(price, atr, "SHORT")  # sl > price, tp < price

        reward = price - tp
        risk   = sl - price
        if risk <= 0 or (reward / risk) < CONFIG["min_reward_risk_ratio"]:
            log.warning(f"Poor R:R on short {symbol}: reward={reward:.2f} risk={risk:.2f} — skipping")
            _safe_del_trade(symbol)
            return False

        account = CONFIG["active_account"]
        # Sell slightly below bid to improve fill probability
        limit_price = round(price * 0.998, 2)

        # Entry: sell short
        entry_order = LimitOrder("SELL", qty, limit_price,
                                 account=account, tif="DAY", outsideRth=True)
        entry_order.transmit = False
        trade = ib.placeOrder(contract, entry_order)
        ib.sleep(0.2)

        parent_id = trade.order.orderId

        # Stop loss: buy to cover if price rises
        sl_order = StopOrder("BUY", qty, sl, account=account, tif="GTC", outsideRth=True)
        sl_order.parentId = parent_id
        sl_order.transmit = False
        sl_trade = ib.placeOrder(contract, sl_order)
        ib.sleep(0.1)
        _sl_order_id = sl_trade.order.orderId

        # Take profit: buy to cover when price falls to target
        tp_order = LimitOrder("BUY", qty, tp, account=account, tif="GTC", outsideRth=True)
        tp_order.parentId = parent_id
        tp_order.transmit = True
        tp_trade = ib.placeOrder(contract, tp_order)

        ib.sleep(1.5)

        order_status = trade.orderStatus.status
        if order_status in ('Cancelled', 'Inactive', 'ApiCancelled', 'ValidationError'):
            log.error(f"Short entry immediately rejected by IBKR for {symbol}: {order_status}")
            _safe_del_trade(symbol)
            return False

        log_order({
            "order_id":   parent_id,
            "symbol":     symbol,
            "side":       "SELL",
            "order_type": "LMT",
            "qty":        qty,
            "price":      limit_price,
            "status":     "SUBMITTED",
            "instrument": "stock",
            "direction":  "SHORT",
            "sl":         sl,
            "tp":         tp,
            "score":       score,
            "reasoning":   reasoning,
            "candle_gate": candle_gate or "UNKNOWN",
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        })
        log_order({
            "order_id":  sl_trade.order.orderId,
            "parent_id": parent_id,
            "symbol":    symbol,
            "side":      "BUY",
            "order_type": "STP",
            "qty":       qty,
            "price":     sl,
            "status":    "SUBMITTED",
            "instrument": "stock",
            "role":      "stop_loss",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        log_order({
            "order_id":  tp_trade.order.orderId,
            "parent_id": parent_id,
            "symbol":    symbol,
            "side":      "BUY",
            "order_type": "LMT",
            "qty":       qty,
            "price":     tp,
            "status":    "SUBMITTED",
            "instrument": "stock",
            "role":      "take_profit",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        try:
            _open_time = open_time or datetime.now(timezone.utc).isoformat()
            try:
                from ic_calculator import get_current_weights as _get_icw
                _icw_at_entry = _get_icw()
            except Exception:
                _icw_at_entry = None
            with _trades_lock:
                active_trades[symbol] = {
                    "symbol":              symbol,
                    "instrument":          "stock",
                    "entry":               price,
                    "current":             price,
                    "qty":                 qty,
                    "sl":                  sl,
                    "tp":                  tp,
                    "score":               score,
                    "entry_score":         score,
                    "reasoning":           reasoning,
                    "direction":           "SHORT",
                    "pnl":                 0.0,
                    "status":              "PENDING",
                    "order_id":            parent_id,
                    "open_time":           _open_time,
                    "signal_scores":       signal_scores or {},
                    "ic_weights_at_entry": _icw_at_entry,
                    "agent_outputs":       agent_outputs or {},
                    "atr":                 atr,
                    "sl_order_id":         _sl_order_id,
                    "high_water_mark":     price,
                    "tranche_mode":        False,
                }
            from learning import log_trade
            log_trade(
                trade=active_trades[symbol],
                agent_outputs=agent_outputs or {},
                regime=regime,
                action="OPEN",
            )
        except Exception as record_err:
            log.error(f"GHOST POSITION RISK {symbol}: short order submitted (id={parent_id}) but "
                       f"failed to record: {record_err}")
            raise

        _rr = (price - tp) / (sl - price) if (sl - price) > 0 else 0
        log.info(f"✅ SHORT {symbol} qty={qty} @ ${price:.2f} | SL=${sl:.2f} TP=${tp:.2f} | R:R={_rr:.1f}")
        return True

    except Exception as e:
        _safe_del_trade(symbol)
        log.error(f"Short failed {symbol}: {e}")
        return False


def execute_sell(ib: IB, symbol: str, reason: str = "Agent signal", qty_override: int = None) -> bool:
    """
    Close an existing position at market.
    Returns True if order placed.
    """
    # Rebind patchable names from the current sys.modules entry so that
    # @patch('orders.*') works even when this module object differs from
    # sys.modules['orders'] (can happen during pytest collection cycles).
    import sys as _sys; _om = _sys.modules.get('orders', _sys.modules[__name__])
    CONFIG = _om.CONFIG                                          # noqa: F841
    _validate_position_price = _om._validate_position_price      # noqa: F841
    _get_ibkr_price = _om._get_ibkr_price                        # noqa: F841
    log_order = _om.log_order                                    # noqa: F841
    record_win = _om.record_win                                  # noqa: F841
    record_loss = _om.record_loss                                # noqa: F841

    with _trades_lock:
        if symbol in active_trades:
            _trade_key = symbol
        else:
            # Options positions are stored under composite keys (e.g. "GSAT_C_35.0_2026-04-17").
            # Search by the "symbol" field so execute_sell("GSAT") finds them.
            _matches = [k for k, v in active_trades.items() if v.get("symbol") == symbol]
            if not _matches:
                log.warning(f"No open position in {symbol} — skipping sell")
                return False
            _trade_key = _matches[0]
            if len(_matches) > 1:
                log.warning(f"Multiple {symbol} positions: {_matches} — closing {_trade_key}")
        info = active_trades[_trade_key]
        if info.get("status") == "EXITING":
            log.info(f"Exit already in flight for {symbol} ({_trade_key}) — skipping duplicate")
            return False
        _safe_update_trade(_trade_key, {"status": "EXITING"})

    _is_partial = qty_override is not None and qty_override < info["qty"]
    sell_qty = qty_override if _is_partial else info["qty"]

    # Stop any active fill watcher so it doesn't race the sell
    from fill_watcher import stop_watcher as _stop_watcher
    _stop_watcher(symbol)

    try:
        if info.get("instrument") == "option":
            from ib_async import Option as _OptContract
            contract = _OptContract(symbol, info["expiry_ibkr"], info["strike"], info["right"],
                                    exchange="SMART", currency="USD")
        else:
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
        close_order = MarketOrder(close_action, sell_qty, account=CONFIG["active_account"])
        close_order.outsideRth = True
        sell_trade = ib.placeOrder(contract, close_order)
        ib.sleep(1)

        # Log the close order
        log_order({
            "order_id":   sell_trade.order.orderId,
            "symbol":     symbol,
            "side":       close_action,
            "order_type": "MKT",
            "qty":        sell_qty,
            "price":      info["current"],
            "status":     "SUBMITTED",
            "instrument": "stock",
            "role":       "close",
            "reason":     reason,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        if direction == "SHORT":
            pnl = (info["entry"] - info["current"]) * sell_qty  # SHORT profits when price drops
        else:
            pnl = (info["current"] - info["entry"]) * sell_qty
        if pnl >= 0:
            record_win()
        else:
            record_loss()

        log.info(f"{'✅' if pnl >= 0 else '❌'} CLOSE {direction} {symbol} ({close_action}) | P&L ${pnl:+.2f} | Reason: {reason}")
        if _is_partial:
            _safe_update_trade(_trade_key, {"qty": info["qty"] - sell_qty, "status": "ACTIVE"})
            log.info(f"[TRIM] {symbol}: sold {sell_qty}, {info['qty'] - sell_qty} remaining")
        else:
            with _trades_lock:
                recently_closed[symbol] = datetime.now(timezone.utc).isoformat()
                del active_trades[_trade_key]
        return True

    except Exception as e:
        log.error(f"Sell failed {symbol}: {e}")
        _safe_update_trade(_trade_key, {"status": "ACTIVE"})
        try:
            from learning import _append_audit_event
            _append_audit_event(
                "sell_exception",
                symbol=symbol,
                error=str(e),
                reason=reason,
                note="execute_sell raised an exception — position may still be open in IBKR.",
            )
        except Exception:
            pass
        return False


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

    # Price at the ask to ensure fill. Options have wide bid-ask spreads (5-25%);
    # pricing at mid means we sit below the ask and never fill (74% cancel rate in data).
    # The alpha is in getting the position on, not saving $0.05-0.10 on entry.
    ask_price = contract_info.get("ask", 0.0)
    if ask_price > mid_price > 0:
        limit_price = round(ask_price, 2)        # at-ask: fills reliably
    else:
        limit_price = round(mid_price * 1.05, 2)  # fallback: 5% above mid

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
            "mid":        mid_price,
            "ask":        ask_price,
            "spread_pct": contract_info.get("spread_pct"),
            "reasoning":  reasoning,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

        try:
            from ic_calculator import get_current_weights as _get_icw_opt
            _icw_at_entry_opt = _get_icw_opt()
        except Exception:
            _icw_at_entry_opt = None
        active_trades[opt_key] = {
            "symbol":              symbol,
            "instrument":          "option",
            "right":               contract_info["right"],
            "strike":              contract_info["strike"],
            "expiry_str":          contract_info["expiry_str"],
            "expiry_ibkr":         contract_info["expiry_ibkr"],
            "dte":                 contract_info["dte"],
            "contracts":           n_contracts,
            "entry_premium":       mid_price,
            "current_premium":     mid_price,
            "entry":               mid_price,          # unified field for dashboard
            "current":             mid_price,
            "qty":                 n_contracts,
            "sl":                  round(mid_price * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
            "tp":                  round(mid_price * (1 + CONFIG.get("options_profit_target", 0.75)), 4),
            "delta":               contract_info.get("delta"),
            "theta":               contract_info.get("theta"),
            "iv":                  contract_info.get("iv"),
            "iv_rank":             contract_info.get("iv_rank"),
            "underlying_price":    contract_info.get("underlying_price"),
            "pnl":                 0.0,
            "score":               0,
            "direction":           "LONG",
            "reasoning":           reasoning,
            "status":              "PENDING",
            "order_id":            trade.order.orderId,
            "ic_weights_at_entry": _icw_at_entry_opt,
        }

        log.info(
            f"✅ BUY {contract_info['right']} {symbol} "
            f"${contract_info['strike']:.0f} exp={contract_info['expiry_str']} "
            f"x{n_contracts} @ ${limit_price:.2f} (ask=${ask_price:.2f} mid=${mid_price:.2f}) "
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

# Exits requested while the options market was closed — flushed on next open cycle
_pending_option_exits: dict = {}   # opt_key → original reason string


def execute_sell_option(ib: IB, opt_key: str, reason: str = "signal", contracts_override: int = None) -> bool:
    """
    Close an open options position using a limit order at the current bid.
    IBKR rejects MKT and midpoint LMT orders on illiquid/falling options, so we
    always start at the bid price. On repeated failures we step 5% below bid per
    retry to chase the market (bid → bid*0.95 → bid*0.90 ...).
    opt_key format: SYMBOL_RIGHT_STRIKE_EXPIRY  (e.g. NVDA_C_180_2026-04-01)
    Returns True if order filled.
    """
    # Options only trade during regular market hours (9:30–16:00 ET)
    if not is_options_market_open():
        now_et = datetime.now(_ET)
        log.warning(
            f"Options market closed ({now_et.strftime('%H:%M ET')}) — "
            f"deferring exit for {opt_key} until next open"
        )
        _pending_option_exits[opt_key] = reason
        return False

    if opt_key not in active_trades:
        log.warning(f"No open options position {opt_key}")
        return False

    pos = active_trades[opt_key]
    if pos.get("instrument") != "option":
        log.warning(f"{opt_key} is not an options position")
        return False

    if pos.get("status") == "EXITING":
        log.info(f"Exit already in flight for {opt_key} — skipping duplicate")
        return False

    _is_partial = contracts_override is not None and contracts_override < pos["contracts"]
    sell_contracts = contracts_override if _is_partial else pos["contracts"]

    # ── Retry gating: don't spam IBKR with the same failing order ──
    # Check BEFORE setting EXITING so we don't lock the position on a cooldown skip
    attempts = _option_sell_attempts.get(opt_key, {"count": 0, "last_try": datetime.min})
    if attempts["count"] >= _MAX_OPTION_SELL_RETRIES:
        elapsed = (datetime.now(timezone.utc) - attempts["last_try"]).total_seconds()
        if elapsed < _OPTION_SELL_COOLDOWN:
            log.warning(f"Option sell for {opt_key} failed {attempts['count']}x — "
                        f"cooling down ({int(_OPTION_SELL_COOLDOWN - elapsed)}s remaining)")
            from learning import _append_audit_event
            _append_audit_event(
                "option_sell_stuck",
                opt_key=opt_key,
                symbol=pos.get("symbol"),
                attempts=attempts["count"],
                cooldown_remaining_s=int(_OPTION_SELL_COOLDOWN - elapsed),
                reason=reason,
                note="Position stuck — max retries hit. Manual review required.",
            )
            return False  # status is still ACTIVE — not stuck

        # Cooldown expired — reset to 1 (not 0) so we stay on the bid path
        # rather than falling back to a midpoint-style first attempt
        attempts["count"] = 1

    _safe_update_trade(opt_key, {"status": "EXITING"})

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

        # Determine order direction: SHORT positions close with BUY, LONG with SELL
        _is_short = pos.get("direction", "LONG").upper() == "SHORT"
        _close_action = "BUY" if _is_short else "SELL"

        # Determine limit price.
        # SELL-to-close (long position): price at bid and step down aggressively.
        # BUY-to-close (short position): price at ask and step up aggressively to ensure fill.
        import math as _m
        _bid_ok = bid is not None and not _m.isnan(bid) and bid > 0
        _ask_ok = ask is not None and not _m.isnan(ask) and ask > 0
        _retry_count = attempts["count"]  # 0 on first attempt, 1+ on retries
        _step = _retry_count
        if _is_short:
            # BUY-to-close: offer at ask, step 5% ABOVE ask each retry to chase fills
            _premium = round(1.0 + (_step * 0.05), 2)  # 1.00, 1.05, 1.10 ...
            if _ask_ok:
                limit_price = round(ask * _premium, 2)
            elif last and not _m.isnan(last) and last > 0:
                limit_price = round(last * 1.03 * _premium, 2)
            else:
                limit_price = round(pos.get("current_premium", 0.10) * 1.10, 2)
        else:
            # SELL-to-close (long position): price at bid and step down aggressively.
            _discount = round(1.0 - (_step * 0.05), 2)  # 1.00, 0.95, 0.90 ...
            if _bid_ok:
                limit_price = round(bid * _discount, 2)
            elif last and not _m.isnan(last) and last > 0:
                limit_price = round(last * 0.97 * _discount, 2)
            else:
                limit_price = round(pos.get("current_premium", 0.01) * 0.95, 2)

            # On final attempt, if bid is near-zero accept $0.01 for near-worthless options
            if _retry_count >= _MAX_OPTION_SELL_RETRIES - 1 and _bid_ok and bid <= 0.02:
                limit_price = 0.01
            # Floor at $0.05 — IBKR minimum tick for US equity options (below $3)
            # Do NOT apply floor when bid is itself below $0.05 (near-worthless option)
            if not (_bid_ok and bid < 0.05):
                limit_price = max(limit_price, 0.05)

        ib.cancelMktData(option_contract)

        sell_order = LimitOrder(_close_action, sell_contracts, limit_price,
                                account=CONFIG["active_account"],
                                tif="DAY")
        sell_order.outsideRth = False
        opt_sell_trade = ib.placeOrder(option_contract, sell_order)

        log.info(f"Option LMT {_close_action} placed: {opt_key} x{sell_contracts} @ ${limit_price:.2f} "
                 f"(bid={bid}, ask={ask}, direction={pos.get('direction', 'LONG')})")

        # Wait for fill confirmation — options can take a moment
        # On retries, use 25s to give the more-aggressive price more time to fill
        max_wait = 15 if attempts["count"] == 0 else 25  # seconds
        for _ in range(max_wait * 2):
            ib.sleep(0.5)
            status = opt_sell_trade.orderStatus.status
            if status in ("Filled", "Cancelled", "Inactive", "ApiCancelled"):
                break

        order_status = opt_sell_trade.orderStatus.status
        if order_status != "Filled":
            # Handle partial fills: IBKR reports "Cancelled" even when some contracts filled
            filled_qty = int(opt_sell_trade.orderStatus.filled or 0)
            if filled_qty > 0:
                remaining = pos["contracts"] - filled_qty
                log.info(f"[PARTIAL FILL] {opt_key}: {filled_qty} contracts filled, {remaining} remaining")
                if remaining <= 0:
                    # All contracts filled despite non-Filled status (paper account quirk)
                    _option_sell_attempts.pop(opt_key, None)
                    del active_trades[opt_key]
                    log.info(f"[PARTIAL→FULL] {opt_key} fully closed via partial fills")
                    return True
                _safe_update_trade(opt_key, {"contracts": remaining})

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
            # Reset EXITING status so the next retry attempt can proceed
            _safe_update_trade(opt_key, {"status": "ACTIVE"})
            return False

        # Guard against paper-account false fills: status can briefly show "Filled"
        # before settling as "Cancelled", but avgFillPrice stays 0 in that case.
        # A real fill always has avgFillPrice > 0.
        fill_price = opt_sell_trade.orderStatus.avgFillPrice
        if not fill_price or fill_price <= 0:
            log.warning(
                f"Option sell {opt_key}: status=Filled but avgFillPrice=0 — "
                f"treating as failed (paper account false positive)."
            )
            attempts["count"] += 1
            attempts["last_try"] = datetime.now(timezone.utc)
            _option_sell_attempts[opt_key] = attempts
            try:
                ib.cancelOrder(opt_sell_trade.order)
            except Exception:
                pass
            _safe_update_trade(opt_key, {"status": "ACTIVE"})
            return False

        # Success — clear retry counter
        _option_sell_attempts.pop(opt_key, None)

        # Log the option close order
        log_order({
            "order_id":   opt_sell_trade.order.orderId,
            "symbol":     pos["symbol"],
            "side":       _close_action,
            "order_type": "LMT",
            "qty":        sell_contracts,
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
        # Direction-aware P&L: SHORT profits when price falls (BUY-to-close at lower price)
        if _is_short:
            pnl = (entry - current) * sell_contracts * 100
        else:
            pnl = (current - entry) * sell_contracts * 100

        # ── Check commission report for IBKR realizedPNL (most accurate) ──
        try:
            import math as _math
            _fills = ib.fills()
            _close_sides = ("SLD", "SELL") if _close_action == "SELL" else ("BOT", "BUY")
            opt_sell_fills = [
                f for f in _fills
                if f.contract.symbol == pos["symbol"]
                and f.execution.side.upper() in _close_sides
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
            f"{'✅' if pnl >= 0 else '❌'} {_close_action} {pos['right']} {pos['symbol']} "
            f"${pos['strike']:.0f} | P&L ${pnl:+.2f} | {reason}"
        )
        if _is_partial:
            remaining_c = pos["contracts"] - sell_contracts
            _safe_update_trade(opt_key, {"contracts": remaining_c, "qty": remaining_c, "status": "ACTIVE"})
            log.info(f"[TRIM] {opt_key}: sold {sell_contracts} contracts, {remaining_c} remaining")
        else:
            recently_closed[pos["symbol"]] = datetime.now(timezone.utc).isoformat()
            del active_trades[opt_key]
        return True

    except Exception as e:
        log.error(f"Option sell failed {opt_key}: {e}")
        # Count exception failures so the retry/cooldown system still gates them
        _exc_att = _option_sell_attempts.get(opt_key, {"count": 0, "last_try": datetime.now(timezone.utc)})
        _exc_att["count"] += 1
        _exc_att["last_try"] = datetime.now(timezone.utc)
        _option_sell_attempts[opt_key] = _exc_att
        _safe_update_trade(opt_key, {"status": "ACTIVE"})
        return False


def flush_pending_option_exits(ib: IB) -> None:
    """
    Execute any option exits that were deferred because the market was closed.
    Called at the top of each scan cycle — safe to call always, no-ops if nothing pending.
    """
    if not _pending_option_exits or not is_options_market_open():
        return
    for opt_key, reason in list(_pending_option_exits.items()):
        if opt_key not in active_trades:
            log.info(f"Deferred exit {opt_key} dropped — position no longer tracked")
            _pending_option_exits.pop(opt_key, None)
            continue
        log.info(f"Flushing deferred option exit: {opt_key} (original reason: {reason})")
        _pending_option_exits.pop(opt_key, None)
        execute_sell_option(ib, opt_key, reason=f"deferred:{reason}")


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
