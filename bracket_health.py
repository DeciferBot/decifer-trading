# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  bracket_health.py                          ║
# ║   Autonomous bracket order health enforcement                 ║
# ║   Inventor: AMIT CHOPRA                                       ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Three-function pipeline called each scan cycle:

  sync_bracket_prices(ib)  — Read IBKR live order prices → correct tracker.
                              IBKR is ground truth. No orders submitted.

  audit_bracket_orders(ib) — Two remediation passes:
    Pass 1 – Presence : SL or TP order missing from IBKR → resubmit with
                         freshly recalculated price (current ± ATR).
    Pass 3 – Missed SL: Current price already past SL level and IBKR stop
                         never fired → close at limit (bid for LONG, ask for
                         SHORT). GTC so it stays live if not filled immediately.

Retry guard: 5-minute per-symbol cooldown prevents infinite retry storms.
"""
from __future__ import annotations

import time

from ib_async import IB, LimitOrder, StopLimitOrder

from config import CONFIG
from orders_contracts import _get_ibkr_bid_ask, get_contract, is_equities_extended_hours
from orders_state import (
    _safe_update_trade,
    _save_positions_file,
    _trades_lock,
    active_trades,
    log,
)

_RETRY_COOLDOWN_S: int = 300  # 5 minutes between remediation attempts per symbol
_retry_ts: dict[str, float] = {}


def _in_cooldown(symbol: str) -> bool:
    return (time.monotonic() - _retry_ts.get(symbol, 0)) < _RETRY_COOLDOWN_S


def _mark_retry(symbol: str) -> None:
    _retry_ts[symbol] = time.monotonic()


# ── Pass 2: Sync ─────────────────────────────────────────────────────────────

def sync_bracket_prices(ib: IB) -> None:
    """
    Read IBKR live order prices back into the tracker. Called before
    update_trailing_stops() so trailing logic always works from the real
    IBKR price, not a stale intended value.

    SL orders: auxPrice (STP/STPLMT trigger price)
    TP orders: lmtPrice (LMT price)
    """
    if not CONFIG.get("bracket_health_enabled", True):
        return
    try:
        live_orders = {t.order.orderId: t for t in ib.openTrades()}
    except Exception as _e:
        log.warning("[BRACKET_SYNC] Failed to fetch IBKR open orders: %s", _e)
        return

    changed = False
    with _trades_lock:
        for key, pos in active_trades.items():
            if pos.get("status") not in ("ACTIVE", "TRIMMING"):
                continue
            if pos.get("instrument") not in ("stock", None):
                continue

            sl_oid = pos.get("sl_order_id")
            if sl_oid and sl_oid in live_orders:
                ibkr_sl = float(live_orders[sl_oid].order.auxPrice or 0)
                if 0 < ibkr_sl < 1e10 and abs(ibkr_sl - (pos.get("sl") or 0)) > 0.005:
                    log.info(
                        "[BRACKET_SYNC] %s sl corrected: tracker=%.2f ibkr=%.2f",
                        pos.get("symbol", key), pos.get("sl") or 0, ibkr_sl,
                    )
                    pos["sl"] = round(ibkr_sl, 4)
                    changed = True

            tp_oid = pos.get("tp_order_id")
            if tp_oid and tp_oid in live_orders:
                ibkr_tp = float(live_orders[tp_oid].order.lmtPrice or 0)
                if 0 < ibkr_tp < 1e10 and abs(ibkr_tp - (pos.get("tp") or 0)) > 0.005:
                    log.info(
                        "[BRACKET_SYNC] %s tp corrected: tracker=%.2f ibkr=%.2f",
                        pos.get("symbol", key), pos.get("tp") or 0, ibkr_tp,
                    )
                    pos["tp"] = round(ibkr_tp, 4)
                    changed = True

    if changed:
        try:
            _save_positions_file()
        except Exception as _e:
            log.warning("[BRACKET_SYNC] positions save failed: %s", _e)


# ── Pass 1 + Pass 3: Audit ───────────────────────────────────────────────────

def audit_bracket_orders(ib: IB) -> None:
    """
    Pass 1 — resubmit missing SL/TP orders.
    Pass 3 — close positions where the stop level was breached but IBKR didn't fire.
    """
    if not CONFIG.get("bracket_health_enabled", True):
        return
    try:
        live_orders = {t.order.orderId: t for t in ib.openTrades()}
    except Exception as _e:
        log.warning("[BRACKET_AUDIT] Failed to fetch IBKR open orders: %s", _e)
        return

    trail_mult = CONFIG.get("atr_trail_multiplier", 1.5)
    account = CONFIG["active_account"]

    with _trades_lock:
        snapshot = list(active_trades.items())

    changed = False
    for key, pos in snapshot:
        try:
            if pos.get("status") not in ("ACTIVE", "TRIMMING"):
                continue
            if pos.get("instrument") not in ("stock", None):
                continue

            symbol = pos.get("symbol", key)
            direction = pos.get("direction", "LONG")
            current = pos.get("current") or pos.get("entry", 0)
            entry = pos.get("entry", 0)
            atr = pos.get("atr") or 0
            qty = pos.get("qty", 0)
            sl = pos.get("sl") or 0
            tp = pos.get("tp") or 0
            sl_oid = pos.get("sl_order_id")
            tp_oid = pos.get("tp_order_id")

            if not current or not entry or not qty:
                continue

            sl_in_ibkr = bool(sl_oid and sl_oid in live_orders)
            tp_in_ibkr = bool(tp_oid and tp_oid in live_orders)
            close_action = "SELL" if direction == "LONG" else "BUY"

            # ── Pass 3: Missed stop ───────────────────────────────────────────
            sl_breached = (
                (direction == "LONG" and sl > 0 and current < sl)
                or (direction == "SHORT" and sl > 0 and current > sl)
            )
            if sl_breached:
                if _in_cooldown(symbol):
                    log.debug("[BRACKET_AUDIT] %s missed-stop cooldown active — skipping", symbol)
                    continue
                if not is_equities_extended_hours():
                    log.warning(
                        "[BRACKET_AUDIT] %s missed stop (current=%.2f sl=%.2f) "
                        "but outside extended hours — deferring",
                        symbol, current, sl,
                    )
                    continue
                log.warning(
                    "[BRACKET_AUDIT] %s missed stop: current=%.2f below sl=%.2f — "
                    "closing at limit",
                    symbol, current, sl,
                )
                try:
                    contract = get_contract(symbol, "stock")
                    ib.qualifyContracts(contract)
                    bid, ask = _get_ibkr_bid_ask(ib, contract)
                    if direction == "LONG":
                        limit_px = bid if bid > 0 else round(current * 0.999, 2)
                    else:
                        limit_px = ask if ask > 0 else round(current * 1.001, 2)
                    close_order = LimitOrder(
                        close_action, qty, limit_px,
                        account=account, tif="GTC", outsideRth=True,
                    )
                    close_trade = ib.placeOrder(contract, close_order)
                    ib.sleep(0.4)
                    close_status = close_trade.orderStatus.status if close_trade else ""
                    if close_status in ("Submitted", "PreSubmitted"):
                        _safe_update_trade(key, {
                            "status": "EXITING",
                            "close_order_id": close_trade.order.orderId,
                            "pending_exit_reason": "bracket_audit_missed_stop",
                        })
                        changed = True
                        log.info(
                            "[BRACKET_AUDIT] %s close at limit %.2f placed (order #%d)",
                            symbol, limit_px, close_trade.order.orderId,
                        )
                    else:
                        log.warning(
                            "[BRACKET_AUDIT] %s limit close rejected (status=%r) — retry next cycle",
                            symbol, close_status,
                        )
                except Exception as _ce:
                    log.error("[BRACKET_AUDIT] %s missed-stop close failed: %s", symbol, _ce)
                _mark_retry(symbol)
                continue  # don't resubmit brackets when we're already trying to close

            if _in_cooldown(symbol):
                continue

            # Qualify contract once for Pass 1 only if an order needs resubmission
            _contract_qualified = False

            def _get_qualified_contract():
                nonlocal _contract_qualified
                c = get_contract(symbol, "stock")
                ib.qualifyContracts(c)
                _contract_qualified = True
                return c

            # ── Pass 1: Missing SL ────────────────────────────────────────────
            if not sl_in_ibkr:
                if not atr:
                    log.warning("[BRACKET_AUDIT] %s sl_order missing but no ATR — skipping", symbol)
                else:
                    new_sl = (
                        round(current - trail_mult * atr, 2)
                        if direction == "LONG"
                        else round(current + trail_mult * atr, 2)
                    )
                    sl_valid = (
                        (direction == "LONG" and new_sl < current)
                        or (direction == "SHORT" and new_sl > current)
                    )
                    if not sl_valid:
                        log.warning(
                            "[BRACKET_AUDIT] %s recalculated sl=%.2f invalid vs current=%.2f — skipping",
                            symbol, new_sl, current,
                        )
                    else:
                        try:
                            contract = _get_qualified_contract()
                            sl_limit = (
                                round(new_sl * 0.99, 2)
                                if direction == "LONG"
                                else round(new_sl * 1.01, 2)
                            )
                            sl_order = StopLimitOrder(
                                close_action, qty, new_sl, sl_limit,
                                account=account, tif="GTC", outsideRth=True,
                            )
                            sl_trade = ib.placeOrder(contract, sl_order)
                            ib.sleep(0.4)
                            sl_status = sl_trade.orderStatus.status if sl_trade else ""
                            if sl_status in ("Submitted", "PreSubmitted"):
                                with _trades_lock:
                                    if key in active_trades:
                                        active_trades[key]["sl"] = new_sl
                                        active_trades[key]["sl_order_id"] = sl_trade.order.orderId
                                changed = True
                                log.info(
                                    "[BRACKET_AUDIT] %s sl resubmitted: %.2f (order #%d)",
                                    symbol, new_sl, sl_trade.order.orderId,
                                )
                            else:
                                log.warning(
                                    "[BRACKET_AUDIT] %s sl resubmit rejected (status=%r)",
                                    symbol, sl_status,
                                )
                        except Exception as _sle:
                            log.error("[BRACKET_AUDIT] %s sl resubmit failed: %s", symbol, _sle)
                        _mark_retry(symbol)

            # ── Pass 1: Missing TP ────────────────────────────────────────────
            if not tp_in_ibkr:
                if not atr:
                    log.warning("[BRACKET_AUDIT] %s tp_order missing but no ATR — skipping", symbol)
                else:
                    # Use stored tp if still on the right side of current, else recalculate
                    if direction == "LONG":
                        new_tp = tp if (tp and tp > current) else round(current + 2 * trail_mult * atr, 2)
                    else:
                        new_tp = tp if (tp and tp < current) else round(current - 2 * trail_mult * atr, 2)

                    tp_valid = (
                        (direction == "LONG" and new_tp > current)
                        or (direction == "SHORT" and new_tp < current)
                    )
                    if not tp_valid:
                        log.warning(
                            "[BRACKET_AUDIT] %s recalculated tp=%.2f invalid vs current=%.2f — skipping",
                            symbol, new_tp, current,
                        )
                    else:
                        try:
                            contract = _get_qualified_contract() if not _contract_qualified else contract
                            tp_order = LimitOrder(
                                close_action, qty, new_tp,
                                account=account, tif="GTC", outsideRth=True,
                            )
                            tp_trade = ib.placeOrder(contract, tp_order)
                            ib.sleep(0.4)
                            tp_status = tp_trade.orderStatus.status if tp_trade else ""
                            if tp_status in ("Submitted", "PreSubmitted"):
                                with _trades_lock:
                                    if key in active_trades:
                                        active_trades[key]["tp"] = new_tp
                                        active_trades[key]["tp_order_id"] = tp_trade.order.orderId
                                changed = True
                                log.info(
                                    "[BRACKET_AUDIT] %s tp resubmitted: %.2f (order #%d)",
                                    symbol, new_tp, tp_trade.order.orderId,
                                )
                            else:
                                log.warning(
                                    "[BRACKET_AUDIT] %s tp resubmit rejected (status=%r)",
                                    symbol, tp_status,
                                )
                        except Exception as _tpe:
                            log.error("[BRACKET_AUDIT] %s tp resubmit failed: %s", symbol, _tpe)
                        _mark_retry(symbol)

        except Exception as _exc:
            log.error("[BRACKET_AUDIT] %s audit loop failed: %s", key, _exc)
            continue

    if changed:
        try:
            _save_positions_file()
        except Exception as _e:
            log.warning("[BRACKET_AUDIT] positions save failed: %s", _e)
