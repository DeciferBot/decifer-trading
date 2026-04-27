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
from datetime import UTC, datetime

_ET = zoneinfo.ZoneInfo("America/New_York")

import bot_state
from bot_state import clog, dash
from config import CONFIG
from orders_core import execute_buy, execute_sell
from orders_portfolio import get_open_positions
from position_sizing import calculate_stops
from risk import calculate_position_size, check_risk_conditions, get_scan_interval, is_trading_day
from sentinel_agents import build_news_trigger_payload
from theme_tracker import build_sentinel_universe

log = logging.getLogger("decifer.bot")


# ── Sentinel universe callback ────────────────────────────────────────────────


def _get_sentinel_universe() -> list[str]:
    """Callback for NewsSentinel — returns current universe to monitor."""
    try:
        open_pos = get_open_positions()
        favs = dash.get("favourites", [])
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
        syms = [p.get("symbol") for p in get_open_positions() if p.get("symbol")]
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

    if not is_trading_day():
        clog("INFO", f"Sentinel trigger for {sym} — not a trading day, skipping")
        return

    # Rate-cap removed: duplicate-event suppression is handled upstream by
    # HeadlineDeduplicator / SymbolCooldown. Risk is capped by check_risk_conditions
    # + downstream order-level gates (cash floor, sector cap, correlation).

    if dash.get("paused") or dash.get("killed"):
        clog("INFO", f"Sentinel trigger for {sym} — bot paused/killed, skipping")
        return

    ib = bot_state.ib
    if not ib.isConnected():
        clog("ERROR", f"Sentinel trigger for {sym} — IBKR disconnected, skipping")
        return

    try:
        pv = dash.get("portfolio_value", 0)
        pnl = dash.get("daily_pnl", 0)
        regime = dash.get("regime", {"regime": "UNKNOWN", "vix": 0, "position_size_multiplier": 0.5})
        open_pos = get_open_positions()

        tradeable, reason = check_risk_conditions(pv, pnl, regime, open_pos, ib=ib)
        if not tradeable:
            clog("RISK", f"Sentinel {sym}: trading suspended — {reason}")
            return

        clog("SIGNAL", f"🚨 SENTINEL TRIGGER: {sym} | {trigger.get('direction')} | urgency={trigger.get('urgency')}")

        decision = {
            "action": "SKIP",
            "symbol": sym,
            "qty": 0, "sl": 0, "tp": 0, "instrument": "stock",
            "confidence": 0,
            "reasoning": "apex news-interrupt",
            "trigger_type": "news_sentinel",
        }
        try:
            import apex_orchestrator as _aorch_s
            from signal_dispatcher import dispatch as _apex_dispatch

            _s_scored_candidate = None
            try:
                from signals import score_universe as _su_ni
                _regime_str = regime.get("regime", "UNKNOWN") if isinstance(regime, dict) else "UNKNOWN"
                _ni_above, _ = _su_ni(
                    [sym],
                    regime=_regime_str,
                    ib=ib,
                    regime_dict=regime if isinstance(regime, dict) else None,
                )
                if _ni_above:
                    _s_scored_candidate = _ni_above[0]
                    clog("INFO", f"Sentinel {sym}: pre-scored for NEWS_INTERRUPT — score={_s_scored_candidate.get('score')}")
                else:
                    clog("INFO", f"Sentinel {sym}: pre-score returned no candidate above threshold — passing empty")
            except Exception as _ni_score_err:
                log.warning("Sentinel %s: pre-score failed (%s) — passing scored_candidate=None", sym, _ni_score_err)

            _s_apex_input = build_news_trigger_payload(
                trigger=trigger,
                open_positions=open_pos,
                portfolio_value=pv,
                daily_pnl=pnl,
                regime=regime,
                scored_candidate=_s_scored_candidate,
            )
            _s_shadow = _aorch_s._run_apex_pipeline(
                _s_apex_input, candidates_by_symbol={}, execute=False
            )
            _aorch_s.log_shadow_result(
                "NEWS_INTERRUPT", _s_shadow,
                trigger_context=_s_apex_input.get("trigger_context"),
            )
            _s_report = _apex_dispatch(
                _s_shadow.get("decision") or {},
                candidates_by_symbol={},
                active_trades={p.get("symbol"): p for p in open_pos if p.get("symbol")},
                ib=ib,
                portfolio_value=pv,
                regime=regime,
                execute=True,
            )
            _entries = _s_report.get("new_entries") or []
            if _entries:
                _e = _entries[0]
                decision.update({
                    "action": "BUY" if (_e.get("direction") == "LONG") else (
                        "SELL" if _e.get("direction") == "SHORT" else "SKIP"
                    ),
                    "symbol": _e.get("symbol") or sym,
                    "qty": int(_e.get("qty") or 0),
                    "sl": float(_e.get("sl") or 0),
                    "tp": float(_e.get("tp") or 0),
                    "instrument": _e.get("instrument") or "stock",
                    "confidence": 7 if _e.get("conviction") == "HIGH" else 5,
                    "reasoning": _e.get("rationale") or decision["reasoning"],
                })
        except Exception as _s_cut_err:
            log.error(f"Sentinel Apex NEWS_INTERRUPT failed for {sym}: {_s_cut_err}")

        dash["sentinel_triggers"].insert(
            0,
            {
                "symbol": sym,
                "action": decision.get("action", "SKIP"),
                "direction": trigger.get("direction"),
                "urgency": trigger.get("urgency"),
                "confidence": decision.get("confidence", 0),
                "reasoning": decision.get("reasoning", "")[:100],
                "catalyst": trigger.get("claude_catalyst", "")[:80],
                "time": datetime.now(_ET).strftime("%H:%M:%S"),
            },
        )
        dash["sentinel_triggers"] = dash["sentinel_triggers"][:50]

        action = decision.get("action", "SKIP")
        confidence = decision.get("confidence", 0)
        min_confidence = CONFIG.get("sentinel_min_confidence", 5)

        if confidence < min_confidence:
            clog("INFO", f"Sentinel {sym}: confidence {confidence}/10 < {min_confidence} min — skipping")
            return

        if action == "BUY":
            decision["_trigger_size_mult"] = CONFIG.get("sentinel_risk_multiplier", 0.75)
            _execute_trigger_buy(decision, pv, regime, trigger, label="SENTINEL")
        elif action == "SELL":
            _execute_sentinel_sell(decision, open_pos, regime, trigger)
        elif action == "HOLD":
            clog("INFO", f"Sentinel {sym}: HOLD — {decision.get('reasoning', '')[:80]}")
        else:
            clog("INFO", f"Sentinel {sym}: SKIP — {decision.get('reasoning', '')[:80]}")

    except Exception as e:
        log.error(f"Sentinel trigger handler error for {sym}: {e}")


