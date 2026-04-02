"""
Tests for ic_validator.py — IC validation gate.

Covers:
  (a) get_ic_health()          — reads ic_weights.json, classifies quality
  (b) load_walkforward_sharpe() — reads most recent backtest result
  (c) check_live_readiness()    — all three gates
  (d) validate_and_persist()    — writes valid JSON to disk
"""

import json
import os
import sys
import time
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Stub heavy deps before importing any Decifer module
from unittest.mock import MagicMock
for _mod in ["ib_async", "ib_insync", "anthropic", "praw",
             "feedparser", "tvDatafeed", "requests_html", "schedule", "colorama"]:
    sys.modules.setdefault(_mod, MagicMock())

import ic_validator as icv
from ic_validator import (
    DIMENSIONS,
    ICHealthReport,
    LiveReadinessReport,
    get_ic_health,
    load_walkforward_sharpe,
    check_live_readiness,
    validate_and_persist,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_ic_cache(tmp_path, raw_ic: dict, n_records: int = 60,
                    using_equal: bool = False) -> str:
    """Write a minimal ic_weights.json to tmp_path and return the path string."""
    equal_w = {d: round(1.0 / len(DIMENSIONS), 6) for d in DIMENSIONS}
    payload = {
        "weights":             equal_w,
        "raw_ic":              raw_ic,
        "n_records":           n_records,
        "using_equal_weights": using_equal,
        "updated":             "2026-01-01T00:00:00+00:00",
    }
    f = tmp_path / "ic_weights.json"
    f.write_text(json.dumps(payload))
    return str(f)


def _write_backtest_result(results_dir, sharpe: float, filename: str = "backtest_test.json") -> str:
    """Write a backtest result JSON with the given Sharpe. Returns file path."""
    f = results_dir / filename
    payload = {
        "report": {"sharpe_ratio": sharpe, "win_rate": 0.55, "total_trades": 40},
        "config": {"symbols": ["AAPL"], "start_date": "2024-01-01"},
    }
    f.write_text(json.dumps(payload))
    return str(f)


def _strong_raw_ic() -> dict:
    """Raw IC where 7 of 9 dimensions are strongly positive."""
    ic = {d: 0.08 for d in DIMENSIONS}
    ic["social"]    = -0.02
    ic["reversion"] = -0.01
    return ic


def _weak_raw_ic() -> dict:
    """Raw IC where only 2 dimensions are barely positive."""
    ic = {d: -0.05 for d in DIMENSIONS}
    ic["trend"]    = 0.01
    ic["momentum"] = 0.015
    return ic


# ── (a) TestGetICHealth ───────────────────────────────────────────────────────


class TestGetICHealth:

    def test_missing_cache_returns_no_signal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icv, "_DEFAULT_IC_WEIGHTS_PATH",
                            str(tmp_path / "nonexistent.json"))
        result = get_ic_health()
        assert result.quality == "NO_SIGNAL"
        assert result.n_records == 0
        assert result.using_equal_weights is True

    def test_all_none_ic_returns_no_signal(self, tmp_path):
        raw = {d: None for d in DIMENSIONS}
        path = _write_ic_cache(tmp_path, raw, using_equal=True)
        result = get_ic_health(ic_weights_path=path)
        assert result.quality == "NO_SIGNAL"
        assert result.n_positive_dims == 0
        assert result.mean_positive_ic == 0.0

    def test_all_negative_ic_returns_no_signal(self, tmp_path):
        raw = {d: -0.1 for d in DIMENSIONS}
        path = _write_ic_cache(tmp_path, raw, using_equal=True)
        result = get_ic_health(ic_weights_path=path)
        assert result.quality == "NO_SIGNAL"
        assert result.n_positive_dims == 0

    def test_weak_ic_returns_weak(self, tmp_path):
        """2 positive dims, low mean IC → WEAK."""
        raw = _weak_raw_ic()
        path = _write_ic_cache(tmp_path, raw, using_equal=False)
        result = get_ic_health(ic_weights_path=path)
        assert result.quality == "WEAK"
        assert result.n_positive_dims == 2

    def test_moderate_ic_returns_moderate(self, tmp_path):
        """3-4 positive dims with mean IC in [0.02, 0.05) → MODERATE."""
        raw = {d: -0.05 for d in DIMENSIONS}
        for d in ["trend", "momentum", "squeeze", "flow"]:
            raw[d] = 0.03   # 4 positive, mean = 0.03
        path = _write_ic_cache(tmp_path, raw, using_equal=False)
        result = get_ic_health(ic_weights_path=path)
        assert result.quality == "MODERATE"
        assert result.n_positive_dims == 4
        assert result.mean_positive_ic == pytest.approx(0.03, abs=1e-6)

    def test_strong_ic_returns_strong(self, tmp_path):
        raw = _strong_raw_ic()
        path = _write_ic_cache(tmp_path, raw, using_equal=False)
        result = get_ic_health(ic_weights_path=path)
        assert result.quality == "STRONG"
        assert result.n_positive_dims >= 5
        assert result.mean_positive_ic >= 0.05

    def test_malformed_json_returns_no_signal(self, tmp_path):
        f = tmp_path / "ic_weights.json"
        f.write_text("{ not valid json }")
        result = get_ic_health(ic_weights_path=str(f))
        assert result.quality == "NO_SIGNAL"

    def test_n_records_populated_from_cache(self, tmp_path):
        raw = _strong_raw_ic()
        path = _write_ic_cache(tmp_path, raw, n_records=75, using_equal=False)
        result = get_ic_health(ic_weights_path=path)
        assert result.n_records == 75
        assert result.n_valid_records == 75

    def test_raw_ic_dict_has_all_nine_dimensions(self, tmp_path):
        raw = _strong_raw_ic()
        path = _write_ic_cache(tmp_path, raw, using_equal=False)
        result = get_ic_health(ic_weights_path=path)
        assert set(result.raw_ic.keys()) == set(DIMENSIONS)

    def test_using_equal_weights_true_forces_no_signal(self, tmp_path):
        """Even if raw_ic values look positive, using_equal_weights=True → NO_SIGNAL."""
        raw = {d: 0.1 for d in DIMENSIONS}   # all positive
        path = _write_ic_cache(tmp_path, raw, using_equal=True)  # but flagged as equal
        result = get_ic_health(ic_weights_path=path)
        assert result.quality == "NO_SIGNAL"


