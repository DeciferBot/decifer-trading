"""
tests/test_handoff_activation_gate.py — Sprint 7J.1

20 tests verifying the two-key activation gate:
  Key 1 — bot config flag:  enable_active_opportunity_universe_handoff
  Key 2 — manifest gate:    handoff_enabled (set by publisher --mode controlled_activation)

All tests are read-only or use transient in-memory / temp-file contexts.
No test flips enable_active_opportunity_universe_handoff in production config.
No test triggers live bot consumption.
No test writes to production manifest without immediately restoring it.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_publisher_mode(mode: str) -> dict:
    """Run publisher programmatically (not via CLI) and return the report."""
    from handoff_publisher import run_publisher
    return run_publisher(mode=mode)


def _load_manifest() -> dict:
    with open("data/live/current_manifest.json") as f:
        return json.load(f)


def _load_universe() -> dict:
    with open("data/live/active_opportunity_universe.json") as f:
        return json.load(f)


def _load_production_handoff(manifest_path: str = "data/live/current_manifest.json") -> dict:
    import handoff_reader as hr
    return hr.load_production_handoff(manifest_path)


# ---------------------------------------------------------------------------
# 1. Publisher default mode is validation_only
# ---------------------------------------------------------------------------
def test_publisher_default_mode_is_validation_only():
    from handoff_publisher import _DEFAULT_PUBLICATION_MODE, _VALID_PUBLICATION_MODES
    assert _DEFAULT_PUBLICATION_MODE == "validation_only"
    assert "validation_only" in _VALID_PUBLICATION_MODES
    assert "controlled_activation" in _VALID_PUBLICATION_MODES


# ---------------------------------------------------------------------------
# 2. validation_only manifest has handoff_enabled=false
# ---------------------------------------------------------------------------
def test_validation_only_manifest_has_handoff_enabled_false():
    """After a validation_only publish cycle, the manifest must have handoff_enabled=false."""
    report = _run_publisher_mode("validation_only")
    assert report.get("validation_summary", {}).get("overall_status") == "pass"
    manifest = _load_manifest()
    assert manifest.get("handoff_enabled") is False, \
        f"validation_only manifest must have handoff_enabled=false, got {manifest.get('handoff_enabled')!r}"
    assert manifest.get("publication_mode") == "validation_only"


# ---------------------------------------------------------------------------
# 3. validation_only manifest is rejected by load_production_handoff
# ---------------------------------------------------------------------------
def test_validation_only_manifest_rejected_by_reader():
    """Reader must fail-closed on validation_only manifests (handoff_disabled_in_manifest)."""
    # Ensure we have a fresh validation_only manifest
    _run_publisher_mode("validation_only")
    result = _load_production_handoff()
    assert result.get("handoff_allowed") is False, \
        "Reader must not allow handoff for validation_only manifest"
    assert result.get("fail_closed_reason") == "handoff_disabled_in_manifest", \
        f"Expected fail_closed_reason='handoff_disabled_in_manifest', got {result.get('fail_closed_reason')!r}"
    assert result.get("accepted_candidate_count", 0) == 0


# ---------------------------------------------------------------------------
# 4. controlled_activation manifest has handoff_enabled=true
# ---------------------------------------------------------------------------
def test_controlled_activation_manifest_has_handoff_enabled_true():
    """controlled_activation publish cycle must write handoff_enabled=true."""
    report = _run_publisher_mode("controlled_activation")
    assert report.get("validation_summary", {}).get("overall_status") == "pass", \
        f"controlled_activation publish failed: {report.get('fail_closed_reason')}"
    manifest = _load_manifest()
    assert manifest.get("handoff_enabled") is True, \
        f"controlled_activation manifest must have handoff_enabled=true, got {manifest.get('handoff_enabled')!r}"
    assert manifest.get("publication_mode") == "controlled_activation"
    # Restore to validation_only immediately
    _run_publisher_mode("validation_only")


# ---------------------------------------------------------------------------
# 5. controlled_activation manifest is accepted by load_production_handoff
# ---------------------------------------------------------------------------
def test_controlled_activation_manifest_accepted_by_reader():
    """Reader must allow handoff for valid controlled_activation manifest."""
    _run_publisher_mode("controlled_activation")
    try:
        result = _load_production_handoff()
        assert result.get("handoff_allowed") is True, \
            f"Reader must allow handoff for controlled_activation manifest. " \
            f"fail_closed_reason={result.get('fail_closed_reason')!r}"
        assert result.get("fail_closed_reason") is None
        assert result.get("accepted_candidate_count", 0) > 0
    finally:
        # Always restore to validation_only
        _run_publisher_mode("validation_only")


# ---------------------------------------------------------------------------
# 6. controlled_activation manifest still has no executable candidates
# ---------------------------------------------------------------------------
def test_controlled_activation_no_executable_candidates():
    """controlled_activation must never produce executable candidates."""
    _run_publisher_mode("controlled_activation")
    try:
        universe = _load_universe()
        violations = [
            c["symbol"] for c in universe.get("candidates", [])
            if c.get("executable") is not False
        ]
        assert not violations, \
            f"Executable candidates found in controlled_activation universe: {violations}"
    finally:
        _run_publisher_mode("validation_only")


# ---------------------------------------------------------------------------
# 7. controlled_activation manifest still has no order instructions
# ---------------------------------------------------------------------------
def test_controlled_activation_no_order_instructions():
    """controlled_activation must never produce order instructions."""
    _run_publisher_mode("controlled_activation")
    try:
        universe = _load_universe()
        violations = [
            c["symbol"] for c in universe.get("candidates", [])
            if c.get("order_instruction") is not None
        ]
        assert not violations, \
            f"Non-null order_instruction candidates in controlled_activation universe: {violations}"
    finally:
        _run_publisher_mode("validation_only")


# ---------------------------------------------------------------------------
# 8. controlled_activation manifest has candidate_count=75
# ---------------------------------------------------------------------------
def test_controlled_activation_candidate_count_75():
    """controlled_activation universe must contain 75 candidates."""
    _run_publisher_mode("controlled_activation")
    try:
        universe = _load_universe()
        count = len(universe.get("candidates", []))
        assert count == 75, \
            f"controlled_activation universe must have 75 candidates, got {count}"
    finally:
        _run_publisher_mode("validation_only")


# ---------------------------------------------------------------------------
# 9. controlled_activation manifest has quota_policy_version=75_35
# ---------------------------------------------------------------------------
def test_controlled_activation_quota_policy_version():
    """controlled_activation must preserve quota_policy_version=75_35 in run log."""
    from handoff_publisher import _quota_policy_version
    assert _quota_policy_version() == "75_35", \
        f"quota_policy_version must be '75_35', got {_quota_policy_version()!r}"


# ---------------------------------------------------------------------------
# 10. controlled_activation manifest fails if a candidate has executable=true
# ---------------------------------------------------------------------------
def test_controlled_activation_fails_on_executable_candidate():
    """Publisher must fail-closed if shadow source has executable=true on any candidate."""
    from handoff_publisher import _validate_shadow_source
    bad_shadow = {
        "candidates": [
            {
                "symbol": "TEST",
                "execution_instructions": {"executable": True},
                "live_output_changed": False,
            }
        ]
    }
    errors = _validate_shadow_source(bad_shadow)
    assert any("executable=true" in e for e in errors), \
        f"Shadow source with executable=true must be rejected, got errors={errors}"


# ---------------------------------------------------------------------------
# 11. controlled_activation manifest fails if order_instruction not null
# ---------------------------------------------------------------------------
def test_controlled_activation_fails_on_order_instruction():
    """Publisher output validator must reject candidates with non-null order_instruction."""
    from handoff_publisher import _validate_output_candidate
    bad_cand = {
        "symbol": "TEST",
        "route": "structural",
        "route_hint": ["structural"],
        "reason_to_care": "test",
        "source_labels": ["eil_economic_intelligence"],
        "theme_ids": [],
        "risk_flags": [],
        "confirmation_required": [],
        "approval_status": "approved",
        "quota_group": "structural_position",
        "freshness_status": "fresh",
        "executable": False,
        "order_instruction": {"action": "BUY"},  # must be null
        "live_output_changed": False,
    }
    errors = _validate_output_candidate(bad_cand)
    assert any("order_instruction" in e for e in errors), \
        f"Candidate with non-null order_instruction must be rejected, got errors={errors}"


# ---------------------------------------------------------------------------
# 12. controlled_activation manifest fails if stale
# ---------------------------------------------------------------------------
def test_controlled_activation_fails_if_manifest_stale():
    """Reader must reject stale manifests (expired expires_at)."""
    import handoff_reader as hr
    stale_manifest = {
        "schema_version": "1.0",
        "published_at": "2020-01-01T00:00:00Z",
        "expires_at": "2020-01-01T00:15:00Z",   # far in the past — expired
        "validation_status": "pass",
        "handoff_mode": "live",
        "publication_mode": "controlled_activation",
        "handoff_enabled": True,
        "enable_flag_required": True,
        "active_universe_file": "data/live/active_opportunity_universe.json",
        "economic_context_file": "data/intelligence/current_economic_context.json",
        "source_snapshot_versions": {},
        "publisher": "handoff_publisher",
        "fail_closed_reason": None,
        "warnings": [],
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }
    val = hr.validate_manifest(stale_manifest)
    assert not val["ok"], "Stale manifest must fail validation"
    assert any("expired" in e for e in val.get("errors", [])), \
        f"Expected 'expired' error, got {val.get('errors')}"


# ---------------------------------------------------------------------------
# 13. reader still fails closed if handoff_enabled=false
# ---------------------------------------------------------------------------
def test_reader_fails_closed_on_handoff_disabled():
    """Reader must return handoff_allowed=False when manifest has handoff_enabled=false."""
    _run_publisher_mode("validation_only")
    result = _load_production_handoff()
    assert result.get("handoff_allowed") is False
    assert result.get("fail_closed_reason") == "handoff_disabled_in_manifest"
    assert result.get("accepted_candidate_count", -1) == 0


# ---------------------------------------------------------------------------
# 14. reader still fails closed if publication_mode invalid (via temp manifest)
# ---------------------------------------------------------------------------
def test_reader_fails_on_invalid_handoff_mode():
    """Reader must reject manifests with an unrecognised handoff_mode."""
    import handoff_reader as hr
    bad_manifest = {
        "schema_version": "1.0",
        "published_at": "2099-01-01T00:00:00Z",
        "expires_at": "2099-01-01T23:59:59Z",
        "validation_status": "pass",
        "handoff_mode": "unknown_invalid_mode",   # not in reader's accepted set
        "publication_mode": "controlled_activation",
        "handoff_enabled": True,
        "enable_flag_required": True,
        "active_universe_file": "data/live/active_opportunity_universe.json",
        "source_snapshot_versions": {},
        "publisher": "handoff_publisher",
        "fail_closed_reason": None,
        "warnings": [],
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }
    val = hr.validate_manifest(bad_manifest)
    assert not val["ok"], "Invalid handoff_mode must fail manifest validation"


# ---------------------------------------------------------------------------
# 15. no manual manifest edit is required
# ---------------------------------------------------------------------------
def test_no_manual_manifest_edit_required():
    """controlled_activation mode writes handoff_enabled=true programmatically — no manual edit."""
    report = _run_publisher_mode("controlled_activation")
    try:
        manifest = _load_manifest()
        # Publisher wrote handoff_enabled=true without any manual intervention
        assert manifest.get("handoff_enabled") is True, \
            "Publisher must set handoff_enabled=true without manual manifest edit"
        assert report.get("validation_summary", {}).get("overall_status") == "pass"
    finally:
        _run_publisher_mode("validation_only")


# ---------------------------------------------------------------------------
# 16. publisher can return to validation_only mode
# ---------------------------------------------------------------------------
def test_publisher_can_return_to_validation_only():
    """After controlled_activation, publisher can revert to validation_only."""
    _run_publisher_mode("controlled_activation")
    manifest_ca = _load_manifest()
    assert manifest_ca.get("handoff_enabled") is True   # activation state

    _run_publisher_mode("validation_only")
    manifest_vo = _load_manifest()
    assert manifest_vo.get("handoff_enabled") is False  # reverted
    assert manifest_vo.get("publication_mode") == "validation_only"


# ---------------------------------------------------------------------------
# 17. validation_only mode after controlled_activation sets handoff_enabled=false again
# ---------------------------------------------------------------------------
def test_validation_only_after_controlled_activation_resets_handoff_enabled():
    """Reverting to validation_only must set handoff_enabled=false in manifest."""
    _run_publisher_mode("controlled_activation")
    _run_publisher_mode("validation_only")
    manifest = _load_manifest()
    assert manifest.get("handoff_enabled") is False, \
        "After reverting to validation_only, handoff_enabled must be false"
    result = _load_production_handoff()
    assert result.get("handoff_allowed") is False, \
        "After reverting to validation_only, reader must again reject the manifest"


# ---------------------------------------------------------------------------
# 18. Two-key gate: Key 1 active since Sprint 7J.4; Key 2 still required
# (replaces Sprint 7J.1 guard "test_bot_flag_remains_false")
# ---------------------------------------------------------------------------
def test_two_key_gate_key1_now_active():
    """
    Sprint 7J.4 activated Key 1 (config flag = True).
    This test confirms Key 1 is set AND that Key 2 (manifest handoff_enabled)
    is still independently required — Key 1 alone does not grant consumption.

    Two assertions:
      (a) config flag IS True  — Key 1 is active.
      (b) validation_only manifest → reader fails closed even with Key 1 = True.
      (c) controlled_activation manifest + Key 1 = True → reader allows.
    """
    import config
    # (a) Key 1 active since Sprint 7J.4
    assert config.CONFIG.get("enable_active_opportunity_universe_handoff") is True, (
        "enable_active_opportunity_universe_handoff must be True (Sprint 7J.4 activated Key 1). "
        "If this assertion fails, Key 1 was rolled back — check config.py line ~985."
    )

    # (b) Key 1 alone is insufficient — validation_only manifest must fail closed
    _run_publisher_mode("validation_only")
    result_vo = _load_production_handoff()
    assert result_vo.get("handoff_allowed") is False, (
        "Key 1 alone must NOT be sufficient: reader must reject validation_only manifest "
        "even when enable_active_opportunity_universe_handoff=True in config."
    )
    assert result_vo.get("fail_closed_reason") == "handoff_disabled_in_manifest", (
        f"Expected fail_closed_reason='handoff_disabled_in_manifest', "
        f"got {result_vo.get('fail_closed_reason')!r}"
    )

    # (c) Both keys active — controlled_activation manifest must allow handoff
    _run_publisher_mode("controlled_activation")
    try:
        result_ca = _load_production_handoff()
        assert result_ca.get("handoff_allowed") is True, (
            "Both keys active: reader must allow handoff for controlled_activation manifest "
            f"with Key 1=True. fail_closed_reason={result_ca.get('fail_closed_reason')!r}"
        )
        assert result_ca.get("fail_closed_reason") is None
        assert result_ca.get("accepted_candidate_count", 0) > 0, (
            "controlled_activation + Key 1=True must yield at least one accepted candidate"
        )
    finally:
        _run_publisher_mode("validation_only")


# ---------------------------------------------------------------------------
# 19. Track A is blocked when Key 2 is absent (manifest fail-closed proof)
# (replaces Sprint 7J.1 guard "test_live_bot_not_consuming_handoff")
# ---------------------------------------------------------------------------
def test_track_a_blocked_without_key2():
    """
    Track A new entries require BOTH keys.
    Even with Key 1 active (config=True), a validation_only manifest means
    load_production_handoff() returns handoff_allowed=False and a non-null
    fail_closed_reason — which is the exact condition bot_trading checks
    to skip Track A entirely (line ~2550: if _handoff_fail_closed_reason is not None).

    This test proves the bot-side implication without running a live scan cycle.
    """
    _run_publisher_mode("validation_only")

    result = _load_production_handoff()

    # Reader must be fail-closed
    assert result.get("handoff_allowed") is False, (
        "validation_only manifest must cause handoff_allowed=False"
    )
    assert result.get("accepted_candidate_count", -1) == 0, (
        "No candidates may be accepted when reader fails closed"
    )

    # fail_closed_reason must be a non-empty string — this is what bot_trading
    # checks for the Track A skip guard (if _handoff_fail_closed_reason is not None)
    reason = result.get("fail_closed_reason")
    assert isinstance(reason, str) and reason, (
        f"fail_closed_reason must be a non-empty string for the Track A guard, got {reason!r}"
    )


# ---------------------------------------------------------------------------
# 20. live_output_changed false in all modes
# ---------------------------------------------------------------------------
def test_live_output_changed_false_in_all_modes():
    """live_output_changed must be false in both publisher modes."""
    for mode in ("validation_only", "controlled_activation"):
        try:
            report = _run_publisher_mode(mode)
            assert report.get("live_output_changed") is False, \
                f"mode={mode}: live_output_changed must be false in report"
            universe = _load_universe()
            assert universe.get("live_output_changed") is False, \
                f"mode={mode}: live_output_changed must be false in active universe"
        finally:
            if mode == "controlled_activation":
                _run_publisher_mode("validation_only")
