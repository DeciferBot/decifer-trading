"""
thesis_store.py — Sprint 4B Thesis Store.

Single responsibility: consume theme_activation.json, current_economic_context.json,
economic_candidate_feed.json, and active_opportunity_universe_shadow.json to build
structured, deterministic theme theses.

On subsequent runs, compares against prior thesis_store.json to track status changes.

Writes:
    data/intelligence/thesis_store.json

Public interface (importable):
    ThesisStore.load(path)        → ThesisStore instance
    store.get(theme_id)           → thesis dict or None
    store.all()                   → list[dict]
    store.active()                → list[dict] (status in {active, strengthened})
    store.count()                 → int

FORBIDDEN (hardcoded — never read from .env or config):
    - No live API calls
    - No broker calls
    - No .env inspection
    - No LLM calls
    - No raw news scraping
    - No broad intraday scanning
    - No production module side effects
    - Thesis store cannot create symbols or trades
    - live_output_changed = false
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Safety constants
# ---------------------------------------------------------------------------
_NO_LIVE_API_CALLED: bool = True
_BROKER_CALLED: bool = False
_ENV_INSPECTED: bool = False
_RAW_NEWS_USED: bool = False
_LLM_USED: bool = False
_BROAD_INTRADAY_SCAN_USED: bool = False
_LIVE_OUTPUT_CHANGED: bool = False

# ---------------------------------------------------------------------------
# Valid thesis status vocabulary
# ---------------------------------------------------------------------------
_STATUS_NEW = "new"
_STATUS_ACTIVE = "active"
_STATUS_STRENGTHENED = "strengthened"
_STATUS_WEAKENED = "weakened"
_STATUS_CROWDED = "crowded"
_STATUS_INVALIDATED = "invalidated"
_STATUS_UNCHANGED = "unchanged"
_STATUS_WATCHLIST = "watchlist"

_VALID_THESIS_STATUSES = {
    _STATUS_NEW, _STATUS_ACTIVE, _STATUS_STRENGTHENED, _STATUS_WEAKENED,
    _STATUS_CROWDED, _STATUS_INVALIDATED, _STATUS_UNCHANGED, _STATUS_WATCHLIST,
}

# Mapping from theme activation state → thesis status
_ACTIVATION_TO_STATUS: dict[str, str] = {
    "activated": _STATUS_ACTIVE,
    "strengthening": _STATUS_STRENGTHENED,
    "watchlist": _STATUS_WATCHLIST,
    "weakening": _STATUS_WEAKENED,
    "crowded": _STATUS_CROWDED,
    "invalidated": _STATUS_INVALIDATED,
    "dormant": _STATUS_WATCHLIST,  # dormant themes are watchlisted, not active
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))
_INTEL_DIR = os.path.join(_BASE, "data", "intelligence")
_UB_DIR = os.path.join(_BASE, "data", "universe_builder")

_ACTIVATION_PATH = os.path.join(_INTEL_DIR, "theme_activation.json")
_CONTEXT_PATH = os.path.join(_INTEL_DIR, "current_economic_context.json")
_FEED_PATH = os.path.join(_INTEL_DIR, "economic_candidate_feed.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")

_OUTPUT_PATH = os.path.join(_INTEL_DIR, "thesis_store.json")


def _read_json(path: str) -> tuple[Any, str | None]:
    if not os.path.exists(path):
        return None, f"not_found:{path}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, f"parse_error:{path}:{e}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Deterministic thesis template — no LLM
# ---------------------------------------------------------------------------

def _build_thesis_text(
    theme_id: str,
    state: str,
    drivers: list[str],
    rules: list[str],
    candidate_count: int,
    candidates_in_shadow: int,
    risk_flags: list[str],
    confirmation_requirements: list[str],
) -> str:
    """
    Deterministic template fill — never calls an LLM.
    Template: "Theme {theme_id} is {state} because drivers {drivers} fired rules
    {rules}. Candidate exposure is {candidate_count} symbols, with
    {candidates_in_shadow_count} currently in the shadow universe. Key risks are
    {risk_flags}. Confirmation still required: {confirmation_requirements}."
    """
    drivers_str = ", ".join(drivers) if drivers else "none confirmed"
    rules_str = ", ".join(rules) if rules else "none fired"
    risks_str = "; ".join(risk_flags[:3]) if risk_flags else "none identified"
    confirm_str = "; ".join(confirmation_requirements[:3]) if confirmation_requirements else "none listed"
    return (
        f"Theme {theme_id} is {state} because drivers {drivers_str} fired rules {rules_str}. "
        f"Candidate exposure is {candidate_count} symbols, with {candidates_in_shadow} "
        f"currently in the shadow universe. Key risks are {risks_str}. "
        f"Confirmation still required: {confirm_str}."
    )


# ---------------------------------------------------------------------------
# Build theses
# ---------------------------------------------------------------------------

def _build_theses(
    activation_data: dict | None,
    feed_data: dict | None,
    shadow_data: dict | None,
    prior_theses: dict[str, dict] | None,
) -> list[dict]:
    """
    Build per-theme thesis records from activation data.
    Compare against prior_theses if available to track status changes.
    """
    if not isinstance(activation_data, dict):
        return []

    themes: list[dict] = activation_data.get("themes") or []

    # Symbols from feed per theme
    feed_by_theme: dict[str, list[str]] = {}
    if isinstance(feed_data, dict):
        for c in (feed_data.get("candidates") or []):
            if isinstance(c, dict) and c.get("theme") and c.get("symbol"):
                feed_by_theme.setdefault(c["theme"], []).append(c["symbol"])

    # Route hints from feed per theme
    route_hints_by_theme: dict[str, list[str]] = {}
    if isinstance(feed_data, dict):
        for c in (feed_data.get("candidates") or []):
            if isinstance(c, dict) and c.get("theme"):
                for hint in (c.get("route_hint") or []):
                    hints = route_hints_by_theme.setdefault(c["theme"], [])
                    if hint not in hints:
                        hints.append(hint)

    freshness = "static_shadow_inference_sprint4b"
    now = _now_iso()
    theses: list[dict] = []

    for theme in themes:
        theme_id = theme.get("theme_id", "")
        if not theme_id:
            continue

        activation_state = theme.get("state", "dormant")
        new_status = _ACTIVATION_TO_STATUS.get(activation_state, _STATUS_WATCHLIST)

        # Prior status comparison
        prior = prior_theses.get(theme_id) if prior_theses else None
        prior_status = prior.get("status") if prior else None
        prior_thesis_text = prior.get("current_thesis") if prior else None

        if prior is None:
            final_status = _STATUS_NEW
            status_change = "created"
        elif prior_status == new_status:
            final_status = _STATUS_UNCHANGED
            status_change = "no_change"
        elif new_status in (_STATUS_ACTIVE, _STATUS_STRENGTHENED) and prior_status in (
            _STATUS_WATCHLIST, _STATUS_WEAKENED
        ):
            final_status = _STATUS_STRENGTHENED
            status_change = "upgraded"
        elif new_status in (_STATUS_WEAKENED, _STATUS_CROWDED) and prior_status in (
            _STATUS_ACTIVE, _STATUS_STRENGTHENED
        ):
            final_status = _STATUS_WEAKENED
            status_change = "downgraded"
        else:
            final_status = new_status
            status_change = f"changed_from_{prior_status}_to_{new_status}"

        # Evidence — from activation record only (deterministic, not invented)
        evidence: list[str] = list(theme.get("evidence") or [])
        if not evidence:
            evidence.append("no_local_evidence_found")

        # Affected symbols — from feed
        affected_symbols = feed_by_theme.get(theme_id, [])
        candidate_route_hints = route_hints_by_theme.get(theme_id, [])

        # Thesis text — deterministic template
        current_thesis = _build_thesis_text(
            theme_id=theme_id,
            state=activation_state,
            drivers=theme.get("active_drivers") or [],
            rules=theme.get("transmission_rules_fired") or [],
            candidate_count=theme.get("candidate_count", 0),
            candidates_in_shadow=theme.get("candidates_in_shadow_count", 0),
            risk_flags=theme.get("risk_flags") or [],
            confirmation_requirements=theme.get("confirmation_requirements") or [],
        )

        changed_by: list[str] = []
        if status_change not in ("no_change", "created"):
            changed_by = theme.get("active_drivers") or ["local_shadow_inference"]

        theses.append({
            "theme_id": theme_id,
            "current_thesis": current_thesis,
            "previous_thesis": prior_thesis_text,
            "status": final_status,
            "previous_status": prior_status,
            "status_change": status_change,
            "changed_by": changed_by,
            "evidence": evidence,
            "confidence": theme.get("confidence", 0.0),
            "horizon": theme.get("horizon", ""),
            "affected_symbols": affected_symbols,
            "candidate_route_hints": candidate_route_hints,
            "invalidation": theme.get("invalidation_rules") or [],
            "confirmation_required": theme.get("confirmation_requirements") or [],
            "risk_flags": theme.get("risk_flags") or [],
            "last_updated": now,
            "freshness_status": freshness,
            "source_label": "intelligence_first_static_rule",
            "used_live_data": False,
            "used_raw_news": False,
            "used_llm": False,
        })

    return theses


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

def generate_thesis_store(
    output_path: str = _OUTPUT_PATH,
    prior_path: str | None = None,
) -> dict:
    """
    Build thesis store from local files. Compare against prior store if available.
    No live API calls. No broker. No .env. No LLM. No raw news.
    live_output_changed = false.
    """
    if prior_path is None:
        prior_path = _OUTPUT_PATH  # compare against existing output if present

    input_paths = [_ACTIVATION_PATH, _CONTEXT_PATH, _FEED_PATH, _SHADOW_PATH]

    source_files: list[str] = []
    unavailable_sources: list[str] = []
    loaded: dict[str, Any] = {}

    for path in input_paths:
        rel = os.path.relpath(path, _BASE)
        data, err = _read_json(path)
        if err:
            unavailable_sources.append(rel)
            loaded[path] = None
        else:
            source_files.append(rel)
            loaded[path] = data

    # Load prior thesis store for comparison (if it exists and differs from output)
    prior_theses: dict[str, dict] | None = None
    if os.path.exists(prior_path):
        try:
            prior_data, _ = _read_json(prior_path)
            if isinstance(prior_data, dict):
                prior_theses = {
                    t["theme_id"]: t
                    for t in (prior_data.get("theses") or [])
                    if isinstance(t, dict) and t.get("theme_id")
                }
        except Exception:
            prior_theses = None

    theses = _build_theses(
        activation_data=loaded.get(_ACTIVATION_PATH),
        feed_data=loaded.get(_FEED_PATH),
        shadow_data=loaded.get(_SHADOW_PATH),
        prior_theses=prior_theses,
    )

    # thesis_summary
    by_status: dict[str, int] = {}
    for t in theses:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1

    low_confidence_count = sum(1 for t in theses if t["confidence"] < 0.20)
    evidence_limited_count = sum(
        1 for t in theses if "no_local_evidence_found" in (t.get("evidence") or [])
    )

    thesis_summary: dict[str, Any] = {
        "total_theses": len(theses),
        "new": by_status.get(_STATUS_NEW, 0),
        "active": by_status.get(_STATUS_ACTIVE, 0),
        "strengthened": by_status.get(_STATUS_STRENGTHENED, 0),
        "weakened": by_status.get(_STATUS_WEAKENED, 0),
        "crowded": by_status.get(_STATUS_CROWDED, 0),
        "invalidated": by_status.get(_STATUS_INVALIDATED, 0),
        "unchanged": by_status.get(_STATUS_UNCHANGED, 0),
        "watchlist": by_status.get(_STATUS_WATCHLIST, 0),
        "low_confidence_count": low_confidence_count,
        "evidence_limited_count": evidence_limited_count,
        "no_live_api_called": _NO_LIVE_API_CALLED,
        "live_output_changed": _LIVE_OUTPUT_CHANGED,
    }

    warnings: list[str] = []
    if unavailable_sources:
        warnings.append(
            f"Sprint 4B: {len(unavailable_sources)} source file(s) unavailable. "
            f"Missing: {unavailable_sources}"
        )
    warnings.append(
        "Sprint 4B: thesis store built from local shadow files only using deterministic "
        "template — no LLM. Thesis store cannot create symbols, trades, or executable "
        "candidates. Not a trade signal."
    )

    output: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": _now_iso(),
        "valid_for_session": _today(),
        "mode": "shadow_thesis_store",
        "data_source_mode": "local_shadow_outputs_only",
        "source_files": source_files,
        "unavailable_sources": unavailable_sources,
        "warnings": warnings,
        "thesis_summary": thesis_summary,
        "theses": theses,
        "no_live_api_called": _NO_LIVE_API_CALLED,
        "broker_called": _BROKER_CALLED,
        "env_inspected": _ENV_INSPECTED,
        "raw_news_used": _RAW_NEWS_USED,
        "llm_used": _LLM_USED,
        "broad_intraday_scan_used": _BROAD_INTRADAY_SCAN_USED,
        "live_output_changed": _LIVE_OUTPUT_CHANGED,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    return output


# ---------------------------------------------------------------------------
# ThesisStore — importable reader class
# ---------------------------------------------------------------------------

class ThesisStore:
    """
    Read-only interface to a generated thesis_store.json.
    Does not write, does not call APIs, does not modify production files.
    """

    def __init__(self, theses: list[dict]) -> None:
        self._by_theme: dict[str, dict] = {t["theme_id"]: t for t in theses if t.get("theme_id")}
        self._all: list[dict] = theses

    @classmethod
    def load(cls, path: str = _OUTPUT_PATH) -> "ThesisStore":
        data, err = _read_json(path)
        if err or not isinstance(data, dict):
            return cls([])
        return cls(data.get("theses") or [])

    def get(self, theme_id: str) -> dict | None:
        return self._by_theme.get(theme_id)

    def all(self) -> list[dict]:
        return list(self._all)

    def active(self) -> list[dict]:
        return [t for t in self._all if t.get("status") in (_STATUS_ACTIVE, _STATUS_STRENGTHENED)]

    def count(self) -> int:
        return len(self._all)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = generate_thesis_store()
    s = result["thesis_summary"]
    print(f"thesis_store.json → {_OUTPUT_PATH}")
    print(f"  total_theses:    {s['total_theses']}")
    print(f"  new:             {s['new']}")
    print(f"  active:          {s['active']}")
    print(f"  strengthened:    {s['strengthened']}")
    print(f"  weakened:        {s['weakened']}")
    print(f"  watchlist:       {s['watchlist']}")
    print(f"  unchanged:       {s['unchanged']}")
    print(f"  low_confidence:  {s['low_confidence_count']}")
    print(f"  evidence_limited:{s['evidence_limited_count']}")
    print(f"  live_output_changed: {result['live_output_changed']}")
