"""
intelligence_schema_validator.py — validate Intelligence-First static files.

Single responsibility: load and validate transmission_rules.json,
theme_taxonomy.json, thematic_roster.json, and economic_candidate_feed.json
against required schemas. Returns structured results; does not raise on
failure so callers can choose how to handle errors.

Public surface:
    validate_transmission_rules(path)  -> ValidationResult
    validate_theme_taxonomy(path)      -> ValidationResult
    validate_thematic_roster(path, taxonomy_path) -> ValidationResult
    validate_economic_candidate_feed(path, roster_path, taxonomy_path) -> ValidationResult
    validate_all(base_dir)             -> dict[str, ValidationResult]

ValidationResult:
    ok      : bool
    errors  : list[str]
    warnings: list[str]
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_VALID_OUTPUT_TYPES = {"theme_tailwind", "theme_headwind", "sector_tailwind", "sector_headwind"}
_VALID_DIRECTIONS = {"positive", "negative", "conditional", "conditional_positive"}
_VALID_ROUTES = {"position", "swing", "intraday_swing", "watchlist", "held", "manual_conviction", "do_not_touch"}
_VALID_ROUTE_BIASES = {"position_or_swing", "position_only", "swing_only", "watchlist_only", "swing_or_intraday", "swing_or_watchlist", "watchlist_or_swing"}
_VALID_LIQUIDITY_CLASSES = {"high", "medium", "low"}

_TRANSMISSION_RULES_REQUIRED_FIELDS = [
    "rule_id", "driver", "output_type", "affected_targets", "direction",
    "confidence", "horizon", "required_confirmations", "blocked_if",
    "reason", "source_type", "source_label", "last_reviewed",
]
_THEME_TAXONOMY_REQUIRED_FIELDS = [
    "theme_id", "name", "beneficiary_types", "typical_horizon",
    "default_routes", "activation_drivers", "confirmation_requirements",
    "risk_flags", "invalidation_examples", "source_label", "last_reviewed",
]
_THEMATIC_ROSTER_REQUIRED_FIELDS = [
    "theme_id", "core_symbols", "etf_proxies", "route_bias",
    "minimum_liquidity_class", "max_candidates", "notes",
    "last_reviewed", "source_label",
]
_FILE_REQUIRED_TOP_KEYS = ["schema_version", "generated_at", "source_label"]

_CANDIDATE_FEED_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "fresh_until", "mode",
    "source_files", "feed_summary", "candidates", "live_output_changed",
]
_CANDIDATE_FEED_SUMMARY_REQUIRED_FLAGS = [
    "llm_symbol_discovery_used", "raw_news_used", "broad_intraday_scan_used",
]
_CANDIDATE_REQUIRED_FIELDS = [
    "symbol", "included_by", "theme", "driver", "role", "reason",
    "reason_to_care", "route_hint", "confidence", "fresh_until",
    "risk_flags", "confirmation_required", "source_labels",
    "transmission_rules_fired", "market_confirmation_required",
]
_EXECUTABLE_ROUTES = {"position", "swing", "intraday_swing"}
_NON_EXECUTABLE_ONLY_ROUTES = {"watchlist", "held", "manual_conviction", "do_not_touch"}

_SHADOW_UNIVERSE_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "valid_for_session", "freshness_status",
    "mode", "source_files", "universe_summary", "quota_summary",
    "candidates", "inclusion_log", "exclusion_log", "live_output_changed",
]
_SHADOW_UNIVERSE_SUMMARY_FLAGS = [
    "llm_symbol_discovery_used", "raw_news_used", "broad_intraday_scan_used",
]
_SHADOW_CANDIDATE_REQUIRED_FIELDS = [
    "symbol", "asset_type", "reason_to_care", "bucket_id", "bucket_type",
    "route", "source_labels", "macro_rules_fired", "transmission_direction",
    "company_validation_status", "thesis_intact", "why_this_symbol",
    "invalidation", "eligibility", "quota", "execution_instructions",
    "risk_notes",
]
_VALID_QUOTA_GROUPS = {
    "structural_position", "catalyst_swing", "attention",
    "etf_proxy", "held", "manual_conviction", "current_source_unclassified",
}
_QUOTA_CAPS = {
    "attention": 15,
    "etf_proxy": 10,
    "structural_position": 20,
    "catalyst_swing": 30,
}
_TOTAL_MAX = 50

_COMPARISON_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "mode", "source_files",
    "current_summary", "shadow_summary", "overlap_summary",
    "tier_d_analysis", "structural_candidate_analysis", "attention_analysis",
    "manual_and_held_analysis", "economic_intelligence_analysis",
    "exclusion_analysis", "quality_warnings", "live_output_changed",
]
_REPORT_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "report_title", "mode",
    "current_summary", "shadow_summary", "overlap",
    "tier_d_analysis", "structural_analysis", "attention_analysis",
    "manual_held_analysis", "economic_analysis", "exclusion_summary",
    "quality_warnings", "live_output_changed",
]


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _load_json(path: str) -> tuple[Any, str | None]:
    """Return (data, error_string). error_string is None on success."""
    if not os.path.exists(path):
        return None, f"File not found: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error in {path}: {e}"


def _check_top_level(data: dict, result: ValidationResult, label: str) -> None:
    for key in _FILE_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"{label}: missing top-level key '{key}'")


def validate_transmission_rules(path: str) -> ValidationResult:
    """Validate data/intelligence/transmission_rules.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result

    if not isinstance(data, dict):
        result.fail(f"{path}: expected a JSON object at top level")
        return result

    _check_top_level(data, result, "transmission_rules")

    rules = data.get("rules")
    if not isinstance(rules, list):
        result.fail("transmission_rules: 'rules' must be a list")
        return result
    if len(rules) == 0:
        result.warn("transmission_rules: 'rules' list is empty")
        return result

    seen_ids: set[str] = set()
    for i, rule in enumerate(rules):
        prefix = f"transmission_rules[{i}]"
        if not isinstance(rule, dict):
            result.fail(f"{prefix}: expected an object")
            continue

        # Required fields
        for field_name in _TRANSMISSION_RULES_REQUIRED_FIELDS:
            if field_name not in rule:
                result.fail(f"{prefix}: missing required field '{field_name}'")

        # Duplicate rule_id
        rule_id = rule.get("rule_id", "")
        if rule_id in seen_ids:
            result.fail(f"{prefix}: duplicate rule_id '{rule_id}'")
        else:
            seen_ids.add(rule_id)

        # output_type
        if rule.get("output_type") not in _VALID_OUTPUT_TYPES:
            result.fail(f"{prefix} '{rule_id}': invalid output_type '{rule.get('output_type')}' — must be one of {sorted(_VALID_OUTPUT_TYPES)}")

        # direction
        if rule.get("direction") not in _VALID_DIRECTIONS:
            result.fail(f"{prefix} '{rule_id}': invalid direction '{rule.get('direction')}' — must be one of {sorted(_VALID_DIRECTIONS)}")

        # confidence
        conf = rule.get("confidence")
        if conf is not None:
            if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
                result.fail(f"{prefix} '{rule_id}': confidence must be a number in [0.0, 1.0], got {conf!r}")

        # affected_targets must be non-empty list
        targets = rule.get("affected_targets")
        if not isinstance(targets, list) or len(targets) == 0:
            result.fail(f"{prefix} '{rule_id}': affected_targets must be a non-empty list")

        # required_confirmations must be non-empty list
        confs = rule.get("required_confirmations")
        if not isinstance(confs, list) or len(confs) == 0:
            result.fail(f"{prefix} '{rule_id}': required_confirmations must be a non-empty list")

        # blocked_if must be a list (can be empty)
        if not isinstance(rule.get("blocked_if"), list):
            result.fail(f"{prefix} '{rule_id}': blocked_if must be a list")

        # source_label must be present and non-empty
        if not rule.get("source_label"):
            result.fail(f"{prefix} '{rule_id}': source_label must be a non-empty string")

        # last_reviewed must be present
        if not rule.get("last_reviewed"):
            result.fail(f"{prefix} '{rule_id}': last_reviewed must be present")

    return result


def validate_theme_taxonomy(path: str) -> ValidationResult:
    """Validate data/intelligence/theme_taxonomy.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result

    if not isinstance(data, dict):
        result.fail(f"{path}: expected a JSON object at top level")
        return result

    _check_top_level(data, result, "theme_taxonomy")

    themes = data.get("themes")
    if not isinstance(themes, list):
        result.fail("theme_taxonomy: 'themes' must be a list")
        return result
    if len(themes) == 0:
        result.warn("theme_taxonomy: 'themes' list is empty")
        return result

    seen_ids: set[str] = set()
    for i, theme in enumerate(themes):
        prefix = f"theme_taxonomy[{i}]"
        if not isinstance(theme, dict):
            result.fail(f"{prefix}: expected an object")
            continue

        # Required fields
        for field_name in _THEME_TAXONOMY_REQUIRED_FIELDS:
            if field_name not in theme:
                result.fail(f"{prefix}: missing required field '{field_name}'")

        # Duplicate theme_id
        theme_id = theme.get("theme_id", "")
        if theme_id in seen_ids:
            result.fail(f"{prefix}: duplicate theme_id '{theme_id}'")
        else:
            seen_ids.add(theme_id)

        # default_routes must be valid route names
        for route in theme.get("default_routes") or []:
            if route not in _VALID_ROUTES:
                result.fail(f"{prefix} '{theme_id}': invalid route '{route}' in default_routes — must be one of {sorted(_VALID_ROUTES)}")

        # beneficiary_types must be non-empty list
        if not isinstance(theme.get("beneficiary_types"), list) or len(theme.get("beneficiary_types", [])) == 0:
            result.fail(f"{prefix} '{theme_id}': beneficiary_types must be a non-empty list")

        # activation_drivers must be non-empty list
        if not isinstance(theme.get("activation_drivers"), list) or len(theme.get("activation_drivers", [])) == 0:
            result.fail(f"{prefix} '{theme_id}': activation_drivers must be a non-empty list")

        # source_label
        if not theme.get("source_label"):
            result.fail(f"{prefix} '{theme_id}': source_label must be a non-empty string")

        # last_reviewed
        if not theme.get("last_reviewed"):
            result.fail(f"{prefix} '{theme_id}': last_reviewed must be present")

    return result


def validate_thematic_roster(path: str, taxonomy_path: str | None = None) -> ValidationResult:
    """Validate data/intelligence/thematic_roster.json.

    If taxonomy_path is provided, theme_id references are checked against the taxonomy.
    """
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result

    if not isinstance(data, dict):
        result.fail(f"{path}: expected a JSON object at top level")
        return result

    _check_top_level(data, result, "thematic_roster")

    # Load known theme IDs from taxonomy if provided
    known_theme_ids: set[str] | None = None
    if taxonomy_path:
        tax_data, tax_err = _load_json(taxonomy_path)
        if tax_err:
            result.warn(f"Could not load taxonomy for cross-reference check: {tax_err}")
        elif isinstance(tax_data, dict) and isinstance(tax_data.get("themes"), list):
            known_theme_ids = {t["theme_id"] for t in tax_data["themes"] if isinstance(t, dict) and "theme_id" in t}

    rosters = data.get("rosters")
    if not isinstance(rosters, list):
        result.fail("thematic_roster: 'rosters' must be a list")
        return result
    if len(rosters) == 0:
        result.warn("thematic_roster: 'rosters' list is empty")
        return result

    seen_theme_ids: set[str] = set()
    for i, roster in enumerate(rosters):
        prefix = f"thematic_roster[{i}]"
        if not isinstance(roster, dict):
            result.fail(f"{prefix}: expected an object")
            continue

        # Required fields
        for field_name in _THEMATIC_ROSTER_REQUIRED_FIELDS:
            if field_name not in roster:
                result.fail(f"{prefix}: missing required field '{field_name}'")

        theme_id = roster.get("theme_id", "")

        # Duplicate theme_id in roster
        if theme_id in seen_theme_ids:
            result.fail(f"{prefix}: duplicate theme_id '{theme_id}' in roster")
        else:
            seen_theme_ids.add(theme_id)

        # Cross-reference theme_id against taxonomy
        if known_theme_ids is not None and theme_id and theme_id not in known_theme_ids:
            result.fail(f"{prefix}: theme_id '{theme_id}' not found in theme_taxonomy — add it to theme_taxonomy.json first")

        # core_symbols must be a non-empty list unless it is a headwind_roster (ETF-proxy-only)
        core = roster.get("core_symbols")
        is_headwind_roster = roster.get("headwind_roster") is True
        if not isinstance(core, list):
            result.fail(f"{prefix} '{theme_id}': core_symbols must be a list")
        elif len(core) == 0 and not is_headwind_roster:
            result.fail(f"{prefix} '{theme_id}': core_symbols must be a non-empty list")
        else:
            for sym in core:
                if not isinstance(sym, str) or not sym.strip():
                    result.fail(f"{prefix} '{theme_id}': core_symbols contains empty or non-string entry: {sym!r}")

        # etf_proxies must be a list (can be empty), no empty strings
        etf = roster.get("etf_proxies")
        if not isinstance(etf, list):
            result.fail(f"{prefix} '{theme_id}': etf_proxies must be a list")
        else:
            for sym in etf:
                if not isinstance(sym, str) or not sym.strip():
                    result.fail(f"{prefix} '{theme_id}': etf_proxies contains empty or non-string entry: {sym!r}")

        # route_bias
        if roster.get("route_bias") not in _VALID_ROUTE_BIASES:
            result.fail(f"{prefix} '{theme_id}': invalid route_bias '{roster.get('route_bias')}' — must be one of {sorted(_VALID_ROUTE_BIASES)}")

        # minimum_liquidity_class
        if roster.get("minimum_liquidity_class") not in _VALID_LIQUIDITY_CLASSES:
            result.fail(f"{prefix} '{theme_id}': invalid minimum_liquidity_class '{roster.get('minimum_liquidity_class')}' — must be one of {sorted(_VALID_LIQUIDITY_CLASSES)}")

        # max_candidates must be a positive integer
        mc = roster.get("max_candidates")
        if not isinstance(mc, int) or mc <= 0:
            result.fail(f"{prefix} '{theme_id}': max_candidates must be a positive integer, got {mc!r}")

        # source_label
        if not roster.get("source_label"):
            result.fail(f"{prefix} '{theme_id}': source_label must be a non-empty string")

        # last_reviewed
        if not roster.get("last_reviewed"):
            result.fail(f"{prefix} '{theme_id}': last_reviewed must be present")

    return result


def validate_economic_candidate_feed(
    path: str,
    roster_path: str | None = None,
    taxonomy_path: str | None = None,
) -> ValidationResult:
    """Validate data/intelligence/economic_candidate_feed.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result

    if not isinstance(data, dict):
        result.fail(f"{path}: expected a JSON object at top level")
        return result

    # Top-level required keys
    for key in _CANDIDATE_FEED_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"economic_candidate_feed: missing top-level key '{key}'")

    # live_output_changed must be false
    if data.get("live_output_changed") is not False:
        result.fail("economic_candidate_feed: live_output_changed must be false")

    # mode must be shadow_report_only
    if data.get("mode") != "shadow_report_only":
        result.fail(f"economic_candidate_feed: mode must be 'shadow_report_only', got {data.get('mode')!r}")

    # feed_summary safety flags
    summary = data.get("feed_summary") or {}
    for flag in _CANDIDATE_FEED_SUMMARY_REQUIRED_FLAGS:
        if summary.get(flag) is not False:
            result.fail(f"economic_candidate_feed: feed_summary.{flag} must be false")

    # Load approved symbols from roster for cross-reference
    known_roster_symbols: set[str] | None = None
    if roster_path:
        roster_data, roster_err = _load_json(roster_path)
        if roster_err:
            result.warn(f"Could not load roster for symbol cross-reference: {roster_err}")
        elif isinstance(roster_data, dict):
            known_roster_symbols = set()
            for entry in (roster_data.get("rosters") or []):
                if isinstance(entry, dict):
                    known_roster_symbols.update(entry.get("core_symbols") or [])
                    known_roster_symbols.update(entry.get("etf_proxies") or [])

    # Load known theme IDs from taxonomy
    known_theme_ids: set[str] | None = None
    if taxonomy_path:
        tax_data, tax_err = _load_json(taxonomy_path)
        if tax_err:
            result.warn(f"Could not load taxonomy for theme cross-reference: {tax_err}")
        elif isinstance(tax_data, dict):
            known_theme_ids = {
                t["theme_id"]
                for t in (tax_data.get("themes") or [])
                if isinstance(t, dict) and "theme_id" in t
            }

    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        result.fail("economic_candidate_feed: 'candidates' must be a list")
        return result

    for i, candidate in enumerate(candidates):
        prefix = f"candidate[{i}]"
        if not isinstance(candidate, dict):
            result.fail(f"{prefix}: expected an object")
            continue

        # Required fields
        for field_name in _CANDIDATE_REQUIRED_FIELDS:
            if field_name not in candidate:
                result.fail(f"{prefix}: missing required field '{field_name}'")

        symbol = candidate.get("symbol", "")
        if not isinstance(symbol, str) or not symbol.strip():
            result.fail(f"{prefix}: symbol must be a non-empty string")

        # Symbol must be in approved roster
        if known_roster_symbols is not None and symbol and symbol not in known_roster_symbols:
            result.fail(
                f"{prefix} '{symbol}': symbol not found in any approved thematic roster — "
                "only approved roster symbols may appear in the candidate feed"
            )

        # Theme must be in taxonomy
        theme = candidate.get("theme", "")
        if known_theme_ids is not None and theme and theme not in known_theme_ids:
            result.fail(f"{prefix} '{symbol}': theme '{theme}' not found in theme_taxonomy")

        # confidence must be [0.0, 1.0]
        conf = candidate.get("confidence")
        if conf is not None:
            if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
                result.fail(f"{prefix} '{symbol}': confidence must be a number in [0.0, 1.0], got {conf!r}")

        # route_hint must only contain valid route names
        route_hint = candidate.get("route_hint")
        if not isinstance(route_hint, list) or len(route_hint) == 0:
            result.fail(f"{prefix} '{symbol}': route_hint must be a non-empty list")
        else:
            for route in route_hint:
                if route not in _VALID_ROUTES:
                    result.fail(
                        f"{prefix} '{symbol}': invalid route '{route}' in route_hint — "
                        f"must be one of {sorted(_VALID_ROUTES)}"
                    )

        # source_labels must be non-empty
        sl = candidate.get("source_labels")
        if not isinstance(sl, list) or len(sl) == 0:
            result.fail(f"{prefix} '{symbol}': source_labels must be a non-empty list")

        # reason_to_care must be non-empty
        if not candidate.get("reason_to_care"):
            result.fail(f"{prefix} '{symbol}': reason_to_care must be non-empty")

        # fresh_until must be present
        if not candidate.get("fresh_until"):
            result.fail(f"{prefix} '{symbol}': fresh_until must be present")

        # No candidate should be marked executable — live_output_changed must be false
        if candidate.get("live_output_changed") is not False:
            result.fail(f"{prefix} '{symbol}': candidate live_output_changed must be false")

        # mode must be shadow_report_only
        if candidate.get("mode") and candidate.get("mode") != "shadow_report_only":
            result.fail(
                f"{prefix} '{symbol}': candidate mode must be 'shadow_report_only', "
                f"got {candidate.get('mode')!r}"
            )

    return result


