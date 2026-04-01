"""Tests for execution_agent.py — AI-powered order execution planner."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap + heavy-dep stubs (mirrors test_fill_watcher.py pattern)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

for _mod_name in ("ib_async", "anthropic"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_STATIC_FW_CONFIG = {
    "enabled":           True,
    "initial_wait_secs": 30,
    "max_attempts":      3,
    "interval_secs":     20,
    "step_pct":          0.002,
    "max_chase_pct":     0.01,
    "orphan_timeout_mins": 5,
}

_EA_CONFIG = {
    "enabled":           True,
    "max_tokens":        350,
    "fallback_on_error": True,
}

_PATCHED_CONFIG = {
    "anthropic_api_key": "test-key",
    "claude_model":      "claude-sonnet-4-6",
    "execution_agent":   _EA_CONFIG,
    "fill_watcher":      _STATIC_FW_CONFIG,
}

_VALID_PAYLOAD = {
    "order_type":            "LIMIT",
    "limit_price":           0,
    "aggression":            "normal",
    "split_into_n_tranches": 1,
    "timeout_secs":          90,
    "fallback_strategy":     "cancel",
    "fill_watcher_params": {
        "initial_wait_secs": 30,
        "interval_secs":     20,
        "max_attempts":      3,
        "step_pct":          0.002,
        "max_chase_pct":     0.01,
    },
    "reasoning": "Normal spread and volume; standard limit order appropriate.",
}

_COMMON_KWARGS = dict(
    symbol="AAPL", direction="LONG", size=100,
    conviction_score=28, bid=149.90, ask=150.10,
    spread_pct=0.13, rel_volume=1.2, vwap_dist_pct=0.1,
    time_of_day_str="10:30", regime_name="BULL_TRENDING",
)


def _make_claude_response(payload: dict) -> MagicMock:
    """Return a mock anthropic messages.create() response."""
    resp = MagicMock()
    resp.content = [MagicMock()]
    resp.content[0].text = json.dumps(payload)
    return resp


# ---------------------------------------------------------------------------
# Test 1 — Happy path: valid JSON → correct ExecutionPlan fields
# ---------------------------------------------------------------------------

class TestValidPlanReturned:
    def test_fields_match_payload(self):
        import execution_agent as ea

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response(_VALID_PAYLOAD)

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)

        assert plan.order_type == "LIMIT"
        assert plan.aggression == "normal"
        assert plan.split_into_n_tranches == 1
        assert plan.timeout_secs == 90
        assert plan.fallback_strategy == "cancel"
        assert plan.fill_watcher_params["max_attempts"] == 3
        assert plan.fill_watcher_params["step_pct"] == 0.002
        assert isinstance(plan.reasoning, str) and len(plan.reasoning) > 0

    def test_claude_api_called_once(self):
        import execution_agent as ea

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response(_VALID_PAYLOAD)

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            ea.get_execution_plan(**_COMMON_KWARGS)

        mock_client.messages.create.assert_called_once()

    def test_midpoint_order_type_accepted(self):
        import execution_agent as ea

        payload = {**_VALID_PAYLOAD, "order_type": "MIDPOINT"}
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response(payload)

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)

        assert plan.order_type == "MIDPOINT"


# ---------------------------------------------------------------------------
# Test 2 — Wide spread → MKT order recommended
# ---------------------------------------------------------------------------

class TestWideSpreadMarketOrder:
    def test_mkt_order_type_propagated(self):
        import execution_agent as ea

        payload = {**_VALID_PAYLOAD, "order_type": "MKT", "aggression": "aggressive"}
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response(payload)

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**{**_COMMON_KWARGS, "spread_pct": 0.8})

        assert plan.order_type == "MKT"
        assert plan.aggression == "aggressive"


# ---------------------------------------------------------------------------
# Test 3 — High conviction → aggressive plan with tighter initial wait
# ---------------------------------------------------------------------------

class TestHighConvictionAggressive:
    def test_aggressive_params_returned(self):
        import execution_agent as ea

        payload = {
            **_VALID_PAYLOAD,
            "aggression": "aggressive",
            "timeout_secs": 10 + 5 * 20,  # 110
            "fill_watcher_params": {
                **_VALID_PAYLOAD["fill_watcher_params"],
                "initial_wait_secs": 10,
                "max_attempts": 5,
            },
        }
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response(payload)

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**{**_COMMON_KWARGS, "conviction_score": 40})

        assert plan.aggression == "aggressive"
        assert plan.fill_watcher_params["initial_wait_secs"] < 30


# ---------------------------------------------------------------------------
# Test 4 — API failure → fallback plan uses static CONFIG values
# ---------------------------------------------------------------------------

class TestAPIFailureFallback:
    def test_exception_returns_fallback(self):
        import execution_agent as ea

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API timeout")

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)

        assert plan.order_type == "LIMIT"
        assert plan.fill_watcher_params["initial_wait_secs"] == 30
        assert plan.fill_watcher_params["max_attempts"] == 3
        assert "Fallback" in plan.reasoning

    def test_fallback_disabled_reraises(self):
        import execution_agent as ea

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API timeout")
        cfg_no_fallback = {
            **_PATCHED_CONFIG,
            "execution_agent": {**_EA_CONFIG, "fallback_on_error": False},
        }

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", cfg_no_fallback):
            with pytest.raises(RuntimeError):
                ea.get_execution_plan(**_COMMON_KWARGS)


# ---------------------------------------------------------------------------
# Test 5 — Invalid JSON from Claude → fallback
# ---------------------------------------------------------------------------

class TestInvalidJSONFallback:
    def test_prose_response_falls_back(self):
        import execution_agent as ea

        bad_resp = MagicMock()
        bad_resp.content = [MagicMock()]
        bad_resp.content[0].text = "Sure! You should buy aggressively right now."
        mock_client = MagicMock()
        mock_client.messages.create.return_value = bad_resp

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)

        assert plan.order_type == "LIMIT"
        assert "Fallback" in plan.reasoning

    def test_invalid_order_type_falls_back(self):
        import execution_agent as ea

        payload = {**_VALID_PAYLOAD, "order_type": "FOO"}
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response(payload)

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)

        assert plan.order_type == "LIMIT"
        assert "Fallback" in plan.reasoning

    def test_missing_fw_key_falls_back(self):
        import execution_agent as ea

        bad_payload = {
            **_VALID_PAYLOAD,
            "fill_watcher_params": {"initial_wait_secs": 20},  # missing 4 keys
        }
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response(bad_payload)

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", _PATCHED_CONFIG):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)

        assert "Fallback" in plan.reasoning


# ---------------------------------------------------------------------------
# Test 6 — FillWatcher uses injected watcher_params, not CONFIG
# ---------------------------------------------------------------------------

class TestPlanUsedInFillWatcher:
    def test_watcher_uses_injected_params_not_config(self):
        import fill_watcher as fw

        custom_params = {
            "initial_wait_secs": 10,
            "interval_secs":     5,
            "max_attempts":      2,
            "step_pct":          0.003,
            "max_chase_pct":     0.015,
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

        # run() should use the custom 10s wait and 2 attempts, not the 30s/3 from CONFIG
        with patch("fill_watcher._interruptible_sleep") as mock_sleep, \
             patch.object(watcher, "_is_filled", return_value=True), \
             patch.object(watcher, "_log_audit"), \
             patch.object(watcher, "_remove_from_registry"), \
             patch("fill_watcher.CONFIG", {"fill_watcher": _STATIC_FW_CONFIG}):
            watcher.run()

        # First sleep call should be for initial_wait_secs=10, not 30
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
            # watcher_params omitted → should default to None → uses CONFIG
        )
        assert watcher._watcher_params is None

        with patch("fill_watcher._interruptible_sleep") as mock_sleep, \
             patch.object(watcher, "_is_filled", return_value=True), \
             patch.object(watcher, "_log_audit"), \
             patch.object(watcher, "_remove_from_registry"), \
             patch("fill_watcher.CONFIG", {"fill_watcher": _STATIC_FW_CONFIG}):
            watcher.run()

        # First sleep should be 30s (from static CONFIG)
        first_sleep_duration = mock_sleep.call_args_list[0][0][0]
        assert first_sleep_duration == 30.0


# ---------------------------------------------------------------------------
# Test 7 — Disabled execution agent → always returns fallback
# ---------------------------------------------------------------------------

class TestDisabledExecutionAgent:
    def test_disabled_skips_claude_call(self):
        import execution_agent as ea

        cfg_disabled = {
            **_PATCHED_CONFIG,
            "execution_agent": {**_EA_CONFIG, "enabled": False},
        }
        mock_client = MagicMock()

        with patch.object(ea, "_get_client", return_value=mock_client), \
             patch("execution_agent.CONFIG", cfg_disabled):
            plan = ea.get_execution_plan(**_COMMON_KWARGS)

        mock_client.messages.create.assert_not_called()
        assert "Fallback" in plan.reasoning
