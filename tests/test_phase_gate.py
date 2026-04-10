"""
Tests for phase_gate.py — regression guard ensuring the phase gate correctly
blocks Phase 4+ features when Phase 1 exit criteria have not been met.

These tests use isolated config dicts so they never touch config.py on disk
and are fully offline (no IBKR, no Claude API, no yfinance).
"""

from __future__ import annotations

import json
import os
import sys
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from phase_gate import (
    PhaseGateViolation,
    assert_feature_allowed,
    get_status,
    validate,
    validate_or_raise,
    PHASE_DESCRIPTIONS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _base_config(current_phase: int = 1, overrides: dict | None = None) -> dict:
    """Minimal config dict mirroring the structure of config.py CONFIG."""
    cfg = {
        "accounts": {
            "paper": "DUP481326",
            "live_1": "",
            "live_2": "",
        },
        "aggregate_accounts": [],
        "trade_log": "data/trades.json",
        "phase_gate": {
            "current_phase": current_phase,
            "phase1_exit_criteria": {
                "min_closed_trades":      200,
                "min_test_pass_rate":     0.80,
                "min_paper_trading_days": 30,
            },
            "frozen_features": {
                "live_account_trading":      4,
                "multi_account_aggregation": 4,
                "cloud_deployment":          4,
                "ic_walkforward_validation": 4,
                "docker_deployment":         5,
                "multi_user_auth":           5,
                "hosted_dashboard":          5,
            },
        },
    }
    if overrides:
        cfg.update(overrides)
    return cfg


# ── Phase descriptions ────────────────────────────────────────────────────────


def test_phase_descriptions_cover_phases_1_to_5():
    for phase in range(1, 6):
        assert phase in PHASE_DESCRIPTIONS
        assert PHASE_DESCRIPTIONS[phase]


# ── assert_feature_allowed ────────────────────────────────────────────────────


class TestAssertFeatureAllowed:

    def test_unknown_feature_always_allowed(self):
        """Features not in the frozen list must never raise."""
        cfg = _base_config(current_phase=1)
        assert_feature_allowed("some_future_feature", config=cfg)  # no raise

    def test_phase4_feature_blocked_in_phase1(self):
        cfg = _base_config(current_phase=1)
        with pytest.raises(PhaseGateViolation, match="live_account_trading"):
            assert_feature_allowed("live_account_trading", config=cfg)

    def test_phase4_feature_blocked_in_phase3(self):
        cfg = _base_config(current_phase=3)
        with pytest.raises(PhaseGateViolation, match="cloud_deployment"):
            assert_feature_allowed("cloud_deployment", config=cfg)

    def test_phase4_feature_allowed_in_phase4(self):
        cfg = _base_config(current_phase=4)
        assert_feature_allowed("live_account_trading", config=cfg)  # no raise

    def test_phase5_feature_blocked_in_phase4(self):
        cfg = _base_config(current_phase=4)
        with pytest.raises(PhaseGateViolation, match="docker_deployment"):
            assert_feature_allowed("docker_deployment", config=cfg)

    def test_phase5_feature_allowed_in_phase5(self):
        cfg = _base_config(current_phase=5)
        assert_feature_allowed("docker_deployment", config=cfg)  # no raise
        assert_feature_allowed("multi_user_auth", config=cfg)     # no raise

    def test_violation_message_contains_required_phase(self):
        cfg = _base_config(current_phase=1)
        with pytest.raises(PhaseGateViolation) as exc_info:
            assert_feature_allowed("live_account_trading", config=cfg)
        msg = str(exc_info.value)
        assert "Phase 4" in msg
        assert "Phase 1" in msg

    def test_violation_message_contains_exit_criteria_hint(self):
        cfg = _base_config(current_phase=1)
        with pytest.raises(PhaseGateViolation) as exc_info:
            assert_feature_allowed("live_account_trading", config=cfg)
        msg = str(exc_info.value)
        assert "200+" in msg or "exit criteria" in msg.lower()


# ── validate ─────────────────────────────────────────────────────────────────


class TestValidate:

    def test_clean_phase1_config_returns_no_violations(self):
        cfg = _base_config(current_phase=1)
        assert validate(cfg) == []

    def test_live_accounts_populated_in_phase1_triggers_violation(self, monkeypatch):
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        cfg["accounts"]["live_1"] = "U3059777"
        violations = validate(cfg)
        live_v = [v for v in violations if "live_account_trading" in v]
        assert len(live_v) == 1

    def test_both_live_accounts_populated_triggers_single_violation(self, monkeypatch):
        """Both live_1 and live_2 set should produce one live_account_trading violation."""
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        cfg["accounts"]["live_1"] = "U3059777"
        cfg["accounts"]["live_2"] = "U24093086"
        violations = validate(cfg)
        live_v = [v for v in violations if "live_account_trading" in v]
        assert len(live_v) == 1

    def test_aggregate_accounts_non_empty_in_phase1_triggers_violation(self):
        cfg = _base_config(current_phase=1)
        cfg["aggregate_accounts"] = ["DUP481326", "U3059777"]
        violations = validate(cfg)
        assert any("multi_account_aggregation" in v for v in violations)

    def test_live_accounts_and_aggregate_in_phase1_triggers_two_violations(self, monkeypatch):
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        cfg["accounts"]["live_1"] = "U3059777"
        cfg["aggregate_accounts"] = ["DUP481326", "U3059777"]
        violations = validate(cfg)
        live_v  = [v for v in violations if "live_account_trading"      in v]
        agg_v   = [v for v in violations if "multi_account_aggregation" in v]
        assert len(live_v) == 1
        assert len(agg_v)  == 1

    def test_phase4_config_with_live_accounts_is_clean(self, monkeypatch):
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result",
                            lambda data_dir=None: {"ready_for_live": True, "failures": []})
        cfg = _base_config(current_phase=4)
        cfg["accounts"]["live_1"] = "U3059777"
        cfg["aggregate_accounts"] = ["DUP481326", "U3059777"]
        assert validate(cfg) == []

    def test_violation_messages_are_human_readable(self):
        cfg = _base_config(current_phase=1)
        cfg["accounts"]["live_1"] = "U3059777"
        violations = validate(cfg)
        assert violations
        msg = violations[0]
        assert "FROZEN" in msg
        assert "Phase" in msg

    def test_paper_only_account_does_not_trigger_violation(self):
        """Paper account ID populated but live_1/live_2 empty → no violation."""
        cfg = _base_config(current_phase=1)
        cfg["accounts"]["paper"] = "DUP481326"
        assert validate(cfg) == []


