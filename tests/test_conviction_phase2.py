"""
test_conviction_phase2.py — Phase 2 dimension tests for conviction_engine.py
and zone management tests for conviction_universe.py.

All file I/O and FMP calls are mocked. Tests are fast and focused.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(hours_ago: float) -> str:
    """Return ISO timestamp for `hours_ago` hours in the past."""
    dt = datetime.now(UTC) - timedelta(hours=hours_ago)
    return dt.isoformat()


def _make_event(
    symbol: str,
    materiality: str = "high",
    hours_ago: float = 12.0,
    themes_strengthened: list | None = None,
    themes_weakened: list | None = None,
    event_family: str = "earnings",
    tickers_first_order: list | None = None,
    tickers_second_order: list | None = None,
) -> dict:
    return {
        "source_published_at": _ts(hours_ago),
        "materiality": materiality,
        "themes_strengthened": themes_strengthened or [],
        "themes_weakened": themes_weakened or [],
        "event_family": event_family,
        "tickers_first_order": tickers_first_order or [symbol],
        "tickers_second_order": tickers_second_order or [],
    }


# ---------------------------------------------------------------------------
# D6 — News / catalyst
# ---------------------------------------------------------------------------

import conviction_engine as ce


def _call_d6(symbol: str, theme_id: str, events: list) -> ce.DimensionScore:
    tape = {"events": events}
    with patch.object(ce, "_read_json", return_value=tape):
        return ce._score_news_catalyst(symbol, theme_id)


class TestD6NewsCatalyst:
    def test_positive_event_within_24h_scores_positive(self):
        events = [_make_event("AAPL", materiality="high", hours_ago=6,
                               themes_strengthened=["ai_compute"],
                               tickers_first_order=["AAPL"])]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts == 12

    def test_negative_event_within_24h_scores_negative(self):
        events = [_make_event("AAPL", materiality="high", hours_ago=6,
                               themes_weakened=["ai_compute"],
                               event_family="geopolitics",
                               tickers_first_order=["AAPL"])]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts < 0

    def test_stale_event_over_7d_scores_zero(self):
        events = [_make_event("AAPL", materiality="high", hours_ago=24 * 8)]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts == 0

    def test_no_events_scores_zero(self):
        result = _call_d6("AAPL", "ai_compute", [])
        assert result.raw_pts == 0

    def test_materiality_below_threshold_not_counted(self):
        # materiality "medium" → 0.5, below the 0.7 gate
        events = [_make_event("AAPL", materiality="medium", hours_ago=6,
                               themes_strengthened=["ai_compute"])]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts == 0

    def test_low_materiality_not_counted(self):
        events = [_make_event("AAPL", materiality="low", hours_ago=6,
                               themes_strengthened=["ai_compute"])]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts == 0

    def test_positive_event_72h_scores_7(self):
        events = [_make_event("AAPL", materiality="high", hours_ago=48,
                               themes_strengthened=["ai_compute"])]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts == 7

    def test_positive_event_within_7d_scores_3(self):
        events = [_make_event("AAPL", materiality="high", hours_ago=120,
                               themes_strengthened=["ai_compute"])]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts == 3

    def test_negative_event_within_72h_scores_minus7(self):
        events = [_make_event("AAPL", materiality="high", hours_ago=48,
                               themes_weakened=["ai_compute"])]
        result = _call_d6("AAPL", "ai_compute", events)
        assert result.raw_pts == -7


# ---------------------------------------------------------------------------
# D7 — Options flow
# ---------------------------------------------------------------------------

def _call_d7(symbol: str, flow_data: dict | None, stale: bool = False) -> ce.DimensionScore:
    """
    Patch file existence, stat mtime, and read_text so we can pass flow dicts directly.
    """
    if flow_data is None:
        with patch("pathlib.Path.exists", return_value=False):
            return ce._score_options_flow(symbol)

    fresh_mtime = time.time() - (31 * 60 if stale else 60)
    mock_stat = MagicMock()
    mock_stat.st_mtime = fresh_mtime

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.stat", return_value=mock_stat), \
         patch("pathlib.Path.read_text", return_value=json.dumps(flow_data)):
        return ce._score_options_flow(symbol)


class TestD7OptionsFlow:
    def test_unusual_calls_gives_plus10(self):
        result = _call_d7("AAPL", {"unusual_calls": True, "unusual_puts": False})
        # +10 from unusual_calls
        assert result.raw_pts == 10

    def test_unusual_puts_gives_minus15(self):
        result = _call_d7("AAPL", {"unusual_calls": False, "unusual_puts": True})
        assert result.raw_pts == -15

    def test_put_penalty_greater_than_call_bonus(self):
        # Asymmetry: unusual_puts penalty (15) > unusual_calls bonus (10)
        call_result = _call_d7("AAPL", {"unusual_calls": True, "unusual_puts": False})
        put_result  = _call_d7("AAPL", {"unusual_calls": False, "unusual_puts": True})
        assert abs(put_result.raw_pts) > call_result.raw_pts

    def test_no_flow_file_scores_zero(self):
        result = _call_d7("AAPL", None)
        assert result.raw_pts == 0

    def test_no_unusual_flags_scores_zero(self):
        result = _call_d7("AAPL", {"unusual_calls": False, "unusual_puts": False,
                                   "call_expansion": 1.0, "put_expansion": 1.0})
        assert result.raw_pts == 0

    def test_stale_file_scores_zero(self):
        result = _call_d7("AAPL", {"unusual_calls": True}, stale=True)
        assert result.raw_pts == 0

    def test_both_unusual_calls_and_puts(self):
        # both set: puts fired first (-15), then calls (+10) → net -5
        result = _call_d7("AAPL", {"unusual_calls": True, "unusual_puts": True})
        assert result.raw_pts == -5

    def test_max_pts_is_12(self):
        result = _call_d7("AAPL", {"unusual_calls": True})
        assert result.max_pts == 12


# ---------------------------------------------------------------------------
# D8 — Peer network
# ---------------------------------------------------------------------------

def _make_exposure(symbol: str, bucket_id: str, status: str = "active") -> dict:
    return {"symbol": symbol, "bucket_id": bucket_id, "theme_id": "t1",
            "driver_id": "d1", "status": status, "confidence": 0.9,
            "exposure_type": "direct_beneficiary"}


def _call_d8(symbol: str, peers_in_bucket: list[str], peer_returns: dict[str, float],
             symbol_status: str = "active") -> ce.DimensionScore:
    exposures = [_make_exposure(symbol, "bucket_ai")] + \
                [_make_exposure(p, "bucket_ai") for p in peers_in_bucket]
    raw_data = {"exposures": exposures}
    all_changes = {p: peer_returns.get(p, 0.0) for p in peers_in_bucket}

    with patch.object(ce, "_read_json", return_value=raw_data):
        return ce._score_peer_network(symbol, all_changes)


class TestD8PeerNetwork:
    def test_100_pct_peers_positive_gives_plus8(self):
        result = _call_d8("NVDA", ["AMD", "INTC", "QCOM"],
                           {"AMD": 2.0, "INTC": 1.0, "QCOM": 3.0})
        assert result.raw_pts == 8

    def test_0_pct_peers_positive_gives_minus5(self):
        result = _call_d8("NVDA", ["AMD", "INTC", "QCOM"],
                           {"AMD": -1.0, "INTC": -2.0, "QCOM": -3.0})
        assert result.raw_pts == -5

    def test_no_peers_gives_zero(self):
        # No peers in the bucket (only the symbol itself)
        exposures = [_make_exposure("NVDA", "bucket_ai")]
        raw_data = {"exposures": exposures}
        with patch.object(ce, "_read_json", return_value=raw_data):
            result = ce._score_peer_network("NVDA", {"AMD": 1.0})
        assert result.raw_pts == 0

    def test_fewer_than_2_peers_gives_zero(self):
        result = _call_d8("NVDA", ["AMD"],  # only 1 peer
                           {"AMD": 2.0})
        assert result.raw_pts == 0

    def test_symbol_not_in_ttg_gives_zero(self):
        with patch.object(ce, "_exposures_for", return_value=[]):
            result = ce._score_peer_network("ZZZZZ", {})
        assert result.raw_pts == 0


# ---------------------------------------------------------------------------
# D9 — Counter-thesis
# ---------------------------------------------------------------------------

def _call_d9(symbol: str, driver_id: str, n_conflicts: int,
              thesis_intact: bool | None) -> ce.DimensionScore:
    conflicts = [{"driver_id": driver_id, "evidence": "x",
                  "verification_status": "verified", "confidence": 0.8}] * n_conflicts

    divergence: dict | None = None
    if thesis_intact is not None:
        divergence = {"symbol": symbol.upper(), "thesis_intact": thesis_intact}

    with patch.object(ce, "_counter_thesis_for", return_value=conflicts), \
         patch.object(ce, "_thesis_divergence_for", return_value=divergence):
        return ce._score_counter_thesis(symbol, driver_id)


class TestD9CounterThesis:
    def test_no_conflicts_and_intact_gives_plus3(self):
        result = _call_d9("AAPL", "d1", n_conflicts=0, thesis_intact=True)
        assert result.raw_pts == 3

    def test_two_or_more_conflicts_gives_penalty(self):
        result = _call_d9("AAPL", "d1", n_conflicts=2, thesis_intact=None)
        assert result.raw_pts < 0  # structural conflicts reduce conviction

    def test_diverging_thesis_gives_minus8(self):
        result = _call_d9("AAPL", "d1", n_conflicts=0, thesis_intact=False)
        assert result.raw_pts == -8

    def test_no_conflicts_no_divergence_data_gives_zero(self):
        result = _call_d9("AAPL", "d1", n_conflicts=0, thesis_intact=None)
        assert result.raw_pts == 0

    def test_diverging_overrides_conflict_count(self):
        # thesis_intact=False should return -8 even with 3 conflicts
        result = _call_d9("AAPL", "d1", n_conflicts=3, thesis_intact=False)
        assert result.raw_pts == -8

    def test_single_conflict_gives_penalty(self):
        result = _call_d9("AAPL", "d1", n_conflicts=1, thesis_intact=None)
        assert result.raw_pts < 0  # 1 verified conflict = -8


# ---------------------------------------------------------------------------
# conviction_universe.py — zone management
# ---------------------------------------------------------------------------

import conviction_universe as cu


def _reset_universe_state():
    """Reset module-level state between tests."""
    cu._state = {}
    cu._rotation_flags = []


def _make_scores(symbol: str, total_score: float, d9_raw_pts: int = 0,
                 d1_raw_pts: int = 5, d5_raw_pts: int = 10,
                 d7_raw_pts: int = 0) -> dict:
    return {
        symbol: {
            "total_score": total_score,
            "d9_raw_pts": d9_raw_pts,
            "d1_raw_pts": d1_raw_pts,
            "d5_raw_pts": d5_raw_pts,
            "d7_raw_pts": d7_raw_pts,
        }
    }


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    """Redirect _DATA_PATH to tmp dir and reset in-memory state before each test."""
    _reset_universe_state()
    monkeypatch.setattr(cu, "_DATA_PATH",
                        tmp_path / "conviction" / "universe_zones.json")
    yield
    _reset_universe_state()


class TestConvictionUniverseZones:
    def test_symbol_enters_tradeable_at_score_65(self):
        report = cu.update(_make_scores("NVDA", 65))
        assert "NVDA" in report.tradeable

    def test_symbol_does_not_enter_tradeable_below_65(self):
        report = cu.update(_make_scores("NVDA", 64))
        assert "NVDA" not in report.tradeable

    def test_symbol_does_not_exit_tradeable_on_first_dip_below_50(self):
        # Enter TRADEABLE
        cu.update(_make_scores("NVDA", 70))
        # First dip below 50
        report = cu.update(_make_scores("NVDA", 45))
        assert "NVDA" in report.tradeable, "Should still be TRADEABLE after 1st dip"

    def test_symbol_exits_tradeable_on_second_consecutive_below_50(self):
        # Enter TRADEABLE
        cu.update(_make_scores("NVDA", 70))
        # First dip
        cu.update(_make_scores("NVDA", 45))
        # Second consecutive dip
        report = cu.update(_make_scores("NVDA", 40))
        assert "NVDA" not in report.tradeable, "Should exit after 2nd consecutive dip"

    def test_hard_stop_d9_minus10_exits_immediately(self):
        # Enter TRADEABLE
        cu.update(_make_scores("NVDA", 70))
        # Single update with D9 <= -10: should exit immediately (no 2-consecutive check)
        report = cu.update(_make_scores("NVDA", 60, d9_raw_pts=-10))
        assert "NVDA" not in report.tradeable, "Hard stop D9=-10 should exit immediately"

    def test_rotation_flag_created_on_hard_stop_exit(self):
        cu.update(_make_scores("NVDA", 70))
        report = cu.update(_make_scores("NVDA", 60, d9_raw_pts=-10))
        assert any(f["symbol"] == "NVDA" for f in report.rotation_flags)

    def test_rotation_flag_created_on_score_drift_exit(self):
        cu.update(_make_scores("NVDA", 70))
        cu.update(_make_scores("NVDA", 45))
        report = cu.update(_make_scores("NVDA", 40))
        assert any(f["symbol"] == "NVDA" for f in report.rotation_flags)

    def test_consecutive_counter_resets_if_score_recovers(self):
        cu.update(_make_scores("NVDA", 70))
        # First dip — counter = 1
        cu.update(_make_scores("NVDA", 45))
        # Recovery above EXIT_TRADEABLE — counter resets
        cu.update(_make_scores("NVDA", 55))
        # Another dip — only 1st consecutive, should NOT exit
        report = cu.update(_make_scores("NVDA", 45))
        assert "NVDA" in report.tradeable

    def test_rotation_flag_has_reason_field(self):
        cu.update(_make_scores("NVDA", 70))
        cu.update(_make_scores("NVDA", 45))
        cu.update(_make_scores("NVDA", 40))
        flags = cu.get_rotation_flags()
        nvda_flags = [f for f in flags if f["symbol"] == "NVDA"]
        assert nvda_flags
        assert "reason" in nvda_flags[0]
