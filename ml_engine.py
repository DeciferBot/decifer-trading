# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ml_engine.py                               ║
# ║   ML Learning Loop: Trade Outcome Labeler, Pattern           ║
# ║   Recognition, Signal Enhancement, Regime Classification    ║
# ║   Production quality. Uses only free resources (scikit-learn)║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import os
import pickle
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import warnings

import numpy as np
import pandas as pd

log = logging.getLogger("decifer.ml_engine")

# Optional ML libraries with graceful fallback
try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score, TimeSeriesSplit
    from sklearn.metrics import (
        classification_report, confusion_matrix, roc_auc_score,
        mean_squared_error, r2_score
    )
    SKLEARN_AVAILABLE = True
    log.info("scikit-learn loaded successfully")
except Exception as e:
    SKLEARN_AVAILABLE = False
    log.warning(f"scikit-learn import failed: {type(e).__name__}: {e}")

try:
    import joblib
    JOBLIB_AVAILABLE = True
    log.info("joblib loaded successfully")
except Exception as e:
    JOBLIB_AVAILABLE = False
    log.warning(f"joblib import failed: {type(e).__name__}: {e}")

from config import CONFIG

# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

TRADE_LOG_FILE = CONFIG.get("trade_log", "data/trades.json")
MODELS_DIR = "data/models"
MIN_TRADES_FOR_ML = CONFIG.get("ml_min_trades", 50)
ML_CONFIDENCE_WEIGHT = CONFIG.get("ml_confidence_weight", 0.3)
REGIME_OPTIONS = ["BULL_TRENDING", "BEAR_TRENDING", "CHOPPY", "PANIC", "BREAKOUT"]
BREAKEVEN_THRESHOLD = 0.001  # Within 0.1% of entry price = breakeven


