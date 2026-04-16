# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  portfolio_manager.py                       ║
# ║   Active portfolio intelligence — thesis drift detection     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Watches open positions for thesis drift. Runs on event triggers,
not on a fixed interval — only fires when information has actually changed.

Trigger sources (checked in bot_trading.py):
  1. pre_market        — once per day before the open
  2. regime_change     — market regime flips
  3. score_collapse    — position's signal score drops 15+ pts from entry
  3b. held_score_rise  — position's score rises 15+ pts AND reaches >=45
                         (symmetric to score_collapse, for ADD consideration)
  4. news_hit          — significant keyword score on a held symbol
  5. earnings_risk     — earnings within 48 hours on a held symbol
  6. cascade           — 2+ positions hit stops in the same session
  7. drawdown          — daily P&L < -1.5% of portfolio

Uses Opus (claude_model_alpha) — same model as Trading Analyst.
Mechanical stops still handle adverse price moves. This agent handles
thesis drift: "does the original story still make sense?"
"""

import logging
from datetime import UTC, datetime

import anthropic

from config import CONFIG
from earnings_calendar import get_earnings_within_hours

log = logging.getLogger("decifer.portfolio_manager")

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    return _client


# Conviction bands mirror the regime thresholds used in the signal engine.
# A band *cross* is a stronger signal than a same-band score change because it
# reflects a qualitative shift in setup strength, not just a number moving.
_CONVICTION_BANDS = (
    (60, "HIGH"),  # >= 60 — very strong conviction
    (45, "STRONG"),  # 45-59
    (30, "STANDARD"),  # 30-44
    (18, "WEAK"),  # 18-29 (paper entry threshold)
    (0, "BELOW_THRESHOLD"),
)


def _conviction_band(score: int | float | None) -> str:
    """Classify a signal score into a named conviction band.

    Used in the PM prompt to annotate entry→current band crosses so Opus sees
    "STANDARD → HIGH" as an explicit upgrade signal, not just "28 → 65".
    """
    try:
        s = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        return "BELOW_THRESHOLD"
    for threshold, label in _CONVICTION_BANDS:
        if s >= threshold:
            return label
    return "BELOW_THRESHOLD"


# ══════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════

_PM_SYSTEM = """You are the Portfolio Manager for Decifer, an autonomous trading system.

Your ONLY job is to review currently open positions and decide whether each should be held, \
trimmed, exited early, or added to. You do NOT enter new trades — that is the Trading Analyst's job.

For each position you receive a rich data block: entry thesis, setup type, pattern, per-dimension \
signal evolution (entry → current) with IC weights marking load-bearing dimensions, regime at entry \
vs. now, P&L, time in trade, news, earnings proximity. You have everything needed to reason. Do not \
ask for more data; synthesize from what you have.

ACTIONS (use exactly one per position):

  HOLD  — No action warranted. Thesis intact, nothing changed materially since entry.
          HOLD is the correct answer most of the time. Do not manufacture action.

  TRIM  — Reduce exposure. Conviction is weakening but the thesis is not broken.
          You decide the percentage.  TRIM_PCT: 25 (barely weakening), 50 (standard half-out),
          or 75 (mostly out, small tracker kept).

  EXIT  — Close the full position immediately. Thesis is broken, or a risk event (regime flip
          against direction, hard news, earnings binary on a SCALP/SWING) makes holding wrong.
          Also EXIT when realized_pnl is materially negative (e.g. >-$500) AND current
          technical signals are not decisively strengthening — large realized losses on a symbol
          are empirical evidence that the thesis keeps failing in practice, even if it looks
          intact on paper. Do not keep re-entering the same broken name.
          NEWS RULE: Absence of news (no signal, silent news dimension) is NOT a reason to
          exit — it is silence, not contradiction. Bear/negative news coverage on a held LONG
          (or bull news on a SHORT) CAN support an EXIT — use judgment on severity.
          SCORE RULE: Entry score is an entry filter only. A low current score does not mean
          the thesis is broken — it means no fresh entry setup exists right now. Exit on thesis
          invalidation, not on score level.

  ADD   — Strengthen the position. Do NOT require a single narrow trigger. Legitimate reasons
          include, non-exhaustively:
            • Signal dimensions strengthening — especially on dimensions that were load-bearing
              at entry (marked with their IC weight in the data block below).
            • Pullback to structural support with thesis intact and core signal holding —
              classic DCA on a pullback is valid WHEN the thesis is not broken and core
              dimensions are not collapsing. Averaging down into a broken thesis is NOT.
            • News catalyst confirming the entry thesis.
            • Rally continuation / breakout confirmation after base-building.
            • Fresh cross-dimension confluence emerging since entry.
          Use your judgment. The code will size the add deterministically using the current
          signal score and the same risk function that sized the original entry — you do NOT
          decide the dollar amount, only whether an ADD is warranted.

