# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_pipeline.py                        ║
# ║   Pure signal data pipeline: universe → Signal objects       ║
# ║                                                              ║
# ║   No IBKR dependency. No dash globals. No execution logic.  ║
# ║   Fully unit-testable in isolation.                          ║
# ║                                                              ║
# ║   Entry point: run_signal_pipeline()                         ║
# ║   Result type: SignalPipelineResult                          ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from signal_types import Signal, SIGNALS_LOG
from signals import score_universe, get_regime_threshold
from news import batch_news_sentiment
from learning import log_signal_scan

log = logging.getLogger("decifer.pipeline")

# Symbols always preserved through the TV pre-filter regardless of TV data.
# Keep in sync with scanner.CORE_SYMBOLS.
_PREFILTER_CORE = frozenset([
    "SPY", "QQQ", "IWM", "VXX",   # Macro ETFs
    "UVXY", "SVXY",                # Volatility
    "SPXS", "SQQQ",               # Inverse ETFs
    "IBIT", "BITO", "MSTR",       # Crypto proxies
    "GLD", "SLV", "USO", "COPX",  # Commodities
])


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
    rejected = {"no_tv_data": 0, "price": 0, "rec": 0, "rel_vol": 0, "rsi_dead": 0, "flat": 0, "no_structure": 0}

    for sym in universe:
        tv = tv_cache.get(sym)
        if not tv:
            rejected["no_tv_data"] += 1
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
            rejected["price"] += 1
            continue
        if rec is None or abs(rec) < 0.05:
            rejected["rec"] += 1
            continue  # Dead neutral
        if rel_vol is not None and rel_vol < 0.5:
            rejected["rel_vol"] += 1
            continue  # Very low volume
        if rsi is not None and 47 < rsi < 53:
            rejected["rsi_dead"] += 1
            continue  # RSI dead zone
        if change is not None and abs(change) < 0.1:
            rejected["flat"] += 1
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
            rejected["no_structure"] += 1
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

    # Always preserve CORE_SYMBOLS that were in the input universe — these include
    # inverse ETFs (SPXS/SQQQ/UVXY) and macro ETFs that must be scored every cycle
    # regardless of TV data availability or rank score.
    missed_core = (_PREFILTER_CORE & set(universe)) - set(result)
    if missed_core:
        result = list(set(result) | missed_core)
        log.info(f"CORE_SYMBOLS preserved through TV pre-filter: {sorted(missed_core)}")

    kills_summary = ", ".join(f"{k}={v}" for k, v in rejected.items() if v > 0)
    log.info(
        f"TV pre-filter: {pre_universe} → {len(result)} symbols "
        f"(top by |signal| × rel_vol, VWAP-confirmed) | kills: {kills_summary}"
    )
    return result


