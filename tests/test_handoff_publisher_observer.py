"""
tests/test_handoff_publisher_observer.py — Sprint 7G / 7G.1: Validation-Only Live Observation

Covers:
  - handoff_publisher_observer.py module exists
  - observation report generated and validates
  - required analysis sections present
  - safety invariants hold (live_bot_consuming_handoff=false, live_output_changed=false, etc.)
  - readiness gate is valid
  - fail-closed behaviour
  - import safety (no forbidden modules)
  - Sprint 7F regression
  - smoke
  - run log gate logic (Sprint 7G.1)
"""
from __future__ import annotations

import ast
import glob
import json
import os
import sys
import tempfile
import time
import unittest.mock as mock

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OBSERVER_PATH = os.path.join(_ROOT, "handoff_publisher_observer.py")
_REPORT_PATH = os.path.join(_ROOT, "data", "live", "handoff_publisher_observation_report.json")
_MANIFEST_PATH = os.path.join(_ROOT, "data", "live", "current_manifest.json")
_UNIVERSE_PATH = os.path.join(_ROOT, "data", "live", "active_opportunity_universe.json")
_HEARTBEAT_PATH = os.path.join(_ROOT, "data", "heartbeats", "handoff_publisher.json")
_PUBLISHER_REPORT_PATH = os.path.join(_ROOT, "data", "live", "handoff_publisher_report.json")

_VALID_READINESS_GATES = {
    "insufficient_observation",
    "validation_only_stable",
    "validation_only_unstable",
    "fix_publisher_before_flag_activation",
    # Sprint 7J.3: controlled_activation mode gates
    "controlled_activation_ready",
    "controlled_activation_unstable",
}

# Note: handoff_enabled is intentionally excluded — it reflects the manifest state and may
# be True in controlled_activation mode. It is NOT an observer action invariant.
_SAFETY_FLAGS_MUST_BE_FALSE = [
    "live_bot_consuming_handoff",
    "enable_active_opportunity_universe_handoff",
    "production_candidate_source_changed",
    "scanner_output_changed",
    "apex_input_changed",
    "risk_logic_changed",
    "order_logic_changed",
    "live_output_changed",
]

_REQUIRED_SECTIONS = [
    "observation_summary",
    "freshness_analysis",
    "manifest_validity_analysis",
    "active_universe_validity_analysis",
    "heartbeat_analysis",
    "candidate_stability_analysis",
    "fail_closed_observations",
    "safety_analysis",
]

