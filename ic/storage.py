# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/storage.py                             ║
# ║   Weight persistence: read current weights, append weekly   ║
# ║   history, and orchestrate the full weekly recompute        ║
# ║   (update_ic_weights).                                      ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
import threading
from datetime import UTC, datetime

from ic.constants import (
    BASELINE_WEIGHTS,
    DIMENSIONS,
    EQUAL_WEIGHTS,
    IC_HISTORY_FILE,
    IC_WEIGHTS_FILE,
    _N,
    _ic_cfg,
    log,
)
from ic.core import compute_rolling_ic, normalize_ic_weights
from ic.data import _load_signal_records, count_independent_dates

# RB-7: Protect ic_weights.json against concurrent reads during a write.
# update_ic_weights() runs on the weekly review background thread while
# _get_edge_gate_adj() reads from the main scan loop. The write is already
# atomic (os.replace), so the risk window is tiny — but a thread-local lock
# eliminates it entirely at zero cost.
_ic_weights_lock = threading.Lock()


def get_current_weights() -> dict:
    """
    Return the weights live scoring should use.

    Returns BASELINE_WEIGHTS (not EQUAL_WEIGHTS) when:
    - The cache file does not exist (no IC data yet)
    - The file is malformed / unreadable
    - ic_valid_for_live_scoring is False in the JSON
    - The weight vector does not sum to ~1.0

    BASELINE_WEIGHTS is strictly better than EQUAL_WEIGHTS as a fallback:
    it gives zero weight to persistently inactive dimensions (pead,
    analyst_revision, insider_buying) that EQUAL_WEIGHTS would accidentally
    activate.  Only the normalise_ic_weights() cold-start path (all-None IC)
    internally uses EQUAL_WEIGHTS — once persisted with ic_valid=False,
    get_current_weights() converts that to BASELINE for live scoring.
    """
    if not os.path.exists(IC_WEIGHTS_FILE):
        log.warning("get_current_weights: ic_weights.json not found — using BASELINE_WEIGHTS")
        return dict(BASELINE_WEIGHTS)
    try:
        with _ic_weights_lock:  # RB-7: serialise against concurrent update_ic_weights write
            with open(IC_WEIGHTS_FILE) as f:
                data = json.load(f)
        weights = data.get("weights", {})

        # Structural sanity: file must contain all dimensions and sum to ~1.0
        if not all(d in weights for d in DIMENSIONS):
            log.warning("get_current_weights: weight file missing dimensions — using BASELINE_WEIGHTS")
            return dict(BASELINE_WEIGHTS)
        total = sum(weights.values())
        if abs(total - 1.0) > 0.05:
            log.warning(
                "get_current_weights: weight sum %.4f far from 1.0 — using BASELINE_WEIGHTS", total
            )
            return dict(BASELINE_WEIGHTS)

        # Validity gate: ic_valid_for_live_scoring must be True.
        # Old-format files (pre-guardrail) lack this key — apply a quick HHI
        # check as backward-compatible safety net.
        ic_valid = data.get("ic_valid_for_live_scoring")
        if ic_valid is False:
            reason = data.get("fallback_reason", "unknown")
            log.warning(
                "get_current_weights: ic_valid_for_live_scoring=False (%s) — using BASELINE_WEIGHTS",
                reason,
            )
            return dict(BASELINE_WEIGHTS)
        if ic_valid is None:
            # Old format: compute quick HHI check
            hhi = sum(w * w for w in weights.values())
            if hhi > 0.40:
                log.warning(
                    "get_current_weights: legacy file with HHI=%.3f — using BASELINE_WEIGHTS", hhi
                )
                return dict(BASELINE_WEIGHTS)

        return {d: float(weights[d]) for d in DIMENSIONS}
    except Exception as e:
        log.warning("get_current_weights: load failed: %s — using BASELINE_WEIGHTS", e)
        return dict(BASELINE_WEIGHTS)


