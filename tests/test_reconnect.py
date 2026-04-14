#!/usr/bin/env python3
"""
tests/test_reconnect.py

Comprehensive unit tests for the IBKR auto-reconnect logic in bot.py.
All heavy dependencies are mocked before import.
"""

from __future__ import annotations

import os
import sys
import types
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


import os
import sys
import threading
import time
import unittest

# ── Path setup ────────────────────────────────────────────────────────────────
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ── Stub heavy imports BEFORE loading bot ────────────────────────────────────


def _make_stub(name):
    mod = types.ModuleType(name)
    return mod


# ib_async — use setdefault so real/conftest stub is preserved
ib_async_stub = _make_stub("ib_async")
ib_async_stub.IB = MagicMock
ib_async_stub.Stock = MagicMock
ib_async_stub.Contract = MagicMock
ib_async_stub.Order = MagicMock
ib_async_stub.OrderStatus = MagicMock
sys.modules.setdefault("ib_async", ib_async_stub)

# anthropic — use setdefault
anthropic_stub = _make_stub("anthropic")
anthropic_stub.Anthropic = MagicMock
sys.modules.setdefault("anthropic", anthropic_stub)

# yfinance — use setdefault
yfinance_stub = _make_stub("yfinance")
yfinance_stub.download = MagicMock(return_value=MagicMock())
sys.modules.setdefault("yfinance", yfinance_stub)

# schedule — use setdefault
schedule_stub = _make_stub("schedule")
schedule_stub.every = MagicMock()
schedule_stub.run_pending = MagicMock()
sys.modules.setdefault("schedule", schedule_stub)

# colorama — use setdefault
colorama_stub = _make_stub("colorama")
colorama_stub.Fore = MagicMock()
colorama_stub.Style = MagicMock()
colorama_stub.init = MagicMock()
sys.modules.setdefault("colorama", colorama_stub)

# Stub every local module that bot.py (or its lazy imports) pulls in
for _mod_name in [
    "scanner",
    "signals",
    "news",
    "agents",
    "orders",
    "orders_portfolio",  # bot_ibkr.connect_ibkr() does a lazy import of this
    "options",
    "options_scanner",
    "risk",
    "learning",
    "dashboard",
    "news_sentinel",
    "theme_tracker",
    "sentinel_agents",
    "social_sentiment",
    "ml_engine",
    "portfolio_optimizer",
]:
    _stub = _make_stub(_mod_name)
    _stub.get_dynamic_universe = MagicMock(return_value=[])
    _stub.get_market_regime = MagicMock(return_value={})
    _stub.get_tv_signal_cache = MagicMock(return_value={})
    _stub.score_universe = MagicMock(return_value=([], []))
    _stub.fetch_multi_timeframe = MagicMock(return_value={})
    _stub.batch_news_sentiment = MagicMock(return_value={})
    _stub.run_all_agents = MagicMock(return_value={})
    _stub.execute_buy = MagicMock()
    _stub.execute_sell = MagicMock()
    _stub.flatten_all = MagicMock()
    _stub.reconcile_with_ibkr = MagicMock()
    _stub.get_open_positions = MagicMock(return_value=[])
    _stub.update_position_prices = MagicMock()
    _stub.update_positions_from_ibkr = MagicMock()
    _stub.execute_buy_option = MagicMock()
    _stub.execute_sell_option = MagicMock()
    _stub.find_best_contract = MagicMock()
    _stub.check_options_exits = MagicMock()
    _stub.scan_options_universe = MagicMock(return_value=[])
    _stub.can_trade = MagicMock(return_value=True)
    _stub.check_risk_conditions = MagicMock(return_value=(True, ""))
    _stub.get_session = MagicMock(return_value="REGULAR")
    _stub.get_scan_interval = MagicMock(return_value=300)
    _stub.reset_daily_state = MagicMock()
    _stub.calculate_position_size = MagicMock(return_value=10)
    _stub.calculate_stops = MagicMock(return_value=(0, 0))
    _stub.update_equity_high_water_mark = MagicMock(return_value=False)
    _stub.init_equity_high_water_mark_from_history = MagicMock()
    _stub.log_trade = MagicMock()
    _stub.load_trades = MagicMock(return_value=[])
    _stub.load_orders = MagicMock(return_value=[])
    _stub.get_performance_summary = MagicMock(return_value={})
    _stub.run_weekly_review = MagicMock()
    _stub.TRADE_LOG_FILE = "data/trades.json"
    _stub.get_effective_capital = MagicMock(return_value=100000)
    _stub.record_capital_adjustment = MagicMock()
    _stub.DASHBOARD_HTML = "<html></html>"
    _stub.NewsSentinel = MagicMock()
    _stub.get_sentinel_history = MagicMock(return_value=[])
    _stub.build_sentinel_universe = MagicMock(return_value=[])
    _stub.load_custom_themes = MagicMock(return_value={})
    _stub.get_all_themes = MagicMock(return_value={})
    _stub.run_sentinel_pipeline = MagicMock()
    sys.modules.setdefault(_mod_name, _stub)

