# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  agents.py                                  ║
# ║   The 6-agent multi-perspective trading intelligence system  ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import anthropic
from concurrent.futures import ThreadPoolExecutor
from config import CONFIG

log = logging.getLogger("decifer.agents")
client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])


def _call_claude(system_prompt: str, user_message: str) -> str:
    """Single Claude API call. Returns text response."""
    try:
        resp = client.messages.create(
            model=CONFIG["claude_model"],
            max_tokens=CONFIG["claude_max_tokens"],
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return ""


# ══════════════════════════════════════════════════════════════
# AGENT 1 — TECHNICAL ANALYST
# ══════════════════════════════════════════════════════════════
TECHNICAL_SYSTEM = """You are the Technical Analyst for Decifer, an autonomous trading system.
Your ONLY job is to analyse price action, volume, and technical indicators.
You have NO knowledge of news, macro events, or fundamentals — only charts.
Be direct, specific, and concise. No fluff.
Output structured analysis only."""

def agent_technical(signals: list, regime: dict) -> str:
    """Analyse technical picture across all scored symbols."""
    if not signals:
        return "No symbols above scoring threshold. No technical setups."

    signal_text = "\n".join([
        f"{s['symbol']}: ${s['price']} | Score={s['score']}/50 | Signal={s['signal']} | "
        f"MFI={s['timeframes']['5m']['mfi']} | RSI_slope={s['timeframes']['5m']['rsi_slope']} | "
        f"MACD_accel={s['timeframes']['5m']['macd_accel']} | Vol={s['vol_ratio']}x | ATR={s['atr']} | "
        f"EMA={'BULL' if s['timeframes']['5m']['bull_aligned'] else 'BEAR' if s['timeframes']['5m']['bear_aligned'] else 'MIXED'} | "
        f"ADX={s['timeframes']['5m']['adx']}({s['timeframes']['5m']['trend_strength']}) | "
        f"Squeeze={'ON(' + str(s['timeframes']['5m']['squeeze_intensity']) + ')' if s['timeframes']['5m']['squeeze_on'] else 'off'} | "
        f"VWAP_dist={s['timeframes']['5m']['vwap_dist']}% | "
        f"Donchian={'BREAKOUT_HIGH' if s['timeframes']['5m']['donch_breakout']==1 else 'BREAKOUT_LOW' if s['timeframes']['5m']['donch_breakout']==-1 else 'inside'} | "
        f"OBV={'UP' if s['timeframes']['5m']['obv_slope']>0 else 'DOWN' if s['timeframes']['5m']['obv_slope']<0 else 'FLAT'} | "
        f"News={s.get('news', {}).get('claude_sentiment', 'N/A')}(kw={s.get('news', {}).get('keyword_score', 0)}) | "
        f"TF={s['timeframes']['5m']['signal']}/{s['timeframes']['1d']['signal'] if s['timeframes']['1d'] else 'N/A'}/{s['timeframes']['1w']['signal'] if s['timeframes']['1w'] else 'N/A'}"
        for s in signals[:15]
    ])

    prompt = f"""Current scanned universe (symbols scoring above threshold):

{signal_text}

DECIFER 2.0 — 6-DIMENSION INDICATOR SYSTEM:

1. TREND: EMA alignment (9/21/50) + ADX strength
   - ADX > 25 = strong trend (trust it) | ADX < 20 = noise (reduce confidence)
   - BULL aligned = EMA9 > EMA21 > EMA50 | BEAR aligned = opposite

2. MOMENTUM: MFI (Money Flow Index — volume-weighted RSI, single source of truth)
   - MFI > 65 + rising RSI = strong institutional buying
   - MFI < 35 + falling RSI = strong institutional selling
   - MFI divergence from price = CRITICAL WARNING (smart money exiting)

3. SQUEEZE: Bollinger Bands inside Keltner Channels = volatility compression
   - Squeeze ON = price coiling like a spring, explosive move incoming
   - Squeeze intensity 0.5+ = tight compression, high-conviction setup
   - Squeeze + Donchian breakout = highest-probability trade type

4. FLOW: VWAP distance + OBV slope = institutional positioning
   - VWAP_dist > 0 = price above VWAP = institutions supporting the move
   - VWAP_dist < 0 = price below VWAP = institutions offloading
   - OBV UP + VWAP above = confirmed institutional accumulation
   - OBV DOWN + price rising = DISTRIBUTION — smart money selling to retail

5. BREAKOUT: Donchian Channel (20-period high/low) breach + volume
   - BREAKOUT_HIGH + Vol > 1.5x = genuine breakout, not a fakeout
   - BREAKOUT_LOW + Vol > 1.5x = breakdown confirmed
   - Inside channel = no breakout, wait for catalyst

6. MULTI-TIMEFRAME: 5m / 1D / 1W agreement
   - All 3 aligned = highest conviction | 2 of 3 = decent | 1 = risky

7. NEWS: Yahoo RSS sentiment (keyword + Claude analysis)
   - BULLISH + keyword_score > 3 = strong positive catalyst in the news
   - BEARISH + keyword_score < -3 = negative catalyst, be cautious
   - News contradicting trade direction = major red flag

VIX: {regime['vix']} | SPY: ${regime['spy_price']} (above EMA: {regime['spy_above_ema']})

Analyse the technical picture using ALL 6 dimensions:
1. Is ADX confirming a real trend or is this noise?
2. Is MFI confirming the move with institutional money flow?
3. Is there a squeeze setup? If so, which direction is it likely to resolve?
4. What does VWAP + OBV say about institutional positioning?
5. Any Donchian breakouts with volume confirmation?
6. Do the timeframes agree?

For each setup rate: HIGH (4+ dimensions aligned) / MEDIUM (2-3 aligned) / LOW (1 or conflicting)
Flag any DIVERGENCES — e.g. price rising but MFI falling + OBV declining = distribution trap."""

    return _call_claude(TECHNICAL_SYSTEM, prompt)


# ══════════════════════════════════════════════════════════════
# AGENT 2 — MACRO ANALYST
# ══════════════════════════════════════════════════════════════
MACRO_SYSTEM = """You are the Macro Analyst for Decifer, an autonomous trading system.
Your ONLY job is to assess the macro environment — market regime, cross-asset dynamics,
FX moves, geopolitical context, and news flow.
You do NOT look at individual stock charts. You look at the big picture.
Output structured analysis only."""

def agent_macro(regime: dict, news_headlines: list, fx_data: dict) -> str:
    """Assess macro environment and risk-on/risk-off positioning."""

    headlines_text = "\n".join([f"- {h}" for h in news_headlines[:15]]) if news_headlines else "No headlines available"

    fx_text = "\n".join([
        f"{pair}: {data.get('price', 'N/A')} (change: {data.get('change_pct', 'N/A')}%)"
        for pair, data in fx_data.items()
    ]) if fx_data else "FX data unavailable"

    prompt = f"""Current market regime data:

REGIME CLASSIFICATION: {regime['regime']}
VIX: {regime['vix']} ({regime['vix_1h_change']:+.1f}% in last hour)
SPY: ${regime['spy_price']} (above 20-EMA: {regime['spy_above_ema']})
QQQ: ${regime['qqq_price']} (above 20-EMA: {regime['qqq_above_ema']})

FX MARKETS:
{fx_text}

RECENT NEWS HEADLINES:
{headlines_text}

Assess:
1. Is the regime classification correct? Any nuance to add?
2. Is this risk-ON or risk-OFF environment right now?
3. Which sectors/asset classes benefit from current macro?
4. Any geopolitical or macro risks that should limit position sizes?
5. Cross-asset signals: what are bonds, gold, oil, and FX saying?
6. Overall verdict: BULLISH / BEARISH / NEUTRAL / UNCERTAIN"""

    return _call_claude(MACRO_SYSTEM, prompt)


# ══════════════════════════════════════════════════════════════
# AGENT 3 — OPPORTUNITY FINDER
# ══════════════════════════════════════════════════════════════
OPPORTUNITY_SYSTEM = """You are the Opportunity Finder for Decifer, an autonomous trading system.
Your job is to synthesise technical and macro analysis to identify the 3 best trading
opportunities available RIGHT NOW across ANY asset class IBKR supports.
You have NO bias toward stocks, FX, options, commodities, or any other instrument.
You go where the opportunity is. Be decisive and specific.
For any unfamiliar symbol, reason about it from first principles using the data provided.
Do not dismiss a symbol just because it is unfamiliar — analyse the data."""

def agent_opportunity(technical_report: str, macro_report: str,
                      signals: list, options_signals: list = None) -> str:
    """Identify top 3 opportunities by synthesising technical, macro, and options flow."""

    available = ", ".join([s["symbol"] for s in signals]) if signals else "None above threshold"

    # Format options flow data
    if options_signals:
        opts_lines = []
        for o in options_signals[:10]:
            ivr_str = f"IVR={o['iv_rank']:.0f}%" if o.get("iv_rank") is not None else "IVR=n/a"
            earn_str = f" | earnings in {o['earnings_days']}d" if o.get("earnings_days") else ""
            opts_lines.append(
                f"  [{o['options_score']:>2}/30] {o['signal']:<14} {o['symbol']:<6} "
                f"${o['price']:.2f} | C/P={o['cp_ratio']:.1f}x | {ivr_str} | "
                f"{o['dte']}DTE {o['expiry']}{earn_str}"
            )
        options_section = "OPTIONS FLOW DATA (yfinance live scanner):\n" + "\n".join(opts_lines)
        options_note = (
            "\n\nOPTIONS FLOW INSTRUCTIONS:\n"
            "- When a stock symbol appears in BOTH the stock signals AND the options flow, "
            "strongly consider recommending the OPTION as the instrument (call for LONG, put for SHORT).\n"
            "- CALL_BUYER signal = smart money buying calls = bullish.\n"
            "- PUT_BUYER signal = smart money buying puts = bearish.\n"
            "- EARNINGS_PLAY = catalyst upcoming — options are often the better instrument.\n"
            "- Low IVR (<30%) = options cheap = good risk/reward to buy premium.\n"
            "- High options score (20+/30) = strong conviction signal from options market."
        )
    else:
        options_section = "OPTIONS FLOW DATA: Not available this cycle."
        options_note = ""

    prompt = f"""TECHNICAL ANALYST REPORT:
{technical_report}

MACRO ANALYST REPORT:
{macro_report}

SYMBOLS CURRENTLY SCORING ABOVE THRESHOLD: {available}

{options_section}{options_note}

Based on all reports, identify the TOP 3 trading opportunities right now.
For each opportunity provide:
1. SYMBOL and ASSET CLASS
2. DIRECTION: LONG or SHORT
3. CONVICTION: HIGH / MEDIUM / LOW
4. ENTRY RATIONALE: Why this, why now? (reference options flow if relevant)
5. KEY RISK: What could make this wrong?
6. SUGGESTED INSTRUMENT: Stock / Call option / Put option / Inverse ETF / FX pair
   — If options flow data supports the trade and IVR is low, PREFER options over stock.

If fewer than 3 genuine opportunities exist, say so clearly. Do not force trades.
Quality over quantity. A good reason to stay in cash is a valid output."""

    return _call_claude(OPPORTUNITY_SYSTEM, prompt)


# ══════════════════════════════════════════════════════════════
# AGENT 4 — DEVIL'S ADVOCATE
# ══════════════════════════════════════════════════════════════
DEVILS_SYSTEM = """You are the Devil's Advocate for Decifer, an autonomous trading system.
Your ONLY job is to find reasons NOT to take each proposed trade.
You are adversarial by design. You protect capital by being skeptical.
For every proposed opportunity, argue against it as strongly as you can.
Flag anything that could cause a loss. Be ruthless but fair.
If a trade is genuinely strong, you may acknowledge it — but still find the weaknesses."""

def agent_devils_advocate(opportunity_report: str, regime: dict) -> str:
    """Challenge every proposed opportunity."""

    prompt = f"""PROPOSED OPPORTUNITIES:
{opportunity_report}

CURRENT REGIME: {regime['regime']} | VIX: {regime['vix']}

For each proposed trade, provide a devil's advocate counter-argument:
1. What technical or macro evidence argues AGAINST this trade?
2. What recent news or events could invalidate this thesis?
3. Is there a crowded trade risk? (everyone already positioned this way)
4. Are there upcoming events (earnings, Fed, economic data) that create binary risk?
5. VETO RATING: STRONG VETO / MODERATE CONCERN / MINOR CONCERN / NO VETO

A STRONG VETO means: do not take this trade under any circumstances.
Be specific. Generic concerns are not useful."""

    return _call_claude(DEVILS_SYSTEM, prompt)


# ══════════════════════════════════════════════════════════════
# AGENT 5 — RISK MANAGER
# ══════════════════════════════════════════════════════════════
RISK_SYSTEM = """You are the Risk Manager for Decifer, an autonomous trading system.
You are an ADVISORY agent — you provide risk analysis and sizing recommendations but do NOT have veto power.
Your job is to flag risks and suggest position size adjustments, not to block trades.
You assess every proposed trade against portfolio risk, sizing constraints, and risk rules.
Output: APPROVE / REDUCE SIZE / FLAG RISK for each trade, with specific sizing.
Note: Your opinion counts as 1 vote of 6 in the consensus — same as every other agent."""

def agent_risk_manager(opportunity_report: str, devils_report: str,
                       open_positions: list, portfolio_value: float,
                       daily_pnl: float, regime: dict) -> str:
    """Assess portfolio risk and approve/reject each trade with sizing."""

    positions_text = "\n".join([
        f"  {p['symbol']}: {p['qty']} shares | Entry ${p['entry']} | Current ${p['current']} | "
        f"P&L ${p['pnl']:.2f} | SL ${p['sl']} | TP ${p['tp']}"
        for p in open_positions
    ]) if open_positions else "  No open positions"

    risk_pct      = CONFIG["risk_pct_per_trade"]
    max_pos       = CONFIG["max_positions"]
    daily_limit   = CONFIG["daily_loss_limit"]
    cash_reserve  = CONFIG["min_cash_reserve"]

    daily_loss_remaining = (portfolio_value * daily_limit) + daily_pnl
    positions_remaining  = max_pos - len(open_positions)

    prompt = f"""PROPOSED OPPORTUNITIES:
{opportunity_report}

DEVIL'S ADVOCATE CONCERNS:
{devils_report}

CURRENT PORTFOLIO:
Portfolio value: ${portfolio_value:,.2f}
Daily P&L: ${daily_pnl:,.2f}
Daily loss budget remaining: ${daily_loss_remaining:,.2f}
Open positions ({len(open_positions)}/{max_pos}):
{positions_text}

RISK CONSTRAINTS:
- Max risk per trade: {risk_pct*100:.0f}% (${portfolio_value * risk_pct:,.2f})
- Position slots available: {positions_remaining}
- Min cash reserve: {cash_reserve*100:.0f}% (${portfolio_value * cash_reserve:,.2f})
- Regime size multiplier: {regime['position_size_multiplier']}x
- Current regime: {regime['regime']}

For each proposed trade, output:
DECISION: APPROVE / REDUCE SIZE / REJECT
SIZE: Exact number of shares or contracts
STOP LOSS: $ price
TAKE PROFIT: $ price (first partial exit)
REASON: One sentence justification

If daily loss limit is near or position slots are full, say so explicitly."""

    return _call_claude(RISK_SYSTEM, prompt)


# ══════════════════════════════════════════════════════════════
# AGENT 6 — FINAL DECISION MAKER
# ══════════════════════════════════════════════════════════════
FINAL_SYSTEM = """You are the Final Decision Maker for Decifer, an autonomous trading system.
You receive reports from 5 specialist agents and synthesise them into executable trade instructions.
You output ONLY valid JSON. No markdown, no explanation outside the JSON.
Every decision you make will be executed automatically with real money.
Be precise, be decisive, and protect capital."""

def agent_final_decision(technical: str, macro: str, opportunity: str,
                         devils: str, risk: str, signals: list,
                         open_positions: list, regime: dict,
                         agents_required: int,
                         weekly_memory: str = "") -> dict:
    """
    Final synthesis — outputs actionable JSON trade instructions.
    Enforces the disagreement protocol.
    """

    open_syms = [p["symbol"] for p in open_positions]

    prompt = f"""You have received reports from 5 specialist agents. Synthesise into trade instructions.

DISAGREEMENT PROTOCOL: A trade requires at least {agents_required} of 6 agents to support it.
(Technical + Macro + Opportunity = 3 analysts. Devil's Advocate strong concern = -1 agent support.
Risk Manager is advisory only — counts as 1 vote like every other agent. No single agent has veto power.)

═══ AGENT REPORTS ═══

[TECHNICAL ANALYST]
{technical}

[MACRO ANALYST]
{macro}

[OPPORTUNITY FINDER]
{opportunity}

[DEVIL'S ADVOCATE]
{devils}

[RISK MANAGER]
{risk}

═══ LEARNING MEMORY (from last weekly review) ═══
{weekly_memory if weekly_memory else "No weekly review yet — first week of trading."}

═══ CURRENT STATE ═══
Regime: {regime['regime']} | VIX: {regime['vix']}
Open positions: {open_syms if open_syms else 'None'}

═══ OUTPUT FORMAT ═══
Respond ONLY with valid JSON:
{{
  "buys": [
    {{"symbol": "AAPL", "qty": 10, "sl": 150.00, "tp": 165.00, "instrument": "stock", "reasoning": "one line"}}
  ],
  "sells": ["MSFT"],
  "hold": ["NVDA"],
  "cash": false,
  "agents_agreed": 4,
  "summary": "One sentence market assessment",
  "claude_reasoning": "2-3 sentences explaining the key decision logic"
}}

Rules:
- Only include buys where Risk Manager said APPROVE
- Only sell symbols currently in open positions
- Set cash=true if regime is PANIC or daily loss limit is hit
- agents_agreed must reflect honest count
- Max 3 new buys per scan"""

    raw = _call_claude(FINAL_SYSTEM, prompt)

    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        return result
    except json.JSONDecodeError:
        log.error(f"Final agent JSON parse error. Raw: {raw[:200]}")
        return {
            "buys": [], "sells": [], "hold": [], "cash": False,
            "agents_agreed": 0,
            "summary": "Parse error — no trades this cycle",
            "claude_reasoning": "JSON parsing failed on final agent output."
        }


# ══════════════════════════════════════════════════════════════
# ORCHESTRATOR — Run all 6 agents in sequence
# ══════════════════════════════════════════════════════════════
def load_weekly_review() -> str:
    """Load most recent weekly review to inject into agents as memory."""
    review_file = "weekly_review.txt"
    import os
    if os.path.exists(review_file):
        try:
            with open(review_file) as f:
                return f.read()[-2000:]  # Last 2000 chars
        except Exception:
            pass
    return ""


def run_all_agents(signals: list, regime: dict, news: list,
                   fx_data: dict, open_positions: list,
                   portfolio_value: float, daily_pnl: float,
                   options_signals: list = None) -> dict:
    """
    Run all 6 agents sequentially and return final decision.
    Each agent's output feeds into the next.
    options_signals: live options flow data from options_scanner.py
    """
    # Inject learning memory from last weekly review
    weekly_memory = load_weekly_review()

    log.info("Agents 1+2: Technical + Macro (parallel)...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        tech_future  = pool.submit(agent_technical, signals, regime)
        macro_future = pool.submit(agent_macro, regime, news, fx_data)
        tech  = tech_future.result()
        macro = macro_future.result()

    log.info("Agent 3: Opportunity Finder (with options flow)...")
    opp    = agent_opportunity(tech, macro, signals, options_signals=options_signals or [])

    log.info("Agent 4: Devil's Advocate...")
    devils = agent_devils_advocate(opp, regime)

    log.info("Agent 5: Risk Manager...")
    risk   = agent_risk_manager(opp, devils, open_positions,
                                portfolio_value, daily_pnl, regime)

    log.info("Agent 6: Final Decision Maker...")
    final  = agent_final_decision(tech, macro, opp, devils, risk,
                                  signals, open_positions, regime,
                                  CONFIG["agents_required_to_agree"],
                                  weekly_memory)

    # Attach full agent outputs for logging
    final["_agent_outputs"] = {
        "technical":   tech,
        "macro":       macro,
        "opportunity": opp,
        "devils":      devils,
        "risk":        risk,
    }

    return final
