"""
tests/test_backlog_validator.py
───────────────────────────────
Tests for tools/validate_backlog.py — dependency enforcement validator.

Coverage:
  TestLoadBacklog                 — file loading, error paths
  TestBuildDepGraph               — graph construction from backlog
  TestCheckDependencyViolations   — core enforcement rule
  TestCheckNoCycles               — cycle detection
  TestValidateAll                 — integration, ValidationResult contract
  TestRealBacklogCompliance       — smoke tests against actual backlog.json
"""
from __future__ import annotations

import json
import os
import sys

import pytest

# ── Path bootstrap ────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from validate_backlog import (  # noqa: E402
    ValidationResult,
    build_dep_graph,
    check_dependency_violations,
    check_no_cycles,
    load_backlog,
    validate_all,
)

# ── Test helpers ──────────────────────────────────────────────────────────


def _backlog(items, terminal=None):
    return {
        "version": "1.2",
        "wip_policy": {
            "terminal_statuses": terminal or [
                "shipped", "validated", "resolved", "superseded"
            ],
        },
        "items": items,
    }


def _item(id, status, depends_on=None, title=None):
    return {
        "id": id,
        "title": title or f"Feature {id}",
        "status": status,
        "depends_on": depends_on or [],
    }


# ── TestLoadBacklog ───────────────────────────────────────────────────────


class TestLoadBacklog:

    def test_loads_valid_json(self, tmp_path):
        data = _backlog([_item("F1", "pending")])
        f = tmp_path / "backlog.json"
        f.write_text(json.dumps(data))
        result = load_backlog(f)
        assert result["version"] == "1.2"

    def test_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_backlog(tmp_path / "nonexistent.json")

    def test_raises_on_malformed_json(self, tmp_path):
        f = tmp_path / "backlog.json"
        f.write_text("{not: valid json")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_backlog(f)

    def test_returns_dict(self, tmp_path):
        data = _backlog([])
        f = tmp_path / "backlog.json"
        f.write_text(json.dumps(data))
        result = load_backlog(f)
        assert isinstance(result, dict)


# ── TestBuildDepGraph ─────────────────────────────────────────────────────


class TestBuildDepGraph:

    def test_empty_backlog_returns_empty_graph(self):
        assert build_dep_graph(_backlog([])) == {}

    def test_item_with_no_deps_has_empty_list(self):
        graph = build_dep_graph(_backlog([_item("F1", "pending")]))
        assert graph["F1"] == []

    def test_item_with_deps_listed_correctly(self):
        bl = _backlog([
            _item("F1", "pending", depends_on=["F2", "F3"]),
            _item("F2", "shipped"),
            _item("F3", "shipped"),
        ])
        assert build_dep_graph(bl)["F1"] == ["F2", "F3"]

    def test_all_item_ids_appear_as_keys(self):
        bl = _backlog([
            _item("A", "pending"),
            _item("B", "ready", depends_on=["A"]),
        ])
        assert set(build_dep_graph(bl).keys()) == {"A", "B"}

    def test_items_without_depends_on_field_get_empty_list(self):
        bl = _backlog([{"id": "F1", "status": "pending", "title": "No deps"}])
        assert build_dep_graph(bl)["F1"] == []


# ── TestCheckDependencyViolations ─────────────────────────────────────────