# ── Now import bot + submodules ───────────────────────────────────────────────
import bot
import bot_ibkr
import bot_state

# ── Helpers ───────────────────────────────────────────────────────────────────


def _reset_reconnect_state():
    """Reset module-level reconnect state between tests."""
    bot._reconnecting = False
    bot_state._subscription_registry.clear()


# ═════════════════════════════════════════════════════════════════════════════
# 1. Config keys present
# ═════════════════════════════════════════════════════════════════════════════


class TestReconnectConfigKeys(unittest.TestCase):
    """All new config keys must be present with sensible defaults."""

    def test_reconnect_max_attempts_present(self):
        self.assertIn("reconnect_max_attempts", bot.CONFIG)
        self.assertGreater(bot.CONFIG["reconnect_max_attempts"], 0)

    def test_reconnect_max_wait_secs_present(self):
        self.assertIn("reconnect_max_wait_secs", bot.CONFIG)
        self.assertGreater(bot.CONFIG["reconnect_max_wait_secs"], 0)

    def test_reconnect_base_wait_secs_present(self):
        self.assertIn("reconnect_base_wait_secs", bot.CONFIG)
        self.assertGreater(bot.CONFIG["reconnect_base_wait_secs"], 0)

    def test_heartbeat_interval_secs_present(self):
        self.assertIn("heartbeat_interval_secs", bot.CONFIG)
        self.assertGreater(bot.CONFIG["heartbeat_interval_secs"], 0)

    def test_reconnect_alert_webhook_present(self):
        self.assertIn("reconnect_alert_webhook", bot.CONFIG)
        # Value may be empty string — just must exist
        self.assertIsInstance(bot.CONFIG["reconnect_alert_webhook"], str)

    def test_default_max_attempts_is_reasonable(self):
        """Default should be between 3 and 20 retries."""
        v = bot.CONFIG["reconnect_max_attempts"]
        self.assertGreaterEqual(v, 3)
        self.assertLessEqual(v, 20)

    def test_default_heartbeat_is_at_least_one_minute(self):
        """Heartbeat interval should be at least 60 seconds."""
        self.assertGreaterEqual(bot.CONFIG["heartbeat_interval_secs"], 60)


# ═════════════════════════════════════════════════════════════════════════════
# 2. Subscription registry
# ═════════════════════════════════════════════════════════════════════════════


