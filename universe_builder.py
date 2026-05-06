"""
universe_builder.py — shadow universe builder for the Intelligence-First architecture.

Single responsibility: combine the economic candidate feed with read-only snapshots
of current approved sources, apply deterministic route/quota logic, and write
data/universe_builder/active_opportunity_universe_shadow.json.

No live bot wiring. No mutations of any existing source file. Shadow only.
All symbols from approved roster, existing current sources, held positions,
or manual conviction lists.

Public surface:
    UniverseBuilder          — builds the shadow universe
    ShadowCandidate          — per-symbol output dataclass
    ShadowUniverse           — full output dataclass
    build_shadow_universe()  — convenience one-shot function
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_SCHEMA_VERSION = "1.0"
_DEFAULT_FEED_PATH = "data/intelligence/economic_candidate_feed.json"
_DEFAULT_OUTPUT_PATH = "data/universe_builder/active_opportunity_universe_shadow.json"
_DEFAULT_SNAPSHOT_PATH = "data/universe_builder/current_pipeline_snapshot.json"
_ADAPTER_SNAPSHOT_PATH = "data/intelligence/source_adapter_snapshot.json"
_THEMATIC_ROSTER_PATH = "data/intelligence/thematic_roster.json"
_COMMITTED_PATH = "data/committed_universe.json"

# Source files read read-only
_TIER_B_PATH = "data/daily_promoted.json"
_TIER_D_PATH = "data/position_research_universe.json"
_FAVOURITES_PATH = "data/favourites.json"

# Quota limits (locked architecture)
_QUOTA = {
    "structural_position": {"min": 8,  "max": 20, "protected": True},
    "catalyst_swing":       {"min": 10, "max": 30, "protected": False},
    "attention":            {"max": 15, "capped": True},
    "etf_proxy":            {"max": 10, "capped": True},
    "held":                 {"protected": True},
    "manual_conviction":    {"protected": True},
}
_TOTAL_MAX = 50
_ATTENTION_MAX = 15
_ETF_PROXY_MAX = 10
_STRUCTURAL_MAX = 20
_CATALYST_MAX = 30

_VALID_ROUTES = {"position", "swing", "intraday_swing", "watchlist", "held", "manual_conviction", "do_not_touch"}
_VALID_BUCKET_TYPES = {"structural", "catalyst", "attention", "proxy", "held", "manual"}
_VALID_QUOTA_GROUPS = {
    "structural_position", "catalyst_swing", "attention",
    "etf_proxy", "held", "manual_conviction",
    "current_source_unclassified",
}


@dataclass
class ShadowCandidate:
    symbol: str
    company_name: str | None
    asset_type: str
    reason_to_care: str
    bucket_id: str
    bucket_type: str
    route: str
    source_labels: list[str]
    macro_rules_fired: list[str]
    transmission_direction: str
    company_validation_status: str
    thesis_intact: bool | None
    why_this_symbol: str
    invalidation: list[str]
    eligibility: dict[str, Any]
    quota: dict[str, Any]
    execution_instructions: dict[str, Any]
    risk_notes: list[str]
    live_output_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":                      self.symbol,
            "company_name":                self.company_name,
            "asset_type":                  self.asset_type,
            "reason_to_care":              self.reason_to_care,
            "bucket_id":                   self.bucket_id,
            "bucket_type":                 self.bucket_type,
            "route":                       self.route,
            "source_labels":               self.source_labels,
            "macro_rules_fired":           self.macro_rules_fired,
            "transmission_direction":      self.transmission_direction,
            "company_validation_status":   self.company_validation_status,
            "thesis_intact":               self.thesis_intact,
            "why_this_symbol":             self.why_this_symbol,
            "invalidation":                self.invalidation,
            "eligibility":                 self.eligibility,
            "quota":                       self.quota,
            "execution_instructions":      self.execution_instructions,
            "risk_notes":                  self.risk_notes,
            "live_output_changed":         self.live_output_changed,
        }


@dataclass
class ShadowUniverse:
    schema_version: str
    generated_at: str
    valid_for_session: str
    freshness_status: str
    mode: str
    source_files: list[str]
    candidates: list[ShadowCandidate]
    inclusion_log: list[dict]
    exclusion_log: list[dict]
    quota_pressure_diagnostics: dict[str, Any]
    source_collision_report: list[dict]
    adapter_usage_summary: dict[str, Any]
    warnings: list[str]
    errors: list[str]
    live_output_changed: bool = False

    @property
    def universe_summary(self) -> dict[str, Any]:
        by_route = {}
        for r in _VALID_ROUTES:
            by_route[r] = sum(1 for c in self.candidates if c.route == r)

        by_bucket = {}
        for bt in _VALID_BUCKET_TYPES:
            by_bucket[bt] = sum(1 for c in self.candidates if c.bucket_type == bt)

        economic = sum(1 for c in self.candidates if "economic_intelligence" in c.source_labels)
        current = sum(1 for c in self.candidates if "economic_intelligence" not in c.source_labels)

        # Route metric distinction (Control 2)
        _STRUCTURAL_REASONS = {"structural", "structural_candidate_source", "structural_or_catalyst_watch"}
        structural_quota_group_count = sum(
            1 for c in self.candidates if c.quota.get("group") == "structural_position"
        )
        structural_reason_count = sum(
            1 for c in self.candidates if c.reason_to_care in _STRUCTURAL_REASONS
        )
        tier_d_structural_count = sum(
            1 for c in self.candidates if "tier_d_position_research" in (c.source_labels or [])
        )
        structural_watchlist = sum(
            1 for c in self.candidates
            if c.quota.get("group") == "structural_position" and c.route == "watchlist"
        )
        structural_swing = sum(
            1 for c in self.candidates
            if c.quota.get("group") == "structural_position" and c.route == "swing"
        )

        return {
            "total_candidates":                len(self.candidates),
            "position_candidates":             by_route.get("position", 0),
            "swing_candidates":                by_route.get("swing", 0),
            "intraday_swing_candidates":       by_route.get("intraday_swing", 0),
            "watchlist_candidates":            by_route.get("watchlist", 0),
            "held_candidates":                 by_route.get("held", 0),
            "manual_candidates":               by_route.get("manual_conviction", 0),
            "attention_candidates":            by_bucket.get("attention", 0),
            "structural_candidates":           by_bucket.get("structural", 0),
            "catalyst_candidates":             by_bucket.get("catalyst", 0),
            "etf_proxy_candidates":            by_bucket.get("proxy", 0),
            "economic_intelligence_candidates": economic,
            "current_source_candidates":       current,
            # Route metric distinction (structural quota ≠ position route)
            "position_route_count":            by_route.get("position", 0),
            "structural_quota_group_count":    structural_quota_group_count,
            "structural_reason_to_care_count": structural_reason_count,
            "tier_d_structural_source_count":  tier_d_structural_count,
            "structural_watchlist_count":      structural_watchlist,
            "structural_swing_count":          structural_swing,
            "llm_symbol_discovery_used":       False,
            "raw_news_used":                   False,
            "broad_intraday_scan_used":        False,
        }

    @property
    def quota_summary(self) -> dict[str, Any]:
        structural = [c for c in self.candidates if c.quota.get("group") == "structural_position"]
        catalyst   = [c for c in self.candidates if c.quota.get("group") == "catalyst_swing"]
        attention  = [c for c in self.candidates if c.quota.get("group") == "attention"]
        etf        = [c for c in self.candidates if c.quota.get("group") == "etf_proxy"]
        held       = [c for c in self.candidates if c.quota.get("group") == "held"]
        manual     = [c for c in self.candidates if c.quota.get("group") == "manual_conviction"]
        return {
            "structural_position": {
                "min":       _QUOTA["structural_position"]["min"],
                "max":       _QUOTA["structural_position"]["max"],
                "used":      len(structural),
                "protected": True,
            },
            "catalyst_swing": {
                "min":  _QUOTA["catalyst_swing"]["min"],
                "max":  _QUOTA["catalyst_swing"]["max"],
                "used": len(catalyst),
            },
            "attention": {
                "max":    _ATTENTION_MAX,
                "used":   len(attention),
                "capped": True,
            },
            "etf_proxy": {
                "max":    _ETF_PROXY_MAX,
                "used":   len(etf),
                "capped": True,
            },
            "held": {
                "protected": True,
                "used":      len(held),
            },
            "manual_conviction": {
                "protected": True,
                "used":      len(manual),
            },
            "total": {
                "max":  _TOTAL_MAX,
                "used": len(self.candidates),
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version":             self.schema_version,
            "generated_at":               self.generated_at,
            "valid_for_session":          self.valid_for_session,
            "freshness_status":           self.freshness_status,
            "mode":                       self.mode,
            "source_files":               self.source_files,
            "universe_summary":           self.universe_summary,
            "quota_summary":              self.quota_summary,
            "quota_pressure_diagnostics": self.quota_pressure_diagnostics,
            "source_collision_report":    self.source_collision_report,
            "adapter_usage_summary":      self.adapter_usage_summary,
            "candidates":                 [c.to_dict() for c in self.candidates],
            "inclusion_log":              self.inclusion_log,
            "exclusion_log":              self.exclusion_log,
            "warnings":                   self.warnings,
            "live_output_changed":        self.live_output_changed,
        }


# ---------------------------------------------------------------------------
# Source readers — all read-only, safe to call, return empty on failure
# ---------------------------------------------------------------------------

def _read_json(path: str) -> tuple[Any, str | None]:
    if not os.path.exists(path):
        return None, f"Not found: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        return None, f"Failed to read {path}: {e}"


def _load_tier_a() -> list[str]:
    """Tier A: hardcoded from scanner.py constants — safe to import read-only."""
    try:
        from scanner import CORE_SYMBOLS, CORE_EQUITIES
        return list(CORE_SYMBOLS) + list(CORE_EQUITIES)
    except ImportError:
        return []


def _load_tier_b() -> list[str]:
    data, err = _read_json(_TIER_B_PATH)
    if err or not isinstance(data, dict):
        return []
    return [s["ticker"] for s in (data.get("symbols") or []) if isinstance(s, dict) and s.get("ticker")]


def _load_tier_d() -> list[str]:
    data, err = _read_json(_TIER_D_PATH)
    if err or not isinstance(data, dict):
        return []
    return [s["ticker"] for s in (data.get("symbols") or []) if isinstance(s, dict) and s.get("ticker")]


def _load_favourites() -> list[str]:
    data, err = _read_json(_FAVOURITES_PATH)
    if err:
        return []
    if isinstance(data, list):
        return [s for s in data if isinstance(s, str) and s.strip()]
    if isinstance(data, dict):
        return [s for s in (data.get("symbols") or data.get("favourites") or []) if isinstance(s, str) and s.strip()]
    return []


def _load_economic_feed(feed_path: str) -> tuple[list[dict], list[str]]:
    data, err = _read_json(feed_path)
    if err:
        return [], [err]
    if not isinstance(data, dict):
        return [], [f"economic_candidate_feed.json: expected dict, got {type(data)}"]
    return data.get("candidates") or [], []


def _load_thematic_roster_symbols() -> set[str]:
    """Return all symbols from thematic_roster.json (core_symbols + etf_proxies)."""
    data, err = _read_json(_THEMATIC_ROSTER_PATH)
    if err or not isinstance(data, dict):
        return set()
    symbols: set[str] = set()
    for roster in (data.get("rosters") or []):
        for sym in roster.get("core_symbols") or []:
            if isinstance(sym, str) and sym.strip():
                symbols.add(sym)
        for sym in roster.get("etf_proxies") or []:
            if isinstance(sym, str) and sym.strip():
                symbols.add(sym)
    return symbols


def _load_committed_symbols() -> set[str]:
    """Return all symbols from committed_universe.json."""
    data, err = _read_json(_COMMITTED_PATH)
    if err or not isinstance(data, dict):
        return set()
    raw = data.get("symbols") or []
    if raw and isinstance(raw[0], dict):
        return {s["symbol"] for s in raw if isinstance(s, dict) and s.get("symbol")}
    return {s for s in raw if isinstance(s, str) and s.strip()}


# ---------------------------------------------------------------------------
# Candidate constructors
# ---------------------------------------------------------------------------

def _execution_instructions_shadow(routes: list[str]) -> dict[str, Any]:
    """No candidate is executable in shadow mode — only allowed routes + future gates."""
    return {
        "executable":                  False,
        "allowed_routes_when_live":    routes,
        "required_future_confirmation": [
            "sector_etf_relative_strength",
            "price_and_volume_confirmation_by_trading_bot",
            "no_extended_chase",
            "live_spread_and_risk_check_at_execution_only",
        ],
        "note": "Shadow only — no order generation permitted",
    }


def _eligibility_shadow(symbol: str) -> dict[str, Any]:
    return {
        "status":  "shadow_only_unknown",
        "reason":  "Day 4 static bootstrap — no live eligibility check run",
        "symbol":  symbol,
    }


def _from_economic_candidate(ec: dict) -> ShadowCandidate | None:
    symbol = ec.get("symbol", "").strip()
    if not symbol:
        return None
    role = ec.get("role", "")
    route_hint: list[str] = ec.get("route_hint") or ["watchlist"]
    # Primary route is the first entry in route_hint
    primary_route = route_hint[0] if route_hint else "watchlist"
    transmission_rules = ec.get("transmission_rules_fired") or []

    if role == "direct_beneficiary":
        bucket_type   = "structural"
        reason_to_care = "structural"
        quota_group    = "structural_position"
        transmission_direction = "tailwind"
    elif role == "second_order_beneficiary":
        bucket_type    = "structural"
        reason_to_care = "structural_or_catalyst_watch"
        quota_group    = "structural_position"
        transmission_direction = "tailwind"
    elif role == "etf_proxy":
        bucket_type    = "proxy"
        reason_to_care = "proxy"
        quota_group    = "etf_proxy"
        primary_route  = "watchlist"
        transmission_direction = "tailwind"
    elif role == "pressure_candidate":
        # Headwind monitoring — watchlist only, never structural quota
        bucket_type    = "attention"
        reason_to_care = "headwind_pressure_watchlist"
        quota_group    = "attention"
        primary_route  = "watchlist"
        transmission_direction = "headwind"
    else:
        bucket_type    = "attention"
        reason_to_care = "economic_intelligence_candidate"
        quota_group    = "attention"
        transmission_direction = "unknown"

    return ShadowCandidate(
        symbol=symbol,
        company_name=None,
        asset_type="equity",
        reason_to_care=reason_to_care,
        bucket_id=f"{ec.get('theme', 'unknown')}_{role}",
        bucket_type=bucket_type,
        route=primary_route,
        source_labels=list(ec.get("source_labels") or ["economic_intelligence"]) + ["economic_intelligence"],
        macro_rules_fired=list(transmission_rules),
        transmission_direction=transmission_direction,
        company_validation_status="not_run_static_bootstrap",
        thesis_intact=None,
        why_this_symbol=ec.get("reason_to_care", ""),
        invalidation=list(ec.get("risk_flags") or []),
        eligibility=_eligibility_shadow(symbol),
        quota={"group": quota_group, "protected": quota_group in {"structural_position", "held", "manual_conviction"}},
        execution_instructions=_execution_instructions_shadow(route_hint),
        risk_notes=list(ec.get("risk_flags") or []),
        live_output_changed=False,
    )


def _from_tier_d(symbol: str) -> ShadowCandidate:
    return ShadowCandidate(
        symbol=symbol,
        company_name=None,
        asset_type="equity",
        reason_to_care="structural_candidate_source",
        bucket_id=f"tier_d_{symbol}",
        bucket_type="structural",
        route="position",
        source_labels=["tier_d_position_research"],
        macro_rules_fired=[],
        transmission_direction="none",
        company_validation_status="not_run_static_bootstrap",
        thesis_intact=None,
        why_this_symbol=f"{symbol} is in the Tier D position research universe (structural fundamental discovery)",
        invalidation=[],
        eligibility=_eligibility_shadow(symbol),
        quota={"group": "structural_position", "protected": True},
        execution_instructions=_execution_instructions_shadow(["position", "watchlist"]),
        risk_notes=[],
        live_output_changed=False,
    )


def _from_tier_b(symbol: str) -> ShadowCandidate:
    return ShadowCandidate(
        symbol=symbol,
        company_name=None,
        asset_type="equity",
        reason_to_care="attention_shadow_only",
        bucket_id=f"tier_b_{symbol}",
        bucket_type="attention",
        route="watchlist",
        source_labels=["tier_b_daily_promoted"],
        macro_rules_fired=[],
        transmission_direction="none",
        company_validation_status="not_run_static_bootstrap",
        thesis_intact=None,
        why_this_symbol=f"{symbol} appeared in daily promoted universe (gap/volume/catalyst score)",
        invalidation=[],
        eligibility=_eligibility_shadow(symbol),
        quota={"group": "attention", "protected": False},
        execution_instructions=_execution_instructions_shadow(["intraday_swing", "watchlist"]),
        risk_notes=[],
        live_output_changed=False,
    )


def _from_tier_a(symbol: str) -> ShadowCandidate:
    return ShadowCandidate(
        symbol=symbol,
        company_name=None,
        asset_type="equity",
        reason_to_care="current_source_unclassified",
        bucket_id=f"tier_a_{symbol}",
        bucket_type="attention",
        route="watchlist",
        source_labels=["tier_a_core_floor"],
        macro_rules_fired=[],
        transmission_direction="none",
        company_validation_status="not_run_static_bootstrap",
        thesis_intact=None,
        why_this_symbol=f"{symbol} is a Tier A always-on core floor symbol",
        invalidation=[],
        eligibility=_eligibility_shadow(symbol),
        quota={"group": "current_source_unclassified", "protected": False},
        execution_instructions=_execution_instructions_shadow(["swing", "intraday_swing", "watchlist"]),
        risk_notes=[],
        live_output_changed=False,
    )


def _from_catalyst_adapter(symbol: str, catalyst_score: float, reason: str) -> ShadowCandidate:
    """Build a catalyst_swing candidate from the catalyst engine adapter."""
    return ShadowCandidate(
        symbol=symbol,
        company_name=None,
        asset_type="equity",
        reason_to_care="catalyst_candidate_from_adapter",
        bucket_id=f"catalyst_adapter_{symbol}",
        bucket_type="catalyst",
        route="swing",
        source_labels=["catalyst_watchlist_read_only"],
        macro_rules_fired=[],
        transmission_direction="catalyst",
        company_validation_status="not_run_static_bootstrap",
        thesis_intact=None,
        why_this_symbol=(
            f"{symbol} from catalyst watchlist adapter "
            f"(catalyst_score={catalyst_score:.2f}): {reason}"
        ),
        invalidation=[],
        eligibility=_eligibility_shadow(symbol),
        quota={"group": "catalyst_swing", "protected": False},
        execution_instructions=_execution_instructions_shadow(["swing", "watchlist"]),
        risk_notes=[],
        live_output_changed=False,
    )


def _from_favourite(symbol: str) -> ShadowCandidate:
    return ShadowCandidate(
        symbol=symbol,
        company_name=None,
        asset_type="equity",
        reason_to_care="manual_conviction",
        bucket_id=f"manual_{symbol}",
        bucket_type="manual",
        route="manual_conviction",
        source_labels=["favourites_manual_conviction"],
        macro_rules_fired=[],
        transmission_direction="none",
        company_validation_status="not_run_static_bootstrap",
        thesis_intact=None,
        why_this_symbol=f"{symbol} is in the manual conviction / favourites list",
        invalidation=[],
        eligibility=_eligibility_shadow(symbol),
        quota={"group": "manual_conviction", "protected": True},
        execution_instructions=_execution_instructions_shadow(["position", "swing", "watchlist", "manual_conviction"]),
        risk_notes=[],
        live_output_changed=False,
    )


# ---------------------------------------------------------------------------
# Universe builder
# ---------------------------------------------------------------------------

class UniverseBuilder:
    """
    Builds the shadow active opportunity universe.

    Priority order (symbols seen earlier win in dedup):
      1. Held positions (protected, always included — empty in static bootstrap)
      2. Manual conviction / favourites (protected)
      3. Economic intelligence candidates (direct_beneficiary → structural_position)
      4. Tier D position research (structural_position, up to structural max)
      5. Tier B daily promoted (attention, capped at 15)
      6. Tier A core floor (current_source_unclassified, fills remaining attention slots)
    """

    def __init__(
        self,
        feed_path: str = _DEFAULT_FEED_PATH,
        output_path: str = _DEFAULT_OUTPUT_PATH,
        snapshot_path: str = _DEFAULT_SNAPSHOT_PATH,
        adapter_snapshot_path: str = _ADAPTER_SNAPSHOT_PATH,
    ) -> None:
        self._feed_path = feed_path
        self._output_path = output_path
        self._snapshot_path = snapshot_path
        self._adapter_snapshot_path = adapter_snapshot_path

    def build(self) -> ShadowUniverse:
        from route_tagger import RouteContext, assign_route
        from quota_allocator import QuotaCandidate, allocate

        now = datetime.now(timezone.utc)
        generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_for_session = generated_at[:10]

        warnings: list[str] = []
        errors:   list[str] = []
        pre_exclusion_log: list[dict] = []   # approved-source guard, runs before allocator

        # ── Pre-load source data (all pure file reads, no side effects)
        _preload_tier_a    = _load_tier_a()
        _preload_tier_b    = _load_tier_b()
        _preload_tier_d    = _load_tier_d()
        _preload_favs      = _load_favourites()
        _preload_roster    = _load_thematic_roster_symbols()
        _preload_committed = _load_committed_symbols()

        adapter_snap, _adapter_snap_err = _read_json(self._adapter_snapshot_path)
        adapter_snap_available = isinstance(adapter_snap, dict)

        source_files = [self._feed_path, self._snapshot_path]

        # ── Build ordered QuotaCandidate list (priority = lower is included first)
        # Priority: 1=manual, 2=economic, 3=tier_d, 4=catalyst, 5=tier_b, 6=tier_a
        quota_candidates: list[QuotaCandidate] = []

        if not _preload_favs:
            warnings.append("No favourites loaded — manual conviction quota group empty in this bootstrap")
        for sym in _preload_favs:
            ctx = RouteContext(
                symbol=sym, reason_to_care="manual_conviction",
                source_labels=["favourites_manual_conviction"],
                role="manual", theme="", driver="",
                is_held=False, is_manual_conviction=True,
                route_hint=["manual_conviction"], bucket_type="manual",
            )
            decision = assign_route(ctx)
            cand = _from_favourite(sym)
            cand.route = decision.route
            quota_candidates.append(QuotaCandidate(
                symbol=sym, quota_group="manual_conviction",
                source_labels=["favourites_manual_conviction"],
                route=decision.route, priority=1, is_protected=True,
                source_name="manual_conviction_favourites",
                payload=cand,
            ))
        source_files.append(_FAVOURITES_PATH)

        ec_list, ec_errors = _load_economic_feed(self._feed_path)
        errors.extend(ec_errors)
        if ec_errors:
            warnings.append("Economic candidate feed load failed — structural quota will be unfilled")
        for ec in ec_list:
            cand = _from_economic_candidate(ec)
            if cand is None:
                continue
            role = ec.get("role", "")
            ctx = RouteContext(
                symbol=cand.symbol, reason_to_care=cand.reason_to_care,
                source_labels=cand.source_labels,
                role=role, theme=ec.get("theme", ""),
                driver=ec.get("driver", ""),
                is_held=False, is_manual_conviction=False,
                route_hint=list(ec.get("route_hint") or ["watchlist"]),
                bucket_type=cand.bucket_type,
            )
            decision = assign_route(ctx)
            cand.route = decision.route
            group = cand.quota.get("group", "attention")
            quota_candidates.append(QuotaCandidate(
                symbol=cand.symbol, quota_group=group,
                source_labels=list(cand.source_labels),
                route=decision.route, priority=2, is_protected=False,
                source_name=f"economic_intelligence_{cand.bucket_type}",
                theme=ec.get("theme", ""), role=role,
                driver=ec.get("driver", ""),
                reason_to_care=cand.reason_to_care,
                payload=cand,
            ))

        source_files.append(_TIER_D_PATH)
        for sym in _preload_tier_d:
            ctx = RouteContext(
                symbol=sym, reason_to_care="structural_candidate_source",
                source_labels=["tier_d_position_research"],
                role="direct_beneficiary", theme="position_research",
                driver="fundamental_discovery",
                is_held=False, is_manual_conviction=False,
                route_hint=["position", "watchlist"], bucket_type="structural",
            )
            decision = assign_route(ctx)
            cand = _from_tier_d(sym)
            cand.route = decision.route
            quota_candidates.append(QuotaCandidate(
                symbol=sym, quota_group="structural_position",
                source_labels=["tier_d_position_research"],
                route=decision.route, priority=3, is_protected=True,
                source_name="tier_d_structural",
                driver="fundamental_discovery",
                reason_to_care="structural_candidate_source",
                payload=cand,
            ))

        # ── Catalyst (priority 4): approved-source guard runs before allocator
        if adapter_snap_available:
            _queued_syms: set[str] = {qc.symbol for qc in quota_candidates}
            _approved_syms: set[str] = (
                _queued_syms
                | set(_preload_tier_a)
                | set(_preload_tier_b)
                | _preload_roster
                | _preload_committed
            )
            cat_adapter = (adapter_snap.get("adapters") or {}).get("catalyst_engine", {})
            if cat_adapter.get("source_status") == "available":
                for cc in (cat_adapter.get("output_summary") or {}).get("catalyst_candidates") or []:
                    sym = (cc.get("symbol") or "").strip()
                    if not sym:
                        continue
                    if sym not in _approved_syms:
                        pre_exclusion_log.append({
                            "symbol":      sym,
                            "excluded_by": ["catalyst_engine_adapter"],
                            "reason":      "catalyst_symbol_not_in_approved_source",
                        })
                        continue
                    ctx = RouteContext(
                        symbol=sym, reason_to_care="catalyst_candidate_from_adapter",
                        source_labels=["catalyst_watchlist_read_only"],
                        role="catalyst", theme="", driver="",
                        is_held=False, is_manual_conviction=False,
                        route_hint=["swing", "watchlist"], bucket_type="catalyst",
                    )
                    decision = assign_route(ctx)
                    cand = _from_catalyst_adapter(
                        sym,
                        float(cc.get("catalyst_score") or 0.0),
                        str(cc.get("reason") or ""),
                    )
                    cand.route = decision.route
                    quota_candidates.append(QuotaCandidate(
                        symbol=sym, quota_group="catalyst_swing",
                        source_labels=["catalyst_watchlist_read_only"],
                        route=decision.route, priority=4, is_protected=False,
                        source_name="catalyst_engine_adapter",
                        payload=cand,
                    ))

        source_files.append(_TIER_B_PATH)
        for sym in _preload_tier_b:
            ctx = RouteContext(
                symbol=sym, reason_to_care="attention_shadow_only",
                source_labels=["tier_b_daily_promoted"],
                role="attention", theme="", driver="",
                is_held=False, is_manual_conviction=False,
                route_hint=["intraday_swing", "watchlist"], bucket_type="attention",
            )
            decision = assign_route(ctx)
            cand = _from_tier_b(sym)
            cand.route = decision.route
            quota_candidates.append(QuotaCandidate(
                symbol=sym, quota_group="attention",
                source_labels=["tier_b_daily_promoted"],
                route=decision.route, priority=5, is_protected=False,
                source_name="tier_b_attention",
                payload=cand,
            ))

        for sym in _preload_tier_a:
            ctx = RouteContext(
                symbol=sym, reason_to_care="current_source_unclassified",
                source_labels=["tier_a_core_floor"],
                role="current_source", theme="", driver="",
                is_held=False, is_manual_conviction=False,
                route_hint=["watchlist"], bucket_type="attention",
            )
            decision = assign_route(ctx)
            cand = _from_tier_a(sym)
            cand.route = decision.route
            quota_candidates.append(QuotaCandidate(
                symbol=sym, quota_group="current_source_unclassified",
                source_labels=["tier_a_core_floor"],
                route=decision.route, priority=6, is_protected=False,
                source_name="tier_a_core_floor",
                payload=cand,
            ))

        # ── Allocate via quota_allocator
        result = allocate(quota_candidates)

        candidates: list[ShadowCandidate] = [
            qc.payload for qc in result.included
            if isinstance(qc.payload, ShadowCandidate)
        ]
        inclusion_log: list[dict] = result.inclusion_log
        exclusion_log: list[dict] = pre_exclusion_log + result.exclusion_log

        structural_used = result.quota_summary["structural_position"]["used"]

        # ── Post-processing: enrich candidates with adapter source labels
        symbols_enriched_by_adapter: list[str] = []
        catalyst_symbols_added: list[str] = []
        if adapter_snap_available:
            catalyst_symbols_added = [
                qc.symbol for qc in result.included
                if qc.source_name == "catalyst_engine_adapter"
            ]
            _ENRICHMENT_ADAPTERS = [
                ("theme_tracker_roster", "legacy_theme_tracker_read_only"),
                ("overnight_research",   "overnight_research_read_only"),
                ("committed_universe",   "committed_universe_read_only"),
            ]
            for adapter_name, label in _ENRICHMENT_ADAPTERS:
                a = (adapter_snap.get("adapters") or {}).get(adapter_name, {})
                if a.get("source_status") != "available":
                    continue
                adapter_sym_set = set(a.get("symbols_read") or [])
                for cand in candidates:
                    if cand.symbol in adapter_sym_set and label not in cand.source_labels:
                        cand.source_labels.append(label)
                        if cand.symbol not in symbols_enriched_by_adapter:
                            symbols_enriched_by_adapter.append(cand.symbol)

        # ── Adapter usage summary
        if adapter_snap_available:
            _asummary = adapter_snap.get("adapter_summary") or {}
            adapter_usage_summary: dict[str, Any] = {
                "adapter_snapshot_available":            True,
                "adapters_total":                        _asummary.get("adapters_total", 0),
                "adapters_available":                    _asummary.get("adapters_available", 0),
                "adapters_unavailable":                  _asummary.get("adapters_unavailable", 0),
                "adapters_skipped_due_side_effect_risk": _asummary.get("adapters_skipped_due_side_effect_risk", 0),
                "symbols_added_by_adapter":              catalyst_symbols_added,
                "symbols_enriched_by_adapter":           symbols_enriched_by_adapter,
                "side_effects_triggered":                False,
                "live_data_called":                      False,
            }
        else:
            adapter_usage_summary = {
                "adapter_snapshot_available": False,
                "adapters_total":             0,
                "adapters_available":         0,
                "adapters_unavailable":       0,
                "symbols_added_by_adapter":   [],
                "symbols_enriched_by_adapter": [],
                "side_effects_triggered":     False,
                "live_data_called":           False,
                "note":                       "Adapter snapshot not available",
            }

        if structural_used < _QUOTA["structural_position"]["min"]:
            warnings.append(
                f"Structural position quota below minimum: {structural_used} < "
                f"{_QUOTA['structural_position']['min']}"
            )

        freshness_status = "static_bootstrap_sprint3"

        return ShadowUniverse(
            schema_version=_SCHEMA_VERSION,
            generated_at=generated_at,
            valid_for_session=valid_for_session,
            freshness_status=freshness_status,
            mode="shadow_only",
            source_files=list(dict.fromkeys(source_files)),
            candidates=candidates,
            inclusion_log=inclusion_log,
            exclusion_log=exclusion_log,
            quota_pressure_diagnostics=result.quota_pressure_diagnostics,
            source_collision_report=result.source_collision_report,
            adapter_usage_summary=adapter_usage_summary,
            warnings=warnings,
            errors=errors,
            live_output_changed=False,
        )

    def write(self) -> ShadowUniverse:
        universe = self.build()
        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)
        with open(self._output_path, "w", encoding="utf-8") as f:
            json.dump(universe.to_dict(), f, indent=2)
        return universe


def build_shadow_universe(
    feed_path: str = _DEFAULT_FEED_PATH,
    output_path: str = _DEFAULT_OUTPUT_PATH,
    snapshot_path: str = _DEFAULT_SNAPSHOT_PATH,
    adapter_snapshot_path: str = _ADAPTER_SNAPSHOT_PATH,
) -> ShadowUniverse:
    """Convenience one-shot function."""
    builder = UniverseBuilder(
        feed_path=feed_path,
        output_path=output_path,
        snapshot_path=snapshot_path,
        adapter_snapshot_path=adapter_snapshot_path,
    )
    return builder.write()


if __name__ == "__main__":
    universe = build_shadow_universe()
    summary = universe.universe_summary
    quota = universe.quota_summary
    print(f"Shadow universe built → {_DEFAULT_OUTPUT_PATH}")
    print(f"  total:         {summary['total_candidates']}")
    print(f"  structural:    {summary['structural_candidates']}")
    print(f"  attention:     {summary['attention_candidates']}")
    print(f"  etf_proxy:     {summary['etf_proxy_candidates']}")
    print(f"  manual:        {summary['manual_candidates']}")
    print(f"  structural_position quota: {quota['structural_position']['used']}/{quota['structural_position']['max']}")
    print(f"  attention quota:           {quota['attention']['used']}/{quota['attention']['max']}")
    print(f"  live_output_changed:       {universe.live_output_changed}")
    if universe.warnings:
        for w in universe.warnings:
            print(f"  WARN: {w}")
