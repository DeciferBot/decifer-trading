# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  portfolio_manager.py                       ║
# ║   Active portfolio intelligence — thesis drift detection     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Watches open positions for thesis drift. Runs on event triggers,
not on a fixed interval — only fires when information has actually changed.

Trigger sources (checked in bot_trading.py):
  1. pre_market      — once per day before the open
  2. regime_change   — market regime flips
  3. score_collapse  — position's signal score drops 15+ pts from entry
  4. news_hit        — significant keyword score on a held symbol
  5. earnings_risk   — earnings within 48 hours on a held symbol
  6. cascade         — 2+ positions hit stops in the same session
  7. drawdown        — daily P&L < -1.5% of portfolio

Uses Opus (claude_model_alpha) — same model as Trading Analyst.
Mechanical stops still handle adverse price moves. This agent handles
thesis drift: "does the original story still make sense?"
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import anthropic

from config import CONFIG

log = logging.getLogger("decifer.portfolio_manager")

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    return _client


# ══════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════

_PM_SYSTEM = """You are the Portfolio Manager for Decifer, an autonomous US equity trading system.

Your ONLY job is to review currently open positions and decide whether each should be held, \
trimmed, exited early, or added to. You do NOT enter new trades — that is the Trading Analyst's job.

ACTIONS (use exactly one per position):
  HOLD  — thesis intact, mechanical stops are sufficient, no action needed
  TRIM  — reduce position by ~50% now; signal weakening but not broken; lock in partial gains
  EXIT  — exit the full position immediately; thesis is broken; do not wait for stop
  ADD   — add to position; signal strengthening, price pulling back to a good level

DECISION FRAMEWORK — reason through these in order for each position:

1. SIGNAL DRIFT: If current score is significantly below entry score, the technical thesis has weakened.
   - Drop of 15+ points → strong EXIT signal
   - Drop of 8–14 points → TRIM or EXIT depending on other factors
   - Within 7 points → HOLD unless other factors override

2. NEWS: If a significant negative/positive catalyst has emerged on this symbol, update accordingly.
   - Negative catalyst on a LONG → EXIT or TRIM
   - Positive catalyst on a LONG → HOLD or ADD (if price is pulling back)

3. REGIME SHIFT: If regime has changed since entry, reassess whether the entry thesis still holds.
   - Entered BULL_TRENDING, now CHOPPY or PANIC → TRIM or EXIT longs
   - Entered BEAR_TRENDING, now BULL_TRENDING → TRIM or EXIT shorts

4. EARNINGS RISK: If earnings are within 48 hours, make a deliberate hold-or-exit decision.
   - High conviction position with strong thesis → HOLD through earnings is a choice, not a default
   - Low conviction or thesis already weakening → EXIT before binary event

5. P&L CONTEXT: Profitable positions get more benefit of the doubt. Losers need a stronger thesis.
   - Position up >3% with intact signal → HOLD or ADD
   - Position flat/down with weakening signal → EXIT

OUTPUT FORMAT — produce exactly this for every position provided, no exceptions:

SYMBOL: <ticker>
ACTION: HOLD | TRIM | EXIT | ADD
REASON: <one clear sentence — lead with the dominant factor>

RULES:
- Every position in the input must get an output entry.
- HOLD is the correct answer when the thesis is intact. Do not manufacture action.
- Be decisive. Vague answers ("maybe trim", "consider exiting") are not allowed.
- Do not recommend new symbols or comment on market conditions generally."""


# ══════════════════════════════════════════════════════════════
# EARNINGS DETECTION
# ══════════════════════════════════════════════════════════════

def _check_earnings_within_hours(symbols: list, hours: int = 48) -> set:
    """
    Return set of symbols with earnings scheduled within `hours` hours.
    Uses yfinance calendar — returns empty set on any failure (non-blocking).
    Caches nothing — called infrequently (event-triggered only).
    """
    flagged = set()
    if not symbols:
        return flagged
    try:
        import yfinance as yf
        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc + timedelta(hours=hours)
        for sym in symbols:
            try:
                cal = yf.Ticker(sym).calendar
                if cal is None or cal.empty:
                    continue
                # calendar has columns like 'Earnings Date'
                for col in cal.columns:
                    if "earnings" in col.lower():
                        for val in cal[col].dropna():
                            if hasattr(val, "to_pydatetime"):
                                val = val.to_pydatetime()
                            if isinstance(val, datetime):
                                if val.tzinfo is None:
                                    val = val.replace(tzinfo=timezone.utc)
                                if now_utc <= val <= cutoff:
                                    flagged.add(sym)
            except Exception:
                continue
    except Exception as exc:
        log.debug(f"portfolio_manager: earnings check failed ({exc})")
    return flagged


# ══════════════════════════════════════════════════════════════
# MAIN REVIEW FUNCTION
# ══════════════════════════════════════════════════════════════

