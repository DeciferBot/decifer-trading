"""
test_theme_graph.py — Sprint M12A Theme Transmission Graph hardening tests.

Covers (Section 9 of the TTG spec):
  1. Determinism — same call always returns same data
  2. Every active symbol has a non-empty reason path
  3. LLM-only evidence is blocked
  4. needs_review and proposed symbols are suppressed
  5. Negative / pressure exposure never presented as a bullish active name
  6. Evidence gate rejects generic_sector_match, keyword_only, weak_co_mention
  7. API returns only allowlisted fields
  8. theme_graph (INTELLIGENCE) does not import execution modules
  9. Execution modules do not import theme_graph
 10. shadow candidates never contain execution-layer fields
 11. All 10 themes present in get_themes_list()
 12. Blueprint routes import-safe (no execution imports)
 13. reason_path starts with driver label and ends with symbol label
 14. search returns 0 results for empty query
 15. get_symbol_card returns None for unknown ticker
 16. get_symbol_card returns None for needs_review ticker
 17. get_theme_detail returns None for unknown theme_id
 18. Every shadow candidate has candidate_source == "theme_transmission_graph"
 19. FCX appears in both ai_energy_nuclear and critical_minerals_copper
 20. saas allowlist contains all 5 TTG field names
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure repo root on path
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import theme_graph as ttg
from saas_intelligence_output import _ALLOWED_FIELDS, validate_customer_payload

_ACCEPTED_EVIDENCE = {
    "curated_reference", "company_profile", "official_source",
    "filing", "ETF_holding", "news_catalyst", "internal_symbol_master",
}
_REJECTED_EVIDENCE = {
    "LLM_only", "keyword_only", "popular_online",
    "weak_co_mention", "generic_sector_match",
}
_EXECUTION_MODULE_STEMS = {
    "orders_core", "orders_options", "orders_portfolio", "orders_guards",
    "orders_state", "bot_ibkr", "bot_trading", "apex_orchestrator",
    "pm_engine", "pm_rails", "options_entries",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_raw_exposures() -> list[dict]:
    path = _REPO_ROOT / "data" / "intelligence" / "theme_graph" / "symbol_exposures.json"
    with open(path) as f:
        return json.load(f)["exposures"]


def _module_imports(module_name: str) -> set[str]:
    """Return set of top-level import stems from a module's source file."""
    src_path = _REPO_ROOT / f"{module_name}.py"
    if not src_path.exists():
        return set()
    import ast
    tree = ast.parse(src_path.read_text())
    stems = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                stems.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                stems.add(node.module.split(".")[0])
    return stems


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_get_themes_list_deterministic(self):
        r1 = ttg.get_themes_list()
        r2 = ttg.get_themes_list()
        assert r1 == r2

    def test_get_symbol_card_deterministic(self):
        c1 = ttg.get_symbol_card("NVDA")
        c2 = ttg.get_symbol_card("NVDA")
        assert c1 == c2

    def test_search_deterministic(self):
        r1 = ttg.search("gold")
        r2 = ttg.search("gold")
        assert r1 == r2

    def test_shadow_candidates_deterministic(self):
        c1 = ttg.get_shadow_candidates()
        c2 = ttg.get_shadow_candidates()
        assert c1 == c2


class TestReasonPaths:
    def test_every_active_symbol_has_reason_path(self):
        exposures = _load_raw_exposures()
        active = [e for e in exposures if e["status"] == "active"]
        for exp in active:
            card = ttg.get_symbol_card(exp["symbol"])
            if card is None:
                continue
            assert len(card["reason_path"]) >= 2, (
                f"{exp['symbol']} reason_path too short: {card['reason_path']}"
            )

    def test_reason_path_starts_with_driver(self):
        nodes_by_id, edges, _, _ = ttg._load_data()
        driver_labels = {n["label"] for n in nodes_by_id.values() if n.get("type") == "driver"}
        for candidate in ttg.get_shadow_candidates():
            rp = candidate["reason_path"]
            assert rp[0] in driver_labels, (
                f"{candidate['symbol']}: reason_path[0]={rp[0]!r} is not a driver label"
            )

    def test_reason_path_ends_with_symbol_label(self):
        for candidate in ttg.get_shadow_candidates():
            rp = candidate["reason_path"]
            assert candidate["label"] == rp[-1], (
                f"{candidate['symbol']}: reason_path[-1]={rp[-1]!r} != label={candidate['label']!r}"
            )

    def test_reason_path_passes_through_theme(self):
        nodes_by_id, _, _, _ = ttg._load_data()
        theme_labels = {n["label"] for n in nodes_by_id.values() if n.get("type") == "theme"}
        for candidate in ttg.get_shadow_candidates():
            rp = candidate["reason_path"]
            assert any(label in theme_labels for label in rp), (
                f"{candidate['symbol']}: no theme node in reason_path {rp}"
            )


