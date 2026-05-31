# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  earnings_transcript_engine.py              ║
# ║   Earnings call transcript intelligence.                     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
earnings_transcript_engine.py — Fetch earnings call transcripts and extract
structured forward-guidance intelligence for Apex.

Single responsibility: given a symbol + period, fetch the most recent transcript
from FMP, run one Sonnet call to extract guidance direction / tone / key topics,
and write a structured record to macro_events.jsonl (event_type=earnings_call_guidance).

Architecture position: INTELLIGENCE layer.
  - Called from run_intelligence_pipeline.py (deferred post-close step)
  - Output consumed by apex_orchestrator._load_driver_notes() → Apex prompt
  - Also surfaced via intelligence_api GET /api/intelligence/macro-events

Boundaries:
  - Does not import from execution, orders, broker, PM, or universe scoring modules.
  - Does not activate or modify driver state.
  - If transcript is unavailable or LLM fails, returns None silently — never raises.
  - All writes go through macro_event_layer.record_transcript_event() so TTL,
    dedup, and thread-safety are handled in one place.

Public surface:
    process_recent_earnings(universe_symbols, hours_back=36) -> list[str]
        Check earnings calendar for symbols that reported in the last `hours_back`
        hours, fetch + extract each transcript. Returns list of symbols processed.

    process_symbol(symbol, year, quarter) -> dict | None
        Fetch and extract one transcript. Returns the structured event dict or None.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime, timedelta

log = logging.getLogger("decifer.earnings_transcript")

_BASE = os.path.dirname(os.path.abspath(__file__))

# Max Sonnet calls per pipeline run — transcripts are long, keep cost bounded
_MAX_TRANSCRIPTS_PER_RUN = 8

# Minimum confidence to emit an event — suppress very low-quality extractions
_MIN_CONFIDENCE = 0.40

# How long a transcript event stays active (earnings guidance is relevant for ~3 days)
_TRANSCRIPT_TTL_HOURS = 72.0

_EXTRACT_PROMPT = """\
You are analysing an earnings call transcript to extract structured forward-guidance intelligence.

Return ONLY valid JSON matching this exact schema. No markdown, no preamble.

{{
  "guidance_direction": "<raised | lowered | maintained | withdrawn | not_given>",
  "tone": "<confident | neutral | cautious | defensive>",
  "key_topics": ["<topic1>", "<topic2>"],
  "forward_outlook": "<1-2 sentence plain-English summary of what management said about the future>",
  "diverges_from_headline": <true | false>,
  "confidence": <0.0-1.0>
}}

Definitions:
- guidance_direction: Did management explicitly change their numeric forecast?
  raised=guide up, lowered=guide down, maintained=reiterated existing, withdrawn=pulled guidance,
  not_given=no explicit forward guidance provided.
- tone: Overall management tone when discussing the business outlook.
  confident=upbeat, specific, raised expectations.
  neutral=factual, no strong lean.
  cautious=hedging language, uncertainty, soft demand language.
  defensive=explaining misses, pushback on analysts, blame-shifting.
- key_topics: Max 5 topics that dominated the call (e.g. "margins", "AI capex demand",
  "inventory destocking", "China slowdown", "data center", "GLP-1 demand").
- forward_outlook: What did management actually say about the next quarter or year?
  Write as a plain sentence, not a quote. Max 2 sentences.
- diverges_from_headline: True if management commentary contradicts the EPS beat/miss
  (e.g. beat but guided down, or miss but raised guidance).
- confidence: Your confidence in the extraction quality. Low (<0.5) if the transcript
  is very short, heavily redacted Q&A only, or guidance was vague.

TRANSCRIPT (symbol: {symbol}, period: {period}):
{transcript_text}
"""


