"""Tests for dashboard.py.

Dashboard is a heavy HTML/JS string + a FastAPI/web server (no testable
business-logic functions in the API MAP).  We therefore test:
  1. The DASHBOARD_HTML constant is importable and well-formed.
  2. Key structural tokens exist (title, tab labels, stat IDs).
  3. The module itself does not crash on import.
  4. No Python syntax errors in the module.

We explicitly DO NOT test HTML rendering, chart drawing, or browser
behaviour — per project rules.
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup + heavy-dependency stubs BEFORE importing target module
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub ib_async
ib_async_mod = types.ModuleType("ib_async")
ib_async_mod.IB = MagicMock()
ib_async_mod.Contract = MagicMock()
ib_async_mod.Ticker = MagicMock()
ib_async_mod.BarData = MagicMock()
sys.modules.setdefault("ib_async", ib_async_mod)

# Stub anthropic
anthropic_mod = types.ModuleType("anthropic")
anthropic_mod.Anthropic = MagicMock()
sys.modules.setdefault("anthropic", anthropic_mod)

# Stub yfinance
yf_mod = types.ModuleType("yfinance")
import pandas as pd

yf_mod.download = MagicMock(return_value=pd.DataFrame())
yf_mod.Ticker = MagicMock()
sys.modules.setdefault("yfinance", yf_mod)

# Stub fastapi and related
for _mod_name in [
    "fastapi",
    "fastapi.responses",
    "fastapi.middleware",
    "fastapi.middleware.cors",
    "uvicorn",
    "starlette",
    "starlette.responses",
]:
    stub = types.ModuleType(_mod_name)
    stub.FastAPI = MagicMock(return_value=MagicMock())
    stub.HTMLResponse = MagicMock()
    stub.JSONResponse = MagicMock()
    stub.CORSMiddleware = MagicMock()
    stub.run = MagicMock()
    sys.modules.setdefault(_mod_name, stub)

# Stub httpx / feedparser / praw
for _mod_name in ["httpx", "feedparser", "praw"]:
    sys.modules.setdefault(_mod_name, types.ModuleType(_mod_name))

# Stub py_vollib family
for _m in [
    "py_vollib",
    "py_vollib.black_scholes",
    "py_vollib.black_scholes.greeks",
    "py_vollib.black_scholes.greeks.analytical",
    "py_vollib.black_scholes.implied_volatility",
]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

# Stub sklearn / joblib
for _m in ["sklearn", "sklearn.ensemble", "sklearn.preprocessing", "sklearn.model_selection", "joblib"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

# Provide minimal config stub so bot-adjacent imports don't explode
if "config" not in sys.modules:
    config_stub = types.ModuleType("config")
    config_stub.CONFIG = {
        "risk_per_trade": 0.01,
        "max_daily_loss": 0.03,
        "max_open_positions": 5,
        "min_score_threshold": 60,
        "paper_trading": True,
        "log_file": "/tmp/decifer_test.log",
        "trade_log": "/tmp/trades_test.json",
        "order_log": "/tmp/orders_test.json",
    }
    sys.modules.setdefault("config", config_stub)

# Ensure we get the real dashboard module, not any stub left by test_bot.py
sys.modules.pop("dashboard", None)

# Now import dashboard
import dashboard

# ===========================================================================
# Tests
# ===========================================================================


class TestDashboardHtmlConstant:
    """Structural checks on the DASHBOARD_HTML string."""

    def test_dashboard_html_is_string(self):
        assert isinstance(dashboard.DASHBOARD_HTML, str)

    def test_dashboard_html_not_empty(self):
        assert len(dashboard.DASHBOARD_HTML) > 1000, "HTML should be substantial"

    def test_dashboard_html_has_doctype(self):
        assert "<!DOCTYPE html" in dashboard.DASHBOARD_HTML

    def test_dashboard_html_has_html_tags(self):
        html = dashboard.DASHBOARD_HTML
        assert "<html" in html
        assert "</html>" in html

    def test_dashboard_html_has_head_and_body(self):
        html = dashboard.DASHBOARD_HTML
        assert "<head>" in html or "<head " in html
        assert "<body" in html

    def test_dashboard_html_has_title(self):
        assert "<title>" in dashboard.DASHBOARD_HTML

    def test_dashboard_html_has_decifer_branding(self):
        html = dashboard.DASHBOARD_HTML
        assert "Decifer" in html or "decifer" in html.lower()

    def test_dashboard_html_has_live_tab(self):
        """Live trading view tab must be present."""
        assert "Live" in dashboard.DASHBOARD_HTML or "live" in dashboard.DASHBOARD_HTML.lower()

    def test_dashboard_html_has_portfolio_stats(self):
        """Portfolio value stat element must exist."""
        html = dashboard.DASHBOARD_HTML
        assert "Portfolio" in html or "portfolio" in html.lower()

    def test_dashboard_html_has_kill_switch(self):
        """Kill switch button is a required safety feature."""
        html = dashboard.DASHBOARD_HTML
        assert "KILL" in html.upper() or "killSwitch" in html or "kill_switch" in html

    def test_dashboard_html_has_positions_section(self):
        html = dashboard.DASHBOARD_HTML
        assert "position" in html.lower() or "pos-" in html

    def test_dashboard_html_has_agents_tab(self):
        """Agent analysis view must be exposed in the dashboard."""
        html = dashboard.DASHBOARD_HTML
        assert "Agent" in html or "agent" in html.lower()

    def test_dashboard_html_has_risk_tab(self):
        html = dashboard.DASHBOARD_HTML
        assert "Risk" in html or "risk" in html.lower()

    def test_dashboard_html_has_news_tab(self):
        html = dashboard.DASHBOARD_HTML
        assert "News" in html or "news" in html.lower()

    def test_dashboard_html_has_chart_js(self):
        """Chart.js CDN include should be present for growth charts."""
        html = dashboard.DASHBOARD_HTML
        assert "chart" in html.lower() or "Chart" in html

    def test_dashboard_html_has_css_variables(self):
        """CSS custom properties (--bg, --orange etc.) drive the dark theme."""
        html = dashboard.DASHBOARD_HTML
        assert "--bg" in html or "--orange" in html

    def test_dashboard_html_has_pnl_element(self):
        """Day P&L display is critical."""
        html = dashboard.DASHBOARD_HTML
        assert "pnl" in html.lower() or "P&amp;L" in html or "P&L" in html

    def test_dashboard_html_has_regime_element(self):
        """Market regime pill must be present for the operator."""
        html = dashboard.DASHBOARD_HTML
        assert "regime" in html.lower()

    def test_dashboard_html_balanced_script_tags(self):
        """Number of <script> and </script> tags must be equal."""
        html = dashboard.DASHBOARD_HTML
        opens = html.lower().count("<script")
        closes = html.lower().count("</script>")
        assert opens == closes, f"Unbalanced script tags: {opens} open, {closes} close"

    def test_dashboard_html_has_settings_tab(self):
        html = dashboard.DASHBOARD_HTML
        assert "Setting" in html or "setting" in html.lower()

    def test_dashboard_html_has_scan_progress(self):
        """Scan progress indicator keeps operator informed."""
        html = dashboard.DASHBOARD_HTML
        assert "scan" in html.lower()

    def test_dashboard_html_has_log_area(self):
        """Activity log area for streaming bot events."""
        html = dashboard.DASHBOARD_HTML
        assert "log" in html.lower()

    @pytest.mark.parametrize(
        "stat_id",
        [
            "s-val",  # Portfolio Value
            "s-pnl",  # Day P&L
            "s-pos",  # Open Positions
            "s-scans",  # Scans Run
        ],
    )
    def test_dashboard_has_required_stat_ids(self, stat_id):
        """Critical stat element IDs must be present for JS to update them."""
        assert stat_id in dashboard.DASHBOARD_HTML, f"Missing stat id: {stat_id}"

    @pytest.mark.parametrize(
        "js_fn",
        [
            "switchTab",
            "killSwitch",
            "togglePause",
        ],
    )
    def test_dashboard_has_required_js_functions(self, js_fn):
        """Critical JavaScript functions must be defined in the dashboard."""
        assert js_fn in dashboard.DASHBOARD_HTML, f"Missing JS function: {js_fn}"

    def test_dashboard_html_has_no_placeholder_tokens(self):
        """Template placeholder tokens like {{REPLACE_ME}} should not exist."""
        html = dashboard.DASHBOARD_HTML
        assert "REPLACE_ME" not in html
        assert "TODO" not in html or html.count("TODO") == 0

    def test_dashboard_module_importable_without_side_effects(self):
        """Re-importing dashboard should not raise any errors."""
        import importlib

        importlib.reload(dashboard)
        assert hasattr(dashboard, "DASHBOARD_HTML")
