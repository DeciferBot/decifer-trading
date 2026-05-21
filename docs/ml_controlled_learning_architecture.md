# Decifer ML — Controlled Learning Architecture

**Date:** 2026-05-20
**Status:** Architecture contract only. No ML code is active.
**Context:** The legacy `ml_engine.py` was deleted in Sprint 1. Leaky saved models were quarantined.
This document defines the replacement architecture. Nothing here is implemented yet.

---

## 1. Architecture Model Answer

**Is the ML system predictive only, self-improving, or controlled self-improving?**

**Controlled self-improving.**

Definition:
- It may continuously collect evidence (passively, as a side-effect of normal trading).
- It may retrain candidate models offline on a schedule or on demand.
- It may compare candidate models against baselines through shadow logging.
- It may NOT automatically promote a model into live scoring influence.
- Live influence requires explicit Amit approval after shadow validation criteria are met.

This is the only safe architecture for a system that generates training data from its own decisions,
because a self-improving loop without a human gate can compound errors faster than it corrects them.

---

## 2. The Twelve Subsystems

### 2.1 Signal Observation

At each scan cycle, every candidate's full signal state is recorded before the trade decision.
Observation record: `{observation_id, scan_id, session_date, symbol, direction, candidate_source,
ranking_position, ranking_total, base_score, signal_scores (dim_* values), regime, vix, timestamp}`.

Stored in `data/ml/ml_observations.jsonl` (not yet implemented).
Key property: observations are written whether or not a trade is taken. This eliminates the
selection bias that plagued the legacy engine's executed-trade-only training set.

### 2.2 Trade Identity Linkage

Every trade entry carries a linkage back to its originating observation:
`{observation_id, scan_id, trade_id}`. This linkage is written to `ORDER_INTENT` in `event_log.py`
at entry time. The join key `observation_id` connects signal observation → decision → execution.

### 2.3 Execution and Position Outcome Capture

`training_store.py` (existing) captures closed-trade outcomes. Schema already includes:
`{trade_id, symbol, direction, trade_type, fill_price, exit_price, pnl, pnl_pct, hold_minutes,
exit_reason, regime, signal_scores, conviction, score, ts_fill, ts_close}`.

`hold_minutes` is stored as an **outcome attribute only**. It is never a model input feature.

### 2.4 Outcome Joining

A periodic offline join links observation records to training_store records via `observation_id`
and `trade_id`. Produces a joined training dataset: signal state at entry + realised outcome.
For candidates where no trade was taken, a "pass" record is written with `trade_taken=False`.
This supports future counterfactual analysis.

### 2.5 Clean Label Creation

Labels are derived from realised pnl_pct after exit confirmation:
- `WIN` = pnl_pct > BREAKEVEN_THRESHOLD (0.1%)
- `LOSS` = pnl_pct < -BREAKEVEN_THRESHOLD
- `BREAKEVEN` = within ±BREAKEVEN_THRESHOLD

For risk-adjusted scoring, a secondary label is: `pnl_pct / max_adverse_excursion`.
Labels are computed offline after the trade closes, not at entry or during the hold.

### 2.6 Dataset Eligibility Rules

A record is **eligible** for training if ALL of the following are true:
- `ml_eligible = True` (or field absent — legacy backwards compat)
- `signal_scores` is not null and not empty
- `direction` is `LONG` or `SHORT` (not `NEUTRAL` or missing)
- `ts_fill` is a parseable timestamp
- `pnl_pct` is not null
- `exit_price` is non-zero
- `trade_type` is not `UNKNOWN`
- `metadata_status` is not `MISSING`
- `trade_id` does not contain `_EXT_`
- `exit_reason` is not `unknown_trade_type`
- `hold_minutes` is stored but NOT added to the feature row

See Section 4 (Dataset Exclusions) for the full exclusion list.

### 2.7 Offline Training

