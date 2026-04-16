# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  market_intelligence.py                    ║
# ║   Intelligence layer. Reads the market, reasons freely,     ║
# ║   classifies every signal before the dispatcher touches it. ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Single responsibility: classify every incoming signal with a trade_type
(SCALP | SWING | HOLD | AVOID) and an evidence-based conviction score
before any order is placed.

Two-tier design — always produces a classification:
  Tier 1 — Opus: reads cross-asset observations, pattern library, news,
            macro calendar, and the scored signal candidates. Reasons
            freely — no regime labels imposed. Returns structured JSON
            classification per signal.
  Tier 2 — Evidence fallback: if Opus fails or times out, derives
            trade_type mechanically from signal score and market context.
            Dumber but always fires.

Session context (market read, patterns, news) is cached for
intelligence_cache_minutes (default 30). Per-scan signal classification
runs each scan using the cached context plus current candidates.

News cadence:
  - First call of session: full summaries (last 4h)
  - Subsequent calls within cache: new headlines only
  - Significance threshold crossed: force full-summary refresh
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anthropic

from config import CONFIG
from macro_calendar import _ALL_EVENTS, get_next_event  # type: ignore[attr-defined]
from market_observer import MarketObservation, get_market_observation, invalidate_cache
from pattern_library import get_relevant_patterns, get_setup_performance, get_thesis_performance

log = logging.getLogger("decifer.intelligence")

# ── Session character vocabulary ──────────────────────────────────────────────
# Opus picks one of these to describe the market session.  Stored as entry_regime
# on every new position — replaces the mechanical BULL_TRENDING/CHOPPY labels.
SESSION_CHARACTER_VOCAB = {
    "MOMENTUM_BULL",  # strong uptrend, VIX low, broad participation
    "RELIEF_RALLY",  # bouncing from recent selloff, VIX declining but still elevated
    "FEAR_ELEVATED",  # VIX rising, cautious but not extreme
    "DISTRIBUTION",  # selling pressure, SPY declining or losing breadth
    "TRENDING_BEAR",  # sustained downtrend, shorts working
}
_DEFAULT_SESSION_CHARACTER = "FEAR_ELEVATED"  # conservative fallback if Opus omits

# ── Significance keywords — trigger full news refresh if seen in headlines ──
_SIGNIFICANCE_KEYWORDS = {
    "fed",
    "fomc",
    "rate",
    "hike",
    "cut",
    "cpi",
    "nfp",
    "payroll",
    "earnings",
    "guidance",
    "miss",
    "beat",
    "downgrade",
    "upgrade",
    "halt",
    "bankruptcy",
    "merger",
    "acquisition",
    "tariff",
    "sanction",
    "default",
    "recession",
    "gdp",
    "inflation",
    "surprise",
    # Geopolitical — unscheduled overnight events that move markets
    "ceasefire",
    "peace deal",
    "peace agreement",
    "war",
    "invasion",
    "military",
    "conflict",
    "nuclear",
    "sanctions lifted",
    "trade deal",
    "trade agreement",
    "embargo",
    "coup",
    "assassination",
}

# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class SignalClassification:
    symbol: str
    trade_type: str  # INTRADAY | SWING | POSITION | AVOID
    conviction: float  # 0.0–1.0, evidence-based — not LLM self-reported confidence
    reasoning: str  # Opus rationale (logged only, never acted upon mechanically)
    source: str  # "opus" | "fallback"


@dataclass
class SessionContext:
    """Cached per intelligence_cache_minutes. Rebuilt on expiry."""

    timestamp: str
    market_read: str  # Opus free-form market interpretation
    observation: MarketObservation
    news_mode: str  # "full" | "headlines"
    news_text: str  # formatted for prompt inclusion
    pattern_text: str  # formatted recent patterns for prompt inclusion
    macro_text: str  # upcoming macro events
    thesis_perf_text: str = ""  # formatted thesis performance for prompt inclusion
    setup_perf_text: str = ""  # formatted setup-type edge data for prompt inclusion
    overnight_text: str = ""  # overnight research notes (pre-market tone, calendar, yesterday)


