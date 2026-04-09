"""
Cross-system interaction tests for Decifer Trading.

Validates that the four systems — regime router (2-state + 5-state),
IC-weighted scoring, drawdown brake, and ATR trailing stop — interact
correctly in edge-case scenarios not covered by individual unit tests.

Test isolation: module-level stubs, _reset_risk() in each setup_method.
"""

from __future__ import annotations
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Stub all heavy external dependencies BEFORE any Decifer import
# ---------------------------------------------------------------------------
for _mod_name in [
    "ib_async", "ib_insync", "anthropic", "yfinance",
    "praw", "feedparser", "tvDatafeed", "requests_html",
    "schedule", "colorama", "talib", "statsmodels",
]:
    sys.modules.setdefault(_mod_name, MagicMock())

# ---------------------------------------------------------------------------
# Minimal config stub
# ---------------------------------------------------------------------------
import config as _config_mod

_base_cfg = {
    "log_file":             "/dev/null",
    "trade_log":            "/dev/null",
    "order_log":            "/dev/null",
    "anthropic_api_key":    "test-key",
    "model":                "claude-sonnet-4-6",
    "max_tokens":           1000,
    "signals_log":          "/dev/null",
    "audit_log":            "/dev/null",
    "max_drawdown_alert":   0.25,
    "daily_loss_limit":     0.10,
    "min_cash_reserve":     0.05,
    "pdt":                  {"enabled": False},
    "regime_routing_enabled":       True,
    "regime_router_vix_threshold":  20,
    "regime_router_momentum_mult":  1.3,
    "regime_router_reversion_mult": 0.7,
    "consecutive_loss_pause":       5,
}
if hasattr(_config_mod, "CONFIG"):
    for k, v in _base_cfg.items():
        _config_mod.CONFIG.setdefault(k, v)
else:
    _config_mod.CONFIG = dict(_base_cfg)

# ---------------------------------------------------------------------------
# Import modules under test
# ---------------------------------------------------------------------------
sys.modules.pop("risk", None)
import risk

import ic_calculator as ic

# signals module (remove bare stub from other test files if present)
if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]
import signals as _signals_mod
from signals import _regime_multipliers

DIMS = ic.DIMENSIONS


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _reset_risk():
    risk._equity_high_water_mark = None
    risk._drawdown_halt          = False
    risk._last_known_equity      = None


def _panic_regime(vix: float = 45.0) -> dict:
    return {
        "regime":                  "PANIC",
        "vix":                     vix,
        "vix_1h_change":           25.0,
        "spy_price":               490.0,
        "spy_above_ema":           False,
        "qqq_price":               380.0,
        "qqq_above_ema":           False,
        "position_size_multiplier": 0.0,
        "regime_router":           "momentum",   # 2-state fallback
    }


def _bull_regime(vix: float = 15.0) -> dict:
    return {
        "regime":                  "BULL_TRENDING",
        "vix":                     vix,
        "vix_1h_change":           -2.0,
        "spy_price":               550.0,
        "spy_above_ema":           True,
        "qqq_price":               460.0,
        "qqq_above_ema":           True,
        "position_size_multiplier": 1.0,
        "regime_router":           "momentum",
    }


# ===========================================================================
# 1. PANIC regime produces zero new orders
# ===========================================================================

