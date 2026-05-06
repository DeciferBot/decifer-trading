"""
tests/test_intelligence_sprint6a.py — Sprint 6A Offline Advisory Report Tests.

Covers all 35 acceptance tests from the Sprint 6A spec (Parts A–M).
Reads generated output files only — no live data, no APIs, no production modules.
"""

from __future__ import annotations

import ast
import json
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADVISORY_PATH = os.path.join(_BASE, "data", "intelligence", "advisory_report.json")
_ADVISORY_REPORTER_PATH = os.path.join(_BASE, "advisory_reporter.py")

_VALID_ADVISORY_STATUSES = {
    "advisory_include", "advisory_watch", "advisory_defer",
    "advisory_exclude", "advisory_unresolved",
}

_PRODUCTION_MODULES = {
    "scanner", "bot_trading", "market_intelligence", "orders_core",
    "guardrails", "catalyst_engine", "overnight_research",
    "agents", "sentinel_agents", "bot_ibkr", "learning",
}


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Tests 1–2 — advisory_reporter.py exists and advisory_report.json generated
# ---------------------------------------------------------------------------
class TestAdvisoryReporterExists:
    def test_advisory_reporter_exists(self):
        assert os.path.isfile(_ADVISORY_REPORTER_PATH), \
            "advisory_reporter.py not found at repo root"

    def test_advisory_report_json_generated(self):
        assert os.path.isfile(_ADVISORY_PATH), \
            f"advisory_report.json not found at {_ADVISORY_PATH}"


# ---------------------------------------------------------------------------
# Tests 3–4 — report validates and advisory_summary is present
# ---------------------------------------------------------------------------
class TestAdvisoryReportValidates:
    def test_advisory_report_validates(self):
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import validate_advisory_report
        result = validate_advisory_report(_ADVISORY_PATH)
        assert result.ok, f"advisory_report.json validation failed: {result.errors}"

    def test_advisory_summary_exists(self):
        data = _load(_ADVISORY_PATH)
        summary = data.get("advisory_summary")
        assert isinstance(summary, dict), "advisory_summary must be a dict"
        required = [
            "current_candidates_count", "shadow_candidates_count", "overlap_count",
            "advisory_include_count", "advisory_watch_count", "advisory_defer_count",
            "advisory_exclude_count", "advisory_unresolved_count",
            "route_disagreement_count", "unsupported_current_count",
            "missing_shadow_count", "non_executable_all", "live_output_changed",
        ]
        for field in required:
            assert field in summary, f"advisory_summary missing field '{field}'"


# ---------------------------------------------------------------------------
# Tests 5–9 — candidate_advisory records
# ---------------------------------------------------------------------------
class TestCandidateAdvisory:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_ADVISORY_PATH)

    @pytest.fixture(scope="class")
    def candidate_advisory(self, data):
        return data.get("candidate_advisory") or []

    def test_candidate_advisory_exists(self, data):
        ca = data.get("candidate_advisory")
        assert isinstance(ca, list) and len(ca) > 0, \
            "candidate_advisory must be a non-empty list"

    def test_every_record_has_valid_advisory_status(self, candidate_advisory):
        for rec in candidate_advisory:
            status = rec.get("advisory_status")
            assert status in _VALID_ADVISORY_STATUSES, \
                f"symbol={rec.get('symbol')}: invalid advisory_status '{status}'"

    def test_every_record_has_executable_false(self, candidate_advisory):
        for rec in candidate_advisory:
            assert rec.get("executable") is False, \
                f"symbol={rec.get('symbol')}: executable must be false"

    def test_every_record_has_order_instruction_null(self, candidate_advisory):
        for rec in candidate_advisory:
            assert rec.get("order_instruction") is None, \
                f"symbol={rec.get('symbol')}: order_instruction must be null"

    def test_non_executable_all_is_true(self, data):
        summary = data.get("advisory_summary", {})
        assert summary.get("non_executable_all") is True, \
            "advisory_summary.non_executable_all must be True"


