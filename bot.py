#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  bot.py                                     ║
# ║   Main orchestrator — runs everything                        ║
# ║                                                              ║
# ║   Usage:                                                     ║
# ║     export ANTHROPIC_API_KEY="sk-ant-..."                    ║
# ║     python3 bot.py                                           ║
# ║                                                              ║
# ║   Dashboard: http://localhost:8080                           ║
# ╚══════════════════════════════════════════════════════════════╝

import sys
import os
import json
import importlib
import hashlib
import logging
import threading
import types as _types
import schedule

from datetime import datetime, timezone
from colorama import Fore, Style, init as colorama_init

from config import CONFIG

# ── Sub-module imports ────────────────────────────────────────────────────────
import bot_state
from bot_state import dash, COLORS, EQUITY_FILE, _subscription_registry, _reconnect_lock, clog
from bot_ibkr import (
    _register_subscription, _unregister_subscription,
    _restore_subscriptions, _send_reconnect_exhausted_alert,
    _on_disconnected, _reconnect_worker,
    connect_ibkr, _heartbeat_worker,
)

# ── Logging ───────────────────────────────────────────────────────────────────
colorama_init()

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["log_file"]),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("decifer.bot")

# ── Dashboard HTML ────────────────────────────────────────────────────────────
from dashboard import DASHBOARD_HTML

# ── Persistence ───────────────────────────────────────────────────────────────
FAVOURITES_FILE = "favourites.json"
SETTINGS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "settings_override.json")
PROMPTS_FILE    = "prompt_versions.json"

# Keys that the dashboard is allowed to persist (prevent writing connection/system keys)
_DASHBOARD_SETTINGS_KEYS = {
    "risk_pct_per_trade", "daily_loss_limit", "max_positions",
    "min_cash_reserve", "max_single_position",
    "min_score_to_trade", "high_conviction_score", "agents_required_to_agree",
    "options_min_score", "options_max_risk_pct", "options_max_ivr", "options_target_delta", "options_delta_range",
    # News Sentinel
    "sentinel_enabled", "sentinel_poll_seconds", "sentinel_cooldown_minutes",
    "sentinel_batch_size", "sentinel_max_symbols", "sentinel_keyword_threshold",
    "sentinel_claude_confidence", "sentinel_min_confidence",
    "sentinel_use_ibkr", "sentinel_use_finviz",
    "sentinel_risk_multiplier", "sentinel_max_trades_per_hour",
}


