# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  macro_calendar.py                          ║
# ║   High-impact macro event calendar                           ║
# ║                                                              ║
# ║   Knows when FOMC, CPI, and NFP events land so the risk      ║
# ║   layer can halve position sizing in the 24h window.         ║
# ║                                                              ║
# ║   Dates are hardcoded for the current year and refreshed     ║
# ║   manually each January. All dates are US Eastern time.      ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
from datetime import date, datetime

import pytz

log = logging.getLogger("decifer.macro_calendar")

_EST = pytz.timezone("America/New_York")

# ── High-Impact Macro Event Dates (Eastern Time) ──────────────────────────────
# Sources: Federal Reserve calendar, BLS CPI release schedule, BLS NFP schedule.
# Update each January. Decision day = announcement day (day 2 of FOMC meeting).

_FOMC_2026 = [
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
]

_CPI_2026 = [
    date(2026, 1, 15),
    date(2026, 2, 12),
    date(2026, 3, 12),
    date(2026, 4, 10),
    date(2026, 5, 13),
    date(2026, 6, 11),
    date(2026, 7, 15),
    date(2026, 8, 12),
    date(2026, 9, 10),
    date(2026, 10, 13),
    date(2026, 11, 10),
    date(2026, 12, 10),
]

_NFP_2026 = [
    date(2026, 1, 9),
    date(2026, 2, 6),
    date(2026, 3, 6),
    date(2026, 4, 3),
    date(2026, 5, 1),
    date(2026, 6, 5),
    date(2026, 7, 10),
    date(2026, 8, 7),
    date(2026, 9, 4),
    date(2026, 10, 2),
    date(2026, 11, 6),
    date(2026, 12, 4),
]

_FOMC_2027 = [
    date(2027, 1, 27),
    date(2027, 3, 17),
    date(2027, 4, 28),
    date(2027, 6, 16),
    date(2027, 7, 28),
    date(2027, 9, 15),
    date(2027, 10, 27),
    date(2027, 12, 8),
]

_CPI_2027 = [
    date(2027, 1, 13),
    date(2027, 2, 10),
    date(2027, 3, 10),
    date(2027, 4, 14),
    date(2027, 5, 12),
    date(2027, 6, 9),
    date(2027, 7, 14),
    date(2027, 8, 11),
    date(2027, 9, 8),
    date(2027, 10, 13),
    date(2027, 11, 10),
    date(2027, 12, 8),
]

_NFP_2027 = [
    date(2027, 1, 8),
    date(2027, 2, 5),
    date(2027, 3, 5),
    date(2027, 4, 2),
    date(2027, 5, 7),
    date(2027, 6, 4),
    date(2027, 7, 9),
    date(2027, 8, 6),
    date(2027, 9, 3),
    date(2027, 10, 1),
    date(2027, 11, 5),
    date(2027, 12, 3),
]

# Combined sorted list with event type labels
_ALL_EVENTS: list[dict] = sorted(
    [{"date": d, "type": "FOMC"} for d in _FOMC_2026]
    + [{"date": d, "type": "CPI"} for d in _CPI_2026]
    + [{"date": d, "type": "NFP"} for d in _NFP_2026]
    + [{"date": d, "type": "FOMC"} for d in _FOMC_2027]
    + [{"date": d, "type": "CPI"} for d in _CPI_2027]
    + [{"date": d, "type": "NFP"} for d in _NFP_2027],
    key=lambda x: x["date"],
)


def get_next_event(from_date: date | None = None) -> dict | None:
    """
    Return the next macro event on or after *from_date* (defaults to today ET).
    Returns None if no future events are scheduled.
    """
    if from_date is None:
        from_date = datetime.now(_EST).date()
    for event in _ALL_EVENTS:
        if event["date"] >= from_date:
            return event
    log.warning("macro_calendar: all scheduled events are in the past — update calendar dates for the new year")
    return None


def hours_to_next_event(from_dt: datetime | None = None) -> float | None:
    """
    Hours until the next macro event (float). Returns None if none scheduled.
    Events are treated as firing at 08:30 ET (pre-market CPI/NFP) or 14:00 ET (FOMC).
    """
    if from_dt is None:
        from_dt = datetime.now(_EST)

    today = from_dt.date()
    event = get_next_event(from_date=today)
    if event is None:
        return None

    # CPI and NFP release pre-market at 08:30 ET; FOMC decision at 14:00 ET
    hour = 8 if event["type"] in ("CPI", "NFP") else 14
    event_dt = _EST.localize(datetime(event["date"].year, event["date"].month, event["date"].day, hour, 30))
    if from_dt.tzinfo is None:
        from_dt = _EST.localize(from_dt)

    delta = (event_dt - from_dt).total_seconds() / 3600.0
    return delta


def is_macro_event_within(hours: float = 24.0) -> bool:
    """Return True if a high-impact macro event fires within *hours* hours."""
    h = hours_to_next_event()
    return h is not None and 0 <= h <= hours


def get_macro_size_multiplier() -> float:
    """
    Position-size multiplier based on proximity to a macro event.

    Returns 0.5 if a FOMC, CPI, or NFP event is within 24 hours.
    Returns 1.0 otherwise (no adjustment).

    The 24-hour window is intentionally wide: pre-event drift and post-event
    whipsaw both compress risk-adjusted returns for mean-reversion and
    trend-following strategies alike.
    """
    from config import CONFIG

    mult = CONFIG.get("macro_event_size_mult", 0.5)
    window = CONFIG.get("macro_event_hours_window", 24.0)

    if is_macro_event_within(hours=window):
        event = get_next_event()
        h = hours_to_next_event()
        log.info(
            "MACRO GATE: %s in %.1fh — position size → %.0f%% of normal",
            event["type"] if event else "event",
            h or 0,
            mult * 100,
        )
        return mult
    return 1.0
