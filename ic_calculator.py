# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic_calculator.py                          ║
# ║   Rolling IC-weighted signal composite                       ║
# ║                                                              ║
# ║   Information Coefficient (IC) = Spearman rank correlation  ║
# ║   between each dimension's Z-score and the N-day forward    ║
# ║   return.  Dimensions with higher recent IC get more weight. ║
# ║                                                              ║
# ║   Weight derivation:                                         ║
# ║     1. Compute Spearman IC per dimension (rolling 60 trades) ║
# ║     2. weight_i = max(IC_i, 0)    — ignore negative IC      ║
# ║     3. Normalise to sum = 1.0                                ║
# ║     4. Fall back to equal weights if all IC <= 0             ║
# ║                                                              ║
# ║   Files written:                                             ║
# ║     data/ic_weights.json          — current weights          ║
# ║     data/ic_weights_history.jsonl — weekly snapshots         ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

log = logging.getLogger("decifer.ic_calculator")

# ── Constants ──────────────────────────────────────────────────────────────────

DIMENSIONS = [
    "trend", "momentum", "squeeze", "flow", "breakout",
    "mtf", "news", "social", "reversion", "iv_skew",
    "pead", "short_squeeze",
]
_N = len(DIMENSIONS)
EQUAL_WEIGHTS: dict = {d: 1.0 / _N for d in DIMENSIONS}

_BASE = os.path.dirname(os.path.abspath(__file__))
IC_WEIGHTS_FILE      = os.path.join(_BASE, "data", "ic_weights.json")
IC_HISTORY_FILE      = os.path.join(_BASE, "data", "ic_weights_history.jsonl")
SIGNALS_LOG_FILE     = os.path.join(_BASE, "data", "signals_log.jsonl")
IC_LIVE_FILE         = os.path.join(_BASE, "data", "ic_weights_live.json")
IC_LIVE_HISTORY_FILE = os.path.join(_BASE, "data", "ic_weights_live_history.jsonl")

ROLLING_WINDOW = 60   # records to use for IC calculation
MIN_VALID      = 20   # minimum records with forward returns before IC is trusted


def _ic_cfg(key: str, default):
    """Read a value from CONFIG['ic_calculator'], falling back to *default*."""
    try:
        from config import CONFIG
        return CONFIG.get("ic_calculator", {}).get(key, default)
    except Exception:
        return default


# ── Helpers ────────────────────────────────────────────────────────────────────

def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """
    Compute Spearman rank correlation.

    Uses scipy.stats.spearmanr when available (handles ties correctly).
    Falls back to a numpy-only approximation that is exact when there are no ties.
    """
    try:
        from scipy.stats import spearmanr
        corr, _ = spearmanr(x, y)
        return float(corr) if np.isfinite(corr) else 0.0
    except ImportError:
        pass
    # numpy fallback — rank via argsort(argsort()), no tie-correction
    n = len(x)
    if n < 3:
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    d  = rx - ry
    denom = n * (n * n - 1)
    return float(1.0 - 6.0 * np.sum(d * d) / denom) if denom > 0 else 0.0


def _zscore_array(arr: np.ndarray) -> np.ndarray:
    """Standardise array to zero mean / unit variance.  Returns zeros if std < 1e-9."""
    std = float(np.std(arr))
    if std < 1e-9:
        return np.zeros_like(arr, dtype=float)
    return (arr - np.mean(arr)) / std


# ── Signal log loading ─────────────────────────────────────────────────────────

def _load_signal_records(
    signals_log_path: str = None,
    window: int = ROLLING_WINDOW,
    min_age_days: int = 0,
) -> list:
    """
    Load the most recent `window` records that have a fully-populated
    score_breakdown (all 9 dimensions present).

    If *min_age_days* > 0, only records at least that many calendar days old
    are included.  This ensures forward-return data can actually be fetched
    before a record enters the IC computation window.
    """
    path = signals_log_path or SIGNALS_LOG_FILE
    if not os.path.exists(path):
        return []
    records = []
    today = datetime.now(timezone.utc).date()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    bd = rec.get("score_breakdown", {})
                    if not (bd and all(d in bd for d in DIMENSIONS)):
                        continue
                    if min_age_days > 0:
                        ts_str = rec.get("ts", "")
                        if not ts_str:
                            continue
                        scan_date = datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        ).date()
                        if (today - scan_date).days < min_age_days:
                            continue
                    records.append(rec)
                except Exception:
                    continue
    except Exception as e:
        log.warning("_load_signal_records: read error %s: %s", path, e)
        return []
    return records[-window:]


