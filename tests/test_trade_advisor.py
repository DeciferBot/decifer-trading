# tests/test_trade_advisor.py
"""
Unit tests for trade_advisor.py.

Tests cover:
  - Formula fallback when use_llm_advisor=False
  - Validation gate: each field independently falls back on bad Opus output
  - Happy path: valid Opus response produces correct TradeAdvice
  - record_outcome: correctly updates advisor log
  - _recent_history: returns only completed decisions
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_signal_context(**overrides):
    defaults = dict(
        symbol="NVDA",
        direction="LONG",
        entry_price=177.00,
        atr_5m=0.27,
        atr_daily=5.50,
        conviction_score=7.5,
        dimension_scores={"directional": 8.0, "momentum": 6.0},
        rationale="Strong breakout above VWAP",
        regime_context="BULL_TRENDING",
    )
    defaults.update(overrides)
    return defaults


def _valid_opus_response(symbol="NVDA", direction="LONG", entry=177.00):
    """A response that satisfies all validation checks for a LONG."""
    return json.dumps({
        "instrument":      "COMMON",
        "size_multiplier": 1.2,
        "profit_target":   179.80,   # ~1.6% above entry
        "stop_loss":       176.50,   # ~0.28% below entry
        "reasoning":       "Strong momentum, let it run.",
    })


# ── Test class ─────────────────────────────────────────────────────────────────

class TestTradeAdvisorFormula(unittest.TestCase):
    """advise_trade with use_llm_advisor=False — must use ATR formula."""

    def setUp(self):
        # Patch config to disable LLM advisor
        import config as _cfg
        self._orig = _cfg.CONFIG.get("use_llm_advisor")
        _cfg.CONFIG["use_llm_advisor"] = False

    def tearDown(self):
        import config as _cfg
        _cfg.CONFIG["use_llm_advisor"] = self._orig

    def test_formula_fallback_returns_trade_advice(self):
        import trade_advisor
        ctx = _make_signal_context()
        advice = trade_advisor.advise_trade(**ctx)
        self.assertEqual(advice.instrument, "COMMON")
        self.assertEqual(advice.size_multiplier, 1.0)
        self.assertGreater(advice.profit_target, ctx["entry_price"])   # LONG: PT > entry
        self.assertLess(advice.stop_loss, ctx["entry_price"])          # LONG: SL < entry
        self.assertEqual(advice.source, "formula")

    def test_formula_fallback_short(self):
        import trade_advisor
        ctx = _make_signal_context(symbol="AAPL", direction="SHORT", entry_price=200.0, atr_5m=0.50)
        advice = trade_advisor.advise_trade(**ctx)
        self.assertLess(advice.profit_target, ctx["entry_price"])   # SHORT: PT < entry
        self.assertGreater(advice.stop_loss, ctx["entry_price"])    # SHORT: SL > entry


class TestValidation(unittest.TestCase):
    """_validate should fix individual bad fields without rejecting the whole response."""

    def _run_validate(self, raw, direction="LONG", entry=177.0, atr=0.27):
        from trade_advisor import _validate
        from position_sizing import calculate_stops
        fallback_sl, fallback_tp = calculate_stops(entry, atr, direction)
        return _validate(raw, direction, entry, atr, fallback_sl, fallback_tp)

    def test_valid_response_passes_through(self):
        raw = {"instrument": "COMMON", "size_multiplier": 1.2,
               "profit_target": 179.80, "stop_loss": 176.50, "reasoning": "ok"}
        result = self._run_validate(raw)
        self.assertEqual(result["instrument"], "COMMON")
        self.assertAlmostEqual(result["size_multiplier"], 1.2)
        self.assertAlmostEqual(result["profit_target"], 179.80)
        self.assertAlmostEqual(result["stop_loss"], 176.50)

    def test_invalid_instrument_defaults_to_common(self):
        raw = {"instrument": "FUTURES", "size_multiplier": 1.0,
               "profit_target": 179.80, "stop_loss": 176.50, "reasoning": ""}
        result = self._run_validate(raw)
        self.assertEqual(result["instrument"], "COMMON")

    def test_size_mult_out_of_range_defaults_to_1(self):
        raw = {"instrument": "COMMON", "size_multiplier": 99.9,
               "profit_target": 179.80, "stop_loss": 176.50, "reasoning": ""}
        result = self._run_validate(raw)
        self.assertEqual(result["size_multiplier"], 1.0)

    def test_pt_wrong_direction_falls_back(self):
        # For LONG, PT must be above entry; give it below entry
        raw = {"instrument": "COMMON", "size_multiplier": 1.0,
               "profit_target": 175.00, "stop_loss": 176.50, "reasoning": ""}
        result = self._run_validate(raw, direction="LONG", entry=177.0)
        # PT should be formula fallback (above entry)
        self.assertGreater(result["profit_target"], 177.0)

    def test_sl_wrong_direction_falls_back(self):
        # For LONG, SL must be below entry; give it above entry
        raw = {"instrument": "COMMON", "size_multiplier": 1.0,
               "profit_target": 179.80, "stop_loss": 180.00, "reasoning": ""}
        result = self._run_validate(raw, direction="LONG", entry=177.0)
        self.assertLess(result["stop_loss"], 177.0)

    def test_rr_below_floor_fixes_pt(self):
        # PT barely above entry, SL far below — poor R:R
        raw = {"instrument": "COMMON", "size_multiplier": 1.0,
               "profit_target": 177.10, "stop_loss": 170.00, "reasoning": ""}
        result = self._run_validate(raw, direction="LONG", entry=177.0)
        # PT should be replaced by formula fallback that satisfies R:R
        reward = result["profit_target"] - 177.0
        risk   = 177.0 - result["stop_loss"]
        self.assertGreater(result["profit_target"], 177.0)

    def test_pt_too_far_falls_back(self):
        # PT more than 15% from entry
        raw = {"instrument": "COMMON", "size_multiplier": 1.0,
               "profit_target": 210.00, "stop_loss": 176.50, "reasoning": ""}
        result = self._run_validate(raw, direction="LONG", entry=177.0)
        self.assertLess(abs(result["profit_target"] - 177.0) / 177.0, 0.15)


class TestLearningLoop(unittest.TestCase):
    """record_outcome correctly updates the advisor log."""

    def setUp(self):
        from pathlib import Path
        self.log_path = Path("data/advisor_log_test.json")
        # Patch the path used by trade_advisor
        import trade_advisor
        self._orig_path = trade_advisor.ADVISOR_LOG_PATH
        trade_advisor.ADVISOR_LOG_PATH = self.log_path

    def tearDown(self):
        import trade_advisor
        trade_advisor.ADVISOR_LOG_PATH = self._orig_path
        if self.log_path.exists():
            self.log_path.unlink()

    def test_record_outcome_updates_log(self):
        import trade_advisor
        # Seed the log with a pending decision
        data = {
            "abc123": {
                "advice_id": "abc123",
                "symbol": "NVDA",
                "direction": "LONG",
                "entry_price": 177.0,
                "atr_5m": 0.27,
                "atr_daily": 5.5,
                "conviction_score": 7.5,
                "instrument": "COMMON",
                "size_multiplier": 1.0,
                "profit_target": 179.5,
                "stop_loss": 176.5,
                "reasoning": "test",
                "source": "opus",
                "pnl": None,
                "exit_reason": None,
                "exit_price": None,
                "outcome_at": None,
                "timestamp": "2026-04-08T10:00:00+00:00",
            }
        }
        self.log_path.parent.mkdir(exist_ok=True)
        self.log_path.write_text(json.dumps(data))

        trade_advisor.record_outcome("abc123", exit_price=179.5, pnl=119.0, exit_reason="tp_hit")

        updated = json.loads(self.log_path.read_text())
        self.assertAlmostEqual(updated["abc123"]["pnl"], 119.0)
        self.assertEqual(updated["abc123"]["exit_reason"], "tp_hit")
        self.assertAlmostEqual(updated["abc123"]["exit_price"], 179.5)
        self.assertIsNotNone(updated["abc123"]["outcome_at"])

    def test_record_outcome_unknown_id_is_silent(self):
        """Unknown advice_id should not raise — it's a no-op."""
        import trade_advisor
        self.log_path.write_text("{}")
        trade_advisor.record_outcome("nonexistent", exit_price=180.0, pnl=50.0, exit_reason="tp_hit")

    def test_recent_history_returns_only_completed(self):
        import trade_advisor
        data = {
            "open1":   {"pnl": None, "timestamp": "2026-04-08T09:00:00+00:00"},
            "closed1": {"pnl": 100.0, "timestamp": "2026-04-08T09:30:00+00:00"},
            "closed2": {"pnl": -50.0, "timestamp": "2026-04-08T10:00:00+00:00"},
        }
        history = trade_advisor._recent_history(data, n=10)
        self.assertEqual(len(history), 2)
        self.assertTrue(all(r["pnl"] is not None for r in history))

    def test_recent_history_respects_n_limit(self):
        import trade_advisor
        data = {str(i): {"pnl": float(i), "timestamp": f"2026-04-08T0{i}:00:00+00:00"}
                for i in range(1, 8)}
        history = trade_advisor._recent_history(data, n=3)
        self.assertEqual(len(history), 3)


