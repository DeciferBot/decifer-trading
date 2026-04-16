#!/usr/bin/env python3
"""
Unit tests for signal_types.py and signal_dispatcher.py.

Covers:
  - Signal dataclass creation and serialisation
  - dispatch_signals() routes 5 LONG signals → 5 order results
  - NEUTRAL signals produce results with success=False
  - Signal logging writes valid JSON lines
  - to_dict() / to_json() round-trips
"""

import json
import os
import sys
import types
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# ── Project root on path ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy dependencies before any project imports ───────────────────────

# ib_async
ib_mod = types.ModuleType("ib_async")
ib_mod.IB = MagicMock
ib_mod.Stock = MagicMock
ib_mod.LimitOrder = MagicMock
ib_mod.StopOrder = MagicMock
ib_mod.MarketOrder = MagicMock
ib_mod.Option = MagicMock
ib_mod.Future = MagicMock
ib_mod.Forex = MagicMock
sys.modules.setdefault("ib_async", ib_mod)

# colorama
col_mod = types.ModuleType("colorama")
col_mod.Fore = types.SimpleNamespace(YELLOW="", GREEN="", CYAN="", RED="", WHITE="", MAGENTA="", RESET="")
col_mod.Style = types.SimpleNamespace(RESET_ALL="", BRIGHT="")
col_mod.init = lambda **kw: None
sys.modules.setdefault("colorama", col_mod)

# zoneinfo
zi_mod = types.ModuleType("zoneinfo")
zi_mod.ZoneInfo = lambda tz: None
sys.modules.setdefault("zoneinfo", zi_mod)

# config (minimal)
cfg_mod = types.ModuleType("config")
cfg_mod.CONFIG = {
    "max_positions": 10,
    "min_score_to_trade": 20,
    "active_account": "DUP00000",
    "trade_log": "/tmp/test_trades.json",
    "order_log": "/tmp/test_orders.json",
    "ORDER_DUPLICATE_CHECK_ENABLED": False,
}
sys.modules.setdefault("config", cfg_mod)

# risk (stubs for correlation checks inside execute_buy)
risk_mod = types.ModuleType("risk")
risk_mod.calculate_position_size = MagicMock(return_value=10)
risk_mod.calculate_stops = MagicMock(return_value=(95.0, 110.0))
risk_mod.check_correlation = MagicMock(return_value=(True, "ok"))
risk_mod.record_win = MagicMock()
risk_mod.record_loss = MagicMock()
risk_mod.check_combined_exposure = MagicMock(return_value=(True, "ok"))
risk_mod.check_sector_concentration = MagicMock(return_value=(True, "ok"))
sys.modules.setdefault("risk", risk_mod)

# learning (stub) — include all symbols consumed by signal_pipeline and signal_dispatcher
learning_mod = types.ModuleType("learning")
learning_mod.log_order = MagicMock()
learning_mod.log_signal_scan = MagicMock()
sys.modules.setdefault("learning", learning_mod)

# scanner (stub)
scanner_mod = types.ModuleType("scanner")
sys.modules.setdefault("scanner", scanner_mod)

# trade_advisor (stub) — advise_trade is called inside dispatch before execute_buy
_FakeAdvice = types.SimpleNamespace(
    profit_target=110.0,
    stop_loss=90.0,
    size_multiplier=1.0,
    instrument="stock",
    advice_id="test-advice-id",
)
trade_advisor_mod = types.ModuleType("trade_advisor")
trade_advisor_mod.advise_trade = MagicMock(return_value=_FakeAdvice)
sys.modules.setdefault("trade_advisor", trade_advisor_mod)

# ── Now import the modules under test ────────────────────────────────────────
import signal_dispatcher
from market_intelligence import SignalClassification
from signal_types import Signal


