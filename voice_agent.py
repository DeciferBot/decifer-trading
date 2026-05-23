"""
voice_agent.py — Decifer Voice Analyst.

Single public entry point: answer_voice_question(query, dash) → str.

Flow:
  1. Classify intent (regex, no LLM cost)
  2. Control intents (PAUSE/RESUME) execute immediately — no LLM
  3. Build focused context slice via voice_context_builder + voice_explainability_tools
  4. Call Claude Haiku once with that context
  5. Return concise spoken answer (3–7 sentences)

Never raises. Falls back to a plain error message on any failure.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import anthropic as _anthropic_module

from config import CONFIG
from voice_context_builder import build_full_context
from voice_explainability_tools import (
    explain_active_themes,
    explain_blocked_candidate,
    explain_bot_health,
    explain_learning,
    explain_market_regime,
    explain_no_trade,
    explain_portfolio_risk,
    explain_position,
    explain_recent_trade,
)

log = logging.getLogger("decifer.voice.agent")

# ─── Intent regex patterns ────────────────────────────────────────────────────
# Each pattern list is matched in order; first match wins.

_CONTROL_PAUSE = [
    r"\b(pause|stop)\s+(the\s+)?(bot|scan(ning)?)\b",
    r"^\s*pause\s*$",
]
_CONTROL_RESUME = [
    r"\b(resume|unpause|restart)\s+(the\s+)?(bot|scan(ning)?)\b",
    r"^\s*resume\s*$",
]

_EXPLAIN_HOLDING = [
    r"why (are|is) (we|you|the bot) (still )?(hold(ing)?|long|short|in) (?P<sym>[A-Z]{1,6})\b",
    r"why (are|is) (?P<sym>[A-Z]{1,6}) (still )?(in (the )?portfolio|being held|a hold)",
    # "position in/on SYMBOL" — require the preposition so sym is unambiguous
    r"(?:tell me about|explain|status of|what.?s happening with) (?:my |our |the )?(?:position (?:in|on)|holding) (?P<sym>[A-Z]{2,6})\b",
    r"(what.?s|what is) (going on with|the deal with|the status on) (?P<sym>[A-Z]{2,6})\b",
]

_EXPLAIN_TRADE = [
    r"why did (you|the bot) (buy|enter|go long( on)?|go short( on)?|sell|exit|close) (?P<sym>[A-Z]{1,6})\b",
    r"(explain|what happened with|tell me about) (the )?(trade|entry|exit|position)( on| in| for)? (?P<sym>[A-Z]{2,6})\b",
    r"why was (?P<sym>[A-Z]{1,6}) (bought|sold|closed|entered|exited|shorted)\b",
    r"(last|latest|most recent) trade (on|for|in)? (?P<sym>[A-Z]{2,6})\b",
]

_EXPLAIN_BLOCKED = [
    r"why (was|is|did) (?P<sym>[A-Z]{1,6}) (blocked|rejected|skipped|not traded|not entered|passed on|missed|avoided)\b",
    r"why (didn.?t|did not) (you|the bot) (trade|buy|enter|take) (?P<sym>[A-Z]{2,6})\b",
    r"what (happened to|stopped|blocked) (?P<sym>[A-Z]{2,6})\b",
    r"why (no|zero) (?P<sym>[A-Z]{2,6})\b",
]

_NO_TRADE = [
    r"why (didn.?t|did.?n.?t|have.?n.?t) (you|the bot|we) trade[d]? (today|this session|anything|at all)",
    r"why did (you|the bot|we) not trade",
    r"why (no|zero|0) trades? (today|this session)",
    r"(what.?s|what is) (holding|blocking|stopping) the bot",
    r"what.?s the bot waiting for",
    r"why (isn.?t|is not) the bot (trading|doing anything|entering)",
    r"(didn.?t trade|no trades) today",
    r"what (is the bot|are you) waiting for",
]

_PORTFOLIO_STATUS = [
    r"(what|which) (are|is) (we|the bot) (hold(ing)?|long|short|in)\b",
    r"(show|give me|tell me about|what.?s in) (the |our |my )?(portfolio|book|positions|holdings)\b",
    r"(are we|how) (over|under)(exposed|weight)\b",
    r"(what are the|main|biggest) risks? (in|of) (the )?(book|portfolio|positions)\b",
    r"(how many|how much) positions? (do we have|are we in|are open)\b",
    r"(what.?s our|tell me our|our) (exposure|risk|book)\b",
    r"which position is (weakest|strongest|worst|best|biggest|smallest|most profitable|losing most)\b",
    r"(concentration|overexposed|overweight|portfolio risk)\b",
]

_MARKET_CONVO = [
    r"(what.?s|what is) the (current )?(market )?(regime|condition|environment|mood|sentiment)\b",
    r"(how is|what.?s happening (with|in)) (the )?market",
    r"which (theme|sector|group) is (strongest|hottest|leading|winning|active|best|top)\b",
    r"(what|which) themes? are? (active|running|working|hot|live|firing)\b",
    r"(what.?s|what is) (driving|moving|leading|pushing) (the )?market",
    r"(what was|tell me) (the |your |apex.?s )?(analysis|view|read|take|synthesis)\b",
    r"(describe|what are) (the )?(active |current )?(themes?|sectors?|drivers?)\b",
    r"(vix|spy|market) (level|price|reading|today)\b",
]

_LEARNING = [
    r"what (did|has) (the bot|decifer|you) learn",
    r"(how|what).?s (our|the bot.?s|decifer.?s) (performance|results|track record|hit rate|win rate)\b",
    r"(recent|latest) (performance|results|pnl|trades?|outcomes?)\b",
    r"(win|hit|batting) (rate|average|percentage)\b",
    r"how (are we|have we been) doing\b",
    r"how have (we|you|the bot) been (performing|trading|doing)\b",
    r"how (have|has) (we|the bot|decifer) (been doing|performed|been performing)\b",
]

_BOT_STATUS = [
    r"(what.?s|what is) (the bot|decifer|you) doing\b",
    r"(is the bot|are you) (running|active|scanning|paused|working|alive|online|connected)\b",
    r"(bot|system|decifer) (status|health|state)\b",
    r"(what.?s|what is) (happening|going on) (right now|with the bot|with decifer)\b",
    r"why is the bot (paused|stopped|idle|not scanning|not running)\b",
    r"(last scan|when did you last scan|when.?s the next scan)\b",
]


# Words that can appear where a ticker symbol would syntactically fit but are not symbols.
_NOT_SYMBOLS = frozenset([
    "MY", "OUR", "THE", "ITS", "ANY", "ALL", "NOW", "TODAY", "THIS", "THAT",
    "LAST", "NEXT", "SOME", "MOST", "VERY", "JUST", "STILL", "ALSO", "BOTH",
    "ABOUT", "AFTER", "AGAIN", "ALONG", "ANOTHER", "AT", "BACK", "BAD",
    "YESTERDAY", "ANYTHING", "EVERYTHING", "NOTHING", "SESSION", "NEVER",
    "ALWAYS", "RECENTLY", "LATELY", "BEFORE", "SINCE", "UNTIL", "WHEN",
])


def _match_any(query: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if re.search(pat, query, re.IGNORECASE):
            return True
    return False


def _extract_symbol(query: str, patterns: list[str]) -> Optional[str]:
    q_upper = query.upper()
    for pat in patterns:
        m = re.search(pat, q_upper, re.IGNORECASE)
        if m and "sym" in m.groupdict() and m.group("sym"):
            sym = m.group("sym").upper()
            if sym not in _NOT_SYMBOLS:
                return sym
    return None


def _classify(query: str) -> tuple[str, Optional[str]]:
    """
    Returns (intent, symbol_if_applicable).

    Intents:
      CONTROL_PAUSE, CONTROL_RESUME,
      EXPLAIN_HOLDING, EXPLAIN_TRADE, EXPLAIN_BLOCKED,
      NO_TRADE, PORTFOLIO_STATUS, MARKET_CONVO,
      LEARNING, BOT_STATUS, GENERAL_QA
    """
    q = query.strip()

    if _match_any(q, _CONTROL_PAUSE):
        return "CONTROL_PAUSE", None
    if _match_any(q, _CONTROL_RESUME):
        return "CONTROL_RESUME", None

    sym = _extract_symbol(q, _EXPLAIN_HOLDING)
    if sym:
        return "EXPLAIN_HOLDING", sym

    sym = _extract_symbol(q, _EXPLAIN_TRADE)
    if sym:
        return "EXPLAIN_TRADE", sym

    # NO_TRADE before EXPLAIN_BLOCKED: "why didn't you trade today" must not
    # match EXPLAIN_BLOCKED with "TODAY" as the symbol.
    if _match_any(q, _NO_TRADE):
        return "NO_TRADE", None

    sym = _extract_symbol(q, _EXPLAIN_BLOCKED)
    if sym:
        return "EXPLAIN_BLOCKED", sym

    if _match_any(q, _PORTFOLIO_STATUS):
        return "PORTFOLIO_STATUS", None

    if _match_any(q, _MARKET_CONVO):
        return "MARKET_CONVO", None

    if _match_any(q, _LEARNING):
        return "LEARNING", None

    if _match_any(q, _BOT_STATUS):
        return "BOT_STATUS", None

    return "GENERAL_QA", None


def _build_context_slice(intent: str, symbol: Optional[str], ctx: dict) -> str:
    """Return a focused plain-text context block for the given intent."""
    if intent == "EXPLAIN_HOLDING":
        return explain_position(symbol, ctx)

    if intent == "EXPLAIN_TRADE":
        return explain_recent_trade(symbol, ctx)

    if intent == "EXPLAIN_BLOCKED":
        return explain_blocked_candidate(symbol, ctx)

    if intent == "NO_TRADE":
        return explain_no_trade(ctx)

    if intent == "PORTFOLIO_STATUS":
        return explain_portfolio_risk(ctx)

    if intent == "MARKET_CONVO":
        regime = explain_market_regime(ctx)
        themes = explain_active_themes(ctx)
        return f"{regime}\n\n{themes}"

    if intent == "BOT_STATUS":
        return explain_bot_health(ctx)

    if intent == "LEARNING":
        return explain_learning(ctx)

    # GENERAL_QA — broad context snapshot
    dash = ctx.get("dash", {})
    regime = dash.get("regime", {})
    positions = ctx.get("positions", {})
    return (
        f"Portfolio: ${dash.get('portfolio_value', 0):,.2f} | "
        f"Day P&L: ${dash.get('daily_pnl', 0):+,.2f}\n"
        f"Session: {dash.get('session', 'UNKNOWN')} | "
        f"Regime: {regime.get('regime', 'UNKNOWN')} | "
        f"VIX: {regime.get('vix', '?')} | SPY: ${regime.get('spy_price', '?')}\n"
        f"Open positions: {len(positions)}\n"
        f"Bot: {'PAUSED' if dash.get('paused') else 'ACTIVE'} | "
        f"Scans: {dash.get('scan_count', 0)}\n"
        + (f"Last Apex view: {dash['claude_analysis'][:400]}" if dash.get("claude_analysis") else "")
    )


_SYSTEM_PROMPT = (
    "You are the Decifer Voice Analyst — a calm, knowledgeable assistant embedded in an autonomous trading system.\n"
    "You have access to live portfolio state, trade history, PM engine decisions, Apex synthesis, and market intelligence.\n"
    "\n"
    "Rules:\n"
    "- Be direct and concise. 3 to 7 sentences maximum.\n"
    "- Base every explanation on the context provided. Do not invent reasons or prices.\n"
    "- If evidence is missing, say so explicitly. Never fabricate confidence you don't have.\n"
    "- Use plain English. No markdown, no bullet points, no headers, no formatting.\n"
    "- Reference actual data points (prices, scores, regimes, reasons) when available in the context.\n"
    "- If data is stale, mention it is stale.\n"
    "- Sound like a knowledgeable colleague briefing the trader — warm, direct, useful."
)


def answer_voice_question(query: str, dash: dict, *, read_only: bool = False) -> str:
    """
    Classify the query, build focused context, call Haiku once, return spoken answer.
    Control commands (pause/resume) are executed without an LLM call.
    Pass read_only=True (mobile) to block state-mutating control intents.
    Never raises — returns a plain fallback on any error.
    """
    if not query or not query.strip():
        return "I didn't catch that. Please try again."

    try:
        intent, symbol = _classify(query)

        # ── Control intents: no LLM ──────────────────────────────────────────
        if intent in ("CONTROL_PAUSE", "CONTROL_RESUME"):
            if read_only:
                return "Control commands are not available on mobile. Use the dashboard to pause or resume scanning."
            if intent == "CONTROL_PAUSE":
                dash["paused"] = True
                return "Scanning paused."
            dash["paused"] = False
            return "Resuming scans."

        # ── Collect context ──────────────────────────────────────────────────
        ctx = build_full_context(dash)
        context_text = _build_context_slice(intent, symbol, ctx)

        # ── Single Haiku call ────────────────────────────────────────────────
        client = _anthropic_module.Anthropic(api_key=CONFIG["anthropic_api_key"])

        resp = client.messages.create(
            model=CONFIG.get("claude_model_haiku", "claude-haiku-4-5-20251001"),
            max_tokens=220,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Context:\n{context_text}\n\nQuestion: {query}",
            }],
        )

        answer = resp.content[0].text.strip()
        return answer or "I couldn't find an answer right now."

    except Exception as e:
        log.error("voice_agent.answer_voice_question error: %s", e)
        return "I ran into an issue retrieving that. Please try again."
