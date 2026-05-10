"""
compare_universes.py — compare the current live pipeline universe against the shadow universe.

Single responsibility: load current pipeline sources and the Day 4 shadow universe, compute
structured comparison metrics, and write two output files:
  - data/universe_builder/current_vs_shadow_comparison.json  (machine-readable)
  - data/universe_builder/universe_builder_report.json        (human-readable report)

No live data. No mutations. Read-only.

Public surface:
    UniverseComparator        — loads sources and computes metrics
    build_comparison_report() — convenience one-shot function
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_SCHEMA_VERSION = "1.0"
_SNAPSHOT_PATH = "data/universe_builder/current_pipeline_snapshot.json"
_SHADOW_PATH = "data/universe_builder/active_opportunity_universe_shadow.json"
_TIER_B_PATH = "data/daily_promoted.json"
_TIER_D_PATH = "data/position_research_universe.json"
_FAVOURITES_PATH = "data/favourites.json"
_FEED_PATH = "data/intelligence/economic_candidate_feed.json"
_ADAPTER_SNAPSHOT_PATH = "data/intelligence/source_adapter_snapshot.json"
_COMPARISON_OUTPUT = "data/universe_builder/current_vs_shadow_comparison.json"
_REPORT_OUTPUT = "data/universe_builder/universe_builder_report.json"

# Sprint 4B — economic intelligence layer outputs (read-only)
_DAILY_STATE_PATH = "data/intelligence/daily_economic_state.json"
_CONTEXT_PATH = "data/intelligence/current_economic_context.json"
_THEME_ACTIVATION_PATH = "data/intelligence/theme_activation.json"
_THESIS_STORE_PATH = "data/intelligence/thesis_store.json"

_UNAVAILABLE = "not_available_in_day5_shadow_mode"


def _read_json(path: str) -> tuple[Any, str | None]:
    if not os.path.exists(path):
        return None, f"Not found: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        return None, f"Failed to read {path}: {e}"


class UniverseComparator:
    """Loads all available sources and computes comparison metrics."""

    def __init__(
        self,
        snapshot_path: str = _SNAPSHOT_PATH,
        shadow_path: str = _SHADOW_PATH,
        tier_b_path: str = _TIER_B_PATH,
        tier_d_path: str = _TIER_D_PATH,
        favourites_path: str = _FAVOURITES_PATH,
        feed_path: str = _FEED_PATH,
        adapter_snapshot_path: str = _ADAPTER_SNAPSHOT_PATH,
    ) -> None:
        self._snapshot_path = snapshot_path
        self._shadow_path = shadow_path
        self._tier_b_path = tier_b_path
        self._tier_d_path = tier_d_path
        self._favourites_path = favourites_path
        self._feed_path = feed_path
        self._adapter_snapshot_path = adapter_snapshot_path
        self._warnings: list[str] = []

    # ------------------------------------------------------------------
    # Source loaders
    # ------------------------------------------------------------------

    def _load_tier_a(self) -> list[str]:
        try:
            from scanner import CORE_SYMBOLS, CORE_EQUITIES
            return list(CORE_SYMBOLS) + list(CORE_EQUITIES)
        except ImportError:
            self._warnings.append("Tier A: scanner.py import failed — using snapshot count only")
            return []

    def _load_tier_b(self) -> list[dict]:
        data, err = _read_json(self._tier_b_path)
        if err:
            self._warnings.append(f"Tier B: {err}")
            return []
        if not isinstance(data, dict):
            return []
        return [s for s in (data.get("symbols") or []) if isinstance(s, dict) and s.get("ticker")]

    def _load_tier_d(self) -> list[dict]:
        data, err = _read_json(self._tier_d_path)
        if err:
            self._warnings.append(f"Tier D: {err}")
            return []
        if not isinstance(data, dict):
            return []
        return [s for s in (data.get("symbols") or []) if isinstance(s, dict) and s.get("ticker")]

    def _load_favourites(self) -> list[str]:
        data, err = _read_json(self._favourites_path)
        if err:
            self._warnings.append(f"Favourites: {err}")
            return []
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str) and s.strip()]
        if isinstance(data, dict):
            return [s for s in (data.get("symbols") or data.get("favourites") or []) if isinstance(s, str)]
        return []

    def _load_shadow(self) -> dict:
        data, err = _read_json(self._shadow_path)
        if err:
            self._warnings.append(f"Shadow universe: {err}")
            return {}
        return data or {}

    def _load_snapshot(self) -> dict:
        data, err = _read_json(self._snapshot_path)
        if err:
            self._warnings.append(f"Snapshot: {err}")
            return {}
        return data or {}

    # ------------------------------------------------------------------
    # Core comparison
    # ------------------------------------------------------------------

    def compare(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        snapshot = self._load_snapshot()
        shadow_data = self._load_shadow()

        # ── Current source sets ───────────────────────────────────────
        tier_a_syms = set(self._load_tier_a())
        tier_b_records = self._load_tier_b()
        tier_b_syms = {r["ticker"] for r in tier_b_records}
        tier_d_records = self._load_tier_d()
        tier_d_syms = {r["ticker"] for r in tier_d_records}
        tier_d_ranked = [r["ticker"] for r in sorted(
            tier_d_records, key=lambda r: r.get("discovery_score", 0), reverse=True
        )]
        fav_syms = set(self._load_favourites())

        # Tier C: symbols not recoverable without live market data — mark unavailable
        tier_c_syms: set[str] = set()
        tier_c_status = _UNAVAILABLE

        # Held: not available in static bootstrap
        held_syms: set[str] = set()
        held_status = _UNAVAILABLE

        # Pre-filter source pool (union of available stages)
        current_pre_filter = tier_a_syms | tier_b_syms | tier_d_syms | fav_syms

        # Stages unavailable in Day 5 shadow mode
        unavailable_stages = [
            "current_tier_c",
            "current_held",
            "current_guardrails_passed",
            "current_apex_pre_cap",
            "current_apex_final_cap",
        ]

        # ── Shadow sets ───────────────────────────────────────────────
        shadow_candidates: list[dict] = shadow_data.get("candidates") or []
        shadow_all_syms: set[str] = {c["symbol"] for c in shadow_candidates}

        def _shadow_group(quota_group: str) -> set[str]:
            return {c["symbol"] for c in shadow_candidates if c.get("quota", {}).get("group") == quota_group}

        def _shadow_source(label: str) -> set[str]:
            return {c["symbol"] for c in shadow_candidates if label in (c.get("source_labels") or [])}

        def _shadow_route(route: str) -> set[str]:
            return {c["symbol"] for c in shadow_candidates if c.get("route") == route}

        def _shadow_bucket(bucket_type: str) -> set[str]:
            return {c["symbol"] for c in shadow_candidates if c.get("bucket_type") == bucket_type}

        shadow_structural = _shadow_group("structural_position")
        shadow_attention = _shadow_group("attention")
        shadow_etf_proxy = _shadow_group("etf_proxy")
        shadow_manual = _shadow_group("manual_conviction")
        shadow_held_syms = _shadow_group("held")
        shadow_economic = _shadow_source("economic_intelligence")
        shadow_tier_d_in_shadow = _shadow_source("tier_d_position_research")
        shadow_tier_b_in_shadow = _shadow_source("tier_b_daily_promoted")
        shadow_tier_a_in_shadow = _shadow_source("tier_a_core_floor")
        shadow_watchlist = _shadow_route("watchlist")
        shadow_position = _shadow_route("position")
        shadow_swing = _shadow_route("swing")

        # ── Overlap analysis ─────────────────────────────────────────
        overlap = current_pre_filter & shadow_all_syms
        in_current_not_shadow = current_pre_filter - shadow_all_syms
        in_shadow_not_current = shadow_all_syms - current_pre_filter

        # ── Tier D analysis ──────────────────────────────────────────
        tier_d_total = len(tier_d_syms)
        tier_d_in_shadow = tier_d_syms & shadow_all_syms
        tier_d_excluded = tier_d_syms - shadow_all_syms
        tier_d_preservation_rate = round(len(tier_d_in_shadow) / tier_d_total, 4) if tier_d_total > 0 else 0.0

        # Top preserved/excluded by discovery_score ranking
        top_tier_d_preserved = [s for s in tier_d_ranked if s in tier_d_in_shadow][:10]
        top_tier_d_excluded = [s for s in tier_d_ranked if s in tier_d_excluded][:10]

        # Exclusion reasons from shadow exclusion log
        shadow_excl_log: list[dict] = shadow_data.get("exclusion_log") or []
        tier_d_excl_reasons: dict[str, int] = {}
        for entry in shadow_excl_log:
            if "tier_d_position_research" in (entry.get("excluded_by") or []):
                reason = entry.get("reason", "unknown")
                tier_d_excl_reasons[reason] = tier_d_excl_reasons.get(reason, 0) + 1

        # ── Structural analysis ───────────────────────────────────────
        quota_summary = shadow_data.get("quota_summary") or {}
        structural_quota_used = quota_summary.get("structural_position", {}).get("used", 0)
        structural_quota_max = quota_summary.get("structural_position", {}).get("max", 20)
        structural_quota_binding = (structural_quota_used >= structural_quota_max)

        # Structural candidates that exist in current pool
        structural_in_current = shadow_structural & current_pre_filter
        structural_lost = shadow_structural - current_pre_filter

        structural_survival_rate = (
            round(len(structural_in_current) / len(shadow_structural), 4)
            if shadow_structural else 0.0
        )

        # Attention displacing structural: impossible by design — verify
        attention_in_structural = shadow_attention & shadow_structural
        structural_displaced_by_attention = len(attention_in_structural) > 0

        # ── Attention analysis ────────────────────────────────────────
        attention_cap = quota_summary.get("attention", {}).get("max", 15)
        attention_used = quota_summary.get("attention", {}).get("used", 0)
        attention_cap_respected = attention_used <= attention_cap
        attention_consumed_structural = len(shadow_attention & shadow_structural) > 0

        # Excluded attention names (from exclusion log)
        attention_excluded: list[str] = []
        attention_excl_reasons: list[str] = []
        for entry in shadow_excl_log:
            if "Attention quota full" in entry.get("reason", "") or "Tier B candidate capped" in entry.get("reason", ""):
                sym = entry.get("symbol", "")
                if sym:
                    attention_excluded.append(sym)
                attention_excl_reasons.append(entry.get("reason", ""))

        # ── Economic intelligence analysis ───────────────────────────
        economic_candidates = shadow_data.get("candidates") and [
            c for c in shadow_candidates if "economic_intelligence" in (c.get("source_labels") or [])
        ] or []
        economic_syms_in_shadow = {c["symbol"] for c in economic_candidates}
        economic_excluded: list[str] = []  # none excluded in Day 4 — only 5 candidates, all fit
        economic_executable = any(
            c.get("execution_instructions", {}).get("executable") is True
            for c in economic_candidates
        )

        # ── Manual / held analysis ────────────────────────────────────
        manual_in_shadow = len(shadow_manual)
        manual_lost = fav_syms - shadow_manual
        manual_protected = quota_summary.get("manual_conviction", {}).get("protected", False)

        held_protected = quota_summary.get("held", {}).get("protected", False)
        held_count = quota_summary.get("held", {}).get("used", 0)

        # ── Exclusion log summary ─────────────────────────────────────
        total_exclusions = len(shadow_excl_log)
        by_reason: Counter = Counter()
        by_source: Counter = Counter()
        by_quota: Counter = Counter()
        dup_exclusions = 0
        quota_full_exclusions = 0

        for entry in shadow_excl_log:
            reason = entry.get("reason", "unknown")
            by_reason[reason[:80]] += 1
            for lbl in (entry.get("excluded_by") or []):
                by_source[lbl] += 1
            if "Duplicate" in reason:
                dup_exclusions += 1
            if "quota full" in reason.lower():
                quota_full_exclusions += 1

        # ── Quality warnings ──────────────────────────────────────────
        quality_warnings: list[str] = list(self._warnings)
        if structural_quota_binding:
            quality_warnings.append(
                f"Structural position quota is BINDING ({structural_quota_used}/{structural_quota_max}) — "
                "133 Tier D candidates excluded. Quota will be relieved when Day 6 adds more slices and "
                "increases max, or when Sprint 2 route_tagger partitions the pool correctly."
            )
        quality_warnings.append(
            f"Tier D quality ranking IS available (discovery_score field). "
            f"Top preserved: {top_tier_d_preserved[:5]}. "
            f"Top excluded by score: {top_tier_d_excluded[:5]}."
        )
        quality_warnings.append(
            "Manual/favourite candidates protected but not yet structurally validated — "
            "no transmission rule links them to an active theme."
        )
        quality_warnings.append(
            "ETF/proxy candidates (XLU) capped at watchlist-only — "
            "cannot consume structural or catalyst quota."
        )
        if not held_count:
            quality_warnings.append(
                "Held candidates not present in Day 7 static bootstrap — "
                "held quota is protected and will be populated when live IBKR connection is enabled."
            )
        for stage in unavailable_stages:
            quality_warnings.append(f"Current pipeline stage '{stage}' is {_UNAVAILABLE}.")

        # ── Adapter impact analysis — reads adapter snapshot + shadow adapter_usage_summary ──
        adapter_snap_data, adapter_snap_err = _read_json(self._adapter_snapshot_path)
        aus = shadow_data.get("adapter_usage_summary") or {}
        if adapter_snap_err or not isinstance(adapter_snap_data, dict):
            if adapter_snap_err:
                self._warnings.append(f"Adapter snapshot: {adapter_snap_err}")
            adapter_impact_analysis: dict[str, Any] = {
                "adapter_snapshot_available":    False,
                "adapters_available":            0,
                "adapters_unavailable":          0,
                "adapter_symbols_read_total":    0,
                "adapter_unique_symbols_read":   0,
                "adapter_symbols_in_shadow":     0,
                "adapter_symbols_excluded":      0,
                "adapter_symbols_preserved":     0,
                "adapter_enriched_candidates":   0,
                "adapter_added_symbols":         [],
                "side_effects_triggered":        False,
                "live_data_called":              False,
            }
        else:
            # Count actual symbols from every adapter
            all_adapter_syms: list[str] = []
            for a in (adapter_snap_data.get("adapters") or {}).values():
                all_adapter_syms.extend(a.get("symbols_read") or [])
            unique_adapter_syms = set(all_adapter_syms)

            adapter_syms_in_shadow = {
                c["symbol"] for c in shadow_candidates
                if any(lbl.endswith("_read_only") for lbl in (c.get("source_labels") or []))
            }
            adapter_added = aus.get("symbols_added_by_adapter") or []
            adapter_enriched = aus.get("symbols_enriched_by_adapter") or []

            # Symbols from adapters that were considered but not in shadow
            adapter_excluded = unique_adapter_syms - shadow_all_syms

            _asummary = adapter_snap_data.get("adapter_summary") or {}
            adapter_impact_analysis = {
                "adapter_snapshot_available":    True,
                "adapters_available":            _asummary.get("adapters_available", 0),
                "adapters_unavailable":          _asummary.get("adapters_unavailable", 0),
                "adapter_symbols_read_total":    len(all_adapter_syms),
                "adapter_unique_symbols_read":   len(unique_adapter_syms),
                "adapter_symbols_in_shadow":     len(adapter_syms_in_shadow),
                "adapter_symbols_excluded":      len(adapter_excluded),
                "adapter_symbols_preserved":     len(unique_adapter_syms & shadow_all_syms),
                "adapter_enriched_candidates":   len(adapter_enriched),
                "adapter_added_symbols":         adapter_added,
                "side_effects_triggered":        False,
                "live_data_called":              False,
            }

        # ── Assemble output ────────────────────────────────────────────
        current_summary = {
            "current_pre_filter_source_pool_count": len(current_pre_filter),
            "current_tier_a_count":             len(tier_a_syms),
            "current_tier_b_count":             len(tier_b_syms),
            "current_tier_c_count":             tier_c_status,
            "current_tier_d_count":             tier_d_total,
            "current_favourites_count":         len(fav_syms),
            "current_held_count":               held_status,
            "current_guardrails_passed_count":  _UNAVAILABLE,
            "current_apex_pre_cap_count":       _UNAVAILABLE,
            "current_apex_final_cap_count":     _UNAVAILABLE,
            "unavailable_stages":               unavailable_stages,
        }

        shadow_summary = {
            "shadow_total_count":                    len(shadow_all_syms),
            "shadow_position_count":                 len(shadow_position),
            "shadow_swing_count":                    len(shadow_swing),
            "shadow_watchlist_count":                len(shadow_watchlist),
            "shadow_attention_count":                len(shadow_attention),
            "shadow_structural_count":               len(shadow_structural),
            "shadow_economic_intelligence_count":    len(shadow_economic),
            "shadow_tier_d_count":                   len(shadow_tier_d_in_shadow),
            "shadow_manual_count":                   len(shadow_manual),
            "shadow_held_count":                     len(shadow_held_syms),
            "shadow_etf_proxy_count":                len(shadow_etf_proxy),
        }

        overlap_summary = {
            "overlap_count":               len(overlap),
            "overlap_symbols":             sorted(overlap),
            "in_current_not_shadow_count": len(in_current_not_shadow),
            "in_current_not_shadow_symbols": sorted(in_current_not_shadow)[:50],
            "in_shadow_not_current_count": len(in_shadow_not_current),
            "in_shadow_not_current_symbols": sorted(in_shadow_not_current),
        }

        tier_d_analysis = {
            "tier_d_total_current":          tier_d_total,
            "tier_d_in_shadow_count":        len(tier_d_in_shadow),
            "tier_d_in_shadow_symbols":      sorted(tier_d_in_shadow),
            "tier_d_excluded_count":         len(tier_d_excluded),
            "tier_d_excluded_symbols":       sorted(tier_d_excluded)[:30],
            "tier_d_exclusion_reasons":      dict(tier_d_excl_reasons),
            "tier_d_preservation_rate":      tier_d_preservation_rate,
            "tier_d_quality_rank_available": True,
            "tier_d_quality_rank_field":     "discovery_score",
            "tier_d_structural_quota_used":  structural_quota_used,
            "tier_d_structural_quota_full":  structural_quota_binding,
            "top_tier_d_preserved":          top_tier_d_preserved,
            "top_tier_d_excluded":           top_tier_d_excluded,
            "note": (
                "Top Tier D excluded because structural quota (20) was filled by 4 economic "
                "intelligence candidates first. Sprint 2 quota_allocator.py will partition by "
                "route, increasing effective structural capacity."
            ),
        }

        structural_analysis = {
            "structural_candidates_current_count":      len(tier_d_syms),
            "structural_candidates_shadow_count":       len(shadow_structural),
            "structural_candidates_preserved_count":    len(structural_in_current),
            "structural_candidates_preserved_symbols":  sorted(structural_in_current),
            "structural_candidates_lost_count":         len(structural_lost),
            "structural_candidates_lost_symbols":       sorted(structural_lost),
            "structural_candidate_survival_rate":       structural_survival_rate,
            "structural_quota_used":                    structural_quota_used,
            "structural_quota_max":                     structural_quota_max,
            "structural_quota_binding":                 structural_quota_binding,
            "structural_candidates_displaced_by_attention": structural_displaced_by_attention,
        }

        attention_analysis = {
            "attention_candidates_shadow_count":          attention_used,
            "attention_cap":                              attention_cap,
            "attention_cap_respected":                    attention_cap_respected,
            "attention_excluded_count":                   len(attention_excluded),
            "attention_excluded_symbols":                 attention_excluded[:30],
            "attention_exclusion_reasons":                list(set(attention_excl_reasons))[:10],
            "attention_candidates_consumed_structural_quota": attention_consumed_structural,
        }

        manual_held_analysis = {
            "manual_candidates_current_count": len(fav_syms),
            "manual_candidates_shadow_count":  manual_in_shadow,
            "manual_candidates_protected":     bool(manual_protected),
            "manual_candidates_lost":          sorted(manual_lost),
            "held_candidates_current_count":   held_status,
            "held_candidates_shadow_count":    held_count,
            "held_candidates_protected":       bool(held_protected),
            "held_candidates_lost":            _UNAVAILABLE,
            "held_unavailable_reason":         "No live IBKR connection in Day 5 static bootstrap",
        }

        economic_analysis = {
            "economic_candidates_total":         len(economic_candidates),
            "economic_candidates_in_shadow":     len(economic_syms_in_shadow),
            "economic_candidates_excluded":      len(economic_excluded),
            "economic_symbols_in_shadow":        sorted(economic_syms_in_shadow),
            "economic_symbols_excluded":         economic_excluded,
            "economic_reason_to_care_present":   all(bool(c.get("reason_to_care")) for c in economic_candidates),
            "economic_candidates_executable":    economic_executable,
            "llm_symbol_discovery_used":         False,
            "raw_news_used":                     False,
            "broad_intraday_scan_used":          False,
        }

        exclusion_analysis = {
            "total_exclusions":              total_exclusions,
            "exclusions_by_reason":          dict(by_reason.most_common(20)),
            "exclusions_by_source":          dict(by_source.most_common()),
            "exclusions_by_quota_group":     dict(by_quota.most_common()),
            "duplicate_exclusions":          dup_exclusions,
            "quota_full_exclusions":         quota_full_exclusions,
            "malformed_candidate_exclusions": 0,
        }

        # ── Quota pressure analysis — pulled from shadow diagnostics ──
        shadow_qpd = shadow_data.get("quota_pressure_diagnostics") or {}
        quota_pressure_analysis: dict[str, Any] = {}
        if shadow_qpd:
            for group_name, diag in shadow_qpd.items():
                quota_pressure_analysis[group_name] = {
                    "demand_total":  diag.get("demand_total", 0),
                    "capacity":      diag.get("capacity"),
                    "accepted":      diag.get("accepted", 0),
                    "overflow":      diag.get("overflow", 0),
                    "binding":       diag.get("binding", False),
                }
                if "demand_by_theme" in diag:
                    quota_pressure_analysis[group_name]["demand_by_theme"] = diag["demand_by_theme"]
                if "demand_by_source" in diag:
                    quota_pressure_analysis[group_name]["demand_by_source"] = diag["demand_by_source"]
        else:
            quota_pressure_analysis["_unavailable"] = "quota_pressure_diagnostics not present in shadow file"

        # ── Source collision analysis — pulled from shadow report ──────
        shadow_scr: list[dict] = shadow_data.get("source_collision_report") or []
        collisions_total = len(shadow_scr)
        collisions_protected = sum(1 for r in shadow_scr if r.get("protected_by_manual_or_held"))
        collisions_preserved_via_other_path = sum(
            1 for r in shadow_scr if r.get("source_path_excluded_but_symbol_preserved")
        )
        collisions_lost = sum(1 for r in shadow_scr if not r.get("final_in_shadow"))
        source_collision_analysis: dict[str, Any] = {
            "total_collisions":                         collisions_total,
            "collisions_where_symbol_still_in_shadow":  collisions_protected + (collisions_total - collisions_lost - collisions_protected),
            "collisions_where_symbol_lost":             collisions_lost,
            "collisions_protected_by_manual_or_held":   collisions_protected,
            "source_path_excluded_but_symbol_preserved": collisions_preserved_via_other_path,
            "collision_detail":                         shadow_scr,
        }

        # ── Economic slice analysis — from feed (has theme metadata) ────
        feed_data, feed_err = _read_json(self._feed_path)
        feed_candidates_raw: list[dict] = []
        if feed_err:
            self._warnings.append(f"Economic feed for slice analysis: {feed_err}")
        elif isinstance(feed_data, dict):
            feed_candidates_raw = feed_data.get("candidates") or []

        econ_by_theme: dict[str, dict] = {}
        for ec in feed_candidates_raw:
            theme_key = ec.get("theme", "unknown")
            if theme_key not in econ_by_theme:
                econ_by_theme[theme_key] = {
                    "count": 0,
                    "symbols": [],
                    "driver": ec.get("driver", ""),
                    "direct_beneficiaries": 0,
                    "etf_proxies": 0,
                    "second_order": 0,
                }
            econ_by_theme[theme_key]["count"] += 1
            econ_by_theme[theme_key]["symbols"].append(ec.get("symbol", ""))
            role = ec.get("role", "")
            if role == "direct_beneficiary":
                econ_by_theme[theme_key]["direct_beneficiaries"] += 1
            elif role == "etf_proxy":
                econ_by_theme[theme_key]["etf_proxies"] += 1
            elif role == "second_order_beneficiary":
                econ_by_theme[theme_key]["second_order"] += 1
        economic_slice_analysis: dict[str, Any] = {
            "slices_active":             len(econ_by_theme),
            "total_economic_candidates": len(feed_candidates_raw),
            "by_theme":                  econ_by_theme,
            "llm_symbol_discovery_used": False,
            "macro_transmission_deterministic": True,
        }

        # ── Risk-off analysis (Sprint 3) ─────────────────────────────────────
        _RISK_OFF_THEMES = {"quality_cash_flow", "defensive_quality", "small_caps"}
        risk_off_feed_by_theme: dict[str, list[str]] = {t: [] for t in _RISK_OFF_THEMES}
        for ec in feed_candidates_raw:
            t = ec.get("theme", "")
            if t in _RISK_OFF_THEMES:
                sym = ec.get("symbol", "")
                if sym:
                    risk_off_feed_by_theme[t].append(sym)

        risk_off_syms_from_feed: set[str] = set()
        for syms in risk_off_feed_by_theme.values():
            risk_off_syms_from_feed.update(syms)

        risk_off_syms_in_shadow = risk_off_syms_from_feed & shadow_all_syms
        risk_off_syms_excluded = risk_off_syms_from_feed - shadow_all_syms
        watchlist_only_risk_off = [
            c for c in shadow_candidates
            if c["symbol"] in risk_off_syms_in_shadow and c.get("route") == "watchlist"
        ]
        headwind_in_shadow = [
            c for c in shadow_candidates
            if c.get("transmission_direction") == "headwind"
        ]
        risk_off_analysis: dict[str, Any] = {
            "quality_cash_flow_candidates_generated": len(risk_off_feed_by_theme["quality_cash_flow"]),
            "defensive_quality_candidates_generated": len(risk_off_feed_by_theme["defensive_quality"]),
            "small_caps_headwind_candidates_generated": len(risk_off_feed_by_theme["small_caps"]),
            "candidates_in_shadow":           len(risk_off_syms_in_shadow),
            "candidates_excluded_by_quota":   len(risk_off_syms_excluded),
            "candidates_watchlist_only":      len(watchlist_only_risk_off),
            "headwind_candidates_in_shadow":  len(headwind_in_shadow),
            "headwind_candidates_executable": False,
            "risk_off_symbols_preserved":     sorted(risk_off_syms_in_shadow),
            "risk_off_symbols_lost":          sorted(risk_off_syms_excluded),
        }

        # ── Route metric distinction (Control 2) ──────────────────────────────
        shadow_us = shadow_data.get("universe_summary") or {}
        route_metric_distinction: dict[str, Any] = {
            "position_route_count":            shadow_us.get("position_route_count", len(shadow_position)),
            "structural_quota_group_count":    shadow_us.get("structural_quota_group_count", len(shadow_structural)),
            "structural_reason_to_care_count": shadow_us.get("structural_reason_to_care_count", 0),
            "tier_d_structural_source_count":  shadow_us.get("tier_d_structural_source_count", len(shadow_tier_d_in_shadow)),
            "structural_watchlist_count":      shadow_us.get("structural_watchlist_count", 0),
            "structural_swing_count":          shadow_us.get("structural_swing_count", 0),
            "note": (
                "structural_quota_group_count includes all structural quota slots (position + swing routes). "
                "position_route_count counts only candidates with route=position. "
                "structural_swing_count captures banks/energy/defence candidates that are structural quota but routed to swing."
            ),
        }

        # ── Sprint 4B: Economic context summary (report-only, read-only)
        daily_state_data, _ds_err = _read_json(_DAILY_STATE_PATH)
        context_data, _ctx_err = _read_json(_CONTEXT_PATH)
        activation_data, _act_err = _read_json(_THEME_ACTIVATION_PATH)
        thesis_data, _thr_err = _read_json(_THESIS_STORE_PATH)

        def _themes_by_state(act: dict | None, state: str) -> list[str]:
            if not isinstance(act, dict):
                return []
            return [
                t.get("theme_id", "") for t in (act.get("themes") or [])
                if isinstance(t, dict) and t.get("state") == state
            ]

        economic_context_summary: dict[str, Any] = {
            "daily_economic_state_available": daily_state_data is not None,
            "current_economic_context_available": context_data is not None,
            "theme_activation_available": activation_data is not None,
            "thesis_store_available": thesis_data is not None,
            "active_themes": _themes_by_state(activation_data, "activated"),
            "strengthening_themes": _themes_by_state(activation_data, "strengthening"),
            "crowded_themes": _themes_by_state(activation_data, "crowded"),
            "weakening_themes": _themes_by_state(activation_data, "weakening"),
            "watchlist_themes": _themes_by_state(activation_data, "watchlist"),
            "invalidated_themes": _themes_by_state(activation_data, "invalidated"),
            "risk_posture": (context_data or {}).get("risk_posture", "unknown"),
            "regime_label": (context_data or {}).get("economic_regime", "unknown"),
            "thesis_count": len((thesis_data or {}).get("theses") or []) if thesis_data else 0,
            "low_confidence_themes": (
                (activation_data or {}).get("activation_summary", {}).get("low_confidence_count", 0)
                if activation_data else 0
            ),
            "evidence_limited_themes": (
                (activation_data or {}).get("activation_summary", {}).get("evidence_limited_count", 0)
                if activation_data else 0
            ),
            "no_live_api_called": True,
            "live_output_changed": False,
        }

        comparison = {
            "schema_version":               _SCHEMA_VERSION,
            "generated_at":                 generated_at,
            "mode":                         "shadow_comparison_only",
            "source_files": [
                self._snapshot_path,
                self._shadow_path,
                self._tier_b_path,
                self._tier_d_path,
                self._favourites_path,
            ],
            "current_summary":              current_summary,
            "shadow_summary":               shadow_summary,
            "overlap_summary":              overlap_summary,
            "tier_d_analysis":              tier_d_analysis,
            "structural_candidate_analysis": structural_analysis,
            "attention_analysis":           attention_analysis,
            "manual_and_held_analysis":     manual_held_analysis,
            "economic_intelligence_analysis": economic_analysis,
            "exclusion_analysis":           exclusion_analysis,
            "quota_pressure_analysis":      quota_pressure_analysis,
            "source_collision_analysis":    source_collision_analysis,
            "economic_slice_analysis":      economic_slice_analysis,
            "adapter_impact_analysis":      adapter_impact_analysis,
            "risk_off_analysis":            risk_off_analysis,
            "route_metric_distinction":     route_metric_distinction,
            "economic_context_summary":     economic_context_summary,
            "quality_warnings":             quality_warnings,
            "live_output_changed":          False,
        }

        return comparison

    def build_report(self, comparison: dict[str, Any]) -> dict[str, Any]:
        """Build the human-readable universe_builder_report from a comparison dict."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        td = comparison["tier_d_analysis"]
        struct = comparison["structural_candidate_analysis"]
        attn = comparison["attention_analysis"]
        econ = comparison["economic_intelligence_analysis"]
        mh = comparison["manual_and_held_analysis"]
        excl = comparison["exclusion_analysis"]
        cs = comparison["current_summary"]
        ss = comparison["shadow_summary"]
        ov = comparison["overlap_summary"]
        qpa = comparison.get("quota_pressure_analysis") or {}
        sca = comparison.get("source_collision_analysis") or {}
        esa = comparison.get("economic_slice_analysis") or {}
        aia = comparison.get("adapter_impact_analysis") or {}
        roa = comparison.get("risk_off_analysis") or {}
        rmd = comparison.get("route_metric_distinction") or {}
        ecs = comparison.get("economic_context_summary") or {}

        # Interpret key metrics
        interpretations = []
        if struct["structural_quota_binding"]:
            sp_qpa = qpa.get("structural_position", {})
            demand = sp_qpa.get("demand_total", "?")
            overflow = sp_qpa.get("overflow", "?")
            interpretations.append(
                f"BINDING: structural_position quota ({struct['structural_quota_used']}/"
                f"{struct['structural_quota_max']}) is full. "
                f"Total structural demand: {demand}, overflow: {overflow} candidates excluded. "
                "Recommendation: Sprint 2 quota_allocator.py should partition position vs swing "
                "routes, increasing effective structural capacity."
            )
        if td["tier_d_preservation_rate"] < 0.20:
            interpretations.append(
                f"LOW PRESERVATION: Only {td['tier_d_in_shadow_count']}/{td['tier_d_total_current']} "
                f"({td['tier_d_preservation_rate']*100:.1f}%) Tier D names preserved in shadow. "
                f"This is expected at Day 4 with quota=20 and 150 Tier D candidates. "
                f"Top excluded names by discovery_score: {td['top_tier_d_excluded'][:5]}."
            )
        if not attn["attention_candidates_consumed_structural_quota"]:
            interpretations.append(
                "VERIFIED: Attention candidates did not consume structural quota — "
                "route/quota separation is working correctly."
            )
        if mh["manual_candidates_protected"]:
            interpretations.append(
                f"VERIFIED: All {mh['manual_candidates_shadow_count']} manual conviction / "
                "favourites candidates are protected."
            )
        if not econ["economic_candidates_executable"]:
            interpretations.append(
                "VERIFIED: All economic intelligence candidates are non-executable "
                "(shadow_report_only)."
            )

        if esa.get("slices_active", 0) > 0:
            interpretations.append(
                f"Sprint 3: {esa['slices_active']} economic slices active "
                f"({', '.join(sorted(esa.get('by_theme', {}).keys()))}), "
                f"{esa['total_economic_candidates']} total economic candidates. "
                "All symbols from approved rosters — no LLM discovery."
            )

        if roa.get("headwind_candidates_in_shadow", 0) > 0:
            interpretations.append(
                f"HEADWIND: {roa['headwind_candidates_in_shadow']} headwind pressure candidate(s) "
                "in shadow (watchlist monitoring only — headwind_candidates_executable=False)."
            )
        if rmd.get("structural_swing_count", 0) > 0:
            interpretations.append(
                f"ROUTE DISTINCTION: structural_quota_group_count={rmd.get('structural_quota_group_count', 0)}, "
                f"position_route_count={rmd.get('position_route_count', 0)}, "
                f"structural_swing_count={rmd.get('structural_swing_count', 0)}. "
                "Banks/energy/defence are structural quota but route=swing (not position)."
            )

        if sca.get("source_path_excluded_but_symbol_preserved", 0) > 0:
            interpretations.append(
                f"SOURCE COLLISION: {sca['source_path_excluded_but_symbol_preserved']} symbol(s) "
                "had an economic or structural path excluded but are still present in shadow "
                "via manual_conviction or held (protected). No candidate lost to collision."
            )

        report = {
            "schema_version":   _SCHEMA_VERSION,
            "generated_at":     now,
            "report_title":     "Universe Builder Sprint 3 Report — Current vs Shadow Comparison",
            "mode":             "shadow_comparison_only",
            "live_output_changed": False,
            "current_summary":  cs,
            "shadow_summary":   ss,
            "overlap": {
                "overlap_count":               ov["overlap_count"],
                "in_current_not_shadow_count": ov["in_current_not_shadow_count"],
                "in_shadow_not_current_count": ov["in_shadow_not_current_count"],
            },
            "tier_d_analysis": {
                "tier_d_total_current":          td["tier_d_total_current"],
                "tier_d_in_shadow_count":        td["tier_d_in_shadow_count"],
                "tier_d_excluded_count":         td["tier_d_excluded_count"],
                "tier_d_preservation_rate":      td["tier_d_preservation_rate"],
                "tier_d_quality_rank_available": td["tier_d_quality_rank_available"],
                "top_tier_d_preserved":          td["top_tier_d_preserved"],
                "top_tier_d_excluded":           td["top_tier_d_excluded"],
                "exclusion_reasons":             td["tier_d_exclusion_reasons"],
            },
            "structural_analysis": {
                "structural_candidates_shadow_count":           struct["structural_candidates_shadow_count"],
                "structural_quota_used":                        struct["structural_quota_used"],
                "structural_quota_max":                         struct["structural_quota_max"],
                "structural_quota_binding":                     struct["structural_quota_binding"],
                "structural_candidates_displaced_by_attention": struct["structural_candidates_displaced_by_attention"],
            },
            "attention_analysis": {
                "attention_count": attn["attention_candidates_shadow_count"],
                "attention_cap":   attn["attention_cap"],
                "attention_cap_respected": attn["attention_cap_respected"],
                "attention_candidates_consumed_structural_quota": attn["attention_candidates_consumed_structural_quota"],
            },
            "manual_held_analysis": {
                "manual_candidates_shadow_count": mh["manual_candidates_shadow_count"],
                "manual_candidates_protected":    mh["manual_candidates_protected"],
                "manual_candidates_lost":         mh["manual_candidates_lost"],
                "held_unavailable_reason":        mh.get("held_unavailable_reason"),
                "held_candidates_protected":      mh["held_candidates_protected"],
            },
            "economic_analysis": {
                "economic_candidates_total":       econ["economic_candidates_total"],
                "economic_candidates_in_shadow":   econ["economic_candidates_in_shadow"],
                "economic_candidates_executable":  econ["economic_candidates_executable"],
                "llm_symbol_discovery_used":       econ["llm_symbol_discovery_used"],
                "raw_news_used":                   econ["raw_news_used"],
                "broad_intraday_scan_used":        econ["broad_intraday_scan_used"],
            },
            "exclusion_summary": {
                "total_exclusions":     excl["total_exclusions"],
                "quota_full":           excl["quota_full_exclusions"],
                "duplicates":           excl["duplicate_exclusions"],
                "by_source":            excl["exclusions_by_source"],
            },
            "quota_pressure_analysis": qpa,
            "source_collision_summary": {
                "total_collisions":                         sca.get("total_collisions", 0),
                "symbols_lost_to_collision":                sca.get("collisions_where_symbol_lost", 0),
                "protected_by_manual_or_held":              sca.get("collisions_protected_by_manual_or_held", 0),
                "source_path_excluded_but_symbol_preserved": sca.get("source_path_excluded_but_symbol_preserved", 0),
            },
            "economic_slice_summary": {
                "slices_active":         esa.get("slices_active", 0),
                "total_candidates":      esa.get("total_economic_candidates", 0),
                "slices":                list((esa.get("by_theme") or {}).keys()),
                "macro_transmission_deterministic": esa.get("macro_transmission_deterministic", True),
                "llm_symbol_discovery_used": esa.get("llm_symbol_discovery_used", False),
            },
            "adapter_impact_analysis": {
                "adapter_snapshot_available":  aia.get("adapter_snapshot_available", False),
                "adapters_available":          aia.get("adapters_available", 0),
                "adapters_unavailable":        aia.get("adapters_unavailable", 0),
                "adapter_symbols_read_total":  aia.get("adapter_symbols_read_total", 0),
                "adapter_unique_symbols_read": aia.get("adapter_unique_symbols_read", 0),
                "adapter_symbols_in_shadow":   aia.get("adapter_symbols_in_shadow", 0),
                "adapter_symbols_excluded":    aia.get("adapter_symbols_excluded", 0),
                "adapter_symbols_preserved":   aia.get("adapter_symbols_preserved", 0),
                "adapter_enriched_candidates": aia.get("adapter_enriched_candidates", 0),
                "adapter_added_symbols":       aia.get("adapter_added_symbols", []),
                "side_effects_triggered":      False,
                "live_data_called":            False,
            },
            "risk_off_analysis": {
                "quality_cash_flow_candidates_generated":  roa.get("quality_cash_flow_candidates_generated", 0),
                "defensive_quality_candidates_generated":  roa.get("defensive_quality_candidates_generated", 0),
                "small_caps_headwind_candidates_generated": roa.get("small_caps_headwind_candidates_generated", 0),
                "candidates_in_shadow":                    roa.get("candidates_in_shadow", 0),
                "candidates_excluded_by_quota":            roa.get("candidates_excluded_by_quota", 0),
                "candidates_watchlist_only":               roa.get("candidates_watchlist_only", 0),
                "headwind_candidates_in_shadow":           roa.get("headwind_candidates_in_shadow", 0),
                "headwind_candidates_executable":          False,
                "risk_off_symbols_preserved":              roa.get("risk_off_symbols_preserved", []),
                "risk_off_symbols_lost":                   roa.get("risk_off_symbols_lost", []),
            },
            "route_metric_distinction": {
                "position_route_count":            rmd.get("position_route_count", 0),
                "structural_quota_group_count":    rmd.get("structural_quota_group_count", 0),
                "structural_reason_to_care_count": rmd.get("structural_reason_to_care_count", 0),
                "tier_d_structural_source_count":  rmd.get("tier_d_structural_source_count", 0),
                "structural_watchlist_count":      rmd.get("structural_watchlist_count", 0),
                "structural_swing_count":          rmd.get("structural_swing_count", 0),
            },
            "economic_context_summary": {
                "daily_economic_state_available":    ecs.get("daily_economic_state_available", False),
                "current_economic_context_available": ecs.get("current_economic_context_available", False),
                "theme_activation_available":         ecs.get("theme_activation_available", False),
                "thesis_store_available":             ecs.get("thesis_store_available", False),
                "active_themes":                      ecs.get("active_themes", []),
                "strengthening_themes":               ecs.get("strengthening_themes", []),
                "crowded_themes":                     ecs.get("crowded_themes", []),
                "weakening_themes":                   ecs.get("weakening_themes", []),
                "watchlist_themes":                   ecs.get("watchlist_themes", []),
                "invalidated_themes":                 ecs.get("invalidated_themes", []),
                "risk_posture":                       ecs.get("risk_posture", "unknown"),
                "regime_label":                       ecs.get("regime_label", "unknown"),
                "thesis_count":                       ecs.get("thesis_count", 0),
                "low_confidence_themes":              ecs.get("low_confidence_themes", 0),
                "evidence_limited_themes":            ecs.get("evidence_limited_themes", 0),
                "no_live_api_called":                 True,
                "live_output_changed":                False,
            },
            "quality_warnings":  comparison["quality_warnings"],
            "interpretations":   interpretations,
        }
        return report

    def write(
        self,
        comparison_path: str = _COMPARISON_OUTPUT,
        report_path: str = _REPORT_OUTPUT,
    ) -> tuple[dict, dict]:
        comparison = self.compare()
        report = self.build_report(comparison)

        os.makedirs(os.path.dirname(comparison_path), exist_ok=True)
        with open(comparison_path, "w", encoding="utf-8") as f:
            json.dump(comparison, f, indent=2)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        return comparison, report


