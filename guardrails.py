"""
guardrails.py — Layer 1.5: deterministic gates between L1 signals and L2 Apex.

Single responsibility: orchestrate existing risk / entry / portfolio checks into
five well-named pass/reject gates. No new logic — this module wraps logic that
already lives in risk.py, entry_gate.py, orders_state.py, orders_guards.py, and
portfolio_manager.py.

Public surface (called by the orchestrator in Phase 6):
    check_system_gates(portfolio_state, regime)          → (ok, reason)
    filter_candidates(scored_signals, open_symbols)       → list[ScannerPayload]
    screen_open_positions(open_positions)                 → list[(sym, reason)]
    flag_positions_for_review(open_positions, regime)     → list[TrackBPositionInput]
    filter_semantic_violations(decision, candidates_by_symbol) → ApexDecision

Helper:
    compute_allowed_trade_types(symbol, regime, minutes_to_close) → list[str]

Phase 2 scope: create the module and its five functions. Phase 6 wires it into
bot_trading.run_scan_cycle(). Existing callers (bot_trading, agents, sentinel)
are NOT rewired here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from config import CONFIG

log = logging.getLogger("decifer.guardrails")


_EST_OFFSET_HOURS = -5  # EST; EDT uses -4. For minutes-to-close we tolerate 1h skew.
_LONG_ONLY = set(CONFIG.get("long_only_symbols", {"SPXS", "SQQQ", "UVXY"}))
_NO_OPTIONS = set(CONFIG.get("no_options_symbols", set()))


# ── 1. System-level gates ─────────────────────────────────────────────────────

def check_system_gates(portfolio_state: dict, regime: dict, ib: Any = None) -> tuple[bool, str]:
    """Wraps risk.check_risk_conditions(). Returns (ok, reason_if_blocked)."""
    from risk import check_risk_conditions

    pv = portfolio_state.get("portfolio_value", 0.0)
    pnl = portfolio_state.get("daily_pnl", 0.0)
    open_positions = portfolio_state.get("open_positions") or []
    return check_risk_conditions(pv, pnl, regime, open_positions=open_positions, ib=ib)


# ── 2. Per-symbol candidate filter ────────────────────────────────────────────

def filter_candidates(
    scored_signals: list[dict],
    open_symbols: set[str],
    regime: dict | None = None,
    minutes_to_close: int | None = None,
) -> list[dict]:
    """Drop candidates that fail any deterministic gate. Tag survivors with
    allowed_trade_types / default_trade_type / options_eligible."""
    from orders_guards import has_open_order_for
    from orders_state import _is_recently_closed, is_failed_thesis_blocked

    kept: list[dict] = []
    for sig in scored_signals:
        sym = sig.get("symbol")
        if not sym:
            continue
        if _is_recently_closed(sym):
            log.info("filter_candidates: %s dropped — cooldown", sym); continue
        blocked, _r = is_failed_thesis_blocked(sym, sig.get("price", 0.0))
        if blocked:
            log.info("filter_candidates: %s dropped — failed-thesis cooldown", sym); continue
        if has_open_order_for(sym):
            log.info("filter_candidates: %s dropped — open order exists", sym); continue
        tc = sig.get("trade_context") or {}
        if tc.get("earnings_days_away") == 0:
            log.info("filter_candidates: %s dropped — earnings same day", sym); continue
        direction = (sig.get("direction") or "").upper()
        if direction == "SHORT" and sym in _LONG_ONLY:
            log.info("filter_candidates: %s dropped — long-only inverse ETF short", sym); continue

        allowed = compute_allowed_trade_types(sym, regime or {}, minutes_to_close)
        if not allowed or allowed == ["AVOID"]:
            log.info("filter_candidates: %s dropped — no allowed trade types", sym); continue

        sig["allowed_trade_types"] = allowed
        sig["default_trade_type"] = _default_trade_type(sig)
        sig["options_eligible"] = (sym not in _NO_OPTIONS) and (sym not in _LONG_ONLY)
        kept.append(sig)
    return kept


def compute_allowed_trade_types(
    symbol: str,
    regime: dict,
    minutes_to_close: int | None,
) -> list[str]:
    """Return allowed trade types for this symbol in this regime at this time."""
    allowed = ["INTRADAY", "SWING", "POSITION"]
    reg = (regime.get("regime") or "").upper()
    if reg in ("PANIC", "CAPITULATION") or symbol in _LONG_ONLY:
        if "POSITION" in allowed:
            allowed.remove("POSITION")
    if reg == "PANIC" and "SWING" in allowed:
        allowed.remove("SWING")
    if minutes_to_close is not None:
        if minutes_to_close < 30 and "INTRADAY" in allowed:
            allowed.remove("INTRADAY")
        if minutes_to_close < 60 and "POSITION" in allowed:
            allowed.remove("POSITION")
    allowed.append("AVOID")
    return allowed


def _default_trade_type(sig: dict) -> str:
    """Deterministic suggestion shown to Apex. Uses entry_gate.classify_trade_type()
    when a TradeContext is attached; falls back to INTRADAY."""
    try:
        from entry_gate import classify_trade_type
        from trade_context import TradeContext  # noqa: F401
        tc_raw = sig.get("trade_context") or {}
        if not tc_raw:
            return "INTRADAY"
        # trade_context may arrive as dict; wrap into the dataclass shape entry_gate expects.
        from types import SimpleNamespace
        ctx = SimpleNamespace(
            symbol=sig.get("symbol"),
            earnings_days_away=tc_raw.get("earnings_days_away"),
            time_of_day_window=tc_raw.get("time_of_day_window"),
            regime=sig.get("regime") or {},
        )
        direction = (sig.get("direction") or "long").lower()
        tt, _, _ = classify_trade_type(direction, ctx, sig.get("score", 0))
        return tt if tt in ("INTRADAY", "SWING", "POSITION") else "INTRADAY"
    except Exception as e:
        log.debug("default_trade_type fallback for %s: %s", sig.get("symbol"), e)
        return "INTRADAY"


# ── 3. Forced exits (no Apex) ─────────────────────────────────────────────────

def screen_open_positions(
    open_positions: dict | list,
    now_utc: datetime | None = None,
) -> list[tuple[str, str]]:
    """Return (symbol, reason) pairs for positions that must exit immediately.
    These bypass the Apex entirely."""
    now = now_utc or datetime.now(UTC)
    scalp_max_mins = CONFIG.get("portfolio_manager", {}).get("scalp_max_hold_minutes", 90)
    forced: list[tuple[str, str]] = []

    _items = open_positions.values() if isinstance(open_positions, dict) else open_positions
    for pos in _items:
        sym = pos.get("symbol") or ""
        tt = (pos.get("trade_type") or "").upper()
        direction = (pos.get("direction") or "LONG").upper()

        if tt in ("UNKNOWN", ""):
            forced.append((sym, "unknown_trade_type")); continue
        if direction == "SHORT" and sym in _LONG_ONLY:
            forced.append((sym, "architecture_violation")); continue

        mins_held = _minutes_held(pos, now)
        if tt in ("INTRADAY", "SCALP") and mins_held > scalp_max_mins:
            forced.append((sym, "scalp_timeout")); continue

        if tt in ("INTRADAY", "SCALP") and _is_eod_window(now):
            forced.append((sym, "eod_flat")); continue

    return forced


def _minutes_held(pos: dict, now: datetime) -> float:
    try:
        open_dt = datetime.fromisoformat(pos.get("open_time", "")).replace(tzinfo=UTC)
        return (now - open_dt).total_seconds() / 60.0
    except Exception:
        return 0.0


def _is_eod_window(now: datetime) -> bool:
    """True if within the final 10 minutes before 16:00 ET (approx, EST/EDT tolerated)."""
    et_hour = (now.hour + _EST_OFFSET_HOURS) % 24
    if et_hour == 15 and now.minute >= 50:
        return True
    return False


# ── 4. Track B review flagger ─────────────────────────────────────────────────

def flag_positions_for_review(
    open_positions: dict | list,
    regime: dict,
    forced_symbols: set[str] | None = None,
) -> list[dict]:
    """Flag open positions whose thesis may have broken. Returns a list of
    TrackBPositionInput dicts for the Apex. Positions already in forced_symbols
    (from screen_open_positions) are skipped."""
    forced_symbols = forced_symbols or set()
    current_regime = (regime.get("regime") or "").upper()
    flagged: list[dict] = []

    _items = open_positions.values() if isinstance(open_positions, dict) else open_positions
    for pos in _items:
        sym = pos.get("symbol") or ""
        if sym in forced_symbols:
            continue
        tt = (pos.get("trade_type") or "").upper()
        reason = _detect_review_reason(pos, tt, current_regime)
        if not reason:
            continue
        flagged.append(_build_review_payload(pos, reason))
    return flagged


def _detect_review_reason(pos: dict, tt: str, current_regime: str) -> str | None:
    """Return flagged_reason string if position needs Apex review, else None."""
    try:
        entry_regime = (pos.get("regime") or pos.get("entry_regime") or "").upper()
        pnl_pct = _pnl_pct(pos)
        mins_held = _minutes_held(pos, datetime.now(UTC))
        earnings_days = (pos.get("trade_context") or {}).get("earnings_days_away")

        if earnings_days is not None and earnings_days <= 2 and tt in ("SWING", "POSITION"):
            return "earnings_approach"

        if tt == "SWING" and entry_regime and current_regime and entry_regime != current_regime:
            return "regime_flip"

        if tt == "SWING" and mins_held > CONFIG.get("swing_max_hold_days", 10) * 390:
            return "swing_timeout"

        if tt == "INTRADAY" and pnl_pct <= -0.03:
            return "thesis_driver_failure"

        from portfolio_manager import _conviction_band
        current_score = pos.get("current_score") or pos.get("score") or 0
        if _conviction_band(current_score) == "BELOW_THRESHOLD":
            return "score_collapse"
    except Exception as e:
        log.debug("_detect_review_reason error for %s: %s", pos.get("symbol"), e)
    return None


def _pnl_pct(pos: dict) -> float:
    entry = pos.get("entry") or 0
    current = pos.get("current") or entry
    if not entry:
        return 0.0
    direction = (pos.get("direction") or "LONG").upper()
    return (current - entry) / entry if direction == "LONG" else (entry - current) / entry


def _build_review_payload(pos: dict, reason: str) -> dict:
    """Build a minimal TrackBPositionInput. Phase 4 will replace this with
    portfolio_manager.prepare_review_payload() for full dimension_deltas."""
    try:
        from portfolio_manager import prepare_review_payload  # type: ignore
        payload = prepare_review_payload(pos, {})
        payload["flagged_reason"] = reason
        return payload
    except ImportError:
        pass
    from portfolio_manager import _conviction_band
    return {
        "symbol": pos.get("symbol"),
        "trade_type": pos.get("trade_type"),
        "direction": pos.get("direction"),
        "qty": pos.get("qty"),
        "entry_price": pos.get("entry"),
        "current_price": pos.get("current"),
        "pnl_pct": _pnl_pct(pos),
        "days_held": _minutes_held(pos, datetime.now(UTC)) / 390.0,
        "entry_regime": pos.get("regime") or pos.get("entry_regime"),
        "entry_score": pos.get("entry_score"),
        "current_score": pos.get("current_score") or pos.get("score"),
        "entry_thesis": pos.get("entry_thesis") or pos.get("reasoning"),
        "entry_conviction_band": _conviction_band(pos.get("entry_score")),
        "current_conviction_band": _conviction_band(pos.get("current_score") or pos.get("score")),
        "dimension_deltas": {},
        "flagged_reason": reason,
        "stop_price": pos.get("stop_loss"),
        "take_profit_price": pos.get("take_profit"),
    }


# ── 5. Post-Apex semantic filter ──────────────────────────────────────────────

def filter_semantic_violations(
    decision: dict,
    candidates_by_symbol: dict[str, dict],
) -> dict:
    """Remove new_entries that violate per-candidate constraints (allowed_trade_types,
    options_eligible). Log each removal. Never abort the whole dispatch."""
    kept = []
    for entry in decision.get("new_entries", []):
        sym = entry.get("symbol")
        tt = entry.get("trade_type")
        if tt == "AVOID":
            kept.append(entry); continue
        cand = candidates_by_symbol.get(sym)
        if cand is None:
            log.warning("filter_semantic_violations: %s not in candidates — dropping", sym); continue
        allowed = cand.get("allowed_trade_types") or []
        if tt not in allowed:
            log.warning(
                "filter_semantic_violations: %s trade_type=%s not in allowed=%s — dropping",
                sym, tt, allowed,
            ); continue
        if entry.get("instrument") in ("call", "put") and not cand.get("options_eligible", True):
            log.warning("filter_semantic_violations: %s options not eligible — dropping", sym); continue
        kept.append(entry)
    decision = {**decision, "new_entries": kept}
    return decision
