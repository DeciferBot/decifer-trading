# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  agents.py                                  ║
# ║   Trading intelligence pipeline                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Architecture (post-redesign):
#   Agent 1 — Technical Analyst     deterministic  (parallel with Trading Analyst)
#   Agent 2 — Trading Analyst       LLM/Opus       (replaces Macro + Opportunity + Devil)
#   Agent 3 — Risk Manager          deterministic
#   Agent 4 — Trade Synthesiser     deterministic
#
# LLM calls per scan: 1 (Trading Analyst, Opus, uncapped tokens)
# LLM calls per trade: 0 (Execution Agent is deterministic — execution_agent.py)
# Portfolio review: event-triggered, separate module (portfolio_manager.py)

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import anthropic

from config import CONFIG

log = logging.getLogger("decifer.agents")
client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])


def _call_claude(system_prompt: str, user_message: str) -> str:
    """Standard Claude call (Sonnet, default token cap)."""
    try:
        resp = client.messages.create(
            model=CONFIG["claude_model"],
            max_tokens=CONFIG["claude_max_tokens"],
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return ""


def _call_claude_alpha(system_prompt: str, user_message: str) -> str:
    """Alpha-agent call — Opus, uncapped tokens, full reasoning depth.
    Used for Trading Analyst and Portfolio Manager only."""
    try:
        resp = client.messages.create(
            model=CONFIG.get("claude_model_alpha", "claude-opus-4-6"),
            max_tokens=CONFIG.get("claude_max_tokens_alpha", 4096),
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"Claude alpha API error: {e}")
        return ""


# ══════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════


def load_weekly_review() -> str:
    """Load most recent weekly review to inject as memory."""
    import os

    review_file = "weekly_review.txt"
    if os.path.exists(review_file):
        try:
            with open(review_file) as f:
                return f.read()[-2000:]
        except Exception:
            pass
    return ""


def run_all_agents(
    signals: list,
    regime: dict,
    news: list,
    fx_data: dict,
    open_positions: list,
    portfolio_value: float,
    daily_pnl: float,
    options_signals: list | None = None,
    strategy_mode: dict | None = None,
    positions_to_reconsider: list | None = None,
    available_cash: float = 0.0,
) -> dict:
    """
    Run agent pipeline and return final decision.

    Pipeline:
      Agent 1 (Technical) + Agent 2 (Trading Analyst/Opus) run in parallel.
      Agent 3 (Risk Manager) runs deterministically.
      Agent 4 (Final Decision) runs deterministically.

    Early-exit gates (no LLM calls):
      - No position slots available
      - No signals above min_score_to_trade threshold
    """
    if strategy_mode is None:
        strategy_mode = {
            "mode": "NORMAL",
            "context": "",
            "size_multiplier": 1.0,
            "score_threshold_adj": 0,
            "regime_changed": False,
        }
    if positions_to_reconsider is None:
        positions_to_reconsider = []

    max_pos = CONFIG["max_positions"]
    open_syms = [p["symbol"] for p in open_positions]
    slots_remaining = max_pos - len(open_positions)

    if slots_remaining <= 0:
        log.info(
            f"Agents: No buy slots ({len(open_positions)}/{max_pos}) — running agents for exit/rotation review only"
        )

    threshold = CONFIG.get("min_score_to_trade", 18)
    qualified = [s for s in signals if s.get("score", 0) >= threshold]
    if not qualified and not positions_to_reconsider:
        log.info("Agents: No signals above threshold — skipping LLM calls")
        _skip_msg = f"No signals above threshold ({threshold} pts) this cycle — agent skipped."
        return {
            "buys": [],
            "sells": [],
            "hold": open_syms,
            "cash": False,
            "agents_agreed": 0,
            "summary": f"No signals >= {threshold} — agents skipped",
            "claude_reasoning": "No qualifying signals this cycle. No LLM agents called.",
            "_agent_outputs": {
                "technical": _skip_msg,
                "trading_analyst": _skip_msg,
                "risk": _skip_msg,
            },
        }

    # ── Fresh-first ordering: unheld candidates surface before already-held ──
    # Agents see a wide signal window. If held symbols dominate the top, the analyst
    # wastes recommendation bandwidth re-proposing names already in the portfolio.
    # Placing fresh candidates first ensures new opportunities are seen and acted on.
    _held_syms_set = set(open_syms)
    fresh_qualified = [s for s in qualified if s["symbol"] not in _held_syms_set]
    held_qualified = [s for s in qualified if s["symbol"] in _held_syms_set]
    ordered_qualified = fresh_qualified + held_qualified  # fresh first, held for context
    n_fresh = len(fresh_qualified)
    log.info(
        f"Agents: {len(qualified)} qualified signals — {n_fresh} fresh, {len(held_qualified)} held "
        f"(fresh-first ordering applied)"
    )

    # ── Agents 1+2: Technical (deterministic) + Trading Analyst (Opus) in parallel ──
    # BC-8: Agent 1 (Technical) evaluates ordered_qualified (all signals, fresh+held).
    # Agent 2 (Trading Analyst/Opus) receives fresh_qualified only — held symbols are
    # already visible in the OPEN POSITIONS block with full entry/P&L detail. Showing
    # them again in the scored signals list causes Opus to double-represent them and
    # cluster ADD recommendations on existing positions rather than evaluating new ones.
    log.info("Agents 1+2: Technical + Trading Analyst/Opus (parallel)...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        tech_future = pool.submit(agent_technical, ordered_qualified, regime)
        analyst_future = pool.submit(
            agent_trading_analyst,
            fresh_qualified,  # BC-8: Opus sees fresh candidates only; held shown in OPEN POSITIONS block
            regime,
            news,
            fx_data,
            options_signals or [],
            strategy_mode,
            portfolio_value,
            daily_pnl,
            open_positions,
            available_cash,
        )
        tech = tech_future.result()
        analyst = analyst_future.result()

    # ── Agent 3: Risk Manager (deterministic) ────────────────────────────────
    log.info("Agent 3: Risk Manager (deterministic)...")
    risk = agent_risk_manager(
        analyst,
        "",
        open_positions,
        portfolio_value,
        daily_pnl,
        regime,
        strategy_mode=strategy_mode,
        signals=ordered_qualified,
    )

    # ── Agent 4: Trade Synthesiser (deterministic) ───────────────────────────
    log.info("Agent 4: Trade Synthesiser (deterministic)...")
    final = agent_final_decision(
        tech,
        analyst,
        analyst,  # macro=analyst, opportunity=analyst (same source)
        "",  # devils="" (removed)
        risk,
        ordered_qualified,
        open_positions,
        regime,
        CONFIG["agents_required_to_agree"],
        weekly_memory=load_weekly_review(),
        strategy_mode=strategy_mode,
        positions_to_reconsider=positions_to_reconsider,
        portfolio_value=portfolio_value,
        daily_pnl=daily_pnl,
        options_signals=options_signals,  # BC-1: pass validated contracts for instrument gate
    )

    final["_agent_outputs"] = {
        "technical": tech,
        "trading_analyst": analyst,
        "risk": risk,
    }

    return final


# ══════════════════════════════════════════════════════════════
# AGENT 4 — TRADE SYNTHESISER  (deterministic)
# ══════════════════════════════════════════════════════════════


def agent_final_decision(
    technical: str,
    macro: str,
    opportunity: str,
    devils: str,
    risk: str,
    signals: list,
    open_positions: list,
    regime: dict,
    agents_required: int,
    weekly_memory: str = "",
    strategy_mode: dict | None = None,
    positions_to_reconsider: list | None = None,
    portfolio_value: float = 0.0,
    daily_pnl: float = 0.0,
    options_signals: list | None = None,
) -> dict:
    """
    Deterministic final synthesis — vote counting + JSON assembly.
    `macro` and `opportunity` are both the Trading Analyst output.
    `devils` is empty string (Devil's Advocate removed).
    """
    from position_sizing import calculate_stops
    from risk import calculate_position_size

    _sm = strategy_mode or {}
    size_mult = _sm.get("size_multiplier", 1.0)
    _reconsider = positions_to_reconsider or []
    # BC-1: build the set of symbols that have a validated options contract.
    # Only symbols present in options_signals passed a get_contract() check upstream.
    # Any symbol Opus labels as CALL/PUT that isn't in this set has no viable contract —
    # downgrade to stock rather than letting a doomed order reach execution.
    _option_valid_syms: set[str] = {o["symbol"] for o in (options_signals or [])}

    if regime.get("regime") == "CAPITULATION":
        return {
            "buys": [],
            "sells": [],
            "hold": [],
            "cash": True,
            "agents_agreed": 0,
            "summary": f"CAPITULATION -- VIX={regime.get('vix', '?')}, all cash",
            "claude_reasoning": "Regime is CAPITULATION. No new trades. Capital preservation mode.",
        }

    proposed = _extract_proposed_symbols(opportunity, signals)
    macro_vote = _extract_macro_vote(macro)

    open_syms = [p["symbol"] for p in open_positions]
    open_syms_set = set(open_syms)
    buys = []

    for s in proposed:
        sym = s["symbol"]
        direction = "LONG" if s.get("direction", "LONG") == "LONG" else "SHORT"

        # Hard gate: already held — a new entry doubles the position, doesn't add alpha.
        # The PM pipeline handles exits and trims on existing positions.
        if sym in open_syms_set:
            log.debug(f"Final: {sym} skipped — already held (PM handles existing positions)")
            continue

        # Hard gate: reject if agent direction contradicts scanner direction.
        # A mismatch means the LLM latched onto bullish/bearish indicators and
        # flipped the direction — executing both trades would immediately close one.
        scanner_signal = s.get("signal", "")
        if scanner_signal in ("BUY", "STRONG_BUY"):
            scanner_dir = "LONG"
        elif scanner_signal in ("SELL", "STRONG_SELL"):
            scanner_dir = "SHORT"
        else:
            scanner_dir = None  # HOLD/NEUTRAL — no hard constraint

        if scanner_dir and direction != scanner_dir:
            log.warning(
                f"Final: {sym} skipped — agent({direction}) contradicts scanner({scanner_dir}). "
                f"Thesis would justify {direction} but execution would go {scanner_dir}."
            )
            continue

        # Votes: Technical (+1) + Macro (+1/0) + Opportunity (+1) + Risk (+1/-1)
        # Macro is direction-aware: BULLISH helps LONGs, BEARISH helps SHORTs.
        # max(0, ...) means the off-direction macro is neutral (no penalty), not a blocker.
        votes = 0
        votes += _extract_technical_conviction(technical, sym)
        votes += max(0, macro_vote if direction == "LONG" else -macro_vote)
        votes += 1  # Trading Analyst proposed this symbol
        votes += _extract_risk_approval(risk, sym)

        if votes < agents_required:
            log.debug(f"Final: {sym} skipped -- {votes} votes < {agents_required} required")
            continue

        # No slot cap. The cash floor (min_cash_reserve), per-trade risk sizing
        # (risk_pct_per_trade), correlation gate, and sector cap are the actual
        # risk controls — a count of positions is a redundant blunt proxy for these.
        # In RECOVERY mode, size_multiplier (0.5x) already reduces exposure; no
        # trade count throttle needed on top.

        price = s.get("price", 0)
        atr = s.get("atr", price * 0.02 if price > 0 else 1.0)
        score = s.get("score", 20)

        if price > 0 and portfolio_value > 0:
            qty = calculate_position_size(portfolio_value, price, score, regime, atr=atr)
            qty = max(1, int(qty * size_mult))
            sl, tp = calculate_stops(price, atr, direction)
        else:
            qty, sl, tp = 1, 0.0, 0.0

        reasoning = _extract_opportunity_reasoning(opportunity, sym) or f"{sym} selected by Trading Analyst"
        instrument = _extract_instrument(opportunity, sym)

        # BC-1: Hard gate — Opus recommended an options instrument but no validated
        # contract exists in options_signals. Force stock rather than let this reach
        # orders_core where get_contract() will silently fail.
        if instrument in ("call", "put") and sym not in _option_valid_syms:
            log.info(
                f"BC-1: {sym} instrument downgraded {instrument}→stock "
                f"(no validated contract in options_signals)"
            )
            instrument = "stock"

        buys.append(
            {
                "symbol": sym,
                "direction": direction,
                "qty": qty,
                "sl": sl,
                "tp": tp,
                "instrument": instrument,
                "reasoning": reasoning,
            }
        )
        pass  # no slot counter — cash floor governs deployment

    sells = [p["symbol"] for p in _reconsider if p.get("reason", "").upper() != "HOLD" and p["symbol"] in open_syms]

    hold = [sym for sym in open_syms if sym not in sells]

    if buys:
        first_sym = buys[0]["symbol"]
        first_dir = buys[0].get("direction", "LONG")
        agents_agreed = max(
            0,
            min(
                4,
                _extract_technical_conviction(technical, first_sym)
                + max(0, macro_vote if first_dir == "LONG" else -macro_vote)
                + 1
                + _extract_risk_approval(risk, first_sym),
            ),
        )
    else:
        agents_agreed = 0

    regime_label = regime.get("regime", "UNKNOWN")
    macro_str = "bullish" if macro_vote > 0 else ("bearish" if macro_vote < 0 else "neutral")

    if buys:
        buy_syms = ", ".join(b["symbol"] for b in buys)
        summary = f"{regime_label} -- {macro_str} macro, entering {buy_syms}"
        first_r = buys[0].get("reasoning", "")[:100]
        claude_reasoning = (
            f"Entering {buys[0]['symbol']}: {first_r}. "
            f"{agents_agreed} agents aligned. "
            f"Risk gate open, {len(open_positions)} positions held."
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
# PRIVATE HELPERS — deterministic vote parsing
# ══════════════════════════════════════════════════════════════


def _extract_proposed_symbols(opportunity_text: str, signals: list) -> list:
    """Return signal dicts for symbols proposed in analyst text, in mention order."""
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
    # No count cap on proposals. Downstream risk gates (cash floor, single-position
    # cap, sector cap, correlation, Risk Manager veto) govern what actually executes.
    return result


def _extract_macro_vote(analyst_text: str) -> int:
    """+1 BULLISH | -1 BEARISH | 0 NEUTRAL/UNCERTAIN. Parses Trading Analyst output."""
    if not analyst_text:
        return 0
    m = re.search(r"MACRO:\s*(BULLISH|BEARISH|NEUTRAL|UNCERTAIN)", analyst_text.upper())
    if m:
        verdict = m.group(1)
        if verdict == "BULLISH":
            return 1
        if verdict == "BEARISH":
            return -1
        return 0  # NEUTRAL or UNCERTAIN
    upper = analyst_text.upper()
    if any(kw in upper for kw in ("BULLISH", "RISK-ON", "RISK ON")):
        return 1
    if any(kw in upper for kw in ("BEARISH", "RISK-OFF", "RISK OFF")):
        return -1
    return 0


def _extract_technical_conviction(tech_text: str, symbol: str) -> int:
    """+1 if technical report rated this symbol HIGH conviction, else 0."""
    if not tech_text:
        return 0
    upper = tech_text.upper()
    idx = upper.find(symbol)
    while idx != -1:
        window = upper[max(0, idx - 20) : idx + 120]
        if "HIGH" in window:
            return 1
        idx = upper.find(symbol, idx + 1)
    return 0


def _extract_risk_approval(risk_text: str, symbol: str) -> int:
    """+1 APPROVE | -1 REJECT/BLOCK | 0 default when symbol not mentioned.

    BC-4: Default changed from +1 to 0. Returning +1 when the Risk Manager text
    simply doesn't mention a symbol silently bypasses the veto — a symbol that
    was never evaluated by the Risk Manager would receive a free approval vote,
    allowing Opus proposals to exceed the approved capital pool. A 0 (neutral,
    not voted) is the correct interpretation of absence.
    """
    if not risk_text:
        return 1
    upper = risk_text.upper()
    if "ALL TRADES: REJECT" in upper or "RISK GATE: CLOSED" in upper:
        return -1
    idx = upper.find(symbol)
    while idx != -1:
        window = upper[idx : idx + 300]
        if "APPROVE" in window:
            return 1
        if "REJECT" in window or "BLOCK" in window:
            return -1
        idx = upper.find(symbol, idx + len(symbol))
    # BC-4: symbol not found in Risk Manager output → neutral (0), not approved (+1).
    # The Risk Manager never evaluated this symbol; it cannot be treated as approved.
    return 0


def _extract_opportunity_reasoning(opportunity_text: str, symbol: str) -> str:
    """Pull the entry rationale for a symbol from the analyst report."""
    if not opportunity_text:
        return ""
    idx = opportunity_text.upper().find(symbol)
    if idx == -1:
        return ""
    section = opportunity_text[idx : idx + 600]
    m = re.search(r"RATIONALE[:\s]+(.+?)(?:\n[0-9A-Z]|\Z)", section, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:200]
    for line in section.split("\n")[1:5]:
        line = line.strip()
        if len(line) > 20 and not line.upper().startswith("SYMBOL"):
            return line[:200]
    return ""


def _extract_instrument(opportunity_text: str, symbol: str) -> str:
    """Derive suggested instrument from analyst text."""
    if not opportunity_text:
        return "stock"
    idx = opportunity_text.upper().find(symbol)
    section = opportunity_text[idx : idx + 500].upper() if idx != -1 else ""
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
# AGENT 1 — TECHNICAL ANALYST  (deterministic)
# ══════════════════════════════════════════════════════════════


def agent_technical(signals: list, regime: dict) -> str:
    """Deterministic technical analysis report built from pre-computed signal data."""
    if not signals:
        return "No symbols above scoring threshold. No technical setups."

    lines = [
        f"TECHNICAL ANALYSIS — {min(len(signals), 15)} symbols above threshold",
        (
            f"Market: VIX={regime['vix']:.1f} | SPY=${regime['spy_price']} "
            f"({'above' if regime.get('spy_above_200d', regime.get('spy_above_ema')) else 'below'} 200d MA)"
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
            "Donchian=BREAKOUT_HIGH" if donch == 1 else "Donchian=BREAKOUT_LOW" if donch == -1 else "Donchian=inside"
        )
        tf_agree = "/".join(
            [
                tf5.get("signal", "?"),
                tf1d.get("signal", "N/A") if tf1d else "N/A",
                tf1w.get("signal", "N/A") if tf1w else "N/A",
            ]
        )

        lines.append(
            f"[{conviction}] {sym}: ${s['price']} | Score={score}pt | {sig} | "
            f"ADX={adx:.0f} | MFI={mfi:.0f} | EMA={ema_str} | {sq_str} | "
            f"Vol={vol_ratio:.1f}x | VWAP_dist={vwap_dist:+.2f}% | {donch_str} | "
            f"OBV={'UP' if obv_slope > 0 else 'DOWN' if obv_slope < 0 else 'FLAT'} | "
            f"TF={tf_agree} | Dims={aligned_dims} aligned"
        )

        news = s.get("news") or {}
        kw = news.get("keyword_score", 0)
        sent = news.get("claude_sentiment", "")
        cat = news.get("claude_catalyst", "")
        if sent and kw != 0:
            lines.append(f"  -> News: {sent} (kw={kw:+d})" + (f" | {cat[:80]}" if cat else ""))

        if sig == "BUY" and obv_slope < 0 and mfi < 40:
            divergences.append(f"  ! {sym}: DISTRIBUTION TRAP -- BUY signal but OBV falling + MFI={mfi:.0f}")
        elif sig == "BUY" and mfi < 35:
            divergences.append(f"  ! {sym}: MFI DIVERGENCE -- BUY signal but MFI={mfi:.0f} (weak institutional flow)")
        elif donch == 1 and vol_ratio < 1.5:
            divergences.append(
                f"  ! {sym}: LOW-VOLUME BREAKOUT -- Donchian breach but Vol={vol_ratio:.1f}x (suspect fakeout)"
            )
        vwap_sd_pct = tf5.get("vwap_sd_pct", 1.0)
        sd_threshold = 2.0 * vwap_sd_pct
        regime_name = regime.get("regime", "")
        if score >= 45 and regime_name in ("RANGE_BOUND", "CHOPPY") and abs(vwap_dist) >= sd_threshold:
            direction_word = "SHORT" if sig in ("SELL", "STRONG_SELL") else "LONG"
            contra = "LONG" if direction_word == "SHORT" else "SHORT"
            divergences.append(
                f"  ! {sym}: OVEREXTENDED IN RANGE -- score={score}pt + "
                f"VWAP={vwap_dist:+.1f}% ({abs(vwap_dist) / vwap_sd_pct:.1f}x SD, threshold=2.0x) "
                f"in {regime_name}. Move already expressed — mean reversion {contra} "
                f"more probable than {direction_word} continuation."
            )

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
# AGENT 2 — TRADING ANALYST  (LLM / Opus / uncapped)
# Single call replacing Macro + Opportunity + Devil's Advocate chain.
# Sees all inputs simultaneously — no anchoring from prior agent outputs.
# ══════════════════════════════════════════════════════════════

_TRADING_ANALYST_SYSTEM = """You are the Trading Analyst for Decifer, an autonomous US equity trading system.

Your job is to simultaneously assess the macro environment AND identify the best trading opportunities. \
You receive all data in one pass — you are not reading summaries from other agents.

OUTPUT FORMAT — produce exactly these sections in this order:

MACRO: [BULLISH | BEARISH | NEUTRAL | UNCERTAIN]
<2-3 sentences on the macro environment: regime, VIX trajectory, cross-asset signals, risk-on/off verdict>

OPPORTUNITIES:
Emit one SYMBOL block per opportunity. Recommend as many or as few as the signals, regime, and account state justify — there is no fixed count. Calibrate against the ACCOUNT block (slots_remaining, available_cash, day_pnl): if slots are scarce or cash is tight, be selective; if the regime is TRENDING and multiple strong signals align, propose broadly. Cash is a valid output when conviction is absent.

SYMBOL: <ticker>
DIRECTION: LONG | SHORT
CONVICTION: HIGH | MEDIUM | LOW
RATIONALE: <Lead with the real-world reason — catalyst, sector move, earnings, breakout from base. \
Do NOT lead with indicator values. A human must understand the trade without knowing what ADX means.>
INSTRUMENT: stock | call | put
  (prefer call/put when: options_score > 20 AND IVR < 30% AND stock also signals)
COUNTER-ARGUMENT: <The single strongest reason this trade fails. Be honest. One sentence.>
KEY RISK: <Binary events, crowding, macro headwind>

RULES:
- Do not self-impose a count cap. Operational constraints (cash floor 10%, single-position cap 6%, sector cap 40%, correlation, cooldown, Risk Manager veto) are enforced downstream — you do not need to anticipate them. Your job is conviction-weighted recommendations; the system filters by risk.
- Cash is a valid output when conviction is absent. Quality bar is CONVICTION and the min_score_to_trade floor, not trade count.
- Options flow: CALL_BUYER = smart money bullish. PUT_BUYER = smart money bearish. Low IVR = cheap premium.
- In CAPITULATION regime: output MACRO: BEARISH and no OPPORTUNITIES. Capital preservation.
- RANGE_BOUND regime: raise your conviction bar — only HIGH conviction setups.
- TRENDING_DOWN regime: you MUST evaluate SHORT setups. If any SELL or STRONG_SELL signals appear in the scored list, include at least one SHORT opportunity. Do not default to cash or LONGs only — name the short candidates explicitly with DIRECTION: SHORT.
- RELIEF_RALLY regime: treat as a bear-market bounce — favour shorts on failed breakouts, be sceptical of longs.
- TRENDING_UP regime: trade with the trend — LONGs are the primary opportunity. Shorts require a specific negative catalyst (earnings miss, sector breakdown, confirmed insider selling) — a bearish signal score alone is not sufficient. If the only bearish evidence is momentum or breakout signals, output CASH not SHORT.
- You determine direction from first principles using all data provided. SELL/STRONG_SELL signals are SHORT candidates; BUY/STRONG_BUY are LONG candidates — but these are starting points, not instructions. If price structure contradicts the signal direction, trust your read and either flip or omit the symbol.
- Vol=Xx ADV means today's volume vs the 20-day average. Vol>2x = unusual conviction. Vol<0.5x = low participation, treat setups with scepticism. VWAP=+/-X% = how far price is from today's VWAP. A short candidate already sitting far below VWAP in a RANGE_BOUND or CHOPPY regime is a mean-reversion long setup, not a short continuation — recognise this and act accordingly.
- Keep each section tight. No padding."""


def agent_trading_analyst(
    signals: list,
    regime: dict,
    news_headlines: list,
    fx_data: dict,
    options_signals: list,
    strategy_mode: dict,
    portfolio_value: float = 0.0,
    daily_pnl: float = 0.0,
    open_positions: list | None = None,
    available_cash: float = 0.0,
) -> str:
    """
    Single Opus LLM call replacing Macro Analyst + Opportunity Finder + Devil's Advocate.
    Receives all inputs simultaneously — eliminates anchoring bias from the old serial chain.
    """
    regime_name = regime.get("regime", "UNKNOWN")
    vix = regime.get("vix", 0)
    vix_1h = regime.get("vix_1h_change", 0)
    spy = regime.get("spy_price", "?")
    qqq = regime.get("qqq_price", "?")
    size_mult = regime.get("position_size_multiplier", 1.0)
    spy_chg_1d = regime.get("spy_chg_1d", 0.0)
    qqq_chg_1d = regime.get("qqq_chg_1d", 0.0)
    iwm_chg_1d = regime.get("iwm_chg_1d", 0.0)
    tape_context = regime.get("tape_context", f"REGIME: {regime_name}")

    sig_lines = []
    # Wide input window — let Opus pick from a meaningful candidate set. Token cost
    # is trivial (≈40 tokens/row) against the 200K context window.
    for s in signals[:50]:
        sym = s["symbol"]
        score = s["score"]
        sig = s.get("signal", "?")
        bd = s.get("score_breakdown", {})
        bd_str = " ".join(f"{k[:3]}={v:.0f}" for k, v in bd.items() if v > 0)
        sig_news = s.get("news") or {}
        kw = sig_news.get("keyword_score", 0)
        sent = sig_news.get("claude_sentiment", "")
        news_str = f" | news={sent}(kw={kw:+d})" if sent else (f" | kw={kw:+d}" if kw else "")
        vol_ratio = s.get("vol_ratio") or s.get("timeframes", {}).get("5m", {}).get("vol_ratio", 1.0)
        vwap_dist = s.get("timeframes", {}).get("5m", {}).get("vwap_dist", 0)
        price = s.get("price", 0)
        atr = s.get("atr", 0)
        price_str = f" | ${price:.2f}" if price > 0 else ""
        atr_str = f" atr=${atr:.2f} ({atr/price*100:.1f}%)" if price > 0 and atr > 0 else ""
        sig_lines.append(
            f"  {sym}: {score}pt {sig} [{bd_str}]{news_str} | Vol={vol_ratio:.1f}x ADV | VWAP={vwap_dist:+.1f}%{price_str}{atr_str}"
        )

    opts_lines = []
    for o in (options_signals or [])[:8]:
        ivr_str = f"IVR={o['iv_rank']:.0f}%" if o.get("iv_rank") is not None else "IVR=n/a"
        earn_str = f" earnings_in={o['earnings_days']}d" if o.get("earnings_days") else ""
        opts_lines.append(
            f"  [{o['options_score']:>2}/30] {o['signal']:<14} {o['symbol']:<6} "
            f"${o['price']:.2f} C/P={o['cp_ratio']:.1f}x {ivr_str} {o['dte']}DTE{earn_str}"
        )

    fx_lines = [f"  {pair}: {d.get('price', '?')} ({d.get('change_pct', '?')}%)" for pair, d in (fx_data or {}).items()]

    hl_lines = [f"  - {h}" for h in (news_headlines or [])[:5]]
    sm_ctx = (strategy_mode or {}).get("context", "")

    overnight_block = ""
    try:
        from overnight_research import load_overnight_notes
        import os as _os_ovn

        # RB-8: Only inject notes when the sentinel confirms this morning's research
        # thread completed. Without this check, a slow FMP API call causes the first
        # 9:30 scan to silently inject yesterday's notes — stale data passed to Opus
        # as current market context.
        _sentinel = _os_ovn.path.join(
            _os_ovn.path.dirname(_os_ovn.path.abspath(__file__)), "data", "overnight_notes.done"
        )
        if _os_ovn.path.exists(_sentinel):
            notes = load_overnight_notes()
            if notes:
                overnight_block = f"\nOVERNIGHT RESEARCH NOTES:\n{notes}\n"
        else:
            log.debug("Overnight research sentinel absent — skipping note injection (thread not yet complete or no run today)")
    except Exception:
        pass

    voice_block = ""
    try:
        import os as _os
        _vm = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "voice_memos.md")
        if _os.path.exists(_vm):
            _vm_text = open(_vm).read().strip()
            if _vm_text:
                voice_block = f"\nUSER VOICE NOTES (written today by the trader):\n{_vm_text}\n"
    except Exception:
        pass

    # ── Catalyst candidates block ─────────────────────────────────────────────
    # Cross-reference scored signals against the M&A catalyst screener.
    # Only show candidates that appear in the current signal set so the block
    # stays tight and Opus doesn't get distracted by irrelevant tickers.
    catalyst_block = ""
    try:
        from signals import _get_catalyst_lookup
        from config import CATALYST_DIR
        import json as _json

        cat_lookup = _get_catalyst_lookup()  # {ticker: catalyst_score} for score >= threshold
        if cat_lookup:
            # Also pull full candidate record for richer context
            files = sorted(CATALYST_DIR.glob("candidates_*.json"), reverse=True)
            full_candidates: dict[str, dict] = {}
            if files:
                raw = _json.loads(files[0].read_text())
                full_candidates = {c["ticker"]: c for c in raw.get("candidates", [])}

            signal_syms = {s["symbol"] for s in signals[:50]}
            hits = [t for t in cat_lookup if t in signal_syms]
            if hits:
                lines = []
                for t in sorted(hits, key=lambda x: -cat_lookup[x]):
                    c = full_candidates.get(t, {})
                    score = cat_lookup[t]
                    f = c.get("fundamental_score", "?")
                    o = c.get("options_anomaly_score", "?")
                    e = c.get("edgar_score", "?")
                    s_sent = c.get("sentiment_score", "?")
                    flags = "; ".join(
                        (c.get("options_anomaly_flags") or [])[:2]
                        + (c.get("edgar_events") or [])[:1]
                    )
                    lines.append(
                        f"  {t}: catalyst={score:.1f}/10 | F={f}/5 O={o}/10 E={e}/10 S={s_sent}/10"
                        + (f" | {flags}" if flags else "")
                    )
                catalyst_block = (
                    "\nCATALYST CANDIDATES (M&A screener — high-conviction setups "
                    "with fundamental + options anomaly + EDGAR signals):\n"
                    + "\n".join(lines)
                    + "\nIMPORTANT — AVOID DOUBLE-WEIGHTING: Each ticker's score in the "
                    "SCORED SIGNALS list above already includes a catalyst boost applied by "
                    "the signal engine. Do NOT treat the elevated score as organic technical "
                    "strength AND also treat the catalyst flag as additional confirmation — "
                    "that is double-counting the same signal. Evaluate these names on the "
                    "underlying catalyst quality (options anomaly, EDGAR event, earnings "
                    "surprise) independently of the inflated score.\n"
                )
    except Exception:
        pass

    # ── Account state block ───────────────────────────────────────────────────
    max_pos = CONFIG["max_positions"]
    slots_used = len(open_positions or [])
    slots_remaining = max_pos - slots_used
    daily_budget_left = (portfolio_value * CONFIG.get("daily_loss_limit", 0.10)) + min(0, daily_pnl)
    pnl_pct = (daily_pnl / portfolio_value * 100) if portfolio_value > 0 else 0
    account_block = ""
    if portfolio_value > 0:
        account_block = (
            f"ACCOUNT: portfolio=${portfolio_value:,.0f} | cash=${available_cash:,.0f} "
            f"| day_pnl=${daily_pnl:+,.0f} ({pnl_pct:+.2f}%) | budget_left=${daily_budget_left:,.0f} "
            f"| positions={slots_used}/{max_pos} ({slots_remaining} slots open)\n"
        )

    # ── Open position detail ──────────────────────────────────────────────────
    held_block = ""
    if open_positions:
        pos_lines = []
        for p in open_positions:
            sym = p.get("symbol", "?")
            direction = p.get("direction", "LONG")
            entry = p.get("entry", 0)
            current = p.get("current", entry)
            qty = p.get("qty", 0)
            notional = current * qty
            pnl_p = ((current - entry) / entry * 100) if entry > 0 else 0
            if direction == "SHORT":
                pnl_p = -pnl_p
            trade_type = p.get("trade_type", "SCALP")
            pos_lines.append(
                f"  {sym}: {direction} {trade_type} | entry=${entry:.2f} now=${current:.2f} "
                f"qty={qty} notional=${notional:,.0f} pnl={pnl_p:+.1f}%"
            )
        held_block = (
            "\nOPEN POSITIONS (do NOT recommend new entries for these symbols):\n"
            + "\n".join(pos_lines)
            + "\n"
        )

    prompt = f"""MARKET: {tape_context} | VIX={vix:.1f} ({vix_1h:+.1f}%/1h) | size_mult={size_mult:.1f}x
SPY=${spy} ({spy_chg_1d:+.1f}% today) | QQQ=${qqq} ({qqq_chg_1d:+.1f}% today) | IWM {iwm_chg_1d:+.1f}%
{account_block}{overnight_block}{voice_block}{catalyst_block}{held_block}
SCORED SIGNALS — fresh candidates first (NOT already held):
{chr(10).join(sig_lines) or "  None above threshold"}

OPTIONS FLOW:
{chr(10).join(opts_lines) or "  No options data"}

FX MARKETS:
{chr(10).join(fx_lines) or "  No FX data"}

RECENT HEADLINES (top 5):
{chr(10).join(hl_lines) or "  No headlines"}
{(chr(10) + "TRADING CONTEXT: " + sm_ctx) if sm_ctx else ""}

Produce your analysis now."""

    return _call_claude_alpha(_TRADING_ANALYST_SYSTEM, prompt)


# ══════════════════════════════════════════════════════════════
# AGENT 3 — RISK MANAGER  (deterministic)
# ══════════════════════════════════════════════════════════════


def agent_risk_manager(
    opportunity_report: str,
    devils_report: str,
    open_positions: list,
    portfolio_value: float,
    daily_pnl: float,
    regime: dict,
    strategy_mode: dict | None = None,
    signals: list | None = None,
) -> str:
    """
    Deterministic risk assessment via risk.py functions.
    `devils_report` retained for API compatibility (unused — Devil's Advocate removed).
    """
    from position_sizing import calculate_stops
    from risk import calculate_position_size, check_risk_conditions

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

    gate_ok, gate_reason = check_risk_conditions(portfolio_value, daily_pnl, regime, open_positions)

    if not gate_ok:
        lines.append(f"RISK GATE: CLOSED -- {gate_reason}")
        lines.append("ALL TRADES: REJECT")
        return "\n".join(lines)

    if slots_remaining <= 0:
        lines.append(f"RISK GATE: CLOSED -- No position slots remaining ({len(open_positions)}/{max_pos})")
        lines.append("ALL TRADES: REJECT")
        return "\n".join(lines)

    lines.append(f"RISK GATE: OPEN -- {gate_reason}")
    lines.append("")

    # Risk Manager is deterministic (no LLM cost) — size every qualifying candidate.
    # min_score_to_trade already bounds the list upstream.
    candidates = signals or []
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
        sl, tp = calculate_stops(price, atr, direction)

        lines.append(f"{sym}:")
        lines.append("  DECISION: APPROVE")
        lines.append(f"  SIZE: {qty} shares")
        lines.append(f"  STOP LOSS: ${sl:.2f}")
        lines.append(f"  TAKE PROFIT: ${tp:.2f}")
        lines.append(
            f"  REASON: Score={score} | "
            f"regime_mult={regime.get('position_size_multiplier', 1.0):.1f}x | "
            f"size_mult={size_mult:.1f}x"
        )

    if not candidates:
        lines.append("No per-symbol signal data provided -- sizing deferred to Final Decision.")

    return "\n".join(lines)
