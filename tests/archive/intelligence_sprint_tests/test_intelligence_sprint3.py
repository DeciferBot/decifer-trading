"""
tests/test_intelligence_sprint3.py — Sprint 3 acceptance tests.

Covers:
  - Credit stress → quality_cash_flow tailwind
  - Credit stress → small_caps headwind (pressure_candidate)
  - Risk-off rotation → defensive_quality tailwind
  - Candidates from approved rosters only (quality, defensive, small_caps)
  - Headwind candidates: not executable, not in structural quota, route=watchlist
  - ETF proxies route to watchlist
  - Route tagger handles pressure_candidate correctly
  - Reports include route_metric_distinction and risk_off_analysis
  - live_output_changed = False throughout
  - Prior sprint tests still pass
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macro_transmission_matrix import MacroTransmissionMatrix
from candidate_resolver import CandidateResolver, generate_feed
from route_tagger import RouteContext, assign_route

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INTEL_DIR = os.path.join(_REPO, "data", "intelligence")
_UB_DIR = os.path.join(_REPO, "data", "universe_builder")
_RULES_PATH = os.path.join(_INTEL_DIR, "transmission_rules.json")
_TAXONOMY_PATH = os.path.join(_INTEL_DIR, "theme_taxonomy.json")
_ROSTER_PATH = os.path.join(_INTEL_DIR, "thematic_roster.json")
_FEED_PATH = os.path.join(_INTEL_DIR, "economic_candidate_feed.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")
_COMPARISON_PATH = os.path.join(_UB_DIR, "current_vs_shadow_comparison.json")
_REPORT_PATH = os.path.join(_UB_DIR, "universe_builder_report.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _feed_candidates() -> list[dict]:
    return _load(_FEED_PATH).get("candidates", [])


def _shadow_candidates() -> list[dict]:
    return _load(_SHADOW_PATH).get("candidates", [])


# ---------------------------------------------------------------------------
# 1. Credit stress activates quality_cash_flow tailwind
# ---------------------------------------------------------------------------

class TestCreditStressQualityCashFlow:

    def test_credit_stress_fires_quality_cash_flow_rule(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["credit_stress_rising"],
            "blocked_conditions": [],
        })
        rule_ids = [r.rule_id for r in result.transmission_rules_fired]
        assert "credit_stress_to_quality_cash_flow" in rule_ids

    def test_quality_cash_flow_in_tailwinds(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["credit_stress_rising"],
            "blocked_conditions": [],
        })
        assert "quality_cash_flow" in result.theme_tailwinds

    def test_quality_cash_flow_candidates_in_feed(self):
        candidates = _feed_candidates()
        themes = {c["theme"] for c in candidates}
        assert "quality_cash_flow" in themes

    def test_quality_cash_flow_symbols_from_approved_roster(self):
        with open(_ROSTER_PATH) as f:
            roster = json.load(f)
        qcf_roster = next(r for r in roster["rosters"] if r["theme_id"] == "quality_cash_flow")
        approved = set(qcf_roster.get("core_symbols", [])) | set(qcf_roster.get("etf_proxies", []))
        candidates = [c for c in _feed_candidates() if c["theme"] == "quality_cash_flow"]
        for c in candidates:
            assert c["symbol"] in approved, \
                f"{c['symbol']} not in quality_cash_flow approved roster"


# ---------------------------------------------------------------------------
# 2. Credit stress creates small_caps headwind (pressure_candidate)
# ---------------------------------------------------------------------------

class TestCreditStressSmallCapsHeadwind:

    def test_credit_stress_fires_small_caps_headwind_rule(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["credit_stress_rising"],
            "blocked_conditions": [],
        })
        rule_ids = [r.rule_id for r in result.transmission_rules_fired]
        assert "credit_stress_to_small_caps_headwind" in rule_ids

    def test_small_caps_in_theme_headwinds(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["credit_stress_rising"],
            "blocked_conditions": [],
        })
        assert "small_caps" in result.theme_headwinds

    def test_small_caps_headwind_candidates_are_pressure_candidate_role(self):
        candidates = [c for c in _feed_candidates() if c["theme"] == "small_caps"]
        assert len(candidates) >= 1
        for c in candidates:
            assert c["role"] == "pressure_candidate", \
                f"{c['symbol']} should be pressure_candidate but got {c['role']}"

    def test_iwm_is_pressure_candidate(self):
        candidates = _feed_candidates()
        iwm = next((c for c in candidates if c["symbol"] == "IWM"), None)
        assert iwm is not None, "IWM should appear as small_caps headwind candidate"
        assert iwm["role"] == "pressure_candidate"
        assert iwm["route_hint"] == ["watchlist"]


# ---------------------------------------------------------------------------
# 3. Risk-off rotation activates defensive_quality
# ---------------------------------------------------------------------------

class TestRiskOffDefensiveQuality:

    def test_risk_off_fires_defensive_quality_rule(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["risk_off_rotation"],
            "blocked_conditions": [],
        })
        rule_ids = [r.rule_id for r in result.transmission_rules_fired]
        assert "risk_off_rotation_to_defensive_quality" in rule_ids

    def test_defensive_quality_in_tailwinds(self):
        matrix = MacroTransmissionMatrix(rules_path=_RULES_PATH)
        result = matrix.fire({
            "active_drivers": ["risk_off_rotation"],
            "blocked_conditions": [],
        })
        assert "defensive_quality" in result.theme_tailwinds

    def test_defensive_quality_candidates_in_feed(self):
        candidates = _feed_candidates()
        themes = {c["theme"] for c in candidates}
        assert "defensive_quality" in themes

    def test_defensive_quality_symbols_from_approved_roster(self):
        with open(_ROSTER_PATH) as f:
            roster = json.load(f)
        dq_roster = next(r for r in roster["rosters"] if r["theme_id"] == "defensive_quality")
        approved = set(dq_roster.get("core_symbols", [])) | set(dq_roster.get("etf_proxies", []))
        candidates = [c for c in _feed_candidates() if c["theme"] == "defensive_quality"]
        for c in candidates:
            assert c["symbol"] in approved, \
                f"{c['symbol']} not in defensive_quality approved roster"


# ---------------------------------------------------------------------------
# 4. Headwind candidates: not executable, watchlist-only, not structural quota
# ---------------------------------------------------------------------------

class TestHeadwindCandidateConstraints:

    def test_headwind_candidates_not_executable(self):
        shadow = _load(_SHADOW_PATH)
        headwind = [
            c for c in shadow["candidates"]
            if c.get("transmission_direction") == "headwind"
        ]
        assert len(headwind) >= 1, "Expected at least one headwind candidate (IWM)"
        for c in headwind:
            assert c["execution_instructions"]["executable"] is False, \
                f"{c['symbol']} headwind candidate must not be executable"

    def test_headwind_candidates_route_watchlist(self):
        shadow = _load(_SHADOW_PATH)
        headwind = [
            c for c in shadow["candidates"]
            if c.get("transmission_direction") == "headwind"
        ]
        for c in headwind:
            assert c["route"] == "watchlist", \
                f"{c['symbol']} headwind candidate must route to watchlist only"

    def test_headwind_candidates_not_in_structural_quota(self):
        shadow = _load(_SHADOW_PATH)
        headwind = [
            c for c in shadow["candidates"]
            if c.get("transmission_direction") == "headwind"
        ]
        for c in headwind:
            assert c["quota"]["group"] != "structural_position", \
                f"{c['symbol']} headwind candidate must not consume structural quota"

    def test_feed_headwind_candidates_executable_false(self):
        feed = _load(_FEED_PATH)
        assert feed["feed_summary"]["headwind_candidates_executable"] is False


# ---------------------------------------------------------------------------
# 5. Route tagger handles pressure_candidate correctly (Rule 9)
# ---------------------------------------------------------------------------

class TestRouteTaggerPressureCandidate:

    def test_pressure_candidate_routes_to_watchlist(self):
        ctx = RouteContext(
            symbol="IWM",
            reason_to_care="headwind_pressure_watchlist",
            source_labels=["intelligence_first_static_rule"],
            role="pressure_candidate",
            theme="small_caps",
            driver="credit_stress_rising",
            is_held=False,
            is_manual_conviction=False,
            route_hint=["watchlist"],
            bucket_type="attention",
        )
        decision = assign_route(ctx)
        assert decision.route == "watchlist"

    def test_pressure_candidate_allowed_routes_watchlist_only(self):
        ctx = RouteContext(
            symbol="IWM",
            reason_to_care="headwind_pressure_watchlist",
            source_labels=[],
            role="pressure_candidate",
            theme="small_caps",
            driver="credit_stress_rising",
            is_held=False,
            is_manual_conviction=False,
            route_hint=["watchlist"],
            bucket_type="attention",
        )
        decision = assign_route(ctx)
        assert decision.allowed_routes == ["watchlist"]

    def test_pressure_candidate_live_output_changed_false(self):
        ctx = RouteContext(
            symbol="IWM",
            reason_to_care="headwind_pressure_watchlist",
            source_labels=[],
            role="pressure_candidate",
            theme="small_caps",
            driver="credit",
            is_held=False,
            is_manual_conviction=False,
            route_hint=["watchlist"],
            bucket_type="attention",
        )
        decision = assign_route(ctx)
        assert decision.live_output_changed is False


# ---------------------------------------------------------------------------
# 6. ETF proxies route to watchlist (quality_cash_flow + defensive_quality)
# ---------------------------------------------------------------------------

class TestSprintThreeEtfProxies:

    def test_qual_is_etf_proxy_in_feed(self):
        candidates = _feed_candidates()
        qual = next((c for c in candidates if c["symbol"] == "QUAL"), None)
        assert qual is not None, "QUAL should appear in feed as quality_cash_flow ETF proxy"
        assert qual["role"] == "etf_proxy"
        assert qual["route_hint"] == ["watchlist"]

    def test_xlp_xlv_splv_are_etf_proxies(self):
        candidates = _feed_candidates()
        etf_syms = {c["symbol"] for c in candidates if c["role"] == "etf_proxy"}
        for sym in ("XLP", "XLV", "SPLV"):
            assert sym in etf_syms, f"{sym} should be an etf_proxy candidate in feed"

    def test_sprint3_etf_proxies_in_shadow_watchlist(self):
        shadow = _load(_SHADOW_PATH)
        sprint3_etf_syms = {"QUAL", "XLP", "XLV", "SPLV"}
        in_shadow = {
            c["symbol"] for c in shadow["candidates"]
            if c["symbol"] in sprint3_etf_syms
        }
        # At least some should be in shadow (capped at 10 total ETF proxies)
        assert len(in_shadow) >= 1, \
            f"Expected sprint3 ETF proxies in shadow, got none"
        for sym in in_shadow:
            c = next(c for c in shadow["candidates"] if c["symbol"] == sym)
            assert c["route"] == "watchlist", f"{sym} ETF proxy must route to watchlist"


# ---------------------------------------------------------------------------
# 7. No LLM discovery, no raw news, no broad intraday scan
# ---------------------------------------------------------------------------

class TestNoForbiddenPaths:

    def test_no_llm_symbol_discovery_in_feed(self):
        feed = _load(_FEED_PATH)
        assert feed["feed_summary"]["llm_symbol_discovery_used"] is False

    def test_no_raw_news_in_feed(self):
        feed = _load(_FEED_PATH)
        assert feed["feed_summary"]["raw_news_used"] is False

    def test_no_broad_intraday_scan_in_feed(self):
        feed = _load(_FEED_PATH)
        assert feed["feed_summary"]["broad_intraday_scan_used"] is False

    def test_live_output_changed_false_feed(self):
        assert _load(_FEED_PATH)["live_output_changed"] is False

    def test_live_output_changed_false_shadow(self):
        assert _load(_SHADOW_PATH)["live_output_changed"] is False

    def test_live_output_changed_false_comparison(self):
        assert _load(_COMPARISON_PATH)["live_output_changed"] is False

    def test_live_output_changed_false_report(self):
        assert _load(_REPORT_PATH)["live_output_changed"] is False


# ---------------------------------------------------------------------------
# 8. Reports include route_metric_distinction
# ---------------------------------------------------------------------------

class TestRouteMetricDistinction:

    def test_shadow_universe_summary_has_route_metric_keys(self):
        shadow = _load(_SHADOW_PATH)
        us = shadow["universe_summary"]
        for key in (
            "position_route_count",
            "structural_quota_group_count",
            "structural_reason_to_care_count",
            "tier_d_structural_source_count",
            "structural_watchlist_count",
            "structural_swing_count",
        ):
            assert key in us, f"universe_summary missing key: {key}"

    def test_structural_quota_count_geq_position_route_count(self):
        us = _load(_SHADOW_PATH)["universe_summary"]
        # structural_quota_group includes swing-routed structural candidates
        assert us["structural_quota_group_count"] >= us["position_route_count"]

    def test_structural_swing_count_positive(self):
        # Banks, energy, defence are structural quota but route=swing
        us = _load(_SHADOW_PATH)["universe_summary"]
        assert us["structural_swing_count"] > 0, \
            "structural_swing_count should be > 0 (banks/energy/defence are structural+swing)"

    def test_report_includes_route_metric_distinction(self):
        report = _load(_REPORT_PATH)
        assert "route_metric_distinction" in report


# ---------------------------------------------------------------------------
# 9. Reports include risk_off_analysis
# ---------------------------------------------------------------------------

class TestRiskOffAnalysis:

    def test_comparison_has_risk_off_analysis(self):
        assert "risk_off_analysis" in _load(_COMPARISON_PATH)

    def test_risk_off_analysis_headwind_not_executable(self):
        roa = _load(_COMPARISON_PATH)["risk_off_analysis"]
        assert roa["headwind_candidates_executable"] is False

    def test_risk_off_analysis_has_expected_keys(self):
        roa = _load(_COMPARISON_PATH)["risk_off_analysis"]
        for key in (
            "quality_cash_flow_candidates_generated",
            "defensive_quality_candidates_generated",
            "small_caps_headwind_candidates_generated",
            "candidates_in_shadow",
            "headwind_candidates_in_shadow",
            "headwind_candidates_executable",
            "risk_off_symbols_preserved",
        ):
            assert key in roa, f"risk_off_analysis missing key: {key}"

    def test_report_includes_risk_off_analysis(self):
        report = _load(_REPORT_PATH)
        assert "risk_off_analysis" in report

    def test_report_title_is_sprint3(self):
        title = _load(_REPORT_PATH)["report_title"]
        assert "Sprint 3" in title, f"Expected Sprint 3 in title, got: {title}"
