# Feature: Walk-Forward Regime-Conditional Weight Calibration

**Status:** Research Complete — Blocked on Prerequisites
**Priority:** HIGH — But must complete 03, 05 first
**Estimated Build Time:** 1-2 weeks
**Files to Modify:** `signals.py`, `config.py`
**New Files:** `weight_optimizer.py`
**Dependencies:**
- `03-hmm-regime-detection.md` — Need regime probabilities
- `05-signal-validation.md` — Need IC data per dimension per regime
- 200+ historical trades segmented by regime (from backtester or paper trading)
**New Dependencies:** `pip install riskfolio-lib` or use existing `scikit-learn`

---

## Problem

The idea: regime determines the weight of each scoring dimension. Momentum gets heavy weight in trending markets, mean-reversion gets heavy weight in choppy markets. Sound in principle, but the weight profiles can't come from intuition — "in a choppy market, SQUEEZE gets 3x weight" is just a guess. Need data-driven weights.

## Proposed Solution

Walk-forward optimization of dimension weights, conditioned on regime.

### How It Works

1. **Training window** (6 months of trades/signals):
   - Group by HMM regime probabilities
   - For each regime bucket: find the weight vector for the 9 dimensions that maximizes Sharpe ratio of resulting signals
   - Method: scikit-learn permutation importance on XGBoost, or direct optimization via `scipy.optimize.minimize`

2. **Test window** (1 month forward):
   - Apply learned weights to new signals
   - Measure out-of-sample IC, Sharpe, win rate
   - If out-of-sample degradation > 50% of in-sample, weights are overfit — widen training window or reduce degrees of freedom

3. **Roll forward:** Repeat monthly. Weights update as market structure evolves.

4. **Blending with regime probabilities:**
   ```
   final_weights = P(bull) * bull_weights + P(bear) * bear_weights + P(choppy) * choppy_weights
   ```
   Where P(regime) comes from HMM (see `03-hmm-regime-detection.md`).

### Simpler Alternative (Start Here)

Before building full walk-forward optimization:

1. Run Alphalens IC analysis (see `05-signal-validation.md`) per dimension per regime
2. Weight each dimension proportional to its IC in that regime
3. Normalize so weights sum to 1.0
4. This is a data-driven starting point, not optimal but principled

### Python Libraries

- **Riskfolio-Lib:** Full portfolio optimization with regime conditioning. Overkill for signal weights but could be useful later for position sizing.
- **scikit-learn:** `permutation_importance()` — shuffle each dimension, measure performance drop. Direct measure of importance per regime.
- **SHAP:** Game-theoretic feature importance. More expensive computationally but captures interactions between dimensions.
- **XGBoost:** Train model to predict trade outcome from dimension scores. Extract `feature_importances_` (gain-based). Already partially built in `ml_engine.py`.

### Key Paper

"Dynamic Factor Allocation Leveraging Regime-Switching Signals" (2024): https://arxiv.org/html/2410.14841v1

Shows that ML-based factor weight allocation improves information ratios from 0.05 (equal-weight) to ~0.40 (dynamic allocation).

## Risks

- **Overfitting:** The #1 risk. Walk-forward with 6-month window on 9 dimensions = ~180 daily observations to fit 9+ parameters per regime. Borderline. Use regularization (L1/L2 on weights) or constrain weights to [0.5, 2.0] range around equal-weight.
- **Regime mislabeling:** If HMM assigns wrong regime probabilities, weight calibration trains on wrong labels. Garbage in, garbage out. Validate HMM first (see `03-hmm-regime-detection.md`).
- **Non-stationarity:** Weight profiles that worked in 2024 may not work in 2026. Monthly retraining with expanding window mitigates. Monitor out-of-sample degradation — if consistently > 40%, the relationship may be unstable.
- **Complexity budget:** Walk-forward optimization + HMM + 9 dimensions = a lot of moving parts. Each adds fragility. Start with the simpler IC-proportional weights before attempting full optimization.

## Validation

- Compare walk-forward optimized weights vs equal weights vs IC-proportional weights on out-of-sample data
- Monitor weight stability: do weights change dramatically month to month? If yes, they're fitting noise.
- Track: does the blended-weight scoring produce better trade outcomes than flat-weight scoring?

## Sequencing

1. Build signal validation (`05-signal-validation.md`) — learn which dimensions matter
2. Build HMM regime detection (`03-hmm-regime-detection.md`) — get regime probabilities
3. Start with IC-proportional weights (simple, data-driven)
4. Graduate to walk-forward optimization once you have 6+ months of regime-labeled trade data

## References

- Walk-forward optimization: Pardo, "The Evaluation and Optimization of Trading Strategies" (2008)
- Feature importance: https://scikit-learn.org/stable/modules/permutation_importance.html
- SHAP: https://shap.readthedocs.io/
- Dynamic allocation paper: https://arxiv.org/html/2410.14841v1
- Riskfolio-Lib: https://github.com/dcajasn/Riskfolio-Lib
