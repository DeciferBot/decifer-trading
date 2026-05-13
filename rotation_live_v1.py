"""
rotation_live_v1.py — Retail Live Rotation V1 Canary
=====================================================

Shadow-only decision module that evaluates whether a single weak position
should be exited to free margin capacity for a stronger blocked candidate.

Design constraints
------------------
* This module NEVER calls execute_buy. Entry of the unblocked candidate
  happens naturally via the next Apex scan cycle after capacity is freed.
* All runtime imports are lazy (inside evaluate()) to avoid circular imports.
* When ENABLE_ROTATION_LIVE_V1=False the module runs in HYPOTHETICAL mode:
  all gates are evaluated and logged but no execute_sell is called.
* Every evaluate() call appends exactly one record to
  data/rotation_live_v1/decisions.jsonl regardless of outcome.
* Thread-safe daily limit counter guards the hard 1-per-day ceiling.

Gate sequence (in order — first failing gate short-circuits):
  G1  Feature flag ON
  G2  Daily limit not exceeded
  G3  Blocked score >= ROTATION_LIVE_MIN_BLOCKED_SCORE
  G4  Gap (blocked_score - book_avg) >= ROTATION_LIVE_MIN_GAP_VS_BOOK
  G5  Account values fresh (< 300 s stale)
  G6  Exit candidate exists with score <= ROTATION_LIVE_EXIT_SCORE_MAX
  G7  Exit candidate notional <= ROTATION_LIVE_MAX_NLV_PCT × NLV
  G8  Exit candidate price quote is fresh (< 30 s)
  G9  Exit candidate bid-ask spread acceptable (< 1 %)

Entry point
-----------
Called from orders_core.execute_buy() after the _trades_lock is released,
still inside sym_lock, when exp_code == "margin_gross_cap_block".

    import rotation_live_v1 as _rlv1
    _rlv1.evaluate(
        blocked_symbol=symbol,
        blocked_score=score,
        portfolio_value=portfolio_value,
        active_trades_snapshot=dict(active_trades),
    )
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import threading
from typing import Any

log = logging.getLogger(__name__)

# ── Daily limit counter ──────────────────────────────────────────────────────

_daily_lock = threading.Lock()
_daily_date: str = ""      # "YYYY-MM-DD" UTC
_daily_count: int = 0

_DECISIONS_DIR = pathlib.Path("data/rotation_live_v1")
_DECISIONS_FILE = _DECISIONS_DIR / "decisions.jsonl"

# Max account-values age before gate G5 fails (seconds).
_ACCOUNT_MAX_AGE_S = 300.0
# Max quote age before gate G8 fails (seconds).
_QUOTE_MAX_AGE_S = 30.0
# Max spread before gate G9 fails (fraction, e.g. 0.01 = 1 %).
_MAX_SPREAD_PCT = 0.01


# ── Helpers ──────────────────────────────────────────────────────────────────


def _today_utc() -> str:
    """Return today's date as YYYY-MM-DD in UTC."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _daily_count_exceeded(max_per_day: int) -> bool:
    """Return True if the daily limit has already been reached."""
    global _daily_date, _daily_count
    today = _today_utc()
    with _daily_lock:
        if _daily_date != today:
            _daily_date = today
            _daily_count = 0
        return _daily_count >= max_per_day


def _increment_daily_count() -> None:
    """Increment the daily counter (call only after a successful execute_sell)."""
    global _daily_count
    with _daily_lock:
        _daily_count += 1


def _log_decision(record: dict) -> None:
    """Append one JSON record to the decisions log (fire-and-forget)."""
    try:
        _DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
        with _DECISIONS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("rotation_live_v1 log write failed: %s", exc)


def _account_is_fresh(max_age_seconds: float = _ACCOUNT_MAX_AGE_S) -> bool:
    """Return True when account values were received within max_age_seconds."""
    import time
    import bot_state
    updated_at = bot_state.account_values_updated_at
    if updated_at is None:
        return False
    age = time.time() - updated_at
    return age <= max_age_seconds


def _price_is_fresh(symbol: str, max_age_seconds: float = _QUOTE_MAX_AGE_S) -> bool:
    """Return True when the cached quote for symbol is within max_age_seconds."""
    import time
    import alpaca_stream
    entry = alpaca_stream.QUOTE_CACHE.get(symbol)
    if entry is None:
        return False
    ts = entry.get("ts")
    if ts is None:
        return False
    age = time.time() - ts
    return age <= max_age_seconds


def _spread_is_acceptable(symbol: str, max_spread_pct: float = _MAX_SPREAD_PCT) -> bool:
    """Return True when the bid-ask spread for symbol is below max_spread_pct."""
    import alpaca_stream
    spread = alpaca_stream.QUOTE_CACHE.get_spread_pct(symbol)
    if spread is None:
        return False
    return spread <= max_spread_pct


