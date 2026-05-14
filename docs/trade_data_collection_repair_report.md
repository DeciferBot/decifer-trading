# Trade Data Collection Repair Report

## Status: COMPLETE ✅

Completed: 2026-05-14

---

## Protected File Touch Map

This section must be reviewed and approved before any protected file is edited.

A "protected file" is any file that participates in: order execution, position sizing, risk management, stop logic, portfolio management, or broker communication.

For each touch point, the following questions are answered:
1. Exact function to be touched
2. Exact reason it must be touched
3. Whether the change is append-only evidence capture
4. Why it cannot be done from a safer outer layer
5. Test that proves execution behaviour is unchanged

---

### Touch 1 — `fill_watcher.py` · `FillWatcher.run()` · lines 157–161

**Exact function:** `FillWatcher.run()` — the inner watch loop, at the point where `self._is_filled()` returns True (line 157), after `self._remove_from_registry()` (line 160), before `return` (line 161).

**Exact reason it must be touched:** Bracket orders (the standard entry path for all equity longs and shorts) have no `ORDER_FILLED` event written anywhere in the current system. `orders_core.execute_buy()` places the bracket and returns `True` immediately — IBKR fill confirmation arrives asynchronously. `FillWatcher.run()` is the only code that detects this fill. Without touching it, bracket fills produce no entry snapshot and no `ORDER_FILLED` event in `trade_events.jsonl`. This is the primary evidence gap.

**Is the change append-only evidence capture?** Yes. Two try/except blocks are appended after line 160:
1. `event_log.append_fill(...)` — writes one `ORDER_FILLED` line to `data/trade_events.jsonl`
2. `trade_data_contract.write_entry_snapshot(...)` — writes one entry snapshot line to `data/ml/entry_trade_snapshots.jsonl`

Neither block modifies `active_trades`, changes order state, touches IBKR, or alters what `run()` returns (it returns `None`).

**Why it cannot be done from a safer outer layer:** The outer layer (`execute_buy`) returns before the fill is confirmed. The fill watcher runs in a background thread spawned by execute_buy. There is no callback or hook in execute_buy that fires on fill confirmation — the watcher is autonomous. The only place fill confirmation is known with certainty for bracket orders is inside this method.

**Exact insertion point (verified from source):**
```
# line 157: if self._is_filled():
# line 158:     self._log_audit(...)
# line 159:     log.info(...)
# line 160:     self._remove_from_registry()
# INSERT HERE — two try/except blocks
# line 161:     return
```

**Available variables at insertion point:**
- `self._symbol` — ticker
- `self._order_id` — IBKR order ID of the bracket entry leg
- `active_trades` — imported lazily inside `_is_filled()` from `orders_state`; must be re-imported
- `_trades_lock` — same lazy import pattern

**Test that proves execution behaviour unchanged:**
```
test_fill_watcher_snapshot_failure_does_not_abort_fill()
  — mock write_entry_snapshot to raise; verify FillWatcher.run() still calls
    self._remove_from_registry() and returns normally
test_fill_watcher_calls_snapshot_on_fill()
  — mock write_entry_snapshot; verify it is called once with correct trade_id
    and symbol when fill is detected
```
These tests mock the new call and verify `run()` return is unaffected.

---

### Touch 2 — `orders_core.py` · `execute_buy()` TWAP path · after line 826

**Exact function:** `execute_buy()` — the TWAP smart execution branch, immediately after the existing `append_fill` try/except block (line 819–826), before `_save_positions_file()` (line 827).

**Exact reason it must be touched:** The TWAP fill path is the only synchronous fill confirmation in `execute_buy()`. At this point, `_fill_price`, `stats.filled_quantity`, `_trade_id`, and `active_trades[symbol]` are all populated and correct. This is the only path in the codebase where a TWAP entry's fill price and confirmed quantity are available together with the full signal context.

