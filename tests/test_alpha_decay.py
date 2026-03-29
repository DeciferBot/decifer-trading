# tests/test_alpha_decay.py
# Unit tests for alpha_decay.py
# Coverage: entry-date parsing, forward-return aggregation, stats output shape,
# direction-sign inversion for SHORT trades, graceful empty/missing-data handling.

import os
import sys
import json
import tempfile
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

# ── Path setup ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub heavy optional deps before importing alpha_decay
import types

_yf_stub = types.ModuleType("yfinance")
_yf_stub.Ticker = MagicMock()
_yf_stub.cache  = MagicMock()
sys.modules.setdefault("yfinance", _yf_stub)

import alpha_decay
from alpha_decay import (
    _parse_entry_date,
    _percentile,
    _aggregate,
    get_alpha_decay_stats,
    compute_alpha_decay,
    HORIZONS,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_trade(**kwargs):
    base = {
        "symbol":      "AAPL",
        "direction":   "LONG",
        "score":       35,
        "regime":      "BULL_TRENDING",
        "entry_time":  "2026-03-20 10:00:00",
        "exit_price":  155.0,
        "pnl":         500.0,
    }
    base.update(kwargs)
    return base


def _make_record(direction="LONG", score=35, regime="BULL_TRENDING",
                 returns=None):
    """Build a pre-computed decay record (as returned by compute_alpha_decay)."""
    fwd = returns or {1: 0.01, 3: 0.02, 5: 0.015, 10: 0.005}
    dir_sign = -1 if direction == "SHORT" else 1
    dir_adj  = {h: round(v * dir_sign, 6) for h, v in fwd.items()}
    return {
        "symbol":                "AAPL",
        "direction":             direction,
        "score":                 score,
        "regime":                regime,
        "entry_date":            "2026-03-20",
        "pnl":                   500.0,
        "forward_returns":       fwd,
        "direction_adj_returns": dir_adj,
    }


# ── _parse_entry_date ─────────────────────────────────────────────────────

class TestParseEntryDate:

    def test_entry_time_space_separator(self):
        t = _make_trade(entry_time="2026-03-20 10:00:00")
        assert _parse_entry_date(t) == date(2026, 3, 20)

    def test_entry_time_iso(self):
        t = _make_trade(entry_time="2026-03-20T10:00:00")
        assert _parse_entry_date(t) == date(2026, 3, 20)

    def test_open_time_fallback(self):
        t = {"open_time": "2026-03-21 09:30:00", "symbol": "TSLA"}
        assert _parse_entry_date(t) == date(2026, 3, 21)

    def test_timestamp_fallback(self):
        t = {"timestamp": "2026-03-22T15:00:00+00:00", "symbol": "SPY"}
        assert _parse_entry_date(t) == date(2026, 3, 22)

    def test_prefers_entry_time_over_timestamp(self):
        t = _make_trade(entry_time="2026-03-20 10:00:00",
                        timestamp="2026-03-25T12:00:00")
        assert _parse_entry_date(t) == date(2026, 3, 20)

    def test_returns_none_for_missing_fields(self):
        assert _parse_entry_date({"symbol": "AAPL"}) is None

    def test_returns_none_for_garbage_value(self):
        assert _parse_entry_date({"entry_time": "not-a-date"}) is None


# ── _percentile ───────────────────────────────────────────────────────────

class TestPercentile:

    def test_median_of_odd_list(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_p25_and_p75(self):
        vals = list(range(1, 101))  # 1–100
        assert _percentile(vals, 25) == 25.75
        assert _percentile(vals, 75) == 75.25

    def test_single_element(self):
        assert _percentile([0.05], 50) == 0.05

    def test_empty_list_returns_none(self):
        assert _percentile([], 50) is None

    def test_none_values_filtered(self):
        assert _percentile([None, 1.0, 2.0, None, 3.0], 50) == 2.0


# ── _aggregate ────────────────────────────────────────────────────────────

class TestAggregate:

    def test_empty_records_returns_none_arrays(self):
        result = _aggregate([], HORIZONS)
        assert result["n"] == 0
        assert all(v is None for v in result["median"])

    def test_single_record_median_equals_value(self):
        record = _make_record(returns={1: 0.01, 3: 0.02, 5: 0.015, 10: 0.005})
        result = _aggregate([record], HORIZONS)
        assert result["n"] == 1
        assert result["median"][0] == pytest.approx(0.01, abs=1e-6)
        assert result["median"][1] == pytest.approx(0.02, abs=1e-6)

    def test_short_sign_inversion_in_records(self):
        # A SHORT trade with price rising should have negative dir_adj_return
        record = _make_record(direction="SHORT",
                              returns={1: 0.01, 3: 0.02, 5: 0.015, 10: 0.005})
        assert record["direction_adj_returns"][1] == pytest.approx(-0.01, abs=1e-6)
        result = _aggregate([record], HORIZONS)
        assert result["median"][0] == pytest.approx(-0.01, abs=1e-6)

    def test_multiple_records_median(self):
        recs = [
            _make_record(returns={1: 0.01, 3: 0.02, 5: 0.03, 10: 0.04}),
            _make_record(returns={1: 0.03, 3: 0.04, 5: 0.05, 10: 0.06}),
            _make_record(returns={1: 0.02, 3: 0.03, 5: 0.04, 10: 0.05}),
        ]
        result = _aggregate(recs, HORIZONS)
        assert result["n"] == 3
        # Median of [0.01, 0.02, 0.03] = 0.02
        assert result["median"][0] == pytest.approx(0.02, abs=1e-6)

    def test_partial_horizons_handled_gracefully(self):
        # Record missing T+10 (trade too recent for that horizon)
        record = _make_record(returns={1: 0.01, 3: 0.02, 5: 0.015})
        record["direction_adj_returns"] = {1: 0.01, 3: 0.02, 5: 0.015}
        result = _aggregate([record], HORIZONS)
        # T+10 should be None (no data)
        assert result["median"][3] is None


# ── get_alpha_decay_stats (with mocked compute_alpha_decay) ───────────────

class TestGetAlphaDecayStats:

    def _mock_records(self):
        return [
            _make_record(direction="LONG", score=40, regime="BULL_TRENDING",
                         returns={1: 0.02, 3: 0.03, 5: 0.025, 10: 0.01}),
            _make_record(direction="LONG", score=30, regime="BULL_TRENDING",
                         returns={1: 0.01, 3: 0.015, 5: 0.01, 10: -0.005}),
            _make_record(direction="SHORT", score=42, regime="BEAR_TRENDING",
                         returns={1: -0.02, 3: -0.03, 5: -0.025, 10: -0.01}),
            _make_record(direction="LONG", score=25, regime="CHOPPY",
                         returns={1: -0.005, 3: -0.01, 5: -0.015, 10: -0.02}),
        ]

    def test_output_shape(self):
        with patch.object(alpha_decay, "compute_alpha_decay",
                          return_value=self._mock_records()):
            stats = get_alpha_decay_stats()

        assert "horizons" in stats
        assert "groups" in stats
        assert "trade_count" in stats
        assert "computed_at" in stats
        assert stats["horizons"] == HORIZONS
        assert stats["trade_count"] == 4

    def test_all_expected_groups_present(self):
        with patch.object(alpha_decay, "compute_alpha_decay",
                          return_value=self._mock_records()):
            stats = get_alpha_decay_stats()

        groups = stats["groups"]
        for g in ("all", "high_score", "low_score", "bull", "bear",
                  "long_only", "short_only"):
            assert g in groups, f"Missing group: {g}"

    def test_group_counts(self):
        with patch.object(alpha_decay, "compute_alpha_decay",
                          return_value=self._mock_records()):
            stats = get_alpha_decay_stats()

        g = stats["groups"]
        assert g["all"]["n"]        == 4
        assert g["high_score"]["n"] == 2  # score 40, 42
        assert g["low_score"]["n"]  == 2  # score 30, 25
        assert g["bull"]["n"]       == 2  # BULL_TRENDING × 2
        assert g["bear"]["n"]       == 1  # BEAR_TRENDING × 1
        assert g["long_only"]["n"]  == 3
        assert g["short_only"]["n"] == 1

    def test_optimal_horizon_is_set(self):
        with patch.object(alpha_decay, "compute_alpha_decay",
                          return_value=self._mock_records()):
            stats = get_alpha_decay_stats()

        # Should be one of the configured horizons
        assert stats["optimal_horizon"] in HORIZONS

    def test_empty_trade_set(self):
        with patch.object(alpha_decay, "compute_alpha_decay", return_value=[]):
            stats = get_alpha_decay_stats()

        assert stats["trade_count"]     == 0
        assert stats["optimal_horizon"] is None
        for g in stats["groups"].values():
            assert g["n"] == 0

    def test_custom_horizons(self):
        custom = [2, 7]
        with patch.object(alpha_decay, "compute_alpha_decay", return_value=[]):
            stats = get_alpha_decay_stats(horizons=custom)

        assert stats["horizons"] == custom


# ── compute_alpha_decay (trades → records, with mocked yfinance) ──────────

class TestComputeAlphaDecay:

    def _mock_df(self, closes):
        """Build a minimal DataFrame mock that behaves like yfinance history output."""
        import pandas as pd
        idx = pd.date_range("2026-03-20", periods=len(closes), freq="B")
        return pd.DataFrame({"Close": closes}, index=idx)

    def test_skips_trade_without_entry_date(self):
        trades = [{"symbol": "AAPL", "exit_price": 155, "pnl": 100}]
        with patch.object(alpha_decay, "fetch_forward_returns", return_value=None):
            result = compute_alpha_decay(trades=trades)
        assert result == []

    def test_skips_open_trade(self):
        # Trade with no pnl and no exit_price is still open
        trades = [_make_trade(exit_price=None, pnl=None)]
        result = compute_alpha_decay(trades=trades)
        assert result == []

    def test_direction_adj_positive_for_long_up(self):
        trades = [_make_trade(direction="LONG")]
        fwd = {1: 0.01, 3: 0.02, 5: 0.015, 10: 0.005}
        with patch.object(alpha_decay, "fetch_forward_returns", return_value=fwd):
            result = compute_alpha_decay(trades=trades)
        assert result[0]["direction_adj_returns"][1] == pytest.approx(0.01)

    def test_direction_adj_inverted_for_short(self):
        # SHORT trade: price rose → unfavourable → should be negative dir_adj
        trades = [_make_trade(direction="SHORT")]
        fwd = {1: 0.01, 3: 0.02, 5: 0.015, 10: 0.005}
        with patch.object(alpha_decay, "fetch_forward_returns", return_value=fwd):
            result = compute_alpha_decay(trades=trades)
        assert result[0]["direction_adj_returns"][1] == pytest.approx(-0.01)

    def test_skips_when_no_price_data(self):
        trades = [_make_trade()]
        with patch.object(alpha_decay, "fetch_forward_returns", return_value=None):
            result = compute_alpha_decay(trades=trades)
        assert result == []

    def test_loads_from_file_when_trades_is_none(self):
        sample = [_make_trade()]
        fwd = {1: 0.01, 3: 0.02, 5: 0.015, 10: 0.005}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False) as f:
            json.dump(sample, f)
            tmp = f.name
        try:
            with patch.object(alpha_decay, "_TRADE_LOG_FILE", tmp), \
                 patch.object(alpha_decay, "fetch_forward_returns",
                              return_value=fwd):
                result = compute_alpha_decay()
            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"
        finally:
            os.unlink(tmp)

    def test_returns_empty_list_when_file_missing(self):
        with patch.object(alpha_decay, "_TRADE_LOG_FILE", "/nonexistent/path.json"):
            result = compute_alpha_decay()
        assert result == []


# ── pytest import ─────────────────────────────────────────────────────────

import pytest
