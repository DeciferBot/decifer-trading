# Trade Evidence Current Flow Audit

Generated: 2026-05-14
Sprint: Trade Evidence Collection Repair

---

## Purpose

This document maps the 8-stage lifecycle of a Decifer trade and identifies exactly which fields are available at each stage, what evidence was previously missing, and what the sprint fixes.

---

## 8-Stage Trade Lifecycle

### Stage 1 — Candidate Discovery

**Where:** `signal_dispatcher.py` → `score_universe()` → `Signal` object created

**Fields available:**
- `symbol`, `direction`, `score`, `signal_scores`, `score_breakdown`
- `scanner_tier` (D = Position Research Universe, else committed/dynamic)
- `handoff_source_labels` (if from handoff reader)
- `regime` dict (session_character, label, vix_proxy, etc.)

**Evidence written:** None (no persistence at this stage — by design).

**Data quality gap (pre-sprint):** `candidate_source` and `handoff_source_labels` were discarded after dispatch and never stored on the active trade or in any event.

**Fix:** `signal_dispatcher.py` now enriches `agent_outputs` with `candidate_source` and `handoff_source_labels` before calling `execute_buy()` / `execute_short()`.

---

### Stage 2 — Apex Decision (Track A)

**Where:** `apex_orchestrator._run_apex_pipeline()` → `apex_call()` → returns `ApexDecision`

**Fields available:**
- `new_entries[]` with symbol, direction, conviction, entry_thesis, advice_id, score
- `regime_label`, `session_character` (in apex payload context)

**Evidence written:**
- `ORDER_INTENT` event written to `data/trade_events.jsonl` by `event_log.append_intent()` (BEFORE IBKR submission — metadata immutability rule)

**Data quality gap (pre-sprint):** `ORDER_INTENT` existed and was well-populated. No gap here.

---

### Stage 3 — Order Submission

**Where:** `orders_core.execute_buy()` or `execute_short()` or `execute_buy_option()`

**Fields available from active_trade:**
- `trade_id`, `symbol`, `direction`, `instrument`, `trade_type`
- `entry_regime` (structural label string)
- `signal_scores`, `conviction`, `score`, `score_breakdown`
- `agent_outputs` (includes `candidate_source` after Stage 1 fix)
- `entry_context` (TradeContext: sector_etf, catalyst_type, session_character)
- `open_time`, `sl`, `tp`, `atr`, `ic_weights_at_entry`

**Evidence written:**
- `ORDER_INTENT` already written before this point (immutability rule)

**Data quality gap (pre-sprint):** No entry snapshot written at order submission time. TWAP fills write `append_fill()` synchronously but no canonical entry snapshot.

---

### Stage 4 — Fill Confirmation

**Where (TWAP):** `orders_core.execute_buy()` → TWAP executor → after `stats.filled_quantity` confirmed

**Where (bracket):** `fill_watcher.FillWatcher.run()` → `self._is_filled()` returns True

**Where (options):** `orders_options.execute_buy_option()` → after `_save_positions_file()`

**Evidence written (pre-sprint):**
- TWAP: `event_log.append_fill()` ✅ — but no entry snapshot
- Bracket: NOTHING ❌ — no `ORDER_FILLED`, no entry snapshot
- Options: NOTHING ❌ — no entry snapshot

**Evidence written (post-sprint):**
- TWAP: `append_fill()` (existing) + `write_entry_snapshot(..., entry_price_source="twap_fill", fill_confirmed=True)` ✅
- Bracket (primary): `append_fill()` + `write_entry_snapshot(..., entry_price_source="bracket_fill_watcher", fill_confirmed=True)` ✅
- Bracket (late detection): same blocks with `entry_price_source="bracket_fill_watcher_late"` ✅
- Options: `write_entry_snapshot(..., entry_price_source="limit_price_approx_option", fill_confirmed=False)` ✅

**`fill_confirmed=False` for options:** Options use mid_price at order time; actual fill price is reconciled via IBKR separately. The `fill_confirmed` field makes this explicit.

---

### Stage 5 — Position Held

**Where:** In-memory `active_trades` dict

**Fields accumulated:**
- All entry fields above
- `high_water_mark`, `trailing_stop`, `pnl_unrealised` (updated on each scan cycle)
- PM actions from Apex Track B (TRIM/EXIT/HOLD)

**Evidence written:** None (in-memory only — by design; entry snapshot already immutable).

---

### Stage 6 — Position Closed

**Where (primary):** `orders_portfolio._close_position_record()`

**Where (deferred EXITING):** `orders_portfolio._resolve_exiting_positions()`

**Where (direct sell):** `orders_core.execute_sell()`

**Fields available:**
- All entry snapshot fields (via `write_closed_record → _load_entry_snapshot`)
- `exit_price`, `realised_pnl`, `exit_reason`, `hold_minutes`
- `pnl_pct` (computed: realised_pnl / (fill_price × fill_qty))

**Evidence written (pre-sprint):**
- `training_store.append()` written from all three paths ✅
- No canonical closed ledger

**Evidence written (post-sprint):**
- `training_store.append()` (existing — unchanged) ✅
- `write_closed_record(trade_id, ...)` appended at all three paths ✅
- `write_closed_record` loads entry snapshot by trade_id, joins outcome, derives win_loss_label ✅

---

