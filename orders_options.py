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
from datetime import UTC, date, datetime

from ib_async import IB, LimitOrder, MarketOrder, Option, StopLimitOrder, StopOrder

from bot_ibkr import cancel_with_reason
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

# Persistent blacklist: opt_keys condemned after repeated failed flush attempts.
# Survives restarts. Checked at re-queue time (execute_sell_option) and at IBKR
# reconcile time (orders_portfolio.reconcile_with_ibkr) so condemned positions
# never re-enter active_trades or pending_option_exits across bot restarts.
_BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), "data", "option_exit_blacklist.json")
_option_exit_blacklist: dict = {}  # opt_key → reason string
_flush_attempt_counts: dict = {}   # opt_key → flush attempts this session (session-scoped counter)
_MAX_FLUSH_ATTEMPTS = 3            # condemn after this many failed flush cycles in one session


def _is_option_expired(opt_key: str) -> bool:
    """Return True if the option's expiry date is in the past (ET)."""
    try:
        expiry_str = opt_key.rsplit("_", 1)[-1]
        expiry_date = date.fromisoformat(expiry_str)
        today_et = datetime.now(_ET).date()
        return today_et > expiry_date
    except Exception:
        return True  # unparseable expiry → treat as expired, safer than retrying indefinitely


def _drop_expired_pending_exits(_log=None) -> None:
    """Remove any deferred exits whose option has already expired. Cleans DB and file."""
    expired = [k for k in list(_pending_option_exits) if _is_option_expired(k)]
    if not expired:
        return
    for k in expired:
        _pending_option_exits.pop(k, None)
        if _log:
            _log.warning("orders_options: dropped expired deferred exit %s — option already expired", k)
        else:
            log.warning(f"Dropped expired deferred exit {k} — option already expired")


def _load_pending_exits() -> None:
    global _pending_option_exits
    _log = __import__("logging").getLogger(__name__)
    try:
        if os.path.exists(_PENDING_EXITS_FILE):
            with open(_PENDING_EXITS_FILE) as f:
                _pending_option_exits = __import__("json").load(f)
            if _pending_option_exits:
                _drop_expired_pending_exits(_log)
                _log.info(
                    "orders_options: loaded %d pending exit(s) from file: %s",
                    len(_pending_option_exits), ", ".join(_pending_option_exits.keys()),
                )
    except Exception:
        _pending_option_exits = {}


def _save_pending_exits() -> None:
    _log = __import__("logging").getLogger(__name__)
    try:
        os.makedirs(os.path.dirname(_PENDING_EXITS_FILE), exist_ok=True)
        tmp = _PENDING_EXITS_FILE + ".tmp"
        with open(tmp, "w") as f:
            __import__("json").dump(_pending_option_exits, f)
        os.replace(tmp, _PENDING_EXITS_FILE)
    except Exception as _e:
        _log.warning(f"orders_options: failed to persist pending exits to file: {_e}")


def _load_blacklist() -> None:
    global _option_exit_blacklist
    try:
        if os.path.exists(_BLACKLIST_FILE):
            with open(_BLACKLIST_FILE) as f:
                _option_exit_blacklist = __import__("json").load(f)
            if _option_exit_blacklist:
                log.warning(
                    "orders_options: %d opt_key(s) on exit blacklist: %s",
                    len(_option_exit_blacklist), ", ".join(_option_exit_blacklist.keys()),
                )
    except Exception:
        _option_exit_blacklist = {}


def _save_blacklist() -> None:
    try:
        os.makedirs(os.path.dirname(_BLACKLIST_FILE), exist_ok=True)
        tmp = _BLACKLIST_FILE + ".tmp"
        with open(tmp, "w") as f:
            __import__("json").dump(_option_exit_blacklist, f, indent=2)
        os.replace(tmp, _BLACKLIST_FILE)
    except Exception as _e:
        log.warning("orders_options: failed to persist option exit blacklist: %s", _e)


def blacklist_option_exit(opt_key: str, reason: str = "manual") -> None:
    """Add opt_key to the persistent exit blacklist. Survives restarts."""
    _option_exit_blacklist[opt_key] = reason
    _save_blacklist()
    log.warning("orders_options: %s added to option exit blacklist (%s)", opt_key, reason)


_load_pending_exits()
_load_blacklist()