# ── validate_or_raise ─────────────────────────────────────────────────────────


class TestValidateOrRaise:

    def test_clean_config_does_not_raise(self):
        cfg = _base_config(current_phase=1)
        validate_or_raise(cfg)  # no raise

    def test_live_account_in_phase1_raises(self):
        cfg = _base_config(current_phase=1)
        cfg["accounts"]["live_1"] = "U3059777"
        with pytest.raises(PhaseGateViolation):
            validate_or_raise(cfg)

    def test_first_violation_message_included_in_raise(self):
        cfg = _base_config(current_phase=1)
        cfg["accounts"]["live_1"] = "U3059777"
        with pytest.raises(PhaseGateViolation, match="live_account_trading"):
            validate_or_raise(cfg)


# ── get_status ────────────────────────────────────────────────────────────────


class TestGetStatus:

    def test_returns_phase_status_object(self):
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert status.current_phase == 1
        assert status.phase_description

    def test_phase1_with_no_trades_not_complete(self):
        cfg = _base_config(current_phase=1, overrides={"trade_log": "/nonexistent/trades.json"})
        status = get_status(cfg)
        assert status.closed_trades == 0
        assert not status.criteria_met["min_closed_trades"]
        assert not status.phase1_complete

    def test_phase2_advances_paper_trading_days_criterion(self):
        """Once current_phase > 1, min_paper_trading_days is considered met."""
        cfg = _base_config(current_phase=2, overrides={"trade_log": "/nonexistent/trades.json"})
        status = get_status(cfg)
        assert status.criteria_met["min_paper_trading_days"]

    def test_as_dict_contains_all_expected_keys(self):
        cfg = _base_config(current_phase=1)
        d = get_status(cfg).as_dict()
        required_keys = {
            "current_phase", "phase_description", "closed_trades",
            "min_closed_trades", "test_pass_rate", "min_test_pass_rate",
            "min_paper_trading_days", "frozen_features", "criteria_met",
            "phase1_complete",
        }
        assert required_keys.issubset(d.keys())

    def test_frozen_features_dict_present_in_status(self):
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert "live_account_trading" in status.frozen_features
        assert status.frozen_features["live_account_trading"] == 4

    def test_trades_counted_from_file(self, tmp_path):
        trades_file = tmp_path / "trades.json"
        trades = [
            {"id": 1, "status": "closed"},
            {"id": 2, "status": "exited"},
            {"id": 3, "status": "open"},    # not counted
            {"id": 4, "status": "filled"},
        ]
        trades_file.write_text(json.dumps(trades))
        cfg = _base_config(current_phase=1, overrides={"trade_log": str(trades_file)})
        status = get_status(cfg)
        assert status.closed_trades == 3   # closed + exited + filled

    def test_missing_trade_log_counts_zero(self):
        cfg = _base_config(current_phase=1, overrides={"trade_log": "/nonexistent/path.json"})
        status = get_status(cfg)
        assert status.closed_trades == 0

    def test_malformed_trade_log_counts_zero(self, tmp_path):
        bad_file = tmp_path / "trades.json"
        bad_file.write_text("not valid json{{{")
        cfg = _base_config(current_phase=1, overrides={"trade_log": str(bad_file)})
        status = get_status(cfg)
        assert status.closed_trades == 0


