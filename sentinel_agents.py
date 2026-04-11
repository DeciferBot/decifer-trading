# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  sentinel_agents.py                   ║
# ║   Lightweight 3-agent pipeline for news-triggered trades    ║
# ║                                                              ║
# ║   Unlike the full 6-agent pipeline (5-10 min), this runs    ║
# ║   in ~15-30 seconds for immediate news-driven decisions.    ║
# ║                                                              ║
# ║   Agents:                                                    ║
# ║     1. Catalyst Analyst  -- LLM (news materiality)          ║
# ║     2. Risk Gate         -- deterministic (rule application) ║
# ║     3. Instant Decision  -- LLM (synthesis + JSON output)   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import anthropic
from config import CONFIG

log = logging.getLogger("decifer.sentinel_agents")
client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])


def _call_claude(system_prompt: str, user_message: str, max_tokens: int = 500) -> str:
    """Single Claude API call with configurable token limit."""
    try:
        resp = client.messages.create(
            model=CONFIG["claude_model"],
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"Sentinel Claude API error: {e}")
        return ""


# ══════════════════════════════════════════════════════════════
# AGENT 1 — CATALYST ANALYST  (LLM — news materiality)
# ══════════════════════════════════════════════════════════════
CATALYST_SYSTEM = """You are the Catalyst Analyst for Decifer's News Sentinel -- an interrupt-driven trading system.
You receive BREAKING NEWS headlines about a specific stock and must assess:
1. Is this a MATERIAL catalyst that should change the stock's trajectory?
2. What is the likely price impact (direction and magnitude)?
3. How durable is this catalyst (one-day event or multi-day trend)?

You are fast and decisive. No fluff. This is time-sensitive.
Output structured analysis only."""


def agent_catalyst(trigger: dict, current_position: dict = None) -> str:
    """
    Assess the news catalyst and its potential market impact.

    trigger: {symbol, headlines, keyword_score, direction, urgency,
              claude_sentiment, claude_confidence, claude_catalyst}
    current_position: {symbol, qty, entry, current, pnl, sl, tp} or None
    """
    sym = trigger["symbol"]
    headlines = "\n".join([f"- {h}" for h in trigger.get("headlines", [])])
    kw_score = trigger.get("keyword_score", 0)
    direction = trigger.get("direction", "UNKNOWN")
    urgency = trigger.get("urgency", "MODERATE")
    claude_sent = trigger.get("claude_sentiment", "NEUTRAL")
    claude_conf = trigger.get("claude_confidence", 0)
    catalyst = trigger.get("claude_catalyst", "")
    sources = ", ".join(trigger.get("sources", []))

    position_text = "NO CURRENT POSITION"
    if current_position:
        pos = current_position
        position_text = (
            f"HOLDING: {pos.get('qty', 0)} shares @ ${pos.get('entry', 0):.2f} | "
            f"Current: ${pos.get('current', 0):.2f} | "
            f"P&L: ${pos.get('pnl', 0):.2f} | "
            f"Direction: {pos.get('direction', 'LONG')} | "
            f"SL: ${pos.get('sl', 0):.2f} | TP: ${pos.get('tp', 0):.2f}"
        )

    prompt = f"""NEWS SENTINEL TRIGGER -- IMMEDIATE ASSESSMENT REQUIRED

SYMBOL: {sym}
URGENCY: {urgency}
NEWS SOURCES: {sources}

HEADLINES:
{headlines}

KEYWORD ANALYSIS:
- Keyword score: {kw_score:+d} (positive = bullish, negative = bearish)
- Direction: {direction}

CLAUDE PRE-READ:
- Sentiment: {claude_sent} | Confidence: {claude_conf}/10
- Catalyst summary: {catalyst}

CURRENT POSITION STATUS:
{position_text}

Assess this catalyst:
1. MATERIALITY: Is this news material enough to move the stock? (YES/NO + why)
2. DIRECTION: BULLISH / BEARISH / NEUTRAL -- clear directional call
3. MAGNITUDE: Expected price impact (small <2%, medium 2-5%, large >5%)
4. DURABILITY: Flash reaction (1 day) / Trend shift (multi-day) / Structural change
5. SPEED: How fast will the market price this in? (already pricing / 30 min / hours / days)
6. RECOMMENDATION: What should we do RIGHT NOW?
   - If we HOLD the stock: HOLD / EXIT / ADD / TIGHTEN STOPS
   - If we DON'T hold: BUY / SHORT (via inverse ETF) / WATCH / IGNORE"""

    return _call_claude(CATALYST_SYSTEM, prompt, max_tokens=500)


# ══════════════════════════════════════════════════════════════
# AGENT 2 — RISK GATE  (deterministic)
# Replaces LLM call: all 5 checks are directly computable from
# CONFIG + risk.py. The original prompt listed explicit boolean
# questions with no reasoning required.
# ══════════════════════════════════════════════════════════════

