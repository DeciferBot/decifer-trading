# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  schemas.py                                 ║
# ║   Minimal schema validators for the 4 most-read JSON files.  ║
# ║   Each validator raises ValueError with a clear message if   ║
# ║   a required field is missing or the wrong type.             ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Usage pattern at every JSON read site:

    for record in raw_list:
        try:
            schemas.validate_catalyst_record(record)
        except ValueError as e:
            log.warning("[module][fn] skipping bad record: %s", e)
            continue
        # use record safely

Only the fields that cause actual failures (KeyError, wrong type,
silent wrong result) when missing are listed as required. Optional
enrichment fields are not validated here.
"""

from __future__ import annotations


def _check(record: dict, required: list[tuple[str, type | tuple]], context: str) -> None:
    """
    Raise ValueError if any required field is missing or the wrong type.

    required is a list of (field_name, expected_type_or_types).
    context is a short string identifying the schema (e.g. "catalyst record").
    """
    for field, expected in required:
        if field not in record:
            raise ValueError(f"{context}: missing required field '{field}'")
        value = record[field]
        if not isinstance(value, expected):
            actual = type(value).__name__
            if isinstance(expected, tuple):
                exp_name = " or ".join(t.__name__ for t in expected)
            else:
                exp_name = expected.__name__
            raise ValueError(
                f"{context}: field '{field}' must be {exp_name}, got {actual} ({value!r:.40})"
            )


# ── Catalyst record ────────────────────────────────────────────────────────────
# Written by: signals/catalyst_screen.py
# Read by:    signals/__init__._get_catalyst_lookup(), bot_dashboard._get_catalyst_payload()
# Failure if missing: lookup returns wrong tickers; dashboard shows wrong candidates

_CATALYST_REQUIRED = [
    ("ticker",         str),
    ("catalyst_score", (int, float)),
]


def validate_catalyst_record(record: dict) -> None:
    """
    Validate a single candidate record from candidates_YYYY-MM-DD.json.
    Raises ValueError if 'ticker' or 'catalyst_score' is missing or wrong type.
    """
    _check(record, _CATALYST_REQUIRED, "catalyst record")


# ── Position record ────────────────────────────────────────────────────────────
# Written by: orders_core.py (entry), orders_portfolio.py (updates)
# Read by:    trade_store.restore(), orders_core (SL/TP/exit decisions)
# Failure if missing: KeyError in order execution; wrong P&L; SL/TP ignored

_POSITION_REQUIRED = [
    ("symbol",     str),
    ("instrument", str),           # trade_store.persist filters on "instrument" in v
    ("entry",      (int, float)),  # P&L base; KeyError in orders_core
    ("qty",        (int, float)),  # position size; KeyError in sizing logic
    ("status",     str),           # PENDING/ACTIVE gate; KeyError in reconcile
    ("direction",  str),           # LONG/SHORT; KeyError in exit logic
]


def validate_position(record: dict) -> None:
    """
    Validate a position record from data/positions.json.
    Raises ValueError if any load-bearing field is missing or wrong type.
    """
    _check(record, _POSITION_REQUIRED, "position record")


# ── Closed trade record ────────────────────────────────────────────────────────
# Written by: learning.log_trade_close()
# Read by:    ic_calculator (IC computation), learning (stats)
# Failure if missing: IC dimension gets no data; wrong win-rate stats

_TRADE_REQUIRED = [
    ("symbol",    str),
    ("score",     (int, float)),  # IC numerator; missing → dimension IC = 0 silently
    ("direction", str),           # LONG/SHORT; IC segmentation
    ("pnl",       (int, float)),  # IC denominator; missing → IC NaN
]


def validate_trade(record: dict) -> None:
    """
    Validate a closed trade record from data/trades.json.
    Raises ValueError if any IC-required field is missing or wrong type.
    """
    _check(record, _TRADE_REQUIRED, "trade record")


# ── Signal log record ──────────────────────────────────────────────────────────
# Written by: bot_trading._log_signal()
# Read by:    ic_calculator (forward-return matching), learning (signal stats)
# Failure if missing: IC forward return unmatched; signal lost from analysis

_SIGNAL_REQUIRED = [
    ("symbol",          str),
    ("score",           (int, float)),
    ("ts",              str),              # ISO timestamp; missing → unmatched in IC
    ("score_breakdown", dict),             # per-dimension scores; missing → IC gets no weights
]


def validate_signal(record: dict) -> None:
    """
    Validate a signal log record from data/signals_log.jsonl.
    Raises ValueError if any IC-required field is missing or wrong type.
    """
    _check(record, _SIGNAL_REQUIRED, "signal record")
