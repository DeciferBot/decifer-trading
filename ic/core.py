# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/core.py                                ║
# ║   Core IC computation: rolling Spearman IC per dimension    ║
# ║   and raw-IC → weight normalisation (with noise floor,      ║
# ║   HHI concentration cap, and validity gates).               ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import os

import numpy as np

from ic.constants import (
    BASELINE_WEIGHTS,
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


def _concentration_metrics(weights: dict) -> tuple[float, float, float, float]:
    """Return (top1, top2_combined, hhi, effective_n) for a weight dict."""
    vals = sorted(weights.values(), reverse=True)
    top1 = vals[0] if vals else 0.0
    top2 = (vals[0] + vals[1]) if len(vals) >= 2 else top1
    hhi = sum(w * w for w in vals)
    eff_n = 1.0 / hhi if hhi > 1e-9 else float(len(DIMENSIONS))
    return top1, top2, hhi, eff_n


def normalize_ic_weights(raw_ic: dict) -> tuple:
    """
    Convert raw IC values to normalised dimension weights with validity gates.

    Rules
    -----
    - Negative IC → weight = 0  (don't invert a negatively-predictive dimension)
    - None or non-finite IC     → treated as 0
    - IC < ic_min_threshold     → weight = 0  (noise floor)
    - If all weights == 0 after flooring → fall back to EQUAL_WEIGHTS (cold start)
    - Remaining positives normalised to sum to 1.0
    - HHI cap: if any weight > max_single_weight, clip and renormalise

    Validity gates (applied after normalization + HHI cap)
    -------------------------------------------------------
    If any gate fails, BASELINE_WEIGHTS is returned and ic_valid_for_live_scoring
    is set to False:
      1. Min-survivors guard: n_survivors <= 2 after thresholding → degenerate,
         the HHI cap would force all remaining budget onto a single other dim,
         potentially inverting the IC-derived ranking.
      2. Min active dims: n non-zero weights < min_active_dims (default 5).
      3. Top-2 combined weight > max_top2_combined_weight (default 0.75).
      4. HHI > max_hhi (default 0.30).
      5. Ranking inversion: a lower-IC dim receives materially higher weight
         than a higher-IC dim solely due to HHI-cap redistribution.

    Paper learning mode (force_equal_weights=True):
    - Returns equal weights bypassing IC weighting entirely.

    Returns
    -------
    (weights dict, metadata dict)

    Metadata keys
    -------------
      ic_valid_for_live_scoring : bool — False → live scoring must use BASELINE_WEIGHTS
      fallback_reason           : str | None
      fallback_weights_source   : "ic" | "baseline" | "equal"
      n_active_dimensions       : int
      n_survivors               : int (after threshold, before normalization)
      top_1_weight              : float
      top_2_combined_weight     : float
      hhi                       : float
      effective_n               : float
      threshold_used            : float
      noise_floor_applied       : bool
      dimensions_suppressed     : list[str]
      hhi_capped                : bool
      advisory_only             : bool (True when n_dates < min_independent_dates)
    """
    if _ic_cfg("force_equal_weights", False):
        log.info("normalize_ic_weights: force_equal_weights=True — returning equal weights (paper learning mode)")
        top1, top2, hhi, eff_n = _concentration_metrics(EQUAL_WEIGHTS)
        return dict(EQUAL_WEIGHTS), {
            "ic_valid_for_live_scoring": True,
            "fallback_reason": None,
            "fallback_weights_source": "equal",
            "n_active_dimensions": len(DIMENSIONS),
            "n_survivors": len(DIMENSIONS),
            "top_1_weight": round(top1, 4),
            "top_2_combined_weight": round(top2, 4),
            "hhi": round(hhi, 4),
            "effective_n": round(eff_n, 2),
            "threshold_used": 0.0,
            "noise_floor_applied": False,
            "dimensions_suppressed": [],
            "hhi_capped": False,
            "advisory_only": False,
        }

    ic_min = _ic_cfg("ic_min_threshold", 0.0)
    hhi_cap = _ic_cfg("max_single_weight", 0.40)
    min_active = _ic_cfg("min_active_dims", 5)
    max_top2 = _ic_cfg("max_top2_combined_weight", 0.75)
    max_hhi = _ic_cfg("max_hhi", 0.30)

    def _baseline(reason: str, *, n_survivors: int = 0, suppressed: list | None = None) -> tuple:
        top1, top2, hhi, eff_n = _concentration_metrics(BASELINE_WEIGHTS)
        log.warning(
            "normalize_ic_weights: IC validity gate failed (%s) — using BASELINE_WEIGHTS for live scoring",
            reason,
        )
        return dict(BASELINE_WEIGHTS), {
            "ic_valid_for_live_scoring": False,
            "fallback_reason": reason,
            "fallback_weights_source": "baseline",
            "n_active_dimensions": sum(1 for w in BASELINE_WEIGHTS.values() if w > 0),
            "n_survivors": n_survivors,
            "top_1_weight": round(top1, 4),
            "top_2_combined_weight": round(top2, 4),
            "hhi": round(hhi, 4),
            "effective_n": round(eff_n, 2),
            "threshold_used": ic_min,
            "noise_floor_applied": ic_min > 0.0,
            "dimensions_suppressed": suppressed or [],
            "hhi_capped": False,
            "advisory_only": True,
        }

    # ── Step 1: apply noise floor ─────────────────────────────────────────────
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
        # All IC non-positive or below noise floor — cold-start fallback to equal weights.
        # (Not the min-survivors BASELINE path: here we have NO positive IC data at all.)
        top1, top2, hhi, eff_n = _concentration_metrics(EQUAL_WEIGHTS)
        return dict(EQUAL_WEIGHTS), {
            "ic_valid_for_live_scoring": False,
            "fallback_reason": "no_positive_ic_above_threshold",
            "fallback_weights_source": "equal",
            "n_active_dimensions": len(DIMENSIONS),
            "n_survivors": 0,
            "top_1_weight": round(top1, 4),
            "top_2_combined_weight": round(top2, 4),
            "hhi": round(hhi, 4),
            "effective_n": round(eff_n, 2),
            "threshold_used": ic_min,
            "noise_floor_applied": ic_min > 0.0,
            "dimensions_suppressed": suppressed,
            "hhi_capped": False,
            "advisory_only": True,
        }

    # ── Step 2: min-survivors guard ───────────────────────────────────────────
    # With only 1 or 2 survivors, the HHI cap redistributes ALL remaining weight
    # to a single other dimension, which can invert the IC-derived ranking
    # (lower-IC dim ends up with higher weight than higher-IC dim).
    # Fail early before normalization to avoid this degenerate path.
    n_survivors = sum(1 for v in floored.values() if v > 0.0)
    if n_survivors <= 2:
        return _baseline(
            f"insufficient_survivors_after_threshold:{n_survivors}",
            n_survivors=n_survivors,
            suppressed=suppressed,
        )

    # ── Step 3: normalize ─────────────────────────────────────────────────────
    normalized = {d: floored[d] / total for d in DIMENSIONS}

    # ── Step 4: HHI concentration cap ────────────────────────────────────────
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
                capped[d] = remaining / max(len(under), 1)
        normalized = capped

    # ── Step 5: validity gates ────────────────────────────────────────────────
    n_active = sum(1 for w in normalized.values() if w > 1e-9)
    top1, top2, hhi_val, eff_n = _concentration_metrics(normalized)

    if n_active < min_active:
        return _baseline(
            f"min_active_dims_not_met:{n_active}<{min_active}",
            n_survivors=n_survivors,
            suppressed=suppressed,
        )

    if top2 > max_top2 + 1e-9:
        return _baseline(
            f"top2_combined_weight_exceeded:{top2:.3f}>{max_top2}",
            n_survivors=n_survivors,
            suppressed=suppressed,
        )

    if hhi_val > max_hhi + 1e-9:
        return _baseline(
            f"hhi_exceeded:{hhi_val:.3f}>{max_hhi}",
            n_survivors=n_survivors,
            suppressed=suppressed,
        )

    # Ranking inversion: lower-IC dim must not receive materially more weight
    # than a higher-IC dim solely due to HHI-cap redistribution.
    _RANKING_MARGIN = 0.05
    positive_dims = [
        (d, float(raw_ic.get(d) or 0))
        for d in DIMENSIONS
        if (raw_ic.get(d) or 0) > 0 and floored.get(d, 0) > 0
    ]
    positive_dims.sort(key=lambda x: x[1], reverse=True)
    for i, (da, ica) in enumerate(positive_dims):
        for db, icb in positive_dims[i + 1:]:
            if normalized[db] > normalized[da] + _RANKING_MARGIN:
                return _baseline(
                    f"ranking_inversion:{db}(ic={icb:.3f},wt={normalized[db]:.3f})"
                    f">  {da}(ic={ica:.3f},wt={normalized[da]:.3f})",
                    n_survivors=n_survivors,
                    suppressed=suppressed,
                )

    # ── All gates passed ──────────────────────────────────────────────────────
    return normalized, {
        "ic_valid_for_live_scoring": True,
        "fallback_reason": None,
        "fallback_weights_source": "ic",
        "n_active_dimensions": n_active,
        "n_survivors": n_survivors,
        "top_1_weight": round(top1, 4),
        "top_2_combined_weight": round(top2, 4),
        "hhi": round(hhi_val, 4),
        "effective_n": round(eff_n, 2),
        "threshold_used": ic_min,
        "noise_floor_applied": ic_min > 0.0,
        "dimensions_suppressed": suppressed,
        "hhi_capped": hhi_capped,
        "advisory_only": False,
    }