# ── Cache ─────────────────────────────────────────────────────────────────────

_ctx_lock = threading.Lock()
_session_ctx: SessionContext | None = None
_ctx_time: datetime | None = None
_session_open_done: bool = False  # tracks whether first-call full news ran
_last_session_character: str = _DEFAULT_SESSION_CHARACTER


def _context_valid() -> bool:
    if _session_ctx is None or _ctx_time is None:
        return False
    ttl = timedelta(minutes=CONFIG.get("intelligence_cache_minutes", 30))
    return (datetime.now(UTC) - _ctx_time) < ttl


def invalidate_session_context() -> None:
    """Force context rebuild on next classify call."""
    global _session_ctx, _ctx_time, _session_open_done
    with _ctx_lock:
        _session_ctx = None
        _ctx_time = None
        _session_open_done = False
    invalidate_cache()


# ── News gathering ────────────────────────────────────────────────────────────


def _fetch_market_news(full: bool) -> tuple[str, bool]:
    """
    Fetch general market news headlines or summaries.
    Returns (formatted_text, significance_triggered).
    """
    try:
        from news import fetch_yahoo_rss

        # Use SPY as the market proxy news source
        articles = fetch_yahoo_rss("SPY", max_articles=15 if full else 8)
        if not articles:
            return "No market news available.", False

        lines = []
        significance_hit = False
        for a in articles:
            title = a.get("title", "")
            summary = a.get("summary", "") if full else ""
            if any(kw in title.lower() for kw in _SIGNIFICANCE_KEYWORDS):
                significance_hit = True
                title = f"[!] {title}"
            if full and summary:
                lines.append(f"- {title}\n  {summary[:200]}")
            else:
                lines.append(f"- {title}")

        return "\n".join(lines), significance_hit

    except Exception as exc:
        log.debug(f"intelligence: news fetch failed — {exc}")
        return "News unavailable.", False


def _format_macro_calendar() -> str:
    """Format upcoming macro events — 5-day window — for prompt context."""
    try:
        from datetime import date as _date

        today = _date.today()
        cutoff = today + timedelta(days=5)

        upcoming = [e for e in _ALL_EVENTS if today <= e["date"] <= cutoff]

        if not upcoming:
            # Fall back to just the next event if none in 5-day window
            event = get_next_event()
            if not event:
                return "No high-impact macro events in the near term."
            return f"Next macro event: {event['type']} on {event['date']}"

        lines = []
        for ev in upcoming:
            label = ev["date"].strftime("%a %b %-d")
            delta_days = (ev["date"] - today).days
            if delta_days == 0:
                tag = "  *** TODAY ***"
            elif delta_days == 1:
                tag = "  *** TOMORROW — elevated event risk ***"
            else:
                tag = ""
            lines.append(f"  {label}: {ev['type']}{tag}")

        return "Macro events — next 5 days:\n" + "\n".join(lines)
    except Exception:
        return "Macro calendar unavailable."


def _format_patterns(patterns: list[dict]) -> str:
    """Compact pattern history block for the prompt."""
    if not patterns:
        return "No prior pattern history available — this is early in the learning cycle."

    lines = ["Recent patterns from similar market conditions (learn from these):"]
    for p in patterns[:15]:
        outcome = "WIN" if (p.get("pnl") or 0) > 0 else "LOSS"
        pnl_pct = p.get("pnl_pct", 0) or 0
        tt = p.get("trade_type", "?")
        sym = p.get("symbol", "?")
        read = (p.get("market_read") or "")[:120]
        reason = p.get("exit_reason", "?")
        lines.append(f"  {sym} {tt} → {outcome} {pnl_pct:+.1f}% ({reason})\n    context: {read}")
    return "\n".join(lines)


