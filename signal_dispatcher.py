# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_dispatcher.py                      ║
# ║   Routes Signal objects to the order layer.                 ║
# ║   Intelligence layer is the gate — nothing executes         ║
# ║   without a trade_type classification.                      ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from market_intelligence import classify_signals
from options import find_best_contract
from orders_core import execute_buy, execute_short
from orders_options import execute_buy_option
from pattern_library import record_entry
from position_sizing import calculate_stops
from signal_types import Signal


@dataclass
class _FormulaAdvice:
    advice_id: str
    instrument: str
    size_multiplier: float
    profit_target: float
    stop_loss: float


def _formula_advice(symbol: str, direction: str, entry_price: float, atr_5m: float) -> _FormulaAdvice:
    sl, tp = calculate_stops(entry_price, atr_5m, direction)
    return _FormulaAdvice(
        advice_id=str(uuid.uuid4())[:8],
        instrument="COMMON",
        size_multiplier=1.0,
        profit_target=tp,
        stop_loss=sl,
    )

log = logging.getLogger("decifer.dispatcher")


# ── Per-account config ────────────────────────────────────────────────────────
_ACCOUNT_DEFAULTS: dict = {}


def _get_account_config(account_id: str) -> dict:
    return _ACCOUNT_DEFAULTS.get(account_id, {})


# ── Signal → candidate dict ───────────────────────────────────────────────────


def _signal_to_candidate(signal: Signal) -> dict:
    """Convert a Signal to the flat dict the intelligence layer expects."""
    cand = {
        "symbol": signal.symbol,
        "direction": signal.direction,
        "score": round(signal.conviction_score * 5),
        "score_breakdown": signal.dimension_scores or {},
        "rationale": signal.rationale or "",
        "regime_context": signal.regime_context or "",
    }
    if getattr(signal, "scanner_tier", ""):
        cand["scanner_tier"] = signal.scanner_tier
    return cand


# ── Tier D context backfill ───────────────────────────────────────────────────
# Fundamental fields used to assess TradeContext quality for Tier D candidates.
_TIER_D_FUND_FIELDS = ("fcf_yield", "dcf_upside_pct", "revenue_growth_yoy",
                       "gross_margin", "analyst_upside_pct")


def _backfill_tier_d_contexts(
    signals: list,
    context_map: dict,
    context_failed: set,
) -> dict[str, dict]:
    """
    After the initial context-map build, scan Tier D candidates for missing or
    no-fundamental TradeContext.  Attempt one fresh build_context() call per
    affected symbol.

    Returns a mapping  symbol.upper() → backfill_info  for every Tier D candidate
    that needed attention:
        {
          "tier_d_rescued_after_context_build": True,
          "context_backfilled": True | False,
          "context_backfill_source": "fresh_fmp" | "failed",
          "missing_fresh_trade_context_after_rescue": True | False,
        }

    Side-effect: updates context_map in place if backfill succeeds.
    Safe: never raises; all errors are logged and included in the returned info.
    """
    from trade_context import build_context as _build_ctx

    backfill_info: dict[str, dict] = {}

    for sig in signals:
        if getattr(sig, "scanner_tier", "") != "D":
            continue
        if sig.direction not in ("LONG", "SHORT"):
            continue
        sym_upper = sig.symbol.upper()
        if sym_upper in context_failed:
            continue  # already excluded from candidates

        ctx = context_map.get(sym_upper)
        has_fundamentals = (
            ctx is not None
            and any(getattr(ctx, f, None) is not None for f in _TIER_D_FUND_FIELDS)
        )
        if has_fundamentals:
            continue  # context is adequately populated — no action needed

        # Context is missing or has no fundamentals — flag and attempt backfill
        info: dict = {
            "tier_d_rescued_after_context_build": True,
            "context_backfilled": False,
            "context_backfill_source": "failed",
            "missing_fresh_trade_context_after_rescue": True,
        }
        log.info(
            "dispatch: Tier D %s ctx has no fundamentals (tier_d_rescued_after_context_build=True) "
            "— attempting backfill",
            sig.symbol,
        )
        try:
            ctx_retry = _build_ctx(
                symbol=sig.symbol, direction=sig.direction, signal=sig,
                current_price=sig.price, regime=sig.regime_context,
            )
            has_fund_retry = any(
                getattr(ctx_retry, f, None) is not None for f in _TIER_D_FUND_FIELDS
            )
            if has_fund_retry:
                context_map[sym_upper] = ctx_retry
                info["context_backfilled"] = True
                info["context_backfill_source"] = "fresh_fmp"
                info["missing_fresh_trade_context_after_rescue"] = False
                log.info(
                    "dispatch: Tier D %s context backfilled "
                    "(context_backfilled=True context_backfill_source=fresh_fmp)",
                    sig.symbol,
                )
            else:
                log.warning(
                    "dispatch: Tier D %s backfill produced no fundamentals "
                    "(context_backfilled=False missing_fresh_trade_context_after_rescue=True)",
                    sig.symbol,
                )
        except Exception as exc:
            log.warning(
                "dispatch: Tier D %s backfill failed "
                "(context_backfill_source=failed missing_fresh_trade_context_after_rescue=True): %s",
                sig.symbol, exc,
            )
        backfill_info[sym_upper] = info

    return backfill_info