def update_ic_weights(
    signals_log_path: str | None = None,
    historical_log_path: str | None = None,
) -> dict:
    """
    Recompute IC weights, run validity gates, write to cache, append to history.

    Returns the newly computed IC weight dict (the raw IC-derived weights, even
    when validity fails — callers use this for logging/reporting only; live
    scoring reads ic_valid_for_live_scoring from the JSON via get_current_weights).

    IC validity pipeline
    --------------------
    1. compute_rolling_ic() → raw Spearman IC per dimension
    2. normalize_ic_weights() → weights + meta (with min-survivors guard and
       concentration validity gates)
    3. Independent-dates gate: if n_dates < min_independent_dates (default 60),
       override ic_valid_for_live_scoring to False regardless of weight shape.
       At n_dates=21 (current state 2026-05-19), this gate always fails —
       live scoring uses BASELINE_WEIGHTS until ~60 trading days of data.
    4. Persist all validity fields to ic_weights.json.
    """
    raw_ic = compute_rolling_ic(signals_log_path, historical_log_path=historical_log_path)
    weights, ic_meta = normalize_ic_weights(raw_ic)

    # Count independent trading dates to assess statistical reliability.
    # The true effective sample size for IC significance testing is n_dates,
    # not n_records (~1,700 symbols × n_dates are not independent).
    records = _load_signal_records(signals_log_path)
    n_records = len(records)
    n_dates = count_independent_dates(records)
    min_dates = _ic_cfg("min_independent_dates", 60)

    ic_valid = ic_meta["ic_valid_for_live_scoring"]
    fallback_reason = ic_meta.get("fallback_reason")

    if ic_valid and n_dates < min_dates:
        # Weights passed the concentration gates but the sample is too small
        # for the IC values to be statistically reliable.
        ic_valid = False
        fallback_reason = f"insufficient_independent_dates:{n_dates}<{min_dates}"
        log.warning(
            "update_ic_weights: only %d independent trading dates (need %d) — "
            "IC weights are advisory-only; live scoring will use BASELINE_WEIGHTS",
            n_dates,
            min_dates,
        )

    all_none = all(v is None for v in raw_ic.values())
    _eq_weight = 1.0 / _N
    all_equal = _ic_cfg("force_equal_weights", False) or all(
        abs(weights.get(d, 0.0) - _eq_weight) < 1e-9 for d in DIMENSIONS
    )

    # Concentration alert: log when HHI is extreme regardless of validity gate
    hhi = ic_meta.get("hhi", 0.0)
    if hhi > 0.40:
        log.warning(
            "update_ic_weights: IC weight concentration critical — HHI=%.3f eff_N=%.1f; "
            "consider lowering ic_min_threshold or collecting more data",
            hhi,
            ic_meta.get("effective_n", 0.0),
        )

    record = {
        "updated": datetime.now(UTC).isoformat(),
        "raw_ic": {d: (raw_ic.get(d) if raw_ic.get(d) is not None else None) for d in DIMENSIONS},
        "weights": weights,
        "n_records": n_records,
        "n_independent_dates": n_dates,
        "ic_valid_for_live_scoring": ic_valid,
        "fallback_reason": fallback_reason,
        "fallback_weights_source": ic_meta.get("fallback_weights_source", "unknown"),
        "advisory_only": not ic_valid,
        "using_equal_weights": all_none or all_equal,
        "noise_floor_applied": ic_meta["noise_floor_applied"],
        "dimensions_suppressed": ic_meta["dimensions_suppressed"],
        "hhi_capped": ic_meta["hhi_capped"],
        "n_active_dimensions": ic_meta.get("n_active_dimensions", 0),
        "n_survivors": ic_meta.get("n_survivors", 0),
        "top_1_weight": ic_meta.get("top_1_weight", 0.0),
        "top_2_combined_weight": ic_meta.get("top_2_combined_weight", 0.0),
        "hhi": ic_meta.get("hhi", 0.0),
        "effective_n": ic_meta.get("effective_n", 0.0),
        "threshold_used": ic_meta.get("threshold_used", 0.0),
    }

    os.makedirs(os.path.dirname(IC_WEIGHTS_FILE), exist_ok=True)

    # Atomic write for the current weights file, serialised with the read lock.
    import tempfile

    dir_ = os.path.dirname(IC_WEIGHTS_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
        with _ic_weights_lock:  # RB-7: hold lock only for the rename, not the full write
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
        "IC weights updated (n=%d, n_dates=%d, valid=%s, equal=%s): %s",
        n_records,
        n_dates,
        ic_valid,
        record["using_equal_weights"],
        ", ".join(f"{d}={weights[d]:.3f}" for d in DIMENSIONS),
    )

    # Auto-disable / auto-enable dimensions based on consecutive IC history.
    # Lazy import to break the storage ↔ monitoring cycle:
    # monitoring._check_ic_auto_disable calls storage.get_ic_weight_history.
    from ic.monitoring import _check_ic_auto_disable

    _check_ic_auto_disable(raw_ic)

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
