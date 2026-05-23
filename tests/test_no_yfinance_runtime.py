"""
Guard test: production runtime modules must not import yfinance.
If yfinance is used in a production module, this test fails — by design.
"""
import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).parent.parent

_PRODUCTION_MODULES = [
    "signals/__init__.py",
    "portfolio_optimizer.py",
    "orders_core.py",
    "bot.py",
    "bot_trading.py",
    "scanner.py",
    "alpaca_data.py",
    "market_intelligence.py",
]


def _module_src(rel_path: str) -> str:
    return (_REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_signals_init_no_yfinance():
    src = _module_src("signals/__init__.py")
    assert "import yfinance" not in src, "signals/__init__.py must not import yfinance"
    assert "yf.Ticker" not in src, "signals/__init__.py must not use yf.Ticker"
    assert "yf.download" not in src, "signals/__init__.py must not use yf.download"


def test_portfolio_optimizer_no_yfinance():
    src = _module_src("portfolio_optimizer.py")
    assert "import yfinance" not in src, "portfolio_optimizer.py must not import yfinance"
    assert "yf.download" not in src, "portfolio_optimizer.py must not use yf.download"


def test_bot_no_yfinance():
    src = _module_src("bot.py")
    assert "yfinance" not in src, "bot.py must not reference yfinance"


def test_orders_core_no_yfinance():
    src = _module_src("orders_core.py")
    assert "yfinance" not in src, "orders_core.py must not reference yfinance"
