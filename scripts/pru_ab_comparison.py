#!/usr/bin/env python3
"""
pru_ab_comparison.py — Position Research Universe quality comparison.

Applies the revised scoring rules (thesis quality gate, risk penalties, primary archetype,
cluster caps, dedup) to the EXISTING data/position_research_universe.json without any API
calls.  Shows exactly what changes when the new universe_position.py runs.

Usage:
    python3 scripts/pru_ab_comparison.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

# ── Paths ──────────────────────────────────────────────────────────────────────

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRU_PATH = os.path.join(_REPO, "data", "position_research_universe.json")

# ── Inline copies of the new scoring constants (must stay in sync) ─────────────

_CLUSTER_CAPS = {
    "crypto_btc_proxy": (frozenset({
        "MSTR", "MARA", "WULF", "CIFR", "IREN", "APLD", "HUT", "CLSK", "RIOT",
        "COIN", "BTBT", "MIGI", "CORZ",
    }), 2),
    "quantum": (frozenset({
        "IONQ", "QBTS", "RGTI", "QUBT", "IQM", "ARQQ",
    }), 2),
    "ai_infra": (frozenset({
        "NVDA", "AMD", "SMCI", "DELL", "ALAB", "CRDO", "CLS", "AXTI",
        "AAOI", "COHR", "ANET", "AVGO", "MRVL",
    }), 4),
    "nuclear_uranium": (frozenset({
        "SMR", "OKLO", "UEC", "LEU", "NNE", "BWXT", "CCJ", "DNN",
    }), 2),
}

_PREFERRED_SHARE_CLASS = {"GOOG": "GOOGL"}

_MEANINGFUL_ARCHETYPES = frozenset({
    "Quality Compounder", "Growth Leader", "Re-rating Candidate", "Turnaround/Inflection",
})

# ── Re-implementation of new scoring functions (pure — no I/O) ────────────────


def _assign_primary_archetype(fund_sigs: set, tech_sigs: set, above_50d_ma_flag: bool) -> str:
    rev_strong   = "revenue_yoy_gt_10pct"     in fund_sigs
    rev_moderate = "revenue_yoy_gt_5pct"       in fund_sigs
    rev_positive = "revenue_yoy_positive"      in fund_sigs
    any_rev_pos  = rev_strong or rev_moderate or rev_positive
    margin_ok    = "gross_margin_positive"     in fund_sigs
    outperform   = "outperforming_spy_1m"      in tech_sigs
    rs_positive  = outperform or above_50d_ma_flag
    upgrade      = "recent_analyst_upgrade"    in fund_sigs
    upside_high  = "analyst_upside_gt_15pct"   in fund_sigs
    upside_low   = "analyst_upside_positive"   in fund_sigs
    consensus_ok = "consensus_not_negative"    in fund_sigs
    base_build   = "base_building_after_drawdown" in tech_sigs

    if rev_strong and margin_ok and rs_positive:
        return "Quality Compounder"
    if rev_strong or (rev_moderate and upgrade and upside_high):
        return "Growth Leader"
    if upgrade or (upside_low and consensus_ok):
        return "Re-rating Candidate"
    if base_build and any_rev_pos:
        return "Turnaround/Inflection"
    return "Speculative Theme"


def _check_thesis_quality_gate(fund_sigs: set, tech_sigs: set, primary: str) -> bool:
    if "revenue_yoy_gt_10pct"   in fund_sigs: return True
    if "analyst_upside_gt_15pct" in fund_sigs: return True
    if "recent_analyst_upgrade"  in fund_sigs: return True
    if "base_building_after_drawdown" in tech_sigs and "outperforming_spy_1m" in tech_sigs:
        return True
    if primary == "Quality Compounder":         return True
    return False


def _compute_risk_penalties(pru: dict) -> int:
    penalty = 0
    rev    = pru.get("revenue_growth_yoy")
    upside = pru.get("analyst_upside_pct")

    if rev is not None:
        if   rev < -25.0: penalty -= 4
        elif rev < -10.0: penalty -= 2

    if upside is not None:
        if   upside < -30.0: penalty -= 5
        elif upside < -20.0: penalty -= 4
        elif upside < -10.0: penalty -= 2

    has_rev    = rev    is not None and rev    > 0.0
    has_upside = upside is not None and upside > 5.0
    if not has_rev and not has_upside:
        penalty -= 3

    return penalty


def _assign_secondary_tags(fund_sigs: set, tech_sigs: set, above_50d_ma_flag: bool, sector_etf_above_50ma: bool) -> list:
    tags = []
    if "outperforming_spy_1m" in tech_sigs and sector_etf_above_50ma:
        tags.append("Sector/RS Leader")
    if above_50d_ma_flag:
        tags.append("Above 50DMA")
    if "base_building_after_drawdown" in tech_sigs:
        tags.append("Breakout")
    if "recent_analyst_upgrade" in fund_sigs:
        tags.append("Analyst Momentum")
    return tags


_TECH_SIGNALS = {
    "outperforming_spy_1m", "outperforming_sector_1m", "higher_lows",
    "base_building_after_drawdown",
    # hygiene — no longer scored but may still appear in old records
    "above_50d_ma", "sector_etf_above_50ma",
}

_FUND_SIGNALS = {
    "revenue_yoy_gt_10pct", "revenue_yoy_gt_5pct", "revenue_yoy_positive",
    "revenue_decline_slowing", "gross_margin_positive", "debt_not_dangerous",
    "analyst_upside_gt_15pct", "analyst_upside_positive", "consensus_not_negative",
    "recent_analyst_upgrade",
}


def _apply_new_rules(sym: dict) -> dict:
    """Re-score one existing PRU record under new rules. Returns enriched copy."""
    sigs = set(sym.get("discovery_signals", []))
    fund_sigs = sigs & _FUND_SIGNALS
    tech_sigs = sigs & _TECH_SIGNALS

    # Hygiene flags — infer from old signals
    above_50d_ma_flag       = "above_50d_ma"        in sigs
    sector_etf_above_50ma   = "sector_etf_above_50ma" in sigs

    # New base score: strip hygiene signal points
    old_pts = dict(sym.get("discovery_signal_points", {}))
    new_pts = {k: v for k, v in old_pts.items()
               if k not in ("above_50d_ma", "sector_etf_above_50ma")}
    discovery_score = sum(new_pts.values())

    pru_snap   = sym.get("pru_fmp_snapshot", {})
    penalty    = _compute_risk_penalties(pru_snap)
    adj_score  = discovery_score + penalty

    primary    = _assign_primary_archetype(fund_sigs, tech_sigs, above_50d_ma_flag)
    thesis_ok  = _check_thesis_quality_gate(fund_sigs, tech_sigs, primary)
    if not thesis_ok:
        primary = "Tactical Momentum"
        bucket  = "tactical_momentum"
    else:
        bucket  = "core_research"

    tags = _assign_secondary_tags(fund_sigs, tech_sigs, above_50d_ma_flag, sector_etf_above_50ma)

    return {
        **sym,
        "revised_discovery_score":    discovery_score,
        "revised_risk_penalty_pts":   penalty,
        "revised_adjusted_score":     adj_score,
        "revised_primary_archetype":  primary,
        "revised_secondary_tags":     tags,
        "revised_universe_bucket":    bucket,
        "revised_thesis_gate_pass":   thesis_ok,
    }


def _apply_cluster_caps_and_dedup(candidates: list) -> tuple[list, list]:
    """Returns (kept, removed_records_with_reason)."""
    tickers_present = {c["ticker"] for c in candidates}
    preferred_present = {
        v for k, v in _PREFERRED_SHARE_CLASS.items() if v in tickers_present
    }

    cluster_counts = {label: 0 for label in _CLUSTER_CAPS}
    kept    = []
    removed = []

    for c in candidates:
        t = c["ticker"]
        if t in _PREFERRED_SHARE_CLASS and _PREFERRED_SHARE_CLASS[t] in preferred_present:
            removed.append({**c, "removal_reason": f"dedup (prefer {_PREFERRED_SHARE_CLASS[t]})"})
            continue

        cluster_label = None
        capped = False
        for label, (members, cap) in _CLUSTER_CAPS.items():
            if t in members:
                cluster_label = label
                if cluster_counts[label] >= cap:
                    capped = True
                else:
                    cluster_counts[label] += 1
                break

        if capped:
            removed.append({**c, "removal_reason": f"cluster_cap: {cluster_label} (max {_CLUSTER_CAPS[cluster_label][1]})"})
            continue

        c2 = dict(c)
        if cluster_label:
            c2["cluster_label"] = cluster_label
        kept.append(c2)

    return kept, removed


# ── Report helpers ─────────────────────────────────────────────────────────────

def _sep(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 60}")
        print(f"  {title}")
        print('─' * 60)
    else:
        print()


def _score_str(s: dict, score_key: str = "discovery_score") -> str:
    rev  = (s.get("pru_fmp_snapshot") or {}).get("revenue_growth_yoy")
    up   = (s.get("pru_fmp_snapshot") or {}).get("analyst_upside_pct")
    rev_s  = f"{rev:+.0f}%"  if rev  is not None else "  N/A"
    up_s   = f"{up:+.0f}%"   if up   is not None else "  N/A"
    return (
        f"  {s['ticker']:8s}  score={s.get(score_key, '?'):3}  "
        f"rev={rev_s:>7}  upside={up_s:>7}"
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if not os.path.exists(_PRU_PATH):
        print(f"ERROR: {_PRU_PATH} not found. Run refresh_position_research_universe() first.")
        sys.exit(1)

    with open(_PRU_PATH) as f:
        data = json.load(f)

    symbols  = data["symbols"]
    built_at = data.get("built_at", "unknown")

    print("=" * 65)
    print("  A/B COMPARISON — PRU Quality Improvement")
    print(f"  Source: {_PRU_PATH}")
    print(f"  Built:  {built_at}")
    print("=" * 65)

    # ── [A] CURRENT UNIVERSE ─────────────────────────────────────────────────

    old_scores   = [s["discovery_score"] for s in symbols]
    old_avg      = sum(old_scores) / len(old_scores) if old_scores else 0
    old_arch     = Counter()
    for s in symbols:
        for a in s.get("matched_position_archetypes", []):
            old_arch[a] += 1

    _sep("[A] CURRENT UNIVERSE")
    print(f"  Total admitted:        {len(symbols)}")
    print(f"  Average discovery_score: {old_avg:.1f}  (range {min(old_scores)}–{max(old_scores)})")
    print(f"  Archetype counts (multi-assign, current system):")
    for arch, cnt in old_arch.most_common():
        print(f"    {arch:30s}: {cnt}")
    print(f"\n  Top 5 by score:")
    for s in sorted(symbols, key=lambda x: x["discovery_score"], reverse=True)[:5]:
        print(_score_str(s))

    # ── Apply new rules ───────────────────────────────────────────────────────

    revised = [_apply_new_rules(s) for s in symbols]

    # Sort: core_research first, then by revised_adjusted_score
    revised.sort(
        key=lambda r: (r["revised_universe_bucket"] == "core_research", r["revised_adjusted_score"]),
        reverse=True,
    )

    # Over-sample, then cluster caps
    top_raw = revised[:300]
    kept, removed_list = _apply_cluster_caps_and_dedup(top_raw)
    top_n = len(symbols)  # preserve same cap
    top = kept[:top_n]

    # ── [B] REVISED UNIVERSE ─────────────────────────────────────────────────

    new_adj_scores  = [r["revised_adjusted_score"]  for r in top]
    new_base_scores = [r["revised_discovery_score"] for r in top]
    new_avg_adj     = sum(new_adj_scores)  / len(new_adj_scores)  if new_adj_scores  else 0
    new_avg_base    = sum(new_base_scores) / len(new_base_scores) if new_base_scores else 0
    core_n          = sum(1 for r in top if r["revised_universe_bucket"] == "core_research")
    tactical_n      = len(top) - core_n

    new_arch = Counter(r["revised_primary_archetype"] for r in top)

    _sep("[B] REVISED UNIVERSE (new rules applied to same data)")
    print(f"  Total admitted:           {len(top)}")
    print(f"  Average base score:        {new_avg_base:.1f}  (hygiene points stripped)")
    print(f"  Average adjusted score:    {new_avg_adj:.1f}  (after risk penalties)")
    print(f"  Core Research:             {core_n}")
    print(f"  Tactical Momentum:         {tactical_n}")
    print(f"  Primary archetype counts (single-assign, new system):")
    for arch, cnt in new_arch.most_common():
        pct = cnt / len(top) * 100
        print(f"    {arch:30s}: {cnt:3d}  ({pct:.0f}%)")
    print(f"\n  Top 5 by adjusted score:")
    for r in top[:5]:
        print(_score_str(r, "revised_adjusted_score") + f"  [{r['revised_primary_archetype']}]")

    # ── [DELTA] NAMES REMOVED BY CLUSTER CAPS / DEDUP ────────────────────────

    _sep("[DELTA] NAMES REMOVED BY CLUSTER CAPS AND DEDUP")
    if removed_list:
        for r in sorted(removed_list, key=lambda x: x.get("revised_adjusted_score", 0), reverse=True):
            reason = r.get("removal_reason", "?")
            pru    = r.get("pru_fmp_snapshot", {}) or {}
            rev    = pru.get("revenue_growth_yoy")
            up     = pru.get("analyst_upside_pct")
            print(
                f"  {r['ticker']:8s}  adj={r.get('revised_adjusted_score',0):3}  "
                f"reason={reason}"
                + (f"  rev={rev:+.0f}%" if rev is not None else "")
                + (f"  upside={up:+.0f}%" if up is not None else "")
            )
    else:
        print("  None.")

    # ── [DELTA] NAMES MOVED TO TACTICAL MOMENTUM ─────────────────────────────

    tactical_names = [r for r in top if r["revised_universe_bucket"] == "tactical_momentum"]
    _sep(f"[DELTA] NAMES MOVED TO TACTICAL MOMENTUM  ({len(tactical_names)} names)")
    if tactical_names:
        # Show why each one failed the thesis gate
        for r in sorted(tactical_names, key=lambda x: x.get("revised_discovery_score", 0), reverse=True):
            sigs = set(r.get("discovery_signals", []))
            reasons = []
            if "revenue_yoy_gt_10pct"    not in sigs: reasons.append("no_strong_revenue")
            if "analyst_upside_gt_15pct" not in sigs: reasons.append("no_analyst_upside_gt_15")
            if "recent_analyst_upgrade"  not in sigs: reasons.append("no_upgrade")
            pru = r.get("pru_fmp_snapshot", {}) or {}
            rev = pru.get("revenue_growth_yoy")
            print(
                f"  {r['ticker']:8s}  base={r['revised_discovery_score']:2}  "
                f"adj={r['revised_adjusted_score']:3}  "
                f"why: {', '.join(reasons)}"
                + (f"  (rev={rev:+.0f}%)" if rev is not None else "")
            )
    else:
        print("  None — all admitted names passed the thesis quality gate.")

    # ── [DELTA] NAMES PENALISED ───────────────────────────────────────────────

    penalised = [r for r in top if r["revised_risk_penalty_pts"] != 0]
    penalised.sort(key=lambda x: x["revised_risk_penalty_pts"])
    _sep(f"[DELTA] NAMES PENALISED  ({len(penalised)} names)")
    if penalised:
        print(f"  {'Ticker':8}  {'Base':>5}  {'Penalty':>7}  {'Adj':>5}  Reason")
        for r in penalised:
            pru    = r.get("pru_fmp_snapshot", {}) or {}
            rev    = pru.get("revenue_growth_yoy")
            up     = pru.get("analyst_upside_pct")
            reasons = []
            if rev is not None and rev < -25: reasons.append(f"rev={rev:+.0f}%")
            elif rev is not None and rev < -10: reasons.append(f"rev={rev:+.0f}%")
            if up is not None and up < -10:   reasons.append(f"upside={up:+.0f}%")
            has_rv = rev is not None and rev > 0
            has_up = up  is not None and up  > 5
            if not has_rv and not has_up:     reasons.append("no_thesis")
            print(
                f"  {r['ticker']:8s}  {r['revised_discovery_score']:5}  "
                f"{r['revised_risk_penalty_pts']:7}  {r['revised_adjusted_score']:5}  "
                f"{', '.join(reasons)}"
            )
    else:
        print("  None.")

    # ── [DELTA] DEDUP REMOVED ─────────────────────────────────────────────────

    dedup_removed = [r for r in removed_list if "dedup" in r.get("removal_reason", "")]
    if dedup_removed:
        _sep("[DELTA] DEDUP REMOVED")
        for r in dedup_removed:
            preferred = _PREFERRED_SHARE_CLASS.get(r["ticker"], "?")
            preferred_score = next(
                (x.get("revised_adjusted_score") for x in top if x["ticker"] == preferred), "?"
            )
            print(
                f"  {r['ticker']} removed  (prefer {preferred})  "
                f"adj_score: {r.get('revised_adjusted_score','?')} vs {preferred_score}"
            )

    # ── [DELTA] PRESERVED DESPITE LOW SCORE ──────────────────────────────────

    preserved = [
        r for r in top
        if r["revised_universe_bucket"] == "core_research"
        and r["revised_adjusted_score"] < 13
    ]
    _sep(f"[DELTA] CORE RESEARCH NAMES WITH LOW ADJ SCORE (<13)  ({len(preserved)} names)")
    if preserved:
        for r in sorted(preserved, key=lambda x: x["revised_adjusted_score"]):
            sigs = set(r.get("discovery_signals", []))
            qual = []
            if "revenue_yoy_gt_10pct"    in sigs: qual.append("strong_rev")
            if "analyst_upside_gt_15pct" in sigs: qual.append("analyst_upside")
            if "recent_analyst_upgrade"  in sigs: qual.append("upgrade")
            print(
                f"  {r['ticker']:8s}  adj={r['revised_adjusted_score']:3}  "
                f"archetype={r['revised_primary_archetype']}  "
                f"qualifying: {', '.join(qual) or '?'}"
            )
    else:
        print("  None.")

    # ── [SUMMARY] ─────────────────────────────────────────────────────────────

    old_core_est = sum(
        1 for s in symbols
        if any(sig in set(s.get("discovery_signals", []))
               for sig in ("revenue_yoy_gt_10pct", "analyst_upside_gt_15pct", "recent_analyst_upgrade"))
    )
    old_tactical_est = len(symbols) - old_core_est

    weak_removed = sum(
        1 for r in removed_list
        if r["revised_universe_bucket"] == "tactical_momentum"
    )
    cluster_removed = sum(
        1 for r in removed_list
        if "cluster_cap" in r.get("removal_reason", "")
    )

    _sep("[SUMMARY]")
    print(f"  Old Core Research (estimated):       {old_core_est}")
    print(f"  Old Tactical Momentum (estimated):   {old_tactical_est}")
    print(f"  New Core Research:                   {core_n}")
    print(f"  New Tactical Momentum (in top {top_n}):   {tactical_n}")
    print(f"  Names removed by cluster caps:       {cluster_removed}")
    print(f"  Names removed by dedup:              {len(dedup_removed)}")
    print(f"  Average score change (base):         {new_avg_base - old_avg:+.1f}  (hygiene pts stripped)")
    print(f"  Average score change (adjusted):     {new_avg_adj - old_avg:+.1f}  (after risk penalties)")
    print()

    print(f"  TOP 30 CORE RESEARCH CANDIDATES (revised):")
    core_top = [r for r in top if r["revised_universe_bucket"] == "core_research"][:30]
    for i, r in enumerate(core_top, 1):
        pru   = r.get("pru_fmp_snapshot", {}) or {}
        rev   = pru.get("revenue_growth_yoy")
        up    = pru.get("analyst_upside_pct")
        rev_s = f"{rev:+.0f}%" if rev is not None else "  N/A"
        up_s  = f"{up:+.0f}%"  if up  is not None else "  N/A"
        print(
            f"  {i:3d}. {r['ticker']:8s}  adj={r['revised_adjusted_score']:3}  "
            f"rev={rev_s:>7}  upside={up_s:>7}  [{r['revised_primary_archetype']}]"
        )

    print()
    print("  CLUSTER CONCENTRATION AFTER CAPS:")
    cluster_tally = Counter(
        r.get("cluster_label", "none")
        for r in top
        if r.get("cluster_label")
    )
    for label, cap_def in _CLUSTER_CAPS.items():
        n = cluster_tally.get(label, 0)
        print(f"    {label:20s}: {n} (cap={cap_def[1]})")

    _sep()
    print("  Done.")


if __name__ == "__main__":
    main()
