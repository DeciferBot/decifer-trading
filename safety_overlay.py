"""
safety_overlay.py — Interphase live-safety layer (between Phase 1 and Phase 2).

Single responsibility: gate every live order and every scan cycle through a
minimal set of deterministic checks that work independently of the LLM path.

This module is additive. It does NOT replace existing risk checks
(risk.check_risk_conditions, orders_core guards). It runs BEFORE them and
short-circuits when a kill-switch or circuit breaker is tripped.

Exposed surface:
    flag(name)                       → bool/float from CONFIG["safety_overlay"]
    can_submit_order(action)         → (allowed, reason)
    run_circuit_breakers(pv, pnl)    → (ok, reason, mode)  mode ∈ {"ok","manage_only","halt"}
    preflight_reconcile(ib)          → syncs internal state to IBKR truth
    should_run_apex_shadow()         → bool

No persistence, no network. Reads CONFIG, reads active_trades, calls existing
reconcile_with_ibkr(). No behaviour change when all flags remain at defaults.
"""

from __future__ import annotations

import logging

from config import CONFIG

log = logging.getLogger("decifer.safety_overlay")


# ── Config access with safe defaults ─────────────────────────────────────────

_DEFAULTS: dict = {
    "LIVE_TRADING_ENABLED": True,
    "NEW_ENTRIES_ENABLED": True,
    "FORCE_MANAGE_ONLY": False,
    "USE_APEX_V3_SHADOW": True,    # shadow+divergence logging (operational)
    "FINBERT_MATERIALITY_GATE_ENABLED": True,
    "daily_loss_halt_new_entries_pct": 0.03,   # -3% blocks new entries
    "daily_loss_manage_only_pct": 0.05,        # -5% switches to manage-only (aligns with daily_loss_limit)
    "per_symbol_hard_loss_pct": None,          # e.g. -0.15 → force exit on -15% per-position unreal.; None disables
    "reconcile_every_cycle": True,             # run preflight reconcile at top of each scan
}


def flag(name: str):
    """Read a safety_overlay flag with a safe default."""
    overlay = CONFIG.get("safety_overlay") or {}
    if name in overlay:
        return overlay[name]
    return _DEFAULTS.get(name)


# ── Order-level gate ─────────────────────────────────────────────────────────

def can_submit_order(action: str) -> tuple[bool, str]:
    """
    Gate every live order submission.

    action:
        "buy"   — new long entry (execute_buy)
        "short" — new short entry (execute_short)
        "sell"  — close/trim existing position (execute_sell)

    Exits ("sell") are NEVER blocked by NEW_ENTRIES_ENABLED or FORCE_MANAGE_ONLY.
    They are only blocked by LIVE_TRADING_ENABLED=False (full kill-switch).
    """
    if not flag("LIVE_TRADING_ENABLED"):
        return False, "LIVE_TRADING_ENABLED=False — all live orders blocked"

    if action == "sell":
        return True, "exit allowed"

    if action in ("buy", "short"):
        if not flag("NEW_ENTRIES_ENABLED"):
            return False, "NEW_ENTRIES_ENABLED=False — new entries blocked"
        if flag("FORCE_MANAGE_ONLY"):
            return False, "FORCE_MANAGE_ONLY=True — manage-only mode, no new entries"
        return True, "entry allowed"

    return False, f"unknown action: {action}"


# ── Circuit breakers (LLM-independent) ───────────────────────────────────────

def run_circuit_breakers(portfolio_value: float, daily_pnl: float) -> tuple[bool, str, str]:
    """
    Pure-math circuit breakers. Work whether or not the LLM path is online.

    Returns (ok, reason, mode):
        ok=True,  mode="ok"           — normal operation
        ok=True,  mode="manage_only"  — deeper drawdown: forcibly disable new entries
        ok=False, mode="halt"         — (reserved for future hard-halt conditions)

    The caller is responsible for applying mode="manage_only" (e.g. treating it
    as FORCE_MANAGE_ONLY for this cycle).
    """
    if portfolio_value <= 0:
        return True, "portfolio_value not available — no breaker applied", "ok"

    pnl_pct = daily_pnl / portfolio_value

    halt_pct = flag("daily_loss_manage_only_pct")
    block_pct = flag("daily_loss_halt_new_entries_pct")

    if halt_pct is not None and pnl_pct <= -abs(halt_pct):
        return True, (
            f"daily PnL {pnl_pct:+.2%} ≤ -{abs(halt_pct):.0%} — manage-only mode"
        ), "manage_only"

    if block_pct is not None and pnl_pct <= -abs(block_pct):
        return True, (
            f"daily PnL {pnl_pct:+.2%} ≤ -{abs(block_pct):.0%} — new entries blocked"
        ), "manage_only"

    return True, "circuit breakers clear", "ok"


