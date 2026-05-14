# Trade Data Collection Healthcheck

Generated: 2026-05-14T19:09:23.637766+00:00
Check date: 2026-05-14

## Verdict: ⚠️ DEGRADED

### DEGRADED — Data Quality Issues

- 2 entry snapshots have empty signal_scores
- 14 records in quarantine_missing_entry_snapshot

---

## Today's Pipeline Coverage

| Check | Count |
|-------|-------|
| ORDER_FILLED events today | 0 |
| Entry snapshots written today | 2 |
| POSITION_CLOSED events today | 0 |
| Closed records written today | 0 |
| Filled today with no snapshot | 0 |
| Closed today with no record | 0 |

## Canonical File Totals

| File | Exists | Lines |
|------|--------|-------|
| `data/ml/entry_trade_snapshots.jsonl` | Yes | 2 |
| `data/ml/closed_trade_training_ledger.jsonl` | No | 0 |
| `data/ml/closed_trade_training_ledger.rebuilt.jsonl` | Yes | 416 |

Latest entry snapshot: `2026-05-14T19:05:58.482803+00:00`
Latest closed record:  `none`

## Duplicate Detection

| Check | Count |
|-------|-------|
| Duplicate trade_ids in entry_snapshots | 0 |
| Duplicate trade_ids in closed_ledger | 0 |
| Fresh duplicates in entry_snapshots (today) | 0 |
| Fresh duplicates in closed_ledger (today) | 0 |

## Data Quality

| Check | Count |
|-------|-------|
| Entry snapshots with empty signal_scores | 2 |
| Entry snapshots with regime=UNKNOWN (total) | 0 |
| Entry snapshots with regime=UNKNOWN (today) | 0 |

## Quarantine Files

| File | Total | Today |
|------|-------|-------|
| quarantine_entry_snapshots | 14 | — |
| quarantine_closed_records | 0 | — |
| quarantine_missing_entry_snapshot | 14 | — |
| quarantine_missing_outcome | 0 | — |
| quarantine_schema_invalid | 0 | — |
| quarantine_duplicate_trade_id | 2 | 2 |

## Verdict Rules (for reference)

- **HEALTHY**: All ORDER_FILLED → entry snapshots, all POSITION_CLOSED → closed records, zero duplicate canonical records, zero critical quarantine
- **DEGRADED**: Non-critical quarantine (empty signal_scores, UNKNOWN regime), stale duplicate canonical records, or missing optional fields
- **BROKEN**: ORDER_FILLED today but zero entry snapshots today, OR POSITION_CLOSED today but zero closed records today, OR fresh duplicate canonical records
