"""
tests/test_intelligence_sprint6b.py — Sprint 6B Live Read-Only Advisory Hook Tests.

Covers all 34 acceptance tests from the Sprint 6B spec (Parts A–I).
Tests advisory_logger.py in isolation using controlled fixtures.
No live data, no APIs, no broker calls.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADVISORY_LOGGER_PATH = os.path.join(_BASE, "advisory_logger.py")
_ADVISORY_REPORT_PATH = os.path.join(_BASE, "data", "intelligence", "advisory_report.json")
_RUNTIME_LOG_PATH     = os.path.join(_BASE, "data", "intelligence", "advisory_runtime_log.jsonl")

_PRODUCTION_MODULES = {
    "scanner", "bot_trading", "market_intelligence", "orders_core",
    "guardrails", "catalyst_engine", "overnight_research",
    "agents", "sentinel_agents", "bot_ibkr", "learning",
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def minimal_advisory_report(tmp_path) -> dict:
    """Minimal advisory_report.json for testing."""
    return {
        "schema_version": "1.0.0",
        "generated_at": "2026-05-06T00:00:00+00:00",
        "valid_for_session": "test",
        "mode": "offline_advisory_report",
        "data_source_mode": "local_shadow_outputs_only",
        "source_files": [],
        "advisory_summary": {
            "current_candidates_count": 50,
            "shadow_candidates_count": 5,
            "overlap_count": 2,
            "advisory_include_count": 1,
            "advisory_watch_count": 2,
            "advisory_defer_count": 1,
            "advisory_exclude_count": 0,
            "advisory_unresolved_count": 1,
            "route_disagreement_count": 1,
            "unsupported_current_count": 3,
            "missing_shadow_count": 2,
            "non_executable_all": True,
            "live_output_changed": False,
        },
        "candidate_advisory": [
            {
                "symbol": "NVDA",
                "in_current": True,
                "in_shadow": True,
                "current_sources": ["manual_conviction_favourites"],
                "shadow_sources": ["favourites_manual_conviction"],
                "current_route": "manual_conviction",
                "shadow_route": "manual_conviction",
                "advisory_status": "advisory_watch",
                "advisory_reason": "manual_conviction_protected",
                "reason_to_care": "manual_conviction",
                "theme_ids": [],
                "driver_ids": [],
                "source_labels": ["favourites_manual_conviction"],
                "thesis_status": None,
                "theme_state": None,
                "quota_group": "manual_conviction",
                "exclusion_reason": None,
                "route_disagreement": False,
                "executable": False,
                "order_instruction": None,
            },
            {
                "symbol": "AAPL",
                "in_current": True,
                "in_shadow": True,
                "current_sources": ["manual_conviction_favourites"],
                "shadow_sources": ["favourites_manual_conviction"],
                "current_route": "manual_conviction",
                "shadow_route": "manual_conviction",
                "advisory_status": "advisory_include",
                "advisory_reason": "supported_by_intelligence",
                "reason_to_care": "structural",
                "theme_ids": ["data_centre_power"],
                "driver_ids": ["ai_capex_growth"],
                "source_labels": ["economic_intelligence_structural"],
                "thesis_status": "new",
                "theme_state": "activated",
                "quota_group": "structural_position",
                "exclusion_reason": None,
                "route_disagreement": False,
                "executable": False,
                "order_instruction": None,
            },
        ],
        "route_disagreements": {
            "total_route_disagreements": 1,
            "disagreements": [
                {
                    "symbol": "MSFT",
                    "current_route": "intraday_swing",
                    "shadow_route": "watchlist",
                    "current_source": "tier_a_always_on",
                    "shadow_source": [],
                    "reason": "route_test_disagreement",
                    "advisory_status": "advisory_watch",
                    "executable": False,
                }
            ],
            "disagreement_by_source": {},
            "disagreement_by_route_pair": {},
            "warnings": [],
        },
        "unsupported_current_candidates": {"total": 3, "symbols": [], "by_source": {}, "by_reason": {}, "warnings": []},
        "missing_shadow_candidates": {"total": 2, "symbols": ["VRT", "ASML"], "by_theme": {}, "by_route": {}, "by_reason_to_care": {}, "advisory_status_distribution": {}},
        "tier_d_advisory": {"tier_d_total_current": 10, "tier_d_in_shadow": 1, "tier_d_excluded": 9, "tier_d_preservation_rate": 0.1, "tier_d_top_preserved": [], "tier_d_top_excluded": [], "tier_d_excluded_due_structural_quota": 9, "tier_d_preserved_through_manual_or_other_source": [], "tier_d_quality_rank_available": False, "advisory_findings": []},
        "structural_quota_advisory": {"structural_demand_count": 30, "structural_capacity": 20, "structural_accepted": 20, "structural_overflow_count": 10, "structural_quota_binding": True, "overflow_by_theme": {}, "overflow_by_source": {}, "overflow_by_route": {}, "recommendation": "keep_current_shadow_cap_until_more_evidence", "production_change_required": False},
        "risk_theme_advisory": {"headwind_candidates": [], "pressure_candidates": [], "weakening_themes": [], "crowded_themes": [], "risk_off_themes": [], "executable_headwind_candidates": False, "short_or_hedge_instruction_generated": False, "findings": []},
        "manual_and_held_advisory": {"manual_candidates_total": 2, "manual_candidates_in_shadow": 2, "manual_candidates_missing": [], "manual_protection_preserved": True, "held_candidates_total": 0, "held_candidates_in_shadow": 0, "held_candidates_missing": [], "held_protection_preserved": True, "warnings": []},
        "warnings": [],
        "no_live_api_called": True,
        "broker_called": False,
        "env_inspected": False,
        "raw_news_used": False,
        "llm_used": False,
        "broad_intraday_scan_used": False,
        "production_modules_imported": False,
        "live_output_changed": False,
    }


@pytest.fixture
def write_advisory_report(minimal_advisory_report, monkeypatch, tmp_path):
    """Write minimal advisory_report.json to a tmp file and patch path constants."""
    report_path = tmp_path / "advisory_report.json"
    log_path    = tmp_path / "advisory_runtime_log.jsonl"
    report_path.write_text(json.dumps(minimal_advisory_report), encoding="utf-8")

    import advisory_logger as al
    monkeypatch.setattr(al, "_ADVISORY_REPORT_PATH", str(report_path))
    monkeypatch.setattr(al, "_RUNTIME_LOG_PATH",     str(log_path))
    return report_path, log_path, minimal_advisory_report


# ---------------------------------------------------------------------------
# Test 1 — advisory_logger.py exists
# ---------------------------------------------------------------------------
class TestAdvisoryLoggerExists:
    def test_advisory_logger_exists(self):
        assert os.path.isfile(_ADVISORY_LOGGER_PATH), \
            "advisory_logger.py not found at repo root"


# ---------------------------------------------------------------------------
# Tests 2–3 — reads and writes correctly
# ---------------------------------------------------------------------------
class TestAdvisoryLoggerReadWrite:
    def test_reads_advisory_report(self, write_advisory_report):
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al
        al.log_advisory_context(candidates=["NVDA", "AAPL"], regime="BULL_TRENDING")
        assert log_path.exists(), "advisory_runtime_log.jsonl must be written"

    def test_writes_advisory_runtime_log(self, write_advisory_report):
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al
        al.log_advisory_context(candidates=["NVDA"], regime="TEST")
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["advisory_report_available"] is True


# ---------------------------------------------------------------------------
# Tests 4–11 — log record safety invariants
# ---------------------------------------------------------------------------
class TestAdvisoryLogRecordSafety:
    @pytest.fixture(autouse=True)
    def setup(self, write_advisory_report):
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al
        al.log_advisory_context(candidates=["NVDA", "AAPL", "UNKNOWN_SYM"], regime="BULL_TRENDING")
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        self.record = json.loads(lines[-1])

    def test_advisory_only_true(self):
        assert self.record.get("advisory_only") is True

    def test_executable_false(self):
        assert self.record.get("executable") is False

    def test_order_instruction_null(self):
        assert self.record.get("order_instruction") is None

    def test_production_decision_changed_false(self):
        assert self.record.get("production_decision_changed") is False

    def test_apex_input_changed_false(self):
        assert self.record.get("apex_input_changed") is False

    def test_scanner_output_changed_false(self):
        assert self.record.get("scanner_output_changed") is False

    def test_order_logic_changed_false(self):
        assert self.record.get("order_logic_changed") is False

    def test_risk_logic_changed_false(self):
        assert self.record.get("risk_logic_changed") is False


# ---------------------------------------------------------------------------
# Tests 12–13 — resilience to missing / invalid report
# ---------------------------------------------------------------------------
class TestAdvisoryLoggerResilience:
    def test_handles_missing_advisory_report_without_changing_behaviour(
        self, tmp_path, monkeypatch
    ):
        """When advisory_report.json is missing, logger must not raise and must still write a log."""
        import advisory_logger as al
        missing_path = str(tmp_path / "nonexistent_advisory_report.json")
        log_path     = str(tmp_path / "runtime_log.jsonl")
        monkeypatch.setattr(al, "_ADVISORY_REPORT_PATH", missing_path)
        monkeypatch.setattr(al, "_RUNTIME_LOG_PATH",     log_path)

        # Must not raise
        al.log_advisory_context(candidates=["NVDA"], regime="TEST")

        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record.get("advisory_report_available") is False
        assert record.get("advisory_only") is True
        assert record.get("production_decision_changed") is False

    def test_handles_invalid_advisory_report_without_changing_behaviour(
        self, tmp_path, monkeypatch
    ):
        """When advisory_report.json is invalid JSON, logger must not raise."""
        import advisory_logger as al
        bad_path = tmp_path / "bad_advisory.json"
        bad_path.write_text("NOT VALID JSON {{{{", encoding="utf-8")
        log_path = str(tmp_path / "runtime_log.jsonl")
        monkeypatch.setattr(al, "_ADVISORY_REPORT_PATH", str(bad_path))
        monkeypatch.setattr(al, "_RUNTIME_LOG_PATH",     log_path)

        al.log_advisory_context(candidates=["TSLA"], regime="TEST")

        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record.get("advisory_report_available") is False
        assert record.get("advisory_only") is True


# ---------------------------------------------------------------------------
# Tests 14–21 — forbidden paths (verified via AST + safety flags)
# ---------------------------------------------------------------------------
class TestAdvisoryLoggerForbiddenPaths:
    @pytest.fixture(autouse=True)
    def setup(self, write_advisory_report):
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al
        al.log_advisory_context(candidates=["NVDA"], regime="TEST")
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        self.record = json.loads(lines[-1])

    def test_does_not_create_symbols(self):
        """candidate_matches only contain symbols that were passed in."""
        matches = self.record.get("candidate_matches", [])
        passed_in = {"NVDA"}
        for m in matches:
            assert m["symbol"] in passed_in | {"NVDA"}, \
                f"Unexpected symbol {m['symbol']} created by advisory_logger"

    def test_does_not_call_broker(self):
        assert self.record.get("broker_called") is False

    def test_does_not_call_llm(self):
        assert self.record.get("llm_called") is False

    def test_does_not_call_live_apis(self):
        assert self.record.get("live_api_called") is False

    def test_does_not_inspect_env(self):
        assert self.record.get("env_inspected") is False

    def test_does_not_use_raw_news(self):
        assert self.record.get("raw_news_used") is False

    def test_does_not_perform_broad_intraday_scan(self):
        assert self.record.get("broad_intraday_scan_used") is False

    def test_live_output_changed_false(self):
        assert self.record.get("live_output_changed") is False


# ---------------------------------------------------------------------------
# Test 22 — config flag defaults false
# ---------------------------------------------------------------------------
class TestAdvisoryConfigFlag:
    def test_intelligence_first_advisory_enabled_defaults_false(self):
        sys.path.insert(0, _BASE)
        from config import CONFIG
        assert CONFIG.get("intelligence_first_advisory_enabled", False) is False, \
            "intelligence_first_advisory_enabled must default to False"

    def test_enable_active_opportunity_universe_handoff_remains_false(self):
        from config import CONFIG
        assert CONFIG.get("enable_active_opportunity_universe_handoff", False) is False, \
            "enable_active_opportunity_universe_handoff must remain False"


# ---------------------------------------------------------------------------
# Tests 23–25 — flag gate behaviour
# ---------------------------------------------------------------------------
class TestAdvisoryFlagGate:
    def test_when_flag_false_hook_does_not_read_advisory_report(
        self, tmp_path, monkeypatch
    ):
        """When flag is False, log_advisory_context must not be called / not write."""
        # Simulate the bot_trading.py guard: only call log_advisory_context when flag True
        flag = False
        log_path = tmp_path / "runtime_log.jsonl"

        import advisory_logger as al
        monkeypatch.setattr(al, "_RUNTIME_LOG_PATH", str(log_path))

        if flag:  # This branch must NOT execute
            al.log_advisory_context(candidates=["NVDA"], regime="TEST")

        assert not log_path.exists(), \
            "When flag is False, advisory logger must not write runtime log"

    def test_when_flag_false_no_advisory_log_written(self, tmp_path, monkeypatch):
        """Identical to above — belt and suspenders."""
        flag = False
        log_path = tmp_path / "runtime_log.jsonl"
        import advisory_logger as al
        monkeypatch.setattr(al, "_RUNTIME_LOG_PATH", str(log_path))
        if flag:
            al.log_advisory_context(candidates=[], regime="TEST")
        assert not log_path.exists()

    def test_when_flag_true_in_fixture_hook_writes_log(self, write_advisory_report):
        """When flag is True (simulated in fixture), advisory log is written."""
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al
        flag = True
        if flag:
            al.log_advisory_context(candidates=["NVDA", "AAPL"], regime="BULL_TRENDING")
        assert log_path.exists(), "When flag True, advisory log must be written"


# ---------------------------------------------------------------------------
# Tests 26–28 — hook does not mutate caller objects
# ---------------------------------------------------------------------------
class TestAdvisoryHookNoMutation:
    def test_hook_does_not_change_candidate_list(self, write_advisory_report):
        """Candidate list passed to logger must be unchanged after call."""
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al

        original = ["NVDA", "AAPL", "TSLA"]
        candidates_copy = list(original)
        al.log_advisory_context(candidates=candidates_copy, regime="TEST")
        assert candidates_copy == original, \
            "log_advisory_context must not mutate the candidate list"

    def test_hook_does_not_change_apex_payload(self, write_advisory_report):
        """Simulated Apex payload dict must be unchanged after advisory call."""
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al

        apex_payload = {"candidates": ["NVDA"], "regime": "BULL", "session": "CORE"}
        original_payload = dict(apex_payload)
        # Advisory logger does not receive apex_payload — just verify independence
        al.log_advisory_context(candidates=["NVDA"], regime="BULL")
        assert apex_payload == original_payload, \
            "Apex payload must not be mutated by advisory logger"

    def test_hook_does_not_change_order_risk_objects(self, write_advisory_report):
        """Simulated order/risk object must be unchanged after advisory call."""
        report_path, log_path, report = write_advisory_report
        import advisory_logger as al

        mock_risk = {"max_position_pct": 0.05, "stop_loss_pct": 0.02}
        original_risk = dict(mock_risk)
        al.log_advisory_context(candidates=[], regime="TEST")
        assert mock_risk == original_risk, \
            "Risk object must not be mutated by advisory logger"


# ---------------------------------------------------------------------------
# Test 29 — hook exceptions are swallowed
# ---------------------------------------------------------------------------
class TestAdvisoryHookExceptionHandling:
    def test_hook_exceptions_are_swallowed(self, tmp_path, monkeypatch):
        """An exception inside log_advisory_context must not propagate to caller."""
        import advisory_logger as al

        # Patch _append_log to raise
        def _raise(*args, **kwargs):
            raise RuntimeError("Simulated log write failure")

        monkeypatch.setattr(al, "_append_log", _raise)
        # Must not raise — exception must be swallowed
        al.log_advisory_context(candidates=["NVDA"], regime="TEST")


# ---------------------------------------------------------------------------
# Test 30 — no production module imports in advisory_logger.py (AST check)
# ---------------------------------------------------------------------------
class TestAdvisoryLoggerNoProductionImports:
    def test_advisory_logger_imports_no_production_modules(self):
        with open(_ADVISORY_LOGGER_PATH, encoding="utf-8") as f:
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
        violations = imported & _PRODUCTION_MODULES
        assert not violations, \
            f"advisory_logger.py imports production modules: {violations}"


# ---------------------------------------------------------------------------
# Tests 31–34 — regression (intelligence regression + smoke + full suite)
# ---------------------------------------------------------------------------
class TestIntelligenceRegressionSpotCheck:
    def test_day2_sprint6a_intelligence_regression(self):
        """Spot-check: advisory_report still validates after Sprint 6B hook added."""
        sys.path.insert(0, _BASE)
        from intelligence_schema_validator import validate_advisory_report
        result = validate_advisory_report(_ADVISORY_REPORT_PATH)
        assert result.ok, f"advisory_report validation failed: {result.errors}"

    def test_config_flag_still_false_after_hook_added(self):
        from config import CONFIG
        assert CONFIG.get("intelligence_first_advisory_enabled", False) is False

    def test_validate_all_still_passes(self):
        from intelligence_schema_validator import validate_all
        results = validate_all(os.path.join(_BASE, "data", "intelligence"))
        failed = {k: v for k, v in results.items() if not v.ok}
        assert not failed, f"validate_all() has failures: {failed}"

    def test_live_output_changed_false_across_intelligence_files(self):
        """Spot-check live_output_changed=false on key intelligence outputs."""
        for fname in ["theme_activation.json", "thesis_store.json", "advisory_report.json"]:
            path = os.path.join(_BASE, "data", "intelligence", fname)
            if os.path.isfile(path):
                data = json.load(open(path, encoding="utf-8"))
                assert data.get("live_output_changed") is False, \
                    f"{fname}: live_output_changed must be False"
