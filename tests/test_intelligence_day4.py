"""
tests/test_intelligence_day4.py

Day 4 acceptance tests for the Shadow Universe Builder:
  - universe_builder.py loads economic_candidate_feed.json and current sources
  - active_opportunity_universe_shadow.json is generated with correct structure
  - Route/quota logic is correct
  - Structural candidates are protected
  - Attention/ETF proxy caps are enforced
  - Held/manual conviction protection logic exists
  - Every inclusion and exclusion has a reason
  - No candidate is executable
  - Governance flags: live_output_changed = False, no LLM, no raw news, no intraday scan
  - Validator accepts valid and rejects invalid shadow universe
  - Day 2 and Day 3 regression smoke
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from universe_builder import (
    UniverseBuilder,
    ShadowUniverse,
    ShadowCandidate,
    build_shadow_universe,
    _from_economic_candidate,
    _from_favourite,
    _from_tier_d,
    _from_tier_b,
    _ATTENTION_MAX,
    _ETF_PROXY_MAX,
    _STRUCTURAL_MAX,
    _TOTAL_MAX,
)
from intelligence_schema_validator import (
    validate_all,
    validate_shadow_universe,
)

_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "intelligence")
_FEED_PATH = os.path.join(_BASE_DIR, "economic_candidate_feed.json")
_SHADOW_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "universe_builder", "active_opportunity_universe_shadow.json"
)
_SNAPSHOT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "universe_builder", "current_pipeline_snapshot.json"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def universe():
    """Build the shadow universe once for the test module."""
    builder = UniverseBuilder(
        feed_path=_FEED_PATH,
        output_path=_SHADOW_PATH,
        snapshot_path=_SNAPSHOT_PATH,
    )
    return builder.build()


@pytest.fixture()
def ec_direct():
    return {
        "symbol": "VRT",
        "role": "direct_beneficiary",
        "route_hint": ["position", "swing", "watchlist"],
        "theme": "data_centre_power",
        "reason_to_care": "VRT is a direct beneficiary of data-centre power",
        "source_labels": ["intelligence_first_static_rule", "economic_intelligence"],
        "transmission_rules_fired": ["ai_capex_growth_to_data_centre_power"],
        "risk_flags": ["valuation", "crowding"],
        "confirmation_required": ["sector_etf_relative_strength"],
    }


@pytest.fixture()
def ec_second_order():
    return {
        "symbol": "CEG",
        "role": "second_order_beneficiary",
        "route_hint": ["swing", "watchlist"],
        "theme": "data_centre_power",
        "reason_to_care": "CEG is a second-order beneficiary",
        "source_labels": ["intelligence_first_static_rule", "economic_intelligence"],
        "transmission_rules_fired": ["ai_capex_growth_to_data_centre_power"],
        "risk_flags": ["valuation"],
        "confirmation_required": ["sector_etf_relative_strength"],
    }


@pytest.fixture()
def ec_etf_proxy():
    return {
        "symbol": "XLU",
        "role": "etf_proxy",
        "route_hint": ["watchlist"],
        "theme": "data_centre_power",
        "reason_to_care": "XLU is an ETF proxy",
        "source_labels": ["intelligence_first_static_rule", "economic_intelligence"],
        "transmission_rules_fired": ["ai_capex_growth_to_data_centre_power"],
        "risk_flags": [],
        "confirmation_required": [],
    }


def _minimal_shadow_candidate(symbol: str = "VRT") -> dict:
    return {
        "symbol":                    symbol,
        "company_name":              None,
        "asset_type":                "equity",
        "reason_to_care":            "structural",
        "bucket_id":                 f"data_centre_power_direct_beneficiary",
        "bucket_type":               "structural",
        "route":                     "position",
        "source_labels":             ["economic_intelligence"],
        "macro_rules_fired":         ["ai_capex_growth_to_data_centre_power"],
        "transmission_direction":    "tailwind",
        "company_validation_status": "not_run_static_bootstrap",
        "thesis_intact":             None,
        "why_this_symbol":           "test reason",
        "invalidation":              ["valuation"],
        "eligibility":               {"status": "shadow_only_unknown"},
        "quota":                     {"group": "structural_position", "protected": True},
        "execution_instructions":    {"executable": False, "allowed_routes_when_live": ["position"]},
        "risk_notes":                ["valuation"],
        "live_output_changed":       False,
    }


def _minimal_shadow_universe(candidates: list | None = None) -> dict:
    if candidates is None:
        candidates = [_minimal_shadow_candidate()]
    return {
        "schema_version":    "1.0",
        "generated_at":      "2026-05-05T00:00:00Z",
        "valid_for_session": "2026-05-05",
        "freshness_status":  "static_bootstrap_day4",
        "mode":              "shadow_only",
        "source_files":      [_FEED_PATH],
        "universe_summary": {
            "total_candidates":                len(candidates),
            "position_candidates":             1,
            "swing_candidates":                0,
            "intraday_swing_candidates":       0,
            "watchlist_candidates":            0,
            "held_candidates":                 0,
            "manual_candidates":               0,
            "attention_candidates":            0,
            "structural_candidates":           1,
            "catalyst_candidates":             0,
            "etf_proxy_candidates":            0,
            "economic_intelligence_candidates": 1,
            "current_source_candidates":       0,
            "llm_symbol_discovery_used":       False,
            "raw_news_used":                   False,
            "broad_intraday_scan_used":        False,
        },
        "quota_summary": {
            "structural_position": {"min": 8,  "max": 20, "used": 1,  "protected": True},
            "catalyst_swing":      {"min": 10, "max": 30, "used": 0},
            "attention":           {"max": 15, "used": 0,  "capped": True},
            "etf_proxy":           {"max": 10, "used": 0,  "capped": True},
            "held":                {"protected": True, "used": 0},
            "manual_conviction":   {"protected": True, "used": 0},
            "total":               {"max": 50, "used": len(candidates)},
        },
        "candidates":    candidates,
        "inclusion_log": [{"symbol": c["symbol"], "reason": "test"} for c in candidates],
        "exclusion_log": [],
        "warnings":      [],
        "live_output_changed": False,
    }


def _write_json(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, f)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Universe builder loads feed and writes output
# ---------------------------------------------------------------------------

class TestBuilderLoadsAndWrites:
    def test_feed_path_exists(self):
        assert os.path.exists(_FEED_PATH), f"Missing: {_FEED_PATH}"

    def test_snapshot_path_exists(self):
        assert os.path.exists(_SNAPSHOT_PATH), f"Missing: {_SNAPSHOT_PATH}"

    def test_universe_builds_without_errors(self, universe):
        assert universe.errors == [], f"Build errors: {universe.errors}"

    def test_output_file_written(self):
        assert os.path.exists(_SHADOW_PATH), f"Shadow universe not written: {_SHADOW_PATH}"

    def test_output_file_parseable_json(self):
        with open(_SHADOW_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_candidates_non_empty(self, universe):
        assert len(universe.candidates) > 0

    def test_inclusion_log_non_empty(self, universe):
        assert len(universe.inclusion_log) > 0

    def test_exclusion_log_exists(self, universe):
        assert isinstance(universe.exclusion_log, list)


# ---------------------------------------------------------------------------
# Economic candidates mapped correctly
# ---------------------------------------------------------------------------

class TestEconomicCandidateMapping:
    def test_vrt_in_universe(self, universe):
        syms = {c.symbol for c in universe.candidates}
        assert "VRT" in syms

    def test_etn_in_universe(self, universe):
        syms = {c.symbol for c in universe.candidates}
        assert "ETN" in syms

    def test_pwr_in_universe(self, universe):
        syms = {c.symbol for c in universe.candidates}
        assert "PWR" in syms

    def test_vrt_maps_to_structural_position_quota(self, universe):
        vrt = next(c for c in universe.candidates if c.symbol == "VRT")
        assert vrt.quota["group"] == "structural_position"

    def test_vrt_maps_to_position_route(self, universe):
        vrt = next(c for c in universe.candidates if c.symbol == "VRT")
        assert vrt.route == "position"

    def test_ceg_maps_to_swing_or_watchlist(self, universe):
        ceg = next(c for c in universe.candidates if c.symbol == "CEG")
        assert ceg.route in {"swing", "watchlist"}

    def test_ceg_not_executable(self, universe):
        ceg = next(c for c in universe.candidates if c.symbol == "CEG")
        assert ceg.execution_instructions["executable"] is False

    def test_xlu_maps_to_etf_proxy_quota(self, universe):
        xlu = next((c for c in universe.candidates if c.symbol == "XLU"), None)
        assert xlu is not None
        assert xlu.quota["group"] == "etf_proxy"

    def test_xlu_maps_to_watchlist_route(self, universe):
        xlu = next(c for c in universe.candidates if c.symbol == "XLU")
        assert xlu.route == "watchlist"

    def test_xlu_not_executable(self, universe):
        xlu = next(c for c in universe.candidates if c.symbol == "XLU")
        assert xlu.execution_instructions["executable"] is False

    def test_direct_beneficiary_maps_to_structural_bucket(self, ec_direct):
        c = _from_economic_candidate(ec_direct)
        assert c.bucket_type == "structural"
        assert c.quota["group"] == "structural_position"

    def test_second_order_maps_to_structural_bucket(self, ec_second_order):
        c = _from_economic_candidate(ec_second_order)
        assert c.bucket_type == "structural"
        assert c.quota["group"] == "structural_position"

    def test_etf_proxy_maps_to_proxy_bucket(self, ec_etf_proxy):
        c = _from_economic_candidate(ec_etf_proxy)
        assert c.bucket_type == "proxy"
        assert c.quota["group"] == "etf_proxy"


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------

class TestQuotaEnforcement:
    def test_structural_quota_not_exceeded(self, universe):
        structural = sum(1 for c in universe.candidates if c.quota.get("group") == "structural_position")
        assert structural <= _STRUCTURAL_MAX

    def test_attention_quota_not_exceeded(self, universe):
        attention = sum(1 for c in universe.candidates if c.quota.get("group") == "attention")
        assert attention <= _ATTENTION_MAX

    def test_etf_proxy_quota_not_exceeded(self, universe):
        etf = sum(1 for c in universe.candidates if c.quota.get("group") == "etf_proxy")
        assert etf <= _ETF_PROXY_MAX

    def test_total_candidates_not_exceeded(self, universe):
        assert len(universe.candidates) <= _TOTAL_MAX

    def test_attention_does_not_consume_structural_quota(self, universe):
        attention = [c for c in universe.candidates if c.quota.get("group") == "attention"]
        for c in attention:
            assert c.bucket_type != "structural", \
                f"Attention candidate {c.symbol} has structural bucket_type"

    def test_structural_candidates_have_protected_quota(self, universe):
        structural = [c for c in universe.candidates if c.quota.get("group") == "structural_position"]
        for c in structural:
            assert c.quota.get("protected") is True, \
                f"Structural candidate {c.symbol} is not marked protected"

    def test_exclusion_log_has_cap_entries(self, universe):
        cap_exclusions = [
            e for e in universe.exclusion_log
            if "quota full" in e.get("reason", "").lower()
            or "cap reached" in e.get("reason", "").lower()
        ]
        # Day 4: attention cap was binding (50 Tier B, cap 15).
        # Day 6: structural cap (20/20) and total cap (50) are the binding constraints.
        # Either way some exclusions must exist when >50 candidates are attempted.
        assert len(cap_exclusions) > 0, "Expected cap exclusions (structural, attention, or total)"


# ---------------------------------------------------------------------------
# Held and manual conviction protection
# ---------------------------------------------------------------------------

class TestProtectedGroups:
    def test_manual_conviction_favourites_are_protected(self, universe):
        manual = [c for c in universe.candidates if c.quota.get("group") == "manual_conviction"]
        for c in manual:
            assert c.quota.get("protected") is True, \
                f"Manual candidate {c.symbol} is not protected"

    def test_manual_conviction_route_correct(self, universe):
        manual = [c for c in universe.candidates if c.quota.get("group") == "manual_conviction"]
        for c in manual:
            assert c.route == "manual_conviction"

    def test_held_group_protected_in_quota_summary(self, universe):
        qs = universe.quota_summary
        assert qs["held"]["protected"] is True

    def test_manual_conviction_group_protected_in_quota_summary(self, universe):
        qs = universe.quota_summary
        assert qs["manual_conviction"]["protected"] is True

    def test_held_candidates_present_in_fixture_with_supplied_symbols(self):
        """Verify held protection logic with an injected favourite as proxy for held."""
        c = _from_favourite("HELD_SYM")
        assert c.quota["protected"] is True
        assert c.route == "manual_conviction"


# ---------------------------------------------------------------------------
# Required fields on every candidate
# ---------------------------------------------------------------------------

class TestRequiredFields:
    def test_every_candidate_has_reason_to_care(self, universe):
        for c in universe.candidates:
            assert c.reason_to_care, f"{c.symbol} missing reason_to_care"

    def test_every_candidate_has_route(self, universe):
        for c in universe.candidates:
            assert c.route in {"position", "swing", "intraday_swing", "watchlist", "held", "manual_conviction", "do_not_touch"}, \
                f"{c.symbol} invalid route: {c.route}"

    def test_every_candidate_has_source_labels(self, universe):
        for c in universe.candidates:
            assert c.source_labels, f"{c.symbol} missing source_labels"

    def test_every_candidate_has_quota(self, universe):
        for c in universe.candidates:
            assert isinstance(c.quota, dict) and c.quota, f"{c.symbol} missing quota"

    def test_every_candidate_has_eligibility(self, universe):
        for c in universe.candidates:
            assert isinstance(c.eligibility, dict), f"{c.symbol} eligibility is not a dict"

    def test_every_candidate_has_execution_instructions(self, universe):
        for c in universe.candidates:
            assert isinstance(c.execution_instructions, dict), \
                f"{c.symbol} execution_instructions is not a dict"

    def test_no_candidate_is_executable(self, universe):
        for c in universe.candidates:
            assert c.execution_instructions.get("executable") is False, \
                f"{c.symbol} is marked executable"

    def test_every_candidate_has_live_output_changed_false(self, universe):
        for c in universe.candidates:
            assert c.live_output_changed is False, \
                f"{c.symbol} live_output_changed is True"


# ---------------------------------------------------------------------------
# Inclusion / exclusion log completeness
# ---------------------------------------------------------------------------

class TestLogs:
    def test_every_included_symbol_in_inclusion_log(self, universe):
        log_syms = {e["symbol"] for e in universe.inclusion_log}
        for c in universe.candidates:
            assert c.symbol in log_syms, f"{c.symbol} not in inclusion_log"

    def test_exclusion_log_entries_have_reason(self, universe):
        for e in universe.exclusion_log:
            assert e.get("reason"), f"Exclusion log entry missing reason: {e}"

    def test_inclusion_log_entries_have_reason(self, universe):
        for e in universe.inclusion_log:
            assert e.get("reason"), f"Inclusion log entry missing reason: {e}"


# ---------------------------------------------------------------------------
# Governance flags
# ---------------------------------------------------------------------------

class TestGovernanceFlags:
    def test_universe_live_output_changed_false(self, universe):
        assert universe.live_output_changed is False

    def test_summary_llm_discovery_false(self, universe):
        assert universe.universe_summary["llm_symbol_discovery_used"] is False

    def test_summary_raw_news_false(self, universe):
        assert universe.universe_summary["raw_news_used"] is False

    def test_summary_intraday_scan_false(self, universe):
        assert universe.universe_summary["broad_intraday_scan_used"] is False

    def test_mode_is_shadow_only(self, universe):
        assert universe.mode == "shadow_only"


# ---------------------------------------------------------------------------
# Validator — valid shadow universe passes
# ---------------------------------------------------------------------------

class TestValidatorPassesValidUniverse:
    def test_generated_shadow_passes_validator(self):
        assert os.path.exists(_SHADOW_PATH), "Shadow universe not generated"
        result = validate_shadow_universe(_SHADOW_PATH)
        assert result.ok, f"Validator errors: {result.errors}"

    def test_validate_all_includes_shadow(self):
        results = validate_all(_BASE_DIR)
        assert "active_opportunity_universe_shadow" in results, \
            "validate_all did not include shadow universe"
        assert results["active_opportunity_universe_shadow"].ok, \
            f"Shadow validation failed: {results['active_opportunity_universe_shadow'].errors}"


# ---------------------------------------------------------------------------
# Validator — rejects invalid shadow universe variants
# ---------------------------------------------------------------------------

class TestValidatorRejectsInvalidUniverse:
    def test_missing_reason_to_care_fails(self):
        bad = _minimal_shadow_candidate()
        del bad["reason_to_care"]
        path = _write_json(_minimal_shadow_universe([bad]))
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("reason_to_care" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_invalid_route_fails(self):
        bad = _minimal_shadow_candidate()
        bad["route"] = "definitely_not_a_route"
        path = _write_json(_minimal_shadow_universe([bad]))
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("route" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_invalid_quota_group_fails(self):
        bad = _minimal_shadow_candidate()
        bad["quota"] = {"group": "not_a_valid_group", "protected": False}
        path = _write_json(_minimal_shadow_universe([bad]))
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("quota.group" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_candidate_marked_executable_fails(self):
        bad = _minimal_shadow_candidate()
        bad["execution_instructions"] = {"executable": True}
        path = _write_json(_minimal_shadow_universe([bad]))
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("executable" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_quota_overrun_fails(self):
        # Put 16 candidates in the attention group (cap is 15)
        candidates = [
            dict(_minimal_shadow_candidate(f"SYM{i}"), quota={"group": "attention", "protected": False}, route="watchlist", reason_to_care="attention_shadow_only")
            for i in range(16)
        ]
        universe = _minimal_shadow_universe(candidates)
        universe["quota_summary"]["attention"]["used"] = 16
        path = _write_json(universe)
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("attention" in e and "exceeds cap" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_live_output_changed_true_fails(self):
        bad = _minimal_shadow_universe()
        bad["live_output_changed"] = True
        path = _write_json(bad)
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("live_output_changed" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_wrong_mode_fails(self):
        bad = _minimal_shadow_universe()
        bad["mode"] = "production"
        path = _write_json(bad)
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("mode" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_llm_discovery_true_fails(self):
        bad = _minimal_shadow_universe()
        bad["universe_summary"]["llm_symbol_discovery_used"] = True
        path = _write_json(bad)
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("llm_symbol_discovery_used" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_total_cap_exceeded_fails(self):
        candidates = [_minimal_shadow_candidate(f"SYM{i}") for i in range(51)]
        universe = _minimal_shadow_universe(candidates)
        path = _write_json(universe)
        try:
            result = validate_shadow_universe(path)
            assert not result.ok
            assert any("cap" in e for e in result.errors)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Day 2 and Day 3 regressions
# ---------------------------------------------------------------------------

class TestPriorDayRegressions:
    def test_validate_all_day2_and_day3_still_pass(self):
        results = validate_all(_BASE_DIR)
        for label in ["transmission_rules", "theme_taxonomy", "thematic_roster", "economic_candidate_feed"]:
            assert results[label].ok, f"Regression: {label} now failing: {results[label].errors}"
