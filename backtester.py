#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  backtester.py                             ║
# ║   Production backtesting engine for Decifer 2.0 signals      ║
# ║                                                              ║
# ║   Walk-forward bar-by-bar replay of historical data with:   ║
# ║   • Signal computation (via signals.py confluence scoring)   ║
# ║   • Risk constraints (position sizing, max positions, etc.)  ║
# ║   • Trade management (entries, ATR stops, partial exits)     ║
# ║   • Performance metrics (Sharpe, Sortino, drawdown, regime) ║
# ║                                                              ║
# ║   Usage:                                                     ║
# ║   python backtester.py --symbols AAPL TSLA --start 2024-01-01 ║
# ║   python backtester.py --param-sweep atr_mult 1.0 2.0 0.5    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import argparse
import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

from config import CONFIG
from signals import compute_confluence, compute_indicators

log = logging.getLogger("decifer.backtester")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

EST = pytz.timezone("America/New_York")
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = BASE_DIR / "data" / "historical"  # legacy — kept for utility functions
FEATURES_DIR = BASE_DIR / "data" / "features"  # tiered store — primary read source
RESULTS_DIR = BASE_DIR / "backtest_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ═════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═════════════════════════════════════════════════════════════════


@dataclass
class Trade:
    """Record of a single executed trade."""

    symbol: str
    entry_date: datetime
    entry_price: float
    qty: int
    exit_date: datetime | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = "OPEN"  # STOP_LOSS, TAKE_PROFIT, PARTIAL, BREAKEVEN
    hold_minutes: int = 0
    regime_at_entry: str = "UNKNOWN"
    score_at_entry: int = 0
    max_profit: float = 0.0
    max_loss: float = 0.0
    atr_at_entry: float = 0.0

    @property
    def is_closed(self) -> bool:
        return self.exit_date is not None

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def is_loser(self) -> bool:
        return self.pnl < 0


@dataclass
class Position:
    """Active position tracking."""

    symbol: str
    qty: int
    entry_price: float
    entry_date: datetime
    trade_id: int
    atr_at_entry: float = 0.0
    regime_at_entry: str = "UNKNOWN"
    score_at_entry: int = 0
    entry_high: float = 0.0
    entry_low: float = 0.0
    max_high_price: float = 0.0
    max_low_price: float = 0.0
    exit_target_1: float | None = None  # First partial exit
    exit_target_2: float | None = None  # Second partial exit
    partial_exit_1_done: bool = False
    partial_exit_2_done: bool = False

    def update_max_prices(self, high: float, low: float):
        """Track highest high and lowest low since entry."""
        self.max_high_price = max(self.max_high_price, high)
        self.max_low_price = min(self.max_low_price, low)

    def current_value(self, price: float) -> float:
        """Current unrealized value."""
        return self.qty * price

    def unrealized_pnl(self, price: float) -> float:
        """Unrealized P&L in dollars."""
        return (price - self.entry_price) * self.qty

    def unrealized_pnl_pct(self, price: float) -> float:
        """Unrealized P&L as percentage."""
        if self.entry_price > 0:
            return (price - self.entry_price) / self.entry_price
        return 0.0


@dataclass
class PortfolioState:
    """Snapshot of portfolio at a point in time."""

    timestamp: datetime
    cash: float
    gross_value: float
    net_value: float
    num_positions: int
    daily_pnl: float
    total_pnl: float
    pnl_pct: float


# ═════════════════════════════════════════════════════════════════
# PORTFOLIO MANAGER
# ═════════════════════════════════════════════════════════════════


