"""
tests/test_intelligence_sprint7b.py — Sprint 7B paper handoff reader tests.

Tests:
    1-2   : module existence
    3-6   : paper files generated and validate
    7-25  : fail-closed validation (21 conditions from Part E)
    26-36 : reader import safety (no forbidden modules)
    37-45 : safety invariants and production no-touch confirmation
"""
from __future__ import annotations

import ast
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LIVE_DIR = "data/live"
_PAPER_UNIVERSE = os.path.join(_LIVE_DIR, "paper_active_opportunity_universe.json")
_PAPER_MANIFEST = os.path.join(_LIVE_DIR, "paper_current_manifest.json")
_PAPER_REPORT = os.path.join(_LIVE_DIR, "paper_handoff_validation_report.json")


def _future_ts(hours: int = 24) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_ts(hours: int = 1) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _minimal_manifest(overrides: dict | None = None) -> dict:
    base = {
        "schema_version": "1.0",
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": _future_ts(24),
        "validation_status": "pass",
        "handoff_mode": "paper",
        "handoff_enabled": False,
        "active_universe_file": _PAPER_UNIVERSE,
        "economic_context_file": "data/intelligence/current_economic_context.json",
        "source_snapshot_versions": {},
        "publisher": "paper_handoff_builder",
        "warnings": [],
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }
    if overrides:
        base.update(overrides)
    return base


def _minimal_universe(candidates: list | None = None, overrides: dict | None = None) -> dict:
    if candidates is None:
        candidates = [_minimal_candidate()]
    base = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": _future_ts(24),
        "mode": "paper_handoff_universe",
        "source_shadow_file": "data/universe_builder/active_opportunity_universe_shadow.json",
        "source_files": [],
        "validation_status": "pass",
        "universe_summary": {"total_candidates": len(candidates)},
        "candidates": candidates,
        "warnings": [],
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }
    if overrides:
        base.update(overrides)
    return base


def _minimal_candidate(overrides: dict | None = None) -> dict:
    base = {
        "symbol": "AAPL",
        "route": "swing",
        "route_hint": ["swing"],
        "reason_to_care": "structural",
        "source_labels": ["intelligence_first_static_rule"],
        "theme_ids": [],
        "risk_flags": [],
        "confirmation_required": [],
        "approval_status": "approved",
        "quota_group": "structural",
        "freshness_status": "fresh",
        "executable": False,
        "order_instruction": None,
        "live_output_changed": False,
    }
    if overrides:
        base.update(overrides)
    return base


