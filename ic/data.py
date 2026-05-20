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
    Load all records from the most recent `window` unique trading DATES that
    have a fully-populated score_breakdown (all 9 dimensions present).

    `window` is a date count, not a record count.  Each scan cycle scores
    ~1,000 symbols, so counting records instead of dates would mean window=60
    covers a single partial scan rather than 60 trading days.

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
                    # Skip records explicitly marked ineligible (new-format records only).
                    # Old records without ic_eligible pass through unchanged (None is not False).
                    if rec.get("ic_eligible") is False:
                        continue
                    # Skip records with explicit UNKNOWN direction (new-format records).
                    # Old records missing direction are still included; _dir_sign() defaults them to LONG.
                    if rec.get("direction") == "UNKNOWN":
                        continue
                    bd = rec.get("score_breakdown", {})
                    # Accept records with at least the 9 core dimensions.
                    # Newer dimensions (iv_skew, pead, short_squeeze) are
                    # backfilled with 0 for records that predate their addition.
                    if not (bd and all(d in bd for d in _CORE_DIMENSIONS)):
                        continue
                    for d in DIMENSIONS:
                        bd.setdefault(d, 0)
                    ts_str = rec.get("ts", "")
                    if not ts_str:
                        continue
                    try:
                        scan_date = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date()
                    except Exception:
                        continue
                    if min_age_days > 0 and (today - scan_date).days < min_age_days:
                        continue
                    rec["_scan_date"] = str(scan_date)
                    records.append(rec)
                except Exception:
                    continue
    except Exception as e:
        log.warning("_load_signal_records: read error %s: %s", path, e)
        return []

    # Select the most recent `window` unique trading dates, then return ALL
    # records from those dates.  This keeps IC estimates stable across the
    # full universe scored each day rather than a single-scan slice.
    all_dates = sorted({r["_scan_date"] for r in records})
    if len(all_dates) <= window:
        return records
    cutoff_date = all_dates[-window]
    return [r for r in records if r["_scan_date"] >= cutoff_date]


def _dir_sign(rec: dict) -> int:
    """Return -1 for SHORT records, +1 for everything else (LONG, NEUTRAL, missing).

    Converts raw price returns into direction-adjusted returns so that a SHORT
    candidate whose price fell (negative raw return) contributes positively to IC,
    matching the convention that a correct directional call is a positive outcome.
    """
    return -1 if rec.get("direction") == "SHORT" else 1


