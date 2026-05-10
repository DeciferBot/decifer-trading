"""
tests/test_intelligence_sprint7c.py — Sprint 7C: Paper Handoff Comparison

Covers:
  - paper_handoff_comparator.py exists
  - paper_handoff_comparison_report.json generated and validates
  - All required analysis sections present
  - SNDK, WDC, IREN are analysed and non-executable
  - All safety invariants hold
  - Production files not written
  - Regression: Sprint 7B and intelligence suite still pass
  - Smoke
"""
from __future__ import annotations

import ast
import json
import os
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPORT_PATH = os.path.join(_ROOT, "data", "live", "paper_handoff_comparison_report.json")
_COMPARATOR_PATH = os.path.join(_ROOT, "paper_handoff_comparator.py")
_MANIFEST_PATH = os.path.join(_ROOT, "data", "live", "paper_current_manifest.json")
_PRODUCTION_MANIFEST_PATH = os.path.join(_ROOT, "data", "live", "current_manifest.json")
_PRODUCTION_UNIVERSE_PATH = os.path.join(_ROOT, "data", "live", "active_opportunity_universe.json")
_GOVERNED_GAP_SYMBOLS = ("SNDK", "WDC", "IREN")

_SAFETY_FLAGS_MUST_BE_FALSE = [
    "production_candidate_source_changed",
    "apex_input_changed",
    "scanner_output_changed",
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

_VALID_RECOMMENDATIONS = {
    "continue_paper_comparison",
    "ready_for_controlled_handoff_design",
    "fix_paper_handoff_validation",
    "fix_coverage_or_quota_before_handoff",
    "insufficient_evidence",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_report() -> dict:
    with open(_REPORT_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Test 1 — comparator module exists
# ---------------------------------------------------------------------------

class TestModuleExistence:
    def test_paper_handoff_comparator_exists(self):
        assert os.path.isfile(_COMPARATOR_PATH), (
            "paper_handoff_comparator.py not found at repo root"
        )


# ---------------------------------------------------------------------------
# Test 2 — output file generated
# ---------------------------------------------------------------------------

class TestComparisonReportGenerated:
    def test_comparison_report_exists(self):
        assert os.path.isfile(_REPORT_PATH), (
            f"paper_handoff_comparison_report.json not found at {_REPORT_PATH}"
        )

    def test_comparison_report_is_valid_json(self):
        report = _load_report()
        assert isinstance(report, dict)

    def test_comparison_report_validates(self):
        from intelligence_schema_validator import validate_paper_handoff_comparison_report
        result = validate_paper_handoff_comparison_report(_REPORT_PATH)
        assert result.ok, f"Comparison report validation failed: {result.errors}"

    def test_comparison_report_mode(self):
        report = _load_report()
        assert report.get("mode") == "paper_handoff_comparison"


# ---------------------------------------------------------------------------
# Tests 4–10 — required analysis sections
# ---------------------------------------------------------------------------

class TestAnalysisSectionsExist:
    def test_overlap_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("overlap_analysis"), dict)

    def test_drop_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("drop_analysis"), list)

    def test_addition_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("addition_analysis"), list)

    def test_route_disagreement_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("route_disagreement_analysis"), dict)

    def test_quota_pressure_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("quota_pressure_analysis"), dict)

    def test_coverage_gap_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("coverage_gap_analysis"), dict)

    def test_approved_gap_symbol_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("approved_gap_symbol_analysis"), dict)


# ---------------------------------------------------------------------------
# Tests 11–15 — SNDK, WDC, IREN are analysed and non-executable
# ---------------------------------------------------------------------------