def agent_risk_gate(catalyst_report: str, trigger: dict,
                    open_positions: list, portfolio_value: float,
                    daily_pnl: float, regime: dict) -> str:
    """
    Deterministic risk gate for news-triggered trades.
    Uses risk.py functions to answer the same 5 questions the LLM was asked.
    Returns human-readable text compatible with agent_instant_decision input.
    """
    from risk import check_risk_conditions, calculate_position_size, calculate_stops

    sym = trigger["symbol"]
    direction = trigger.get("direction", "UNKNOWN")

    max_pos = CONFIG["max_positions"]
    risk_pct = CONFIG["risk_pct_per_trade"]
    daily_limit = CONFIG["daily_loss_limit"]
    # News-triggered trades use a tighter risk multiplier (0.75x normal)
    sentinel_mult = CONFIG.get("sentinel_risk_multiplier", 0.75)

    open_syms = [p.get("symbol") for p in open_positions]
    existing_pos = next((p for p in open_positions if p.get("symbol") == sym), None)
    slots_remaining = max_pos - len(open_positions)
    daily_budget_left = (portfolio_value * daily_limit) + daily_pnl

    positions_text = "\n".join([
        f"  {p.get('symbol', '?')}: {p.get('qty', 0)} shares | "
        f"Entry ${p.get('entry', 0):.2f} | P&L ${p.get('pnl', 0):.2f}"
        for p in open_positions[:10]
    ]) if open_positions else "  No open positions"

    lines = [
        f"SENTINEL RISK GATE -- {sym}",
        f"Portfolio: ${portfolio_value:,.2f} | Daily P&L: ${daily_pnl:+,.2f}",
        f"Positions: {len(open_positions)}/{max_pos} ({slots_remaining} slots remaining)",
        "",
        "RISK CHECKS:",
    ]

    # Q1: Position slots available?
    q1 = slots_remaining > 0 or existing_pos is not None
    lines.append(f"  1. Position slots available: {'YES' if q1 else 'NO'} ({slots_remaining} remaining)")

    # Q2: Daily loss budget intact?
    q2 = daily_budget_left > 0
    lines.append(f"  2. Daily loss budget intact: {'YES' if q2 else 'NO'} (${daily_budget_left:,.2f} remaining)")

    # Q3: Regime alignment?
    reg = regime.get("regime", "UNKNOWN")
    dir_upper = direction.upper()
    q3 = True
    if reg == "CAPITULATION":
        q3 = False
    elif reg in ("TRENDING_DOWN", "RELIEF_RALLY") and dir_upper == "BULLISH":
        q3 = False
    lines.append(f"  3. Regime aligned with trade direction: {'YES' if q3 else 'NO'} ({reg} / {direction})")

    # Q4: Urgency justifies interrupt trade?
    urgency = trigger.get("urgency", "MODERATE").upper()
    q4 = urgency in ("HIGH", "CRITICAL", "EXTREME")
    lines.append(f"  4. Urgency justifies sentinel interrupt: {'YES' if q4 else 'NO'} ({urgency})")

    # Q5: Master risk gate via check_risk_conditions
    gate_ok, gate_reason = check_risk_conditions(portfolio_value, daily_pnl, regime, open_positions)
    q5 = gate_ok
    lines.append(f"  5. Master risk gate: {'OPEN' if q5 else 'CLOSED'} -- {gate_reason}")

    lines.append("")

    # ── Final decision ──────────────────────────────────────────
    if not all([q1, q2, q3, q5]):
        failed = []
        if not q1:
            failed.append("no position slots")
        if not q2:
            failed.append("daily loss limit reached")
        if not q3:
            failed.append(f"regime {reg} conflicts with {direction}")
        if not q5:
            failed.append(gate_reason)
        decision = "BLOCK"
        reason = "; ".join(failed)
        lines.append(f"DECISION: BLOCK")
        lines.append(f"REASON: {reason}")
        return "\n".join(lines)

    # Size the trade (tighter than normal -- news trades are volatile)
    # Use a placeholder price for sizing; actual sizing in Instant Decision
    price = trigger.get("price", 0)
    atr = trigger.get("atr", price * 0.015 if price > 0 else 1.0)
    trade_direction = "LONG" if dir_upper in ("BULLISH", "LONG", "BUY") else "SHORT"

    if price > 0 and portfolio_value > 0:
        qty = calculate_position_size(portfolio_value, price,
                                       trigger.get("score", 20), regime,
                                       atr=atr, external_mult=sentinel_mult)
        sl, tp = calculate_stops(price, atr, trade_direction)
        # Tighten stop loss by 25% for news-driven trades
        if trade_direction == "LONG":
            sl = round(price - (price - sl) * 0.75, 2)
        else:
            sl = round(price + (sl - price) * 0.75, 2)
    else:
        qty = 1
        sl = 0.0
        tp = 0.0

    lines.append("DECISION: APPROVE")
    lines.append(f"SIZE: {qty} shares")
    lines.append(f"STOP LOSS: ${sl:.2f}  (tight -- news-driven trade)")
    lines.append(f"TAKE PROFIT: ${tp:.2f}")
    lines.append(f"MAX RISK: ${portfolio_value * risk_pct * sentinel_mult:,.2f}")
    lines.append(f"REASON: All risk checks passed; sentinel multiplier={sentinel_mult}x applied")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# AGENT 3 — INSTANT DECISION MAKER  (LLM — synthesis + JSON)