Training runs offline via a CLI script (`python3 scripts/ml_train.py`). Not yet implemented.
The script:
1. Loads eligible records from `training_store.load()` + observation join.
2. Applies eligibility filter.
3. Builds feature matrix (dims, score, vix, time_of_day, day_of_week, regime — no outcome proxies).
4. Trains with `class_weight='balanced'` and `TimeSeriesSplit(n_splits=5)`.
5. Saves model under `data/ml/registry/{model_id}/` (not `data/models/`).
6. Writes evaluation report alongside the model.
7. Never touches `ic_weights.json` or any live config.

### 2.8 Out-of-Sample Evaluation

Every trained model is evaluated on a held-out time window before shadow deployment:
- Walk-forward OOS: last 20% of records by `ts_fill` date.
- Metrics: ROC-AUC, Brier score, precision at top-tercile win_prob, regime-stratified win rate.
- Minimum bar for shadow deployment: Brier score ≤ 0.25 AND top-tercile win rate > 35%.
- Evaluation results written to the model registry entry.
- No model is shadow-deployed without a passing evaluation.

### 2.9 Shadow Comparison

Shadow mode writes an `ml_shadow_predictions.jsonl` log alongside normal scan output:
`{ts, symbol, base_score, win_prob, expected_return, confidence, dim_features_used, model_id}`.
Score is NOT changed. Execution is NOT affected. The log is used to:
- Measure calibration (Brier score over real outcomes).
- Detect regime-specific miscalibration.
- Identify where ML and IC-weighted scores diverge.

Shadow mode is activated by `ml_observer_enabled = True` (config, defaults to False).

### 2.10 Model Registry and Versioning

Candidate models are stored in `data/ml/registry/{model_id}/`:
```
{model_id}/
  classifier.pkl
  regressor.pkl
  features.pkl
  metadata.json     # train_date, n_samples, oos_metrics, feature_schema_version
  eval_report.json  # full OOS evaluation
  status.json       # pending | shadow | approved | retired
```

The registry is append-only. Old models are retired (status = retired), never deleted.
`data/models/` is no longer used as a model path. It was emptied in Sprint 1.

### 2.11 Human-Approved Promotion

Promotion path: `pending` → `shadow` → `approved`.

- `pending` → `shadow`: Automated gate. OOS Brier ≤ 0.25 AND top-tercile win rate > 35%.
  Amit is notified; shadow activates after confirmation.
- `shadow` → `approved`: Manual gate only. Requires:
  1. Minimum 60 days of shadow observation.
  2. Positive win-rate lift vs. baseline in ≥ 2 distinct regime types.
  3. No systematic bias detected (e.g., bull-only lift that collapses in bear regimes).
  4. Explicit Amit approval via a promotion command.
  5. `ml_score_influence_enabled = True` set by Amit in config (not by code).

### 2.12 Rollback and Retirement

If an approved model produces worse outcomes than the IC-weighted baseline over any 30-day window:
- Model status is set to `retired`.
- `ml_score_influence_enabled` is set back to `False`.
- The prior approved model (if any) is reactivated, not re-trained.
- Rollback is always to a known-good prior state, never to a partial state.

Rollback is manual — initiated by Amit. The system does not auto-rollback.

---

## 3. The Ten Architecture Questions

| # | Question | Answer |
|---|----------|--------|
| 1 | Predictive only, self-improving, or controlled self-improving? | **Controlled self-improving** — collects evidence, trains offline, shadow-validates, human-gates promotion |
| 2 | What evidence does it learn from? | Closed trade records with signal_scores + realised pnl_pct, joined to observations of ALL candidates (not just traded ones) |
| 3 | What data is excluded? | Records without signal_scores, without pnl_pct, with UNKNOWN trade_type, EXT orphans, holding_minutes as a feature, any post-outcome data as a feature input |
| 4 | How are labels created? | Offline after exit confirmation from pnl_pct vs BREAKEVEN_THRESHOLD; never at entry time |
| 5 | How is leakage prevented? | Feature matrix excludes all post-outcome fields (holding_minutes, exit_price, pnl, pnl_pct); training only uses data available at entry time |
| 6 | How are candidate models evaluated? | Walk-forward OOS on held-out time window; Brier score + regime-stratified win rate; minimum bar required before shadow |
| 7 | Who or what promotes a model? | Automated gate for shadow deployment; explicit Amit approval for live influence |
| 8 | How is live influence blocked by default? | `ml_score_influence_enabled = False` in config; shadow mode does not change scores; promotion requires config change by Amit |
| 9 | How is rollback handled? | Manual: set model status = retired, set ml_score_influence_enabled = False, reactivate prior approved model |
| 10 | What must be true before ML can influence trades? | (a) OOS Brier ≤ 0.25, (b) 60+ days shadow observation, (c) lift in ≥ 2 regime types, (d) no systematic bias, (e) explicit Amit approval, (f) ml_score_influence_enabled = True set by Amit |

