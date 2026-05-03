#!/usr/bin/env python3
"""
tier_d_evidence_report.py
Phase 1 Position Research Universe — real scan-cycle evidence report.

Run after several scan cycles have completed:
    python3 scripts/tier_d_evidence_report.py

Reads:
  data/tier_d_funnel.jsonl          — per-cycle attrition counts (stages 1-11)
  data/position_research_shadow.jsonl — shadow validation outcomes (stage 11)
  data/position_research_universe.json — PRU metadata

Phase 2 gate: collect evidence from multiple scan cycles, then review
with Amit. Do NOT enable live Tier D POSITION entries until this report
shows all ✓ in Section 8.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FUNNEL_JSONL = os.path.join(REPO, "data", "tier_d_funnel.jsonl")
SHADOW_JSONL = os.path.join(REPO, "data", "position_research_shadow.jsonl")
PRU_JSON     = os.path.join(REPO, "data", "position_research_universe.json")
TRAINING     = os.path.join(REPO, "data", "training_records.jsonl")
TRADE_EVENTS = os.path.join(REPO, "data", "trade_events.jsonl")

SEP = "=" * 72

# ── Re-classification constants (mirrors universe_position.py) ─────────────

_TECH_SIGNALS = frozenset({
    "outperforming_spy_1m", "outperforming_sector_1m",
    "above_50d_ma", "sector_etf_above_50ma",
    "higher_lows", "base_building_after_drawdown",
})

_CLUSTER_CAPS: dict[str, tuple[frozenset, int]] = {
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

_PREFERRED_SHARE_CLASS: dict[str, str] = {"GOOG": "GOOGL"}


def _load_jsonl(path: str) -> list[dict]:
    records = []
    if not os.path.exists(path):
        return records
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return records


def _load_pru() -> dict:
    if not os.path.exists(PRU_JSON):
        return {}
    with open(PRU_JSON) as f:
        return json.load(f)


def _ts_display(ts_str: str) -> str:
    if not ts_str:
        return "?"
    return ts_str[:19].replace("T", " ")


def section(title: str) -> None:
    print(f"\n{SEP}")
    print(title)
    print(SEP)


# ── PRU Re-classification ──────────────────────────────────────────────────


def _assign_primary_archetype(fund_pts: dict, tech_pts: dict, above_50d_ma_flag: bool) -> str:
    rev_strong  = "revenue_yoy_gt_10pct" in fund_pts
    rev_moderate= "revenue_yoy_gt_5pct"  in fund_pts
    rev_positive= "revenue_yoy_positive" in fund_pts
    any_rev_pos = rev_strong or rev_moderate or rev_positive
    margin_ok   = "gross_margin_positive" in fund_pts
    outperform  = "outperforming_spy_1m" in tech_pts
    rs_positive = outperform or above_50d_ma_flag
    recent_upg  = "recent_analyst_upgrade" in fund_pts
    upside_high = "analyst_upside_gt_15pct" in fund_pts
    upside_low  = "analyst_upside_positive" in fund_pts
    consensus_ok= "consensus_not_negative" in fund_pts
    base_build  = "base_building_after_drawdown" in tech_pts

    if rev_strong and margin_ok and rs_positive:
        return "Quality Compounder"
    if rev_strong or (rev_moderate and recent_upg and upside_high):
        return "Growth Leader"
    if recent_upg or (upside_low and consensus_ok):
        return "Re-rating Candidate"
    if base_build and any_rev_pos:
        return "Turnaround/Inflection"
    return "Speculative Theme"


def _check_thesis_quality_gate(fund_pts: dict, tech_pts: dict, primary_archetype: str) -> bool:
    if "revenue_yoy_gt_10pct" in fund_pts:
        return True
    if "analyst_upside_gt_15pct" in fund_pts:
        return True
    if "recent_analyst_upgrade" in fund_pts:
        return True
    if "base_building_after_drawdown" in tech_pts and "outperforming_spy_1m" in tech_pts:
        return True
    if primary_archetype == "Quality Compounder":
        return True
    return False


def _compute_risk_penalties(snap: dict) -> int:
    penalty = 0
    rev = snap.get("revenue_growth_yoy")
    if rev is not None:
        if rev < -25.0:
            penalty -= 4
        elif rev < -10.0:
            penalty -= 2
    upside = snap.get("analyst_upside_pct")
    if upside is not None:
        if upside < -30.0:
            penalty -= 5
        elif upside < -20.0:
            penalty -= 4
        elif upside < -10.0:
            penalty -= 2
    has_rev_strength  = rev is not None and rev > 0.0
    has_analyst_support = upside is not None and upside > 5.0
    if not has_rev_strength and not has_analyst_support:
        penalty -= 3
    return penalty


def _classify_pru_symbols(pru_meta: dict[str, dict]) -> dict[str, dict]:
    """
    Enrich PRU symbols with universe_bucket, primary_archetype,
    adjusted_discovery_score, risk_penalty_pts.

    Uses stored fields if present (new PRU built after the quality upgrade).
    Falls back to inline re-derivation from discovery_signal_points for
    older PRU files that pre-date the upgrade.
    """
    result: dict[str, dict] = {}
    for ticker, entry in pru_meta.items():
        if "universe_bucket" in entry and "primary_archetype" in entry:
            result[ticker] = entry
            continue

        # Old PRU — re-derive from stored signal data
        sig_pts = entry.get("discovery_signal_points", {})
        sigs    = set(entry.get("discovery_signals", []))

        # Split into fundamental and technical point dicts
        fund_pts = {s: sig_pts.get(s, 1) for s in sigs if s not in _TECH_SIGNALS}
        tech_pts = {s: sig_pts.get(s, 1) for s in sigs if s in _TECH_SIGNALS}
        above_50d_ma_flag = "above_50d_ma" in sigs

        snap = entry.get("pru_fmp_snapshot", {})
        risk_penalty = _compute_risk_penalties(snap)

        # Deduct hygiene points that the upgrade removed from scoring
        hygiene_deduction = sig_pts.get("above_50d_ma", 0) + sig_pts.get("sector_etf_above_50ma", 0)
        ds        = entry.get("discovery_score", 0)
        adj_score = ds - hygiene_deduction + risk_penalty

        primary     = _assign_primary_archetype(fund_pts, tech_pts, above_50d_ma_flag)
        thesis_pass = _check_thesis_quality_gate(fund_pts, tech_pts, primary)
        bucket      = "core_research" if thesis_pass else "tactical_momentum"

        result[ticker] = {
            **entry,
            "universe_bucket":          bucket,
            "primary_archetype":        primary,
            "adjusted_discovery_score": adj_score,
            "risk_penalty_pts":         risk_penalty,
        }
    return result


def _apply_caps_dedup_report(
    classified: dict[str, dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Apply cluster caps and dedup to classified PRU symbols.
    Returns (kept, cluster_removed, dedup_removed).
    Sorts by (core_research first, adjusted_discovery_score desc) before applying caps.
    """
    sorted_syms = sorted(
        classified.values(),
        key=lambda r: (r["universe_bucket"] == "core_research", r.get("adjusted_discovery_score", 0)),
        reverse=True,
    )

    tickers_present   = {s["ticker"] for s in sorted_syms}
    preferred_present = {
        preferred
        for dropped, preferred in _PREFERRED_SHARE_CLASS.items()
        if preferred in tickers_present
    }

    cluster_counts: dict[str, int] = {label: 0 for label in _CLUSTER_CAPS}
    kept:            list[dict] = []
    cluster_removed: list[dict] = []
    dedup_removed:   list[dict] = []

    for sym in sorted_syms:
        ticker = sym["ticker"]

        if ticker in _PREFERRED_SHARE_CLASS and _PREFERRED_SHARE_CLASS[ticker] in preferred_present:
            dedup_removed.append(sym)
            continue

        cluster_label: str | None = None
        capped = False
        for label, (members, cap) in _CLUSTER_CAPS.items():
            if ticker in members:
                cluster_label = label
                if cluster_counts[label] >= cap:
                    capped = True
                else:
                    cluster_counts[label] += 1
                break

        if capped:
            cluster_removed.append({**sym, "cluster_label": cluster_label})
            continue

        entry = dict(sym)
        if cluster_label:
            entry["cluster_label"] = cluster_label
        kept.append(entry)

    return kept, cluster_removed, dedup_removed