def _get_nlv() -> float | None:
    """Return NetLiquidation from bot_state.account_values, or None."""
    import bot_state
    val = bot_state.account_values.get("NetLiquidation")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _book_avg(active_trades_snapshot: dict[str, Any]) -> float | None:
    """
    Return the mean entry_score of all positions in active_trades_snapshot.
    Falls back to the 'score' field if 'entry_score' is absent.
    Returns None when the snapshot is empty.
    """
    scores: list[float] = []
    for pos in active_trades_snapshot.values():
        raw = pos.get("entry_score") if pos.get("entry_score") is not None else pos.get("score")
        if raw is not None:
            try:
                scores.append(float(raw))
            except (TypeError, ValueError):
                pass
    if not scores:
        return None
    return sum(scores) / len(scores)


def _select_exit_candidate(
    active_trades_snapshot: dict[str, Any],
    exit_score_max: int,
) -> dict[str, Any] | None:
    """
    Return the weakest non-protected position with score <= exit_score_max,
    or None if no such position exists.

    A position is protected if any of:
      - pos.get("hold_protected") is truthy
      - pos.get("status") in ("RESERVED", "EXITING")
      - entry_score (or score) is None / not numeric
    """
    best: dict[str, Any] | None = None
    best_score: float = float("inf")

    for pos in active_trades_snapshot.values():
        status = pos.get("status", "")
        if status in ("RESERVED", "EXITING"):
            continue
        if pos.get("hold_protected"):
            continue

        raw = pos.get("entry_score") if pos.get("entry_score") is not None else pos.get("score")
        if raw is None:
            continue
        try:
            s = float(raw)
        except (TypeError, ValueError):
            continue

        if s <= exit_score_max and s < best_score:
            best_score = s
            best = pos

    return best


# ── Main entry point ─────────────────────────────────────────────────────────


