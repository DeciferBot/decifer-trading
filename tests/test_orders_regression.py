"""
test_orders_regression.py — Regression tests for orders.py critical bug fixes.

Covers the bugs fixed in commits 62078f6 and 5a4662a:
  1. Orphaned SL reattachment on reconcile_with_ibkr (62078f6)
     - Reattach existing live SL order after reconnect
     - Re-submit new SL when none found in IBKR
  2. Deferred option exits never drained (62078f6)
     - execute_sell_option defers to _pending_option_exits when market closed
     - flush_pending_option_exits drains the queue on market open
     - flush_pending_option_exits is a no-op when market is closed
  3. execute_sell_option state machine EXITING lock (62078f6)
     - Status resets to ACTIVE on unfilled order (not stuck as EXITING)
     - Status resets to ACTIVE on paper-account false fill (avgFillPrice=0)
     - Duplicate exit blocked when status is already EXITING
  4. execute_sell_option SHORT direction pricing (62078f6)
     - SHORT uses ask on attempt 0 (not bid)
     - SHORT steps up on retry (not down)
  5. execute_sell finds option by composite key (5a4662a)
     - execute_sell("GSAT") finds "GSAT_C_35.0_2026-04-17" via symbol field

Each test is a direct regression for a specific code path added in the fix.
"""

import os
import sys
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg

from datetime import UTC, datetime, timedelta

import pytest

# Evict any hollow stubs planted by earlier test files (e.g. test_bot.py
# installs bare types.ModuleType() for "orders" which lacks all attributes)
for _decifer_mod in ("orders", "risk", "scanner", "signals", "news", "agents"):
    sys.modules.pop(_decifer_mod, None)

# Import the REAL orders module (conftest has already patched all heavy deps)
import orders  # noqa: E402 — must follow stub setup above


