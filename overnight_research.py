# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  overnight_research.py                     ║
# ║   Generates data/overnight_notes.md after market close.     ║
# ║   Opus reads this file at the start of each trading day     ║
# ║   before making any trade decisions.                         ║
# ║                                                              ║
# ║   Sources:                                                   ║
# ║     • Alpaca — pre-market / after-hours price gaps          ║
# ║     • data/trades.json — yesterday's performance summary    ║
# ║     • FMP — economic calendar + earnings with estimates     ║
# ║     • FMP — analyst upgrades/downgrades (last 24h)          ║
# ║     • macro_calendar — FOMC/CPI/NFP 5-day window            ║
# ║                                                              ║
# ║   Run automatically at ~4:15pm ET via bot_trading.py.       ║
# ║   Can also be run standalone: python overnight_research.py  ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import zoneinfo
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("decifer.overnight")

_ET          = zoneinfo.ZoneInfo("America/New_York")
NOTES_PATH   = "data/overnight_notes.md"
TRADES_FILE  = "data/trades.json"
_TONE_SYMS   = ["SPY", "QQQ", "IWM"]   # market breadth proxies


# ── Pre-market / after-hours tone ─────────────────────────────────────────────

def _get_price_tone() -> str:
    """
    Fetch latest price vs previous close for SPY, QQQ, IWM via Alpaca snapshots.
    Returns a formatted string block. Never raises.
    """
    try:
        from alpaca_data import fetch_snapshots
        snaps = fetch_snapshots(_TONE_SYMS)
        if not snaps:
            return "Pre-market tone: unavailable (Alpaca not connected)"

        lines = []
        for sym in _TONE_SYMS:
            s = snaps.get(sym)
            if not s:
                continue
            price  = s.get("price") or 0
            chg    = s.get("change_1d")
            if chg is None:
                lines.append(f"  {sym:<4} ${price:.2f}  (gap vs close: n/a)")
            else:
                pct    = chg * 100
                tag    = "gap-up" if pct > 0.15 else ("gap-down" if pct < -0.15 else "flat")
                lines.append(f"  {sym:<4} ${price:.2f}  {pct:+.2f}% vs close  ({tag})")

        return "PRE-MARKET / AFTER-HOURS TONE:\n" + "\n".join(lines) if lines \
               else "Pre-market tone: no data returned"
    except Exception as exc:
        log.debug("overnight: price tone failed — %s", exc)
        return "Pre-market tone: unavailable"


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

        pnls     = [t["pnl"] for t in day_trades]
        wins     = [t for t in day_trades if t["pnl"] > 0]
        losses   = [t for t in day_trades if t["pnl"] <= 0]
        total_pnl = sum(pnls)
        win_rate  = len(wins) / len(day_trades) * 100 if day_trades else 0

        # Best and worst trade
        best  = max(day_trades, key=lambda x: x["pnl"])
        worst = min(day_trades, key=lambda x: x["pnl"])

        # Regime breakdown
        by_regime: dict[str, dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
        for t in day_trades:
            r = t.get("regime", "UNKNOWN")
            by_regime[r]["count"] += 1
            by_regime[r]["pnl"]   += t["pnl"]
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
                f"  Best:  {bw.get('symbol','?')} {bw.get('direction','?')} "
                f"+${bw['pnl']:,.2f}  ({bw.get('exit_reason','?')} / {bw.get('regime','?')})"
            )
        if losses:
            bw = worst
            lines.append(
                f"  Worst: {bw.get('symbol','?')} {bw.get('direction','?')} "
                f"${bw['pnl']:,.2f}  ({bw.get('exit_reason','?')} / {bw.get('regime','?')})"
            )

        # Regime performance (only regimes with >= 2 trades)
        regime_notes = []
        for regime_name, data in sorted(by_regime.items(), key=lambda x: x[1]["pnl"]):
            if data["count"] >= 2:
                wr = data["wins"] / data["count"] * 100
                regime_notes.append(
                    f"    {regime_name}: {data['count']} trades  "
                    f"{wr:.0f}% WR  ${data['pnl']:+,.2f}"
                )
        if regime_notes:
            lines.append("  By regime:")
            lines.extend(regime_notes)

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

