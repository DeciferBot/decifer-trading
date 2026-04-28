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
import zoneinfo
from pathlib import Path
from datetime import UTC, datetime

_ET = zoneinfo.ZoneInfo("America/New_York")

import bot_state
from bot_account import get_account_data, get_fx_snapshot, get_news_headlines, save_equity_history
from bot_ibkr import connect_ibkr, sync_orders_from_ibkr
from bot_state import clog, dash
from bot_voice import speak_natural
from config import CONFIG
from learning import (
    get_effective_capital,
    get_performance_summary,
    load_orders,
    load_trades,
    log_trade,
    run_weekly_review,
)
from options import check_options_exits, find_best_contract
from options_scanner import scan_options_universe
from orders_core import execute_buy, execute_sell, execute_short
from orders_options import (
    _get_open_option_position,
    ask_opus_add_to_option,
    execute_add_to_option,
    execute_buy_option,
    execute_sell_option,
    flush_pending_option_exits,
    update_trailing_stops,
    update_tranche_status,
)
from orders_portfolio import (
    flatten_all,
    get_open_positions,
    update_position_prices,
    update_positions_from_ibkr,
)
from portfolio_manager import lightweight_cycle_check
from risk import (
    calculate_position_size,
    check_risk_conditions,
    check_thesis_validity,
    get_consecutive_losses,
    get_intraday_strategy_mode,
    get_session,
    is_trading_day,
    set_session_opening_regime,
    update_equity_high_water_mark,
)
from risk_gates import auto_rebalance_cash
from scanner import get_dynamic_universe, get_market_regime
from signal_dispatcher import dispatch_signals as _dispatch_signals
from signal_pipeline import run_signal_pipeline
from signal_types import Signal
from signals import fetch_multi_timeframe

log = logging.getLogger("decifer.bot")

# ── EOD options review state ──────────────────────────────────────────────────
_eod_options_review_done: bool = False
_session_state_reset_date: "date | None" = None  # date on which the pre-market state reset last ran

# ── Portfolio manager state ───────────────────────────────────────────────────
_portfolio_review_done_today: bool = False
_last_known_regime: str = ""
_session_stop_count: int = 0
_cascade_reviewed_this_session: bool = False  # prevent cascade from re-firing every loop
_consecutive_zero_scored: int = 0  # circuit breaker: escalates to ERROR after 3 consecutive zero-scored scans
_trimmed_today: set = set()  # symbols already trimmed this session — TRIM fires once only
_pm_reviewed_regime: dict = {}  # symbol → regime label when PM last reviewed it
_last_pm_review_ts: datetime | None = None  # when the last PM review completed (any trigger)
_last_pm_review_ts_by_symbol: dict = {}  # symbol → datetime when that position was last Opus-reviewed
# Edge-trigger dedup: track what value last fired each state-based trigger so that
# persistent conditions (e.g. GLD news never clears) don't re-fire every cooldown cycle.
_last_news_scores: dict = {}  # symbol → keyword_score at last news_hit review
_last_collapse_scores: dict = {}  # symbol → current_score at last score_collapse review
_last_rise_scores: dict = {}  # symbol → current_score at last held_score_rise review (edge dedup)

# ── Last-decision writer (for Chief Decifer trade card) ───────────────────────


def _synthesize_trade_card(
    symbol: str,
    company_name: str,
    opp_text: str,
    dev_text: str,
    tech_text: str,
    price: float,
    sl: float,
    tp: float,
    score: int,
    api_key: str,
) -> dict:
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
        f"Signal score: {score}  |  Entry: ${price:.2f}  |  "
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
        model=CONFIG.get("claude_model_haiku", "claude-haiku-4-5-20251001"),
        max_tokens=350,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()

    result: dict = {}
    for label, key in [("THESIS", "thesis"), ("EDGE", "edge_why_now"), ("RISK", "risk")]:
        m = _re.search(
            rf"{label}:\s*(.+?)(?=(?:THESIS:|EDGE:|RISK:)|\Z)",
            text,
            _re.DOTALL | _re.IGNORECASE,
        )
        if m:
            result[key] = m.group(1).strip()
    return result