# ─────────────────────────────────────────────────────────────────────────────
# SHARED CONFIG FIXTURE  (mirrors test_orders_core.py mock_config)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_config():
    return {
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 7496,
        "ibkr_client_id": 10,
        "active_account": "DUP481326",
        "accounts": {"paper": "DUP481326", "live_1": "U3059777"},
        "risk_pct_per_trade": 0.03,
        "max_positions": 5,
        "daily_loss_limit": 0.10,
        "max_drawdown_alert": 0.25,
        "min_cash_reserve": 0.05,
        "max_single_position": 0.10,
        "max_sector_exposure": 0.50,
        "consecutive_loss_pause": 8,
        "max_portfolio_allocation": 1.0,
        "starting_capital": 100_000,
        "atr_stop_multiplier": 1.5,
        "atr_trail_multiplier": 2.0,
        "partial_exit_1_pct": 0.04,
        "partial_exit_2_pct": 0.08,
        "min_reward_risk_ratio": 1.5,
        "gap_protection_pct": 0.03,
        "agents_required_to_agree": 2,
        "scan_interval_prime": 3,
        "min_score_to_trade": 18,
        "high_conviction_score": 30,
        "pre_market_start": "04:00",
        "market_open": "09:30",
        "prime_start": "09:45",
        "lunch_start": "11:30",
        "afternoon_start": "14:00",
        "close_buffer": "15:55",
        "market_close": "16:00",
        "after_hours_end": "20:00",
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
        "dashboard_port": 8080,
        "vix_bull_max": 15,
        "vix_choppy_max": 25,
        "vix_panic_min": 35,
        "vix_spike_pct": 0.20,
        "inverse_etfs": {"market_short": "SPXS", "tech_short": "SQQQ", "vix_long": "UVXY"},
        "log_file": "logs/decifer.log",
        "trade_log": "data/trades.json",
        "order_log": "data/orders.json",
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
        "reentry_cooldown_minutes": 30,
        "trailing_stop_enabled": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLASS 1: Orphaned Stop-Loss Reattachment  (commit 62078f6, lines 1801–1831)
# ─────────────────────────────────────────────────────────────────────────────


class TestOrphanedStopLoss:
    """
    reconcile_with_ibkr must reattach or re-submit the stop-loss bracket after
    a reconnect. Before the fix, both paths were missing: positions recovered
    from IBKR had no SL protection until they were manually closed.
    """

    def _make_stock_portfolio_item(self, symbol="TSLA", position=10, avg_cost=100.0, mkt_price=102.0):
        """Build a minimal MagicMock that looks like an ib_insync portfolio item."""
        item = MagicMock()
        item.position = position
        item.averageCost = avg_cost
        item.marketPrice = mkt_price
        item.contract.symbol = symbol
        item.contract.secType = "STK"
        item.contract.right = ""  # empty → _is_option_contract returns False
        item.contract.strike = 0
        return item

    def _make_ib(self, portfolio_items, open_trades_list, sl_order_id=8888):
        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.portfolio.return_value = portfolio_items
        ib.openTrades.return_value = open_trades_list
        ib.sleep.return_value = None
        ib.reqExecutions.return_value = []
        ib.placeOrder.return_value.order.orderId = sl_order_id
        # placeOrder returns a trade whose order has a known orderId
        new_sl_trade = MagicMock()
        new_sl_trade.order.orderId = sl_order_id
        ib.placeOrder.return_value = new_sl_trade
        return ib

    def _make_sl_open_trade(self, symbol="TSLA", order_id=9999, order_type="STP", action="SELL"):
        """Simulate an existing stop-loss order live in IBKR."""
        t = MagicMock()
        t.contract.symbol = symbol
        t.order.orderType = order_type
        t.order.action = action
        t.order.orderId = order_id
        t.orderStatus.status = "Submitted"
        return t

    def test_reconcile_reattaches_existing_sl_order(self, mock_config):
        """
        Reconcile adds the position to active_trades but does NOT place a new SL order.
        sl_order_id reattachment is handled by bracket_health.audit_bracket_orders()
        on the next scan cycle — reconcile is no longer responsible for it.
        """
        _om = sys.modules["orders"]
        _om.active_trades.clear()

        item = self._make_stock_portfolio_item()
        sl_trade = self._make_sl_open_trade(symbol="TSLA", order_id=9999)
        ib = self._make_ib([item], [sl_trade])

        with (
            patch("orders.CONFIG", mock_config),
            patch("orders._validate_position_price", return_value=(102.0, "IBKR")),
            patch("event_log.open_trades", return_value={}),
            patch("orders_portfolio._load_positions_file", return_value={}),
            patch("orders_portfolio._save_positions_file"),
        ):
            _om.reconcile_with_ibkr(ib)

        assert "TSLA" in _om.active_trades, "Position must be added by reconcile"
        ib.placeOrder.assert_not_called()  # reconcile never places SL orders

    def test_reconcile_does_not_place_sl_when_none_found_in_ibkr(self, mock_config):
        """
        Reconcile adds the position to active_trades but does NOT place a new SL order
        even when IBKR has no matching stop. Missing SL submission is handled by
        bracket_health.audit_bracket_orders() on the next scan cycle.
        """
        _om = sys.modules["orders"]
        _om.active_trades.clear()

        item = self._make_stock_portfolio_item()
        ib = self._make_ib([item], [], sl_order_id=8888)  # no open SL in IBKR

        with (
            patch("orders.CONFIG", mock_config),
            patch("orders._validate_position_price", return_value=(102.0, "IBKR")),
            patch("event_log.open_trades", return_value={}),
            patch("orders_portfolio._load_positions_file", return_value={}),
            patch("orders_portfolio._save_positions_file"),
        ):
            _om.reconcile_with_ibkr(ib)

        assert "TSLA" in _om.active_trades, "Position must be added by reconcile"
        ib.placeOrder.assert_not_called()  # reconcile never places SL orders


# ─────────────────────────────────────────────────────────────────────────────
# CLASS 2: Deferred Option Exits  (commit 62078f6, lines 2244–2252, 2510–2524)
# ─────────────────────────────────────────────────────────────────────────────


class TestDeferredOptionExits:
    """
    execute_sell_option defers exits to _pending_option_exits when the options
    market is closed. flush_pending_option_exits drains that queue on next open.

    Before fix: _pending_option_exits was populated but NEVER drained — exits
    were silently dropped each scan cycle.
    After fix: flush_pending_option_exits is called at scan start and drains
    the queue by re-calling execute_sell_option with reason="deferred:<orig>".
    """

    OPT_KEY = "TEST_C_35.0_2026-06-20"

    def _opt_pos(self, direction="LONG"):
        return {
            "symbol": "TEST",
            "instrument": "option",
            "right": "C",
            "strike": 35.0,
            "expiry_ibkr": "20260620",
            "expiry_str": "2026-06-20",
            "contracts": 2,
            "qty": 2,
            "entry": 3.50,
            "entry_premium": 3.50,
            "current_premium": 2.50,
            "direction": direction,
            "status": "ACTIVE",
        }

    def test_execute_sell_option_defers_when_market_closed(self):
        """
        Regression: when is_options_market_open() is False, execute_sell_option
        must add opt_key to _pending_option_exits and return False without
        touching IBKR.

        Before fix: this path existed but the queue was never consumed.
        This test pins that the deferral itself is correct.
        """
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om._pending_option_exits.clear()

        ib = MagicMock()

        with patch("orders_options.is_options_market_open", return_value=False):
            result = _om.execute_sell_option(ib, self.OPT_KEY, reason="signal")

        assert result is False
        assert _om._pending_option_exits.get(self.OPT_KEY) == "signal", (
            "Deferred exit must be stored in _pending_option_exits"
        )
        ib.placeOrder.assert_not_called()

    def test_flush_pending_option_exits_drains_on_market_open(self):
        """
        Regression: flush_pending_option_exits must call execute_sell_option
        for every entry in _pending_option_exits when the market is open.

        Before fix: this function did not exist — queue never drained.
        After fix: each pending key is consumed with reason="deferred:<orig>".
        """
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om._pending_option_exits.clear()

        _om._pending_option_exits[self.OPT_KEY] = "signal"
        _om.active_trades[self.OPT_KEY] = self._opt_pos()

        ib = MagicMock()

        with (
            patch("orders_options.is_options_market_open", return_value=True),
            patch("orders_options.execute_sell_option") as mock_sell,
        ):
            _om.flush_pending_option_exits(ib)

        mock_sell.assert_called_once_with(ib, self.OPT_KEY, reason="deferred:signal")
        assert self.OPT_KEY not in _om._pending_option_exits, (
            "Processed entry must be removed from _pending_option_exits"
        )

    def test_flush_pending_option_exits_noop_when_market_closed(self):
        """
        flush_pending_option_exits must not process the queue when the market
        is closed — it will be retried on the next scan cycle when open.
        """
        _om = sys.modules["orders"]
        _om._pending_option_exits.clear()
        _om._pending_option_exits[self.OPT_KEY] = "signal"

        ib = MagicMock()

        with (
            patch("orders_options.is_options_market_open", return_value=False),
            patch("orders_options.execute_sell_option") as mock_sell,
        ):
            _om.flush_pending_option_exits(ib)

        mock_sell.assert_not_called()
        assert self.OPT_KEY in _om._pending_option_exits, "Deferred exit must still be in queue when market is closed"


# ─────────────────────────────────────────────────────────────────────────────
# CLASS 3: Option Sell State Machine  (commit 62078f6, lines 2294, 2404, 2423)
# ─────────────────────────────────────────────────────────────────────────────


class TestOptionSellStateMachine:
    """
    execute_sell_option must reset status to ACTIVE on all failure paths so
    the position is never stuck as EXITING and can be retried.

    Before fix: status was set to EXITING before the cooldown check. Any
    exception or unfilled order left the position permanently locked.
    After fix: EXITING is set after cooldown check; all failure paths call
    _safe_update_trade(opt_key, {"status": "ACTIVE"}).
    """

    OPT_KEY = "NVDA_C_900.0_2026-06-20"

    def _opt_pos(self):
        return {
            "symbol": "NVDA",
            "instrument": "option",
            "right": "C",
            "strike": 900.0,
            "expiry_ibkr": "20260620",
            "expiry_str": "2026-06-20",
            "contracts": 1,
            "qty": 1,
            "entry": 10.00,
            "entry_premium": 10.00,
            "current_premium": 8.00,
            "direction": "LONG",
            "status": "ACTIVE",
        }

    def _make_ib(self, order_status="Cancelled", filled=0, avg_fill_price=None):
        """Build IB mock that returns a trade with the given fill status."""
        ib = MagicMock()
        ticker = MagicMock()
        ticker.bid = 8.00
        ticker.ask = 8.20
        ticker.last = 8.00
        ib.reqMktData.return_value = ticker

        trade = MagicMock()
        trade.orderStatus.status = order_status
        trade.orderStatus.filled = filled
        trade.orderStatus.avgFillPrice = avg_fill_price
        ib.placeOrder.return_value = trade
        return ib

    def _run(self, mock_config, ib):
        """Run execute_sell_option with standard patches; return the result."""
        import sys as _sys

        _om = _sys.modules["orders"]
        _om.active_trades.clear()
        _om._option_sell_attempts.clear()
        _om.active_trades[self.OPT_KEY] = self._opt_pos()

        with (
            patch("orders_options.is_options_market_open", return_value=True),
            patch("orders_options.CONFIG") as mock_cfg,
            patch("orders_options.log_order"),
            patch("orders_options.record_win"),
            patch("orders_options.record_loss"),
            patch("learning.log_order"),
            patch("learning._save_orders"),
            patch("learning._save_trades"),
            patch("learning.log_trade"),
        ):
            mock_cfg.__getitem__.side_effect = lambda k: mock_config[k]
            mock_cfg.get = lambda k, d=None: mock_config.get(k, d)
            return _om.execute_sell_option(ib, self.OPT_KEY, reason="test")

    def test_status_resets_to_active_on_unfilled_order(self, mock_config):
        """
        Regression: if the limit order is Cancelled without a fill, the
        position status must return to ACTIVE so the next scan can retry.

        Before fix: status was left as EXITING — position permanently locked.
        After fix:  _safe_update_trade(opt_key, {"status": "ACTIVE"}) on line 2404.
        """
        _om = sys.modules["orders"]
        ib = self._make_ib(order_status="Cancelled", filled=0, avg_fill_price=0)

        result = self._run(mock_config, ib)

        assert result is False
        assert _om.active_trades.get(self.OPT_KEY, {}).get("status") == "ACTIVE", (
            "Status must reset to ACTIVE after unfilled order (not stuck as EXITING)"
        )

    def test_status_resets_to_active_on_false_fill(self, mock_config):
        """
        Regression: paper accounts can report status=Filled with avgFillPrice=0.
        This is a false fill — the position must reset to ACTIVE and be retried.

        Before fix: status was left as EXITING on this false-fill path.
        After fix:  _safe_update_trade(opt_key, {"status": "ACTIVE"}) on line 2423.
        """
        _om = sys.modules["orders"]
        ib = self._make_ib(order_status="Filled", filled=0, avg_fill_price=0.0)

        result = self._run(mock_config, ib)

        assert result is False
        assert _om.active_trades.get(self.OPT_KEY, {}).get("status") == "ACTIVE", (
            "Status must reset to ACTIVE after paper-account false fill"
        )

    def test_duplicate_exit_blocked_when_already_exiting(self, mock_config):
        """
        Regression: if status is already EXITING (exit in flight), a second
        call must return False immediately without placing another order.

        This prevents double-close race conditions during parallel scan cycles.
        """
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om._option_sell_attempts.clear()

        pos = self._opt_pos()
        pos["status"] = "EXITING"
        _om.active_trades[self.OPT_KEY] = pos

        ib = MagicMock()

        with (
            patch("orders_options.is_options_market_open", return_value=True),
            patch("orders_options.CONFIG") as mock_cfg,
        ):
            mock_cfg.__getitem__.side_effect = lambda k: mock_config[k]
            mock_cfg.get = lambda k, d=None: mock_config.get(k, d)
            result = _om.execute_sell_option(ib, self.OPT_KEY, reason="test")

        assert result is False
        ib.placeOrder.assert_not_called()

    def test_sell_blocked_when_buy_in_flight_reserved(self, mock_config):
        """
        Regression: IBKR rejects with "Cannot have open orders on both sides of
        the same US Option contract" when a BUY and SELL are simultaneously open.

        Root cause: execute_options_entries() fires a BUY (setting status=RESERVED)
        earlier in the scan cycle; the PM EXIT loop then finds the RESERVED slot in
        active_trades and calls execute_sell_option — submitting a SELL before the
        BUY fills.

        Fix: execute_sell_option must return False immediately when status is RESERVED.
        """
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om._option_sell_attempts.clear()

        pos = self._opt_pos()
        pos["status"] = "RESERVED"  # BUY submitted but not yet filled
        _om.active_trades[self.OPT_KEY] = pos

        ib = MagicMock()

        with (
            patch("orders_options.is_options_market_open", return_value=True),
            patch("orders_options.CONFIG") as mock_cfg,
        ):
            mock_cfg.__getitem__.side_effect = lambda k: mock_config[k]
            mock_cfg.get = lambda k, d=None: mock_config.get(k, d)
            result = _om.execute_sell_option(ib, self.OPT_KEY, reason="pm_exit")

        assert result is False, "SELL must be blocked while BUY is in flight (RESERVED)"
        ib.placeOrder.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# CLASS 4: SHORT Option Exit Pricing  (commit 62078f6, lines 2329–2337)
# ─────────────────────────────────────────────────────────────────────────────


class TestShortOptionExit:
    """
    SHORT option positions (e.g. sold calls/puts) close via BUY-to-close.
    The limit price must be at the ASK (not bid) and step UP on retries to
    ensure the buy-to-close fills.

    Before fix: SHORT exits used bid pricing and stepped DOWN, making them
    unlikely to fill — the position remained open.
    After fix:  SHORT uses ask × (1 + retry*0.05) on each attempt.
    """

    OPT_KEY = "GSAT_C_35.0_2026-06-20"

    def _short_pos(self):
        return {
            "symbol": "GSAT",
            "instrument": "option",
            "right": "C",
            "strike": 35.0,
            "expiry_ibkr": "20260620",
            "expiry_str": "2026-06-20",
            "contracts": 2,
            "qty": 2,
            "entry": 3.50,
            "entry_premium": 3.50,
            "current_premium": 2.50,
            "direction": "SHORT",  # BUY-to-close
            "status": "ACTIVE",
        }

    def _make_ib(self, bid=2.00, ask=2.50):
        ib = MagicMock()
        ticker = MagicMock()
        ticker.bid = bid
        ticker.ask = ask
        ticker.last = bid
        ib.reqMktData.return_value = ticker
        # Return Cancelled so we don't need to mock the full success path
        trade = MagicMock()
        trade.orderStatus.status = "Cancelled"
        trade.orderStatus.filled = 0
        trade.orderStatus.avgFillPrice = 0
        ib.placeOrder.return_value = trade
        return ib

    def _run(self, mock_config, ib, retry_count=0):
        """Run execute_sell_option and return (captured_action, captured_price)."""
        import sys as _sys

        _om = _sys.modules["orders"]
        _om.active_trades.clear()
        _om._option_sell_attempts.clear()

        if retry_count > 0:
            _om._option_sell_attempts[self.OPT_KEY] = {
                "count": retry_count,
                # Use a past timestamp so the min-retry-interval guard (90s) is already elapsed.
                "last_try": datetime.now(UTC) - timedelta(seconds=200),
            }

        _om.active_trades[self.OPT_KEY] = self._short_pos()

        captured = {}

        def fake_limit_order(side, qty, price, **kwargs):
            captured["action"] = side
            captured["price"] = price
            obj = MagicMock()
            obj.lmtPrice = price
            return obj

        with (
            patch("orders_options.is_options_market_open", return_value=True),
            patch("orders_options.CONFIG") as mock_cfg,
            patch("orders_options.LimitOrder", side_effect=fake_limit_order),
            patch("orders_options.log_order"),
            patch("orders_options.record_win"),
            patch("orders_options.record_loss"),
            patch("learning.log_order"),
            patch("learning._save_orders"),
            patch("learning._save_trades"),
            patch("learning.log_trade"),
        ):
            mock_cfg.__getitem__.side_effect = lambda k: mock_config[k]
            mock_cfg.get = lambda k, d=None: mock_config.get(k, d)
            _om.execute_sell_option(ib, self.OPT_KEY, reason="test")

        return captured.get("action"), captured.get("price")

    def test_short_option_exit_uses_ask_on_attempt_0(self, mock_config):
        """
        Regression: SHORT position BUY-to-close must use ask price on first attempt.

        Before fix: SHORT exits used bid pricing — orders rarely filled because
        bid < ask, meaning the buy was below the market offer.
        After fix:  limit_price = round(ask * 1.0, 2)  (lines 2332–2333).
        """
        ib = self._make_ib(bid=2.00, ask=2.50)
        action, price = self._run(mock_config, ib, retry_count=0)

        assert action == "BUY", f"SHORT close must use BUY action, got {action}"
        assert price == 2.50, f"Attempt 0 must use ask=2.50, got {price}"

    def test_short_option_exit_steps_up_on_retry(self, mock_config):
        """
        Regression: SHORT retries must step UP (ask × 1.05 per retry) so that
        the BUY-to-close offer increases aggressively to chase fills.

        Before fix: retries stepped DOWN (bid × 0.95), making fills even less likely.
        After fix:  _premium = 1.0 + (retry * 0.05) applied to ask (lines 2331–2333).
        """
        ib = self._make_ib(bid=2.00, ask=2.50)
        action, price = self._run(mock_config, ib, retry_count=1)

        expected = round(2.50 * 1.05, 2)  # ask × (1 + 1×0.05)
        assert action == "BUY", f"SHORT close must use BUY action, got {action}"
        assert price == expected, f"Retry 1 must use ask*1.05={expected}, got {price}"


# ─────────────────────────────────────────────────────────────────────────────
# CLASS 5: execute_sell Composite Key Lookup  (commit 5a4662a, lines 1269–1282)
# ─────────────────────────────────────────────────────────────────────────────


class TestExecuteSellCompositeKey:
    """
    Options are stored in active_trades under composite keys such as
    "GSAT_C_35.0_2026-04-17". Before the fix, execute_sell("GSAT") did a
    direct dict lookup that failed — the sell signal was silently ignored.

    After fix: on a direct key miss, execute_sell searches active_trades by
    the "symbol" field and closes the first match.
    """

    OPT_KEY = "GSAT_C_35.0_2026-04-17"

    def _opt_pos(self):
        return {
            "symbol": "GSAT",  # plain symbol — the search key
            "instrument": "option",
            "right": "C",
            "strike": 35.0,
            "expiry_ibkr": "20260417",
            "expiry_str": "2026-04-17",
            "qty": 2,
            "entry": 3.50,
            "current": 3.50,
            "direction": "LONG",
            "status": "ACTIVE",
        }

    def test_execute_sell_finds_option_by_symbol_field(self, mock_config):
        """
        Regression: execute_sell("GSAT") must find the option position stored
        under the composite key "GSAT_C_35.0_2026-04-17" and initiate its exit.

        Before fix: active_trades["GSAT"] KeyError → function returned False
        with "No open position" warning — the option was never closed.
        After fix:  symbol-field search (line 1275) finds the composite key;
        status is set to EXITING to initiate closure.
        """
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om.recently_closed.clear()
        _om.active_trades[self.OPT_KEY] = self._opt_pos()

        ib = MagicMock()
        # Simulate market order filled within the 2s sleep window (normal happy path).
        ib.placeOrder.return_value.orderStatus.status = "Filled"
        ib.placeOrder.return_value.orderStatus.filled = 100

        with (
            patch("orders.CONFIG", mock_config),
            patch("orders_core.is_equities_extended_hours", return_value=True),
            patch("orders_core.is_options_market_open", return_value=True),
            patch("orders._validate_position_price", return_value=(3.50, "IBKR")),
            patch("orders._get_ibkr_price", return_value=3.50),
            patch("orders.record_win"),
            patch("orders.record_loss"),
            patch("orders.log_order"),
            patch("learning.log_order"),
            patch("learning._save_orders"),
            patch("learning._save_trades"),
            patch("learning.log_trade"),
            patch("fill_watcher.stop_watcher"),
        ):
            result = _om.execute_sell(ib, "GSAT", reason="signal")

        # A True return proves the option was found via the composite key search.
        # False with "No open position" would mean the bug regressed.
        assert result is True, "execute_sell('GSAT') must find and close the option stored under composite key"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Closed-while-down exit price sourcing (Problem 2 fix)
# ─────────────────────────────────────────────────────────────────────────────


class TestClosedWhileDownExitPrice:
    """
    When a position is found in our store but absent from IBKR on reconcile,
    the exit price written to the CLOSE log must use the real IBKR fill price
    when a matching execution exists, and fall back to the last polled market
    price otherwise.

    Covers _resolve_cwd_exit_price and _build_ibkr_execution_index via the
    full reconcile_with_ibkr path.
    """

    def _make_fill(self, symbol, side, avg_price):
        fill = MagicMock()
        fill.contract.symbol = symbol
        fill.execution.side = side
        fill.execution.avgPrice = avg_price
        fill.execution.time = "20260428 10:30:00"
        return fill

    def _make_ib(self, portfolio_items, fills):
        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.portfolio.return_value = portfolio_items
        ib.openTrades.return_value = []
        ib.sleep.return_value = None
        ib.reqExecutions.return_value = fills
        return ib

    def _make_portfolio_item(self, symbol, position=10, price=155.0):
        item = MagicMock()
        item.position = position
        item.contract.symbol = symbol
        item.contract.secType = "STK"
        item.contract.strike = 0
        item.marketPrice = price
        item.averageCost = price
        item.unrealizedPNL = 0.0
        return item

    def test_uses_ibkr_fill_when_execution_exists(self, mock_config):
        """Exit price must be taken from IBKR execution when a SLD fill exists."""
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om.active_trades["NVDA"] = {
            "symbol": "NVDA",
            "status": "ACTIVE",
            "direction": "LONG",
            "entry": 800.0,
            "current": 810.0,
            "qty": 5,
            "trade_type": "INTRADAY",
        }

        # IBKR portfolio has AAPL but NOT NVDA → NVDA was closed while down
        aapl_item = self._make_portfolio_item("AAPL")
        ibkr_fill = self._make_fill("NVDA", "SLD", 825.50)
        ib = self._make_ib([aapl_item], [ibkr_fill])

        logged_outcomes = []

        def _capture_log_trade(trade, agent_outputs, regime, action, outcome):
            if action == "CLOSE":
                logged_outcomes.append(outcome)

        with (
            patch("orders.CONFIG", mock_config),
            patch("event_log.open_trades", return_value={}),
            patch("orders_portfolio._load_positions_file", return_value={}),
            patch("orders_portfolio._save_positions_file"),
            patch("event_log.append_close"),
            patch("learning.log_trade", side_effect=_capture_log_trade),
        ):
            _om.reconcile_with_ibkr(ib)

        assert "NVDA" not in _om.active_trades
        assert logged_outcomes, "CLOSE log_trade must be called for closed-while-down position"
        assert logged_outcomes[0]["exit_price"] == 825.50, (
            f"Expected IBKR fill price 825.50, got {logged_outcomes[0]['exit_price']}"
        )
        assert logged_outcomes[0]["exit_price_source"] == "ibkr_fill"

    def test_falls_back_to_estimated_when_no_execution(self, mock_config):
        """When reqExecutions returns nothing for the symbol, exit price is the stored 'current'."""
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om.active_trades["NVDA"] = {
            "symbol": "NVDA",
            "status": "ACTIVE",
            "direction": "LONG",
            "entry": 800.0,
            "current": 810.0,
            "qty": 5,
            "trade_type": "INTRADAY",
        }

        aapl_item = self._make_portfolio_item("AAPL")
        ib = self._make_ib([aapl_item], [])  # no fills

        logged_outcomes = []

        def _capture_log_trade(trade, agent_outputs, regime, action, outcome):
            if action == "CLOSE":
                logged_outcomes.append(outcome)

        with (
            patch("orders.CONFIG", mock_config),
            patch("event_log.open_trades", return_value={}),
            patch("orders_portfolio._load_positions_file", return_value={}),
            patch("orders_portfolio._save_positions_file"),
            patch("event_log.append_close"),
            patch("learning.log_trade", side_effect=_capture_log_trade),
        ):
            _om.reconcile_with_ibkr(ib)

        assert "NVDA" not in _om.active_trades
        assert logged_outcomes
        assert logged_outcomes[0]["exit_price"] == 810.0, (
            f"Expected fallback to current=810.0, got {logged_outcomes[0]['exit_price']}"
        )
        assert logged_outcomes[0]["exit_price_source"] == "estimated"


# ─────────────────────────────────────────────────────────────────────────────
# 7. trade_type recovery for UNKNOWN positions (Problems 1 & 3 fix)
# ─────────────────────────────────────────────────────────────────────────────


class TestTradeTypeRecovery:
    """
    When a position enters reconcile with trade_type=UNKNOWN (metadata lost on
    a prior restart), the code must recover trade_type from the event_log
    ORDER_INTENT before writing CLOSE records or adding external positions.

    Covers:
      - closed-while-down trade_type recovery via get_intent (trade_id present)
      - closed-while-down trade_type recovery via last_intent_for_symbol (no trade_id)
      - _find_saved last-resort tier uses last_intent_for_symbol when all else fails
    """

    def _make_ib_with_aapl(self):
        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.openTrades.return_value = []
        ib.sleep.return_value = None
        ib.reqExecutions.return_value = []
        # IBKR has AAPL so NVDA (absent from portfolio) triggers cwd sweep
        aapl_item = MagicMock()
        aapl_item.position = 10
        aapl_item.contract.symbol = "AAPL"
        aapl_item.contract.secType = "STK"
        aapl_item.contract.strike = 0
        aapl_item.marketPrice = 155.0
        aapl_item.averageCost = 150.0
        aapl_item.unrealizedPNL = 50.0
        ib.portfolio.return_value = [aapl_item]
        return ib

    def test_cwd_recovers_trade_type_via_get_intent(self, mock_config):
        """When trade_id is present, closed-while-down must recover trade_type from get_intent."""
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om.active_trades["NVDA"] = {
            "symbol": "NVDA",
            "status": "ACTIVE",
            "direction": "LONG",
            "entry": 800.0,
            "current": 810.0,
            "qty": 5,
            "trade_type": "UNKNOWN",
            "trade_id": "NVDA_20260428_test",
        }

        ib = self._make_ib_with_aapl()
        logged_trades = []

        def _capture_log_trade(trade, agent_outputs, regime, action, outcome):
            if action == "CLOSE":
                logged_trades.append(dict(trade))

        with (
            patch("orders.CONFIG", mock_config),
            patch("event_log.open_trades", return_value={}),
            patch("orders_portfolio._load_positions_file", return_value={}),
            patch("orders_portfolio._save_positions_file"),
            patch("event_log.get_intent", return_value={"trade_type": "INTRADAY", "trade_id": "NVDA_20260428_test"}),
            patch("event_log.append_close"),
            patch("learning.log_trade", side_effect=_capture_log_trade),
        ):
            _om.reconcile_with_ibkr(ib)

        assert "NVDA" not in _om.active_trades
        assert logged_trades, "CLOSE record must be written"
        assert logged_trades[0]["trade_type"] == "INTRADAY", (
            f"trade_type must be recovered from get_intent; got {logged_trades[0].get('trade_type')}"
        )

    def test_cwd_recovers_trade_type_via_last_intent_fallback(self, mock_config):
        """When no trade_id, closed-while-down must fall back to last_intent_for_symbol."""
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om.active_trades["NVDA"] = {
            "symbol": "NVDA",
            "status": "ACTIVE",
            "direction": "LONG",
            "entry": 800.0,
            "current": 810.0,
            "qty": 5,
            "trade_type": None,
        }

        ib = self._make_ib_with_aapl()
        logged_trades = []

        def _capture_log_trade(trade, agent_outputs, regime, action, outcome):
            if action == "CLOSE":
                logged_trades.append(dict(trade))

        with (
            patch("orders.CONFIG", mock_config),
            patch("event_log.open_trades", return_value={}),
            patch("orders_portfolio._load_positions_file", return_value={}),
            patch("orders_portfolio._save_positions_file"),
            patch("event_log.get_intent", return_value={}),
            patch("event_log.last_intent_for_symbol", return_value={"trade_type": "SWING", "symbol": "NVDA"}),
            patch("event_log.append_close"),
            patch("learning.log_trade", side_effect=_capture_log_trade),
        ):
            _om.reconcile_with_ibkr(ib)

        assert "NVDA" not in _om.active_trades
        assert logged_trades
        assert logged_trades[0]["trade_type"] == "SWING", (
            f"trade_type must be recovered via last_intent_for_symbol; got {logged_trades[0].get('trade_type')}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLASS 8: reset_stale_exits — self-correcting unfillable limits (commit cd33b70+)
# ─────────────────────────────────────────────────────────────────────────────


class TestResetStaleExits:
    """
    reset_stale_exits must detect and self-correct two failure modes without
    manual intervention:

      Case 1 — Order vanished: close_order_id not found in ib.openTrades().
               Reset to ACTIVE so execute_sell can retry.

      Case 2 — Unfillable limit: SELL limit >1% above current price, or
               BUY limit >1% below current price.  Cancel the IBKR order
               and reset to ACTIVE.

    Regression for the EQIX incident: stale pre-market force-exit placed a
    SELL LMT at $1086.89 when EQIX was at $1045.  The bot was permanently
    stuck in EXITING with a live-but-unfillable order.
    """

    @pytest.fixture(autouse=True)
    def _clear(self):
        import orders_portfolio as _op
        _op.active_trades.clear()
        yield
        _op.active_trades.clear()

    def _make_ib(self, open_trades):
        ib = MagicMock()
        ib.openTrades.return_value = open_trades
        return ib

    def _make_ibkr_trade(self, order_id, action, order_type, lmt_price, status="Submitted"):
        t = MagicMock()
        t.order.orderId = order_id
        t.order.action = action
        t.order.orderType = order_type
        t.order.lmtPrice = lmt_price
        t.orderStatus.status = status
        return t

    def test_order_vanished_resets_to_active(self, mock_config):
        """close_order_id not in ib.openTrades → reset to ACTIVE."""
        import orders_portfolio as _op
        _op.active_trades["EQIX"] = {
            "symbol": "EQIX", "status": "EXITING",
            "close_order_id": 8587, "current": 1050.0, "direction": "LONG",
        }
        ib = self._make_ib([])  # empty — order 8587 is gone
        with patch("orders_portfolio.CONFIG", mock_config):
            result = _op.reset_stale_exits(ib)
        assert "EQIX" in result
        assert _op.active_trades["EQIX"]["status"] == "ACTIVE"
        assert _op.active_trades["EQIX"]["close_order_id"] is None

    def test_unfillable_sell_limit_cancelled_and_reset(self, mock_config):
        """SELL LMT $1086 on a $1050 stock → cancel + reset to ACTIVE."""
        import orders_portfolio as _op
        _op.active_trades["EQIX"] = {
            "symbol": "EQIX", "status": "EXITING",
            "close_order_id": 8587, "current": 1050.0, "direction": "LONG",
        }
        ibkr_trade = self._make_ibkr_trade(8587, "SELL", "LMT", 1086.89)
        ib = self._make_ib([ibkr_trade])
        with (
            patch("orders_portfolio.CONFIG", mock_config),
            patch("orders_portfolio._cancel_ibkr_order_by_id") as mock_cancel,
        ):
            result = _op.reset_stale_exits(ib)
        assert "EQIX" in result, "Unfillable SELL limit must be detected and reset"
        assert _op.active_trades["EQIX"]["status"] == "ACTIVE"
        assert _op.active_trades["EQIX"]["close_order_id"] is None
        mock_cancel.assert_called_once_with(ib, 8587)

    def test_unfillable_buy_limit_cancelled_and_reset(self, mock_config):
        """BUY LMT $900 on a $1050 SHORT cover → cancel + reset to ACTIVE."""
        import orders_portfolio as _op
        _op.active_trades["EQIX"] = {
            "symbol": "EQIX", "status": "EXITING",
            "close_order_id": 9001, "current": 1050.0, "direction": "SHORT",
        }
        ibkr_trade = self._make_ibkr_trade(9001, "BUY", "LMT", 900.0)
        ib = self._make_ib([ibkr_trade])
        with (
            patch("orders_portfolio.CONFIG", mock_config),
            patch("orders_portfolio._cancel_ibkr_order_by_id") as mock_cancel,
        ):
            result = _op.reset_stale_exits(ib)
        assert "EQIX" in result
        assert _op.active_trades["EQIX"]["status"] == "ACTIVE"
        mock_cancel.assert_called_once_with(ib, 9001)

    def test_fillable_limit_not_touched(self, mock_config):
        """SELL LMT $1048 on a $1050 stock (0.2% below) — normal close order, leave it."""
        import orders_portfolio as _op
        _op.active_trades["EQIX"] = {
            "symbol": "EQIX", "status": "EXITING",
            "close_order_id": 8600, "current": 1050.0, "direction": "LONG",
        }
        ibkr_trade = self._make_ibkr_trade(8600, "SELL", "LMT", 1047.90)
        ib = self._make_ib([ibkr_trade])
        with (
            patch("orders_portfolio.CONFIG", mock_config),
            patch("orders_portfolio._cancel_ibkr_order_by_id") as mock_cancel,
        ):
            result = _op.reset_stale_exits(ib)
        assert "EQIX" not in result, "Fillable limit must not be cancelled"
        assert _op.active_trades["EQIX"]["status"] == "EXITING"
        mock_cancel.assert_not_called()

    def test_gtc_limit_cancelled_at_regular_session_open(self, mock_config):
        """GTC LMT close order present when regular session opens → cancel + reset to ACTIVE for MKT."""
        import orders_portfolio as _op
        _op.active_trades["EQIX"] = {
            "symbol": "EQIX", "status": "EXITING",
            "close_order_id": 8752, "current": 1050.0, "direction": "LONG",
        }
        ibkr_trade = self._make_ibkr_trade(8752, "SELL", "LMT", 1054.29)
        ibkr_trade.order.tif = "GTC"
        ib = self._make_ib([ibkr_trade])
        with (
            patch("orders_portfolio.CONFIG", mock_config),
            patch("orders_portfolio.is_options_market_open", return_value=True),
            patch("orders_portfolio._cancel_ibkr_order_by_id") as mock_cancel,
        ):
            result = _op.reset_stale_exits(ib)
        assert "EQIX" in result, "GTC limit at regular session open must be cancelled and reset"
        assert _op.active_trades["EQIX"]["status"] == "ACTIVE"
        assert _op.active_trades["EQIX"]["close_order_id"] is None
        mock_cancel.assert_called_once_with(ib, 8752)

    def test_gtc_limit_not_cancelled_in_extended_hours(self, mock_config):
        """GTC LMT during pre-market/after-hours and within fill range — leave it alone."""
        import orders_portfolio as _op
        _op.active_trades["EQIX"] = {
            "symbol": "EQIX", "status": "EXITING",
            "close_order_id": 8752, "current": 1050.0, "direction": "LONG",
        }
        ibkr_trade = self._make_ibkr_trade(8752, "SELL", "LMT", 1047.90)
        ibkr_trade.order.tif = "GTC"
        ib = self._make_ib([ibkr_trade])
        with (
            patch("orders_portfolio.CONFIG", mock_config),
            patch("orders_portfolio.is_options_market_open", return_value=False),
            patch("orders_portfolio._cancel_ibkr_order_by_id") as mock_cancel,
        ):
            result = _op.reset_stale_exits(ib)
        assert "EQIX" not in result, "Fillable GTC limit in extended hours must not be cancelled"
        assert _op.active_trades["EQIX"]["status"] == "EXITING"
        mock_cancel.assert_not_called()

    def test_non_exiting_position_ignored(self, mock_config):
        """ACTIVE positions are never touched by reset_stale_exits."""
        import orders_portfolio as _op
        _op.active_trades["NVDA"] = {
            "symbol": "NVDA", "status": "ACTIVE",
            "close_order_id": None, "current": 800.0, "direction": "LONG",
        }
        ib = self._make_ib([])
        with patch("orders_portfolio.CONFIG", mock_config):
            result = _op.reset_stale_exits(ib)
        assert "NVDA" not in result
        assert _op.active_trades["NVDA"]["status"] == "ACTIVE"


class TestAvoidEntrySanitization:
    """Regression: Apex returning banned fields on AVOID entries must not trigger schema_error."""

    def test_avoid_entry_instrument_sanitized(self):
        from market_intelligence import _sanitize_avoid_entries
        from schemas import validate_apex_decision_schema

        decision = {
            "new_entries": [{
                "symbol": "META",
                "trade_type": "AVOID",
                "instrument": "stock",
                "direction": "LONG",
                "conviction": "MEDIUM",
                "direction_flipped": False,
                "counter_argument": "strong momentum",
                "key_risk": "earnings gap",
                "rationale": "macro binary risk today",
            }],
            "portfolio_actions": [],
            "market_read": "test",
            "macro_bias": "NEUTRAL",
            "session_character": "FEAR_ELEVATED",
        }
        _sanitize_avoid_entries(decision)
        validate_apex_decision_schema(decision)  # must not raise
        entry = decision["new_entries"][0]
        assert entry.get("instrument") is None
        assert entry.get("direction") is None
        assert entry.get("conviction") is None
        assert entry.get("direction_flipped") is None
        assert entry.get("counter_argument") is None
        assert entry.get("key_risk") is None
        assert entry["rationale"] == "macro binary risk today"

    def test_non_avoid_entries_untouched(self):
        from market_intelligence import _sanitize_avoid_entries

        decision = {
            "new_entries": [{
                "symbol": "AAPL",
                "trade_type": "INTRADAY",
                "direction": "LONG",
                "conviction": "MEDIUM",
                "instrument": "stock",
                "rationale": "momentum breakout",
            }],
            "portfolio_actions": [],
        }
        _sanitize_avoid_entries(decision)
        entry = decision["new_entries"][0]
        assert entry["instrument"] == "stock"
        assert entry["direction"] == "LONG"


# ─────────────────────────────────────────────────────────────────────────────
# CLASS 9: execute_sell closes linked option legs (unified close)
# ─────────────────────────────────────────────────────────────────────────────


class TestExecuteSellClosesLinkedOptions:
    """
    When execute_sell(ib, "GSAT") closes a stock position that has a co-existing
    option leg in active_trades, it must call execute_sell_option for that leg.

    Before fix: option legs were silently abandoned — orphaned until next restart.
    After fix:  execute_sell sweeps active_trades for instrument=option rows whose
    symbol field matches the closed underlying and routes each through execute_sell_option.
    """

    STOCK_KEY = "GSAT"
    OPT_KEY = "GSAT_C_35.0_2026-04-17"

    def _stock_pos(self):
        return {
            "symbol": "GSAT",
            "instrument": "stock",
            "qty": 100,
            "entry": 30.0,
            "current": 32.0,
            "direction": "LONG",
            "status": "ACTIVE",
        }

    def _opt_pos(self):
        return {
            "symbol": "GSAT",
            "instrument": "option",
            "right": "C",
            "strike": 35.0,
            "expiry_ibkr": "20260417",
            "expiry_str": "2026-04-17",
            "qty": 2,
            "contracts": 2,
            "entry": 1.50,
            "current": 2.00,
            "direction": "LONG",
            "status": "ACTIVE",
        }

    def test_linked_option_closed_when_stock_exits(self, mock_config):
        """execute_sell('GSAT') must call execute_sell_option for the linked option."""
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om.recently_closed.clear()
        _om.active_trades[self.STOCK_KEY] = self._stock_pos()
        _om.active_trades[self.OPT_KEY] = self._opt_pos()

        ib = MagicMock()
        ib.placeOrder.return_value.orderStatus.status = "Filled"
        ib.placeOrder.return_value.orderStatus.filled = 100

        with (
            patch("orders.CONFIG", mock_config),
            patch("orders_core.is_equities_extended_hours", return_value=True),
            patch("orders_core.is_options_market_open", return_value=True),
            patch("orders._validate_position_price", return_value=(32.0, "IBKR")),
            patch("orders._get_ibkr_price", return_value=32.0),
            patch("orders.record_win"),
            patch("orders.record_loss"),
            patch("orders.log_order"),
            patch("learning.log_order"),
            patch("learning._save_orders"),
            patch("learning._save_trades"),
            patch("learning.log_trade"),
            patch("fill_watcher.stop_watcher"),
            patch("orders_options.execute_sell_option") as mock_sell_opt,
        ):
            result = _om.execute_sell(ib, self.STOCK_KEY, reason="apex_exit")

        assert result is True, "Stock close must succeed"
        mock_sell_opt.assert_called_once_with(
            ib, self.OPT_KEY, reason="parent_closed:apex_exit"
        )

    def test_no_option_sweep_when_instrument_is_option(self, mock_config):
        """Closing an option directly must not trigger a recursive option sweep."""
        _om = sys.modules["orders"]
        _om.active_trades.clear()
        _om.recently_closed.clear()
        _om.active_trades[self.OPT_KEY] = self._opt_pos()

        ib = MagicMock()
        ib.placeOrder.return_value.orderStatus.status = "Filled"
        ib.placeOrder.return_value.orderStatus.filled = 2

        with (
            patch("orders.CONFIG", mock_config),
            patch("orders_core.is_equities_extended_hours", return_value=True),
            patch("orders_core.is_options_market_open", return_value=True),
            patch("orders._validate_position_price", return_value=(2.00, "IBKR")),
            patch("orders._get_ibkr_price", return_value=2.00),
            patch("orders.record_win"),
            patch("orders.record_loss"),
            patch("orders.log_order"),
            patch("learning.log_order"),
            patch("learning._save_orders"),
            patch("learning._save_trades"),
            patch("learning.log_trade"),
            patch("fill_watcher.stop_watcher"),
            patch("orders_options.execute_sell_option") as mock_sell_opt,
        ):
            _om.execute_sell(ib, self.OPT_KEY, reason="manual")

        mock_sell_opt.assert_not_called()


class TestDeferredTrimOnUnfilledLimitSell:
    """Regression: trim SELL not confirmed within 2s must set TRIMMING status
    without decrementing qty, writing POSITION_TRIMMED, or placing a new bracket.

    Root cause of SNX order accumulation (2026-04-30): execute_sell wrote
    POSITION_TRIMMED and placed a new SL/TP bracket even when the extended-hours
    limit SELL never filled. Over nine trim attempts the system held a phantom
    position of 1 share while IBKR held 263, and accumulated dozens of orphaned
    Inactive bracket children.
    """

    TRADE_KEY = "SNX"

    def _position(self):
        return {
            "symbol": "SNX",
            "direction": "LONG",
            "trade_type": "SWING",
            "instrument": "stock",
            "qty": 263,
            "entry": 224.18,
            "intended_price": 224.22,
            "current": 284.00,
            "sl": 218.05,
            "tp": 238.08,
            "status": "ACTIVE",
            "trade_id": "SNX_20260429_172800_821400",
            "order_id": 101,
            "sl_order_id": 102,
            "tp_order_id": 103,
            "open_time": "2026-04-29T17:33:05+00:00",
        }

    def test_unconfirmed_trim_sets_trimming_status_not_decrement(self, mock_config):
        """When a trim SELL limit order is not filled within 2s, qty must not
        be decremented and status must be TRIMMING."""
        import orders as _om
        _om.active_trades.clear()
        _om.recently_closed.clear()
        _om.active_trades[self.TRADE_KEY] = self._position()

        ib = MagicMock()
        # Unfilled: status=Submitted, filled=0
        ib.placeOrder.return_value.orderStatus.status = "Submitted"
        ib.placeOrder.return_value.orderStatus.filled = 0
        ib.placeOrder.return_value.orderStatus.avgFillPrice = None
        ib.placeOrder.return_value.order.orderId = 999
        ib.openTrades.return_value = []

        with (
            patch("orders.CONFIG", mock_config),
            patch("orders_core.is_equities_extended_hours", return_value=True),
            patch("orders_core.is_options_market_open", return_value=False),
            patch("orders._validate_position_price", return_value=(284.00, "IBKR")),
            patch("orders._get_ibkr_bid_ask", return_value=(284.00, 284.05)),
            patch("orders.record_win"),
            patch("orders.record_loss"),
            patch("orders.log_order"),
            patch("learning.log_order"),
            patch("learning._save_orders"),
            patch("learning._save_trades"),
            patch("learning.log_trade"),
            patch("fill_watcher.stop_watcher"),
            patch("event_log.append_trim") as mock_trim_event,
        ):
            result = _om.execute_sell(ib, self.TRADE_KEY, reason="profit_harvesting", qty_override=131)

        assert result is True
        pos = _om.active_trades.get(self.TRADE_KEY, {})
        assert pos.get("qty") == 263, "qty must not be decremented for an unconfirmed trim"
        assert pos.get("status") == "TRIMMING", "status must be TRIMMING when fill unconfirmed"
        assert pos.get("pending_trim_order_id") == 999
        assert pos.get("pending_trim_qty") == 131
        mock_trim_event.assert_not_called(), "POSITION_TRIMMED must not be written until fill confirmed"

    def test_confirmed_trim_proceeds_normally(self, mock_config):
        """When trim SELL is confirmed filled within 2s, normal trim path runs:
        qty decremented, POSITION_TRIMMED written, new bracket placed."""
        import orders as _om
        _om.active_trades.clear()
        _om.recently_closed.clear()
        _om.active_trades[self.TRADE_KEY] = self._position()

        ib = MagicMock()
        # Filled immediately
        ib.placeOrder.return_value.orderStatus.status = "Filled"
        ib.placeOrder.return_value.orderStatus.filled = 131
        ib.placeOrder.return_value.orderStatus.avgFillPrice = 284.42
        ib.placeOrder.return_value.order.orderId = 888
        ib.openTrades.return_value = []

        with (
            patch("orders.CONFIG", mock_config),
            patch("orders_core.is_equities_extended_hours", return_value=True),
            patch("orders_core.is_options_market_open", return_value=False),
            patch("orders._validate_position_price", return_value=(284.00, "IBKR")),
            patch("orders._get_ibkr_bid_ask", return_value=(284.42, 284.50)),
            patch("orders.record_win"),
            patch("orders.record_loss"),
            patch("orders.log_order"),
            patch("learning.log_order"),
            patch("learning._save_orders"),
            patch("learning._save_trades"),
            patch("learning.log_trade"),
            patch("fill_watcher.stop_watcher"),
            patch("event_log.append_trim") as mock_trim_event,
        ):
            result = _om.execute_sell(ib, self.TRADE_KEY, reason="profit_harvesting", qty_override=131)

        assert result is True
        pos = _om.active_trades.get(self.TRADE_KEY, {})
        assert pos.get("qty") == 132, "qty must be decremented when fill confirmed"
        assert pos.get("status") == "ACTIVE", "status must return to ACTIVE after confirmed trim"
        assert pos.get("pending_trim_order_id") is None
        mock_trim_event.assert_called_once(), "POSITION_TRIMMED must be written on confirmed fill"