---

## 4. Canonical Learning Evidence Contract

A training record is trusted only if it can link the complete chain:

```
signal observation
  → candidate decision (trade taken or passed)
  → order submitted (or blocked)
  → position opened (fill confirmed)
  → position closed (exit confirmed)
  → realised outcome (pnl_pct, exit_reason, hold_minutes)
```

### 4.1 Complete Record Schema

Each canonical learning record should eventually include:

| Field | Source | Type |
|-------|--------|------|
| `session_date` | scan cycle | str (YYYYMMDD) |
| `scan_id` | scan cycle | str |
| `observation_id` | observation record | str |
| `symbol` | signal | str |
| `direction` | signal | LONG \| SHORT |
| `candidate_source` | handoff reader | str |
| `ranking_position` | scanner ranking | int |
| `ranking_total` | scanner ranking | int |
| `base_score` | IC-weighted scorer | float |
| `signal_scores` | signal engine | dict (dim_* values) |
| `regime` | scanner.get_market_regime() | str |
| `vix` | live_driver_resolver | float |
| `decision` | Apex / AVOID | str |
| `trade_taken` | execution | bool |
| `fill_price` | IBKR fill | float \| null |
| `exit_price` | IBKR exit | float \| null |
| `exit_reason` | exit handler | str \| null |
| `pnl_pct` | training_store | float \| null |
| `hold_minutes` | training_store (OUTCOME ONLY) | float \| null |
| `ml_eligible` | classify_record_quality() | bool |
| `metadata_quality` | classify_record_quality() | str |
| `exclusion_reason` | eligibility filter | str \| null |

**Critical:** `hold_minutes` is stored as an outcome attribute. It MUST NOT appear in
the feature matrix used as model input. This is the root cause of the leakage in the
deleted legacy engine (holding_minutes importance = 0.275, the highest feature).

### 4.2 Dataset Exclusions

Exclude a record from training if ANY of the following is true:

| Exclusion criterion | Reason |
|---------------------|--------|
| `signal_scores` is null or empty | No signal features to learn from |
| `pnl_pct` is null | No outcome to learn toward |
| `ml_eligible = False` | Explicitly marked degraded by classify_record_quality() |
| `trade_type = UNKNOWN` | Metadata loss on restart — outcome is meaningless |
| `metadata_status = MISSING` | EXT orphan — entry context lost |
| `trade_id` contains `_EXT_` | Reconcile-anchored orphan — origin unknown |
| `exit_reason = unknown_trade_type` | Forced exit for metadata failure, not signal failure |
| `direction` not in (LONG, SHORT) | Cannot construct a directional feature vector |
| `exit_price = 0` | Exit not confirmed |
| `ts_fill` not parseable | Cannot determine entry time features |
| Any post-outcome field used as model input | Leakage — see holding_minutes above |
| Observation missing for this trade | Candidate-selection bias cannot be corrected |

---

## 5. Score Influence Design (Stage 3 — not yet approved)

When ML score influence is eventually activated (pending promotion gate + Amit approval),
the adjustment formula must obey these constraints:

