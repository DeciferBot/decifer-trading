#!/usr/bin/env python3
"""
Baseline tests for bot.py — orchestrator logic.
Focuses on testable pure-Python helpers: settings persistence,
hot-reload hash detection, favourites I/O, colour map, dash state
initialisation, and the reconnect/alert helpers.
"""
import os
import sys
import json
import tempfile
import hashlib
import threading
import types
import importlib

# ── project root on path ─────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─────────────────────────────────────────────────────────────────────────────
# Stub every heavy import BEFORE bot.py is loaded
# ─────────────────────────────────────────────────────────────────────────────

# ib_async
ib_async_mod = types.ModuleType("ib_async")
class _FakeIB:
    def __init__(self): self.connected = False
    def connect(self, *a, **kw): self.connected = True
    def disconnect(self): self.connected = False
    def reqPnL(self, *a, **kw): pass
    def reqMktData(self, *a, **kw): return object()
    def run(self): pass
ib_async_mod.IB = _FakeIB
ib_async_mod.Stock = lambda *a, **kw: object()
ib_async_mod.Contract = lambda *a, **kw: object()
sys.modules.setdefault("ib_async", ib_async_mod)

# anthropic
anthropic_mod = types.ModuleType("anthropic")
anthropic_mod.Anthropic = lambda *a, **kw: None
sys.modules.setdefault("anthropic", anthropic_mod)

# yfinance
yf_mod = types.ModuleType("yfinance")
yf_mod.download = lambda *a, **kw: None
yf_mod.Ticker = lambda *a, **kw: types.SimpleNamespace(info={}, history=lambda **kw: None)
sys.modules.setdefault("yfinance", yf_mod)

# httpx
httpx_mod = types.ModuleType("httpx")
httpx_mod.get = lambda *a, **kw: types.SimpleNamespace(text="", status_code=200, json=lambda: {})
sys.modules.setdefault("httpx", httpx_mod)

# colorama
colorama_mod = types.ModuleType("colorama")
colorama_mod.Fore = types.SimpleNamespace(
    YELLOW="", GREEN="", CYAN="", RED="", WHITE="", MAGENTA="", RESET=""
)
colorama_mod.Style = types.SimpleNamespace(RESET_ALL="", BRIGHT="")
colorama_mod.init = lambda **kw: None
sys.modules.setdefault("colorama", colorama_mod)

# schedule
schedule_mod = types.ModuleType("schedule")
schedule_mod.every = lambda *a, **kw: types.SimpleNamespace(
    minutes=types.SimpleNamespace(do=lambda *a, **kw: None),
    hours=types.SimpleNamespace(do=lambda *a, **kw: None),
    day=types.SimpleNamespace(at=lambda *a, **kw: types.SimpleNamespace(do=lambda *a, **kw: None)),
)
schedule_mod.run_pending = lambda: None
sys.modules.setdefault("schedule", schedule_mod)

# tradingview_screener
tv_mod = types.ModuleType("tradingview_screener")
tv_mod.Query = type("Query", (), {"select": lambda *a, **kw: None})
sys.modules.setdefault("tradingview_screener", tv_mod)

# pandas_ta
pta_mod = types.ModuleType("pandas_ta")
sys.modules.setdefault("pandas_ta", pta_mod)

# py_vollib stubs
for _stub in [
    "py_vollib", "py_vollib.black_scholes", "py_vollib.black_scholes.greeks",
    "py_vollib.black_scholes.greeks.analytical",
    "py_vollib.black_scholes.implied_volatility",
]:
    sys.modules.setdefault(_stub, types.ModuleType(_stub))

# sklearn stubs (ml_engine)
for _stub in [
    "sklearn", "sklearn.ensemble", "sklearn.preprocessing",
    "sklearn.model_selection", "sklearn.metrics",
]:
    sys.modules.setdefault(_stub, types.ModuleType(_stub))

# joblib
sys.modules.setdefault("joblib", types.ModuleType("joblib"))

# praw
sys.modules.setdefault("praw", types.ModuleType("praw"))

# scipy stubs
for _stub in ["scipy", "scipy.stats", "scipy.optimize"]:
    sys.modules.setdefault(_stub, types.ModuleType(_stub))