def validate_comparison(path: str) -> ValidationResult:
    """Validate data/universe_builder/current_vs_shadow_comparison.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail(f"{path}: expected a JSON object at top level")
        return result

    for key in _COMPARISON_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"comparison: missing top-level key '{key}'")

    if data.get("live_output_changed") is not False:
        result.fail("comparison: live_output_changed must be false")

    if data.get("mode") != "shadow_comparison_only":
        result.fail(f"comparison: mode must be 'shadow_comparison_only', got {data.get('mode')!r}")

    # Economic intelligence flags
    econ = data.get("economic_intelligence_analysis") or {}
    for flag in ["llm_symbol_discovery_used", "raw_news_used", "broad_intraday_scan_used"]:
        if econ.get(flag) is not False:
            result.fail(f"comparison: economic_intelligence_analysis.{flag} must be false")

    if econ.get("economic_candidates_executable") is True:
        result.fail("comparison: economic_candidates_executable must be false")

    # Structural safety checks
    struct = data.get("structural_candidate_analysis") or {}
    if struct.get("structural_candidates_displaced_by_attention") is True:
        result.fail("comparison: structural_candidates_displaced_by_attention must be false")

    # Attention cap
    attn = data.get("attention_analysis") or {}
    if attn.get("attention_cap_respected") is False:
        result.fail("comparison: attention_cap_respected must be true")

    if attn.get("attention_candidates_consumed_structural_quota") is True:
        result.fail("comparison: attention_candidates_consumed_structural_quota must be false")

    # Manual/held protection when candidates present
    mh = data.get("manual_and_held_analysis") or {}
    if mh.get("manual_candidates_shadow_count", 0) > 0 and mh.get("manual_candidates_protected") is False:
        result.fail("comparison: manual_candidates_protected must be true when manual candidates are present")

    # Unavailable stages must be explicitly marked
    cs = data.get("current_summary") or {}
    unavailable = cs.get("unavailable_stages")
    if not isinstance(unavailable, list):
        result.fail("comparison: current_summary.unavailable_stages must be a list")

    # Count consistency: overlap ≤ min(current_pool, shadow_total)
    ov = data.get("overlap_summary") or {}
    ss = data.get("shadow_summary") or {}
    shadow_total = ss.get("shadow_total_count", 0)
    overlap_count = ov.get("overlap_count", 0)
    if isinstance(shadow_total, int) and isinstance(overlap_count, int):
        if overlap_count > shadow_total:
            result.fail(
                f"comparison: overlap_count ({overlap_count}) cannot exceed "
                f"shadow_total_count ({shadow_total})"
            )

    # Day 6 optional sections — warn if absent
    for optional_key in ("quota_pressure_analysis", "source_collision_analysis", "economic_slice_analysis"):
        if optional_key not in data:
            result.warn(f"comparison: '{optional_key}' not present — expected from Day 6+")

    # economic_slice_analysis safety checks when present
    esa = data.get("economic_slice_analysis")
    if isinstance(esa, dict):
        if esa.get("llm_symbol_discovery_used") is True:
            result.fail("comparison: economic_slice_analysis.llm_symbol_discovery_used must be false")
        if esa.get("macro_transmission_deterministic") is False:
            result.fail("comparison: economic_slice_analysis.macro_transmission_deterministic must be true")

    # Day 7 adapter_impact_analysis — warn if absent
    aia = data.get("adapter_impact_analysis")
    if aia is None:
        result.warn("comparison: 'adapter_impact_analysis' not present — expected from Day 7+")
    elif isinstance(aia, dict):
        if aia.get("side_effects_triggered") is not False:
            result.fail("comparison: adapter_impact_analysis.side_effects_triggered must be false")
        if aia.get("live_data_called") is not False:
            result.fail("comparison: adapter_impact_analysis.live_data_called must be false")

    return result


def validate_report(path: str) -> ValidationResult:
    """Validate data/universe_builder/universe_builder_report.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail(f"{path}: expected a JSON object at top level")
        return result

    for key in _REPORT_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"report: missing top-level key '{key}'")

    if data.get("live_output_changed") is not False:
        result.fail("report: live_output_changed must be false")

    if data.get("mode") != "shadow_comparison_only":
        result.fail(f"report: mode must be 'shadow_comparison_only', got {data.get('mode')!r}")

    econ = data.get("economic_analysis") or {}
    for flag in ["llm_symbol_discovery_used", "raw_news_used", "broad_intraday_scan_used"]:
        if econ.get(flag) is not False:
            result.fail(f"report: economic_analysis.{flag} must be false")

    return result


def validate_shadow_universe(path: str) -> ValidationResult:
    """Validate data/universe_builder/active_opportunity_universe_shadow.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result

    if not isinstance(data, dict):
        result.fail(f"{path}: expected a JSON object at top level")
        return result

    # Top-level required keys
    for key in _SHADOW_UNIVERSE_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"shadow_universe: missing top-level key '{key}'")

    # mode must be shadow_only
    if data.get("mode") != "shadow_only":
        result.fail(f"shadow_universe: mode must be 'shadow_only', got {data.get('mode')!r}")

    # live_output_changed must be false
    if data.get("live_output_changed") is not False:
        result.fail("shadow_universe: live_output_changed must be false")

    # Safety flags in universe_summary
    summary = data.get("universe_summary") or {}
    for flag in _SHADOW_UNIVERSE_SUMMARY_FLAGS:
        if summary.get(flag) is not False:
            result.fail(f"shadow_universe: universe_summary.{flag} must be false")

    # inclusion_log and exclusion_log must be lists
    if not isinstance(data.get("inclusion_log"), list):
        result.fail("shadow_universe: inclusion_log must be a list")
    if not isinstance(data.get("exclusion_log"), list):
        result.fail("shadow_universe: exclusion_log must be a list")

    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        result.fail("shadow_universe: 'candidates' must be a list")
        return result

    # Total cap check
    if len(candidates) > _TOTAL_MAX:
        result.fail(f"shadow_universe: total candidates {len(candidates)} exceeds cap {_TOTAL_MAX}")

    # Per-quota-group count tracking
    group_counts: dict[str, int] = {}

    for i, candidate in enumerate(candidates):
        prefix = f"shadow_candidate[{i}]"
        if not isinstance(candidate, dict):
            result.fail(f"{prefix}: expected an object")
            continue

        symbol = candidate.get("symbol", "")

        # Required fields
        for field_name in _SHADOW_CANDIDATE_REQUIRED_FIELDS:
            if field_name not in candidate:
                result.fail(f"{prefix} '{symbol}': missing required field '{field_name}'")

        # symbol non-empty
        if not isinstance(symbol, str) or not symbol.strip():
            result.fail(f"{prefix}: symbol must be a non-empty string")

        # reason_to_care non-empty
        if not candidate.get("reason_to_care"):
            result.fail(f"{prefix} '{symbol}': reason_to_care must be non-empty")

        # route valid
        route = candidate.get("route", "")
        if route not in _VALID_ROUTES:
            result.fail(
                f"{prefix} '{symbol}': invalid route '{route}' — "
                f"must be one of {sorted(_VALID_ROUTES)}"
            )

        # quota must be a dict with a group field
        quota = candidate.get("quota")
        if not isinstance(quota, dict):
            result.fail(f"{prefix} '{symbol}': quota must be an object")
        else:
            group = quota.get("group", "")
            if group not in _VALID_QUOTA_GROUPS:
                result.fail(
                    f"{prefix} '{symbol}': invalid quota.group '{group}' — "
                    f"must be one of {sorted(_VALID_QUOTA_GROUPS)}"
                )
            group_counts[group] = group_counts.get(group, 0) + 1

        # source_labels non-empty
        sl = candidate.get("source_labels")
        if not isinstance(sl, list) or len(sl) == 0:
            result.fail(f"{prefix} '{symbol}': source_labels must be a non-empty list")

        # eligibility must be present dict
        if not isinstance(candidate.get("eligibility"), dict):
            result.fail(f"{prefix} '{symbol}': eligibility must be an object")

        # execution_instructions must be present dict
        ei = candidate.get("execution_instructions")
        if not isinstance(ei, dict):
            result.fail(f"{prefix} '{symbol}': execution_instructions must be an object")
        else:
            # No candidate should be executable
            if ei.get("executable") is True:
                result.fail(
                    f"{prefix} '{symbol}': execution_instructions.executable must be false "
                    "— no shadow candidate is executable"
                )

        # candidate-level live_output_changed
        if "live_output_changed" in candidate and candidate["live_output_changed"] is not False:
            result.fail(f"{prefix} '{symbol}': candidate live_output_changed must be false")

    # Quota cap checks
    for group, cap in _QUOTA_CAPS.items():
        count = group_counts.get(group, 0)
        if count > cap:
            result.fail(
                f"shadow_universe: quota group '{group}' has {count} candidates — "
                f"exceeds cap {cap}"
            )

    # Structural candidates must not be fewer than attention candidates
    # (this is a warning, not a hard failure — min-8 is the hard gate)
    structural = group_counts.get("structural_position", 0)
    attention = group_counts.get("attention", 0)
    if structural < _QUOTA_CAPS.get("attention", 15) and structural == 0 and attention > 0:
        result.warn(
            "shadow_universe: no structural_position candidates while attention candidates are present — "
            "structural candidates may be displaced"
        )

    # Day 7 adapter_usage_summary — warn if absent, validate safety flags if present
    aus = data.get("adapter_usage_summary")
    if aus is None:
        result.warn("shadow_universe: 'adapter_usage_summary' not present — expected from Day 7+")
    elif isinstance(aus, dict):
        if aus.get("side_effects_triggered") is not False:
            result.fail("shadow_universe: adapter_usage_summary.side_effects_triggered must be false")
        if aus.get("live_data_called") is not False:
            result.fail("shadow_universe: adapter_usage_summary.live_data_called must be false")
    else:
        result.fail("shadow_universe: adapter_usage_summary must be an object")

    # Day 6 optional sections — warn if absent, validate structure if present
    qpd = data.get("quota_pressure_diagnostics")
    if qpd is None:
        result.warn("shadow_universe: quota_pressure_diagnostics not present — expected from Day 6+")
    elif isinstance(qpd, dict):
        sp = qpd.get("structural_position")
        if isinstance(sp, dict):
            for field_name in ("demand_total", "capacity", "accepted", "overflow", "binding"):
                if field_name not in sp:
                    result.warn(f"shadow_universe: quota_pressure_diagnostics.structural_position missing '{field_name}'")
            cap = sp.get("capacity")
            accepted = sp.get("accepted", 0)
            if isinstance(cap, int) and isinstance(accepted, int) and accepted > cap:
                result.fail(
                    f"shadow_universe: quota_pressure_diagnostics.structural_position accepted ({accepted}) "
                    f"exceeds capacity ({cap})"
                )
    else:
        result.fail("shadow_universe: quota_pressure_diagnostics must be an object")

    scr = data.get("source_collision_report")
    if scr is None:
        result.warn("shadow_universe: source_collision_report not present — expected from Day 6+")
    elif not isinstance(scr, list):
        result.fail("shadow_universe: source_collision_report must be a list")
    else:
        for i, entry in enumerate(scr):
            if not isinstance(entry, dict):
                result.fail(f"shadow_universe: source_collision_report[{i}] must be an object")
                continue
            for field_name in ("symbol", "final_in_shadow", "protected_by_manual_or_held",
                               "source_path_excluded_but_symbol_preserved"):
                if field_name not in entry:
                    result.warn(
                        f"shadow_universe: source_collision_report[{i}] missing '{field_name}'"
                    )

    return result


_ADAPTER_SNAPSHOT_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "mode", "adapters",
    "adapter_summary", "live_output_changed",
]
_VALID_ADAPTER_STATUSES = {"available", "unavailable", "skipped_due_side_effect_risk"}


def validate_adapter_snapshot(path: str) -> ValidationResult:
    """Validate data/intelligence/source_adapter_snapshot.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("adapter_snapshot: expected a JSON object at top level")
        return result

    for key in _ADAPTER_SNAPSHOT_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"adapter_snapshot: missing top-level key '{key}'")

    if data.get("live_output_changed") is not False:
        result.fail("adapter_snapshot: live_output_changed must be false")

    if data.get("mode") != "read_only_adapter_snapshot":
        result.fail(
            f"adapter_snapshot: mode must be 'read_only_adapter_snapshot', got {data.get('mode')!r}"
        )

    adapters = data.get("adapters")
    if not isinstance(adapters, dict):
        result.fail("adapter_snapshot: 'adapters' must be an object")
        return result

    for name, adapter in adapters.items():
        if not isinstance(adapter, dict):
            result.fail(f"adapter_snapshot: adapter '{name}' must be an object")
            continue

        # Safety contract: no adapter may trigger side effects or call live data
        if adapter.get("side_effects_triggered") is not False:
            result.fail(f"adapter_snapshot '{name}': side_effects_triggered must be false")
        if adapter.get("live_data_called") is not False:
            result.fail(f"adapter_snapshot '{name}': live_data_called must be false")

        status = adapter.get("source_status")
        if status not in _VALID_ADAPTER_STATUSES:
            result.fail(
                f"adapter_snapshot '{name}': invalid source_status '{status}' — "
                f"must be one of {sorted(_VALID_ADAPTER_STATUSES)}"
            )

        if status == "skipped_due_side_effect_risk" and not adapter.get("skipped_reason"):
            result.warn(
                f"adapter_snapshot '{name}': source_status is skipped_due_side_effect_risk "
                "but skipped_reason is empty"
            )

        if not isinstance(adapter.get("symbols_read"), list):
            result.fail(f"adapter_snapshot '{name}': symbols_read must be a list")

        if not isinstance(adapter.get("fields_available"), list):
            result.fail(f"adapter_snapshot '{name}': fields_available must be a list")

        if not isinstance(adapter.get("warnings"), list):
            result.fail(f"adapter_snapshot '{name}': warnings must be a list")

    # adapter_summary consistency
    summary = data.get("adapter_summary") or {}
    summary_total = summary.get("adapters_total")
    if isinstance(summary_total, int) and summary_total != len(adapters):
        result.warn(
            f"adapter_snapshot: adapter_summary.adapters_total ({summary_total}) "
            f"does not match actual adapter count ({len(adapters)})"
        )

    return result