def _fetch_news(universe: list, timeout_sec: int = 8) -> dict:
    """
    Fetch news sentiment for up to 50 symbols with a hard timeout.
    Returns empty dict on error or if the fetch stalls past timeout_sec.
    A stalled news fetch used to block the entire scan pipeline; the timeout
    ensures at worst we skip news for one cycle rather than hanging indefinitely.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="news_fetch") as pool:
            future = pool.submit(batch_news_sentiment, universe[:50])
            sentiment = future.result(timeout=timeout_sec)
        hits = sum(1 for v in sentiment.values() if v.get("news_score", 0) > 0)
        log.info(f"News: {len(sentiment)} symbols scanned, {hits} with sentiment signal")
        return sentiment
    except _FuturesTimeout:
        log.warning(f"News sentiment fetch timed out after {timeout_sec}s — skipping this cycle")
        return {}
    except Exception as e:
        log.error(f"News sentiment error: {e}")
        return {}


def _fetch_social(universe: list, session: str) -> dict:
    """
    Fetch social sentiment for up to 50 symbols.
    Skipped during PRE_MARKET and AFTER_HOURS (Reddit/ApeWisdom inactive).
    Skipped when social dimension is disabled in config (saves 2-5s per scan).
    """
    if session in ("PRE_MARKET", "AFTER_HOURS"):
        return {}
    try:
        from config import CONFIG
        if not CONFIG.get("dimension_flags", {}).get("social", True):
            return {}
    except Exception:
        pass
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


def _get_edge_gate_adj() -> tuple:
    """
    Return (score_adj: int, state: str) from the IC edge gate.

    Reads system IC health from cached ic_weights.json (no live API call).
    state is one of: "healthy" | "degraded" | "broken" | "disabled" | "no_data"
    """
    try:
        from config import CONFIG
        ic_cfg = CONFIG.get("ic_calculator", {})
        if not ic_cfg.get("edge_gate_enabled", True):
            return 0, "disabled"
        from ic_calculator import get_system_ic_health
        health = get_system_ic_health()
        if health == 0.0:
            return 0, "no_data"
        off_thresh  = ic_cfg.get("edge_gate_off_threshold", 0.005)
        warn_thresh = ic_cfg.get("edge_gate_warn_threshold", 0.02)
        off_adj     = ic_cfg.get("edge_gate_off_adj", 12)
        warn_adj    = ic_cfg.get("edge_gate_warn_adj", 5)
        if health < off_thresh:
            return off_adj, "broken"
        if health < warn_thresh:
            return warn_adj, "degraded"
        return 0, "healthy"
    except Exception as e:
        log.debug(f"Edge gate check failed (non-critical): {e}")
        return 0, "no_data"


def _apply_strategy_threshold(
    scored: list, strategy_mode: dict, regime_name: str
) -> list:
    """
    Filter the scored list by the effective score threshold.

    The effective threshold = base regime threshold
                             + strategy_mode adjustment
                             + IC edge gate adjustment (system health)

    Edge gate raises the bar when the signal engine's rolling IC health drops,
    protecting capital when signals are less predictive than usual.
    """
    mode_adj = strategy_mode.get("score_threshold_adj", 0)
    edge_adj, edge_state = _get_edge_gate_adj()
    adj = mode_adj + edge_adj

    used_threshold = get_regime_threshold(regime_name)
    effective = used_threshold + adj

    parts = []
    if mode_adj:
        parts.append(f"mode={strategy_mode.get('mode','?')}+{mode_adj}")
    if edge_adj:
        parts.append(f"edge_gate={edge_state}+{edge_adj}")

    if adj > 0:
        pre = len(scored)
        filtered = [s for s in scored if s["score"] >= effective]
        reason = " | ".join(parts)
        log.info(
            f"Scored: {pre} → {len(filtered)} after threshold filter "
            f"(raised {used_threshold}→{effective} [{reason}])"
        )
        if edge_adj:
            log.warning(
                f"EDGE GATE [{edge_state.upper()}]: system IC health low — "
                f"score bar raised +{edge_adj} (need {effective} to trade)"
            )
        return filtered

    if edge_state not in ("healthy", "disabled", "no_data"):
        log.warning(f"EDGE GATE [{edge_state.upper()}]: system IC health low (adj={edge_adj})")

    log.info(
        f"Scored: {len(scored)} above threshold ({used_threshold}) "
        f"[{regime_name}] edge={edge_state}"
    )
    return scored


_SHORT_IC_PROVEN_THRESHOLD = 0.03  # IC quality score above which shorts are treated equally


def _apply_short_quality_gate(scored: list, regime_name: str) -> list:
    """
    Gate SHORT-direction signals when short IC is unproven.

    When get_short_quality_score() < _SHORT_IC_PROVEN_THRESHOLD (not yet proven),
    require SHORT signals to have score >= min_score_to_trade (same floor as longs).
    This removes the previous asymmetry where shorts needed 20 while longs needed 14,
    which created a circular trap: shorts couldn't trade → short IC stayed at 0.0.

    Once short IC is proven (>= _SHORT_IC_PROVEN_THRESHOLD), the gate relaxes entirely.
    """
    try:
        from ic_calculator import get_short_quality_score
        short_quality = get_short_quality_score()
    except Exception as e:
        log.debug(f"Short quality gate: could not fetch IC — skipping gate: {e}")
        return scored

    if short_quality >= _SHORT_IC_PROVEN_THRESHOLD:
        log.debug(f"Short quality gate: IC proven (quality={short_quality:.3f}) — no extra filter")
        return scored

    from config import CONFIG as _cfg
    short_min = _cfg.get("min_score_to_trade", 14)  # Match the long floor — no asymmetry

    pre = len(scored)
    result = []
    for s in scored:
        if s.get("direction") == "SHORT" and s.get("score", 0) < short_min:
            log.info(
                f"Short quality gate: {s['symbol']} score={s['score']} below "
                f"SHORT threshold {short_min} "
                f"(IC_short unproven: quality={short_quality:.3f})"
            )
            continue
        result.append(s)

    n_filtered = pre - len(result)
    if n_filtered > 0:
        log.info(
            f"Short quality gate: filtered {n_filtered} low-confidence short signals "
            f"(IC_short quality={short_quality:.3f} < {_SHORT_IC_PROVEN_THRESHOLD})"
        )
    return result


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
            atr_daily=s.get("atr_daily", 0.0),
            candle_gate=s.get("candle_gate", "UNKNOWN"),
            instrument=s.get("instrument", "stock"),
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
    ib=None,
) -> SignalPipelineResult:
    """
    Execute the full signal data pipeline for one scan cycle.

    Pipeline stages
    ---------------
    1. TV pre-filter      — cuts universe using TradingView indicators
    2. News sentiment     — Yahoo RSS + keyword scoring
    3. Social sentiment   — Reddit/ApeWisdom (skipped in extended hours + when disabled)
    4. Score universe     — 10-dimension yfinance scoring (alpha-pipeline-v2)
    4b. Small cap track   — supplemental $50M-$2B universe (if enabled)
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

    # 1b. Sympathy scanner — add sector peers when a leader has earnings within 48h
    try:
        from sympathy_scanner import get_sympathy_candidates
        sympathy_peers = get_sympathy_candidates(filtered)
        if sympathy_peers:
            filtered = filtered + sympathy_peers
            log.info("Sympathy scanner: %d peer(s) added to universe", len(sympathy_peers))
    except Exception as _sy_e:
        log.debug("Sympathy scanner skipped: %s", _sy_e)

    # 2. News sentiment (always)
    log.info("Fetching news sentiment (Yahoo RSS + keyword scoring)...")
    news_sentiment = _fetch_news(filtered)

    # 3. Social sentiment (gated on session)
    social_sentiment = _fetch_social(filtered, session)

    # 4. Score universe on 10 dimensions (alpha-pipeline-v2)
    regime_name = regime.get("regime", "UNKNOWN")
    log.info(f"Scoring universe on 10 dimensions [{regime_name}]...")
    scored, all_scored = score_universe(
        filtered,
        regime_name,
        news_data=news_sentiment,
        social_data=social_sentiment,
        regime_router=regime.get("regime_router", "unknown"),
        ib=ib,
    )
    log.info(f"score_universe: {len(scored)} above threshold, {len(all_scored)} total")

    # 4b. Small cap supplemental track ($50M–$2B market cap)
    try:
        from config import CONFIG as _cfg
        if _cfg.get("small_cap_enabled", False):
            from scanner import get_small_cap_universe
            sc_symbols = get_small_cap_universe()
            if sc_symbols:
                sc_threshold = _cfg.get("small_cap_min_score", 22)
                sc_scored, sc_all = score_universe(
                    sc_symbols,
                    regime_name,
                    news_data=news_sentiment,
                    social_data=social_sentiment,
                    regime_router=regime.get("regime_router", "unknown"),
                    ib=ib,
                )
                # Tag small cap results so downstream can apply tighter position sizing
                for s in sc_scored:
                    s["universe_track"] = "small_cap"
                for s in sc_all:
                    s["universe_track"] = "small_cap"
                # Filter by small cap threshold and merge (dedup by symbol)
                existing_syms = {s["symbol"] for s in scored}
                sc_above = [s for s in sc_scored
                            if s["score"] >= sc_threshold and s["symbol"] not in existing_syms]
                scored.extend(sc_above)
                sc_all_new = [s for s in sc_all if s["symbol"] not in {x["symbol"] for x in all_scored}]
                all_scored.extend(sc_all_new)
                log.info(f"Small cap track: {len(sc_above)} new candidates above {sc_threshold}")
    except Exception as _sc_e:
        log.debug(f"Small cap track skipped: {_sc_e}")

    # 4c. FX track — score major currency pairs (disabled by default)
    try:
        from config import CONFIG as _cfg
        if _cfg.get("fx_enabled", False):
            from fx_signals import score_fx_universe
            fx_scored = score_fx_universe(regime)
            if fx_scored:
                existing_syms = {s["symbol"] for s in scored}
                new_fx = [s for s in fx_scored if s["symbol"] not in existing_syms]
                scored.extend(new_fx)
                new_fx_all = [s for s in fx_scored if s["symbol"] not in {x["symbol"] for x in all_scored}]
                all_scored.extend(new_fx_all)
                log.info("FX track: %d pair(s) above threshold", len(new_fx))
    except Exception as _fx_e:
        log.debug("FX track skipped: %s", _fx_e)

    # 5. Strategy-mode threshold adjustment
    scored = _apply_strategy_threshold(scored, strategy_mode, regime_name)

    # 5b. Short quality gate — raise bar for SHORT signals when IC is unproven
    scored = _apply_short_quality_gate(scored, regime_name)

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
