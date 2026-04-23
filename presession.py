# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  presession.py                              ║
# ║   Pre-session catalyst pipeline (fires at 08:00 ET)          ║
# ║                                                              ║
# ║   Pulls top candidates from CatalystEngine, runs the 3-agent ║
# ║   sentinel on each, logs decisions for next-day IC analysis. ║
# ║                                                              ║
# ║   Phase 3a: DRY-RUN only (logs decisions, no orders).        ║
# ║   Phase 3b will add MOO execution once dry-run data is clean.║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Pre-session catalyst pipeline.

Rationale:
  The CatalystEngine scores candidates in the background (fundamental +
  options anomaly + EDGAR + sentiment) but nothing currently hands the
  top-ranked ones to the 4-agent pipeline before 09:30 ET. By the time
  intraday scans fire, overnight alpha has already been priced in.

Flow:
  1. Guard: trading day only (skip weekends, holidays).
  2. Pull top-N candidates from bot_state._catalyst_engine.store,
     threshold by catalyst_score >= presession_catalyst_score_floor.
  3. Enrich each with earnings-within-24h flag + pre-market snapshot
     (price vs prior close).
  4. Build a catalyst-flavoured trigger and run the 3-agent sentinel
     (Catalyst Analyst + Risk Gate + Instant Decision).
  5. In dry-run (Phase 3a default): log candidate + decision to
     data/presession_log.jsonl. No orders placed.
  6. (Phase 3b): for each approved decision, place MOO or pre-market
     limit per execution-mode logic.

Safety:
  - Master gated by CONFIG['presession_enabled'] (default True).
  - Order execution gated by CONFIG['presession_dry_run'] (default True
    — ship safe, flip after validation).
  - All errors caught at the top level; main bot loop continues even
    if presession blows up.
