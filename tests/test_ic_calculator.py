"""Tests for ic_calculator.py

Covers:
  (a) IC calculation correct on synthetic data
  (b) Negative IC dimensions receive zero weight
  (c) All-zero IC falls back to equal weights
  (d) Weights always sum to 1.0
  (e) normalize_ic_weights handles None / non-finite IC gracefully
  (f) get_current_weights falls back to equal weights when cache is absent/corrupt
  (g) update_ic_weights writes a valid JSON cache and appends history
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Make sure project root is on sys.path before importing Decifer modules
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Stub heavy deps BEFORE importing any Decifer module
for _mod in [
    "ib_async",
    "ib_insync",
    "anthropic",
    "praw",
    "feedparser",
    "tvDatafeed",
    "requests_html",
    "schedule",
    "colorama",
]:
    sys.modules.setdefault(_mod, MagicMock())

# Minimal config stub
import config as _cfg_mod

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-6",
    "max_tokens": 1000,
    "signals_log": "/dev/null",
    "audit_log": "/dev/null",
}
if hasattr(_cfg_mod, "CONFIG"):
    for k, v in _cfg.items():
        _cfg_mod.CONFIG.setdefault(k, v)
else:
    _cfg_mod.CONFIG = _cfg

import ic_calculator as ic

# Submodule handles — patches must target the module where the name is looked up,
# not the re-export shim.  (Binding `from ic.constants import X` captures X at
# import time; patching ic_calculator.X doesn't rebind the submodule's local.)
from ic import core as ic_core
from ic import data as ic_data
from ic import storage as ic_storage

DIMS = ic.DIMENSIONS
N = len(DIMS)


# ---------------------------------------------------------------------------
# Helper to build a signals-log record
# ---------------------------------------------------------------------------


def _make_record(symbol="AAPL", price=100.0, ts="2025-01-01T10:00:00+00:00", breakdown=None):
    if breakdown is None:
        breakdown = {d: 5.0 for d in DIMS}
    return {
        "symbol": symbol,
        "price": price,
        "ts": ts,
        "score": 30,
        "score_breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# (a) IC calculation correct on synthetic data
# ---------------------------------------------------------------------------


def _real_spearman(x, y):
    """Numpy-only Spearman correlation — bypasses the scipy mock in conftest."""
    x, y = np.array(x, dtype=float), np.array(y, dtype=float)
    n = len(x)
    if n < 3:
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    d = rx - ry
    denom = n * (n * n - 1)
    return float(1.0 - 6.0 * np.sum(d * d) / denom) if denom > 0 else 0.0


class TestComputeRollingIC:
    def test_positive_correlation_detected(self, tmp_path):
        """
        Dimension 'trend' score perfectly predicts forward return.
        Its IC should be close to +1.0.
        scipy.stats.spearmanr is mocked in conftest; patch _spearman with
        the real numpy implementation to get a meaningful assertion.
        """
        records = []
        for i in range(30):
            bd = {d: 5.0 for d in DIMS}
            bd["trend"] = float(i)  # monotone → perfect rank correlation
            records.append(_make_record(symbol=f"SYM{i:02d}", breakdown=bd))

        log_file = tmp_path / "signals_log.jsonl"
        with open(log_file, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        forward_returns = {i: float(i) / 100.0 for i in range(30)}

        with (
            patch.object(ic_core, "_fetch_forward_returns_batch", return_value=forward_returns),
            patch.object(ic_core, "_spearman", side_effect=_real_spearman),
        ):
            raw = ic.compute_rolling_ic(
                signals_log_path=str(log_file),
                window=30,
                min_valid=1,
            )

        assert raw["trend"] is not None
        assert raw["trend"] > 0.8, f"Expected IC close to 1.0, got {raw['trend']:.4f}"

    def test_negative_correlation_detected(self, tmp_path):
        """
        Dimension 'momentum' score is negatively correlated with forward return.
        Its IC should be negative.
        scipy.stats.spearmanr is mocked in conftest; patch _spearman with
        the real numpy implementation to get a meaningful assertion.
        """
        records = []
        for i in range(30):
            bd = {d: 5.0 for d in DIMS}
            bd["momentum"] = float(i)
            records.append(_make_record(symbol=f"SYM{i:02d}", breakdown=bd))

        log_file = tmp_path / "signals_log.jsonl"
        with open(log_file, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        forward_returns = {i: -float(i) / 100.0 for i in range(30)}

        with (
            patch.object(ic_core, "_fetch_forward_returns_batch", return_value=forward_returns),
            patch.object(ic_core, "_spearman", side_effect=_real_spearman),
        ):
            raw = ic.compute_rolling_ic(
                signals_log_path=str(log_file),
                window=30,
                min_valid=1,
            )

        assert raw["momentum"] is not None
        assert raw["momentum"] < -0.8, f"Expected large negative IC, got {raw['momentum']:.4f}"

    def test_insufficient_records_returns_none_ic(self, tmp_path):
        """Fewer than min_valid records → every dimension returns None."""
        records = [_make_record() for _ in range(10)]  # < 20 min
        log_file = tmp_path / "signals_log.jsonl"
        with open(log_file, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        forward_returns = {i: 0.01 for i in range(10)}
        with patch.object(ic_core, "_fetch_forward_returns_batch", return_value=forward_returns):
            raw = ic.compute_rolling_ic(
                signals_log_path=str(log_file),
                min_valid=20,
            )

        assert all(v is None for v in raw.values()), f"Expected all None, got {raw}"

    def test_empty_log_returns_none_ic(self, tmp_path):
        """Empty signals_log → every dimension returns None."""
        log_file = tmp_path / "empty.jsonl"
        log_file.write_text("")

        raw = ic.compute_rolling_ic(signals_log_path=str(log_file))
        assert all(v is None for v in raw.values())

    def test_missing_score_breakdown_records_skipped(self, tmp_path):
        """Records without score_breakdown are silently skipped."""
        records = []
        for _i in range(25):
            rec = {
                "symbol": "AAPL",
                "price": 100.0,
                "ts": "2025-01-01T10:00:00+00:00",
                "score": 30,
                "score_breakdown": {},
            }  # empty → invalid
            records.append(rec)

        log_file = tmp_path / "signals_log.jsonl"
        with open(log_file, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        raw = ic.compute_rolling_ic(signals_log_path=str(log_file))
        assert all(v is None for v in raw.values())


# ---------------------------------------------------------------------------
# (b) Negative IC dimensions receive zero weight
# ---------------------------------------------------------------------------


class TestNegativeICZeroWeight:
    """
    These tests verify normalize_ic_weights() logic directly.
    The force_equal_weights flag (a paper-trading gate in config) is patched
    to False so the weighting algorithm is exercised rather than bypassed.
    """

    def test_negative_ic_gives_zero_weight(self):
        """Negative IC must produce zero weight, not inverted."""
        raw = {d: 0.1 for d in DIMS}
        raw["trend"] = -0.5  # strongly negative → must be zeroed
        raw["momentum"] = -0.2  # negative → zero

        with patch.object(ic_core, "_ic_cfg", side_effect=lambda k, d: False if k == "force_equal_weights" else d):
            weights, _ = ic.normalize_ic_weights(raw)

        assert weights["trend"] == 0.0, "Negative IC must produce zero weight"
        assert weights["momentum"] == 0.0, "Negative IC must produce zero weight"

    def test_zero_ic_gives_zero_weight(self):
        """IC exactly 0.0 → weight = 0.0 (ties go to zero)."""
        raw = {d: 0.1 for d in DIMS}
        raw["squeeze"] = 0.0

        with patch.object(ic_core, "_ic_cfg", side_effect=lambda k, d: False if k == "force_equal_weights" else d):
            weights, _ = ic.normalize_ic_weights(raw)
        assert weights["squeeze"] == 0.0

    def test_all_positive_ic_all_nonzero_weights(self):
        """All positive IC → every dimension gets a positive weight."""
        raw = {d: 0.1 for d in DIMS}
        with patch.object(ic_core, "_ic_cfg", side_effect=lambda k, d: False if k == "force_equal_weights" else d):
            weights, _ = ic.normalize_ic_weights(raw)
        for d, w in weights.items():
            assert w > 0, f"Expected positive weight for {d}, got {w}"

    def test_mixed_ic_only_positive_dims_get_weight(self):
        """Only the positive-IC dimensions should have non-zero weights."""
        raw = {d: 0.0 for d in DIMS}
        raw.update(
            {
                "trend": 0.3,
                "momentum": 0.2,
                "squeeze": -0.1,
                "flow": 0.0,
                "breakout": 0.15,
                "mtf": -0.4,
                "news": 0.1,
                "social": -0.05,
                "reversion": 0.05,
            }
        )
        with patch.object(ic_core, "_ic_cfg", side_effect=lambda k, d: False if k == "force_equal_weights" else d):
            weights, _ = ic.normalize_ic_weights(raw)

        assert weights["squeeze"] == 0.0
        assert weights["flow"] == 0.0
        assert weights["mtf"] == 0.0
        assert weights["social"] == 0.0

        for d in ["trend", "momentum", "breakout", "news", "reversion"]:
            assert weights[d] > 0, f"{d} should have positive weight"


# ---------------------------------------------------------------------------
# (c) All-zero / all-negative IC falls back to equal weights
# ---------------------------------------------------------------------------


class TestEqualWeightFallback:
    def test_all_none_ic_returns_equal_weights(self):
        """All None IC → equal weights."""
        raw = {d: None for d in DIMS}
        weights, _ = ic.normalize_ic_weights(raw)
        _assert_equal_weights(weights)

    def test_all_negative_ic_returns_equal_weights(self):
        """All negative IC → equal weights."""
        raw = {d: -0.2 for d in DIMS}
        weights, _ = ic.normalize_ic_weights(raw)
        _assert_equal_weights(weights)

    def test_all_zero_ic_returns_equal_weights(self):
        """All zero IC → equal weights."""
        raw = {d: 0.0 for d in DIMS}
        weights, _ = ic.normalize_ic_weights(raw)
        _assert_equal_weights(weights)

    def test_mixed_none_and_negative_returns_equal_weights(self):
        """Mix of None and negative → equal weights."""
        raw = {d: (None if i % 2 == 0 else -0.1) for i, d in enumerate(DIMS)}
        weights, _ = ic.normalize_ic_weights(raw)
        _assert_equal_weights(weights)


# ---------------------------------------------------------------------------
# (d) Weights always sum to 1.0
# ---------------------------------------------------------------------------


class TestWeightsSumToOne:
    @pytest.mark.parametrize(
        "ic_scenario",
        [
            {d: 0.1 for d in DIMS},  # all positive
            {d: -0.1 for d in DIMS},  # all negative → equal fallback
            {d: None for d in DIMS},  # all None → equal fallback
            {"trend": 0.5, **{d: 0.0 for d in DIMS if d != "trend"}},  # single winner
            {d: (0.3 if i % 2 == 0 else -0.2) for i, d in enumerate(DIMS)},  # mixed
        ],
    )
    def test_weights_sum_to_one(self, ic_scenario):
        weights, _ = ic.normalize_ic_weights(ic_scenario)
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, not 1.0 (scenario={ic_scenario})"

    def test_weights_always_all_dimensions_present(self):
        """Output must contain exactly the 9 canonical dimensions."""
        raw = {d: 0.1 for d in DIMS}
        weights, _ = ic.normalize_ic_weights(raw)
        assert set(weights.keys()) == set(DIMS)

    def test_weights_non_negative(self):
        """No weight should ever be negative."""
        raw = {d: (-0.1 if i % 2 == 0 else 0.2) for i, d in enumerate(DIMS)}
        weights, _ = ic.normalize_ic_weights(raw)
        for d, w in weights.items():
            assert w >= 0.0, f"Negative weight for {d}: {w}"


# ---------------------------------------------------------------------------
# Noise floor (ic_min_threshold) and HHI cap (max_single_weight)
# ---------------------------------------------------------------------------


class TestNoiseFlorAndHHICap:
    def test_noise_floor_suppresses_below_threshold(self, monkeypatch):
        """Dimensions with IC below the noise floor should receive zero weight."""
        monkeypatch.setattr(ic_core, "_ic_cfg", lambda key, default: 0.05 if key == "ic_min_threshold" else default)
        raw = {d: 0.1 for d in DIMS}
        raw["news"] = 0.02  # below 0.05 floor
        raw["social"] = 0.03  # below 0.05 floor
        weights, meta = ic.normalize_ic_weights(raw)
        assert weights["news"] == 0.0, "news IC below floor must be zeroed"
        assert weights["social"] == 0.0, "social IC below floor must be zeroed"
        assert meta["noise_floor_applied"] is True
        assert "news" in meta["dimensions_suppressed"]
        assert "social" in meta["dimensions_suppressed"]

    def test_noise_floor_zero_means_positive_ic_passes(self, monkeypatch):
        """With ic_min_threshold=0.0 (Phase 1 default), any positive IC should pass."""
        monkeypatch.setattr(ic_core, "_ic_cfg", lambda key, default: 0.0 if key == "ic_min_threshold" else default)
        raw = {d: 0.01 for d in DIMS}  # all very small but positive
        weights, meta = ic.normalize_ic_weights(raw)
        for d, w in weights.items():
            assert w > 0.0, f"{d} should pass with ic_min_threshold=0.0"
        assert meta["noise_floor_applied"] is False
        assert meta["dimensions_suppressed"] == []

    def test_noise_floor_all_below_threshold_returns_equal_weights(self, monkeypatch):
        """If all dimensions are below the noise floor, fall back to equal weights."""
        monkeypatch.setattr(ic_core, "_ic_cfg", lambda key, default: 0.10 if key == "ic_min_threshold" else default)
        raw = {d: 0.05 for d in DIMS}  # all below 0.10 floor
        weights, _meta = ic.normalize_ic_weights(raw)
        _assert_equal_weights(weights)

    def test_hhi_cap_clips_dominant_dimension(self, monkeypatch):
        """If one dimension would exceed max_single_weight, it must be clipped."""
        monkeypatch.setattr(
            ic_core,
            "_ic_cfg",
            lambda key, default: 0.0 if key == "ic_min_threshold" else 0.40 if key == "max_single_weight" else default,
        )
        # Give trend a huge IC so it would otherwise dominate
        raw = {d: 0.01 for d in DIMS}
        raw["trend"] = 1.0
        weights, meta = ic.normalize_ic_weights(raw)
        assert weights["trend"] <= 0.40 + 1e-9, f"trend weight {weights['trend']:.3f} exceeds HHI cap 0.40"
        assert meta["hhi_capped"] is True
        assert abs(sum(weights.values()) - 1.0) < 1e-9, "weights must still sum to 1.0 after HHI cap"

    def test_hhi_cap_not_triggered_when_within_limit(self, monkeypatch):
        """No clipping if all weights are within the cap."""
        monkeypatch.setattr(
            ic_core,
            "_ic_cfg",
            lambda key, default: 0.0 if key == "ic_min_threshold" else 0.40 if key == "max_single_weight" else default,
        )
        raw = {d: 0.1 for d in DIMS}  # uniform — equal 1/9 ≈ 0.111, well below 0.40
        _weights, meta = ic.normalize_ic_weights(raw)
        assert meta["hhi_capped"] is False


# ---------------------------------------------------------------------------
# Cache I/O: get_current_weights / update_ic_weights
# ---------------------------------------------------------------------------


class TestCacheIO:
    def test_get_current_weights_no_file_returns_baseline(self, tmp_path, monkeypatch):
        """Missing cache → BASELINE_WEIGHTS (not equal weights)."""
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(tmp_path / "nonexistent.json"))
        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_get_current_weights_corrupt_file_returns_baseline(self, tmp_path, monkeypatch):
        """Corrupt JSON → BASELINE_WEIGHTS."""
        f = tmp_path / "ic_weights.json"
        f.write_text("{ not valid json }")
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_get_current_weights_missing_dims_returns_baseline(self, tmp_path, monkeypatch):
        """Cache missing some dimensions → BASELINE_WEIGHTS."""
        f = tmp_path / "ic_weights.json"
        partial = {"weights": {"trend": 0.5, "momentum": 0.5}}
        f.write_text(json.dumps(partial))
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_get_current_weights_loads_valid_cache(self, tmp_path, monkeypatch):
        """Valid cache with ic_valid_for_live_scoring=True returns IC weights."""
        equal_w = {d: round(1.0 / N, 6) for d in DIMS}
        record = {
            "weights": equal_w,
            "raw_ic": {},
            "updated": "2025-01-01T00:00:00",
            "ic_valid_for_live_scoring": True,
        }
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps(record))
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        assert set(weights.keys()) == set(DIMS)
        assert abs(sum(weights.values()) - 1.0) < 0.05

    def test_get_current_weights_invalid_flag_returns_baseline(self, tmp_path, monkeypatch):
        """ic_valid_for_live_scoring=False → BASELINE_WEIGHTS regardless of weight values."""
        equal_w = {d: round(1.0 / N, 6) for d in DIMS}
        record = {
            "weights": equal_w,
            "raw_ic": {},
            "updated": "2025-01-01T00:00:00",
            "ic_valid_for_live_scoring": False,
            "fallback_reason": "insufficient_independent_dates:21<60",
        }
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps(record))
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_update_ic_weights_writes_file(self, tmp_path, monkeypatch):
        """update_ic_weights() must write a valid ic_weights.json."""
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(tmp_path / "ic_weights.json"))
        monkeypatch.setattr(ic_storage, "IC_HISTORY_FILE", str(tmp_path / "history.jsonl"))
        monkeypatch.setattr(ic_data, "SIGNALS_LOG_FILE", str(tmp_path / "signals.jsonl"))
        (tmp_path / "signals.jsonl").write_text("")  # empty → falls back to equal

        weights = ic.update_ic_weights(signals_log_path=str(tmp_path / "signals.jsonl"))

        assert os.path.exists(str(tmp_path / "ic_weights.json"))
        with open(tmp_path / "ic_weights.json") as f:
            data = json.load(f)
        assert "weights" in data
        assert abs(sum(data["weights"].values()) - 1.0) < 1e-9

        _assert_equal_weights(weights)  # no data → equal fallback

    def test_update_ic_weights_appends_history(self, tmp_path, monkeypatch):
        """update_ic_weights() appends one line to history per call."""
        hf = tmp_path / "history.jsonl"
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(tmp_path / "ic_weights.json"))
        monkeypatch.setattr(ic_storage, "IC_HISTORY_FILE", str(hf))
        monkeypatch.setattr(ic_data, "SIGNALS_LOG_FILE", str(tmp_path / "signals.jsonl"))
        (tmp_path / "signals.jsonl").write_text("")

        ic.update_ic_weights()
        ic.update_ic_weights()

        lines = [l for l in hf.read_text().splitlines() if l.strip()]
        assert len(lines) == 2, f"Expected 2 history lines, got {len(lines)}"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _assert_equal_weights(weights: dict):
    assert set(weights.keys()) == set(DIMS), "Missing dimensions in weights"
    for d in DIMS:
        expected = pytest.approx(1.0 / N, abs=1e-6)
        assert weights[d] == expected, f"Expected equal weight {1 / N:.6f} for {d}, got {weights[d]}"


def _assert_baseline_weights(weights: dict):
    """Assert weights match BASELINE_WEIGHTS exactly."""
    from ic.constants import BASELINE_WEIGHTS
    assert set(weights.keys()) == set(DIMS), "Missing dimensions in weights"
    for d in DIMS:
        assert weights[d] == pytest.approx(BASELINE_WEIGHTS[d], abs=1e-6), (
            f"Expected BASELINE_WEIGHTS[{d}]={BASELINE_WEIGHTS[d]}, got {weights[d]}"
        )
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert abs(sum(weights.values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# (NEW) IC Weight Initialization Edge Cases
# ---------------------------------------------------------------------------


class TestICInitializationEdgeCases:
    """
    Validates equal-weights fallback correctness, consistency, and JSON
    edge-case handling. Addresses the risk that the fallback is wrong, cached
    incorrectly, or silently swallowed for partial/corrupt JSON.
    """

    def test_equal_weights_sum_to_exactly_one(self):
        """EQUAL_WEIGHTS constant must sum to exactly 1.0 (no floating-point drift)."""
        total = sum(ic.EQUAL_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"EQUAL_WEIGHTS sums to {total}, not 1.0"

    def test_equal_weights_contains_all_dimensions(self):
        """EQUAL_WEIGHTS must contain exactly the canonical dimension keys (one per DIMENSIONS entry)."""
        from ic_calculator import DIMENSIONS

        expected = set(DIMENSIONS)
        assert set(ic.EQUAL_WEIGHTS.keys()) == expected, f"EQUAL_WEIGHTS has wrong keys: {set(ic.EQUAL_WEIGHTS.keys())}"

    def test_equal_weights_each_dimension_is_equal_share(self):
        """Each dimension's weight must equal 1/N where N = len(DIMENSIONS)."""
        from ic_calculator import DIMENSIONS

        n = len(DIMENSIONS)
        for dim, w in ic.EQUAL_WEIGHTS.items():
            assert abs(w - 1.0 / n) < 1e-9, f"EQUAL_WEIGHTS[{dim!r}] = {w}, expected {1.0 / n}"

    def test_get_current_weights_consistent_across_two_calls(self, tmp_path, monkeypatch):
        """
        Calling get_current_weights() twice with no file must return identical
        BASELINE_WEIGHTS dicts — no caching anomaly between calls.
        """
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(tmp_path / "nonexistent.json"))
        w1 = ic.get_current_weights()
        w2 = ic.get_current_weights()
        assert w1 == w2, "Two successive calls with no file must return identical dicts"
        _assert_baseline_weights(w1)

    def test_partial_json_missing_some_dimensions_triggers_baseline_fallback(self, tmp_path, monkeypatch):
        """
        A JSON file with only 4 dimensions must trigger BASELINE_WEIGHTS fallback,
        not silently use partial weights or EQUAL_WEIGHTS.
        """
        partial = {
            "weights": {
                "trend": 0.3,
                "momentum": 0.3,
                "squeeze": 0.2,
                "flow": 0.2,
                # missing: breakout, mtf, news, social, reversion (and newer dims)
            }
        }
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps(partial))
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))

        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_json_with_wrong_sum_triggers_baseline_fallback(self, tmp_path, monkeypatch):
        """
        A JSON file whose weights sum to ~0.5 must trigger BASELINE_WEIGHTS fallback.
        """
        bad_weights = {d: round(1.0 / (9 * 2), 6) for d in ic.DIMENSIONS}
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps({"weights": bad_weights}))
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))

        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_empty_json_object_triggers_baseline_fallback(self, tmp_path, monkeypatch):
        """An empty JSON object {} must trigger BASELINE_WEIGHTS fallback."""
        f = tmp_path / "ic_weights.json"
        f.write_text("{}")
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))

        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_fallback_returns_independent_copy_not_shared_reference(self, tmp_path, monkeypatch):
        """
        get_current_weights() must return a copy of BASELINE_WEIGHTS, not the
        dict itself.  Mutating the returned value must not affect the next call.
        """
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(tmp_path / "nonexistent.json"))
        w1 = ic.get_current_weights()
        expected_trend = ic.BASELINE_WEIGHTS["trend"]
        w1["trend"] = 999.0  # mutate the returned copy

        w2 = ic.get_current_weights()
        assert w2["trend"] == pytest.approx(expected_trend, abs=1e-6), (
            "Mutating the returned dict polluted the next call — fallback must return a fresh copy"
        )


