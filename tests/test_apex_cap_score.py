"""
test_apex_cap_score.py — Tests for stratify_apex_shortlist and compute_apex_cap_score.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from apex_cap_score import compute_apex_cap_score, stratify_apex_shortlist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _c(symbol: str, score: float, rules: list | None = None, tier: str = "") -> dict:
    """Build a minimal scored candidate dict."""
    c: dict = {"symbol": symbol, "score": score, "apex_cap_score": score}
    if tier:
        c["scanner_tier"] = tier
    if rules is not None:
        c["_rules"] = rules  # stored for test reference only — not used by stratify
    return c


def _gov(symbol: str, rules: list) -> tuple:
    """Build a governance_map entry keyed by symbol."""
    return symbol, {"symbol": symbol, "macro_rules_fired": rules}


def _build_gov(*entries: tuple) -> dict:
    return dict(entries)


def _sorted_by_score(candidates: list) -> list:
    return sorted(candidates, key=lambda c: c.get("apex_cap_score", c.get("score", 0)), reverse=True)


# ---------------------------------------------------------------------------
# compute_apex_cap_score
# ---------------------------------------------------------------------------

def test_non_tier_d_returns_raw_score():
    assert compute_apex_cap_score({"score": 45}) == 45.0


def test_tier_d_below_guardrail_returns_raw_score():
    assert compute_apex_cap_score({"score": 17, "scanner_tier": "D"}) == 17.0


def test_tier_d_gets_discovery_bonus():
    result = compute_apex_cap_score({
        "score": 20, "scanner_tier": "D",
        "adjusted_discovery_score": 10,
    })
    assert result == 25.0  # 20 + min(10,10)*0.5 = 25


def test_tier_d_discovery_bonus_capped_at_5():
    result = compute_apex_cap_score({
        "score": 20, "scanner_tier": "D",
        "adjusted_discovery_score": 100,  # would be 50 without cap
    })
    assert result == 25.0


def test_tier_d_archetype_bonus():
    result = compute_apex_cap_score({
        "score": 20, "scanner_tier": "D",
        "primary_archetype": "compounder",
    })
    assert result == 22.0  # 20 + 0 discovery + 2 archetype


def test_tier_d_bucket_bonus():
    result = compute_apex_cap_score({
        "score": 20, "scanner_tier": "D",
        "universe_bucket": "core_research",
    })
    assert result == 21.0  # 20 + 0 + 0 + 1


def test_tier_d_max_bonus():
    result = compute_apex_cap_score({
        "score": 20, "scanner_tier": "D",
        "adjusted_discovery_score": 10,
        "primary_archetype": "compounder",
        "universe_bucket": "core_research",
    })
    assert result == 28.0  # 20 + 5 + 2 + 1


# ---------------------------------------------------------------------------
# stratify_apex_shortlist — core band unchanged
# ---------------------------------------------------------------------------

def test_core_is_top_30_by_score():
    candidates = _sorted_by_score([_c(f"S{i}", float(100 - i)) for i in range(60)])
    core, theme_lift, expanded = stratify_apex_shortlist(candidates, {})
    assert [c["symbol"] for c in core] == [f"S{i}" for i in range(30)]


def test_core_unchanged_regardless_of_governance_map():
    """Adding theme_lift must never remove or reorder core candidates."""
    candidates = _sorted_by_score([
        _c("A", 60), _c("B", 55), _c("C", 40),  # core
        _c("D", 25),  # remaining — D has a theme not in core
    ])
    gov = _build_gov(
        _gov("A", ["ai_capex_growth_to_semiconductors"]),
        _gov("B", ["ai_capex_growth_to_cybersecurity"]),  # same driver as A
        _gov("C", ["yields_falling_to_software_cloud"]),
        _gov("D", ["geopolitical_risk_to_defence"]),  # new driver → theme_lift
    )
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, gov, core_limit=3, cap_limit=10, expanded_floor=0.0,
    )
    assert [c["symbol"] for c in core] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# theme_lift — guaranteed theme representation
# ---------------------------------------------------------------------------

def test_theme_absent_from_core_gets_lifted():
    """Driver 'software_cloud' has no core representative → D should be lifted."""
    candidates = _sorted_by_score([
        _c("A", 60), _c("B", 55),  # core (2 slots)
        _c("D", 30),  # remaining — software_cloud driver
        _c("E", 25),  # remaining — same driver as core (ai_capex_growth)
    ])
    gov = _build_gov(
        _gov("A", ["ai_capex_growth_to_semiconductors"]),
        _gov("B", ["ai_capex_growth_to_cybersecurity"]),
        _gov("D", ["yields_falling_to_software_cloud"]),
        _gov("E", ["ai_capex_growth_to_data_centre"]),
    )
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, gov, core_limit=2, cap_limit=10, expanded_floor=0.0,
    )
    assert "D" in {c["symbol"] for c in theme_lift}
    assert "E" not in {c["symbol"] for c in theme_lift}  # ai_capex_growth already in core


def test_theme_already_in_core_not_lifted_again():
    candidates = _sorted_by_score([
        _c("A", 60), _c("B", 55),  # core
        _c("C", 30),  # remaining — same driver as A
    ])
    gov = _build_gov(
        _gov("A", ["ai_capex_growth_to_semiconductors"]),
        _gov("B", ["yields_falling_to_software_cloud"]),
        _gov("C", ["ai_capex_growth_to_cybersecurity"]),
    )
    core, theme_lift, _ = stratify_apex_shortlist(
        candidates, gov, core_limit=2, cap_limit=10, expanded_floor=0.0,
    )
    assert "C" not in {c["symbol"] for c in theme_lift}


def test_two_missing_themes_both_lifted():
    candidates = _sorted_by_score([
        _c("A", 60),  # core
        _c("B", 30),  # remaining — theme_1
        _c("C", 20),  # remaining — theme_2
    ])
    gov = _build_gov(
        _gov("A", ["ai_capex_growth_to_semiconductors"]),
        _gov("B", ["yields_falling_to_software_cloud"]),
        _gov("C", ["geopolitical_risk_to_defence"]),
    )
    core, theme_lift, _ = stratify_apex_shortlist(
        candidates, gov, core_limit=1, cap_limit=10, expanded_floor=0.0,
    )
    lift_syms = {c["symbol"] for c in theme_lift}
    assert "B" in lift_syms
    assert "C" in lift_syms


def test_best_scoring_candidate_lifted_per_theme():
    """When two candidates share the same missing driver, only the highest-scored is lifted."""
    candidates = _sorted_by_score([
        _c("A", 60),  # core
        _c("B", 40),  # remaining — yields_falling, higher score
        _c("C", 25),  # remaining — yields_falling, lower score
    ])
    gov = _build_gov(
        _gov("A", ["ai_capex_growth_to_semiconductors"]),
        _gov("B", ["yields_falling_to_software_cloud"]),
        _gov("C", ["yields_falling_to_reits"]),  # same driver prefix
    )
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, gov, core_limit=1, cap_limit=10, expanded_floor=0.0,
    )
    lift_syms = {c["symbol"] for c in theme_lift}
    assert "B" in lift_syms      # best from yields_falling
    assert "C" not in lift_syms  # same driver, second-best


def test_no_rules_candidates_never_lifted():
    """Tier A/B/D candidates with empty macro_rules_fired are never theme-lifted."""
    candidates = _sorted_by_score([
        _c("A", 60),  # core
        _c("B", 30),  # remaining — no rules (Tier D / Tier A)
    ])
    gov = _build_gov(
        _gov("A", ["ai_capex_growth_to_semiconductors"]),
        _gov("B", []),  # empty rules
    )
    core, theme_lift, _ = stratify_apex_shortlist(
        candidates, gov, core_limit=1, cap_limit=10, expanded_floor=0.0,
    )
    assert theme_lift == []


def test_candidate_not_in_governance_map_never_lifted():
    """Candidate missing from governance map has no theme info → never lifted."""
    candidates = _sorted_by_score([
        _c("A", 60),  # core
        _c("UNKNOWN", 30),  # remaining — not in governance_map
    ])
    gov = _build_gov(_gov("A", ["ai_capex_growth_to_semiconductors"]))
    core, theme_lift, _ = stratify_apex_shortlist(
        candidates, gov, core_limit=1, cap_limit=10, expanded_floor=0.0,
    )
    assert theme_lift == []


# ---------------------------------------------------------------------------
# expanded band
# ---------------------------------------------------------------------------

def test_expanded_respects_floor():
    candidates = _sorted_by_score([
        _c("A", 60), _c("B", 55),  # core
        _c("C", 25),  # above floor
        _c("D", 5),   # below floor — must be excluded
    ])
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, {}, core_limit=2, cap_limit=10, expanded_floor=20.0,
    )
    expanded_syms = {c["symbol"] for c in expanded}
    assert "C" in expanded_syms
    assert "D" not in expanded_syms


def test_theme_lift_symbol_not_in_expanded():
    """A symbol that was theme-lifted must not also appear in expanded."""
    candidates = _sorted_by_score([
        _c("A", 60),  # core
        _c("B", 30),  # theme_lift candidate (meets expanded floor too)
    ])
    gov = _build_gov(
        _gov("A", ["ai_capex_growth_to_semiconductors"]),
        _gov("B", ["yields_falling_to_software_cloud"]),
    )
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, gov, core_limit=1, cap_limit=10, expanded_floor=0.0,
    )
    assert "B" in {c["symbol"] for c in theme_lift}
    assert "B" not in {c["symbol"] for c in expanded}


# ---------------------------------------------------------------------------
# Total cap invariant
# ---------------------------------------------------------------------------

def test_total_never_exceeds_cap_limit():
    # 5 core + many theme_lift candidates + many expanded
    candidates = _sorted_by_score([_c(f"S{i}", float(100 - i)) for i in range(60)])
    # Give each non-core candidate a unique driver to maximise theme_lift pressure
    gov = {}
    for i, c in enumerate(candidates):
        gov[c["symbol"]] = {"macro_rules_fired": [f"driver_{i}_to_theme"]}
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, gov, core_limit=5, cap_limit=15, expanded_floor=0.0,
    )
    assert len(core) + len(theme_lift) + len(expanded) <= 15


def test_no_symbol_in_multiple_groups():
    candidates = _sorted_by_score([_c(f"S{i}", float(100 - i)) for i in range(60)])
    gov = {}
    for i, c in enumerate(candidates):
        gov[c["symbol"]] = {"macro_rules_fired": [f"driver_{i % 5}_to_theme"]}
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, gov, core_limit=10, cap_limit=30, expanded_floor=0.0,
    )
    all_syms = [c["symbol"] for c in core + theme_lift + expanded]
    assert len(all_syms) == len(set(all_syms)), "Duplicate symbols across groups"


def test_empty_candidates_returns_three_empty_lists():
    core, theme_lift, expanded = stratify_apex_shortlist([], {})
    assert core == []
    assert theme_lift == []
    assert expanded == []


def test_fewer_candidates_than_core_limit():
    candidates = _sorted_by_score([_c("A", 60), _c("B", 40)])
    core, theme_lift, expanded = stratify_apex_shortlist(
        candidates, {}, core_limit=30, cap_limit=50, expanded_floor=20.0,
    )
    assert len(core) == 2
    assert theme_lift == []
    assert expanded == []