# ── Main dispatch ─────────────────────────────────────────────────────────────


def dispatch_signals(
    signals: list,
    ib,
    portfolio_value: float,
    regime: dict,
    account_id: str = "",
    agent_outputs: dict | None = None,
    execute: bool = True,
) -> list:
    """
    Classify then route each Signal to the order layer.

    Intelligence gate: every signal is classified by the intelligence layer
    before dispatch. AVOID classifications are blocked. SCALP / SWING / HOLD
    classifications proceed with trade_type and conviction stored on the position.

    Parameters
    ----------
    signals         : List[Signal] — pre-scored signals from the pipeline
    ib              : active IB connection
    portfolio_value : current portfolio value for position sizing
    regime          : regime dict from get_market_regime()
    account_id      : IBKR account ID
    agent_outputs   : raw agent output dict forwarded to execute_buy for logging

    Returns
    -------
    list of dicts, one per input signal:
        {
            "signal":     Signal,
            "success":    bool,
            "side":       str,    # "BUY" | "SHORT" | "NEUTRAL" | "AVOIDED"
            "price":      float,
            "trade_type": str,
            "conviction": float,
        }
    """
    if agent_outputs is None:
        agent_outputs = {}

    account_cfg = _get_account_config(account_id)
    allowed_dirs = account_cfg.get("allowed_directions", ["LONG", "SHORT"])

    # ── Build TradeContexts upfront (before Opus so it sees full context) ────────
    # Built for all LONG/SHORT signals. Reused in per-signal entry gate below
    # so we don't fetch FMP/Alpaca data twice per signal.
    _context_map: dict[str, object] = {}  # symbol.upper() → TradeContext
    _context_failed: set[str] = set()  # symbols excluded due to context build failure
    for _sig in signals:
        if _sig.direction not in ("LONG", "SHORT"):
            continue
        try:
            from trade_context import build_context as _build_ctx
            from alpaca_data import get_intraday_vwap as _get_vwap, get_relative_volume as _get_rvol

            _sv = None
            _rv = None
            _cs = None
            _ct = None
            _ed = None

            try:
                from alpaca_stream import QUOTE_CACHE as _QC2
                _snap2 = _QC2.get(_sig.symbol) if hasattr(_QC2, "get") else None
                if _snap2 and _snap2.get("vwap"):
                    _sv = float(_snap2["vwap"])
            except Exception:
                pass
            if _sv is None:
                try:
                    _sv = _get_vwap(_sig.symbol)
                except Exception:
                    pass
            try:
                _rv = _get_rvol(_sig.symbol)
            except Exception:
                pass
            try:
                from catalyst_engine import get_catalyst_score as _gcs
                _c = _gcs(_sig.symbol)
                if _c:
                    _cs, _ct = _c.get("score"), _c.get("type")
            except Exception:
                pass
            try:
                from risk import get_earnings_days_away as _geda
                _ed = _geda(_sig.symbol)
            except Exception:
                pass

            _context_map[_sig.symbol.upper()] = _build_ctx(
                symbol=_sig.symbol, direction=_sig.direction, signal=_sig,
                current_price=_sig.price, vwap=_sv, rel_volume=_rv,
                catalyst_score=_cs, catalyst_type=_ct,
                earnings_days_away=_ed, regime=_sig.regime_context,
            )
        except Exception as _ce:
            log.warning("dispatch: pre-build context failed for %s — excluding from candidates: %s", _sig.symbol, _ce)
            _context_failed.add(_sig.symbol.upper())

    # ── Tier D context backfill ────────────────────────────────────────────────
    # Detect Tier D candidates with missing or no-fundamental ctx; attempt retry.
    # Mutates _context_map in place if backfill succeeds.
    _tier_d_backfill = _backfill_tier_d_contexts(signals, _context_map, _context_failed)

    # ── Intelligence classification (gate) ────────────────────
    # Convert signals to candidate dicts, classify the full batch in one call.
    # Pass serialised TradeContexts so Opus has full entry context per symbol.
    candidates = [_signal_to_candidate(s) for s in signals if s.symbol.upper() not in _context_failed]
    _ctx_for_opus = {}
    for _sym, _tctx in _context_map.items():
        try:
            _ctx_for_opus[_sym] = _tctx.to_dict()
        except Exception as _te:
            log.warning("dispatch: to_dict() failed for %s — Apex will have no context: %s", _sym, _te)
    _cand_syms_missing_ctx = [c["symbol"].upper() for c in candidates if c["symbol"].upper() not in _ctx_for_opus]
    if _cand_syms_missing_ctx:
        log.warning("dispatch: %d candidate(s) have no Apex context: %s", len(_cand_syms_missing_ctx), _cand_syms_missing_ctx)
    session_character, market_read, classifications = classify_signals(
        candidates, regime=regime, trade_contexts=_ctx_for_opus
    )
    # Propagate session_character into the regime dict so orders_core and
    # check_external_closes can read it without a separate import.
    if isinstance(regime, dict):
        regime["session_character"] = session_character

    # Build lookup: symbol → SignalClassification
    # If a symbol appears more than once (shouldn't), last entry wins.
    class_map = {c.symbol: c for c in classifications}

    results = []

    for signal in signals:
        result = {
            "signal": signal,
            "success": False,
            "side": signal.direction,
            "price": signal.price,
            "trade_type": "",
            "conviction": 0.0,
            "skip_reason": "",
        }

        # ── Cooldown / exit guard ──────────────────────────────
        import orders as _ord

        with _ord._trades_lock:
            _existing = _ord.active_trades.get(signal.symbol, {})
        if _existing.get("status") == "EXITING" or _ord._is_recently_closed(signal.symbol):
            log.debug(f"dispatch: skipping {signal.symbol} — exiting or in cooldown")
            result["skip_reason"] = "In cooldown — position recently closed or still exiting"
            results.append(result)
            continue

        # ── Straddle guard — block before intelligence gate ────
        # If an active position already exists for this symbol in the opposite
        # direction, block here. This prevents unintentional straddles and saves
        # the Opus classification call for a signal that would be rejected anyway
        # by execute_buy/execute_short.
        _existing_dir = _existing.get("direction") if _existing else None
        if _existing_dir and _existing_dir != signal.direction:
            log.warning(f"dispatch: {signal.symbol} straddle blocked — open={_existing_dir} new={signal.direction}")
            result["side"] = "BLOCKED_STRADDLE"
            result["skip_reason"] = f"Straddle blocked — already holding {_existing_dir} position"
            results.append(result)
            continue

        # ── Intelligence gate ──────────────────────────────────
        cls = class_map.get(signal.symbol.upper())
        if cls is None:
            # Should not happen — classify_signals covers all candidates.
            # Treat as AVOID to be safe.
            log.warning(f"dispatch: no classification for {signal.symbol} — skipping")
            result["side"] = "AVOIDED"
            result["skip_reason"] = "Intelligence gate: no classification returned (fallback AVOID)"
            results.append(result)
            continue

        if cls.trade_type == "AVOID":
            log.info(f"dispatch: {signal.symbol} AVOIDED by intelligence | {cls.reasoning[:80]}")
            result["side"] = "AVOIDED"
            result["skip_reason"] = cls.reasoning  # full Opus reasoning, up to 300 chars
            results.append(result)
            continue

        result["trade_type"] = cls.trade_type
        result["conviction"] = cls.conviction

        # ── Entry gate: reuse pre-built context, validate ─────────────────────
        # Context was built before Opus call so it was included in classification.
        # Reusing avoids double-fetching Alpaca/FMP data per signal.
        trade_ctx = None
        try:
            from entry_gate import validate_entry

            trade_ctx = _context_map.get(signal.symbol.upper())

            # Count currently open INTRADAY positions for the concurrency gate (Change 4)
            _open_intraday_count = 0
            try:
                from orders_portfolio import get_open_positions as _gop
                _open_intraday_count = sum(
                    1 for _p in _gop()
                    if (_p.get("trade_type") or "").upper() in ("INTRADAY", "SCALP")
                )
            except Exception as _oi_err:
                log.debug("dispatch: open_intraday_count fetch failed: %s", _oi_err)

            raw_score = round(signal.conviction_score * 5)

            # For Tier D signals, retrieve the PRU FMP snapshot and backfill info
            # so entry_gate shadow validation can detect ctx data-flow gaps and
            # log missing_fresh_trade_context_after_rescue.
            _pru_snap: dict = {}
            _t4_backfill: dict = {}
            if getattr(signal, "scanner_tier", "") == "D":
                try:
                    import scanner as _scanner_mod
                    _, _pru_meta = _scanner_mod.get_position_research_universe()
                    _pru_snap = _pru_meta.get(signal.symbol, {}).get("pru_fmp_snapshot") or {}
                except Exception as _pe:
                    log.debug("dispatch: pru_fmp_snapshot lookup failed for %s: %s", signal.symbol, _pe)
                _t4_backfill = _tier_d_backfill.get(signal.symbol.upper(), {})

            gate_ok, gate_type, gate_reason, effective_score = validate_entry(
                direction=signal.direction,
                ctx=trade_ctx,
                score=raw_score,
                opus_trade_type=cls.trade_type,
                score_breakdown=signal.dimension_scores,
                instrument=signal.instrument,
                open_intraday_count=_open_intraday_count,
                scanner_tier=getattr(signal, "scanner_tier", "") or None,
                pru_fmp_snapshot=_pru_snap or None,
                tier_d_backfill_info=_t4_backfill or None,
            )

            if not gate_ok:
                log.info(
                    "dispatch: %s %s REJECTED by entry_gate | %s",
                    signal.symbol, signal.direction, gate_reason,
                )
                result["side"] = "REJECTED"
                result["skip_reason"] = f"entry_gate: {gate_reason}"
                results.append(result)
                continue

            # entry_gate may override trade_type for special cases:
            #   - market-closed → SWING for overnight entries (promotes)
            #   - POSITION fundamentals failed → SWING (demotes — intentional)
            # For non-POSITION cases, never let entry_gate demote Opus's label:
            # INTRADAY < SWING < POSITION — only override if gate_type ranks higher
            # (except when Opus said POSITION and entry_gate ran the checklist).
            _type_rank = {"INTRADAY": 0, "SWING": 1, "POSITION": 2}
            # Allow POSITION→SWING downgrade (fundamentals checklist failed).
            # Allow any promotion (gate_type ranks higher than Opus label).
            # Block any other demotion (entry_gate should not silently drop Opus labels).
            _opus_rank = _type_rank.get(cls.trade_type, 0)
            _gate_rank = _type_rank.get(gate_type, 0)
            _position_downgrade = (cls.trade_type == "POSITION" and gate_type == "SWING")
            if gate_type not in ("REJECT", cls.trade_type) and (
                _gate_rank > _opus_rank or _position_downgrade
            ):
                log.debug(
                    "dispatch: %s trade_type %s → %s by entry_gate",
                    signal.symbol, cls.trade_type, gate_type,
                )
                cls.trade_type = gate_type
                result["trade_type"] = gate_type

        except Exception as _gate_exc:
            # Gate failure is non-fatal — log and continue (paper trading safety)
            log.warning(
                "dispatch: entry_gate failed for %s — proceeding without gate: %s",
                signal.symbol, _gate_exc,
            )

        # ── Record entry in pattern library ───────────────────
        # Returns pattern_id stored on the position for learning loop.
        try:
            from market_observer import get_market_observation

            obs = get_market_observation()
            from orders_core import _derive_setup_type

            pattern_id = record_entry(
                observation=obs,
                symbol=signal.symbol,
                direction=signal.direction,
                trade_type=cls.trade_type,
                conviction=cls.conviction,
                market_read=market_read,
                signal_score=signal.conviction_score * 5,
                setup_type=_derive_setup_type(signal.dimension_scores or {}),
            )
        except Exception as exc:
            log.debug(f"dispatch: pattern_library record_entry failed: {exc}")
            pattern_id = ""

        # ── Trade advisor (PT / SL / size / instrument) ───────
        if signal.direction == "LONG" and "LONG" in allowed_dirs:
            try:
                advice = _formula_advice(signal.symbol, "LONG", signal.price, signal.atr)
                _entry_ctx = None
                try:
                    if trade_ctx is not None:
                        _entry_ctx = trade_ctx.to_dict()
                except Exception:
                    pass
                if execute:
                    success = execute_buy(
                        ib=ib,
                        symbol=signal.symbol,
                        price=signal.price,
                        atr=signal.atr,
                        score=round(signal.conviction_score * 5),
                        portfolio_value=portfolio_value,
                        regime=regime,
                        reasoning=signal.rationale,
                        signal_scores=signal.dimension_scores,
                        agent_outputs=agent_outputs,
                        open_time=datetime.now(UTC).isoformat(),
                        candle_gate=signal.candle_gate,
                        instrument=signal.instrument,
                        advice_pt=advice.profit_target,
                        advice_sl=advice.stop_loss,
                        advice_size_mult=advice.size_multiplier,
                        advice_id=advice.advice_id,
                        trade_type=cls.trade_type,
                        conviction=cls.conviction,
                        pattern_id=pattern_id,
                        market_read=market_read,
                        entry_context=_entry_ctx,
                    )
                else:
                    success = False
            except Exception as exc:
                log.error(f"dispatch execute_buy failed {signal.symbol}: {exc}")
                success = False

            result["success"] = success
            result["side"] = "BUY"

        elif signal.direction == "SHORT" and "SHORT" in allowed_dirs:
            try:
                advice = _formula_advice(signal.symbol, "SHORT", signal.price, signal.atr)
                _entry_ctx = None
                try:
                    if trade_ctx is not None:
                        _entry_ctx = trade_ctx.to_dict()
                except Exception:
                    pass
                if execute:
                    success = execute_short(
                        ib=ib,
                        symbol=signal.symbol,
                        price=signal.price,
                        atr=signal.atr,
                        score=round(signal.conviction_score * 5),
                        portfolio_value=portfolio_value,
                        regime=regime,
                        reasoning=signal.rationale,
                        signal_scores=signal.dimension_scores,
                        agent_outputs=agent_outputs,
                        open_time=datetime.now(UTC).isoformat(),
                        candle_gate=signal.candle_gate,
                        instrument=signal.instrument,
                        advice_pt=advice.profit_target,
                        advice_sl=advice.stop_loss,
                        advice_size_mult=advice.size_multiplier,
                        advice_id=advice.advice_id,
                        trade_type=cls.trade_type,
                        conviction=cls.conviction,
                        pattern_id=pattern_id,
                        market_read=market_read,
                        entry_context=_entry_ctx,
                    )
                else:
                    success = False
            except Exception as exc:
                log.error(f"dispatch execute_short failed {signal.symbol}: {exc}")
                success = False

            result["success"] = success
            result["side"] = "SHORT"

        else:
            log.debug(
                f"dispatch: skipping {signal.symbol} direction={signal.direction} (not a dispatchable LONG or SHORT)"
            )

        results.append(result)

    return results


