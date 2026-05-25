# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  customer_event_tape.py                    ║
# ║   Customer-only Event Tape writer/reader                    ║
# ║   Sprint M11A — Customer Event Tape                          ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
customer_event_tape.py — Customer-only Event Tape.

Writes data/intelligence/customer_event_tape.json — a structured, customer-safe
record of fresh market-moving events for the Market Map.

Boundaries (enforced by scripts/verify_customer_event_tape_safety.py):
  - Must NOT be imported by execution modules (orders_*, bot_trading, bot_ibkr,
    apex_orchestrator, options_entries, alpaca_news, news_sentinel, pm_*, …).
  - Must NOT be imported by universe_builder.py for live scoring.
  - Must NOT be imported by handoff_reader.py for live trading eligibility.
  - Must NOT alter trading behaviour.

The tape is advisory customer intelligence only. Live trading, execution,
PM actions, universe scoring, and handoff eligibility are unaffected.

Entry point:
    maybe_record_customer_event(headline, body_or_snippet, symbols, source,
                                 source_published_at, source_type) -> list[event_id]
        Fail-soft. Catches every exception so the caller (news intake) is
        unaffected by tape failures.

Read API:
    load_customer_event_tape() -> dict
    get_recent_events(within_hours: float) -> list[dict]
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from customer_event_classifier import ClassifiedEvent, classify_headline

log = logging.getLogger("decifer.customer_event_tape")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE = os.path.dirname(os.path.abspath(__file__))
_TAPE_PATH = os.path.join(_BASE, "data/intelligence/customer_event_tape.json")

_SCHEMA_VERSION = "customer_event_tape_v1"

# Cap retained events to keep the file small. The Market Map only reads the
# most recent ones; older context lives in the source news stores.
_MAX_EVENTS = 200

# Default time-to-live for an event. After this the freshness_status drops
# from "fresh" to "stale".
_DEFAULT_TTL_HOURS = 12.0

# Section freshness windows (hours).
_FRESH_WINDOW_H = 4.0      # within this → "fresh"
_DEGRADED_WINDOW_H = 12.0  # within this → "degraded"; beyond → "stale"

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _empty_tape() -> dict[str, Any]:
    now = _now_iso()
    valid = (datetime.now(UTC) + timedelta(hours=_DEFAULT_TTL_HOURS)).isoformat()
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": now,
        "valid_until": valid,
        "freshness_status": "fresh",
        "events": [],
    }


def _read_tape() -> dict[str, Any]:
    if not os.path.exists(_TAPE_PATH):
        return _empty_tape()
    try:
        with open(_TAPE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "events" not in data:
            return _empty_tape()
        return data
    except Exception as exc:
        log.debug("_read_tape: failed to read tape (%s) — returning empty.", exc)
        return _empty_tape()


def _write_tape(tape: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_TAPE_PATH), exist_ok=True)
    tmp = _TAPE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tape, f, indent=2, sort_keys=False)
    os.replace(tmp, _TAPE_PATH)


# ---------------------------------------------------------------------------
# Freshness logic
# ---------------------------------------------------------------------------

def compute_freshness_status(
    processed_at_iso: str,
    valid_until_iso: str | None = None,
) -> str:
    """Return one of: fresh | stale | degraded | under_review | unknown.

    Rules:
      - if processed_at unparseable → "unknown"
      - if now > valid_until → "stale"
      - if age ≤ _FRESH_WINDOW_H → "fresh"
      - if age ≤ _DEGRADED_WINDOW_H → "degraded"
      - else → "stale"
    """
    if not processed_at_iso:
        return "unknown"
    try:
        proc = datetime.fromisoformat(processed_at_iso)
        if proc.tzinfo is None:
            proc = proc.replace(tzinfo=UTC)
    except Exception:
        return "unknown"

    now = datetime.now(UTC)

    if valid_until_iso:
        try:
            valid = datetime.fromisoformat(valid_until_iso)
            if valid.tzinfo is None:
                valid = valid.replace(tzinfo=UTC)
            if now > valid:
                return "stale"
        except Exception:
            pass

    age_h = (now - proc).total_seconds() / 3600.0
    if age_h <= _FRESH_WINDOW_H:
        return "fresh"
    if age_h <= _DEGRADED_WINDOW_H:
        return "degraded"
    return "stale"


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------

