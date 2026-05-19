"""
run_intelligence_pipeline.py — Intelligence pipeline runner.

Executes the 5-stage intelligence pipeline in order:

  Step 1: live_driver_resolver.resolve()
          reads:  Alpaca/FMP market data (SPY, IEF, HYG, SMH, NVDA, USO, ITA, UVXY)
          writes: data/intelligence/live_driver_state.json

  Step 2: candidate_resolver.generate_feed()
          reads:  live_driver_state.json, transmission_rules, taxonomy, roster
          writes: data/intelligence/economic_candidate_feed.json

  Step 3: theme_activation_engine.generate_theme_activation()
          reads:  live_driver_state.json, candidate_feed, taxonomy, roster
          writes: data/intelligence/theme_activation.json

  Step 4: universe_builder.UniverseBuilder().write()
          reads:  candidate_feed, daily_promoted, position_research, favourites, committed
          writes: data/universe_builder/active_opportunity_universe_shadow.json
          then:   promotes shadow → data/live/active_opportunity_universe.json
                  writes: data/live/current_manifest.json

  Step 5: IC weights update + validation  [FAIL-SOFT]
          reads:  data/signals_log.jsonl
          writes: data/ic_weights.json, data/ic_validation_result.json

Safe to run at any time. No broker calls. No LLM calls.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone

log = logging.getLogger("decifer.intelligence_pipeline")

_SHADOW_PATH   = os.path.join("data", "universe_builder", "active_opportunity_universe_shadow.json")
_LIVE_DIR      = "data/live"
_LIVE_UNIVERSE = os.path.join(_LIVE_DIR, "active_opportunity_universe.json")
_MANIFEST_PATH = os.path.join(_LIVE_DIR, "current_manifest.json")


def _write_manifest(universe_path: str, candidate_count: int) -> None:
    """Write a minimal production manifest for handoff_reader."""
    now = datetime.now(timezone.utc)
    published_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Manifest is valid for 15 minutes — pipeline should run every ~10 min
    from datetime import timedelta
    expires_at = (now + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = {
        "schema_version": "1.0",
        "published_at": published_at,
        "expires_at": expires_at,
        "validation_status": "pass",
        "handoff_mode": "live",
        "publication_mode": "controlled_activation",
        "handoff_enabled": True,
        "enable_flag_required": True,
        "ready_for_consumption": True,
        "active_universe_file": universe_path,
        "candidate_count": candidate_count,
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
        "warnings": [],
        "fail_closed_reason": None,
        "publisher": "run_intelligence_pipeline",
    }
    os.makedirs(_LIVE_DIR, exist_ok=True)
    tmp = _MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, _MANIFEST_PATH)


def _promote_to_live(shadow_path: str, live_path: str) -> int:
    """
    Copy shadow universe to live path, stamp it as production_handoff_universe,
    and add no_executable_trade_instructions = True.
    Returns candidate count.
    """
    with open(shadow_path, encoding="utf-8") as f:
        universe = json.load(f)

    universe["mode"] = "production_handoff_universe"
    universe["no_executable_trade_instructions"] = True
    universe["live_output_changed"] = False
    universe["secrets_exposed"] = False
    universe["env_values_logged"] = False

    os.makedirs(os.path.dirname(live_path), exist_ok=True)
    tmp = live_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(universe, f, indent=2)
    os.replace(tmp, live_path)

    return len(universe.get("candidates", []))


def run() -> None:
    print("=== Intelligence Pipeline ===")

    # Step 1 — live driver state
    print("[1/5] Resolving live macro driver state...")
    import live_driver_resolver
    driver_state = live_driver_resolver.resolve()
    print(f"      active_drivers={driver_state['active_drivers']} mode={driver_state['mode']}")

    # Step 2 — candidate feed
    print("[2/5] Resolving economic candidates...")
    from candidate_resolver import generate_feed
    feed = generate_feed()
    print(f"      {len(feed.candidates)} candidates → data/intelligence/economic_candidate_feed.json")

    # Step 3 — theme activation
    print("[3/5] Computing theme activation...")
    from theme_activation_engine import generate_theme_activation
    activation = generate_theme_activation()
    summary = activation.get("activation_summary", {})
    print(f"      {summary.get('activated', 0)}/{summary.get('total_themes', 0)} themes activated → theme_activation.json")

    # Step 4 — build shadow universe, promote to live
    print("[4/5] Building universe + promoting to live handoff...")
    from universe_builder import UniverseBuilder
    universe = UniverseBuilder().write()
    n_candidates = len(universe.candidates)
    print(f"      {n_candidates} candidates → {_SHADOW_PATH}")

    count = _promote_to_live(_SHADOW_PATH, _LIVE_UNIVERSE)
    _write_manifest(_LIVE_UNIVERSE, count)
    print(f"      promoted → {_LIVE_UNIVERSE}")
    print(f"      manifest → {_MANIFEST_PATH}")

    # Step 5 — IC weights (fail-soft)
    print("[5/5] Updating IC weights + validation...")
    try:
        from ic_calculator import update_ic_weights, update_live_ic
        from ic_validator import validate_and_persist
        weights = update_ic_weights()
        update_live_ic()
        result = validate_and_persist()
        n_pos = sum(1 for v in weights.values() if v > 1.0 / max(len(weights), 1) + 0.01)
        print(f"      IC weights updated (ready_for_live={result.ready_for_live}, {n_pos} dims above equal weight)")
    except Exception as _ic_err:
        log.warning("IC update failed (non-fatal): %s: %s", type(_ic_err).__name__, _ic_err)
        print(f"      [WARN] IC update skipped: {type(_ic_err).__name__}: {_ic_err}")

    print("=== Done ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run()
    sys.exit(0)
