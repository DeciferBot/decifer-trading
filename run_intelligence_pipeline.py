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

  Step 6: Counter-thesis FMP verification cache  [FAIL-SOFT]
          writes: data/intelligence/counter_thesis_cache.json

  Step 7: Conviction scoring for full watchlist  [FAIL-SOFT]
          reads:  live handoff candidates + data/favourites.json
          writes: data/intelligence/conviction/scores.json

  Step 8: Earnings transcript intelligence  [FAIL-SOFT]
          reads:  committed universe symbols, FMP transcripts
          writes: data/intelligence/macro_events.jsonl

Safe to run at any time. No broker calls. No LLM calls (except earnings transcripts).
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


def _session_expiry_utc(published_at: datetime) -> datetime:
    """Return session-valid expiry for a handoff published at `published_at`.

    Valid until 22:00 UTC on the same calendar day as publication.
    22:00 UTC covers NYSE close (20:00 UTC EDT / 21:00 UTC EST) plus a 1-2h buffer.
    If publication is at or after 22:00 UTC (edge case / test), push to next day.
    """
    from datetime import time, timedelta
    session_end = datetime.combine(
        published_at.date(), time(22, 0), tzinfo=timezone.utc
    )
    if published_at >= session_end:
        session_end += timedelta(days=1)
    return session_end


def _derive_regime_from_drivers(active_drivers: list[str]) -> str:
    """Derive a canonical regime key from the live driver set.

    Mirrors the _RISK_ON/_RISK_OFF classification in market_now_reconciler so
    the manifest carries a meaningful regime the mobile customer layer can parse.
    geopolitical_risk_rising and oil_supply_shock are sector catalysts, not
    broad risk-off signals — they are excluded from _RISK_OFF intentionally.
    """
    _RISK_ON  = {"futures_risk_on", "small_cap_risk_on", "risk_on_rotation",
                 "credit_stress_easing", "yields_falling", "gold_safe_haven_bid",
                 "ai_capex_growth", "ai_compute_demand"}
    _RISK_OFF = {"futures_risk_off", "yields_rising"}
    driver_set = set(active_drivers)
    on_count  = len(driver_set & _RISK_ON)
    off_count = len(driver_set & _RISK_OFF)
    if off_count > 0 and on_count == 0:
        return "TRENDING_DOWN"
    if on_count > 0:
        return "TRENDING_UP"
    return "RANGE_BOUND"


def _ttg_candidate_to_feed_entry(ttg: dict, now_str: str) -> dict:
    """Convert a TTG shadow candidate dict into an economic_candidate_feed candidate entry."""
    route_hint = ttg.get("route_hint", "swing")
    if isinstance(route_hint, str):
        route_hints = [route_hint]
    else:
        route_hints = list(route_hint) if route_hint else ["swing"]
    return {
        "symbol":                    ttg["symbol"],
        "included_by":               "theme_transmission_graph",
        "theme":                     ttg.get("theme_id", "ttg_theme"),
        "driver":                    "",
        "role":                      "direct_beneficiary",
        "reason":                    ttg.get("reason_to_care", ""),
        "reason_to_care":            ttg.get("reason_to_care", ""),
        "route_hint":                route_hints,
        "confidence":                float(ttg.get("confidence", 0.70)),
        "fresh_until":               now_str,
        "risk_flags":                [],
        "confirmation_required":     [],
        "source_labels":             ["theme_transmission_graph"],
        "transmission_rules_fired":  [],
        "market_confirmation_required": [
            "price_and_volume_confirmation_by_trading_bot",
            "live_spread_and_risk_check_at_execution_only",
        ],
        "generated_at":              now_str,
        "mode":                      "intelligence_advisory_feed",
        "live_output_changed":       False,
        "candidate_source":          "theme_transmission_graph",
        "bucket_id":                 ttg.get("bucket_id", ""),
        "exposure_type":             ttg.get("exposure_type", ""),
        "driver_active":             ttg.get("driver_active", False),
    }


