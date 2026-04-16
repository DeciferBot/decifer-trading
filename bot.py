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
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import os
import resource
import sys
import threading
import types as _types

import schedule

# Raise the fd soft limit to match the hard limit so the bot never hits
# "[Errno 24] Too many open files" from accumulated CLOSE_WAIT sockets.
try:
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if _soft < 4096:
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, _hard), _hard))
except Exception:
    pass

import zoneinfo
from datetime import datetime

_ET = zoneinfo.ZoneInfo("America/New_York")
from colorama import Fore, Style
from colorama import init as colorama_init

# ── Sub-module imports ────────────────────────────────────────────────────────
import bot_state
from bot_ibkr import (
    _register_subscription,
    _restore_subscriptions,
    _send_reconnect_exhausted_alert,
    _unregister_subscription,
    connect_ibkr,
)
from bot_state import _subscription_registry, clog, dash
from config import CONFIG

# ── Logging ───────────────────────────────────────────────────────────────────
colorama_init()

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Rotating file handler — 50MB per file, keep 10 backups (500MB ceiling).
# Prevents decifer.log from ballooning (9.4GB on 2026-04-14 before the OOM crash).
from logging.handlers import RotatingFileHandler as _RotatingFileHandler

_file_handler = _RotatingFileHandler(
    CONFIG["log_file"],
    maxBytes=50 * 1024 * 1024,
    backupCount=10,
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %Z",
    handlers=[_file_handler, logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("decifer.bot")

# ── Version ───────────────────────────────────────────────────────────────────
from version import __codename__, __version__

log.info(f"Decifer Trading v{__version__} ({__codename__}) — starting up")

# ── Dashboard HTML ────────────────────────────────────────────────────────────
from dashboard import DASHBOARD_HTML

DASHBOARD_HTML = DASHBOARD_HTML.replace("Autonomous AI Trading", f"Autonomous AI Trading &nbsp;·&nbsp; v{__version__}")

# ── Persistence ───────────────────────────────────────────────────────────────
FAVOURITES_FILE = "favourites.json"
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "settings_override.json")
PROMPTS_FILE = "prompt_versions.json"

# Keys that the dashboard is allowed to persist (prevent writing connection/system keys)
_DASHBOARD_SETTINGS_KEYS = {
    "risk_pct_per_trade",
    "daily_loss_limit",
    "max_positions",
    "min_cash_reserve",
    "max_single_position",
    "min_score_to_trade",
    "high_conviction_score",
    "agents_required_to_agree",
    "options_min_score",
    "options_max_risk_pct",
    "options_max_ivr",
    "options_target_delta",
    "options_delta_range",
    # News Sentinel
    "sentinel_enabled",
    "sentinel_poll_seconds",
    "sentinel_cooldown_minutes",
    "sentinel_batch_size",
    "sentinel_max_symbols",
    "sentinel_keyword_threshold",
    "sentinel_claude_confidence",
    "sentinel_min_confidence",
    "sentinel_use_ibkr",
    "sentinel_use_finviz",
    "sentinel_risk_multiplier",
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
    with open(FAVOURITES_FILE, "w") as f:
        json.dump(favs, f)


# ── Voice command execution (main-thread, called from the main loop) ──────────

def _log_voice_audit_bot(action: str, symbol, voice_text: str, result: str) -> None:
    """Append a voice execution event to data/audit_log.jsonl (main-thread scope)."""
    import json as _json
    from datetime import datetime, timezone

    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "audit_log.jsonl")
    try:
        with open(_path, "a") as f:
            f.write(_json.dumps({
                "type": "voice_execution",
                "action": action,
                "symbol": symbol,
                "voice_text": voice_text,
                "result": result,
                "ts": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
    except Exception:
        pass


def _execute_voice_sell(ib, dash: dict, symbol: str, cmd: dict) -> None:
    from orders_core import execute_sell
    from bot_voice import speak as _speak

    clog("TRADE", f"[VOICE] Executing sell for {symbol}")
    success = execute_sell(ib, symbol, reason="voice_command")
    if success:
        _speak(f"Closing {symbol}.")
        _log_voice_audit_bot("execute_sell", symbol, cmd.get("voice_text", ""), "success")
    else:
        _speak(f"Could not close {symbol}. It may not be in the portfolio.")
        _log_voice_audit_bot("execute_sell", symbol, cmd.get("voice_text", ""), "failed")


def _execute_voice_buy(ib, dash: dict, symbol: str, cmd: dict) -> None:
    import pandas as pd
    from bot_voice import speak as _speak

    clog("TRADE", f"[VOICE] Executing buy for {symbol}")

    # 1. Get live price (IBKR first, Alpaca fallback)
    price = 0.0
    try:
        from orders_contracts import get_contract, _get_ibkr_price
        contract = get_contract(symbol, "stock")
        ib.qualifyContracts(contract)
        price = _get_ibkr_price(ib, contract, fallback=0)
    except Exception as e:
        clog("WARN", f"[VOICE] IBKR price failed for {symbol}: {e}")

    if price <= 0:
        try:
            from alpaca_data import fetch_snapshots
            snap = fetch_snapshots([symbol])
            price = (snap.get(symbol) or {}).get("price", 0.0)
        except Exception as e:
            clog("WARN", f"[VOICE] Alpaca price failed for {symbol}: {e}")

    if price <= 0:
        _speak(f"Could not get a price for {symbol}. Trade aborted.")
        _log_voice_audit_bot("execute_buy", symbol, cmd.get("voice_text", ""), "no_price")
        return

    # 2. Compute ATR from 10 days of daily bars (fallback: 2% of price)
    atr = price * 0.02
    try:
        from alpaca_data import fetch_bars
        bars = fetch_bars(symbol, period="10d", interval="1d")
        if bars is not None and len(bars) >= 5:
            hi = bars["High"]
            lo = bars["Low"]
            cl = bars["Close"]
            tr = pd.concat([
                hi - lo,
                (hi - cl.shift()).abs(),
                (lo - cl.shift()).abs(),
            ], axis=1).max(axis=1)
            atr_val = float(tr.rolling(10).mean().dropna().iloc[-1])
            if atr_val > 0:
                atr = atr_val
    except Exception as e:
        clog("WARN", f"[VOICE] ATR calculation failed for {symbol}: {e}")

    # 3. Execute — all guards inside execute_buy run normally
    portfolio_value = dash.get("portfolio_value", 0)
    regime = dash.get("regime", {})

    from orders_core import execute_buy
    success = execute_buy(
        ib=ib,
        symbol=symbol,
        price=price,
        atr=atr,
        score=30,  # minimum viable — no Kelly boost, all risk guards still apply
        portfolio_value=portfolio_value,
        regime=regime,
        reasoning="voice_command",
    )
    if success:
        _speak(f"Buy order placed for {symbol}.")
        _log_voice_audit_bot("execute_buy", symbol, cmd.get("voice_text", ""), "success")
    else:
        _speak(f"Could not buy {symbol}. Check the logs for details.")
        _log_voice_audit_bot("execute_buy", symbol, cmd.get("voice_text", ""), "failed_guards")


def _process_voice_commands(ib, dash: dict) -> None:
    """Process confirmed voice trade commands. Called on the main thread every tick.

    Skipped while a scan is in progress — prevents stale-data races on dash and
    active_trades. Commands wait in bot_state._pending_voice_commands until the
    scan finishes (typically 15–45 s delay, acceptable for a paper account).
    """
    from datetime import datetime, timezone
    from bot_voice import speak as _speak

    if dash.get("scanning") or dash.get("killed"):
        return

    q = bot_state._pending_voice_commands
    if not q:
        return

    now = datetime.now(timezone.utc)

    # Expire stale commands (older than 120 s) — scan can take 45 s, 120 s gives
    # the user comfortable room to say "confirm" even if a scan starts immediately.
    expired = [c for c in list(q) if (now - c["created_at"]).total_seconds() > 120]
    for cmd in expired:
        try:
            q.remove(cmd)
        except ValueError:
            pass
        sym = cmd.get("symbol", "unknown")
        _speak(f"The pending {cmd['type'].lower().replace('_', ' ')} for {sym} expired. Please try again.")
        _log_voice_audit_bot("expired", sym, cmd.get("voice_text", ""), "expired")

    # Process confirmed commands from the front of the queue
    while q:
        cmd = q[0]
        if not cmd.get("confirmed"):
            break  # Front is unconfirmed — wait for user to say "confirm"
        q.popleft()
        sym = cmd.get("symbol", "").upper()
        if cmd["type"] == "TRADE_SELL":
            _execute_voice_sell(ib, dash, sym, cmd)
        elif cmd["type"] == "TRADE_BUY":
            _execute_voice_buy(ib, dash, sym, cmd)


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
        with open(SETTINGS_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        clog("ERROR", f"Failed to save settings: {e}")


# ── Hot reload (extracted to bot_hot_reload.py) ───────────────────────────────
# Re-exported here so that callers using `bot.check_and_reload()`,
# `bot._file_hash()`, etc. continue to work unchanged.
# Tests access `bot._file_hashes` as a dict mutation — shared by reference.
# LOAD-BEARING: do NOT strip as "unused" — bot_trading.py:1039 does
# sys.modules.get("bot").check_and_reload(); tests do bot.check_and_reload().
# Ruff respects the noqa comments below.
from bot_hot_reload import (  # noqa: F401
    WATCHED_MODULES,
    _file_hash,
    _file_hashes,
    _init_hashes,
    check_and_reload,
)

# ── Color map — used by dashboard and tests ───────────────────────────────────
COLORS: dict = {
    "TRADE": "cyan",
    "SIGNAL": "green",
    "ANALYSIS": "blue",
    "ERROR": "red",
    "INFO": "white",
    "RISK": "yellow",
    "SCAN": "magenta",
}


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
# Register as "bot" so sub-modules can resolve via sys.modules.get("bot")
# (when run as __main__ the module lives under "__main__", not "bot")
if "bot" not in sys.modules:
    sys.modules["bot"] = sys.modules[__name__]


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    # Lazy imports — keep module-level import chain minimal for tests
    from bot_account import backfill_equity_history_if_needed, get_account_data, load_equity_history
    from bot_dashboard import start_dashboard
    from bot_ibkr import (
        _on_order_status_event,
        backfill_trades_from_ibkr,
        cancel_orphan_stop_orders,
        subscribe_pnl,
        sync_orders_from_ibkr,
    )
    from bot_sentinel import (
        countdown_tick,
        start_alpaca_news_stream,
        start_catalyst_engine,
        start_catalyst_sentinel,
        start_news_sentinel,
    )
    from bot_trading import _check_kill, _process_close_queue, run_scan
    from learning import (
        get_performance_summary,
        load_orders,
        load_trades,
    )
    from risk import (
        get_scan_interval,
        init_equity_high_water_mark_from_history,
        reset_daily_state,
    )
    from theme_tracker import get_all_themes, load_custom_themes

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
  {Fore.WHITE}Account : {Fore.YELLOW}{CONFIG["active_account"]}{Style.RESET_ALL}
  {Fore.WHITE}Agents  : {Fore.YELLOW}4 Claude agents | {CONFIG["agents_required_to_agree"]}/4 required to trade{Style.RESET_ALL}
  {Fore.WHITE}Risk    : {Fore.YELLOW}{int(CONFIG["risk_pct_per_trade"] * 100)}% per trade | {int(CONFIG["daily_loss_limit"] * 100)}% daily limit{Style.RESET_ALL}
  {Fore.WHITE}Dashboard: {Fore.CYAN}http://localhost:{CONFIG["dashboard_port"]}{Style.RESET_ALL}
""")

    # API key check
    if CONFIG["anthropic_api_key"] == "YOUR_API_KEY_HERE":
        print(f"{Fore.RED}ERROR: Set ANTHROPIC_API_KEY environment variable.{Style.RESET_ALL}")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
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
            clog(
                "INFO",
                f"Data collection complete: {result['total_rows']:,} rows, "
                f"{result['daily_symbols']} daily + {result['intraday_symbols']} intraday symbols",
            )
        except ImportError:
            clog("INFO", "data_collector.py not found — skipping historical data collection")
        except Exception as e:
            clog("ERROR", f"Background data collection error: {e}")

    threading.Thread(target=_background_data_collection, daemon=True, name="DataCollector").start()

    # Connect to IBKR — retry loop so dashboard stays live when TWS is offline
    if not connect_ibkr():
        port = CONFIG["ibkr_port"]
        clog(
            "WARN",
            f"TWS not reachable on port {port} — dashboard is live at http://localhost:{CONFIG['dashboard_port']}",
        )
        clog("WARN", "Start TWS and click Reconnect in the dashboard, or wait for auto-retry (30s).")
        dash["ibkr_disconnected"] = True
        dash["status"] = "disconnected"
        while not bot_state.ib.isConnected():
            # Wait up to 30 s — woken early if user clicks Reconnect
            bot_state._manual_reconnect_evt.wait(timeout=30)
            bot_state._manual_reconnect_evt.clear()
            if connect_ibkr():
                dash["ibkr_disconnected"] = False
                clog("INFO", "TWS connected — resuming startup")
                break
            clog("WARN", f"Still waiting for TWS on port {port}...")
            dash["status"] = "disconnected"

    # ── IBKR streaming data manager ───────────────────────────────────────────
    # Provides real-time quotes + 5s→1m→5m bar aggregation via the live IB connection.
    # signals.py reads from this before falling back to Alpaca / yfinance.
    try:
        from ibkr_streaming import IBKRDataManager

        bot_state.ibkr_data_manager = IBKRDataManager(bot_state.ib)
        clog("INFO", "IBKR streaming data manager ready")
    except Exception as _e:
        clog("WARN", f"IBKR streaming data manager unavailable: {_e}")
        bot_state.ibkr_data_manager = None

    # Reset daily risk state — only once per calendar day
    pv, _ = get_account_data()
    today = datetime.now(_ET).date()
    if not hasattr(main, "_last_reset_date") or main._last_reset_date != today:
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
    cancel_orphan_stop_orders()  # Cancel stale exit orders with no corresponding active position

    # Hot reload hashes intentionally not initialised — check_and_reload() is not called in the main loop

    # Load persistent data
    load_settings_overrides()  # Apply saved dashboard settings on top of config.py defaults
    dash["favourites"] = load_favourites()
    dash["equity_history"] = load_equity_history()
    backfill_equity_history_if_needed()  # extend history from IBKR Flex or trade reconstruction
    if dash["equity_history"]:
        init_equity_high_water_mark_from_history(dash["equity_history"])
    dash["all_trades"] = load_trades()
    dash["all_orders"] = load_orders()
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
            dash["sentinel_stats"] = bot_state._sentinel.stats
            dash["sentinel_status"] = bot_state._sentinel.stats.get("status", "unknown")
        # Reschedule with fresh interval
        interval = get_scan_interval()
        dash["next_scan_seconds"] = interval
        schedule.clear("scan")
        schedule.every(interval).seconds.do(scheduled_scan).tag("scan")

    # Store reference so the main loop can call scheduled_scan() on momentum interrupt
    bot_state._scheduled_scan_fn = scheduled_scan

    interval = get_scan_interval()
    dash["next_scan_seconds"] = interval
    schedule.every(interval).seconds.do(scheduled_scan).tag("scan")

    # ── Start Alpaca News Stream (primary real-time push feed) ───────────────
    # Push-based Benzinga feed — no polling, symbols pre-tagged.
    # Replaces Yahoo RSS + Finviz scraping. Runs independently of sentinel_enabled.
    if CONFIG.get("alpaca_news_enabled", True):
        try:
            bot_state._alpaca_news_stream = start_alpaca_news_stream()
            clog("INFO", "📰 Alpaca news stream active (Benzinga real-time push feed)")
        except Exception as _ane_err:
            clog("INFO", f"📰 Alpaca news stream skipped: {_ane_err}")

    # ── Start News Sentinel (IBKR news poller — secondary source) ────────────
    if CONFIG.get("sentinel_enabled", True):
        bot_state._sentinel = start_news_sentinel(bot_state.ib)
        dash["sentinel_status"] = "running"
        dash["sentinel_stats"] = bot_state._sentinel.stats
        clog("INFO", f"📡 News Sentinel active (IBKR) | polling every {CONFIG.get('sentinel_poll_seconds', 45)}s")
    else:
        clog("INFO", "📡 News Sentinel disabled (sentinel_enabled=False in config)")

    # ── CatalystSentinel retired (Session 2) ─────────────────────────────────
    # CatalystEngine now owns all real-time M&A monitoring (news + EDGAR) and
    # enriches every trigger with screener_context + size_multiplier.
    # catalyst_sentinel.py is kept but no longer started.
    # if CONFIG.get("catalyst_sentinel_enabled", True):
    #     bot_state._catalyst_sentinel = start_catalyst_sentinel(bot_state.ib)
    clog("INFO", "⚡ CatalystSentinel retired — CatalystEngine owns real-time monitoring")

    # ── Start Catalyst Engine (M&A intelligence layer) ───────────────────────
    # Session 1: WatchlistStore + 4 scoring runners (fundamental/EDGAR/options/sentiment).
    # Session 2: real-time news/EDGAR monitors added, CatalystSentinel retired.
    try:
        bot_state._catalyst_engine = start_catalyst_engine()
        dash["catalyst_engine_stats"] = bot_state._catalyst_engine.get_stats()
        clog("INFO", f"⚡ Catalyst Engine active | {bot_state._catalyst_engine.store.count()} candidates pre-loaded")
    except Exception as _ce_err:
        clog("WARN", f"⚡ Catalyst Engine failed to start: {_ce_err}")

    # ── Pre-session catalyst pipeline (08:00 ET daily) ────────────────────────
    # Phase 3a: Pulls top catalyst candidates, runs 3-agent sentinel, logs
    # decisions to data/presession_log.jsonl. Dry-run only — no orders.
    # Phase 3b (deferred) will add MOO execution once dry-run data is clean.
    # Requires the catalyst engine above; wrap in try so a registration failure
    # doesn't block the rest of startup.
    if CONFIG.get("presession_enabled", True):
        try:
            from presession import presession_catalyst_pipeline

            fire_time = CONFIG.get("presession_fire_time_et", "08:00")
            schedule.every().day.at(fire_time).do(presession_catalyst_pipeline).tag("presession")
            clog(
                "INFO",
                f"⏰ Pre-session catalyst pipeline scheduled for {fire_time} ET "
                f"(dry_run={CONFIG.get('presession_dry_run', True)})",
            )
        except Exception as _ps_err:
            clog("WARN", f"⏰ Pre-session pipeline registration failed: {_ps_err}")
    else:
        clog("INFO", "⏰ Pre-session catalyst pipeline disabled (presession_enabled=False)")

    # ── Tier B promoter schedules ─────────────────────────────────────────────
    # 16:15 ET (post-close): promote top-50 from committed universe for next day.
    # 08:00 ET (pre-open): re-promote so overnight gappers surface in the PM scan.
    # Sunday 23:00 ET: weekly refresh of the ~1000-symbol committed universe.
    if CONFIG.get("promoter_enabled", True):
        try:
            from universe_committed import refresh_committed_universe
            from universe_promoter import run_promoter

            schedule.every().day.at("16:15").do(run_promoter).tag("promoter_eod")
            schedule.every().day.at("08:00").do(run_promoter).tag("promoter_premarket")
            schedule.every().sunday.at("23:00").do(refresh_committed_universe).tag("universe_refresh")
            clog(
                "INFO",
                "⏰ Universe promoter scheduled — 16:15 ET (EOD), 08:00 ET (pre-open), "
                "Sundays 23:00 ET (committed refresh)",
            )
        except Exception as _pr_err:
            clog("WARN", f"⏰ Universe promoter registration failed: {_pr_err}")
    else:
        clog("INFO", "⏰ Universe promoter disabled (promoter_enabled=False)")

    # ── Start Social Sentiment background polling ─────────────────────────────
    try:
        from social_sentiment import start_sentiment_polling

        start_sentiment_polling()
        clog("INFO", "Social sentiment polling active (Reddit + ApeWisdom, 60s interval)")
    except ImportError:
        clog("INFO", "Social sentiment module not installed — skipping background polling")
    except Exception as e:
        clog("ERROR", f"Social sentiment startup error: {e}")

    # ── Start Alpaca bar stream (pre-warms cache before first scan) ───────────
    # Stream subscribes to 1-minute bars for the initial universe.
    # fetch_multi_timeframe() reads from BAR_CACHE on every scan — no further
    # wiring needed. Universe subscriptions refresh each scan in run_scan().
    try:
        from alpaca_stream import AlpacaBarStream
        from scanner import get_dynamic_universe

        _initial_universe = get_dynamic_universe(bot_state.ib, {})
        bot_state._bar_stream = AlpacaBarStream()
        bot_state._bar_stream.start(_initial_universe)
        clog("INFO", f"📶 Alpaca bar stream active | {len(_initial_universe)} symbols subscribed")
    except Exception as _as_err:
        clog("INFO", f"📶 Alpaca bar stream skipped: {_as_err}")

    # ── Start live price updater (QUOTE_CACHE → active_trades, 2s) ────────────
    # Propagates real-time bid/ask mid-prices into position "current" field so
    # /api/prices and the next dashboard poll both reflect live market prices.
    try:
        from price_updater import PriceUpdater

        bot_state._price_updater = PriceUpdater()
        bot_state._price_updater.start()
        clog("INFO", "💹 Live price updater active (2s, QUOTE_CACHE → positions)")
    except Exception as _pu_err:
        clog("INFO", f"💹 Live price updater skipped: {_pu_err}")

    # ── Start Momentum Sentinel (SPY fast-move scan bypass) ───────────────────
    # Monitors live SPY 1m bars; fires an immediate scan when SPY moves fast.
    # Requires BAR_CACHE to be warm (bar stream started above).
    if CONFIG.get("momentum_sentinel_enabled", True):
        try:
            from momentum_sentinel import start_momentum_sentinel

            bot_state._momentum_sentinel = start_momentum_sentinel()
            dash["momentum_sentinel_stats"] = bot_state._momentum_sentinel.stats
            clog(
                "INFO",
                f"⚡ Momentum Sentinel active | "
                f"fast {CONFIG.get('momentum_sentinel_fast_pct', 0.3)}% / "
                f"slow {CONFIG.get('momentum_sentinel_slow_pct', 0.6)}% | "
                f"cooldown {CONFIG.get('momentum_sentinel_cooldown_m', 15)}m",
            )
        except Exception as _ms_err:
            clog("INFO", f"⚡ Momentum Sentinel skipped: {_ms_err}")

    # ── Start Telegram Kill Switch ────────────────────────────────────────────
    _tg_cfg = CONFIG.get("telegram", {})
    _tg_token = _tg_cfg.get("bot_token", "")
    _tg_chat_ids = _tg_cfg.get("authorized_chat_ids", [])
    if _tg_token and _tg_chat_ids:
        try:
            import telegram_bot as _tg_mod

            def _tg_on_kill() -> str:
                dash["killed"] = True
                clog("RISK", "🚨 Telegram KILL — executing FLATTEN ALL...")
                try:
                    from orders_portfolio import flatten_all

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
        from ml_engine import enhance_score as _enhance_score  # noqa: F401 — import tests availability

        if CONFIG.get("ml_enabled", False):
            clog("INFO", "ML signal enhancement active (will enhance scores when models trained)")
        else:
            clog("INFO", "ML engine available but disabled (ml_enabled=False)")
    except ImportError:
        clog("INFO", "ML engine not installed — skipping")
    except Exception as e:
        clog("ERROR", f"ML engine startup error: {e}")

    # ── iCloud backup sync (every 5 min, runs in this process so FDA inherited) ─
    _ICLOUD_SYNC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "icloud-sync.sh")

    def _run_icloud_sync():
        if os.path.exists(_ICLOUD_SYNC_SCRIPT):
            try:
                import subprocess

                subprocess.Popen(
                    ["bash", _ICLOUD_SYNC_SCRIPT],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as _sync_err:
                clog("WARN", f"iCloud sync failed: {_sync_err}")

    schedule.every(5).minutes.do(_run_icloud_sync)
    _run_icloud_sync()  # run immediately on startup

    # ── Startup health check — warn if any enabled real-time subsystem failed ──
    _failed = []
    if CONFIG.get("alpaca_news_enabled", True) and getattr(bot_state, "_alpaca_news_stream", None) is None:
        _failed.append("Alpaca news stream")
    if getattr(bot_state, "_bar_stream", None) is None:
        _failed.append("Alpaca bar stream (real-time price data)")
    if CONFIG.get("sentinel_enabled", True) and getattr(bot_state, "_sentinel", None) is None:
        _failed.append("News sentinel (IBKR)")
    if _failed:
        clog("WARN", "⚠️  STARTUP WARNING — the following subsystems failed to start:")
        for _f in _failed:
            clog("WARN", f"   ✗ {_f}")
        clog("WARN", "⚠️  Bot is running with degraded signal coverage.")

    clog("INFO", f"<> Decifer running. Dashboard → http://localhost:{CONFIG['dashboard_port']}")
    clog("INFO", "Press Ctrl+C to stop.")

    try:
        while True:
            # ── Kill switch check (runs on main thread for ib_insync safety) ──
            _check_kill()

            # ── Process individual position close requests ──
            _process_close_queue()

            # ── Process confirmed voice trade commands (main-thread IBKR safety) ──
            _process_voice_commands(bot_state.ib, dash)

            # ── Sync sentinel state to dashboard ──
            if bot_state._sentinel:
                dash["sentinel_stats"] = bot_state._sentinel.stats
                dash["sentinel_status"] = bot_state._sentinel.stats.get("status", "unknown")

            # ── Sync catalyst sentinel state to dashboard ──
            if bot_state._catalyst_sentinel:
                dash["catalyst_sentinel_stats"] = bot_state._catalyst_sentinel.stats

            # ── Sync catalyst engine stats to dashboard ──
            if bot_state._catalyst_engine:
                dash["catalyst_engine_stats"] = bot_state._catalyst_engine.get_stats()

            # ── Sync momentum sentinel state to dashboard ──
            if bot_state._momentum_sentinel:
                dash["momentum_sentinel_stats"] = bot_state._momentum_sentinel.stats

            # ── Momentum interrupt: fire immediate scan if sentinel triggered ──
            # The sentinel sets this event when SPY moves fast (background thread).
            # We clear it and call scheduled_scan() on the main thread — safe for IBKR.
            if (
                bot_state._momentum_scan_requested.is_set()
                and bot_state._scheduled_scan_fn is not None
                and not dash.get("paused")
                and not dash.get("killed")
            ):
                bot_state._momentum_scan_requested.clear()
                clog("SIGNAL", "⚡ MOMENTUM INTERRUPT — bypassing scheduler, scanning now")
                schedule.clear("scan")
                bot_state._scheduled_scan_fn()

            schedule.run_pending()
            bot_state.ib.sleep(1)
    except KeyboardInterrupt:
        dash["status"] = "stopped"
        if bot_state._bar_stream:
            bot_state._bar_stream.stop()
        if bot_state._price_updater:
            bot_state._price_updater.stop()
        if bot_state._alpaca_news_stream:
            bot_state._alpaca_news_stream.stop()
        if bot_state._sentinel:
            bot_state._sentinel.stop()
        if bot_state._catalyst_sentinel:
            bot_state._catalyst_sentinel.stop()
        if bot_state._catalyst_engine:
            bot_state._catalyst_engine.stop()
        try:
            from social_sentiment import stop_sentiment_polling

            stop_sentiment_polling()
        except Exception:
            pass
        clog("INFO", "<> Decifer stopped.")
        bot_state.ib.disconnect()


if __name__ == "__main__":
    main()
