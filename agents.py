# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  agents.py                                  ║
# ║   The 6-agent multi-perspective trading intelligence system  ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import re
import anthropic
from concurrent.futures import ThreadPoolExecutor
from config import CONFIG

log = logging.getLogger("decifer.agents")
client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])


def _call_claude(system_prompt: str, user_message: str) -> str:
    """Single Claude API call with prompt caching on system prompt (~90% cost reduction)."""
    try:
        resp = client.messages.create(
            model=CONFIG["claude_model"],
            max_tokens=CONFIG["claude_max_tokens"],
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return ""


# ══════════════════════════════════════════════════════════════
# AGENT 1 — TECHNICAL ANALYST  (deterministic)
# Replaces LLM call: signals already carry all indicator data;
# conviction thresholds and divergence rules are fully known.
# ══════════════════════════════════════════════════════════════

def agent_technical(signals: list, regime: dict) -> str:
    """Deterministic technical analysis report built from pre-computed signal data."""
    if not signals:
        return "No symbols above scoring threshold. No technical setups."

    lines = [
        f"TECHNICAL ANALYSIS — {min(len(signals), 15)} symbols above threshold",
        (
            f"Market: VIX={regime['vix']:.1f} | SPY=${regime['spy_price']} "
            f"({'above' if regime['spy_above_ema'] else 'below'} 20-EMA)"
        ),
        "",
    ]

    high_syms, medium_syms, low_syms, divergences = [], [], [], []

    for s in signals[:15]:
        sym = s["symbol"]
        score = s["score"]
        sig = s.get("signal", "NEUTRAL")
        tf5 = s.get("timeframes", {}).get("5m") or {}
        tf1d = s.get("timeframes", {}).get("1d") or {}
        tf1w = s.get("timeframes", {}).get("1w") or {}

        mfi = tf5.get("mfi", 50)
        obv_slope = tf5.get("obv_slope", 0)
        vol_ratio = s.get("vol_ratio", 1.0)
        squeeze_on = tf5.get("squeeze_on", False)
        squeeze_int = tf5.get("squeeze_intensity", 0)
        donch = tf5.get("donch_breakout", 0)
        adx = tf5.get("adx", 0)
        vwap_dist = tf5.get("vwap_dist", 0)
        bull_al = tf5.get("bull_aligned", False)
        bear_al = tf5.get("bear_aligned", False)

        aligned_dims = sum(1 for v in s.get("score_breakdown", {}).values() if v > 0)

        if score >= 30:
            high_syms.append(sym)
            conviction = "HIGH"
        elif score >= 20:
            medium_syms.append(sym)
            conviction = "MEDIUM"
        else:
            low_syms.append(sym)
            conviction = "LOW"

        ema_str = "BULL" if bull_al else ("BEAR" if bear_al else "MIXED")
        sq_str = f"Squeeze=ON({squeeze_int:.1f})" if squeeze_on else "Squeeze=off"
        donch_str = (
            "Donchian=BREAKOUT_HIGH" if donch == 1
            else "Donchian=BREAKOUT_LOW" if donch == -1
            else "Donchian=inside"
        )
        tf_agree = "/".join([
            tf5.get("signal", "?"),
            tf1d.get("signal", "N/A") if tf1d else "N/A",
            tf1w.get("signal", "N/A") if tf1w else "N/A",
        ])

        lines.append(
            f"[{conviction}] {sym}: ${s['price']} | Score={score}/50 | {sig} | "
            f"ADX={adx:.0f} | MFI={mfi:.0f} | EMA={ema_str} | {sq_str} | "
            f"Vol={vol_ratio:.1f}x | VWAP_dist={vwap_dist:+.2f}% | {donch_str} | "
            f"OBV={'UP' if obv_slope > 0 else 'DOWN' if obv_slope < 0 else 'FLAT'} | "
            f"TF={tf_agree} | Dims={aligned_dims} aligned"
        )

        # News overlay
        news = s.get("news") or {}
        kw = news.get("keyword_score", 0)
        sent = news.get("claude_sentiment", "")
        cat = news.get("claude_catalyst", "")
        if sent and kw != 0:
            lines.append(
                f"  -> News: {sent} (kw={kw:+d})"
                + (f" | {cat[:80]}" if cat else "")
            )

        # Divergence flags (rules stated explicitly in original prompt)
        if sig == "BUY" and obv_slope < 0 and mfi < 40:
            divergences.append(
                f"  ! {sym}: DISTRIBUTION TRAP -- BUY signal but OBV falling + MFI={mfi:.0f}")
        elif sig == "BUY" and mfi < 35:
            divergences.append(
                f"  ! {sym}: MFI DIVERGENCE -- BUY signal but MFI={mfi:.0f} (weak institutional flow)")
        elif donch == 1 and vol_ratio < 1.5:
            divergences.append(
                f"  ! {sym}: LOW-VOLUME BREAKOUT -- Donchian breach but Vol={vol_ratio:.1f}x (suspect fakeout)")

    lines.append("")
    lines.append(
        f"SUMMARY: HIGH={len(high_syms)} ({', '.join(high_syms) or 'none'}) | "
        f"MEDIUM={len(medium_syms)} ({', '.join(medium_syms) or 'none'}) | "
        f"LOW={len(low_syms)} ({', '.join(low_syms) or 'none'})"
    )
    if divergences:
        lines.append("")
        lines.append("DIVERGENCE WARNINGS:")
        lines.extend(divergences)
    else:
        lines.append("No divergences detected.")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# AGENT 2 — MACRO ANALYST  (LLM — genuine cross-asset synthesis)
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
# AGENT 3 — OPPORTUNITY FINDER  (LLM — genuine synthesis)
# ══════════════════════════════════════════════════════════════
OPPORTUNITY_SYSTEM = """You are the Opportunity Finder for Decifer, an autonomous trading system.
Your job is to synthesise technical and macro analysis to identify the 3 best trading
opportunities available RIGHT NOW across ANY asset class IBKR supports.
You have NO bias toward stocks, FX, options, commodities, or any other instrument.
You go where the opportunity is. Be decisive and specific.
For any unfamiliar symbol, reason about it from first principles using the data provided.
Do not dismiss a symbol just because it is unfamiliar — analyse the data."""


def agent_opportunity(technical_report: str, macro_report: str,
                      signals: list, options_signals: list = None,
                      strategy_mode: dict = None) -> str:
    """Identify top 3 opportunities by synthesising technical, macro, and options flow."""

    available = ", ".join([s["symbol"] for s in signals]) if signals else "None above threshold"

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
            "- EARNINGS_PLAY = catalyst upcoming -- options are often the better instrument.\n"
            "- Low IVR (<30%) = options cheap = good risk/reward to buy premium.\n"
            "- High options score (20+/30) = strong conviction signal from options market."
        )
    else:
        options_section = "OPTIONS FLOW DATA: Not available this cycle."
        options_note = ""

    _sm = strategy_mode or {}
    _sm_context = _sm.get("context", "")
    if _sm_context:
        strategy_block = f"\n=== TRADING CONTEXT ===\n{_sm_context}\n=======================\n"
    else:
        strategy_block = ""

    prompt = f"""TECHNICAL ANALYST REPORT:
{technical_report}

MACRO ANALYST REPORT:
{macro_report}

SYMBOLS CURRENTLY SCORING ABOVE THRESHOLD: {available}

{options_section}{options_note}
{strategy_block}
Based on all reports, identify the TOP 3 trading opportunities right now.
For each opportunity provide:
1. SYMBOL and ASSET CLASS
2. DIRECTION: LONG or SHORT
3. CONVICTION: HIGH / MEDIUM / LOW
4. ENTRY RATIONALE: Why this, why now? (reference options flow if relevant)
   IMPORTANT: Lead with the REAL WORLD reason -- a news catalyst, earnings event, sector rotation,
   fundamental change, or a clear chart pattern (e.g. "breaking out of 3-month base on 2x volume").
   Do NOT lead with indicator values. A human reading this should understand the trade thesis
   without knowing what ADX or MFI means.
5. KEY RISK: What could make this wrong?
6. SUGGESTED INSTRUMENT: Stock / Call option / Put option / Inverse ETF / FX pair
   -- If options flow data supports the trade and IVR is low, PREFER options over stock.

If fewer than 3 genuine opportunities exist, say so clearly. Do not force trades.
Quality over quantity. A good reason to stay in cash is a valid output."""

    return _call_claude(OPPORTUNITY_SYSTEM, prompt)


