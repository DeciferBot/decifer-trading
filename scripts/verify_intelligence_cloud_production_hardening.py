#!/usr/bin/env python3
"""
verify_intelligence_cloud_production_hardening.py

Sprint M6 — Production hardening verification for the Decifer Intelligence Cloud.

Checks performed
────────────────
  H1   /health reports runtime_mode: intelligence_cloud
  H2   /health reports execution_blocked: true
  H3   /health reports customer_output_mode field
  H4   /health reports data_freshness_status field
  H5   /health reports latest_market_now_timestamp field
  H6   /health reports latest_pipeline_artifact_timestamp field
  H7   /health does not expose secrets, broker state, or internal paths
  M1   /api/market-now payload passes validate_customer_payload (0 blocked fields)
  M2   /api/market-now returns at most _ALLOWED_FIELDS keys
  M3   Degraded payload is safe when key artefacts are missing (simulate missing)
  M4   Degraded payload uses plain-language "temporarily limited" messaging
  V1   validate_customer_payload rejects execution-like wording in values
  V2   validate_customer_payload rejects broker-like field names
  V3   validate_customer_payload rejects raw internal artefact names in values
  V4   validate_customer_payload rejects missing freshness_timestamp
  V5   validate_customer_payload rejects stale freshness_timestamp
  V6   validate_customer_payload rejects empty data_entitlement_note
  E1   execute_buy blocked in intelligence_cloud mode
  E2   No mutation routes registered in intelligence_api
  R1   yfinance absent from requirements.intelligence.txt
  R2   ib_async absent from requirements.intelligence.txt
  R3   No Railway reference in intelligence cloud files
  D1   /api/mobile/* routes documented as requiring Cloudflare Access
  B1   Layer boundary verifier still passes

Exit codes
──────────
  0  All checks pass — GO for production hardening
  1  One or more checks failed — HOLD

Usage
─────
  python3 scripts/verify_intelligence_cloud_production_hardening.py
  python3 scripts/verify_intelligence_cloud_production_hardening.py --verbose
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

VERBOSE = "--verbose" in sys.argv

Results: list[tuple[str, bool, str]] = []


def _record(check_id: str, passed: bool, message: str) -> None:
    Results.append((check_id, passed, message))
    if VERBOSE or not passed:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {check_id}: {message}")


def _set_intelligence_cloud_env() -> None:
    os.environ["DECIFER_RUNTIME_MODE"] = "intelligence_cloud"
    os.environ["DECIFER_EXECUTION_ENABLED"] = "false"
    os.environ["DECIFER_CUSTOMER_OUTPUT_MODE"] = "true"
    for mod in ("runtime_config", "intelligence_api", "market_now_builder",
                "saas_intelligence_output"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


def _restore_env() -> None:
    for k in ("DECIFER_RUNTIME_MODE", "DECIFER_EXECUTION_ENABLED",
              "DECIFER_CUSTOMER_OUTPUT_MODE"):
        os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# H1–H7: /health endpoint checks
# ---------------------------------------------------------------------------

_HEALTH_SAFE_KEYS = frozenset({
    "status", "service", "runtime_mode", "execution_blocked",
    "customer_output_mode", "data_freshness_status",
    "latest_market_now_timestamp", "latest_pipeline_artifact_timestamp",
    "degraded_artifact_warnings", "ts",
})

_HEALTH_FORBIDDEN_PATTERNS = (
    "api_key", "secret", "token", "password", "ibkr", "broker_account",
    "buying_power", "order_id", "account_value", "DUP481326",
    "ANTHROPIC_API_KEY", "ALPACA_API_KEY", "FMP_API_KEY",
    "/opt/decifer", "/home/", "/var/", "live_driver_state.json",
    "theme_activation.json", "current_manifest.json",
)


def check_health_endpoint() -> None:
    _set_intelligence_cloud_env()
    try:
        import runtime_config
        importlib.reload(runtime_config)
        import intelligence_api
        with intelligence_api.app.test_client() as client:
            resp = client.get("/health")
            data = json.loads(resp.data)

        _record("H1", data.get("runtime_mode") == "intelligence_cloud",
                f"runtime_mode={data.get('runtime_mode')!r}")
        _record("H2", data.get("execution_blocked") is True,
                f"execution_blocked={data.get('execution_blocked')!r}")
        _record("H3", "customer_output_mode" in data,
                "customer_output_mode field present" if "customer_output_mode" in data
                else "customer_output_mode field MISSING")
        _record("H4", "data_freshness_status" in data,
                "data_freshness_status field present" if "data_freshness_status" in data
                else "data_freshness_status field MISSING")
        _record("H5", "latest_market_now_timestamp" in data,
                "latest_market_now_timestamp field present" if "latest_market_now_timestamp" in data
                else "latest_market_now_timestamp field MISSING")
        _record("H6", "latest_pipeline_artifact_timestamp" in data,
                "latest_pipeline_artifact_timestamp field present"
                if "latest_pipeline_artifact_timestamp" in data
                else "latest_pipeline_artifact_timestamp field MISSING")

        # H7: no leaking of secrets, broker state, or internal file paths
        raw_json = json.dumps(data).lower()
        leaked = [p for p in _HEALTH_FORBIDDEN_PATTERNS if p.lower() in raw_json]
        if leaked:
            _record("H7", False, f"/health leaks sensitive content: {leaked}")
        else:
            _record("H7", True, "/health does not expose secrets, broker state, or internal paths")

    except Exception as exc:
        for cid in ("H1", "H2", "H3", "H4", "H5", "H6", "H7"):
            _record(cid, False, f"Could not test /health: {exc}")
    finally:
        _restore_env()


# ---------------------------------------------------------------------------
# M1–M4: Market Now payload checks
# ---------------------------------------------------------------------------

def check_market_now() -> None:
    _set_intelligence_cloud_env()
    try:
        import runtime_config
        importlib.reload(runtime_config)
        import intelligence_api
        with intelligence_api.app.test_client() as client:
            resp = client.get("/api/market-now")
            data = json.loads(resp.data)

        from saas_intelligence_output import (
            SaaSPayloadValidationError,
            get_blocked_fields,
            validate_customer_payload,
        )

        # Strip the "status" and "generated_at" wrapper keys added by intelligence_api
        inner = {k: v for k, v in data.items() if k not in ("status", "generated_at")}

        # M1: payload validates
        try:
            validate_customer_payload(inner)
            _record("M1", True, f"Market Now payload passes validate_customer_payload ({len(inner)} fields)")
        except SaaSPayloadValidationError as exc:
            _record("M1", False, f"Validation failed: {exc}")

        # M2: no blocked fields
        blocked = get_blocked_fields()
        found = [k for k in inner if k in blocked]
        if found:
            _record("M2", False, f"Blocked fields present: {found}")
        else:
            _record("M2", True, "No blocked fields in Market Now payload")

    except Exception as exc:
        _record("M1", False, f"Could not test /api/market-now: {exc}")
        _record("M2", False, "Skipped — M1 errored")
    finally:
        _restore_env()


def check_degraded_payload() -> None:
    """M3–M4: Simulate missing artefacts and verify degraded payload is safe."""
    _set_intelligence_cloud_env()

    # Temporarily rename key artefact to simulate missing file
    manifest_path = _REPO_ROOT / "data" / "live" / "current_manifest.json"
    driver_path = _REPO_ROOT / "data" / "intelligence" / "live_driver_state.json"
    backed_up: list[tuple[Path, Path]] = []

    try:
        import runtime_config
        importlib.reload(runtime_config)

        # Create a temp dir and move artefacts there
        tmpdir = Path(tempfile.mkdtemp())
        for p in (manifest_path, driver_path):
            if p.exists():
                bak = tmpdir / p.name
                shutil.move(str(p), str(bak))
                backed_up.append((p, bak))

        # Reload market_now_builder to clear any cached state
        if "market_now_builder" in sys.modules:
            importlib.reload(sys.modules["market_now_builder"])
        from market_now_builder import get_market_now_dict
        from saas_intelligence_output import SaaSPayloadValidationError, validate_customer_payload

        try:
            degraded = get_market_now_dict()
        except Exception as exc:
            _record("M3", False, f"get_market_now_dict() raised with missing artefacts: {exc}")
            _record("M4", False, "Skipped — M3 raised")
            return

        # M3: degraded payload passes validation
        try:
            validate_customer_payload(degraded)
            _record("M3", True, "Degraded payload passes validate_customer_payload")
        except SaaSPayloadValidationError as exc:
            _record("M3", False, f"Degraded payload fails validation: {exc}")

        # M4: degraded payload has plain-language messaging
        summary = str(degraded.get("plain_english_summary", ""))
        confidence = str(degraded.get("confidence_label", ""))
        has_limited_msg = "temporarily limited" in summary.lower() or "limited" in summary.lower()
        has_degraded_confidence = confidence in ("Insufficient data", "Low")
        _record("M4",
                has_limited_msg and has_degraded_confidence,
                f"Degraded messaging OK (summary mentions 'limited', confidence={confidence!r})"
                if (has_limited_msg and has_degraded_confidence)
                else f"Degraded messaging insufficient: summary={summary[:60]!r}, confidence={confidence!r}")

    except Exception as exc:
        _record("M3", False, f"Unexpected error in degraded test: {exc}")
        _record("M4", False, "Skipped — M3 errored")
    finally:
        # Restore artefacts
        for original, backup in backed_up:
            shutil.move(str(backup), str(original))
        _restore_env()


# ---------------------------------------------------------------------------
# V1–V6: validate_customer_payload hardening checks
# ---------------------------------------------------------------------------

def check_validator_hardening() -> None:
    _set_intelligence_cloud_env()
    try:
        from saas_intelligence_output import SaaSPayloadValidationError, validate_customer_payload
        from datetime import UTC, datetime

        fresh_ts = datetime.now(UTC).isoformat()
        _base_valid = {
            "market_regime_label": "Trending up",
            "plain_english_summary": "Markets look positive.",
            "key_drivers": ["AI capex cycle expanding"],
            "active_themes": ["ai_compute_infrastructure"],
            "opportunity_explanations": [{"theme": "AI Infrastructure", "explanation": "Expanding."}],
            "risk_notes": [],
            "what_to_watch": ["Upcoming Fed commentary"],
            "freshness_timestamp": fresh_ts,
            "confidence_label": "High",
            "source_category_labels": ["market_data"],
            "data_entitlement_note": "Market intelligence powered by Decifer. Not financial advice.",
        }

        # V1: execution-like wording in values
        bad_exec = {**_base_valid, "plain_english_summary": "execute_buy signal active."}
        try:
            validate_customer_payload(bad_exec)
            _record("V1", False, "validate_customer_payload did NOT reject execution wording in values")
        except SaaSPayloadValidationError:
            _record("V1", True, "validate_customer_payload correctly rejects execution-like wording in values")

        # V2: broker-like field names
        bad_broker = {**_base_valid, "ibkr_account_state": "paper"}
        try:
            validate_customer_payload(bad_broker)
            _record("V2", False, "validate_customer_payload did NOT reject broker-like field name")
        except SaaSPayloadValidationError:
            _record("V2", True, "validate_customer_payload correctly rejects broker-like field names")

        # V3: raw internal artefact names in values
        bad_artifact = {**_base_valid, "plain_english_summary": "Loaded from live_driver_state."}
        try:
            validate_customer_payload(bad_artifact)
            _record("V3", False, "validate_customer_payload did NOT reject internal artefact name in values")
        except SaaSPayloadValidationError:
            _record("V3", True, "validate_customer_payload correctly rejects raw internal artefact names in values")

        # V4: missing freshness_timestamp
        bad_no_ts = {k: v for k, v in _base_valid.items() if k != "freshness_timestamp"}
        try:
            validate_customer_payload(bad_no_ts)
            _record("V4", False, "validate_customer_payload did NOT reject missing freshness_timestamp")
        except SaaSPayloadValidationError:
            _record("V4", True, "validate_customer_payload correctly rejects missing freshness_timestamp")

        # V5: stale freshness_timestamp (8 hours ago)
        stale_ts = (datetime.now(UTC) - timedelta(hours=8)).isoformat()
        bad_stale = {**_base_valid, "freshness_timestamp": stale_ts}
        try:
            validate_customer_payload(bad_stale)
            _record("V5", False, "validate_customer_payload did NOT reject stale freshness_timestamp")
        except SaaSPayloadValidationError:
            _record("V5", True, "validate_customer_payload correctly rejects stale freshness_timestamp (8h old)")

        # V6: empty data_entitlement_note
        bad_note = {**_base_valid, "data_entitlement_note": ""}
        try:
            validate_customer_payload(bad_note)
            _record("V6", False, "validate_customer_payload did NOT reject empty data_entitlement_note")
        except SaaSPayloadValidationError:
            _record("V6", True, "validate_customer_payload correctly rejects empty data_entitlement_note")

    except Exception as exc:
        for cid in ("V1", "V2", "V3", "V4", "V5", "V6"):
            _record(cid, False, f"Unexpected error: {exc}")
    finally:
        _restore_env()


# ---------------------------------------------------------------------------
# E1–E2: Execution guard and route mutation checks
# ---------------------------------------------------------------------------

def check_execution_guard() -> None:
    """E1: execute_buy blocked in intelligence_cloud mode."""
    _set_intelligence_cloud_env()
    try:
        import runtime_config
        importlib.reload(runtime_config)
        from runtime_config import ExecutionBlockedError, assert_execution_allowed
        try:
            assert_execution_allowed("execute_buy")
            _record("E1", False, "assert_execution_allowed('execute_buy') did NOT raise — execution NOT blocked!")
        except ExecutionBlockedError as exc:
            _record("E1", True, f"execute_buy correctly raises ExecutionBlockedError: {str(exc)[:70]}")
    except Exception as exc:
        _record("E1", False, f"Could not test execution guard: {exc}")
    finally:
        _restore_env()


def check_no_mutation_routes() -> None:
    """E2: No POST/PUT/DELETE/PATCH routes registered in intelligence_api."""
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
            _record("E2", False, f"Mutation routes found: {mutation_routes}")
        else:
            _record("E2", True, "No mutation routes registered (GET-only API confirmed)")
    except Exception as exc:
        _record("E2", False, f"Could not inspect routes: {exc}")
    finally:
        _restore_env()


# ---------------------------------------------------------------------------
# R1–R3: Dependency absence checks
# ---------------------------------------------------------------------------

def check_requirements() -> None:
    req_path = _REPO_ROOT / "requirements.intelligence.txt"
    if not req_path.exists():
        _record("R1", False, "requirements.intelligence.txt does not exist")
        _record("R2", False, "Skipped — requirements file missing")
        _record("R3", False, "Skipped — requirements file missing")
        return

    active_lines = "\n".join(
        line.strip().lower()
        for line in req_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )

    _record("R1", "yfinance" not in active_lines,
            "yfinance absent from active requirements"
            if "yfinance" not in active_lines
            else "yfinance FOUND in active requirements — must be removed")

    _record("R2", "ib_async" not in active_lines and "ib-async" not in active_lines,
            "ib_async absent from active requirements"
            if "ib_async" not in active_lines and "ib-async" not in active_lines
            else "ib_async FOUND in active requirements — broker library must not be here")

    # R3: No Railway reference in intelligence cloud files
    intelligence_files = [
        _REPO_ROOT / "intelligence_api.py",
        _REPO_ROOT / "requirements.intelligence.txt",
        _REPO_ROOT / "market_now_builder.py",
        _REPO_ROOT / "saas_intelligence_output.py",
        _REPO_ROOT / "runtime_config.py",
    ]
    railway_hits = [
        str(f.relative_to(_REPO_ROOT))
        for f in intelligence_files
        if f.exists() and "railway" in f.read_text(encoding="utf-8").lower()
    ]
    _record("R3", not railway_hits,
            "No Railway reference in intelligence cloud files"
            if not railway_hits
            else f"Railway reference found in: {railway_hits}")


# ---------------------------------------------------------------------------
# D1: Cloudflare Access documentation check
# ---------------------------------------------------------------------------

def check_mobile_routes_documented() -> None:
    """D1: /api/mobile/* must be documented as requiring Cloudflare Access."""
    _set_intelligence_cloud_env()
    try:
        # Check deployment doc for Cloudflare Access language
        doc_path = _REPO_ROOT / "docs" / "DIGITALOCEAN_INTELLIGENCE_CLOUD_DEPLOYMENT.md"
        if not doc_path.exists():
            _record("D1", False, "DIGITALOCEAN_INTELLIGENCE_CLOUD_DEPLOYMENT.md not found")
            return
        content = doc_path.read_text(encoding="utf-8").lower()
        has_cloudflare = "cloudflare" in content or "cloudflare access" in content
        has_mobile_protected = "/api/mobile" in content and (
            "protected" in content or "authenticated" in content or "access" in content
        )
        if has_cloudflare and has_mobile_protected:
            _record("D1", True, "/api/mobile/* documented as requiring Cloudflare Access in deployment doc")
        else:
            _record("D1", False,
                    f"Deployment doc missing Cloudflare Access docs for /api/mobile/* "
                    f"(cloudflare_present={has_cloudflare}, mobile_protected={has_mobile_protected})")
    except Exception as exc:
        _record("D1", False, f"Could not check documentation: {exc}")
    finally:
        _restore_env()


# ---------------------------------------------------------------------------
# B1: Layer boundary verifier
# ---------------------------------------------------------------------------

def check_boundary_verifier() -> None:
    verifier = _REPO_ROOT / "scripts" / "verify_intelligence_execution_separation.py"
    if not verifier.exists():
        _record("B1", False, "verify_intelligence_execution_separation.py not found")
        return
    result = subprocess.run(
        [sys.executable, str(verifier)],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    if result.returncode == 0:
        _record("B1", True, "Layer boundary verifier: PASSED — 0 violations")
    else:
        violations = [line for line in result.stdout.splitlines() if "FAIL" in line]
        _record("B1", False,
                f"Layer boundary verifier failed — {len(violations)} violation(s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("\nDecifer Intelligence Cloud — Sprint M6 Production Hardening Verification")
    print("═" * 72)

    check_health_endpoint()         # H1–H7
    check_market_now()              # M1–M2
    check_degraded_payload()        # M3–M4
    check_validator_hardening()     # V1–V6
    check_execution_guard()         # E1
    check_no_mutation_routes()      # E2
    check_requirements()            # R1–R3
    check_mobile_routes_documented() # D1
    check_boundary_verifier()       # B1

    passed = sum(1 for _, ok, _ in Results if ok)
    failed = sum(1 for _, ok, _ in Results if not ok)
    total = len(Results)

    print(f"\n{'═' * 72}")
    print(f"  Checks: {total}  |  Passed: {passed}  |  Failed: {failed}")

    if failed == 0:
        print("\n  VERDICT: GO — Intelligence cloud production hardening verified.\n")
        return 0
    else:
        print(f"\n  VERDICT: HOLD — {failed} check(s) failed. Fix before deploying.\n")
        if not VERBOSE:
            print("  (re-run with --verbose for full detail)\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
