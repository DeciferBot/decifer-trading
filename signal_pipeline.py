# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_pipeline.py                        ║
# ║   Pure signal data pipeline: universe → Signal objects       ║
# ║                                                              ║
# ║   No IBKR dependency. No dash globals. No execution logic.  ║
# ║   Fully unit-testable in isolation.                          ║
# ║                                                              ║
# ║   Entry point: run_signal_pipeline()                         ║
# ║   Result type: SignalPipelineResult                          ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from signal_types import Signal, SIGNALS_LOG
from signals import score_universe, get_regime_threshold
from news import batch_news_sentiment
from learning import log_signal_scan

log = logging.getLogger("decifer.pipeline")


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class SignalPipelineResult:
    """
    Output of run_signal_pipeline().  All fields are read-only snapshots
    of the pipeline state at the end of one scan cycle.

    Fields
    ------
    signals       : List[Signal] — typed Signal objects above the score threshold.
                    Consumed by signal_dispatcher for order routing.
    scored        : List[dict]  — raw scored dicts above the score threshold.
                    Consumed by run_all_agents() (which expects raw dicts) and
                    update_position_prices().
    all_scored    : List[dict]  — all scored symbols including below-threshold.
                    Consumed by log_signal_scan() for IC tracking (already called
                    inside the pipeline) and exposed here for any caller that needs
                    the full picture.
    news_sentiment: dict — symbol → sentiment dict from Yahoo RSS scoring.
                    Written to dash["news_data"] by the bot orchestrator.
    universe      : list — final filtered symbol list after TV pre-filter.
                    Consumed by the options scanner.
    regime_name   : str  — regime label at scoring time (e.g. "BULL_TRENDING").
    """
    signals: list
    scored: list
    all_scored: list
    news_sentiment: dict
    universe: list
    regime_name: str


# ── Internal helpers ───────────────────────────────────────────────────────────

def _apply_tv_prefilter(universe: list, tv_cache: dict, favourites: list) -> list:
    """
    Use pre-fetched TradingView indicator data to cut universe before the
    expensive yfinance multi-timeframe scoring pass.

    Goal: ~97 symbols → ~10-25 high-potential candidates.
    Returns universe unchanged when tv_cache is empty (no TV data available).

    All inputs are parameters — no globals, no side effects.
    """
    if not tv_cache:
        return universe

    pre_universe = len(universe)
    ranked = []

    for sym in universe:
        tv = tv_cache.get(sym)
        if not tv:
            continue  # No TV data → skip (CORE_SYMBOLS without TV hits)

        close   = tv.get("tv_close")
        rec     = tv.get("tv_recommend")
        rel_vol = tv.get("tv_rel_vol")
        rsi     = tv.get("tv_rsi_1h")
        ema9    = tv.get("tv_ema9_1h")
        ema21   = tv.get("tv_ema21_1h")
        macd    = tv.get("tv_macd_1h")
        macd_s  = tv.get("tv_macd_sig_1h")
        change  = tv.get("tv_change")
        vwap    = tv.get("tv_vwap")

        # ── Hard kills — no edge, don't waste yfinance calls ──────────────
        if close is None or close <= 0:
            continue
        if rec is None or abs(rec) < 0.05:
            continue  # Dead neutral
        if rel_vol is not None and rel_vol < 0.5:
            continue  # Very low volume
        if rsi is not None and 47 < rsi < 53:
            continue  # RSI dead zone
        if change is not None and abs(change) < 0.1:
            continue  # Truly flat

        # ── EMA alignment — need some trend structure ──────────────────────
        ema_aligned = (
            ema9 is not None and ema21 is not None
            and ema9 != 0 and ema21 != 0
            and abs(ema9 - ema21) / max(ema9, ema21) > 0.001
        )

        # ── MACD thrust — need some acceleration ───────────────────────────
        macd_thrust = (
            macd is not None and macd_s is not None
            and abs(macd - macd_s) > 0.01
        )

        if not ema_aligned and not macd_thrust:
            continue

        # ── Rank score: |signal| × unusual volume, VWAP-confirmed ─────────
        # Treat missing rel_vol as neutral (1.0) so the symbol isn't penalised
        # for a TV data gap while still participating in ranking.
        rank_score = abs(rec) * (rel_vol if rel_vol is not None else 1.0)
        if vwap and close and vwap > 0:
            if (rec > 0 and close > vwap) or (rec < 0 and close < vwap):
                rank_score *= 1.3  # 30% VWAP alignment bonus

        ranked.append((sym, rank_score))

    # Top 25 by rank score
    ranked.sort(key=lambda x: x[1], reverse=True)
    result = [sym for sym, _ in ranked[:25]]

    # Always preserve favourites — never let pre-filter drop them
    favs_set = set(favourites)
    missed_favs = favs_set - set(result)
    if missed_favs:
        result = list(set(result) | missed_favs)
        log.info(f"Favourites preserved through TV pre-filter: {sorted(missed_favs)}")

    log.info(
        f"TV pre-filter: {pre_universe} → {len(result)} symbols "
        f"(top by |signal| × rel_vol, VWAP-confirmed)"
    )
    return result


