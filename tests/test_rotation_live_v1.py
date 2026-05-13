"""
tests/test_rotation_live_v1.py — Unit tests for rotation_live_v1.py

Coverage targets (25 requirements):
  R1  Flag OFF → HYPOTHETICAL, no execute_sell called
  R2  Flag ON, all gates pass → EXIT_OK_AWAITING_NEXT_SCAN
  R3  G2 daily limit exceeded → GATE_BLOCKED
  R4  G3 blocked_score too low → GATE_BLOCKED
  R5  G4 gap too small (book_avg too high) → GATE_BLOCKED
  R6  G4 book_avg unavailable (empty snapshot) → GATE_BLOCKED
  R7  G5 account values stale → GATE_BLOCKED
  R8  G6 no exit candidate below threshold → GATE_BLOCKED
  R9  G6 protected positions (RESERVED, EXITING) not selected
  R10 G6 hold_protected positions not selected
  R11 G7 NLV unavailable → GATE_BLOCKED
  R12 G7 exit notional exceeds max_nlv_pct → GATE_BLOCKED
  R13 G8 price quote stale → GATE_BLOCKED
  R14 G9 spread too wide → GATE_BLOCKED
  R15 execute_sell failure → EXIT_FAILED, daily count not incremented
  R16 daily count incremented only after EXIT_OK
  R17 _book_avg returns mean of entry_score values
  R18 _book_avg falls back to score field when entry_score absent
  R19 _book_avg returns None for empty snapshot
  R20 _select_exit_candidate returns lowest-scoring eligible position
  R21 decisions.jsonl record written for every evaluate() call
  R22 decisions.jsonl record contains all required fields
  R23 no execute_sell imported at module level (no circular import risk)
  R24 flag ON, execute_sell raises exception → EXIT_FAILED logged
  R25 _daily_count_exceeded resets on new UTC day
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(*positions) -> dict:
    """Build an active_trades_snapshot dict from a list of position dicts."""
    return {pos["symbol"]: pos for pos in positions}


def _make_pos(symbol: str, score: float, qty: int = 100, entry: float = 50.0,
              status: str = "OPEN", hold_protected: bool = False,
              entry_score: float | None = None) -> dict:
    pos: dict = {
        "symbol":   symbol,
        "score":    score,
        "qty":      qty,
        "entry":    entry,
        "status":   status,
    }
    if entry_score is not None:
        pos["entry_score"] = entry_score
    if hold_protected:
        pos["hold_protected"] = True
    return pos


def _fresh_module():
    """
    Return a freshly imported rotation_live_v1 module with all daily-counter
    state reset and no lingering patches.
    """
    mod_name = "rotation_live_v1"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    mod = importlib.import_module(mod_name)
    mod._daily_date = ""
    mod._daily_count = 0
    return mod


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

def _stub_config(enabled: bool = False,
                 max_per_day: int = 1,
                 min_blocked_score: int = 75,
                 min_gap: float = 15,
                 exit_score_max: int = 35,
                 max_nlv_pct: float = 0.02) -> dict:
    return {
        "ENABLE_ROTATION_LIVE_V1":         enabled,
        "ROTATION_LIVE_MAX_PER_DAY":       max_per_day,
        "ROTATION_LIVE_MIN_BLOCKED_SCORE": min_blocked_score,
        "ROTATION_LIVE_MIN_GAP_VS_BOOK":   min_gap,
        "ROTATION_LIVE_EXIT_SCORE_MAX":    exit_score_max,
        "ROTATION_LIVE_MAX_NLV_PCT":       max_nlv_pct,
    }


def _stub_bot_state(updated_at: float | None = None, nlv: float = 1_000_000.0):
    bs = types.SimpleNamespace()
    bs.account_values_updated_at = updated_at if updated_at is not None else time.time()
    bs.account_values = {"NetLiquidation": nlv}
    bs.ib = MagicMock()
    return bs


def _stub_quote_cache(fresh: bool = True, spread: float = 0.001):
    """Return a mock QUOTE_CACHE where get() and get_spread_pct() behave as specified."""
    cache = MagicMock()
    if fresh:
        cache.get.return_value = {"bid": 50.0, "ask": 50.05, "spread_pct": spread, "ts": time.time()}
    else:
        cache.get.return_value = {"bid": 50.0, "ask": 50.05, "spread_pct": spread, "ts": time.time() - 9999}
    cache.get_spread_pct.return_value = spread
    return cache


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestFlagOff(unittest.TestCase):
    """R1 — flag OFF → HYPOTHETICAL, no execute_sell."""

    def test_flag_off_hypothetical_no_sell(self, tmp_path=None):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, qty=100, entry=95.0, entry_score=22),
        )
        cfg = _stub_config(enabled=False)
        bs  = _stub_bot_state()
        qc  = _stub_quote_cache(fresh=True, spread=0.001)

        sell_called = []

        with (
            patch.dict("sys.modules", {
                "config":       types.SimpleNamespace(CONFIG=cfg),
                "bot_state":    bs,
                "alpaca_stream": types.SimpleNamespace(QUOTE_CACHE=qc),
                "orders_core":  types.SimpleNamespace(execute_sell=lambda *a, **k: sell_called.append(1) or True),
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate(
                blocked_symbol="NBIS",
                blocked_score=85,
                portfolio_value=1_000_000,
                active_trades_snapshot=snapshot,
            )

        self.assertEqual(len(sell_called), 0)
        self.assertEqual(mock_log.call_count, 1)
        record = mock_log.call_args[0][0]
        self.assertEqual(record["final_status"], "HYPOTHETICAL")
        self.assertEqual(record["blocked_symbol"], "NBIS")


class TestFlagOnAllGatesPass(unittest.TestCase):
    """R2 — flag ON, all gates pass → EXIT_OK_AWAITING_NEXT_SCAN."""

    def test_flag_on_exit_ok(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, qty=100, entry=95.0, entry_score=22),
            _make_pos("AAPL", score=70, qty=50, entry=180.0, entry_score=70),
        )
        cfg = _stub_config(enabled=True, min_blocked_score=75, min_gap=15, exit_score_max=35)
        bs  = _stub_bot_state(nlv=1_000_000.0)
        qc  = _stub_quote_cache(fresh=True, spread=0.001)

        sell_mock = MagicMock(return_value=True)
        oc_stub   = types.SimpleNamespace(execute_sell=sell_mock)

        with (
            patch.dict("sys.modules", {
                "config":       types.SimpleNamespace(CONFIG=cfg),
                "bot_state":    bs,
                "alpaca_stream": types.SimpleNamespace(QUOTE_CACHE=qc),
                "orders_core":  oc_stub,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate(
                blocked_symbol="NBIS",
                blocked_score=85,
                portfolio_value=1_000_000,
                active_trades_snapshot=snapshot,
            )

        sell_mock.assert_called_once()
        record = mock_log.call_args[0][0]
        self.assertEqual(record["final_status"], "EXIT_OK_AWAITING_NEXT_SCAN")
        self.assertEqual(record["exit_symbol"], "XLE")


class TestGate2DailyLimit(unittest.TestCase):
    """R3 — daily limit exceeded → GATE_BLOCKED on G2."""

    def test_daily_limit_blocks(self):
        mod = _fresh_module()
        mod._daily_date = mod._today_utc()
        mod._daily_count = 1  # already at limit

        cfg = _stub_config(enabled=True, max_per_day=1)

        with (
            patch.dict("sys.modules", {"config": types.SimpleNamespace(CONFIG=cfg)}),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("DVA", 80, 1_000_000, _make_snapshot())

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G2")


class TestGate3BlockedScore(unittest.TestCase):
    """R4 — blocked_score < min_blocked_score → GATE_BLOCKED on G3."""

    def test_blocked_score_too_low(self):
        mod = _fresh_module()
        cfg = _stub_config(enabled=True, min_blocked_score=75)

        with (
            patch.dict("sys.modules", {"config": types.SimpleNamespace(CONFIG=cfg)}),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("TSLA", 60, 1_000_000, _make_snapshot())

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G3")


class TestGate4Gap(unittest.TestCase):
    """R5/R6 — gap too small or book_avg unavailable."""

    def test_gap_too_small(self):
        mod = _fresh_module()
        # book_avg = (70 + 68) / 2 = 69 → gap = 85 - 69 = 16, min_gap = 20
        snapshot = _make_snapshot(
            _make_pos("A", score=70, entry_score=70),
            _make_pos("B", score=68, entry_score=68),
        )
        cfg = _stub_config(enabled=True, min_blocked_score=75, min_gap=20)

        with (
            patch.dict("sys.modules", {"config": types.SimpleNamespace(CONFIG=cfg)}),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G4")

    def test_empty_snapshot_book_avg_unavailable(self):
        mod = _fresh_module()
        cfg = _stub_config(enabled=True)

        with (
            patch.dict("sys.modules", {"config": types.SimpleNamespace(CONFIG=cfg)}),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, _make_snapshot())

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G4")


class TestGate5AccountFreshness(unittest.TestCase):
    """R7 — stale account values → GATE_BLOCKED on G5."""

    def test_stale_account_values(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=100, entry=95.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state(updated_at=time.time() - 9999)  # stale

        with (
            patch.dict("sys.modules", {
                "config":    types.SimpleNamespace(CONFIG=cfg),
                "bot_state": bs,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G5")


class TestGate6ExitCandidate(unittest.TestCase):
    """R8/R9/R10 — exit candidate selection gates."""

    def test_no_candidate_below_threshold(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("AAPL", score=50, entry_score=50),  # above exit_score_max=35
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state()

        with (
            patch.dict("sys.modules", {
                "config":    types.SimpleNamespace(CONFIG=cfg),
                "bot_state": bs,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G6")

    def test_reserved_position_skipped(self):
        """R9 — RESERVED status position must not be selected."""
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=20, entry_score=20, status="RESERVED"),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state()

        with (
            patch.dict("sys.modules", {
                "config":    types.SimpleNamespace(CONFIG=cfg),
                "bot_state": bs,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G6")

    def test_exiting_position_skipped(self):
        """R9 — EXITING status position must not be selected."""
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=20, entry_score=20, status="EXITING"),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state()

        with (
            patch.dict("sys.modules", {
                "config":    types.SimpleNamespace(CONFIG=cfg),
                "bot_state": bs,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G6")

    def test_hold_protected_position_skipped(self):
        """R10 — hold_protected position must not be selected."""
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=20, entry_score=20, hold_protected=True),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state()

        with (
            patch.dict("sys.modules", {
                "config":    types.SimpleNamespace(CONFIG=cfg),
                "bot_state": bs,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G6")


class TestGate7Notional(unittest.TestCase):
    """R11/R12 — NLV unavailable or notional too large."""

    def test_nlv_unavailable(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=100, entry=95.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state(nlv=0.0)
        bs.account_values = {"NetLiquidation": None}

        with (
            patch.dict("sys.modules", {
                "config":    types.SimpleNamespace(CONFIG=cfg),
                "bot_state": bs,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G7")

    def test_notional_exceeds_nlv_pct(self):
        """R12 — exit notional > max_nlv_pct × NLV."""
        mod = _fresh_module()
        # 200 shares × $100 = $20,000 notional; NLV = $1M; max_nlv_pct = 0.01 → limit = $10K
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=200, entry=100.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True, max_nlv_pct=0.01)
        bs  = _stub_bot_state(nlv=1_000_000.0)

        with (
            patch.dict("sys.modules", {
                "config":    types.SimpleNamespace(CONFIG=cfg),
                "bot_state": bs,
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G7")


class TestGate8QuoteFreshness(unittest.TestCase):
    """R13 — stale price quote → GATE_BLOCKED on G8."""

    def test_stale_quote_blocks(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=100, entry=95.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state(nlv=1_000_000.0)
        qc  = _stub_quote_cache(fresh=False)

        with (
            patch.dict("sys.modules", {
                "config":        types.SimpleNamespace(CONFIG=cfg),
                "bot_state":     bs,
                "alpaca_stream": types.SimpleNamespace(QUOTE_CACHE=qc),
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G8")


class TestGate9Spread(unittest.TestCase):
    """R14 — spread too wide → GATE_BLOCKED on G9."""

    def test_wide_spread_blocks(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=100, entry=95.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state(nlv=1_000_000.0)
        qc  = _stub_quote_cache(fresh=True, spread=0.05)  # 5% > 1% limit

        with (
            patch.dict("sys.modules", {
                "config":        types.SimpleNamespace(CONFIG=cfg),
                "bot_state":     bs,
                "alpaca_stream": types.SimpleNamespace(QUOTE_CACHE=qc),
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["failed_gate"], "G9")


class TestSellFailure(unittest.TestCase):
    """R15 — execute_sell failure → EXIT_FAILED, daily count NOT incremented."""

    def test_sell_failure_does_not_increment_counter(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=100, entry=95.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state(nlv=1_000_000.0)
        qc  = _stub_quote_cache(fresh=True, spread=0.001)
        sell_mock = MagicMock(return_value=False)  # sell fails

        with (
            patch.dict("sys.modules", {
                "config":        types.SimpleNamespace(CONFIG=cfg),
                "bot_state":     bs,
                "alpaca_stream": types.SimpleNamespace(QUOTE_CACHE=qc),
                "orders_core":   types.SimpleNamespace(execute_sell=sell_mock),
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["final_status"], "EXIT_FAILED")
        self.assertEqual(mod._daily_count, 0)


class TestDailyCounterIncrement(unittest.TestCase):
    """R16 — daily count incremented only after EXIT_OK."""

    def test_daily_count_incremented_on_success(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=100, entry=95.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state(nlv=1_000_000.0)
        qc  = _stub_quote_cache(fresh=True, spread=0.001)
        sell_mock = MagicMock(return_value=True)

        with (
            patch.dict("sys.modules", {
                "config":        types.SimpleNamespace(CONFIG=cfg),
                "bot_state":     bs,
                "alpaca_stream": types.SimpleNamespace(QUOTE_CACHE=qc),
                "orders_core":   types.SimpleNamespace(execute_sell=sell_mock),
            }),
            patch.object(mod, "_log_decision"),
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        self.assertEqual(mod._daily_count, 1)


class TestBookAvg(unittest.TestCase):
    """R17/R18/R19 — _book_avg helper."""

    def setUp(self):
        self.mod = _fresh_module()

    def test_book_avg_from_entry_score(self):
        """R17 — mean of entry_score values."""
        snapshot = _make_snapshot(
            _make_pos("A", score=30, entry_score=30),
            _make_pos("B", score=20, entry_score=20),
            _make_pos("C", score=10, entry_score=10),
        )
        avg = self.mod._book_avg(snapshot)
        self.assertAlmostEqual(avg, 20.0)

    def test_book_avg_falls_back_to_score(self):
        """R18 — falls back to score when entry_score absent."""
        snapshot = _make_snapshot(
            _make_pos("A", score=40),
            _make_pos("B", score=60),
        )
        avg = self.mod._book_avg(snapshot)
        self.assertAlmostEqual(avg, 50.0)

    def test_book_avg_empty_returns_none(self):
        """R19 — returns None for empty snapshot."""
        self.assertIsNone(self.mod._book_avg({}))


class TestSelectExitCandidate(unittest.TestCase):
    """R20 — _select_exit_candidate returns lowest-scoring eligible position."""

    def setUp(self):
        self.mod = _fresh_module()

    def test_returns_lowest_scoring_eligible(self):
        snapshot = _make_snapshot(
            _make_pos("XLE", score=23, entry_score=23),
            _make_pos("XLK", score=26, entry_score=26),
            _make_pos("AAPL", score=65, entry_score=65),
        )
        result = self.mod._select_exit_candidate(snapshot, exit_score_max=35)
        self.assertIsNotNone(result)
        self.assertEqual(result["symbol"], "XLE")

    def test_returns_none_when_all_above_threshold(self):
        snapshot = _make_snapshot(
            _make_pos("AAPL", score=65, entry_score=65),
            _make_pos("NVDA", score=70, entry_score=70),
        )
        result = self.mod._select_exit_candidate(snapshot, exit_score_max=35)
        self.assertIsNone(result)


class TestDecisionLog(unittest.TestCase):
    """R21/R22 — decisions.jsonl written for every call with required fields."""

    REQUIRED_FIELDS = {
        "ts", "blocked_symbol", "blocked_score", "portfolio_value",
        "flag_enabled", "gates_passed", "failed_gate", "failed_reason",
        "exit_symbol", "exit_score", "exit_notional", "book_avg",
        "gap", "nlv", "final_status",
    }

    def test_log_written_on_gate_block(self):
        """R21 — record written even when a gate fails."""
        mod = _fresh_module()
        cfg = _stub_config(enabled=True, min_blocked_score=75)

        records = []
        with (
            patch.dict("sys.modules", {"config": types.SimpleNamespace(CONFIG=cfg)}),
            patch.object(mod, "_log_decision", side_effect=records.append),
        ):
            mod.evaluate("TSLA", 50, 1_000_000, _make_snapshot())

        self.assertEqual(len(records), 1)

    def test_record_has_required_fields(self):
        """R22 — record contains all required fields."""
        mod = _fresh_module()
        cfg = _stub_config(enabled=True)

        records = []
        with (
            patch.dict("sys.modules", {"config": types.SimpleNamespace(CONFIG=cfg)}),
            patch.object(mod, "_log_decision", side_effect=records.append),
        ):
            mod.evaluate("TSLA", 50, 1_000_000, _make_snapshot())

        record = records[0]
        missing = self.REQUIRED_FIELDS - set(record.keys())
        self.assertEqual(missing, set(), f"Record missing fields: {missing}")


class TestNoCircularImport(unittest.TestCase):
    """R23 — execute_sell, bot_state, orders_core NOT imported at module level."""

    def test_no_runtime_modules_at_top_level(self):
        """
        Verify the module source does not import orders_core, bot_state, or
        alpaca_stream at the true module level (zero-indentation import lines).

        Helper functions that contain '    import bot_state' inside their body
        are still lazy imports — they only execute when the function is called.
        The check here only catches lines that start at column 0 (no indentation).
        """
        import re

        mod_path = pathlib.Path(__file__).parent.parent / "rotation_live_v1.py"
        src = mod_path.read_text(encoding="utf-8")

        # Match only unindented (column-0) import lines.
        for name in ("bot_state", "orders_core", "alpaca_stream"):
            # re.MULTILINE: ^ anchors to start of each line
            top_level_import = re.compile(
                rf"^import {re.escape(name)}|^from {re.escape(name)} import",
                re.MULTILINE,
            )
            match = top_level_import.search(src)
            self.assertIsNone(
                match,
                f"Top-level (unindented) import of '{name}' found in rotation_live_v1.py",
            )

        # Also verify the module's own __dict__ doesn't hold these as attributes
        # (i.e., they were never imported at module load time).
        mod = _fresh_module()
        for name in ("bot_state", "orders_core", "alpaca_stream"):
            self.assertNotIn(
                name, mod.__dict__,
                f"'{name}' present in module __dict__ after import (top-level import executed)",
            )


class TestSellException(unittest.TestCase):
    """R24 — execute_sell raises exception → EXIT_FAILED logged."""

    def test_sell_exception_logs_exit_failed(self):
        mod = _fresh_module()
        snapshot = _make_snapshot(
            _make_pos("XLE", score=22, entry_score=22, qty=100, entry=95.0),
            _make_pos("AAPL", score=65, entry_score=65, qty=50, entry=180.0),
        )
        cfg = _stub_config(enabled=True)
        bs  = _stub_bot_state(nlv=1_000_000.0)
        qc  = _stub_quote_cache(fresh=True, spread=0.001)

        def bad_sell(*a, **k):
            raise RuntimeError("IBKR connection lost")

        with (
            patch.dict("sys.modules", {
                "config":        types.SimpleNamespace(CONFIG=cfg),
                "bot_state":     bs,
                "alpaca_stream": types.SimpleNamespace(QUOTE_CACHE=qc),
                "orders_core":   types.SimpleNamespace(execute_sell=bad_sell),
            }),
            patch.object(mod, "_log_decision") as mock_log,
        ):
            mod.evaluate("NBIS", 85, 1_000_000, snapshot)

        record = mock_log.call_args[0][0]
        self.assertEqual(record["final_status"], "EXIT_FAILED")
        self.assertEqual(mod._daily_count, 0)


class TestDailyCounterReset(unittest.TestCase):
    """R25 — _daily_count_exceeded resets on new UTC day."""

    def test_resets_on_new_day(self):
        mod = _fresh_module()
        # Simulate yesterday's count at limit.
        mod._daily_date  = "2026-05-12"
        mod._daily_count = 5
        cfg = _stub_config(enabled=True, max_per_day=1)

        # Today's date is different → counter should reset → not exceeded
        exceeded = mod._daily_count_exceeded(max_per_day=1)
        # After the call the date should be today and count 0.
        self.assertFalse(exceeded)
        today = mod._today_utc()
        self.assertEqual(mod._daily_date, today)
        self.assertEqual(mod._daily_count, 0)


if __name__ == "__main__":
    unittest.main()
