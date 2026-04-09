# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  execution_agent.py                         ║
# ║   Deterministic execution planner — no LLM call              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Sits between the agent signal pipeline and IBKR order placement.
Receives the pre-approved trade context and returns a structured
ExecutionPlan that governs order type, aggression, and FillWatcher
parameters for that specific trade.

Pure deterministic rules — no LLM call. All decision logic was already
encoded in the prior system prompt; moved to Python for speed, cost,
and reliability. Falls back to static CONFIG["fill_watcher"] values
on any exception — a failed execution planner must never block a trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import CONFIG

log = logging.getLogger("decifer.execution_agent")


# ══════════════════════════════════════════════════════════════
# DATA CONTRACT
# ══════════════════════════════════════════════════════════════

@dataclass
class ExecutionPlan:
    """
    Structured output from the execution planner.

    fill_watcher_params keys:
        initial_wait_secs, interval_secs, max_attempts, step_pct, max_chase_pct
    """
    order_type: str            # "LIMIT" | "MKT"
    limit_price: float         # 0 = use system default (bid/ask midpoint logic)
    aggression: str            # "patient" | "normal" | "aggressive"
    split_into_n_tranches: int # 1 or 2
    timeout_secs: int          # total fill watcher lifetime in seconds
    fallback_strategy: str     # "cancel" | "market" | "retry"
    fill_watcher_params: dict  # per-trade FillWatcher overrides
    reasoning: str             # one sentence describing primary decision factor


# ══════════════════════════════════════════════════════════════
# AGGRESSION PARAMS MAP
# ══════════════════════════════════════════════════════════════

_PARAMS = {
    "patient": {
        "initial_wait_secs": 45,
        "interval_secs":     25,
        "max_attempts":      2,
        "step_pct":          0.001,
        "max_chase_pct":     0.005,
    },
    "normal": {
        "initial_wait_secs": 30,
        "interval_secs":     20,
        "max_attempts":      3,
        "step_pct":          0.002,
        "max_chase_pct":     0.010,
    },
    "aggressive": {
        "initial_wait_secs": 15,
        "interval_secs":     15,
        "max_attempts":      4,
        "step_pct":          0.003,
        "max_chase_pct":     0.015,
    },
}


# ══════════════════════════════════════════════════════════════
# FALLBACK
# ══════════════════════════════════════════════════════════════

def _fallback_plan() -> ExecutionPlan:
    """Build an ExecutionPlan from static CONFIG values. Never raises."""
    fw = CONFIG.get("fill_watcher", {})
    iw = float(fw.get("initial_wait_secs", 30))
    ma = int(fw.get("max_attempts", 3))
    iv = float(fw.get("interval_secs", 20))
    return ExecutionPlan(
        order_type="LIMIT",
        limit_price=0,
        aggression="normal",
        split_into_n_tranches=1,
        timeout_secs=int(iw + ma * iv),
        fallback_strategy="cancel",
        fill_watcher_params={
            "initial_wait_secs": iw,
            "interval_secs":     iv,
            "max_attempts":      ma,
            "step_pct":          float(fw.get("step_pct", 0.002)),
            "max_chase_pct":     float(fw.get("max_chase_pct", 0.01)),
        },
        reasoning="Fallback: using static config values.",
    )


# ══════════════════════════════════════════════════════════════
# DETERMINISTIC RULES
# ══════════════════════════════════════════════════════════════

