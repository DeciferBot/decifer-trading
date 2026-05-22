"""
pm_rails.py — Safety rails for the Portfolio Management Engine.

Single responsibility: given a selected PMAction, validate it against
9 market-condition safety checks and return it blocked or cleared.

Rails are applied AFTER action selection — they validate execution intent,
not decision logic. This is the key architectural difference from the old
G1-G9 waterfall where gates were mixed into decision logic.

Note: the feature flag (ENABLE_PM_ENGINE) is NOT a safety rail — it is an
activation gate that lives in _execute() in pm_engine.py. Rails check market
conditions so they produce useful SAFETY_BLOCKED signals in HYPOTHETICAL mode
(e.g. stale quote still shows as SAFETY_BLOCKED even when the flag is off).

Daily counter is module-level state (same pattern as rotation_live_v1).
"""
from __future__ import annotations

import datetime
import threading

UTC = datetime.timezone.utc

_daily_lock  = threading.Lock()
_daily_date: str = ""
_daily_count: int = 0


def daily_limit_exceeded(max_per_day: int) -> bool:
    global _daily_date, _daily_count
    today = datetime.datetime.now(UTC).strftime("%Y-%m-%d")
    with _daily_lock:
        if _daily_date != today:
            _daily_date = today
            _daily_count = 0
        return _daily_count >= max_per_day


def increment_daily_count() -> None:
    global _daily_count
    with _daily_lock:
        _daily_count += 1


def apply(action: "PMAction", nlv: float, cfg: dict) -> "PMAction":
    """
    Run action through 9 market-condition safety rails.
    First failing rail sets safety_blocked=True and short-circuits.
    DO_NOTHING always passes all rails (it requires no execution).

    The feature flag (ENABLE_PM_ENGINE) is intentionally NOT a rail here —
    it lives in pm_engine._execute(). This allows HYPOTHETICAL mode to show
    genuine SAFETY_BLOCKED signals (stale quote, bad spread) rather than
    masking everything behind a feature_flag_off block.
    """
    from pm_engine import ActionType
    from pm_thesis import _quote_info

    def block(reason: str) -> "PMAction":
        action.safety_blocked = True
        action.safety_block_reason = reason
        return action

    # DO_NOTHING never requires execution — no rails apply
    if action.action_type == ActionType.DO_NOTHING:
        return action

    passthrough = {ActionType.HOLD}

    # Rail 1 — daily limit (execution actions only)
    if action.action_type not in passthrough:
        if daily_limit_exceeded(int(cfg.get("PM_MAX_ACTIONS_PER_DAY", 3))):
            return block("daily_limit_exceeded")

    # Rail 2 — account values fresh
    if not _account_is_fresh(float(cfg.get("PM_ACCOUNT_MAX_AGE_S", 300))):
        return block("account_values_stale")

    # Rail 3 — quote fresh
    _, age = _quote_info(action.symbol)
    max_age = float(cfg.get("PM_QUOTE_MAX_AGE_S", 30))
    if age is None or age > max_age:
        return block("quote_stale")

    # Rail 4 — spread acceptable
    spread, _ = _quote_info(action.symbol)
    max_spread = float(cfg.get("PM_MAX_SPREAD_PCT", 0.01))
    if spread is not None and spread > max_spread:
        return block("spread_unacceptable")

    # Rail 5 — valid NLV
    if nlv is None or nlv <= 0:
        return block("invalid_nlv")

    # Rail 6 — proposed action notional ≤ PM_MAX_ACTION_NLV_PCT × NLV
    #           Checks the PROPOSED notional, not the full position value.
    #           A 5% NLV position providing a 1% trim passes this rail.
    if action.action_type not in passthrough and action.proposed_notional is not None:
        max_action = nlv * float(cfg.get("PM_MAX_ACTION_NLV_PCT", 0.02))
        if action.proposed_notional > max_action:
            return block(
                f"action_notional_{action.proposed_notional:.0f}_exceeds_{max_action:.0f}"
            )

    # Rail 7 — minimum useful action notional
    min_notional = float(cfg.get("PM_MIN_ACTION_NOTIONAL", 500.0))
    if action.action_type not in passthrough and action.proposed_notional is not None:
        if action.proposed_notional < min_notional:
            return block("action_notional_too_small")

    # Rail 8 — cooldown after entry
    cooldown_h = float(cfg.get("PM_COOLDOWN_HOURS", 2.0))
    if action.action_type not in passthrough:
        if action.holding_period_hours < cooldown_h:
            return block(
                f"cooldown_{action.holding_period_hours:.1f}h_lt_{cooldown_h}h"
            )

    # Rail 9 — cost hurdle (ROTATE only)
    if action.action_type == ActionType.ROTATE:
        cost_floor = float(cfg.get("PM_TRANSACTION_COST_PCT", 0.001)) * 2
        if action.cost_advantage_pct is not None and action.cost_advantage_pct < cost_floor:
            return block("cost_hurdle_not_met")

    return action


def _account_is_fresh(max_age_s: float) -> bool:
    try:
        import time
        import bot_state
        updated_at = bot_state.account_values_updated_at
        if updated_at is None:
            return False
        return (time.time() - updated_at) <= max_age_s
    except Exception:
        return False