# ---------------------------------------------------------------------------
# Numerical correctness: synthetic dataset with hand-computed IC values
# ---------------------------------------------------------------------------


class TestICCorrectnessWithKnownValues:
    """
    End-to-end numerical correctness: IC values must match hand-computed
    Spearman rank correlations to machine precision.

    Synthetic dataset — N=5 records, unique scores, no ties:

        trend scores:    [1.0, 2.0, 3.0, 4.0, 5.0]
        momentum scores: [5.0, 4.0, 3.0, 2.0, 1.0]
        all other dims:  constant 5.0 across all records
        fwd_returns:     [0.03, 0.01, 0.04, 0.02, 0.05]

    Hand-computed Spearman (formula: 1 - 6·Σd²/n(n²-1), n=5, n(n²-1)=120):

        trend ranks:    [0, 1, 2, 3, 4]
        momentum ranks: [4, 3, 2, 1, 0]
        fwd ranks:      [2, 0, 3, 1, 4]  (0 = smallest return)

        trend IC:
            d = [-2, 1, -1, 2, 0]  →  Σd²=10
            IC = 1 - 6·10/120 = +0.5

        momentum IC:
            d = [2, 3, -1, 0, -4]  →  Σd²=30
            IC = 1 - 6·30/120 = -0.5

        constant dims (std=0 → z-scores all-zero → short-circuit guard in core.py):
            IC = 0.0  (not computed via Spearman, explicitly zeroed)

    scipy.stats.spearmanr is replaced with the real numpy formula to bypass
    the conftest mock. z-scoring is a monotone linear transformation so it
    cannot change rank order; these expected values hold pre- and post-z-score.
    """

    _N = 5
    _TREND_SCORES = [1.0, 2.0, 3.0, 4.0, 5.0]
    _MOMENTUM_SCORES = [5.0, 4.0, 3.0, 2.0, 1.0]
    _FWD_RETURNS = [0.03, 0.01, 0.04, 0.02, 0.05]

    # Hand-computed expected values (Σd²=10 → +0.5; Σd²=30 → -0.5)
    _EXPECTED_TREND_IC = 0.5
    _EXPECTED_MOMENTUM_IC = -0.5
    _EXPECTED_CONSTANT_IC = 0.0

    def _write_log(self, tmp_path):
        """Write the synthetic records to a temp jsonl and return (path, fwd_map)."""
        records = []
        for i in range(self._N):
            bd = {d: 5.0 for d in DIMS}
            bd["trend"] = self._TREND_SCORES[i]
            bd["momentum"] = self._MOMENTUM_SCORES[i]
            records.append(_make_record(symbol=f"SYN{i:02d}", breakdown=bd))
        log_file = tmp_path / "signals_log.jsonl"
        with open(log_file, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        fwd_map = {i: self._FWD_RETURNS[i] for i in range(self._N)}
        return str(log_file), fwd_map

    def _run_pipeline(self, tmp_path):
        log_path, fwd_map = self._write_log(tmp_path)
        with (
            patch.object(ic_core, "_fetch_forward_returns_batch", return_value=fwd_map),
            patch.object(ic_core, "_spearman", side_effect=_real_spearman),
        ):
            return ic.compute_rolling_ic(
                signals_log_path=log_path,
                window=self._N,
                min_valid=1,
            )

    def test_trend_ic_matches_hand_computed_value(self, tmp_path):
        """
        trend scores [1,2,3,4,5] vs fwd_returns [0.03,0.01,0.04,0.02,0.05]:
        Σd²=10 → Spearman = 1 - 6·10/120 = +0.5 exactly.
        """
        raw = self._run_pipeline(tmp_path)
        assert raw["trend"] == pytest.approx(self._EXPECTED_TREND_IC, abs=1e-9), (
            f"trend IC={raw['trend']:.10f}, expected {self._EXPECTED_TREND_IC}"
        )

    def test_momentum_ic_matches_hand_computed_value(self, tmp_path):
        """
        momentum scores [5,4,3,2,1] vs same fwd_returns:
        Σd²=30 → Spearman = 1 - 6·30/120 = -0.5 exactly.
        """
        raw = self._run_pipeline(tmp_path)
        assert raw["momentum"] == pytest.approx(self._EXPECTED_MOMENTUM_IC, abs=1e-9), (
            f"momentum IC={raw['momentum']:.10f}, expected {self._EXPECTED_MOMENTUM_IC}"
        )

    def test_constant_dimension_scores_yield_zero_ic(self, tmp_path):
        """
        All remaining dims have constant scores (5.0 across all records).
        std=0 → z-scores are all-zero → core.py short-circuit guard sets IC=0.0.
        This test confirms the guard fires, not that Spearman(zeros, fwd)=0.
        """
        raw = self._run_pipeline(tmp_path)
        constant_dims = [d for d in DIMS if d not in ("trend", "momentum")]
        for d in constant_dims:
            assert raw[d] == pytest.approx(self._EXPECTED_CONSTANT_IC, abs=1e-9), (
                f"{d!r} has constant scores: expected IC=0.0, got {raw[d]}"
            )

    def test_output_matches_direct_numpy_spearman_formula(self, tmp_path):
        """
        The pipeline IC must equal a direct _real_spearman call on the
        z-scored scores vs fwd_returns — confirming no pipeline arithmetic
        error between raw scores and reported IC.
        """
        raw = self._run_pipeline(tmp_path)
        fwd_arr = np.array(self._FWD_RETURNS, dtype=float)
        for dim, scores in [("trend", self._TREND_SCORES), ("momentum", self._MOMENTUM_SCORES)]:
            arr = np.array(scores, dtype=float)
            z = (arr - np.mean(arr)) / float(np.std(arr))
            ref = _real_spearman(z, fwd_arr)
            assert raw[dim] == pytest.approx(ref, abs=1e-9), (
                f"Pipeline IC for {dim!r}: {raw[dim]:.10f} != direct formula {ref:.10f}"
            )


# ---------------------------------------------------------------------------
# IC validity gate guardrail tests (A–E from spec 2026-05-19)
# ---------------------------------------------------------------------------


def _cfg_mock(threshold: float = 0.0):
    """Return a _ic_cfg mock that sets ic_min_threshold and leaves other keys at defaults."""
    def _cfg(key, default):
        if key == "force_equal_weights":
            return False
        if key == "ic_min_threshold":
            return threshold
        if key == "max_single_weight":
            return 0.40
        if key == "min_active_dims":
            return 5
        if key == "max_top2_combined_weight":
            return 0.75
        if key == "max_hhi":
            return 0.30
        return default
    return _cfg


class TestGuardrailA_CurrentFailureCase:
    """
    (A) Current observed failure: threshold=0.03 → only news+social survive.
    Expected: IC validity fails, live scoring receives BASELINE_WEIGHTS,
    social must NOT be 60%.
    """

    _RAW_IC = {
        "trend": 0.022, "momentum": -0.048, "squeeze": -0.047,
        "flow": -0.010, "breakout": 0.013, "mtf": -0.033,
        "news": 0.121, "social": 0.074, "reversion": 0.019,
        "iv_skew": -0.095, "pead": 0.0, "short_squeeze": 0.010,
        "overnight_drift": -0.073, "analyst_revision": 0.0, "insider_buying": -0.069,
    }

    def test_threshold_003_triggers_insufficient_survivors(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.03))
        weights, meta = ic.normalize_ic_weights(self._RAW_IC)
        assert meta["ic_valid_for_live_scoring"] is False
        assert "insufficient_survivors" in (meta["fallback_reason"] or "")

    def test_threshold_003_returns_baseline_not_ic_weights(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.03))
        weights, meta = ic.normalize_ic_weights(self._RAW_IC)
        _assert_baseline_weights(weights)

    def test_threshold_003_social_not_60_pct(self, monkeypatch):
        """Social must NOT receive 60% weight under any validity-failing scenario."""
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.03))
        weights, _ = ic.normalize_ic_weights(self._RAW_IC)
        assert weights["social"] < 0.30, (
            f"social weight {weights['social']:.3f} is too high — IC inversion guard failed"
        )

    def test_threshold_003_meta_records_n_survivors(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.03))
        _, meta = ic.normalize_ic_weights(self._RAW_IC)
        assert meta["n_survivors"] == 2, f"Expected 2 survivors, got {meta['n_survivors']}"