def _make_scalp_classify(candidates, regime=None, trade_contexts=None):
    """Stub for classify_signals: passes every signal as SCALP, no AVOID."""
    return (
        "TRENDING_UP",
        "mock market read",
        [
            SignalClassification(
                symbol=c["symbol"],
                trade_type="SCALP",
                conviction=0.8,
                reasoning="test",
                source="mock",
            )
            for c in candidates
        ],
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_signal(symbol: str, direction: str = "LONG") -> Signal:
    return Signal(
        symbol=symbol,
        direction=direction,
        conviction_score=7.0,
        dimension_scores={
            "trend": 7,
            "momentum": 6,
            "squeeze": 5,
            "flow": 4,
            "breakout": 3,
            "mtf": 6,
            "news": 4,
            "social": 2,
            "reversion": 1,
        },
        timestamp=datetime(2026, 3, 29, 10, 0, 0, tzinfo=UTC),
        regime_context="TRENDING_UP",
        source_agents=[0, 1, 2, 3],
        rationale="Test rationale",
        price=100.0,
        atr=2.5,
    )


# ── Test cases ────────────────────────────────────────────────────────────────


class TestSignalDataclass(unittest.TestCase):
    def test_fields_set_correctly(self):
        s = _make_signal("AAPL")
        self.assertEqual(s.symbol, "AAPL")
        self.assertEqual(s.direction, "LONG")
        self.assertAlmostEqual(s.conviction_score, 7.0)
        self.assertEqual(s.regime_context, "TRENDING_UP")
        self.assertEqual(s.price, 100.0)
        self.assertEqual(s.atr, 2.5)

    def test_to_dict_keys(self):
        d = _make_signal("TSLA").to_dict()
        for key in (
            "symbol",
            "direction",
            "conviction_score",
            "dimension_scores",
            "timestamp",
            "regime_context",
            "source_agents",
            "rationale",
            "price",
            "atr",
        ):
            self.assertIn(key, d)

    def test_to_json_is_valid_json(self):
        line = _make_signal("NVDA").to_json()
        parsed = json.loads(line)
        self.assertEqual(parsed["symbol"], "NVDA")

    def test_timestamp_is_iso_string_in_dict(self):
        d = _make_signal("MSFT").to_dict()
        # Should be an ISO 8601 string, not a datetime object
        self.assertIsInstance(d["timestamp"], str)
        self.assertIn("T", d["timestamp"])

    def test_default_rationale_is_empty(self):
        s = Signal(
            symbol="X",
            direction="NEUTRAL",
            conviction_score=0.0,
            dimension_scores={},
            timestamp=datetime.now(UTC),
            regime_context="UNKNOWN",
        )
        self.assertEqual(s.rationale, "")
        self.assertEqual(s.source_agents, [])


class TestDispatchSignals(unittest.TestCase):
    """
    Tests for dispatch_signals().  execute_buy is mocked so no IBKR
    connection is needed.
    """

    def setUp(self):
        self.ib = MagicMock()
        self.regime = {"regime": "TRENDING_UP", "vix": 15.0, "spy_price": 500.0}
        self.pv = 100_000.0

        # Fix the orders MagicMock so dispatch's cooldown/straddle guards don't
        # block signals before they reach execute_buy.
        import orders as _ord_stub

        _ord_stub._trades_lock = MagicMock()
        # Clear in-place rather than replacing — replacing breaks the reference
        # that orders_core.py holds, causing test_tranche_exits to see an empty dict.
        if isinstance(getattr(_ord_stub, "active_trades", None), dict):
            _ord_stub.active_trades.clear()
            self.addCleanup(_ord_stub.active_trades.clear)
        else:
            _ord_stub.active_trades = {}
        _ord_stub._is_recently_closed = MagicMock(return_value=False)

        # Patch classify_signals so the intelligence gate passes every LONG signal
        # as SCALP (no AVOID), allowing execute_buy to be reached.
        patcher = patch.object(signal_dispatcher, "classify_signals", side_effect=_make_scalp_classify)
        self.mock_classify = patcher.start()
        self.addCleanup(patcher.stop)

        # Patch the entry_gate so it approves all signals in dispatch tests.
        # Entry gate logic is covered separately in test_entry_gate.py.
        gate_patcher = patch("entry_gate.validate_entry", return_value=(True, "INTRADAY", "gate mocked in test", 35))
        gate_patcher.start()
        self.addCleanup(gate_patcher.stop)

        ctx_patcher = patch("trade_context.build_context", return_value=None)
        ctx_patcher.start()
        self.addCleanup(ctx_patcher.stop)

    def test_five_long_signals_produce_five_results(self):
        """DOD: mock 5 Signal objects → dispatch_signals() produces 5 order results."""
        signals = [_make_signal(sym) for sym in ("AAPL", "MSFT", "NVDA", "TSLA", "GOOG")]

        with patch.object(signal_dispatcher, "execute_buy", return_value=True):
            results = signal_dispatcher.dispatch_signals(
                signals,
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
                account_id="DUP00000",
            )

        self.assertEqual(len(results), 5)
        for r in results:
            self.assertIn("signal", r)
            self.assertIn("success", r)
            self.assertIn("side", r)
            self.assertIn("price", r)

    def test_long_signals_call_execute_buy(self):
        signals = [_make_signal("AAPL")]

        with patch.object(signal_dispatcher, "execute_buy", return_value=True) as mock_buy:
            results = signal_dispatcher.dispatch_signals(
                signals,
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
            )

        mock_buy.assert_called_once()
        self.assertTrue(results[0]["success"])
        self.assertEqual(results[0]["side"], "BUY")

    def test_execute_buy_receives_correct_args(self):
        sig = _make_signal("AMD")
        sig.rationale = "Breakout on earnings"

        with patch.object(signal_dispatcher, "execute_buy", return_value=True) as mock_buy:
            signal_dispatcher.dispatch_signals(
                [sig],
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
            )

        call_kwargs = mock_buy.call_args.kwargs
        self.assertEqual(call_kwargs["symbol"], "AMD")
        self.assertAlmostEqual(call_kwargs["price"], 100.0)
        self.assertAlmostEqual(call_kwargs["atr"], 2.5)
        self.assertEqual(call_kwargs["score"], 35)  # 7.0 * 5 = 35
        self.assertEqual(call_kwargs["reasoning"], "Breakout on earnings")
        self.assertEqual(call_kwargs["signal_scores"]["trend"], 7)

    def test_neutral_signals_not_executed(self):
        signals = [_make_signal("SPY", direction="NEUTRAL")]

        with patch.object(signal_dispatcher, "execute_buy", return_value=True) as mock_buy:
            results = signal_dispatcher.dispatch_signals(
                signals,
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
            )

        mock_buy.assert_not_called()
        self.assertFalse(results[0]["success"])

    def test_failed_execute_buy_returns_success_false(self):
        signals = [_make_signal("BABA")]

        with patch.object(signal_dispatcher, "execute_buy", return_value=False):
            results = signal_dispatcher.dispatch_signals(
                signals,
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
            )

        self.assertFalse(results[0]["success"])

    def test_execute_buy_exception_returns_success_false(self):
        signals = [_make_signal("CRWD")]

        with patch.object(signal_dispatcher, "execute_buy", side_effect=RuntimeError("conn lost")):
            results = signal_dispatcher.dispatch_signals(
                signals,
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
            )

        self.assertFalse(results[0]["success"])

    def test_empty_signal_list_returns_empty_results(self):
        with patch.object(signal_dispatcher, "execute_buy", return_value=True):
            results = signal_dispatcher.dispatch_signals(
                [],
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
            )
        self.assertEqual(results, [])

    def test_mixed_directions(self):
        signals = [
            _make_signal("AAPL", "LONG"),
            _make_signal("TSLA", "NEUTRAL"),
            _make_signal("SPY", "SHORT"),
        ]

        with (
            patch.object(signal_dispatcher, "execute_buy", return_value=True) as mock_buy,
            patch.object(signal_dispatcher, "execute_short", return_value=True) as mock_short,
        ):
            results = signal_dispatcher.dispatch_signals(
                signals,
                ib=self.ib,
                portfolio_value=self.pv,
                regime=self.regime,
            )

        # execute_buy called once (LONG), execute_short called once (SHORT)
        mock_buy.assert_called_once()
        mock_short.assert_called_once()
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0]["success"])  # LONG → execute_buy
        self.assertFalse(results[1]["success"])  # NEUTRAL → skipped
        self.assertTrue(results[2]["success"])  # SHORT → execute_short


if __name__ == "__main__":
    unittest.main()