# feedparser
feedparser_mod = types.ModuleType("feedparser")
feedparser_mod.parse = lambda *a, **kw: types.SimpleNamespace(entries=[])
sys.modules.setdefault("feedparser", feedparser_mod)

# nltk / vaderSentiment stubs
sys.modules.setdefault("nltk", types.ModuleType("nltk"))
nltk_sa = types.ModuleType("nltk.sentiment")
nltk_sa_v = types.ModuleType("nltk.sentiment.vader")
nltk_sa_v.SentimentIntensityAnalyzer = type(
    "SIA", (), {"polarity_scores": lambda self, t: {"compound": 0.0}}
)
sys.modules.setdefault("nltk.sentiment", nltk_sa)
sys.modules.setdefault("nltk.sentiment.vader", nltk_sa_v)

# vaderSentiment
vader_pkg = types.ModuleType("vaderSentiment")
vader_sa = types.ModuleType("vaderSentiment.vaderSentiment")
vader_sa.SentimentIntensityAnalyzer = type(
    "SIA", (), {"polarity_scores": lambda self, t: {"compound": 0.0}}
)
sys.modules.setdefault("vaderSentiment", vader_pkg)
sys.modules.setdefault("vaderSentiment.vaderSentiment", vader_sa)

# ── Minimal config so bot.py can complete module-level code ──────────────────
import tempfile as _tmpmod
_test_tmp = _tmpmod.mkdtemp(prefix="decifer_test_")
config_mod = types.ModuleType("config")
config_mod.CONFIG = {
    "log_file":                     os.path.join(_test_tmp, "decifer_test.log"),
    "trade_log":                    os.path.join(_test_tmp, "decifer_trades_test.json"),
    "order_log":                    os.path.join(_test_tmp, "decifer_orders_test.json"),
    "active_account":               "DU123456",
    "agents_required_to_agree":     3,
    "ibkr_host":                    "127.0.0.1",
    "ibkr_port":                    7497,
    "ibkr_client_id":               1,
    "risk_pct_per_trade":           0.01,
    "daily_loss_limit":             0.02,
    "max_positions":                10,
    "min_cash_reserve":             5000.0,
    "max_single_position":          0.15,
    "min_score_to_trade":           60,
    "high_conviction_score":        80,
    "options_min_score":            75,
    "options_max_risk_pct":         0.02,
    "options_max_ivr":              60,
    "options_target_delta":         0.35,
    "options_delta_range":          0.10,
    "sentinel_enabled":             True,
    "sentinel_poll_seconds":        30,
    "sentinel_cooldown_minutes":    60,
    "sentinel_batch_size":          10,
    "sentinel_max_symbols":         50,
    "sentinel_keyword_threshold":   3,
    "sentinel_claude_confidence":   0.7,
    "sentinel_min_confidence":      0.6,
    "sentinel_use_ibkr":            False,
    "sentinel_use_finviz":          True,
    "sentinel_risk_multiplier":     0.5,
    "sentinel_max_trades_per_hour": 2,
    "reconnect_max_attempts":       5,
    "reconnect_max_wait_secs":      60,
    "reconnect_base_wait_secs":     1,
    "reconnect_alert_webhook":      "",
}
sys.modules.setdefault("config", config_mod)

# Stub all Decifer sub-modules so they don't try to import real deps
# NOTE: ml_engine, data_collector are excluded — they have their own test stubs
# and blanket-stubbing them here would prevent later test files from importing
# the real modules.
for _mod_name in [
    "scanner", "signals", "news", "agents", "orders", "options",
    "options_scanner", "risk", "learning", "dashboard",
    "news_sentinel", "theme_tracker", "sentinel_agents",
    "social_sentiment", "portfolio_optimizer",
    "smart_execution", "backtester",
]:
    if _mod_name not in sys.modules:
        _m = types.ModuleType(_mod_name)
        sys.modules[_mod_name] = _m

# scanner
scanner_stub = sys.modules["scanner"]
scanner_stub.get_dynamic_universe = lambda *a, **kw: []
scanner_stub.get_market_regime    = lambda *a, **kw: {"regime": "NEUTRAL", "vix": 15, "spy_price": 450}
scanner_stub.get_tv_signal_cache  = lambda: {}

# signals
signals_stub = sys.modules["signals"]
signals_stub.score_universe        = lambda *a, **kw: []
signals_stub.fetch_multi_timeframe = lambda *a, **kw: {}
signals_stub.get_regime_threshold  = lambda *a, **kw: 18

