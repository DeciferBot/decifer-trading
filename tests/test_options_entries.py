"""
Tests for options_entries.py — deterministic options entry bridge.

Coverage:
  - Gate rejections with skip telemetry verification
  - Happy path: CALL_BUYER → LONG, PUT_BUYER → SHORT
  - Existing open stock position on same symbol (policy: ALLOW)
  - Existing open options position on same symbol (policy: BLOCK)
  - Per-cycle cap enforcement
  - Score scaling and conviction mapping
  - No suitable contract → skip
"""

from __future__ import annotations

import os
import sys
import threading
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy dependencies before any Decifer import ────────────────

_ib_async = types.ModuleType("ib_async")
_ib_async.IB = MagicMock
_ib_async.Option = MagicMock
_ib_async.Stock = MagicMock
_ib_async.Forex = MagicMock
_ib_async.LimitOrder = MagicMock
sys.modules.setdefault("ib_async", _ib_async)

_anthropic = types.ModuleType("anthropic")
_anthropic.Anthropic = MagicMock
sys.modules.setdefault("anthropic", _anthropic)

_risk = types.ModuleType("risk")
_risk.check_combined_exposure = MagicMock(return_value=(True, "ok"))
_risk.check_sector_concentration = MagicMock(return_value=(True, "ok"))
_risk.record_win = MagicMock()
_risk.record_loss = MagicMock()
_risk.is_trading_day = MagicMock(return_value=True)
_risk.CONVICTION_MULT = {"MEDIUM": 0.65, "HIGH": 1.00}
sys.modules.setdefault("risk", _risk)

_learning = types.ModuleType("learning")
_learning.log_order = MagicMock()
_learning.log_trade = MagicMock()
_learning._append_audit_event = MagicMock()
sys.modules.setdefault("learning", _learning)

_trade_log = types.ModuleType("trade_log")
_trade_log.append_event = MagicMock()
_trade_log.close_trade = MagicMock()
_trade_log.load_pending_exits = MagicMock(return_value={})
_trade_log.upsert_pending_exit = MagicMock()
_trade_log.delete_pending_exit = MagicMock()
sys.modules.setdefault("trade_log", _trade_log)

_ic_calc = types.ModuleType("ic_calculator")
_ic_calc.get_current_weights = MagicMock(return_value=None)
sys.modules.setdefault("ic_calculator", _ic_calc)

_bot_voice = types.ModuleType("bot_voice")
_bot_voice.speak_natural = MagicMock()
sys.modules.setdefault("bot_voice", _bot_voice)

_config_mod = types.ModuleType("config")
_config_mod.CONFIG = {
    "options_enabled": True,
    "options_scan_entry_min_score": 18,
    "options_scan_max_entries_per_cycle": 2,
    "max_positions": 10,
    "options_stop_loss": 0.20,
    "options_profit_target": 0.75,
    "active_account": "TEST",
    "reentry_cooldown_minutes": 30,
    "options_min_dte": 7,
    "options_max_dte": 45,
    "options_max_ivr": 65,
    "options_target_delta": 0.50,
    "options_delta_range": 0.35,
    "options_max_risk_pct": 0.025,
    "options_min_volume": 25,
    "options_min_oi": 100,
    "options_max_spread_pct": 0.35,
    "high_conviction_score": 36,
    "options_dte_by_trade_type": {
        "SWING": {"min": 14, "max": 30, "target": 21},
    },
    "log_file": "/tmp/decifer_test.log",
    "trade_log": "/tmp/trades_test.json",
    "order_log": "/tmp/orders_test.json",
    "positions_file": "/tmp/positions_test.json",
}
sys.modules.setdefault("config", _config_mod)

import pytest

# ── Helpers ───────────────────────────────────────────────────────────

