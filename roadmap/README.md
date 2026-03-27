# Decifer Roadmap — Bias Removal & Regime Adaptation

> Feature pipeline for removing directional bias and making the system regime-adaptive.
> Origin: Architecture review conversation, 2026-03-26.

---

## The Problem

The 6-agent pipeline has a structural bullish bias. Three root causes:
1. Signal engine scores bullish setups higher than equivalent bearish setups
2. Scanner only feeds long candidates — agents never see short opportunities
3. Consensus threshold (2/6) is too low to filter bad trades

## Features

| # | Feature | Priority | Build Time | Status |
|---|---------|----------|------------|--------|
| 01 | [Direction-Agnostic Signal Engine](01-direction-agnostic-signals.md) | CRITICAL | 3-5 days | Ready |
| 02 | [Short-Candidate Scanner](02-short-candidate-scanner.md) | CRITICAL | 2-3 days | Ready |
| 03 | [HMM Regime Detection](03-hmm-regime-detection.md) | HIGH | 1-2 weeks | Needs Validation |
| 04 | [Mean-Reversion Dimension](04-mean-reversion-dimension.md) | HIGH | 3-5 days | Ready |
| 05 | [Signal Validation (Alphalens)](05-signal-validation.md) | HIGH | 3-5 days | Ready |
| 06 | [Walk-Forward Weight Calibration](06-weight-calibration.md) | HIGH | 1-2 weeks | Blocked |
| 07 | [Directional Skew Tracking](07-directional-skew-tracking.md) | MEDIUM | 1 day | Ready |
| 08 | [Consensus Threshold → 3](08-consensus-threshold.md) | HIGH | 5 minutes | Ready |

## Build Sequence

```
Phase A — Immediate (no dependencies, do first)
├── 08: Raise consensus to 3 .............. [5 min, config change]
├── 02: Short-candidate scanner ........... [2-3 days]
└── 07: Directional skew dashboard ........ [1 day]

Phase B — Core Refactor (in parallel after Phase A)
├── 01: Direction-agnostic signals ........ [3-5 days]
└── 04: Mean-reversion dimension .......... [3-5 days]

Phase C — Validation (needs trade data from Phase A+B)
└── 05: Signal validation / IC analysis ... [3-5 days]
     └── Requires: 200+ trades across regimes

Phase D — Regime Intelligence (needs Phase C results)
├── 03: HMM regime detection .............. [1-2 weeks]
│    └── Requires: 5yr historical data (yfinance, free)
└── 06: Walk-forward weight calibration ... [1-2 weeks]
     └── Requires: 03 (regime probs) + 05 (IC per dimension)
```

## Dependency Graph

```
08 (consensus)  ──→  standalone
02 (short scan) ──→  standalone
07 (skew)       ──→  standalone
01 (agnostic)   ──→  standalone
04 (reversion)  ──→  standalone (but validate after 01)
05 (validation) ──→  needs trade data from 01+02+04
03 (HMM)        ──→  standalone build, but validate with 05
06 (weights)    ──→  needs 03 + 05
```

## Key References

| Topic | Source | Type |
|-------|--------|------|
| Signal validation | Alphalens (alphalens-reloaded) | Python library |
| Regime detection | Ang & Bekaert (2002); hmmlearn | Paper + library |
| Weight optimization | Dynamic Factor Allocation (2024) | arXiv paper |
| Mean reversion | Ernie Chan, "Algorithmic Trading" (2013) | Book |
| Factor importance | SHAP, scikit-learn permutation importance | Python libraries |
| Walk-forward | Pardo, "Evaluation & Optimization of Trading Strategies" | Book |

## How to Use This Directory

- Each `.md` file is a self-contained feature spec with problem, solution, risks, and validation criteria
- When ready to build a feature, move its status to IN PROGRESS
- After building, update the spec with actual implementation notes and validation results
- Promote completed features into `Decifer_Feature_Plan.xlsx` and log decisions in `docs/DECISIONS.md`
- Add new feature ideas as new numbered `.md` files