def _fetch_news(universe: list) -> dict:
    """Fetch news sentiment for up to 50 symbols. Returns empty dict on error."""
    try:
        sentiment = batch_news_sentiment(universe[:50])
        hits = sum(1 for v in sentiment.values() if v.get("news_score", 0) > 0)
        log.info(f"News: {len(sentiment)} symbols scanned, {hits} with sentiment signal")
        return sentiment
    except Exception as e:
        log.error(f"News sentiment error: {e}")
        return {}


def _fetch_social(universe: list, session: str) -> dict:
    """
    Fetch social sentiment for up to 50 symbols.
    Skipped during PRE_MARKET and AFTER_HOURS (Reddit/ApeWisdom inactive).
    """
    if session in ("PRE_MARKET", "AFTER_HOURS"):
        return {}
    try:
        from social_sentiment import get_social_sentiment
        sentiment = get_social_sentiment(universe[:50])
        hits = sum(1 for v in sentiment.values() if v.get("social_score", 0) > 0)
        log.info(f"Social: {len(sentiment)} symbols scanned, {hits} with sentiment signal")
        return sentiment
    except ImportError:
        log.info("Social sentiment module not available — skipping")
        return {}
    except Exception as e:
        log.error(f"Social sentiment error: {e}")
        return {}


def _apply_strategy_threshold(
    scored: list, strategy_mode: dict, regime_name: str
) -> list:
    """
    Filter the scored list by the effective score threshold.

    The effective threshold = base regime threshold + strategy_mode adjustment.
    When adjustment is zero the list is returned unchanged (no copy).
    """
    adj = strategy_mode.get("score_threshold_adj", 0)
    used_threshold = get_regime_threshold(regime_name)
    effective = used_threshold + adj

    if adj > 0:
        pre = len(scored)
        filtered = [s for s in scored if s["score"] >= effective]
        log.info(
            f"Scored: {pre} → {len(filtered)} after strategy mode filter "
            f"(threshold raised {used_threshold}→{effective}/50 in "
            f"{strategy_mode.get('mode', '?')} mode)"
        )
        return filtered

    log.info(
        f"Scored: {len(scored)} above threshold ({used_threshold}/50) [{regime_name}]"
    )
    return scored


def _scored_to_signals(scored: list, regime_name: str) -> list:
    """Convert score_universe() raw dicts → typed Signal objects."""
    now = datetime.now(timezone.utc)
    signals = []
    for s in scored:
        direction = s.get("direction", "NEUTRAL")
        if direction not in ("LONG", "SHORT", "NEUTRAL"):
            direction = "NEUTRAL"
        signals.append(Signal(
            symbol=s["symbol"],
            direction=direction,
            conviction_score=round(s.get("score", 0) / 5.0, 3),
            dimension_scores=s.get("score_breakdown", {}),
            timestamp=now,
            regime_context=regime_name,
            price=s.get("price", 0.0),
            atr=s.get("atr", 0.0),
        ))
    return signals


