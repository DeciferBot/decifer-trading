#!/usr/bin/env python3
"""
bot_trading.py — Core trading pipeline for the Decifer trading bot.

Covers: run_scan (main loop), external-close detection, options position
monitoring, kill-switch check, close-queue processing, and cash rebalancing.
"""

import logging
import sys
import time
import threading
from datetime import datetime, timezone

from config import CONFIG
import bot_state
from bot_state import dash, clog

from bot_account import get_account_data, get_account_details, get_news_headlines, get_fx_snapshot, save_equity_history
from bot_ibkr import sync_orders_from_ibkr, connect_ibkr

from scanner import get_dynamic_universe, get_market_regime, get_tv_signal_cache
from signals import fetch_multi_timeframe
from agents import run_all_agents
from orders import (execute_buy, execute_sell, flatten_all,
                    get_open_positions, update_position_prices,
                    update_positions_from_ibkr, execute_buy_option,
                    execute_sell_option, update_trailing_stops,
                    update_tranche_status)
from options import find_best_contract, check_options_exits
from options_scanner import scan_options_universe
from risk import (check_risk_conditions, get_session, get_scan_interval,
                  calculate_position_size, calculate_stops,
                  update_equity_high_water_mark,
                  init_equity_high_water_mark_from_history,
                  get_intraday_strategy_mode, set_session_opening_regime,
                  check_thesis_validity, get_consecutive_losses)
from learning import (log_trade, load_trades, load_orders,
                      get_performance_summary, run_weekly_review,
                      TRADE_LOG_FILE, get_effective_capital)
from signal_types import Signal, SIGNALS_LOG
from signal_dispatcher import dispatch_signals as _dispatch_signals
from signal_pipeline import run_signal_pipeline, SignalPipelineResult

log = logging.getLogger("decifer.bot")


# ── Detect positions closed externally (stop loss / take profit) ──────────────

