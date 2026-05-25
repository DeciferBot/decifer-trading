"""
Guard test: zero yfinance imports in active source modules.

yfinance is not approved for Decifer Trading (removed v4.31.3, hardened
v4.31.4). These tests prevent re-introduction.

Coverage:
  1. Specific high-risk files — explicit per-file checks.
  2. Broad module scan — walks every .py in active source directories and
     asserts no active `import yfinance` or `from yfinance` statement.

Active source directories (tested by broad scan):
  signals/  scripts/  and all top-level .py files.

Excluded from broad scan:
  archive/  Chief-Decifer-recovered/  tests/  docs/  .git/  .claire/
  .claude/  chief-decifer/
"""

from __future__ import annotations

import os
import pathlib

_REPO_ROOT = pathlib.Path(__file__).parent.parent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _module_src(rel_path: str) -> str:
    return (_REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _has_yfinance_import(src: str) -> bool:
    """Return True if any non-comment line contains a yfinance import."""
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "import yfinance" in stripped or "from yfinance" in stripped:
            return True
    return False


_SKIP_DIRS = {
    "archive",
    "Chief-Decifer-recovered",
    "tests",
    "docs",
    ".git",
    ".claire",
    ".claude",
    "chief-decifer",
    "venv",
    ".venv",
    "site-packages",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "eggs",
    ".eggs",
}


def _walk_active_py_files():
    """
    Yield (relative_path, Path) for every .py in active source dirs.
    Prunes archived, test, docs, and hidden directories.
    """
    for root, dirs, files in os.walk(_REPO_ROOT):
        root_path = pathlib.Path(root)
        # Prune in-place so os.walk doesn't descend into excluded dirs
        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            abs_path = root_path / fname
            rel = abs_path.relative_to(_REPO_ROOT)
            # Extra safety: skip if any parent part is in SKIP_DIRS
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            yield str(rel), abs_path


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Specific high-risk file checks (explicit, fast feedback)
# ═══════════════════════════════════════════════════════════════════════════════


def test_signals_init_no_yfinance():
    src = _module_src("signals/__init__.py")
    assert "import yfinance" not in src, "signals/__init__.py must not import yfinance"
    assert "yf.Ticker" not in src, "signals/__init__.py must not use yf.Ticker"
    assert "yf.download" not in src, "signals/__init__.py must not use yf.download"


def test_portfolio_optimizer_no_yfinance():
    src = _module_src("portfolio_optimizer.py")
    assert "import yfinance" not in src, "portfolio_optimizer.py must not import yfinance"
    assert "yf.download" not in src, "portfolio_optimizer.py must not use yf.download"


def test_bot_no_yfinance():
    src = _module_src("bot.py")
    assert "yfinance" not in src, "bot.py must not reference yfinance"


def test_orders_core_no_yfinance():
    src = _module_src("orders_core.py")
    assert "yfinance" not in src, "orders_core.py must not reference yfinance"


def test_catalyst_screen_no_yfinance():
    src = _module_src("signals/catalyst_screen.py")
    assert "import yfinance" not in src, "signals/catalyst_screen.py must not import yfinance"
    assert "yf.Ticker" not in src, "signals/catalyst_screen.py must not use yf.Ticker"


def test_options_anomaly_no_yfinance():
    src = _module_src("signals/options_anomaly.py")
    assert "import yfinance" not in src, "signals/options_anomaly.py must not import yfinance"
    assert "yf.Ticker" not in src, "signals/options_anomaly.py must not use yf.Ticker"
    assert "yf.fast_info" not in src, "signals/options_anomaly.py must not use yf.fast_info"


def test_factor_analysis_no_yfinance():
    src = _module_src("scripts/factor_analysis.py")
    assert "import yfinance" not in src, "scripts/factor_analysis.py must not import yfinance"
    assert "yf.download" not in src, "scripts/factor_analysis.py must not use yf.download"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Broad active-source scan — catches future re-introduction
# ═══════════════════════════════════════════════════════════════════════════════


# Explicit approved exceptions — yfinance permitted in these files only.
# Each exception must have a documented reason in the file's module docstring.
_YFINANCE_APPROVED = {
    "futures_data.py",                        # ES=F / NQ=F — not available via Alpaca or FMP Premium
    "verify_customer_event_tape_safety.py",   # false positive — string appears in docstring only, no actual import
}


def test_no_yfinance_import_in_any_active_source_module():
    """
    Walk every .py in active source dirs (excluding archive, tests, docs,
    Chief-Decifer-recovered). Assert none contain an active yfinance import,
    except files listed in _YFINANCE_APPROVED.
    """
    violations: list[str] = []
    for rel, abs_path in _walk_active_py_files():
        if abs_path.name in _YFINANCE_APPROVED:
            continue
        try:
            src = abs_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if _has_yfinance_import(src):
            violations.append(rel)

    assert not violations, (
        "The following active source files import yfinance (not approved):\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
        + "\n\nRemove the import and replace with Alpaca/FMP, or archive the file."
    )


def test_signals_module_has_no_yf_binding():
    """signals/__init__.py must not bind 'yf' as a module-level yfinance alias."""
    src = _module_src("signals/__init__.py")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "import yfinance as yf" not in stripped, (
            "signals/__init__.py binds 'yf' to yfinance — must be removed"
        )