def _format_thesis_performance(perfs: list[dict]) -> str:
    """Compact thesis performance block for the classification prompt."""
    if not perfs:
        return "Historical thesis performance\nNo completed patterns with sufficient data yet."
    lines = ["Historical thesis performance (≥3 trades per combination):"]
    for p in perfs:
        lines.append(
            f"  {p['trade_type']} / {p['thesis_class']}: "
            f"{p['win_rate'] * 100:.0f}% WR, avg {p['avg_pnl_pct']:+.1f}% "
            f"({p['count']} trades)"
        )
    return "\n".join(lines)


def _format_setup_performance(perfs: list[dict]) -> str:
    """
    Format entry-thesis learning block for the classification prompt.
    Shows which signal setup types (momentum, breakout, mean_reversion, etc.)
    have historically generated positive expectancy — directly informs which
    setups to classify as actionable vs AVOID.
    """
    if not perfs:
        return "Setup-type edge data\nInsufficient data yet (need ≥3 completed trades per setup type)."
    lines = ["Entry setup edge (by dominant signal dimension, ≥3 trades):"]
    for p in perfs:
        edge_label = (
            "EDGE"
            if p["win_rate"] >= 0.55 and p["avg_pnl_pct"] > 0
            else "AVOID"
            if p["win_rate"] < 0.40 or p["avg_pnl_pct"] < -0.1
            else "NEUTRAL"
        )
        lines.append(
            f"  {p['trade_type']} / {p['setup_type']}: "
            f"{p['win_rate'] * 100:.0f}% WR, avg {p['avg_pnl_pct']:+.2f}% "
            f"({p['count']} trades) [{edge_label}]"
        )
    return "\n".join(lines)


# ── Session context builder ───────────────────────────────────────────────────


def _build_session_context(full_news: bool) -> SessionContext:
    """Fetch all context data and build SessionContext. Called when cache expires."""
    obs = get_market_observation(force_refresh=True)
    patterns = get_relevant_patterns(obs, n=CONFIG.get("intelligence_pattern_lookback", 20))
    news_text, _ = _fetch_market_news(full=full_news)
    macro_text = _format_macro_calendar()
    pattern_text = _format_patterns(patterns)
    thesis_perfs = get_thesis_performance(min_samples=3)
    thesis_text = _format_thesis_performance(thesis_perfs)
    setup_perfs = get_setup_performance(min_samples=3)
    setup_text = _format_setup_performance(setup_perfs)

    overnight_text = ""
    try:
        from overnight_research import load_overnight_notes

        overnight_text = load_overnight_notes()
    except Exception as exc:
        log.debug("intelligence: overnight notes unavailable — %s", exc)

    return SessionContext(
        timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        market_read="",  # filled after Opus call in classify_signals
        observation=obs,
        news_mode="full" if full_news else "headlines",
        news_text=news_text,
        pattern_text=pattern_text,
        macro_text=macro_text,
        thesis_perf_text=thesis_text,
        setup_perf_text=setup_text,
        overnight_text=overnight_text,
    )


# ── Prompt builders ───────────────────────────────────────────────────────────


def _build_regime_context_block(regime: dict) -> str:
    """Format the raw regime dict as a readable context block for the prompt."""
    vix = regime.get("vix", 0)
    vix_1h = regime.get("vix_1h_change", 0)
    spy_price = regime.get("spy_price", 0)
    spy_above = regime.get("spy_above_200d", False)
    qqq_price = regime.get("qqq_price", 0)
    qqq_above = regime.get("qqq_above_200d", False)
    breadth = regime.get("breadth_pct")
    credit_stress = regime.get("credit_stress", False)
    credit_spread = regime.get("credit_spread")
    label = regime.get("regime", "UNKNOWN")
    router = regime.get("regime_router", "unknown")

    breadth_str = f"{breadth:.0f}%" if breadth is not None else "unavailable"
    credit_str = f"{credit_spread:.0f} bps" if credit_spread is not None else "unavailable"

    return (
        f"  System regime label: {label}  (informational — reason freely from the data below)\n"
        f"  VIX: {vix:.1f}  (1h change: {vix_1h:+.1%})\n"
        f"  SPY: ${spy_price:.2f}  above 200d MA: {spy_above}\n"
        f"  QQQ: ${qqq_price:.2f}  above 200d MA: {qqq_above}\n"
        f"  Breadth (% S&P500 above 200d MA): {breadth_str}\n"
        f"  Credit stress: {credit_stress}  spread: {credit_str}\n"
        f"  Intraday regime router: {router}"
    )


