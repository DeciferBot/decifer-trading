# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  overnight_research.py                     ║
# ║   Generates data/overnight_notes.md at 6:00 AM ET.          ║
# ║   Runs pre-market so Opus has real gap data, overnight       ║
# ║   news, and catalyst context before the 9:30 AM open.       ║
# ║                                                              ║
# ║   Sources:                                                   ║
# ║     • Alpaca — pre-market / after-hours price gaps          ║
# ║     • Alpaca News — last 16h headlines (Haiku interpretation)║
# ║     • data/trades.json — yesterday's performance summary    ║
# ║     • FMP — economic calendar + earnings with estimates     ║
# ║     • FMP — analyst upgrades/downgrades (last 24h)          ║
# ║     • FRED — macro snapshot (CPI, rates, spread, crude)     ║
# ║     • macro_calendar — FOMC/CPI/NFP 5-day window            ║
# ║                                                              ║
# ║   Run automatically at 6:00 AM ET via bot_trading.py.       ║
# ║   Can also be run standalone: python overnight_research.py  ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import zoneinfo
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

log = logging.getLogger("decifer.overnight")

_ET = zoneinfo.ZoneInfo("America/New_York")
NOTES_PATH = "data/overnight_notes.md"
TRADES_FILE = "data/trades.json"
UNIVERSE_PATH = "data/committed_universe.json"
_TONE_SYMS = ["SPY", "QQQ", "IWM"]  # market breadth proxies
_SECTOR_ETFS = ["XLF", "XLK", "XLE", "XLV", "XLI", "XLU", "XLB", "XLRE"]  # sector rotation


# ── Pre-market / after-hours tone ─────────────────────────────────────────────


def _get_price_tone() -> str:
    """
    Fetch latest price vs previous close for market breadth proxies and sector ETFs.
    Returns a formatted string block. Never raises.
    """
    try:
        from alpaca_data import fetch_snapshots

        all_syms = _TONE_SYMS + _SECTOR_ETFS
        snaps = fetch_snapshots(all_syms)
        if not snaps:
            return "Pre-market tone: unavailable (Alpaca not connected)"

        def _fmt_sym(sym: str, width: int = 5) -> str:
            s = snaps.get(sym)
            if not s:
                return f"  {sym:<{width}} n/a"
            price = s.get("price") or 0
            chg = s.get("change_1d")
            if chg is None:
                return f"  {sym:<{width}} ${price:.2f}  n/a"
            pct = chg * 100
            tag = "gap-up" if pct > 0.15 else ("gap-down" if pct < -0.15 else "flat")
            return f"  {sym:<{width}} ${price:.2f}  {pct:+.2f}%  ({tag})"

        breadth_lines = [_fmt_sym(s) for s in _TONE_SYMS if s in snaps]
        sector_lines = [_fmt_sym(s) for s in _SECTOR_ETFS if s in snaps]

        out = ["PRE-MARKET / AFTER-HOURS TONE:"]
        if breadth_lines:
            out += breadth_lines
        if sector_lines:
            out += ["  ---  sector ETFs  ---"]
            out += sector_lines
        return "\n".join(out) if len(out) > 1 else "Pre-market tone: no data returned"
    except Exception as exc:
        log.debug("overnight: price tone failed — %s", exc)
        return "Pre-market tone: unavailable"


# ── Pre-market universe movers ────────────────────────────────────────────────


def _get_universe_movers(extra_syms: list[str] | None = None) -> str:
    """
    Fetch pre-market movers from the committed universe.
    Takes top 75 by dollar volume from committed_universe.json plus any extra
    symbols (e.g. open positions). Flags those with abs gap > 1.5%.
    Returns formatted string. Never raises.
    """
    try:
        from alpaca_data import fetch_snapshots

        syms: list[str] = []
        if os.path.exists(UNIVERSE_PATH):
            with open(UNIVERSE_PATH) as f:
                u = json.load(f)
            syms = [entry["symbol"] for entry in (u.get("symbols") or [])[:75]]

        if extra_syms:
            extra_upper = [s.upper() for s in extra_syms]
            existing = set(syms)
            syms += [s for s in extra_upper if s not in existing]

        if not syms:
            return ""

        snaps = fetch_snapshots(syms)
        if not snaps:
            return ""

        movers = []
        for sym, s in snaps.items():
            chg = s.get("change_1d")
            if chg is None:
                continue
            pct = chg * 100
            if abs(pct) >= 1.5:
                movers.append((sym, s.get("price") or 0, pct))

        if not movers:
            return "PRE-MARKET MOVERS:\n  No significant gaps (>1.5%) in top universe"

        movers.sort(key=lambda x: abs(x[2]), reverse=True)
        lines = ["PRE-MARKET MOVERS (>1.5% gap vs yesterday's close):"]
        for sym, price, pct in movers[:15]:
            direction = "gap-up" if pct > 0 else "gap-down"
            lines.append(f"  {sym:<6} ${price:>8.2f}  {pct:+.2f}%  ({direction})")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("overnight: universe movers failed — %s", exc)
        return ""


