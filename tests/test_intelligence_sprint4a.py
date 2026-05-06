"""
tests/test_intelligence_sprint4a.py — Sprint 4A acceptance tests.

Covers:
  - intelligence_engine.py generates both output files
  - daily_economic_state.json schema and driver fields
  - current_economic_context.json schema and route_adjustments
  - All forbidden paths confirmed absent (no live API, broker, env, LLM, raw news, scan)
  - Local inference: ai_capex_growth active_shadow_inferred, unavailable drivers tagged
  - Context constraints: no symbols created, no executable flag, no universe modified
  - Prior sprint regression pass
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intelligence_schema_validator import (
    validate_daily_economic_state,
    validate_current_economic_context,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INTEL_DIR = os.path.join(_REPO, "data", "intelligence")
_UB_DIR = os.path.join(_REPO, "data", "universe_builder")

_DAILY_STATE_PATH = os.path.join(_INTEL_DIR, "daily_economic_state.json")
_CONTEXT_PATH = os.path.join(_INTEL_DIR, "current_economic_context.json")
_FEED_PATH = os.path.join(_INTEL_DIR, "economic_candidate_feed.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. Intelligence engine generates both output files
# ---------------------------------------------------------------------------

class TestIntelligenceEngineGeneration:

    def test_daily_economic_state_exists(self):
        assert os.path.exists(_DAILY_STATE_PATH), \
            "daily_economic_state.json not found — run python3 intelligence_engine.py"

    def test_current_economic_context_exists(self):
        assert os.path.exists(_CONTEXT_PATH), \
            "current_economic_context.json not found — run python3 intelligence_engine.py"


# ---------------------------------------------------------------------------
# 2. daily_economic_state.json schema
# ---------------------------------------------------------------------------

class TestDailyEconomicStateSchema:

    def test_validates_without_errors(self):
        result = validate_daily_economic_state(_DAILY_STATE_PATH)
        assert result.ok, f"daily_economic_state validation failed: {result.errors}"

    def test_required_top_keys_present(self):
        data = _load(_DAILY_STATE_PATH)
        for key in (
            "schema_version", "generated_at", "valid_for_session", "mode",
            "data_source_mode", "driver_scores", "active_drivers",
            "inactive_drivers", "blocked_drivers", "confidence_summary",
            "no_live_api_called", "broker_called", "env_inspected",
            "raw_news_used", "llm_used", "broad_intraday_scan_used",
            "live_output_changed",
        ):
            assert key in data, f"daily_economic_state missing key: {key}"

    def test_driver_scores_has_16_entries(self):
        data = _load(_DAILY_STATE_PATH)
        drivers = data["driver_scores"]
        assert len(drivers) == 16, \
            f"Expected 16 driver entries, got {len(drivers)}: {list(drivers.keys())}"

    def test_all_drivers_have_required_fields(self):
        data = _load(_DAILY_STATE_PATH)
        required = {"driver_id", "state", "confidence", "source_label", "freshness_status",
                    "used_live_data", "used_raw_news", "used_llm"}
        for driver_id, driver in data["driver_scores"].items():
            missing = required - set(driver.keys())
            assert not missing, \
                f"Driver '{driver_id}' missing fields: {missing}"


# ---------------------------------------------------------------------------
# 3. current_economic_context.json schema
# ---------------------------------------------------------------------------

class TestCurrentEconomicContextSchema:

    def test_validates_without_errors(self):
        result = validate_current_economic_context(_CONTEXT_PATH)
        assert result.ok, f"current_economic_context validation failed: {result.errors}"

    def test_required_top_keys_present(self):
        data = _load(_CONTEXT_PATH)
        for key in (
            "schema_version", "generated_at", "mode", "economic_regime",
            "risk_posture", "confidence", "route_adjustments",
            "active_driver_summary", "active_theme_summary",
            "no_live_api_called", "live_output_changed",
        ):
            assert key in data, f"current_economic_context missing key: {key}"

    def test_route_adjustments_has_all_four_groups(self):
        data = _load(_CONTEXT_PATH)
        ra = data["route_adjustments"]
        for group in ("POSITION", "SWING", "INTRADAY_SWING", "WATCHLIST"):
            assert group in ra, \
                f"route_adjustments missing required group '{group}'"


# ---------------------------------------------------------------------------
# 4. Forbidden path checks (safety invariants)
# ---------------------------------------------------------------------------

class TestForbiddenPathsSprint4A:

    def test_no_live_api_called_daily_state(self):
        assert _load(_DAILY_STATE_PATH)["no_live_api_called"] is True

    def test_broker_called_false_daily_state(self):
        assert _load(_DAILY_STATE_PATH)["broker_called"] is False

    def test_env_inspected_false_daily_state(self):
        assert _load(_DAILY_STATE_PATH)["env_inspected"] is False

    def test_raw_news_used_false_daily_state(self):
        assert _load(_DAILY_STATE_PATH)["raw_news_used"] is False

    def test_llm_used_false_daily_state(self):
        assert _load(_DAILY_STATE_PATH)["llm_used"] is False

    def test_broad_intraday_scan_false_daily_state(self):
        assert _load(_DAILY_STATE_PATH)["broad_intraday_scan_used"] is False

    def test_live_output_changed_false_daily_state(self):
        assert _load(_DAILY_STATE_PATH)["live_output_changed"] is False

    def test_no_live_api_called_context(self):
        assert _load(_CONTEXT_PATH)["no_live_api_called"] is True

    def test_broker_called_false_context(self):
        assert _load(_CONTEXT_PATH)["broker_called"] is False

    def test_env_inspected_false_context(self):
        assert _load(_CONTEXT_PATH)["env_inspected"] is False

    def test_raw_news_used_false_context(self):
        assert _load(_CONTEXT_PATH)["raw_news_used"] is False

    def test_llm_used_false_context(self):
        assert _load(_CONTEXT_PATH)["llm_used"] is False

    def test_broad_intraday_scan_false_context(self):
        assert _load(_CONTEXT_PATH)["broad_intraday_scan_used"] is False

    def test_live_output_changed_false_context(self):
        assert _load(_CONTEXT_PATH)["live_output_changed"] is False


# ---------------------------------------------------------------------------
# 5. Local inference checks
# ---------------------------------------------------------------------------

class TestLocalInference:

    def test_ai_capex_growth_is_active_shadow_inferred(self):
        data = _load(_DAILY_STATE_PATH)
        ai = data["driver_scores"].get("ai_capex_growth", {})
        assert ai.get("state") == "active_shadow_inferred", \
            f"ai_capex_growth expected active_shadow_inferred, got {ai.get('state')!r}"

    def test_unavailable_drivers_have_unavailable_reason(self):
        data = _load(_DAILY_STATE_PATH)
        for driver_id, driver in data["driver_scores"].items():
            if driver.get("state") == "unavailable":
                assert driver.get("unavailable_reason"), \
                    f"Driver '{driver_id}' is unavailable but missing unavailable_reason"

    def test_at_least_one_unavailable_driver(self):
        data = _load(_DAILY_STATE_PATH)
        unavailable = [
            did for did, d in data["driver_scores"].items()
            if d.get("state") == "unavailable"
        ]
        assert len(unavailable) >= 1, \
            "Expected at least one unavailable driver (inflation/growth/usd/valuation/etc)"

    def test_no_driver_claims_used_live_data(self):
        data = _load(_DAILY_STATE_PATH)
        for driver_id, driver in data["driver_scores"].items():
            assert driver.get("used_live_data") is False, \
                f"Driver '{driver_id}' claims used_live_data=True — forbidden in Sprint 4A"

    def test_inflation_unavailable(self):
        data = _load(_DAILY_STATE_PATH)
        inflation = data["driver_scores"].get("inflation", {})
        assert inflation.get("state") == "unavailable", \
            f"inflation expected unavailable, got {inflation.get('state')!r}"

    def test_growth_unavailable(self):
        data = _load(_DAILY_STATE_PATH)
        growth = data["driver_scores"].get("growth", {})
        assert growth.get("state") == "unavailable", \
            f"growth expected unavailable, got {growth.get('state')!r}"

    def test_usd_unavailable(self):
        data = _load(_DAILY_STATE_PATH)
        usd = data["driver_scores"].get("usd", {})
        assert usd.get("state") == "unavailable", \
            f"usd expected unavailable, got {usd.get('state')!r}"


# ---------------------------------------------------------------------------
# 6. Context constraints
# ---------------------------------------------------------------------------

class TestContextConstraints:

    def test_context_creates_no_symbols(self):
        """current_economic_context.json must not contain a 'candidates' list."""
        data = _load(_CONTEXT_PATH)
        assert "candidates" not in data, \
            "current_economic_context must not create or list symbols"

    def test_context_does_not_modify_shadow_universe(self):
        """Shadow universe file must be unchanged — no 'modified_by' link to intelligence_engine."""
        shadow = _load(_SHADOW_PATH)
        # The shadow universe should not have any field that references intelligence_engine as a mutator
        assert shadow.get("live_output_changed") is False
        # source_files in shadow should not contain daily_economic_state or current_economic_context
        source_files = shadow.get("source_files") or []
        for sf in source_files:
            assert "daily_economic_state" not in sf, \
                "Shadow universe source_files unexpectedly references daily_economic_state"
            assert "current_economic_context" not in sf, \
                "Shadow universe source_files unexpectedly references current_economic_context"

    def test_route_adjustments_contain_no_executable_flag(self):
        data = _load(_CONTEXT_PATH)
        ra = data.get("route_adjustments") or {}
        for group_name, group_data in ra.items():
            if isinstance(group_data, dict):
                assert group_data.get("executable") is not True, \
                    f"route_adjustments.{group_name}.executable must not be True"

    def test_economic_regime_is_valid(self):
        data = _load(_CONTEXT_PATH)
        valid_regimes = {
            "unknown_static_bootstrap", "mixed_shadow_regime",
            "ai_infrastructure_tailwind_shadow", "credit_stress_watch_shadow",
            "risk_off_watch_shadow", "selective_shadow", "unavailable",
        }
        assert data.get("economic_regime") in valid_regimes, \
            f"economic_regime '{data.get('economic_regime')}' not in allowed set"

    def test_risk_posture_is_valid(self):
        data = _load(_CONTEXT_PATH)
        valid_postures = {"unknown", "neutral", "selective", "cautious", "defensive_selective"}
        assert data.get("risk_posture") in valid_postures, \
            f"risk_posture '{data.get('risk_posture')}' not in allowed set"

    def test_context_confidence_below_threshold(self):
        """Sprint 4A context confidence must be conservative (≤ 0.45)."""
        data = _load(_CONTEXT_PATH)
        assert data.get("confidence", 1.0) <= 0.45, \
            f"Context confidence {data.get('confidence')} exceeds 0.45 — overstating certainty"


# ---------------------------------------------------------------------------
# 7. Prior suite regression
# ---------------------------------------------------------------------------

class TestPriorSuiteRegression:

    def test_shadow_universe_freshness_still_sprint3(self):
        """Shadow universe must still carry sprint3 freshness (not clobbered by intelligence_engine)."""
        shadow = _load(_SHADOW_PATH)
        assert shadow["freshness_status"] == "static_bootstrap_sprint3", \
            f"Shadow universe freshness_status changed unexpectedly: {shadow['freshness_status']}"

    def test_feed_live_output_changed_still_false(self):
        feed_path = os.path.join(_INTEL_DIR, "economic_candidate_feed.json")
        assert _load(feed_path)["live_output_changed"] is False

    def test_shadow_universe_live_output_changed_still_false(self):
        assert _load(_SHADOW_PATH)["live_output_changed"] is False

    def test_all_16_driver_ids_present(self):
        """All 16 required driver_ids must be present in driver_scores."""
        data = _load(_DAILY_STATE_PATH)
        expected = {
            "ai_capex_growth", "corporate_capex", "interest_rates", "bonds_yields",
            "oil_energy", "geopolitics", "credit", "liquidity", "risk_appetite",
            "volatility", "sector_rotation", "valuation", "consumer_behaviour",
            "inflation", "growth", "usd",
        }
        actual = set(data["driver_scores"].keys())
        missing = expected - actual
        assert not missing, f"Missing driver_ids in driver_scores: {missing}"