# ══════════════════════════════════════════════════════════════
INSTANT_DECISION_SYSTEM = """You are the Instant Decision Maker for Decifer's News Sentinel.
You synthesise the Catalyst Analyst and Risk Gate reports into an executable trade instruction.
Output ONLY valid JSON. No markdown, no explanation outside the JSON.
This trade will execute IMMEDIATELY with real money. Be precise."""


def agent_instant_decision(catalyst_report: str, risk_report: str,
                            trigger: dict, current_position: dict = None) -> dict:
    """
    Final synthesis -- outputs actionable JSON for immediate execution.
    """
    sym = trigger["symbol"]
    is_holding = current_position is not None

    prompt = f"""CATALYST ANALYST:
{catalyst_report}

RISK GATE:
{risk_report}

SYMBOL: {sym}
CURRENTLY HOLDING: {'YES' if is_holding else 'NO'}
NEWS DIRECTION: {trigger.get('direction', 'UNKNOWN')}
URGENCY: {trigger.get('urgency', 'MODERATE')}

Synthesise both reports into a single executable instruction.

Output ONLY valid JSON:
{{
  "action": "BUY" or "SELL" or "HOLD" or "SKIP",
  "symbol": "{sym}",
  "qty": 0,
  "sl": 0.00,
  "tp": 0.00,
  "instrument": "stock" or "option" or "inverse_etf",
  "inverse_symbol": "SPXS or SQQQ if shorting via inverse ETF, else null",
  "urgency": "CRITICAL" or "HIGH" or "MODERATE",
  "confidence": 0-10,
  "reasoning": "One sentence explaining the decision",
  "catalyst": "{trigger.get('claude_catalyst', trigger.get('headlines', [''])[0][:60])}",
  "trigger_type": "news_sentinel"
}}

Rules:
- SELL only if we currently hold the stock
- BUY only if Risk Gate said APPROVE
- If Risk Gate said BLOCK, output action="SKIP"
- For SHORT signals, use inverse_symbol (SPXS for broad market, SQQQ for tech)
- Set tight stops -- news-driven trades are volatile
- confidence must honestly reflect your conviction (0-10)"""

    raw = _call_claude(INSTANT_DECISION_SYSTEM, prompt, max_tokens=350)

    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        result.setdefault("action", "SKIP")
        result.setdefault("symbol", sym)
        result.setdefault("qty", 0)
        result.setdefault("sl", 0)
        result.setdefault("tp", 0)
        result.setdefault("instrument", "stock")
        result.setdefault("confidence", 0)
        result.setdefault("reasoning", "")
        result.setdefault("trigger_type", "news_sentinel")
        return result
    except json.JSONDecodeError:
        log.error(f"Sentinel agent JSON parse error. Raw: {raw[:200]}")
        return {
            "action": "SKIP",
            "symbol": sym,
            "qty": 0,
            "sl": 0,
            "tp": 0,
            "instrument": "stock",
            "confidence": 0,
            "reasoning": "JSON parse error -- skipping for safety",
            "trigger_type": "news_sentinel",
        }


# ══════════════════════════════════════════════════════════════
# ORCHESTRATOR — Run the 3-agent sentinel pipeline
# ══════════════════════════════════════════════════════════════
def run_sentinel_pipeline(trigger: dict,
                           open_positions: list,
                           portfolio_value: float,
                           daily_pnl: float,
                           regime: dict) -> dict:
    """
    Run the lightweight 3-agent sentinel pipeline.
    Agent 1 (Catalyst) and Agent 3 (Instant Decision) remain LLM.
    Agent 2 (Risk Gate) is now deterministic -- saves ~400 tokens/trigger.

    trigger: news trigger from news_sentinel.py
    Returns: trade decision dict with action, qty, sl, tp, reasoning
    """
    sym = trigger["symbol"]
    log.info(f"Sentinel pipeline started for {sym} | urgency={trigger.get('urgency')}")

    current_pos = next((p for p in open_positions if p.get("symbol") == sym), None)

    # Agent 1: Catalyst Analyst (LLM)
    log.info(f"  Agent 1: Catalyst Analyst ({sym})...")
    catalyst = agent_catalyst(trigger, current_pos)

    # Agent 2: Risk Gate (deterministic)
    log.info(f"  Agent 2: Risk Gate ({sym}) [deterministic]...")
    risk = agent_risk_gate(catalyst, trigger, open_positions,
                           portfolio_value, daily_pnl, regime)

    # Agent 3: Instant Decision (LLM)
    log.info(f"  Agent 3: Instant Decision ({sym})...")
    decision = agent_instant_decision(catalyst, risk, trigger, current_pos)

    decision["_sentinel_outputs"] = {
        "catalyst": catalyst,
        "risk_gate": risk,
    }
    decision["_trigger"] = trigger

    action = decision.get("action", "SKIP")
    confidence = decision.get("confidence", 0)
    reasoning = decision.get("reasoning", "")

    log.info(
        f"Sentinel decision for {sym}: {action} | "
        f"confidence={confidence}/10 | {reasoning[:80]}"
    )

    return decision
