"""Shared fixtures and import-time mocks for the Decifer test suite.

All heavy external dependencies (ib_async, anthropic, yfinance, etc.) are
replaced with lightweight fakes before any Decifer module is imported, so
the suite runs fully offline and never touches a live system.
"""

import os
import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path (flat project — no package structure)
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Helper: build a minimal fake module and register it in sys.modules
# ---------------------------------------------------------------------------
def _fake_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------------------
# ib_async / ib_insync stubs
# ---------------------------------------------------------------------------
class _FakeIB:
    """Minimal synchronous stand-in for ib_insync.IB."""

    def __init__(self):
        self.connected = False
        self.orders_placed = []

    def connect(self, *a, **kw):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def qualifyContracts(self, *contracts):
        return list(contracts)

    def placeOrder(self, contract, order):
        self.orders_placed.append((contract, order))
        trade = MagicMock()
        trade.order = order
        trade.orderStatus.status = "Submitted"
        return trade

    def reqMktData(self, *a, **kw):
        ticker = MagicMock()
        ticker.last = 150.0
        ticker.bid = 149.9
        ticker.ask = 150.1
        return ticker

    def reqPnL(self, *a, **kw):
        pass

    def sleep(self, secs=0):
        pass

    def run(self):
        pass


class _FakeContract:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeMarketOrder:
    def __init__(self, action, quantity, **kwargs):
        self.action = action
        self.totalQuantity = quantity
        self.orderType = "MKT"
        for k, v in kwargs.items():
            setattr(self, k, v)


_ib_insync = _fake_module(
    "ib_insync",
    IB=_FakeIB,
    Contract=_FakeContract,
    Stock=lambda symbol, exchange="SMART", currency="USD": _FakeContract(
        symbol=symbol, exchange=exchange, currency=currency
    ),
    Option=lambda *a, **kw: _FakeContract(**kw),
    Forex=lambda *a, **kw: _FakeContract(**kw),
    Future=lambda *a, **kw: _FakeContract(**kw),
    MarketOrder=_FakeMarketOrder,
    LimitOrder=MagicMock(),
    StopOrder=MagicMock(),
    StopLimitOrder=MagicMock(),
    Ticker=MagicMock(),
    BarData=MagicMock(),
    util=MagicMock(),
    Trade=MagicMock(),
    Order=MagicMock(),
    OrderStatus=MagicMock(),
)
sys.modules["ib_async"] = _ib_insync

# ---------------------------------------------------------------------------
# anthropic stub
# ---------------------------------------------------------------------------
_anthropic = _fake_module("anthropic", Anthropic=MagicMock, AsyncAnthropic=MagicMock)

# ---------------------------------------------------------------------------
# yfinance stub
# ---------------------------------------------------------------------------


def _make_ohlcv(rows=60):
    """Return a realistic-looking OHLCV DataFrame."""
    np.random.seed(42)
    _end = pd.Timestamp.today().normalize()
    if _end.dayofweek >= 5:
        _end -= pd.offsets.BDay(1)
    idx = pd.date_range(end=_end, periods=rows, freq="B")
    close = 100.0 + np.cumsum(np.random.randn(rows) * 0.5)
    high = close + np.abs(np.random.randn(rows) * 0.3)
    low = close - np.abs(np.random.randn(rows) * 0.3)
    open_ = close + np.random.randn(rows) * 0.2
    volume = np.random.randint(1_000_000, 10_000_000, rows).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol
        self._df = _make_ohlcv()

    def history(self, period="1mo", interval="1d", **kwargs):
        return self._df

    @property
    def info(self):
        return {
            "shortName": self.symbol,
            "sector": "Technology",
            "marketCap": 1_000_000_000,
            "trailingPE": 20.0,
            "dividendYield": 0.01,
            "regularMarketPrice": 150.0,
            "fiftyTwoWeekHigh": 180.0,
            "fiftyTwoWeekLow": 120.0,
        }

    @property
    def options(self):
        return ["2025-01-17", "2025-02-21", "2025-03-21"]

    def option_chain(self, date):
        chain = MagicMock()
        strikes = [140, 145, 150, 155, 160]
        chain.calls = pd.DataFrame(
            {
                "strike": strikes,
                "lastPrice": [10.0, 6.0, 3.0, 1.0, 0.5],
                "impliedVolatility": [0.22, 0.25, 0.28, 0.32, 0.36],
                "openInterest": [500, 300, 200, 150, 100],
                "volume": [200, 150, 100, 75, 50],
                "delta": [0.75, 0.60, 0.50, 0.40, 0.25],
                "gamma": [0.03, 0.05, 0.06, 0.05, 0.03],
                "theta": [-0.03, -0.05, -0.06, -0.05, -0.03],
                "vega": [0.08, 0.10, 0.12, 0.10, 0.08],
            }
        )
        chain.puts = chain.calls.copy()
        return chain


