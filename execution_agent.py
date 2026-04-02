# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  execution_agent.py                         ║
# ║   7th Claude agent — decides HOW to execute each trade       ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Sits between the 6-agent signal pipeline and IBKR order placement.
Receives the pre-approved trade context and returns a structured
ExecutionPlan that governs order type, aggression, and FillWatcher
parameters for that specific trade.

One Claude API call per trade, synchronous.
Falls back to static CONFIG["fill_watcher"] values on any error —
a failed execution agent must never block a trade.
"""

import json
import logging
from dataclasses import dataclass

import anthropic

from config import CONFIG

log = logging.getLogger("decifer.execution_agent")

# Module-level client — lazy-initialised on first call (same pattern as agents.py)
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    """Return the shared Anthropic client, creating it on first use."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    return _client


# ══════════════════════════════════════════════════════════════
# DATA CONTRACT
# ══════════════════════════════════════════════════════════════

@dataclass
class ExecutionPlan:
    """
    Structured output from the execution agent.

    fill_watcher_params keys:
        initial_wait_secs, interval_secs, max_attempts, step_pct, max_chase_pct
    """
    order_type: str            # "LIMIT" | "MKT" | "MIDPOINT"
    limit_price: float         # agent-suggested entry price; 0 = use system default
    aggression: str            # "patient" | "normal" | "aggressive"
    split_into_n_tranches: int # 1 or 2
    timeout_secs: int          # total fill watcher lifetime in seconds
    fallback_strategy: str     # "cancel" | "market" | "retry"
    fill_watcher_params: dict  # per-trade FillWatcher overrides
    reasoning: str             # one sentence from the agent


# ══════════════════════════════════════════════════════════════
# SYSTEM PROMPT  (fixed / cacheable)
# ══════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
You are the Execution Agent for Decifer, an autonomous US equity trading system \
connected to Interactive Brokers.

Your sole job is to decide HOW to execute a pre-approved trade — not WHETHER to trade.
The 6-agent signal pipeline has already decided to enter. You optimise the fill.

DECISION FRAMEWORK — reason through these in order:

1. SPREAD WIDTH
   - spread_pct > 0.5%  → prefer MKT or aggressive LIMIT (chase immediately)
   - spread_pct 0.2–0.5% → normal LIMIT with standard chase
   - spread_pct < 0.2%   → patient LIMIT, let the market come to you

2. RELATIVE VOLUME
   - rel_volume > 2.0   → market is liquid, aggressive fill is safe
   - rel_volume 0.8–2.0 → normal aggression
   - rel_volume < 0.5   → thin book, be patient; a market order risks slippage

3. VWAP DISTANCE
   - vwap_dist_pct > +0.5% → momentum may be fading, fill quickly before reversal
   - vwap_dist_pct ±0.3%   → neutral, standard fill
   - vwap_dist_pct < -0.3% → price is cheap vs institutions, be patient

4. TIME OF DAY (ET)
   - 09:30–10:00 (open)       → high volatility and wide spreads; prefer LIMIT, reduce aggression
   - 10:00–11:30 (morning)    → ideal window; normal aggression
   - 11:30–14:00 (lunch)      → thin volume; be patient, avoid MKT
   - 14:00–15:45 (afternoon)  → volume returning; normal to aggressive
   - 15:45–16:00 (close)      → aggressive or skip; slippage risk high

5. CONVICTION SCORE (0–50)
   - score > 35 → reduce initial_wait_secs, increase step_pct slightly; fill is worth chasing
   - score 20–35 → standard parameters
   - score < 20  → patient; if it doesn't fill quickly, cancel

6. REGIME
   - BULL_TRENDING  → normal to aggressive
   - BEAR_TRENDING  → patient on longs; preserve capital
   - CHOPPY         → patient; reduce max_attempts to avoid chasing noise
   - PANIC          → this trade should not happen; return LIMIT with max patience

OUTPUT RULES — CRITICAL:
- Respond with ONLY a single JSON object. No prose, no markdown, no code fences.
- fill_watcher_params.initial_wait_secs : integer, 10–60
- fill_watcher_params.interval_secs     : integer, 10–30
- fill_watcher_params.max_attempts      : integer, 1–5
- fill_watcher_params.step_pct          : float, 0.001–0.005
- fill_watcher_params.max_chase_pct     : float, 0.005–0.02
- timeout_secs must equal initial_wait_secs + max_attempts × interval_secs
- reasoning: one sentence, 10–25 words, name the primary factor driving your decision