_THEME_ACTIVATION_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "valid_for_session", "mode",
    "data_source_mode", "source_files", "activation_summary", "themes",
    "no_live_api_called", "broker_called", "env_inspected",
    "raw_news_used", "llm_used", "broad_intraday_scan_used", "live_output_changed",
]
_VALID_THEME_ACTIVATION_STATES = {
    "activated", "strengthening", "watchlist",
    "weakening", "crowded", "invalidated", "dormant",
}
_THEME_RECORD_REQUIRED_FIELDS = [
    "theme_id", "state", "direction", "confidence", "evidence",
    "confirmation_requirements", "risk_flags", "invalidation_rules",
    "freshness_status", "route_bias", "candidate_count",
    "candidates_in_shadow_count", "candidates_excluded_count",
    "source_label",
]
_THESIS_STORE_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "valid_for_session", "mode",
    "data_source_mode", "source_files", "thesis_summary", "theses",
    "no_live_api_called", "broker_called", "env_inspected",
    "raw_news_used", "llm_used", "broad_intraday_scan_used", "live_output_changed",
]
_VALID_THESIS_STATUSES = {
    "new", "active", "strengthened", "weakened",
    "crowded", "invalidated", "unchanged", "watchlist",
}
_THESIS_RECORD_REQUIRED_FIELDS = [
    "theme_id", "current_thesis", "status", "evidence",
    "confidence", "invalidation", "confirmation_required",
    "affected_symbols", "freshness_status", "source_label",
]


def validate_theme_activation(path: str) -> ValidationResult:
    """Validate data/intelligence/theme_activation.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("theme_activation: expected a JSON object at top level")
        return result

    # Required top-level keys
    for key in _THEME_ACTIVATION_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"theme_activation: missing top-level key '{key}'")

    # Safety flags
    if data.get("no_live_api_called") is not True:
        result.fail("theme_activation: no_live_api_called must be true")
    if data.get("broker_called") is not False:
        result.fail("theme_activation: broker_called must be false")
    if data.get("env_inspected") is not False:
        result.fail("theme_activation: env_inspected must be false")
    if data.get("raw_news_used") is not False:
        result.fail("theme_activation: raw_news_used must be false")
    if data.get("llm_used") is not False:
        result.fail("theme_activation: llm_used must be false")
    if data.get("broad_intraday_scan_used") is not False:
        result.fail("theme_activation: broad_intraday_scan_used must be false")
    if data.get("live_output_changed") is not False:
        result.fail("theme_activation: live_output_changed must be false")

    # mode check
    if data.get("mode") != "shadow_theme_activation":
        result.fail(
            f"theme_activation: mode must be 'shadow_theme_activation', "
            f"got {data.get('mode')!r}"
        )

    # themes list
    themes = data.get("themes")
    if not isinstance(themes, list):
        result.fail("theme_activation: 'themes' must be a list")
        return result

    # activation_summary
    summary = data.get("activation_summary")
    if not isinstance(summary, dict):
        result.fail("theme_activation: activation_summary must be an object")
    else:
        if summary.get("no_live_api_called") is not True:
            result.fail("theme_activation: activation_summary.no_live_api_called must be true")
        if summary.get("live_output_changed") is not False:
            result.fail("theme_activation: activation_summary.live_output_changed must be false")

    # Per-theme record validation
    for i, theme in enumerate(themes):
        prefix = f"theme_activation.themes[{i}]"
        if not isinstance(theme, dict):
            result.fail(f"{prefix}: expected an object")
            continue

        theme_id = theme.get("theme_id", f"index_{i}")

        for field_name in _THEME_RECORD_REQUIRED_FIELDS:
            if field_name not in theme:
                result.fail(f"{prefix} '{theme_id}': missing required field '{field_name}'")

        # state must be valid
        state = theme.get("state")
        if state not in _VALID_THEME_ACTIVATION_STATES:
            result.fail(
                f"{prefix} '{theme_id}': invalid state '{state}' — "
                f"must be one of {sorted(_VALID_THEME_ACTIVATION_STATES)}"
            )

        # confidence must be [0.0, 1.0]
        conf = theme.get("confidence")
        if conf is not None and (not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0)):
            result.fail(f"{prefix} '{theme_id}': confidence must be in [0.0, 1.0], got {conf!r}")

        # evidence must be a list
        if not isinstance(theme.get("evidence"), list):
            result.fail(f"{prefix} '{theme_id}': evidence must be a list")

        # confirmation_requirements must be a list
        if not isinstance(theme.get("confirmation_requirements"), list):
            result.fail(f"{prefix} '{theme_id}': confirmation_requirements must be a list")

        # risk_flags must be a list
        if not isinstance(theme.get("risk_flags"), list):
            result.fail(f"{prefix} '{theme_id}': risk_flags must be a list")

        # invalidation_rules must be a list
        if not isinstance(theme.get("invalidation_rules"), list):
            result.fail(f"{prefix} '{theme_id}': invalidation_rules must be a list")

        # used_live_data must be false
        if theme.get("used_live_data") is not False:
            result.fail(f"{prefix} '{theme_id}': used_live_data must be false")

    return result


def validate_thesis_store(path: str) -> ValidationResult:
    """Validate data/intelligence/thesis_store.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("thesis_store: expected a JSON object at top level")
        return result

    # Required top-level keys
    for key in _THESIS_STORE_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"thesis_store: missing top-level key '{key}'")

    # Safety flags
    if data.get("no_live_api_called") is not True:
        result.fail("thesis_store: no_live_api_called must be true")
    if data.get("broker_called") is not False:
        result.fail("thesis_store: broker_called must be false")
    if data.get("env_inspected") is not False:
        result.fail("thesis_store: env_inspected must be false")
    if data.get("raw_news_used") is not False:
        result.fail("thesis_store: raw_news_used must be false")
    if data.get("llm_used") is not False:
        result.fail("thesis_store: llm_used must be false")
    if data.get("broad_intraday_scan_used") is not False:
        result.fail("thesis_store: broad_intraday_scan_used must be false")
    if data.get("live_output_changed") is not False:
        result.fail("thesis_store: live_output_changed must be false")

    # mode check
    if data.get("mode") != "shadow_thesis_store":
        result.fail(
            f"thesis_store: mode must be 'shadow_thesis_store', got {data.get('mode')!r}"
        )

    # theses list
    theses = data.get("theses")
    if not isinstance(theses, list):
        result.fail("thesis_store: 'theses' must be a list")
        return result

    # thesis_summary
    summary = data.get("thesis_summary")
    if not isinstance(summary, dict):
        result.fail("thesis_store: thesis_summary must be an object")
    else:
        if summary.get("no_live_api_called") is not True:
            result.fail("thesis_store: thesis_summary.no_live_api_called must be true")
        if summary.get("live_output_changed") is not False:
            result.fail("thesis_store: thesis_summary.live_output_changed must be false")

    # Per-thesis record validation
    for i, thesis in enumerate(theses):
        prefix = f"thesis_store.theses[{i}]"
        if not isinstance(thesis, dict):
            result.fail(f"{prefix}: expected an object")
            continue

        theme_id = thesis.get("theme_id", f"index_{i}")

        for field_name in _THESIS_RECORD_REQUIRED_FIELDS:
            if field_name not in thesis:
                result.fail(f"{prefix} '{theme_id}': missing required field '{field_name}'")

        # status must be valid
        status = thesis.get("status")
        if status not in _VALID_THESIS_STATUSES:
            result.fail(
                f"{prefix} '{theme_id}': invalid status '{status}' — "
                f"must be one of {sorted(_VALID_THESIS_STATUSES)}"
            )

        # evidence must be a list
        if not isinstance(thesis.get("evidence"), list):
            result.fail(f"{prefix} '{theme_id}': evidence must be a list")

        # affected_symbols must be a list
        if not isinstance(thesis.get("affected_symbols"), list):
            result.fail(f"{prefix} '{theme_id}': affected_symbols must be a list")

        # confidence must be [0.0, 1.0]
        conf = thesis.get("confidence")
        if conf is not None and (not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0)):
            result.fail(f"{prefix} '{theme_id}': confidence must be in [0.0, 1.0], got {conf!r}")

        # current_thesis must be non-empty string
        ct = thesis.get("current_thesis")
        if not isinstance(ct, str) or not ct.strip():
            result.fail(f"{prefix} '{theme_id}': current_thesis must be a non-empty string")

        # used_live_data must be false
        if thesis.get("used_live_data") is not False:
            result.fail(f"{prefix} '{theme_id}': used_live_data must be false")

    return result


_DAILY_ECONOMIC_STATE_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "valid_for_session", "mode",
    "data_source_mode", "source_files", "driver_scores", "driver_states",
    "active_drivers", "inactive_drivers", "blocked_drivers",
    "confidence_summary", "no_live_api_called", "broker_called",
    "env_inspected", "raw_news_used", "llm_used",
    "broad_intraday_scan_used", "live_output_changed",
]
_DRIVER_REQUIRED_FIELDS = [
    "driver_id", "state", "confidence", "source_label", "freshness_status",
    "used_live_data", "used_raw_news", "used_llm",
]
_VALID_DRIVER_STATES = {
    "active_shadow_inferred", "watch_shadow_inferred",
    "inactive_shadow", "unavailable",
}
_CURRENT_ECONOMIC_CONTEXT_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "valid_for_session", "mode",
    "data_source_mode", "economic_regime", "risk_posture", "confidence",
    "active_driver_summary", "active_theme_summary", "route_adjustments",
    "risk_modifiers", "freshness", "source_files",
    "no_live_api_called", "broker_called", "env_inspected",
    "raw_news_used", "llm_used", "broad_intraday_scan_used", "live_output_changed",
]
_VALID_ECONOMIC_REGIMES = {
    "unknown_static_bootstrap", "mixed_shadow_regime",
    "ai_infrastructure_tailwind_shadow", "credit_stress_watch_shadow",
    "risk_off_watch_shadow", "selective_shadow", "unavailable",
}
_VALID_RISK_POSTURES = {
    "unknown", "neutral", "selective", "cautious", "defensive_selective",
}
_REQUIRED_ROUTE_ADJUSTMENT_GROUPS = {"POSITION", "SWING", "INTRADAY_SWING", "WATCHLIST"}