class Portfolio:
    """Manages positions, cash, and P&L tracking."""

    def __init__(self, starting_capital: float):
        self.starting_capital = starting_capital
        self.cash = starting_capital
        self.positions: dict[int, Position] = {}  # trade_id -> Position
        self.closed_trades: list[Trade] = []
        self.daily_start_value = starting_capital
        self.daily_pnl = 0.0
        self.trade_counter = 0
        self.history: list[PortfolioState] = []

    def open_position(
        self, symbol: str, qty: int, price: float, date: datetime, atr: float, regime: str, score: int
    ) -> Trade:
        """Open a new position and return the Trade record."""
        if qty <= 0:
            return None

        cost = qty * price
        if cost > self.cash:
            log.warning(f"Insufficient cash: need ${cost:.2f}, have ${self.cash:.2f}")
            return None

        self.cash -= cost
        self.trade_counter += 1
        trade_id = self.trade_counter

        trade = Trade(
            symbol=symbol,
            entry_date=date,
            entry_price=price,
            qty=qty,
            atr_at_entry=atr,
            regime_at_entry=regime,
            score_at_entry=score,
        )

        position = Position(
            symbol=symbol,
            qty=qty,
            entry_price=price,
            entry_date=date,
            trade_id=trade_id,
            atr_at_entry=atr,
            regime_at_entry=regime,
            score_at_entry=score,
            entry_high=price,
            entry_low=price,
            max_high_price=price,
            max_low_price=price,
        )

        # Calculate partial exit targets
        position.exit_target_1 = price * (1 + CONFIG["partial_exit_1_pct"])
        position.exit_target_2 = price * (1 + CONFIG["partial_exit_2_pct"])

        self.positions[trade_id] = position
        log.info(f"ENTRY {symbol}: {qty} @ ${price:.2f} | Score: {score} | Regime: {regime} | Cash: ${self.cash:,.2f}")
        return trade

    def close_position(self, trade_id: int, price: float, date: datetime, reason: str = "MANUAL") -> Trade | None:
        """Close a position and return the completed Trade."""
        if trade_id not in self.positions:
            return None

        pos = self.positions[trade_id]
        qty = pos.qty
        pnl_dollars = (price - pos.entry_price) * qty
        pnl_pct = (price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

        # Recover cash
        self.cash += qty * price

        # Create closed trade record
        trade = Trade(
            symbol=pos.symbol,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            qty=qty,
            exit_date=date,
            exit_price=price,
            pnl=pnl_dollars,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            hold_minutes=int((date - pos.entry_date).total_seconds() / 60),
            regime_at_entry=pos.regime_at_entry,
            score_at_entry=pos.score_at_entry,
            atr_at_entry=pos.atr_at_entry,
            max_profit=pos.unrealized_pnl(pos.max_high_price),
            max_loss=pos.unrealized_pnl(pos.max_low_price),
        )

        self.closed_trades.append(trade)
        del self.positions[trade_id]

        log.info(
            f"EXIT {pos.symbol}: {qty} @ ${price:.2f} ({reason}) | "
            f"P&L: ${pnl_dollars:+.2f} ({pnl_pct:+.1%}) | Hold: {trade.hold_minutes}m"
        )
        return trade

    def partial_close(
        self, trade_id: int, qty_close: int, price: float, date: datetime, reason: str = "PARTIAL_EXIT"
    ) -> Trade | None:
        """Close part of a position."""
        if trade_id not in self.positions:
            return None

        pos = self.positions[trade_id]
        if qty_close >= pos.qty:
            return self.close_position(trade_id, price, date, reason)

        # Partial close
        qty_remain = pos.qty - qty_close
        pnl_dollars = (price - pos.entry_price) * qty_close
        pnl_pct = (price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

        self.cash += qty_close * price

        trade = Trade(
            symbol=pos.symbol,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            qty=qty_close,
            exit_date=date,
            exit_price=price,
            pnl=pnl_dollars,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            hold_minutes=int((date - pos.entry_date).total_seconds() / 60),
            regime_at_entry=pos.regime_at_entry,
            score_at_entry=pos.score_at_entry,
        )
        self.closed_trades.append(trade)

        # Update position with remaining qty
        pos.qty = qty_remain
        log.info(
            f"PARTIAL_EXIT {pos.symbol}: -{qty_close} (keep {qty_remain}) @ ${price:.2f} | P&L: ${pnl_dollars:+.2f}"
        )
        return trade

    def update_prices(self, prices: dict[str, float]):
        """Update current prices for all positions."""
        for _trade_id, pos in self.positions.items():
            if pos.symbol in prices:
                price = prices[pos.symbol]
                pos.update_max_prices(price, price)

    def gross_value(self, prices: dict[str, float]) -> float:
        """Sum of all position values + cash."""
        value = self.cash
        for pos in self.positions.values():
            if pos.symbol in prices:
                value += pos.current_value(prices[pos.symbol])
        return value

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        """Total unrealized P&L across all positions."""
        pnl = 0.0
        for pos in self.positions.values():
            if pos.symbol in prices:
                pnl += pos.unrealized_pnl(prices[pos.symbol])
        return pnl

    def daily_pnl_current(self, prices: dict[str, float]) -> float:
        """Realized daily P&L from closed trades + unrealized from open."""
        realized = sum(
            t.pnl for t in self.closed_trades if t.exit_date and t.exit_date.date() == datetime.now(EST).date()
        )
        unrealized = self.unrealized_pnl(prices)
        return realized + unrealized

    def record_state(self, timestamp: datetime, prices: dict[str, float]):
        """Snapshot current portfolio state."""
        gross = self.gross_value(prices)
        net = gross
        total_pnl = net - self.starting_capital
        pnl_pct = (total_pnl / self.starting_capital) if self.starting_capital > 0 else 0

        state = PortfolioState(
            timestamp=timestamp,
            cash=self.cash,
            gross_value=gross,
            net_value=net,
            num_positions=len(self.positions),
            daily_pnl=self.daily_pnl_current(prices),
            total_pnl=total_pnl,
            pnl_pct=pnl_pct,
        )
        self.history.append(state)
        return state


# ═════════════════════════════════════════════════════════════════
# BACKTESTER ENGINE
# ═════════════════════════════════════════════════════════════════


class Backtester:
    """Main backtesting engine."""

    def __init__(
        self,
        symbols: list[str],
        start_date: datetime,
        end_date: datetime,
        min_score: int | None = None,
        atr_stop_mult: float | None = None,
        atr_trail_mult: float | None = None,
    ):
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.portfolio = Portfolio(CONFIG["starting_capital"])

        # Override config params if provided
        self.min_score = min_score or CONFIG["min_score_to_trade"]
        self.atr_stop_mult = atr_stop_mult or CONFIG["atr_stop_multiplier"]
        self.atr_trail_mult = atr_trail_mult or CONFIG["atr_trail_multiplier"]

        # Data storage
        self.data: dict[str, pd.DataFrame] = {}
        self.current_bar = {}  # Symbol -> current index in its dataframe
        self.regimes: dict[datetime, str] = {}  # Date -> market regime

        log.info(f"Backtester initialized: {len(symbols)} symbols, {start_date.date()} → {end_date.date()}")
        log.info(
            f"Min score: {self.min_score}, ATR stop mult: {self.atr_stop_mult}, ATR trail mult: {self.atr_trail_mult}"
        )

    def load_data(self):
        """Load feature-enriched Parquet files for all symbols.

        Load order (first match wins):
          1. data/features/daily/{symbol}_1d.parquet   — tiered store (preferred)
          2. data/features/intraday/{symbol}_5m.parquet
          3. data/historical/daily/{symbol}_1d.parquet  — legacy fallback
          4. data/historical/intraday/{symbol}_5m.parquet
        """
        log.info("Loading historical data...")

        feat_daily = FEATURES_DIR / "daily"
        feat_intraday = FEATURES_DIR / "intraday"
        legacy_daily = DATA_DIR / "daily"
        legacy_intraday = DATA_DIR / "intraday"

        candidates = [
            (feat_daily, "{symbol}_1d.parquet", "daily (features)"),
            (feat_intraday, "{symbol}_5m.parquet", "5m (features)"),
            (legacy_daily, "{symbol}_1d.parquet", "daily (legacy)"),
            (legacy_intraday, "{symbol}_5m.parquet", "5m (legacy)"),
        ]

        for symbol in self.symbols:
            loaded = False
            for base_dir, pattern, label in candidates:
                path = base_dir / pattern.format(symbol=symbol)
                if not path.exists():
                    continue
                try:
                    df = pd.read_parquet(path)
                    if df.empty:
                        continue
                    if not isinstance(df.index, pd.DatetimeIndex):
                        df.index = pd.to_datetime(df.index)
                    df = df[(df.index >= self.start_date) & (df.index <= self.end_date)]
                    if df.empty:
                        continue
                    # Normalize all column names to lowercase for consistent access.
                    df.columns = [c.lower() for c in df.columns]
                    self.data[symbol] = df.sort_index()
                    log.info(f"Loaded {symbol}: {len(df)} bars ({label})")
                    loaded = True
                    break
                except Exception as e:
                    log.warning(f"Failed to load {label} {symbol}: {e}")

            if not loaded:
                log.warning(f"No data found for {symbol}")

        if not self.data:
            raise ValueError("No data loaded for any symbol")

        # Initialize bar indices
        for symbol in self.data:
            self.current_bar[symbol] = 0

    def get_regime(self, date: datetime) -> str:
        """
        Simplified market regime detection.
        In a real backtest, you'd use VIX, breadth indicators, etc.
        """
        # For backtest: static "BULL_TRENDING" unless a specific date is marked
        if date in self.regimes:
            return self.regimes[date]
        return "BULL_TRENDING"

    def compute_signal(self, symbol: str, idx: int) -> dict | None:
        """
        Compute signal score for a bar using local indicators.
        Returns dict with 'score', 'direction', 'signal', 'atr'.
        """
        if symbol not in self.data:
            return None

        df = self.data[symbol]
        if idx < 30:  # Need at least 30 bars for indicators
            return None

        # Extract lookback window (last 50 bars for stability)
        window = df.iloc[max(0, idx - 49) : idx + 1].copy()
        if len(window) < 30:
            return None

        # compute_indicators expects yfinance-style title-case column names;
        # features parquet uses lowercase — normalize before calling.
        _col_map = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        window = window.rename(columns={k: v for k, v in _col_map.items() if k in window.columns})

        # Compute indicators
        indicators = compute_indicators(window, symbol, "1d")
        if not indicators:
            return None

        # Compute confluence score (single timeframe for backtest)
        confluence = compute_confluence(indicators, None, None, news_score=0)

        return {
            "score": confluence["score"],
            "direction": confluence["direction"],
            "signal": confluence["signal"],
            "atr": indicators.get("atr", 0.0),
            "price": indicators.get("price", 0.0),
            "bull_aligned": indicators.get("bull_aligned", False),
            "mfi": indicators.get("mfi", 50),
            "vol_ratio": indicators.get("vol_ratio", 1.0),
        }

    def check_stops_and_exits(self, date: datetime, prices: dict[str, float]):
        """Walk through all positions and check for stop/profit targets."""
        to_close = []

        for trade_id, pos in list(self.portfolio.positions.items()):
            if pos.symbol not in prices:
                continue

            price = prices[pos.symbol]
            pos.unrealized_pnl(price)
            pos.unrealized_pnl_pct(price)

            # ATR-based stop loss
            stop_price = pos.entry_price - (self.atr_stop_mult * pos.atr_at_entry)
            if price < stop_price:
                to_close.append((trade_id, price, "ATR_STOP"))
                continue

            # Trailing stop (2 × ATR from max high)
            trail_dist = self.atr_trail_mult * pos.atr_at_entry
            trailing_stop = pos.max_high_price - trail_dist
            if price < trailing_stop:
                to_close.append((trade_id, price, "TRAILING_STOP"))
                continue

            # Partial exits
            if not pos.partial_exit_1_done and price >= pos.exit_target_1:
                qty_close = max(1, int(pos.qty / 3))  # Sell 1/3
                self.portfolio.partial_close(trade_id, qty_close, price, date, "PARTIAL_1")
                pos.partial_exit_1_done = True

            if not pos.partial_exit_2_done and pos.partial_exit_1_done and price >= pos.exit_target_2:
                qty_close = max(1, int(pos.qty / 3))  # Sell another 1/3
                self.portfolio.partial_close(trade_id, qty_close, price, date, "PARTIAL_2")
                pos.partial_exit_2_done = True

        # Execute closes
        for trade_id, price, reason in to_close:
            self.portfolio.close_position(trade_id, price, date, reason)

    def run(self) -> list[Trade]:
        """Execute full backtest walk-forward."""
        log.info("Starting backtest walk-forward...")

        # Collect all unique dates across symbols
        all_dates = set()
        for df in self.data.values():
            all_dates.update(df.index.date)
        all_dates = sorted(all_dates)

        processed = 0
        for current_date in all_dates:
            if processed % 50 == 0:
                log.info(f"Processed {processed}/{len(all_dates)} days...")
            processed += 1

            # Get current prices and signals for all symbols
            current_prices = {}
            current_signals = {}

            for symbol in self.symbols:
                if symbol not in self.data:
                    continue

                df = self.data[symbol]
                # Get bar(s) for this date
                day_data = df[df.index.date == current_date]
                if day_data.empty:
                    continue

                # Use last bar of the day
                bar = day_data.iloc[-1]
                current_prices[symbol] = bar["close"]

                # Compute signal (index in full dataframe)
                idx = df.index.get_loc(day_data.index[-1])
                signal = self.compute_signal(symbol, idx)
                if signal:
                    current_signals[symbol] = signal

            # Check existing positions for stops/exits
            self.check_stops_and_exits(datetime.combine(current_date, time(16, 0), EST), current_prices)

            # Attempt new entries
            if len(self.portfolio.positions) < CONFIG["max_positions"]:
                regime = self.get_regime(datetime.combine(current_date, time(10, 0), EST))

                for symbol, signal in current_signals.items():
                    if signal["score"] < self.min_score:
                        continue
                    if signal["direction"] not in ["LONG"]:  # Only LONG for now
                        continue
                    if symbol in [p.symbol for p in self.portfolio.positions.values()]:
                        continue  # Skip if already in position

                    # Calculate position size
                    price = signal["price"]
                    atr = signal.get("atr_5m", signal.get("atr", 0.0))
                    portfolio_val = self.portfolio.gross_value(current_prices)
                    risk_amount = portfolio_val * CONFIG["risk_pct_per_trade"]
                    atr_stop = atr * self.atr_stop_mult
                    if atr_stop > 0:
                        qty = int(risk_amount / atr_stop)
                    else:
                        qty = int(risk_amount / price) if price > 0 else 0

                    if qty > 0 and qty * price <= self.portfolio.cash:
                        trade_date = datetime.combine(current_date, time(10, 0), EST)
                        self.portfolio.open_position(symbol, qty, price, trade_date, atr, regime, signal["score"])

            # Record portfolio state
            self.portfolio.record_state(datetime.combine(current_date, time(16, 0), EST), current_prices)

        # Close all remaining positions at market close
        final_date = all_dates[-1]
        final_prices = {}
        for symbol in self.symbols:
            if symbol in self.data:
                final_prices[symbol] = self.data[symbol].iloc[-1]["close"]

        for trade_id in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[trade_id]
            if pos.symbol in final_prices:
                self.portfolio.close_position(
                    trade_id,
                    final_prices[pos.symbol],
                    datetime.combine(final_date, time(16, 0), EST),
                    "END_OF_BACKTEST",
                )

        log.info(f"Backtest complete: {len(self.portfolio.closed_trades)} trades closed")
        return self.portfolio.closed_trades


# ═════════════════════════════════════════════════════════════════
# REPORTING
# ═════════════════════════════════════════════════════════════════


def generate_report(trades: list[Trade], start_date: datetime, end_date: datetime, portfolio: Portfolio = None) -> dict:
    """Generate comprehensive performance report."""
    if not trades:
        return {"error": "No trades to report"}

    winners = [t for t in trades if t.is_winner]
    losers = [t for t in trades if t.is_loser]
    breakevens = [t for t in trades if t.pnl == 0]

    total_trades = len(trades)
    win_rate = len(winners) / total_trades if total_trades > 0 else 0
    avg_win = np.mean([t.pnl for t in winners]) if winners else 0
    avg_loss = np.mean([t.pnl for t in losers]) if losers else 0
    total_pnl = sum(t.pnl for t in trades)

    # Profit factor
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    # Sharpe ratio (assume 252 trading days/year)
    pnls = np.array([t.pnl for t in trades])
    if len(pnls) > 1:
        daily_returns = pnls / CONFIG["starting_capital"]
        sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
    else:
        sharpe = 0

    # Drawdown
    cum_pnl = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cum_pnl)
    drawdown = cum_pnl - running_max
    max_drawdown = np.min(drawdown) if len(drawdown) > 0 else 0

    # Regime breakdown
    regimes = defaultdict(list)
    for t in trades:
        regimes[t.regime_at_entry].append(t.pnl)

    # Monthly returns
    monthly_pnl = defaultdict(float)
    for t in trades:
        month = t.entry_date.strftime("%Y-%m")
        monthly_pnl[month] += t.pnl

    report = {
        "period": f"{start_date.date()} → {end_date.date()}",
        "total_trades": total_trades,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "breakeven_trades": len(breakevens),
        "win_rate": round(win_rate, 3),
        "avg_win_pnl": round(avg_win, 2),
        "avg_loss_pnl": round(avg_loss, 2),
        "total_pnl": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown / CONFIG["starting_capital"], 2),
        "avg_hold_minutes": int(np.mean([t.hold_minutes for t in trades if t.hold_minutes > 0])),
        "regime_breakdown": {k: {"trades": len(v), "pnl": round(sum(v), 2)} for k, v in regimes.items()},
        "monthly_pnl": {k: round(v, 2) for k, v in sorted(monthly_pnl.items())},
    }

    return report


