"""
quota_allocator.py — pure quota allocation for the Intelligence-First architecture.

Single responsibility: given an ordered list of QuotaCandidate objects, enforce
quota group caps, deduplication (first-claim wins), and total cap, then return
a structured AllocationResult.

Pure function — no side effects, no network calls, no live data, no LLMs.
No candidate is ever marked executable by this module.

Quota groups and caps (locked architecture):
    held:                        protected (always included)
    manual_conviction:           protected (always included)
    structural_position:         min 8, max 20
    catalyst_swing:              min 10, max 30
    attention:                   max 15  (shared with current_source_unclassified)
    etf_proxy:                   max 10
    current_source_unclassified: shares attention cap
    total:                       max 50

Priority: lower integer = included first.
Dedup: first-claim wins (by priority order).

Public surface:
    QuotaCandidate   — input per candidate
    AllocationResult — output of allocation
    allocate()       — pure deterministic function
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_TOTAL_MAX = 50
_STRUCTURAL_MIN = 8
_STRUCTURAL_MAX = 20
_CATALYST_MAX = 30
_CATALYST_MIN = 10
_ATTENTION_MAX = 15   # shared: attention + current_source_unclassified
_ETF_PROXY_MAX = 10

_PROTECTED_GROUPS = frozenset({"held", "manual_conviction"})
_STRUCTURAL_GROUP = "structural_position"
_CATALYST_GROUP = "catalyst_swing"
_ATTENTION_GROUPS = frozenset({"attention", "current_source_unclassified"})
_ETF_GROUP = "etf_proxy"

_VALID_GROUPS = frozenset({
    "held", "manual_conviction", "structural_position",
    "catalyst_swing", "attention", "etf_proxy",
    "current_source_unclassified",
})


@dataclass
class QuotaCandidate:
    symbol: str
    quota_group: str
    source_labels: list[str]
    route: str
    priority: int           # lower = higher priority (0=held, 1=manual, 2=economic, 3=tier_d, 4=catalyst, 5=tier_b, 6=tier_a)
    is_protected: bool
    source_name: str
    theme: str = ""
    role: str = ""
    driver: str = ""
    reason_to_care: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: Any = None     # holds the original ShadowCandidate


@dataclass
class AllocationResult:
    included: list[QuotaCandidate]
    exclusion_log: list[dict]
    inclusion_log: list[dict]
    quota_summary: dict[str, Any]
    quota_pressure_diagnostics: dict[str, Any]
    source_collision_report: list[dict]
    live_output_changed: bool = False


def allocate(candidates: list[QuotaCandidate]) -> AllocationResult:
    """
    Pure quota allocation.

    Candidates must be provided in priority order (lower priority integer = first).
    Protected groups (held, manual_conviction) always bypass quota and total caps.
    Every inclusion and exclusion is logged with a reason.
    Returns AllocationResult; live_output_changed is always False.
    """
    included: list[QuotaCandidate] = []
    exclusion_log: list[dict] = []
    inclusion_log: list[dict] = []
    seen: dict[str, str] = {}       # symbol → source_name that claimed it first

    structural_demand_total = 0
    structural_demand_by_theme: dict[str, int] = {}
    structural_demand_by_source: dict[str, int] = {}
    etf_demand_total = 0
    attention_demand_total = 0
    catalyst_demand_total = 0

    # Overflow diagnostics per group (captured when quota is full)
    structural_overflow_by_theme: dict[str, int] = {}
    structural_overflow_by_driver: dict[str, int] = {}
    structural_overflow_by_reason: dict[str, int] = {}
    structural_overflow_by_route: dict[str, int] = {}
    structural_overflow_by_source: dict[str, int] = {}

    structural_used = 0
    catalyst_used = 0
    attention_used = 0      # shared: attention + current_source_unclassified
    etf_used = 0

    symbol_attempts: dict[str, list[dict]] = {}

    def _record_attempt(c: QuotaCandidate) -> None:
        symbol_attempts.setdefault(c.symbol, []).append({
            "source":        c.source_name,
            "source_labels": list(c.source_labels),
            "quota_group":   c.quota_group,
            "route":         c.route,
            "priority":      c.priority,
        })

    for c in sorted(candidates, key=lambda x: x.priority):
        group = c.quota_group
        _record_attempt(c)

        # Track demand before any filtering
        if group == _STRUCTURAL_GROUP:
            structural_demand_total += 1
            structural_demand_by_theme[c.theme] = (
                structural_demand_by_theme.get(c.theme, 0) + 1
            )
            structural_demand_by_source[c.source_name] = (
                structural_demand_by_source.get(c.source_name, 0) + 1
            )
        elif group == _ETF_GROUP:
            etf_demand_total += 1
        elif group in _ATTENTION_GROUPS:
            attention_demand_total += 1
        elif group == _CATALYST_GROUP:
            catalyst_demand_total += 1

        # Dedup — first-claim wins
        if c.symbol in seen:
            exclusion_log.append({
                "symbol":      c.symbol,
                "excluded_by": c.source_labels,
                "source":      c.source_name,
                "reason":      f"Duplicate — already claimed by {seen[c.symbol]}",
                "quota_group": group,
            })
            continue

        # Quota cap checks
        if group in _PROTECTED_GROUPS:
            pass  # protected: always include, bypass caps
        elif group == _STRUCTURAL_GROUP:
            if structural_used >= _STRUCTURAL_MAX:
                # Track fine-grained overflow diagnostics
                structural_overflow_by_theme[c.theme] = structural_overflow_by_theme.get(c.theme, 0) + 1
                structural_overflow_by_driver[c.driver] = structural_overflow_by_driver.get(c.driver, 0) + 1
                structural_overflow_by_reason[c.reason_to_care] = structural_overflow_by_reason.get(c.reason_to_care, 0) + 1
                structural_overflow_by_route[c.route] = structural_overflow_by_route.get(c.route, 0) + 1
                structural_overflow_by_source[c.source_name] = structural_overflow_by_source.get(c.source_name, 0) + 1
                exclusion_log.append({
                    "symbol":      c.symbol,
                    "excluded_by": c.source_labels,
                    "source":      c.source_name,
                    "reason":      f"Structural quota full ({_STRUCTURAL_MAX})",
                    "quota_group": group,
                })
                continue
        elif group == _CATALYST_GROUP:
            if catalyst_used >= _CATALYST_MAX:
                exclusion_log.append({
                    "symbol":      c.symbol,
                    "excluded_by": c.source_labels,
                    "source":      c.source_name,
                    "reason":      f"Catalyst swing quota full ({_CATALYST_MAX})",
                    "quota_group": group,
                })
                continue
        elif group in _ATTENTION_GROUPS:
            if attention_used >= _ATTENTION_MAX:
                exclusion_log.append({
                    "symbol":      c.symbol,
                    "excluded_by": c.source_labels,
                    "source":      c.source_name,
                    "reason":      f"Attention quota full ({_ATTENTION_MAX})",
                    "quota_group": group,
                })
                continue
        elif group == _ETF_GROUP:
            if etf_used >= _ETF_PROXY_MAX:
                exclusion_log.append({
                    "symbol":      c.symbol,
                    "excluded_by": c.source_labels,
                    "source":      c.source_name,
                    "reason":      f"ETF proxy quota full ({_ETF_PROXY_MAX})",
                    "quota_group": group,
                })
                continue

        # Total cap (protected groups always bypass)
        if len(included) >= _TOTAL_MAX and group not in _PROTECTED_GROUPS:
            exclusion_log.append({
                "symbol":      c.symbol,
                "excluded_by": c.source_labels,
                "source":      c.source_name,
                "reason":      f"Total universe cap reached ({_TOTAL_MAX})",
                "quota_group": group,
            })
            continue

        # Include
        seen[c.symbol] = c.source_name
        included.append(c)
        inclusion_log.append({
            "symbol":      c.symbol,
            "source":      c.source_labels,
            "source_name": c.source_name,
            "reason":      c.source_name,
            "route":       c.route,
            "quota":       group,
        })

        if group == _STRUCTURAL_GROUP:
            structural_used += 1
        elif group == _CATALYST_GROUP:
            catalyst_used += 1
        elif group in _ATTENTION_GROUPS:
            attention_used += 1
        elif group == _ETF_GROUP:
            etf_used += 1

    # Quota summary
    quota_summary: dict[str, Any] = {
        "structural_position": {
            "min":       _STRUCTURAL_MIN,
            "max":       _STRUCTURAL_MAX,
            "used":      structural_used,
            "protected": True,
        },
        "catalyst_swing": {
            "min":  _CATALYST_MIN,
            "max":  _CATALYST_MAX,
            "used": catalyst_used,
        },
        "attention": {
            "max":    _ATTENTION_MAX,
            "used":   attention_used,
            "capped": True,
            "note":   "shared cap: attention + current_source_unclassified",
        },
        "etf_proxy": {
            "max":    _ETF_PROXY_MAX,
            "used":   etf_used,
            "capped": True,
        },
        "held": {
            "protected": True,
            "used":      sum(1 for c in included if c.quota_group == "held"),
        },
        "manual_conviction": {
            "protected": True,
            "used":      sum(1 for c in included if c.quota_group == "manual_conviction"),
        },
        "total": {
            "max":  _TOTAL_MAX,
            "used": len(included),
        },
    }

    # Quota pressure diagnostics
    quota_pressure_diagnostics: dict[str, Any] = {
        "structural_position": {
            "demand_total":          structural_demand_total,
            "capacity":              _STRUCTURAL_MAX,
            "accepted":              structural_used,
            "overflow":              max(0, structural_demand_total - structural_used),
            "binding":               structural_used >= _STRUCTURAL_MAX,
            "demand_by_theme":       structural_demand_by_theme,
            "demand_by_source":      structural_demand_by_source,
            "overflow_by_theme":     structural_overflow_by_theme,
            "overflow_by_driver":    structural_overflow_by_driver,
            "overflow_by_reason_to_care": structural_overflow_by_reason,
            "overflow_by_route":     structural_overflow_by_route,
            "overflow_by_source":    structural_overflow_by_source,
        },
        "etf_proxy": {
            "demand_total": etf_demand_total,
            "capacity":     _ETF_PROXY_MAX,
            "accepted":     etf_used,
            "overflow":     max(0, etf_demand_total - etf_used),
            "binding":      etf_used >= _ETF_PROXY_MAX,
        },
        "attention": {
            "demand_total": attention_demand_total,
            "capacity":     _ATTENTION_MAX,
            "accepted":     attention_used,
            "overflow":     max(0, attention_demand_total - attention_used),
            "binding":      attention_used >= _ATTENTION_MAX,
        },
        "catalyst_swing": {
            "demand_total": catalyst_demand_total,
            "capacity":     _CATALYST_MAX,
            "accepted":     catalyst_used,
            "overflow":     max(0, catalyst_demand_total - catalyst_used),
            "binding":      catalyst_used >= _CATALYST_MAX,
        },
    }

    # Source collision report
    accepted_symbols = {c.symbol for c in included}
    manual_held_symbols = {
        c.symbol for c in included if c.quota_group in _PROTECTED_GROUPS
    }
    source_collision_report: list[dict] = []
    for symbol, attempts in symbol_attempts.items():
        if len(attempts) <= 1:
            continue
        winning = seen.get(symbol)
        losing_sources = {a["source"] for a in attempts if a["source"] != winning}
        final_in_shadow = symbol in accepted_symbols
        protected = symbol in manual_held_symbols
        excluded_entries = [
            {"source": e.get("excluded_by", []), "reason": e.get("reason", "")}
            for e in exclusion_log
            if e.get("symbol") == symbol and "Duplicate" in e.get("reason", "")
        ]
        source_collision_report.append({
            "symbol":                                    symbol,
            "collision_count":                           len(attempts),
            "attempted_by":                              [a["source"] for a in attempts],
            "winning_source":                            winning,
            "excluded_source_paths":                     sorted(losing_sources),
            "final_in_shadow":                           final_in_shadow,
            "protected_by_manual_or_held":               protected,
            "source_path_excluded_but_symbol_preserved": bool(losing_sources) and final_in_shadow,
            "excluded_entries":                          excluded_entries,
        })
    source_collision_report.sort(key=lambda r: r["symbol"])

    return AllocationResult(
        included=included,
        exclusion_log=exclusion_log,
        inclusion_log=inclusion_log,
        quota_summary=quota_summary,
        quota_pressure_diagnostics=quota_pressure_diagnostics,
        source_collision_report=source_collision_report,
        live_output_changed=False,
    )
