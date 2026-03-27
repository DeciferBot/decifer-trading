# ML Engine Integration Guide

Decifer's ML learning loop module (`ml_engine.py`) provides machine learning capabilities that learn from your trade history to identify winning patterns and enhance trading signals.

## Features

### 1. Trade Outcome Labeler
- Reads `data/trades.json` and labels outcomes (WIN/LOSS/BREAKEVEN)
- Extracts 17+ features at entry time:
  - Signal score, regime, VIX level, volume metrics
  - Time-of-day, day-of-week patterns
  - Holding period, agents agreement count
  - Technical indicators (RSI, EMA alignment, sector)

### 2. Pattern Recognition Models
- **RandomForestClassifier**: Predicts win/loss probability for new setups
- **GradientBoostingRegressor**: Predicts expected return in %
- Walk-forward cross-validation prevents lookahead bias
- Feature importance analysis identifies which dimensions drive wins

### 3. Signal Enhancer
Wraps existing 0-50 score with ML adjustments:
```python
from ml_engine import enhance_score

result = enhance_score({
    "base_score": 35,
    "symbol": "AAPL",
    "regime": "BULL_TRENDING",
    "vix": 18.5,
    "agents_agreed": 4,
    # ... other features
})

# Result:
# {
#   "enhanced_score": 42.3,  # Boosted because similar setups won 80% of time
#   "ml_confidence": 0.6,     # High confidence in adjustment
#   "ml_details": {
#     "win_probability": 0.80,
#     "expected_return": 0.015,  # 1.5% expected gain
#     "confidence": 0.6
#   }
# }
```

### 4. Regime Classifier
ML-based market regime detection (supplement to VIX):
- BULL_TRENDING: Strong uptrend, favorable for longs
- BEAR_TRENDING: Strong downtrend, favorable for shorts
- CHOPPY: Sideways consolidation, high whipsaw risk
- PANIC: VIX spike, elevated risk
- BREAKOUT: Major support/resistance breach

### 5. Weekly Report Generator
Comprehensive performance analytics:
- Win rate by regime
- Time-of-day patterns
- Best/worst score ranges
- Feature importance drift over time

## Setup

### Install Dependencies
```bash
pip install scikit-learn joblib
```

The module gracefully degrades if dependencies aren't available.

### Training

```bash
# Train all models from scratch
python3 ml_engine.py --train --verbose

# Generate weekly performance report
python3 ml_engine.py --report

# Evaluate trained models
python3 ml_engine.py --eval
```

Models are saved to `data/models/`:
- `classifier.pkl` — Win/Loss RandomForest
- `regressor.pkl` — Expected Return GradientBoosting
- `scaler.pkl` — Feature scaling
- `features.pkl` — Feature column names
- `metadata.json` — Training info & feature importance

## Integration into bot.py

### Method 1: Direct Score Enhancement
```python
from ml_engine import enhance_score

# In your signal evaluation code:
base_score = calculate_score(symbol, regime)

# Enhance with ML
enhanced = enhance_score({
    "base_score": base_score,
    "symbol": symbol,
    "regime": regime,
    "vix": current_vix,
    "time_of_day": datetime.now().hour,
    "day_of_week": datetime.now().weekday(),
    "is_after_hours": is_after_hours,
    "agents_agreed": agents_count,
    "holding_target": target_holding_minutes
})

final_score = enhanced["enhanced_score"]
ml_confidence = enhanced["ml_confidence"]
ml_details = enhanced["ml_details"]
```

### Method 2: SignalEnhancer Class
```python
from ml_engine import SignalEnhancer

class YourBot:
    def __init__(self):
        self.ml_enhancer = SignalEnhancer()

    def evaluate_signal(self, symbol, base_score, regime, vix, ...):
        result = self.ml_enhancer.enhance_score({
            "base_score": base_score,
            "symbol": symbol,
            "regime": regime,
            "vix": vix,
            # ... other features
        })

        if result["ml_enabled"]:
            log.info(f"{symbol}: ML enhanced score {base_score} → {result['enhanced_score']:.1f}")
            log.info(f"  Win probability: {result['ml_details']['win_probability']:.1%}")
            log.info(f"  Expected return: {result['ml_details']['expected_return']:.2%}")

        return result["enhanced_score"]
```

### Method 3: Full ML Engine Usage
```python
from ml_engine import DeciferML, TradeLabeler, WeeklyReportGenerator

# Periodic training (e.g., weekly)
def retrain_models():
    ml = DeciferML()
    if ml.train():
        log.info("ML models retrained successfully")
        eval_results = ml.evaluate()
        log.info(f"Evaluation: {eval_results}")

# Weekly reporting
def send_weekly_report():
    from learning import send_email  # Your existing email function

    report = WeeklyReportGenerator().generate_report()
    send_email(subject="Weekly ML Analysis", body=report)

# Schedule these
scheduler.add_job(retrain_models, 'cron', day_of_week='sun', hour=0)
scheduler.add_job(send_weekly_report, 'cron', day_of_week='sun', hour=1)
```

## Configuration