def print_report(report: dict):
    """Pretty-print performance report."""
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS".center(70))
    print("=" * 70)

    print(f"\nPeriod: {report.get('period', 'N/A')}")
    print(f"Starting Capital: ${CONFIG['starting_capital']:,.0f}")

    print("\n--- TRADE METRICS ---")
    print(f"Total Trades:           {report['total_trades']}")
    print(f"Winners:                {report['winning_trades']} ({report['win_rate']:.1%})")
    print(f"Losers:                 {report['losing_trades']}")
    print(f"Breakevens:             {report['breakeven_trades']}")
    print(f"Avg Win:                ${report['avg_win_pnl']:,.2f}")
    print(f"Avg Loss:               ${report['avg_loss_pnl']:,.2f}")
    print(f"Avg Hold:               {report['avg_hold_minutes']} min")

    print("\n--- P&L ---")
    print(f"Total P&L:              ${report['total_pnl']:+,.2f}")
    print(f"Gross Profit:           ${report['gross_profit']:+,.2f}")
    print(f"Gross Loss:             ${report['gross_loss']:+,.2f}")
    print(f"Profit Factor:          {report['profit_factor']:.2f}x")

    print("\n--- RISK METRICS ---")
    print(f"Sharpe Ratio:           {report['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:           ${report['max_drawdown']:,.2f} ({report['max_drawdown_pct']:.1%})")

    print("\n--- BY REGIME ---")
    for regime, stats in report["regime_breakdown"].items():
        print(f"{regime:15s} {stats['trades']:3d} trades, ${stats['pnl']:+10,.2f} P&L")

    print("\n--- MONTHLY P&L ---")
    for month, pnl in report["monthly_pnl"].items():
        pct = (pnl / CONFIG["starting_capital"]) * 100
        print(f"{month}: ${pnl:+10,.2f} ({pct:+.2f}%)")

    print("\n" + "=" * 70 + "\n")


