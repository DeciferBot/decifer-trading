# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  wip_tracker.py                             ║
# ║   Enforces the WIP (Work In Progress) limit: no more than    ║
# ║   max_active features may be in_progress simultaneously.     ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Root cause this addresses
# ─────────────────────────
# The backlog tracked features with free-text status fields and had
# no enforcement layer. Nothing prevented all 50 planned features
# from being marked in_progress at once, diluting engineering focus
# and guaranteeing nothing ships to completion.
#
# This module provides the enforcement layer, mirroring phase_gate.py:
#   validate_wip()        → returns list of violation strings (empty = OK)
#   check_wip_limit()     → raises WIPLimitViolation if over limit
#   can_activate()        → bool, safe to pull another feature into active?
#   get_wip_status()      → WIPStatus dataclass for dashboard display
#   load_backlog()        → read chief-decifer/state/backlog.json

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Default path (relative to repo root) ─────────────────────────

BACKLOG_PATH = Path("chief-decifer/state/backlog.json")

# ── Public exception ──────────────────────────────────────────────


class WIPLimitViolation(RuntimeError):
    """Raised when activating a feature would exceed the WIP limit."""


# ── Public data structures ────────────────────────────────────────


@dataclass
class WIPStatus:
    active_count: int
    max_active: int
    active_features: list[dict[str, Any]]
    over_limit: bool
    slots_available: int
    wip_policy: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_count": self.active_count,
            "max_active": self.max_active,
            "active_features": [
                {"id": f.get("id"), "title": f.get("title"), "roadmap_phase": f.get("roadmap_phase")}
                for f in self.active_features
            ],
            "over_limit": self.over_limit,
            "slots_available": self.slots_available,
        }


# ── Internal helpers ──────────────────────────────────────────────


def load_backlog(backlog_path: str | Path | None = None) -> dict[str, Any]:
    """Load and return the backlog dict from backlog.json."""
    p = Path(backlog_path) if backlog_path else BACKLOG_PATH
    if not p.exists():
        raise FileNotFoundError(f"backlog.json not found at {p.resolve()}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"backlog.json is not valid JSON: {exc}") from exc


def _get_active_statuses(backlog: dict[str, Any]) -> list[str]:
    """Return the list of statuses that count toward the WIP limit."""
    policy = backlog.get("wip_policy", {})
    return list(policy.get("active_statuses", ["in_progress"]))


def _get_max_active(backlog: dict[str, Any]) -> int:
    """Return the configured WIP limit (defaults to 3 if unset)."""
    policy = backlog.get("wip_policy", {})
    return int(policy.get("max_active", 3))


def _active_items(backlog: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all backlog items whose status counts toward WIP."""
    active_statuses = set(_get_active_statuses(backlog))
    return [
        item for item in backlog.get("items", [])
        if item.get("status") in active_statuses
    ]


# ── Public API ────────────────────────────────────────────────────


def get_wip_status(backlog: dict[str, Any] | None = None,
                   backlog_path: str | Path | None = None) -> WIPStatus:
    """
    Return a WIPStatus describing current work-in-progress state.

    Pass either a pre-loaded *backlog* dict (for tests) or a *backlog_path*.
    If neither is given, loads from the default BACKLOG_PATH.
    """
    if backlog is None:
        backlog = load_backlog(backlog_path)

    max_active = _get_max_active(backlog)
    active = _active_items(backlog)
    active_count = len(active)
    over_limit = active_count > max_active

    return WIPStatus(
        active_count=active_count,
        max_active=max_active,
        active_features=active,
        over_limit=over_limit,
        slots_available=max(0, max_active - active_count),
        wip_policy=backlog.get("wip_policy", {}),
    )


def validate_wip(backlog: dict[str, Any] | None = None,
                 backlog_path: str | Path | None = None) -> list[str]:
    """
    Validate the backlog against the WIP limit.
    Returns a list of violation strings (empty list = all clear).

    Usage::

        violations = validate_wip()
        if violations:
            for v in violations:
                print(v)
    """
    if backlog is None:
        backlog = load_backlog(backlog_path)

    violations: list[str] = []
    status = get_wip_status(backlog)

    if status.over_limit:
        active_ids = [f.get("id", "?") for f in status.active_features]
        violations.append(
            f"WIP_LIMIT_EXCEEDED: {status.active_count} features are in_progress "
            f"(limit is {status.max_active}). "
            f"Active: {active_ids}. "
            f"Ship and validate at least {status.active_count - status.max_active} "
            f"feature(s) before pulling in new work."
        )

    # Check for items blocked by unfinished dependencies still marked in_progress
    terminal_statuses = set(
        backlog.get("wip_policy", {}).get(
            "terminal_statuses", ["shipped", "validated", "resolved"]
        )
    )
    items_by_id: dict[str, dict] = {
        item["id"]: item for item in backlog.get("items", []) if "id" in item
    }
    active_statuses = set(_get_active_statuses(backlog))

    for item in backlog.get("items", []):
        if item.get("status") not in active_statuses:
            continue
        for dep_id in item.get("dependencies", []):
            dep = items_by_id.get(dep_id)
            if dep is None:
                violations.append(
                    f"MISSING_DEPENDENCY: '{item['id']}' depends on '{dep_id}' "
                    f"which does not exist in the backlog."
                )
            elif dep.get("status") not in terminal_statuses:
                violations.append(
                    f"UNMET_DEPENDENCY: '{item['id']}' is in_progress but its "
                    f"dependency '{dep_id}' has status='{dep.get('status')}' "
                    f"(must be one of {sorted(terminal_statuses)} first)."
                )

    return violations


def check_wip_limit(backlog: dict[str, Any] | None = None,
                    backlog_path: str | Path | None = None) -> None:
    """
    Raise WIPLimitViolation if the WIP limit is exceeded.
    Call this before marking any new feature as in_progress.

    Usage::

        from wip_tracker import check_wip_limit
        check_wip_limit()   # raises if already at/over limit
    """
    if backlog is None:
        backlog = load_backlog(backlog_path)

    violations = validate_wip(backlog)
    # Only raise on the WIP limit violation, not dependency issues
    wip_violations = [v for v in violations if v.startswith("WIP_LIMIT_EXCEEDED")]
    if wip_violations:
        raise WIPLimitViolation(wip_violations[0])


def can_activate(backlog: dict[str, Any] | None = None,
                 backlog_path: str | Path | None = None) -> bool:
    """
    Return True if a new feature can be pulled into active (in_progress)
    without exceeding the WIP limit.

    Usage::

        if can_activate():
            # safe to mark next feature in_progress
    """
    if backlog is None:
        backlog = load_backlog(backlog_path)

    status = get_wip_status(backlog)
    return status.slots_available > 0