def _format_trade_context_block(trade_contexts: dict[str, dict]) -> str:
    """
    Format per-symbol TradeContext as a compact text block for the Opus prompt.
    Only renders symbols that have a context dict.
    """
    if not trade_contexts:
        return ""

    lines = ["## Per-symbol entry context (live data — use this to inform trade_type and conviction)"]
    for sym, ctx in trade_contexts.items():
        if not ctx:
            continue
        dq = ctx.get("data_quality", "unknown")
        direction = ctx.get("direction", "?")
        lines.append(f"\n{sym} {direction}  [data_quality={dq}]")

        # Analyst block
        consensus   = ctx.get("analyst_consensus") or "n/a"
        upside      = ctx.get("analyst_upside_pct")
        buy_ct      = ctx.get("analyst_buy_count")
        sell_ct     = ctx.get("analyst_sell_count")
        earnings_d  = ctx.get("earnings_days_away")
        upside_str  = f" | PT upside {upside:+.1f}%" if upside is not None else ""
        grade_str   = f" | {buy_ct}↑/{sell_ct}↓" if buy_ct is not None else ""
        earn_str    = f" | earnings {earnings_d}d" if earnings_d is not None else ""
        lines.append(f"  Analyst: {consensus}{grade_str}{upside_str}{earn_str}")

        # Smart money
        insider     = ctx.get("insider_net_sentiment")
        ins_val     = ctx.get("insider_buy_value_3m")
        congress    = ctx.get("congressional_sentiment")
        ins_str     = f"insider={insider}" if insider else ""
        ins_val_str = f" (${ins_val:.1f}M net)" if ins_val is not None else ""
        cong_str    = f" | congress={congress}" if congress and congress != "NONE" else ""
        if ins_str or cong_str:
            lines.append(f"  Smart money: {ins_str}{ins_val_str}{cong_str}")

        # Fundamentals
        rev_yoy     = ctx.get("revenue_growth_yoy")
        eps_accel   = ctx.get("eps_accelerating")
        beat_rate   = ctx.get("eps_beat_rate")
        gm          = ctx.get("gross_margin")
        fcf         = ctx.get("fcf_yield")
        dcf_up      = ctx.get("dcf_upside_pct")
        rev_decel   = ctx.get("revenue_decelerating", False)
        if rev_yoy is not None:
            accel_str  = " EPS↑" if eps_accel else ""
            beat_str   = f" beat={beat_rate:.0f}%" if beat_rate is not None else ""
            gm_str     = f" | margin={gm:.1f}%" if gm is not None else ""
            fcf_str    = f" | FCF_yield={fcf:.1f}%" if fcf is not None else ""
            dcf_str    = f" | DCF_upside={dcf_up:+.1f}%" if dcf_up is not None else ""
            decel_flag = " ⚠DECEL" if rev_decel else ""
            lines.append(
                f"  Fundamentals: rev_yoy={rev_yoy:+.1f}%{decel_flag}{accel_str}{beat_str}"
                f"{gm_str}{fcf_str}{dcf_str}"
            )

        # Sector
        etf         = ctx.get("sector_etf")
        above_50d   = ctx.get("sector_above_50d")
        vs_spy      = ctx.get("sector_3m_vs_spy")
        if etf:
            above_str = "above_50d ✓" if above_50d else ("below_50d ✗" if above_50d is False else "50d=n/a")
            spy_str   = f" | {vs_spy:+.1f}% vs SPY 3m" if vs_spy is not None else ""
            lines.append(f"  Sector {etf}: {above_str}{spy_str}")

        # Intraday
        hod_d  = ctx.get("hod_distance_pct")
        vwap_d = ctx.get("vwap_distance_pct")
        rvol   = ctx.get("rel_volume")
        dead   = ctx.get("in_dead_window", False)
        spread = ctx.get("bid_ask_spread_pct")
        age    = ctx.get("signal_age_minutes")
        sf     = ctx.get("short_float_pct")
        intra_parts = []
        if hod_d  is not None: intra_parts.append(f"HOD_dist={hod_d:+.1f}%")
        if vwap_d is not None: intra_parts.append(f"VWAP_dist={vwap_d:+.1f}%")
        if rvol   is not None: intra_parts.append(f"rel_vol={rvol:.1f}x")
        if dead:               intra_parts.append("dead_window=Y")
        if spread is not None: intra_parts.append(f"spread={spread:.2f}%")
        if age    is not None: intra_parts.append(f"age={age:.1f}min")
        if sf     is not None: intra_parts.append(f"short_float={sf:.1f}%")
        if intra_parts:
            lines.append(f"  Intraday: {' | '.join(intra_parts)}")

        # Institutional + 52wk
        inst_pct  = ctx.get("institutional_ownership_pct")
        inst_chg  = ctx.get("institutional_ownership_change")
        w52_dist  = ctx.get("week52_high_distance_pct")
        extra = []
        if inst_pct  is not None: extra.append(f"inst_own={inst_pct:.1f}%{f' ({inst_chg:+.1f}pp QoQ)' if inst_chg is not None else ''}")
        if w52_dist  is not None: extra.append(f"52wk_high_dist={w52_dist:+.1f}%")
        if extra:
            lines.append(f"  Structure: {' | '.join(extra)}")

    return "\n".join(lines)