class TestCheckDependencyViolations:

    # ── No violations ─────────────────────────────────────────────────────

    def test_all_deps_terminal_no_violation(self):
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "shipped")])
        assert check_dependency_violations(bl) == []

    def test_no_deps_no_violation(self):
        assert check_dependency_violations(_backlog([_item("F1", "ready")])) == []

    def test_pending_status_not_enforced(self):
        bl = _backlog([
            _item("F1", "pending", depends_on=["F2"]),
            _item("F2", "pending"),
        ])
        assert check_dependency_violations(bl) == []

    def test_shipped_feature_with_pending_dep_not_flagged(self):
        """Cannot retroactively block a shipped item — the code is already in production."""
        bl = _backlog([
            _item("F1", "shipped", depends_on=["F2"]),
            _item("F2", "pending"),
        ])
        assert check_dependency_violations(bl) == []

    def test_validated_feature_with_pending_dep_not_flagged(self):
        bl = _backlog([_item("F1", "validated", depends_on=["F2"]), _item("F2", "pending")])
        assert check_dependency_violations(bl) == []

    def test_resolved_feature_with_pending_dep_not_flagged(self):
        bl = _backlog([_item("F1", "resolved", depends_on=["F2"]), _item("F2", "pending")])
        assert check_dependency_violations(bl) == []

    def test_superseded_feature_with_pending_dep_not_flagged(self):
        bl = _backlog([_item("F1", "superseded", depends_on=["F2"]), _item("F2", "pending")])
        assert check_dependency_violations(bl) == []

    def test_dep_superseded_counts_as_terminal(self):
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "superseded")])
        assert check_dependency_violations(bl) == []

    def test_dep_validated_counts_as_terminal(self):
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "validated")])
        assert check_dependency_violations(bl) == []

    # ── Violations ────────────────────────────────────────────────────────

    def test_ready_with_pending_dep_flagged(self):
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "pending")])
        violations = check_dependency_violations(bl)
        assert len(violations) == 1
        assert "DEP_VIOLATION" in violations[0]
        assert "F1" in violations[0]
        assert "F2" in violations[0]

    def test_in_progress_with_pending_dep_flagged(self):
        bl = _backlog([_item("F1", "in_progress", depends_on=["F2"]), _item("F2", "pending")])
        violations = check_dependency_violations(bl)
        assert any("DEP_VIOLATION" in v and "F1" in v for v in violations)

    def test_ready_with_ready_dep_flagged(self):
        """ready is not terminal — deps must be shipped/validated/resolved/superseded."""
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "ready")])
        violations = check_dependency_violations(bl)
        assert any("DEP_VIOLATION" in v for v in violations)

    def test_missing_dep_flagged(self):
        bl = _backlog([_item("F1", "ready", depends_on=["NONEXISTENT"])])
        violations = check_dependency_violations(bl)
        assert any("MISSING_DEP" in v and "NONEXISTENT" in v for v in violations)

    def test_multiple_violations_all_reported(self):
        bl = _backlog([
            _item("F1", "ready", depends_on=["F2"]),
            _item("F2", "ready", depends_on=["F3"]),
            _item("F3", "pending"),
        ])
        assert len(check_dependency_violations(bl)) >= 2

    def test_violation_message_includes_dep_status(self):
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "in_progress")])
        violations = check_dependency_violations(bl)
        assert violations and "in_progress" in violations[0]

    # ── Phase E specific scenarios (LightGBM → SHAP/LSTM → GT-Score) ─────

    def test_shap_pruner_cannot_be_ready_if_lightgbm_not_shipped(self):
        """BACK-017 (SHAP) depends on BACK-016 (LightGBM)."""
        bl = _backlog([
            _item("BACK-016", "pending"),
            _item("BACK-017", "ready", depends_on=["BACK-016"]),
        ])
        violations = check_dependency_violations(bl)
        assert any("DEP_VIOLATION" in v and "BACK-017" in v and "BACK-016" in v
                   for v in violations)

    def test_shap_pruner_can_be_ready_when_lightgbm_shipped(self):
        bl = _backlog([
            _item("BACK-016", "shipped"),
            _item("BACK-017", "ready", depends_on=["BACK-016"]),
        ])
        assert check_dependency_violations(bl) == []

    def test_lstm_cannot_be_ready_if_lightgbm_not_shipped(self):
        """BACK-018 (LSTM) depends on BACK-016 (LightGBM)."""
        bl = _backlog([
            _item("BACK-016", "ready"),
            _item("BACK-018", "ready", depends_on=["BACK-016"]),
        ])
        violations = check_dependency_violations(bl)
        assert any("BACK-018" in v and "BACK-016" in v for v in violations)

    def test_gt_score_blocked_if_either_upstream_not_shipped(self):
        """BACK-019 needs both BACK-017 (SHAP) and BACK-018 (LSTM)."""
        bl = _backlog([
            _item("BACK-017", "shipped"),
            _item("BACK-018", "pending"),
            _item("BACK-019", "ready", depends_on=["BACK-017", "BACK-018"]),
        ])
        violations = check_dependency_violations(bl)
        assert any("BACK-019" in v and "BACK-018" in v for v in violations)

    def test_gt_score_allowed_when_both_upstreams_shipped(self):
        bl = _backlog([
            _item("BACK-017", "shipped"),
            _item("BACK-018", "shipped"),
            _item("BACK-019", "ready", depends_on=["BACK-017", "BACK-018"]),
        ])
        assert check_dependency_violations(bl) == []

    def test_lightgbm_blocked_if_ic_phase2_not_shipped(self):
        """BACK-016 (LightGBM) depends on BACK-011 (IC Phase 2)."""
        bl = _backlog([
            _item("BACK-011", "pending"),
            _item("BACK-003", "shipped"),
            _item("BACK-016", "ready", depends_on=["BACK-011", "BACK-003"]),
        ])
        violations = check_dependency_violations(bl)
        assert any("BACK-016" in v and "BACK-011" in v for v in violations)

    def test_lightgbm_allowed_when_both_gates_shipped(self):
        bl = _backlog([
            _item("BACK-011", "shipped"),
            _item("BACK-003", "shipped"),
            _item("BACK-016", "ready", depends_on=["BACK-011", "BACK-003"]),
        ])
        assert check_dependency_violations(bl) == []


