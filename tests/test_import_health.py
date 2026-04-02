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
import os
import sys
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
    "catalyst_sentinel.py",
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
    "signals.py",
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
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
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