# ── Forward-return computation ─────────────────────────────────────────────────

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

    result: dict[int, Optional[float]] = {}

    for sym, idxs in by_symbol.items():
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

        earliest  = min(ts_list) - timedelta(days=1)
        # +15 calendar days covers 5+ trading days from the latest scan
        latest    = max(ts_list) + timedelta(days=15)
        start_str = earliest.strftime("%Y-%m-%d")
        end_str   = latest.strftime("%Y-%m-%d")

        try:
            df = yf.download(sym, start=start_str, end=end_str,
                             interval="1d", progress=False, auto_adjust=True)
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

        fwd_horizon: int = int(_ic_cfg("forward_horizon_days", 1))
        # Calendar-day offsets derived from the configured trading-day horizon:
        #   min_age  = horizon + 1  (at least one extra day for settlement lag)
        #   fwd_offset = horizon + 2  (adds a weekend buffer; e.g. horizon=1 → +3d,
        #                              horizon=5 → +7d which matches the original value)
        min_age_cal: int = fwd_horizon + 1
        fwd_offset_cal: int = fwd_horizon + 2

        for i in idxs:
            rec = records[i]
            # Historical replay records embed the forward return directly —
            # skip the yfinance round-trip for these.
            if rec.get("fwd_return") is not None:
                try:
                    result[i] = float(rec["fwd_return"])
                except (TypeError, ValueError):
                    result[i] = None
                continue
            scan_price = rec.get("price")
            ts_str = rec.get("ts", "")
            try:
                scan_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                scan_date = scan_dt.date()
                if (datetime.now(timezone.utc).date() - scan_date).days < min_age_cal:
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
                result[i] = (future_price - sp) / sp
            except Exception as e:
                log.debug("forward return idx=%d %s: %s", i, sym, e)
                result[i] = None

    # Fill any missing indices
    for idx in range(len(records)):
        result.setdefault(idx, None)

    return result


# ── IC calculation ─────────────────────────────────────────────────────────────