# ── Phase gate does not break current paper-trading config ────────────────────


class TestRealConfigCompatibility:
    """
    Smoke tests against the actual config.py to ensure the current production
    config passes the gate (live accounts are blank, aggregate_accounts=[]).
    These tests catch regressions if someone edits config.py to add live
    account IDs or enable multi-account aggregation while in Phase 1.
    """

    def test_production_config_has_no_violations(self):
        """
        Validates that config.py source defaults have no phase gate violations.

        IBKR_LIVE_* env vars may be set in .env for future live-account use —
        this test checks the source defaults (empty strings), not runtime values.
        We prevent load_dotenv from re-populating them so the test is idempotent.
        """
        import os, sys
        from unittest.mock import patch
        # Temporarily clear live-account vars and prevent .env from restoring them
        with patch.dict(os.environ, {"IBKR_LIVE_1_ACCOUNT": "", "IBKR_LIVE_2_ACCOUNT": ""}):
            with patch("dotenv.load_dotenv"):  # no-op: don't reload from .env
                sys.modules.pop("config", None)
                from config import CONFIG
                violations = validate(CONFIG)
        sys.modules.pop("config", None)
        assert violations == [], (
            "Production config.py has phase gate violations:\n"
            + "\n".join(violations)
        )

    def test_production_config_is_in_phase1(self):
        from config import CONFIG
        pg = CONFIG.get("phase_gate", {})
        assert pg.get("current_phase") == 1, (
            "current_phase is not 1. Has Phase 1 been formally signed off by Amit?"
        )

    def test_production_config_has_frozen_features_defined(self):
        from config import CONFIG
        frozen = CONFIG.get("phase_gate", {}).get("frozen_features", {})
        assert "live_account_trading" in frozen
        assert "docker_deployment" in frozen
        assert frozen["live_account_trading"] >= 4
        assert frozen["docker_deployment"] >= 5

    def test_production_config_has_telegram_kill_switch_as_phase4_gate(self):
        """telegram_kill_switch must be frozen at Phase 4 — gate condition for live trading."""
        from config import CONFIG
        frozen = CONFIG.get("phase_gate", {}).get("frozen_features", {})
        assert "telegram_kill_switch" in frozen, (
            "telegram_kill_switch must be in frozen_features (required before live trading)"
        )
        assert frozen["telegram_kill_switch"] == 4

    def test_production_config_has_telegram_section(self):
        """config.py must declare the telegram section (even if token is empty in Phase 1)."""
        from config import CONFIG
        tg = CONFIG.get("telegram", {})
        assert "bot_token" in tg
        assert "authorized_chat_ids" in tg


# ── IC + walk-forward validation gate ────────────────────────────────────────


