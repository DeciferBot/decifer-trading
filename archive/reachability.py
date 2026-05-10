#!/usr/bin/env python3
"""
reachability.py — Confirmed dead-code detector for Decifer

Walks the import tree from one or more entry points using AST.
Every file NOT reachable is confirmed dead — no inference, no LLM.

Usage:
    python3 reachability.py                     # entry: bot.py
    python3 reachability.py telegram_bot.py     # custom entry point
    python3 reachability.py --all-entries       # bot + dashboard + telegram_bot
    python3 reachability.py --json              # machine-readable output
"""

from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).parent

# Entry points that can be launched independently
ALL_ENTRY_POINTS = ["bot.py", "dashboard.py", "telegram_bot.py"]

# Third-party / stdlib modules — never project files
STDLIB_PREFIXES = {
    "ast",
    "os",
    "sys",
    "json",
    "re",
    "math",
    "time",
    "datetime",
    "pathlib",
    "threading",
    "subprocess",
    "logging",
    "collections",
    "functools",
    "itertools",
    "typing",
    "dataclasses",
    "enum",
    "abc",
    "io",
    "copy",
    "random",
    "hashlib",
    "hmac",
    "base64",
    "urllib",
    "http",
    "socket",
    "ssl",
    "struct",
    "traceback",
    "contextlib",
    "weakref",
    "gc",
    "signal",
    "resource",
    "platform",
    "shutil",
    "tempfile",
    "glob",
    "fnmatch",
    "stat",
    "errno",
    "inspect",
    "importlib",
    "types",
    "warnings",
    "textwrap",
    "string",
    "unicodedata",
    "zoneinfo",
    "asyncio",
    "concurrent",
    "queue",
    "multiprocessing",
    "pickle",
    "shelve",
    "csv",
    "configparser",
    "argparse",
    "getpass",
    "getopt",
    "unittest",
    "decimal",
    "fractions",
    "statistics",
    "operator",
    "heapq",
    "bisect",
    # third-party
    "anthropic",
    "alpaca_trade_api",
    "alpaca",
    "ibapi",
    "requests",
    "aiohttp",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "lightgbm",
    "torch",
    "tensorflow",
    "yfinance",
    "ta",
    "tulipy",
    "colorama",
    "schedule",
    "flask",
    "fastapi",
    "uvicorn",
    "pydantic",
    "dotenv",
    "pytest",
    "coverage",
    "websocket",
    "websockets",
    "httpx",
    "certifi",
    "charset_normalizer",
    "idna",
    "urllib3",
    "tzdata",
    "pytz",
    "dateutil",
    "arrow",
    "telegram",
    "alpaca_streams",
    "msgspec",
    "orjson",
    "ujson",
}


def get_all_project_files() -> dict[str, Path]:
    """Map stem → Path for every .py file in the project (excluding this script)."""
    files: dict[str, Path] = {}
    exclude_dirs = {".claude", "__pycache__", "tests", ".git", "logs"}
    for fp in sorted(ROOT.rglob("*.py")):
        rel = fp.relative_to(ROOT)
        if any(p in exclude_dirs for p in rel.parts[:-1]):
            continue
        if fp.name == Path(__file__).name:
            continue
        # Register by both stem and relative path stem
        files[fp.stem] = fp
        # Also register subdirectory files by their full relative path without .py
        rel_no_ext = str(rel)[:-3].replace("/", ".")
        if rel_no_ext != fp.stem:
            files[rel_no_ext] = fp
    return files


