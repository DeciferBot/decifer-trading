# tests/test_learning.py
# Tests for learning.py — trade logging, performance tracking, capital management

import os
import sys
import json
import math
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy dependencies BEFORE importing Decifer modules ──────────────
import types

# Stub anthropic
anthropicmod = types.ModuleType("anthropic")
class _FakeAnthropic:
    def __init__(self, api_key=None):
        pass
    class messages:
        @staticmethod
        def create(**kwargs):
            m = MagicMock()
            m.content = [MagicMock(text="Weekly review text.")]
            return m
anthropicmod.Anthropic = _FakeAnthropic
sys.modules.setdefault("anthropic", anthropicmod)

# Stub ib_async
ib_async_mod = types.ModuleType("ib_async")
ib_async_mod.IB = MagicMock
sys.modules.setdefault("ib_async", ib_async_mod)

# Stub config
configmod = types.ModuleType("config")
configmod.CONFIG = {
    "anthropic_api_key": "test-key",
    "claude_model": "claude-3-5-sonnet-20241022",
    "starting_capital": 100_000,
    "trade_log": "/tmp/test_trades.json",
    "order_log": "/tmp/test_orders.json",
    "log_file": "/tmp/test_decifer.log",
}
sys.modules.setdefault("config", configmod)

# Evict any hollow stub that test_bot.py may have planted for 'learning'
sys.modules.pop("learning", None)

import learning


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _clear_files():
    for f in ["/tmp/test_trades.json", "/tmp/test_orders.json"]:
        if os.path.exists(f):
            os.remove(f)


def _make_trade(symbol="AAPL", action="OPEN", pnl=None, exit_price=None,
                open_time=None, qty=10, entry=150.0):
    t = {
        "symbol": symbol,
        "action": action,
        "direction": "LONG",
        "qty": qty,
        "entry": entry,
        "score": 7.5,
        "reasoning": "Agents agreed 5/6",
        "sl": 145.0,
        "tp": 160.0,
    }
    if pnl is not None:
        t["pnl"] = pnl
    if exit_price is not None:
        t["exit_price"] = exit_price
    if open_time is not None:
        t["open_time"] = open_time
    return t


# ────────────────────────────────────────────────────────────────────────────
# Capital base tests
# ────────────────────────────────────────────────────────────────────────────

class TestCapitalBase:
    """Tests for capital tracking functions."""

    def setup_method(self):
        """Point CAPITAL_FILE to a temp file and fix starting_capital to test value."""
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.remove(self.tmp.name)  # start without file
        self._orig = learning.CAPITAL_FILE
        learning.CAPITAL_FILE = self.tmp.name
        # The real config may have a different starting_capital (e.g. 1_000_000).
        # Override it in learning's CONFIG to the value this test suite uses.
        self._orig_capital = learning.CONFIG.get("starting_capital")
        learning.CONFIG["starting_capital"] = 100_000

    def teardown_method(self):
        learning.CAPITAL_FILE = self._orig
        # Restore original starting_capital
        if self._orig_capital is not None:
            learning.CONFIG["starting_capital"] = self._orig_capital
        else:
            learning.CONFIG.pop("starting_capital", None)
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_load_capital_base_defaults_when_no_file(self):
        """load_capital_base returns config default when file is absent."""
        result = learning.load_capital_base()
        assert result["starting_capital"] == 100_000
        assert result["adjustments"] == []

    def test_get_effective_capital_no_adjustments(self):
        """get_effective_capital returns starting capital when there are no adjustments."""
        cap = learning.get_effective_capital()
        assert cap == 100_000

    def test_record_capital_adjustment_deposit(self):
        """Depositing increases effective capital."""
        learning.record_capital_adjustment(5_000, "deposit")
        cap = learning.get_effective_capital()
        assert cap == 105_000

    def test_record_capital_adjustment_withdrawal(self):
        """Withdrawing decreases effective capital."""
        learning.record_capital_adjustment(-20_000, "withdrawal")
        cap = learning.get_effective_capital()
        assert cap == 80_000

    def test_multiple_adjustments_accumulate(self):
        """Multiple adjustments are summed correctly."""
        learning.record_capital_adjustment(10_000, "deposit 1")
        learning.record_capital_adjustment(-3_000, "fee")
        learning.record_capital_adjustment(5_000, "deposit 2")
        cap = learning.get_effective_capital()
        assert cap == 100_000 + 10_000 - 3_000 + 5_000

    def test_capital_file_persists_adjustments(self):
        """Adjustments survive a reload from disk."""
        learning.record_capital_adjustment(7_500, "bonus")
        # Re-load from disk
        data = learning.load_capital_base()
        assert len(data["adjustments"]) == 1
        assert data["adjustments"][0]["amount"] == 7_500
        assert data["adjustments"][0]["note"] == "bonus"


