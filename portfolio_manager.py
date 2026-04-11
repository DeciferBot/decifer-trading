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
from earnings_calendar import get_earnings_within_hours

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

_PM_SYSTEM = """You are the Portfolio Manager for Decifer, an autonomous trading system.

Your ONLY job is to review currently open positions and decide whether each should be held, \
trimmed, exited early, or added to. You do NOT enter new trades — that is the Trading Analyst's job.

ACTIONS (use exactly one per position):
  HOLD  — thesis intact, mechanical stops are sufficient, no action needed
  TRIM  — reduce position by ~50% now; signal weakening but not broken; lock in partial gains
  EXIT  — exit the full position immediately; thesis is broken; do not wait for stop
  ADD   — add to position; signal strengthening, price pulling back to a good level

TRADE TYPES — each position has a type that determines the right review lens:
  SCALP — pure technical, short hold. Score drift > 5 points or any regime/news deterioration → EXIT fast.
          These are meant to be quick. Do not hold a SCALP hoping for recovery.
  SWING — technical entry with a backing thesis. Moderate tolerance for drift.
          Score drift 8–14 → TRIM. Drop 15+ → EXIT. News and regime are primary factors.
  HOLD  — thesis-driven, longer time horizon. Score drift is largely irrelevant.
          Technical noise should be ignored. EXIT only if the original backing thesis has broken.
          These are meant to be held through noise. Do not exit a HOLD on a bad day alone.

DECISION FRAMEWORK — apply in order, weighted by trade_type:

1. SIGNAL DRIFT (primary for SCALP/SWING, secondary for HOLD):
   - SCALP: Drop of 5+ points → EXIT immediately
   - SWING: Drop of 15+ points → EXIT. Drop 8–14 → TRIM or EXIT on other factors.
   - HOLD: Score drift alone is not sufficient to exit. Assess thesis integrity instead.
   - "unscored_today" = scanner did not rescore this cycle. Treat as neutral, not a drop.

2. NEWS: Significant catalyst on the symbol.
   - Negative catalyst on a LONG → EXIT (all types) or TRIM (HOLD if thesis survives news)
   - Positive catalyst on a LONG → HOLD or ADD if pulling back to entry zone
   - Positive catalyst on a SHORT → EXIT or TRIM (news is against the thesis)
   - Negative catalyst on a SHORT → HOLD or ADD (news supports the thesis)

3. REGIME SHIFT: Has the market environment changed since entry?
   - SCALP/SWING: Regime shift against position direction → EXIT or TRIM
   - HOLD: Regime shift alone is not sufficient — assess whether the original thesis is broken

4. EARNINGS RISK: Earnings within 48 hours.
   - SCALP → EXIT before binary event (no reason to hold through earnings on a scalp)
   - SWING → Deliberate choice: high conviction intact thesis = HOLD; weakening = EXIT
   - HOLD → HOLD through earnings is the default if thesis is intact

5. P&L CONTEXT: Profitable positions get more benefit of the doubt. Losers need stronger thesis.
   - Up >3% with intact signal → HOLD or ADD
   - Flat/down with weakening signal → EXIT (SCALP/SWING) or TRIM (HOLD)

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
    earnings_flagged = get_earnings_within_hours(stock_syms, earnings_lookahead)

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
            score_line += " → current=unscored_today (scanner did not rescore this cycle)"

        earnings_str = f"  *** EARNINGS WITHIN {earnings_lookahead}h ***" if sym in earnings_flagged else ""

        trade_type = p.get("trade_type", "SCALP")
        conviction = p.get("conviction", 0.0)

        pos_lines.append(
            f"POSITION: {sym} ({instrument}) {direction}  trade_type={trade_type}  conviction={conviction:.2f}\n"
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

def _regime_polarity(regime_str: str) -> str:
    """Return 'BULL', 'BEAR', or '' from a regime label string.
    Handles both legacy mechanical labels (BULL_TRENDING, BEAR_TRENDING) and
    the current regime vocab (TRENDING_UP, TRENDING_DOWN, CAPITULATION, etc.).
    """
    r = (regime_str or "").upper()
    # Explicit BULL mappings — unambiguously bullish regimes
    if r in ("TRENDING_UP", "MOMENTUM_BULL", "BULL", "BULL_TRENDING"):
        return "BULL"
    # Explicit BEAR mappings — bearish/risk-off regimes including relief rallies
    if r in ("TRENDING_DOWN", "RELIEF_RALLY", "CAPITULATION", "DISTRIBUTION",
             "TRENDING_BEAR", "BEAR", "BEAR_TRENDING"):
        return "BEAR"
    # Legacy substring match for any other labels
    if "BULL" in r:
        return "BULL"
    if "BEAR" in r:
        return "BEAR"
    return ""


def lightweight_cycle_check(
    open_positions: list,
    regime: dict,
    all_scored: list,
) -> list:
    """
    Fast in-process check run every scan cycle for all open positions.
    No LLM call. Returns only positions that need action — callers treat
    missing symbols as HOLD (no change).

    Rules by trade_type:
      SCALP — time_in_trade > scalp_max_hold_minutes AND pnl < scalp_min_pnl_pct
               → EXIT: momentum thesis did not fire; free the capital.
      SWING — regime changed since entry (entry_regime != current_regime)
               → REVIEW: queue for full Opus PM review this cycle.
      HOLD  — polar regime flip (BULL→BEAR or BEAR→BULL) since entry
               → REVIEW: macro backdrop has shifted; thesis integrity check required.
    """
    pm_cfg = CONFIG.get("portfolio_manager", {})
    if not pm_cfg.get("enabled", True):
        return []
    if not open_positions:
        return []

    # Prefer session_character (Opus-generated) over mechanical label so that
    # the cycle check compares the same vocabulary as entry_regime.
    current_regime        = regime.get("session_character") or regime.get("regime", "UNKNOWN")
    scalp_max_mins        = pm_cfg.get("scalp_max_hold_minutes", 90)
    scalp_min_pnl         = pm_cfg.get("scalp_min_pnl_pct", 0.003)   # 0.3%
    score_collapse_delta  = pm_cfg.get("cycle_score_collapse_threshold", 10)

    # Build current-score lookup from latest scan results (symbol → score)
    scored_map: dict = {}
    for s in (all_scored or []):
        sym_key = s.get("symbol") or s.get("ticker")
        score_val = s.get("score") or s.get("conviction_score", 0)
        if sym_key:
            try:
                scored_map[sym_key] = float(score_val)
            except (TypeError, ValueError):
                pass

    now_utc = datetime.now(timezone.utc)
    actions = []
    actioned_syms: set = set()

    for pos in open_positions:
        sym           = pos.get("symbol", "")
        trade_type    = pos.get("trade_type", "SCALP")
        entry_price   = pos.get("entry", 0)
        current_price = pos.get("current", entry_price)
        entry_regime  = pos.get("regime", "") or pos.get("entry_regime", "")

        try:
            open_dt   = datetime.fromisoformat(pos.get("open_time", "")).replace(tzinfo=timezone.utc)
            mins_held = (now_utc - open_dt).total_seconds() / 60
        except Exception:
            mins_held = 0

        pnl_pct = ((current_price - entry_price) / entry_price) if entry_price > 0 else 0

        if trade_type == "SCALP":
            if mins_held > scalp_max_mins and pnl_pct < scalp_min_pnl:
                actions.append({
                    "symbol":    sym,
                    "action":    "EXIT",
                    "reasoning": (
                        f"SCALP thesis stale: {mins_held:.0f}m elapsed, "
                        f"pnl={pnl_pct * 100:+.2f}% (target >{scalp_min_pnl * 100:.1f}%) — "
                        "momentum did not materialise; exit to free capital"
                    ),
                })
                actioned_syms.add(sym)

        elif trade_type == "SWING":
            if entry_regime and current_regime and entry_regime != current_regime:
                actions.append({
                    "symbol":    sym,
                    "action":    "REVIEW",
                    "reasoning": (
                        f"SWING regime shifted: entry={entry_regime} → now={current_regime}; "
                        "thesis context changed — full Opus review required"
                    ),
                })
                actioned_syms.add(sym)

        elif trade_type == "HOLD":
            entry_polarity   = _regime_polarity(entry_regime)
            current_polarity = _regime_polarity(current_regime)
            if entry_polarity and current_polarity and entry_polarity != current_polarity:
                actions.append({
                    "symbol":    sym,
                    "action":    "REVIEW",
                    "reasoning": (
                        f"HOLD macro backdrop flipped: entry={entry_regime} → now={current_regime}; "
                        "polar regime shift — thesis integrity check required"
                    ),
                })
                actioned_syms.add(sym)

        # Score collapse check — applies to all trade types not already actioned.
        # If signal quality has materially deteriorated since entry, queue a review
        # before the bracket fires so the exit can be thesis-driven, not price-driven.
        if sym not in actioned_syms and sym in scored_map:
            entry_sc   = pos.get("entry_score") or pos.get("score") or 0
            current_sc = scored_map[sym]
            try:
                drop = float(entry_sc) - float(current_sc)
            except (TypeError, ValueError):
                drop = 0
            if drop >= score_collapse_delta:
                actions.append({
                    "symbol":    sym,
                    "action":    "REVIEW",
                    "reasoning": (
                        f"Signal quality collapsed: entry_score={entry_sc:.0f} → "
                        f"current={current_sc:.0f} (drop={drop:.0f}pts ≥ threshold={score_collapse_delta}); "
                        "original setup may no longer be valid — review thesis before bracket fires"
                    ),
                })

    return actions


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
