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

import json
import logging
import os
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from learning import log_signal_scan
from news import batch_news_sentiment
from signal_types import SIGNALS_LOG, Signal
from signals import get_regime_threshold, score_universe

log = logging.getLogger("decifer.pipeline")

# ── Score persistence tracking ─────────────────────────────────────────────────
# Tracks whether each symbol was above the score threshold in recent scan cycles.
# Populated every scan regardless of threshold outcome so the history is accurate.
# RB-6: Persisted to disk (data/threshold_history.json) so bot restarts don't
# reset all histories — marginal signals that were building toward the persistence
# gate don't lose their progress across a restart.
_THRESHOLD_HISTORY: dict = {}  # symbol → deque[bool]
_THRESHOLD_HISTORY_MAXLEN = 4   # keep last 4 scans (enough for persistence_scans=3)
_THRESHOLD_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "threshold_history.json"
)
_THRESHOLD_HISTORY_MAX_STALENESS_MIN = 30  # discard entries older than this on load


def _load_threshold_history() -> None:
    """Load _THRESHOLD_HISTORY from disk, discarding stale entries (> max_staleness)."""
    global _THRESHOLD_HISTORY
    if not os.path.exists(_THRESHOLD_HISTORY_PATH):
        return
    try:
        with open(_THRESHOLD_HISTORY_PATH) as f:
            raw = json.load(f)
        saved_at_str = raw.get("saved_at")
        if not saved_at_str:
            return
        saved_at = datetime.fromisoformat(saved_at_str)
        age_min = (datetime.now(UTC) - saved_at).total_seconds() / 60
        if age_min > _THRESHOLD_HISTORY_MAX_STALENESS_MIN:
            log.debug("threshold_history.json is %.0fm old (> %dm) — discarding on load",
                      age_min, _THRESHOLD_HISTORY_MAX_STALENESS_MIN)
            return
        for sym, bits in raw.get("history", {}).items():
            d = deque(maxlen=_THRESHOLD_HISTORY_MAXLEN)
            d.extend(bits[-_THRESHOLD_HISTORY_MAXLEN:])
            _THRESHOLD_HISTORY[sym] = d
        log.debug("Loaded threshold history for %d symbols (%.0fm old)", len(_THRESHOLD_HISTORY), age_min)
    except Exception as e:
        log.debug("Failed to load threshold_history.json (non-critical): %s", e)


def _save_threshold_history() -> None:
    """Persist _THRESHOLD_HISTORY to disk atomically."""
    try:
        payload = {
            "saved_at": datetime.now(UTC).isoformat(),
            "history": {sym: list(hist) for sym, hist in _THRESHOLD_HISTORY.items()},
        }
        os.makedirs(os.path.dirname(_THRESHOLD_HISTORY_PATH), exist_ok=True)
        _dir = os.path.dirname(_THRESHOLD_HISTORY_PATH)
        _fd, _tmp = tempfile.mkstemp(dir=_dir, suffix=".tmp")
        with os.fdopen(_fd, "w") as f:
            json.dump(payload, f)
        os.replace(_tmp, _THRESHOLD_HISTORY_PATH)
    except Exception as e:
        log.debug("Failed to save threshold_history.json (non-critical): %s", e)


# Load persisted history on module import (runs once at bot startup).
_load_threshold_history()

# Symbols that must always be in the scan universe. Authoritative — computed
# from scanner's CORE_SYMBOLS + CORE_EQUITIES so the two lists can never drift.
# Used as an always-in-universe assertion for tests; Tier A (scanner.get_dynamic_universe)
# is responsible for actually injecting these symbols.
try:
    from scanner import CORE_EQUITIES as _SCANNER_CORE_EQUITIES
    from scanner import CORE_SYMBOLS as _SCANNER_CORE_SYMBOLS

    _PREFILTER_CORE = frozenset(_SCANNER_CORE_SYMBOLS) | frozenset(_SCANNER_CORE_EQUITIES)
