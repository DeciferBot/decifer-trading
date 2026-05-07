# Intelligence-First Snapshot Contract

**Created:** 2026-05-07
**Sprint:** 7A.4
**Status:** Design / Pre-production — no handoff enabled
**Owner:** Cowork (Claude)
**Approver:** Amit

This document defines the data contract for every versioned snapshot produced by the Intelligence-First architecture, including the live manifest that the trading bot consumes.

---

## 1. Universal Snapshot Schema

Every runtime snapshot produced by any intelligence layer worker must include the following top-level fields.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | Yes | Schema version for this snapshot type (e.g. `"1.0"`) |
| `generated_at` | ISO 8601 UTC | Yes | Timestamp when the snapshot was written |
| `expires_at` | ISO 8601 UTC | Yes | Timestamp after which the snapshot is considered stale |
| `mode` | string | Yes | One of: `production`, `advisory`, `shadow`, `backtest` |
| `data_source_mode` | string | Yes | One of: `live_api`, `cached`, `static`, `backtest_replay` |
| `source_files` | list[string] | Yes | List of input files consumed to build this snapshot |
| `source_snapshot_versions` | dict[str, str] | Yes | Map of input file → `generated_at` timestamp of that input |
| `validation_status` | string | Yes | See Section 2 |
| `validation_errors` | list[string] | Yes | Empty list if valid; error messages if not |
| `warnings` | list[string] | Yes | Non-blocking warnings |
| `record_count` | int | Yes | Number of primary records (candidates, factors, symbols, etc.) |
| `producer` | string | Yes | Name of the worker that produced this file |
| `producer_version` | string | Yes | Version/commit of the producer |
| `freshness_status` | string | Yes | One of: `fresh`, `stale_fallback`, `expired`, `missing_inputs` |
| `no_executable_trade_instructions` | bool | Yes | Must be `true` for all intelligence layer outputs |
| `live_output_changed` | bool | Yes | Must be `false` for all intelligence layer outputs |
| `secrets_exposed` | bool | Yes | Must be `false` for all outputs |
| `env_values_logged` | bool | Yes | Must be `false` for all outputs |

### 1.1 Invariant Enforcement

The Handoff Publisher **must reject** any snapshot where:
- `no_executable_trade_instructions` is not `true`
- `live_output_changed` is not `false`
- `secrets_exposed` is not `false`
- `env_values_logged` is not `false`
- `validation_status` is not `pass`
- `expires_at` is in the past

---

## 2. Snapshot States

The `validation_status` field must be exactly one of:

| Value | Meaning |
|-------|---------|
| `pass` | Schema valid, invariants hold, freshness within SLA, ready for consumption |
| `fail` | Schema violation or invariant breach — must not be consumed |
| `warning` | Valid but non-blocking issues present; may be consumed with caution |
| `stale` | Within acceptable stale window but past primary SLA |
| `missing_inputs` | One or more required upstream inputs were absent or expired |
| `insufficient_data` | Input data was present but below minimum required coverage |

