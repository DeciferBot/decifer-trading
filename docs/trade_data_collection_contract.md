# Trade Data Collection Contract

Version: 1.0
Date: 2026-05-14
Sprint: Trade Evidence Collection Repair

---

## Purpose

This document is the canonical schema specification for Decifer's two ML training ledgers. Any code that reads or writes these files must conform to this contract. Schema version bumps require a DECISIONS.md entry and Amit approval.

---

## Canonical Files

| File | Written by | Purpose |
|------|-----------|---------|
| `data/ml/entry_trade_snapshots.jsonl` | `write_entry_snapshot()` | One record per filled trade at entry time. Immutable after write. |
| `data/ml/closed_trade_training_ledger.jsonl` | `write_closed_record()` | One record per closed trade. Built from entry snapshot + outcome join. |
| `data/ml/closed_trade_training_ledger.rebuilt.jsonl` | `rebuild_closed_trade_training_ledger.py` | Research-only rebuild from legacy data. Never used as canonical source. |

**Rule:** No process may write to `closed_trade_training_ledger.jsonl` except `write_closed_record()`. No process may write to `entry_trade_snapshots.jsonl` except `write_entry_snapshot()`.

---

## Entry Snapshot Schema (`entry_trade_snapshots.jsonl`)

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | str | Always "1.0" |
| `trade_id` | str | Unique identifier. UUID format from execute_buy. |
| `symbol` | str | Ticker symbol |
| `direction` | str | "LONG" or "SHORT" |
| `instrument` | str | "stock", "option", "etf" |
| `trade_type` | str | "INTRADAY", "SWING", "POSITION" |
| `fill_price` | float | Actual fill price (or mid_price for options at order time) |
| `fill_qty` | int | Number of shares / contracts filled |
| `entry_price_source` | str | How fill_price was determined (see values below) |
| `fill_confirmed` | bool | True = broker confirmed fill; False = order-time approximation |
| `regime` | str | Structural regime label ("BULL_TRENDING", "BEAR_TRENDING", "CHOPPY", "PANIC", "UNKNOWN") |
| `signal_scores` | dict | 10-dimension signal scores at entry time |
| `conviction` | float | Apex conviction score (0.0–1.0) |
| `score` | float | Total composite score |
| `ts_fill` | str | ISO-8601 timestamp of fill confirmation |
| `ts_written` | str | ISO-8601 timestamp when record was written |

### Optional Fields (always present, may be empty/null)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `intended_price` | float | fill_price | Price intended at order submission |
| `order_id` | int | 0 | IBKR order ID |
| `sl` | float | 0.0 | Stop loss price |
| `tp` | float | 0.0 | Take profit price |
| `score_breakdown` | dict | {} | Per-dimension score components |
| `session_character` | str | "" | Market session context |
| `sector` | str | "" | Sector ETF (from TradeContext) |
| `catalyst` | str | "" | Catalyst type (from TradeContext) |
| `candidate_source` | str | "UNKNOWN" | How the candidate was discovered |
| `handoff_source` | list | [] | Handoff reader labels (if applicable) |
| `source_mode` | str | "UNKNOWN" | Field does not exist in execution layer (N/A) |
| `setup_type` | str | "" | Setup classification |
| `pattern_id` | str | "" | Technical pattern identifier |
| `atr` | float | 0.0 | ATR at entry time |
| `advice_id` | str | "" | Apex advice identifier |
| `entry_thesis` | str | "" | Apex reasoning for entry |
| `ic_weight_snapshot` | dict/null | null | IC dimension weights at entry |
| `entry_context` | dict/null | null | Full TradeContext dict |
| `open_time` | str | "" | Position open timestamp |
| `missing_field_flags` | list | [] | Fields absent or UNKNOWN at entry time |

### `entry_price_source` Values

| Value | Meaning |
|-------|---------|
| `twap_fill` | Fill confirmed by TWAP executor in execute_buy() |
| `bracket_fill_watcher` | Fill confirmed by FillWatcher.run() (primary detection) |
| `bracket_fill_watcher_late` | Fill confirmed by FillWatcher.run() (late detection loop) |
| `limit_price_approx_option` | Mid-price at option order time (fill_confirmed=False) |
| `extended_hours_approx` | Extended hours entry; price may not be confirmed |
| `legacy_training_records` | Rebuilt from legacy data; original source unknown |

### Forbidden Fields (must NEVER appear in entry snapshots)

`exit_price`, `realised_pnl`, `pnl`, `pnl_pct`, `win_loss_label`, `hold_minutes`, `ts_exit`, `ts_outcome_written`

These are exit-time fields. Their presence in an entry snapshot indicates a bug.

---

## Closed Record Schema (`closed_trade_training_ledger.jsonl`)

### All Entry Snapshot Fields (inherited via join)

All required and optional entry snapshot fields are copied into the closed record. The `ts_written` field is refreshed to the close time.

### Additional Required Fields (outcome-only)

| Field | Type | Description |
|-------|------|-------------|
| `exit_price` | float | Price at position close |
| `ts_exit` | str | ISO-8601 timestamp of position close |
| `hold_minutes` | int | Duration of trade in minutes |
| `realised_pnl` | float | Realised profit/loss in dollars |
| `pnl_pct` | float | P&L as fraction of notional (realised_pnl / fill_price × fill_qty) |
| `exit_reason` | str | Why the position was closed (see values below) |
| `win_loss_label` | str | "WIN", "LOSS", or "BREAKEVEN" |
| `ts_outcome_written` | str | ISO-8601 timestamp when outcome was recorded |

