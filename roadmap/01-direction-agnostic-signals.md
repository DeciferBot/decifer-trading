# Feature: Direction-Agnostic Signal Engine

**Status:** Research Complete — Ready to Build
**Priority:** CRITICAL — Root cause of bullish bias
**Estimated Build Time:** 3-5 days
**Files to Modify:** `signals.py`
**Dependencies:** None

---

## Problem

The 8-dimension scoring engine in `signals.py` is structurally bullish. The scoring thresholds and dimensions (TREND, MOMENTUM, FLOW, BREAKOUT) are easier to score high on when price is going up. There is no equivalent "breakdown" dimension that rewards clean bearish setups with equal conviction. The signal classification (STRONG_BUY vs STRONG_SELL) has asymmetric requirements in practice even though the thresholds look symmetric on paper — MFI spends more time above 50 in a market that trends up over time.

## Proposed Solution

Separate **conviction** (how strong is this setup?) from **direction** (long or short?).

Each dimension should score the *quality of the setup*, not whether it's bullish. A clean bearish breakdown with volume, bearish EMA alignment, and falling MFI should score identically to the equivalent bullish setup. The total score tells you conviction. A separate field tells you direction.

### Implementation

1. **Refactor `compute_indicators()`** — Each dimension returns `(score, direction)` instead of a single score. Direction is +1 (long), -1 (short), or 0 (neutral).

2. **TREND dimension:** Bull-aligned EMAs score the same as bear-aligned EMAs. Score measures *alignment quality* (how cleanly ordered the EMAs are + ADX strength), direction comes from which way they're aligned.

3. **MOMENTUM dimension:** MFI > 65 scores the same as MFI < 35. Both indicate strong directional pressure. Score = distance from 50 (neutral), direction = which side of 50.

4. **FLOW dimension:** Price > VWAP with rising OBV scores the same as price < VWAP with falling OBV. Both are clean institutional flow signals.

5. **BREAKOUT dimension:** Donchian high break scores the same as Donchian low break. Volume confirmation applies equally to both.

6. **Final signal:** `total_score` = sum of all dimension scores (direction-agnostic). `direction` = majority vote of dimension directions, weighted by score. A stock scoring 35 with direction SHORT is a high-conviction short.

## Risks

- **Overcorrection:** If mean-reversion dimension (see `04-mean-reversion-dimension.md`) is added simultaneously, could flip to bearish bias in certain regimes. Sequence matters — build this first, validate, then add mean-reversion.
- **Backward compatibility:** All downstream consumers (agents, ML engine, dashboard) expect a single score with implied bullish direction. Need to update `agent prompts`, `ml_engine.py` feature extraction, and `dashboard.py` display.
- **Testing:** No way to A/B test on paper simultaneously. Run old engine for 1 week, switch, run new engine for 1 week, compare signal quality.

## Validation

- After refactoring, run Alphalens IC analysis (see `05-signal-validation.md`) on historical data to verify that score predicts returns equally well in both directions.
- Check that the ratio of LONG vs SHORT signals in a flat/choppy market approaches 50/50.
- Check that in a historically bearish period (e.g., 2022), the engine generates more SHORT signals than LONG.

## References

- Current signal classification logic: `signals.py` lines 366-384
- Current dimension scoring: `signals.py:compute_confluence()` lines 433-614
