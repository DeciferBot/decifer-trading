"""
Apex cap score adjuster.

compute_apex_cap_score() replaces the raw signal score as the sort key for
the Apex top-50 cap. Non-Tier-D candidates are unchanged. Tier D candidates
with a real signal (score >= 18) receive a bounded bonus drawn from their
position-research metadata so they can compete fairly inside the unified cap.

No quota, reserve, or separate lane is created — this is purely a sort-key
adjustment. The cap limit is 50.
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
