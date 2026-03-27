# Feature: Signal Quality Validation (Alphalens / IC Analysis)

**Status:** Research Complete — Ready to Build
**Priority:** HIGH — Must validate before re-weighting
**Estimated Build Time:** 3-5 days
**Files to Modify:** None (new standalone tool)
**New Files:** `validation.py` or `notebooks/signal_validation.ipynb`
**Dependencies:** Historical trade data OR backtester output (50+ trades minimum)
**New Dependencies:** `pip install alphalens-reloaded` (maintained fork of Quantopian's Alphalens)

---

## Problem

We're about to re-weight signal dimensions by regime (see `06-weight-calibration.md`). But we don't actually know which dimensions predict returns and which are noise. A MOMENTUM score of 8 might be meaningless. Re-weighting noise just rearranges noise. We need to validate each dimension independently before trusting the weights.

## Proposed Solution

Build a signal validation pipeline using Information Coefficient (IC) analysis.

### What IC Measures

IC = Spearman rank correlation between signal value today and actual forward returns (1-day, 5-day, 20-day). It answers: "when this dimension scores high, do returns actually follow?"

- IC > 0.05 = useful signal
- IC 0.03-0.05 = marginal
- IC < 0.03 = noise (consider removing)
- IC should be *stable* — low rolling standard deviation means reliable

### Implementation

1. **Data Collection:** For each historical trade (or backtested signal), record:
   - All 8 (soon 9) dimension scores at time of signal
   - Forward returns at +1h, +1d, +5d, +20d
   - Regime at time of signal

2. **Per-Dimension IC Calculation:**
   ```python
   from scipy.stats import spearmanr
   ic, p_value = spearmanr(dimension_scores, forward_returns)
   ```
   Calculate rolling IC (30-day windows) to check stability.

3. **Alphalens Full Analysis (richer):**
   - Quantile returns: divide signals into 5 buckets, plot average return per bucket. Monotonic increase = good signal.
   - IC decay: calculate IC at t+1, t+5, t+20. How fast does the signal lose predictive power?
   - Turnover: how often does the signal change? High turnover + low IC = unprofitable after costs.

4. **Regime-Conditional IC:**
   - Split analysis by regime (bull/bear/choppy)
   - A dimension might have IC=0.10 in trending markets but IC=0.01 in choppy markets
   - This directly feeds into regime-dependent weights (see `06-weight-calibration.md`)

### Output

A report (or dashboard page) showing:
- IC per dimension (overall and per regime)
- IC stability (rolling std)
- Quantile return plots per dimension
- Recommendation: KEEP / REDUCE WEIGHT / REMOVE for each dimension

## Risks

- **Insufficient data:** Need 200+ observations per regime for meaningful IC. With ~5-10 trades per day on paper, that's 20-40 trading days to accumulate. Can supplement with backtester output.
- **Survivorship bias:** If only measuring IC on candidates that passed the scoring threshold, you're measuring conditional IC. Need to also score (but not trade) candidates that were filtered out, to get unconditional IC.
- **IC is necessary but not sufficient.** A dimension can have positive IC but still lose money after transaction costs if turnover is too high. Always validate IC * turnover metrics.

## Validation

- Run IC analysis on each of the current 8 dimensions
- Compare: which dimensions are actually carrying the alpha vs which are noise?
- Use results to inform initial weight profiles before walk-forward optimization

## References

- Alphalens (maintained fork): https://github.com/stefan-jansen/alphalens-reloaded
- IC methodology: Grinold & Kahn, "Active Portfolio Management" (2000)
- Signal decay research: Di Mascio, Lines, Naik, "Alpha Decay" (SSRN)
- Stefan Jansen, "Machine Learning for Algorithmic Trading" (2020) — Chapter 4 covers factor validation
