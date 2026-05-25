#!/usr/bin/env python3
"""
verify_customer_event_tape_safety.py — Sprint M11A + M11B safety verifier.

Scans the repository and fails if any of the following invariants are
violated:

  E1  customer_event_tape is imported by an execution module
  E2  customer_event_tape is imported by universe_builder.py for live scoring
  E3  customer_event_tape is imported by handoff_reader.py for live trading
  E4  market_now_reconciler is imported by anything other than
       market_now_builder.py, tests, or scripts (the verifier itself)
  E5  customer_event_classifier is imported by anything other than
       customer_event_tape.py, tests, or scripts
  E6  yfinance is imported by any new M11A module
  E7  Mac-only absolute paths leak into any of the three new modules
  E8  data/intelligence/customer_event_tape.json fails saas safety walk

  C1  /customer route imports an operator view
       (ApexView, TodayView, HoldingsView, ActivityView, ResultsView)
  C2  customerApi.ts references NEXT_PUBLIC_BOT_API_URL, a relative
       /api/market-now URL, or operator api.ts
  C3  customerApi.ts is missing the safe default Intelligence API URL
  C4  customer-facing TSX code exposes private trading terms
  C5  customer route introduces a mutation method (POST, PUT, PATCH, DELETE)

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


# ─── M11B TypeScript / customer-surface checks ───────────────────────────────

_MOBILE_SRC = _REPO_ROOT / "mobile" / "src"

_OPERATOR_VIEWS = frozenset({
    "ApexView", "TodayView", "HoldingsView", "ActivityView", "ResultsView",
})

# Private trading terms that must not appear in customer-facing rendering code.
# Chosen to be high-confidence — specific enough to avoid false positives on
# common English words or CSS property names.
_PRIVATE_RENDERING_TERMS = (
    "ibkr", "ibroker",
    "signal_score", "ic_weight", "ic_weights",
    "stop_loss", "stop loss",
    "buy_signal", "sell_signal", "execution_signal",
    "trade_recommendation", "execution_readiness",
    "unrealized_pnl", "realized_pnl", "daily_pnl",
    "broker_account", "account_id", "ibkr_account", "ibkr_order",
    "buy now", "sell now",
)

_SAFE_INTELLIGENCE_URL = "https://intelligence.decifertrading.com"


def _read_ts(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def check_customer_surface_imports() -> list[str]:
    """C1 — /customer route does not import operator views."""
    violations: list[str] = []
    customer_dir = _MOBILE_SRC / "app" / "customer"
    if not customer_dir.exists():
        return []
    for path in customer_dir.rglob("*.tsx"):
        src = _read_ts(path)
        for view in sorted(_OPERATOR_VIEWS):
            if view in src:
                violations.append(
                    f"[C1] {path.relative_to(_REPO_ROOT)}: customer route "
                    f"imports or references operator view {view!r}."
                )
    return violations


def check_customer_api_ts() -> list[str]:
    """C2, C3 — customerApi.ts uses Intelligence API, not bot dashboard."""
    violations: list[str] = []
    path = _MOBILE_SRC / "lib" / "customerApi.ts"
    if not path.exists():
        violations.append("[C2] mobile/src/lib/customerApi.ts not found.")
        return violations
    src = _read_ts(path)
    if "NEXT_PUBLIC_BOT_API_URL" in src:
        violations.append(
            "[C2] customerApi.ts references NEXT_PUBLIC_BOT_API_URL. "
            "Customer API must not reference the bot dashboard env var."
        )
    # Relative /api/market-now as a bare string literal (not as a URL suffix)
    if '"/api/market-now"' in src or "'/api/market-now'" in src:
        violations.append(
            "[C2] customerApi.ts contains a bare relative URL '/api/market-now'. "
            "Must use absolute URL via getIntelligenceApiBase()."
        )
    # Import of operator api.ts
    for bad_import in ('from "./api"', 'from "@/lib/api"', "from '../lib/api'", 'from "./api.ts"'):
        if bad_import in src:
            violations.append(
                f"[C2] customerApi.ts imports operator api.ts ({bad_import!r}). "
                "Customer API must be isolated from the bot dashboard client."
            )
    # C3: safe default URL must be present
    if _SAFE_INTELLIGENCE_URL not in src:
        violations.append(
            f"[C3] customerApi.ts is missing the safe default Intelligence API URL "
            f"({_SAFE_INTELLIGENCE_URL!r}). Empty NEXT_PUBLIC_INTELLIGENCE_API_URL "
            "must fall back to this constant."
        )
    return violations


def check_no_private_terms_in_customer_tsx() -> list[str]:
    """C4 — customer-facing TSX does not expose private trading internals."""
    violations: list[str] = []
    files_to_check: list[Path] = []
    customer_dir = _MOBILE_SRC / "app" / "customer"
    if customer_dir.exists():
        files_to_check.extend(customer_dir.rglob("*.tsx"))
    market_view = _MOBILE_SRC / "views" / "MarketView.tsx"
    if market_view.exists():
        files_to_check.append(market_view)
    for path in files_to_check:
        src = _read_ts(path).lower()
        for term in _PRIVATE_RENDERING_TERMS:
            if term.lower() in src:
                violations.append(
                    f"[C4] {path.relative_to(_REPO_ROOT)}: customer-facing "
                    f"code contains private trading term {term!r}."
                )
    return violations


def check_no_mutation_methods_in_customer_routes() -> list[str]:
    """C5 — customer routes do not export POST/PUT/PATCH/DELETE handlers."""
    violations: list[str] = []
    customer_dir = _MOBILE_SRC / "app" / "customer"
    if not customer_dir.exists():
        return []
    for path in customer_dir.rglob("*.ts"):
        src = _read_ts(path)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            if (f"export async function {method}" in src
                    or f"export function {method}" in src):
                violations.append(
                    f"[C5] {path.relative_to(_REPO_ROOT)}: customer route "
                    f"exports mutation handler {method!r}. Customer routes are GET-only."
                )
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
    # M11B customer-surface checks
    all_violations.extend(check_customer_surface_imports())
    all_violations.extend(check_customer_api_ts())
    all_violations.extend(check_no_private_terms_in_customer_tsx())
    all_violations.extend(check_no_mutation_methods_in_customer_routes())

    print("Decifer Sprint M11A/M11B — Customer safety verifier")
    print(f"  Scanned: {len(files)} Python files")
    print(f"  New M11A modules: {', '.join(_NEW_M11A_MODULES)}")
    print(f"  Allowed tape importers: {sorted(_ALLOWED_TAPE_IMPORTERS)}")

    if all_violations:
        print(f"\n  FAILED — {len(all_violations)} violation(s):\n")
        for v in all_violations:
            print(f"    {v}")
        print()
        return 1

    print("\n  PASSED — all Sprint M11A/M11B safety invariants hold.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
