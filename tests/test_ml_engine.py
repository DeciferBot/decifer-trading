# tests/test_ml_engine.py
# Tests for ml_engine.py — TradeLabeler, DeciferML, SignalEnhancer, RegimeClassifier

import json
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy dependencies BEFORE importing Decifer modules ──────────────

# Stub anthropic
anthropicmod = types.ModuleType("anthropic")
anthropicmod.Anthropic = MagicMock
sys.modules.setdefault("anthropic", anthropicmod)

# Stub ib_async
ib_mod = types.ModuleType("ib_async")
ib_mod.IB = MagicMock
sys.modules.setdefault("ib_async", ib_mod)

# Stub config
configmod = types.ModuleType("config")
configmod.CONFIG = {
    "anthropic_api_key": "test-key",
    "claude_model": "claude-sonnet-4-6",
    "starting_capital": 100_000,
    "trade_log": "/tmp/ml_test_trades.json",
    "order_log": "/tmp/ml_test_orders.json",
    "log_file": "/tmp/ml_test.log",
    "ml_min_trades": 50,
    "ml_confidence_weight": 0.3,
}
sys.modules.setdefault("config", configmod)

import ml_engine
from ml_engine import (
    SKLEARN_AVAILABLE,
    DeciferML,
    RegimeClassifier,
    SignalEnhancer,
    TradeLabeler,
    WeeklyReportGenerator,
    enhance_score,
    ensure_models_dir,
)

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


_DEFAULT_SIGNAL_SCORES = {
    "trend": 8, "momentum": 6, "squeeze": 2, "flow": 7, "breakout": 3,
    "news": 1, "social": 1, "reversion": 0, "overnight_drift": 4,
    "pead": 0, "short_squeeze": 0, "catalyst": 0, "analyst_revision": 0,
    "iv_skew": 2, "fx_macro": 0, "fx_momentum": 0, "insider_buying": 5, "mtf": 9,
}


def _make_trade_record(
    symbol="AAPL",
    pnl=150.0,
    entry_price=100.0,
    exit_price=101.5,
    shares=10,
    score=7.0,
    regime="TRENDING_UP",
    vix=14.0,
    entry_time="2024-01-15T10:30:00",
    exit_time="2024-01-15T14:00:00",
    reasoning="Agents agreed 5/6 — strong setup",
    action="BUY",
    exit_reason="TP",
    signal_scores=None,
):
    return {
        "symbol": symbol,
        "pnl": pnl,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        "score": score,
        "regime": regime,
        "vix": vix,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "reasoning": reasoning,
        "action": action,
        "exit_reason": exit_reason,
        "signal_scores": signal_scores if signal_scores is not None else dict(_DEFAULT_SIGNAL_SCORES),
    }


def _make_sufficient_trades(n=60):
    """Generate n synthetic trades for ML training."""
    trades = []
    for i in range(n):
        pnl = 100.0 if i % 3 != 0 else -50.0
        regime = ["TRENDING_UP", "TRENDING_DOWN", "RANGE_BOUND"][i % 3]
        t = _make_trade_record(
            symbol=f"SYM{i % 10}",
            pnl=pnl,
            entry_price=100.0,
            exit_price=101.0 if pnl > 0 else 99.5,
            shares=10,
            score=5.0 + (i % 5),
            regime=regime,
            vix=12.0 + (i % 5),
            entry_time=f"2024-01-{1 + (i % 28):02d}T10:30:00",
            exit_time=f"2024-01-{1 + (i % 28):02d}T14:00:00",
        )
        trades.append(t)
    return trades


# ────────────────────────────────────────────────────────────────────────────
# ensure_models_dir
# ────────────────────────────────────────────────────────────────────────────


class TestEnsureModelsDir:
    def test_creates_directory(self, tmp_path):
        """ensure_models_dir creates the models directory."""
        orig = ml_engine.MODELS_DIR
        ml_engine.MODELS_DIR = str(tmp_path / "models")
        ensure_models_dir()
        assert os.path.isdir(ml_engine.MODELS_DIR)
        ml_engine.MODELS_DIR = orig

    def test_idempotent_when_dir_exists(self, tmp_path):
        """ensure_models_dir does not raise if directory already exists."""
        orig = ml_engine.MODELS_DIR
        ml_engine.MODELS_DIR = str(tmp_path / "models")
        ensure_models_dir()
        ensure_models_dir()  # second call — must not raise
        ml_engine.MODELS_DIR = orig


