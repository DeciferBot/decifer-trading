# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_dispatcher.py                      ║
# ║   Routes Signal objects to the order layer                  ║
# ║                                                              ║
# ║   dispatch_signals() is the single entry point for all      ║
# ║   order execution.  It replaces direct execute_buy() calls  ║
# ║   in bot.py, enabling future multi-account routing and       ║
# ║   per-account filters without touching order logic.          ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
from datetime import datetime, timezone

from signal_types import Signal
from orders import execute_buy

SIGNALS_LOG = "signals_log.jsonl"
log = logging.getLogger("decifer.dispatcher")


# ── Per-account config (extend when multi-account support lands) ──────────────
_ACCOUNT_DEFAULTS: dict = {
    # Placeholder — future per-account filters go here.
    # e.g. "DUP12345": {"max_positions": 5, "allowed_directions": ["LONG"]}
}


def _get_account_config(account_id: str) -> dict:
    """Return per-account override config, falling back to empty dict."""
    return _ACCOUNT_DEFAULTS.get(account_id, {})


def dispatch_signals(
    signals: list,
    ib,
    portfolio_value: float,
    regime: dict,
    account_id: str = "",
    agent_outputs: dict = None,
) -> list:
    """
    Route each Signal to the order layer and return execution results.

    Parameters
    ----------
    signals         : List[Signal] — pre-approved LONG signals (filtered by agents).
                      Only LONG direction signals are executed; others are returned
                      with success=False and side matching their direction.
    ib              : active IB connection
    portfolio_value : current portfolio value for position sizing
    regime          : regime dict from get_market_regime()
    account_id      : active IBKR account ID (reserved for future multi-account routing)
    agent_outputs   : raw agent output dict forwarded to execute_buy for trade logging

    Returns
    -------
    list of dicts, one per input signal:
        {
            "signal":  Signal,   # source signal object
            "success": bool,     # True if order was placed
            "side":    str,      # "BUY" | "SHORT" | "NEUTRAL"
            "price":   float,    # price at time of dispatch
        }

    Notes
    -----
    Sell decisions for existing open positions are NOT handled here — they require
    open-position context (entry price, P&L, qty) that is not encoded in the Signal.
    The run_scan() sell path in bot.py remains unchanged.
    """
    if agent_outputs is None:
        agent_outputs = {}

    account_cfg = _get_account_config(account_id)
    allowed_dirs = account_cfg.get("allowed_directions", ["LONG", "SHORT"])

    results = []

    for signal in signals:
        result = {
            "signal":  signal,
            "success": False,
            "side":    signal.direction,
            "price":   signal.price,
        }

        if signal.direction == "LONG" and "LONG" in allowed_dirs:
            try:
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
                )
            except Exception as exc:
                log.error(f"dispatch execute_buy failed {signal.symbol}: {exc}")
                success = False

            result["success"] = success
            result["side"] = "BUY"

        else:
            # NEUTRAL / SHORT signals are logged but not executed by the dispatcher.
            # The sell path remains in run_scan() because it needs open-position
            # context (entry price, qty) that the Signal does not carry.
            log.debug(
                f"dispatch: skipping {signal.symbol} direction={signal.direction} "
                f"(not a dispatchable LONG)"
            )

        results.append(result)

    return results