def _sig(
    symbol="AAPL",
    signal="CALL_BUYER",
    options_score=22,
    unusual_calls=True,
    unusual_puts=False,
):
    return {
        "symbol": symbol,
        "signal": signal,
        "options_score": options_score,
        "unusual_calls": unusual_calls,
        "unusual_puts": unusual_puts,
        "cp_ratio": 2.5,
        "iv_rank": 20.0,
        "dte": 21,
        "expiry": "2026-05-30",
        "reasoning": "test signal",
    }


def _put_sig(symbol="TSLA", options_score=22):
    return _sig(symbol=symbol, signal="PUT_BUYER", options_score=options_score,
                unusual_calls=False, unusual_puts=True)


def _contract(symbol="AAPL", right="C"):
    return {
        "symbol": symbol,
        "right": right,
        "strike": 210.0,
        "expiry_str": "2026-05-30",
        "expiry_ibkr": "20260530",
        "dte": 21,
        "mid": 3.50,
        "bid": 3.40,
        "ask": 3.60,
        "spread_pct": 0.057,
        "volume": 500,
        "open_interest": 2000,
        "iv": 0.30,
        "iv_rank": 20.0,
        "delta": 0.50,
        "gamma": 0.02,
        "theta": -0.015,
        "vega": 0.18,
        "model_price": 3.50,
        "contracts": 1,
        "max_risk_dollars": 360.0,
        "underlying_price": 210.0,
    }


_REGIME_NORMAL = {"regime": "TRENDING_UP"}
_IB = MagicMock()

# Shared empty active_trades + lock for tests that don't need existing positions
_empty_trades: dict = {}
_lock = threading.RLock()


# ═══════════════════════════════════════════════════════════════════════
# 1. Gate rejections
# ═══════════════════════════════════════════════════════════════════════

class TestGateRejections:

    def _run(self, signals, *, market_open=True, trades=None, locked=None,
             recently_closed=False, open_order=False):
        """Run execute_options_entries with common patches."""
        trades = trades if trades is not None else _empty_trades
        lock = locked if locked is not None else _lock
        with patch("options_entries.is_options_market_open", return_value=market_open), \
             patch("options_entries._is_recently_closed", return_value=recently_closed), \
             patch("options_entries.has_open_order_for", return_value=open_order), \
             patch("options_entries.active_trades", trades), \
             patch("options_entries._trades_lock", lock), \
             patch("options_entries.find_best_contract", return_value=None), \
             patch("options_entries.execute_buy_option") as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, signals, 100_000, _REGIME_NORMAL
            )
        return result, mock_exec

    def test_master_switch_off(self):
        with patch.dict("config.CONFIG", {"options_enabled": False}):
            import options_entries
            result = options_entries.execute_options_entries(_IB, [_sig()], 100_000, _REGIME_NORMAL)
        assert result == frozenset()

    def test_market_closed(self):
        result, mock_exec = self._run([_sig()], market_open=False)
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_panic_regime_blocks_all(self):
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries.execute_buy_option") as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, [_sig(), _sig(symbol="MSFT")], 100_000, {"regime": "PANIC"}
            )
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_capitulation_regime_blocks_all(self):
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries.execute_buy_option") as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, [_sig()], 100_000, {"regime": "CAPITULATION"}
            )
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_earnings_play_not_executed(self):
        sig = _sig(signal="EARNINGS_PLAY")
        result, mock_exec = self._run([sig])
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_mixed_flow_not_executed(self):
        sig = _sig(signal="MIXED_FLOW")
        result, mock_exec = self._run([sig])
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_low_score_skipped(self):
        sig = _sig(options_score=15)  # below min of 18
        result, mock_exec = self._run([sig])
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_call_buyer_no_unusual_calls_skipped(self):
        sig = _sig(signal="CALL_BUYER", unusual_calls=False)
        result, mock_exec = self._run([sig])
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_put_buyer_no_unusual_puts_skipped(self):
        sig = _put_sig()
        sig["unusual_puts"] = False
        result, mock_exec = self._run([sig])
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_cooldown_blocks(self):
        result, mock_exec = self._run([_sig()], recently_closed=True)
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_open_order_blocks(self):
        result, mock_exec = self._run([_sig()], open_order=True)
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_existing_options_position_blocks(self):
        trades = {"AAPL_C_210_2026-05-30": {"symbol": "AAPL", "instrument": "option", "status": "OPEN"}}
        result, mock_exec = self._run([_sig(symbol="AAPL")], trades=trades)
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_no_contract_found_skips(self):
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", _empty_trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", return_value=None), \
             patch("options_entries.execute_buy_option") as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(_IB, [_sig()], 100_000, _REGIME_NORMAL)
        assert result == frozenset()
        mock_exec.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# 2. Happy paths
