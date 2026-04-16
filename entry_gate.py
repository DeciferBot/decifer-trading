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

    # No volume
    min_rel_vol = _cfg("intraday_min_rel_volume", 1.3)
    hard_fail_vol = _cfg("intraday_hard_fail_rel_volume", 0.8)
    if ctx.rel_volume is not None and ctx.rel_volume < hard_fail_vol:
        return False, f"rel_volume {ctx.rel_volume:.2f}× below hard floor {hard_fail_vol}×", 0

    # Spread too wide
    max_spread = _cfg("intraday_max_spread_pct", 0.4)
    hard_fail_spread = _cfg("intraday_hard_fail_spread_pct", 0.8)
    if ctx.bid_ask_spread_pct is not None and ctx.bid_ask_spread_pct > hard_fail_spread:
        return False, f"spread {ctx.bid_ask_spread_pct:.3f}% above hard ceiling {hard_fail_spread}%", 0

    # HOD no-man's land: between -1% and -4% from HOD (worst win rate bucket)
    hod_low  = _cfg("intraday_hod_noman_low",  -4.0)
    hod_high = _cfg("intraday_hod_noman_high", -1.0)
    if ctx.hod_distance_pct is not None:
        if hod_low <= ctx.hod_distance_pct < hod_high:
            return (
                False,
                f"HOD no-man's land: {ctx.hod_distance_pct:.2f}% below HOD "
                f"(worst entry zone — {hod_low}% to {hod_high}%)",
                0,
            )

    # VWAP direction: long must be above VWAP, short below
    if ctx.vwap_distance_pct is not None:
        if direction == "LONG" and ctx.vwap_distance_pct < _cfg("intraday_long_min_vwap_dist", -1.5):
            return (
                False,
                f"LONG entry {ctx.vwap_distance_pct:.2f}% below VWAP — price on wrong side",
                0,
            )
        if direction == "SHORT" and ctx.vwap_distance_pct > _cfg("intraday_short_max_vwap_dist", 1.5):
            return (
                False,
                f"SHORT entry {ctx.vwap_distance_pct:.2f}% above VWAP — price on wrong side",
                0,
            )

    # ── Soft gates → score penalty ────────────────────────────────────────────

    # Dead window: 11:00–14:30 ET requires extra score
    dead_window_penalty = _cfg("intraday_dead_window_penalty", 8)
    if ctx.in_dead_window:
        score_penalty += dead_window_penalty

    # Below-threshold rel volume (not a hard fail, but penalise)
    if ctx.rel_volume is not None and ctx.rel_volume < min_rel_vol:
        score_penalty += _cfg("intraday_low_volume_penalty", 5)

    # Spread elevated but not failing
    if ctx.bid_ask_spread_pct is not None and ctx.bid_ask_spread_pct > max_spread:
        score_penalty += _cfg("intraday_wide_spread_penalty", 4)

    # Stale signal
    max_signal_age = _cfg("intraday_max_signal_age_minutes", 15)
    if ctx.signal_age_minutes is not None and ctx.signal_age_minutes > max_signal_age:
        return (
            False,
            f"signal age {ctx.signal_age_minutes:.1f} min exceeds {max_signal_age} min limit — stale",
            0,
        )

    return True, f"INTRADAY conditions met (penalty={score_penalty})", score_penalty


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

    # Analyst headwind: SELL consensus for longs, BUY for shorts
    if direction == "LONG" and ctx.analyst_consensus in ("SELL", "STRONG_SELL"):
        return False, f"analyst consensus {ctx.analyst_consensus} — institutional headwind for LONG", 0
    if direction == "SHORT" and ctx.analyst_consensus in ("BUY", "STRONG_BUY"):
        return False, f"analyst consensus {ctx.analyst_consensus} — institutional tailwind against SHORT", 0

    # High short float without squeeze signal (traps longs)
    max_short_float = _cfg("swing_max_short_float_pct", 30.0)
    if (
        ctx.short_float_pct is not None
        and ctx.short_float_pct > max_short_float
        and (ctx.catalyst_type or "") not in ("short_squeeze",)
    ):
        return (
            False,
            f"short float {ctx.short_float_pct:.1f}% > {max_short_float}% without squeeze catalyst",
            0,
        )

    # ── Catalyst requirement: at least ONE must qualify ───────────────────────

    catalyst_reasons = []

    # 1. Earnings beat + guidance raise
    if ctx.catalyst_type in ("earnings", "earnings_beat"):
        catalyst_reasons.append(f"earnings catalyst (type={ctx.catalyst_type})")

    # 2. Analyst upgrade
    if ctx.recent_upgrade and direction == "LONG":
        catalyst_reasons.append("analyst upgrade in last 10 days")
    if ctx.recent_downgrade and direction == "SHORT":
        catalyst_reasons.append("analyst downgrade in last 10 days")

    # 3. Tactical sector rotation: ETF breakout < 10 days old
    if (
        ctx.sector_above_50d
        and ctx.sector_days_since_breakout is not None
        and ctx.sector_days_since_breakout <= _cfg("swing_sector_rotation_max_days", 10)
    ):
        catalyst_reasons.append(
            f"tactical sector rotation: {ctx.sector_etf} breakout "
            f"{ctx.sector_days_since_breakout}d ago"
        )

    # 4. News catalyst with legs (catalyst_engine score)
    min_catalyst_score = _cfg("swing_min_catalyst_score", 30)
    if (
        ctx.catalyst_score is not None
        and ctx.catalyst_score >= min_catalyst_score
        and ctx.catalyst_type not in (None, "none", "headline")
    ):
        catalyst_reasons.append(
            f"catalyst score {ctx.catalyst_score:.0f} (type={ctx.catalyst_type})"
        )

    # 5. Insider buying (Form 4 filings — strong smart-money signal)
    if direction == "LONG" and getattr(ctx, "insider_net_sentiment", None) == "BUYING":
        val = getattr(ctx, "insider_buy_value_3m", None)
        val_str = f" ${val:.1f}M net" if val is not None else ""
        catalyst_reasons.append(f"insider buying detected{val_str} (Form 4 filings, last 90 days)")
    if direction == "SHORT" and getattr(ctx, "insider_net_sentiment", None) == "SELLING":
        catalyst_reasons.append("insider selling detected (Form 4 filings, last 90 days)")

    # 6. Congressional trading (Senate / House — historically strong alpha signal)
    cong = getattr(ctx, "congressional_sentiment", None)
    if direction == "LONG" and cong == "BUYING":
        catalyst_reasons.append("congressional buying (Senate/House trades, last 90 days)")
    if direction == "SHORT" and cong == "SELLING":
        catalyst_reasons.append("congressional selling (Senate/House trades, last 90 days)")

    # 7. Overnight drift — strong overnight_drift signal is itself the catalyst for EOD/post-close entries
    if ctx.catalyst_type == "overnight_drift":
        catalyst_reasons.append("overnight drift signal — price dislocation setup for next open")

    if not catalyst_reasons:
        return (
            False,
            f"no qualifying catalyst found — SWING requires earnings beat, analyst upgrade, "
            f"sector rotation, news, insider buying, or congressional buying "
            f"(catalyst_type={ctx.catalyst_type}, catalyst_score={ctx.catalyst_score}, "
            f"recent_upgrade={ctx.recent_upgrade}, "
            f"insider={getattr(ctx, 'insider_net_sentiment', None)}, "
            f"congress={getattr(ctx, 'congressional_sentiment', None)})",
            0,
        )

    reason = f"SWING conditions met: {'; '.join(catalyst_reasons)}"
    return True, reason, 0


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

    # Analyst headwind
    if direction == "LONG" and ctx.analyst_consensus in ("SELL", "STRONG_SELL"):
        return False, f"analyst consensus {ctx.analyst_consensus} — cannot hold POSITION against institutional SELL", 0
    if direction == "SHORT" and ctx.analyst_consensus in ("BUY", "STRONG_BUY"):
        return False, f"analyst consensus {ctx.analyst_consensus} — cannot hold SHORT POSITION against institutional BUY", 0

    # Revenue decelerating two consecutive quarters
    if ctx.revenue_decelerating:
        return False, "revenue decelerating QoQ — POSITION thesis requires sustained growth", 0

    # ── Primary condition 1: Revenue growth ───────────────────────────────────
    min_rev_growth = _cfg("position_min_revenue_growth_yoy", 15.0)
    if ctx.revenue_growth_yoy is None:
        return (
            False,
            "revenue_growth_yoy unavailable — cannot confirm POSITION primary condition 1",
            0,
        )
    if ctx.revenue_growth_yoy < min_rev_growth:
        return (
            False,
            f"revenue growth {ctx.revenue_growth_yoy:.1f}% < {min_rev_growth}% threshold — "
            "POSITION requires sustained revenue expansion",
            0,
        )

    # ── Primary condition 2: Sector in structural uptrend ─────────────────────
    if ctx.sector_above_50d is None:
        return (
            False,
            f"sector ETF context unavailable for {ctx.sector_etf} — "
            "cannot confirm POSITION primary condition 2",
            0,
        )

    if not ctx.sector_above_50d:
        return (
            False,
            f"sector ETF {ctx.sector_etf} below 50-day MA — "
            "POSITION requires structural sector uptrend",
            0,
        )

    min_sector_outperf = _cfg("position_min_sector_vs_spy", 5.0)
    if (
        ctx.sector_3m_vs_spy is not None
        and ctx.sector_3m_vs_spy < min_sector_outperf
    ):
        return (
            False,
            f"sector {ctx.sector_etf} 3m vs SPY = {ctx.sector_3m_vs_spy:.1f}% — "
            f"below {min_sector_outperf}% outperformance threshold",
            0,
        )

    # ── Primary condition 3: technical base is handled by signal engine ───────
    # (squeeze + breakout dimensions must be elevated — enforced by score threshold)

    reason = (
        f"POSITION conditions met: "
        f"rev_growth_yoy={ctx.revenue_growth_yoy:.1f}%, "
        f"sector {ctx.sector_etf} above 50d, "
        f"sector_3m_vs_spy={ctx.sector_3m_vs_spy:.1f}%"
    )
    return True, reason, 0