except Exception:  # pragma: no cover — scanner import failure is fatal elsewhere
    # Minimal ETF fallback so the module still loads in isolated unit tests.
    _PREFILTER_CORE = frozenset(
        [
            "SPY", "QQQ", "IWM", "VXX",
            "UVXY", "SVXY",
            "SPXS", "SQQQ",
            "IBIT", "BITO", "MSTR",
            "GLD", "SLV", "USO", "COPX",
        ]
    )


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
    universe      : list — the scan universe used for scoring.
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


def _fetch_news(universe: list, timeout_sec: int = 8) -> dict:
    """
    Fetch news sentiment for up to 50 symbols with a hard timeout.
    Returns empty dict on error or if the fetch stalls past timeout_sec.
    A stalled news fetch used to block the entire scan pipeline; the timeout
    ensures at worst we skip news for one cycle rather than hanging indefinitely.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as _FuturesTimeout

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
        off_thresh = ic_cfg.get("edge_gate_off_threshold", 0.005)
        warn_thresh = ic_cfg.get("edge_gate_warn_threshold", 0.02)
        off_adj = ic_cfg.get("edge_gate_off_adj", 12)
        warn_adj = ic_cfg.get("edge_gate_warn_adj", 5)
        if health < off_thresh:
            return off_adj, "broken"
        if health < warn_thresh:
            return warn_adj, "degraded"
        return 0, "healthy"
    except Exception as e:
        log.debug(f"Edge gate check failed (non-critical): {e}")
        return 0, "no_data"


def _apply_strategy_threshold(scored: list, strategy_mode: dict, regime_name: str) -> list:
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
        parts.append(f"mode={strategy_mode.get('mode', '?')}+{mode_adj}")
    if edge_adj:
        parts.append(f"edge_gate={edge_state}+{edge_adj}")

    if adj > 0:
        pre = len(scored)
        filtered = [s for s in scored if s["score"] >= effective]
        reason = " | ".join(parts)
        log.info(
            f"Scored: {pre} → {len(filtered)} after threshold filter (raised {used_threshold}→{effective} [{reason}])"
        )
        if edge_adj:
            log.warning(
                f"EDGE GATE [{edge_state.upper()}]: system IC health low — "
                f"score bar raised +{edge_adj} (need {effective} to trade)"
            )
        return filtered

    if edge_state not in ("healthy", "disabled", "no_data"):
        log.warning(f"EDGE GATE [{edge_state.upper()}]: system IC health low (adj={edge_adj})")

    log.info(f"Scored: {len(scored)} above threshold ({used_threshold}) [{regime_name}] edge={edge_state}")
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


def _apply_persistence_gate(scored: list, all_scored: list, persistence_scans: int) -> list:
    """
    Filter signals that haven't been above threshold for persistence_scans consecutive scans.

    High-conviction signals (score >= persistence_conviction_bypass) bypass the gate and
    pass immediately on scan 1 — strong setups shouldn't be held back by history warmup.
    Marginal signals (below the bypass threshold) still require persistence_scans
    consecutive above-threshold scans to filter single-scan DAR spikes.

    Updates _THRESHOLD_HISTORY for every symbol in all_scored (not just above-threshold)
    so the history accurately reflects when a signal dropped below threshold.
    Catalyst/sentinel entries bypass this gate in signal_dispatcher before reaching here.
    """
    if persistence_scans <= 1:
        return scored

    from config import CONFIG as _cfg
    bypass_score = _cfg.get("persistence_conviction_bypass", _cfg.get("high_conviction_score", 36))

    above = {s["symbol"] for s in scored}

    for s in all_scored:
        sym = s["symbol"]
        if sym not in _THRESHOLD_HISTORY:
            _THRESHOLD_HISTORY[sym] = deque(maxlen=_THRESHOLD_HISTORY_MAXLEN)
        _THRESHOLD_HISTORY[sym].append(sym in above)

    passed = []
    bypassed = []
    for s in scored:
        sym = s["symbol"]
        if s.get("score", 0) >= bypass_score:
            passed.append(s)
            bypassed.append(sym)
            continue
        history = list(_THRESHOLD_HISTORY.get(sym, []))
        recent = history[-persistence_scans:]
        if len(recent) >= persistence_scans and all(recent):
            passed.append(s)
        else:
            log.debug(
                "Persistence gate blocked %s: needed %d consecutive above-threshold, got %s",
                sym, persistence_scans, recent,
            )

    if bypassed:
        log.info("Persistence gate: %d high-conviction bypass (score >= %d): %s", len(bypassed), bypass_score, bypassed)
    if len(passed) < len(scored):
        log.info(
            "Persistence gate: %d/%d signals passed (need %d consecutive scans above threshold)",
            len(passed), len(scored), persistence_scans,
        )
    # RB-6: Persist history after each gate update so restarts don't zero it.
    _save_threshold_history()
    return passed


def _scored_to_signals(scored: list, regime_name: str) -> list:
    """Convert score_universe() raw dicts → typed Signal objects."""
    now = datetime.now(UTC)
    signals = []
    for s in scored:
        direction = s.get("direction", "NEUTRAL")
        if direction not in ("LONG", "SHORT", "NEUTRAL"):
            direction = "NEUTRAL"
        signals.append(
            Signal(
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
            )
        )
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
    try:
        from trade_log import append_signal as _tl_append_signal
        scan_id = signals[0].timestamp.strftime("%Y%m%d_%H%M%S")
        for s in signals:
            _tl_append_signal(
                scan_id=scan_id,
                symbol=s.symbol,
                score=round(s.conviction_score * 5),
                direction=s.direction,
                regime=s.regime_context,
                breakdown=s.dimension_scores,
            )
    except Exception as _tl_e:
        log.debug("trade_log.append_signal failed: %s", _tl_e)


# ── Public entry point ─────────────────────────────────────────────────────────


def run_signal_pipeline(
    universe: list,
    regime: dict,
    strategy_mode: dict,
    session: str,
    favourites: list,
    signals_log_path: str = SIGNALS_LOG,
    ib=None,
) -> SignalPipelineResult:
    """
    Execute the full signal data pipeline for one scan cycle.

    Pipeline stages
    ---------------
    1. Sympathy scanner   — add sector peers when a leader has earnings within 48h
    2. News sentiment     — Yahoo RSS + keyword scoring
    3. Social sentiment   — Reddit/ApeWisdom (skipped in extended hours + when disabled)
    4. Score universe     — 10-dimension yfinance scoring (alpha-pipeline-v2)
    5. Strategy threshold — raise bar in defensive strategy modes
    6. IC audit log       — write all_scored to learning log (side effect)
    7. Typed signals      — convert scored dicts → Signal objects
    8. Signals log        — append to signals_log.jsonl (side effect)

    Parameters
    ----------
    universe         : symbol list (Tier A ∪ Tier B promoted ∪ Tier C)
    regime           : dict from get_market_regime()
    strategy_mode    : dict from get_intraday_strategy_mode()
    session          : session label from get_session()
    favourites       : always-included symbols from dash["favourites"]
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
    # 1. Universe flows directly into scoring — screening is done upstream
    #    by scanner.get_dynamic_universe() (Tier A hardcoded + Tier B promoter
    #    top-50 + Tier C dynamic adds). No TV pre-filter.
    filtered = list(universe)
    log.info(f"Universe: {len(filtered)} symbols entering scoring")

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

    # 4b. FX track — score major currency pairs (disabled by default)
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

    # 5c. Score persistence gate — require signal above threshold for N consecutive scans.
    # Prevents single-scan DAR spikes from triggering entries.
    # Catalyst/sentinel entries are routed through signal_dispatcher separately and bypass this.
    from config import CONFIG as _cfg
    _persistence = _cfg.get("score_persistence_scans", 2)
    scored = _apply_persistence_gate(scored, all_scored, _persistence)

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
