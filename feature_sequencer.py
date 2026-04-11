# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  feature_sequencer.py                       ║
# ║   Observation-window enforcement between feature activations ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Feature Sequencer — enforces a minimum observation window between
sizing-affecting feature activations.

Root cause this addresses
─────────────────────────
9 features shipped simultaneously in March–April 2026 with overlapping
position-sizing logic (IC weights, regime router, VIX-Kelly, 10-dim pipeline).
Paper-trading results became unattributable: a +2% week could be the IC
weights working, PEAD alpha, VIX-Kelly risk reduction, or random noise.

Mitigation
──────────
Any feature that touches the position-sizing pipeline (signal score →
conviction multiplier, regime multiplier, Kelly fraction, ATR sizing)
must be activated sequentially, with OBSERVATION_WINDOW_DAYS of paper
trading between each activation.

Usage
─────
    from feature_sequencer import can_activate, record_activation, get_status

    # Before turning on a new feature:
    ok, reason = can_activate("feat-ic-weighted-scoring")
    if not ok:
        raise RuntimeError(f"Sequencer blocked activation: {reason}")

    # After turning it on in config / code:
    record_activation("feat-ic-weighted-scoring", approved_by="Amit")
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("decifer.feature_sequencer")

# ── Constants ───────────────────────────────────────────────────
OBSERVATION_WINDOW_DAYS = 7  # Minimum paper-trading days between activations

# Features that feed into the position-sizing pipeline.
# Adding a feature here gates its activation behind the observation window.
#
# Criteria for inclusion:
#   • Changes signal score  → affects conviction multiplier in risk.py Layer 2
#   • Changes regime dict   → affects regime multiplier in risk.py Layer 3
#   • Changes Kelly fraction → risk.py Layer 1 (VIX-Kelly)
#   • Changes ATR sizing    → risk.py primary conversion
#   • Adds/removes signal dimensions → score scale change
#
# Analytics-only features (read position data, no sizing effect) are NOT
# included — they can ship at any time.
SIZING_AFFECTING_FEATURES: frozenset[str] = frozenset({
    "feat-ic-weighted-scoring",  # IC weights → dimension weight vector → score
    "feat-regime-router",        # VIX → dimension multipliers → score AND regime mult
    "feat-alpha-pipeline-v2",    # 9→10 dims, new PEAD/SHORT_SQUEEZE/OVERNIGHT_DRIFT
    "feat-vix-kelly",            # VIX rank → Kelly fraction (risk.py Layer 1)
    "feat-position-management",  # pyramiding + scale-out → direct sizing change
})

# Features confirmed analytics-only (no sequencing constraint):
ANALYTICS_ONLY_FEATURES: frozenset[str] = frozenset({
    "feat-alpha-decay",   # read-only forward return tracker, no sizing path
    "feat-dim-flags",     # config flags — gating mechanism, not a new sizing path
})

_BASE = os.path.dirname(os.path.abspath(__file__))
ACTIVATION_LOG_FILE = os.path.join(_BASE, "data", "feature_activation_log.json")


# ── Persistence helpers ─────────────────────────────────────────

def _load_log() -> dict:
    try:
        if os.path.exists(ACTIVATION_LOG_FILE):
            with open(ACTIVATION_LOG_FILE) as f:
                return json.load(f)
    except Exception as exc:
        log.warning(f"feature_sequencer: could not read activation log — {exc}")
    return {"schema_version": "1.0", "activations": []}


def _save_log(data: dict) -> None:
    os.makedirs(os.path.dirname(ACTIVATION_LOG_FILE), exist_ok=True)
    tmp = ACTIVATION_LOG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, ACTIVATION_LOG_FILE)


# ── Core API ────────────────────────────────────────────────────