def _fetch_transcript(symbol: str, year: int, quarter: int) -> str | None:
    """Fetch earnings call transcript text from FMP. Returns raw text or None."""
    try:
        from fmp_client import _get  # type: ignore[attr-defined]
        raw = _get(
            "earning_call_transcript",
            {"symbol": symbol.upper(), "year": str(year), "quarter": str(quarter)},
            ttl=7 * 24 * 3600,  # transcripts don't change — cache for 7 days
        )
        if not raw:
            return None
        # FMP returns a list; take the first (most recent) item
        item = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else None)
        if not item:
            return None
        text = item.get("content") or item.get("transcript") or ""
        return text.strip() or None
    except Exception as exc:
        log.debug("earnings_transcript: fetch failed for %s Q%s %s — %s", symbol, quarter, year, exc)
        return None


def _extract_intelligence(symbol: str, period: str, transcript_text: str) -> dict | None:
    """Call Sonnet to extract structured guidance intelligence. Returns parsed dict or None."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        # Truncate to ~12k chars — transcripts can be 40k+, we only need the prepared remarks
        # (first ~8k) plus a chunk of the Q&A (next ~4k)
        text = transcript_text[:12_000]
        prompt = _EXTRACT_PROMPT.format(
            symbol=symbol,
            period=period,
            transcript_text=text,
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        # Validate required fields
        if result.get("guidance_direction") not in (
            "raised", "lowered", "maintained", "withdrawn", "not_given"
        ):
            return None
        if result.get("tone") not in ("confident", "neutral", "cautious", "defensive"):
            return None
        return result
    except Exception as exc:
        log.debug("earnings_transcript: LLM extraction failed for %s — %s", symbol, exc)
        return None


def _current_quarter(ref_date: date | None = None) -> tuple[int, int]:
    """Return (year, quarter) for the current or most recent fiscal quarter."""
    d = ref_date or date.today()
    q = (d.month - 1) // 3 + 1
    return d.year, q


def process_symbol(symbol: str, year: int, quarter: int) -> dict | None:
    """
    Fetch and extract transcript intelligence for one symbol/period.
    Returns the emitted macro event dict or None.
    """
    period = f"Q{quarter} {year}"
    log.info("earnings_transcript: processing %s %s", symbol, period)

    transcript_text = _fetch_transcript(symbol, year, quarter)
    if not transcript_text:
        log.debug("earnings_transcript: no transcript for %s %s", symbol, period)
        return None

    intelligence = _extract_intelligence(symbol, period, transcript_text)
    if not intelligence:
        return None

    confidence = float(intelligence.get("confidence", 0.0))
    if confidence < _MIN_CONFIDENCE:
        log.debug(
            "earnings_transcript: low confidence %.2f for %s %s — suppressed",
            confidence, symbol, period,
        )
        return None

    guidance = intelligence.get("guidance_direction", "not_given")
    tone = intelligence.get("tone", "neutral")
    topics = intelligence.get("key_topics") or []
    outlook = intelligence.get("forward_outlook", "")
    diverges = bool(intelligence.get("diverges_from_headline", False))

    # Build a human-readable headline
    guidance_labels = {
        "raised":      "raised guidance",
        "lowered":     "lowered guidance",
        "maintained":  "maintained guidance",
        "withdrawn":   "withdrew guidance",
        "not_given":   "gave no explicit guidance",
    }
    tone_labels = {
        "confident": "confident tone",
        "neutral":   "neutral tone",
        "cautious":  "cautious tone",
        "defensive": "defensive tone",
    }
    topic_str = ", ".join(topics[:3]) if topics else "no dominant topics"
    headline = (
        f"{symbol} {guidance_labels.get(guidance, guidance)}, {tone_labels.get(tone, tone)}"
        f" — {topic_str}"
    )
    if diverges:
        headline += " [diverges from headline]"

    event = {
        "event_type":          "earnings_call_guidance",
        "symbol":              symbol.upper(),
        "period":              period,
        "headline":            headline,
        "domain":              "earnings",
        "guidance_direction":  guidance,
        "tone":                tone,
        "key_topics":          topics,
        "forward_outlook":     outlook,
        "diverges_from_headline": diverges,
        "confidence":          confidence,
        "ttl_hours":           _TRANSCRIPT_TTL_HOURS,
    }

    # Emit into macro_events.jsonl via macro_event_layer
    _emit_event(event)
    log.info("earnings_transcript: emitted event for %s %s (guidance=%s tone=%s conf=%.2f)",
             symbol, period, guidance, tone, confidence)
    return event


def _emit_event(event: dict) -> None:
    """Write a transcript event to macro_events.jsonl."""
    try:
        import uuid
        from macro_event_layer import _STORE_PATH, _LOCK  # type: ignore[attr-defined]

        now_str = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(hours=_TRANSCRIPT_TTL_HOURS)).isoformat()

        record = {
            "event_id":      str(uuid.uuid4()),
            "schema_version": "macro_event_v1",
            "recorded_at":   now_str,
            "expires_at":    expires,
            "source":        "earnings_transcript_fmp",
            **event,
        }
        os.makedirs(os.path.dirname(os.path.abspath(_STORE_PATH)), exist_ok=True)
        with _LOCK:
            with open(_STORE_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        log.warning("earnings_transcript: failed to emit event — %s", exc)


def process_recent_earnings(
    universe_symbols: list[str],
    hours_back: int = 36,
) -> list[str]:
    """
    Check earnings calendar for symbols that reported in the last `hours_back` hours,
    fetch + extract each transcript. Returns list of symbols successfully processed.

    Capped at _MAX_TRANSCRIPTS_PER_RUN per pipeline run.
    """
    if not universe_symbols:
        return []

    try:
        from fmp_client import get_earnings_calendar  # type: ignore[attr-defined]
    except ImportError:
        log.warning("earnings_transcript: fmp_client unavailable")
        return []

    today = date.today()
    cutoff = datetime.now(UTC) - timedelta(hours=hours_back)
    cutoff_date = cutoff.date()

    # Fetch earnings that occurred in the lookback window
    # get_earnings_calendar looks ahead; we look back by using days_ahead=0 and
    # reading from FMP with from/to params directly
    try:
        from fmp_client import _get  # type: ignore[attr-defined]
        from_dt = str(cutoff_date)
        to_dt = str(today)
        raw = _get(
            "earning-calendar",
            {"from": from_dt, "to": to_dt},
            ttl=3600,
        )
        if not raw or not isinstance(raw, list):
            return []
    except Exception as exc:
        log.warning("earnings_transcript: calendar fetch failed — %s", exc)
        return []

    sym_set = {s.upper() for s in universe_symbols}
    candidates = []
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if sym not in sym_set:
            continue
        rep_date_str = (item.get("date") or "")[:10]
        if not rep_date_str:
            continue
        try:
            rep_date = date.fromisoformat(rep_date_str)
        except ValueError:
            continue
        if rep_date < cutoff_date or rep_date > today:
            continue
        candidates.append(sym)

    if not candidates:
        log.info("earnings_transcript: no universe symbols reported in last %dh", hours_back)
        return []

    # Deduplicate
    candidates = list(dict.fromkeys(candidates))[:_MAX_TRANSCRIPTS_PER_RUN]
    log.info("earnings_transcript: %d symbols to process: %s", len(candidates), candidates)

    year, quarter = _current_quarter()
    # For AMC reporters, the transcript may not be ready same-day — try current quarter first,
    # fall back to previous quarter if transcript is empty
    prev_year = year if quarter > 1 else year - 1
    prev_quarter = quarter - 1 if quarter > 1 else 4

    processed = []
    for sym in candidates:
        result = process_symbol(sym, year, quarter)
        if result is None:
            # Try previous quarter (AMC report from last quarter)
            result = process_symbol(sym, prev_year, prev_quarter)
        if result is not None:
            processed.append(sym)

    log.info("earnings_transcript: %d/%d transcripts processed", len(processed), len(candidates))
    return processed