def _build_classification_prompt(
    ctx: SessionContext,
    candidates: list[dict],
    regime: dict | None = None,
    trade_contexts: dict[str, dict] | None = None,
) -> str:
    """Build the full classification prompt for Opus."""

    candidate_lines = []
    for c in candidates:
        sym = c.get("symbol", "?")
        dir_ = c.get("direction", "?")
        score = c.get("score", 0)
        dims = c.get("score_breakdown", {})
        dim_str = ", ".join(f"{k}={v:.0f}" for k, v in dims.items() if v)
        candidate_lines.append(f"  {sym} {dir_}  score={score}  dims=[{dim_str}]")
    candidates_block = "\n".join(candidate_lines) if candidate_lines else "  (no candidates)"

    regime_block = (
        f"\n## Market context at classification time\n{_build_regime_context_block(regime)}\n" if regime else ""
    )

    vocab_str = " | ".join(sorted(SESSION_CHARACTER_VOCAB))

    overnight_block = f"\n## Overnight research notes\n{ctx.overnight_text}\n" if ctx.overnight_text else ""
    entry_ctx_block = (
        f"\n{_format_trade_context_block(trade_contexts)}\n"
        if trade_contexts else ""
    )

    return f"""You are the intelligence layer for Decifer, an autonomous trading system. \
Before any trade is placed, you reason about the market and classify each signal.
{overnight_block}
## Market observation
{ctx.observation.to_prompt_text()}
{regime_block}
## Macro calendar
{ctx.macro_text}

## Market news ({ctx.news_mode})
{ctx.news_text}

## {ctx.pattern_text}

## {ctx.thesis_perf_text}

## {ctx.setup_perf_text}
{entry_ctx_block}
## Signal candidates (technically scored by the scanner)
Scores are on a 0–50 scale. Minimum to reach this stage: 14/50 (28%). \
A score of 35+ (70%) is high-conviction. Do not treat these as percentages out of 100.
{candidates_block}

## Your task

First, choose a session_character label that best describes today's market session. \
Pick exactly one from: {vocab_str}

Then write a brief market_read (2-4 sentences): what is the market environment \
right now? What dynamics are active? What does the cross-asset picture tell you?

Then, for each candidate, decide:
1. trade_type: SCALP (minutes to hours, pure technical), SWING (days, technical + \
backing thesis), HOLD (weeks, thesis-driven — only when the fundamental and \
environmental case is strong), or AVOID (does not fit the environment).
2. conviction: 0.0–1.0. This is NOT your confidence — it is the count of \
independent observations that support this trade divided by total possible support \
points. Base it only on observable facts in the data above. Do not inflate.

## Hard rules
- AVOID if the macro calendar shows a high-impact event within 6 hours
- AVOID if the market observation shows acute stress (multiple assets in sharp \
coordinated move that contradicts the signal direction)
- HOLD requires both strong environmental fit AND a clear backing thesis — rare
- conviction above 0.8 requires at least 4 independent supporting observations
- Every candidate must get a classification — no omissions

Respond with ONLY valid JSON, no markdown:
{{
  "session_character": "RELIEF_RALLY",
  "market_read": "...",
  "classifications": [
    {{"symbol": "X", "trade_type": "SWING", "conviction": 0.65, "reasoning": "one sentence"}},
    ...
  ]
}}"""