def ensure_models_dir():
    """Create models directory if it doesn't exist."""
    os.makedirs(MODELS_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────────────────
# TradeLabeler: Read trades.json and extract features + labels
# ──────────────────────────────────────────────────────────────────

class TradeLabeler:
    """
    Reads trades.json, labels outcomes (WIN/LOSS/BREAKEVEN),
    and extracts features at entry time.
    """

    def __init__(self, trade_log_file: str = TRADE_LOG_FILE):
        self.trade_log_file = trade_log_file
        self.trades = []
        self.labeled_data = None
        self.load_trades()

    def load_trades(self):
        """Load all trades from JSON."""
        if not os.path.exists(self.trade_log_file):
            log.warning(f"Trade log not found: {self.trade_log_file}")
            self.trades = []
            return

        try:
            with open(self.trade_log_file, "r") as f:
                self.trades = json.load(f)
            log.info(f"Loaded {len(self.trades)} trades from {self.trade_log_file}")
        except Exception as e:
            log.error(f"Failed to load trades: {e}")
            self.trades = []

    def label_trade(self, trade: dict) -> str:
        """
        Label trade outcome: WIN, LOSS, or BREAKEVEN.
        Uses actual P&L and holding period.
        """
        pnl = trade.get("pnl", 0)

        # Breakeven threshold: within 0.1% of entry price
        entry_price = trade.get("entry_price", 0)
        if entry_price > 0:
            pnl_pct = pnl / (entry_price * trade.get("shares", 1))
            if abs(pnl_pct) < BREAKEVEN_THRESHOLD:
                return "BREAKEVEN"

        if pnl > 0:
            return "WIN"
        elif pnl < 0:
            return "LOSS"
        else:
            return "BREAKEVEN"

    def extract_features(self, trade: dict) -> dict:
        """
        Extract features from trade at entry time.
        Includes: score, regime, VIX, volume, momentum, sector, time info.
        """
        try:
            entry_time = datetime.fromisoformat(
                trade.get("entry_time", "").replace(" ", "T")
            )
        except Exception:
            return None

        features = {
            "symbol": trade.get("symbol", ""),
            "score": trade.get("score", 0),
            "regime": trade.get("regime", "UNKNOWN"),
            "vix": trade.get("vix", 0.0),
            "shares": trade.get("shares", 0),
            "entry_price": trade.get("entry_price", 0.0),
            "exit_price": trade.get("exit_price", 0.0),
            "pnl": trade.get("pnl", 0),
            "holding_minutes": self._calculate_holding_time(trade),
            "time_of_day": entry_time.hour,
            "day_of_week": entry_time.weekday(),  # 0=Monday, 6=Sunday
            "is_weekend": entry_time.weekday() >= 5,
            "is_after_hours": entry_time.hour < 9 or entry_time.hour >= 16,
            "action": trade.get("action", "BUY"),
            "exit_reason": trade.get("exit_reason", ""),
            "agents_agreed": self._extract_agents_count(trade),
        }

        return features

    def _calculate_holding_time(self, trade: dict) -> float:
        """Calculate holding time in minutes."""
        try:
            entry = datetime.fromisoformat(
                trade.get("entry_time", "").replace(" ", "T")
            )
            exit_t = datetime.fromisoformat(
                trade.get("exit_time", "").replace(" ", "T")
            )
            delta = exit_t - entry
            return delta.total_seconds() / 60.0
        except Exception:
            return 0.0

    def _extract_agents_count(self, trade: dict) -> int:
        """Extract agents agreement count from reasoning field."""
        reasoning = trade.get("reasoning", "")
        # Look for "Agents agreed N/6" pattern
        import re
        match = re.search(r"Agents agreed (\d+)/\d+", reasoning)
        if match:
            return int(match.group(1))
        return 0

    def create_dataset(self) -> Optional[pd.DataFrame]:
        """Create labeled training dataset from all trades."""
        if not self.trades:
            log.warning("No trades to label")
            return None

        data_list = []
        for trade in self.trades:
            features = self.extract_features(trade)
            if features is None:
                continue

            label = self.label_trade(trade)
            features["outcome"] = label

            data_list.append(features)

        if not data_list:
            log.warning("Could not extract features from any trades")
            return None

        df = pd.DataFrame(data_list)
        self.labeled_data = df
        log.info(
            f"Created dataset: {len(df)} trades. "
            f"Outcomes: {df['outcome'].value_counts().to_dict()}"
        )
        return df


# ──────────────────────────────────────────────────────────────────
# DeciferML: Train models, predict, evaluate
# ──────────────────────────────────────────────────────────────────

class DeciferML:
    """
    Main ML engine. Trains RandomForest (win/loss) and GradientBoosting (returns).
    Includes cross-validation with walk-forward splits to prevent lookahead bias.
    """

    def __init__(self):
        if not SKLEARN_AVAILABLE:
            raise ImportError("scikit-learn required for ML engine")

        self.labeler = TradeLabeler()
        self.df = None
        self.X = None
        self.y = None
        self.scaler = StandardScaler()
        self.model_clf = None  # Win/Loss classifier
        self.model_reg = None  # Return predictor
        self.feature_names = []
        self.feature_importance = None
        self.train_date = None

    def prepare_data(self) -> bool:
        """Prepare features and labels for training."""
        self.df = self.labeler.create_dataset()
        if self.df is None or len(self.df) < MIN_TRADES_FOR_ML:
            log.warning(
                f"Insufficient trades ({len(self.df) if self.df is not None else 0} < {MIN_TRADES_FOR_ML})"
            )
            return False

        # Select features for ML
        feature_cols = [
            "score", "vix", "holding_minutes", "time_of_day", "day_of_week",
            "is_after_hours", "agents_agreed"
        ]

        # Add regime one-hot encoding
        regime_dummies = pd.get_dummies(self.df["regime"], prefix="regime")
        regime_dummies = regime_dummies.reindex(
            [f"regime_{r}" for r in REGIME_OPTIONS],
            fill_value=0,
            axis=1
        )

        # Combine features
        X = self.df[feature_cols].copy()
        X = pd.concat([X, regime_dummies], axis=1)

        # Handle missing/infinite values
        X = X.fillna(0)
        X = X.replace([np.inf, -np.inf], 0)

        self.X = X
        self.feature_names = list(X.columns)

        # Labels: binary (WIN=1, LOSS=0, BREAKEVEN=0.5 normalized)
        self.y = (self.df["outcome"] == "WIN").astype(int)

        log.info(f"Prepared {len(self.X)} samples with {len(self.feature_names)} features")
        return True

    def train_classifier(self) -> bool:
        """Train RandomForest for win/loss prediction."""
        if self.X is None:
            return False

        try:
            self.model_clf = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                min_samples_split=5,
                min_samples_leaf=2,
                random_state=42,
                n_jobs=-1
            )
            self.model_clf.fit(self.X, self.y)

            # Cross-validation with walk-forward splits (no lookahead bias)
            tscv = TimeSeriesSplit(n_splits=5)
            scores = cross_val_score(
                self.model_clf, self.X, self.y,
                cv=tscv, scoring="roc_auc"
            )
            log.info(
                f"Win/Loss classifier trained. "
                f"CV ROC-AUC: {scores.mean():.3f} (+/- {scores.std():.3f})"
            )

            # Feature importance
            importance = self.model_clf.feature_importances_
            self.feature_importance = dict(zip(self.feature_names, importance))

            return True
        except Exception as e:
            log.error(f"Failed to train classifier: {e}")
            return False

    def train_regressor(self) -> bool:
        """Train GradientBoosting for expected return prediction."""
        if self.X is None:
            return False

        try:
            # Target: PnL as percentage of entry
            y_returns = (
                self.df["pnl"] / (self.df["entry_price"] * self.df["shares"])
            ).fillna(0)

            self.model_reg = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42,
                subsample=0.8
            )
            self.model_reg.fit(self.X, y_returns)

            # Cross-validation
            tscv = TimeSeriesSplit(n_splits=5)
            scores = cross_val_score(
                self.model_reg, self.X, y_returns,
                cv=tscv, scoring="r2"
            )
            log.info(
                f"Return regressor trained. "
                f"CV R²: {scores.mean():.3f} (+/- {scores.std():.3f})"
            )

            return True
        except Exception as e:
            log.error(f"Failed to train regressor: {e}")
            return False

    def train(self) -> bool:
        """Full training pipeline."""
        if not self.prepare_data():
            return False

        success = True
        success &= self.train_classifier()
        success &= self.train_regressor()

        if success:
            self.train_date = datetime.now()
            self.save_models()

        return success

    def predict(self, features: dict) -> dict:
        """
        Predict win probability and expected return for a setup.
        features: dict with keys matching training features.
        Returns: {win_prob, expected_return, confidence}
        """
        if self.model_clf is None or self.model_reg is None:
            return {"win_prob": 0.5, "expected_return": 0.0, "confidence": 0.0}

        try:
            # Convert features dict to array
            X_sample = self._features_to_array([features])

            win_prob = self.model_clf.predict_proba(X_sample)[0, 1]
            expected_return = self.model_reg.predict(X_sample)[0]
            confidence = abs(2 * win_prob - 1)  # 0.5 = no confidence, 1.0 = high

            return {
                "win_prob": float(win_prob),
                "expected_return": float(expected_return),
                "confidence": float(confidence)
            }
        except Exception as e:
            log.error(f"Prediction failed: {e}")
            return {"win_prob": 0.5, "expected_return": 0.0, "confidence": 0.0}

    def _features_to_array(self, features_list: List[dict]) -> np.ndarray:
        """Convert list of feature dicts to properly shaped array."""
        rows = []
        for features in features_list:
            row = []
            for fname in self.feature_names:
                if fname.startswith("regime_"):
                    regime = features.get("regime", "UNKNOWN")
                    row.append(1 if fname == f"regime_{regime}" else 0)
                else:
                    row.append(features.get(fname, 0))
            rows.append(row)
        return np.array(rows)

    def save_models(self):
        """Persist models to disk."""
        if not JOBLIB_AVAILABLE:
            log.warning("joblib unavailable. Skipping model save.")
            return

        ensure_models_dir()

        try:
            joblib.dump(self.model_clf, os.path.join(MODELS_DIR, "classifier.pkl"))
            joblib.dump(self.model_reg, os.path.join(MODELS_DIR, "regressor.pkl"))
            joblib.dump(self.scaler, os.path.join(MODELS_DIR, "scaler.pkl"))
            joblib.dump(self.feature_names, os.path.join(MODELS_DIR, "features.pkl"))

            metadata = {
                "train_date": self.train_date.isoformat() if self.train_date else None,
                "num_trades": len(self.df) if self.df is not None else 0,
                "feature_importance": self.feature_importance,
            }
            with open(os.path.join(MODELS_DIR, "metadata.json"), "w") as f:
                json.dump(metadata, f, indent=2)

            log.info(f"Models saved to {MODELS_DIR}")
        except Exception as e:
            log.error(f"Failed to save models: {e}")

    def load_models(self) -> bool:
        """Load persisted models."""
        if not JOBLIB_AVAILABLE:
            return False

        try:
            self.model_clf = joblib.load(os.path.join(MODELS_DIR, "classifier.pkl"))
            self.model_reg = joblib.load(os.path.join(MODELS_DIR, "regressor.pkl"))
            self.scaler = joblib.load(os.path.join(MODELS_DIR, "scaler.pkl"))
            self.feature_names = joblib.load(os.path.join(MODELS_DIR, "features.pkl"))

            with open(os.path.join(MODELS_DIR, "metadata.json"), "r") as f:
                metadata = json.load(f)
                self.train_date = datetime.fromisoformat(metadata.get("train_date"))
                self.feature_importance = metadata.get("feature_importance", {})

            log.info(f"Models loaded from {MODELS_DIR}")
            return True
        except Exception as e:
            log.error(f"Failed to load models: {e}")
            return False

    def evaluate(self) -> dict:
        """Evaluate models on training data."""
        if self.model_clf is None or self.X is None:
            return {}

        try:
            y_pred = self.model_clf.predict(self.X)
            y_pred_proba = self.model_clf.predict_proba(self.X)[:, 1]

            results = {
                "classifier": {
                    "roc_auc": float(roc_auc_score(self.y, y_pred_proba)),
                    "accuracy": float((y_pred == self.y).mean()),
                    "confusion": confusion_matrix(self.y, y_pred).tolist(),
                }
            }

            if self.model_reg is not None:
                y_returns = (
                    self.df["pnl"] / (self.df["entry_price"] * self.df["shares"])
                ).fillna(0)
                y_returns_pred = self.model_reg.predict(self.X)

                results["regressor"] = {
                    "r2": float(r2_score(y_returns, y_returns_pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y_returns, y_returns_pred))),
                }

            return results
        except Exception as e:
            log.error(f"Evaluation failed: {e}")
            return {}