def _write_last_decision(symbol: str, buy: dict, sig: dict, decision: dict, portfolio_value: float) -> None:
    """
    Write data/last_decision.json after a successful trade so Chief Decifer
    can display a rich trade card on its home page. Works for LONG and SHORT.
    """
    import os
    import re
    from pathlib import Path

    outputs = decision.get("_agent_outputs", {})
    opp_text = outputs.get("opportunity", "")
    dev_text = outputs.get("devils", "")
    tech_text = outputs.get("technical", "")

    price = sig.get("price", 0)
    qty = buy.get("qty", 1)
    sl = buy.get("sl", 0)
    tp = buy.get("tp", 0)
    score = sig.get("score", 20)
    alloc = round((qty * price / portfolio_value * 100), 1) if portfolio_value > 0 else 0

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
                symbol,
                company_name,
                opp_text,
                dev_text,
                tech_text,
                price,
                sl,
                tp,
                score,
                api_key,
            )
            clog("INFO", f"Claude trade card synthesis complete for {symbol}")
        except Exception as exc:
            clog("WARN", f"Claude synthesis failed for {symbol}, using fallback: {exc}")

    # Fallback extraction if Claude synthesis unavailable or incomplete
    reasoning = buy.get("reasoning", "")

    thesis = synthesis.get("thesis") or (reasoning[:400] if reasoning else f"{symbol} selected by AI agent council")

    edge = synthesis.get("edge_why_now") or ""
    if not edge and reasoning:
        # Last-resort regex: find a sentence with timing language distinct from thesis start
        timing_kws = (
            "catalyst",
            "announ",
            "break",
            "decis",
            "approv",
            "launch",
            "event",
            "earning",
            "FDA",
            "coming",
            "imminent",
            "near-term",
            "upcoming",
            "breakout",
            "momentum",
            "volume spike",
        )
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
            section = dev_text[idx : idx + 800]
            m = re.search(
                r"(?:KEY\s+RISK|RISK[:\s]|MAIN\s+CONCERN)[:\s]+(.+?)(?:\n[0-9A-Z]|\Z)",
                section,
                re.IGNORECASE | re.DOTALL,
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
        rr = round(tp_pct / sl_pct, 1) if sl_pct else 0
        price_targets = {
            "target_pct": tp_pct if direction == "LONG" else -tp_pct,
            "stop_pct": -sl_pct if direction == "LONG" else sl_pct,
            "rr_ratio": rr,
            "target_price": round(tp, 2),
            "stop_price": round(sl, 2),
        }

    payload = {
        "symbol": symbol,
        "company_name": company_name,
        "direction": direction,
        "allocation_pct": alloc,
        "price": round(price, 2),
        "qty": qty,
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
        "score": score,
        "thesis": thesis,
        "edge_why_now": edge,
        "risk": risk,
        "price_targets": price_targets,
        "timestamp": datetime.now(_ET).isoformat(timespec="seconds"),
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


# ── Regime polarity + PM exit reason helpers ──────────────────────────────────


def _polarity(s: str) -> str:
    r = (s or "").upper()
    if r in ("TRENDING_UP", "MOMENTUM_BULL") or "BULL" in r:
        return "BULL"
    if r in ("TRENDING_DOWN", "TRENDING_BEAR", "DISTRIBUTION", "RELIEF_RALLY", "CAPITULATION") or "BEAR" in r:
        return "BEAR"
    return ""


def _build_pm_exit_reason(pos: dict, regime: dict, pm_trigger: str, reason_pm: str, exit_tag: str = "pm_exit") -> str:
    """Build a structured, thesis-level exit reason for PM-initiated exits/trims."""
    entry_regime = pos.get("entry_regime", "UNKNOWN")
    exit_regime = (
        (regime.get("session_character") or regime.get("regime", "UNKNOWN")) if isinstance(regime, dict) else "UNKNOWN"
    )
    trade_type_ex = pos.get("trade_type", "INTRADAY")
    try:
        held_mins = int(
            (datetime.now(UTC) - datetime.fromisoformat(pos["open_time"].replace("Z", "+00:00"))).total_seconds() / 60
        )
    except Exception:
        held_mins = 0
    entry_pol = _polarity(entry_regime)
    exit_pol = _polarity(exit_regime)
    if entry_pol and exit_pol and entry_pol != exit_pol:
        thesis_class = "breached_regime_shift"
    elif trade_type_ex in ("SCALP", "INTRADAY") and held_mins > CONFIG.get("scalp_max_hold_minutes", 90):
        thesis_class = "breached_stale_scalp"
    else:
        thesis_class = "noise_stop"
    full_reason = reason_pm or pm_trigger
    return (
        f"{exit_tag} | {trade_type_ex} | regime:{entry_regime}→{exit_regime}"
        f" | held:{held_mins}min | thesis:{thesis_class} | {pm_trigger}: {full_reason}"
    )


# ── Detect positions closed externally (stop loss / take profit) ──────────────


def check_external_closes(regime: dict):
    """
    Compare bot's open_trades tracker against IBKR actual positions.
    If a position exists in our tracker but not in IBKR, it was closed
    externally.  Log it properly so Trade History tab shows it.
    """
    from orders_contracts import _ibkr_item_to_key, _is_option_contract
    from orders_state import open_trades

    ib = bot_state.ib

    try:
        portfolio_items = ib.portfolio(CONFIG["active_account"])
        ibkr_syms = {_ibkr_item_to_key(item) for item in portfolio_items if item.position != 0}

        realized_pnl_map = {}
        for item in portfolio_items:
            sym = item.contract.symbol
            rpnl = getattr(item, "realizedPNL", None)
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
                        clog(
                            "INFO",
                            f"Removing unfilled order from tracker: {sym} (order #{order_id} no longer active in IBKR)",
                        )
                        del open_trades[sym]
                        continue

                exit_price = None
                is_opt_pos = trade.get("instrument") == "option"
                underlying = trade.get("symbol", sym)
                try:
                    import math as _math

                    fills = ib.fills()
                    is_short = trade.get("direction", "LONG") == "SHORT"
                    # LONG close = sell (SLD); SHORT close = buy (BOT)
                    close_sides = ("BOT", "BUY") if is_short else ("SLD", "SELL")
                    if is_opt_pos:
                        close_fills = [
                            f
                            for f in fills
                            if f.contract.symbol == underlying
                            and f.execution.side.upper() in close_sides
                            and _is_option_contract(f.contract)
                        ]
                    else:
                        close_fills = [
                            f
                            for f in fills
                            if f.contract.symbol == underlying
                            and f.execution.side.upper() in close_sides
                            and not _is_option_contract(f.contract)
                        ]
                    if close_fills:
                        close_fills.sort(key=lambda f: f.execution.time or datetime.min)
                        exit_price = float(close_fills[-1].execution.price)
                        _fill_order_id = getattr(close_fills[-1].execution, "orderId", None)
                    else:
                        _fill_order_id = None
                except Exception:
                    _fill_order_id = None

                rpnl_key = underlying if is_opt_pos else sym
                if exit_price is None and rpnl_key in realized_pnl_map:
                    rpnl = realized_pnl_map[rpnl_key]
                    qty = trade["qty"]
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

                rpnl_lookup = underlying if is_opt_pos else sym
                mult = 100 if is_opt_pos else 1
                manual_pnl = (
                    ((trade["entry"] - exit_price) if is_short else (exit_price - trade["entry"])) * trade["qty"] * mult
                )
                rpnl = realized_pnl_map.get(rpnl_lookup, 0.0)
                pnl = rpnl if (rpnl != 0.0 and not _math.isnan(rpnl)) else manual_pnl

                sl_order_id = trade.get("sl_order_id")
                tp_order_id = trade.get("tp_order_id")
                # ── Determine mechanical exit type ─────────────────────────
                if sl_order_id and _fill_order_id and int(_fill_order_id) == int(sl_order_id):
                    exit_type = "sl_hit"
                elif tp_order_id and _fill_order_id and int(_fill_order_id) == int(tp_order_id):
                    exit_type = "tp_hit"
                elif pnl > 0 and trade.get("tp"):
                    tp = trade.get("tp")
                    hit_tp = (not is_short and exit_price >= tp * 0.99) or (is_short and exit_price <= tp * 1.01)
                    exit_type = "tp_hit" if hit_tp else "manual"
                else:
                    exit_type = "manual"
                # ── Build thesis-level reason (GAP-002) ────────────────────
                entry_regime = trade.get("entry_regime", "UNKNOWN")
                # Prefer session_character in regime dict (set by dispatcher) so the
                # exit label uses the same vocabulary as the entry label.
                exit_regime = (
                    (regime.get("session_character") or regime.get("regime", "UNKNOWN"))
                    if isinstance(regime, dict)
                    else "UNKNOWN"
                )
                trade_type_ex = trade.get("trade_type", "INTRADAY")
                try:
                    held_mins = int(
                        (
                            datetime.now(UTC) - datetime.fromisoformat(trade["open_time"].replace("Z", "+00:00"))
                        ).total_seconds()
                        / 60
                    )
                except Exception:
                    held_mins = 0
                entry_pol = _polarity(entry_regime)
                exit_pol = _polarity(exit_regime)
                if exit_type == "tp_hit":
                    thesis_class = "confirmed"
                elif entry_pol and exit_pol and entry_pol != exit_pol:
                    thesis_class = "breached_regime_shift"
                elif trade_type_ex in ("SCALP", "INTRADAY") and held_mins > CONFIG.get("scalp_max_hold_minutes", 90):
                    thesis_class = "breached_stale_scalp"
                else:
                    thesis_class = "noise_stop"
                exit_reason = (
                    f"{exit_type} | {trade_type_ex} | regime:{entry_regime}→{exit_regime}"
                    f" | held:{held_mins}min | thesis:{thesis_class}"
                )
                clog(
                    "TRADE",
                    f"External close detected: {sym} | Exit ${exit_price:.2f} | P&L ${pnl:+.2f} | {exit_reason}",
                )
                _news_stop = (dash.get("news_data") or {}).get(sym, {})
                speak_natural(
                    "exit_stop",
                    fallback=f"{sym} was closed externally, {'up' if pnl >= 0 else 'down'} {abs(pnl):.0f} dollars.",
                    symbol=sym,
                    exit_type=(exit_type or "closed").replace("_", " "),
                    pnl=f"{pnl:+.0f}",
                    reason=exit_reason[:200] if exit_reason else "",
                    news=_news_stop.get("claude_catalyst") or "none",
                )

                try:
                    log_trade(
                        trade=trade,
                        agent_outputs={},
                        regime=regime,
                        action="CLOSE",
                        outcome={
                            "exit_price": round(exit_price, 2),
                            "pnl": round(pnl, 2),
                            "pnl_pct": round(
                                pnl
                                / (
                                    (trade.get("entry") or 1)
                                    * (trade.get("qty") or 1)
                                    * (100 if trade.get("instrument") == "option" else 1)
                                ),
                                4,
                            ),
                            "reason": exit_reason,
                        },
                    )

                    dash["trades"].insert(
                        0,
                        {
                            "side": "SELL",
                            "symbol": sym,
                            "price": str(round(exit_price, 2)),
                            "time": datetime.now(_ET).strftime("%H:%M:%S"),
                            "pnl": round(pnl, 2),
                        },
                    )

                    from learning import get_performance_summary
                    from learning import load_trades as lt

                    dash["all_trades"] = lt()
                    dash["performance"] = get_performance_summary(lt())

                    dash["positions"] = get_open_positions()

                    if pnl >= 0:
                        from risk import record_win

                        record_win()
                    else:
                        from risk import record_loss

                        record_loss(source="external" if exit_reason != "sl_hit" else "sl")
                        global _session_stop_count
                        _session_stop_count += 1

                except Exception as _log_exc:
                    clog("ERROR", f"External close log/dashboard error for {sym}: {_log_exc}")
                finally:
                    # Always remove from tracker — prevents infinite reprocessing on next cycle
                    open_trades.pop(sym, None)
                    bot_state._sl_fill_events.discard(sym)

    except Exception as e:
        clog("ERROR", f"External close check error: {e}")


def check_options_positions():
    """Monitor open options positions for profit target, stop loss, and DTE exits."""
    from orders_contracts import is_options_market_open
    from orders_state import open_trades

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
            try:
                clog("TRADE", f"Closing options position: {opt_key}")
                sold = execute_sell_option(ib, opt_key, reason="exit_condition")
                if sold:
                    dash["positions"] = get_open_positions()
                else:
                    from orders_options import (
                        _MAX_OPTION_SELL_RETRIES,
                        _OPTION_SELL_COOLDOWN,
                        _option_sell_attempts,
                        _pending_option_exits,
                    )

                    if opt_key in _pending_option_exits:
                        clog("INFO", f"Options market closed — {opt_key} queued for next open")
                    else:
                        _att = _option_sell_attempts.get(opt_key, {})
                        _cnt = _att.get("count", 0)
                        if _cnt >= _MAX_OPTION_SELL_RETRIES:
                            _elapsed = (
                                datetime.now(UTC) - _att.get("last_try", datetime.min.replace(tzinfo=UTC))
                            ).total_seconds()
                            _remaining = max(0, int(_OPTION_SELL_COOLDOWN - _elapsed))
                            clog(
                                "WARN", f"Option sell cooling down for {opt_key} — {_cnt} failures, {_remaining}s remaining"
                            )
                        else:
                            clog(
                                "WARN",
                                f"Option sell failed for {opt_key} — will retry next cycle (attempt {_cnt}/{_MAX_OPTION_SELL_RETRIES})",
                            )
            except Exception as _opt_exc:
                clog("ERROR", f"Options exit error for {opt_key}: {_opt_exc}")
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
            from orders_portfolio import close_position

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
    import anthropic as _anthropic
    from orders_portfolio import close_position, get_open_positions

    def _call_claude(system_prompt: str, user_message: str) -> str:
        try:
            _cl = _anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
            resp = _cl.messages.create(
                model=CONFIG["claude_model"],
                max_tokens=CONFIG["claude_max_tokens"],
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_message}],
            )
            return resp.content[0].text.strip()
        except Exception as _e:
            log.error(f"Claude API error in EOD options review: {_e}")
            return ""

    ib = bot_state.ib
    positions = get_open_positions()
    opts = [p for p in positions if p.get("instrument") == "option"]

    if not opts:
        clog("INFO", "EOD options review: no open options positions")
        return

    # INTRADAY positions are always closed at EOD — no AI judgment needed.
    intraday_opts = [p for p in opts if p.get("trade_type") == "INTRADAY"]
    claude_opts   = [p for p in opts if p.get("trade_type") != "INTRADAY"]

    for p in intraday_opts:
        key = p.get("_trade_key", p.get("symbol"))
        sym = key.split("_")[0]
        clog("TRADE", f"EOD INTRADAY force-close: {key}")
        try:
            result = close_position(ib, key) or close_position(ib, sym)
            if result:
                from bot_voice import speak_natural as _speak_eod
                _speak_eod("exit_pm", fallback=f"Closing {sym} into the bell.", symbol=sym,
                           reason="intraday flat EOD", news="none")
            else:
                clog("ERROR", f"EOD INTRADAY close failed for {key}")
        except Exception as e:
            clog("ERROR", f"EOD INTRADAY close error for {key}: {e}")

    if not claude_opts:
        clog("INFO", f"EOD options review: {len(intraday_opts)} INTRADAY closed, no SWING/POSITION to review")
        return

    clog("ANALYSIS", f"EOD options review: {len(intraday_opts)} INTRADAY closed, evaluating {len(claude_opts)} SWING/POSITION position(s)")

    # Build a readable context block for each position
    pos_lines = []
    for p in claude_opts:
        entry_prem = p.get("entry_premium", 0)
        curr_prem = p.get("current_premium", entry_prem)
        pnl_pct = ((curr_prem - entry_prem) / entry_prem * 100) if entry_prem else 0
        key = p.get("_trade_key", p.get("symbol"))
        pos_lines.append(
            f"- Key: {key} | {p.get('right', '?')} ${p.get('strike', '?')} exp {p.get('expiry_str', '?')} "
            f"| DTE: {p.get('dte', '?')} | P&L: {pnl_pct:+.1f}% "
            f"| Delta: {p.get('delta', '?')} | Theta/day: {p.get('theta', '?')} "
            f"| IV: {p.get('iv', '?')} | Entry thesis: {str(p.get('reasoning', ''))[:120]}"
        )

    regime_str = (
        f"Regime: {regime.get('regime', 'unknown')} | VIX: {regime.get('vix', '?')} | Trend: {regime.get('trend', '?')}"
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
        f"Open options positions:\n" + "\n".join(pos_lines) + "\n\nReturn your JSON array decision now."
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
        key = item.get("key", "")
        decision = item.get("decision", "HOLD").upper()
        reason = item.get("reason", "")
        clog("ANALYSIS", f"EOD [{decision}] {key} — {reason}")
        if decision == "CLOSE":
            try:
                sym = key.split("_")[0]  # underlying symbol for close_position lookup
                result = close_position(ib, key) or close_position(ib, sym)
                if result:
                    clog("TRADE", f"EOD closed: {key} — {result}")
                    from bot_voice import speak_natural as _speak_eod
                    _speak_eod(
                        "exit_pm",
                        fallback=f"Closing {sym} into the bell.",
                        symbol=sym,
                        reason=reason or "end of day review",
                        news="none",
                    )
                else:
                    clog("ERROR", f"EOD close failed for {key}")
            except Exception as e:
                clog("ERROR", f"EOD close error for {key}: {e}")


def _maybe_eod_options_review(regime: dict):
    """Fire _eod_options_review once per day between 3:30 PM and 3:55 PM ET."""
    global _eod_options_review_done, _session_state_reset_date
    import zoneinfo as _zi

    _ET = _zi.ZoneInfo("America/New_York")
    from datetime import time as dtime

    now_et = datetime.now(_ET)
    t = now_et.time()
    today = now_et.date()
    # Reset once per calendar day before the session opens — not on every cycle.
    # Previously this block ran on every scan cycle while t < 9:30, which reset
    # _portfolio_review_done_today and _last_pm_review_ts each cycle and caused
    # the pre-market PM review to re-fire every ~18 minutes throughout pre-market.
    if t < dtime(9, 30) and _session_state_reset_date != today:
        _session_state_reset_date = today
        _eod_options_review_done = False
        global \
            _portfolio_review_done_today, \
            _session_stop_count, \
            _cascade_reviewed_this_session, \
            _trimmed_today, \
            _pm_reviewed_regime, \
            _last_pm_review_ts, \
            _last_news_scores, \
            _last_collapse_scores, \
            _last_rise_scores, \
            _last_pm_review_ts_by_symbol
        _portfolio_review_done_today = False
        _session_stop_count = 0
        _cascade_reviewed_this_session = False
        _trimmed_today = set()
        _pm_reviewed_regime = {}
        _last_pm_review_ts = None
        _last_news_scores = {}
        _last_collapse_scores = {}
        _last_rise_scores = {}
        _last_pm_review_ts_by_symbol = {}
    # Fire once in the pre-close window
    if dtime(15, 30) <= t < dtime(15, 55) and not _eod_options_review_done:
        _eod_options_review_done = True
        _eod_options_review(regime)


# ── Overnight research trigger ───────────────────────────────────────────────

_overnight_research_done: bool = False


def _maybe_generate_overnight_research():
    """
    Fire once per day at 6:00–9:00 AM ET (pre-market window).
    At 6 AM real pre-market prices, overnight news, and catalyst data are all available.
    Generates data/overnight_notes.md — read by Opus before the 9:30 AM open.
    Runs in a background thread so it doesn't block the scan loop.
    """
    global _overnight_research_done
    import threading as _th
    import zoneinfo as _zi

    _ET = _zi.ZoneInfo("America/New_York")
    from datetime import time as dtime

    t = datetime.now(_ET).time()

    # Reset after 9 PM so next morning's 6 AM run fires cleanly
    if t >= dtime(21, 0):
        _overnight_research_done = False

    if dtime(6, 0) <= t < dtime(9, 0) and not _overnight_research_done:
        _overnight_research_done = True

        # RB-8: sentinel path — written by _run() on success, checked before
        # notes injection at scan time so stale data is never silently injected.
        import os as _os
        _overnight_sentinel = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "data", "overnight_notes.done"
        )
        # Clear any previous cycle's sentinel so the sentinel reflects this run.
        try:
            _os.remove(_overnight_sentinel)
        except OSError:
            pass

        def _run():
            try:
                from overnight_research import generate_overnight_notes
            except Exception as exc:
                clog("WARNING", f"Overnight research failed (import): {exc}")
                return

            try:
                from orders_portfolio import get_open_positions
                universe = [p["symbol"] for p in get_open_positions()]
            except Exception as exc:
                clog("WARNING", f"Overnight research: could not fetch positions (TWS down?), running without universe — {exc}")
                universe = []

            try:
                generate_overnight_notes(universe=universe or None)
                clog("INFO", "Overnight research notes generated → data/overnight_notes.md")
                # RB-8: write sentinel so the first scan knows notes are fresh.
                # Without this, a slow FMP API call causes the 9:30 scan to silently
                # inject yesterday's notes if the thread hasn't completed yet.
                import os as _os2
                try:
                    with open(_overnight_sentinel, "w") as _sf:
                        _sf.write(datetime.now(_ET).isoformat())
                except Exception as _se:
                    clog("WARNING", f"Overnight research: sentinel write failed: {_se}")
            except Exception as exc:
                clog("WARNING", f"Overnight research failed: {exc}")

        _th.Thread(target=_run, name="overnight-research", daemon=True).start()


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
      scalp_signal_lost — held SCALP: direction flipped against entry OR momentum collapsed
      news_hit      — keyword_score on a held symbol changed materially since last review
      earnings_risk — any held symbol has earnings within 48 hours
      cascade       — 2+ stop losses hit this session
      drawdown      — daily PnL / portfolio < -1.5%

    news_hit and scalp_signal_lost are EDGE triggers: they only fire when the
    underlying value has changed materially since the last review, not simply
    because the condition persists. This prevents a single persistent news event
    (e.g. tariff headlines keeping GLD keyword_score elevated) from re-triggering
    the PM on every scan cycle.
    """
    global _portfolio_review_done_today, _last_known_regime, _last_pm_review_ts
    global _last_news_scores, _last_collapse_scores, _last_rise_scores, _last_pm_review_ts_by_symbol
    pm_cfg = CONFIG.get("portfolio_manager", {})
    if not pm_cfg.get("enabled", True):
        return False, ""

    # 1. Pre-market: once per day
    if session == "PRE_MARKET" and not _portfolio_review_done_today:
        return True, "pre_market"

    # Global review cooldown — all non-pre_market triggers are suppressed if a
    # review ran recently.  This prevents persistent conditions (earnings within 48h,
    # sustained drawdown) from firing the PM — and executing another TRIM — on
    # every scan cycle.  Uses the same trim_cooldown_minutes setting so the two
    # guards are always in sync.
    _review_cooldown = pm_cfg.get("review_cooldown_minutes", pm_cfg.get("trim_cooldown_minutes", 30))
    if _last_pm_review_ts is not None:
        _review_age_mins = (datetime.now(_ET) - _last_pm_review_ts).total_seconds() / 60
        if _review_age_mins < _review_cooldown:
            return False, ""

    # RB-1: Collect ALL active triggers rather than returning on the first.
    # Previously, a single early-return meant Opus received one trigger reason string
    # and was blind to every other condition that also fired. If regime changed AND
    # a news hit AND cascade all fired simultaneously, Opus saw only "regime_change".
    # On the next cycle a different trigger was still active — Opus got called again
    # with incomplete context each time. Accumulate all triggers and pass them all.
    _active_triggers: list[str] = []

    # 2. Regime change — edge trigger: fires only when regime label changes
    current_regime = regime.get("regime", "")
    if _last_known_regime and current_regime and current_regime != _last_known_regime:
        _active_triggers.append("regime_change")

    if not open_positions:
        return bool(_active_triggers), "; ".join(_active_triggers)

    # 3. SCALP signal lost — direction flipped against entry OR momentum
    #    (the primary SCALP driver) collapsed from load-bearing at entry to zero.
    #    Score level alone is not a trigger — entry score is an entry filter, not
    #    an exit driver.  Absence of signal (no news, overnight_drift fading) is
    #    silence, not thesis change.  Only fire for SCALP positions.
    scalp_mom_entry_min = pm_cfg.get("scalp_momentum_entry_min", 4)
    scalp_mom_current_max = pm_cfg.get("scalp_momentum_current_max", 1)
    scored_map = {s["symbol"]: s.get("score", 0) for s in all_scored}
    _dir_map = {s["symbol"]: s.get("direction", "NEUTRAL") for s in all_scored if s.get("symbol")}
    _mom_map = {
        s["symbol"]: float((s.get("score_breakdown") or {}).get("momentum", 0) or 0)
        for s in all_scored if s.get("symbol")
    }
    _scalp_lost_syms = []
    for pos in open_positions:
        if pos.get("trade_type", "INTRADAY") not in ("SCALP", "INTRADAY"):
            continue
        sym = pos.get("symbol", "")
        entry_dir = pos.get("direction", "LONG")
        current_dir = _dir_map.get(sym, "NEUTRAL")
        entry_mom = float((pos.get("signal_scores") or {}).get("momentum", 0) or 0)
        current_mom = _mom_map.get(sym, 0)
        dir_flipped = (
            (entry_dir == "LONG" and current_dir == "SHORT")
            or (entry_dir == "SHORT" and current_dir == "LONG")
        )
        mom_lost = entry_mom >= scalp_mom_entry_min and current_mom <= scalp_mom_current_max
        if dir_flipped or mom_lost:
            _scalp_lost_syms.append(sym)
    if _scalp_lost_syms:
        _active_triggers.append(f"scalp_signal_lost:{','.join(_scalp_lost_syms)}")

    # 3b. Held-score rise — edge trigger: fires when a held position's score has
    #     risen materially since entry AND reached a minimum conviction threshold.
    #     Symmetric to score_collapse, but for the UP side. Lets PM consider ADD
    #     when conviction strengthens on an existing winner. Only re-fires if
    #     score continues to rise further than the last review's snapshot.
    #     Addresses 2026-04-14 "AMZN 28→65 scored but never ADDed" gap — there
    #     was no trigger for upward moves, so PM simply never woke up on winners.
    rise_delta = CONFIG.get("add_trigger_score_delta", 15)
    rise_redfire = CONFIG.get("add_trigger_redfire_delta", 5)
    rise_min_score = CONFIG.get("add_trigger_min_score", 45)
    _rise_syms = []
    for pos in open_positions:
        sym = pos.get("symbol", "")
        entry_sc = pos.get("entry_score", pos.get("score", 0)) or 0
        current_sc = scored_map.get(sym)
        if current_sc is None:
            continue
        if current_sc < rise_min_score:
            continue
        if (current_sc - entry_sc) < rise_delta:
            continue
        last_sc = _last_rise_scores.get(sym)
        if last_sc is None or (current_sc - last_sc) >= rise_redfire:
            _rise_syms.append(sym)
    if _rise_syms:
        _active_triggers.append(f"held_score_rise:{','.join(_rise_syms)}")

    # 4. News hit — edge trigger: fires only when keyword_score has changed
    #    materially since the last review for that symbol.
    #    Prevents persistent headlines (e.g. GLD tariff news) from re-triggering
    #    the PM on every scan cycle.
    news_thresh = pm_cfg.get("news_hit_threshold", 3)
    news_redfire_delta = pm_cfg.get("news_hit_redfire_delta", 2)
    _news_hit_syms = []
    for pos in open_positions:
        sym = pos.get("symbol", "")
        kw = news_sentiment.get(sym, {}).get("keyword_score", 0)
        if abs(kw) < news_thresh:
            continue
        last_kw = _last_news_scores.get(sym)
        if last_kw is None or abs(kw - last_kw) >= news_redfire_delta:
            _news_hit_syms.append(sym)
    if _news_hit_syms:
        _active_triggers.append(f"news_hit:{','.join(_news_hit_syms)}")

    # 5. Earnings within 48 hours
    try:
        from earnings_calendar import get_earnings_within_hours as _gew

        held_syms = [p["symbol"] for p in open_positions]
        lookahead = pm_cfg.get("earnings_lookahead_hours", 48)
        if _gew(held_syms, lookahead):
            _active_triggers.append("earnings_risk")
    except Exception:
        pass

    # 6. Cascade: 2+ stops this session — fire once per session only
    cascade_thresh = pm_cfg.get("cascade_stop_count", 2)
    if _session_stop_count >= cascade_thresh and not _cascade_reviewed_this_session:
        _active_triggers.append("cascade")

    # 7. Daily drawdown threshold
    drawdown_thresh = pm_cfg.get("drawdown_trigger_pct", -0.015)
    if portfolio_value > 0 and (daily_pnl / portfolio_value) < drawdown_thresh:
        _active_triggers.append("drawdown")

    # 8. Stale position — any open position held > N hours without an Opus review
    stale_hours = pm_cfg.get("stale_position_review_hours", 3)
    stale_secs = stale_hours * 3600
    now_utc = datetime.now(UTC)
    _stale_syms = []
    for pos in open_positions:
        sym = pos.get("symbol", "")
        open_time_str = pos.get("open_time", "")
        if not open_time_str:
            continue
        try:
            open_dt = datetime.fromisoformat(open_time_str).replace(tzinfo=UTC)
            secs_held = (now_utc - open_dt).total_seconds()
        except Exception:
            continue
        if secs_held < stale_secs:
            continue
        last_reviewed = _last_pm_review_ts_by_symbol.get(sym)
        since_last = (now_utc - last_reviewed).total_seconds() if last_reviewed else secs_held
        if since_last >= stale_secs:
            _stale_syms.append(sym)
    if _stale_syms:
        _active_triggers.append(f"stale_position:{','.join(_stale_syms)}")

    return bool(_active_triggers), "; ".join(_active_triggers)


# ── Scan helpers ──────────────────────────────────────────────────────────────


def _print_score_table(scored: list, n: int = 10) -> None:
    """Print a ranked score table to terminal after each scan."""
    if not scored:
        return

    # ── DAR distribution across full universe (diagnostic) ────────
    dar_vals = [s.get("dar", 1.0) for s in scored]
    perfect = sum(1 for d in dar_vals if d >= 0.999)
    partial = sum(1 for d in dar_vals if 0.5 <= d < 0.999)
    low = sum(1 for d in dar_vals if d < 0.5)
    dar_min = min(dar_vals)
    dar_avg = sum(dar_vals) / len(dar_vals)
    clog(
        "DAR",
        f"Universe={len(scored)}  perfect(1.0)={perfect}  partial(.5-.99)={partial}"
        f"  low(<.5)={low}  min={dar_min:.2f}  avg={dar_avg:.2f}",
    )

    top = sorted(scored, key=lambda s: s.get("score", 0), reverse=True)[:n]
    clog("SCAN", f"── Top {len(top)} Signals {'─' * 40}")
    for i, s in enumerate(top, 1):
        sym = s.get("symbol", "?")
        direction = s.get("direction", "?")
        score = s.get("score", 0)
        breakdown = s.get("score_breakdown") or {}
        top_dims = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
        top_dims = [(k, v) for k, v in top_dims if v > 0][:3]
        dims_str = "  ".join(f"{k}:{v}" for k, v in top_dims) if top_dims else "—"
        dir_short = {"LONG": "L", "SHORT": "S"}.get(direction, direction)
        dar_val = s.get("dar", 1.0)
        clog("SIGNAL", f"#{i:<2} {sym:<8} {dir_short:<5} {score:>3}  DAR={dar_val:.2f}  │ {dims_str}")


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
    dash["scan_count"] = bot_state.scan_count
    dash["last_scan"] = datetime.now(_ET).strftime("%H:%M:%S")
    dash["scanning"] = True
    dash["session"] = get_session()

    # Housekeeping: evict stale recently_closed entries every scan to prevent unbounded growth
    try:
        from orders_state import cleanup_recently_closed

        cleanup_recently_closed()
    except Exception:
        pass

    dash["recent_orders"] = []
    dash["trades"] = []
    dash["_scan_start"] = datetime.now(_ET).isoformat()

    clog("SCAN", f"Scan #{bot_state.scan_count} started | Session: {dash['session']}")

    if not ib.isConnected():
        clog("ERROR", "IBKR disconnected — attempting reconnect...")
        if not connect_ibkr():
            clog("ERROR", "Reconnect failed — skipping scan")
            dash["scanning"] = False
            return

    pv, pnl = get_account_data()
    dash["portfolio_value"] = pv
    dash["daily_pnl"] = pnl

    if pv > 0:
        newly_halted = update_equity_high_water_mark(pv)
        if newly_halted:
            clog("RISK", "⛔ DRAWDOWN BRAKE: drawdown limit exceeded — flattening all positions")
            speak_natural("drawdown", fallback="I've hit the drawdown limit. Flattening all positions now.")
            flatten_all(ib)
            dash["scanning"] = False
            return

    # ── Interphase safety overlay: LLM-independent circuit breakers ──────────
    # Runs before any trade decision. Does not replace risk.check_risk_conditions()
    # or the drawdown brake above — it layers on top, so that even if the Apex/
    # agents path breaks we still block new entries on deeper daily loss.
    try:
        import safety_overlay as _so
        _breaker_ok, _breaker_reason, _breaker_mode = _so.run_circuit_breakers(pv, pnl)
        if _breaker_mode == "manage_only":
            clog("RISK", f"⚠️ SAFETY OVERLAY manage-only: {_breaker_reason}")
            dash["safety_mode"] = "manage_only"
        else:
            dash["safety_mode"] = "ok"
        # Preflight broker reconciliation — IBKR is ground truth for qty/fills.
        _recon = _so.preflight_reconcile(ib)
        if _recon.get("mismatches"):
            clog("RISK", f"🔄 Preflight reconcile: {_recon['mismatches']} mismatch(es) repaired")
    except Exception as _so_err:
        log.error("safety_overlay hook failed: %s", _so_err)

    clog("INFO", f"Portfolio: ${pv:,.2f} | DayP&L: ${pnl:+,.2f} | Positions: {len(get_open_positions())}")

    update_positions_from_ibkr(ib)
    update_tranche_status(ib)
    update_trailing_stops(ib)
    flush_pending_option_exits(ib)
    dash["positions"] = get_open_positions()

    if not is_trading_day():
        clog("INFO", "Not a trading day — pipeline sleeping. Sentinel monitoring news.")
        return

    if get_session() == "CLOSED":
        clog("INFO", "Market closed — pipeline sleeping. Sentinel monitoring news.")
        return

    check_options_positions()

    clog("INFO", "Detecting market regime...")
    regime = get_market_regime(ib)
    _vix_val = regime.get("vix") or 0
    _rr_threshold = CONFIG.get("regime_router_vix_threshold", 20)
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
    clog(
        "INFO", f"Regime: {regime['regime']} | VIX: {_vix_val} | SPY: ${regime['spy_price']} | Router: {_router_state}"
    )
    set_session_opening_regime(regime["regime"])
    _maybe_eod_options_review(regime)
    _maybe_generate_overnight_research()

    check_external_closes(regime)

    tradeable, reason = check_risk_conditions(pv, pnl, regime, get_open_positions(), ib=ib)
    if not tradeable:
        if "Cash reserve too low" in reason:
            clog("RISK", "Cash reserve below minimum — auto-rebalancing to free up cash")
            auto_rebalance_cash(ib, pv, regime)
            pv, pnl = get_account_data()
            dash["portfolio_value"] = pv
            dash["daily_pnl"] = pnl
            dash["positions"] = get_open_positions()
            tradeable, reason = check_risk_conditions(pv, pnl, regime, get_open_positions(), ib=ib)

        if not tradeable:
            clog("RISK", f"Trading suspended: {reason}")
            dash["claude_analysis"] = f"Trading suspended: {reason}"
            dash["scanning"] = False
            return

    strategy_mode = get_intraday_strategy_mode(pv, pnl, regime["regime"])
    if strategy_mode["mode"] != "NORMAL":
        clog(
            "RISK",
            f"Strategy mode: {strategy_mode['mode']} | "
            f"PnL={strategy_mode['daily_pnl_pct'] * 100:+.1f}% | "
            f"Streak={get_consecutive_losses()} | "
            f"ScoreAdj=+{strategy_mode['score_threshold_adj']} | "
            f"SizeMult={strategy_mode['size_multiplier']}x",
        )
    if strategy_mode["regime_changed"]:
        clog("RISK", "Regime changed since session open — thesis check active for open positions")
        speak_natural(
            "regime",
            fallback="Heads up, the market regime has shifted.",
            regime=regime.get("regime", "unknown"),
            vix=regime.get("vix", "?"),
        )

    clog("SCAN", "Building dynamic universe (Alpaca screening)...")
    universe = get_dynamic_universe(ib, regime)
    # Sector bias is cached inside get_dynamic_universe — fetch the cached result for the dashboard.
    try:
        from scanner import get_sector_rotation_bias as _get_sbias

        dash["sector_bias"] = _get_sbias()
    except Exception:
        pass

    # Capture per-tier universe composition BEFORE merges for coverage telemetry.
    # Used below at the pipeline-summary log and written to universe_coverage.jsonl.
    # Tier A = CORE_SYMBOLS (macro/ETF) + CORE_EQUITIES (mega-cap equities).
    # Tier B = promoted top-50 from data/daily_promoted.json.
    # Rest = sector-rotation leaders + constituents (dynamic Tier C adds happen below).
    try:
        from scanner import CORE_EQUITIES as _CORE_EQ
        from scanner import CORE_SYMBOLS as _CORE_SYM
        from universe_promoter import load_promoted_universe as _load_promoted

        _universe_pre_merge = set(universe)
        _cov_core = len(set(_CORE_SYM) & _universe_pre_merge)
        _cov_equities = len(set(_CORE_EQ) & _universe_pre_merge)
        _promoted_set = set(_load_promoted())
        _cov_promoted = len(_promoted_set & _universe_pre_merge)
        _tierA = set(_CORE_SYM) | set(_CORE_EQ)
        _cov_other = max(0, len(_universe_pre_merge) - len(_tierA & _universe_pre_merge) - _cov_promoted)
    except Exception:
        _cov_core = _cov_equities = _cov_promoted = _cov_other = -1

    favs = dash.get("favourites", [])
    if favs:
        before = len(universe)
        universe = list(set(universe + favs))
        new_count = len(universe) - before
        clog("INFO", f"Favourites: {len(favs)} tickers ({new_count} new additions to universe)")
    clog("INFO", f"Universe: {len(universe)} symbols to score")

    # Pull open positions BEFORE the pipeline so held symbols are always scored.
    # This prevents the portfolio manager from seeing "not_in_universe" on reboot
    # simply because the promoter didn't surface a still-valid position today.
    open_pos = get_open_positions()
    held_syms = [p["symbol"] for p in open_pos if p.get("instrument") != "option"]
    if held_syms:
        universe = list(set(universe + held_syms))
        new_held = [s for s in held_syms if s not in favs]
        if new_held:
            clog("INFO", f"Held positions pinned into pipeline universe: {new_held}")
    # Merge held symbols with favourites for downstream protected-set consumers.
    pipeline_favs = list(set(favs + held_syms))

    _cov_favs = len(favs)
    _cov_held = len(held_syms)

    # Refresh Alpaca stream subscriptions to match the finalised universe.
    # update_symbols() is a no-op if the symbol list hasn't changed.
    try:
        import bot_state as _bs

        if _bs._bar_stream is not None:
            _bs._bar_stream.update_symbols(universe)
    except Exception:
        pass

    # Pre-warm FMP fundamentals cache for the full universe so that
    # trade_context.py finds revenue growth and EPS data without blocking.
    try:
        from fmp_client import warm_fundamentals_cache as _fmp_warm
        _fmp_warm(universe)
    except Exception:
        pass

    clog("SCAN", "Running signal pipeline (sympathy → sentiment → 9-dim score)...")
    pipeline = run_signal_pipeline(
        universe=universe,
        regime=regime,
        strategy_mode=strategy_mode,
        session=get_session(),
        favourites=pipeline_favs,
        ib=ib,
    )
    signals = pipeline.signals
    scored = pipeline.scored
    news_sentiment = pipeline.news_sentiment
    universe = pipeline.universe
    regime_name = pipeline.regime_name

    dash["news_data"] = news_sentiment

    # BACK-007 — update directional skew display each scan
    try:
        from learning import get_directional_skew_multi

        dash["skew"] = get_directional_skew_multi()
    except Exception as _skew_err:
        log.debug(f"Skew update skipped: {_skew_err}")

    clog(
        "SCAN",
        f"Pipeline: core={_cov_core} equities={_cov_equities} promoted={_cov_promoted} "
        f"other={_cov_other} favs={_cov_favs} held={_cov_held} → universe={len(universe)} "
        f"→ scored={len(scored)} → signals={len(signals)} [{regime_name}]",
    )
    # Coverage audit log — per-cycle layer breakdown for universe health monitoring.
    # If `core + equities` drops below ~45 on any cycle, the floor is broken.
    try:
        import json as _json
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        _cov_path = "data/universe_coverage.jsonl"
        _cov_record = {
            "ts": _dt.now(_UTC).isoformat(),
            "regime": regime_name,
            "core": _cov_core,
            "equities": _cov_equities,
            "promoted": _cov_promoted,
            "other": _cov_other,
            "favs": _cov_favs,
            "held": _cov_held,
            "universe": len(universe),
            "scored": len(scored),
            "signals": len(signals),
        }
        with open(_cov_path, "a") as _cov_f:
            _cov_f.write(_json.dumps(_cov_record) + "\n")
    except Exception as _cov_err:
        log.debug(f"Universe coverage log skipped: {_cov_err}")

    # Scoring gap circuit breaker: if signal pipeline produces zero scored symbols
    # for 3+ consecutive scans the scoring engine has silently failed — escalate.
    global _consecutive_zero_scored
    if len(scored) == 0:
        _consecutive_zero_scored += 1
        if _consecutive_zero_scored >= 3:
            log.error(
                "SCORING_GAP: scored=0 for %d consecutive scan(s) — signal pipeline "
                "may be silently failing. universe=%d regime=%s",
                _consecutive_zero_scored, len(universe), regime_name,
            )
            try:
                from learning import _append_audit_event
                _append_audit_event(
                    "SCORING_GAP",
                    consecutive_zero_scans=_consecutive_zero_scored,
                    universe_size=len(universe),
                    regime=regime_name,
                )
            except Exception:
                pass
    else:
        _consecutive_zero_scored = 0

    _print_score_table(scored)

    # CP-1: Options scan runs before update_position_prices so both use the same
    # live-price moment. Previously options scan ran ~30s after position marks were
    # frozen from the pipeline snapshot, creating a divergent price universe between
    # options analysis and PM ADD/TRIM notional calculations.
    options_signals = []
    if CONFIG.get("options_enabled") and get_session() not in ("PRE_MARKET", "AFTER_HOURS"):
        try:
            clog("ANALYSIS", "Scanning options flow (unusual vol, IV rank, earnings)...")
            top_scored_syms = [s["symbol"] for s in scored[:20]]
            favs_for_opts = dash.get("favourites", [])
            extra = list(set(top_scored_syms + favs_for_opts))
            options_signals = scan_options_universe(extra_symbols=extra, regime=regime)
            clog("ANALYSIS", f"Options scan: {len(options_signals)} notable setups found")
            from learning import _append_audit_event
            _append_audit_event(
                "options_scan",
                symbols_scanned=len(extra),
                signals_found=len(options_signals),
                regime=regime.get("regime", "UNKNOWN"),
            )
        except Exception as _opts_err:
            clog("ERROR", f"Options scanner error: {_opts_err}")
            from learning import _append_audit_event
            _append_audit_event("options_scan_error", error=str(_opts_err))

    update_position_prices(pipeline.scored)

    if _check_kill():
        return
    _process_close_queue()

    news = get_news_headlines()
    fx = get_fx_snapshot()

    if _check_kill():
        return
    _process_close_queue()

    clog("ANALYSIS", "Running agent analysis pipeline...")
    # open_pos already fetched above (before pipeline) so held positions were included in scoring
    open_pos = get_open_positions()  # refresh after any close-queue processing

    # ── Lightweight per-cycle position check (every scan, no LLM) ───────────
    global _pm_reviewed_regime
    _force_pm_review = False
    _cc_review_reasons: list[str] = []  # CP-2: accumulate all REVIEW reasons for PM context
    if open_pos:
        cycle_actions = lightweight_cycle_check(open_pos, regime, pipeline.all_scored)
        for _ca in cycle_actions:
            _sym_ca = _ca.get("symbol", "")
            _act_ca = _ca.get("action", "")
            _rsn_ca = _ca.get("reasoning", "")
            if _act_ca == "EXIT" and _sym_ca:
                from orders_state import _trades_lock as _ce_lock
                from orders_state import open_trades as _ce_trades

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
                                "pnl": round(_pnl_ce, 2),
                                "pnl_pct": round(
                                    _pnl_ce
                                    / (
                                        (_pos_ce.get("entry") or 1)
                                        * (_pos_ce.get("qty") or 1)
                                        * (100 if _pos_ce.get("instrument") == "option" else 1)
                                    ),
                                    4,
                                ),
                                "reason": f"cycle_check:{_rsn_ca[:120]}",
                            },
                        )
            elif _act_ca == "REVIEW":
                _cur_regime_cc = regime.get("session_character") or regime.get("regime", "UNKNOWN")
                if _pm_reviewed_regime.get(_sym_ca) == _cur_regime_cc:
                    clog(
                        "INFO",
                        f"Cycle check REVIEW suppressed for {_sym_ca}: already reviewed under regime {_cur_regime_cc}",
                    )
                else:
                    clog("ANALYSIS", f"Cycle check queued PM review: {_sym_ca} — {_rsn_ca}")
                    _force_pm_review = True
                    _cc_review_reasons.append(f"{_sym_ca}: {_rsn_ca}")  # CP-2

    # ── Portfolio manager review (event-triggered) ────────────────────────────
    global \
        _portfolio_review_done_today, \
        _last_known_regime, \
        _cascade_reviewed_this_session, \
        _trimmed_today, \
        _last_pm_review_ts, \
        _last_news_scores, \
        _last_collapse_scores, \
        _last_rise_scores, \
        _last_pm_review_ts_by_symbol
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
        # Respect the same cooldown as event-triggered reviews so that a
        # persistent regime-shift condition doesn't fire Opus every cycle.
        _force_cooldown_mins = CONFIG.get("portfolio_manager", {}).get("trim_cooldown_minutes", 30)
        _force_age_mins = (
            (datetime.now(_ET) - _last_pm_review_ts).total_seconds() / 60 if _last_pm_review_ts is not None else None
        )
        if _force_age_mins is None or _force_age_mins >= _force_cooldown_mins:
            should_review = True
            # CP-2: use the actual accumulated reasons so Opus sees full context,
            # not just a generic label. Falls back to "cycle_regime_shift" if somehow empty.
            pm_trigger = ("cycle_check: " + "; ".join(_cc_review_reasons)) if _cc_review_reasons else "cycle_regime_shift"
        else:
            clog(
                "INFO",
                f"Cycle check REVIEW suppressed: last PM review {_force_age_mins:.0f}m ago (cooldown {_force_cooldown_mins}m)",
            )
    # CP-5: Exclude PENDING (entry submitted but not yet filled) and EXITING
    # (sell already in flight) from PM review. A position entered this cycle should
    # not be eligible for an immediate EXIT recommendation before IBKR confirms the
    # fill — it never gets a chance to breathe. EXITING positions are already being
    # closed, so PM reviewing them is redundant and can produce conflicting orders.
    _pm_review_statuses = {"ACTIVE", "PARTIAL_FILL"}
    pm_open_pos = [p for p in open_pos if p.get("status", "ACTIVE") in _pm_review_statuses]

    if should_review and pm_open_pos:
        clog("ANALYSIS", f"Portfolio review triggered: {pm_trigger}")
        if len(pm_open_pos) < len(open_pos):
            clog(
                "INFO",
                f"PM review: {len(pm_open_pos)}/{len(open_pos)} positions eligible "
                f"({len(open_pos) - len(pm_open_pos)} PENDING/EXITING excluded)",
            )
        try:
            # BC-2: Refresh position prices from IBKR immediately before PM review.
            # update_position_prices() above marks positions from the pipeline snapshot
            # (~30s stale by the time PM runs). A fast-moving position can appear stable
            # to PM when it isn't — ADD sizing, TRIM thresholds, and conviction-band
            # crossings are all computed against the frozen marks. A second IBKR price
            # pull here ensures PM sees live marks, not pipeline-era ones.
            update_positions_from_ibkr(ib)
            # Re-read pm_open_pos from the now-refreshed store so PM receives current prices.
            pm_open_pos = [
                p for p in get_open_positions()
                if p.get("status", "ACTIVE") in _pm_review_statuses
            ]

            # Approximate available cash from portfolio value minus open position notionals.
            # Avoids an extra IBKR call; accurate enough for Opus sizing decisions.
            _pos_notional = sum(
                p.get("current", p.get("entry", 0)) * p.get("qty", 0) for p in pm_open_pos
            )
            _available_cash = max(0.0, pv - _pos_notional)

            pm_actions = []
            try:
                import apex_orchestrator as _aorch_pm
                from signal_dispatcher import dispatch as _apex_dispatch

                _pm_review_payload = []
                try:
                    from guardrails import flag_positions_for_review as _flag_pm
                    _pm_review_payload = _flag_pm(pm_open_pos, regime)
                except Exception as _pm_flag_err:
                    log.warning("PM cutover: flag_positions_for_review failed — %s", _pm_flag_err)

                # Pre-check: skip the Apex call when it is deterministically
                # a no-op — no available slots for new entries and no
                # positions flagged for TRIM/EXIT review. Saves one Sonnet
                # round-trip per idle PM interval (~17 min cadence).
                _pm_slots_avail = max(
                    0, int(CONFIG.get("max_positions", 0) or 0) - len(pm_open_pos)
                )
                _pm_no_op = _pm_slots_avail == 0 and not _pm_review_payload
                if _pm_no_op:
                    log.info(
                        "TRACK_B_PM: skipped (slots=%d, flagged=%d) — no actionable work",
                        _pm_slots_avail, len(_pm_review_payload),
                    )
                if not _pm_no_op:
                    _pm_apex_input = _aorch_pm.build_scan_cycle_apex_input(
                        candidates=[],
                        review_positions=_pm_review_payload,
                        portfolio_state={
                            "portfolio_value": pv,
                            "daily_pnl": pnl,
                            "position_count": len(pm_open_pos),
                            "position_slots_remaining": _pm_slots_avail,
                            "open_positions": pm_open_pos,
                        },
                        regime=regime,
                    )
                    _pm_shadow = _aorch_pm._run_apex_pipeline(
                        _pm_apex_input, candidates_by_symbol={}, execute=False
                    )
                    _aorch_pm.log_shadow_result("TRACK_B_PM", _pm_shadow)

                    _pm_cutover_execute = True

                    _pm_decision = _pm_shadow.get("decision") or {}
                    _pm_report = _apex_dispatch(
                        _pm_decision,
                        candidates_by_symbol={},
                        active_trades={p.get("symbol"): p for p in pm_open_pos if p.get("symbol")},
                        ib=ib,
                        portfolio_value=pv,
                        regime=regime,
                        execute=_pm_cutover_execute,
                    )
                    # Translate report → pm_actions shape expected by the
                    # existing loop below (symbol/action/trim_pct/reasoning).
                    for _pa in _pm_report.get("portfolio_actions") or []:
                        pm_actions.append({
                            "symbol": _pa.get("symbol"),
                            "action": _pa.get("action"),
                            "trim_pct": _pa.get("trim_pct") or 50,
                            "reasoning": _pa.get("reasoning_tag") or "apex_track_b",
                        })
            except Exception as _pm_cutover_err:
                log.error("PM cutover branch failed — %s", _pm_cutover_err)
                pm_actions = []
            for action in pm_actions:
                sym_pm = action.get("symbol", "")
                act_pm = action.get("action", "HOLD")
                reason_pm = action.get("reasoning", "portfolio manager")
                if act_pm == "EXIT" and sym_pm:
                    from orders_state import _trades_lock as _pm_lock
                    from orders_state import open_trades as _pm_trades

                    # Dedup: skip if exit already in flight for this symbol
                    with _pm_lock:
                        _already_exiting = _pm_trades.get(sym_pm, {}).get("status") == "EXITING" or any(
                            v.get("status") == "EXITING" for k, v in _pm_trades.items() if v.get("symbol") == sym_pm
                        )
                    if _already_exiting:
                        clog("INFO", f"Portfolio manager EXIT: {sym_pm} already exiting — skipping duplicate")
                    else:
                        clog("TRADE", f"Portfolio manager EXIT: {sym_pm} — {reason_pm}")
                    pos_pm = next((p for p in open_pos if p["symbol"] == sym_pm), None)
                    ep_pm = pos_pm["current"] if pos_pm else 0
                    _opt_keys_pm = [
                        k
                        for k in _pm_trades
                        if k.startswith(sym_pm + "_") and _pm_trades[k].get("instrument") == "option"
                    ]
                    if not _already_exiting:
                        _pm_order_placed = False
                        if _opt_keys_pm:
                            for _ok in _opt_keys_pm:
                                clog("TRADE", f"PM EXIT routing to option sell: {_ok}")
                                _exit_reason_pm = _build_pm_exit_reason(pos_pm or {}, regime, pm_trigger, reason_pm)
                                if execute_sell_option(ib, _ok, reason=_exit_reason_pm):
                                    _pm_order_placed = True
                        if sym_pm in _pm_trades:
                            _exit_reason_pm = _build_pm_exit_reason(pos_pm or {}, regime, pm_trigger, reason_pm)
                            if execute_sell(ib, sym_pm, reason=_exit_reason_pm):
                                _pm_order_placed = True
                        if _pm_order_placed:
                            _news_pm = (dash.get("news_data") or {}).get(sym_pm, {})
                            speak_natural(
                                "exit_pm",
                                fallback=f"I'm closing {sym_pm}.",
                                symbol=sym_pm,
                                reason=reason_pm or "portfolio review",
                                news=_news_pm.get("claude_catalyst") or "none",
                            )
                    if not _already_exiting and not _opt_keys_pm and sym_pm not in _pm_trades:
                        clog(
                            "WARN",
                            f"PM EXIT: no active position found for {sym_pm} — not in tracker as stock or option",
                        )
                    if pos_pm:
                        pnl_pm = (
                            (ep_pm - pos_pm["entry"]) * pos_pm["qty"]
                            if pos_pm.get("direction", "LONG") == "LONG"
                            else (pos_pm["entry"] - ep_pm) * pos_pm["qty"]
                        )
                        from learning import log_trade as _log_trade_pm

                        _log_trade_pm(
                            trade=pos_pm,
                            agent_outputs={},
                            regime=regime,
                            action="CLOSE",
                            outcome={
                                "exit_price": round(ep_pm, 4),
                                "pnl": round(pnl_pm, 2),
                                "pnl_pct": round(
                                    pnl_pm
                                    / (
                                        (pos_pm.get("entry") or 1)
                                        * (pos_pm.get("qty") or 1)
                                        * (100 if pos_pm.get("instrument") == "option" else 1)
                                    ),
                                    4,
                                ),
                                "reason": _build_pm_exit_reason(
                                    pos_pm, regime, pm_trigger, reason_pm, exit_tag="pm_exit"
                                ),
                            },
                        )
                elif act_pm == "TRIM" and sym_pm:
                    from orders_state import _trades_lock as _pm_lock
                    from orders_state import open_trades as _pm_trades

                    pos_pm = next((p for p in open_pos if p["symbol"] == sym_pm), None)
                    # TRIM fires at most once per symbol per session.  A second TRIM on
                    # the same position (same trigger condition, just time has passed)
                    # is a loop artefact, not a new thesis event.  EXIT can still fire
                    # at any time if the thesis genuinely breaks.
                    if sym_pm in _trimmed_today:
                        clog(
                            "INFO",
                            f"PM TRIM: {sym_pm} already trimmed this session — skipping (thesis unchanged; EXIT will fire if thesis breaks)",
                        )
                        continue
                    _opt_keys_pm = [
                        k
                        for k in _pm_trades
                        if k.startswith(sym_pm + "_") and _pm_trades[k].get("instrument") == "option"
                    ]
                    _has_pos = bool(_opt_keys_pm) or sym_pm in _pm_trades
                    if not _has_pos:
                        clog("WARN", f"PM TRIM: no active position found for {sym_pm}")
                    else:
                        clog("TRADE", f"Portfolio manager TRIM: {sym_pm} — {reason_pm}")
                        _trimmed_today.add(sym_pm)
                        _trim_order_placed = False
                        _trim_pct_used = action.get("trim_pct", 50)
                        if _opt_keys_pm:
                            for _ok in _opt_keys_pm:
                                with _pm_lock:
                                    _c = _pm_trades.get(_ok, {}).get("contracts", 0)
                                _trim_c = max(1, round(_c * _trim_pct_used / 100))
                                if execute_sell_option(
                                    ib,
                                    _ok,
                                    reason=f"portfolio_manager_trim:{pm_trigger}",
                                    contracts_override=_trim_c if _trim_c < _c else None,
                                ):
                                    _trim_order_placed = True
                        if sym_pm in _pm_trades:
                            with _pm_lock:
                                _q = _pm_trades.get(sym_pm, {}).get("qty", 0)
                            _trim_q = max(1, round(_q * _trim_pct_used / 100))
                            _trim_reason = _build_pm_exit_reason(
                                pos_pm or {}, regime, pm_trigger, reason_pm, exit_tag="pm_trim"
                            )
                            if execute_sell(
                                ib, sym_pm, reason=_trim_reason, qty_override=_trim_q if _trim_q < _q else None
                            ):
                                _trim_order_placed = True
                        if _trim_order_placed:
                            speak_natural(
                                "trim",
                                fallback=f"I trimmed {sym_pm}.",
                                symbol=sym_pm,
                                pct=_trim_pct_used,
                                reason=reason_pm or "portfolio review",
                            )
                            if pos_pm:
                                _ep_trim = pos_pm.get("current", pos_pm.get("entry", 0))
                                _entry = pos_pm.get("entry", 0)
                                _trim_pnl = (
                                    (_entry - _ep_trim) * _trim_q
                                    if pos_pm.get("direction") == "SHORT"
                                    else (_ep_trim - _entry) * _trim_q
                                )
                                from learning import log_trade as _log_trade_trim

                                _trim_pos = dict(pos_pm)
                                _trim_pos["qty"] = _trim_q
                                _log_trade_trim(
                                    trade=_trim_pos,
                                    agent_outputs={},
                                    regime=regime,
                                    action="CLOSE",
                                    outcome={
                                        "exit_price": round(_ep_trim, 4),
                                        "pnl": round(_trim_pnl, 2),
                                        "pnl_pct": round(
                                            _trim_pnl
                                            / (
                                                ((_entry or 1) * _trim_q)
                                                * (100 if _trim_pos.get("instrument") == "option" else 1)
                                            ),
                                            4,
                                        ),
                                        "reason": _trim_reason,
                                    },
                                )
                elif act_pm == "ADD" and sym_pm:
                    # PM ADD path — Opus decides the verb, code decides the size.
                    # Sizing flows through calculate_position_size() on the current
                    # signal score (not entry score) — weaker signal now = smaller add.
                    # Every hardcoded risk gate that entries respect applies here too:
                    # daily loss limit, drawdown CB, min cash reserve, market hours,
                    # PDT rule, CAPITULATION regime, single-position cap, earnings 48h.
                    from orders_core import execute_add_to_position as _execute_add

                    pos_pm = next((p for p in open_pos if p["symbol"] == sym_pm), None)
                    if not pos_pm:
                        clog("WARN", f"PM ADD: no active position found for {sym_pm} — skipping")
                    else:
                        _add_price = pos_pm.get("current", 0)
                        if _add_price <= 0:
                            clog("WARN", f"PM ADD: {sym_pm} — invalid current price, skipping")
                        else:
                            # Gate 1: same portfolio-level risk checks as entries.
                            _risk_ok, _risk_reason = check_risk_conditions(
                                pv, pnl, regime, open_pos, ib=ib
                            )
                            if not _risk_ok:
                                clog("WARN", f"PM ADD: {sym_pm} blocked — {_risk_reason}")
                            else:
                                # Gate 2: earnings within lookahead window — no ADD
                                # into a binary event, even if Opus asked for it.
                                from earnings_calendar import (
                                    get_earnings_within_hours as _gew_add,
                                )

                                _pm_cfg = CONFIG.get("portfolio_manager", {})
                                _earnings_lookahead = _pm_cfg.get("earnings_lookahead_hours", 48)
                                _earnings_flagged = (
                                    _gew_add([sym_pm], _earnings_lookahead)
                                    if pos_pm.get("instrument") not in ("option", "fx")
                                    else set()
                                )
                                if sym_pm in _earnings_flagged:
                                    clog(
                                        "WARN",
                                        f"PM ADD: {sym_pm} blocked — earnings within "
                                        f"{_earnings_lookahead}h; do not add into binary event",
                                    )
                                else:
                                    # Size deterministically — current score, current ATR.
                                    _score_map_pm = {
                                        s["symbol"]: s.get("score", 0)
                                        for s in (pipeline.all_scored or [])
                                    }
                                    _current_score = int(
                                        _score_map_pm.get(sym_pm, pos_pm.get("entry_score", 0) or 0)
                                    )
                                    _current_atr = float(pos_pm.get("atr", 0) or 0)
                                    _add_qty = calculate_position_size(
                                        pv, _add_price, _current_score, regime, atr=_current_atr
                                    )
                                    # Single-position cap clamp: existing_qty + add_qty
                                    # must not push total exposure beyond max_single_position.
                                    # If no headroom, downgrade to HOLD (logged).
                                    _max_pos_frac = CONFIG.get("max_single_position", 0.10)
                                    _max_single_notional = pv * _max_pos_frac
                                    _existing_notional = pos_pm.get("qty", 0) * _add_price
                                    _headroom = max(0.0, _max_single_notional - _existing_notional)
                                    _max_add_qty = int(_headroom / _add_price) if _add_price > 0 else 0
                                    if _max_add_qty <= 0:
                                        clog(
                                            "INFO",
                                            f"PM ADD: {sym_pm} — single-position cap "
                                            f"({_max_pos_frac * 100:.0f}%) already met "
                                            f"(existing=${_existing_notional:,.0f}); "
                                            "downgrading to HOLD",
                                        )
                                    else:
                                        if _add_qty > _max_add_qty:
                                            clog(
                                                "INFO",
                                                f"PM ADD: {sym_pm} — sized {_add_qty} clamped "
                                                f"to {_max_add_qty} by single-position cap",
                                            )
                                            _add_qty = _max_add_qty
                                        _add_notional_final = _add_qty * _add_price
                                        clog(
                                            "TRADE",
                                            f"Portfolio manager ADD: {sym_pm} +{_add_qty} shares "
                                            f"(~${_add_notional_final:,.0f} @ ${_add_price:.2f}, "
                                            f"score={_current_score}) — {reason_pm}",
                                        )
                                        _added = _execute_add(
                                            ib=ib,
                                            symbol=sym_pm,
                                            add_qty=_add_qty,
                                            current_price=_add_price,
                                            regime=regime,
                                            reason=f"portfolio_manager_add:{pm_trigger}",
                                        )
                                        # A successful ADD represents a fresh conviction
                                        # re-entry.  Clear the trim-once guard so Opus can
                                        # TRIM again if the signal later collapses.
                                        if _added:
                                            _trimmed_today.discard(sym_pm)
                                            speak_natural(
                                                "add",
                                                fallback=f"I added to {sym_pm}.",
                                                symbol=sym_pm,
                                                qty=_add_qty,
                                                reason=reason_pm or "conviction strengthened",
                                            )
                else:
                    clog("INFO", f"Portfolio manager HOLD: {sym_pm} — {reason_pm}")
            # Log all actions to audit log
            import json as _json

            _audit_path = pathlib.Path("data/audit_log.jsonl")
            try:
                with _audit_path.open("a") as _af:
                    _af.write(
                        _json.dumps(
                            {
                                "type": "portfolio_review",
                                "trigger": pm_trigger,
                                "actions": pm_actions,
                                "ts": datetime.now(_ET).isoformat(),
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
            dash["pm_decisions"] = {
                "actions": pm_actions,
                "trigger": pm_trigger,
                "ts": datetime.now(_ET).strftime("%H:%M:%S"),
            }
        except Exception as _pm_err:
            clog("ERROR", f"Portfolio review error: {_pm_err}")
        finally:
            if pm_trigger == "pre_market":
                _portfolio_review_done_today = True
            if pm_trigger == "cascade":
                _cascade_reviewed_this_session = True
            _last_known_regime = regime.get("regime", _last_known_regime)
            _last_pm_review_ts = datetime.now(_ET)
            # Snapshot the news scores and collapse scores seen at this review so that
            # news_hit and score_collapse don't re-fire unless values change materially.
            _scored_map_snap = {s["symbol"]: s.get("score", 0) for s in (pipeline.all_scored or [])}
            # CP-5: iterate pm_open_pos (reviewed set) not open_pos (includes PENDING/EXITING)
            for _rp in pm_open_pos:
                _rsym = _rp["symbol"]
                _kw = news_sentiment.get(_rsym, {}).get("keyword_score", 0)
                _last_news_scores[_rsym] = _kw
                _cs = _scored_map_snap.get(_rsym)
                if _cs is not None:
                    _last_collapse_scores[_rsym] = _cs
                    # Snapshot current score for held_score_rise edge dedup too.
                    # Without this, a position scoring 65 on every cycle would
                    # re-fire PM every cycle; with it, only further rises fire.
                    _last_rise_scores[_rsym] = _cs
            # Record which regime each reviewed position was reviewed under so cycle_check
            # does not re-queue the same REVIEW on subsequent cycles for the same regime state.
            _reviewed_regime_label = regime.get("session_character") or regime.get("regime", "UNKNOWN")
            _now_reviewed = datetime.now(UTC)
            for _rp in pm_open_pos:
                _pm_reviewed_regime[_rp["symbol"]] = _reviewed_regime_label
                _last_pm_review_ts_by_symbol[_rp["symbol"]] = _now_reviewed
    else:
        _last_known_regime = regime.get("regime", _last_known_regime)

    open_pos = get_open_positions()  # refresh after any PM exits
    dash["positions"] = open_pos

    # CP-4: PM exits change daily P&L but strategy_mode was frozen before PM ran.
    # Recompute pnl + strategy_mode so agents size new trades against the actual
    # post-PM portfolio state (e.g. a large exit tipping NORMAL → DEFENSIVE).
    pv, pnl = get_account_data()
    _post_pm_mode = get_intraday_strategy_mode(pv, pnl, regime["regime"])
    if _post_pm_mode["mode"] != strategy_mode["mode"]:
        clog(
            "RISK",
            f"Strategy mode updated post-PM: {strategy_mode['mode']} → {_post_pm_mode['mode']} "
            f"(PnL {_post_pm_mode['daily_pnl_pct']*100:+.1f}%)",
        )
    strategy_mode = _post_pm_mode

    positions_to_reconsider = check_thesis_validity(open_pos, regime["regime"])
    if positions_to_reconsider:
        clog(
            "RISK",
            f"Thesis invalidation: {len(positions_to_reconsider)} position(s) flagged for agent review (regime shift)",
        )
        for _p in positions_to_reconsider:
            clog("RISK", f"  Reconsider: {_p['symbol']} — {_p['reason']}")

    # CP-3: Re-check regime immediately before agents — the scoring + PM pipeline
    # can take 2-5 minutes; a VIX spike mid-scan would leave agents sizing trades
    # with a stale (pre-spike) regime and wrong size_multiplier. If regime shifted,
    # recompute strategy_mode so Agent 4 uses the correct sizing context.
    _pre_agent_regime = get_market_regime(ib)
    if _pre_agent_regime.get("regime") != regime.get("regime"):
        clog(
            "RISK",
            f"Regime shifted mid-scan: {regime['regime']} → {_pre_agent_regime['regime']} — updating before agents",
        )
        regime = _pre_agent_regime
        strategy_mode = get_intraday_strategy_mode(pv, pnl, regime["regime"])

    _agent_pos_notional = sum(
        p.get("current", p.get("entry", 0)) * p.get("qty", 0) for p in open_pos
    )
    _agent_cash = max(0.0, pv - _agent_pos_notional)

    _open_syms_pre = {p["symbol"] for p in open_pos if p.get("symbol")}
    _apex_mode_sells = [
        p["symbol"]
        for p in positions_to_reconsider
        if p.get("reason", "").upper() != "HOLD" and p["symbol"] in _open_syms_pre
    ]
    decision = {
        "buys": [],
        "sells": _apex_mode_sells,
        "hold": [],
        "_agent_outputs": {},
        "cash": False,
    }

    dash["claude_analysis"] = "Apex scan in progress…"

    now_str = datetime.now(_ET).strftime("%H:%M:%S")

    if dash.get("killed"):
        clog("RISK", "🚨 Kill switch active — skipping all trade execution")
        dash["scanning"] = False
        return

    if decision.get("cash"):
        clog("RISK", "Agents instructed: go to cash — flattening all positions")
        flatten_all(ib)
        dash["scanning"] = False
        return

    for sym in decision.get("sells", []):
        clog("TRADE", f"Selling {sym} on agent signal")
        pos = next((p for p in open_pos if p["symbol"] == sym), None)
        exit_price = pos["current"] if pos else 0
        _sell_placed = execute_sell(ib, sym, reason="Agent sell signal")
        if _sell_placed:
            _news_ctx = (dash.get("news_data") or {}).get(sym, {})
            speak_natural(
                "exit_agent",
                fallback=f"I'm closing out {sym}.",
                symbol=sym,
                reason=decision.get("reasoning", "agent signal"),
                news=_news_ctx.get("claude_catalyst") or _news_ctx.get("headlines", [""])[0] if _news_ctx else "none",
            )
        dash["trades"].insert(
            0, {"side": "SELL", "symbol": sym, "price": str(exit_price), "time": datetime.now(_ET).strftime("%H:%M:%S")}
        )
        if pos:
            pnl_val = (
                (exit_price - pos["entry"]) * pos["qty"]
                if pos.get("direction", "LONG") == "LONG"
                else (pos["entry"] - exit_price) * pos["qty"]
            )
            from learning import log_trade as _log_trade

            _log_trade(
                trade=pos,
                agent_outputs=decision.get("_agent_outputs", {}),
                regime=regime,
                action="CLOSE",
                outcome={
                    "exit_price": round(exit_price, 4),
                    "pnl": round(pnl_val, 2),
                    "pnl_pct": round(
                        pnl_val
                        / (
                            (pos.get("entry") or 1)
                            * (pos.get("qty") or 1)
                            * (100 if pos.get("instrument") == "option" else 1)
                        ),
                        4,
                    ),
                    "reason": "agent_sell",
                },
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

    try:
        import apex_orchestrator as _aorch_track_a
        from guardrails import filter_candidates as _fc_track_a
        from guardrails import screen_open_positions as _screen_track_a
        from guardrails import flag_positions_for_review as _flag_track_a

        _cut_candidates_raw = _fc_track_a(
            [c for c in (pipeline.all_scored or []) if c.get("symbol")],
            {p.get("symbol") for p in open_pos if p.get("symbol")},
            regime=regime,
        )
        # Cap at top 30 by score — sending 80+ candidates bloats the Apex
        # response past the token budget and causes JSON truncation.
        _cut_candidates = sorted(
            _cut_candidates_raw, key=lambda c: c.get("score", 0), reverse=True
        )[:30]
        if len(_cut_candidates_raw) > 30:
            clog("INFO",
                 f"APEX_LIVE: {len(_cut_candidates_raw)} candidates after guardrails "
                 f"— capped to top 30 by score")
        try:
            _cut_review = _flag_track_a(open_pos, regime)
        except Exception:
            _cut_review = []
        try:
            _cut_forced = _screen_track_a(open_pos) or []
        except Exception:
            _cut_forced = []
        _cut_portfolio_state = {
            "portfolio_value": pv,
            "daily_pnl": pnl,
            "position_count": len(open_pos),
            "position_slots_remaining": max(
                0, int(CONFIG.get("max_positions", 0) or 0) - len(open_pos)
            ),
            "open_positions": open_pos,
        }
        _cut_apex_input = _aorch_track_a.build_scan_cycle_apex_input(
            candidates=_cut_candidates,
            review_positions=_cut_review,
            portfolio_state=_cut_portfolio_state,
            regime=regime,
            options_flow=options_signals,
        )
        _cut_by_sym = {
            c.get("symbol"): c for c in _cut_candidates if c.get("symbol")
        }
        _cut_active = {
            p.get("symbol"): p for p in open_pos if p.get("symbol")
        }
        _cut_result = _aorch_track_a._run_apex_pipeline(
            _cut_apex_input,
            _cut_by_sym,
            execute=True,
            active_trades=_cut_active,
            ib=ib,
            portfolio_value=pv,
            regime=regime,
            forced_exits=_cut_forced,
        )
        _cut_report = _cut_result.get("dispatch_report") or {}
        clog(
            "INFO",
            "APEX_LIVE SCAN_CYCLE: "
            f"{len(_cut_report.get('new_entries') or [])} entries, "
            f"{len(_cut_report.get('portfolio_actions') or [])} actions, "
            f"{len(_cut_report.get('forced_exits') or [])} forced, "
            f"{len(_cut_report.get('errors') or [])} errors",
        )
        try:
            _aorch_track_a.log_shadow_result("SCAN_CYCLE", _cut_result)
        except Exception as _log_err:
            log.warning("APEX_LIVE: shadow log failed — %s", _log_err)
        _apex_decision = _cut_result.get("decision") or {}
        _apex_meta = _apex_decision.get("_meta") or {}
        dash["claude_analysis"] = (
            _apex_decision.get("market_read")
            or _apex_decision.get("session_character")
            or "Apex scan complete"
        )
        dash["agent_conversation"] = [{
            "agent": "Apex Synthesizer",
            "role": "Single claude-sonnet-4-6 call — candidates, regime, portfolio, session context → ApexDecision",
            "time": now_str,
            "session_character": _apex_decision.get("session_character") or "",
            "macro_bias": _apex_decision.get("macro_bias") or "",
            "market_read": _apex_decision.get("market_read") or "",
            "new_entries": _apex_decision.get("new_entries") or [],
            "latency_ms": _apex_meta.get("latency_ms"),
            "output_tokens": _apex_meta.get("output_tokens"),
        }]
        if _apex_decision.get("portfolio_actions"):
            dash["pm_decisions"] = {
                "actions": [
                    {
                        "symbol": _pa.get("symbol"),
                        "action": _pa.get("action"),
                        "trim_pct": _pa.get("trim_pct"),
                        "reasoning": _pa.get("reasoning"),
                        "reasoning_tag": _pa.get("reasoning_tag"),
                    }
                    for _pa in _apex_decision["portfolio_actions"]
                ],
                "trigger": "apex_scan_cycle",
                "ts": now_str,
            }
    except Exception as _cut_err:
        log.error("APEX_LIVE SCAN_CYCLE failed — %s", _cut_err)
    dash["scanning"] = False
    clog("SCAN", f"Scan #{bot_state.scan_count} complete")

