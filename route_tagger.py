"""
route_tagger.py — deterministic route assignment for the Intelligence-First architecture.

Single responsibility: assign a route tag to a candidate based on its
reason_to_care, source_labels, role, route_hint, and protection status.

Pure function — no side effects, no network calls, no live data, no LLMs.
No candidate is ever marked executable by this module.

Public surface:
    RouteContext    — input to route assignment
    RouteDecision   — output of route assignment
    assign_route()  — pure deterministic function
"""

from __future__ import annotations

from dataclasses import dataclass, field

_VALID_ROUTES = frozenset({
    "position", "swing", "intraday_swing", "watchlist",
    "held", "manual_conviction", "do_not_touch",
})

_HELD_SOURCE_LABELS = frozenset({"held_position", "held_positions"})
_MANUAL_SOURCE_LABELS = frozenset({"favourites_manual_conviction", "manual_conviction"})
_TIER_B_SOURCE_LABELS = frozenset({"tier_b_daily_promoted", "tier_b"})
_TIER_A_SOURCE_LABELS = frozenset({"tier_a_core_floor", "tier_a"})
_CATALYST_SOURCE_LABELS = frozenset({
    "catalyst_watchlist_read_only", "catalyst_engine", "catalyst",
})
_STRUCTURAL_REASONS = frozenset({
    "structural", "structural_candidate_source", "structural_or_catalyst_watch",
})


@dataclass
class RouteContext:
    symbol: str
    reason_to_care: str
    source_labels: list[str]
    role: str               # direct_beneficiary, second_order_beneficiary, etf_proxy, etc.
    theme: str
    driver: str
    is_held: bool
    is_manual_conviction: bool
    route_hint: list[str]   # ordered: preferred route first
    bucket_type: str        # structural, catalyst, attention, proxy, held, manual
    source_name: str = ""


@dataclass
class RouteDecision:
    route: str
    route_reason: str
    route_confidence: float     # 0.0–1.0
    allowed_routes: list[str]
    required_confirmations: list[str]
    downgrade_reason: str = ""
    live_output_changed: bool = False


def _first_valid_hint(hints: list[str]) -> str:
    for h in hints:
        if h in _VALID_ROUTES:
            return h
    return "watchlist"


