"""
Regression guard: all core modules must be importable on Python 3.9.

This test exists because the bot.py → 6-module refactor introduced Python
3.10+ union-type syntax (e.g. `dict | None`) in module-level variable
annotations across 15 files. On Python 3.9 those expressions are evaluated at
import time and raise TypeError. The entire test suite for those modules
silently fell off — zero tests were running.

The canonical fix is `from __future__ import annotations` at the top of each
affected file. If this test fails, that import is missing from the offending
module.

The test uses AST parsing (no deps required) to verify that either:
  (a) `from __future__ import annotations` is present, OR
  (b) no bare `X | Y` union expressions exist at module scope

In practice we enforce (a) for all core modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Project root is one level up from the tests/ directory.
PROJECT_ROOT = Path(__file__).parent.parent

# Every module that was broken by the refactor, plus all new sub-modules.
# Update this list whenever a new core module is introduced.
CORE_MODULE_FILES = [
    "alpha_decay.py",
    "bot_account.py",
    "bot_dashboard.py",
    "bot_ibkr.py",
    "bot_sentinel.py",
    "bot_state.py",
    "bot_trading.py",
    "config.py",
    "execution_agent.py",
    "ic_validator.py",
    "learning.py",
    "options.py",
    "options_scanner.py",
    "orders.py",
    "phase_gate.py",
    "risk.py",
    "scanner.py",
    "signals/__init__.py",
    "telegram_bot.py",
    "wip_tracker.py",
]


def _has_future_annotations(source: str) -> bool:
    """Return True if `from __future__ import annotations` is present."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            for alias in node.names:
                if alias.name == "annotations":
                    return True
    return False


def _has_bare_union_annotation(source: str) -> bool:
    """
    Return True if any module-level variable annotation uses `X | Y` syntax.

    These are safe in Python 3.10+ but raise TypeError on 3.9 at import time
    unless `from __future__ import annotations` is present.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in tree.body:  # only top-level statements
        if isinstance(node, ast.AnnAssign):
            ann_src = ast.unparse(node.annotation) if hasattr(ast, "unparse") else ""
            if "|" in ann_src:
                return True
    return False


def test_signals_resolves_to_package() -> None:
    """
    Regression guard: `import signals` must resolve to signals/__init__.py,
    not any signals.py at the repo root.

    Python silently prefers a package (signals/) over a same-named module
    (signals.py) with no warning. If signals.py ever reappears at the repo
    root, edits to it will be dead code — exactly what happened with the
    catalyst boost in commit 8a589df. This test makes that mistake visible
    immediately rather than after a silent production outage.
    """
    import importlib, sys

    # Force a fresh import so no stale sys.modules entry interferes.
    sys.modules.pop("signals", None)
    import signals  # noqa: PLC0415

    expected_suffix = str(Path("signals") / "__init__.py")
    actual = signals.__file__ or ""
    assert actual.endswith(expected_suffix), (
        f"`import signals` resolved to {actual!r}.\n"
        f"Expected it to end with {expected_suffix!r}.\n"
        "A signals.py at the repo root is shadowing the package — delete it."
    )


@pytest.mark.parametrize("filename", CORE_MODULE_FILES)
def test_future_annotations_present(filename: str) -> None:
    """
    Each core module must declare `from __future__ import annotations`.

    This is the required guard for Python 3.9 compatibility with X|Y union
    type hints.  Fail fast here so the problem is obvious, rather than
    discovering it through silent test-collection failures.
    """
    path = PROJECT_ROOT / filename
    if not path.exists():
        pytest.skip(f"{filename} not found at {path}")

    source = path.read_text(encoding="utf-8")
    assert _has_future_annotations(source), (
        f"{filename} is missing `from __future__ import annotations`.\n"
        f"This file uses X|Y union type syntax which crashes on Python 3.9 "
        f"at import time. Add `from __future__ import annotations` as the "
        f"first non-comment import in the file."
    )
