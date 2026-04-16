# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  trade_advisor.py                          ║
# ║   Opus intelligence layer — decides PT, SL, size, and       ║
# ║   instrument before each order is placed.                   ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Single responsibility: ask Opus for execution decisions (profit target, stop loss,
position size multiplier, instrument type) given the full trade context.

The learning loop:
  - Each Opus decision is logged to data/advisor_log.json keyed by advice_id.
  - When a trade closes, record_outcome() updates the log entry with P&L and exit reason.
  - The next Opus call receives the last N completed decisions as context, enabling
    self-correction over time without any retraining.

Fallback: if the API call fails, times out, or output fails validation, the ATR
formula from position_sizing.calculate_stops() is used for that field only.
All guardrails (R:R floor, direction sanity, max distance) are enforced regardless.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anthropic

from config import CONFIG
from position_sizing import calculate_stops

log = logging.getLogger("decifer.advisor")

ADVISOR_LOG_PATH = Path("data/advisor_log.json")
_MAX_ADVISOR_ENTRIES = 500  # Trim to this many entries on every save to prevent unbounded growth

# ── Dataclass ─────────────────────────────────────────────────────────────────


@dataclass
class TradeAdvice:
    advice_id: str
    instrument: str  # "COMMON" | "CALL" | "PUT"
    size_multiplier: float  # 0.25–2.0 applied to formula base size
    profit_target: float  # absolute price level
    stop_loss: float  # absolute price level
    reasoning: str  # Opus rationale (logged only, never acted upon)
    source: str = "opus"  # "opus" | "formula" — which path produced this advice


# ── Log helpers ───────────────────────────────────────────────────────────────


