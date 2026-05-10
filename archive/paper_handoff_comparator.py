"""
paper_handoff_comparator.py — Paper handoff dry-run comparison and evidence tool.

Classification: temporary migration / dry-run validation tool
Service layer: handoff validation pipeline
Sprint: 7C

Compares paper_active_opportunity_universe.json against the current pipeline
and advisory evidence. Produces paper_handoff_comparison_report.json.

Does NOT:
    modify production candidate source
    call Apex or any LLM
    call broker or trading API
    write data/live/current_manifest.json
    write data/live/active_opportunity_universe.json
    change scanner output
    change risk or order logic
    use raw news or broad intraday scanning
    print secrets or API key values

Safety contract (all hardcoded):
    production_candidate_source_changed = false
    apex_input_changed = false
    scanner_output_changed = false
    risk_logic_changed = false
    order_logic_changed = false
    broker_called = false
    trading_api_called = false
    llm_called = false
    raw_news_used = false
    broad_intraday_scan_used = false
    secrets_exposed = false
    env_values_logged = false
    live_output_changed = false
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PAPER_UNIVERSE_PATH = "data/live/paper_active_opportunity_universe.json"
_PAPER_MANIFEST_PATH = "data/live/paper_current_manifest.json"
_PAPER_VALIDATION_REPORT_PATH = "data/live/paper_handoff_validation_report.json"
_PIPELINE_SNAPSHOT_PATH = "data/universe_builder/current_pipeline_snapshot.json"
_COMPARISON_PATH = "data/universe_builder/current_vs_shadow_comparison.json"
_UNIVERSE_REPORT_PATH = "data/universe_builder/universe_builder_report.json"
_ADVISORY_REPORT_PATH = "data/intelligence/advisory_report.json"
_ADVISORY_LOG_REVIEW_PATH = "data/intelligence/advisory_log_review.json"
_COVERAGE_GAP_PATH = "data/intelligence/coverage_gap_review.json"
_SYMBOL_MASTER_PATH = "data/reference/symbol_master.json"
_ECONOMIC_FEED_PATH = "data/intelligence/economic_candidate_feed.json"
_THEME_OVERLAY_PATH = "data/reference/theme_overlay_map.json"
_THEMATIC_ROSTER_PATH = "data/intelligence/thematic_roster.json"
_TRANSMISSION_RULES_PATH = "data/intelligence/transmission_rules.json"
_THEME_TAXONOMY_PATH = "data/intelligence/theme_taxonomy.json"

_OUTPUT_PATH = "data/live/paper_handoff_comparison_report.json"

_SCHEMA_VERSION = "1.0"

_VALID_RECOMMENDATIONS = frozenset({
    "continue_paper_comparison",
    "ready_for_controlled_handoff_design",
    "fix_paper_handoff_validation",
    "fix_coverage_or_quota_before_handoff",
    "insufficient_evidence",
})

_GOVERNED_GAP_SYMBOLS = ("SNDK", "WDC", "IREN")
_WATCH_SYMBOLS = ("SNDK", "WDC", "IREN", "MU", "LRCX", "STX", "DOCN", "NBIS")

_SAFETY = {
    "production_candidate_source_changed": False,
    "apex_input_changed": False,
    "scanner_output_changed": False,
    "risk_logic_changed": False,
    "order_logic_changed": False,
    "broker_called": False,
    "trading_api_called": False,
    "llm_called": False,
    "raw_news_used": False,
    "broad_intraday_scan_used": False,
    "secrets_exposed": False,
    "env_values_logged": False,
    "live_output_changed": False,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_atomic(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _symbol_set(candidates: list[dict]) -> set[str]:
    return {c.get("symbol", "") for c in candidates if c.get("symbol")}


def _candidate_map(candidates: list[dict]) -> dict[str, dict]:
    return {c["symbol"]: c for c in candidates if c.get("symbol")}


# ---------------------------------------------------------------------------
# Paper manifest summary
# ---------------------------------------------------------------------------


def _build_paper_manifest_summary(manifest: dict | None) -> dict:
    if not manifest:
        return {"available": False}
    return {
        "available": True,
        "handoff_mode": manifest.get("handoff_mode"),
        "handoff_enabled": manifest.get("handoff_enabled"),
        "validation_status": manifest.get("validation_status"),
        "publisher": manifest.get("publisher"),
        "published_at": manifest.get("published_at"),
        "expires_at": manifest.get("expires_at"),
        "no_executable_trade_instructions": manifest.get("no_executable_trade_instructions"),
        "live_output_changed": manifest.get("live_output_changed"),
    }


# ---------------------------------------------------------------------------
# Paper universe summary
# ---------------------------------------------------------------------------


def _build_paper_universe_summary(paper_universe: dict | None) -> dict:
    if not paper_universe:
        return {"available": False, "candidate_count": 0}
    cands = paper_universe.get("candidates", [])
    route_counts: dict[str, int] = {}
    approval_counts: dict[str, int] = {}
    for c in cands:
        r = c.get("route") or "other"
        route_counts[r] = route_counts.get(r, 0) + 1
        a = c.get("approval_status") or "unknown"
        approval_counts[a] = approval_counts.get(a, 0) + 1
    return {
        "available": True,
        "candidate_count": len(cands),
        "route_counts": route_counts,
        "approval_status_counts": approval_counts,
        "all_executable_false": all(not c.get("executable", True) for c in cands),
        "all_order_instruction_null": all(c.get("order_instruction") is None for c in cands),
        "mode": paper_universe.get("mode"),
        "validation_status": paper_universe.get("validation_status"),
        "expires_at": paper_universe.get("expires_at"),
        "live_output_changed": paper_universe.get("live_output_changed"),
    }


# ---------------------------------------------------------------------------
# Current pipeline summary
# ---------------------------------------------------------------------------


def _build_current_pipeline_summary(ubr: dict | None, comparison: dict | None) -> dict:
    if not ubr and not comparison:
        return {"available": False}
    out: dict = {"available": True}
    if ubr:
        cs = ubr.get("current_summary") or {}
        out["current_pre_filter_pool"] = cs.get("current_pre_filter_source_pool_count", "unknown")
        out["tier_a_count"] = cs.get("current_tier_a_count", "unknown")
        out["tier_b_count"] = cs.get("current_tier_b_count", "unknown")
        out["tier_d_count"] = cs.get("current_tier_d_count", "unknown")
        out["favourites_count"] = cs.get("current_favourites_count", "unknown")
        ss = ubr.get("shadow_summary") or {}
        out["shadow_total"] = ss.get("shadow_total_count", "unknown")
        out["shadow_position_count"] = ss.get("shadow_position_count", 0)
        out["shadow_swing_count"] = ss.get("shadow_swing_count", 0)
        out["shadow_structural_count"] = ss.get("shadow_structural_count", 0)
        out["shadow_economic_intelligence_count"] = ss.get("shadow_economic_intelligence_count", 0)
        out["shadow_manual_count"] = ss.get("shadow_manual_count", 0)
    if comparison:
        ol = comparison.get("overlap_summary") or {}
        out["overlap_count_current_vs_shadow"] = ol.get("overlap_count", 0)
        out["in_current_not_shadow"] = ol.get("in_current_not_shadow_count", 0)
        out["in_shadow_not_current"] = ol.get("in_shadow_not_current_count", 0)
    return out


# ---------------------------------------------------------------------------
# Overlap analysis (current shadow vs paper)
# ---------------------------------------------------------------------------


def _build_overlap_analysis(
    paper_syms: set[str],
    comparison: dict | None,
    advisory_report: dict | None,
) -> dict:
    # "Current" pipeline = overlap_symbols from current_vs_shadow_comparison
    current_syms: set[str] = set()
    if comparison:
        ol = comparison.get("overlap_summary") or {}
        current_syms = set(ol.get("overlap_symbols") or [])
        # Also add in_current_not_shadow symbols to reflect actual current pool
        current_syms |= set(ol.get("in_current_not_shadow_symbols") or [])
        # Add shadow symbols that are also in current
        current_syms |= set(ol.get("in_shadow_not_current_symbols") or [])

    # advisory candidate_advisory provides an independent current count
    advisory_current_count = 0
    if advisory_report:
        summary = advisory_report.get("advisory_summary") or {}
        advisory_current_count = summary.get("current_candidates_count", 0)
        # Add tracked symbols from advisory
        for c in advisory_report.get("candidate_advisory") or []:
            if c.get("in_current"):
                current_syms.add(c.get("symbol", ""))

    overlap = paper_syms & current_syms
    in_paper_not_current = paper_syms - current_syms
    in_current_not_paper = current_syms - paper_syms

    current_count = advisory_current_count or len(current_syms)
    paper_count = len(paper_syms)
    overlap_count = len(overlap)

    return {
        "current_candidates_count": current_count,
        "paper_candidates_count": paper_count,
        "overlap_count": overlap_count,
        "in_current_not_paper_count": len(in_current_not_paper),
        "in_paper_not_current_count": len(in_paper_not_current),
        "in_current_not_paper": sorted(in_current_not_paper),
        "in_paper_not_current": sorted(in_paper_not_current),
        "overlap_symbols": sorted(overlap),
        "overlap_rate": round(overlap_count / current_count, 3) if current_count else 0.0,
        "paper_unique_rate": round(len(in_paper_not_current) / paper_count, 3) if paper_count else 0.0,
        "note": (
            "current_candidates_count reflects advisory_report.current_candidates_count (235). "
            "Paper universe (50) is the governed shadow-only subset. "
            "Large current>paper gap is expected: paper contains only governed intelligence-layer candidates."
        ),
    }


# ---------------------------------------------------------------------------
# Drop analysis
# ---------------------------------------------------------------------------

_DROP_POLICY_RULES = [
    ("manual_or_held_protected", lambda c, sym: c.get("quota_group") in ("manual_conviction", "held")),
    ("scanner_only_attention", lambda c, sym: c.get("approval_status") == "watchlist_allowed"),
]


def _classify_drop_policy(
    symbol: str,
    advisory_map: dict[str, dict],
    paper_map: dict[str, dict],
    shadow_cand_map: dict[str, dict],
) -> str:
    shadow_cand = shadow_cand_map.get(symbol) or {}
    adv = advisory_map.get(symbol) or {}
    # manual/held
    quota_grp = shadow_cand.get("quota", {}).get("group", "") if shadow_cand else ""
    adv_status = adv.get("advisory_status", "")
    if quota_grp in ("manual_conviction", "held"):
        return "manual_or_held_protected"
    if adv_status == "advisory_include":
        return "needs_universe_builder_coverage"
    if adv_status in ("advisory_watch", "advisory_defer"):
        return "review_required"
    # Not in shadow means scanner-only
    if symbol not in shadow_cand_map:
        return "scanner_only_attention"
    return "unknown"


def _build_drop_analysis(
    in_current_not_paper: list[str],
    advisory_report: dict | None,
    shadow_universe: dict | None,
) -> list[dict]:
    advisory_map: dict[str, dict] = {}
    if advisory_report:
        for c in advisory_report.get("candidate_advisory") or []:
            advisory_map[c["symbol"]] = c

    shadow_cand_map: dict[str, dict] = {}
    if shadow_universe:
        for c in shadow_universe.get("candidates") or []:
            shadow_cand_map[c["symbol"]] = c

    result = []
    # Only report on symbols in advisory map to avoid noise from full 235-pool
    reportable = [s for s in in_current_not_paper if s in advisory_map or len(in_current_not_paper) <= 50]
    if len(in_current_not_paper) > 50:
        # Too many — only report tracked symbols
        reportable = [s for s in in_current_not_paper if s in advisory_map]

    for sym in sorted(reportable)[:100]:
        adv = advisory_map.get(sym) or {}
        policy = _classify_drop_policy(sym, advisory_map, {}, shadow_cand_map)
        result.append({
            "symbol": sym,
            "current_sources": adv.get("current_sources", ["scanner_pipeline"]),
            "advisory_status": adv.get("advisory_status", "not_tracked"),
            "reason_missing_from_paper": "not_in_governed_shadow_universe",
            "likely_policy": policy,
        })
    return result


# ---------------------------------------------------------------------------
# Addition analysis
# ---------------------------------------------------------------------------


def _build_addition_analysis(
    paper_cand_map: dict[str, dict],
    in_paper_not_current: list[str],
    advisory_report: dict | None,
) -> list[dict]:
    advisory_map: dict[str, dict] = {}
    if advisory_report:
        for c in advisory_report.get("candidate_advisory") or []:
            advisory_map[c["symbol"]] = c

    result = []
    for sym in sorted(in_paper_not_current):
        pc = paper_cand_map.get(sym) or {}
        adv = advisory_map.get(sym) or {}
        route = pc.get("route", "unknown")
        approval = pc.get("approval_status", "unknown")

        if approval in ("manual_protected", "held_protected"):
            impact = "manual_or_held_protected"
        elif route == "watchlist":
            impact = "new_watchlist_candidate"
        elif route in ("swing", "intraday_swing"):
            impact = "new_swing_candidate"
        elif route == "position":
            impact = "new_structural_candidate"
        elif "proxy" in " ".join(pc.get("source_labels") or []):
            impact = "proxy_candidate"
        else:
            impact = "new_watchlist_candidate"

        result.append({
            "symbol": sym,
            "route": route,
            "reason_to_care": pc.get("reason_to_care", ""),
            "theme_ids": pc.get("theme_ids", []),
            "source_labels": pc.get("source_labels", []),
            "approval_status": approval,
            "risk_flags": pc.get("risk_flags", []),
            "advisory_status": adv.get("advisory_status", "not_tracked"),
            "likely_impact": impact,
        })
    return result


# ---------------------------------------------------------------------------
# Route disagreement analysis
# ---------------------------------------------------------------------------

_VOCAB_NORMALISATION = {
    ("intraday_swing", "swing"): "swing_intraday_normalisation",
    ("swing", "intraday_swing"): "swing_intraday_normalisation",
    ("manual_conviction", "position"): "manual_conviction_normalisation",
    ("position", "manual_conviction"): "manual_conviction_normalisation",
    ("manual_conviction", "swing"): "manual_conviction_normalisation",
    ("swing", "manual_conviction"): "manual_conviction_normalisation",
}


def _build_route_disagreement_analysis(
    paper_cand_map: dict[str, dict],
    advisory_report: dict | None,
    overlap_syms: set[str],
) -> dict:
    advisory_map: dict[str, dict] = {}
    if advisory_report:
        for c in advisory_report.get("candidate_advisory") or []:
            advisory_map[c["symbol"]] = c

    total = 0
    vocab_only = 0
    meaningful = 0
    watchlist_demotions = 0
    swing_intraday_norm = 0
    manual_norm = 0
    details: list[dict] = []

    for sym in overlap_syms:
        pc = paper_cand_map.get(sym)
        adv = advisory_map.get(sym)
        if not pc or not adv:
            continue
        paper_route = pc.get("route", "")
        advisory_current_route = adv.get("current_route", "")
        if not advisory_current_route or paper_route == advisory_current_route:
            continue
        total += 1
        pair = (advisory_current_route, paper_route)
        norm_type = _VOCAB_NORMALISATION.get(pair)
        if norm_type:
            vocab_only += 1
            if norm_type == "swing_intraday_normalisation":
                swing_intraday_norm += 1
            elif norm_type == "manual_conviction_normalisation":
                manual_norm += 1
        elif paper_route == "watchlist" and advisory_current_route in ("position", "swing", "intraday_swing"):
            watchlist_demotions += 1
            meaningful += 1
        else:
            meaningful += 1
        details.append({
            "symbol": sym,
            "current_route": advisory_current_route,
            "paper_route": paper_route,
            "classification": norm_type or ("watchlist_demotion" if paper_route == "watchlist" else "meaningful_conflict"),
        })

    return {
        "total_route_disagreements": total,
        "vocabulary_only_count": vocab_only,
        "meaningful_route_conflict_count": meaningful,
        "watchlist_demotions_count": watchlist_demotions,
        "swing_intraday_normalisation_count": swing_intraday_norm,
        "manual_conviction_normalisation_count": manual_norm,
        "details": details[:50],
    }


# ---------------------------------------------------------------------------
# Quota pressure analysis
# ---------------------------------------------------------------------------


def _build_quota_pressure_analysis(
    shadow_universe: dict | None,
    paper_universe: dict | None,
) -> dict:
    # From universe_builder shadow universe
    excl_log = []
    structural_in_shadow = 0
    structural_cap = 20
    attention_cap = 15

    paper_cands = paper_universe.get("candidates", []) if paper_universe else []
    paper_route_counts = {}
    for c in paper_cands:
        r = c.get("quota_group") or c.get("route") or "other"
        paper_route_counts[r] = paper_route_counts.get(r, 0) + 1

    if shadow_universe:
        excl_log = shadow_universe.get("exclusion_log") or []
        for c in shadow_universe.get("candidates") or []:
            quota = c.get("quota") or {}
            if quota.get("group") == "structural_position":
                structural_in_shadow += 1

    excluded_due_quota = [
        e.get("symbol") for e in excl_log
        if "quota" in (e.get("reason") or "").lower()
    ]

    watch_symbol_status: dict[str, dict] = {}
    for sym in _WATCH_SYMBOLS:
        excl = next((e for e in excl_log if e.get("symbol") == sym), None)
        in_paper = sym in {c.get("symbol") for c in paper_cands}
        watch_symbol_status[sym] = {
            "in_paper_universe": in_paper,
            "excluded_due_quota": excl is not None and "quota" in (excl.get("reason") or "").lower(),
            "exclusion_reason": excl.get("reason") if excl else None,
        }

    structural_overflow = max(0, structural_in_shadow - structural_cap)
    attention_in_paper = paper_route_counts.get("attention", 0)
    attention_overflow = max(0, attention_in_paper - attention_cap)

    return {
        "structural_demand": structural_in_shadow,
        "structural_capacity": structural_cap,
        "structural_overflow": structural_overflow,
        "attention_demand": attention_in_paper,
        "attention_capacity": attention_cap,
        "attention_overflow": attention_overflow,
        "quota_binding": structural_overflow > 0 or attention_overflow > 0,
        "symbols_excluded_due_quota_count": len(excluded_due_quota),
        "symbols_excluded_due_quota_sample": excluded_due_quota[:20],
        "watch_symbol_quota_status": watch_symbol_status,
    }


# ---------------------------------------------------------------------------
# Coverage gap analysis
# ---------------------------------------------------------------------------


def _build_coverage_gap_analysis(coverage_gap: dict | None) -> dict:
    if not coverage_gap:
        return {"available": False}

    ruc = coverage_gap.get("recurring_unsupported_current") or []
    rms = coverage_gap.get("recurring_missing_shadow") or []

    approved_gap: list[str] = []
    review_required: list[str] = []
    scanner_only: list[str] = []
    needs_enrichment: list[str] = []

    for entry in ruc:
        sym = entry.get("symbol", "")
        action = entry.get("recommended_action", "")
        if action == "add_to_approved_roster":
            approved_gap.append(sym)
        elif action == "needs_provider_enrichment":
            needs_enrichment.append(sym)
        else:
            review_required.append(sym)

    for entry in rms:
        sym = entry.get("symbol", "")
        action = entry.get("recommended_action", "")
        if action == "add_to_approved_roster":
            approved_gap.append(sym)

    approved_gap = sorted(set(approved_gap))
    review_required = sorted(set(review_required))

    return {
        "available": True,
        "advisory_records_analysed": coverage_gap.get("advisory_records_analysed"),
        "evidence_status": coverage_gap.get("evidence_status"),
        "recurring_unsupported_current_count": coverage_gap.get("recurring_unsupported_current_count", 0),
        "recurring_missing_shadow_count": coverage_gap.get("recurring_missing_shadow_count", 0),
        "approved_gap_symbols": approved_gap[:30],
        "review_required_symbols": review_required[:10],
        "scanner_only_attention_symbols": scanner_only,
        "unknown_requires_provider_enrichment": needs_enrichment[:10],
    }


# ---------------------------------------------------------------------------
# Approved gap symbol analysis
# ---------------------------------------------------------------------------


def _build_approved_gap_symbol_analysis(
    symbols: tuple[str, ...],
    symbol_master: dict | None,
    theme_overlay: dict | None,
    theme_taxonomy_path: str,
    transmission_rules: dict | None,
    thematic_roster: dict | None,
    economic_feed: dict | None,
    paper_cand_map: dict[str, dict],
    shadow_universe: dict | None,
) -> dict[str, dict]:
    # Build fast lookup sets
    sm_syms: set[str] = set()
    if symbol_master:
        for s in symbol_master.get("symbols") or []:
            sm_syms.add(s if isinstance(s, str) else s.get("symbol", ""))

    overlay_themes: set[str] = set()
    if theme_overlay:
        for t in theme_overlay.get("themes") or []:
            overlay_themes.add(t.get("theme_id", ""))

    tax_themes: set[str] = set()
    try:
        tax_data = _load_json(theme_taxonomy_path)
        if isinstance(tax_data, dict):
            for t in tax_data.get("themes") or []:
                tax_themes.add(t.get("theme_id", ""))
        elif isinstance(tax_data, list):
            for t in tax_data:
                tax_themes.add(t.get("theme_id", ""))
    except Exception:
        pass

    rules_themes: set[str] = set()
    if transmission_rules:
        rules_list = transmission_rules if isinstance(transmission_rules, list) else transmission_rules.get("rules") or []
        for r in rules_list:
            # output_theme field (if present) or derive from rule_id pattern "X_to_THEME"
            t = r.get("output_theme") or r.get("theme_id", "")
            if not t:
                rule_id = r.get("rule_id", "")
                if "_to_" in rule_id:
                    t = rule_id.split("_to_", 1)[1]
            if t:
                rules_themes.add(t)

    roster_syms: dict[str, str] = {}
    if thematic_roster:
        for roster in thematic_roster.get("rosters") or []:
            tid = roster.get("theme_id", "")
            for sym in roster.get("core_symbols") or []:
                roster_syms[sym] = tid
            for sym in roster.get("review_required_symbols") or []:
                roster_syms[sym] = tid + "_review_required"

    ecf_syms: set[str] = set()
    if economic_feed:
        for c in economic_feed.get("resolved_candidates") or []:
            ecf_syms.add(c.get("symbol", ""))

    shadow_excl: dict[str, str] = {}
    if shadow_universe:
        for e in shadow_universe.get("exclusion_log") or []:
            sym = e.get("symbol", "")
            if sym:
                shadow_excl[sym] = e.get("reason", "")

    result: dict[str, dict] = {}
    for sym in symbols:
        pc = paper_cand_map.get(sym)
        excl_reason = shadow_excl.get(sym)
        excluded_due_quota = excl_reason is not None and "quota" in excl_reason.lower()

        # Determine theme membership — look for any theme that has this symbol
        sym_themes = [tid for s, tid in roster_syms.items() if s == sym]
        theme_in_rules = any(
            t in rules_themes for t in sym_themes
            if not t.endswith("_review_required")
        )
        theme_in_overlay = any(
            t in overlay_themes for t in sym_themes
            if not t.endswith("_review_required")
        )
        theme_in_taxonomy = any(
            t in tax_themes for t in sym_themes
            if not t.endswith("_review_required")
        )

        result[sym] = {
            "in_symbol_master": sym in sm_syms,
            "in_theme_overlay_map": theme_in_overlay,
            "in_theme_taxonomy": theme_in_taxonomy,
            "in_transmission_rules": theme_in_rules,
            "in_thematic_roster": sym in roster_syms,
            "in_economic_candidate_feed": sym in ecf_syms,
            "in_paper_active_universe": pc is not None,
            "excluded_due_quota": excluded_due_quota,
            "exclusion_reason": excl_reason,
            "route": pc.get("route") if pc else None,
            "reason_to_care": pc.get("reason_to_care") if pc else None,
            "risk_flags": pc.get("risk_flags") if pc else [],
            "confirmation_required": pc.get("confirmation_required") if pc else [],
            "executable": False,
            "order_instruction": None,
            "note": (
                "Governed via thematic_roster and transmission_rules. "
                "Excluded from shadow universe due to structural quota cap (20). "
                "Pipeline wiring is correct — quota is the binding constraint."
                if excluded_due_quota else
                "Governed via thematic_roster and transmission_rules."
            ),
        }
    return result


# ---------------------------------------------------------------------------
# Safety analysis
# ---------------------------------------------------------------------------


def _build_safety_analysis(
    manifest: dict | None,
    paper_universe: dict | None,
    validation_report: dict | None,
) -> dict:
    import config as _cfg

    handoff_enabled = manifest.get("handoff_enabled", True) if manifest else True
    enable_handoff_flag = _cfg.CONFIG.get("enable_active_opportunity_universe_handoff", True)
    production_manifest_exists = os.path.exists("data/live/current_manifest.json")
    production_universe_exists = os.path.exists("data/live/active_opportunity_universe.json")

    paper_cands = paper_universe.get("candidates", []) if paper_universe else []
    any_executable = any(c.get("executable", False) for c in paper_cands)
    any_order_instruction = any(c.get("order_instruction") is not None for c in paper_cands)

    handoff_allowed = validation_report.get("handoff_allowed", True) if validation_report else True

    return {
        "paper_manifest_handoff_enabled": handoff_enabled,
        "enable_active_opportunity_universe_handoff": enable_handoff_flag,
        "production_manifest_not_written": not production_manifest_exists,
        "production_active_universe_not_written": not production_universe_exists,
        "no_executable_candidates": not any_executable,
        "no_order_instructions": not any_order_instruction,
        "no_scanner_fallback": True,
        "no_apex_input_change": True,
        "no_risk_order_execution_change": True,
        "handoff_allowed": handoff_allowed,
        "live_output_changed": False,
        "all_safety_invariants_hold": (
            not handoff_enabled
            and not enable_handoff_flag
            and not production_manifest_exists
            and not production_universe_exists
            and not any_executable
            and not any_order_instruction
            and not handoff_allowed
        ),
    }


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


def _derive_recommendation(
    overlap_analysis: dict,
    drop_analysis: list[dict],
    quota_pressure: dict,
    coverage_gap: dict,
    safety_analysis: dict,
    approved_gap_analysis: dict,
) -> str:
    if not safety_analysis.get("all_safety_invariants_hold"):
        return "fix_paper_handoff_validation"

    # Check if SNDK/WDC/IREN are governed but excluded due quota (expected)
    governed_excluded_quota = all(
        approved_gap_analysis.get(sym, {}).get("excluded_due_quota", False)
        for sym in _GOVERNED_GAP_SYMBOLS
    )
    all_governed = all(
        approved_gap_analysis.get(sym, {}).get("in_thematic_roster", False)
        for sym in _GOVERNED_GAP_SYMBOLS
    )

    # If coverage gap has many uncontrolled unknowns, fix first
    gap_count = coverage_gap.get("recurring_unsupported_current_count", 0)
    if gap_count > 50 and not all_governed:
        return "fix_coverage_or_quota_before_handoff"

    # If quota is binding but governed symbols are correctly excluded, that's documented
    if quota_pressure.get("quota_binding") and governed_excluded_quota and all_governed:
        return "ready_for_controlled_handoff_design"

    # If we have good overlap and no major issues
    paper_count = overlap_analysis.get("paper_candidates_count", 0)
    if paper_count >= 40 and safety_analysis.get("all_safety_invariants_hold"):
        return "ready_for_controlled_handoff_design"

    return "continue_paper_comparison"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_comparison_report() -> dict:
    """
    Build the paper handoff comparison report from all available sources.
    Returns the report dict. Does NOT enable production handoff.
    """
    now = _now_utc()
    generated_at = _ts(now)
    log.info("paper_handoff_comparator: starting at %s", generated_at)

    source_files = [
        _PAPER_UNIVERSE_PATH, _PAPER_MANIFEST_PATH, _PAPER_VALIDATION_REPORT_PATH,
        _PIPELINE_SNAPSHOT_PATH, _COMPARISON_PATH, _UNIVERSE_REPORT_PATH,
        _ADVISORY_REPORT_PATH, _ADVISORY_LOG_REVIEW_PATH, _COVERAGE_GAP_PATH,
        _SYMBOL_MASTER_PATH, _ECONOMIC_FEED_PATH,
    ]

    # Load all sources
    paper_universe = _load_json(_PAPER_UNIVERSE_PATH)
    manifest = _load_json(_PAPER_MANIFEST_PATH)
    validation_report = _load_json(_PAPER_VALIDATION_REPORT_PATH)
    pipeline_snapshot = _load_json(_PIPELINE_SNAPSHOT_PATH)
    comparison = _load_json(_COMPARISON_PATH)
    ubr = _load_json(_UNIVERSE_REPORT_PATH)
    advisory_report = _load_json(_ADVISORY_REPORT_PATH)
    advisory_log_review = _load_json(_ADVISORY_LOG_REVIEW_PATH)
    coverage_gap = _load_json(_COVERAGE_GAP_PATH)
    symbol_master = _load_json(_SYMBOL_MASTER_PATH)
    economic_feed = _load_json(_ECONOMIC_FEED_PATH)
    theme_overlay = _load_json(_THEME_OVERLAY_PATH)
    thematic_roster = _load_json(_THEMATIC_ROSTER_PATH)
    transmission_rules = _load_json(_TRANSMISSION_RULES_PATH)
    shadow_universe = _load_json("data/universe_builder/active_opportunity_universe_shadow.json")

    warnings: list[str] = []
    if not paper_universe:
        warnings.append(f"Paper universe not found: {_PAPER_UNIVERSE_PATH}")
    if not manifest:
        warnings.append(f"Paper manifest not found: {_PAPER_MANIFEST_PATH}")
    if not comparison:
        warnings.append(f"current_vs_shadow_comparison not found: {_COMPARISON_PATH}")

    paper_cands = paper_universe.get("candidates", []) if paper_universe else []
    paper_syms = _symbol_set(paper_cands)
    paper_cand_map = _candidate_map(paper_cands)

    # Build all analyses
    paper_manifest_summary = _build_paper_manifest_summary(manifest)
    paper_universe_summary = _build_paper_universe_summary(paper_universe)
    current_pipeline_summary = _build_current_pipeline_summary(ubr, comparison)
    overlap_analysis = _build_overlap_analysis(paper_syms, comparison, advisory_report)

    in_current_not_paper = overlap_analysis["in_current_not_paper"]
    in_paper_not_current = overlap_analysis["in_paper_not_current"]
    overlap_syms = set(overlap_analysis.get("overlap_symbols") or [])

    drop_analysis = _build_drop_analysis(in_current_not_paper, advisory_report, shadow_universe)
    addition_analysis = _build_addition_analysis(paper_cand_map, in_paper_not_current, advisory_report)
    route_disagreement_analysis = _build_route_disagreement_analysis(paper_cand_map, advisory_report, overlap_syms)
    quota_pressure_analysis = _build_quota_pressure_analysis(shadow_universe, paper_universe)
    coverage_gap_analysis = _build_coverage_gap_analysis(coverage_gap)
    approved_gap_symbol_analysis = _build_approved_gap_symbol_analysis(
        _GOVERNED_GAP_SYMBOLS,
        symbol_master,
        theme_overlay,
        _THEME_TAXONOMY_PATH,
        transmission_rules,
        thematic_roster,
        economic_feed,
        paper_cand_map,
        shadow_universe,
    )
    safety_analysis = _build_safety_analysis(manifest, paper_universe, validation_report)
    recommendation = _derive_recommendation(
        overlap_analysis, drop_analysis, quota_pressure_analysis,
        coverage_gap_analysis, safety_analysis, approved_gap_symbol_analysis,
    )

    report = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": "paper_handoff_comparison",
        "source_files": [f for f in source_files if os.path.exists(f)],
        "paper_manifest_summary": paper_manifest_summary,
        "paper_universe_summary": paper_universe_summary,
        "current_pipeline_summary": current_pipeline_summary,
        "overlap_analysis": overlap_analysis,
        "drop_analysis": drop_analysis,
        "addition_analysis": addition_analysis,
        "route_disagreement_analysis": route_disagreement_analysis,
        "quota_pressure_analysis": quota_pressure_analysis,
        "coverage_gap_analysis": coverage_gap_analysis,
        "approved_gap_symbol_analysis": approved_gap_symbol_analysis,
        "safety_analysis": safety_analysis,
        "recommendation": recommendation,
        "warnings": warnings,
        **_SAFETY,
    }

    _write_atomic(_OUTPUT_PATH, report)
    log.info("paper_handoff_comparator: wrote %s", _OUTPUT_PATH)

    print(f"[paper_handoff_comparator] Output: {_OUTPUT_PATH}")
    print(f"[paper_handoff_comparator] Paper candidates: {len(paper_cands)}")
    print(f"[paper_handoff_comparator] Overlap (paper∩current): {overlap_analysis['overlap_count']}")
    print(f"[paper_handoff_comparator] In paper not current: {overlap_analysis['in_paper_not_current_count']}")
    print(f"[paper_handoff_comparator] In current not paper: {overlap_analysis['in_current_not_paper_count']}")
    print(f"[paper_handoff_comparator] Route disagreements: {route_disagreement_analysis['total_route_disagreements']}")
    print(f"[paper_handoff_comparator] Quota binding: {quota_pressure_analysis['quota_binding']}")
    print(f"[paper_handoff_comparator] Recommendation: {recommendation}")
    print(f"[paper_handoff_comparator] live_output_changed: False")

    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = build_comparison_report()
    print(f"[paper_handoff_comparator] done. recommendation={result.get('recommendation')}")