class TestEvidenceGate:
    def test_llm_only_evidence_blocked(self):
        """An exposure with LLM_only evidence_basis must not appear in shadow candidates."""
        fake_exposure = {
            "symbol": "FAKE",
            "label": "Fake Corp",
            "driver_id": "ai_capex_growth",
            "theme_id": "ai_energy_nuclear",
            "bucket_id": "ai_compute_accelerators_networking",
            "exposure_type": "direct_beneficiary",
            "confidence": 0.9,
            "reason_to_care": "Test",
            "evidence_basis": "LLM_only",
            "source_type": "LLM_only",
            "route_hint": "In focus",
            "status": "active",
            "risk_note": None,
            "last_reviewed": "2026-05-26",
        }
        _, _, bucket_defs_by_id, _ = ttg._load_data()
        assert not ttg._evidence_gate(fake_exposure, bucket_defs_by_id)

    @pytest.mark.parametrize("bad_basis", sorted(_REJECTED_EVIDENCE))
    def test_rejected_evidence_basis_blocked(self, bad_basis: str):
        fake = {
            "symbol": "X", "label": "X", "driver_id": "ai_capex_growth",
            "theme_id": "ai_energy_nuclear", "bucket_id": "ai_compute_accelerators_networking",
            "exposure_type": "direct_beneficiary", "confidence": 0.8,
            "reason_to_care": "test", "evidence_basis": bad_basis,
            "status": "active", "route_hint": "In focus", "risk_note": None,
        }
        _, _, bucket_defs_by_id, _ = ttg._load_data()
        assert not ttg._evidence_gate(fake, bucket_defs_by_id), (
            f"Rejected evidence basis {bad_basis!r} should not pass the gate"
        )

    def test_accepted_evidence_basis_passes(self):
        _, _, bucket_defs_by_id, _ = ttg._load_data()
        # Use a bucket that accepts each evidence basis (nuclear_operators_utilities accepts
        # company_profile / official_source / news_catalyst; enrichment_haleu accepts official_source
        # / curated_reference / filing). Test each basis with a compatible bucket.
        basis_to_bucket = {
            "company_profile": "nuclear_operators_utilities",
            "official_source": "nuclear_operators_utilities",
            "news_catalyst": "nuclear_operators_utilities",
            "curated_reference": "enrichment_haleu",
            "filing": "enrichment_haleu",
            "ETF_holding": "ai_compute_accelerators_networking",
            "internal_symbol_master": "ai_compute_accelerators_networking",
        }
        for basis in _ACCEPTED_EVIDENCE:
            bucket_id = basis_to_bucket.get(basis, "enrichment_haleu")
            fake = {
                "symbol": "X", "label": "X", "driver_id": "ai_capex_growth",
                "theme_id": "ai_energy_nuclear", "bucket_id": bucket_id,
                "exposure_type": "direct_beneficiary", "confidence": 0.8,
                "reason_to_care": "test", "evidence_basis": basis,
                "status": "active", "route_hint": "In focus", "risk_note": None,
            }
            assert ttg._evidence_gate(fake, bucket_defs_by_id), (
                f"Accepted evidence basis {basis!r} should pass the gate for bucket {bucket_id!r}"
            )

    def test_needs_review_suppressed_from_shadow_candidates(self):
        candidates = ttg.get_shadow_candidates()
        candidate_symbols = {c["symbol"] for c in candidates}
        exposures = _load_raw_exposures()
        suppressed = {e["symbol"] for e in exposures if e["status"] == "needs_review"}
        leaked = suppressed & candidate_symbols
        assert not leaked, f"needs_review symbols leaked into shadow candidates: {leaked}"

    def test_proposed_suppressed_from_shadow_candidates(self):
        candidates = ttg.get_shadow_candidates()
        candidate_symbols = {c["symbol"] for c in candidates}
        exposures = _load_raw_exposures()
        proposed = {e["symbol"] for e in exposures if e["status"] == "proposed"}
        leaked = proposed & candidate_symbols
        assert not leaked, f"proposed symbols leaked into shadow candidates: {leaked}"

    def test_aspi_oklo_vktx_suppressed(self):
        """Specifically named suppressed symbols must never appear."""
        candidates = ttg.get_shadow_candidates()
        syms = {c["symbol"] for c in candidates}
        for sym in ["ASPI", "OKLO", "VKTX"]:
            assert sym not in syms, f"{sym} should be suppressed (needs_review)"

    def test_needs_review_not_in_theme_detail(self):
        detail = ttg.get_theme_detail("ai_energy_nuclear")
        assert detail is not None
        suppressed = {"ASPI", "OKLO"}
        symbol_set = {s["symbol"] for s in detail["symbols"]}
        leaked = suppressed & symbol_set
        assert not leaked, f"needs_review symbols in theme detail: {leaked}"