# ── (b) TestLoadWalkforwardSharpe ─────────────────────────────────────────────


class TestLoadWalkforwardSharpe:

    def test_nonexistent_dir_returns_none(self, tmp_path):
        sharpe = load_walkforward_sharpe(str(tmp_path / "nonexistent"))
        assert sharpe is None

    def test_empty_results_dir_returns_none(self, tmp_path):
        d = tmp_path / "results"
        d.mkdir()
        sharpe = load_walkforward_sharpe(str(d))
        assert sharpe is None

    def test_single_result_file_returns_sharpe(self, tmp_path):
        d = tmp_path / "results"
        d.mkdir()
        _write_backtest_result(d, sharpe=1.25)
        sharpe = load_walkforward_sharpe(str(d))
        assert sharpe == pytest.approx(1.25)

    def test_multiple_files_returns_most_recent(self, tmp_path):
        """The file with the highest mtime must be used."""
        d = tmp_path / "results"
        d.mkdir()
        older = d / "older.json"
        older.write_text(json.dumps({"report": {"sharpe_ratio": 0.5}}))
        # Force a different mtime
        os.utime(str(older), (1_000_000, 1_000_000))
        newer = d / "newer.json"
        newer.write_text(json.dumps({"report": {"sharpe_ratio": 1.5}}))
        os.utime(str(newer), (2_000_000, 2_000_000))
        sharpe = load_walkforward_sharpe(str(d))
        assert sharpe == pytest.approx(1.5)

    def test_malformed_json_skipped_falls_to_next(self, tmp_path):
        d = tmp_path / "results"
        d.mkdir()
        good = d / "good.json"
        good.write_text(json.dumps({"report": {"sharpe_ratio": 0.9}}))
        os.utime(str(good), (1_000_000, 1_000_000))
        bad = d / "corrupt.json"
        bad.write_text("{ bad json")
        os.utime(str(bad), (2_000_000, 2_000_000))
        sharpe = load_walkforward_sharpe(str(d))
        assert sharpe == pytest.approx(0.9)

    def test_file_without_sharpe_key_skipped(self, tmp_path):
        d = tmp_path / "results"
        d.mkdir()
        good = d / "good.json"
        good.write_text(json.dumps({"report": {"sharpe_ratio": 0.7}}))
        os.utime(str(good), (1_000_000, 1_000_000))
        no_sharpe = d / "nosharpe.json"
        no_sharpe.write_text(json.dumps({"report": {"win_rate": 0.5}}))
        os.utime(str(no_sharpe), (2_000_000, 2_000_000))
        # nosharpe.json is newest but has no sharpe_ratio → fall back to good.json
        sharpe = load_walkforward_sharpe(str(d))
        assert sharpe == pytest.approx(0.7)