JSON schema:
{
  "order_type":            "LIMIT",
  "limit_price":           0,
  "aggression":            "normal",
  "split_into_n_tranches": 1,
  "timeout_secs":          90,
  "fallback_strategy":     "cancel",
  "fill_watcher_params": {
    "initial_wait_secs": 30,
    "interval_secs":     20,
    "max_attempts":      3,
    "step_pct":          0.002,
    "max_chase_pct":     0.01
  },
  "reasoning": "One sentence rationale here."
}
"""


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
        reasoning="Fallback: execution agent unavailable, using static config.",
    )


# ══════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════

_VALID_ORDER_TYPES      = {"LIMIT", "MKT", "MIDPOINT"}
_VALID_AGGRESSIONS      = {"patient", "normal", "aggressive"}
_VALID_FALLBACKS        = {"cancel", "market", "retry"}
_REQUIRED_FW_KEYS       = {"initial_wait_secs", "interval_secs", "max_attempts",
                           "step_pct", "max_chase_pct"}


def _validate_and_build(data: dict) -> ExecutionPlan:
    """
    Parse and validate the Claude response dict.
    Raises ValueError on any field violation — caller catches and falls back.
    """
    ot = data.get("order_type", "")
    if ot not in _VALID_ORDER_TYPES:
        raise ValueError(f"Invalid order_type: {ot!r}")

    lp = float(data.get("limit_price", 0))
    if lp < 0:
        raise ValueError(f"limit_price must be >= 0, got {lp}")

    ag = data.get("aggression", "")
    if ag not in _VALID_AGGRESSIONS:
        raise ValueError(f"Invalid aggression: {ag!r}")

    tr = int(data.get("split_into_n_tranches", 1))
    if tr not in {1, 2}:
        raise ValueError(f"split_into_n_tranches must be 1 or 2, got {tr}")

    ts = int(data.get("timeout_secs", 0))
    if not (0 < ts <= 600):
        raise ValueError(f"timeout_secs out of range: {ts}")

    fb = data.get("fallback_strategy", "")
    if fb not in _VALID_FALLBACKS:
        raise ValueError(f"Invalid fallback_strategy: {fb!r}")

    fw = data.get("fill_watcher_params", {})
    if not isinstance(fw, dict):
        raise ValueError("fill_watcher_params must be a dict")
    missing = _REQUIRED_FW_KEYS - fw.keys()
    if missing:
        raise ValueError(f"fill_watcher_params missing keys: {missing}")
    for k, v in fw.items():
        if k in _REQUIRED_FW_KEYS and not (isinstance(v, (int, float)) and v > 0):
            raise ValueError(f"fill_watcher_params.{k} must be a positive number, got {v!r}")

    rs = data.get("reasoning", "")
    if not isinstance(rs, str) or not rs.strip():
        raise ValueError("reasoning must be a non-empty string")

    return ExecutionPlan(
        order_type=ot,
        limit_price=lp,
        aggression=ag,
        split_into_n_tranches=tr,
        timeout_secs=ts,
        fallback_strategy=fb,
        fill_watcher_params={
            "initial_wait_secs": float(fw["initial_wait_secs"]),
            "interval_secs":     float(fw["interval_secs"]),
            "max_attempts":      int(fw["max_attempts"]),
            "step_pct":          float(fw["step_pct"]),
            "max_chase_pct":     float(fw["max_chase_pct"]),
        },
        reasoning=rs.strip(),
    )


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
    Ask Claude how to execute this pre-approved trade.

    Returns an ExecutionPlan. If the execution agent is disabled or errors,
    returns _fallback_plan() using static CONFIG["fill_watcher"] values.
    """
    ea_cfg = CONFIG.get("execution_agent", {})
    if not ea_cfg.get("enabled", True):
        return _fallback_plan()

    try:
        user_msg = (
            f"symbol={symbol} direction={direction} size={size} "
            f"conviction={conviction_score}/50 "
            f"bid={bid:.2f} ask={ask:.2f} spread_pct={spread_pct:.3f} "
            f"rel_volume={rel_volume:.2f} vwap_dist_pct={vwap_dist_pct:.3f} "
            f"time_ET={time_of_day_str} regime={regime_name}"
        )

        resp = _get_client().messages.create(
            model=CONFIG["claude_model"],
            max_tokens=ea_cfg.get("max_tokens", 350),
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = resp.content[0].text.strip()
        data = json.loads(raw)
        plan = _validate_and_build(data)

        log.info(
            f"ExecutionPlan {symbol}: type={plan.order_type} aggr={plan.aggression} "
            f"wait={plan.fill_watcher_params['initial_wait_secs']}s "
            f"attempts={plan.fill_watcher_params['max_attempts']} | {plan.reasoning!r}"
        )
        return plan

    except Exception as exc:
        if ea_cfg.get("fallback_on_error", True):
            log.warning(
                f"execution_agent: falling back to static config for {symbol} ({exc})"
            )
            return _fallback_plan()
        raise
