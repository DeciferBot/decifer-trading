"""
tests/test_intelligence_sprint5b.py — Sprint 5B Historical Replay Tests.

Covers all 27 acceptance tests from the Sprint 5B spec (Parts A–E).
Reads generated output files only — no live data, no APIs, no production modules.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BT_DIR = os.path.join(_BASE, "data", "intelligence", "backtest")
_FIXTURES_PATH = os.path.join(_BT_DIR, "historical_replay_fixtures.json")
_RESULTS_PATH = os.path.join(_BT_DIR, "historical_replay_results.json")
_SUMMARY_PATH = os.path.join(_BT_DIR, "intelligence_backtest_summary.json")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Tests 1–4 — historical_replay_fixtures.json exists and is well-formed
# ---------------------------------------------------------------------------
class TestHistoricalReplayFixturesFile:
    def test_historical_replay_fixtures_exists(self):
        assert os.path.isfile(_FIXTURES_PATH), \
            f"historical_replay_fixtures.json not found at {_FIXTURES_PATH}"

    def test_has_at_least_6_scenarios(self):
        data = _load(_FIXTURES_PATH)
        scenarios = data.get("scenarios") or []
        assert len(scenarios) >= 6, \
            f"Expected >= 6 fixture scenarios, got {len(scenarios)}"
        assert data.get("total_scenarios", 0) >= 6, \
            f"total_scenarios must be >= 6, got {data.get('total_scenarios')}"

    def test_each_fixture_has_date_anchor(self):
        data = _load(_FIXTURES_PATH)
        for s in (data.get("scenarios") or []):
            assert "date_anchor" in s and s["date_anchor"], \
                f"Fixture '{s.get('scenario_id')}' missing date_anchor"

    def test_each_fixture_has_expected_forbidden_outputs(self):
        data = _load(_FIXTURES_PATH)
        for s in (data.get("scenarios") or []):
            efs = s.get("expected_forbidden_outputs")
            assert isinstance(efs, dict), \
                f"Fixture '{s.get('scenario_id')}' missing expected_forbidden_outputs"
            assert efs.get("executable_candidates") is False, \
                f"Fixture '{s.get('scenario_id')}' executable_candidates must be false"


# ---------------------------------------------------------------------------
# Tests 5–6 — historical_replay_results.json generated and validates
# ---------------------------------------------------------------------------
class TestHistoricalReplayResultsFile:
    def test_historical_replay_results_exists(self):
        assert os.path.isfile(_RESULTS_PATH), \
            f"historical_replay_results.json not found at {_RESULTS_PATH}"

    def test_historical_replay_results_validates(self):
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import validate_historical_replay_results
        result = validate_historical_replay_results(_RESULTS_PATH)
        assert result.ok, \
            f"historical_replay_results.json validation failed: {result.errors}"


# ---------------------------------------------------------------------------
# Tests 7–12 — Each named scenario runs and has a result
# ---------------------------------------------------------------------------
class TestHistoricalReplayScenarios:
    @pytest.fixture(scope="class")
    def results(self):
        return _load(_RESULTS_PATH)

    @pytest.fixture(scope="class")
    def results_by_id(self, results):
        return {r["scenario_id"]: r for r in (results.get("results") or [])}

    def _check_scenario(self, results_by_id: dict, scenario_id: str) -> None:
        assert scenario_id in results_by_id, \
            f"Scenario '{scenario_id}' not found in historical_replay_results"
        r = results_by_id[scenario_id]
        assert r.get("pass") is True, \
            f"Scenario '{scenario_id}' failed: {r.get('mismatches')}"

    def test_2022_rate_inflation_shock_runs(self, results_by_id):
        self._check_scenario(results_by_id, "2022_rate_inflation_shock")

    def test_2022_ukraine_oil_geopolitical_shock_runs(self, results_by_id):
        self._check_scenario(results_by_id, "2022_ukraine_oil_geopolitical_shock")

    def test_2023_ai_infrastructure_emergence_runs(self, results_by_id):
        self._check_scenario(results_by_id, "2023_ai_infrastructure_emergence")

    def test_2023_rate_peak_growth_pressure_runs(self, results_by_id):
        self._check_scenario(results_by_id, "2023_rate_peak_growth_pressure")

    def test_2024_rate_cut_pivot_selective_risk_on_runs(self, results_by_id):
        self._check_scenario(results_by_id, "2024_rate_cut_pivot_selective_risk_on")

    def test_covid_liquidity_shock_runs(self, results_by_id):
        self._check_scenario(results_by_id, "covid_liquidity_shock_and_policy_support")


# ---------------------------------------------------------------------------
# Tests 13–16 — No scenario creates executable candidates or uses forbidden paths
# ---------------------------------------------------------------------------
class TestHistoricalReplayForbiddenPaths:
    @pytest.fixture(scope="class")
    def results(self):
        return _load(_RESULTS_PATH)

    def test_no_scenario_creates_executable_candidates(self, results):
        for r in (results.get("results") or []):
            foc = r.get("forbidden_outputs_checked") or {}
            assert foc.get("executable_candidates") is False, \
                f"Scenario '{r.get('scenario_id')}' has executable_candidates=True"

    def test_no_scenario_uses_symbol_discovery(self, results):
        for r in (results.get("results") or []):
            foc = r.get("forbidden_outputs_checked") or {}
            assert foc.get("symbol_discovery") is False, \
                f"Scenario '{r.get('scenario_id')}' uses symbol discovery"

    def test_no_scenario_uses_raw_news(self, results):
        for r in (results.get("results") or []):
            foc = r.get("forbidden_outputs_checked") or {}
            assert foc.get("raw_news_used") is False, \
                f"Scenario '{r.get('scenario_id')}' uses raw news"

    def test_no_scenario_uses_llm(self, results):
        for r in (results.get("results") or []):
            foc = r.get("forbidden_outputs_checked") or {}
            assert foc.get("llm_used") is False, \
                f"Scenario '{r.get('scenario_id')}' uses LLM"

    def test_no_scenario_calls_live_apis(self, results):
        for r in (results.get("results") or []):
            foc = r.get("forbidden_outputs_checked") or {}
            assert foc.get("live_api_called") is False, \
                f"Scenario '{r.get('scenario_id')}' calls live API"


# ---------------------------------------------------------------------------
# Tests 17–22 — Safety flags across historical replay outputs
# ---------------------------------------------------------------------------
class TestHistoricalReplaySafetyFlags:
    @pytest.fixture(scope="class")
    def all_files(self):
        return {
            "historical_replay_fixtures": _load(_FIXTURES_PATH),
            "historical_replay_results": _load(_RESULTS_PATH),
        }

    def test_no_live_api_called_true(self, all_files):
        for label, data in all_files.items():
            assert data.get("no_live_api_called") is True, \
                f"{label}: no_live_api_called must be True"

    def test_broker_called_false(self, all_files):
        for label, data in all_files.items():
            assert data.get("broker_called") is False, \
                f"{label}: broker_called must be False"

    def test_env_inspected_false(self, all_files):
        for label, data in all_files.items():
            assert data.get("env_inspected") is False, \
                f"{label}: env_inspected must be False"

    def test_broad_intraday_scan_false(self, all_files):
        for label, data in all_files.items():
            assert data.get("broad_intraday_scan_used") is False, \
                f"{label}: broad_intraday_scan_used must be False"

    def test_llm_used_false(self, all_files):
        for label, data in all_files.items():
            assert data.get("llm_used") is False, \
                f"{label}: llm_used must be False"

    def test_live_output_changed_false(self, all_files):
        for label, data in all_files.items():
            assert data.get("live_output_changed") is False, \
                f"{label}: live_output_changed must be False"


# ---------------------------------------------------------------------------
# Tests 23–24 — Summary includes historical_replay_status and valid decision_gate
# ---------------------------------------------------------------------------
class TestSummaryHistoricalReplayStatus:
    @pytest.fixture(scope="class")
    def summary(self):
        return _load(_SUMMARY_PATH)

    def test_summary_includes_historical_replay_status(self, summary):
        assert "historical_replay_status" in summary, \
            "intelligence_backtest_summary.json must include historical_replay_status"
        hrs = summary["historical_replay_status"]
        assert isinstance(hrs, dict), "historical_replay_status must be an object"
        for field in ["scenarios_run", "scenarios_passed", "scenarios_failed",
                      "pass_rate", "limitations"]:
            assert field in hrs, \
                f"historical_replay_status missing field '{field}'"

    def test_decision_gate_is_valid(self, summary):
        valid_gates = {
            "pass_for_next_shadow_sprint",
            "fail_needs_fix",
            "pass_but_not_for_advisory",
            "insufficient_evidence",
            "pass_but_more_replay_needed",
        }
        dg = summary.get("decision_gate")
        assert dg in valid_gates, \
            f"decision_gate '{dg}' not in {valid_gates}"


# ---------------------------------------------------------------------------
# Test 25 — Sprint 5A tests still pass (spot check)
# ---------------------------------------------------------------------------
class TestSprint5ARegression:
    def test_sprint5a_regime_fixture_results_still_valid(self):
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import validate_regime_fixture_results
        regime_path = os.path.join(_BT_DIR, "regime_fixture_results.json")
        result = validate_regime_fixture_results(regime_path)
        assert result.ok, f"regime_fixture_results validation failed: {result.errors}"

    def test_sprint5a_risk_overlay_still_clean(self):
        risk_path = os.path.join(_BT_DIR, "risk_overlay_fixture_results.json")
        data = _load(risk_path)
        assert data.get("headwind_candidates_executable") is False
        assert data.get("structural_displaced_by_attention") is False
        assert data.get("attention_cap_respected") is True

    def test_sprint5a_ablation_variants_still_present(self):
        ablation_path = os.path.join(_BT_DIR, "candidate_feed_ablation_results.json")
        data = _load(ablation_path)
        labels = {v.get("variant_label") for v in (data.get("variants") or [])}
        for required in ["baseline_shadow_universe", "no_quota_allocator", "no_route_tagger"]:
            assert required in labels, f"Ablation variant '{required}' missing"


# ---------------------------------------------------------------------------
# Test 26 — Intelligence regression spot check (prior sprints not regressed)
# ---------------------------------------------------------------------------
class TestIntelligenceRegressionSpotCheck:
    def test_theme_activation_live_output_changed_still_false(self):
        path = os.path.join(_BASE, "data", "intelligence", "theme_activation.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("live_output_changed") is False

    def test_thesis_store_live_output_changed_still_false(self):
        path = os.path.join(_BASE, "data", "intelligence", "thesis_store.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("live_output_changed") is False

    def test_shadow_universe_freshness_unchanged(self):
        path = os.path.join(_BASE, "data", "universe_builder",
                            "active_opportunity_universe_shadow.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("freshness_status") in (
            "static_bootstrap_sprint3", "static_bootstrap_sprint2",
            "static_bootstrap_day7",
        )

    def test_daily_economic_state_live_output_changed_still_false(self):
        path = os.path.join(_BASE, "data", "intelligence", "daily_economic_state.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("live_output_changed") is False


# ---------------------------------------------------------------------------
# Test 27 — Smoke spot check
# ---------------------------------------------------------------------------
class TestSmokeSpotCheck:
    def test_validator_includes_historical_replay(self):
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import validate_all
        results = validate_all(os.path.join(_BASE, "data", "intelligence"))
        for key in ["historical_replay_fixtures", "historical_replay_results"]:
            assert key in results, f"validate_all() missing key: {key}"
            assert results[key].ok, \
                f"validate_all() {key} failed: {results[key].errors}"

    def test_backtest_summary_live_output_changed_false(self):
        data = _load(_SUMMARY_PATH)
        assert data.get("live_output_changed") is False
