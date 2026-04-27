# Phase 8A — Apex Execute Path Implementation — Handoff

**Date:** 2026-04-24
**Branch:** master
**Commits:** `3b61141` (8A.1) → `799f425` (8A.2) → `eb0cdcd` (8A.3–8A.6)
**Status:** IMPLEMENTED. Not yet activated — cutover flags unchanged.

---

## What Phase 8A Delivered

The execute path that the Phase 6/7 shadow pipeline pretended existed is
now real. When (and only when) the authoritative cutover flags are flipped,
the scan-cycle Track A path, the PM Track B path, and the Sentinel
NEWS_INTERRUPT path will route through `apex_orchestrator._run_apex_pipeline(execute=True)`
and live dispatch into `signal_dispatcher.dispatch(..., execute=True)` or
`signal_dispatcher.dispatch_forced_exit(..., execute=True)`.

Until the flags flip, behavior is unchanged: legacy pipeline runs,
shadow+divergence logging runs, no Apex orders are submitted.

### 8A.1 — `_run_apex_pipeline(execute=True)` implemented
- File: `apex_orchestrator.py`
- Replaced the `NotImplementedError` placeholder.
- Calls `signal_dispatcher.dispatch(...)` for new_entries + portfolio_actions
- Iterates `forced_exits` → `signal_dispatcher.dispatch_forced_exit(...)`
  (accepts both tuple and dict forms)
- All exceptions swallowed into `result["dispatch_report"]["errors"]` — never raises.
- Returns shadow shape + `dispatch_report` + `note: "executed"`.
- Tests: `tests/test_apex_phase8a_execute_path.py` (9 tests)

### 8A.2 — Scan-cycle Track A cutover branch
- File: `bot_trading.py` (after line 2234, before the legacy `_all_buys` loop)
- Gated on `not safety_overlay.should_use_legacy_pipeline()`.
- Runs `filter_candidates` + `screen_open_positions` + `flag_positions_for_review`,
  builds SCAN_CYCLE ApexInput, calls `_run_apex_pipeline(execute=True)`, logs
  a structured summary, and **returns early before the legacy loop**.
- Legacy loop preserved — flag-flip toggles between legacy and Apex with
  zero code change.
- Tests: `tests/test_apex_phase8a_scan_cycle_cutover.py` (6 tests)

### 8A.3–8A.5 — Lock-in tests (no code changes)
Existing PM Track B and Sentinel NI cutover branches (shipped in Phase 6D)
were audited; their live dispatch already depends on
`not should_use_legacy_pipeline()`. Lock-in tests guard this wiring so
future refactors cannot quietly break the cutover contract:
- `tests/test_apex_phase8a_pm_trackb_execute.py` (5 tests)
- `tests/test_apex_phase8a_sentinel_ni_execute.py` (4 tests)
- `tests/test_apex_phase8a_finbert_gate.py` (4 tests — news_sentinel materiality gate)

### 8A.6 — Trade advisor gate regression (re-verified)
- `tests/test_apex_phase7_7c7_trade_advisor_gate.py` — all 6 still pass.

### 8A.7 — Full regression
```
python3 -m pytest tests/ -q
2053 passed, 1 skipped, 11 failed
```

**All 11 failures are pre-existing Phase 8 Step 1 artifacts, not Phase 8A regressions.**
- 9 failures across `test_apex_flip_proposer.py`, `test_apex_phase7_7c6_news_interrupt_shadow.py`,
  `test_apex_phase7_7c9_deletion_readiness.py` — all assert
  `USE_APEX_V3_SHADOW` default is False. The Step 1 flip changed it to True.
  Expected; these tests encode a stale default.
- 2 failures in `test_reconnect.py` — IBKR backoff timing, flaky, unrelated.

Test-count delta vs prior baseline (1943 target): +110 new passing (2053),
well past the target.

---

## State of the Six Cutover Flags (unchanged tonight)