# ══════════════════════════════════════════════════════════════
# AGENT 4 — DEVIL'S ADVOCATE  (LLM — adversarial reasoning)
# ══════════════════════════════════════════════════════════════
DEVILS_SYSTEM = """You are the Devil's Advocate for Decifer, an autonomous trading system.
Your ONLY job is to find reasons NOT to take each proposed trade.
You are adversarial by design. You protect capital by being skeptical.
For every proposed opportunity, argue against it as strongly as you can.
Flag anything that could cause a loss. Be ruthless but fair.
If a trade is genuinely strong, you may acknowledge it -- but still find the weaknesses."""


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
# PRIVATE HELPERS — deterministic vote parsing for Agent 6
# ══════════════════════════════════════════════════════════════

def _extract_proposed_symbols(opportunity_text: str, signals: list) -> list:
    """
    Return signal dicts for symbols proposed in opportunity_text that are
    also present in the scored signals list.  Order = first mention.
    """
    if not opportunity_text or not signals:
        return []
    sig_map = {s["symbol"]: s for s in signals}
    labeled = re.findall(r"SYMBOL[:\s]+([A-Z]{1,5})", opportunity_text.upper())
    bare = re.findall(r"\b([A-Z]{2,5})\b", opportunity_text)
    seen, result = set(), []
    for sym in labeled + bare:
        if sym in sig_map and sym not in seen:
            seen.add(sym)
            result.append(sig_map[sym])
        if len(result) >= 3:
            break
    return result