TRADE TYPES — each position has a type that sets the review lens. Use them to weight factors,
not as rigid rules:
  SCALP — short-hold, pure technical. Momentum and direction are what matter.
          If momentum has not fired and direction has flipped against you, thesis is stale → EXIT.
          Score level alone is not grounds for exit — only momentum failure and direction flip are.
  SWING — technical entry with backing thesis. Moderate tolerance; news and regime matter.
          Score drift is noise. Regime flip against entry direction is the thesis invalidation signal.
  HOLD  — thesis-driven, longer horizon. Technical noise should be ignored; the thesis line
          in the data block below is what to judge against.

DISAMBIGUATION — "pullback to support" vs "averaging down into a losing trade":
  - Pullback is valid when: core signal dimensions have NOT collapsed (check the per-dimension
    deltas below), the thesis line has not been invalidated by the current price, and the
    regime has not flipped against the direction.
  - It is averaging down (BAD) when: load-bearing dimensions from entry have fallen sharply,
    the thesis invalidation condition is near, or regime has flipped. In that case TRIM or
    EXIT — never ADD.

OUTPUT FORMAT — produce exactly this for every position provided, no exceptions:

SYMBOL: <ticker>
ACTION: HOLD | TRIM | EXIT | ADD
TRIM_PCT: <integer>            (required when ACTION is TRIM — e.g. 25, 50, or 75)
REASON: <tag>: <one sentence explanation>

The REASON must lead with a short snake_case tag that names the dominant factor, followed
by a colon, followed by one sentence of detail. Examples of good tags:
  signal_strengthening, pullback_to_support, news_catalyst_confirms, rally_continuation,
  confluence_emerging, thesis_intact, signal_drift, regime_flip, thesis_broken,
  earnings_risk, stop_protection. You pick the tag — it doesn't have to come from this list.

Omit TRIM_PCT for HOLD, EXIT, and ADD actions. Do NOT output ADD_NOTIONAL — the code sizes
the add itself.

