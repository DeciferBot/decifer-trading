"""
tests/test_intelligence_sprint6c.py — Sprint 6C Evidence Review Tests (29 tests)

Tests for advisory_log_reviewer.py and validate_advisory_log_review().

Tiered testing: sprint tests + intelligence regression + validator + smoke.
No full suite — no production modules touched.

Safety invariants verified:
- advisory_only = true
- executable = false
- live_output_changed = false
- production_decision_changed = false
- apex_input_changed = false
- No production module imports (AST check)
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_REVIEW_PATH = os.path.join(_ROOT, "data", "intelligence", "advisory_log_review.json")
_RUNTIME_LOG_PATH = os.path.join(_ROOT, "data", "intelligence", "advisory_runtime_log.jsonl")
_REVIEWER_MODULE = os.path.join(_ROOT, "advisory_log_reviewer.py")

# Modules that must NOT be imported by advisory_log_reviewer.py
_FORBIDDEN_IMPORTS = {
    "scanner", "bot_trading", "market_intelligence", "orders_core",
    "guardrails", "theme_tracker", "catalyst_engine", "overnight_research",
    "universe_position", "universe_committed", "learning", "agents",
    "sentinel_agents", "apex_orchestrator",
}


# ---------------------------------------------------------------------------
# Helper: run reviewer on a temp log and return review dict
# ---------------------------------------------------------------------------

def _run_reviewer_on(records: list[dict]) -> dict:
    """Write records to a temp JSONL, run reviewer, return review dict."""
    from advisory_log_reviewer import (
        _load_records,
        _extract_session_keys,
        _check_safety_invariants,
        _compute_rates,
        _compute_candidate_overlap,
        _determine_decision_gate,
        _build_review,
    )
    sessions  = _extract_session_keys(records)
    safety    = _check_safety_invariants(records)
    rates     = _compute_rates(records)
    ca        = _compute_candidate_overlap(records)
    gate, reasons = _determine_decision_gate(records, sessions, safety, [])
    return _build_review(
        records=records,
        sessions=sessions,
        safety=safety,
        rates=rates,
        candidate_analysis=ca,
        decision_gate=gate,
        gate_reasons=reasons,
        parse_warnings=[],
    )


def _make_safe_record(
    timestamp: str = "2026-05-06T10:00:00+00:00",
    regime: str = "BULL_TRENDING",
    candidate_symbols: list[str] | None = None,
) -> dict:
    """Build a minimal valid advisory runtime log record."""
    syms = candidate_symbols or []
    matches = [
        {
            "symbol": s, "advisory_status": "advisory_watch",
            "executable": False, "order_instruction": None,
        }
        for s in syms
    ]
    return {
        "timestamp": timestamp,
        "mode": "live_read_only_advisory",
        "regime": regime,
        "advisory_report_available": True,
        "advisory_report_fresh": True,
        "advisory_only": True,
        "executable": False,
        "order_instruction": None,
        "production_decision_changed": False,
        "apex_input_changed": False,
        "scanner_output_changed": False,
        "order_logic_changed": False,
        "risk_logic_changed": False,
        "broker_called": False,
        "llm_called": False,
        "live_api_called": False,
        "env_inspected": False,
        "raw_news_used": False,
        "broad_intraday_scan_used": False,
        "live_output_changed": False,
        "candidate_matches": matches,
        "candidate_matches_count": len(matches),
        "route_disagreements_summary": {"total": 0, "in_current_candidates": 0},
    }


# ===========================================================================
# Test classes
# ===========================================================================

class TestReviewOutputExists(unittest.TestCase):
    """(2 tests) advisory_log_review.json exists and is valid JSON."""

    def test_review_file_exists(self):
        self.assertTrue(
            os.path.isfile(_REVIEW_PATH),
            f"advisory_log_review.json not found at {_REVIEW_PATH}"
        )

    def test_review_file_is_valid_json(self):
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)


class TestReviewTopLevelKeys(unittest.TestCase):
    """(4 tests) Required top-level keys present."""

    @classmethod
    def setUpClass(cls):
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            cls.data = json.load(f)

    def test_schema_version_present(self):
        self.assertIn("schema_version", self.data)

    def test_decision_gate_present(self):
        self.assertIn("decision_gate", self.data)

    def test_review_summary_present(self):
        self.assertIn("review_summary", self.data)

    def test_safety_analysis_present(self):
        self.assertIn("safety_analysis", self.data)


class TestReviewSafetyFlags(unittest.TestCase):
    """(5 tests) Safety invariants all hardcoded correctly."""

    @classmethod
    def setUpClass(cls):
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            cls.data = json.load(f)

    def test_advisory_only_is_true(self):
        self.assertIs(self.data.get("advisory_only"), True)

    def test_executable_is_false(self):
        self.assertIs(self.data.get("executable"), False)

    def test_live_output_changed_is_false(self):
        self.assertIs(self.data.get("live_output_changed"), False)

    def test_production_decision_changed_is_false(self):
        self.assertIs(self.data.get("production_decision_changed"), False)

    def test_apex_input_changed_is_false(self):
        self.assertIs(self.data.get("apex_input_changed"), False)


class TestReviewSummarySection(unittest.TestCase):
    """(4 tests) review_summary section structure."""

    @classmethod
    def setUpClass(cls):
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            cls.data = json.load(f)
        cls.rs = cls.data.get("review_summary", {})

    def test_records_read_is_int(self):
        self.assertIsInstance(self.rs.get("records_read"), int)

    def test_sessions_detected_is_int(self):
        self.assertIsInstance(self.rs.get("sessions_detected"), int)

    def test_production_decision_changed_count_is_zero(self):
        self.assertEqual(self.rs.get("production_decision_changed_count"), 0)

    def test_apex_input_changed_count_is_zero(self):
        self.assertEqual(self.rs.get("apex_input_changed_count"), 0)


class TestDecisionGateValue(unittest.TestCase):
    """(3 tests) decision_gate is a valid value."""

    _VALID_GATES = {
        "insufficient_live_observation",
        "advisory_safe_continue_logging",
        "advisory_ready_for_handoff_design",
        "advisory_needs_fix",
    }

    @classmethod
    def setUpClass(cls):
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            cls.data = json.load(f)

    def test_decision_gate_is_valid(self):
        gate = self.data.get("decision_gate")
        self.assertIn(gate, self._VALID_GATES, f"Invalid decision_gate: {gate!r}")

    def test_gate_reasons_is_list(self):
        self.assertIsInstance(self.data.get("gate_reasons"), list)

    def test_gate_reasons_non_empty(self):
        self.assertGreater(len(self.data.get("gate_reasons", [])), 0)


class TestObservationThresholdMet(unittest.TestCase):
    """(3 tests) Observation complete: 35-record log, gate = advisory_ready_for_handoff_design."""

    @classmethod
    def setUpClass(cls):
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            cls.data = json.load(f)

    def test_records_at_or_above_threshold(self):
        rs = self.data.get("review_summary", {})
        self.assertGreaterEqual(rs.get("records_read", 0), 10)

    def test_sessions_below_multi_session_threshold(self):
        rs = self.data.get("review_summary", {})
        self.assertLess(rs.get("sessions_detected", 0), 3)

    def test_gate_is_ready_for_handoff_design(self):
        self.assertEqual(self.data.get("decision_gate"), "advisory_ready_for_handoff_design")


class TestZeroRecordCase(unittest.TestCase):
    """(2 tests) Zero-record case — reviewer handles gracefully."""

    def test_zero_records_produces_dict(self):
        result = _run_reviewer_on([])
        self.assertIsInstance(result, dict)

    def test_zero_records_gate_is_insufficient(self):
        result = _run_reviewer_on([])
        self.assertEqual(result.get("decision_gate"), "insufficient_live_observation")


class TestSafetyViolationDetection(unittest.TestCase):
    """(3 tests) Reviewer detects safety violations and sets advisory_needs_fix."""

    def _record_with_violation(self, **overrides) -> dict:
        r = _make_safe_record()
        r.update(overrides)
        return r

    def test_executable_true_triggers_needs_fix(self):
        bad = self._record_with_violation(executable=True)
        result = _run_reviewer_on([bad])
        self.assertEqual(result["decision_gate"], "advisory_needs_fix")

    def test_live_output_changed_true_triggers_needs_fix(self):
        bad = self._record_with_violation(live_output_changed=True)
        result = _run_reviewer_on([bad])
        self.assertEqual(result["decision_gate"], "advisory_needs_fix")

    def test_production_decision_changed_true_triggers_needs_fix(self):
        bad = self._record_with_violation(production_decision_changed=True)
        result = _run_reviewer_on([bad])
        self.assertEqual(result["decision_gate"], "advisory_needs_fix")


class TestAboveThresholdBehaviour(unittest.TestCase):
    """(3 tests) With ≥10 clean records across ≥3 sessions, gate is not insufficient."""

    @classmethod
    def setUpClass(cls):
        # 10 clean records across 3 days
        days = ["2026-05-01", "2026-05-02", "2026-05-03"]
        cls.records = []
        for i in range(10):
            day = days[i % 3]
            cls.records.append(
                _make_safe_record(
                    timestamp=f"{day}T10:{i:02d}:00+00:00",
                    candidate_symbols=["NVDA", "AAPL"],
                )
            )
        cls.result = _run_reviewer_on(cls.records)

    def test_gate_is_not_insufficient(self):
        self.assertNotEqual(self.result["decision_gate"], "insufficient_live_observation")

    def test_safety_invariants_hold(self):
        sa = self.result.get("safety_analysis", {})
        self.assertTrue(sa.get("all_invariants_hold"))

    def test_records_count_correct(self):
        rs = self.result.get("review_summary", {})
        self.assertEqual(rs.get("records_read"), 10)


class TestValidatorIntegration(unittest.TestCase):
    """(3 tests) validate_advisory_log_review passes on generated output."""

    def test_validator_passes_on_generated_file(self):
        from intelligence_schema_validator import validate_advisory_log_review as _val
        result = _val(_REVIEW_PATH)
        self.assertTrue(result.ok, f"Validation errors: {result.errors}")

    def test_validator_fails_on_missing_file(self):
        from intelligence_schema_validator import validate_advisory_log_review as _val
        result = _val("/tmp/nonexistent_advisory_log_review.json")
        self.assertFalse(result.ok)

    def test_validator_fails_on_invalid_gate(self):
        from intelligence_schema_validator import validate_advisory_log_review as _val
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            data = json.load(f)
        data["decision_gate"] = "invalid_gate_value"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(data, tmp)
            tmp_path = tmp.name
        try:
            result = _val(tmp_path)
            self.assertFalse(result.ok)
        finally:
            os.unlink(tmp_path)


class TestForbiddenImports(unittest.TestCase):
    """(1 test) advisory_log_reviewer.py must not import production modules."""

    def test_no_production_module_imports(self):
        with open(_REVIEWER_MODULE, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden_found = imported & _FORBIDDEN_IMPORTS
        self.assertEqual(
            forbidden_found, set(),
            f"advisory_log_reviewer.py imports forbidden modules: {forbidden_found}"
        )


class TestMinimumThresholdSection(unittest.TestCase):
    """(1 test) minimum_threshold section is present and correct."""

    def test_minimum_threshold_section_structure(self):
        with open(_REVIEW_PATH, encoding="utf-8") as f:
            data = json.load(f)
        mt = data.get("minimum_threshold")
        self.assertIsInstance(mt, dict)
        for key in ("min_records", "min_sessions", "records_met", "sessions_met"):
            self.assertIn(key, mt, f"minimum_threshold missing key '{key}'")
        self.assertEqual(mt.get("min_records"), 10)
        self.assertEqual(mt.get("min_sessions"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