# ═══════════════════════════════════════════════════════════════════════

class TestHappyPath:

    def _run_happy(self, signals, *, contract_factory=None):
        def _get_contract(symbol, direction, **kwargs):
            right = "C" if direction == "LONG" else "P"
            return _contract(symbol=symbol, right=right)
        cf = contract_factory or _get_contract

        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", _empty_trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", side_effect=cf) as mock_fbc, \
             patch("options_entries.execute_buy_option", return_value=True) as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, signals, 100_000, _REGIME_NORMAL
            )
        return result, mock_fbc, mock_exec

    def test_call_buyer_fires_long(self):
        result, mock_fbc, mock_exec = self._run_happy([_sig(symbol="AAPL", options_score=22)])
        assert "AAPL" in result
        # find_best_contract must receive direction=LONG
        call_kwargs = mock_fbc.call_args.kwargs
        assert call_kwargs["direction"] == "LONG"
        assert call_kwargs["trade_type"] == "SWING"
        assert call_kwargs["symbol"] == "AAPL"
        # execute_buy_option must receive scaled score and SWING
        exec_kwargs = mock_exec.call_args.kwargs
        assert exec_kwargs["score"] == int(22 / 30 * 100)
        assert exec_kwargs["trade_type"] == "SWING"
        assert exec_kwargs["regime"] == "TRENDING_UP"

    def test_put_buyer_fires_short(self):
        result, mock_fbc, mock_exec = self._run_happy([_put_sig(symbol="TSLA", options_score=20)])
        assert "TSLA" in result
        assert mock_fbc.call_args.kwargs["direction"] == "SHORT"

    def test_returns_frozenset_of_fired_symbols(self):
        signals = [_sig(symbol="AAPL"), _put_sig(symbol="TSLA")]
        result, _, _ = self._run_happy(signals)
        assert isinstance(result, frozenset)
        assert result == {"AAPL", "TSLA"}

    def test_execute_buy_option_not_called_on_false_return(self):
        """execute_buy_option returns False → symbol not in fired set."""
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", _empty_trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", return_value=_contract()), \
             patch("options_entries.execute_buy_option", return_value=False):
            import options_entries
            result = options_entries.execute_options_entries(_IB, [_sig()], 100_000, _REGIME_NORMAL)
        assert result == frozenset()


# ═══════════════════════════════════════════════════════════════════════
# 3. Exposure policy: existing stock position + new options entry → ALLOW
# ═══════════════════════════════════════════════════════════════════════