**Is the change append-only evidence capture?** Yes. One try/except block is inserted:
```python
try:
    from trade_data_contract import write_entry_snapshot as _wes
    with _trades_lock:
        _twap_snap = dict(active_trades.get(symbol, {}))
    _wes(trade_id=_trade_id, active_trade_copy=_twap_snap,
         fill_price=_fill_price, fill_qty=int(stats.filled_quantity),
         entry_price_source="twap_fill", fill_confirmed=True,
         order_id=int(_result.get("order_id") or 0))
except Exception as _snap_err:
    log.warning("execute_buy %s: entry snapshot write failed (non-fatal): %s", symbol, _snap_err)
```
This does not alter `active_trades`, does not affect IBKR, does not change the return value of `execute_buy()`.

**Why it cannot be done from a safer outer layer:** The TWAP executor result (`_fill_price`, `stats.filled_quantity`) is local to this function. It is not stored on `active_trades` directly — `active_trades[symbol]["entry"]` holds the fill price, but only after the `execute_buy` internal assignment at line ~757–802. A safer outer layer has no way to know when a TWAP fill completes without polling active_trades, which would introduce timing races.

**Exact insertion point (verified from source):**
```
# line 819: try:
# line 820:     from event_log import append_fill as _el_fill
# line 821:     _el_fill(_trade_id, symbol, fill_price=_fill_price, ...)
# line 825: except Exception as _elf_err:
# line 826:     log.warning(...)
# INSERT HERE — one try/except block
# line 827: _save_positions_file()
```

**Test that proves execution behaviour unchanged:**
```
test_execute_buy_twap_snapshot_failure_does_not_change_return_value()
  — mock write_entry_snapshot to raise; verify execute_buy() still returns True
test_execute_buy_twap_snapshot_called_once()
  — mock write_entry_snapshot; verify called once with fill_price and trade_id
```

---

### Touch 3 — `orders_core.py` · `execute_sell()` · after line 2550

**Exact function:** `execute_sell()` — after the existing `training_store.append` try/except block (lines 2518–2550), before `with _trades_lock:` (line 2551).

**Exact reason it must be touched:** `execute_sell()` is one of three close paths that write training records. It already writes to `training_store` at line 2523. The closed canonical ledger write must be co-located with the training_store write so both happen in the same outcome-confirmed context, with the same variables (`_close_trade_id`, `_exit_price_val`, `pnl`, `reason`, `_hold_mins`). The `write_closed_record` call needs `trade_id` to look up the entry snapshot — these variables are only available here.

**Is the change append-only evidence capture?** Yes. One try/except block after line 2550:
```python
try:
    from trade_data_contract import write_closed_record as _wcr_sell
    _wcr_sell(trade_id=_close_trade_id, exit_price=_exit_price_val,
               realised_pnl=pnl, exit_reason=reason, hold_minutes=_hold_mins,
               outcome_source="execute_sell")
except Exception as _wcr_sell_err:
    log.warning("execute_sell %s: closed ledger write failed (non-fatal): %s", symbol, _wcr_sell_err)
```
No change to `active_trades`, IBKR, risk logic, stop logic, or return value.

**Why it cannot be done from a safer outer layer:** `execute_sell()` is called by both direct position-close commands and by the PM exit dispatcher. The callers do not have access to the confirmed exit price or realised P&L — these are local to `execute_sell()`.

**Exact insertion point (verified from source):** After line 2550 (`log.warning("execute_sell %s: training_store write failed..."`), before line 2551 (`with _trades_lock:`).

**Test that proves execution behaviour unchanged:**
```
test_execute_sell_closed_record_failure_does_not_abort_sell()
  — mock write_closed_record to raise; verify execute_sell() still returns True and position is removed from active_trades
```

---

### Touch 4 — `orders_portfolio.py` · `_close_position_record()` · after line 260

**Exact function:** `_close_position_record()` — the single canonical exit point for all non-execute_sell close paths. After the `training_store` try/except block (lines 232–260), before `with _trades_lock: active_trades.pop(key, None)` (line 261).

**Exact reason it must be touched:** This function is the authoritative close path for PM exits, dashboard manual closes, and TP/SL fills. It already writes to `training_store` (line 238). The canonical closed ledger write must happen in the same window, before the position is removed from `active_trades`. Variables `_tid`, `exit_price`, `pnl`, `exit_reason`, `hold_minutes` are all available here.

**Is the change append-only evidence capture?** Yes. One try/except block (8 lines) inserted after line 260.