_FORBIDDEN_MODULES = {
    "orders_core", "bot_ibkr", "guardrails", "apex_orchestrator",
    "market_intelligence", "bot_trading", "ibkr_connection",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_report() -> dict:
    with open(_REPORT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _observer_imports() -> set[str]:
    with open(_OBSERVER_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    return imported


# ---------------------------------------------------------------------------
# Group 1 — module and report existence
# ---------------------------------------------------------------------------

class TestModuleAndReportExistence:
    def test_observer_module_exists(self):
        assert os.path.isfile(_OBSERVER_PATH), (
            "handoff_publisher_observer.py not found at repo root"
        )

    def test_observation_report_exists(self):
        assert os.path.isfile(_REPORT_PATH), (
            f"handoff_publisher_observation_report.json not found at {_REPORT_PATH}"
        )

    def test_observation_report_is_valid_json(self):
        report = _load_report()
        assert isinstance(report, dict)

    def test_observation_report_validates(self):
        from intelligence_schema_validator import validate_handoff_publisher_observation_report
        result = validate_handoff_publisher_observation_report(_REPORT_PATH)
        assert result.ok, f"Observation report validation failed: {result.errors}"


# ---------------------------------------------------------------------------
# Group 2 — mode and schema fields
# ---------------------------------------------------------------------------

class TestModeAndSchemaFields:
    def test_mode_correct(self):
        report = _load_report()
        assert report.get("mode") == "validation_only_handoff_publisher_observation"

    def test_schema_version_present(self):
        report = _load_report()
        assert "schema_version" in report

    def test_generated_at_present(self):
        report = _load_report()
        assert "generated_at" in report

    def test_publication_mode_correct(self):
        report = _load_report()
        assert report.get("publication_mode") == "validation_only"

    def test_source_files_is_list(self):
        report = _load_report()
        assert isinstance(report.get("source_files"), list)


# ---------------------------------------------------------------------------
# Group 3 — required analysis sections
# ---------------------------------------------------------------------------

class TestRequiredAnalysisSections:
    def test_observation_summary_exists(self):
        assert isinstance(_load_report().get("observation_summary"), dict)

    def test_freshness_analysis_exists(self):
        assert isinstance(_load_report().get("freshness_analysis"), dict)

    def test_manifest_validity_analysis_exists(self):
        assert isinstance(_load_report().get("manifest_validity_analysis"), dict)

    def test_active_universe_validity_analysis_exists(self):
        assert isinstance(_load_report().get("active_universe_validity_analysis"), dict)

    def test_heartbeat_analysis_exists(self):
        assert isinstance(_load_report().get("heartbeat_analysis"), dict)

    def test_candidate_stability_analysis_exists(self):
        assert isinstance(_load_report().get("candidate_stability_analysis"), dict)

    def test_fail_closed_observations_exists(self):
        assert isinstance(_load_report().get("fail_closed_observations"), dict)

    def test_safety_analysis_exists(self):
        assert isinstance(_load_report().get("safety_analysis"), dict)

    def test_warnings_is_list(self):
        assert isinstance(_load_report().get("warnings"), list)


# ---------------------------------------------------------------------------
# Group 4 — safety invariants (top-level flags)
# ---------------------------------------------------------------------------

class TestSafetyInvariants:
    def test_live_bot_consuming_handoff_false(self):
        assert _load_report().get("live_bot_consuming_handoff") is False

    def test_enable_active_opportunity_universe_handoff_false(self):
        assert _load_report().get("enable_active_opportunity_universe_handoff") is False

    def test_handoff_enabled_false(self):
        assert _load_report().get("handoff_enabled") is False

    def test_live_output_changed_false(self):
        assert _load_report().get("live_output_changed") is False

    def test_production_candidate_source_changed_false(self):
        assert _load_report().get("production_candidate_source_changed") is False

    def test_scanner_output_changed_false(self):
        assert _load_report().get("scanner_output_changed") is False

    def test_all_safety_invariants_hold(self):
        sa = _load_report().get("safety_analysis", {})
        assert sa.get("all_safety_invariants_hold") is True, (
            f"all_safety_invariants_hold is False: {sa}"
        )

    def test_all_safety_flags_false(self):
        report = _load_report()
        for flag in _SAFETY_FLAGS_MUST_BE_FALSE:
            assert report.get(flag) is False, (
                f"Safety flag {flag!r} must be False, got {report.get(flag)!r}"
            )


# ---------------------------------------------------------------------------
# Group 5 — readiness gate
# ---------------------------------------------------------------------------

class TestReadinessGate:
    def test_readiness_gate_present(self):
        assert "readiness_gate" in _load_report()

    def test_readiness_gate_is_valid(self):
        gate = _load_report().get("readiness_gate")
        assert gate in _VALID_READINESS_GATES, (
            f"readiness_gate {gate!r} not in valid set"
        )

    def test_readiness_gate_is_valid(self):
        # Sprint 7G: gate starts at insufficient_observation.
        # Sprint 7I/7J: gate advances to validation_only_stable once both thresholds
        # (successful_runs>=10, distinct_sessions>=3) are met under 75/35 quota policy.
        _valid_gates = {
            "insufficient_observation",
            "validation_only_stable",
            "validation_only_unstable",
            "fix_publisher_before_flag_activation",
            "ready_for_flag_activation_design",
        }
        gate = _load_report().get("readiness_gate")
        assert gate in _valid_gates, (
            f"readiness_gate {gate!r} not in valid set {_valid_gates}"
        )


# ---------------------------------------------------------------------------
# Group 6 — freshness analysis
# ---------------------------------------------------------------------------

class TestFreshnessAnalysis:
    def test_sla_thresholds_present(self):
        fa = _load_report().get("freshness_analysis", {})
        assert "sla_primary_threshold_seconds" in fa
        assert "sla_stale_threshold_seconds" in fa
        assert "sla_expired_threshold_seconds" in fa

    def test_sla_primary_is_600(self):
        fa = _load_report().get("freshness_analysis", {})
        assert fa.get("sla_primary_threshold_seconds") == 600

    def test_sla_stale_is_900(self):
        fa = _load_report().get("freshness_analysis", {})
        assert fa.get("sla_stale_threshold_seconds") == 900

    def test_sla_expired_is_1200(self):
        fa = _load_report().get("freshness_analysis", {})
        assert fa.get("sla_expired_threshold_seconds") == 1200

    def test_freshness_sla_met(self):
        # Immediately after publisher run, SLA must be met
        fa = _load_report().get("freshness_analysis", {})
        assert fa.get("sla_met") is True


# ---------------------------------------------------------------------------
# Group 7 — manifest and universe validity
# ---------------------------------------------------------------------------

class TestManifestAndUniverseValidity:
    def test_manifest_validity_passes(self):
        mv = _load_report().get("manifest_validity_analysis", {})
        assert mv.get("validation_status") == "pass", (
            f"manifest validation_status: {mv.get('validation_status')}"
        )

    def test_manifest_handoff_enabled_false(self):
        mv = _load_report().get("manifest_validity_analysis", {})
        assert mv.get("handoff_enabled") is False

    def test_universe_validity_passes(self):
        uv = _load_report().get("active_universe_validity_analysis", {})
        assert uv.get("validation_status") == "pass", (
            f"universe validation_status: {uv.get('validation_status')}"
        )

    def test_universe_no_executable_violations(self):
        uv = _load_report().get("active_universe_validity_analysis", {})
        assert uv.get("executable_violations") == [], (
            f"executable_violations: {uv.get('executable_violations')}"
        )


# ---------------------------------------------------------------------------
# Group 8 — fail-closed behaviour
# ---------------------------------------------------------------------------

class TestFailClosedBehaviour:
    def test_fail_closed_events_is_int(self):
        fc = _load_report().get("fail_closed_observations", {})
        assert isinstance(fc.get("fail_closed_events"), int)

    def test_fail_diagnostics_is_list(self):
        fc = _load_report().get("fail_closed_observations", {})
        assert isinstance(fc.get("fail_diagnostics_found"), list)

    def test_observer_run_on_missing_publisher_outputs(self):
        # Observer must not raise even when publisher outputs are missing
        import handoff_publisher_observer as hpo
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_paths = {
                "_MANIFEST_PATH": os.path.join(tmpdir, "current_manifest.json"),
                "_UNIVERSE_PATH": os.path.join(tmpdir, "active_opportunity_universe.json"),
                "_HEARTBEAT_PATH": os.path.join(tmpdir, "handoff_publisher.json"),
                "_PUBLISHER_REPORT_PATH": os.path.join(tmpdir, "handoff_publisher_report.json"),
                "_RUN_LOG_PATH": os.path.join(tmpdir, "publisher_run_log.jsonl"),
                "_OUTPUT_PATH": os.path.join(tmpdir, "observation_report.json"),
                "_FAIL_GLOB": os.path.join(tmpdir, ".fail_*.json"),
            }
            with mock.patch.multiple(hpo, **{k: v for k, v in fake_paths.items()}):
                hpo.run_observer()
            assert os.path.isfile(fake_paths["_OUTPUT_PATH"])
            with open(fake_paths["_OUTPUT_PATH"]) as f:
                data = json.load(f)
            assert data.get("readiness_gate") in _VALID_READINESS_GATES


# ---------------------------------------------------------------------------
# Group 9 — import safety
# ---------------------------------------------------------------------------

class TestImportSafety:
    def test_no_forbidden_modules_imported(self):
        imported = _observer_imports()
        violations = imported & _FORBIDDEN_MODULES
        assert not violations, (
            f"Observer imports forbidden modules: {violations}"
        )

    def test_no_broker_import(self):
        imported = _observer_imports()
        broker_modules = {m for m in imported if "ibkr" in m.lower() or "broker" in m.lower()}
        assert not broker_modules, f"Observer imports broker module: {broker_modules}"


# ---------------------------------------------------------------------------
# Group 10 — Sprint 7F regression
# ---------------------------------------------------------------------------

class TestSprint7FRegression:
    def test_publisher_report_still_valid(self):
        from intelligence_schema_validator import validate_handoff_publisher_report
        if not os.path.isfile(_PUBLISHER_REPORT_PATH):
            pytest.skip("handoff_publisher_report.json not present")
        result = validate_handoff_publisher_report(_PUBLISHER_REPORT_PATH)
        assert result.ok, f"Sprint 7F publisher report validation failed: {result.errors}"

    def test_heartbeat_still_valid(self):
        from intelligence_schema_validator import validate_handoff_publisher_heartbeat
        if not os.path.isfile(_HEARTBEAT_PATH):
            pytest.skip("handoff_publisher.json heartbeat not present")
        result = validate_handoff_publisher_heartbeat(_HEARTBEAT_PATH)
        assert result.ok, f"Sprint 7F heartbeat validation failed: {result.errors}"

    def test_prod_manifest_still_valid(self):
        from intelligence_schema_validator import validate_prod_manifest
        if not os.path.isfile(_MANIFEST_PATH):
            pytest.skip("current_manifest.json not present")
        result = validate_prod_manifest(_MANIFEST_PATH)
        assert result.ok, f"Sprint 7F prod manifest validation failed: {result.errors}"

    def test_prod_universe_still_valid(self):
        from intelligence_schema_validator import validate_prod_active_universe
        if not os.path.isfile(_UNIVERSE_PATH):
            pytest.skip("active_opportunity_universe.json not present")
        result = validate_prod_active_universe(_UNIVERSE_PATH)
        assert result.ok, f"Sprint 7F prod universe validation failed: {result.errors}"


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------

class TestSmokeSpotCheck:
    @pytest.mark.smoke
    def test_smoke_passes_for_7g(self):
        report = _load_report()
        assert report.get("mode") == "validation_only_handoff_publisher_observation"
        assert report.get("live_bot_consuming_handoff") is False
        assert report.get("live_output_changed") is False
        assert report.get("handoff_enabled") is False
        assert report.get("enable_active_opportunity_universe_handoff") is False
        assert report.get("readiness_gate") in _VALID_READINESS_GATES
        assert isinstance(report.get("observation_summary"), dict)
        assert isinstance(report.get("safety_analysis"), dict)
        sa = report["safety_analysis"]
        assert sa.get("all_safety_invariants_hold") is True


# ---------------------------------------------------------------------------
# Group 11 — Run log gate logic (Sprint 7G.1)
# ---------------------------------------------------------------------------

_RUN_LOG_PATH = os.path.join(_ROOT, "data", "live", "publisher_run_log.jsonl")


def _make_run_log_record(utc_date: str = "2026-05-07", n: int = 0) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": f"test-{utc_date}-{n:03d}",
        "worker": "handoff_publisher",
        "completed_at": f"{utc_date}T12:00:{n:02d}Z",
        "utc_date": utc_date,
        "validation_status": "pass",
        "publication_mode": "validation_only",
        "handoff_enabled": False,
        "enable_active_opportunity_universe_handoff": False,
        "active_universe_file": "data/live/active_opportunity_universe.json",
        "current_manifest_file": "data/live/current_manifest.json",
        "candidate_count": 10,
        "manifest_expires_at": f"{utc_date}T12:15:00Z",
        "freshness_status": "fresh",
        "source_shadow_file": "data/universe_builder/active_opportunity_universe_shadow.json",
        "safety_flags": {"live_output_changed": False, "secrets_exposed": False},
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }


def _write_run_log(path: str, records: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


class TestRunLogObservation:
    """Group 11: Sprint 7G.1 — run log gate logic in observer."""

    def _clean_analysis(self) -> tuple[dict, dict, dict, dict]:
        manifest = {"validation_status": "pass", "issue_count": 0, "handoff_enabled": False}
        universe = {"validation_status": "pass", "issue_count": 0}
        heartbeat = {"exists": True, "validation_status": "pass"}
        freshness = {"expired_count": 0, "sla_met": True}
        return manifest, universe, heartbeat, freshness

    # Test 11 — observer reads successful_publisher_runs from run log
    def test_observer_uses_run_log_not_heartbeat_for_run_count(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        records = [_make_run_log_record("2026-05-07", i) for i in range(5)]
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "publisher_run_log.jsonl")
            _write_run_log(log_path, records)
            with mock.patch.object(hpo, "_RUN_LOG_PATH", log_path):
                run_log = hpo._read_run_log()
        assert run_log["successful_runs"] == 5
        assert run_log["run_log_exists"] is True

    # Test 12 — observer counts distinct UTC sessions
    def test_observer_counts_distinct_utc_sessions(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        records = [
            _make_run_log_record("2026-05-05", 0),
            _make_run_log_record("2026-05-06", 0),
            _make_run_log_record("2026-05-07", 0),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "publisher_run_log.jsonl")
            _write_run_log(log_path, records)
            with mock.patch.object(hpo, "_RUN_LOG_PATH", log_path):
                run_log = hpo._read_run_log()
        assert run_log["distinct_sessions"] == 3

    # Test 13 — gate remains insufficient_observation below both thresholds
    def test_observer_gate_insufficient_observation_below_thresholds(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = {"successful_runs": 3, "distinct_sessions": 1, "run_log_exists": True}
        manifest, universe, heartbeat, freshness = self._clean_analysis()
        gate, threshold_met, basis = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, []
        )
        assert gate == "insufficient_observation"
        assert threshold_met is False
        assert basis == "not_met"

    # Test 14 — gate advances when successful_publisher_runs >= 10
    def test_observer_gate_advances_on_10_successful_runs(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = {"successful_runs": 10, "distinct_sessions": 1, "run_log_exists": True}
        manifest, universe, heartbeat, freshness = self._clean_analysis()
        gate, threshold_met, basis = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, []
        )
        assert gate != "insufficient_observation"
        assert threshold_met is True
        assert basis == "successful_runs"

    # Test 15 — gate advances when distinct_utc_sessions >= 3
    def test_observer_gate_advances_on_3_distinct_sessions(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = {"successful_runs": 3, "distinct_sessions": 3, "run_log_exists": True}
        manifest, universe, heartbeat, freshness = self._clean_analysis()
        gate, threshold_met, basis = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, []
        )
        assert gate != "insufficient_observation"
        assert threshold_met is True
        assert basis == "distinct_sessions"

    # Test 16 — gate does not advance if publisher has issues even above threshold
    def test_observer_does_not_advance_if_publisher_has_issues(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = {"successful_runs": 10, "distinct_sessions": 3, "run_log_exists": True}
        manifest = {"validation_status": "fail", "issue_count": 2, "handoff_enabled": False}
        _, universe, heartbeat, freshness = self._clean_analysis()
        gate, threshold_met, basis = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, []
        )
        assert gate == "fix_publisher_before_flag_activation"
        assert threshold_met is True

    # Test 17 — missing run log returns insufficient_observation
    def test_observer_handles_missing_run_log_as_insufficient_observation(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = os.path.join(tmpdir, "nonexistent_run_log.jsonl")
            with mock.patch.object(hpo, "_RUN_LOG_PATH", missing_path):
                run_log = hpo._read_run_log()
        assert run_log["run_log_exists"] is False
        assert run_log["successful_runs"] == 0
        assert run_log["distinct_sessions"] == 0
        # confirm that gate logic treats 0 runs / 0 sessions as insufficient
        manifest, universe, heartbeat, freshness = self._clean_analysis()
        gate, threshold_met, _ = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, []
        )
        assert gate == "insufficient_observation"
        assert threshold_met is False

    # Test 18 — heartbeat still reports latest run health
    def test_heartbeat_still_reports_latest_run_health(self):
        from intelligence_schema_validator import validate_handoff_publisher_heartbeat
        heartbeat_path = os.path.join(_ROOT, "data", "heartbeats", "handoff_publisher.json")
        if not os.path.isfile(heartbeat_path):
            pytest.skip("heartbeat not present")
        result = validate_handoff_publisher_heartbeat(heartbeat_path)
        assert result.ok, f"heartbeat validation failed: {result.errors}"

    # Test 19 — live bot does not read publisher_run_log
    def test_live_bot_does_not_read_run_log(self):
        forbidden_ref = "publisher_run_log"
        for fname in ("bot_trading.py", "scanner.py", "orders_core.py"):
            fpath = os.path.join(_ROOT, fname)
            if not os.path.isfile(fpath):
                continue
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            assert forbidden_ref not in content, (
                f"{fname} references {forbidden_ref!r} — live bot must not read run log"
            )

    # Test 20 — production_candidate_source_changed is false in observation report
    def test_no_production_candidate_source_changed(self):
        report = _load_report()
        assert report.get("production_candidate_source_changed") is False

    # Test 21 — live_output_changed is false in observation report
    def test_live_output_changed_is_false_in_report(self):
        report = _load_report()
        assert report.get("live_output_changed") is False


# ---------------------------------------------------------------------------
# Group 12 — Sprint 7J.3: Mode-aware observer (controlled_activation support)
# ---------------------------------------------------------------------------


def _make_min_valid_report(publication_mode: str, readiness_gate: str) -> dict:
    """Build a minimal but validator-compliant observation report for the given mode."""
    handoff_enabled = publication_mode == "controlled_activation"
    obs_summary = {
        "readiness_gate": readiness_gate,
        "successful_publisher_runs": 10,
        "distinct_utc_sessions": 3,
        "threshold_met": True,
        "threshold_basis": "distinct_sessions",
        "run_log_exists": True,
        "current_manifest_exists": True,
        "active_universe_exists": True,
        "heartbeat_exists": True,
        "publisher_report_exists": True,
    }
    return {
        "schema_version": "1.0",
        "generated_at": "2026-05-09T10:00:00Z",
        "mode": "validation_only_handoff_publisher_observation",
        "source_files": [],
        "observation_summary": obs_summary,
        "freshness_analysis": {},
        "manifest_validity_analysis": {},
        "active_universe_validity_analysis": {},
        "heartbeat_analysis": {},
        "candidate_stability_analysis": {},
        "fail_closed_observations": {},
        "safety_analysis": {"all_safety_invariants_hold": True},
        "readiness_gate": readiness_gate,
        "warnings": [],
        "handoff_enabled": handoff_enabled,
        "publication_mode": publication_mode,
        "live_bot_consuming_handoff": False,
        "enable_active_opportunity_universe_handoff": False,
        "live_output_changed": False,
        "production_candidate_source_changed": False,
        "scanner_output_changed": False,
        "apex_input_changed": False,
        "risk_logic_changed": False,
        "order_logic_changed": False,
    }


class TestModeAwareness:
    """Group 12 — Sprint 7J.3: Mode-aware observer (controlled_activation support)."""

    def _ca_manifest_analysis(self) -> dict:
        """A clean controlled_activation manifest analysis (no issues)."""
        return {
            "exists": True,
            "validation_status": "pass",
            "handoff_enabled": True,
            "publication_mode": "controlled_activation",
            "enable_flag_required": True,
            "ready_for_consumption": True,
            "active_universe_file": "data/live/active_opportunity_universe.json",
            "active_universe_file_exists": True,
            "publisher": "handoff_publisher",
            "safety_flags_clean": True,
            "issues": [],
            "issue_count": 0,
        }

    def _vo_manifest_analysis(self) -> dict:
        """A clean validation_only manifest analysis (no issues)."""
        return {
            "exists": True,
            "validation_status": "pass",
            "handoff_enabled": False,
            "publication_mode": "validation_only",
            "enable_flag_required": True,
            "ready_for_consumption": True,
            "active_universe_file": "data/live/active_opportunity_universe.json",
            "active_universe_file_exists": True,
            "publisher": "handoff_publisher",
            "safety_flags_clean": True,
            "issues": [],
            "issue_count": 0,
        }

    def _clean_universe(self) -> dict:
        return {
            "exists": True,
            "validation_status": "pass",
            "issue_count": 0,
            "candidate_count": 50,
            "executable_violations": [],
            "order_instruction_violations": [],
        }

    def _clean_heartbeat(self) -> dict:
        return {"exists": True, "validation_status": "pass"}

    def _clean_freshness(self) -> dict:
        return {"expired_count": 0, "sla_met": True}

    def _threshold_run_log(self) -> dict:
        """Run log with both thresholds met."""
        return {
            "successful_runs": 10,
            "distinct_sessions": 3,
            "run_log_exists": True,
            "run_log_records": 10,
            "successful_runs_for_current_quota": 10,
            "distinct_sessions_for_current_quota": 3,
        }

    def _write_manifest(self, tmpdir: str, publication_mode: str, handoff_enabled: bool) -> str:
        manifest_path = os.path.join(tmpdir, "current_manifest.json")
        universe_path = os.path.join(tmpdir, "universe.json")
        with open(universe_path, "w") as f:
            json.dump({}, f)
        manifest_data = {
            "validation_status": "pass",
            "publication_mode": publication_mode,
            "handoff_enabled": handoff_enabled,
            "enable_flag_required": True,
            "active_universe_file": universe_path,
            "live_output_changed": False,
            "secrets_exposed": False,
            "env_values_logged": False,
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest_data, f)
        return manifest_path

    # Test 1 — validation_only manifest with handoff_enabled=false is accepted
    def test_vo_manifest_handoff_false_accepted(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = self._write_manifest(tmpdir, "validation_only", False)
            with mock.patch.object(hpo, "_MANIFEST_PATH", manifest_path):
                warnings: list = []
                result = hpo._analyse_manifest(warnings, expected_mode="validation_only")
        assert result["issue_count"] == 0, f"Unexpected issues: {result['issues']}"
        assert result["handoff_enabled"] is False

    # Test 2 — validation_only manifest with handoff_enabled=true is rejected
    def test_vo_manifest_handoff_true_rejected(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = self._write_manifest(tmpdir, "validation_only", True)
            with mock.patch.object(hpo, "_MANIFEST_PATH", manifest_path):
                warnings: list = []
                result = hpo._analyse_manifest(warnings, expected_mode="validation_only")
        assert result["issue_count"] > 0
        assert any("handoff_enabled" in issue for issue in result["issues"])

    # Test 3 — controlled_activation manifest with handoff_enabled=true is accepted
    def test_ca_manifest_handoff_true_accepted(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = self._write_manifest(tmpdir, "controlled_activation", True)
            with mock.patch.object(hpo, "_MANIFEST_PATH", manifest_path):
                warnings: list = []
                result = hpo._analyse_manifest(warnings, expected_mode="controlled_activation")
        assert result["issue_count"] == 0, f"Unexpected issues: {result['issues']}"
        assert result["handoff_enabled"] is True

    # Test 4 — controlled_activation manifest with handoff_enabled=false is rejected
    def test_ca_manifest_handoff_false_rejected(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = self._write_manifest(tmpdir, "controlled_activation", False)
            with mock.patch.object(hpo, "_MANIFEST_PATH", manifest_path):
                warnings: list = []
                result = hpo._analyse_manifest(warnings, expected_mode="controlled_activation")
        assert result["issue_count"] > 0
        assert any("handoff_enabled" in issue for issue in result["issues"])

    # Test 5 — controlled_activation does not trigger fix_publisher_before_flag_activation
    def test_ca_mode_does_not_trigger_fix_publisher_gate(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = self._threshold_run_log()
        manifest = self._ca_manifest_analysis()
        universe = self._clean_universe()
        heartbeat = self._clean_heartbeat()
        freshness = self._clean_freshness()
        gate, threshold_met, _ = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, [],
            current_mode="controlled_activation",
        )
        assert gate != "fix_publisher_before_flag_activation", (
            f"controlled_activation mode with clean manifest triggered gate={gate!r}"
        )
        assert threshold_met is True

    # Test 6 — controlled_activation report includes mode_interpretation=controlled_activation_precheck
    def test_ca_mode_context_has_correct_interpretation(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        manifest_analysis = self._ca_manifest_analysis()
        universe_analysis = self._clean_universe()
        mode_context = hpo._build_mode_context(
            "controlled_activation", manifest_analysis, universe_analysis
        )
        assert mode_context["mode_interpretation"] == "controlled_activation_precheck"

    # Test 7 — controlled_activation report includes manifest_allows_handoff=true
    def test_ca_mode_context_manifest_allows_handoff_true(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        manifest_analysis = self._ca_manifest_analysis()
        universe_analysis = self._clean_universe()
        mode_context = hpo._build_mode_context(
            "controlled_activation", manifest_analysis, universe_analysis
        )
        assert mode_context["manifest_allows_handoff"] is True

    # Test 8 — controlled_activation report states bot flag disabled when config flag=False
    def test_ca_mode_context_reports_bot_flag_disabled(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        manifest_analysis = self._ca_manifest_analysis()
        universe_analysis = self._clean_universe()
        # config.enable_active_opportunity_universe_handoff is False in worktree
        mode_context = hpo._build_mode_context(
            "controlled_activation", manifest_analysis, universe_analysis
        )
        # manifest_allows_handoff=True but bot flag=False → consumption blocked
        assert mode_context["bot_consumption_allowed"] is False
        assert mode_context["bot_consumption_note"] == "manifest_ready_but_bot_flag_disabled"

    # Test 9 — unknown publication_mode is rejected by _get_current_mode
    def test_unknown_publication_mode_rejected_by_get_current_mode(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "current_manifest.json")
            with open(manifest_path, "w") as f:
                json.dump({"publication_mode": "not_a_valid_mode"}, f)
            with mock.patch.object(hpo, "_MANIFEST_PATH", manifest_path):
                mode = hpo._get_current_mode()
        assert mode == "unknown"

    # Test 10a — readiness_gate is valid for validation_only mode
    def test_readiness_gate_valid_for_validation_only_mode(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = self._threshold_run_log()
        manifest = self._vo_manifest_analysis()
        universe = self._clean_universe()
        heartbeat = self._clean_heartbeat()
        freshness = self._clean_freshness()
        gate, _, _ = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, [],
            current_mode="validation_only",
        )
        assert gate in _VALID_READINESS_GATES

    # Test 10b — readiness_gate is valid for controlled_activation mode
    def test_readiness_gate_valid_for_controlled_activation_mode(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = self._threshold_run_log()
        manifest = self._ca_manifest_analysis()
        universe = self._clean_universe()
        heartbeat = self._clean_heartbeat()
        freshness = self._clean_freshness()
        gate, _, _ = hpo._determine_readiness_gate(
            run_log, manifest, universe, heartbeat, freshness, [],
            current_mode="controlled_activation",
        )
        assert gate in _VALID_READINESS_GATES

    # Test 11 — live_output_changed is hardcoded False in _SAFETY (never changes by mode)
    def test_live_output_changed_always_false_in_safety(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        assert hpo._SAFETY.get("live_output_changed") is False

    # Test 12 — no broker calls in observer (static import check)
    def test_no_broker_calls_in_observer(self):
        imported = _observer_imports()
        broker_modules = {m for m in imported if "ibkr" in m.lower() or "broker" in m.lower()}
        assert not broker_modules, f"Observer imports broker module: {broker_modules}"

    # Test 13 — no LLM calls in observer
    def test_no_llm_calls_in_observer(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        assert hpo._SAFETY.get("llm_called") is False

    # Test 14 — no live bot consumption in observer
    def test_no_live_bot_consumption_in_observer(self):
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        assert hpo._SAFETY.get("live_bot_consuming_handoff") is False

    # Test 15 — validator accepts a controlled_activation observation report
    def test_validator_accepts_controlled_activation_report(self):
        from intelligence_schema_validator import validate_handoff_publisher_observation_report
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = os.path.join(tmpdir, "obs_report.json")
            report = _make_min_valid_report("controlled_activation", "controlled_activation_ready")
            with open(report_path, "w") as f:
                json.dump(report, f)
            result = validate_handoff_publisher_observation_report(report_path)
        assert result.ok, f"Validator rejected controlled_activation report: {result.errors}"

    # Test 16 — validator accepts a validation_only observation report
    def test_validator_accepts_validation_only_report(self):
        from intelligence_schema_validator import validate_handoff_publisher_observation_report
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = os.path.join(tmpdir, "obs_report.json")
            report = _make_min_valid_report("validation_only", "validation_only_stable")
            with open(report_path, "w") as f:
                json.dump(report, f)
            result = validate_handoff_publisher_observation_report(report_path)
        assert result.ok, f"Validator rejected validation_only report: {result.errors}"

    # Test 17 — smoke: mode awareness produces valid gates for both modes
    @pytest.mark.smoke
    def test_smoke_mode_awareness_7j3(self):
        """Smoke: observer is mode-aware and produces valid gates for both modes."""
        sys.path.insert(0, _ROOT)
        import handoff_publisher_observer as hpo

        run_log = self._threshold_run_log()
        universe = self._clean_universe()
        heartbeat = self._clean_heartbeat()
        freshness = self._clean_freshness()

        # validation_only path
        vo_manifest = self._vo_manifest_analysis()
        gate_vo, tm_vo, _ = hpo._determine_readiness_gate(
            run_log, vo_manifest, universe, heartbeat, freshness, [],
            current_mode="validation_only",
        )
        assert gate_vo in _VALID_READINESS_GATES
        assert tm_vo is True

        # controlled_activation path — clean manifest must NOT trigger fix_publisher gate
        ca_manifest = self._ca_manifest_analysis()
        gate_ca, tm_ca, _ = hpo._determine_readiness_gate(
            run_log, ca_manifest, universe, heartbeat, freshness, [],
            current_mode="controlled_activation",
        )
        assert gate_ca in _VALID_READINESS_GATES
        assert gate_ca != "fix_publisher_before_flag_activation"
        assert tm_ca is True