RULES:
- Every position in the input must get an output entry.
- Be decisive. Vague answers ("maybe trim", "consider exiting") are not allowed.
- Do not recommend new symbols or comment on market conditions generally.
- Trust the data block. If the entry thesis, per-dimension deltas, and regime all agree
  that something should happen, act. If they disagree, default to HOLD."""


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
    available_cash: float = 0.0,
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

    # Build score lookup from all_scored. Each scored row carries a composite score
    # and a per-dimension score_breakdown (signals.py:2473). We keep both so the
    # prompt can show Opus the composite delta AND the per-dimension evolution.
    score_map = {s["symbol"]: s.get("score", 0) for s in (all_scored or [])}
    breakdown_map = {s["symbol"]: (s.get("score_breakdown") or {}) for s in (all_scored or [])}

    # Check earnings (stocks only — options and FX have no earnings events)
    stock_syms = [p["symbol"] for p in open_positions if p.get("instrument") not in ("option", "fx")]
    earnings_lookahead = pm_cfg.get("earnings_lookahead_hours", 48)
    earnings_flagged = get_earnings_within_hours(stock_syms, earnings_lookahead)

    # Build position summaries for the prompt
    now_utc = datetime.now(UTC)
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

        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        realized_pnl = p.get("realized_pnl", 0) or 0

        # Days held
        try:
            open_dt = datetime.fromisoformat(p.get("open_time", "")).replace(tzinfo=UTC)
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

        score_line = f"entry_score={entry_score}"
        if current_score is not None:
            delta = current_score - entry_score
            score_line += f" → current={current_score} (delta={delta:+d})"
            # Annotate conviction-band cross (entry vs current band). Helps Opus
            # decide ADD vs HOLD: a cross from STANDARD→HIGH is a materially
            # stronger signal than a same-band rise from 46→58.
            entry_band = _conviction_band(entry_score)
            current_band = _conviction_band(current_score)
            if entry_band != current_band:
                direction_tag = "↑" if current_score > entry_score else "↓"
                score_line += f"  [band {direction_tag} {entry_band}→{current_band}]"
        else:
            score_line += " → current=unscored_today (scanner did not rescore this cycle)"

        earnings_str = f"  *** EARNINGS WITHIN {earnings_lookahead}h ***" if sym in earnings_flagged else ""

        trade_type = p.get("trade_type", "SCALP")
        conviction = p.get("conviction", 0.0)
        setup_type = p.get("setup_type", "")
        pattern_id = p.get("pattern_id", "")
        entry_thesis = (p.get("entry_thesis") or "").strip()

        notional = current_price * qty
        pos_pct = (notional / portfolio_value * 100) if portfolio_value > 0 else 0

        # Per-dimension score evolution (entry signal_scores → current score_breakdown),
        # with IC weights at entry annotating which dimensions were load-bearing. This
        # is the decision surface for ADD/TRIM/EXIT — Opus can see WHERE conviction is
        # rising or collapsing, not just the composite delta.
        entry_dim = p.get("signal_scores") or {}
        current_dim = breakdown_map.get(sym) or {}
        ic_weights = p.get("ic_weights_at_entry") or {}
        dim_lines = []
        # overnight_drift is an entry-only signal — it scores the overnight positioning
        # window and is spent once the trade is open.  Showing its delta (always negative
        # as the session progresses) misleads Opus into treating decay as thesis failure.
        _ENTRY_ONLY_DIMS = {"overnight_drift"}
        if entry_dim or current_dim:
            all_dims = sorted(
                (set(entry_dim.keys()) | set(current_dim.keys())) - _ENTRY_ONLY_DIMS
            )
            for dim in all_dims:
                try:
                    e_val = float(entry_dim.get(dim, 0) or 0)
                    c_val = float(current_dim.get(dim, 0) or 0) if current_dim else None
                except (TypeError, ValueError):
                    continue
                if c_val is None:
                    # Not rescored this cycle — show entry only
                    dim_lines.append(f"    {dim:<18} {e_val:5.1f} → unscored")
                else:
                    delta = c_val - e_val
                    w = ic_weights.get(dim)
                    w_tag = ""
                    try:
                        if w is not None and float(w) > 0:
                            w_tag = f"  [load-bearing w={float(w):.2f}]"
                    except (TypeError, ValueError):
                        pass
                    dim_lines.append(
                        f"    {dim:<18} {e_val:5.1f} → {c_val:5.1f} ({delta:+5.1f}){w_tag}"
                    )

        setup_bits = []
        if setup_type:
            setup_bits.append(f"setup={setup_type}")
        if pattern_id:
            setup_bits.append(f"pattern={pattern_id}")
        setup_line = ("  " + "  ".join(setup_bits)) if setup_bits else ""

        thesis_line = f"\n  THESIS: {entry_thesis}" if entry_thesis else ""
        dim_block = ("\n  DIMENSION EVOLUTION (entry → current):\n" + "\n".join(dim_lines)) if dim_lines else ""

        pos_lines.append(
            f"POSITION: {sym} ({instrument}) {direction}  trade_type={trade_type}  conviction={conviction:.2f}{setup_line}\n"
            f"  entry=${entry_price:.2f} current=${current_price:.2f} "
            f"qty={qty} notional=${notional:,.0f} position_pct={pos_pct:.1f}% pnl={pnl_pct:+.1f}%"
            + (f"  realized_pnl=${realized_pnl:+,.2f}" if realized_pnl != 0 else "")
            + "\n"
            f"  {score_line}\n"
            f"  entry_regime={entry_regime} current_regime={regime_name}\n"
            f"  days_held={days_held}"
            + thesis_line
            + dim_block
            + (f"\n{news_str}" if news_str else "")
            + (f"\n{earnings_str}" if earnings_str else "")
        )

    prompt = f"""REVIEW TRIGGER: {trigger}