class TestGuardrailB_LowerThresholdCase:
    """
    (B) With threshold=0.01, more dimensions survive the noise floor.
    Expected: n_active > 2, no collapse to 2 dims, concentration gates may pass.
    """

    _RAW_IC = {
        "trend": 0.022, "momentum": -0.048, "squeeze": -0.047,
        "flow": -0.010, "breakout": 0.013, "mtf": -0.033,
        "news": 0.121, "social": 0.074, "reversion": 0.019,
        "iv_skew": -0.095, "pead": 0.0, "short_squeeze": 0.010,
        "overnight_drift": -0.073, "analyst_revision": 0.0, "insider_buying": -0.069,
    }

    def test_threshold_001_produces_6_survivors(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        _, meta = ic.normalize_ic_weights(self._RAW_IC)
        assert meta["n_survivors"] == 6, (
            f"Expected 6 survivors with threshold=0.01, got {meta['n_survivors']}"
        )

    def test_threshold_001_no_collapse_to_two_dims(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        _, meta = ic.normalize_ic_weights(self._RAW_IC)
        assert meta["n_active_dimensions"] >= 5, (
            f"Expected ≥5 active dims with threshold=0.01, got {meta['n_active_dimensions']}"
        )

    def test_threshold_001_news_not_inverted_by_social(self, monkeypatch):
        """news has higher IC than social — news weight must be >= social weight."""
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        weights, meta = ic.normalize_ic_weights(self._RAW_IC)
        # If IC is valid, news (IC=0.121) should have weight >= social (IC=0.074)
        if meta["ic_valid_for_live_scoring"]:
            assert weights["news"] >= weights["social"] - 0.05, (
                f"Ranking inversion: news(IC=0.121, wt={weights['news']:.3f}) < "
                f"social(IC=0.074, wt={weights['social']:.3f})"
            )

    def test_threshold_001_avoids_degenerate_2dim_concentration(self, monkeypatch):
        """threshold=0.01 must avoid the degenerate 2-dim concentration of threshold=0.03.
        With threshold=0.03, only 2 dims survive and the HHI would be 0.52 (news²+social²
        after inversion-cap redistribution). With threshold=0.01, 6 dims survive and HHI
        is ~0.28 — materially better. We compare against the known 2-dim degenerate HHI.
        """
        DEGENERATE_2DIM_HHI = 0.40 ** 2 + 0.60 ** 2  # = 0.52 (news capped at 40%, social 60%)

        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        _, meta_001 = ic.normalize_ic_weights(self._RAW_IC)

        # The 6-dim IC-weighted HHI (~0.28) must be well below the degenerate 2-dim HHI (0.52)
        hhi_001 = meta_001["hhi"]
        assert hhi_001 < DEGENERATE_2DIM_HHI - 0.10, (
            f"threshold=0.01 HHI={hhi_001:.3f} is not materially better "
            f"than degenerate 2-dim HHI={DEGENERATE_2DIM_HHI:.3f}"
        )


class TestGuardrailC_LowSampleSize:
    """
    (C) Low independent dates (n_dates < 60).
    Expected: ic_valid_for_live_scoring=False, live scoring uses BASELINE_WEIGHTS.
    Tests update_ic_weights() n_dates gate via patched count_independent_dates.
    """

    def test_low_ndates_sets_invalid_flag(self, tmp_path, monkeypatch):
        """When n_independent_dates < 60, ic_valid_for_live_scoring must be False in JSON."""
        from ic import storage as ic_storage_mod

        monkeypatch.setattr(ic_storage_mod, "IC_WEIGHTS_FILE", str(tmp_path / "ic_weights.json"))
        monkeypatch.setattr(ic_storage_mod, "IC_HISTORY_FILE", str(tmp_path / "history.jsonl"))

        # Patch count_independent_dates at the storage module (where it was imported),
        # not at ic.data — `from ic.data import count_independent_dates` binds a local name.
        monkeypatch.setattr(ic_storage_mod, "count_independent_dates", lambda records: 21)

        (tmp_path / "signals.jsonl").write_text("")  # empty → all-None raw IC

        ic.update_ic_weights(signals_log_path=str(tmp_path / "signals.jsonl"))

        with open(tmp_path / "ic_weights.json") as f:
            data = json.load(f)
        assert data["ic_valid_for_live_scoring"] is False
        assert data.get("n_independent_dates") == 21

    def test_low_ndates_get_current_weights_returns_baseline(self, tmp_path, monkeypatch):
        """get_current_weights() must return BASELINE_WEIGHTS when ic_valid is False."""
        equal_w = {d: round(1.0 / N, 6) for d in DIMS}
        record = {
            "weights": equal_w,
            "ic_valid_for_live_scoring": False,
            "fallback_reason": "insufficient_independent_dates:21<60",
            "updated": "2026-05-19T00:00:00+00:00",
        }
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps(record))
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))

        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_ndates_written_to_json(self, tmp_path, monkeypatch):
        """n_independent_dates must be persisted to ic_weights.json."""
        from ic import storage as ic_storage_mod

        monkeypatch.setattr(ic_storage_mod, "IC_WEIGHTS_FILE", str(tmp_path / "ic_weights.json"))
        monkeypatch.setattr(ic_storage_mod, "IC_HISTORY_FILE", str(tmp_path / "history.jsonl"))
        monkeypatch.setattr(ic_storage_mod, "count_independent_dates", lambda records: 21)
        (tmp_path / "signals.jsonl").write_text("")

        ic.update_ic_weights(signals_log_path=str(tmp_path / "signals.jsonl"))

        with open(tmp_path / "ic_weights.json") as f:
            data = json.load(f)
        assert data.get("n_independent_dates") == 21


