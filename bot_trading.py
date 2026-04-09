#!/usr/bin/env python3
"""
bot_trading.py — Core trading pipeline for the Decifer trading bot.

Covers: run_scan (main loop), external-close detection, options position
monitoring, kill-switch check, close-queue processing, and cash rebalancing.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys
import time
import threading
import zoneinfo
from datetime import datetime, timezone

_ET = zoneinfo.ZoneInfo("America/New_York")

from config import CONFIG
import bot_state
from bot_state import dash, clog

from bot_account import get_account_data, get_account_details, get_news_headlines, get_fx_snapshot, save_equity_history
from bot_ibkr import sync_orders_from_ibkr, connect_ibkr

from scanner import get_dynamic_universe, get_market_regime, get_tv_signal_cache
from signals import fetch_multi_timeframe
from agents import run_all_agents
from orders import (execute_buy, execute_sell, execute_short, flatten_all,
                    get_open_positions, update_position_prices,
                    update_positions_from_ibkr, execute_buy_option,
                    execute_sell_option, update_trailing_stops,
                    update_tranche_status, flush_pending_option_exits)
from options import find_best_contract, check_options_exits
from options_scanner import scan_options_universe
from risk import (check_risk_conditions, get_session, get_scan_interval,
                  calculate_position_size, calculate_stops,
                  update_equity_high_water_mark,
                  init_equity_high_water_mark_from_history,
                  get_intraday_strategy_mode, set_session_opening_regime,
                  check_thesis_validity, get_consecutive_losses)
from risk_gates import auto_rebalance_cash
from orders import _options_attempted_today, _record_options_attempt
from learning import (log_trade, load_trades, load_orders,
                      get_performance_summary, run_weekly_review,
                      TRADE_LOG_FILE, get_effective_capital)
from signal_types import Signal
from signal_dispatcher import dispatch_signals as _dispatch_signals
from signal_pipeline import run_signal_pipeline, SignalPipelineResult
from portfolio_manager import run_portfolio_review, lightweight_cycle_check

log = logging.getLogger("decifer.bot")

# ── EOD options review state ──────────────────────────────────────────────────
_eod_options_review_done: bool = False

# ── Portfolio manager state ───────────────────────────────────────────────────
_portfolio_review_done_today: bool = False
_last_known_regime: str = ""
_session_stop_count: int = 0
_cascade_reviewed_this_session: bool = False   # prevent cascade from re-firing every loop
_last_trim_ts: dict = {}                        # symbol → datetime of last TRIM execution

# ── Last-decision writer (for Chief Decifer trade card) ───────────────────────

def _synthesize_trade_card(symbol: str, company_name: str,
                           opp_text: str, dev_text: str, tech_text: str,
                           price: float, sl: float, tp: float,
                           score: int, api_key: str) -> dict:
    """
    Call Claude Haiku to synthesize a clean thesis/edge/risk from raw agent outputs.
    Returns dict with keys: thesis, edge_why_now, risk.
    Raises on API failure so caller can fall back gracefully.
    """
    import re as _re
    import anthropic

    sl_pct = round(abs(price - sl) / price * 100, 1) if price > 0 and sl > 0 else 0
    tp_pct = round(abs(tp - price) / price * 100, 1) if price > 0 and tp > 0 else 0

    prompt = (
        f"You are summarizing a live trading decision for {symbol} ({company_name}).\n\n"
        f"Signal score: {score}/50  |  Entry: ${price:.2f}  |  "
        f"Stop: ${sl:.2f} (-{sl_pct}%)  |  Target: ${tp:.2f} (+{tp_pct}%)\n\n"
        f"OPPORTUNITY AGENT:\n{opp_text[:2500]}\n\n"
        f"DEVIL'S ADVOCATE:\n{dev_text[:1500]}\n\n"
        f"TECHNICAL AGENT (excerpt):\n{tech_text[:800]}\n\n"
        f"Write exactly three labelled fields. Be specific to {symbol} — no generic filler.\n\n"
        f"THESIS: [2 sentences. Why this stock, what structural or technical theme supports entry.]\n"
        f"EDGE: [1 sentence. The specific catalyst, breakout level, or time-sensitive setup that "
        f"makes this actionable RIGHT NOW. Must add new information beyond the thesis.]\n"
        f"RISK: [1 sentence. The most specific bear case from the devil's advocate — "
        f"what could make this trade wrong.]"
    )

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()

    result: dict = {}
    for label, key in [("THESIS", "thesis"), ("EDGE", "edge_why_now"), ("RISK", "risk")]:
        m = _re.search(
            rf"{label}:\s*(.+?)(?=(?:THESIS:|EDGE:|RISK:)|\Z)",
            text, _re.DOTALL | _re.IGNORECASE,
        )
        if m:
            result[key] = m.group(1).strip()
    return result


def _write_last_decision(symbol: str, buy: dict, sig: dict, decision: dict,
                         portfolio_value: float) -> None:
    """
    Write data/last_decision.json after a successful trade so Chief Decifer
    can display a rich trade card on its home page. Works for LONG and SHORT.
    """
    import json, os, re
    from pathlib import Path

    outputs   = decision.get("_agent_outputs", {})
    opp_text  = outputs.get("opportunity", "")
    dev_text  = outputs.get("devils", "")
    tech_text = outputs.get("technical", "")

    price  = sig.get("price", 0)
    qty    = buy.get("qty", 1)
    sl     = buy.get("sl", 0)
    tp     = buy.get("tp", 0)
    score  = sig.get("score", 20)
    alloc  = round((qty * price / portfolio_value * 100), 1) if portfolio_value > 0 else 0

    # ── Company name (best-effort) ────────────────────────────────────────────
    company_name = symbol
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).fast_info
        long_name = getattr(info, "company_name", None) or getattr(info, "longName", None)
        if not long_name:
            full = yf.Ticker(symbol).info
            long_name = full.get("longName") or full.get("shortName")
        if long_name:
            company_name = long_name
    except Exception:
        pass

    # ── Claude synthesis of thesis / edge / risk ──────────────────────────────
    api_key = CONFIG.get("anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    synthesis: dict = {}
    if api_key and api_key not in ("YOUR_API_KEY_HERE", ""):
        try:
            synthesis = _synthesize_trade_card(
                symbol, company_name, opp_text, dev_text, tech_text,
                price, sl, tp, score, api_key,
            )
            clog("INFO", f"Claude trade card synthesis complete for {symbol}")
        except Exception as exc:
            clog("WARN", f"Claude synthesis failed for {symbol}, using fallback: {exc}")

    # Fallback extraction if Claude synthesis unavailable or incomplete
    reasoning = buy.get("reasoning", "")

    thesis = synthesis.get("thesis") or (
        reasoning[:400] if reasoning else f"{symbol} selected by AI agent council"
    )

    edge = synthesis.get("edge_why_now") or ""
    if not edge and reasoning:
        # Last-resort regex: find a sentence with timing language distinct from thesis start
        timing_kws = ("catalyst", "announ", "break", "decis", "approv", "launch",
                      "event", "earning", "FDA", "coming", "imminent", "near-term",
                      "upcoming", "breakout", "momentum", "volume spike")
        sentences = re.split(r"(?<=[.!?])\s+", reasoning)
        for i, sent in enumerate(sentences):
            if i == 0:
                continue  # always skip first — it's already the thesis
            if any(kw.lower() in sent.lower() for kw in timing_kws):
                edge = sent.strip()
                break

    risk = synthesis.get("risk") or ""
    if not risk and dev_text:
        upper_dev = dev_text.upper()
        idx = upper_dev.find(symbol)
        if idx != -1:
            section = dev_text[idx:idx + 800]
            m = re.search(
                r"(?:KEY\s+RISK|RISK[:\s]|MAIN\s+CONCERN)[:\s]+(.+?)(?:\n[0-9A-Z]|\Z)",
                section, re.IGNORECASE | re.DOTALL,
            )
            if m:
                risk = m.group(1).strip()[:250]
            else:
                for sent in re.split(r"(?<=[.!?])\s+", section):
                    if any(kw in sent.lower() for kw in ("risk", "wrong", "concern", "veto", "fail")):
                        risk = sent.strip()[:250]
                        break
    if not risk:
        risk = "No specific risk identified by devil's advocate this cycle."

    direction = buy.get("direction", "LONG")

    # ── Price targets — honest representation of stops ────────────────────────
    # Labelled as targets, not forecasts. Validated per direction.
    price_targets: dict = {}
    if direction == "LONG":
        valid_targets = price > 0 and tp > price and sl > 0 and sl < price
    else:  # SHORT
        valid_targets = price > 0 and tp < price and sl > 0 and sl > price
    if valid_targets:
        tp_pct = round(abs(tp - price) / price * 100, 1)
        sl_pct = round(abs(price - sl) / price * 100, 1)
        rr     = round(tp_pct / sl_pct, 1) if sl_pct else 0
        price_targets = {
            "target_pct": tp_pct if direction == "LONG" else -tp_pct,
            "stop_pct":   -sl_pct if direction == "LONG" else sl_pct,
            "rr_ratio":   rr,
            "target_price": round(tp, 2),
            "stop_price":   round(sl, 2),
        }

    payload = {
        "symbol":          symbol,
        "company_name":    company_name,
        "direction":       direction,
        "allocation_pct":  alloc,
        "price":           round(price, 2),
        "qty":             qty,
        "stop_loss":       round(sl, 2),
        "take_profit":     round(tp, 2),
        "score":           score,
        "thesis":          thesis,
        "edge_why_now":    edge,
        "risk":            risk,
        "price_targets":   price_targets,
        "agents_agreed":   decision.get("agents_agreed", 0),
        "timestamp":       datetime.now(_ET).isoformat(timespec="seconds"),
    }

    out_path = Path(__file__).parent / "data" / "last_decision.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2))
        clog("INFO", f"last_decision.json written for {symbol}")
    except Exception as e:
        clog("ERROR", f"Could not write last_decision.json: {e}")

    # Append to decision history so dashboard can navigate back through all trades
    hist_path = Path(__file__).parent / "data" / "decision_history.jsonl"
    try:
        with hist_path.open("a") as hf:
            hf.write(json.dumps(payload) + "\n")
    except Exception as e:
        clog("ERROR", f"Could not append to decision_history.jsonl: {e}")


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

        # Fetch open IBKR orders once — avoids N IPC calls (one per PENDING trade)
        try:
            _active_order_ids = {t.order.orderId for t in ib.openTrades()}
        except Exception:
            _active_order_ids = None  # fail safe: assume all orders still active

        for sym in list(open_trades.keys()):
            if sym not in ibkr_syms:
                trade = open_trades[sym]

                if trade.get("status") == "PENDING":
                    order_id = trade.get("order_id")
                    if _active_order_ids is None:
                        still_active = True  # can't confirm — play safe
                    else:
                        still_active = order_id is not None and order_id in _active_order_ids
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
                        _fill_order_id = getattr(sell_fills[-1].execution, "orderId", None)
                    else:
                        _fill_order_id = None
                except Exception:
                    _fill_order_id = None

                rpnl_key = underlying if is_opt_pos else sym
                if exit_price is None and rpnl_key in realized_pnl_map:
                    rpnl = realized_pnl_map[rpnl_key]
                    qty  = trade["qty"]
                    mult = 100 if is_opt_pos else 1
                    if qty and not _math.isnan(rpnl) and rpnl != 0.0:
                        exit_price = round(trade["entry"] + rpnl / (qty * mult), 4)

                if exit_price is None and not is_opt_pos:
                    # Fill event was lost (connectivity blip, reconnect).  Use the
                    # recorded stop_loss price as a best-effort exit so the trade
                    # lands in learning history rather than being silently discarded.
                    stop_px = trade.get("stop_loss") or trade.get("sl")
                    if stop_px:
                        exit_price = float(stop_px)
                        clog("WARN", f"No fill evidence for {sym} — estimating exit at stop_loss ${exit_price:.2f}")
                    else:
                        clog("INFO", f"No fill evidence for {sym} — removing from tracker (not logging as trade)")
                        del open_trades[sym]
                        continue
                elif exit_price is None:
                    clog("INFO", f"No fill evidence for {sym} — removing from tracker (not logging as trade)")
                    del open_trades[sym]
                    continue

                import math as _math
                is_short    = trade.get("direction", "LONG") == "SHORT"
                rpnl_lookup = underlying if is_opt_pos else sym
                mult        = 100 if is_opt_pos else 1
                manual_pnl  = ((trade["entry"] - exit_price) if is_short else (exit_price - trade["entry"])) * trade["qty"] * mult
                rpnl        = realized_pnl_map.get(rpnl_lookup, 0.0)
                pnl         = rpnl if (rpnl != 0.0 and not _math.isnan(rpnl)) else manual_pnl

                sl_order_id = trade.get("sl_order_id")
                # ── Determine mechanical exit type ─────────────────────────
                if sl_order_id and _fill_order_id and int(_fill_order_id) == int(sl_order_id):
                    exit_type = "sl_hit"
                elif pnl > 0 and trade.get("tp"):
                    tp       = trade.get("tp")
                    hit_tp   = (not is_short and exit_price >= tp * 0.99) or \
                               (is_short and exit_price <= tp * 1.01)
                    exit_type = "tp_hit" if hit_tp else "manual"
                else:
                    exit_type = "manual"
                # ── Build thesis-level reason (GAP-002) ────────────────────
                entry_regime  = trade.get("entry_regime", "UNKNOWN")
                # Prefer session_character in regime dict (set by dispatcher) so the
                # exit label uses the same vocabulary as the entry label.
                exit_regime   = (
                    (regime.get("session_character") or regime.get("regime", "UNKNOWN"))
                    if isinstance(regime, dict) else "UNKNOWN"
                )
                trade_type_ex = trade.get("trade_type", "SCALP")
                try:
                    held_mins = int(
                        (datetime.now(timezone.utc) -
                         datetime.fromisoformat(trade["open_time"].replace("Z", "+00:00")))
                        .total_seconds() / 60
                    )
                except Exception:
                    held_mins = 0
                # Compare polarities (BULL/BEAR) rather than exact strings so that
                # mixed-vocabulary comparisons (e.g. RELIEF_RALLY vs BULL_TRENDING)
                # don't spuriously trigger breached_regime_shift.
                def _polarity(s: str) -> str:
                    r = (s or "").upper()
                    if r in ("MOMENTUM_BULL", "RELIEF_RALLY") or "BULL" in r:
                        return "BULL"
                    if r in ("TRENDING_BEAR", "DISTRIBUTION") or "BEAR" in r:
                        return "BEAR"
                    return ""
                entry_pol = _polarity(entry_regime)
                exit_pol  = _polarity(exit_regime)
                if exit_type == "tp_hit":
                    thesis_class = "confirmed"
                elif entry_pol and exit_pol and entry_pol != exit_pol:
                    thesis_class = "breached_regime_shift"
                elif trade_type_ex == "SCALP" and held_mins > CONFIG.get("scalp_max_hold_minutes", 90):
                    thesis_class = "breached_stale_scalp"
                else:
                    thesis_class = "noise_stop"
                exit_reason = (
                    f"{exit_type} | {trade_type_ex} | regime:{entry_regime}→{exit_regime}"
                    f" | held:{held_mins}min | thesis:{thesis_class}"
                )
                clog("TRADE", f"External close detected: {sym} | Exit ${exit_price:.2f} | P&L ${pnl:+.2f} | {exit_reason}")

                log_trade(
                    trade=trade,
                    agent_outputs={},
                    regime=regime,
                    action="CLOSE",
                    outcome={
                        "exit_price": round(exit_price, 2),
                        "pnl":        round(pnl, 2),
                        "pnl_pct":    round(pnl / ((trade.get("entry") or 1) * (trade.get("qty") or 1)), 4),
                        "reason":     exit_reason,
                    }
                )

                dash["trades"].insert(0, {
                    "side":   "SELL",
                    "symbol": sym,
                    "price":  str(round(exit_price, 2)),
                    "time":   datetime.now(_ET).strftime("%H:%M:%S"),
                    "pnl":    round(pnl, 2),
                })

                from learning import get_performance_summary, load_trades as lt
                dash["all_trades"]  = lt()
                dash["performance"] = get_performance_summary(lt())

                del open_trades[sym]
                bot_state._sl_fill_events.discard(sym)
                dash["positions"] = get_open_positions()

                if pnl >= 0:
                    from risk import record_win
                    record_win()
                else:
                    from risk import record_loss
                    record_loss(source="external" if exit_reason != "sl_hit" else "sl")
                    global _session_stop_count
                    _session_stop_count += 1

    except Exception as e:
        clog("ERROR", f"External close check error: {e}")


def check_options_positions():
    """Monitor open options positions for profit target, stop loss, and DTE exits."""
    from orders import open_trades, is_options_market_open
    ib = bot_state.ib
    if not CONFIG.get("options_enabled"):
        return
    if not is_options_market_open():
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
                from orders import (_pending_option_exits, _option_sell_attempts,
                                    _MAX_OPTION_SELL_RETRIES, _OPTION_SELL_COOLDOWN)
                if opt_key in _pending_option_exits:
                    clog("INFO", f"Options market closed — {opt_key} queued for next open")
                else:
                    _att = _option_sell_attempts.get(opt_key, {})
                    _cnt = _att.get("count", 0)
                    if _cnt >= _MAX_OPTION_SELL_RETRIES:
                        from datetime import timezone as _tz
                        _elapsed = (datetime.now(_tz.utc) - _att.get("last_try", datetime.min.replace(tzinfo=_tz.utc))).total_seconds()
                        _remaining = max(0, int(_OPTION_SELL_COOLDOWN - _elapsed))
                        clog("WARN", f"Option sell cooling down for {opt_key} — {_cnt} failures, {_remaining}s remaining")
                    else:
                        clog("WARN", f"Option sell failed for {opt_key} — will retry next cycle (attempt {_cnt}/{_MAX_OPTION_SELL_RETRIES})")
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


# ── Pre-close options review ──────────────────────────────────────────────────

def _eod_options_review(regime: dict):
    """
    At 3:30 PM ET, ask Claude whether each open options position should be
    held overnight or closed before the bell.  No hard-coded rules — pure AI
    judgment based on each position's greeks, P&L, and the current regime.
    """
    import json, zoneinfo as _zi
    from agents import _call_claude
    from orders import close_position, get_open_positions

    ib = bot_state.ib
    positions = get_open_positions()
    opts = [p for p in positions if p.get("instrument") == "option"]

    if not opts:
        clog("INFO", "EOD options review: no open options positions")
        return

    clog("ANALYSIS", f"EOD options review: evaluating {len(opts)} position(s) at 3:30 PM")

    # Build a readable context block for each position
    pos_lines = []
    for p in opts:
        entry_prem  = p.get("entry_premium", 0)
        curr_prem   = p.get("current_premium", entry_prem)
        pnl_pct     = ((curr_prem - entry_prem) / entry_prem * 100) if entry_prem else 0
        key         = p.get("_trade_key", p.get("symbol"))
        pos_lines.append(
            f"- Key: {key} | {p.get('right','?')} ${p.get('strike','?')} exp {p.get('expiry_str','?')} "
            f"| DTE: {p.get('dte','?')} | P&L: {pnl_pct:+.1f}% "
            f"| Delta: {p.get('delta','?')} | Theta/day: {p.get('theta','?')} "
            f"| IV: {p.get('iv','?')} | Entry thesis: {str(p.get('reasoning',''))[:120]}"
        )

    regime_str = (
        f"Regime: {regime.get('regime','unknown')} | VIX: {regime.get('vix','?')} | "
        f"Trend: {regime.get('trend','?')}"
    )

    system_prompt = (
        "You are a senior options risk manager conducting a pre-close end-of-day review. "
        "For each open options position, decide whether it should be HOLD (carry overnight) "
        "or CLOSE (exit before today's bell). "
        "Consider: DTE and gamma risk near expiry, theta decay cost of holding overnight, "
        "delta exposure relative to regime, current P&L and whether the thesis is still valid. "
        "Lean toward CLOSE when the overnight edge is not clearly positive. "
        "Respond ONLY with a JSON array, one object per position, using the exact key provided:\n"
        '[{"key": "...", "decision": "HOLD", "reason": "..."}, ...]'
    )

    user_message = (
        f"Market regime at 3:30 PM ET:\n{regime_str}\n\n"
        f"Open options positions:\n" + "\n".join(pos_lines) +
        "\n\nReturn your JSON array decision now."
    )

    raw = _call_claude(system_prompt, user_message)

    # Parse Claude's JSON response
    try:
        # Strip markdown fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
            clean = clean.rsplit("```", 1)[0].strip()
        decisions = json.loads(clean)
    except Exception as e:
        clog("ERROR", f"EOD options review: could not parse Claude response — {e}\nRaw: {raw[:300]}")
        return

    for item in decisions:
        key      = item.get("key", "")
        decision = item.get("decision", "HOLD").upper()
        reason   = item.get("reason", "")
        clog("ANALYSIS", f"EOD [{decision}] {key} — {reason}")
        if decision == "CLOSE":
            try:
                sym = key.split("_")[0]  # underlying symbol for close_position lookup
                result = close_position(ib, key) or close_position(ib, sym)
                if result:
                    clog("TRADE", f"EOD closed: {key} — {result}")
                else:
                    clog("ERROR", f"EOD close failed for {key}")
            except Exception as e:
                clog("ERROR", f"EOD close error for {key}: {e}")


def _maybe_eod_options_review(regime: dict):
    """Fire _eod_options_review once per day between 3:30 PM and 3:55 PM ET."""
    global _eod_options_review_done
    import zoneinfo as _zi
    _ET = _zi.ZoneInfo("America/New_York")
    from datetime import time as dtime
    now_et = datetime.now(_ET)
    t = now_et.time()
    # Reset each morning before the session opens
    if t < dtime(9, 30):
        _eod_options_review_done = False
        # Ledger auto-expires by date — no explicit clear needed.
        # Pruning of stale entries happens inside _record_options_attempt.
        global _portfolio_review_done_today, _session_stop_count, _cascade_reviewed_this_session, _last_trim_ts
        _portfolio_review_done_today = False
        _session_stop_count = 0
        _cascade_reviewed_this_session = False
        _last_trim_ts = {}
    # Fire once in the pre-close window
    if dtime(15, 30) <= t < dtime(15, 55) and not _eod_options_review_done:
        _eod_options_review_done = True
        _eod_options_review(regime)


# ── Portfolio review trigger detection ───────────────────────────────────────

def _should_run_portfolio_review(
    session: str,
    regime: dict,
    open_positions: list,
    all_scored: list,
    news_sentiment: dict,
    daily_pnl: float,
    portfolio_value: float,
) -> tuple:
    """
    Return (bool, trigger_name) indicating whether a portfolio review should fire.

    Triggers (checked in priority order):
      pre_market    — once per day before session open
      regime_change — current regime differs from last known
      score_collapse — any held position dropped 15+ pts from entry score
      news_hit      — keyword_score |magnitude| >= 3 on a held symbol
      earnings_risk — any held symbol has earnings within 48 hours
      cascade       — 2+ stop losses hit this session
      drawdown      — daily PnL / portfolio < -1.5%
    """
    global _portfolio_review_done_today, _last_known_regime
    pm_cfg = CONFIG.get("portfolio_manager", {})
    if not pm_cfg.get("enabled", True):
        return False, ""

    # 1. Pre-market: once per day
    if session == "PRE_MARKET" and not _portfolio_review_done_today:
        return True, "pre_market"

    # 2. Regime change
    current_regime = regime.get("regime", "")
    if _last_known_regime and current_regime and current_regime != _last_known_regime:
        return True, "regime_change"

    if not open_positions:
        return False, ""

    # 3. Score collapse: entry_score - current_score >= threshold
    collapse_thresh = pm_cfg.get("score_collapse_threshold", 15)
    scored_map = {s["symbol"]: s.get("score", 0) for s in all_scored}
    for pos in open_positions:
        sym = pos.get("symbol", "")
        entry_sc = pos.get("entry_score", pos.get("score", 0))
        current_sc = scored_map.get(sym)
        if current_sc is not None and (entry_sc - current_sc) >= collapse_thresh:
            return True, "score_collapse"

    # 4. News hit on held position
    news_thresh = pm_cfg.get("news_hit_threshold", 3)
    for pos in open_positions:
        sym = pos.get("symbol", "")
        kw = news_sentiment.get(sym, {}).get("keyword_score", 0)
        if abs(kw) >= news_thresh:
            return True, "news_hit"

    # 5. Earnings within 48 hours
    try:
        from portfolio_manager import _check_earnings_within_hours
        held_syms = [p["symbol"] for p in open_positions]
        lookahead = pm_cfg.get("earnings_lookahead_hours", 48)
        if _check_earnings_within_hours(held_syms, hours=lookahead):
            return True, "earnings_risk"
    except Exception:
        pass

    # 6. Cascade: 2+ stops this session — fire once per session only
    cascade_thresh = pm_cfg.get("cascade_stop_count", 2)
    if _session_stop_count >= cascade_thresh and not _cascade_reviewed_this_session:
        return True, "cascade"

    # 7. Daily drawdown threshold
    drawdown_thresh = pm_cfg.get("drawdown_trigger_pct", -0.015)
    if portfolio_value > 0 and (daily_pnl / portfolio_value) < drawdown_thresh:
        return True, "drawdown"

    return False, ""


# ── Scan helpers ──────────────────────────────────────────────────────────────

def _print_score_table(scored: list, n: int = 10) -> None:
    """Print a ranked score table to terminal after each scan."""
    if not scored:
        return
    top = sorted(scored, key=lambda s: s.get("score", 0), reverse=True)[:n]
    clog("SCAN", f"── Top {len(top)} Signals {'─' * 40}")
    for i, s in enumerate(top, 1):
        sym       = s.get("symbol", "?")
        direction = s.get("direction", "?")
        score     = s.get("score", 0)
        breakdown = s.get("score_breakdown") or {}
        top_dims  = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
        top_dims  = [(k, v) for k, v in top_dims if v > 0][:3]
        dims_str  = "  ".join(f"{k}:{v}" for k, v in top_dims) if top_dims else "—"
        dir_short = {"LONG": "L", "SHORT": "S"}.get(direction, direction)
        clog("SIGNAL", f"#{i:<2} {sym:<8} {dir_short:<5} {score:>2}/50  │ {dims_str}")


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
    dash["last_scan"]   = datetime.now(_ET).strftime("%H:%M:%S")
    dash["scanning"]    = True
    dash["session"]     = get_session()

    dash["recent_orders"] = []
    dash["trades"]        = []
    dash["_scan_start"]   = datetime.now(_ET).isoformat()

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
    flush_pending_option_exits(ib)
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
        _vix_regime = "momentum" if _vix_val and _vix_val < _rr_threshold else "mean_reversion"
        # Hurst DFA second signal
        _hurst_regime = "unknown"
        if CONFIG.get("hurst_regime", {}).get("enabled", False):
            from signals import get_hurst_regime_spy
            _hurst_result = get_hurst_regime_spy()
            _hurst_regime = _hurst_result.get("regime", "unknown")
            regime["hurst_regime"] = _hurst_result
        # HMM third signal — 2-state Gaussian HMM on SPY daily returns
        _hmm_regime = "unknown"
        if CONFIG.get("hmm_regime", {}).get("enabled", False):
            from signals import get_hmm_regime_spy
            _hmm_result = get_hmm_regime_spy()
            _hmm_regime = _hmm_result.get("regime", "unknown")
            regime["hmm_regime"] = _hmm_result
        from signals import _resolve_regime_router
        _router_state = _resolve_regime_router(_vix_regime, _hurst_regime, _hmm_regime)
    else:
        _router_state = "disabled"
    regime["regime_router"] = _router_state
    dash["regime"] = regime
    from risk import get_sizing_state
    dash["regime"].update(get_sizing_state())
    clog("INFO", f"Regime: {regime['regime']} | VIX: {_vix_val} | SPY: ${regime['spy_price']} | Router: {_router_state}")
    set_session_opening_regime(regime["regime"])
    _maybe_eod_options_review(regime)

    check_external_closes(regime)

    tradeable, reason = check_risk_conditions(pv, pnl, regime, get_open_positions(), ib=ib)
    if not tradeable:
        if "Cash reserve too low" in reason:
            clog("RISK", f"Cash reserve below minimum — auto-rebalancing to free up cash")
            auto_rebalance_cash(ib, pv, regime)
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
    # Sector bias is cached inside get_dynamic_universe — fetch the cached result for the dashboard.
    try:
        from scanner import get_sector_rotation_bias as _get_sbias
        dash["sector_bias"] = _get_sbias()
    except Exception:
        pass
    favs     = dash.get("favourites", [])
    if favs:
        before   = len(universe)
        universe = list(set(universe + favs))
        new_count = len(universe) - before
        clog("INFO", f"Favourites: {len(favs)} tickers ({new_count} new additions to universe)")
    clog("INFO", f"Universe: {len(universe)} symbols to score")

    # Pull open positions BEFORE the pipeline so held symbols are always scored.
    # This prevents the portfolio manager from seeing "not_in_universe" on reboot
    # simply because today's TV screener didn't surface a still-valid position.
    open_pos  = get_open_positions()
    held_syms = [p["symbol"] for p in open_pos if p.get("instrument") != "option"]
    if held_syms:
        universe = list(set(universe + held_syms))
        new_held = [s for s in held_syms if s not in favs]
        if new_held:
            clog("INFO", f"Held positions pinned into pipeline universe: {new_held}")
    # Merge held symbols into the protected set so _apply_tv_prefilter never drops them
    pipeline_favs = list(set(favs + held_syms))

    # Refresh Alpaca stream subscriptions to match the finalised universe.
    # update_symbols() is a no-op if the symbol list hasn't changed.
    try:
        import bot_state as _bs
        if _bs._bar_stream is not None:
            _bs._bar_stream.update_symbols(universe)
    except Exception:
        pass

    clog("SCAN", "Running signal pipeline (TV pre-filter → sentiment → 9-dim score)...")
    pipeline = run_signal_pipeline(
        universe=universe,
        regime=regime,
        strategy_mode=strategy_mode,
        session=get_session(),
        favourites=pipeline_favs,
        tv_cache=get_tv_signal_cache(),
        ib=ib,
    )
    signals        = pipeline.signals
    scored         = pipeline.scored
    news_sentiment = pipeline.news_sentiment
    universe       = pipeline.universe
    regime_name    = pipeline.regime_name

    dash["news_data"] = news_sentiment

    # BACK-007 — update directional skew display each scan
    try:
        from learning import get_directional_skew_multi
        dash["skew"] = get_directional_skew_multi()
    except Exception as _skew_err:
        log.debug(f"Skew update skipped: {_skew_err}")

    clog("SCAN", f"Pipeline: {len(universe)} symbols → {len(scored)} scored "
         f"→ {len(signals)} signals [{regime_name}]")
    _print_score_table(scored)

    update_position_prices(pipeline.scored)

    if _check_kill():
        return
    _process_close_queue()

    news = get_news_headlines()
    fx   = get_fx_snapshot()

    options_signals = []
    if CONFIG.get("options_enabled") and get_session() not in ("PRE_MARKET", "AFTER_HOURS"):
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

    clog("ANALYSIS", "Running agent analysis pipeline...")
    # open_pos already fetched above (before pipeline) so held positions were included in scoring
    open_pos = get_open_positions()  # refresh after any close-queue processing

    # ── Lightweight per-cycle position check (every scan, no LLM) ───────────
    _force_pm_review = False
    if open_pos:
        cycle_actions = lightweight_cycle_check(open_pos, regime, pipeline.all_scored)
        for _ca in cycle_actions:
            _sym_ca = _ca.get("symbol", "")
            _act_ca = _ca.get("action", "")
            _rsn_ca = _ca.get("reasoning", "")
            if _act_ca == "EXIT" and _sym_ca:
                from orders import open_trades as _ce_trades, _trades_lock as _ce_lock
                with _ce_lock:
                    _already_ce = _ce_trades.get(_sym_ca, {}).get("status") == "EXITING"
                if _already_ce:
                    clog("INFO", f"Cycle check EXIT: {_sym_ca} already exiting — skipping")
                else:
                    clog("TRADE", f"Cycle check EXIT: {_sym_ca} — {_rsn_ca}")
                    execute_sell(ib, _sym_ca, reason="cycle_check")
                    _pos_ce = next((p for p in open_pos if p["symbol"] == _sym_ca), None)
                    if _pos_ce:
                        _ep_ce = _pos_ce.get("current", _pos_ce.get("entry", 0))
                        _dir_ce = _pos_ce.get("direction", "LONG")
                        _pnl_ce = (
                            (_ep_ce - _pos_ce["entry"]) * _pos_ce["qty"]
                            if _dir_ce == "LONG"
                            else (_pos_ce["entry"] - _ep_ce) * _pos_ce["qty"]
                        )
                        from learning import log_trade as _log_trade_ce
                        _log_trade_ce(
                            trade=_pos_ce,
                            agent_outputs={},
                            regime=regime,
                            action="CLOSE",
                            outcome={
                                "exit_price": round(_ep_ce, 4),
                                "pnl":        round(_pnl_ce, 2),
                                "pnl_pct":    round(_pnl_ce / ((_pos_ce.get("entry") or 1) * (_pos_ce.get("qty") or 1)), 4),
                                "reason":     f"cycle_check:{_rsn_ca[:120]}",
                            },
                        )
            elif _act_ca == "REVIEW":
                clog("ANALYSIS", f"Cycle check queued PM review: {_sym_ca} — {_rsn_ca}")
                _force_pm_review = True

    # ── Portfolio manager review (event-triggered) ────────────────────────────
    global _portfolio_review_done_today, _last_known_regime, _cascade_reviewed_this_session, _last_trim_ts
    should_review, pm_trigger = _should_run_portfolio_review(
        session=get_session(),
        regime=regime,
        open_positions=open_pos,
        all_scored=pipeline.all_scored,
        news_sentiment=news_sentiment,
        daily_pnl=pnl,
        portfolio_value=pv,
    )
    if not should_review and _force_pm_review:
        should_review = True
        pm_trigger = "cycle_regime_shift"
    if should_review and open_pos:
        clog("ANALYSIS", f"Portfolio review triggered: {pm_trigger}")
        try:
            pm_actions = run_portfolio_review(
                open_positions=open_pos,
                all_scored=pipeline.all_scored,
                regime=regime,
                news_sentiment=news_sentiment,
                portfolio_value=pv,
                trigger=pm_trigger,
            )
            for action in pm_actions:
                sym_pm    = action.get("symbol", "")
                act_pm    = action.get("action", "HOLD")
                reason_pm = action.get("reasoning", "portfolio manager")
                if act_pm == "EXIT" and sym_pm:
                    from orders import open_trades as _pm_trades, _trades_lock as _pm_lock
                    # Dedup: skip if exit already in flight for this symbol
                    with _pm_lock:
                        _already_exiting = (
                            _pm_trades.get(sym_pm, {}).get("status") == "EXITING"
                            or any(v.get("status") == "EXITING"
                                   for k, v in _pm_trades.items()
                                   if v.get("symbol") == sym_pm)
                        )
                    if _already_exiting:
                        clog("INFO", f"Portfolio manager EXIT: {sym_pm} already exiting — skipping duplicate")
                    else:
                        clog("TRADE", f"Portfolio manager EXIT: {sym_pm} — {reason_pm}")
                    pos_pm = next((p for p in open_pos if p["symbol"] == sym_pm), None)
                    ep_pm  = pos_pm["current"] if pos_pm else 0
                    _opt_keys_pm = [k for k in _pm_trades
                                    if k.startswith(sym_pm + "_") and _pm_trades[k].get("instrument") == "option"]
                    if not _already_exiting:
                        if _opt_keys_pm:
                            for _ok in _opt_keys_pm:
                                clog("TRADE", f"PM EXIT routing to option sell: {_ok}")
                                execute_sell_option(ib, _ok, reason=f"portfolio_manager:{pm_trigger}")
                        if sym_pm in _pm_trades:
                            execute_sell(ib, sym_pm, reason=f"portfolio_manager:{pm_trigger}")
                    if not _already_exiting and not _opt_keys_pm and sym_pm not in _pm_trades:
                        clog("WARN", f"PM EXIT: no active position found for {sym_pm} — not in tracker as stock or option")
                    if pos_pm:
                        pnl_pm = (ep_pm - pos_pm["entry"]) * pos_pm["qty"] if pos_pm.get("direction", "LONG") == "LONG" else (pos_pm["entry"] - ep_pm) * pos_pm["qty"]
                        from learning import log_trade as _log_trade_pm
                        _log_trade_pm(
                            trade=pos_pm,
                            agent_outputs={},
                            regime=regime,
                            action="CLOSE",
                            outcome={
                                "exit_price": round(ep_pm, 4),
                                "pnl":        round(pnl_pm, 2),
                                "pnl_pct":    round(pnl_pm / ((pos_pm.get("entry") or 1) * (pos_pm.get("qty") or 1)), 4),
                                "reason":     f"portfolio_manager:{pm_trigger}",
                            },
                        )
                elif act_pm == "TRIM" and sym_pm:
                    from orders import open_trades as _pm_trades, _trades_lock as _pm_lock
                    _trim_cooldown_mins = CONFIG.get("portfolio_manager", {}).get("trim_cooldown_minutes", 30)
                    _last_trim = _last_trim_ts.get(sym_pm)
                    _trim_age = (datetime.now(_ET) - _last_trim).total_seconds() / 60 if _last_trim else None
                    if _trim_age is not None and _trim_age < _trim_cooldown_mins:
                        clog("INFO", f"PM TRIM: {sym_pm} cooldown active ({_trim_age:.0f}m ago, {_trim_cooldown_mins}m required) — skipping")
                        continue
                    _opt_keys_pm = [k for k in _pm_trades
                                    if k.startswith(sym_pm + "_") and _pm_trades[k].get("instrument") == "option"]
                    _has_pos = bool(_opt_keys_pm) or sym_pm in _pm_trades
                    if not _has_pos:
                        clog("WARN", f"PM TRIM: no active position found for {sym_pm}")
                    else:
                        clog("TRADE", f"Portfolio manager TRIM: {sym_pm} — {reason_pm}")
                        _last_trim_ts[sym_pm] = datetime.now(_ET)
                        if _opt_keys_pm:
                            for _ok in _opt_keys_pm:
                                with _pm_lock:
                                    _c = _pm_trades.get(_ok, {}).get("contracts", 0)
                                _trim_c = max(1, _c // 2)
                                execute_sell_option(ib, _ok,
                                                    reason=f"portfolio_manager_trim:{pm_trigger}",
                                                    contracts_override=_trim_c if _trim_c < _c else None)
                        if sym_pm in _pm_trades:
                            with _pm_lock:
                                _q = _pm_trades.get(sym_pm, {}).get("qty", 0)
                            _trim_q = max(1, _q // 2)
                            execute_sell(ib, sym_pm,
                                         reason=f"portfolio_manager_trim:{pm_trigger}",
                                         qty_override=_trim_q if _trim_q < _q else None)
                elif act_pm == "ADD":
                    clog("INFO", f"Portfolio manager ADD: {sym_pm} — {reason_pm} (manual review)")
                else:
                    clog("INFO", f"Portfolio manager HOLD: {sym_pm} — {reason_pm}")
            # Log all actions to audit log
            from bot_state import clog as _clog
            import json as _json
            _audit_path = pathlib.Path("data/audit_log.jsonl")
            try:
                with _audit_path.open("a") as _af:
                    _af.write(_json.dumps({
                        "type":    "portfolio_review",
                        "trigger": pm_trigger,
                        "actions": pm_actions,
                        "ts":      datetime.now(_ET).isoformat(),
                    }) + "\n")
            except Exception:
                pass
        except Exception as _pm_err:
            clog("ERROR", f"Portfolio review error: {_pm_err}")
        finally:
            if pm_trigger == "pre_market":
                _portfolio_review_done_today = True
            if pm_trigger == "cascade":
                _cascade_reviewed_this_session = True
            _last_known_regime = regime.get("regime", _last_known_regime)
    else:
        _last_known_regime = regime.get("regime", _last_known_regime)

    open_pos = get_open_positions()  # refresh after any PM exits
    dash["positions"] = open_pos

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

    now_str    = datetime.now(_ET).strftime("%H:%M:%S")
    agent_convo = []
    agent_names = [
        ("technical",       "Technical Analyst",  "Analyses price action, volume, and all indicator dimensions"),
        ("trading_analyst", "Trading Analyst",    "Macro context, opportunity synthesis, devil's advocate (Opus)"),
        ("risk",            "Risk Manager",        "Sizes positions and flags portfolio-level risk"),
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
        "role":   "Synthesises all agent reports into executable trade instructions",
        "time":   now_str,
        "output": _final_output,
    })
    dash["agent_conversation"] = agent_convo

    _max_votes = len(agent_names) + 1  # 3 agents + final decision
    clog("ANALYSIS", f"Agents agreed: {decision.get('agents_agreed',0)}/{_max_votes} | {decision.get('summary','')}")

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
            "time": datetime.now(_ET).strftime("%H:%M:%S")
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
                    "pnl_pct":    round(pnl_val / ((pos.get("entry") or 1) * (pos.get("qty") or 1)), 4),
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

        clog("INFO", f"Evaluating {sym} | Score={sig['score']}/50 | {reason[:80]}")

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
        buy_signal.direction     = buy.get("direction", "LONG")  # use agent-recommended direction
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
            trade_side = "SHORT" if buy.get("direction") == "SHORT" else "BUY"
            clog("TRADE", f"{trade_side} {sym} | Score={sig['score']}/50 | {reason[:80]}")
            dash["trades"].insert(0, {
                "side": trade_side, "symbol": sym,
                "price": str(sig["price"]),
                "time": datetime.now(_ET).strftime("%H:%M:%S")
            })
            _write_last_decision(sym, buy, sig, decision, pv)

        from orders import is_options_market_open
        if (CONFIG.get("options_enabled") and
                get_session() not in ("PRE_MARKET", "AFTER_HOURS") and
                sig["score"] >= CONFIG.get("options_min_score", 42)):
            if not is_options_market_open():
                clog("INFO", f"Score {sig['score']} qualifies for options — market closed until 9:30 ET. Stock trade executed.")
            else:
                _sig_dir = buy.get("direction", "LONG")  # use agent direction (matches scanner via hard gate)
                if _sig_dir not in ("LONG", "SHORT"):
                    clog("INFO", f"Score {sig['score']} qualifies for options but direction={_sig_dir!r} — skipping (no clear conviction)")
                else:
                    direction = _sig_dir
                    if _options_attempted_today(sym, direction):
                        clog("INFO", f"Options attempt already recorded for {sym} {direction} today — skipping (DAY order terminal)")
                    else:
                        clog("TRADE", f"Score {sig['score']} qualifies for options — evaluating {sym} {direction}")
                        _record_options_attempt(sym, direction)  # mark BEFORE submit (survives crash)
                        try:
                            contract_info = find_best_contract(sym, direction, pv, ib, regime, score=sig["score"])
                            if contract_info:
                                opt_success = execute_buy_option(ib, contract_info, pv, reasoning=reason, score=sig["score"])
                                if opt_success:
                                    dash["trades"].insert(0, {
                                        "side":   f"BUY {contract_info['right']} OPT",
                                        "symbol": f"{sym} ${contract_info['strike']:.0f} {contract_info['expiry_str']}",
                                        "price":  str(contract_info["mid"]),
                                        "time":   datetime.now(_ET).strftime("%H:%M:%S")
                                    })
                                    clog("TRADE", f"Options trade executed for {sym} (independent of stock)")
                                    if not stock_success:
                                        _write_last_decision(sym, buy, sig, decision, pv)
                            else:
                                clog("INFO", f"No suitable options contract for {sym}")
                        except Exception as _opt_err:
                            clog("ERROR", f"Options evaluation failed for {sym}: {_opt_err}")

    # ── FX direct dispatch ────────────────────────────────────────────────────────
    # FX signals bypass the equity intelligence gate — the fx_signals scorer is the
    # complete decision; no agent classification needed.
    if CONFIG.get("fx_enabled") and pipeline:
        try:
            from fx_signals import FX_PAIRS
            _fx_sigs = [s for s in pipeline.signals if s.symbol in FX_PAIRS]
            _fx_min  = CONFIG.get("fx_min_score", 20)
            for _fxs in _fx_sigs:
                _fxs_score = int(round(_fxs.conviction_score * 5))
                if _fxs_score < _fx_min or _fxs.direction not in ("LONG", "SHORT"):
                    continue
                _fxs_reasoning = f"FX-direct {_fxs.symbol}: {_fxs.dimension_scores}"
                if _fxs.direction == "LONG":
                    _fx_ok = execute_buy(
                        ib=ib, symbol=_fxs.symbol, price=_fxs.price, atr=_fxs.atr,
                        score=_fxs_score, portfolio_value=pv, regime=regime,
                        reasoning=_fxs_reasoning, signal_scores=_fxs.dimension_scores,
                        open_time=datetime.now(timezone.utc).isoformat(),
                        instrument="fx",
                    )
                else:
                    _fx_ok = execute_short(
                        ib=ib, symbol=_fxs.symbol, price=_fxs.price, atr=_fxs.atr,
                        score=_fxs_score, portfolio_value=pv, regime=regime,
                        reasoning=_fxs_reasoning, signal_scores=_fxs.dimension_scores,
                        open_time=datetime.now(timezone.utc).isoformat(),
                        instrument="fx",
                    )
                if _fx_ok:
                    clog("TRADE", f"FX {_fxs.direction} {_fxs.symbol} | Score={_fxs_score}/50")
                    dash["trades"].insert(0, {
                        "side": _fxs.direction, "symbol": _fxs.symbol,
                        "price": str(round(_fxs.price, 5)),
                        "time": datetime.now(_ET).strftime("%H:%M:%S"),
                    })
        except Exception as _fxe:
            clog("ERROR", f"FX dispatch: {_fxe}")

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
        "date":  datetime.now(_ET).strftime("%Y-%m-%d %H:%M ET"),
        "value": pv
    })
    if len(dash["equity_history"]) > 2000:
        dash["equity_history"] = dash["equity_history"][-2000:]
    save_equity_history(dash["equity_history"])

    today = datetime.now(_ET).weekday()
    if today == 6 and bot_state.last_sunday_review != datetime.now(_ET).date():
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

        bot_state.last_sunday_review = datetime.now(_ET).date()

    dash["scanning"] = False
    clog("SCAN", f"Scan #{bot_state.scan_count} complete")
