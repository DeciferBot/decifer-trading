# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_dispatcher.py                      ║
# ║   Routes Signal objects to the order layer.                 ║
# ║   Intelligence layer is the gate — nothing executes         ║
# ║   without a trade_type classification.                      ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
from datetime import UTC, datetime

from market_intelligence import classify_signals
from orders_core import execute_buy, execute_short
from pattern_library import record_entry
from signal_types import Signal
from trade_advisor import advise_trade

log = logging.getLogger("decifer.dispatcher")


# ── Per-account config ────────────────────────────────────────────────────────
_ACCOUNT_DEFAULTS: dict = {}


def _get_account_config(account_id: str) -> dict:
    return _ACCOUNT_DEFAULTS.get(account_id, {})


# ── Signal → candidate dict ───────────────────────────────────────────────────


def _signal_to_candidate(signal: Signal) -> dict:
    """Convert a Signal to the flat dict the intelligence layer expects."""
    return {
        "symbol": signal.symbol,
        "direction": signal.direction,
        "score": round(signal.conviction_score * 5),
        "score_breakdown": signal.dimension_scores or {},
        "rationale": signal.rationale or "",
        "regime_context": signal.regime_context or "",
    }


# ── Main dispatch ─────────────────────────────────────────────────────────────


def dispatch_signals(
    signals: list,
    ib,
    portfolio_value: float,
    regime: dict,
    account_id: str = "",
    agent_outputs: dict | None = None,
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
            log.debug("dispatch: pre-build context failed for %s — %s", _sig.symbol, _ce)

    # ── Intelligence classification (gate) ────────────────────
    # Convert signals to candidate dicts, classify the full batch in one call.
    # Pass serialised TradeContexts so Opus has full entry context per symbol.
    candidates = [_signal_to_candidate(s) for s in signals]
    _ctx_for_opus = {}
    for _sym, _tctx in _context_map.items():
        try:
            _ctx_for_opus[_sym] = _tctx.to_dict()
        except Exception:
            pass
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

            raw_score = round(signal.conviction_score * 5)
            gate_ok, gate_type, gate_reason, effective_score = validate_entry(
                direction=signal.direction,
                ctx=trade_ctx,
                score=raw_score,
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

            # Promote gate-classified trade type if more specific than Opus
            if gate_type != "REJECT" and gate_type != cls.trade_type:
                log.debug(
                    "dispatch: %s trade_type promoted %s → %s by entry_gate",
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
                advice = advise_trade(
                    symbol=signal.symbol,
                    direction="LONG",
                    entry_price=signal.price,
                    atr_5m=signal.atr,
                    atr_daily=signal.atr_daily,
                    conviction_score=signal.conviction_score,
                    dimension_scores=signal.dimension_scores,
                    rationale=signal.rationale,
                    regime_context=signal.regime_context,
                    trade_type=cls.trade_type,
                )
                _entry_ctx = None
                try:
                    if trade_ctx is not None:
                        _entry_ctx = trade_ctx.to_dict()
                except Exception:
                    pass
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
                    advice_instrument=advice.instrument,
                    advice_id=advice.advice_id,
                    trade_type=cls.trade_type,
                    conviction=cls.conviction,
                    pattern_id=pattern_id,
                    market_read=market_read,
                    agents_agreed=len(signal.source_agents or []),
                    entry_context=_entry_ctx,
                )
            except Exception as exc:
                log.error(f"dispatch execute_buy failed {signal.symbol}: {exc}")
                success = False

            result["success"] = success
            result["side"] = "BUY"

        elif signal.direction == "SHORT" and "SHORT" in allowed_dirs:
            try:
                advice = advise_trade(
                    symbol=signal.symbol,
                    direction="SHORT",
                    entry_price=signal.price,
                    atr_5m=signal.atr,
                    atr_daily=signal.atr_daily,
                    conviction_score=signal.conviction_score,
                    dimension_scores=signal.dimension_scores,
                    rationale=signal.rationale,
                    regime_context=signal.regime_context,
                    trade_type=cls.trade_type,
                )
                _entry_ctx = None
                try:
                    if trade_ctx is not None:
                        _entry_ctx = trade_ctx.to_dict()
                except Exception:
                    pass
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
                    advice_instrument=advice.instrument,
                    advice_id=advice.advice_id,
                    trade_type=cls.trade_type,
                    conviction=cls.conviction,
                    pattern_id=pattern_id,
                    market_read=market_read,
                    agents_agreed=len(signal.source_agents or []),
                    entry_context=_entry_ctx,
                )
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
