#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  scripts/enrich_ttg.py                     ║
# ║   Systematic TTG enrichment — reconcile roster → graph       ║
# ║   Layer: INTELLIGENCE tooling — no execution imports         ║
# ╚══════════════════════════════════════════════════════════════╝
"""
enrich_ttg.py — Systematic Theme Transmission Graph enrichment.

The trading roster (thematic_roster.json) is the authoritative internal source
(`internal_symbol_master` — in the accepted evidence gate). It already maps every
name Apex trades to a theme. The customer-facing TTG (theme_graph/symbol_exposures.json)
drifts behind it. This tool reconciles the two:

  1. Compute the gap — roster symbols not yet in the TTG, grouped by theme.
  2. For each missing symbol: confirm via FMP company profile (real label/sector),
     draft an exposure with evidence_basis=internal_symbol_master, status=needs_review.
  3. Per-symbol reason_to_care is parsed from the roster's own FMP-verified notes
     (NOT invented — the roster notes are reference_data_approved).
  4. Write data/intelligence/theme_graph/proposed_exposures.json (never touches the
     live graph directly — proposal-gated, same pattern as proposed_calibrated_weights).

Promotion (--promote <theme_id>) merges a reviewed theme's exposures into the live
symbol_exposures.json and adds the theme/bucket nodes to theme_nodes.json with
status=active. Only run --promote after the proposals are reviewed.

Usage:
    python3 scripts/enrich_ttg.py                    # full gap report → proposed_exposures.json
    python3 scripts/enrich_ttg.py --theme memory_storage   # one theme
    python3 scripts/enrich_ttg.py --promote memory_storage # promote reviewed theme to active
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

_TTG_DIR   = os.path.join(_REPO, "data", "intelligence", "theme_graph")
_EXPOSURES = os.path.join(_TTG_DIR, "symbol_exposures.json")
_NODES     = os.path.join(_TTG_DIR, "theme_nodes.json")
_ROSTER    = os.path.join(_REPO, "data", "intelligence", "thematic_roster.json")
_PROPOSED  = os.path.join(_TTG_DIR, "proposed_exposures.json")

# Roster theme_id → (TTG theme_id, theme_label, bucket_id, bucket_label, driver_id,
#                     theme_description, theme_risk)
# Only themes with a confirmed driver mapping can be promoted. Memory is the first.
THEME_MAP: dict[str, dict] = {
    "memory_storage": {
        "theme_id": "memory_storage",
        "theme_label": "Memory & Storage Cycle",
        "bucket_id": "memory_hbm_storage",
        "bucket_label": "HBM, DRAM, NAND & Enterprise Storage",
        "driver_id": "ai_capex_growth",
        "theme_description": (
            "AI data-centre buildout is driving structural demand for high-bandwidth "
            "memory (HBM), DRAM, NAND flash, and enterprise storage. Memory pricing is "
            "cyclical, but AI training and inference create a demand floor independent of "
            "the traditional PC/handset cycle."
        ),
        "theme_risk": (
            "Memory is a commodity cycle — oversupply and pricing collapses are the key "
            "risk. HBM demand depends on sustained hyperscaler AI capex."
        ),
    },
}


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _fmp_profile(symbol: str) -> dict:
    """Fetch FMP company profile for label/sector confirmation. {} on failure."""
    try:
        import requests
        key = os.environ.get("FMP_API_KEY", "")
        if not key:
            return {}
        r = requests.get(
            "https://financialmodelingprep.com/stable/profile",
            params={"symbol": symbol, "apikey": key}, timeout=8,
        )
        d = r.json()
        return d[0] if isinstance(d, list) and d else {}
    except Exception:
        return {}


def _parse_roster_notes(notes: str) -> dict[str, str]:
    """
    Roster notes format: 'MU (Micron — ...). SNDK (...). '
    Extract per-symbol reason_to_care from the parenthetical for each ticker.
    """
    out: dict[str, str] = {}
    # Match: TICKER (text up to the matching close paren)
    for m in re.finditer(r"\b([A-Z]{1,5})\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", notes):
        sym, body = m.group(1), m.group(2).strip()
        # Normalise the em-dash separator and clip
        body = body.replace("—", "-").strip(" .-")
        if body:
            out[sym] = body
    return out


def compute_gap() -> dict[str, list[str]]:
    """Return {roster_theme_id: [missing_symbols]} for symbols not in active TTG."""
    ttg = _read(_EXPOSURES)
    ttg_syms = {e["symbol"].upper() for e in ttg["exposures"]}
    roster = _read(_ROSTER)
    gap: dict[str, list[str]] = {}
    for r in roster.get("rosters", []):
        tid = r.get("theme_id", "")
        miss = [s.upper() for s in r.get("core_symbols", []) if s.upper() not in ttg_syms]
        if miss:
            gap[tid] = miss
    return gap


def build_proposals(theme_filter: str | None = None) -> dict:
    """Generate proposed exposures for the roster→TTG gap."""
    roster = _read(_ROSTER)
    roster_by_theme = {r["theme_id"]: r for r in roster.get("rosters", [])}
    gap = compute_gap()

    proposals = []
    unmapped_themes = []
    for tid, missing in sorted(gap.items()):
        if theme_filter and tid != theme_filter:
            continue
        mapping = THEME_MAP.get(tid)
        if mapping is None:
            unmapped_themes.append({"roster_theme": tid, "missing_symbols": missing,
                                    "status": "needs_driver_mapping"})
            continue
        notes = roster_by_theme.get(tid, {}).get("notes", "")
        per_sym_reason = _parse_roster_notes(notes)
        for sym in missing:
            profile = _fmp_profile(sym)
            label = profile.get("companyName") or sym
            reason = per_sym_reason.get(sym) or (
                f"{label} — {mapping['theme_label']} exposure (roster-confirmed)."
            )
            proposals.append({
                "symbol": sym,
                "label": label,
                "driver_id": mapping["driver_id"],
                "theme_id": mapping["theme_id"],
                "bucket_id": mapping["bucket_id"],
                "exposure_type": "direct_beneficiary",
                "confidence": 0.78,   # conservative default for roster-sourced names
                "reason_to_care": reason,
                "evidence_basis": "internal_symbol_master",
                "source_type": "internal_symbol_master",
                "route_hint": "Watchlist",
                "status": "needs_review",
                "risk_note": mapping["theme_risk"],
                "last_reviewed": datetime.now(UTC).strftime("%Y-%m-%d"),
                "_fmp_sector": profile.get("sector"),
                "_fmp_industry": profile.get("industry"),
            })

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "roster_reconciliation",
        "total_proposed": len(proposals),
        "proposals": proposals,
        "unmapped_themes": unmapped_themes,
    }


def promote(theme_id: str) -> None:
    """Merge a reviewed theme's proposals into the live TTG with status=active."""
    mapping = THEME_MAP.get(theme_id)
    if mapping is None:
        print(f"ERROR: no driver mapping for theme '{theme_id}'. Add it to THEME_MAP first.")
        sys.exit(1)

    proposed = _read(_PROPOSED) if os.path.exists(_PROPOSED) else {}
    to_promote = [p for p in proposed.get("proposals", []) if p["theme_id"] == theme_id]
    if not to_promote:
        print(f"No proposals found for '{theme_id}'. Run without --promote first.")
        sys.exit(1)

    # 1. Add theme + bucket nodes if absent
    nodes = _read(_NODES)
    node_ids = {n["id"] for n in nodes["nodes"]}
    added_nodes = []
    if mapping["theme_id"] not in node_ids:
        nodes["nodes"].append({
            "id": mapping["theme_id"], "label": mapping["theme_label"], "type": "theme",
            "plain_english_description": mapping["theme_description"],
            "status": "active", "risk_note": mapping["theme_risk"],
        })
        added_nodes.append(mapping["theme_id"])
    if mapping["bucket_id"] not in node_ids:
        nodes["nodes"].append({
            "id": mapping["bucket_id"], "label": mapping["bucket_label"], "type": "bucket",
            "plain_english_description": mapping["theme_description"],
            "status": "active", "risk_note": mapping["theme_risk"],
        })
        added_nodes.append(mapping["bucket_id"])
    if added_nodes:
        _write(_NODES, nodes)

    # 2. Merge exposures (status=active), dedup by symbol+theme
    ttg = _read(_EXPOSURES)
    existing = {(e["symbol"].upper(), e.get("theme_id")) for e in ttg["exposures"]}
    added = []
    for p in to_promote:
        keyc = (p["symbol"].upper(), p["theme_id"])
        if keyc in existing:
            continue
        clean = {k: v for k, v in p.items() if not k.startswith("_")}
        clean["status"] = "active"
        ttg["exposures"].append(clean)
        added.append(p["symbol"])
    _write(_EXPOSURES, ttg)

    print(f"Promoted theme '{theme_id}':")
    print(f"  nodes added: {added_nodes or 'none (already present)'}")
    print(f"  exposures added (active): {added}")
    print(f"  TTG total now: {len(ttg['exposures'])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", help="Limit proposals to one roster theme_id")
    ap.add_argument("--promote", help="Promote a reviewed theme to active in the live TTG")
    args = ap.parse_args()

    if args.promote:
        promote(args.promote)
        return

    result = build_proposals(theme_filter=args.theme)
    _write(_PROPOSED, result)
    print(f"Wrote {result['total_proposed']} proposals → {_PROPOSED}")
    if result["unmapped_themes"]:
        print(f"\n{len(result['unmapped_themes'])} roster themes need a driver mapping "
              f"before they can be promoted:")
        for u in result["unmapped_themes"]:
            print(f"  {u['roster_theme']}: {u['missing_symbols']}")


if __name__ == "__main__":
    main()
