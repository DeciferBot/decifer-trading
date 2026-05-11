"""
candidate_resolver.py — resolves activated themes into structured Economic Candidate Feed entries.

Single responsibility: given a list of activated theme IDs (from macro_transmission_matrix.py
or supplied directly), load the theme taxonomy and thematic roster, then produce one
ResolvedCandidate per approved symbol.

No LLM involvement. No market data. No live-bot wiring.
All symbols come from the static thematic_roster.json only.

Public surface:
    ResolvedCandidate         — output dataclass per symbol
    CandidateResolver         — loads taxonomy + roster, exposes resolve()
    CandidateFeed             — complete feed result
    resolve_candidates(...)   — convenience one-shot function
    generate_feed(...)        — convenience function that also writes JSON output
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

_DEFAULT_TAXONOMY_PATH = "data/intelligence/theme_taxonomy.json"
_DEFAULT_ROSTER_PATH = "data/intelligence/thematic_roster.json"
_DEFAULT_OUTPUT_PATH = "data/intelligence/economic_candidate_feed.json"
_SCHEMA_VERSION = "1.0"

_VALID_ROUTES = {"position", "swing", "intraday_swing", "watchlist", "held", "manual_conviction", "do_not_touch"}

# Role classification: loaded from beneficiary_types in the taxonomy.
# These lists define how we map beneficiary_type values to roles.
_DIRECT_BENEFICIARY_TYPES = {
    "power_equipment",
    "cooling_infrastructure",
    "grid_infrastructure",
    "data_centre_electrical_infrastructure",
    "semiconductors",
    "ai_hardware",
    "defence_prime",
    "bank_net_interest_margin",
    "energy_producer",
    # Sprint 3 additions
    "high_free_cash_flow",
    "strong_balance_sheet",
    "resilient_earnings",
    "defensive_compounders",
    "mega_cap_quality",
    "quality_compounder",
}
_SECOND_ORDER_TYPES = {
    "utilities_power_demand",
    "utilities",
    "credit_spread",
    "real_estate",
    "second_order",
}

# Confidence decay applied to second_order_beneficiary vs base rule confidence
_SECOND_ORDER_DECAY = 0.15
# ETF proxy confidence floor — ETF proxies use lower fixed confidence
_ETF_PROXY_CONFIDENCE = 0.45


@dataclass
class ResolvedCandidate:
    symbol: str
    included_by: str
    theme: str
    driver: str
    role: str
    reason: str
    reason_to_care: str
    route_hint: list[str]
    confidence: float
    fresh_until: str
    risk_flags: list[str]
    confirmation_required: list[str]
    source_labels: list[str]
    transmission_rules_fired: list[str]
    market_confirmation_required: list[str]
    generated_at: str
    mode: str
    live_output_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":                    self.symbol,
            "included_by":               self.included_by,
            "theme":                     self.theme,
            "driver":                    self.driver,
            "role":                      self.role,
            "reason":                    self.reason,
            "reason_to_care":            self.reason_to_care,
            "route_hint":                self.route_hint,
            "confidence":                self.confidence,
            "fresh_until":               self.fresh_until,
            "risk_flags":                self.risk_flags,
            "confirmation_required":     self.confirmation_required,
            "source_labels":             self.source_labels,
            "transmission_rules_fired":  self.transmission_rules_fired,
            "market_confirmation_required": self.market_confirmation_required,
            "generated_at":              self.generated_at,
            "mode":                      self.mode,
            "live_output_changed":       self.live_output_changed,
        }


@dataclass
class CandidateFeed:
    schema_version: str
    generated_at: str
    fresh_until: str
    mode: str
    source_files: list[str]
    candidates: list[ResolvedCandidate]
    warnings: list[str]
    errors: list[str]
    live_output_changed: bool = False

    @property
    def feed_summary(self) -> dict[str, Any]:
        direct = [c for c in self.candidates if c.role == "direct_beneficiary"]
        second = [c for c in self.candidates if c.role == "second_order_beneficiary"]
        etf = [c for c in self.candidates if c.role == "etf_proxy"]
        pressure = [c for c in self.candidates if c.role == "pressure_candidate"]
        watchlist_only = [c for c in self.candidates if c.route_hint == ["watchlist"]]
        themes_active = sorted(set(c.theme for c in self.candidates if c.role != "pressure_candidate"))
        headwind_themes = sorted(set(c.theme for c in self.candidates if c.role == "pressure_candidate"))
        drivers_active = sorted(set(c.driver for c in self.candidates if c.driver))
        return {
            "total_candidates":              len(self.candidates),
            "themes_active":                 themes_active,
            "headwind_themes":               headwind_themes,
            "drivers_active":                drivers_active,
            "direct_beneficiaries":          len(direct),
            "second_order_beneficiaries":    len(second),
            "etf_proxies":                   len(etf),
            "pressure_candidates":           len(pressure),
            "watchlist_only":                len(watchlist_only),
            "headwind_candidates_executable": False,
            "llm_symbol_discovery_used":     False,
            "raw_news_used":                 False,
            "broad_intraday_scan_used":      False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version":   self.schema_version,
            "generated_at":     self.generated_at,
            "fresh_until":      self.fresh_until,
            "mode":             self.mode,
            "source_files":     self.source_files,
            "feed_summary":     self.feed_summary,
            "candidates":       [c.to_dict() for c in self.candidates],
            "warnings":         self.warnings,
            "live_output_changed": self.live_output_changed,
        }


class CandidateResolver:
    """
    Resolves a list of activated theme IDs into ResolvedCandidate entries.

    Activation input can come from:
      - MacroTransmissionMatrix.fire() result (pass TransmissionResult)
      - A caller-supplied list of theme IDs
      - A caller-supplied dict {theme_id: [rule_id, ...]} mapping themes to the rules that fired
    """

    def __init__(
        self,
        taxonomy_path: str = _DEFAULT_TAXONOMY_PATH,
        roster_path: str = _DEFAULT_ROSTER_PATH,
    ) -> None:
        self._taxonomy_path = taxonomy_path
        self._roster_path = roster_path
        self._taxonomy: dict[str, dict] = {}
        self._roster: dict[str, dict] = {}
        self._load_errors: list[str] = []
        self._load()

    def _load(self) -> None:
        for path, attr, key in [
            (self._taxonomy_path, "_taxonomy_raw", "themes"),
            (self._roster_path,   "_roster_raw",   "rosters"),
        ]:
            if not os.path.exists(path):
                self._load_errors.append(f"File not found: {path}")
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self._load_errors.append(f"Failed to load {path}: {e}")
                continue
            items = data.get(key, [])
            # Both taxonomy themes and roster entries use "theme_id" as their primary key
            parsed = {item["theme_id"]: item for item in items if isinstance(item, dict) and "theme_id" in item}
            setattr(self, attr.replace("_raw", ""), parsed)

    # ------------------------------------------------------------------
    # Role classification
    # ------------------------------------------------------------------

    def _classify_role(self, symbol: str, theme_entry: dict, roster_entry: dict) -> str:
        """Classify a symbol's role based on theme beneficiary types."""
        beneficiary_types: list[str] = theme_entry.get("beneficiary_types") or []
        # Check if any beneficiary_type suggests this is a direct beneficiary
        for bt in beneficiary_types:
            if bt in _DIRECT_BENEFICIARY_TYPES:
                # CEG is a utility/power demand proxy even in data_centre_power
                if bt == "utilities_power_demand" or symbol in {"CEG", "XLU", "XLE", "XLF", "XLU", "GLD", "SLV"}:
                    return "second_order_beneficiary"
                return "direct_beneficiary"
        # ETF proxies come from the roster's etf_proxies list
        return "direct_beneficiary"

    def _classify_role_for_symbol(self, symbol: str, theme_entry: dict, roster_entry: dict) -> str:
        """Determine role for a specific symbol within a theme."""
        etf_proxies = roster_entry.get("etf_proxies") or []
        if symbol in etf_proxies:
            return "etf_proxy"

        # Per-symbol override table (data_centre_power specific knowledge)
        _SECOND_ORDER = {"CEG", "D", "NEE", "SO", "AEP", "EXC"}
        if symbol in _SECOND_ORDER:
            return "second_order_beneficiary"

        return "direct_beneficiary"

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def _compute_confidence(self, role: str, base_confidence: float) -> float:
        if role == "direct_beneficiary":
            return round(base_confidence, 4)
        if role == "second_order_beneficiary":
            return round(max(0.0, base_confidence - _SECOND_ORDER_DECAY), 4)
        if role == "etf_proxy":
            return _ETF_PROXY_CONFIDENCE
        if role == "pressure_candidate":
            # Headwind monitoring candidates — significant confidence discount
            return round(max(0.0, base_confidence - 0.30), 4)
        # Unknown role — downgrade
        return round(max(0.0, base_confidence - 0.25), 4)

    # ------------------------------------------------------------------
    # Route hint
    # ------------------------------------------------------------------

    def _route_hint(self, role: str, roster_entry: dict) -> list[str]:
        route_bias = roster_entry.get("route_bias", "")
        default_routes_from_bias = {
            "position_or_swing":    ["position", "swing", "watchlist"],
            "position_only":        ["position", "watchlist"],
            "swing_only":           ["swing", "watchlist"],
            "watchlist_only":       ["watchlist"],
            "swing_or_intraday":    ["swing", "intraday_swing", "watchlist"],
        }
        base = default_routes_from_bias.get(route_bias, ["watchlist"])

        if role == "direct_beneficiary":
            return base
        if role == "second_order_beneficiary":
            # Downgrade: strip position, keep swing/watchlist
            return [r for r in base if r in {"swing", "intraday_swing", "watchlist"}] or ["watchlist"]
        if role == "etf_proxy":
            return ["watchlist"]
        return ["watchlist"]

    # ------------------------------------------------------------------
    # Reason-to-care string
    # ------------------------------------------------------------------

    def _reason_to_care(self, symbol: str, role: str, theme_entry: dict, fired_rule_reason: str) -> str:
        theme_name = theme_entry.get("name", theme_entry.get("theme_id", ""))
        if role == "direct_beneficiary":
            return f"{symbol} is a direct beneficiary of {theme_name}: {fired_rule_reason}"
        if role == "second_order_beneficiary":
            return f"{symbol} is a second-order beneficiary of {theme_name}: benefits indirectly from {fired_rule_reason}"
        if role == "etf_proxy":
            return f"{symbol} is an ETF proxy for {theme_name}: sector-level exposure only, not a direct beneficiary"
        return f"{symbol} linked to {theme_name}"

    # ------------------------------------------------------------------
    # Core resolve
    # ------------------------------------------------------------------

    def resolve(
        self,
        activated_themes: dict[str, list[str]],
        base_confidence: float = 0.82,
        fired_rule_reasons: dict[str, str] | None = None,
        theme_confidence: dict[str, float] | None = None,
        headwind_theme_ids: set[str] | None = None,
        fresh_hours: int = 48,
    ) -> CandidateFeed:
        """
        Resolve activated themes into a CandidateFeed.

        Args:
            activated_themes: {theme_id: [rule_id, ...]} — which rules fired for each theme
            base_confidence: fallback confidence when no rule provides it
            fired_rule_reasons: {rule_id: reason_string} — reason text per fired rule
            fresh_hours: how many hours the feed is valid (default 48)
        """
        now = datetime.now(timezone.utc)
        generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh_until = (now + timedelta(hours=fresh_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        warnings: list[str] = []
        errors: list[str] = list(self._load_errors)
        candidates: list[ResolvedCandidate] = []

        if fired_rule_reasons is None:
            fired_rule_reasons = {}

        _headwind_ids: set[str] = headwind_theme_ids or set()

        for theme_id, rule_ids in activated_themes.items():
            theme_entry = self._taxonomy.get(theme_id)
            roster_entry = self._roster.get(theme_id)

            if theme_entry is None:
                warnings.append(f"Theme '{theme_id}' not found in taxonomy — skipping")
                continue
            if roster_entry is None:
                warnings.append(f"Theme '{theme_id}' has no roster entry — skipping")
                continue

            is_headwind = theme_id in _headwind_ids

            # Aggregate reason text from all fired rules for this theme
            combined_reason = "; ".join(
                fired_rule_reasons[rid] for rid in rule_ids if rid in fired_rule_reasons
            ) or theme_entry.get("name", theme_id)

            theme_base_conf = (theme_confidence or {}).get(theme_id, base_confidence)

            risk_flags: list[str] = theme_entry.get("risk_flags") or []
            confirmation_required: list[str] = list(theme_entry.get("confirmation_requirements") or [])
            market_confirmation_required = [
                "sector_etf_relative_strength",
                "price_and_volume_confirmation_by_trading_bot",
                "no_extended_chase",
                "live_spread_and_risk_check_at_execution_only",
            ]
            source_labels = list({
                roster_entry.get("source_label", "intelligence_first_static_rule"),
                theme_entry.get("source_label", "intelligence_first_static_rule"),
            })

            # Core symbols
            core_symbols: list[str] = roster_entry.get("core_symbols") or []
            etf_proxies: list[str] = roster_entry.get("etf_proxies") or []

            all_symbols = list(core_symbols)
            # Include ETF proxies only if roster has them
            for sym in etf_proxies:
                if sym not in all_symbols:
                    all_symbols.append(sym)

            theme_name = theme_entry.get("name", theme_id)

            for symbol in all_symbols:
                if is_headwind:
                    # Headwind themes generate pressure_candidate watchlist entries only
                    role = "pressure_candidate"
                    confidence = self._compute_confidence(role, theme_base_conf)
                    route_hint = ["watchlist"]
                    reason_to_care = (
                        f"{symbol} is a headwind pressure watchlist for {theme_name}: "
                        f"monitor for risk — {combined_reason}"
                    )
                else:
                    role = self._classify_role_for_symbol(symbol, theme_entry, roster_entry)
                    confidence = self._compute_confidence(role, theme_base_conf)
                    route_hint = self._route_hint(role, roster_entry)
                    reason_to_care = self._reason_to_care(symbol, role, theme_entry, combined_reason)

                candidates.append(ResolvedCandidate(
                    symbol=symbol,
                    included_by="economic_intelligence",
                    theme=theme_id,
                    driver=", ".join(theme_entry.get("activation_drivers") or []),
                    role=role,
                    reason=combined_reason,
                    reason_to_care=reason_to_care,
                    route_hint=route_hint,
                    confidence=confidence,
                    fresh_until=fresh_until,
                    risk_flags=risk_flags,
                    confirmation_required=confirmation_required,
                    source_labels=source_labels,
                    transmission_rules_fired=list(rule_ids),
                    market_confirmation_required=market_confirmation_required,
                    generated_at=generated_at,
                    mode="shadow_report_only",
                    live_output_changed=False,
                ))

        return CandidateFeed(
            schema_version=_SCHEMA_VERSION,
            generated_at=generated_at,
            fresh_until=fresh_until,
            mode="shadow_report_only",
            source_files=[self._taxonomy_path, self._roster_path],
            candidates=candidates,
            warnings=warnings,
            errors=errors,
            live_output_changed=False,
        )


def resolve_candidates(
    activated_themes: dict[str, list[str]],
    taxonomy_path: str = _DEFAULT_TAXONOMY_PATH,
    roster_path: str = _DEFAULT_ROSTER_PATH,
    base_confidence: float = 0.82,
    fired_rule_reasons: dict[str, str] | None = None,
    theme_confidence: dict[str, float] | None = None,
    headwind_theme_ids: set[str] | None = None,
    fresh_hours: int = 48,
) -> CandidateFeed:
    """Convenience function. Creates a resolver and fires it."""
    resolver = CandidateResolver(taxonomy_path=taxonomy_path, roster_path=roster_path)
    return resolver.resolve(
        activated_themes=activated_themes,
        base_confidence=base_confidence,
        fired_rule_reasons=fired_rule_reasons,
        theme_confidence=theme_confidence,
        headwind_theme_ids=headwind_theme_ids,
        fresh_hours=fresh_hours,
    )


def generate_feed(
    activated_themes: dict[str, list[str]] | None = None,
    output_path: str = _DEFAULT_OUTPUT_PATH,
    taxonomy_path: str = _DEFAULT_TAXONOMY_PATH,
    roster_path: str = _DEFAULT_ROSTER_PATH,
    fired_rule_reasons: dict[str, str] | None = None,
    fresh_hours: int = 48,
) -> CandidateFeed:
    """
    Resolve candidates and write the economic_candidate_feed.json output file.

    If activated_themes is not supplied, fires the macro transmission matrix
    against a default ai_capex_growth driver state to populate the feed.
    This is the standard Day 3 invocation.
    """
    theme_confidence: dict[str, float] | None = None

    headwind_theme_ids: set[str] | None = None

    if activated_themes is None:
        # Default: fire the transmission matrix with all Sprint 3 active drivers.
        # Each driver alias maps to the rules in transmission_rules.json.
        from macro_transmission_matrix import MacroTransmissionMatrix
        rules_path = os.path.join(os.path.dirname(taxonomy_path), "transmission_rules.json")
        matrix = MacroTransmissionMatrix(rules_path=rules_path)
        result = matrix.fire({
            "active_drivers": [
                "ai_capex_growth",
                "yields_rising",
                "oil_supply_shock",
                "geopolitical_risk_rising",
                "credit_stress_rising",    # Sprint 3
                "risk_off_rotation",       # Sprint 3
                "ai_compute_demand",       # Sprint 7A.2: neocloud/AI compute infrastructure
            ],
            "blocked_conditions": [],
        })

        activated_themes = {}
        if fired_rule_reasons is None:
            fired_rule_reasons = {}
        theme_confidence = {}
        headwind_theme_ids = set()
        for fired_rule in result.transmission_rules_fired:
            # Track headwind themes so the resolver assigns pressure_candidate role
            if fired_rule.output_type == "theme_headwind":
                headwind_theme_ids.update(fired_rule.affected_targets)
            for target in fired_rule.affected_targets:
                activated_themes.setdefault(target, []).append(fired_rule.rule_id)
                fired_rule_reasons[fired_rule.rule_id] = fired_rule.reason
                # Per-theme confidence from the rule; keep highest if multiple rules fire per theme
                theme_confidence[target] = max(
                    theme_confidence.get(target, 0.0),
                    fired_rule.confidence,
                )

    feed = resolve_candidates(
        activated_themes=activated_themes,
        taxonomy_path=taxonomy_path,
        roster_path=roster_path,
        fired_rule_reasons=fired_rule_reasons,
        theme_confidence=theme_confidence,
        headwind_theme_ids=headwind_theme_ids,
        fresh_hours=fresh_hours,
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(feed.to_dict(), f, indent=2)

    return feed


if __name__ == "__main__":
    feed = generate_feed()
    print(f"Generated {len(feed.candidates)} candidates → data/intelligence/economic_candidate_feed.json")
    summary = feed.feed_summary
    print(f"  direct_beneficiaries:       {summary['direct_beneficiaries']}")
    print(f"  second_order_beneficiaries: {summary['second_order_beneficiaries']}")
    print(f"  etf_proxies:                {summary['etf_proxies']}")
    print(f"  live_output_changed:        {feed.live_output_changed}")
