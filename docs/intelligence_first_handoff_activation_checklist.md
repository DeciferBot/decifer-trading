# Intelligence-First Handoff Activation Checklist

**Sprint:** 7H design artefact — for use in controlled activation sprint only
**Status:** Pre-activation. Flag is False. Checklist is not active.
**Classification:** Advisory/design document. Do not use until Amit explicitly approves activation sprint.

---

## Instructions

This checklist is executed once per activation attempt. All items must be checked before setting `enable_active_opportunity_universe_handoff = True`. Any unchecked item or FAIL result aborts the activation. Do not skip items.

Fill in each field at the time of execution. Leave blank if not yet reached.

---

## Section 1 — Pre-Activation Checks

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1.1 | Confirm current branch is master and clean (`git status`) | | |
| 1.2 | Confirm `enable_active_opportunity_universe_handoff = False` in config.py | | |
| 1.3 | Confirm `handoff_enabled = false` in current_manifest.json | | |
| 1.4 | Confirm `live_bot_consuming_handoff = false` in observation report | | |
| 1.5 | Confirm bot is running on paper account DUP481326 | | |
| 1.6 | Confirm no live account is active | | |
| 1.7 | Confirm Amit is monitoring this session | | |

---

## Section 2 — Config Flag Check

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.1 | Current value of `enable_active_opportunity_universe_handoff` in config.py | False | Must be False before activation |
| 2.2 | Confirm no other handoff-related flags are set to True | | |

---

## Section 3 — Manifest Validation

Run: `python3 -c "import json; from intelligence_schema_validator import validate_prod_manifest; r = validate_prod_manifest('data/live/current_manifest.json'); print('PASS' if r.ok else r.errors)"`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 3.1 | `validation_status = pass` | | |
| 3.2 | `handoff_enabled = false` | | |
| 3.3 | `publication_mode = validation_only` | | |
| 3.4 | `active_universe_file` exists on disk | | |
| 3.5 | `safety_flags_clean = true` | | |
| 3.6 | Manifest age < 600s (primary SLA) at time of activation | | |

---

## Section 4 — Active Universe Validation

Run: `python3 -c "import json; from intelligence_schema_validator import validate_prod_active_universe; r = validate_prod_active_universe('data/live/active_opportunity_universe.json'); print('PASS' if r.ok else r.errors)"`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 4.1 | `validation_status = pass` | | |
| 4.2 | `candidate_count > 0` | | |
| 4.3 | `no_executable_trade_instructions = true` | | |
| 4.4 | `executable_violations = []` | | |
| 4.5 | `order_instruction_violations = []` | | |
| 4.6 | `publication_mode = validation_only` | | |

---

## Section 5 — Publisher Freshness Check

Run: `python3 handoff_publisher.py && python3 handoff_publisher_observer.py`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 5.1 | Publisher exits with `publish_cycle=success` | | |
| 5.2 | `sla_met = true` in observation report | | |
| 5.3 | `stale_count = 0` | | |
| 5.4 | `expired_count = 0` | | |
| 5.5 | `fail_closed_reason = null` in heartbeat | | |
| 5.6 | Manifest expires_at is > 10 minutes from now | | |

---

## Section 6 — Observer Gate Check

Run: `python3 handoff_publisher_observer.py`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 6.1 | `readiness_gate = validation_only_stable` | | |
| 6.2 | `threshold_met = true` | | |
| 6.3 | `successful_publisher_runs >= 10` | | |
| 6.4 | `all_safety_invariants_hold = true` | | |
| 6.5 | `live_output_changed = false` | | |

---

## Section 7 — Full Suite Result

Run: `python3 -m pytest -q`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 7.1 | Total tests passing | | Record count |
| 7.2 | Total tests failing | | Must be 0 new failures |
| 7.3 | Pre-existing known failures (trailing stop) | 2 | Pre-existing only — verify these are the same 2 |
| 7.4 | Full suite exit code = 0 (or same pre-existing baseline) | | |

---

## Section 8 — Smoke Result

Run: `python3 -m pytest -m smoke -q`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 8.1 | Smoke tests passing | | Must be 9/9 or higher |
| 8.2 | Any new smoke failures | | Must be 0 |

---

## Section 9 — Rollback Test Result

Before activating, test rollback in isolation (not during live bot operation):

1. Set `enable_active_opportunity_universe_handoff = True` in a test config context
2. Confirm `[handoff_wiring] flag_state=True` appears in test cycle
3. Set `enable_active_opportunity_universe_handoff = False`
4. Confirm `Building dynamic universe (Alpaca screening)...` appears in next test cycle
5. Confirm `[handoff_wiring] flag_state=True` absent from post-rollback logs

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 9.1 | Flag → True: handoff wiring active confirmed | | |
| 9.2 | Flag → False: scanner path restored confirmed | | |
| 9.3 | Rollback test completed successfully | | |
| 9.4 | No open positions affected during rollback test | | |

---

## Section 10 — Monitoring Owner

| # | Field | Value |
|---|-------|-------|
| 10.1 | Person monitoring this session | |
| 10.2 | Log monitoring tool / method | |
| 10.3 | Rollback authority | Amit only |
| 10.4 | Escalation contact if Amit unavailable | Do not activate |

---

## Section 11 — Activation Record

| # | Field | Value |
|---|-------|-------|
| 11.1 | Activation timestamp (UTC) | |
| 11.2 | Config change committed by | |
| 11.3 | Bot restart confirmed at | |
| 11.4 | First post-activation log cycle at | |
| 11.5 | `candidate_source=handoff_reader` confirmed at | |
| 11.6 | First post-activation candidate count | |

---

## Section 12 — Rollback Record (if triggered)

| # | Field | Value |
|---|-------|-------|
| 12.1 | Rollback triggered at (UTC) | |
| 12.2 | Rollback trigger reason | |
| 12.3 | Config change committed by | |
| 12.4 | Bot restart confirmed at | |
| 12.5 | Scanner path restored confirmed at | |
| 12.6 | `Building dynamic universe (Alpaca screening)...` confirmed in log at | |

---

## Section 13 — Post-Activation Review

Complete within 24 hours of activation or immediately after any rollback.

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 13.1 | Number of Track A scan cycles completed under handoff | | |
| 13.2 | Number of fail-closed events during activation | | Must be 0 |
| 13.3 | Candidate count stable across cycles | | Verify 50 ±0 |
| 13.4 | No executable candidate detected | | |
| 13.5 | No unexpected order instructions | | |
| 13.6 | Scanner fallback never occurred | | |
| 13.7 | PM Track B ran independently each cycle | | |
| 13.8 | Publisher freshness SLA met throughout | | |
| 13.9 | `validate_intelligence_files.py` re-run post-activation | | Must pass |
| 13.10 | Activation window result (success / rollback) | | |

---

## Section 14 — Amit Approval

**Activation must not proceed without all fields in this section completed.**

| # | Field | Value |
|---|-------|-------|
| 14.1 | Amit has reviewed this checklist | |
| 14.2 | Amit confirms all Section 1–9 checks pass | |
| 14.3 | Amit approves activation | **REQUIRED — must be explicit** |
| 14.4 | Approval recorded at (UTC) | |
| 14.5 | Activation sprint name/number | |
