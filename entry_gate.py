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


def _validate_intraday(
    direction: str,
    ctx: TradeContext,
    score_breakdown: dict | None = None,
) -> tuple[bool, str, int]:
    """
    Validate INTRADAY entry conditions.

    Returns (allowed, reason, score_penalty).
    score_penalty > 0 means this many extra points are required above base threshold.
    """
    score_penalty = 0
    sig_threshold = _cfg("intraday_signal_threshold", 5)

    # ── Hard disqualifiers ────────────────────────────────────────────────────

    # Market is closed — no intraday entries post-close or pre-open
    if ctx.time_of_day_window == "CLOSE":
        return False, "market closed — INTRADAY entry requires open market (OPEN/MIDDAY/PRIME_PM)", 0

    # Earnings same day or pre-market tomorrow
    if ctx.earnings_days_away is not None and ctx.earnings_days_away <= 0:
        return False, "earnings same day — binary event, not a technical trade", 0

    # ── Change 2 — 2-of-3 signal gate ────────────────────────────────────────
    # IC evidence: flow=+0.219, squeeze=+0.197, momentum=+0.082.
    # Any 2 of the 3 must score ≥ threshold (default 5). 1-of-3 → REJECT.
    bd = score_breakdown or {}
    flow_score = float(bd.get("flow", 0) or 0)
    squeeze_score = float(bd.get("squeeze", 0) or 0)
    momentum_score = float(bd.get("momentum", 0) or 0)
    signals_firing = sum([
        flow_score >= sig_threshold,
        squeeze_score >= sig_threshold,
        momentum_score >= sig_threshold,
    ])
    if signals_firing < 2:
        return False, (
            f"INTRADAY 2-of-3 signal gate: only {signals_firing}/3 signals ≥{sig_threshold} "
            f"(flow={flow_score:.0f} squeeze={squeeze_score:.0f} momentum={momentum_score:.0f}) "
            "— need any 2 of (flow, squeeze, momentum); entry_gate:2of3_signal_gate"
        ), 0

    # ── Change 3 — INTRADAY SHORT: flow AND squeeze required ─────────────────
    # Short side has structural long bias against it — require stronger confirmation.
    if direction.lower() == "short":
        if flow_score < sig_threshold or squeeze_score < sig_threshold:
            return False, (
                f"INTRADAY SHORT requires flow≥{sig_threshold} AND squeeze≥{sig_threshold}: "
                f"flow={flow_score:.0f} squeeze={squeeze_score:.0f} "
                "— entry_gate:short_flow_squeeze_gate"
            ), 0

    # ── Tape gate: long entries require tape not deeply bearish ───────────────
    if direction.lower() == "long":
        spy_chg = ctx.regime.get("spy_chg_1d", 0.0) if isinstance(ctx.regime, dict) else 0.0
        qqq_chg = ctx.regime.get("qqq_chg_1d", 0.0) if isinstance(ctx.regime, dict) else 0.0
        hard_block = CONFIG.get("tape_bearish_hard_block_pct", -2.0)
        soft_threshold = CONFIG.get("tape_bearish_score_penalty_pct", -1.2)
        penalty = CONFIG.get("tape_bearish_score_penalty", 3)
        if spy_chg < hard_block and qqq_chg < hard_block:
            return False, f"tape too bearish for longs (SPY {spy_chg:+.1f}%, QQQ {qqq_chg:+.1f}%)", 0
        if spy_chg < soft_threshold and qqq_chg < soft_threshold:
            score_penalty += penalty

    return True, "INTRADAY approved", score_penalty


# ── SWING gate ────────────────────────────────────────────────────────────────


_STRUCTURAL_CATALYST_TYPES = frozenset({
    "earnings", "earnings_beat", "earnings_surprise", "pead",
    "upgrade", "sector", "overnight_drift",
})
_SWING_SHORT_BEARISH_REGIMES = frozenset({"TRENDING_DOWN", "RELIEF_RALLY", "CAPITULATION"})