def assign_route(ctx: RouteContext) -> RouteDecision:
    """
    Assign a deterministic route to a candidate. Pure function — no side effects.

    Rules (evaluated in priority order):
      1. Held source → held
      2. Manual conviction / favourites → manual_conviction
      3. ETF proxy → watchlist
      4. Direct beneficiary (structural) → route_hint[0] (position or swing per theme)
      5. Second-order beneficiary → route_hint[0] (typically swing)
      6. Catalyst adapter → swing
      7. Tier B attention source → intraday_swing
      8. Tier A / unclassified current source → watchlist
      9. Pressure candidate (headwind monitoring) → watchlist (never executable)
     10. do_not_touch → do_not_touch
     11. Fallback → watchlist
    """
    labels = set(ctx.source_labels or [])

    # Rule 1 — held source (always protected)
    if ctx.is_held or labels & _HELD_SOURCE_LABELS:
        return RouteDecision(
            route="held",
            route_reason="Symbol is a held position — protected, do not touch",
            route_confidence=1.0,
            allowed_routes=["held"],
            required_confirmations=[],
        )

    # Rule 2 — manual conviction / favourites
    if ctx.is_manual_conviction or labels & _MANUAL_SOURCE_LABELS:
        return RouteDecision(
            route="manual_conviction",
            route_reason="Symbol is in manual conviction / favourites list — protected",
            route_confidence=1.0,
            allowed_routes=["manual_conviction", "position", "swing", "watchlist"],
            required_confirmations=[],
        )

    # Rule 3 — ETF proxy
    if ctx.role == "etf_proxy" or ctx.bucket_type == "proxy":
        return RouteDecision(
            route="watchlist",
            route_reason="ETF/proxy candidate — watchlist only, no direct position",
            route_confidence=1.0,
            allowed_routes=["watchlist"],
            required_confirmations=["sector_relative_strength_confirmation"],
        )

    # Rule 4 — direct beneficiary (structural)
    if ctx.role == "direct_beneficiary" and ctx.reason_to_care in _STRUCTURAL_REASONS:
        primary = _first_valid_hint(ctx.route_hint) if ctx.route_hint else "position"
        confidence = 0.9 if primary == "position" else 0.8
        downgrade = (
            ""
            if primary == "position"
            else f"route_hint overrides default position: {ctx.route_hint}"
        )
        allowed = list(dict.fromkeys(
            [primary] + [r for r in (ctx.route_hint or []) if r in _VALID_ROUTES]
        ))
        return RouteDecision(
            route=primary,
            route_reason=(
                f"Direct beneficiary of {ctx.theme or 'theme'} thesis "
                f"via {ctx.driver or 'driver'}"
            ),
            route_confidence=confidence,
            allowed_routes=allowed,
            required_confirmations=[
                "sector_etf_relative_strength",
                "price_volume_confirmation",
                "no_extended_chase",
            ],
            downgrade_reason=downgrade,
        )

    # Rule 5 — second-order beneficiary
    if ctx.role == "second_order_beneficiary":
        primary = _first_valid_hint(ctx.route_hint) if ctx.route_hint else "swing"
        return RouteDecision(
            route=primary,
            route_reason=f"Second-order beneficiary of {ctx.theme or 'theme'} thesis",
            route_confidence=0.7,
            allowed_routes=list(dict.fromkeys([primary, "watchlist"])),
            required_confirmations=[
                "primary_beneficiary_confirmation",
                "price_volume_confirmation",
            ],
        )

    # Rule 6 — catalyst adapter
    if labels & _CATALYST_SOURCE_LABELS or ctx.reason_to_care == "catalyst_candidate_from_adapter":
        return RouteDecision(
            route="swing",
            route_reason="Catalyst-driven candidate — short-duration swing trade",
            route_confidence=0.8,
            allowed_routes=["swing", "watchlist"],
            required_confirmations=[
                "catalyst_confirmation_within_session",
                "price_volume_confirmation",
            ],
        )

    # Rule 7 — Tier B daily promoted (attention)
    if labels & _TIER_B_SOURCE_LABELS:
        return RouteDecision(
            route="intraday_swing",
            route_reason="Tier B daily promoted candidate — intraday attention only",
            route_confidence=0.7,
            allowed_routes=["intraday_swing", "watchlist"],
            required_confirmations=[
                "gap_fill_or_volume_surge_confirmation",
                "intraday_session_only",
            ],
        )

    # Rule 8 — Tier A unclassified / attention shadow
    if (
        labels & _TIER_A_SOURCE_LABELS
        or ctx.reason_to_care in {"current_source_unclassified", "attention_shadow_only"}
    ):
        return RouteDecision(
            route="watchlist",
            route_reason="Unclassified current source — watchlist monitoring only",
            route_confidence=0.6,
            allowed_routes=["watchlist"],
            required_confirmations=["intelligence_layer_classification_required"],
        )

    # Rule 9 — pressure_candidate (headwind monitoring only, never executable)
    if ctx.role == "pressure_candidate" or ctx.reason_to_care == "headwind_pressure_watchlist":
        return RouteDecision(
            route="watchlist",
            route_reason="Headwind pressure candidate — monitoring only, not executable",
            route_confidence=1.0,
            allowed_routes=["watchlist"],
            required_confirmations=["headwind_monitoring_only_no_execution"],
        )

    # Rule 10 — do_not_touch
    if "do_not_touch" in labels or ctx.reason_to_care == "do_not_touch":
        return RouteDecision(
            route="do_not_touch",
            route_reason="Source flagged as do_not_touch",
            route_confidence=1.0,
            allowed_routes=["do_not_touch"],
            required_confirmations=[],
        )

    # Rule 11 — fallback
    return RouteDecision(
        route="watchlist",
        route_reason="No routing rule matched — default watchlist fallback",
        route_confidence=0.5,
        allowed_routes=["watchlist"],
        required_confirmations=["intelligence_layer_classification_required"],
        downgrade_reason="No matching route rule — downgraded to watchlist",
    )
