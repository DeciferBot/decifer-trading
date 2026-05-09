# Codebase Cleanup Step 1 Report
**Branch:** `cleanup/safe-non-behavioural-cleanup-step1`  
**Date:** 2026-05-09  
**Based on audit:** `docs/codebase_cleanup_retirement_audit.md`  
**Scope:** Non-behavioural housekeeping only. No trading logic changed.

---

## Summary

Step 1 implements the safe, immediate cleanup actions from the audit. Zero trading behaviour was changed. Zero live_runtime modules were deleted. All smoke tests and targeted safety/risk/order tests pass at the same rate as before the changes.

---

## Files Deleted

### Migration Scripts (5 deleted)

All confirmed safe: zero imports, zero live-chain references, migrations already complete.

| File | Reason for deletion | Reference scan result |
|------|--------------------|-----------------------|
| `scripts/migrate_trades_to_training_store.py` | One-time JSONL migration (2026-04-28) — complete | Zero references in repo |
| `scripts/backfill_may5_trades.py` | One-time trade backfill — complete | Zero references in repo |
| `scripts/backfill_position_closed.py` | One-time position_closed backfill — complete | Zero references in repo |
| `scripts/reconcile_trades_json.py` | One-time trade JSON reconciliation — complete | Self-reference only (its own `__main__` block) |
| `scripts/recover_pattern_ids.py` | One-time pattern_id recovery — complete | Historical session log reference only (`chief-decifer/state/sessions/2026-04-13_metadata-immutability-learning-fixes.json`) — not a live reference |

### Files Intentionally NOT Deleted

| File | Why kept |
|------|----------|
| `tests/test_backfill_direction.py` | Tests `backfill_trades_from_ibkr()` direction labeling (SHORT-entry SELL mislabeled as LONG). This is a **permanent regression test** for broker reconcile logic, unrelated to the one-time migration scripts. The filename is misleading. |
| `scripts/rebuild_positions_from_intents.py` | Disaster recovery tool — reconstructs positions from ORDER_INTENT events. Not yet used but critical insurance. Classified as `migration_tool` in audit but with explicit note to keep. |

---

## Files Modified

### Log Rotation — New Helper

**`utils/__init__.py`** (new)  
**`utils/log_rotation.py`** (new)

Introduced `rotate_jsonl_if_needed(path, max_bytes, backup_count=3)` — a fail-safe, size-triggered JSONL rotation helper. Strategy: shift `.1 → .2 → .3`, then rename live file to `.1`. A fresh file is created on the next append. All errors are swallowed at DEBUG level — rotation must never block a write.

### Log Rotation — Wired into Production Files

**`apex_orchestrator.py`** — 4 write functions modified:

| Function | Log file | Max size | Backups |
|----------|----------|----------|---------|
| `log_shadow_result()` | `apex_shadow_log.jsonl` | 50 MB (`apex_shadow_log_max_mb` in CONFIG) | 3 |
| `_write_apex_audit()` | `apex_decision_audit.jsonl` | 50 MB (`apex_decision_audit_max_mb`) | 3 |
| `_write_prompt_snapshot()` | `apex_prompt_snapshot.jsonl` | 25 MB (`apex_prompt_snapshot_max_mb`) | 2 |
| `_write_response_snapshot()` | `apex_response_snapshot.jsonl` | 25 MB (`apex_response_snapshot_max_mb`) | 2 |

Lock discipline preserved: `rotate_jsonl_if_needed` is called **inside** the existing lock (`_shadow_log_lock`, `_audit_log_lock`, `_snapshot_lock`) before the `open()` call, so rotation and the subsequent write are atomic with respect to concurrent threads.

**`signal_pipeline.py`** — 2 tier_d_funnel write points modified:
- Scoring cap stage write (~line 722): rotation added before `open()`
- Pipeline stage write (~line 893): rotation added before `open()`

**`signal_dispatcher.py`** — 1 tier_d_funnel write point modified:
- Dispatch stage write (~line 639): rotation added before `open()`

**`bot_trading.py`** — 1 tier_d_funnel write point modified:
- Apex cap stage write (~line 2805): rotation added before `open()`

**Rotation size for `tier_d_funnel.jsonl`:** 10 MB (configurable via `tier_d_funnel_max_mb` in CONFIG).

### `signals_log.jsonl` — Already Handled

`learning.py:log_signal_scan()` already calls `_rotate_signals_log()` at a 50 MB threshold before each scan write. The `signal_pipeline._append_signals_log()` function writes to the same file and benefits from the rotation triggered by `log_signal_scan()` which is called immediately before it in the pipeline (line 843 → line 849). No additional rotation needed for signals_log.

### `.gitignore` Updated

Added the following new entries:

```
# Apex debug/audit logs (rotation-managed in code, not committed)
data/apex_shadow_log.jsonl
data/apex_shadow_log.jsonl.*
data/apex_decision_audit.jsonl
data/apex_decision_audit.jsonl.*
data/apex_prompt_snapshot.jsonl
data/apex_prompt_snapshot.jsonl.*
data/apex_response_snapshot.jsonl
data/apex_response_snapshot.jsonl.*

# Handoff publisher failure records
data/live/.fail_*.json

# Rotation backup files for signals_log
data/signals_log.jsonl.*
```

---

## Git Tracking Changes (Untracked from Git)

These files were removed from git tracking via `git rm --cached`. Local files were also deleted (the `.fail_*` files) or will be regenerated naturally (`__pycache__`, apex JSONL logs).

### Bytecode — Chief-Decifer-recovered (23 files removed)

All `.pyc` files in `Chief-Decifer-recovered/__pycache__/` and subdirectories. The `.gitignore` already had `__pycache__/` and `*.pyc` rules but these files were committed before that rule existed.

