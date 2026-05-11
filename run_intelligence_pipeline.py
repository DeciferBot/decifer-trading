"""
run_intelligence_pipeline.py — regenerates all four intelligence output files in sequence.

Step 1: candidate_resolver.generate_feed()
         reads: transmission_rules, taxonomy, roster
         writes: data/intelligence/economic_candidate_feed.json

Step 2: intelligence_engine.generate_economic_intelligence()
         reads: candidate_feed, shadow, report, comparison
         writes: data/intelligence/daily_economic_state.json
                 data/intelligence/current_economic_context.json

Step 3: theme_activation_engine.generate_theme_activation()
         reads: daily_economic_state, candidate_feed, taxonomy, roster
         writes: data/intelligence/theme_activation.json

Step 4: thesis_store.generate_thesis_store()
         reads: theme_activation.json, current_economic_context.json,
                economic_candidate_feed.json, active_opportunity_universe_shadow.json
         writes: data/intelligence/thesis_store.json

No LLM calls. No broker calls. No live data. Safe to run at any time.
"""

from __future__ import annotations

import sys

from candidate_resolver import generate_feed
from intelligence_engine import generate_economic_intelligence
from theme_activation_engine import generate_theme_activation
from thesis_store import generate_thesis_store


def run() -> None:
    print("=== Intelligence Pipeline ===")

    print("[1/4] Resolving candidates...")
    feed = generate_feed()
    print(f"      {len(feed.candidates)} candidates → data/intelligence/economic_candidate_feed.json")

    print("[2/4] Generating economic intelligence...")
    daily_state, _ = generate_economic_intelligence()
    active_drivers = len(daily_state.get("active_drivers", []))
    print(f"      {active_drivers} active drivers → daily_economic_state.json + current_economic_context.json")

    print("[3/4] Computing theme activation...")
    activation = generate_theme_activation()
    summary = activation.get("activation_summary", {})
    activated = summary.get("activated", 0)
    total = summary.get("total_themes", 0)
    print(f"      {activated}/{total} themes activated → data/intelligence/theme_activation.json")

    print("[4/4] Building thesis store...")
    ts_result = generate_thesis_store()
    ts_count = ts_result.get("thesis_summary", {}).get("total_theses", 0)
    missing = ts_result.get("unavailable_sources", [])
    if missing:
        print(f"      thesis_store.json written ({ts_count} theses; {len(missing)} source(s) unavailable: {missing})")
    else:
        print(f"      {ts_count} theses → data/intelligence/thesis_store.json")

    print("=== Done ===")


if __name__ == "__main__":
    run()
    sys.exit(0)
