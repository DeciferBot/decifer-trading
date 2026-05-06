"""
tests/test_intelligence_day2.py

Day 2 acceptance tests for the Intelligence-First architecture:
  - Static files exist and parse
  - Schema validator passes valid files
  - Schema validator rejects invalid files (missing fields, duplicate IDs, bad enums, empty symbols)
  - MacroTransmissionMatrix fires correctly and does not touch live output
  - live_output_changed is always False
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

import pytest

# Allow imports from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intelligence_schema_validator import (
    ValidationResult,
    validate_all,
    validate_theme_taxonomy,
    validate_thematic_roster,
    validate_transmission_rules,
)
from macro_transmission_matrix import MacroTransmissionMatrix, fire_transmission

_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "intelligence")
_RULES_PATH = os.path.join(_BASE_DIR, "transmission_rules.json")
_TAXONOMY_PATH = os.path.join(_BASE_DIR, "theme_taxonomy.json")
_ROSTER_PATH = os.path.join(_BASE_DIR, "thematic_roster.json")


# ---------------------------------------------------------------------------
# Fixtures — minimal valid objects for inject-and-mutate tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def valid_rule():
    return {
        "rule_id": "test_rule_001",
        "driver": "test_driver",
        "output_type": "theme_tailwind",
        "affected_targets": ["data_centre_power"],
        "direction": "positive",
        "confidence": 0.8,
        "horizon": "multi_week",
        "required_confirmations": ["price_and_volume_confirmation"],
        "blocked_if": [],
        "reason": "test reason",
        "source_type": "deterministic_rule",
        "source_label": "test",
        "last_reviewed": "2026-05-05",
    }


@pytest.fixture()
def valid_theme():
    return {
        "theme_id": "test_theme_001",
        "name": "Test Theme",
        "beneficiary_types": ["test_type"],
        "typical_horizon": "multi_week",
        "default_routes": ["position"],
        "activation_drivers": ["test_driver"],
        "confirmation_requirements": ["price_and_volume"],
        "risk_flags": ["valuation"],
        "invalidation_examples": ["example 1"],
        "source_label": "test",
        "last_reviewed": "2026-05-05",
    }


@pytest.fixture()
def valid_roster_entry():
    return {
        "theme_id": "test_theme_001",
        "core_symbols": ["AAPL"],
        "etf_proxies": [],
        "route_bias": "position_or_swing",
        "minimum_liquidity_class": "high",
        "max_candidates": 5,
        "notes": "test",
        "last_reviewed": "2026-05-05",
        "source_label": "test",
    }


def _wrap_rules(rules: list) -> dict:
    return {"schema_version": "1.0", "generated_at": "2026-05-05", "source_label": "test", "rules": rules}


def _wrap_themes(themes: list) -> dict:
    return {"schema_version": "1.0", "generated_at": "2026-05-05", "source_label": "test", "themes": themes}


def _wrap_rosters(rosters: list) -> dict:
    return {"schema_version": "1.0", "generated_at": "2026-05-05", "source_label": "test", "rosters": rosters}


def _write_json(data: dict) -> str:
    """Write dict to a temp file, return path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, f)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Static file existence and parseability
# ---------------------------------------------------------------------------

class TestStaticFilesExist:
    def test_transmission_rules_exists(self):
        assert os.path.exists(_RULES_PATH), f"Missing: {_RULES_PATH}"

    def test_theme_taxonomy_exists(self):
        assert os.path.exists(_TAXONOMY_PATH), f"Missing: {_TAXONOMY_PATH}"

    def test_thematic_roster_exists(self):
        assert os.path.exists(_ROSTER_PATH), f"Missing: {_ROSTER_PATH}"

    def test_all_files_parse_as_json(self):
        for path in [_RULES_PATH, _TAXONOMY_PATH, _ROSTER_PATH]:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict), f"{path} did not parse as a JSON object"