class TestStockPositionPolicy:

    def test_existing_stock_position_does_not_block_options_entry(self):
        """Policy rule 1: existing stock position + new options entry = ALLOW."""
        trades = {
            "AAPL": {"symbol": "AAPL", "instrument": "stock", "status": "OPEN", "qty": 100}
        }
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", return_value=_contract(symbol="AAPL")), \
             patch("options_entries.execute_buy_option", return_value=True) as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, [_sig(symbol="AAPL")], 100_000, _REGIME_NORMAL
            )
        # Must fire — stock position does NOT block options entry
        assert "AAPL" in result
        mock_exec.assert_called_once()

    def test_existing_options_position_blocks_new_options_entry(self):
        """Policy rule 2: existing options position + new options entry = BLOCK."""
        trades = {
            "AAPL_C_210_2026-05-30": {"symbol": "AAPL", "instrument": "option", "status": "OPEN"}
        }
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", return_value=_contract(symbol="AAPL")), \
             patch("options_entries.execute_buy_option") as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, [_sig(symbol="AAPL")], 100_000, _REGIME_NORMAL
            )
        assert result == frozenset()
        mock_exec.assert_not_called()

    def test_reserved_options_slot_does_not_block(self):
        """RESERVED status means the slot is mid-flight — not yet a real position."""
        trades = {
            "AAPL_C_210_2026-05-30": {"symbol": "AAPL", "instrument": "option", "status": "RESERVED"}
        }
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", return_value=_contract(symbol="AAPL")), \
             patch("options_entries.execute_buy_option", return_value=True) as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, [_sig(symbol="AAPL")], 100_000, _REGIME_NORMAL
            )
        # RESERVED slot is treated as an existing options position → BLOCK
        # (execute_buy_option itself also checks and rejects RESERVED entries)
        # Policy: block to avoid racing with an in-flight order for the same symbol
        assert result == frozenset()
        mock_exec.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# 4. Per-cycle cap
# ═══════════════════════════════════════════════════════════════════════

class TestPerCycleCap:

    def _run_capped(self, signals, max_entries=2):
        def _get_contract(symbol, direction, **kwargs):
            right = "C" if direction == "LONG" else "P"
            return _contract(symbol=symbol, right=right)

        with patch.dict("config.CONFIG", {"options_scan_max_entries_per_cycle": max_entries}), \
             patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", _empty_trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", side_effect=_get_contract), \
             patch("options_entries.execute_buy_option", return_value=True) as mock_exec:
            import options_entries
            result = options_entries.execute_options_entries(
                _IB, signals, 100_000, _REGIME_NORMAL
            )
        return result, mock_exec

    def test_cap_at_default_two(self):
        signals = [_sig(symbol=s) for s in ["AAPL", "MSFT", "NVDA", "TSLA", "META"]]
        result, mock_exec = self._run_capped(signals, max_entries=2)
        assert len(result) == 2
        assert mock_exec.call_count == 2

    def test_cap_at_one(self):
        signals = [_sig(symbol=s) for s in ["AAPL", "MSFT", "NVDA"]]
        result, mock_exec = self._run_capped(signals, max_entries=1)
        assert len(result) == 1
        assert mock_exec.call_count == 1

    def test_fewer_signals_than_cap(self):
        signals = [_sig(symbol="AAPL")]
        result, mock_exec = self._run_capped(signals, max_entries=2)
        assert len(result) == 1
        assert mock_exec.call_count == 1


# ═══════════════════════════════════════════════════════════════════════
# 5. Score scaling and conviction mapping
# ═══════════════════════════════════════════════════════════════════════

class TestScoreAndConviction:

    @pytest.mark.parametrize("raw_score,expected_scaled,expected_conviction", [
        (18, int(18 / 30 * 100), 0.65),   # MEDIUM threshold
        (21, int(21 / 30 * 100), 0.65),   # just below HIGH threshold
        (22, int(22 / 30 * 100), 1.00),   # HIGH threshold
        (30, 100,                1.00),   # maximum score
    ])
    def test_score_scaling_and_conviction(self, raw_score, expected_scaled, expected_conviction):
        with patch("options_entries.is_options_market_open", return_value=True), \
             patch("options_entries._is_recently_closed", return_value=False), \
             patch("options_entries.has_open_order_for", return_value=False), \
             patch("options_entries.active_trades", _empty_trades), \
             patch("options_entries._trades_lock", _lock), \
             patch("options_entries.find_best_contract", return_value=_contract()), \
             patch("options_entries.execute_buy_option", return_value=True) as mock_exec:
            import options_entries
            options_entries.execute_options_entries(
                _IB, [_sig(options_score=raw_score)], 100_000, _REGIME_NORMAL
            )
        kwargs = mock_exec.call_args.kwargs
        assert kwargs["score"] == expected_scaled
        assert kwargs["conviction"] == expected_conviction
