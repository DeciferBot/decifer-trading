# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  theme_graph.py                            ║
# ║   Theme Transmission Graph — intelligence layer             ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
theme_graph.py — Theme Transmission Graph (TTG) for customer-facing intelligence.

Loads the four static data files in data/intelligence/theme_graph/ and provides:
  - Evidence-gated symbol cards with full driver→theme→subtheme→bucket reason paths
  - Theme and bucket browse endpoints
  - Text search across symbols, themes, and buckets
  - Shadow candidate feed for universe_builder (read-only; does NOT trigger execution)

Layer: INTELLIGENCE — must NOT import any execution module.
Data: deterministic static JSON — no LLM, no broker, no yfinance at this layer.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

log = logging.getLogger("decifer.theme_graph")

_BASE = os.path.dirname(os.path.abspath(__file__))
# Prefer intelligence_ref/theme_graph/ (baked into Docker image outside volume mount);
# fall back to data/intelligence/theme_graph/ for local dev.
_INTEL_REF_TTG = os.path.join(_BASE, "intelligence_ref", "theme_graph")
_INTEL_DATA_TTG = os.path.join(_BASE, "data", "intelligence", "theme_graph")
_TTG_DIR = _INTEL_REF_TTG if os.path.isdir(_INTEL_REF_TTG) else _INTEL_DATA_TTG
_DRIVER_STATE_PATH = os.path.join(_BASE, "data", "intelligence", "live_driver_state.json")

# ---------------------------------------------------------------------------
# Evidence gate
# ---------------------------------------------------------------------------

_ACCEPTED_EVIDENCE_BASIS: frozenset[str] = frozenset({
    "curated_reference",
    "company_profile",
    "official_source",
    "filing",
    "ETF_holding",
    "news_catalyst",
    "internal_symbol_master",
})

_REJECTED_EVIDENCE_BASIS: frozenset[str] = frozenset({
    "LLM_only",
    "keyword_only",
    "popular_online",
    "weak_co_mention",
    "generic_sector_match",
})