def load_favourites() -> list:
    try:
        if os.path.exists(FAVOURITES_FILE):
            with open(FAVOURITES_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_favourites(favs: list):
    with open(FAVOURITES_FILE, 'w') as f:
        json.dump(favs, f)


def load_settings_overrides():
    """Load persisted settings overrides and apply them to CONFIG."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                overrides = json.load(f)
            applied = []
            for key, val in overrides.items():
                if key in CONFIG and key in _DASHBOARD_SETTINGS_KEYS:
                    CONFIG[key] = val
                    applied.append(key)
            # Sync dash state that was initialized from CONFIG at module level
            _sync_dash_from_config()
            if applied:
                clog("INFO", f"⚙️ Loaded saved settings: {', '.join(applied)}")
    except Exception as e:
        clog("ERROR", f"Failed to load settings overrides: {e}")


def _sync_dash_from_config():
    """Keep dash dictionary in sync with CONFIG values that were copied at module load."""
    dash["agents_required"] = CONFIG["agents_required_to_agree"]


def save_settings_overrides(settings: dict):
    """Persist dashboard settings to disk."""
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        # Merge with existing overrides (don't lose keys not in this request)
        existing = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                existing = json.load(f)
        for key, val in settings.items():
            if key in _DASHBOARD_SETTINGS_KEYS:
                existing[key] = val
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        clog("ERROR", f"Failed to save settings: {e}")


# ── HOT RELOAD SYSTEM ─────────────────────────────────────────────────────────
# Watches all .py files and reloads modules when they change.
# Bot keeps running — positions, state, and IBKR connection are preserved.

WATCHED_MODULES = {
    "signals":          "signals",
    "scanner":          "scanner",
    "agents":           "agents",
    "risk":             "risk",
    "orders":           "orders",
    "learning":         "learning",
    "dashboard":        "dashboard",
    "news":             "news",
    "news_sentinel":    "news_sentinel",
    "theme_tracker":    "theme_tracker",
    "sentinel_agents":  "sentinel_agents",
}

_file_hashes = {}


def _file_hash(path: str) -> str:
    """Return MD5 hash of file contents."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""


def _init_hashes():
    """Record initial file hashes on startup."""
    base = os.path.dirname(os.path.abspath(__file__))
    for name in list(WATCHED_MODULES.keys()) + ["bot", "config"]:
        path = os.path.join(base, f"{name}.py")
        _file_hashes[name] = _file_hash(path)


def check_and_reload():
    """
    Check all watched files for changes.
    If changed: reload the module, update the dashboard HTML, log the reload.
    Called at the start of every scan — zero overhead when nothing changed.
    """
    global DASHBOARD_HTML
    base    = os.path.dirname(os.path.abspath(__file__))
    changed = []

    for mod_name, import_name in WATCHED_MODULES.items():
        path    = os.path.join(base, f"{mod_name}.py")
        current = _file_hash(path)
        if current and current != _file_hashes.get(mod_name, ""):
            try:
                if import_name in sys.modules:
                    importlib.reload(sys.modules[import_name])
                    _file_hashes[mod_name] = current
                    changed.append(mod_name)
                    clog("INFO", f"🔄 Hot reload: {mod_name}.py updated and reloaded")
            except Exception as e:
                clog("ERROR", f"Hot reload failed for {mod_name}: {e}")

    # Special case: dashboard.py — update the HTML served to browser
    dash_path    = os.path.join(base, "dashboard.py")
    dash_current = _file_hash(dash_path)
    if dash_current and dash_current != _file_hashes.get("dashboard", ""):
        try:
            import dashboard as _dash
            importlib.reload(_dash)
            DASHBOARD_HTML = _dash.DASHBOARD_HTML
            _file_hashes["dashboard"] = dash_current
            changed.append("dashboard")
            clog("INFO", "🔄 Hot reload: dashboard.py updated — refresh browser to see changes")
        except Exception as e:
            clog("ERROR", f"Hot reload failed for dashboard: {e}")

    # Special case: config.py — reload config and apply new settings
    config_path    = os.path.join(base, "config.py")
    config_current = _file_hash(config_path)
    if config_current and config_current != _file_hashes.get("config", ""):
        try:
            import config as _config
            importlib.reload(_config)
            CONFIG.update(_config.CONFIG)
            # Re-apply dashboard overrides so they aren't wiped by config.py defaults
            load_settings_overrides()
            _file_hashes["config"] = config_current
            changed.append("config")
            clog("INFO", "🔄 Hot reload: config.py updated — new settings active immediately (dashboard overrides preserved)")
        except Exception as e:
            clog("ERROR", f"Hot reload failed for config: {e}")

    if changed:
        dash["hot_reload_count"] = dash.get("hot_reload_count", 0) + 1
        dash["last_reload"]      = datetime.now().strftime("%H:%M:%S")
        dash["last_reload_files"] = changed

    return changed


# ── Module __class__ shim ─────────────────────────────────────────────────────
# Forwards bot._reconnecting / bot._heartbeat_thread writes to bot_state, and
# provides live reads via __getattr__ when those keys are absent from __dict__.
#
# For bot.ib: we also store the value in __dict__ so patch.object() sees
# is_local=True and uses setattr/setattr (not setattr/delattr) for patch+restore.
#
# Why this matters for tests:
#   patch.object(bot, "ib", mock) → sets bot_state.ib = mock  (via __setattr__)
#                                    & bot.__dict__["ib"] = mock
#   patch exit (is_local=True)    → setattr(bot, "ib", original)
#                                 → restores bot_state.ib and bot.__dict__["ib"]
#
#   bot._reconnecting = False     → sets bot_state._reconnecting = False
#   bot._reconnecting  (read)     → __getattr__ → bot_state._reconnecting (live)

class _BotModule(_types.ModuleType):
    def __setattr__(self, name, value):
        if name == "ib":
            # Write-through to bot_state AND keep __dict__ current for patch.object
            bot_state.ib = value
            super().__setattr__(name, value)
        elif name in ("_reconnecting", "_heartbeat_thread"):
            # Write-through to bot_state only; __getattr__ provides live reads
            setattr(bot_state, name, value)
        else:
            super().__setattr__(name, value)

    def __getattr__(self, name):
        # Called only when name is absent from __dict__
        if name in ("_reconnecting", "_heartbeat_thread"):
            return getattr(bot_state, name)
        raise AttributeError(f"module 'bot' has no attribute {name!r}")


sys.modules[__name__].__class__ = _BotModule
# Seed ib into __dict__ (bypassing __setattr__) so patch.object is_local=True
sys.modules[__name__].__dict__["ib"] = bot_state.ib


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Lazy imports — keep module-level import chain minimal for tests
    from bot_dashboard import start_dashboard
    from bot_ibkr import (
        connect_ibkr, subscribe_pnl, backfill_trades_from_ibkr,
        sync_orders_from_ibkr, _on_order_status_event,
    )
    from bot_account import get_account_data, load_equity_history
    from bot_trading import run_scan, _check_kill, _process_close_queue
    from bot_sentinel import (
        _get_sentinel_universe, handle_news_trigger,
        handle_catalyst_trigger, countdown_tick,
    )
    from risk import (
        reset_daily_state, get_scan_interval,
        init_equity_high_water_mark_from_history,
    )
    from learning import (
        load_trades, load_orders, get_performance_summary,
    )
    from theme_tracker import load_custom_themes, get_all_themes
    from news_sentinel import NewsSentinel
    from catalyst_sentinel import CatalystSentinel

    print(f"""
{Fore.YELLOW}
  ██████╗ ███████╗ ██████╗██╗███████╗███████╗██████╗
  ██╔══██╗██╔════╝██╔════╝██║██╔════╝██╔════╝██╔══██╗
  ██║  ██║█████╗  ██║     ██║█████╗  █████╗  ██████╔╝
  ██║  ██║██╔══╝  ██║     ██║██╔══╝  ██╔══╝  ██╔══██╗
  ██████╔╝███████╗╚██████╗██║██║     ███████╗██║  ██║
  ╚═════╝ ╚══════╝ ╚═════╝╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝
{Style.RESET_ALL}
  {Fore.WHITE}<>  Autonomous AI Trading System  v3.0{Style.RESET_ALL}
  {Fore.WHITE}Account : {Fore.YELLOW}{CONFIG['active_account']}{Style.RESET_ALL}
  {Fore.WHITE}Agents  : {Fore.YELLOW}6 Claude agents | {CONFIG['agents_required_to_agree']}/6 required to trade{Style.RESET_ALL}
  {Fore.WHITE}Risk    : {Fore.YELLOW}{int(CONFIG['risk_pct_per_trade']*100)}% per trade | {int(CONFIG['daily_loss_limit']*100)}% daily limit{Style.RESET_ALL}
  {Fore.WHITE}Dashboard: {Fore.CYAN}http://localhost:{CONFIG['dashboard_port']}{Style.RESET_ALL}
""")

    # API key check
    if CONFIG["anthropic_api_key"] == "YOUR_API_KEY_HERE":
        print(f"{Fore.RED}ERROR: Set ANTHROPIC_API_KEY environment variable.{Style.RESET_ALL}")
        print(f"  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    # Start dashboard
    start_dashboard()

    # ── One-time setup: NLTK VADER lexicon (needed for social sentiment) ──
    try:
        import nltk
        nltk.download("vader_lexicon", quiet=True)
    except Exception:
        pass  # Optional — social_sentiment.py has keyword fallback

    # ── Background data collection (historical training data) ──
    def _background_data_collection():
        try:
            from data_collector import collect_all
            clog("INFO", "Background data collection started (historical training data)")
            result = collect_all(intraday=True, daily=True, add_ml_features=True)
            clog("INFO", f"Data collection complete: {result['total_rows']:,} rows, "
                 f"{result['daily_symbols']} daily + {result['intraday_symbols']} intraday symbols")
        except ImportError:
            clog("INFO", "data_collector.py not found — skipping historical data collection")
        except Exception as e:
            clog("ERROR", f"Background data collection error: {e}")

    threading.Thread(target=_background_data_collection, daemon=True, name="DataCollector").start()

    # Connect to IBKR
    if not connect_ibkr():
        print(f"{Fore.RED}ERROR: Could not connect to IBKR on port {CONFIG['ibkr_port']}.{Style.RESET_ALL}")
        print(f"  Make sure TWS is running with API enabled on port {CONFIG['ibkr_port']}")
        sys.exit(1)

    # Reset daily risk state — only once per calendar day
    pv, _ = get_account_data()
    today = datetime.now().date()
    if not hasattr(main, '_last_reset_date') or main._last_reset_date != today:
        reset_daily_state(pv)
        main._last_reset_date = today

    # Start countdown thread
    threading.Thread(target=countdown_tick, daemon=True).start()

    # Subscribe to live P&L
    subscribe_pnl()
    bot_state.ib.sleep(3)  # Wait for first P&L update to arrive from IBKR

    # Register real-time order status listener
    bot_state.ib.orderStatusEvent += _on_order_status_event

    # Backfill trade history from IBKR execution records
    bot_state.ib.sleep(2)  # Ensure commissionReports are linked to fills before backfill
    backfill_trades_from_ibkr()
    sync_orders_from_ibkr()

    # Initialise hot reload file hashes
    _init_hashes()

    # Load persistent data
    load_settings_overrides()   # Apply saved dashboard settings on top of config.py defaults
    dash["favourites"]     = load_favourites()
    dash["equity_history"] = load_equity_history()
    if dash["equity_history"]:
        init_equity_high_water_mark_from_history(dash["equity_history"])
    dash["all_trades"]  = load_trades()
    dash["all_orders"]  = load_orders()
    dash["performance"] = get_performance_summary(dash["all_trades"])

    dash["status"] = "running"

    # ── Load custom themes from disk ──────────────────────────────────────────
    load_custom_themes()
    dash["sentinel_themes"] = get_all_themes()

    run_scan()

    # Schedule subsequent scans dynamically based on session
    def scheduled_scan():
        run_scan()
        # Update sentinel dashboard state after each scan
        if bot_state._sentinel:
            dash["sentinel_stats"]  = bot_state._sentinel.stats
            dash["sentinel_status"] = bot_state._sentinel.stats.get("status", "unknown")
        # Reschedule with fresh interval
        interval = get_scan_interval()
        dash["next_scan_seconds"] = interval
        schedule.clear("scan")
        schedule.every(interval).seconds.do(scheduled_scan).tag("scan")

    interval = get_scan_interval()
    dash["next_scan_seconds"] = interval
    schedule.every(interval).seconds.do(scheduled_scan).tag("scan")

    # ── Start News Sentinel (independent background thread) ───────────────────
    if CONFIG.get("sentinel_enabled", True):
        bot_state._sentinel = NewsSentinel(
            get_universe_fn=_get_sentinel_universe,
            on_trigger_fn=handle_news_trigger,
            ib=bot_state.ib,
            poll_interval=CONFIG.get("sentinel_poll_seconds", 45),
        )
        bot_state._sentinel.start()
        dash["sentinel_status"] = "running"
        dash["sentinel_stats"]  = bot_state._sentinel.stats
        clog("INFO", f"📡 News Sentinel active | polling every {CONFIG.get('sentinel_poll_seconds', 45)}s")
    else:
        clog("INFO", "📡 News Sentinel disabled (sentinel_enabled=False in config)")

    # ── Start Catalyst Sentinel (M&A / acquisition monitor) ──────────────────
    if CONFIG.get("catalyst_sentinel_enabled", True):
        bot_state._catalyst_sentinel = CatalystSentinel(
            get_universe_fn=_get_sentinel_universe,
            on_trigger_fn=handle_catalyst_trigger,
            ib=bot_state.ib,
        )
        bot_state._catalyst_sentinel.start()
        dash["catalyst_triggers"]       = []
        dash["catalyst_sentinel_stats"] = bot_state._catalyst_sentinel.stats
        clog("INFO",
             f"⚡ Catalyst Sentinel active | "
             f"news every {CONFIG.get('catalyst_news_poll_seconds', 60)}s | "
             f"EDGAR every {CONFIG.get('catalyst_edgar_poll_seconds', 600)}s")
    else:
        clog("INFO", "⚡ Catalyst Sentinel disabled (catalyst_sentinel_enabled=False in config)")

    # ── Start Social Sentiment background polling ─────────────────────────────
    try:
        from social_sentiment import start_sentiment_polling
        start_sentiment_polling()
        clog("INFO", "Social sentiment polling active (Reddit + ApeWisdom, 60s interval)")
    except ImportError:
        clog("INFO", "Social sentiment module not installed — skipping background polling")
    except Exception as e:
        clog("ERROR", f"Social sentiment startup error: {e}")

    # ── Start Telegram Kill Switch ────────────────────────────────────────────
    _tg_cfg      = CONFIG.get("telegram", {})
    _tg_token    = _tg_cfg.get("bot_token", "")
    _tg_chat_ids = _tg_cfg.get("authorized_chat_ids", [])
    if _tg_token and _tg_chat_ids:
        try:
            import telegram_bot as _tg_mod

            def _tg_on_kill() -> str:
                dash["killed"] = True
                clog("RISK", "🚨 Telegram KILL — executing FLATTEN ALL...")
                try:
                    from orders import flatten_all
                    flatten_all(bot_state.ib)
                    clog("RISK", "🚨 Telegram FLATTEN ALL complete")
                    return "✅ KILL executed — all positions flattened and bot halted."
                except Exception as _exc:
                    clog("ERROR", f"🚨 Telegram FLATTEN ALL failed: {_exc}")
                    return f"❌ FLATTEN ALL failed: {_exc}"

            def _tg_on_status() -> str:
                state = "HALTED 🛑" if dash.get("killed") else ("PAUSED ⏸" if dash.get("paused") else "RUNNING ✅")
                n_pos = len(dash.get("positions", {}))
                return f"Bot state: {state}\nOpen positions: {n_pos}"

            def _tg_on_resume() -> str:
                if not dash.get("killed"):
                    return "ℹ️ Bot is not halted — nothing to resume."
                dash["killed"] = False
                clog("INFO", "▶️ Telegram RESUME — kill flag cleared")
                return "▶️ Bot resumed. Kill flag cleared."

            _tg_mod.start(_tg_token, _tg_chat_ids, _tg_on_kill, _tg_on_status, _tg_on_resume)
            clog("INFO", f"📱 Telegram kill switch active | {len(_tg_chat_ids)} authorized chat(s)")
        except ImportError:
            clog("INFO", "telegram_bot.py not found — Telegram kill switch disabled")
        except Exception as _tg_exc:
            clog("ERROR", f"Telegram kill switch startup error: {_tg_exc}")
    else:
        clog("INFO", "📱 Telegram kill switch not configured (set TELEGRAM_BOT_TOKEN + authorized_chat_ids)")

    # ── Start ML Signal Enhancement ───────────────────────────────────────────
    try:
        from ml_engine import enhance_score
        if CONFIG.get("ml_enabled", False):
            clog("INFO", "ML signal enhancement active (will enhance scores when models trained)")
        else:
            clog("INFO", "ML engine available but disabled (ml_enabled=False)")
    except ImportError:
        clog("INFO", "ML engine not installed — skipping")
    except Exception as e:
        clog("ERROR", f"ML engine startup error: {e}")

    clog("INFO", f"<> Decifer running. Dashboard → http://localhost:{CONFIG['dashboard_port']}")
    clog("INFO", "Press Ctrl+C to stop.")

    try:
        while True:
            # ── Kill switch check (runs on main thread for ib_insync safety) ──
            _check_kill()

            # ── Process individual position close requests ──
            _process_close_queue()

            # ── Sync sentinel state to dashboard ──
            if bot_state._sentinel:
                dash["sentinel_stats"]  = bot_state._sentinel.stats
                dash["sentinel_status"] = bot_state._sentinel.stats.get("status", "unknown")

            # ── Sync catalyst sentinel state to dashboard ──
            if bot_state._catalyst_sentinel:
                dash["catalyst_sentinel_stats"] = bot_state._catalyst_sentinel.stats

            schedule.run_pending()
            bot_state.ib.sleep(1)
    except KeyboardInterrupt:
        dash["status"] = "stopped"
        if bot_state._sentinel:
            bot_state._sentinel.stop()
        if bot_state._catalyst_sentinel:
            bot_state._catalyst_sentinel.stop()
        clog("INFO", "<> Decifer stopped.")
        bot_state.ib.disconnect()


if __name__ == "__main__":
    main()