### macOS App Bundles (6 files removed)

- `Decifer Trading.app/Contents/Info.plist`, `.../MacOS/launch`, `.../Resources/AppIcon.icns`
- `Decifer.app/Contents/Info.plist`, `.../MacOS/Decifer`, `.../Resources/AppIcon.icns`

The `.gitignore` already had `*.app/` but these were committed before that rule. The physical `.app/` directories remain on disk (only untracked from git).

### Apex Debug JSONL Files (4 files removed from git)

- `data/apex_decision_audit.jsonl`
- `data/apex_prompt_snapshot.jsonl`
- `data/apex_response_snapshot.jsonl`
- `data/apex_shadow_log.jsonl`

These are now rotation-managed, grow unboundedly, and are not business-critical data. Added to `.gitignore`. Local files remain on disk for debugging continuity.

### `.fail_*` Diagnostic Records (11 files removed + deleted from disk)

All 11 `data/live/.fail_*.json` files (from 2026-05-07) were untracked and deleted. These are handoff publisher diagnostic output files written on validation failure. They are NOT sentinel files — the publisher only writes them, and the observer reads them for analysis. All 11 are from a single troubleshooting session on 2026-05-07. Added `data/live/.fail_*.json` to `.gitignore` so future fail files do not accumulate in git.

---

## `.gitignore` Status Check

The `.gitignore` already contained correct rules for:
- `__pycache__/` ✓
- `*.pyc` ✓  
- `.DS_Store` ✓
- `*.app/` ✓
- `logs/` ✓
- `data/signals_log.jsonl` ✓

All rules above were correct but the files were tracked because they were committed before the rules were added. `git rm --cached` removes the historical tracking.

---

## Test Results

| Test suite | Before Step 1 | After Step 1 | Delta |
|-----------|--------------|-------------|-------|
| Smoke (`-m smoke`) | 9 passed, 1 skipped | 9 passed, 1 skipped | ✓ unchanged |
| Risk + Orders + Guardrails + Imports | 325 passed, 1 skipped | 325 passed, 1 skipped | ✓ unchanged |
| Intelligence validator (`validate_intelligence_files.py`) | 40 PASS | 40 PASS | ✓ unchanged |

**Live import checks (all pass):**
```
bot_trading: OK
scanner: OK
apex_orchestrator: OK
signal_pipeline: OK
signal_dispatcher: OK
utils.log_rotation: OK
```

---

## Confirmation: Trading Behaviour Not Changed

- No signal dimension scores changed.
- No scoring thresholds changed.
- No symbol selection logic changed.
- No Apex prompt content changed.
- No order sizing logic changed.
- No risk limits changed.
- No broker/scanner/entry/exit logic changed.
- No feature flags changed.
- Log rotation calls are inside existing `try/except` blocks or (for apex) inside existing lock contexts. Errors are silently swallowed at DEBUG level — trading is never blocked by rotation failure.

**Config flags verified unchanged:**
- `use_intelligence_layer: True`
- `intelligence_first_advisory_enabled: False`
- `intelligence_first_shadow_enabled: False`
- `enable_active_opportunity_universe_handoff: True`
- `apex_shadow_log_max_mb`, `tier_d_funnel_max_mb`: NOT SET in config — using in-code defaults (50 MB and 10 MB respectively)

---

## Confirmation: Live Runtime Closure Intact

All 114 live_runtime modules from the audit remain on disk. The only source files deleted were the 5 migration scripts, all classified as `migration_tool` (not `live_runtime`) in the audit.

---

## Remaining Blockers for Step 2 (Post-Activation)

Step 2 requires activation to be confirmed before any of the following:

- Shadow modules: `handoff_publisher_observer.py`, `paper_handoff_builder.py`, `paper_handoff_comparator.py`, `apex_divergence.py` — retire only after activation confirmed.
- Advisory modules: `advisory_reporter.py`, `advisory_log_reviewer.py`, `intelligence_engine.py`, `intelligence_adapters.py` — retire only after intelligence-first cutover.
- `intelligence_schema_validator.py` (158KB) + `factor_registry.py` (61KB) — cloud exclusion only, keep locally.

## Remaining Blockers for Step 3 (Cloud Container)

- Implement `Dockerfile` / `.dockerignore` cloud exclusion list (cloud deployment planning required).
- Move `backtester.py`, `backtest_intelligence.py`, `build_brain.py` to `scripts/ml/` — await Amit approval on directory restructuring.
- Archive 21 intelligence sprint/day dev tests — require confirmation sprint phase is complete.
- Fix 2 pre-existing failing tests in `test_trailing_stop.py` — do not ship with known failures.
- Implement `test_orders_execute.py` (11 TODO stubs) — either implement or delete before cloud deploy.

---

## Open Issues After Step 1

| Issue | Action needed |
|-------|--------------|
| `data/tier_d_funnel.jsonl` is still git-tracked (2.9MB) | Keep tracked until Phase 2 gate review with Amit. Add rotation to prevent growth beyond 10MB (done in this branch). |
| `audit_log.jsonl` (1.9MB) is git-tracked and grows | Already in `.gitignore` — but currently tracked. Consider `git rm --cached data/audit_log.jsonl` in a future pass. |
| `execution_ic.jsonl` (2.4MB) is committed | Business-critical IC data — keep committed for cross-machine sync per CLAUDE.md. |
| `data/models/*.pkl` committed as binary blobs | ML models should be versioned separately before cloud deploy. |
| `test_orders_execute.py` has 11 TODO-only stubs | Implement or delete in Step 2+ planning. |
| 2 pre-existing failures in `test_trailing_stop.py` | Fix before cloud deployment — not introduced by Step 1. |
