"""
earnings_calendar.py — Single source of truth for earnings date queries.

Provides:
  get_earnings_within_hours(symbols, hours=48) -> set[str]
    Returns symbols that have earnings scheduled within the given hours window.
    Used by portfolio_manager to flag positions for earnings risk review.

  get_earnings_days(symbol) -> int | None
    Returns days until next earnings for a single symbol, or None.
    Used by options_scanner to score earnings catalyst plays.

Data source priority:
  1. Alpha Vantage EARNINGS_CALENDAR (1 call covers all symbols, cached 4 hours)
  2. yfinance calendar (per-symbol fallback — fragile but covers stragglers)

Both callers previously had independent yfinance calendar implementations.
This module owns that logic in one place.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("decifer.earnings_calendar")


def get_earnings_within_hours(symbols: list[str], hours: int = 48) -> set[str]:
    """
    Return the subset of symbols with earnings scheduled within `hours` hours.
    Returns empty set on any failure — non-blocking, called infrequently.

    Tries Alpha Vantage first (cached, 1 call covers all symbols), then falls
    back to yfinance for any symbols not found in the AV calendar.
    """
    flagged: set[str] = set()
    if not symbols:
        return flagged

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc + timedelta(hours=hours)
    covered: set[str] = set()

    # ── Source 1: Alpha Vantage EARNINGS_CALENDAR (cached, zero-cost after first fetch) ──
    try:
        from alpha_vantage_client import get_earnings_calendar
        av_calendar = get_earnings_calendar()
        if av_calendar:
            for sym in symbols:
                report_str = av_calendar.get(sym.upper())
                if report_str:
                    covered.add(sym)
                    try:
                        report_dt = datetime.strptime(report_str, "%Y-%m-%d").replace(
                            tzinfo=timezone.utc
                        )
                        if now_utc <= report_dt <= cutoff:
                            flagged.add(sym)
                    except ValueError:
                        pass
    except Exception as exc:
        log.debug("earnings_calendar: AV source failed: %s", exc)

    # ── Source 2: yfinance fallback for symbols not in AV calendar ─────────────
    remaining = [s for s in symbols if s not in covered]
    if not remaining:
        return flagged

    try:
        import yfinance as yf
        for sym in remaining:
            try:
                cal = yf.Ticker(sym).calendar
                if cal is None or cal.empty:
                    continue
                for col in cal.columns:
                    if "earnings" in col.lower():
                        for val in cal[col].dropna():
                            if hasattr(val, "to_pydatetime"):
                                val = val.to_pydatetime()
                            if isinstance(val, datetime):
                                if val.tzinfo is None:
                                    val = val.replace(tzinfo=timezone.utc)
                                if now_utc <= val <= cutoff:
                                    flagged.add(sym)
            except Exception:
                continue
    except Exception as exc:
        log.debug("earnings_calendar.get_earnings_within_hours yfinance fallback failed: %s", exc)

    return flagged


def get_earnings_days(symbol: str) -> Optional[int]:
    """
    Return days until next earnings for a single symbol, or None.
    Returns None on any failure or if earnings are > 60 days out.

    Tries Alpha Vantage first (cached calendar), then falls back to yfinance.
    """
    from datetime import date as _date

    # ── Source 1: Alpha Vantage EARNINGS_CALENDAR (cached) ────────────────────
    try:
        from alpha_vantage_client import get_earnings_calendar
        av_calendar = get_earnings_calendar()
        if av_calendar:
            report_str = av_calendar.get(symbol.upper())
            if report_str:
                report_date = datetime.strptime(report_str, "%Y-%m-%d").date()
                days = (report_date - _date.today()).days
                if 0 <= days <= 60:
                    return int(days)
                return None  # Found in AV but outside window
    except Exception as exc:
        log.debug("earnings_calendar.get_earnings_days AV source failed for %s: %s", symbol, exc)

    # ── Source 2: yfinance fallback ────────────────────────────────────────────
    try:
        import yfinance as yf
        import pandas as pd

        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return None

        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.columns:
                ed = cal["Earnings Date"].iloc[0]
            elif "Earnings Date" in cal.index:
                ed = cal.T["Earnings Date"].iloc[0]

        if ed is None:
            return None

        if isinstance(ed, (list, pd.Series)):
            ed = ed[0] if len(ed) > 0 else None
        if ed is None:
            return None

        if hasattr(ed, "date"):
            ed = ed.date()
        elif isinstance(ed, str):
            ed = datetime.strptime(ed[:10], "%Y-%m-%d").date()

        days = (ed - _date.today()).days
        return int(days) if 0 <= days <= 60 else None

    except Exception as exc:
        log.debug("earnings_calendar.get_earnings_days(%s) yfinance fallback failed: %s", symbol, exc)
        return None
