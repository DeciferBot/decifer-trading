# Intelligence-First: Controlled Handoff Implementation Test Plan

**Sprint:** 7D  
**Date:** 2026-05-07  
**Status:** Design only — tests to be implemented in Sprint 7E  
**Prerequisite:** Full suite required (production module touched)

---

## Overview

Sprint 7E will wire `handoff_reader.py` into `bot_trading.py` at the `get_dynamic_universe()` call site. Because a production module (`bot_trading.py`) will be modified, the **full test suite is required** before the sprint is declared complete.

All tests below must pass. Any failure blocks the sprint.

---

## Group 1 — Flag False Path (scanner unchanged)

Tests verifying that when `enable_active_opportunity_universe_handoff=False`, the scanner path is completely unchanged.

| # | Test | Expected |
|---|------|----------|
| 1.1 | `get_dynamic_universe()` called normally when flag False | `get_dynamic_universe` is called; `handoff_reader` is not |
| 1.2 | `read_manifest()` not called when flag False | No manifest read attempted |
| 1.3 | No active universe read when flag False | `data/live/current_manifest.json` not accessed |
| 1.4 | No handoff reader call in live path when flag False | `load_production_handoff` never called in scan cycle |
| 1.5 | `scanner_output_changed = False` when flag False | Confirmed in scan cycle log |
| 1.6 | `live_output_changed = False` when flag False | Confirmed in scan cycle log |
| 1.7 | Scan cycle produces same candidates as before wiring change | Regression: scored output identical to pre-implementation baseline |

---

## Group 2 — Flag True, Valid Manifest

Tests verifying that when `enable_active_opportunity_universe_handoff=True` with a valid manifest and universe:

| # | Test | Expected |
|---|------|----------|
| 2.1 | `read_manifest()` called with correct path | `data/live/current_manifest.json` read |
| 2.2 | Manifest validated before use | `validate_manifest()` called; result checked |
| 2.3 | Active universe file read | Path from manifest used |
| 2.4 | All candidates validated | `validate_candidate()` called per candidate |
| 2.5 | `get_dynamic_universe()` NOT called | Scanner discovery bypassed |
| 2.6 | Symbol list extracted and passed to `run_signal_pipeline()` | Symbol list contains only handoff symbols |
| 2.7 | Governance metadata attached to scored dicts | `handoff_route_hint`, `handoff_theme_ids`, etc. present in scored dict |
| 2.8 | Downstream scoring unchanged | `score_universe()` receives same input type (`list[str]`) |
| 2.9 | Guardrails applied to handoff-scored candidates | Guardrails path unchanged |
| 2.10 | Apex input shape valid | `build_scan_cycle_apex_input()` receives valid candidates |
| 2.11 | `apex_input_changed = False` in log | Apex receives same-shaped input |
| 2.12 | `candidate_source = handoff_reader` in log | Logged correctly |

---

## Group 3 — Flag True, Invalid Manifest

Tests verifying fail-closed behaviour on invalid inputs:

| # | Test | Expected |
|---|------|----------|
| 3.1 | Manifest file missing → fail closed | No new entries; no scanner fallback; log reason |
| 3.2 | Manifest invalid JSON → fail closed | Same |
| 3.3 | Manifest expired → fail closed | Same |
| 3.4 | `validation_status != "pass"` → fail closed | Same |
| 3.5 | `handoff_enabled = False` in manifest → fail closed | Same |
| 3.6 | Active universe file missing → fail closed | Same |
| 3.7 | Zero accepted candidates → fail closed | Same |
| 3.8 | Handoff reader exception → fail closed | Exception caught; bot continues; PM path unaffected |
| 3.9 | Scanner NOT called after any fail-closed event | `get_dynamic_universe()` never invoked as fallback |
| 3.10 | Existing position PM logic runs after fail-closed | Track B PM review continues independently |

---

## Group 4 — Fail-Closed Matrix (all 21 Sprint 7B conditions)

| # | Condition | Result |
|---|-----------|--------|
| 4.1 | Manifest file missing | fail closed |
| 4.2 | Manifest invalid JSON | fail closed |
| 4.3 | Manifest expired | fail closed |
| 4.4 | `validation_status != "pass"` | fail closed |
| 4.5 | `handoff_mode` invalid | fail closed |
| 4.6 | Safety flags wrong in manifest | fail closed |
| 4.7 | `active_universe_file` missing from manifest | fail closed |
| 4.8 | Active universe file missing | fail closed |
| 4.9 | Active universe invalid JSON | fail closed |
| 4.10 | Active universe expired | fail closed |
| 4.11 | Active universe `validation_status` not pass/warning | fail closed |
| 4.12 | Active universe safety flags wrong | fail closed |
| 4.13 | Candidate missing `symbol` | candidate rejected, not universe fail |
| 4.14 | Candidate missing `reason_to_care` | candidate rejected |
| 4.15 | Candidate missing `route` AND `route_hint` | candidate rejected |
| 4.16 | Candidate missing `source_labels` | candidate rejected |
| 4.17 | Candidate `executable = True` | candidate rejected |
| 4.18 | Candidate `order_instruction` not null | candidate rejected |
| 4.19 | Candidate unapproved `approval_status` | candidate rejected |
| 4.20 | Candidate unapproved `source_label` | candidate rejected |
| 4.21 | Zero candidates after per-candidate validation | fail closed |