# ──────────────────────────────────────────────────────────────────
# SignalEnhancer: Adjust 0-50 score with ML confidence
# ──────────────────────────────────────────────────────────────────

class SignalEnhancer:
    """
    Takes base signal score (0-50) and adjusts it using ML predictions.
    Returns enhanced_score and ml_confidence.
    """

    def __init__(self):
        self.ml_engine = None
        self.ml_enabled = CONFIG.get("ml_enabled", True)

        if self.ml_enabled and SKLEARN_AVAILABLE:
            try:
                self.ml_engine = DeciferML()
                self.ml_engine.load_models()
            except Exception as e:
                log.warning(f"Could not initialize ML engine: {e}. Using base scores only.")
                self.ml_enabled = False

    def enhance_score(self, symbol_data: dict) -> dict:
        """
        Enhance base score with ML confidence.

        symbol_data should contain:
          - base_score: 0-50 from signal engine
          - symbol: stock ticker
          - regime: current market regime
          - vix: current VIX level
          - time_of_day: current hour (0-23)
          - holding_target: target holding period in minutes
          - agents_agreed: how many agents agreed

        Returns:
          - enhanced_score: adjusted 0-50 score
          - ml_confidence: 0-1 confidence in adjustment
          - ml_details: dict with win_prob, expected_return
        """
        base_score = symbol_data.get("base_score", 25)
        result = {
            "enhanced_score": base_score,
            "adjusted_score": base_score,  # alias — same value, preferred key in tests
            "ml_confidence": 0.0,
            "ml_details": {},
            "ml_enabled": self.ml_enabled,
        }

        if not self.ml_enabled or self.ml_engine is None:
            return result

        try:
            # Prepare features for ML prediction
            features = {
                "score": base_score,
                "vix": symbol_data.get("vix", 20.0),
                "holding_minutes": symbol_data.get("holding_target", 60),
                "time_of_day": symbol_data.get("time_of_day", 10),
                "day_of_week": symbol_data.get("day_of_week", 0),
                "is_after_hours": symbol_data.get("is_after_hours", False),
                "agents_agreed": symbol_data.get("agents_agreed", 2),
                "regime": symbol_data.get("regime", "UNKNOWN"),
            }

            # Get ML prediction
            ml_pred = self.ml_engine.predict(features)

            # Adjust score based on win probability
            # win_prob > 0.5 = setup looks like past winners
            # confidence multiplier: 0.5x to 1.5x
            multiplier = 0.5 + ml_pred["win_prob"]  # Range: [0.5, 1.5]
            enhanced = base_score * multiplier

            # Clamp to 0-50 range
            enhanced = max(0, min(50, enhanced))

            result["enhanced_score"] = enhanced
            result["adjusted_score"] = enhanced  # keep alias in sync
            result["ml_confidence"] = ml_pred["confidence"]
            result["ml_details"] = {
                "win_probability": ml_pred["win_prob"],
                "expected_return": ml_pred["expected_return"],
                "confidence": ml_pred["confidence"],
            }

            # Log if significant change
            score_change = enhanced - base_score
            if abs(score_change) > 2:
                win_pct = int(ml_pred["win_prob"] * 100)
                log.info(
                    f"{symbol_data.get('symbol', '?')}: Base score {base_score} → {enhanced:.1f} "
                    f"(similar setups won {win_pct}% of time)"
                )

        except Exception as e:
            log.error(f"Failed to enhance score: {e}")

        return result