class TestICValidationGate:
    """
    phase_gate.validate() must enforce the IC + walk-forward gate when
    live accounts are active or current_phase >= 4.
    The gate must be silent in Phase 1 without live accounts.
    """

    def test_missing_ic_result_triggers_violation_when_live_active(self, monkeypatch):
        """
        When live_1 is set and ic_validation_result.json does not exist,
        validate() must include an ic_walkforward_validation violation.
        """
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=4)
        cfg["accounts"]["live_1"] = "U3059777"
        violations = validate(cfg)
        ic_v = [v for v in violations if "ic_walkforward_validation" in v]
        assert len(ic_v) == 1
        assert "not found" in ic_v[0].lower() or "ic_validation_result" in ic_v[0]

    def test_ic_result_not_ready_blocks_live_transition(self, monkeypatch):
        """
        When ic_validation_result.json exists but ready_for_live is False,
        validate() must still include the IC validation violation.
        """
        import phase_gate as pg
        not_ready = {
            "ready_for_live": False,
            "failures": ["SHARPE GATE: sharpe 0.3 < 0.8"],
        }
        monkeypatch.setattr(pg, "_load_ic_validation_result",
                            lambda data_dir=None: not_ready)
        cfg = _base_config(current_phase=4)
        cfg["accounts"]["live_1"] = "U3059777"
        violations = validate(cfg)
        ic_v = [v for v in violations if "ic_walkforward_validation" in v]
        assert len(ic_v) == 1
        assert "sharpe" in ic_v[0].lower() or "gate not passed" in ic_v[0].lower()

    def test_ic_result_ready_clears_ic_violation(self, monkeypatch):
        """
        When ic_validation_result.json has ready_for_live=True,
        the IC validation violation must NOT appear.
        """
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result",
                            lambda data_dir=None: {"ready_for_live": True, "failures": []})
        cfg = _base_config(current_phase=4)
        cfg["accounts"]["live_1"] = "U3059777"
        violations = validate(cfg)
        ic_v = [v for v in violations if "ic_walkforward_validation" in v]
        assert len(ic_v) == 0

    def test_ic_gate_silent_in_phase1_without_live_accounts(self, monkeypatch):
        """
        In Phase 1 with no live accounts, the IC gate must not fire.
        Do not add noise to the paper trading setup.
        """
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        # No live accounts in default _base_config
        violations = validate(cfg)
        ic_v = [v for v in violations if "ic_walkforward_validation" in v]
        assert len(ic_v) == 0

    def test_get_status_has_ic_validation_passed_field(self):
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert hasattr(status, "ic_validation_passed")
        assert isinstance(status.ic_validation_passed, bool)

    def test_as_dict_includes_ic_validation_passed(self):
        cfg = _base_config(current_phase=1)
        d = get_status(cfg).as_dict()
        assert "ic_validation_passed" in d

    def test_ic_validation_passed_true_when_result_ready(self, monkeypatch):
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result",
                            lambda data_dir=None: {"ready_for_live": True, "failures": []})
        cfg = _base_config(current_phase=4)
        status = get_status(cfg)
        assert status.ic_validation_passed is True

    def test_ic_validation_passed_false_when_result_missing(self, monkeypatch):
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert status.ic_validation_passed is False

    def test_production_config_has_ic_walkforward_validation_frozen(self):
        from config import CONFIG
        frozen = CONFIG.get("phase_gate", {}).get("frozen_features", {})
        assert "ic_walkforward_validation" in frozen, (
            "ic_walkforward_validation must be in frozen_features (Phase 4 gate)"
        )
        assert frozen["ic_walkforward_validation"] == 4

    def test_production_config_has_ic_validation_gate_thresholds(self):
        from config import CONFIG
        gate = CONFIG.get("phase_gate", {}).get("ic_validation_gate", {})
        assert "min_valid_records"      in gate
        assert "min_mean_positive_ic"   in gate
        assert "min_positive_dims"      in gate
        assert "min_walkforward_sharpe" in gate
        assert gate["min_valid_records"]      == 50
        assert gate["min_mean_positive_ic"]   == pytest.approx(0.05)
        assert gate["min_positive_dims"]      == 5
        assert gate["min_walkforward_sharpe"] == pytest.approx(0.8)


# ── IC + walk-forward as mandatory paper trading exit criterion ────────────────


