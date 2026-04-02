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
            "dependencies": deps or []}


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
            {"id": "F1", "title": "Feature One", "status": "ready", "dependencies": []},
            {"id": "F1", "title": "Feature One (copy)", "status": "pending", "dependencies": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_ID" in v for v in violations)
        assert any("F1" in v for v in violations if "DUPLICATE_ID" in v)

    def test_duplicate_id_violation_includes_count(self):
        bl = _base_backlog(items=[
            {"id": "DUP", "title": "Alpha", "status": "ready", "dependencies": []},
            {"id": "DUP", "title": "Beta", "status": "pending", "dependencies": []},
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
            {"id": "F1", "title": "IC-Weighted Signal Scoring", "status": "ready", "dependencies": []},
            {"id": "F2", "title": "IC-Weighted Signal Scoring", "status": "pending", "dependencies": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_title_case_insensitive(self):
        """Title dedup is case-insensitive to catch reformulations."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Connection Manager", "status": "ready", "dependencies": []},
            {"id": "F2", "title": "connection manager", "status": "pending", "dependencies": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_title_whitespace_normalised(self):
        """Extra whitespace should not allow duplicate titles to slip through."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Account Decision Agent", "status": "ready", "dependencies": []},
            {"id": "F2", "title": "Account  Decision  Agent", "status": "pending", "dependencies": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_title_violation_includes_both_ids(self):
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Telegram Bot", "status": "ready", "dependencies": []},
            {"id": "F2", "title": "Telegram Bot", "status": "pending", "dependencies": []},
        ])
        violations = validate_wip(bl)
        dup = next(v for v in violations if "DUPLICATE_TITLE" in v)
        assert "F1" in dup
        assert "F2" in dup

    def test_unique_titles_no_duplicate_title_violation(self):
        bl = _base_backlog(items=[
            {"id": "A1", "title": "Scanner", "status": "ready", "dependencies": []},
            {"id": "A2", "title": "Risk Engine", "status": "ready", "dependencies": []},
            {"id": "A3", "title": "Dashboard", "status": "pending", "dependencies": []},
        ])
        violations = validate_wip(bl)
        assert not any("DUPLICATE_TITLE" in v for v in violations)

    def test_duplicate_id_and_duplicate_title_both_reported(self):
        """Both violation types can coexist in a single bad backlog."""
        bl = _base_backlog(items=[
            {"id": "F1", "title": "Same Title", "status": "ready", "dependencies": []},
            {"id": "F1", "title": "Same Title", "status": "pending", "dependencies": []},
        ])
        violations = validate_wip(bl)
        assert any("DUPLICATE_ID" in v for v in violations)
        assert any("DUPLICATE_TITLE" in v for v in violations)


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
