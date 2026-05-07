# Intelligence-First Controlled Activation Test Plan

**Sprint:** 7H design artefact — test plan for future controlled activation sprint
**Status:** Design only. No tests implemented yet. Tests to be created in activation sprint.
**Classification:** Advisory/design document.

---

## Overview

This test plan covers the tests required before any activation of `enable_active_opportunity_universe_handoff = True`. Tests must be implemented and passing in the activation sprint. Test groups are ordered by dependency: baseline first, live-path last.

**Target test file:** `tests/test_handoff_controlled_activation.py`

---

## Test Group 1 — Flag False Baseline

**Objective:** Confirm the existing scanner-led path is completely unchanged when the flag is False. These are regression guards — they must pass identically before and after the activation sprint.

| # | Test | What it verifies |
|---|------|-----------------|
| 1.1 | `test_flag_false_scanner_path_runs` | When `enable_active_opportunity_universe_handoff=False`, `get_dynamic_universe()` is called and handoff wiring block does not execute |
| 1.2 | `test_flag_false_no_handoff_reader_import` | When flag is False, `handoff_reader` is not imported during the wiring block |
| 1.3 | `test_flag_false_no_handoff_adapter_import` | When flag is False, `handoff_candidate_adapter` is not imported |
| 1.4 | `test_flag_false_universe_is_scanner_output` | When flag is False, `universe` variable at wiring point contains scanner output, not handoff output |
| 1.5 | `test_flag_false_fail_closed_guard_inactive` | When flag is False, fail-closed guard at bot_trading.py:2542 does not execute |
| 1.6 | `test_flag_false_governance_map_empty` | When flag is False, `_handoff_governance_map = {}` throughout cycle |

---

## Test Group 2 — Flag True, Valid Manifest

**Objective:** Confirm the happy path when flag is True and all files are valid.

| # | Test | What it verifies |
|---|------|-----------------|
| 2.1 | `test_flag_true_handoff_reader_called` | When flag is True, `handoff_reader.load_production_handoff()` is called |
| 2.2 | `test_flag_true_universe_is_handoff_symbols` | Universe contains exactly the symbols from the handoff active_universe |
| 2.3 | `test_flag_true_candidate_count_50` | Candidate count = 50 (matches current publisher output) |
| 2.4 | `test_flag_true_governance_map_populated` | `_handoff_governance_map` contains per-symbol governance metadata |
| 2.5 | `test_flag_true_no_fail_closed_reason` | `_handoff_fail_closed_reason = None` on successful load |
| 2.6 | `test_flag_true_scanner_not_called` | `get_dynamic_universe()` is NOT called when flag is True and handoff succeeds |
| 2.7 | `test_flag_true_track_a_receives_handoff_symbols` | Apex Track A candidate list contains handoff symbols, not scanner symbols |

---

## Test Group 3 — Flag True, Invalid Manifest, Fail-Closed

**Objective:** Confirm that a corrupt, missing, or invalid manifest causes Track A to be skipped with no scanner fallback.

| # | Test | What it verifies |
|---|------|-----------------|
| 3.1 | `test_invalid_manifest_sets_fail_closed_reason` | `_handoff_fail_closed_reason` is non-null when manifest schema is invalid |
| 3.2 | `test_invalid_manifest_universe_is_empty` | `universe = []` when manifest is invalid |
| 3.3 | `test_invalid_manifest_track_a_skipped` | Fail-closed guard fires, Track A skipped |
| 3.4 | `test_invalid_manifest_no_scanner_fallback` | `get_dynamic_universe()` NOT called when manifest is invalid |
| 3.5 | `test_missing_manifest_sets_fail_closed` | Missing `current_manifest.json` sets fail_closed_reason |
| 3.6 | `test_missing_universe_sets_fail_closed` | Missing `active_opportunity_universe.json` sets fail_closed_reason |
| 3.7 | `test_zero_candidates_sets_fail_closed` | `candidate_count = 0` in universe sets fail_closed_reason |
| 3.8 | `test_executable_candidate_sets_fail_closed` | Any candidate with `executable=True` sets fail_closed_reason |
| 3.9 | `test_order_instruction_sets_fail_closed` | Any candidate with non-null `order_instruction` sets fail_closed_reason |
| 3.10 | `test_fail_closed_logs_no_scanner_fallback_attempted` | Log contains `scanner_fallback_attempted=False` |

---

## Test Group 4 — Flag True, Stale Manifest, Fail-Closed

**Objective:** Confirm that a stale or expired manifest causes fail-closed, not degraded operation.

| # | Test | What it verifies |
|---|------|-----------------|
| 4.1 | `test_stale_manifest_sets_fail_closed` | Manifest with `age > sla_expired_threshold_seconds` sets fail_closed_reason |
| 4.2 | `test_expired_manifest_universe_empty` | `universe = []` when manifest is expired |
| 4.3 | `test_expired_manifest_track_a_skipped` | Fail-closed guard fires |
| 4.4 | `test_manifest_expiry_field_enforced` | `expires_at` field in manifest is read and checked against current time |