# ── TestCheckNoCycles ─────────────────────────────────────────────────────


class TestCheckNoCycles:

    def test_no_deps_no_cycle(self):
        bl = _backlog([_item("A", "pending"), _item("B", "pending")])
        assert check_no_cycles(bl) == []

    def test_linear_chain_no_cycle(self):
        bl = _backlog([
            _item("A", "pending", depends_on=["B"]),
            _item("B", "pending", depends_on=["C"]),
            _item("C", "shipped"),
        ])
        assert check_no_cycles(bl) == []

    def test_direct_cycle_detected(self):
        bl = _backlog([
            _item("A", "pending", depends_on=["B"]),
            _item("B", "pending", depends_on=["A"]),
        ])
        violations = check_no_cycles(bl)
        assert violations
        assert any("CYCLE" in v and "A" in v and "B" in v for v in violations)

    def test_three_node_cycle_detected(self):
        bl = _backlog([
            _item("A", "pending", depends_on=["B"]),
            _item("B", "pending", depends_on=["C"]),
            _item("C", "pending", depends_on=["A"]),
        ])
        violations = check_no_cycles(bl)
        assert violations and any("CYCLE" in v for v in violations)

    def test_self_loop_detected(self):
        bl = _backlog([_item("A", "pending", depends_on=["A"])])
        violations = check_no_cycles(bl)
        assert violations and any("CYCLE" in v for v in violations)

    def test_diamond_is_not_a_cycle(self):
        """A→B, A→C, B→D, C→D is a diamond, not a cycle."""
        bl = _backlog([
            _item("A", "pending", depends_on=["B", "C"]),
            _item("B", "pending", depends_on=["D"]),
            _item("C", "pending", depends_on=["D"]),
            _item("D", "shipped"),
        ])
        assert check_no_cycles(bl) == []

    def test_cycle_uses_arrow_notation(self):
        bl = _backlog([
            _item("X", "pending", depends_on=["Y"]),
            _item("Y", "pending", depends_on=["X"]),
        ])
        violations = check_no_cycles(bl)
        assert violations and "\u2192" in violations[0]


# ── TestValidateAll ───────────────────────────────────────────────────────


class TestValidateAll:

    def test_returns_validation_result(self):
        result = validate_all(_backlog([_item("F1", "pending")]))
        assert isinstance(result, ValidationResult)

    def test_ok_when_no_violations(self):
        bl = _backlog([_item("F1", "shipped"), _item("F2", "ready", depends_on=["F1"])])
        result = validate_all(bl)
        assert result.ok is True
        assert result.all_violations == []

    def test_not_ok_when_dep_violation(self):
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "pending")])
        result = validate_all(bl)
        assert result.ok is False
        assert result.dependency_violations

    def test_not_ok_when_cycle(self):
        bl = _backlog([
            _item("A", "pending", depends_on=["B"]),
            _item("B", "pending", depends_on=["A"]),
        ])
        result = validate_all(bl)
        assert result.ok is False
        assert result.cycle_violations

    def test_both_dep_and_cycle_violations_reported(self):
        bl = _backlog([
            _item("A", "pending", depends_on=["B"]),
            _item("B", "pending", depends_on=["A"]),
            _item("C", "ready", depends_on=["D"]),
            _item("D", "pending"),
        ])
        result = validate_all(bl)
        assert result.dependency_violations
        assert result.cycle_violations

    def test_all_violations_combines_both_lists(self):
        bl = _backlog([
            _item("A", "pending", depends_on=["B"]),
            _item("B", "pending", depends_on=["A"]),
        ])
        result = validate_all(bl)
        combined = result.dependency_violations + result.cycle_violations
        assert set(result.all_violations) == set(combined)

    def test_as_dict_has_required_keys(self):
        d = validate_all(_backlog([_item("F1", "pending")])).as_dict()
        assert all(k in d for k in ("ok", "dependency_violations", "cycle_violations", "total_violations"))

    def test_as_dict_total_matches_list_length(self):
        bl = _backlog([_item("F1", "ready", depends_on=["F2"]), _item("F2", "pending")])
        result = validate_all(bl)
        assert result.as_dict()["total_violations"] == len(result.all_violations)

    def test_accepts_pre_loaded_backlog(self):
        result = validate_all(backlog=_backlog([_item("F1", "shipped")]))
        assert result.ok

    def test_accepts_path_argument(self, tmp_path):
        f = tmp_path / "backlog.json"
        f.write_text(json.dumps(_backlog([_item("F1", "pending")])))
        assert validate_all(backlog_path=f).ok


