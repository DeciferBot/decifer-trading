"""
tests/test_intelligence_day3.py

Day 3 acceptance tests for the Economic Candidate Feed:
  - candidate_resolver.py loads taxonomy and roster
  - resolve() produces the correct candidate set
  - Role classification, confidence decay, route_hint assignment
  - Validator accepts valid feed and rejects invalid variants
  - No LLM discovery, no raw news, no intraday scan, live_output_changed = False
  - Day 2 tests are not modified (they run in a separate file)
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

import pytest

# Allow imports from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from candidate_resolver import (
    CandidateResolver,
    CandidateFeed,
    generate_feed,
    resolve_candidates,
)
from intelligence_schema_validator import (
    validate_all,
    validate_economic_candidate_feed,
)

_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "intelligence")
_TAXONOMY_PATH = os.path.join(_BASE_DIR, "theme_taxonomy.json")
_ROSTER_PATH = os.path.join(_BASE_DIR, "thematic_roster.json")
_FEED_PATH = os.path.join(_BASE_DIR, "economic_candidate_feed.json")
_RULES_PATH = os.path.join(_BASE_DIR, "transmission_rules.json")

_DATA_CENTRE_POWER_RULE = "ai_capex_growth_to_data_centre_power"
_DATA_CENTRE_RULE_REASON = (
    "AI data-centre buildout increases demand for power equipment, cooling infrastructure, "
    "and grid capacity as hyperscalers expand compute footprint"
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def resolver():
    return CandidateResolver(taxonomy_path=_TAXONOMY_PATH, roster_path=_ROSTER_PATH)


@pytest.fixture()
def activated_data_centre():
    return {
        "data_centre_power": [_DATA_CENTRE_POWER_RULE],
    }


@pytest.fixture()
def fired_reasons():
    return {_DATA_CENTRE_POWER_RULE: _DATA_CENTRE_RULE_REASON}


@pytest.fixture()
def feed(activated_data_centre, fired_reasons):
    resolver = CandidateResolver(taxonomy_path=_TAXONOMY_PATH, roster_path=_ROSTER_PATH)
    return resolver.resolve(activated_data_centre, fired_rule_reasons=fired_reasons)


def _write_feed(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, f)
    f.close()
    return f.name


def _minimal_candidate(symbol: str = "VRT") -> dict:
    return {
        "symbol":                       symbol,
        "included_by":                  "economic_intelligence",
        "theme":                        "data_centre_power",
        "driver":                       "ai_capex_growth",
        "role":                         "direct_beneficiary",
        "reason":                       "test reason",
        "reason_to_care":               "test reason to care",
        "route_hint":                   ["position", "swing", "watchlist"],
        "confidence":                   0.82,
        "fresh_until":                  "2026-05-07T00:00:00Z",
        "risk_flags":                   ["valuation"],
        "confirmation_required":        ["sector_etf_relative_strength"],
        "source_labels":                ["intelligence_first_static_rule"],
        "transmission_rules_fired":     [_DATA_CENTRE_POWER_RULE],
        "market_confirmation_required": ["price_and_volume_confirmation_by_trading_bot"],
        "generated_at":                 "2026-05-05T00:00:00Z",
        "mode":                         "shadow_report_only",
        "live_output_changed":          False,
    }


def _minimal_feed(candidates: list | None = None) -> dict:
    return {
        "schema_version":    "1.0",
        "generated_at":      "2026-05-05T00:00:00Z",
        "fresh_until":       "2026-05-07T00:00:00Z",
        "mode":              "shadow_report_only",
        "source_files":      [_TAXONOMY_PATH, _ROSTER_PATH],
        "feed_summary": {
            "total_candidates":           1,
            "themes_active":              ["data_centre_power"],
            "drivers_active":             ["ai_capex_growth"],
            "direct_beneficiaries":       1,
            "second_order_beneficiaries": 0,
            "etf_proxies":                0,
            "watchlist_only":             0,
            "llm_symbol_discovery_used":  False,
            "raw_news_used":              False,
            "broad_intraday_scan_used":   False,
        },
        "candidates":        candidates if candidates is not None else [_minimal_candidate()],
        "warnings":          [],
        "live_output_changed": False,
    }


# ---------------------------------------------------------------------------
# Resolver loads taxonomy and roster
# ---------------------------------------------------------------------------

class TestResolverLoads:
    def test_taxonomy_loaded(self, resolver):
        assert "data_centre_power" in resolver._taxonomy

    def test_roster_loaded(self, resolver):
        assert "data_centre_power" in resolver._roster

    def test_no_load_errors(self, resolver):
        assert resolver._load_errors == []

    def test_missing_taxonomy_records_error(self):
        r = CandidateResolver(taxonomy_path="/nonexistent/taxonomy.json", roster_path=_ROSTER_PATH)
        assert len(r._load_errors) == 1

    def test_missing_roster_records_error(self):
        r = CandidateResolver(taxonomy_path=_TAXONOMY_PATH, roster_path="/nonexistent/roster.json")
        assert len(r._load_errors) == 1


# ---------------------------------------------------------------------------
# Resolver produces correct candidate set
# ---------------------------------------------------------------------------

class TestResolverProducesFeed:
    def test_produces_candidates(self, feed):
        assert len(feed.candidates) > 0

    def test_feed_contains_vrt(self, feed):
        symbols = {c.symbol for c in feed.candidates}
        assert "VRT" in symbols

    def test_feed_contains_etn(self, feed):
        symbols = {c.symbol for c in feed.candidates}
        assert "ETN" in symbols

    def test_feed_contains_pwr(self, feed):
        symbols = {c.symbol for c in feed.candidates}
        assert "PWR" in symbols

    def test_feed_contains_ceg(self, feed):
        symbols = {c.symbol for c in feed.candidates}
        assert "CEG" in symbols

    def test_xlu_included_as_etf_proxy(self, feed):
        """XLU should be included because it's in the roster's etf_proxies list."""
        xlu = next((c for c in feed.candidates if c.symbol == "XLU"), None)
        assert xlu is not None, "XLU should be present as an ETF proxy"
        assert xlu.role == "etf_proxy"

    def test_all_symbols_from_approved_roster(self, feed):
        with open(_ROSTER_PATH) as f:
            roster_data = json.load(f)
        approved = set()
        for entry in roster_data["rosters"]:
            approved.update(entry.get("core_symbols", []))
            approved.update(entry.get("etf_proxies", []))
        for c in feed.candidates:
            assert c.symbol in approved, f"{c.symbol} is not in any approved roster"

    def test_no_unknown_symbols(self, resolver):
        """An activated theme with no roster entry should produce no candidates and a warning."""
        feed = resolver.resolve({"no_such_theme": ["some_rule"]})
        assert len(feed.candidates) == 0
        assert any("no_such_theme" in w for w in feed.warnings)

    def test_inactive_theme_generates_no_candidates(self, resolver):
        feed = resolver.resolve({})
        assert len(feed.candidates) == 0


# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------

class TestRoleClassification:
    def test_vrt_is_direct_beneficiary(self, feed):
        vrt = next(c for c in feed.candidates if c.symbol == "VRT")
        assert vrt.role == "direct_beneficiary"

    def test_etn_is_direct_beneficiary(self, feed):
        etn = next(c for c in feed.candidates if c.symbol == "ETN")
        assert etn.role == "direct_beneficiary"

    def test_pwr_is_direct_beneficiary(self, feed):
        pwr = next(c for c in feed.candidates if c.symbol == "PWR")
        assert pwr.role == "direct_beneficiary"

    def test_ceg_is_second_order(self, feed):
        ceg = next(c for c in feed.candidates if c.symbol == "CEG")
        assert ceg.role == "second_order_beneficiary"

    def test_xlu_is_etf_proxy(self, feed):
        xlu = next(c for c in feed.candidates if c.symbol == "XLU")
        assert xlu.role == "etf_proxy"


# ---------------------------------------------------------------------------
# Confidence logic
# ---------------------------------------------------------------------------

class TestConfidenceLogic:
    def test_direct_beneficiary_keeps_base_confidence(self, feed):
        direct = [c for c in feed.candidates if c.role == "direct_beneficiary"]
        assert all(c.confidence == 0.82 for c in direct), \
            f"Direct beneficiary confidences: {[c.confidence for c in direct]}"

    def test_second_order_has_lower_confidence_than_direct(self, feed):
        direct = next(c for c in feed.candidates if c.role == "direct_beneficiary")
        second = next(c for c in feed.candidates if c.role == "second_order_beneficiary")
        assert second.confidence < direct.confidence

    def test_etf_proxy_has_lower_confidence_than_direct(self, feed):
        direct = next(c for c in feed.candidates if c.role == "direct_beneficiary")
        etf = next(c for c in feed.candidates if c.role == "etf_proxy")
        assert etf.confidence < direct.confidence

    def test_confidence_within_bounds(self, feed):
        for c in feed.candidates:
            assert 0.0 <= c.confidence <= 1.0, f"{c.symbol} confidence out of bounds: {c.confidence}"


# ---------------------------------------------------------------------------
# Route hints
# ---------------------------------------------------------------------------

class TestRouteHints:
    def test_direct_beneficiary_has_position_route(self, feed):
        direct = [c for c in feed.candidates if c.role == "direct_beneficiary"]
        for c in direct:
            assert "position" in c.route_hint or "swing" in c.route_hint, \
                f"{c.symbol} direct_beneficiary should have position or swing route_hint"

    def test_etf_proxy_is_watchlist_only(self, feed):
        xlu = next(c for c in feed.candidates if c.symbol == "XLU")
        assert xlu.route_hint == ["watchlist"]

    def test_second_order_has_no_position_route(self, feed):
        ceg = next(c for c in feed.candidates if c.symbol == "CEG")
        assert "position" not in ceg.route_hint


# ---------------------------------------------------------------------------
# No candidate is marked executable
# ---------------------------------------------------------------------------

class TestNoExecutable:
    def test_all_candidates_have_shadow_mode(self, feed):
        for c in feed.candidates:
            assert c.mode == "shadow_report_only", \
                f"{c.symbol} mode is '{c.mode}', expected 'shadow_report_only'"

    def test_all_candidates_live_output_changed_false(self, feed):
        for c in feed.candidates:
            assert c.live_output_changed is False, \
                f"{c.symbol} live_output_changed is True"


# ---------------------------------------------------------------------------
# Required fields present
# ---------------------------------------------------------------------------

class TestRequiredFields:
    def test_every_candidate_has_reason_to_care(self, feed):
        for c in feed.candidates:
            assert c.reason_to_care, f"{c.symbol} missing reason_to_care"

    def test_every_candidate_has_route_hint(self, feed):
        for c in feed.candidates:
            assert c.route_hint, f"{c.symbol} missing route_hint"

    def test_every_candidate_has_role(self, feed):
        for c in feed.candidates:
            assert c.role, f"{c.symbol} missing role"

    def test_every_candidate_has_confidence(self, feed):
        for c in feed.candidates:
            assert c.confidence is not None, f"{c.symbol} missing confidence"

    def test_every_candidate_has_confirmation_required(self, feed):
        for c in feed.candidates:
            assert c.confirmation_required, f"{c.symbol} missing confirmation_required"

    def test_every_candidate_has_risk_flags(self, feed):
        for c in feed.candidates:
            assert c.risk_flags, f"{c.symbol} missing risk_flags"

    def test_risk_flags_include_valuation(self, feed):
        for c in feed.candidates:
            assert "valuation" in c.risk_flags, f"{c.symbol} missing 'valuation' in risk_flags"


# ---------------------------------------------------------------------------
# Governance flags
# ---------------------------------------------------------------------------

class TestGovernanceFlags:
    def test_feed_summary_llm_discovery_false(self, feed):
        assert feed.feed_summary["llm_symbol_discovery_used"] is False

    def test_feed_summary_raw_news_false(self, feed):
        assert feed.feed_summary["raw_news_used"] is False

    def test_feed_summary_intraday_scan_false(self, feed):
        assert feed.feed_summary["broad_intraday_scan_used"] is False

    def test_feed_live_output_changed_false(self, feed):
        assert feed.live_output_changed is False


# ---------------------------------------------------------------------------
# Validator — valid feed passes
# ---------------------------------------------------------------------------

class TestValidatorPassesValidFeed:
    def test_generated_feed_passes_validator(self):
        assert os.path.exists(_FEED_PATH), "economic_candidate_feed.json not generated"
        result = validate_economic_candidate_feed(
            _FEED_PATH, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH
        )
        assert result.ok, f"Validator errors: {result.errors}"

    def test_validate_all_includes_feed(self):
        results = validate_all(_BASE_DIR)
        assert "economic_candidate_feed" in results
        assert results["economic_candidate_feed"].ok, \
            f"Feed validation failed: {results['economic_candidate_feed'].errors}"


# ---------------------------------------------------------------------------
# Validator — invalid feed variants rejected
# ---------------------------------------------------------------------------

class TestValidatorRejectsInvalidFeed:
    def test_missing_reason_to_care_fails(self):
        bad = _minimal_candidate()
        del bad["reason_to_care"]
        path = _write_feed(_minimal_feed([bad]))
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("reason_to_care" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_missing_route_hint_fails(self):
        bad = _minimal_candidate()
        del bad["route_hint"]
        path = _write_feed(_minimal_feed([bad]))
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("route_hint" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_invalid_route_hint_value_fails(self):
        bad = _minimal_candidate()
        bad["route_hint"] = ["not_a_real_route"]
        path = _write_feed(_minimal_feed([bad]))
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("not_a_real_route" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_missing_source_labels_fails(self):
        bad = _minimal_candidate()
        bad["source_labels"] = []
        path = _write_feed(_minimal_feed([bad]))
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("source_labels" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_confidence_above_one_fails(self):
        bad = _minimal_candidate()
        bad["confidence"] = 1.5
        path = _write_feed(_minimal_feed([bad]))
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("confidence" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_confidence_negative_fails(self):
        bad = _minimal_candidate()
        bad["confidence"] = -0.1
        path = _write_feed(_minimal_feed([bad]))
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("confidence" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_unknown_symbol_fails(self):
        bad = _minimal_candidate("TOTALLY_UNKNOWN_SYM")
        path = _write_feed(_minimal_feed([bad]))
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("TOTALLY_UNKNOWN_SYM" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_live_output_changed_true_fails(self):
        bad_feed = _minimal_feed()
        bad_feed["live_output_changed"] = True
        path = _write_feed(bad_feed)
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("live_output_changed" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_llm_symbol_discovery_true_fails(self):
        bad_feed = _minimal_feed()
        bad_feed["feed_summary"]["llm_symbol_discovery_used"] = True
        path = _write_feed(bad_feed)
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("llm_symbol_discovery_used" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_raw_news_used_true_fails(self):
        bad_feed = _minimal_feed()
        bad_feed["feed_summary"]["raw_news_used"] = True
        path = _write_feed(bad_feed)
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("raw_news_used" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_broad_intraday_scan_true_fails(self):
        bad_feed = _minimal_feed()
        bad_feed["feed_summary"]["broad_intraday_scan_used"] = True
        path = _write_feed(bad_feed)
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("broad_intraday_scan_used" in e for e in result.errors)
        finally:
            os.unlink(path)

    def test_wrong_mode_fails(self):
        bad_feed = _minimal_feed()
        bad_feed["mode"] = "production"
        path = _write_feed(bad_feed)
        try:
            result = validate_economic_candidate_feed(path, roster_path=_ROSTER_PATH, taxonomy_path=_TAXONOMY_PATH)
            assert not result.ok
            assert any("mode" in e for e in result.errors)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# generate_feed() writes output file
# ---------------------------------------------------------------------------

class TestGenerateFeed:
    def test_generate_feed_creates_output_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            feed = generate_feed(
                activated_themes={"data_centre_power": [_DATA_CENTRE_POWER_RULE]},
                output_path=tmp_path,
                taxonomy_path=_TAXONOMY_PATH,
                roster_path=_ROSTER_PATH,
                fired_rule_reasons={_DATA_CENTRE_POWER_RULE: _DATA_CENTRE_RULE_REASON},
            )
            assert os.path.exists(tmp_path)
            with open(tmp_path) as f:
                data = json.load(f)
            assert data["live_output_changed"] is False
            assert data["mode"] == "shadow_report_only"
            assert len(data["candidates"]) > 0
        finally:
            os.unlink(tmp_path)

    def test_generate_feed_default_driver_fires_data_centre_power(self):
        """generate_feed() without explicit themes should use the transmission matrix."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            feed = generate_feed(output_path=tmp_path, taxonomy_path=_TAXONOMY_PATH, roster_path=_ROSTER_PATH)
            assert len(feed.candidates) > 0
            themes = {c.theme for c in feed.candidates}
            assert "data_centre_power" in themes
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Day 2 regression — smoke check
# ---------------------------------------------------------------------------

class TestDay2RegressionSmoke:
    def test_validate_all_still_passes_for_day2_files(self):
        results = validate_all(_BASE_DIR)
        for label in ["transmission_rules", "theme_taxonomy", "thematic_roster"]:
            assert results[label].ok, f"Day 2 file regression: {label} now failing: {results[label].errors}"
