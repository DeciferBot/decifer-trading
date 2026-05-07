# Intelligence-First Handoff Activation Design Review

**Sprint:** 7H — Design Review Only
**Status:** Design review complete. Flag activation blocked pending Amit approval.
**Date:** 2026-05-07
**Classification:** Advisory/design document. No production code changed.

---

## 1. Current State

### Components Built

| Component | File | Status | Classification |
|-----------|------|--------|----------------|
| Handoff reader | `handoff_reader.py` | Built and tested | Adapter-only |
| Candidate adapter | `handoff_candidate_adapter.py` | Built and tested | Adapter-only |
| Bot wiring | `bot_trading.py` lines 1548, 2542 | Built, flag-gated off | Production runtime |
| Publisher | `handoff_publisher.py` | Built and running | Production runtime candidate |
| Observer | `handoff_publisher_observer.py` | Built and running | Advisory/shadow-only |
| Manifest | `data/live/current_manifest.json` | Published (validation_only) | Production output |
| Active universe | `data/live/active_opportunity_universe.json` | Published (validation_only) | Production output |
| Observation report | `data/live/handoff_publisher_observation_report.json` | Generated | Advisory/shadow-only output |
| Run log | `data/live/publisher_run_log.jsonl` | 10 records | Production observability output |

### Flag States

| Flag | Value |
|------|-------|
| `enable_active_opportunity_universe_handoff` | **False** |
| `handoff_enabled` (in manifest) | **false** |
| `publication_mode` | **validation_only** |
| `live_bot_consuming_handoff` | **false** |

### Current Bot Path

The live bot is **scanner-led**. `get_dynamic_universe()` runs on every scan cycle. The handoff wiring block at `bot_trading.py:1548` is gated by `enable_active_opportunity_universe_handoff=False` and executes zero code in the current state.

---

## 2. Evidence Summary

| Metric | Value |
|--------|-------|
| Successful publisher runs | 10 |
| Failed publisher runs | 6 (all test artefacts — sprint development) |
| Distinct UTC sessions | 1 (2026-05-07) |
| Live observation failures | 0 |
| Validator result | 40/40 PASS |
| Smoke result | 9/9 PASS |
| Candidate count per run | 50 (stable across all 10 runs) |
| Executable candidates | 0 |
| Order instructions | 0 |
| Safety invariants | All 13 clean across all runs |
| Freshness SLA | Met on every run |
| `live_output_changed` | false on every run |
| Gate | `validation_only_stable` |

**Observation window:** 38 minutes (16:11:11Z – 16:49:48Z), single UTC date. This is a compressed intra-session observation window, not a multi-day cross-session window.

---

## 3. Remaining Risk Review

### RISK-01 — Single-Session Observation Window (HIGH)

**Description:** All 10 successful publisher runs occurred on 2026-05-07 within a 38-minute window. No cross-UTC-date evidence. The `distinct_utc_sessions=1` criterion was not met — activation was entered via the 10-run threshold only.

**Implication:** The publisher has not been observed across market open/close boundaries, overnight state changes, or different market sessions. Shadow universe freshness and candidate composition stability across session boundaries is unverified.

**Mitigation options:** Continue observation across ≥3 UTC sessions before activation (Option A). Or accept single-session evidence with enhanced monitoring (Option B).

**Status:** Open — requires Amit decision on acceptable observation depth.

---

### RISK-02 — 208 Scanner-Only Removals (HIGH)

**Description:** When `enable_active_opportunity_universe_handoff=True`, the live bot's candidate universe switches from `get_dynamic_universe()` to `handoff_reader.load_production_handoff()`. The current shadow universe produces 50 governed candidates. The live scanner produces ~258 candidates per cycle. The delta is approximately 208 scanner-sourced symbols that would disappear from Track A candidate eligibility.

**Implication:** These 208 symbols include Tier A/B scanner-discovered candidates, real-time momentum/breakout setups, intraday sector rotation leaders, and sympathy plays. The handoff universe contains only Intelligence-First governed candidates. Any active scanner-discovered setup not in the shadow universe would become invisible to Track A.

**Mitigation:** PM Track B (open position review: TRIM/EXIT/HOLD) is independent of the handoff flag and continues to run regardless. No open position is abandoned. Only *new entries* are gated. A rollback (flag → False) restores full scanner discovery immediately.

**Status:** Documented and accepted per Sprint 7D risk review. Requires explicit Amit acknowledgement before any activation sprint.

---

### RISK-03 — SNDK / WDC / IREN Quota Exclusion (MEDIUM)

