"""
Portfolio-level optimization module for Decifer Trading bot.
Handles correlation-aware position sizing, risk parity, VaR calculations,
sector concentration monitoring, and rebalancing signals.

All calculations use yfinance for real-time data with 60-day historical lookback.
Correlation matrix cached and recomputed every 30 minutes for performance.
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import logging
from dataclasses import dataclass, field
from functools import lru_cache
import time

logger = logging.getLogger(__name__)


@dataclass
class CorrelationWarning:
    """Warning for high correlation between positions."""
    symbol: str
    correlated_symbol: str
    correlation: float
    message: str


@dataclass
class RiskReport:
    """Comprehensive portfolio risk report."""
    portfolio_var_95: float
    conditional_var_95: float
    max_drawdown_potential: float
    sector_concentration: Dict[str, float]
    sector_alerts: List[str] = field(default_factory=list)
    correlation_warnings: List[CorrelationWarning] = field(default_factory=list)
    position_count_optimal: int = 0
    total_risk_score: float = 0.0


@dataclass
class RebalanceSignal:
    """Rebalancing recommendation."""
    symbol: str
    action: str  # 'TRIM', 'ADD', 'CLOSE'
    current_weight: float
    target_weight: float
    suggested_adjustment: float
    reason: str


class CorrelationTracker:
    """
    Maintains rolling correlation matrix between portfolio positions.
    Caches results for 30 minutes to avoid redundant yfinance calls.
    """

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days
        self.correlation_matrix = None
        self.last_update = None
        self.cache_interval_seconds = 1800  # 30 minutes
        self.symbols_cached = None

    def _fetch_returns(self, symbols: List[str]) -> pd.DataFrame:
        """Fetch daily returns for symbols using yfinance."""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.lookback_days)

            # Fetch adjusted close prices
            data = yf.download(
                ' '.join(symbols),
                start=start_date,
                end=end_date,
                progress=False,
                threads=True
            )

            # Handle single symbol case
            if len(symbols) == 1:
                prices = data['Adj Close'].to_frame(name=symbols[0])
            else:
                prices = data['Adj Close']

            # Calculate daily returns
            returns = prices.pct_change().dropna()
            return returns
        except Exception as e:
            logger.error(f"Error fetching returns for {symbols}: {e}")
            return pd.DataFrame()

    def update(self, symbols: List[str]) -> np.ndarray:
        """
        Update correlation matrix. Returns cached result if <30 min old.

        Args:
            symbols: List of ticker symbols

        Returns:
            Correlation matrix (NxN numpy array)
        """
        # Check cache validity
        if (self.correlation_matrix is not None and
            self.symbols_cached == symbols and
            self.last_update is not None):

            elapsed = time.time() - self.last_update
            if elapsed < self.cache_interval_seconds:
                return self.correlation_matrix

        # Fetch fresh data
        returns = self._fetch_returns(symbols)

        if returns.empty or len(returns) < 10:
            logger.warning(f"Insufficient data for correlation matrix")
            # Return identity matrix if data unavailable
            return np.eye(len(symbols))

        # Calculate correlation matrix
        self.correlation_matrix = returns.corr().values
        self.last_update = time.time()
        self.symbols_cached = symbols.copy()

        return self.correlation_matrix

    def get_correlation(self, symbol1: str, symbol2: str, symbols: List[str]) -> float:
        """Get correlation between two specific symbols."""
        corr_matrix = self.update(symbols)
        try:
            idx1 = symbols.index(symbol1)
            idx2 = symbols.index(symbol2)
            return corr_matrix[idx1, idx2]
        except (ValueError, IndexError):
            return 0.0

    def find_correlated_cluster(self, symbol: str, symbols: List[str],
                               threshold: float = 0.7) -> List[str]:
        """Find all symbols correlated >threshold with given symbol."""
        corr_matrix = self.update(symbols)
        try:
            idx = symbols.index(symbol)
            correlations = corr_matrix[idx]
            cluster = [symbols[i] for i, corr in enumerate(correlations)
                      if corr > threshold and symbols[i] != symbol]
            return cluster
        except ValueError:
            return []


class RiskParitySizer:
    """
    Calculates position weights inversely proportional to volatility.
    Higher volatility = smaller position, lower volatility = larger position.
    Ensures equal risk contribution from each position.
    """

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days
        self.volatility_cache = {}
        self.cache_time = {}

    def _calculate_volatility(self, symbol: str, lookback_days: int = None) -> float:
        """Calculate annualized volatility for a symbol."""
        if lookback_days is None:
            lookback_days = self.lookback_days

        # Check cache (valid for 1 hour)
        if symbol in self.volatility_cache:
            if time.time() - self.cache_time[symbol] < 3600:
                return self.volatility_cache[symbol]

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days)

            data = yf.download(symbol, start=start_date, end=end_date,
                             progress=False)

            if data.empty:
                logger.warning(f"No data for {symbol}, returning default vol")
                return 0.20  # Default 20% volatility

            returns = data['Adj Close'].pct_change().dropna()
            daily_vol = returns.std()
            annual_vol = daily_vol * np.sqrt(252)

            self.volatility_cache[symbol] = annual_vol
            self.cache_time[symbol] = time.time()

            return annual_vol
        except Exception as e:
            logger.error(f"Error calculating volatility for {symbol}: {e}")
            return 0.20

    def calculate_weights(self, symbols: List[str],
                         volatilities: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """
        Calculate risk-parity weights for given symbols.

        Args:
            symbols: List of ticker symbols
            volatilities: Optional dict of pre-calculated volatilities

        Returns:
            Dict of symbol -> weight (sums to 1.0)
        """
        if not symbols:
            return {}

        # Get volatilities
        if volatilities is None:
            vols = {sym: self._calculate_volatility(sym) for sym in symbols}
        else:
            vols = volatilities

        # Prevent division by zero
        vols = {sym: max(vol, 0.001) for sym, vol in vols.items()}

        # Inverse volatility weights: w_i = (1/vol_i) / sum(1/vol_j)
        inverse_vols = {sym: 1.0 / vols[sym] for sym in symbols}
        total_inverse = sum(inverse_vols.values())

        weights = {sym: inverse_vols[sym] / total_inverse for sym in symbols}

        return weights

    def adjust_for_correlation(self, weights: Dict[str, float],
                              correlation_matrix: np.ndarray,
                              symbols: List[str]) -> Dict[str, float]:
        """
        Adjust weights for portfolio correlation.
        High correlation between positions should reduce position count.
        """
        if not symbols or len(symbols) < 2:
            return weights

        # Calculate average correlation
        mask = np.triu(np.ones_like(correlation_matrix), k=1)
        avg_correlation = np.sum(correlation_matrix * mask) / np.sum(mask)

        # Reduce weights proportionally to correlation
        # Higher correlation = more concentration needed = lower weight adjustment
        correlation_factor = 1.0 / (1.0 + avg_correlation)

        adjusted = {sym: weight * correlation_factor for sym, weight in weights.items()}

        # Renormalize
        total = sum(adjusted.values())
        adjusted = {sym: weight / total for sym, weight in adjusted.items()}

        return adjusted


class PortfolioVaR:
    """
    Computes Value at Risk (VaR) and Conditional VaR (Expected Shortfall).
    Supports both historical and parametric VaR methods.
    """

    def __init__(self, confidence_level: float = 0.95, lookback_days: int = 60):
        self.confidence_level = confidence_level
        self.lookback_days = lookback_days
        self.percentile = (1.0 - confidence_level) * 100

    def _get_portfolio_returns(self, portfolio: Dict[str, Tuple[int, float]],
                              symbols: List[str]) -> pd.Series:
        """Calculate portfolio returns from position weights."""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.lookback_days)

            # Fetch price data
            data = yf.download(' '.join(symbols), start=start_date,
                             end=end_date, progress=False)

            if len(symbols) == 1:
                prices = data['Adj Close'].to_frame(name=symbols[0])
            else:
                prices = data['Adj Close']

            returns = prices.pct_change().dropna()

            # Calculate weighted portfolio returns
            weights = {}
            total_value = sum(qty * price for qty, price in portfolio.values())

            for symbol, (qty, price) in portfolio.items():
                weights[symbol] = (qty * price) / total_value if total_value > 0 else 0

            portfolio_returns = pd.Series(0.0, index=returns.index)
            for symbol, weight in weights.items():
                if symbol in returns.columns:
                    portfolio_returns += returns[symbol] * weight

            return portfolio_returns
        except Exception as e:
            logger.error(f"Error calculating portfolio returns: {e}")
            return pd.Series()

    def historical_var(self, portfolio: Dict[str, Tuple[int, float]],
                      symbols: List[str]) -> float:
        """
        Calculate historical VaR as percentile of historical returns.
        Returns the daily loss at confidence level.
        """
        returns = self._get_portfolio_returns(portfolio, symbols)

        if returns.empty or len(returns) < 10:
            logger.warning("Insufficient data for historical VaR")
            return 0.0

        # VaR is the negative of the percentile (loss is negative return)
        var = -np.percentile(returns, self.percentile)
        return var

    def conditional_var(self, portfolio: Dict[str, Tuple[int, float]],
                       symbols: List[str]) -> float:
        """
        Calculate Conditional VaR (Expected Shortfall).
        Average loss beyond the VaR level.
        """
        returns = self._get_portfolio_returns(portfolio, symbols)

        if returns.empty or len(returns) < 10:
            logger.warning("Insufficient data for CVaR")
            return 0.0

        var_threshold = np.percentile(returns, self.percentile)
        tail_losses = returns[returns <= var_threshold]

        if len(tail_losses) == 0:
            return 0.0

        cvar = -tail_losses.mean()
        return cvar

    def parametric_var(self, portfolio: Dict[str, Tuple[int, float]],
                      correlation_matrix: np.ndarray,
                      symbols: List[str],
                      volatilities: Dict[str, float]) -> float:
        """
        Calculate parametric VaR using correlation matrix and volatilities.
        Assumes normal distribution of returns.
        """
        from scipy.stats import norm

        if not symbols or not volatilities:
            return 0.0

        # Calculate portfolio volatility
        total_value = sum(qty * price for qty, price in portfolio.values())
        if total_value == 0:
            return 0.0

        weights = np.array([
            (portfolio.get(sym, (0, 1))[0] * portfolio.get(sym, (0, 1))[1]) / total_value
            for sym in symbols
        ])

        vols = np.array([volatilities.get(sym, 0.20) / np.sqrt(252)
                        for sym in symbols])

        # Portfolio variance: w^T * Cov * w
        cov_matrix = correlation_matrix * np.outer(vols, vols)
        portfolio_var = weights @ cov_matrix @ weights
        portfolio_vol = np.sqrt(portfolio_var)

        # VaR using normal distribution
        z_score = norm.ppf(self.confidence_level)
        var = z_score * portfolio_vol

        return var


class SectorMonitor:
    """
    Tracks sector exposure with auto-detection from yfinance.
    Dynamically adjusts sector caps based on trading regime.
    Alerts when approaching concentration limits.
    """

    # Default sector mapping (fallback)
    DEFAULT_SECTOR_MAP = {
        'NVDA': 'Technology', 'AMD': 'Technology', 'AVGO': 'Technology',
        'INTC': 'Technology', 'MSFT': 'Technology', 'AAPL': 'Technology',
        'GOOGL': 'Technology', 'META': 'Technology', 'JPM': 'Financials',
        'BAC': 'Financials', 'GS': 'Financials', 'XOM': 'Energy',
        'CVX': 'Energy', 'COP': 'Energy', 'JNJ': 'Healthcare',
        'UNH': 'Healthcare', 'PFE': 'Healthcare', 'MCD': 'Consumer',
        'AMZN': 'Consumer', 'WMT': 'Consumer', 'BA': 'Industrials'
    }

    def __init__(self):
        self.sector_cache = {}
        self.cache_time = {}
        self.sector_limits = {
            'NORMAL': 0.30,      # 30% max per sector in normal regime
            'CHOPPY': 0.20,      # 20% max in choppy regime
            'PANIC': 0.15        # 15% max in panic regime
        }

    def _get_sector_from_yfinance(self, symbol: str) -> str:
        """Fetch sector from yfinance ticker info."""
        try:
            ticker = yf.Ticker(symbol)
            sector = ticker.info.get('sector', None)

            if sector:
                self.sector_cache[symbol] = sector
                self.cache_time[symbol] = time.time()
                return sector

            # Fallback to manual mapping
            return self.DEFAULT_SECTOR_MAP.get(symbol, 'Other')
        except Exception as e:
            logger.warning(f"Could not fetch sector for {symbol}: {e}")
            return self.DEFAULT_SECTOR_MAP.get(symbol, 'Other')

    def get_sector(self, symbol: str) -> str:
        """Get sector for symbol, using cache if available."""
        if symbol in self.sector_cache:
            if time.time() - self.cache_time.get(symbol, 0) < 86400:  # 24h cache
                return self.sector_cache[symbol]

        return self._get_sector_from_yfinance(symbol)

    def calculate_sector_weights(self, portfolio: Dict[str, Tuple[int, float]]) -> Dict[str, float]:
        """Calculate current sector exposure as % of portfolio."""
        total_value = sum(qty * price for qty, price in portfolio.values())

        if total_value == 0:
            return {}

        sector_values = defaultdict(float)
        for symbol, (qty, price) in portfolio.items():
            sector = self.get_sector(symbol)
            sector_values[sector] += qty * price

        sector_weights = {
            sector: value / total_value
            for sector, value in sector_values.items()
        }

        return sector_weights

    def check_concentration(self, portfolio: Dict[str, Tuple[int, float]],
                           regime: str = 'NORMAL') -> Tuple[Dict[str, float], List[str]]:
        """
        Check sector concentration and generate alerts.

        Args:
            portfolio: Dict of symbol -> (quantity, price)
            regime: Trading regime ('NORMAL', 'CHOPPY', 'PANIC')

        Returns:
            (sector_weights dict, list of alert strings)
        """
        sector_weights = self.calculate_sector_weights(portfolio)
        alerts = []

        limit = self.sector_limits.get(regime, 0.30)

        for sector, weight in sector_weights.items():
            if weight > limit:
                alerts.append(
                    f"SECTOR ALERT: {sector} at {weight:.1%} "
                    f"(limit: {limit:.1%} in {regime} regime)"
                )
            elif weight > limit * 0.8:
                alerts.append(
                    f"SECTOR WARNING: {sector} at {weight:.1%} "
                    f"approaching {limit:.1%} limit"
                )

        return sector_weights, alerts


class PortfolioOptimizer:
    """
    Main orchestrator combining correlation tracking, risk parity sizing,
    VaR calculations, sector monitoring, and rebalancing suggestions.
    """

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days
        self.correlation_tracker = CorrelationTracker(lookback_days)
        self.risk_parity_sizer = RiskParitySizer(lookback_days)
        self.portfolio_var = PortfolioVaR(lookback_days=lookback_days)
        self.sector_monitor = SectorMonitor()

    def check_new_position(self, new_symbol: str,
                          existing_symbols: List[str],
                          correlation_threshold: float = 0.7) -> List[CorrelationWarning]:
        """
        Check if new position is too correlated with existing positions.

        Args:
            new_symbol: Ticker to add
            existing_symbols: Current positions
            correlation_threshold: Alert if correlation > this value

        Returns:
            List of CorrelationWarning objects
        """
        warnings = []

        if not existing_symbols:
            return warnings

        symbols = existing_symbols + [new_symbol]

        # Find correlated symbols
        cluster = self.correlation_tracker.find_correlated_cluster(
            new_symbol, symbols, correlation_threshold
        )

        for corr_symbol in cluster:
            corr_value = self.correlation_tracker.get_correlation(
                new_symbol, corr_symbol, symbols
            )

            warning = CorrelationWarning(
                symbol=new_symbol,
                correlated_symbol=corr_symbol,
                correlation=corr_value,
                message=f"{new_symbol} has correlation of {corr_value:.2f} "
                       f"with existing position {corr_symbol}"
            )
            warnings.append(warning)

        return warnings

    def get_optimal_position_size(self, symbol: str, base_size: int,
                                 portfolio: Dict[str, Tuple[int, float]],
                                 capital: float) -> Tuple[int, str]:
        """
        Adjust position size for correlation and risk parity.

        Args:
            symbol: Ticker symbol
            base_size: Initial suggested size
            portfolio: Current portfolio
            capital: Available capital

        Returns:
            (adjusted_size, reasoning)
        """
        if not portfolio:
            return base_size, "No existing positions for adjustment"

        existing_symbols = list(portfolio.keys())
        symbols = existing_symbols + [symbol]

        # Get correlations
        correlations = []
        for existing_sym in existing_symbols:
            corr = self.correlation_tracker.get_correlation(
                symbol, existing_sym, symbols
            )
            correlations.append(corr)

        avg_correlation = np.mean(correlations) if correlations else 0.0

        # Reduce size if highly correlated
        correlation_factor = 1.0 / (1.0 + max(0, avg_correlation - 0.5))
        adjusted_size = int(base_size * correlation_factor)

        reasoning = f"Correlation factor: {correlation_factor:.2f} " \
                   f"(avg correlation: {avg_correlation:.2f})"

        return max(1, adjusted_size), reasoning

    def check_portfolio_risk(self, portfolio: Dict[str, Tuple[int, float]],
                            trading_regime: str = 'NORMAL',
                            var_threshold: float = 0.05) -> RiskReport:
        """
        Generate comprehensive portfolio risk report.

        Args:
            portfolio: Dict of symbol -> (quantity, price)
            trading_regime: Current regime ('NORMAL', 'CHOPPY', 'PANIC')
            var_threshold: Alert if daily VaR > this %

        Returns:
            RiskReport with all risk metrics
        """
        symbols = list(portfolio.keys())

        if not symbols:
            return RiskReport(
                portfolio_var_95=0.0,
                conditional_var_95=0.0,
                max_drawdown_potential=0.0,
                sector_concentration={}
            )

        # Calculate correlations
        correlation_matrix = self.correlation_tracker.update(symbols)

        # Calculate volatilities
        volatilities = {
            sym: self.risk_parity_sizer._calculate_volatility(sym)
            for sym in symbols
        }

        # Calculate VaR metrics
        var_95 = self.portfolio_var.historical_var(portfolio, symbols)
        cvar_95 = self.portfolio_var.conditional_var(portfolio, symbols)

        total_value = sum(qty * price for qty, price in portfolio.values())
        var_pct = var_95 / total_value if total_value > 0 else 0.0

        # Sector concentration
        sector_weights, sector_alerts = self.sector_monitor.check_concentration(
            portfolio, trading_regime
        )

        # Correlation warnings
        correlation_warnings = []
        for i, sym1 in enumerate(symbols):
            for j in range(i + 1, len(symbols)):
                sym2 = symbols[j]
                corr = correlation_matrix[i, j]
                if corr > 0.7:
                    correlation_warnings.append(
                        CorrelationWarning(
                            symbol=sym1,
                            correlated_symbol=sym2,
                            correlation=corr,
                            message=f"High correlation {corr:.2f} between {sym1} "
                                   f"and {sym2}"
                        )
                    )

        # Optimal position count
        win_rate = 0.55  # Conservative estimate
        avg_win_loss_ratio = 1.5
        kelly_f = (win_rate * avg_win_loss_ratio - (1 - win_rate)) / avg_win_loss_ratio

        # Adjust for correlation
        avg_correlation = np.mean(correlation_matrix[np.triu_indices_from(
            correlation_matrix, k=1)])
        correlation_adjustment = 1.0 / (1.0 + avg_correlation)

        optimal_count = max(5, int(len(symbols) / (kelly_f * correlation_adjustment)))

        # Risk score (0-100)
        risk_score = min(100, var_pct * 100 + len(correlation_warnings) * 5 +
                        max(sector_weights.values()) * 50 if sector_weights else 0)

        return RiskReport(
            portfolio_var_95=var_95,
            conditional_var_95=cvar_95,
            max_drawdown_potential=var_pct,
            sector_concentration=sector_weights,
            sector_alerts=sector_alerts,
            correlation_warnings=correlation_warnings,
            position_count_optimal=optimal_count,
            total_risk_score=risk_score
        )

    def suggest_rebalance(self, portfolio: Dict[str, Tuple[int, float]]) -> List[RebalanceSignal]:
        """
        Generate rebalancing suggestions based on position drift.

        Args:
            portfolio: Dict of symbol -> (quantity, price)

        Returns:
            List of RebalanceSignal objects
        """
        signals = []

        symbols = list(portfolio.keys())
        if not symbols:
            return signals

        # Calculate target weights using risk parity
        volatilities = {
            sym: self.risk_parity_sizer._calculate_volatility(sym)
            for sym in symbols
        }

        target_weights = self.risk_parity_sizer.calculate_weights(symbols, volatilities)

        # Adjust for correlation
        correlation_matrix = self.correlation_tracker.update(symbols)
        target_weights = self.risk_parity_sizer.adjust_for_correlation(
            target_weights, correlation_matrix, symbols
        )

        # Calculate current weights
        total_value = sum(qty * price for qty, price in portfolio.values())
        current_weights = {}
        for symbol, (qty, price) in portfolio.items():
            current_weights[symbol] = (qty * price) / total_value if total_value > 0 else 0

        # Generate signals
        for symbol in symbols:
            current = current_weights.get(symbol, 0)
            target = target_weights.get(symbol, 0)

            # Trim if >2x target
            if current > target * 2:
                signal = RebalanceSignal(
                    symbol=symbol,
                    action='TRIM',
                    current_weight=current,
                    target_weight=target,
                    suggested_adjustment=current - target,
                    reason=f"Position grown to {current:.1%}, target is {target:.1%}"
                )
                signals.append(signal)

            # Add if <0.5x target
            elif current < target * 0.5 and target > 0:
                signal = RebalanceSignal(
                    symbol=symbol,
                    action='ADD',
                    current_weight=current,
                    target_weight=target,
                    suggested_adjustment=target - current,
                    reason=f"Position shrunk to {current:.1%}, target is {target:.1%}"
                )
                signals.append(signal)

            # Close if weight <1% and trending down
            elif current < 0.01 and current < target * 0.3:
                signal = RebalanceSignal(
                    symbol=symbol,
                    action='CLOSE',
                    current_weight=current,
                    target_weight=target,
                    suggested_adjustment=-current,
                    reason=f"Position at {current:.1%}, minimal contribution"
                )
                signals.append(signal)

        return signals


# Convenience functions for integration with bot.py and risk.py

def get_optimal_size(symbol: str, base_size: int, portfolio: Dict[str, Tuple[int, float]],
                     capital: float = 100000) -> Tuple[int, str]:
    """
    Get optimal position size adjusted for correlation and risk.

    Called from risk.py's calculate_position_size().

    Args:
        symbol: Ticker to size
        base_size: Base position size
        portfolio: Current portfolio {symbol -> (qty, price)}
        capital: Available capital

    Returns:
        (adjusted_size, reasoning_string)
    """
    optimizer = PortfolioOptimizer()
    return optimizer.get_optimal_position_size(symbol, base_size, portfolio, capital)


def check_portfolio_risk(portfolio: Dict[str, Tuple[int, float]],
                        trading_regime: str = 'NORMAL') -> Dict:
    """
    Generate comprehensive risk report.

    Called from bot.py before entering trades.

    Args:
        portfolio: Current positions {symbol -> (qty, price)}
        trading_regime: Current regime

    Returns:
        Dict with risk metrics and warnings
    """
    optimizer = PortfolioOptimizer()
    report = optimizer.check_portfolio_risk(portfolio, trading_regime)

    return {
        'var_95': report.portfolio_var_95,
        'cvar_95': report.conditional_var_95,
        'max_drawdown_potential': report.max_drawdown_potential,
        'sector_concentration': report.sector_concentration,
        'sector_alerts': report.sector_alerts,
        'correlation_warnings': [
            {
                'symbol': w.symbol,
                'correlated_with': w.correlated_symbol,
                'correlation': w.correlation,
                'message': w.message
            }
            for w in report.correlation_warnings
        ],
        'optimal_position_count': report.position_count_optimal,
        'total_risk_score': report.total_risk_score
    }


def suggest_rebalance(portfolio: Dict[str, Tuple[int, float]]) -> List[Dict]:
    """
    Get rebalancing suggestions.

    Called periodically from bot.py to suggest adjustments.

    Args:
        portfolio: Current positions

    Returns:
        List of rebalance signal dicts
    """
    optimizer = PortfolioOptimizer()
    signals = optimizer.suggest_rebalance(portfolio)

    return [
        {
            'symbol': s.symbol,
            'action': s.action,
            'current_weight': s.current_weight,
            'target_weight': s.target_weight,
            'suggested_adjustment': s.suggested_adjustment,
            'reason': s.reason
        }
        for s in signals
    ]


if __name__ == '__main__':
    # Example usage
    logging.basicConfig(level=logging.INFO)

    # Sample portfolio
    sample_portfolio = {
        'NVDA': (100, 875.50),
        'AMD': (150, 185.25),
        'INTC': (200, 45.75)
    }

    # Create optimizer and run analysis
    optimizer = PortfolioOptimizer()

    # Check risk
    risk_report = optimizer.check_portfolio_risk(sample_portfolio, 'NORMAL')
    print(f"\nPortfolio Risk Report:")
    print(f"  VaR (95%): ${risk_report.portfolio_var_95:,.2f}")
    print(f"  CVaR (95%): ${risk_report.conditional_var_95:,.2f}")
    print(f"  Max Drawdown: {risk_report.max_drawdown_potential:.2%}")
    print(f"  Risk Score: {risk_report.total_risk_score:.1f}/100")
    print(f"  Sector Weights: {risk_report.sector_concentration}")

    # Get rebalance suggestions
    signals = optimizer.suggest_rebalance(sample_portfolio)
    if signals:
        print(f"\nRebalancing Suggestions:")
        for signal in signals:
            print(f"  {signal.symbol}: {signal.action} "
                  f"({signal.current_weight:.1%} -> {signal.target_weight:.1%})")

    # Check new position
    warnings = optimizer.check_new_position('AVGO', list(sample_portfolio.keys()))
    if warnings:
        print(f"\nCorrelation Warnings for AVGO:")
        for warning in warnings:
            print(f"  {warning.message}")