class TestPhase1CompleteRequiresICValidation:
    """
    Regression guard for the high-severity risk:
      'System could be paper trading profitably due to favorable market
       conditions rather than valid signals.'

    phase1_complete MUST be False until ic_walkforward_validated is True.
    Walk-forward out-of-sample Sharpe and IC quality are mandatory paper
    trading exit criteria — not optional enhancements.
    """

    def test_criteria_met_always_contains_ic_walkforward_validated_key(self, monkeypatch):
        """criteria_met must always expose the ic_walkforward_validated key."""
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert "ic_walkforward_validated" in status.criteria_met

    def test_ic_walkforward_validated_false_when_result_missing(self, monkeypatch):
        """Missing ic_validation_result.json → ic_walkforward_validated is False."""
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert status.criteria_met["ic_walkforward_validated"] is False

    def test_ic_walkforward_validated_false_when_not_ready(self, monkeypatch):
        """ready_for_live=False → ic_walkforward_validated is False."""
        import phase_gate as pg
        monkeypatch.setattr(
            pg, "_load_ic_validation_result",
            lambda data_dir=None: {"ready_for_live": False, "failures": ["SHARPE GATE: 0.3 < 0.8"]},
        )
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert status.criteria_met["ic_walkforward_validated"] is False

    def test_ic_walkforward_validated_true_when_ready(self, monkeypatch):
        """ready_for_live=True → ic_walkforward_validated is True."""
        import phase_gate as pg
        monkeypatch.setattr(
            pg, "_load_ic_validation_result",
            lambda data_dir=None: {"ready_for_live": True, "failures": []},
        )
        cfg = _base_config(current_phase=1)
        status = get_status(cfg)
        assert status.criteria_met["ic_walkforward_validated"] is True

    def test_phase1_complete_false_when_only_ic_missing(self, monkeypatch, tmp_path):
        """
        Even with 200+ trades and all other criteria met,
        phase1_complete must be False when IC validation has not been run.
        """
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        trades_file = tmp_path / "trades.json"
        trades_file.write_text(
            json.dumps([{"id": i, "status": "closed"} for i in range(200)])
        )
        cfg = _base_config(current_phase=1, overrides={"trade_log": str(trades_file)})
        status = get_status(cfg)
        assert status.criteria_met["ic_walkforward_validated"] is False
        assert not status.phase1_complete

    def test_phase1_complete_false_when_only_ic_not_ready(self, monkeypatch, tmp_path):
        """
        IC validation result exists but Sharpe gate failed →
        phase1_complete must still be False.
        """
        import phase_gate as pg
        monkeypatch.setattr(
            pg, "_load_ic_validation_result",
            lambda data_dir=None: {"ready_for_live": False, "failures": ["SHARPE GATE: 0.3 < 0.8"]},
        )
        trades_file = tmp_path / "trades.json"
        trades_file.write_text(
            json.dumps([{"id": i, "status": "closed"} for i in range(200)])
        )
        cfg = _base_config(current_phase=1, overrides={"trade_log": str(trades_file)})
        status = get_status(cfg)
        assert not status.phase1_complete

    def test_phase1_complete_still_false_when_only_ic_passes_no_trades(self, monkeypatch):
        """IC passing alone is not enough — trade count must also be met."""
        import phase_gate as pg
        monkeypatch.setattr(
            pg, "_load_ic_validation_result",
            lambda data_dir=None: {"ready_for_live": True, "failures": []},
        )
        cfg = _base_config(current_phase=1, overrides={"trade_log": "/nonexistent/trades.json"})
        status = get_status(cfg)
        assert status.criteria_met["ic_walkforward_validated"] is True
        assert status.criteria_met["min_closed_trades"] is False
        assert not status.phase1_complete

    def test_as_dict_exposes_ic_walkforward_validated_in_criteria_met(self, monkeypatch):
        """as_dict() must include ic_walkforward_validated inside criteria_met."""
        import phase_gate as pg
        monkeypatch.setattr(pg, "_load_ic_validation_result", lambda data_dir=None: None)
        cfg = _base_config(current_phase=1)
        d = get_status(cfg).as_dict()
        assert "ic_walkforward_validated" in d["criteria_met"]
        assert d["criteria_met"]["ic_walkforward_validated"] is False
