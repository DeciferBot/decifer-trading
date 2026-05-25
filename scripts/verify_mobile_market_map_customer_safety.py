#!/usr/bin/env python3
"""
verify_mobile_market_map_customer_safety.py
============================================
Sprint M11B Phase 1A.1 safety verifier.

Checks that the customer mobile Market Map entry surface is fully decoupled
from all private bot routes, broker state, execution data, and operator views.

The customer entry surface is: mobile/src/app/customer/page.tsx
Supporting files:  mobile/src/views/MarketView.tsx
                   mobile/src/lib/customerApi.ts

Exit 0 = all checks pass (SAFE).
Exit 1 = one or more checks failed (UNSAFE — do not deploy as customer product).
"""
from __future__ import annotations

import os
import re
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOBILE_SRC = os.path.join(_BASE, "mobile", "src")

_CUSTOMER_PAGE = os.path.join(_MOBILE_SRC, "app", "customer", "page.tsx")
_MARKET_VIEW   = os.path.join(_MOBILE_SRC, "views", "MarketView.tsx")
_CUSTOMER_API  = os.path.join(_MOBILE_SRC, "lib", "customerApi.ts")


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

    customer_page = _read(_CUSTOMER_PAGE)
    market_view   = _read(_MARKET_VIEW)
    customer_api  = _read(_CUSTOMER_API)

    if not customer_page:
        print(f"ERROR: {_CUSTOMER_PAGE} not found — customer entry surface missing")
        return 1
    if not market_view:
        print(f"ERROR: {_MARKET_VIEW} not found")
        return 1
    if not customer_api:
        print(f"ERROR: {_CUSTOMER_API} not found")
        return 1

    print("=" * 62)
    print("M11B Phase 1A.1 — Customer Market Map Safety Verifier")
    print("=" * 62)

    # ── Customer entry surface: operator view containment ────────────────────

    tag = " 1. Customer entry does not render ApexView"
    if "ApexView" in customer_page:
        _fail(f"{tag}  [ApexView found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = " 2. Customer entry does not render HoldingsView"
    if "HoldingsView" in customer_page:
        _fail(f"{tag}  [HoldingsView found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = " 3. Customer entry does not render ActivityView"
    if "ActivityView" in customer_page:
        _fail(f"{tag}  [ActivityView found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = " 4. Customer entry does not render ResultsView"
    if "ResultsView" in customer_page:
        _fail(f"{tag}  [ResultsView found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = " 5. Customer entry does not render TodayView (uses /api/state)"
    if "TodayView" in customer_page:
        _fail(f"{tag}  [TodayView found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = " 6. Customer entry exposes only MarketView"
    if "MarketView" not in customer_page:
        _fail(f"{tag}  [MarketView not found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── Customer entry surface: private API isolation ────────────────────────

    tag = " 7. Customer entry does not import @/lib/api"
    if re.search(r"""from\s+['"]@/lib/api['"]""", customer_page):
        _fail(f"{tag}  [found 'from @/lib/api' in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = " 8. Customer entry does not reference /api/state"
    if "/api/state" in customer_page:
        _fail(f"{tag}  [found '/api/state' in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = " 9. Customer entry does not reference /api/pm"
    if "/api/pm" in customer_page:
        _fail(f"{tag}  [found '/api/pm' in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = "10. Customer entry does not reference /api/analytics"
    if "/api/analytics" in customer_page:
        _fail(f"{tag}  [found '/api/analytics' in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = "11. Customer entry does not reference 192.168.1.221"
    if "192.168.1.221" in customer_page:
        _fail(f"{tag}  [LAN IP found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = "12. Customer entry does not reference localhost:8080"
    if "localhost:8080" in customer_page:
        _fail(f"{tag}  [localhost:8080 found in customer/page.tsx]")
        failures += 1
    else:
        _pass(tag)

    # ── MarketView: disclaimer and private isolation ──────────────────────────

    tag = "13. MarketView does not import @/lib/api"
    if re.search(r"""from\s+['"]@/lib/api['"]""", market_view):
        _fail(f"{tag}  [found 'from @/lib/api' in MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = "14. MarketView does not reference private bot routes"
    bad_routes = [r for r in ["/api/state", "/api/pm", "/api/analytics",
                               "192.168.1.221", "localhost:8080"]
                  if r in market_view]
    if bad_routes:
        _fail(f"{tag}  [found: {bad_routes}]")
        failures += 1
    else:
        _pass(tag)

    tag = "15. MarketView includes required disclaimer copy"
    if "Market intelligence only" not in market_view or "No trade execution" not in market_view:
        _fail(f"{tag}  [disclaimer text missing from MarketView.tsx]")
        failures += 1
    else:
        _pass(tag)

    tag = "16. MarketView does not display account/P&L/position/order fields"
    forbidden_fields = [
        "portfolio_value", "daily_pnl", "total_pnl", "unrealised_pnl",
        "position_size", "scan_count", "last_decision",
        "order_id", "pm_action", "thesis_status", "action_type",
        "ibkr", "broker_account", "execute_buy", "execute_sell",
        "BotState", "PMDecision", "HealthReport",
    ]
    bad_fields = [f for f in forbidden_fields if f in market_view]
    if bad_fields:
        _fail(f"{tag}  [found: {bad_fields}]")
        failures += 1
    else:
        _pass(tag)

    # ── customerApi.ts: route isolation ──────────────────────────────────────

    tag = "17. customerApi.ts uses NEXT_PUBLIC_INTELLIGENCE_API_URL"
    if "NEXT_PUBLIC_INTELLIGENCE_API_URL" not in customer_api:
        _fail(f"{tag}  [NEXT_PUBLIC_INTELLIGENCE_API_URL missing from customerApi.ts]")
        failures += 1
    else:
        _pass(tag)

    tag = "18. customerApi.ts only fetches /api/market-now"
    api_routes = re.findall(r"""['"](/api/[^'"]+)['"]""", customer_api)
    non_market_now = [r for r in api_routes if r != "/api/market-now"]
    if non_market_now:
        _fail(f"{tag}  [unexpected routes found: {non_market_now}]")
        failures += 1
    else:
        _pass(tag)

    tag = "19. customerApi.ts does not reference private bot routes"
    forbidden_api = ["/api/state", "/api/pm", "/api/analytics",
                     "/api/rotation", "/api/health",
                     "192.168.1.221", "localhost:8080", "BOT_API_URL"]
    bad_api = [r for r in forbidden_api if r in customer_api]
    if bad_api:
        _fail(f"{tag}  [found: {bad_api}]")
        failures += 1
    else:
        _pass(tag)

    # ── Summary ───────────────────────────────────────────────────────────────
    total = 19
    passed = total - failures
    print("-" * 62)
    if failures == 0:
        print(f"RESULT: ALL CHECKS PASS ({passed}/{total}) — Customer surface is safe.")
    else:
        print(f"RESULT: {failures} CHECK(S) FAILED ({passed}/{total}) — Customer surface is NOT safe.")
    print("=" * 62)
    return failures


if __name__ == "__main__":
    sys.exit(run_checks())
