"""Tests for the Drawdown Brake fix.

Covers:
- update_equity_high_water_mark() returns True only on first breach
- Idempotency: already-halted returns False (no double-flatten)
- Normal equity updates return False
- Recovery from halt clears _drawdown_halt
- check_risk_conditions() blocks new trades when drawdown halted
- init_equity_high_water_mark_from_history() sets HWM to max of history
- Empty equity history is a no-op
- Existing higher HWM is not downgraded
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(mod, MagicMock())

import config as _config_mod

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
    "max_drawdown_alert": 0.25,
    "daily_loss_limit": 0.10,
    "min_cash_reserve": 0.05,
    "consecutive_loss_pause": 5,
    "pdt": {"enabled": False},
}
if hasattr(_config_mod, "CONFIG"):
    for k, v in _cfg.items():
        _config_mod.CONFIG.setdefault(k, v)
    _config_mod.CONFIG["max_drawdown_alert"] = 0.25
else:
    _config_mod.CONFIG = _cfg

sys.modules.pop("risk", None)
import pytest

import risk


def _reset():
    risk._equity_high_water_mark = None
    risk._drawdown_halt = False
    risk._last_known_equity = None


# ---------------------------------------------------------------------------
# Test 1: first breach returns True
# ---------------------------------------------------------------------------


class TestNewlyHaltedReturnsTrue:
    def setup_method(self):
        _reset()

    def test_returns_true_on_first_breach(self):
        """Exactly at the 25% drawdown limit must return True."""
        risk.update_equity_high_water_mark(100_000.0)
        result = risk.update_equity_high_water_mark(75_000.0)  # exactly 25%
        assert result is True
        assert risk._drawdown_halt is True

    def test_returns_false_on_hwm_init(self):
        """First call (HWM initialisation) must always return False."""
        result = risk.update_equity_high_water_mark(100_000.0)
        assert result is False


# ---------------------------------------------------------------------------
# Test 2: subsequent calls when already halted return False (idempotent)
# ---------------------------------------------------------------------------


class TestAlreadyHaltedReturnsFalse:
    def setup_method(self):
        _reset()

    def test_second_call_at_same_equity_returns_false(self):
        risk.update_equity_high_water_mark(100_000.0)
        risk.update_equity_high_water_mark(74_000.0)  # first breach → True
        result = risk.update_equity_high_water_mark(74_000.0)  # already halted
        assert result is False

    def test_deeper_loss_while_halted_returns_false(self):
        risk.update_equity_high_water_mark(100_000.0)
        risk.update_equity_high_water_mark(74_000.0)
        result = risk.update_equity_high_water_mark(60_000.0)
        assert result is False


# ---------------------------------------------------------------------------
# Test 3: normal equity updates return False
# ---------------------------------------------------------------------------


class TestNoBreachReturnsFalse:
    def setup_method(self):
        _reset()

    def test_small_drawdown_returns_false(self):
        """10% drawdown — well below 25% limit — must return False."""
        risk.update_equity_high_water_mark(100_000.0)
        result = risk.update_equity_high_water_mark(90_000.0)
        assert result is False
        assert risk._drawdown_halt is False

    def test_new_high_returns_false_and_updates_hwm(self):
        """New all-time high: returns False, updates HWM."""
        risk.update_equity_high_water_mark(100_000.0)
        result = risk.update_equity_high_water_mark(105_000.0)
        assert result is False
        assert risk._equity_high_water_mark == 105_000.0

    def test_equity_at_hwm_returns_false(self):
        """Equity exactly equal to HWM (zero drawdown) returns False."""
        risk.update_equity_high_water_mark(100_000.0)
        result = risk.update_equity_high_water_mark(100_000.0)
        assert result is False


# ---------------------------------------------------------------------------
# Test 4: equity exceeding HWM clears _drawdown_halt
# ---------------------------------------------------------------------------


class TestHaltClearsOnRecovery:
    def setup_method(self):
        _reset()

    def test_halt_clears_on_new_high(self):
        risk.update_equity_high_water_mark(100_000.0)
        risk.update_equity_high_water_mark(74_000.0)  # breach
        assert risk._drawdown_halt is True

        result = risk.update_equity_high_water_mark(101_000.0)  # new all-time high
        assert result is False
        assert risk._drawdown_halt is False
        assert risk._equity_high_water_mark == 101_000.0


# ---------------------------------------------------------------------------
# Test 5: check_risk_conditions() blocks new trades when drawdown halted
# ---------------------------------------------------------------------------


class TestCheckRiskConditionsBlockedWhenDrawdownHalted:
    def setup_method(self):
        _reset()

    def test_blocks_new_trades(self):
        risk._drawdown_halt = True
        risk._equity_high_water_mark = 100_000.0
        risk._last_known_equity = 74_000.0

        ok, reason = risk.check_risk_conditions(
            portfolio_value=74_000.0,
            daily_pnl=0.0,
            regime={"regime": "NEUTRAL", "position_size_multiplier": 1.0},
            open_positions=[],
            ib=None,
        )
        assert ok is False
        assert "drawdown" in reason.lower() or "circuit" in reason.lower()


# ---------------------------------------------------------------------------
# Test 6–9: init_equity_high_water_mark_from_history()
# ---------------------------------------------------------------------------


class TestInitHWMFromHistory:
    def setup_method(self):
        _reset()

    def test_sets_max_value(self):
        history = [
            {"date": "2026-03-25 09:30", "value": 100_000.0},
            {"date": "2026-03-26 09:30", "value": 115_000.0},
            {"date": "2026-03-27 09:30", "value": 108_000.0},
        ]
        risk.init_equity_high_water_mark_from_history(history)
        assert risk._equity_high_water_mark == 115_000.0

    def test_empty_list_is_noop_when_hwm_is_none(self, tmp_path, monkeypatch):
        # No state file AND empty list → HWM stays None
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "nonexistent.json"))
        risk.init_equity_high_water_mark_from_history([])
        assert risk._equity_high_water_mark is None

    def test_empty_list_does_not_overwrite_existing_hwm(self):
        risk._equity_high_water_mark = 200_000.0
        risk.init_equity_high_water_mark_from_history([])
        assert risk._equity_high_water_mark == 200_000.0

    def test_does_not_downgrade_higher_in_memory_hwm(self):
        risk._equity_high_water_mark = 200_000.0
        history = [{"date": "2026-03-25 09:30", "value": 150_000.0}]
        risk.init_equity_high_water_mark_from_history(history)
        assert risk._equity_high_water_mark == 200_000.0

    def test_upgrades_lower_in_memory_hwm(self):
        risk._equity_high_water_mark = 100_000.0
        history = [{"date": "2026-03-25 09:30", "value": 150_000.0}]
        risk.init_equity_high_water_mark_from_history(history)
        assert risk._equity_high_water_mark == 150_000.0


# ---------------------------------------------------------------------------
# (NEW) HWM state file persistence tests
# ---------------------------------------------------------------------------


class TestHWMStatePersistence:
    """
    Validates load_hwm_state() / save_hwm_state() and the updated
    init_equity_high_water_mark_from_history() that checks the state file
    before the (truncatable) equity history list.
    """

    def setup_method(self):
        _reset()

    def test_save_hwm_state_creates_valid_json(self, tmp_path, monkeypatch):
        """save_hwm_state() must create a readable JSON file with 'hwm' and 'updated' keys."""
        import json as _json

        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "hwm_state.json"))
        risk.save_hwm_state(123_456.78)

        state_file = tmp_path / "hwm_state.json"
        assert state_file.exists(), "hwm_state.json was not created"
        data = _json.loads(state_file.read_text())
        assert "hwm" in data
        assert abs(data["hwm"] - 123_456.78) < 0.01
        assert "updated" in data

    def test_load_hwm_state_returns_saved_value(self, tmp_path, monkeypatch):
        """load_hwm_state() must return exactly the value written by save_hwm_state()."""
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "hwm_state.json"))
        risk.save_hwm_state(200_000.0)
        loaded = risk.load_hwm_state()
        assert loaded == pytest.approx(200_000.0)

    def test_load_hwm_state_missing_file_returns_none(self, tmp_path, monkeypatch):
        """load_hwm_state() with no file must return None (no crash)."""
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "nonexistent.json"))
        assert risk.load_hwm_state() is None

    def test_load_hwm_state_corrupt_file_returns_none(self, tmp_path, monkeypatch):
        """load_hwm_state() with corrupt JSON must return None, not raise."""
        state_file = tmp_path / "hwm_state.json"
        state_file.write_text("{ not valid json }")
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(state_file))
        assert risk.load_hwm_state() is None

    def test_truncated_history_without_state_file_misses_peak(self, tmp_path, monkeypatch):
        """
        Documents the original bug: 2000 truncated entries that don't include
        the all-time peak produce a lower HWM when no state file is present.
        """
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "nonexistent.json"))
        all_time_peak = 200_000.0
        truncated_history = [{"date": f"2026-01-{i:04d}", "value": 100_000.0 + i} for i in range(2000)]
        risk.init_equity_high_water_mark_from_history(truncated_history)
        assert risk._equity_high_water_mark < all_time_peak, (
            "Without state file, truncated history should produce a lower HWM"
        )

    def test_state_file_restores_peak_lost_by_truncation(self, tmp_path, monkeypatch):
        """
        With state file holding the all-time peak (200k), even a truncated
        history (max 101,999) seeds HWM at 200k after restart.
        """
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "hwm_state.json"))
        risk.save_hwm_state(200_000.0)

        truncated_history = [{"date": f"2026-01-{i:04d}", "value": 100_000.0 + i} for i in range(2000)]
        risk.init_equity_high_water_mark_from_history(truncated_history)
        assert risk._equity_high_water_mark == pytest.approx(200_000.0), (
            f"State file must restore all-time peak, got {risk._equity_high_water_mark:,.2f}"
        )

    def test_state_file_takes_priority_over_lower_history_peak(self, tmp_path, monkeypatch):
        """State file 150k + history max 120k → HWM = 150k."""
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "hwm_state.json"))
        risk.save_hwm_state(150_000.0)
        history = [{"date": "2026-01-01", "value": 120_000.0}]
        risk.init_equity_high_water_mark_from_history(history)
        assert risk._equity_high_water_mark == pytest.approx(150_000.0)

    def test_update_hwm_saves_to_state_file_on_new_peak(self, tmp_path, monkeypatch):
        """
        update_equity_high_water_mark() must call save_hwm_state() every time
        a new all-time high is set so the state file stays current.
        """
        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "hwm_state.json"))
        risk.update_equity_high_water_mark(100_000.0)  # init
        risk.update_equity_high_water_mark(110_000.0)  # new high

        loaded = risk.load_hwm_state()
        assert loaded == pytest.approx(110_000.0), "State file must reflect the new all-time high after update"