def _validate_swing(direction: str, ctx: TradeContext) -> tuple[bool, str, int]:
    """Validate SWING entry — requires at least one qualifying structural catalyst."""

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

    # Catalyst score floor
    min_catalyst = _cfg("swing_min_catalyst_score", 5.0)
    cat_score = ctx.catalyst_score or 0.0
    if cat_score < min_catalyst:
        return (
            False,
            f"catalyst score {cat_score:.1f} below SWING floor {min_catalyst:.1f} — entry_gate:catalyst_score_floor",
            0,
        )

    # ── Change 6 — Block news-alone SWING entries ─────────────────────────────
    # News IC = -0.253 (anti-predictive). Structural catalysts required.
    # Allowed: earnings/PEAD, analyst upgrade, insider buying, congressional trade, sector.
    # Currently SHADOW MODE (swing_news_alone_blocks=False): logs would-have-blocked
    # but does not reject. Enable once ≥80% of SWING entries populate catalyst_type.
    has_structural = (
        (ctx.catalyst_type or "").lower() in _STRUCTURAL_CATALYST_TYPES
        or ctx.recent_upgrade
        or ctx.insider_net_sentiment == "BUYING"
        or (ctx.congressional_sentiment or "").upper() == "BUYING"
    )
    if not has_structural:
        if _cfg("swing_news_alone_blocks", True):
            return False, (
                f"news-alone SWING blocked: no structural catalyst found "
                f"(catalyst_type={ctx.catalyst_type}, recent_upgrade={ctx.recent_upgrade}, "
                f"insider={ctx.insider_net_sentiment}, congressional={ctx.congressional_sentiment}) "
                "— news IC=-0.253 is anti-predictive; need earnings/upgrade/insider/congressional/sector — "
                "entry_gate:news_alone_swing_block"
            ), 0
        elif _cfg("swing_news_alone_blocks_shadow", False):
            log.info(
                "entry_gate SHADOW: %s SWING would-have-blocked (news-alone, no structural catalyst) — "
                "catalyst_type=%s recent_upgrade=%s insider=%s congressional=%s — "
                "entry_gate:news_alone_swing_block_shadow",
                ctx.symbol if ctx else "?",
                ctx.catalyst_type, ctx.recent_upgrade,
                ctx.insider_net_sentiment, ctx.congressional_sentiment,
            )

    # ── Change 7 — SWING SHORT: bearish structural regime only ────────────────
    # Short book lost -$75,907. Only allow SWING SHORTs in genuinely bearish structure.
    if direction.lower() == "short" and _cfg("swing_short_bearish_regimes_only", True):
        structural_regime = (
            ctx.regime.get("regime", "") if isinstance(ctx.regime, dict) else (ctx.regime or "")
        ).upper()
        if structural_regime not in _SWING_SHORT_BEARISH_REGIMES:
            return False, (
                f"SWING SHORT blocked in regime '{structural_regime}' "
                f"(allowed: TRENDING_DOWN, RELIEF_RALLY, CAPITULATION) "
                "— entry_gate:swing_short_bearish_regime_gate"
            ), 0

    # ── Tape gate: hard block long swings on deeply bearish tape ─────────────
    if direction.lower() == "long":
        spy_chg = ctx.regime.get("spy_chg_1d", 0.0) if isinstance(ctx.regime, dict) else 0.0
        qqq_chg = ctx.regime.get("qqq_chg_1d", 0.0) if isinstance(ctx.regime, dict) else 0.0
        hard_block = CONFIG.get("tape_bearish_hard_block_pct", -2.0)
        if spy_chg < hard_block and qqq_chg < hard_block:
            return False, f"tape too bearish for long swing (SPY {spy_chg:+.1f}%, QQQ {qqq_chg:+.1f}%)", 0

    return True, "SWING approved", 0


# ── POSITION gate ─────────────────────────────────────────────────────────────


