# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_dispatcher.py                      ║
# ║   Routes Signal objects to the order layer.                 ║
# ║   Intelligence layer is the gate — nothing executes         ║
# ║   without a trade_type classification.                      ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
from datetime import datetime, timezone

from signal_types import Signal
from orders import execute_buy, execute_short
from trade_advisor import advise_trade
from market_intelligence import classify_signals
from pattern_library import record_entry

log = logging.getLogger("decifer.dispatcher")


# ── Per-account config ────────────────────────────────────────────────────────
_ACCOUNT_DEFAULTS: dict = {}


def _get_account_config(account_id: str) -> dict:
    return _ACCOUNT_DEFAULTS.get(account_id, {})


# ── Signal → candidate dict ───────────────────────────────────────────────────

def _signal_to_candidate(signal: Signal) -> dict:
    """Convert a Signal to the flat dict the intelligence layer expects."""
    return {
        "symbol":          signal.symbol,
        "direction":       signal.direction,
        "score":           int(round(signal.conviction_score * 5)),
        "score_breakdown": signal.dimension_scores or {},
        "rationale":       signal.rationale or "",
        "regime_context":  signal.regime_context or "",
    }


# ── Main dispatch ─────────────────────────────────────────────────────────────

def dispatch_signals(
    signals: list,
    ib,
    portfolio_value: float,
    regime: dict,
    account_id: str = "",
    agent_outputs: dict = None,
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

    account_cfg  = _get_account_config(account_id)
    allowed_dirs = account_cfg.get("allowed_directions", ["LONG", "SHORT"])

    # ── Intelligence classification (gate) ────────────────────
    # Convert signals to candidate dicts, classify the full batch in one call.
    # classify_signals always returns a classification for every candidate.
    candidates    = [_signal_to_candidate(s) for s in signals]
    session_character, market_read, classifications = classify_signals(candidates, regime=regime)
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
            "signal":     signal,
            "success":    False,
            "side":       signal.direction,
            "price":      signal.price,
            "trade_type": "",
            "conviction": 0.0,
        }

        # ── Cooldown / exit guard ──────────────────────────────
        import orders as _ord
        with _ord._trades_lock:
            _existing = _ord.active_trades.get(signal.symbol, {})
        if _existing.get("status") == "EXITING" or _ord._is_recently_closed(signal.symbol):
            log.debug(f"dispatch: skipping {signal.symbol} — exiting or in cooldown")
            results.append(result)
            continue

        # ── Straddle guard — block before intelligence gate ────
        # If an active position already exists for this symbol in the opposite
        # direction, block here. This prevents unintentional straddles and saves
        # the Opus classification call for a signal that would be rejected anyway
        # by execute_buy/execute_short.
        _existing_dir = _existing.get("direction") if _existing else None
        if _existing_dir and _existing_dir != signal.direction:
            log.warning(
                f"dispatch: {signal.symbol} straddle blocked — "
                f"open={_existing_dir} new={signal.direction}"
            )
            result["side"] = "BLOCKED_STRADDLE"
            results.append(result)
            continue

        # ── Intelligence gate ──────────────────────────────────
        cls = class_map.get(signal.symbol.upper())
        if cls is None:
            # Should not happen — classify_signals covers all candidates.
            # Treat as AVOID to be safe.
            log.warning(f"dispatch: no classification for {signal.symbol} — skipping")
            result["side"] = "AVOIDED"
            results.append(result)
            continue

        if cls.trade_type == "AVOID":
            log.info(
                f"dispatch: {signal.symbol} AVOIDED by intelligence | "
                f"{cls.reasoning[:80]}"
            )
            result["side"] = "AVOIDED"
            results.append(result)
            continue

        result["trade_type"] = cls.trade_type
        result["conviction"] = cls.conviction

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
                )
                success = execute_buy(
                    ib=ib,
                    symbol=signal.symbol,
                    price=signal.price,
                    atr=signal.atr,
                    score=int(round(signal.conviction_score * 5)),
                    portfolio_value=portfolio_value,
                    regime=regime,
                    reasoning=signal.rationale,
                    signal_scores=signal.dimension_scores,
                    agent_outputs=agent_outputs,
                    open_time=datetime.now(timezone.utc).isoformat(),
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
                )
            except Exception as exc:
                log.error(f"dispatch execute_buy failed {signal.symbol}: {exc}")
                success = False

            result["success"] = success
            result["side"]    = "BUY"

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
                )
                success = execute_short(
                    ib=ib,
                    symbol=signal.symbol,
                    price=signal.price,
                    atr=signal.atr,
                    score=int(round(signal.conviction_score * 5)),
                    portfolio_value=portfolio_value,
                    regime=regime,
                    reasoning=signal.rationale,
                    signal_scores=signal.dimension_scores,
                    agent_outputs=agent_outputs,
                    open_time=datetime.now(timezone.utc).isoformat(),
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
                )
            except Exception as exc:
                log.error(f"dispatch execute_short failed {signal.symbol}: {exc}")
                success = False

            result["success"] = success
            result["side"]    = "SHORT"

        else:
            log.debug(
                f"dispatch: skipping {signal.symbol} direction={signal.direction} "
                f"(not a dispatchable LONG or SHORT)"
            )

        results.append(result)

    return results
