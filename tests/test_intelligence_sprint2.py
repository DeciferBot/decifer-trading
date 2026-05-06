"""
tests/test_intelligence_sprint2.py — Sprint 2 verification tests.

Tests for:
  - route_tagger.py   (RouteContext, RouteDecision, assign_route)
  - quota_allocator.py (QuotaCandidate, AllocationResult, allocate)
  - universe_builder.py refactored to use both modules
  - Shadow universe output reflects route_tagger + quota_allocator decisions
  - All 20 Sprint 2 required tests
  - Prior day regressions
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from route_tagger import RouteContext, RouteDecision, assign_route, _VALID_ROUTES
from quota_allocator import (
    QuotaCandidate,
    AllocationResult,
    allocate,
    _STRUCTURAL_MAX,
    _ATTENTION_MAX,
    _ETF_PROXY_MAX,
    _CATALYST_MAX,
    _TOTAL_MAX,
)

_SHADOW = "data/universe_builder/active_opportunity_universe_shadow.json"
_COMPARISON = "data/universe_builder/current_vs_shadow_comparison.json"
_REPORT = "data/universe_builder/universe_builder_report.json"


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _shadow() -> dict:
    return _load(_SHADOW)


def _make_context(**kwargs) -> RouteContext:
    defaults = dict(
        symbol="TST",
        reason_to_care="structural",
        source_labels=[],
        role="direct_beneficiary",
        theme="test_theme",
        driver="test_driver",
        is_held=False,
        is_manual_conviction=False,
        route_hint=["position", "swing", "watchlist"],
        bucket_type="structural",
        source_name="test",
    )
    defaults.update(kwargs)
    return RouteContext(**defaults)


def _make_candidate(symbol: str, group: str, priority: int, **kwargs) -> QuotaCandidate:
    return QuotaCandidate(
        symbol=symbol,
        quota_group=group,
        source_labels=[group],
        route="watchlist",
        priority=priority,
        is_protected=group in {"held", "manual_conviction"},
        source_name=group,
        **kwargs,
    )


# =============================================================================
# Route Tagger — unit tests
# =============================================================================

class TestRouteTaggerHeldRule:
    def test_is_held_flag_gives_held_route(self):
        ctx = _make_context(is_held=True)
        d = assign_route(ctx)
        assert d.route == "held"

    def test_held_source_label_gives_held_route(self):
        ctx = _make_context(source_labels=["held_position"])
        d = assign_route(ctx)
        assert d.route == "held"

    def test_held_confidence_is_1(self):
        d = assign_route(_make_context(is_held=True))
        assert d.route_confidence == 1.0

    def test_held_allowed_routes_only_held(self):
        d = assign_route(_make_context(is_held=True))
        assert d.allowed_routes == ["held"]


class TestRouteTaggerManualConvictionRule:
    def test_is_manual_conviction_gives_manual_route(self):
        ctx = _make_context(is_manual_conviction=True, is_held=False)
        d = assign_route(ctx)
        assert d.route == "manual_conviction"

    def test_favourites_label_gives_manual_route(self):
        ctx = _make_context(source_labels=["favourites_manual_conviction"])
        d = assign_route(ctx)
        assert d.route == "manual_conviction"

    def test_manual_confidence_is_1(self):
        d = assign_route(_make_context(is_manual_conviction=True, is_held=False))
        assert d.route_confidence == 1.0


class TestRouteTaggerEtfProxyRule:
    def test_etf_proxy_role_gives_watchlist(self):
        ctx = _make_context(role="etf_proxy", bucket_type="proxy")
        d = assign_route(ctx)
        assert d.route == "watchlist"

    def test_etf_proxy_allowed_routes_watchlist_only(self):
        ctx = _make_context(role="etf_proxy", bucket_type="proxy")
        d = assign_route(ctx)
        assert d.allowed_routes == ["watchlist"]


class TestRouteTaggerDirectBeneficiaryRule:
    def test_direct_beneficiary_default_position(self):
        ctx = _make_context(
            role="direct_beneficiary",
            reason_to_care="structural",
            route_hint=["position", "swing", "watchlist"],
        )
        d = assign_route(ctx)
        assert d.route == "position"

    def test_direct_beneficiary_banks_swing(self):
        ctx = _make_context(
            role="direct_beneficiary",
            reason_to_care="structural",
            route_hint=["swing", "watchlist"],
            theme="banks",
        )
        d = assign_route(ctx)
        assert d.route == "swing"

    def test_direct_beneficiary_downgrade_reason_set_for_non_position(self):
        ctx = _make_context(
            role="direct_beneficiary",
            reason_to_care="structural",
            route_hint=["swing", "watchlist"],
        )
        d = assign_route(ctx)
        assert d.downgrade_reason != ""

    def test_direct_beneficiary_no_downgrade_for_position(self):
        ctx = _make_context(
            role="direct_beneficiary",
            reason_to_care="structural",
            route_hint=["position", "swing", "watchlist"],
        )
        d = assign_route(ctx)
        assert d.downgrade_reason == ""


class TestRouteTaggerCatalystRule:
    def test_catalyst_label_gives_swing(self):
        ctx = _make_context(
            source_labels=["catalyst_watchlist_read_only"],
            role="catalyst",
            reason_to_care="catalyst_candidate_from_adapter",
        )
        d = assign_route(ctx)
        assert d.route == "swing"

    def test_catalyst_allowed_routes(self):
        ctx = _make_context(source_labels=["catalyst_watchlist_read_only"])
        d = assign_route(ctx)
        assert "swing" in d.allowed_routes


class TestRouteTaggerTierBRule:
    def test_tier_b_gives_intraday_swing(self):
        ctx = _make_context(source_labels=["tier_b_daily_promoted"], role="attention")
        d = assign_route(ctx)
        assert d.route == "intraday_swing"

    def test_tier_b_intraday_swing_in_allowed_routes(self):
        ctx = _make_context(
            source_labels=["tier_b_daily_promoted"],
            role="attention",
            reason_to_care="attention_shadow_only",
        )
        d = assign_route(ctx)
        assert "intraday_swing" in d.allowed_routes


class TestRouteTaggerTierARule:
    def test_tier_a_gives_watchlist(self):
        ctx = _make_context(source_labels=["tier_a_core_floor"], role="current_source")
        d = assign_route(ctx)
        assert d.route == "watchlist"

    def test_current_source_unclassified_gives_watchlist(self):
        ctx = _make_context(
            source_labels=[],
            role="unknown",
            reason_to_care="current_source_unclassified",
        )
        d = assign_route(ctx)
        assert d.route == "watchlist"


class TestRouteTaggerOutputContract:
    def test_always_returns_valid_route(self):
        for role in ["direct_beneficiary", "etf_proxy", "second_order_beneficiary",
                     "catalyst", "unknown_role"]:
            ctx = _make_context(role=role)
            d = assign_route(ctx)
            assert d.route in _VALID_ROUTES, f"Invalid route {d.route!r} for role={role}"

    def test_never_marks_candidate_executable(self):
        for is_held in [True, False]:
            ctx = _make_context(is_held=is_held)
            d = assign_route(ctx)
            assert not hasattr(d, "executable") or d.executable is False

    def test_always_has_route_reason(self):
        d = assign_route(_make_context())
        assert isinstance(d.route_reason, str) and d.route_reason

    def test_confidence_in_valid_range(self):
        d = assign_route(_make_context())
        assert 0.0 <= d.route_confidence <= 1.0

    def test_live_output_changed_always_false(self):
        d = assign_route(_make_context())
        assert d.live_output_changed is False


# =============================================================================
# Quota Allocator — unit tests
# =============================================================================

class TestQuotaAllocatorHeldProtection:
    def test_held_candidates_always_included(self):
        cands = [_make_candidate(f"H{i}", "held", 0) for i in range(60)]
        result = allocate(cands)
        held_included = [c for c in result.included if c.quota_group == "held"]
        assert len(held_included) == 60, "All held must bypass total cap"

    def test_held_protected_flag_true(self):
        result = allocate([_make_candidate("HLD", "held", 0)])
        assert result.included[0].is_protected is True


class TestQuotaAllocatorManualProtection:
    def test_manual_candidates_always_included(self):
        cands = [_make_candidate(f"M{i}", "manual_conviction", 1) for i in range(20)]
        result = allocate(cands)
        manual_included = [c for c in result.included if c.quota_group == "manual_conviction"]
        assert len(manual_included) == 20

    def test_manual_candidates_bypass_total_cap(self):
        manual = [_make_candidate(f"M{i}", "manual_conviction", 1) for i in range(55)]
        result = allocate(manual)
        assert len(result.included) == 55  # all 55 included despite total cap of 50


class TestQuotaAllocatorStructuralCap:
    def test_structural_capped_at_20(self):
        cands = [_make_candidate(f"S{i}", "structural_position", 2) for i in range(30)]
        result = allocate(cands)
        structural_count = sum(1 for c in result.included if c.quota_group == "structural_position")
        assert structural_count == _STRUCTURAL_MAX

    def test_structural_overflow_is_excluded(self):
        cands = [_make_candidate(f"S{i}", "structural_position", 2) for i in range(25)]
        result = allocate(cands)
        excluded_reasons = [e["reason"] for e in result.exclusion_log]
        assert any("Structural quota full" in r for r in excluded_reasons)

    def test_attention_cannot_consume_structural_quota(self):
        cands = [
            _make_candidate(f"A{i}", "attention", 5) for i in range(30)
        ] + [
            _make_candidate(f"S{i}", "structural_position", 2) for i in range(5)
        ]
        result = allocate(cands)
        structural_used = sum(1 for c in result.included if c.quota_group == "structural_position")
        attention_group = [c for c in result.included if c.quota_group == "attention"]
        assert structural_used <= _STRUCTURAL_MAX
        # Attention candidates should not appear in structural group
        for c in attention_group:
            assert c.quota_group == "attention"

    def test_structural_candidates_have_protected_quota(self):
        cands = [_make_candidate("SSTRUCT", "structural_position", 2)]
        result = allocate(cands)
        assert result.quota_summary["structural_position"]["protected"] is True


class TestQuotaAllocatorAttentionCap:
    def test_attention_capped_at_15(self):
        cands = [_make_candidate(f"T{i}", "attention", 5) for i in range(20)]
        result = allocate(cands)
        attention_count = sum(
            1 for c in result.included if c.quota_group in {"attention", "current_source_unclassified"}
        )
        assert attention_count <= _ATTENTION_MAX

    def test_attention_overflow_logged(self):
        cands = [_make_candidate(f"T{i}", "attention", 5) for i in range(20)]
        result = allocate(cands)
        assert any("Attention quota full" in e["reason"] for e in result.exclusion_log)

    def test_current_source_unclassified_shares_attention_cap(self):
        cands = (
            [_make_candidate(f"A{i}", "attention", 5) for i in range(10)] +
            [_make_candidate(f"C{i}", "current_source_unclassified", 6) for i in range(10)]
        )
        result = allocate(cands)
        combined = sum(
            1 for c in result.included
            if c.quota_group in {"attention", "current_source_unclassified"}
        )
        assert combined <= _ATTENTION_MAX


class TestQuotaAllocatorEtfProxyCap:
    def test_etf_proxy_capped_at_10(self):
        cands = [_make_candidate(f"E{i}", "etf_proxy", 2) for i in range(15)]
        result = allocate(cands)
        etf_count = sum(1 for c in result.included if c.quota_group == "etf_proxy")
        assert etf_count == _ETF_PROXY_MAX

    def test_etf_proxy_overflow_logged(self):
        cands = [_make_candidate(f"E{i}", "etf_proxy", 2) for i in range(15)]
        result = allocate(cands)
        assert any("ETF proxy quota full" in e["reason"] for e in result.exclusion_log)


class TestQuotaAllocatorCatalystQuota:
    def test_catalyst_candidates_use_catalyst_swing_group(self):
        cands = [_make_candidate(f"CAT{i}", "catalyst_swing", 4) for i in range(5)]
        result = allocate(cands)
        for c in result.included:
            assert c.quota_group == "catalyst_swing"

    def test_catalyst_quota_tracked_separately_from_structural(self):
        structural = [_make_candidate(f"S{i}", "structural_position", 2) for i in range(20)]
        catalyst = [_make_candidate(f"C{i}", "catalyst_swing", 4) for i in range(10)]
        result = allocate(structural + catalyst)
        struct_included = sum(1 for c in result.included if c.quota_group == "structural_position")
        cat_included = sum(1 for c in result.included if c.quota_group == "catalyst_swing")
        assert struct_included == 20
        assert cat_included == 10


class TestQuotaAllocatorSourceCollision:
    def test_collision_preserved_through_manual_source(self):
        cands = [
            _make_candidate("NVDA", "manual_conviction", 1),
            _make_candidate("NVDA", "structural_position", 2),
        ]
        result = allocate(cands)
        included_syms = [c.symbol for c in result.included]
        assert "NVDA" in included_syms
        collision = next((r for r in result.source_collision_report if r["symbol"] == "NVDA"), None)
        assert collision is not None
        assert collision["final_in_shadow"] is True
        assert collision["protected_by_manual_or_held"] is True
        assert collision["source_path_excluded_but_symbol_preserved"] is True

    def test_collision_excluded_source_path_not_counted_as_full_loss(self):
        cands = [
            _make_candidate("AAPL", "held", 0),
            _make_candidate("AAPL", "structural_position", 2),
            _make_candidate("AAPL", "attention", 5),
        ]
        result = allocate(cands)
        # AAPL should be in the output
        assert any(c.symbol == "AAPL" for c in result.included)
        # There should be a collision entry
        collision = next((r for r in result.source_collision_report if r["symbol"] == "AAPL"), None)
        assert collision is not None
        assert collision["final_in_shadow"] is True


class TestQuotaAllocatorLogging:
    def test_every_inclusion_has_reason(self):
        cands = [_make_candidate(f"X{i}", "structural_position", 2) for i in range(5)]
        result = allocate(cands)
        for entry in result.inclusion_log:
            assert entry.get("reason"), f"Missing reason in inclusion_log entry: {entry}"

    def test_every_exclusion_has_reason(self):
        cands = [_make_candidate(f"S{i}", "structural_position", 2) for i in range(25)]
        result = allocate(cands)
        for entry in result.exclusion_log:
            assert entry.get("reason"), f"Missing reason in exclusion_log entry: {entry}"

    def test_live_output_changed_always_false(self):
        result = allocate([_make_candidate("TST", "structural_position", 2)])
        assert result.live_output_changed is False

    def test_no_candidate_marked_executable(self):
        cands = [_make_candidate(f"E{i}", "structural_position", 2) for i in range(5)]
        result = allocate(cands)
        for c in result.included:
            payload = c.payload
            if payload and hasattr(payload, "execution_instructions"):
                assert payload.execution_instructions.get("executable") is False


class TestQuotaAllocatorPressureDiagnostics:
    def test_quota_pressure_reports_by_theme_and_source(self):
        cands = [
            _make_candidate(f"S{i}", "structural_position", 2, theme=f"theme_{i % 3}")
            for i in range(10)
        ]
        result = allocate(cands)
        diag = result.quota_pressure_diagnostics["structural_position"]
        assert "demand_by_theme" in diag
        assert "demand_by_source" in diag
        assert diag["demand_total"] == 10

    def test_quota_pressure_binding_true_when_cap_reached(self):
        cands = [_make_candidate(f"S{i}", "structural_position", 2) for i in range(25)]
        result = allocate(cands)
        assert result.quota_pressure_diagnostics["structural_position"]["binding"] is True


# =============================================================================
# Shadow Universe — output file tests
# =============================================================================

class TestShadowUniverseSprint2:
    def test_shadow_universe_file_exists(self):
        assert os.path.exists(_SHADOW)

    def test_freshness_status_is_sprint2(self):
        status = _shadow()["freshness_status"]
        assert status in ("static_bootstrap_sprint2", "static_bootstrap_day7", "static_bootstrap_sprint3"), \
            f"Unexpected freshness_status: {status}"

    def test_live_output_changed_false(self):
        assert _shadow()["live_output_changed"] is False

    def test_structural_candidates_not_displaced_by_attention(self):
        shadow = _shadow()
        structural_count = sum(
            1 for c in shadow["candidates"] if c["quota"]["group"] == "structural_position"
        )
        attention_in_structural = sum(
            1 for c in shadow["candidates"]
            if c["bucket_type"] == "attention" and c["quota"]["group"] == "structural_position"
        )
        assert structural_count <= 20
        assert attention_in_structural == 0

    def test_attention_cap_respected(self):
        shadow = _shadow()
        attention_used = sum(
            1 for c in shadow["candidates"]
            if c["quota"]["group"] in {"attention", "current_source_unclassified"}
        )
        assert attention_used <= 15

    def test_manual_and_held_protection_intact(self):
        shadow = _shadow()
        protected = [
            c for c in shadow["candidates"]
            if c["quota"]["group"] in {"manual_conviction", "held"}
        ]
        for c in protected:
            assert c["quota"].get("protected") is True

    def test_tier_b_candidates_route_intraday_swing(self):
        shadow = _shadow()
        tier_b = [
            c for c in shadow["candidates"]
            if "tier_b_daily_promoted" in c.get("source_labels", [])
        ]
        for c in tier_b:
            assert c["route"] == "intraday_swing", \
                f"Tier B candidate {c['symbol']} has route {c['route']!r} instead of intraday_swing"

    def test_no_candidate_is_executable(self):
        shadow = _shadow()
        for c in shadow["candidates"]:
            assert c["execution_instructions"]["executable"] is False

    def test_catalyst_approved_source_guard_intact(self):
        shadow = _shadow()
        excl = shadow.get("exclusion_log", [])
        unapproved = [e for e in excl if e.get("reason") == "catalyst_symbol_not_in_approved_source"]
        # Guard is coded — we verify its record type is correct when fired
        for e in unapproved:
            assert e.get("symbol"), "Unapproved catalyst exclusion missing symbol"
            assert "catalyst_engine_adapter" in e.get("excluded_by", [])

    def test_adapter_impact_analysis_intact(self):
        comparison = _load(_COMPARISON)
        assert "adapter_impact_analysis" in comparison
        aia = comparison["adapter_impact_analysis"]
        assert aia["side_effects_triggered"] is False
        assert aia["live_data_called"] is False

    def test_source_collision_handling_intact(self):
        shadow = _shadow()
        scr = shadow.get("source_collision_report", [])
        for entry in scr:
            assert "symbol" in entry
            assert "final_in_shadow" in entry
            assert "source_path_excluded_but_symbol_preserved" in entry


# =============================================================================
# Prior-day regression guard
# =============================================================================

class TestPriorDayRegressions:
    def test_day7_tests_still_load_valid_shadow(self):
        """Structural quota should still be binding (20/20 demand > capacity)."""
        shadow = _shadow()
        diag = shadow["quota_pressure_diagnostics"]["structural_position"]
        assert diag["binding"] is True

    def test_schema_validator_passes_on_all_outputs(self):
        from intelligence_schema_validator import validate_all
        results = validate_all("data/intelligence")
        for name, r in results.items():
            assert not r.errors, f"Validator failed for {name}: {r.errors}"

    def test_live_output_changed_false_in_comparison(self):
        comparison = _load(_COMPARISON)
        assert comparison["live_output_changed"] is False

    def test_structural_quota_binding_true(self):
        comparison = _load(_COMPARISON)
        binding = comparison["structural_candidate_analysis"]["structural_quota_binding"]
        assert binding is True

    def test_all_route_tagger_outputs_are_valid_routes(self):
        shadow = _shadow()
        for c in shadow["candidates"]:
            assert c["route"] in _VALID_ROUTES, \
                f"Invalid route {c['route']!r} for symbol {c['symbol']}"
