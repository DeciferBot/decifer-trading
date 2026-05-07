"""
handoff_candidate_adapter.py — Pure governance metadata adapter for handoff candidates.

Classification: adapter-only / production runtime candidate
Service layer: Handoff / Live bot boundary adapter
Sprint: 7E

Attaches handoff governance metadata to scored dicts after signal scoring.
Pure functions: no I/O, no network, no broker calls, no side effects.

Does NOT modify: score, raw_score, or any signal dimension.
Does NOT add: executable or order_instruction fields to scored dicts.
All governance fields use handoff_* prefix to avoid field collision.

No imports of: scanner, bot_trading, orders_core, guardrails, bot_ibkr,
market_intelligence, apex_orchestrator, advisory_reporter, advisory_log_reviewer,
provider_fetch_tester, backtest_intelligence.
"""
from __future__ import annotations


def build_governance_map(accepted_candidates: list[dict]) -> dict[str, dict]:
    """
    Build a symbol → governance dict from accepted handoff candidates.

    Pure function. No I/O.
    Returns {symbol: candidate_dict} for each accepted candidate.
    Candidates missing a symbol field are silently skipped.
    """
    gov_map: dict[str, dict] = {}
    for candidate in accepted_candidates:
        sym = candidate.get("symbol")
        if sym:
            gov_map[sym] = candidate
    return gov_map


def attach_governance_metadata(
    scored_dicts: list[dict],
    governance_map: dict[str, dict],
) -> None:
    """
    In-place attachment of handoff governance metadata to scored dicts.

    For each scored dict whose symbol is in governance_map, attaches the
    following fields using the handoff_* prefix:

        handoff_symbol            — canonical symbol from handoff
        handoff_route             — route from handoff candidate
        handoff_route_hint        — route_hint from handoff candidate
        handoff_reason_to_care    — reason_to_care from handoff candidate
        handoff_source_labels     — source_labels list from handoff candidate
        handoff_theme_ids         — theme_ids list from handoff candidate
        handoff_risk_flags        — risk_flags list from handoff candidate
        handoff_confirmation_required — confirmation_required from handoff
        handoff_approval_status   — approval_status from handoff candidate
        handoff_quota_group       — quota_group from handoff candidate
        handoff_freshness_status  — freshness_status from handoff candidate
        handoff_executable        — always False (never an executable instruction)
        handoff_order_instruction — always None (never an order instruction)

    Pure lookup — no I/O, no side effects, no network calls.
    Symbols not in governance_map are silently skipped.
    score, raw_score, and signal dimensions are never modified.
    """
    for sd in scored_dicts:
        sym = sd.get("symbol")
        if not sym:
            continue
        gov = governance_map.get(sym)
        if gov is None:
            continue
        sd["handoff_symbol"] = sym
        sd["handoff_route"] = gov.get("route")
        sd["handoff_route_hint"] = gov.get("route_hint")
        sd["handoff_reason_to_care"] = gov.get("reason_to_care")
        sd["handoff_source_labels"] = gov.get("source_labels")
        sd["handoff_theme_ids"] = gov.get("theme_ids")
        sd["handoff_risk_flags"] = gov.get("risk_flags")
        sd["handoff_confirmation_required"] = gov.get("confirmation_required")
        sd["handoff_approval_status"] = gov.get("approval_status")
        sd["handoff_quota_group"] = gov.get("quota_group")
        sd["handoff_freshness_status"] = gov.get("freshness_status")
        sd["handoff_executable"] = False
        sd["handoff_order_instruction"] = None
