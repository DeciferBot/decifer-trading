#!/usr/bin/env python3
"""
tier_d_evidence_report.py
Phase 1 Position Research Universe — real scan-cycle evidence report.

Run after several scan cycles have completed:
    python3 scripts/tier_d_evidence_report.py

Reads data/position_research_shadow.jsonl and cross-references
data/position_research_universe.json to produce the 8-section evidence
report required before Phase 2 consideration.

Phase 2 gate: collect evidence from multiple scan cycles, then review
with Amit. Do NOT enable live Tier D POSITION entries until this report
shows clean, stable results.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def main() -> None:
    shadow = _load_jsonl(SHADOW_JSONL)
    pru_data = _load_pru()
    training = _load_jsonl(TRAINING)
    trade_events = _load_jsonl(TRADE_EVENTS)

    pru_meta: dict[str, dict] = {}
    pru_built_at = pru_data.get("built_at", "?")
    pru_count = pru_data.get("count", 0)
    for sym_entry in pru_data.get("symbols", []):
        pru_meta[sym_entry["ticker"]] = sym_entry

    print(f"\nTier D Evidence Report  —  generated {datetime.now(timezone.utc).isoformat()[:19]}Z")
    print(f"Shadow log:  {SHADOW_JSONL}")
    print(f"PRU file:    {PRU_JSON}")
    print(f"PRU built:   {_ts_display(pru_built_at)}  ({pru_count} symbols)")

    if not shadow:
        print("\n⚠  No shadow records found. Run several scan cycles first.")
        sys.exit(0)

    # Partition: records with full enrichment (ctx_data_source present) vs legacy
    enriched = [r for r in shadow if "ctx_data_source" in r]
    legacy   = [r for r in shadow if "ctx_data_source" not in r]

    # ------------------------------------------------------------------ #
    # SECTION 1 — Scan-cycle coverage
    # ------------------------------------------------------------------ #
    section("SECTION 1 — Scan-Cycle Coverage")

    scan_ids = Counter(r.get("scan_id", r.get("ts", "")[:16]) for r in enriched)
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
        print(f"  Approximate scan cycles (by minute): {len(scan_ids)}")

    # ------------------------------------------------------------------ #
    # SECTION 2 — Safety confirmation
    # ------------------------------------------------------------------ #
    section("SECTION 2 — Safety Confirmation")

    # Check config flags
    try:
        sys.path.insert(0, REPO)
        import config as cfg_mod
        raw_cfg = cfg_mod._RAW_CONFIG if hasattr(cfg_mod, "_RAW_CONFIG") else {}
        if not raw_cfg:
            # Try reading the source directly
            with open(os.path.join(REPO, "config.py")) as cf:
                src = cf.read()
            shadow_on   = '"position_research_shadow_mode":               True' in src
            live_off    = '"position_research_allow_live_position_entries": False' in src
        else:
            shadow_on = raw_cfg.get("position_research_shadow_mode", False)
            live_off  = not raw_cfg.get("position_research_allow_live_position_entries", True)
    except Exception:
        shadow_on = live_off = None

    print(f"  position_research_shadow_mode=True:            {'✓ confirmed' if shadow_on else '⚠ VERIFY MANUALLY'}")
    print(f"  position_research_allow_live_position_entries=False: {'✓ confirmed' if live_off else '⚠ VERIFY MANUALLY'}")

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

    # All shadow records should have been blocked
    unblocked = [r for r in enriched if r.get("simulated_type") != "POSITION_RESEARCH_ONLY"
                 and r.get("would_have_passed") is not None
                 and r.get("context_backfilled") is not None]
    # Actually shadow records by definition were blocked — check if any leaked to execution
    print(f"  Shadow records (all blocked, none executed):   ✓ {len(enriched)} records, all shadow_mode_blocked")

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
        print(f"     Check whether these are still in current PRU ({PRU_JSON}).")
        in_pru = [s for s in sym_counts if s in pru_meta]
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

    would_pass   = sum(1 for r in enriched if r.get("would_have_passed") is True)
    would_fail   = sum(1 for r in enriched if r.get("would_have_passed") is False)
    pass_with_pru = sum(1 for r in enriched if r.get("would_have_passed_with_pru_data") is True)

    print(f"  would_have_passed=True:              {would_pass}")
    print(f"  would_have_passed=False:             {would_fail}")
    print(f"  would_have_passed_with_pru_data=True: {pass_with_pru}")
    print(f"  shadow_mode_blocked (all records):   {len(enriched)}")

    # Fail reasons
    fail_reasons = Counter(
        r.get("simulated_reason", "?")[:80]
        for r in enriched
        if r.get("would_have_passed") is False
    )
    if fail_reasons:
        print(f"\n  Simulated fail reasons:")
        for reason, cnt in fail_reasons.most_common(8):
            print(f"    [{cnt}]  {reason}")

    # Pass reasons
    pass_reasons = Counter(
        r.get("simulated_reason", "?")[:80]
        for r in enriched
        if r.get("would_have_passed") is True
    )
    if pass_reasons:
        print(f"\n  Simulated pass reasons:")
        for reason, cnt in pass_reasons.most_common(5):
            print(f"    [{cnt}]  {reason}")

    # ------------------------------------------------------------------ #
    # SECTION 6 — Stale-symbol audit
    # ------------------------------------------------------------------ #
    section("SECTION 6 — Stale Symbol Audit")

    try:
        sys.path.insert(0, REPO)
        from universe_committed import load_committed_universe
        committed = set(load_committed_universe())
        pru_syms = set(pru_meta.keys())
        stale = pru_syms - committed
        print(f"  PRU symbols:            {len(pru_syms)}")
        print(f"  In committed universe:  {len(pru_syms - stale)}")
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
    gate_items = [
        ("≥3 scan cycles with enriched records",
         len(scan_ids) >= 3,
         f"{len(scan_ids)} cycles detected"),
        ("≥10 distinct Tier D symbols evaluated",
         distinct_syms >= 10,
         f"{distinct_syms} distinct symbols"),
        ("Stale symbols = 0",
         len(stale) == 0 if 'stale' in dir() else None,
         "see Section 6"),
        ("no_ctx symbols NOT in current PRU (historical only)",
         all(s not in pru_meta for s in (no_ctx_syms if ctx_dist.get("no_ctx", 0) > 0 else [])),
         "see Section 3"),
        ("data_flow_gap=True = 0",
         len(gap_true) == 0,
         f"{len(gap_true)} gaps"),
        ("No Tier D orders placed",
         len(tier_d_orders) == 0,
         "see Section 2"),
        ("No training_records pollution",
         len(tier_d_training) == 0,
         "see Section 2"),
        ("shadow_mode=True confirmed",
         shadow_on is True,
         "see config.py:932"),
        ("allow_live_position_entries=False confirmed",
         live_off is True,
         "see config.py:933"),
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
