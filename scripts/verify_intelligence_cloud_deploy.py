#!/usr/bin/env python3
"""
verify_intelligence_cloud_deploy.py

Pre-deploy verification for the Decifer Intelligence Cloud (DigitalOcean).

Checks performed
────────────────
  E1  DECIFER_RUNTIME_MODE=intelligence_cloud blocks execute_buy
  E2  execute_short blocks in intelligence_cloud mode
  E3  execute_sell blocks in intelligence_cloud mode
  E4  execute_buy_option blocks in intelligence_cloud mode
  E5  execute_sell_option blocks in intelligence_cloud mode
  E6  flatten_all blocks in intelligence_cloud mode
  P1  /api/market-now produces a SaaS-safe payload
  P2  Blocked fields are absent from the Market Now payload
  P3  /api/mobile/portfolio returns intelligence-only placeholder (no broker state)
  P4  No mutation routes are registered in intelligence_api
  R1  yfinance is not in requirements.intelligence.txt
  R2  ib_async is not in requirements.intelligence.txt
  R3  No Railway reference in any intelligence cloud file
  B1  Layer boundary verifier passes (delegates to verify_intelligence_execution_separation.py)

Exit codes
──────────
  0  All checks pass — GO for DigitalOcean intelligence deployment
  1  One or more checks failed — HOLD

Usage
─────
  python3 scripts/verify_intelligence_cloud_deploy.py
  python3 scripts/verify_intelligence_cloud_deploy.py --verbose
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

VERBOSE = "--verbose" in sys.argv

Results: list[tuple[str, bool, str]] = []  # (check_id, passed, message)


def _record(check_id: str, passed: bool, message: str) -> None:
    Results.append((check_id, passed, message))
    if VERBOSE or not passed:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {check_id}: {message}")


def _set_intelligence_cloud_env() -> None:
    os.environ["DECIFER_RUNTIME_MODE"] = "intelligence_cloud"
    os.environ["DECIFER_EXECUTION_ENABLED"] = "false"
    if "runtime_config" in sys.modules:
        import importlib as _il
        _il.reload(sys.modules["runtime_config"])


def _restore_env() -> None:
    for k in ("DECIFER_RUNTIME_MODE", "DECIFER_EXECUTION_ENABLED"):
        os.environ.pop(k, None)
    if "runtime_config" in sys.modules:
        import importlib as _il
        _il.reload(sys.modules["runtime_config"])


# ---------------------------------------------------------------------------
# E1–E6: Execution guards block in intelligence_cloud mode
# ---------------------------------------------------------------------------

def check_execution_guards() -> None:
    _set_intelligence_cloud_env()
    try:
        import runtime_config
        importlib.reload(runtime_config)
        from runtime_config import ExecutionBlockedError, assert_execution_allowed

        actions = [
            ("E1", "execute_buy"),
            ("E2", "execute_short"),
            ("E3", "execute_sell"),
            ("E4", "execute_buy_option"),
            ("E5", "execute_sell_option"),
            ("E6", "flatten_all"),
        ]
        for check_id, action_name in actions:
            try:
                assert_execution_allowed(action_name)
                _record(check_id, False, f"assert_execution_allowed('{action_name}') did NOT raise — execution is NOT blocked!")
            except ExecutionBlockedError as exc:
                _record(check_id, True, f"'{action_name}' correctly raises ExecutionBlockedError: {str(exc)[:80]}")
            except Exception as exc:
                _record(check_id, False, f"Unexpected error checking '{action_name}': {exc}")
    except ImportError as exc:
        for check_id, _ in [("E1",""), ("E2",""), ("E3",""), ("E4",""), ("E5",""), ("E6","")]:
            _record(check_id, False, f"Could not import runtime_config: {exc}")
    finally:
        _restore_env()


# ---------------------------------------------------------------------------
# P1–P4: Payload and route checks
# ---------------------------------------------------------------------------

def check_market_now_payload() -> None:
    _set_intelligence_cloud_env()
    try:
        # Reload runtime_config with intelligence_cloud
        import runtime_config
        importlib.reload(runtime_config)

        from market_now_builder import get_market_now_dict
        from saas_intelligence_output import (
            SaaSPayloadValidationError,
            get_blocked_fields,
            validate_customer_payload,
        )

        try:
            payload = get_market_now_dict()
        except Exception as exc:
            _record("P1", False, f"get_market_now_dict() raised: {exc}")
            _record("P2", False, "Skipped — P1 failed")
            return

        # P1: payload validates
        try:
            validate_customer_payload(payload)
            _record("P1", True, f"Market Now payload is SaaS-safe ({len(payload)} fields)")
        except SaaSPayloadValidationError as exc:
            _record("P1", False, f"Payload validation failed: {exc}")

        # P2: no blocked fields present
        blocked = get_blocked_fields()
        found = [k for k in payload if k in blocked]
        if found:
            _record("P2", False, f"Blocked fields present in payload: {found}")
        else:
            _record("P2", True, "No blocked fields in Market Now payload")

    except Exception as exc:
        _record("P1", False, f"Unexpected error: {exc}")
        _record("P2", False, "Skipped — P1 errored")
    finally:
        _restore_env()


def check_portfolio_placeholder() -> None:
    """P3: /api/mobile/portfolio returns intelligence-only placeholder."""
    _set_intelligence_cloud_env()
    try:
        import runtime_config
        importlib.reload(runtime_config)

        # Import the flask app and use test client
        import intelligence_api
        with intelligence_api.app.test_client() as client:
            resp = client.get("/api/mobile/portfolio")
            data = json.loads(resp.data)

        # Must not contain broker state fields
        broker_fields = {"portfolio_value", "account_value", "buying_power", "pnl",
                         "daily_pnl", "order_id", "positions"}
        found_broker = [k for k in data if k in broker_fields and data[k] not in ([], None, 0)]
        if found_broker:
            _record("P3", False, f"Portfolio route returned broker state fields: {found_broker}")
        elif data.get("status") == "intelligence_cloud":
            _record("P3", True, "Portfolio route returns intelligence-only placeholder")
        else:
            # Check positions is empty list
            if data.get("positions") == [] and resp.status_code == 200:
                _record("P3", True, "Portfolio route returns empty positions (safe)")
            else:
                _record("P3", False, f"Portfolio route response unexpected: status={data.get('status')}, positions={data.get('positions')}")
    except Exception as exc:
        _record("P3", False, f"Could not test portfolio route: {exc}")
    finally:
        _restore_env()


def check_no_mutation_routes() -> None:
    """P4: No mutation routes (POST/DELETE) registered in intelligence_api."""
    _set_intelligence_cloud_env()
    try:
        import runtime_config
        importlib.reload(runtime_config)

        import intelligence_api
        mutation_routes: list[str] = []
        for rule in intelligence_api.app.url_map.iter_rules():
            methods = set(rule.methods or []) - {"HEAD", "OPTIONS"}
            if methods - {"GET"}:
                mutation_routes.append(f"{rule.rule} [{', '.join(sorted(methods - {'GET'}))}]")

        if mutation_routes:
            _record("P4", False, f"Mutation routes found in intelligence_api: {mutation_routes}")
        else:
            _record("P4", True, "No mutation routes registered (GET-only API confirmed)")
    except Exception as exc:
        _record("P4", False, f"Could not inspect routes: {exc}")
    finally:
        _restore_env()


# ---------------------------------------------------------------------------
# R1–R3: Requirements and external dependency checks
# ---------------------------------------------------------------------------

def check_requirements() -> None:
    req_path = _REPO_ROOT / "requirements.intelligence.txt"
    if not req_path.exists():
        _record("R1", False, "requirements.intelligence.txt does not exist")
        _record("R2", False, "Skipped — requirements file missing")
        return

    # Only check non-comment, non-empty lines — comment blocks may mention
    # excluded packages by name for documentation purposes.
    active_lines = [
        line.strip().lower()
        for line in req_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    active_content = "\n".join(active_lines)

    # R1: yfinance absent from active requirements
    if "yfinance" in active_content:
        _record("R1", False, "yfinance found as active requirement in requirements.intelligence.txt — must not be present")
    else:
        _record("R1", True, "yfinance is absent from requirements.intelligence.txt (active lines)")

    # R2: ib_async absent (broker library must not be in intelligence cloud deps)
    if "ib_async" in active_content or "ib-async" in active_content:
        _record("R2", False, "ib_async found as active requirement — broker library must not be in cloud deps")
    else:
        _record("R2", True, "ib_async is absent from requirements.intelligence.txt (active lines)")


def check_no_railway() -> None:
    """R3: No Railway reference in any intelligence cloud file."""
    intelligence_files = [
        _REPO_ROOT / "intelligence_api.py",
        _REPO_ROOT / "requirements.intelligence.txt",
        _REPO_ROOT / "market_now_builder.py",
        _REPO_ROOT / "saas_intelligence_output.py",
        _REPO_ROOT / "runtime_config.py",
        _REPO_ROOT / "docker-compose.yml",
    ]
    railway_hits: list[str] = []
    for f in intelligence_files:
        if f.exists():
            content = f.read_text(encoding="utf-8")
            if "railway" in content.lower():
                railway_hits.append(str(f.relative_to(_REPO_ROOT)))

    if railway_hits:
        _record("R3", False, f"Railway reference found in: {railway_hits}")
    else:
        _record("R3", True, "No Railway reference in intelligence cloud files")


# ---------------------------------------------------------------------------
# B1: Layer boundary verifier
# ---------------------------------------------------------------------------

def check_boundary_verifier() -> None:
    """B1: scripts/verify_intelligence_execution_separation.py passes."""
    verifier = _REPO_ROOT / "scripts" / "verify_intelligence_execution_separation.py"
    if not verifier.exists():
        _record("B1", False, "verify_intelligence_execution_separation.py not found")
        return

    result = subprocess.run(
        [sys.executable, str(verifier)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    if result.returncode == 0:
        _record("B1", True, "Layer boundary verifier: PASSED — 0 violations")
    else:
        violations = [line for line in result.stdout.splitlines() if "FAIL" in line]
        _record("B1", False, f"Layer boundary verifier failed — {len(violations)} violation(s). Run with --verbose for detail.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("\nDecifer Intelligence Cloud — Pre-Deploy Verification")
    print("═" * 60)

    check_execution_guards()      # E1–E6
    check_market_now_payload()    # P1–P2
    check_portfolio_placeholder() # P3
    check_no_mutation_routes()    # P4
    check_requirements()          # R1–R2
    check_no_railway()            # R3
    check_boundary_verifier()     # B1

    passed = sum(1 for _, ok, _ in Results if ok)
    failed = sum(1 for _, ok, _ in Results if not ok)
    total = len(Results)

    print(f"\n{'═' * 60}")
    print(f"  Checks: {total}  |  Passed: {passed}  |  Failed: {failed}")

    if failed == 0:
        print("\n  VERDICT: GO — DigitalOcean intelligence deployment is cleared.\n")
        return 0
    else:
        print(f"\n  VERDICT: HOLD — {failed} check(s) failed. Fix before deploying.\n")
        if not VERBOSE:
            print("  (re-run with --verbose for full detail)\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