---

## Test Group 5 — Flag True, Zero Candidates, Fail-Closed

| # | Test | What it verifies |
|---|------|-----------------|
| 5.1 | `test_zero_candidate_universe_sets_fail_closed` | `len(candidates) == 0` after schema validation sets fail_closed_reason |
| 5.2 | `test_zero_candidate_track_a_skipped` | No Apex call when universe is empty |
| 5.3 | `test_zero_candidate_no_scanner_fallback` | Scanner not called as fallback |

---

## Test Group 6 — Candidate Adapter Mapping

**Objective:** Confirm that `handoff_candidate_adapter.py` maps governance fields to the `handoff_*` prefix format correctly and does not invent non-existent governance data.

| # | Test | What it verifies |
|---|------|-----------------|
| 6.1 | `test_adapter_maps_route_to_handoff_route` | `route` → `handoff_route` |
| 6.2 | `test_adapter_maps_bucket_type_to_handoff_bucket_type` | `bucket_type` → `handoff_bucket_type` |
| 6.3 | `test_adapter_maps_thesis_intact_to_handoff_thesis_intact` | `thesis_intact` → `handoff_thesis_intact` |
| 6.4 | `test_adapter_maps_macro_rules_to_handoff_macro_rules` | `macro_rules_fired` → `handoff_macro_rules_fired` |
| 6.5 | `test_adapter_executable_always_false` | `executable = False` on every adapted candidate |
| 6.6 | `test_adapter_order_instruction_always_null` | `order_instruction = null` on every adapted candidate |
| 6.7 | `test_adapter_no_score_invention` | Adapter does not invent `signal_scores` or `conviction` values |
| 6.8 | `test_adapter_source_label_preserved` | `source_labels` from shadow universe preserved in adapted output |
| 6.9 | `test_adapter_pure_function` | Same input → same output; no side effects; no file I/O |

---

## Test Group 7 — Apex Input Boundary

**Objective:** Confirm that Apex receives exactly the same input structure when handoff=True as when scanner=True. The only difference must be the candidate source labels — not the structure, schema, or any decision logic.

| # | Test | What it verifies |
|---|------|-----------------|
| 7.1 | `test_apex_input_schema_unchanged_when_handoff_true` | `ApexInput` schema is identical regardless of candidate source |
| 7.2 | `test_apex_prompt_unchanged_when_handoff_true` | Apex prompt template is not modified when flag is True |
| 7.3 | `test_apex_receives_handoff_symbols_not_scanner_symbols` | Apex Track A candidates contain handoff symbols |
| 7.4 | `test_apex_regime_input_unchanged` | Regime context passed to Apex is identical regardless of handoff flag |
| 7.5 | `test_apex_portfolio_state_unchanged` | Portfolio state passed to Apex is identical regardless of handoff flag |
| 7.6 | `test_apex_input_changed_flag_false` | `apex_input_changed = False` in all cycle logs |

---

## Test Group 8 — Scanner Fallback Prevention

**Objective:** Confirm that no code path can invoke scanner discovery when `handoff_enabled=True`.

| # | Test | What it verifies |
|---|------|-----------------|
| 8.1 | `test_scanner_not_called_on_successful_handoff` | `get_dynamic_universe()` mock is never called when handoff succeeds |
| 8.2 | `test_scanner_not_called_on_failed_handoff` | `get_dynamic_universe()` mock is never called when handoff fails (fail-closed instead) |
| 8.3 | `test_scanner_output_changed_always_false` | `scanner_output_changed = False` in all cycle outputs |
| 8.4 | `test_handoff_wiring_log_confirms_no_fallback` | Log explicitly contains `scanner_fallback_attempted=False` on fail-closed |

---

## Test Group 9 — PM Track B Independence

**Objective:** Confirm that PM Track B (open position TRIM/EXIT/HOLD) runs independently of the handoff flag and is never blocked by handoff failures.

| # | Test | What it verifies |
|---|------|-----------------|
| 9.1 | `test_track_b_runs_when_handoff_false` | PM Track B review executes with flag=False |
| 9.2 | `test_track_b_runs_when_handoff_true_success` | PM Track B review executes with flag=True, handoff success |
| 9.3 | `test_track_b_runs_when_handoff_fail_closed` | PM Track B review executes when handoff fails (Track A skipped, Track B not skipped) |
| 9.4 | `test_track_b_not_affected_by_handoff_flag_state` | Track B output identical regardless of handoff flag state |

---

## Test Group 10 — Risk/Order/Execution Unchanged

**Objective:** Confirm that guardrails, position sizing, order submission, and broker calls are identical regardless of candidate source.

