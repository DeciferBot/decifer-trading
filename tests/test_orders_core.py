"""
test_orders_core.py - Comprehensive tests for orders.py module

Tests core order execution logic:
- execute_buy: bracket orders, position tracking, risk checks
- execute_sell: position closing, P&L recording
- _validate_position_price: 3-way price consensus logic
- Thread safety: open_trades + _trades_lock
"""
import os, sys, types
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance",
             "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod
_cfg = {"log_file": "/dev/null", "trade_log": "/dev/null",
        "order_log": "/dev/null", "anthropic_api_key": "test-key",
        "model": "claude-sonnet-4-20250514", "max_tokens": 1000,
        "mongo_uri": "", "db_name": "test"}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


import pytest
import threading
import time
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

# Import orders module (conftest has already patched dependencies)
import orders


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG FIXTURE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_config():
    """Create a complete CONFIG dict matching orders.py expectations."""
    return {
        # IBKR Connection
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 7496,
        "ibkr_client_id": 10,
        "active_account": "DUP481326",

        # Accounts
        "accounts": {
            "paper": "DUP481326",
            "live_1": "U3059777",
        },

        # Risk Management
        "risk_pct_per_trade": 0.03,
        "max_positions": 5,  # Small for testing
        "daily_loss_limit": 0.10,
        "max_drawdown_alert": 0.25,
        "min_cash_reserve": 0.05,
        "max_single_position": 0.10,
        "max_sector_exposure": 0.50,
        "consecutive_loss_pause": 8,
        "max_portfolio_allocation": 1.0,
        "starting_capital": 100_000,

        # Stops & TP
        "atr_stop_multiplier": 1.5,
        "atr_trail_multiplier": 2.0,
        "partial_exit_1_pct": 0.04,
        "partial_exit_2_pct": 0.08,
        "min_reward_risk_ratio": 1.5,  # IMPORTANT for R:R validation
        "gap_protection_pct": 0.03,

        # Scanning & Scoring
        "agents_required_to_agree": 2,
        "scan_interval_prime": 3,
        "min_score_to_trade": 18,
        "high_conviction_score": 30,

        # Market Hours
        "pre_market_start": "04:00",
        "market_open": "09:30",
        "prime_start": "09:45",
        "lunch_start": "11:30",
        "afternoon_start": "14:00",
        "close_buffer": "15:55",
        "market_close": "16:00",
        "after_hours_end": "20:00",

        # Indicators
        "ema_fast": 9,
        "ema_slow": 21,
        "ema_trend": 50,
        "rsi_period": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_period": 14,
        "volume_surge_multiplier": 1.5,
        "keltner_period": 20,
        "keltner_atr_period": 10,
        "keltner_multiplier": 1.5,
        "donchian_period": 20,

        # Dashboard
        "dashboard_port": 8080,

        # VIX Thresholds
        "vix_bull_max": 15,
        "vix_choppy_max": 25,
        "vix_panic_min": 35,
        "vix_spike_pct": 0.20,

        # Inverse ETFs
        "inverse_etfs": {
            "market_short": "SPXS",
            "tech_short": "SQQQ",
            "vix_long": "UVXY",
        },

        # Logging
        "log_file": "logs/decifer.log",
        "trade_log": "data/trades.json",
        "order_log": "data/orders.json",

        # Options
        "options_enabled": True,
        "options_min_score": 35,
        "options_max_ivr": 65,
        "options_target_delta": 0.50,
        "options_delta_range": 0.35,
        "options_min_dte": 5,
        "options_max_dte": 45,
        "options_min_volume": 25,
        "options_min_oi": 100,
        "options_max_spread_pct": 0.35,
        "options_max_risk_pct": 0.025,
    }