def check_external_closes(regime: dict):
    """
    Compare bot's open_trades tracker against IBKR actual positions.
    If a position exists in our tracker but not in IBKR, it was closed
    externally.  Log it properly so Trade History tab shows it.
    """
    from orders import open_trades, _ibkr_item_to_key, _is_option_contract
    from learning import log_trade, load_trades
    ib = bot_state.ib

    try:
        portfolio_items = ib.portfolio(CONFIG["active_account"])
        ibkr_syms = {_ibkr_item_to_key(item) for item in portfolio_items if item.position != 0}

        realized_pnl_map = {}
        for item in portfolio_items:
            sym  = item.contract.symbol
            rpnl = getattr(item, 'realizedPNL', None)
            if rpnl is not None:
                try:
                    realized_pnl_map[sym] = float(rpnl)
                except (ValueError, TypeError):
                    pass

        for sym in list(open_trades.keys()):
            if sym not in ibkr_syms:
                trade = open_trades[sym]

                if trade.get("status") == "PENDING":
                    order_id     = trade.get("order_id")
                    still_active = False
                    if order_id:
                        try:
                            for t in ib.openTrades():
                                if t.order.orderId == order_id:
                                    still_active = True
                                    break
                        except Exception:
                            still_active = True
                    if still_active:
                        continue
                    else:
                        clog("INFO", f"Removing unfilled order from tracker: {sym} (order #{order_id} no longer active in IBKR)")
                        del open_trades[sym]
                        continue

                exit_price = None
                is_opt_pos = trade.get("instrument") == "option"
                underlying = trade.get("symbol", sym)
                try:
                    import math as _math
                    fills = ib.fills()
                    if is_opt_pos:
                        sell_fills = [
                            f for f in fills
                            if f.contract.symbol == underlying
                            and f.execution.side.upper() in ("SLD", "SELL")
                            and _is_option_contract(f.contract)
                        ]
                    else:
                        sell_fills = [
                            f for f in fills
                            if f.contract.symbol == underlying
                            and f.execution.side.upper() in ("SLD", "SELL")
                            and not _is_option_contract(f.contract)
                        ]
                    if sell_fills:
                        sell_fills.sort(key=lambda f: f.execution.time or datetime.min)
                        exit_price = float(sell_fills[-1].execution.price)
                except Exception:
                    pass

                rpnl_key = underlying if is_opt_pos else sym
                if exit_price is None and rpnl_key in realized_pnl_map:
                    rpnl = realized_pnl_map[rpnl_key]
                    qty  = trade["qty"]
                    mult = 100 if is_opt_pos else 1
                    if qty and not _math.isnan(rpnl) and rpnl != 0.0:
                        exit_price = round(trade["entry"] + rpnl / (qty * mult), 4)

                if exit_price is None:
                    clog("INFO", f"No fill evidence for {sym} — removing from tracker (not logging as trade)")
                    del open_trades[sym]
                    continue

                is_short   = trade.get("direction", "LONG") == "SHORT"
                rpnl_lookup = underlying if is_opt_pos else sym
                if rpnl_lookup in realized_pnl_map and realized_pnl_map[rpnl_lookup] != 0.0:
                    import math as _math
                    rpnl = realized_pnl_map[rpnl_lookup]
                    if not _math.isnan(rpnl):
                        pnl = rpnl
                    else:
                        mult = 100 if is_opt_pos else 1
                        if is_short:
                            pnl = (trade["entry"] - exit_price) * trade["qty"] * mult
                        else:
                            pnl = (exit_price - trade["entry"]) * trade["qty"] * mult
                else:
                    mult = 100 if is_opt_pos else 1
                    if is_short:
                        pnl = (trade["entry"] - exit_price) * trade["qty"] * mult
                    else:
                        pnl = (exit_price - trade["entry"]) * trade["qty"] * mult

                exit_reason = "stop_loss" if pnl < 0 else "take_profit"
                clog("TRADE", f"External close detected: {sym} | Exit ${exit_price:.2f} | P&L ${pnl:+.2f} | {exit_reason}")

                log_trade(
                    trade=trade,
                    agent_outputs={},
                    regime=regime,
                    action="CLOSE",
                    outcome={
                        "exit_price": round(exit_price, 2),
                        "pnl":        round(pnl, 2),
                        "reason":     exit_reason,
                    }
                )

                dash["trades"].insert(0, {
                    "side":   "SELL",
                    "symbol": sym,
                    "price":  str(round(exit_price, 2)),
                    "time":   datetime.now().strftime("%H:%M:%S"),
                    "pnl":    round(pnl, 2),
                })

                from learning import get_performance_summary, load_trades as lt
                dash["all_trades"]  = lt()
                dash["performance"] = get_performance_summary(lt())

                del open_trades[sym]
                dash["positions"] = get_open_positions()

                if pnl >= 0:
                    from risk import record_win
                    record_win()
                else:
                    from risk import record_loss
                    record_loss(source="external")

    except Exception as e:
        clog("ERROR", f"External close check error: {e}")


def check_options_positions():
    """Monitor open options positions for profit target, stop loss, and DTE exits."""
    from orders import open_trades
    ib = bot_state.ib
    if not CONFIG.get("options_enabled"):
        return
    try:
        opts = {k: v for k, v in open_trades.items() if v.get("instrument") == "option"}
        if not opts:
            return
        to_exit = check_options_exits(opts, ib)
        for opt_key in to_exit:
            clog("TRADE", f"Closing options position: {opt_key}")
            sold = execute_sell_option(ib, opt_key, reason="exit_condition")
            if sold:
                dash["positions"] = get_open_positions()
            else:
                clog("WARN", f"Option sell failed for {opt_key} — will retry next cycle (with backoff)")
    except Exception as e:
        clog("ERROR", f"Options position check error: {e}")


# ── Scan helpers ──────────────────────────────────────────────────────────────

def _check_kill():
    """Check if kill switch was activated. Returns True if scan should abort."""
    if dash.get("killed") or dash.get("ibkr_disconnected"):
        dash["scanning"] = False
        return True
    return False