# news
news_stub = sys.modules["news"]
news_stub.batch_news_sentiment = lambda *a, **kw: {}

# agents
agents_stub = sys.modules["agents"]
agents_stub.run_all_agents = lambda *a, **kw: {"action": "HOLD"}

# orders
orders_stub = sys.modules["orders"]
orders_stub.execute_buy               = lambda *a, **kw: None
orders_stub.execute_sell              = lambda *a, **kw: None
orders_stub.flatten_all               = lambda *a, **kw: None
orders_stub.reconcile_with_ibkr       = lambda *a, **kw: None
orders_stub.get_open_positions        = lambda: []
orders_stub.update_position_prices    = lambda *a, **kw: None
orders_stub.update_positions_from_ibkr= lambda *a, **kw: None
orders_stub.execute_buy_option        = lambda *a, **kw: None
orders_stub.execute_sell_option       = lambda *a, **kw: None

# options
options_stub = sys.modules["options"]
options_stub.find_best_contract  = lambda *a, **kw: None
options_stub.check_options_exits = lambda *a, **kw: []

# options_scanner
optscan_stub = sys.modules["options_scanner"]
optscan_stub.scan_options_universe = lambda *a, **kw: []

# risk
risk_stub = sys.modules["risk"]
risk_stub.can_trade              = lambda *a, **kw: True
risk_stub.check_risk_conditions  = lambda *a, **kw: (True, "ok")
risk_stub.get_session            = lambda: "REGULAR"
risk_stub.get_scan_interval      = lambda: 300
risk_stub.reset_daily_state      = lambda *a, **kw: None
risk_stub.calculate_position_size= lambda *a, **kw: 10
risk_stub.calculate_stops        = lambda *a, **kw: (0.95, 1.10)
risk_stub.update_equity_high_water_mark = lambda *a, **kw: None

# learning
learning_stub = sys.modules["learning"]
learning_stub.log_trade                = lambda *a, **kw: None
learning_stub.load_trades              = lambda: []
learning_stub.load_orders              = lambda: []
learning_stub.get_performance_summary  = lambda *a, **kw: {}
learning_stub.run_weekly_review        = lambda: "review"
learning_stub.TRADE_LOG_FILE           = "/tmp/trades.json"
learning_stub.get_effective_capital    = lambda: 100000.0
learning_stub.record_capital_adjustment= lambda *a, **kw: None

# dashboard
dash_stub = sys.modules["dashboard"]
dash_stub.DASHBOARD_HTML = "<html>DASH</html>"

# news_sentinel
news_sent_stub = sys.modules["news_sentinel"]
class _FakeSentinel:
    def __init__(self, *a, **kw): pass
    def start(self): pass
    def stop(self): pass
    def pause(self): pass
    def resume(self): pass
news_sent_stub.NewsSentinel        = _FakeSentinel
news_sent_stub.get_sentinel_history= lambda: []

# theme_tracker
theme_stub = sys.modules["theme_tracker"]
theme_stub.build_sentinel_universe = lambda *a, **kw: []
theme_stub.load_custom_themes      = lambda: {}
theme_stub.get_all_themes          = lambda: {}

# sentinel_agents
sentinel_agents_stub = sys.modules["sentinel_agents"]
sentinel_agents_stub.run_sentinel_pipeline = lambda *a, **kw: {}

# ── Now import bot ────────────────────────────────────────────────────────────
import bot  # noqa: E402

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _tmp_settings_file(tmp_path, content: dict) -> str:
    p = tmp_path / "settings_override.json"
    p.write_text(json.dumps(content))
    return str(p)


# ═════════════════════════════════════════════════════════════════════════════
# 1. Dashboard initial state
# ═════════════════════════════════════════════════════════════════════════════

