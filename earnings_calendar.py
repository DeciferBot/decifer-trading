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
  1. FMP earning_calendar (1 call covers all symbols, cached — paid tier, best quality)
  2. Alpha Vantage EARNINGS_CALENDAR (1 call covers all symbols, cached 4 hours)
  3. yfinance calendar (per-symbol fallback — fragile but covers stragglers)

Both callers previously had independent yfinance calendar implementations.
This module owns that logic in one place.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

log = logging.getLogger("decifer.earnings_calendar")


def get_earnings_within_hours(symbols: list[str], hours: int = 48) -> set[str]:
    """
    Return the subset of symbols with earnings scheduled within `hours` hours.
    Returns empty set on any failure — non-blocking, called infrequently.

    Tries FMP first (paid, best quality), then Alpha Vantage (cached, 1 call
    covers all symbols), then falls back to yfinance for any stragglers.
    """
    flagged: set[str] = set()
    if not symbols:
        return flagged

    now_utc = datetime.now(UTC)
    cutoff = now_utc + timedelta(hours=hours)
    covered: set[str] = set()

    # ── Source 1: FMP earning_calendar (paid tier, best quality, 1 call) ─────────
    try:
        import fmp_client as _fmp

        fmp_entries = _fmp.get_earnings_calendar(symbols=symbols, days_ahead=int(hours / 24) + 2)
        for entry in fmp_entries:
            sym = entry.get("symbol", "").upper()
            date_str = entry.get("date", "")
            if not sym or not date_str:
                continue
            covered.add(sym)
            try:
                report_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                if now_utc <= report_dt <= cutoff:
                    flagged.add(sym)
            except ValueError:
                pass
    except Exception as exc:
        log.debug("earnings_calendar: FMP source failed: %s", exc)

    # ── Source 2: Alpha Vantage EARNINGS_CALENDAR (cached, zero-cost after first fetch) ──
    remaining_av = [s for s in symbols if s not in covered]
    if remaining_av:
        try:
            from alpha_vantage_client import get_earnings_calendar

            av_calendar = get_earnings_calendar()
            if av_calendar:
                for sym in remaining_av:
                    report_str = av_calendar.get(sym.upper())
                    if report_str:
                        covered.add(sym)
                        try:
                            report_dt = datetime.strptime(report_str, "%Y-%m-%d").replace(tzinfo=UTC)
                            if now_utc <= report_dt <= cutoff:
                                flagged.add(sym)
                        except ValueError:
                            pass
        except Exception as exc:
            log.debug("earnings_calendar: AV source failed: %s", exc)

    # ── Source 3: yfinance fallback for symbols not in FMP or AV calendar ───────
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
                                    val = val.replace(tzinfo=UTC)
                                if now_utc <= val <= cutoff:
                                    flagged.add(sym)
            except Exception:
                continue
    except Exception as exc:
        log.debug("earnings_calendar.get_earnings_within_hours yfinance fallback failed: %s", exc)

    return flagged


def get_earnings_days(symbol: str) -> int | None:
    """
    Return days until next earnings for a single symbol, or None.
    Returns None on any failure or if earnings are > 60 days out.

    Tries FMP first (paid, best quality), then Alpha Vantage (cached calendar),
    then falls back to yfinance.
    """
    from datetime import date as _date

    # ── Source 1: FMP earning_calendar (paid tier, best quality) ──────────────
    try:
        import fmp_client as _fmp

        fmp_entries = _fmp.get_earnings_calendar(symbols=[symbol], days_ahead=62)
        if fmp_entries:
            date_str = fmp_entries[0].get("date", "")
            if date_str:
                report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                days = (report_date - _date.today()).days
                if 0 <= days <= 60:
                    return int(days)
                return None
    except Exception as exc:
        log.debug("earnings_calendar.get_earnings_days FMP source failed for %s: %s", symbol, exc)

    # ── Source 2: Alpha Vantage EARNINGS_CALENDAR (cached) ────────────────────
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

    # ── Source 3: yfinance fallback ────────────────────────────────────────────
    try:
        import pandas as pd
        import yfinance as yf

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
