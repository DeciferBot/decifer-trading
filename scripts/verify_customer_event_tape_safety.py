#!/usr/bin/env python3
"""
verify_customer_event_tape_safety.py — Sprint M11A safety verifier.

Scans the repository and fails if any of the following invariants are
violated:

  E1  customer_event_tape is imported by an execution module
       (orders_*, bot_trading, bot_ibkr, apex_orchestrator,
        options_entries, alpaca_news, news_sentinel, pm_*, …)
  E2  customer_event_tape is imported by universe_builder.py for live scoring
  E3  customer_event_tape is imported by handoff_reader.py for live trading
  E4  market_now_reconciler is imported by anything other than
       market_now_builder.py, tests, or scripts (the verifier itself)
  E5  customer_event_classifier is imported by anything other than
       customer_event_tape.py, tests, or scripts
  E6  yfinance is imported by any new M11A module
  E7  Mac-only absolute paths leak into any of the three new modules
  E8  data/intelligence/customer_event_tape.json fails saas safety walk

Exit codes
──────────
  0  No violations
  1  One or more violations detected

Usage
─────
  python3 scripts/verify_customer_event_tape_safety.py
  python3 scripts/verify_customer_event_tape_safety.py --verbose
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from architecture.layer_boundary import (  # noqa: E402
    Layer,
    classify_module_path,
    get_execution_module_names,
)

VERBOSE = "--verbose" in sys.argv

# ─── Paths excluded from scanning ────────────────────────────────────────────

_EXCLUDED_PATH_FRAGMENTS = (
    "/__pycache__/", "/.git/", "/node_modules/", "/venv/", "/.venv/",
    "/site-packages/", "/chief-decifer/", "/Chief-Decifer-recovered/",
    "/worktree-", "/archive/", "/deprecated/", "/homepage/",
    "/.claude/", "/.claire/", "/mobile/",
)

# ─── Allowed importers ──────────────────────────────────────────────────────

# customer_event_tape may be imported by:
#  - intelligence modules: news, catalyst_engine
#  - saas_output: market_now_reconciler, market_now_builder
#  - tests / scripts
_ALLOWED_TAPE_IMPORTERS = frozenset({
    "news",
    "catalyst_engine",
    "market_now_reconciler",
    "market_now_builder",
})

# market_now_reconciler may be imported by:
#  - market_now_builder (its sole production caller)
#  - tests / scripts
_ALLOWED_RECONCILER_IMPORTERS = frozenset({
    "market_now_builder",
})

# customer_event_classifier may be imported by:
#  - customer_event_tape (its sole production caller)
#  - tests / scripts
_ALLOWED_CLASSIFIER_IMPORTERS = frozenset({
    "customer_event_tape",
})

_NEW_M11A_MODULES = ("customer_event_classifier", "customer_event_tape",
                      "market_now_reconciler")

_MAC_ONLY_PATTERNS = ("/Users/", "~/Library/",
                       "/Library/Application Support/", "/private/var/")

_BANNED_NESTED_KEYS = (
    "position_size", "qty", "quantity", "shares",
    "entry_price", "exit_price", "stop_price", "limit_price",
    "take_profit", "stop_order", "market_order",
    "order_id", "client_order_id", "ibkr_order_id",
    "broker_account", "account_id", "ibkr_account",
    "buying_power", "account_value", "portfolio_value",
    "pnl", "unrealized_pnl", "realized_pnl", "cost_basis",
    "daily_pnl", "total_pnl",
    "raw_score", "signal_score", "ic_weight",
    "pm_action", "trade_id", "execution_signal",
    "buy_signal", "sell_signal", "trade_recommendation",
    "execution_readiness", "account_exposure",
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _is_excluded(path: Path) -> bool:
    s = str(path)
    return any(frag in s for frag in _EXCLUDED_PATH_FRAGMENTS)


def _collect_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*.py"):
        if not _is_excluded(p):
            files.append(p)
    return files


def _parse_imports(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def _source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _walk_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


# ─── Checks ─────────────────────────────────────────────────────────────────

def check_tape_importers(files: list[Path]) -> list[str]:
    """E1, E2, E3 — customer_event_tape import discipline."""
    exec_names = get_execution_module_names()
    violations: list[str] = []
    for path in files:
        stem = path.stem
        layer = classify_module_path(path)
        if layer == Layer.TEST_ONLY:
            continue
        if stem == "customer_event_tape":
            continue
        imports = _parse_imports(_source(path))
        if "customer_event_tape" not in imports:
            continue
        if stem in _ALLOWED_TAPE_IMPORTERS:
            continue
        if stem in exec_names:
            violations.append(
                f"[E1] {path.relative_to(_REPO_ROOT)}: execution module "
                f"{stem!r} imports customer_event_tape directly. "
                "Use news.record_article_for_customer_tape (intelligence-layer "
                "bridge) instead."
            )
        elif stem == "universe_builder":
            violations.append(
                f"[E2] {path.relative_to(_REPO_ROOT)}: universe_builder must "
                "not import customer_event_tape for live scoring. "
                "Event Tape is advisory customer intelligence only."
            )
        elif stem == "handoff_reader":
            violations.append(
                f"[E3] {path.relative_to(_REPO_ROOT)}: handoff_reader must "
                "not import customer_event_tape for live trading eligibility."
            )
        else:
            violations.append(
                f"[E1] {path.relative_to(_REPO_ROOT)}: module {stem!r} "
                f"(layer={layer.value}) imports customer_event_tape but is "
                "not on the allowed importer list. Add to _ALLOWED_TAPE_IMPORTERS "
                "in the verifier only after explicit Amit approval."
            )
    return violations


def check_reconciler_importers(files: list[Path]) -> list[str]:
    """E4 — market_now_reconciler is helper for market_now_builder only."""
    violations: list[str] = []
    for path in files:
        stem = path.stem
        if stem in ("market_now_reconciler",):
            continue
        layer = classify_module_path(path)
        if layer == Layer.TEST_ONLY:
            continue
        imports = _parse_imports(_source(path))
        if "market_now_reconciler" not in imports:
            continue
        if stem not in _ALLOWED_RECONCILER_IMPORTERS:
            violations.append(
                f"[E4] {path.relative_to(_REPO_ROOT)}: module {stem!r} "
                f"(layer={layer.value}) imports market_now_reconciler — only "
                "market_now_builder.py is allowed to import it. Tests/scripts "
                "are exempt; production callers are not."
            )
    return violations


def check_classifier_importers(files: list[Path]) -> list[str]:
    """E5 — customer_event_classifier is helper for the tape only."""
    violations: list[str] = []
    for path in files:
        stem = path.stem
        if stem == "customer_event_classifier":
            continue
        layer = classify_module_path(path)
        if layer == Layer.TEST_ONLY:
            continue
        imports = _parse_imports(_source(path))
        if "customer_event_classifier" not in imports:
            continue
        if stem not in _ALLOWED_CLASSIFIER_IMPORTERS:
            violations.append(
                f"[E5] {path.relative_to(_REPO_ROOT)}: module {stem!r} "
                f"(layer={layer.value}) imports customer_event_classifier "
                "directly. Only customer_event_tape may. Use the tape's API."
            )
    return violations


_FORBIDDEN_PROVIDER = "yfinance"  # removed in v4.31.1


def check_no_yfinance_in_m11a(files: list[Path]) -> list[str]:
    """E6 — none of the three new modules may pull in the removed provider."""
    violations: list[str] = []
    for path in files:
        if path.stem not in _NEW_M11A_MODULES:
            continue
        imports = _parse_imports(_source(path))
        if _FORBIDDEN_PROVIDER in imports:
            violations.append(
                f"[E6] {path.relative_to(_REPO_ROOT)}: {_FORBIDDEN_PROVIDER!r} "
                f"import found in M11A module {path.stem!r}. This provider is "
                "forbidden in production code (removed in v4.31.1)."
            )
    return violations


def check_no_mac_only_paths(files: list[Path]) -> list[str]:
    """E7 — Mac-only paths in any M11A module break cloud deploy."""
    violations: list[str] = []
    for path in files:
        if path.stem not in _NEW_M11A_MODULES:
            continue
        src = _source(path)
        for pattern in _MAC_ONLY_PATTERNS:
            if pattern in src:
                violations.append(
                    f"[E7] {path.relative_to(_REPO_ROOT)}: Mac-only path "
                    f"pattern {pattern!r} found in M11A module {path.stem!r}. "
                    "Cloud-deployed intelligence modules must not depend on "
                    "Mac-local paths."
                )
    return violations


def check_tape_file_safety() -> list[str]:
    """E8 — the persisted tape file must contain no banned nested keys."""
    violations: list[str] = []
    tape_path = _REPO_ROOT / "data" / "intelligence" / "customer_event_tape.json"
    if not tape_path.exists():
        # No tape on disk → nothing to check (not a failure)
        return []
    try:
        with open(tape_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        violations.append(
            f"[E8] {tape_path.relative_to(_REPO_ROOT)}: tape file is "
            f"unreadable ({exc})."
        )
        return violations
    for key in _walk_keys(data):
        if not isinstance(key, str):
            continue
        kl = key.lower()
        for banned in _BANNED_NESTED_KEYS:
            if banned in kl:
                violations.append(
                    f"[E8] {tape_path.relative_to(_REPO_ROOT)}: tape contains "
                    f"banned nested key {key!r} (matched {banned!r}). The "
                    "Event Tape must be customer-safe at every level."
                )
                break
    return violations


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    files = _collect_python_files(_REPO_ROOT)

    all_violations: list[str] = []
    all_violations.extend(check_tape_importers(files))
    all_violations.extend(check_reconciler_importers(files))
    all_violations.extend(check_classifier_importers(files))
    all_violations.extend(check_no_yfinance_in_m11a(files))
    all_violations.extend(check_no_mac_only_paths(files))
    all_violations.extend(check_tape_file_safety())

    print("Decifer Sprint M11A — Customer Event Tape safety verifier")
    print(f"  Scanned: {len(files)} Python files")
    print(f"  New M11A modules: {', '.join(_NEW_M11A_MODULES)}")
    print(f"  Allowed tape importers: {sorted(_ALLOWED_TAPE_IMPORTERS)}")

    if all_violations:
        print(f"\n  FAILED — {len(all_violations)} violation(s):\n")
        for v in all_violations:
            print(f"    {v}")
        print()
        return 1

    print("\n  PASSED — all Sprint M11A safety invariants hold.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
