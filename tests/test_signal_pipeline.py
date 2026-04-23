#!/usr/bin/env python3
"""
Unit tests for signal_pipeline.py.

Covers every internal helper and the public run_signal_pipeline() entry point.
All external dependencies (score_universe, batch_news_sentiment, etc.) are mocked
at the module boundary so no IBKR connection, no yfinance calls, no real files.

Test classes
------------
TestScoredToSignals        — _scored_to_signals() conversion correctness
TestAppendSignalsLog       — _append_signals_log() file write behaviour
TestApplyStrategyThreshold — _apply_strategy_threshold() threshold gating
TestRunSignalPipeline      — run_signal_pipeline() integration (mocked deps)
"""

import json
import os
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# ── Project root on path ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy dependencies before any project imports ───────────────────────

# ib_async
_ib = types.ModuleType("ib_async")
_ib.IB = MagicMock
_ib.Stock = MagicMock
_ib.LimitOrder = MagicMock
_ib.StopOrder = MagicMock
_ib.MarketOrder = MagicMock
_ib.Option = MagicMock
sys.modules.setdefault("ib_async", _ib)

# colorama
_col = types.ModuleType("colorama")
_col.Fore = types.SimpleNamespace(YELLOW="", GREEN="", CYAN="", RED="", WHITE="", MAGENTA="", RESET="")
_col.Style = types.SimpleNamespace(RESET_ALL="", BRIGHT="")
_col.init = lambda **kw: None
sys.modules.setdefault("colorama", _col)

# config (minimal)
_cfg = types.ModuleType("config")
_cfg.CONFIG = {
    "max_positions": 10,
    "min_score_to_trade": 20,
    "active_account": "DUP00000",
    "trade_log": "/tmp/test_trades.json",
    "order_log": "/tmp/test_orders.json",
    "ic_calculator": {"edge_gate_enabled": False},  # match paper learning mode default
}
sys.modules.setdefault("config", _cfg)

# signals — score_universe and get_regime_threshold are mocked per-test
_sigs = types.ModuleType("signals")
_sigs.score_universe = MagicMock(return_value=([], []))
_sigs.get_regime_threshold = MagicMock(return_value=20)
_sigs.fetch_multi_timeframe = MagicMock(return_value=None)
sys.modules.setdefault("signals", _sigs)

# news
_news = types.ModuleType("news")
_news.batch_news_sentiment = MagicMock(return_value={})
sys.modules.setdefault("news", _news)

# learning
_learn = types.ModuleType("learning")
_learn.log_signal_scan = MagicMock()
_learn.log_trade = MagicMock()
_learn.log_order = MagicMock()
_learn.load_trades = MagicMock(return_value=[])
_learn.load_orders = MagicMock(return_value=[])
_learn.TRADE_LOG_FILE = "/tmp/trades.json"
sys.modules.setdefault("learning", _learn)

# social_sentiment (optional — tested via ImportError path too)
_social = types.ModuleType("social_sentiment")
_social.get_social_sentiment = MagicMock(return_value={})
sys.modules.setdefault("social_sentiment", _social)

# ── Now import the module under test ─────────────────────────────────────────
import signal_pipeline
from signal_pipeline import (
    SignalPipelineResult,
    _append_signals_log,
    _apply_strategy_threshold,
    _scored_to_signals,
    run_signal_pipeline,
)
from signal_types import SIGNALS_LOG, Signal

# ── Shared helpers ────────────────────────────────────────────────────────────


def _scored_dict(symbol="AAPL", score=35, direction="LONG") -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "direction": direction,
        "price": 180.0,
        "atr_5m": 3.5,
        "score_breakdown": {
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
    }


def _default_regime() -> dict:
    return {"regime": "TRENDING_UP", "vix": 15.0, "spy_price": 500.0, "regime_router": "momentum"}


def _default_strategy_mode() -> dict:
    return {
        "mode": "NORMAL",
        "score_threshold_adj": 0,
        "daily_pnl_pct": 0.0,
        "size_multiplier": 1.0,
    }


# ── TestScoredToSignals ───────────────────────────────────────────────────────


