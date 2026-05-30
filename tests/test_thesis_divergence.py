"""
test_thesis_divergence.py — Unit tests for thesis_divergence.compute_thesis_divergence.

Coverage:
- proxy up + candidate lags > threshold → thesis_intact=False
- proxy up + candidate within threshold → thesis_intact=True
- proxy negative → thesis_intact=True (divergence only fires when proxy is positive)
- no transmission_rules_fired → thesis_intact=None
- non-tailwind role → thesis_intact=None
- unrecognised rule prefix → thesis_intact=None
- proxy return unavailable in evidence → thesis_intact=None
- candidate fetch failure (Alpaca down) → thesis_intact=None, pipeline continues
- output artifact written correctly
- _rule_prefix helper strips "_to_" suffix
- _proxy_sensor_for_rules returns longest-match prefix
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser",
             "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

import thesis_divergence as td


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feed(candidates: list[dict]) -> dict:
    return {"candidates": candidates}


def _make_driver_state(evidence: dict) -> dict:
    return {"active_drivers": [], "evidence": evidence}


def _candidate(sym: str, role: str, rules: list[str]) -> dict:
    return {"symbol": sym, "role": role, "transmission_rules_fired": rules}


# Minimal evidence block that covers SMH, NVDA, ITA, IEF, USO, SPY
_BASE_EVIDENCE = {
    "smh_5d_ret":  0.0666,   # SMH +6.66%
    "nvda_5d_ret": -0.0479,
    "ita_5d_ret":  0.0555,
    "ief_5d_ret":  0.0084,
    "uso_5d_ret":  -0.0942,
    "spy_5d_ret":  0.0173,
    "iwm_5d_ret":  0.0437,
    "gld_5d_ret":  -0.011,
    "hyg_5d_ret":  0.0048,
    "uvxy_5d_ret": -0.1264,
}


# ---------------------------------------------------------------------------
# _rule_prefix helper
# ---------------------------------------------------------------------------

def test_rule_prefix_strips_to_suffix():
    assert td._rule_prefix("ai_capex_growth_to_data_centre_power") == "ai_capex_growth"


def test_rule_prefix_no_to_returns_full():
    assert td._rule_prefix("geopolitical_risk") == "geopolitical_risk"


# ---------------------------------------------------------------------------
# _proxy_sensor_for_rules
# ---------------------------------------------------------------------------

def test_proxy_sensor_ai_capex_growth():
    assert td._proxy_sensor_for_rules(["ai_capex_growth_to_data_centre_power"]) == "SMH"


def test_proxy_sensor_geopolitical():
    assert td._proxy_sensor_for_rules(["geopolitical_risk_to_defence_contractors"]) == "ITA"


def test_proxy_sensor_unknown_rule_returns_none():
    assert td._proxy_sensor_for_rules(["unknown_rule_to_whatever"]) is None


def test_proxy_sensor_picks_first_match():
    # First rule recognised wins
    result = td._proxy_sensor_for_rules(["ai_capex_growth_to_data_centre", "yields_falling_to_reits"])
    assert result == "SMH"


# ---------------------------------------------------------------------------
# Divergence logic via compute_thesis_divergence (file-level)
# ---------------------------------------------------------------------------

def _run_divergence(tmp_path, candidates, evidence, candidate_returns):
    feed_path   = str(tmp_path / "feed.json")
    driver_path = str(tmp_path / "driver.json")
    out_path    = str(tmp_path / "out.json")

    with open(feed_path, "w") as f:
        json.dump(_make_feed(candidates), f)
    with open(driver_path, "w") as f:
        json.dump(_make_driver_state(evidence), f)

    with patch.object(td, "_fetch_candidate_returns", return_value=candidate_returns):
        result = td.compute_thesis_divergence(feed_path, driver_path, out_path)

    return result, out_path


def test_diverging_candidate_flagged_false(tmp_path):
    # SMH +6.66%, VRT -5.27% 5D → lag = 6.66 - (-5.27) = 11.93pp > 5pp → False
    candidates = [_candidate("VRT", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"VRT": -0.0527})
    assert result["VRT"] is False


def test_intact_candidate_within_threshold(tmp_path):
    # SMH +6.66%, AVGO +5.0% → lag = 1.66pp < 5pp → True
    candidates = [_candidate("AVGO", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"AVGO": 0.0500})
    assert result["AVGO"] is True


def test_intact_candidate_exceeds_proxy(tmp_path):
    # SMH +6.66%, AMD +8.0% → candidate ahead of proxy → no divergence → True
    candidates = [_candidate("AMD", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"AMD": 0.0800})
    assert result["AMD"] is True


def test_negative_proxy_never_flags_divergence(tmp_path):
    # USO is negative (-9.42%) → divergence only fires when proxy > 0 → True
    candidates = [_candidate("XOM", "direct_beneficiary", ["oil_supply_shock_to_integrated_oil"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"XOM": -0.08})
    assert result["XOM"] is True


def test_at_exactly_threshold_is_intact(tmp_path):
    # SMH +6.66%, candidate at proxy - 5pp exactly = +1.66% → lag = 5.00pp → NOT > threshold → True
    candidates = [_candidate("AMD", "second_order_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    proxy_pct  = _BASE_EVIDENCE["smh_5d_ret"] * 100   # 6.66
    cand_5d    = (proxy_pct - 5.0) / 100              # exactly at threshold
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"AMD": cand_5d})
    assert result["AMD"] is True


def test_just_over_threshold_diverges(tmp_path):
    # lag = 5.01pp > 5pp → False
    candidates = [_candidate("AMD", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    proxy_pct = _BASE_EVIDENCE["smh_5d_ret"] * 100   # 6.66
    cand_5d   = (proxy_pct - 5.01) / 100
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"AMD": cand_5d})
    assert result["AMD"] is False


# ---------------------------------------------------------------------------
# Non-tailwind roles → None
# ---------------------------------------------------------------------------

def test_headwind_role_returns_none(tmp_path):
    candidates = [_candidate("REIT", "pressure_candidate", ["yields_rising_to_reits_falling"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {})
    assert result.get("REIT") is None


def test_etf_proxy_role_returns_none(tmp_path):
    candidates = [_candidate("SMH", "etf_proxy", ["ai_capex_growth_to_semiconductors"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {})
    assert result.get("SMH") is None


# ---------------------------------------------------------------------------
# Missing rules / unknown prefix → None
# ---------------------------------------------------------------------------

def test_no_rules_fired_returns_none(tmp_path):
    candidates = [{"symbol": "XYZ", "role": "direct_beneficiary", "transmission_rules_fired": []}]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {})
    assert result.get("XYZ") is None


def test_unrecognised_rule_prefix_returns_none(tmp_path):
    candidates = [_candidate("XYZ", "direct_beneficiary", ["unknown_driver_to_whatever"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {})
    assert result.get("XYZ") is None


# ---------------------------------------------------------------------------
# Data unavailable → None
# ---------------------------------------------------------------------------

def test_candidate_return_unavailable_returns_none(tmp_path):
    candidates = [_candidate("VRT", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"VRT": None})
    assert result.get("VRT") is None


def test_alpaca_fetch_fails_all_none(tmp_path):
    # Simulate total Alpaca failure — _fetch_candidate_returns catches the exception
    # and returns {sym: None} for all symbols; verify compute_thesis_divergence handles this.
    candidates = [
        _candidate("VRT",  "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"]),
        _candidate("AVGO", "direct_beneficiary", ["ai_capex_growth_to_semiconductors"]),
    ]
    # Return None for every symbol (as _fetch_candidate_returns does when Alpaca is down)
    with patch.object(td, "_fetch_candidate_returns", return_value={"VRT": None, "AVGO": None}):
        result, _ = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"VRT": None, "AVGO": None})
    # Pipeline must not raise — all symbols return None
    assert result.get("VRT") is None
    assert result.get("AVGO") is None


def test_proxy_evidence_missing_returns_none(tmp_path):
    # Remove SMH from evidence so proxy is unavailable
    evidence = {k: v for k, v in _BASE_EVIDENCE.items() if k != "smh_5d_ret"}
    candidates = [_candidate("VRT", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    result, _ = _run_divergence(tmp_path, candidates, evidence, {"VRT": -0.05})
    assert result.get("VRT") is None


# ---------------------------------------------------------------------------
# Debug artifact written correctly
# ---------------------------------------------------------------------------

def test_debug_artifact_written(tmp_path):
    candidates = [_candidate("VRT", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]
    result, out_path = _run_divergence(tmp_path, candidates, _BASE_EVIDENCE, {"VRT": -0.0527})
    assert os.path.exists(out_path)
    with open(out_path) as f:
        artifact = json.load(f)
    assert "generated_at" in artifact
    assert "detail" in artifact
    assert artifact["diverging_count"] == 1
    # Detail entry for VRT must be present
    vrt_detail = next((d for d in artifact["detail"] if d["symbol"] == "VRT"), None)
    assert vrt_detail is not None
    assert vrt_detail["thesis_intact"] is False


def test_empty_feed_returns_empty_dict(tmp_path):
    result, _ = _run_divergence(tmp_path, [], _BASE_EVIDENCE, {})
    assert result == {}


# ---------------------------------------------------------------------------
# Staleness guard — empty evidence must not overwrite existing output
# ---------------------------------------------------------------------------

def test_empty_evidence_skips_write_preserves_existing_file(tmp_path):
    """When all 5d_ret values are None, the module must not overwrite an existing file."""
    out_path = str(tmp_path / "out.json")
    # Write a valid existing artifact
    valid_artifact = {"generated_at": "2026-05-29T20:02:00Z", "intact_count": 5, "detail": []}
    with open(out_path, "w") as f:
        json.dump(valid_artifact, f)

    empty_evidence: dict = {}  # no 5d_ret keys at all
    candidates = [_candidate("VRT", "direct_beneficiary", ["ai_capex_growth_to_data_centre_power"])]

    feed_path   = str(tmp_path / "feed.json")
    driver_path = str(tmp_path / "driver.json")
    with open(feed_path, "w") as f:
        json.dump(_make_feed(candidates), f)
    with open(driver_path, "w") as f:
        json.dump(_make_driver_state(empty_evidence), f)

    with patch.object(td, "_fetch_candidate_returns", return_value={"VRT": None}):
        result = td.compute_thesis_divergence(feed_path, driver_path, out_path)

    assert result == {}
    # Existing file must be preserved — not overwritten with garbage
    with open(out_path) as f:
        on_disk = json.load(f)
    assert on_disk["intact_count"] == 5


def test_all_5d_ret_none_skips_write(tmp_path):
    """Evidence block with all None values (not absent) also triggers the guard."""
    out_path = str(tmp_path / "out.json")
    valid_artifact = {"generated_at": "2026-05-29T18:00:00Z", "diverging_count": 3, "detail": []}
    with open(out_path, "w") as f:
        json.dump(valid_artifact, f)

    all_none_evidence = {k: None for k in _BASE_EVIDENCE}
    candidates = [_candidate("AVGO", "direct_beneficiary", ["ai_capex_growth_to_semiconductors"])]

    feed_path   = str(tmp_path / "feed.json")
    driver_path = str(tmp_path / "driver.json")
    with open(feed_path, "w") as f:
        json.dump(_make_feed(candidates), f)
    with open(driver_path, "w") as f:
        json.dump(_make_driver_state(all_none_evidence), f)

    with patch.object(td, "_fetch_candidate_returns", return_value={"AVGO": None}):
        result = td.compute_thesis_divergence(feed_path, driver_path, out_path)

    assert result == {}
    with open(out_path) as f:
        on_disk = json.load(f)
    assert on_disk["diverging_count"] == 3
