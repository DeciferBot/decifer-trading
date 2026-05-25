#!/usr/bin/env python3
"""
verify_intelligence_execution_separation.py

Scan the Decifer Trading repository and fail if any layer boundary violation
is detected.  Run this in CI or before each commit.

Violations checked
──────────────────
  V1  Intelligence module imports an execution module
  V2  SaaS/mobile/API output module imports an execution module
  V3  yfinance import found anywhere outside archive paths
  V4  Mac-only absolute paths (/Users/, ~/Library/) in intelligence or saas modules

Exit codes
──────────
  0  No violations
  1  One or more violations detected

Usage
─────
  python3 scripts/verify_intelligence_execution_separation.py
  python3 scripts/verify_intelligence_execution_separation.py --verbose
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

# Ensure repo root on sys.path so we can import architecture/layer_boundary
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from architecture.layer_boundary import (  # noqa: E402
    Layer,
    classify_module_path,
    get_execution_module_names,
    get_intelligence_module_names,
    get_saas_output_module_names,
)

VERBOSE = "--verbose" in sys.argv

# ---------------------------------------------------------------------------
# Paths excluded from all checks (archive, vendored deps, caches)
# ---------------------------------------------------------------------------

_EXCLUDED_PATH_FRAGMENTS = (
    "/__pycache__/",
    "/.git/",
    "/node_modules/",
    "/venv/",
    "/.venv/",
    "/site-packages/",
    "/chief-decifer/",
    "/Chief-Decifer-recovered/",
    "/worktree-",
    "/archive/",
    "/deprecated/",
    "/homepage/",
    "/.claude/",
    "/.claire/",
    "/quarantine/",
)

# ---------------------------------------------------------------------------
# Mac-only path patterns that must not appear in intelligence/saas modules
# ---------------------------------------------------------------------------

_MAC_ONLY_PATTERNS = (
    "/Users/",
    "~/Library/",
    "/Library/Application Support/",
    "/private/var/",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_excluded(path: Path) -> bool:
    s = str(path)
    return any(frag in s for frag in _EXCLUDED_PATH_FRAGMENTS)


def _collect_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*.py"):
        if not _is_excluded(p):
            files.append(p)
    return files


def _parse_imports(source: str) -> list[str]:
    """Return all module names directly imported in `source` (top-level names only)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
    return names


def _source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _check_mac_only_paths(source: str) -> list[str]:
    """Return any mac-only literal path strings found in source."""
    hits: list[str] = []
    for pattern in _MAC_ONLY_PATTERNS:
        if pattern in source:
            hits.append(pattern)
    return hits


# ---------------------------------------------------------------------------
# Violation collector
# ---------------------------------------------------------------------------

Violations = list[str]


def _check_file(path: Path, exec_names: frozenset[str]) -> Violations:
    layer = classify_module_path(path)
    source = _source(path)
    imports = set(_parse_imports(source))
    violations: Violations = []

    is_boundary_checked = layer in (Layer.INTELLIGENCE, Layer.SAAS_OUTPUT)

    # V1 + V2: Intelligence or SaaS output module must not import execution modules
    if is_boundary_checked:
        bad = imports & exec_names
        for bad_mod in sorted(bad):
            violations.append(
                f"[V{'1' if layer == Layer.INTELLIGENCE else '2'}] "
                f"{path.relative_to(_REPO_ROOT)}: "
                f"layer={layer.value} imports execution module '{bad_mod}'"
            )

    # V3: yfinance import in any RUNTIME module (not tests or scripts — those may
    # reference yfinance in strings or to assert its absence).
    # Use AST import detection only to avoid false positives on string literals.
    if layer not in (Layer.TEST_ONLY, Layer.ARCHIVE_OR_DEPRECATED):
        if "yfinance" in imports:
            violations.append(
                f"[V3] {path.relative_to(_REPO_ROOT)}: yfinance import detected. "
                "yfinance was removed in v4.31.1 and must not be re-introduced. "
                "Use Alpaca (primary) or FMP (fundamentals)."
            )

    # V4: Mac-only absolute paths in intelligence or saas modules
    if is_boundary_checked:
        mac_hits = _check_mac_only_paths(source)
        for hit in mac_hits:
            violations.append(
                f"[V4] {path.relative_to(_REPO_ROOT)}: "
                f"Mac-only path pattern {hit!r} found in a {layer.value} module. "
                "Cloud-deployed intelligence modules must not depend on Mac-local paths."
            )

    return violations


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _print_layer_summary(files: list[Path]) -> None:
    counts: dict[str, int] = {}
    for f in files:
        lbl = classify_module_path(f).value
        counts[lbl] = counts.get(lbl, 0) + 1
    print("\nModule layer distribution:")
    for lbl, cnt in sorted(counts.items()):
        print(f"  {lbl:<25} {cnt:>4} files")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    exec_names = get_execution_module_names()
    intel_names = get_intelligence_module_names()
    saas_names = get_saas_output_module_names()

    all_files = _collect_python_files(_REPO_ROOT)
    all_violations: Violations = []

    checked = 0
    for path in sorted(all_files):
        vs = _check_file(path, exec_names)
        all_violations.extend(vs)
        layer = classify_module_path(path)
        if layer in (Layer.INTELLIGENCE, Layer.SAAS_OUTPUT):
            checked += 1
        if VERBOSE and vs:
            for v in vs:
                print(f"  FAIL  {v}")

    total_files = len(all_files)
    print(f"\nDecifer layer boundary verifier")
    print(f"  Scanned: {total_files} Python files")
    print(f"  Checked (intelligence + saas_output): {checked} modules")
    print(f"  Execution modules in registry: {len(exec_names)}")
    print(f"  Intelligence modules in registry: {len(intel_names)}")
    print(f"  SaaS output modules in registry: {len(saas_names)}")

    if VERBOSE:
        _print_layer_summary(all_files)

    if all_violations:
        print(f"\n  FAILED — {len(all_violations)} violation(s):\n")
        for v in all_violations:
            print(f"    {v}")
        print()
        return 1

    print(f"\n  PASSED — no layer boundary violations detected.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
