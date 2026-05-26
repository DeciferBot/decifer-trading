#!/usr/bin/env python3
"""
scripts/theme_coverage_report.py — Theme Transmission Graph coverage audit.

Prints per-theme statistics: active / monitor_only / needs_review / proposed counts,
evidence basis breakdown, and highlights symbols with missing or weak evidence.

Usage:
    python3 scripts/theme_coverage_report.py
    python3 scripts/theme_coverage_report.py --json   # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import theme_graph as ttg


def _load_raw() -> tuple[list, list]:
    """Load raw exposures and theme nodes for the report (bypasses evidence gate)."""
    import json as _json

    ttg_dir = os.path.join(_REPO_ROOT, "data", "intelligence", "theme_graph")
    with open(os.path.join(ttg_dir, "symbol_exposures.json")) as f:
        exposures = _json.load(f)["exposures"]
    with open(os.path.join(ttg_dir, "theme_nodes.json")) as f:
        nodes = _json.load(f)["nodes"]
    return exposures, nodes


def build_report() -> dict:
    themes = {n["id"]: n["label"] for n in _load_raw()[1] if n["type"] == "theme"}
    exposures, _ = _load_raw()

    # Bucket exposures by theme
    by_theme: dict[str, list] = {tid: [] for tid in themes}
    unthemed = []
    for exp in exposures:
        tid = exp.get("theme_id", "")
        if tid in by_theme:
            by_theme[tid].append(exp)
        else:
            unthemed.append(exp)

    theme_sections: list[dict] = []
    for tid, label in sorted(themes.items()):
        exps = by_theme[tid]
        active = [e for e in exps if e["status"] == "active" and e.get("route_hint") != "Monitor only"]
        monitor_only = [e for e in exps if e["status"] == "active" and e.get("route_hint") == "Monitor only"]
        needs_review = [e for e in exps if e["status"] == "needs_review"]
        proposed = [e for e in exps if e["status"] == "proposed"]

        # Evidence basis breakdown
        evidence_counts: dict[str, int] = {}
        for e in exps:
            eb = e.get("evidence_basis", "MISSING")
            evidence_counts[eb] = evidence_counts.get(eb, 0) + 1

        # Flags: weak or missing evidence
        weak_flags = [
            f"{e['symbol']} ({e.get('evidence_basis', 'MISSING')} — {e['status']})"
            for e in exps
            if e.get("evidence_basis") in {"LLM_only", "keyword_only", "popular_online",
                                            "weak_co_mention", "generic_sector_match", None}
        ]

        theme_sections.append({
            "theme_id": tid,
            "label": label,
            "total": len(exps),
            "active": len(active),
            "monitor_only": len(monitor_only),
            "needs_review": len(needs_review),
            "proposed": len(proposed),
            "evidence_breakdown": evidence_counts,
            "weak_evidence_flags": weak_flags,
            "active_symbols": sorted(e["symbol"] for e in active),
            "monitor_only_symbols": sorted(e["symbol"] for e in monitor_only),
            "suppressed_symbols": sorted(e["symbol"] for e in needs_review + proposed),
        })

    shadow = ttg.get_shadow_candidates()

    return {
        "schema_version": "1.0",
        "total_exposures": len(exposures),
        "total_shadow_candidates": len(shadow),
        "themes": theme_sections,
        "unthemed_count": len(unthemed),
    }


def print_report(report: dict) -> None:
    print("=" * 70)
    print("THEME TRANSMISSION GRAPH — COVERAGE REPORT")
    print("=" * 70)
    print(f"Total exposures: {report['total_exposures']}")
    print(f"Shadow candidates (active, evidence-gated): {report['total_shadow_candidates']}")
    print()

    for t in report["themes"]:
        print(f"── {t['label']} ({t['theme_id']})")
        print(f"   Total: {t['total']}  |  Active: {t['active']}  |  "
              f"Monitor-only: {t['monitor_only']}  |  "
              f"Needs-review: {t['needs_review']}  |  Proposed: {t['proposed']}")
        if t["active_symbols"]:
            print(f"   Active: {', '.join(t['active_symbols'])}")
        if t["monitor_only_symbols"]:
            print(f"   Monitor: {', '.join(t['monitor_only_symbols'])}")
        if t["suppressed_symbols"]:
            print(f"   Suppressed: {', '.join(t['suppressed_symbols'])}")
        if t["weak_evidence_flags"]:
            print(f"   ⚠ Weak evidence: {', '.join(t['weak_evidence_flags'])}")
        print()

    if report["unthemed_count"]:
        print(f"⚠  Unthemed exposures (orphan theme_id): {report['unthemed_count']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Theme Transmission Graph coverage report")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
