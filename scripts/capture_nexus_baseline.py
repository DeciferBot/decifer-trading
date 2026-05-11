"""
scripts/capture_nexus_baseline.py — Sprint nexus-runtime-provider-data-stability

Read-only evidence baseline capture.
No broker calls. No LLM calls. No provider network calls. No file writes except output.

Captures:
  - Config flags relevant to Nexus/handoff
  - File existence + mtime for all intelligence/handoff files
  - Manifest freshness
  - Provider cache state (neg_cache, FMP cache count, AV cache count)
  - Bug classification table as structured data

Output: data/runtime/nexus_runtime_bug_baseline.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUTPUT = os.path.join(_BASE, "data", "runtime", "nexus_runtime_bug_baseline.json")

_FILES_TO_CHECK = [
    "data/intelligence/theme_activation.json",
    "data/intelligence/current_economic_context.json",
    "data/intelligence/economic_candidate_feed.json",
    "data/intelligence/thesis_store.json",
    "data/intelligence/daily_economic_state.json",
    "data/universe_builder/active_opportunity_universe_shadow.json",
    "data/live/current_manifest.json",
    "data/live/active_opportunity_universe.json",
    "data/symbol_master.json",
    "data/layer_factor_map.json",
    "data/ic_validation_result.json",
    "data/pru_cache.json",
]

_CONFIG_FLAGS = [
    "enable_active_opportunity_universe_handoff",
    "use_nexus_candidate_source",
    "apex_shadow_mode",
    "USE_APEX_V3_SHADOW",
    "momentum_sentinel_enabled",
    "alpha_vantage_daily_limit",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_snapshot(rel_path: str) -> dict:
    abs_path = os.path.join(_BASE, rel_path)
    if not os.path.exists(abs_path):
        return {"exists": False, "mtime_iso": None, "age_hours": None, "size_bytes": None}
    stat = os.stat(abs_path)
    age_hours = (time.time() - stat.st_mtime) / 3600
    mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "exists": True,
        "mtime_iso": mtime_iso,
        "age_hours": round(age_hours, 2),
        "size_bytes": stat.st_size,
    }


def _config_flags() -> dict:
    try:
        from config import CONFIG
        return {flag: CONFIG.get(flag) for flag in _CONFIG_FLAGS}
    except Exception as e:
        return {"error": str(e)}


def _fmp_cache_state() -> dict:
    try:
        import fmp_client
        return {
            "cache_entries": len(fmp_client._cache),
            "neg_cache_entries": len(fmp_client._neg_cache),
            "neg_cache_keys": list(fmp_client._neg_cache.keys())[:10],
        }
    except Exception as e:
        return {"error": str(e)}


def _av_cache_state() -> dict:
    try:
        import alpha_vantage_client as av
        news_entries = len(av._news_cache)
        articles_cached = av._articles_cache[0] is not None
        return {
            "news_cache_entries": news_entries,
            "articles_cache_populated": articles_cached,
            "calls_today": av.get_calls_today(),
        }
    except Exception as e:
        return {"error": str(e)}


def _manifest_freshness() -> dict:
    manifest_path = os.path.join(_BASE, "data/live/current_manifest.json")
    if not os.path.exists(manifest_path):
        return {"available": False}
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        generated_at = manifest.get("generated_at") or manifest.get("published_at") or ""
        handoff_allowed = manifest.get("handoff_allowed")
        candidate_count = len(manifest.get("accepted_candidates") or [])
        return {
            "available": True,
            "generated_at": generated_at,
            "handoff_allowed": handoff_allowed,
            "accepted_candidate_count": candidate_count,
        }
    except Exception as e:
        return {"available": True, "parse_error": str(e)}


_BUG_CLASSIFICATION = [
    {"id": "1",  "issue": "FMP 402: no negative caching",             "classification": "A+C", "action": "fixed"},
    {"id": "1b", "issue": "FMP Error Message: no negative caching",   "classification": "A+C", "action": "fixed"},
    {"id": "1c", "issue": "FMP 402 log ambiguity",                    "classification": "I",   "action": "fixed"},
    {"id": "2",  "issue": "AV multi-ticker simultaneous-mention",     "classification": "E+B", "action": "fixed"},
    {"id": "2b", "issue": "AV Error Message not handled",             "classification": "E+I", "action": "fixed"},
    {"id": "3",  "issue": "Alpaca startup uses scanner not handoff",  "classification": "C",   "action": "fixed"},
    {"id": "4",  "issue": "thesis_store missing from pipeline",       "classification": "B+G", "action": "fixed"},
    {"id": "5",  "issue": "PRU cache stale",                          "classification": "G",   "action": "no_action_rescue_gated"},
    {"id": "6",  "issue": "current_manifest.json absent in worktree", "classification": "H",   "action": "no_action_env_only"},
    {"id": "7",  "issue": "IBIT OCA 10327",                           "classification": "C",   "action": "no_action_position_protected"},
    {"id": "8",  "issue": "USE_APEX_V3_SHADOW=True",                  "classification": "K",   "action": "no_action_correct"},
    {"id": "9",  "issue": "symbol_master/layer_factor_map 5d old",    "classification": "G",   "action": "no_action_reference_data"},
    {"id": "10", "issue": "thesis_store.json absent",                 "classification": "B",   "action": "fixed_by_bug_4"},
    {"id": "11", "issue": "handoff gate tests fail in worktree",      "classification": "H",   "action": "no_action_env_only"},
]


def capture() -> dict:
    files = {rel: _file_snapshot(rel) for rel in _FILES_TO_CHECK}
    output = {
        "captured_at": _now_iso(),
        "sprint": "nexus-runtime-provider-data-stability",
        "config_flags": _config_flags(),
        "files": files,
        "manifest_freshness": _manifest_freshness(),
        "fmp_cache_state": _fmp_cache_state(),
        "av_cache_state": _av_cache_state(),
        "bug_classification_table": _BUG_CLASSIFICATION,
        "no_live_api_called": True,
        "no_broker_called": True,
        "no_llm_called": True,
    }

    os.makedirs(os.path.dirname(_OUTPUT), exist_ok=True)
    with open(_OUTPUT, "w") as f:
        json.dump(output, f, indent=2)

    return output


if __name__ == "__main__":
    result = capture()
    print(f"Baseline written → {_OUTPUT}")
    print(f"  captured_at:      {result['captured_at']}")
    print(f"  files checked:    {len(result['files'])}")
    existing = sum(1 for v in result["files"].values() if v["exists"])
    print(f"  files existing:   {existing}/{len(result['files'])}")
    print(f"  fmp neg_cache:    {result['fmp_cache_state'].get('neg_cache_entries', 'n/a')}")
    print(f"  av calls today:   {result['av_cache_state'].get('calls_today', 'n/a')}")
    manifest = result["manifest_freshness"]
    if manifest.get("available"):
        print(f"  manifest:         handoff_allowed={manifest.get('handoff_allowed')}, "
              f"candidates={manifest.get('accepted_candidate_count')}")
    else:
        print("  manifest:         not available (expected in worktree)")