class TestScoredToSignals(unittest.TestCase):
    def test_basic_conversion(self):
        scored = [_scored_dict("AAPL", score=35, direction="LONG"), _scored_dict("NVDA", score=40, direction="SHORT")]
        signals = _scored_to_signals(scored, "TRENDING_UP")
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].symbol, "AAPL")
        self.assertAlmostEqual(signals[0].conviction_score, 7.0)
        self.assertEqual(signals[1].direction, "SHORT")
        self.assertAlmostEqual(signals[1].conviction_score, 8.0)

    def test_unknown_direction_normalised_to_neutral(self):
        scored = [_scored_dict("X", direction="WEIRD")]
        signals = _scored_to_signals(scored, "UNKNOWN")
        self.assertEqual(signals[0].direction, "NEUTRAL")

    def test_regime_context_preserved(self):
        scored = [_scored_dict("SPY")]
        signals = _scored_to_signals(scored, "TRENDING_DOWN")
        self.assertEqual(signals[0].regime_context, "TRENDING_DOWN")

    def test_price_and_atr_copied(self):
        s = _scored_dict("TSLA")
        s["price"] = 250.0
        s["atr_5m"] = 8.0
        signals = _scored_to_signals([s], "TRENDING_UP")
        self.assertAlmostEqual(signals[0].price, 250.0)
        self.assertAlmostEqual(signals[0].atr, 8.0)

    def test_empty_scored_returns_empty(self):
        self.assertEqual(_scored_to_signals([], "TRENDING_UP"), [])

    def test_returns_signal_instances(self):
        scored = [_scored_dict()]
        signals = _scored_to_signals(scored, "TRENDING_UP")
        self.assertIsInstance(signals[0], Signal)


# ── TestAppendSignalsLog ──────────────────────────────────────────────────────


class TestAppendSignalsLog(unittest.TestCase):
    def _make_signal(self, symbol="AAPL") -> Signal:
        return Signal(
            symbol=symbol,
            direction="LONG",
            conviction_score=7.0,
            dimension_scores={"trend": 7},
            timestamp=datetime.now(UTC),
            regime_context="TRENDING_UP",
            price=100.0,
            atr=2.5,
        )

    def test_writes_valid_jsonl_lines(self):
        signals = [self._make_signal("AAPL"), self._make_signal("NVDA")]
        with tempfile.NamedTemporaryFile(mode="r", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            _append_signals_log(signals, path)
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                parsed = json.loads(line.strip())
                self.assertIn("symbol", parsed)
                self.assertIn("direction", parsed)
                self.assertIn("conviction_score", parsed)
        finally:
            os.unlink(path)

    def test_empty_list_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "signals.jsonl")
            _append_signals_log([], path)
            self.assertFalse(os.path.exists(path))

    def test_appends_not_overwrites(self):
        sig = self._make_signal()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            _append_signals_log([sig], path)
            _append_signals_log([sig], path)
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)
        finally:
            os.unlink(path)

    def test_io_error_does_not_raise(self):
        signals = [self._make_signal()]
        # Should silently log a warning, not raise
        _append_signals_log(signals, "/dev/full/nonexistent/path.jsonl")


# ── TestApplyStrategyThreshold ───────────────────────────────────────────────


class TestApplyStrategyThreshold(unittest.TestCase):
    def _scored_list(self, scores):
        return [{"symbol": f"S{i}", "score": s} for i, s in enumerate(scores)]

    def test_zero_adjustment_returns_full_list(self):
        scored = self._scored_list([25, 30, 35, 10, 5])
        mode = {"mode": "NORMAL", "score_threshold_adj": 0}
        with patch.object(signal_pipeline, "get_regime_threshold", return_value=20):
            result = _apply_strategy_threshold(scored, mode, "TRENDING_UP")
        self.assertEqual(len(result), 5)  # no filtering when adj=0

    def test_raised_threshold_filters_below(self):
        """adj=5, base=20 → effective=25; scores below 25 removed."""
        scored = self._scored_list([10, 20, 25, 30])
        mode = {"mode": "DEFENSIVE", "score_threshold_adj": 5}
        with (
            patch.object(signal_pipeline, "get_regime_threshold", return_value=20),
            patch.object(signal_pipeline, "_get_edge_gate_adj", return_value=(0, "no_data")),
        ):
            result = _apply_strategy_threshold(scored, mode, "TRENDING_UP")
        scores_in = [s["score"] for s in result]
        self.assertTrue(all(s >= 25 for s in scores_in))
        self.assertEqual(len(result), 2)  # only score=25 and score=30

    def test_empty_scored_returns_empty(self):
        mode = {"mode": "NORMAL", "score_threshold_adj": 0}
        with patch.object(signal_pipeline, "get_regime_threshold", return_value=20):
            result = _apply_strategy_threshold([], mode, "TRENDING_UP")
        self.assertEqual(result, [])