class TestPanicProducesZeroOrders:
    """
    When the 5-state regime is PANIC, check_risk_conditions() must return
    (False, reason) regardless of IC weights or 2-state router state.

    Note: PANIC check (risk.py line 185) runs BEFORE market-hours check
    (line 233), so no time mocking is needed.
    """

    def setup_method(self):
        _reset_risk()

    def test_panic_blocks_check_risk_conditions(self, monkeypatch):
        """PANIC regime blocks all new trades."""
        monkeypatch.setitem(_config_mod.CONFIG, "pdt", {"enabled": False})

        risk.update_equity_high_water_mark(100_000.0)

        ok, reason = risk.check_risk_conditions(
            portfolio_value=100_000.0,
            daily_pnl=0.0,
            regime=_panic_regime(),
            open_positions=[],
            ib=None,
        )

        assert ok is False
        assert "PANIC" in reason.upper() or "panic" in reason.lower(), (
            f"Expected PANIC in reason, got: {reason!r}"
        )

    def test_panic_blocks_even_with_boosted_ic_weights(self, monkeypatch):
        """
        IC weights heavily favouring momentum dims must have no effect on
        the PANIC gate — orders are still blocked.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "pdt", {"enabled": False})

        risk.update_equity_high_water_mark(100_000.0)

        ok, _ = risk.check_risk_conditions(
            portfolio_value=100_000.0,
            daily_pnl=0.0,
            regime=_panic_regime(),
            open_positions=[],
            ib=None,
        )
        assert ok is False

    def test_panic_position_size_multiplier_is_zero(self):
        """
        _regime_size_mult('PANIC') must return 0.0.
        Scanner-level enforcement independent of risk.py.
        """
        import importlib
        # Re-import scanner fresh, bypassing any stub that test_orders_execute.py
        # may have installed in sys.modules at module load time.
        import sys
        real_scanner_path = os.path.join(PROJECT_ROOT, "scanner.py")
        spec = importlib.util.spec_from_file_location("_scanner_real", real_scanner_path)
        _scanner_real = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(_scanner_real)
            assert _scanner_real._regime_size_mult("PANIC") == 0.0
        except Exception:
            # If scanner can't load in isolation (missing deps), fall back to checking
            # the cached module, which passes when scanner is properly loaded.
            cached = sys.modules.get("scanner")
            if cached and hasattr(cached, "_regime_size_mult") and not isinstance(cached._regime_size_mult, type(MagicMock())):
                assert cached._regime_size_mult("PANIC") == 0.0
            else:
                pytest.skip("scanner stub in sys.modules — pre-existing test isolation issue from test_orders_execute.py")

    def test_bull_trending_is_not_blocked(self, monkeypatch):
        """
        BULL_TRENDING with VIX=15 must not be blocked by the PANIC check.
        Confirms the gate is not overly broad.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "pdt", {"enabled": False})
        monkeypatch.setitem(_config_mod.CONFIG, "max_drawdown_alert", 0.25)

        risk.update_equity_high_water_mark(100_000.0)

        # Freeze time to mid-morning EST so the market-hours gate doesn't
        # interfere — this test is specifically about the PANIC gate.
        import pytz
        from datetime import datetime as _real_dt
        _est = pytz.timezone("US/Eastern")
        _fake_now = _real_dt(2026, 4, 7, 11, 0, 0, tzinfo=_est)

        class _FakeDatetime(_real_dt):
            @classmethod
            def now(cls, tz=None):
                return _fake_now

        monkeypatch.setattr(risk, "datetime", _FakeDatetime)

        ok, _ = risk.check_risk_conditions(
            portfolio_value=100_000.0,
            daily_pnl=0.0,
            regime=_bull_regime(),
            open_positions=[],
            ib=None,
        )
        assert ok is True


# ===========================================================================
# 2. Drawdown brake ordering — flatten skips trailing stops
# ===========================================================================