# ── (c) TestCheckLiveReadiness ────────────────────────────────────────────────


class TestCheckLiveReadiness:

    def _setup_passing(self, tmp_path, monkeypatch, n_records=60, sharpe=1.2):
        """Wire up all passing conditions."""
        monkeypatch.setattr(
            icv, "_DEFAULT_IC_WEIGHTS_PATH",
            _write_ic_cache(tmp_path, _strong_raw_ic(), n_records=n_records, using_equal=False),
        )
        d = tmp_path / "results"
        d.mkdir()
        _write_backtest_result(d, sharpe)
        monkeypatch.setattr(icv, "_DEFAULT_RESULTS_DIR", str(d))

    def test_all_gates_pass(self, tmp_path, monkeypatch):
        self._setup_passing(tmp_path, monkeypatch)
        result = check_live_readiness()
        assert result.ready_for_live is True
        assert result.failures == []
        assert result.sample_gate_passed is True
        assert result.ic_gate_passed is True
        assert result.sharpe_gate_passed is True

    def test_sample_gate_failure(self, tmp_path, monkeypatch):
        """Fewer than 50 valid records → sample gate fails."""
        self._setup_passing(tmp_path, monkeypatch, n_records=30)
        result = check_live_readiness()
        assert result.sample_gate_passed is False
        assert result.ready_for_live is False
        assert any("SAMPLE GATE" in f for f in result.failures)
        assert "30" in " ".join(result.failures)

    def test_ic_gate_failure_too_few_positive_dims(self, tmp_path, monkeypatch):
        """Only 2 positive dims (need 5) → IC gate fails with breadth message."""
        monkeypatch.setattr(
            icv, "_DEFAULT_IC_WEIGHTS_PATH",
            _write_ic_cache(tmp_path, _weak_raw_ic(), n_records=60, using_equal=False),
        )
        d = tmp_path / "results"
        d.mkdir()
        _write_backtest_result(d, 1.2)
        monkeypatch.setattr(icv, "_DEFAULT_RESULTS_DIR", str(d))
        result = check_live_readiness()
        assert result.ic_gate_passed is False
        assert result.ready_for_live is False
        assert any("breadth" in f.lower() or "IC GATE" in f for f in result.failures)

    def test_ic_gate_failure_ic_too_low(self, tmp_path, monkeypatch):
        """5+ positive dims but mean IC < 0.05 → IC gate fails with magnitude message."""
        raw = {d: 0.02 for d in DIMENSIONS}   # 9 positive dims, mean = 0.02 < 0.05
        monkeypatch.setattr(
            icv, "_DEFAULT_IC_WEIGHTS_PATH",
            _write_ic_cache(tmp_path, raw, n_records=60, using_equal=False),
        )
        d = tmp_path / "results"
        d.mkdir()
        _write_backtest_result(d, 1.2)
        monkeypatch.setattr(icv, "_DEFAULT_RESULTS_DIR", str(d))
        result = check_live_readiness()
        assert result.ic_gate_passed is False
        assert any("barely predictive" in f.lower() or "IC GATE" in f for f in result.failures)

    def test_sharpe_gate_failure_no_results(self, tmp_path, monkeypatch):
        """No backtest results → sharpe gate fails."""
        monkeypatch.setattr(
            icv, "_DEFAULT_IC_WEIGHTS_PATH",
            _write_ic_cache(tmp_path, _strong_raw_ic(), n_records=60, using_equal=False),
        )
        empty_dir = tmp_path / "empty_results"
        empty_dir.mkdir()
        monkeypatch.setattr(icv, "_DEFAULT_RESULTS_DIR", str(empty_dir))
        result = check_live_readiness()
        assert result.sharpe_gate_passed is False
        assert result.ready_for_live is False
        assert any("SHARPE GATE" in f for f in result.failures)

    def test_sharpe_gate_failure_sharpe_too_low(self, tmp_path, monkeypatch):
        """Sharpe = 0.5 < 0.8 threshold → sharpe gate fails."""
        self._setup_passing(tmp_path, monkeypatch, sharpe=0.5)
        result = check_live_readiness()
        assert result.sharpe_gate_passed is False
        assert result.ready_for_live is False
        assert any("0.5" in f or "SHARPE GATE" in f for f in result.failures)

    def test_multiple_failures_all_reported(self, tmp_path, monkeypatch):
        """When all three gates fail, failures list has three entries."""
        monkeypatch.setattr(icv, "_DEFAULT_IC_WEIGHTS_PATH",
                            str(tmp_path / "nonexistent.json"))
        empty_dir = tmp_path / "empty_results"
        empty_dir.mkdir()
        monkeypatch.setattr(icv, "_DEFAULT_RESULTS_DIR", str(empty_dir))
        result = check_live_readiness()
        assert result.ready_for_live is False
        assert len(result.failures) == 3

    def test_checked_at_is_valid_iso8601(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icv, "_DEFAULT_IC_WEIGHTS_PATH",
                            str(tmp_path / "nonexistent.json"))
        result = check_live_readiness()
        assert result.checked_at
        from datetime import datetime
        datetime.fromisoformat(result.checked_at)   # must not raise

    def test_ic_quality_propagated(self, tmp_path, monkeypatch):
        self._setup_passing(tmp_path, monkeypatch)
        result = check_live_readiness()
        assert result.ic_quality == "STRONG"

    def test_n_positive_dims_propagated(self, tmp_path, monkeypatch):
        self._setup_passing(tmp_path, monkeypatch)
        result = check_live_readiness()
        assert result.n_positive_dims == 7   # _strong_raw_ic has 7 positive