# ---------------------------------------------------------------------------
# Validator — valid files pass
# ---------------------------------------------------------------------------

class TestValidatorPassesValidFiles:
    def test_transmission_rules_passes(self):
        result = validate_transmission_rules(_RULES_PATH)
        assert result.ok, f"Unexpected errors: {result.errors}"

    def test_theme_taxonomy_passes(self):
        result = validate_theme_taxonomy(_TAXONOMY_PATH)
        assert result.ok, f"Unexpected errors: {result.errors}"

    def test_thematic_roster_passes(self):
        result = validate_thematic_roster(_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
        assert result.ok, f"Unexpected errors: {result.errors}"

    def test_validate_all_passes(self):
        results = validate_all(_BASE_DIR)
        for label, result in results.items():
            assert result.ok, f"{label} failed: {result.errors}"


# ---------------------------------------------------------------------------
# Validator — missing required fields
# ---------------------------------------------------------------------------

class TestValidatorMissingFields:
    def test_rule_missing_required_field(self, valid_rule):
        bad_rule = {k: v for k, v in valid_rule.items() if k != "reason"}
        path = _write_json(_wrap_rules([bad_rule]))
        try:
            result = validate_transmission_rules(path)
            assert not result.ok
            assert any("reason" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_theme_missing_required_field(self, valid_theme):
        bad_theme = {k: v for k, v in valid_theme.items() if k != "activation_drivers"}
        path = _write_json(_wrap_themes([bad_theme]))
        try:
            result = validate_theme_taxonomy(path)
            assert not result.ok
            assert any("activation_drivers" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_roster_missing_required_field(self, valid_roster_entry):
        bad_roster = {k: v for k, v in valid_roster_entry.items() if k != "core_symbols"}
        path = _write_json(_wrap_rosters([bad_roster]))
        try:
            result = validate_thematic_roster(path)
            assert not result.ok
            assert any("core_symbols" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_file_missing_top_level_schema_version(self, valid_rule):
        data = _wrap_rules([valid_rule])
        del data["schema_version"]
        path = _write_json(data)
        try:
            result = validate_transmission_rules(path)
            assert not result.ok
            assert any("schema_version" in e for e in result.errors)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Validator — duplicate IDs
# ---------------------------------------------------------------------------

class TestValidatorDuplicateIDs:
    def test_duplicate_rule_id(self, valid_rule):
        path = _write_json(_wrap_rules([valid_rule, copy.deepcopy(valid_rule)]))
        try:
            result = validate_transmission_rules(path)
            assert not result.ok
            assert any("duplicate rule_id" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_duplicate_theme_id(self, valid_theme):
        path = _write_json(_wrap_themes([valid_theme, copy.deepcopy(valid_theme)]))
        try:
            result = validate_theme_taxonomy(path)
            assert not result.ok
            assert any("duplicate theme_id" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_duplicate_roster_theme_id(self, valid_roster_entry):
        path = _write_json(_wrap_rosters([valid_roster_entry, copy.deepcopy(valid_roster_entry)]))
        try:
            result = validate_thematic_roster(path)
            assert not result.ok
            assert any("duplicate theme_id" in e for e in result.errors)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Validator — invalid enum values
# ---------------------------------------------------------------------------

class TestValidatorInvalidEnums:
    def test_invalid_output_type(self, valid_rule):
        bad = dict(valid_rule, output_type="not_a_valid_output_type")
        path = _write_json(_wrap_rules([bad]))
        try:
            result = validate_transmission_rules(path)
            assert not result.ok
            assert any("output_type" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_invalid_direction(self, valid_rule):
        bad = dict(valid_rule, direction="sideways")
        path = _write_json(_wrap_rules([bad]))
        try:
            result = validate_transmission_rules(path)
            assert not result.ok
            assert any("direction" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_invalid_route_in_taxonomy(self, valid_theme):
        bad = dict(valid_theme, default_routes=["not_a_real_route"])
        path = _write_json(_wrap_themes([bad]))
        try:
            result = validate_theme_taxonomy(path)
            assert not result.ok
            assert any("not_a_real_route" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_invalid_route_bias_in_roster(self, valid_roster_entry):
        bad = dict(valid_roster_entry, route_bias="invalid_bias")
        path = _write_json(_wrap_rosters([bad]))
        try:
            result = validate_thematic_roster(path)
            assert not result.ok
            assert any("route_bias" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_invalid_liquidity_class_in_roster(self, valid_roster_entry):
        bad = dict(valid_roster_entry, minimum_liquidity_class="ultra_high")
        path = _write_json(_wrap_rosters([bad]))
        try:
            result = validate_thematic_roster(path)
            assert not result.ok
            assert any("minimum_liquidity_class" in e for e in result.errors)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Validator — empty symbols
# ---------------------------------------------------------------------------

class TestValidatorEmptySymbols:
    def test_empty_core_symbols_list(self, valid_roster_entry):
        bad = dict(valid_roster_entry, core_symbols=[])
        path = _write_json(_wrap_rosters([bad]))
        try:
            result = validate_thematic_roster(path)
            assert not result.ok
            assert any("core_symbols" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_blank_string_in_core_symbols(self, valid_roster_entry):
        bad = dict(valid_roster_entry, core_symbols=["AAPL", ""])
        path = _write_json(_wrap_rosters([bad]))
        try:
            result = validate_thematic_roster(path)
            assert not result.ok
            assert any("core_symbols" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_blank_string_in_etf_proxies(self, valid_roster_entry):
        bad = dict(valid_roster_entry, etf_proxies=[""])
        path = _write_json(_wrap_rosters([bad]))
        try:
            result = validate_thematic_roster(path)
            assert not result.ok
            assert any("etf_proxies" in e for e in result.errors)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Validator — confidence range
# ---------------------------------------------------------------------------

class TestValidatorConfidenceRange:
    def test_confidence_above_one(self, valid_rule):
        bad = dict(valid_rule, confidence=1.5)
        path = _write_json(_wrap_rules([bad]))
        try:
            result = validate_transmission_rules(path)
            assert not result.ok
            assert any("confidence" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_confidence_negative(self, valid_rule):
        bad = dict(valid_rule, confidence=-0.1)
        path = _write_json(_wrap_rules([bad]))
        try:
            result = validate_transmission_rules(path)
            assert not result.ok
            assert any("confidence" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_confidence_zero_is_valid(self, valid_rule):
        good = dict(valid_rule, confidence=0.0)
        path = _write_json(_wrap_rules([good]))
        try:
            result = validate_transmission_rules(path)
            assert result.ok, f"Unexpected errors: {result.errors}"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# MacroTransmissionMatrix — deterministic firing
# ---------------------------------------------------------------------------

class TestMacroTransmissionMatrix:
    def test_ai_capex_growth_driver_fires_data_centre_power(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["ai_capex_growth"], "blocked_conditions": []})
        # Day 6: ai_capex_growth fires both data_centre_power and semiconductors rules
        assert len(result.transmission_rules_fired) >= 1
        rule_ids = [r.rule_id for r in result.transmission_rules_fired]
        assert "ai_capex_growth_to_data_centre_power" in rule_ids
        assert "data_centre_power" in result.theme_tailwinds

    def test_driver_alias_fires_rule(self):
        # driver_alias "ai_capex_growth" should match just like the alias itself
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["ai_capex_growth"], "blocked_conditions": []})
        assert result.transmission_rules_fired, "Rule should fire via driver_alias"

    def test_unrelated_driver_does_not_fire(self):
        # Day 6: geopolitical_risk_rising now fires the defence rule — use a truly unknown driver
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["completely_unknown_driver_xyz"], "blocked_conditions": []})
        assert len(result.transmission_rules_fired) == 0
        assert result.theme_tailwinds == []

    def test_blocked_condition_suppresses_rule(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["ai_capex_growth"],
            "blocked_conditions": ["capex_guidance_cut"],
        })
        # Day 6: capex_guidance_cut blocks both ai_capex_growth rules (data_centre_power + semiconductors)
        assert len(result.transmission_rules_fired) == 0
        assert len(result.blocked_rules) >= 1
        blocked_ids = [r.rule_id for r in result.blocked_rules]
        assert "ai_capex_growth_to_data_centre_power" in blocked_ids

    def test_empty_driver_state_fires_nothing(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": [], "blocked_conditions": []})
        assert result.transmission_rules_fired == []
        assert result.theme_tailwinds == []

    def test_convenience_function_works(self):
        # Day 6: ai_capex_growth fires both data_centre_power and semiconductors rules
        result = fire_transmission({"active_drivers": ["ai_capex_growth"], "blocked_conditions": []}, rules_path=_RULES_PATH)
        assert len(result.transmission_rules_fired) >= 1
        assert "data_centre_power" in result.theme_tailwinds

    def test_missing_rules_file_returns_error(self):
        matrix = MacroTransmissionMatrix(rules_path="/nonexistent/path/rules.json")
        result = matrix.fire({"active_drivers": ["ai_capex_growth"], "blocked_conditions": []})
        assert len(result.errors) > 0

    def test_to_dict_serialisable(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["ai_capex_growth"], "blocked_conditions": []})
        d = result.to_dict()
        # Must be JSON-serialisable
        serialised = json.dumps(d)
        assert "data_centre_power" in serialised


# ---------------------------------------------------------------------------
# live_output_changed invariant
# ---------------------------------------------------------------------------

class TestLiveOutputUnchanged:
    def test_transmission_result_never_sets_live_output_changed(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        for drivers in [
            {"active_drivers": ["ai_capex_growth"], "blocked_conditions": []},
            {"active_drivers": [], "blocked_conditions": []},
            {"active_drivers": ["ai_capex_growth"], "blocked_conditions": ["capex_guidance_cut"]},
        ]:
            result = matrix.fire(drivers)
            assert result.live_output_changed is False, \
                f"live_output_changed was set to True for driver_state={drivers}"

    def test_validate_all_does_not_touch_live_files(self):
        # validate_all() must only read files, never write
        # Verify no file modification time changes on known live files
        import time
        live_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trades.json")
        if os.path.exists(live_file):
            before_mtime = os.path.getmtime(live_file)
            validate_all(_BASE_DIR)
            after_mtime = os.path.getmtime(live_file)
            assert before_mtime == after_mtime, "validate_all() modified a live data file"


# ---------------------------------------------------------------------------
# Cross-reference: roster theme_ids must match taxonomy
# ---------------------------------------------------------------------------

class TestCrossReference:
    def test_roster_theme_id_must_exist_in_taxonomy(self, valid_theme, valid_roster_entry):
        # roster references theme_id "test_theme_001", taxonomy only has "test_theme_001"
        tax_path = _write_json(_wrap_themes([valid_theme]))
        ros_path = _write_json(_wrap_rosters([valid_roster_entry]))
        try:
            result = validate_thematic_roster(ros_path, taxonomy_path=tax_path)
            assert result.ok, f"Unexpected errors: {result.errors}"
        finally:
            os.unlink(tax_path)
            os.unlink(ros_path)

    def test_roster_unknown_theme_id_fails(self, valid_theme, valid_roster_entry):
        bad_roster = dict(valid_roster_entry, theme_id="totally_unknown_theme")
        tax_path = _write_json(_wrap_themes([valid_theme]))
        ros_path = _write_json(_wrap_rosters([bad_roster]))
        try:
            result = validate_thematic_roster(ros_path, taxonomy_path=tax_path)
            assert not result.ok
            assert any("totally_unknown_theme" in e for e in result.errors)
        finally:
            os.unlink(tax_path)
            os.unlink(ros_path)