**Why it cannot be done from a safer outer layer:** The callers of `_close_position_record()` are spread across the portfolio and trading modules. Instrumenting every caller would require more diffs, not fewer. This function is already the consolidated exit point — adding to it keeps the evidence write co-located with the existing training_store write.

**Exact insertion point (verified from source):** After line 260 (`log.warning("_close_position_record: training_store write failed...")`), before line 261 (`with _trades_lock:`).

**Test that proves execution behaviour unchanged:**
```
test_close_position_record_closed_ledger_failure_does_not_prevent_position_removal()
  — mock write_closed_record to raise; verify active_trades.pop() is still called
    and _save_positions_file() still runs
```

---

### Touch 5 — `orders_portfolio.py` · `_resolve_exiting_positions()` · after line 1827

**Exact function:** `_resolve_exiting_positions()` — the deferred close path for positions stuck in `EXITING` status. After the deferred `training_store` try/except block (lines 1798–1827), before `with _trades_lock: recently_closed...` (line 1839).

**Exact reason it must be touched:** This is the second close path in `orders_portfolio.py` that writes to `training_store` independently of `_close_position_record()`. Without adding the closed ledger write here, any trade that closes via the deferred EXITING path will have a training_store record but no canonical closed ledger record.

**Is the change append-only evidence capture?** Yes. One try/except block after line 1827 using `_trade_id`, `_exit_px`, `_pnl`, `_exit_reason` variables confirmed available in scope.

**Why it cannot be done from a safer outer layer:** The deferred close path bypasses `_close_position_record()` by design (it already has confirmed exit data from IBKR reconciliation). There is no shared exit hook.

**Exact insertion point (verified from source):** After line 1827 (`log.warning("Deferred CLOSE training_store write failed...")`), before line 1828 (`try: from learning import log_trade...`).

**Test that proves execution behaviour unchanged:**
```
test_resolve_exiting_positions_closed_record_failure_does_not_prevent_position_cleanup()
  — mock write_closed_record to raise; verify active_trades.pop() and recently_closed update still execute
```

---

### Touch 6 — `orders_options.py` · `execute_buy_option()` · after line 346

**Exact function:** `execute_buy_option()` — after `_save_positions_file()` at line 346, before the function's `return True` or next logical block.

**Exact reason it must be touched:** Options trades are the only entry type with no fill confirmation at order time. The entry snapshot must be written at the closest available point (order acceptance), with `fill_confirmed=False` and `entry_price_source="limit_price_approx_option"` to make the imprecision explicit. Without this, options trades have no entry snapshot at all.

**Is the change append-only evidence capture?** Yes. One try/except block after line 346. `active_trades[opt_key]` is populated at line 309 and available. `mid_price`, `n_contracts`, `trade.order.orderId`, `_trade_id_opt` are all in local scope.

**Why it cannot be done from a safer outer layer:** The options fill watcher / reconciler path does not have access to the full signal context (signal_scores, conviction, regime). That context only exists as local variables inside `execute_buy_option()` at order placement time.

**Exact insertion point (verified from source):** After line 346 (`_save_positions_file()`).

**Test that proves execution behaviour unchanged:**
```
test_execute_buy_option_snapshot_failure_does_not_abort_option_entry()
  — mock write_entry_snapshot to raise; verify execute_buy_option() still returns True
```

---

### Touch 7 — `signal_dispatcher.py` · `dispatch_signals()` · before line 526

**Exact function:** `dispatch_signals()` — immediately before the `execute_buy(...)` call at line 526, inside the `if signal.direction == "LONG"` block.

**Exact reason it must be touched:** `candidate_source` (which scan tier/source produced this candidate) is derivable from `signal.scanner_tier` and `signal.handoff_source_labels` — both are attributes on the `Signal` object, which is in scope here. These fields are not currently passed into `agent_outputs` and therefore do not appear in `active_trades` or `ORDER_INTENT`. Without this 4-line addition, every entry snapshot will have `candidate_source=UNKNOWN` permanently.

**Is the change append-only evidence capture?** Yes. Creates a shallow dict copy of `agent_outputs`, adds two fields, passes the enriched dict to `execute_buy()` instead of the original. The original `agent_outputs` parameter is not mutated. `execute_buy()` receives the same dict shape it always has — just with two additional keys it already handles via `**kwargs` passthrough.