def _determine_aggression(
    spread_pct: float,
    rel_volume: float,
    vwap_dist_pct: float,
    time_str: str,
    score: int,
    regime_name: str,
) -> str:
    """
    Return "patient" | "normal" | "aggressive" from market microstructure inputs.

    Resolution rule: most conservative wins. "aggressive" only when 3+ factors
    independently vote aggressive and none vote patient.
    """
    tiers = []

    # 1. Spread width
    if spread_pct > 0.5:
        tiers.append("aggressive")  # wide spread — hit market before it widens more
    elif spread_pct > 0.2:
        tiers.append("normal")
    else:
        tiers.append("patient")     # tight spread — let market come to us

    # 2. Relative volume
    if rel_volume > 2.0:
        tiers.append("aggressive")
    elif rel_volume >= 0.5:
        tiers.append("normal")
    else:
        tiers.append("patient")     # thin book — MKT order risks slippage

    # 3. Time of day (ET)
    try:
        hour, minute = int(time_str.split(":")[0]), int(time_str.split(":")[1])
    except (ValueError, IndexError):
        hour, minute = 11, 0        # default to normal session on parse error

    if hour == 9 and minute < 60:
        tiers.append("patient")     # open volatility
    elif hour == 9 and minute >= 30:
        tiers.append("patient")
    elif 10 <= hour < 11:
        tiers.append("normal")      # morning ideal window
    elif 11 <= hour < 14:
        tiers.append("patient")     # lunch thin
    elif 14 <= hour < 15:
        tiers.append("normal")      # afternoon recovery
    elif hour >= 15:
        tiers.append("aggressive")  # close — fill or cancel
    else:
        tiers.append("normal")

    # 4. Regime
    regime_map = {
        "CAPITULATION":  "patient",
        "RANGE_BOUND":   "patient",
        "RELIEF_RALLY":  "patient",
        "TRENDING_DOWN": "normal",
        "TRENDING_UP":   "normal",
    }
    tiers.append(regime_map.get(regime_name, "normal"))

    # 5. Conviction score (0–50)
    if score > 35:
        tiers.append("aggressive")
    elif score < 20:
        tiers.append("patient")
    else:
        tiers.append("normal")

    # Resolution: most conservative wins
    if "patient" in tiers:
        return "patient"
    if tiers.count("aggressive") >= 3:
        return "aggressive"
    return "normal"


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def get_execution_plan(
    symbol: str,
    direction: str,        # "LONG" | "SHORT"
    size: int,             # share count
    conviction_score: int, # 0–50
    bid: float,
    ask: float,
    spread_pct: float,     # (ask - bid) / ask × 100, e.g. 0.42
    rel_volume: float,     # relative volume vs 10d avg, e.g. 1.8
    vwap_dist_pct: float,  # (price - vwap) / vwap × 100, e.g. 0.35
    time_of_day_str: str,  # "HH:MM" ET, e.g. "10:47"
    regime_name: str,      # e.g. "BULL_TRENDING"
) -> ExecutionPlan:
    """
    Determine execution parameters deterministically from market microstructure.

    Returns an ExecutionPlan. Falls back to _fallback_plan() on any exception.
    """
    ea_cfg = CONFIG.get("execution_agent", {})
    if not ea_cfg.get("enabled", True):
        return _fallback_plan()

    try:
        aggression = _determine_aggression(
            spread_pct, rel_volume, vwap_dist_pct,
            time_of_day_str, conviction_score, regime_name,
        )

        fw = _PARAMS[aggression].copy()

        # MKT only when genuinely liquid AND spread is wide (slippage is acceptable)
        order_type = "MKT" if (spread_pct > 0.5 and rel_volume > 2.0) else "LIMIT"

        timeout = int(fw["initial_wait_secs"] + fw["max_attempts"] * fw["interval_secs"])

        reasoning_map = {
            "patient":    f"patient fill: spread={spread_pct:.3f}% rel_vol={rel_volume:.1f}x score={conviction_score}",
            "normal":     f"normal fill: spread={spread_pct:.3f}% rel_vol={rel_volume:.1f}x score={conviction_score}",
            "aggressive": f"aggressive fill: spread={spread_pct:.3f}% rel_vol={rel_volume:.1f}x score={conviction_score}",
        }

        plan = ExecutionPlan(
            order_type=order_type,
            limit_price=0,
            aggression=aggression,
            split_into_n_tranches=1,
            timeout_secs=timeout,
            fallback_strategy="cancel",
            fill_watcher_params=fw,
            reasoning=reasoning_map[aggression],
        )

        log.info(
            f"ExecutionPlan {symbol}: type={plan.order_type} aggr={plan.aggression} "
            f"wait={fw['initial_wait_secs']}s attempts={fw['max_attempts']} | {plan.reasoning}"
        )
        return plan

    except Exception as exc:
        if ea_cfg.get("fallback_on_error", True):
            log.warning(f"execution_agent: falling back to static config for {symbol} ({exc})")
            return _fallback_plan()
        raise
