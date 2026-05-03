#!/usr/bin/env python3
"""
apex_cap_replay.py
Offline replay of the Apex top-30 hard cap against the most recent real scan cycle.

Run:
    python3 scripts/apex_cap_replay.py

Reads:
  data/signals_log.jsonl              — post-pipeline scored candidates, grouped by scan_id
  data/position_research_universe.json — current PRU for Tier D tagging

Reports:
  - Candidates before cap
  - Tier D before cap
  - Tier D after cap
  - Dropped Tier D (with scores and discovery quality)
  - Whether the hard cap would have blocked Tier D

No orders placed. No live APIs called. No files written.

NOTE: signals_log.jsonl records post-pipeline, pre-guardrails candidates.
Guardrails (filter_candidates in guardrails.py) further removes held symbols,
cooldown symbols, open-order symbols, etc. using live state not available here.
The replay therefore uses an upper bound on the real cap input (≤73 in practice).
"""
import json
import os
import sys
from collections import defaultdict

REPO         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS_LOG  = os.path.join(REPO, "data", "signals_log.jsonl")
PRU_JSON     = os.path.join(REPO, "data", "position_research_universe.json")
CAP_LIMIT    = 30


# ── 1. Load most recent real scan cycle ─────────────────────────────────────

def _load_most_recent_scan() -> tuple[str, list[dict]]:
    """Return (scan_id, candidates) for the scan_id with the latest timestamp."""
    by_scan: dict[str, list[dict]] = defaultdict(list)
    latest_ts: dict[str, str] = {}

    with open(SIGNALS_LOG) as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            sid = rec.get("scan_id")
            if not sid:
                continue
            by_scan[sid].append(rec)
            ts = rec.get("ts", "")
            if ts > latest_ts.get(sid, ""):
                latest_ts[sid] = ts

    if not by_scan:
        sys.exit("ERROR: no scan_id entries found in signals_log.jsonl")

    best_sid = max(latest_ts, key=lambda s: latest_ts[s])
    return best_sid, by_scan[best_sid]


# ── 2. Load PRU ──────────────────────────────────────────────────────────────

def _load_pru() -> tuple[dict, str, int]:
    """Return (pru_meta dict, built_at str, count int)."""
    with open(PRU_JSON) as fh:
        data = json.load(fh)
    pru_meta: dict[str, dict] = {}
    for entry in data.get("symbols", []):
        sym = entry.get("ticker") or entry.get("symbol")
        if sym:
            pru_meta[sym] = entry
    return pru_meta, data.get("built_at", "unknown"), data.get("count", len(pru_meta))


# ── 3. Tag Tier D and run cap ────────────────────────────────────────────────

def _tag_and_cap(candidates: list[dict], pru_meta: dict) -> dict:
    """Tag Tier D, sort by score, apply CAP_LIMIT. Return analysis dict."""
    for c in candidates:
        sym = c.get("symbol", "")
        if sym in pru_meta:
            meta = pru_meta[sym]
            c["scanner_tier"] = "D"
            c.setdefault("discovery_score", meta.get("discovery_score", 0))
            c.setdefault("matched_position_archetypes", meta.get("matched_position_archetypes", []))
            c.setdefault("discovery_signals", meta.get("discovery_signals", []))

    sorted_all = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)
    selected   = sorted_all[:CAP_LIMIT]
    dropped    = sorted_all[CAP_LIMIT:]

    td_before  = [c for c in sorted_all if c.get("scanner_tier") == "D"]
    td_after   = [c for c in selected   if c.get("scanner_tier") == "D"]
    td_dropped = [c for c in dropped    if c.get("scanner_tier") == "D"]

    return {
        "sorted_all":  sorted_all,
        "selected":    selected,
        "dropped":     dropped,
        "td_before":   td_before,
        "td_after":    td_after,
        "td_dropped":  td_dropped,
        "min_selected_score":         min((c.get("score", 0) for c in selected), default=None),
        "highest_dropped_td_score":   max((c.get("score", 0) for c in td_dropped), default=None),
        "max_td_score_before_cap":    max((c.get("score", 0) for c in td_before), default=None),
    }