def execute_buy_option(
    ib: IB,
    contract_info: dict,
    portfolio_value: float,
    reasoning: str = "",
    score: int = 0,
    trade_type: str = "SCALP",
    conviction: float = 0.0,
    signal_scores: dict | None = None,
    agent_outputs: dict | None = None,
    regime: str = "UNKNOWN",
    pattern_id: str = "",
    advice_id: str = "",
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

        exp_ok, exp_reason, exp_code = check_combined_exposure(
            symbol, est_option_value, list(active_trades.values()), portfolio_value, instrument="option"
        )
        if not exp_ok:
            log.warning(f"Combined exposure block for {symbol} options: {exp_reason}")
            # Propagate into orders_core._block_reason so signal_dispatcher sees it in
            # blocker_flags. Lazy sys.modules lookup avoids the circular import
            # (orders_core imports orders_options, not the other way around).
            import sys as _sys
            _oc = _sys.modules.get("orders_core") or _sys.modules.get("orders")
            if _oc is not None:
                _oc._block_reason[symbol] = exp_code or "exposure_block"
                _oc._exposure_block_details[symbol] = {
                    "exp_reason": exp_reason,
                    "exp_code": exp_code,
                    "proposed_trade_notional": est_option_value,
                    "portfolio_value": portfolio_value,
                    "instrument": "option",
                }
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

        # ── Write-ahead metadata commit ───────────────────────────────────────
        _trade_id_opt = f"{opt_key}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
        _opt_open_time = datetime.now(UTC).isoformat()
        try:
            from ic_calculator import get_current_weights as _get_icw_opt_wal
            _icw_wal = _get_icw_opt_wal()
        except Exception:
            _icw_wal = None
        try:
            from event_log import append_intent as _el_intent_opt
            _el_intent_opt(
                _trade_id_opt, symbol,
                direction="LONG",
                trade_type=trade_type or "SCALP",
                instrument="option",
                intended_price=contract_info.get("mid", 0.0),
                qty=n_contracts, sl=0.0, tp=0.0,
                regime=regime or "UNKNOWN",
                signal_scores=signal_scores or {},
                conviction=conviction,
                reasoning=reasoning or "",
                score=float(score),
                open_time=_opt_open_time,
            )
        except Exception as _wal_err_opt:
            log.warning("execute_buy_option %s: ORDER_INTENT write failed: %s", opt_key, _wal_err_opt)

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
            "sl": round(mid_price * (1 - CONFIG.get("options_stop_loss", 0.20)), 4),
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
            "ic_weights_at_entry": _icw_wal,
            "trade_type": trade_type,
            "conviction": conviction,
            "open_time": _opt_open_time,
            "trade_id": _trade_id_opt,  # stored so execute_sell_option can write POSITION_CLOSED
        }

        from orders_state import _save_positions_file

        _save_positions_file()

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
        if opt_key in _option_exit_blacklist:
            log.warning("execute_sell_option: %s is blacklisted — not re-queuing", opt_key)
            return False
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
    if pos.get("status") in ("RESERVED", "PENDING"):
        log.warning(
            f"execute_sell_option: {opt_key} BUY order not yet filled "
            f"(status={pos.get('status')}) — cannot sell while BUY is live in IBKR; "
            "deferred to next cycle"
        )
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

        # ── Get bid/ask: Alpaca (primary) → IBKR (secondary) → MKT fallback ──
        # Alpaca is the declared primary data source for all market data.
        # IBKR reqMktData for options requires a separate quote subscription
        # that is absent for many symbols (error 10091) — that is why blind
        # reliance on IBKR here causes stuck exits. Every other function in
        # the options stack (options.py, price_updater.py, iv_skew.py) already
        # uses Alpaca for option pricing; this function must do the same.
        import math as _m

        bid = ask = last = None
        _bid_ok = _ask_ok = _last_ok = False
        _ibkr_mkt_data_requested = False

        try:
            from alpaca_options import build_option_symbol, get_snapshot_greeks
            _occ_sym = build_option_symbol(
                pos["symbol"], pos["expiry_ibkr"], pos["right"], float(pos["strike"])
            )
            _snap = get_snapshot_greeks(_occ_sym)
            if _snap:
                _ab = float(_snap.get("bid") or 0)
                _aa = float(_snap.get("ask") or 0)
                if _ab > 0:
                    bid, ask = _ab, _aa
                    _bid_ok = True
                    _ask_ok = _aa > 0
        except Exception:
            pass

        if not _bid_ok:
            ticker = ib.reqMktData(option_contract, "", False, False)
            ib.sleep(2)
            _ibkr_mkt_data_requested = True
            _ib_bid = getattr(ticker, "bid", None)
            _ib_ask = getattr(ticker, "ask", None)
            last = getattr(ticker, "last", None)
            if _ib_bid is not None and not _m.isnan(_ib_bid) and _ib_bid > 0:
                bid, ask = _ib_bid, _ib_ask
                _bid_ok = True
                _ask_ok = _ib_ask is not None and not _m.isnan(_ib_ask) and _ib_ask > 0
            if last is not None and not _m.isnan(last) and last > 0:
                _last_ok = True

        # Determine order direction: SHORT positions close with BUY, LONG with SELL
        _is_short = pos.get("direction", "LONG").upper() == "SHORT"
        _close_action = "BUY" if _is_short else "SELL"
        _retry_count = attempts["count"]
        # Only step price if the last attempt had ZERO fills (market rejected the price).
        # Partial fills mean the market IS trading at the current level — don't step further.
        _step = 0 if attempts.get("had_partial") else _retry_count
        _use_market_order = False
        if _is_short:
            # BUY-to-close: offer at ask, step 5% ABOVE ask each retry to chase fills
            _premium = round(1.0 + (_step * 0.05), 2)
            if _ask_ok:
                limit_price = round(ask * _premium, 2)
            elif _last_ok:
                limit_price = round(last * 1.03 * _premium, 2)
            else:
                # No market data at all (e.g. IBKR 10091 no options quote subscription).
                # A stale limit price from current_premium will always miss the market.
                # Market order guarantees exit — the lack of a quote subscription does
                # NOT block order placement, only streaming quotes.
                _use_market_order = True
                limit_price = round(pos.get("current_premium", 0.10) * 1.10, 2)  # for logging only
                log.warning(
                    "execute_sell_option %s: no bid/ask/last from IBKR — using MKT order for guaranteed exit",
                    opt_key,
                )
        else:
            # SELL-to-close: price at bid and step down aggressively
            _discount = round(1.0 - (_step * 0.05), 2)
            if _bid_ok:
                limit_price = round(bid * _discount, 2)
            elif _last_ok:
                limit_price = round(last * 0.97 * _discount, 2)
            else:
                # No market data at all (e.g. IBKR 10091 no options quote subscription).
                # A stale limit price from current_premium will always miss the market.
                # Market order guarantees exit — the lack of a quote subscription does
                # NOT block order placement, only streaming quotes.
                _use_market_order = True
                limit_price = round(pos.get("current_premium", 0.01) * 0.95, 2)  # for logging only
                log.warning(
                    "execute_sell_option %s: no bid/ask/last from IBKR — using MKT order for guaranteed exit",
                    opt_key,
                )

            if not _use_market_order:
                # On final attempt, if bid is near-zero accept $0.01 for near-worthless options
                if _retry_count >= _MAX_OPTION_SELL_RETRIES - 1 and _bid_ok and bid <= 0.02:
                    limit_price = 0.01
                # Floor at $0.05 — IBKR minimum tick for US equity options (below $3)
                if not (_bid_ok and bid < 0.05):
                    limit_price = max(limit_price, 0.05)

        if _ibkr_mkt_data_requested:
            ib.cancelMktData(option_contract)

        # Cancel all live IBKR orders on this exact contract (both directions) before
        # placing the close. IBKR rejects "Cannot have open orders on both sides of
        # the same US Option contract" if a stale entry order or bracket is still open.
        try:
            for _ot in ib.openTrades():
                _c = _ot.contract
                if (
                    getattr(_c, "symbol", None) == pos["symbol"]
                    and str(getattr(_c, "lastTradeDateOrContractMonth", "")) == pos.get("expiry_ibkr", "")
                    and getattr(_c, "strike", None) == pos.get("strike")
                    and getattr(_c, "right", None) == pos.get("right")
                ):
                    cancel_with_reason(ib, _ot.order, f"pre-cancel before close of {opt_key}")
                    log.info("[pre-cancel] cleared open order #%d on %s before close", _ot.order.orderId, opt_key)
            ib.sleep(0.5)
        except Exception as _pre_e:
            log.warning("execute_sell_option %s: pre-cancel sweep failed (non-fatal): %s", opt_key, _pre_e)

        if _use_market_order:
            sell_order = MarketOrder(_close_action, sell_contracts, account=CONFIG["active_account"], tif="DAY")
            sell_order.outsideRth = False
            opt_sell_trade = ib.placeOrder(option_contract, sell_order)
            log.info(
                f"Option MKT {_close_action} placed: {opt_key} x{sell_contracts} "
                f"(no market data — MKT for guaranteed exit, ref_price=${limit_price:.2f})"
            )
        else:
            sell_order = LimitOrder(_close_action, sell_contracts, limit_price, account=CONFIG["active_account"], tif="DAY")
            sell_order.outsideRth = False
            opt_sell_trade = ib.placeOrder(option_contract, sell_order)
            log.info(
                f"Option LMT {_close_action} placed: {opt_key} x{sell_contracts} @ ${limit_price:.2f} "
                f"(bid={bid}, ask={ask}, direction={pos.get('direction', 'LONG')})"
            )

        # Wait for fill confirmation — MKT orders need longer for paper account processing
        max_wait = 30 if _use_market_order else (15 if attempts["count"] == 0 else 25)
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
                cancel_with_reason(ib, opt_sell_trade.order, f"option sell not filled after {attempts['count']} attempts (status={order_status})")
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
                cancel_with_reason(ib, opt_sell_trade.order, "option sell false fill (paper account zero avgFillPrice)")
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
            _close_trade_id_opt = pos.get("trade_id") or f"{opt_key}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
            _now_ts_opt = datetime.now(UTC).isoformat()
            try:
                from event_log import append_close as _el_close_opt
                _el_close_opt(_close_trade_id_opt, pos["symbol"],
                              exit_price=round(current, 4), pnl=round(pnl, 2),
                              exit_reason=reason, hold_minutes=0)
            except Exception as _el_err_opt:
                log.warning("event_log.append_close failed for %s: %s", opt_key, _el_err_opt)
            try:
                import training_store as _ts_opt
                _ts_opt.append({
                    "trade_id": _close_trade_id_opt, "symbol": pos["symbol"],
                    "direction": pos.get("direction", "LONG"),
                    "trade_type": pos.get("trade_type") or "SCALP",
                    "instrument": "option",
                    "fill_price": float(pos.get("entry", 0.0)),
                    "intended_price": float(pos.get("intended_price") or pos.get("entry", 0.0)),
                    "exit_price": round(current, 4), "pnl": round(pnl, 2),
                    "hold_minutes": 0, "exit_reason": reason,
                    "regime": pos.get("entry_regime", "UNKNOWN"),
                    "signal_scores": pos.get("signal_scores") or {},
                    "conviction": float(pos.get("conviction") or 0.0),
                    "score": float(pos.get("score") or pos.get("entry_score") or 0.0),
                    "ts_fill": pos.get("open_time") or _now_ts_opt,
                    "ts_close": _now_ts_opt,
                })
            except Exception as _ts_err_opt:
                log.warning("training_store.append failed for %s: %s", opt_key, _ts_err_opt)
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
        if _is_option_expired(opt_key):
            log.warning(f"Deferred exit {opt_key} dropped — option has expired")
            _pending_option_exits.pop(opt_key, None)
            _save_pending_exits()
            continue
        if opt_key not in active_trades:
            log.info(f"Deferred exit {opt_key} dropped — position no longer tracked")
            _pending_option_exits.pop(opt_key, None)
            _save_pending_exits()
            continue
        if opt_key in _option_exit_blacklist:
            log.warning("Deferred exit %s is blacklisted — dropping without execute", opt_key)
            _pending_option_exits.pop(opt_key, None)
            _save_pending_exits()
            continue
        _flush_attempt_counts[opt_key] = _flush_attempt_counts.get(opt_key, 0) + 1
        if _flush_attempt_counts[opt_key] > _MAX_FLUSH_ATTEMPTS:
            blacklist_option_exit(opt_key, reason=f"flush_loop:{_flush_attempt_counts[opt_key]}_attempts")
            _pending_option_exits.pop(opt_key, None)
            _save_pending_exits()
            try:
                from learning import _append_audit_event
                _append_audit_event(
                    "deferred_exit_stuck",
                    opt_key=opt_key,
                    symbol=opt_key.split("_")[0],
                    flush_attempts=_flush_attempt_counts[opt_key],
                    note="Blacklisted after exceeding max flush attempts — IBKR paper account may still hold this position.",
                )
            except Exception:
                pass
            continue
        log.info(f"Flushing deferred option exit: {opt_key} (original reason: {reason})")
        _pending_option_exits.pop(opt_key, None)
        _save_pending_exits()
        _flushed = execute_sell_option(ib, opt_key, reason=f"deferred:{reason}")
        if _flushed:
            try:
                from bot_voice import speak_natural as _speak_deferred
                _sym = opt_key.split("_")[0]
                _speak_deferred(
                    "deferred_exit",
                    fallback=f"Closing {_sym} option — deferred exit from earlier.",
                    symbol=_sym,
                    reason=reason,
                )
            except Exception:
                pass


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

            direction = trade.get("direction", "LONG")
            if direction == "SHORT":
                t1_pnl = round((entry - tp_t1) * t1_qty, 2)
            else:
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

            # Place standalone T2 stop — BUY to cover for SHORT, SELL to exit for LONG
            t2_side = "BUY" if direction == "SHORT" else "SELL"
            contract = get_contract(symbol)
            t2_sl_limit = round(sl_price * 0.99, 2) if direction == "LONG" else round(sl_price * 1.01, 2)
            t2_stop = StopLimitOrder(
                t2_side,
                t2_qty,
                sl_price,
                t2_sl_limit,
                account=CONFIG["active_account"],
                tif="GTC",
                outsideRth=True,
            )
            t2_stop.transmit = True
            trade_id = active_trades.get(symbol, {}).get("trade_id", "")
            t2_stop.orderRef = f"SL:{trade_id}"[:20]
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
                if new_sl >= current:
                    continue
            else:  # SHORT
                new_hwm = min(hwm, current)
                new_sl = round(new_hwm + trail_mult * atr, 2)
                if new_sl >= old_sl:
                    continue
                if new_sl <= current:
                    # SL is already below current price — no protection; skip until trade moves in favor
                    continue

            if not ib.isConnected():
                log.warning("[TRAIL] IBKR disconnected — skipping trailing stop update")
                return

            close_action = "BUY" if direction == "SHORT" else "SELL"
            contract = get_contract(symbol)
            sl_limit = round(new_sl * 0.99, 2) if direction == "LONG" else round(new_sl * 1.01, 2)
            modified_stop = StopLimitOrder(
                close_action,
                qty,
                new_sl,
                sl_limit,
                account=CONFIG["active_account"],
                tif="GTC",
                outsideRth=True,
            )
            modified_stop.orderId = sl_order_id
            modified_stop.transmit = True
            mod_trade = ib.placeOrder(contract, modified_stop)
            ib.sleep(0.4)

            mod_status = mod_trade.orderStatus.status if mod_trade else ""
            if mod_status not in ("Submitted", "PreSubmitted", "Filled"):
                log.warning(
                    f"[TRAIL] {symbol} stop modify rejected by IBKR "
                    f"(status={mod_status!r}) — keeping sl={old_sl:.2f}"
                )
                continue

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


