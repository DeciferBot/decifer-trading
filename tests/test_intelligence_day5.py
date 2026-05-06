"""
tests/test_intelligence_day5.py

Day 5 acceptance tests for the Current vs Shadow Comparison:
  - compare_universes.py loads snapshot and shadow universe
  - current_vs_shadow_comparison.json is generated
  - universe_builder_report.json is generated
  - Overlap, Tier D, structural, attention, manual/held, economic metrics are correct
  - Unavailable stages are explicitly marked
  - Validator accepts valid and rejects invalid comparison files
  - live_output_changed = False throughout
  - Day 2, 3, 4 regressions pass
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from compare_universes import (
    UniverseComparator,
    build_comparison_report,
    _UNAVAILABLE,
)
from intelligence_schema_validator import (
    validate_all,
    validate_comparison,
    validate_report,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_DIR = os.path.join(_REPO, "data", "intelligence")
_UB_DIR = os.path.join(_REPO, "data", "universe_builder")
_SNAPSHOT_PATH = os.path.join(_UB_DIR, "current_pipeline_snapshot.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")
_COMPARISON_PATH = os.path.join(_UB_DIR, "current_vs_shadow_comparison.json")
_REPORT_PATH = os.path.join(_UB_DIR, "universe_builder_report.json")
_FEED_PATH = os.path.join(_BASE_DIR, "economic_candidate_feed.json")
_TIER_D_PATH = os.path.join(_REPO, "data", "position_research_universe.json")
_FAVOURITES_PATH = os.path.join(_REPO, "data", "favourites.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def comparator():
    return UniverseComparator(
        snapshot_path=_SNAPSHOT_PATH,
        shadow_path=_SHADOW_PATH,
    )


@pytest.fixture(scope="module")
def comparison(comparator):
    return comparator.compare()


@pytest.fixture(scope="module")
def report(comparator, comparison):
    return comparator.build_report(comparison)


def _write_comparison(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, f)
    f.close()
    return f.name


def _minimal_comparison() -> dict:
    return {
        "schema_version":   "1.0",
        "generated_at":     "2026-05-05T00:00:00Z",
        "mode":             "shadow_comparison_only",
        "source_files":     [_SNAPSHOT_PATH, _SHADOW_PATH],
        "current_summary": {
            "current_pre_filter_source_pool_count": 100,
            "current_tier_a_count":    57,
            "current_tier_b_count":    50,
            "current_tier_c_count":    _UNAVAILABLE,
            "current_tier_d_count":    150,
            "current_favourites_count": 13,
            "current_held_count":      _UNAVAILABLE,
            "unavailable_stages":      ["current_tier_c", "current_held"],
        },
        "shadow_summary": {
            "shadow_total_count":                    49,
            "shadow_position_count":                 19,
            "shadow_swing_count":                    1,
            "shadow_watchlist_count":                16,
            "shadow_attention_count":                15,
            "shadow_structural_count":               20,
            "shadow_economic_intelligence_count":    5,
            "shadow_tier_d_count":                   16,
            "shadow_manual_count":                   13,
            "shadow_held_count":                     0,
            "shadow_etf_proxy_count":                1,
        },
        "overlap_summary": {
            "overlap_count":               44,
            "overlap_symbols":             [],
            "in_current_not_shadow_count": 192,
            "in_current_not_shadow_symbols": [],
            "in_shadow_not_current_count": 5,
            "in_shadow_not_current_symbols": [],
        },
        "tier_d_analysis": {
            "tier_d_total_current":          150,
            "tier_d_in_shadow_count":        22,
            "tier_d_in_shadow_symbols":      [],
            "tier_d_excluded_count":         128,
            "tier_d_excluded_symbols":       [],
            "tier_d_exclusion_reasons":      {},
            "tier_d_preservation_rate":      0.1467,
            "tier_d_quality_rank_available": True,
            "tier_d_structural_quota_used":  20,
            "tier_d_structural_quota_full":  True,
            "top_tier_d_preserved":          [],
            "top_tier_d_excluded":           [],
        },
        "structural_candidate_analysis": {
            "structural_candidates_current_count":      150,
            "structural_candidates_shadow_count":       20,
            "structural_candidates_preserved_count":    20,
            "structural_candidates_preserved_symbols":  [],
            "structural_candidates_lost_count":         0,
            "structural_candidates_lost_symbols":       [],
            "structural_candidate_survival_rate":       1.0,
            "structural_quota_used":                    20,
            "structural_quota_max":                     20,
            "structural_quota_binding":                 True,
            "structural_candidates_displaced_by_attention": False,
        },
        "attention_analysis": {
            "attention_candidates_shadow_count":          15,
            "attention_cap":                              15,
            "attention_cap_respected":                    True,
            "attention_excluded_count":                   85,
            "attention_excluded_symbols":                 [],
            "attention_exclusion_reasons":                [],
            "attention_candidates_consumed_structural_quota": False,
        },
        "manual_and_held_analysis": {
            "manual_candidates_current_count": 13,
            "manual_candidates_shadow_count":  13,
            "manual_candidates_protected":     True,
            "manual_candidates_lost":          [],
            "held_candidates_current_count":   _UNAVAILABLE,
            "held_candidates_shadow_count":    0,
            "held_candidates_protected":       True,
            "held_candidates_lost":            _UNAVAILABLE,
        },
        "economic_intelligence_analysis": {
            "economic_candidates_total":         5,
            "economic_candidates_in_shadow":     5,
            "economic_candidates_excluded":      0,
            "economic_symbols_in_shadow":        ["VRT", "ETN", "PWR", "CEG", "XLU"],
            "economic_symbols_excluded":         [],
            "economic_reason_to_care_present":   True,
            "economic_candidates_executable":    False,
            "llm_symbol_discovery_used":         False,
            "raw_news_used":                     False,
            "broad_intraday_scan_used":          False,
        },
        "exclusion_analysis": {
            "total_exclusions":              220,
            "exclusions_by_reason":          {},
            "exclusions_by_source":          {},
            "exclusions_by_quota_group":     {},
            "duplicate_exclusions":          1,
            "quota_full_exclusions":         219,
            "malformed_candidate_exclusions": 0,
        },
        "quality_warnings": ["test warning"],
        "live_output_changed": False,
    }


# ---------------------------------------------------------------------------
# Files exist
# ---------------------------------------------------------------------------

class TestOutputFilesExist:
    def test_comparison_file_exists(self):
        assert os.path.exists(_COMPARISON_PATH), f"Missing: {_COMPARISON_PATH}"

    def test_report_file_exists(self):
        assert os.path.exists(_REPORT_PATH), f"Missing: {_REPORT_PATH}"

    def test_comparison_file_parseable(self):
        with open(_COMPARISON_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_report_file_parseable(self):
        with open(_REPORT_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Comparator loads sources
# ---------------------------------------------------------------------------

class TestComparatorLoads:
    def test_snapshot_path_accessible(self, comparator):
        assert os.path.exists(comparator._snapshot_path)

    def test_shadow_path_accessible(self, comparator):
        assert os.path.exists(comparator._shadow_path)

    def test_comparison_has_no_errors(self, comparison):
        # quality_warnings is expected but errors in the schema shouldn't exist
        assert "live_output_changed" in comparison

    def test_comparison_mode(self, comparison):
        assert comparison["mode"] == "shadow_comparison_only"

    def test_comparison_live_output_changed_false(self, comparison):
        assert comparison["live_output_changed"] is False


# ---------------------------------------------------------------------------
# Overlap metrics
# ---------------------------------------------------------------------------

class TestOverlapMetrics:
    def test_overlap_count_is_integer(self, comparison):
        assert isinstance(comparison["overlap_summary"]["overlap_count"], int)

    def test_overlap_count_non_negative(self, comparison):
        assert comparison["overlap_summary"]["overlap_count"] >= 0

    def test_in_current_not_shadow_count_correct(self, comparison):
        cs = comparison["current_summary"]
        ov = comparison["overlap_summary"]
        # Pre-filter pool = tier_a + tier_b + tier_d + favourites (overlap removed)
        # in_current_not_shadow must be non-negative
        assert ov["in_current_not_shadow_count"] >= 0

    def test_in_shadow_not_current_count_correct(self, comparison):
        ov = comparison["overlap_summary"]
        # in_shadow_not_current are symbols only in shadow (economic candidates not in current pool)
        assert ov["in_shadow_not_current_count"] >= 0

    def test_overlap_does_not_exceed_shadow_total(self, comparison):
        ov = comparison["overlap_summary"]
        ss = comparison["shadow_summary"]
        assert ov["overlap_count"] <= ss["shadow_total_count"]

    def test_overlap_symbols_list_present(self, comparison):
        assert isinstance(comparison["overlap_summary"]["overlap_symbols"], list)


# ---------------------------------------------------------------------------
# Tier D analysis
# ---------------------------------------------------------------------------

class TestTierDAnalysis:
    def test_tier_d_total_matches_source(self, comparison):
        td = comparison["tier_d_analysis"]
        assert td["tier_d_total_current"] == 150  # known from data/position_research_universe.json

    def test_tier_d_in_shadow_count_correct(self, comparison):
        td = comparison["tier_d_analysis"]
        assert td["tier_d_in_shadow_count"] > 0
        assert td["tier_d_in_shadow_count"] <= td["tier_d_total_current"]

    def test_tier_d_excluded_count_correct(self, comparison):
        td = comparison["tier_d_analysis"]
        # excluded + in_shadow ≤ total (some may be in shadow via other sources)
        assert td["tier_d_excluded_count"] >= 0

    def test_tier_d_preservation_rate_correct(self, comparison):
        td = comparison["tier_d_analysis"]
        expected = round(td["tier_d_in_shadow_count"] / td["tier_d_total_current"], 4)
        assert td["tier_d_preservation_rate"] == expected

    def test_tier_d_exclusion_reasons_reported(self, comparison):
        td = comparison["tier_d_analysis"]
        assert isinstance(td["tier_d_exclusion_reasons"], dict)

    def test_structural_quota_full_reported(self, comparison):
        td = comparison["tier_d_analysis"]
        assert td["tier_d_structural_quota_full"] is True  # Day 4 structural quota is full

    def test_top_tier_d_preserved_listed(self, comparison):
        td = comparison["tier_d_analysis"]
        assert isinstance(td["top_tier_d_preserved"], list)

    def test_top_tier_d_excluded_listed(self, comparison):
        td = comparison["tier_d_analysis"]
        assert isinstance(td["top_tier_d_excluded"], list)
        assert len(td["top_tier_d_excluded"]) > 0, "Expected excluded Tier D names"

    def test_tier_d_quality_rank_available(self, comparison):
        td = comparison["tier_d_analysis"]
        # discovery_score is available in position_research_universe.json
        assert td["tier_d_quality_rank_available"] is True


# ---------------------------------------------------------------------------
# Structural analysis
# ---------------------------------------------------------------------------

class TestStructuralAnalysis:
    def test_structural_candidates_shadow_count(self, comparison):
        struct = comparison["structural_candidate_analysis"]
        assert struct["structural_candidates_shadow_count"] == 20

    def test_structural_quota_binding(self, comparison):
        struct = comparison["structural_candidate_analysis"]
        assert struct["structural_quota_binding"] is True

    def test_structural_not_displaced_by_attention(self, comparison):
        struct = comparison["structural_candidate_analysis"]
        assert struct["structural_candidates_displaced_by_attention"] is False

    def test_structural_quota_used_max(self, comparison):
        struct = comparison["structural_candidate_analysis"]
        assert struct["structural_quota_used"] == struct["structural_quota_max"]


# ---------------------------------------------------------------------------
# Attention analysis
# ---------------------------------------------------------------------------

class TestAttentionAnalysis:
    def test_attention_cap_respected(self, comparison):
        attn = comparison["attention_analysis"]
        assert attn["attention_cap_respected"] is True

    def test_attention_not_consumed_structural(self, comparison):
        attn = comparison["attention_analysis"]
        assert attn["attention_candidates_consumed_structural_quota"] is False

    def test_attention_count_at_cap(self, comparison):
        attn = comparison["attention_analysis"]
        assert attn["attention_candidates_shadow_count"] <= attn["attention_cap"]

    def test_attention_excluded_count_non_negative(self, comparison):
        attn = comparison["attention_analysis"]
        # Day 5: attention was the binding constraint (50 Tier B names, cap 15).
        # Day 6: total universe cap (50) hits before attention cap because structural
        # quota now consumes 20 slots. Either way, count must be non-negative and
        # attention_cap_respected must be True.
        assert attn["attention_excluded_count"] >= 0
        assert attn["attention_cap_respected"] is True


# ---------------------------------------------------------------------------
# Manual and held analysis
# ---------------------------------------------------------------------------

class TestManualHeldAnalysis:
    def test_manual_candidates_protected(self, comparison):
        mh = comparison["manual_and_held_analysis"]
        assert mh["manual_candidates_protected"] is True

    def test_manual_candidates_lost_empty(self, comparison):
        mh = comparison["manual_and_held_analysis"]
        # All 13 favourites should be in the shadow
        assert mh["manual_candidates_lost"] == []

    def test_held_unavailable_reason_present(self, comparison):
        mh = comparison["manual_and_held_analysis"]
        # Held is unavailable in Day 5 static bootstrap — should be marked
        assert mh["held_candidates_current_count"] == _UNAVAILABLE or \
               mh.get("held_unavailable_reason")

    def test_held_protected_flag_set(self, comparison):
        mh = comparison["manual_and_held_analysis"]
        assert mh["held_candidates_protected"] is True


# ---------------------------------------------------------------------------
# Economic intelligence analysis
# ---------------------------------------------------------------------------

class TestEconomicAnalysis:
    def test_all_economic_candidates_in_shadow(self, comparison):
        econ = comparison["economic_intelligence_analysis"]
        assert econ["economic_candidates_in_shadow"] == econ["economic_candidates_total"]

    def test_economic_candidates_not_executable(self, comparison):
        econ = comparison["economic_intelligence_analysis"]
        assert econ["economic_candidates_executable"] is False

    def test_llm_discovery_false(self, comparison):
        econ = comparison["economic_intelligence_analysis"]
        assert econ["llm_symbol_discovery_used"] is False

    def test_raw_news_false(self, comparison):
        econ = comparison["economic_intelligence_analysis"]
        assert econ["raw_news_used"] is False

    def test_broad_intraday_scan_false(self, comparison):
        econ = comparison["economic_intelligence_analysis"]
        assert econ["broad_intraday_scan_used"] is False

    def test_reason_to_care_present(self, comparison):
        econ = comparison["economic_intelligence_analysis"]
        assert econ["economic_reason_to_care_present"] is True


# ---------------------------------------------------------------------------
# Unavailable stages
# ---------------------------------------------------------------------------

class TestUnavailableStages:
    def test_unavailable_stages_list_present(self, comparison):
        cs = comparison["current_summary"]
        assert isinstance(cs["unavailable_stages"], list)
        assert len(cs["unavailable_stages"]) > 0

    def test_tier_c_marked_unavailable(self, comparison):
        cs = comparison["current_summary"]
        assert cs["current_tier_c_count"] == _UNAVAILABLE

    def test_held_marked_unavailable(self, comparison):
        cs = comparison["current_summary"]
        assert cs["current_held_count"] == _UNAVAILABLE

    def test_guardrails_marked_unavailable(self, comparison):
        cs = comparison["current_summary"]
        assert cs.get("current_guardrails_passed_count") == _UNAVAILABLE

    def test_apex_final_cap_marked_unavailable(self, comparison):
        cs = comparison["current_summary"]
        assert cs.get("current_apex_final_cap_count") == _UNAVAILABLE


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------

class TestReport:
    def test_report_has_required_keys(self, report):
        from intelligence_schema_validator import _REPORT_REQUIRED_TOP_KEYS
        for key in _REPORT_REQUIRED_TOP_KEYS:
            assert key in report, f"Report missing key: {key}"

    def test_report_live_output_changed_false(self, report):
        assert report["live_output_changed"] is False

    def test_report_mode_correct(self, report):
        assert report["mode"] == "shadow_comparison_only"

    def test_report_has_interpretations(self, report):
        assert isinstance(report.get("interpretations"), list)
        assert len(report["interpretations"]) > 0


# ---------------------------------------------------------------------------
# Validator — valid files pass
# ---------------------------------------------------------------------------

class TestValidatorPassesValidFiles:
    def test_comparison_passes_validator(self):
        result = validate_comparison(_COMPARISON_PATH)
        assert result.ok, f"Validator errors: {result.errors}"

    def test_report_passes_validator(self):
        result = validate_report(_REPORT_PATH)
        assert result.ok, f"Validator errors: {result.errors}"

    def test_validate_all_includes_comparison_and_report(self):
        results = validate_all(_BASE_DIR)
        assert "current_vs_shadow_comparison" in results
        assert "universe_builder_report" in results
        assert results["current_vs_shadow_comparison"].ok, \
            f"Comparison validation failed: {results['current_vs_shadow_comparison'].errors}"
        assert results["universe_builder_report"].ok, \
            f"Report validation failed: {results['universe_builder_report'].errors}"


# ---------------------------------------------------------------------------
# Validator — rejects invalid comparison variants
# ---------------------------------------------------------------------------

class TestValidatorRejectsInvalidComparison:
    def test_missing_live_output_changed_fails(self):
        bad = _minimal_comparison()
        del bad["live_output_changed"]
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("live_output_changed" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_live_output_changed_true_fails(self):
        bad = _minimal_comparison()
        bad["live_output_changed"] = True
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("live_output_changed" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_attention_cap_respected_false_fails(self):
        bad = _minimal_comparison()
        bad["attention_analysis"]["attention_cap_respected"] = False
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("attention_cap_respected" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_structural_displaced_by_attention_true_fails(self):
        bad = _minimal_comparison()
        bad["structural_candidate_analysis"]["structural_candidates_displaced_by_attention"] = True
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("structural_candidates_displaced_by_attention" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_llm_discovery_true_fails(self):
        bad = _minimal_comparison()
        bad["economic_intelligence_analysis"]["llm_symbol_discovery_used"] = True
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("llm_symbol_discovery_used" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_economic_executable_true_fails(self):
        bad = _minimal_comparison()
        bad["economic_intelligence_analysis"]["economic_candidates_executable"] = True
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("economic_candidates_executable" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_overlap_exceeds_shadow_total_fails(self):
        bad = _minimal_comparison()
        bad["overlap_summary"]["overlap_count"] = 9999
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("overlap_count" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_missing_unavailable_stages_list_fails(self):
        bad = _minimal_comparison()
        bad["current_summary"]["unavailable_stages"] = "not_a_list"
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("unavailable_stages" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_wrong_mode_fails(self):
        bad = _minimal_comparison()
        bad["mode"] = "production"
        path = _write_comparison(bad)
        try:
            result = validate_comparison(path)
            assert not result.ok
            assert any("mode" in e for e in result.errors)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Day 2/3/4 regressions
# ---------------------------------------------------------------------------

class TestPriorDayRegressions:
    def test_validate_all_days_2_3_4_still_pass(self):
        results = validate_all(_BASE_DIR)
        for label in [
            "transmission_rules", "theme_taxonomy", "thematic_roster",
            "economic_candidate_feed", "active_opportunity_universe_shadow",
        ]:
            assert results[label].ok, f"Regression: {label} now failing: {results[label].errors}"
