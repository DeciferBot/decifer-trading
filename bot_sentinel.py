#!/usr/bin/env python3
"""
bot_sentinel.py — News and Catalyst sentinel handlers for the Decifer trading bot.

Covers: handle_news_trigger, _execute_sentinel_buy/sell,
handle_catalyst_trigger, _execute_catalyst_buy, and the countdown ticker.
"""
from __future__ import annotations

import logging
import time
import zoneinfo
from datetime import datetime, timezone

_ET = zoneinfo.ZoneInfo("America/New_York")

from config import CONFIG
import bot_state
from bot_state import dash, clog

from risk import check_risk_conditions, calculate_position_size, calculate_stops, get_scan_interval
from orders import execute_buy, execute_sell, get_open_positions
from sentinel_agents import run_sentinel_pipeline
from theme_tracker import build_sentinel_universe

log = logging.getLogger("decifer.bot")


# ── Sentinel universe callback ────────────────────────────────────────────────

def _get_sentinel_universe() -> list[str]:
    """Callback for NewsSentinel — returns current universe to monitor."""
    try:
        open_pos = get_open_positions()
        favs     = dash.get("favourites", [])
        recent_headlines = []
        for sym_data in dash.get("news_data", {}).values():
            recent_headlines.extend(sym_data.get("headlines", []))
        return build_sentinel_universe(
            open_positions=open_pos,
            favourites=favs,
            trending_headlines=recent_headlines[:50],
        )
    except Exception as e:
        log.error(f"Sentinel universe error: {e}")
        syms  = [p.get("symbol") for p in get_open_positions() if p.get("symbol")]
        syms += dash.get("favourites", [])
        return list(set(syms))


# ── News sentinel trigger handler ─────────────────────────────────────────────

def handle_news_trigger(trigger: dict):
    """
    Callback fired by NewsSentinel when material news is detected.
    Runs the 3-agent mini pipeline and executes trades immediately.
    This runs on the sentinel's background thread.
    """
    sym = trigger.get("symbol", "?")

    now = datetime.now(_ET)
    if (bot_state._sentinel_hour_start is None or
            (now - bot_state._sentinel_hour_start).total_seconds() > 3600):
        bot_state._sentinel_trades_this_hour = 0
        bot_state._sentinel_hour_start       = now

    max_per_hour = CONFIG.get("sentinel_max_trades_per_hour", 3)
    if bot_state._sentinel_trades_this_hour >= max_per_hour:
        clog("RISK", f"Sentinel rate limit: {bot_state._sentinel_trades_this_hour}/{max_per_hour} trades this hour — skipping {sym}")
        return

    if dash.get("paused") or dash.get("killed"):
        clog("INFO", f"Sentinel trigger for {sym} — bot paused/killed, skipping")
        return

    ib = bot_state.ib
    if not ib.isConnected():
        clog("ERROR", f"Sentinel trigger for {sym} — IBKR disconnected, skipping")
        return

    try:
        pv      = dash.get("portfolio_value", 0)
        pnl     = dash.get("daily_pnl", 0)
        regime  = dash.get("regime", {"regime": "UNKNOWN", "vix": 0, "position_size_multiplier": 0.5})
        open_pos = get_open_positions()

        tradeable, reason = check_risk_conditions(pv, pnl, regime, open_pos, ib=ib)
        if not tradeable:
            clog("RISK", f"Sentinel {sym}: trading suspended — {reason}")
            return

        clog("SIGNAL", f"🚨 SENTINEL TRIGGER: {sym} | {trigger.get('direction')} | urgency={trigger.get('urgency')}")

        decision = run_sentinel_pipeline(
            trigger=trigger,
            open_positions=open_pos,
            portfolio_value=pv,
            daily_pnl=pnl,
            regime=regime,
        )

        dash["sentinel_triggers"].insert(0, {
            "symbol":    sym,
            "action":    decision.get("action", "SKIP"),
            "direction": trigger.get("direction"),
            "urgency":   trigger.get("urgency"),
            "confidence": decision.get("confidence", 0),
            "reasoning": decision.get("reasoning", "")[:100],
            "catalyst":  trigger.get("claude_catalyst", "")[:80],
            "time":      datetime.now(_ET).strftime("%H:%M:%S"),
        })
        dash["sentinel_triggers"] = dash["sentinel_triggers"][:50]

        action         = decision.get("action", "SKIP")
        confidence     = decision.get("confidence", 0)
        min_confidence = CONFIG.get("sentinel_min_confidence", 5)

        if confidence < min_confidence:
            clog("INFO", f"Sentinel {sym}: confidence {confidence}/10 < {min_confidence} min — skipping")
            return

        if action == "BUY":
            decision["_trigger_size_mult"] = CONFIG.get("sentinel_risk_multiplier", 0.75)
            _execute_trigger_buy(decision, pv, regime, trigger, label="SENTINEL")
            bot_state._sentinel_trades_this_hour += 1
        elif action == "SELL":
            _execute_sentinel_sell(decision, open_pos, regime, trigger)
            bot_state._sentinel_trades_this_hour += 1
        elif action == "HOLD":
            clog("INFO", f"Sentinel {sym}: HOLD — {decision.get('reasoning', '')[:80]}")
        else:
            clog("INFO", f"Sentinel {sym}: SKIP — {decision.get('reasoning', '')[:80]}")

    except Exception as e:
        log.error(f"Sentinel trigger handler error for {sym}: {e}")