def _fetch_forward_returns_batch(records: list) -> dict:
    """
    Fetch N-trading-day forward returns for every (symbol, scan_date) pair.

    Data source: Alpaca SIP daily bars (split-adjusted) — same feed as live scoring.
    All unique symbols are fetched in one batch request to minimise API calls.

    Records with a pre-computed ``fwd_return`` field (historical replay) are
    unpacked directly without any network call.

    Records missing the ``direction`` field default to LONG in ``_dir_sign()``.
    These are early-cycle records that predate direction tracking; they are not
    excluded because the majority were LONG candidates and the directional IC
    bias introduced by this default is minor.  The count is logged at INFO level.

    Returns a dict mapping record index → forward_return (float) or None.
    None means the record is too recent, price data was unavailable for that
    symbol, or scan_price was zero/invalid.
    """
    import pandas as pd

    fwd_horizon: int = int(_ic_cfg("forward_horizon_days", 1))
    min_age_cal: int = fwd_horizon + 1
    fwd_offset_cal: int = fwd_horizon + 2
    today = datetime.now(UTC).date()

    # ── Fast path: pre-computed fwd_return (historical replay) ───────────────
    result: dict[int, float | None] = {}
    needs_fetch: list[int] = []
    for idx, rec in enumerate(records):
        if rec.get("fwd_return") is not None:
            try:
                result[idx] = _dir_sign(rec) * float(rec["fwd_return"])
            except (TypeError, ValueError):
                result[idx] = None
        else:
            needs_fetch.append(idx)

    if not needs_fetch:
        return result

    # ── Log missing-direction records (informational, not an error) ──────────
    missing_dir = sum(1 for i in needs_fetch if not records[i].get("direction"))
    if missing_dir:
        log.info(
            "_fetch_forward_returns: %d/%d records lack 'direction' — defaulting "
            "to LONG (early-cycle data predating direction tracking)",
            missing_dir,
            len(needs_fetch),
        )

    # ── Group by symbol and collect the global timestamp range ───────────────
    by_symbol: dict[str, list[int]] = {}
    all_ts: list[datetime] = []
    for i in needs_fetch:
        rec = records[i]
        sym = rec.get("symbol")
        if not sym:
            result[i] = None
            continue
        by_symbol.setdefault(sym, []).append(i)
        ts_str = rec.get("ts", "")
        try:
            all_ts.append(datetime.fromisoformat(ts_str.replace("Z", "+00:00")))
        except Exception:
            pass

    if not all_ts:
        for idx in range(len(records)):
            result.setdefault(idx, None)
        return result

    # ── Build fetch window with explicit past-date guarantee ─────────────────
    # end date is always ≤ yesterday — no future prices can leak into IC computation
    earliest_dt = min(all_ts) - timedelta(days=1)
    latest_dt = max(all_ts) + timedelta(days=15)

    def _to_utc(dt: datetime) -> datetime:
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

    fetch_start = _to_utc(earliest_dt).replace(hour=0, minute=0, second=0, microsecond=0)
    fetch_end_raw = _to_utc(latest_dt).replace(hour=23, minute=59, second=59, microsecond=0)
    yesterday_eod = datetime.combine(today - timedelta(days=1), datetime.max.time()).replace(tzinfo=UTC)
    fetch_end = min(fetch_end_raw, yesterday_eod)

    assert fetch_end.date() < today, (
        f"_fetch_forward_returns: fetch_end={fetch_end.date()} is not in the past — "
        "lookahead bias guard triggered"
    )

    fetch_start_str = str(fetch_start.date())
    fetch_end_str = str(fetch_end.date())

    # ── Alpaca batch fetch ────────────────────────────────────────────────────
    bars_by_symbol: dict[str, "pd.Series"] = {}
    fetch_errors: list[str] = []

    try:
        from alpaca_data import _get_client
        client = _get_client()
    except ImportError:
        client = None

    if client is None:
        log.warning(
            "_fetch_forward_returns: Alpaca client unavailable — "
            "forward returns cannot be computed (provider=alpaca)"
        )
        for idx in range(len(records)):
            result.setdefault(idx, None)
        return result

    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError as exc:
        log.warning("_fetch_forward_returns: alpaca-py not installed — %s", exc)
        for idx in range(len(records)):
            result.setdefault(idx, None)
        return result

    all_symbols = list(by_symbol.keys())
    _CHUNK_SIZE = 500  # well within Alpaca SIP batch limits
    for chunk_start in range(0, len(all_symbols), _CHUNK_SIZE):
        chunk = all_symbols[chunk_start: chunk_start + _CHUNK_SIZE]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=chunk,
                timeframe=TimeFrame.Day,
                start=fetch_start,
                end=fetch_end,
                feed="sip",
                adjustment="split",
            )
            bars = client.get_stock_bars(req)
            df = bars.df
            if df is None or df.empty:
                fetch_errors.extend(chunk)
                continue
            if isinstance(df.index, pd.MultiIndex):
                for sym in chunk:
                    try:
                        sym_df = df.xs(sym, level=0)
                        close_col = "close" if "close" in sym_df.columns else "Close"
                        if not sym_df.empty and close_col in sym_df.columns:
                            bars_by_symbol[sym] = sym_df[close_col].dropna()
                        else:
                            fetch_errors.append(sym)
                    except KeyError:
                        fetch_errors.append(sym)
            elif len(chunk) == 1:
                sym_df = df
                close_col = "close" if "close" in sym_df.columns else "Close"
                if close_col in sym_df.columns:
                    bars_by_symbol[chunk[0]] = sym_df[close_col].dropna()
        except Exception as e:
            log.debug("_fetch_forward_returns: chunk[%s…] failed — %s", chunk[0], e)
            fetch_errors.extend(chunk)

    # ── Compute forward returns per record ────────────────────────────────────
    returns_found = 0
    returns_missing = 0

    for sym, idxs in by_symbol.items():
        close_series = bars_by_symbol.get(sym)
        if close_series is None or close_series.empty:
            for i in idxs:
                result[i] = None
            returns_missing += len(idxs)
            continue

        # Normalise index to tz-aware UTC for consistent .date comparison
        if hasattr(close_series.index, "tz") and close_series.index.tz is None:
            close_series.index = close_series.index.tz_localize(UTC)

        for i in idxs:
            rec = records[i]
            scan_price = rec.get("price")
            ts_str = rec.get("ts", "")
            try:
                scan_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                scan_date = scan_dt.date()
                if (today - scan_date).days < min_age_cal:
                    result[i] = None
                    returns_missing += 1
                    continue
                future_date = scan_date + timedelta(days=fwd_offset_cal)
                idx_dates = close_series.index.date
                future_candidates = close_series[idx_dates >= future_date]
                if future_candidates.empty:
                    result[i] = None
                    returns_missing += 1
                    continue
                future_price = float(future_candidates.iloc[0])
                sp = float(scan_price) if scan_price else 0.0
                if sp <= 0 or future_price <= 0:
                    result[i] = None
                    returns_missing += 1
                    continue
                result[i] = _dir_sign(rec) * (future_price - sp) / sp
                returns_found += 1
            except Exception as e:
                log.debug("_fetch_forward_returns idx=%d %s: %s", i, sym, e)
                result[i] = None
                returns_missing += 1

    pre_computed = len(records) - len(needs_fetch)
    log.info(
        "_fetch_forward_returns: provider=alpaca requested=%d "
        "found=%d (pre_computed=%d live=%d) missing=%d "
        "n_missing_symbols=%d start=%s end=%s",
        len(records),
        pre_computed + returns_found,
        pre_computed,
        returns_found,
        returns_missing,
        len(fetch_errors),
        fetch_start_str,
        fetch_end_str,
    )
    if fetch_errors:
        log.debug(
            "_fetch_forward_returns: symbols with no data: %s%s",
            fetch_errors[:10],
            f" … (+{len(fetch_errors) - 10} more)" if len(fetch_errors) > 10 else "",
        )

    for idx in range(len(records)):
        result.setdefault(idx, None)

    return result


def count_independent_dates(records: list[dict]) -> int:
    """
    Count unique trading dates in a set of signal records.

    Each record's ``ts`` field (ISO-8601) is truncated to YYYY-MM-DD.
    This is used to assess IC statistical reliability: the effective
    sample size for time-series significance testing is the number of
    independent cross-section dates, not the total number of records.
    """
    dates: set = set()
    for rec in records:
        ts = rec.get("ts", "")
        if ts and isinstance(ts, str) and len(ts) >= 10:
            dates.add(ts[:10])
    return len(dates)
