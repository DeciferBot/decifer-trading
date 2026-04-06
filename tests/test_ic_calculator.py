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
for _mod in ["ib_async", "ib_insync", "anthropic", "praw",
             "feedparser", "tvDatafeed", "requests_html", "schedule", "colorama"]:
    sys.modules.setdefault(_mod, MagicMock())

# Minimal config stub
import config as _cfg_mod
_cfg = {
    "log_file": "/dev/null", "trade_log": "/dev/null",
    "order_log": "/dev/null", "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-6", "max_tokens": 1000,
    "signals_log": "/dev/null", "audit_log": "/dev/null",
}
if hasattr(_cfg_mod, "CONFIG"):
    for k, v in _cfg.items():
        _cfg_mod.CONFIG.setdefault(k, v)
else:
    _cfg_mod.CONFIG = _cfg

import ic_calculator as ic


DIMS = ic.DIMENSIONS
N    = len(DIMS)


# ---------------------------------------------------------------------------
# Helper to build a signals-log record
# ---------------------------------------------------------------------------

def _make_record(symbol="AAPL", price=100.0, ts="2025-01-01T10:00:00+00:00",
                 breakdown=None):
    if breakdown is None:
        breakdown = {d: 5.0 for d in DIMS}
    return {
        "symbol":         symbol,
        "price":          price,
        "ts":             ts,
        "score":          30,
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
    d  = rx - ry
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
            bd["trend"] = float(i)       # monotone → perfect rank correlation
            records.append(_make_record(symbol=f"SYM{i:02d}", breakdown=bd))

        log_file = tmp_path / "signals_log.jsonl"
        with open(log_file, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        forward_returns = {i: float(i) / 100.0 for i in range(30)}

        with patch.object(ic, "_fetch_forward_returns_batch",
                          return_value=forward_returns), \
             patch.object(ic, "_spearman", side_effect=_real_spearman):
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

        with patch.object(ic, "_fetch_forward_returns_batch",
                          return_value=forward_returns), \
             patch.object(ic, "_spearman", side_effect=_real_spearman):
            raw = ic.compute_rolling_ic(
                signals_log_path=str(log_file),
                window=30,
                min_valid=1,
            )

        assert raw["momentum"] is not None
        assert raw["momentum"] < -0.8, (
            f"Expected large negative IC, got {raw['momentum']:.4f}"
        )

    def test_insufficient_records_returns_none_ic(self, tmp_path):
        """Fewer than min_valid records → every dimension returns None."""
        records = [_make_record() for _ in range(10)]  # < 20 min
        log_file = tmp_path / "signals_log.jsonl"
        with open(log_file, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        forward_returns = {i: 0.01 for i in range(10)}
        with patch.object(ic, "_fetch_forward_returns_batch",
                          return_value=forward_returns):
            raw = ic.compute_rolling_ic(
                signals_log_path=str(log_file),
                min_valid=20,
            )

        assert all(v is None for v in raw.values()), (
            f"Expected all None, got {raw}"
        )

    def test_empty_log_returns_none_ic(self, tmp_path):
        """Empty signals_log → every dimension returns None."""
        log_file = tmp_path / "empty.jsonl"
        log_file.write_text("")

        raw = ic.compute_rolling_ic(signals_log_path=str(log_file))
        assert all(v is None for v in raw.values())

    def test_missing_score_breakdown_records_skipped(self, tmp_path):
        """Records without score_breakdown are silently skipped."""
        records = []
        for i in range(25):
            rec = {"symbol": "AAPL", "price": 100.0,
                   "ts": "2025-01-01T10:00:00+00:00", "score": 30,
                   "score_breakdown": {}}  # empty → invalid
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

    def test_negative_ic_gives_zero_weight(self):
        """Negative IC must produce zero weight, not inverted."""
        raw = {d: 0.1 for d in DIMS}
        raw["trend"]    = -0.5  # strongly negative → must be zeroed
        raw["momentum"] = -0.2  # negative → zero

        weights, _ = ic.normalize_ic_weights(raw)

        assert weights["trend"]    == 0.0, "Negative IC must produce zero weight"
        assert weights["momentum"] == 0.0, "Negative IC must produce zero weight"

    def test_zero_ic_gives_zero_weight(self):
        """IC exactly 0.0 → weight = 0.0 (ties go to zero)."""
        raw = {d: 0.1 for d in DIMS}
        raw["squeeze"] = 0.0

        weights, _ = ic.normalize_ic_weights(raw)
        assert weights["squeeze"] == 0.0

    def test_all_positive_ic_all_nonzero_weights(self):
        """All positive IC → every dimension gets a positive weight."""
        raw = {d: 0.1 for d in DIMS}
        weights, _ = ic.normalize_ic_weights(raw)
        for d, w in weights.items():
            assert w > 0, f"Expected positive weight for {d}, got {w}"

    def test_mixed_ic_only_positive_dims_get_weight(self):
        """Only the positive-IC dimensions should have non-zero weights."""
        raw = {
            "trend":     0.3,
            "momentum":  0.2,
            "squeeze":  -0.1,
            "flow":      0.0,
            "breakout":  0.15,
            "mtf":      -0.4,
            "news":      0.1,
            "social":   -0.05,
            "reversion": 0.05,
        }
        weights, _ = ic.normalize_ic_weights(raw)

        assert weights["squeeze"]  == 0.0
        assert weights["flow"]     == 0.0
        assert weights["mtf"]      == 0.0
        assert weights["social"]   == 0.0

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

    @pytest.mark.parametrize("ic_scenario", [
        {d: 0.1 for d in DIMS},                  # all positive
        {d: -0.1 for d in DIMS},                 # all negative → equal fallback
        {d: None for d in DIMS},                 # all None → equal fallback
        {"trend": 0.5, **{d: 0.0 for d in DIMS if d != "trend"}},  # single winner
        {d: (0.3 if i % 2 == 0 else -0.2)
         for i, d in enumerate(DIMS)},           # mixed
    ])
    def test_weights_sum_to_one(self, ic_scenario):
        weights, _ = ic.normalize_ic_weights(ic_scenario)
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, (
            f"Weights sum to {total}, not 1.0 (scenario={ic_scenario})"
        )

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
        monkeypatch.setattr(ic, "_ic_cfg",
                            lambda key, default: 0.05 if key == "ic_min_threshold" else default)
        raw = {d: 0.1 for d in DIMS}
        raw["news"]   = 0.02  # below 0.05 floor
        raw["social"] = 0.03  # below 0.05 floor
        weights, meta = ic.normalize_ic_weights(raw)
        assert weights["news"]   == 0.0, "news IC below floor must be zeroed"
        assert weights["social"] == 0.0, "social IC below floor must be zeroed"
        assert meta["noise_floor_applied"] is True
        assert "news"   in meta["dimensions_suppressed"]
        assert "social" in meta["dimensions_suppressed"]

    def test_noise_floor_zero_means_positive_ic_passes(self, monkeypatch):
        """With ic_min_threshold=0.0 (Phase 1 default), any positive IC should pass."""
        monkeypatch.setattr(ic, "_ic_cfg",
                            lambda key, default: 0.0 if key == "ic_min_threshold" else default)
        raw = {d: 0.01 for d in DIMS}  # all very small but positive
        weights, meta = ic.normalize_ic_weights(raw)
        for d, w in weights.items():
            assert w > 0.0, f"{d} should pass with ic_min_threshold=0.0"
        assert meta["noise_floor_applied"] is False
        assert meta["dimensions_suppressed"] == []

    def test_noise_floor_all_below_threshold_returns_equal_weights(self, monkeypatch):
        """If all dimensions are below the noise floor, fall back to equal weights."""
        monkeypatch.setattr(ic, "_ic_cfg",
                            lambda key, default: 0.10 if key == "ic_min_threshold" else default)
        raw = {d: 0.05 for d in DIMS}  # all below 0.10 floor
        weights, meta = ic.normalize_ic_weights(raw)
        _assert_equal_weights(weights)

    def test_hhi_cap_clips_dominant_dimension(self, monkeypatch):
        """If one dimension would exceed max_single_weight, it must be clipped."""
        monkeypatch.setattr(ic, "_ic_cfg", lambda key, default: (
            0.0  if key == "ic_min_threshold" else
            0.40 if key == "max_single_weight" else default
        ))
        # Give trend a huge IC so it would otherwise dominate
        raw = {d: 0.01 for d in DIMS}
        raw["trend"] = 1.0
        weights, meta = ic.normalize_ic_weights(raw)
        assert weights["trend"] <= 0.40 + 1e-9, \
            f"trend weight {weights['trend']:.3f} exceeds HHI cap 0.40"
        assert meta["hhi_capped"] is True
        assert abs(sum(weights.values()) - 1.0) < 1e-9, "weights must still sum to 1.0 after HHI cap"

    def test_hhi_cap_not_triggered_when_within_limit(self, monkeypatch):
        """No clipping if all weights are within the cap."""
        monkeypatch.setattr(ic, "_ic_cfg", lambda key, default: (
            0.0  if key == "ic_min_threshold" else
            0.40 if key == "max_single_weight" else default
        ))
        raw = {d: 0.1 for d in DIMS}  # uniform — equal 1/9 ≈ 0.111, well below 0.40
        weights, meta = ic.normalize_ic_weights(raw)
        assert meta["hhi_capped"] is False


# ---------------------------------------------------------------------------
# Cache I/O: get_current_weights / update_ic_weights
# ---------------------------------------------------------------------------

class TestCacheIO:

    def test_get_current_weights_no_file_returns_equal(self, tmp_path, monkeypatch):
        """Missing cache → equal weights."""
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE",
                            str(tmp_path / "nonexistent.json"))
        weights = ic.get_current_weights()
        _assert_equal_weights(weights)

    def test_get_current_weights_corrupt_file_returns_equal(self, tmp_path, monkeypatch):
        """Corrupt JSON → equal weights."""
        f = tmp_path / "ic_weights.json"
        f.write_text("{ not valid json }")
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        _assert_equal_weights(weights)

    def test_get_current_weights_missing_dims_returns_equal(self, tmp_path, monkeypatch):
        """Cache missing some dimensions → equal weights."""
        f = tmp_path / "ic_weights.json"
        partial = {"weights": {"trend": 0.5, "momentum": 0.5}}
        f.write_text(json.dumps(partial))
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        _assert_equal_weights(weights)

    def test_get_current_weights_loads_valid_cache(self, tmp_path, monkeypatch):
        """Valid cache is loaded and returned correctly."""
        equal_w = {d: round(1.0 / N, 6) for d in DIMS}
        record = {"weights": equal_w, "raw_ic": {}, "updated": "2025-01-01T00:00:00"}
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps(record))
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE", str(f))
        weights = ic.get_current_weights()
        assert set(weights.keys()) == set(DIMS)
        assert abs(sum(weights.values()) - 1.0) < 0.05

    def test_update_ic_weights_writes_file(self, tmp_path, monkeypatch):
        """update_ic_weights() must write a valid ic_weights.json."""
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE",  str(tmp_path / "ic_weights.json"))
        monkeypatch.setattr(ic, "IC_HISTORY_FILE",  str(tmp_path / "history.jsonl"))
        monkeypatch.setattr(ic, "SIGNALS_LOG_FILE", str(tmp_path / "signals.jsonl"))
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
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE",  str(tmp_path / "ic_weights.json"))
        monkeypatch.setattr(ic, "IC_HISTORY_FILE",  str(hf))
        monkeypatch.setattr(ic, "SIGNALS_LOG_FILE", str(tmp_path / "signals.jsonl"))
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
        assert weights[d] == expected, (
            f"Expected equal weight {1/N:.6f} for {d}, got {weights[d]}"
        )
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
        assert abs(total - 1.0) < 1e-9, (
            f"EQUAL_WEIGHTS sums to {total}, not 1.0"
        )

    def test_equal_weights_contains_all_nine_dimensions(self):
        """EQUAL_WEIGHTS must contain exactly the 9 canonical dimension keys."""
        expected = {"trend", "momentum", "squeeze", "flow", "breakout",
                    "mtf", "news", "social", "reversion"}
        assert set(ic.EQUAL_WEIGHTS.keys()) == expected, (
            f"EQUAL_WEIGHTS has wrong keys: {set(ic.EQUAL_WEIGHTS.keys())}"
        )

    def test_equal_weights_each_dimension_is_one_ninth(self):
        """Each dimension's weight must be 1/9 (equal share)."""
        for dim, w in ic.EQUAL_WEIGHTS.items():
            assert abs(w - 1.0 / 9) < 1e-9, (
                f"EQUAL_WEIGHTS[{dim!r}] = {w}, expected {1/9}"
            )

    def test_get_current_weights_consistent_across_two_calls(self, tmp_path, monkeypatch):
        """
        Calling get_current_weights() twice with no file must return identical
        equal-weights dicts — no caching anomaly between calls.
        """
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE",
                            str(tmp_path / "nonexistent.json"))
        w1 = ic.get_current_weights()
        w2 = ic.get_current_weights()
        assert w1 == w2, "Two successive calls with no file must return identical dicts"
        _assert_equal_weights(w1)

    def test_partial_json_missing_some_dimensions_triggers_fallback(self, tmp_path, monkeypatch):
        """
        A JSON file with only 4 of 9 dimensions must trigger the equal-weights
        fallback, not silently use the partial weights.
        """
        partial = {
            "weights": {
                "trend": 0.3,
                "momentum": 0.3,
                "squeeze": 0.2,
                "flow": 0.2,
                # missing: breakout, mtf, news, social, reversion
            }
        }
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps(partial))
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE", str(f))

        weights = ic.get_current_weights()
        _assert_equal_weights(weights)

    def test_json_with_wrong_sum_triggers_fallback(self, tmp_path, monkeypatch):
        """
        A JSON file whose weights sum to ~0.5 (clearly wrong) must trigger
        the equal-weights fallback, not return the malformed weights.
        """
        bad_weights = {d: round(1.0 / (9 * 2), 6) for d in ic.DIMENSIONS}
        f = tmp_path / "ic_weights.json"
        f.write_text(json.dumps({"weights": bad_weights}))
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE", str(f))

        weights = ic.get_current_weights()
        _assert_equal_weights(weights)

    def test_empty_json_object_triggers_fallback(self, tmp_path, monkeypatch):
        """An empty JSON object {} must trigger the equal-weights fallback."""
        f = tmp_path / "ic_weights.json"
        f.write_text("{}")
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE", str(f))

        weights = ic.get_current_weights()
        _assert_equal_weights(weights)

    def test_fallback_returns_independent_copy_not_shared_reference(self, tmp_path, monkeypatch):
        """
        get_current_weights() must return a copy of EQUAL_WEIGHTS, not the dict
        itself. Mutating the returned value must not affect the next call.
        """
        monkeypatch.setattr(ic, "IC_WEIGHTS_FILE",
                            str(tmp_path / "nonexistent.json"))
        w1 = ic.get_current_weights()
        w1["trend"] = 999.0   # mutate the returned copy

        w2 = ic.get_current_weights()
        assert w2["trend"] == pytest.approx(1.0 / N, abs=1e-6), (
            "Mutating the returned dict polluted the next call — "
            "fallback must return a fresh copy"
        )