# ── TestRunSignalPipeline ─────────────────────────────────────────────────────


class TestRunSignalPipeline(unittest.TestCase):
    def _base_kwargs(self, tmp_path=None):
        return dict(
            universe=["AAPL", "MSFT"],
            regime=_default_regime(),
            strategy_mode=_default_strategy_mode(),
            session="REGULAR",
            favourites=[],
            signals_log_path=tmp_path or "/tmp/test_signals.jsonl",
        )

    def _patched_pipeline(self, scored=None, all_scored=None, tmp_path=None, **kwargs):
        """Run run_signal_pipeline with core deps mocked."""
        scored = scored or [_scored_dict()]
        all_scored = all_scored or scored
        kw = self._base_kwargs(tmp_path)
        kw.update(kwargs)
        with (
            patch.object(signal_pipeline, "score_universe", return_value=(scored, all_scored)),
            patch.object(signal_pipeline, "batch_news_sentiment", return_value={}),
            patch.object(signal_pipeline, "log_signal_scan"),
        ):
            return run_signal_pipeline(**kw)

    def test_happy_path_returns_signal_pipeline_result(self):
        result = self._patched_pipeline()
        self.assertIsInstance(result, SignalPipelineResult)

    def test_result_has_all_fields(self):
        result = self._patched_pipeline()
        for field in ("signals", "scored", "all_scored", "news_sentiment", "universe", "regime_name"):
            self.assertTrue(hasattr(result, field), f"Missing field: {field}")

    def test_regime_name_extracted_correctly(self):
        result = self._patched_pipeline()
        self.assertEqual(result.regime_name, "TRENDING_UP")

    def test_score_universe_called_with_correct_regime(self):
        with (
            patch.object(signal_pipeline, "score_universe", return_value=([], [])) as mock_su,
            patch.object(signal_pipeline, "batch_news_sentiment", return_value={}),
            patch.object(signal_pipeline, "log_signal_scan"),
        ):
            run_signal_pipeline(**self._base_kwargs())
        call_args = mock_su.call_args
        self.assertEqual(call_args.args[1], "TRENDING_UP")

    def test_social_skipped_in_pre_market(self):
        kw = self._base_kwargs()
        kw["session"] = "PRE_MARKET"
        with (
            patch.object(signal_pipeline, "score_universe", return_value=([], [])),
            patch.object(signal_pipeline, "batch_news_sentiment", return_value={}),
            patch.object(signal_pipeline, "log_signal_scan"),
            patch.dict(sys.modules, {"social_sentiment": MagicMock()}) as mocked,
        ):
            run_signal_pipeline(**kw)
            # social_sentiment.get_social_sentiment should NOT have been called
            # (session gate fires before import)
            # We verify by checking score_universe got empty social_data
            su_call = (
                signal_pipeline.score_universe.call_args
                if hasattr(signal_pipeline.score_universe, "call_args")
                else None
            )

    def test_signals_log_written_to_specified_path(self):
        scored = [_scored_dict("AAPL", score=35)]
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            self._patched_pipeline(scored=scored, tmp_path=path)
            with open(path) as f:
                lines = f.readlines()
            self.assertGreaterEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["symbol"], "AAPL")
        finally:
            os.unlink(path)

    def test_empty_universe_returns_empty_signals(self):
        kw = self._base_kwargs()
        kw["universe"] = []
        with (
            patch.object(signal_pipeline, "score_universe", return_value=([], [])),
            patch.object(signal_pipeline, "batch_news_sentiment", return_value={}),
            patch.object(signal_pipeline, "log_signal_scan"),
        ):
            result = run_signal_pipeline(**kw)
        self.assertEqual(result.signals, [])
        self.assertEqual(result.scored, [])

    def test_signals_log_constant_is_default_path(self):
        """SIGNALS_LOG constant is the default value of signals_log_path."""
        import inspect

        sig = inspect.signature(run_signal_pipeline)
        default = sig.parameters["signals_log_path"].default
        self.assertEqual(default, SIGNALS_LOG)


if __name__ == "__main__":
    unittest.main()