def parameter_sweep(
    symbols: list[str], start_date: datetime, end_date: datetime, param_name: str, param_values: list[float]
) -> list[dict]:
    """Grid search over a parameter."""
    log.info(f"Parameter sweep: {param_name} = {param_values}")
    results = []

    for param_val in param_values:
        log.info(f"\n>>> Testing {param_name}={param_val}")

        kwargs = {
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
        }

        if param_name == "min_score":
            kwargs["min_score"] = int(param_val)
        elif param_name == "atr_stop_mult":
            kwargs["atr_stop_mult"] = param_val
        elif param_name == "atr_trail_mult":
            kwargs["atr_trail_mult"] = param_val

        backtester = Backtester(**kwargs)
        backtester.load_data()
        trades = backtester.run()

        report = generate_report(trades, start_date, end_date)
        report["param_name"] = param_name
        report["param_value"] = param_val
        results.append(report)

        print_report(report)

    # Summary table
    print("\n" + "=" * 70)
    print("PARAMETER SWEEP SUMMARY".center(70))
    print("=" * 70)
    print(f"\n{param_name:20s} | Trades | Win% | Profit | Sharpe | Max DD")
    print("-" * 70)
    for r in results:
        print(
            f"{r['param_value']:20} | {r['total_trades']:6d} | "
            f"{r['win_rate']:4.0%} | ${r['total_pnl']:8,.0f} | "
            f"{r['sharpe_ratio']:6.2f} | ${r['max_drawdown']:7,.0f}"
        )

    return results