def validate_daily_economic_state(path: str) -> ValidationResult:
    """Validate data/intelligence/daily_economic_state.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("daily_economic_state: expected a JSON object at top level")
        return result

    # Required top-level keys
    for key in _DAILY_ECONOMIC_STATE_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"daily_economic_state: missing top-level key '{key}'")

    # Safety flags — must be exactly these values
    if data.get("no_live_api_called") is not True:
        result.fail("daily_economic_state: no_live_api_called must be true")
    if data.get("broker_called") is not False:
        result.fail("daily_economic_state: broker_called must be false")
    if data.get("env_inspected") is not False:
        result.fail("daily_economic_state: env_inspected must be false")
    if data.get("raw_news_used") is not False:
        result.fail("daily_economic_state: raw_news_used must be false")
    if data.get("llm_used") is not False:
        result.fail("daily_economic_state: llm_used must be false")
    if data.get("broad_intraday_scan_used") is not False:
        result.fail("daily_economic_state: broad_intraday_scan_used must be false")
    if data.get("live_output_changed") is not False:
        result.fail("daily_economic_state: live_output_changed must be false")

    # mode check
    if data.get("mode") != "shadow_local_economic_state":
        result.fail(
            f"daily_economic_state: mode must be 'shadow_local_economic_state', "
            f"got {data.get('mode')!r}"
        )

    # driver_scores validation
    driver_scores = data.get("driver_scores")
    if not isinstance(driver_scores, dict):
        result.fail("daily_economic_state: driver_scores must be an object")
        return result

    if len(driver_scores) == 0:
        result.fail("daily_economic_state: driver_scores must not be empty")

    for driver_id, driver in driver_scores.items():
        prefix = f"daily_economic_state.driver_scores['{driver_id}']"
        if not isinstance(driver, dict):
            result.fail(f"{prefix}: must be an object")
            continue

        for field_name in _DRIVER_REQUIRED_FIELDS:
            if field_name not in driver:
                result.fail(f"{prefix}: missing required field '{field_name}'")

        # state must be valid
        state = driver.get("state")
        if state not in _VALID_DRIVER_STATES:
            result.fail(
                f"{prefix}: invalid state '{state}' — "
                f"must be one of {sorted(_VALID_DRIVER_STATES)}"
            )

        # unavailable drivers must have unavailable_reason
        if state == "unavailable" and not driver.get("unavailable_reason"):
            result.fail(
                f"{prefix}: state is 'unavailable' but unavailable_reason is missing or empty"
            )

        # used_live_data must be false
        if driver.get("used_live_data") is not False:
            result.fail(f"{prefix}: used_live_data must be false")

        # confidence must be [0.0, 1.0]
        conf = driver.get("confidence")
        if conf is not None and (not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0)):
            result.fail(f"{prefix}: confidence must be in [0.0, 1.0], got {conf!r}")

    # active_drivers and inactive_drivers must be lists
    if not isinstance(data.get("active_drivers"), list):
        result.fail("daily_economic_state: active_drivers must be a list")
    if not isinstance(data.get("inactive_drivers"), list):
        result.fail("daily_economic_state: inactive_drivers must be a list")
    if not isinstance(data.get("blocked_drivers"), list):
        result.fail("daily_economic_state: blocked_drivers must be a list")

    # confidence_summary must be a dict
    cs = data.get("confidence_summary")
    if not isinstance(cs, dict):
        result.fail("daily_economic_state: confidence_summary must be an object")
    else:
        if cs.get("drivers_with_live_evidence", -1) != 0:
            result.fail("daily_economic_state: confidence_summary.drivers_with_live_evidence must be 0")

    return result


def validate_current_economic_context(path: str) -> ValidationResult:
    """Validate data/intelligence/current_economic_context.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("current_economic_context: expected a JSON object at top level")
        return result

    # Required top-level keys
    for key in _CURRENT_ECONOMIC_CONTEXT_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"current_economic_context: missing top-level key '{key}'")

    # Safety flags
    if data.get("no_live_api_called") is not True:
        result.fail("current_economic_context: no_live_api_called must be true")
    if data.get("broker_called") is not False:
        result.fail("current_economic_context: broker_called must be false")
    if data.get("env_inspected") is not False:
        result.fail("current_economic_context: env_inspected must be false")
    if data.get("raw_news_used") is not False:
        result.fail("current_economic_context: raw_news_used must be false")
    if data.get("llm_used") is not False:
        result.fail("current_economic_context: llm_used must be false")
    if data.get("broad_intraday_scan_used") is not False:
        result.fail("current_economic_context: broad_intraday_scan_used must be false")
    if data.get("live_output_changed") is not False:
        result.fail("current_economic_context: live_output_changed must be false")

    # mode check
    if data.get("mode") != "shadow_current_economic_context":
        result.fail(
            f"current_economic_context: mode must be 'shadow_current_economic_context', "
            f"got {data.get('mode')!r}"
        )

    # economic_regime
    regime = data.get("economic_regime")
    if regime not in _VALID_ECONOMIC_REGIMES:
        result.fail(
            f"current_economic_context: invalid economic_regime '{regime}' — "
            f"must be one of {sorted(_VALID_ECONOMIC_REGIMES)}"
        )

    # risk_posture
    posture = data.get("risk_posture")
    if posture not in _VALID_RISK_POSTURES:
        result.fail(
            f"current_economic_context: invalid risk_posture '{posture}' — "
            f"must be one of {sorted(_VALID_RISK_POSTURES)}"
        )

    # confidence in [0, 1]
    conf = data.get("confidence")
    if conf is not None and (not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0)):
        result.fail(f"current_economic_context: confidence must be in [0.0, 1.0], got {conf!r}")

    # route_adjustments — all required groups must be present
    ra = data.get("route_adjustments")
    if not isinstance(ra, dict):
        result.fail("current_economic_context: route_adjustments must be an object")
    else:
        for group in _REQUIRED_ROUTE_ADJUSTMENT_GROUPS:
            if group not in ra:
                result.fail(f"current_economic_context: route_adjustments missing required group '{group}'")
        # route_adjustments must not mark anything executable
        for group_name, group_data in ra.items():
            if isinstance(group_data, dict) and group_data.get("executable") is True:
                result.fail(
                    f"current_economic_context: route_adjustments.{group_name}.executable "
                    "must not be true — context cannot make candidates executable"
                )

    # active_driver_summary and active_theme_summary must be lists
    if not isinstance(data.get("active_driver_summary"), list):
        result.fail("current_economic_context: active_driver_summary must be a list")
    if not isinstance(data.get("active_theme_summary"), list):
        result.fail("current_economic_context: active_theme_summary must be a list")

    return result


# ---------------------------------------------------------------------------
# Sprint 5A — Backtest output validators
# ---------------------------------------------------------------------------

_BACKTEST_REQUIRED_SAFETY_FLAGS = [
    "no_live_api_called", "broker_called", "env_inspected",
    "raw_news_used", "llm_used", "broad_intraday_scan_used", "live_output_changed",
]

_VALID_DECISION_GATES = {
    "pass_for_next_shadow_sprint",
    "fail_needs_fix",
    "pass_but_not_for_advisory",
    "insufficient_evidence",
    "pass_but_more_replay_needed",  # Sprint 5B: historical replay limitations documented
}


def _validate_backtest_safety_flags(data: dict, label: str, result: "ValidationResult") -> None:
    """Check all backtest safety flags are present with correct values."""
    if data.get("no_live_api_called") is not True:
        result.fail(f"{label}: no_live_api_called must be true")
    if data.get("broker_called") is not False:
        result.fail(f"{label}: broker_called must be false")
    if data.get("env_inspected") is not False:
        result.fail(f"{label}: env_inspected must be false")
    if data.get("raw_news_used") is not False:
        result.fail(f"{label}: raw_news_used must be false")
    if data.get("llm_used") is not False:
        result.fail(f"{label}: llm_used must be false")
    if data.get("broad_intraday_scan_used") is not False:
        result.fail(f"{label}: broad_intraday_scan_used must be false")
    if data.get("live_output_changed") is not False:
        result.fail(f"{label}: live_output_changed must be false")


def validate_regime_fixture_results(path: str) -> "ValidationResult":
    """Validate data/intelligence/backtest/regime_fixture_results.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("regime_fixture_results: expected a JSON object")
        return result

    for key in ["schema_version", "mode", "scenarios_run", "scenarios_passed",
                "scenarios_failed", "results", "failure_reasons"]:
        if key not in data:
            result.fail(f"regime_fixture_results: missing key '{key}'")

    _validate_backtest_safety_flags(data, "regime_fixture_results", result)

    if data.get("mode") != "local_fixture_backtest":
        result.fail(
            f"regime_fixture_results: mode must be 'local_fixture_backtest', got {data.get('mode')!r}"
        )

    scenarios_run = data.get("scenarios_run", 0)
    if not isinstance(scenarios_run, int) or scenarios_run < 6:
        result.fail(
            f"regime_fixture_results: scenarios_run must be >= 6, got {scenarios_run}"
        )

    results_list = data.get("results") or []
    if not isinstance(results_list, list):
        result.fail("regime_fixture_results: 'results' must be a list")
        return result

    for i, r in enumerate(results_list):
        if not isinstance(r, dict):
            result.fail(f"regime_fixture_results: result[{i}] is not an object")
            continue
        for req_field in ["scenario_id", "input_driver_state", "expected_outputs",
                           "actual_outputs", "pass", "mismatches"]:
            if req_field not in r:
                result.fail(f"regime_fixture_results: result[{i}] missing field '{req_field}'")

    # pass/fail count consistency
    s_passed = data.get("scenarios_passed", 0)
    s_failed = data.get("scenarios_failed", 0)
    if isinstance(s_passed, int) and isinstance(s_failed, int) and isinstance(scenarios_run, int):
        if s_passed + s_failed != scenarios_run:
            result.fail(
                f"regime_fixture_results: scenarios_passed ({s_passed}) + "
                f"scenarios_failed ({s_failed}) != scenarios_run ({scenarios_run})"
            )

    return result


def validate_theme_activation_fixture_results(path: str) -> "ValidationResult":
    """Validate data/intelligence/backtest/theme_activation_fixture_results.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("theme_activation_fixture_results: expected a JSON object")
        return result

    for key in ["schema_version", "mode", "total_scenarios", "pass_count", "fail_count",
                "themes_tested", "false_activation_count", "missing_evidence_handled_correctly",
                "headwind_handled_correctly", "crowded_handled_correctly", "scenario_results"]:
        if key not in data:
            result.fail(f"theme_activation_fixture_results: missing key '{key}'")

    _validate_backtest_safety_flags(data, "theme_activation_fixture_results", result)

    if data.get("mode") != "local_fixture_backtest":
        result.fail(
            f"theme_activation_fixture_results: mode must be 'local_fixture_backtest', "
            f"got {data.get('mode')!r}"
        )

    total = data.get("total_scenarios", 0)
    passes = data.get("pass_count", 0)
    fails = data.get("fail_count", 0)
    if isinstance(total, int) and isinstance(passes, int) and isinstance(fails, int):
        if passes + fails != total:
            result.fail(
                f"theme_activation_fixture_results: pass_count ({passes}) + "
                f"fail_count ({fails}) != total_scenarios ({total})"
            )

    return result


def validate_candidate_feed_ablation_results(path: str) -> "ValidationResult":
    """Validate data/intelligence/backtest/candidate_feed_ablation_results.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("candidate_feed_ablation_results: expected a JSON object")
        return result

    for key in ["schema_version", "mode", "variants_run", "variants_passed",
                "variants_failed", "variants"]:
        if key not in data:
            result.fail(f"candidate_feed_ablation_results: missing key '{key}'")

    _validate_backtest_safety_flags(data, "candidate_feed_ablation_results", result)

    if data.get("mode") != "local_fixture_backtest":
        result.fail(
            f"candidate_feed_ablation_results: mode must be 'local_fixture_backtest', "
            f"got {data.get('mode')!r}"
        )

    variants = data.get("variants") or []
    if not isinstance(variants, list):
        result.fail("candidate_feed_ablation_results: 'variants' must be a list")
        return result

    required_variants = {
        "baseline_shadow_universe", "no_economic_candidate_feed", "no_route_tagger",
        "no_quota_allocator", "no_headwind_pressure_candidates",
        "no_manual_protection", "no_attention_cap",
    }
    actual_labels = {v.get("variant_label") for v in variants if isinstance(v, dict)}
    for req in required_variants:
        if req not in actual_labels:
            result.fail(f"candidate_feed_ablation_results: missing required variant '{req}'")

    for v in variants:
        if not isinstance(v, dict):
            continue
        if v.get("live_output_changed") is not False:
            result.fail(
                f"candidate_feed_ablation_results: variant '{v.get('variant_label')}' "
                "live_output_changed must be false"
            )

    # structural_displaced_by_attention must be false on baseline
    baseline = next((v for v in variants if isinstance(v, dict) and
                     v.get("variant_label") == "baseline_shadow_universe"), None)
    if baseline and baseline.get("structural_displaced_by_attention") is True:
        result.fail(
            "candidate_feed_ablation_results: baseline structural_displaced_by_attention must be false"
        )

    return result


def validate_risk_overlay_fixture_results(path: str) -> "ValidationResult":
    """Validate data/intelligence/backtest/risk_overlay_fixture_results.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("risk_overlay_fixture_results: expected a JSON object")
        return result

    for key in ["schema_version", "mode", "scenarios_run", "scenarios_passed",
                "headwind_candidates_executable", "structural_displaced_by_attention",
                "attention_cap_respected", "manual_protection_preserved",
                "no_short_or_order_instruction_generated", "scenario_results"]:
        if key not in data:
            result.fail(f"risk_overlay_fixture_results: missing key '{key}'")

    _validate_backtest_safety_flags(data, "risk_overlay_fixture_results", result)

    if data.get("headwind_candidates_executable") is not False:
        result.fail(
            "risk_overlay_fixture_results: headwind_candidates_executable must be false"
        )
    if data.get("structural_displaced_by_attention") is not False:
        result.fail(
            "risk_overlay_fixture_results: structural_displaced_by_attention must be false"
        )
    if data.get("attention_cap_respected") is not True:
        result.fail(
            "risk_overlay_fixture_results: attention_cap_respected must be true"
        )
    if data.get("manual_protection_preserved") is not True:
        result.fail(
            "risk_overlay_fixture_results: manual_protection_preserved must be true"
        )
    if data.get("no_short_or_order_instruction_generated") is not True:
        result.fail(
            "risk_overlay_fixture_results: no_short_or_order_instruction_generated must be true"
        )

    # Per-scenario checks
    for i, r in enumerate(data.get("scenario_results") or []):
        if not isinstance(r, dict):
            continue
        if r.get("headwind_candidates_executable") is True:
            result.fail(
                f"risk_overlay_fixture_results: scenario[{i}] '{r.get('scenario_id')}' "
                "headwind_candidates_executable must be false"
            )
        if r.get("no_order_instruction_generated") is not True:
            result.fail(
                f"risk_overlay_fixture_results: scenario[{i}] '{r.get('scenario_id')}' "
                "no_order_instruction_generated must be true"
            )

    return result


