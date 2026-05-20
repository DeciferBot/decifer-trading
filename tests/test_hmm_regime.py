"""
Tests for the HMM regime signal in signals/__init__.py.

Coverage:
  1.  _hmm_fit_2state — bear series lands in state 0; bull series in state 1
  2.  _hmm_fit_2state — transition matrix is row-stochastic
  3.  _hmm_fit_2state — state means ordered (mu[0] < mu[1]) by construction
  4.  _hmm_fit_2state — rejects too-short observation arrays
  5.  _hmm_fit_2state — returns arrays of expected shapes and dtypes

  6.  get_hmm_regime_spy — returns required dict keys
  7.  get_hmm_regime_spy — "bull" result when SPY in persistent up-trend
  8.  get_hmm_regime_spy — "bear" result when SPY in persistent down-trend
  9.  get_hmm_regime_spy — disabled config → regime="unknown", source="disabled"
  10. get_hmm_regime_spy — insufficient SPY data → regime="unknown"
  11. get_hmm_regime_spy — fetch error → regime="unknown", source="error"
  12. get_hmm_regime_spy — cache is returned within TTL on same day
  13. get_hmm_regime_spy — confidence is a float in (0, 1]

  14. HMM gate: below threshold → regime="unknown", source="gate_not_met"
  15. HMM gate: uses count_eligible(), NOT count()
  16. HMM gate: degraded records (ml_eligible=False) excluded from gate count
  17. HMM gate: legacy records (no ml_eligible field) count toward gate
  18. HMM gate: exactly at threshold → gate passes, HMM runs
  19. HMM gate: eligible_trades reported in gate_not_met response
  20. HMM gate: gate_min_eligible_trades is configurable via config
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config as _config_mod

# Remove stale stub that other tests might have installed
if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]

import signals as _signals_mod
from signals import _hmm_fit_2state, get_hmm_regime_spy


# ── Helpers ───────────────────────────────────────────────────────────────────


def _bear_returns(n: int = 200, seed: int = 1) -> np.ndarray:
    """Strongly negative daily log returns (persistent bear regime)."""
    rng = np.random.default_rng(seed)
    return rng.normal(-0.008, 0.005, n)


def _bull_returns(n: int = 200, seed: int = 2) -> np.ndarray:
    """Strongly positive daily log returns (persistent bull regime)."""
    rng = np.random.default_rng(seed)
    return rng.normal(+0.008, 0.005, n)


def _mixed_returns(n: int = 300, seed: int = 3) -> np.ndarray:
    """Returns that switch regime mid-series: bear first, bull second."""
    rng = np.random.default_rng(seed)
    bear = rng.normal(-0.006, 0.005, n // 2)
    bull = rng.normal(+0.006, 0.005, n - n // 2)
    return np.concatenate([bear, bull])


def _bimodal_spy_df_ends_bull(n_each: int = 100, seed: int = 10) -> pd.DataFrame:
    """SPY price DataFrame: n_each bear returns, then n_each bull returns.
    Designed to be consumed by get_hmm_regime_spy() with lookback_days=2*n_each.
    With 50/50 distribution the median split initialises states correctly:
    mu[0]≈-0.015 (bear), mu[1]≈+0.015 (bull). Final return is in bull state."""
    rng = np.random.default_rng(seed)
    bear = rng.normal(-0.015, 0.004, n_each)
    bull = rng.normal(+0.015, 0.004, n_each)
    returns = np.concatenate([bear, bull])
    prices = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(returns)]))
    return pd.DataFrame({"Close": prices})


def _bimodal_spy_df_ends_bear(n_each: int = 100, seed: int = 11) -> pd.DataFrame:
    """SPY price DataFrame: n_each bull returns, then n_each bear returns.
    Final return is in bear state."""
    rng = np.random.default_rng(seed)
    bull = rng.normal(+0.015, 0.004, n_each)
    bear = rng.normal(-0.015, 0.004, n_each)
    returns = np.concatenate([bull, bear])
    prices = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(returns)]))
    return pd.DataFrame({"Close": prices})


def _spy_df(returns: np.ndarray) -> pd.DataFrame:
    """Build a minimal SPY price DataFrame from a log-return array."""
    prices = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(returns)]))
    return pd.DataFrame({"Close": prices})


# ── 1–5: _hmm_fit_2state ─────────────────────────────────────────────────────


class TestHmmFit2State:
    def test_bear_returns_produce_state_0_at_end(self):
        """Pure bear series: last decoded state should be 0 (lower mean)."""
        obs = _bear_returns(300)
        _, mu, _, states = _hmm_fit_2state(obs)
        assert mu[0] < mu[1], "State 0 must be the lower-mean state"
        assert states[-1] == 0, f"Expected bear state 0 at end, got {states[-1]}"

    def test_bull_returns_produce_state_1_at_end(self):
        """Pure bull series: last decoded state should be 1 (higher mean)."""
        obs = _bull_returns(300)
        _, mu, _, states = _hmm_fit_2state(obs)
        assert mu[0] < mu[1], "State 0 must be the lower-mean state"
        assert states[-1] == 1, f"Expected bull state 1 at end, got {states[-1]}"

    def test_transition_matrix_row_stochastic(self):
        """A must be row-stochastic: each row sums to 1.0."""
        obs = _mixed_returns()
        A, _, _, _ = _hmm_fit_2state(obs)
        assert A.shape == (2, 2)
        for row_idx in range(2):
            assert abs(A[row_idx].sum() - 1.0) < 1e-9, (
                f"Row {row_idx} of A sums to {A[row_idx].sum()}, expected 1.0"
            )

    def test_state_means_ordered_lower_to_higher(self):
        """mu[0] < mu[1] must hold by construction (bear state first)."""
        for seed in range(5):
            obs = np.random.default_rng(seed).normal(0, 0.01, 150)
            _, mu, _, _ = _hmm_fit_2state(obs)
            assert mu[0] <= mu[1], f"seed={seed}: mu ordering violated: {mu}"

    def test_output_shapes(self):
        """Verify shapes and dtypes of all four return values."""
        n = 100
        obs = _mixed_returns(n + 1)[1:]  # length n
        A, mu, sigma, states = _hmm_fit_2state(obs)
        assert A.shape == (2, 2)
        assert mu.shape == (2,)
        assert sigma.shape == (2,)
        assert states.shape == (n,)
        assert states.dtype in (np.int8, np.int64, int)


# ── 6–13: get_hmm_regime_spy ─────────────────────────────────────────────────


class TestGetHmmRegimeSpy:
    _REQUIRED_KEYS = {"regime", "confidence", "mu_bull", "mu_bear", "source", "lookback_days"}

    def _cfg(self, enabled: bool = True, gate: int = 0) -> dict:
        # lookback_days=200 matches the bimodal 200-return test series (100 bear + 100 bull).
        # With a 50/50 split the median initialises HMM states correctly.
        return {"enabled": enabled, "lookback_days": 200, "cache_ttl_seconds": 3600, "gate_min_eligible_trades": gate}

    def _patch_eligible(self, n: int):
        """Return a context manager patching training_store.count_eligible to return n."""
        ts_mock = MagicMock()
        ts_mock.count_eligible.return_value = n
        return patch.dict("sys.modules", {"training_store": ts_mock})

    def test_returns_required_dict_keys(self, monkeypatch):
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=0))
        spy_df = _bimodal_spy_df_ends_bull()
        with (
            self._patch_eligible(999),
            patch.object(_signals_mod, "_safe_download", return_value=spy_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()
        assert self._required_keys_present(result), f"Missing keys: {self._REQUIRED_KEYS - set(result)}"

    def _required_keys_present(self, result: dict) -> bool:
        return self._REQUIRED_KEYS.issubset(result.keys())

    def test_bull_ending_spy_returns_bull_regime(self, monkeypatch):
        """50/50 bimodal series (bear first, bull last): HMM must end in bull state.
        lookback_days=200 consumes all 200 returns ensuring clear regime separation."""
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=0))
        spy_df = _bimodal_spy_df_ends_bull()
        with (
            self._patch_eligible(999),
            patch.object(_signals_mod, "_safe_download", return_value=spy_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()
        assert result["regime"] == "bull", f"Expected 'bull', got '{result['regime']}'"

    def test_bear_ending_spy_returns_bear_regime(self, monkeypatch):
        """50/50 bimodal series (bull first, bear last): HMM must end in bear state."""
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=0))
        spy_df = _bimodal_spy_df_ends_bear()
        with (
            self._patch_eligible(999),
            patch.object(_signals_mod, "_safe_download", return_value=spy_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()
        assert result["regime"] == "bear", f"Expected 'bear', got '{result['regime']}'"

    def test_disabled_config_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(enabled=False))
        result = get_hmm_regime_spy()
        assert result["regime"] == "unknown"
        assert result["source"] == "disabled"

    def test_insufficient_spy_data_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=0))
        tiny_df = pd.DataFrame({"Close": [100.0, 101.0, 102.0]})
        with (
            self._patch_eligible(999),
            patch.object(_signals_mod, "_safe_download", return_value=tiny_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()
        assert result["regime"] == "unknown"

    def test_fetch_error_returns_unknown_error(self, monkeypatch):
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=0))
        with (
            self._patch_eligible(999),
            patch.object(_signals_mod, "_safe_download", side_effect=RuntimeError("network")),
        ):
            result = get_hmm_regime_spy()
        assert result["regime"] == "unknown"
        assert result["source"] == "error"

    def test_cache_returned_within_ttl_same_day(self, monkeypatch):
        cached = {
            "regime": "bull", "confidence": 0.97, "mu_bull": 0.001,
            "mu_bear": -0.001, "source": "HMM_SPY_2state", "lookback_days": 100,
        }
        now = datetime.now(UTC)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", cached)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", now)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=0))
        with patch.object(_signals_mod, "_safe_download", side_effect=AssertionError("should not fetch")):
            result = get_hmm_regime_spy()
        assert result is cached

    def test_confidence_is_valid_probability(self, monkeypatch):
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=0))
        spy_df = _bimodal_spy_df_ends_bull()
        with (
            self._patch_eligible(999),
            patch.object(_signals_mod, "_safe_download", return_value=spy_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()
        assert isinstance(result["confidence"], float)
        assert 0.0 < result["confidence"] <= 1.0, f"confidence={result['confidence']} not in (0, 1]"


# ── 14–20: HMM phase gate ─────────────────────────────────────────────────────


class TestHmmGate:
    """Verify get_hmm_regime_spy() gates on count_eligible(), not count()."""

    def _cfg(self, gate: int = 200) -> dict:
        return {"enabled": True, "lookback_days": 200, "cache_ttl_seconds": 3600, "gate_min_eligible_trades": gate}

    def _patch_ts(self, eligible: int, total: int | None = None):
        """Patch training_store: count_eligible returns eligible, count returns total."""
        ts_mock = MagicMock()
        ts_mock.count_eligible.return_value = eligible
        ts_mock.count.return_value = total if total is not None else eligible
        return patch.dict("sys.modules", {"training_store": ts_mock})

    def test_below_threshold_returns_gate_not_met(self, monkeypatch):
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=200))
        with self._patch_ts(eligible=150, total=250):
            result = get_hmm_regime_spy()
        assert result["regime"] == "unknown"
        assert result["source"] == "gate_not_met"

    def test_gate_uses_count_eligible_not_count(self, monkeypatch):
        """
        250 raw records but only 150 eligible (100 degraded).
        Gate = 200. count() would pass; count_eligible() does not.
        Must return gate_not_met.
        """
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=200))
        with self._patch_ts(eligible=150, total=250):
            result = get_hmm_regime_spy()
        assert result["source"] == "gate_not_met", (
            "gate must use count_eligible(), not count() — "
            "250 raw records should not satisfy the 200-eligible gate when only 150 are eligible"
        )

    def test_degraded_records_excluded_from_gate_count(self, tmp_path, monkeypatch):
        """
        Write 5 records to a temp training store: 3 degraded (ml_eligible=False),
        2 eligible (ml_eligible=True). count_eligible() must return 2, not 5.
        Gate = 3. Must return gate_not_met because 2 < 3.
        """
        import training_store

        store_file = tmp_path / "training_records.jsonl"
        monkeypatch.setattr(training_store, "_STORE_FILE", store_file)

        records = [
            {"trade_id": "T1", "ml_eligible": True},
            {"trade_id": "T2", "ml_eligible": False},  # degraded
            {"trade_id": "T3", "ml_eligible": False},  # degraded
            {"trade_id": "T4", "ml_eligible": False},  # degraded
            {"trade_id": "T5", "ml_eligible": True},
        ]
        with open(store_file, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=3))

        assert training_store.count_eligible() == 2, "count_eligible must exclude 3 degraded records"

        result = get_hmm_regime_spy()
        assert result["source"] == "gate_not_met", (
            "2 eligible records should not satisfy gate=3; degraded records must be excluded"
        )

    def test_legacy_records_without_ml_eligible_count_toward_gate(self, tmp_path, monkeypatch):
        """
        Legacy records (no ml_eligible field) must count as eligible per
        count_eligible() backwards-compat contract. Gate = 2, legacy records = 3.
        Must pass gate and attempt to fetch SPY.
        """
        import training_store

        store_file = tmp_path / "training_records.jsonl"
        monkeypatch.setattr(training_store, "_STORE_FILE", store_file)

        # 3 legacy records + 1 degraded
        records = [
            {"trade_id": "L1"},                        # legacy — no ml_eligible
            {"trade_id": "L2"},                        # legacy
            {"trade_id": "L3"},                        # legacy
            {"trade_id": "D1", "ml_eligible": False},  # degraded — excluded
        ]
        with open(store_file, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        assert training_store.count_eligible() == 3, "3 legacy records must be counted as eligible"

        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=2))

        # Gate is met (3 >= 2). HMM will try to fetch SPY.
        # Return insufficient data so the test doesn't need a real SPY download.
        tiny_df = pd.DataFrame({"Close": [100.0, 101.0]})
        with (
            patch.object(_signals_mod, "_safe_download", return_value=tiny_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()

        assert result["source"] != "gate_not_met", (
            "Legacy records must count: gate=2, eligible=3 → gate should pass"
        )

    def test_exactly_at_threshold_passes_gate(self, monkeypatch):
        """eligible == gate threshold → gate passes, HMM runs."""
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=200))
        spy_df = _bimodal_spy_df_ends_bull()
        with (
            self._patch_ts(eligible=200, total=200),
            patch.object(_signals_mod, "_safe_download", return_value=spy_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()
        assert result["source"] != "gate_not_met", "eligible==threshold must pass the gate"

    def test_gate_not_met_response_includes_eligible_count(self, monkeypatch):
        """gate_not_met response must include 'eligible_trades' for observability."""
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=200))
        with self._patch_ts(eligible=42, total=99):
            result = get_hmm_regime_spy()
        assert result.get("eligible_trades") == 42, (
            f"gate_not_met must report eligible_trades=42, got {result.get('eligible_trades')}"
        )

    def test_gate_threshold_is_configurable(self, monkeypatch):
        """Gate respects gate_min_eligible_trades from config, not a hardcoded value."""
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache", None)
        monkeypatch.setattr(_signals_mod, "_hmm_spy_cache_ts", None)
        # Set gate to 50. Provide 60 eligible → must pass.
        monkeypatch.setitem(_config_mod.CONFIG, "hmm_regime", self._cfg(gate=50))
        spy_df = _bimodal_spy_df_ends_bull()
        with (
            self._patch_ts(eligible=60, total=60),
            patch.object(_signals_mod, "_safe_download", return_value=spy_df),
            patch.object(_signals_mod, "_flatten_columns", side_effect=lambda df: df),
        ):
            result = get_hmm_regime_spy()
        assert result["source"] != "gate_not_met", (
            "gate=50, eligible=60 → should pass with configurable threshold"
        )