def get_last_sizing_activation() -> Optional[dict]:
    """
    Returns the most recent activation record for a sizing-affecting feature,
    or None if no sizing-affecting feature has ever been recorded.
    """
    data = _load_log()
    sizing = [a for a in data.get("activations", [])
              if a.get("feature_id") in SIZING_AFFECTING_FEATURES]
    if not sizing:
        return None
    return max(sizing, key=lambda a: a["activated_at"])


def get_days_since_last_activation() -> Optional[float]:
    """
    Returns elapsed days since the last sizing-affecting activation,
    or None if no such activation has been recorded.
    """
    last = get_last_sizing_activation()
    if last is None:
        return None
    activated_at = datetime.fromisoformat(last["activated_at"])
    if activated_at.tzinfo is None:
        activated_at = activated_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - activated_at).total_seconds() / 86400.0


def can_activate(
    feature_id: str,
    observation_window_days: int = OBSERVATION_WINDOW_DAYS,
) -> tuple[bool, str]:
    """
    Check whether a feature may be activated now.

    Returns
    -------
    (True, reason_string)   — activation is allowed
    (False, reason_string)  — observation window not yet elapsed
    """
    if feature_id in ANALYTICS_ONLY_FEATURES:
        return True, f"{feature_id} is analytics-only — no sequencing constraint"

    if feature_id not in SIZING_AFFECTING_FEATURES:
        return True, f"{feature_id} not in SIZING_AFFECTING_FEATURES — no constraint"

    days_since = get_days_since_last_activation()
    if days_since is None:
        return True, "No prior sizing-affecting activation recorded — first activation allowed"

    last = get_last_sizing_activation()
    assert last is not None  # implied by days_since not None

    if last["feature_id"] == feature_id:
        return True, f"{feature_id} is already recorded as active — re-activation allowed"

    if days_since < observation_window_days:
        remaining = observation_window_days - days_since
        return False, (
            f"Observation window not elapsed. "
            f"Last activation: {last['feature_id']} on {last['activated_at'][:10]}. "
            f"Days elapsed: {days_since:.1f} / {observation_window_days}. "
            f"Retry in {remaining:.1f} day(s)."
        )

    return True, (
        f"Observation window elapsed ({days_since:.1f}d since {last['feature_id']}). "
        f"Activation allowed."
    )


def record_activation(
    feature_id: str,
    approved_by: str = "Amit",
    notes: str = "",
    _now: Optional[datetime] = None,  # injectable for tests
) -> dict:
    """
    Record that a sizing-affecting feature has been activated.

    Call this immediately after turning the feature on in config/code
    so the next team member (or future Claude session) can see the clock.

    Returns the activation record that was saved.
    """
    now = _now or datetime.now(timezone.utc)
    record = {
        "feature_id":         feature_id,
        "activated_at":       now.isoformat(),
        "approved_by":        approved_by,
        "is_sizing_affecting": feature_id in SIZING_AFFECTING_FEATURES,
        "notes":              notes,
    }
    data = _load_log()
    data.setdefault("activations", []).append(record)
    _save_log(data)
    log.info(
        f"feature_sequencer: recorded activation of '{feature_id}' "
        f"by {approved_by} at {now.date()}"
    )
    return record


def get_status() -> dict:
    """
    Returns the current sequencer state — suitable for dashboard injection
    and Chief Decifer state files.
    """
    last = get_last_sizing_activation()
    days_since = get_days_since_last_activation()
    data = _load_log()

    window_elapsed = days_since is None or days_since >= OBSERVATION_WINDOW_DAYS

    return {
        "last_sizing_activation":     last,
        "days_since_last_activation": round(days_since, 2) if days_since is not None else None,
        "observation_window_days":    OBSERVATION_WINDOW_DAYS,
        "window_elapsed":             window_elapsed,
        "total_activations_recorded": len(data.get("activations", [])),
        "sizing_affecting_features":  sorted(SIZING_AFFECTING_FEATURES),
        "analytics_only_features":    sorted(ANALYTICS_ONLY_FEATURES),
    }
