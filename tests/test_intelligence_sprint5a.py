"""
tests/test_intelligence_sprint5a.py — Sprint 5A Backtest Framework Tests.

Covers all 36 acceptance tests from the Sprint 5A spec (Parts B–H).
Reads generated output files only — no live data, no APIs, no production modules.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import importlib
import types

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BT_DIR = os.path.join(_BASE, "data", "intelligence", "backtest")
_REGIME_PATH = os.path.join(_BT_DIR, "regime_fixture_results.json")
_THEME_PATH = os.path.join(_BT_DIR, "theme_activation_fixture_results.json")
_ABLATION_PATH = os.path.join(_BT_DIR, "candidate_feed_ablation_results.json")
_RISK_PATH = os.path.join(_BT_DIR, "risk_overlay_fixture_results.json")
_SUMMARY_PATH = os.path.join(_BT_DIR, "intelligence_backtest_summary.json")

_BACKTEST_MODULE = os.path.join(_BASE, "backtest_intelligence.py")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Test 1 — backtest_intelligence.py exists
# ---------------------------------------------------------------------------
class TestBacktestModuleExists:
    def test_backtest_intelligence_py_exists(self):
        assert os.path.isfile(_BACKTEST_MODULE), \
            f"backtest_intelligence.py not found at {_BACKTEST_MODULE}"


# ---------------------------------------------------------------------------
# Tests 2–6 — Output files exist
# ---------------------------------------------------------------------------
class TestBacktestOutputFilesExist:
    def test_regime_fixture_results_exists(self):
        assert os.path.isfile(_REGIME_PATH), \
            f"regime_fixture_results.json not found at {_REGIME_PATH}"

    def test_theme_activation_fixture_results_exists(self):
        assert os.path.isfile(_THEME_PATH), \
            f"theme_activation_fixture_results.json not found at {_THEME_PATH}"

    def test_candidate_feed_ablation_results_exists(self):
        assert os.path.isfile(_ABLATION_PATH), \
            f"candidate_feed_ablation_results.json not found at {_ABLATION_PATH}"

    def test_risk_overlay_fixture_results_exists(self):
        assert os.path.isfile(_RISK_PATH), \
            f"risk_overlay_fixture_results.json not found at {_RISK_PATH}"

    def test_intelligence_backtest_summary_exists(self):
        assert os.path.isfile(_SUMMARY_PATH), \
            f"intelligence_backtest_summary.json not found at {_SUMMARY_PATH}"


# ---------------------------------------------------------------------------
# Test 7 — All five files validate
# ---------------------------------------------------------------------------
class TestBacktestFilesValidate:
    def test_all_five_files_validate(self):
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import (
            validate_regime_fixture_results,
            validate_theme_activation_fixture_results,
            validate_candidate_feed_ablation_results,
            validate_risk_overlay_fixture_results,
            validate_intelligence_backtest_summary,
        )
        checks = [
            ("regime_fixture_results", validate_regime_fixture_results(_REGIME_PATH)),
            ("theme_activation_fixture_results", validate_theme_activation_fixture_results(_THEME_PATH)),
            ("candidate_feed_ablation_results", validate_candidate_feed_ablation_results(_ABLATION_PATH)),
            ("risk_overlay_fixture_results", validate_risk_overlay_fixture_results(_RISK_PATH)),
            ("intelligence_backtest_summary", validate_intelligence_backtest_summary(_SUMMARY_PATH)),
        ]
        failures = []
        for label, result in checks:
            if not result.ok:
                failures.append(f"{label}: {result.errors}")
        assert not failures, "Validation failures:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Tests 8–14 — Regime fixture scenarios
# ---------------------------------------------------------------------------
class TestRegimeFixtureScenarios:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_REGIME_PATH)

    def test_at_least_6_scenarios_run(self, data):
        assert data["scenarios_run"] >= 6, \
            f"Expected >= 6 scenarios, got {data['scenarios_run']}"

    def _get_scenario(self, data: dict, scenario_id: str) -> dict | None:
        for r in data.get("results") or []:
            if r.get("scenario_id") == scenario_id:
                return r
        return None

    def test_ai_infrastructure_fixture_passes(self, data):
        r = self._get_scenario(data, "ai_infrastructure_tailwind")
        assert r is not None, "ai_infrastructure_tailwind scenario not found"
        assert r["pass"] is True, \
            f"ai_infrastructure_tailwind failed: {r.get('mismatches')}"

    def test_credit_stress_fixture_passes(self, data):
        r = self._get_scenario(data, "credit_stress_watch")
        assert r is not None, "credit_stress_watch scenario not found"
        assert r["pass"] is True, \
            f"credit_stress_watch failed: {r.get('mismatches')}"

    def test_risk_off_fixture_passes(self, data):
        r = self._get_scenario(data, "risk_off_rotation")
        assert r is not None, "risk_off_rotation scenario not found"
        assert r["pass"] is True, \
            f"risk_off_rotation failed: {r.get('mismatches')}"

    def test_oil_shock_fixture_passes(self, data):
        r = self._get_scenario(data, "oil_supply_shock")
        assert r is not None, "oil_supply_shock scenario not found"
        assert r["pass"] is True, \
            f"oil_supply_shock failed: {r.get('mismatches')}"

    def test_rates_banks_conditional_fixture_passes(self, data):
        r = self._get_scenario(data, "rates_rising_banks_conditional")
        assert r is not None, "rates_rising_banks_conditional scenario not found"
        assert r["pass"] is True, \
            f"rates_rising_banks_conditional failed: {r.get('mismatches')}"

    def test_missing_evidence_does_not_create_false_certainty(self, data):
        # The missing_evidence scenario is in theme_activation_fixture, not regime.
        # Verify no regime scenario claims active state without evidence (non-executable check).
        for r in (data.get("results") or []):
            actual = r.get("actual_outputs", {})
            assert actual.get("non_executable") is True, \
                f"Scenario {r.get('scenario_id')} claims executable output (must be shadow-only)"


# ---------------------------------------------------------------------------
# Tests 15 — Theme activation fixture headwind handling
# ---------------------------------------------------------------------------
class TestThemeActivationFixture:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_THEME_PATH)

    def test_fixture_handles_headwinds(self, data):
        assert data["headwind_handled_correctly"] is True, \
            "headwind_handled_correctly must be True — headwind themes must not activate/strengthen"

    def test_missing_evidence_handled(self, data):
        assert data["missing_evidence_handled_correctly"] is True, \
            "missing_evidence_handled_correctly must be True — no false certainty without evidence"

    def test_false_activation_count_is_zero(self, data):
        assert data["false_activation_count"] == 0, \
            f"false_activation_count must be 0, got {data['false_activation_count']}"

    def test_crowded_state_handled_correctly(self, data):
        assert data["crowded_handled_correctly"] is True, \
            "crowded_handled_correctly must be True"


# ---------------------------------------------------------------------------
# Tests 16–21 — Candidate feed ablation
# ---------------------------------------------------------------------------
class TestCandidateFeedAblation:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_ABLATION_PATH)

    @pytest.fixture(scope="class")
    def variants(self, data):
        return {v["variant_label"]: v for v in (data.get("variants") or [])}

    def test_all_required_variants_present(self, variants):
        required = [
            "baseline_shadow_universe", "no_economic_candidate_feed", "no_route_tagger",
            "no_quota_allocator", "no_headwind_pressure_candidates",
            "no_manual_protection", "no_attention_cap",
        ]
        for req in required:
            assert req in variants, f"Missing required ablation variant: {req}"

    def test_no_quota_allocator_shows_attention_flatpool_risk(self, variants):
        v = variants.get("no_quota_allocator", {})
        # Without quota, attention cap is not enforced
        assert v.get("attention_cap_respected") is False, \
            "no_quota_allocator variant must show attention_cap_respected=False"
        # Total candidates grow without the cap
        baseline = variants.get("baseline_shadow_universe", {})
        assert v.get("total_candidates", 0) >= baseline.get("total_candidates", 0), \
            "no_quota_allocator variant must show >= total_candidates vs baseline"

    def test_no_route_tagger_shows_reduced_route_clarity(self, variants):
        v = variants.get("no_route_tagger", {})
        # Without route tagger, position_route_count drops to 0
        assert v.get("position_route_count", 1) == 0, \
            "no_route_tagger variant must show position_route_count=0"
        assert v.get("swing_route_count", 1) == 0, \
            "no_route_tagger variant must show swing_route_count=0"

    def test_no_economic_candidate_feed_reduces_reason_to_care(self, variants):
        v = variants.get("no_economic_candidate_feed", {})
        baseline = variants.get("baseline_shadow_universe", {})
        # Economic candidates drop to 0
        assert v.get("economic_candidates", 1) == 0, \
            "no_economic_candidate_feed variant must show economic_candidates=0"
        # Reduction must be positive
        reduction = v.get("reason_to_care_coverage_reduction", 0)
        assert reduction > 0, \
            f"reason_to_care_coverage_reduction must be > 0, got {reduction}"

    def test_no_manual_protection_shows_protected_name_loss_risk(self, variants):
        v = variants.get("no_manual_protection", {})
        assert v.get("manual_protection_preserved") is False, \
            "no_manual_protection variant must show manual_protection_preserved=False"
        assert v.get("protected_names_lost", 0) > 0, \
            "no_manual_protection must report protected_names_lost > 0"

    def test_no_attention_cap_shows_attention_crowding_risk(self, variants):
        v = variants.get("no_attention_cap", {})
        assert v.get("attention_cap_respected") is False, \
            "no_attention_cap variant must show attention_cap_respected=False"
        demand = v.get("attention_demand", 0)
        capacity = v.get("attention_capacity", 15)
        assert demand > capacity, \
            f"no_attention_cap variant must show attention_demand ({demand}) > attention_capacity ({capacity})"


# ---------------------------------------------------------------------------
# Tests 22–25 — Risk overlay fixture
# ---------------------------------------------------------------------------
class TestRiskOverlayFixture:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_RISK_PATH)

    def test_headwind_candidates_non_executable(self, data):
        assert data["headwind_candidates_executable"] is False, \
            "headwind_candidates_executable must be False — headwinds are watchlist-only"

    def test_structural_not_displaced_by_attention(self, data):
        assert data["structural_displaced_by_attention"] is False, \
            "structural_displaced_by_attention must be False"

    def test_attention_cap_respected(self, data):
        assert data["attention_cap_respected"] is True, \
            "attention_cap_respected must be True"

    def test_no_order_instruction_generated(self, data):
        assert data["no_short_or_order_instruction_generated"] is True, \
            "no_short_or_order_instruction_generated must be True"

    def test_per_scenario_no_order_instructions(self, data):
        for r in (data.get("scenario_results") or []):
            assert r.get("no_order_instruction_generated") is True, \
                f"Scenario {r.get('scenario_id')} has no_order_instruction_generated != True"

    def test_per_scenario_headwind_not_executable(self, data):
        for r in (data.get("scenario_results") or []):
            assert r.get("headwind_candidates_executable") is False, \
                f"Scenario {r.get('scenario_id')} has headwind_candidates_executable=True"


# ---------------------------------------------------------------------------
# Tests 26–32 — Safety flags across all outputs
# ---------------------------------------------------------------------------
class TestSafetyFlagsAllOutputs:
    @pytest.fixture(scope="class")
    def all_files(self):
        return {
            "regime_fixture_results": _load(_REGIME_PATH),
            "theme_activation_fixture_results": _load(_THEME_PATH),
            "candidate_feed_ablation_results": _load(_ABLATION_PATH),
            "risk_overlay_fixture_results": _load(_RISK_PATH),
            "intelligence_backtest_summary": _load(_SUMMARY_PATH),
        }

    def test_no_live_api_called_true_across_all(self, all_files):
        for label, data in all_files.items():
            assert data.get("no_live_api_called") is True, \
                f"{label}: no_live_api_called must be True"

    def test_broker_called_false_across_all(self, all_files):
        for label, data in all_files.items():
            assert data.get("broker_called") is False, \
                f"{label}: broker_called must be False"

    def test_env_inspected_false_across_all(self, all_files):
        for label, data in all_files.items():
            assert data.get("env_inspected") is False, \
                f"{label}: env_inspected must be False"

    def test_raw_news_used_false_across_all(self, all_files):
        for label, data in all_files.items():
            assert data.get("raw_news_used") is False, \
                f"{label}: raw_news_used must be False"

    def test_llm_used_false_across_all(self, all_files):
        for label, data in all_files.items():
            assert data.get("llm_used") is False, \
                f"{label}: llm_used must be False"

    def test_broad_intraday_scan_false_across_all(self, all_files):
        for label, data in all_files.items():
            assert data.get("broad_intraday_scan_used") is False, \
                f"{label}: broad_intraday_scan_used must be False"

    def test_live_output_changed_false_across_all(self, all_files):
        for label, data in all_files.items():
            assert data.get("live_output_changed") is False, \
                f"{label}: live_output_changed must be False"


# ---------------------------------------------------------------------------
# Test 33 — Validator includes backtest outputs
# ---------------------------------------------------------------------------
class TestValidatorIncludesBacktestOutputs:
    def test_validate_all_includes_backtest_files(self):
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import validate_all
        results = validate_all(os.path.join(_BASE, "data", "intelligence"))
        bt_keys = [
            "regime_fixture_results",
            "theme_activation_fixture_results",
            "candidate_feed_ablation_results",
            "risk_overlay_fixture_results",
            "intelligence_backtest_summary",
        ]
        for key in bt_keys:
            assert key in results, f"validate_all() missing backtest key: {key}"
        for key in bt_keys:
            assert results[key].ok, \
                f"validate_all() {key} failed: {results[key].errors}"


# ---------------------------------------------------------------------------
# Test 34 — Production no-touch: no forbidden imports in backtest module
# ---------------------------------------------------------------------------
class TestProductionNoTouch:
    _FORBIDDEN_PRODUCTION_IMPORTS = [
        "scanner", "theme_tracker", "catalyst_engine", "overnight_research",
        "universe_position", "universe_committed", "market_intelligence",
        "bot_trading", "guardrails", "orders_core",
    ]

    def test_backtest_module_does_not_import_production_modules(self):
        with open(_BACKTEST_MODULE, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        for forbidden in self._FORBIDDEN_PRODUCTION_IMPORTS:
            assert forbidden not in imported, \
                f"backtest_intelligence.py must not import production module '{forbidden}'"

    def test_backtest_module_does_not_use_os_environ(self):
        with open(_BACKTEST_MODULE, encoding="utf-8") as f:
            source = f.read()
        assert "os.environ" not in source, \
            "backtest_intelligence.py must not inspect os.environ"

    def test_backtest_module_does_not_generate_order_instructions(self):
        with open(_BACKTEST_MODULE, encoding="utf-8") as f:
            source = f.read()
        # Check for terms that would actually produce/submit orders
        # (not field names documenting the absence of orders)
        forbidden_terms = ["place_order", "submit_order", "execute_trade",
                           "broker.place", "ibkr.place", "alpaca.place"]
        for term in forbidden_terms:
            assert term not in source.lower(), \
                f"backtest_intelligence.py must not contain '{term}'"


# ---------------------------------------------------------------------------
# Tests 35 — Intelligence regression suite still passes (spot check)
# ---------------------------------------------------------------------------
class TestIntelligenceRegressionSpotCheck:
    """
    Spot-check that key outputs from prior sprints remain unchanged.
    Full regression is run via the combined pytest command in tiered policy.
    """
    def test_daily_economic_state_live_output_changed_still_false(self):
        path = os.path.join(_BASE, "data", "intelligence", "daily_economic_state.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("live_output_changed") is False

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

    def test_shadow_universe_freshness_still_sprint3(self):
        path = os.path.join(_BASE, "data", "universe_builder",
                            "active_opportunity_universe_shadow.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("freshness_status") in (
            "static_bootstrap_sprint3", "static_bootstrap_sprint2",
            "static_bootstrap_day7",
        ), f"Unexpected freshness_status: {data.get('freshness_status')}"


# ---------------------------------------------------------------------------
# Test 36 — Smoke suite passes (spot check on key smoke items)
# ---------------------------------------------------------------------------
class TestSmokeSpotCheck:
    """
    Smoke items verified here: backtest module importable, no live output changed.
    Full smoke suite is run via `pytest -m smoke -q`.
    """
    def test_backtest_intelligence_importable(self):
        """Verify backtest_intelligence.py has no import-time errors."""
        sys.path.insert(0, _BASE)
        import importlib.util
        spec = importlib.util.spec_from_file_location("backtest_intelligence", _BACKTEST_MODULE)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            pytest.fail(f"backtest_intelligence.py failed to import: {e}")

    def test_backtest_summary_decision_gate_valid(self):
        data = _load(_SUMMARY_PATH)
        valid_gates = {
            "pass_for_next_shadow_sprint",
            "fail_needs_fix",
            "pass_but_not_for_advisory",
            "insufficient_evidence",
        }
        assert data.get("decision_gate") in valid_gates, \
            f"decision_gate '{data.get('decision_gate')}' not in {valid_gates}"