**Why it cannot be done from a safer outer layer:** `signal.scanner_tier` and `signal.handoff_source_labels` are only available on the `Signal` object, which is a local variable at the call site. Outer layers (bot_trading.py, apex_orchestrator.py) do not have direct access to individual Signal objects at the time of order execution.

**Exact insertion point:** Lines 525–526 in signal_dispatcher.py:
```python
# Before:
if execute:
    success = execute_buy(
        ...
        agent_outputs=agent_outputs,
        ...
    )

# After (4-line additive change):
if execute:
    _eo = dict(agent_outputs or {})
    _eo["candidate_source"] = (
        "position_research_universe" if getattr(signal, "scanner_tier", "") == "D"
        else ("handoff_reader" if getattr(signal, "handoff_source_labels", None)
              else "legacy_scanner")
    )
    _eo["handoff_source_labels"] = getattr(signal, "handoff_source_labels", None) or []
    success = execute_buy(
        ...
        agent_outputs=_eo,
        ...
    )
```

Same pattern must be applied to the `execute_short()` call site in the same function for SHORT signals.

**Test that proves execution behaviour unchanged:**
```
test_dispatch_signals_candidate_source_enrichment_does_not_change_order_submission()
  — verify execute_buy is called with same symbol, price, score, regime
    regardless of whether candidate_source is added to agent_outputs
test_dispatch_signals_scanner_tier_d_maps_to_position_research_universe()
  — mock signal.scanner_tier = "D"; verify candidate_source == "position_research_universe"
test_dispatch_signals_handoff_labels_maps_to_handoff_reader()
  — mock signal.handoff_source_labels = ["rule_x"]; verify candidate_source == "handoff_reader"
```

---

### Touch 8 — `config.py` · ml_enabled default

**Exact change:** `"ml_enabled": True` → `"ml_enabled": False`. Add four new keys:
- `"ml_live_multiplier_enabled": False`
- `"ml_can_block_entries": False`
- `"ml_can_size_positions": False`
- `"ml_data_dir": "data/ml"`

**Exact reason:** `enhance_score()` is never called in production. `ml_enabled=True` is a stale legacy setting that creates a false impression that ML is active. The four new keys make the safe-disabled posture explicit and testable.

**Is the change append-only evidence capture?** No — this is a config default change. But it has zero runtime effect because the config flag gates code that is never invoked.

**Why it cannot be done from a safer outer layer:** Config defaults live in config.py. This is the authoritative definition of `ml_enabled`.

**Test that proves execution behaviour unchanged:**
```
test_ml_enabled_false_by_default()
  — assert CONFIG.get("ml_enabled", True) is False after config loads
test_ml_multiplier_flags_false_by_default()
  — assert all four safety flags are False
test_enhance_score_not_called_in_production_paths()
  — grep/import check: no live trading module calls enhance_score()
```

---

### Summary Table

| File | Function | Lines | Change Type | Trading Logic Changed? | Return Value Changed? |
|------|----------|-------|------------|----------------------|----------------------|
| `fill_watcher.py` | `FillWatcher.run()` | After 160, before 161 | Append-only try/except | No | No (returns None) |
| `orders_core.py` | `execute_buy()` TWAP | After 826, before 827 | Append-only try/except | No | No |
| `orders_core.py` | `execute_sell()` | After 2550, before 2551 | Append-only try/except | No | No |
| `orders_portfolio.py` | `_close_position_record()` | After 260, before 261 | Append-only try/except | No | No (returns None) |
| `orders_portfolio.py` | `_resolve_exiting_positions()` | After 1827, before 1828 | Append-only try/except | No | No |
| `orders_options.py` | `execute_buy_option()` | After 346 | Append-only try/except | No | No |
| `signal_dispatcher.py` | `dispatch_signals()` | Before 526 | Additive dict enrichment | No | No |
| `config.py` | ml defaults | After ml block | Default value change | No | N/A |

All changes are strictly additive. No existing logic is removed, reordered, or modified. Every new block is wrapped in `try/except` that logs a warning and continues. No order placement, risk check, sizing, stop, or routing code is touched.

---

*This touch map must be reviewed before implementation begins. Protected files will not be edited until this map is confirmed.*
