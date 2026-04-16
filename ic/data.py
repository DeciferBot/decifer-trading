# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/data.py                                ║
# ║   Signal-log loading and forward-return fetching.           ║
# ║   Bridges raw on-disk signal records to the numerical       ║
# ║   arrays consumed by core IC computation.                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
from datetime import UTC, datetime, timedelta

import schemas

from ic.constants import (
    DIMENSIONS,
    ROLLING_WINDOW,
    SIGNALS_LOG_FILE,
    _CORE_DIMENSIONS,
    _ic_cfg,
    log,
)


def _load_signal_records(
    signals_log_path: str | None = None,
    window: int = ROLLING_WINDOW,
    min_age_days: int = 0,
) -> list[dict]:
    """
    Load the most recent `window` records that have a fully-populated
    score_breakdown (all 9 dimensions present).

    If *min_age_days* > 0, only records at least that many calendar days old
    are included.  This ensures forward-return data can actually be fetched
    before a record enters the IC computation window.

    Returns an empty list if the file is missing or unreadable (logged at WARNING).
    Bad individual records are skipped and logged at WARNING level.
    """
    path = signals_log_path or SIGNALS_LOG_FILE
    if not os.path.exists(path):
        return []
    records = []
    today = datetime.now(UTC).date()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    try:
                        schemas.validate_signal(rec)
                    except ValueError as _ve:
                        log.warning("_load_signal_records: skipping bad signal record: %s", _ve)
                        continue
                    bd = rec.get("score_breakdown", {})
                    # Accept records with at least the 9 core dimensions.
                    # Newer dimensions (iv_skew, pead, short_squeeze) are
                    # backfilled with 0 for records that predate their addition.
                    if not (bd and all(d in bd for d in _CORE_DIMENSIONS)):
                        continue
                    for d in DIMENSIONS:
                        bd.setdefault(d, 0)
                    if min_age_days > 0:
                        ts_str = rec.get("ts", "")
                        if not ts_str:
                            continue
                        scan_date = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date()
                        if (today - scan_date).days < min_age_days:
                            continue
                    records.append(rec)
                except Exception:
                    continue
    except Exception as e:
        log.warning("_load_signal_records: read error %s: %s", path, e)
        return []
    return records[-window:]


def _dir_sign(rec: dict) -> int:
    """Return -1 for SHORT records, +1 for everything else (LONG, NEUTRAL, missing).

    Converts raw price returns into direction-adjusted returns so that a SHORT
    candidate whose price fell (negative raw return) contributes positively to IC,
    matching the convention that a correct directional call is a positive outcome.
    """
    return -1 if rec.get("direction") == "SHORT" else 1


def _fetch_forward_returns_batch(records: list) -> dict:
    """
    Fetch 5-trading-day forward returns for every (symbol, scan_date) pair.

    Groups records by symbol to minimise yfinance calls.
    Returns a dict mapping record index → forward_return (float) or None.
    """
    import yfinance as yf

    # Group record indices by symbol
    by_symbol: dict[str, list[int]] = {}
    for idx, rec in enumerate(records):
        sym = rec.get("symbol")
        if sym:
            by_symbol.setdefault(sym, []).append(idx)

    result: dict[int, float | None] = {}

    fwd_horizon: int = int(_ic_cfg("forward_horizon_days", 1))
    min_age_cal: int = fwd_horizon + 1
    fwd_offset_cal: int = fwd_horizon + 2

    for sym, idxs in by_symbol.items():
        # Fast path: if every record for this symbol already has a pre-computed
        # fwd_return (e.g. historical replay records), skip the yfinance download
        # entirely and just unpack the values.
        if all(records[i].get("fwd_return") is not None for i in idxs):
            for i in idxs:
                try:
                    raw = float(records[i]["fwd_return"])
                    result[i] = _dir_sign(records[i]) * raw
                except (TypeError, ValueError):
                    result[i] = None
            continue

        # Determine the date range to download
        ts_list = []
        for i in idxs:
            ts_str = records[i].get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ts_list.append(dt)
            except Exception:
                pass

        if not ts_list:
            for i in idxs:
                result[i] = None
            continue

        earliest = min(ts_list) - timedelta(days=1)
        # +15 calendar days covers 5+ trading days from the latest scan
        latest = max(ts_list) + timedelta(days=15)
        start_str = earliest.strftime("%Y-%m-%d")
        end_str = latest.strftime("%Y-%m-%d")

        try:
            df = yf.download(sym, start=start_str, end=end_str, interval="1d", progress=False, auto_adjust=True)
            if df is None or len(df) < 2:
                for i in idxs:
                    result[i] = None
                continue
            # Flatten multi-level columns if present
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            close_series = df["Close"].dropna()
            if len(close_series) < 2:
                for i in idxs:
                    result[i] = None
                continue
        except Exception as e:
            log.debug("_fetch_forward_returns_batch %s: %s", sym, e)
            for i in idxs:
                result[i] = None
            continue

        for i in idxs:
            rec = records[i]
            # Historical replay records embed the forward return directly —
            # skip the yfinance round-trip for these.
            if rec.get("fwd_return") is not None:
                try:
                    raw = float(rec["fwd_return"])
                    result[i] = _dir_sign(rec) * raw
                except (TypeError, ValueError):
                    result[i] = None
                continue
            scan_price = rec.get("price")
            ts_str = rec.get("ts", "")
            try:
                scan_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                scan_date = scan_dt.date()
                if (datetime.now(UTC).date() - scan_date).days < min_age_cal:
                    result[i] = None
                    continue
                # Find the first trading close on or after fwd_offset_cal calendar days
                future_date = scan_date + timedelta(days=fwd_offset_cal)
                future_candidates = close_series[
                    close_series.index.date >= future_date  # type: ignore[operator]
                ]
                if len(future_candidates) == 0:
                    result[i] = None
                    continue
                future_price = float(future_candidates.iloc[0])
                sp = float(scan_price) if scan_price else 0.0
                if sp <= 0 or future_price <= 0:
                    result[i] = None
                    continue
                result[i] = _dir_sign(rec) * (future_price - sp) / sp
            except Exception as e:
                log.debug("forward return idx=%d %s: %s", i, sym, e)
                result[i] = None

    # Fill any missing indices
    for idx in range(len(records)):
        result.setdefault(idx, None)

    return result