# ── (d) TestValidateAndPersist ────────────────────────────────────────────────


class TestValidateAndPersist:

    def test_writes_valid_json_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icv, "_DEFAULT_IC_WEIGHTS_PATH",
                            str(tmp_path / "nonexistent.json"))
        out = tmp_path / "validation_result.json"
        validate_and_persist(out_path=str(out))
        assert out.exists()
        data = json.loads(out.read_text())
        assert "ready_for_live" in data
        assert isinstance(data["ready_for_live"], bool)

    def test_persisted_json_matches_returned_report(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icv, "_DEFAULT_IC_WEIGHTS_PATH",
                            str(tmp_path / "nonexistent.json"))
        out = tmp_path / "result.json"
        result = validate_and_persist(out_path=str(out))
        data = json.loads(out.read_text())
        assert data["ready_for_live"] == result.ready_for_live
        assert data["ic_quality"] == result.ic_quality
        assert data["failures"] == result.failures

    def test_persisted_json_has_all_required_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icv, "_DEFAULT_IC_WEIGHTS_PATH",
                            str(tmp_path / "nonexistent.json"))
        out = tmp_path / "result.json"
        validate_and_persist(out_path=str(out))
        data = json.loads(out.read_text())
        required = {
            "ready_for_live", "ic_gate_passed", "sharpe_gate_passed",
            "sample_gate_passed", "n_valid_records", "mean_positive_ic",
            "n_positive_dims", "walkforward_sharpe", "ic_quality",
            "failures", "checked_at",
        }
        assert required.issubset(data.keys())

    def test_second_call_overwrites_first(self, tmp_path, monkeypatch):
        """Calling validate_and_persist twice must overwrite, not append."""
        monkeypatch.setattr(icv, "_DEFAULT_IC_WEIGHTS_PATH",
                            str(tmp_path / "nonexistent.json"))
        out = tmp_path / "result.json"
        validate_and_persist(out_path=str(out))
        validate_and_persist(out_path=str(out))
        # File must be valid JSON (not concatenated)
        data = json.loads(out.read_text())
        assert "ready_for_live" in data
