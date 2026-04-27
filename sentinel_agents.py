"""
sentinel_agents.py — Apex NEWS_INTERRUPT payload builder.

The legacy 3-agent sentinel pipeline (agent_catalyst, agent_risk_gate,
agent_instant_decision, run_sentinel_pipeline) was removed at Decifer 3.0
cutover (2026-04-24). Only build_news_trigger_payload remains — a pure
function that shapes trigger data into an ApexInput dict for apex_call().
"""

import logging

from config import CONFIG

log = logging.getLogger("decifer.sentinel_agents")


def build_news_trigger_payload(
    trigger: dict,
    open_positions: list | None = None,
    portfolio_value: float = 0.0,
    daily_pnl: float = 0.0,
    regime: dict | None = None,
    scored_candidate: dict | None = None,
    overnight_research: str | None = None,
) -> dict:
    """
    Build an ApexInput dict for a NEWS_INTERRUPT trigger.

    Pure function — no LLM call, no dispatch, no side effects.

    Output shape (ApexInput consumed by apex_call):
      {
        "trigger_type": "NEWS_INTERRUPT",
        "trigger_context": {...},
        "track_a": {"candidates": [...]},
        "track_b": [],
        "market_context": {...},
        "portfolio_state": {...},
        "scan_ts": "<ISO UTC>"
      }
    """
    from datetime import UTC, datetime

    sym = trigger.get("symbol", "")
    open_positions = list(open_positions or [])
    regime = regime or {}

    trigger_context = {
        "symbol": sym,
        "headline": (trigger.get("headlines") or [None])[0],
        "headlines": trigger.get("headlines") or [],
        "keyword_score": trigger.get("keyword_score"),
        "direction": trigger.get("direction"),
        "urgency": trigger.get("urgency"),
        "sources": trigger.get("sources") or [],
        "finbert_sentiment": trigger.get("claude_sentiment"),
        "finbert_confidence": trigger.get("claude_confidence"),
        "catalyst_summary": trigger.get("claude_catalyst"),
    }

    candidates = [scored_candidate] if scored_candidate else []

    market_context = {
        "regime": regime,
        "overnight_research": overnight_research,
    }

    gross_long = sum(
        float(p.get("current") or p.get("entry") or 0) * int(p.get("qty") or 0)
        for p in open_positions if (p.get("direction") or "LONG").upper() == "LONG"
    )
    gross_short = sum(
        float(p.get("current") or p.get("entry") or 0) * int(p.get("qty") or 0)
        for p in open_positions if (p.get("direction") or "LONG").upper() == "SHORT"
    )
    max_positions = int(CONFIG.get("max_positions", 0) or 0)

    portfolio_state = {
        "portfolio_value": portfolio_value,
        "daily_pnl": daily_pnl,
        "position_count": len(open_positions),
        "position_slots_remaining": max(0, max_positions - len(open_positions)),
        "gross_long_notional": gross_long,
        "gross_short_notional": gross_short,
        "net_exposure_pct": (
            (gross_long - gross_short) / portfolio_value if portfolio_value else 0.0
        ),
        "open_positions": open_positions,
    }

    apex_input = {
        "trigger_type": "NEWS_INTERRUPT",
        "trigger_context": trigger_context,
        "track_a": {"candidates": candidates},
        "track_b": [],
        "market_context": market_context,
        "portfolio_state": portfolio_state,
        "scan_ts": datetime.now(UTC).isoformat(),
    }

    log.debug("build_news_trigger_payload: sym=%s urgency=%s candidates=%d",
              sym, trigger.get("urgency"), len(candidates))
    return apex_input