def validate_historical_replay_fixtures(path: str) -> "ValidationResult":
    """Validate data/intelligence/backtest/historical_replay_fixtures.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("historical_replay_fixtures: expected a JSON object")
        return result

    for key in ["schema_version", "mode", "total_scenarios", "scenarios",
                "engine_limitations"]:
        if key not in data:
            result.fail(f"historical_replay_fixtures: missing key '{key}'")

    _validate_backtest_safety_flags(data, "historical_replay_fixtures", result)

    if data.get("mode") != "local_historical_replay_fixtures":
        result.fail(
            f"historical_replay_fixtures: mode must be 'local_historical_replay_fixtures', "
            f"got {data.get('mode')!r}"
        )

    scenarios = data.get("scenarios") or []
    if not isinstance(scenarios, list):
        result.fail("historical_replay_fixtures: 'scenarios' must be a list")
        return result

    total = data.get("total_scenarios", 0)
    if not isinstance(total, int) or total < 6:
        result.fail(
            f"historical_replay_fixtures: total_scenarios must be >= 6, got {total}"
        )
    if len(scenarios) != total:
        result.warn(
            f"historical_replay_fixtures: scenarios list length ({len(scenarios)}) "
            f"!= total_scenarios ({total})"
        )

    for i, s in enumerate(scenarios):
        if not isinstance(s, dict):
            result.fail(f"historical_replay_fixtures: scenario[{i}] is not an object")
            continue
        for req_field in ["scenario_id", "date_anchor", "scenario_family",
                           "driver_state", "expected_theme_states",
                           "expected_forbidden_outputs"]:
            if req_field not in s:
                result.fail(
                    f"historical_replay_fixtures: scenario[{i}] missing field '{req_field}'"
                )
        # expected_forbidden_outputs must have executable_candidates = false
        efs = s.get("expected_forbidden_outputs") or {}
        if efs.get("executable_candidates") is not False:
            result.fail(
                f"historical_replay_fixtures: scenario[{i}] '{s.get('scenario_id')}' "
                "expected_forbidden_outputs.executable_candidates must be false"
            )

    return result


def validate_historical_replay_results(path: str) -> "ValidationResult":
    """Validate data/intelligence/backtest/historical_replay_results.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("historical_replay_results: expected a JSON object")
        return result

    for key in ["schema_version", "mode", "scenarios_run", "scenarios_passed",
                "scenarios_failed", "results", "overall_status", "warnings",
                "limitations"]:
        if key not in data:
            result.fail(f"historical_replay_results: missing key '{key}'")

    _validate_backtest_safety_flags(data, "historical_replay_results", result)

    if data.get("mode") != "local_historical_replay_fixtures":
        result.fail(
            f"historical_replay_results: mode must be 'local_historical_replay_fixtures', "
            f"got {data.get('mode')!r}"
        )

    scenarios_run = data.get("scenarios_run", 0)
    if not isinstance(scenarios_run, int) or scenarios_run < 6:
        result.fail(
            f"historical_replay_results: scenarios_run must be >= 6, got {scenarios_run}"
        )

    s_passed = data.get("scenarios_passed", 0)
    s_failed = data.get("scenarios_failed", 0)
    if isinstance(s_passed, int) and isinstance(s_failed, int) and isinstance(scenarios_run, int):
        if s_passed + s_failed != scenarios_run:
            result.fail(
                f"historical_replay_results: scenarios_passed ({s_passed}) + "
                f"scenarios_failed ({s_failed}) != scenarios_run ({scenarios_run})"
            )

    results_list = data.get("results") or []
    if not isinstance(results_list, list):
        result.fail("historical_replay_results: 'results' must be a list")
        return result

    for i, r in enumerate(results_list):
        if not isinstance(r, dict):
            result.fail(f"historical_replay_results: result[{i}] is not an object")
            continue
        for req_field in ["scenario_id", "date_anchor", "expected_theme_states",
                           "actual_theme_states", "expected_risk_posture",
                           "actual_risk_posture", "forbidden_outputs_checked",
                           "pass", "mismatches"]:
            if req_field not in r:
                result.fail(
                    f"historical_replay_results: result[{i}] missing field '{req_field}'"
                )
        # forbidden outputs must all be false
        foc = r.get("forbidden_outputs_checked") or {}
        for flag in ["executable_candidates", "symbol_discovery", "raw_news_used",
                     "llm_used", "live_api_called"]:
            if foc.get(flag) is not False:
                result.fail(
                    f"historical_replay_results: result[{i}] '{r.get('scenario_id')}' "
                    f"forbidden_outputs_checked.{flag} must be false"
                )

    return result


def validate_intelligence_backtest_summary(path: str) -> "ValidationResult":
    """Validate data/intelligence/backtest/intelligence_backtest_summary.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("intelligence_backtest_summary: expected a JSON object")
        return result

    for key in ["schema_version", "mode", "regime_fixture_status",
                "theme_activation_fixture_status", "candidate_feed_ablation_status",
                "risk_overlay_fixture_status", "overall_status", "decision_gate",
                "blockers", "warnings", "recommended_next_step"]:
        if key not in data:
            result.fail(f"intelligence_backtest_summary: missing key '{key}'")

    _validate_backtest_safety_flags(data, "intelligence_backtest_summary", result)

    if data.get("mode") != "local_fixture_backtest_summary":
        result.fail(
            f"intelligence_backtest_summary: mode must be 'local_fixture_backtest_summary', "
            f"got {data.get('mode')!r}"
        )

    dg = data.get("decision_gate")
    if dg not in _VALID_DECISION_GATES:
        result.fail(
            f"intelligence_backtest_summary: decision_gate '{dg}' not in "
            f"{sorted(_VALID_DECISION_GATES)}"
        )

    # risk overlay consistency
    ro_status = data.get("risk_overlay_fixture_status") or {}
    if ro_status.get("headwind_candidates_executable") is True:
        result.fail(
            "intelligence_backtest_summary: risk_overlay headwind_candidates_executable must be false"
        )
    if ro_status.get("structural_displaced_by_attention") is True:
        result.fail(
            "intelligence_backtest_summary: risk_overlay structural_displaced_by_attention must be false"
        )
    if ro_status.get("attention_cap_respected") is False:
        result.fail(
            "intelligence_backtest_summary: risk_overlay attention_cap_respected must be true"
        )

    # Sprint 5B: historical_replay_status should be present (warn if absent)
    if "historical_replay_status" not in data:
        result.warn(
            "intelligence_backtest_summary: historical_replay_status not present "
            "(expected after Sprint 5B)"
        )
    else:
        hr_status = data.get("historical_replay_status") or {}
        if not isinstance(hr_status, dict):
            result.fail(
                "intelligence_backtest_summary: historical_replay_status must be an object"
            )

    return result


def validate_all(base_dir: str = "data/intelligence") -> dict[str, ValidationResult]:
    """Validate all intelligence files. Returns a dict keyed by file label."""
    rules_path = os.path.join(base_dir, "transmission_rules.json")
    taxonomy_path = os.path.join(base_dir, "theme_taxonomy.json")
    roster_path = os.path.join(base_dir, "thematic_roster.json")
    feed_path = os.path.join(base_dir, "economic_candidate_feed.json")

    results = {
        "transmission_rules": validate_transmission_rules(rules_path),
        "theme_taxonomy":      validate_theme_taxonomy(taxonomy_path),
        "thematic_roster":     validate_thematic_roster(roster_path, taxonomy_path=taxonomy_path),
    }

    if os.path.exists(feed_path):
        results["economic_candidate_feed"] = validate_economic_candidate_feed(
            feed_path,
            roster_path=roster_path,
            taxonomy_path=taxonomy_path,
        )

    adapter_snap_path = os.path.join(base_dir, "source_adapter_snapshot.json")
    if os.path.exists(adapter_snap_path):
        results["adapter_snapshot"] = validate_adapter_snapshot(adapter_snap_path)

    ub_dir = os.path.join(os.path.dirname(base_dir), "universe_builder")

    shadow_path = os.path.join(ub_dir, "active_opportunity_universe_shadow.json")
    if os.path.exists(shadow_path):
        results["active_opportunity_universe_shadow"] = validate_shadow_universe(shadow_path)

    comparison_path = os.path.join(ub_dir, "current_vs_shadow_comparison.json")
    if os.path.exists(comparison_path):
        results["current_vs_shadow_comparison"] = validate_comparison(comparison_path)

    report_path = os.path.join(ub_dir, "universe_builder_report.json")
    if os.path.exists(report_path):
        results["universe_builder_report"] = validate_report(report_path)

    # Sprint 4A — daily_economic_state and current_economic_context
    daily_state_path = os.path.join(base_dir, "daily_economic_state.json")
    if os.path.exists(daily_state_path):
        results["daily_economic_state"] = validate_daily_economic_state(daily_state_path)

    context_path = os.path.join(base_dir, "current_economic_context.json")
    if os.path.exists(context_path):
        results["current_economic_context"] = validate_current_economic_context(context_path)

    # Sprint 4B — theme_activation and thesis_store
    activation_path = os.path.join(base_dir, "theme_activation.json")
    if os.path.exists(activation_path):
        results["theme_activation"] = validate_theme_activation(activation_path)

    thesis_path = os.path.join(base_dir, "thesis_store.json")
    if os.path.exists(thesis_path):
        results["thesis_store"] = validate_thesis_store(thesis_path)

    # Sprint 5A — backtest outputs (optional, only if present)
    bt_dir = os.path.join(base_dir, "backtest")

    regime_bt_path = os.path.join(bt_dir, "regime_fixture_results.json")
    if os.path.exists(regime_bt_path):
        results["regime_fixture_results"] = validate_regime_fixture_results(regime_bt_path)

    theme_bt_path = os.path.join(bt_dir, "theme_activation_fixture_results.json")
    if os.path.exists(theme_bt_path):
        results["theme_activation_fixture_results"] = validate_theme_activation_fixture_results(
            theme_bt_path
        )

    ablation_bt_path = os.path.join(bt_dir, "candidate_feed_ablation_results.json")
    if os.path.exists(ablation_bt_path):
        results["candidate_feed_ablation_results"] = validate_candidate_feed_ablation_results(
            ablation_bt_path
        )

    risk_bt_path = os.path.join(bt_dir, "risk_overlay_fixture_results.json")
    if os.path.exists(risk_bt_path):
        results["risk_overlay_fixture_results"] = validate_risk_overlay_fixture_results(
            risk_bt_path
        )

    summary_bt_path = os.path.join(bt_dir, "intelligence_backtest_summary.json")
    if os.path.exists(summary_bt_path):
        results["intelligence_backtest_summary"] = validate_intelligence_backtest_summary(
            summary_bt_path
        )

    # Sprint 5B — historical replay fixtures and results
    hist_fixtures_path = os.path.join(bt_dir, "historical_replay_fixtures.json")
    if os.path.exists(hist_fixtures_path):
        results["historical_replay_fixtures"] = validate_historical_replay_fixtures(
            hist_fixtures_path
        )

    hist_results_path = os.path.join(bt_dir, "historical_replay_results.json")
    if os.path.exists(hist_results_path):
        results["historical_replay_results"] = validate_historical_replay_results(
            hist_results_path
        )

    # Sprint 6A — advisory report (optional, only if present)
    advisory_path = os.path.join(base_dir, "advisory_report.json")
    if os.path.exists(advisory_path):
        results["advisory_report"] = validate_advisory_report(advisory_path)

    # Sprint 6C — advisory log review (optional, only if present)
    review_path = os.path.join(base_dir, "advisory_log_review.json")
    if os.path.exists(review_path):
        results["advisory_log_review"] = validate_advisory_log_review(review_path)

    # Sprint 7A.1 — reference data layer (optional, only if present)
    ref_dir = os.path.join(os.path.dirname(base_dir), "reference")

    sector_schema_path = os.path.join(ref_dir, "sector_schema.json")
    if os.path.exists(sector_schema_path):
        results["sector_schema"] = validate_sector_schema(sector_schema_path)

    symbol_master_path = os.path.join(ref_dir, "symbol_master.json")
    if os.path.exists(symbol_master_path):
        results["symbol_master"] = validate_symbol_master(symbol_master_path)

    theme_overlay_path = os.path.join(ref_dir, "theme_overlay_map.json")
    if os.path.exists(theme_overlay_path):
        results["theme_overlay_map"] = validate_theme_overlay_map(theme_overlay_path)

    coverage_gap_path = os.path.join(base_dir, "coverage_gap_review.json")
    if os.path.exists(coverage_gap_path):
        results["coverage_gap_review"] = validate_coverage_gap_review(coverage_gap_path)

    # Sprint 7A.3 — factor registry and provider files
    factor_registry_path = os.path.join(ref_dir, "factor_registry.json")
    if os.path.exists(factor_registry_path):
        results["factor_registry"] = validate_factor_registry(factor_registry_path)

    provider_cap_path = os.path.join(ref_dir, "provider_capability_matrix.json")
    if os.path.exists(provider_cap_path):
        results["provider_capability_matrix"] = validate_provider_capability_matrix(provider_cap_path)

    fetch_test_path = os.path.join(ref_dir, "provider_fetch_test_results.json")
    if os.path.exists(fetch_test_path):
        results["provider_fetch_test_results"] = validate_provider_fetch_test_results(fetch_test_path)

    layer_map_path = os.path.join(ref_dir, "layer_factor_map.json")
    if os.path.exists(layer_map_path):
        results["layer_factor_map"] = validate_layer_factor_map(layer_map_path)

    data_quality_path = os.path.join(ref_dir, "data_quality_report.json")
    if os.path.exists(data_quality_path):
        results["data_quality_report"] = validate_data_quality_report(data_quality_path)

    # Sprint 7B — paper handoff files (optional, only if present)
    live_dir = os.path.join(os.path.dirname(base_dir), "live")

    paper_universe_path = os.path.join(live_dir, "paper_active_opportunity_universe.json")
    if os.path.exists(paper_universe_path):
        results["paper_active_opportunity_universe"] = validate_paper_active_universe(
            paper_universe_path
        )

    paper_manifest_path = os.path.join(live_dir, "paper_current_manifest.json")
    if os.path.exists(paper_manifest_path):
        results["paper_current_manifest"] = validate_paper_manifest(paper_manifest_path)

    paper_report_path = os.path.join(live_dir, "paper_handoff_validation_report.json")
    if os.path.exists(paper_report_path):
        results["paper_handoff_validation_report"] = validate_paper_handoff_validation_report(
            paper_report_path
        )

    # Sprint 7C — paper handoff comparison report (optional, only if present)
    comparison_report_path = os.path.join(live_dir, "paper_handoff_comparison_report.json")
    if os.path.exists(comparison_report_path):
        results["paper_handoff_comparison_report"] = validate_paper_handoff_comparison_report(
            comparison_report_path
        )

    return results


# ---------------------------------------------------------------------------
# Sprint 6A — advisory_report.json validator
# ---------------------------------------------------------------------------
_ADVISORY_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "valid_for_session", "mode",
    "data_source_mode", "source_files", "advisory_summary", "candidate_advisory",
    "route_disagreements", "unsupported_current_candidates", "missing_shadow_candidates",
    "tier_d_advisory", "structural_quota_advisory", "risk_theme_advisory",
    "manual_and_held_advisory", "warnings",
    "no_live_api_called", "broker_called", "env_inspected", "raw_news_used",
    "llm_used", "broad_intraday_scan_used", "production_modules_imported",
    "live_output_changed",
]

_ADVISORY_SUMMARY_REQUIRED_KEYS = [
    "current_candidates_count", "shadow_candidates_count", "overlap_count",
    "advisory_include_count", "advisory_watch_count", "advisory_defer_count",
    "advisory_exclude_count", "advisory_unresolved_count", "route_disagreement_count",
    "unsupported_current_count", "missing_shadow_count", "non_executable_all",
    "live_output_changed",
]

_VALID_ADVISORY_STATUSES = {
    "advisory_include", "advisory_watch", "advisory_defer",
    "advisory_exclude", "advisory_unresolved",
}

_ADVISORY_CANDIDATE_REQUIRED_KEYS = [
    "symbol", "in_current", "in_shadow", "current_sources", "shadow_sources",
    "advisory_status", "advisory_reason", "executable", "order_instruction",
]

_ADVISORY_SECTION_KEYS = [
    "route_disagreements", "unsupported_current_candidates", "missing_shadow_candidates",
    "tier_d_advisory", "structural_quota_advisory", "risk_theme_advisory",
    "manual_and_held_advisory",
]


def validate_advisory_report(path: str) -> "ValidationResult":
    """Validate data/intelligence/advisory_report.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("advisory_report: not a dict")
        return result

    # Required top-level keys
    for key in _ADVISORY_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"advisory_report: missing required key '{key}'")

    # Safety flags
    if data.get("no_live_api_called") is not True:
        result.fail("advisory_report: no_live_api_called must be true")
    if data.get("broker_called") is not False:
        result.fail("advisory_report: broker_called must be false")
    if data.get("env_inspected") is not False:
        result.fail("advisory_report: env_inspected must be false")
    if data.get("raw_news_used") is not False:
        result.fail("advisory_report: raw_news_used must be false")
    if data.get("llm_used") is not False:
        result.fail("advisory_report: llm_used must be false")
    if data.get("broad_intraday_scan_used") is not False:
        result.fail("advisory_report: broad_intraday_scan_used must be false")
    if data.get("production_modules_imported") is not False:
        result.fail("advisory_report: production_modules_imported must be false")
    if data.get("live_output_changed") is not False:
        result.fail("advisory_report: live_output_changed must be false")

    # Mode checks
    if data.get("mode") != "offline_advisory_report":
        result.fail(f"advisory_report: mode must be 'offline_advisory_report', got '{data.get('mode')}'")

    # advisory_summary checks
    adv_sum = data.get("advisory_summary")
    if not isinstance(adv_sum, dict):
        result.fail("advisory_report: advisory_summary must be a dict")
    else:
        for key in _ADVISORY_SUMMARY_REQUIRED_KEYS:
            if key not in adv_sum:
                result.fail(f"advisory_report.advisory_summary: missing key '{key}'")
        if adv_sum.get("non_executable_all") is not True:
            result.fail("advisory_report.advisory_summary: non_executable_all must be true")
        if adv_sum.get("live_output_changed") is not False:
            result.fail("advisory_report.advisory_summary: live_output_changed must be false")

    # candidate_advisory checks
    ca = data.get("candidate_advisory")
    if not isinstance(ca, list):
        result.fail("advisory_report: candidate_advisory must be a list")
    else:
        for i, rec in enumerate(ca):
            if not isinstance(rec, dict):
                result.fail(f"advisory_report.candidate_advisory[{i}]: not a dict")
                continue
            for key in _ADVISORY_CANDIDATE_REQUIRED_KEYS:
                if key not in rec:
                    result.fail(f"advisory_report.candidate_advisory[{i}]: missing key '{key}'")
            status = rec.get("advisory_status")
            if status not in _VALID_ADVISORY_STATUSES:
                result.fail(f"advisory_report.candidate_advisory[{i}] symbol={rec.get('symbol')}: "
                            f"invalid advisory_status '{status}'")
            if rec.get("executable") is not False:
                result.fail(f"advisory_report.candidate_advisory[{i}] symbol={rec.get('symbol')}: "
                            f"executable must be false")
            if rec.get("order_instruction") is not None:
                result.fail(f"advisory_report.candidate_advisory[{i}] symbol={rec.get('symbol')}: "
                            f"order_instruction must be null")

    # Required section keys exist
    for section in _ADVISORY_SECTION_KEYS:
        if section not in data:
            result.fail(f"advisory_report: missing required section '{section}'")

    # route_disagreements check
    rd = data.get("route_disagreements")
    if isinstance(rd, dict):
        for dis in (rd.get("disagreements") or []):
            if isinstance(dis, dict) and dis.get("executable") is not False:
                result.fail(f"advisory_report.route_disagreements: disagreement for "
                            f"'{dis.get('symbol')}' has executable != false")
    else:
        result.warn("advisory_report: route_disagreements is not a dict")

    # structural_quota_advisory
    sqa = data.get("structural_quota_advisory")
    if isinstance(sqa, dict):
        if sqa.get("production_change_required") is not False:
            result.fail("advisory_report.structural_quota_advisory: production_change_required must be false")
    else:
        result.warn("advisory_report: structural_quota_advisory is not a dict")

    # risk_theme_advisory
    rta = data.get("risk_theme_advisory")
    if isinstance(rta, dict):
        if rta.get("executable_headwind_candidates") is not False:
            result.fail("advisory_report.risk_theme_advisory: executable_headwind_candidates must be false")
        if rta.get("short_or_hedge_instruction_generated") is not False:
            result.fail("advisory_report.risk_theme_advisory: short_or_hedge_instruction_generated must be false")
    else:
        result.warn("advisory_report: risk_theme_advisory is not a dict")

    return result


