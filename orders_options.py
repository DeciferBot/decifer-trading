# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders_options.py                          ║
# ║   Options order execution — buy, sell, tranche, trailing     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Options execution functions.
Imports from orders_state (shared state), orders_contracts (utilities),
and orders_guards (duplicate checks).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from ib_async import IB, LimitOrder, Option, StopOrder

from config import CONFIG
from learning import log_order
from orders_contracts import (
    _ET,
    _cancel_ibkr_order_by_id,
    _is_option_contract,
    get_contract,
    is_options_market_open,
)
from orders_state import (
    _safe_del_trade,
    _safe_update_trade,
    _trades_lock,
    active_trades,
    log,
    recently_closed,
)
from risk import check_combined_exposure, check_sector_concentration, record_loss, record_win

# ── Module-level options tracking state ─────────────────────────────────────
_option_sell_attempts: dict = {}  # opt_key → {"count": int, "last_try": datetime, "had_partial": bool}
_MAX_OPTION_SELL_RETRIES = 3  # after this many failures, pause retries for cooldown
_OPTION_SELL_COOLDOWN = 600  # seconds (10 min) before retrying after max failures
_MIN_SELL_RETRY_INTERVAL_S = 90  # min seconds between any two sell attempts (prevents PM+check_options double-fire)

# Exits requested while the options market was closed — flushed on next open cycle
_PENDING_EXITS_FILE = os.path.join(os.path.dirname(__file__), "data", "pending_option_exits.json")
_pending_option_exits: dict = {}  # opt_key → original reason string


def _load_pending_exits() -> None:
    global _pending_option_exits
    try:
        if os.path.exists(_PENDING_EXITS_FILE):
            with open(_PENDING_EXITS_FILE) as f:
                _pending_option_exits = json.load(f)
            if _pending_option_exits:
                import logging as _logging

                _logging.getLogger(__name__).info(
                    f"orders_options: loaded {len(_pending_option_exits)} persisted pending exit(s): "
                    + ", ".join(_pending_option_exits.keys())
                )
    except Exception:
        _pending_option_exits = {}


def _save_pending_exits() -> None:
    try:
        os.makedirs(os.path.dirname(_PENDING_EXITS_FILE), exist_ok=True)
        tmp = _PENDING_EXITS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_pending_option_exits, f)
        os.replace(tmp, _PENDING_EXITS_FILE)
    except Exception as _e:
        import logging as _logging

        _logging.getLogger(__name__).warning(f"orders_options: failed to persist pending exits: {_e}")


_load_pending_exits()