# ── 4. Print report ──────────────────────────────────────────────────────────

def _fmt_candidate_row(c: dict) -> str:
    sym    = c.get("symbol", "?")
    score  = c.get("score", 0)
    tier   = c.get("scanner_tier", "")
    dscore = c.get("discovery_score") or ""
    archs  = c.get("matched_position_archetypes") or []
    arch_s = ", ".join(archs) if archs else "—"
    tier_s = f"[D ds={dscore}]" if tier == "D" else "[  ]"
    return f"  {sym:<8}  score={score:>5.1f}  {tier_s}  archetypes={arch_s}"


def _print_report(scan_id: str, pru_built: str, pru_count: int, r: dict) -> None:
    W = 65
    print("═" * W)
    print(f"  APEX CAP REPLAY — scan_id {scan_id}")
    print(f"  Source : data/signals_log.jsonl")
    print(f"  PRU    : {pru_count} symbols  built {pru_built[:10]}")
    print(f"  NOTE   : pre-guardrails replay — real cap input ≤ {len(r['sorted_all'])}")
    print("═" * W)
    print()

    total  = len(r["sorted_all"])
    n_td   = len(r["td_before"])
    n_non  = total - n_td
    print(f"CANDIDATES BEFORE CAP:  {total}  (cap limit = {CAP_LIMIT})")
    print(f"  Non-Tier D: {n_non}   Tier D: {n_td}")
    print()

    print(f"TIER D BEFORE CAP:  {n_td} symbol(s)")
    if r["td_before"]:
        for c in sorted(r["td_before"], key=lambda x: x.get("score", 0), reverse=True):
            print(_fmt_candidate_row(c))
    else:
        print("  (none — no PRU symbols in this scan cycle)")
    print()

    print(f"TIER D AFTER CAP:  {len(r['td_after'])} symbol(s)")
    if r["td_after"]:
        for c in sorted(r["td_after"], key=lambda x: x.get("score", 0), reverse=True):
            print(_fmt_candidate_row(c))
    else:
        print("  (none)")
    print()

    print(f"DROPPED TIER D:  {len(r['td_dropped'])} symbol(s)")
    if r["td_dropped"]:
        for c in sorted(r["td_dropped"], key=lambda x: x.get("score", 0), reverse=True):
            print(_fmt_candidate_row(c))
    else:
        print("  (none)")
    print()

    print("─" * W)
    blocked = len(r["td_dropped"]) > 0
    if blocked:
        print(f"VERDICT: Hard cap DID block Tier D candidates.")
        print(f"  Score boundary:  min_selected={r['min_selected_score']}  "
              f"highest_dropped_td={r['highest_dropped_td_score']}")
        archs_dropped = sum(
            1 for c in r["td_dropped"] if c.get("matched_position_archetypes")
        )
        strong_disc = sum(
            1 for c in r["td_dropped"] if (c.get("discovery_score") or 0) >= 6
        )
        if archs_dropped:
            print(f"  {archs_dropped} dropped Tier D symbol(s) had matched archetypes.")
        if strong_disc:
            print(f"  {strong_disc} dropped Tier D symbol(s) had discovery_score >= 6.")
    else:
        if total <= CAP_LIMIT:
            print(f"VERDICT: Hard cap did NOT activate (total={total} <= cap={CAP_LIMIT}).")
            print(f"  All {n_td} Tier D candidate(s) passed through unchanged.")
        else:
            print(f"VERDICT: Hard cap activated ({total} > {CAP_LIMIT}) "
                  f"but did NOT drop any Tier D.")
            print(f"  All {n_td} Tier D candidate(s) scored above the cap boundary.")
            if r["min_selected_score"] is not None:
                print(f"  Score boundary: min_selected={r['min_selected_score']}  "
                      f"max_td_score={r['max_td_score_before_cap']}")

    print("═" * W)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    scan_id, candidates = _load_most_recent_scan()
    pru_meta, pru_built, pru_count = _load_pru()
    result = _tag_and_cap(candidates, pru_meta)
    _print_report(scan_id, pru_built, pru_count, result)


if __name__ == "__main__":
    main()