# ── Evidence-based fallback ───────────────────────────────────────────────────


def _fallback_classify(
    candidate: dict,
    obs: MarketObservation,
) -> SignalClassification:
    """
    Tier 2: mechanical classification from signal score and observation.
    Used when Opus is unavailable. Always produces a valid result.
    """
    sym = candidate.get("symbol", "?")
    score = candidate.get("score", 0)
    dir_ = candidate.get("direction", "LONG")

    # AVOID if acute market stress contradicts direction
    spy = obs.assets.get("SPY") if obs else None
    if spy and dir_ == "LONG" and spy.change_1d < -2.0 and obs.vix > 28:
        return SignalClassification(
            symbol=sym,
            trade_type="AVOID",
            conviction=0.0,
            reasoning="Fallback: acute market stress contradicts LONG",
            source="fallback",
        )
    if spy and dir_ == "SHORT" and spy.change_1d > 2.0 and obs.vix < 15:
        return SignalClassification(
            symbol=sym,
            trade_type="AVOID",
            conviction=0.0,
            reasoning="Fallback: strong risk-on contradicts SHORT",
            source="fallback",
        )

    # Classify by score band
    conviction = round(min(score / 50.0, 1.0), 2)
    if score >= 40:
        trade_type = "SWING"
    elif score >= 28 or score >= CONFIG.get("min_score_to_trade", 14):
        trade_type = "SCALP"
    else:
        trade_type = "AVOID"
        conviction = 0.0

    return SignalClassification(
        symbol=sym,
        trade_type=trade_type,
        conviction=conviction,
        reasoning=f"Fallback: score={score}",
        source="fallback",
    )


# ── Response parser ───────────────────────────────────────────────────────────


def _parse_response(
    text: str,
    candidates: list[dict],
    obs: MarketObservation,
) -> tuple[str, str, list[SignalClassification]]:
    """
    Parse Opus JSON response. Returns (session_character, market_read, classifications).
    Any missing or invalid classification falls back to evidence-based tier.
    """
    # Strip markdown code fences
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        raw = json.loads(text)
    except Exception:
        log.warning("intelligence: JSON parse failed — full fallback")
        return _DEFAULT_SESSION_CHARACTER, "", [_fallback_classify(c, obs) for c in candidates]

    raw_char = str(raw.get("session_character", "")).upper().strip()
    session_character = raw_char if raw_char in SESSION_CHARACTER_VOCAB else _DEFAULT_SESSION_CHARACTER

    market_read = str(raw.get("market_read", ""))[:600]
    raw_list = raw.get("classifications", [])

    # Index by symbol for lookup
    raw_by_sym = {r.get("symbol", "").upper(): r for r in raw_list}

    results = []
    for c in candidates:
        sym = c.get("symbol", "").upper()
        r = raw_by_sym.get(sym)

        if not r:
            log.debug(f"intelligence: {sym} missing from Opus output — fallback")
            results.append(_fallback_classify(c, obs))
            continue

        tt = str(r.get("trade_type", "SCALP")).upper()
        if tt not in ("SCALP", "SWING", "HOLD", "AVOID"):
            tt = "SCALP"

        try:
            conviction = max(0.0, min(1.0, float(r.get("conviction", 0.5))))
        except (TypeError, ValueError):
            conviction = 0.5

        results.append(
            SignalClassification(
                symbol=sym,
                trade_type=tt,
                conviction=round(conviction, 3),
                reasoning=str(r.get("reasoning", ""))[:300],
                source="opus",
            )
        )

    return session_character, market_read, results


