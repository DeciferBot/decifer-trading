"""
tests/test_intelligence_sprint4b.py — Sprint 4B acceptance tests.

Covers:
  - theme_activation_engine.py generates theme_activation.json
  - thesis_store.py generates thesis_store.json
  - Schema validation of both files
  - Forbidden paths (no live API, broker, env, LLM, raw news, scan)
  - Local inference: data_centre_power and semiconductors activated by ai_capex_growth
  - small_caps headwind → weakening or watchlist
  - quality_cash_flow and defensive_quality → watchlist or activated under local evidence
  - First run creates new theses; second run tracks status
  - Thesis evidence is deterministic (not invented)
  - Theme activation cannot create symbols or executable candidates
  - universe_builder_report.json includes economic_context_summary
  - Prior suite regression pass
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intelligence_schema_validator import validate_theme_activation, validate_thesis_store
from theme_activation_engine import generate_theme_activation
from thesis_store import generate_thesis_store, ThesisStore

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INTEL_DIR = os.path.join(_REPO, "data", "intelligence")
_UB_DIR = os.path.join(_REPO, "data", "universe_builder")

_ACTIVATION_PATH = os.path.join(_INTEL_DIR, "theme_activation.json")
_THESIS_PATH = os.path.join(_INTEL_DIR, "thesis_store.json")
_REPORT_PATH = os.path.join(_UB_DIR, "universe_builder_report.json")
_SHADOW_PATH = os.path.join(_UB_DIR, "active_opportunity_universe_shadow.json")
_FEED_PATH = os.path.join(_INTEL_DIR, "economic_candidate_feed.json")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. File generation
# ---------------------------------------------------------------------------

class TestThemeActivationGeneration:

    def test_theme_activation_json_exists(self):
        assert os.path.exists(_ACTIVATION_PATH), \
            "theme_activation.json not found — run python3 theme_activation_engine.py"

    def test_thesis_store_json_exists(self):
        assert os.path.exists(_THESIS_PATH), \
            "thesis_store.json not found — run python3 thesis_store.py"


# ---------------------------------------------------------------------------
# 2. theme_activation.json schema
# ---------------------------------------------------------------------------

class TestThemeActivationSchema:

    def test_validates_without_errors(self):
        result = validate_theme_activation(_ACTIVATION_PATH)
        assert result.ok, f"theme_activation validation failed: {result.errors}"

    def test_required_top_keys_present(self):
        data = _load(_ACTIVATION_PATH)
        for key in (
            "schema_version", "generated_at", "mode", "data_source_mode",
            "source_files", "activation_summary", "themes",
            "no_live_api_called", "live_output_changed",
        ):
            assert key in data, f"theme_activation missing key: {key}"

    def test_themes_list_non_empty(self):
        data = _load(_ACTIVATION_PATH)
        assert len(data["themes"]) > 0, "theme_activation.themes must not be empty"

    def test_all_theme_records_have_required_fields(self):
        data = _load(_ACTIVATION_PATH)
        required = {
            "theme_id", "state", "direction", "confidence",
            "evidence", "confirmation_requirements", "risk_flags",
            "invalidation_rules", "freshness_status", "route_bias",
            "candidate_count", "candidates_in_shadow_count",
            "candidates_excluded_count", "source_label",
        }
        for theme in data["themes"]:
            missing = required - set(theme.keys())
            assert not missing, f"Theme '{theme.get('theme_id')}' missing fields: {missing}"

    def test_all_theme_states_are_valid(self):
        data = _load(_ACTIVATION_PATH)
        valid_states = {
            "activated", "strengthening", "watchlist",
            "weakening", "crowded", "invalidated", "dormant",
        }
        for theme in data["themes"]:
            assert theme["state"] in valid_states, \
                f"Theme '{theme.get('theme_id')}' has invalid state: {theme.get('state')}"

    def test_activation_summary_has_required_fields(self):
        data = _load(_ACTIVATION_PATH)
        summary = data["activation_summary"]
        for key in (
            "total_themes", "activated", "strengthening", "watchlist",
            "weakening", "crowded", "invalidated", "dormant",
            "low_confidence_count", "evidence_limited_count",
            "no_live_api_called", "live_output_changed",
        ):
            assert key in summary, f"activation_summary missing key: {key}"


# ---------------------------------------------------------------------------
# 3. thesis_store.json schema
# ---------------------------------------------------------------------------

class TestThesisStoreSchema:

    def test_validates_without_errors(self):
        result = validate_thesis_store(_THESIS_PATH)
        assert result.ok, f"thesis_store validation failed: {result.errors}"

    def test_required_top_keys_present(self):
        data = _load(_THESIS_PATH)
        for key in (
            "schema_version", "generated_at", "mode", "data_source_mode",
            "source_files", "thesis_summary", "theses",
            "no_live_api_called", "live_output_changed",
        ):
            assert key in data, f"thesis_store missing key: {key}"

    def test_thesis_summary_has_required_fields(self):
        data = _load(_THESIS_PATH)
        summary = data["thesis_summary"]
        for key in (
            "total_theses", "new", "active", "strengthened", "weakened",
            "crowded", "invalidated", "unchanged", "watchlist",
            "low_confidence_count", "evidence_limited_count",
            "no_live_api_called", "live_output_changed",
        ):
            assert key in summary, f"thesis_summary missing key: {key}"

    def test_all_thesis_records_have_required_fields(self):
        data = _load(_THESIS_PATH)
        required = {
            "theme_id", "current_thesis", "status", "evidence",
            "confidence", "invalidation", "confirmation_required",
            "affected_symbols", "freshness_status", "source_label",
        }
        for thesis in data["theses"]:
            missing = required - set(thesis.keys())
            assert not missing, \
                f"Thesis '{thesis.get('theme_id')}' missing fields: {missing}"


# ---------------------------------------------------------------------------
# 4. Forbidden path checks
# ---------------------------------------------------------------------------

class TestForbiddenPathsSprint4B:

    def test_no_live_api_called_activation(self):
        assert _load(_ACTIVATION_PATH)["no_live_api_called"] is True

    def test_broker_called_false_activation(self):
        assert _load(_ACTIVATION_PATH)["broker_called"] is False

    def test_env_inspected_false_activation(self):
        assert _load(_ACTIVATION_PATH)["env_inspected"] is False

    def test_llm_used_false_activation(self):
        assert _load(_ACTIVATION_PATH)["llm_used"] is False

    def test_raw_news_used_false_activation(self):
        assert _load(_ACTIVATION_PATH)["raw_news_used"] is False

    def test_broad_intraday_scan_false_activation(self):
        assert _load(_ACTIVATION_PATH)["broad_intraday_scan_used"] is False

    def test_live_output_changed_false_activation(self):
        assert _load(_ACTIVATION_PATH)["live_output_changed"] is False

    def test_no_live_api_called_thesis(self):
        assert _load(_THESIS_PATH)["no_live_api_called"] is True

    def test_broker_called_false_thesis(self):
        assert _load(_THESIS_PATH)["broker_called"] is False

    def test_env_inspected_false_thesis(self):
        assert _load(_THESIS_PATH)["env_inspected"] is False

    def test_llm_used_false_thesis(self):
        assert _load(_THESIS_PATH)["llm_used"] is False

    def test_raw_news_used_false_thesis(self):
        assert _load(_THESIS_PATH)["raw_news_used"] is False

    def test_live_output_changed_false_thesis(self):
        assert _load(_THESIS_PATH)["live_output_changed"] is False


# ---------------------------------------------------------------------------
# 5. Theme activation content — fixture-style checks
# ---------------------------------------------------------------------------

class TestThemeActivationContent:

    def _theme(self, theme_id: str) -> dict:
        data = _load(_ACTIVATION_PATH)
        return next((t for t in data["themes"] if t["theme_id"] == theme_id), {})

    def test_data_centre_power_activated_or_strengthening(self):
        t = self._theme("data_centre_power")
        assert t.get("state") in ("activated", "strengthening", "crowded"), \
            f"data_centre_power expected activated/strengthening/crowded (ai_capex evidence), got {t.get('state')}"

    def test_semiconductors_activated_or_strengthening(self):
        t = self._theme("semiconductors")
        assert t.get("state") in ("activated", "strengthening", "crowded"), \
            f"semiconductors expected activated/strengthening/crowded (ai_capex evidence), got {t.get('state')}"

    def test_small_caps_weakening_or_watchlist(self):
        t = self._theme("small_caps")
        assert t.get("state") in ("weakening", "watchlist"), \
            f"small_caps expected weakening or watchlist (headwind), got {t.get('state')}"

    def test_small_caps_direction_is_headwind(self):
        t = self._theme("small_caps")
        assert t.get("direction") == "headwind", \
            f"small_caps direction expected headwind, got {t.get('direction')}"

    def test_quality_cash_flow_not_dormant(self):
        """quality_cash_flow has a rule + candidates — must not be dormant."""
        t = self._theme("quality_cash_flow")
        assert t.get("state") != "dormant", \
            f"quality_cash_flow should not be dormant — has credit stress rule + candidates"

    def test_defensive_quality_not_dormant(self):
        """defensive_quality has a rule + candidates — must not be dormant."""
        t = self._theme("defensive_quality")
        assert t.get("state") != "dormant", \
            f"defensive_quality should not be dormant — has risk_off rule + candidates"

    def test_theme_activation_creates_no_symbols(self):
        """theme_activation.json must not contain a 'candidates' list."""
        data = _load(_ACTIVATION_PATH)
        assert "candidates" not in data, \
            "theme_activation must not contain a candidates list"

    def test_per_theme_used_live_data_false(self):
        data = _load(_ACTIVATION_PATH)
        for theme in data["themes"]:
            assert theme.get("used_live_data") is False, \
                f"Theme '{theme.get('theme_id')}' claims used_live_data=True"

    def test_crowded_themes_have_quota_pressure(self):
        data = _load(_ACTIVATION_PATH)
        for theme in data["themes"]:
            if theme["state"] == "crowded":
                qp = theme.get("quota_pressure") or {}
                assert qp.get("structural_quota_binding") is True, \
                    f"Crowded theme '{theme.get('theme_id')}' must have structural_quota_binding=True"

    def test_evidence_list_non_empty_for_active_themes(self):
        data = _load(_ACTIVATION_PATH)
        for theme in data["themes"]:
            if theme["state"] in ("activated", "strengthening"):
                assert len(theme.get("evidence") or []) > 0, \
                    f"Active theme '{theme.get('theme_id')}' must have non-empty evidence"


# ---------------------------------------------------------------------------
# 6. Thesis store content
# ---------------------------------------------------------------------------

class TestThesisStoreContent:

    def _thesis(self, theme_id: str) -> dict:
        data = _load(_THESIS_PATH)
        return next((t for t in data["theses"] if t["theme_id"] == theme_id), {})

    def test_first_run_creates_new_theses(self):
        """On first run (no prior store), all theses are 'new'."""
        data = _load(_THESIS_PATH)
        new_count = data["thesis_summary"]["new"]
        total = data["thesis_summary"]["total_theses"]
        # First run: most/all should be new (unchanged is valid if prior existed)
        assert new_count > 0 or total == 0, \
            f"Expected new theses on first run, got new={new_count} of total={total}"

    def test_thesis_uses_deterministic_template(self):
        """current_thesis must contain the deterministic template pattern."""
        data = _load(_THESIS_PATH)
        for thesis in data["theses"]:
            ct = thesis.get("current_thesis", "")
            theme_id = thesis.get("theme_id", "")
            assert f"Theme {theme_id}" in ct, \
                f"Thesis for '{theme_id}' does not use deterministic template"
            assert "Confirmation still required:" in ct, \
                f"Thesis for '{theme_id}' missing 'Confirmation still required:' section"

    def test_thesis_does_not_claim_llm_generation(self):
        data = _load(_THESIS_PATH)
        for thesis in data["theses"]:
            assert thesis.get("used_llm") is False, \
                f"Thesis '{thesis.get('theme_id')}' claims used_llm=True"

    def test_thesis_invalidation_present(self):
        data = _load(_THESIS_PATH)
        themes_with_rules = {"data_centre_power", "semiconductors", "banks", "energy", "defence"}
        for thesis in data["theses"]:
            if thesis.get("theme_id") in themes_with_rules:
                assert isinstance(thesis.get("invalidation"), list), \
                    f"Thesis '{thesis.get('theme_id')}' must have invalidation list"

    def test_thesis_affected_symbols_from_feed(self):
        """affected_symbols in thesis must come from feed, not invented."""
        feed = _load(_FEED_PATH)
        feed_syms = {c["symbol"] for c in feed.get("candidates", [])}
        data = _load(_THESIS_PATH)
        for thesis in data["theses"]:
            for sym in (thesis.get("affected_symbols") or []):
                assert sym in feed_syms, \
                    f"Thesis symbol '{sym}' not in economic_candidate_feed — symbol invented"

    def test_thesis_store_creates_no_executable_candidates(self):
        """thesis_store.json must not contain execution_instructions."""
        data = _load(_THESIS_PATH)
        for thesis in data["theses"]:
            assert "execution_instructions" not in thesis, \
                f"Thesis '{thesis.get('theme_id')}' must not contain execution_instructions"


# ---------------------------------------------------------------------------
# 7. Second-run status comparison
# ---------------------------------------------------------------------------

class TestThesisStoreSecondRun:

    def test_second_run_detects_unchanged_status(self):
        """Running thesis_store generation again with same inputs yields 'unchanged' theses."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        try:
            # First run
            first_result = generate_thesis_store(output_path=tmp_path)
            # Second run reads first result as prior
            second_result = generate_thesis_store(output_path=tmp_path, prior_path=tmp_path)
            summary = second_result["thesis_summary"]
            # With same input, all statuses should be "unchanged"
            assert summary["unchanged"] > 0, \
                f"Second run should produce 'unchanged' theses, got: {summary}"
        finally:
            os.unlink(tmp_path)

    def test_thesis_store_reader_class(self):
        """ThesisStore class loads and exposes correct interface."""
        store = ThesisStore.load(_THESIS_PATH)
        assert store.count() > 0, "ThesisStore.count() must return > 0"
        # get() should return a thesis dict or None
        all_theses = store.all()
        assert len(all_theses) == store.count()
        if all_theses:
            first_theme_id = all_theses[0]["theme_id"]
            thesis = store.get(first_theme_id)
            assert thesis is not None
            assert thesis["theme_id"] == first_theme_id


