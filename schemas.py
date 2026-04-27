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

from typing import TYPE_CHECKING, Any


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


# ── Apex decision validation ───────────────────────────────────────────────────
# These functions are called in two stages:
#   1. validate_apex_decision_schema()  — structural only; called inside apex_call()
#   2. validate_apex_decision_semantic() — payload-aware; called by filter_semantic_violations()
#
# Keep both stages separate: market_intelligence.py has no access to the candidates payload.

_VALID_TRADE_TYPES = {"INTRADAY", "SWING", "POSITION", "AVOID"}
_VALID_DIRECTIONS  = {"LONG", "SHORT"}
_VALID_CONVICTIONS = {"MEDIUM", "HIGH"}
_VALID_INSTRUMENTS = {"stock", "call", "put"}
_VALID_ACTIONS     = {"HOLD", "TRIM", "EXIT"}
_VALID_TRIM_PCTS   = {25, 50, 75}


def validate_apex_decision_schema(decision: dict[str, Any]) -> None:
    """
    Structural validation of an ApexDecision dict.
    Raises ValueError with a specific message on any violation.
    Does NOT require payload/candidate context.
    """
    for entry in decision.get("new_entries", []):
        sym = entry.get("symbol")
        if not isinstance(sym, str) or not sym:
            raise ValueError(f"new_entry: missing or null symbol field (got {sym!r})")
        tt = entry.get("trade_type")
        if tt not in _VALID_TRADE_TYPES:
            raise ValueError(f"new_entry {sym}: invalid trade_type {tt!r}")

        if tt == "AVOID":
            for nullable_field in ("direction", "conviction", "instrument",
                                   "direction_flipped", "counter_argument", "key_risk"):
                if entry.get(nullable_field) is not None:
                    raise ValueError(
                        f"new_entry {sym}: AVOID entry must have {nullable_field}=null"
                    )
            if not entry.get("rationale"):
                raise ValueError(f"new_entry {sym}: AVOID entry requires non-empty rationale")
        else:
            direction = entry.get("direction")
            if direction not in _VALID_DIRECTIONS:
                raise ValueError(f"new_entry {sym}: invalid direction {direction!r}")
            conviction = entry.get("conviction")
            if conviction not in _VALID_CONVICTIONS:
                raise ValueError(f"new_entry {sym}: invalid conviction {conviction!r}")
            instrument = entry.get("instrument")
            if instrument not in _VALID_INSTRUMENTS:
                raise ValueError(f"new_entry {sym}: invalid instrument {instrument!r}")
            if entry.get("direction_flipped") and not entry.get("rationale"):
                raise ValueError(
                    f"new_entry {sym}: direction_flipped=true requires non-empty rationale"
                )

    for action in decision.get("portfolio_actions", []):
        sym = action.get("symbol", "<unknown>")
        act = action.get("action")
        if act == "ADD":
            raise ValueError(
                f"portfolio_action {sym}: ADD is not valid in v1 — Track B is HOLD/TRIM/EXIT only"
            )
        if act not in _VALID_ACTIONS:
            raise ValueError(f"portfolio_action {sym}: invalid action {act!r}")
        if act == "TRIM":
            pct = action.get("trim_pct")
            if pct not in _VALID_TRIM_PCTS:
                raise ValueError(
                    f"portfolio_action {sym}: TRIM requires trim_pct in {{25, 50, 75}}, got {pct!r}"
                )


def validate_apex_decision_semantic(
    decision: dict[str, Any],
    payloads_by_symbol: dict[str, dict],
) -> None:
    """
    Payload-aware semantic validation of an ApexDecision.
    Raises ValueError for the first violation found.
    Requires candidates_by_symbol (ScannerPayload dicts with allowed_trade_types/options_eligible).
    """
    for entry in decision.get("new_entries", []):
        sym = entry.get("symbol", "<unknown>")
        if entry.get("trade_type") == "AVOID":
            continue
        payload = payloads_by_symbol.get(sym)
        if payload is None:
            continue  # symbol not in candidates — schema pass, semantic skip
        allowed = payload.get("allowed_trade_types", [])
        if allowed and entry.get("trade_type") not in allowed:
            raise ValueError(
                f"new_entry {sym}: trade_type {entry.get('trade_type')!r} "
                f"not in allowed_trade_types {allowed}"
            )
        if entry.get("instrument") in ("call", "put") and not payload.get("options_eligible", False):
            raise ValueError(
                f"new_entry {sym}: instrument {entry.get('instrument')!r} chosen "
                f"but options_eligible=False for this symbol"
            )