def _extract_macro_vote(macro_text: str) -> int:
    """+1 BULLISH/risk-ON | -1 BEARISH/risk-OFF/UNCERTAIN | 0 neutral."""
    if not macro_text:
        return 0
    upper = macro_text.upper()
    if any(kw in upper for kw in ("BULLISH", "RISK-ON", "RISK ON", "BULL_TRENDING")):
        return 1
    if any(kw in upper for kw in ("BEARISH", "RISK-OFF", "RISK OFF", "UNCERTAIN")):
        return -1
    return 0


def _extract_devils_vetoes(devils_text: str, symbols: list) -> set:
    """Return set of symbols where the devil's advocate issued a STRONG VETO."""
    if not devils_text:
        return set()
    upper = devils_text.upper()
    vetoed = set()
    for sym in symbols:
        idx = upper.find(sym)
        while idx != -1:
            window = upper[max(0, idx - 50):idx + 300]
            if "STRONG VETO" in window:
                vetoed.add(sym)
                break
            idx = upper.find(sym, idx + 1)
    if "STRONG VETO" in upper and len(symbols) == 1:
        vetoed.update(symbols)
    return vetoed


def _extract_technical_conviction(tech_text: str, symbol: str) -> int:
    """+1 if technical report rated this symbol HIGH conviction, else 0."""
    if not tech_text:
        return 0
    upper = tech_text.upper()
    idx = upper.find(symbol)
    while idx != -1:
        window = upper[max(0, idx - 20):idx + 120]
        if "HIGH" in window:
            return 1
        idx = upper.find(symbol, idx + 1)
    return 0


def _extract_risk_approval(risk_text: str, symbol: str) -> int:
    """
    +1 APPROVE | -1 REJECT/BLOCK | 1 default when symbol not mentioned.
    Also returns -1 on global gate closure signals in the text.
    """
    if not risk_text:
        return 1
    upper = risk_text.upper()
    if "ALL TRADES: REJECT" in upper or "RISK GATE: CLOSED" in upper:
        return -1
    idx = upper.find(symbol)
    while idx != -1:
        window = upper[idx:idx + 300]
        if "APPROVE" in window:
            return 1
        if "REJECT" in window or "BLOCK" in window:
            return -1
        idx = upper.find(symbol, idx + len(symbol))
    return 1  # Not specifically mentioned -> approved by default


