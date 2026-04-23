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
from sentinel_agents import run_sentinel_pipeline
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

        # Phase 5 completion: the legacy 3-agent sentinel pipeline is gated
        # behind a safety_overlay flag (default True — authoritative today).
        # The Phase 6 cutover flips the flag and replaces the else branch with
        # an Apex NEWS_INTERRUPT dispatch built from
        # sentinel_agents.build_news_trigger_payload().
        try:
            import safety_overlay as _so_sent
            _sent_legacy_on = _so_sent.sentinel_legacy_pipeline_enabled()
        except Exception:
            _sent_legacy_on = True  # fail-safe: preserve legacy live behavior
        if _sent_legacy_on:
            decision = run_sentinel_pipeline(
                trigger=trigger,
                open_positions=open_pos,
                portfolio_value=pv,
                daily_pnl=pnl,
                regime=regime,
            )

            # ── Phase 7C.1B — NEWS_INTERRUPT shadow + divergence record ────
            # When USE_APEX_V3_SHADOW is on, run the Apex path in parallel
            # (execute=False, no orders) and write a comparable legacy/Apex
            # divergence record. Read-only: no state mutation, no dispatch.
            try:
                import safety_overlay as _so_shadow_ni
                if _so_shadow_ni.should_run_apex_shadow():
                    import apex_divergence as _AD_ni
                    import apex_orchestrator as _aorch_ni
                    from sentinel_agents import build_news_trigger_payload as _bntp
                    _ni_input = _bntp(
                        trigger=trigger,
                        open_positions=open_pos,
                        portfolio_value=pv,
                        daily_pnl=pnl,
                        regime=regime,
                        scored_candidate=None,
                    )
                    _ni_shadow = _aorch_ni._run_apex_pipeline(
                        _ni_input, candidates_by_symbol={}, execute=False
                    )
                    _aorch_ni.log_shadow_result(
                        "NEWS_INTERRUPT", _ni_shadow,
                        trigger_context=_ni_input.get("trigger_context"),
                    )
                    # Legacy mirror built from the already-computed legacy
                    # sentinel decision. No re-dispatch; read-only.
                    _ni_legacy_entries = []
                    _ni_act = (decision.get("action") or "SKIP").upper()
                    if _ni_act in ("BUY", "SELL"):
                        _ni_legacy_entries.append({
                            "symbol": decision.get("symbol") or sym,
                            "direction": "LONG" if _ni_act == "BUY" else "SHORT",
                            "trade_type": decision.get("trade_type"),
                            "instrument": decision.get("instrument", "stock"),
                            "qty": decision.get("qty"),
                            "stop_loss": decision.get("sl"),
                            "take_profit": decision.get("tp"),
                        })
                    _ni_legacy_mirror = _AD_ni.mirror_legacy_decision(
                        cycle_id=f"news-{sym}-{datetime.now(_ET).strftime('%H%M%S')}",
                        trigger_type="NEWS_INTERRUPT",
                        new_entries=_ni_legacy_entries,
                    )
                    _ni_apex_mirror = _AD_ni.mirror_apex_decision(
                        cycle_id=_ni_legacy_mirror["cycle_id"],
                        trigger_type="NEWS_INTERRUPT",
                        pipeline_result=_ni_shadow,
                        candidates_by_symbol={},
                    )
                    _ni_events = _AD_ni.classify(_ni_legacy_mirror, _ni_apex_mirror)
                    _AD_ni.write_divergence_record(
                        legacy_mirror=_ni_legacy_mirror,
                        apex_mirror=_ni_apex_mirror,
                        events=_ni_events,
                    )
            except Exception as _ni_shadow_err:
                log.warning("apex_divergence NEWS_INTERRUPT write failed (non-fatal): %s", _ni_shadow_err)
        else:
            # Phase 6D cutover branch (OFF BY DEFAULT — legacy flag above is
            # True). When Phase 7 flips SENTINEL_LEGACY_PIPELINE_ENABLED to
            # False, this branch becomes live: build an Apex NEWS_INTERRUPT
            # ApexInput, call market_intelligence.apex_call, dispatch via
            # signal_dispatcher.dispatch. Until cutover, execute=False.
            clog("INFO", f"Sentinel {sym}: legacy disabled — invoking Apex NEWS_INTERRUPT cutover branch")
            decision = {
                "action": "SKIP",
                "symbol": sym,
                "qty": 0, "sl": 0, "tp": 0, "instrument": "stock",
                "confidence": 0,
                "reasoning": "apex news-interrupt cutover dry-run",
                "trigger_type": "news_sentinel",
            }
            try:
                import apex_orchestrator as _aorch_s
                import safety_overlay as _so_s
                from sentinel_agents import build_news_trigger_payload
                from signal_dispatcher import dispatch as _apex_dispatch

                _s_apex_input = build_news_trigger_payload(
                    trigger=trigger,
                    open_positions=open_pos,
                    portfolio_value=pv,
                    daily_pnl=pnl,
                    regime=regime,
                    scored_candidate=None,  # Phase 7 scores the triggered symbol on demand
                )
                _s_shadow = _aorch_s._run_apex_pipeline(
                    _s_apex_input, candidates_by_symbol={}, execute=False
                )
                _aorch_s.log_shadow_result(
                    "NEWS_INTERRUPT", _s_shadow,
                    trigger_context=_s_apex_input.get("trigger_context"),
                )
                _s_execute = not _so_s.should_use_legacy_pipeline()
                _s_report = _apex_dispatch(
                    _s_shadow.get("decision") or {},
                    candidates_by_symbol={},
                    active_trades={p.get("symbol"): p for p in open_pos if p.get("symbol")},
                    ib=ib,
                    portfolio_value=pv,
                    regime=regime,
                    execute=_s_execute,
                )
                # Surface the first Track A entry as the decision so the
                # existing downstream BUY/SELL/HOLD switch below still runs
                # shape-compatibly. No orders fire unless _s_execute=True.
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
                log.error(f"Sentinel cutover branch failed for {sym}: {_s_cut_err}")

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

        # Phase 5 completion: catalyst trigger path is also gated.
        try:
            import safety_overlay as _so_cat
            _cat_legacy_on = _so_cat.sentinel_legacy_pipeline_enabled()
        except Exception:
            _cat_legacy_on = True
        if _cat_legacy_on:
            decision = run_sentinel_pipeline(
                trigger=trigger,
                open_positions=open_pos,
                portfolio_value=pv,
                daily_pnl=pnl,
                regime=regime,
            )
        else:
            clog("INFO", f"Catalyst {sym}: legacy pipeline disabled by safety_overlay — SKIP")
            decision = {
                "action": "SKIP",
                "symbol": sym,
                "qty": 0,
                "sl": 0,
                "tp": 0,
                "instrument": "stock",
                "confidence": 0,
                "reasoning": "sentinel legacy pipeline disabled",
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