def _execute_trigger_buy(
    decision: dict, portfolio_value: float, regime: dict, trigger: dict, *, label: str = "SENTINEL"
):
    """
    Execute a sentinel or catalyst-triggered buy order.
    Caller must set decision["_trigger_size_mult"] before calling.
    label: "SENTINEL" or "CATALYST" — used in log messages and trade card.
    """
    sym = decision.get("symbol", "")
    qty = decision.get("qty", 0)
    sl = decision.get("sl", 0)
    tp = decision.get("tp", 0)
    instrument = decision.get("instrument", "stock")
    reasoning = decision.get("reasoning", "")
    size_mult = decision.get("_trigger_size_mult", CONFIG.get("sentinel_risk_multiplier", 0.75))
    ib = bot_state.ib

    if qty <= 0:
        try:
            from signals import fetch_multi_timeframe

            sig = fetch_multi_timeframe(sym)
            if sig:
                price = sig.get("price", 0)
                atr = sig.get("atr", 0)
                score = max(sig.get("score", 0), 30)
                qty = calculate_position_size(portfolio_value, price, score, regime, external_mult=size_mult)
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
    tag = f"[{label}:{trigger_type}]" if trigger_type else f"[{label}]"
    type_part = f" | type={trigger_type}" if trigger_type and label == "CATALYST" else ""
    clog("TRADE", f"⚡ {label} BUY {sym} | qty={qty} | SL=${sl:.2f} | TP=${tp:.2f}{type_part} | {reasoning[:60]}")

    try:
        from signals import fetch_multi_timeframe

        sig = fetch_multi_timeframe(sym)
        if sig:
            # Enrich signal_scores with CATALYST dimension (0–10 scale, same as other dims)
            ic_context = trigger.get("ic_context", {})
            screener_ctx = trigger.get("screener_context", {})
            catalyst_score = screener_ctx.get("catalyst_score", 0)
            base_scores = dict(sig.get("score_breakdown", {}))
            if catalyst_score:
                base_scores["CATALYST"] = round(float(catalyst_score), 1)

            success = execute_buy(
                ib=ib,
                symbol=sym,
                price=sig["price"],
                atr=sig.get("atr_5m", sig.get("atr", 0.0)),
                candle_gate=sig.get("candle_gate", "UNKNOWN"),
                score=max(sig.get("score", 0), 30),
                portfolio_value=portfolio_value,
                regime=regime,
                reasoning=f"{tag} {reasoning}",
                signal_scores=base_scores,
                agent_outputs={"catalyst_ic_context": ic_context} if ic_context else {},
                open_time=datetime.now(UTC).isoformat(),
                advice_size_mult=size_mult,
            )
            if success:
                dash["trades"].insert(
                    0,
                    {
                        "side": f"⚡ {label} BUY",
                        "symbol": sym,
                        "price": str(sig["price"]),
                        "time": datetime.now(_ET).strftime("%H:%M:%S"),
                    },
                )
                clog("TRADE", f"⚡ {label} BUY {sym} executed successfully")
        else:
            clog("ERROR", f"{label} BUY {sym}: failed to fetch signal data")
    except Exception as e:
        clog("ERROR", f"{label} BUY execution error for {sym}: {e}")


