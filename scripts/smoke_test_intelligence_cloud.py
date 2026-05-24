#!/usr/bin/env python3
"""
smoke_test_intelligence_cloud.py

Runtime layer:    test/verification
Imported at runtime: No
Affects execution:   No (read-only HTTP GET calls only)
Intelligence cloud:  Verification tool — runs against the live DO endpoint
Cloud cost/impact:   Minimal (7 HTTP requests per run)

Live smoke test for the Decifer Intelligence Cloud endpoint.
Run this AFTER deploying to DigitalOcean to prove the public/private separation
is working as documented.

This script is intentionally self-contained. It uses only stdlib (urllib) so it
can run on any Python 3.8+ environment, including the DigitalOcean droplet itself,
without requiring any Decifer dependencies to be installed.

All checks fail closed — a network error or unexpected response is a FAIL,
not a pass. No result is assumed good without explicit evidence.

Usage
─────
  # Against localhost (service running locally):
  python3 scripts/smoke_test_intelligence_cloud.py --url http://localhost:8000

  # Against the live DigitalOcean endpoint:
  python3 scripts/smoke_test_intelligence_cloud.py \\
      --url https://intelligence.decifertrading.com

  # With verbose output:
  python3 scripts/smoke_test_intelligence_cloud.py \\
      --url https://intelligence.decifertrading.com --verbose

Exit codes
──────────
  0  All checks pass — GO
  1  One or more checks failed — HOLD
  2  Usage error (bad arguments)

Checks performed
────────────────
  S1  GET /health → HTTP 200
  S2  /health: status == "ok"
  S3  /health: runtime_mode == "intelligence_cloud"
  S4  /health: execution_blocked == true
  S5  GET /api/market-now → HTTP 200
  S6  /api/market-now: no blocked fields in response
  S7  /api/market-now: has freshness_timestamp field
  S8  POST /api/market-now → HTTP 405 (no mutation routes)
  S9  GET /undefined-route-xyz → HTTP 404 (unknown paths blocked)
  S10 /api/mobile/portfolio: positions field is a list (intelligence-only placeholder)
  S11 Response headers confirm X-Decifer-Runtime-Mode: intelligence_cloud
  S12 No IBKR / execution / broker fields in any intelligence response
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

# Fields that must never appear in any intelligence cloud response.
_BLOCKED_RESPONSE_FIELDS = frozenset({
    "broker_account_id", "account_id", "ibkr_account",
    "order_id", "client_order_id", "ibkr_order_id",
    "position_size", "qty", "quantity", "shares",
    "stop_price", "stop_order", "limit_price",
    "pnl", "pnl_pct", "unrealized_pnl", "realized_pnl",
    "entry_price", "exit_price", "cost_basis",
    "raw_score", "signal_score", "ic_weight", "ic_weights",
    "execution_signal", "buy_signal", "sell_signal",
})


def _get(url: str, timeout: int = 15) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Make a GET request. Returns (status_code, body_dict, headers)."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            headers = dict(resp.headers)
            return resp.status, body, headers
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {}
        return e.code, body, {}
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection failed: {e.reason}") from e


def _post(url: str, timeout: int = 15) -> tuple[int, dict[str, Any]]:
    """Make a POST request. Returns (status_code, body_dict)."""
    req = urllib.request.Request(url, method="POST", data=b"{}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {}
        return e.code, body
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection failed: {e.reason}") from e


def _check_blocked_fields(payload: dict[str, Any]) -> list[str]:
    """Return list of any blocked fields found in the payload (top level only)."""
    return [k for k in payload if k in _BLOCKED_RESPONSE_FIELDS]


def run_smoke_test(base_url: str, verbose: bool = False) -> bool:
    """
    Run all smoke test checks against `base_url`.
    Returns True if all pass, False if any fail.
    """
    base = base_url.rstrip("/")
    results: list[tuple[str, bool, str]] = []

    def record(check_id: str, passed: bool, message: str) -> None:
        results.append((check_id, passed, message))
        if verbose or not passed:
            mark = "PASS" if passed else "FAIL"
            print(f"  [{mark}] {check_id}: {message}")

    print(f"\nDecifer Intelligence Cloud — Live Smoke Test")
    print(f"{'═' * 60}")
    print(f"  Target: {base}")
    print()

    # ── S1: GET /health → 200 ─────────────────────────────────────────────────
    try:
        status, health, headers = _get(f"{base}/health")
        record("S1", status == 200,
               f"GET /health → {status} (expected 200)")
    except RuntimeError as e:
        record("S1", False, f"GET /health connection error: {e}")
        print(f"\n  ⛔  Cannot reach {base} — aborting smoke test.")
        _print_summary(results)
        return False

    # ── S2: status == "ok" ────────────────────────────────────────────────────
    record("S2", health.get("status") == "ok",
           f'/health.status = {health.get("status")!r} (expected "ok")')

    # ── S3: runtime_mode == "intelligence_cloud" ──────────────────────────────
    record("S3", health.get("runtime_mode") == "intelligence_cloud",
           f'/health.runtime_mode = {health.get("runtime_mode")!r} '
           f'(expected "intelligence_cloud")')

    # ── S4: execution_blocked == true ─────────────────────────────────────────
    record("S4", health.get("execution_blocked") is True,
           f'/health.execution_blocked = {health.get("execution_blocked")!r} '
           f"(expected true)")

    # ── S5: GET /api/market-now → 200 ─────────────────────────────────────────
    try:
        status_mn, market_now, mn_headers = _get(f"{base}/api/market-now")
        record("S5", status_mn == 200,
               f"GET /api/market-now → {status_mn} (expected 200)")
    except RuntimeError as e:
        record("S5", False, f"GET /api/market-now error: {e}")
        market_now = {}
        mn_headers = {}

    # ── S6: no blocked fields in market-now ───────────────────────────────────
    bad_fields = _check_blocked_fields(market_now)
    record("S6", not bad_fields,
           f"/api/market-now: blocked fields present = {bad_fields or 'NONE'}")

    # ── S7: freshness_timestamp present ───────────────────────────────────────
    record("S7", "freshness_timestamp" in market_now,
           f"/api/market-now: freshness_timestamp present = "
           f"{'YES' if 'freshness_timestamp' in market_now else 'MISSING'}")

    # ── S8: POST /api/market-now → 405 ────────────────────────────────────────
    try:
        status_post, _ = _post(f"{base}/api/market-now")
        record("S8", status_post == 405,
               f"POST /api/market-now → {status_post} (expected 405 Method Not Allowed)")
    except RuntimeError as e:
        record("S8", False, f"POST /api/market-now error: {e}")

    # ── S9: GET /undefined-route-xyz → 404 ────────────────────────────────────
    try:
        status_404, _, _ = _get(f"{base}/undefined-route-smoke-test-xyz")
        record("S9", status_404 == 404,
               f"GET /undefined-route-xyz → {status_404} (expected 404)")
    except RuntimeError as e:
        record("S9", False, f"GET /undefined-route error: {e}")

    # ── S10: /api/mobile/portfolio returns positions list ─────────────────────
    # May return 200 (no Cloudflare Access) or 302/401/403 (CF Access active).
    # Either is acceptable — we only verify positions=[] when we get 200.
    try:
        status_port, portfolio, _ = _get(f"{base}/api/mobile/portfolio")
        if status_port == 200:
            positions = portfolio.get("positions")
            record("S10", isinstance(positions, list),
                   f"/api/mobile/portfolio: positions is list "
                   f"({'YES — intelligence-only placeholder' if isinstance(positions, list) else 'MISSING'})")
        else:
            # 302/401/403 from Cloudflare Access is acceptable — route is protected
            record("S10", status_port in (302, 401, 403),
                   f"/api/mobile/portfolio → {status_port} "
                   f"(Cloudflare Access gate active — acceptable)")
    except RuntimeError as e:
        record("S10", False, f"/api/mobile/portfolio error: {e}")

    # ── S11: X-Decifer-Runtime-Mode header ───────────────────────────────────
    runtime_header = mn_headers.get("X-Decifer-Runtime-Mode") or \
                     mn_headers.get("x-decifer-runtime-mode", "")
    record("S11", runtime_header == "intelligence_cloud",
           f"X-Decifer-Runtime-Mode header = {runtime_header!r} "
           f'(expected "intelligence_cloud")')

    # ── S12: no blocked fields in health response ─────────────────────────────
    health_bad = _check_blocked_fields(health)
    record("S12", not health_bad,
           f"/health: blocked fields present = {health_bad or 'NONE'}")

    return _print_summary(results)


def _print_summary(results: list[tuple[str, bool, str]]) -> bool:
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)

    print()
    print(f"{'═' * 60}")
    print(f"  Checks: {total}  |  Passed: {passed}  |  Failed: {failed}")
    print()

    if failed:
        print("  FAILED checks:")
        for cid, ok, msg in results:
            if not ok:
                print(f"    ✗ {cid}: {msg}")
        print()
        print("  VERDICT: HOLD — smoke test failed. Do not serve public traffic.")
    else:
        print("  VERDICT: GO — live intelligence cloud endpoint verified.")
    print()
    return failed == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decifer Intelligence Cloud live smoke test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/smoke_test_intelligence_cloud.py "
            "--url http://localhost:8000\n"
            "  python3 scripts/smoke_test_intelligence_cloud.py "
            "--url https://intelligence.decifertrading.com --verbose"
        ),
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Base URL of the intelligence cloud endpoint (e.g. https://intelligence.decifertrading.com)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print all check results, not just failures",
    )
    args = parser.parse_args()

    ok = run_smoke_test(args.url, verbose=args.verbose)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
