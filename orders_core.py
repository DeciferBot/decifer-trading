# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders_core.py                             ║
# ║   Core order execution — buy, short, sell                    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Core execution functions: execute_buy, execute_short, execute_sell.

All three functions use the rebinding pattern at their start:
    _om = sys.modules.get('orders', sys.modules[__name__])
which means @patch('orders.*') test patches are honoured
even though the functions now live in this module.
Imports from orders_state (shared state), orders_guards (duplicate
checks), and orders_contracts (price/contract utilities).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone, time as dtime
from typing import Optional, Tuple
import zoneinfo

from ib_async import IB, Stock, Forex, Option, Future
from ib_async import LimitOrder, StopOrder, StopLimitOrder, MarketOrder

from config import CONFIG
from risk import (calculate_position_size, calculate_stops, check_correlation,
                  record_win, record_loss, check_combined_exposure,
                  check_sector_concentration)
from learning import log_order
from scanner import get_tv_signal_cache

from orders_state import (
    log,
    active_trades, recently_closed, open_orders,
    _trades_lock,
    _get_symbol_lock,
    _safe_set_trade, _safe_update_trade, _safe_del_trade,
    _is_recently_closed,
    _save_positions_file,
)
from orders_guards import (
    _is_duplicate_check_enabled,
    has_open_order_for,
    _check_ibkr_open_order,
)
from orders_contracts import (
    get_contract,
    _get_ibkr_price, _get_ibkr_bid_ask, _get_alpaca_price,
    _is_option_contract,
    _validate_position_price,
    is_equities_extended_hours,
)


def _derive_setup_type(signal_scores: dict) -> str:
    """Return the dominant signal dimension name from score breakdown."""
    if not signal_scores:
        return "unknown"
    try:
        best = max(signal_scores, key=lambda k: float(signal_scores.get(k, 0)))
        return best.lower().replace(" ", "_")
    except Exception:
        return "unknown"


def _build_entry_thesis(
    trade_type: str,
    symbol: str,
    direction: str,
    conviction: float,
    score: int,
    entry_regime: str,
    market_read: str = "",
    rationale: str = "",
) -> str:
    """
    Build a falsifiable entry thesis string for a new position.
    Records what would prove the thesis wrong — not a price level, a condition.
    Incorporates per-trade Opus reasoning (market_read) when available so each
    thesis describes THIS specific setup, not just the trade_type category.
    """
    pm = CONFIG.get("portfolio_manager", {})
    scalp_mins = pm.get("scalp_max_hold_minutes", 90)
    scalp_pnl  = pm.get("scalp_min_pnl_pct", 0.003) * 100

    tt = (trade_type or "SCALP").upper()
    if tt == "SCALP":
        condition = f"momentum does not produce >{scalp_pnl:.1f}% move within {scalp_mins}min"
    elif tt == "SWING":
        condition = "regime shifts against entry direction"
    elif tt == "HOLD":
        condition = "macro polarity flips (BULL/BEAR) against entry"
    else:
        condition = "score collapses or regime contradicts entry direction"

    # Use Opus market_read if available; fall back to agent synthesis rationale
    setup_context = (market_read or rationale or "").strip()
    setup_tag = f" | setup: {setup_context[:150]}" if len(setup_context) > 10 else ""

    return (
        f"{tt} {direction} {symbol} | "
        f"wrong_if: {condition}{setup_tag} | "
        f"regime={entry_regime} conv={conviction:.2f} score={score}"
    )