class TestSubscriptionRegistry(unittest.TestCase):
    def setUp(self):
        _reset_reconnect_state()

    def test_register_stores_entry(self):
        bot_ibkr._register_subscription("AAPL", {"type": "ticker"})
        self.assertIn("AAPL", bot_state._subscription_registry)

    def test_register_stores_correct_params(self):
        params = {"type": "ticker", "exchange": "SMART"}
        bot_ibkr._register_subscription("TSLA", params)
        self.assertEqual(bot_state._subscription_registry["TSLA"], params)

    def test_register_overwrites_existing_key(self):
        bot_ibkr._register_subscription("MSFT", {"type": "ticker"})
        bot_ibkr._register_subscription("MSFT", {"type": "pnl"})
        self.assertEqual(bot_state._subscription_registry["MSFT"]["type"], "pnl")

    def test_unregister_removes_entry(self):
        bot_ibkr._register_subscription("GOOG", {"type": "ticker"})
        bot_ibkr._unregister_subscription("GOOG")
        self.assertNotIn("GOOG", bot_state._subscription_registry)

    def test_unregister_nonexistent_is_noop(self):
        """Should not raise KeyError."""
        bot_ibkr._unregister_subscription("DOES_NOT_EXIST")

    def test_registry_starts_empty_after_reset(self):
        bot_ibkr._register_subscription("X", {"type": "ticker"})
        _reset_reconnect_state()
        self.assertEqual(len(bot_state._subscription_registry), 0)

    def test_restore_subscriptions_calls_reqPnL(self):
        """_restore_subscriptions calls ib.reqPnL for PnL subscriptions."""
        bot_ibkr._register_subscription("__pnl__", {"type": "pnl", "account": "DU999"})
        mock_ib = MagicMock()
        with patch.object(bot, "ib", mock_ib):
            bot_ibkr._restore_subscriptions()
        mock_ib.reqPnL.assert_called_once_with("DU999")

    def test_restore_subscriptions_calls_reqMktData(self):
        """_restore_subscriptions calls ib.reqMktData for ticker subscriptions."""
        bot_ibkr._register_subscription("NVDA", {"type": "ticker"})
        mock_ib = MagicMock()
        with patch.object(bot, "ib", mock_ib):
            bot_ibkr._restore_subscriptions()
        mock_ib.reqMktData.assert_called_once()

    def test_restore_subscriptions_multiple(self):
        """Multiple subscriptions are all restored."""
        bot_ibkr._register_subscription("__pnl__", {"type": "pnl", "account": "DU1"})
        bot_ibkr._register_subscription("AMD", {"type": "ticker"})
        bot_ibkr._register_subscription("SPY", {"type": "ticker"})
        mock_ib = MagicMock()
        with patch.object(bot, "ib", mock_ib):
            bot_ibkr._restore_subscriptions()
        self.assertEqual(mock_ib.reqPnL.call_count, 1)
        self.assertEqual(mock_ib.reqMktData.call_count, 2)

    def test_restore_subscriptions_empty_registry_no_raise(self):
        """Empty registry must not raise."""
        bot_ibkr._restore_subscriptions()

    def test_restore_subscriptions_unknown_type_no_raise(self):
        """Unknown subscription type is logged and skipped without raising."""
        bot_ibkr._register_subscription("WEIRD", {"type": "unknown_type"})
        mock_ib = MagicMock()
        with patch.object(bot, "ib", mock_ib):
            bot_ibkr._restore_subscriptions()  # must not raise

    def test_restore_subscriptions_exception_in_one_does_not_abort(self):
        """A failed restore for one subscription should not prevent the rest."""
        bot_ibkr._register_subscription("__pnl__", {"type": "pnl", "account": "DU1"})
        bot_ibkr._register_subscription("FAIL", {"type": "ticker"})
        bot_ibkr._register_subscription("OK", {"type": "ticker"})

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated mkt data failure")

        mock_ib = MagicMock()
        mock_ib.reqMktData.side_effect = side_effect
        with patch.object(bot, "ib", mock_ib):
            bot_ibkr._restore_subscriptions()  # must not raise
        # reqMktData was called twice (FAIL and OK), reqPnL once
        self.assertEqual(mock_ib.reqPnL.call_count, 1)
        self.assertEqual(mock_ib.reqMktData.call_count, 2)


# ═════════════════════════════════════════════════════════════════════════════
# 3. _on_disconnected guard
# ═════════════════════════════════════════════════════════════════════════════


class TestOnDisconnected(unittest.TestCase):
    def setUp(self):
        _reset_reconnect_state()

    def _make_fake_thread(self, started_list):
        def factory(*args, **kwargs):
            t = MagicMock()
            t.start = MagicMock(side_effect=lambda: started_list.append(1))
            return t

        return factory

    def test_spawns_one_reconnect_thread(self):
        started = []
        with patch("threading.Thread", side_effect=self._make_fake_thread(started)):
            bot_ibkr._on_disconnected()
        self.assertEqual(len(started), 1)

    def test_sets_reconnecting_flag(self):
        self.assertFalse(bot._reconnecting)
        with patch("threading.Thread", side_effect=self._make_fake_thread([])):
            bot_ibkr._on_disconnected()
        self.assertTrue(bot._reconnecting)

    def test_second_call_while_reconnecting_ignored(self):
        started = []
        with patch("threading.Thread", side_effect=self._make_fake_thread(started)):
            bot_ibkr._on_disconnected()  # first call
            bot_ibkr._on_disconnected()  # second call — must be ignored
        self.assertEqual(len(started), 1, "Only one reconnect thread should start")

    def test_dashboard_status_set_to_disconnected(self):
        bot.dash["status"] = "connected"
        with patch("threading.Thread", side_effect=self._make_fake_thread([])):
            bot_ibkr._on_disconnected()
        self.assertEqual(bot.dash["status"], "disconnected")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Exponential backoff in _reconnect_worker
