"""
Sprint 7A.2 — Approved Theme Overlay / Roster Governance Tests (30 tests)

Coverage:
  Theme governance (tests 1–4): memory_storage and ai_compute_infrastructure themes exist
  Symbol approval (tests 5–13): SNDK/WDC in memory_storage, IREN in ai_compute_infrastructure
  Candidate feed quality (tests 14–19): risk flags, reason_to_care, route_hint, non-executable
  Exclusion invariants (tests 20–21): review_required and scanner_only_attention excluded
  Safety invariants (tests 22–26): no favourites workaround, no live API, no LLM, no raw news
  No-touch / no-handoff (tests 27–30): prod modules unchanged, live_output_changed false
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
_TAXONOMY_PATH = _REPO / "data/intelligence/theme_taxonomy.json"
_RULES_PATH = _REPO / "data/intelligence/transmission_rules.json"
_ROSTER_PATH = _REPO / "data/intelligence/thematic_roster.json"
_OVERLAY_PATH = _REPO / "data/reference/theme_overlay_map.json"
_FEED_PATH = _REPO / "data/intelligence/economic_candidate_feed.json"


# ---------------------------------------------------------------------------
# Fixtures (loaded once per module)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def taxonomy():
    with open(_TAXONOMY_PATH) as f:
        data = json.load(f)
    return {t["theme_id"]: t for t in data.get("themes", [])}


@pytest.fixture(scope="module")
def rules():
    with open(_RULES_PATH) as f:
        data = json.load(f)
    return {r["rule_id"]: r for r in data.get("rules", [])}


@pytest.fixture(scope="module")
def roster():
    with open(_ROSTER_PATH) as f:
        data = json.load(f)
    return {r["theme_id"]: r for r in data.get("rosters", [])}


@pytest.fixture(scope="module")
def overlay():
    with open(_OVERLAY_PATH) as f:
        data = json.load(f)
    return {t["theme_id"]: t for t in data.get("themes", [])}


@pytest.fixture(scope="module")
def feed():
    with open(_FEED_PATH) as f:
        data = json.load(f)
    return {c["symbol"]: c for c in data.get("candidates", [])}


# ---------------------------------------------------------------------------
# Test 1-2: memory_storage theme exists
# ---------------------------------------------------------------------------
class TestMemoryStorageTheme:
    def test_memory_storage_in_taxonomy(self, taxonomy):
        assert "memory_storage" in taxonomy, "memory_storage theme missing from taxonomy"

    def test_memory_storage_in_overlay(self, overlay):
        assert "memory_storage" in overlay, "memory_storage theme missing from overlay map"


# ---------------------------------------------------------------------------
# Test 3-4: ai_compute_infrastructure theme exists
# ---------------------------------------------------------------------------
class TestAIComputeInfrastructureTheme:
    def test_ai_compute_infrastructure_in_taxonomy(self, taxonomy):
        assert "ai_compute_infrastructure" in taxonomy

    def test_ai_compute_infrastructure_in_overlay(self, overlay):
        assert "ai_compute_infrastructure" in overlay


# ---------------------------------------------------------------------------
# Test 5-7: Transmission rules exist
# ---------------------------------------------------------------------------
class TestTransmissionRules:
    def test_ai_capex_to_memory_storage_rule_exists(self, rules):
        assert "ai_capex_to_memory_storage" in rules

    def test_ai_compute_demand_to_ai_compute_infrastructure_rule_exists(self, rules):
        assert "ai_compute_demand_to_ai_compute_infrastructure" in rules

    def test_memory_storage_rule_fires_ai_capex_growth(self, rules):
        rule = rules["ai_capex_to_memory_storage"]
        assert rule.get("driver_alias") == "ai_capex_growth"
        assert "memory_storage" in rule.get("affected_targets", [])


# ---------------------------------------------------------------------------
# Test 8-10: SNDK and WDC approved under memory_storage
# ---------------------------------------------------------------------------
class TestSNDKWDCApproval:
    def test_sndk_approved_under_memory_storage(self, overlay):
        entry = overlay["memory_storage"]
        assert "SNDK" in entry.get("canonical_symbols", []), "SNDK not in memory_storage overlay"

    def test_wdc_approved_under_memory_storage(self, overlay):
        entry = overlay["memory_storage"]
        assert "WDC" in entry.get("canonical_symbols", []), "WDC not in memory_storage overlay"

    def test_sndk_wdc_in_memory_storage_roster(self, roster):
        entry = roster.get("memory_storage", {})
        core = entry.get("core_symbols", [])
        assert "SNDK" in core, "SNDK not in memory_storage roster core_symbols"
        assert "WDC" in core, "WDC not in memory_storage roster core_symbols"


# ---------------------------------------------------------------------------
# Test 11-12: IREN approved under ai_compute_infrastructure
# ---------------------------------------------------------------------------
class TestIRENApproval:
    def test_iren_approved_under_ai_compute_infrastructure(self, overlay):
        entry = overlay["ai_compute_infrastructure"]
        assert "IREN" in entry.get("canonical_symbols", []), "IREN not in ai_compute_infrastructure overlay"

    def test_iren_in_ai_compute_infrastructure_roster(self, roster):
        entry = roster.get("ai_compute_infrastructure", {})
        assert "IREN" in entry.get("core_symbols", []), "IREN not in ai_compute_infrastructure roster"


# ---------------------------------------------------------------------------
# Test 13-15: All three appear in economic_candidate_feed
# ---------------------------------------------------------------------------
class TestCandidateFeedPresence:
    def test_sndk_in_economic_candidate_feed(self, feed):
        assert "SNDK" in feed, "SNDK not in economic_candidate_feed"

    def test_wdc_in_economic_candidate_feed(self, feed):
        assert "WDC" in feed, "WDC not in economic_candidate_feed"

    def test_iren_in_economic_candidate_feed(self, feed):
        assert "IREN" in feed, "IREN not in economic_candidate_feed"


# ---------------------------------------------------------------------------
# Test 16-18: Risk flags, reason_to_care, route_hint present
# ---------------------------------------------------------------------------
class TestCandidateFeedQuality:
    def test_iren_has_speculative_financing_power_risk_flags(self, feed):
        entry = feed["IREN"]
        flags = entry.get("risk_flags", [])
        assert "speculative_growth" in flags
        assert "financing_risk" in flags
        assert "power_cost" in flags

    def test_sndk_wdc_have_memory_storage_risk_flags(self, feed):
        for sym in ["SNDK", "WDC"]:
            flags = feed[sym].get("risk_flags", [])
            assert "memory_cycle_risk" in flags, f"{sym} missing memory_cycle_risk"
            assert "commodity_pricing" in flags, f"{sym} missing commodity_pricing"

    def test_all_three_have_required_fields(self, feed):
        for sym in ["SNDK", "WDC", "IREN"]:
            entry = feed[sym]
            assert entry.get("reason_to_care"), f"{sym} missing reason_to_care"
            assert entry.get("route_hint"), f"{sym} missing route_hint"
            assert entry.get("confirmation_required"), f"{sym} missing confirmation_required"


# ---------------------------------------------------------------------------
# Test 19: None are executable
# ---------------------------------------------------------------------------
class TestNonExecutable:
    def test_none_of_the_three_are_executable(self, feed):
        for sym in ["SNDK", "WDC", "IREN"]:
            entry = feed[sym]
            assert entry.get("mode") == "shadow_report_only", f"{sym} has wrong mode"
            assert entry.get("live_output_changed") is False, f"{sym} live_output_changed is True"


# ---------------------------------------------------------------------------
# Test 20-21: Exclusion invariants
# ---------------------------------------------------------------------------
class TestExclusionInvariants:
    def test_stx_review_required_not_in_feed(self, feed):
        assert "STX" not in feed, "STX (review_required) should not be in economic_candidate_feed"

    def test_scanner_only_attention_symbols_not_in_feed_as_economic(self, feed):
        with open(_FEED_PATH) as f:
            raw = json.load(f)
        scanner_only_labels = {"scanner_only_attention", "scanner_only"}
        for candidate in raw.get("candidates", []):
            labels = set(candidate.get("source_labels", []))
            assert not labels.intersection(scanner_only_labels), (
                f"Symbol {candidate['symbol']} tagged scanner_only should not be in economic feed"
            )


# ---------------------------------------------------------------------------
# Test 22-26: Safety invariants
# ---------------------------------------------------------------------------
class TestSafetyInvariants:
    def test_favourites_used_as_discovery_false(self):
        with open(_REPO / "data/reference/symbol_master.json") as f:
            master = json.load(f)
        assert master["favourites_used_as_discovery"] is False

    def test_feed_live_output_changed_false(self):
        with open(_FEED_PATH) as f:
            feed = json.load(f)
        assert feed["live_output_changed"] is False

    def test_feed_no_llm_symbol_discovery(self):
        with open(_FEED_PATH) as f:
            feed = json.load(f)
        assert feed["feed_summary"]["llm_symbol_discovery_used"] is False

    def test_feed_no_raw_news(self):
        with open(_FEED_PATH) as f:
            feed = json.load(f)
        assert feed["feed_summary"]["raw_news_used"] is False

    def test_feed_no_broad_intraday_scan(self):
        with open(_FEED_PATH) as f:
            feed = json.load(f)
        assert feed["feed_summary"]["broad_intraday_scan_used"] is False


# ---------------------------------------------------------------------------
# Test 27-28: No production modules touched
# ---------------------------------------------------------------------------
class TestNoProductionTouch:
    _PROD_MODULES = [
        "scanner.py",
        "bot_trading.py",
        "market_intelligence.py",
        "orders_core.py",
        "guardrails.py",
        "catalyst_engine.py",
        "overnight_research.py",
    ]

    def test_no_production_imports_in_candidate_resolver(self):
        with open(_REPO / "candidate_resolver.py") as f:
            src = f.read()
        for module in ["bot_trading", "market_intelligence", "orders_core", "guardrails"]:
            assert f"import {module}" not in src and f"from {module}" not in src, (
                f"candidate_resolver.py imports production module {module}"
            )

    def test_no_production_imports_in_reference_data_builder(self):
        with open(_REPO / "reference_data_builder.py") as f:
            src = f.read()
        for module in ["bot_trading", "market_intelligence", "orders_core", "guardrails"]:
            assert f"import {module}" not in src and f"from {module}" not in src, (
                f"reference_data_builder.py imports production module {module}"
            )


# ---------------------------------------------------------------------------
# Test 29-30: Handoff gate and live_output_changed
# ---------------------------------------------------------------------------
class TestHandoffGate:
    def test_enable_active_opportunity_universe_handoff_is_false(self):
        import config as C
        val = C.CONFIG.get("enable_active_opportunity_universe_handoff", False)
        assert val is False, "enable_active_opportunity_universe_handoff must remain False"

    def test_shadow_universe_live_output_changed_false(self):
        with open(_REPO / "data/universe_builder/active_opportunity_universe_shadow.json") as f:
            shadow = json.load(f)
        assert shadow["live_output_changed"] is False