def execute_buy(ib: IB, symbol: str, price: float, atr: float,
                score: int, portfolio_value: float, regime: dict,
                reasoning: str = "",
                signal_scores: dict = None,
                agent_outputs: dict = None,
                open_time: str = None,
                candle_gate: str = None,
                tranche_mode: bool = True,
                instrument: str = "stock",
                # Trade advisor kwargs — override ATR formula when provided
                advice_pt: float = 0.0,
                advice_sl: float = 0.0,
                advice_size_mult: float = 1.0,
                advice_instrument: str = "COMMON",
                advice_id: str = "",
                # Intelligence layer classification
                trade_type: str = "",
                conviction: float = 0.0,
                pattern_id: str = "",
                market_read: str = "") -> bool:
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
    _get_alpaca_price = _om._get_alpaca_price                        # noqa: F841
    MarketOrder = _om.MarketOrder                            # noqa: F841
    LimitOrder  = _om.LimitOrder                             # noqa: F841
    StopOrder   = _om.StopOrder                              # noqa: F841

    # ── Alpaca real-time guards (fast path — no lock needed) ──────────────
    try:
        from alpaca_stream import HALT_CACHE, QUOTE_CACHE
        if HALT_CACHE.is_halted(symbol):
            log.warning(f"execute_buy {symbol}: trading halted (Alpaca status feed) — aborting")
            return False
        spread = QUOTE_CACHE.get_spread_pct(symbol)
        max_spread = CONFIG.get("max_spread_pct", 0.003)
        if spread is not None and spread > max_spread:
            log.warning(
                f"execute_buy {symbol}: spread {spread:.4%} > max {max_spread:.4%} — aborting"
            )
            return False
    except ImportError:
        pass  # alpaca_stream not wired — checks skipped

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
        contract = get_contract(symbol, instrument)
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

        # ── Advisor size multiplier ───────────────────────────────────
        if advice_size_mult != 1.0 and 0.25 <= advice_size_mult <= 2.0:
            qty = max(1, int(qty * advice_size_mult))
            log.info(f"[advisor] {symbol} size_mult={advice_size_mult} → qty={qty}")

        # ── HARD CAPS — last line of defense against contaminated data ──
        # Max order value = 20% of portfolio (stricter than max_single_position for safety)
        max_order_value = portfolio_value * 0.20
        if qty * price > max_order_value:
            old_qty = qty
            qty = max(1, int(max_order_value / price))
            log.warning(f"Order value ${old_qty * price:,.0f} exceeds 20% cap ${max_order_value:,.0f} for {symbol} — reduced qty {old_qty}→{qty}")

        # ── FX minimum lot size ───────────────────────────────────────────
        if instrument == "fx":
            fx_min_lot = CONFIG.get("fx_min_lot_size", 20000)
            if qty < fx_min_lot:
                log.info(f"FX {symbol}: qty {qty} below min lot {fx_min_lot} — raising to {fx_min_lot}")
                qty = fx_min_lot

        # ── PT / SL — use advisor levels if provided, otherwise ATR formula ──
        if advice_sl > 0 and advice_pt > 0:
            sl, tp = advice_sl, advice_pt
            log.info(f"[advisor] {symbol} PT=${tp:.2f} SL=${sl:.2f} (Opus)")
        else:
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

        # Non-SCALP trades are managed by Portfolio Manager — no mechanical TP.
        # Tranche mode relies on a T1 TP bracket; without it there is no reason to split.
        if trade_type and trade_type != "SCALP":
            tranche_mode = False

        if tranche_mode:
            t1_qty = qty // 2
            t2_qty = qty - t1_qty          # handles odd qty — T2 gets the extra share
            # tp is already set from the advisor or calculate_stops above — do not override.
            # T1 exits at the full advisor/formula TP; T2 runs open-ended past it.
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

        # ── Halt guard ────────────────────────────────────────────
        import bot_state as _bs
        if symbol in _bs._halted_symbols:
            log.warning(f"Skipping {symbol} — symbol is halted (IBKR error 154)")
            _safe_del_trade(symbol)
            return False

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
        # Stamp signal source on the order — survives in IBKR execution history.
        entry_order.orderRef = f"DEC:{score}"[:20]
        entry_order.transmit = False
        trade = ib.placeOrder(contract, entry_order)
        ib.sleep(0.2)  # brief pause for IBKR to assign orderId

        parent_id = trade.order.orderId

        # Leg 2: Stop loss — attached to parent, DO NOT transmit yet
        # StopLimitOrder is used instead of StopOrder: IBKR paper trading rejects pure
        # StopOrder bracket children with ValidationError on many small/mid-cap stocks.
        # Limit price = 1% below stop gives a reasonable fill window while being accepted.
        sl_limit = round(sl * 0.99, 2)
        sl_order = StopLimitOrder("SELL", qty, sl, sl_limit, account=account, tif="GTC", outsideRth=True)
        sl_order.parentId = parent_id
        # Empty trade_type falls back to SCALP behaviour (legacy callers, tests)
        place_tp = (not trade_type or trade_type == "SCALP")
        sl_order.transmit = not place_tp  # SCALP: False (TP follows); SWING/HOLD: True (transmit entry+SL)
        sl_trade = ib.placeOrder(contract, sl_order)
        ib.sleep(0.1)
        _sl_order_id = sl_trade.order.orderId  # captured for trailing stop modifications

        tp_trade = None
        if place_tp:
            # Leg 3: Take profit — SCALP only; transmit=True sends ALL 3 legs together
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
            "order_type": "STPLMT",
            "qty":        qty,
            "price":      sl,
            "status":     "SUBMITTED",
            "instrument": "stock",
            "role":       "stop_loss",
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })
        if place_tp and tp_trade is not None:
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
        # Even with atomic submission, edge cases (connectivity blips, race conditions)
        # can cause child orders to go Inactive. If that happens, cancel the broken
        # children and place standalone SL/TP orders (no parentId).
        order_status = trade.orderStatus.status
        if order_status in ('Cancelled', 'Inactive', 'ApiCancelled', 'ValidationError'):
            log.error(f"Entry order immediately rejected by IBKR for {symbol}: {order_status} — not tracking")
            return False

        sl_status = sl_trade.orderStatus.status
        _child_reject = ('Inactive', 'Cancelled', 'ApiCancelled', 'ValidationError')
        tp_status = tp_trade.orderStatus.status if tp_trade is not None else "N/A"

        if sl_status in _child_reject or (tp_trade is not None and tp_status in _child_reject):
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
                _sl_limit2 = round(sl * 0.99, 2)
                standalone_sl = StopLimitOrder("SELL", qty, sl, _sl_limit2, account=account, tif="GTC", outsideRth=True)
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

            if place_tp:
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
                    "advice_id":        advice_id,
                    "trade_type":       trade_type or "SCALP",
                    "conviction":       conviction,
                    "setup_type":       _derive_setup_type(signal_scores or {}),
                    "entry_regime":     (regime.get("session_character") or regime.get("regime", "UNKNOWN")) if isinstance(regime, dict) else "UNKNOWN",
                    "entry_thesis":     _build_entry_thesis(
                                            trade_type or "SCALP", symbol, "LONG",
                                            conviction, score,
                                            (regime.get("session_character") or regime.get("regime", "UNKNOWN")) if isinstance(regime, dict) else "UNKNOWN",
                                            market_read=market_read,
                                            rationale=reasoning,
                                        ),
                    "pattern_id":       pattern_id,
                    "sl_order_id":      _sl_order_id,
                    "tp_order_id":      tp_trade.order.orderId if tp_trade is not None else None,
                    "high_water_mark":  price,
                    # ── Tranche tracking ──────────────────────────────────
                    "tranche_mode":     tranche_mode,
                    "t1_qty":           t1_qty,
                    "t2_qty":           t2_qty,
                    "t1_status":        "OPEN" if tranche_mode else "N/A",
                    "t1_order_id":      tp_trade.order.orderId if tranche_mode else None,
                    "t2_sl_order_id":   None,  # set by update_tranche_status after T1 fills
                }
            _save_positions_file()
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
                  candle_gate: str = None,
                  instrument: str = "stock",
                  # Trade advisor kwargs — override ATR formula when provided
                  advice_pt: float = 0.0,
                  advice_sl: float = 0.0,
                  advice_size_mult: float = 1.0,
                  advice_instrument: str = "COMMON",
                  advice_id: str = "",
                  # Intelligence layer classification
                  trade_type: str = "",
                  conviction: float = 0.0,
                  pattern_id: str = "",
                  market_read: str = "") -> bool:
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
    _get_alpaca_price = _om._get_alpaca_price                        # noqa: F841
    MarketOrder = _om.MarketOrder                            # noqa: F841
    LimitOrder  = _om.LimitOrder                             # noqa: F841
    StopOrder   = _om.StopOrder                              # noqa: F841

    # ── Alpaca real-time guards (fast path — no lock needed) ──────────────
    try:
        from alpaca_stream import HALT_CACHE, QUOTE_CACHE
        if HALT_CACHE.is_halted(symbol):
            log.warning(f"execute_short {symbol}: trading halted (Alpaca status feed) — aborting")
            return False
        spread = QUOTE_CACHE.get_spread_pct(symbol)
        max_spread = CONFIG.get("max_spread_pct", 0.003)
        if spread is not None and spread > max_spread:
            log.warning(
                f"execute_short {symbol}: spread {spread:.4%} > max {max_spread:.4%} — aborting"
            )
            return False
    except ImportError:
        pass  # alpaca_stream not wired — checks skipped

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
        contract = get_contract(symbol, instrument)
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

        # ── Advisor size multiplier ───────────────────────────────────
        if advice_size_mult != 1.0 and 0.25 <= advice_size_mult <= 2.0:
            qty = max(1, int(qty * advice_size_mult))
            log.info(f"[advisor] {symbol} size_mult={advice_size_mult} → qty={qty}")

        max_order_value = portfolio_value * 0.20
        if qty * price > max_order_value:
            qty = max(1, int(max_order_value / price))

        # ── FX minimum lot size ───────────────────────────────────────────
        if instrument == "fx":
            fx_min_lot = CONFIG.get("fx_min_lot_size", 20000)
            if qty < fx_min_lot:
                log.info(f"FX {symbol}: qty {qty} below min lot {fx_min_lot} — raising to {fx_min_lot}")
                qty = fx_min_lot

        # ── PT / SL — use advisor levels if provided, otherwise ATR formula ──
        if advice_sl > 0 and advice_pt > 0:
            sl, tp = advice_sl, advice_pt
            log.info(f"[advisor] {symbol} PT=${tp:.2f} SL=${sl:.2f} (Opus)")
        else:
            sl, tp = calculate_stops(price, atr, "SHORT")  # sl > price, tp < price

        # Empty trade_type falls back to SCALP behaviour (legacy callers, tests)
        place_tp = (not trade_type or trade_type == "SCALP")

        reward = price - tp
        risk   = sl - price
        if risk <= 0 or (reward / risk) < CONFIG["min_reward_risk_ratio"]:
            log.warning(f"Poor R:R on short {symbol}: reward={reward:.2f} risk={risk:.2f} — skipping")
            _safe_del_trade(symbol)
            return False

        # ── Halt guard ────────────────────────────────────────────
        import bot_state as _bs
        if symbol in _bs._halted_symbols:
            log.warning(f"Skipping short {symbol} — symbol is halted (IBKR error 154)")
            _safe_del_trade(symbol)
            return False

        account = CONFIG["active_account"]
        # Sell slightly below bid to improve fill probability
        limit_price = round(price * 0.998, 2)

        # Entry: sell short
        entry_order = LimitOrder("SELL", qty, limit_price,
                                 account=account, tif="DAY", outsideRth=True)
        entry_order.orderRef = f"DEC:{score}"[:20]
        entry_order.transmit = False
        trade = ib.placeOrder(contract, entry_order)
        ib.sleep(0.2)

        parent_id = trade.order.orderId

        # Stop loss: buy to cover if price rises
        # StopLimitOrder: limit 1% above stop so IBKR accepts it reliably in paper trading.
        sl_limit = round(sl * 1.01, 2)
        sl_order = StopLimitOrder("BUY", qty, sl, sl_limit, account=account, tif="GTC", outsideRth=True)
        sl_order.parentId = parent_id
        sl_order.transmit = not place_tp  # SCALP: False (TP follows); SWING/HOLD: True (transmit entry+SL)
        sl_trade = ib.placeOrder(contract, sl_order)
        ib.sleep(0.1)
        _sl_order_id = sl_trade.order.orderId

        tp_trade = None
        if place_tp:
            # Take profit: buy to cover when price falls to target — SCALP only
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
            "order_type": "STPLMT",
            "qty":       qty,
            "price":     sl,
            "status":    "SUBMITTED",
            "instrument": "stock",
            "role":      "stop_loss",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if place_tp and tp_trade is not None:
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
                    "advice_id":           advice_id,
                    "trade_type":          trade_type or "SCALP",
                    "conviction":          conviction,
                    "setup_type":          _derive_setup_type(signal_scores or {}),
                    "entry_regime":        (regime.get("session_character") or regime.get("regime", "UNKNOWN")) if isinstance(regime, dict) else "UNKNOWN",
                    "entry_thesis":        _build_entry_thesis(
                                               trade_type or "SCALP", symbol, "SHORT",
                                               conviction, score,
                                               (regime.get("session_character") or regime.get("regime", "UNKNOWN")) if isinstance(regime, dict) else "UNKNOWN",
                                               market_read=market_read,
                                               rationale=reasoning,
                                           ),
                    "pattern_id":          pattern_id,
                    "sl_order_id":         _sl_order_id,
                    "tp_order_id":         tp_trade.order.orderId if tp_trade is not None else None,
                    "high_water_mark":     price,
                    "tranche_mode":        False,
                }
            _save_positions_file()
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

    # Guard: IBKR cancels MKT orders outside 4 AM–8 PM ET extended hours.
    # Defer rather than place-and-get-cancelled. The next scan cycle will retry.
    if not is_equities_extended_hours():
        import zoneinfo as _zi
        _now_et = datetime.now(_zi.ZoneInfo("America/New_York")).strftime("%H:%M ET")
        log.warning(f"execute_sell {symbol}: market closed ({_now_et}) — deferring until extended hours open (4 AM–8 PM ET)")
        return False

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
            contract = get_contract(symbol, info.get("instrument", "stock"))
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
        ib.sleep(2)

        # Cancel any orphaned bracket children (SL + TP legs) that remain open
        # after the position is closed. IBKR OCA only fires when one child fills —
        # it does not fire when the position is closed externally (e.g. manually).
        _entry_order_id = info.get("order_id")
        if _entry_order_id:
            try:
                for _t in ib.openTrades():
                    if getattr(_t.order, "parentId", None) == _entry_order_id:
                        ib.cancelOrder(_t.order)
                        log.info(f"Cancelled orphaned bracket child for {symbol} "
                                 f"(orderId={_t.order.orderId}, parentId={_entry_order_id})")
            except Exception as _e:
                log.warning(f"Bracket child cleanup for {symbol} failed: {_e}")

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
            _save_positions_file()
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