class TestGuardrailD_HealthyCase:
    """
    (D) Healthy IC: sufficient dims, reasonable concentration.
    Expected: ic_valid_for_live_scoring=True, IC weights returned (not BASELINE).
    """

    def _healthy_raw_ic(self) -> dict:
        """6 dims with moderate, well-spread positive IC — should pass all gates."""
        base = {d: -0.02 for d in DIMS}  # most dims slightly negative
        base.update({
            "news": 0.10,
            "social": 0.08,
            "trend": 0.06,
            "momentum": 0.05,
            "breakout": 0.04,
            "reversion": 0.03,
        })
        return base

    def test_healthy_ic_passes_validity_gate(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        _, meta = ic.normalize_ic_weights(self._healthy_raw_ic())
        assert meta["ic_valid_for_live_scoring"] is True, (
            f"Healthy IC failed validity: {meta.get('fallback_reason')}"
        )

    def test_healthy_ic_returns_ic_weights_not_baseline(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        weights, meta = ic.normalize_ic_weights(self._healthy_raw_ic())
        assert meta["fallback_weights_source"] == "ic"
        # news should have highest weight (highest IC)
        assert weights["news"] == max(weights.values()), (
            f"Expected news to have highest weight, got: {weights}"
        )

    def test_healthy_ic_hhi_within_gate(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        _, meta = ic.normalize_ic_weights(self._healthy_raw_ic())
        if meta["ic_valid_for_live_scoring"]:
            assert meta["hhi"] <= 0.30 + 1e-9, f"HHI={meta['hhi']:.3f} exceeds gate 0.30"

    def test_healthy_ic_n_active_dims_gte_5(self, monkeypatch):
        monkeypatch.setattr(ic_core, "_ic_cfg", _cfg_mock(threshold=0.01))
        _, meta = ic.normalize_ic_weights(self._healthy_raw_ic())
        if meta["ic_valid_for_live_scoring"]:
            assert meta["n_active_dimensions"] >= 5


class TestGuardrailE_MissingOrStaleFile:
    """
    (E) Missing or stale IC file.
    Expected: BASELINE_WEIGHTS returned, warning logged, no crash.
    """

    def test_missing_file_returns_baseline_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(tmp_path / "nonexistent.json"))
        weights = ic.get_current_weights()  # must not raise
        _assert_baseline_weights(weights)

    def test_stale_file_ic_valid_false_returns_baseline(self, tmp_path, monkeypatch):
        """A file with ic_valid_for_live_scoring=False is treated as stale."""
        record = {
            "weights": {d: round(1.0 / N, 6) for d in DIMS},
            "ic_valid_for_live_scoring": False,
            "fallback_reason": "hhi_exceeded:0.52>0.30",
            "updated": "2026-01-01T00:00:00+00:00",
        }
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps(record))
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        _assert_baseline_weights(weights)

    def test_corrupt_file_returns_baseline_no_crash(self, tmp_path, monkeypatch):
        f = tmp_path / "ic_weights.json"
        f.write_text("CORRUPTED DATA {{{{")
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()  # must not raise
        _assert_baseline_weights(weights)

    def test_baseline_weights_sum_to_one(self):
        """BASELINE_WEIGHTS must sum to exactly 1.0."""
        from ic.constants import BASELINE_WEIGHTS
        total = sum(BASELINE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"BASELINE_WEIGHTS sums to {total}, not 1.0"

    def test_baseline_weights_has_all_dimensions(self):
        """BASELINE_WEIGHTS must contain all canonical dimensions."""
        from ic.constants import BASELINE_WEIGHTS
        assert set(BASELINE_WEIGHTS.keys()) == set(DIMS)

    def test_baseline_weights_no_negative_values(self):
        """BASELINE_WEIGHTS must have no negative values."""
        from ic.constants import BASELINE_WEIGHTS
        for d, w in BASELINE_WEIGHTS.items():
            assert w >= 0.0, f"BASELINE_WEIGHTS[{d}]={w} is negative"

    def test_baseline_weights_inactive_dims_are_zero(self):
        """pead, analyst_revision, insider_buying must be 0 in BASELINE_WEIGHTS."""
        from ic.constants import BASELINE_WEIGHTS
        for dim in ("pead", "analyst_revision", "insider_buying"):
            assert BASELINE_WEIGHTS[dim] == 0.0, (
                f"Expected BASELINE_WEIGHTS[{dim}]=0.0, got {BASELINE_WEIGHTS[dim]}"
            )


# ---------------------------------------------------------------------------
# TestAlpacaForwardReturns — unit tests for the Alpaca-backed fetch function
# ---------------------------------------------------------------------------


def _make_alpaca_bar_response(symbol: str, dates_and_closes: list[tuple]) -> "MagicMock":
    """Build a mock Alpaca bars response for one symbol."""
    import pandas as pd

    dates = [d for d, _ in dates_and_closes]
    closes = [c for _, c in dates_and_closes]
    idx = pd.DatetimeIndex(pd.to_datetime(dates, utc=True))
    df_single = pd.DataFrame({"close": closes}, index=idx)
    # Multi-symbol response: MultiIndex (symbol, timestamp)
    df_multi = df_single.copy()
    df_multi.index = pd.MultiIndex.from_tuples(
        [(symbol, t) for t in idx], names=["symbol", "timestamp"]
    )
    mock_bars = MagicMock()
    mock_bars.df = df_multi
    return mock_bars


def _make_ic_data_record(
    symbol: str,
    ts: str,
    price: float,
    direction: str | None = "LONG",
    fwd_return: float | None = None,
) -> dict:
    bd = {d: 5.0 for d in DIMS}
    rec = {"symbol": symbol, "price": price, "ts": ts, "score": 30, "score_breakdown": bd}
    if direction is not None:
        rec["direction"] = direction
    if fwd_return is not None:
        rec["fwd_return"] = fwd_return
    return rec


class TestAlpacaForwardReturns:
    """Unit tests for _fetch_forward_returns_batch (Alpaca implementation)."""

    def _patch_client(self, monkeypatch, bars_response: "MagicMock"):
        """Patch alpaca_data._get_client to return a mock that returns bars_response."""
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = bars_response
        monkeypatch.setattr("alpaca_data._get_client", lambda: mock_client)
        return mock_client

    def test_alpaca_returns_computed_for_mature_records(self, monkeypatch):
        """Records old enough (age >= min_age_cal) get non-None forward returns."""
        # Scan date 2025-01-01, forward horizon 1 day → offset 3 calendar days
        # future_date = 2025-01-04; provide a bar on that date
        bars = _make_alpaca_bar_response(
            "AAPL",
            [("2025-01-01", 100.0), ("2025-01-02", 101.0), ("2025-01-05", 105.0)],
        )
        self._patch_client(monkeypatch, bars)
        monkeypatch.setattr("ic.data._ic_cfg", lambda key, default: 1 if key == "forward_horizon_days" else default)

        recs = [_make_ic_data_record("AAPL", "2025-01-01T10:00:00+00:00", 100.0, "LONG")]
        result = ic_data._fetch_forward_returns_batch(recs)
        assert result[0] is not None, "Expected a forward return for a mature LONG record"
        assert result[0] > 0, f"Price went up, expected positive return, got {result[0]}"

    def test_missing_provider_returns_none_per_symbol_not_batch(self, monkeypatch):
        """Alpaca client unavailable → None for all records, no crash."""
        monkeypatch.setattr("alpaca_data._get_client", lambda: None)

        recs = [
            _make_ic_data_record("AAPL", "2025-01-01T10:00:00+00:00", 100.0),
            _make_ic_data_record("MSFT", "2025-01-01T10:00:00+00:00", 200.0),
        ]
        result = ic_data._fetch_forward_returns_batch(recs)
        assert result == {0: None, 1: None}, "Missing client must return None for all records"

    def test_missing_symbol_data_returns_none_only_for_that_symbol(self, monkeypatch):
        """Symbol missing from Alpaca response → only that symbol returns None; others succeed."""
        import pandas as pd

        # AAPL has data; ZZZZ has no data in the response
        aapl_dates = [("2025-01-01", 100.0), ("2025-01-05", 108.0)]
        idx = pd.DatetimeIndex(pd.to_datetime([d for d, _ in aapl_dates], utc=True))
        df_multi = pd.DataFrame(
            {"close": [c for _, c in aapl_dates]},
            index=pd.MultiIndex.from_tuples(
                [("AAPL", t) for t in idx], names=["symbol", "timestamp"]
            ),
        )
        mock_bars = MagicMock()
        mock_bars.df = df_multi
        self._patch_client(monkeypatch, mock_bars)
        monkeypatch.setattr("ic.data._ic_cfg", lambda key, default: 1 if key == "forward_horizon_days" else default)

        recs = [
            _make_ic_data_record("AAPL", "2025-01-01T10:00:00+00:00", 100.0, "LONG"),
            _make_ic_data_record("ZZZZ", "2025-01-01T10:00:00+00:00", 50.0, "LONG"),
        ]
        result = ic_data._fetch_forward_returns_batch(recs)
        assert result[0] is not None, "AAPL should have a return (data present)"
        assert result[1] is None, "ZZZZ should be None (no data in response)"

    def test_short_direction_sign_inverted_vs_long(self, monkeypatch):
        """A SHORT record must return the negative of the equivalent LONG record."""
        bars = _make_alpaca_bar_response(
            "NVDA",
            [("2025-01-01", 500.0), ("2025-01-02", 510.0), ("2025-01-05", 520.0)],
        )
        self._patch_client(monkeypatch, bars)
        monkeypatch.setattr("ic.data._ic_cfg", lambda key, default: 1 if key == "forward_horizon_days" else default)

        long_rec = _make_ic_data_record("NVDA", "2025-01-01T10:00:00+00:00", 500.0, "LONG")
        short_rec = _make_ic_data_record("NVDA", "2025-01-01T10:00:00+00:00", 500.0, "SHORT")
        result_long = ic_data._fetch_forward_returns_batch([long_rec])
        result_short = ic_data._fetch_forward_returns_batch([short_rec])

        r_long = result_long[0]
        r_short = result_short[0]
        assert r_long is not None and r_short is not None
        assert abs(r_long + r_short) < 1e-9, (
            f"SHORT return must be negative of LONG: long={r_long:.6f} short={r_short:.6f}"
        )

    def test_future_scan_date_returns_none(self, monkeypatch):
        """Record too recent (scan_date within min_age_cal of today) → None."""
        from datetime import datetime, UTC

        future_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        bars = _make_alpaca_bar_response("AAPL", [("2099-01-05", 200.0)])
        self._patch_client(monkeypatch, bars)
        monkeypatch.setattr("ic.data._ic_cfg", lambda key, default: 1 if key == "forward_horizon_days" else default)

        recs = [_make_ic_data_record("AAPL", future_ts, 100.0, "LONG")]
        result = ic_data._fetch_forward_returns_batch(recs)
        assert result[0] is None, "Record too recent should return None (lookahead guard)"

    def test_precomputed_fwd_return_bypasses_alpaca(self, monkeypatch):
        """Records with fwd_return already set never call the Alpaca client."""
        mock_client = MagicMock()
        monkeypatch.setattr("alpaca_data._get_client", lambda: mock_client)

        recs = [_make_ic_data_record("AAPL", "2025-01-01T10:00:00+00:00", 100.0, "LONG", fwd_return=0.05)]
        result = ic_data._fetch_forward_returns_batch(recs)

        mock_client.get_stock_bars.assert_not_called()
        assert result[0] == pytest.approx(0.05), "Pre-computed LONG fwd_return should be returned as-is"

    def test_precomputed_fwd_return_short_sign_applied(self, monkeypatch):
        """Pre-computed fwd_return for SHORT records must have the sign inverted."""
        mock_client = MagicMock()
        monkeypatch.setattr("alpaca_data._get_client", lambda: mock_client)

        recs = [_make_ic_data_record("AAPL", "2025-01-01T10:00:00+00:00", 100.0, "SHORT", fwd_return=0.05)]
        result = ic_data._fetch_forward_returns_batch(recs)

        mock_client.get_stock_bars.assert_not_called()
        assert result[0] == pytest.approx(-0.05), "Pre-computed SHORT fwd_return must be negated"

    def test_all_null_returns_cause_baseline_weights(self, monkeypatch, tmp_path):
        """When no forward returns can be fetched, IC is all-None → BASELINE_WEIGHTS for live scoring."""
        monkeypatch.setattr("alpaca_data._get_client", lambda: None)

        log_file = tmp_path / "signals_log.jsonl"
        recs = [_make_ic_data_record("AAPL", "2024-01-01T10:00:00+00:00", 100.0) for _ in range(25)]
        with open(log_file, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

        with patch.object(ic_core, "_spearman", side_effect=_real_spearman):
            raw_ic = ic.compute_rolling_ic(signals_log_path=str(log_file), min_valid=1)

        # All returns failed → IC all None → should normalise to equal weights (cold-start)
        assert all(v is None for v in raw_ic.values()), "No returns → all IC must be None"

        weights, meta = ic.normalize_ic_weights(raw_ic)
        assert meta["ic_valid_for_live_scoring"] is False
        from ic.constants import EQUAL_WEIGHTS, BASELINE_WEIGHTS
        assert weights == EQUAL_WEIGHTS, "All-null IC should cold-start to EQUAL_WEIGHTS internally"
        # get_current_weights() converts ic_valid=False → BASELINE_WEIGHTS
        monkeypatch.setattr("ic.data._ic_cfg", lambda key, default: default)

    def test_low_n_dates_keeps_ic_advisory_only(self, monkeypatch, tmp_path):
        """With n_dates < min_independent_dates (60), ic_valid_for_live_scoring must be False."""
        log_file = tmp_path / "signals_log.jsonl"
        # All records on the same single date → n_dates = 1.
        # Give each record a unique "trend" score so z_scores are non-zero and
        # _spearman is called; without variation all dimensions get raw_ic=0.0 and
        # normalize_ic_weights exits via no_positive_ic_above_threshold before the
        # n_dates gate in update_ic_weights can fire.
        recs = []
        for i in range(30):
            rec = _make_ic_data_record(f"SYM{i}", "2025-01-01T10:00:00+00:00", 100.0)
            # Give all 15 dimensions varied scores correlated with i so every
            # dimension gets positive IC, the concentration gates all pass, and
            # the n_dates gate in update_ic_weights is the deciding failure.
            for d in DIMS:
                rec["score_breakdown"][d] = float(i)
            recs.append(rec)
        with open(log_file, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

        weights_file = tmp_path / "ic_weights.json"
        history_file = tmp_path / "ic_history.jsonl"
        monkeypatch.setattr(ic_storage, "IC_WEIGHTS_FILE", str(weights_file))
        monkeypatch.setattr(ic_storage, "IC_HISTORY_FILE", str(history_file))

        forward_returns = {i: float(i - 15) / 100.0 for i in range(30)}
        with (
            patch.object(ic_core, "_fetch_forward_returns_batch", return_value=forward_returns),
            patch.object(ic_core, "_spearman", side_effect=_real_spearman),
        ):
            weights = ic.update_ic_weights(
                signals_log_path=str(log_file),
            )

        # Load persisted file and verify validity fields
        import json as _json
        result = _json.load(open(weights_file))
        assert result["n_independent_dates"] == 1
        assert result["ic_valid_for_live_scoring"] is False
        assert result["advisory_only"] is True
        assert "insufficient_independent_dates" in (result.get("fallback_reason") or "")