# ──────────────────────────────────────────────────────────────────
# RegimeClassifier: ML-based regime detection
# ──────────────────────────────────────────────────────────────────

class RegimeClassifier:
    """
    ML-based regime detection. Trained on labeled historical data.
    Predicts: BULL_TRENDING, BEAR_TRENDING, CHOPPY, PANIC, BREAKOUT

    NOT connected to the production pipeline.
    Use scanner.get_market_regime() for live regime detection.
    See DECISIONS.md Action #9 and config["regime_detector"].
    """

    # Production guard — prevents accidental wiring into the live pipeline.
    # Set to False only after IC Phase 2 gate review (closed_trades >= 200)
    # and only when replacing (not supplementing) the VIX-proxy detector.
    PRODUCTION_LOCKED = True

    def __init__(self):
        if not SKLEARN_AVAILABLE:
            raise ImportError("scikit-learn required for RegimeClassifier")

        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def train_from_data(self, df: pd.DataFrame) -> bool:
        """
        Train regime classifier from OHLCV data with regime labels.
        df should have: close, volume, returns, volatility, regime columns.
        """
        if df is None or len(df) < 50:
            log.warning("Insufficient data for regime classifier")
            return False

        try:
            # Features: rolling returns, volatility, volume trend
            feature_cols = ["returns", "volatility", "volume_ma_ratio"]

            X = df[feature_cols].fillna(0).replace([np.inf, -np.inf], 0)
            y = df["regime"]

            self.feature_names = feature_cols
            self.model = RandomForestClassifier(
                n_estimators=50, max_depth=7, random_state=42
            )
            self.model.fit(X, y)

            score = self.model.score(X, y)
            log.info(f"Regime classifier trained. Accuracy: {score:.3f}")
            return True

        except Exception as e:
            log.error(f"Failed to train regime classifier: {e}")
            return False

    def predict_regime(self, market_data: dict) -> str:
        """
        Predict market regime from current data.
        market_data: {returns, volatility, volume_ma_ratio}
        Returns: regime string
        """
        if self.PRODUCTION_LOCKED:
            raise RuntimeError(
                "RegimeClassifier.predict_regime() is locked for production use. "
                "Use scanner.get_market_regime() instead. "
                "See DECISIONS.md Action #9."
            )

        if self.model is None:
            return "UNKNOWN"

        try:
            X = np.array([[
                market_data.get("returns", 0),
                market_data.get("volatility", 1.0),
                market_data.get("volume_ma_ratio", 1.0)
            ]])

            regime = self.model.predict(X)[0]
            return str(regime)
        except Exception as e:
            log.error(f"Regime prediction failed: {e}")
            return "UNKNOWN"