def extract_imports(filepath: Path) -> list[str]:
    """Return list of module names imported by a file (top-level only)."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except Exception:
        return []

    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module.split(".")[0])
            # Handle `from . import X` — relative, skip
    return modules


def is_project_module(name: str, project_files: dict[str, Path]) -> bool:
    return name in project_files and name not in STDLIB_PREFIXES


def walk_reachable(
    entry_points: list[Path],
    project_files: dict[str, Path],
) -> dict[str, dict]:
    """
    BFS from entry points through import graph.
    Returns {stem: {path, depth, imported_by}} for every reachable file.
    """
    reachable: dict[str, dict] = {}
    queue: deque[tuple[str, int, str]] = deque()  # (stem, depth, importer)

    for ep in entry_points:
        stem = ep.stem
        if stem not in reachable:
            reachable[stem] = {"path": ep, "depth": 0, "imported_by": ["<entry>"], "importers": set()}
            queue.append((stem, 0, "<entry>"))

    while queue:
        stem, depth, _importer = queue.popleft()
        fp = project_files.get(stem)
        if fp is None:
            continue

        for mod in extract_imports(fp):
            if not is_project_module(mod, project_files):
                continue
            if mod not in reachable:
                reachable[mod] = {
                    "path": project_files[mod],
                    "depth": depth + 1,
                    "imported_by": [stem],
                    "importers": {stem},
                }
                queue.append((mod, depth + 1, stem))
            else:
                reachable[mod]["importers"].add(stem)

    return reachable


def run(entry_names: list[str], json_output: bool = False) -> None:
    project_files = get_all_project_files()
    all_files = {fp.stem: fp for fp in project_files.values()}

    entry_paths = []
    for name in entry_names:
        stem = Path(name).stem
        fp = project_files.get(stem)
        if fp is None:
            print(f"[!] Entry point not found: {name}")
            sys.exit(1)
        entry_paths.append(fp)

    reachable = walk_reachable(entry_paths, project_files)

    # Compute dead files — reachable stems vs all project stems
    all_stems = set(all_files.keys())
    reachable_stems = set(reachable.keys())
    dead_stems = all_stems - reachable_stems

    # Enrich dead info
    dead: list[dict] = []
    for stem in sorted(dead_stems):
        fp = all_files[stem]
        try:
            lines = fp.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
        except Exception:
            lines = 0
        dead.append({"file": fp.name, "path": str(fp.relative_to(ROOT)), "lines": lines})
    dead.sort(key=lambda x: -x["lines"])

    # Reachable by depth
    by_depth: dict[int, list[str]] = defaultdict(list)
    for stem, info in sorted(reachable.items()):
        by_depth[info["depth"]].append(stem + ".py")

    total_dead_lines = sum(d["lines"] for d in dead)

    if json_output:
        print(
            json.dumps(
                {
                    "entry_points": entry_names,
                    "reachable_count": len(reachable),
                    "dead_count": len(dead),
                    "dead_lines": total_dead_lines,
                    "reachable": {
                        s: {"depth": info["depth"], "file": info["path"].name} for s, info in reachable.items()
                    },
                    "dead": dead,
                },
                indent=2,
            )
        )
        return

    # ── Terminal output ───────────────────────────────────────────────────────
    W = "\033[0m"
    B = "\033[1m"
    R = "\033[91m"
    G = "\033[92m"
    Y = "\033[93m"
    BL = "\033[94m"
    DIM = "\033[2m"

    print()
    print(f"{B}{'─' * 62}{W}")
    print(f"{B}  Decifer Reachability Analysis{W}")
    print(f"{DIM}  Entry: {', '.join(entry_names)}{W}")
    print(f"{B}{'─' * 62}{W}")
    print()

    # Reachable tree
    print(f"{B}{G}  REACHABLE  {len(reachable)} files{W}")
    for depth in sorted(by_depth.keys()):
        files_at_depth = sorted(by_depth[depth])
        label = "entry" if depth == 0 else f"depth {depth}"
        print(f"  {DIM}[{label}]{W}  {', '.join(f'{BL}{f}{W}' for f in files_at_depth)}")
    print()

    # Dead files
    print(f"{B}{R}  CONFIRMED DEAD  {len(dead)} files  ·  {total_dead_lines:,} lines{W}")
    print()
    for d in dead:
        d["path"]
        lines = d["lines"]
        bar = "▓" * min(30, max(1, lines // 100))
        print(f"  {R}{d['file']:40s}{W}  {Y}{lines:4d} lines{W}  {DIM}{bar}{W}")
    print()

    # Summary
    total_files = len(reachable) + len(dead)
    pct_dead = (len(dead) / total_files * 100) if total_files else 0
    print(f"{'─' * 62}")
    print(f"  Total project files : {total_files}")
    print(f"  Reachable           : {G}{len(reachable)}{W}")
    print(f"  {R}Confirmed dead      : {len(dead)}  ({pct_dead:.0f}%)  ·  {total_dead_lines:,} lines{W}")
    print(f"{'─' * 62}")
    print()

    # Warn about entry points that weren't found
    missing = [n for n in ALL_ENTRY_POINTS if Path(n).stem not in all_files]
    if missing:
        print(f"{Y}  Note: skipped missing entry points: {', '.join(missing)}{W}")
        print()


def main() -> None:
    args = sys.argv[1:]
    json_output = "--json" in args
    args = [a for a in args if a != "--json"]

    if "--all-entries" in args:
        entries = [e for e in ALL_ENTRY_POINTS if (ROOT / e).exists()]
    elif args:
        entries = args
    else:
        entries = ["bot.py"]

    run(entries, json_output=json_output)


if __name__ == "__main__":
    main()