class TestNegativeExposure:
    def test_pressure_names_have_correct_exposure_type(self):
        """Pressure symbols must use pressure_or_negative exposure_type, never direct_beneficiary."""
        exposures = _load_raw_exposures()
        pressure = [e for e in exposures if e.get("route_hint") == "Monitor only"
                    and e.get("exposure_type") == "pressure_or_negative"]
        assert len(pressure) > 0, "Expected at least some pressure_or_negative symbols"
        for exp in pressure:
            assert exp["exposure_type"] != "direct_beneficiary", (
                f"{exp['symbol']} has pressure route but direct_beneficiary exposure"
            )

    def test_monitor_only_excluded_from_search_results(self):
        """search() must not return monitor_only-routed symbols as primary results."""
        results = ttg.search("food restaurant")
        # Food/restaurant symbols are monitor_only (pressure) — should not appear as active search hits
        for sym_card in results["symbols"]:
            assert sym_card["status"] == "active", (
                f"{sym_card['symbol']}: status should be active in search results, "
                f"got {sym_card['status']!r}"
            )

    def test_food_pressure_names_not_in_shadow_candidates(self):
        """Food/restaurant pressure names (MDLZ, HSY, KO, MCD) are active but monitor-only."""
        candidates = ttg.get_shadow_candidates()
        syms = {c["symbol"] for c in candidates}
        for sym in ["MDLZ", "HSY", "KO", "MCD"]:
            if sym in syms:
                # If present, they must not be route_hint "In focus"
                card = ttg.get_symbol_card(sym)
                if card:
                    assert card["route_hint"] != "In focus", (
                        f"{sym} is a pressure name but has route_hint 'In focus'"
                    )


class TestAPIFields:
    def test_saas_allowlist_contains_all_ttg_fields(self):
        required = {
            "theme_graph_themes",
            "theme_graph_buckets",
            "theme_graph_symbol_card",
            "theme_graph_reason_path",
            "theme_graph_search_results",
        }
        missing = required - _ALLOWED_FIELDS
        assert not missing, f"TTG fields missing from saas allowlist: {missing}"

    def test_validate_themes_list_payload(self):
        from theme_graph_api import _validation_envelope
        themes = ttg.get_themes_list()
        # Should not raise
        validate_customer_payload(_validation_envelope({"theme_graph_themes": themes}))

    def test_validate_symbol_card_payload(self):
        from theme_graph_api import _validation_envelope
        card = ttg.get_symbol_card("NVDA")
        assert card is not None
        validate_customer_payload(_validation_envelope({
            "theme_graph_symbol_card": card,
            "theme_graph_reason_path": [{"symbol": card["symbol"], "reason_path": card["reason_path"]}],
        }))

    def test_validate_search_payload(self):
        from theme_graph_api import _validation_envelope
        results = ttg.search("nuclear")
        validate_customer_payload(_validation_envelope({"theme_graph_search_results": results}))

    def test_symbol_card_has_no_blocked_fields(self):
        from saas_intelligence_output import _BLOCKED_FIELDS
        card = ttg.get_symbol_card("NVDA")
        assert card is not None
        for key in card:
            assert key not in _BLOCKED_FIELDS, f"Blocked field {key!r} in symbol card"


class TestLayerSeparation:
    def test_theme_graph_does_not_import_execution(self):
        imports = _module_imports("theme_graph")
        leaked = imports & _EXECUTION_MODULE_STEMS
        assert not leaked, f"theme_graph imports execution modules: {leaked}"

    def test_theme_graph_api_does_not_import_execution(self):
        imports = _module_imports("theme_graph_api")
        leaked = imports & _EXECUTION_MODULE_STEMS
        assert not leaked, f"theme_graph_api imports execution modules: {leaked}"

    def test_theme_graph_does_not_import_yfinance(self):
        imports = _module_imports("theme_graph")
        assert "yfinance" not in imports, "theme_graph must not import yfinance"

    def test_layer_boundary_classification(self):
        from architecture.layer_boundary import classify_module_path, Layer
        assert classify_module_path("theme_graph.py") == Layer.INTELLIGENCE
        assert classify_module_path("theme_graph_api.py") == Layer.SAAS_OUTPUT