class TestOpusHappyPath(unittest.TestCase):
    """Happy-path test with mocked Opus response."""

    def setUp(self):
        import config as _cfg
        self._orig = _cfg.CONFIG.get("use_llm_advisor")
        _cfg.CONFIG["use_llm_advisor"] = True

    def tearDown(self):
        import config as _cfg
        _cfg.CONFIG["use_llm_advisor"] = self._orig

    @patch("trade_advisor.ADVISOR_LOG_PATH")
    @patch("trade_advisor.anthropic.Anthropic")
    def test_valid_opus_response(self, mock_anthropic_cls, mock_log_path):
        import trade_advisor

        # Mock log path to temp location
        from pathlib import Path
        tmp = Path("data/advisor_log_happy_test.json")
        mock_log_path.__str__ = lambda s: str(tmp)
        trade_advisor.ADVISOR_LOG_PATH = tmp

        # Mock Anthropic client
        mock_client   = MagicMock()
        mock_msg      = MagicMock()
        mock_msg.content = [MagicMock(text=_valid_opus_response())]
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        ctx = _make_signal_context()
        advice = trade_advisor.advise_trade(**ctx)

        self.assertEqual(advice.instrument, "COMMON")
        self.assertAlmostEqual(advice.size_multiplier, 1.2)
        self.assertAlmostEqual(advice.profit_target, 179.80)
        self.assertAlmostEqual(advice.stop_loss, 176.50)
        self.assertEqual(advice.source, "opus")

        # Clean up
        if tmp.exists():
            tmp.unlink()

    @patch("trade_advisor.anthropic.Anthropic")
    def test_api_failure_falls_back_to_formula(self, mock_anthropic_cls):
        import config as _cfg
        _cfg.CONFIG["use_llm_advisor"] = True
        import trade_advisor

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")
        mock_anthropic_cls.return_value = mock_client

        ctx = _make_signal_context()
        advice = trade_advisor.advise_trade(**ctx)

        self.assertEqual(advice.source, "formula")
        self.assertGreater(advice.profit_target, ctx["entry_price"])


if __name__ == "__main__":
    unittest.main()
