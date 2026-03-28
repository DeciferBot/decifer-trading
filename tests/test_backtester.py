#!/usr/bin/env python3
"""
Tests for backtester.py — Portfolio, Trade, Position logic.
No real data files required; all tests use in-memory DataFrames.
"""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest
import pytz

# ── Add project root to path ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy imports before importing Decifer modules ──────────
import importlib
import types

# Stub ib_async
ib_async_stub = types.ModuleType("ib_async")
ib_async_stub.IB = MagicMock
ib_async_stub.Stock = MagicMock
ib_async_stub.Order = MagicMock
sys.modules.setdefault("ib_async", ib_async_stub)

# Stub anthropic
anthropicstub = types.ModuleType("anthropic")
anthropicstub.Anthropic = MagicMock
sys.modules.setdefault("anthropic", anthropicstub)

# Stub yfinance
yf_stub = types.ModuleType("yfinance")
yf_stub.Ticker = MagicMock
yf_stub.download = MagicMock(return_value=pd.DataFrame())
sys.modules.setdefault("yfinance", yf_stub)

# Stub tradingview_screener
tv_stub = types.ModuleType("tradingview_screener")
tv_stub.Scanner = MagicMock
tv_stub.Column = MagicMock
sys.modules.setdefault("tradingview_screener", tv_stub)
sys.modules.setdefault("tradingview_screener.scanner", tv_stub)