**Description:** SNDK, WDC, and IREN are governed through the full Intelligence-First architecture (coverage_gap_review → thematic_roster → transmission_rules → candidate_resolver → shadow universe). They appear in `economic_candidate_feed.json` but are excluded from the production-published shadow universe by quota: the structural_position quota is capped at 20, and all 20 slots are filled by higher-priority candidates.

**Implication:** Activating the handoff would make SNDK, WDC, and IREN ineligible for Track A new entries — not because they are ungoverned, but because the quota policy excludes them at the shadow pipeline level. This is correct architecture, but it means quota design decisions directly affect what symbols Apex can be asked about in Track A.

**Mitigation:** Quota policy must be reviewed before activation. If SNDK/WDC/IREN are genuine conviction names, either (a) expand the structural_position quota, or (b) accept that they remain watchlist/advisory-only. Neither option can be decided during an activation sprint — it is a pre-activation prerequisite.

**Status:** Open — quota policy decision required before activation sprint.

---

### RISK-04 — No Per-Run Snapshot Archive for Candidate Diff (MEDIUM)

**Description:** `candidate_stability_analysis` in the observer reports `status=multi_observation_available` but all diff fields (added/removed/route_changes/quota_group_changes) are null. There is no snapshot archive that stores candidate composition per run, so cross-run stability cannot be quantified.

**Implication:** The 50-symbol set appeared stable across 10 runs within a single session, but this is unverified. A snapshot archive would prove that the composition is deterministic and predictable — a material property for deciding when to activate.

**Mitigation:** Add snapshot-per-run archive to publisher (append one JSON snapshot per successful cycle to `data/live/publisher_snapshot_archive.jsonl`). This is a low-risk addition to the publisher. Not strictly required for activation, but it closes the observability gap.

**Status:** Open — optional pre-activation improvement. Does not block activation if Amit accepts the risk.

---

### RISK-05 — Fail-Closed Blocks Track A New Entries (MEDIUM)

**Description:** When `handoff_enabled=True` and the handoff fails validation (manifest stale, universe missing, schema invalid, candidate count zero), the fail-closed guard at `bot_trading.py:2542` skips Track A entirely. No new entries are produced. The bot continues to manage open positions via Track B.

**Implication:** Any publisher disruption (process crash, file system issue, stale manifest) causes a complete new-entry blackout for the duration of the failure. Under the scanner path, a transient failure would at worst cause score degradation — the scanner still runs. Under the handoff path, failure means zero Track A entries until the publisher recovers.

**Mitigation:** Publisher must run reliably on a schedule. The heartbeat SLA (expiry = 15 minutes after publication) provides a detection window. Publisher failures are surfaced in `.fail_*.json` diagnostics. Monitoring must alert when `fail_closed_reason` is non-null.

**Status:** By design. Acceptable with monitoring in place.

---

### RISK-06 — No Scanner Fallback When Handoff=True (MEDIUM)

**Description:** The wiring design decision (Sprint 7D, locked) is: when `handoff_enabled=True`, **no scanner fallback**. If the handoff fails, Track A is skipped. The scanner does not run as a backup.

**Implication:** This is intentional — scanner fallback would allow a corrupt or stale handoff to silently degrade to the old path without detection. The fail-closed design forces explicit acknowledgement of failures. However, it means the bot becomes dependent on the publisher's availability.

**Mitigation:** Publisher must be treated as a hard dependency when flag is True. Scheduler reliability, heartbeat SLA monitoring, and rollback procedure are the mitigations.

**Status:** Locked design decision. No change.

---

### RISK-07 — PM Track B Independence (LOW)

**Description:** PM Track B (open position TRIM/EXIT/HOLD review) runs independently of the handoff flag at `bot_trading.py` and is not affected by handoff failures. This means even if Track A is fail-closed, open positions continue to be managed.

**Implication:** Positive safety property. No open position is orphaned by handoff failures.

**Status:** Confirmed by code audit. No risk.

---

### RISK-08 — Rollback Depends on Flag Returning to False (LOW)

**Description:** The rollback procedure (set `enable_active_opportunity_universe_handoff = False` in `config.py`, restart bot) restores the scanner path immediately. No code revert is required. Publisher output files are preserved.

**Implication:** Rollback is fast and clean. The only risk is if the flag change is not propagated before the next scan cycle.

**Mitigation:** Rollback test must be part of activation prerequisites (see Section 5). Bot restart confirms scanner path resumes.

**Status:** Low risk. Rollback design is straightforward.

---

## 4. Activation Options

### Option A — Continue Observation Across 3 UTC Sessions Before Any Flag Activation

