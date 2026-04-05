"""Tests for execution_agent.py — deterministic order execution planner."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap + heavy-dep stubs
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

for _mod_name in ("ib_async", "anthropic"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_STATIC_FW_CONFIG = {
    "enabled":             True,
    "initial_wait_secs":   30,
    "max_attempts":        3,
    "interval_secs":       20,
    "step_pct":            0.002,
    "max_chase_pct":       0.01,
    "orphan_timeout_mins": 5,
}

_EA_CONFIG = {
    "enabled":           True,
    "fallback_on_error": True,
}

_PATCHED_CONFIG = {
    "execution_agent": _EA_CONFIG,
    "fill_watcher":    _STATIC_FW_CONFIG,
    "ic_calculator":   {},
}

_COMMON_KWARGS = dict(
    symbol="AAPL", direction="LONG", size=100,
    conviction_score=28, bid=149.90, ask=150.10,
    spread_pct=0.13, rel_volume=1.2, vwap_dist_pct=0.1,
    time_of_day_str="10:30", regime_name="BULL_TRENDING",
)


# ---------------------------------------------------------------------------
# Test 1 — Happy path: tight spread + low volume → patient, LIMIT order
# ---------------------------------------------------------------------------

class TestPatientPlan:
    def test_tight_spread_patient(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(
                symbol="AAPL", direction="LONG", size=100, conviction_score=18,
                bid=149.95, ask=150.00, spread_pct=0.03,
                rel_volume=0.4, vwap_dist_pct=0.1,
                time_of_day_str="12:00", regime_name="BULL_TRENDING",
            )
        assert plan.aggression == "patient"
        assert plan.order_type == "LIMIT"
        assert plan.fill_watcher_params["initial_wait_secs"] == 45
        assert plan.fill_watcher_params["max_attempts"] == 2

    def test_panic_regime_forces_patient(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(
                **{**_COMMON_KWARGS,
                   "spread_pct": 0.6, "rel_volume": 2.5,
                   "regime_name": "PANIC", "conviction_score": 40},
            )
        # PANIC regime votes patient → most conservative wins
        assert plan.aggression == "patient"

    def test_open_hour_forces_patient(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(
                **{**_COMMON_KWARGS, "time_of_day_str": "09:35"},
            )
        assert plan.aggression == "patient"


# ---------------------------------------------------------------------------
# Test 2 — Aggressive plan: wide spread + high volume + high score + close
# ---------------------------------------------------------------------------

class TestAggressivePlan:
    def test_aggressive_conditions(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(
                symbol="TSLA", direction="LONG", size=50, conviction_score=40,
                bid=200.00, ask=201.20, spread_pct=0.6,
                rel_volume=2.5, vwap_dist_pct=0.1,
                time_of_day_str="14:30", regime_name="BULL_TRENDING",
            )
        assert plan.aggression == "aggressive"
        assert plan.fill_watcher_params["initial_wait_secs"] == 15
        assert plan.fill_watcher_params["max_attempts"] == 4


# ---------------------------------------------------------------------------
# Test 3 — MKT order gating: requires BOTH wide spread AND high volume
# ---------------------------------------------------------------------------

class TestMarketOrderGate:
    def test_wide_spread_and_high_volume_gives_mkt(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(
                symbol="NVDA", direction="LONG", size=30, conviction_score=40,
                bid=500.00, ask=503.00, spread_pct=0.6,
                rel_volume=2.5, vwap_dist_pct=0.2,
                time_of_day_str="14:30", regime_name="BULL_TRENDING",
            )
        assert plan.order_type == "MKT"

    def test_wide_spread_low_volume_stays_limit(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(
                symbol="XYZ", direction="LONG", size=30, conviction_score=38,
                bid=50.00, ask=50.35, spread_pct=0.7,
                rel_volume=0.8, vwap_dist_pct=0.2,
                time_of_day_str="14:00", regime_name="BULL_TRENDING",
            )
        assert plan.order_type == "LIMIT"

    def test_high_volume_tight_spread_stays_limit(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(
                symbol="AMZN", direction="LONG", size=10, conviction_score=38,
                bid=180.00, ask=180.05, spread_pct=0.03,
                rel_volume=3.0, vwap_dist_pct=0.1,
                time_of_day_str="14:00", regime_name="BULL_TRENDING",
            )
        assert plan.order_type == "LIMIT"


# ---------------------------------------------------------------------------
# Test 4 — Fallback: disabled agent always returns static config fallback
# ---------------------------------------------------------------------------

class TestDisabledExecutionAgent:
    def test_disabled_returns_fallback(self):
        import execution_agent as ea
        cfg_disabled = {
            **_PATCHED_CONFIG,
            "execution_agent": {**_EA_CONFIG, "enabled": False},
        }
        with patch("execution_agent.CONFIG", cfg_disabled):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)
        assert "Fallback" in plan.reasoning
        assert plan.order_type == "LIMIT"
        assert plan.aggression == "normal"


# ---------------------------------------------------------------------------
# Test 5 — Fallback on exception: bad CONFIG raises gracefully
# ---------------------------------------------------------------------------

class TestFallbackOnError:
    def test_exception_in_aggression_returns_fallback(self):
        import execution_agent as ea
        # Corrupt IC config to cause an exception inside _determine_aggression
        with patch("execution_agent._determine_aggression", side_effect=RuntimeError("boom")), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)
        assert "Fallback" in plan.reasoning

    def test_fallback_disabled_reraises(self):
        import execution_agent as ea
        cfg_no_fallback = {
            **_PATCHED_CONFIG,
            "execution_agent": {**_EA_CONFIG, "fallback_on_error": False},
        }
        with patch("execution_agent._determine_aggression", side_effect=RuntimeError("boom")), \
             patch("execution_agent.CONFIG", cfg_no_fallback):
            with pytest.raises(RuntimeError):
                ea.get_execution_plan(**_COMMON_KWARGS)


# ---------------------------------------------------------------------------
# Test 6 — Plan structure: all required fields present and typed correctly
# ---------------------------------------------------------------------------

class TestPlanStructure:
    def test_all_fields_present(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)
        assert isinstance(plan.order_type, str)
        assert isinstance(plan.limit_price, (int, float))
        assert plan.aggression in ("patient", "normal", "aggressive")
        assert isinstance(plan.split_into_n_tranches, int)
        assert isinstance(plan.timeout_secs, int)
        assert plan.fallback_strategy in ("cancel", "market", "retry")
        fw = plan.fill_watcher_params
        assert all(k in fw for k in (
            "initial_wait_secs", "interval_secs",
            "max_attempts", "step_pct", "max_chase_pct"
        ))
        assert isinstance(plan.reasoning, str) and len(plan.reasoning) > 0

    def test_timeout_derived_from_params(self):
        import execution_agent as ea
        with patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)
        fw = plan.fill_watcher_params
        expected = int(fw["initial_wait_secs"] + fw["max_attempts"] * fw["interval_secs"])
        assert plan.timeout_secs == expected


# ---------------------------------------------------------------------------
# Test 7 — FillWatcher uses injected watcher_params, not CONFIG
# ---------------------------------------------------------------------------

class TestPlanUsedInFillWatcher:
    def test_watcher_uses_injected_params_not_config(self):
        import fill_watcher as fw

        custom_params = {
            "initial_wait_secs": 10,
            "interval_secs":      5,
            "max_attempts":       2,
            "step_pct":           0.003,
            "max_chase_pct":      0.015,
        }
        ib = MagicMock()
        ib.isConnected.return_value = True

        entry_trade = MagicMock()
        entry_trade.order.orderId = 99

        watcher = fw.FillWatcher(
            ib=ib, symbol="TSLA", order_id=99,
            entry_trade=entry_trade, original_limit=200.0,
            contract=MagicMock(), qty=50,
            watcher_params=custom_params,
        )
        assert watcher._watcher_params is custom_params

        with patch("fill_watcher._interruptible_sleep") as mock_sleep, \
             patch.object(watcher, "_is_filled", return_value=True), \
             patch.object(watcher, "_log_audit"), \
             patch.object(watcher, "_remove_from_registry"), \
             patch("fill_watcher.CONFIG", {"fill_watcher": _STATIC_FW_CONFIG}):
            watcher.run()

        first_sleep_duration = mock_sleep.call_args_list[0][0][0]
        assert first_sleep_duration == 10.0

    def test_watcher_without_params_uses_config(self):
        import fill_watcher as fw

        ib = MagicMock()
        ib.isConnected.return_value = True

        entry_trade = MagicMock()
        entry_trade.order.orderId = 100

        watcher = fw.FillWatcher(
            ib=ib, symbol="NVDA", order_id=100,
            entry_trade=entry_trade, original_limit=500.0,
            contract=MagicMock(), qty=20,
        )
        assert watcher._watcher_params is None

        with patch("fill_watcher._interruptible_sleep") as mock_sleep, \
             patch.object(watcher, "_is_filled", return_value=True), \
             patch.object(watcher, "_log_audit"), \
             patch.object(watcher, "_remove_from_registry"), \
             patch("fill_watcher.CONFIG", {"fill_watcher": _STATIC_FW_CONFIG}):
            watcher.run()

        first_sleep_duration = mock_sleep.call_args_list[0][0][0]
        assert first_sleep_duration == 30.0