def execute_buy_option(
    ib: IB, contract_info: dict, portfolio_value: float, reasoning: str = "", score: int = 0
) -> bool:
    """
    Buy an options contract (call or put).
    contract_info is the dict returned by options.find_best_contract().
    Entry is a limit order at the mid price.
    Returns True if order placed successfully.
    """
    symbol = contract_info["symbol"]
    opt_key = f"{symbol}_{contract_info['right']}_{contract_info['strike']}_{contract_info['expiry_str']}"

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
        mid_price = contract_info["mid"]
        est_option_value = n_contracts * mid_price * 100  # total premium outlay

        exp_ok, exp_reason = check_combined_exposure(
            symbol, est_option_value, list(active_trades.values()), portfolio_value, instrument="option"
        )
        if not exp_ok:
            log.warning(f"Combined exposure block for {symbol} options: {exp_reason}")
            return False

        # ── FIX #2: Sector concentration check ────────────────────────
        sec_ok, sec_reason = check_sector_concentration(
            symbol,
            list(active_trades.values()),
            portfolio_value,  # regime not passed to execute_buy_option, default NORMAL
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
        limit_price = round(ask_price, 2)  # at-ask: fills reliably
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
        entry_order = LimitOrder("BUY", n_contracts, limit_price, account=account, tif="DAY", outsideRth=False)
        trade = ib.placeOrder(option_contract, entry_order)
        ib.sleep(1)

        # Check if IBKR immediately rejected the order
        order_status = trade.orderStatus.status
        if order_status in ("Cancelled", "Inactive", "ApiCancelled", "ValidationError"):
            log.error(f"Option order immediately rejected by IBKR for {opt_key}: {order_status}")
            _safe_del_trade(opt_key)  # release reservation
            return False

        # Log the option order
        log_order(
            {
                "order_id": trade.order.orderId,
                "symbol": symbol,
                "side": "BUY",
                "order_type": "LMT",
                "qty": n_contracts,
                "price": limit_price,
                "status": "SUBMITTED",
                "instrument": "option",
                "right": contract_info["right"],
                "strike": contract_info["strike"],
                "expiry": contract_info["expiry_str"],
                "mid": mid_price,
                "ask": ask_price,
                "spread_pct": contract_info.get("spread_pct"),
                "reasoning": reasoning,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        try:
            from ic_calculator import get_current_weights as _get_icw_opt

            _icw_at_entry_opt = _get_icw_opt()
        except Exception:
            _icw_at_entry_opt = None
        active_trades[opt_key] = {
            "symbol": symbol,
            "instrument": "option",
            "right": contract_info["right"],
            "strike": contract_info["strike"],
            "expiry_str": contract_info["expiry_str"],
            "expiry_ibkr": contract_info["expiry_ibkr"],
            "dte": contract_info["dte"],
            "contracts": n_contracts,
            "entry_premium": mid_price,
            "current_premium": mid_price,
            "entry": mid_price,  # unified field for dashboard
            "current": mid_price,
            "qty": n_contracts,
            "sl": round(mid_price * (1 - CONFIG.get("options_stop_loss", 0.50)), 4),
            "tp": round(mid_price * (1 + CONFIG.get("options_profit_target", 0.75)), 4),
            "delta": contract_info.get("delta"),
            "theta": contract_info.get("theta"),
            "iv": contract_info.get("iv"),
            "iv_rank": contract_info.get("iv_rank"),
            "underlying_price": contract_info.get("underlying_price"),
            "pnl": 0.0,
            "score": score,
            "entry_score": score,  # immutable snapshot for portfolio manager
            "direction": "LONG",
            "reasoning": reasoning,
            "status": "PENDING",
            "order_id": trade.order.orderId,
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


def execute_sell_option(ib: IB, opt_key: str, reason: str = "signal", contracts_override: int | None = None) -> bool:
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
            f"Options market closed ({now_et.strftime('%H:%M ET')}) — deferring exit for {opt_key} until next open"
        )
        _pending_option_exits[opt_key] = reason
        _save_pending_exits()
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
    attempts = _option_sell_attempts.get(opt_key, {"count": 0, "last_try": datetime.min, "had_partial": False})
    if attempts["count"] >= _MAX_OPTION_SELL_RETRIES:
        elapsed = (datetime.now(UTC) - attempts["last_try"]).total_seconds()
        if elapsed < _OPTION_SELL_COOLDOWN:
            log.warning(
                f"Option sell for {opt_key} failed {attempts['count']}x — "
                f"cooling down ({int(_OPTION_SELL_COOLDOWN - elapsed)}s remaining)"
            )
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
        attempts["count"] = 1
        attempts["had_partial"] = False

    # ── Min interval guard: prevent PM + check_options double-firing in the same cycle ──
    if attempts["count"] > 0 and attempts["last_try"] != datetime.min:
        elapsed_since_last = (datetime.now(UTC) - attempts["last_try"]).total_seconds()
        if elapsed_since_last < _MIN_SELL_RETRY_INTERVAL_S:
            log.debug(
                f"Option sell {opt_key}: retry interval not elapsed "
                f"({int(elapsed_since_last)}s / {_MIN_SELL_RETRY_INTERVAL_S}s) — skipping"
            )
            return False

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
        ticker = ib.reqMktData(option_contract, "", False, False)
        ib.sleep(2)  # allow quote data to arrive

        bid = getattr(ticker, "bid", None)
        ask = getattr(ticker, "ask", None)
        last = getattr(ticker, "last", None)

        # Determine order direction: SHORT positions close with BUY, LONG with SELL
        _is_short = pos.get("direction", "LONG").upper() == "SHORT"
        _close_action = "BUY" if _is_short else "SELL"

        import math as _m

        _bid_ok = bid is not None and not _m.isnan(bid) and bid > 0
        _ask_ok = ask is not None and not _m.isnan(ask) and ask > 0
        _retry_count = attempts["count"]
        # Only step price if the last attempt had ZERO fills (market rejected the price).
        # Partial fills mean the market IS trading at the current level — don't step further.
        _step = 0 if attempts.get("had_partial") else _retry_count
        if _is_short:
            # BUY-to-close: offer at ask, step 5% ABOVE ask each retry to chase fills
            _premium = round(1.0 + (_step * 0.05), 2)
            if _ask_ok:
                limit_price = round(ask * _premium, 2)
            elif last and not _m.isnan(last) and last > 0:
                limit_price = round(last * 1.03 * _premium, 2)
            else:
                limit_price = round(pos.get("current_premium", 0.10) * 1.10, 2)
        else:
            # SELL-to-close: price at bid and step down aggressively
            _discount = round(1.0 - (_step * 0.05), 2)
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
            if not (_bid_ok and bid < 0.05):
                limit_price = max(limit_price, 0.05)

        ib.cancelMktData(option_contract)

        sell_order = LimitOrder(_close_action, sell_contracts, limit_price, account=CONFIG["active_account"], tif="DAY")
        sell_order.outsideRth = False
        opt_sell_trade = ib.placeOrder(option_contract, sell_order)

        log.info(
            f"Option LMT {_close_action} placed: {opt_key} x{sell_contracts} @ ${limit_price:.2f} "
            f"(bid={bid}, ask={ask}, direction={pos.get('direction', 'LONG')})"
        )

        # Wait for fill confirmation
        max_wait = 15 if attempts["count"] == 0 else 25
        for _ in range(max_wait * 2):
            ib.sleep(0.5)
            status = opt_sell_trade.orderStatus.status
            if status in ("Filled", "Cancelled", "Inactive", "ApiCancelled"):
                break

        order_status = opt_sell_trade.orderStatus.status
        if order_status != "Filled":
            # Handle partial fills
            filled_qty = int(opt_sell_trade.orderStatus.filled or 0)
            if filled_qty > 0:
                remaining = pos["contracts"] - filled_qty
                log.info(f"[PARTIAL FILL] {opt_key}: {filled_qty} contracts filled, {remaining} remaining")
                if remaining <= 0:
                    _option_sell_attempts.pop(opt_key, None)
                    del active_trades[opt_key]
                    log.info(f"[PARTIAL→FULL] {opt_key} fully closed via partial fills")
                    return True
                _safe_update_trade(opt_key, {"contracts": remaining})
                # Partial fill: market IS trading at this level — don't step price down,
                # just record the timestamp so the min-interval guard prevents immediate re-fire.
                attempts["had_partial"] = True
                attempts["last_try"] = datetime.now(UTC)
                _option_sell_attempts[opt_key] = attempts
            else:
                # Zero fill: market rejected this price — step down next attempt.
                attempts["count"] += 1
                attempts["had_partial"] = False
                attempts["last_try"] = datetime.now(UTC)
                _option_sell_attempts[opt_key] = attempts
            log.error(
                f"Option sell for {opt_key} not filled — status={order_status}, "
                f"limit=${limit_price:.2f}. Attempt {attempts['count']}/{_MAX_OPTION_SELL_RETRIES}. "
                f"Keeping position in tracker (IBKR still holds it)."
            )
            try:
                ib.cancelOrder(opt_sell_trade.order)
            except Exception:
                pass
            _safe_update_trade(opt_key, {"status": "ACTIVE"})
            return False

        # Guard against paper-account false fills
        fill_price = opt_sell_trade.orderStatus.avgFillPrice
        if not fill_price or fill_price <= 0:
            log.warning(
                f"Option sell {opt_key}: status=Filled but avgFillPrice=0 — "
                f"treating as failed (paper account false positive)."
            )
            attempts["count"] += 1
            attempts["last_try"] = datetime.now(UTC)
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
        log_order(
            {
                "order_id": opt_sell_trade.order.orderId,
                "symbol": pos["symbol"],
                "side": _close_action,
                "order_type": "LMT",
                "qty": sell_contracts,
                "price": limit_price,
                "status": "FILLED",
                "instrument": "option",
                "right": pos["right"],
                "strike": pos["strike"],
                "expiry": pos["expiry_str"],
                "fill_price": fill_price,
                "role": "close",
                "reason": reason,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        entry = pos["entry_premium"]
        current = fill_price
        if _is_short:
            pnl = (entry - current) * sell_contracts * 100
        else:
            pnl = (current - entry) * sell_contracts * 100

        # Check commission report for IBKR realizedPNL
        try:
            import math as _math

            _fills = ib.fills()
            _close_sides = ("SLD", "SELL") if _close_action == "SELL" else ("BOT", "BUY")
            opt_sell_fills = [
                f
                for f in _fills
                if f.contract.symbol == pos["symbol"]
                and f.execution.side.upper() in _close_sides
                and _is_option_contract(f.contract)
            ]
            for f in opt_sell_fills:
                cr = f.commissionReport
                if cr is not None:
                    raw = getattr(cr, "realizedPNL", None)
                    if raw is not None:
                        raw_f = float(raw)
                        if not _math.isnan(raw_f) and raw_f != 0.0:
                            pnl = raw_f
                            break
        except Exception:
            pass

        if pnl >= 0:
            record_win()
        else:
            record_loss()

        from learning import log_trade

        log_trade(
            trade=pos,
            agent_outputs={},
            regime={"regime": "UNKNOWN", "vix": 0.0},
            action="CLOSE",
            outcome={
                "exit_price": round(current, 4),
                "pnl": round(pnl, 2),
                "reason": reason,
            },
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
            recently_closed[pos["symbol"]] = datetime.now(UTC).isoformat()
            del active_trades[opt_key]
        return True

    except Exception as e:
        log.error(f"Option sell failed {opt_key}: {e}")
        _exc_att = _option_sell_attempts.get(opt_key, {"count": 0, "last_try": datetime.now(UTC)})
        _exc_att["count"] += 1
        _exc_att["last_try"] = datetime.now(UTC)
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
            _save_pending_exits()
            continue
        log.info(f"Flushing deferred option exit: {opt_key} (original reason: {reason})")
        _pending_option_exits.pop(opt_key, None)
        _save_pending_exits()
        execute_sell_option(ib, opt_key, reason=f"deferred:{reason}")


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

            entry = trade["entry"]
            t1_qty = trade["t1_qty"]
            t2_qty = trade["t2_qty"]
            tp_t1 = trade["tp"]
            sl_price = trade["sl"]

            t1_pnl = round((tp_t1 - entry) * t1_qty, 2)
            from learning import log_trade

            log_trade(
                trade={**trade, "qty": t1_qty, "tranche_id": 1, "parent_trade_id": trade.get("order_id")},
                agent_outputs=trade.get("agent_outputs", {}),
                regime={"regime": "UNKNOWN", "vix": 0.0},
                action="CLOSE",
                outcome={"exit_price": tp_t1, "pnl": t1_pnl, "reason": "tranche_1_tp"},
            )

            # Cancel full-qty bracket SL
            old_sl_id = trade.get("sl_order_id")
            if old_sl_id:
                _cancel_ibkr_order_by_id(ib, old_sl_id)
                ib.sleep(0.3)

            # Place standalone T2 stop
            contract = get_contract(symbol)
            t2_stop = StopOrder(
                "SELL",
                t2_qty,
                sl_price,
                account=CONFIG["active_account"],
                tif="GTC",
                outsideRth=True,
            )
            t2_stop.transmit = True
            t2_stop_trade = ib.placeOrder(contract, t2_stop)
            ib.sleep(0.5)
            new_id = t2_stop_trade.order.orderId

            with _trades_lock:
                if symbol in active_trades:
                    active_trades[symbol]["t1_status"] = "FILLED"
                    active_trades[symbol]["t2_sl_order_id"] = new_id
                    active_trades[symbol]["sl_order_id"] = new_id
                    active_trades[symbol]["qty"] = t2_qty

            log.info(
                f"[TRANCHE] {symbol} T1 ✅ P&L ${t1_pnl:+.2f} — "
                f"T2 stop placed: qty={t2_qty} @ ${sl_price:.2f} orderId={new_id}"
            )

        except Exception as exc:
            log.error(f"[TRANCHE] update_tranche_status failed for {symbol}: {exc}")


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
            if trade.get("tranche_mode") and trade.get("t1_status") == "OPEN":
                continue
            sl_order_id = trade.get("sl_order_id")
            if not sl_order_id:
                continue

            atr = trade.get("atr")
            if not atr or atr <= 0:
                continue

            direction = trade.get("direction", "LONG")
            current = trade.get("current", trade["entry"])
            hwm = trade.get("high_water_mark", trade["entry"])
            old_sl = trade["sl"]
            qty = trade["qty"]

            if direction == "LONG":
                new_hwm = max(hwm, current)
                new_sl = round(new_hwm - trail_mult * atr, 2)
                if new_sl <= old_sl:
                    continue
            else:  # SHORT
                new_hwm = min(hwm, current)
                new_sl = round(new_hwm + trail_mult * atr, 2)
                if new_sl >= old_sl:
                    continue

            if not ib.isConnected():
                log.warning("[TRAIL] IBKR disconnected — skipping trailing stop update")
                return

            contract = get_contract(symbol)
            modified_stop = StopOrder(
                "SELL",
                qty,
                new_sl,
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
                    active_trades[symbol]["sl"] = new_sl
                    active_trades[symbol]["high_water_mark"] = new_hwm

            log.info(
                f"[TRAIL] {symbol} {'▲' if direction == 'LONG' else '▼'} "
                f"stop {old_sl:.2f} → {new_sl:.2f}  hwm={new_hwm:.2f}"
            )

        except Exception as exc:
            log.error(f"[TRAIL] {symbol} trailing stop update failed: {exc}")
            continue


# ── Session-scoped options attempt ledger ────────────────────────────────────
# Tracks which (symbol, direction) pairs have already been attempted today.
# Key  : "{symbol}_{direction}"  e.g. "NKE_LONG"
# Value: ISO date string         e.g. "2026-04-02"
#
# Design rationale (industry standard for DAY orders):
#   A cancelled DAY order is TERMINAL for that signal instance. Retrying the
#   same symbol+direction on the same session date is almost always wrong —
#   if IBKR couldn't fill it once, market conditions haven't changed enough
#   in 5 minutes to justify another attempt.
#   The natural retry boundary is the NEXT session when a fresh signal fires.
#
# Disk-persisted and auto-expires by date — no manual clearing needed.
import json as _json
import pathlib as _pathlib

_OPTIONS_LEDGER_PATH = _pathlib.Path("data/options_attempt_ledger.json")


def _load_options_ledger() -> dict:
    """Load ledger from disk. Returns {} on any error."""
    try:
        if _OPTIONS_LEDGER_PATH.exists():
            return _json.loads(_OPTIONS_LEDGER_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_options_ledger(ledger: dict) -> None:
    """Persist ledger to disk. Silently ignores write errors."""
    try:
        _OPTIONS_LEDGER_PATH.write_text(_json.dumps(ledger))
    except Exception:
        pass


def _options_attempted_today(symbol: str, direction: str) -> bool:
    """Return True if we already attempted options on this symbol+direction today."""
    from datetime import datetime as _dt

    key = f"{symbol}_{direction}"
    today = _dt.now().strftime("%Y-%m-%d")
    return _options_ledger.get(key) == today


def _record_options_attempt(symbol: str, direction: str) -> None:
    """Mark this symbol+direction as attempted today and persist."""
    from datetime import datetime as _dt

    key = f"{symbol}_{direction}"
    today = _dt.now().strftime("%Y-%m-%d")
    _options_ledger[key] = today
    # Prune stale entries (any date != today) to keep the file tidy
    stale = [k for k, v in _options_ledger.items() if v != today]
    for k in stale:
        del _options_ledger[k]
    _save_options_ledger(_options_ledger)


_options_ledger: dict = _load_options_ledger()
