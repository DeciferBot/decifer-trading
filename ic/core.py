# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/core.py                                ║
# ║   Core IC computation: rolling Spearman IC per dimension    ║
# ║   and raw-IC → weight normalisation (with noise floor and   ║
# ║   HHI concentration cap).                                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import os

import numpy as np

from ic.constants import (
    DIMENSIONS,
    EQUAL_WEIGHTS,
    MIN_VALID,
    ROLLING_WINDOW,
    _ic_cfg,
    log,
)
from ic.data import _fetch_forward_returns_batch, _load_signal_records
from ic.math import _spearman, _zscore_array


def compute_rolling_ic(
    signals_log_path: str | None = None,
    window: int = ROLLING_WINDOW,
    min_valid: int = MIN_VALID,
    historical_log_path: str | None = None,
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
        # Historical records carry pre-computed fwd_return — cap at 5 000 most
        # recent to keep Spearman computation fast while still dwarfing the live
        # window.  No age gate needed (fwd_return is already embedded).
        hist_records = _load_signal_records(historical_log_path, window=5_000, min_age_days=0)
        records = live_records + hist_records
        log.info(
            "compute_rolling_ic: merged %d live + %d historical = %d records",
            len(live_records),
            len(hist_records),
            len(records),
        )
    else:
        records = _load_signal_records(signals_log_path, window, min_age_days=min_age_days)

    if len(records) < min_valid:
        log.info(
            "compute_rolling_ic: %d valid records (need %d) — returning None IC",
            len(records),
            min_valid,
        )
        return {d: None for d in DIMENSIONS}

    fwd_map = _fetch_forward_returns_batch(records)

    # Build paired arrays of (dim_scores, forward_return) for each dimension
    dim_raw: dict[str, list] = {d: [] for d in DIMENSIONS}
    fwd_returns: list = []

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
            "compute_rolling_ic: only %d records have forward returns (need %d) — returning None IC",
            n,
            min_valid,
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

    ic_min = _ic_cfg("ic_min_threshold", 0.0)
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
        over = [d for d, w in normalized.items() if w > hhi_cap]
        under = {d: w for d, w in normalized.items() if w <= hhi_cap}
        log.warning(
            "normalize_ic_weights: HHI cap triggered — %s exceeded %.0f%% weight; clipping",
            over,
            hhi_cap * 100,
        )
        remaining = 1.0 - len(over) * hhi_cap
        under_total = sum(under.values())
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