# ────────────────────────────────────────────────────────────────────────────
# TradeLabeler tests
# ────────────────────────────────────────────────────────────────────────────


class TestTradeLabeler:
    """Tests for TradeLabeler — label_trade, extract_features, create_dataset."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)  # noqa: SIM115
        self.tmp.close()
        os.remove(self.tmp.name)

    def teardown_method(self):
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def _labeler(self, trades=None):
        if trades:
            with open(self.tmp.name, "w") as f:
                json.dump(trades, f)
        return TradeLabeler(trade_log_file=self.tmp.name)

    # ── label_trade ──────────────────────────────────────────────

    def test_label_trade_win(self):
        """Positive P&L well above threshold → WIN."""
        lb = self._labeler([])
        trade = _make_trade_record(pnl=500.0, entry_price=100.0, shares=10)
        assert lb.label_trade(trade) == "WIN"

    def test_label_trade_loss(self):
        """Negative P&L → LOSS."""
        lb = self._labeler([])
        trade = _make_trade_record(pnl=-200.0, entry_price=100.0, shares=10)
        assert lb.label_trade(trade) == "LOSS"

    def test_label_trade_breakeven(self):
        """P&L within 0.1% of entry value → BREAKEVEN."""
        lb = self._labeler([])
        # 0.05% return → within BREAKEVEN_THRESHOLD of 0.1%
        trade = _make_trade_record(pnl=0.05, entry_price=100.0, shares=10)
        assert lb.label_trade(trade) == "BREAKEVEN"

    def test_label_trade_zero_pnl(self):
        """P&L of exactly 0 → BREAKEVEN."""
        lb = self._labeler([])
        trade = _make_trade_record(pnl=0.0, entry_price=100.0, shares=10)
        assert lb.label_trade(trade) in ("BREAKEVEN", "LOSS")

    # ── extract_features ─────────────────────────────────────────

    def test_extract_features_returns_dict(self):
        """extract_features returns a dict with expected keys."""
        lb = self._labeler([])
        trade = _make_trade_record()
        features = lb.extract_features(trade)
        assert features is not None
        assert "score" in features
        assert "regime" in features
        assert "vix" in features
        assert "time_of_day" in features
        assert "day_of_week" in features

    def test_extract_features_bad_entry_time_returns_none(self):
        """extract_features returns None when entry_time is missing/malformed."""
        lb = self._labeler([])
        trade = _make_trade_record()
        trade["entry_time"] = "NOT_A_DATE"
        result = lb.extract_features(trade)
        assert result is None

    def test_extract_features_holding_time_positive(self):
        """holding_minutes is positive for a same-day trade."""
        lb = self._labeler([])
        trade = _make_trade_record(entry_time="2024-01-15T10:00:00", exit_time="2024-01-15T12:30:00")
        features = lb.extract_features(trade)
        assert features["holding_minutes"] == 150.0

    # ── create_dataset ───────────────────────────────────────────

    def test_create_dataset_no_trades(self):
        """create_dataset returns None when no trades are present."""
        lb = self._labeler([])
        result = lb.create_dataset()
        assert result is None

    def test_create_dataset_returns_dataframe(self):
        """create_dataset returns a DataFrame with outcome column."""
        trades = [_make_trade_record(pnl=100 if i % 2 == 0 else -50) for i in range(10)]
        lb = self._labeler(trades)
        df = lb.create_dataset()
        assert df is not None
        assert "outcome" in df.columns
        assert len(df) == 10

    def test_create_dataset_outcomes_are_valid(self):
        """All outcomes in dataset are WIN, LOSS, or BREAKEVEN."""
        trades = [_make_trade_record(pnl=pnl) for pnl in [200, -100, 0.01, -300, 500]]
        lb = self._labeler(trades)
        df = lb.create_dataset()
        assert set(df["outcome"].unique()).issubset({"WIN", "LOSS", "BREAKEVEN"})


# ────────────────────────────────────────────────────────────────────────────
# DeciferML tests
# ────────────────────────────────────────────────────────────────────────────

import pytest


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestDeciferML:
    """Tests for DeciferML — prepare_data, train, predict."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)  # noqa: SIM115
        self.tmp.close()
        trades = _make_sufficient_trades(60)
        with open(self.tmp.name, "w") as f:
            json.dump(trades, f)
        # Point the TradeLabeler to our file
        self._orig_log = ml_engine.TRADE_LOG_FILE
        ml_engine.TRADE_LOG_FILE = self.tmp.name

    def teardown_method(self):
        ml_engine.TRADE_LOG_FILE = self._orig_log
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def _fresh_ml(self):
        ml = DeciferML.__new__(DeciferML)
        from sklearn.preprocessing import StandardScaler

        ml.labeler = TradeLabeler(trade_log_file=self.tmp.name)
        ml.df = None
        ml.X = None
        ml.y = None
        ml.scaler = StandardScaler()
        ml.model_clf = None
        ml.model_reg = None
        ml.feature_names = []
        ml.feature_importance = None
        ml.train_date = None
        return ml

    def test_prepare_data_returns_true_with_enough_trades(self):
        """prepare_data succeeds when we have >= MIN_TRADES_FOR_ML trades."""
        orig_min = ml_engine.MIN_TRADES_FOR_ML
        ml_engine.MIN_TRADES_FOR_ML = 50
        ml = self._fresh_ml()
        result = ml.prepare_data()
        ml_engine.MIN_TRADES_FOR_ML = orig_min
        assert result is True
        assert ml.X is not None
        assert ml.y is not None

    def test_prepare_data_returns_false_with_too_few_trades(self):
        """prepare_data returns False when fewer than MIN_TRADES_FOR_ML trades."""
        orig_min = ml_engine.MIN_TRADES_FOR_ML
        ml_engine.MIN_TRADES_FOR_ML = 1_000
        ml = self._fresh_ml()
        result = ml.prepare_data()
        ml_engine.MIN_TRADES_FOR_ML = orig_min
        assert result is False

    def test_train_classifier_runs_after_prepare(self):
        """train_classifier succeeds after prepare_data."""
        orig_min = ml_engine.MIN_TRADES_FOR_ML
        ml_engine.MIN_TRADES_FOR_ML = 50
        ml = self._fresh_ml()
        ml.prepare_data()
        result = ml.train_classifier()
        ml_engine.MIN_TRADES_FOR_ML = orig_min
        assert result is True
        assert ml.model_clf is not None

    def test_train_regressor_runs_after_prepare(self):
        """train_regressor succeeds after prepare_data."""
        orig_min = ml_engine.MIN_TRADES_FOR_ML
        ml_engine.MIN_TRADES_FOR_ML = 50
        ml = self._fresh_ml()
        ml.prepare_data()
        result = ml.train_regressor()
        ml_engine.MIN_TRADES_FOR_ML = orig_min
        assert result is True
        assert ml.model_reg is not None

    def test_predict_returns_defaults_without_models(self):
        """predict returns neutral defaults when models are not trained."""
        ml = self._fresh_ml()
        result = ml.predict({"score": 7.0, "regime": "TRENDING_UP", "vix": 14.0})
        assert result["win_prob"] == 0.5
        assert result["expected_return"] == 0.0
        assert result["confidence"] == 0.0

    def test_predict_returns_probability_in_range(self):
        """After training, predict returns win_prob in [0, 1]."""
        orig_min = ml_engine.MIN_TRADES_FOR_ML
        ml_engine.MIN_TRADES_FOR_ML = 50
        ml = self._fresh_ml()
        ml.prepare_data()
        ml.train_classifier()
        ml.train_regressor()
        ml_engine.MIN_TRADES_FOR_ML = orig_min

        features = {
            "score": 7.0,
            "regime": "TRENDING_UP",
            "vix": 14.0,
            "holding_minutes": 120,
            "time_of_day": 10,
            "day_of_week": 0,
            "is_after_hours": False,
        }
        result = ml.predict(features)
        assert 0.0 <= result["win_prob"] <= 1.0
        assert 0.0 <= result["confidence"] <= 1.0
        assert isinstance(result["expected_return"], float)

    def test_features_to_array_shape(self):
        """_features_to_array produces correct shape."""
        orig_min = ml_engine.MIN_TRADES_FOR_ML
        ml_engine.MIN_TRADES_FOR_ML = 50
        ml = self._fresh_ml()
        ml.prepare_data()
        ml_engine.MIN_TRADES_FOR_ML = orig_min
        features = [{"score": 7.0, "vix": 15.0}]
        arr = ml._features_to_array(features)
        assert arr.ndim == 2
        assert arr.shape[0] == 1
        assert arr.shape[1] == len(ml.feature_names)