# ═════════════════════════════════════════════════════════════════
# DATA DOWNLOADER
# ═════════════════════════════════════════════════════════════════


def download_historical_data(
    symbols: list[str],
    start: str,
    end: str,
    interval: str = "1d",
    out_dir=None,
) -> dict[str, bool]:
    """
    Download OHLCV history via yfinance and cache as Parquet files.

    Required before running the walk-forward backtester when no local data
    files exist.  Each symbol is downloaded individually to work around
    yfinance thread-safety issues.

    Parameters
    ----------
    symbols  : list of ticker strings
    start    : "YYYY-MM-DD" start date (inclusive)
    end      : "YYYY-MM-DD" end date (exclusive, yfinance convention)
    interval : yfinance interval string — "1d" (default) or "5m", etc.
    out_dir  : override the output directory (used in tests); accepts str or Path

    Returns
    -------
    dict mapping symbol → True (saved successfully) / False (failed or empty)

    Usage
    -----
    # Download 2 years of daily data before running a backtest:
    from backtester import download_historical_data, Backtester
    download_historical_data(["AAPL", "TSLA"], "2022-01-01", "2024-01-01")
    bt = Backtester(["AAPL", "TSLA"], ...)
    bt.load_data()   # will now find the Parquet files
    """
    import yfinance as yf

    if out_dir is None:
        if interval == "1d":
            out_dir = DATA_DIR / "daily"
        else:
            out_dir = DATA_DIR / "intraday"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, bool] = {}
    suffix = "" if interval == "1d" else f"_{interval}"

    for symbol in symbols:
        out_file = out_dir / f"{symbol}{suffix}.parquet"
        try:
            df = yf.download(
                symbol,
                start=start,
                end=end,
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty:
                log.warning("download_historical_data: no data returned for %s", symbol)
                results[symbol] = False
                continue

            # Flatten multi-level columns (yfinance >= 0.2 multi-ticker response)
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)

            # Normalise to lowercase so backtester can read open/high/low/close/volume
            df.columns = [c.lower() for c in df.columns]

            required = {"open", "high", "low", "close", "volume"}
            missing = required - set(df.columns)
            if missing:
                log.warning(
                    "download_historical_data: %s missing required columns %s",
                    symbol,
                    missing,
                )
                results[symbol] = False
                continue

            df.to_parquet(out_file)
            log.info(
                "download_historical_data: saved %s (%d bars) → %s",
                symbol,
                len(df),
                out_file,
            )
            results[symbol] = True

        except Exception as e:
            log.warning("download_historical_data: %s failed — %s", symbol, e)
            results[symbol] = False

    return results


