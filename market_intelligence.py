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
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import anthropic

from config import CONFIG
from market_observer import get_market_observation, invalidate_cache, MarketObservation
from pattern_library import get_relevant_patterns
from macro_calendar import get_next_event, hours_to_next_event

log = logging.getLogger("decifer.intelligence")

# ── Significance keywords — trigger full news refresh if seen in headlines ──
_SIGNIFICANCE_KEYWORDS = {
    "fed", "fomc", "rate", "hike", "cut", "cpi", "nfp", "payroll",
    "earnings", "guidance", "miss", "beat", "downgrade", "upgrade",
    "halt", "bankruptcy", "merger", "acquisition", "tariff", "sanction",
    "default", "recession", "gdp", "inflation", "surprise",
}

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SignalClassification:
    symbol:      str
    trade_type:  str    # SCALP | SWING | HOLD | AVOID
    conviction:  float  # 0.0–1.0, evidence-based — not LLM self-reported confidence
    reasoning:   str    # Opus rationale (logged only, never acted upon mechanically)
    source:      str    # "opus" | "fallback"


@dataclass
class SessionContext:
    """Cached per intelligence_cache_minutes. Rebuilt on expiry."""
    timestamp:    str
    market_read:  str           # Opus free-form market interpretation
    observation:  MarketObservation
    news_mode:    str           # "full" | "headlines"
    news_text:    str           # formatted for prompt inclusion
    pattern_text: str           # formatted recent patterns for prompt inclusion
    macro_text:   str           # upcoming macro events


# ── Cache ─────────────────────────────────────────────────────────────────────

_ctx_lock    = threading.Lock()
_session_ctx: Optional[SessionContext] = None
_ctx_time:    Optional[datetime]       = None
_session_open_done: bool               = False   # tracks whether first-call full news ran


def _context_valid() -> bool:
    if _session_ctx is None or _ctx_time is None:
        return False
    ttl = timedelta(minutes=CONFIG.get("intelligence_cache_minutes", 30))
    return (datetime.now(timezone.utc) - _ctx_time) < ttl


def invalidate_session_context() -> None:
    """Force context rebuild on next classify call."""
    global _session_ctx, _ctx_time, _session_open_done
    with _ctx_lock:
        _session_ctx       = None
        _ctx_time          = None
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
    """Format upcoming macro events for prompt context."""
    try:
        event = get_next_event()
        if not event:
            return "No high-impact macro events in the near term."
        hours = hours_to_next_event()
        name  = event.get("name", "unknown event")
        date  = event.get("date", "")
        if hours is not None and hours < 48:
            return f"UPCOMING: {name} in {hours:.0f}h ({date}) — elevated event risk"
        return f"Next macro event: {name} on {date}"
    except Exception:
        return "Macro calendar unavailable."


def _format_patterns(patterns: list[dict]) -> str:
    """Compact pattern history block for the prompt."""
    if not patterns:
        return "No prior pattern history available — this is early in the learning cycle."

    lines = ["Recent patterns from similar market conditions (learn from these):"]
    for p in patterns[:15]:
        outcome  = "WIN" if (p.get("pnl") or 0) > 0 else "LOSS"
        pnl_pct  = p.get("pnl_pct", 0) or 0
        tt       = p.get("trade_type", "?")
        sym      = p.get("symbol", "?")
        read     = (p.get("market_read") or "")[:120]
        reason   = p.get("exit_reason", "?")
        lines.append(
            f"  {sym} {tt} → {outcome} {pnl_pct:+.1f}% ({reason})\n"
            f"    context: {read}"
        )
    return "\n".join(lines)


# ── Session context builder ───────────────────────────────────────────────────