def _fake_yf_download(tickers, *args, **kwargs):
    if isinstance(tickers, str):
        tickers = [tickers]
    df = _make_ohlcv()
    if len(tickers) == 1:
        return df
    arrays = [["Open", "High", "Low", "Close", "Volume"], tickers]
    cols = pd.MultiIndex.from_product(arrays)
    data = {}
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        for sym in tickers:
            data[(col, sym)] = df[col].values
    return pd.DataFrame(data, index=df.index)


_yfinance = _fake_module(
    "yfinance",
    Ticker=_FakeTicker,
    download=_fake_yf_download,
)


# ---------------------------------------------------------------------------
# requests stub
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, data=None, text="", status_code=200):
        self._data = data or {}
        self.text = text
        self.status_code = status_code
        self.content = text.encode()

    def json(self):
        return self._data

    def raise_for_status(self):
        pass

    def close(self):
        pass


_requests = _fake_module(
    "requests",
    get=MagicMock(return_value=_FakeResponse()),
    post=MagicMock(return_value=_FakeResponse()),
    Session=MagicMock,
    exceptions=types.SimpleNamespace(
        RequestException=Exception,
        Timeout=Exception,
        ConnectionError=Exception,
        HTTPError=Exception,
    ),
)

# ---------------------------------------------------------------------------
# pandas_ta stub
# ---------------------------------------------------------------------------
_pandas_ta = _fake_module("pandas_ta")
_pandas_ta.rsi = lambda s, length=14: pd.Series(np.full(len(s), 50.0), index=s.index)
_pandas_ta.macd = lambda s, **kw: pd.DataFrame(
    {
        "MACD_12_26_9": np.full(len(s), 0.1),
        "MACDh_12_26_9": np.full(len(s), 0.05),
        "MACDs_12_26_9": np.full(len(s), 0.05),
    },
    index=s.index,
)
_pandas_ta.bbands = lambda s, **kw: pd.DataFrame(
    {
        "BBL_20_2.0": np.full(len(s), 95.0),
        "BBM_20_2.0": np.full(len(s), 100.0),
        "BBU_20_2.0": np.full(len(s), 105.0),
        "BBB_20_2.0": np.full(len(s), 10.0),
        "BBP_20_2.0": np.full(len(s), 0.5),
    },
    index=s.index,
)
_pandas_ta.atr = lambda high, low, close, length=14: pd.Series(np.full(len(close), 1.5), index=close.index)
_pandas_ta.stoch = lambda high, low, close, **kw: pd.DataFrame(
    {
        "STOCHk_14_3_3": np.full(len(close), 50.0),
        "STOCHd_14_3_3": np.full(len(close), 50.0),
    },
    index=close.index,
)
_pandas_ta.ema = lambda s, length=20: pd.Series(
    np.full(len(s), s.mean() if hasattr(s, "mean") else 100.0), index=s.index
)
_pandas_ta.sma = lambda s, length=20: pd.Series(
    np.full(len(s), s.mean() if hasattr(s, "mean") else 100.0), index=s.index
)
_pandas_ta.adx = lambda high, low, close, **kw: pd.DataFrame(
    {
        "ADX_14": np.full(len(close), 25.0),
        "DMP_14": np.full(len(close), 20.0),
        "DMN_14": np.full(len(close), 15.0),
    },
    index=close.index,
)

# ---------------------------------------------------------------------------
# sklearn stubs
# ---------------------------------------------------------------------------
_sklearn = _fake_module("sklearn")
_sklearn_ensemble = _fake_module("sklearn.ensemble")
_sklearn_ensemble.RandomForestClassifier = MagicMock
_sklearn_ensemble.GradientBoostingClassifier = MagicMock
_sklearn_ensemble.GradientBoostingRegressor = MagicMock
_sklearn_ensemble.RandomForestRegressor = MagicMock
_sklearn_preprocessing = _fake_module("sklearn.preprocessing")
_sklearn_preprocessing.StandardScaler = MagicMock
_sklearn_preprocessing.LabelEncoder = MagicMock
_sklearn_model_selection = _fake_module("sklearn.model_selection")
_sklearn_model_selection.train_test_split = MagicMock(return_value=([], [], [], []))
_sklearn_model_selection.cross_val_score = MagicMock(return_value=np.array([0.8, 0.82, 0.79]))
_sklearn_model_selection.TimeSeriesSplit = MagicMock
_sklearn_metrics = _fake_module("sklearn.metrics")
_sklearn_metrics.accuracy_score = MagicMock(return_value=0.8)
_sklearn_metrics.classification_report = MagicMock(return_value="")
_sklearn_metrics.mean_squared_error = MagicMock(return_value=0.01)
_sklearn_metrics.confusion_matrix = MagicMock(return_value=np.array([[1, 0], [0, 1]]))
_sklearn_metrics.roc_auc_score = MagicMock(return_value=0.85)
_sklearn_metrics.r2_score = MagicMock(return_value=0.75)

# ---------------------------------------------------------------------------
# joblib stub
# ---------------------------------------------------------------------------
_joblib = _fake_module("joblib", dump=MagicMock(), load=MagicMock(), Parallel=MagicMock(), delayed=MagicMock())