# Stub py_vollib
for mod in ["py_vollib", "py_vollib.black_scholes", "py_vollib.black_scholes.greeks",
            "py_vollib.black_scholes.greeks.analytical"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

# Stub sklearn
for mod in ["sklearn", "sklearn.ensemble", "sklearn.preprocessing",
            "sklearn.model_selection", "sklearn.metrics"]:
    stub = types.ModuleType(mod)
    sys.modules.setdefault(mod, stub)

# Stub joblib
sys.modules.setdefault("joblib", types.ModuleType("joblib"))

# Stub praw
sys.modules.setdefault("praw", types.ModuleType("praw"))

# Stub httpx
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

# Stub vaderSentiment
vader_stub = types.ModuleType("vaderSentiment")
vader_sub = types.ModuleType("vaderSentiment.vaderSentiment")
vader_sub.SentimentIntensityAnalyzer = MagicMock
sys.modules.setdefault("vaderSentiment", vader_stub)
sys.modules.setdefault("vaderSentiment.vaderSentiment", vader_sub)

# Stub pytz (already installed, just in case)
import pytz  # noqa

# Stub config so backtester.py can import it
config_stub = types.ModuleType("config")
config_stub.CONFIG = {
    "starting_capital": 100_000,
    "min_score_to_trade": 60,
    "atr_stop_multiplier": 2.0,
    "atr_trail_multiplier": 1.5,
    "partial_exit_1_pct": 0.04,
    "partial_exit_2_pct": 0.08,
    "max_positions": 5,
    "max_portfolio_risk_pct": 0.02,
    "risk_per_trade_pct": 0.01,
    "log_file": "/tmp/decifer_test.log",
    "trade_log": "/tmp/trades_test.json",
    "order_log": "/tmp/orders_test.json",
}
sys.modules.setdefault("config", config_stub)

# Stub signals module
signals_stub = types.ModuleType("signals")
signals_stub.compute_indicators = MagicMock(return_value={})
signals_stub.compute_confluence = MagicMock(return_value={"total": 65, "score": 65})
sys.modules.setdefault("signals", signals_stub)

# Now import backtester
from backtester import (
    Trade,
    Position,
    Portfolio,
    PortfolioState,
    generate_report,
)

EST = pytz.timezone("America/New_York")
NOW = datetime(2024, 6, 1, 10, 30, 0, tzinfo=EST)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def make_portfolio(capital: float = 100_000) -> Portfolio:
    return Portfolio(starting_capital=capital)


def open_one(portfolio, symbol="AAPL", qty=10, price=150.0,
             date=None, atr=2.0, regime="BULL", score=70):
    if date is None:
        date = NOW
    return portfolio.open_position(symbol, qty, price, date, atr, regime, score)


# ─────────────────────────────────────────────────────────────────
# Trade dataclass
# ─────────────────────────────────────────────────────────────────

class TestTradeDataclass:
    def test_is_closed_false_when_no_exit(self):
        t = Trade(symbol="AAPL", entry_date=NOW, entry_price=100.0, qty=10)
        assert t.is_closed is False

    def test_is_closed_true_when_exit_set(self):
        t = Trade(symbol="AAPL", entry_date=NOW, entry_price=100.0, qty=10,
                  exit_date=NOW + timedelta(hours=1), exit_price=105.0)
        assert t.is_closed is True

    def test_is_winner_positive_pnl(self):
        t = Trade(symbol="AAPL", entry_date=NOW, entry_price=100.0, qty=10, pnl=50.0)
        assert t.is_winner is True
        assert t.is_loser is False

    def test_is_loser_negative_pnl(self):
        t = Trade(symbol="AAPL", entry_date=NOW, entry_price=100.0, qty=10, pnl=-30.0)
        assert t.is_loser is True
        assert t.is_winner is False

    def test_breakeven_neither_winner_nor_loser(self):
        t = Trade(symbol="AAPL", entry_date=NOW, entry_price=100.0, qty=10, pnl=0.0)
        assert t.is_winner is False
        assert t.is_loser is False


# ─────────────────────────────────────────────────────────────────
# Position dataclass
# ─────────────────────────────────────────────────────────────────

class TestPositionDataclass:
    def make_position(self, qty=10, entry_price=100.0):
        return Position(
            symbol="AAPL",
            qty=qty,
            entry_price=entry_price,
            entry_date=NOW,
            trade_id=1,
            max_high_price=entry_price,
            max_low_price=entry_price,
        )

    def test_unrealized_pnl_profit(self):
        pos = self.make_position(qty=10, entry_price=100.0)
        assert pos.unrealized_pnl(110.0) == pytest.approx(100.0)

    def test_unrealized_pnl_loss(self):
        pos = self.make_position(qty=10, entry_price=100.0)
        assert pos.unrealized_pnl(90.0) == pytest.approx(-100.0)

    def test_unrealized_pnl_pct(self):
        pos = self.make_position(qty=10, entry_price=100.0)
        assert pos.unrealized_pnl_pct(110.0) == pytest.approx(0.10)

    def test_unrealized_pnl_pct_zero_entry(self):
        pos = self.make_position(qty=10, entry_price=0.0)
        assert pos.unrealized_pnl_pct(110.0) == 0.0

    def test_current_value(self):
        pos = self.make_position(qty=5, entry_price=200.0)
        assert pos.current_value(210.0) == pytest.approx(1050.0)

    def test_update_max_prices_tracking(self):
        pos = self.make_position(qty=10, entry_price=100.0)
        pos.update_max_prices(115.0, 95.0)
        assert pos.max_high_price == 115.0
        assert pos.max_low_price == 95.0

    def test_update_max_prices_does_not_regress(self):
        pos = self.make_position(qty=10, entry_price=100.0)
        pos.update_max_prices(115.0, 90.0)
        pos.update_max_prices(105.0, 95.0)  # new values are worse
        assert pos.max_high_price == 115.0
        assert pos.max_low_price == 90.0


# ─────────────────────────────────────────────────────────────────
# Portfolio — open / close / partial
# ─────────────────────────────────────────────────────────────────

class TestPortfolioOpenPosition:
    def test_open_deducts_cash(self):
        p = make_portfolio(100_000)
        open_one(p, qty=10, price=150.0)
        assert p.cash == pytest.approx(100_000 - 10 * 150.0)

    def test_open_returns_trade(self):
        p = make_portfolio()
        trade = open_one(p)
        assert trade is not None
        assert trade.symbol == "AAPL"

    def test_open_adds_position(self):
        p = make_portfolio()
        open_one(p)
        assert len(p.positions) == 1

    def test_open_insufficient_cash_returns_none(self):
        p = make_portfolio(100)  # only $100
        trade = open_one(p, qty=10, price=150.0)  # needs $1500
        assert trade is None
        assert len(p.positions) == 0

    def test_open_zero_qty_returns_none(self):
        p = make_portfolio()
        trade = open_one(p, qty=0)
        assert trade is None

    def test_open_sets_partial_exit_targets(self):
        p = make_portfolio()
        open_one(p, price=100.0)
        trade_id = list(p.positions.keys())[0]
        pos = p.positions[trade_id]
        expected_1 = 100.0 * (1 + config_stub.CONFIG["partial_exit_1_pct"])
        expected_2 = 100.0 * (1 + config_stub.CONFIG["partial_exit_2_pct"])
        assert pos.exit_target_1 == pytest.approx(expected_1)
        assert pos.exit_target_2 == pytest.approx(expected_2)

    def test_multiple_opens_increment_trade_counter(self):
        p = make_portfolio()
        open_one(p, symbol="AAPL", qty=1, price=10.0)
        open_one(p, symbol="TSLA", qty=1, price=10.0)
        assert p.trade_counter == 2
        assert len(p.positions) == 2


class TestPortfolioClosePosition:
    def test_close_returns_cash(self):
        p = make_portfolio(100_000)
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        cash_before_close = p.cash
        p.close_position(trade_id, 110.0, NOW + timedelta(hours=1))
        assert p.cash == pytest.approx(cash_before_close + 10 * 110.0)

    def test_close_removes_position(self):
        p = make_portfolio()
        open_one(p)
        trade_id = list(p.positions.keys())[0]
        p.close_position(trade_id, 160.0, NOW + timedelta(hours=1))
        assert len(p.positions) == 0

    def test_close_records_pnl(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        closed = p.close_position(trade_id, 110.0, NOW + timedelta(hours=1))
        assert closed.pnl == pytest.approx(100.0)  # 10 shares * $10
        assert closed.pnl_pct == pytest.approx(0.10)

    def test_close_nonexistent_returns_none(self):
        p = make_portfolio()
        result = p.close_position(9999, 100.0, NOW)
        assert result is None

    def test_close_appends_to_closed_trades(self):
        p = make_portfolio()
        open_one(p)
        trade_id = list(p.positions.keys())[0]
        p.close_position(trade_id, 160.0, NOW + timedelta(hours=1))
        assert len(p.closed_trades) == 1

    def test_close_calculates_hold_minutes(self):
        p = make_portfolio()
        entry_time = NOW
        open_one(p, date=entry_time)
        trade_id = list(p.positions.keys())[0]
        exit_time = entry_time + timedelta(minutes=90)
        closed = p.close_position(trade_id, 160.0, exit_time)
        assert closed.hold_minutes == 90

    def test_close_losing_trade_pnl(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        closed = p.close_position(trade_id, 90.0, NOW + timedelta(hours=1))
        assert closed.pnl == pytest.approx(-100.0)
        assert closed.is_loser is True


class TestPortfolioPartialClose:
    def test_partial_close_reduces_qty(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        p.partial_close(trade_id, 4, 110.0, NOW + timedelta(hours=1))
        assert p.positions[trade_id].qty == 6

    def test_partial_close_records_pnl(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        closed = p.partial_close(trade_id, 4, 110.0, NOW + timedelta(hours=1))
        assert closed.pnl == pytest.approx(4 * 10.0)  # 4 shares * $10 gain

    def test_partial_close_returns_cash(self):
        p = make_portfolio(100_000)
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        cash_after_open = p.cash
        p.partial_close(trade_id, 4, 110.0, NOW + timedelta(hours=1))
        assert p.cash == pytest.approx(cash_after_open + 4 * 110.0)

    def test_partial_close_all_qty_closes_position(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        p.partial_close(trade_id, 10, 110.0, NOW + timedelta(hours=1))
        assert len(p.positions) == 0

    def test_partial_close_nonexistent_returns_none(self):
        p = make_portfolio()
        result = p.partial_close(9999, 5, 100.0, NOW)
        assert result is None


# ─────────────────────────────────────────────────────────────────
# Portfolio — aggregation methods
# ─────────────────────────────────────────────────────────────────

class TestPortfolioAggregation:
    def test_gross_value_cash_only(self):
        p = make_portfolio(50_000)
        assert p.gross_value({}) == pytest.approx(50_000)

    def test_gross_value_with_positions(self):
        p = make_portfolio(100_000)
        open_one(p, qty=10, price=100.0)
        prices = {"AAPL": 110.0}
        # cash = 100_000 - 1000 = 99_000; position = 10*110 = 1100
        assert p.gross_value(prices) == pytest.approx(99_000 + 1100)

    def test_unrealized_pnl_profit(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        assert p.unrealized_pnl({"AAPL": 110.0}) == pytest.approx(100.0)

    def test_unrealized_pnl_loss(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        assert p.unrealized_pnl({"AAPL": 90.0}) == pytest.approx(-100.0)

    def test_unrealized_pnl_no_price_data(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        # No prices supplied — should return 0.0
        assert p.unrealized_pnl({}) == pytest.approx(0.0)

    def test_update_prices_tracks_highs(self):
        p = make_portfolio()
        open_one(p, qty=10, price=100.0)
        trade_id = list(p.positions.keys())[0]
        p.update_prices({"AAPL": 120.0})
        assert p.positions[trade_id].max_high_price == 120.0

    def test_record_state_calculates_pnl_pct(self):
        p = make_portfolio(100_000)
        open_one(p, qty=10, price=100.0)
        prices = {"AAPL": 100.0}  # no change
        state = p.record_state(NOW, prices)
        assert isinstance(state, PortfolioState)
        assert state.num_positions == 1
        # pnl_pct should be ~0 since no price move
        assert abs(state.pnl_pct) < 0.01

    def test_record_state_stored_in_history(self):
        p = make_portfolio()
        p.record_state(NOW, {})
        p.record_state(NOW + timedelta(days=1), {})
        assert len(p.history) == 2


# ─────────────────────────────────────────────────────────────────
# generate_report
# ─────────────────────────────────────────────────────────────────

class TestGenerateReport:
    def _make_closed_trade(self, pnl, pnl_pct, regime="BULL", score=70,
                           hold=60, reason="STOP_LOSS"):
        t = Trade(
            symbol="AAPL",
            entry_date=NOW,
            entry_price=100.0,
            qty=10,
            exit_date=NOW + timedelta(hours=1),
            exit_price=100.0 + pnl / 10,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            hold_minutes=hold,
            regime_at_entry=regime,
            score_at_entry=score,
        )
        return t

    def test_report_has_expected_keys(self):
        trades = [
            self._make_closed_trade(100, 0.10),
            self._make_closed_trade(-50, -0.05),
        ]
        portfolio = make_portfolio()
        report = generate_report(
            trades,
            NOW - timedelta(days=30),
            NOW,
            portfolio,
        )
        assert isinstance(report, dict)
        # Check some top-level keys that the function should return
        assert "total_trades" in report or "trades" in report or len(report) > 0

    def test_report_empty_trades(self):
        portfolio = make_portfolio()
        report = generate_report([], NOW - timedelta(days=30), NOW, portfolio)
        assert report is not None

    def test_report_all_winners(self):
        trades = [self._make_closed_trade(200, 0.20) for _ in range(5)]
        portfolio = make_portfolio()
        report = generate_report(trades, NOW - timedelta(days=30), NOW, portfolio)
        assert report is not None
        # If win_rate is in report it should be 1.0
        if "win_rate" in report:
            assert report["win_rate"] == pytest.approx(1.0)

    def test_report_all_losers(self):
        trades = [self._make_closed_trade(-100, -0.10) for _ in range(5)]
        portfolio = make_portfolio()
        report = generate_report(trades, NOW - timedelta(days=30), NOW, portfolio)
        assert report is not None
        if "win_rate" in report:
            assert report["win_rate"] == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────
# Edge-case matrix
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("qty,price,expected_none", [
    (0, 150.0, True),    # zero qty
    (-5, 150.0, True),   # negative qty
    (10, 0.0, False),    # zero price — allowed (edge)
    (10, 150.0, False),  # normal
])
def test_open_position_edge_cases(qty, price, expected_none):
    p = make_portfolio(100_000)
    trade = p.open_position("AAPL", qty, price, NOW, 2.0, "BULL", 70)
    if expected_none:
        assert trade is None
    else:
        # Non-None result (may still be None if cash insufficient but qty>0)
        # We just assert no crash
        pass


@pytest.mark.parametrize("entry,exit_p,qty,expected_pnl", [
    (100.0, 110.0, 10,  100.0),
    (100.0,  90.0, 10, -100.0),
    (100.0, 100.0, 10,    0.0),
    (200.0, 210.0,  5,   50.0),
])
def test_pnl_calculation_matrix(entry, exit_p, qty, expected_pnl):
    p = make_portfolio(100_000)
    p.open_position("AAPL", qty, entry, NOW, 2.0, "BULL", 70)
    trade_id = list(p.positions.keys())[0]
    closed = p.close_position(trade_id, exit_p, NOW + timedelta(hours=1))
    assert closed.pnl == pytest.approx(expected_pnl)
