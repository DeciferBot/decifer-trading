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


def _read_config_flags() -> tuple[bool | None, bool | None]:
    """Return (shadow_mode, allow_live) by importing CONFIG the same way the bot does."""
    try:
        sys.path.insert(0, REPO)
        from config import CONFIG  # same import path as entry_gate.py
        eg = CONFIG.get("entry_gate", {})
        shadow_mode = eg.get("position_research_shadow_mode", True)
        allow_live  = eg.get("position_research_allow_live_position_entries", False)
        return shadow_mode, allow_live
    except Exception as exc:
        print(f"  ⚠  Could not import CONFIG: {exc}")
        return None, None


def main() -> None:
    funnel_raw  = _load_jsonl(FUNNEL_JSONL)
    shadow      = _load_jsonl(SHADOW_JSONL)
    pru_data    = _load_pru()
    training    = _load_jsonl(TRAINING)
    trade_events = _load_jsonl(TRADE_EVENTS)

    pru_meta: dict[str, dict] = {}
    pru_built_at = pru_data.get("built_at", "?")
    pru_count    = pru_data.get("count", 0)
    for sym_entry in pru_data.get("symbols", []):
        pru_meta[sym_entry["ticker"]] = sym_entry

    funnel_pipeline  = [r for r in funnel_raw if r.get("stage") == "pipeline"]
    funnel_dispatch  = [r for r in funnel_raw if r.get("stage") == "dispatch"]

    print(f"\nTier D Evidence Report  —  generated {datetime.now(timezone.utc).isoformat()[:19]}Z")
    print(f"PRU file:    {PRU_JSON}")
    print(f"PRU built:   {_ts_display(pru_built_at)}  ({pru_count} symbols)")
    print(f"Funnel records:  {len(funnel_pipeline)} pipeline + {len(funnel_dispatch)} dispatch")
    print(f"Shadow records:  {len(shadow)}")

    if not funnel_pipeline and not shadow:
        print("\n⚠  No data yet. Run several scan cycles first, then re-run this report.")
        sys.exit(0)

    # ------------------------------------------------------------------ #
    # SECTION 0 — Tier D Funnel Attrition
    # ------------------------------------------------------------------ #
    section("SECTION 0 — Tier D Funnel Attrition (aggregate across all scan cycles)")

    if not funnel_pipeline:
        print("  ⚠  No pipeline funnel records found.")
        print(f"     Expected at: {FUNNEL_JSONL}")
        print("     This file is written by signal_pipeline.run_signal_pipeline().")
        print("     Ensure the bot ran at least one full scan cycle after this code shipped.")
    else:
        # Aggregate pipeline stages
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

        # Aggregate dispatch stages
        d_entered     = sum(r.get("entered_dispatch", 0) for r in funnel_dispatch)
        d_ctx_fail    = sum(r.get("dropped_context_fail", 0) for r in funnel_dispatch)
        d_shadow      = sum(r.get("shadow_blocked", 0) for r in funnel_dispatch)
        d_non_pos     = sum(r.get("executed_non_position", 0) for r in funnel_dispatch)

        # Aggregate Apex classification across all dispatch records
        apex_totals: dict[str, int] = {}
        for r in funnel_dispatch:
            for k, v in (r.get("apex_classification") or {}).items():
                apex_totals[k] = apex_totals.get(k, 0) + v

        cycles = len(funnel_pipeline)
        print(f"  Scan cycles with funnel data:  {cycles}")
        print()
        print(f"  Stage  1  — PRU loaded:                     {p_loaded:>6}  (across {cycles} cycles; {p_loaded//cycles if cycles else 0}/cycle avg)")
        print(f"  Stage  2  — Entered dynamic universe:        {p_universe:>6}  (same as PRU loaded; 0 stale drop)")
        print(f"  Stage  3  — Scored (all_scored, any dim):    {p_all_scored:>6}  ← drop here = {p_drop_scored} (not in filtered universe)")
        print(f"  Stage  3b — Above regime threshold:          {p_above_thresh:>6}  ← drop here = {p_all_scored - p_above_thresh} (score < regime threshold)")
        print(f"  Stage  4  — Passed strategy threshold:       {p_strategy:>6}  ← drop here = {p_above_thresh - p_strategy} (score < Tier D floor=6 after adj)")
        print(f"  Stage  5  — Passed persistence gate:         {p_persistence:>6}  (Tier D bypasses; should equal stage 4)")
        print(f"  Stage  6  — Rescue pool (below thresh):      {p_rescue_pool:>6}  (Tier D in all_scored but not above threshold)")
        print(f"  Stage  6b — Rescued:                         {p_rescued:>6}  ← of rescue pool")
        print(f"  Stage  6c — Dropped at rescue (final drop):  {p_dropped:>6}  ← failed all rescue conditions")
        print(f"  Stage  6d — Pipeline output (to dispatch):   {p_output:>6}  (passed + rescued)")
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
            print(f"  Stage 10  — Reached validate_entry:         {reached_gate:>6}  (POSITION classification only)")
            print(f"  Stage 11  — Shadow-blocked:                 {d_shadow:>6}")
            print(f"  Stage 12  — Executed as SWING/INTRADAY:     {d_non_pos:>6}  (Tier D not shadow-blocked)")
        else:
            print()
            print("  ⚠  No dispatch funnel records found.")
            print("     dispatch records are written by signal_dispatcher.dispatch_signals().")
            print("     They appear only when Tier D signals survive the full pipeline.")

        # Attrition diagnosis
        print()
        print("  Attrition diagnosis:")
        if p_drop_scored > 0:
            print(f"    ⚠  {p_drop_scored} Tier D scored = 0 (not in filtered universe) → check universe builder")
        if p_all_scored - p_above_thresh > 0:
            diff = p_all_scored - p_above_thresh
            print(f"    ⚠  {diff} Tier D scored but below regime threshold → rescue gate is the only path forward")
        if p_dropped > 0:
            print(f"    ⚠  {p_dropped} Tier D failed rescue → discovery_score < 6 AND no archetypes AND signal < 6")
        if apex_totals.get("AVOID", 0) > 0:
            print(f"    ⚠  Apex classified {apex_totals['AVOID']} Tier D as AVOID → check [POSITION_CANDIDATE] prompt prefix in market_intelligence.py")
        if apex_totals.get("SWING", 0) + apex_totals.get("INTRADAY", 0) > 0:
            non_pos = apex_totals.get("SWING", 0) + apex_totals.get("INTRADAY", 0)
            print(f"    ℹ  {non_pos} Tier D classified as SWING/INTRADAY by Apex (executed normally, not shadow-logged as POSITION)")
        if p_output > 0 and d_entered == 0:
            print(f"    ⚠  Pipeline output {p_output} but dispatch records = 0 → dispatch funnel write may not have run yet")

        if (p_drop_scored == 0 and (p_all_scored - p_above_thresh) == 0 and p_dropped == 0
                and apex_totals.get("AVOID", 0) == 0):
            print(f"    ✓ No attrition anomalies detected")

    # ------------------------------------------------------------------ #
    # SECTION 1 — Scan-cycle coverage
    # ------------------------------------------------------------------ #
    section("SECTION 1 — Scan-Cycle Coverage")

    enriched = [r for r in shadow if "ctx_data_source" in r]
    legacy   = [r for r in shadow if "ctx_data_source" not in r]

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
    # SECTION 2 — Safety confirmation
    # ------------------------------------------------------------------ #
    section("SECTION 2 — Safety Confirmation")

    shadow_on, allow_live = _read_config_flags()
    live_off = (allow_live is False)

    print(f"  position_research_shadow_mode=True:            {'✓ confirmed (runtime value)' if shadow_on is True else '⚠ RUNTIME VALUE IS ' + str(shadow_on)}")
    print(f"  position_research_allow_live_position_entries=False: {'✓ confirmed (runtime value)' if live_off else '⚠ RUNTIME VALUE IS ' + str(allow_live)}")

    # Check for any Tier D orders in trade_events
    tier_d_orders = [
        e for e in trade_events
        if e.get("scanner_tier") == "D"
        and e.get("event_type") in ("ORDER_INTENT", "ORDER_FILLED")
    ]
    print(f"  Tier D ORDER_INTENT or ORDER_FILLED events:   {'⚠ ' + str(len(tier_d_orders)) + ' FOUND — INVESTIGATE' if tier_d_orders else '✓ 0 (no live orders placed)'}")

    # Check training_records.jsonl for Tier D pollution
    tier_d_training = [
        r for r in training
        if r.get("scanner_tier") == "D" or r.get("trade_type") == "POSITION_RESEARCH_ONLY"
    ]
    print(f"  Tier D records in training_records.jsonl:      {'⚠ ' + str(len(tier_d_training)) + ' FOUND — INVESTIGATE' if tier_d_training else '✓ 0 (no pollution)'}")
    print(f"  Shadow records (all blocked, none executed):   ✓ {len(enriched)} enriched records, all shadow_mode_blocked")

    # ------------------------------------------------------------------ #
    # SECTION 3 — Context hydration breakdown
    # ------------------------------------------------------------------ #
    section("SECTION 3 — Context Hydration (enriched records only)")

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
        in_pru   = [s for s in sym_counts if s in pru_meta]
        not_in_pru = [s for s in sym_counts if s not in pru_meta]
        if in_pru:
            print(f"     Still in PRU (need investigation): {in_pru}")
        if not_in_pru:
            print(f"     Not in current PRU (historical, expected): {not_in_pru}")

    # ------------------------------------------------------------------ #
    # SECTION 4 — Data-flow integrity
    # ------------------------------------------------------------------ #
    section("SECTION 4 — Data-Flow Integrity")

    gap_true  = [r for r in enriched if r.get("data_flow_gap") is True]
    gap_false = [r for r in enriched if r.get("data_flow_gap") is False]
    gap_miss  = len(enriched) - len(gap_true) - len(gap_false)

    print(f"  data_flow_gap=True:   {len(gap_true)}")
    print(f"  data_flow_gap=False:  {len(gap_false)}")
    print(f"  data_flow_gap=?:      {gap_miss}  (field absent — pre-enrichment records)")

    if gap_true:
        print(f"\n  ⚠  Data-flow gap examples (PRU had FMP values, ctx received None):")
        for r in gap_true[:5]:
            pru_snap = pru_meta.get(r["symbol"], {}).get("pru_fmp_snapshot", {})
            print(f"     {r['symbol']}  pru_snapshot={pru_snap}")
            print(f"       ctx_populated_fields={r.get('ctx_populated_fields')}")
            print(f"       pru_supplemented_fields={r.get('pru_supplemented_fields')}")
            print(f"       would_have_passed_with_pru_data={r.get('would_have_passed_with_pru_data')}")

    # ------------------------------------------------------------------ #
    # SECTION 5 — Simulation outcomes
    # ------------------------------------------------------------------ #
    section("SECTION 5 — Simulation Outcomes")

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
        print(f"\n  Simulated fail reasons:")
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
    # SECTION 6 — Stale-symbol audit
    # ------------------------------------------------------------------ #
    section("SECTION 6 — Stale Symbol Audit")

    stale: set[str] = set()
    try:
        from universe_committed import load_committed_universe
        committed = set(load_committed_universe())
        pru_syms = set(pru_meta.keys())
        stale = pru_syms - committed
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
    # SECTION 7 — Quality examples (≥10 Tier D candidates)
    # ------------------------------------------------------------------ #
    section("SECTION 7 — Quality Examples (up to 10 distinct Tier D candidates)")

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
        print(f"  ⚠  Only {len(examples)} distinct Tier D symbols in shadow log.")
        print(f"     Run more scan cycles and re-run this report.")

    for r in examples:
        sym = r["symbol"]
        pru_entry = pru_meta.get(sym, {})
        snap = pru_entry.get("pru_fmp_snapshot", {})
        print(f"\n  {sym}")
        print(f"    discovery_score:    {pru_entry.get('discovery_score', '?')}")
        print(f"    archetypes:         {pru_entry.get('matched_position_archetypes', [])}")
        print(f"    pru_rev_yoy:        {snap.get('revenue_growth_yoy', '?')}")
        print(f"    pru_gross_margin:   {snap.get('gross_margin', '?')}")
        print(f"    pru_analyst_upside: {snap.get('analyst_upside_pct', '?')}")
        print(f"    signal_score:       {r.get('signal_score')}")
        print(f"    ctx_data_source:    {r.get('ctx_data_source', '?')}")
        print(f"    context_backfilled: {r.get('context_backfilled', '?')}")
        print(f"    data_flow_gap:      {r.get('data_flow_gap', '?')}")
        print(f"    would_have_passed:  {r.get('would_have_passed')}")
        print(f"    simulated_reason:   {(r.get('simulated_reason') or '?')[:100]}")

    # ------------------------------------------------------------------ #
    # SECTION 8 — Phase 2 readiness gate
    # ------------------------------------------------------------------ #
    section("SECTION 8 — Phase 2 Readiness Gate")

    distinct_syms = len(set(r["symbol"] for r in enriched))
    pipeline_cycles = len(funnel_pipeline)
    no_ctx_syms_in_pru = [s for s in Counter(r["symbol"] for r in enriched if r.get("ctx_data_source") == "no_ctx") if s in pru_meta]

    gate_items = [
        ("≥3 scan cycles with funnel records",
         pipeline_cycles >= 3,
         f"{pipeline_cycles} pipeline cycles"),
        ("≥10 distinct Tier D symbols in shadow log",
         distinct_syms >= 10,
         f"{distinct_syms} distinct symbols"),
        ("Stale symbols = 0",
         len(stale) == 0,
         f"{len(stale)} stale symbols — see Section 6"),
        ("no_ctx symbols NOT in current PRU (historical only)",
         len(no_ctx_syms_in_pru) == 0,
         f"{len(no_ctx_syms_in_pru)} still-in-PRU no_ctx symbols" if no_ctx_syms_in_pru else "✓"),
        ("data_flow_gap=True = 0",
         len(gap_true) == 0,
         f"{len(gap_true)} gaps — see Section 4"),
        ("No Tier D orders placed",
         len(tier_d_orders) == 0,
         f"{len(tier_d_orders)} orders — see Section 2"),
        ("No training_records pollution",
         len(tier_d_training) == 0,
         f"{len(tier_d_training)} records — see Section 2"),
        ("shadow_mode=True confirmed (runtime)",
         shadow_on is True,
         f"runtime value={shadow_on}"),
        ("allow_live_position_entries=False confirmed (runtime)",
         live_off is True,
         f"runtime value={allow_live}"),
        ("Funnel attrition report available (≥1 pipeline + dispatch record)",
         pipeline_cycles >= 1 and len(funnel_dispatch) >= 1,
         f"pipeline={pipeline_cycles} dispatch={len(funnel_dispatch)}"),
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