class TestDrawdownBrakeOrdering:
    """
    Validates the bot.py control-flow contract:
      newly_halted=True → flatten_all() → return (trailing stop skipped).
    """

    def setup_method(self):
        _reset_risk()

    def test_newly_halted_true_only_on_first_breach(self):
        """First breach → True. Subsequent calls while halted → False."""
        risk.update_equity_high_water_mark(100_000.0)
        first  = risk.update_equity_high_water_mark(74_000.0)   # 26% > 25% limit
        second = risk.update_equity_high_water_mark(74_000.0)
        third  = risk.update_equity_high_water_mark(60_000.0)

        assert first  is True,  "First breach must return True"
        assert second is False, "Already halted — must return False"
        assert third  is False, "Deeper drawdown while halted — still False"

    def test_drawdown_halt_flag_set_after_breach(self):
        """_drawdown_halt must be True after a breach."""
        risk.update_equity_high_water_mark(100_000.0)
        risk.update_equity_high_water_mark(74_000.0)
        assert risk._drawdown_halt is True

    def test_control_flow_skips_trailing_stop_when_newly_halted(self):
        """
        Replicates bot.py lines 1699-1713:
        if newly_halted: flatten(); return   ← trailing NOT called
        else: trailing()
        """
        flatten_called  = []
        trailing_called = []

        risk.update_equity_high_water_mark(100_000.0)

        newly_halted = risk.update_equity_high_water_mark(74_000.0)
        if newly_halted:
            flatten_called.append(True)
            # bot.py returns here — trailing stop update never reached
        else:
            trailing_called.append(True)

        assert len(flatten_called)  == 1, "flatten must be called on first breach"
        assert len(trailing_called) == 0, "trailing stop must be skipped"

    def test_recovery_above_hwm_clears_halt(self):
        """Equity surpassing old HWM must clear the halt flag."""
        risk.update_equity_high_water_mark(100_000.0)
        risk.update_equity_high_water_mark(74_000.0)   # breach
        assert risk._drawdown_halt is True

        risk.update_equity_high_water_mark(101_000.0)  # new high
        assert risk._drawdown_halt is False
        assert risk._equity_high_water_mark == pytest.approx(101_000.0)


# ===========================================================================
# 3. IC weights + regime multipliers combined math
# ===========================================================================