def _extract_opportunity_reasoning(opportunity_text: str, symbol: str) -> str:
    """Pull the entry rationale sentence for a symbol from the opportunity report."""
    if not opportunity_text:
        return ""
    idx = opportunity_text.upper().find(symbol)
    if idx == -1:
        return ""
    section = opportunity_text[idx:idx + 600]
    m = re.search(r"RATIONALE[:\s]+(.+?)(?:\n[0-9A-Z]|\Z)", section,
                  re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:200]
    for line in section.split("\n")[1:5]:
        line = line.strip()
        if len(line) > 20 and not line.upper().startswith("SYMBOL"):
            return line[:200]
    return ""


def _extract_instrument(opportunity_text: str, symbol: str) -> str:
    """Derive suggested instrument (stock/call/put/inverse_etf/fx) from opportunity text."""
    if not opportunity_text:
        return "stock"
    idx = opportunity_text.upper().find(symbol)
    section = opportunity_text[idx:idx + 500].upper() if idx != -1 else ""
    if "PUT OPTION" in section or " PUT " in section:
        return "put"
    if "CALL OPTION" in section or " CALL " in section:
        return "call"
    if "INVERSE ETF" in section:
        return "inverse_etf"
    if "FX PAIR" in section or "FOREX" in section:
        return "fx"
    return "stock"


# ══════════════════════════════════════════════════════════════
# AGENT 5 — RISK MANAGER  (deterministic)
# Replaces LLM call: all rules are in config.py and risk.py.
# The original prompt had CRITICAL INSTRUCTION "do NOT invent a
# lower number" -- a clear signal the LLM added no value here.
# ══════════════════════════════════════════════════════════════

def agent_risk_manager(opportunity_report: str, devils_report: str,
                       open_positions: list, portfolio_value: float,
                       daily_pnl: float, regime: dict,
                       strategy_mode: dict = None,
                       signals: list = None) -> str:
    """
    Deterministic risk assessment via risk.py functions.
    Returns human-readable text (same format as before) for logging.
    Added `signals` keyword arg (backward-compatible, defaults to None).
    """
    from risk import check_risk_conditions, calculate_position_size, calculate_stops

    _sm = strategy_mode or {}
    size_mult = _sm.get("size_multiplier", 1.0)
    max_pos = CONFIG["max_positions"]
    daily_limit = CONFIG["daily_loss_limit"]

    slots_remaining = max_pos - len(open_positions)
    daily_budget_left = (portfolio_value * daily_limit) + daily_pnl

    lines = [
        f"Risk Gate check | Portfolio: ${portfolio_value:,.2f} | Daily P&L: ${daily_pnl:+,.2f}",
        f"Positions: {len(open_positions)}/{max_pos} ({slots_remaining} slots remaining)",
        f"Daily loss budget remaining: ${daily_budget_left:,.2f}",
        "",
    ]

    gate_ok, gate_reason = check_risk_conditions(
        portfolio_value, daily_pnl, regime, open_positions
    )

    if not gate_ok:
        lines.append(f"RISK GATE: CLOSED -- {gate_reason}")
        lines.append("ALL TRADES: REJECT")
        return "\n".join(lines)

    if slots_remaining <= 0:
        lines.append(
            f"RISK GATE: CLOSED -- No position slots remaining ({len(open_positions)}/{max_pos})")
        lines.append("ALL TRADES: REJECT")
        return "\n".join(lines)

    lines.append(f"RISK GATE: OPEN -- {gate_reason}")
    lines.append("")

    candidates = signals[:5] if signals else []
    for s in candidates:
        sym = s["symbol"]
        price = s.get("price", 0)
        atr = s.get("atr", price * 0.02)
        score = s.get("score", 20)
        direction = "LONG" if s.get("signal", "BUY") == "BUY" else "SHORT"

        if price <= 0:
            lines.append(f"{sym}:\n  DECISION: REJECT\n  REASON: Invalid price data")
            continue

        qty = calculate_position_size(portfolio_value, price, score, regime, atr=atr)
        qty = max(1, int(qty * size_mult))
        sl, tp = calculate_stops(price, atr, direction)

        lines.append(f"{sym}:")
        lines.append(f"  DECISION: APPROVE")
        lines.append(f"  SIZE: {qty} shares")
        lines.append(f"  STOP LOSS: ${sl:.2f}")
        lines.append(f"  TAKE PROFIT: ${tp:.2f}")
        lines.append(
            f"  REASON: Score={score}/50 | "
            f"regime_mult={regime.get('position_size_multiplier', 1.0):.1f}x | "
            f"size_mult={size_mult:.1f}x"
        )

    if not candidates:
        lines.append("No per-symbol signal data provided -- sizing deferred to Final Decision.")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# AGENT 6 — FINAL DECISION MAKER  (deterministic)
