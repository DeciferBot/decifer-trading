#!/usr/bin/env python3.11
"""
scripts/control_plane_status.py — Control-plane health report.

Checks the operational status of every scheduled job, critical data file,
and heartbeat in the Decifer production system. Run this at any time to
get a one-command view of system health.

Usage:
    python3.11 scripts/control_plane_status.py              # human-readable report
    python3.11 scripts/control_plane_status.py --json       # machine-readable JSON
    python3.11 scripts/control_plane_status.py --fail-fast  # exit 1 if any critical check fails

No broker calls. No live data. No order logic. Safe to run at any time.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime

# ── Repo root detection ─────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

from freshness_checks import (
    check_committed_universe_freshness,
    check_ic_weights_freshness,
    check_intelligence_freshness,
)

# ── Constants ───────────────────────────────────────────────────────────────

_INTELLIGENCE_MAX_AGE_HOURS = 25.0
_MANIFEST_TTL_MINUTES = 15.0

# critical = failure here affects live trading or intelligence quality
# utility  = failure is an operational inconvenience, not a trading risk
_LAUNCHD_PLISTS = [
    {"label": "com.decifer.intelligence-pipeline",    "priority": "critical"},
    {"label": "com.decifer.handoff-publisher",        "priority": "critical"},
    {"label": "com.decifer.universe-committed",       "priority": "critical"},
    {"label": "com.decifer.universe-promoter-preopen","priority": "critical"},
    {"label": "com.decifer.universe-promoter-eod",    "priority": "critical"},
    {"label": "com.decifer.auto-push",                "priority": "utility"},
    {"label": "com.decifer.icloud-sync",              "priority": "utility"},
]

_LAUNCHD_AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")

_HEARTBEAT_FILES = {
    "handoff_publisher": "data/heartbeats/handoff_publisher.json",
    "universe_committed": "data/heartbeats/universe_committed_worker.json",
    "universe_promoter": "data/heartbeats/universe_promoter_worker.json",
}

_MANIFEST_PATH = "data/live/current_manifest.json"
_COMMITTED_UNIVERSE_PATH = "data/committed_universe.json"
_IC_WEIGHTS_PATH = "data/ic_weights.json"

_INTELLIGENCE_FILES = [
    "data/intelligence/current_economic_context.json",
    "data/intelligence/theme_activation.json",
    "data/intelligence/thesis_store.json",
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(UTC)


def _age_hours(iso_ts: str) -> float | None:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return (_now_utc() - dt).total_seconds() / 3600.0
    except Exception:
        return None


def _read_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _check_launchctl() -> dict[str, dict]:
    """Return launchctl list output parsed per job label."""
    results: dict[str, dict] = {}
    try:
        out = subprocess.check_output(
            ["launchctl", "list"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            parts = line.strip().split("\t")
            if len(parts) != 3:
                continue
            pid_str, exit_str, label = parts
            results[label] = {
                "loaded": True,
                "running": pid_str != "-",
                "last_exit": int(exit_str) if exit_str.lstrip("-").isdigit() else None,
            }
    except Exception:
        pass
    return results


def _check_launchd_plists() -> list[dict]:
    checks = []
    launchctl_state = _check_launchctl()
    for entry in _LAUNCHD_PLISTS:
        label = entry["label"]
        priority = entry["priority"]
        plist_file = os.path.join(_LAUNCHD_AGENTS_DIR, f"{label}.plist")
        installed = os.path.exists(plist_file)
        loaded = launchctl_state.get(label, {}).get("loaded", False)
        last_exit = launchctl_state.get(label, {}).get("last_exit")
        running = launchctl_state.get(label, {}).get("running", False)

        if not installed:
            status = "NOT_INSTALLED"
        elif not loaded:
            status = "INSTALLED_NOT_LOADED"
        elif last_exit not in (0, None):
            status = f"LAST_EXIT_{last_exit}"
        else:
            status = "OK"

        checks.append({
            "label": label,
            "priority": priority,
            "installed": installed,
            "loaded": loaded,
            "running": running,
            "last_exit": last_exit,
            "status": status,
        })
    return checks


def _check_intelligence_files() -> dict:
    fresh = check_intelligence_freshness(_INTELLIGENCE_FILES, max_age_hours=_INTELLIGENCE_MAX_AGE_HOURS)
    return fresh


def _check_committed_universe() -> dict:
    return check_committed_universe_freshness(_COMMITTED_UNIVERSE_PATH)


def _check_ic_weights() -> dict:
    return check_ic_weights_freshness(_IC_WEIGHTS_PATH, warn_age_days=14.0)


def _check_manifest() -> dict:
    data = _read_json(_MANIFEST_PATH)
    if data is None:
        return {"status": "missing", "age_minutes": None, "sla_ok": False,
                "detail": "current_manifest.json not found"}

    # Manifest uses "published_at" (not "generated_at") — check both for compatibility.
    generated_at = data.get("published_at") or data.get("generated_at")
    expires_at = data.get("expires_at")
    handoff_enabled = data.get("handoff_enabled", False)
    publication_mode = data.get("publication_mode", "unknown")

    age_h = _age_hours(generated_at) if generated_at else None
    age_m = age_h * 60 if age_h is not None else None
    sla_ok = age_m is not None and age_m <= _MANIFEST_TTL_MINUTES

    if age_m is None:
        status = "no_timestamp"
    elif not sla_ok:
        status = "expired"
    else:
        status = "fresh"

    return {
        "status": status,
        "age_minutes": round(age_m, 1) if age_m is not None else None,
        "sla_ok": sla_ok,
        "handoff_enabled": handoff_enabled,
        "publication_mode": publication_mode,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "detail": (
            f"manifest {status}: {age_m:.0f}min old (SLA={_MANIFEST_TTL_MINUTES:.0f}min), "
            f"handoff_enabled={handoff_enabled}, mode={publication_mode}"
            if age_m is not None
            else f"manifest {status}: no generated_at field"
        ),
    }


def _check_heartbeats() -> list[dict]:
    results = []
    for name, path in _HEARTBEAT_FILES.items():
        data = _read_json(path)
        if data is None:
            results.append({
                "name": name, "path": path, "status": "missing",
                "age_hours": None, "last_exit_code": None,
            })
            continue

        ts = data.get("last_success_at") or data.get("timestamp") or data.get("last_attempt_at")
        age_h = _age_hours(ts) if ts else None
        # "validation_status" is used by handoff_publisher heartbeat; "status" by universe workers.
        heartbeat_status = data.get("validation_status") or data.get("status", "unknown")
        results.append({
            "name": name,
            "path": path,
            "status": heartbeat_status,
            "age_hours": round(age_h, 1) if age_h is not None else None,
            "last_exit_code": data.get("exit_code"),
        })
    return results


def _check_bot_restart_mechanism() -> dict:
    bot_plist_installed = os.path.exists(
        os.path.join(_LAUNCHD_AGENTS_DIR, "com.decifer.bot.plist")
    )
    return {
        "restart_on_failure": bot_plist_installed,
        "status": "launchd_managed" if bot_plist_installed else "manual_start_only",
        "detail": (
            "bot.py managed by launchd (restart-on-failure active)"
            if bot_plist_installed
            else "bot.py requires manual restart — com.decifer.bot.plist not installed. "
                 "Template at ops/launchd/com.decifer.bot.plist (resolve blockers first)"
        ),
    }


def _check_dual_scheduling() -> dict:
    """Report whether bot.py internal universe schedule is active or suppressed."""
    plist_installed = os.path.exists(
        os.path.join(_LAUNCHD_AGENTS_DIR, "com.decifer.universe-committed.plist")
    )
    return {
        "launchd_installed": plist_installed,
        "internal_schedule_active": not plist_installed,
        "status": "launchd_sole_authority" if plist_installed else "internal_fallback",
        "detail": (
            "Universe scheduling: launchd sole authority — bot.py internal schedule suppressed"
            if plist_installed
            else "Universe scheduling: bot.py internal fallback — launchd plists not installed"
        ),
    }


# ── Report builder ───────────────────────────────────────────────────────────

def build_report() -> dict:
    ts = _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    intelligence = _check_intelligence_files()
    committed = _check_committed_universe()
    ic = _check_ic_weights()
    manifest = _check_manifest()
    heartbeats = _check_heartbeats()
    launchd = _check_launchd_plists()
    restart = _check_bot_restart_mechanism()
    dual = _check_dual_scheduling()

    critical_failures = []
    warnings = []

    if not intelligence["ok"]:
        critical_failures.append(f"intelligence_files: {intelligence['detail']}")
    if not committed["ok"]:
        critical_failures.append(f"committed_universe: {committed['detail']}")
    elif committed.get("warn"):
        warnings.append(f"committed_universe: {committed['detail']}")
    if not ic["ok"]:
        warnings.append(f"ic_weights: {ic['detail']}")
    if not manifest["sla_ok"]:
        warnings.append(f"manifest: {manifest['detail']}")
    for hb in heartbeats:
        if hb["status"] not in ("success", "ok", "pass", "unknown") and hb["status"] != "missing":
            warnings.append(f"heartbeat/{hb['name']}: {hb['status']}")
        elif hb["status"] == "missing":
            warnings.append(f"heartbeat/{hb['name']}: file missing (worker may not have run yet)")

    launchd_issues = [j for j in launchd if j["status"] not in ("OK",)]
    for j in launchd_issues:
        msg = f"launchd/{j['label']}: {j['status']}"
        if j["priority"] == "critical" and "exit" in j["status"].lower():
            critical_failures.append(msg)
        else:
            warnings.append(msg)

    if not restart["restart_on_failure"]:
        warnings.append(f"bot_restart: {restart['detail']}")

    overall = (
        "CRITICAL" if critical_failures
        else "WARN" if warnings
        else "OK"
    )

    return {
        "report_ts": ts,
        "overall": overall,
        "critical_failures": critical_failures,
        "warnings": warnings,
        "sections": {
            "intelligence_files": intelligence,
            "committed_universe": committed,
            "ic_weights": ic,
            "manifest": manifest,
            "heartbeats": heartbeats,
            "launchd_jobs": launchd,
            "bot_restart": restart,
            "dual_scheduling": dual,
        },
    }


# ── Human-readable formatter ─────────────────────────────────────────────────

def _status_icon(ok: bool, warn: bool = False) -> str:
    if ok and not warn:
        return "✓"
    if warn:
        return "⚠"
    return "✗"


def print_report(report: dict) -> None:
    overall = report["overall"]
    overall_icon = {"OK": "✓", "WARN": "⚠", "CRITICAL": "✗"}.get(overall, "?")
    print(f"\n{'='*60}")
    print(f"  DECIFER CONTROL-PLANE STATUS — {report['report_ts']}")
    print(f"  Overall: {overall_icon} {overall}")
    print(f"{'='*60}\n")

    s = report["sections"]

    # Intelligence files
    intel = s["intelligence_files"]
    icon = _status_icon(intel["ok"])
    print(f"[Intelligence Files]  {icon} {intel['detail']}")

    # Committed universe
    cu = s["committed_universe"]
    icon = _status_icon(cu["ok"], cu.get("warn", False))
    print(f"[Committed Universe]  {icon} {cu['detail']}")

    # IC weights
    ic = s["ic_weights"]
    icon = _status_icon(ic["ok"])
    print(f"[IC Weights]          {icon} {ic['detail']}")

    # Manifest
    mf = s["manifest"]
    icon = _status_icon(mf["sla_ok"])
    print(f"[Manifest SLA]        {icon} {mf['detail']}")

    # Heartbeats
    print(f"\n[Heartbeats]")
    for hb in s["heartbeats"]:
        ok = hb["status"] in ("success", "ok", "pass")
        age_str = f"{hb['age_hours']:.1f}h ago" if hb["age_hours"] is not None else "unknown age"
        icon = _status_icon(ok)
        print(f"  {icon} {hb['name']:30s}  {hb['status']:10s}  {age_str}")

    # Launchd
    print(f"\n[launchd Jobs]")
    for job in s["launchd_jobs"]:
        ok = job["status"] == "OK"
        icon = _status_icon(ok)
        exit_str = f"  (last exit: {job['last_exit']})" if job["last_exit"] not in (0, None) else ""
        print(f"  {icon} {job['label']:45s}  {job['status']}{exit_str}")

    # Dual scheduling
    ds = s["dual_scheduling"]
    icon = _status_icon(ds["launchd_installed"])
    print(f"\n[Scheduling]          {icon} {ds['detail']}")

    # Restart mechanism
    rs = s["bot_restart"]
    icon = _status_icon(rs["restart_on_failure"])
    print(f"[Restart-on-Failure]  {icon} {rs['detail']}")

    # Summary
    if report["critical_failures"]:
        print(f"\n{'!'*60}")
        print("  CRITICAL FAILURES:")
        for f in report["critical_failures"]:
            print(f"    ✗ {f}")
    if report["warnings"]:
        print(f"\n  WARNINGS:")
        for w in report["warnings"]:
            print(f"    ⚠ {w}")
    print()


# ── Entry point ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Decifer control-plane health report")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    parser.add_argument(
        "--fail-fast", action="store_true",
        help="Exit with code 1 if any critical check fails",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Override data directory (default: <repo_root>/data). "
             "Use to point at production data when running from a worktree.",
    )
    args = parser.parse_args()

    if args.data_dir:
        # Rewrite path constants to use the specified data root.
        global _MANIFEST_PATH, _COMMITTED_UNIVERSE_PATH, _IC_WEIGHTS_PATH, _INTELLIGENCE_FILES
        _MANIFEST_PATH = os.path.join(args.data_dir, "live/current_manifest.json")
        _COMMITTED_UNIVERSE_PATH = os.path.join(args.data_dir, "committed_universe.json")
        _IC_WEIGHTS_PATH = os.path.join(args.data_dir, "ic_weights.json")
        _INTELLIGENCE_FILES = [
            os.path.join(args.data_dir, "intelligence/current_economic_context.json"),
            os.path.join(args.data_dir, "intelligence/theme_activation.json"),
            os.path.join(args.data_dir, "intelligence/thesis_store.json"),
        ]
        global _HEARTBEAT_FILES
        _HEARTBEAT_FILES = {
            "handoff_publisher": os.path.join(args.data_dir, "heartbeats/handoff_publisher.json"),
            "universe_committed": os.path.join(args.data_dir, "heartbeats/universe_committed_worker.json"),
            "universe_promoter": os.path.join(args.data_dir, "heartbeats/universe_promoter_worker.json"),
        }

    report = build_report()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    if args.fail_fast and report["overall"] == "CRITICAL":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
