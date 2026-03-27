# Feature: Directional Skew Tracking

**Status:** Research Complete — Ready to Build
**Priority:** MEDIUM — Diagnostic tool, not a trading signal
**Estimated Build Time:** 1 day
**Files to Modify:** `dashboard.py`, `learning.py`
**Dependencies:** None

---

## Problem

The system has no visibility into its own directional bias. If 95% of trades over the last 2 weeks are LONG in a flat market, that's evidence the pipeline is broken. Currently there's no metric tracking this.

## Proposed Solution

Track and display directional skew as a **dashboard metric and alert**, NOT as a feedback loop into agent prompts.

### Why NOT a Feedback Loop

Feeding skew back into agents ("you've been 80% long, correct yourselves") would create forced short trades to balance a statistic, not because the setup is good. The market IS long-biased over time — forcing 50/50 directionality fights the base rate. Skew tracking is a diagnostic, not a signal.

### Implementation

1. **Metric calculation (in `learning.py`):**
   ```python
   def get_directional_skew(window_hours=48):
       recent_trades = [t for t in trades if t['entry_time'] > now - timedelta(hours=window_hours)]
       long_count = sum(1 for t in recent_trades if t['direction'] == 'LONG')
       short_count = sum(1 for t in recent_trades if t['direction'] == 'SHORT')
       total = long_count + short_count
       if total == 0:
           return 0.0
       return (long_count - short_count) / total  # -1.0 (all short) to +1.0 (all long)
   ```

2. **Dashboard widget (in `dashboard.py`):**
   - Show skew as a gauge: -1.0 to +1.0
   - Color coding: GREEN if aligned with regime (long skew in bull, short skew in bear). YELLOW if neutral. RED if misaligned (heavy long in bear/choppy, heavy short in bull).

3. **Alert thresholds:**
   - Skew > 0.8 in CHOPPY or BEAR regime → log warning
   - Skew < -0.8 in BULL regime → log warning
   - These are diagnostic alerts for human review, not automatic overrides

4. **Attribution tracking:**
   - Log skew alongside trade outcomes in `trades.json`
   - Weekly review (`learning.py`) includes skew analysis: "This week: 85% LONG, regime was CHOPPY for 60% of trading hours. Consider: is the short-candidate pipeline producing enough candidates?"

## Risks

- **Over-reacting to skew:** A human seeing "90% long" might panic and start forcing shorts manually. The metric should always be shown alongside regime context. 90% long in a BULL_TRENDING regime is correct behavior, not a bug.
- **Window length sensitivity:** 48-hour window might be too short (10 trades) or too long (100 trades). Make configurable. Start with 48 hours and 1-week views side by side.

## Validation

- After deploying short-candidate scanner (`02-short-candidate-scanner.md`), skew should naturally decrease in non-bull regimes. If it doesn't, the scanner isn't working.
- Track skew vs regime over time. The correlation should be positive (more long in bull, more short in bear).