# Replaces LLM call: vote counting + JSON assembly are pure logic.
# LLM agents 2, 3, 4 still contribute text; votes are extracted
# via lightweight keyword parsing on their outputs.
# ══════════════════════════════════════════════════════════════

def agent_final_decision(technical: str, macro: str, opportunity: str,
                         devils: str, risk: str, signals: list,
                         open_positions: list, regime: dict,
                         agents_required: int,
                         weekly_memory: str = "",
                         strategy_mode: dict = None,
                         positions_to_reconsider: list = None,
                         portfolio_value: float = 0.0,
                         daily_pnl: float = 0.0) -> dict:
    """
    Deterministic final synthesis -- vote counting + JSON assembly.
    Added `portfolio_value` and `daily_pnl` keyword args for accurate sizing;
    both default to 0.0 for backward compatibility with existing callers.
    """
    from risk import calculate_position_size, calculate_stops

    _sm = strategy_mode or {}
    _sm_max_trades = _sm.get("max_new_trades", 3)
    size_mult = _sm.get("size_multiplier", 1.0)
    _reconsider = positions_to_reconsider or []
    max_pos = CONFIG["max_positions"]

    # PANIC override
    if regime.get("regime") == "PANIC":
        return {
            "buys": [], "sells": [], "hold": [], "cash": True,
            "agents_agreed": 0,
            "summary": f"PANIC -- VIX={regime.get('vix', '?')}, all cash",
            "claude_reasoning": "Regime is PANIC. No new trades. Capital preservation mode.",
        }

    # Identify proposed symbols from opportunity report
    proposed = _extract_proposed_symbols(opportunity, signals)

    # Macro vote applies to all trades equally
    macro_vote = _extract_macro_vote(macro)

    open_syms = [p["symbol"] for p in open_positions]
    slots_available = max_pos - len(open_positions)
    buys = []

    for s in proposed:
        sym = s["symbol"]

        # Vote tally per symbol
        # Technical: +1 if HIGH conviction for this symbol
        # Macro: +1 / 0 / -1
        # Opportunity: +1 (the agent proposed this symbol)
        # Risk: +1 APPROVE / -1 REJECT
        # Devil: -1 STRONG VETO (only penalty; MODERATE CONCERN has no vote impact)
        votes = 0
        votes += _extract_technical_conviction(technical, sym)
        votes += macro_vote
        votes += 1  # Opportunity Finder proposed this symbol
        votes += _extract_risk_approval(risk, sym)
        if sym in _extract_devils_vetoes(devils, [sym]):
            votes -= 1

        if votes < agents_required:
            log.debug(f"Final: {sym} skipped -- {votes} votes < {agents_required} required")
            continue

        if slots_available <= 0 or len(buys) >= _sm_max_trades:
            break

        # Sizing
        price = s.get("price", 0)
        atr = s.get("atr", price * 0.02 if price > 0 else 1.0)
        score = s.get("score", 20)
        direction = "LONG" if s.get("signal", "BUY") == "BUY" else "SHORT"

        if price > 0 and portfolio_value > 0:
            qty = calculate_position_size(portfolio_value, price, score, regime, atr=atr)
            qty = max(1, int(qty * size_mult))
            sl, tp = calculate_stops(price, atr, direction)
        else:
            qty, sl, tp = 1, 0.0, 0.0

        reasoning = (
            _extract_opportunity_reasoning(opportunity, sym)
            or f"{sym} selected by opportunity agent"
        )
        instrument = _extract_instrument(opportunity, sym)

        buys.append({
            "symbol": sym,
            "qty": qty,
            "sl": sl,
            "tp": tp,
            "instrument": instrument,
            "reasoning": reasoning,
        })
        slots_available -= 1

    # Sells from positions to reconsider
    sells = [
        p["symbol"] for p in _reconsider
        if p.get("reason", "").upper() != "HOLD" and p["symbol"] in open_syms
    ]

    hold = [sym for sym in open_syms if sym not in sells]

    # agents_agreed = vote count for the first accepted buy (or 0)
    if buys:
        first_sym = buys[0]["symbol"]
        agents_agreed = max(0, min(6,
            _extract_technical_conviction(technical, first_sym)
            + macro_vote
            + 1
            + _extract_risk_approval(risk, first_sym)
            + (-1 if first_sym in _extract_devils_vetoes(devils, [first_sym]) else 0)
        ))
    else:
        agents_agreed = 0

    # Summary and reasoning templates
    regime_label = regime.get("regime", "UNKNOWN")
    macro_str = "bullish" if macro_vote > 0 else ("bearish" if macro_vote < 0 else "neutral")

    if buys:
        buy_syms = ", ".join(b["symbol"] for b in buys)
        summary = f"{regime_label} -- {macro_str} macro, entering {buy_syms}"
        first_r = buys[0].get("reasoning", "")[:100]
        claude_reasoning = (
            f"Entering {buys[0]['symbol']}: {first_r}. "
            f"{agents_agreed} agents aligned. "
            f"Risk gate open, {len(open_positions)}/{max_pos} positions used."
        )
    elif sells:
        summary = f"{regime_label} -- exiting {', '.join(sells)}"
        claude_reasoning = f"Exiting {', '.join(sells)} based on position reconsideration."
    else:
        summary = f"{regime_label} -- {macro_str} macro, no new entries this cycle"
        claude_reasoning = (
            f"No trades this cycle. {macro_str.capitalize()} macro environment. "
            f"Insufficient agent consensus ({agents_required} votes required) or no qualifying setups."
        )

    return {
        "buys": buys,
        "sells": sells,
        "hold": hold,
        "cash": False,
        "agents_agreed": agents_agreed,
        "summary": summary,
        "claude_reasoning": claude_reasoning,
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
                   options_signals: list = None,
                   strategy_mode: dict = None,
                   positions_to_reconsider: list = None) -> dict:
    """
    Run all 6 agents sequentially and return final decision.
    Each agent output feeds into the next.
    options_signals: live options flow data from options_scanner.py
    """
    weekly_memory = load_weekly_review()

    if strategy_mode is None:
        strategy_mode = {"mode": "NORMAL", "context": "", "size_multiplier": 1.0,
                         "max_new_trades": 3, "score_threshold_adj": 0, "regime_changed": False}
    if positions_to_reconsider is None:
        positions_to_reconsider = []

    log.info("Agents 1+2: Technical + Macro (parallel)...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        tech_future = pool.submit(agent_technical, signals, regime)
        macro_future = pool.submit(agent_macro, regime, news, fx_data)
        tech = tech_future.result()
        macro = macro_future.result()

    log.info("Agent 3: Opportunity Finder (with options flow)...")
    opp = agent_opportunity(tech, macro, signals, options_signals=options_signals or [],
                            strategy_mode=strategy_mode)

    log.info("Agent 4: Devil\'s Advocate...")
    devils = agent_devils_advocate(opp, regime)

    log.info("Agent 5: Risk Manager (deterministic)...")
    risk = agent_risk_manager(opp, devils, open_positions,
                              portfolio_value, daily_pnl, regime,
                              strategy_mode=strategy_mode,
                              signals=signals)

    log.info("Agent 6: Final Decision Maker (deterministic)...")
    final = agent_final_decision(tech, macro, opp, devils, risk,
                                 signals, open_positions, regime,
                                 CONFIG["agents_required_to_agree"],
                                 weekly_memory=weekly_memory,
                                 strategy_mode=strategy_mode,
                                 positions_to_reconsider=positions_to_reconsider,
                                 portfolio_value=portfolio_value,
                                 daily_pnl=daily_pnl)

    final["_agent_outputs"] = {
        "technical": tech,
        "macro": macro,
        "opportunity": opp,
        "devils": devils,
        "risk": risk,
    }

    return final