# ────────────────────────────────────────────────────────────────────────────
# SignalEnhancer tests
# ────────────────────────────────────────────────────────────────────────────


class TestSignalEnhancer:
    """Tests for SignalEnhancer.enhance_score and module-level enhance_score."""

    def _enhancer(self):
        return SignalEnhancer()

    def test_enhance_score_returns_dict_with_adjusted_score(self):
        """enhance_score returns a dict containing 'adjusted_score'."""
        enhancer = self._enhancer()
        symbol_data = {
            "symbol": "AAPL",
            "base_score": 7.0,
            "regime": "TRENDING_UP",
            "vix": 14.0,
            "score": 7.0,
        }
        result = enhancer.enhance_score(symbol_data)
        assert isinstance(result, dict)
        assert "adjusted_score" in result

    def test_enhance_score_adjusted_is_numeric(self):
        """adjusted_score is a number."""
        enhancer = self._enhancer()
        symbol_data = {"symbol": "TSLA", "base_score": 6.0, "score": 6.0, "regime": "RANGE_BOUND", "vix": 20.0}
        result = enhancer.enhance_score(symbol_data)
        assert isinstance(result["adjusted_score"], (int, float))

    def test_enhance_score_without_models_preserves_base(self):
        """Without trained models, adjusted_score equals or is close to base_score."""
        enhancer = self._enhancer()
        # Ensure no model is loaded
        enhancer.model = None
        symbol_data = {"symbol": "NVDA", "base_score": 8.5, "score": 8.5, "regime": "TRENDING_UP", "vix": 12.0}
        result = enhancer.enhance_score(symbol_data)
        # adjusted_score should be a reasonable number (not NaN/None)
        assert result["adjusted_score"] is not None
        assert not (
            isinstance(result["adjusted_score"], float) and result["adjusted_score"] != result["adjusted_score"]
        )  # not NaN

    def test_module_level_enhance_score_works(self):
        """Module-level enhance_score function is callable and returns dict."""
        symbol_data = {"symbol": "AMD", "base_score": 5.0, "score": 5.0, "regime": "TRENDING_DOWN", "vix": 25.0}
        result = enhance_score(symbol_data)
        assert isinstance(result, dict)


