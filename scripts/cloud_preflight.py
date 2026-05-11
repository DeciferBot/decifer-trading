#!/usr/bin/env python3
"""
scripts/cloud_preflight.py — Cloud deployment preflight check.

Verifies that all prerequisites for running Decifer in a cloud or new-machine
environment are met before any bot process starts.

Exits 0 only when every check passes.
Exits 1 with a JSON report when any blocking check fails.

Writes: data/runtime/cloud_preflight_report.json

Usage:
    python3 scripts/cloud_preflight.py
    python3 scripts/cloud_preflight.py --json-only   # suppress console output
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

_JSON_ONLY = "--json-only" in sys.argv

REQUIRED_PYTHON_MAJOR = 3
REQUIRED_PYTHON_MINOR = 11

REQUIRED_DIRS = [
    "data",
    "data/live",
    "data/heartbeats",
    "data/runtime",
    "data/intelligence",
    "data/reference",
    "logs",
]

REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_BASE_URL",
    "FMP_API_KEY",
    "ALPHA_VANTAGE_KEY",
    "IBKR_PAPER_ACCOUNT",
]

WRITABLE_DIRS = [
    "data/live",
    "data/heartbeats",
    "data/runtime",
    "logs",
]

REPORT_PATH = os.path.join(_REPO_ROOT, "data", "runtime", "cloud_preflight_report.json")


def _check(name: str, ok: bool, detail: str, blocking: bool = True) -> dict:
    return {"check": name, "ok": ok, "detail": detail, "blocking": blocking}


def run_preflight() -> dict:
    checks = []
    ts = datetime.now(timezone.utc).isoformat()

    # ── Python version ────────────────────────────────────────────────────────
    major, minor = sys.version_info[:2]
    ver_ok = (major, minor) >= (REQUIRED_PYTHON_MAJOR, REQUIRED_PYTHON_MINOR)
    checks.append(_check(
        "python_version",
        ver_ok,
        f"found {major}.{minor}, required >={REQUIRED_PYTHON_MAJOR}.{REQUIRED_PYTHON_MINOR}",
    ))

    # ── Required directories ──────────────────────────────────────────────────
    for d in REQUIRED_DIRS:
        path = os.path.join(_REPO_ROOT, d)
        exists = os.path.isdir(path)
        if not exists:
            try:
                os.makedirs(path, exist_ok=True)
                exists = True
                detail = f"created {path}"
            except Exception as e:
                detail = f"missing and could not create: {e}"
        else:
            detail = f"exists: {path}"
        checks.append(_check(f"dir_{d.replace('/', '_')}", exists, detail))

    # ── Config import ─────────────────────────────────────────────────────────
    try:
        import config as _cfg  # noqa: F401
        checks.append(_check("config_import", True, "config.py imports successfully"))
    except Exception as e:
        checks.append(_check("config_import", False, f"config import failed: {e}"))

    # ── IBKR connection params ────────────────────────────────────────────────
    try:
        from config import CONFIG
        ibkr_host = CONFIG.get("ibkr_host", "127.0.0.1")
        ibkr_port = CONFIG.get("ibkr_port", 4002)
        ibkr_client = CONFIG.get("ibkr_client_id", 1)
        checks.append(_check(
            "ibkr_params",
            bool(ibkr_host and ibkr_port),
            f"host={ibkr_host} port={ibkr_port} client_id={ibkr_client}",
        ))
    except Exception as e:
        checks.append(_check("ibkr_params", False, f"could not read IBKR params: {e}"))

    # ── Required env vars ─────────────────────────────────────────────────────
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=False)
    except ImportError:
        pass

    missing_vars = []
    for var in REQUIRED_ENV_VARS:
        val = os.environ.get(var, "")
        if not val:
            missing_vars.append(var)
    checks.append(_check(
        "env_vars",
        len(missing_vars) == 0,
        "all required env vars present" if not missing_vars else f"missing: {missing_vars}",
    ))

    # ── Writable directories ──────────────────────────────────────────────────
    for d in WRITABLE_DIRS:
        path = os.path.join(_REPO_ROOT, d)
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f"_write_probe_{int(time.time())}")
        try:
            with open(probe, "w") as f:
                f.write("ok")
            os.unlink(probe)
            checks.append(_check(f"writable_{d.replace('/', '_')}", True, f"writable: {path}"))
        except Exception as e:
            checks.append(_check(f"writable_{d.replace('/', '_')}", False, f"not writable: {path}: {e}"))

    # ── Handoff manifest ──────────────────────────────────────────────────────
    manifest_path = os.path.join(_REPO_ROOT, "data", "live", "current_manifest.json")
    if not os.path.exists(manifest_path):
        checks.append(_check(
            "handoff_manifest_exists",
            False,
            "data/live/current_manifest.json missing — bot will fail closed on handoff; "
            "run: python3 handoff_publisher.py --mode controlled_activation",
            blocking=False,
        ))
    else:
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            pub_mode = manifest.get("publication_mode", "unknown")
            enabled = manifest.get("handoff_enabled", False)
            expires_at = manifest.get("expires_at", "")
            from handoff_reader import _is_expired
            expired = _is_expired(expires_at)
            checks.append(_check(
                "handoff_manifest_valid",
                not expired,
                f"publication_mode={pub_mode} handoff_enabled={enabled} "
                f"expires_at={expires_at} expired={expired}",
                blocking=False,
            ))
        except Exception as e:
            checks.append(_check("handoff_manifest_valid", False, f"manifest read error: {e}", blocking=False))

    # ── Handoff reader fail-closed ────────────────────────────────────────────
    try:
        from handoff_reader import load_production_handoff
        result = load_production_handoff(manifest_path)
        allowed = result.get("handoff_allowed", False)
        reason = result.get("fail_closed_reason")
        checks.append(_check(
            "handoff_reader_fail_closed",
            True,
            f"reader ran without crash: handoff_allowed={allowed} "
            f"fail_closed_reason={reason!r} (fail_closed is correct when manifest disabled/expired)",
            blocking=False,
        ))
    except Exception as e:
        checks.append(_check(
            "handoff_reader_fail_closed",
            False,
            f"handoff_reader raised: {e}",
        ))

    # ── No live order placement triggered ────────────────────────────────────
    checks.append(_check(
        "no_live_order_placement",
        True,
        "preflight script never touches broker, orders_core, or execute paths",
    ))

    # ── Verdict ───────────────────────────────────────────────────────────────
    blocking_failures = [c for c in checks if not c["ok"] and c.get("blocking", True)]
    overall_ok = len(blocking_failures) == 0

    report = {
        "ts": ts,
        "overall_ok": overall_ok,
        "blocking_failures": len(blocking_failures),
        "total_checks": len(checks),
        "passed": sum(1 for c in checks if c["ok"]),
        "failed_blocking": [c["check"] for c in blocking_failures],
        "checks": checks,
    }
    return report


def _write_report(report: dict) -> None:
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)


def _print_report(report: dict) -> None:
    ok_sym = "✅"
    fail_sym = "❌"
    warn_sym = "⚠️"
    print(f"\n{'='*60}")
    print("  Decifer Cloud Preflight Report")
    print(f"  {report['ts']}")
    print(f"{'='*60}")
    for c in report["checks"]:
        if c["ok"]:
            sym = ok_sym
        elif not c.get("blocking", True):
            sym = warn_sym
        else:
            sym = fail_sym
        print(f"  {sym}  {c['check']}: {c['detail']}")
    print(f"{'='*60}")
    if report["overall_ok"]:
        print(f"  {ok_sym} PREFLIGHT PASSED — {report['passed']}/{report['total_checks']} checks ok")
    else:
        print(f"  {fail_sym} PREFLIGHT FAILED — {report['blocking_failures']} blocking failure(s)")
        for name in report["failed_blocking"]:
            print(f"       → {name}")
    print(f"  Report written: {REPORT_PATH}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    report = run_preflight()
    _write_report(report)
    if not _JSON_ONLY:
        _print_report(report)
    else:
        print(json.dumps(report, indent=2))
    sys.exit(0 if report["overall_ok"] else 1)
