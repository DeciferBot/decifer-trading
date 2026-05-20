# Feature: HMM-Based Regime Detection

**Status:** PHASE B COMPLETE ✅ — HMM advisory active in signal weight router (2026-05-20)
**Scanner-level replacement:** CLOSED — not recommended (see DECISIONS.md 2026-05-20)
**Priority:** No further work required — final architecture is the two-layer design
**Files Modified:** `signals/__init__.py`, `config.py`, `tests/test_hmm_regime.py`

---

## Phase B — Complete (2026-05-20)

Gate met: 406 eligible training records (ml_eligible=True or absent) ≥ 200 threshold.

HMM is active as an advisory 3rd vote in `_resolve_regime_router(vix, hurst, hmm)`. This
determines whether signal dimension weight multipliers favour momentum (1.3×) or mean_reversion
(0.7×) — not structural execution gating. `config["regime_detector"]` remains `"vix_proxy"`.
`scanner.get_market_regime()` is still the structural hard-gating layer. See DECISIONS.md
2026-05-20 for the full architectural rationale.

## Scanner-Level Replacement — Closed (2026-05-20)

The original spec said "HMM replaces VIX-proxy entirely." That directive is superseded.

**The two-layer separation is intentional architecture, not a temporary state:**

- `scanner.get_market_regime()` = structural gating (real-time VIX, intraday flash-crash detection,
  6-state classifier, hard execution blocks). This layer must remain VIX-driven.
- `_resolve_regime_router()` = probabilistic weight routing (daily-bar consensus, 3-signal majority
  vote). This is where HMM belongs.

**Why scanner-level replacement is not the right next step:**
1. Flash-crash latency: HMM uses daily closes; CAPITULATION must fire on intraday VIX spikes.
2. RELIEF_RALLY preservation: 2-state HMM would lose the bear-market-bounce detection that
   drives the 0.5× LONG size cap in `signal_dispatcher.py`.
3. Label continuity: 406 training records carry VIX-proxy `entry_regime` labels. Mid-stream
   switch degrades Phase C/D ML training quality.
4. Signal type mismatch: HMM is a slow probabilistic signal — hard binary execution gating
   misuses it.

This item will not be reopened unless Phase C/D ML analysis shows VIX-proxy `entry_regime`
labels are specifically limiting model quality and HMM labels would resolve it.

---

## Original Spec (preserved for reference)

**Original gate condition (now met and superseded):**
1. `closed_trades >= 200` — need enough IC data across multiple regimes
2. IC Phase 2 review confirms the current VIX-proxy is the limiting factor

~~When the gate is met, HMM **replaces** the VIX-proxy entirely — it does not run alongside it.~~
*(Superseded — see DECISIONS.md 2026-05-20)*

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
