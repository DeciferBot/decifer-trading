"""
earnings_calendar.py — Single source of truth for earnings date queries.

Provides:
  get_earnings_within_hours(symbols, hours=48) -> set[str]
    Returns symbols that have earnings scheduled within the given hours window.
    Used by portfolio_manager to flag positions for earnings risk review.

  get_earnings_days(symbol) -> int | None
    Returns days until next earnings for a single symbol, or None.
    Used by options_scanner to score earnings catalyst plays.

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
    """
    flagged: set[str] = set()
    if not symbols:
        return flagged
    try:
        import yfinance as yf
        now_utc = datetime.now(timezone.utc)
        cutoff  = now_utc + timedelta(hours=hours)
        for sym in symbols:
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
        log.debug(f"earnings_calendar.get_earnings_within_hours failed: {exc}")
    return flagged


def get_earnings_days(symbol: str) -> Optional[int]:
    """
    Return days until next earnings for a single symbol, or None.
    Returns None on any failure or if earnings are > 60 days out.
    """
    try:
        import yfinance as yf
        import pandas as pd
        from datetime import date

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

        days = (ed - date.today()).days
        return int(days) if 0 <= days <= 60 else None

    except Exception as exc:
        log.debug(f"earnings_calendar.get_earnings_days({symbol}) failed: {exc}")
        return None
