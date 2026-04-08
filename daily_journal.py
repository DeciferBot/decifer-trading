# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  daily_journal.py                          ║
# ║   Automated daily trade journal. Run at end of each day.    ║
# ║   Produces a dated .md file in journals/ with full analysis ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
import sys
import zoneinfo
from datetime import datetime, timedelta, timezone
from collections import defaultdict

_ET = zoneinfo.ZoneInfo("America/New_York")

TRADE_FILE = "data/trades.json"
ORDER_FILE = "data/orders.json"
EQUITY_FILE = "data/equity_history.json"
JOURNAL_DIR = "journals"
LOG_FILE = "logs/decifer.log"

# ── Helpers ──────────────────────────────────────────────────────

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def parse_ts(raw):
    """Best-effort parse of various timestamp formats in our data."""
    if not raw:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S"]:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def get_day_trades(trades, target_date):
    """Return trades that were entered OR exited on target_date (string YYYY-MM-DD)."""
    day_trades = []
    for t in trades:
        # Use entry_time, exit_time, or timestamp — whichever matches the day
        for key in ["entry_time", "exit_time", "timestamp"]:
            val = t.get(key, "")
            if val and val[:10] == target_date:
                day_trades.append(t)
                break
    return day_trades


def get_day_orders(orders, target_date):
    """Return orders from target_date."""
    return [o for o in orders if (o.get("timestamp", "") or "")[:10] == target_date]


# ── Core Analysis ────────────────────────────────────────────────

