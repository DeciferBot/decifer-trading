# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ml_observation_writer.py                   ║
# ║   Sprint 2 — Signal observation writer for ML learning loop  ║
# ║                                                              ║
# ║   Writes one observation record per scored candidate to:     ║
# ║   data/ml/ml_observations.jsonl                              ║
# ║                                                              ║
# ║   Inert unless ml_observer_enabled=True (config default).    ║
# ║   No score changes. No ranking changes. No execution.        ║
# ║   No model training. No model loading. No predictions.       ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Signal observation writer — Sprint 2.

Called from signal_pipeline.run_signal_pipeline() after scoring and ranking
are complete. Appends one observation record per scored candidate to
data/ml/ml_observations.jsonl when ml_observer_enabled=True.

Layer classification
--------------------
  Runtime layer  : signal pipeline side-effect (non-blocking)
  Execution layer: none — does not touch orders, positions, or scores
  Training layer : none — stdlib only, no third-party ML dependencies
  Dependencies   : stdlib only (json, logging, os, threading, datetime, pathlib)

Contract
--------
  - NEVER raises — all errors degrade gracefully.
  - NEVER changes scores, rankings, order eligibility, sizing, or execution.
  - No third-party ML libraries (no scikit-learn, joblib, model loading code).
  - Writes nothing when ml_observer_enabled=False.
  - Creates the data/ml/ directory if missing.
  - Thread-safe: module-level lock serialises all JSONL appends.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("decifer.ml_observation_writer")

# ── Constants ──────────────────────────────────────────────────────────────────

SCHEMA_VERSION = "sprint2_v1"

_REPO_ROOT = Path(os.path.dirname(os.path.abspath(__file__)))

# Module-level lock — serialises concurrent JSONL appends when score_universe()
# runs in a ThreadPoolExecutor and multiple threads call write_observations().
_lock = threading.Lock()


# ── Path resolution ────────────────────────────────────────────────────────────

def _observations_path(config: dict) -> Path:
    """Return canonical path for ml_observations.jsonl from config."""
    data_dir = config.get("ml_data_dir", "data/ml")
    return _REPO_ROOT / data_dir / "ml_observations.jsonl"


# ── Time context ───────────────────────────────────────────────────────────────

def _time_context(ts: datetime) -> dict:
    """
    Derive time-of-day features from a UTC timestamp.

    is_after_hours uses an approximate EDT offset (UTC-4).  The observation
    record is for learning purposes — exact market-hours classification is
    handled by the execution layer, not here.
    """
    hour_et_approx = ts.hour - 4  # approximate EDT (UTC-4)
    return {
        "time_of_day": ts.strftime("%H:%M"),
        "day_of_week": ts.strftime("%A"),
        "is_after_hours": not (9 <= hour_et_approx < 16),
    }


# ── Record builder ─────────────────────────────────────────────────────────────

def _build_record(
    s: dict,
    rank_map: dict,
    ranking_total: int,
    regime: str,
    vix: float,
    session_date: str,
    ts: datetime,
    config: dict,
) -> dict:
    """
    Build one observation record from a scored candidate dict.

    Parameters
    ----------
    s             : all_scored dict from signal_pipeline — already stamped with
                    scan_id, observation_id, session_date, passed_base_threshold.
    rank_map      : {symbol: rank_position} covering all scored candidates.
    ranking_total : total candidates scored this cycle (len(all_scored)).
    regime        : regime name string from run_signal_pipeline().
    vix           : VIX value at scan time.
    session_date  : YYYY-MM-DD ISO date string.
    ts            : UTC datetime of observation write.
    config        : CONFIG dict for gate keys.

    Returns a JSON-safe dict.  live_score_after_observer always equals
    base_score — the observer NEVER changes scores.
    """
    sym: str = s.get("symbol", "")
    observation_id: str = s.get("observation_id") or ""
    scan_id: str = s.get("scan_id") or ""
    base_score: float = float(s.get("score") or 0.0)
    direction: str = s.get("direction", "NEUTRAL")

    # signal_scores: the per-dimension score_breakdown dict from score_universe().
    # This is the canonical feature matrix input for future training.
    signal_scores: dict | None = s.get("score_breakdown") or None

    # ── ML eligibility classification ────────────────────────────────────────
    # ml_inference_eligible is always False in Sprint 2 — no model exists yet.
    # The exclusion_reason documents why this specific record cannot be used
    # for inference today, so the outcome joiner knows the exact blocker.
    if not signal_scores:
        exclusion_reason = "missing_signal_scores"
    elif not observation_id:
        exclusion_reason = "missing_observation_id"
    elif direction not in ("LONG", "SHORT"):
        exclusion_reason = "direction_not_directional"
    else:
        exclusion_reason = "prediction_not_implemented_sprint_2"

    rec: dict = {
        "schema_version": SCHEMA_VERSION,
        "timestamp_utc": ts.isoformat(),
        "session_date": s.get("session_date") or session_date,
        "scan_id": scan_id,
        "observation_id": observation_id,
        "symbol": sym,
        "direction": direction,
        "candidate_source": s.get("candidate_source", "unknown"),
        # Score fields — live_score_after_observer == base_score (observer never changes scores)
        "base_score": base_score,
        "live_score_after_observer": base_score,
        "live_score_unchanged": True,
        # Ranking within this scan cycle
        "ranking_position": rank_map.get(sym, 0),
        "ranking_total": ranking_total,
        # Signal features (None when score_breakdown absent)
        "signal_scores": signal_scores,
        # Market context at observation time
        "regime": s.get("regime_context") or regime,
        "vix": vix,
        **_time_context(ts),
        # Threshold outcome for this candidate
        "passed_base_threshold": bool(s.get("passed_base_threshold", False)),
        # Gate state — written for auditability; both False in Sprint 2
        "ml_observer_enabled": bool(config.get("ml_observer_enabled", False)),
        "ml_score_influence_enabled": bool(config.get("ml_score_influence_enabled", False)),
        # Eligibility — always False in Sprint 2 (no model)
        "ml_inference_eligible": False,
        "exclusion_reason": exclusion_reason,
        # order_intent_linked: False at observation time.
        # Sprint 2 adds observation_id to ORDER_INTENT as a top-level field,
        # but this flag on the observation record remains False until the
        # outcome joiner (Sprint 3) confirms the linkage retroactively.
        "order_intent_linked": False,
    }

    # Flatten dim_* features alongside the record for fast column access
    # in the training dataset (avoids nested dict access in Sprint 3).
    if signal_scores:
        for dim_key, dim_val in signal_scores.items():
            rec[f"dim_{dim_key}"] = dim_val

    return rec


