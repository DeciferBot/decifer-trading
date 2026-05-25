#!/usr/bin/env python3
"""
verify_customer_mobile_ui_safety.py — Sprint M11B.4 customer surface verifier.

Scans the mobile Next.js app and fails if any customer-surface invariant is violated.

Checks
──────
  UI1   BottomNav is not imported by the /customer route or customer views
  UI2   ApexView is not imported by the /customer route or customer views
  UI3   HoldingsView, PortfolioView, ActivityView, ResultsView not in customer surface
  UI4   M11B.4 build marker exists in customer-facing code
  UI5   Tabs exist for Today, Theme Map, Signals, Universe
  UI6   Visual drill-down exists (name detail panel)
  UI7   Breadcrumb or back-navigation exists
  UI8   Customer disclaimer exists
  UI9   Data freshness state exists
  UI10  Stale/unavailable state exists
  UI11  Forbidden trading terms absent from customer-surface source files
  UI12  No mock data presented as live data in customer route
  UI13  CustomerApp is the top-level customer component (not old MarketView + BottomNav shell)
  UI14  customer/page.tsx does not mount BottomNav or any operator tab view

Forbidden terms scanned in customer-surface source files
─────────────────────────────────────────────────────────
  "BottomNav", "ApexView", "HoldingsView", "PortfolioView", "ActivityView",
  "ResultsView", "High conviction", "trade-ready", "entry confirms",
  "position or swing", "IBKR", "broker", "execution",
  "BottomNav" (import), "P&L"

  Note: "order" and "fill" are intentionally excluded because they appear
  in non-trading contexts (e.g. "in order to", "fulfilled"). The check targets
  unambiguous internal trading/operator terms only.

Exit codes
──────────
  0  All invariants pass
  1  One or more violations detected

Usage
─────
  python3 scripts/verify_customer_mobile_ui_safety.py
  python3 scripts/verify_customer_mobile_ui_safety.py --verbose
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
MOBILE_ROOT = REPO_ROOT / "mobile"

# Files that constitute the customer surface (source code only)
CUSTOMER_SURFACE_PATHS = [
    MOBILE_ROOT / "src" / "app" / "customer" / "page.tsx",
    MOBILE_ROOT / "src" / "app" / "layout.tsx",
    MOBILE_ROOT / "src" / "app" / "page.tsx",
    MOBILE_ROOT / "src" / "views" / "CustomerApp.tsx",
    MOBILE_ROOT / "src" / "views" / "TodayTab.tsx",
    MOBILE_ROOT / "src" / "views" / "ThemeMapTab.tsx",
    MOBILE_ROOT / "src" / "views" / "SignalsTab.tsx",
    MOBILE_ROOT / "src" / "views" / "UniverseTab.tsx",
    MOBILE_ROOT / "src" / "views" / "NameDetailPanel.tsx",
]

# Operator views that must NOT appear in customer surface imports
OPERATOR_VIEWS = [
    "BottomNav",
    "ApexView",
    "HoldingsView",
    "PortfolioView",
    "ActivityView",
    "ResultsView",
    "TodayView",   # old internal TodayView (not TodayTab)
    "PositionSheet",
]

# Forbidden UI-visible terms — unambiguous internal/trading language
FORBIDDEN_TERMS = [
    "BottomNav",
    "ApexView",
    "HoldingsView",
    "PortfolioView",
    "ActivityView",
    "ResultsView",
    "High conviction",
    "trade-ready",
    "entry confirms",
    "position or swing",
    "IBKR",
    r"\bbroker\b",
    r"\bexecution\b",
    r"\bP&L\b",
]

# Required content signals — at least one of these must appear in CustomerApp
REQUIRED_TABS = ["Today", "Theme Map", "Signals", "Universe"]
REQUIRED_MARKERS = ["M11B.4"]
REQUIRED_FEATURES = {
    "breadcrumb or back-nav": ["Breadcrumb", "breadcrumb", "← ", "onClose", "ChevronRight"],
    "disclaimer":             ["Not financial advice", "No trade execution", "market intelligence only"],
    "data freshness":         ["freshness", "Freshness", "Fresh", "Stale", "Delayed"],
    "stale/unavailable":      ["Stale", "stale", "unavailable", "temporarily unavailable", "Degraded"],
    "name detail panel":      ["NameDetailPanel", "name detail", "Detail"],
    "drill-down":             ["onThemeSelect", "onNameSelect", "goToTheme"],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def read_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

def strip_comments(src: str) -> str:
    # Remove // line comments and /* */ block comments
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return src

def has_import(src: str, symbol: str) -> bool:
    return bool(re.search(rf'\bimport\b[^;]*\b{re.escape(symbol)}\b', src))

def find_term(src: str, pattern: str) -> list[str]:
    try:
        matches = re.findall(pattern, src, re.IGNORECASE)
        return matches
    except re.error:
        return re.findall(re.escape(pattern), src, re.IGNORECASE)

# ── Violation collector ───────────────────────────────────────────────────────

class Verifier:
    def __init__(self, verbose: bool = False) -> None:
        self.violations: list[str] = []
        self.passes: list[str] = []
        self.verbose = verbose

    def fail(self, check: str, detail: str) -> None:
        self.violations.append(f"  FAIL [{check}] {detail}")

    def ok(self, check: str, detail: str = "") -> None:
        msg = f"  PASS [{check}]" + (f" {detail}" if detail else "")
        self.passes.append(msg)

    def warn(self, check: str, detail: str) -> None:
        # A warning doesn't fail the build but prints if verbose
        if self.verbose:
            print(f"  WARN [{check}] {detail}")

# ── Check implementations ─────────────────────────────────────────────────────

def check_ui1_no_bottomnav(v: Verifier, sources: dict[str, str]) -> None:
    for path, src in sources.items():
        if has_import(src, "BottomNav"):
            v.fail("UI1", f"BottomNav imported in customer surface file: {path}")
            return
    v.ok("UI1", "BottomNav not imported by any customer surface file")

def check_ui2_no_apexview(v: Verifier, sources: dict[str, str]) -> None:
    for path, src in sources.items():
        if has_import(src, "ApexView"):
            v.fail("UI2", f"ApexView imported in customer surface file: {path}")
            return
    v.ok("UI2", "ApexView not imported by any customer surface file")

def check_ui3_no_operator_views(v: Verifier, sources: dict[str, str]) -> None:
    bad = [v_name for v_name in OPERATOR_VIEWS if any(
        has_import(src, v_name) for src in sources.values()
    )]
    if bad:
        v.fail("UI3", f"Operator views imported in customer surface: {bad}")
    else:
        v.ok("UI3", "No operator views imported in customer surface")

def check_ui4_build_marker(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    for marker in REQUIRED_MARKERS:
        if marker not in combined:
            v.fail("UI4", f"Build marker '{marker}' not found in customer surface")
            return
    v.ok("UI4", f"Build marker {REQUIRED_MARKERS} present")

def check_ui5_tabs(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    missing = [tab for tab in REQUIRED_TABS if tab not in combined]
    if missing:
        v.fail("UI5", f"Required tabs missing from customer surface: {missing}")
    else:
        v.ok("UI5", f"All required tabs present: {REQUIRED_TABS}")

def check_ui6_name_detail(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    if "NameDetailPanel" not in combined:
        v.fail("UI6", "NameDetailPanel not found in customer surface")
    else:
        v.ok("UI6", "NameDetailPanel present")

def check_ui7_breadcrumb(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    signals = REQUIRED_FEATURES["breadcrumb or back-nav"]
    if not any(s in combined for s in signals):
        v.fail("UI7", f"No breadcrumb/back-nav signal found. Expected one of: {signals}")
    else:
        v.ok("UI7", "Breadcrumb/back-navigation present")

def check_ui8_disclaimer(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    signals = REQUIRED_FEATURES["disclaimer"]
    if not any(s.lower() in combined.lower() for s in signals):
        v.fail("UI8", f"Disclaimer not found. Expected one of: {signals}")
    else:
        v.ok("UI8", "Customer disclaimer present")

def check_ui9_freshness(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    signals = REQUIRED_FEATURES["data freshness"]
    if not any(s in combined for s in signals):
        v.fail("UI9", f"Data freshness state not found. Expected one of: {signals}")
    else:
        v.ok("UI9", "Data freshness state present")

def check_ui10_stale_state(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    signals = REQUIRED_FEATURES["stale/unavailable"]
    if not any(s in combined for s in signals):
        v.fail("UI10", f"Stale/unavailable state not found. Expected one of: {signals}")
    else:
        v.ok("UI10", "Stale/unavailable state present")

# "No trade execution" is the required customer disclaimer phrase — it is explicitly allowed.
# Only flag standalone "execution" that is NOT part of the disclaimer.
_ALLOWED_EXECUTION_PHRASES = ["no trade execution", "not for trade execution"]

def _is_allowed_execution_context(context: str) -> bool:
    lower = context.lower()
    return any(p in lower for p in _ALLOWED_EXECUTION_PHRASES)

def check_ui11_forbidden_terms(v: Verifier, sources: dict[str, str]) -> None:
    any_violation = False
    for path, raw_src in sources.items():
        # Strip comments before checking so inline comments don't trigger false positives
        src = strip_comments(raw_src)
        for term in FORBIDDEN_TERMS:
            try:
                contexts = re.findall(rf'.{{0,80}}{term}.{{0,80}}', src, re.IGNORECASE)
            except re.error:
                contexts = [src[m.start()-40:m.end()+40] for m in re.finditer(re.escape(term), src, re.IGNORECASE)]
            for ctx in contexts:
                # Skip import statements
                if re.match(r'\s*import\s', ctx.lstrip()):
                    continue
                # Allow "execution" when it appears only in the required disclaimer phrase
                if term == r"\bexecution\b" and _is_allowed_execution_context(ctx):
                    continue
                # Allow "broker" when it appears in "not a broker" or similar negations
                if term == r"\bbroker\b" and "not a broker" in ctx.lower():
                    continue
                v.fail("UI11", f"Forbidden term '{term}' in {path}: {ctx.strip()!r}")
                any_violation = True
                break  # one violation per term per file is enough
    if not any_violation:
        v.ok("UI11", "No forbidden trading/internal terms in customer surface source files")

def check_ui12_no_mock_data(v: Verifier, sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    mock_patterns = ["MOCK_DATA", "mockData", "isMock", "demo_mode", "USE_MOCK"]
    hits = [p for p in mock_patterns if p in combined]
    if hits:
        v.fail("UI12", f"Mock data markers found in customer surface: {hits}")
    else:
        v.ok("UI12", "No mock data markers in customer surface")

def check_ui13_customer_app(v: Verifier, sources: dict[str, str]) -> None:
    customer_page_path = str(MOBILE_ROOT / "src" / "app" / "customer" / "page.tsx")
    src = sources.get(customer_page_path, "")
    if not src:
        v.fail("UI13", "customer/page.tsx not found or empty")
        return
    if "CustomerApp" not in src:
        v.fail("UI13", "customer/page.tsx does not import CustomerApp")
    else:
        v.ok("UI13", "customer/page.tsx imports CustomerApp")

def check_ui14_no_bottomnav_in_customer_page(v: Verifier, sources: dict[str, str]) -> None:
    customer_page_path = str(MOBILE_ROOT / "src" / "app" / "customer" / "page.tsx")
    src = sources.get(customer_page_path, "")
    if not src:
        v.fail("UI14", "customer/page.tsx not found")
        return
    for bad in OPERATOR_VIEWS:
        if bad in src:
            v.fail("UI14", f"{bad} referenced in customer/page.tsx")
            return
    v.ok("UI14", "customer/page.tsx contains no operator tab views")

def check_missing_files(v: Verifier) -> None:
    required = [
        MOBILE_ROOT / "src" / "views" / "CustomerApp.tsx",
        MOBILE_ROOT / "src" / "views" / "TodayTab.tsx",
        MOBILE_ROOT / "src" / "views" / "ThemeMapTab.tsx",
        MOBILE_ROOT / "src" / "views" / "SignalsTab.tsx",
        MOBILE_ROOT / "src" / "views" / "UniverseTab.tsx",
        MOBILE_ROOT / "src" / "views" / "NameDetailPanel.tsx",
        MOBILE_ROOT / "src" / "app" / "customer" / "page.tsx",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        v.fail("FILES", f"Required customer surface files missing: {missing}")
    else:
        v.ok("FILES", "All required customer surface files present")

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    print("=" * 65)
    print("verify_customer_mobile_ui_safety.py — M11B.4")
    print("=" * 65)

    if not MOBILE_ROOT.exists():
        print(f"\nERROR: mobile/ directory not found at {MOBILE_ROOT}")
        return 1

    # Load all customer surface source files
    sources: dict[str, str] = {}
    for path in CUSTOMER_SURFACE_PATHS:
        content = read_file(path)
        if content is not None:
            sources[str(path)] = content
        elif verbose:
            print(f"  INFO: not found (will be checked separately): {path}")

    v = Verifier(verbose=verbose)

    check_missing_files(v)
    check_ui1_no_bottomnav(v, sources)
    check_ui2_no_apexview(v, sources)
    check_ui3_no_operator_views(v, sources)
    check_ui4_build_marker(v, sources)
    check_ui5_tabs(v, sources)
    check_ui6_name_detail(v, sources)
    check_ui7_breadcrumb(v, sources)
    check_ui8_disclaimer(v, sources)
    check_ui9_freshness(v, sources)
    check_ui10_stale_state(v, sources)
    check_ui11_forbidden_terms(v, sources)
    check_ui12_no_mock_data(v, sources)
    check_ui13_customer_app(v, sources)
    check_ui14_no_bottomnav_in_customer_page(v, sources)

    print()
    for msg in v.passes:
        print(msg)

    if v.violations:
        print()
        print("VIOLATIONS FOUND:")
        for msg in v.violations:
            print(msg)
        print()
        print(f"Result: {len(v.violations)} violation(s). NO-GO.")
        return 1

    print()
    print(f"Result: All {len(v.passes)} checks passed. GO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