# ─────────────────────────────────────────────────────────────────────────────
# IB MOCK FIXTURE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ib():
    """Create a mock IB object with necessary methods for order placement."""
    ib = MagicMock()

    # Mock qualifyContracts — returns the contract unchanged
    def qualify_contracts(contract):
        return [contract]
    ib.qualifyContracts.side_effect = qualify_contracts

    # Mock placeOrder — returns a mock Trade with order info
    def place_order(contract, order):
        trade = MagicMock()
        trade.order.orderId = getattr(order, 'orderId', 12345)
        trade.orderStatus.status = "Submitted"
        return trade
    ib.placeOrder.side_effect = place_order

    # Mock sleep — no-op
    ib.sleep.return_value = None

    # Mock reqTickers — returns a ticker with price
    def req_tickers(contract):
        ticker = MagicMock()
        ticker.marketPrice.return_value = 100.0
        ticker.last = 100.0
        ticker.close = 100.0
        return [ticker]
    ib.reqTickers.side_effect = req_tickers

    # Mock portfolio — returns empty list by default
    ib.portfolio.return_value = []

    # Mock openOrders — returns empty list by default
    ib.openOrders.return_value = []

    return ib


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: Module-level globals
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleGlobals:
    """Test that module-level globals exist and have correct types."""

    def test_open_trades_exists_and_is_dict(self):
        """open_trades should be an empty dict on module import."""
        # Since conftest pre-imports, we need to check the actual object
        assert isinstance(orders.open_trades, dict)

    def test_trades_lock_exists_and_is_rlock(self):
        """_trades_lock should be a threading.RLock."""
        assert isinstance(orders._trades_lock, type(threading.RLock()))


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: execute_buy
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteBuy:
    """Test execute_buy order placement logic."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_config, mock_ib):
        """Reset open_trades before each test."""
        orders.open_trades.clear()
        self.mock_config = mock_config
        self.mock_ib = mock_ib

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_happy_path_returns_true(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """Happy path: execute_buy places bracket order and returns True."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 100.0
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")
        mock_stops.return_value = (98.0, 105.0)  # SL, TP
        mock_position_size.return_value = 100  # qty
        mock_log_order.return_value = None

        # Call execute_buy
        result = orders.execute_buy(
            ib=mock_ib,
            symbol="AAPL",
            price=100.0,
            atr=2.0,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
            reasoning="Test trade"
        )

        assert result is True
        assert "AAPL" in orders.open_trades
        assert orders.open_trades["AAPL"]["symbol"] == "AAPL"
        assert orders.open_trades["AAPL"]["qty"] == 100

    @patch('orders.CONFIG')
    @patch('orders.check_correlation')
    def test_execute_buy_duplicate_symbol_returns_false(
        self, mock_correlation, mock_config_obj, mock_config, mock_ib
    ):
        """execute_buy should reject if symbol already in open_trades."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]

        # Pre-populate open_trades
        orders.open_trades["AAPL"] = {"symbol": "AAPL", "qty": 100}

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="AAPL",
            price=100.0,
            atr=2.0,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
        )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders.check_correlation')
    def test_execute_buy_max_positions_returns_false(
        self, mock_correlation, mock_config_obj, mock_config, mock_ib
    ):
        """execute_buy should reject if max_positions reached."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]

        # Fill up open_trades to max
        for i in range(mock_config["max_positions"]):
            orders.open_trades[f"SYM{i}"] = {"symbol": f"SYM{i}", "qty": 100}

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="NEWSTOCK",
            price=100.0,
            atr=2.0,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
        )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders.check_correlation')
    def test_execute_buy_correlation_block_returns_false(
        self, mock_correlation, mock_config_obj, mock_config, mock_ib
    ):
        """execute_buy should reject if correlation check fails."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_correlation.return_value = (False, "Correlated with existing position")

        orders.open_trades["MSFT"] = {"symbol": "MSFT", "qty": 100}

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="AAPL",
            price=100.0,
            atr=2.0,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
        )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_poor_rr_returns_false(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should reject if R:R ratio is below threshold."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 100.0
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")
        # SL too close, poor R:R
        mock_stops.return_value = (99.5, 100.5)  # SL = 0.5, TP = 0.5 (R:R = 1.0 < 1.5)
        mock_position_size.return_value = 100

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="AAPL",
            price=100.0,
            atr=2.0,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
        )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_price_too_low_returns_false(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should reject prices under $1 (contaminated data)."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 0.50  # Too low
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="PENNY",
            price=0.50,
            atr=0.02,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
        )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_price_too_high_returns_false(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should reject prices over $10,000 (contaminated data)."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 50000.0  # Too high
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="BADDATA",
            price=50000.0,
            atr=2000.0,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
        )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_no_price_data_returns_false(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should reject if no price data from any source."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")

        # All sources return 0
        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 0
        # Mock _get_ibkr_price to return 0
        with patch('orders._get_ibkr_price', return_value=0):
            result = orders.execute_buy(
                ib=mock_ib,
                symbol="NOPRICE",
                price=0,
                atr=2.0,
                score=30,
                portfolio_value=100_000,
                regime={"regime": "bull"},
            )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_price_contamination_blocks_trade(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should reject if sources diverge >50%."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")
        # Diverging sources: 100 vs 200 = 50% divergence
        mock_tv_cache.return_value = {"SYM": {"tv_close": 200.0}}
        mock_yf_price.return_value = 100.0
        with patch('orders._get_ibkr_price', return_value=100.0):
            result = orders.execute_buy(
                ib=mock_ib,
                symbol="SYM",
                price=100.0,
                atr=2.0,
                score=30,
                portfolio_value=100_000,
                regime={"regime": "bull"},
            )

        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: execute_sell
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteSell:
    """Test execute_sell order execution logic."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_config, mock_ib):
        """Reset open_trades before each test."""
        orders.open_trades.clear()
        self.mock_config = mock_config
        self.mock_ib = mock_ib

    @patch('orders.CONFIG')
    @patch('orders._validate_position_price')
    @patch('orders._get_ibkr_price')
    @patch('orders.log_order')
    @patch('orders.record_win')
    def test_execute_sell_happy_path_returns_true(
        self, mock_record_win, mock_log_order, mock_ibkr_price,
        mock_validate_price, mock_config_obj, mock_config, mock_ib
    ):
        """Happy path: execute_sell closes position and returns True."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]

        # Setup open position
        orders.open_trades["AAPL"] = {
            "symbol": "AAPL",
            "qty": 100,
            "entry": 100.0,
            "current": 100.0,
            "direction": "LONG",
        }

        mock_ibkr_price.return_value = 105.0
        mock_validate_price.return_value = (105.0, "IBKR=$105.00")
        mock_record_win.return_value = None

        result = orders.execute_sell(
            ib=mock_ib,
            symbol="AAPL",
            reason="Test close"
        )

        assert result is True
        assert "AAPL" not in orders.open_trades

    @patch('orders.CONFIG')
    def test_execute_sell_nonexistent_position_returns_false(
        self, mock_config_obj, mock_config, mock_ib
    ):
        """execute_sell should return False if symbol not in open_trades."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]

        result = orders.execute_sell(
            ib=mock_ib,
            symbol="NONEXISTENT",
            reason="Test"
        )

        assert result is False

    @patch('orders.CONFIG')
    @patch('orders._validate_position_price')
    @patch('orders._get_ibkr_price')
    @patch('orders.log_order')
    @patch('orders.record_loss')
    def test_execute_sell_loss_records_loss(
        self, mock_record_loss, mock_log_order, mock_ibkr_price,
        mock_validate_price, mock_config_obj, mock_config, mock_ib
    ):
        """execute_sell should call record_loss if exit price < entry."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]

        orders.open_trades["AAPL"] = {
            "symbol": "AAPL",
            "qty": 100,
            "entry": 100.0,
            "current": 100.0,
            "direction": "LONG",
        }

        mock_ibkr_price.return_value = 95.0  # Loss
        mock_validate_price.return_value = (95.0, "IBKR=$95.00")

        result = orders.execute_sell(
            ib=mock_ib,
            symbol="AAPL",
            reason="Stop hit"
        )

        assert result is True
        mock_record_loss.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: _validate_position_price
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatePositionPrice:
    """Test 3-way price consensus validation."""

    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    def test_validate_position_price_no_sources_returns_zero(
        self, mock_yf_price, mock_tv_cache
    ):
        """If all sources invalid, return (0, reason)."""
        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 0

        price, src = orders._validate_position_price("SYM", ibkr_price=0, entry=100.0)

        assert price == 0
        assert "No price data" in src

    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    def test_validate_position_price_single_source_works(
        self, mock_yf_price, mock_tv_cache
    ):
        """Single valid source should be accepted if within 50% of entry."""
        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 0

        price, src = orders._validate_position_price("SYM", ibkr_price=100.0, entry=100.0)

        assert price == 100.0
        assert "IBKR" in src

    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    def test_validate_position_price_single_source_outlier_rejected(
        self, mock_yf_price, mock_tv_cache
    ):
        """Single source >50% from entry should be rejected."""
        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 0

        # 49/100 = 51% divergence from entry, exceeds 50% threshold
        price, src = orders._validate_position_price("SYM", ibkr_price=49.0, entry=100.0)

        assert price == 0
        assert "too far from entry" in src

    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    def test_validate_position_price_two_sources_agreeing(
        self, mock_yf_price, mock_tv_cache
    ):
        """Two sources within 50% should use closest to entry."""
        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 100.5

        price, src = orders._validate_position_price("SYM", ibkr_price=100.0, entry=100.0)

        assert price > 0
        assert price in (100.0, 100.5) or price == round((100.0 + 100.5) / 2, 4)

    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    def test_validate_position_price_two_sources_diverging(
        self, mock_yf_price, mock_tv_cache
    ):
        """Two sources diverging >50% should be rejected."""
        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 210.0  # 52.4% divergence from IBKR (>50%)

        price, src = orders._validate_position_price("SYM", ibkr_price=100.0, entry=100.0)

        assert price == 0
        assert "divergence" in src.lower()

    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    def test_validate_position_price_three_sources_consensus(
        self, mock_yf_price, mock_tv_cache
    ):
        """Three sources within 50% should use median."""
        mock_tv_cache.return_value = {"SYM": {"tv_close": 101.0}}
        mock_yf_price.return_value = 100.5

        price, src = orders._validate_position_price("SYM", ibkr_price=100.0, entry=100.0)

        assert price > 0
        assert "consensus" in src.lower() or price == 100.5  # median

    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    def test_validate_position_price_three_sources_one_outlier(
        self, mock_yf_price, mock_tv_cache
    ):
        """Three sources with one outlier should reject outlier and use other two."""
        mock_tv_cache.return_value = {"SYM": {"tv_close": 300.0}}  # Outlier
        mock_yf_price.return_value = 100.5

        price, src = orders._validate_position_price("SYM", ibkr_price=100.0, entry=100.0)

        assert price > 0
        # Should use IBKR + yfinance, not TV
        assert price in (100.0, 100.5) or abs(price - 100.25) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: Thread Safety
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety:
    """Test that open_trades is thread-safe with _trades_lock."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset open_trades before each test."""
        orders.open_trades.clear()

    def test_concurrent_writes_do_not_corrupt_dict(self):
        """Two threads writing to open_trades concurrently should not corrupt."""
        num_writes = 100

        def thread_1_work():
            for i in range(num_writes):
                orders._safe_set_trade(f"T1_{i}", {"data": f"thread1_{i}"})
                time.sleep(0.0001)

        def thread_2_work():
            for i in range(num_writes):
                orders._safe_set_trade(f"T2_{i}", {"data": f"thread2_{i}"})
                time.sleep(0.0001)

        t1 = threading.Thread(target=thread_1_work)
        t2 = threading.Thread(target=thread_2_work)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # All writes should be present
        assert len(orders.open_trades) == num_writes * 2
        for i in range(num_writes):
            assert f"T1_{i}" in orders.open_trades
            assert f"T2_{i}" in orders.open_trades

    def test_safe_set_trade_under_lock(self):
        """_safe_set_trade should use lock."""
        orders._safe_set_trade("TEST", {"value": 1})
        assert orders.open_trades["TEST"]["value"] == 1

    def test_safe_del_trade_under_lock(self):
        """_safe_del_trade should safely remove entry."""
        orders._safe_set_trade("TEST", {"value": 1})
        orders._safe_del_trade("TEST")
        assert "TEST" not in orders.open_trades

    def test_safe_del_nonexistent_doesnt_raise(self):
        """_safe_del_trade on non-existent key should not raise."""
        orders._safe_del_trade("NONEXISTENT")
        # Should pass without exception

    def test_concurrent_read_write_race(self):
        """Concurrent reads and writes should not crash or corrupt."""
        errors = []

        def writer():
            try:
                for i in range(50):
                    orders._safe_set_trade(f"pos_{i}", {"qty": i * 10})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    _ = dict(orders.open_trades)  # Snapshot
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: Edge Cases & Integration
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_config, mock_ib):
        """Reset open_trades before each test."""
        orders.open_trades.clear()

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_qty_capped_at_5000(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should cap qty at 5000 shares."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 0  # No yfinance fallback
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")
        mock_stops.return_value = (95.0, 115.0)  # Reasonable stops for $100 price
        mock_position_size.return_value = 10000  # Exceeds 5000 hard cap

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="CHEAP",
            price=100.0,  # Must match IBKR mock price to avoid contamination check
            atr=2.0,
            score=30,
            portfolio_value=5_000_000,  # Very large portfolio to avoid 20% cap conflict
            regime={"regime": "bull"},
        )

        # Should still succeed but qty should be capped at 5000
        assert result is True
        assert orders.open_trades["CHEAP"]["qty"] <= 5000

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_order_value_capped_at_20pct_portfolio(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should cap order value at 20% of portfolio."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        portfolio = 100_000
        price = 100.0

        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = price
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")
        mock_stops.return_value = (95.0, 110.0)
        # Position size would be 3000 shares = $300,000 (300% of portfolio)
        mock_position_size.return_value = 3000

        result = orders.execute_buy(
            ib=mock_ib,
            symbol="EXPENSIVE",
            price=price,
            atr=5.0,
            score=30,
            portfolio_value=portfolio,
            regime={"regime": "bull"},
        )

        assert result is True
        # Qty should be capped: max_order_value = 20_000, qty = 20_000 / 100 = 200
        actual_qty = orders.open_trades["EXPENSIVE"]["qty"]
        assert actual_qty == 200

    def test_open_trades_starts_empty(self):
        """open_trades should start as empty dict."""
        orders.open_trades.clear()
        assert orders.open_trades == {}

    @patch('orders.CONFIG')
    @patch('orders.calculate_position_size')
    @patch('orders.calculate_stops')
    @patch('orders.check_correlation')
    @patch('orders.check_combined_exposure')
    @patch('orders.check_sector_concentration')
    @patch('orders.get_tv_signal_cache')
    @patch('orders._get_yf_price')
    @patch('orders.log_order')
    def test_execute_buy_reason_parameter_stored(
        self, mock_log_order, mock_yf_price, mock_tv_cache,
        mock_sector, mock_exposure, mock_correlation,
        mock_stops, mock_position_size, mock_config_obj,
        mock_config, mock_ib
    ):
        """execute_buy should store the reasoning parameter."""
        mock_config_obj.__getitem__.side_effect = lambda k: mock_config[k]
        mock_config_obj.get.side_effect = lambda k, default=None: mock_config.get(k, default)

        mock_tv_cache.return_value = {}
        mock_yf_price.return_value = 100.0
        mock_correlation.return_value = (True, "OK")
        mock_exposure.return_value = (True, "OK")
        mock_sector.return_value = (True, "OK")
        mock_stops.return_value = (98.0, 105.0)
        mock_position_size.return_value = 100

        reason = "Breakout above EMA with high RSI"
        result = orders.execute_buy(
            ib=mock_ib,
            symbol="AAPL",
            price=100.0,
            atr=2.0,
            score=30,
            portfolio_value=100_000,
            regime={"regime": "bull"},
            reasoning=reason,
        )

        assert result is True
        assert orders.open_trades["AAPL"]["reasoning"] == reason