# ---------------------------------------------------------------------------
# scipy stubs
# ---------------------------------------------------------------------------
_scipy = _fake_module("scipy")
_scipy_stats = _fake_module("scipy.stats")
_scipy_stats.pearsonr = MagicMock(return_value=(0.5, 0.05))
_scipy_stats.spearmanr = MagicMock(return_value=(0.5, 0.05))
_scipy_stats.norm = MagicMock()
_scipy_optimize = _fake_module("scipy.optimize")
_scipy_optimize.minimize = MagicMock(return_value=MagicMock(x=np.array([0.5, 0.5]), success=True, fun=0.1))

# ---------------------------------------------------------------------------
# feedparser stub
# ---------------------------------------------------------------------------
_feedparser = _fake_module("feedparser")
_feedparser.parse = MagicMock(
    return_value=MagicMock(
        entries=[
            MagicMock(
                title="Test headline",
                summary="Test summary text",
                link="https://example.com/article",
                published="Mon, 01 Jan 2025 12:00:00 GMT",
            )
        ]
    )
)

# ---------------------------------------------------------------------------
# praw (Reddit) stub
# ---------------------------------------------------------------------------
_praw = _fake_module("praw")
_praw.Reddit = MagicMock()

# ---------------------------------------------------------------------------
# cvxpy stub
# ---------------------------------------------------------------------------
_cvxpy = _fake_module("cvxpy")
_cvxpy.Variable = MagicMock()
_cvxpy.Problem = MagicMock()
_cvxpy.Minimize = MagicMock()
_cvxpy.Maximize = MagicMock()
_cvxpy.sum = MagicMock()
_cvxpy.quad_form = MagicMock()
_cvxpy.OSQP = "OSQP"

# ---------------------------------------------------------------------------
# dash / plotly stubs (for dashboard tests)
# ---------------------------------------------------------------------------
_dash = _fake_module("dash")
_dash.Dash = MagicMock()
_dash.html = _fake_module("dash.html")
_dash.dcc = _fake_module("dash.dcc")
_dash.dependencies = _fake_module("dash.dependencies")
_dash.dependencies.Input = MagicMock()
_dash.dependencies.Output = MagicMock()
_dash.dependencies.State = MagicMock()
_plotly = _fake_module("plotly")
_plotly_graph_objects = _fake_module("plotly.graph_objects")
_plotly_graph_objects.Figure = MagicMock()
_plotly_graph_objects.Scatter = MagicMock()
_plotly_express = _fake_module("plotly.express")

# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ib():
    """Return a fresh _FakeIB instance."""
    return _FakeIB()


@pytest.fixture
def ohlcv_df():
    """Return a small OHLCV DataFrame for signal/indicator tests."""
    return _make_ohlcv(60)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory and point DATA_DIR at it."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture(autouse=True)
def _patch_trades_file(tmp_path, monkeypatch):
    """Redirect all trades.json I/O to a temp file so live data is never touched."""
    fake_trades = tmp_path / "trades.json"
    fake_trades.write_text("[]")
    monkeypatch.setenv("DECIFER_TRADES_PATH", str(fake_trades))
    monkeypatch.setenv("DECIFER_TEST_MODE", "1")
    yield


@pytest.fixture(autouse=True)
def _redirect_hwm_state_file(tmp_path, monkeypatch):
    """
    Redirect risk.HWM_STATE_FILE to a per-test temp path so tests that call
    update_equity_high_water_mark() never write to the real data/hwm_state.json.
    Tests that specifically test HWM persistence override this via their own
    monkeypatch.setattr(risk, 'HWM_STATE_FILE', ...) call (which takes precedence).
    """
    try:
        import risk

        monkeypatch.setattr(risk, "HWM_STATE_FILE", str(tmp_path / "hwm_state.json"))
    except Exception:
        pass  # risk not yet imported in this test — no-op
    yield


@pytest.fixture(autouse=True)
def _redirect_event_log(tmp_path, monkeypatch):
    """
    Redirect event_log._LOG_FILE to a per-test temp path so execute_sell /
    execute_sell_option calls never write POSITION_CLOSED records to the real
    data/trade_events.jsonl.  Tests in test_event_log_and_training_store.py
    override this with their own monkeypatch.setattr() which takes precedence.
    """
    try:
        import event_log
        from pathlib import Path

        monkeypatch.setattr(event_log, "_LOG_FILE", Path(tmp_path / "events.jsonl"))
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _redirect_training_store(tmp_path, monkeypatch):
    """
    Redirect training_store._STORE_FILE to a per-test temp path so execute_sell
    calls never write training records to the real data/training_records.jsonl.
    """
    try:
        import training_store
        from pathlib import Path

        monkeypatch.setattr(training_store, "_STORE_FILE", Path(tmp_path / "training.jsonl"))
    except Exception:
        pass
    yield


@pytest.fixture
def config():
    """Return the Decifer CONFIG dict for signal tests."""
    import config as config_mod

    return getattr(config_mod, "CONFIG", {})


@pytest.fixture
def sample_ohlcv():
    """Alias for ohlcv_df — 60-row OHLCV DataFrame."""
    return _make_ohlcv(60)
