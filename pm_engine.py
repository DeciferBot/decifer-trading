"""
pm_engine.py — Portfolio Management Engine.

Replaces rotation_live_v1.py. Evaluates every held position on each call
and generates typed PortfolioAction recommendations via deterministic scoring.

Called from:
  orders_core.execute_buy()  — trigger="margin_cap_block"  (replaces _rlv1 call)
  bot_trading.py scan cycle  — trigger="scan_cycle"        (proactive PM)

Architecture:
  pm_thesis.py  — position enrichment + thesis classification
  pm_rails.py   — safety rails (10 checks, applied post-selection)
  pm_engine.py  — action generation, scoring, execution, decision log

Decision log: data/pm_engine/decisions.jsonl
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

_DECISIONS_DIR  = pathlib.Path("data/pm_engine")
_DECISIONS_FILE = _DECISIONS_DIR / "decisions.jsonl"

UTC = datetime.timezone.utc


# ── Types ─────────────────────────────────────────────────────────────────────

class ActionType(str, Enum):
    HOLD      = "HOLD"
    ADD       = "ADD"
    DCA       = "DCA"
    TRIM      = "TRIM"
    FULL_EXIT = "FULL_EXIT"
    ROTATE    = "ROTATE"
    DO_NOTHING = "DO_NOTHING"


@dataclass
class PMAction:
    action_type:          ActionType
    symbol:               str
    proposed_notional:    float | None
    action_score:         float
    rationale:            str
    trigger:              str
    holding_period_hours: float = 0.0
    cost_advantage_pct:   float | None = None
    thesis_status:        str  = ""
    score_delta:          float = 0.0
    unrealised_pnl_pct:   float = 0.0
    safety_blocked:       bool  = False
    safety_block_reason:  str | None = None


# ── Public entry point ────────────────────────────────────────────────────────

def evaluate(
    trigger: str,
    active_trades_snapshot: dict[str, Any],
    candidates: list[dict] | None = None,
    candidate_symbol: str | None = None,
    candidate_score: int | None = None,
) -> None:
    """
    Evaluate every held position and generate PM action recommendations.
    Logs one decision record per position. Executes if flag ON and rails pass.
    """
    from config import CONFIG
    from pm_thesis import build_position, PMPosition

    nlv = _get_nlv()
    if nlv is None or nlv <= 0:
        log.debug("pm_engine.evaluate: NLV unavailable — skipping")
        return

    candidate_scores = _extract_candidate_scores(candidates or [])
    best_candidate   = _best_candidate(candidates or [], candidate_symbol, candidate_score)

    positions = []
    for sym, pos in active_trades_snapshot.items():
        if isinstance(pos, dict) and pos.get("status") not in ("RESERVED", "EXITING"):
            p = build_position(sym, pos, nlv, candidate_scores)
            if p is not None:
                positions.append(p)

    if not positions:
        log.debug("pm_engine.evaluate: no evaluable positions")
        return

    for pos in positions:
        actions = _generate_actions(pos, best_candidate, trigger, CONFIG)
        if not actions:
            continue
        actions.sort(key=lambda a: a.action_score, reverse=True)
        selected = actions[0]

        import pm_rails
        flag_on = bool(CONFIG.get("ENABLE_PM_ENGINE", False))
        selected = pm_rails.apply(selected, nlv, CONFIG)

        # Fallback to next-best is a live-execution concern only.
        # In HYPOTHETICAL mode, log the intended (top-scoring) action —
        # whether it would pass or fail the rails — for accurate monitoring.
        if flag_on and selected.safety_blocked and len(actions) > 1:
            for alt in actions[1:]:
                alt = pm_rails.apply(alt, nlv, CONFIG)
                if not alt.safety_blocked:
                    selected = alt
                    break

        _execute(selected, nlv, CONFIG)
        _log(selected, trigger, nlv, candidate_symbol, candidate_score)


# ── Action generation ─────────────────────────────────────────────────────────

def _generate_actions(
    pos: "PMPosition",
    best_candidate: dict | None,
    trigger: str,
    cfg: dict,
) -> list[PMAction]:
    from pm_thesis import ThesisStatus

    actions: list[PMAction] = [_do_nothing(pos)]

    trim_pct  = float(cfg.get("PM_DEFAULT_TRIM_PCT", 0.33))
    oversize  = float(cfg.get("PM_OVERSIZE_THRESHOLD", 0.06))
    target    = float(cfg.get("PM_TARGET_POSITION_PCT", 0.04))
    rotate_adv = float(cfg.get("PM_MIN_ROTATE_ADVANTAGE", 10))
    min_hold   = float(cfg.get("PM_MIN_HOLD_HOURS", 4.0))
    tx_cost    = float(cfg.get("PM_TRANSACTION_COST_PCT", 0.001))

    churn_penalty = 30.0 if pos.holding_period_hours < min_hold else 0.0

    # FULL_EXIT — thesis broken or played out, or severe decay
    if pos.thesis_status in (ThesisStatus.BROKEN, ThesisStatus.PLAYED_OUT) or pos.score_delta < -20:
        opp = (best_candidate["score"] - pos.current_score) if best_candidate else 0
        score = max(0, -pos.score_delta * 1.5) + max(0, -pos.unrealised_pnl_pct * 100) + max(0, opp) - churn_penalty
        actions.append(PMAction(
            action_type=ActionType.FULL_EXIT,
            symbol=pos.symbol,
            proposed_notional=pos.market_value,
            action_score=score,
            rationale=_exit_rationale(pos),
            trigger=trigger,
            holding_period_hours=pos.holding_period_hours,
            thesis_status=pos.thesis_status.value,
            score_delta=pos.score_delta,
            unrealised_pnl_pct=pos.unrealised_pnl_pct,
        ))

    # TRIM — decaying or played-out thesis, oversized position
    # PLAYED_OUT also generates TRIM (partial exit is better than nothing when
    # the thesis has run its course but FULL_EXIT is blocked by the notional cap).
    if pos.thesis_status in (ThesisStatus.DECAYING, ThesisStatus.PLAYED_OUT) or pos.position_pct_nlv > oversize:
        trim_notional = pos.market_value * trim_pct
        opp = (best_candidate["score"] - pos.current_score) if best_candidate else 0
        score = max(0, -pos.score_delta * 0.8) + max(0, (pos.position_pct_nlv - oversize) * 100) + max(0, opp * 0.5) - churn_penalty
        actions.append(PMAction(
            action_type=ActionType.TRIM,
            symbol=pos.symbol,
            proposed_notional=trim_notional,
            action_score=score,
            rationale=_trim_rationale(pos, trim_pct),
            trigger=trigger,
            holding_period_hours=pos.holding_period_hours,
            thesis_status=pos.thesis_status.value,
            score_delta=pos.score_delta,
            unrealised_pnl_pct=pos.unrealised_pnl_pct,
        ))

    # HOLD — default positive action for INTACT, DECAYING, STRENGTHENING, UNKNOWN.
    # Not generated for BROKEN (always exit) or PLAYED_OUT (exit or trim, not hold).
    if pos.thesis_status not in (ThesisStatus.BROKEN, ThesisStatus.PLAYED_OUT):
        score = 20 + max(0, pos.score_delta * 0.5)
        actions.append(PMAction(
            action_type=ActionType.HOLD,
            symbol=pos.symbol,
            proposed_notional=None,
            action_score=score,
            rationale=f"Thesis {pos.thesis_status.value}. Score delta {pos.score_delta:+.0f}.",
            trigger=trigger,
            holding_period_hours=pos.holding_period_hours,
            thesis_status=pos.thesis_status.value,
            score_delta=pos.score_delta,
            unrealised_pnl_pct=pos.unrealised_pnl_pct,
        ))

    # DCA — intact/strengthening thesis, in loss, conviction solid
    if pos.thesis_status in (ThesisStatus.INTACT, ThesisStatus.STRENGTHENING) and pos.unrealised_pnl_pct < -0.03:
        dca_notional = pos.market_value * 0.5
        score = 25 + pos.score_delta - churn_penalty
        if pos.unrealised_pnl_pct < -0.05:
            score -= 15  # penalise DCA into deep loss
        actions.append(PMAction(
            action_type=ActionType.DCA,
            symbol=pos.symbol,
            proposed_notional=dca_notional,
            action_score=score,
            rationale=f"Thesis {pos.thesis_status.value}. In loss {pos.unrealised_pnl_pct:.1%}. DCA to reduce average.",
            trigger=trigger,
            holding_period_hours=pos.holding_period_hours,
            thesis_status=pos.thesis_status.value,
            score_delta=pos.score_delta,
            unrealised_pnl_pct=pos.unrealised_pnl_pct,
        ))

    # ADD — strengthening thesis, undersized position
    if pos.thesis_status == ThesisStatus.STRENGTHENING and pos.position_pct_nlv < target:
        add_notional = pos.market_value * 0.5
        score = 30 + pos.score_delta - churn_penalty
        actions.append(PMAction(
            action_type=ActionType.ADD,
            symbol=pos.symbol,
            proposed_notional=add_notional,
            action_score=score,
            rationale=f"Thesis STRENGTHENING. Position {pos.position_pct_nlv:.1%} NLV < target {target:.0%}. Add.",
            trigger=trigger,
            holding_period_hours=pos.holding_period_hours,
            thesis_status=pos.thesis_status.value,
            score_delta=pos.score_delta,
            unrealised_pnl_pct=pos.unrealised_pnl_pct,
        ))

    # ROTATE — margin cap trigger AND candidate clearly better
    if trigger == "margin_cap_block" and best_candidate is not None:
        advantage = best_candidate["score"] - pos.current_score
        cost_advantage_pct = advantage / max(pos.current_score, 1)
        if advantage > rotate_adv:
            score = advantage - churn_penalty - tx_cost * 200
            actions.append(PMAction(
                action_type=ActionType.ROTATE,
                symbol=pos.symbol,
                proposed_notional=pos.market_value,
                action_score=score,
                rationale=(
                    f"Candidate {best_candidate['symbol']} scores {best_candidate['score']}, "
                    f"held {pos.symbol} scores {pos.current_score:.0f} (advantage {advantage:.0f}). "
                    f"Rotate to free capacity."
                ),
                trigger=trigger,
                holding_period_hours=pos.holding_period_hours,
                cost_advantage_pct=cost_advantage_pct,
                thesis_status=pos.thesis_status.value,
                score_delta=pos.score_delta,
                unrealised_pnl_pct=pos.unrealised_pnl_pct,
            ))

    return actions


# ── Execution ─────────────────────────────────────────────────────────────────

def _execute(action: PMAction, nlv: float, cfg: dict) -> None:
    """Execute the action if the feature flag is ON and rails passed."""
    if action.safety_blocked:
        return
    if not cfg.get("ENABLE_PM_ENGINE", False):
        return

    import bot_state
    import orders_core

    ib = bot_state.ib

    if action.action_type == ActionType.FULL_EXIT:
        try:
            ok = orders_core.execute_sell(ib, action.symbol, reason=action.rationale)
            if ok:
                import pm_rails
                pm_rails.increment_daily_count()
                log.info("pm_engine FULL_EXIT executed: %s", action.symbol)
            else:
                action.safety_blocked = True
                action.safety_block_reason = "execute_sell_returned_false"
        except Exception as exc:
            log.warning("pm_engine FULL_EXIT error %s: %s", action.symbol, exc)
            action.safety_blocked = True
            action.safety_block_reason = f"execute_sell_raised: {exc}"

    elif action.action_type == ActionType.TRIM:
        try:
            import bot_state as _bs
            pos = _bs.active_trades.get(action.symbol, {})
            qty = int(pos.get("qty", 0))
            trim_qty = max(1, round(qty * float(cfg.get("PM_DEFAULT_TRIM_PCT", 0.33))))
            ok = orders_core.execute_sell(
                ib, action.symbol, reason=action.rationale, qty_override=trim_qty
            )
            if ok:
                import pm_rails
                pm_rails.increment_daily_count()
                log.info("pm_engine TRIM executed: %s qty=%d", action.symbol, trim_qty)
            else:
                action.safety_blocked = True
                action.safety_block_reason = "execute_sell_returned_false"
        except Exception as exc:
            log.warning("pm_engine TRIM error %s: %s", action.symbol, exc)
            action.safety_blocked = True
            action.safety_block_reason = f"execute_sell_raised: {exc}"

    elif action.action_type == ActionType.ROTATE:
        try:
            ok = orders_core.execute_sell(ib, action.symbol, reason=action.rationale)
            if ok:
                import pm_rails
                pm_rails.increment_daily_count()
                log.info(
                    "pm_engine ROTATE executed: exited %s — candidate will enter on next scan",
                    action.symbol,
                )
            else:
                action.safety_blocked = True
                action.safety_block_reason = "execute_sell_returned_false"
        except Exception as exc:
            log.warning("pm_engine ROTATE error %s: %s", action.symbol, exc)
            action.safety_blocked = True
            action.safety_block_reason = f"execute_sell_raised: {exc}"
    # ADD and DCA are logged as recommendations only — entry happens via Apex on next scan cycle


# ── Decision log ──────────────────────────────────────────────────────────────

def _log(
    action: PMAction,
    trigger: str,
    nlv: float,
    candidate_symbol: str | None,
    candidate_score: int | None,
) -> None:
    from config import CONFIG
    flag_on = bool(CONFIG.get("ENABLE_PM_ENGINE", False))
    # SAFETY_BLOCKED: a real market-condition rail fired (stale quote, bad spread,
    #                 excessive notional, cooldown, etc.) — logged regardless of flag.
    # EXECUTED:       flag on + rails passed + action requires order submission.
    # HYPOTHETICAL:   flag off (engine not yet activated) or action needs no execution
    #                 (HOLD, DO_NOTHING always hypothetical regardless of flag state).
    final_status = (
        "SAFETY_BLOCKED" if action.safety_blocked
        else ("EXECUTED" if flag_on
              and action.action_type not in (ActionType.HOLD, ActionType.DO_NOTHING)
              else "HYPOTHETICAL")
    )
    record = {
        "ts":                   datetime.datetime.now(UTC).isoformat(),
        "trigger":              trigger,
        "symbol":               action.symbol,
        "action_type":          action.action_type.value,
        "proposed_notional":    round(action.proposed_notional, 2) if action.proposed_notional else None,
        "action_score":         round(action.action_score, 2),
        "rationale":            action.rationale,
        "thesis_status":        action.thesis_status,
        "score_delta":          round(action.score_delta, 2),
        "unrealised_pnl_pct":   round(action.unrealised_pnl_pct, 4),
        "holding_period_hours": round(action.holding_period_hours, 2),
        "nlv":                  round(nlv, 2) if nlv else None,
        "safety_blocked":       action.safety_blocked,
        "safety_block_reason":  action.safety_block_reason,
        "candidate_symbol":     candidate_symbol,
        "candidate_score":      candidate_score,
        "final_status":         final_status,
    }
    try:
        _DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
        with _DECISIONS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("pm_engine log write failed: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_nlv() -> float | None:
    try:
        import bot_state
        val = bot_state.account_values.get("NetLiquidation")
        return float(val) if val is not None else None
    except Exception:
        return None


def _extract_candidate_scores(candidates: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in candidates:
        sym = c.get("symbol") or c.get("ticker")
        score = c.get("score") or c.get("final_score")
        if sym and score is not None:
            try:
                out[sym] = float(score)
            except (TypeError, ValueError):
                pass
    return out


def _best_candidate(
    candidates: list[dict],
    candidate_symbol: str | None,
    candidate_score: int | None,
) -> dict | None:
    if candidate_symbol and candidate_score is not None:
        return {"symbol": candidate_symbol, "score": candidate_score}
    if not candidates:
        return None
    best = max(candidates, key=lambda c: float(c.get("score") or c.get("final_score") or 0))
    sym = best.get("symbol") or best.get("ticker")
    score = best.get("score") or best.get("final_score")
    if sym and score is not None:
        return {"symbol": sym, "score": float(score)}
    return None


def _exit_rationale(pos: "PMPosition") -> str:
    parts = [f"Thesis {pos.thesis_status.value}."]
    if pos.score_delta < -15:
        parts.append(f"Score decayed {pos.score_delta:+.0f} pts from entry.")
    if pos.unrealised_pnl_pct < -0.05:
        parts.append(f"Unrealised loss {pos.unrealised_pnl_pct:.1%}.")
    return " ".join(parts)


def _trim_rationale(pos: "PMPosition", trim_pct: float) -> str:
    parts = [f"Thesis {pos.thesis_status.value}."]
    if pos.score_delta < 0:
        parts.append(f"Score delta {pos.score_delta:+.0f}.")
    parts.append(f"Position {pos.position_pct_nlv:.1%} NLV. Trim {trim_pct:.0%}.")
    return " ".join(parts)


def _do_nothing(pos: "PMPosition") -> PMAction:
    return PMAction(
        action_type=ActionType.DO_NOTHING,
        symbol=pos.symbol,
        proposed_notional=None,
        action_score=5.0,
        rationale=(
            f"Thesis {pos.thesis_status.value}. "
            f"Score delta {pos.score_delta:+.0f}. "
            f"PnL {pos.unrealised_pnl_pct:+.1%}. "
            "No actionable signal."
        ),
        trigger="",
        holding_period_hours=pos.holding_period_hours,
        thesis_status=pos.thesis_status.value,
        score_delta=pos.score_delta,
        unrealised_pnl_pct=pos.unrealised_pnl_pct,
    )