1. **Upside only in early stages**: `enhanced_score = base_score + max(0, win_prob - 0.5) * adjustment_factor`
   Scores may not be reduced below `base_score` until 90+ days of shadow validation across
   multiple regimes. The legacy formula (`base_score * (0.5 + win_prob)`) allowed a 0.5× suppression
   which could implicitly block entries — this is prohibited until the model is validated.

2. **Cap at 1.2× base_score**: Enhancement is bounded. A model with win_prob=1.0 may not more
   than double a score.

3. **No overriding min_score_to_trade**: The IC-weighted minimum score threshold is not changed
   by ML. If ML enhancement pushes a score above the threshold, the entry proceeds on its IC merits,
   not ML merits alone.

4. **Transparency**: Every enhanced score is logged with the base_score, win_prob, and model_id.
   Amit can inspect any entry decision and see exactly how much ML influenced the score.

---

## 6. Sprint 2 — Implemented (2026-05-20)

Sprint 2 built the main-frame signal observation writer. All T1–T20 tests pass.

### Attachment point
`signal_pipeline.run_signal_pipeline()` — between step 7 (Signal objects built) and step 8
(signals_log append). The writer receives `all_scored` (ALL scored candidates including
below-threshold), `_rank_map_all`, `_scan_id`, `regime_name`, and `_vix` — exactly the data
available at that point in the pipeline.

### Files created
- `ml_observation_writer.py` — stdlib-only observation writer. Inert unless
  `ml_observer_enabled=True`. No ML imports, no model loading, no score changes.
- `tests/test_ml_observation_writer.py` — T1–T20 proof tests (20 tests, all passing).

### Files modified
- `signal_pipeline.py` — step 7b insertion between steps 7 and 8.
- `signal_dispatcher.py` — `observation_id` and `scan_id` added as top-level kwargs to
  `execute_buy()` and `execute_short()` calls. ORDER_INTENT records now carry `observation_id`
  at the top level (previously nested inside `agent_outputs` only).

### ORDER_INTENT linkage status
- `observation_id` is now at `record["observation_id"]` in ORDER_INTENT (top-level).
- Previously it was only at `record["agent_outputs"]["observation_id"]` (nested).
- Sprint 3 outcome joiner should use the top-level field.
- `order_intent_linked=False` on observation records — Sprint 3 confirms linkage retroactively.

### What Sprint 3 should build
→ **Sprint 3 is now complete. See §7.**

---

## 7. Sprint 3 — Implemented (2026-05-20)

Sprint 3 built the offline outcome joiner. All T1–T20 tests pass.

### Files created
- `scripts/ml_outcome_joiner.py` — stdlib-only offline outcome joiner. Never imported
  by the live bot. Run via `python3 scripts/ml_outcome_joiner.py [--dry-run]`.
- `tests/test_ml_outcome_joiner.py` — T1–T20 proof tests (20 tests, all passing).

### Input files
| File | Role |
|------|------|
| `data/ml/ml_observations.jsonl` | Primary anchor — one record per scored candidate (Sprint 2) |
| `data/trade_events.jsonl` | ORDER_INTENT / ORDER_FILLED / POSITION_CLOSED events |
| `data/training_records.jsonl` | Outcome source (older records, simpler schema) |
| `data/ml/closed_trade_training_ledger.jsonl` | Outcome source (newer, richer schema — takes precedence) |

### Output files
| File | Description |
|------|-------------|
| `data/ml/canonical_learning_dataset.jsonl` | One record per observation, fully joined |
| `data/ml/canonical_learning_dataset_summary.json` | Aggregate counts and distribution statistics |

### Join key hierarchy
1. **Exact join** (`join_quality="exact"`): ORDER_INTENT has `observation_id` matching the
   observation record (top-level field added in Sprint 2). Only exact-join records are
   eligible for ML training.
2. **Fallback join** (`join_quality="fallback"`): No `observation_id` in ORDER_INTENT
   (pre-Sprint 2 records). Matched by symbol + direction + timestamp within ±300 s.
   Fallback-joined records are stored but `ml_eligible=False` — origin unverifiable.