Edit `config.py`:
```python
CONFIG = {
    # ... existing config ...

    # ML ENGINE
    "ml_enabled":             True,        # Enable ML enhancements
    "ml_min_trades":          50,          # Min trades before ML active
    "ml_retrain_interval":    168,         # Hours between retraining (7 days)
    "ml_confidence_weight":   0.3,         # How much ML adjusts the base score
}
```

## How It Works

### Training Pipeline
1. **Load trades** from `data/trades.json`
2. **Label outcomes** as WIN/LOSS/BREAKEVEN based on actual P&L
3. **Extract features** at entry time (score, regime, VIX, time, etc.)
4. **Scale features** to standard normal distribution
5. **Train classifier** (RandomForest) to predict win probability
6. **Train regressor** (GradientBoosting) to predict expected return
7. **Cross-validate** with TimeSeriesSplit (no lookahead bias)
8. **Save models** to `data/models/`

### Prediction
When enhancing a signal:
1. Extract features for current setup
2. Pass through trained classifier → win_probability [0, 1]
3. Pass through trained regressor → expected_return [%, float]
4. Calculate confidence as `|2 * win_prob - 1|` (0.5=uncertain, 1.0=certain)
5. Multiply base_score by confidence multiplier: `0.5 + win_prob` (range [0.5, 1.5])
6. Return enhanced_score, clamped to [0, 50]

## Model Performance Metrics

### Classifier (Win/Loss)
- **ROC-AUC**: Area under the receiver-operator curve (0-1, higher=better)
- **Accuracy**: % of trades correctly predicted as win/loss
- **Confusion Matrix**: True positives, false positives, false negatives, true negatives

### Regressor (Return Prediction)
- **R²**: Coefficient of determination (0-1, higher=better)
- **RMSE**: Root mean squared error in return prediction

The model is evaluated only on training data. For robust performance estimates, use the built-in cross-validation scores during training.

## Troubleshooting

### "scikit-learn not installed" warning
Solution: `pip install scikit-learn joblib`

### "Insufficient trades (5 < 50)" message
The module needs minimum 50 trades to train. This is intentional to avoid overfitting. Once you have enough trade data, training will activate automatically.

### Models not loading
Check that `data/models/` directory exists and contains:
- `classifier.pkl`
- `regressor.pkl`
- `features.pkl`
- `metadata.json`

If missing, run `python3 ml_engine.py --train` to retrain.

### Score enhancement not applying
Verify:
1. `config.py` has `"ml_enabled": True`
2. `data/models/` contains trained models
3. Run `python3 ml_engine.py --eval` to verify models load

## Performance Considerations

- **Training**: ~2-5 seconds on 100 trades (single-threaded)
- **Prediction**: <10ms per signal
- **Model size**: ~1MB on disk (pickle format)
- **Memory**: ~50MB when loaded (scales with trade history)

## Example Report Output

```
======================================================================
DECIFER ML WEEKLY PERFORMANCE REPORT
Generated: 2026-03-26 10:15:30
======================================================================

OVERALL PERFORMANCE
----------------------------------------------------------------------
Total Trades:        127
Wins / Losses / BE:  58 / 65 / 4
Win Rate:            45.7%
Total P&L:           $12,345.67
Avg Win / Avg Loss:  $342.18 / $287.50
Profit Factor:       1.64

REGIME-SPECIFIC PERFORMANCE
----------------------------------------------------------------------
BULL_TRENDING       │  42 trades │ Win: 52.4% │ Avg P&L:   $125.45
BEAR_TRENDING       │  31 trades │ Win: 41.9% │ Avg P&L:  -$98.32
CHOPPY              │  34 trades │ Win: 35.3% │ Avg P&L: -$156.78
PANIC               │  20 trades │ Win: 30.0% │ Avg P&L: -$234.56

TIME-OF-DAY PERFORMANCE
----------------------------------------------------------------------
09:00-10:00 │  12 trades │ Win: 58.3% │ Avg P&L:   $178.90
10:00-11:00 │  18 trades │ Win: 50.0% │ Avg P&L:    $92.34
...

SETUP ANALYSIS
----------------------------------------------------------------------
Score  0-10 │   8 trades │ Win: 25.0% │ Avg P&L:  -$234.56
Score 10-20 │  24 trades │ Win: 33.3% │ Avg P&L:  -$145.67
Score 20-30 │  32 trades │ Win: 43.8% │ Avg P&L:    $45.23
Score 30-40 │  38 trades │ Win: 52.6% │ Avg P&L:   $234.56
Score 40-50 │  25 trades │ Win: 64.0% │ Avg P&L:   $567.89

FEATURE IMPORTANCE
----------------------------------------------------------------------
agents_agreed        │ 0.2345
score                │ 0.1876
vix                  │ 0.1523
time_of_day          │ 0.1234
regime_BULL_TRENDING │ 0.1098
...
```

## Notes

- Models are trained on all available historical trades
- Trades with missing or invalid data are skipped
- Time-series cross-validation prevents lookahead bias
- Win probability is predicted on the original feature distribution
- Expected return is in percent (1.0 = 1% gain)
- Score enhancement applies 0.5x to 1.5x multiplier based on win probability

## Support

For issues or questions:
1. Check the logs: `tail -f logs/decifer.log | grep ml_engine`
2. Run with verbose: `python3 ml_engine.py --train --verbose`
3. Review the generated metadata: `cat data/models/metadata.json`
