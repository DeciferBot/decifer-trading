"""
voice_explainability_tools.py — Context slice formatters for specific voice query types.

Each function takes the full context dict (from voice_context_builder.build_full_context)
and returns a plain-text string targeted at a specific question type.
No LLM calls. No side effects. Safe from any thread.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional


def _age_str(ts_str: Optional[str]) -> str:
    if not ts_str or ts_str in ("unknown", "None", ""):
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return "unknown"
        age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        if age_s < 60:
            return "just now"
        if age_s < 3600:
            return f"{int(age_s / 60)}m ago"
        if age_s < 86400:
            return f"{int(age_s / 3600)}h ago"
        return f"{int(age_s / 86400)}d ago"
    except Exception:
        return "unknown"


def explain_position(symbol: str, ctx: dict) -> str:
    """Context for 'why are we holding X' / 'tell me about X'."""
    sym = symbol.upper()
    positions = ctx.get("positions", {})
    pm = ctx.get("pm_decisions", [])

    pos = positions.get(sym)
    if not pos:
        # Check if recently closed
        for t in ctx.get("recent_trades", []):
            if (t.get("symbol") or "").upper() == sym:
                pnl_pct = (t.get("pnl_pct") or 0) * 100
                return (
                    f"NO OPEN POSITION — {sym} was recently closed.\n"
                    f"Exit reason: {t.get('exit_reason', 'unknown')}\n"
                    f"P&L: ${t.get('pnl', 0):+.2f} ({pnl_pct:+.2f}%)\n"
                    f"Hold time: {t.get('hold_minutes', 0)} minutes\n"
                    f"Closed: {_age_str(t.get('exit_time'))}"
                )
        return f"No open position or recent trade record found for {sym}."

    entry = pos.get("entry") or 0
    current = pos.get("current") or 0
    pnl_pct = pos.get("pnl_pct") or 0

    lines = [
        f"OPEN POSITION: {sym} {pos.get('direction', 'LONG')}",
        f"Qty: {pos.get('qty', 0)} | Entry: ${entry:.2f} | Now: ${current:.2f}",
        f"P&L: ${pos.get('pnl', 0):+,.2f} ({pnl_pct:+.2f}%)",
    ]
    if pos.get("sl") or pos.get("tp"):
        lines.append(
            f"Stop: ${pos.get('sl', 0):.2f} | Target: ${pos.get('tp', 0):.2f}"
        )
    lines += [
        f"Entry score: {pos.get('score', 'n/a')} | Conviction: {(pos.get('conviction') or 0) * 100:.0f}%",
        f"Setup: {pos.get('setup_type', 'unknown')} | Type: {pos.get('trade_type', 'unknown')}",
        f"Regime at entry: {pos.get('entry_regime', 'unknown')}",
        f"Opened: {_age_str(str(pos.get('open_time') or ''))}",
    ]
    if pos.get("entry_thesis"):
        lines.append(f"Entry thesis: {pos['entry_thesis'][:300]}")
    if pos.get("reasoning"):
        lines.append(f"Reasoning: {pos['reasoning'][:200]}")

    # Latest PM decision for this symbol
    pm_for_sym = [d for d in pm if (d.get("symbol") or "").upper() == sym]
    if pm_for_sym:
        latest_pm = pm_for_sym[-1]
        lines.append(
            f"\nLatest PM review ({_age_str(latest_pm.get('ts'))}):\n"
            f"  Action: {latest_pm.get('action_type', 'HOLD')} | "
            f"Thesis: {latest_pm.get('thesis_status', 'unknown')}\n"
            f"  Rationale: {(latest_pm.get('rationale') or '')[:150]}"
        )

    return "\n".join(l for l in lines if l)


def explain_recent_trade(symbol: Optional[str], ctx: dict) -> str:
    """Context for 'why did you buy/sell X' or 'what was the last trade'."""
    trades = ctx.get("recent_trades", [])
    if not trades:
        return "No recent closed trade records found."

    if symbol:
        sym = symbol.upper()
        matches = [t for t in trades if (t.get("symbol") or "").upper() == sym]
        if not matches:
            return f"No recent trade record found for {sym}."
        t = matches[0]
    else:
        t = trades[0]

    pnl_pct = (t.get("pnl_pct") or 0) * 100
    lines = [
        f"TRADE: {t.get('symbol')} {t.get('direction', 'LONG')}",
        f"Entry: ${t.get('entry_price', 0):.2f} | Exit: ${t.get('exit_price', 0):.2f}",
        f"P&L: ${t.get('pnl', 0):+.2f} ({pnl_pct:+.2f}%)",
        f"Exit reason: {t.get('exit_reason', 'unknown')}",
        f"Hold time: {t.get('hold_minutes', 0)} minutes",
        f"Score at entry: {t.get('score', 'n/a')} | Setup: {t.get('setup_type', 'unknown')}",
        f"Regime at entry: {t.get('regime', 'unknown')}",
        f"Trade type: {t.get('trade_type', 'unknown')}",
        f"Closed: {_age_str(t.get('exit_time'))}",
    ]
    if t.get("entry_thesis"):
        lines.append(f"Entry thesis: {t['entry_thesis'][:300]}")
    if t.get("reasoning"):
        lines.append(f"Reasoning: {t['reasoning'][:200]}")

    return "\n".join(l for l in lines if l)


def explain_blocked_candidate(symbol: str, ctx: dict) -> str:
    """Context for 'why was X blocked / why didn't you trade X'."""
    sym = symbol.upper()

    # Check Apex per-symbol decision log
    apex_candidates = ctx.get("apex_candidates", [])
    sym_decisions = [d for d in apex_candidates if (d.get("symbol") or "").upper() == sym]
    latest_apex = sym_decisions[-1] if sym_decisions else None

    # Check if in live universe
    universe = ctx.get("live_universe", {})
    top_candidates = universe.get("top_candidates", [])
    in_universe = any((c.get("symbol") or "").upper() == sym for c in top_candidates)

    # Check recent signals
    signals = ctx.get("recent_signals", [])
    sig = next((s for s in signals if (s.get("symbol") or "").upper() == sym), None)

    # Check if currently held
    positions = ctx.get("positions", {})
    held = sym in positions

    lines = [f"CANDIDATE ANALYSIS: {sym}"]

    if held:
        pos = positions[sym]
        lines.append(f"Status: CURRENTLY HELD — {pos.get('direction')} {pos.get('qty')} shares, P&L ${pos.get('pnl', 0):+.2f}")
        return "\n".join(lines)

    lines.append(f"In active opportunity universe: {in_universe}")

    if sig:
        lines.append(f"Recent signal score: {sig.get('score')} (regime: {sig.get('regime', 'unknown')}, {_age_str(sig.get('ts'))})")
    else:
        lines.append(f"No recent high-scoring signal record for {sym}.")

    if latest_apex:
        lines += [
            f"\nLatest Apex decision for {sym} ({_age_str(latest_apex.get('ts'))}):",
            f"  Decision: {latest_apex.get('apex_decision', 'unknown')}",
            f"  Score: {latest_apex.get('raw_score', 'n/a')}",
            f"  Reason: {latest_apex.get('apex_reason', 'no reason recorded')}",
        ]
    else:
        lines.append(f"\nNo Apex per-symbol decision record found for {sym} in recent audit log.")

    # Latest aggregate — gives context about the cycle
    aggregates = ctx.get("apex_aggregates", [])
    if aggregates:
        agg = aggregates[-1]
        lines.append(
            f"\nLatest scan cycle ({_age_str(agg.get('ts'))}): "
            f"{agg.get('total_candidates', 0)} candidates sent to Apex, "
            f"{agg.get('new_entries_count', 0)} entries selected, "
            f"{agg.get('blocked_count', 0)} blocked."
        )

    return "\n".join(l for l in lines if l)