# ──────────────────────────────────────────────────────────────────
# Weekly Report Generator
# ──────────────────────────────────────────────────────────────────

class WeeklyReportGenerator:
    """Generate performance analytics and pattern insights."""

    def __init__(self):
        self.labeler = TradeLabeler()
        self.df = None

    def generate_report(self) -> str:
        """Generate comprehensive weekly performance report."""
        self.df = self.labeler.create_dataset()

        if self.df is None or len(self.df) < 10:
            return "Insufficient trade data for report (< 10 trades)"

        report = []
        report.append("=" * 70)
        report.append("DECIFER ML WEEKLY PERFORMANCE REPORT")
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("=" * 70)
        report.append("")

        # Overall stats
        report.extend(self._overall_stats())
        report.append("")

        # Regime performance
        report.extend(self._regime_performance())
        report.append("")

        # Time-of-day patterns
        report.extend(self._time_of_day_performance())
        report.append("")

        # Setup analysis
        report.extend(self._setup_analysis())
        report.append("")

        # Feature importance
        if SKLEARN_AVAILABLE:
            report.extend(self._feature_importance())

        report.append("")
        report.append("=" * 70)

        return "\n".join(report)

    def _overall_stats(self) -> List[str]:
        """Overall win rate, average return, etc."""
        lines = ["OVERALL PERFORMANCE"]
        lines.append("-" * 70)

        total_trades = len(self.df)
        wins = (self.df["outcome"] == "WIN").sum()
        losses = (self.df["outcome"] == "LOSS").sum()
        breakeven = (self.df["outcome"] == "BREAKEVEN").sum()

        win_rate = wins / total_trades if total_trades > 0 else 0

        total_pnl = self.df["pnl"].sum()
        avg_win = self.df[self.df["outcome"] == "WIN"]["pnl"].mean()
        avg_loss = abs(self.df[self.df["outcome"] == "LOSS"]["pnl"].mean())

        lines.append(f"Total Trades:        {total_trades}")
        lines.append(f"Wins / Losses / BE:  {wins} / {losses} / {breakeven}")
        lines.append(f"Win Rate:            {win_rate:.1%}")
        lines.append(f"Total P&L:           ${total_pnl:,.2f}")
        lines.append(f"Avg Win / Avg Loss:  ${avg_win:,.2f} / ${avg_loss:,.2f}")

        if losses > 0:
            lines.append(f"Profit Factor:       {abs(wins * avg_win) / (losses * avg_loss):.2f}" if avg_loss > 0 else "Inf")

        return lines

    def _regime_performance(self) -> List[str]:
        """Breakdown by market regime."""
        lines = ["REGIME-SPECIFIC PERFORMANCE"]
        lines.append("-" * 70)

        for regime in self.df["regime"].unique():
            subset = self.df[self.df["regime"] == regime]
            wins = (subset["outcome"] == "WIN").sum()
            total = len(subset)
            win_pct = wins / total if total > 0 else 0

            avg_pnl = subset["pnl"].mean()

            lines.append(
                f"{regime:15} │ {total:3} trades │ Win: {win_pct:6.1%} │ Avg P&L: ${avg_pnl:>9,.2f}"
            )

        return lines

    def _time_of_day_performance(self) -> List[str]:
        """Breakdown by hour of day."""
        lines = ["TIME-OF-DAY PERFORMANCE"]
        lines.append("-" * 70)

        for hour in sorted(self.df["time_of_day"].unique()):
            subset = self.df[self.df["time_of_day"] == hour]
            wins = (subset["outcome"] == "WIN").sum()
            total = len(subset)

            if total == 0:
                continue

            win_pct = wins / total
            avg_pnl = subset["pnl"].mean()

            time_range = f"{int(hour):02d}:00-{int(hour)+1:02d}:00"
            lines.append(
                f"{time_range} │ {total:3} trades │ Win: {win_pct:6.1%} │ Avg P&L: ${avg_pnl:>9,.2f}"
            )

        return lines

    def _setup_analysis(self) -> List[str]:
        """Best and worst setups."""
        lines = ["SETUP ANALYSIS"]
        lines.append("-" * 70)

        # Score ranges
        score_ranges = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50)]

        for low, high in score_ranges:
            subset = self.df[(self.df["score"] >= low) & (self.df["score"] < high)]
            if len(subset) == 0:
                continue

            wins = (subset["outcome"] == "WIN").sum()
            total = len(subset)
            win_pct = wins / total

            avg_pnl = subset["pnl"].mean()

            lines.append(
                f"Score {low:2d}-{high:2d} │ {total:3} trades │ Win: {win_pct:6.1%} │ Avg P&L: ${avg_pnl:>9,.2f}"
            )

        return lines

    def _feature_importance(self) -> List[str]:
        """Show which dimensions matter most."""
        lines = ["FEATURE IMPORTANCE (from last trained model)"]
        lines.append("-" * 70)

        models_dir = MODELS_DIR
        metadata_path = os.path.join(models_dir, "metadata.json")

        if not os.path.exists(metadata_path):
            return ["No model metadata available."]

        try:
            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            importance = metadata.get("feature_importance", {})
            if not importance:
                return ["No feature importance data available."]

            # Sort by importance descending
            sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)

            for feature, imp in sorted_features[:10]:
                lines.append(f"{feature:25} │ {imp:.4f}")

        except Exception as e:
            lines.append(f"Error reading metadata: {e}")

        return lines