def _execute_trigger_buy(decision: dict, portfolio_value: float,
                         regime: dict, trigger: dict, *, label: str = "SENTINEL"):
    """
    Execute a sentinel or catalyst-triggered buy order.
    Caller must set decision["_trigger_size_mult"] before calling.
    label: "SENTINEL" or "CATALYST" — used in log messages and trade card.
    """
    sym        = decision.get("symbol", "")
    qty        = decision.get("qty", 0)
    sl         = decision.get("sl", 0)
    tp         = decision.get("tp", 0)
    instrument = decision.get("instrument", "stock")
    reasoning  = decision.get("reasoning", "")
    size_mult  = decision.get("_trigger_size_mult", CONFIG.get("sentinel_risk_multiplier", 0.75))
    ib         = bot_state.ib

    if qty <= 0:
        try:
            from signals import fetch_multi_timeframe
            sig = fetch_multi_timeframe(sym)
            if sig:
                price = sig.get("price", 0)
                atr   = sig.get("atr", 0)
                score = max(sig.get("score", 0), 30)
                qty   = calculate_position_size(portfolio_value, price, score, regime, external_mult=size_mult)
                if sl <= 0 and atr > 0:
                    sl, tp = calculate_stops(price, atr, "LONG")
        except Exception as e:
            log.error(f"{label} position sizing error for {sym}: {e}")
            return

    if qty <= 0:
        clog("INFO", f"{label} BUY {sym}: calculated qty=0, skipping")
        return

    if instrument == "inverse_etf" and decision.get("inverse_symbol"):
        sym = decision["inverse_symbol"]
        clog("TRADE", f"⚡ {label} SHORT via {sym} (inverse ETF)")

    trigger_type = trigger.get("trigger_type", "")
    tag          = f"[{label}:{trigger_type}]" if trigger_type else f"[{label}]"
    type_part    = f" | type={trigger_type}" if trigger_type and label == "CATALYST" else ""
    clog("TRADE",
         f"⚡ {label} BUY {sym} | qty={qty} | SL=${sl:.2f} | TP=${tp:.2f}"
         f"{type_part} | {reasoning[:60]}")

    try:
        from signals import fetch_multi_timeframe
        sig = fetch_multi_timeframe(sym)
        if sig:
            success = execute_buy(
                ib=ib,
                symbol=sym,
                price=sig["price"],
                atr=sig["atr"],
                candle_gate=sig.get("candle_gate", "UNKNOWN"),
                score=max(sig.get("score", 0), 30),
                portfolio_value=portfolio_value,
                regime=regime,
                reasoning=f"{tag} {reasoning}",
                signal_scores=sig.get("score_breakdown", {}),
                agent_outputs={},
                open_time=datetime.now(timezone.utc).isoformat(),
            )
            if success:
                dash["trades"].insert(0, {
                    "side":   f"⚡ {label} BUY",
                    "symbol": sym,
                    "price":  str(sig["price"]),
                    "time":   datetime.now(_ET).strftime("%H:%M:%S"),
                })
                clog("TRADE", f"⚡ {label} BUY {sym} executed successfully")
        else:
            clog("ERROR", f"{label} BUY {sym}: failed to fetch signal data")
    except Exception as e:
        clog("ERROR", f"{label} BUY execution error for {sym}: {e}")


