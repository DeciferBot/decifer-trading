"""
tests/test_intelligence_day6.py

Day 6 acceptance tests:
  - 4 new thematic slices: semiconductors, banks (conditional), energy, defence
  - Per-theme confidence from fired rule (banks gets 0.62, not 0.82)
  - generate_feed() fires all 5 drivers by default
  - Banks conditional rule blocked by credit_stress_rising
  - Semiconductors blocked by export_regulatory_shock
  - source_collision_report: NVDA and HIMS-like preserved via manual_conviction
  - quota_pressure_diagnostics: structural 20/20 binding, overflow tracked
  - All 5 output files pass validator
  - live_output_changed = False throughout
  - Days 2-5 regressions pass
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from candidate_resolver import (
    CandidateResolver,
    CandidateFeed,
    generate_feed,
    resolve_candidates,
)
from macro_transmission_matrix import MacroTransmissionMatrix, fire_transmission
from intelligence_schema_validator import (
    validate_all,
    validate_transmission_rules,
    validate_theme_taxonomy,
    validate_thematic_roster,
    validate_economic_candidate_feed,
    validate_shadow_universe,
    validate_comparison,
    validate_report,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_DIR = os.path.join(_REPO, "data", "intelligence")
_UB_DIR = os.path.join(_REPO, "data", "universe_builder")
_RULES_PATH = os.path.join(_BASE_DIR, "transmission_rules.json")
_TAXONOMY_PATH = os.path.join(_BASE_DIR, "theme_taxonomy.json")
_ROSTER_PATH = os.path.join(_BASE_DIR, "thematic_roster.json")
_FEED_PATH = os.path.join(_BASE_DIR, "economic_candidate_feed.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")
_COMPARISON_PATH = os.path.join(_UB_DIR, "current_vs_shadow_comparison.json")
_REPORT_PATH = os.path.join(_UB_DIR, "universe_builder_report.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def feed() -> dict:
    with open(_FEED_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def candidates(feed) -> list[dict]:
    return feed["candidates"]


@pytest.fixture(scope="module")
def shadow() -> dict:
    with open(_SHADOW_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def comparison() -> dict:
    with open(_COMPARISON_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def report() -> dict:
    with open(_REPORT_PATH) as f:
        return json.load(f)


def _candidates_for_theme(candidates: list[dict], theme: str) -> list[dict]:
    return [c for c in candidates if c.get("theme") == theme]


def _symbols_for_theme(candidates: list[dict], theme: str) -> set[str]:
    return {c["symbol"] for c in _candidates_for_theme(candidates, theme)}


# ---------------------------------------------------------------------------
# Fixture 1 — AI infrastructure leadership: ai_capex_growth fires 2 rules
# ---------------------------------------------------------------------------

class TestAIInfrastructureSlice:
    def test_ai_capex_fires_both_data_centre_and_semiconductors(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["ai_capex_growth"], "blocked_conditions": []})
        tailwinds = result.theme_tailwinds
        assert "data_centre_power" in tailwinds
        assert "semiconductors" in tailwinds

    def test_semiconductors_in_feed(self, candidates):
        syms = _symbols_for_theme(candidates, "semiconductors")
        assert "NVDA" in syms
        assert "TSM" in syms
        assert "AVGO" in syms
        assert "AMD" in syms
        assert "ASML" in syms

    def test_smh_is_etf_proxy_in_feed(self, candidates):
        smh = next((c for c in candidates if c["symbol"] == "SMH"), None)
        assert smh is not None, "SMH should be in feed as semiconductor ETF proxy"
        assert smh["role"] == "etf_proxy"
        assert smh["confidence"] == 0.45

    def test_semiconductor_direct_confidence_is_083(self, candidates):
        nvda = next((c for c in candidates if c["symbol"] == "NVDA"), None)
        assert nvda is not None
        assert nvda["confidence"] == 0.83

    def test_data_centre_power_still_in_feed(self, candidates):
        syms = _symbols_for_theme(candidates, "data_centre_power")
        assert "VRT" in syms
        assert "ETN" in syms

    def test_all_semiconductor_direct_route_hint_includes_position_or_swing(self, candidates):
        direct = [c for c in candidates if c["theme"] == "semiconductors" and c["role"] == "direct_beneficiary"]
        assert direct, "Should have direct semiconductor beneficiaries"
        for c in direct:
            route_hint = c["route_hint"]
            assert any(r in route_hint for r in ("position", "swing")), \
                f"{c['symbol']} semiconductor direct should have position or swing route"


# ---------------------------------------------------------------------------
# Fixture 2 — Yields rising + banks: conditional direction, lower confidence
# ---------------------------------------------------------------------------

class TestBanksRatesSlice:
    def test_yields_rising_fires_banks_rule(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["yields_rising"], "blocked_conditions": []})
        assert "banks" in result.theme_tailwinds

    def test_banks_rule_direction_is_conditional(self):
        with open(_RULES_PATH) as f:
            rules = json.load(f)
        banks_rule = next(r for r in rules["rules"] if r["rule_id"] == "rates_rising_to_banks_conditional")
        assert banks_rule["direction"] == "conditional"

    def test_banks_in_feed(self, candidates):
        syms = _symbols_for_theme(candidates, "banks")
        assert "JPM" in syms
        assert "BAC" in syms
        assert "WFC" in syms
        assert "GS" in syms

    def test_banks_confidence_is_lower_due_to_conditional_rule(self, candidates):
        jpm = next((c for c in candidates if c["symbol"] == "JPM"), None)
        assert jpm is not None
        # banks rule confidence = 0.62 (conditional) — lower than other direct rules (~0.82-0.83)
        assert jpm["confidence"] == 0.62

    def test_xlf_is_banks_etf_proxy(self, candidates):
        xlf = next((c for c in candidates if c["symbol"] == "XLF"), None)
        assert xlf is not None
        assert xlf["role"] == "etf_proxy"
        assert xlf["theme"] == "banks"

    def test_banks_blocked_by_credit_stress_rising(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["yields_rising"],
            "blocked_conditions": ["credit_stress_rising"],
        })
        assert "banks" not in result.theme_tailwinds
        blocked_ids = [r.rule_id for r in result.blocked_rules]
        assert "rates_rising_to_banks_conditional" in blocked_ids

    def test_banks_route_hint_is_swing_not_position(self, candidates):
        # banks route_bias = swing_only — direct beneficiaries should not have "position" hint
        jpm = next((c for c in candidates if c["symbol"] == "JPM"), None)
        assert jpm is not None
        assert "position" not in jpm["route_hint"], \
            "Banks candidates are swing_only — position route should not appear in route_hint"


# ---------------------------------------------------------------------------
# Fixture 3 — Oil supply shock + energy sector
# ---------------------------------------------------------------------------

class TestEnergyOilShockSlice:
    def test_oil_supply_shock_fires_energy_rule(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["oil_supply_shock"], "blocked_conditions": []})
        assert "energy" in result.theme_tailwinds

    def test_energy_in_feed(self, candidates):
        syms = _symbols_for_theme(candidates, "energy")
        assert "XOM" in syms
        assert "CVX" in syms
        assert "OXY" in syms
        assert "SLB" in syms

    def test_xle_is_energy_etf_proxy(self, candidates):
        xle = next((c for c in candidates if c["symbol"] == "XLE"), None)
        assert xle is not None
        assert xle["role"] == "etf_proxy"
        assert xle["theme"] == "energy"

    def test_energy_confidence_is_075(self, candidates):
        xom = next((c for c in candidates if c["symbol"] == "XOM"), None)
        assert xom is not None
        assert xom["confidence"] == 0.75

    def test_energy_blocked_by_oil_demand_destruction(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["oil_supply_shock"],
            "blocked_conditions": ["oil_rise_is_demand_destruction"],
        })
        assert "energy" not in result.theme_tailwinds


# ---------------------------------------------------------------------------
# Fixture 4 — Geopolitical defence bid
# ---------------------------------------------------------------------------

class TestDefenceGeopoliticalSlice:
    def test_geopolitical_risk_fires_defence_rule(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({"active_drivers": ["geopolitical_risk_rising"], "blocked_conditions": []})
        assert "defence" in result.theme_tailwinds

    def test_defence_in_feed(self, candidates):
        syms = _symbols_for_theme(candidates, "defence")
        assert "LMT" in syms
        assert "NOC" in syms
        assert "RTX" in syms
        assert "GD" in syms

    def test_ita_is_defence_etf_proxy(self, candidates):
        ita = next((c for c in candidates if c["symbol"] == "ITA"), None)
        assert ita is not None
        assert ita["role"] == "etf_proxy"
        assert ita["theme"] == "defence"

    def test_defence_confidence_is_076(self, candidates):
        lmt = next((c for c in candidates if c["symbol"] == "LMT"), None)
        assert lmt is not None
        assert lmt["confidence"] == 0.76

    def test_defence_blocked_by_de_escalation(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["geopolitical_risk_rising"],
            "blocked_conditions": ["risk_event_de_escalates"],
        })
        assert "defence" not in result.theme_tailwinds


# ---------------------------------------------------------------------------
# Fixture 5 — All 5 slices active simultaneously (default generate_feed)
# ---------------------------------------------------------------------------

class TestAllFiveSlices:
    def test_generate_feed_produces_all_5_themes(self, candidates):
        themes = {c["theme"] for c in candidates}
        assert "data_centre_power" in themes
        assert "semiconductors" in themes
        assert "banks" in themes
        assert "energy" in themes
        assert "defence" in themes

    def test_total_candidates_is_26(self, candidates):
        # Sprint 3 adds 3 new themes (quality_cash_flow, defensive_quality, small_caps headwind)
        # expanding from 26 to more. Accept >= 26.
        assert len(candidates) >= 26

    def test_5_etf_proxies(self, candidates):
        etf_proxies = [c for c in candidates if c["role"] == "etf_proxy"]
        # Sprint 3 adds QUAL, XLP, XLV, SPLV ETF proxies — accept >= 5
        assert len(etf_proxies) >= 5

    def test_llm_discovery_not_used(self, feed):
        assert feed["feed_summary"]["llm_symbol_discovery_used"] is False

    def test_live_output_changed_false(self, feed):
        assert feed["live_output_changed"] is False

    def test_all_symbols_from_approved_roster_only(self, candidates):
        # Load approved roster symbols
        with open(_ROSTER_PATH) as f:
            roster_data = json.load(f)
        approved: set[str] = set()
        for entry in roster_data["rosters"]:
            approved.update(entry.get("core_symbols", []))
            approved.update(entry.get("etf_proxies", []))
        for c in candidates:
            assert c["symbol"] in approved, \
                f"{c['symbol']} not in any approved roster — LLM discovery guard failed"

    def test_semiconductors_export_blocked_does_not_affect_other_slices(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["ai_capex_growth", "yields_rising", "oil_supply_shock", "geopolitical_risk_rising"],
            "blocked_conditions": ["export_regulatory_shock"],
        })
        # semiconductors blocked; others still fire
        assert "semiconductors" not in result.theme_tailwinds
        assert "data_centre_power" in result.theme_tailwinds
        assert "banks" in result.theme_tailwinds
        assert "energy" in result.theme_tailwinds
        assert "defence" in result.theme_tailwinds


# ---------------------------------------------------------------------------
# Fixture 6 — Per-theme confidence correctly differentiated
# ---------------------------------------------------------------------------

class TestPerThemeConfidence:
    def test_banks_lower_confidence_than_semiconductors(self, candidates):
        jpm = next((c for c in candidates if c["symbol"] == "JPM"), None)
        nvda = next((c for c in candidates if c["symbol"] == "NVDA"), None)
        assert jpm is not None and nvda is not None
        assert jpm["confidence"] < nvda["confidence"], \
            "Banks (conditional rule 0.62) should have lower confidence than semiconductors (0.83)"

    def test_defence_lower_confidence_than_semiconductors(self, candidates):
        lmt = next((c for c in candidates if c["symbol"] == "LMT"), None)
        avgo = next((c for c in candidates if c["symbol"] == "AVGO"), None)
        assert lmt is not None and avgo is not None
        assert lmt["confidence"] < avgo["confidence"], \
            "Defence (0.76) should have lower confidence than semiconductors (0.83)"

    def test_second_order_confidence_decayed(self, candidates):
        ceg = next((c for c in candidates if c["symbol"] == "CEG"), None)
        assert ceg is not None
        assert ceg["role"] == "second_order_beneficiary"
        # data_centre_power base=0.82, second_order decay=0.15 → 0.67
        assert ceg["confidence"] == pytest.approx(0.67, abs=0.01)


# ---------------------------------------------------------------------------
# Fixture 7 — Quota pressure diagnostics: structural 20/20 binding
# ---------------------------------------------------------------------------

class TestQuotaPressureDiagnostics:
    def test_quota_pressure_diagnostics_present(self, shadow):
        assert "quota_pressure_diagnostics" in shadow, \
            "Day 6 must include quota_pressure_diagnostics"

    def test_structural_quota_binding(self, shadow):
        qpd = shadow["quota_pressure_diagnostics"]
        sp = qpd["structural_position"]
        assert sp["accepted"] == 20
        assert sp["capacity"] == 20
        assert sp["binding"] is True

    def test_structural_demand_exceeds_capacity(self, shadow):
        qpd = shadow["quota_pressure_diagnostics"]
        sp = qpd["structural_position"]
        assert sp["demand_total"] > sp["capacity"], \
            "Structural demand should exceed capacity due to 150 Tier D candidates"

    def test_structural_overflow_positive(self, shadow):
        qpd = shadow["quota_pressure_diagnostics"]
        sp = qpd["structural_position"]
        assert sp["overflow"] > 0

    def test_structural_demand_by_theme_has_5_themes(self, shadow):
        qpd = shadow["quota_pressure_diagnostics"]
        sp = qpd["structural_position"]
        by_theme = sp.get("demand_by_theme", {})
        assert len(by_theme) >= 5, "All 5 economic slices should have structural demand"

    def test_structural_demand_by_source_has_both_sources(self, shadow):
        qpd = shadow["quota_pressure_diagnostics"]
        sp = qpd["structural_position"]
        by_source = sp.get("demand_by_source", {})
        assert "economic_intelligence_structural" in by_source
        # Sprint 2 refactor: source_name is "tier_d_structural" (from quota_allocator)
        assert "tier_d_position_research" in by_source or "tier_d_structural" in by_source

    def test_etf_proxy_quota_not_binding(self, shadow):
        qpd = shadow["quota_pressure_diagnostics"]
        etf = qpd["etf_proxy"]
        assert etf["binding"] is False
        # Sprint 3 adds QUAL, XLP, XLV, SPLV ETF proxies; accept >= 5
        assert etf["accepted"] >= 5
        assert etf["overflow"] == 0


# ---------------------------------------------------------------------------
# Fixture 8 — Source collision report: NVDA, HIMS-like protected via manual
# ---------------------------------------------------------------------------

class TestSourceCollisionReport:
    def test_source_collision_report_present(self, shadow):
        assert "source_collision_report" in shadow, \
            "Day 6 must include source_collision_report"

    def test_source_collision_report_is_list(self, shadow):
        assert isinstance(shadow["source_collision_report"], list)

    def test_nvda_collision_protected_via_manual(self, shadow):
        scr = shadow["source_collision_report"]
        nvda = next((r for r in scr if r["symbol"] == "NVDA"), None)
        assert nvda is not None, "NVDA should appear in collision report (manual + economic + tier_d + tier_a)"
        assert nvda["final_in_shadow"] is True
        assert nvda["protected_by_manual_or_held"] is True
        assert nvda["source_path_excluded_but_symbol_preserved"] is True
        assert nvda["winning_source"] == "manual_conviction_favourites"

    def test_nvda_economic_path_excluded_but_symbol_preserved(self, shadow):
        scr = shadow["source_collision_report"]
        nvda = next((r for r in scr if r["symbol"] == "NVDA"), None)
        assert nvda is not None
        excluded = nvda.get("excluded_source_paths", [])
        assert "economic_intelligence_structural" in excluded

    def test_all_collision_report_fields_present(self, shadow):
        scr = shadow["source_collision_report"]
        required_fields = [
            "symbol", "collision_count", "attempted_by", "winning_source",
            "final_in_shadow", "protected_by_manual_or_held",
            "source_path_excluded_but_symbol_preserved",
        ]
        for entry in scr:
            for field in required_fields:
                assert field in entry, f"source_collision_report entry missing '{field}': {entry}"

    def test_no_symbol_lost_purely_to_collision(self, shadow):
        scr = shadow["source_collision_report"]
        # A symbol should never be LOST just because it had a collision — the winning source keeps it
        # Symbols can be lost only when ALL their source paths are quota-rejected or truly excluded
        for entry in scr:
            if entry["protected_by_manual_or_held"]:
                assert entry["final_in_shadow"] is True, \
                    f"{entry['symbol']}: protected by manual/held but not in shadow"


# ---------------------------------------------------------------------------
# Fixture 9 — Comparison file: new diagnostic sections present
# ---------------------------------------------------------------------------

class TestComparisonNewSections:
    def test_quota_pressure_analysis_present(self, comparison):
        assert "quota_pressure_analysis" in comparison, \
            "Comparison must include quota_pressure_analysis from Day 6"

    def test_source_collision_analysis_present(self, comparison):
        assert "source_collision_analysis" in comparison, \
            "Comparison must include source_collision_analysis from Day 6"

    def test_economic_slice_analysis_present(self, comparison):
        assert "economic_slice_analysis" in comparison, \
            "Comparison must include economic_slice_analysis from Day 6"

    def test_economic_slice_analysis_has_5_slices(self, comparison):
        esa = comparison["economic_slice_analysis"]
        # Sprint 3 adds 3 new slices; accept >= 5
        assert esa["slices_active"] >= 5
        assert len(esa["by_theme"]) >= 5

    def test_economic_slice_analysis_no_llm_discovery(self, comparison):
        esa = comparison["economic_slice_analysis"]
        assert esa["llm_symbol_discovery_used"] is False
        assert esa["macro_transmission_deterministic"] is True

    def test_source_collision_has_symbol_preserved_via_manual(self, comparison):
        sca = comparison["source_collision_analysis"]
        # NVDA and other manual favourites have source paths excluded but are preserved
        assert sca["source_path_excluded_but_symbol_preserved"] > 0

    def test_quota_pressure_structural_binding(self, comparison):
        qpa = comparison["quota_pressure_analysis"]
        sp = qpa.get("structural_position", {})
        assert sp.get("binding") is True

    def test_report_has_day6_sections(self, report):
        assert "quota_pressure_analysis" in report
        assert "source_collision_summary" in report
        assert "economic_slice_summary" in report

    def test_report_title_is_day6(self, report):
        # Title advances with each sprint; accept Day 6 or later
        title = report["report_title"]
        assert "Day 6" in title or "Day 7" in title or "Sprint 2" in title or "Sprint 3" in title, \
            f"Unexpected report title: {title}"

    def test_live_output_changed_false(self, comparison, report):
        assert comparison["live_output_changed"] is False
        assert report["live_output_changed"] is False


# ---------------------------------------------------------------------------
# All output files pass validator
# ---------------------------------------------------------------------------

class TestValidatorPassesAllDay6Files:
    def test_validate_all_passes(self):
        results = validate_all(_BASE_DIR)
        for label, result in results.items():
            assert result.ok, \
                f"Validator failed for '{label}': {result.errors}"

    def test_transmission_rules_5_rules(self):
        with open(_RULES_PATH) as f:
            data = json.load(f)
        # Sprint 3 adds 3 rules (credit_stress × 2, risk_off); accept >= 5
        assert len(data["rules"]) >= 5

    def test_theme_taxonomy_5_themes(self):
        with open(_TAXONOMY_PATH) as f:
            data = json.load(f)
        # Sprint 3 adds quality_cash_flow, defensive_quality, small_caps; accept >= 5
        assert len(data["themes"]) >= 5

    def test_thematic_roster_5_entries(self):
        with open(_ROSTER_PATH) as f:
            data = json.load(f)
        # Sprint 3 adds 3 roster entries; accept >= 5
        assert len(data["rosters"]) >= 5

    def test_all_roster_themes_in_taxonomy(self):
        with open(_TAXONOMY_PATH) as f:
            tax = json.load(f)
        with open(_ROSTER_PATH) as f:
            roster = json.load(f)
        taxonomy_ids = {t["theme_id"] for t in tax["themes"]}
        roster_ids = {r["theme_id"] for r in roster["rosters"]}
        assert roster_ids == taxonomy_ids

    def test_transmission_rules_all_valid_directions(self):
        with open(_RULES_PATH) as f:
            data = json.load(f)
        for rule in data["rules"]:
            assert rule["direction"] in ("positive", "negative", "conditional")

    def test_banks_rule_has_conditional_direction(self):
        with open(_RULES_PATH) as f:
            data = json.load(f)
        banks_rule = next(r for r in data["rules"] if r["rule_id"] == "rates_rising_to_banks_conditional")
        assert banks_rule["direction"] == "conditional"
        assert banks_rule["confidence"] == pytest.approx(0.62, abs=0.01)


# ---------------------------------------------------------------------------
# Shadow universe structural integrity
# ---------------------------------------------------------------------------

class TestShadowUniverseDay6:
    def test_total_candidates_50(self, shadow):
        assert len(shadow["candidates"]) == 50

    def test_structural_quota_20(self, shadow):
        structural = [c for c in shadow["candidates"] if c["quota"]["group"] == "structural_position"]
        assert len(structural) == 20

    def test_all_5_etf_proxies_in_shadow(self, shadow):
        etf = [c for c in shadow["candidates"] if c["quota"]["group"] == "etf_proxy"]
        # Sprint 3 adds QUAL, XLP, XLV, SPLV ETF proxies; accept >= 5
        assert len(etf) >= 5
        syms = {c["symbol"] for c in etf}
        assert "XLU" in syms  # data_centre_power proxy
        assert "SMH" in syms  # semiconductors proxy
        assert "XLF" in syms  # banks proxy
        assert "XLE" in syms  # energy proxy
        assert "ITA" in syms  # defence proxy

    def test_no_candidate_executable(self, shadow):
        for c in shadow["candidates"]:
            assert c["execution_instructions"]["executable"] is False

    def test_freshness_status_is_day6(self, shadow):
        # Advances with each sprint; accept day6 or later
        status = shadow["freshness_status"]
        assert status in (
            "static_bootstrap_day6", "static_bootstrap_day7",
            "static_bootstrap_sprint2", "static_bootstrap_sprint3",
        ), f"Unexpected freshness_status: {status}"

    def test_live_output_changed_false(self, shadow):
        assert shadow["live_output_changed"] is False


# ---------------------------------------------------------------------------
# Regression tests: Days 2-5 still pass
# ---------------------------------------------------------------------------

class TestPriorDayRegressions:
    def test_validate_all_still_passes_all_files(self):
        results = validate_all(_BASE_DIR)
        for label, result in results.items():
            assert result.ok, \
                f"Regression: Day {label} validation failed: {result.errors}"

    def test_data_centre_power_still_in_feed(self, candidates):
        syms = _symbols_for_theme(candidates, "data_centre_power")
        assert "VRT" in syms and "ETN" in syms and "PWR" in syms

    def test_manual_conviction_protected_in_shadow(self, shadow):
        manual = [c for c in shadow["candidates"] if c["quota"]["group"] == "manual_conviction"]
        assert all(c["quota"]["protected"] is True for c in manual)

    def test_attention_not_consuming_structural_quota(self, shadow):
        attention_syms = {c["symbol"] for c in shadow["candidates"] if c["quota"]["group"] == "attention"}
        structural_syms = {c["symbol"] for c in shadow["candidates"] if c["quota"]["group"] == "structural_position"}
        assert len(attention_syms & structural_syms) == 0

    def test_no_executables_anywhere(self, shadow, feed):
        for c in shadow["candidates"]:
            assert c["execution_instructions"]["executable"] is False
        # feed candidates don't have execution_instructions at the top level —
        # they're just candidate dicts. Verify live_output_changed is false.
        assert feed["live_output_changed"] is False

    def test_comparison_structural_not_displaced_by_attention(self, comparison):
        struct = comparison["structural_candidate_analysis"]
        assert struct["structural_candidates_displaced_by_attention"] is False

    def test_comparison_attention_cap_respected(self, comparison):
        attn = comparison["attention_analysis"]
        assert attn["attention_cap_respected"] is True

    def test_comparison_llm_discovery_false(self, comparison):
        econ = comparison["economic_intelligence_analysis"]
        assert econ["llm_symbol_discovery_used"] is False
