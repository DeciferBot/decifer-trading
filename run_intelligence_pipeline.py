"""
run_intelligence_pipeline.py — regenerates all three intelligence output files in sequence.

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

No LLM calls. No broker calls. No live data. Safe to run at any time.
"""

from __future__ import annotations

import sys

from candidate_resolver import generate_feed
from intelligence_engine import generate_economic_intelligence
from theme_activation_engine import generate_theme_activation


def run() -> None:
    print("=== Intelligence Pipeline ===")

    print("[1/3] Resolving candidates...")
    feed = generate_feed()
    print(f"      {len(feed.candidates)} candidates → data/intelligence/economic_candidate_feed.json")

    print("[2/3] Generating economic intelligence...")
    daily_state, _ = generate_economic_intelligence()
    active_drivers = len(daily_state.get("active_drivers", []))
    print(f"      {active_drivers} active drivers → daily_economic_state.json + current_economic_context.json")

    print("[3/3] Computing theme activation...")
    activation = generate_theme_activation()
    summary = activation.get("activation_summary", {})
    activated = summary.get("activated", 0)
    total = summary.get("total_themes", 0)
    print(f"      {activated}/{total} themes activated → data/intelligence/theme_activation.json")

    print("=== Done ===")


if __name__ == "__main__":
    run()
    sys.exit(0)