def explain_no_trade(ctx: dict) -> str:
    """Context for 'why did you not trade today' / 'what is the bot waiting for'."""
    dash = ctx.get("dash", {})
    universe = ctx.get("live_universe", {})
    aggregates = ctx.get("apex_aggregates", [])
    signals = ctx.get("recent_signals", [])

    lines = [
        f"Bot status: {'PAUSED' if dash.get('paused') else 'ACTIVE'}",
        f"Session: {dash.get('session', 'unknown')}",
        f"IBKR connected: {not dash.get('ibkr_disconnected', False)}",
        f"Total scans run: {dash.get('scan_count', 0)}",
        f"Apex errors last hour: {dash.get('apex_errors_1h', 0)}",
        f"\nOpportunity universe: {universe.get('total_candidates', 0)} total candidates",
        f"  Position candidates: {universe.get('position_candidates', 0)}",
        f"  Universe age: {universe.get('age_str', 'unknown')}"
        + (" — STALE" if universe.get("stale") else ""),
    ]

    if signals:
        top = signals[0]
        lines.append(
            f"\nHighest signal score seen recently: "
            f"{top.get('symbol')} scored {top.get('score')} "
            f"in {top.get('regime', 'unknown')} regime ({_age_str(top.get('ts'))})"
        )

    if aggregates:
        # Show last few cycles
        lines.append(f"\nRecent scan cycles ({len(aggregates)} shown):")
        for agg in aggregates[-3:]:
            entries = agg.get("new_entries_count", 0)
            syms = agg.get("new_entries_symbols", [])
            lines.append(
                f"  {_age_str(agg.get('ts'))}: "
                f"{agg.get('total_candidates', 0)} candidates → "
                f"{entries} entries selected"
                + (f" ({', '.join(syms)})" if syms else "")
                + f", {agg.get('blocked_count', 0)} blocked"
            )

    if dash.get("claude_analysis"):
        lines.append(f"\nLatest Apex synthesis:\n{dash['claude_analysis'][:400]}")

    return "\n".join(l for l in lines if l)


