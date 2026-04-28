# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_entries.py                         ║
# ║   Deterministic options entry bridge                         ║
# ║   CALL_BUYER / PUT_BUYER scanner signals → execute_buy_option║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Bridges scan_options_universe() output to execute_buy_option() without
LLM involvement.

Only CALL_BUYER and PUT_BUYER signals with confirmed unusual volume are
executed deterministically. EARNINGS_PLAY and MIXED_FLOW remain as Apex
context only — direction is ambiguous without a full thesis.

Exposure policy (enforced here and in bot_trading.py):
  1. Existing stock position + new options entry       → ALLOW
  2. Existing options position + new options entry     → BLOCK (same symbol, any cycle)
  3. Same-cycle options entry + Apex stock entry       → BLOCK (enforced in bot_trading.py
                                                          via the returned frozenset)
  4. Existing options position + Apex stock entry      → BLOCK (same frozenset in bot_trading.py
                                                          includes active_trades snapshot)
  5. Conviction: options_score >= 22 → 1.00 (HIGH), else 0.65 (MEDIUM),
                 matching CONVICTION_MULT in risk.py — stored in trade_log and active_trades.

Public API:
  execute_options_entries(ib, options_signals, portfolio_value, regime) -> frozenset[str]
    Returns the set of symbols that had entries placed this cycle.
"""

from __future__ import annotations

import logging

from config import CONFIG
from options import find_best_contract
from orders_contracts import is_options_market_open
from orders_guards import has_open_order_for
from orders_options import execute_buy_option
from orders_state import _is_recently_closed, active_trades, _trades_lock

log = logging.getLogger("decifer.options_entries")

_DIRECTIONAL_SIGNALS = {"CALL_BUYER", "PUT_BUYER"}
_SKIP_REGIMES = {"PANIC", "CAPITULATION"}

# Conviction float values mirror CONVICTION_MULT in risk.py
_CONVICTION_HIGH = 1.00   # options_score >= 22
_CONVICTION_MEDIUM = 0.65  # options_score >= 18 (minimum)


def execute_options_entries(
    ib,
    options_signals: list[dict],
    portfolio_value: float,
    regime: dict | None = None,
) -> frozenset[str]:
    """
    Evaluate options scanner signals and fire deterministic entries.

    Returns frozenset of symbols that had entries placed this cycle.
    Caller (bot_trading.py) uses this set to block Apex from opening
    stock positions on the same symbols in the same cycle.
    """
    if not CONFIG.get("options_enabled"):
        log.debug("options_entries: master switch off — no entries")
        return frozenset()

    if not is_options_market_open():
        log.debug("options_entries: options market closed — no entries")
        return frozenset()

    regime_name = (regime or {}).get("regime", "UNKNOWN")
    if regime_name in _SKIP_REGIMES:
        log.info("options_entries: skip all — regime=%s", regime_name)
        return frozenset()

    min_score: int = CONFIG.get("options_scan_entry_min_score", 18)
    max_entries: int = CONFIG.get("options_scan_max_entries_per_cycle", 2)

    fired: set[str] = set()
    skips: dict[str, int] = {
        "non_directional": 0,
        "low_score": 0,
        "no_unusual_vol": 0,
        "cooldown": 0,
        "open_order": 0,
        "existing_options_position": 0,
        "no_contract": 0,
        "cap_reached": 0,
    }

    for i, sig in enumerate(options_signals):
        if len(fired) >= max_entries:
            skips["cap_reached"] += sum(
                1 for s in options_signals[i:]
                if s.get("signal") in _DIRECTIONAL_SIGNALS
                and s.get("options_score", 0) >= min_score
            )
            break

        symbol: str = sig.get("symbol", "")
        signal: str = sig.get("signal", "")
        options_score: int = sig.get("options_score", 0)

        if signal not in _DIRECTIONAL_SIGNALS:
            skips["non_directional"] += 1
            continue

        if options_score < min_score:
            log.debug("options_entries: %s score=%d < min=%d — skip", symbol, options_score, min_score)
            skips["low_score"] += 1
            continue

        # Unusual volume must be confirmed for the asserted direction
        if signal == "CALL_BUYER" and not sig.get("unusual_calls"):
            log.info("options_entries: %s CALL_BUYER but unusual_calls=False — skip", symbol)
            skips["no_unusual_vol"] += 1
            continue
        if signal == "PUT_BUYER" and not sig.get("unusual_puts"):
            log.info("options_entries: %s PUT_BUYER but unusual_puts=False — skip", symbol)
            skips["no_unusual_vol"] += 1
            continue

        if _is_recently_closed(symbol):
            log.info("options_entries: %s in reentry cooldown — skip", symbol)
            skips["cooldown"] += 1
            continue

        if has_open_order_for(symbol):
            log.info("options_entries: %s has open order — skip", symbol)
            skips["open_order"] += 1
            continue

        # Policy rule 2: block if options position already exists for this symbol
        with _trades_lock:
            already_opts = any(
                v.get("symbol") == symbol and v.get("instrument") == "option"
                for v in active_trades.values()
            )
        if already_opts:
            log.info("options_entries: %s already has open options position — skip", symbol)
            skips["existing_options_position"] += 1
            continue

        direction = "LONG" if signal == "CALL_BUYER" else "SHORT"
        scaled_score = int(options_score / 30 * 100)
        conviction_float = _CONVICTION_HIGH if options_score >= 22 else _CONVICTION_MEDIUM

        contract_info = find_best_contract(
            symbol=symbol,
            direction=direction,
            portfolio_value=portfolio_value,
            ib=ib,
            regime=regime,
            score=scaled_score,
            trade_type="SWING",
        )
        if contract_info is None:
            log.info("options_entries: %s no suitable contract found — skip", symbol)
            skips["no_contract"] += 1
            continue

        conviction_label = "HIGH" if conviction_float >= _CONVICTION_HIGH else "MEDIUM"
        reasoning = (
            f"Deterministic options entry: {signal} options_score={options_score}/30 "
            f"(scaled={scaled_score}/100) conviction={conviction_label} | "
            f"{sig.get('reasoning', '')}"
        )

        ok = execute_buy_option(
            ib=ib,
            contract_info=contract_info,
            portfolio_value=portfolio_value,
            reasoning=reasoning,
            score=scaled_score,
            trade_type="SWING",
            conviction=conviction_float,
            signal_scores={},
            regime=regime_name,
        )
        if ok:
            fired.add(symbol)
            log.info(
                "options_entries: fired %s %s options_score=%d/30 conviction=%s — %d/%d this cycle",
                signal, symbol, options_score, conviction_label,
                len(fired), max_entries,
            )

    if any(skips.values()):
        log.info(
            "options_entries: skip summary — %s",
            " ".join(f"{k}={v}" for k, v in skips.items() if v),
        )

    return frozenset(fired)
