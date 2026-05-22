"""
pm_outcome_tracker.py — Links PME decisions to future market outcomes.

Reads:  data/pm_engine/decisions.jsonl
Writes: data/pm_engine/outcomes.jsonl  (append-only)

Called from bot_trading.py scan cycle via resolve_pending().
Each decision record produces up to 6 outcome records — one per time window.
Price data is fetched from Alpaca historical bars; unavailable windows remain
pending and are retried on the next call.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib

log = logging.getLogger(__name__)

UTC = datetime.timezone.utc

_DECISIONS_FILE = pathlib.Path("data/pm_engine/decisions.jsonl")
_OUTCOMES_FILE  = pathlib.Path("data/pm_engine/outcomes.jsonl")

# (name, min_elapsed_minutes, lookup_strategy, lookup_param)
# strategy "intraday" → 1-min bars; strategy "daily" → daily bars (param = trading days ahead)
_WINDOWS = [
    ("30min", 30,   "intraday", 30),
    ("1h",    60,   "intraday", 60),
    ("eod",   390,  "daily",    0),   # same-day close (~6.5h after open)
    ("1d",    1440, "daily",    1),
    ("3d",    4320, "daily",    3),
    ("5d",    7200, "daily",    5),
]

_DECLINE = -0.02   # > 2% drop  → significant decline
_RISE    = +0.02   # > 2% rise  → significant rise


# ── Public API ─────────────────────────────────────────────────────────────────

def decision_id(record: dict) -> str:
    """Stable, URL-safe ID derived from symbol + decision timestamp."""
    ts = record.get("ts", "")[:19].replace(":", "").replace("-", "").replace("T", "_")
    return f"{record.get('symbol', 'UNK')}_{ts}"


def resolve_pending(
    decisions_path: pathlib.Path = _DECISIONS_FILE,
    outcomes_path:  pathlib.Path = _OUTCOMES_FILE,
    max_fetches:    int = 20,
) -> int:
    """
    Check all pending outcome windows, resolve any that are now observable,
    and append new outcome records to outcomes_path.

    max_fetches caps Alpaca API calls per invocation so the scan cycle
    does not stall on large backlogs.  Unresolved windows remain pending
    and are retried on the next call.

    Returns the number of new outcome records written.
    """
    decisions = _read_jsonl(decisions_path)
    if not decisions:
        return 0

    resolved = _get_resolved_keys(outcomes_path)
    now      = datetime.datetime.now(UTC)
    new_records: list[dict] = []
    fetches = 0

    for d in decisions:
        if d.get("event") == "PM_SKIPPED":
            continue
        ts_str = d.get("ts", "")
        if not ts_str:
            continue

        did = decision_id(d)
        price_at_decision = d.get("current_price") or d.get("entry_price")

        for w_name, w_elapsed_min, w_strat, w_param in _WINDOWS:
            if (did, w_name) in resolved:
                continue
            if not _elapsed(ts_str, w_elapsed_min, now):
                continue
            if fetches >= max_fetches:
                break

            price_out = _fetch_price(d["symbol"], ts_str, w_strat, w_param)
            fetches += 1
            if price_out is None:
                continue

            new_records.append(
                _build_record(d, did, w_name, price_at_decision, price_out)
            )

    if new_records:
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        with outcomes_path.open("a", encoding="utf-8") as fh:
            for r in new_records:
                fh.write(json.dumps(r, default=str) + "\n")
        log.info("pm_outcome_tracker: %d new outcome records written", len(new_records))

    return len(new_records)


def get_summary(outcomes_path: pathlib.Path = _OUTCOMES_FILE) -> dict:
    """Return aggregate outcome stats for the /api/pm_outcomes dashboard endpoint."""
    records = _read_jsonl(outcomes_path)
    if not records:
        return {
            "total": 0, "by_action": {}, "quality_counts": {}, "recent": [],
        }

    by_action: dict[str, dict] = {}
    quality_counts: dict[str, int] = {}

    for r in records:
        at = r.get("action_type", "UNKNOWN")
        q  = r.get("outcome_quality", "UNKNOWN")
        by_action.setdefault(at, {"GOOD": 0, "BAD": 0, "NEUTRAL": 0})
        by_action[at][q] = by_action[at].get(q, 0) + 1
        quality_counts[q] = quality_counts.get(q, 0) + 1

    return {
        "total":          len(records),
        "by_action":      by_action,
        "quality_counts": quality_counts,
        "recent":         list(reversed(records[-20:])),
    }


# ── Internals ─────────────────────────────────────────────────────────────────

def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def _get_resolved_keys(path: pathlib.Path) -> set[tuple[str, str]]:
    return {
        (r.get("decision_id", ""), r.get("window", ""))
        for r in _read_jsonl(path)
    }


def _elapsed(ts_iso: str, min_elapsed: int, now: datetime.datetime) -> bool:
    try:
        ts = datetime.datetime.fromisoformat(ts_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (now - ts).total_seconds() >= min_elapsed * 60
    except Exception:
        return False


def _fetch_price(symbol: str, ts_iso: str, strategy: str, param: int) -> float | None:
    """Fetch closing price for outcome window via Alpaca. Returns None on any failure."""
    try:
        import pandas as pd
        from alpaca_data import fetch_bars

        ts = pd.Timestamp(ts_iso).tz_convert("UTC")

        if strategy == "intraday":
            df = fetch_bars(symbol, period="3d", interval="1m")
            if df is None or df.empty:
                return None
            target = ts + pd.Timedelta(minutes=param)
            after  = df[df.index >= target]
            return float(after["Close"].iloc[0]) if not after.empty else None

        # daily strategy
        df = fetch_bars(symbol, period="2wk", interval="1d")
        if df is None or df.empty:
            return None
        idx          = df.index.tz_convert("UTC").normalize()
        decision_day = ts.normalize()

        if param == 0:  # eod: same-day close
            same = df[idx == decision_day]
            return float(same["Close"].iloc[-1]) if not same.empty else None

        future = df[idx > decision_day]
        return float(future["Close"].iloc[param - 1]) if len(future) >= param else None

    except Exception:
        return None


def _classify(
    action_type: str, final_status: str, return_pct: float
) -> tuple[str, str]:
    """
    Return (quality, label) for a resolved outcome.

    Exit executed  — we sold: price falling is GOOD (avoided loss), rising is BAD.
    Exit blocked   — rails stopped us: price falling is BAD (rail too strict).
    Hold / Monitor — we kept: price rising is GOOD, falling is BAD.
    Advisory (DCA/ADD) — recommendation: price rising is GOOD.
    """
    is_exit = action_type in {"FULL_EXIT", "TRIM", "ROTATE"}
    is_hold = action_type in {"HOLD", "DO_NOTHING", "MONITORING"}
    is_adv  = action_type in {"DCA", "ADD", "RECOMMENDATION"}

    if is_exit and final_status == "EXECUTED":
        if return_pct < _DECLINE:  return "GOOD",    "caught_decline"
        if return_pct > _RISE:     return "BAD",     "cut_winner_early"
        return "NEUTRAL", "neutral_exit"

    if is_exit and final_status == "SAFETY_BLOCKED":
        if return_pct < _DECLINE:  return "BAD",     "rail_too_strict"
        if return_pct > _RISE:     return "GOOD",    "rail_correct"
        return "NEUTRAL", "rail_neutral"

    if is_hold:
        if return_pct > _RISE:     return "GOOD",    "justified_hold"
        if return_pct < _DECLINE:  return "BAD",     "held_too_long"
        return "NEUTRAL", "neutral_hold"

    if is_adv:
        if return_pct > _RISE:     return "GOOD",    "dca_justified"
        if return_pct < _DECLINE:  return "BAD",     "dca_into_loss"
        return "NEUTRAL", "dca_neutral"

    return "NEUTRAL", "unknown"


def _build_record(
    d: dict,
    did: str,
    window: str,
    price_at_decision: float | None,
    price_at_outcome: float,
) -> dict:
    ret = (
        (price_at_outcome - price_at_decision) / price_at_decision
        if price_at_decision and price_at_decision > 0
        else 0.0
    )
    quality, label = _classify(
        d.get("action_type", ""),
        d.get("final_status", ""),
        ret,
    )
    return {
        "decision_id":              did,
        "ts_decision":              d.get("ts"),
        "ts_resolved":              datetime.datetime.now(UTC).isoformat(),
        "symbol":                   d.get("symbol"),
        "action_type":              d.get("action_type"),
        "final_status":             d.get("final_status"),
        "thesis_status":            d.get("thesis_status"),
        "score_delta":              d.get("score_delta"),
        "score_source":             d.get("score_source"),
        "data_quality":             d.get("data_quality"),
        "market_regime":            d.get("market_regime"),
        "unrealised_pnl_pct":       d.get("unrealised_pnl_pct"),
        "position_pct_nlv":         d.get("position_pct_nlv"),
        "proposed_notional":        d.get("proposed_notional"),
        "candidate_symbol":         d.get("candidate_symbol"),
        "safety_block_reason":      d.get("safety_block_reason"),
        "window":                   window,
        "price_at_decision":        price_at_decision,
        "price_at_outcome":         price_at_outcome,
        "symbol_return_pct":        round(ret, 6),
        "counterfactual_nlv_impact": round(ret * (d.get("position_pct_nlv") or 0.0), 6),
        "outcome_quality":          quality,
        "outcome_label":            label,
    }