def explain_portfolio_risk(ctx: dict) -> str:
    """Context for 'are we overexposed' / 'main risks in the book'."""
    positions = ctx.get("positions", {})
    dash = ctx.get("dash", {})
    pm = ctx.get("pm_decisions", [])

    nlv = dash.get("portfolio_value") or 1

    lines = [
        f"Portfolio NLV: ${nlv:,.2f}",
        f"Daily P&L: ${dash.get('daily_pnl', 0):+,.2f}",
        f"Open positions: {len(positions)}",
    ]

    if positions:
        long_notional = sum(
            p.get("qty", 0) * (p.get("current") or 0)
            for p in positions.values()
            if p.get("direction") == "LONG"
        )
        short_notional = sum(
            p.get("qty", 0) * (p.get("current") or 0)
            for p in positions.values()
            if p.get("direction") == "SHORT"
        )
        if long_notional:
            lines.append(f"Long notional: ${long_notional:,.2f} ({long_notional / nlv * 100:.1f}% of NLV)")
        if short_notional:
            lines.append(f"Short notional: ${short_notional:,.2f} ({short_notional / nlv * 100:.1f}% of NLV)")

        # Sort by P&L to surface weakest/strongest
        sorted_pos = sorted(positions.items(), key=lambda x: x[1].get("pnl", 0))
        lines.append("\nPositions (worst to best P&L):")
        for sym, p in sorted_pos:
            notional = p.get("qty", 0) * (p.get("current") or 0)
            lines.append(
                f"  {sym} {p.get('direction', 'LONG')}: "
                f"P&L ${p.get('pnl', 0):+,.2f} | "
                f"${notional:,.0f} notional | "
                f"setup: {p.get('setup_type', 'unknown')} | "
                f"thesis: {p.get('entry_thesis', '')[:80]}"
            )

    # Safety-blocked PM actions
    blocked = [d for d in pm if d.get("safety_blocked")]
    if blocked:
        lines.append(f"\nRecent PM safety blocks: {len(blocked)}")
        for b in blocked[-3:]:
            lines.append(f"  {b.get('symbol')}: {b.get('safety_block_reason', 'unknown')}")

    return "\n".join(l for l in lines if l)


def explain_market_regime(ctx: dict) -> str:
    """Context for 'what is the market regime / how is the market'."""
    dash = ctx.get("dash", {})
    regime = dash.get("regime", {})
    drivers = ctx.get("driver_state", {})

    lines = [
        f"Market regime: {regime.get('regime', 'UNKNOWN')}",
        f"VIX: {regime.get('vix', '?')}",
        f"SPY: ${regime.get('spy_price', '?')}",
        f"Session: {dash.get('session', 'UNKNOWN')}",
    ]

    if drivers.get("active_drivers"):
        lines.append(f"Active market drivers: {', '.join(drivers['active_drivers'])}")
    if drivers.get("blocked_conditions"):
        lines.append(f"Blocked conditions: {', '.join(drivers['blocked_conditions'])}")
    if drivers.get("age_str"):
        lines.append(f"Driver state age: {drivers['age_str']}")

    if dash.get("claude_analysis"):
        lines.append(f"\nLatest Apex market view:\n{dash['claude_analysis'][:500]}")

    return "\n".join(l for l in lines if l)


