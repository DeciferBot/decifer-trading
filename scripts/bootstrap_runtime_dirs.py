#!/usr/bin/env python3
"""
scripts/bootstrap_runtime_dirs.py — Idempotent runtime directory bootstrap.

Creates all directories required by the Decifer runtime on a fresh
local machine, cloud VM, or Docker volume mount.

Safe to run multiple times — never overwrites existing files or directories.
Prints a clear CREATED / EXISTS report for every path.

Does NOT:
  - create secrets or .env files
  - create fake trading data
  - connect to any broker or API
  - modify any existing file

Usage:
    python3 scripts/bootstrap_runtime_dirs.py
    python3 scripts/bootstrap_runtime_dirs.py --quiet
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QUIET = "--quiet" in sys.argv

# Canonical list from docs/cloud_readiness_contract.md §3
REQUIRED_DIRS: list[tuple[str, str]] = [
    ("data",                        "Root runtime state directory"),
    ("data/live",                   "Handoff publisher output — manifests and universe files"),
    ("data/heartbeats",             "Worker heartbeat signals"),
    ("data/intelligence",           "Intelligence layer state files"),
    ("data/universe_builder",       "Shadow universe files consumed by handoff publisher"),
    ("data/reference",              "Static reference tables (sector schema, symbol master, etc.)"),
    ("data/runtime",                "Preflight reports and transient runtime artefacts"),
    ("data/intelligence/backtest",  "Intelligence backtest fixtures (offline research — read-only at runtime)"),
    ("logs",                        "Application and worker logs"),
    ("chief-decifer/state/sessions","Chief Decifer session logs"),
    ("chief-decifer/state/research","Chief Decifer research files"),
    ("chief-decifer/state/specs",   "Chief Decifer feature specs"),
]


def bootstrap() -> tuple[list[str], list[str]]:
    """Create missing directories. Return (created, already_existed)."""
    created, existed = [], []
    for rel, _ in REQUIRED_DIRS:
        path = os.path.join(_REPO_ROOT, rel)
        if os.path.isdir(path):
            existed.append(rel)
        else:
            try:
                os.makedirs(path, exist_ok=True)
                created.append(rel)
            except OSError as exc:
                print(f"  [ERROR] Could not create {rel}: {exc}", file=sys.stderr)
                sys.exit(1)
    return created, existed


def _print_report(created: list[str], existed: list[str]) -> None:
    width = 56
    print(f"\n{'─' * width}")
    print("  Decifer Runtime Directory Bootstrap")
    print(f"{'─' * width}")
    for rel in existed:
        print(f"  [=] EXISTS   {rel}")
    for rel in created:
        print(f"  [+] CREATED  {rel}")
    print(f"{'─' * width}")
    if created:
        print(f"  Created {len(created)} director{'y' if len(created)==1 else 'ies'}.")
    else:
        print("  All directories already exist — nothing to do.")
    print(f"{'─' * width}\n")


def main() -> int:
    created, existed = bootstrap()
    if not _QUIET:
        _print_report(created, existed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
