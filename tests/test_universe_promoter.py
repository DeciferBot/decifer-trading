# Tests for universe_promoter.py — scoring formula + staleness guard.
# Runtime is isolated via temporary JSON files; no Alpaca calls.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from universe_promoter import (
    _reason_for,
    _score_row,
    load_promoted_universe,
    run_promoter,
)


# ── _score_row ────────────────────────────────────────────────────────────────


def test_score_row_weights_gap_and_volume_and_catalyst():
    """Score = w_gap·|gap|·100 + w_pm·pm_vol_ratio + w_cat·catalyst."""
    # prev_volume=390_000 → avg_per_minute=1000. minute_volume=10_000 → ratio=10x.
    snap = {"gap_pct": 0.03, "minute_volume": 10_000, "prev_volume": 390_000}
    score, comps = _score_row(snap, catalyst_score=5.0)
    # default weights: 3.0 * |0.03| * 100 = 9.0; 2.0 * 10.0 = 20.0; 2.0 * 5.0 = 10.0
    # Total = 39.0
    assert score == pytest.approx(39.0, rel=1e-3)
    assert comps["gap_pct"] == 0.03
    assert comps["pm_vol_ratio"] == 10.0
    assert comps["catalyst_score"] == 5.0


def test_score_row_handles_zero_prev_volume():
    """prev_volume=0 should not divide-by-zero; pm_vol_ratio clamps to 0."""
    snap = {"gap_pct": 0.05, "minute_volume": 100_000, "prev_volume": 0}
    score, comps = _score_row(snap, catalyst_score=0.0)
    assert comps["pm_vol_ratio"] == 0.0
    # 3.0 * 0.05 * 100 = 15.0 only
    assert score == pytest.approx(15.0, rel=1e-3)


def test_score_row_clamps_pm_vol_ratio_at_50():
    """Thin stocks can blow up pm_vol_ratio — must clamp to 50."""
    snap = {
        "gap_pct": 0.0,
        "minute_volume": 10_000_000,  # 25,641x vs prev_vol 390
        "prev_volume": 390,
    }
    score, comps = _score_row(snap, catalyst_score=0.0)
    assert comps["pm_vol_ratio"] == 50.0  # clamped
    assert score == pytest.approx(2.0 * 50.0, rel=1e-3)


def test_score_row_missing_gap_defaults_to_zero():
    """None gap_pct must not raise."""
    snap = {"gap_pct": None, "minute_volume": 0, "prev_volume": 390}
    score, comps = _score_row(snap, catalyst_score=0.0)
    assert score == 0.0
    assert comps["gap_pct"] == 0.0


# ── _reason_for ───────────────────────────────────────────────────────────────


def test_reason_for_tags_strong_components():
    comps = {"gap_pct": 0.04, "pm_vol_ratio": 3.5, "catalyst_score": 7.0}
    reason = _reason_for(comps)
    assert "gap=" in reason
    assert "relvol=" in reason
    assert "cat=" in reason


def test_reason_for_baseline_when_all_weak():
    comps = {"gap_pct": 0.001, "pm_vol_ratio": 0.5, "catalyst_score": 0.1}
    assert _reason_for(comps) == "baseline"


# ── load_promoted_universe staleness ──────────────────────────────────────────


def test_load_promoted_universe_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # No data/daily_promoted.json — should return [] (no raise).
    assert load_promoted_universe() == []


def test_load_promoted_universe_rejects_stale(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    stale_ts = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    (tmp_path / "data" / "daily_promoted.json").write_text(
        json.dumps({"promoted_at": stale_ts, "symbols": [{"ticker": "AAPL"}]})
    )
    assert load_promoted_universe(max_staleness_hours=18) == []


def test_load_promoted_universe_accepts_fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    fresh_ts = datetime.now(UTC).isoformat()
    (tmp_path / "data" / "daily_promoted.json").write_text(
        json.dumps(
            {
                "promoted_at": fresh_ts,
                "symbols": [{"ticker": "AAPL"}, {"ticker": "NVDA"}],
            }
        )
    )
    assert load_promoted_universe() == ["AAPL", "NVDA"]


# ── run_promoter end-to-end ───────────────────────────────────────────────────


def test_run_promoter_ranks_by_score(tmp_path, monkeypatch):
    """Highest-scoring symbol should be first in the output."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    committed = ["AAA", "BBB", "CCC"]
    snaps = {
        "AAA": {"gap_pct": 0.01, "minute_volume": 390, "prev_volume": 390, "price": 10.0},
        "BBB": {"gap_pct": 0.10, "minute_volume": 3_900, "prev_volume": 390, "price": 20.0},  # biggest
        "CCC": {"gap_pct": 0.00, "minute_volume": 0, "prev_volume": 390, "price": 30.0},
    }

    with patch("universe_promoter.load_committed_universe", return_value=committed), \
         patch("universe_promoter.fetch_snapshots_batched", return_value=snaps), \
         patch("universe_promoter._catalyst_score_for", return_value=0.0):
        result = run_promoter(top_n=3)

    assert result[0]["ticker"] == "BBB"
    assert result[0]["score"] > result[1]["score"] >= result[2]["score"]
    # File should be written
    assert (tmp_path / "data" / "daily_promoted.json").exists()


def test_run_promoter_handles_empty_committed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("universe_promoter.load_committed_universe", return_value=[]):
        assert run_promoter() == []
