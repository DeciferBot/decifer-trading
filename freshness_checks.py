"""
freshness_checks.py — Control-plane staleness validation utilities.

Single responsibility: check that critical data artifacts are recent
enough to be trusted. Used by:
  - run_intelligence_pipeline.py  (intelligence files — fail closed if stale)
  - ic_validator.py               (ic_weights.json — warn only)
  - scripts/control_plane_status.py (observability report)

No broker calls. No live data. No order logic. Safe to import anywhere.

Timestamp field conventions used by each artifact:
  intelligence/*.json          → "generated_at"  (ISO 8601 UTC, e.g. "2026-05-12T12:45:00Z")
  data/committed_universe.json → "refreshed_at"  (ISO 8601 UTC)
  data/ic_weights.json         → "updated"        (ISO 8601 UTC)

If a file exists but has no recognised timestamp field, the check returns
INSUFFICIENT_DATA rather than silently assuming freshness. This prevents
the false-confidence failure pattern (file exists → assumed current).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

log = logging.getLogger("decifer.freshness_checks")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO 8601 UTC string to aware datetime. Returns None on failure."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _age_hours(ts: str) -> float | None:
    """Return age in hours of an ISO 8601 timestamp. Returns None if unparseable."""
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return (_utcnow() - dt).total_seconds() / 3600.0


def _read_json_field(path: str, field: str) -> tuple[str | None, str | None]:
    """
    Read a single string field from a JSON file.

    Returns (value, error_description).
    value is None when the file is missing, unreadable, or the field is absent.
    """
    if not os.path.exists(path):
        return None, f"file_missing: {path}"
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:
        return None, f"json_parse_error: {path}: {exc}"
    val = data.get(field)
    if val is None:
        return None, f"field_absent: {field} not in {path}"
    return str(val), None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_intelligence_freshness(
    paths: list[str] | None = None,
    max_age_hours: float = 25.0,
) -> dict:
    """
    Verify that all intelligence output files exist and have a recent generated_at.

    Default paths:
      data/intelligence/current_economic_context.json
      data/intelligence/theme_activation.json
      data/intelligence/thesis_store.json

    Returns dict with keys:
      ok       — True only when every file passes
      stale    — list of paths whose age exceeds max_age_hours
      missing  — list of paths that don't exist or can't be read
      no_ts    — list of paths without a generated_at field (INSUFFICIENT_DATA)
      ages     — {path: age_hours | None} for every checked path
      detail   — human-readable summary string
    """
    if paths is None:
        paths = [
            "data/intelligence/current_economic_context.json",
            "data/intelligence/theme_activation.json",
            "data/intelligence/thesis_store.json",
        ]

    stale: list[str] = []
    missing: list[str] = []
    no_ts: list[str] = []
    ages: dict[str, float | None] = {}

    for path in paths:
        ts, err = _read_json_field(path, "generated_at")
        if err:
            if "file_missing" in err or "json_parse_error" in err:
                missing.append(path)
            else:
                no_ts.append(path)  # field absent — INSUFFICIENT_DATA
            ages[path] = None
            log.warning("freshness_checks: intelligence file check failed — %s", err)
            continue

        age = _age_hours(ts)
        ages[path] = age
        if age is None:
            no_ts.append(path)
            log.warning("freshness_checks: unparseable generated_at in %s: %r", path, ts)
        elif age > max_age_hours:
            stale.append(path)
            log.warning(
                "freshness_checks: stale intelligence file — %s (%.1fh > %.1fh max)",
                path, age, max_age_hours,
            )

    ok = not stale and not missing and not no_ts
    problem_files = stale or missing or no_ts
    if problem_files:
        detail = (
            f"stale={[os.path.basename(p) for p in stale]}, "
            f"missing={[os.path.basename(p) for p in missing]}, "
            f"no_ts={[os.path.basename(p) for p in no_ts]}, "
            f"max_age_hours={max_age_hours}"
        )
    else:
        max_age = max((a for a in ages.values() if a is not None), default=0.0)
        detail = f"all {len(paths)} files fresh (oldest={max_age:.1f}h, limit={max_age_hours:.0f}h)"

    return {
        "ok": ok,
        "stale": stale,
        "missing": missing,
        "no_ts": no_ts,
        "ages": ages,
        "detail": detail,
        "max_age_hours": max_age_hours,
    }


def check_committed_universe_freshness(
    path: str = "data/committed_universe.json",
    max_age_days: float = 9.0,
    warn_age_days: float = 7.0,
) -> dict:
    """
    Check that committed_universe.json exists and has a recent refreshed_at.

    This check is WARN-ONLY — failing the committed universe means no candidates,
    which stops all trading. The caller decides how to act on the result.

    Returns dict with keys:
      ok           — True when file is fresh within max_age_days
      warn         — True when file is older than warn_age_days but within max_age_days
      age_days     — float or None
      detail       — human-readable summary
      status       — "fresh" | "warn" | "stale" | "missing" | "no_timestamp"
    """
    ts, err = _read_json_field(path, "refreshed_at")
    if err:
        status = "missing" if "file_missing" in err else "no_timestamp"
        log.warning("freshness_checks: committed_universe check failed — %s", err)
        return {"ok": False, "warn": False, "age_days": None,
                "detail": err, "status": status}

    age_h = _age_hours(ts)
    if age_h is None:
        return {"ok": False, "warn": False, "age_days": None,
                "detail": f"unparseable refreshed_at: {ts!r}", "status": "no_timestamp"}

    age_d = age_h / 24.0
    if age_d > max_age_days:
        status = "stale"
        ok, warn = False, False
        log.warning(
            "freshness_checks: committed_universe.json is %.1f days old (max %.0fd) — "
            "universe worker may have failed",
            age_d, max_age_days,
        )
    elif age_d > warn_age_days:
        status = "warn"
        ok, warn = True, True
        log.warning(
            "freshness_checks: committed_universe.json is %.1f days old (warn after %.0fd) — "
            "refresh is due soon",
            age_d, warn_age_days,
        )
    else:
        status = "fresh"
        ok, warn = True, False

    return {
        "ok": ok,
        "warn": warn,
        "age_days": round(age_d, 2),
        "detail": f"committed_universe.json: {age_d:.1f}d old (warn>{warn_age_days:.0f}d, max>{max_age_days:.0f}d)",
        "status": status,
    }


def check_ic_weights_freshness(
    path: str = "data/ic_weights.json",
    warn_age_days: float = 14.0,
) -> dict:
    """
    Check that ic_weights.json exists and has a recent updated field.

    WARN-ONLY — ic_weights.json is manually refreshed weekly. The Apex call
    still runs if weights are stale; this surfaces the issue for the operator.

    Returns dict with keys:
      ok        — True when file exists and age is within warn_age_days
      age_days  — float or None
      detail    — human-readable summary
      status    — "fresh" | "warn" | "missing" | "no_timestamp"
    """
    ts, err = _read_json_field(path, "updated")
    if err:
        status = "missing" if "file_missing" in err else "no_timestamp"
        log.warning("freshness_checks: ic_weights check failed — %s", err)
        return {"ok": False, "age_days": None, "detail": err, "status": status}

    age_h = _age_hours(ts)
    if age_h is None:
        return {"ok": False, "age_days": None,
                "detail": f"unparseable updated: {ts!r}", "status": "no_timestamp"}

    age_d = age_h / 24.0
    if age_d > warn_age_days:
        status = "warn"
        ok = False
        log.warning(
            "freshness_checks: ic_weights.json is %.1f days old (warn after %.0fd) — "
            "run ic_calculator.update_ic_weights() to refresh",
            age_d, warn_age_days,
        )
    else:
        status = "fresh"
        ok = True

    return {
        "ok": ok,
        "age_days": round(age_d, 2),
        "detail": f"ic_weights.json: {age_d:.1f}d old (warn after {warn_age_days:.0f}d)",
        "status": status,
    }
