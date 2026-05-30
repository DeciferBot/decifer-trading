"""
Apex cap score adjuster + theme-stratified shortlist.

compute_apex_cap_score() replaces the raw signal score as the sort key for
the Apex top-50 cap. Non-Tier-D candidates are unchanged. Tier D candidates
with a real signal (score >= 18) receive a bounded bonus drawn from their
position-research metadata so they can compete fairly inside the unified cap.

stratify_apex_shortlist() builds the final shortlist as three disjoint groups:
  core        — top core_limit by apex_cap_score (unconditional)
  theme_lift  — highest-scoring candidate from each driver theme absent from core
  expanded    — remaining candidates >= expanded_floor, up to total cap

theme_lift guarantees that every driver theme represented in the scored pool
has at least one name in the shortlist Apex evaluates. It never removes anything
from the core and never exceeds cap_limit total candidates.
"""


def compute_apex_cap_score(candidate: dict) -> float:
    """Return the adjusted cap sort score for a candidate.

    For non-Tier-D candidates the result equals the raw signal score.
    For Tier D candidates with signal_score < 18 the result also equals
    the raw signal score (guardrail: no research bonus without a real signal).
    For Tier D candidates with signal_score >= 18 a bounded bonus is added:
      discovery_bonus  = min(adjusted_discovery_score, 10) * 0.5   (max  5.0)
      archetype_bonus  = 2 if primary_archetype is set              (max  2.0)
      bucket_bonus     = 1 if universe_bucket == "core_research"    (max  1.0)
    Maximum possible bonus: 8.0 points.
    """
    signal_score = candidate.get("score", 0) or 0

    if candidate.get("scanner_tier") != "D":
        return signal_score

    if signal_score < 18:
        return signal_score

    discovery_score = candidate.get("discovery_score", 0) or 0
    adjusted_discovery_score = candidate.get("adjusted_discovery_score", discovery_score) or 0
    primary_archetype = candidate.get("primary_archetype")
    universe_bucket = candidate.get("universe_bucket")

    conviction_score = min(adjusted_discovery_score, 10)
    discovery_bonus = conviction_score * 0.5
    archetype_bonus = 2 if primary_archetype else 0
    bucket_bonus = 1 if universe_bucket == "core_research" else 0

    return signal_score + discovery_bonus + archetype_bonus + bucket_bonus


# ---------------------------------------------------------------------------
# Theme-stratified shortlist
# ---------------------------------------------------------------------------

def _driver_from_rules(macro_rules_fired: list) -> str:
    """Extract driver prefix from the first transmission rule.

    'ai_capex_growth_to_cybersecurity' → 'ai_capex_growth'
    Returns '' for candidates with no rules (Tier D, Tier A/B, catalyst-only).
    """
    if not macro_rules_fired:
        return ""
    r = macro_rules_fired[0]
    return r.split("_to_")[0] if "_to_" in r else r


def stratify_apex_shortlist(
    sorted_candidates: list,
    governance_map: dict,
    core_limit: int = 30,
    cap_limit: int = 50,
    expanded_floor: float = 20.0,
) -> tuple:
    """
    Build a theme-stratified Apex shortlist from a pre-sorted candidate list.

    Args:
        sorted_candidates: candidates sorted by apex_cap_score descending (all of them).
        governance_map: {symbol: handoff_candidate_dict} — provides macro_rules_fired.
        core_limit: unconditional top-N slots (default 30).
        cap_limit: total shortlist hard ceiling (default 50).
        expanded_floor: minimum apex_cap_score for the expanded band (default 20.0).

    Returns:
        (core, theme_lift, expanded) — three disjoint lists, no symbol in more than one.

        core        — sorted_candidates[:core_limit], unchanged.
        theme_lift  — one candidate per driver theme absent from core, taken from the
                      best-scoring remaining candidates. Capped so total <= cap_limit.
        expanded    — remaining candidates (not in core or theme_lift) that meet
                      expanded_floor, filling up to cap_limit total.

    Guarantees:
        len(core) + len(theme_lift) + len(expanded) <= cap_limit
        No symbol appears in more than one group.
        Core is never modified.
    """
    core = sorted_candidates[:core_limit]
    remaining = sorted_candidates[core_limit:]

    core_syms: set = {c.get("symbol") for c in core}

    # Identify driver themes already covered by the core
    core_drivers: set = set()
    for sym in core_syms:
        gov = governance_map.get(sym) or {}
        rules = gov.get("macro_rules_fired") or []
        driver = _driver_from_rules(rules)
        if driver:
            core_drivers.add(driver)

    # Walk remaining pool (already sorted best-first) and lift one candidate per
    # driver theme that has no core representative.
    theme_lift: list = []
    theme_lift_syms: set = set()
    seen_lifted_drivers: set = set()
    max_lift_slots = cap_limit - core_limit  # safety ceiling

    for c in remaining:
        if len(theme_lift) >= max_lift_slots:
            break
        sym = c.get("symbol")
        gov = governance_map.get(sym) or {}
        rules = gov.get("macro_rules_fired") or []
        driver = _driver_from_rules(rules)
        if not driver:
            continue  # non-intelligence candidate — no theme guarantee
        if driver in core_drivers:
            continue  # theme already in core
        if driver in seen_lifted_drivers:
            continue  # already lifted one from this driver
        theme_lift.append(c)
        theme_lift_syms.add(sym)
        seen_lifted_drivers.add(driver)

    # Expanded: remaining candidates not yet selected, score >= floor, up to cap
    expanded_slots = cap_limit - len(core) - len(theme_lift)
    expanded: list = []
    for c in remaining:
        if len(expanded) >= expanded_slots:
            break
        if c.get("symbol") in theme_lift_syms:
            continue
        if c.get("apex_cap_score", c.get("score", 0)) >= expanded_floor:
            expanded.append(c)

    return core, theme_lift, expanded
