"""
Sprint 7A.1 — Reference Data Layer Tests (44 tests)

Tests cover:
  Group A: sector_schema.json (6 tests)
  Group B: symbol_master.json (10 tests)
  Group C: theme_overlay_map.json (9 tests)
  Group D: coverage_gap_review.json (9 tests)
  Group E: reference_data_builder safety invariants (6 tests)
  Integration: validate_all() (4 tests)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure repo root on path
_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

import intelligence_schema_validator as v
from intelligence_schema_validator import (
    validate_coverage_gap_review,
    validate_sector_schema,
    validate_symbol_master,
    validate_theme_overlay_map,
)
from reference_data_builder import (
    _build_coverage_gap_review,
    _build_sector_schema,
    _build_theme_overlay_map,
    _build_symbol_master,
    _collect_all_symbols,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_tmp(data: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    return path


def _minimal_sector_schema(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "generated_at": "2026-05-06T00:00:00+00:00",
        "source": "reference_data_builder",
        "sectors": [
            {"sector_id": f"sector_{i}", "sector_name": f"Sector {i}", "industries": ["ind_a"]}
            for i in range(11)
        ],
        "proxy_classifications": [
            {"classification_id": "etf_proxy", "description": "etf", "sub_types": []},
            {"classification_id": "index_proxy", "description": "index", "sub_types": []},
            {"classification_id": "commodity_proxy", "description": "commodity", "sub_types": []},
            {"classification_id": "crypto_proxy", "description": "crypto", "sub_types": []},
            {"classification_id": "volatility_proxy", "description": "volatility", "sub_types": []},
            {"classification_id": "macro_proxy", "description": "macro", "sub_types": []},
            {"classification_id": "unknown", "description": "unknown", "sub_types": []},
        ],
    }
    base.update(overrides)
    return base


def _minimal_symbol_master(num_symbols: int = 102, **overrides) -> dict:
    symbols = [
        {
            "symbol": f"SYM{i:04d}",
            "sector": "information_technology",
            "industry": "software",
            "classification_status": "classified_local",
            "approval_status": "approved",
            "sources": ["committed_universe"],
        }
        for i in range(num_symbols)
    ]
    base = {
        "schema_version": "1.0",
        "generated_at": "2026-05-06T00:00:00+00:00",
        "symbol_count": num_symbols,
        "favourites_used_as_discovery": False,
        "live_api_called": False,
        "llm_called": False,
        "env_inspected": False,
        "symbols": symbols,
    }
    base.update(overrides)
    return base


def _minimal_theme_overlay(num_themes: int = 82) -> dict:
    meta_ids = [
        "emerging_or_unclassified_theme",
        "scanner_only_attention",
        "event_driven_special_situation",
        "unknown_requires_provider_enrichment",
    ]
    themes = [
        {
            "theme_id": meta_ids[i] if i < len(meta_ids) else f"theme_{i}",
            "theme_name": f"Theme {i}",
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        }
        for i in range(num_themes)
    ]
    return {
        "schema_version": "1.0",
        "generated_at": "2026-05-06T00:00:00+00:00",
        "source": "reference_data_builder",
        "theme_count": num_themes,
        "themes": themes,
    }


def _minimal_coverage_gap(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "generated_at": "2026-05-06T00:00:00+00:00",
        "source": "reference_data_builder",
        "advisory_records_analysed": 5,
        "evidence_status": "partial_advisory_input",
        "required_input_missing": False,
        "recurring_missing_shadow_count": 1,
        "recurring_unsupported_current_count": 0,
        "live_api_called": False,
        "llm_called": False,
        "env_inspected": False,
        "recurring_missing_shadow": [
            {
                "symbol": "VRT",
                "occurrence_count": 5,
                "total_records": 5,
                "occurrence_rate": 1.0,
                "counter_type": "missing_shadow",
                "sector": "industrials",
                "industry": "power_management",
                "classification_status": "classified_local",
                "recommended_action": "add_to_approved_roster",
            }
        ],
        "recurring_unsupported_current": [],
    }
    base.update(overrides)
    return base


# ===========================================================================
# Group A — sector_schema.json (6 tests)
# ===========================================================================

class TestSectorSchema:
    def test_valid_schema_passes(self):
        path = _write_tmp(_minimal_sector_schema())
        r = validate_sector_schema(path)
        assert r.ok, r.errors

    def test_missing_sectors_key_fails(self):
        data = _minimal_sector_schema()
        del data["sectors"]
        path = _write_tmp(data)
        r = validate_sector_schema(path)
        assert not r.ok
        assert any("sectors" in e for e in r.errors)

    def test_fewer_than_10_sectors_fails(self):
        data = _minimal_sector_schema()
        data["sectors"] = data["sectors"][:5]
        path = _write_tmp(data)
        r = validate_sector_schema(path)
        assert not r.ok
        assert any("10" in e for e in r.errors)

    def test_sector_missing_industries_fails(self):
        data = _minimal_sector_schema()
        del data["sectors"][0]["industries"]
        path = _write_tmp(data)
        r = validate_sector_schema(path)
        assert not r.ok

    def test_missing_proxy_classifications_fails(self):
        data = _minimal_sector_schema()
        del data["proxy_classifications"]
        path = _write_tmp(data)
        r = validate_sector_schema(path)
        assert not r.ok

    def test_built_sector_schema_passes_validator(self):
        schema = _build_sector_schema()
        path = _write_tmp(schema)
        r = validate_sector_schema(path)
        assert r.ok, r.errors


# ===========================================================================
# Group B — symbol_master.json (10 tests)
# ===========================================================================

class TestSymbolMaster:
    def test_valid_symbol_master_passes(self):
        path = _write_tmp(_minimal_symbol_master())
        r = validate_symbol_master(path)
        assert r.ok, r.errors

    def test_favourites_used_as_discovery_true_fails(self):
        data = _minimal_symbol_master(favourites_used_as_discovery=True)
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok
        assert any("favourites_used_as_discovery" in e for e in r.errors)

    def test_live_api_called_true_fails(self):
        data = _minimal_symbol_master(live_api_called=True)
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok
        assert any("live_api_called" in e for e in r.errors)

    def test_llm_called_true_fails(self):
        data = _minimal_symbol_master(llm_called=True)
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok
        assert any("llm_called" in e for e in r.errors)

    def test_env_inspected_true_fails(self):
        data = _minimal_symbol_master(env_inspected=True)
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok
        assert any("env_inspected" in e for e in r.errors)

    def test_symbol_count_mismatch_fails(self):
        data = _minimal_symbol_master()
        data["symbol_count"] = 999  # override after creation
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok
        assert any("symbol_count" in e for e in r.errors)

    def test_invalid_classification_status_fails(self):
        data = _minimal_symbol_master()
        data["symbols"][0]["classification_status"] = "invalid_status"
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok

    def test_invalid_approval_status_fails(self):
        data = _minimal_symbol_master()
        data["symbols"][0]["approval_status"] = "not_a_real_status"
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok

    def test_sources_not_list_fails(self):
        data = _minimal_symbol_master()
        data["symbols"][0]["sources"] = "committed_universe"  # string not list
        path = _write_tmp(data)
        r = validate_symbol_master(path)
        assert not r.ok

    def test_built_symbol_master_passes_validator(self):
        # Need ≥100 symbols to pass the validator minimum threshold
        committed = [f"SYM{i:04d}" for i in range(110)]
        all_syms = _collect_all_symbols(committed, [], [], [], {}, [])
        master = _build_symbol_master(all_syms)
        path = _write_tmp(master)
        r = validate_symbol_master(path)
        assert r.ok, r.errors


# ===========================================================================
# Group C — theme_overlay_map.json (9 tests)
# ===========================================================================

class TestThemeOverlayMap:
    def test_valid_theme_map_passes(self):
        path = _write_tmp(_minimal_theme_overlay(82))
        r = validate_theme_overlay_map(path)
        assert r.ok, r.errors

    def test_fewer_than_80_themes_fails(self):
        path = _write_tmp(_minimal_theme_overlay(79))
        r = validate_theme_overlay_map(path)
        assert not r.ok
        assert any("80" in e for e in r.errors)

    def test_theme_count_mismatch_fails(self):
        data = _minimal_theme_overlay(82)
        data["theme_count"] = 50
        path = _write_tmp(data)
        r = validate_theme_overlay_map(path)
        assert not r.ok
        assert any("theme_count" in e for e in r.errors)

    def test_missing_meta_overlay_fails(self):
        data = _minimal_theme_overlay(82)
        # Remove the scanner_only_attention theme
        data["themes"] = [
            t for t in data["themes"] if t["theme_id"] != "scanner_only_attention"
        ]
        data["theme_count"] = len(data["themes"])
        path = _write_tmp(data)
        r = validate_theme_overlay_map(path)
        assert not r.ok
        assert any("scanner_only_attention" in e for e in r.errors)

    def test_all_four_meta_overlays_required(self):
        for meta_id in [
            "emerging_or_unclassified_theme",
            "scanner_only_attention",
            "event_driven_special_situation",
            "unknown_requires_provider_enrichment",
        ]:
            data = _minimal_theme_overlay(82)
            data["themes"] = [t for t in data["themes"] if t["theme_id"] != meta_id]
            data["theme_count"] = len(data["themes"])
            path = _write_tmp(data)
            r = validate_theme_overlay_map(path)
            assert not r.ok, f"Expected failure when {meta_id} is missing"
            assert any(meta_id in e for e in r.errors)

    def test_canonical_symbols_not_list_fails(self):
        data = _minimal_theme_overlay(82)
        data["themes"][0]["canonical_symbols"] = "AAPL"  # string not list
        path = _write_tmp(data)
        r = validate_theme_overlay_map(path)
        assert not r.ok

    def test_duplicate_theme_id_fails(self):
        data = _minimal_theme_overlay(82)
        data["themes"].append({
            "theme_id": data["themes"][5]["theme_id"],  # duplicate
            "theme_name": "duplicate",
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        })
        data["theme_count"] = len(data["themes"])
        path = _write_tmp(data)
        r = validate_theme_overlay_map(path)
        assert not r.ok
        assert any("duplicate" in e for e in r.errors)

    def test_missing_themes_key_fails(self):
        data = _minimal_theme_overlay(82)
        del data["themes"]
        path = _write_tmp(data)
        r = validate_theme_overlay_map(path)
        assert not r.ok

    def test_built_theme_overlay_passes_validator(self):
        overlay = _build_theme_overlay_map()
        path = _write_tmp(overlay)
        r = validate_theme_overlay_map(path)
        assert r.ok, r.errors


# ===========================================================================
# Group D — coverage_gap_review.json (7 tests)
# ===========================================================================

class TestCoverageGapReview:
    def test_valid_coverage_gap_passes(self):
        path = _write_tmp(_minimal_coverage_gap())
        r = validate_coverage_gap_review(path)
        assert r.ok, r.errors

    def test_live_api_called_true_fails(self):
        data = _minimal_coverage_gap(live_api_called=True)
        path = _write_tmp(data)
        r = validate_coverage_gap_review(path)
        assert not r.ok
        assert any("live_api_called" in e for e in r.errors)

    def test_llm_called_true_fails(self):
        data = _minimal_coverage_gap(llm_called=True)
        path = _write_tmp(data)
        r = validate_coverage_gap_review(path)
        assert not r.ok

    def test_count_mismatch_missing_shadow_fails(self):
        data = _minimal_coverage_gap(recurring_missing_shadow_count=99)
        path = _write_tmp(data)
        r = validate_coverage_gap_review(path)
        assert not r.ok
        assert any("recurring_missing_shadow_count" in e for e in r.errors)

    def test_invalid_recommended_action_fails(self):
        data = _minimal_coverage_gap()
        data["recurring_missing_shadow"][0]["recommended_action"] = "do_something_weird"
        path = _write_tmp(data)
        r = validate_coverage_gap_review(path)
        assert not r.ok

    def test_entry_missing_symbol_fails(self):
        data = _minimal_coverage_gap()
        del data["recurring_missing_shadow"][0]["symbol"]
        path = _write_tmp(data)
        r = validate_coverage_gap_review(path)
        assert not r.ok

    def test_invalid_evidence_status_fails(self):
        data = _minimal_coverage_gap(evidence_status="bad_value")
        path = _write_tmp(data)
        r = validate_coverage_gap_review(path)
        assert not r.ok
        assert any("evidence_status" in e for e in r.errors)

    def test_required_input_missing_not_bool_fails(self):
        data = _minimal_coverage_gap(required_input_missing="maybe")
        path = _write_tmp(data)
        r = validate_coverage_gap_review(path)
        assert not r.ok
        assert any("required_input_missing" in e for e in r.errors)

    def test_built_coverage_gap_passes_validator(self):
        advisory_records = [
            {
                "missing_shadow_candidates": {"total": 2, "symbols": ["VRT", "ETN"]},
                "candidate_matches": [
                    {"symbol": "NVDA", "advisory_status": "advisory_unresolved", "in_current": True, "in_shadow": False},
                ],
            }
        ]
        all_syms = _collect_all_symbols(["VRT", "ETN", "NVDA"], [], [], [], {}, [])
        master = _build_symbol_master(all_syms)
        gap = _build_coverage_gap_review(advisory_records, master)
        path = _write_tmp(gap)
        r = validate_coverage_gap_review(path)
        assert r.ok, r.errors


# ===========================================================================
# Group E — reference_data_builder safety invariants (6 tests)
# ===========================================================================

class TestBuilderSafetyInvariants:
    def test_symbol_master_favourites_used_as_discovery_is_false(self):
        all_syms = _collect_all_symbols(
            [], [], [],
            favourites=["AAPL", "NVDA"],  # favourites present
            thematic={}, shadow=[],
        )
        master = _build_symbol_master(all_syms)
        assert master["favourites_used_as_discovery"] is False

    def test_symbol_master_live_api_called_is_false(self):
        all_syms = _collect_all_symbols(["AAPL"], [], [], [], {}, [])
        master = _build_symbol_master(all_syms)
        assert master["live_api_called"] is False

    def test_symbol_master_llm_called_is_false(self):
        all_syms = _collect_all_symbols(["AAPL"], [], [], [], {}, [])
        master = _build_symbol_master(all_syms)
        assert master["llm_called"] is False

    def test_symbol_master_env_inspected_is_false(self):
        all_syms = _collect_all_symbols(["AAPL"], [], [], [], {}, [])
        master = _build_symbol_master(all_syms)
        assert master["env_inspected"] is False

    def test_coverage_gap_review_live_api_called_is_false(self):
        gap = _build_coverage_gap_review([], {"symbols": []})
        assert gap["live_api_called"] is False

    def test_favourites_labelled_reference_only_not_discovery(self):
        """Favourites must be tagged 'favourites_reference_only', never a discovery label."""
        all_syms = _collect_all_symbols(
            [], [], [],
            favourites=["AAPL"],
            thematic={}, shadow=[],
        )
        assert "AAPL" in all_syms
        sources = all_syms["AAPL"]
        assert "favourites_reference_only" in sources
        # Must not appear as a discovery source
        assert "favourites" not in sources
        assert "manual_conviction" not in sources
        assert "committed_universe" not in sources


# ===========================================================================
# Integration: validate_all() includes the 4 new files
# ===========================================================================

class TestValidateAllIntegration:
    def test_validate_all_includes_sector_schema(self):
        results = v.validate_all()
        assert "sector_schema" in results
        assert results["sector_schema"].ok, results["sector_schema"].errors

    def test_validate_all_includes_symbol_master(self):
        results = v.validate_all()
        assert "symbol_master" in results
        assert results["symbol_master"].ok, results["symbol_master"].errors

    def test_validate_all_includes_theme_overlay_map(self):
        results = v.validate_all()
        assert "theme_overlay_map" in results
        assert results["theme_overlay_map"].ok, results["theme_overlay_map"].errors

    def test_validate_all_includes_coverage_gap_review(self):
        results = v.validate_all()
        assert "coverage_gap_review" in results
        assert results["coverage_gap_review"].ok, results["coverage_gap_review"].errors
