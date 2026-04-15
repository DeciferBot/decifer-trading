# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  bot_hot_reload.py                          ║
# ║   Hot-reload: watches .py files, reloads on change           ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
File-change watcher and module reloader.

Extracted from bot.py so the orchestrator is not cluttered with
infrastructure concerns. Bot keeps running; positions, state, and
IBKR connection are all preserved across reloads.

bot.py re-exports every public name here so callers that do
`import bot; bot.check_and_reload()` continue to work unchanged.
Tests that access `bot._file_hash`, `bot._file_hashes`, etc. still
work because re-exported names share the same objects.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
from datetime import datetime

from bot_state import clog, dash

WATCHED_MODULES: dict = {
    "signals": "signals",
    "scanner": "scanner",
    "agents": "agents",
    "risk": "risk",
    "orders": "orders",
    "learning": "learning",
    "dashboard": "dashboard",
    "news": "news",
    "news_sentinel": "news_sentinel",
    "theme_tracker": "theme_tracker",
    "sentinel_agents": "sentinel_agents",
    "bot_dashboard": "bot_dashboard",
}

_file_hashes: dict = {}


def _file_hash(path: str) -> str:
    """Return MD5 hash of file contents."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""


def _init_hashes() -> None:
    """Record initial file hashes on startup."""
    base = os.path.dirname(os.path.abspath(__file__))
    for name in [*list(WATCHED_MODULES.keys()), "bot", "config"]:
        path = os.path.join(base, f"{name}.py")
        _file_hashes[name] = _file_hash(path)


def check_and_reload() -> list:
    """
    Check all watched files for changes.
    If changed: reload the module, update the dashboard HTML, log the reload.
    Called at the start of every scan — zero overhead when nothing changed.
    """
    base = os.path.dirname(os.path.abspath(__file__))
    changed: list = []
    _bot = sys.modules.get("bot")

    for mod_name, import_name in WATCHED_MODULES.items():
        path = os.path.join(base, f"{mod_name}.py")
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
    dash_path = os.path.join(base, "dashboard.py")
    dash_current = _file_hash(dash_path)
    if dash_current and dash_current != _file_hashes.get("dashboard", ""):
        try:
            import dashboard as _dash

            importlib.reload(_dash)
            if _bot:
                _bot.DASHBOARD_HTML = _dash.DASHBOARD_HTML
            _file_hashes["dashboard"] = dash_current
            changed.append("dashboard")
            clog("INFO", "🔄 Hot reload: dashboard.py updated — refresh browser to see changes")
        except Exception as e:
            clog("ERROR", f"Hot reload failed for dashboard: {e}")

    # Special case: config.py — reload config and apply new settings
    config_path = os.path.join(base, "config.py")
    config_current = _file_hash(config_path)
    if config_current and config_current != _file_hashes.get("config", ""):
        try:
            import config as _config

            importlib.reload(_config)
            if _bot:
                _bot.CONFIG.update(_config.CONFIG)
                # Re-apply dashboard overrides so they aren't wiped by config.py defaults
                _bot.load_settings_overrides()
            _file_hashes["config"] = config_current
            changed.append("config")
            clog(
                "INFO",
                "🔄 Hot reload: config.py updated — new settings active immediately (dashboard overrides preserved)",
            )
        except Exception as e:
            clog("ERROR", f"Hot reload failed for config: {e}")

    if changed:
        dash["hot_reload_count"] = dash.get("hot_reload_count", 0) + 1
        dash["last_reload"] = datetime.now().strftime("%H:%M:%S")
        dash["last_reload_files"] = changed

    return changed