Continue running validation-only publisher cycles until `distinct_utc_sessions >= 3`. This provides cross-session evidence: shadow universe stability across market open/close/overnight boundaries, consistent freshness SLA compliance, and confirmation that the publisher runs reliably on different days with different underlying data.

**Advantages:** Closes RISK-01 entirely. Provides the strongest possible evidence base before activation. Aligns with the original dual-threshold intent (sessions criterion is the harder, more meaningful gate).

**Disadvantages:** Adds 2+ calendar days before activation sprint begins. No new evidence expected — the pipeline architecture is already validated. This is primarily a waiting cost.

---

### Option B — Proceed to Controlled Activation Design Based on 10 Successful Runs

Accept the 10-run single-session evidence as sufficient for entering a *design and test* sprint (Sprint 7I), with the activation window itself restricted to a monitored intraday window. Activation would happen within a single session under direct monitoring, with rollback primed.

**Advantages:** Faster path. The pipeline is architecturally sound. The publisher is deterministic. Adding more same-day runs adds no new information.

**Disadvantages:** RISK-01 remains partially open. If the publisher behaves differently across sessions (e.g., overnight state changes cause different candidate composition), this would not be detected pre-activation.

---

### Option C — Do Not Activate Until Candidate Snapshot Archive Exists

Before any activation sprint, implement the per-run snapshot archive (RISK-04) so that candidate composition stability across sessions can be verified quantitatively.

**Advantages:** Closes RISK-04. Provides concrete cross-run diff evidence.

**Disadvantages:** Requires a modest publisher extension sprint before activation. Not strictly required if Amit accepts the observability gap.

---

### Option D — Do Not Activate Until Quota Policy Is Revisited

Before any activation sprint, explicitly decide the quota policy for SNDK/WDC/IREN and whether the structural_position quota (currently 20) is the correct ceiling.

**Advantages:** Closes RISK-03. Ensures the first activated universe is the intended one.

**Disadvantages:** Quota policy is a design decision, not a blocker — SNDK/WDC/IREN being in quota-overflow is documented and expected. Revisiting quota can happen during an activation sprint without blocking the flag flip.

---

### Recommendation: **Option A**

Continue observation until `distinct_utc_sessions >= 3`. The primary reason is not technical — the publisher and pipeline are demonstrably sound after 10 clean runs. The reason is operational: the only unresolved question is how the publisher behaves across session boundaries (overnight state, different market conditions, date rollover in `utc_date` field). This takes 2 calendar days, costs nothing, and closes the most significant open evidence gap. The activation sprint that follows will be cleaner with this evidence in hand.

Option B is acceptable if Amit judges the session diversity requirement to be overly conservative given the architecture's determinism. If Option B is chosen, the activation window must be intraday, monitored continuously, with rollback primed.

Options C and D can proceed in parallel with continued observation (they do not delay the observation window).

---

## 5. Activation Prerequisites

The following must all be satisfied before any flag flip is executed:

| # | Prerequisite | Status |
|---|-------------|--------|
| 1 | Full test suite passes | Not yet run post Sprint 7H (documentation only) |
| 2 | `validate_intelligence_files.py` passes (40/40 or higher) | Pass — 40/40 current |
| 3 | Smoke tests pass (9/9 or higher) | Pass — 9/9 current |
| 4 | `current_manifest.json` validates (`validation_status=pass`) | Pass |
| 5 | `active_opportunity_universe.json` validates (`validation_status=pass`) | Pass |
| 6 | `publisher_run_log.jsonl` validates (all lines pass) | Pass — 10/10 |
| 7 | Observer gate = `validation_only_stable` | Pass — met |
| 8 | Session diversity ≥ 3 UTC dates (Option A) | **Not met — 1 session** |
| 9 | Rollback procedure tested (flag → False → scanner resumes) | Not tested |
| 10 | Fail-closed condition tested (stale manifest → Track A blocked) | Not tested in live context |
| 11 | SNDK/WDC/IREN quota decision made | Acknowledged; decision pending |
| 12 | 208 scanner-only removal acknowledged by Amit | Acknowledged in Sprint 7D |
| 13 | Amit explicit approval recorded | **Pending** |

---

## 6. Activation Window Design

### Recommended Window

- **Market session:** Regular US market hours (09:30–16:00 ET)
- **Day:** First active market day after all prerequisites are met
- **Duration:** Single intraday session (1 day maximum for initial controlled activation)
- **Mode:** Paper account only (DUP481326) — no live order execution

### Monitoring During Activation

Every scan cycle during the activation window, confirm in logs:

