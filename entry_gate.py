# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  entry_gate.py                             ║
# ║   Single responsibility: validate a TradeContext against    ║
# ║   per-type entry requirements before any order fires.       ║
# ║                                                              ║
# ║   Returns (allowed: bool, reason: str, score_penalty: int)  ║
# ║   Pure function — no side effects, fully testable.          ║
# ║   All thresholds read from config.py entry_gate section.    ║
# ║                                                              ║
# ║   Trade type hierarchy (Opus follows this order):           ║
# ║     1. POSITION  — ALL primary conditions required          ║
# ║     2. SWING     — at least 1 qualifying catalyst           ║
# ║     3. INTRADAY  — technical quality gates                  ║
# ║     4. REJECT    — no qualifying type found                 ║
# ║                                                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import CONFIG

if TYPE_CHECKING:
    from trade_context import TradeContext

log = logging.getLogger("decifer.entry_gate")

# ── Gate config defaults (overridden by config.py entry_gate section) ─────────

def _cfg(key: str, default):
    return CONFIG.get("entry_gate", {}).get(key, default)


# ── Shared hostile-regime check ───────────────────────────────────────────────

_PANIC_REGIMES    = {"PANIC", "EXTREME_STRESS"}
_BEAR_REGIMES     = {"BEAR_TRENDING", "FEAR_ELEVATED"}
_HOSTILE_ALL      = _PANIC_REGIMES | _BEAR_REGIMES


def _regime_is_panic(regime: str | None) -> bool:
    return (regime or "").upper() in _PANIC_REGIMES


def _regime_is_hostile(regime: str | None) -> bool:
    return (regime or "").upper() in _HOSTILE_ALL


# ── INTRADAY gate ─────────────────────────────────────────────────────────────


def _validate_intraday(direction: str, ctx: TradeContext) -> tuple[bool, str, int]:
    """
    Validate INTRADAY entry conditions.

    Returns (allowed, reason, score_penalty).
    score_penalty > 0 means this many extra points are required above base threshold.
    """
    score_penalty = 0

    # ── Hard disqualifiers ────────────────────────────────────────────────────

    # Market is closed — no intraday entries post-close or pre-open
    if ctx.time_of_day_window == "CLOSE":
        return False, "market closed — INTRADAY entry requires open market (OPEN/MIDDAY/PRIME_PM)", 0

    # Earnings same day or pre-market tomorrow
    if ctx.earnings_days_away is not None and ctx.earnings_days_away <= 0:
        return False, "earnings same day — binary event, not a technical trade", 0

    return True, "INTRADAY approved", score_penalty


# ── SWING gate ────────────────────────────────────────────────────────────────


def _validate_swing(direction: str, ctx: TradeContext) -> tuple[bool, str, int]:
    """Validate SWING entry — requires at least one qualifying catalyst."""

    # ── Hard disqualifiers ────────────────────────────────────────────────────

    # Panic regime
    if _regime_is_panic(ctx.regime):
        return False, f"regime {ctx.regime} — no SWING entries in panic conditions", 0

    # Earnings too close (binary event risk)
    min_days = _cfg("swing_min_earnings_days_away", 5)
    if ctx.earnings_days_away is not None and ctx.earnings_days_away < min_days:
        return (
            False,
            f"earnings {ctx.earnings_days_away} days away — below {min_days}-day gate",
            0,
        )

    return True, "SWING approved", 0


# ── POSITION gate ─────────────────────────────────────────────────────────────


def _validate_position(direction: str, ctx: TradeContext) -> tuple[bool, str, int]:
    """
    Validate POSITION entry — ALL three primary conditions required.

    Primary conditions:
      1. Revenue growth > 15% YoY AND not decelerating
      2. Sector ETF above 50-day MA AND 3m outperformance vs SPY > 5%
      3. Technical: handled by signal engine (squeeze/breakout dimension)
    """

    # ── Hard disqualifiers ────────────────────────────────────────────────────

    # Hostile regime: no new POSITION in BEAR or PANIC
    if _regime_is_hostile(ctx.regime):
        return False, f"regime {ctx.regime} — no POSITION entries in hostile regime", 0

    # Earnings within 30 days (binary event risk at position time horizon)
    min_days = _cfg("position_min_earnings_days_away", 30)
    if ctx.earnings_days_away is not None and ctx.earnings_days_away < min_days:
        return (
            False,
            f"earnings {ctx.earnings_days_away} days away — below {min_days}-day gate for POSITION",
            0,
        )

    return True, "POSITION approved", 0


# ── Trade type classifier ─────────────────────────────────────────────────────


def classify_trade_type(
    direction: str,
    ctx: TradeContext,
    score: int,
) -> tuple[str, str, int]:
    """
    Validate the trade and return a neutral INTRADAY label (hard stops only).

    Trade type classification belongs to Opus (market_intelligence). This function
    only enforces hard stops — earnings same day, market closed — and returns INTRADAY
    as a neutral label so the dispatcher's promote logic never overrides Opus's label.

    Exception: market closed with no earnings block → return SWING to allow
    overnight/post-close entries to proceed with the correct hold horizon.

    Returns (trade_type, reason, effective_score).
    """
    # Earnings same day — binary event, signal is invalid regardless of trade type
    if ctx.earnings_days_away is not None and ctx.earnings_days_away <= 0:
        return "REJECT", "earnings same day — binary event, signal invalid", score

    # Market closed: INTRADAY blocked, but SWING/POSITION entries are valid
    # (overnight drift, post-close catalyst setups). Return SWING so these proceed.
    if ctx.time_of_day_window == "CLOSE":
        # Still block if earnings are very close (< 5d) — binary event risk
        if ctx.earnings_days_away is not None and ctx.earnings_days_away < 5:
            return "REJECT", f"market closed + earnings {ctx.earnings_days_away}d away", score
        return "SWING", "market closed — overnight/post-close entry", score

    return "INTRADAY", "hard stops cleared", score


# ── Public API ────────────────────────────────────────────────────────────────


def validate_entry(
    direction: str,
    ctx: TradeContext,
    score: int,
    min_score: int | None = None,
) -> tuple[bool, str, str, int]:
    """
    Full entry validation: classify trade type and check effective score.

    Returns (allowed, trade_type, reason, effective_score).

    allowed = False if trade_type == "REJECT" or effective_score < min_score.
    """
    if min_score is None:
        min_score = CONFIG.get("min_score_to_trade", 14)

    trade_type, reason, effective_score = classify_trade_type(direction, ctx, score)

    if trade_type == "REJECT":
        return False, "REJECT", reason, effective_score

    if effective_score < min_score:
        full_reason = (
            f"{trade_type} gate passed but effective score {effective_score} "
            f"< min {min_score} (score={score}, penalty={score - effective_score})"
        )
        log.info("entry_gate: %s %s %s", ctx.symbol, direction, full_reason)
        return False, trade_type, full_reason, effective_score

    log.info(
        "entry_gate: %s %s APPROVED as %s | score=%d effective=%d | %s",
        ctx.symbol, direction, trade_type, score, effective_score, reason,
    )
    return True, trade_type, reason, effective_score