# ══════════════════════════════════════════════════════════════
# PHASE 6D — APEX DECISION DISPATCHER  (cutover bridge — off by default)
# ══════════════════════════════════════════════════════════════
# These two functions consume an ApexDecision (produced by
# market_intelligence.apex_call + guardrails.filter_semantic_violations) and
# translate it into calls to the existing orders_core execution primitives.
#
# They are NOT invoked by any live code path in Phase 6D. The Phase 7 cutover
# flips USE_LEGACY_PIPELINE / PM_LEGACY_OPUS_REVIEW_ENABLED / SENTINEL_LEGACY_
# PIPELINE_ENABLED to False, which activates the cutover else-branches that
# call these functions. Default execute=False keeps them shadow-safe until
# that moment.
#
# Unlike dispatch_signals() above (which consumes legacy Signal objects and
# calls classify_signals internally), dispatch() consumes an already-validated
# ApexDecision and does no additional LLM work. It is a pure translator.


def dispatch_forced_exit(
    symbol: str,
    reason: str,
    ib=None,
    *,
    execute: bool = False,
) -> dict:
    """
    Close a position via execute_sell() with a deterministic reason tag.

    Used for guardrails-detected forced exits (eod_flat, scalp_timeout,
    architecture_violation, unknown_trade_type). No LLM involvement.

    execute=False (default) is a dry run — returns the shape of the action
    without calling execute_sell. Phase 6D keeps execute=False at every call
    site; Phase 7 flips it True for live forced exits.
    """
    action = {
        "symbol": symbol,
        "action": "FORCED_EXIT",
        "reason": reason,
        "executed": False,
    }
    if not execute:
        log.info(f"dispatch_forced_exit (dry): {symbol} — {reason}")
        return action

    try:
        from orders_core import execute_sell
        ok = execute_sell(ib, symbol, reason=reason)
        action["executed"] = bool(ok)
    except Exception as exc:
        log.error(f"dispatch_forced_exit: execute_sell failed for {symbol} — {exc}")
        action["error"] = str(exc)
    return action