### Stage 7 — ML Training Record Ready

**Where:** `data/ml/closed_trade_training_ledger.jsonl`

**Record structure:**
- Full entry snapshot fields (from Stage 4)
- Full outcome fields (from Stage 6)
- `win_loss_label` (WIN/LOSS/BREAKEVEN)
- `missing_field_flags` (list of fields that were absent at entry time)
- `schema_version`, `ts_written`, `ts_outcome_written`

**Pre-sprint:** ML read from `data/training_records.jsonl` (legacy training_store). 60.4% had empty `signal_scores`, 51.7% had `regime=UNKNOWN`.

**Post-sprint:** `ml_engine.TradeLabeler.load_trades()` and `alpha_validation._load_training()` both try canonical ledger first, fall back to legacy with explicit warning.

---

### Stage 8 — Healthcheck Verification

**Where:** `scripts/trade_data_collection_healthcheck.py` (daily)

**Checks:**
- ORDER_FILLED events today → entry snapshots today (BROKEN if none match)
- POSITION_CLOSED events today → closed records today (BROKEN if none match)
- Duplicate canonical trade_ids (BROKEN if fresh, DEGRADED if stale)
- Schema quality (empty signal_scores, UNKNOWN regime → DEGRADED)
- Quarantine file sizes (DEGRADED if non-zero critical quarantine)

---

## Field Availability by Stage

| Field | Stage 1 | Stage 2 | Stage 3 | Stage 4 | Stage 5 | Stage 6 | Stage 7 |
|-------|---------|---------|---------|---------|---------|---------|---------|
| trade_id | — | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| symbol | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| direction | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| signal_scores | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| regime | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| conviction | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| entry_thesis | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| fill_price | — | — | — | ✅ | ✅ | ✅ | ✅ |
| fill_confirmed | — | — | — | ✅ | ✅ | ✅ | ✅ |
| candidate_source | ✅ | — | ✅* | ✅ | ✅ | ✅ | ✅ |
| session_character | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| exit_price | — | — | — | — | — | ✅ | ✅ |
| realised_pnl | — | — | — | — | ⚠️ | ✅ | ✅ |
| win_loss_label | — | — | — | — | — | derived | ✅ |

*candidate_source added at Stage 1 → Stage 3 transition by signal_dispatcher enrichment (this sprint).

---

## Data Quality Gaps (Pre-Sprint)

| Gap | Root Cause | Impact | Fix |
|-----|-----------|--------|-----|
| 60.4% empty signal_scores | signal_scores not stored on active_trade at close time; UNKNOWN mapped to {} | ML dimension weights have no input signal for these records | Entry snapshot captures signal_scores at fill time; field never lost |
| 51.7% regime=UNKNOWN | regime dict not serialised to structural label at ORDER_INTENT time | IC validation cannot weight by regime; all UNKNOWN records grouped | entry_regime stored as structural string; captured in entry snapshot |
| 6 duplicate trade_ids | training_store.append() called from 3 close paths; no dedup | Training data has repeated outcomes; distorts IC | write_closed_record is idempotent; duplicates → quarantine |
| No candidate_source | Signal.scanner_tier not passed to execute_buy | Cannot measure Tier D ROI vs committed universe | signal_dispatcher enriches agent_outputs; captured in entry snapshot |
| Bracket fills unlogged | FillWatcher had no append_fill or snapshot write | ~80% of fills missing from evidence chain | FillWatcher now writes ORDER_FILLED + entry snapshot |

---

## candidate_source Upstream Trace

| Field | Available? | Source | Plumbing required |
|-------|-----------|--------|-------------------|
| `candidate_source` | Derivable | Signal.scanner_tier ("D" → position_research_universe) + handoff_source_labels | ~4 lines in signal_dispatcher.py before execute_buy |
| `handoff_source_labels` | Yes | Signal.handoff_source_labels (already on Signal) | Same 4-line enrichment block |
| `session_character` | Yes | regime dict on active_trade (already stored) | None — already in entry_context |
| `sector` | Yes | entry_context["sector_etf"] (TradeContext, already on active_trades) | None |
| `catalyst` | Yes | entry_context["catalyst_type"] (already on active_trades) | None |
| `source_mode` | **NO** | Field does not exist in Decifer execution layer. Closest is `data_source_mode` in intelligence layer; not available at order execution time. | N/A — written as "UNKNOWN" in all snapshots with `missing_field_flags` note |

---

## Known Gaps (Documented, Not Fixed in This Sprint)

1. **source_mode** — Field does not exist in the execution layer. `data_source_mode` is a concept in the intelligence pipeline, not at order dispatch. Written as "UNKNOWN" + missing_field_flags.

2. **Options fill_price precision** — Options entry snapshots use `mid_price` at order time (`fill_confirmed=False`). Actual fill price is reconciled via IBKR later. Acceptable for research; marked explicitly in the snapshot.

3. **Historical records (pre-2026-04-28 migration)** — 422 records in `training_records.jsonl` will have no canonical entry snapshot. These are rebuilt via `rebuild_closed_trade_training_ledger.py` into a `.rebuilt` research file, never the canonical ledger. The healthcheck will show these as `quarantine_missing_entry_snapshot` — this is expected and documented.

4. **`time_bucket`** — Not defined as a field in the Decifer system. Written as null / omitted.