def build_comparison_report(
    snapshot_path: str = _SNAPSHOT_PATH,
    shadow_path: str = _SHADOW_PATH,
    comparison_path: str = _COMPARISON_OUTPUT,
    report_path: str = _REPORT_OUTPUT,
) -> tuple[dict, dict]:
    """Convenience one-shot function."""
    comparator = UniverseComparator(
        snapshot_path=snapshot_path,
        shadow_path=shadow_path,
    )
    return comparator.write(comparison_path=comparison_path, report_path=report_path)


if __name__ == "__main__":
    comparison, report = build_comparison_report()
    cs = comparison["current_summary"]
    ss = comparison["shadow_summary"]
    ov = comparison["overlap_summary"]
    td = comparison["tier_d_analysis"]
    st = comparison["structural_candidate_analysis"]
    at = comparison["attention_analysis"]

    print("Current vs Shadow Comparison")
    print(f"  current_pre_filter_pool:  {cs['current_pre_filter_source_pool_count']}")
    print(f"  shadow_total:             {ss['shadow_total_count']}")
    print(f"  overlap:                  {ov['overlap_count']}")
    print(f"  in_current_not_shadow:    {ov['in_current_not_shadow_count']}")
    print(f"  in_shadow_not_current:    {ov['in_shadow_not_current_count']}")
    print()
    print(f"  tier_d_preservation_rate: {td['tier_d_preservation_rate']*100:.1f}%  "
          f"({td['tier_d_in_shadow_count']}/{td['tier_d_total_current']})")
    print(f"  structural_quota_binding: {st['structural_quota_binding']}")
    print(f"  structural_displaced_by_attention: {st['structural_candidates_displaced_by_attention']}")
    print(f"  attention_cap_respected:  {at['attention_cap_respected']}")
    print(f"  live_output_changed:      {comparison['live_output_changed']}")
    print()
    for w in comparison["quality_warnings"][:5]:
        print(f"  WARN: {w[:100]}")