def _validate_position(
    direction: str,
    ctx: TradeContext,
    instrument: str | None = None,
) -> tuple[bool, str]:
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
    # ── Change 8 — POSITION: LONG only, equity only ──────────────────────────
    if _cfg("position_long_only", True) and (direction or "").lower() == "short":
        return False, (
            "POSITION blocked for SHORT direction (position_long_only=True) — "
            "downgrade to SWING — entry_gate:position_long_only"
        )

    _instr = (instrument or "").lower()
    if _cfg("position_equity_only", True) and _instr in ("call", "put", "option", "options"):
        return False, (
            f"POSITION blocked for instrument='{instrument}' (position_equity_only=True): "
            "theta decay incompatible with multi-week hold — "
            "downgrade to SWING — entry_gate:position_equity_only"
        )

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
        and ctx.eps_accelerating is not False
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
    score_breakdown: dict | None = None,
    instrument: str | None = None,
    open_intraday_count: int = 0,
    scanner_tier: str | None = None,
) -> tuple[bool, str, str, int]:
    """
    Full entry validation: classify trade type and check effective score.

    Returns (allowed, trade_type, reason, effective_score).

    allowed = False if trade_type == "REJECT" or effective_score < min_score.

    opus_trade_type: the hold-horizon label from Opus (POSITION/SWING/INTRADAY).
    When Opus says POSITION, the two-path fundamental checklist in _validate_position()
    is run. If it fails, trade_type is downgraded to SWING — the trade still opens,
    it just gets the shorter hold horizon.

    score_breakdown: per-dimension signal scores {flow, squeeze, momentum, ...}
    instrument: "stock"|"call"|"put"|"COMMON" — used for POSITION equity-only gate
    open_intraday_count: number of currently open INTRADAY positions (concurrency gate)
    scanner_tier: "D" if candidate came from Position Research Universe (Tier D)
    """
    if min_score is None:
        min_score = CONFIG.get("min_score_to_trade", 14)

    # ── Change 5 — Block score=0 SWING/POSITION entries ──────────────────────
    # score=0 means no signal data was available. 18% of historical SWING trades
    # entered at score=0. These have no signal basis and must not enter.
    # Rollback: set score_zero_swing_position_blocks=False in config entry_gate section.
    if score == 0 and opus_trade_type in ("SWING", "POSITION") and _cfg("score_zero_swing_position_blocks", True):
        log.info(
            "entry_gate: %s %s score=0 REJECTED for %s — no signal data available — "
            "entry_gate:score_zero_swing_position",
            ctx.symbol if ctx else "?", direction, opus_trade_type,
        )
        return False, "REJECT", (
            f"score=0 — no signal data available for {opus_trade_type} entry "
            "— entry_gate:score_zero_swing_position"
        ), score

    trade_type, reason, effective_score = classify_trade_type(direction, ctx, score)

    if trade_type == "REJECT":
        return False, "REJECT", reason, effective_score

    # ── Change 4 — INTRADAY max concurrent check ──────────────────────────────
    if opus_trade_type == "INTRADAY":
        max_concurrent = _cfg("intraday_max_concurrent", 2)
        if open_intraday_count >= max_concurrent:
            log.info(
                "entry_gate: %s %s INTRADAY concurrent limit reached (%d/%d open) — "
                "entry_gate:intraday_max_concurrent",
                ctx.symbol if ctx else "?", direction, open_intraday_count, max_concurrent,
            )
            return False, "REJECT", (
                f"INTRADAY max concurrent {max_concurrent} reached "
                f"({open_intraday_count} open) — entry_gate:intraday_max_concurrent"
            ), score

    # ── INTRADAY checklist: signal gates for INTRADAY entries ────────────────
    if opus_trade_type == "INTRADAY" and ctx is not None:
        intra_ok, intra_reason, intra_penalty = _validate_intraday(
            direction, ctx, score_breakdown=score_breakdown
        )
        if not intra_ok:
            log.info(
                "entry_gate: %s %s INTRADAY gate REJECTED | %s",
                ctx.symbol, direction, intra_reason,
            )
            return False, "REJECT", intra_reason, effective_score
        effective_score -= intra_penalty  # score_penalty reduces effective score

    # ── SWING checklist: catalyst gates for SWING entries ────────────────────
    if opus_trade_type == "SWING" and ctx is not None:
        swing_ok, swing_reason, _ = _validate_swing(direction, ctx)
        if not swing_ok:
            log.info(
                "entry_gate: %s %s SWING gate REJECTED | %s",
                ctx.symbol, direction, swing_reason,
            )
            return False, "REJECT", swing_reason, effective_score

    # ── POSITION checklist: validate fundamentals when Opus said POSITION ─────
    if opus_trade_type == "POSITION":
        qualifies, pos_reason = _validate_position(direction, ctx, instrument=instrument)
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
            # Audit trail: distinguish missing-data downgrade from failed-criteria downgrade
            if ctx is not None:
                _no_fcf = ctx.fcf_yield is None
                _no_rev = ctx.revenue_growth_yoy is None
                _no_margin = getattr(ctx, "gross_margin", None) is None
                if _no_fcf and _no_rev and _no_margin:
                    log.info(
                        "entry_gate: %s %s POSITION→SWING missing_fundamentals_no_entry "
                        "(fcf_yield=None, revenue_growth_yoy=None, gross_margin=None) | %s",
                        ctx.symbol, direction, pos_reason,
                    )
                elif _no_fcf or _no_rev or _no_margin:
                    log.info(
                        "entry_gate: %s %s POSITION→SWING missing_trade_context_position_cap "
                        "(partial data: fcf=%s rev=%s margin=%s) | %s",
                        ctx.symbol, direction,
                        ctx.fcf_yield, ctx.revenue_growth_yoy,
                        getattr(ctx, "gross_margin", None), pos_reason,
                    )
                else:
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
