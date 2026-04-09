"""
tools/validate_backlog.py
─────────────────────────
Enforces the dependency ordering rule for the Decifer Trading feature backlog.

Rule: no feature may move to `ready` or `in_progress` while any item listed
in its `depends_on` array has a non-terminal status.

Terminal statuses:  shipped, validated, resolved, superseded
Enforced statuses:  ready, in_progress
NOT checked:        shipped (cannot retroactively block shipped code)

Usage (CLI):
    python tools/validate_backlog.py               # default backlog.json
    python tools/validate_backlog.py path/to/backlog.json
    Exit code 0 = clean, 1 = violations found

Usage (module):
    from tools.validate_backlog import validate_all, load_backlog
    result = validate_all()          # uses default path
    result = validate_all(backlog=my_dict)
    assert result.ok
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Default path ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
BACKLOG_PATH = _REPO_ROOT / "chief-decifer" / "state" / "backlog.json"

# ── Status classifications ────────────────────────────────────────────────

_DEFAULT_TERMINAL = frozenset(["shipped", "validated", "resolved", "superseded"])

# Statuses where upstream deps must be terminal before this item can proceed
_ENFORCED = frozenset(["ready", "in_progress"])


# ── Public data structures ────────────────────────────────────────────────


@dataclass
class ValidationResult:
    """Structured result returned by validate_all()."""

    dependency_violations: list[str] = field(default_factory=list)
    cycle_violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.dependency_violations and not self.cycle_violations

    @property
    def all_violations(self) -> list[str]:
        return self.dependency_violations + self.cycle_violations

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dependency_violations": self.dependency_violations,
            "cycle_violations": self.cycle_violations,
            "total_violations": len(self.all_violations),
        }


# ── I/O ───────────────────────────────────────────────────────────────────


def load_backlog(backlog_path: "str | Path | None" = None) -> dict[str, Any]:
    """
    Load and return the backlog dict from backlog.json.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the file is not valid JSON.
    """
    p = Path(backlog_path) if backlog_path else BACKLOG_PATH
    if not p.exists():
        raise FileNotFoundError(f"backlog.json not found at {p.resolve()}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"backlog.json is not valid JSON: {exc}") from exc


# ── Graph construction ────────────────────────────────────────────────────


def build_dep_graph(backlog: dict[str, Any]) -> dict[str, list[str]]:
    """
    Return a dependency graph as {item_id: [dep_id, ...]}.

    Items without a `depends_on` field are included with an empty list.
    """
    graph: dict[str, list[str]] = {}
    for item in backlog.get("items", []):
        item_id = item.get("id")
        if not item_id:
            continue
        graph[item_id] = list(item.get("depends_on", []))
    return graph


# ── Core validation functions ─────────────────────────────────────────────


def check_dependency_violations(backlog: dict[str, Any]) -> list[str]:
    """
    Return violation strings for dependency ordering errors.

    Violation codes:
      DEP_VIOLATION   — upstream dep is non-terminal
      MISSING_DEP     — dep ID does not exist in the backlog
    """
    policy = backlog.get("wip_policy", {})
    terminal = frozenset(
        policy.get("terminal_statuses", list(_DEFAULT_TERMINAL))
    )

    items_by_id: dict[str, dict] = {
        item["id"]: item
        for item in backlog.get("items", [])
        if "id" in item
    }

    violations: list[str] = []
    for item in backlog.get("items", []):
        status = item.get("status", "")
        if status not in _ENFORCED:
            continue

        item_id = item.get("id", "?")
        for dep_id in item.get("depends_on", []):
            dep = items_by_id.get(dep_id)
            if dep is None:
                violations.append(
                    f"MISSING_DEP: '{item_id}' (status={status}) depends on "
                    f"'{dep_id}' which does not exist in the backlog."
                )
            elif dep.get("status") not in terminal:
                dep_status = dep.get("status", "unknown")
                violations.append(
                    f"DEP_VIOLATION: '{item_id}' has status='{status}' but its "
                    f"dependency '{dep_id}' has status='{dep_status}' "
                    f"(must be one of {sorted(terminal)} before '{item_id}' "
                    f"can be {status})."
                )

    return violations


def check_no_cycles(backlog: dict[str, Any]) -> list[str]:
    """
    Detect dependency cycles using iterative DFS.

    Returns a list of cycle description strings (empty = no cycles).
    Format: "CYCLE: A → B → A"
    """
    graph = build_dep_graph(backlog)
    violations: list[str] = []
    visited: set[str] = set()

    def _find_cycle(start: str) -> "list[str] | None":
        stack = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for neighbour in graph.get(node, []):
                if neighbour not in graph:
                    continue  # external / missing ref — not a cycle
                if neighbour == start:
                    return path + [neighbour]
                if neighbour not in visited and neighbour not in path:
                    stack.append((neighbour, path + [neighbour]))
        return None

    for node in graph:
        if node in visited:
            continue
        cycle = _find_cycle(node)
        if cycle:
            cycle_str = " \u2192 ".join(cycle)
            violations.append(f"CYCLE: {cycle_str}")
            visited.update(cycle)
        else:
            visited.add(node)

    return violations


def validate_all(
    backlog: "dict[str, Any] | None" = None,
    backlog_path: "str | Path | None" = None,
) -> ValidationResult:
    """
    Run all dependency validations against the backlog.

    Pass either a pre-loaded *backlog* dict (for tests) or a *backlog_path*.
    If neither is given, loads from the default BACKLOG_PATH.

    Returns a ValidationResult with .ok == True if no violations found.
    """
    if backlog is None:
        backlog = load_backlog(backlog_path)

    return ValidationResult(
        dependency_violations=check_dependency_violations(backlog),
        cycle_violations=check_no_cycles(backlog),
    )


# ── CLI entry point ───────────────────────────────────────────────────────


def _main(argv: "list[str] | None" = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0]) if args else None

    try:
        backlog = load_backlog(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    result = validate_all(backlog)

    if result.ok:
        item_count = len(backlog.get("items", []))
        print(f"OK \u2014 {item_count} backlog items checked, no dependency violations.")
        return 0

    print(
        f"FAIL \u2014 {len(result.all_violations)} violation(s) found:\n",
        file=sys.stderr,
    )
    for v in result.all_violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main())
