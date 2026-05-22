"""
pm_thesis.py — Portfolio position enrichment and thesis classification.

Single responsibility: given a raw active_trades position dict, return an
enriched PMPosition dataclass with computed fields and a ThesisStatus label.

No execution logic. No config reads. No side-effects.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Any

UTC = datetime.timezone.utc


class ThesisStatus(str, Enum):
    STRENGTHENING   = "THESIS_STRENGTHENING"
    INTACT          = "THESIS_INTACT"
    INTACT_DEGRADED = "THESIS_INTACT_DEGRADED"   # intact but score_delta unreliable
    PLAYED_OUT      = "THESIS_PLAYED_OUT"
    DECAYING        = "THESIS_DECAYING"
    BROKEN          = "THESIS_BROKEN"
    UNKNOWN         = "THESIS_UNKNOWN"


@dataclass
class PMPosition:
    symbol:               str
    market_value:         float
    position_pct_nlv:     float
    unrealised_pnl_pct:   float
    holding_period_hours: float
    entry_score:          float
    current_score:        float
    score_delta:          float
    thesis_status:        ThesisStatus
    spread_pct:           float | None
    quote_age_s:          float | None
    qty:                  float
    entry_price:          float
    current_price:        float
    score_source:         str   = "ENTRY_SCORE_FALLBACK"


def build_position(
    symbol: str,
    pos: dict[str, Any],
    nlv: float,
    candidate_scores: dict[str, float],
) -> PMPosition | None:
    """
    Build PMPosition from a raw active_trades entry.
    Returns None if essential numeric fields are absent or zero.
    candidate_scores maps symbol → current signal score from latest scan.
    """
    qty   = _f(pos.get("qty"))
    entry = _f(pos.get("entry"))
    if qty is None or entry is None or qty <= 0 or entry <= 0:
        return None

    current      = _f(pos.get("current")) or entry
    market_value = qty * current
    cost_basis   = qty * entry
    pnl_dollar   = _f(pos.get("pnl")) or 0.0
    pnl_pct      = pnl_dollar / cost_basis if cost_basis > 0 else 0.0
    pos_pct_nlv  = market_value / nlv if nlv > 0 else 0.0

    entry_score = _f(pos.get("entry_score") or pos.get("score")) or 0.0

    import pm_score_resolver
    current_score, score_source = pm_score_resolver.resolve(
        symbol, entry_score, candidate_scores
    )
    score_delta = current_score - entry_score

    spread, age = _quote_info(symbol)

    thesis = _classify(
        entry_score=entry_score,
        score_delta=score_delta,
        pnl_pct=pnl_pct,
        holding_hours=_holding_hours(pos.get("open_time")),
    )

    # When score_delta is unreliable (entry fallback), the INTACT classification
    # is meaningless — score_delta is always 0. Demote to INTACT_DEGRADED so
    # downstream logic and the decision log can distinguish real INTACT from
    # "we just don't know."
    if score_source == "ENTRY_SCORE_FALLBACK" and thesis == ThesisStatus.INTACT:
        thesis = ThesisStatus.INTACT_DEGRADED

    return PMPosition(
        symbol=symbol,
        market_value=market_value,
        position_pct_nlv=pos_pct_nlv,
        unrealised_pnl_pct=pnl_pct,
        holding_period_hours=_holding_hours(pos.get("open_time")),
        entry_score=entry_score,
        current_score=current_score,
        score_delta=score_delta,
        thesis_status=thesis,
        spread_pct=spread,
        quote_age_s=age,
        qty=qty,
        entry_price=entry,
        current_price=current,
        score_source=score_source,
    )


def _classify(
    entry_score: float,
    score_delta: float,
    pnl_pct: float,
    holding_hours: float,
) -> ThesisStatus:
    if entry_score == 0.0:
        return ThesisStatus.UNKNOWN
    if pnl_pct < -0.08 and score_delta < -15:
        return ThesisStatus.BROKEN
    if score_delta < -10 or (pnl_pct < -0.04 and score_delta < -5):
        return ThesisStatus.DECAYING
    if holding_hours > 48 and abs(score_delta) < 3:
        return ThesisStatus.PLAYED_OUT
    if pnl_pct > 0.02 and score_delta > 5:
        return ThesisStatus.STRENGTHENING
    return ThesisStatus.INTACT


def _holding_hours(open_time: Any) -> float:
    if not open_time:
        return 0.0
    try:
        opened = datetime.datetime.fromisoformat(str(open_time))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        return (datetime.datetime.now(UTC) - opened).total_seconds() / 3600.0
    except Exception:
        return 0.0


def _quote_info(symbol: str) -> tuple[float | None, float | None]:
    """Return (spread_pct, age_seconds) from quote cache. Never raises."""
    try:
        import time
        import alpaca_stream
        q = alpaca_stream.QUOTE_CACHE.get(symbol)
        if q is None:
            return None, None
        spread = q.get("spread_pct")
        ts = q.get("ts")
        age = time.time() - ts if ts is not None else None
        return spread, age
    except Exception:
        return None, None


def _f(v: Any) -> float | None:
    """Safe float cast — returns None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