# ---------------------------------------------------------------------------
# Sprint 6C — advisory_log_review.json validator
# ---------------------------------------------------------------------------
_ADVISORY_LOG_REVIEW_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "mode", "source_file",
    "review_summary", "candidate_overlap_analysis", "safety_analysis",
    "decision_gate", "gate_reasons", "warnings", "minimum_threshold",
    "advisory_only", "executable", "order_instruction",
    "production_decision_changed", "apex_input_changed",
    "live_output_changed",
]

_ADVISORY_LOG_REVIEW_SUMMARY_REQUIRED_KEYS = [
    "records_read", "sessions_detected",
    "advisory_report_available_rate", "advisory_report_fresh_rate",
    "advisory_only_all_records", "non_executable_all_records",
    "production_decision_changed_count", "apex_input_changed_count",
]

_VALID_DECISION_GATE_VALUES = {
    "insufficient_live_observation",
    "advisory_safe_continue_logging",
    "advisory_ready_for_handoff_design",
    "advisory_needs_fix",
}

_ADVISORY_LOG_REVIEW_MUST_BE_FALSE = [
    "executable", "production_decision_changed", "apex_input_changed",
    "live_output_changed",
]


def validate_advisory_log_review(path: str) -> "ValidationResult":
    """Validate data/intelligence/advisory_log_review.json (Sprint 6C)."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("advisory_log_review: not a dict")
        return result

    # Required top-level keys
    for key in _ADVISORY_LOG_REVIEW_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"advisory_log_review: missing required key '{key}'")

    # mode must be evidence_review_only
    if data.get("mode") != "evidence_review_only":
        result.fail(
            f"advisory_log_review: mode must be 'evidence_review_only', "
            f"got {data.get('mode')!r}"
        )

    # decision_gate must be a valid value
    gate = data.get("decision_gate")
    if gate not in _VALID_DECISION_GATE_VALUES:
        result.fail(
            f"advisory_log_review: invalid decision_gate '{gate}'; "
            f"must be one of {sorted(_VALID_DECISION_GATE_VALUES)}"
        )

    # gate_reasons must be a non-empty list
    gate_reasons = data.get("gate_reasons")
    if not isinstance(gate_reasons, list):
        result.fail("advisory_log_review: gate_reasons must be a list")
    elif len(gate_reasons) == 0:
        result.warn("advisory_log_review: gate_reasons is empty")

    # advisory_only must be True
    if data.get("advisory_only") is not True:
        result.fail("advisory_log_review: advisory_only must be true")

    # Must-be-false safety flags
    for flag in _ADVISORY_LOG_REVIEW_MUST_BE_FALSE:
        if data.get(flag) is not False:
            result.fail(
                f"advisory_log_review: {flag} must be false, "
                f"got {data.get(flag)!r}"
            )

    # review_summary section
    rs = data.get("review_summary")
    if not isinstance(rs, dict):
        result.fail("advisory_log_review: review_summary must be a dict")
    else:
        for key in _ADVISORY_LOG_REVIEW_SUMMARY_REQUIRED_KEYS:
            if key not in rs:
                result.fail(f"advisory_log_review.review_summary: missing key '{key}'")
        records_read = rs.get("records_read", 0)
        if not isinstance(records_read, int):
            result.fail("advisory_log_review.review_summary: records_read must be int")
        if not isinstance(rs.get("sessions_detected"), int):
            result.fail("advisory_log_review.review_summary: sessions_detected must be int")
        if rs.get("advisory_only_all_records") is not True and records_read > 0:
            result.fail(
                "advisory_log_review.review_summary: advisory_only_all_records must be true "
                "when records exist"
            )
        if rs.get("non_executable_all_records") is not True and records_read > 0:
            result.fail(
                "advisory_log_review.review_summary: non_executable_all_records must be true "
                "when records exist"
            )
        if rs.get("production_decision_changed_count", -1) != 0:
            result.fail(
                "advisory_log_review.review_summary: production_decision_changed_count must be 0"
            )
        if rs.get("apex_input_changed_count", -1) != 0:
            result.fail(
                "advisory_log_review.review_summary: apex_input_changed_count must be 0"
            )

    # safety_analysis section
    sa = data.get("safety_analysis")
    if not isinstance(sa, dict):
        result.fail("advisory_log_review: safety_analysis must be a dict")
    else:
        if sa.get("all_invariants_hold") is not True:
            # Not a fail — it means there are genuine violations that the reviewer caught;
            # that is valid output. But decision_gate should be advisory_needs_fix.
            if gate != "advisory_needs_fix":
                result.fail(
                    "advisory_log_review: safety_analysis.all_invariants_hold is false "
                    "but decision_gate is not 'advisory_needs_fix'"
                )
        if not isinstance(sa.get("violations"), list):
            result.fail("advisory_log_review.safety_analysis: violations must be a list")

    # minimum_threshold section
    mt = data.get("minimum_threshold")
    if not isinstance(mt, dict):
        result.fail("advisory_log_review: minimum_threshold must be a dict")
    else:
        for key in ("min_records", "min_sessions", "records_met", "sessions_met"):
            if key not in mt:
                result.fail(f"advisory_log_review.minimum_threshold: missing key '{key}'")

    # Zero-record tolerance: if records_read == 0, accept with warning
    rs_dict = data.get("review_summary") or {}
    if rs_dict.get("records_read", 0) == 0:
        result.warn(
            "advisory_log_review: no records observed yet — "
            "advisory_runtime_log.jsonl is empty or missing"
        )

    return result


# ---------------------------------------------------------------------------
# Sprint 7A.1 — Reference data layer validators
# ---------------------------------------------------------------------------

_VALID_CLASSIFICATION_STATUSES = {
    "classified_local",
    "classified_from_existing_source",
    "etf_proxy_classification",
    "commodity_proxy",
    "volatility_proxy",
    "unknown_requires_provider_enrichment",
    "non_equity_proxy",
    "index_or_macro_proxy",
    "crypto_proxy",
    "delisted_or_inactive_unknown",
}

_VALID_APPROVAL_STATUSES = {
    "approved",
    "review_required",
    "scanner_only_attention",
    "rejected",
    "unknown_requires_provider_enrichment",
    "legacy_source_only",
}

_VALID_RECOMMENDED_ACTIONS = {
    "add_to_approved_roster",
    "add_to_review_required",
    "keep_scanner_only_attention",
    "reject_from_intelligence_coverage",
    "needs_provider_enrichment",
    "needs_new_theme",
    "already_covered",
}

_SECTOR_SCHEMA_REQUIRED_KEYS = ["schema_version", "generated_at", "sectors", "proxy_classifications"]
_SYMBOL_MASTER_REQUIRED_KEYS = [
    "schema_version", "generated_at", "symbol_count", "symbols",
    "favourites_used_as_discovery", "live_api_called", "llm_called", "env_inspected",
]
_SYMBOL_RECORD_REQUIRED_KEYS = ["symbol", "sector", "industry", "classification_status", "approval_status", "sources"]
_THEME_OVERLAY_REQUIRED_KEYS = ["schema_version", "generated_at", "theme_count", "themes"]
_THEME_RECORD_REQUIRED_KEYS = ["theme_id", "canonical_symbols", "proxy_symbols", "source"]
_COVERAGE_GAP_REQUIRED_KEYS = [
    "schema_version", "generated_at", "advisory_records_analysed",
    "evidence_status", "required_input_missing",
    "recurring_missing_shadow_count", "recurring_unsupported_current_count",
    "recurring_missing_shadow", "recurring_unsupported_current",
    "live_api_called", "llm_called", "env_inspected",
]

_VALID_EVIDENCE_STATUSES = {
    "sufficient_advisory_input",
    "partial_advisory_input",
    "insufficient_or_stale_advisory_input",
}
_COVERAGE_GAP_ENTRY_REQUIRED_KEYS = [
    "symbol", "occurrence_count", "total_records", "occurrence_rate",
    "counter_type", "sector", "industry", "classification_status", "recommended_action",
]


def validate_sector_schema(path: str) -> "ValidationResult":
    """Validate data/reference/sector_schema.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("sector_schema: not a dict")
        return result

    for key in _SECTOR_SCHEMA_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"sector_schema: missing required key '{key}'")

    sectors = data.get("sectors", [])
    if not isinstance(sectors, list):
        result.fail("sector_schema: sectors must be a list")
    elif len(sectors) < 10:
        result.fail(f"sector_schema: expected ≥10 sectors, got {len(sectors)}")
    else:
        for i, sec in enumerate(sectors):
            if not isinstance(sec, dict):
                result.fail(f"sector_schema.sectors[{i}]: not a dict")
                continue
            for key in ("sector_id", "sector_name", "industries"):
                if key not in sec:
                    result.fail(f"sector_schema.sectors[{i}]: missing key '{key}'")
            if not isinstance(sec.get("industries"), list):
                result.fail(f"sector_schema.sectors[{i}]: industries must be a list")

    # Required proxy classification types per Sprint 7A.1 patch:
    # ETF Proxy, Index Proxy, Commodity Proxy, Crypto Proxy, Volatility Proxy, Macro Proxy, Unknown
    required_proxy_ids = {
        "etf_proxy", "index_proxy", "commodity_proxy",
        "crypto_proxy", "volatility_proxy", "macro_proxy", "unknown",
    }
    proxy_classes = data.get("proxy_classifications", [])
    if not isinstance(proxy_classes, list):
        result.fail("sector_schema: proxy_classifications must be a list")
    else:
        seen_proxy_ids = {p.get("classification_id") for p in proxy_classes if isinstance(p, dict)}
        for pid in sorted(required_proxy_ids - seen_proxy_ids):
            result.fail(f"sector_schema: required proxy_classification '{pid}' is missing")

    if data.get("source") != "reference_data_builder":
        result.warn("sector_schema: source is not 'reference_data_builder'")

    return result