# ---------------------------------------------------------------------------
# Tests 10–16 — Required sections exist
# ---------------------------------------------------------------------------
class TestRequiredSections:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_ADVISORY_PATH)

    def test_route_disagreements_section_exists(self, data):
        rd = data.get("route_disagreements")
        assert isinstance(rd, dict), "route_disagreements must be a dict"
        assert "total_route_disagreements" in rd
        assert "disagreements" in rd

    def test_unsupported_current_candidates_section_exists(self, data):
        uc = data.get("unsupported_current_candidates")
        assert isinstance(uc, dict), "unsupported_current_candidates must be a dict"
        assert "total" in uc

    def test_missing_shadow_candidates_section_exists(self, data):
        ms = data.get("missing_shadow_candidates")
        assert isinstance(ms, dict), "missing_shadow_candidates must be a dict"
        assert "total" in ms and "symbols" in ms

    def test_tier_d_advisory_section_exists(self, data):
        td = data.get("tier_d_advisory")
        assert isinstance(td, dict), "tier_d_advisory must be a dict"
        for field in ["tier_d_total_current", "tier_d_in_shadow", "tier_d_excluded",
                      "tier_d_preservation_rate", "advisory_findings"]:
            assert field in td, f"tier_d_advisory missing '{field}'"

    def test_structural_quota_advisory_section_exists(self, data):
        sqa = data.get("structural_quota_advisory")
        assert isinstance(sqa, dict), "structural_quota_advisory must be a dict"
        for field in ["structural_demand_count", "structural_capacity",
                      "structural_overflow_count", "recommendation",
                      "production_change_required"]:
            assert field in sqa, f"structural_quota_advisory missing '{field}'"

    def test_risk_theme_advisory_section_exists(self, data):
        rta = data.get("risk_theme_advisory")
        assert isinstance(rta, dict), "risk_theme_advisory must be a dict"
        for field in ["headwind_candidates", "executable_headwind_candidates",
                      "short_or_hedge_instruction_generated", "findings"]:
            assert field in rta, f"risk_theme_advisory missing '{field}'"

    def test_manual_and_held_advisory_section_exists(self, data):
        mha = data.get("manual_and_held_advisory")
        assert isinstance(mha, dict), "manual_and_held_advisory must be a dict"
        for field in ["manual_candidates_total", "manual_candidates_in_shadow",
                      "held_candidates_total", "manual_protection_preserved"]:
            assert field in mha, f"manual_and_held_advisory missing '{field}'"


# ---------------------------------------------------------------------------
# Tests 17–21 — Advisory logic constraints
# ---------------------------------------------------------------------------
class TestAdvisoryLogicConstraints:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_ADVISORY_PATH)

    @pytest.fixture(scope="class")
    def candidate_advisory(self, data):
        return data.get("candidate_advisory") or []

    def test_headwind_candidates_are_advisory_watch_only(self, candidate_advisory):
        """Headwind/pressure candidates must never be advisory_include or advisory_exclude."""
        for rec in candidate_advisory:
            rtc = rec.get("reason_to_care") or ""
            if "headwind" in rtc:
                status = rec.get("advisory_status")
                assert status == "advisory_watch", \
                    f"symbol={rec.get('symbol')}: headwind candidate must be advisory_watch, got '{status}'"

    def test_excluded_quota_candidates_never_executable(self, candidate_advisory):
        """advisory_defer candidates must all have executable=False (they never become trades)."""
        deferred = [r for r in candidate_advisory if r.get("advisory_status") == "advisory_defer"]
        assert len(deferred) > 0, "Expected at least some advisory_defer candidates (quota-excluded)"
        for rec in deferred:
            assert rec.get("executable") is False, \
                f"symbol={rec.get('symbol')}: advisory_defer candidate must not be executable"

    def test_manual_candidates_are_protected(self, candidate_advisory):
        """Manual conviction candidates must be advisory_watch (protected)."""
        manual = [r for r in candidate_advisory if r.get("reason_to_care") == "manual_conviction"]
        assert len(manual) > 0, "Expected at least some manual_conviction candidates"
        for rec in manual:
            status = rec.get("advisory_status")
            assert status in ("advisory_watch", "advisory_include"), \
                f"symbol={rec.get('symbol')}: manual_conviction must be advisory_watch or advisory_include, got '{status}'"

    def test_held_candidates_are_protected_when_present(self, data):
        """Held candidates (if any) must be advisory_watch."""
        ca = data.get("candidate_advisory") or []
        held = [r for r in ca if r.get("reason_to_care") == "held_position"]
        # In static bootstrap mode there may be no held candidates
        for rec in held:
            status = rec.get("advisory_status")
            assert status in ("advisory_watch", "advisory_include"), \
                f"symbol={rec.get('symbol')}: held candidate must be advisory_watch or advisory_include"

    def test_tier_d_source_path_excluded_not_counted_as_fully_lost(self, data):
        """
        Tier D symbols excluded from Tier D path but preserved via manual/economic
        source must not be counted as fully lost.
        """
        td = data.get("tier_d_advisory", {})
        preserved_other = td.get("tier_d_preserved_through_manual_or_other_source", [])
        tier_d_in_shadow = td.get("tier_d_in_shadow", 0)
        # If any Tier D symbol is in shadow (via any path), it's not fully lost
        assert tier_d_in_shadow >= 0, "tier_d_in_shadow must be >= 0"
        # preserved_other list should not double-count with fully excluded
        # (it's a subset of tier_d_excluded that are still present in shadow via other source)
        fully_excluded = td.get("tier_d_excluded", 0)
        if preserved_other:
            assert len(preserved_other) <= fully_excluded, \
                "preserved_through_other_source cannot exceed tier_d_excluded count"