# ──────────────────────────────────────────────────────────────────
# Public API Functions
# ──────────────────────────────────────────────────────────────────

def train_models() -> bool:
    """
    Train all ML models from scratch.
    Requires sklearn and joblib.
    """
    if not SKLEARN_AVAILABLE:
        log.error("scikit-learn required for training. Install: pip install scikit-learn joblib")
        return False

    log.info("Starting ML training pipeline...")

    try:
        ml = DeciferML()
        success = ml.train()

        if success:
            log.info("ML training complete")
            eval_results = ml.evaluate()
            if eval_results:
                log.info(f"Evaluation results: {eval_results}")

        return success

    except Exception as e:
        log.error(f"Training failed: {e}")
        return False


def enhance_score(symbol_data: dict) -> dict:
    """
    Public API: Enhance base signal score with ML adjustments.

    Args:
        symbol_data: dict with base_score (0-50), symbol, regime, vix, etc.

    Returns:
        dict with enhanced_score, ml_confidence, ml_details
    """
    enhancer = SignalEnhancer()
    return enhancer.enhance_score(symbol_data)


def generate_weekly_report() -> str:
    """
    Public API: Generate weekly performance analytics.
    Returns formatted report string.
    """
    generator = WeeklyReportGenerator()
    return generator.generate_report()


# ──────────────────────────────────────────────────────────────────
# CLI Interface
# ──────────────────────────────────────────────────────────────────

def main():
    """CLI mode: python3 ml_engine.py --train or --report"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Decifer ML Engine — Train models or generate reports"
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Train all ML models from scratch"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate weekly performance report"
    )
    parser.add_argument(
        "--eval",
        action="store_true",
        help="Evaluate trained models"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging"
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )

    if not args.train and not args.report and not args.eval:
        parser.print_help()
        return

    if args.train:
        print("\n[ML Engine] Training models...")
        success = train_models()
        print(f"Training {'succeeded' if success else 'failed'}")

    if args.report:
        print("\n[ML Engine] Generating weekly report...")
        report = generate_weekly_report()
        print(report)

    if args.eval:
        print("\n[ML Engine] Evaluating models...")
        if not SKLEARN_AVAILABLE:
            print("ERROR: scikit-learn not available")
            return

        try:
            ml = DeciferML()
            if ml.load_models():
                results = ml.evaluate()
                print("\nEvaluation Results:")
                print(json.dumps(results, indent=2))
            else:
                print("Failed to load models")
        except Exception as e:
            print(f"Evaluation failed: {e}")


if __name__ == "__main__":
    main()