# ═════════════════════════════════════════════════════════════════════════════


class TestReconnectWorkerBackoff(unittest.TestCase):
    def setUp(self):
        _reset_reconnect_state()
        bot.CONFIG["reconnect_max_attempts"] = 5
        bot.CONFIG["reconnect_max_wait_secs"] = 16
        bot.CONFIG["reconnect_base_wait_secs"] = 1
        bot.CONFIG["ibkr_host"] = "127.0.0.1"
        bot.CONFIG["ibkr_port"] = 7497
        bot.CONFIG["ibkr_client_id"] = 1

    def _run_worker_all_fail(self):
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        sleep_calls = []
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_send_reconnect_exhausted_alert"):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                bot_ibkr._reconnect_worker()
        return sleep_calls

    def test_correct_backoff_sequence(self):
        """Sleep sequence must double: 1, 2, 4, 8, 16 (capped at 16)."""
        sleep_calls = self._run_worker_all_fail()
        self.assertEqual(sleep_calls, [1, 2, 4, 8, 16])

    def test_backoff_capped_at_max_wait(self):
        """No sleep call should exceed reconnect_max_wait_secs."""
        bot.CONFIG["reconnect_max_attempts"] = 8
        bot.CONFIG["reconnect_max_wait_secs"] = 4
        sleep_calls = self._run_worker_all_fail()
        self.assertTrue(all(s <= 4 for s in sleep_calls), f"Sleep exceeded max: {sleep_calls}")

    def test_backoff_capped_sequence_values(self):
        """With max=4 and base=1 over 6 attempts: 1,2,4,4,4,4."""
        bot.CONFIG["reconnect_max_attempts"] = 6
        bot.CONFIG["reconnect_max_wait_secs"] = 4
        bot.CONFIG["reconnect_base_wait_secs"] = 1
        sleep_calls = self._run_worker_all_fail()
        self.assertEqual(sleep_calls, [1, 2, 4, 4, 4, 4])

    def test_correct_number_of_connect_attempts(self):
        """ib.connect is called exactly max_attempts times on all failures."""
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_send_reconnect_exhausted_alert"):
            with patch("time.sleep"):
                bot_ibkr._reconnect_worker()
        self.assertEqual(mock_ib.connect.call_count, 5)

    def test_stops_after_success(self):
        """If connect succeeds on attempt 2, only 2 connect calls are made."""
        bot.CONFIG["reconnect_max_attempts"] = 5
        call_count = [0]

        def connect_succeeds_on_second(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                raise OSError("refused")

        mock_ib = MagicMock()
        mock_ib.connect = MagicMock(side_effect=connect_succeeds_on_second)
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_restore_subscriptions"), patch("time.sleep"):
            bot_ibkr._reconnect_worker()
        self.assertEqual(mock_ib.connect.call_count, 2)

    def test_sleep_called_before_each_connect(self):
        """sleep is called once before each connect attempt."""
        bot.CONFIG["reconnect_max_attempts"] = 3
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        sleep_calls = []
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_send_reconnect_exhausted_alert"):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                bot_ibkr._reconnect_worker()
        # One sleep per attempt — filter out heartbeat tick (60 s) which a concurrent
        # daemon thread may inject when time.sleep is globally patched.
        backoff_sleeps = [s for s in sleep_calls if s != 60]
        self.assertEqual(len(backoff_sleeps), 3)


# ═════════════════════════════════════════════════════════════════════════════
# 5. _reconnect_worker state management
# ═════════════════════════════════════════════════════════════════════════════


class TestReconnectWorkerState(unittest.TestCase):
    def setUp(self):
        _reset_reconnect_state()
        bot.CONFIG["reconnect_max_attempts"] = 3
        bot.CONFIG["reconnect_max_wait_secs"] = 8
        bot.CONFIG["reconnect_base_wait_secs"] = 1
        bot.CONFIG["ibkr_host"] = "127.0.0.1"
        bot.CONFIG["ibkr_port"] = 7497
        bot.CONFIG["ibkr_client_id"] = 1

    def test_reconnecting_cleared_after_success(self):
        bot._reconnecting = True
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock()
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_restore_subscriptions"), patch("time.sleep"):
            bot_ibkr._reconnect_worker()
        self.assertFalse(bot._reconnecting)

    def test_reconnecting_cleared_after_exhaustion(self):
        bot._reconnecting = True
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_send_reconnect_exhausted_alert"):
            with patch("time.sleep"):
                bot_ibkr._reconnect_worker()
        self.assertFalse(bot._reconnecting)

    def test_restore_subscriptions_called_on_success(self):
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock()
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_restore_subscriptions") as mock_restore:
            with patch("time.sleep"):
                bot_ibkr._reconnect_worker()
        mock_restore.assert_called_once()

    def test_restore_subscriptions_not_called_on_exhaustion(self):
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_restore_subscriptions") as mock_restore:
            with patch.object(bot_ibkr, "_send_reconnect_exhausted_alert"):
                with patch("time.sleep"):
                    bot_ibkr._reconnect_worker()
        mock_restore.assert_not_called()

    def test_exhaustion_triggers_alert_with_correct_count(self):
        bot.CONFIG["reconnect_max_attempts"] = 3
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        with patch.object(bot, "ib", mock_ib):
            with patch.object(bot_ibkr, "_send_reconnect_exhausted_alert") as mock_alert:
                with patch("time.sleep"):
                    bot_ibkr._reconnect_worker()
        mock_alert.assert_called_once_with(3)

    def test_dashboard_status_connected_on_success(self):
        bot.dash["status"] = "reconnecting"
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock()
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_restore_subscriptions"), patch("time.sleep"):
            bot_ibkr._reconnect_worker()
        self.assertEqual(bot.dash["status"], "connected")

    def test_dashboard_status_shows_attempt_number(self):
        """Dashboard status is updated on each attempt before connecting."""
        statuses = []
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock()

        original_sleep = time.sleep

        def capture_status(s):
            statuses.append(bot.dash.get("status", ""))

        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_restore_subscriptions"):
            with patch("time.sleep", side_effect=capture_status):
                bot_ibkr._reconnect_worker()

        self.assertTrue(len(statuses) >= 1)
        self.assertIn("reconnecting", statuses[0])

    def test_connect_called_with_correct_args(self):
        """ib.connect receives host, port and clientId from CONFIG."""
        bot.CONFIG["ibkr_host"] = "192.168.1.50"
        bot.CONFIG["ibkr_port"] = 4001
        bot.CONFIG["ibkr_client_id"] = 7
        bot.CONFIG["reconnect_max_attempts"] = 1
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock()
        with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_restore_subscriptions"), patch("time.sleep"):
            bot_ibkr._reconnect_worker()
        mock_ib.connect.assert_called_once_with("192.168.1.50", 4001, clientId=7, readonly=False)


# ═════════════════════════════════════════════════════════════════════════════
# 6. Heartbeat worker
# ═════════════════════════════════════════════════════════════════════════════


class TestHeartbeatWorker(unittest.TestCase):
    def setUp(self):
        _reset_reconnect_state()
        bot.CONFIG["heartbeat_interval_secs"] = 60  # 60 s = 1 tick

    def _run_n_ticks(self, n, mock_ib):
        """Run _heartbeat_worker for exactly n ticks then stop."""
        tick_count = [0]

        def fake_sleep(s):
            tick_count[0] += 1
            if tick_count[0] >= n:
                raise StopIteration()

        with patch.object(bot, "ib", mock_ib), patch("time.sleep", side_effect=fake_sleep):
            try:
                bot_ibkr._heartbeat_worker()
            except StopIteration:
                pass

    def test_heartbeat_fires_after_interval(self):
        """reqCurrentTime is called once after one full interval."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        self._run_n_ticks(2, mock_ib)
        mock_ib.reqCurrentTime.assert_called_once()

    def test_heartbeat_skips_when_disconnected(self):
        """reqCurrentTime is NOT called when isConnected returns False."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = False
        self._run_n_ticks(2, mock_ib)
        mock_ib.reqCurrentTime.assert_not_called()

    def test_heartbeat_fires_multiple_times(self):
        """reqCurrentTime is called once per interval over multiple intervals."""
        bot.CONFIG["heartbeat_interval_secs"] = 60
        tick_count = [0]
        hb_count = [0]

        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True

        original_req = mock_ib.reqCurrentTime

        def fake_sleep(s):
            tick_count[0] += 1
            if tick_count[0] >= 5:  # 5 ticks = ~5 intervals (interval=60, tick=60)
                raise StopIteration()

        with patch.object(bot, "ib", mock_ib), patch("time.sleep", side_effect=fake_sleep):
            try:
                bot_ibkr._heartbeat_worker()
            except StopIteration:
                pass

        # At least 2 calls over 5 ticks (5 intervals)
        self.assertGreaterEqual(mock_ib.reqCurrentTime.call_count, 2)

    def test_heartbeat_exception_does_not_crash(self):
        """A failed reqCurrentTime must not crash the heartbeat loop."""
        bot.CONFIG["heartbeat_interval_secs"] = 60
        tick_count = [0]
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib.reqCurrentTime.side_effect = RuntimeError("timeout")

        def fake_sleep(s):
            tick_count[0] += 1
            if tick_count[0] >= 3:
                raise StopIteration()

        with patch.object(bot, "ib", mock_ib), patch("time.sleep", side_effect=fake_sleep):
            try:
                bot_ibkr._heartbeat_worker()  # must not propagate RuntimeError
            except StopIteration:
                pass

    def test_heartbeat_tick_interval_is_positive(self):
        """Internal tick sleep must be a positive number."""
        sleep_args = []
        tick_count = [0]
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = False

        def fake_sleep(s):
            sleep_args.append(s)
            tick_count[0] += 1
            if tick_count[0] >= 1:
                raise StopIteration()

        with patch.object(bot, "ib", mock_ib), patch("time.sleep", side_effect=fake_sleep):
            try:
                bot_ibkr._heartbeat_worker()
            except StopIteration:
                pass

        self.assertTrue(all(s > 0 for s in sleep_args))


# ═════════════════════════════════════════════════════════════════════════════
# 7. connect_ibkr integration
# ═════════════════════════════════════════════════════════════════════════════


class TestConnectIbkr(unittest.TestCase):
    def setUp(self):
        _reset_reconnect_state()
        bot._heartbeat_thread = None
        bot.CONFIG["ibkr_host"] = "127.0.0.1"
        bot.CONFIG["ibkr_port"] = 7497
        bot.CONFIG["ibkr_client_id"] = 1

    def _make_mock_ib(self):
        mock_ib = MagicMock()
        mock_ib.connect = MagicMock()
        mock_ib.isConnected = MagicMock(return_value=False)
        mock_ib.reqMarketDataType = MagicMock()
        mock_ib.reqPnL = MagicMock()

        # Use a list subclass that supports the += event-subscription pattern
        class EventList(list):
            def __iadd__(self, handler):
                self.append(handler)
                return self

        mock_ib.disconnectedEvent = EventList()
        return mock_ib

    def _make_thread_factory(self, started_list=None):
        if started_list is None:
            started_list = []

        def factory(*args, **kwargs):
            t = MagicMock()
            t.is_alive = MagicMock(return_value=False)
            t.start = MagicMock(side_effect=lambda: started_list.append(1))
            return t

        return factory, started_list

    def test_returns_true_on_success(self):
        mock_ib = self._make_mock_ib()
        factory, _ = self._make_thread_factory()
        with patch.object(bot, "ib", mock_ib), patch("threading.Thread", side_effect=factory):
            result = bot.connect_ibkr()
        self.assertTrue(result)

    def test_returns_false_on_connect_exception(self):
        mock_ib = self._make_mock_ib()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        with patch.object(bot, "ib", mock_ib):
            result = bot.connect_ibkr()
        self.assertFalse(result)

    def test_registers_disconnect_handler(self):
        mock_ib = self._make_mock_ib()
        factory, _ = self._make_thread_factory()
        with patch.object(bot, "ib", mock_ib), patch("threading.Thread", side_effect=factory):
            bot.connect_ibkr()
        self.assertIn(bot_ibkr._on_disconnected, mock_ib.disconnectedEvent)

    def test_handler_not_registered_on_failure(self):
        """Disconnect handler should NOT be registered if connect fails."""
        mock_ib = self._make_mock_ib()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        with patch.object(bot, "ib", mock_ib):
            bot.connect_ibkr()
        self.assertNotIn(bot_ibkr._on_disconnected, mock_ib.disconnectedEvent)

    def test_pnl_subscription_registered(self):
        mock_ib = self._make_mock_ib()
        factory, _ = self._make_thread_factory()
        with patch.object(bot, "ib", mock_ib), patch("threading.Thread", side_effect=factory):
            bot.connect_ibkr()
        self.assertIn("__pnl__", bot_state._subscription_registry)
        self.assertEqual(bot_state._subscription_registry["__pnl__"]["type"], "pnl")

    def test_pnl_subscription_not_registered_on_failure(self):
        mock_ib = self._make_mock_ib()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        with patch.object(bot, "ib", mock_ib):
            bot.connect_ibkr()
        self.assertNotIn("__pnl__", bot_state._subscription_registry)

    def test_heartbeat_thread_started(self):
        mock_ib = self._make_mock_ib()
        factory, started = self._make_thread_factory()
        with patch.object(bot, "ib", mock_ib), patch("threading.Thread", side_effect=factory):
            bot.connect_ibkr()
        self.assertGreater(len(started), 0)

    def test_heartbeat_thread_not_started_on_failure(self):
        mock_ib = self._make_mock_ib()
        mock_ib.connect = MagicMock(side_effect=OSError("refused"))
        factory, started = self._make_thread_factory()
        with patch.object(bot, "ib", mock_ib), patch("threading.Thread", side_effect=factory):
            bot.connect_ibkr()
        self.assertEqual(len(started), 0)

    def test_handler_registered_exactly_once_on_double_call(self):
        """Calling connect_ibkr twice must not duplicate the handler."""
        mock_ib = self._make_mock_ib()
        factory, _ = self._make_thread_factory()
        with patch.object(bot, "ib", mock_ib), patch("threading.Thread", side_effect=factory):
            bot.connect_ibkr()
            bot.connect_ibkr()
        count = mock_ib.disconnectedEvent.count(bot_ibkr._on_disconnected)
        self.assertEqual(count, 1)

    def test_ib_connect_called_with_correct_args(self):
        bot.CONFIG["ibkr_host"] = "10.0.0.1"
        bot.CONFIG["ibkr_port"] = 4001
        bot.CONFIG["ibkr_client_id"] = 5
        mock_ib = self._make_mock_ib()
        factory, _ = self._make_thread_factory()
        with patch.object(bot, "ib", mock_ib), patch("threading.Thread", side_effect=factory):
            bot.connect_ibkr()
        mock_ib.connect.assert_called_once_with("10.0.0.1", 4001, clientId=5, readonly=False)


# ═════════════════════════════════════════════════════════════════════════════
# 8. Webhook alert on reconnect exhaustion
# ═════════════════════════════════════════════════════════════════════════════


class TestReconnectExhaustedAlert(unittest.TestCase):
    def setUp(self):
        _reset_reconnect_state()
        bot.dash["status"] = "connected"

    def test_no_webhook_does_not_raise(self):
        bot.CONFIG["reconnect_alert_webhook"] = ""
        bot_ibkr._send_reconnect_exhausted_alert(10)  # must not raise

    def test_dashboard_status_updated(self):
        bot.CONFIG["reconnect_alert_webhook"] = ""
        bot_ibkr._send_reconnect_exhausted_alert(5)
        self.assertIn("reconnect failed", bot.dash["status"])

    def test_dashboard_status_contains_disconnected(self):
        bot.CONFIG["reconnect_alert_webhook"] = ""
        bot_ibkr._send_reconnect_exhausted_alert(5)
        self.assertIn("disconnected", bot.dash["status"])

    def test_webhook_post_attempted(self):
        bot.CONFIG["reconnect_alert_webhook"] = "http://example.com/hook"
        with patch("urllib.request.urlopen") as mock_urlopen:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=MagicMock())
            ctx.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = ctx
            bot_ibkr._send_reconnect_exhausted_alert(10)
        mock_urlopen.assert_called_once()

    def test_webhook_post_is_to_correct_url(self):
        url = "http://hooks.slack.com/test123"
        bot.CONFIG["reconnect_alert_webhook"] = url
        captured_req = []
        with patch("urllib.request.urlopen") as mock_urlopen:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=MagicMock())
            ctx.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = ctx

            def capture(req, timeout=None):
                captured_req.append(req)
                return ctx

            mock_urlopen.side_effect = capture
            bot_ibkr._send_reconnect_exhausted_alert(10)
        self.assertEqual(len(captured_req), 1)
        self.assertEqual(captured_req[0].full_url, url)

    def test_webhook_network_failure_does_not_raise(self):
        bot.CONFIG["reconnect_alert_webhook"] = "http://example.com/hook"
        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            bot_ibkr._send_reconnect_exhausted_alert(10)  # must not raise

    def test_webhook_payload_contains_attempt_count(self):
        """The POSTed JSON body should mention the number of attempts."""
        bot.CONFIG["reconnect_alert_webhook"] = "http://example.com/hook"
        payloads = []

        def capture(req, timeout=None):
            payloads.append(req.data)
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=MagicMock())
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        with patch("urllib.request.urlopen", side_effect=capture):
            bot_ibkr._send_reconnect_exhausted_alert(7)

        self.assertEqual(len(payloads), 1)
        payload_str = payloads[0].decode()
        self.assertIn("7", payload_str)