def _execute_sentinel_sell(decision: dict, open_positions: list, regime: dict, trigger: dict):
    """Execute a sentinel-triggered sell order."""
    sym = decision.get("symbol", "")
    reasoning = decision.get("reasoning", "")
    ib = bot_state.ib

    pos = next((p for p in open_positions if p.get("symbol") == sym), None)
    if not pos:
        clog("INFO", f"Sentinel SELL {sym}: no position found, skipping")
        return

    clog("TRADE", f"⚡ Sentinel SELL {sym} | {reasoning[:80]}")

    try:
        exit_price = pos.get("current", 0)
        execute_sell(ib, sym, reason=f"[SENTINEL] {reasoning}")
        dash["trades"].insert(
            0,
            {
                "side": "⚡ SELL",
                "symbol": sym,
                "price": str(exit_price),
                "time": datetime.now(_ET).strftime("%H:%M:%S"),
            },
        )
        pnl_val = (exit_price - pos.get("entry", 0)) * pos.get("qty", 0)
        from learning import log_trade as _log_trade

        _log_trade(
            trade=pos,
            agent_outputs=decision.get("_sentinel_outputs", {}),
            regime=regime,
            action="CLOSE",
            outcome={
                "exit_price": round(exit_price, 4),
                "pnl": round(pnl_val, 2),
                "pnl_pct": round(pnl_val / ((pos.get("entry") or 1) * (pos.get("qty") or 1)), 4),
                "reason": f"sentinel_{trigger.get('direction', 'news').lower()}",
            },
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
    sym = trigger.get("symbol", "?")
    trigger_type = trigger.get("trigger_type", "unknown")

    # Daily rate-cap removed: EDGAR events are deduped upstream by `_seen_edgar_events`
    # (keyed on form_type|cik|updated[:10]), headline-driven catalysts by
    # HeadlineDeduplicator, and per-ticker re-firing by `_threshold_fired`. Risk is
    # capped by check_risk_conditions + downstream order-level gates.

    if dash.get("paused") or dash.get("killed"):
        clog("INFO", f"Catalyst trigger for {sym} ({trigger_type}) — bot paused/killed, skipping")
        return

    ib = bot_state.ib
    if not ib.isConnected():
        clog("ERROR", f"Catalyst trigger for {sym} — IBKR disconnected, skipping")
        return

    if not sym or sym == "?":
        clog(
            "INFO", f"Catalyst EDGAR event — no ticker resolved, logged only: {trigger.get('claude_catalyst', '')[:80]}"
        )
        dash.setdefault("catalyst_triggers", []).insert(
            0,
            {
                "symbol": "?",
                "action": "LOG",
                "trigger_type": trigger_type,
                "catalyst": trigger.get("claude_catalyst", "")[:80],
                "time": datetime.now(_ET).strftime("%H:%M:%S"),
            },
        )
        return

    try:
        pv = dash.get("portfolio_value", 0)
        pnl = dash.get("daily_pnl", 0)
        regime = dash.get("regime", {"regime": "UNKNOWN", "vix": 0, "position_size_multiplier": 0.5})
        open_pos = get_open_positions()

        tradeable, reason = check_risk_conditions(pv, pnl, regime, open_pos, ib=ib)
        if not tradeable:
            clog("RISK", f"Catalyst {sym}: trading suspended — {reason}")
            return

        clog(
            "SIGNAL",
            f"⚡ CATALYST TRIGGER: {sym} | type={trigger_type} | "
            f"urgency={trigger.get('urgency')} | {trigger.get('claude_catalyst', '')[:60]}",
        )

        decision = {
            "action": "SKIP",
            "symbol": sym,
            "qty": 0,
            "sl": 0,
            "tp": 0,
            "instrument": "stock",
            "confidence": 0,
            "reasoning": "catalyst routed via news-interrupt Apex path",
            "trigger_type": "catalyst",
        }

        dash.setdefault("catalyst_triggers", []).insert(
            0,
            {
                "symbol": sym,
                "action": decision.get("action", "SKIP"),
                "trigger_type": trigger_type,
                "urgency": trigger.get("urgency"),
                "confidence": decision.get("confidence", 0),
                "reasoning": decision.get("reasoning", "")[:100],
                "catalyst": trigger.get("claude_catalyst", "")[:80],
                "time": datetime.now(_ET).strftime("%H:%M:%S"),
            },
        )
        dash["catalyst_triggers"] = dash["catalyst_triggers"][:50]

        action = decision.get("action", "SKIP")
        confidence = decision.get("confidence", 0)
        min_confidence = CONFIG.get("catalyst_min_confidence", 5)

        if confidence < min_confidence:
            clog("INFO", f"Catalyst {sym}: confidence {confidence}/10 < {min_confidence} min — skipping")
            return

        if action == "BUY":
            catalyst_mult = CONFIG.get("catalyst_risk_multiplier", 0.50)
            sentinel_mult = CONFIG.get("sentinel_risk_multiplier", 0.75)
            engine_mult   = trigger.get("size_multiplier", 1.0)  # 1.0 = no change (CatalystSentinel compat)
            decision["_trigger_size_mult"] = catalyst_mult * engine_mult * sentinel_mult
            _execute_trigger_buy(decision, pv, regime, trigger, label="CATALYST")
        elif action == "SELL":
            _execute_sentinel_sell(decision, open_pos, regime, trigger)
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


def start_catalyst_engine():
    """
    Initialise and start the CatalystEngine — unified M&A intelligence service.
    Returns the running engine instance; caller stores in bot_state._catalyst_engine.

    Owns: WatchlistStore + 4 scoring runners + news monitor + EDGAR monitor.
    Fires handle_catalyst_trigger() with screener_context + size_multiplier enrichment.
    """
    from catalyst_engine import CatalystEngine

    engine = CatalystEngine(
        get_universe_fn=_get_sentinel_universe,
        on_trigger_fn=handle_catalyst_trigger,
    )
    engine.start()
    return engine


# ── Scan countdown ────────────────────────────────────────────────────────────


def countdown_tick():
    """Update next_scan_seconds every second for dashboard progress bar."""
    while True:
        time.sleep(1)
        if dash["next_scan_seconds"] > 0:
            dash["next_scan_seconds"] -= 1
        dash["scan_interval_seconds"] = get_scan_interval()