# ── Yesterday's performance summary ──────────────────────────────────────────


def _get_performance_summary() -> str:
    """
    Read data/trades.json and summarise yesterday's closed trades.
    Returns a formatted string block. Never raises.
    """
    try:
        if not os.path.exists(TRADES_FILE):
            return "Yesterday's performance: no trade data found"

        with open(TRADES_FILE) as f:
            all_trades = json.load(f)

        yesterday = (datetime.now(_ET) - timedelta(days=1)).strftime("%Y-%m-%d")
        # Also include today if this is being generated intraday after close
        today_str = datetime.now(_ET).strftime("%Y-%m-%d")

        day_trades = []
        for t in all_trades:
            for key in ("exit_time", "entry_time", "timestamp"):
                val = (t.get(key) or "")[:10]
                if val in (yesterday, today_str) and t.get("pnl") is not None:
                    day_trades.append(t)
                    break

        if not day_trades:
            return f"Yesterday's performance: no closed trades on {yesterday}"

        pnls = [t["pnl"] for t in day_trades]
        wins = [t for t in day_trades if t["pnl"] > 0]
        losses = [t for t in day_trades if t["pnl"] <= 0]
        total_pnl = sum(pnls)
        win_rate = len(wins) / len(day_trades) * 100 if day_trades else 0

        # Best and worst trade
        best = max(day_trades, key=lambda x: x["pnl"])
        worst = min(day_trades, key=lambda x: x["pnl"])

        # Regime breakdown
        by_regime: dict[str, dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
        for t in day_trades:
            r = t.get("regime", "UNKNOWN")
            by_regime[r]["count"] += 1
            by_regime[r]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                by_regime[r]["wins"] += 1

        lines = [
            "YESTERDAY'S PERFORMANCE:",
            f"  Trades: {len(day_trades)} ({len(wins)}W/{len(losses)}L)  "
            f"Win rate: {win_rate:.0f}%  P&L: ${total_pnl:+,.2f}",
        ]

        if wins:
            bw = best
            lines.append(
                f"  Best:  {bw.get('symbol', '?')} {bw.get('direction', '?')} "
                f"+${bw['pnl']:,.2f}  ({bw.get('exit_reason', '?')} / {bw.get('regime', '?')})"
            )
        if losses:
            bw = worst
            lines.append(
                f"  Worst: {bw.get('symbol', '?')} {bw.get('direction', '?')} "
                f"${bw['pnl']:,.2f}  ({bw.get('exit_reason', '?')} / {bw.get('regime', '?')})"
            )

        # Regime performance (only regimes with >= 2 trades)
        regime_notes = []
        for regime_name, data in sorted(by_regime.items(), key=lambda x: x[1]["pnl"]):
            if data["count"] >= 2:
                wr = data["wins"] / data["count"] * 100
                regime_notes.append(f"    {regime_name}: {data['count']} trades  {wr:.0f}% WR  ${data['pnl']:+,.2f}")
        if regime_notes:
            lines.append("  By regime:")
            lines.extend(regime_notes)

        # Dimension-level P&L (requires signal_scores on trade records)
        dim_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
        for t in day_trades:
            scores = t.get("signal_scores") or {}
            if not scores:
                continue
            # Only credit dimensions that were actually non-zero at entry
            for dim, score in scores.items():
                if score and score != 0:
                    dim_stats[dim]["count"] += 1
                    dim_stats[dim]["pnl"] += t["pnl"]
                    if t["pnl"] > 0:
                        dim_stats[dim]["wins"] += 1

        if dim_stats:
            sorted_dims = sorted(dim_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
            lines.append("  By dimension (non-zero entry scores):")
            for dim, data in sorted_dims:
                if data["count"] < 2:
                    continue
                dwr = data["wins"] / data["count"] * 100
                lines.append(
                    f"    {dim:<16} {data['count']:>2} trades  {dwr:.0f}% WR  ${data['pnl']:+,.0f}"
                )

        # Flags
        if len(day_trades) > 12:
            lines.append("  FLAG: High trade count — overtrading risk")
        if win_rate < 40 and len(day_trades) >= 4:
            lines.append("  FLAG: Win rate < 40% — entry selectivity concern")

        return "\n".join(lines)

    except Exception as exc:
        log.debug("overnight: performance summary failed — %s", exc)
        return "Yesterday's performance: data read error"


# ── Macro snapshot (FRED recent values) ──────────────────────────────────────


_DAILY_MACRO_NAMES = {"10Y-2Y Spread", "10Y Treasury", "WTI Crude"}


def _get_macro_snapshot() -> str:
    """
    Fetch daily-moving macro indicators from FRED (yield spread, crude).
    Monthly series (CPI, unemployment, Fed funds) omitted — too stale for daily use.
    Returns a formatted string block. Never raises.
    """
    try:
        from fred_client import get_macro_snapshot
        from fred_client import is_available as fred_ok

        if not fred_ok():
            return ""
        items = [i for i in get_macro_snapshot() if i.get("name") in _DAILY_MACRO_NAMES]
        if not items:
            return ""
        lines = ["MACRO (daily):"]
        for item in items:
            val_str = f"{item['value']:.2f}{item['unit']}"
            prior_str = f"  prior: {item['prior']:.2f}" if item.get("prior") is not None else ""
            lines.append(f"  {item['name']}: {val_str}  (as of {item['date']}){prior_str}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("overnight: macro snapshot failed — %s", exc)
        return ""


# ── Economic calendar (macro_calendar + FMP primary + FRED fallback) ──────────


def _get_economic_calendar() -> str:
    """
    Build a 5-day economic calendar.
    Layer 1: hardcoded macro_calendar (FOMC/CPI/NFP — always available).
    Layer 2: FMP economic calendar (primary — US events with estimates, High/Medium only).
    Layer 3: FRED releases/dates (fallback — only when FMP unavailable).
    Returns a formatted string block. Never raises.
    """
    today = date.today()
    days = [(today + timedelta(days=i)) for i in range(6)]
    day_strs = {str(d): [] for d in days}

    def _add_event(d_str: str, ev_name: str, impact: str) -> None:
        if d_str not in day_strs:
            return
        already = any(
            ev_name.upper() in e["event"].upper() or e["event"].upper() in ev_name.upper() for e in day_strs[d_str]
        )
        if not already:
            day_strs[d_str].append({"event": ev_name, "impact": impact, "detail": ""})

    # ── Layer 1: hardcoded macro calendar ────────────────────────
    try:
        from macro_calendar import _ALL_EVENTS  # type: ignore[attr-defined]

        for event in _ALL_EVENTS:
            _add_event(str(event["date"]), event["type"], "High")
    except Exception as exc:
        log.debug("overnight: macro_calendar layer failed — %s", exc)

    # ── Layer 2: FMP (primary — US events with estimates) ────────────────────
    fmp_populated = False
    try:
        from fmp_client import get_economic_calendar
        from fmp_client import is_available as fmp_ok

        if fmp_ok():
            fmp_events = get_economic_calendar(days_ahead=5)
            for ev in fmp_events:
                if ev.get("impact", "").lower() in ("high", "medium"):
                    _add_event(ev["date"], ev["event"], ev["impact"])
            fmp_populated = bool(fmp_events)
    except Exception as exc:
        log.debug("overnight: FMP calendar layer failed — %s", exc)

    # ── Layer 3: FRED (fallback — only when FMP unavailable/empty) ───────────
    if not fmp_populated:
        try:
            from fred_client import get_upcoming_releases
            from fred_client import is_available as fred_ok

            if fred_ok():
                fred_events = get_upcoming_releases(days_ahead=5)
                for ev in fred_events:
                    _add_event(ev["date"], ev["name"], ev["impact"])
        except Exception as exc:
            log.debug("overnight: FRED calendar fallback failed — %s", exc)

    lines = ["ECONOMIC CALENDAR — Next 5 Days:"]
    for d in days:
        d_str = str(d)
        label = d.strftime("%a %b %-d")
        events = day_strs.get(d_str, [])
        if not events:
            lines.append(f"  {label}: No high-impact events")
        else:
            for ev in events:
                impact_tag = f"[{ev['impact'].upper()}]" if ev["impact"] else ""
                lines.append(f"  {label}: {ev['event']} {impact_tag}")

    return "\n".join(lines)


# ── Earnings calendar ─────────────────────────────────────────────────────────


def _get_earnings_calendar(universe: list[str] | None) -> str:
    """
    Fetch earnings for the next 5 days.
    Source priority: FMP (with estimates) → Alpha Vantage (dates only).
    Returns formatted string. Never raises.
    """
    # ── Source 1: FMP (has EPS estimates) ────────────────────────
    try:
        from fmp_client import get_earnings_calendar
        from fmp_client import is_available as fmp_ok

        if fmp_ok():
            items = get_earnings_calendar(symbols=universe, days_ahead=5)
            if items:
                return _format_earnings(items)
    except Exception as exc:
        log.debug("overnight: FMP earnings failed — %s", exc)

    # ── Source 2: Alpha Vantage fallback (dates, no estimates) ───
    try:
        from alpha_vantage_client import get_earnings_calendar as av_calendar

        av_raw = av_calendar()  # {symbol: "YYYY-MM-DD"}
        if av_raw:
            today = date.today()
            cutoff = today + timedelta(days=5)
            sym_set = {s.upper() for s in universe} if universe else None
            items = []
            for sym, d_str in av_raw.items():
                sym_up = sym.upper()
                # Filter to exchange-listed equities only:
                # exclude preferred shares (contain "-"), foreign OTC (end in F/Y),
                # and long OTC symbols (>5 chars usually OTC)
                if "-" in sym_up:
                    continue
                if len(sym_up) > 5:
                    continue
                if sym_up.endswith(("F", "Y")) and len(sym_up) >= 5:
                    continue
                if sym_set and sym_up not in sym_set:
                    continue
                try:
                    d = datetime.strptime(d_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if today <= d <= cutoff:
                    items.append(
                        {
                            "date": d_str,
                            "symbol": sym_up,
                            "timing": "",
                            "eps_est": None,
                            "eps_prior": None,
                        }
                    )
            items.sort(key=lambda x: x["date"])
            if items:
                return _format_earnings(items, source="Alpha Vantage — dates only")
    except Exception as exc:
        log.debug("overnight: AV earnings fallback failed — %s", exc)

    return "Earnings calendar: unavailable (no data source connected)"


def _format_earnings(items: list[dict], source: str = "") -> str:
    lines = [f"EARNINGS — Next 5 Days:{f'  ({source})' if source else ''}"]
    for item in items:
        d_str = item["date"]
        label = datetime.strptime(d_str, "%Y-%m-%d").strftime("%a %b %-d")
        timing_s = f" {item['timing']}" if item.get("timing") else ""
        sym = item["symbol"]
        eps_e = f"EPS est ${item['eps_est']:.2f}" if item.get("eps_est") is not None else ""
        eps_p = f"prior ${item['eps_prior']:.2f}" if item.get("eps_prior") is not None else ""
        details = "  ".join(filter(None, [eps_e, eps_p]))
        lines.append(f"  {label}{timing_s}: {sym}  {details}")
    return "\n".join(lines)


# ── Market news (FMP symbol-tagged) ──────────────────────────────────────────


def _get_market_news(universe_syms: list[str] | None = None) -> str:
    """
    Fetch last 16h of symbol-tagged news from FMP for the committed universe.
    Articles are pre-sentiment-scored by FMP. No LLM call required.
    Falls back to empty string if FMP unavailable. Never raises.
    """
    try:
        from fmp_client import get_fmp_news_articles
        from fmp_client import is_available as fmp_ok

        if not fmp_ok():
            return "Market news: FMP not available"

        # Build symbol list: top 50 from committed universe + any open positions
        syms: list[str] = []
        if os.path.exists(UNIVERSE_PATH):
            with open(UNIVERSE_PATH) as f:
                u = json.load(f)
            syms = [entry["symbol"] for entry in (u.get("symbols") or [])[:50]]
        if universe_syms:
            existing = set(syms)
            syms += [s.upper() for s in universe_syms if s.upper() not in existing]

        if not syms:
            return "Market news: no universe symbols available"

        articles = get_fmp_news_articles(syms, limit=60)
        # Filter to last 16h
        recent = [a for a in articles if a.get("age_hours", 999) <= 16]
        if not recent:
            return "Market news: no FMP articles in last 16h"

        # Sort: BULLISH/BEARISH first (non-neutral), then by recency
        recent.sort(key=lambda a: (a["sentiment"] == "NEUTRAL", a.get("age_hours", 999)))

        bull_arts = [a for a in recent if a["sentiment"] == "BULLISH"]
        bear_arts = [a for a in recent if a["sentiment"] == "BEARISH"]
        neutral_arts = [a for a in recent if a["sentiment"] == "NEUTRAL"]
        bull, bear, neutral = len(bull_arts), len(bear_arts), len(neutral_arts)

        if bull > bear * 1.3:
            net_bias = "bullish"
        elif bear > bull * 1.3:
            net_bias = "bearish"
        else:
            net_bias = "neutral"

        lines = [f"MARKET NEWS — Last 16h (FMP, {len(recent)} articles):"]
        for a in recent[:12]:
            sym_tag = f"[{a['symbols'][0]}]" if a.get("symbols") else "[MACRO]"
            sent = a["sentiment"]
            sent_tag = "▲" if sent == "BULLISH" else ("▼" if sent == "BEARISH" else "─")
            age_h = a.get("age_hours", 0)
            age_str = f"{age_h:.0f}h ago" if age_h >= 1 else f"{age_h*60:.0f}m ago"
            lines.append(f"  {sent_tag} {sym_tag:<8} {age_str:<8} {a['headline'][:90]}")

        # ── Symbol-mapped net bias — tells Opus which stocks to act on ──────
        lines.append("")
        lines.append(f"Net bias: {net_bias}  ({bull} bullish / {bear} bearish / {neutral} neutral)")

        # Collect top symbols per sentiment (deduplicated, max 5 each)
        def _top_syms(arts: list[dict], n: int = 5) -> list[tuple[str, str]]:
            seen: set[str] = set()
            out = []
            for a in arts:
                sym = a["symbols"][0] if a.get("symbols") else ""
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                # Extract a short reason from the headline (first clause before comma/semicolon)
                headline = a.get("headline", "")
                reason = headline.split(";")[0].split(",")[0][:60].strip()
                out.append((sym, reason))
                if len(out) >= n:
                    break
            return out

        favor = _top_syms(bull_arts)
        avoid = _top_syms(bear_arts)

        if avoid:
            lines.append("  AVOID (bearish catalyst):")
            for sym, reason in avoid:
                lines.append(f"    {sym:<6} — {reason}")
        if favor:
            lines.append("  FAVOR (bullish catalyst):")
            for sym, reason in favor:
                lines.append(f"    {sym:<6} — {reason}")

        return "\n".join(lines)

    except Exception as exc:
        log.debug("overnight: FMP news failed — %s", exc)
        return "Market news: unavailable"


# ── Analyst changes ───────────────────────────────────────────────────────────


def _get_analyst_changes(universe: list[str] | None) -> str:
    """
    Fetch analyst upgrades/downgrades in the last 24 hours.
    Filtered to universe if provided. Returns formatted string. Never raises.
    """
    try:
        from fmp_client import get_analyst_changes
        from fmp_client import is_available as fmp_ok

        if not fmp_ok():
            return "Analyst changes: FMP_API_KEY not set"

        items = get_analyst_changes(symbols=universe, hours_back=24)
        if not items:
            return "Analyst changes: none in last 24h" + (" for tracked universe" if universe else "")

        lines = ["ANALYST CHANGES (Last 24h):"]
        for item in items[:15]:  # cap at 15 lines
            action = item["action"].upper()
            from_g = item["from_grade"]
            to_g = item["to_grade"]
            firm = item["firm"]
            sym = item["symbol"]
            grade_str = f"{from_g} → {to_g}" if from_g and to_g else (to_g or from_g or "")
            lines.append(f"  {sym}  {action}  {grade_str}  ({firm})")

        return "\n".join(lines)

    except Exception as exc:
        log.debug("overnight: analyst changes failed — %s", exc)
        return "Analyst changes: unavailable"


JSON_PATH = "data/overnight_notes.json"


# ── Structured JSON builder ───────────────────────────────────────────────────


def _build_overnight_json(universe: list[str] | None, gen_time: str) -> dict:
    """
    Build structured JSON for the dashboard.  All data sources are already
    cached by Alpaca/FMP clients so re-calling them here is zero-cost.
    """
    data: dict = {"generated": gen_time, "available": True}

    # ── Market tone ───────────────────────────────────────────────
    try:
        from alpaca_data import fetch_snapshots

        snaps = fetch_snapshots(_TONE_SYMS + _SECTOR_ETFS)

        def _snap_entry(sym: str) -> dict | None:
            s = snaps.get(sym)
            if not s:
                return None
            price = s.get("price") or 0
            chg = s.get("change_1d")
            pct = round(chg * 100, 2) if chg is not None else None
            tag = "up" if (pct or 0) > 0.15 else ("down" if (pct or 0) < -0.15 else "flat")
            return {"sym": sym, "price": round(price, 2), "pct": pct, "tag": tag}

        data["market_tone"] = [e for s in _TONE_SYMS if (e := _snap_entry(s))]
        data["sector_tone"] = [e for s in _SECTOR_ETFS if (e := _snap_entry(s))]
    except Exception:
        data["market_tone"] = []
        data["sector_tone"] = []

    # ── Pre-market movers ─────────────────────────────────────────
    try:
        from alpaca_data import fetch_snapshots as _fs

        syms: list[str] = []
        if os.path.exists(UNIVERSE_PATH):
            with open(UNIVERSE_PATH) as f:
                u = json.load(f)
            syms = [entry["symbol"] for entry in (u.get("symbols") or [])[:75]]
        if universe:
            existing = set(syms)
            syms += [s.upper() for s in universe if s.upper() not in existing]

        snaps = _fs(syms) if syms else {}
        movers = []
        for sym, s in snaps.items():
            chg = s.get("change_1d")
            if chg is None:
                continue
            pct = round(chg * 100, 2)
            if abs(pct) >= 1.5:
                movers.append({"sym": sym, "price": round(s.get("price") or 0, 2), "pct": pct,
                                "tag": "up" if pct > 0 else "down"})
        movers.sort(key=lambda x: abs(x["pct"]), reverse=True)
        data["movers"] = movers[:15]
    except Exception:
        data["movers"] = []

    # ── Yesterday's performance ───────────────────────────────────
    try:
        perf: dict = {}
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE) as f:
                all_trades = json.load(f)
            yesterday = (datetime.now(_ET) - timedelta(days=1)).strftime("%Y-%m-%d")
            today_str = datetime.now(_ET).strftime("%Y-%m-%d")
            day_trades = []
            for t in all_trades:
                for key in ("exit_time", "entry_time", "timestamp"):
                    val = (t.get(key) or "")[:10]
                    if val in (yesterday, today_str) and t.get("pnl") is not None:
                        day_trades.append(t)
                        break
            wins = [t for t in day_trades if t["pnl"] > 0]
            by_regime: dict[str, dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
            dim_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
            for t in day_trades:
                r = t.get("regime", "UNKNOWN")
                by_regime[r]["count"] += 1
                by_regime[r]["pnl"] += t["pnl"]
                if t["pnl"] > 0:
                    by_regime[r]["wins"] += 1
                for dim, score in (t.get("signal_scores") or {}).items():
                    if score:
                        dim_stats[dim]["count"] += 1
                        dim_stats[dim]["pnl"] += t["pnl"]
                        if t["pnl"] > 0:
                            dim_stats[dim]["wins"] += 1

            best = max(day_trades, key=lambda x: x["pnl"]) if day_trades else None
            worst = min(day_trades, key=lambda x: x["pnl"]) if day_trades else None
            perf = {
                "trades": len(day_trades),
                "wins": len(wins),
                "losses": len(day_trades) - len(wins),
                "win_rate": round(len(wins) / len(day_trades) * 100) if day_trades else 0,
                "pnl": round(sum(t["pnl"] for t in day_trades), 2),
                "best": {"sym": best.get("symbol"), "pnl": round(best["pnl"], 2),
                         "exit_reason": best.get("exit_reason", "")} if best else None,
                "worst": {"sym": worst.get("symbol"), "pnl": round(worst["pnl"], 2),
                          "exit_reason": worst.get("exit_reason", "")} if worst else None,
                "by_regime": [
                    {"regime": r, "count": d["count"],
                     "win_rate": round(d["wins"] / d["count"] * 100),
                     "pnl": round(d["pnl"], 2)}
                    for r, d in sorted(by_regime.items(), key=lambda x: x[1]["pnl"], reverse=True)
                    if d["count"] >= 2
                ],
                "by_dimension": sorted(
                    [{"dim": dim, "count": d["count"],
                      "win_rate": round(d["wins"] / d["count"] * 100) if d["count"] else 0,
                      "pnl": round(d["pnl"], 2)}
                     for dim, d in dim_stats.items() if d["count"] >= 2],
                    key=lambda x: x["pnl"], reverse=True
                ),
                "flags": (
                    (["High trade count — overtrading risk"] if len(day_trades) > 12 else []) +
                    (["Win rate < 40% — entry selectivity concern"]
                     if (len(wins) / len(day_trades) * 100 < 40 if day_trades else False) else [])
                ),
            }
        data["performance"] = perf
    except Exception:
        data["performance"] = {}

    # ── News structured data ──────────────────────────────────────
    try:
        from fmp_client import get_fmp_news_articles
        from fmp_client import is_available as fmp_ok

        news_data: dict = {"count": 0, "net_bias": "neutral", "articles": [], "avoid": [], "favor": []}
        if fmp_ok():
            syms_for_news: list[str] = []
            if os.path.exists(UNIVERSE_PATH):
                with open(UNIVERSE_PATH) as f:
                    u = json.load(f)
                syms_for_news = [e["symbol"] for e in (u.get("symbols") or [])[:50]]
            if universe:
                existing = set(syms_for_news)
                syms_for_news += [s.upper() for s in universe if s.upper() not in existing]

            if syms_for_news:
                articles = get_fmp_news_articles(syms_for_news, limit=60)
                recent = [a for a in articles if a.get("age_hours", 999) <= 16]
                bull = [a for a in recent if a["sentiment"] == "BULLISH"]
                bear = [a for a in recent if a["sentiment"] == "BEARISH"]
                net = "bullish" if len(bull) > len(bear) * 1.3 else (
                    "bearish" if len(bear) > len(bull) * 1.3 else "neutral")

                def _top(arts: list[dict], n: int = 5) -> list[dict]:
                    seen: set[str] = set()
                    out = []
                    for a in arts:
                        sym = a["symbols"][0] if a.get("symbols") else ""
                        if not sym or sym in seen:
                            continue
                        seen.add(sym)
                        reason = a.get("headline", "").split(";")[0].split(",")[0][:60].strip()
                        out.append({"sym": sym, "reason": reason})
                        if len(out) >= n:
                            break
                    return out

                news_data = {
                    "count": len(recent),
                    "net_bias": net,
                    "bull_count": len(bull),
                    "bear_count": len(bear),
                    "neutral_count": len(recent) - len(bull) - len(bear),
                    "articles": [
                        {"sym": a["symbols"][0] if a.get("symbols") else "",
                         "sentiment": a["sentiment"],
                         "age_hours": a.get("age_hours", 0),
                         "headline": a.get("headline", "")[:120]}
                        for a in recent[:12]
                    ],
                    "avoid": _top(bear),
                    "favor": _top(bull),
                }
        data["news"] = news_data
    except Exception:
        data["news"] = {"count": 0, "net_bias": "neutral", "articles": [], "avoid": [], "favor": []}

    # ── Macro ─────────────────────────────────────────────────────
    try:
        from fred_client import get_macro_snapshot
        from fred_client import is_available as fred_ok

        macro = []
        if fred_ok():
            macro = [
                {"name": i["name"], "value": i["value"], "unit": i["unit"],
                 "date": i["date"], "prior": i.get("prior")}
                for i in get_macro_snapshot()
                if i.get("name") in _DAILY_MACRO_NAMES
            ]
        data["macro"] = macro
    except Exception:
        data["macro"] = []

    # ── Economic calendar ─────────────────────────────────────────
    try:
        from fmp_client import get_economic_calendar
        from fmp_client import is_available as fmp_ok

        cal_events = []
        if fmp_ok():
            for ev in get_economic_calendar(days_ahead=5):
                if ev.get("impact", "").lower() in ("high", "medium"):
                    cal_events.append({
                        "date": ev["date"],
                        "event": ev["event"],
                        "impact": ev["impact"],
                        "estimate": ev.get("estimate"),
                        "previous": ev.get("previous"),
                    })
        data["calendar"] = cal_events
    except Exception:
        data["calendar"] = []

    # ── Earnings ──────────────────────────────────────────────────
    try:
        from fmp_client import get_earnings_calendar
        from fmp_client import is_available as fmp_ok

        earnings = []
        if fmp_ok():
            for item in get_earnings_calendar(symbols=universe, days_ahead=5):
                earnings.append({
                    "date": item["date"],
                    "symbol": item["symbol"],
                    "timing": item.get("timing", ""),
                    "eps_est": item.get("eps_est"),
                    "eps_prior": item.get("eps_prior"),
                })
        data["earnings"] = earnings
    except Exception:
        data["earnings"] = []

    # ── Analyst changes ───────────────────────────────────────────
    try:
        from fmp_client import get_analyst_changes
        from fmp_client import is_available as fmp_ok

        changes = []
        if fmp_ok():
            for item in get_analyst_changes(symbols=universe, hours_back=24)[:15]:
                changes.append({
                    "symbol": item["symbol"],
                    "action": item["action"].upper(),
                    "from_grade": item.get("from_grade", ""),
                    "to_grade": item.get("to_grade", ""),
                    "firm": item.get("firm", ""),
                })
        data["analyst_changes"] = changes
    except Exception:
        data["analyst_changes"] = []

    return data


# ── Main generator ────────────────────────────────────────────────────────────


def generate_overnight_notes(universe: list[str] | None = None) -> str:
    """
    Generate overnight research notes and write to data/overnight_notes.md.

    Args:
        universe: optional list of symbols from the scan universe.
                  Used to filter earnings and analyst changes to relevant symbols.

    Returns:
        The full notes text (also written to disk).
    """
    now_et = datetime.now(_ET)
    date_str = now_et.strftime("%Y-%m-%d")
    gen_time = now_et.strftime("%Y-%m-%d %H:%M ET")

    macro_snapshot = _get_macro_snapshot()
    universe_movers = _get_universe_movers(extra_syms=universe)
    sections = [
        f"OVERNIGHT RESEARCH NOTES — {date_str}",
        f"Generated: {gen_time}",
        "",
        _get_price_tone(),
    ]
    if universe_movers:
        sections += ["", universe_movers]
    sections += [
        "",
        _get_performance_summary(),
        "",
        _get_economic_calendar(),
        "",
        _get_earnings_calendar(universe),
        "",
        _get_analyst_changes(universe),
        "",
        _get_market_news(universe_syms=universe),
    ]
    if macro_snapshot:
        sections += ["", macro_snapshot]

    text = "\n".join(sections)

    os.makedirs("data", exist_ok=True)
    try:
        with open(NOTES_PATH, "w") as f:
            f.write(text)
        log.info("overnight: notes written to %s", NOTES_PATH)
    except Exception as exc:
        log.warning("overnight: could not write notes file — %s", exc)

    try:
        structured = _build_overnight_json(universe, gen_time)
        with open(JSON_PATH, "w") as f:
            json.dump(structured, f)
        log.info("overnight: structured JSON written to %s", JSON_PATH)
    except Exception as exc:
        log.warning("overnight: could not write JSON file — %s", exc)

    return text


def load_overnight_notes() -> str:
    """
    Load the most recently generated overnight notes.
    Returns empty string if file doesn't exist or is stale (> 20 hours old).
    """
    if not os.path.exists(NOTES_PATH):
        return ""
    try:
        mtime = os.path.getmtime(NOTES_PATH)
        import time as _t

        # Weekend: Monday (0) or Sunday (6) — notes may be up to 80h old (Fri 4pm → Mon 8am)
        weekday = datetime.now(_ET).weekday()
        max_age = 80 * 3600 if weekday in (0, 6) else 20 * 3600
        if _t.time() - mtime > max_age:
            log.debug("overnight: notes file is stale (> %dh), skipping", max_age // 3600)
            return ""
        with open(NOTES_PATH) as f:
            return f.read()
    except Exception as exc:
        log.debug("overnight: load failed — %s", exc)
        return ""


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Load .env for standalone runs (bot sets env vars at startup)
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except ImportError:
        _env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(_env_path):
            with open(_env_path) as _env_f:
                for _line in _env_f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _, _v = _line.partition("=")
                        # Force-set: shell may export empty stubs; .env values are authoritative
                        os.environ[_k.strip()] = _v.strip()
    print(generate_overnight_notes())