| # | Test | What it verifies |
|---|------|-----------------|
| 10.1 | `test_guardrails_called_identically_with_handoff_candidates` | `filter_candidates()` receives same structure from handoff as from scanner |
| 10.2 | `test_position_sizing_unchanged` | Position size calculation does not read handoff governance fields |
| 10.3 | `test_order_submission_path_unchanged` | Orders submitted via `execute_buy()` / `execute_short()` regardless of source |
| 10.4 | `test_risk_logic_changed_always_false` | `risk_logic_changed = False` in all cycle outputs |
| 10.5 | `test_order_logic_changed_always_false` | `order_logic_changed = False` in all cycle outputs |
| 10.6 | `test_broker_called_field_unchanged` | Broker call instrumentation unchanged |

---

## Test Group 11 — Rollback Flag Flip

**Objective:** Confirm that setting `enable_active_opportunity_universe_handoff = False` immediately restores the scanner path with no residual handoff state.

| # | Test | What it verifies |
|---|------|-----------------|
| 11.1 | `test_flag_false_after_true_restores_scanner` | Scanner path active on first cycle after flag→False |
| 11.2 | `test_rollback_clears_governance_map` | `_handoff_governance_map = {}` after rollback |
| 11.3 | `test_rollback_clears_fail_closed_reason` | `_handoff_fail_closed_reason = None` after rollback |
| 11.4 | `test_rollback_no_stale_handoff_state` | No handoff-sourced candidates appear in post-rollback Track A |
| 11.5 | `test_rollback_publisher_files_preserved` | `current_manifest.json` and `active_opportunity_universe.json` exist after rollback |

---

## Test Group 12 — Publisher Stale Scenario

**Objective:** Confirm system behaviour when the publisher has not run for > SLA threshold.

| # | Test | What it verifies |
|---|------|-----------------|
| 12.1 | `test_stale_manifest_detected_by_reader` | `handoff_reader` detects manifest age > 1200s and returns fail_closed |
| 12.2 | `test_stale_universe_detected_by_reader` | Universe file older than SLA triggers fail_closed |
| 12.3 | `test_heartbeat_stale_detected_by_observer` | Observer detects heartbeat age > threshold and sets `expired_count > 0` |
| 12.4 | `test_observer_gate_unstable_when_publisher_stale` | Gate = `validation_only_unstable` when freshness expired |

---

## Test Group 13 — Run-Log Observation

**Objective:** Confirm that `publisher_run_log.jsonl` accumulates correctly and the observer reads it accurately during an activation window.

| # | Test | What it verifies |
|---|------|-----------------|
| 13.1 | `test_run_log_appended_after_each_successful_cycle` | Each successful publisher run adds one JSONL line |
| 13.2 | `test_run_log_not_appended_on_fail_closed` | Failed publisher cycle does not add a line |
| 13.3 | `test_observer_successful_runs_matches_run_log` | `successful_publisher_runs` in observation report matches JSONL line count |
| 13.4 | `test_run_log_safety_flags_all_false` | All safety flags in every run log line are False |
| 13.5 | `test_run_log_live_output_changed_false` | `live_output_changed=False` in every run log line |

---

## Test Group 14 — Post-Activation Audit Report

**Objective:** Confirm that the observer produces a valid post-activation report immediately after activation and again after rollback.

| # | Test | What it verifies |
|---|------|-----------------|
| 14.1 | `test_observer_runs_after_activation` | Observer generates report when flag=True (file still valid_only publication mode) |
| 14.2 | `test_observer_report_mode_unchanged` | Observer report mode remains `validation_only_handoff_publisher_observation` |
| 14.3 | `test_observer_safety_invariants_hold_post_activation` | All 13 safety flags still False in observation report after activation |
| 14.4 | `test_validator_passes_post_activation` | `validate_intelligence_files.py` passes after activation |
| 14.5 | `test_observer_gate_valid_post_activation` | Observer gate remains `validation_only_stable` (publisher is still producing valid output) |

---

## Summary

| Group | Test count | Sprint dependency |
|-------|-----------|-------------------|
| 1 — Flag false baseline | 6 | Must pass before and after activation sprint |
| 2 — Flag true valid manifest | 7 | Activation sprint |
| 3 — Invalid manifest fail-closed | 10 | Activation sprint |
| 4 — Stale manifest fail-closed | 4 | Activation sprint |
| 5 — Zero candidates fail-closed | 3 | Activation sprint |
| 6 — Candidate adapter mapping | 9 | Activation sprint |
| 7 — Apex input boundary | 6 | Activation sprint |
| 8 — Scanner fallback prevention | 4 | Activation sprint |
| 9 — PM Track B independence | 4 | Activation sprint |
| 10 — Risk/order/execution unchanged | 6 | Activation sprint |
| 11 — Rollback flag flip | 5 | Activation sprint |
| 12 — Publisher stale scenario | 4 | Activation sprint |
| 13 — Run-log observation | 5 | Activation sprint |
| 14 — Post-activation audit | 5 | Activation sprint |
| **Total** | **78** | |

All 78 tests must pass before any activation is considered complete.