---

## Group 5 — Candidate Adapter Tests

Tests for `handoff_candidate_adapter.py`:

| # | Test | Expected |
|---|------|----------|
| 5.1 | `attach_governance_metadata()` adds all required fields | All `handoff_*` fields present in scored dict |
| 5.2 | Route value preserved from handoff | `handoff_route` matches source candidate |
| 5.3 | `source_labels` preserved | `handoff_source_labels` matches source |
| 5.4 | `theme_ids` preserved | `handoff_theme_ids` matches source |
| 5.5 | `risk_flags` preserved | `handoff_risk_flags` matches source |
| 5.6 | `executable` never attached | No `executable` field added to scored dict by adapter |
| 5.7 | `order_instruction` never attached | No `order_instruction` field added |
| 5.8 | Adapter does not modify `score`, `raw_score`, signal dimensions | Signal values unchanged after adapter call |
| 5.9 | Adapter is pure (no I/O, no side effects) | No file reads, no network, no logging |
| 5.10 | Symbol not in governance map → scored dict unchanged | Unknown symbols silently skipped |

---

## Group 6 — Apex Boundary Tests

| # | Test | Expected |
|---|------|----------|
| 6.1 | Apex `track_a.candidates` contains only handoff-symbol dicts | No scanner-only symbol in Apex input |
| 6.2 | Apex `track_a` symbols are a subset of accepted handoff symbols | No extra symbols injected |
| 6.3 | Apex cannot receive scanner-only candidates when flag True | Confirmed by symbol set intersection test |
| 6.4 | Apex receives governance metadata fields | `handoff_route_hint` etc. present in candidate dicts |
| 6.5 | Apex input shape is identical to pre-implementation shape | Same dict keys, same structure |

---

## Group 7 — Risk/Order/Execution Unchanged

| # | Test | Expected |
|---|------|----------|
| 7.1 | `guardrails.py` unchanged | No import changes; same function signatures |
| 7.2 | `orders_core.py` unchanged | No modifications |
| 7.3 | `bot_ibkr.py` unchanged | No modifications |
| 7.4 | `risk` logic unchanged | Risk computations identical |
| 7.5 | Order sizing unchanged | Position sizing from existing logic |
| 7.6 | `execute_buy()` / `execute_short()` unchanged | No changes to order submission |
| 7.7 | Forced exits unchanged | EOD flat, 90-min timeout, thesis-validity sells — all unchanged |

---

## Group 8 — Rollback Tests

| # | Test | Expected |
|---|------|----------|
| 8.1 | Flag False → scanner path restored immediately | First scan after flag flip uses scanner |
| 8.2 | Invalid manifest after rollback → no corruption | Bot state clean after fail-closed followed by rollback |
| 8.3 | Config flip test (settings_override.json) | Flag change reflected without code restart if hot-reload supported |
| 8.4 | Handoff files intact after rollback | `data/live/` files not deleted or modified by rollback |

---

## Group 9 — Dry-Run Compare Mode (if implemented)

| # | Test | Expected |
|---|------|----------|
| 9.1 | `enable_handoff_dry_run_compare=True` does not replace candidate source | Scanner path unchanged |
| 9.2 | Comparison log written to `data/live/handoff_dry_run_compare_log.jsonl` | File exists and is valid JSONL |
| 9.3 | `live_output_changed = False` in dry-run mode | Confirmed |
| 9.4 | Apex input unchanged in dry-run mode | Apex receives same scanner-scored candidates |

---

## Group 10 — Full Suite Requirement

Because `bot_trading.py` is modified:

```bash
python3 -m pytest -q
```

All tests must pass. No new failures permitted against the pre-implementation baseline.

Specific regressions to verify:
- `tests/test_orders_core.py` — order logic unchanged
- `tests/test_event_log_and_training_store.py` — data persistence unchanged
- `tests/test_intelligence_sprint7b.py` — handoff reader unchanged
- `tests/test_intelligence_sprint7c.py` — comparator unchanged
- All `tests/test_intelligence_*.py` — intelligence pipeline unchanged

---

## Implementation Prerequisites Checklist

Before writing a single line of Sprint 7E code:

- [ ] `docs/intelligence_first_controlled_handoff_wiring_design.md` approved by Amit
- [ ] Wiring point at `bot_trading.py:1447` confirmed
- [ ] Candidate shape mapping approved (Section 6 of wiring design)
- [ ] Fail-closed matrix reviewed
- [ ] This test plan reviewed
- [ ] Risk review accepted
- [ ] Metric reconciliation accepted
- [ ] **Amit explicitly approves Sprint 7E implementation**
