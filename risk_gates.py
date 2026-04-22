# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  risk_gates.py                              ║
# ║   Risk-triggered portfolio actions                           ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Risk-triggered portfolio actions.

These functions belong to the risk layer conceptually but require
orders to execute, so they cannot live in risk.py (which orders.py
already imports from). risk_gates.py sits above both:

    risk_gates → orders → orders_core → risk
    risk_gates → risk

No circularity. bot_trading.py calls these in response to risk signals.
"""

from __future__ import annotations

from bot_state import clog
from config import CONFIG
from learning import log_trade
from orders_portfolio import close_position, get_open_positions, reconcile_with_ibkr
from risk import _get_ibkr_cash


def auto_rebalance_cash(ib, portfolio_value: float, regime: dict) -> None:
    """
    Auto-close the weakest position(s) to bring cash reserve back above
    min_cash_reserve. Closes positions one by one (worst P&L first) until
    cash is restored or no positions remain.

    Options are skipped — they cannot be closed outside regular hours.
    """
    min_reserve = CONFIG.get("min_cash_reserve", 0.10)
    positions = get_open_positions()

    if not positions:
        clog("RISK", "Auto-rebalance: No positions to close")
        return

    def _current_cash_pct() -> float:
        cash = _get_ibkr_cash(ib, CONFIG.get("active_account", ""))
        if cash is not None:
            return cash / portfolio_value if portfolio_value > 0 else 1.0
        open_pos = get_open_positions()
        deployed = sum(p.get("current", p.get("entry", 0)) * p.get("qty", 0) for p in open_pos)
        return (portfolio_value - deployed) / portfolio_value if portfolio_value > 0 else 1.0

    cash_pct = _current_cash_pct()
    cash_deficit = (min_reserve - cash_pct) * portfolio_value
    clog(
        "RISK",
        f"Auto-rebalance: cash={cash_pct * 100:.1f}% (need {min_reserve * 100:.0f}%) "
        f"— need to free ~${cash_deficit:,.0f}",
    )

    ranked = []
    for p in positions:
        if p.get("instrument") == "option":
            continue  # Options can't close outside regular hours — stocks only
        entry = p.get("entry", 0)
        current = p.get("current", entry)
        qty = p.get("qty", 0)
        if entry > 0 and qty != 0:
            ranked.append(
                {
                    "symbol": p.get("_trade_key", p.get("symbol")),
                    "pnl_pct": (current - entry) / entry,
                    "position_value": abs(current * qty),
                }
            )

    if not ranked:
        clog("RISK", "Auto-rebalance: Could not evaluate positions")
        return

    ranked.sort(key=lambda x: x["pnl_pct"])

    for candidate in ranked:
        if cash_pct >= min_reserve:
            break
        sym = candidate["symbol"]
        clog(
            "RISK",
            f"Auto-rebalance: Closing {sym} (P&L: {candidate['pnl_pct']:+.1%}, "
            f"value: ${candidate['position_value']:,.0f}) to free cash",
        )
        try:
            result = close_position(ib, sym)
            if result:
                clog("RISK", f"Auto-rebalance: {result}")
                ib.sleep(2)
                cash_pct = _current_cash_pct()
                clog("RISK", f"Auto-rebalance: cash now at {cash_pct * 100:.1f}%")
                # BC-7: Auto-rebalance exits must reach the IC learning loop.
                # close_position() calls log_order() (compliance) but NOT log_trade().
                # Without this call, force-closed positions leave no exit record —
                # the learning loop never calculates forward return and dimension IC
                # is silently biased toward the normal execution path only.
                _full_pos = next((p for p in positions if p.get("_trade_key", p.get("symbol")) == sym), None)
                if _full_pos:
                    _entry = _full_pos.get("entry", 0)
                    _current = _full_pos.get("current", _entry)
                    _qty = _full_pos.get("qty", 0)
                    _pnl = (_current - _entry) * _qty if _entry > 0 and _qty != 0 else None
                    _pnl_pct = ((_current - _entry) / _entry) if _entry > 0 else None
                    try:
                        log_trade(
                            _full_pos,
                            agent_outputs={},
                            regime=regime,
                            action="CLOSE",
                            outcome={
                                "exit_price": _current,
                                "pnl": _pnl,
                                "pnl_pct": _pnl_pct,
                                "reason": "AUTO_REBALANCE_CASH",
                            },
                        )
                    except Exception as _lt_e:
                        clog("ERROR", f"Auto-rebalance: log_trade failed for {sym}: {_lt_e}")
            else:
                clog(
                    "ERROR",
                    f"Auto-rebalance: Could not close {sym} (not in IBKR — phantom entry?), purging and trying next",
                )
                reconcile_with_ibkr(ib)
                continue
        except Exception as e:
            clog("ERROR", f"Auto-rebalance: Failed to close {sym}: {e}, trying next")
            continue

    if cash_pct < min_reserve:
        clog("RISK", f"Auto-rebalance: cash still at {cash_pct * 100:.1f}% after closing all eligible positions")
