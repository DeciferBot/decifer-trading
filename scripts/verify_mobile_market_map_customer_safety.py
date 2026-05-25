#!/usr/bin/env python3
"""
verify_mobile_market_map_customer_safety.py
============================================
Sprint M11B Phase 1A safety verifier.

Checks that the customer mobile Market Map surface is decoupled from all
private bot routes, broker state, and execution data.

Exit 0 = all checks pass (SAFE).
Exit 1 = one or more checks failed (UNSAFE — do not deploy as customer product).
"""
from __future__ import annotations

import os
import re
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOBILE_SRC = os.path.join(_BASE, "mobile", "src")

# Paths under test
_MARKET_VIEW = os.path.join(_MOBILE_SRC, "views", "MarketView.tsx")
_CUSTOMER_API = os.path.join(_MOBILE_SRC, "lib", "customerApi.ts")
_PRIVATE_API  = os.path.join(_MOBILE_SRC, "lib", "api.ts")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def _pass(msg: str) -> None:
    print(f"  PASS  {msg}")


def run_checks() -> int:
    failures = 0
    market_view  = _read(_MARKET_VIEW)
    customer_api = _read(_CUSTOMER_API)

    if not market_view:
        print(f"ERROR: {_MARKET_VIEW} not found")
        return 1
    if not customer_api:
        print(f"ERROR: {_CUSTOMER_API} not found")
        return 1

    print("=" * 60)
    print("M11B Phase 1A — Customer Market Map Safety Verifier")
    print("=" * 60)

    # ── Check 1: MarketView does not import private bot api client ──────────
    tag = "1. MarketView does not import @/lib/api"
    # Allow any reference to @/lib/api — it must not be imported
    if re.search(r"""from\s+['"]@/lib/api['"]""", market_view):
        _fail(f"{tag}  [found 'from @/lib/api' in MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 2: MarketView does not reference /api/state ──────────────────
    tag = "2. MarketView does not reference /api/state"
    if "/api/state" in market_view:
        _fail(f"{tag}  [found '/api/state' in MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 3: MarketView does not reference /api/pm ─────────────────────
    tag = "3. MarketView does not reference /api/pm"
    if "/api/pm" in market_view:
        _fail(f"{tag}  [found '/api/pm' in MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 4: MarketView does not reference /api/analytics ──────────────
    tag = "4. MarketView does not reference /api/analytics"
    if "/api/analytics" in market_view:
        _fail(f"{tag}  [found '/api/analytics' in MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 5: MarketView does not reference private LAN IP ──────────────
    tag = "5. MarketView does not reference 192.168.1.221"
    if "192.168.1.221" in market_view:
        _fail(f"{tag}  [found LAN IP in MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 6: MarketView does not reference localhost:8080 ───────────────
    tag = "6. MarketView does not reference localhost:8080"
    if "localhost:8080" in market_view:
        _fail(f"{tag}  [found localhost:8080 in MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 7: customerApi.ts does not reference private bot routes ───────
    tag = "7. customerApi.ts does not reference private bot routes"
    forbidden_routes = ["/api/state", "/api/pm", "/api/analytics",
                        "/api/rotation", "/api/health", "192.168.1.221",
                        "localhost:8080", "BOT_API_URL"]
    bad = [r for r in forbidden_routes if r in customer_api]
    if bad:
        _fail(f"{tag}  [found: {bad}]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 8: customerApi.ts uses NEXT_PUBLIC_INTELLIGENCE_API_URL ───────
    tag = "8. customerApi.ts uses NEXT_PUBLIC_INTELLIGENCE_API_URL"
    if "NEXT_PUBLIC_INTELLIGENCE_API_URL" not in customer_api:
        _fail(f"{tag}  [NEXT_PUBLIC_INTELLIGENCE_API_URL not found in customerApi.ts]")
        failures += 1
    else:
        _pass(tag)

    # ── Check 9: Disclaimer copy present ────────────────────────────────────
    tag = "9. Customer Market Map includes required disclaimer copy"
    has_intel  = "Market intelligence only" in market_view
    has_no_exec = "No trade execution" in market_view
    if not has_intel:
        _fail(f"{tag}  [missing 'Market intelligence only']")
        failures += 1
    elif not has_no_exec:
        _fail(f"{tag}  [missing 'No trade execution']")
        failures += 1
    else:
        _pass(tag)

    # ── Check 10: No private/execution field names in MarketView ────────────
    tag = "10. MarketView does not display account/P&L/position/order fields"
    # These are field names from the private BotState / Position / PMDecision types.
    # A hit means the customer view is referencing broker/execution data.
    forbidden_fields = [
        "portfolio_value", "daily_pnl", "total_pnl", "unrealised_pnl",
        "position_size", "qty", ".entry", ".current",
        "order_id", "pm_action", "thesis_status", "action_type",
        "ibkr", "broker_account",
        "execute_buy", "execute_sell",
        "BotState", "Position", "PMDecision", "HealthReport",
        "scan_count", "last_decision", "last_scan", "paused",
    ]
    bad_fields = [f for f in forbidden_fields if f in market_view]
    if bad_fields:
        _fail(f"{tag}  [found: {bad_fields}]")
        failures += 1
    else:
        _pass(tag)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("-" * 60)
    if failures == 0:
        print(f"RESULT: ALL CHECKS PASS ({10 - failures}/10) — Customer surface is safe.")
    else:
        print(f"RESULT: {failures} CHECK(S) FAILED — Customer surface is NOT safe.")
    print("=" * 60)
    return failures


if __name__ == "__main__":
    sys.exit(run_checks())