# ── Public API ────────────────────────────────────────────────────────────────


def classify_signals(
    candidates: list[dict],
    force_context_refresh: bool = False,
    regime: dict | None = None,
    trade_contexts: dict[str, dict] | None = None,
) -> tuple[str, str, list[SignalClassification]]:
    """
    Classify a batch of scored signal candidates.

    Rebuilds session context when cache expires. Always returns a
    classification for every candidate — never blocks on Opus failure.

    Args:
        candidates: list of signal dicts from the scanner, each with
                    {symbol, direction, score, score_breakdown, ...}
        force_context_refresh: bypass context cache
        regime: regime dict from get_market_regime() — passed as context to Opus

    Returns:
        (session_character, market_read, classifications)
        session_character: Opus-chosen label from SESSION_CHARACTER_VOCAB
        market_read: Opus free-form interpretation of current environment
        classifications: one SignalClassification per candidate
    """
    global _session_ctx, _ctx_time, _session_open_done, _last_session_character

    if not candidates:
        return _last_session_character, "", []

    if not CONFIG.get("use_intelligence_layer", True):
        obs = get_market_observation()
        return _DEFAULT_SESSION_CHARACTER, "", [_fallback_classify(c, obs) for c in candidates]

    # ── Rebuild session context if needed ─────────────────────
    with _ctx_lock:
        needs_rebuild = force_context_refresh or not _context_valid()
        first_call = not _session_open_done

    if needs_rebuild:
        ctx = _build_session_context(full_news=first_call)

        # Check if any new headline warrants a full news refresh
        if not first_call:
            _headlines, significant = _fetch_market_news(full=False)
            if significant:
                log.info("intelligence: significance keyword detected — forcing full news refresh")
                ctx = _build_session_context(full_news=True)

        with _ctx_lock:
            _session_ctx = ctx
            _ctx_time = datetime.now(UTC)
            _session_open_done = True
    else:
        with _ctx_lock:
            ctx = _session_ctx

    obs = ctx.observation

    # ── Tier 1: Opus classification ────────────────────────────
    try:
        prompt = _build_classification_prompt(ctx, candidates, regime=regime, trade_contexts=trade_contexts)
        client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        resp = client.messages.create(
            model=CONFIG.get("intelligence_model", "claude-opus-4-6"),
            max_tokens=CONFIG.get("intelligence_max_tokens", 1024),
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        session_character, market_read, classifications = _parse_response(text, candidates, obs)

        # Store market_read and session_character in cache for external access
        with _ctx_lock:
            if _session_ctx:
                _session_ctx.market_read = market_read
            _last_session_character = session_character

        avoid_count = sum(1 for c in classifications if c.trade_type == "AVOID")
        log.info(
            f"[intelligence] {session_character} | {len(candidates)} candidates → "
            f"{len(classifications) - avoid_count} actionable, "
            f"{avoid_count} avoided | {market_read[:80]}"
        )
        return session_character, market_read, classifications

    except Exception as exc:
        log.warning(f"[intelligence] Opus failed ({exc}) — full evidence fallback")
        return _DEFAULT_SESSION_CHARACTER, "", [_fallback_classify(c, obs) for c in candidates]


def get_current_market_read() -> str:
    """Return the most recent cached market_read, or empty string."""
    with _ctx_lock:
        return (_session_ctx.market_read if _session_ctx else "") or ""


def get_current_session_character() -> str:
    """Return the most recent session_character produced by Opus."""
    return _last_session_character