def _build_session_context(full_news: bool) -> SessionContext:
    """Fetch all context data and build SessionContext. Called when cache expires."""
    obs          = get_market_observation(force_refresh=True)
    patterns     = get_relevant_patterns(obs, n=CONFIG.get("intelligence_pattern_lookback", 20))
    news_text, _ = _fetch_market_news(full=full_news)
    macro_text   = _format_macro_calendar()
    pattern_text = _format_patterns(patterns)

    return SessionContext(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        market_read="",          # filled after Opus call in classify_signals
        observation=obs,
        news_mode="full" if full_news else "headlines",
        news_text=news_text,
        pattern_text=pattern_text,
        macro_text=macro_text,
    )


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_classification_prompt(
    ctx: SessionContext,
    candidates: list[dict],
) -> str:
    """Build the full classification prompt for Opus."""

    candidate_lines = []
    for c in candidates:
        sym   = c.get("symbol", "?")
        dir_  = c.get("direction", "?")
        score = c.get("score", 0)
        dims  = c.get("score_breakdown", {})
        dim_str = ", ".join(f"{k}={v:.0f}" for k, v in dims.items() if v)
        candidate_lines.append(
            f"  {sym} {dir_}  score={score}/50  dims=[{dim_str}]"
        )
    candidates_block = "\n".join(candidate_lines) if candidate_lines else "  (no candidates)"

    return f"""You are the intelligence layer for Decifer, an autonomous trading system. \
Before any trade is placed, you reason about the market and classify each signal.

## Market observation
{ctx.observation.to_prompt_text()}

## Macro calendar
{ctx.macro_text}

## Market news ({ctx.news_mode})
{ctx.news_text}

## {ctx.pattern_text}

## Signal candidates (technically scored by the scanner)
{candidates_block}

## Your task

First, write a brief market_read (2-4 sentences): what is the market environment \
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
    sym    = candidate.get("symbol", "?")
    score  = candidate.get("score", 0)
    dir_   = candidate.get("direction", "LONG")

    # AVOID if acute market stress contradicts direction
    spy = obs.assets.get("SPY") if obs else None
    if spy and dir_ == "LONG" and spy.change_1d < -2.0 and obs.vix > 28:
        return SignalClassification(
            symbol=sym, trade_type="AVOID", conviction=0.0,
            reasoning="Fallback: acute market stress contradicts LONG",
            source="fallback",
        )
    if spy and dir_ == "SHORT" and spy.change_1d > 2.0 and obs.vix < 15:
        return SignalClassification(
            symbol=sym, trade_type="AVOID", conviction=0.0,
            reasoning="Fallback: strong risk-on contradicts SHORT",
            source="fallback",
        )

    # Classify by score band
    conviction = round(min(score / 50.0, 1.0), 2)
    if score >= 40:
        trade_type = "SWING"
    elif score >= 28:
        trade_type = "SCALP"
    elif score >= CONFIG.get("min_score_to_trade", 14):
        trade_type = "SCALP"
    else:
        trade_type = "AVOID"
        conviction = 0.0

    return SignalClassification(
        symbol=sym, trade_type=trade_type, conviction=conviction,
        reasoning=f"Fallback: score={score}/50",
        source="fallback",
    )


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(
    text: str,
    candidates: list[dict],
    obs: MarketObservation,
) -> tuple[str, list[SignalClassification]]:
    """
    Parse Opus JSON response. Returns (market_read, classifications).
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
        return "", [_fallback_classify(c, obs) for c in candidates]

    market_read = str(raw.get("market_read", ""))[:600]
    raw_list    = raw.get("classifications", [])

    # Index by symbol for lookup
    raw_by_sym = {r.get("symbol", "").upper(): r for r in raw_list}

    results = []
    for c in candidates:
        sym = c.get("symbol", "").upper()
        r   = raw_by_sym.get(sym)

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

        results.append(SignalClassification(
            symbol=sym,
            trade_type=tt,
            conviction=round(conviction, 3),
            reasoning=str(r.get("reasoning", ""))[:300],
            source="opus",
        ))

    return market_read, results


# ── Public API ────────────────────────────────────────────────────────────────

def classify_signals(
    candidates: list[dict],
    force_context_refresh: bool = False,
) -> tuple[str, list[SignalClassification]]:
    """
    Classify a batch of scored signal candidates.

    Rebuilds session context when cache expires. Always returns a
    classification for every candidate — never blocks on Opus failure.

    Args:
        candidates: list of signal dicts from the scanner, each with
                    {symbol, direction, score, score_breakdown, ...}
        force_context_refresh: bypass context cache

    Returns:
        (market_read, classifications)
        market_read: Opus free-form interpretation of current environment
        classifications: one SignalClassification per candidate
    """
    global _session_ctx, _ctx_time, _session_open_done

    if not candidates:
        return "", []

    if not CONFIG.get("use_intelligence_layer", True):
        obs = get_market_observation()
        return "", [_fallback_classify(c, obs) for c in candidates]

    # ── Rebuild session context if needed ─────────────────────
    with _ctx_lock:
        needs_rebuild = force_context_refresh or not _context_valid()
        first_call    = not _session_open_done

    if needs_rebuild:
        ctx = _build_session_context(full_news=first_call)

        # Check if any new headline warrants a full news refresh
        if not first_call:
            headlines, significant = _fetch_market_news(full=False)
            if significant:
                log.info("intelligence: significance keyword detected — forcing full news refresh")
                ctx = _build_session_context(full_news=True)

        with _ctx_lock:
            _session_ctx       = ctx
            _ctx_time          = datetime.now(timezone.utc)
            _session_open_done = True
    else:
        with _ctx_lock:
            ctx = _session_ctx

    obs = ctx.observation

    # ── Tier 1: Opus classification ────────────────────────────
    try:
        prompt = _build_classification_prompt(ctx, candidates)
        client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        resp   = client.messages.create(
            model=CONFIG.get("intelligence_model", "claude-opus-4-6"),
            max_tokens=CONFIG.get("intelligence_max_tokens", 1024),
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        market_read, classifications = _parse_response(text, candidates, obs)

        # Store market_read in session context for pattern recording
        with _ctx_lock:
            if _session_ctx:
                _session_ctx.market_read = market_read

        avoid_count = sum(1 for c in classifications if c.trade_type == "AVOID")
        log.info(
            f"[intelligence] {len(candidates)} candidates → "
            f"{len(classifications) - avoid_count} actionable, "
            f"{avoid_count} avoided | {market_read[:80]}"
        )
        return market_read, classifications

    except Exception as exc:
        log.warning(f"[intelligence] Opus failed ({exc}) — full evidence fallback")
        return "", [_fallback_classify(c, obs) for c in candidates]


def get_current_market_read() -> str:
    """Return the most recent cached market_read, or empty string."""
    with _ctx_lock:
        return (_session_ctx.market_read if _session_ctx else "") or ""