3. **No match** (`join_quality="no_match"`): Pass row — `trade_taken=False`, `outcome_label=None`.

### ml_eligible=True requires ALL
- `observation_id` exists
- `signal_scores` not empty
- `direction` is LONG or SHORT
- `trade_taken=True`, `order_filled=True`, `position_closed=True`
- `realised_pnl_pct` not null
- `join_quality="exact"`

### Outcome labels
`pnl_pct > 0 → WIN` | `pnl_pct < 0 → LOSS` | `pnl_pct == 0.0 → BREAKEVEN`
BREAKEVEN is never WIN. Non-traded pass rows → `outcome_label=None`.

### LEAKAGE_FIELDS (stored but never model inputs)
`hold_minutes`, `exit_price`, `exit_reason`, `realised_pnl`, `realised_pnl_pct`,
`outcome_label`, `position_closed`, `exit_timestamp`.

---

## Training-Readiness Gate (replaces retired 50-trade gate)

**The old 50-trade ML activation gate is retired.** It belonged to `ml_engine.py` which was
deleted in Sprint 3 (ML Clean-Slate Sprint 1). Any reference to "ML engine activation (gate met:
50+ trades)" is incorrect — that gate no longer exists in the codebase.

**ML activation is not yet eligible.** Under the controlled learning architecture, the gate
that must be met before any model training, shadow deployment, or live influence is:

`canonical_learning_dataset.jsonl` must contain **at least 200 `ml_eligible=true` exact
closed-trade records** satisfying ALL of the following conditions:

| Condition | Field / check |
|-----------|--------------|
| Exact identity linkage | `join_quality="exact"` — `observation_id` linked from observation → ORDER_INTENT → closed outcome |
| Trade was executed | `trade_taken=true` |
| Order was filled | `order_filled=true` |
| Position was closed | `position_closed=true` |
| Outcome is known | `realised_pnl_pct` present and not null |
| Regime diversity | At least 2 distinct regimes represented |
| No regime concentration | No single regime above 75% of eligible records |
| Label distribution | WIN / LOSS / BREAKEVEN counts reported and non-degenerate |
| No leakage in features | No `LEAKAGE_FIELDS` in model input feature matrix |
| Source accuracy validated | No `candidate_source="unknown"` records in post-Sprint-3.7 scans |
| Linkage validated end-to-end | `observation_id` chain confirmed: observation → ORDER_INTENT → closed trade |

**Research-only experiments** may be allowed earlier but must be explicitly labelled:
- `research-only` — not production
- not shadow
- not eligible for live influence

No model training. No model loading. No prediction. No advisory scoring. No live trading
behaviour changes until the gate above is met and Amit approves.

---

### Current output (2026-05-21, post-Sprint-3.7)
`ml_observations.jsonl` exists and is actively populated. `ml_observer_enabled=True` in config.
As of 2026-05-21: 4,118 observation records across 92 scan IDs. `schema_version=sprint37_v1`
on all records written after Sprint 3.7 deployment. `candidate_source` will be `"handoff_reader"`
or `"scanner"` on post-Sprint-3.7 records (pre-Sprint-3.6 records show `"unknown"`).

Canonical learning dataset (`canonical_learning_dataset.jsonl`): zero `ml_eligible=true` records.
Exact-joined closed-trade records will accumulate as post-Sprint-3.7 bot cycles produce trades
that carry `observation_id` in ORDER_INTENT and subsequently close with confirmed outcomes.

### What Sprint 4 should build
Shadow logging infrastructure (requires training-readiness gate met first):
1. `ml_shadow_predictions.jsonl` writer — log `win_prob`, `expected_return`, `confidence`,
   `model_id` alongside each scan without changing scores.
2. Activated by `ml_observer_enabled=True` (existing gate) + a candidate model in
   `data/ml/registry/` with `status="shadow"`.
3. Sprint 4 must not change scores, rankings, or execution paths.
4. Brier score tracking for shadow calibration (offline, not runtime).