def _process_close_queue():
    """Process individual position close requests (safe to call from main thread)."""
    ib = bot_state.ib
    close_queue = dash.pop("_close_queue", [])
    for sym in close_queue:
        try:
            from orders import close_position
            result = close_position(ib, sym)
            if result:
                clog("TRADE", f"✅ Close order placed for {sym}: {result}")
                dash["positions"] = get_open_positions()
            else:
                clog("ERROR", f"❌ Could not close {sym} — not found in portfolio")
        except Exception as e:
            clog("ERROR", f"❌ Close {sym} failed: {e}")


def _auto_rebalance_cash(portfolio_value: float, regime: dict):
    """
    Auto-close the weakest position(s) to bring cash reserve back above
    min_cash_reserve.  Closes ONE position per scan cycle.
    """
    ib = bot_state.ib
    min_reserve = CONFIG.get("min_cash_reserve", 0.10)
    positions   = get_open_positions()

    if not positions:
        clog("RISK", "Auto-rebalance: No positions to close")
        return

    from risk import _get_ibkr_cash
    ibkr_cash = _get_ibkr_cash(ib, CONFIG.get("active_account", ""))
    if ibkr_cash is not None:
        cash_pct = ibkr_cash / portfolio_value if portfolio_value > 0 else 1.0
    else:
        deployed = sum(p.get("current", p.get("entry", 0)) * p.get("qty", 0) for p in positions)
        cash_pct = (portfolio_value - deployed) / portfolio_value if portfolio_value > 0 else 1.0
    cash_deficit = (min_reserve - cash_pct) * portfolio_value
    clog("RISK", f"Auto-rebalance: cash={cash_pct*100:.1f}% (need {min_reserve*100:.0f}%) "
         f"— need to free ~${cash_deficit:,.0f}")

    ranked = []
    for p in positions:
        entry   = p.get("entry", 0)
        current = p.get("current", entry)
        qty     = p.get("qty", 0)
        if entry > 0 and qty != 0:
            pnl_pct        = (current - entry) / entry
            position_value = abs(current * qty)
            ranked.append({
                "symbol":         p.get("symbol"),
                "pnl_pct":        pnl_pct,
                "position_value": position_value,
                "entry":          entry,
                "current":        current,
                "qty":            qty,
            })

    if not ranked:
        clog("RISK", "Auto-rebalance: Could not evaluate positions")
        return

    ranked.sort(key=lambda x: x["pnl_pct"])
    worst = ranked[0]
    sym   = worst["symbol"]
    clog("RISK", f"Auto-rebalance: Closing {sym} (worst P&L: {worst['pnl_pct']:+.1%}, "
         f"value: ${worst['position_value']:,.0f}) to free cash")

    try:
        from orders import close_position
        result = close_position(ib, sym)
        if result:
            clog("RISK", f"Auto-rebalance: {result}")
            ib.sleep(2)
        else:
            clog("ERROR", f"Auto-rebalance: Could not close {sym}")
    except Exception as e:
        clog("ERROR", f"Auto-rebalance: Failed to close {sym}: {e}")


# ── Main scan ─────────────────────────────────────────────────────────────────