# ---------------------------------------------------------------------------
# 8. Economic context integration in report
# ---------------------------------------------------------------------------

class TestEconomicContextSummaryInReport:

    def test_report_includes_economic_context_summary(self):
        report = _load(_REPORT_PATH)
        assert "economic_context_summary" in report, \
            "universe_builder_report.json must include 'economic_context_summary'"

    def test_economic_context_summary_has_required_fields(self):
        report = _load(_REPORT_PATH)
        ecs = report["economic_context_summary"]
        for key in (
            "daily_economic_state_available", "current_economic_context_available",
            "theme_activation_available", "thesis_store_available",
            "active_themes", "strengthening_themes", "weakening_themes",
            "watchlist_themes", "risk_posture", "regime_label",
            "thesis_count", "no_live_api_called", "live_output_changed",
        ):
            assert key in ecs, f"economic_context_summary missing key: {key}"

    def test_economic_context_summary_safety_flags(self):
        report = _load(_REPORT_PATH)
        ecs = report["economic_context_summary"]
        assert ecs["no_live_api_called"] is True
        assert ecs["live_output_changed"] is False

    def test_economic_context_does_not_affect_candidates(self):
        """Candidates in shadow universe must not be modified by economic_context_summary."""
        shadow = _load(_SHADOW_PATH)
        assert shadow["live_output_changed"] is False
        # Shadow universe source_files should not list intelligence engine outputs as mutators
        source_files = shadow.get("source_files") or []
        for sf in source_files:
            assert "theme_activation" not in sf
            assert "thesis_store" not in sf