def run_portfolio_review(
    open_positions: list,
    all_scored: list,
    regime: dict,
    news_sentiment: dict,
    portfolio_value: float,
    trigger: str,
) -> list:
    """
    Review open positions for thesis drift.

    Args:
        open_positions: from get_open_positions() — each has symbol, entry, current,
                        qty, score (entry_score), entry_score, pnl, direction, open_time
        all_scored:     full scored universe from signal_pipeline (includes below-threshold)
        regime:         current regime dict
        news_sentiment: symbol → {keyword_score, claude_sentiment, claude_catalyst}
        portfolio_value: current portfolio value
        trigger:        why this review was triggered (for logging + context)

    Returns:
        list of {symbol, action, reasoning} — one entry per open position
    """
    pm_cfg = CONFIG.get("portfolio_manager", {})
    if not pm_cfg.get("enabled", True):
        return []

    if not open_positions:
        return []

    regime_name = regime.get("regime", "UNKNOWN")
    vix = regime.get("vix", 0)

    # Build score lookup from all_scored
    score_map = {s["symbol"]: s.get("score", 0) for s in (all_scored or [])}

    # Check earnings
    stock_syms = [p["symbol"] for p in open_positions if p.get("instrument") != "option"]
    earnings_lookahead = pm_cfg.get("earnings_lookahead_hours", 48)
    earnings_flagged = _check_earnings_within_hours(stock_syms, earnings_lookahead)

    # Build position summaries for the prompt
    now_utc = datetime.now(timezone.utc)
    pos_lines = []
    for p in open_positions:
        sym = p.get("symbol", "?")
        entry_price = p.get("entry", 0)
        current_price = p.get("current", entry_price)
        qty = p.get("qty", 0)
        direction = p.get("direction", "LONG")
        instrument = p.get("instrument", "stock")

        # Use entry_score if stored separately, fall back to score field
        entry_score = p.get("entry_score", p.get("score", 0))
        current_score = score_map.get(sym)

        pnl = p.get("pnl", 0)
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        # Days held
        try:
            open_dt = datetime.fromisoformat(p.get("open_time", "")).replace(tzinfo=timezone.utc)
            days_held = (now_utc - open_dt).days
        except Exception:
            days_held = 0

        # Entry regime (stored on position or fall back to current)
        entry_regime = p.get("regime", regime_name)

        # News
        news = news_sentiment.get(sym, {})
        kw = news.get("keyword_score", 0)
        sent = news.get("claude_sentiment", "")
        cat = news.get("claude_catalyst", "")
        news_str = ""
        if sent or kw:
            news_str = f"  News: {sent} (keyword={kw:+d})"
            if cat:
                news_str += f" | {cat[:60]}"

        score_line = f"entry_score={entry_score}/50"
        if current_score is not None:
            delta = current_score - entry_score
            score_line += f" → current={current_score}/50 (delta={delta:+d})"
        else:
            score_line += " → current=not_in_universe"

        earnings_str = f"  *** EARNINGS WITHIN {earnings_lookahead}h ***" if sym in earnings_flagged else ""

        pos_lines.append(
            f"POSITION: {sym} ({instrument}) {direction}\n"
            f"  entry=${entry_price:.2f} current=${current_price:.2f} "
            f"qty={qty} pnl={pnl_pct:+.1f}%\n"
            f"  {score_line}\n"
            f"  entry_regime={entry_regime} current_regime={regime_name}\n"
            f"  days_held={days_held}"
            + (f"\n{news_str}" if news_str else "")
            + (f"\n{earnings_str}" if earnings_str else "")
        )

    prompt = f"""REVIEW TRIGGER: {trigger}
REGIME: {regime_name} | VIX={vix:.1f}
PORTFOLIO VALUE: ${portfolio_value:,.2f}

OPEN POSITIONS ({len(open_positions)}):
{chr(10).join(pos_lines)}

Review each position and output SYMBOL / ACTION / REASON for every one."""

    try:
        client = _get_client()
        resp = client.messages.create(
            model=CONFIG.get("claude_model_alpha", "claude-opus-4-6"),
            max_tokens=CONFIG.get("claude_max_tokens_alpha", 4096),
            system=[{
                "type": "text",
                "text": _PM_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        log.info(f"Portfolio review ({trigger}): {len(open_positions)} positions reviewed")
        return _parse_actions(raw, open_positions)

    except Exception as exc:
        log.error(f"portfolio_manager: LLM call failed ({exc}) — returning all HOLD")
        return [{"symbol": p["symbol"], "action": "HOLD", "reasoning": "review_failed"} for p in open_positions]


# ══════════════════════════════════════════════════════════════
# RESPONSE PARSER
# ══════════════════════════════════════════════════════════════

def _parse_actions(text: str, open_positions: list) -> list:
    """
    Parse SYMBOL/ACTION/REASON blocks from Portfolio Manager output.
    Falls back to HOLD for any position not found in output.
    """
    import re
    results = {}
    blocks = re.split(r"\n(?=SYMBOL:)", text.strip())
    for block in blocks:
        sym_m = re.search(r"SYMBOL:\s*([A-Z]{1,6})", block)
        act_m = re.search(r"ACTION:\s*(HOLD|TRIM|EXIT|ADD)", block)
        rea_m = re.search(r"REASON:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
        if sym_m and act_m:
            sym = sym_m.group(1)
            results[sym] = {
                "symbol": sym,
                "action": act_m.group(1),
                "reasoning": rea_m.group(1).strip() if rea_m else "",
            }

    # Ensure every open position has an entry
    output = []
    for p in open_positions:
        sym = p["symbol"]
        if sym in results:
            output.append(results[sym])
        else:
            log.warning(f"portfolio_manager: no action parsed for {sym} — defaulting HOLD")
            output.append({"symbol": sym, "action": "HOLD", "reasoning": "not_in_output"})

    return output
