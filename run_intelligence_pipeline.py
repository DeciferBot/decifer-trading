"""
run_intelligence_pipeline.py — regenerates all intelligence output files in sequence.

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

Step 5: IC weights update + validation refresh  [FAIL-SOFT]
         reads: data/signals_log.jsonl (+ yfinance for forward returns)
         writes: data/ic_weights.json
                 data/ic_weights_history.jsonl
                 data/ic_weights_live.json
                 data/ic_weights_live_history.jsonl
                 data/ic_validation_result.json
         failure: logged at WARNING; pipeline exits 0 so Steps 1–4 output
                  reaches universe_builder.py and handoff_publisher.py.

No LLM calls. No broker calls. Safe to run at any time (Step 5 makes yfinance calls).
"""

from __future__ import annotations

import logging
import sys

from candidate_resolver import generate_feed
from ic_calculator import update_ic_weights, update_live_ic
from ic_validator import validate_and_persist
from intelligence_engine import generate_economic_intelligence
from theme_activation_engine import generate_theme_activation
from thesis_store import generate_thesis_store

log = logging.getLogger("decifer.intelligence_pipeline")


def run() -> None:
    print("=== Intelligence Pipeline ===")

    print("[1/5] Resolving candidates...")
    feed = generate_feed()
    print(f"      {len(feed.candidates)} candidates → data/intelligence/economic_candidate_feed.json")

    print("[2/5] Generating economic intelligence...")
    daily_state, _ = generate_economic_intelligence()
    active_drivers = len(daily_state.get("active_drivers", []))
    print(f"      {active_drivers} active drivers → daily_economic_state.json + current_economic_context.json")

    print("[3/5] Computing theme activation...")
    activation = generate_theme_activation()
    summary = activation.get("activation_summary", {})
    activated = summary.get("activated", 0)
    total = summary.get("total_themes", 0)
    print(f"      {activated}/{total} themes activated → data/intelligence/theme_activation.json")

    print("[4/5] Building thesis store...")
    ts_result = generate_thesis_store()
    ts_count = ts_result.get("thesis_summary", {}).get("total_theses", 0)
    missing = ts_result.get("unavailable_sources", [])
    if missing:
        print(f"      thesis_store.json written ({ts_count} theses; {len(missing)} source(s) unavailable: {missing})")
    else:
        print(f"      {ts_count} theses → data/intelligence/thesis_store.json")

    print("[5/5] Updating IC weights + validation...")
    try:
        weights = update_ic_weights()
        update_live_ic()
        result = validate_and_persist()
        n_pos = sum(1 for v in weights.values() if v > 1.0 / len(weights) + 0.01)
        print(f"      IC weights updated (ready_for_live={result.ready_for_live}, {n_pos} dims above equal weight)")
        print(f"      → data/ic_weights.json + data/ic_validation_result.json")
    except Exception as _ic_err:
        log.warning(
            "IC update failed (non-fatal) — universe and handoff will still run: %s: %s",
            type(_ic_err).__name__, _ic_err,
        )
        print(f"      [WARN] IC update skipped due to error: {type(_ic_err).__name__}: {_ic_err}")

    print("=== Done ===")


if __name__ == "__main__":
    run()
    sys.exit(0)
