"""
Tests for alpha_validation.py — per-dimension IC analysis pipeline.
"""

import json
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from alpha_validation import (
    _clip_outliers,
    _ic_stats,
    _quintile_spread,
    _rolling_stability,
    _verdict,
    print_report,
    save_report,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_df(n: int = 200, ic: float = 0.3, seed: int = 42) -> pd.DataFrame:
    """Synthetic DataFrame with one signal dim correlated with fwd_return at ~ic."""
    rng = np.random.default_rng(seed)
    scores = rng.integers(0, 11, size=n).astype(float)
    noise = rng.standard_normal(n)
    returns = ic * scores + (1 - abs(ic)) * noise
    return pd.DataFrame({"signal": scores, "fwd_return": returns})


# ── _clip_outliers ─────────────────────────────────────────────────────────────


def test_clip_outliers_removes_extremes():
    # Outlier must be many σ beyond the bulk to be clipped reliably
    core = list(range(100))  # tight cluster 0-99
    s = pd.Series(core + [10_000.0, -10_000.0])
    clipped = _clip_outliers(s, sigma=2.0)
    assert clipped.max() < 10_000.0
    assert clipped.min() > -10_000.0


def test_clip_outliers_preserves_inliers():
    s = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5])
    clipped = _clip_outliers(s, sigma=3.0)
    pd.testing.assert_series_equal(s, clipped)


# ── _ic_stats ──────────────────────────────────────────────────────────────────


def test_ic_stats_positive_correlation():
    df = _make_df(n=500, ic=0.6)
    result = _ic_stats(df["signal"], df["fwd_return"])
    assert result["ic"] is not None
    assert result["ic"] > 0.1
    # pvalue may be None when scipy.stats is stubbed in test environment
    assert result["n"] >= 100


def test_ic_stats_negative_correlation():
    rng = np.random.default_rng(99)
    n = 300
    scores = rng.integers(1, 11, size=n).astype(float)  # no zeros so all pass mask
    returns = -0.6 * scores + 0.2 * rng.standard_normal(n)  # strong anti-correlation
    result = _ic_stats(pd.Series(scores), pd.Series(returns))
    assert result["ic"] is not None
    assert result["ic"] < -0.3


def test_ic_stats_insufficient_data():
    df = _make_df(n=10)
    result = _ic_stats(df["signal"], df["fwd_return"])
    assert result["ic"] is None
    assert result["n"] < 20


def test_ic_stats_excludes_zero_scores():
    rng = np.random.default_rng(0)
    scores = pd.Series([0.0] * 50 + list(rng.integers(1, 11, 100).astype(float)))
    returns = pd.Series(rng.standard_normal(150))
    result = _ic_stats(scores, returns)
    assert result["n"] == 100  # zeros excluded


def test_ic_stats_constant_returns_none():
    # All scores identical → constant input warning → nan IC → None
    scores = pd.Series([5.0] * 60)
    returns = pd.Series(np.random.default_rng(0).standard_normal(60))
    result = _ic_stats(scores, returns)
    assert result["ic"] is None


# ── _rolling_stability ─────────────────────────────────────────────────────────


def test_rolling_stability_returns_float_for_good_data():
    df = _make_df(n=2000, ic=0.3)
    std = _rolling_stability(df, "signal", n_windows=20)
    assert std is not None
    assert 0.0 <= std <= 1.0


def test_rolling_stability_returns_none_for_small_df():
    df = _make_df(n=50)
    result = _rolling_stability(df, "signal", n_windows=20)
    assert result is None


# ── _quintile_spread ───────────────────────────────────────────────────────────


def test_quintile_spread_positive_for_predictive_signal():
    df = _make_df(n=500, ic=0.5)
    spread = _quintile_spread(df, "signal")
    assert spread is not None
    assert spread > 0


def test_quintile_spread_none_for_insufficient_data():
    df = _make_df(n=50, ic=0.5)
    spread = _quintile_spread(df, "signal")
    assert spread is None


def test_quintile_spread_none_for_missing_dim():
    df = _make_df(n=500)
    spread = _quintile_spread(df, "nonexistent_dim")
    assert spread is None


# ── _verdict ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ic,pval,expected", [
    (0.10, 0.001, "KEEP"),
    (0.10, 0.10, "REDUCE_WEIGHT"),   # good IC but not significant
    (0.03, 0.001, "REDUCE_WEIGHT"),  # borderline IC, significant
    (0.01, 0.001, "REMOVE"),
    (-0.10, 0.001, "NEGATIVE_IC"),
    (None, None, "INSUFFICIENT_DATA"),
])
def test_verdict_classification(ic, pval, expected):
    n = 100 if ic is not None else 5
    assert _verdict(ic, pval, n) == expected


def test_verdict_insufficient_data_on_small_n():
    assert _verdict(0.20, 0.001, 10) == "INSUFFICIENT_DATA"


# ── save_report / print_report ─────────────────────────────────────────────────


def _minimal_report() -> dict:
    return {
        "generated_at": "2026-04-28T00:00:00+00:00",
        "live_ic_n": 60,
        "live_ic_updated": "2026-04-14",
        "historical_n": 600_000,
        "training_n": 63,
        "dimensions": {
            "mtf": {"ic": 0.385, "tstat": None, "pvalue": None, "n": 60,
                    "ic_stability_std": None, "quintile_spread": None,
                    "source": "live_ic_cache", "verdict": "KEEP"},
            "trend": {"ic": -0.185, "tstat": None, "pvalue": None, "n": 60,
                      "ic_stability_std": None, "quintile_spread": None,
                      "source": "live_ic_cache", "verdict": "NEGATIVE_IC"},
        },
        "summary": {
            "keep": ["mtf"],
            "reduce_weight": [],
            "remove": [],
            "negative_ic": ["trend"],
            "total_analyzed": 2,
            "equal_weights_active": True,
        },
    }


def test_save_report_writes_file():
    report = _minimal_report()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        save_report(report, path=path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["summary"]["keep"] == ["mtf"]
    finally:
        os.unlink(path)


def test_print_report_no_crash(capsys):
    print_report(_minimal_report())
    out = capsys.readouterr().out
    assert "ALPHA VALIDATION" in out
    assert "KEEP" in out