def _append_signals_log(signals: list, log_path: str) -> None:
    """Append typed Signal objects to the signals log (one JSON line each)."""
    if not signals:
        return
    try:
        with open(log_path, "a") as f:
            for s in signals:
                f.write(s.to_json() + "\n")
    except Exception as e:
        log.warning(f"typed signals_log write failed: {e}")


# ── Public entry point ─────────────────────────────────────────────────────────

def run_signal_pipeline(
    universe: list,
    regime: dict,
    strategy_mode: dict,
    session: str,
    favourites: list,
    tv_cache: dict,
    signals_log_path: str = SIGNALS_LOG,
) -> SignalPipelineResult:
    """
    Execute the full signal data pipeline for one scan cycle.

    Pipeline stages
    ---------------
    1. TV pre-filter      — cuts universe using TradingView indicators
    2. News sentiment     — Yahoo RSS + keyword scoring
    3. Social sentiment   — Reddit/ApeWisdom (skipped in extended hours)
    4. Score universe     — 9-dimension yfinance scoring
    5. Strategy threshold — raise bar in defensive strategy modes
    6. IC audit log       — write all_scored to learning log (side effect)
    7. Typed signals      — convert scored dicts → Signal objects
    8. Signals log        — append to signals_log.jsonl (side effect)

    Parameters
    ----------
    universe         : symbol list (pre-TV-filter)
    regime           : dict from get_market_regime()
    strategy_mode    : dict from get_intraday_strategy_mode()
    session          : session label from get_session()
    favourites       : always-included symbols from dash["favourites"]
    tv_cache         : dict from get_tv_signal_cache()
    signals_log_path : path to the typed signals JSONL log

    Returns
    -------
    SignalPipelineResult — see dataclass docstring for field details.

    Guarantees
    ----------
    - No IBKR calls
    - No dash globals read or written
    - No order execution
    - All side effects are file writes (signals_log, IC log)
    """
    # 1. TV pre-filter
    filtered = _apply_tv_prefilter(universe, tv_cache, favourites)
    log.info(f"Universe after TV pre-filter: {len(filtered)} symbols")

    # 2. News sentiment (always)
    log.info("Fetching news sentiment (Yahoo RSS + keyword scoring)...")
    news_sentiment = _fetch_news(filtered)

    # 3. Social sentiment (gated on session)
    social_sentiment = _fetch_social(filtered, session)

    # 4. Score universe on 9 dimensions
    regime_name = regime.get("regime", "UNKNOWN")
    log.info(f"Scoring universe on 9 dimensions [{regime_name}]...")
    scored, all_scored = score_universe(
        filtered,
        regime_name,
        news_data=news_sentiment,
        social_data=social_sentiment,
        regime_router=regime.get("regime_router", "unknown"),
    )
    log.info(f"score_universe: {len(scored)} above threshold, {len(all_scored)} total")

    # 5. Strategy-mode threshold adjustment
    scored = _apply_strategy_threshold(scored, strategy_mode, regime_name)

    # 6. IC audit log — write all scored symbols for forward-return tracking
    log_signal_scan(all_scored, regime)

    # 7. Build typed Signal objects
    signals = _scored_to_signals(scored, regime_name)

    # 8. Append to signals_log.jsonl for IC calculator
    _append_signals_log(signals, log_path=signals_log_path)

    return SignalPipelineResult(
        signals=signals,
        scored=scored,
        all_scored=all_scored,
        news_sentiment=news_sentiment,
        universe=filtered,
        regime_name=regime_name,
    )
