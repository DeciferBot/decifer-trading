# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/monitoring.py                          ║
# ║   Health, quality, divergence checks, and auto-disable /   ║
# ║   auto-enable governance.  Read-mostly — the one writer is ║
# ║   _check_ic_auto_disable (settings_override.json +         ║
# ║   audit_log.jsonl).                                         ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
from datetime import UTC, datetime

import numpy as np

from ic.constants import (
    DIMENSIONS,
    IC_LIVE_FILE,
    IC_WEIGHTS_FILE,
    MIN_VALID,
    _BASE,
    log,
)
from ic.data import _fetch_forward_returns_batch, _load_signal_records
from ic.math import _spearman, _zscore_array


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
        positive_ics = [float(v) for v in raw_ic.values() if v is not None and np.isfinite(float(v)) and float(v) > 0.0]
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
                len(short_records),
                MIN_VALID,
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
    live_n = live_data.get("n_records", 0)

    if live_n < MIN_VALID:
        log.debug(
            "check_ic_divergence: only %d live records (need %d) — skipping",
            live_n,
            MIN_VALID,
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

        if (l > 0 and h < 0) or (l < 0 and h > 0):
            direction = "sign_flip"
        elif delta > 0:
            direction = "live_better"
        else:
            direction = "live_worse"

        entry = {
            "dimension": d,
            "live_ic": round(l, 4),
            "historical_ic": round(h, 4),
            "delta": round(delta, 4),
            "direction": direction,
            "live_n": live_n,
        }
        warnings.append(entry)

        log.warning(
            "IC_DIVERGENCE [%s] live=%.4f hist=%.4f delta=%+.4f (%s) n_live=%d",
            d,
            l,
            h,
            delta,
            direction,
            live_n,
        )

    if not warnings:
        log.info(
            "check_ic_divergence: no significant divergence (threshold=%.3f, n_live=%d)",
            divergence_threshold,
            live_n,
        )

    return warnings


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

        # Lazy import to avoid cycle: storage imports monitoring.
        from ic.storage import get_ic_weight_history

        ic_cfg = CONFIG.get("ic_calculator", {})
        disable_thresh = ic_cfg.get("auto_disable_threshold", -0.02)
        disable_weeks = ic_cfg.get("auto_disable_weeks", 3)
        enable_thresh = ic_cfg.get("auto_enable_threshold", 0.01)
        enable_weeks = ic_cfg.get("auto_enable_weeks", 2)

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
        now_iso = datetime.now(UTC).isoformat()

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
                        dim,
                        [round(v, 4) for v in last_n_ics],
                        disable_weeks,
                    )
                    try:
                        with open(audit_path, "a") as f:
                            f.write(
                                json.dumps(
                                    {
                                        "ts": now_iso,
                                        "event": "IC_AUTO_DISABLE",
                                        "dimension": dim,
                                        "ic_history": last_n_ics,
                                        "reason": f"{disable_weeks} consecutive weeks IC < {disable_thresh}",
                                    }
                                )
                                + "\n"
                            )
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
                        dim,
                        [round(v, 4) for v in last_m_ics],
                        enable_weeks,
                    )
                    try:
                        with open(audit_path, "a") as f:
                            f.write(
                                json.dumps(
                                    {
                                        "ts": now_iso,
                                        "event": "IC_AUTO_ENABLE",
                                        "dimension": dim,
                                        "ic_history": last_m_ics,
                                        "reason": f"{enable_weeks} consecutive weeks IC > {enable_thresh}",
                                    }
                                )
                                + "\n"
                            )
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
