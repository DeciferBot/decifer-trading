# Feature: HMM-Based Regime Detection

**Status:** DEFERRED — Gate: ≥200 closed trades + IC Phase 2 review (see DECISIONS.md Action #9)
**Priority:** ON HOLD — Do not build until gate is met
**Estimated Build Time:** 1-2 weeks (when gate opens)
**Files to Modify:** `scanner.py`, `bot.py`, `config.py`
**Dependencies:** Enough historical trade data to validate (50+ trades across regimes)
**New Dependencies:** `pip install hmmlearn`

---

## Gate Condition (DO NOT BUILD UNTIL MET)

This feature is locked pending:
1. `closed_trades >= 200` — need enough IC data across multiple regimes to validate any alternative approach
2. IC Phase 2 review confirms the current VIX-proxy is the limiting factor in signal quality (not scoring, execution, or data quality)

When the gate is met, HMM **replaces** the VIX-proxy entirely — it does not run alongside it. Running two regime detectors in parallel produces architectural incoherence.

---

## Problem

Current regime detection is a crude if/else tree based on VIX levels and SPY's position relative to its 20-EMA. This is a lagging indicator — by the time SPY crosses below the EMA and VIX spikes, the move is 60% done. Hard regime labels (BULL_TRENDING, BEAR_TRENDING, CHOPPY, PANIC) cause binary weight switches that are late to every transition and create incoherent portfolio states during regime changes.

## Proposed Solution

Replace the if/else regime classifier with a Hidden Markov Model that outputs **regime probabilities** instead of hard labels. Use these probabilities to smoothly blend dimension weights.

### How HMMs Solve This

Instead of "you are in BEAR_TRENDING" (binary), HMM outputs: "today is 60% bull, 30% choppy, 10% bear." Weights blend proportionally. During transitions, the probabilities shift gradually — no sudden portfolio whipsaw.

### Implementation

1. **Feature vector for HMM:** Daily log returns of SPY, VIX level, VIX term structure (VIX/VIX3M ratio — contango vs backwardation), realized volatility (20-day), SPY-QQQ correlation (risk-on/off proxy)

2. **Model:** `hmmlearn.GaussianHMM` with 3 states (bull, bear, choppy). Fit on 5+ years of daily data (available free via yfinance).

3. **Training:** Fit offline on historical data. Save model parameters. Retrain monthly with new data appended.

4. **Inference:** Each trading day, feed current feature vector → get probability distribution over 3 states. Pass probabilities (not labels) downstream.

5. **Weight blending:** Each regime has a weight profile for the 8 signal dimensions (see `06-weight-calibration.md`). Final weights = weighted average of regime profiles by regime probability.

   Example: 60% bull (momentum-heavy weights) + 30% choppy (mean-reversion-heavy weights) + 10% bear = blended weight vector.

6. **Keep PANIC as a hard override:** VIX > 35 or VIX spike > 20% in 1 hour stays as a hardcoded kill switch. HMM is too slow to catch flash crashes. This is a risk gate, not a regime.

### Key Academic Reference

- **Ang & Bekaert (2002)** — "International Asset Allocation With Regime Shifts." Proved regime-switching strategies outperform static strategies out-of-sample.
- **Hamilton (1989)** — Original regime-switching model paper. Foundation for all subsequent work.

### Python Libraries

- `hmmlearn` — Primary. `GaussianHMM` class. Fit via EM algorithm. Well-maintained, scikit-learn compatible.
- `statsmodels.tsa.regime_switching.MarkovAutoregression` — Alternative. More flexible (time-varying transition probabilities). Better for research, more complex to deploy.

## Risks

- **Lag:** Hamilton filter has 1-2 period lag between actual regime change and detected probability shift. VIX term structure (forward-looking) partially mitigates this — backwardation signals stress before realized vol catches up.
- **Overfitting states:** 3 states is a guess. Could be 2 (risk-on/off) or 4+. Use BIC/AIC to select optimal number of states. Start with 2, test 3 and 4.
- **Label assignment:** HMM states are unlabeled (State 0, State 1, State 2). You need to interpret which state corresponds to "bull" vs "bear" by examining the emission parameters (mean return, volatility). This is manual and could be wrong.
- **Non-stationarity:** Market dynamics change over decades. A model trained on 2015-2025 may not capture 2025-2035 dynamics. Monthly retraining with expanding window mitigates but doesn't solve.

## Validation

- Backtest: label historical periods with HMM, compare against known regimes (2020 crash, 2022 bear, 2023-2024 bull). Do the labels make sense?
- Out-of-sample: train on 2015-2023, test regime predictions on 2024-2025.
- Measure: does HMM-weighted scoring produce better signal IC than current if/else regime scoring?

## References

- Current regime detection: `scanner.py:get_market_regime()`, `bot.py`
- Current regime thresholds: `config.py`, `signals.py` lines 631-641
- Ang & Bekaert (2002): https://doi.org/10.1093/rfs/15.4.1137
- hmmlearn docs: https://hmmlearn.readthedocs.io/