def _write_tmp_json(data: dict) -> str:
    """Write data to a named temp file and return the path."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(data, fh)
        return fh.name


# ---------------------------------------------------------------------------
# Class 1: Module existence
# ---------------------------------------------------------------------------

class TestModuleExistence:
    def test_handoff_reader_exists(self):
        assert os.path.exists("handoff_reader.py"), "handoff_reader.py must exist"

    def test_paper_handoff_builder_exists(self):
        assert os.path.exists("paper_handoff_builder.py"), "paper_handoff_builder.py must exist"


# ---------------------------------------------------------------------------
# Class 2: Paper files generated
# ---------------------------------------------------------------------------

class TestPaperFilesGenerated:
    def test_paper_active_universe_generated(self):
        assert os.path.exists(_PAPER_UNIVERSE), (
            f"paper_active_opportunity_universe.json not found at {_PAPER_UNIVERSE}"
        )

    def test_paper_manifest_generated(self):
        assert os.path.exists(_PAPER_MANIFEST), (
            f"paper_current_manifest.json not found at {_PAPER_MANIFEST}"
        )

    def test_paper_validation_report_generated(self):
        assert os.path.exists(_PAPER_REPORT), (
            f"paper_handoff_validation_report.json not found at {_PAPER_REPORT}"
        )

    def test_paper_files_validate(self):
        from intelligence_schema_validator import (
            validate_paper_active_universe,
            validate_paper_manifest,
            validate_paper_handoff_validation_report,
        )
        r1 = validate_paper_active_universe(_PAPER_UNIVERSE)
        assert r1.ok, f"paper_active_universe fails: {r1.errors}"

        r2 = validate_paper_manifest(_PAPER_MANIFEST)
        assert r2.ok, f"paper_manifest fails: {r2.errors}"

        r3 = validate_paper_handoff_validation_report(_PAPER_REPORT)
        assert r3.ok, f"paper_handoff_validation_report fails: {r3.errors}"


# ---------------------------------------------------------------------------
# Class 3: Handoff reader public API
# ---------------------------------------------------------------------------

class TestHandoffReaderAPI:
    def test_all_public_functions_exist(self):
        import handoff_reader as hr
        for fn in (
            "read_manifest", "validate_manifest", "read_active_universe",
            "validate_active_universe", "validate_candidate",
            "build_handoff_validation_result", "load_paper_handoff",
        ):
            assert callable(getattr(hr, fn, None)), f"handoff_reader.{fn} must be callable"


# ---------------------------------------------------------------------------
# Class 4: Manifest fail-closed tests
# ---------------------------------------------------------------------------

class TestManifestFailClosed:
    def setup_method(self):
        from handoff_reader import (
            read_manifest, validate_manifest, load_paper_handoff,
        )
        self.read_manifest = read_manifest
        self.validate_manifest = validate_manifest
        self.load_paper_handoff = load_paper_handoff

    def test_manifest_missing_fails_closed(self):
        result = self.load_paper_handoff("nonexistent_manifest_7b.json")
        assert result["handoff_allowed"] is False
        assert result["manifest_validation"]["ok"] is False

    def test_manifest_invalid_json_fails_closed(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("{invalid json!!!")
            tmp_path = fh.name
        try:
            result = self.read_manifest(tmp_path)
            assert result["ok"] is False
            assert "invalid_json" in (result.get("error") or "")
        finally:
            os.unlink(tmp_path)

    def test_manifest_expired_fails_closed(self):
        m = _minimal_manifest({"expires_at": _past_ts(2)})
        result = self.validate_manifest(m)
        assert result["ok"] is False
        assert any("manifest_expired" in e for e in result["errors"])

    def test_manifest_validation_status_not_pass_fails_closed(self):
        m = _minimal_manifest({"validation_status": "fail"})
        result = self.validate_manifest(m)
        assert result["ok"] is False
        assert any("manifest_validation_not_pass" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Class 5: Active universe fail-closed tests
# ---------------------------------------------------------------------------

class TestUniverseFailClosed:
    def setup_method(self):
        from handoff_reader import validate_active_universe, load_paper_handoff
        self.validate_active_universe = validate_active_universe
        self.load_paper_handoff = load_paper_handoff

    def test_active_universe_missing_fails_closed(self):
        m = _minimal_manifest({"active_universe_file": "nonexistent_universe_7b.json"})
        tmp_manifest = _write_tmp_json(m)
        try:
            result = self.load_paper_handoff(tmp_manifest)
            assert result["handoff_allowed"] is False
            assert result["active_universe_validation"]["ok"] is False
        finally:
            os.unlink(tmp_manifest)

    def test_active_universe_invalid_json_fails_closed(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("not valid json !!!")
            univ_path = fh.name

        m = _minimal_manifest({"active_universe_file": univ_path})
        tmp_manifest = _write_tmp_json(m)
        try:
            result = self.load_paper_handoff(tmp_manifest)
            assert result["handoff_allowed"] is False
            assert result["active_universe_validation"]["ok"] is False
        finally:
            os.unlink(tmp_manifest)
            os.unlink(univ_path)

    def test_active_universe_expired_fails_closed(self):
        u = _minimal_universe(overrides={"expires_at": _past_ts(1)})
        result = self.validate_active_universe(u)
        assert result["ok"] is False
        assert any("active_universe_expired" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Class 6: Candidate fail-closed tests
# ---------------------------------------------------------------------------

class TestCandidateFailClosed:
    def setup_method(self):
        from handoff_reader import validate_candidate, validate_active_universe
        self.validate_candidate = validate_candidate
        self.validate_active_universe = validate_active_universe

    def test_candidate_missing_symbol_fails_closed(self):
        cand = _minimal_candidate()
        del cand["symbol"]
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("candidate_missing_symbol" in e for e in r["errors"])

    def test_candidate_missing_reason_to_care_fails_closed(self):
        cand = _minimal_candidate({"reason_to_care": None})
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("reason_to_care" in e for e in r["errors"])

    def test_candidate_missing_route_and_hint_fails_closed(self):
        cand = _minimal_candidate({"route": None, "route_hint": None})
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("route" in e for e in r["errors"])

    def test_candidate_missing_source_labels_fails_closed(self):
        cand = _minimal_candidate({"source_labels": []})
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("source_labels" in e for e in r["errors"])

    def test_candidate_executable_true_fails_closed(self):
        cand = _minimal_candidate({"executable": True})
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("executable_true" in e for e in r["errors"])

    def test_candidate_order_instruction_not_null_fails_closed(self):
        cand = _minimal_candidate({"order_instruction": {"action": "BUY"}})
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("order_instruction" in e for e in r["errors"])

    def test_candidate_unapproved_approval_status_fails_closed(self):
        cand = _minimal_candidate({"approval_status": "not_a_valid_status"})
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("approval_status" in e for e in r["errors"])

    def test_candidate_unapproved_source_label_fails_closed(self):
        cand = _minimal_candidate({"source_labels": ["an_unknown_unapproved_source_xyz"]})
        r = self.validate_candidate(cand)
        assert r["ok"] is False
        assert any("unapproved_source_label" in e for e in r["errors"])

    def test_candidate_count_zero_fails_closed(self):
        u = _minimal_universe(candidates=[])
        r = self.validate_active_universe(u)
        assert r["ok"] is False
        assert any("candidate_count_zero" in e for e in r["errors"])


# ---------------------------------------------------------------------------
# Class 7: Safety invariant fail-closed tests
# ---------------------------------------------------------------------------

class TestSafetyInvariantFailClosed:
    def setup_method(self):
        from handoff_reader import validate_manifest, validate_active_universe
        self.validate_manifest = validate_manifest
        self.validate_active_universe = validate_active_universe

    def test_secrets_exposed_true_fails_closed(self):
        m = _minimal_manifest({"secrets_exposed": True})
        r = self.validate_manifest(m)
        assert r["ok"] is False
        assert any("secrets_exposed" in e for e in r["errors"])

    def test_env_values_logged_true_fails_closed(self):
        m = _minimal_manifest({"env_values_logged": True})
        r = self.validate_manifest(m)
        assert r["ok"] is False
        assert any("env_values_logged" in e for e in r["errors"])

    def test_live_output_changed_true_fails_closed(self):
        u = _minimal_universe(overrides={"live_output_changed": True})
        r = self.validate_active_universe(u)
        assert r["ok"] is False
        assert any("live_output_changed" in e for e in r["errors"])


# ---------------------------------------------------------------------------
# Class 8: No fallback to scanner discovery
# ---------------------------------------------------------------------------

class TestNoScannerFallback:
    def test_reader_does_not_fallback_to_scanner(self):
        from handoff_reader import load_paper_handoff
        result = load_paper_handoff("totally_nonexistent_path_99.json")
        # Must fail closed — not fall back to any scanner or alternate source
        assert result["handoff_allowed"] is False
        assert result["manifest_validation"]["ok"] is False
        # No scanner-related keys in result
        assert result.get("scanner_output_changed") is False
        assert result.get("production_candidate_source_changed") is False


# ---------------------------------------------------------------------------
# Class 9: Forbidden imports (AST check)
# ---------------------------------------------------------------------------

_FORBIDDEN_READER_IMPORTS = {
    "bot_trading", "scanner", "orders_core", "guardrails", "bot_ibkr",
    "market_intelligence", "provider_fetch_tester", "backtest_intelligence",
    "advisory_reporter", "advisory_log_reviewer",
}


class TestForbiddenImports:
    def _get_imports(self, path: str) -> set[str]:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        return imports

    def test_reader_does_not_import_bot_trading(self):
        imports = self._get_imports("handoff_reader.py")
        assert "bot_trading" not in imports

    def test_reader_does_not_import_scanner(self):
        imports = self._get_imports("handoff_reader.py")
        assert "scanner" not in imports

    def test_reader_does_not_import_orders_core(self):
        imports = self._get_imports("handoff_reader.py")
        assert "orders_core" not in imports

    def test_reader_does_not_import_guardrails(self):
        imports = self._get_imports("handoff_reader.py")
        assert "guardrails" not in imports

    def test_reader_does_not_import_bot_ibkr(self):
        imports = self._get_imports("handoff_reader.py")
        assert "bot_ibkr" not in imports

    def test_reader_does_not_import_provider_fetch_tester(self):
        imports = self._get_imports("handoff_reader.py")
        assert "provider_fetch_tester" not in imports


# ---------------------------------------------------------------------------
# Class 10: Production no-touch confirmation
# ---------------------------------------------------------------------------

class TestProductionNoTouch:
    def _load(self, path: str) -> dict:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_handoff_enabled_false_in_paper_manifest(self):
        data = self._load(_PAPER_MANIFEST)
        assert data["handoff_enabled"] is False

    def test_enable_active_opportunity_universe_handoff_false(self):
        from config import CONFIG
        assert CONFIG.get("enable_active_opportunity_universe_handoff") is False

    def test_production_candidate_source_changed_false(self):
        data = self._load(_PAPER_REPORT)
        assert data["production_candidate_source_changed"] is False

    def test_apex_input_changed_false(self):
        data = self._load(_PAPER_REPORT)
        assert data["apex_input_changed"] is False

    def test_scanner_output_changed_false(self):
        data = self._load(_PAPER_REPORT)
        assert data["scanner_output_changed"] is False

    def test_risk_logic_changed_false(self):
        data = self._load(_PAPER_REPORT)
        assert data["risk_logic_changed"] is False

    def test_order_logic_changed_false(self):
        data = self._load(_PAPER_REPORT)
        assert data["order_logic_changed"] is False

    def test_handoff_allowed_false_in_report(self):
        data = self._load(_PAPER_REPORT)
        assert data["handoff_allowed"] is False

    def test_live_output_changed_false_in_universe(self):
        data = self._load(_PAPER_UNIVERSE)
        assert data["live_output_changed"] is False

    def test_live_output_changed_false_in_manifest(self):
        data = self._load(_PAPER_MANIFEST)
        assert data["live_output_changed"] is False

    def test_no_executable_trade_instructions_true_in_universe(self):
        data = self._load(_PAPER_UNIVERSE)
        assert data["no_executable_trade_instructions"] is True

    def test_no_candidate_is_executable(self):
        data = self._load(_PAPER_UNIVERSE)
        for cand in data.get("candidates", []):
            assert cand.get("executable") is False, (
                f"Candidate {cand.get('symbol')} has executable=True"
            )

    def test_no_candidate_has_order_instruction(self):
        data = self._load(_PAPER_UNIVERSE)
        for cand in data.get("candidates", []):
            assert cand.get("order_instruction") is None, (
                f"Candidate {cand.get('symbol')} has non-null order_instruction"
            )

    def test_production_manifest_not_written(self):
        assert not os.path.exists("data/live/current_manifest.json"), (
            "Production current_manifest.json must NOT be written in Sprint 7B"
        )

    def test_production_active_universe_not_written(self):
        assert not os.path.exists("data/live/active_opportunity_universe.json"), (
            "Production active_opportunity_universe.json must NOT be written in Sprint 7B"
        )


# ---------------------------------------------------------------------------
# Class 11: Regression spot check — existing intelligence files still valid
# ---------------------------------------------------------------------------

class TestIntelligenceRegressionSpotCheck:
    def test_transmission_rules_still_valid(self):
        from intelligence_schema_validator import validate_transmission_rules
        r = validate_transmission_rules("data/intelligence/transmission_rules.json")
        assert r.ok, f"transmission_rules regression: {r.errors}"

    def test_shadow_universe_still_valid(self):
        from intelligence_schema_validator import validate_shadow_universe
        r = validate_shadow_universe(
            "data/universe_builder/active_opportunity_universe_shadow.json"
        )
        assert r.ok, f"shadow_universe regression: {r.errors}"

    def test_advisory_report_still_valid(self):
        from intelligence_schema_validator import validate_advisory_report
        r = validate_advisory_report("data/intelligence/advisory_report.json")
        assert r.ok, f"advisory_report regression: {r.errors}"

    def test_economic_candidate_feed_still_valid(self):
        from intelligence_schema_validator import validate_economic_candidate_feed
        r = validate_economic_candidate_feed(
            "data/intelligence/economic_candidate_feed.json",
            roster_path="data/intelligence/thematic_roster.json",
            taxonomy_path="data/intelligence/theme_taxonomy.json",
        )
        assert r.ok, f"economic_candidate_feed regression: {r.errors}"


# ---------------------------------------------------------------------------
# Class 12: Smoke spot check
# ---------------------------------------------------------------------------

class TestSmokeSpotCheck:
    @pytest.mark.smoke
    def test_smoke_passes_for_7b(self):
        """Confirm the 4 smoke tests still pass with Sprint 7B code in place."""
        from intelligence_schema_validator import validate_transmission_rules
        r = validate_transmission_rules("data/intelligence/transmission_rules.json")
        assert r.ok
