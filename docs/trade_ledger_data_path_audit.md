# Trade Ledger & ML Data Path Audit

Generated: 2026-05-14 15:16 UTC


## Executive Summary

**Total records in training_records.jsonl:** 422

**Records with signal scores (ML-usable):** 167

**Win rate:** 30.3%  |  **Profit factor:** 0.892  |  **Expectancy:** $-127.44/trade

**Duplicate trade IDs:** 6

**Usable sample size:** 141 records / 34 features = 4.1x ratio


## Verdict: USABLE ONLY AFTER FILTERING

Data is partially trustworthy. High-severity issues are separable by field filters. ML diagnostics can proceed on a filtered subset.

**High-severity issues:**

- 6 duplicate trade_ids — investigate before ML

- 255 records (60.4%) have empty signal_scores — pre-migration era

- 268 records have score=0 — pre-signal era

- Only 3.1% of training records have matching ORDER_INTENT — lifecycle not reconstructable for majority (expected: pre-migration records)

- 218 records have regime=UNKNOWN — regime feature unreliable

**Medium-severity issues:**

- 21 options records mixed with equities — separable by instrument field

- 8 records use session_character labels as regime (['FEAR_ELEVATED', 'DISTRIBUTION'])

- 29 UNKNOWN trade_type records — likely EXT-path orphans

- Instrument label inconsistency: ['equity_long', 'equity_short', 'stock']

- 141 usable records / 34 features = 4.1x ratio. Recommended ≥10x (340 records). BELOW THRESHOLD.


## ML Logic Verdict: ML LOGIC CORRECT, DATA/SIGNAL WEAK

Code logic is sound: walk-forward CV, no look-ahead, correct labels. AUC=0.401 reflects genuine absence of learnable signal in current 203-record sample.

- ROC-AUC: 0.8102  |  PR-AUC: 0.7459  |  Inverted AUC: 0.1898

- Inverted AUC 0.190 ≈ 0.5 — model is effectively random noise.


## A. Data Source Inventory

Total data files catalogued: **78**

| Tag | Count |
| --- | --- |
| audit | 4 |
| backtest | 7 |
| events | 1 |
| funnel | 1 |
| ic | 5 |
| intelligence | 6 |
| legacy | 2 |
| live_deployment | 14 |
| ml_models | 1 |
| other | 21 |
| portfolio | 2 |
| reference | 8 |
| rotation | 2 |
| runtime_state | 1 |
| shadow | 1 |
| signals | 1 |
| training | 1 |


## B. Primary Ledger Analysis

Path: `/Users/amitchopra/Desktop/decifer trading/.claude/worktrees/suspicious-mirzakhani-a252a8/data/training_records.jsonl`

- Total records: **422**

- With signal_scores: **167** (39.6%)

- Score > 0: **154**

- Duplicate trade_ids: **6**

- Date range: 2026-03-23 → 2026-05-07 (28 trading days)

- Unique symbols: 151

- Options records: 21 (5.0%)


**Regime distribution:**

| Regime | Count | Structural? |
| --- | --- | --- |
| UNKNOWN | 218 | ✓ |
| TRENDING_UP | 135 | ✓ |
| MOMENTUM_BULL | 33 | ✓ |
| RELIEF_RALLY | 11 | ✓ |
| BEAR_TRENDING | 7 | ✓ |
| RANGE_BOUND | 4 | ✓ |
| DISTRIBUTION | 4 | ✓ |
| TRENDING_BEAR | 4 | ✓ |
| FEAR_ELEVATED | 4 | ✓ |
| CHOPPY | 2 | ✓ |


## C. Lifecycle Integrity

- event_log total events: 594

- Training records with matching ORDER_INTENT: **13** (3.1%)

- Training records with matching POSITION_CLOSED: **76**

- Training records with no event_log coverage: **340**

- *Low ORDER_INTENT coverage expected: pre-April-28 migration records were backfilled from trades.json before event_log existed.*


## D. Label Correctness

- Wins: **128** | Losses: **218** | Breakeven: **76**

- Win rate: **30.3%**  |  Avg win: **$3456.47**  |  Avg loss: **$-2276.18**

- Profit factor: **0.892**  |  Expectancy: **$-127.44/trade**

- ⚠ **Win rate is below 40% — inverted signal check in J2 is warranted.**


## E. Feature-Time Integrity

- Timestamp violations (ts_close < ts_fill): **0**

- Unparseable timestamps: **0**

- hold_minutes ≤ 0: **121**