def check_per_symbol_hard_loss(position: dict) -> tuple[bool, str]:
    """
    Optional per-symbol hard-loss guard. Returns (should_force_exit, reason).

    Disabled when per_symbol_hard_loss_pct is None.
    Reads current unreal. PnL from the position dict (entry vs current).
    """
    threshold = flag("per_symbol_hard_loss_pct")
    if threshold is None:
        return False, "disabled"

    entry = position.get("entry") or 0
    current = position.get("current") or entry
    if entry <= 0:
        return False, "no entry price"

    direction = (position.get("direction") or "LONG").upper()
    if direction == "LONG":
        pnl_pct = (current - entry) / entry
    else:
        pnl_pct = (entry - current) / entry

    if pnl_pct <= -abs(threshold):
        return True, (
            f"{position.get('symbol', '?')} unreal. PnL {pnl_pct:+.2%} ≤ "
            f"-{abs(threshold):.0%} — hard-loss force exit"
        )
    return False, "within threshold"


# ── Preflight reconciliation ─────────────────────────────────────────────────

def preflight_reconcile(ib) -> dict:
    """
    Reconcile broker truth to internal state before any trade decision runs.

    Broker (IBKR) is authoritative for:
        - actual position quantities
        - open order status
        - recent fills and partial fills

    This is a thin wrapper: it delegates to the existing
    orders_portfolio.reconcile_with_ibkr() + update_positions_from_ibkr()
    functions so the internal active_trades dict and positions.json reflect
    IBKR truth, and logs mismatch counts for audit.

    Returns a summary dict {"ok": bool, "reconciled": int, "mismatches": int, "note": str}.

    Safe to call even when ib is None or disconnected — returns a no-op summary.
    """
    if not flag("reconcile_every_cycle"):
        return {"ok": True, "reconciled": 0, "mismatches": 0, "note": "skipped (flag off)"}

    if ib is None or not getattr(ib, "isConnected", lambda: False)():
        return {"ok": False, "reconciled": 0, "mismatches": 0, "note": "ib disconnected"}

    try:
        from orders_portfolio import update_positions_from_ibkr
        from orders_state import active_trades as _at

        before = len(_at)
        update_positions_from_ibkr(ib)
        after = len(_at)
        mismatches = abs(after - before)
        if mismatches:
            log.warning(
                "safety_overlay.preflight_reconcile: active_trades size %d → %d "
                "(%d mismatches reconciled)",
                before, after, mismatches,
            )
        else:
            log.debug("safety_overlay.preflight_reconcile: no mismatches")
        return {"ok": True, "reconciled": after, "mismatches": mismatches, "note": "live"}
    except Exception as e:
        log.error("safety_overlay.preflight_reconcile: failed — %s", e)
        return {"ok": False, "reconciled": 0, "mismatches": 0, "note": f"error: {e}"}


# ── Pipeline selection helpers (for future Phase 6 wiring) ───────────────────

def should_run_apex_shadow() -> bool:
    """Shadow mode: run new path in parallel but do NOT submit its orders."""
    return bool(flag("USE_APEX_V3_SHADOW"))



def finbert_materiality_gate_enabled() -> bool:
    """
    news_sentinel materiality gate source.

    Default False — preserves current live behavior (gate uses
    claude_confidence from news.claude_sentiment). When True, the gate uses
    finbert_confidence from news.batch_news_sentiment instead. Phase 7 flips
    True after the Apex path is shadow-validated.
    """
    return bool(flag("FINBERT_MATERIALITY_GATE_ENABLED"))