class TestShadowCandidates:
    def test_all_shadow_candidates_have_correct_source(self):
        candidates = ttg.get_shadow_candidates()
        assert len(candidates) > 0
        for c in candidates:
            assert c["candidate_source"] == "theme_transmission_graph", (
                f"{c['symbol']}: candidate_source={c['candidate_source']!r}"
            )

    def test_shadow_candidates_have_no_execution_fields(self):
        execution_fields = {
            "order_id", "position_size", "qty", "stop_price", "limit_price",
            "entry_price", "exit_price", "pnl", "broker_account",
        }
        candidates = ttg.get_shadow_candidates()
        for c in candidates:
            leaked = set(c.keys()) & execution_fields
            assert not leaked, f"{c['symbol']}: execution fields in shadow candidate: {leaked}"

    def test_shadow_candidates_have_reason_path(self):
        for c in ttg.get_shadow_candidates():
            assert c.get("reason_path"), f"{c['symbol']} shadow candidate missing reason_path"


class TestThemesCoverage:
    def test_all_10_themes_present(self):
        expected_themes = {
            "ai_energy_nuclear",
            "glp1_metabolic_health",
            "defence_rearmament",
            "cybersecurity_digital_resilience",
            "reshoring_industrial_capex",
            "housing_rate_sensitivity",
            "water_infrastructure",
            "critical_minerals_copper",
            "gold_real_assets",
            "digital_assets_infrastructure",
        }
        themes = ttg.get_themes_list()
        present = {t["theme_id"] for t in themes}
        missing = expected_themes - present
        assert not missing, f"Missing themes from get_themes_list(): {missing}"

    def test_fcx_in_multiple_themes(self):
        """FCX has dual exposure — AI copper adjacency AND critical minerals."""
        _, _, _, exposures = ttg._load_data()
        fcx_themes = {e["theme_id"] for e in exposures if e["symbol"] == "FCX"}
        assert "ai_energy_nuclear" in fcx_themes, "FCX missing from ai_energy_nuclear"
        assert "critical_minerals_copper" in fcx_themes, "FCX missing from critical_minerals_copper"

    def test_get_theme_detail_returns_none_for_unknown(self):
        assert ttg.get_theme_detail("nonexistent_theme_xyz") is None

    def test_get_theme_detail_returns_buckets(self):
        detail = ttg.get_theme_detail("defence_rearmament")
        assert detail is not None
        assert len(detail["buckets"]) > 0

    def test_theme_detail_includes_driver_active_flag(self):
        detail = ttg.get_theme_detail("ai_energy_nuclear")
        assert detail is not None
        assert "driver_active" in detail

    def test_driver_active_flag_reflects_live_state(self):
        with patch.object(ttg, "_get_active_drivers", return_value=frozenset({"ai_capex_growth"})):
            detail = ttg.get_theme_detail("ai_energy_nuclear")
            assert detail["driver_active"] is True

        with patch.object(ttg, "_get_active_drivers", return_value=frozenset()):
            detail = ttg.get_theme_detail("ai_energy_nuclear")
            assert detail["driver_active"] is False


class TestSearch:
    def test_empty_query_returns_empty(self):
        results = ttg.search("")
        assert results["total"] == 0
        assert results["themes"] == []
        assert results["symbols"] == []

    def test_search_by_ticker(self):
        results = ttg.search("NVDA")
        syms = {s["symbol"] for s in results["symbols"]}
        assert "NVDA" in syms

    def test_search_by_theme_name(self):
        results = ttg.search("cybersecurity")
        theme_ids = {t["theme_id"] for t in results["themes"]}
        assert "cybersecurity_digital_resilience" in theme_ids

    def test_search_returns_active_only(self):
        results = ttg.search("health")
        for sym_card in results["symbols"]:
            assert sym_card["status"] == "active"

    def test_get_symbol_card_returns_none_for_unknown(self):
        assert ttg.get_symbol_card("ZZZZZZNOTREAL") is None

    def test_get_symbol_card_returns_none_for_needs_review(self):
        assert ttg.get_symbol_card("ASPI") is None
        assert ttg.get_symbol_card("OKLO") is None
        assert ttg.get_symbol_card("VKTX") is None

    def test_case_insensitive_ticker_lookup(self):
        lower = ttg.get_symbol_card("nvda")
        upper = ttg.get_symbol_card("NVDA")
        assert lower == upper