# ── Public API ─────────────────────────────────────────────────────────────────

def write_observations(
    all_scored: list,
    rank_map: dict,
    scan_id: str,
    regime: str,
    vix: float,
    config: dict,
    obs_path: str | Path | None = None,
) -> int:
    """
    Write one observation record per scored candidate to ml_observations.jsonl.

    Called from signal_pipeline.run_signal_pipeline() between steps 7 and 8
    (after Signal objects are built, before signals_log append), when
    ml_observer_enabled=True.

    Parameters
    ----------
    all_scored : All scored dicts from score_universe(), including below-threshold
                 candidates. Each dict has scan_id, observation_id, session_date,
                 and passed_base_threshold already stamped by signal_pipeline.
    rank_map   : {symbol: rank_position} for all scored candidates (1-indexed).
    scan_id    : Scan cycle ID (YYYYMMDDTHHmmss) — shared across signals_log
                 and ic_decision_events for cross-log correlation.
    regime     : Regime name string (e.g. "TRENDING_UP").
    vix        : VIX value at scan time (from regime dict).
    config     : CONFIG dict — must contain ml_observer_enabled, ml_data_dir.
    obs_path   : Override output path (tests only — do not use in production).

    Returns
    -------
    int — number of records written.  0 if observer disabled, empty input,
          or any write error.

    Guarantees
    ----------
    - NEVER raises.  All errors are logged and the function returns 0.
    - Trading is never blocked by a write failure.
    - Scores, rankings, and order eligibility are never modified.
    - No model files are created or loaded.
    - data/ml/ directory is created if it does not exist.
    """
    if not config.get("ml_observer_enabled", False):
        return 0

    if not all_scored:
        log.debug("ml_observation_writer: all_scored empty — nothing to write")
        return 0

    path = Path(obs_path) if obs_path else _observations_path(config)
    ts = datetime.now(UTC)
    session_date = ts.date().isoformat()
    ranking_total = len(all_scored)

    # ── Build all records before touching the file ────────────────────────────
    records: list[dict] = []
    for s in all_scored:
        try:
            records.append(
                _build_record(s, rank_map, ranking_total, regime, vix, session_date, ts, config)
            )
        except Exception as exc:
            sym = s.get("symbol", "?") if isinstance(s, dict) else "?"
            log.warning("ml_observation_writer: skipped candidate '%s': %s", sym, exc)

    if not records:
        return 0

    # ── Ensure directory exists ───────────────────────────────────────────────
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log.warning(
            "ml_observation_writer: cannot create directory %s — observations skipped: %s",
            path.parent,
            exc,
        )
        return 0

    # ── Safe JSONL append ─────────────────────────────────────────────────────
    # Build the full output string in memory first so the file open is minimal.
    # Lock ensures only one thread writes at a time (ThreadPoolExecutor safety).
    lines = "".join(json.dumps(r, default=str) + "\n" for r in records)
    try:
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(lines)
                f.flush()
                os.fsync(f.fileno())
    except Exception as exc:
        log.warning("ml_observation_writer: JSONL append failed (non-fatal): %s", exc)
        return 0

    log.info(
        "ml_observation_writer: wrote %d observation records (scan_id=%s)",
        len(records),
        scan_id,
    )
    return len(records)
