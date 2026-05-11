#!/usr/bin/env python3
"""
scripts/healthcheck.py — Lightweight Docker health check for Decifer runtime.

Purpose:
    Confirms the runtime is intact without connecting to any broker, placing
    orders, or requiring IBKR Gateway availability.

    Designed to be fast (< 5 seconds) and safe for use as a Docker HEALTHCHECK
    instruction or as a pre-start sanity check.

Scope:
    - Key Python imports succeed
    - Required runtime directories exist
    - config.py imports without error
    - handoff_reader imports without error
    - utils.log_rotation imports without error
    - Required environment variables are PRESENT (values never printed)
    - No recent fatal .fail_* sentinel files (non-blocking warning)
    - No live broker connection attempted
    - No trading state mutated

Not in scope (see scripts/cloud_preflight.py for these):
    - Manifest validation and freshness check
    - Handoff reader end-to-end execution
    - IBKR connectivity test
    - Write-probe on directories
    - Report file generation

Exit codes:
    0  All checks pass — runtime is healthy
    1  One or more blocking checks failed — runtime is not usable

Usage:
    python3 scripts/healthcheck.py
    python3 scripts/healthcheck.py --quiet   # suppress table, only exit code
"""
from __future__ import annotations

import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

_QUIET = "--quiet" in sys.argv

# ─────────────────────────────────────────────────────────────────────────────
# Check definitions
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_DIRS = [
    "data",
    "data/live",
    "data/heartbeats",
    "data/intelligence",
    "data/universe_builder",
    "logs",
]

# Presence is checked; values are never read or printed.
REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "IBKR_PAPER_ACCOUNT",
]

# Recommended env vars — missing causes a warning, not a failure
RECOMMENDED_ENV_VARS = [
    "ALPACA_BASE_URL",
    "FMP_API_KEY",
    "IBKR_ACTIVE_ACCOUNT",
]

# Key imports to verify — these must work or the runtime is broken
REQUIRED_IMPORTS = [
    ("anthropic", "anthropic"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("ib_async", "ib_async"),
    ("talib", "talib (TA-Lib C library)"),
    ("alpaca.trading.client", "alpaca-py"),
]

# Fail-sentinel file pattern in data/live/ — non-blocking warning
FAIL_SENTINEL_PATTERN = ".fail_"
FAIL_SENTINEL_DIR = os.path.join(_REPO_ROOT, "data", "live")
FAIL_SENTINEL_RECENT_SECONDS = 3600  # warn if any .fail_* file is < 1h old


# ─────────────────────────────────────────────────────────────────────────────
# Check runners
# ─────────────────────────────────────────────────────────────────────────────

def _result(name: str, ok: bool, detail: str, blocking: bool = True) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "blocking": blocking}


def check_python_version() -> dict:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    return _result(
        "python_version",
        ok,
        f"{major}.{minor} {'✓' if ok else '(requires 3.11+)'}",
    )


def check_imports() -> list[dict]:
    results = []
    for module, label in REQUIRED_IMPORTS:
        try:
            __import__(module)
            results.append(_result(f"import:{label}", True, "ok"))
        except ImportError as exc:
            results.append(_result(
                f"import:{label}", False,
                f"ImportError: {exc}",
            ))
        except Exception as exc:
            results.append(_result(
                f"import:{label}", False,
                f"Unexpected error: {type(exc).__name__}: {exc}",
            ))
    return results


def check_core_modules() -> list[dict]:
    """Verify Decifer-specific modules import cleanly."""
    results = []
    modules = [
        ("config", "config.py"),
        ("handoff_reader", "handoff_reader.py"),
        ("utils.log_rotation", "utils/log_rotation.py"),
    ]
    for module, label in modules:
        try:
            __import__(module)
            results.append(_result(f"module:{label}", True, "ok"))
        except Exception as exc:
            results.append(_result(
                f"module:{label}", False,
                f"{type(exc).__name__}: {exc}",
            ))
    return results


def check_dirs() -> list[dict]:
    results = []
    for rel in REQUIRED_DIRS:
        path = os.path.join(_REPO_ROOT, rel)
        exists = os.path.isdir(path)
        results.append(_result(
            f"dir:{rel}",
            exists,
            "exists" if exists else f"MISSING — create: mkdir -p {rel}",
        ))
    return results


def check_env_vars() -> list[dict]:
    """Check presence only. Values are never read or printed."""
    results = []
    # Load .env if present (for local dev; in cloud env vars come from runtime)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=False)
    except ImportError:
        pass

    for var in REQUIRED_ENV_VARS:
        present = bool(os.environ.get(var, "").strip())
        results.append(_result(
            f"env:{var}",
            present,
            "set" if present else "MISSING",
        ))
    for var in RECOMMENDED_ENV_VARS:
        present = bool(os.environ.get(var, "").strip())
        results.append(_result(
            f"env:{var} (recommended)",
            present,
            "set" if present else "not set — degraded operation",
            blocking=False,
        ))
    return results


def check_fail_sentinels() -> dict:
    """Non-blocking warning if recent .fail_* files exist in data/live/."""
    if not os.path.isdir(FAIL_SENTINEL_DIR):
        return _result(
            "fail_sentinels",
            True,
            "data/live/ not present yet — skip",
            blocking=False,
        )
    now = time.time()
    recent = []
    try:
        for fname in os.listdir(FAIL_SENTINEL_DIR):
            if not fname.startswith(FAIL_SENTINEL_PATTERN):
                continue
            fpath = os.path.join(FAIL_SENTINEL_DIR, fname)
            try:
                age = now - os.path.getmtime(fpath)
                if age < FAIL_SENTINEL_RECENT_SECONDS:
                    recent.append(fname)
            except OSError:
                pass
    except OSError:
        pass

    if recent:
        return _result(
            "fail_sentinels",
            False,
            f"{len(recent)} recent .fail_* file(s) in data/live/ — publisher failed recently",
            blocking=False,
        )
    return _result("fail_sentinels", True, "no recent fail files", blocking=False)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run_healthcheck() -> list[dict]:
    checks: list[dict] = []
    checks.append(check_python_version())
    checks.extend(check_imports())
    checks.extend(check_core_modules())
    checks.extend(check_dirs())
    checks.extend(check_env_vars())
    checks.append(check_fail_sentinels())
    return checks


def _print_table(checks: list[dict]) -> None:
    width = 52
    print(f"\n{'─' * width}")
    print("  Decifer Runtime Health Check")
    print(f"{'─' * width}")
    for c in checks:
        if c["ok"]:
            sym = "✓"
        elif not c["blocking"]:
            sym = "!"
        else:
            sym = "✗"
        print(f"  [{sym}] {c['name']}")
        if not c["ok"]:
            print(f"       {c['detail']}")
    print(f"{'─' * width}")

    blocking_failures = [c for c in checks if not c["ok"] and c["blocking"]]
    warnings = [c for c in checks if not c["ok"] and not c["blocking"]]
    passed = sum(1 for c in checks if c["ok"])

    if not blocking_failures:
        print(f"  PASS  {passed}/{len(checks)} checks ok  ({len(warnings)} warning(s))")
    else:
        print(f"  FAIL  {len(blocking_failures)} blocking failure(s)")
        for c in blocking_failures:
            print(f"  → {c['name']}: {c['detail']}")
    print(f"{'─' * width}\n")


def main() -> int:
    checks = run_healthcheck()
    if not _QUIET:
        _print_table(checks)
    blocking_failures = [c for c in checks if not c["ok"] and c["blocking"]]
    return 0 if not blocking_failures else 1


if __name__ == "__main__":
    sys.exit(main())