def _execute_sentinel_sell(decision: dict, open_positions: list,
                           regime: dict, trigger: dict):
    """Execute a sentinel-triggered sell order."""
    sym       = decision.get("symbol", "")
    reasoning = decision.get("reasoning", "")
    ib        = bot_state.ib

    pos = next((p for p in open_positions if p.get("symbol") == sym), None)
    if not pos:
        clog("INFO", f"Sentinel SELL {sym}: no position found, skipping")
        return

    clog("TRADE", f"⚡ Sentinel SELL {sym} | {reasoning[:80]}")

    try:
        exit_price = pos.get("current", 0)
        execute_sell(ib, sym, reason=f"[SENTINEL] {reasoning}")
        dash["trades"].insert(0, {
            "side":   "⚡ SELL",
            "symbol": sym,
            "price":  str(exit_price),
            "time":   datetime.now(_ET).strftime("%H:%M:%S"),
        })
        pnl_val = (exit_price - pos.get("entry", 0)) * pos.get("qty", 0)
        from learning import log_trade as _log_trade
        _log_trade(
            trade=pos,
            agent_outputs=decision.get("_sentinel_outputs", {}),
            regime=regime,
            action="CLOSE",
            outcome={
                "exit_price": round(exit_price, 4),
                "pnl":        round(pnl_val, 2),
                "pnl_pct":    round(pnl_val / ((pos.get("entry") or 1) * (pos.get("qty") or 1)), 4),
                "reason":     f"sentinel_{trigger.get('direction', 'news').lower()}",
            }
        )
        clog("TRADE", f"⚡ Sentinel SELL {sym} executed | P&L: ${pnl_val:+,.2f}")
    except Exception as e:
        clog("ERROR", f"Sentinel SELL execution error for {sym}: {e}")


# ── Catalyst sentinel trigger handler ─────────────────────────────────────────