# ═════════════════════════════════════════════════════════════════════════════
# 9. Parametrized backoff math
# ═════════════════════════════════════════════════════════════════════════════

import pytest


@pytest.mark.parametrize(
    "base,max_w,attempts,expected",
    [
        (1, 16, 5, [1, 2, 4, 8, 16]),
        (1, 60, 7, [1, 2, 4, 8, 16, 32, 60]),
        (2, 8, 4, [2, 4, 8, 8]),
        (1, 4, 6, [1, 2, 4, 4, 4, 4]),
        (1, 1, 3, [1, 1, 1]),
    ],
)
def test_backoff_parametrized(base, max_w, attempts, expected):
    """Parametrized check that backoff sequence matches hand-calculated values."""
    _reset_reconnect_state()
    bot.CONFIG["reconnect_max_attempts"] = attempts
    bot.CONFIG["reconnect_max_wait_secs"] = max_w
    bot.CONFIG["reconnect_base_wait_secs"] = base
    bot.CONFIG["ibkr_host"] = "127.0.0.1"
    bot.CONFIG["ibkr_port"] = 7497
    bot.CONFIG["ibkr_client_id"] = 1

    mock_ib = MagicMock()
    mock_ib.connect = MagicMock(side_effect=OSError("refused"))
    sleep_calls = []
    with patch.object(bot, "ib", mock_ib), patch.object(bot_ibkr, "_send_reconnect_exhausted_alert"):
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            bot_ibkr._reconnect_worker()
    assert sleep_calls == expected, f"Expected {expected}, got {sleep_calls}"


# ═════════════════════════════════════════════════════════════════════════════
# 10. Module-level state initialisation
# ═════════════════════════════════════════════════════════════════════════════


class TestModuleLevelState(unittest.TestCase):
    def test_reconnect_lock_is_lock(self):
        self.assertIsInstance(bot_state._reconnect_lock, type(threading.Lock()))

    def test_reconnecting_starts_false(self):
        """After reset, _reconnecting must be False."""
        _reset_reconnect_state()
        self.assertFalse(bot._reconnecting)

    def test_subscription_registry_is_dict(self):
        self.assertIsInstance(bot_state._subscription_registry, dict)

    def test_heartbeat_thread_attr_exists(self):
        """Module must expose _heartbeat_thread attribute."""
        self.assertTrue(hasattr(bot, "_heartbeat_thread"))


if __name__ == "__main__":
    unittest.main()