REGIME: {regime_name} | VIX={vix:.1f}
PORTFOLIO VALUE: ${portfolio_value:,.2f} | AVAILABLE CASH: ${available_cash:,.0f}

OPEN POSITIONS ({len(open_positions)}):
{chr(10).join(pos_lines)}

Review each position and output SYMBOL / ACTION / TRIM_PCT (if TRIM) / REASON for every one.
Do NOT output ADD_NOTIONAL — if ACTION is ADD, the code sizes it using the same risk function
that sized the original entry. Your job is the verb and the reasoning tag, not the dollar amount."""

    try:
        client = _get_client()
        resp = client.messages.create(
            model=CONFIG.get("claude_model_alpha", "claude-opus-4-6"),
            max_tokens=CONFIG.get("claude_max_tokens_alpha", 4096),
            system=[
                {
                    "type": "text",
                    "text": _PM_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
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
    if r in ("TRENDING_DOWN", "RELIEF_RALLY", "CAPITULATION", "DISTRIBUTION", "TRENDING_BEAR", "BEAR", "BEAR_TRENDING"):
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
      SCALP — direction flipped against entry OR momentum collapsed (was load-bearing, now zero)
               → REVIEW: thesis driver not playing out; Opus review required.
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
    current_regime = regime.get("session_character") or regime.get("regime", "UNKNOWN")
    scalp_max_mins = pm_cfg.get("scalp_max_hold_minutes", 90)
    scalp_min_pnl = pm_cfg.get("scalp_min_pnl_pct", 0.003)  # 0.3%
    scalp_mom_entry_min = pm_cfg.get("scalp_momentum_entry_min", 4)
    scalp_mom_current_max = pm_cfg.get("scalp_momentum_current_max", 1)

    # Build current-score, direction, and momentum lookups from latest scan results
    scored_map: dict = {}
    direction_map: dict = {}
    momentum_map: dict = {}
    for s in all_scored or []:
        sym_key = s.get("symbol") or s.get("ticker")
        if not sym_key:
            continue
        try:
            scored_map[sym_key] = float(s.get("score") or s.get("conviction_score", 0))
        except (TypeError, ValueError):
            pass
        direction_map[sym_key] = s.get("direction", "NEUTRAL")
        momentum_map[sym_key] = float((s.get("score_breakdown") or {}).get("momentum", 0) or 0)

    now_utc = datetime.now(UTC)
    actions = []
    actioned_syms: set = set()

    for pos in open_positions:
        sym = pos.get("symbol", "")
        trade_type = pos.get("trade_type", "SCALP")
        entry_price = pos.get("entry", 0)
        current_price = pos.get("current", entry_price)
        entry_regime = pos.get("regime", "") or pos.get("entry_regime", "")

        try:
            open_dt = datetime.fromisoformat(pos.get("open_time", "")).replace(tzinfo=UTC)
            mins_held = (now_utc - open_dt).total_seconds() / 60
        except Exception:
            mins_held = 0

        pnl_pct = ((current_price - entry_price) / entry_price) if entry_price > 0 else 0

        if trade_type == "SCALP":
            # Timeout: momentum never fired → mechanical exit
            if mins_held > scalp_max_mins and pnl_pct < scalp_min_pnl:
                actions.append(
                    {
                        "symbol": sym,
                        "action": "EXIT",
                        "reasoning": (
                            f"SCALP thesis stale: {mins_held:.0f}m elapsed, "
                            f"pnl={pnl_pct * 100:+.2f}% (target >{scalp_min_pnl * 100:.1f}%) — "
                            "momentum did not materialise; exit to free capital"
                        ),
                    }
                )
                actioned_syms.add(sym)
            else:
                # Thesis check: direction flipped OR momentum collapsed → Opus review
                entry_dir = pos.get("direction", "LONG")
                current_dir = direction_map.get(sym, "NEUTRAL")
                entry_mom = float((pos.get("signal_scores") or {}).get("momentum", 0) or 0)
                current_mom = momentum_map.get(sym, 0)
                dir_flipped = (
                    (entry_dir == "LONG" and current_dir == "SHORT")
                    or (entry_dir == "SHORT" and current_dir == "LONG")
                )
                mom_lost = entry_mom >= scalp_mom_entry_min and current_mom <= scalp_mom_current_max
                if dir_flipped or mom_lost:
                    reason = "direction_flipped" if dir_flipped else "momentum_lost"
                    actions.append(
                        {
                            "symbol": sym,
                            "action": "REVIEW",
                            "reasoning": (
                                f"SCALP {reason}: entry_dir={entry_dir} current_dir={current_dir} "
                                f"entry_mom={entry_mom:.0f} current_mom={current_mom:.0f} — "
                                "thesis driver not playing out; Opus review required"
                            ),
                        }
                    )
                    actioned_syms.add(sym)

        elif trade_type == "SWING":
            if entry_regime and current_regime and entry_regime != current_regime:
                actions.append(
                    {
                        "symbol": sym,
                        "action": "REVIEW",
                        "reasoning": (
                            f"SWING regime shifted: entry={entry_regime} → now={current_regime}; "
                            "thesis context changed — full Opus review required"
                        ),
                    }
                )
                actioned_syms.add(sym)

        elif trade_type == "HOLD":
            entry_polarity = _regime_polarity(entry_regime)
            current_polarity = _regime_polarity(current_regime)
            if entry_polarity and current_polarity and entry_polarity != current_polarity:
                actions.append(
                    {
                        "symbol": sym,
                        "action": "REVIEW",
                        "reasoning": (
                            f"HOLD macro backdrop flipped: entry={entry_regime} → now={current_regime}; "
                            "polar regime shift — thesis integrity check required"
                        ),
                    }
                )
                actioned_syms.add(sym)


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
            action = act_m.group(1)
            entry: dict = {
                "symbol": sym,
                "action": action,
                "reasoning": rea_m.group(1).strip() if rea_m else "",
            }

            # TRIM_PCT — how much of the position to sell (25/50/75)
            if action == "TRIM":
                trim_m = re.search(r"TRIM_PCT:\s*(\d+)", block)
                if trim_m:
                    raw_pct = int(trim_m.group(1))
                    entry["trim_pct"] = max(1, min(raw_pct, 99))
                else:
                    entry["trim_pct"] = 50  # safe default
                    log.warning(f"portfolio_manager: {sym} TRIM missing TRIM_PCT — defaulting 50%")

            # ADD sizing is no longer Opus's responsibility — the bot sizes ADDs
            # deterministically via calculate_position_size() using the current signal
            # score, same risk function that sized the original entry. See
            # bot_trading.py ADD handler. Opus only decides the verb and the reason.

            results[sym] = entry
        elif sym_m:
            raw_act = re.search(r"ACTION:\s*(\S+)", block)
            raw_val = raw_act.group(1) if raw_act else "<missing>"
            log.warning(f"portfolio_manager: {sym_m.group(1)} had unparseable ACTION '{raw_val}' — defaulting HOLD")

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
