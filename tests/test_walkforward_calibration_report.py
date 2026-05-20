"""
tests/test_walkforward_calibration_report.py — Walk-forward calibration tests.

Covers:
  (a) Inactive dims (pead, analyst_revision) have proposed weight = 0
  (b) overnight_drift is classified BLOCKED_NEGATIVE_BOTH_CRITICAL, weight = 0
  (c) Sign-flip dims (candidate+, exec-) are flagged, weights preserved
  (d) squeeze (candidate-, exec+) is SIGN_FLIP_EXEC_POSITIVE_CAND_NEGATIVE, weight = 0
  (e) Execution IC cannot increase any weight above candidate-derived level
  (f) Proposed weights sum to 1.0 (within epsilon)
  (g) ic_weights.json not modified after running report
  (h) Proposed output is a different file from ic_weights.json
  (i) Weight delta is correctly computed
  (j) Report JSON has all required top-level keys
  (k) Advisory cap applied for statistically significant sign-flip
  (l) Stability metrics present for all DIMENSIONS
  (m) Report activation_status says PROPOSAL_ONLY
  (n) Confirmed positive dims (both sources agree) correctly identified
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Stub heavy deps
for _mod in [
    "ib_async", "ib_insync", "anthropic", "praw", "feedparser",
    "tvDatafeed", "requests_html", "schedule", "colorama",
]:
    sys.modules.setdefault(_mod, MagicMock())

import config as _cfg_mod

_cfg_stub = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-6",
    "max_tokens": 1000,
    "signals_log": "/dev/null",
    "audit_log": "/dev/null",
    "training_records": "/dev/null",
    "ic_calculator": {},
}
if hasattr(_cfg_mod, "CONFIG"):
    for k, v in _cfg_stub.items():
        _cfg_mod.CONFIG.setdefault(k, v)
else:
    _cfg_mod.CONFIG = _cfg_stub

from scripts.walkforward_calibration_report import (  # noqa: E402
    C_BLOCKED_CRITICAL,
    C_CANDIDATE_ONLY,
    C_CONFIRMED,
    C_INACTIVE,
    C_NEGATIVE_BOTH,
    C_SIGN_FLIP_EXEC_NEG,
    C_SIGN_FLIP_EXEC_NEG_SIG,
    C_SIGN_FLIP_EXEC_POS,
    EXEC_SIG_P,
    MIN_EXEC_N,
    _CANDIDATE_IC_FILE,
    _PROPOSED_FILE,
    _advisory_weight,
    _classify,
    _compute_stability,
    _recommendations,
    _renormalize,
)
from ic.constants import BASELINE_WEIGHTS, DIMENSIONS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _exec(ic, p_value=0.5, n=177, flag="NEGATIVE"):
    return {"ic": ic, "p_value": p_value, "n": n, "flag": flag}


# ── (a) Inactive dimensions ────────────────────────────────────────────────────

def test_inactive_dim_candidate_zero_exec_zero_classified_inactive():
    assert _classify("pead", 0.0, 0.0, 1.0, 176) == C_INACTIVE


def test_inactive_dim_candidate_zero_exec_none_classified_inactive():
    assert _classify("analyst_revision", 0.0, None, None, 0) == C_INACTIVE


def test_inactive_advisory_weight_is_zero():
    w, action = _advisory_weight(C_INACTIVE, 0.0, "pead", None)
    assert w == 0.0


# ── (b) overnight_drift blocked ───────────────────────────────────────────────

def test_overnight_drift_blocked_when_both_negative_and_exec_significant():
    """Both sources negative, exec p=0.009 < 0.05 → BLOCKED_NEGATIVE_BOTH_CRITICAL."""
    classification = _classify(
        "overnight_drift",
        cand_ic=-0.076,
        exec_ic=-0.199,
        exec_p=0.009,
        exec_n=170,
    )
    assert classification == C_BLOCKED_CRITICAL


def test_blocked_critical_weight_is_zero():
    w, action = _advisory_weight(C_BLOCKED_CRITICAL, 0.0, "overnight_drift", 0.009)
    assert w == 0.0


def test_blocked_with_positive_candidate_weight_still_zeroed():
    """Even if candidate weight were non-zero, BLOCKED means weight = 0."""
    w, _ = _advisory_weight(C_BLOCKED_CRITICAL, 0.15, "overnight_drift", 0.009)
    assert w == 0.0


# ── (c) Sign-flip dims flagged, weights preserved ────────────────────────────

def test_sign_flip_exec_neg_not_significant_classified_correctly():
    """Candidate positive, exec negative, p=0.76 → SIGN_FLIP_EXEC_NEGATIVE."""
    cls = _classify("news", cand_ic=0.122, exec_ic=-0.023, exec_p=0.76, exec_n=177)
    assert cls == C_SIGN_FLIP_EXEC_NEG


def test_sign_flip_preserves_candidate_weight():
    """Non-significant sign flip preserves candidate-derived weight."""
    cand_weight = 0.40
    w, action = _advisory_weight(C_SIGN_FLIP_EXEC_NEG, cand_weight, "news", 0.76)
    assert w == pytest.approx(cand_weight)
    assert action == "PRESERVED_CANDIDATE_WEIGHT"


@pytest.mark.parametrize("dim,cand_ic,exec_ic,exec_p", [
    ("news", 0.122, -0.023, 0.760),
    ("social", 0.070, -0.010, 0.900),
    ("trend", 0.022, -0.001, 0.986),
    ("reversion", 0.021, -0.098, 0.195),
    ("breakout", 0.012, -0.067, 0.376),
    ("short_squeeze", 0.011, -0.104, 0.172),
])
def test_all_sign_flip_dims_flagged(dim, cand_ic, exec_ic, exec_p):
    cls = _classify(dim, cand_ic, exec_ic, exec_p, 170)
    assert cls == C_SIGN_FLIP_EXEC_NEG, f"{dim}: expected SIGN_FLIP, got {cls}"


# ── (d) squeeze — candidate negative, execution positive ──────────────────────

def test_squeeze_sign_flip_exec_positive_classified():
    cls = _classify("squeeze", cand_ic=-0.046, exec_ic=0.100, exec_p=0.185, exec_n=177)
    assert cls == C_SIGN_FLIP_EXEC_POS


def test_squeeze_sign_flip_exec_positive_weight_stays_zero():
    """Execution IC cannot increase a weight above candidate level (candidate is negative → 0)."""
    w, _ = _advisory_weight(C_SIGN_FLIP_EXEC_POS, 0.0, "squeeze", 0.185)
    assert w == 0.0


# ── (e) Execution IC cannot increase weights ──────────────────────────────────

def test_exec_ic_advisory_cannot_produce_weight_above_candidate():
    """For every classification, proposed weight ≤ candidate weight."""
    for cls in (C_SIGN_FLIP_EXEC_NEG, C_CONFIRMED, C_CANDIDATE_ONLY):
        cand_w = 0.35
        w, _ = _advisory_weight(cls, cand_w, "news", 0.5)
        assert w <= cand_w + 1e-9, f"{cls}: proposed {w} > candidate {cand_w}"


# ── (f) Proposed weights sum to 1.0 ──────────────────────────────────────────

def test_renormalize_sums_to_one():
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    result = _renormalize(weights)
    assert sum(result.values()) == pytest.approx(1.0, abs=1e-6)


def test_renormalize_zero_total_returns_unchanged():
    """All-zero weights should not crash."""
    weights = {"a": 0.0, "b": 0.0}
    result = _renormalize(weights)
    assert all(v == 0.0 for v in result.values())


def test_renormalize_preserves_relative_proportions():
    weights = {"a": 0.4, "b": 0.6}
    result = _renormalize(weights)
    assert result["a"] == pytest.approx(0.4, abs=1e-5)
    assert result["b"] == pytest.approx(0.6, abs=1e-5)


# ── (g) ic_weights.json not modified ─────────────────────────────────────────

def test_ic_weights_file_unchanged_after_run(tmp_path):
    import scripts.walkforward_calibration_report as wcr

    # Create minimal fake ic_weights.json
    ic_content = {
        "raw_ic": {d: 0.0 for d in DIMENSIONS},
        "weights": {d: 0.0 for d in DIMENSIONS},
        "n_records": 100,
        "n_independent_dates": 5,
        "advisory_only": True,
    }
    ic_path = tmp_path / "ic_weights.json"
    ic_path.write_text(json.dumps(ic_content))

    exec_path = tmp_path / "signal_validation_report.json"
    exec_path.write_text(json.dumps({"dim_ic": {}}))

    history_path = tmp_path / "ic_weights_live_history.jsonl"
    history_path.write_text("")

    proposed_path = tmp_path / "proposed.json"

    orig_cand = wcr._CANDIDATE_IC_FILE
    orig_exec = wcr._EXEC_IC_FILE
    orig_hist = wcr._IC_HISTORY_FILE
    orig_prop = wcr._PROPOSED_FILE
    try:
        wcr._CANDIDATE_IC_FILE = ic_path
        wcr._EXEC_IC_FILE = exec_path
        wcr._IC_HISTORY_FILE = history_path
        wcr._PROPOSED_FILE = proposed_path
        wcr.main()
        written = json.loads(ic_path.read_text())
        assert written == ic_content, "ic_weights.json must not be modified"
    finally:
        wcr._CANDIDATE_IC_FILE = orig_cand
        wcr._EXEC_IC_FILE = orig_exec
        wcr._IC_HISTORY_FILE = orig_hist
        wcr._PROPOSED_FILE = orig_prop


# ── (h) Proposed output is a different file ───────────────────────────────────

def test_proposed_file_path_differs_from_candidate_file():
    assert _PROPOSED_FILE != _CANDIDATE_IC_FILE
    assert "proposed" in str(_PROPOSED_FILE)
    assert "ic_weights.json" not in str(_PROPOSED_FILE)


# ── (i) Weight delta correctly computed ───────────────────────────────────────

def test_weight_delta_is_proposed_minus_current():
    """Delta = proposed_weight - candidate_weight."""
    # SIGN_FLIP_EXEC_NEG preserves weight → delta = 0
    cand_w = 0.40
    prop_w, _ = _advisory_weight(C_SIGN_FLIP_EXEC_NEG, cand_w, "news", 0.76)
    delta = round(prop_w - cand_w, 4)
    assert delta == pytest.approx(0.0)


def test_weight_delta_negative_for_capped_dimension():
    """When advisory cap reduces weight below candidate, delta is negative."""
    # Artificial case: candidate weight 0.30, baseline 0.12 → cap at 0.12
    cand_w = 0.30
    w, action = _advisory_weight(C_SIGN_FLIP_EXEC_NEG_SIG, cand_w, "trend", 0.01)
    baseline_cap = BASELINE_WEIGHTS.get("trend", 0.0)
    if cand_w > baseline_cap:
        assert w <= baseline_cap
        delta = w - cand_w
        assert delta < 0


# ── (j) Report JSON structure ─────────────────────────────────────────────────

def test_report_has_all_required_keys(tmp_path):
    import scripts.walkforward_calibration_report as wcr

    ic_content = {
        "raw_ic": {d: 0.0 for d in DIMENSIONS},
        "weights": {d: 0.0 for d in DIMENSIONS},
        "n_records": 100,
        "n_independent_dates": 5,
        "advisory_only": True,
    }
    ic_path = tmp_path / "ic_weights.json"
    ic_path.write_text(json.dumps(ic_content))
    exec_path = tmp_path / "svr.json"
    exec_path.write_text(json.dumps({"dim_ic": {}}))
    hist_path = tmp_path / "hist.jsonl"
    hist_path.write_text("")
    proposed_path = tmp_path / "proposed.json"

    orig_c, orig_e, orig_h, orig_p = (
        wcr._CANDIDATE_IC_FILE, wcr._EXEC_IC_FILE,
        wcr._IC_HISTORY_FILE, wcr._PROPOSED_FILE)
    try:
        wcr._CANDIDATE_IC_FILE = ic_path
        wcr._EXEC_IC_FILE = exec_path
        wcr._IC_HISTORY_FILE = hist_path
        wcr._PROPOSED_FILE = proposed_path
        wcr.main()
        report = json.loads(proposed_path.read_text())
    finally:
        wcr._CANDIDATE_IC_FILE = orig_c
        wcr._EXEC_IC_FILE = orig_e
        wcr._IC_HISTORY_FILE = orig_h
        wcr._PROPOSED_FILE = orig_p

    required = {"meta", "dim_calibration", "proposed_weights",
                "current_weights", "recommendations"}
    assert required <= set(report.keys())


# ── (k) Advisory cap for statistically significant sign-flip ──────────────────

def test_advisory_cap_applied_when_exec_significant_and_sign_flip():
    """Candidate positive weight 0.40, exec significantly negative → cap at baseline."""
    cls = C_SIGN_FLIP_EXEC_NEG_SIG
    cand_w = 0.40
    dim = "news"
    baseline = BASELINE_WEIGHTS[dim]
    w, action = _advisory_weight(cls, cand_w, dim, 0.01)
    assert w <= baseline + 1e-9
    assert "CAPPED" in action or "BELOW_BASELINE" in action


def test_no_cap_when_already_below_baseline():
    """If candidate weight is already ≤ baseline, no cap is needed even for sig flip."""
    cls = C_SIGN_FLIP_EXEC_NEG_SIG
    dim = "breakout"
    baseline = BASELINE_WEIGHTS[dim]  # 0.09
    cand_w = 0.05  # below baseline
    w, action = _advisory_weight(cls, cand_w, dim, 0.01)
    assert w == pytest.approx(cand_w)
    assert "BELOW_BASELINE" in action


# ── (l) Stability metrics present ────────────────────────────────────────────

def test_compute_stability_returns_all_dimensions():
    entries = [
        {"raw_ic": {d: 0.05 if i % 2 == 0 else -0.02 for d in DIMENSIONS}}
        for i in range(5)
    ]
    stability = _compute_stability(entries)
    for dim in DIMENSIONS:
        assert dim in stability
        assert "mean_ic" in stability[dim]
        assert "std_ic" in stability[dim]
        assert "sign_pct" in stability[dim]
        assert "n_obs" in stability[dim]


def test_compute_stability_100_percent_positive():
    entries = [{"raw_ic": {"trend": 0.05}} for _ in range(10)]
    stability = _compute_stability(entries)
    assert stability["trend"]["sign_pct"] == 100.0


def test_compute_stability_no_data_returns_none():
    stability = _compute_stability([])
    for dim in DIMENSIONS:
        assert stability[dim]["mean_ic"] is None
        assert stability[dim]["n_obs"] == 0


# ── (m) Activation status is PROPOSAL_ONLY ───────────────────────────────────

def test_activation_status_is_proposal_only(tmp_path):
    import scripts.walkforward_calibration_report as wcr

    ic_content = {
        "raw_ic": {d: 0.0 for d in DIMENSIONS},
        "weights": {d: 0.0 for d in DIMENSIONS},
        "n_records": 100,
        "n_independent_dates": 5,
        "advisory_only": True,
    }
    for path_attr, content in [
        ("_CANDIDATE_IC_FILE", json.dumps(ic_content)),
        ("_EXEC_IC_FILE", json.dumps({"dim_ic": {}})),
        ("_IC_HISTORY_FILE", ""),
    ]:
        p = tmp_path / f"{path_attr}.json"
        p.write_text(content)
        setattr(wcr, path_attr, p)
    proposed_path = tmp_path / "proposed.json"
    wcr._PROPOSED_FILE = proposed_path
    wcr.main()
    report = json.loads(proposed_path.read_text())
    assert "PROPOSAL_ONLY" in report["meta"]["activation_status"]


# ── (n) Confirmed positive dims ───────────────────────────────────────────────

def test_confirmed_positive_classification():
    """Candidate positive, execution positive → CONFIRMED."""
    cls = _classify("news", cand_ic=0.12, exec_ic=0.08, exec_p=0.20, exec_n=50)
    assert cls == C_CONFIRMED


def test_candidate_only_classification():
    """Candidate positive, execution zero → CANDIDATE_ONLY."""
    cls = _classify("breakout", cand_ic=0.015, exec_ic=0.0, exec_p=1.0, exec_n=30)
    assert cls == C_CANDIDATE_ONLY


def test_negative_both_sources_classification():
    """Both negative, exec not significant → NEGATIVE_BOTH."""
    cls = _classify("momentum", cand_ic=-0.048, exec_ic=-0.013, exec_p=0.86, exec_n=177)
    assert cls == C_NEGATIVE_BOTH


# ── Recommendations dict ──────────────────────────────────────────────────────

def test_recommendations_structure_complete():
    dim_entries = {
        "news": {"classification": C_SIGN_FLIP_EXEC_NEG},
        "trend": {"classification": C_CONFIRMED},
        "pead": {"classification": C_INACTIVE},
        "overnight_drift": {"classification": C_BLOCKED_CRITICAL},
        "momentum": {"classification": C_NEGATIVE_BOTH},
    }
    r = _recommendations(dim_entries)
    required = {"confirmed_positive", "flagged_sign_flip",
                "blocked_critical_negative", "inactive_excluded",
                "weight_capped_by_advisory"}
    assert required <= set(r.keys())
    assert "news" in r["flagged_sign_flip"]
    assert "trend" in r["confirmed_positive"]
    assert "pead" in r["inactive_excluded"]
    assert "overnight_drift" in r["blocked_critical_negative"]
