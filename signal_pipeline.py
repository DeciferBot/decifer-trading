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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from learning import log_signal_scan
from news import batch_news_sentiment
from signal_types import SIGNALS_LOG, Signal
from signals import get_regime_threshold, score_universe
from utils.log_rotation import rotate_jsonl_if_needed

# Maximum size for signals log before rotation; configurable via CONFIG.
try:
    from config import CONFIG as _CFG
    _SIGNALS_LOG_MAX_BYTES = int(_CFG.get("signals_log_max_mb", 20)) * 1_048_576
except Exception:
    _SIGNALS_LOG_MAX_BYTES = 20 * 1_048_576

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
                    Consumed by apex_orchestrator (via guardrails) and
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
    sensor_payloads: list = field(default_factory=list)  # list[SensorPayload] — Decifer 3.0 Apex Agent inputs
    status: str = "OK"  # "OK" | "MONITOR_ONLY"
    tier_d_funnel: dict = field(default_factory=dict)  # per-cycle Tier D attrition counts (stages 1-6)
    scan_id: str = ""  # YYYYMMDDTHHmmss — shared across signals_log and ic_decision_events
    rank_map: dict = field(default_factory=dict)  # {symbol: rank_position} for all scored candidates
    ranking_total: int = 0  # total candidates scored this cycle
    vix: float = 0.0  # VIX value at scan time


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

    effective threshold = base regime threshold
                        + strategy_mode adjustment
                        + IC edge gate adjustment (system health)
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
        filtered = [s for s in scored if s["score"] >= effective]
        reason = " | ".join(parts)
        log.info(
            f"Scored: {len(scored)} → {len(filtered)} after threshold filter "
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




def _scored_to_signals(
    scored: list,
    regime_name: str,
    governance_map: dict | None = None,
    scan_id: str = "",
    rank_map: dict | None = None,
    ranking_total: int = 0,
) -> list:
    """Convert score_universe() raw dicts → typed Signal objects.

    scan_id, rank_map, and ranking_total are provided by run_signal_pipeline()
    so Signal objects carry the same scan provenance as the signals_log records.
    """
    now = datetime.now(UTC)
    gov = governance_map or {}
    _rank_map = rank_map or {}
    _ranking_total = ranking_total or len(scored)
    signals = []
    for s in scored:
        direction = s.get("direction", "NEUTRAL")
        if direction not in ("LONG", "SHORT", "NEUTRAL"):
            direction = "NEUTRAL"
        sym = s["symbol"]
        candidate = gov.get(sym)
        _obs_id = f"{scan_id}_{sym}" if scan_id and sym else ""
        signals.append(
            Signal(
                symbol=sym,
                direction=direction,
                conviction_score=round(s.get("score", 0) / 5.0, 3),
                dimension_scores=s.get("score_breakdown", {}),
                timestamp=now,
                regime_context=regime_name,
                price=s.get("price", 0.0),
                atr=s.get("atr_5m", 0.0),
                atr_daily=s.get("atr_daily", 0.0),
                candle_gate=s.get("candle_gate", "UNKNOWN"),
                instrument=s.get("instrument", "stock"),
                scanner_tier=s.get("scanner_tier", ""),
                extension_at_entry=s.get("extension_at_entry"),
                handoff_source_labels=candidate.get("source_labels") if candidate else None,
                handoff_route=candidate.get("route") if candidate else None,
                handoff_reason_to_care=candidate.get("reason_to_care") if candidate else None,
                handoff_freshness_status=candidate.get("freshness_status") if candidate else None,
                handoff_candidate_id=candidate.get("candidate_id") if candidate else None,
                scan_id=scan_id,
                observation_id=_obs_id,
                ranking_position=_rank_map.get(sym, 0),
                ranking_total=_ranking_total,
            )
        )
    return signals


def _append_signals_log(signals: list, log_path: str) -> None:
    """Append typed Signal objects to the signals log (one JSON line each)."""
    if not signals:
        return
    try:
        rotate_jsonl_if_needed(log_path, _SIGNALS_LOG_MAX_BYTES)
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
    signals_log_path: str = SIGNALS_LOG,
    ib=None,
    governance_map: dict | None = None,
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

    # Resolve regime_name early — needed by the scoring cap and VIX gate below.
    regime_name = regime.get("regime", "UNKNOWN")

    # 2. News sentiment (always)
    log.info("Fetching news sentiment (Yahoo RSS + keyword scoring)...")
    news_sentiment = _fetch_news(filtered)

    # 3. Social sentiment (gated on session)
    social_sentiment = _fetch_social(filtered, session)

    # Gate 1: VIX panic — suppress scan above threshold, return MONITOR_ONLY sentinel.
    # The full 10-dimension scoring is expensive (~5-10s). When VIX signals a panic
    # regime no new entries should be considered anyway — skip the scan entirely.
    from config import CONFIG as _cfg_gate

    _vix = regime.get("vix", 0.0) or 0.0
    _monitor_threshold = _cfg_gate.get("vix_monitor_only_threshold", 40)
    if _vix > _monitor_threshold:
        log.warning(
            f"Gate 1 MONITOR_ONLY: VIX={_vix:.1f} > {_monitor_threshold} — scan suppressed this cycle"
        )
        return SignalPipelineResult(
            signals=[],
            scored=[],
            all_scored=[],
            news_sentiment={},
            universe=filtered,
            regime_name=regime_name,
            sensor_payloads=[],
            status="MONITOR_ONLY",
        )

    # 4. Score universe on 10 dimensions (alpha-pipeline-v2)
    log.info(f"Scoring universe on 10 dimensions [{regime_name}]...")
    scored, all_scored = score_universe(
        filtered,
        regime_name,
        news_data=news_sentiment,
        social_data=social_sentiment,
        regime_router=regime.get("regime_router", "unknown"),
        ib=ib,
        regime_dict=regime,
    )
    log.info(f"score_universe: {len(scored)} above threshold, {len(all_scored)} total")

    # Check for data-fetch failure (all_scored empty from DATA_FETCH_BLOCKED,
    # not from a genuine zero-signal scan).  Distinguishes DATA_FETCH_BLOCKED
    # from RISK_BLOCKED so callers can route correctly.
    if not all_scored and len(filtered) > 0:
        try:
            from signals import get_score_universe_status as _get_fetch_status
            if _get_fetch_status() == "DATA_FETCH_BLOCKED":
                log.critical(
                    "DATA_FETCH_BLOCKED: propagating from score_universe — "
                    "entries paused this cycle; portfolio/exit/risk unaffected"
                )
                return SignalPipelineResult(
                    signals=[],
                    scored=[],
                    all_scored=[],
                    news_sentiment=news_sentiment,
                    universe=filtered,
                    regime_name=regime_name,
                    status="DATA_FETCH_BLOCKED",
                )
        except Exception:
            pass  # Non-critical; fall through to normal zero-signal handling


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

    # 5c. Score persistence gate — require signal above threshold for N consecutive scans
    from config import CONFIG as _cfg
    _persistence = _cfg.get("score_persistence_scans", 2)
    scored = _apply_persistence_gate(scored, all_scored, _persistence)

    # 6. IC audit log — generate scan_id once for cross-log correlation, then write.
    _scan_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    _rank_map_all: dict[str, int] = {
        s.get("symbol"): i + 1
        for i, s in enumerate(
            sorted(all_scored, key=lambda x: float(x.get("score") or 0), reverse=True)
        )
        if s.get("symbol")
    }
    # Stamp scan provenance onto all_scored dicts so observation_id flows downstream
    # into candidates_by_symbol without needing separate lookup.  Also stamp
    # passed_base_threshold so the signals_log record and decision events agree.
    # ranking_position / ranking_total / candidate_source are stamped here so
    # ORDER_INTENT receives them via _origin_extras in signal_dispatcher.dispatch().
    _scored_syms: set[str] = {s.get("symbol") for s in scored if s.get("symbol")}
    _session_date_str = datetime.now(UTC).date().isoformat()
    _ranking_total = len(all_scored)
    for _s in all_scored:
        _sym = _s.get("symbol")
        _s["scan_id"] = _scan_id
        _s["observation_id"] = f"{_scan_id}_{_sym}" if _sym else None
        _s["passed_base_threshold"] = _sym in _scored_syms if _sym else False
        _s["session_date"] = _session_date_str
        _s["ranking_position"] = _rank_map_all.get(_sym, 0) if _sym else 0
        _s["ranking_total"] = _ranking_total
        # candidate_source: determined from scanner_tier at pipeline time.
        # bot_trading.py updates this to "handoff_reader" for handoff-sourced
        # candidates after the pipeline returns (observation records get "scanner"
        # since the writer runs inside run_signal_pipeline before that enrichment).
        if not _s.get("candidate_source"):
            if _s.get("scanner_tier") == "D":
                _s["candidate_source"] = "position_research_universe"
            else:
                _s["candidate_source"] = "scanner"
    log_signal_scan(all_scored, regime, scan_id=_scan_id)
    # Write below_threshold decision events for symbols that didn't clear base threshold.
    try:
        from ic_decision_writer import write_events_bulk as _write_de_bulk
        _below_events = [
            {
                "observation_id": _s.get("observation_id"),
                "scan_id": _scan_id,
                "symbol": _s.get("symbol"),
                "decision_status": "below_threshold",
                "session_date": _session_date_str,
                "candidate_source": _s.get("candidate_source", "unknown"),
                "ranking_position": _rank_map_all.get(_s.get("symbol")),
                "ranking_total": len(all_scored),
                "reason": "score_below_base_threshold",
            }
            for _s in all_scored
            if not _s.get("passed_base_threshold") and _s.get("symbol")
        ]
        if _below_events:
            _write_de_bulk(_below_events)
    except Exception as _de_exc:
        log.debug("signal_pipeline: below_threshold event write failed (non-fatal): %s", _de_exc)

    # 7. Build typed Signal objects (governance_map attaches handoff provenance when available)
    signals = _scored_to_signals(
        scored,
        regime_name,
        governance_map=governance_map,
        scan_id=_scan_id,
        rank_map=_rank_map_all,
        ranking_total=len(all_scored),
    )

    # 7b. ML Observation Writer — moved to bot_trading.py (Sprint 3.7).
    #     rank_map, ranking_total, and vix are now exposed on SignalPipelineResult
    #     so bot_trading.py can call write_observations() AFTER handoff enrichment
    #     has promoted candidate_source to "handoff_reader" for handoff candidates.

    # 8. Append to signals_log.jsonl for IC calculator
    _append_signals_log(signals, log_path=signals_log_path)

    # 9. Sensor payloads — module not yet implemented; reserved for future enrichment.
    sensor_payloads = []

    return SignalPipelineResult(
        signals=signals,
        scored=scored,
        all_scored=all_scored,
        news_sentiment=news_sentiment,
        universe=filtered,
        regime_name=regime_name,
        sensor_payloads=sensor_payloads,
        status="OK",
        tier_d_funnel={},
        scan_id=_scan_id,
        rank_map=_rank_map_all,
        ranking_total=_ranking_total,
        vix=_vix,
    )