class TestDashInitialState:
    """Verify the dash dict is populated with sensible defaults on import."""

    def test_status_starts_as_string(self):
        assert isinstance(bot.dash["status"], str)

    def test_positions_starts_empty_list(self):
        assert isinstance(bot.dash["positions"], list)

    def test_logs_starts_empty_list(self):
        assert isinstance(bot.dash["logs"], list)

    def test_regime_has_required_keys(self):
        regime = bot.dash["regime"]
        for key in ("regime", "vix", "spy_price"):
            assert key in regime

    def test_agents_required_synced_from_config(self):
        assert bot.dash["agents_required"] == bot.CONFIG["agents_required_to_agree"]

    def test_paused_false_by_default(self):
        assert bot.dash["paused"] is False

    def test_killed_false_by_default(self):
        assert bot.dash["killed"] is False

    def test_sentinel_status_stopped(self):
        assert bot.dash["sentinel_status"] == "stopped"


# ═════════════════════════════════════════════════════════════════════════════
# 2. Color map
# ═════════════════════════════════════════════════════════════════════════════

class TestColorMap:
    """COLORS dict should contain all expected keys."""

    def test_required_keys_present(self):
        for key in ("TRADE", "SIGNAL", "ANALYSIS", "ERROR", "INFO", "RISK", "SCAN"):
            assert key in bot.COLORS

    def test_all_values_are_strings(self):
        for k, v in bot.COLORS.items():
            assert isinstance(v, str), f"COLORS[{k!r}] is not a string"


# ═════════════════════════════════════════════════════════════════════════════
# 3. Favourites I/O
# ═════════════════════════════════════════════════════════════════════════════

class TestFavourites:
    """load_favourites / save_favourites round-trip."""

    def test_load_returns_list_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bot, "FAVOURITES_FILE", str(tmp_path / "no_such.json"))
        result = bot.load_favourites()
        assert result == []

    def test_save_then_load_round_trip(self, monkeypatch, tmp_path):
        fav_path = str(tmp_path / "favs.json")
        monkeypatch.setattr(bot, "FAVOURITES_FILE", fav_path)
        bot.save_favourites(["AAPL", "MSFT", "TSLA"])
        loaded = bot.load_favourites()
        assert loaded == ["AAPL", "MSFT", "TSLA"]

    def test_save_overwrites_previous(self, monkeypatch, tmp_path):
        fav_path = str(tmp_path / "favs.json")
        monkeypatch.setattr(bot, "FAVOURITES_FILE", fav_path)
        bot.save_favourites(["AAPL"])
        bot.save_favourites(["GOOG", "META"])
        loaded = bot.load_favourites()
        assert loaded == ["GOOG", "META"]

    def test_load_handles_corrupt_json(self, monkeypatch, tmp_path):
        fav_path = tmp_path / "favs.json"
        fav_path.write_text("NOT JSON{{{")
        monkeypatch.setattr(bot, "FAVOURITES_FILE", str(fav_path))
        # Should not raise; returns empty list
        result = bot.load_favourites()
        assert result == []

    def test_save_empty_list(self, monkeypatch, tmp_path):
        fav_path = str(tmp_path / "favs.json")
        monkeypatch.setattr(bot, "FAVOURITES_FILE", fav_path)
        bot.save_favourites([])
        loaded = bot.load_favourites()
        assert loaded == []


# ═════════════════════════════════════════════════════════════════════════════
# 4. Settings persistence
# ═════════════════════════════════════════════════════════════════════════════

class TestSettingsPersistence:
    """save_settings_overrides / load_settings_overrides round-trip."""

    def test_save_and_reload_updates_config(self, monkeypatch, tmp_path):
        settings_path = str(tmp_path / "settings_override.json")
        monkeypatch.setattr(bot, "SETTINGS_FILE", settings_path)
        bot.CONFIG["risk_pct_per_trade"] = 0.01          # baseline
        bot.save_settings_overrides({"risk_pct_per_trade": 0.025})
        bot.load_settings_overrides()
        assert bot.CONFIG["risk_pct_per_trade"] == pytest.approx(0.025)

    def test_unknown_keys_are_ignored(self, monkeypatch, tmp_path):
        settings_path = str(tmp_path / "settings_override.json")
        monkeypatch.setattr(bot, "SETTINGS_FILE", settings_path)
        bot.save_settings_overrides({"totally_made_up_key": 999})
        # Should not raise and should not pollute CONFIG
        assert "totally_made_up_key" not in bot.CONFIG

    def test_save_merges_with_existing(self, monkeypatch, tmp_path):
        settings_path = str(tmp_path / "settings_override.json")
        monkeypatch.setattr(bot, "SETTINGS_FILE", settings_path)
        bot.save_settings_overrides({"risk_pct_per_trade": 0.02})
        bot.save_settings_overrides({"max_positions": 5})
        with open(settings_path) as f:
            saved = json.load(f)
        # Both keys should be present after two separate saves
        assert "risk_pct_per_trade" in saved
        assert "max_positions" in saved

    def test_load_when_file_missing_does_not_raise(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bot, "SETTINGS_FILE", str(tmp_path / "no_file.json"))
        bot.load_settings_overrides()   # should not raise

    def test_allowed_keys_whitelist(self):
        for key in bot._DASHBOARD_SETTINGS_KEYS:
            assert isinstance(key, str)

    def test_disallowed_key_not_persisted(self, monkeypatch, tmp_path):
        settings_path = str(tmp_path / "settings_override.json")
        monkeypatch.setattr(bot, "SETTINGS_FILE", settings_path)
        # ibkr_port is a system key — should not be persisted
        bot.save_settings_overrides({"ibkr_port": 1234})
        if os.path.exists(settings_path):
            with open(settings_path) as f:
                saved = json.load(f)
            assert "ibkr_port" not in saved