Only snapshots with `validation_status = pass` or `validation_status = warning` (at handoff publisher's discretion) may be referenced by `current_manifest.json`.

---

## 3. Atomic Write Policy

Every worker producing a snapshot file must:

1. Compute the full output in memory
2. Write to `{final_path}.tmp` in the same directory
3. Run `intelligence_schema_validator` on the `.tmp` file
4. If validation **passes**: call `os.replace(tmp_path, final_path)` — atomic on POSIX
5. If validation **fails**:
   - Delete the `.tmp` file
   - Do not overwrite the existing valid file at `final_path`
   - Log a structured error with `validation_errors`
   - Write a separate `{final_path}.fail_{timestamp}.json` with failure details for debugging

No consuming process reads `.tmp` files.

No consuming process reads a file whose modification timestamp is within the current write transaction window (use atomic rename to eliminate this race).

---

## 4. Snapshot Freshness Policy

| Snapshot type | Primary SLA | Stale-but-acceptable window | Expired |
|---------------|-------------|----------------------------|---------|
| Reference data (sector schema, symbol master, theme overlay) | 7 days | 14 days | >14 days |
| Economic context (`current_economic_context.json`) | 1 trading day | 26 hours | >48 hours |
| Company quality snapshot | 7 days | 10 days | >14 days |
| Catalyst / event snapshot | 4 hours (intraday) | 8 hours | >12 hours |
| Technical / market sensor snapshot | 10 minutes | 15 minutes | >20 minutes |
| Active universe snapshot | 10 minutes | 15 minutes | >20 minutes |
| Live manifest (`current_manifest.json`) | 10 minutes | 15 minutes | >20 minutes |

When a snapshot is in the `stale` window (past primary SLA but not expired): the Handoff Publisher may publish a manifest referencing it with `freshness_status = stale_fallback` and a warning logged. It must not use an expired snapshot.

---

## 5. Snapshot Failure Policy

The Handoff Publisher must **fail closed** and must not update `current_manifest.json` if any of the following is true for any required input snapshot:

| Failure condition | Response |
|-------------------|----------|
| Snapshot file is missing | Fail closed. Write `manifest_fail_{ts}.json`. Log `missing_upstream_snapshot`. |
| Snapshot is expired (`expires_at` in past) | Fail closed. Log `expired_upstream_snapshot`. |
| Snapshot `validation_status = fail` | Fail closed. Log `upstream_validation_failed`. |
| Snapshot `schema_version` not recognised | Fail closed. Log `unknown_schema_version`. |
| Snapshot `no_executable_trade_instructions != true` | Fail closed. This is a critical invariant breach. Log `executable_trade_instruction_detected`. Alert immediately. |
| Snapshot contains candidate with `executable = true` | Fail closed. Same as above. |
| Snapshot references an unapproved source | Fail closed. Log `unapproved_source_in_snapshot`. |
| Snapshot `live_output_changed = true` | Fail closed. Critical invariant breach. Alert immediately. |
| Snapshot `secrets_exposed = true` | Fail closed. Critical invariant breach. Alert immediately. |

In all fail-closed cases:
- The **existing** `current_manifest.json` (if valid and not yet expired) remains in place
- The live bot continues using the prior manifest until it also expires
- Once the prior manifest expires, the live bot must degrade gracefully (log, do not halt entirely unless configured to do so)

---

## 6. Live Manifest Contract

### 6.1 File Location

`data/live/current_manifest.json`

Written by `handoff_validator_publisher` only. Written atomically. No other process writes to this path.

### 6.2 Required Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | Yes | `"1.0"` |
| `published_at` | ISO 8601 UTC | Yes | When the manifest was published |
| `expires_at` | ISO 8601 UTC | Yes | When the manifest becomes stale |
| `validation_status` | string | Yes | `pass` / `fail` / `warning` |
| `active_universe_file` | string | Yes | Path to the active universe snapshot |
| `economic_context_file` | string | Yes | Path to the economic context snapshot |
| `company_quality_file` | string | No | Path to company quality snapshot (optional in Phase 1) |
| `catalyst_snapshot_file` | string | No | Path to catalyst snapshot |
| `technical_snapshot_file` | string | No | Path to technical sensor snapshot |
| `risk_snapshot_file` | string | No | Path to risk/execution config snapshot |
| `source_snapshot_versions` | dict | Yes | Map of each referenced file → its `generated_at` |
| `handoff_mode` | string | Yes | `paper` / `live` / `shadow` |
| `handoff_enabled` | bool | Yes | Must match `enable_active_opportunity_universe_handoff` config value |
| `publisher` | string | Yes | `"handoff_validator_publisher"` |
| `fail_closed_reason` | string | No | Populated only when `validation_status = fail` |
| `warnings` | list[string] | Yes | Non-blocking warnings from validation |
| `live_output_changed` | bool | Yes | Must be `false` |
| `secrets_exposed` | bool | Yes | Must be `false` |
| `env_values_logged` | bool | Yes | Must be `false` |

### 6.3 Live Bot Rules for Manifest Consumption

1. The live bot checks `data/live/current_manifest.json` at the start of each scan cycle.
2. If the manifest is **missing**: log `manifest_missing`; degrade gracefully per config.
3. If `expires_at` is in the past: log `manifest_expired`; degrade gracefully.
4. If `validation_status != pass`: log `manifest_invalid`; degrade gracefully.
5. If `handoff_enabled = false`: skip manifest; use legacy scanner-led discovery.
6. If `handoff_enabled = true` but manifest fails any check above: **do not fall back to scanner discovery**; degrade gracefully (hold positions, do not enter new positions).
7. The bot reads **only** files explicitly referenced by `active_universe_file`, `economic_context_file`, etc. It never searches for alternate files.
8. The bot logs `fail_closed_reason` if populated.
9. The bot does not call provider ingestion, LLM, raw news, or broad scan when handoff is enabled.

### 6.4 Minimum Valid Manifest Example

```json
{
  "schema_version": "1.0",
  "published_at": "2026-05-07T14:30:00+00:00",
  "expires_at": "2026-05-07T14:45:00+00:00",
  "validation_status": "pass",
  "active_universe_file": "data/staging/active_universe_20260507T143000.json",
  "economic_context_file": "data/intelligence/current_economic_context.json",
  "company_quality_file": null,
  "catalyst_snapshot_file": "data/staging/catalyst_snapshot_20260507T070000.json",
  "technical_snapshot_file": "data/staging/technical_snapshot_20260507T142500.json",
  "risk_snapshot_file": null,
  "source_snapshot_versions": {
    "data/staging/active_universe_20260507T143000.json": "2026-05-07T14:30:00+00:00",
    "data/intelligence/current_economic_context.json": "2026-05-07T06:00:00+00:00",
    "data/staging/catalyst_snapshot_20260507T070000.json": "2026-05-07T07:00:00+00:00",
    "data/staging/technical_snapshot_20260507T142500.json": "2026-05-07T14:25:00+00:00"
  },
  "handoff_mode": "paper",
  "handoff_enabled": true,
  "publisher": "handoff_validator_publisher",
  "fail_closed_reason": null,
  "warnings": [],
  "live_output_changed": false,
  "secrets_exposed": false,
  "env_values_logged": false
}
```