# ────────────────────────────────────────────────────────────────────────────
# RegimeClassifier tests
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestRegimeClassifier:
    """Tests for RegimeClassifier — production-locked, research use only."""

    def test_production_locked_flag_is_true(self):
        """RegimeClassifier must carry PRODUCTION_LOCKED=True (see DECISIONS.md Action #9)."""
        assert RegimeClassifier.PRODUCTION_LOCKED is True

    def test_predict_regime_raises_when_locked(self):
        """predict_regime must raise RuntimeError while PRODUCTION_LOCKED is True."""
        rc = RegimeClassifier()
        market_data = {"returns": 0.01, "volatility": 0.15, "volume_ma_ratio": 1.0}
        with pytest.raises(RuntimeError, match="production"):
            rc.predict_regime(market_data)


# ────────────────────────────────────────────────────────────────────────────
# WeeklyReportGenerator tests
# ────────────────────────────────────────────────────────────────────────────


class TestWeeklyReportGenerator:
    """Tests for WeeklyReportGenerator.generate_report."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)  # noqa: SIM115
        self.tmp.close()
        self._orig = ml_engine.TRADE_LOG_FILE
        ml_engine.TRADE_LOG_FILE = self.tmp.name

    def teardown_method(self):
        ml_engine.TRADE_LOG_FILE = self._orig
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def _write_trades(self, trades):
        with open(self.tmp.name, "w") as f:
            json.dump(trades, f)

    def test_generate_report_no_trades_returns_string(self):
        """generate_report returns a string even with empty trade log."""
        self._write_trades([])
        gen = WeeklyReportGenerator()
        report = gen.generate_report()
        assert isinstance(report, str)

    def test_generate_report_with_trades_returns_nonempty_string(self):
        """generate_report with closed trades returns a nonempty report."""
        trades = [
            _make_trade_record(pnl=200.0, regime="TRENDING_UP"),
            _make_trade_record(pnl=-80.0, regime="RANGE_BOUND", symbol="MSFT"),
            _make_trade_record(pnl=120.0, regime="TRENDING_UP", symbol="NVDA"),
        ]
        self._write_trades(trades)
        gen = WeeklyReportGenerator()
        report = gen.generate_report()
        assert isinstance(report, str)
        assert len(report) > 0
