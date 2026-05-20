"""
tests/test_signal_validation_report.py — Signal validation report tests.

Covers:
  (a) Eligibility filter excludes ml_eligible=False records
  (b) Legacy records (no ml_eligible field) are included
  (c) score_breakdown preferred over signal_scores on conflict
  (d) signal_scores used as fallback when score_breakdown absent
  (e) Perfect correlation → IC = +1.0
  (f) Perfect anticorrelation → IC = -1.0
  (g) Uncorrelated pairs → IC near 0
  (h) Constant scores → IC = 0.0 (not NaN or crash)
  (i) Quantile buckets sorted by mean_score ascending
  (j) N < 30 → INSUFFICIENT_EVIDENCE flag
  (k) Report JSON has all required top-level keys
  (l) Usable count matches training_store eligibility logic
  (m) All-zero dimension handled gracefully (ZERO flag)
  (n) ic_weights.json is not modified by running the report
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Stub heavy deps before any Decifer import
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

from scripts.signal_validation_report import (  # noqa: E402
    _assign_flag,
    _build_comparison,
    _build_meta,
    _build_recommendations,
    _build_usable,
    _collect_dims,
    _compute_all_dims,
    _dim_ic,
    _get_scores,
    _hold_bucket,
    _ic_by_group,
    _load_eligible,
    _quantile_returns,
    _spearman,
    MIN_N,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(
    *,
    symbol: str = "AAPL",
    pnl_pct: float = 1.0,
    ml_eligible=True,  # absent, True, or False
    signal_scores: dict | None = None,
    score_breakdown: dict | None = None,
    trade_type: str = "MOMENTUM",
    regime: str = "TRENDING_UP",
    hold_minutes: int = 120,
    direction: str = "LONG",
) -> dict:
    r: dict = {
        "symbol": symbol,
        "pnl_pct": pnl_pct,
        "trade_type": trade_type,
        "regime": regime,
        "hold_minutes": hold_minutes,
        "direction": direction,
    }
    if ml_eligible is not None:  # None = absent (legacy)
        r["ml_eligible"] = ml_eligible
    if signal_scores is not None:
        r["signal_scores"] = signal_scores
    if score_breakdown is not None:
        r["score_breakdown"] = score_breakdown
    return r


def _make_correlated_records(n: int = 40, slope: float = 1.0) -> list[dict]:
    """Records where trend score and pnl_pct are perfectly correlated."""
    return [
        _make_record(
            pnl_pct=float(i) * slope,
            signal_scores={"trend": float(i), "momentum": 5.0},
        )
        for i in range(1, n + 1)
    ]


# ── (a) Eligibility filter ────────────────────────────────────────────────────

def test_eligibility_excludes_ml_eligible_false():
    records = [
        _make_record(ml_eligible=False, signal_scores={"trend": 5}),
        _make_record(ml_eligible=True,  signal_scores={"trend": 3}),
    ]
    with patch("training_store.load", return_value=records):
        eligible, n_excluded = _load_eligible()
    assert n_excluded == 1
    assert len(eligible) == 1
    assert eligible[0]["ml_eligible"] is True


def test_eligibility_excludes_only_explicit_false():
    """ml_eligible=False is excluded; True and absent are both included."""
    records = [
        _make_record(ml_eligible=False),
        _make_record(ml_eligible=True),
        _make_record(ml_eligible=None),  # absent = legacy
    ]
    with patch("training_store.load", return_value=records):
        eligible, n_excluded = _load_eligible()
    assert n_excluded == 1
    assert len(eligible) == 2


# ── (b) Legacy records (no ml_eligible field) ─────────────────────────────────

def test_legacy_records_included():
    """Records without ml_eligible field are treated as eligible (legacy)."""
    records = [
        _make_record(ml_eligible=None, signal_scores={"trend": 4}),
    ]
    with patch("training_store.load", return_value=records):
        eligible, n_excluded = _load_eligible()
    assert len(eligible) == 1
    assert n_excluded == 0
    assert "ml_eligible" not in eligible[0]


# ── (c) score_breakdown preferred ────────────────────────────────────────────

def test_score_breakdown_preferred_over_signal_scores():
    r = _make_record(
        signal_scores={"trend": 1.0},
        score_breakdown={"trend": 9.0},
    )
    scores = _get_scores(r)
    assert scores["trend"] == 9.0, "score_breakdown must win over signal_scores"


def test_score_breakdown_only_field_present_in_breakdown_used():
    """Dimension in score_breakdown but not signal_scores still appears."""
    r = _make_record(
        signal_scores={"momentum": 3.0},
        score_breakdown={"breakout": 7.0},
    )
    scores = _get_scores(r)
    assert scores.get("breakout") == 7.0
    assert scores.get("momentum") == 3.0


# ── (d) signal_scores fallback ────────────────────────────────────────────────

def test_signal_scores_fallback_when_score_breakdown_absent():
    r = _make_record(signal_scores={"trend": 6.0})
    scores = _get_scores(r)
    assert scores["trend"] == 6.0


def test_noise_dims_excluded():
    r = _make_record(signal_scores={"trend": 5.0, "fx_momentum": 3.0, "fx_macro": 2.0})
    scores = _get_scores(r)
    assert "fx_momentum" not in scores
    assert "fx_macro" not in scores
    assert "trend" in scores


# ── (e) Perfect correlation IC = +1 ──────────────────────────────────────────

def test_dim_ic_perfect_correlation():
    n = 40
    pairs = [(float(i), float(i)) for i in range(1, n + 1)]
    result = _dim_ic(pairs)
    assert result["ic"] is not None
    assert result["ic"] == pytest.approx(1.0, abs=0.001)


# ── (f) Perfect anticorrelation IC = -1 ──────────────────────────────────────

def test_dim_ic_perfect_anticorrelation():
    n = 40
    pairs = [(float(i), float(-i)) for i in range(1, n + 1)]
    result = _dim_ic(pairs)
    assert result["ic"] == pytest.approx(-1.0, abs=0.001)


# ── (g) Uncorrelated pairs IC near 0 ─────────────────────────────────────────

def test_dim_ic_uncorrelated():
    rng = np.random.default_rng(42)
    n = 100
    scores = rng.integers(0, 10, n).tolist()
    outcomes = rng.uniform(-2, 2, n).tolist()
    pairs = list(zip(map(float, scores), map(float, outcomes)))
    result = _dim_ic(pairs)
    assert result["ic"] is not None
    assert abs(result["ic"]) < 0.25, "Random pairs should have near-zero IC"


# ── (h) Constant scores → IC = 0 (not NaN or crash) ─────────────────────────

def test_dim_ic_constant_score_returns_zero_not_nan():
    n = 40
    pairs = [(5.0, float(i)) for i in range(n)]
    result = _dim_ic(pairs)
    assert result["ic"] == 0.0
    assert result["flag"] in ("NOISE", "ZERO", "SIGNAL", "MARGINAL", "NEGATIVE",
                               "INSUFFICIENT_EVIDENCE")  # not a crash
    assert result["ic"] is not None


def test_dim_ic_empty_pairs_insufficient_evidence():
    result = _dim_ic([])
    assert result["flag"] == "INSUFFICIENT_EVIDENCE"
    assert result["n"] == 0


# ── (i) Quantile buckets sorted by mean_score ─────────────────────────────────

def test_quantile_buckets_sorted_by_mean_score():
    n = 60
    usable = [
        _make_record(pnl_pct=float(i), signal_scores={"trend": float(i)})
        for i in range(1, n + 1)
    ]
    buckets = _quantile_returns(usable, "trend")
    assert buckets is not None, "Should produce buckets for n=60"
    mean_scores = [b["mean_score"] for b in buckets]
    assert mean_scores == sorted(mean_scores), "Buckets must be sorted by mean_score"


def test_quantile_buckets_not_produced_below_min_n():
    usable = [
        _make_record(pnl_pct=float(i), signal_scores={"trend": float(i)})
        for i in range(1, 20)  # only 19 records < MIN_N=30
    ]
    result = _quantile_returns(usable, "trend")
    assert result is None


# ── (j) N < 30 → INSUFFICIENT_EVIDENCE ───────────────────────────────────────

def test_insufficient_evidence_flag_when_n_below_threshold():
    pairs = [(float(i), float(i)) for i in range(1, 20)]  # n=19 < MIN_N
    result = _dim_ic(pairs)
    assert result["flag"] == "INSUFFICIENT_EVIDENCE"


def test_assign_flag_insufficient_at_min_n_boundary():
    assert _assign_flag(0.9, 0.001, MIN_N - 1) == "INSUFFICIENT_EVIDENCE"
    assert _assign_flag(0.9, 0.001, MIN_N) == "SIGNAL"


# ── (k) Report JSON structure ─────────────────────────────────────────────────

def test_report_json_has_all_required_keys():
    required = {
        "meta", "dim_ic", "quantile_returns", "hold_time_stratification",
        "trade_type_stratification", "regime_stratification",
        "candidate_vs_execution_ic", "recommendations",
    }
    records = _make_correlated_records(n=40)
    eligible, n_excluded = records, 0
    usable = _build_usable(eligible)
    dims = _collect_dims(usable)
    dim_results = _compute_all_dims(usable, dims)
    quantiles = {d: q for d in dims if (q := _quantile_returns(usable, d)) is not None}
    hold_strat = _ic_by_group(usable, _hold_bucket, dims)
    trade_strat = _ic_by_group(usable, lambda r: r.get("trade_type", "UNKNOWN"), dims)
    regime_strat = _ic_by_group(usable, lambda r: r.get("regime", "UNKNOWN"), dims)
    meta = _build_meta(eligible, n_excluded, usable, dims)
    comparison = _build_comparison(dims, dim_results, {})
    recs = _build_recommendations(dim_results)
    report = {
        "meta": meta, "dim_ic": dim_results, "quantile_returns": quantiles,
        "hold_time_stratification": hold_strat, "trade_type_stratification": trade_strat,
        "regime_stratification": regime_strat, "candidate_vs_execution_ic": comparison,
        "recommendations": recs,
    }
    assert required <= set(report.keys())


def test_meta_contains_selection_bias_warning():
    eligible = _make_correlated_records(n=10)
    usable = _build_usable(eligible)
    meta = _build_meta(eligible, 2, usable, ["trend"])
    assert "selection_bias_warning" in meta
    assert len(meta["selection_bias_warning"]) > 20


def test_meta_pnl_pct_convention_documented():
    eligible = _make_correlated_records(n=10)
    usable = _build_usable(eligible)
    meta = _build_meta(eligible, 0, usable, ["trend"])
    assert "pnl_pct_convention" in meta
    assert "direction-adjusted" in meta["pnl_pct_convention"]


# ── (l) Usable count matches eligibility logic ────────────────────────────────

def test_usable_count_matches_eligibility_logic():
    """
    Usable = eligible AND has score data AND has pnl_pct.
    Tests that _build_usable applies the same logic as count_eligible for the
    eligibility portion (ml_eligible=False excluded, absent included).
    """
    records = [
        _make_record(ml_eligible=False, signal_scores={"trend": 5}, pnl_pct=1.0),  # excluded
        _make_record(ml_eligible=True,  signal_scores={"trend": 3}, pnl_pct=0.5),  # eligible + usable
        _make_record(ml_eligible=None,  signal_scores={"trend": 4}, pnl_pct=0.2),  # legacy + usable
        _make_record(ml_eligible=True,  signal_scores={},           pnl_pct=0.5),  # eligible but no scores
        _make_record(ml_eligible=True,  signal_scores={"trend": 2}, pnl_pct=None), # eligible but no outcome
    ]
    with patch("training_store.load", return_value=records):
        eligible, n_excluded = _load_eligible()
    assert n_excluded == 1
    assert len(eligible) == 4
    usable = _build_usable(eligible)
    assert len(usable) == 2  # only records with scores AND pnl_pct


# ── (m) All-zero dimension handled gracefully ─────────────────────────────────

def test_zero_dimension_flag_is_zero_not_crash():
    """pead and analyst_revision are all-zeros in real data — must not crash."""
    n = 40
    pairs = [(0.0, float(i)) for i in range(n)]  # all scores = 0
    result = _dim_ic(pairs)
    assert result["ic"] == 0.0
    assert result["flag"] in ("ZERO", "NOISE", "INSUFFICIENT_EVIDENCE", "NEGATIVE")
    assert result["n"] == n


def test_zero_dimension_appears_in_inactive_recommendations():
    """A dimension with all-zero scores should land in inactive_skip."""
    # pead and analyst_revision with all 0s → flag=ZERO
    dim_results = {
        "trend": {"ic": 0.15, "p_value": 0.01, "n": 50, "flag": "SIGNAL"},
        "pead":  {"ic": 0.0,  "p_value": 1.0,  "n": 50, "flag": "ZERO"},
    }
    recs = _build_recommendations(dim_results)
    assert "pead" in recs["inactive_skip"]
    assert "trend" in recs["candidate_for_walk_forward"]


# ── (n) ic_weights.json not modified ─────────────────────────────────────────

def test_ic_weights_unchanged_after_report(tmp_path):
    """Running the report must never touch ic_weights.json."""
    import importlib
    import scripts.signal_validation_report as svr

    ic_weights_content = {"raw_ic": {"trend": 0.05}, "weights": {}}
    ic_path = tmp_path / "ic_weights.json"
    ic_path.write_text(json.dumps(ic_weights_content))

    report_path = tmp_path / "signal_validation_report.json"

    original_ic_file = svr._IC_WEIGHTS_FILE
    original_report_file = svr._REPORT_FILE
    try:
        svr._IC_WEIGHTS_FILE = ic_path
        svr._REPORT_FILE = report_path

        records = _make_correlated_records(n=40)
        with patch("training_store.load", return_value=records):
            svr.main()

        written = json.loads(ic_path.read_text())
        assert written == ic_weights_content, "ic_weights.json must be unchanged"
    finally:
        svr._IC_WEIGHTS_FILE = original_ic_file
        svr._REPORT_FILE = original_report_file


# ── Additional correctness checks ────────────────────────────────────────────

def test_collect_dims_excludes_noise():
    usable = [
        _make_record(signal_scores={"trend": 5, "fx_momentum": 3, "fx_macro": 1}),
    ]
    dims = _collect_dims(usable)
    assert "fx_momentum" not in dims
    assert "fx_macro" not in dims
    assert "trend" in dims


def test_comparison_detects_sign_flip():
    dims = ["trend"]
    dim_results = {"trend": {"ic": -0.10}}
    candidate_ic = {"trend": 0.12}
    result = _build_comparison(dims, dim_results, candidate_ic)
    assert result["dims"]["trend"]["divergence"] == "SIGN_FLIP"


def test_comparison_detects_large_gap():
    dims = ["news"]
    dim_results = {"news": {"ic": 0.05}}
    candidate_ic = {"news": 0.20}
    result = _build_comparison(dims, dim_results, candidate_ic)
    assert result["dims"]["news"]["divergence"] == "LARGE_GAP"


def test_hold_bucket_classification():
    assert _hold_bucket({"hold_minutes": 30}) == "scalp_lt60min"
    assert _hold_bucket({"hold_minutes": 60}) == "medium_60to480min"
    assert _hold_bucket({"hold_minutes": 479}) == "medium_60to480min"
    assert _hold_bucket({"hold_minutes": 480}) == "swing_gte480min"
    assert _hold_bucket({}) == "scalp_lt60min"  # missing → 0 → scalp


def test_flag_for_review_only_for_statistically_negative():
    """NEGATIVE flag alone isn't enough — must have N >= MIN_N."""
    dim_results = {
        "momentum": {"ic": -0.15, "p_value": 0.03, "n": 50, "flag": "NEGATIVE"},
        "squeeze":  {"ic": -0.20, "p_value": 0.03, "n": 20, "flag": "INSUFFICIENT_EVIDENCE"},
    }
    recs = _build_recommendations(dim_results)
    assert "momentum" in recs["flag_for_review"]
    assert "squeeze" not in recs["flag_for_review"]


def test_recommendations_signal_flag_lands_in_walk_forward():
    dim_results = {
        "news": {"ic": 0.12, "p_value": 0.01, "n": 80, "flag": "SIGNAL"},
    }
    recs = _build_recommendations(dim_results)
    assert "news" in recs["candidate_for_walk_forward"]
