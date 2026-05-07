"""
tests/test_quota_capacity_calibrator.py — Sprint 7H.3

16 tests for quota_capacity_calibrator.py.
All tests are read-only against already-generated calibration output.
None of them re-run the calibrator (which writes files).
"""

import json
import os

import pytest

_REPORT_PATH  = "data/live/quota_capacity_calibration_report.json"
_SUMMARY_PATH = "docs/intelligence_first_quota_capacity_calibration_summary.md"
_CALIB_DIR    = "data/live/quota_calibration"
_PROD_MANIFEST = "data/live/current_manifest.json"
_PROD_UNIVERSE = "data/live/active_opportunity_universe.json"

_EXPECTED_LABELS = {
    "A_baseline",
    "B_moderate",
    "C_production_candidate",
    "D_upper_bound",
    "E_stress",
}
_GOVERNED_WATCH = ["COST", "MSFT", "PG"]
_QUOTA_WATCH    = ["SNDK", "WDC", "IREN"]


@pytest.fixture(scope="module")
def report():
    with open(_REPORT_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def scenarios(report):
    return {s["label"]: s for s in report["scenarios"]}


# 1. Calibrator module exists
def test_calibrator_exists():
    assert os.path.exists("quota_capacity_calibrator.py"), \
        "quota_capacity_calibrator.py must exist"


# 2. Scenario outputs generated for all 5 labels
def test_scenario_outputs_generated():
    for label in _EXPECTED_LABELS:
        out_path = os.path.join(_CALIB_DIR, f"scenario_{label}", "universe.json")
        assert os.path.exists(out_path), f"Scenario output missing: {out_path}"
        with open(out_path) as f:
            data = json.load(f)
        assert "candidates" in data, f"Scenario {label} output missing 'candidates'"


# 3. Production current_manifest not overwritten
def test_production_manifest_not_overwritten(report):
    assert report["production_manifest_overwritten"] is False
    # Verify file still contains handoff_enabled=false
    with open(_PROD_MANIFEST) as f:
        manifest = json.load(f)
    assert manifest.get("handoff_enabled") is False, \
        "Production manifest handoff_enabled was changed"


# 4. Production active universe not overwritten
def test_production_universe_not_overwritten(report):
    assert report["production_universe_overwritten"] is False
    # Verify the file still has a candidates list
    with open(_PROD_UNIVERSE) as f:
        universe = json.load(f)
    assert isinstance(universe.get("candidates"), list), \
        "Production universe candidates list was corrupted"


# 5. All scenarios validation_only
def test_all_scenarios_validation_only(scenarios):
    for label, sc in scenarios.items():
        flags = sc["safety_flags"]
        assert flags["publication_mode"] == "validation_only", \
            f"{label}: publication_mode must be validation_only"
        assert flags["live_output_changed"] is False, \
            f"{label}: live_output_changed must be False"
        assert flags["handoff_enabled"] is False, \
            f"{label}: handoff_enabled must be False"


# 6. Safety flags clean for all scenarios
def test_safety_flags_clean(scenarios):
    bool_false_flags = [
        "enable_active_opportunity_universe_handoff",
        "handoff_enabled",
        "live_bot_consuming_handoff",
        "production_candidate_source_changed",
        "scanner_output_changed",
        "apex_input_changed",
        "risk_logic_changed",
        "order_logic_changed",
        "broker_called",
        "trading_api_called",
        "llm_called",
        "raw_news_used",
        "broad_intraday_scan_used",
        "secrets_exposed",
        "env_values_logged",
        "live_output_changed",
    ]
    for label, sc in scenarios.items():
        flags = sc["safety_flags"]
        for flag in bool_false_flags:
            assert flags.get(flag) is False, \
                f"{label}: safety flag '{flag}' must be False"


# 7. COST/MSFT/PG tracked in all scenarios
def test_governed_watch_tracked(scenarios):
    for label, sc in scenarios.items():
        gw = sc["governed_watch_status"]
        for sym in _GOVERNED_WATCH:
            assert sym in gw, f"{label}: governed_watch_status missing {sym}"
            assert gw[sym] in {"included", "excluded"}, \
                f"{label}: governed_watch_status[{sym}] must be 'included' or 'excluded'"


# 8. SNDK/WDC/IREN tracked in all scenarios
def test_quota_watch_tracked(scenarios):
    for label, sc in scenarios.items():
        qw = sc["quota_watch_status"]
        for sym in _QUOTA_WATCH:
            assert sym in qw, f"{label}: quota_watch_status missing {sym}"
            assert qw[sym] in {"included", "excluded"}, \
                f"{label}: quota_watch_status[{sym}] must be 'included' or 'excluded'"


# 9. ETF/proxy counts reported
def test_etf_proxy_counts_reported(scenarios):
    for label, sc in scenarios.items():
        assert "etf_proxy_count" in sc, f"{label}: etf_proxy_count missing"
        assert isinstance(sc["etf_proxy_count"], int), \
            f"{label}: etf_proxy_count must be int"
        assert "etf_crowding_ratio" in sc, f"{label}: etf_crowding_ratio missing"
        assert 0.0 <= sc["etf_crowding_ratio"] <= 1.0, \
            f"{label}: etf_crowding_ratio must be in [0,1]"


# 10. Governed-but-excluded counts reported
def test_governed_excluded_counts_reported(scenarios):
    for label, sc in scenarios.items():
        assert "governed_excluded_count" in sc, f"{label}: governed_excluded_count missing"
        assert isinstance(sc["governed_excluded_count"], int), \
            f"{label}: governed_excluded_count must be int"
        assert "governed_excluded_symbols" in sc, f"{label}: governed_excluded_symbols missing"
        assert len(sc["governed_excluded_symbols"]) == sc["governed_excluded_count"], \
            f"{label}: governed_excluded_count mismatch"


# 11. Runtime timing fields present
def test_runtime_timing_fields_present(scenarios):
    required_timing = {
        "publisher_generation",
        "manifest_validation",
        "handoff_reader_load",
        "total_scenario",
    }
    for label, sc in scenarios.items():
        timing = sc.get("timing_ms", {})
        for key in required_timing:
            assert key in timing, f"{label}: timing_ms.{key} missing"
            assert isinstance(timing[key], (int, float)), \
                f"{label}: timing_ms.{key} must be numeric"
            assert timing[key] >= 0, \
                f"{label}: timing_ms.{key} must be non-negative"


# 12. Recommendation generated
def test_recommendation_generated(report):
    assert "recommended_scenario" in report, "recommended_scenario missing from report"
    assert report["recommended_scenario"] in _EXPECTED_LABELS, \
        f"recommended_scenario '{report['recommended_scenario']}' not in expected labels"
    assert "recommended_total_cap" in report
    assert "recommended_structural_cap" in report
    assert isinstance(report["recommended_total_cap"], int)
    assert isinstance(report["recommended_structural_cap"], int)


# 13. live_output_changed false at report level
def test_report_live_output_changed_false(report):
    assert report["live_output_changed"] is False
    assert report["production_manifest_overwritten"] is False
    assert report["production_universe_overwritten"] is False


# 14. No production modules modified — calibrator context manager restores constants
# Sprint 7I promoted production constants to 75/35; these assertions verify the
# current production values (not the pre-7I 50/20 values).
def test_no_production_modules_modified():
    import quota_allocator
    assert quota_allocator._TOTAL_MAX == 75, \
        f"quota_allocator._TOTAL_MAX unexpected: {quota_allocator._TOTAL_MAX}"
    assert quota_allocator._STRUCTURAL_MAX == 35, \
        f"quota_allocator._STRUCTURAL_MAX unexpected: {quota_allocator._STRUCTURAL_MAX}"
    assert quota_allocator._ETF_PROXY_MAX == 15, \
        f"quota_allocator._ETF_PROXY_MAX unexpected: {quota_allocator._ETF_PROXY_MAX}"
    assert quota_allocator._ATTENTION_MAX == 20, \
        f"quota_allocator._ATTENTION_MAX unexpected: {quota_allocator._ATTENTION_MAX}"


# 15. Scenario A matches current production baseline
def test_scenario_a_matches_baseline(scenarios):
    a = scenarios["A_baseline"]
    assert a["caps"]["total"] == 50
    assert a["caps"]["structural"] == 20
    assert a["candidate_count"] == 50, \
        f"Scenario A candidate count should be 50 (current production), got {a['candidate_count']}"
    assert a["quota_summary_used"]["structural"] == 20, \
        "Scenario A structural quota should be fully used at 20"
    # Baseline should have COST/MSFT/PG excluded
    for sym in _GOVERNED_WATCH:
        assert a["governed_watch_status"][sym] == "excluded", \
            f"Scenario A: {sym} should be excluded in baseline"


# 16. Scenario B includes all governed watch symbols
def test_scenario_b_includes_governed_symbols(scenarios):
    b = scenarios["B_moderate"]
    assert b["caps"]["total"] == 75
    assert b["caps"]["structural"] == 35
    for sym in _GOVERNED_WATCH:
        assert b["governed_watch_status"][sym] == "included", \
            f"Scenario B: {sym} should be included at structural_cap=35"
    for sym in _QUOTA_WATCH:
        assert b["quota_watch_status"][sym] == "included", \
            f"Scenario B: {sym} should be included at structural_cap=35"
    assert b["governed_excluded_count"] == 0, \
        "Scenario B should have zero governed-but-excluded symbols"