```
[handoff_wiring] flag_state=True — loading production handoff...
[handoff_wiring] candidate_source=handoff_reader universe=50 symbols
```

And confirm absence of:
```
[handoff_wiring] fail_closed_reason=...
```

**Monitoring frequency:** Every scan cycle (bot cycles every N minutes per config). Real-time log tailing required during the activation window. No unattended activation.

**Logs to monitor:**

| Log | What to watch for |
|-----|-------------------|
| `bot_trading.py` clog HANDOFF channel | `fail_closed_reason` non-null |
| `bot_trading.py` clog SCAN channel | `candidate_source=handoff_reader` confirmed each cycle |
| `data/live/handoff_publisher_observation_report.json` | `readiness_gate` stays `validation_only_stable` |
| `data/live/publisher_run_log.jsonl` | `validation_status=pass` on every line |
| `data/heartbeats/handoff_publisher.json` | `fail_closed_reason=null` each cycle |

### Approval Owner

Amit. No activation proceeds without explicit in-session approval. No autonomous activation.

### Rollback Triggers

Immediate rollback if any of the following occur:

- `[handoff_wiring] fail_closed_reason=` appears in any scan cycle log
- Candidate count drops to 0
- Any executable candidate detected in handoff universe
- Any order_instruction detected in handoff universe
- Publisher freshness expires (manifest age > 1200s)
- Any unexpected Track A entry that cannot be attributed to a governed candidate
- Any unexpected Apex input change
- Scanner output diverges from expected post-rollback
- Amit instructs rollback for any reason

---

## 7. Rollback Design

### Procedure

1. Set `enable_active_opportunity_universe_handoff = False` in `config.py` (single line change)
2. Restart the bot process
3. Confirm in first post-restart log: `Building dynamic universe (Alpaca screening)...`
4. Confirm absence of `[handoff_wiring] flag_state=True` in logs
5. Log rollback reason in session log

### What Is Preserved

- `data/live/current_manifest.json` — preserved, not deleted
- `data/live/active_opportunity_universe.json` — preserved, not deleted
- `data/live/publisher_run_log.jsonl` — preserved, not deleted
- `.fail_*.json` diagnostics — preserved, not deleted
- `data/live/handoff_publisher_observation_report.json` — preserved, not deleted
- All bot_trading.py position state — unaffected

### What Is Not Required

- No code revert (wiring remains in `bot_trading.py`, gate-flagged off)
- No file deletion
- No database rollback

### Scanner Fallback Confirmation

After rollback, the first scan cycle log must contain:

```
Building dynamic universe (Alpaca screening)...
```

And must NOT contain:

```
[handoff_wiring] flag_state=True
```

This confirms the scanner path resumed. If `[handoff_wiring] flag_state=True` appears after rollback, the config change did not propagate — investigate before proceeding.

---

## 8. Go/No-Go Criteria

### Go Criteria (all must be true)

| # | Criterion |
|---|-----------|
| G1 | Full test suite passes (all tests, not just smoke) |
| G2 | `validate_intelligence_files.py` passes (40/40 or higher) |
| G3 | Smoke passes (9/9 or higher) |
| G4 | No new test failures since last clean suite run |
| G5 | Publisher freshness SLA met at activation time |
| G6 | `current_manifest.json` validates (`validation_status=pass`) |
| G7 | `active_opportunity_universe.json` validates (`validation_status=pass`) |
| G8 | Observer gate = `validation_only_stable` at activation time |
| G9 | All 13 safety invariants clean |
| G10 | Rollback procedure tested successfully (flag → False → scanner resumes) |
| G11 | Session diversity criterion met (≥3 distinct UTC sessions) — if Option A adopted |
| G12 | SNDK/WDC/IREN quota decision documented |
| G13 | Amit explicit in-session approval recorded |

### No-Go Criteria (any one triggers abort)

| # | Condition |
|---|-----------|
| N1 | Manifest stale (`age > sla_stale_threshold_seconds = 900s`) |
| N2 | Active universe stale |
| N3 | Candidate count = 0 |
| N4 | Any executable candidate detected (`executable=true` in any candidate) |
| N5 | Any order_instruction detected (non-null `order_instruction` field) |
| N6 | Any safety invariant non-false |
| N7 | Any fail-closed bug in test suite |
| N8 | Scanner fallback occurs while `handoff=True` |
| N9 | Apex prompt, risk logic, order logic, or execution changed unexpectedly |
| N10 | Any unresolved operational concern remains at activation time |
| N11 | Observer gate is not `validation_only_stable` at activation time |
| N12 | Rollback test fails or scanner path does not resume after rollback |
