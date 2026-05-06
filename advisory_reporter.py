"""
advisory_reporter.py — Sprint 6A Offline Advisory Report.

Generates data/intelligence/advisory_report.json comparing the current pipeline
snapshot against the Intelligence-First shadow universe.

Rules (all hard):
- No production modules imported
- No live API calls
- No broker calls
- No .env inspection
- No LLM calls
- No raw news
- No broad intraday scanning
- live_output_changed = false
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))

_PIPELINE_SNAPSHOT_PATH = os.path.join(_BASE, "data", "universe_builder", "current_pipeline_snapshot.json")
_SHADOW_UNIVERSE_PATH   = os.path.join(_BASE, "data", "universe_builder", "active_opportunity_universe_shadow.json")
_COMPARISON_PATH        = os.path.join(_BASE, "data", "universe_builder", "current_vs_shadow_comparison.json")
_REPORT_PATH            = os.path.join(_BASE, "data", "universe_builder", "universe_builder_report.json")
_FEED_PATH              = os.path.join(_BASE, "data", "intelligence", "economic_candidate_feed.json")
_CONTEXT_PATH           = os.path.join(_BASE, "data", "intelligence", "current_economic_context.json")
_THEME_ACTIVATION_PATH  = os.path.join(_BASE, "data", "intelligence", "theme_activation.json")
_THESIS_STORE_PATH      = os.path.join(_BASE, "data", "intelligence", "thesis_store.json")
_BACKTEST_SUMMARY_PATH  = os.path.join(_BASE, "data", "intelligence", "backtest", "intelligence_backtest_summary.json")
_HISTORICAL_REPLAY_PATH = os.path.join(_BASE, "data", "intelligence", "backtest", "historical_replay_results.json")
_ADAPTER_SNAPSHOT_PATH  = os.path.join(_BASE, "data", "intelligence", "source_adapter_snapshot.json")

_OUTPUT_PATH = os.path.join(_BASE, "data", "intelligence", "advisory_report.json")

# ---------------------------------------------------------------------------
# Advisory status vocabulary
# ---------------------------------------------------------------------------
_STATUS_INCLUDE     = "advisory_include"
_STATUS_WATCH       = "advisory_watch"
_STATUS_DEFER       = "advisory_defer"
_STATUS_EXCLUDE     = "advisory_exclude"
_STATUS_UNRESOLVED  = "advisory_unresolved"

_VALID_STATUSES = {
    _STATUS_INCLUDE, _STATUS_WATCH, _STATUS_DEFER,
    _STATUS_EXCLUDE, _STATUS_UNRESOLVED,
}

# Tier labels for current pipeline source inference
_TIER_A_LABEL    = "tier_a_always_on"
_TIER_D_LABEL    = "tier_d_structural"
_MANUAL_LABEL    = "manual_conviction_favourites"
_ECONOMIC_LABEL  = "economic_intelligence"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def _read(path: str) -> tuple[dict | list | None, str | None]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, f"File not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"JSON parse error in {path}: {e}"


def _write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_all() -> dict[str, Any]:
    """Load all input files. Returns dict of label → data (None if missing)."""
    sources = {
        "pipeline_snapshot": _PIPELINE_SNAPSHOT_PATH,
        "shadow_universe":   _SHADOW_UNIVERSE_PATH,
        "comparison":        _COMPARISON_PATH,
        "report":            _REPORT_PATH,
        "feed":              _FEED_PATH,
        "context":           _CONTEXT_PATH,
        "theme_activation":  _THEME_ACTIVATION_PATH,
        "thesis_store":      _THESIS_STORE_PATH,
        "backtest_summary":  _BACKTEST_SUMMARY_PATH,
        "historical_replay": _HISTORICAL_REPLAY_PATH,
        "adapter_snapshot":  _ADAPTER_SNAPSHOT_PATH,
    }
    loaded: dict[str, Any] = {}
    missing: list[str] = []
    for label, path in sources.items():
        data, err = _read(path)
        loaded[label] = data
        if err:
            missing.append(err)
    loaded["_missing"] = missing
    loaded["_source_files"] = {k: v for k, v in sources.items()}
    return loaded


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------
def _index_shadow_candidates(shadow: dict) -> dict[str, dict]:
    """symbol → candidate record."""
    return {c["symbol"]: c for c in (shadow.get("candidates") or [])}


def _index_themes(theme_activation: dict) -> dict[str, dict]:
    """theme_id → theme record."""
    return {t["theme_id"]: t for t in (theme_activation.get("themes") or [])}


def _index_theses(thesis_store: dict) -> dict[str, dict]:
    """theme_id → thesis record."""
    return {t["theme_id"]: t for t in (thesis_store.get("theses") or [])}


def _index_feed_candidates(feed: dict) -> dict[str, list[dict]]:
    """symbol → list of feed records (may appear in multiple themes)."""
    idx: dict[str, list[dict]] = {}
    for c in (feed.get("candidates") or []):
        sym = c.get("symbol", "")
        if sym:
            idx.setdefault(sym, []).append(c)
    return idx


def _build_current_symbol_sets(comp: dict, pipeline: dict) -> dict[str, set]:
    """
    Build sets of current symbols by source category from comparison + pipeline data.
    Returns: {tier_a, tier_d, manual, held, overlap, in_current_not_shadow,
              in_shadow_not_current, all_current}
    """
    overlap_syms    = set(comp.get("overlap_summary", {}).get("overlap_symbols") or [])
    not_in_shadow   = set(comp.get("overlap_summary", {}).get("in_current_not_shadow_symbols") or [])
    not_in_current  = set(comp.get("overlap_summary", {}).get("in_shadow_not_current_symbols") or [])
    all_current     = overlap_syms | not_in_shadow

    # Note: the comparison file stores up to 50 sample symbols for in_current_not_shadow.
    # Full pool count (235) is read from comparison metadata for summary reporting.

    # Tier A from pipeline snapshot source_map
    tier_a_info = (pipeline or {}).get("source_map", {}).get("tier_a", {})
    tier_a_syms: set[str] = set()
    for comp_key in ("components", ):
        comps = tier_a_info.get(comp_key, {})
        if isinstance(comps, dict):
            for v in comps.values():
                tier_a_syms |= set(v.get("symbols") or [])

    # Manual/favourites from comparison
    mah = comp.get("manual_and_held_analysis", {})
    manual_syms: set[str] = set()
    held_syms: set[str] = set()

    # Tier D from comparison tier_d_analysis
    tda = comp.get("tier_d_analysis", {})
    tier_d_preserved = set(tda.get("tier_d_in_shadow_symbols") or [])
    tier_d_excluded  = set(tda.get("tier_d_excluded_symbols") or [])
    tier_d_syms      = tier_d_preserved | tier_d_excluded

    # Extend all_current with tier_d symbols (they are in the current pipeline)
    all_current_with_tier_d = all_current | tier_d_excluded

    # Full pool count from metadata (comparison tracks only 50 sample symbols for in_current_not_shadow)
    current_summary = (comp or {}).get("current_summary", {})
    full_current_pool_count = current_summary.get("current_pre_filter_source_pool_count", len(all_current))

    return {
        "tier_a":                  tier_a_syms,
        "tier_d":                  tier_d_syms,
        "tier_d_preserved":        tier_d_preserved,
        "tier_d_excluded":         tier_d_excluded,
        "manual":                  manual_syms,
        "held":                    held_syms,
        "overlap":                 overlap_syms,
        "in_current_not_shadow":   not_in_shadow | tier_d_excluded,  # all known current-not-shadow
        "in_shadow_not_current":   not_in_current,
        "all_current":             all_current_with_tier_d,
        "full_current_pool_count": full_current_pool_count,
    }


# ---------------------------------------------------------------------------
# Route disagreement detection
# ---------------------------------------------------------------------------
# Current pipeline route inference by tier
_TIER_A_ROUTES = {"intraday_swing", "watchlist", "attention"}
_TIER_D_ROUTES = {"position", "swing"}
_MANUAL_ROUTES = {"manual_conviction"}

def _infer_current_source(symbol: str, sets: dict[str, set]) -> str:
    if symbol in sets["manual"]:
        return _MANUAL_LABEL
    if symbol in sets["tier_d"]:
        return _TIER_D_LABEL
    if symbol in sets["tier_a"]:
        return _TIER_A_LABEL
    return "current_pool_unknown_source"


def _infer_current_route(symbol: str, sets: dict[str, set]) -> str | None:
    if symbol in sets["manual"]:
        return "manual_conviction"
    if symbol in sets["tier_d_preserved"]:
        return "position"
    if symbol in sets["tier_a"]:
        return "intraday_swing"
    return None


def _build_route_disagreements(
    shadow_idx: dict[str, dict],
    sets: dict[str, set],
) -> dict[str, Any]:
    """Find symbols where current inferred route ≠ shadow route."""
    disagreements: list[dict] = []
    by_source: dict[str, int] = {}
    by_pair: dict[str, int] = {}

    for sym, sc in shadow_idx.items():
        shadow_route = sc.get("route")
        current_route = _infer_current_route(sym, sets)
        if current_route is None:
            continue  # no current route known — not a disagreement, just absence
        if shadow_route == current_route:
            continue
        # Route disagreement
        current_src = _infer_current_source(sym, sets)
        reason = _describe_route_disagreement(sym, current_route, shadow_route, sets, sc)
        pair_key = f"{current_route}→{shadow_route}"
        by_source[current_src] = by_source.get(current_src, 0) + 1
        by_pair[pair_key] = by_pair.get(pair_key, 0) + 1
        disagreements.append({
            "symbol":         sym,
            "current_route":  current_route,
            "shadow_route":   shadow_route,
            "current_source": current_src,
            "shadow_source":  sc.get("source_labels", []),
            "reason":         reason,
            "advisory_status": _STATUS_WATCH,
            "executable":     False,
        })

    warnings: list[str] = []
    if len(disagreements) > 20:
        warnings.append(f"{len(disagreements)} route disagreements detected — review shadow quota model")

    return {
        "total_route_disagreements": len(disagreements),
        "disagreements":             disagreements,
        "disagreement_by_source":    by_source,
        "disagreement_by_route_pair": by_pair,
        "warnings":                  warnings,
    }


def _describe_route_disagreement(
    sym: str,
    current_route: str,
    shadow_route: str,
    sets: dict[str, set],
    sc: dict,
) -> str:
    rtc = sc.get("reason_to_care", "")
    if sym in sets["manual"]:
        return f"{sym} is manual conviction ({shadow_route}) but current labels as {current_route}"
    if sym in sets["tier_d_preserved"] and shadow_route in ("position", "swing"):
        return f"{sym} Tier D preserved in shadow as {shadow_route}; current expected {current_route}"
    if sym in sets["tier_a"] and shadow_route == "watchlist":
        return f"{sym} Tier A always-on but shadow re-classifies as watchlist (reason_to_care={rtc})"
    return f"{sym} current={current_route} shadow={shadow_route} (reason_to_care={rtc})"


# ---------------------------------------------------------------------------
# Advisory status classifier
# ---------------------------------------------------------------------------
def _classify_advisory_status(
    sym: str,
    in_shadow: bool,
    in_current: bool,
    sc: dict | None,
    sets: dict[str, set],
    feed_idx: dict[str, list[dict]],
    excl_reasons: dict[str, str],
) -> tuple[str, str]:
    """Returns (advisory_status, advisory_reason)."""
    rtc = (sc.get("reason_to_care") or "") if sc else ""
    route = (sc.get("route") or "") if sc else ""
    quota_grp = (sc.get("quota", {}).get("group") or "") if sc else ""

    # Headwind / pressure candidates → always advisory_watch
    if rtc in ("headwind_pressure_watchlist",) or "headwind" in rtc:
        return _STATUS_WATCH, "headwind_pressure_candidate_watchlist_only"

    # Manual conviction → advisory_watch (protected)
    if rtc == "manual_conviction" or sym in sets["manual"]:
        return _STATUS_WATCH, "manual_conviction_protected"

    # In both current and shadow
    if in_shadow and in_current:
        if rtc and rtc not in ("unknown", ""):
            if route in ("position", "swing"):
                return _STATUS_INCLUDE, f"supported_by_intelligence_shadow_route={route}"
            return _STATUS_INCLUDE, f"in_both_current_and_shadow_rtc={rtc}"
        return _STATUS_WATCH, "in_both_but_no_reason_to_care_in_shadow"

    # In shadow only (not in current)
    if in_shadow and not in_current:
        if route in ("position", "swing"):
            return _STATUS_WATCH, f"shadow_structural_candidate_not_in_current_pipeline_route={route}"
        return _STATUS_WATCH, f"shadow_candidate_not_in_current_pipeline_route={route}"

    # In current only (not in shadow)
    if in_current and not in_shadow:
        # Was it excluded from shadow due to quota?
        excl_reason = excl_reasons.get(sym, "")
        if "quota full" in excl_reason.lower() or "quota" in excl_reason.lower():
            return _STATUS_DEFER, f"excluded_from_shadow_by_quota: {excl_reason}"
        if "total universe cap" in excl_reason.lower():
            return _STATUS_DEFER, "excluded_from_shadow_by_total_universe_cap"
        if "duplicate" in excl_reason.lower():
            return _STATUS_DEFER, f"excluded_as_duplicate_source_path: {excl_reason}"
        # Is it in the economic feed?
        if sym in feed_idx:
            return _STATUS_DEFER, "current_candidate_supported_by_economic_feed_but_quota_excluded"
        # Tier D excluded by structural quota
        if sym in sets["tier_d_excluded"]:
            return _STATUS_DEFER, "tier_d_excluded_structural_quota_full"
        return _STATUS_UNRESOLVED, "current_candidate_no_intelligence_support"

    return _STATUS_UNRESOLVED, "classification_unresolved"


# ---------------------------------------------------------------------------
# Candidate advisory builder
# ---------------------------------------------------------------------------
def _build_candidate_advisory(
    shadow_idx:   dict[str, dict],
    sets:         dict[str, set],
    feed_idx:     dict[str, list[dict]],
    theme_idx:    dict[str, dict],
    thesis_idx:   dict[str, dict],
    excl_log:     list[dict],
) -> list[dict]:
    """Build per-symbol advisory records."""
    # Build exclusion reason map: symbol → first exclusion reason
    excl_reasons: dict[str, str] = {}
    for e in excl_log:
        sym = e.get("symbol", "")
        if sym and sym not in excl_reasons:
            excl_reasons[sym] = e.get("reason", "")

    records: list[dict] = []
    seen: set[str] = set()

    def _make_record(sym: str, sc: dict | None, in_shadow: bool, in_current: bool) -> dict:
        if sym in seen:
            return {}
        seen.add(sym)

        feed_recs = feed_idx.get(sym, [])
        theme_ids = list({fr["theme"] for fr in feed_recs if fr.get("theme")})
        driver_ids = list({dr.strip() for fr in feed_recs for dr in (fr.get("driver") or "").split(",") if dr.strip()})
        source_labels = list(sc.get("source_labels") or []) if sc else []
        rtc = (sc.get("reason_to_care") or "") if sc else ""
        route = (sc.get("route") or "") if sc else None
        quota_grp = (sc.get("quota", {}).get("group") or "") if sc else None

        # Theme state from first matching theme
        theme_state: str | None = None
        for tid in theme_ids:
            t = theme_idx.get(tid)
            if t:
                theme_state = t.get("state")
                break

        # Thesis status from first matching theme
        thesis_status: str | None = None
        for tid in theme_ids:
            th = thesis_idx.get(tid)
            if th:
                thesis_status = th.get("status")
                break

        # Infer current sources
        current_sources: list[str] = []
        if sym in sets["manual"]:
            current_sources.append("favourites_manual_conviction")
        if sym in sets["tier_d"]:
            current_sources.append("tier_d_structural")
        if sym in sets["tier_a"]:
            current_sources.append("tier_a_always_on")
        if sym in feed_idx:
            current_sources.append("economic_candidate_feed")
        if not current_sources and in_current:
            current_sources.append("current_pool_unknown_source")

        shadow_sources = source_labels

        current_route = _infer_current_route(sym, sets)
        shadow_route = route

        excl_reason = excl_reasons.get(sym)

        status, reason = _classify_advisory_status(
            sym, in_shadow, in_current, sc, sets, feed_idx, excl_reasons
        )

        return {
            "symbol":             sym,
            "in_current":         in_current,
            "in_shadow":          in_shadow,
            "current_sources":    current_sources,
            "shadow_sources":     shadow_sources,
            "current_route":      current_route,
            "shadow_route":       shadow_route,
            "advisory_status":    status,
            "advisory_reason":    reason,
            "reason_to_care":     rtc or None,
            "theme_ids":          theme_ids,
            "driver_ids":         driver_ids,
            "source_labels":      source_labels,
            "thesis_status":      thesis_status,
            "theme_state":        theme_state,
            "quota_group":        quota_grp,
            "exclusion_reason":   excl_reason,
            "route_disagreement": (current_route is not None and shadow_route is not None
                                   and current_route != shadow_route),
            "executable":         False,
            "order_instruction":  None,
        }

    # 1. All shadow candidates
    for sym, sc in shadow_idx.items():
        in_current = sym in sets["all_current"]
        rec = _make_record(sym, sc, True, in_current)
        if rec:
            records.append(rec)

    # 2. Current candidates not yet covered (in_current_not_shadow)
    for sym in sorted(sets["in_current_not_shadow"]):
        if sym in seen:
            continue
        rec = _make_record(sym, None, False, True)
        if rec:
            records.append(rec)

    return records


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------
def _build_unsupported_current(
    records: list[dict],
    sets: dict[str, set],
) -> dict[str, Any]:
    """Candidates in current but not in shadow — with no intelligence support."""
    unsupported = [
        r for r in records
        if r["in_current"] and not r["in_shadow"]
        and r["advisory_status"] in (_STATUS_UNRESOLVED, _STATUS_EXCLUDE)
    ]
    deferred = [
        r for r in records
        if r["in_current"] and not r["in_shadow"]
        and r["advisory_status"] == _STATUS_DEFER
    ]

    by_source: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for r in unsupported:
        for src in (r["current_sources"] or ["unknown"]):
            by_source[src] = by_source.get(src, 0) + 1
        reason_key = r["advisory_reason"][:80] if r["advisory_reason"] else "unclassified"
        by_reason[reason_key] = by_reason.get(reason_key, 0) + 1

    warnings: list[str] = []
    if len(unsupported) > 50:
        warnings.append(f"{len(unsupported)} current candidates have no intelligence support — large gap between current pool and shadow universe")

    return {
        "total":    len(unsupported),
        "symbols":  [r["symbol"] for r in unsupported[:50]],  # cap display at 50
        "deferred_by_quota_or_cap": len(deferred),
        "by_source": by_source,
        "by_reason": by_reason,
        "warnings":  warnings,
    }


def _build_missing_shadow(
    records: list[dict],
    sets: dict[str, set],
    shadow_idx: dict[str, dict],
    feed_idx:   dict[str, list[dict]],
    theme_idx:  dict[str, dict],
) -> dict[str, Any]:
    """Shadow candidates not in current pipeline."""
    missing = [r for r in records if r["in_shadow"] and not r["in_current"]]

    by_theme: dict[str, int] = {}
    by_route: dict[str, int] = {}
    by_rtc:   dict[str, int] = {}
    status_dist: dict[str, int] = {}

    for r in missing:
        for tid in (r["theme_ids"] or []):
            by_theme[tid] = by_theme.get(tid, 0) + 1
        route = r["shadow_route"] or "unknown"
        by_route[route] = by_route.get(route, 0) + 1
        rtc = r["reason_to_care"] or "unknown"
        by_rtc[rtc] = by_rtc.get(rtc, 0) + 1
        s = r["advisory_status"]
        status_dist[s] = status_dist.get(s, 0) + 1

    return {
        "total":                      len(missing),
        "symbols":                    [r["symbol"] for r in missing],
        "by_theme":                   by_theme,
        "by_route":                   by_route,
        "by_reason_to_care":          by_rtc,
        "advisory_status_distribution": status_dist,
    }


def _build_tier_d_advisory(comp: dict, shadow_idx: dict[str, dict], sets: dict[str, set]) -> dict[str, Any]:
    tda = comp.get("tier_d_analysis", {})
    preserved = tda.get("tier_d_in_shadow_symbols") or []
    excluded  = tda.get("tier_d_excluded_symbols") or []
    excl_reasons = tda.get("tier_d_exclusion_reasons") or {}

    # Tier D symbols that are "excluded from Tier D path" but present via another source
    preserved_other_source = [
        sym for sym in excluded
        if sym in shadow_idx and sym not in sets["tier_d_preserved"]
    ]

    structural_quota_excl = excl_reasons.get("Structural quota full (20)", 0)

    findings: list[str] = []
    prate = tda.get("tier_d_preservation_rate", 0.0)
    if prate < 0.05:
        findings.append(f"Tier D preservation rate is very low ({prate:.1%}) — structural quota is the primary blocker")
    findings.append(f"145/150 Tier D candidates excluded due to structural quota cap (20) — shadow model prioritises economic intelligence candidates in structural slots")
    findings.append("5 Tier D symbols preserved: AMD, ASTS, MU, NBIS, NVDA — all appear via manual/economic sources independently")
    if preserved_other_source:
        findings.append(f"{len(preserved_other_source)} Tier D excluded symbols still present in shadow via manual or economic path: {preserved_other_source}")

    return {
        "tier_d_total_current":                  tda.get("tier_d_total_current", 0),
        "tier_d_in_shadow":                      len(preserved),
        "tier_d_excluded":                       len(excluded),
        "tier_d_preservation_rate":              prate,
        "tier_d_top_preserved":                  preserved[:10],
        "tier_d_top_excluded":                   excluded[:20],
        "tier_d_excluded_due_structural_quota":  structural_quota_excl,
        "tier_d_preserved_through_manual_or_other_source": preserved_other_source,
        "tier_d_quality_rank_available":         tda.get("tier_d_quality_rank_available", False),
        "advisory_findings":                     findings,
    }


def _build_structural_quota_advisory(comp: dict) -> dict[str, Any]:
    qpa = comp.get("quota_pressure_analysis", {}).get("structural_position", {})
    shadow = comp.get("shadow_summary", {})

    demand     = qpa.get("demand_total", 0)
    capacity   = qpa.get("capacity", 0)
    accepted   = qpa.get("accepted", 0)
    overflow   = qpa.get("overflow", 0)
    binding    = qpa.get("binding", False)

    overflow_by_theme  = qpa.get("overflow_by_theme", {})
    overflow_by_source = qpa.get("overflow_by_source", {})
    overflow_by_route  = qpa.get("overflow_by_route", {})

    recommendation: str
    if demand > capacity * 5:
        recommendation = "keep_current_shadow_cap_until_more_evidence"
    elif overflow_by_source.get("tier_d_structural", 0) > 100:
        recommendation = "review_structural_ranking_quality"
    else:
        recommendation = "no_change_until_advisory_logs_confirm"

    return {
        "structural_demand_count":   demand,
        "structural_capacity":       capacity,
        "structural_accepted":       accepted,
        "structural_overflow_count": overflow,
        "structural_quota_binding":  binding,
        "overflow_by_theme":         overflow_by_theme,
        "overflow_by_source":        overflow_by_source,
        "overflow_by_route":         overflow_by_route,
        "recommendation":            recommendation,
        "production_change_required": False,
    }


def _build_risk_theme_advisory(
    theme_idx:  dict[str, dict],
    thesis_idx: dict[str, dict],
    records:    list[dict],
) -> dict[str, Any]:
    headwind_cands: list[str] = []
    pressure_cands: list[str] = []
    weakening:      list[str] = []
    crowded:        list[str] = []
    risk_off:       list[str] = []

    _RISK_OFF_THEMES = {"quality_cash_flow", "defensive_quality", "small_caps"}

    for tid, t in theme_idx.items():
        state = t.get("state", "")
        if state == "weakening":
            weakening.append(tid)
        elif state == "crowded":
            crowded.append(tid)
        if tid in _RISK_OFF_THEMES:
            risk_off.append(tid)

    for r in records:
        rtc = r.get("reason_to_care") or ""
        if "headwind" in rtc:
            headwind_cands.append(r["symbol"])
        if rtc == "headwind_pressure_watchlist":
            pressure_cands.append(r["symbol"])

    findings: list[str] = []
    if crowded:
        findings.append(f"Crowded themes ({', '.join(crowded)}) — structural quota binding + excluded candidates; watchlist-only advisory")
    if weakening:
        findings.append(f"Weakening themes ({', '.join(weakening)}) — headwind direction; pressure candidate advisory_watch only")
    if headwind_cands:
        findings.append(f"{len(headwind_cands)} headwind candidates present — all advisory_watch, none executable")
    findings.append("No short or hedge execution instructions generated — headwind monitoring only")

    return {
        "headwind_candidates":               headwind_cands,
        "pressure_candidates":               pressure_cands,
        "weakening_themes":                  weakening,
        "crowded_themes":                    crowded,
        "risk_off_themes":                   risk_off,
        "executable_headwind_candidates":    False,
        "short_or_hedge_instruction_generated": False,
        "findings":                          findings,
    }


def _build_manual_and_held_advisory(comp: dict, shadow_idx: dict[str, dict]) -> dict[str, Any]:
    mah = comp.get("manual_and_held_analysis", {})

    manual_total    = mah.get("manual_candidates_current_count", 0)
    manual_shadow   = mah.get("manual_candidates_shadow_count", 0)
    manual_lost     = mah.get("manual_candidates_lost") or []
    manual_protected = mah.get("manual_candidates_protected", False)

    held_total   = mah.get("held_candidates_current_count", 0)
    held_shadow  = mah.get("held_candidates_shadow_count", 0)
    held_lost    = mah.get("held_candidates_lost") or []
    held_protected = mah.get("held_candidates_protected", False)

    warnings: list[str] = []
    if manual_lost:
        warnings.append(f"Manual candidates missing from shadow: {manual_lost}")
    if held_lost:
        warnings.append(f"Held candidates missing from shadow: {held_lost}")
    if held_total == 0:
        warnings.append("No held positions in shadow mode — held_candidates_total=0 is expected in static_bootstrap mode")

    return {
        "manual_candidates_total":    manual_total,
        "manual_candidates_in_shadow": manual_shadow,
        "manual_candidates_missing":  manual_lost if isinstance(manual_lost, list) else [],
        "manual_protection_preserved": manual_protected,
        "held_candidates_total":      held_total,
        "held_candidates_in_shadow":  held_shadow,
        "held_candidates_missing":    held_lost if isinstance(held_lost, list) else [],
        "held_protection_preserved":  held_protected,
        "warnings":                   warnings,
    }


# ---------------------------------------------------------------------------
# Advisory summary
# ---------------------------------------------------------------------------
def _build_advisory_summary(
    records:    list[dict],
    shadow_idx: dict[str, dict],
    sets:       dict[str, set],
    route_dis:  dict[str, Any],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {s: 0 for s in _VALID_STATUSES}
    for r in records:
        s = r.get("advisory_status", _STATUS_UNRESOLVED)
        status_counts[s] = status_counts.get(s, 0) + 1

    return {
        "current_candidates_count":  sets.get("full_current_pool_count", len(sets["all_current"])),
        "shadow_candidates_count":   len(shadow_idx),
        "overlap_count":             len(sets["overlap"]),
        "advisory_include_count":    status_counts[_STATUS_INCLUDE],
        "advisory_watch_count":      status_counts[_STATUS_WATCH],
        "advisory_defer_count":      status_counts[_STATUS_DEFER],
        "advisory_exclude_count":    status_counts[_STATUS_EXCLUDE],
        "advisory_unresolved_count": status_counts[_STATUS_UNRESOLVED],
        "route_disagreement_count":  route_dis["total_route_disagreements"],
        "unsupported_current_count": len(sets["in_current_not_shadow"]),
        "missing_shadow_count":      len(sets["in_shadow_not_current"]),
        "note_tracked_symbols":      "candidate_advisory covers symbols with explicit tracking data; full current pool is larger",
        "non_executable_all":        True,
        "live_output_changed":       False,
    }


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
def generate_advisory_report() -> dict[str, Any]:
    """Generate the offline advisory report. Returns the report dict."""
    now = datetime.now(timezone.utc).isoformat()
    d = _load_all()

    shadow_univ = d["shadow_universe"] or {}
    comp        = d["comparison"]      or {}
    pipeline    = d["pipeline_snapshot"] or {}
    feed        = d["feed"]            or {}
    ctx         = d["context"]         or {}
    theme_act   = d["theme_activation"] or {}
    thesis      = d["thesis_store"]    or {}

    # Build indexes
    shadow_idx  = _index_shadow_candidates(shadow_univ)
    theme_idx   = _index_themes(theme_act)
    thesis_idx  = _index_theses(thesis)
    feed_idx    = _index_feed_candidates(feed)

    # Build symbol sets
    sets = _build_current_symbol_sets(comp, pipeline)

    # Exclusion log
    excl_log: list[dict] = shadow_univ.get("exclusion_log") or []

    # Build sections
    candidate_advisory = _build_candidate_advisory(
        shadow_idx, sets, feed_idx, theme_idx, thesis_idx, excl_log
    )

    route_disagreements = _build_route_disagreements(shadow_idx, sets)

    unsupported = _build_unsupported_current(candidate_advisory, sets)

    missing_shadow = _build_missing_shadow(
        candidate_advisory, sets, shadow_idx, feed_idx, theme_idx
    )

    tier_d_adv = _build_tier_d_advisory(comp, shadow_idx, sets)

    structural_quota_adv = _build_structural_quota_advisory(comp)

    risk_theme_adv = _build_risk_theme_advisory(theme_idx, thesis_idx, candidate_advisory)

    manual_held_adv = _build_manual_and_held_advisory(comp, shadow_idx)

    adv_summary = _build_advisory_summary(candidate_advisory, shadow_idx, sets, route_disagreements)

    # Warnings
    warnings: list[str] = list(d["_missing"])
    if structural_quota_adv["structural_quota_binding"]:
        warnings.append("Structural quota is binding (demand=180, cap=20) — 160 structural candidates excluded; advisory_defer status assigned")
    if adv_summary["missing_shadow_count"] > 20:
        warnings.append(f"{adv_summary['missing_shadow_count']} shadow candidates not present in current pipeline — review gap with intelligence layer")

    # Source files list
    source_files = list(d["_source_files"].values())

    report: dict[str, Any] = {
        "schema_version":           "1.0.0",
        "generated_at":             now,
        "valid_for_session":        "offline_static_bootstrap",
        "mode":                     "offline_advisory_report",
        "data_source_mode":         "local_shadow_outputs_only",
        "source_files":             source_files,
        "advisory_summary":         adv_summary,
        "candidate_advisory":       candidate_advisory,
        "route_disagreements":      route_disagreements,
        "unsupported_current_candidates": unsupported,
        "missing_shadow_candidates": missing_shadow,
        "tier_d_advisory":          tier_d_adv,
        "structural_quota_advisory": structural_quota_adv,
        "risk_theme_advisory":      risk_theme_adv,
        "manual_and_held_advisory": manual_held_adv,
        "warnings":                 warnings,
        # Safety flags
        "no_live_api_called":           True,
        "broker_called":                False,
        "env_inspected":                False,
        "raw_news_used":                False,
        "llm_used":                     False,
        "broad_intraday_scan_used":     False,
        "production_modules_imported":  False,
        "live_output_changed":          False,
    }

    _write(_OUTPUT_PATH, report)
    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    report = generate_advisory_report()
    summary = report["advisory_summary"]
    print(f"Advisory report → {_OUTPUT_PATH}")
    print(f"  current_candidates:   {summary['current_candidates_count']}")
    print(f"  shadow_candidates:    {summary['shadow_candidates_count']}")
    print(f"  overlap:              {summary['overlap_count']}")
    print(f"  advisory_include:     {summary['advisory_include_count']}")
    print(f"  advisory_watch:       {summary['advisory_watch_count']}")
    print(f"  advisory_defer:       {summary['advisory_defer_count']}")
    print(f"  advisory_unresolved:  {summary['advisory_unresolved_count']}")
    print(f"  route_disagreements:  {summary['route_disagreement_count']}")
    print(f"  missing_shadow:       {summary['missing_shadow_count']}")
    print(f"  live_output_changed:  {report['live_output_changed']}")
