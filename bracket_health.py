# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  bracket_health.py                          ║
# ║   Autonomous bracket order health enforcement                 ║
# ║   Inventor: AMIT CHOPRA                                       ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Three-function pipeline called each scan cycle:

  sync_bracket_prices(ib)  — Read IBKR live order prices → correct tracker.
                              Reattaches sl_order_id / tp_order_id from IBKR
                              when local cache is empty. IBKR is ground truth.

  audit_bracket_orders(ib) — IBKR-first audit: queries IBKR first, counts
                              bracket legs per position, then acts:
                                count == 0  → submit new order (and cache ID)
                                count == 1  → reattach ID to local cache (no resubmit)
                                count  > 1  → cancel duplicates, keep best, reattach ID

    Pass 3 – Missed SL: Current price already past SL level and IBKR stop
             never fired → close at limit (bid for LONG, ask for SHORT).
             GTC so it stays live if not filled immediately.

Retry guard: 5-minute per-symbol cooldown governs new order submissions only.
Duplicate cancellation and ID reattachment are immediate, not cooldown-gated.
"""
from __future__ import annotations

import time

from ib_async import IB, LimitOrder, StopLimitOrder, Trade

from config import CONFIG
from orders_contracts import _get_ibkr_bid_ask, get_contract, is_equities_extended_hours
from orders_state import (
    _safe_update_trade,
    _save_positions_file,
    _trades_lock,
    active_trades,
    log,
)

_RETRY_COOLDOWN_S: int = 300  # 5 minutes between new-order submissions per symbol
_retry_ts: dict[str, float] = {}


def _in_cooldown(symbol: str) -> bool:
    return (time.monotonic() - _retry_ts.get(symbol, 0)) < _RETRY_COOLDOWN_S


def _mark_retry(symbol: str) -> None:
    _retry_ts[symbol] = time.monotonic()


# ── IBKR bracket map ─────────────────────────────────────────────────────────

def _build_ibkr_bracket_map(ib: IB) -> dict[str, dict[str, list[Trade]]]:
    """
    Single ib.openTrades() call. Returns symbol-keyed map of active bracket legs.

    result[sym]["sl_orders"] — stop-type orders (STP, STP LMT, TRAIL, TRAILLMT)
    result[sym]["tp_orders"] — limit orders (LMT) that are exit-side

    Only Submitted / PreSubmitted STK orders are included.
    """
    result: dict[str, dict[str, list]] = {}
    try:
        for trade in ib.openTrades():
            sec_type = (trade.contract.secType or "").upper()
            if sec_type not in ("STK", ""):
                continue
            if trade.orderStatus.status not in ("Submitted", "PreSubmitted"):
                continue
            sym = trade.contract.symbol
            otype = (trade.order.orderType or "").upper().replace(" ", "")
            action = (trade.order.action or "").upper()
            if sym not in result:
                result[sym] = {"sl_orders": [], "tp_orders": []}
            if otype in ("STP", "STPLMT", "TRAIL", "TRAILLMT"):
                result[sym]["sl_orders"].append(trade)
            elif otype == "LMT" and action in ("SELL", "BUY"):
                result[sym]["tp_orders"].append(trade)
    except Exception as _e:
        log.warning("[BRACKET_MAP] Failed to build IBKR bracket map: %s", _e)
        return {}
    return result


def _pick_best_sl(candidates: list[Trade], stored_sl: float) -> Trade:
    """Keep SL closest to stored price; if no stored price, keep highest orderId."""
    if stored_sl > 0:
        return min(candidates, key=lambda t: abs(float(t.order.auxPrice or 0) - stored_sl))
    return max(candidates, key=lambda t: t.order.orderId)


def _pick_best_tp(candidates: list[Trade], stored_tp: float) -> Trade:
    """Keep TP closest to stored price; if no stored price, keep highest orderId."""
    if stored_tp > 0:
        return min(candidates, key=lambda t: abs(float(t.order.lmtPrice or 0) - stored_tp))
    return max(candidates, key=lambda t: t.order.orderId)


# ── Pass 2: Sync ─────────────────────────────────────────────────────────────

def sync_bracket_prices(ib: IB) -> None:
    """
    Read IBKR live order prices back into the tracker. Called before
    update_trailing_stops() so trailing logic always works from the real
    IBKR price, not a stale intended value.

    Also reattaches sl_order_id / tp_order_id from IBKR when the local cache
    is empty — makes bracket health resilient to restarts without reconcile.

    SL orders: auxPrice (STP/STPLMT trigger price)
    TP orders: lmtPrice (LMT price)
    """
    if not CONFIG.get("bracket_health_enabled", True):
        return
    try:
        ibkr_map = _build_ibkr_bracket_map(ib)
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

            symbol = pos.get("symbol", key)
            direction = pos.get("direction", "LONG")
            close_action = "SELL" if direction == "LONG" else "BUY"
            sym_brackets = ibkr_map.get(symbol, {"sl_orders": [], "tp_orders": []})

            # SL sync
            sl_candidates = [t for t in sym_brackets["sl_orders"] if t.order.action.upper() == close_action]
            if sl_candidates:
                sl_oid = pos.get("sl_order_id")
                sl_match = next((t for t in sl_candidates if t.order.orderId == sl_oid), sl_candidates[0])
                if not sl_oid or sl_oid != sl_match.order.orderId:
                    log.info("[BRACKET_SYNC] %s sl_order_id reattached: #%d", symbol, sl_match.order.orderId)
                    pos["sl_order_id"] = sl_match.order.orderId
                    changed = True
                ibkr_sl = float(sl_match.order.auxPrice or 0)
                if 0 < ibkr_sl < 1e10 and abs(ibkr_sl - (pos.get("sl") or 0)) > 0.005:
                    log.info(
                        "[BRACKET_SYNC] %s sl corrected: tracker=%.2f ibkr=%.2f",
                        symbol, pos.get("sl") or 0, ibkr_sl,
                    )
                    pos["sl"] = round(ibkr_sl, 4)
                    changed = True

            # TP sync
            tp_candidates = [t for t in sym_brackets["tp_orders"] if t.order.action.upper() == close_action]
            if tp_candidates:
                tp_oid = pos.get("tp_order_id")
                tp_match = next((t for t in tp_candidates if t.order.orderId == tp_oid), tp_candidates[0])
                if not tp_oid or tp_oid != tp_match.order.orderId:
                    log.info("[BRACKET_SYNC] %s tp_order_id reattached: #%d", symbol, tp_match.order.orderId)
                    pos["tp_order_id"] = tp_match.order.orderId
                    changed = True
                ibkr_tp = float(tp_match.order.lmtPrice or 0)
                if 0 < ibkr_tp < 1e10 and abs(ibkr_tp - (pos.get("tp") or 0)) > 0.005:
                    log.info(
                        "[BRACKET_SYNC] %s tp corrected: tracker=%.2f ibkr=%.2f",
                        symbol, pos.get("tp") or 0, ibkr_tp,
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
    Pass 1 — IBKR-first bracket audit per position:
      sl_count == 0  → submit new SL (ATR-based price, cooldown-gated)
      sl_count == 1  → reattach ID to local cache (no resubmit needed)
      sl_count  > 1  → cancel duplicates immediately, keep best price match

    TP follows the same logic with one extra guard: when tp_order_id is unknown,
    we reattach but do NOT cancel extras (a TRIMMING position may have a pending
    tranche LMT that looks identical to a TP order).

    Pass 3 — close positions where the stop level was breached but IBKR didn't fire.
    """
    if not CONFIG.get("bracket_health_enabled", True):
        return
    try:
        ibkr_map = _build_ibkr_bracket_map(ib)
    except Exception as _e:
        log.warning("[BRACKET_AUDIT] Failed to build IBKR bracket map: %s", _e)
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

            close_action = "SELL" if direction == "LONG" else "BUY"
            sym_brackets = ibkr_map.get(symbol, {"sl_orders": [], "tp_orders": []})
            sl_candidates = [t for t in sym_brackets["sl_orders"] if t.order.action.upper() == close_action]
            tp_candidates = [t for t in sym_brackets["tp_orders"] if t.order.action.upper() == close_action]
            sl_count = len(sl_candidates)
            tp_count = len(tp_candidates)

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
                continue  # don't audit brackets when already trying to close

            # ── Pass 1: SL bracket ────────────────────────────────────────────
            if sl_count == 0:
                if not _in_cooldown(symbol):
                    if not atr:
                        log.warning("[BRACKET_AUDIT] %s sl missing but no ATR — skipping", symbol)
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
                                all_sell_count = sum(
                                    1 for t in ib.openTrades()
                                    if t.contract.symbol == symbol
                                    and t.order.action.upper() == close_action
                                    and t.orderStatus.status in ("Submitted", "PreSubmitted")
                                )
                                if all_sell_count >= 13:
                                    log.warning(
                                        "[BRACKET_AUDIT] %s skipping new sl — %d open %s orders already (IBKR limit 15)",
                                        symbol, all_sell_count, close_action,
                                    )
                                    _mark_retry(symbol)
                                    continue
                                contract = get_contract(symbol, "stock")
                                ib.qualifyContracts(contract)
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
                                        "[BRACKET_AUDIT] %s sl submitted: %.2f (order #%d)",
                                        symbol, new_sl, sl_trade.order.orderId,
                                    )
                                else:
                                    log.warning(
                                        "[BRACKET_AUDIT] %s sl submit rejected (status=%r)",
                                        symbol, sl_status,
                                    )
                            except Exception as _sle:
                                log.error("[BRACKET_AUDIT] %s sl submit failed: %s", symbol, _sle)
                            _mark_retry(symbol)

            elif sl_count == 1:
                found_id = sl_candidates[0].order.orderId
                if sl_oid != found_id:
                    _safe_update_trade(key, {"sl_order_id": found_id})
                    changed = True
                    log.info("[BRACKET_AUDIT] %s sl_order_id reattached: #%d (no resubmit needed)", symbol, found_id)

            else:  # sl_count > 1 — cancel duplicates immediately
                best_sl = _pick_best_sl(sl_candidates, sl)
                for t in sl_candidates:
                    if t.order.orderId != best_sl.order.orderId:
                        try:
                            ib.cancelOrder(t.order)
                            ib.sleep(0.3)
                            log.info("[BRACKET_AUDIT] %s duplicate sl cancelled: #%d", symbol, t.order.orderId)
                        except Exception as _ce:
                            log.warning("[BRACKET_AUDIT] %s duplicate sl cancel failed: %s", symbol, _ce)
                _safe_update_trade(key, {"sl_order_id": best_sl.order.orderId})
                changed = True
                log.info(
                    "[BRACKET_AUDIT] %s duplicate sl resolved: kept #%d (%d removed)",
                    symbol, best_sl.order.orderId, sl_count - 1,
                )

            # ── Pass 1: TP bracket ────────────────────────────────────────────
            if tp_count == 0 and pos.get("status") == "ACTIVE":
                if not _in_cooldown(symbol):
                    if not atr:
                        log.warning("[BRACKET_AUDIT] %s tp missing but no ATR — skipping", symbol)
                    else:
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
                                contract = get_contract(symbol, "stock")
                                ib.qualifyContracts(contract)
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
                                        "[BRACKET_AUDIT] %s tp submitted: %.2f (order #%d)",
                                        symbol, new_tp, tp_trade.order.orderId,
                                    )
                                else:
                                    log.warning(
                                        "[BRACKET_AUDIT] %s tp submit rejected (status=%r)",
                                        symbol, tp_status,
                                    )
                            except Exception as _tpe:
                                log.error("[BRACKET_AUDIT] %s tp submit failed: %s", symbol, _tpe)
                            _mark_retry(symbol)

            elif tp_count >= 1:
                id_match = next((t for t in tp_candidates if t.order.orderId == tp_oid), None)
                if tp_oid and id_match:
                    # Known TP is live in IBKR — cancel any extras
                    if tp_count > 1:
                        for t in tp_candidates:
                            if t.order.orderId != tp_oid:
                                try:
                                    ib.cancelOrder(t.order)
                                    ib.sleep(0.3)
                                    log.info("[BRACKET_AUDIT] %s duplicate tp cancelled: #%d", symbol, t.order.orderId)
                                except Exception as _ce:
                                    log.warning("[BRACKET_AUDIT] %s duplicate tp cancel failed: %s", symbol, _ce)
                        changed = True
                        log.info("[BRACKET_AUDIT] %s duplicate tp resolved: kept #%d", symbol, tp_oid)
                else:
                    # No known ID or stale — reattach first candidate, don't cancel
                    # (a TRIMMING position may have a tranche LMT that looks like a TP)
                    found_id = tp_candidates[0].order.orderId
                    if tp_oid != found_id:
                        _safe_update_trade(key, {"tp_order_id": found_id})
                        changed = True
                        log.info("[BRACKET_AUDIT] %s tp_order_id reattached: #%d (no resubmit needed)", symbol, found_id)

        except Exception as _exc:
            log.error("[BRACKET_AUDIT] %s audit loop failed: %s", key, _exc)
            continue

    if changed:
        try:
            _save_positions_file()
        except Exception as _e:
            log.warning("[BRACKET_AUDIT] positions save failed: %s", _e)