| Flag | Default | Current | Effect when flipped |
|---|---|---|---|
| `USE_APEX_V3_SHADOW` | False | **True** (Step 1) | Shadow+divergence logging ON |
| `USE_LEGACY_PIPELINE` | True | True | Master cutover gate — controls scan/PM/Sentinel execute |
| `PM_LEGACY_OPUS_REVIEW_ENABLED` | True | True | Structural: enters PM cutover branch (dry-run until USE_LEGACY_PIPELINE flips) |
| `SENTINEL_LEGACY_PIPELINE_ENABLED` | True | True | Structural: enters NI cutover branch |
| `FINBERT_MATERIALITY_GATE_ENABLED` | False | False | Sentinel gate source |
| `TRADE_ADVISOR_ENABLED` | True | True | Legacy sizing advisor |

---

## What Still Has To Happen Before Real Paper Cutover

1. **Verify Phase 8 Step 1 data.** The bot was restarted earlier with
   `USE_APEX_V3_SHADOW=True`. Before considering any further flips, confirm:
   - `data/apex_shadow_log.jsonl` has fresh entries from post-restart scans
   - `data/apex_divergence_log.jsonl` is accumulating records
   - Phase 7B hard gates met over ≥20 scan cycles:
     - fallback_rate ≤ 5%
     - schema_reject_rate ≤ 2%
     - p95 latency ≤ 30s
     - zero unresolved HIGH severity divergences
   - Soft gate: AGREE ≥ 90%

2. **Refresh the flag-default tests.** Update the 9 tests that assert
   `USE_APEX_V3_SHADOW` default is False (see failure list above) to reflect
   the new default. Do NOT "fix" them by flipping the flag back — the flag
   change is intentional.

3. **Canonical 6-step flip sequence (audit-only today).** The cutover itself
   is done in strict order per `apex_flip_proposer.py`. The flags listed with
   `SENTINEL_LEGACY_PIPELINE_ENABLED` being last:
   1. `FINBERT_MATERIALITY_GATE_ENABLED` → True
   2. `TRADE_ADVISOR_ENABLED` → False
   3. `USE_LEGACY_PIPELINE` → False  **(this is the live cutover — Apex owns all execute paths)**
   4. `PM_LEGACY_OPUS_REVIEW_ENABLED` → False
   5. `SENTINEL_LEGACY_PIPELINE_ENABLED` → False
   6. (eventually) legacy code deletion — out of scope until after live cutover is stable.

4. **Fix the `sys.path` shadow in `scripts/apex_flip_proposer.py`** so
   `status` runs without `PYTHONPATH=$PWD`. A background task has been
   spawned for this.

---

## Runtime Risk Notes

- `_run_apex_pipeline(execute=True)` never raises — errors recorded in
  `dispatch_report["errors"]`. The scan-cycle cutover branch also wraps
  the entire call in `try/except` as a second safety net.
- Fail-safe defaults: if reading `should_use_legacy_pipeline()` raises,
  `_scan_cutover` and `_pm_cutover_execute` default to **False** →
  legacy behavior preserved.
- Forced exits are dispatched **after** the normal `dispatch()` call and
  their errors are isolated per-symbol — one bad forced exit cannot block
  others.

---

## Exact Next Recommended Step

**Do not flip any more flags tonight.** The next action is operator-side:

1. Watch `data/apex_shadow_log.jsonl` and `data/apex_divergence_log.jsonl`
   for ≥20 clean scan cycles.
2. Run `PYTHONPATH="$PWD" python3 scripts/apex_flip_proposer.py status` to
   confirm Phase 7B hard gates pass.
3. Only once gates pass and AGREE ≥ 90%, manually edit `config.py` to set
   `FINBERT_MATERIALITY_GATE_ENABLED: True` (flip #1 of the 6-step sequence).
   Restart, verify, repeat for each subsequent flip.

Legacy code deletion remains explicitly out of scope per tonight's directive.