def _get_macro_snapshot() -> str:
    """
    Fetch recent values of key macro indicators from FRED.
    Returns a formatted string block. Never raises.
    """
    try:
        from fred_client import get_macro_snapshot, is_available as fred_ok
        if not fred_ok():
            return ""
        items = get_macro_snapshot()
        if not items:
            return ""
        lines = ["MACRO INDICATORS (latest FRED):"]
        for item in items:
            val_str   = f"{item['value']:.2f}{item['unit']}"
            prior_str = f"  prior: {item['prior']:.2f}" if item.get("prior") is not None else ""
            lines.append(f"  {item['name']}: {val_str}  (as of {item['date']}){prior_str}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("overnight: macro snapshot failed — %s", exc)
        return ""


# ── Economic calendar (macro_calendar + FRED primary + FMP fallback) ──────────

def _get_economic_calendar() -> str:
    """
    Build a 5-day economic calendar.
    Layer 1: hardcoded macro_calendar (FOMC/CPI/NFP — always available).
    Layer 2: FRED releases/dates (primary — broader set, requires FRED_API_KEY).
    Layer 3: FMP economic calendar (fallback — only used when FRED is unavailable).
    Returns a formatted string block. Never raises.
    """
    today    = date.today()
    days     = [(today + timedelta(days=i)) for i in range(6)]
    day_strs = {str(d): [] for d in days}

    def _add_event(d_str: str, ev_name: str, impact: str) -> None:
        if d_str not in day_strs:
            return
        already = any(
            ev_name.upper() in e["event"].upper()
            or e["event"].upper() in ev_name.upper()
            for e in day_strs[d_str]
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

    # ── Layer 2: FRED (primary) ───────────────────────────────────
    fred_populated = False
    try:
        from fred_client import get_upcoming_releases, is_available as fred_ok
        if fred_ok():
            fred_events = get_upcoming_releases(days_ahead=5)
            for ev in fred_events:
                _add_event(ev["date"], ev["name"], ev["impact"])
            fred_populated = bool(fred_events)
    except Exception as exc:
        log.debug("overnight: FRED calendar layer failed — %s", exc)

    # ── Layer 3: FMP (fallback — only when FRED unavailable/empty) ────────────
    if not fred_populated:
        try:
            from fmp_client import get_economic_calendar, is_available as fmp_ok
            if fmp_ok():
                fmp_events = get_economic_calendar(days_ahead=5)
                for ev in fmp_events:
                    _add_event(ev["date"], ev["event"], ev["impact"])
                if fmp_events:
                    log.debug("overnight: economic calendar using FMP fallback (%d events)",
                              len(fmp_events))
        except Exception as exc:
            log.debug("overnight: FMP calendar fallback failed — %s", exc)

    lines = ["ECONOMIC CALENDAR — Next 5 Days:"]
    for d in days:
        d_str   = str(d)
        label   = d.strftime("%a %b %-d")
        events  = day_strs.get(d_str, [])
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
        from fmp_client import get_earnings_calendar, is_available as fmp_ok
        if fmp_ok():
            items = get_earnings_calendar(symbols=universe, days_ahead=5)
            if items:
                return _format_earnings(items)
    except Exception as exc:
        log.debug("overnight: FMP earnings failed — %s", exc)

    # ── Source 2: Alpha Vantage fallback (dates, no estimates) ───
    try:
        from alpha_vantage_client import get_earnings_calendar as av_calendar
        av_raw = av_calendar()   # {symbol: "YYYY-MM-DD"}
        if av_raw:
            today   = date.today()
            cutoff  = today + timedelta(days=5)
            sym_set = {s.upper() for s in universe} if universe else None
            items   = []
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
                    items.append({
                        "date":        d_str,
                        "symbol":      sym_up,
                        "timing":      "",
                        "eps_est":     None,
                        "eps_prior":   None,
                    })
            items.sort(key=lambda x: x["date"])
            if items:
                return _format_earnings(items, source="Alpha Vantage — dates only")
    except Exception as exc:
        log.debug("overnight: AV earnings fallback failed — %s", exc)

    return "Earnings calendar: unavailable (no data source connected)"


def _format_earnings(items: list[dict], source: str = "") -> str:
    lines = [f"EARNINGS — Next 5 Days:{f'  ({source})' if source else ''}"]
    for item in items:
        d_str    = item["date"]
        label    = datetime.strptime(d_str, "%Y-%m-%d").strftime("%a %b %-d")
        timing_s = f" {item['timing']}" if item.get("timing") else ""
        sym      = item["symbol"]
        eps_e    = f"EPS est ${item['eps_est']:.2f}" if item.get("eps_est") is not None else ""
        eps_p    = f"prior ${item['eps_prior']:.2f}" if item.get("eps_prior") is not None else ""
        details  = "  ".join(filter(None, [eps_e, eps_p]))
        lines.append(f"  {label}{timing_s}: {sym}  {details}")
    return "\n".join(lines)


# ── Analyst changes ───────────────────────────────────────────────────────────

def _get_analyst_changes(universe: list[str] | None) -> str:
    """
    Fetch analyst upgrades/downgrades in the last 24 hours.
    Filtered to universe if provided. Returns formatted string. Never raises.
    """
    try:
        from fmp_client import get_analyst_changes, is_available as fmp_ok
        if not fmp_ok():
            return "Analyst changes: FMP_API_KEY not set"

        items = get_analyst_changes(symbols=universe, hours_back=24)
        if not items:
            return "Analyst changes: none in last 24h" + \
                   (" for tracked universe" if universe else "")

        lines = ["ANALYST CHANGES (Last 24h):"]
        for item in items[:15]:   # cap at 15 lines
            action    = item["action"].upper()
            from_g    = item["from_grade"]
            to_g      = item["to_grade"]
            firm      = item["firm"]
            sym       = item["symbol"]
            grade_str = f"{from_g} → {to_g}" if from_g and to_g else (to_g or from_g or "")
            lines.append(f"  {sym}  {action}  {grade_str}  ({firm})")

        return "\n".join(lines)

    except Exception as exc:
        log.debug("overnight: analyst changes failed — %s", exc)
        return "Analyst changes: unavailable"


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
    now_et    = datetime.now(_ET)
    date_str  = now_et.strftime("%Y-%m-%d")
    gen_time  = now_et.strftime("%Y-%m-%d %H:%M ET")

    macro_snapshot = _get_macro_snapshot()
    sections = [
        f"OVERNIGHT RESEARCH NOTES — {date_str}",
        f"Generated: {gen_time}",
        "",
        _get_price_tone(),
        "",
        _get_performance_summary(),
        "",
        _get_economic_calendar(),
        "",
        _get_earnings_calendar(universe),
        "",
        _get_analyst_changes(universe),
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
        if _t.time() - mtime > 20 * 3600:
            log.debug("overnight: notes file is stale (> 20h), skipping")
            return ""
        with open(NOTES_PATH) as f:
            return f.read()
    except Exception as exc:
        log.debug("overnight: load failed — %s", exc)
        return ""


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(generate_overnight_notes())