def compute_rolling_ic(
    signals_log_path: str = None,
    window: int = ROLLING_WINDOW,
    min_valid: int = MIN_VALID,
    historical_log_path: str = None,
) -> dict:
    """
    Compute Spearman IC per dimension using the most recent `window` records.

    IC is computed between each dimension's Z-scored values and the configured
    forward return horizon (default: 1 trading day for Phase 1, 5 days for Phase 2+).
    Z-scoring normalises across the heterogeneous 0-10 ranges before the
    correlation, so the resulting IC values are comparable.

    historical_log_path
        Optional path to signals_log_historical.jsonl (produced by backtest_signals.py).
        When provided, historical records are merged with live records.  Historical
        records carry a pre-computed `fwd_return` field so no extra yfinance calls
        are needed.  The effective window grows to accommodate both sources
        (min 500 records when historical data is present).

    Returns
    -------
    dict mapping dimension name → raw IC (float in [-1, 1]) or None if
    insufficient data is available for that dimension.
    """
    fwd_horizon: int = int(_ic_cfg("forward_horizon_days", 1))
    min_age_days: int = fwd_horizon + 1  # records must be old enough to have returns

    # Merge live and historical records when a historical log is provided
    if historical_log_path and os.path.exists(historical_log_path):
        live_records = _load_signal_records(signals_log_path, window, min_age_days=min_age_days)
        # Historical records carry pre-computed fwd_return — load ALL of them,
        # no window cap and no age gate.
        hist_records = _load_signal_records(historical_log_path, window=10_000_000, min_age_days=0)
        # Use all records — no further slicing; the full corpus is the training set
        records = live_records + hist_records
        log.info(
            "compute_rolling_ic: merged %d live + %d historical = %d records",
            len(live_records), len(hist_records), len(records),
        )
    else:
        records = _load_signal_records(signals_log_path, window, min_age_days=min_age_days)

    if len(records) < min_valid:
        log.info(
            "compute_rolling_ic: %d valid records (need %d) — returning None IC",
            len(records), min_valid,
        )
        return {d: None for d in DIMENSIONS}

    fwd_map = _fetch_forward_returns_batch(records)

    # Build paired arrays of (dim_scores, forward_return) for each dimension
    dim_raw:     dict[str, list] = {d: [] for d in DIMENSIONS}
    fwd_returns: list            = []

    for idx, rec in enumerate(records):
        fwd = fwd_map.get(idx)
        if fwd is None or not np.isfinite(fwd):
            continue
        bd = rec.get("score_breakdown", {})
        fwd_returns.append(fwd)
        for d in DIMENSIONS:
            dim_raw[d].append(float(bd.get(d, 0.0)))

    n = len(fwd_returns)
    if n < min_valid:
        log.info(
            "compute_rolling_ic: only %d records have forward returns (need %d) — "
            "returning None IC",
            n, min_valid,
        )
        return {d: None for d in DIMENSIONS}

    fwd_arr = np.array(fwd_returns)
    raw_ic: dict = {}

    for d in DIMENSIONS:
        scores_arr = np.array(dim_raw[d])
        if len(scores_arr) != n:
            raw_ic[d] = None
            continue
        z_scores = _zscore_array(scores_arr)
        # If all scores identical, std=0 → z_scores all 0 → no correlation signal
        if np.all(z_scores == 0.0):
            raw_ic[d] = 0.0
            continue
        try:
            raw_ic[d] = _spearman(z_scores, fwd_arr)
        except Exception:
            raw_ic[d] = 0.0

    log.info(
        "compute_rolling_ic: n=%d  IC=[%s]",
        n,
        ", ".join(f"{d}={raw_ic.get(d, 0):.3f}" for d in DIMENSIONS),
    )
    return raw_ic


# ── Weight normalisation ───────────────────────────────────────────────────────

def normalize_ic_weights(raw_ic: dict) -> tuple:
    """
    Convert raw IC values to normalised dimension weights.

    Rules
    -----
    - Negative IC → weight = 0  (don't invert a negatively-predictive dimension)
    - None or non-finite IC     → treated as 0
    - IC < ic_min_threshold     → weight = 0  (noise floor, default 0.0 = Phase 1)
    - If all weights == 0 after flooring → fall back to equal weights
    - Remaining positives normalised to sum to 1.0
    - HHI cap: if any weight > max_single_weight, clip and renormalize (logged as WARNING)

    Paper learning mode (force_equal_weights=True):
    - Returns equal weights for all dimensions, bypassing IC weighting entirely.
    - Fixes the cold-start trap: dimensions with IC=0 (no trade data yet) would
      otherwise get zero weight, never generate trades, and never build IC.
      Equal weights ensures all dimensions are sampled so IC can converge.

    Returns
    -------
    (weights dict, metadata dict) where metadata contains:
      noise_floor_applied, dimensions_suppressed, hhi_capped
    """
    if _ic_cfg("force_equal_weights", False):
        log.info("normalize_ic_weights: force_equal_weights=True — returning equal weights (paper learning mode)")
        return dict(EQUAL_WEIGHTS), {
            "noise_floor_applied": False,
            "dimensions_suppressed": [],
            "hhi_capped": False,
        }

    ic_min  = _ic_cfg("ic_min_threshold", 0.0)
    hhi_cap = _ic_cfg("max_single_weight", 0.40)

    floored: dict = {}
    suppressed: list = []
    for d in DIMENSIONS:
        ic = raw_ic.get(d)
        if ic is None or not np.isfinite(float(ic)):
            floored[d] = 0.0
        else:
            v = float(ic)
            if v < ic_min:
                floored[d] = 0.0
                if v > 0.0:  # positive but below noise floor — worth tracking
                    suppressed.append(d)
            else:
                floored[d] = max(v, 0.0)

    total = sum(floored.values())
    if total <= 1e-9:
        # All IC non-positive or below noise floor — fall back to equal weights
        return dict(EQUAL_WEIGHTS), {
            "noise_floor_applied": ic_min > 0.0,
            "dimensions_suppressed": suppressed,
            "hhi_capped": False,
        }

    normalized = {d: floored[d] / total for d in DIMENSIONS}

    # HHI concentration cap: no single dimension may exceed max_single_weight.
    # Set over-cap dims to exactly hhi_cap; distribute the remainder
    # proportionally among the under-cap dims (equal split if all are zero).
    hhi_capped = False
    if any(w > hhi_cap for w in normalized.values()):
        hhi_capped = True
        over  = [d for d, w in normalized.items() if w > hhi_cap]
        under = {d: w for d, w in normalized.items() if w <= hhi_cap}
        log.warning(
            "normalize_ic_weights: HHI cap triggered — %s exceeded %.0f%% weight; clipping",
            over, hhi_cap * 100,
        )
        remaining    = 1.0 - len(over) * hhi_cap
        under_total  = sum(under.values())
        capped: dict = {}
        for d in DIMENSIONS:
            if d in over:
                capped[d] = hhi_cap
            elif under_total > 1e-9:
                capped[d] = under[d] / under_total * remaining
            else:
                # All remaining dims have zero IC — split remainder equally
                capped[d] = remaining / max(len(under), 1)
        normalized = capped

    return normalized, {
        "noise_floor_applied": ic_min > 0.0,
        "dimensions_suppressed": suppressed,
        "hhi_capped": hhi_capped,
    }