def run_scan():
    ib = bot_state.ib

    if _check_kill():
        return

    if dash["paused"]:
        clog("INFO", "Bot is paused — skipping scan")
        return

    # Hot reload check (access bot module via sys.modules to avoid circular import)
    _bot_mod = sys.modules.get("bot")
    if _bot_mod:
        try:
            _bot_mod.check_and_reload()
        except Exception:
            pass

    bot_state.scan_count += 1
    dash["scan_count"]  = bot_state.scan_count
    dash["last_scan"]   = datetime.now().strftime("%H:%M:%S")
    dash["scanning"]    = True
    dash["session"]     = get_session()

    dash["recent_orders"] = []
    dash["trades"]        = []
    dash["_scan_start"]   = datetime.now().isoformat()

    clog("SCAN", f"Scan #{bot_state.scan_count} started | Session: {dash['session']}")

    if not ib.isConnected():
        clog("ERROR", "IBKR disconnected — attempting reconnect...")
        if not connect_ibkr():
            clog("ERROR", "Reconnect failed — skipping scan")
            dash["scanning"] = False
            return

    pv, pnl = get_account_data()
    dash["portfolio_value"] = pv
    dash["daily_pnl"]       = pnl

    if pv > 0:
        newly_halted = update_equity_high_water_mark(pv)
        if newly_halted:
            clog("RISK", "⛔ DRAWDOWN BRAKE: drawdown limit exceeded — flattening all positions")
            flatten_all(ib)
            dash["scanning"] = False
            return

    clog("INFO", f"Portfolio: ${pv:,.2f} | DayP&L: ${pnl:+,.2f} | Positions: {len(get_open_positions())}")

    update_positions_from_ibkr(ib)
    update_tranche_status(ib)
    update_trailing_stops(ib)
    dash["positions"] = get_open_positions()

    if get_session() == "OVERNIGHT":
        clog("INFO", "Overnight — pipeline sleeping. Sentinel monitoring news.")
        return

    check_options_positions()

    clog("INFO", "Detecting market regime...")
    regime = get_market_regime(ib)
    _vix_val       = regime.get("vix") or 0
    _rr_threshold  = CONFIG.get("regime_router_vix_threshold", 20)
    if CONFIG.get("regime_routing_enabled", True):
        _router_state = "momentum" if _vix_val and _vix_val < _rr_threshold else "mean_reversion"
    else:
        _router_state = "disabled"
    regime["regime_router"] = _router_state
    dash["regime"] = regime
    from risk import get_sizing_state
    dash["regime"].update(get_sizing_state())
    clog("INFO", f"Regime: {regime['regime']} | VIX: {_vix_val} | SPY: ${regime['spy_price']} | Router: {_router_state}")
    set_session_opening_regime(regime["regime"])

    check_external_closes(regime)

    tradeable, reason = check_risk_conditions(pv, pnl, regime, get_open_positions(), ib=ib)
    if not tradeable:
        if "Cash reserve too low" in reason:
            clog("RISK", f"Cash reserve below minimum — auto-rebalancing to free up cash")
            _auto_rebalance_cash(pv, regime)
            pv, pnl = get_account_data()
            dash["portfolio_value"] = pv
            dash["daily_pnl"]       = pnl
            dash["positions"]       = get_open_positions()
            tradeable, reason = check_risk_conditions(pv, pnl, regime, get_open_positions(), ib=ib)

        if not tradeable:
            clog("RISK", f"Trading suspended: {reason}")
            dash["claude_analysis"] = f"Trading suspended: {reason}"
            dash["scanning"]        = False
            return

    strategy_mode = get_intraday_strategy_mode(pv, pnl, regime["regime"])
    if strategy_mode["mode"] != "NORMAL":
        clog("RISK", f"Strategy mode: {strategy_mode['mode']} | "
                     f"PnL={strategy_mode['daily_pnl_pct']*100:+.1f}% | "
                     f"Streak={get_consecutive_losses()} | "
                     f"ScoreAdj=+{strategy_mode['score_threshold_adj']} | "
                     f"SizeMult={strategy_mode['size_multiplier']}x | "
                     f"MaxTrades={strategy_mode['max_new_trades']}")
    if strategy_mode["regime_changed"]:
        clog("RISK", "Regime changed since session open — thesis check active for open positions")

    clog("SCAN", "Building dynamic universe from TradingView screener...")
    universe = get_dynamic_universe(ib, regime)
    favs     = dash.get("favourites", [])
    if favs:
        before   = len(universe)
        universe = list(set(universe + favs))
        new_count = len(universe) - before
        clog("INFO", f"Favourites: {len(favs)} tickers ({new_count} new additions to universe)")
    clog("INFO", f"Universe: {len(universe)} symbols to score")

    clog("SCAN", "Running signal pipeline (TV pre-filter → sentiment → 9-dim score)...")
    pipeline = run_signal_pipeline(
        universe=universe,
        regime=regime,
        strategy_mode=strategy_mode,
        session=get_session(),
        favourites=favs,
        tv_cache=get_tv_signal_cache(),
    )
    signals        = pipeline.signals
    scored         = pipeline.scored
    news_sentiment = pipeline.news_sentiment
    universe       = pipeline.universe
    regime_name    = pipeline.regime_name

    dash["news_data"] = news_sentiment
    clog("SCAN", f"Pipeline: {len(universe)} symbols → {len(scored)} scored "
         f"→ {len(signals)} signals [{regime_name}]")

    update_position_prices(pipeline.scored)

    if _check_kill():
        return
    _process_close_queue()

    news = get_news_headlines()
    fx   = get_fx_snapshot()

    options_signals = []
    if CONFIG.get("options_enabled"):
        try:
            clog("ANALYSIS", "Scanning options flow (unusual vol, IV rank, earnings)...")
            top_scored_syms = [s["symbol"] for s in scored[:20]]
            favs_for_opts   = dash.get("favourites", [])
            extra           = list(set(top_scored_syms + favs_for_opts))
            options_signals = scan_options_universe(extra_symbols=extra, regime=regime)
            clog("ANALYSIS", f"Options scan: {len(options_signals)} notable setups found")
        except Exception as _opts_err:
            clog("ERROR", f"Options scanner error: {_opts_err}")

    if _check_kill():
        return
    _process_close_queue()

    clog("ANALYSIS", "Running 6-agent analysis pipeline...")
    open_pos = get_open_positions()

    positions_to_reconsider = check_thesis_validity(open_pos, regime["regime"])
    if positions_to_reconsider:
        clog("RISK", f"Thesis invalidation: {len(positions_to_reconsider)} position(s) flagged "
                     f"for agent review (regime shift)")
        for _p in positions_to_reconsider:
            clog("RISK", f"  Reconsider: {_p['symbol']} — {_p['reason']}")

    decision = run_all_agents(
        signals=scored,
        regime=regime,
        news=news,
        fx_data=fx,
        open_positions=open_pos,
        portfolio_value=pv,
        daily_pnl=pnl,
        options_signals=options_signals,
        strategy_mode=strategy_mode,
        positions_to_reconsider=positions_to_reconsider,
    )

    dash["claude_analysis"]    = decision.get("summary", decision.get("claude_reasoning", ""))
    dash["agent_outputs"]      = decision.get("_agent_outputs", {})
    dash["last_agents_agreed"] = decision.get("agents_agreed", 0)

    now_str    = datetime.now().strftime("%H:%M:%S")
    agent_convo = []
    agent_names = [
        ("technical",   "Technical Analyst",  "Analyses price action, volume, and all 7 indicator dimensions"),
        ("macro",       "Macro Analyst",      "Assesses market regime, VIX, cross-asset dynamics, and news flow"),
        ("opportunity", "Opportunity Finder",  "Synthesises technical + macro to find the top 3 trades"),
        ("devils",      "Devil's Advocate",    "Argues against every proposed trade to protect capital"),
        ("risk",        "Risk Manager",        "Sizes positions and flags portfolio-level risk"),
    ]
    outputs = decision.get("_agent_outputs", {})
    for key, name, role_desc in agent_names:
        raw = outputs.get(key, "")
        if raw:
            agent_convo.append({
                "agent":  name,
                "role":   role_desc,
                "time":   now_str,
                "output": raw[:800],
            })
    _buys  = decision.get("buys", [])
    _sells = decision.get("sells", [])
    _holds = decision.get("hold", [])
    _action_lines = []
    for _b in _buys:
        _sym    = _b.get("symbol", "?") if isinstance(_b, dict) else _b
        _reason = _b.get("reasoning", "No reason given") if isinstance(_b, dict) else "No reason given"
        _action_lines.append(f"BUY {_sym} — {_reason}")
    for _s in _sells:
        _sym = _s if isinstance(_s, str) else _s.get("symbol", str(_s))
        _action_lines.append(f"SELL {_sym}")
    for _h in _holds:
        _sym = _h if isinstance(_h, str) else _h.get("symbol", str(_h))
        _action_lines.append(f"HOLD {_sym}")
    _final_output = "\n".join(_action_lines) if _action_lines else "No trades this cycle."
    agent_convo.append({
        "agent":  "Final Decision Maker",
        "role":   "Synthesises all 5 reports into executable trade instructions",
        "time":   now_str,
        "output": _final_output,
    })
    dash["agent_conversation"] = agent_convo

    clog("ANALYSIS", f"Agents agreed: {decision.get('agents_agreed',0)}/6 | {decision.get('summary','')}")

    if dash.get("killed"):
        clog("RISK", "🚨 Kill switch active — skipping all trade execution")
        dash["scanning"] = False
        return

    if decision.get("cash"):
        clog("RISK", "Agents instructed: go to cash — flattening all positions")
        flatten_all(ib)
        dash["scanning"] = False
        return

    from orders import open_trades as _open_trades
    for sym in decision.get("sells", []):
        clog("TRADE", f"Selling {sym} on agent signal")
        pos        = next((p for p in open_pos if p["symbol"] == sym), None)
        exit_price = pos["current"] if pos else 0
        execute_sell(ib, sym, reason="Agent sell signal")
        dash["trades"].insert(0, {
            "side": "SELL", "symbol": sym,
            "price": str(exit_price),
            "time": datetime.now().strftime("%H:%M:%S")
        })
        if pos:
            pnl_val = (exit_price - pos["entry"]) * pos["qty"] if pos.get("direction", "LONG") == "LONG" else (pos["entry"] - exit_price) * pos["qty"]
            from learning import log_trade as _log_trade
            _log_trade(
                trade=pos,
                agent_outputs=decision.get("_agent_outputs", {}),
                regime=regime,
                action="CLOSE",
                outcome={
                    "exit_price": round(exit_price, 4),
                    "pnl":        round(pnl_val, 2),
                    "reason":     "agent_sell",
                }
            )

    if dash.get("killed"):
        clog("RISK", "🚨 Kill switch active — skipping buy execution")
        dash["scanning"] = False
        return

    tradeable_now, reason_now = check_risk_conditions(pv, pnl, regime, get_open_positions(), ib=ib)
    if not tradeable_now:
        clog("RISK", f"Trading suspended before buy execution: {reason_now}")
        dash["scanning"] = False
        return

    _all_buys = decision.get("buys", [])
    _max_buys = strategy_mode.get("max_new_trades", 3)
    if len(_all_buys) > _max_buys:
        clog("RISK", f"Strategy mode cap: {len(_all_buys)} agent buys → {_max_buys} "
                     f"(mode: {strategy_mode['mode']})")
        _all_buys = _all_buys[:_max_buys]

    for buy in _all_buys:
        sym      = buy.get("symbol") if isinstance(buy, dict) else buy
        qty_hint = buy.get("qty")    if isinstance(buy, dict) else None
        reason   = buy.get("reasoning", "") if isinstance(buy, dict) else ""

        sig = next((s for s in scored if s["symbol"] == sym), None)

        if not sig:
            clog("INFO", f"{sym} not in scored list — fetching signal data for agent-recommended symbol")
            for _attempt in range(3):
                try:
                    raw = fetch_multi_timeframe(sym)
                    if raw:
                        raw["score"] = max(raw.get("score", 0), 30)
                        sig = raw
                        break
                    time.sleep(2)
                except Exception:
                    time.sleep(2)
            if not sig:
                clog("INFO", f"No signal data for {sym} after 3 attempts — skipping")
                continue

        clog("TRADE", f"Buying {sym} | Score={sig['score']}/50 | {reason[:80]}")

        buy_signal = next((s for s in signals if s.symbol == sym), None)
        if buy_signal is None:
            buy_signal = Signal(
                symbol=sym,
                direction="LONG",
                conviction_score=round(sig.get("score", 30) / 5.0, 3),
                dimension_scores=sig.get("score_breakdown", {}),
                timestamp=datetime.now(timezone.utc),
                regime_context=regime_name,
                price=sig["price"],
                atr=sig["atr"],
                candle_gate=sig.get("candle_gate", "UNKNOWN"),
            )
        buy_signal.rationale     = reason
        buy_signal.source_agents = list(range(decision.get("agents_agreed", 0)))

        dispatch_results = _dispatch_signals(
            [buy_signal],
            ib=ib,
            portfolio_value=pv,
            regime=regime,
            account_id=CONFIG.get("active_account", ""),
            agent_outputs=decision.get("_agent_outputs", {}),
        )
        stock_success = any(r["success"] for r in dispatch_results)
        if stock_success:
            dash["trades"].insert(0, {
                "side": "BUY", "symbol": sym,
                "price": str(sig["price"]),
                "time": datetime.now().strftime("%H:%M:%S")
            })

        from orders import is_options_market_open
        if (CONFIG.get("options_enabled") and
                sig["score"] >= CONFIG.get("options_min_score", 42)):
            if not is_options_market_open():
                clog("INFO", f"Score {sig['score']} qualifies for options but market closed — will retry next open scan")
            else:
                direction = "LONG" if sig.get("direction", "LONG") == "LONG" else "SHORT"
                clog("TRADE", f"Score {sig['score']} qualifies for options — evaluating {sym} {direction}")
                try:
                    contract_info = find_best_contract(sym, direction, pv, ib, regime, score=sig["score"])
                    if contract_info:
                        opt_success = execute_buy_option(ib, contract_info, pv, reasoning=reason)
                        if opt_success:
                            dash["trades"].insert(0, {
                                "side":   f"BUY {contract_info['right']} OPT",
                                "symbol": f"{sym} ${contract_info['strike']:.0f} {contract_info['expiry_str']}",
                                "price":  str(contract_info["mid"]),
                                "time":   datetime.now().strftime("%H:%M:%S")
                            })
                            clog("TRADE", f"Options trade executed for {sym} (independent of stock)")
                    else:
                        clog("INFO", f"No suitable options contract for {sym}")
                except Exception as _opt_err:
                    clog("ERROR", f"Options evaluation failed for {sym}: {_opt_err}")

    dash["positions"] = get_open_positions()
    _seen_dash = {}
    _deduped   = []
    for _t in dash["trades"]:
        _key = f"{_t.get('side','')}-{_t.get('symbol','')}-{_t.get('time','')[:5]}"
        if _key not in _seen_dash:
            _seen_dash[_key] = True
            _deduped.append(_t)
    dash["trades"] = _deduped[:200]

    sync_orders_from_ibkr()

    all_trades = load_trades()
    dash["all_trades"]  = all_trades
    dash["all_orders"]  = load_orders()
    _scan_start = dash.get("_scan_start")
    if _scan_start:
        dash["recent_orders"] = [o for o in dash["all_orders"] if (o.get("timestamp") or "") >= _scan_start]
    else:
        dash["recent_orders"] = dash["all_orders"]
    dash["performance"] = get_performance_summary(all_trades)
    dash["performance"]["total_pnl"] = round(dash.get("portfolio_value", 0) - get_effective_capital(), 2)

    dash["equity_history"].append({
        "date":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        "value": pv
    })
    if len(dash["equity_history"]) > 2000:
        dash["equity_history"] = dash["equity_history"][-2000:]
    save_equity_history(dash["equity_history"])

    today = datetime.now().weekday()
    if today == 6 and bot_state.last_sunday_review != datetime.now().date():
        clog("ANALYSIS", "Running weekly performance review...")
        review = run_weekly_review()
        clog("ANALYSIS", f"Weekly review: {review[:200]}...")

        try:
            from ic_calculator import update_ic_weights
            new_weights = update_ic_weights()
            clog("ANALYSIS", "IC weights updated: " +
                 ", ".join(f"{k}={v:.3f}" for k, v in new_weights.items()))
        except Exception as _ic_exc:
            log.warning("IC weight update failed: %s", _ic_exc)

        bot_state.last_sunday_review = datetime.now().date()

    dash["scanning"] = False
    clog("SCAN", f"Scan #{bot_state.scan_count} complete")