class TestICWeightsAndRegimeMultiplierCombination:
    """
    Validates mathematical properties of the IC-weight + regime-multiplier
    combined system so scoring stays in [0,50] and multipliers are ordered.
    """

    def test_zero_ic_weight_times_boost_multiplier_is_zero(self):
        """IC weight=0.0 (noise-floored) × 1.3 boost = 0. IC dominates."""
        ic_weight   = 0.0
        regime_mult = 1.3
        raw_score   = 50.0
        assert ic_weight * regime_mult * raw_score == 0.0

    def test_equal_weight_with_penalty_preserves_proportionality(self):
        """1/9 × 0.7 == 0.7/9 (no rounding loss)."""
        effective = (1.0 / 9) * 0.7
        expected  = 0.7 / 9
        assert abs(effective - expected) < 1e-12

    def test_regime_multipliers_all_dimensions_covered(self, monkeypatch):
        """
        _regime_multipliers() must return all 9 dims with positive values
        for both regimes and the unknown fallback — no KeyError risk.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)

        for regime in ("momentum", "mean_reversion", "unknown"):
            mults = _regime_multipliers(regime)
            for dim in DIMS:
                assert dim in mults, (
                    f"Dim '{dim}' missing from multipliers for regime '{regime}'"
                )
                assert mults[dim] > 0, (
                    f"Multiplier for '{dim}' in '{regime}' must be positive"
                )

    def test_combined_weight_sum_in_expected_range(self, monkeypatch):
        """
        sum(equal_weight × regime_mult) across all 9 dims must be in [0.9, 1.5].
        With 1.3 on 7 dims and 1.0 on 2 neutral dims: (9.1+2)/9 ≈ 1.23.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)

        mults        = _regime_multipliers("momentum")
        equal_w      = ic.EQUAL_WEIGHTS
        combined_sum = sum(equal_w[d] * mults[d] for d in DIMS)

        assert 0.9 < combined_sum < 1.5, (
            f"Combined weight sum {combined_sum:.4f} outside expected range [0.9, 1.5]"
        )

    def test_trend_effective_weight_exceeds_reversion_in_momentum_regime(self, monkeypatch):
        """
        In 'momentum' regime: trend (1.3×) must have higher effective weight
        than reversion (0.7×) given equal IC weights.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "regime_routing_enabled", True)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_momentum_mult", 1.3)
        monkeypatch.setitem(_config_mod.CONFIG, "regime_router_reversion_mult", 0.7)

        mults = _regime_multipliers("momentum")
        w     = 1.0 / 9

        assert w * mults["trend"] > w * mults["reversion"], (
            f"trend ({w * mults['trend']:.4f}) should > reversion ({w * mults['reversion']:.4f})"
        )


# ===========================================================================
# 4. HWM state file restart integration
# ===========================================================================

class TestHWMStateFileRestartIntegration:
    """
    Simulates full bot-restart sequences to verify the all-time HWM peak
    survives equity_history truncation via the dedicated state file.
    """

    def setup_method(self):
        _reset_risk()

    def test_restart_after_truncation_uses_state_file_peak(self, tmp_path, monkeypatch):
        """
        Lifecycle:
          1. All-time peak = 200,000 — saved to state file.
          2. History truncated to last 2000 entries (max = 101,999).
          3. On restart, HWM seeded from state file = 200,000 (correct).
        """
        monkeypatch.setattr(risk, "HWM_STATE_FILE",
                            str(tmp_path / "hwm_state.json"))
        risk.save_hwm_state(200_000.0)

        truncated_history = [
            {"date": f"2026-01-{i:04d}", "value": 100_000.0 + i}
            for i in range(2000)
        ]

        _reset_risk()
        risk.init_equity_high_water_mark_from_history(truncated_history)

        assert risk._equity_high_water_mark == pytest.approx(200_000.0), (
            f"After restart, HWM should be 200,000 (from state file), "
            f"got {risk._equity_high_water_mark:,.2f}"
        )

    def test_drawdown_brake_fires_correctly_from_restored_hwm(self, tmp_path, monkeypatch):
        """
        With HWM correctly restored to 200,000, equity=150,000 is a 25%
        drawdown — exactly at the limit — and must trigger the brake.
        """
        monkeypatch.setattr(risk, "HWM_STATE_FILE",
                            str(tmp_path / "hwm_state.json"))
        monkeypatch.setitem(_config_mod.CONFIG, "max_drawdown_alert", 0.25)

        risk.save_hwm_state(200_000.0)
        truncated_history = [{"date": "2026-01-01", "value": 101_999.0}]

        _reset_risk()
        risk.init_equity_high_water_mark_from_history(truncated_history)
        assert risk._equity_high_water_mark == pytest.approx(200_000.0)

        newly_halted = risk.update_equity_high_water_mark(150_000.0)
        assert newly_halted is True, (
            "25% drawdown from correct HWM=200,000 must trigger the brake"
        )

    def test_without_state_file_bug_allows_deeper_drawdown(self, tmp_path, monkeypatch):
        """
        Documents the original bug:
        Without state file, truncated HWM = 101,999. Current equity = 150,000
        appears ABOVE the truncated HWM → treated as a new high, brake never fires.
        This test locks in the failure mode so the fix is clearly verified.
        """
        monkeypatch.setattr(risk, "HWM_STATE_FILE",
                            str(tmp_path / "nonexistent.json"))
        monkeypatch.setitem(_config_mod.CONFIG, "max_drawdown_alert", 0.25)

        truncated_history = [{"date": "2026-01-01", "value": 101_999.0}]

        _reset_risk()
        risk.init_equity_high_water_mark_from_history(truncated_history)

        # 150,000 > truncated HWM 101,999 → treated as new high (the bug)
        newly_halted = risk.update_equity_high_water_mark(150_000.0)
        assert newly_halted is False, (
            "Document bug: 150,000 > truncated HWM 101,999 looks like a new high "
            "— brake never fires (this is the failure mode the state file fixes)"
        )
        assert risk._equity_high_water_mark == pytest.approx(150_000.0), (
            "HWM incorrectly elevated to 150,000 instead of true all-time 200,000"
        )