def _load_log() -> dict:
    if ADVISOR_LOG_PATH.exists():
        try:
            return json.loads(ADVISOR_LOG_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_log(data: dict) -> None:
    ADVISOR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Trim to the newest _MAX_ADVISOR_ENTRIES by timestamp so the log never grows unbounded.
    # Entries without outcomes are still kept — they may get outcomes soon.
    if len(data) > _MAX_ADVISOR_ENTRIES:
        items = sorted(data.items(), key=lambda kv: kv[1].get("timestamp", ""), reverse=True)
        data = dict(items[:_MAX_ADVISOR_ENTRIES])
    ADVISOR_LOG_PATH.write_text(json.dumps(data, indent=2))


def _recent_history(data: dict, n: int) -> list:
    """Return the last n completed decisions (those with pnl recorded)."""
    completed = [r for r in data.values() if r.get("pnl") is not None]
    completed.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return completed[:n]


# ── Prompt builder ────────────────────────────────────────────────────────────


def _build_prompt(
    symbol: str,
    direction: str,
    entry_price: float,
    atr_5m: float,
    atr_daily: float,
    conviction_score: float,
    dimension_scores: dict,
    rationale: str,
    regime_context: str,
    history: list,
    trade_type: str = "INTRADAY",
) -> str:
    # Format dimension scores compactly
    dims = ", ".join(f"{k}={v:.1f}" for k, v in dimension_scores.items() if v != 0)

    # Format recent history as a compact table
    if history:
        rows = []
        for r in history:
            outcome = "WIN" if (r.get("pnl") or 0) > 0 else "LOSS"
            rows.append(
                f"  {r['symbol']} {r['direction']} entry=${r['entry_price']:.2f} "
                f"PT=${r['profit_target']:.2f} SL=${r['stop_loss']:.2f} "
                f"size_mult={r['size_multiplier']} → {outcome} PnL=${r.get('pnl', 0):.2f} "
                f"({r.get('exit_reason', '?')})"
            )
        history_block = "Your recent decisions (learn from these):\n" + "\n".join(rows)
    else:
        history_block = "No prior decision history yet — this is your first trade."

    return f"""You are the execution advisor for Decifer, an algorithmic trading system. \
Before each trade is placed, you decide four things: the instrument, position size scaling, \
profit target price, and stop loss price. You receive feedback on your past decisions and \
are expected to improve over time.

## Current trade
Symbol: {symbol}
Direction: {direction}
Entry price: ${entry_price:.2f}
5-min ATR (bar noise / stop sizing): ${atr_5m:.4f}
Daily ATR (typical full-session range): ${atr_daily:.4f}
Conviction score: {conviction_score:.1f} / 10
Dimension breakdown: {dims}
Regime: {regime_context}
Signal rationale: {rationale}

## {history_block}

## Trade type: {trade_type}

## Your four decisions

1. **instrument** — "COMMON" (stock), "CALL" (long call option), or "PUT" (long put option).
   Recommend options only when: conviction_score ≥ 8, daily ATR implies ≥1% move potential, \
and it is before 12:00 ET. Otherwise use "COMMON".

2. **size_multiplier** — scale factor (0.25 to 2.0) applied to the formula-calculated base size.
   Defaults by trade type:
   - INTRADAY: 1.0 (standard). Scale down for borderline scores or choppy regimes.
   - SWING: 1.5 (catalyst-backed thesis earns larger size). Scale to 1.0 if catalyst is weak.
   - POSITION: 2.0 (strong fundamental thesis). Scale down only if regime is uncertain.

3. **profit_target** — an absolute price (not a percentage). Anchor by trade type:
   - INTRADAY: entry ± (daily_ATR × 0.4 to 0.6). Tighten near end-of-day or known resistance.
   - SWING: entry ± (daily_ATR × 1.5 to 3.0) or analyst price target if known. Minimum 3% move \
from entry. Exit target is catalyst resolution, not a fixed ATR multiple.
   - POSITION: entry ± (daily_ATR × 5.0 to 10.0) or analyst price target (15%+ upside). \
This is a thesis-driven hold — target the fundamental value, not a short-term ATR level.

4. **stop_loss** — an absolute price. Anchor by trade type:
   - INTRADAY: 5-min ATR × 1.0 minimum (prevents noise-outs).
   - SWING: daily ATR × 0.5 to 1.0 (give the thesis room to develop). Wider in choppy regimes.
   - POSITION: daily ATR × 1.0 to 2.0 (structural hold — stop should be below key support).

## Hard constraints (enforced by validation — your output will be overridden if violated)
- LONG: profit_target > entry_price AND stop_loss < entry_price
- SHORT: profit_target < entry_price AND stop_loss > entry_price
- (profit_target − entry) / (entry − stop_loss) ≥ 1.5  [minimum R:R for LONG]
- (entry − profit_target) / (stop_loss − entry) ≥ 1.5  [minimum R:R for SHORT]
- Neither level more than 15% from entry

Respond with ONLY a JSON object — no markdown, no explanation outside the JSON:
{{"instrument": "COMMON", "size_multiplier": 1.0, "profit_target": 0.0, "stop_loss": 0.0, "reasoning": "one or two sentences"}}"""


# ── Validation ────────────────────────────────────────────────────────────────


def _validate(
    raw: dict,
    direction: str,
    entry_price: float,
    atr_5m: float,
    fallback_sl: float,
    fallback_tp: float,
) -> dict:
    """
    Validate each Opus field independently.
    Invalid fields are replaced with formula fallbacks — no whole-advice rejection.
    Returns a dict of validated values.
    """
    min_rr = CONFIG.get("min_reward_risk_ratio", 1.5)
    max_dist_pct = 0.15

    # ── instrument ────────────────────────────────────────────
    instrument = raw.get("instrument", "COMMON")
    if instrument not in ("COMMON", "CALL", "PUT"):
        log.warning(f"advisor: invalid instrument '{instrument}' — defaulting to COMMON")
        instrument = "COMMON"

    # ── size_multiplier ───────────────────────────────────────
    try:
        size_mult = float(raw.get("size_multiplier", 1.0))
        if not (0.25 <= size_mult <= 2.0):
            log.warning(f"advisor: size_multiplier {size_mult} out of range — defaulting to 1.0")
            size_mult = 1.0
    except (TypeError, ValueError):
        size_mult = 1.0

    # ── profit_target ─────────────────────────────────────────
    try:
        pt = float(raw.get("profit_target", 0.0))
        pt_ok = (
            pt > 0
            and abs(pt - entry_price) / entry_price <= max_dist_pct
            and (pt > entry_price if direction == "LONG" else pt < entry_price)
        )
    except (TypeError, ValueError):
        pt_ok = False

    if not pt_ok:
        log.warning(f"advisor: profit_target {raw.get('profit_target')} invalid — using formula")
        pt = fallback_tp

    # ── stop_loss ─────────────────────────────────────────────
    try:
        sl = float(raw.get("stop_loss", 0.0))
        sl_ok = (
            sl > 0
            and abs(sl - entry_price) / entry_price <= max_dist_pct
            and (sl < entry_price if direction == "LONG" else sl > entry_price)
        )
    except (TypeError, ValueError):
        sl_ok = False

    if not sl_ok:
        log.warning(f"advisor: stop_loss {raw.get('stop_loss')} invalid — using formula")
        sl = fallback_sl

    # ── R:R floor ─────────────────────────────────────────────
    if direction == "LONG":
        reward = pt - entry_price
        risk = entry_price - sl
    else:
        reward = entry_price - pt
        risk = sl - entry_price

    if risk <= 0 or (reward / risk) < min_rr:
        log.warning(f"advisor: R:R {reward:.2f}/{risk:.2f} below floor {min_rr} — using formula PT")
        pt = fallback_tp

    return {
        "instrument": instrument,
        "size_multiplier": size_mult,
        "profit_target": round(pt, 2),
        "stop_loss": round(sl, 2),
        "reasoning": str(raw.get("reasoning", ""))[:300],
    }


# ── Formula fallback ──────────────────────────────────────────────────────────


def _formula_advice(
    symbol: str,
    direction: str,
    entry_price: float,
    atr_5m: float,
    advice_id: str = "",
) -> TradeAdvice:
    """ATR-formula fallback — preserves current behaviour exactly."""
    sl, tp = calculate_stops(entry_price, atr_5m, direction)
    return TradeAdvice(
        advice_id=advice_id or str(uuid.uuid4())[:8],
        instrument="COMMON",
        size_multiplier=1.0,
        profit_target=tp,
        stop_loss=sl,
        reasoning="formula fallback",
        source="formula",
    )


# ── Public API ────────────────────────────────────────────────────────────────


def advise_trade(
    symbol: str,
    direction: str,
    entry_price: float,
    atr_5m: float,
    atr_daily: float,
    conviction_score: float,
    dimension_scores: dict,
    rationale: str,
    regime_context: str,
    trade_type: str = "INTRADAY",
) -> TradeAdvice:
    """
    Ask Opus for PT, SL, size multiplier, and instrument recommendation.

    Logs the decision to data/advisor_log.json. Call record_outcome() when
    the trade closes to complete the learning loop.

    Always returns a valid TradeAdvice — falls back to ATR formula on any error.
    """
    advice_id = str(uuid.uuid4())[:8]

    # ── Compute formula fallbacks upfront ─────────────────────
    fallback_sl, fallback_tp = calculate_stops(entry_price, atr_5m, direction)

    if not CONFIG.get("use_llm_advisor", False):
        return _formula_advice(symbol, direction, entry_price, atr_5m, advice_id)

    try:
        log_data = _load_log()
        history = _recent_history(log_data, CONFIG.get("llm_advisor_history", 15))
        prompt = _build_prompt(
            symbol,
            direction,
            entry_price,
            atr_5m,
            atr_daily,
            conviction_score,
            dimension_scores,
            rationale,
            regime_context,
            history,
            trade_type=trade_type,
        )

        client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        message = client.messages.create(
            model=CONFIG.get("llm_advisor_model", "claude-opus-4-6"),
            max_tokens=CONFIG.get("llm_advisor_max_tokens", 512),
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()

        # Strip markdown code fences if Opus wraps the JSON
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        raw = json.loads(text)
        validated = _validate(raw, direction, entry_price, atr_5m, fallback_sl, fallback_tp)

        advice = TradeAdvice(
            advice_id=advice_id,
            instrument=validated["instrument"],
            size_multiplier=validated["size_multiplier"],
            profit_target=validated["profit_target"],
            stop_loss=validated["stop_loss"],
            reasoning=validated["reasoning"],
            source="opus",
        )

        # Log for learning loop
        log_data[advice_id] = {
            "advice_id": advice_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "trade_type": trade_type,
            "entry_price": entry_price,
            "atr_5m": atr_5m,
            "atr_daily": atr_daily,
            "conviction_score": conviction_score,
            "instrument": advice.instrument,
            "size_multiplier": advice.size_multiplier,
            "profit_target": advice.profit_target,
            "stop_loss": advice.stop_loss,
            "reasoning": advice.reasoning,
            "source": advice.source,
            # Outcome fields — filled by record_outcome() when trade closes
            "exit_price": None,
            "pnl": None,
            "exit_reason": None,
            "outcome_at": None,
        }
        _save_log(log_data)

        log.info(
            f"[advisor] {symbol} {direction} | instrument={advice.instrument} "
            f"size_mult={advice.size_multiplier} PT=${advice.profit_target:.2f} "
            f"SL=${advice.stop_loss:.2f} | {advice.reasoning[:80]}"
        )
        return advice

    except Exception as exc:
        log.warning(f"[advisor] Opus call failed for {symbol}: {exc} — formula fallback")
        return _formula_advice(symbol, direction, entry_price, atr_5m, advice_id)


def record_outcome(
    advice_id: str,
    exit_price: float,
    pnl: float,
    exit_reason: str,
) -> None:
    """
    Record the trade outcome against the Opus decision.
    Called from learning.log_trade() on CLOSE to close the feedback loop.
    """
    if not advice_id:
        return
    try:
        log_data = _load_log()
        if advice_id not in log_data:
            return
        log_data[advice_id].update(
            {
                "exit_price": exit_price,
                "pnl": pnl,
                "exit_reason": exit_reason,
                "outcome_at": datetime.now(UTC).isoformat(),
            }
        )
        _save_log(log_data)
    except Exception as exc:
        log.warning(f"[advisor] record_outcome failed for {advice_id}: {exc}")
