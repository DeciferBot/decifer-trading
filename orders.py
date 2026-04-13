# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  orders.py                                  ║
# ║   Order execution — limit orders, OCO brackets, exits        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

# ── Contract/price utilities (pure, no shared state) ─────────────────────────
# ── Core order execution ──────────────────────────────────────────────────────
# ── Duplicate order guards (reads orders_state) ───────────────────────────────
# ── Options execution ─────────────────────────────────────────────────────────
# ── Position tracking and reconciliation ─────────────────────────────────────

# ── Shared state (all mutable state lives in orders_state) ───────────────────

# ── Order lifecycle utilities ─────────────────────────────────────────────────


def cancel_order_by_id(ib, order_id) -> bool:
    """
    Cancel an open IBKR order by orderId.
    Returns True if the order was found and cancellation was requested.
    Callers should call sync_orders_from_ibkr() and update open_trades
    after a successful cancel.
    """
    for t in ib.openTrades():
        if t.order.orderId == order_id:
            ib.cancelOrder(t.order)
            ib.sleep(1)
            return True
    return False
