# Closed Trade Training Ledger — Rebuild Report

Generated: 2026-05-31T18:44:45.036230+00:00
Mode: LIVE

## Summary

| Metric | Count |
|--------|-------|
| Records read from training_records.jsonl | 485 |
| Records written to .rebuilt file | 36 |
| Skipped (duplicate trade_id) | 449 |
| Quarantined (blank trade_id) | 0 |
| Quarantined (schema invalid) | 0 |
| Flagged (empty signal_scores — in rebuilt + quarantine) | 5 |
| Records with regime=UNKNOWN (in rebuilt) | 5 |
| Records with canonical entry snapshot available | 18 |

## Output Files

| File | Status |
|------|--------|
| `data/ml/closed_trade_training_ledger.rebuilt.jsonl` | WRITTEN |
| `data/ml/rebuild_quarantine.jsonl` | WRITTEN |

## Notes

- Source file `data/training_records.jsonl` was NOT modified.
- All rebuilt records have `rebuilt_from_legacy=true`.
- Records with `empty_signal_scores` are written to the rebuilt file (flagged) AND quarantine.
- Records with `regime=UNKNOWN` are written to the rebuilt file without quarantine (common in legacy data).
- `candidate_source` is always `UNKNOWN` for legacy records (field did not exist before this sprint).
- This rebuilt file is for research only. The canonical `closed_trade_training_ledger.jsonl` is populated only by live trades after sprint deployment.
