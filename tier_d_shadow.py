"""
tier_d_shadow.py — Tier D Shadow Apex Lane.

Single responsibility: pure functions for selecting shadow Tier D candidates
(those dropped by the main top-30 Apex cap), ranking them, and building the
funnel log record. No IBKR deps, no file I/O, no live API calls.

Consumed by bot_trading.py after the main Apex cap is applied.
Imported directly by tests for unit verification.

Shadow lane guarantee:
- execute=False is enforced at the _run_apex_pipeline call site in bot_trading.py.
- tier_d_shadow_allow_live_entries=False is an additional config-layer hard lock.
- Every entry returned by Apex for the shadow call has execution_allowed=False
  and block_reason="tier_d_shadow_apex_only" forced onto it before logging.
- No orders are placed. No training records are written.
"""
from __future__ import annotations

from datetime import UTC, datetime

# Prefix injected into every shadow candidate so Apex evaluates on
# multi-week thesis quality rather than intraday momentum.
SHADOW_APEX_PREFIX = (
    "[POSITION_CANDIDATE_SHADOW] Source: Position Research Universe. "
    "Candidate was dropped by the main top-30 Apex cap but retained for "
    "shadow-only POSITION evaluation. Evaluate primarily on multi-week "
    "thesis quality, business improvement, relative strength, sector support, "
    "and risk/reward. Do not penalise solely for lack of gap, premarket "
    "volume, or intraday momentum."
)


def _shadow_rank(c: dict) -> float:
    """
    Rank score for shadow Tier D selection.

    Higher = higher priority for shadow Apex evaluation.
      discovery_score * 2   — fundamental quality dominates
      + min(signal_score, 20) — intraday signal adds up to 20 pts
      + 3 * archetype_count  — each matched archetype adds weight
      + 5 if priority_overlap — bonus for overlap with active universe
    """
    ds = c.get("discovery_score") or 0
    ss = min(c.get("score") or 0, 20)
    arch_count = len(c.get("matched_position_archetypes") or [])
    prio = 5 if c.get("priority_overlap") else 0
    return ds * 2 + ss + 3 * arch_count + prio


def select_tier_d_shadow_candidates(
    td_dropped: list[dict],
    cfg: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Filter and rank Tier D candidates dropped by the main Apex cap.

    Returns (selected, not_selected).
    selected   — top-N eligible candidates, tagged with _shadow_hint.
    not_selected — eligible candidates beyond the cap, or all if disabled.

    Config keys read from cfg (falls back to False/defaults if absent):
      tier_d_shadow_apex_enabled          (bool, default False)
      tier_d_shadow_apex_cap              (int,  default 10)
      tier_d_shadow_min_discovery_score   (int,  default 6)
      tier_d_shadow_require_archetype     (bool, default True)
      tier_d_shadow_allow_live_entries    (bool, default False — safety)
    """
    cfg = cfg or {}

    if not cfg.get("tier_d_shadow_apex_enabled", False):
        return [], []

    min_ds   = cfg.get("tier_d_shadow_min_discovery_score", 6)
    req_arch = cfg.get("tier_d_shadow_require_archetype", True)
    cap      = int(cfg.get("tier_d_shadow_apex_cap", 10))

    eligible: list[dict] = []
    for c in td_dropped:
        ds    = c.get("discovery_score") or 0
        archs = c.get("matched_position_archetypes") or []
        if ds < min_ds:
            continue
        if req_arch and not archs:
            continue
        eligible.append(c)

    eligible.sort(key=_shadow_rank, reverse=True)
    selected     = eligible[:cap]
    not_selected = eligible[cap:]

    for c in selected:
        c["_shadow_hint"] = SHADOW_APEX_PREFIX

    return selected, not_selected


def force_shadow_only(entries: list[dict]) -> list[dict]:
    """
    Force every Apex entry to be non-executable.
    Called on the shadow Apex new_entries before logging.
    Returns the modified list.
    """
    for entry in entries:
        entry["trade_type"]         = "POSITION_RESEARCH_ONLY"
        entry["execution_allowed"]  = False
        entry["block_reason"]       = "tier_d_shadow_apex_only"
    return entries


def build_tier_d_shadow_funnel_record(
    cut_all_sorted: list[dict],
    td_before: list[dict],
    td_after: list[dict],
    td_dropped: list[dict],
    selected: list[dict],
    not_selected: list[dict],
    apex_new_entries: list[dict],
    scan_type: str = "live",
) -> dict:
    """
    Build the tier_d_funnel.jsonl record for stage='tier_d_shadow_apex'.
    Pure function — no I/O.
    """
    classifications: dict[str, int] = {}
    for entry in apex_new_entries:
        tt = entry.get("trade_type") or "no_classification"
        classifications[tt] = classifications.get(tt, 0) + 1

    return {
        "ts":                                    datetime.now(UTC).isoformat(),
        "stage":                                 "tier_d_shadow_apex",
        "scan_type":                             scan_type,
        "raw_candidates_before_main_cap":        len(cut_all_sorted),
        "tier_d_before_main_cap":                len(td_before),
        "tier_d_selected_main_cap":              len(td_after),
        "tier_d_dropped_main_cap":               len(td_dropped),
        "tier_d_shadow_eligible":                len(selected) + len(not_selected),
        "tier_d_shadow_selected":                len(selected),
        "tier_d_shadow_not_selected":            len(not_selected),
        "tier_d_shadow_symbols":                 [c.get("symbol") for c in selected],
        "tier_d_shadow_rank_scores": [
            {
                "symbol":          c.get("symbol"),
                "rank_score":      _shadow_rank(c),
                "discovery_score": c.get("discovery_score"),
                "signal_score":    c.get("score"),
                "archetypes":      c.get("matched_position_archetypes"),
            }
            for c in selected
        ],
        "tier_d_shadow_apex_classifications":          classifications,
        "tier_d_shadow_would_have_passed_validation":  0,
        "tier_d_shadow_blocked_count":                 len(apex_new_entries),
        "tier_d_shadow_orders_placed":                 0,
        "tier_d_shadow_training_records_written":      0,
    }
