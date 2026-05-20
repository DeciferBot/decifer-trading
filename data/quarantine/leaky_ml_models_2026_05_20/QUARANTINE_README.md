# QUARANTINE — Leaky ML Models (2026-05-20)

## DO NOT USE THESE FILES

These model files were quarantined on 2026-05-20 as part of the Decifer ML Clean-Slate Sprint 1.

## Why they are unsafe

The models in this directory were trained with `holding_minutes` as an input feature.

`holding_minutes` is the **actual trade hold duration** — it is determined by when the trade exits,
which is post-outcome data. A model trained on holding_minutes learns patterns like:
- "trades that were held for 5 minutes tend to be losses"
- "trades that ran for 4 hours tend to be winners"

This is **direct lookahead leakage**. The model is fitting on outcome information, not entry signals.

Evidence: `metadata.json` in this directory shows `holding_minutes` feature importance = **0.275**,
the highest of all features — confirming the model heavily relied on this leaked feature.

## Files in this directory

| File | Contents |
|------|----------|
| `classifier.pkl` | RandomForestClassifier trained with holding_minutes (leaky) |
| `regressor.pkl` | GradientBoostingRegressor trained with holding_minutes (leaky) |
| `scaler.pkl` | StandardScaler fitted on training data including holding_minutes |
| `features.pkl` | Feature name list — includes "holding_minutes" |
| `metadata.json` | Training metadata — shows holding_minutes importance = 0.275 |

## These models must never be used

- Not in production
- Not in shadow mode
- Not in research or backtesting
- Not in tests
- Not loaded by any code path

## What was done

The `ml_engine.py` file that trained and loaded these models was deleted in the same sprint.
The `data/models/` directory is now empty. Any code that tries to load from `data/models/` will
find no files.

Future trained models must be stored in a new path (not `data/models/`) under a versioned registry.
The new ML architecture is documented in `docs/ml_controlled_learning_architecture.md`.

## Quarantine date: 2026-05-20