# ── Cache I/O ──────────────────────────────────────────────────────────────────

def get_current_weights() -> dict:
    """
    Return the current IC weights from the on-disk cache.

    Falls back to equal weights if:
    - The cache file does not exist
    - The file is malformed / missing dimensions
    - The weight vector does not sum to ~1.0
    """
    if not os.path.exists(IC_WEIGHTS_FILE):
        return dict(EQUAL_WEIGHTS)
    try:
        with open(IC_WEIGHTS_FILE) as f:
            data = json.load(f)
        weights = data.get("weights", {})
        if not all(d in weights for d in DIMENSIONS):
            return dict(EQUAL_WEIGHTS)
        total = sum(weights.values())
        if abs(total - 1.0) > 0.05:
            return dict(EQUAL_WEIGHTS)
        return {d: float(weights[d]) for d in DIMENSIONS}
    except Exception as e:
        log.warning("get_current_weights: load failed: %s", e)
        return dict(EQUAL_WEIGHTS)


def update_ic_weights(
    signals_log_path: str = None,
    historical_log_path: str = None,
) -> dict:
    """
    Recompute IC weights, write to cache, append to history log.

    Returns the new normalised weight dict.
    Should be called once per week (Sunday review cycle).

    historical_log_path
        Optional path to signals_log_historical.jsonl generated by
        backtest_signals.py.  When provided, historical records are merged
        with live records for a statistically richer IC estimate.
    """
    raw_ic          = compute_rolling_ic(signals_log_path, historical_log_path=historical_log_path)
    weights, ic_meta = normalize_ic_weights(raw_ic)

    all_none  = all(v is None for v in raw_ic.values())
    all_equal = weights == {d: round(1.0 / _N, 10) for d in DIMENSIONS}

    n_records = len(_load_signal_records(signals_log_path))

    record = {
        "updated":              datetime.now(timezone.utc).isoformat(),
        "raw_ic":               {d: (raw_ic.get(d) if raw_ic.get(d) is not None
                                     else None) for d in DIMENSIONS},
        "weights":              weights,
        "n_records":            n_records,
        "using_equal_weights":  all_none or all_equal,
        "noise_floor_applied":  ic_meta["noise_floor_applied"],
        "dimensions_suppressed": ic_meta["dimensions_suppressed"],
        "hhi_capped":           ic_meta["hhi_capped"],
    }

    os.makedirs(os.path.dirname(IC_WEIGHTS_FILE), exist_ok=True)

    # Atomic write for the current weights file
    import tempfile
    dir_ = os.path.dirname(IC_WEIGHTS_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
        os.replace(tmp, IC_WEIGHTS_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Append-only history
    with open(IC_HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    log.info(
        "IC weights updated (n=%d, equal=%s): %s",
        n_records,
        record["using_equal_weights"],
        ", ".join(f"{d}={weights[d]:.3f}" for d in DIMENSIONS),
    )

    # Auto-disable / auto-enable dimensions based on consecutive IC history
    _check_ic_auto_disable(raw_ic)

    return weights


def get_system_ic_health() -> float:
    """
    Return a single float representing the current predictive health of the
    whole signal engine.

    Computed as the mean of all non-None IC values from the most recent
    ic_weights.json cache entry. Only positive ICs are included (negative IC
    means the dimension is actively misleading — already zeroed in weights,
    and shouldn't inflate the health score).

    Returns
    -------
    float — mean positive IC across active dimensions.
             0.0 if cache is absent, corrupt, or all IC values are negative.
             Typically 0.01–0.08 for a working system; < 0.0 means the system
             has lost edge and the deployment gate should activate.

    Note: reads from the cached ic_weights.json (updated weekly). Does not
    trigger a live recomputation — safe to call every scan cycle.
    """
    if not os.path.exists(IC_WEIGHTS_FILE):
        return 0.0
    try:
        with open(IC_WEIGHTS_FILE) as f:
            data = json.load(f)
        raw_ic = data.get("raw_ic", {})
        if not raw_ic:
            return 0.0
        positive_ics = [
            float(v) for v in raw_ic.values()
            if v is not None and np.isfinite(float(v)) and float(v) > 0.0
        ]
        if not positive_ics:
            return 0.0
        return float(np.mean(positive_ics))
    except Exception as e:
        log.debug("get_system_ic_health: read error: %s", e)
        return 0.0


def get_short_quality_score() -> float:
    """
    Return the predictive IC quality of short-direction signals.

    Reads signals_log.jsonl, filters to records where direction == "SHORT",
    and returns the mean positive raw IC across dimensions for that subset.

    Returns 0.0 when:
    - Fewer than 20 short records exist (insufficient data — Phase 1)
    - No ic_weights.json cache exists
    - Any error occurs (fail-safe: caller interprets 0.0 as "unproven")

    Used by signal_pipeline.py and risk.py to gate / size SHORT entries until
    short-side IC is demonstrated. The proven threshold is 0.03.
    """
    try:
        records = _load_signal_records()
        short_records = [r for r in records if r.get("direction", "").upper() == "SHORT"]
        if len(short_records) < MIN_VALID:
            log.debug(
                "get_short_quality_score: %d short records (need %d) — returning 0.0",
                len(short_records), MIN_VALID,
            )
            return 0.0

        fwd_map = _fetch_forward_returns_batch(short_records)

        dim_raw: dict[str, list] = {d: [] for d in DIMENSIONS}
        fwd_returns: list = []

        for idx, rec in enumerate(short_records):
            fwd = fwd_map.get(idx)
            if fwd is None or not np.isfinite(fwd):
                continue
            # For shorts, a negative return is a win — invert the sign so
            # positive IC means the signal correctly predicted the price drop.
            bd = rec.get("score_breakdown", {})
            fwd_returns.append(-fwd)
            for d in DIMENSIONS:
                dim_raw[d].append(float(bd.get(d, 0.0)))

        n = len(fwd_returns)
        if n < MIN_VALID:
            return 0.0

        fwd_arr = np.array(fwd_returns)
        positive_ics = []
        for d in DIMENSIONS:
            scores_arr = np.array(dim_raw[d])
            if len(scores_arr) != n:
                continue
            z = _zscore_array(scores_arr)
            if np.all(z == 0.0):
                continue
            ic = _spearman(z, fwd_arr)
            if np.isfinite(ic) and ic > 0.0:
                positive_ics.append(ic)

        if not positive_ics:
            return 0.0

        quality = float(np.mean(positive_ics))
        log.debug("get_short_quality_score: n=%d, quality=%.4f", n, quality)
        return quality

    except Exception as e:
        log.debug("get_short_quality_score: error (%s) — returning 0.0", e)
        return 0.0


def update_live_ic(signals_log_path: str = None) -> dict:
    """
    Compute IC from live trades only and write to ic_weights_live.json.

    This is intentionally separate from update_ic_weights() which uses the
    historical corpus. Live IC is a small-sample real-time signal:
      - n < MIN_VALID  → raw_ic all None (not enough data yet)
      - n >= MIN_VALID → real Spearman IC from actual closed trades

    Written to:
      data/ic_weights_live.json          — latest live IC snapshot
      data/ic_weights_live_history.jsonl — append-only history for trend tracking

    Call this on the same weekly cycle as update_ic_weights().
    """
    raw_ic = compute_rolling_ic(signals_log_path)   # live-only (no historical_log_path)
    n_records = len(_load_signal_records(signals_log_path))

    record = {
        "updated":    datetime.now(timezone.utc).isoformat(),
        "raw_ic":     {d: (raw_ic.get(d) if raw_ic.get(d) is not None else None)
                       for d in DIMENSIONS},
        "n_records":  n_records,
        "source":     "live_trades_only",
    }

    os.makedirs(os.path.dirname(IC_LIVE_FILE), exist_ok=True)

    import tempfile
    dir_ = os.path.dirname(IC_LIVE_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
        os.replace(tmp, IC_LIVE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    with open(IC_LIVE_HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    log.info(
        "Live IC updated (n=%d): %s",
        n_records,
        ", ".join(
            f"{d}={raw_ic[d]:.3f}" if raw_ic.get(d) is not None else f"{d}=None"
            for d in DIMENSIONS
        ),
    )
    return record


def check_ic_divergence(divergence_threshold: float = 0.03) -> list:
    """
    Compare live IC vs historical IC per dimension.

    A dimension is flagged when:
      abs(live_ic - historical_ic) >= divergence_threshold
      AND both values are non-None (live has enough data)

    Returns a list of warning dicts, one per flagged dimension:
      { dimension, live_ic, historical_ic, delta, direction }

    direction = "live_better"  — live IC exceeds historical (signal improving)
                "live_worse"   — live IC below historical (signal degrading)
                "sign_flip"    — live and historical have opposite signs (regime shift)

    Logs a WARNING for each flagged dimension. Returns [] if data is
    insufficient or no divergence found.
    """
    if not os.path.exists(IC_LIVE_FILE) or not os.path.exists(IC_WEIGHTS_FILE):
        return []

    try:
        with open(IC_LIVE_FILE) as f:
            live_data = json.load(f)
        with open(IC_WEIGHTS_FILE) as f:
            hist_data = json.load(f)
    except Exception as e:
        log.debug("check_ic_divergence: load error — %s", e)
        return []

    live_ic = live_data.get("raw_ic", {})
    hist_ic = hist_data.get("raw_ic", {})
    live_n  = live_data.get("n_records", 0)

    if live_n < MIN_VALID:
        log.debug(
            "check_ic_divergence: only %d live records (need %d) — skipping",
            live_n, MIN_VALID,
        )
        return []

    warnings = []
    for d in DIMENSIONS:
        l = live_ic.get(d)
        h = hist_ic.get(d)
        if l is None or h is None:
            continue
        if not (np.isfinite(float(l)) and np.isfinite(float(h))):
            continue
        l, h = float(l), float(h)
        delta = l - h
        if abs(delta) < divergence_threshold:
            continue

        if l > 0 and h < 0:
            direction = "sign_flip"
        elif l < 0 and h > 0:
            direction = "sign_flip"
        elif delta > 0:
            direction = "live_better"
        else:
            direction = "live_worse"

        entry = {
            "dimension":    d,
            "live_ic":      round(l, 4),
            "historical_ic": round(h, 4),
            "delta":        round(delta, 4),
            "direction":    direction,
            "live_n":       live_n,
        }
        warnings.append(entry)

        log.warning(
            "IC_DIVERGENCE [%s] live=%.4f hist=%.4f delta=%+.4f (%s) n_live=%d",
            d, l, h, delta, direction, live_n,
        )

    if not warnings:
        log.info(
            "check_ic_divergence: no significant divergence (threshold=%.3f, n_live=%d)",
            divergence_threshold, live_n,
        )

    return warnings


def get_ic_weight_history(last_n: int = 4) -> list:
    """
    Return the last `last_n` weekly IC weight snapshots for trend display.
    Each entry has keys: updated, weights, raw_ic, n_records, using_equal_weights.
    """
    if not os.path.exists(IC_HISTORY_FILE):
        return []
    records = []
    try:
        with open(IC_HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        return []
    return records[-last_n:]


def _check_ic_auto_disable(raw_ic: dict) -> None:
    """
    Auto-disable / auto-enable dimensions based on consecutive IC history.

    Rules:
      - IC < auto_disable_threshold for N consecutive weeks → disable
      - IC > auto_enable_threshold for M consecutive weeks → re-enable
        (only if previously disabled by this function)

    Writes to data/settings_override.json (dimension_flags section only).
    CONFIG._apply_settings_override() merges this at scan time.
    Appends IC_AUTO_DISABLE / IC_AUTO_ENABLE events to data/audit_log.jsonl.
    """
    try:
        from config import CONFIG
        ic_cfg = CONFIG.get("ic_calculator", {})
        disable_thresh  = ic_cfg.get("auto_disable_threshold", -0.02)
        disable_weeks   = ic_cfg.get("auto_disable_weeks",     3)
        enable_thresh   = ic_cfg.get("auto_enable_threshold",  0.01)
        enable_weeks    = ic_cfg.get("auto_enable_weeks",      2)

        # Load IC history (need enough snapshots to check consecutive weeks)
        needed = max(disable_weeks, enable_weeks) + 1
        history = get_ic_weight_history(last_n=needed)
        if len(history) < 2:
            return  # Not enough history yet

        # Load current override state
        override_path = os.path.join(_BASE, "data", "settings_override.json")
        override = {}
        if os.path.exists(override_path):
            try:
                with open(override_path) as f:
                    override = json.load(f)
            except Exception:
                override = {}
        dim_overrides = override.setdefault("dimension_flags", {})

        # Audit log path
        audit_path = os.path.join(_BASE, "data", "audit_log.jsonl")
        now_iso = datetime.now(timezone.utc).isoformat()

        changed = False
        for dim in DIMENSIONS:
            # Gather recent IC values for this dimension
            recent_ics = [h.get("raw_ic", {}).get(dim) for h in history]
            recent_ics = [v for v in recent_ics if v is not None]

            if len(recent_ics) < 2:
                continue

            currently_disabled = dim_overrides.get(dim) is False

            # ── Auto-disable check ─────────────────────────────────────
            if not currently_disabled and len(recent_ics) >= disable_weeks:
                last_n_ics = recent_ics[-disable_weeks:]
                if all(v < disable_thresh for v in last_n_ics):
                    dim_overrides[dim] = False
                    changed = True
                    log.warning(
                        "IC_AUTO_DISABLE: %s (IC %s for %d consecutive weeks)",
                        dim, [round(v, 4) for v in last_n_ics], disable_weeks,
                    )
                    try:
                        with open(audit_path, "a") as f:
                            f.write(json.dumps({
                                "ts": now_iso, "event": "IC_AUTO_DISABLE",
                                "dimension": dim, "ic_history": last_n_ics,
                                "reason": f"{disable_weeks} consecutive weeks IC < {disable_thresh}",
                            }) + "\n")
                    except Exception:
                        pass

            # ── Auto-re-enable check (only dims disabled by this function) ──
            elif currently_disabled and len(recent_ics) >= enable_weeks:
                last_m_ics = recent_ics[-enable_weeks:]
                if all(v > enable_thresh for v in last_m_ics):
                    dim_overrides[dim] = True
                    changed = True
                    log.info(
                        "IC_AUTO_ENABLE: %s (IC %s for %d consecutive weeks)",
                        dim, [round(v, 4) for v in last_m_ics], enable_weeks,
                    )
                    try:
                        with open(audit_path, "a") as f:
                            f.write(json.dumps({
                                "ts": now_iso, "event": "IC_AUTO_ENABLE",
                                "dimension": dim, "ic_history": last_m_ics,
                                "reason": f"{enable_weeks} consecutive weeks IC > {enable_thresh}",
                            }) + "\n")
                    except Exception:
                        pass

        if changed:
            import tempfile
            dir_ = os.path.dirname(override_path)
            os.makedirs(dir_, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(override, f, indent=2)
                os.replace(tmp, override_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    except Exception as e:
        log.debug("_check_ic_auto_disable failed (non-critical): %s", e)