def analyse_trades(trades):
    """Full analysis of a set of trades. Returns dict of metrics."""
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        return {"total": 0}

    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    pnls = [t["pnl"] for t in closed]

    gross_win = sum(t["pnl"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0

    # By exit reason
    by_reason = defaultdict(lambda: {"count": 0, "pnl": 0})
    for t in closed:
        r = t.get("exit_reason", "unknown")
        by_reason[r]["count"] += 1
        by_reason[r]["pnl"] += t["pnl"]

    # By regime
    by_regime = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in closed:
        r = t.get("regime", "UNKNOWN")
        by_regime[r]["count"] += 1
        by_regime[r]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            by_regime[r]["wins"] += 1

    # By direction
    by_dir = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in closed:
        d = t.get("direction", "LONG")
        by_dir[d]["count"] += 1
        by_dir[d]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            by_dir[d]["wins"] += 1

    # By symbol
    by_sym = defaultdict(lambda: {"count": 0, "pnl": 0})
    for t in closed:
        s = t.get("symbol", "?")
        by_sym[s]["count"] += 1
        by_sym[s]["pnl"] += t["pnl"]

    # Hold time analysis
    hold_times = [t.get("hold_minutes") for t in closed if t.get("hold_minutes") is not None and t["hold_minutes"] > 0]
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else None

    # Quick exits (<5 min)
    quick_exits = [t for t in closed if t.get("hold_minutes") is not None and 0 < t["hold_minutes"] <= 5]

    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
        "best": max(pnls),
        "worst": min(pnls),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "by_reason": dict(by_reason),
        "by_regime": dict(by_regime),
        "by_direction": dict(by_dir),
        "by_symbol": dict(by_sym),
        "avg_hold_min": round(avg_hold, 1) if avg_hold else None,
        "quick_exits": len(quick_exits),
        "trades": closed,
    }


def identify_patterns(today, cumulative):
    """Compare today vs cumulative to find emerging patterns."""
    patterns = []

    # Pattern: Long bias in bear market
    if today.get("by_direction", {}).get("LONG", {}).get("count", 0) > 0:
        long_data = today["by_direction"]["LONG"]
        total = today["total"]
        long_pct = long_data["count"] / total * 100 if total else 0
        bear_trades = today.get("by_regime", {}).get("BEAR_TRENDING", {}).get("count", 0)
        if long_pct > 75 and bear_trades > 0:
            patterns.append({
                "type": "CRITICAL",
                "name": "Long Bias in Bear Market",
                "detail": f"{long_pct:.0f}% of trades were LONG but {bear_trades} trades occurred in BEAR_TRENDING regime. "
                          f"Longs lost ${long_data['pnl']:+,.2f} today.",
                "action": "Prioritize building direction-agnostic signals (roadmap #01) and short-candidate scanner (#02)."
            })

    # Pattern: Stop-loss domination
    sl_data = today.get("by_reason", {}).get("stop_loss", {})
    if sl_data.get("count", 0) > today["total"] * 0.5 and today["total"] >= 3:
        patterns.append({
            "type": "WARNING",
            "name": "Stop-Loss Dominated",
            "detail": f"{sl_data['count']}/{today['total']} trades hit stop loss (${sl_data['pnl']:+,.2f}). "
                      f"Either entries are poor or stops are too tight.",
            "action": "Review entry timing relative to intraday volatility. Consider wider stops with smaller position sizes."
        })

    # Pattern: Accelerating losses (each day worse than prior)
    if cumulative and today["total_pnl"] < cumulative.get("avg_daily_pnl", 0) * 1.5:
        patterns.append({
            "type": "WARNING",
            "name": "Accelerating Losses",
            "detail": f"Today's P&L (${today['total_pnl']:+,.2f}) is worse than the cumulative daily average "
                      f"(${cumulative.get('avg_daily_pnl', 0):+,.2f}). Losses are getting bigger, not smaller.",
            "action": "Consider reducing position sizing or pausing until roadmap fixes are live."
        })

    # Pattern: Overtrading
    if today["total"] > 15:
        patterns.append({
            "type": "WARNING",
            "name": "Overtrading",
            "detail": f"{today['total']} trades in a single day. More trades ≠ more alpha. "
                      f"Commission drag and poor entry selectivity compound losses.",
            "action": "Raise consensus threshold to 3/6 (roadmap #08 — 5-minute config change)."
        })

    # Pattern: Quick exits (held < 5 min)
    if today.get("quick_exits", 0) >= 3:
        patterns.append({
            "type": "INFO",
            "name": "Rapid-Fire Exits",
            "detail": f"{today['quick_exits']} trades held < 5 minutes. These are likely noise trades that "
                      f"enter and immediately get stopped out.",
            "action": "Check if stop-loss placement is too tight relative to bid-ask spread and short-term volatility."
        })

    # Pattern: UNKNOWN regime trading
    unk = today.get("by_regime", {}).get("UNKNOWN", {})
    if unk.get("count", 0) > today["total"] * 0.4 and today["total"] >= 3:
        patterns.append({
            "type": "WARNING",
            "name": "Blind Regime Trading",
            "detail": f"{unk['count']}/{today['total']} trades entered with regime=UNKNOWN. "
                      f"The system doesn't know what market it's trading in.",
            "action": "Fix regime detection so it resolves before order placement. Never enter with UNKNOWN regime."
        })

    # Pattern: Repeated symbol losses
    for sym, data in today.get("by_symbol", {}).items():
        if data["count"] >= 2 and data["pnl"] < -500:
            patterns.append({
                "type": "INFO",
                "name": f"Repeat Loser: {sym}",
                "detail": f"{sym} traded {data['count']} times today for ${data['pnl']:+,.2f}. "
                          f"Re-entering a losing name in the same session compounds losses.",
                "action": f"Consider a cooldown period after closing a losing {sym} position."
            })

    return patterns


# ── Journal Rendering ────────────────────────────────────────────

def render_journal(target_date, today_analysis, cumulative_analysis, patterns, day_orders, day_number):
    """Render a markdown journal entry."""
    a = today_analysis
    lines = []

    lines.append(f"# Decifer Daily Journal — {target_date} (Day {day_number})")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Headline
    emoji = "🟢" if a["total_pnl"] >= 0 else "🔴"
    lines.append(f"## {emoji} Day Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Trades | {a['total']} ({a['wins']}W / {a['losses']}L) |")
    lines.append(f"| Win Rate | {a['win_rate']}% |")
    lines.append(f"| Day P&L | ${a['total_pnl']:+,.2f} |")
    lines.append(f"| Best Trade | ${a['best']:+,.2f} |")
    lines.append(f"| Worst Trade | ${a['worst']:+,.2f} |")
    lines.append(f"| Avg Win | ${a['avg_win']:+,.2f} |")
    lines.append(f"| Avg Loss | ${a['avg_loss']:+,.2f} |")
    lines.append(f"| Profit Factor | {a['profit_factor']} |")
    if a.get("avg_hold_min"):
        lines.append(f"| Avg Hold Time | {a['avg_hold_min']} min |")
    lines.append(f"| Orders Placed | {len(day_orders)} |")
    lines.append("")

    # ── Trade Log
    lines.append("## Trade Log")
    lines.append("")
    lines.append("| # | Symbol | Dir | Qty | Entry | Exit | P&L | Exit Reason | Regime | Hold |")
    lines.append("|---|--------|-----|-----|-------|------|-----|-------------|--------|------|")
    for i, t in enumerate(a["trades"], 1):
        sym = t.get("symbol", "?")
        direction = t.get("direction", "LONG")
        qty = t.get("shares") or t.get("qty", 0)
        entry = t.get("entry_price", 0)
        exit_p = t.get("exit_price", 0)
        pnl = t["pnl"]
        reason = t.get("exit_reason", "?")
        regime = t.get("regime", "?")
        hold = t.get("hold_minutes")
        hold_str = f"{hold}m" if hold and hold > 0 else "—"
        pnl_fmt = f"${pnl:+,.2f}"
        lines.append(f"| {i} | {sym} | {direction} | {qty} | ${entry:.2f} | ${exit_p:.2f} | {pnl_fmt} | {reason} | {regime} | {hold_str} |")
    lines.append("")

    # ── What Worked
    lines.append("## What Worked")
    lines.append("")
    winners = [t for t in a["trades"] if t["pnl"] > 0]
    if winners:
        for t in sorted(winners, key=lambda x: x["pnl"], reverse=True):
            sym = t.get("symbol", "?")
            pnl = t["pnl"]
            direction = t.get("direction", "LONG")
            reason = t.get("exit_reason", "?")
            reasoning = (t.get("reasoning") or "")[:150]
            lines.append(f"**{sym} {direction} (+${pnl:,.2f})** — Exited via {reason}. {reasoning}")
            lines.append("")
    else:
        lines.append("Nothing. Zero winning trades today. That's the signal — something is fundamentally wrong with entry selection.")
        lines.append("")

    # ── What Didn't Work
    lines.append("## What Didn't Work")
    lines.append("")
    losers = sorted([t for t in a["trades"] if t["pnl"] <= 0], key=lambda x: x["pnl"])
    top_losers = losers[:5]  # Worst 5
    for t in top_losers:
        sym = t.get("symbol", "?")
        pnl = t["pnl"]
        direction = t.get("direction", "LONG")
        reason = t.get("exit_reason", "?")
        regime = t.get("regime", "?")
        reasoning = (t.get("reasoning") or "")[:150]
        lines.append(f"**{sym} {direction} (${pnl:+,.2f})** — {reason} in {regime} regime. {reasoning}")
        lines.append("")

    # ── Breakdowns
    lines.append("## Breakdown by Exit Reason")
    lines.append("")
    lines.append("| Reason | Count | P&L |")
    lines.append("|--------|-------|-----|")
    for r, v in sorted(a["by_reason"].items(), key=lambda x: x[1]["pnl"]):
        lines.append(f"| {r} | {v['count']} | ${v['pnl']:+,.2f} |")
    lines.append("")

    lines.append("## Breakdown by Regime")
    lines.append("")
    lines.append("| Regime | Count | Wins | P&L |")
    lines.append("|--------|-------|------|-----|")
    for r, v in sorted(a["by_regime"].items(), key=lambda x: x[1]["pnl"]):
        lines.append(f"| {r} | {v['count']} | {v['wins']} | ${v['pnl']:+,.2f} |")
    lines.append("")

    lines.append("## Breakdown by Direction")
    lines.append("")
    lines.append("| Direction | Count | Win Rate | P&L |")
    lines.append("|-----------|-------|----------|-----|")
    for d, v in sorted(a["by_direction"].items()):
        wr = round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0
        lines.append(f"| {d} | {v['count']} | {wr}% | ${v['pnl']:+,.2f} |")
    lines.append("")

    # ── Patterns & Alerts
    if patterns:
        lines.append("## Patterns & Alerts")
        lines.append("")
        for p in patterns:
            icon = {"CRITICAL": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}.get(p["type"], "•")
            lines.append(f"### {icon} {p['type']}: {p['name']}")
            lines.append("")
            lines.append(p["detail"])
            lines.append("")
            lines.append(f"**Action:** {p['action']}")
            lines.append("")

    # ── Cumulative
    if cumulative_analysis and cumulative_analysis.get("total", 0) > 0:
        c = cumulative_analysis
        lines.append("## Cumulative Performance (All Days)")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total Trades | {c['total']} |")
        lines.append(f"| Win Rate | {c['win_rate']}% |")
        lines.append(f"| Total P&L | ${c['total_pnl']:+,.2f} |")
        lines.append(f"| Profit Factor | {c['profit_factor']} |")
        lines.append(f"| Avg Daily P&L | ${c.get('avg_daily_pnl', 0):+,.2f} |")
        lines.append("")

    # ── Tomorrow's Focus
    lines.append("## Tomorrow's Focus")
    lines.append("")
    focus_items = []
    if any(p["type"] == "CRITICAL" for p in patterns):
        focus_items.append("**Fix the root cause** — the bias roadmap features (especially #01 direction-agnostic signals and #02 short scanner) are not nice-to-haves. Every day without them is more money lost to the same structural bug.")
    if a["total"] > 12:
        focus_items.append("**Trade less** — raise consensus threshold to 3/6 immediately (roadmap #08, 5-minute change). Quality over quantity.")
    if a.get("by_regime", {}).get("UNKNOWN", {}).get("count", 0) > 3:
        focus_items.append("**Never enter blind** — fix regime detection so UNKNOWN regime blocks new entries instead of letting them through.")
    if not focus_items:
        focus_items.append("Continue monitoring. System performing within parameters.")
    for item in focus_items:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("---")
    lines.append(f"*Generated: {datetime.now(_ET).strftime('%Y-%m-%d %H:%M:%S')} by daily_journal.py*")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────

def generate_journal(target_date=None, day_number=None):
    """Generate journal for a specific date. Defaults to today."""
    if target_date is None:
        target_date = datetime.now(_ET).strftime("%Y-%m-%d")

    trades = load_json(TRADE_FILE)
    orders = load_json(ORDER_FILE)

    # Get today's trades and orders
    day_trades = get_day_trades(trades, target_date)
    day_orders = get_day_orders(orders, target_date)

    if not day_trades:
        print(f"No trades found for {target_date}.")
        return None

    # Analyse today
    today_analysis = analyse_trades(day_trades)

    # Analyse cumulative (all trades up to and including today)
    all_up_to = [t for t in trades
                 if (t.get("entry_time") or t.get("timestamp", ""))[:10] <= target_date
                 and t.get("pnl") is not None]
    cumulative = analyse_trades(all_up_to)

    # Calculate avg daily P&L for cumulative
    if cumulative.get("total", 0) > 0:
        days_seen = set()
        for t in all_up_to:
            d = (t.get("entry_time") or t.get("timestamp", ""))[:10]
            if d:
                days_seen.add(d)
        num_days = len(days_seen) or 1
        cumulative["avg_daily_pnl"] = round(cumulative["total_pnl"] / num_days, 2)

    # Identify patterns
    patterns = identify_patterns(today_analysis, cumulative)

    # Render
    md = render_journal(target_date, today_analysis, cumulative, patterns, day_orders,
                        day_number or "?")

    # Save
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    filepath = os.path.join(JOURNAL_DIR, f"{target_date}.md")
    with open(filepath, "w") as f:
        f.write(md)

    print(f"Journal saved: {filepath}")
    return filepath


def generate_all_journals():
    """Generate journals for every trading day found in the data."""
    trades = load_json(TRADE_FILE)
    if not trades:
        print("No trades found.")
        return

    # Find all unique trading days
    days = set()
    for t in trades:
        for key in ["entry_time", "exit_time", "timestamp"]:
            val = t.get(key, "")
            if val and len(val) >= 10:
                days.add(val[:10])

    trading_days = sorted(days)
    print(f"Found {len(trading_days)} trading days: {', '.join(trading_days)}")
    print()

    for i, day in enumerate(trading_days, 1):
        generate_journal(day, day_number=i)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        generate_all_journals()
    elif len(sys.argv) > 1:
        generate_journal(sys.argv[1])
    else:
        generate_journal()