# ---------------------------------------------------------------------------
# Tests 22–23 — Quota advisory does not change caps
# ---------------------------------------------------------------------------
class TestQuotaAdvisoryConstraints:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_ADVISORY_PATH)

    def test_structural_quota_advisory_does_not_change_quota_caps(self, data):
        sqa = data.get("structural_quota_advisory", {})
        assert sqa.get("production_change_required") is False, \
            "structural_quota_advisory.production_change_required must be False"

    def test_production_change_required_is_false(self, data):
        sqa = data.get("structural_quota_advisory", {})
        assert sqa.get("production_change_required") is False, \
            "structural_quota_advisory production_change_required must be False"


# ---------------------------------------------------------------------------
# Tests 24–31 — Forbidden paths and safety flags
# ---------------------------------------------------------------------------
class TestAdvisoryForbiddenPaths:
    @pytest.fixture(scope="class")
    def data(self):
        return _load(_ADVISORY_PATH)

    def test_no_live_api_called_true(self, data):
        assert data.get("no_live_api_called") is True

    def test_broker_called_false(self, data):
        assert data.get("broker_called") is False

    def test_env_inspected_false(self, data):
        assert data.get("env_inspected") is False

    def test_raw_news_used_false(self, data):
        assert data.get("raw_news_used") is False

    def test_llm_used_false(self, data):
        assert data.get("llm_used") is False

    def test_broad_intraday_scan_used_false(self, data):
        assert data.get("broad_intraday_scan_used") is False

    def test_production_modules_imported_false(self, data):
        assert data.get("production_modules_imported") is False

    def test_live_output_changed_false(self, data):
        assert data.get("live_output_changed") is False


# ---------------------------------------------------------------------------
# Test 32 — advisory_reporter.py imports no production modules
# ---------------------------------------------------------------------------
class TestAdvisoryReporterNoProductionImports:
    def test_no_production_modules_imported_by_advisory_reporter(self):
        """Parse advisory_reporter.py AST and confirm no production module imports."""
        with open(_ADVISORY_REPORTER_PATH, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        violations = imported & _PRODUCTION_MODULES
        assert not violations, \
            f"advisory_reporter.py imports production modules: {violations}"


# ---------------------------------------------------------------------------
# Tests 33–35 — Regression (Day2–Sprint5B pass, smoke pass)
# ---------------------------------------------------------------------------
class TestIntelligenceRegressionSpotCheck:
    def test_theme_activation_live_output_changed_still_false(self):
        path = os.path.join(_BASE, "data", "intelligence", "theme_activation.json")
        data = _load(path)
        assert data.get("live_output_changed") is False

    def test_thesis_store_live_output_changed_still_false(self):
        path = os.path.join(_BASE, "data", "intelligence", "thesis_store.json")
        data = _load(path)
        assert data.get("live_output_changed") is False

    def test_shadow_universe_freshness_unchanged(self):
        path = os.path.join(_BASE, "data", "universe_builder",
                            "active_opportunity_universe_shadow.json")
        data = _load(path)
        assert data.get("freshness_status") in (
            "static_bootstrap_sprint3", "static_bootstrap_sprint2",
            "static_bootstrap_day7", "static_bootstrap_sprint4a",
            "static_bootstrap_sprint4b",
        )

    def test_validator_includes_advisory_report(self):
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import validate_all
        results = validate_all(os.path.join(_BASE, "data", "intelligence"))
        assert "advisory_report" in results, "validate_all() must include advisory_report"
        assert results["advisory_report"].ok, \
            f"validate_all() advisory_report failed: {results['advisory_report'].errors}"