# ---------------------------------------------------------------------------
# 9. Production module no-touch
# ---------------------------------------------------------------------------

class TestProductionNoTouch:

    def test_no_production_modules_modified(self):
        """
        Confirm theme_activation_engine.py and thesis_store.py do not import
        production modules that would trigger side effects.
        """
        import ast

        forbidden_imports = {
            "scanner", "bot_trading", "market_intelligence",
            "guardrails", "orders_core", "catalyst_engine",
        }
        sprint4b_files = [
            os.path.join(_REPO, "theme_activation_engine.py"),
            os.path.join(_REPO, "thesis_store.py"),
        ]
        for module_path in sprint4b_files:
            with open(module_path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else ([node.module] if node.module else [])
                    )
                    for name in names:
                        base = (name or "").split(".")[0]
                        assert base not in forbidden_imports, \
                            f"{os.path.basename(module_path)} imports forbidden module '{base}'"

    def test_no_env_handling_in_sprint4b_modules(self):
        """Confirm no os.environ or dotenv usage in Sprint 4B modules."""
        import ast

        sprint4b_files = [
            os.path.join(_REPO, "theme_activation_engine.py"),
            os.path.join(_REPO, "thesis_store.py"),
        ]
        for module_path in sprint4b_files:
            with open(module_path, encoding="utf-8") as f:
                source = f.read()
            assert "os.environ" not in source, \
                f"{os.path.basename(module_path)} uses os.environ — forbidden"
            assert "dotenv" not in source, \
                f"{os.path.basename(module_path)} uses dotenv — forbidden"
            assert "load_dotenv" not in source, \
                f"{os.path.basename(module_path)} uses load_dotenv — forbidden"


# ---------------------------------------------------------------------------
# 10. Prior suite regression
# ---------------------------------------------------------------------------

class TestPriorSuiteRegression:

    def test_feed_live_output_changed_still_false(self):
        assert _load(_FEED_PATH)["live_output_changed"] is False

    def test_shadow_universe_live_output_changed_still_false(self):
        assert _load(_SHADOW_PATH)["live_output_changed"] is False

    def test_report_live_output_changed_still_false(self):
        assert _load(_REPORT_PATH)["live_output_changed"] is False

    def test_shadow_universe_freshness_still_sprint3(self):
        shadow = _load(_SHADOW_PATH)
        assert shadow["freshness_status"] == "static_bootstrap_sprint3"