class TestGovernedGapSymbolAnalysis:
    def test_sndk_is_analysed(self):
        report = _load_report()
        aga = report.get("approved_gap_symbol_analysis", {})
        assert "SNDK" in aga, "SNDK not found in approved_gap_symbol_analysis"

    def test_wdc_is_analysed(self):
        report = _load_report()
        aga = report.get("approved_gap_symbol_analysis", {})
        assert "WDC" in aga, "WDC not found in approved_gap_symbol_analysis"

    def test_iren_is_analysed(self):
        report = _load_report()
        aga = report.get("approved_gap_symbol_analysis", {})
        assert "IREN" in aga, "IREN not found in approved_gap_symbol_analysis"

    def test_sndk_executable_false(self):
        report = _load_report()
        assert report["approved_gap_symbol_analysis"]["SNDK"]["executable"] is False

    def test_wdc_executable_false(self):
        report = _load_report()
        assert report["approved_gap_symbol_analysis"]["WDC"]["executable"] is False

    def test_iren_executable_false(self):
        report = _load_report()
        assert report["approved_gap_symbol_analysis"]["IREN"]["executable"] is False

    def test_sndk_order_instruction_null(self):
        report = _load_report()
        assert report["approved_gap_symbol_analysis"]["SNDK"].get("order_instruction") is None

    def test_wdc_order_instruction_null(self):
        report = _load_report()
        assert report["approved_gap_symbol_analysis"]["WDC"].get("order_instruction") is None

    def test_iren_order_instruction_null(self):
        report = _load_report()
        assert report["approved_gap_symbol_analysis"]["IREN"].get("order_instruction") is None

    def test_governed_symbols_in_thematic_roster(self):
        report = _load_report()
        aga = report["approved_gap_symbol_analysis"]
        for sym in _GOVERNED_GAP_SYMBOLS:
            assert aga[sym].get("in_thematic_roster") is True, (
                f"{sym} expected in_thematic_roster=True"
            )

    def test_governed_symbols_in_transmission_rules(self):
        report = _load_report()
        aga = report["approved_gap_symbol_analysis"]
        for sym in _GOVERNED_GAP_SYMBOLS:
            assert aga[sym].get("in_transmission_rules") is True, (
                f"{sym} expected in_transmission_rules=True"
            )

    def test_governed_symbols_excluded_due_quota(self):
        # SNDK/WDC/IREN are governed but excluded by structural quota — this is expected
        report = _load_report()
        aga = report["approved_gap_symbol_analysis"]
        for sym in _GOVERNED_GAP_SYMBOLS:
            assert aga[sym].get("excluded_due_quota") is True, (
                f"{sym} expected excluded_due_quota=True (structural quota full)"
            )


# ---------------------------------------------------------------------------
# Tests 16–29 — safety invariants
# ---------------------------------------------------------------------------

class TestSafetyInvariants:
    def test_paper_manifest_handoff_enabled_false(self):
        manifest_path = _MANIFEST_PATH
        if not os.path.isfile(manifest_path):
            pytest.skip("paper manifest not present")
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest.get("handoff_enabled") is False

    def test_production_candidate_source_changed_false(self):
        report = _load_report()
        assert report.get("production_candidate_source_changed") is False

    def test_apex_input_changed_false(self):
        report = _load_report()
        assert report.get("apex_input_changed") is False

    def test_scanner_output_changed_false(self):
        report = _load_report()
        assert report.get("scanner_output_changed") is False

    def test_risk_logic_changed_false(self):
        report = _load_report()
        assert report.get("risk_logic_changed") is False

    def test_order_logic_changed_false(self):
        report = _load_report()
        assert report.get("order_logic_changed") is False

    def test_broker_called_false(self):
        report = _load_report()
        assert report.get("broker_called") is False

    def test_llm_called_false(self):
        report = _load_report()
        assert report.get("llm_called") is False

    def test_raw_news_used_false(self):
        report = _load_report()
        assert report.get("raw_news_used") is False

    def test_broad_intraday_scan_used_false(self):
        report = _load_report()
        assert report.get("broad_intraday_scan_used") is False

    def test_live_output_changed_false(self):
        report = _load_report()
        assert report.get("live_output_changed") is False

    def test_no_production_manifest_written(self):
        # Sprint 7C: these files were not written by Sprint 7C.
        # Sprint 7F (handoff_publisher.py) now writes them with handoff_enabled=false.
        # If they exist, verify safe content rather than asserting absence.
        if os.path.isfile(_PRODUCTION_MANIFEST_PATH):
            import json as _json
            data = _json.load(open(_PRODUCTION_MANIFEST_PATH))
            assert data.get("handoff_enabled") is False, (
                "current_manifest.json exists but handoff_enabled must be false"
            )
            assert data.get("publication_mode") == "validation_only", (
                "current_manifest.json exists but publication_mode must be validation_only"
            )

    def test_no_production_active_universe_written(self):
        # Sprint 7C: these files were not written by Sprint 7C.
        # Sprint 7F (handoff_publisher.py) now writes them with publication_mode=validation_only.
        # If they exist, verify safe content rather than asserting absence.
        if os.path.isfile(_PRODUCTION_UNIVERSE_PATH):
            import json as _json
            data = _json.load(open(_PRODUCTION_UNIVERSE_PATH))
            assert data.get("publication_mode") == "validation_only", (
                "active_opportunity_universe.json exists but publication_mode must be validation_only"
            )
            for c in data.get("candidates", []):
                assert c.get("executable") is not True, (
                    f"candidate {c.get('symbol')} has executable=true"
                )

    def test_safety_analysis_exists(self):
        report = _load_report()
        assert isinstance(report.get("safety_analysis"), dict)

    def test_all_safety_invariants_hold(self):
        report = _load_report()
        sa = report.get("safety_analysis", {})
        assert sa.get("all_safety_invariants_hold") is True, (
            f"all_safety_invariants_hold is False: {sa}"
        )