# ── Add-to-position helpers ──────────────────────────────────────────────────

def _get_open_option_position(symbol: str) -> tuple[str, dict] | None:
    """Return (opt_key, position_dict) for an open options position on symbol, or None."""
    with _trades_lock:
        for key, pos in active_trades.items():
            if (
                pos.get("symbol") == symbol
                and pos.get("instrument") == "option"
                and pos.get("status") not in ("EXITING", "RESERVED")
            ):
                return key, dict(pos)
    return None


def ask_opus_add_to_option(
    symbol: str,
    position: dict,
    signal_score: int,
    signal_breakdown: dict,
    direction: str,
    regime: str,
) -> dict:
    """
    Ask Opus whether to add contracts to an existing options position.

    Returns {"action": "ADD", "contracts": N, "reasoning": "..."}
         or {"action": "HOLD", "reasoning": "..."}.
    Falls back to HOLD on any error — never blocks execution on a bad API call.
    """
    import json as _json2

    import anthropic

    entry = position.get("entry_premium", 0)
    current = position.get("current_premium", entry)
    pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0.0
    contracts_held = position.get("contracts", 1)
    max_add = max(1, round(contracts_held * 0.5))

    breakdown_lines = "\n".join(
        f"  {dim}: {val}" for dim, val in sorted(signal_breakdown.items(), key=lambda x: -x[1]) if val > 0
    )

    prompt = f"""You are managing an existing options position and must decide whether to add contracts.

EXISTING POSITION:
  Symbol   : {symbol}
  Contract : {position.get('right','?')} ${position.get('strike','?')} exp {position.get('expiry_str','?')} ({position.get('dte','?')} DTE)
  Held     : {contracts_held} contracts
  Entry    : ${entry:.2f}  |  Current: ${current:.2f}  |  P&L: {pnl_pct:+.1f}%
  Delta    : {position.get('delta','?')}

CURRENT SIGNAL (score={signal_score}/60, direction={direction}):
{breakdown_lines or '  (no breakdown available)'}

REGIME: {regime}

RULES (non-negotiable):
- Never add to a losing position (current premium < entry premium)
- Max add is {max_add} contracts (50% of current holding, rounded up)
- Only ADD if signal has materially strengthened — score increase, new dimension firing, or clear momentum continuation
- HOLD if signal is flat, weakened, or there is any ambiguity

Respond with valid JSON only, no markdown:
{{"action": "ADD", "contracts": N, "reasoning": "..."}}
or
{{"action": "HOLD", "reasoning": "..."}}"""

    try:
        client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        message = client.messages.create(
            model=CONFIG.get("llm_advisor_model", "claude-sonnet-4-6"),
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        result = _json2.loads(text)
        action = result.get("action", "HOLD").upper()
        if action == "ADD":
            contracts = max(1, min(int(result.get("contracts", 1)), max_add))
            log.info(
                f"Opus add-to-option {symbol}: ADD {contracts} contracts — {result.get('reasoning','')[:120]}"
            )
            return {"action": "ADD", "contracts": contracts, "reasoning": result.get("reasoning", "")}
        else:
            log.info(f"Opus add-to-option {symbol}: HOLD — {result.get('reasoning','')[:120]}")
            return {"action": "HOLD", "reasoning": result.get("reasoning", "")}
    except Exception as exc:
        log.warning(f"ask_opus_add_to_option {symbol} failed ({exc}) — defaulting to HOLD")
        return {"action": "HOLD", "reasoning": str(exc), "_opus_failed": True}


def execute_add_to_option(
    ib: IB,
    opt_key: str,
    contract_info: dict,
    add_contracts: int,
    reasoning: str = "",
    score: int = 0,
) -> bool:
    """
    Add contracts to an existing options position.
    Places a new DAY limit order at the ask for add_contracts.
    Updates active_trades qty/contracts in place — does not reset SL/TP.
    Returns True if order placed successfully.
    """
    if opt_key not in active_trades:
        log.warning(f"execute_add_to_option: {opt_key} not in active_trades — aborting")
        return False

    symbol = contract_info["symbol"]
    ask_price = contract_info.get("ask", 0.0)
    mid_price = contract_info.get("mid", 0.0)
    limit_price = round(ask_price if ask_price > mid_price > 0 else mid_price * 1.05, 2)

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

        entry_order = LimitOrder("BUY", add_contracts, limit_price, account=account, tif="DAY", outsideRth=False)
        trade = ib.placeOrder(option_contract, entry_order)
        ib.sleep(1)

        order_status = trade.orderStatus.status
        if order_status in ("Cancelled", "Inactive", "ApiCancelled", "ValidationError"):
            log.error(f"Add-to-option order rejected by IBKR for {opt_key}: {order_status}")
            return False

        log_order(
            {
                "order_id": trade.order.orderId,
                "symbol": symbol,
                "side": "ADD",
                "order_type": "LMT",
                "qty": add_contracts,
                "price": limit_price,
                "status": "SUBMITTED",
                "instrument": "option",
                "right": contract_info["right"],
                "strike": contract_info["strike"],
                "expiry": contract_info["expiry_str"],
                "mid": mid_price,
                "ask": ask_price,
                "reasoning": reasoning,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        with _trades_lock:
            if opt_key in active_trades:
                active_trades[opt_key]["contracts"] = active_trades[opt_key].get("contracts", 0) + add_contracts
                active_trades[opt_key]["qty"] = active_trades[opt_key]["contracts"]

        from orders_state import _save_positions_file

        _save_positions_file()

        log.info(
            f"✅ ADD {add_contracts} contracts {opt_key} @ ${limit_price:.2f} "
            f"(total now {active_trades.get(opt_key, {}).get('contracts', '?')})"
        )
        return True

    except Exception as exc:
        log.error(f"execute_add_to_option {symbol}: {exc}")
        return False