def handle_catalyst_trigger(trigger: dict):
    """
    Callback fired by CatalystSentinel when a catalyst event is detected.
    Runs the existing 3-agent sentinel pipeline and executes immediately.
    """
    sym          = trigger.get("symbol", "?")
    trigger_type = trigger.get("trigger_type", "unknown")

    today = datetime.now(_ET).strftime("%Y-%m-%d")
    if bot_state._catalyst_trade_date != today:
        bot_state._catalyst_trades_today = 0
        bot_state._catalyst_trade_date   = today

    max_per_day = CONFIG.get("catalyst_max_trades_per_day", 2)
    if bot_state._catalyst_trades_today >= max_per_day:
        clog("RISK", f"Catalyst rate limit: {bot_state._catalyst_trades_today}/{max_per_day} trades today — skipping {sym}")
        return

    if dash.get("paused") or dash.get("killed"):
        clog("INFO", f"Catalyst trigger for {sym} ({trigger_type}) — bot paused/killed, skipping")
        return

    ib = bot_state.ib
    if not ib.isConnected():
        clog("ERROR", f"Catalyst trigger for {sym} — IBKR disconnected, skipping")
        return

    if not sym or sym == "?":
        clog("INFO", f"Catalyst EDGAR event — no ticker resolved, logged only: {trigger.get('claude_catalyst', '')[:80]}")
        dash.setdefault("catalyst_triggers", []).insert(0, {
            "symbol":       "?",
            "action":       "LOG",
            "trigger_type": trigger_type,
            "catalyst":     trigger.get("claude_catalyst", "")[:80],
            "time":         datetime.now(_ET).strftime("%H:%M:%S"),
        })
        return

    try:
        pv      = dash.get("portfolio_value", 0)
        pnl     = dash.get("daily_pnl", 0)
        regime  = dash.get("regime", {"regime": "UNKNOWN", "vix": 0, "position_size_multiplier": 0.5})
        open_pos = get_open_positions()

        tradeable, reason = check_risk_conditions(pv, pnl, regime, open_pos, ib=ib)
        if not tradeable:
            clog("RISK", f"Catalyst {sym}: trading suspended — {reason}")
            return

        clog("SIGNAL",
             f"⚡ CATALYST TRIGGER: {sym} | type={trigger_type} | "
             f"urgency={trigger.get('urgency')} | {trigger.get('claude_catalyst', '')[:60]}")

        decision = run_sentinel_pipeline(
            trigger=trigger,
            open_positions=open_pos,
            portfolio_value=pv,
            daily_pnl=pnl,
            regime=regime,
        )

        dash.setdefault("catalyst_triggers", []).insert(0, {
            "symbol":       sym,
            "action":       decision.get("action", "SKIP"),
            "trigger_type": trigger_type,
            "urgency":      trigger.get("urgency"),
            "confidence":   decision.get("confidence", 0),
            "reasoning":    decision.get("reasoning", "")[:100],
            "catalyst":     trigger.get("claude_catalyst", "")[:80],
            "time":         datetime.now(_ET).strftime("%H:%M:%S"),
        })
        dash["catalyst_triggers"] = dash["catalyst_triggers"][:50]

        action         = decision.get("action", "SKIP")
        confidence     = decision.get("confidence", 0)
        min_confidence = CONFIG.get("catalyst_min_confidence", 5)

        if confidence < min_confidence:
            clog("INFO", f"Catalyst {sym}: confidence {confidence}/10 < {min_confidence} min — skipping")
            return

        if action == "BUY":
            catalyst_mult = CONFIG.get("catalyst_risk_multiplier", 0.50)
            sentinel_mult = CONFIG.get("sentinel_risk_multiplier", 0.75)
            decision["_trigger_size_mult"] = catalyst_mult * sentinel_mult
            _execute_trigger_buy(decision, pv, regime, trigger, label="CATALYST")
            bot_state._catalyst_trades_today += 1
        elif action == "SELL":
            _execute_sentinel_sell(decision, open_pos, regime, trigger)
            bot_state._catalyst_trades_today += 1
        elif action == "HOLD":
            clog("INFO", f"Catalyst {sym}: HOLD — {decision.get('reasoning', '')[:80]}")
        else:
            clog("INFO", f"Catalyst {sym}: SKIP — {decision.get('reasoning', '')[:80]}")

    except Exception as e:
        log.error(f"Catalyst trigger handler error for {sym}: {e}")



# ── Sentinel factory functions ────────────────────────────────────────────────

def start_news_sentinel(ib):
    """
    Initialise and start the NewsSentinel background thread.
    Returns the running sentinel instance; caller should store in bot_state._sentinel.
    """
    from news_sentinel import NewsSentinel
    sentinel = NewsSentinel(
        get_universe_fn=_get_sentinel_universe,
        on_trigger_fn=handle_news_trigger,
        ib=ib,
        poll_interval=CONFIG.get("sentinel_poll_seconds", 45),
    )
    sentinel.start()
    return sentinel


def start_alpaca_news_stream():
    """
    Initialise and start the AlpacaNewsStream (push-based Benzinga feed).
    Returns the running stream instance; caller stores it in bot_state._alpaca_news_stream.
    No ib argument — Alpaca news stream is independent of IBKR.
    """
    from alpaca_news import AlpacaNewsStream
    stream = AlpacaNewsStream(
        get_universe_fn=_get_sentinel_universe,
        on_trigger_fn=handle_news_trigger,
    )
    stream.start()
    return stream


def start_catalyst_sentinel(ib):
    """
    Initialise and start the CatalystSentinel background thread.
    Returns the running sentinel instance; caller should store in bot_state._catalyst_sentinel.
    """
    from catalyst_sentinel import CatalystSentinel
    sentinel = CatalystSentinel(
        get_universe_fn=_get_sentinel_universe,
        on_trigger_fn=handle_catalyst_trigger,
        ib=ib,
    )
    sentinel.start()
    return sentinel


# ── Scan countdown ────────────────────────────────────────────────────────────

def countdown_tick():
    """Update next_scan_seconds every second for dashboard progress bar."""
    while True:
        time.sleep(1)
        if dash["next_scan_seconds"] > 0:
            dash["next_scan_seconds"] -= 1
        dash["scan_interval_seconds"] = get_scan_interval()
