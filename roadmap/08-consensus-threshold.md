# Feature: Raise Consensus Threshold to 3

**Status:** Ready to Deploy (Config Change)
**Priority:** HIGH — Immediate quality improvement
**Estimated Build Time:** 5 minutes
**Files to Modify:** `config.py`
**Dependencies:** None

---

## Problem

Paper trading consensus threshold is 2 out of 6 agents. This means ANY two agents agreeing is enough to execute a trade. With 5 out of 6 agents biased toward action (only Devil's Advocate is structurally skeptical), getting 2 to agree on a buy is trivially easy. This isn't consensus — it's a rubber stamp.

## Proposed Solution

Raise `agents_required_to_agree` from 2 to 3 for paper trading.

### Why 3, Not 4

- **2 → 4 would halve trade frequency overnight.** The ML engine needs 50 closed trades to activate, and you need trade volume for learning. Going to 4 could starve the system of data.
- **3 is the minimum meaningful threshold.** Requires half the agents to agree — the Opportunity Finder can't just agree with itself and one friendly agent.
- **Preserves data generation** while filtering the worst signals.

### Implementation

In `config.py`:
```python
# Change from:
"agents_required_to_agree": 2,
# To:
"agents_required_to_agree": 3,
```

### Expected Impact

- Trade volume drops ~30-50% (estimate based on how often exactly 2 agents agree vs 3+)
- Win rate should improve (filtered trades were likely the lowest conviction)
- Track: agreement count distribution in `trades.json` to measure actual impact

## Risks

- **Data starvation:** If trade frequency drops below 3-5 per day, ML engine training slows significantly. Monitor for 1 week. If volume is too low, revert.
- **Not the root cause fix:** Raising the threshold treats a symptom (too many bad trades) not the cause (bullish bias in pipeline). Don't rely on this as the primary fix — it's a stopgap while `01-direction-agnostic-signals.md` and `02-short-candidate-scanner.md` are built.

## Validation

- Compare win rate, average return, and Sharpe of trades with 2 agents vs 3+ agents in historical `trades.json` data — this tells you whether the threshold increase actually filters losers.
- After 1 week at threshold 3: is trade quality measurably better? Is volume sufficient for ML training?

## Decision Log Entry

See `docs/DECISIONS.md` — entry added for this change.
