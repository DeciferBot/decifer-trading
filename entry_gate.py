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


def _validate_position(direction: str, ctx: TradeContext) -> tuple[bool, str]:
    """
    Validate POSITION entry using a two-path fundamental checklist.

    Returns (qualifies_as_position, reason).
    Failing downgrades to SWING — the trade still opens, just with a shorter
    hold horizon. This is NOT a REJECT.

    Hard gates (both paths):
      1. No binary catalyst within 5 days (earnings)
      2. Regime not hostile (BEAR_TRENDING, PANIC)

    Path A — Quality / Value (profitable company):
      FCF yield > 0 AND (DCF upside > threshold OR analyst upside > threshold)
      AND revenue not shrinking AND not decelerating

    Path B — Growth (pre-profitable / early stage):
      Revenue growth > threshold AND not decelerating
      AND gross margin > threshold AND EPS accelerating

    Supporting evidence (need ≥ 2 of 4 — breadth of thesis):
      1. Sector ETF above 50d MA AND outperforming SPY ≥ 5% over 3m
         OR stock itself above 200d MA
      2. Analyst consensus BUY or STRONG_BUY
      3. Recent analyst upgrade (last 10 days)
      4. Insider net buying
    """
    # ── Hard gate 1: binary catalyst risk ────────────────────────────────────
    min_earn_days = _cfg("position_min_earnings_days_away", 5)
    if ctx.earnings_days_away is not None and ctx.earnings_days_away < min_earn_days:
        return False, (
            f"earnings {ctx.earnings_days_away}d away — binary event within "
            f"{min_earn_days}d, downgrade to SWING"
        )

    # ── Hard gate 2: hostile regime ───────────────────────────────────────────
    if _regime_is_hostile(ctx.regime):
        return False, f"hostile regime {ctx.regime} — multi-week long contradicted, downgrade to SWING"

    # ── Path A: quality / value (profitable) ─────────────────────────────────
    min_dcf   = _cfg("position_min_dcf_upside_pct", 15)
    min_pt    = _cfg("position_min_analyst_upside_pct", 10)
    path_a = (
        ctx.fcf_yield is not None
        and ctx.fcf_yield > 0
        and (
            (ctx.dcf_upside_pct is not None and ctx.dcf_upside_pct > min_dcf)
            or (ctx.analyst_upside_pct is not None and ctx.analyst_upside_pct > min_pt)
        )
        and (ctx.revenue_growth_yoy is None or ctx.revenue_growth_yoy > 0)
        and not ctx.revenue_decelerating
    )

    # ── Path B: growth (pre-profitable) ──────────────────────────────────────
    min_rev_growth  = _cfg("position_min_revenue_growth_pct", 20)
    min_gross_margin = _cfg("position_min_gross_margin_pct", 30)
    path_b = (
        ctx.revenue_growth_yoy is not None
        and ctx.revenue_growth_yoy > min_rev_growth
        and not ctx.revenue_decelerating
        and (ctx.gross_margin is None or ctx.gross_margin > min_gross_margin)
        and ctx.eps_accelerating is True
    )

    if not path_a and not path_b:
        return False, (
            "neither quality/value nor growth path qualifies — "
            f"fcf_yield={ctx.fcf_yield} dcf_upside={ctx.dcf_upside_pct} "
            f"rev_growth={ctx.revenue_growth_yoy} rev_decel={ctx.revenue_decelerating} "
            f"gross_margin={ctx.gross_margin} eps_accel={ctx.eps_accelerating} — "
            "downgrade to SWING"
        )

    # ── Supporting evidence: need ≥ 2 of 4 ───────────────────────────────────
    support = 0
    support_detail = []

    # 1. Sector momentum OR stock above 200d MA
    sector_ok = bool(ctx.sector_above_50d and (ctx.sector_3m_vs_spy or 0) >= 5)
    stock_trend_ok = bool(ctx.stock_above_200d)
    if sector_ok or stock_trend_ok:
        support += 1
        support_detail.append("sector/200d trend")

    # 2. Analyst consensus BUY or better
    if ctx.analyst_consensus in ("BUY", "STRONG_BUY"):
        support += 1
        support_detail.append("analyst BUY")

    # 3. Recent analyst upgrade
    if ctx.recent_upgrade:
        support += 1
        support_detail.append("recent upgrade")

    # 4. Insider net buying
    if ctx.insider_net_sentiment == "BUYING":
        support += 1
        support_detail.append("insider buying")

    min_support = _cfg("position_min_supporting_signals", 2)
    if support < min_support:
        return False, (
            f"only {support}/{min_support} supporting signals "
            f"({', '.join(support_detail) or 'none'}) — downgrade to SWING"
        )

    path_label = "value/quality" if path_a else "growth"
    return True, (
        f"POSITION validated: {path_label} path | "
        f"{support}/4 signals ({', '.join(support_detail)})"
    )


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
    opus_trade_type: str | None = None,
) -> tuple[bool, str, str, int]:
    """
    Full entry validation: classify trade type and check effective score.

    Returns (allowed, trade_type, reason, effective_score).

    allowed = False if trade_type == "REJECT" or effective_score < min_score.

    opus_trade_type: the hold-horizon label from Opus (POSITION/SWING/INTRADAY).
    When Opus says POSITION, the two-path fundamental checklist in _validate_position()
    is run. If it fails, trade_type is downgraded to SWING — the trade still opens,
    it just gets the shorter hold horizon.
    """
    if min_score is None:
        min_score = CONFIG.get("min_score_to_trade", 14)

    trade_type, reason, effective_score = classify_trade_type(direction, ctx, score)

    if trade_type == "REJECT":
        return False, "REJECT", reason, effective_score

    # ── POSITION checklist: validate fundamentals when Opus said POSITION ─────
    if opus_trade_type == "POSITION":
        qualifies, pos_reason = _validate_position(direction, ctx)
        if qualifies:
            trade_type = "POSITION"
            reason = pos_reason
            log.info(
                "entry_gate: %s %s POSITION approved | %s",
                ctx.symbol, direction, pos_reason,
            )
        else:
            # Downgrade to SWING — don't block the trade, just reduce hold horizon
            trade_type = "SWING"
            reason = pos_reason
            log.info(
                "entry_gate: %s %s POSITION→SWING | %s",
                ctx.symbol, direction, pos_reason,
            )

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