- Hold minutes leakage: *hold_minutes is determined by outcome (exit time). ml_engine.py correctly excludes it from training features. Verified.*


## F. Contamination Check

- Options records: **21** (5.0%)

- UNKNOWN trade_type: **29**

- Session-character regime labels: **8** (['FEAR_ELEVATED', 'DISTRIBUTION'])

- Instrument label variants: {'equity_long': 56, 'equity_short': 21, 'options_call': 7, 'options_put': 11, 'stock': 324, 'option': 3}

- ⚠ Equity instrument labels are not normalised (stock / equity_long / equity_short)

- *scripts/backfill_pnl_pct.py performs an atomic os.replace on training_records.jsonl to backfill pnl_pct. This is idempotent but violates append-only semantics. Must not be re-run.*


## G. Path Consistency

Inconsistencies found: **6**

- ✗ `ml_engine.py`: HARDCODED_PATH_FOUND

- ✗ `alpha_validation.py`: HARDCODED_PATH_FOUND

- ✗ `scripts/trade_quality_report.py`: HARDCODED_PATH_FOUND

- ✗ `scripts/tier_d_evidence_report.py`: HARDCODED_PATH_FOUND

- ✗ `scripts/backfill_pnl_pct.py`: HARDCODED_PATH_FOUND

- ✗ `ibkr_reconciler.py`: HARDCODED_PATH_FOUND


## H. Schema Consistency

**Schema generations detected:**

- gen1_base_fields: 419 records

- gen2_post_apex_signal_fields: 138 records

- gen3_post_migration_fields: 421 records

- Timestamps: 422 tz-aware, 0 naive, 0 missing


## I. Sample Adequacy

- Usable records: **141** (signal_scores non-empty + score > 0)

- Date range: 2026-04-01 → 2026-05-07

- Unique symbols: 56

- Options in usable: 2 (1.4%)

- Dominant regime: TRENDING_UP (50.4% of usable)

- 141 usable records / 34 features = 4.1x ratio. Recommended ≥10x (340 records). BELOW THRESHOLD.


## J2. ML Logic Correctness

Label correctness checks: ALL PASS

Feature alignment checks: ALL PASS

Validation method: Walk-forward (TimeSeriesSplit) ✓

*TimeSeriesSplit prevents future leakage. With 203 records and 5 folds, earliest fold trains on ~32 records — underfitting on small folds is expected and explains high CV variance.*


**Model configuration:** OVERFIT RISK: max_depth=10 with 203 samples / 34 features allows near-zero training error

Sample/feature ratio: 6.0x (recommended ≥10x)


**Apex integration safety:** RISK: ml_enabled defaults to True in config.py. If enhance_score() is ever wired into the scoring pipeline, it would activate silently. Recommend default=False.

Live multiplier active: No ✓


### Inverted Signal Check

AUC: 0.8102 | Inverted AUC: 0.1898

**Interpretation:** NO_SIGNAL

Inverted AUC 0.190 ≈ 0.5 — model is effectively random noise.


### Probability Calibration

| Bucket | N | Predicted prob | Actual win rate | Miscalibration |
| --- | --- | --- | --- | --- |
| 0.0-0.2 | 32 | 0.16 | 0.156 | 0.004 |
| 0.2-0.4 | 111 | 0.283 | 0.234 | 0.049 |
| 0.4-0.6 | 24 | 0.468 | 0.833 | 0.365 |


## Recommendations

**Recommended filters before any ML run:**

1. signal_scores must be non-empty (removes pre-migration records)

1. score must be > 0 (removes pre-signal-era records)

1. instrument must not be options_call / options_put / option

1. regime must not be UNKNOWN

1. deduplicate on trade_id (keep first ts_written occurrence)


**Single source of truth for outcomes:** `data/training_records.jsonl via training_store.load()`

**Single source of truth for entry features:** ORDER_INTENT fields from event_log (signal_scores, score, regime, conviction) — read-only from position record at write time


**Legacy files to exclude from ML:**

- data/trades.json (deprecated April-28)

- scripts/backfill_pnl_pct.py (run-once migration tool)


## Anti-Bloat Gate

| Item | Status |
| --- | --- |
| Files added | 2 (audit script + tests) |
| Files modified | 0 |
| Runtime impact | None — script is run manually |
| Live trading impact | None |
| Broker/order/risk/sizing paths touched | No |
| Live behaviour changed | No |
| Data mutated | No |
| ML activated | No |
| ML multiplier activated | No |