def evaluate(
    blocked_symbol: str,
    blocked_score: int,
    portfolio_value: float,
    active_trades_snapshot: dict[str, Any],
) -> None:
    """
    Evaluate whether a single weak position should be exited to free capacity
    for the blocked candidate.

    This function NEVER returns a meaningful value.  The caller (orders_core)
    discards the return value and always returns False for the blocked buy.
    The only side-effects are:
      - a record appended to data/rotation_live_v1/decisions.jsonl
      - possibly one execute_sell() call (when flag ON and all gates pass)
    """
    # ── Lazy imports ────────────────────────────────────────────────────────
    from config import CONFIG

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Shared base record — updated incrementally as gates are evaluated.
    record: dict[str, Any] = {
        "ts":              ts,
        "blocked_symbol":  blocked_symbol,
        "blocked_score":   blocked_score,
        "portfolio_value": portfolio_value,
        "flag_enabled":    bool(CONFIG.get("ENABLE_ROTATION_LIVE_V1", False)),
        "gates_passed":    [],
        "failed_gate":     None,
        "failed_reason":   None,
        "exit_symbol":     None,
        "exit_score":      None,
        "exit_notional":   None,
        "book_avg":        None,
        "gap":             None,
        "nlv":             None,
        "final_status":    "HYPOTHETICAL",
    }

    flag_on: bool = bool(CONFIG.get("ENABLE_ROTATION_LIVE_V1", False))
    max_per_day:       int   = int(CONFIG.get("ROTATION_LIVE_MAX_PER_DAY", 1))
    min_blocked_score: int   = int(CONFIG.get("ROTATION_LIVE_MIN_BLOCKED_SCORE", 75))
    min_gap:           float = float(CONFIG.get("ROTATION_LIVE_MIN_GAP_VS_BOOK", 15))
    exit_score_max:    int   = int(CONFIG.get("ROTATION_LIVE_EXIT_SCORE_MAX", 35))
    max_nlv_pct:       float = float(CONFIG.get("ROTATION_LIVE_MAX_NLV_PCT", 0.02))

    def _fail(gate: str, reason: str) -> None:
        record["failed_gate"]   = gate
        record["failed_reason"] = reason
        record["final_status"]  = "GATE_BLOCKED"
        _log_decision(record)
        log.debug("rotation_live_v1 GATE_BLOCKED [%s] %s: %s", gate, blocked_symbol, reason)

    # ── G1: Feature flag ────────────────────────────────────────────────────
    # Always evaluate gates for logging even when flag is OFF, but mark as HYPOTHETICAL.
    # The flag check only controls whether execute_sell is called at the end.

    # ── G2: Daily limit ─────────────────────────────────────────────────────
    if _daily_count_exceeded(max_per_day):
        _fail("G2", f"daily limit {max_per_day} already reached")
        return
    record["gates_passed"].append("G2")

    # ── G3: Blocked score threshold ─────────────────────────────────────────
    if blocked_score < min_blocked_score:
        _fail("G3", f"blocked_score {blocked_score} < {min_blocked_score}")
        return
    record["gates_passed"].append("G3")

    # ── G4: Gap vs book average ─────────────────────────────────────────────
    avg = _book_avg(active_trades_snapshot)
    record["book_avg"] = round(avg, 2) if avg is not None else None
    if avg is None:
        _fail("G4", "book_avg unavailable (empty snapshot)")
        return
    gap = blocked_score - avg
    record["gap"] = round(gap, 2)
    if gap < min_gap:
        _fail("G4", f"gap {gap:.1f} < {min_gap}")
        return
    record["gates_passed"].append("G4")

    # ── G5: Account values freshness ────────────────────────────────────────
    if not _account_is_fresh():
        _fail("G5", f"account values stale (> {_ACCOUNT_MAX_AGE_S:.0f} s)")
        return
    record["gates_passed"].append("G5")

    # ── G6: Exit candidate exists ────────────────────────────────────────────
    candidate = _select_exit_candidate(active_trades_snapshot, exit_score_max)
    if candidate is None:
        _fail("G6", f"no exit candidate with score <= {exit_score_max}")
        return
    exit_sym = candidate.get("symbol", "")
    raw_score = candidate.get("entry_score") if candidate.get("entry_score") is not None else candidate.get("score")
    exit_score = float(raw_score) if raw_score is not None else 0.0
    record["exit_symbol"] = exit_sym
    record["exit_score"]  = exit_score
    record["gates_passed"].append("G6")

    # ── G7: Exit candidate notional vs NLV ──────────────────────────────────
    nlv = _get_nlv()
    record["nlv"] = nlv
    if nlv is None or nlv <= 0:
        _fail("G7", "NLV unavailable from account_values")
        return
    qty   = candidate.get("qty", 0) or 0
    entry = candidate.get("entry", 0.0) or 0.0
    notional = float(qty) * float(entry)
    record["exit_notional"] = round(notional, 2)
    nlv_pct = notional / nlv if nlv > 0 else float("inf")
    if nlv_pct > max_nlv_pct:
        _fail("G7", f"exit notional {notional:.0f} is {nlv_pct:.1%} NLV > {max_nlv_pct:.1%}")
        return
    record["gates_passed"].append("G7")

    # ── G8: Price quote freshness ────────────────────────────────────────────
    if not _price_is_fresh(exit_sym):
        _fail("G8", f"quote for {exit_sym} stale (> {_QUOTE_MAX_AGE_S:.0f} s)")
        return
    record["gates_passed"].append("G8")

    # ── G9: Bid-ask spread ───────────────────────────────────────────────────
    if not _spread_is_acceptable(exit_sym):
        _fail("G9", f"spread for {exit_sym} > {_MAX_SPREAD_PCT:.1%}")
        return
    record["gates_passed"].append("G9")

    # ── All gates passed — decide based on flag ──────────────────────────────
    if not flag_on:
        record["final_status"] = "HYPOTHETICAL"
        _log_decision(record)
        log.info(
            "rotation_live_v1 HYPOTHETICAL: would exit %s (score %.0f) to unblock %s (score %d) — flag OFF",
            exit_sym, exit_score, blocked_symbol, blocked_score,
        )
        return

    # ── Execute sell ─────────────────────────────────────────────────────────
    import bot_state
    import orders_core

    ib = bot_state.ib
    reason = (
        f"rotation_live_v1: exit {exit_sym} (score {exit_score:.0f}) "
        f"to free capacity for {blocked_symbol} (score {blocked_score})"
    )
    log.info("rotation_live_v1 EXECUTING EXIT: %s — %s", exit_sym, reason)

    try:
        ok = orders_core.execute_sell(ib, exit_sym, reason=reason)
    except Exception as exc:
        log.warning("rotation_live_v1 execute_sell raised: %s", exc)
        ok = False

    if ok:
        _increment_daily_count()
        record["final_status"] = "EXIT_OK_AWAITING_NEXT_SCAN"
        log.info(
            "rotation_live_v1 EXIT_OK: %s sold. Blocked candidate %s will retry on next Apex scan.",
            exit_sym, blocked_symbol,
        )
    else:
        record["final_status"] = "EXIT_FAILED"
        log.warning(
            "rotation_live_v1 EXIT_FAILED: execute_sell(%s) returned False", exit_sym
        )

    _log_decision(record)