def validate_symbol_master(path: str) -> "ValidationResult":
    """Validate data/reference/symbol_master.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("symbol_master: not a dict")
        return result

    for key in _SYMBOL_MASTER_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"symbol_master: missing required key '{key}'")

    # Safety invariants
    if data.get("favourites_used_as_discovery") is not False:
        result.fail("symbol_master: favourites_used_as_discovery must be false")
    if data.get("live_api_called") is not False:
        result.fail("symbol_master: live_api_called must be false")
    if data.get("llm_called") is not False:
        result.fail("symbol_master: llm_called must be false")
    if data.get("env_inspected") is not False:
        result.fail("symbol_master: env_inspected must be false")

    symbols = data.get("symbols", [])
    if not isinstance(symbols, list):
        result.fail("symbol_master: symbols must be a list")
        return result

    declared_count = data.get("symbol_count", -1)
    if declared_count != len(symbols):
        result.fail(
            f"symbol_master: symbol_count={declared_count} does not match "
            f"len(symbols)={len(symbols)}"
        )

    if len(symbols) < 100:
        result.fail(f"symbol_master: expected ≥100 symbols, got {len(symbols)}")

    seen = set()
    for i, rec in enumerate(symbols[:50]):  # validate first 50 for speed
        if not isinstance(rec, dict):
            result.fail(f"symbol_master.symbols[{i}]: not a dict")
            continue
        for key in _SYMBOL_RECORD_REQUIRED_KEYS:
            if key not in rec:
                result.fail(f"symbol_master.symbols[{i}]: missing key '{key}'")
        sym = rec.get("symbol", "")
        if sym in seen:
            result.fail(f"symbol_master: duplicate symbol '{sym}'")
        seen.add(sym)
        cs = rec.get("classification_status", "")
        if cs not in _VALID_CLASSIFICATION_STATUSES:
            result.fail(f"symbol_master.symbols[{i}]: invalid classification_status '{cs}'")
        ap = rec.get("approval_status", "")
        if ap not in _VALID_APPROVAL_STATUSES:
            result.fail(f"symbol_master.symbols[{i}]: invalid approval_status '{ap}'")
        if not isinstance(rec.get("sources"), list):
            result.fail(f"symbol_master.symbols[{i}]: sources must be a list")

    return result


def validate_theme_overlay_map(path: str) -> "ValidationResult":
    """Validate data/reference/theme_overlay_map.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("theme_overlay_map: not a dict")
        return result

    for key in _THEME_OVERLAY_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"theme_overlay_map: missing required key '{key}'")

    themes = data.get("themes", [])
    if not isinstance(themes, list):
        result.fail("theme_overlay_map: themes must be a list")
        return result

    if len(themes) < 80:
        result.fail(f"theme_overlay_map: expected ≥80 themes, got {len(themes)}")

    declared_count = data.get("theme_count", -1)
    if declared_count != len(themes):
        result.fail(
            f"theme_overlay_map: theme_count={declared_count} does not match "
            f"len(themes)={len(themes)}"
        )

    # Check meta-overlays are present
    required_meta = {
        "emerging_or_unclassified_theme",
        "scanner_only_attention",
        "event_driven_special_situation",
        "unknown_requires_provider_enrichment",
    }
    seen_ids = set()
    for i, t in enumerate(themes):
        if not isinstance(t, dict):
            result.fail(f"theme_overlay_map.themes[{i}]: not a dict")
            continue
        for key in _THEME_RECORD_REQUIRED_KEYS:
            if key not in t:
                result.fail(f"theme_overlay_map.themes[{i}]: missing key '{key}'")
        tid = t.get("theme_id", "")
        if tid in seen_ids:
            result.fail(f"theme_overlay_map: duplicate theme_id '{tid}'")
        seen_ids.add(tid)
        if not isinstance(t.get("canonical_symbols"), list):
            result.fail(f"theme_overlay_map.themes[{i}]: canonical_symbols must be a list")
        if not isinstance(t.get("proxy_symbols"), list):
            result.fail(f"theme_overlay_map.themes[{i}]: proxy_symbols must be a list")

    missing_meta = required_meta - seen_ids
    for meta_id in missing_meta:
        result.fail(f"theme_overlay_map: required meta-overlay '{meta_id}' is missing")

    return result


def validate_coverage_gap_review(path: str) -> "ValidationResult":
    """Validate data/intelligence/coverage_gap_review.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("coverage_gap_review: not a dict")
        return result

    for key in _COVERAGE_GAP_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"coverage_gap_review: missing required key '{key}'")

    # Safety invariants
    if data.get("live_api_called") is not False:
        result.fail("coverage_gap_review: live_api_called must be false")
    if data.get("llm_called") is not False:
        result.fail("coverage_gap_review: llm_called must be false")
    if data.get("env_inspected") is not False:
        result.fail("coverage_gap_review: env_inspected must be false")

    # Evidence status
    es = data.get("evidence_status", "")
    if es not in _VALID_EVIDENCE_STATUSES:
        result.fail(f"coverage_gap_review: invalid evidence_status '{es}'")
    if not isinstance(data.get("required_input_missing"), bool):
        result.fail("coverage_gap_review: required_input_missing must be a boolean")

    for section in ("recurring_missing_shadow", "recurring_unsupported_current"):
        entries = data.get(section, [])
        if not isinstance(entries, list):
            result.fail(f"coverage_gap_review: {section} must be a list")
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                result.fail(f"coverage_gap_review.{section}[{i}]: not a dict")
                continue
            for key in _COVERAGE_GAP_ENTRY_REQUIRED_KEYS:
                if key not in entry:
                    result.fail(f"coverage_gap_review.{section}[{i}]: missing key '{key}'")
            ra = entry.get("recommended_action", "")
            if ra not in _VALID_RECOMMENDED_ACTIONS:
                result.fail(
                    f"coverage_gap_review.{section}[{i}]: invalid recommended_action '{ra}'"
                )

    # Declared counts must match list lengths
    for section, count_key in [
        ("recurring_missing_shadow", "recurring_missing_shadow_count"),
        ("recurring_unsupported_current", "recurring_unsupported_current_count"),
    ]:
        declared = data.get(count_key, -1)
        actual = len(data.get(section, []))
        if declared != actual:
            result.fail(
                f"coverage_gap_review: {count_key}={declared} does not match "
                f"len({section})={actual}"
            )

    return result


# ---------------------------------------------------------------------------
# Sprint 7A.3 — factor_registry.json validator
# ---------------------------------------------------------------------------
_FACTOR_REGISTRY_REQUIRED_KEYS = {
    "schema_version", "generated_at", "source", "total_factors", "categories",
    "factors", "live_output_changed", "llm_called", "live_api_called", "env_inspected",
}
_FACTOR_REQUIRED_KEYS = {
    "factor_id", "factor_name", "category", "owning_layer", "consuming_layers",
    "providers", "primary_provider", "production_runtime_allowed",
    "offline_job_allowed", "update_frequency", "freshness_sla",
    "must_not_trigger_trade_directly",
}


def validate_factor_registry(path: str) -> "ValidationResult":
    """Validate data/reference/factor_registry.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("factor_registry: not a dict")
        return result

    for key in _FACTOR_REGISTRY_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"factor_registry: missing required key '{key}'")

    for flag in ("live_output_changed", "llm_called", "live_api_called", "env_inspected"):
        if data.get(flag) is not False:
            result.fail(f"factor_registry: {flag} must be false")

    factors = data.get("factors", [])
    if not isinstance(factors, list):
        result.fail("factor_registry: factors must be a list")
        return result
    if len(factors) == 0:
        result.fail("factor_registry: factors list is empty")

    seen_ids: set[str] = set()
    for i, factor in enumerate(factors):
        if not isinstance(factor, dict):
            result.fail(f"factor_registry.factors[{i}]: not a dict")
            continue
        for key in _FACTOR_REQUIRED_KEYS:
            if key not in factor:
                result.fail(f"factor_registry.factors[{i}]: missing key '{key}'")
        fid = factor.get("factor_id", "")
        if fid in seen_ids:
            result.fail(f"factor_registry: duplicate factor_id '{fid}'")
        seen_ids.add(fid)
        if factor.get("must_not_trigger_trade_directly") is not True:
            result.fail(
                f"factor_registry.factors[{i}] ('{fid}'): "
                "must_not_trigger_trade_directly must be true"
            )
        if not isinstance(factor.get("consuming_layers"), list):
            result.fail(f"factor_registry.factors[{i}]: consuming_layers must be a list")
        if not isinstance(factor.get("providers"), list):
            result.fail(f"factor_registry.factors[{i}]: providers must be a list")

    declared = data.get("total_factors", -1)
    if declared != len(factors):
        result.fail(
            f"factor_registry: total_factors={declared} does not match len(factors)={len(factors)}"
        )

    return result


# ---------------------------------------------------------------------------
# Sprint 7A.3 — provider_capability_matrix.json validator
# ---------------------------------------------------------------------------
_PROVIDER_CAP_REQUIRED_KEYS = {
    "schema_version", "generated_at", "source", "provider_count",
    "providers", "live_output_changed",
}
_VALID_CAPABILITY_TIERS = {
    "primary_candidate", "secondary_candidate", "fallback_only",
    "research_only", "not_suitable",
}


def validate_provider_capability_matrix(path: str) -> "ValidationResult":
    """Validate data/reference/provider_capability_matrix.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("provider_capability_matrix: not a dict")
        return result

    for key in _PROVIDER_CAP_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"provider_capability_matrix: missing required key '{key}'")

    if data.get("live_output_changed") is not False:
        result.fail("provider_capability_matrix: live_output_changed must be false")

    providers = data.get("providers", [])
    if not isinstance(providers, list):
        result.fail("provider_capability_matrix: providers must be a list")
        return result

    seen_names: set[str] = set()
    for i, prov in enumerate(providers):
        if not isinstance(prov, dict):
            result.fail(f"provider_capability_matrix.providers[{i}]: not a dict")
            continue
        name = prov.get("provider_name", "")
        if not name:
            result.fail(f"provider_capability_matrix.providers[{i}]: missing provider_name")
        if name in seen_names:
            result.fail(f"provider_capability_matrix: duplicate provider_name '{name}'")
        seen_names.add(name)
        caps = prov.get("capabilities", [])
        if not isinstance(caps, list):
            result.fail(f"provider_capability_matrix.providers[{i}]: capabilities must be a list")
            continue
        for j, cap in enumerate(caps):
            if not isinstance(cap, dict):
                result.fail(
                    f"provider_capability_matrix.providers[{i}].capabilities[{j}]: not a dict"
                )
                continue
            # field is named production_suitability in the generated output
            tier = cap.get("production_suitability", cap.get("tier", ""))
            if tier not in _VALID_CAPABILITY_TIERS:
                result.fail(
                    f"provider_capability_matrix.providers[{i}] ('{name}')"
                    f".capabilities[{j}]: invalid production_suitability '{tier}'"
                )

    declared = data.get("provider_count", -1)
    if declared != len(providers):
        result.fail(
            f"provider_capability_matrix: provider_count={declared} does not match "
            f"len(providers)={len(providers)}"
        )

    return result


# ---------------------------------------------------------------------------
# Sprint 7A.3 — provider_fetch_test_results.json validator
# ---------------------------------------------------------------------------
_FETCH_TEST_REQUIRED_KEYS = {
    "schema_version", "generated_at", "source", "test_symbol",
    "safety", "summary", "results",
}
_FETCH_RESULT_REQUIRED_KEYS = {
    "provider", "endpoint", "success", "latency_ms",
    "credentials_present", "secrets_exposed", "live_output_changed",
}
# Flags in the top-level safety block that must always be false (trading/broker/secret)
_FETCH_SAFETY_MUST_BE_FALSE = {
    "trading_api_called",
    "broker_order_api_called",
    "broker_account_api_called",
    "broker_position_api_called",
    "broker_execution_api_called",
    "ibkr_order_account_position_calls",
    "env_values_logged",
    "secrets_exposed",
    "live_output_changed",
}
# Required keys in the safety block (some may be true — e.g. data_provider_api_called)
_FETCH_SAFETY_REQUIRED_KEYS = _FETCH_SAFETY_MUST_BE_FALSE | {
    "data_provider_api_called",
    "ibkr_market_data_connection_attempted",
    "env_presence_checked",
    "env_file_read",
}


def validate_provider_fetch_test_results(path: str) -> "ValidationResult":
    """Validate data/reference/provider_fetch_test_results.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("provider_fetch_test_results: not a dict")
        return result

    for key in _FETCH_TEST_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"provider_fetch_test_results: missing required key '{key}'")

    safety = data.get("safety", {})
    if not isinstance(safety, dict):
        result.fail("provider_fetch_test_results: safety must be a dict")
    else:
        for flag in _FETCH_SAFETY_REQUIRED_KEYS:
            if flag not in safety:
                result.fail(f"provider_fetch_test_results.safety: missing flag '{flag}'")
        for flag in _FETCH_SAFETY_MUST_BE_FALSE:
            if safety.get(flag) is not False:
                result.fail(f"provider_fetch_test_results.safety.{flag} must be false")

    results_list = data.get("results", [])
    if not isinstance(results_list, list):
        result.fail("provider_fetch_test_results: results must be a list")
        return result

    for i, res in enumerate(results_list):
        if not isinstance(res, dict):
            result.fail(f"provider_fetch_test_results.results[{i}]: not a dict")
            continue
        for key in _FETCH_RESULT_REQUIRED_KEYS:
            if key not in res:
                result.fail(f"provider_fetch_test_results.results[{i}]: missing key '{key}'")
        if res.get("secrets_exposed") is not False:
            result.fail(f"provider_fetch_test_results.results[{i}]: secrets_exposed must be false")
        if res.get("live_output_changed") is not False:
            result.fail(f"provider_fetch_test_results.results[{i}]: live_output_changed must be false")

    return result