# ── TestRealBacklogCompliance ─────────────────────────────────────────────


_BACKLOG_FILE = os.path.join(PROJECT_ROOT, "chief-decifer", "state", "backlog.json")


class TestRealBacklogCompliance:
    """
    Smoke tests against the actual chief-decifer/state/backlog.json.

    All Phase E items start as `pending` — no enforcement fires at creation.
    These tests catch regressions if someone manually moves a feature to
    ready/in_progress before its upstream dependencies are terminal.

    Skipped when chief-decifer/state/backlog.json does not exist.
    """

    @pytest.fixture(autouse=True)
    def require_backlog(self):
        if not os.path.exists(_BACKLOG_FILE):
            pytest.skip("chief-decifer/state/backlog.json not present — skipping real-backlog smoke tests")

    def test_real_backlog_loads_without_error(self):
        bl = load_backlog()
        assert isinstance(bl, dict) and "items" in bl

    def test_real_backlog_has_phase_e(self):
        phases = load_backlog().get("wip_policy", {}).get("phases", {})
        assert "E" in phases, "wip_policy.phases is missing Phase E"

    def test_real_backlog_phase_e_items_exist(self):
        ids = {item["id"] for item in load_backlog().get("items", [])}
        for expected in ["BACK-016", "BACK-017", "BACK-018", "BACK-019"]:
            assert expected in ids, f"{expected} missing from backlog"

    def test_real_backlog_phase_e_dependency_chain(self):
        """Verify the exact dependency chain: LightGBM → SHAP, LSTM → GT-Score."""
        items = {item["id"]: item for item in load_backlog().get("items", [])}
        assert "BACK-016" in items["BACK-017"]["depends_on"], \
            "BACK-017 (SHAP) must depend on BACK-016 (LightGBM)"
        assert "BACK-016" in items["BACK-018"]["depends_on"], \
            "BACK-018 (LSTM) must depend on BACK-016 (LightGBM)"
        assert "BACK-017" in items["BACK-019"]["depends_on"], \
            "BACK-019 (GT-Score) must depend on BACK-017 (SHAP)"
        assert "BACK-018" in items["BACK-019"]["depends_on"], \
            "BACK-019 (GT-Score) must depend on BACK-018 (LSTM)"

    def test_real_backlog_all_items_have_depends_on(self):
        """Every item must have a `depends_on` list (uniform schema)."""
        for item in load_backlog().get("items", []):
            assert "depends_on" in item, \
                f"Item {item.get('id')} is missing `depends_on` field"
            assert isinstance(item["depends_on"], list), \
                f"Item {item.get('id')} has `depends_on` that is not a list"

    def test_real_backlog_no_legacy_dependencies_field(self):
        """The old `dependencies` field must be absent after migration."""
        for item in load_backlog().get("items", []):
            assert "dependencies" not in item, \
                f"Item {item.get('id')} still has legacy `dependencies` field — rename to `depends_on`"

    def test_real_backlog_terminal_statuses_includes_superseded(self):
        terminal = load_backlog().get("wip_policy", {}).get("terminal_statuses", [])
        assert "superseded" in terminal, \
            "terminal_statuses must include 'superseded'"

    def test_real_backlog_no_dependency_violations(self):
        bl = load_backlog()
        result = validate_all(bl)
        assert result.dependency_violations == [], (
            "backlog.json has dependency violations:\n"
            + "\n".join(result.dependency_violations)
        )

    def test_real_backlog_no_cycles(self):
        bl = load_backlog()
        result = validate_all(bl)
        assert result.cycle_violations == [], (
            "backlog.json has dependency cycles:\n"
            + "\n".join(result.cycle_violations)
        )

    def test_real_backlog_validate_all_ok(self):
        bl = load_backlog()
        result = validate_all(bl)
        assert result.ok, (
            "validate_all() found violations:\n"
            + "\n".join(result.all_violations)
        )