def _read_config_flags() -> tuple[bool | None, bool | None]:
    """Return (shadow_mode, allow_live) by importing CONFIG the same way the bot does."""
    try:
        sys.path.insert(0, REPO)
        from config import CONFIG
        eg = CONFIG.get("entry_gate", {})
        shadow_mode = eg.get("position_research_shadow_mode", True)
        allow_live  = eg.get("position_research_allow_live_position_entries", False)
        return shadow_mode, allow_live
    except Exception as exc:
        print(f"  ⚠  Could not import CONFIG: {exc}")
        return None, None


def main() -> None:
    funnel_raw   = _load_jsonl(FUNNEL_JSONL)
    shadow       = _load_jsonl(SHADOW_JSONL)
    pru_data     = _load_pru()
    training     = _load_jsonl(TRAINING)
    trade_events = _load_jsonl(TRADE_EVENTS)

    pru_meta: dict[str, dict] = {}
    pru_built_at = pru_data.get("built_at", "?")
    pru_count    = pru_data.get("count", 0)
    for sym_entry in pru_data.get("symbols", []):
        pru_meta[sym_entry["ticker"]] = sym_entry

    funnel_pipeline       = [r for r in funnel_raw if r.get("stage") == "pipeline"]
    funnel_dispatch       = [r for r in funnel_raw if r.get("stage") == "dispatch"]
    funnel_apex_cap       = [r for r in funnel_raw if r.get("stage") == "apex_cap"]
    funnel_shadow_compare = [r for r in funnel_raw if r.get("stage") == "apex_cap_shadow_compare"]
    funnel_shadow_apex    = [r for r in funnel_raw if r.get("stage") == "tier_d_shadow_apex"]

    print(f"\nTier D Evidence Report  —  generated {datetime.now(timezone.utc).isoformat()[:19]}Z")
    print(f"PRU file:    {PRU_JSON}")
    print(f"PRU built:   {_ts_display(pru_built_at)}  ({pru_count} symbols)")
    print(f"Funnel records:  {len(funnel_pipeline)} pipeline + {len(funnel_dispatch)} dispatch + "
          f"{len(funnel_apex_cap)} apex_cap + {len(funnel_shadow_compare)} shadow_compare")
    print(f"Shadow records:  {len(shadow)}")

    if not funnel_pipeline and not shadow:
        print("\n⚠  No data yet. Run several scan cycles first, then re-run this report.")
        sys.exit(0)

    # Classify PRU symbols (handles both old and new PRU formats)
    classified = _classify_pru_symbols(pru_meta)
    kept, cluster_removed, dedup_removed = _apply_caps_dedup_report(classified)

    enriched = [r for r in shadow if "ctx_data_source" in r]
    legacy   = [r for r in shadow if "ctx_data_source" not in r]

    # ── Helper: look up bucket for a ticker ───────────────────────────────
    def _bucket(ticker: str) -> str:
        return classified.get(ticker, {}).get("universe_bucket", "unknown")

    # ================================================================== #
    # SECTION 1 — PRU COMPOSITION
    # ================================================================== #
    section("SECTION 1 — PRU Composition (bucket breakdown)")

    cr_syms = [s for s in classified.values() if s["universe_bucket"] == "core_research"]
    tm_syms = [s for s in classified.values() if s["universe_bucket"] == "tactical_momentum"]

    pru_is_old = not any("universe_bucket" in s for s in pru_meta.values())
    if pru_is_old:
        print(f"  ⚠  PRU was built before the quality upgrade. Buckets re-derived from discovery_signals.")
        print(f"     Rebuild with refresh_position_research_universe() to persist these fields.")
    else:
        print(f"  ✓  PRU built with v3.0.82+ scoring (universe_bucket field present)")
    print()
    print(f"  Total symbols:               {len(classified)}")
    print(f"  Core Research:               {len(cr_syms)}")
    print(f"  Tactical Momentum:           {len(tm_syms)}")
    print()
    print(f"  After cluster caps + dedup:")
    cr_kept = [s for s in kept if s["universe_bucket"] == "core_research"]
    tm_kept = [s for s in kept if s["universe_bucket"] == "tactical_momentum"]
    print(f"    Core Research kept:        {len(cr_kept)}")
    print(f"    Tactical Momentum kept:    {len(tm_kept)}")
    print(f"    Cluster-capped removals:   {len(cluster_removed)}")
    if cluster_removed:
        by_cluster = Counter(s.get("cluster_label", "?") for s in cluster_removed)
        for label, cnt in sorted(by_cluster.items()):
            removed_tickers = [s["ticker"] for s in cluster_removed if s.get("cluster_label") == label]
            print(f"      {label}: {cnt} removed  {removed_tickers}")
    print(f"    Dedup removals:            {len(dedup_removed)}")
    if dedup_removed:
        for s in dedup_removed:
            preferred = _PREFERRED_SHARE_CLASS.get(s["ticker"], "?")
            print(f"      {s['ticker']} removed (prefer {preferred})")

    # Primary archetype distribution
    arch_dist = Counter(s["primary_archetype"] for s in classified.values())
    print()
    print(f"  Primary archetype distribution:")
    for arch, cnt in arch_dist.most_common():
        bucket_marker = ""
        cr_cnt = sum(1 for s in classified.values() if s["primary_archetype"] == arch and s["universe_bucket"] == "core_research")
        tm_cnt = sum(1 for s in classified.values() if s["primary_archetype"] == arch and s["universe_bucket"] == "tactical_momentum")
        print(f"    {arch:<28}  {cnt:>3}  (CR={cr_cnt} TM={tm_cnt})")

    # Top 20 Core Research
    print()
    print(f"  Top 20 Core Research (by adjusted_discovery_score):")
    print(f"  {'Ticker':<8}  {'Adj':>4}  {'Base':>4}  {'Pen':>4}  {'Archetype':<28}  Signals")
    print(f"  {'-'*8}  {'-'*4}  {'-'*4}  {'-'*4}  {'-'*28}  -------")
    for s in sorted(cr_syms, key=lambda x: x.get("adjusted_discovery_score", 0), reverse=True)[:20]:
        sigs = ", ".join(s.get("discovery_signals", [])[:4])
        if len(s.get("discovery_signals", [])) > 4:
            sigs += f" +{len(s['discovery_signals'])-4}"
        print(f"  {s['ticker']:<8}  {s.get('adjusted_discovery_score', '?'):>4}  "
              f"{s.get('discovery_score', '?'):>4}  {s.get('risk_penalty_pts', 0):>4}  "
              f"{s.get('primary_archetype', '?'):<28}  {sigs}")

    # Top 20 Tactical Momentum
    print()
    print(f"  Top 20 Tactical Momentum (by adjusted_discovery_score):")
    print(f"  {'Ticker':<8}  {'Adj':>4}  {'Base':>4}  {'Pen':>4}  {'Archetype':<28}  Why tactical")
    print(f"  {'-'*8}  {'-'*4}  {'-'*4}  {'-'*4}  {'-'*28}  ------------")
    for s in sorted(tm_syms, key=lambda x: x.get("adjusted_discovery_score", 0), reverse=True)[:20]:
        sigs = set(s.get("discovery_signals", []))
        why = []
        if "revenue_yoy_gt_10pct" not in sigs: why.append("no strong rev")
        if "analyst_upside_gt_15pct" not in sigs: why.append("no high upside")
        if "recent_analyst_upgrade" not in sigs: why.append("no upgrade")
        why_str = ", ".join(why[:2]) or "—"
        print(f"  {s['ticker']:<8}  {s.get('adjusted_discovery_score', '?'):>4}  "
              f"{s.get('discovery_score', '?'):>4}  {s.get('risk_penalty_pts', 0):>4}  "
              f"{s.get('primary_archetype', '?'):<28}  {why_str}")

    # ================================================================== #
    # SECTION 2 — APEX CAP IMPACT (by bucket)
    # ================================================================== #
    section("SECTION 2 — Apex Cap Impact (by bucket)")

    if not funnel_apex_cap:
        print("  ⚠  No apex_cap funnel records found.")
        print(f"     Expected at: {FUNNEL_JSONL}  (stage=apex_cap)")
        print("     Written by bot_trading.py after the guardrails filter.")
        print("     Ensure the bot ran at least one full scan cycle after this code shipped.")
    else:
        # Aggregate bucket breakdown across all apex_cap records
        cr_before = 0
        cr_selected = 0
        cr_dropped = 0
        tm_before = 0
        tm_selected = 0
        tm_dropped = 0

        for r in funnel_apex_cap:
            for sym_info in (r.get("top_10_selected_by_score") or []):
                if sym_info.get("scanner_tier") == "D":
                    b = _bucket(sym_info.get("symbol", ""))
                    if b == "core_research":
                        cr_selected += 1
                    elif b == "tactical_momentum":
                        tm_selected += 1

            for sym_info in (r.get("top_10_dropped_tier_d") or []):
                b = _bucket(sym_info.get("symbol", ""))
                if b == "core_research":
                    cr_dropped += 1
                elif b == "tactical_momentum":
                    tm_dropped += 1

            for ticker in (r.get("selected_tier_d_symbols") or []):
                b = _bucket(ticker)
                if b == "core_research":
                    pass  # already counted above from top_10
                elif b == "tactical_momentum":
                    pass

        # Use the symbol lists for accurate counts (top_10 may be truncated)
        cr_selected_syms: set[str] = set()
        cr_dropped_syms:  set[str] = set()
        tm_selected_syms: set[str] = set()
        tm_dropped_syms:  set[str] = set()

        for r in funnel_apex_cap:
            for ticker in (r.get("selected_tier_d_symbols") or []):
                b = _bucket(ticker)
                if b == "core_research":
                    cr_selected_syms.add(ticker)
                elif b == "tactical_momentum":
                    tm_selected_syms.add(ticker)

            for sym_info in (r.get("top_10_dropped_tier_d") or []):
                ticker = sym_info.get("symbol", "")
                b = _bucket(ticker)
                if b == "core_research":
                    cr_dropped_syms.add(ticker)
                elif b == "tactical_momentum":
                    tm_dropped_syms.add(ticker)

            for ticker in (r.get("dropped_tier_d_symbols_top_20") or []):
                b = _bucket(ticker)
                if b == "core_research":
                    cr_dropped_syms.add(ticker)
                elif b == "tactical_momentum":
                    tm_dropped_syms.add(ticker)

        cr_before_syms = cr_selected_syms | cr_dropped_syms
        tm_before_syms = tm_selected_syms | tm_dropped_syms

        cycles = len(funnel_apex_cap)
        print(f"  Apex cap cycles with data:  {cycles}")
        print()
        print(f"  {'':30}  {'Core Research':>15}  {'Tactical Momentum':>18}")
        print(f"  {'':30}  {'─'*15}  {'─'*18}")
        print(f"  {'Before main cap (distinct syms)':30}  {len(cr_before_syms):>15}  {len(tm_before_syms):>18}")
        print(f"  {'Selected by main cap':30}  {len(cr_selected_syms):>15}  {len(tm_selected_syms):>18}")
        print(f"  {'Dropped by main cap':30}  {len(cr_dropped_syms):>15}  {len(tm_dropped_syms):>18}")
        print()

        if cr_dropped_syms:
            print(f"  ⚠  Core Research names dropped by cap: {sorted(cr_dropped_syms)}")
            cr_drop_scores = []
            for r in funnel_apex_cap:
                for sym_info in (r.get("top_10_dropped_tier_d") or []):
                    if sym_info.get("symbol") in cr_dropped_syms:
                        cr_drop_scores.append((sym_info["symbol"], sym_info.get("discovery_score", "?")))
            if cr_drop_scores:
                print(f"     (discovery_score where available: {cr_drop_scores[:5]})")
        else:
            print(f"  ✓  No Core Research names dropped by the main cap.")

        if tm_dropped_syms:
            print(f"  ℹ  Tactical Momentum names dropped by cap: {sorted(tm_dropped_syms)}")
        else:
            print(f"  ✓  No Tactical Momentum names dropped by the main cap.")

        # Most recent cycle raw detail
        _recent = funnel_apex_cap[-1]
        print(f"\n  Most recent cycle ({_ts_display(_recent.get('ts', ''))}):")
        print(f"    cap={_recent.get('cap_limit')}  "
              f"raw_tier_d={_recent.get('raw_tier_d_before_cap', 0)}  "
              f"selected_tier_d={_recent.get('selected_tier_d_after_cap', 0)}  "
              f"dropped_tier_d={_recent.get('dropped_tier_d_by_cap', 0)}")
        if _recent.get("selected_tier_d_symbols"):
            by_bucket_sel = Counter(_bucket(t) for t in _recent["selected_tier_d_symbols"])
            print(f"    Selected Tier D by bucket:  CR={by_bucket_sel.get('core_research', 0)}  "
                  f"TM={by_bucket_sel.get('tactical_momentum', 0)}  "
                  f"unknown={by_bucket_sel.get('unknown', 0)}")
        if _recent.get("dropped_tier_d_symbols_top_20"):
            by_bucket_drop = Counter(_bucket(t) for t in _recent["dropped_tier_d_symbols_top_20"])
            print(f"    Dropped Tier D by bucket:   CR={by_bucket_drop.get('core_research', 0)}  "
                  f"TM={by_bucket_drop.get('tactical_momentum', 0)}  "
                  f"unknown={by_bucket_drop.get('unknown', 0)}")

    # ================================================================== #
    # SECTION 3 — SHADOW APEX (by bucket)
    # ================================================================== #
    section("SECTION 3 — Shadow Apex (by bucket)")

    # ── 3a: Main pipeline shadow log (the primary evidence source) ─────

    enriched_with_bucket = []
    for r in enriched:
        sym = r.get("symbol", "")
        b   = _bucket(sym)
        enriched_with_bucket.append({**r, "_bucket": b})

    cr_shadow = [r for r in enriched_with_bucket if r["_bucket"] == "core_research"]
    tm_shadow = [r for r in enriched_with_bucket if r["_bucket"] == "tactical_momentum"]
    unk_shadow = [r for r in enriched_with_bucket if r["_bucket"] == "unknown"]

    print(f"  Main pipeline shadow log (position_research_shadow.jsonl):")
    print(f"  Total enriched records:      {len(enriched)}")
    print()
    print(f"  {'':35}  {'Core Research':>14}  {'Tactical Momentum':>18}  {'Unknown':>8}")
    print(f"  {'':35}  {'─'*14}  {'─'*18}  {'─'*8}")
    print(f"  {'Sent to shadow validation':35}  {len(cr_shadow):>14}  {len(tm_shadow):>18}  {len(unk_shadow):>8}")

    # Apex classification breakdown (simulated_type) by bucket
    for bucket_label, bucket_records, header in [
        ("Core Research",    cr_shadow, ""),
        ("Tactical Momentum",tm_shadow, ""),
    ]:
        if not bucket_records:
            continue
        sim_types = Counter(r.get("simulated_type", "?") for r in bucket_records)
        print(f"\n  Apex classification — {bucket_label} ({len(bucket_records)} records):")
        for stype, cnt in sim_types.most_common():
            print(f"    {stype:<30} {cnt:>4}")

    # would_have_passed by bucket
    print()
    print(f"  would_have_passed validation:")
    print(f"  {'':35}  {'Core Research':>14}  {'Tactical Momentum':>18}")
    print(f"  {'':35}  {'─'*14}  {'─'*18}")
    cr_pass  = sum(1 for r in cr_shadow if r.get("would_have_passed") is True)
    cr_fail  = sum(1 for r in cr_shadow if r.get("would_have_passed") is False)
    tm_pass  = sum(1 for r in tm_shadow if r.get("would_have_passed") is True)
    tm_fail  = sum(1 for r in tm_shadow if r.get("would_have_passed") is False)
    print(f"  {'would_have_passed=True':35}  {cr_pass:>14}  {tm_pass:>18}")
    print(f"  {'would_have_passed=False':35}  {cr_fail:>14}  {tm_fail:>18}")

    if cr_pass + cr_fail > 0:
        cr_pct = 100 * cr_pass / (cr_pass + cr_fail)
        print(f"\n  Core Research pass rate:        {cr_pct:.0f}%  ({cr_pass}/{cr_pass+cr_fail})")
    if tm_pass + tm_fail > 0:
        tm_pct = 100 * tm_pass / (tm_pass + tm_fail)
        print(f"  Tactical Momentum pass rate:    {tm_pct:.0f}%  ({tm_pass}/{tm_pass+tm_fail})")

    # Reject reasons by bucket
    for bucket_label, fail_records in [
        ("Core Research",     [r for r in cr_shadow if r.get("would_have_passed") is False]),
        ("Tactical Momentum", [r for r in tm_shadow if r.get("would_have_passed") is False]),
    ]:
        if not fail_records:
            continue
        reasons = Counter(
            (r.get("simulated_reason") or "?")[:80]
            for r in fail_records
        )
        print(f"\n  Reject reasons — {bucket_label} ({len(fail_records)} failing records):")
        for reason, cnt in reasons.most_common(6):
            print(f"    [{cnt}]  {reason}")

    # Verdict: are Core Research names being evaluated?
    print()
    if len(cr_shadow) == 0 and len(tm_shadow) == 0:
        print("  ⚠  No shadow records yet — insufficient data.")
    elif len(cr_shadow) == 0:
        print("  ⚠  Zero Core Research names in shadow log. Only Tactical Momentum evaluated.")
        print("     Check pipeline and apex_cap sections — Core Research may be blocked before shadow.")
    elif len(cr_shadow) >= len(tm_shadow):
        print(f"  ✓  Core Research ({len(cr_shadow)}) is the dominant bucket in shadow validation.")
    else:
        print(f"  ℹ  Tactical Momentum ({len(tm_shadow)}) outnumbers Core Research ({len(cr_shadow)}) in shadow log.")
        print(f"     This may be expected if TM names score higher on raw signal dimensions.")

    # ── 3b: Phase 1B shadow Apex lane (if any records exist) ──────────
    if funnel_shadow_apex:
        print(f"\n  Phase 1B Shadow Apex lane ({len(funnel_shadow_apex)} cycles):")
        sa_syms_all: list[str] = []
        for r in funnel_shadow_apex:
            sa_syms_all.extend(r.get("tier_d_shadow_symbols") or [])

        sa_cr = [s for s in sa_syms_all if _bucket(s) == "core_research"]
        sa_tm = [s for s in sa_syms_all if _bucket(s) == "tactical_momentum"]
        print(f"    Core Research sent to shadow Apex:       {len(set(sa_cr))} distinct")
        print(f"    Tactical Momentum sent to shadow Apex:   {len(set(sa_tm))} distinct")

        sa_class_totals: dict[str, int] = {}
        for r in funnel_shadow_apex:
            for k, v in (r.get("tier_d_shadow_apex_classifications") or {}).items():
                sa_class_totals[k] = sa_class_totals.get(k, 0) + v
        if sa_class_totals:
            print(f"    Aggregate classifications:")
            for cls, cnt in sorted(sa_class_totals.items(), key=lambda x: -x[1]):
                print(f"      {cls:<30} {cnt}")
    else:
        print(f"\n  Phase 1B Shadow Apex lane: no records yet (stage=tier_d_shadow_apex)")
        print(f"  Written when Tier D is dropped by the main top-30 cap.")

    # ================================================================== #
    # SECTION 4 — SAFETY
    # ================================================================== #
    section("SECTION 4 — Safety")

    shadow_on, allow_live = _read_config_flags()
    live_off = (allow_live is False)

    print(f"  shadow_mode=True:                    {'✓ confirmed' if shadow_on is True else '⚠  RUNTIME VALUE IS ' + str(shadow_on)}")
    print(f"  allow_live_position_entries=False:   {'✓ confirmed' if live_off else '⚠  RUNTIME VALUE IS ' + str(allow_live)}")

    tier_d_orders = [
        e for e in trade_events
        if e.get("scanner_tier") == "D"
        and e.get("event_type") in ("ORDER_INTENT", "ORDER_FILLED")
    ]
    tier_d_training = [
        r for r in training
        if r.get("scanner_tier") == "D" or r.get("trade_type") == "POSITION_RESEARCH_ONLY"
    ]

    orders_ok   = len(tier_d_orders) == 0
    training_ok = len(tier_d_training) == 0

    print(f"  orders placed (Tier D):              {'✓ 0' if orders_ok else '⚠  ' + str(len(tier_d_orders)) + ' — INVESTIGATE'}")
    print(f"  training_records pollution:          {'✓ 0' if training_ok else '⚠  ' + str(len(tier_d_training)) + ' — INVESTIGATE'}")
    print(f"  Tier D live entries:                 {'✓ DISABLED' if live_off else '⚠  ENABLED — STOP'}")

    print()
    all_safe = shadow_on is True and live_off and orders_ok and training_ok
    if all_safe:
        print("  ✓ All safety checks pass. Observation only — no live impact.")
    else:
        print("  ✗ Safety check FAILED. Investigate before continuing.")

    print()
    print("  ─── Do not proceed to Phase 2 ───")
    print("  ─── Do not enable live Tier D entries ───")
    print("  ─── Do not change the main Apex cap yet ───")
    print()
    print("  Observation question: are real Core Research names now being")
    print("  evaluated, and are Tactical Momentum names correctly separated?")
    print()
    cr_evaluated = len(set(r["symbol"] for r in cr_shadow)) if cr_shadow else 0
    tm_evaluated = len(set(r["symbol"] for r in tm_shadow)) if tm_shadow else 0
    print(f"  Core Research distinct symbols evaluated:      {cr_evaluated}")
    print(f"  Tactical Momentum distinct symbols evaluated:  {tm_evaluated}")
    if cr_evaluated > 0 and tm_evaluated > 0:
        print(f"  Answer: ✓ Both buckets are being evaluated. Separation is visible in shadow log.")
    elif cr_evaluated > 0:
        print(f"  Answer: ✓ Core Research being evaluated. No TM names reached shadow yet.")
    elif tm_evaluated > 0:
        print(f"  Answer: ⚠  Only Tactical Momentum evaluated. No CR names reached shadow.")
    else:
        print(f"  Answer: ⚠  No Tier D names in shadow log yet. Need more scan cycles.")

    # ================================================================== #
    # ─── DETAIL SECTIONS ─────────────────────────────────────────────── #
    # ================================================================== #

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 0 — Tier D Funnel Attrition
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 0 — Tier D Funnel Attrition (aggregate across all scan cycles)")

    if not funnel_pipeline:
        print("  ⚠  No pipeline funnel records found.")
        print(f"     Expected at: {FUNNEL_JSONL}")
        print("     This file is written by signal_pipeline.run_signal_pipeline().")
        print("     Ensure the bot ran at least one full scan cycle after this code shipped.")
    else:
        def _agg(key: str) -> int:
            return sum(r.get(key, 0) for r in funnel_pipeline)

        p_loaded      = _agg("pru_loaded")
        p_universe    = _agg("in_universe")
        p_all_scored  = _agg("scored_all")
        p_above_thresh= _agg("above_regime_threshold")
        p_strategy    = _agg("passed_strategy_threshold")
        p_persistence = _agg("passed_persistence")
        p_rescue_pool = _agg("rescue_pool")
        p_rescued     = _agg("rescued")
        p_dropped     = _agg("dropped_final")
        p_output      = _agg("pipeline_output")
        p_drop_scored = _agg("drop_at_all_scored")
        p_drop_strat  = _agg("drop_at_strategy_threshold")

        d_entered     = sum(r.get("entered_dispatch", 0) for r in funnel_dispatch)
        d_ctx_fail    = sum(r.get("dropped_context_fail", 0) for r in funnel_dispatch)
        d_shadow      = sum(r.get("shadow_blocked", 0) for r in funnel_dispatch)
        d_non_pos     = sum(r.get("executed_non_position", 0) for r in funnel_dispatch)

        apex_totals: dict[str, int] = {}
        for r in funnel_dispatch:
            for k, v in (r.get("apex_classification") or {}).items():
                apex_totals[k] = apex_totals.get(k, 0) + v

        cycles = len(funnel_pipeline)
        print(f"  Scan cycles with funnel data:  {cycles}")
        print()
        print(f"  Stage  1  — PRU loaded:                     {p_loaded:>6}  (across {cycles} cycles; {p_loaded//cycles if cycles else 0}/cycle avg)")
        print(f"  Stage  2  — Entered dynamic universe:        {p_universe:>6}")
        print(f"  Stage  3  — Scored (all_scored, any dim):    {p_all_scored:>6}  ← drop here = {p_drop_scored}")
        print(f"  Stage  3b — Above regime threshold:          {p_above_thresh:>6}  ← drop here = {p_all_scored - p_above_thresh}")
        print(f"  Stage  4  — Passed strategy threshold:       {p_strategy:>6}  ← drop here = {p_above_thresh - p_strategy}")
        print(f"  Stage  5  — Passed persistence gate:         {p_persistence:>6}")
        print(f"  Stage  6  — Rescue pool (below thresh):      {p_rescue_pool:>6}")
        print(f"  Stage  6b — Rescued:                         {p_rescued:>6}")
        print(f"  Stage  6c — Dropped at rescue (final drop):  {p_dropped:>6}")
        print(f"  Stage  6d — Pipeline output (to dispatch):   {p_output:>6}")
        if funnel_dispatch:
            print()
            print(f"  Stage  7  — Entered dispatch:               {d_entered:>6}")
            print(f"  Stage  7b — Dropped (context-build fail):   {d_ctx_fail:>6}")
            if apex_totals:
                print(f"  Stage  8  — Apex classification breakdown:")
                for atype in ("POSITION", "SWING", "INTRADAY", "AVOID", "no_classification"):
                    cnt = apex_totals.get(atype, 0)
                    if cnt:
                        arrow = " ← reaches shadow gate" if atype == "POSITION" else ""
                        print(f"              {atype:<22} {cnt:>5}{arrow}")
            reached_gate = sum(r.get("reached_validate_entry", 0) for r in funnel_dispatch)
            print(f"  Stage 10  — Reached validate_entry:         {reached_gate:>6}")
            print(f"  Stage 11  — Shadow-blocked:                 {d_shadow:>6}")
            print(f"  Stage 12  — Executed as SWING/INTRADAY:     {d_non_pos:>6}")
        else:
            print()
            print("  ⚠  No dispatch funnel records found.")

        print()
        print("  Attrition diagnosis:")
        if p_drop_scored > 0:
            print(f"    ⚠  {p_drop_scored} Tier D scored = 0 (not in filtered universe) → check universe builder")
        if p_all_scored - p_above_thresh > 0:
            diff = p_all_scored - p_above_thresh
            print(f"    ⚠  {diff} Tier D below regime threshold → rescue gate is the only path forward")
        if p_dropped > 0:
            print(f"    ⚠  {p_dropped} Tier D failed rescue → discovery_score < 6 AND no archetypes AND signal < 6")
        if apex_totals.get("AVOID", 0) > 0:
            print(f"    ⚠  Apex classified {apex_totals['AVOID']} Tier D as AVOID")
        if apex_totals.get("SWING", 0) + apex_totals.get("INTRADAY", 0) > 0:
            non_pos = apex_totals.get("SWING", 0) + apex_totals.get("INTRADAY", 0)
            print(f"    ℹ  {non_pos} Tier D classified as SWING/INTRADAY by Apex")
        if (p_drop_scored == 0 and (p_all_scored - p_above_thresh) == 0 and p_dropped == 0
                and apex_totals.get("AVOID", 0) == 0):
            print(f"    ✓ No attrition anomalies detected")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 0b — Apex Cap Analysis (original detail)
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 0b — Apex Cap Analysis (full detail)")

    if not funnel_apex_cap:
        print("  ⚠  No apex_cap funnel records found.")
    else:
        ac_cycles    = len(funnel_apex_cap)
        ac_pre_td    = sum(r.get("raw_tier_d_before_cap",        0) for r in funnel_apex_cap)
        ac_sel_td    = sum(r.get("selected_tier_d_after_cap",    0) for r in funnel_apex_cap)
        ac_drop_td   = sum(r.get("dropped_tier_d_by_cap",        0) for r in funnel_apex_cap)
        ac_pre_all   = sum(r.get("raw_candidates_before_cap",    0) for r in funnel_apex_cap)
        ac_sel_all   = sum(r.get("selected_candidates_after_cap",0) for r in funnel_apex_cap)
        ac_drop_all  = sum(r.get("dropped_by_cap_total",         0) for r in funnel_apex_cap)
        ac_arch_drop = sum(1 for r in funnel_apex_cap if r.get("tier_d_with_archetypes_dropped"))
        ac_disc_drop = sum(1 for r in funnel_apex_cap if r.get("tier_d_strong_discovery_dropped"))
        ac_fully_excluded = sum(
            1 for r in funnel_apex_cap
            if r.get("raw_tier_d_before_cap", 0) > 0
            and r.get("selected_tier_d_after_cap", 0) == 0
        )
        ac_td_present_cycles = sum(1 for r in funnel_apex_cap if r.get("raw_tier_d_before_cap", 0) > 0)

        print(f"  Apex cap records (scan cycles):                {ac_cycles}")
        print(f"  Tier D present pre-cap (cycles):               {ac_td_present_cycles}/{ac_cycles}")
        print()
        print(f"  Aggregate totals across all cycles:")
        print(f"    All candidates before cap:                   {ac_pre_all}")
        print(f"    All candidates after cap:                    {ac_sel_all}")
        print(f"    All dropped by cap:                          {ac_drop_all}")
        print()
        print(f"    Tier D before cap:                           {ac_pre_td}")
        print(f"    Tier D selected after cap:                   {ac_sel_td}")
        print(f"    Tier D dropped by cap:                       {ac_drop_td}")
        print()
        print(f"  Quality of dropped Tier D:")
        print(f"    Cycles where Tier D with archetypes was dropped:         {ac_arch_drop}/{ac_td_present_cycles}")
        print(f"    Cycles where Tier D with discovery_score>=6 was dropped: {ac_disc_drop}/{ac_td_present_cycles}")
        print(f"    Cycles where Tier D was present but fully excluded:      {ac_fully_excluded}/{ac_td_present_cycles}")

        print()
        if ac_td_present_cycles == 0:
            print("  Verdict: Tier D reached pre-cap in 0 cycles. Check pipeline stages 1-6.")
        elif ac_drop_td > 0 and ac_drop_td >= ac_sel_td:
            print("  Verdict: ⚠  CAP IS THE PRIMARY BOTTLENECK.")
            print(f"    More Tier D dropped ({ac_drop_td}) than selected ({ac_sel_td}).")
        elif ac_drop_td > 0:
            print(f"  Verdict: Cap is a partial bottleneck ({ac_drop_td} dropped, {ac_sel_td} selected).")
        else:
            print(f"  Verdict: ✓ Cap is NOT dropping Tier D candidates.")

        _recent_with_drops = [r for r in reversed(funnel_apex_cap) if r.get("dropped_tier_d_by_cap", 0) > 0]
        if _recent_with_drops:
            r = _recent_with_drops[0]
            print(f"\n  Most recent cycle with Tier D cap drops ({_ts_display(r.get('ts',''))}):")
            print(f"    raw={r['raw_candidates_before_cap']} cap={r['cap_limit']} "
                  f"selected={r['selected_candidates_after_cap']}")
            print(f"    Tier D before={r['raw_tier_d_before_cap']} after={r['selected_tier_d_after_cap']} "
                  f"dropped={r['dropped_tier_d_by_cap']}")
            print(f"    max_tier_d_score_before_cap:  {r.get('max_tier_d_score_before_cap')}")
            print(f"    min_selected_score_after_cap: {r.get('min_selected_score_after_cap')}")
            print(f"    highest_dropped_tier_d_score: {r.get('highest_dropped_tier_d_score')}")
            if r.get("top_10_selected_by_score"):
                print(f"    top-5 selected (symbol/score/tier):")
                for item in r["top_10_selected_by_score"][:5]:
                    tier_tag = " [TIER D]" if item.get("scanner_tier") == "D" else ""
                    print(f"      {item.get('symbol'):<8} score={item.get('score')}{tier_tag}")
            if r.get("top_10_dropped_tier_d"):
                print(f"    dropped Tier D (up to 5):")
                for item in r["top_10_dropped_tier_d"][:5]:
                    print(f"      {item.get('symbol'):<8} score={item.get('score')} "
                          f"discovery={item.get('discovery_score')} "
                          f"archetypes={item.get('matched_archetypes', [])}")

        _recent_cap = funnel_apex_cap[-1] if funnel_apex_cap else None
        if _recent_cap and _recent_cap.get("selected_tier_d_symbols"):
            print(f"\n  Most recent cycle — Tier D that survived cap: {_recent_cap['selected_tier_d_symbols']}")
        if _recent_cap and _recent_cap.get("dropped_tier_d_symbols_top_20"):
            print(f"  Most recent cycle — Tier D dropped by cap (up to 20): {_recent_cap['dropped_tier_d_symbols_top_20']}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 0c — Apex Cap Shadow Comparator
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 0c — Apex Cap Shadow Comparator (tier-aware vs hard top-30)")

    if not funnel_shadow_compare:
        print("  ⚠  No apex_cap_shadow_compare records yet.")
    else:
        sc_cycles    = len(funnel_shadow_compare)
        sc_cur_td    = sum(r.get("current_selected_tier_d", 0)  for r in funnel_shadow_compare)
        sc_shad_td   = sum(r.get("shadow_selected_tier_d", 0)   for r in funnel_shadow_compare)
        sc_td_improvement = sum(
            max(0, r.get("shadow_selected_tier_d", 0) - r.get("current_selected_tier_d", 0))
            for r in funnel_shadow_compare
        )
        sc_non_td_displaced = sum(
            len(r.get("shadow_non_tier_d_displaced_vs_current", []))
            for r in funnel_shadow_compare
        )
        sc_same_total = all(r.get("shadow_token_budget_same_total") for r in funnel_shadow_compare)
        sc_arch_recovered = sum(
            1 for r in funnel_shadow_compare
            if any(entry.get("archetypes") for entry in r.get("shadow_top_tier_d_added", []))
        )
        verdict_counts = Counter(r.get("bottleneck_verdict", "?") for r in funnel_shadow_compare)

        print(f"  Shadow compare records (scan cycles): {sc_cycles}")
        print()
        print(f"  Aggregate across all cycles:")
        print(f"    Current cap — Tier D selected:          {sc_cur_td}")
        print(f"    Shadow cap  — Tier D selected:          {sc_shad_td}")
        print(f"    Tier D improvement (shadow − current):  {sc_td_improvement}")
        print(f"    Non-Tier-D displaced by shadow:         {sc_non_td_displaced}")
        print(f"    Total cap ≤ 30 in all cycles:           {'✓' if sc_same_total else '✗  BUG'}")
        print()
        print(f"  Bottleneck verdict distribution:")
        for verdict, count in verdict_counts.most_common():
            print(f"    {verdict}: {count}/{sc_cycles}")

        _sc_recent = funnel_shadow_compare[-1]
        print(f"\n  Most recent cycle ({_ts_display(_sc_recent.get('ts', ''))}):")
        print(f"    Current:  {_sc_recent.get('current_selected_tier_d', 0)} Tier D selected, "
              f"{_sc_recent.get('current_dropped_tier_d', 0)} dropped")
        print(f"    Shadow:   {_sc_recent.get('shadow_selected_tier_d', 0)} Tier D selected, "
              f"{_sc_recent.get('shadow_dropped_tier_d', 0)} dropped")

        primary  = verdict_counts.get("current_cap_kills_tier_d", 0)
        partial  = verdict_counts.get("current_cap_partially_suppresses_tier_d", 0)
        print()
        if primary > 0:
            print(f"  ⚠  Cap kills Tier D in {primary}/{sc_cycles} cycles.")
        elif partial > 0:
            print(f"  ⚡ Cap partially suppresses Tier D in {partial}/{sc_cycles} cycles.")
        else:
            print("  ✓ Current cap is not the primary bottleneck for Tier D.")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 0d — Tier D Shadow Apex Lane
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 0d — Tier D Shadow Apex Lane (Phase 1B)")

    if not funnel_shadow_apex:
        print("  ⚠  No tier_d_shadow_apex records yet.")
        print(f"     Expected at: {FUNNEL_JSONL}  (stage=tier_d_shadow_apex)")
        print("     Written by bot_trading.py after each scan cycle where Tier D")
        print("     candidates were dropped by the main top-30 cap.")
    else:
        sa_cycles          = len(funnel_shadow_apex)
        sa_dropped_total   = sum(r.get("tier_d_dropped_main_cap",    0) for r in funnel_shadow_apex)
        sa_eligible        = sum(r.get("tier_d_shadow_eligible",     0) for r in funnel_shadow_apex)
        sa_selected        = sum(r.get("tier_d_shadow_selected",     0) for r in funnel_shadow_apex)
        sa_not_selected    = sum(r.get("tier_d_shadow_not_selected", 0) for r in funnel_shadow_apex)
        sa_orders          = sum(r.get("tier_d_shadow_orders_placed",0) for r in funnel_shadow_apex)
        sa_training        = sum(r.get("tier_d_shadow_training_records_written", 0) for r in funnel_shadow_apex)

        sa_class_totals: dict[str, int] = {}
        for r in funnel_shadow_apex:
            for k, v in (r.get("tier_d_shadow_apex_classifications") or {}).items():
                sa_class_totals[k] = sa_class_totals.get(k, 0) + v

        sa_all_symbols: list[str] = []
        for r in funnel_shadow_apex:
            sa_all_symbols.extend(r.get("tier_d_shadow_symbols") or [])
        sa_distinct_syms = len(set(sa_all_symbols))

        print(f"  Shadow Apex records (scan cycles):             {sa_cycles}")
        print(f"  Distinct Tier D symbols evaluated:             {sa_distinct_syms}")
        print()
        print(f"  Aggregate totals across all cycles:")
        print(f"    Tier D dropped by main cap:                  {sa_dropped_total}")
        print(f"    Tier D eligible for shadow Apex:             {sa_eligible}")
        print(f"    Tier D selected for shadow Apex:             {sa_selected}")
        print(f"    Tier D eligible but not selected (cap):      {sa_not_selected}")
        print()
        print(f"  Shadow Apex classification breakdown:")
        for cls, cnt in sorted(sa_class_totals.items(), key=lambda x: -x[1]):
            print(f"    {cls:<30} {cnt}")
        print()

        print(f"  orders placed:                               {'✓ 0' if sa_orders == 0 else '⚠  ' + str(sa_orders)}")
        print(f"  training_records pollution:                  {'✓ 0' if sa_training == 0 else '⚠  ' + str(sa_training)}")

        _sa_recent = funnel_shadow_apex[-1]
        print(f"\n  Most recent cycle ({_ts_display(_sa_recent.get('ts', ''))}):")
        print(f"    Tier D dropped main cap:   {_sa_recent.get('tier_d_dropped_main_cap', 0)}")
        print(f"    Shadow eligible:           {_sa_recent.get('tier_d_shadow_eligible', 0)}")
        print(f"    Shadow selected:           {_sa_recent.get('tier_d_shadow_selected', 0)}")
        print(f"    Symbols:                   {_sa_recent.get('tier_d_shadow_symbols', [])}")

        p1b_ok = sa_orders == 0 and sa_training == 0 and sa_distinct_syms >= 10
        print()
        if p1b_ok:
            print(f"  ✓ Phase 1B success criteria met:")
            print(f"    orders_placed=0  training_pollution=0  distinct_symbols={sa_distinct_syms} (≥10)")
        else:
            issues = []
            if sa_orders > 0:     issues.append(f"orders_placed={sa_orders} ⚠")
            if sa_training > 0:   issues.append(f"training_pollution={sa_training} ⚠")
            if sa_distinct_syms < 10: issues.append(f"distinct_symbols={sa_distinct_syms} < 10")
            print(f"  ✗ Phase 1B not yet complete: {', '.join(issues)}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 1 — Scan-Cycle Coverage
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 1 — Scan-Cycle Coverage")

    print(f"  Total shadow records (all time):     {len(shadow)}")
    print(f"    Enriched (post-backfill schema):   {len(enriched)}")
    print(f"    Legacy (pre-enrichment schema):    {len(legacy)}")

    if enriched:
        first_ts = min(r["ts"] for r in enriched)
        last_ts  = max(r["ts"] for r in enriched)
        symbols  = Counter(r["symbol"] for r in enriched)
        print(f"  Unique symbols evaluated:            {len(symbols)}")
        print(f"  First enriched record:               {_ts_display(first_ts)}")
        print(f"  Last enriched record:                {_ts_display(last_ts)}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 2 — Context Hydration
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 2 — Context Hydration (enriched records only)")

    ctx_dist  = Counter(r.get("ctx_data_source", "?") for r in enriched)
    backfilled = sum(1 for r in enriched if r.get("context_backfilled") is True)
    backfill_failed = sum(1 for r in enriched if r.get("missing_fresh_trade_context_after_rescue") is True)
    backfill_attempted = sum(1 for r in enriched if r.get("tier_d_rescued_after_context_build") is True)

    print(f"  ctx_data_source=full_ctx:     {ctx_dist.get('full_ctx', 0)}")
    print(f"  ctx_data_source=partial_ctx:  {ctx_dist.get('partial_ctx', 0)}")
    print(f"  ctx_data_source=no_ctx:       {ctx_dist.get('no_ctx', 0)}")
    print(f"  backfill attempted:           {backfill_attempted}")
    print(f"  context_backfilled=True:      {backfilled}")
    print(f"  backfill failed (no_ctx persists): {backfill_failed}")

    if ctx_dist.get("no_ctx", 0) > 0:
        no_ctx_syms = [r["symbol"] for r in enriched if r.get("ctx_data_source") == "no_ctx"]
        sym_counts = Counter(no_ctx_syms)
        print(f"\n  ⚠  no_ctx symbols: {dict(sym_counts)}")
        in_pru     = [s for s in sym_counts if s in pru_meta]
        not_in_pru = [s for s in sym_counts if s not in pru_meta]
        if in_pru:
            print(f"     Still in PRU (need investigation): {in_pru}")
        if not_in_pru:
            print(f"     Not in current PRU (historical, expected): {not_in_pru}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 3 — Data-Flow Integrity
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 3 — Data-Flow Integrity")

    gap_true  = [r for r in enriched if r.get("data_flow_gap") is True]
    gap_false = [r for r in enriched if r.get("data_flow_gap") is False]
    gap_miss  = len(enriched) - len(gap_true) - len(gap_false)

    print(f"  data_flow_gap=True:   {len(gap_true)}")
    print(f"  data_flow_gap=False:  {len(gap_false)}")
    print(f"  data_flow_gap=?:      {gap_miss}")

    if gap_true:
        print(f"\n  ⚠  Data-flow gap examples (PRU had FMP values, ctx received None):")
        for r in gap_true[:5]:
            pru_snap = pru_meta.get(r["symbol"], {}).get("pru_fmp_snapshot", {})
            print(f"     {r['symbol']}  pru_snapshot={pru_snap}")
            print(f"       ctx_populated_fields={r.get('ctx_populated_fields')}")
            print(f"       pru_supplemented_fields={r.get('pru_supplemented_fields')}")
            print(f"       would_have_passed_with_pru_data={r.get('would_have_passed_with_pru_data')}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 4 — Simulation Outcomes
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 4 — Simulation Outcomes")

    would_pass    = sum(1 for r in enriched if r.get("would_have_passed") is True)
    would_fail    = sum(1 for r in enriched if r.get("would_have_passed") is False)
    pass_with_pru = sum(1 for r in enriched if r.get("would_have_passed_with_pru_data") is True)

    print(f"  would_have_passed=True:               {would_pass}")
    print(f"  would_have_passed=False:              {would_fail}")
    print(f"  would_have_passed_with_pru_data=True: {pass_with_pru}")
    print(f"  shadow_mode_blocked (all records):    {len(enriched)}")

    fail_reasons = Counter(
        r.get("simulated_reason", "?")[:80]
        for r in enriched if r.get("would_have_passed") is False
    )
    if fail_reasons:
        print(f"\n  Simulated fail reasons (all buckets):")
        for reason, cnt in fail_reasons.most_common(8):
            print(f"    [{cnt}]  {reason}")

    pass_reasons = Counter(
        r.get("simulated_reason", "?")[:80]
        for r in enriched if r.get("would_have_passed") is True
    )
    if pass_reasons:
        print(f"\n  Simulated pass reasons:")
        for reason, cnt in pass_reasons.most_common(5):
            print(f"    [{cnt}]  {reason}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 5 — Stale-Symbol Audit
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 5 — Stale Symbol Audit")

    stale: set[str] = set()
    try:
        from universe_committed import load_committed_universe
        committed = set(load_committed_universe())
        pru_syms  = set(pru_meta.keys())
        stale     = pru_syms - committed
        print(f"  PRU symbols:              {len(pru_syms)}")
        print(f"  In committed universe:    {len(pru_syms - stale)}")
        print(f"  Stale (not in committed): {len(stale)}")
        if stale:
            print(f"  ⚠  Stale symbols: {sorted(stale)[:10]}")
        else:
            print(f"  ✓ 0 stale symbols")
    except Exception as exc:
        print(f"  Could not load committed universe: {exc}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 6 — Quality Examples
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 6 — Quality Examples (up to 10 distinct Tier D candidates)")

    seen_syms: set[str] = set()
    examples = []
    for r in sorted(enriched, key=lambda x: x.get("ts", ""), reverse=True):
        sym = r["symbol"]
        if sym in seen_syms:
            continue
        seen_syms.add(sym)
        examples.append(r)
        if len(examples) >= 10:
            break

    if len(examples) < 10:
        print(f"  ⚠  Only {len(examples)} distinct Tier D symbols in shadow log. Run more cycles.")

    for r in examples:
        sym       = r["symbol"]
        pru_entry = classified.get(sym, pru_meta.get(sym, {}))
        snap      = pru_entry.get("pru_fmp_snapshot", {})
        bucket_tag = f"[{pru_entry.get('universe_bucket', '?')}]" if pru_entry else "[?]"
        print(f"\n  {sym}  {bucket_tag}")
        print(f"    discovery_score:    {pru_entry.get('discovery_score', '?')}")
        print(f"    adjusted_score:     {pru_entry.get('adjusted_discovery_score', '?')}")
        print(f"    primary_archetype:  {pru_entry.get('primary_archetype', '?')}")
        print(f"    pru_rev_yoy:        {snap.get('revenue_growth_yoy', '?')}")
        print(f"    pru_analyst_upside: {snap.get('analyst_upside_pct', '?')}")
        print(f"    signal_score:       {r.get('signal_score')}")
        print(f"    ctx_data_source:    {r.get('ctx_data_source', '?')}")
        print(f"    would_have_passed:  {r.get('would_have_passed')}")
        print(f"    simulated_reason:   {(r.get('simulated_reason') or '?')[:100]}")

    # ------------------------------------------------------------------ #
    # DETAIL SECTION 7 — Phase 2 Readiness Gate
    # ------------------------------------------------------------------ #
    section("DETAIL SECTION 7 — Phase 2 Readiness Gate")

    distinct_syms      = len(set(r["symbol"] for r in enriched))
    pipeline_cycles    = len(funnel_pipeline)
    no_ctx_syms_in_pru = [
        s for s in Counter(r["symbol"] for r in enriched if r.get("ctx_data_source") == "no_ctx")
        if s in pru_meta
    ]

    gate_items = [
        ("≥3 scan cycles with funnel records",
         pipeline_cycles >= 3,
         f"{pipeline_cycles} pipeline cycles"),
        ("≥10 distinct Tier D symbols in shadow log",
         distinct_syms >= 10,
         f"{distinct_syms} distinct symbols"),
        ("Stale symbols = 0",
         len(stale) == 0,
         f"{len(stale)} stale symbols"),
        ("no_ctx symbols NOT in current PRU",
         len(no_ctx_syms_in_pru) == 0,
         f"{len(no_ctx_syms_in_pru)} still-in-PRU no_ctx symbols" if no_ctx_syms_in_pru else "✓"),
        ("data_flow_gap=True = 0",
         len(gap_true) == 0,
         f"{len(gap_true)} gaps"),
        ("No Tier D orders placed",
         len(tier_d_orders) == 0,
         f"{len(tier_d_orders)} orders"),
        ("No training_records pollution",
         len(tier_d_training) == 0,
         f"{len(tier_d_training)} records"),
        ("shadow_mode=True confirmed",
         shadow_on is True,
         f"runtime value={shadow_on}"),
        ("allow_live_position_entries=False confirmed",
         live_off is True,
         f"runtime value={allow_live}"),
        ("Funnel attrition report available",
         pipeline_cycles >= 1 and len(funnel_dispatch) >= 1,
         f"pipeline={pipeline_cycles} dispatch={len(funnel_dispatch)}"),
        ("Apex cap analysis available",
         len(funnel_apex_cap) >= 1,
         f"apex_cap records={len(funnel_apex_cap)}"),
        ("Cap is not the primary bottleneck",
         len(funnel_apex_cap) == 0
         or sum(r.get("raw_tier_d_before_cap", 0) for r in funnel_apex_cap) == 0
         or sum(r.get("dropped_tier_d_by_cap", 0) for r in funnel_apex_cap)
            < sum(r.get("selected_tier_d_after_cap", 0) for r in funnel_apex_cap),
         f"dropped={sum(r.get('dropped_tier_d_by_cap',0) for r in funnel_apex_cap)} "
         f"selected={sum(r.get('selected_tier_d_after_cap',0) for r in funnel_apex_cap)}"),
    ]

    all_pass = True
    for label, result, note in gate_items:
        if result is None:
            icon = "?"
        elif result:
            icon = "✓"
        else:
            icon = "✗"
            all_pass = False
        print(f"  [{icon}] {label}  ({note})")

    print()
    if all_pass:
        print("  ✓ All Phase 2 pre-conditions met.")
        print("  → Present this report to Amit before enabling any Phase 2 changes.")
    else:
        print("  ✗ Phase 2 pre-conditions NOT yet met. Continue shadow-mode observation.")
        print("  → Do not proceed to Phase 2 until all gates show ✓.")


if __name__ == "__main__":
    main()