# ---------------------------------------------------------------------------
# Sprint 7A.3 — layer_factor_map.json validator
# ---------------------------------------------------------------------------
_LAYER_MAP_REQUIRED_KEYS = {
    "schema_version", "generated_at", "source", "layers", "live_output_changed",
}


def validate_layer_factor_map(path: str) -> "ValidationResult":
    """Validate data/reference/layer_factor_map.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("layer_factor_map: not a dict")
        return result

    for key in _LAYER_MAP_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"layer_factor_map: missing required key '{key}'")

    if data.get("live_output_changed") is not False:
        result.fail("layer_factor_map: live_output_changed must be false")

    layers = data.get("layers", [])
    if not isinstance(layers, list):
        result.fail("layer_factor_map: layers must be a list")
        return result
    if len(layers) == 0:
        result.fail("layer_factor_map: layers list is empty")

    # layers is a list of dicts with layer_id, layer_name, factor_ids, factor_count
    for i, layer_data in enumerate(layers):
        if not isinstance(layer_data, dict):
            result.fail(f"layer_factor_map.layers[{i}]: not a dict")
            continue
        layer_name = layer_data.get("layer_name", layer_data.get("layer_id", f"index-{i}"))
        for key in ("factor_count", "factor_ids"):
            if key not in layer_data:
                result.fail(f"layer_factor_map.layers[{i}] ('{layer_name}'): missing key '{key}'")
        factor_ids = layer_data.get("factor_ids", [])
        if not isinstance(factor_ids, list):
            result.fail(f"layer_factor_map.layers[{i}] ('{layer_name}'): factor_ids must be a list")
            continue
        declared = layer_data.get("factor_count", -1)
        if declared != len(factor_ids):
            result.fail(
                f"layer_factor_map.layers[{i}] ('{layer_name}'): factor_count={declared} "
                f"does not match len(factor_ids)={len(factor_ids)}"
            )

    return result


# ---------------------------------------------------------------------------
# Sprint 7A.3 — data_quality_report.json validator
# ---------------------------------------------------------------------------
_DATA_QUALITY_REQUIRED_KEYS = {
    "schema_version", "generated_at", "source",
    "provider_summary", "factor_coverage_summary",
    "production_ready_categories", "partial_categories", "unavailable_categories",
    "live_output_changed", "data_provider_api_called", "live_trading_api_called",
    "env_values_logged", "secrets_exposed",
}


def validate_data_quality_report(path: str) -> "ValidationResult":
    """Validate data/reference/data_quality_report.json."""
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("data_quality_report: not a dict")
        return result

    for key in _DATA_QUALITY_REQUIRED_KEYS:
        if key not in data:
            result.fail(f"data_quality_report: missing required key '{key}'")

    # All of these must be false (static generator — no API calls)
    for flag in (
        "live_output_changed", "data_provider_api_called", "live_trading_api_called",
        "env_values_logged", "secrets_exposed",
    ):
        if data.get(flag) is not False:
            result.fail(f"data_quality_report: {flag} must be false")

    # These are counts (int) not lists in the generated output
    for section in ("production_ready_categories", "partial_categories", "unavailable_categories"):
        if not isinstance(data.get(section), int):
            result.fail(f"data_quality_report: {section} must be an int")

    if not isinstance(data.get("provider_summary"), dict):
        result.fail("data_quality_report: provider_summary must be a dict")

    return result


# ---------------------------------------------------------------------------
# Sprint 7B — paper handoff validators
# ---------------------------------------------------------------------------

_PAPER_UNIVERSE_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "expires_at", "mode",
    "source_shadow_file", "source_files", "validation_status",
    "universe_summary", "candidates",
    "no_executable_trade_instructions", "live_output_changed",
    "secrets_exposed", "env_values_logged",
]

_PAPER_MANIFEST_REQUIRED_TOP_KEYS = [
    "schema_version", "published_at", "expires_at", "validation_status",
    "handoff_mode", "handoff_enabled", "active_universe_file",
    "source_snapshot_versions", "publisher",
    "no_executable_trade_instructions", "live_output_changed",
    "secrets_exposed", "env_values_logged",
]

_PAPER_REPORT_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "mode",
    "manifest_path", "active_universe_path",
    "manifest_validation", "active_universe_validation",
    "candidate_validation_summary",
    "accepted_candidates_count", "rejected_candidates_count",
    "handoff_allowed",
    "production_candidate_source_changed", "apex_input_changed",
    "scanner_output_changed", "risk_logic_changed", "order_logic_changed",
    "broker_called", "trading_api_called", "llm_called",
    "raw_news_used", "broad_intraday_scan_used",
    "secrets_exposed", "env_values_logged", "live_output_changed",
]

_PAPER_SAFETY_MUST_BE_FALSE = {
    "live_output_changed", "secrets_exposed", "env_values_logged",
    "production_candidate_source_changed", "apex_input_changed",
    "scanner_output_changed", "risk_logic_changed", "order_logic_changed",
    "broker_called", "trading_api_called", "llm_called",
    "raw_news_used", "broad_intraday_scan_used",
    "handoff_allowed",
}


def validate_paper_active_universe(path: str) -> ValidationResult:
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("paper_active_universe: not a dict")
        return result

    for key in _PAPER_UNIVERSE_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"paper_active_universe: missing required field '{key}'")

    # mode must be paper_handoff_universe
    if data.get("mode") != "paper_handoff_universe":
        result.fail(f"paper_active_universe: mode must be 'paper_handoff_universe', got {data.get('mode')!r}")

    # no_executable_trade_instructions must be True
    if data.get("no_executable_trade_instructions") is not True:
        result.fail("paper_active_universe: no_executable_trade_instructions must be true")

    # Safety flags must be False
    for flag in ("live_output_changed", "secrets_exposed", "env_values_logged"):
        if data.get(flag) is not False:
            result.fail(f"paper_active_universe: {flag} must be false")

    # candidates must be a non-empty list
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        result.fail("paper_active_universe: candidates must be a list")
    else:
        if len(candidates) == 0:
            result.warn("paper_active_universe: candidates list is empty")
        for i, cand in enumerate(candidates):
            sym = cand.get("symbol", f"idx_{i}")
            if cand.get("executable") is True:
                result.fail(f"paper_active_universe: candidate {sym} has executable=true")
            if cand.get("order_instruction") is not None:
                result.fail(f"paper_active_universe: candidate {sym} has non-null order_instruction")
            if cand.get("live_output_changed") is not False:
                result.fail(f"paper_active_universe: candidate {sym} has live_output_changed!=false")

    if "validation_status" not in data:
        result.fail("paper_active_universe: validation_status missing")

    return result


def validate_paper_manifest(path: str) -> ValidationResult:
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("paper_manifest: not a dict")
        return result

    for key in _PAPER_MANIFEST_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"paper_manifest: missing required field '{key}'")

    # handoff_enabled must be False
    if data.get("handoff_enabled") is not False:
        result.fail(
            f"paper_manifest: handoff_enabled must be false (Sprint 7B), "
            f"got {data.get('handoff_enabled')!r}"
        )

    # handoff_mode must be "paper"
    if data.get("handoff_mode") != "paper":
        result.fail(f"paper_manifest: handoff_mode must be 'paper', got {data.get('handoff_mode')!r}")

    # no_executable_trade_instructions must be True
    if data.get("no_executable_trade_instructions") is not True:
        result.fail("paper_manifest: no_executable_trade_instructions must be true")

    # Safety flags must be False
    for flag in ("live_output_changed", "secrets_exposed", "env_values_logged"):
        if data.get(flag) is not False:
            result.fail(f"paper_manifest: {flag} must be false")

    if "validation_status" not in data:
        result.fail("paper_manifest: validation_status missing")

    # active_universe_file must not point to production files
    auf = data.get("active_universe_file") or ""
    if auf.endswith("active_opportunity_universe.json") and "paper_" not in auf:
        result.fail(
            "paper_manifest: active_universe_file must not point to production universe; "
            f"got {auf!r}"
        )

    if not isinstance(data.get("source_snapshot_versions"), dict):
        result.fail("paper_manifest: source_snapshot_versions must be a dict")

    return result


def validate_paper_handoff_validation_report(path: str) -> ValidationResult:
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("paper_handoff_validation_report: not a dict")
        return result

    for key in _PAPER_REPORT_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"paper_handoff_validation_report: missing required field '{key}'")

    # mode must be paper_handoff_validation
    if data.get("mode") != "paper_handoff_validation":
        result.fail(
            f"paper_handoff_validation_report: mode must be 'paper_handoff_validation', "
            f"got {data.get('mode')!r}"
        )

    # All must-be-false flags
    for flag in _PAPER_SAFETY_MUST_BE_FALSE:
        if data.get(flag) is not False:
            result.fail(
                f"paper_handoff_validation_report: {flag} must be false, "
                f"got {data.get(flag)!r}"
            )

    for section in ("manifest_validation", "active_universe_validation"):
        if not isinstance(data.get(section), dict):
            result.fail(f"paper_handoff_validation_report: {section} must be a dict")

    cvs = data.get("candidate_validation_summary")
    if not isinstance(cvs, dict):
        result.fail("paper_handoff_validation_report: candidate_validation_summary must be a dict")
    else:
        for k in ("total", "accepted", "rejected"):
            if k not in cvs:
                result.fail(
                    f"paper_handoff_validation_report: candidate_validation_summary missing '{k}'"
                )

    return result


# ---------------------------------------------------------------------------
# Sprint 7C — paper_handoff_comparison_report.json validator
# ---------------------------------------------------------------------------

_COMPARISON_REPORT_REQUIRED_TOP_KEYS = [
    "schema_version", "generated_at", "mode",
    "paper_manifest_summary", "paper_universe_summary", "current_pipeline_summary",
    "overlap_analysis", "drop_analysis", "addition_analysis",
    "route_disagreement_analysis", "quota_pressure_analysis",
    "coverage_gap_analysis", "approved_gap_symbol_analysis",
    "safety_analysis", "recommendation",
    "production_candidate_source_changed", "apex_input_changed",
    "scanner_output_changed", "risk_logic_changed", "order_logic_changed",
    "broker_called", "trading_api_called", "llm_called",
    "raw_news_used", "broad_intraday_scan_used",
    "secrets_exposed", "env_values_logged", "live_output_changed",
]

_COMPARISON_SAFETY_MUST_BE_FALSE = {
    "production_candidate_source_changed", "apex_input_changed",
    "scanner_output_changed", "risk_logic_changed", "order_logic_changed",
    "broker_called", "trading_api_called", "llm_called",
    "raw_news_used", "broad_intraday_scan_used",
    "secrets_exposed", "env_values_logged", "live_output_changed",
}

_VALID_COMPARISON_RECOMMENDATIONS = {
    "continue_paper_comparison",
    "ready_for_controlled_handoff_design",
    "fix_paper_handoff_validation",
    "fix_coverage_or_quota_before_handoff",
    "insufficient_evidence",
}

_COMPARISON_GOVERNED_GAP_SYMBOLS = ("SNDK", "WDC", "IREN")


def validate_paper_handoff_comparison_report(path: str) -> ValidationResult:
    result = ValidationResult()
    data, err = _load_json(path)
    if err:
        result.fail(err)
        return result
    if not isinstance(data, dict):
        result.fail("paper_handoff_comparison_report: not a dict")
        return result

    # Required top-level fields
    for key in _COMPARISON_REPORT_REQUIRED_TOP_KEYS:
        if key not in data:
            result.fail(f"paper_handoff_comparison_report: missing required field '{key}'")

    # mode must be paper_handoff_comparison
    if data.get("mode") != "paper_handoff_comparison":
        result.fail(
            f"paper_handoff_comparison_report: mode must be 'paper_handoff_comparison', "
            f"got {data.get('mode')!r}"
        )

    # All safety flags must be False
    for flag in _COMPARISON_SAFETY_MUST_BE_FALSE:
        if data.get(flag) is not False:
            result.fail(
                f"paper_handoff_comparison_report: {flag} must be false, "
                f"got {data.get(flag)!r}"
            )

    # recommendation must be a valid value
    rec = data.get("recommendation")
    if rec not in _VALID_COMPARISON_RECOMMENDATIONS:
        result.fail(
            f"paper_handoff_comparison_report: recommendation {rec!r} is not a valid value; "
            f"must be one of {sorted(_VALID_COMPARISON_RECOMMENDATIONS)}"
        )

    # safety_analysis must exist and be a dict
    if not isinstance(data.get("safety_analysis"), dict):
        result.fail("paper_handoff_comparison_report: safety_analysis must be a dict")

    # approved_gap_symbol_analysis must include SNDK, WDC, IREN
    aga = data.get("approved_gap_symbol_analysis")
    if not isinstance(aga, dict):
        result.fail("paper_handoff_comparison_report: approved_gap_symbol_analysis must be a dict")
    else:
        for sym in _COMPARISON_GOVERNED_GAP_SYMBOLS:
            if sym not in aga:
                result.fail(
                    f"paper_handoff_comparison_report: approved_gap_symbol_analysis "
                    f"missing required symbol '{sym}'"
                )
            else:
                sym_data = aga[sym]
                if sym_data.get("executable") is not False:
                    result.fail(
                        f"paper_handoff_comparison_report: approved_gap_symbol_analysis[{sym}] "
                        f"executable must be False"
                    )

    # Required analysis sections must be dicts/lists
    for section in (
        "overlap_analysis", "route_disagreement_analysis",
        "quota_pressure_analysis", "coverage_gap_analysis",
    ):
        if not isinstance(data.get(section), dict):
            result.fail(f"paper_handoff_comparison_report: {section} must be a dict")

    for section in ("drop_analysis", "addition_analysis"):
        if not isinstance(data.get(section), list):
            result.fail(f"paper_handoff_comparison_report: {section} must be a list")

    return result