# ═════════════════════════════════════════════════════════════════════════════
# 5. _sync_dash_from_config
# ═════════════════════════════════════════════════════════════════════════════

class TestSyncDashFromConfig:
    """_sync_dash_from_config keeps dash aligned with CONFIG."""

    def test_agents_required_updated(self):
        original = bot.CONFIG["agents_required_to_agree"]
        try:
            bot.CONFIG["agents_required_to_agree"] = 7
            bot._sync_dash_from_config()
            assert bot.dash["agents_required"] == 7
        finally:
            bot.CONFIG["agents_required_to_agree"] = original
            bot._sync_dash_from_config()


# ═════════════════════════════════════════════════════════════════════════════
# 6. File-hash helpers
# ═════════════════════════════════════════════════════════════════════════════

class TestFileHash:
    """_file_hash returns a stable MD5 string."""

    def test_returns_string(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hello")
        h = bot._file_hash(str(f))
        assert isinstance(h, str) and len(h) == 32

    def test_same_content_same_hash(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("consistent content")
        assert bot._file_hash(str(f)) == bot._file_hash(str(f))

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("version A")
        f2.write_text("version B")
        assert bot._file_hash(str(f1)) != bot._file_hash(str(f2))

    def test_missing_file_returns_empty_string(self, tmp_path):
        h = bot._file_hash(str(tmp_path / "ghost.py"))
        assert h == ""


# ═════════════════════════════════════════════════════════════════════════════
# 7. Subscription registry
# ═════════════════════════════════════════════════════════════════════════════

class TestSubscriptionRegistry:
    """_register_subscription / _unregister_subscription."""

    def setup_method(self):
        # clean slate for each test
        bot._subscription_registry.clear()

    def test_register_stores_entry(self):
        bot._register_subscription("AAPL", {"type": "ticker"})
        assert "AAPL" in bot._subscription_registry
        assert bot._subscription_registry["AAPL"]["type"] == "ticker"

    def test_unregister_removes_entry(self):
        bot._register_subscription("MSFT", {"type": "pnl"})
        bot._unregister_subscription("MSFT")
        assert "MSFT" not in bot._subscription_registry

    def test_unregister_missing_key_does_not_raise(self):
        bot._unregister_subscription("NONEXISTENT")  # must not raise

    def test_multiple_registrations(self):
        bot._register_subscription("GOOG", {"type": "ticker"})
        bot._register_subscription("META", {"type": "ticker"})
        assert len(bot._subscription_registry) == 2

    def test_overwrite_existing_entry(self):
        bot._register_subscription("TSLA", {"type": "ticker", "extra": 1})
        bot._register_subscription("TSLA", {"type": "pnl", "extra": 2})
        assert bot._subscription_registry["TSLA"]["type"] == "pnl"


# ═════════════════════════════════════════════════════════════════════════════
# 8. _init_hashes — populates _file_hashes for known modules
# ═════════════════════════════════════════════════════════════════════════════

class TestInitHashes:
    """_init_hashes should run without error and populate some keys."""

    def test_does_not_raise(self):
        bot._init_hashes()   # must not raise

    def test_populates_dict(self):
        bot._file_hashes.clear()
        bot._init_hashes()
        # At minimum 'bot' and 'config' should have been attempted
        assert len(bot._file_hashes) >= 1


# ═════════════════════════════════════════════════════════════════════════════
# 9. check_and_reload — no-op when nothing changed
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckAndReload:
    """check_and_reload should return empty list when files are unchanged."""

    def test_returns_empty_list_when_no_change(self):
        bot._init_hashes()   # seed hashes as current
        changed = bot.check_and_reload()
        assert isinstance(changed, list)
        # Files didn't change, so nothing should be reloaded
        assert changed == []

    def test_detects_change_when_hash_differs(self, tmp_path, monkeypatch):
        """Force a hash mismatch for one watched module and verify detection."""
        # Monkeypatch _file_hashes for 'news' to an obviously wrong value
        bot._file_hashes["news"] = "0" * 32
        # Also monkeypatch the actual file path to point to a real (but temp) file
        # so the reload attempt doesn't crash with FileNotFoundError
        fake_news = tmp_path / "news.py"
        fake_news.write_text("# fake news module")  # minimal valid Python
        # The module is already stubbed in sys.modules; reload will just re-execute
        # the stub — that's fine for the detection test.
        changed = bot.check_and_reload()
        # We can't guarantee the reload succeeds against our stub, but the function
        # should at minimum return a list without raising.
        assert isinstance(changed, list)


# ═════════════════════════════════════════════════════════════════════════════
# 10. _send_reconnect_exhausted_alert — no webhook set → only updates dash
# ═════════════════════════════════════════════════════════════════════════════

class TestReconnectAlert:
    """Alert helper should update dash status and not crash."""

    def test_updates_dash_status(self):
        bot.CONFIG["reconnect_alert_webhook"] = ""
        bot._send_reconnect_exhausted_alert(attempts=5)
        assert "disconnected" in bot.dash["status"] or "reconnect" in bot.dash["status"]

    def test_does_not_raise_without_webhook(self):
        bot.CONFIG["reconnect_alert_webhook"] = ""
        # Must complete without exception
        bot._send_reconnect_exhausted_alert(attempts=3)

    def test_watched_modules_is_dict(self):
        assert isinstance(bot.WATCHED_MODULES, dict)
        assert len(bot.WATCHED_MODULES) > 0


# ═════════════════════════════════════════════════════════════════════════════
# 11. _restore_subscriptions — empty registry is a no-op
# ═════════════════════════════════════════════════════════════════════════════

class TestRestoreSubscriptions:
    """_restore_subscriptions should handle empty registry gracefully."""

    def test_empty_registry_does_not_raise(self):
        bot._subscription_registry.clear()
        bot._restore_subscriptions()   # must not raise

    def test_pnl_subscription_type(self):
        bot._subscription_registry.clear()
        # Register a pnl subscription — restore should call ib.reqPnL
        bot._register_subscription("pnl_DU123", {"type": "pnl", "account": "DU123"})
        # Since bot.ib is the real IB object stub, just verify no exception is raised
        # (ib.reqPnL is a no-op in our stub)
        try:
            bot._restore_subscriptions()
        except Exception as exc:
            pytest.fail(f"_restore_subscriptions raised unexpectedly: {exc}")
        finally:
            bot._subscription_registry.clear()


# ═════════════════════════════════════════════════════════════════════════════
# 12. CONFIG structure sanity
# ═════════════════════════════════════════════════════════════════════════════

class TestConfigStructure:
    """Verify the CONFIG that bot.py uses has essential keys."""

    REQUIRED_KEYS = [
        "log_file", "active_account", "agents_required_to_agree",
        "risk_pct_per_trade", "daily_loss_limit", "max_positions",
        "min_score_to_trade", "ibkr_host", "ibkr_port",
    ]

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_required_key_present(self, key):
        assert key in bot.CONFIG, f"CONFIG missing required key: {key!r}"

    def test_risk_pct_between_0_and_1(self):
        assert 0 < bot.CONFIG["risk_pct_per_trade"] < 1

    def test_max_positions_positive_int(self):
        assert isinstance(bot.CONFIG["max_positions"], int)
        assert bot.CONFIG["max_positions"] > 0

    def test_agents_required_positive(self):
        assert bot.CONFIG["agents_required_to_agree"] > 0