def _inject_ttg_into_feed(feed_path: str) -> int:
    """
    Load evidence-gated TTG candidates and merge them into the candidate feed.

    Rules:
    - Only include TTG candidates where status != 'needs_review'
    - Intelligence-layer candidates already in the feed win on dedup
    - Returns count of TTG candidates injected
    """
    try:
        from theme_graph import get_shadow_candidates
    except Exception as _e:
        log.warning("TTG inject: import failed (non-fatal): %s", _e)
        return 0

    try:
        ttg_raw = get_shadow_candidates()
    except Exception as _e:
        log.warning("TTG inject: get_shadow_candidates failed (non-fatal): %s", _e)
        return 0

    # Filter out suppressed (needs_review / non-active) symbols
    # get_shadow_candidates() already applies the evidence gate; status='needs_review' is
    # excluded by _evidence_gate (only 'active' and 'monitor_only' pass).
    # We additionally drop any that leaked through with status='needs_review'.
    eligible = [t for t in ttg_raw if t.get("status") != "needs_review"]

    if not eligible:
        log.info("TTG inject: 0 eligible TTG candidates after filter")
        return 0

    try:
        with open(feed_path, encoding="utf-8") as f:
            feed_data = json.load(f)
    except Exception as _e:
        log.warning("TTG inject: failed to read feed at %s: %s", feed_path, _e)
        return 0

    existing_symbols: set[str] = {
        c["symbol"] for c in feed_data.get("candidates", [])
        if isinstance(c, dict) and c.get("symbol")
    }

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_until = (now + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")

    injected = 0
    for ttg in eligible:
        sym = ttg.get("symbol", "").strip()
        if not sym or sym in existing_symbols:
            continue
        entry = _ttg_candidate_to_feed_entry(ttg, fresh_until)
        feed_data["candidates"].append(entry)
        existing_symbols.add(sym)
        injected += 1

    if injected > 0:
        tmp = feed_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(feed_data, f, indent=2)
        os.replace(tmp, feed_path)

    log.info("TTG inject: %d candidates injected into %s", injected, feed_path)
    return injected


def _pru_candidate_to_feed_entry(pru_symbol: dict, now_str: str) -> dict:
    """Convert a PRU symbol metadata dict into an economic_candidate_feed candidate entry."""
    ticker = pru_symbol.get("ticker", "")
    archetype = pru_symbol.get("primary_archetype", "")
    reason = (
        f"{ticker} — Position Research Universe: {archetype}. "
        f"{pru_symbol.get('universe_entry_reason', '')}"
    )
    return {
        "symbol":                    ticker,
        "included_by":               "position_research_universe",
        "theme":                     "position_research_universe",
        "driver":                    "",
        "role":                      "direct_beneficiary",
        "reason":                    reason,
        "reason_to_care":            reason,
        "route_hint":                ["position", "swing", "watchlist"],
        "confidence":                0.65,
        "fresh_until":               now_str,
        "risk_flags":                [],
        "confirmation_required":     [],
        "source_labels":             ["position_research_universe"],
        "transmission_rules_fired":  [],
        "market_confirmation_required": [
            "price_and_volume_confirmation_by_trading_bot",
            "live_spread_and_risk_check_at_execution_only",
        ],
        "generated_at":              now_str,
        "mode":                      "intelligence_advisory_feed",
        "live_output_changed":       False,
        "candidate_source":          "position_research_universe",
        "scanner_tier":              "D",
        "primary_archetype":         archetype,
        "universe_bucket":           pru_symbol.get("universe_bucket", ""),
    }


def _inject_pru_into_feed(feed_path: str) -> int:
    """
    Load PRU symbols and merge them into the candidate feed.

    Rules:
    - Loads data/position_research_universe.json (graceful if missing/stale)
    - Intelligence-layer + TTG candidates already in the feed win on dedup
    - Returns count of PRU candidates injected
    """
    try:
        from universe_position import load_position_research_universe
    except Exception as _e:
        log.warning("PRU inject: import failed (non-fatal): %s", _e)
        return 0

    try:
        _tickers, pru_symbols, built_at = load_position_research_universe()
    except Exception as _e:
        log.warning("PRU inject: load failed (non-fatal): %s", _e)
        return 0

    if not pru_symbols:
        log.info("PRU inject: no PRU symbols available (missing or stale)")
        return 0

    try:
        with open(feed_path, encoding="utf-8") as f:
            feed_data = json.load(f)
    except Exception as _e:
        log.warning("PRU inject: failed to read feed at %s: %s", feed_path, _e)
        return 0

    existing_symbols: set[str] = {
        c["symbol"] for c in feed_data.get("candidates", [])
        if isinstance(c, dict) and c.get("symbol")
    }

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    fresh_until = (now + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")

    injected = 0
    for pru_sym in pru_symbols:
        ticker = pru_sym.get("ticker", "").strip()
        if not ticker or ticker in existing_symbols:
            continue
        entry = _pru_candidate_to_feed_entry(pru_sym, fresh_until)
        feed_data["candidates"].append(entry)
        existing_symbols.add(ticker)
        injected += 1

    if injected > 0:
        tmp = feed_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(feed_data, f, indent=2)
        os.replace(tmp, feed_path)

    log.info("PRU inject: %d candidates injected from PRU (built_at=%s)", injected, built_at)
    return injected


def _write_manifest(
    universe_path: str,
    candidate_count: int,
    market_regime: str | None = None,
) -> None:
    """Write a minimal production manifest for handoff_reader."""
    now = datetime.now(timezone.utc)
    published_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Manifest is session-valid: expires at 22:00 UTC same day (covers full NYSE session)
    expires_at = _session_expiry_utc(now).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        "economic_context_file": os.path.join("data", "intelligence", "live_driver_state.json"),
        "source_snapshot_versions": {},
        "candidate_count": candidate_count,
        "market_regime": market_regime,
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
    universe["validation_status"] = "pass"
    universe["source_shadow_file"] = shadow_path
    # Universe is session-valid: expires at 22:00 UTC same day as generation
    gen_str = universe.get("generated_at", "")
    try:
        from datetime import datetime as _dt
        gen = _dt.fromisoformat(gen_str.replace("Z", "+00:00"))
        universe["expires_at"] = _session_expiry_utc(gen).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        universe["expires_at"] = _session_expiry_utc(datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def _cleanup_stale_fail_files() -> int:
    """Delete .fail_*.json files in data/live/ older than 24 hours."""
    import glob as _glob
    import time as _time
    cutoff = _time.time() - 86400
    removed = 0
    for path in _glob.glob(os.path.join(_LIVE_DIR, ".fail_*.json")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def run() -> None:
    print("=== Intelligence Pipeline ===")
    _n = _cleanup_stale_fail_files()
    if _n:
        print(f"      cleaned up {_n} stale .fail_*.json files from {_LIVE_DIR}")

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

    # Step 3.5 — thesis divergence scanner (fail-soft)
    print("[3.5/5] Computing thesis divergence (candidate vs proxy 5D returns)...")
    thesis_intact_map: dict = {}
    try:
        from thesis_divergence import compute_thesis_divergence
        thesis_intact_map = compute_thesis_divergence()
        diverging = sum(1 for v in thesis_intact_map.values() if v is False)
        intact    = sum(1 for v in thesis_intact_map.values() if v is True)
        print(f"      {intact} intact / {diverging} diverging candidates flagged → thesis_divergence.json")
    except Exception as _td_err:
        log.warning("thesis_divergence step failed (non-fatal): %s", _td_err)
        print(f"      [WARN] thesis_divergence skipped: {_td_err}")

    # Step 3.6 — TTG injection (fail-soft)
    print("[3.6/5] Injecting Theme Transmission Graph candidates into feed...")
    try:
        _ttg_count = _inject_ttg_into_feed("data/intelligence/economic_candidate_feed.json")
        print(f"      {_ttg_count} TTG candidates injected")
    except Exception as _ttg_err:
        log.warning("TTG inject failed (non-fatal): %s", _ttg_err)
        print(f"      [WARN] TTG inject skipped: {_ttg_err}")

    # Step 3.7 — PRU injection (fail-soft)
    print("[3.7/5] Injecting Position Research Universe candidates into feed...")
    try:
        _pru_count = _inject_pru_into_feed("data/intelligence/economic_candidate_feed.json")
        print(f"      {_pru_count} PRU candidates injected")
    except Exception as _pru_err:
        log.warning("PRU inject failed (non-fatal): %s", _pru_err)
        print(f"      [WARN] PRU inject skipped: {_pru_err}")

    # Step 4 — build shadow universe, promote to live
    print("[4/5] Building universe + promoting to live handoff...")
    from universe_builder import UniverseBuilder
    universe = UniverseBuilder(thesis_intact_map=thesis_intact_map).write()
    n_candidates = len(universe.candidates)
    print(f"      {n_candidates} candidates → {_SHADOW_PATH}")

    count = _promote_to_live(_SHADOW_PATH, _LIVE_UNIVERSE)
    regime_key = _derive_regime_from_drivers(driver_state["active_drivers"])
    _write_manifest(_LIVE_UNIVERSE, count, market_regime=regime_key)
    print(f"      promoted → {_LIVE_UNIVERSE}")
    print(f"      manifest → {_MANIFEST_PATH}")

    # Step 5 — IC weights (fail-soft)
    print("[5/6] Updating IC weights + validation...")
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

    # Step 6 — Counter-thesis FMP verification cache (fail-soft)
    print("[6/6] Refreshing counter-thesis cache (FMP verification)...")
    try:
        from counter_thesis_engine import build_and_cache_counter_thesis
        ct = build_and_cache_counter_thesis()
        n_conflicts = len(ct.get("structural_conflicts", []))
        freshness = ct.get("data_freshness", "unknown")
        print(f"      {n_conflicts} active conflicts cached (freshness={freshness})")
    except Exception as _ct_err:
        log.warning("Counter-thesis cache refresh failed (non-fatal): %s: %s", type(_ct_err).__name__, _ct_err)
        print(f"      [WARN] Counter-thesis cache skipped: {type(_ct_err).__name__}: {_ct_err}")

    # Step 7 — Conviction scoring for full watchlist (fail-soft)
    # Symbol set = TTG-active ∪ live handoff candidates ∪ favourites.
    print("[7/8] Scoring conviction for full watchlist...")
    try:
        from conviction_cache import refresh_all as _conviction_refresh_all

        _score_symbols: set[str] = set()

        # TTG-active set (status == "active" exposures) — the structural watchlist
        try:
            _ttg_path = os.path.join(
                "data", "intelligence", "theme_graph", "symbol_exposures.json"
            )
            with open(_ttg_path, encoding="utf-8") as _fh:
                _ttg_raw = json.load(_fh)
            _score_symbols |= {
                e.get("symbol", "").upper()
                for e in _ttg_raw.get("exposures", [])
                if e.get("status") == "active" and e.get("symbol")
            }
        except Exception:
            pass

        # Live handoff candidates
        try:
            with open(_LIVE_UNIVERSE, encoding="utf-8") as _fh:
                _lu = json.load(_fh)
            _score_symbols |= {
                c["symbol"].upper() for c in _lu.get("candidates", []) if c.get("symbol")
            }
        except Exception:
            pass

        # Favourites (the user's persistent watchlist — always scored)
        try:
            _favs_path = os.path.join("data", "favourites.json")
            with open(_favs_path, encoding="utf-8") as _fh:
                _favs_raw = json.load(_fh)
            if isinstance(_favs_raw, list):
                _favs = _favs_raw
            elif isinstance(_favs_raw, dict):
                _favs = _favs_raw.get("symbols", _favs_raw.get("favourites", []))
            else:
                _favs = []
            _score_symbols |= {s.upper() for s in _favs if isinstance(s, str) and s}
        except Exception:
            pass

        _score_list = sorted(s for s in _score_symbols if s)
        if _score_list:
            _conviction_refresh_all(_score_list)
            print(f"      conviction scores refreshed for {len(_score_list)} symbols "
                  f"(ttg-active ∪ handoff ∪ favourites)")
        else:
            print("      [WARN] no symbols to score")
    except Exception as _cv_err:
        log.warning("Conviction scoring failed (non-fatal): %s: %s", type(_cv_err).__name__, _cv_err)
        print(f"      [WARN] Conviction scoring skipped: {type(_cv_err).__name__}: {_cv_err}")

    # Step 8 — Earnings transcript intelligence (fail-soft)
    print("[8/8] Processing recent earnings call transcripts...")
    try:
        from earnings_transcript_engine import process_recent_earnings
        # Load committed universe symbols for filtering
        _universe_symbols: list[str] = []
        try:
            _committed_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "data", "committed_universe.json"
            )
            with open(_committed_path, encoding="utf-8") as _fh:
                _cu = json.load(_fh)
            _universe_symbols = [s.get("symbol", "") for s in (_cu if isinstance(_cu, list) else [])]
        except Exception:
            pass
        processed = process_recent_earnings(_universe_symbols, hours_back=36)
        if processed:
            print(f"      transcripts processed: {', '.join(processed)}")
        else:
            print("      no earnings transcripts to process")
    except Exception as _tr_err:
        log.warning("Transcript processing failed (non-fatal): %s: %s", type(_tr_err).__name__, _tr_err)
        print(f"      [WARN] Transcript step skipped: {type(_tr_err).__name__}: {_tr_err}")

    print("=== Done ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run()
    sys.exit(0)