### Additional Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `fees` | float/null | Trading fees (null if not captured) |
| `slippage` | float/null | Slippage vs intended price (null if not captured) |
| `outcome_source` | str | Which close path wrote this record |

### `exit_reason` Values

| Value | Source |
|-------|--------|
| `tp_hit` | Take profit order filled |
| `sl_hit` | Stop loss order filled |
| `eod_flat` | End-of-day forced close |
| `intraday_timeout` | 90-minute INTRADAY timeout |
| `apex_exit` | Apex PM Track B EXIT decision |
| `apex_trim` | Apex PM Track B TRIM decision |
| `regime_change` | Thesis validity check failed |
| `manual` | Manual intervention |
| `_close_position_record` | `orders_portfolio._close_position_record()` (direct) |
| `_resolve_exiting_positions` | `orders_portfolio._resolve_exiting_positions()` (deferred) |
| `execute_sell` | `orders_core.execute_sell()` (direct sell) |

### `win_loss_label` Derivation

```
realised_pnl > 0  →  "WIN"
realised_pnl < 0  →  "LOSS"
realised_pnl == 0 →  "BREAKEVEN"
```

---

## Quarantine Files

All quarantine files live in `data/ml/`. They are append-only and never read by ML training pipelines. They are read by the healthcheck script to compute DEGRADED/BROKEN verdicts.

| File | Condition |
|------|-----------|
| `quarantine_entry_snapshots.jsonl` | Entry snapshot with blank trade_id, invalid direction, empty signal_scores, or invalid fill_price. Also: build errors. |
| `quarantine_closed_records.jsonl` | Reserved for future closed-record-specific flagging. |
| `quarantine_missing_entry_snapshot.jsonl` | write_closed_record called for a trade_id with no entry snapshot. Contains outcome context for manual reconstruction. |
| `quarantine_missing_outcome.jsonl` | write_closed_record called with realised_pnl=None. |
| `quarantine_schema_invalid.jsonl` | write_closed_record passed schema validation but was missing required fields. |
| `quarantine_duplicate_trade_id.jsonl` | Duplicate trade_id in either canonical file. Contains `quarantine_reason`: "duplicate_entry_snapshot" or "duplicate_closed_record". |
| `rebuild_quarantine.jsonl` | Rebuild script only: records that could not be mapped from legacy data. |

### Quarantine Semantics

| Condition | Quarantine only | Main + Quarantine |
|-----------|----------------|-------------------|
| blank trade_id | ✅ | |
| invalid direction | ✅ | |
| missing entry snapshot | ✅ | |
| realised_pnl=None | ✅ | |
| schema invalid | ✅ | |
| duplicate trade_id | ✅ | |
| empty signal_scores | | ✅ (flagged, not excluded) |
| invalid fill_price | | ✅ (flagged, not excluded) |
| regime=UNKNOWN | | written to main only (not quarantined — common in legacy) |

---

## Duplicate Policy

Both `write_entry_snapshot()` and `write_closed_record()` are idempotent. Before appending, they scan the canonical file for the trade_id.

- If trade_id already exists: the incoming record goes to `quarantine_duplicate_trade_id.jsonl` with `quarantine_reason` set. The canonical file is unchanged.
- This scan is O(n) at write time. For retail-scale ledgers (hundreds of records/day) this is acceptable and avoids in-memory state that would be lost on process restart.
- The healthcheck reports fresh duplicates (written today) as BROKEN, stale duplicates as DEGRADED.

---

## ML Read Path Priority

Both `ml_engine.TradeLabeler.load_trades()` and `alpha_validation._load_training()` follow this priority:

1. `data/ml/closed_trade_training_ledger.jsonl` — canonical (live trades only, post-sprint)
2. `data/training_records.jsonl` — legacy training_store fallback (with explicit log.warning)

The rebuilt file (`closed_trade_training_ledger.rebuilt.jsonl`) is **never** in the read priority. It is for manual research only.

Every ML report must state which ledger was used and whether it is canonical, rebuilt, or legacy.

---

## Schema Version Policy

- `SCHEMA_VERSION = "1.0"` is written on every record.
- Breaking schema changes (removing required fields, changing field semantics) require:
  1. New `SCHEMA_VERSION` value (e.g. "1.1")
  2. Entry in `docs/DECISIONS.md`
  3. Amit approval
  4. Migration script for existing records (or clear documentation that pre-1.1 records are incompatible)
- Additive changes (new optional fields) do not require a version bump.

---

## Thread Safety

`trade_data_contract.py` uses its own `threading.Lock()` (`_lock`) separate from `event_log._lock`. These are different file families with no shared state. Coupling the locks would introduce a potential deadlock path between the fill confirmation and evidence writing code paths.

---

## Known Limitations

1. **source_mode** — This field does not exist in the Decifer execution layer. The closest concept is `data_source_mode` in the intelligence pipeline, which is not available at order dispatch time. All records write `source_mode="UNKNOWN"` with `"source_mode"` in `missing_field_flags`. This is a documented system gap, not a code defect.

2. **Options fill_price** — `fill_confirmed=False` for all option entries. The mid_price at order time is a reasonable approximation but not broker-confirmed. Actual fill prices can be reconciled via IBKR position history.

3. **Pre-sprint historical records** — 422 records in `training_records.jsonl` predate this sprint. They will show no entry snapshot in the healthcheck. The rebuild script creates `closed_trade_training_ledger.rebuilt.jsonl` for research purposes but this file is never used in production ML pipelines.
