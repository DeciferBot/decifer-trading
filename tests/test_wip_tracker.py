"""
Tests for wip_tracker.py — regression guard ensuring the WIP limit is
enforced correctly and never silently violated.

These tests use isolated backlog dicts so they never touch backlog.json on
disk and are fully offline.
"""

from __future__ import annotations

import json
import os
import sys
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from wip_tracker import (
    WIPLimitViolation,
    WIPStatus,
    can_activate,
    check_wip_limit,
    get_wip_status,
    load_backlog,
    validate_wip,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _base_backlog(max_active: int = 3, items: list | None = None) -> dict:
    """Minimal backlog dict matching the structure of backlog.json."""
    return {
        "version": "1.1",
        "wip_policy": {
            "max_active": max_active,
            "active_statuses": ["in_progress"],
            "terminal_statuses": ["shipped", "validated", "resolved"],
        },
        "items": items or [],
    }


def _item(id: str, status: str, deps: list | None = None) -> dict:
    return {"id": id, "title": f"Feature {id}", "status": status,
            "depends_on": deps or []}


def _backlog_with_phases(phases: dict, items: list | None = None) -> dict:
    """Backlog dict with a wip_policy.phases section for orphaned-ref tests."""
    bl = _base_backlog(items=items)
    bl["wip_policy"]["phases"] = phases
    return bl


# ── load_backlog ──────────────────────────────────────────────────────────────


class TestLoadBacklog:

    def test_loads_valid_json(self, tmp_path):
        bl = _base_backlog()
        f = tmp_path / "backlog.json"
        f.write_text(json.dumps(bl))
        result = load_backlog(f)
        assert result["version"] == "1.1"

    def test_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_backlog(tmp_path / "nonexistent.json")

    def test_raises_on_malformed_json(self, tmp_path):
        f = tmp_path / "backlog.json"
        f.write_text("{not: valid json")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_backlog(f)


# ── get_wip_status ────────────────────────────────────────────────────────────


class TestGetWIPStatus:

    def test_returns_wip_status_object(self):
        bl = _base_backlog(items=[_item("F1", "ready")])
        s = get_wip_status(bl)
        assert isinstance(s, WIPStatus)

    def test_zero_active_when_no_in_progress(self):
        bl = _base_backlog(items=[
            _item("F1", "ready"),
            _item("F2", "shipped"),
        ])
        s = get_wip_status(bl)
        assert s.active_count == 0
        assert not s.over_limit
        assert s.slots_available == 3

    def test_counts_only_in_progress_items(self):
        bl = _base_backlog(items=[
            _item("F1", "in_progress"),
            _item("F2", "ready"),
            _item("F3", "shipped"),
            _item("F4", "in_progress"),
        ])
        s = get_wip_status(bl)
        assert s.active_count == 2
        assert s.slots_available == 1

    def test_over_limit_when_active_exceeds_max(self):
        bl = _base_backlog(max_active=2, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        s = get_wip_status(bl)
        assert s.over_limit is True
        assert s.active_count == 3
        assert s.slots_available == 0

    def test_exactly_at_limit_is_not_over(self):
        bl = _base_backlog(max_active=3, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        s = get_wip_status(bl)
        assert s.over_limit is False
        assert s.slots_available == 0

    def test_active_features_list_contains_in_progress_items(self):
        bl = _base_backlog(items=[
            _item("F1", "in_progress"),
            _item("F2", "ready"),
        ])
        s = get_wip_status(bl)
        ids = [f["id"] for f in s.active_features]
        assert "F1" in ids
        assert "F2" not in ids

    def test_as_dict_has_required_keys(self):
        bl = _base_backlog(items=[_item("F1", "in_progress")])
        d = get_wip_status(bl).as_dict()
        required = {"active_count", "max_active", "active_features", "over_limit", "slots_available"}
        assert required.issubset(d.keys())

    def test_missing_wip_policy_defaults_to_max_3(self):
        bl = {"version": "1.0", "items": [_item("F1", "in_progress")]}
        s = get_wip_status(bl)
        assert s.max_active == 3


# ── validate_wip ──────────────────────────────────────────────────────────────


class TestValidateWIP:

    def test_empty_backlog_has_no_violations(self):
        bl = _base_backlog()
        assert validate_wip(bl) == []

    def test_under_limit_has_no_violations(self):
        bl = _base_backlog(max_active=3, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
        ])
        assert validate_wip(bl) == []

    def test_at_limit_has_no_violations(self):
        bl = _base_backlog(max_active=3, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        assert validate_wip(bl) == []

    def test_over_limit_returns_violation(self):
        bl = _base_backlog(max_active=2, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        violations = validate_wip(bl)
        assert len(violations) == 1
        assert "WIP_LIMIT_EXCEEDED" in violations[0]

    def test_violation_message_contains_active_ids(self):
        bl = _base_backlog(max_active=1, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
        ])
        violations = validate_wip(bl)
        assert violations
        assert "F1" in violations[0]
        assert "F2" in violations[0]

    def test_violation_message_contains_count_and_limit(self):
        bl = _base_backlog(max_active=2, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        violations = validate_wip(bl)
        assert "3" in violations[0]
        assert "2" in violations[0]

    def test_non_active_statuses_do_not_trigger_violation(self):
        bl = _base_backlog(max_active=2, items=[
            _item("F1", "shipped"),
            _item("F2", "validated"),
            _item("F3", "resolved"),
            _item("F4", "ready"),
            _item("F5", "pending"),
        ])
        assert validate_wip(bl) == []

    def test_unmet_dependency_triggers_violation(self):
        bl = _base_backlog(items=[
            _item("F1", "in_progress", deps=["F2"]),
            _item("F2", "ready"),   # not terminal
        ])
        violations = validate_wip(bl)
        assert any("UNMET_DEPENDENCY" in v for v in violations)

    def test_met_dependency_no_violation(self):
        bl = _base_backlog(items=[
            _item("F1", "in_progress", deps=["F2"]),
            _item("F2", "shipped"),   # terminal
        ])
        assert validate_wip(bl) == []

    def test_missing_dependency_triggers_violation(self):
        bl = _base_backlog(items=[
            _item("F1", "in_progress", deps=["NONEXISTENT"]),
        ])
        violations = validate_wip(bl)
        assert any("MISSING_DEPENDENCY" in v for v in violations)

    def test_wip_and_dependency_violations_can_coexist(self):
        """Over limit AND an unmet dependency → both violations reported."""
        bl = _base_backlog(max_active=1, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress", deps=["F3"]),
            _item("F3", "ready"),   # not terminal
        ])
        violations = validate_wip(bl)
        assert any("WIP_LIMIT_EXCEEDED" in v for v in violations)
        assert any("UNMET_DEPENDENCY" in v for v in violations)

    # ── Deduplication checks ──────────────────────────────────────────────────

    def test_duplicate_id_triggers_violation(self):
        """IC-001 regression: same ID on two items must be caught."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Feature One", "status": "ready", "depends_on": []},
            {"id": "F1", "title": "Feature One (copy)", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_ID" in v for v in violations)
        assert any("F1" in v for v in violations if "DUPLICATE_ID" in v)

    def test_duplicate_id_violation_includes_count(self):
        bl = _base_backlog(items=[
            {"id": "DUP", "title": "Alpha", "status": "ready", "depends_on": []},
            {"id": "DUP", "title": "Beta", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        dup = next(v for v in violations if "DUPLICATE_ID" in v)
        assert "2" in dup

    def test_unique_ids_no_duplicate_id_violation(self):
        bl = _base_backlog(items=[
            _item("A1", "ready"),
            _item("A2", "ready"),
            _item("A3", "pending"),
        ])
        violations = validate_wip(bl)
        assert not any("DUPLICATE_ID" in v for v in violations)

    def test_duplicate_title_triggers_violation(self):
        """IC-001 regression: same title on two items must be caught."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "IC-Weighted Signal Scoring", "status": "ready", "depends_on": []},
            {"id": "F2", "title": "IC-Weighted Signal Scoring", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_title_case_insensitive(self):
        """Title dedup is case-insensitive to catch reformulations."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Connection Manager", "status": "ready", "depends_on": []},
            {"id": "F2", "title": "connection manager", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_title_whitespace_normalised(self):
        """Extra whitespace should not allow duplicate titles to slip through."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Account Decision Agent", "status": "ready", "depends_on": []},
            {"id": "F2", "title": "Account  Decision  Agent", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_title_violation_includes_both_ids(self):
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Telegram Bot", "status": "ready", "depends_on": []},
            {"id": "F2", "title": "Telegram Bot", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        dup = next(v for v in violations if "DUPLICATE_TITLE" in v)
        assert "F1" in dup
        assert "F2" in dup

    def test_unique_titles_no_duplicate_title_violation(self):
        bl = _base_backlog(items=[
            {"id": "A1", "title": "Scanner", "status": "ready", "depends_on": []},
            {"id": "A2", "title": "Risk Engine", "status": "ready", "depends_on": []},
            {"id": "A3", "title": "Dashboard", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        assert not any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_id_and_duplicate_title_both_reported(self):
        """Both violation types can coexist in a single bad backlog."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Same Title", "status": "ready", "depends_on": []},
            {"id": "F1", "title": "Same Title", "status": "pending", "depends_on": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_ID" in v for v in violations)
        assert any("DUPLICATE_TITLE" in v for v in violations)


# ── TestOrphanedPhaseRef ──────────────────────────────────────────────────────


class TestOrphanedPhaseRef:
    """Unit tests for the ORPHANED_PHASE_REF validation added to validate_wip()."""

    def test_orphaned_phase_ref_triggers_violation(self):
        """A feature_id in phases that has no matching item must raise ORPHANED_PHASE_REF."""
        bl = _backlog_with_phases(
            phases={"A": {"feature_ids": ["BACK-001"]}},
            items=[_item("BACK-002", "ready")],  # BACK-001 not in items
        )
        violations = validate_wip(bl)
        assert any("ORPHANED_PHASE_REF" in v for v in violations)

    def test_orphaned_phase_ref_includes_feature_id_and_phase(self):
        """Violation message must name the missing feature_id and the phase label."""
        bl = _backlog_with_phases(
            phases={"B": {"feature_ids": ["MISSING-99"]}},
            items=[_item("BACK-001", "ready")],
        )
        violations = validate_wip(bl)
        orphan = next(v for v in violations if "ORPHANED_PHASE_REF" in v)
        assert "MISSING-99" in orphan
        assert "'B'" in orphan

    def test_valid_phase_refs_no_orphan_violation(self):
        """All feature_ids present in items must produce no ORPHANED_PHASE_REF."""
        bl = _backlog_with_phases(
            phases={"A": {"feature_ids": ["BACK-001", "BACK-002"]}},
            items=[_item("BACK-001", "ready"), _item("BACK-002", "shipped")],
        )
        violations = validate_wip(bl)
        assert not any("ORPHANED_PHASE_REF" in v for v in violations)

    def test_empty_phases_no_orphan_violation(self):
        """A backlog with no phases defined must produce no ORPHANED_PHASE_REF."""
        bl = _backlog_with_phases(phases={}, items=[_item("BACK-001", "ready")])
        violations = validate_wip(bl)
        assert not any("ORPHANED_PHASE_REF" in v for v in violations)

    def test_multiple_orphaned_refs_all_reported(self):
        """Each orphaned ref across multiple phases must appear as a separate violation."""
        bl = _backlog_with_phases(
            phases={
                "A": {"feature_ids": ["MISSING-01"]},
                "B": {"feature_ids": ["MISSING-02"]},
            },
            items=[_item("BACK-001", "ready")],
        )
        violations = validate_wip(bl)
        orphans = [v for v in violations if "ORPHANED_PHASE_REF" in v]
        assert len(orphans) == 2

    def test_orphaned_phase_ref_coexists_with_other_violations(self):
        """ORPHANED_PHASE_REF and WIP_LIMIT_EXCEEDED can appear in the same result."""
        bl = _backlog_with_phases(
            phases={"A": {"feature_ids": ["GHOST-01"]}},
            items=[
                _item("F1", "in_progress"),
                _item("F2", "in_progress"),
                _item("F3", "in_progress"),
                _item("F4", "in_progress"),
            ],
        )
        bl["wip_policy"]["max_active"] = 2
        violations = validate_wip(bl)
        assert any("WIP_LIMIT_EXCEEDED" in v for v in violations)
        assert any("ORPHANED_PHASE_REF" in v for v in violations)


# ── check_wip_limit ───────────────────────────────────────────────────────────


class TestCheckWIPLimit:

    def test_does_not_raise_when_under_limit(self):
        bl = _base_backlog(max_active=3, items=[_item("F1", "in_progress")])
        check_wip_limit(bl)  # no raise

    def test_does_not_raise_at_limit(self):
        bl = _base_backlog(max_active=3, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        check_wip_limit(bl)  # no raise

    def test_raises_when_over_limit(self):
        bl = _base_backlog(max_active=2, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        with pytest.raises(WIPLimitViolation):
            check_wip_limit(bl)

    def test_violation_message_mentions_limit(self):
        bl = _base_backlog(max_active=1, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
        ])
        with pytest.raises(WIPLimitViolation) as exc_info:
            check_wip_limit(bl)
        assert "2" in str(exc_info.value)
        assert "1" in str(exc_info.value)

    def test_does_not_raise_for_unmet_dependency_alone(self):
        """check_wip_limit only raises on WIP limit, not dependency issues."""
        bl = _base_backlog(max_active=3, items=[
            _item("F1", "in_progress", deps=["F2"]),
            _item("F2", "ready"),
        ])
        check_wip_limit(bl)  # no raise — dependency is validate_wip's concern


# ── can_activate ──────────────────────────────────────────────────────────────


class TestCanActivate:

    def test_true_when_slots_available(self):
        bl = _base_backlog(max_active=3, items=[_item("F1", "in_progress")])
        assert can_activate(bl) is True

    def test_false_when_at_limit(self):
        bl = _base_backlog(max_active=3, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        assert can_activate(bl) is False

    def test_false_when_over_limit(self):
        bl = _base_backlog(max_active=2, items=[
            _item("F1", "in_progress"),
            _item("F2", "in_progress"),
            _item("F3", "in_progress"),
        ])
        assert can_activate(bl) is False

    def test_true_when_no_items_at_all(self):
        bl = _base_backlog()
        assert can_activate(bl) is True


# ── Integration: real backlog.json ────────────────────────────────────────────


class TestRealBacklogCompliance:
    """
    Smoke tests against the actual backlog.json.
    These catch regressions if someone manually sets too many features to
    in_progress or activates features with unmet dependencies.
    """

    def test_real_backlog_has_wip_policy(self):
        bl = load_backlog()
        assert "wip_policy" in bl, (
            "backlog.json is missing wip_policy section — "
            "the WIP limit cannot be enforced without it."
        )

    def test_real_backlog_wip_policy_has_max_active(self):
        bl = load_backlog()
        policy = bl.get("wip_policy", {})
        assert "max_active" in policy
        assert isinstance(policy["max_active"], int)
        assert policy["max_active"] >= 1

    def test_real_backlog_max_active_is_3_to_5(self):
        """WIP limit should be 3–5 per the mitigation strategy."""
        bl = load_backlog()
        max_active = bl["wip_policy"]["max_active"]
        assert 3 <= max_active <= 5, (
            f"wip_policy.max_active={max_active} is outside the 3–5 range "
            f"specified in the mitigation strategy."
        )

    def test_real_backlog_has_no_wip_violations(self):
        bl = load_backlog()
        violations = validate_wip(bl)
        assert violations == [], (
            "backlog.json has WIP violations:\n" + "\n".join(violations)
        )

    def test_real_backlog_items_have_required_fields(self):
        bl = load_backlog()
        for item in bl.get("items", []):
            assert "id" in item, f"Item missing 'id': {item}"
            assert "status" in item, f"Item {item.get('id')} missing 'status'"
            assert "roadmap_phase" in item or item.get("status") in ("resolved",), (
                f"Item {item.get('id')} missing 'roadmap_phase'"
            )

    def test_real_backlog_has_no_duplicate_ids(self):
        """Regression guard: duplicate IDs cause double-shipping risk (IC-001)."""
        bl = load_backlog()
        violations = validate_wip(bl)
        dup_id_violations = [v for v in violations if v.startswith("DUPLICATE_ID")]
        assert dup_id_violations == [], (
            "backlog.json contains duplicate feature IDs:\n" + "\n".join(dup_id_violations)
        )

    def test_real_backlog_has_no_duplicate_titles(self):
        """Regression guard: duplicate titles cause conflicting implementations (IC-001)."""
        bl = load_backlog()
        violations = validate_wip(bl)
        dup_title_violations = [v for v in violations if v.startswith("DUPLICATE_TITLE")]
        assert dup_title_violations == [], (
            "backlog.json contains duplicate feature titles:\n" + "\n".join(dup_title_violations)
        )


# ── Integration: BACK-015 regression guard ───────────────────────────────────


class TestRealBacklogBack015:
    """
    Regression guard: BACK-015 must exist in backlog.json as a resolved entry,
    and no wip_policy.phases reference may point to a missing item.
    """

    def test_back_015_exists(self):
        """BACK-015 must be present in backlog.json items."""
        bl = load_backlog()
        ids = [item.get("id") for item in bl.get("items", [])]
        assert "BACK-015" in ids, (
            "BACK-015 (Execution Agent) is missing from backlog.json items. "
            "Add the resolved entry as documented in 2026-04-02_execution-architecture-adr.json."
        )

    def test_back_015_is_resolved(self):
        """BACK-015 must have status='resolved'."""
        bl = load_backlog()
        item = next(
            (i for i in bl.get("items", []) if i.get("id") == "BACK-015"), None
        )
        assert item is not None, "BACK-015 not found — cannot check status."
        assert item.get("status") == "resolved", (
            f"BACK-015 has status='{item.get('status')}', expected 'resolved'."
        )

    def test_no_orphaned_phase_refs_in_real_backlog(self):
        """Every feature_id in wip_policy.phases must exist in items."""
        bl = load_backlog()
        violations = validate_wip(bl)
        orphan_violations = [v for v in violations if v.startswith("ORPHANED_PHASE_REF")]
        assert orphan_violations == [], (
            "backlog.json has orphaned phase references:\n" + "\n".join(orphan_violations)
        )