# Customer-visible statuses (needs_review and proposed are suppressed)
_CUSTOMER_VISIBLE_STATUSES: frozenset[str] = frozenset({"active", "monitor_only"})


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _read_json(filename: str) -> Any:
    path = os.path.join(_TTG_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_data() -> tuple[dict, list, dict, list]:
    """Load and cache all four TTG data files. Returns (nodes_by_id, edges, bucket_defs_by_id, exposures)."""
    nodes_raw = _read_json("theme_nodes.json").get("nodes", [])
    edges = _read_json("theme_edges.json").get("edges", [])
    buckets_raw = _read_json("bucket_definitions.json").get("buckets", [])
    exposures = _read_json("symbol_exposures.json").get("exposures", [])

    nodes_by_id = {n["id"]: n for n in nodes_raw}
    bucket_defs_by_id = {b["bucket_id"]: b for b in buckets_raw}
    return nodes_by_id, edges, bucket_defs_by_id, exposures


def _get_active_drivers() -> frozenset[str]:
    """Read live_driver_state.json and return the set of currently active driver IDs. Fail soft."""
    try:
        with open(_DRIVER_STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        return frozenset(state.get("active_drivers", []))
    except Exception:
        return frozenset()


# ---------------------------------------------------------------------------
# Evidence gate
# ---------------------------------------------------------------------------

def _evidence_gate(exposure: dict, bucket_defs_by_id: dict) -> bool:
    """Return True if this exposure passes all evidence and status gates."""
    if exposure.get("status") not in _CUSTOMER_VISIBLE_STATUSES:
        return False
    eb = exposure.get("evidence_basis", "")
    if eb in _REJECTED_EVIDENCE_BASIS or eb not in _ACCEPTED_EVIDENCE_BASIS:
        return False
    bucket_id = exposure.get("bucket_id", "")
    if bucket_id in bucket_defs_by_id:
        accepted = bucket_defs_by_id[bucket_id].get("accepted_evidence_types", [])
        if accepted and eb not in accepted:
            return False
    return True


# ---------------------------------------------------------------------------
# Reason path builder
# ---------------------------------------------------------------------------

def _build_reason_path(exposure: dict, nodes_by_id: dict, edges: list) -> list[str]:
    """Build the transmission chain: driver → theme → (subtheme?) → bucket → symbol."""
    driver_id = exposure.get("driver_id", "")
    theme_id = exposure.get("theme_id", "")
    bucket_id = exposure.get("bucket_id", "")

    path: list[str] = []

    if driver_id and driver_id in nodes_by_id:
        path.append(nodes_by_id[driver_id]["label"])
    if theme_id and theme_id in nodes_by_id:
        path.append(nodes_by_id[theme_id]["label"])

    # Find subtheme node on the theme→bucket path (theme→subtheme and subtheme→bucket edges)
    subtheme_candidates = {
        e["to_id"] for e in edges
        if e["from_id"] == theme_id
        and nodes_by_id.get(e["to_id"], {}).get("type") == "subtheme"
    }
    for stid in subtheme_candidates:
        connects_to_bucket = any(
            e["from_id"] == stid and e["to_id"] == bucket_id for e in edges
        )
        if connects_to_bucket and stid in nodes_by_id:
            path.append(nodes_by_id[stid]["label"])
            break

    if bucket_id and bucket_id in nodes_by_id:
        path.append(nodes_by_id[bucket_id]["label"])
    if exposure.get("label"):
        path.append(exposure["label"])

    return path


# ---------------------------------------------------------------------------
# Symbol card builder
# ---------------------------------------------------------------------------

def _build_symbol_card(exposure: dict, nodes_by_id: dict, edges: list,
                       bucket_defs_by_id: dict, active_drivers: frozenset) -> dict:
    driver_id = exposure.get("driver_id", "")
    theme_id = exposure.get("theme_id", "")
    bucket_id = exposure.get("bucket_id", "")
    theme_node = nodes_by_id.get(theme_id, {})
    bucket_node = nodes_by_id.get(bucket_id, {})
    bucket_def = bucket_defs_by_id.get(bucket_id, {})

    return {
        "symbol": exposure.get("symbol", ""),
        "label": exposure.get("label", ""),
        "theme_id": theme_id,
        "theme_label": theme_node.get("label", ""),
        "bucket_id": bucket_id,
        "bucket_label": bucket_node.get("label", bucket_def.get("definition", "")),
        "exposure_type": exposure.get("exposure_type", ""),
        "confidence": exposure.get("confidence"),
        "reason_to_care": exposure.get("reason_to_care", ""),
        "reason_path": _build_reason_path(exposure, nodes_by_id, edges),
        "evidence_basis_label": exposure.get("evidence_basis", ""),
        "route_hint": exposure.get("route_hint", ""),
        "status": exposure.get("status", ""),
        "risk_note": exposure.get("risk_note"),
        "driver_active": driver_id in active_drivers,
        "theme_risk_note": theme_node.get("risk_note"),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_themes_list() -> list[dict]:
    """
    Return all theme-type nodes with driver-activation status.
    Each entry: id, label, plain_english_description, status, driver_ids, driver_active, risk_note.
    """
    nodes_by_id, edges, _, _ = _load_data()
    active_drivers = _get_active_drivers()

    themes = [n for n in nodes_by_id.values() if n.get("type") == "theme"]

    result = []
    for theme in themes:
        tid = theme["id"]
        # Find drivers that connect to this theme
        driver_ids = [
            e["from_id"] for e in edges
            if e["to_id"] == tid and nodes_by_id.get(e["from_id"], {}).get("type") == "driver"
        ]
        result.append({
            "theme_id": tid,
            "label": theme.get("label", ""),
            "plain_english_description": theme.get("plain_english_description", ""),
            "status": theme.get("status", ""),
            "driver_ids": driver_ids,
            "driver_active": any(d in active_drivers for d in driver_ids),
            "risk_note": theme.get("risk_note"),
        })

    return result


def get_theme_detail(theme_id: str) -> dict | None:
    """
    Return full theme detail: theme node, buckets, and all evidence-gated symbols.
    Returns None if theme_id is unknown.
    """
    nodes_by_id, edges, bucket_defs_by_id, exposures = _load_data()
    active_drivers = _get_active_drivers()

    theme_node = nodes_by_id.get(theme_id)
    if not theme_node or theme_node.get("type") != "theme":
        return None

    driver_ids = [
        e["from_id"] for e in edges
        if e["to_id"] == theme_id and nodes_by_id.get(e["from_id"], {}).get("type") == "driver"
    ]

    # Buckets that belong to this theme
    buckets_for_theme = [
        {
            "bucket_id": b["bucket_id"],
            "definition": b.get("definition", ""),
            "default_route_hint": b.get("default_route_hint", ""),
            "status": b.get("status", ""),
        }
        for b in bucket_defs_by_id.values()
        if b.get("parent_theme") == theme_id
    ]

    # Evidence-gated symbols for this theme
    theme_symbols = [
        _build_symbol_card(exp, nodes_by_id, edges, bucket_defs_by_id, active_drivers)
        for exp in exposures
        if exp.get("theme_id") == theme_id and _evidence_gate(exp, bucket_defs_by_id)
    ]

    return {
        "theme_id": theme_id,
        "label": theme_node.get("label", ""),
        "plain_english_description": theme_node.get("plain_english_description", ""),
        "status": theme_node.get("status", ""),
        "driver_ids": driver_ids,
        "driver_active": any(d in active_drivers for d in driver_ids),
        "risk_note": theme_node.get("risk_note"),
        "buckets": buckets_for_theme,
        "symbols": theme_symbols,
    }


def get_symbol_card(ticker: str) -> dict | None:
    """
    Return the CustomerSymbolCard for a ticker. Returns None if not found or fails evidence gate.
    If a ticker appears under multiple themes, returns the highest-confidence active record.
    """
    nodes_by_id, edges, bucket_defs_by_id, exposures = _load_data()
    active_drivers = _get_active_drivers()

    ticker_upper = ticker.upper()
    candidates = [
        exp for exp in exposures
        if exp.get("symbol", "").upper() == ticker_upper
        and _evidence_gate(exp, bucket_defs_by_id)
    ]
    if not candidates:
        return None

    # Prefer active over monitor_only; break ties by confidence
    candidates.sort(key=lambda e: (0 if e.get("status") == "active" else 1, -(e.get("confidence") or 0)))
    return _build_symbol_card(candidates[0], nodes_by_id, edges, bucket_defs_by_id, active_drivers)


def search(query: str) -> dict:
    """
    Search across themes, buckets, and evidence-gated active symbols.
    Returns {themes, symbols, total}.
    Only status=active symbols appear in search results (monitor_only requires direct card lookup).
    """
    nodes_by_id, edges, bucket_defs_by_id, exposures = _load_data()
    active_drivers = _get_active_drivers()

    q = query.strip().lower()
    if not q:
        return {"themes": [], "symbols": [], "total": 0}

    # Theme matches
    matched_themes = [
        {
            "theme_id": tid,
            "label": n["label"],
            "plain_english_description": n.get("plain_english_description", ""),
        }
        for tid, n in nodes_by_id.items()
        if n.get("type") == "theme"
        and (q in n["label"].lower() or q in n.get("plain_english_description", "").lower())
    ]

    # Symbol matches — active status only for search results
    matched_symbols = [
        _build_symbol_card(exp, nodes_by_id, edges, bucket_defs_by_id, active_drivers)
        for exp in exposures
        if exp.get("status") == "active"
        and _evidence_gate(exp, bucket_defs_by_id)
        and (
            q in exp.get("symbol", "").lower()
            or q in exp.get("label", "").lower()
            or q in exp.get("reason_to_care", "").lower()
        )
    ]

    # Deduplicate symbols — one card per ticker (highest confidence active)
    seen: dict[str, dict] = {}
    for card in matched_symbols:
        sym = card["symbol"]
        if sym not in seen or card["confidence"] > seen[sym]["confidence"]:
            seen[sym] = card
    deduped = list(seen.values())

    return {
        "themes": matched_themes,
        "symbols": deduped,
        "total": len(matched_themes) + len(deduped),
    }


def get_shadow_candidates() -> list[dict]:
    """
    Return evidence-gated active symbols as shadow candidates for universe_builder.

    candidate_source = "theme_transmission_graph" on each record.
    These records must NOT trigger execution, order logic, or broker logic.
    Route is advisory only — universe_builder enforces the shadow gate.
    """
    nodes_by_id, edges, bucket_defs_by_id, exposures = _load_data()
    active_drivers = _get_active_drivers()

    candidates = []
    for exp in exposures:
        if exp.get("status") != "active":
            continue
        if not _evidence_gate(exp, bucket_defs_by_id):
            continue
        card = _build_symbol_card(exp, nodes_by_id, edges, bucket_defs_by_id, active_drivers)
        candidates.append({
            "symbol": card["symbol"],
            "label": card["label"],
            "candidate_source": "theme_transmission_graph",
            "theme_id": card["theme_id"],
            "bucket_id": card["bucket_id"],
            "exposure_type": card["exposure_type"],
            "confidence": card["confidence"],
            "reason_to_care": card["reason_to_care"],
            "reason_path": card["reason_path"],
            "evidence_basis": card["evidence_basis_label"],
            "route_hint": card["route_hint"],
            "status": card["status"],
            "driver_active": card["driver_active"],
        })

    return candidates
