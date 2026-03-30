# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic_calculator.py                          ║
# ║   Rolling IC-weighted signal composite                       ║
# ║                                                              ║
# ║   Information Coefficient (IC) = Spearman rank correlation  ║
# ║   between each dimension's Z-score and the 5-day forward    ║
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
    "mtf", "news", "social", "reversion",
]
_N = len(DIMENSIONS)
EQUAL_WEIGHTS: dict = {d: 1.0 / _N for d in DIMENSIONS}

_BASE = os.path.dirname(os.path.abspath(__file__))
IC_WEIGHTS_FILE  = os.path.join(_BASE, "data", "ic_weights.json")
IC_HISTORY_FILE  = os.path.join(_BASE, "data", "ic_weights_history.jsonl")
SIGNALS_LOG_FILE = os.path.join(_BASE, "data", "signals_log.jsonl")

ROLLING_WINDOW = 60   # records to use for IC calculation
MIN_VALID      = 20   # minimum records with forward returns before IC is trusted


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

def _load_signal_records(signals_log_path: str = None, window: int = ROLLING_WINDOW) -> list:
    """
    Load the most recent `window` records that have a fully-populated
    score_breakdown (all 9 dimensions present).
    """
    path = signals_log_path or SIGNALS_LOG_FILE
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    bd = rec.get("score_breakdown", {})
                    if bd and all(d in bd for d in DIMENSIONS):
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

        for i in idxs:
            rec = records[i]
            scan_price = rec.get("price")
            ts_str = rec.get("ts", "")
            try:
                scan_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                scan_date = scan_dt.date()
                # Records less than 6 days old cannot have a 5-day forward return yet
                if (datetime.now(timezone.utc).date() - scan_date).days < 6:
                    result[i] = None
                    continue
                # Find the first trading day >= 5 business days after the scan date
                future_date = scan_date + timedelta(days=7)  # ≥ 5 trading days
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
) -> dict:
    """
    Compute Spearman IC per dimension using the most recent `window` records.

    IC is computed between each dimension's Z-scored values and the 5-day
    forward return.  Z-scoring normalises across the heterogeneous 0-10 ranges
    before the correlation, so the resulting IC values are comparable.

    Returns
    -------
    dict mapping dimension name → raw IC (float in [-1, 1]) or None if
    insufficient data is available for that dimension.
    """
    records = _load_signal_records(signals_log_path, window)
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

def normalize_ic_weights(raw_ic: dict) -> dict:
    """
    Convert raw IC values to normalised dimension weights.

    Rules
    -----
    - Negative IC → weight = 0  (don't invert a negatively-predictive dimension)
    - None or non-finite IC     → treated as 0
    - If all weights == 0 after flooring → fall back to equal weights
    - Remaining positives normalised to sum to 1.0
    """
    floored: dict = {}
    for d in DIMENSIONS:
        ic = raw_ic.get(d)
        if ic is None or not np.isfinite(float(ic)):
            floored[d] = 0.0
        else:
            floored[d] = max(float(ic), 0.0)

    total = sum(floored.values())
    if total <= 1e-9:
        # All IC non-positive — fall back to equal weights
        return dict(EQUAL_WEIGHTS)

    return {d: floored[d] / total for d in DIMENSIONS}


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


def update_ic_weights(signals_log_path: str = None) -> dict:
    """
    Recompute IC weights, write to cache, append to history log.

    Returns the new normalised weight dict.
    Should be called once per week (Sunday review cycle).
    """
    raw_ic  = compute_rolling_ic(signals_log_path)
    weights = normalize_ic_weights(raw_ic)

    all_none  = all(v is None for v in raw_ic.values())
    all_equal = weights == {d: round(1.0 / _N, 10) for d in DIMENSIONS}

    n_records = len(_load_signal_records(signals_log_path))

    record = {
        "updated":            datetime.now(timezone.utc).isoformat(),
        "raw_ic":             {d: (raw_ic.get(d) if raw_ic.get(d) is not None
                                   else None) for d in DIMENSIONS},
        "weights":            weights,
        "n_records":          n_records,
        "using_equal_weights": all_none or all_equal,
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
    return weights


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