def _select_atr(entry: dict, payload: dict) -> float:
    """SWING/POSITION use daily ATR; INTRADAY/AVOID use 5m ATR (per master plan §L3)."""
    tt = (entry.get("trade_type") or "").upper()
    if tt in ("SWING", "POSITION"):
        return float(payload.get("atr_daily") or payload.get("atr_5m") or 0.0)
    return float(payload.get("atr_5m") or payload.get("atr") or 0.0)


def _conviction_external_mult(conviction: str | None) -> float:
    """Map Apex conviction enum (MEDIUM/HIGH) → external_mult for position sizing."""
    from risk import CONVICTION_MULT
    if not conviction:
        return 0.65  # conservative default; should not occur for non-AVOID entries
    return CONVICTION_MULT.get(conviction.upper(), 0.65)


def dispatch(
    decision: dict,
    candidates_by_symbol: dict[str, dict],
    active_trades: dict,
    *,
    ib=None,
    portfolio_value: float = 0.0,
    regime: dict | None = None,
    execute: bool = False,
) -> dict:
    """
    Translate an ApexDecision into order-layer calls.

    decision             — ApexDecision dict (schema-validated upstream)
    candidates_by_symbol — {symbol: ScannerPayload} for every Track A candidate
    active_trades        — current open positions dict
    execute              — False (default) returns a dry-run report; True
                           actually submits orders via execute_buy /
                           execute_short / execute_sell. Phase 6D callers set
                           execute=False; Phase 7 flips to True.

    Returns a report dict:
        {
          "new_entries":       [ {symbol, direction, trade_type, conviction,
                                  instrument, qty, sl, tp, executed}, ... ],
          "portfolio_actions": [ {symbol, action, trim_pct, executed}, ... ],
          "forced_exits":      [ {symbol, reason, executed}, ... ],
          "errors":            [str, ...],
        }

    CONVICTION_MULT and ATR selection are applied here (not at the Apex) so
    the LLM's only sizing lever is the MEDIUM/HIGH conviction enum.
    """
    regime = regime or {}
    report: dict = {
        "new_entries": [],
        "portfolio_actions": [],
        "forced_exits": [],
        "errors": [],
    }

    # Symbols with an active Track B EXIT or TRIM this cycle — block Track A re-entry.
    _exiting_syms: set[str] = {
        act.get("symbol")
        for act in (decision.get("portfolio_actions") or [])
        if (act.get("action") or "").upper() in ("EXIT", "TRIM") and act.get("symbol")
    }

    # Dedup Track B: if Apex returns multiple actions for the same symbol, keep
    # the highest-priority one. EXIT > TRIM > ADD > HOLD prevents self-contradiction.
    _ACTION_PRIORITY = {"EXIT": 4, "TRIM": 3, "ADD": 2, "HOLD": 1}
    _deduped_b: dict[str, dict] = {}
    for _a in (decision.get("portfolio_actions") or []):
        _s = _a.get("symbol")
        if not _s:
            continue
        _pri = _ACTION_PRIORITY.get((_a.get("action") or "").upper(), 0)
        _cur_pri = _ACTION_PRIORITY.get((_deduped_b.get(_s, {}).get("action") or "").upper(), 0)
        if _s not in _deduped_b or _pri > _cur_pri:
            _deduped_b[_s] = _a
    _pm_actions = list(_deduped_b.values())

    # RELIEF_RALLY: cap new LONG size to 50% — deterministic enforcement of what
    # the Apex prompt requests softly. Checked once here so it applies to all entries.
    _sc = (decision.get("session_character") or regime.get("session_character") or "").upper()
    _relief_rally = (_sc == "RELIEF_RALLY")

    # ── Track A: new entries ──────────────────────────────────────────────
    for entry in (decision.get("new_entries") or []):
        sym = entry.get("symbol")
        trade_type = (entry.get("trade_type") or "").upper()

        if trade_type == "AVOID" or not sym:
            continue

        if sym in _exiting_syms:
            log.warning("dispatch: %s Track A entry blocked — Track B EXIT/TRIM in same cycle", sym)
            report["errors"].append(f"{sym}: Track A entry blocked — Track B EXIT/TRIM in same cycle")
            continue

        payload = candidates_by_symbol.get(sym) or {}
        if not payload:
            report["errors"].append(f"{sym}: no payload for Track A entry")
            continue

        price = float(payload.get("price") or 0.0)
        score = int(payload.get("score") or 0)
        atr = _select_atr(entry, payload)
        ext_mult = _conviction_external_mult(entry.get("conviction"))
        direction = (entry.get("direction") or "").upper()
        if _relief_rally and direction == "LONG":
            ext_mult = min(ext_mult, 0.5)
            log.info("dispatch: %s LONG size capped to 0.5× — RELIEF_RALLY session character", sym)

        qty = 0
        sl = tp = 0.0
        if price > 0 and portfolio_value > 0:
            try:
                from risk import calculate_position_size
                qty = calculate_position_size(
                    portfolio_value, price, score, regime, atr=atr, external_mult=ext_mult
                )
                sl, tp = calculate_stops(price, atr, "LONG" if direction == "LONG" else "SHORT")
            except Exception as exc:
                report["errors"].append(f"{sym}: sizing failed — {exc}")

        rec = {
            "symbol": sym,
            "direction": direction,
            "trade_type": trade_type,
            "conviction": entry.get("conviction"),
            "instrument": entry.get("instrument"),
            "price": price,
            "atr": atr,
            "external_mult": ext_mult,
            "qty": qty,
            "sl": sl,
            "tp": tp,
            "executed": False,
        }

        if not execute:
            report["new_entries"].append(rec)
            continue

        try:
            signal_scores = payload.get("score_breakdown") or {}
            rationale = entry.get("rationale", "")
            instrument = entry.get("instrument") or "stock"
            regime_name = regime.get("regime", "UNKNOWN") if isinstance(regime, dict) else str(regime)

            if instrument in ("call", "put"):
                contract_info = find_best_contract(
                    symbol=sym,
                    direction=direction,
                    portfolio_value=portfolio_value,
                    ib=ib,
                    regime=regime,
                    score=score,
                    trade_type=trade_type,
                )
                if contract_info is None:
                    log.warning(
                        f"dispatch {sym}: options contract not found (instrument={instrument}) "
                        f"— falling back to stock entry"
                    )
                    instrument = "stock"
                else:
                    ok = execute_buy_option(
                        ib=ib,
                        contract_info=contract_info,
                        portfolio_value=portfolio_value,
                        reasoning=rationale,
                        score=score,
                        trade_type=trade_type,
                        conviction=ext_mult,
                        signal_scores=signal_scores,
                        regime=regime_name,
                    )

            if instrument == "stock":
                if direction == "LONG":
                    ok = execute_buy(
                        ib=ib,
                        symbol=sym,
                        price=price,
                        atr=atr,
                        score=score,
                        portfolio_value=portfolio_value,
                        regime=regime,
                        reasoning=rationale,
                        signal_scores=signal_scores,
                        open_time=datetime.now(UTC).isoformat(),
                        trade_type=trade_type,
                        conviction=ext_mult,
                    )
                elif direction == "SHORT":
                    ok = execute_short(
                        ib=ib,
                        symbol=sym,
                        price=price,
                        atr=atr,
                        score=score,
                        portfolio_value=portfolio_value,
                        regime=regime,
                        reasoning=rationale,
                        signal_scores=signal_scores,
                        open_time=datetime.now(UTC).isoformat(),
                        trade_type=trade_type,
                        conviction=ext_mult,
                    )
                else:
                    ok = False
                    report["errors"].append(f"{sym}: unknown direction {direction!r}")
            rec["executed"] = bool(ok)
        except Exception as exc:
            report["errors"].append(f"{sym}: execute failed — {exc}")
        report["new_entries"].append(rec)

    # ── Track B: portfolio actions (HOLD / TRIM / EXIT / ADD) ────────────
    for act in _pm_actions:
        sym = act.get("symbol")
        action_type = (act.get("action") or "").upper()
        rec = {
            "symbol": sym,
            "action": action_type,
            "trim_pct": act.get("trim_pct"),
            "add_pct": act.get("add_pct"),
            "reasoning_tag": act.get("reasoning_tag"),
            "executed": False,
        }
        if action_type == "HOLD" or not sym:
            report["portfolio_actions"].append(rec)
            continue

        if not execute:
            report["portfolio_actions"].append(rec)
            continue

        try:
            pos = active_trades.get(sym) or {}
            pos_qty = int(pos.get("qty") or 0)
            if action_type == "EXIT":
                from orders_core import execute_sell
                ok = execute_sell(ib, sym, reason=act.get("reasoning_tag") or "apex_exit")
                rec["executed"] = bool(ok)
            elif action_type == "TRIM":
                from orders_core import execute_sell
                trim_pct = int(act.get("trim_pct") or 50)
                trim_qty = max(1, int(pos_qty * trim_pct / 100))
                ok = execute_sell(
                    ib, sym,
                    reason=act.get("reasoning_tag") or f"apex_trim_{trim_pct}",
                    qty_override=trim_qty,
                )
                rec["executed"] = bool(ok)
            elif action_type == "ADD":
                add_pct = int(act.get("add_pct") or 25)
                add_qty = max(1, int(pos_qty * add_pct / 100))
                direction = (pos.get("direction") or "LONG").upper()
                price = float(pos.get("entry") or 0.0)
                atr = float(pos.get("atr") or 0.0)
                score = int(pos.get("score") or 0)
                rationale = act.get("reasoning_tag") or f"apex_add_{add_pct}"
                if direction == "LONG":
                    ok = execute_buy(
                        ib=ib, symbol=sym, price=price, atr=atr, score=score,
                        portfolio_value=portfolio_value, regime=regime or {},
                        reasoning=rationale, signal_scores={},
                        open_time=datetime.now(UTC).isoformat(),
                        trade_type=pos.get("trade_type") or "INTRADAY",
                        conviction=1.0,
                        qty_override=add_qty,
                    )
                else:
                    ok = execute_short(
                        ib=ib, symbol=sym, price=price, atr=atr, score=score,
                        portfolio_value=portfolio_value, regime=regime or {},
                        reasoning=rationale, signal_scores={},
                        open_time=datetime.now(UTC).isoformat(),
                        trade_type=pos.get("trade_type") or "INTRADAY",
                        conviction=1.0,
                        qty_override=add_qty,
                    )
                rec["executed"] = bool(ok)
            else:
                report["errors"].append(f"{sym}: unknown action {action_type!r}")
        except Exception as exc:
            report["errors"].append(f"{sym}: portfolio action failed — {exc}")
        report["portfolio_actions"].append(rec)

    return report