# ═════════════════════════════════════════════════════════════════
# CLI & MAIN
# ═════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="Decifer Backtester")
    parser.add_argument("--symbols", nargs="+", default=["AAPL", "TSLA"], help="Symbols to backtest")
    parser.add_argument("--start", type=str, default="2024-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2024-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--min-score", type=int, default=None, help="Override min_score_to_trade")
    parser.add_argument("--atr-stop-mult", type=float, default=None, help="Override atr_stop_multiplier")
    parser.add_argument("--atr-trail-mult", type=float, default=None, help="Override atr_trail_multiplier")
    parser.add_argument(
        "--param-sweep", nargs="+", metavar=("PARAM", "VALUES"), help="Grid search: --param-sweep min_score 10 15 20 25"
    )
    parser.add_argument("--save-trades", action="store_true", help="Save individual trades to JSON")
    parser.add_argument(
        "--download-data", action="store_true", help="Download historical data via yfinance before running backtest"
    )

    args = parser.parse_args()

    # Optionally download data before backtesting
    if args.download_data:
        log.info("Downloading historical data via yfinance...")
        dl_results = download_historical_data(args.symbols, args.start, args.end)
        failed = [s for s, ok in dl_results.items() if not ok]
        if failed:
            log.warning("Failed to download data for: %s", failed)
        else:
            log.info("All symbols downloaded successfully.")

    start_date = pd.to_datetime(args.start)
    end_date = pd.to_datetime(args.end)

    # Parameter sweep mode
    if args.param_sweep and len(args.param_sweep) >= 2:
        param_name = args.param_sweep[0]
        param_values = [float(v) for v in args.param_sweep[1:]]
        results = parameter_sweep(args.symbols, start_date, end_date, param_name, param_values)

        # Save sweep results
        output_file = RESULTS_DIR / f"sweep_{param_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, default=str)
        log.info(f"Sweep results saved: {output_file}")
        return

    # Single backtest mode
    log.info(f"Starting backtest: {', '.join(args.symbols)}")
    backtester = Backtester(
        args.symbols,
        start_date,
        end_date,
        min_score=args.min_score,
        atr_stop_mult=args.atr_stop_mult,
        atr_trail_mult=args.atr_trail_mult,
    )

    backtester.load_data()
    trades = backtester.run()

    report = generate_report(trades, start_date, end_date, backtester.portfolio)
    print_report(report)

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = RESULTS_DIR / f"backtest_{timestamp}.json"

    output = {
        "report": report,
        "config": {
            "symbols": args.symbols,
            "start_date": str(start_date.date()),
            "end_date": str(end_date.date()),
            "min_score": backtester.min_score,
            "atr_stop_mult": backtester.atr_stop_mult,
            "atr_trail_mult": backtester.atr_trail_mult,
        },
    }

    if args.save_trades:
        output["trades"] = [asdict(t) for t in trades]

    with open(results_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log.info(f"Results saved: {results_file}")


if __name__ == "__main__":
    main()
