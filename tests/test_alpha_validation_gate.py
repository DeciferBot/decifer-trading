"""
Tests for the alpha validation gate — regression guard ensuring the 50-trade /
positive-expectancy checkpoint is enforced before new signal dimensions,
infrastructure work, or live trading are permitted.

Covers:
- _is_closed_trade()   — recognises both trade shapes (status field vs exit fields)
- _count_closed_trades() — counts correctly for production trade schema
- _compute_expectancy()  — calculates avg PnL per closed trade
- check_alpha_gate()     — non-raising gate check with full diagnostic detail
- assert_alpha_gate_passed() — raising version
- PhaseStatus.alpha_gate — populated by get_status()
- Production config has alpha_validation_gate section defined
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from phase_gate import (
    AlphaGateStatus,
    PhaseGateViolation,
    _compute_expectancy,
    _count_closed_trades,
    _is_closed_trade,
    assert_alpha_gate_passed,
    check_alpha_gate,
    get_status,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_config(min_trades: int = 50, require_positive: bool = True,
                 trade_log: str = "/nonexistent/trades.json") -> dict:
    return {
        "trade_log": trade_log,
        "phase_gate": {
            "current_phase": 1,
            "alpha_validation_gate": {
                "min_closed_trades": min_trades,
                "require_positive_expectancy": require_positive,
            },
            "phase1_exit_criteria": {
                "min_closed_trades": 200,
                "min_test_pass_rate": 0.80,
                "min_paper_trading_days": 30,
            },
            "frozen_features": {
                "live_account_trading": 4,
            },
        },
        "accounts": {"paper": "DUP123", "live_1": "", "live_2": ""},
        "aggregate_accounts": [],
    }


def _write_trades(tmp_path, trades: list) -> str:
    p = tmp_path / "trades.json"
    p.write_text(json.dumps(trades))
    return str(p)


def _closed_trade(pnl: float, use_status: bool = False) -> dict:
    """Return a closed trade record in production schema (exit fields) or legacy (status)."""
    if use_status:
        return {"pnl": pnl, "status": "closed"}
    return {
        "symbol": "AAPL",
        "action": "BUY",
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl / 10,
        "shares": 10,
        "pnl": pnl,
        "entry_time": "2026-03-01 09:30:00",
        "exit_time": "2026-03-01 10:00:00",
        "exit_reason": "take_profit",
    }


def _open_trade() -> dict:
    """Return an open trade (no exit_price, no exit_time, no status)."""
    return {
        "symbol": "TSLA",
        "action": "BUY",
        "entry_price": 200.0,
        "exit_price": None,
        "shares": 5,
        "pnl": None,
        "entry_time": "2026-03-01 09:30:00",
        "exit_time": None,
    }


# ── _is_closed_trade ──────────────────────────────────────────────────────────


class TestIsClosedTrade:

    def test_production_schema_with_exit_fields_is_closed(self):
        t = _closed_trade(100.0, use_status=False)
        assert _is_closed_trade(t) is True

    def test_legacy_status_closed_is_closed(self):
        assert _is_closed_trade({"status": "closed", "pnl": 50}) is True

    def test_legacy_status_exited_is_closed(self):
        assert _is_closed_trade({"status": "exited", "pnl": -20}) is True

    def test_legacy_status_filled_is_closed(self):
        assert _is_closed_trade({"status": "filled", "pnl": 0}) is True

    def test_open_trade_no_exit_fields_is_not_closed(self):
        assert _is_closed_trade(_open_trade()) is False

    def test_trade_with_exit_price_but_no_exit_time_is_not_closed(self):
        t = {"entry_price": 100.0, "exit_price": 105.0, "exit_time": None, "pnl": 50}
        assert _is_closed_trade(t) is False

    def test_trade_with_exit_time_but_zero_exit_price_is_not_closed(self):
        t = {"entry_price": 100.0, "exit_price": 0, "exit_time": "2026-03-01 10:00:00", "pnl": 0}
        assert _is_closed_trade(t) is False

    def test_unknown_status_with_exit_fields_is_closed(self):
        """A trade with an unrecognised status but exit fields present = closed."""
        t = {"status": "partial", "exit_price": 105.0, "exit_time": "2026-03-01 10:00:00"}
        # status='partial' is not in the recognised set, and it is truthy, so
        # the "no status" branch won't fire — result is False (not falsely promoted)
        assert _is_closed_trade(t) is False

    def test_empty_dict_is_not_closed(self):
        assert _is_closed_trade({}) is False


# ── _count_closed_trades ──────────────────────────────────────────────────────


class TestCountClosedTrades:

    def test_missing_file_returns_zero(self):
        assert _count_closed_trades("/nonexistent/trades.json") == 0

    def test_malformed_json_returns_zero(self, tmp_path):
        f = tmp_path / "trades.json"
        f.write_text("not {valid} json")
        assert _count_closed_trades(str(f)) == 0

    def test_counts_production_schema_closed_trades(self, tmp_path):
        trades = [_closed_trade(100), _closed_trade(-50), _open_trade()]
        path = _write_trades(tmp_path, trades)
        assert _count_closed_trades(path) == 2

    def test_counts_legacy_status_trades(self, tmp_path):
        trades = [
            {"status": "closed", "pnl": 100},
            {"status": "exited", "pnl": -20},
            {"status": "filled", "pnl": 5},
            {"status": "open", "pnl": None},
        ]
        path = _write_trades(tmp_path, trades)
        assert _count_closed_trades(path) == 3

    def test_counts_mixed_schema(self, tmp_path):
        trades = [
            _closed_trade(100, use_status=False),   # production
            _closed_trade(-30, use_status=True),     # legacy
            _open_trade(),                           # open — not counted
        ]
        path = _write_trades(tmp_path, trades)
        assert _count_closed_trades(path) == 2

    def test_empty_list_returns_zero(self, tmp_path):
        path = _write_trades(tmp_path, [])
        assert _count_closed_trades(path) == 0

    def test_non_list_json_returns_zero(self, tmp_path):
        f = tmp_path / "trades.json"
        f.write_text('{"key": "value"}')
        assert _count_closed_trades(str(f)) == 0


# ── _compute_expectancy ───────────────────────────────────────────────────────


class TestComputeExpectancy:

    def test_missing_file_returns_zero_none(self):
        n, e = _compute_expectancy("/nonexistent/trades.json")
        assert n == 0
        assert e is None

    def test_single_trade_returns_none_expectancy(self, tmp_path):
        path = _write_trades(tmp_path, [_closed_trade(100)])
        n, e = _compute_expectancy(path)
        assert n == 1
        assert e is None

    def test_two_trades_computes_average(self, tmp_path):
        path = _write_trades(tmp_path, [_closed_trade(100), _closed_trade(-40)])
        n, e = _compute_expectancy(path)
        assert n == 2
        assert e == pytest.approx(30.0)

    def test_positive_expectancy(self, tmp_path):
        trades = [_closed_trade(200), _closed_trade(100), _closed_trade(-50)]
        path = _write_trades(tmp_path, trades)
        n, e = _compute_expectancy(path)
        assert n == 3
        assert e is not None and e > 0

    def test_negative_expectancy(self, tmp_path):
        trades = [_closed_trade(-200), _closed_trade(-100), _closed_trade(50)]
        path = _write_trades(tmp_path, trades)
        n, e = _compute_expectancy(path)
        assert n == 3
        assert e is not None and e < 0

    def test_open_trades_excluded_from_expectancy(self, tmp_path):
        trades = [_closed_trade(100), _closed_trade(200), _open_trade()]
        path = _write_trades(tmp_path, trades)
        n, e = _compute_expectancy(path)
        assert n == 2   # open trade not counted
        assert e == pytest.approx(150.0)

    def test_trades_without_pnl_field_excluded(self, tmp_path):
        trades = [
            _closed_trade(100),
            {"exit_price": 110, "exit_time": "2026-03-01 10:00:00"},  # no pnl
            _closed_trade(200),
        ]
        path = _write_trades(tmp_path, trades)
        n, e = _compute_expectancy(path)
        assert n == 2
        assert e == pytest.approx(150.0)


# ── check_alpha_gate ──────────────────────────────────────────────────────────


class TestCheckAlphaGate:

    def test_returns_alpha_gate_status(self):
        cfg = _make_config()
        result = check_alpha_gate(cfg)
        assert isinstance(result, AlphaGateStatus)

    def test_gate_blocked_with_no_trades(self):
        cfg = _make_config(min_trades=50, trade_log="/nonexistent/trades.json")
        result = check_alpha_gate(cfg)
        assert result.gate_passed is False
        assert result.closed_trades == 0
        assert result.blocking_reason is not None
        assert "50" in result.blocking_reason

    def test_gate_blocked_with_insufficient_trades(self, tmp_path):
        trades = [_closed_trade(100) for _ in range(30)]
        path = _write_trades(tmp_path, trades)
        cfg = _make_config(min_trades=50, trade_log=path)
        result = check_alpha_gate(cfg)
        assert result.gate_passed is False
        assert result.closed_trades == 30
        assert "20 more required" in result.blocking_reason

    def test_gate_blocked_with_negative_expectancy(self, tmp_path):
        trades = [_closed_trade(-50) for _ in range(50)]
        path = _write_trades(tmp_path, trades)
        cfg = _make_config(min_trades=50, trade_log=path)
        result = check_alpha_gate(cfg)
        assert result.gate_passed is False
        assert result.closed_trades == 50
        assert result.positive_expectancy is False
        assert "Positive expectancy required" in result.blocking_reason

    def test_gate_passed_with_sufficient_positive_trades(self, tmp_path):
        trades = [_closed_trade(100) for _ in range(50)]
        path = _write_trades(tmp_path, trades)
        cfg = _make_config(min_trades=50, trade_log=path)
        result = check_alpha_gate(cfg)
        assert result.gate_passed is True
        assert result.closed_trades == 50
        assert result.positive_expectancy is True
        assert result.blocking_reason is None

    def test_gate_blocked_on_both_criteria(self, tmp_path):
        """Fewer than 50 trades AND negative expectancy — both reasons reported."""
        trades = [_closed_trade(-100) for _ in range(10)]
        path = _write_trades(tmp_path, trades)
        cfg = _make_config(min_trades=50, trade_log=path)
        result = check_alpha_gate(cfg)
        assert result.gate_passed is False
        assert "|" in result.blocking_reason  # two reasons joined

    def test_expectancy_not_required_when_flag_false(self, tmp_path):
        """If require_positive_expectancy=False, negative expectancy alone does not block."""
        trades = [_closed_trade(-100) for _ in range(50)]
        path = _write_trades(tmp_path, trades)
        cfg = _make_config(min_trades=50, require_positive=False, trade_log=path)
        result = check_alpha_gate(cfg)
        assert result.gate_passed is True

    def test_as_dict_has_expected_keys(self):
        cfg = _make_config()
        d = check_alpha_gate(cfg).as_dict()
        assert {"gate_passed", "closed_trades", "min_closed_trades",
                "expectancy", "positive_expectancy", "blocking_reason"}.issubset(d)

    def test_defaults_to_50_trades_when_config_missing(self, tmp_path):
        """Falls back to 50 if alpha_validation_gate section absent from config."""
        trades = [_closed_trade(100) for _ in range(40)]
        path = _write_trades(tmp_path, trades)
        cfg = {
            "trade_log": path,
            "phase_gate": {},  # no alpha_validation_gate key
        }
        result = check_alpha_gate(cfg)
        assert result.min_closed_trades == 50
        assert result.gate_passed is False


# ── assert_alpha_gate_passed ──────────────────────────────────────────────────


class TestAssertAlphaGatePassed:

    def test_raises_when_gate_blocked(self):
        cfg = _make_config(trade_log="/nonexistent/trades.json")
        with pytest.raises(PhaseGateViolation, match="ALPHA VALIDATION GATE BLOCKED"):
            assert_alpha_gate_passed(cfg)

    def test_violation_message_references_live_trading_gate_doc(self):
        cfg = _make_config(trade_log="/nonexistent/trades.json")
        with pytest.raises(PhaseGateViolation) as exc_info:
            assert_alpha_gate_passed(cfg)
        assert "LIVE_TRADING_GATE.md" in str(exc_info.value)

    def test_violation_message_states_no_new_dimensions(self):
        cfg = _make_config(trade_log="/nonexistent/trades.json")
        with pytest.raises(PhaseGateViolation) as exc_info:
            assert_alpha_gate_passed(cfg)
        msg = str(exc_info.value)
        assert "signal dimension" in msg.lower() or "new signal" in msg.lower()

    def test_does_not_raise_when_gate_passed(self, tmp_path):
        trades = [_closed_trade(100) for _ in range(50)]
        path = _write_trades(tmp_path, trades)
        cfg = _make_config(min_trades=50, trade_log=path)
        assert_alpha_gate_passed(cfg)  # no raise


# ── get_status includes alpha_gate ────────────────────────────────────────────


class TestGetStatusAlphaGate:

    def test_get_status_includes_alpha_gate(self):
        cfg = _make_config()
        status = get_status(cfg)
        assert status.alpha_gate is not None
        assert isinstance(status.alpha_gate, AlphaGateStatus)

    def test_as_dict_includes_alpha_gate(self):
        cfg = _make_config()
        d = get_status(cfg).as_dict()
        assert "alpha_gate" in d
        assert d["alpha_gate"] is not None

    def test_alpha_gate_blocked_in_phase1_with_no_trades(self):
        cfg = _make_config(trade_log="/nonexistent/trades.json")
        status = get_status(cfg)
        assert status.alpha_gate.gate_passed is False


# ── Production config regression tests ───────────────────────────────────────


class TestProductionConfigAlphaGate:

    def test_production_config_has_alpha_validation_gate(self):
        from config import CONFIG
        pg = CONFIG.get("phase_gate", {})
        assert "alpha_validation_gate" in pg, (
            "config.py phase_gate must contain alpha_validation_gate section"
        )

    def test_production_config_alpha_gate_min_trades_is_50(self):
        from config import CONFIG
        alpha = CONFIG["phase_gate"]["alpha_validation_gate"]
        assert alpha.get("min_closed_trades") == 50

    def test_production_config_alpha_gate_requires_positive_expectancy(self):
        from config import CONFIG
        alpha = CONFIG["phase_gate"]["alpha_validation_gate"]
        assert alpha.get("require_positive_expectancy") is True

    def test_production_config_alpha_gate_is_currently_blocked(self):
        """
        Production gate must be blocked right now — paper trading just started,
        no confirmed positive-expectancy result yet.  This test is intentionally
        expected to FAIL once the alpha gate is genuinely cleared, at which point
        it should be removed or inverted by Amit.
        """
        from config import CONFIG
        result = check_alpha_gate(CONFIG)
        assert result.gate_passed is False, (
            "Alpha gate is passing — if this is genuine, Amit must review and "
            "explicitly remove or invert this test before pulling Phase B work."
        )