def _build_event_record(
    ce: ClassifiedEvent,
    *,
    headline: str,
    symbols: list[str],
    source: str,
    source_published_at: str,
    processed_at: str,
    valid_until: str,
) -> dict[str, Any]:
    """Assemble the persisted event dict from a classifier result."""
    if ce.event_family not in {
        "geopolitics", "commodities", "earnings_guidance", "corporate_action",
        "central_bank", "macro_data", "major_economy_policy", "regulation_legal",
        "credit_liquidity", "technology_product", "company_specific_shock",
        "market_structure",
    }:
        # Classifier produced an unknown family — drop the record rather than
        # leak unexpected taxonomy into customer output.
        raise ValueError(f"unknown event_family: {ce.event_family!r}")

    return {
        "event_id": str(uuid.uuid4()),
        "event_family": ce.event_family,
        "event_type": ce.event_type,
        "status": ce.status or "reported",
        "title": ce.title or headline[:200],
        "summary_plain_english": ce.summary_plain_english,
        "source": source,
        "source_published_at": source_published_at,
        "ingested_at": processed_at,
        "processed_at": processed_at,
        "valid_until": valid_until,
        "freshness_status": compute_freshness_status(processed_at, valid_until),
        "entities": list(ce.entities),
        "geography": list(ce.geography),
        "affected_channels": list(ce.affected_channels),
        "likely_positive_exposures": list(ce.likely_positive_exposures),
        "likely_negative_exposures": list(ce.likely_negative_exposures),
        "sectors_positive": list(ce.sectors_positive),
        "sectors_negative": list(ce.sectors_negative),
        "themes_strengthened": list(ce.themes_strengthened),
        "themes_weakened": list(ce.themes_weakened),
        "tickers_first_order": list(ce.tickers_first_order or symbols),
        "tickers_second_order": list(ce.tickers_second_order),
        "confirmation_signals": list(ce.confirmation_signals),
        "invalidation_signals": list(ce.invalidation_signals),
        "known_conflicts": list(ce.known_conflicts),
        "source_confidence": ce.source_confidence,
        "materiality": ce.materiality,
        "customer_safe": True,
    }


# ---------------------------------------------------------------------------
# Public entry — fail-soft writer
# ---------------------------------------------------------------------------

def maybe_record_customer_event(
    headline: str,
    body_or_snippet: str = "",
    symbols: list[str] | None = None,
    source: str = "unknown",
    source_published_at: str | None = None,
    source_type: str = "news",
) -> list[str]:
    """Classify a headline and append zero-or-more events to the customer tape.

    Fail-soft: any exception is caught and logged at DEBUG. The caller (news
    intake) is unaffected — Event Tape failures cannot break trigger dispatch
    or execution.

    Returns:
        List of event_ids written. Empty list if nothing classified or on error.
    """
    try:
        if not headline or not isinstance(headline, str):
            return []
        events = classify_headline(headline, body_or_snippet, symbols or [])
        if not events:
            return []

        now = _now_iso()
        valid = (datetime.now(UTC) + timedelta(hours=_DEFAULT_TTL_HOURS)).isoformat()
        spt = source_published_at or now

        new_records: list[dict[str, Any]] = []
        for ce in events:
            try:
                rec = _build_event_record(
                    ce,
                    headline=headline,
                    symbols=symbols or [],
                    source=source or "unknown",
                    source_published_at=spt,
                    processed_at=now,
                    valid_until=valid,
                )
                new_records.append(rec)
            except Exception as exc:
                log.debug("record builder skipped event: %s", exc)
                continue

        if not new_records:
            return []

        with _LOCK:
            tape = _read_tape()
            tape["events"] = new_records + list(tape.get("events", []))
            tape["events"] = tape["events"][:_MAX_EVENTS]
            tape["generated_at"] = now
            tape["valid_until"] = valid
            tape["freshness_status"] = compute_freshness_status(now, valid)
            tape["schema_version"] = _SCHEMA_VERSION
            _write_tape(tape)

        return [r["event_id"] for r in new_records]

    except Exception as exc:
        log.debug("maybe_record_customer_event: fail-soft (%s)", exc)
        return []


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------

def load_customer_event_tape() -> dict[str, Any]:
    """Return the full tape dict. Empty structure if missing/unreadable."""
    tape = _read_tape()
    # Refresh per-event freshness on read so consumers see live status.
    for ev in tape.get("events", []):
        ev["freshness_status"] = compute_freshness_status(
            ev.get("processed_at", ""),
            ev.get("valid_until"),
        )
    if tape.get("events"):
        # Tape-level freshness = freshest event's freshness
        most_recent = max(
            (e for e in tape["events"] if e.get("processed_at")),
            key=lambda e: e["processed_at"],
            default=None,
        )
        if most_recent is not None:
            tape["freshness_status"] = compute_freshness_status(
                most_recent.get("processed_at", ""),
                most_recent.get("valid_until"),
            )
    return tape


def get_recent_events(within_hours: float = 4.0) -> list[dict[str, Any]]:
    """Return events whose processed_at is within `within_hours` of now."""
    tape = load_customer_event_tape()
    cutoff = datetime.now(UTC) - timedelta(hours=within_hours)
    out: list[dict[str, Any]] = []
    for ev in tape.get("events", []):
        try:
            proc = datetime.fromisoformat(ev.get("processed_at", ""))
            if proc.tzinfo is None:
                proc = proc.replace(tzinfo=UTC)
            if proc >= cutoff:
                out.append(ev)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Test/operational helpers
# ---------------------------------------------------------------------------

def clear_tape_for_tests() -> None:
    """Delete the tape file. Test-only helper; never call in production paths."""
    try:
        if os.path.exists(_TAPE_PATH):
            os.remove(_TAPE_PATH)
    except Exception as exc:
        log.debug("clear_tape_for_tests: %s", exc)


def get_tape_path() -> str:
    """Return the absolute path of the tape file. For diagnostics only."""
    return _TAPE_PATH