# ── Trade type classifier ─────────────────────────────────────────────────────


def classify_trade_type(
    direction: str,
    ctx: TradeContext,
    score: int,
) -> tuple[str, str, int]:
    """
    Classify a setup into POSITION / SWING / INTRADAY / REJECT.

    Follows the hierarchy:
      POSITION → SWING → INTRADAY → REJECT

    Returns (trade_type, reason, effective_score).
    effective_score = score - score_penalty (for INTRADAY dead-window penalty).

    Callers should reject if effective_score < min_score_to_trade.
    """
    # Try POSITION first
    ok, reason, penalty = _validate_position(direction, ctx)
    if ok:
        return "POSITION", reason, score

    pos_fail_reason = reason

    # Try SWING
    ok, reason, penalty = _validate_swing(direction, ctx)
    if ok:
        return "SWING", reason, score

    swing_fail_reason = reason

    # Try INTRADAY
    ok, reason, penalty = _validate_intraday(direction, ctx)
    if ok:
        return "INTRADAY", reason, score - penalty

    intraday_fail_reason = reason

    # REJECT — log why each type failed
    reject_reason = (
        f"REJECT — no qualifying trade type. "
        f"POSITION: {pos_fail_reason} | "
        f"SWING: {swing_fail_reason} | "
        f"INTRADAY: {intraday_fail_reason}"
    )
    log.info("entry_gate: %s %s %s", ctx.symbol, direction, reject_reason)
    return "REJECT", reject_reason, score


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