"""

from __future__ import annotations

import json
import logging
import pathlib
from datetime import UTC, datetime
from typing import Any

from config import CONFIG

log = logging.getLogger("decifer.presession")

_PRESESSION_LOG = "data/presession_log.jsonl"


# ══════════════════════════════════════════════════════════════
# CANDIDATE COLLECTION
# ══════════════════════════════════════════════════════════════


def _collect_candidates(engine: Any, top_n: int, score_floor: float) -> list[dict]:
    """
    Pull top-N candidates from the CatalystEngine store.

    Returns a list of candidate dicts sorted by catalyst_score desc.
    Candidate dicts carry fundamental_score, options_anomaly_score,
    edgar_score, sentiment_score, and composite catalyst_score (0-10).

    Returns [] if the engine isn't running or has no candidates.
    """
    if engine is None:
        log.info("presession: catalyst engine not started — skipping")
        return []

    try:
        tickers = engine.store.all_tickers() if hasattr(engine, "store") else engine.all_tickers()
    except Exception as exc:
        log.warning(f"presession: failed to list candidates — {exc}")
        return []

    candidates = []
    for tk in tickers:
        try:
            c = engine.get_candidate(tk) if hasattr(engine, "get_candidate") else engine.store.get(tk)
        except Exception:
            continue
        if not c:
            continue
        cs = float(c.get("catalyst_score", 0) or 0)
        if cs < score_floor:
            continue
        candidates.append(c)

    candidates.sort(key=lambda c: c.get("catalyst_score", 0), reverse=True)
    return candidates[:top_n]


# ══════════════════════════════════════════════════════════════
# ENRICHMENT
# ══════════════════════════════════════════════════════════════


def _enrich_candidates(candidates: list[dict]) -> list[dict]:
    """
    Attach earnings-within-window flag and pre-market snapshot data
    (price, % change vs prior close) to each candidate. Failures here
    are non-fatal — the candidate is returned with whatever enrichment
    succeeded, and missing fields stay absent rather than silently zero.
    """
    if not candidates:
        return candidates

    syms = [c["ticker"] for c in candidates if c.get("ticker")]

    # Earnings within lookahead window
    try:
        from earnings_calendar import get_earnings_within_hours

        earn_window = CONFIG.get("presession_earnings_lookahead_hours", 24)
        earnings_flagged = get_earnings_within_hours(syms, earn_window)
    except Exception as exc:
        log.debug(f"presession: earnings enrichment failed — {exc}")
        earnings_flagged = set()

    # Pre-market snapshots
    try:
        from alpaca_data import fetch_snapshots

        snaps = fetch_snapshots(syms)
    except Exception as exc:
        log.debug(f"presession: snapshot enrichment failed — {exc}")
        snaps = {}

    enriched = []
    for c in candidates:
        sym = c.get("ticker", "")
        snap = snaps.get(sym, {})
        enriched.append(
            {
                **c,
                "earnings_within_window": sym in earnings_flagged,
                "premarket_price": snap.get("price"),
                "premarket_change_1d": snap.get("change_1d"),
            }
        )
    return enriched


# ══════════════════════════════════════════════════════════════
# TRIGGER CONSTRUCTION (for the 3-agent sentinel)
# ══════════════════════════════════════════════════════════════


def _build_trigger(candidate: dict) -> dict:
    """
    Build a trigger dict in the shape sentinel_agents.run_sentinel_pipeline()
    expects. Catalyst-driven triggers don't have news headlines, so we
    synthesize a short "why this is on the list" description from the
    sub-scores.

    The sentinel prompt expects: symbol, headlines, keyword_score,
    direction, urgency, claude_sentiment, claude_confidence, claude_catalyst.
    """
    sym = candidate.get("ticker", "?")
    cs = float(candidate.get("catalyst_score", 0) or 0)

    # Build a compact description of what drove the catalyst score.
    drivers = []
    if candidate.get("fundamental_score", 0) >= 3.5:
        drivers.append(f"fundamentals {candidate['fundamental_score']:.1f}/5")
    if candidate.get("options_anomaly_score", 0) >= 3.5:
        drivers.append(f"options-flow {candidate['options_anomaly_score']:.1f}/5")
    if candidate.get("edgar_score", 0) >= 3.5:
        drivers.append(f"EDGAR filing {candidate['edgar_score']:.1f}/5")
    if candidate.get("sentiment_score", 0) >= 3.5:
        drivers.append(f"sentiment {candidate['sentiment_score']:.1f}/5")

    driver_str = ", ".join(drivers) if drivers else "composite catalyst score"

    pre_px = candidate.get("premarket_price")
    pre_chg = candidate.get("premarket_change_1d")
    pre_note = ""
    if pre_px is not None and pre_chg is not None:
        pre_note = f"  Pre-market: ${pre_px:.2f} ({pre_chg * 100:+.2f}% vs prior close)."
    earn_note = "  **EARNINGS within 24h**." if candidate.get("earnings_within_window") else ""

    headline = (
        f"[PRE-SESSION CATALYST] {sym} on watchlist — {driver_str} "
        f"(composite {cs:.1f}/10).{pre_note}{earn_note}"
    )

    return {
        "symbol": sym,
        "headlines": [headline],
        "keyword_score": int(cs),  # sentinel uses this as a materiality proxy
        "direction": "LONG",  # catalyst engine is long-biased by construction
        "urgency": "MEDIUM",  # not a breaking-news emergency; it's pre-session
        "claude_sentiment": "positive" if cs >= 7 else "neutral",
        "claude_confidence": min(10, max(1, int(cs))),
        "claude_catalyst": driver_str,
        "trigger_source": "presession_pipeline",
        "catalyst_score": cs,
    }


# ══════════════════════════════════════════════════════════════
# LOG WRITER
# ══════════════════════════════════════════════════════════════


def _log_decision(candidate: dict, trigger: dict, decision: dict, dry_run: bool) -> None:
    """Append one candidate→decision record to data/presession_log.jsonl.

    Used for next-day IC analysis: did the catalyst_score predict T+1 return?
    Did the sentinel's action correlate with P&L? Without this log there's
    no way to tune thresholds later.
    """
    try:
        pathlib.Path("data").mkdir(exist_ok=True)
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "symbol": candidate.get("ticker"),
            "catalyst_score": candidate.get("catalyst_score"),
            "fundamental_score": candidate.get("fundamental_score"),
            "options_anomaly_score": candidate.get("options_anomaly_score"),
            "edgar_score": candidate.get("edgar_score"),
            "sentiment_score": candidate.get("sentiment_score"),
            "earnings_within_window": candidate.get("earnings_within_window"),
            "premarket_price": candidate.get("premarket_price"),
            "premarket_change_1d": candidate.get("premarket_change_1d"),
            "sentinel_action": decision.get("action"),
            "sentinel_confidence": decision.get("confidence"),
            "sentinel_reasoning": (decision.get("reasoning") or "")[:300],
            "dry_run": dry_run,
            "executed": False,  # flipped True in Phase 3b when we actually place orders
        }
        with open(_PRESESSION_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as exc:
        log.warning(f"presession log write failed for {candidate.get('ticker')}: {exc}")


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════


def presession_catalyst_pipeline() -> dict:
    """
    Main entry point, invoked by the scheduler at 08:00 ET each day.

    Returns a summary dict for logging — number of candidates, decisions, etc.
    Top-level try/except ensures a pipeline failure never wedges the main bot.
    """
    summary = {
        "ts": datetime.now(UTC).isoformat(),
        "fired": False,
        "reason": "",
        "candidates_total": 0,
        "candidates_reviewed": 0,
        "sentinel_approvals": 0,
        "dry_run": True,
    }

    try:
        if not CONFIG.get("presession_enabled", True):
            summary["reason"] = "presession_disabled"
            return summary

        # Trading-day guard — no run on weekends/holidays.
        try:
            from risk import is_trading_day

            if not is_trading_day():
                summary["reason"] = "not_trading_day"
                log.info("presession: skipping — not a trading day")
                return summary
        except Exception as exc:
            log.warning(f"presession: trading-day check failed, proceeding cautiously — {exc}")

        dry_run = bool(CONFIG.get("presession_dry_run", True))
        summary["dry_run"] = dry_run
        top_n = int(CONFIG.get("presession_top_n", 20))
        score_floor = float(CONFIG.get("presession_catalyst_score_floor", 7.0))

        # Pull the running catalyst engine from bot_state (set in bot.py:462).
        try:
            import bot_state

            engine = getattr(bot_state, "_catalyst_engine", None)
        except Exception:
            engine = None

        candidates = _collect_candidates(engine, top_n=top_n, score_floor=score_floor)
        summary["candidates_total"] = len(candidates)

        if not candidates:
            summary["reason"] = "no_candidates_above_floor"
            log.info("presession: no candidates above score floor %.1f — nothing to review", score_floor)
            return summary

        log.info(
            "presession: %d candidate(s) above floor %.1f — %s",
            len(candidates),
            score_floor,
            ", ".join(f"{c['ticker']}({c['catalyst_score']:.1f})" for c in candidates[:10]),
        )

        candidates = _enrich_candidates(candidates)

        # Gather sentinel context — we want the same trading context the
        # sentinel pipeline would use during RTH.
        try:
            from orders_state import get_open_positions

            open_positions = get_open_positions()
        except Exception:
            open_positions = []

        try:
            from signals import get_market_regime_vix

            regime = get_market_regime_vix()
        except Exception:
            regime = {"regime": "UNKNOWN", "vix": 0}

        portfolio_value = float(CONFIG.get("starting_capital", 1_000_000))
        daily_pnl = 0.0  # pre-session; PnL hasn't started accruing yet

        # Import sentinel lazily so a presession failure doesn't break imports
        # elsewhere. anthropic client instantiation happens here.
        try:
            from sentinel_agents import run_sentinel_pipeline
        except Exception as exc:
            summary["reason"] = f"sentinel_import_failed:{exc}"
            log.error("presession: sentinel import failed — %s", exc)
            return summary

        approvals = 0
        for candidate in candidates:
            sym = candidate.get("ticker", "?")
            try:
                trigger = _build_trigger(candidate)
                # Phase 5 completion: legacy sentinel pipeline gated.
                try:
                    import safety_overlay as _so_ps
                    _ps_legacy_on = _so_ps.sentinel_legacy_pipeline_enabled()
                except Exception:
                    _ps_legacy_on = True
                if _ps_legacy_on:
                    decision = run_sentinel_pipeline(
                        trigger=trigger,
                        open_positions=open_positions,
                        portfolio_value=portfolio_value,
                        daily_pnl=daily_pnl,
                        regime=regime,
                    )
                else:
                    decision = {
                        "action": "SKIP",
                        "symbol": sym,
                        "qty": 0,
                        "confidence": 0,
                        "reasoning": "sentinel legacy pipeline disabled",
                        "trigger_type": "presession",
                    }
                action = decision.get("action", "SKIP")
                if action and action.upper() not in ("SKIP", "NO_TRADE", "HOLD"):
                    approvals += 1

                _log_decision(candidate, trigger, decision, dry_run)
                log.info(
                    "presession: %s score=%.1f → sentinel=%s conf=%s",
                    sym,
                    candidate.get("catalyst_score", 0),
                    action,
                    decision.get("confidence"),
                )
            except Exception as exc:
                log.warning("presession: %s sentinel failed — %s", sym, exc)
                # Still log the failure so IC analysis sees the full set.
                _log_decision(candidate, {}, {"action": "ERROR", "reasoning": str(exc)}, dry_run)

        summary["candidates_reviewed"] = len(candidates)
        summary["sentinel_approvals"] = approvals
        summary["fired"] = True

        if dry_run:
            log.info(
                "presession: DRY-RUN complete — %d reviewed, %d would-trade (no orders placed)",
                len(candidates),
                approvals,
            )
        else:
            # Phase 3b: wire order execution here.
            log.warning(
                "presession: dry_run=False but execution path not implemented yet (Phase 3b). "
                "No orders placed."
            )

        return summary

    except Exception as exc:
        log.exception("presession: pipeline error — %s", exc)
        summary["reason"] = f"pipeline_error:{exc}"
        return summary