def explain_active_themes(ctx: dict) -> str:
    """Context for 'which theme is strongest / what themes are active'."""
    themes = ctx.get("themes", {})
    universe = ctx.get("live_universe", {})
    drivers = ctx.get("driver_state", {})

    lines = []

    if themes.get("activated"):
        lines.append(f"Active themes: {', '.join(themes['activated'])}")
    else:
        lines.append("No themes currently activated.")

    if themes.get("crowded"):
        lines.append(f"Crowded (position limit reached): {', '.join(themes['crowded'])}")

    if themes.get("dormant"):
        lines.append(f"Dormant themes: {', '.join(themes['dormant'][:6])}")

    if themes.get("age_str"):
        lines.append(f"Theme data: {themes['age_str']}" + (" — SHADOW MODE" if "shadow" in (themes.get("mode") or "") else ""))

    if drivers.get("active_drivers"):
        lines.append(f"\nMarket forces driving themes: {', '.join(drivers['active_drivers'])}")

    if universe.get("top_candidates"):
        lines.append("\nTop candidates from universe:")
        for c in universe["top_candidates"][:6]:
            reason = c.get("reason") or "no reason logged"
            lines.append(f"  {c.get('symbol')}: {reason[:90]}")

    return "\n".join(l for l in lines if l)


def explain_bot_health(ctx: dict) -> str:
    """Context for 'what is the bot doing / what is holding it back'."""
    dash = ctx.get("dash", {})
    universe = ctx.get("live_universe", {})

    if dash.get("killed"):
        status = "KILLED"
    elif dash.get("paused"):
        status = "PAUSED"
    elif dash.get("scanning"):
        status = "SCANNING NOW"
    else:
        status = "IDLE (between scans)"

    lines = [
        f"Bot status: {status}",
        f"IBKR connection: {'DISCONNECTED' if dash.get('ibkr_disconnected') else 'OK'}",
        f"Session: {dash.get('session', 'UNKNOWN')}",
        f"Total scans completed: {dash.get('scan_count', 0)}",
        f"Last scan: {_age_str(str(dash.get('last_scan') or ''))}",
        f"Apex errors last hour: {dash.get('apex_errors_1h', 0)}",
        f"\nUniverse: {universe.get('total_candidates', 0)} candidates",
        f"  Handoff: {'enabled' if universe.get('handoff_enabled') else 'disabled'}",
        f"  Universe age: {universe.get('age_str', 'unknown')}"
        + (" — STALE" if universe.get("stale") else ""),
    ]

    return "\n".join(l for l in lines if l)


def explain_learning(ctx: dict) -> str:
    """Context for 'what did the bot learn / how have we been doing'."""
    training = ctx.get("training_summary", {})
    recent = ctx.get("recent_trades", [])

    lines = [
        f"Training records (last 50 reviewed): {training.get('recent_record_count', 0)}",
        f"ML eligible: {training.get('ml_eligible_count', 0)}",
        f"Winners: {training.get('winners', 0)} | Losers: {training.get('losers', 0)}",
        f"Average P&L (recent sample): ${training.get('avg_pnl_recent', 0):+.2f}",
    ]

    exit_reasons = training.get("exit_reasons", {})
    if exit_reasons:
        lines.append(f"Exit reason mix: {json.dumps(exit_reasons)}")

    if recent:
        last = recent[0]
        pnl_pct = (last.get("pnl_pct") or 0) * 100
        lines.append(
            f"\nMost recent closed trade: {last.get('symbol')} "
            f"{last.get('direction', 'LONG')} — "
            f"${last.get('pnl', 0):+.2f} ({pnl_pct:+.2f}%) "
            f"via {last.get('exit_reason', 'unknown')}"
        )

    return "\n".join(l for l in lines if l)