# ---------------------------------------------------------------------------
# Test 30 — recommendation is valid
# ---------------------------------------------------------------------------

class TestRecommendation:
    def test_recommendation_is_valid(self):
        report = _load_report()
        rec = report.get("recommendation")
        assert rec in _VALID_RECOMMENDATIONS, (
            f"recommendation {rec!r} is not a valid value"
        )


# ---------------------------------------------------------------------------
# Test 31 — Sprint 7B regression
# ---------------------------------------------------------------------------

class TestSprint7BRegression:
    def test_paper_handoff_validation_report_still_valid(self):
        from intelligence_schema_validator import validate_paper_handoff_validation_report
        path = os.path.join(_ROOT, "data", "live", "paper_handoff_validation_report.json")
        if not os.path.isfile(path):
            pytest.skip("paper_handoff_validation_report.json not present")
        result = validate_paper_handoff_validation_report(path)
        assert result.ok, f"Sprint 7B paper validation report failed: {result.errors}"

    def test_paper_manifest_still_valid(self):
        from intelligence_schema_validator import validate_paper_manifest
        path = _MANIFEST_PATH
        if not os.path.isfile(path):
            pytest.skip("paper_current_manifest.json not present")
        result = validate_paper_manifest(path)
        assert result.ok, f"Sprint 7B paper manifest validation failed: {result.errors}"

    def test_paper_active_universe_still_valid(self):
        from intelligence_schema_validator import validate_paper_active_universe
        path = os.path.join(_ROOT, "data", "live", "paper_active_opportunity_universe.json")
        if not os.path.isfile(path):
            pytest.skip("paper_active_opportunity_universe.json not present")
        result = validate_paper_active_universe(path)
        assert result.ok, f"Sprint 7B paper universe validation failed: {result.errors}"


# ---------------------------------------------------------------------------
# Test 32 — intelligence regression spot check
# ---------------------------------------------------------------------------

class TestIntelligenceRegressionSpotCheck:
    def test_transmission_rules_still_valid(self):
        from intelligence_schema_validator import validate_transmission_rules
        path = os.path.join(_ROOT, "data", "intelligence", "transmission_rules.json")
        result = validate_transmission_rules(path)
        assert result.ok, f"Transmission rules regression: {result.errors}"

    def test_shadow_universe_still_valid(self):
        from intelligence_schema_validator import validate_shadow_universe
        path = os.path.join(
            _ROOT, "data", "universe_builder", "active_opportunity_universe_shadow.json"
        )
        if not os.path.isfile(path):
            pytest.skip("shadow universe not present")
        result = validate_shadow_universe(path)
        assert result.ok, f"Shadow universe regression: {result.errors}"

    def test_advisory_report_still_valid(self):
        from intelligence_schema_validator import validate_advisory_report
        path = os.path.join(_ROOT, "data", "intelligence", "advisory_report.json")
        if not os.path.isfile(path):
            pytest.skip("advisory report not present")
        result = validate_advisory_report(path)
        assert result.ok, f"Advisory report regression: {result.errors}"

    def test_coverage_gap_still_valid(self):
        from intelligence_schema_validator import validate_coverage_gap_review
        path = os.path.join(_ROOT, "data", "intelligence", "coverage_gap_review.json")
        if not os.path.isfile(path):
            pytest.skip("coverage_gap_review not present")
        result = validate_coverage_gap_review(path)
        assert result.ok, f"Coverage gap regression: {result.errors}"


# ---------------------------------------------------------------------------
# Test 33 — smoke
# ---------------------------------------------------------------------------

class TestSmokeSpotCheck:
    @pytest.mark.smoke
    def test_smoke_passes_for_7c(self):
        report = _load_report()
        assert report.get("mode") == "paper_handoff_comparison"
        assert report.get("live_output_changed") is False
        assert isinstance(report.get("approved_gap_symbol_analysis"), dict)
        for sym in _GOVERNED_GAP_SYMBOLS:
            assert sym in report["approved_gap_symbol_analysis"]
        assert report.get("recommendation") in _VALID_RECOMMENDATIONS