# ────────────────────────────────────────────────────────────────────────────
# Order logging tests
# ────────────────────────────────────────────────────────────────────────────

class TestOrderLogging:
    """Tests for log_order, load_orders, update_order_status."""

    def setup_method(self):
        _clear_files()
        self._orig = learning.ORDER_LOG_FILE
        learning.ORDER_LOG_FILE = "/tmp/test_orders.json"

    def teardown_method(self):
        learning.ORDER_LOG_FILE = self._orig
        _clear_files()

    def _order(self, symbol="AAPL", side="BUY", qty=10, price=150.0,
                order_id=1, status="SUBMITTED", instrument="stock"):
        return {
            "symbol": symbol, "side": side, "qty": qty, "price": price,
            "order_id": order_id, "status": status,
            "order_type": "LMT", "instrument": instrument,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def test_log_order_creates_record(self):
        """log_order writes a record to disk."""
        rec = self._order(order_id=42)
        learning.log_order(rec)
        orders = learning.load_orders()
        assert len(orders) == 1
        assert orders[0]["symbol"] == "AAPL"
        assert orders[0]["order_id"] == 42

    def test_log_order_dedup_by_order_id(self):
        """Logging the same order_id twice updates, not duplicates."""
        rec = self._order(order_id=99, status="SUBMITTED")
        learning.log_order(rec)
        rec2 = self._order(order_id=99, status="FILLED", price=151.0)
        learning.log_order(rec2)
        orders = learning.load_orders()
        assert len(orders) == 1
        assert orders[0]["status"] == "FILLED"

    def test_log_order_dedup_zero_order_id(self):
        """Orders with id=0 dedup on symbol+side+qty+price+instrument."""
        rec = self._order(order_id=0, status="SUBMITTED")
        learning.log_order(rec)
        rec2 = self._order(order_id=0, status="SUBMITTED")
        learning.log_order(rec2)
        orders = learning.load_orders()
        assert len(orders) == 1

    def test_log_order_rejects_nan_price(self):
        """NaN price is sanitized to 0 before logging."""
        rec = self._order(order_id=7, price=float("nan"))
        learning.log_order(rec)
        orders = learning.load_orders()
        assert orders[0]["price"] == 0

    def test_log_order_rejects_inf_price(self):
        """Infinite price is sanitized to 0 before logging."""
        rec = self._order(order_id=8, price=float("inf"))
        learning.log_order(rec)
        orders = learning.load_orders()
        assert orders[0]["price"] == 0

    def test_update_order_status_sets_field(self):
        """update_order_status changes the status of an existing order."""
        learning.log_order(self._order(order_id=55))
        learning.update_order_status(55, "FILLED", fill_price=152.0, filled_qty=10)
        orders = learning.load_orders()
        assert orders[0]["status"] == "FILLED"
        assert orders[0]["fill_price"] == 152.0
        assert orders[0]["filled_qty"] == 10

    def test_update_order_status_missing_order_is_noop(self):
        """update_order_status on unknown id does not crash."""
        learning.update_order_status(9999, "FILLED")  # should not raise
        orders = learning.load_orders()
        assert len(orders) == 0

    def test_load_orders_returns_empty_when_no_file(self):
        """load_orders returns [] when file does not exist."""
        orders = learning.load_orders()
        assert orders == []

    def test_multiple_different_orders_stored(self):
        """Different order_ids are stored as separate records."""
        for i in range(1, 4):
            learning.log_order(self._order(order_id=i, symbol=f"SYM{i}"))
        orders = learning.load_orders()
        assert len(orders) == 3
        symbols = {o["symbol"] for o in orders}
        assert symbols == {"SYM1", "SYM2", "SYM3"}


# ────────────────────────────────────────────────────────────────────────────
# Trade logging tests
# ────────────────────────────────────────────────────────────────────────────

class TestTradeLogging:
    """Tests for log_trade and load_trades."""

    def setup_method(self):
        _clear_files()
        self._orig_tl = learning.TRADE_LOG_FILE
        learning.TRADE_LOG_FILE = "/tmp/test_trades.json"

    def teardown_method(self):
        learning.TRADE_LOG_FILE = self._orig_tl
        _clear_files()

    def _regime(self):
        return {"regime": "BULL_TRENDING", "vix": 15.0}

    def _agents(self):
        return {"technical": "bullish", "macro": "neutral",
                "opportunity": "buy", "devils": "risks", "risk": "approved"}

    def test_log_trade_open_creates_record(self):
        """log_trade OPEN creates a persisted record."""
        trade = _make_trade("TSLA", action="OPEN")
        learning.log_trade(trade, self._agents(), self._regime(), "OPEN")
        trades = learning.load_trades()
        assert len(trades) == 1
        assert trades[0]["symbol"] == "TSLA"
        assert trades[0]["action"] == "OPEN"

    def test_log_trade_close_includes_pnl(self):
        """log_trade CLOSE stores P&L and exit price."""
        trade = _make_trade("AAPL", action="CLOSE", pnl=500.0, exit_price=155.0)
        outcome = {"pnl": 500.0, "exit_price": 155.0, "reason": "TP hit"}
        learning.log_trade(trade, self._agents(), self._regime(), "CLOSE", outcome)
        trades = learning.load_trades()
        assert len(trades) == 1
        assert trades[0]["pnl"] == 500.0
        assert trades[0]["exit_price"] == 155.0

    def test_log_trade_duplicate_open_skipped(self):
        """Duplicate OPEN for same symbol within 30 min is not stored."""
        trade = _make_trade("MSFT", action="OPEN")
        learning.log_trade(trade, self._agents(), self._regime(), "OPEN")
        learning.log_trade(trade, self._agents(), self._regime(), "OPEN")  # duplicate
        trades = learning.load_trades()
        assert len(trades) == 1

    def test_log_trade_duplicate_close_keeps_best_pnl(self):
        """Duplicate CLOSE keeps the record with actual P&L over zero P&L."""
        trade_zero = _make_trade("GOOG", action="CLOSE", pnl=0.0, exit_price=130.0)
        outcome_zero = {"pnl": 0.0, "exit_price": 130.0, "reason": "partial"}
        learning.log_trade(trade_zero, self._agents(), self._regime(), "CLOSE", outcome_zero)

        trade_real = _make_trade("GOOG", action="CLOSE", pnl=350.0, exit_price=133.5)
        outcome_real = {"pnl": 350.0, "exit_price": 133.5, "reason": "TP"}
        learning.log_trade(trade_real, self._agents(), self._regime(), "CLOSE", outcome_real)

        trades = learning.load_trades()
        assert len(trades) == 1
        assert trades[0]["pnl"] == 350.0

    def test_log_trade_agents_truncated_at_500_chars(self):
        """Agent output is stored and truncated to 500 chars."""
        agents = {"technical": "X" * 600, "macro": "",
                  "opportunity": "", "devils": "", "risk": ""}
        trade = _make_trade("AMD", action="OPEN")
        learning.log_trade(trade, agents, self._regime(), "OPEN")
        trades = learning.load_trades()
        assert len(trades[0]["agents"]["technical"]) == 500

    def test_load_trades_returns_empty_when_no_file(self):
        """load_trades returns [] when file does not exist."""
        result = learning.load_trades()
        assert result == []

    def test_log_trade_hold_minutes_calculated_on_close(self):
        """hold_minutes is populated on CLOSE when open_time is present."""
        open_time = "2024-01-15T10:00:00+00:00"
        trade = _make_trade("NVDA", action="CLOSE", open_time=open_time)
        outcome = {"pnl": 100.0, "exit_price": 155.0, "reason": "TP"}
        learning.log_trade(trade, self._agents(), self._regime(), "CLOSE", outcome)
        trades = learning.load_trades()
        assert trades[0]["hold_minutes"] is not None
        assert trades[0]["hold_minutes"] > 0


# ────────────────────────────────────────────────────────────────────────────
# Signal scan logging tests
# ────────────────────────────────────────────────────────────────────────────

class TestSignalScanLogging:
    """Tests for log_signal_scan."""

    _TMP = "/tmp/test_signals_log.jsonl"

    def setup_method(self):
        self._orig = learning.SIGNALS_LOG_FILE
        learning.SIGNALS_LOG_FILE = self._TMP
        if os.path.exists(self._TMP):
            os.remove(self._TMP)

    def teardown_method(self):
        learning.SIGNALS_LOG_FILE = self._orig
        if os.path.exists(self._TMP):
            os.remove(self._TMP)

    def _regime(self):
        return {"regime": "BEAR_TRENDING", "vix": 22.5}

    def _sig(self, symbol="AAPL", score=35, price=150.0):
        return {
            "symbol": symbol,
            "score": score,
            "price": price,
            "score_breakdown": {
                "trend": 5, "momentum": 4, "squeeze": 3, "flow": 4,
                "breakout": 3, "mtf": 4, "news": 5, "social": 3, "reversion": 4,
            },
            "disabled_dimensions": [],
        }

    def test_writes_one_line_per_symbol(self):
        """Each symbol in scored produces one JSONL line."""
        scored = [self._sig("AAPL"), self._sig("TSLA", score=40)]
        learning.log_signal_scan(scored, self._regime())
        with open(self._TMP) as f:
            lines = [l for l in f.read().splitlines() if l]
        assert len(lines) == 2
        records = [json.loads(l) for l in lines]
        assert records[0]["symbol"] == "AAPL"
        assert records[1]["symbol"] == "TSLA"

    def test_record_contains_required_fields(self):
        """Each record has ts, scan_id, symbol, score, price, regime, vix, score_breakdown."""
        learning.log_signal_scan([self._sig()], self._regime())
        with open(self._TMP) as f:
            record = json.loads(f.readline())
        for field in ("ts", "scan_id", "symbol", "score", "price", "regime", "vix", "score_breakdown"):
            assert field in record, f"missing field: {field}"

    def test_score_breakdown_preserved(self):
        """All 9 dimension scores are present in score_breakdown."""
        learning.log_signal_scan([self._sig()], self._regime())
        with open(self._TMP) as f:
            record = json.loads(f.readline())
        dims = record["score_breakdown"]
        for dim in ("trend", "momentum", "squeeze", "flow", "breakout", "mtf", "news", "social", "reversion"):
            assert dim in dims, f"missing dimension: {dim}"

    def test_appends_across_multiple_scans(self):
        """Two scan calls append to the same file (not overwrite)."""
        learning.log_signal_scan([self._sig("AAPL")], self._regime())
        learning.log_signal_scan([self._sig("NVDA", score=42)], self._regime())
        with open(self._TMP) as f:
            lines = [l for l in f.read().splitlines() if l]
        assert len(lines) == 2
        syms = [json.loads(l)["symbol"] for l in lines]
        assert "AAPL" in syms and "NVDA" in syms

    def test_same_scan_id_per_cycle(self):
        """All symbols in one call share the same scan_id."""
        scored = [self._sig("AAPL"), self._sig("TSLA"), self._sig("NVDA")]
        learning.log_signal_scan(scored, self._regime())
        with open(self._TMP) as f:
            records = [json.loads(l) for l in f.read().splitlines() if l]
        scan_ids = {r["scan_id"] for r in records}
        assert len(scan_ids) == 1

    def test_empty_scored_writes_nothing(self):
        """Empty scored list produces no output."""
        learning.log_signal_scan([], self._regime())
        assert not os.path.exists(self._TMP)

    def test_missing_score_breakdown_writes_empty_dict(self):
        """Symbol with no score_breakdown writes {} rather than crashing."""
        sig = {"symbol": "XYZ", "score": 30, "price": 10.0}
        learning.log_signal_scan([sig], self._regime())
        with open(self._TMP) as f:
            record = json.loads(f.readline())
        assert record["score_breakdown"] == {}


# ────────────────────────────────────────────────────────────────────────────
# Performance summary tests
# ────────────────────────────────────────────────────────────────────────────

class TestPerformanceSummary:
    """Tests for get_performance_summary."""

    def _closed_trade(self, pnl, entry=100.0, exit_p=None):
        return {
            "pnl": pnl,
            "entry_price": entry,
            "exit_price": exit_p if exit_p is not None else (entry + pnl),
        }

    def test_empty_trades_returns_zeros(self):
        """Empty trade list produces all-zero summary."""
        result = learning.get_performance_summary([])
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0
        assert result["total_pnl"] == 0

    def test_all_wins(self):
        """All winning trades produce 100% win rate."""
        trades = [self._closed_trade(100), self._closed_trade(200), self._closed_trade(50)]
        result = learning.get_performance_summary(trades)
        assert result["total_trades"] == 3
        assert result["wins"] == 3
        assert result["losses"] == 0
        assert result["win_rate"] == 100.0
        assert result["total_pnl"] == 350.0

    def test_all_losses(self):
        """All losing trades produce 0% win rate and negative P&L."""
        trades = [self._closed_trade(-100), self._closed_trade(-200)]
        result = learning.get_performance_summary(trades)
        assert result["wins"] == 0
        assert result["losses"] == 2
        assert result["win_rate"] == 0.0
        assert result["total_pnl"] == -300.0

    def test_mixed_trades_win_rate(self):
        """3 wins, 2 losses → 60% win rate."""
        trades = [
            self._closed_trade(100),
            self._closed_trade(200),
            self._closed_trade(150),
            self._closed_trade(-80),
            self._closed_trade(-120),
        ]
        result = learning.get_performance_summary(trades)
        assert result["total_trades"] == 5
        assert result["wins"] == 3
        assert result["losses"] == 2
        assert result["win_rate"] == 60.0

    def test_profit_factor_calculation(self):
        """Profit factor = gross_profit / abs(gross_loss)."""
        trades = [
            self._closed_trade(300),
            self._closed_trade(-100),
        ]
        result = learning.get_performance_summary(trades)
        assert result["profit_factor"] == 3.0

    def test_best_and_worst_trade(self):
        """best_trade and worst_trade pick extremes correctly."""
        trades = [
            self._closed_trade(500),
            self._closed_trade(100),
            self._closed_trade(-300),
        ]
        result = learning.get_performance_summary(trades)
        assert result["best_trade"] == 500.0
        assert result["worst_trade"] == -300.0

    def test_open_trades_excluded(self):
        """Trades without exit_price/pnl are excluded from summary."""
        trades = [
            self._closed_trade(100),
            {"pnl": None, "exit_price": None, "entry_price": 100},  # open
            {"pnl": 50},                                              # no exit_price
        ]
        result = learning.get_performance_summary(trades)
        assert result["total_trades"] == 1

    def test_expectancy_is_positive_on_winning_book(self):
        """Positive expectancy when avg_win > avg_loss (scaled by win rate)."""
        trades = [
            self._closed_trade(200),
            self._closed_trade(200),
            self._closed_trade(-50),
        ]
        result = learning.get_performance_summary(trades)
        assert result["expectancy"] > 0

    def test_avg_win_avg_loss_computed(self):
        """avg_win and avg_loss are averages of their respective groups."""
        trades = [
            self._closed_trade(100),
            self._closed_trade(300),
            self._closed_trade(-200),
            self._closed_trade(-400),
        ]
        result = learning.get_performance_summary(trades)
        assert result["avg_win"] == 200.0
        assert result["avg_loss"] == -300.0


# ────────────────────────────────────────────────────────────────────────────
# Weekly review test
# ────────────────────────────────────────────────────────────────────────────

class TestWeeklyReview:
    """Tests for run_weekly_review."""

    def setup_method(self):
        _clear_files()
        self._orig_tl = learning.TRADE_LOG_FILE
        learning.TRADE_LOG_FILE = "/tmp/test_trades.json"

    def teardown_method(self):
        learning.TRADE_LOG_FILE = self._orig_tl
        _clear_files()
        try:
            if os.path.exists("weekly_review.txt"):
                os.remove("weekly_review.txt")
        except (PermissionError, OSError):
            pass  # workspace mount may not allow deletion; non-fatal

    def test_weekly_review_no_trades_returns_message(self):
        """run_weekly_review returns informative string when no trades exist."""
        result = learning.run_weekly_review()
        assert isinstance(result, str)
        assert "No trades" in result

    def test_weekly_review_no_recent_trades_returns_message(self):
        """run_weekly_review returns message when trades exist but none in last 7 days."""
        # Write old trade
        old_trade = {
            "timestamp": "2020-01-01T00:00:00+00:00",
            "symbol": "AAPL",
            "action": "CLOSE",
            "pnl": 100,
            "exit_price": 155,
            "direction": "LONG",
            "regime": "BULL_TRENDING",
            "reasoning": "test",
        }
        with open("/tmp/test_trades.json", "w") as f:
            json.dump([old_trade], f)
        result = learning.run_weekly_review()
        assert "No trades in the last 7 days" in result

    def test_weekly_review_calls_claude_and_saves_file(self):
        """run_weekly_review calls Claude and writes weekly_review.txt."""
        from datetime import timedelta

        recent_ts = datetime.now(timezone.utc).isoformat()
        trade = {
            "timestamp": recent_ts,
            "symbol": "TSLA",
            "action": "CLOSE",
            "pnl": 250.0,
            "exit_price": 260.0,
            "entry_price": 250.0,
            "direction": "LONG",
            "regime": "BULL_TRENDING",
            "reasoning": "good setup",
        }
        with open("/tmp/test_trades.json", "w") as f:
            json.dump([trade], f)

        # Patch anthropic.Anthropic
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="GREAT WEEK: everything worked.")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("learning.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = fake_client
            result = learning.run_weekly_review()

        assert "GREAT WEEK" in result
        assert os.path.exists("weekly_review.txt")
        with open("weekly_review.txt") as f:
            content = f.read()
        assert "WEEKLY REVIEW" in content
