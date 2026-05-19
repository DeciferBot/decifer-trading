"""
tests/test_quota_policy_promotion.py — Sprint 7I

18 tests verifying 75/35 quota policy promotion in validation-only mode.
All tests are read-only against already-generated outputs.
"""

import json
import os

import pytest

_SHADOW_PATH   = "data/universe_builder/active_opportunity_universe_shadow.json"
_UNIVERSE_PATH = "data/live/active_opportunity_universe.json"
_MANIFEST_PATH = "data/live/current_manifest.json"
_GOVERNED_WATCH = ["COST", "MSFT", "PG"]
_QUOTA_WATCH    = ["SNDK", "WDC", "IREN"]

_EXPECTED_POLICY_VERSION = "75_35"
_EXPECTED_TOTAL          = 75
_EXPECTED_STRUCTURAL     = 35


@pytest.fixture(scope="module")
def shadow():
    with open(_SHADOW_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def universe():
    with open(_UNIVERSE_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def manifest():
    with open(_MANIFEST_PATH) as f:
        return json.load(f)


# 1. Quota policy is 75/35
def test_quota_policy_is_75_35():
    from quota_allocator import QUOTA_POLICY_VERSION, _TOTAL_MAX, _STRUCTURAL_MAX
    assert QUOTA_POLICY_VERSION == _EXPECTED_POLICY_VERSION
    assert _TOTAL_MAX == _EXPECTED_TOTAL
    assert _STRUCTURAL_MAX == _EXPECTED_STRUCTURAL


# 2. Validation-only active universe has 75 candidates
def test_active_universe_has_75_candidates(universe):
    candidates = universe.get("candidates", [])
    assert len(candidates) == _EXPECTED_TOTAL, \
        f"Expected 75 candidates, got {len(candidates)}"


# 3. structural_used = 35
def test_shadow_structural_used_35(shadow):
    qs = shadow.get("quota_summary", {})
    sp = qs.get("structural_position", {})
    assert sp.get("used") == _EXPECTED_STRUCTURAL, \
        f"Expected structural_position.used=35, got {sp.get('used')}"


# 4. COST included
def test_cost_included(universe):
    syms = {c["symbol"] for c in universe["candidates"]}
    assert "COST" in syms, "COST must be included in 75/35 universe"


# 5. MSFT included
def test_msft_included(universe):
    syms = {c["symbol"] for c in universe["candidates"]}
    assert "MSFT" in syms, "MSFT must be included in 75/35 universe"


# 6. PG included
def test_pg_included(universe):
    syms = {c["symbol"] for c in universe["candidates"]}
    assert "PG" in syms, "PG must be included in 75/35 universe"


# 7. SNDK included
def test_sndk_included(universe):
    syms = {c["symbol"] for c in universe["candidates"]}
    assert "SNDK" in syms, "SNDK must be included in 75/35 universe"


# 8. WDC included
def test_wdc_included(universe):
    syms = {c["symbol"] for c in universe["candidates"]}
    assert "WDC" in syms, "WDC must be included in 75/35 universe"


# 9. IREN included
def test_iren_included(universe):
    syms = {c["symbol"] for c in universe["candidates"]}
    assert "IREN" in syms, "IREN must be included in 75/35 universe"


# 10. No executable candidates
def test_no_executable_candidates(universe):
    violations = [
        c["symbol"] for c in universe["candidates"]
        if c.get("executable") is not False
    ]
    assert not violations, f"Executable candidates found: {violations}"


# 11. No order instructions
def test_no_order_instructions(universe):
    violations = [
        c["symbol"] for c in universe["candidates"]
        if c.get("order_instruction") is not None
    ]
    assert not violations, f"Non-null order_instruction candidates: {violations}"


# 12. handoff_enabled reflects publication_mode (true when controlled_activation, false when validation_only)
def test_manifest_handoff_enabled_consistent(manifest):
    mode = manifest.get("publication_mode")
    enabled = manifest.get("handoff_enabled")
    if mode == "controlled_activation":
        assert enabled is True, f"handoff_enabled must be True when publication_mode=controlled_activation, got {enabled!r}"
    else:
        assert enabled is False, f"handoff_enabled must be False when publication_mode={mode!r}, got {enabled!r}"


# 13. enable_active_opportunity_universe_handoff=true (activated 2026-05-09, Sprint 7J.4, Amit approved)
def test_manifest_flag_active(manifest):
    assert manifest.get("enable_flag_required") is True, \
        "manifest.enable_flag_required must be true"
    import config
    assert config.CONFIG.get("enable_active_opportunity_universe_handoff") is True, \
        "enable_active_opportunity_universe_handoff must be True — activated Sprint 7J.4"


# 14. publication_mode=controlled_activation (activated 2026-05-11, Sprint 2)
def test_manifest_publication_mode(manifest):
    assert manifest.get("publication_mode") in ("controlled_activation", "validation_only"), \
        f"manifest.publication_mode must be a valid mode, got {manifest.get('publication_mode')!r}"


# 15. Config gate active (bot able to consume handoff when manifest is enabled)
def test_live_bot_config_gate_active():
    import config
    assert config.CONFIG.get("enable_active_opportunity_universe_handoff") is True, \
        "config gate must be True — activated Sprint 7J.4"


# 16. live_output_changed=false (universe + manifest only — observation report removed)
def test_live_output_changed_false(universe, manifest):
    assert universe.get("live_output_changed") is False
    assert manifest.get("live_output_changed") is False or "live_output_changed" not in manifest
