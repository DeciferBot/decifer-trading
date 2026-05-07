# Intelligence-First Production Handoff — Cutover Readiness Checklist

**Sprint:** 7A
**Status:** Not started — checklist items must be completed before `enable_active_opportunity_universe_handoff = True` is approved
**Created:** 2026-05-06

This checklist must be fully resolved before any production handoff is approved. Each item must be checked off by Amit explicitly. "Waived" items must include a written waiver reason.

---

## Gate Summary

| Gate | Status | Notes |
|------|--------|-------|
| Advisory reviewer gate | `advisory_ready_for_handoff_design` ✅ | Met 2026-05-06 (35 records, 1 session) |
| Sprint 7A design complete | ✅ | This document and associated docs created |
| Sprint 7B implementation | Not started | Required before any checklist item below can be checked |
| Amit approval of Sprint 7B | Not started | |

---

## 1. Advisory Evidence Reviewed

- [ ] **1.1** Advisory log review produced and read by Amit
- [ ] **1.2** All 22 report fields reviewed
- [ ] **1.3** All 9 monitoring questions answered across ≥3 distinct sessions
- [ ] **1.4** Safety invariants confirmed clean across all records (production_decision_changed=0, apex_input_changed=0, live_output_changed=false)

**Status:** Partially met — 35 records reviewed, 1 session only. Session diversity not yet satisfied (see item 2).

---

## 2. Session Diversity Satisfied or Waived

- [ ] **2.1** ≥3 distinct UTC calendar sessions observed in advisory log, OR
- [ ] **2.2** Session diversity requirement formally waived by Amit with written reason

**Status:** 1 session observed (2026-05-06). Session threshold not yet met. Waiver not yet granted.

**Note:** The records threshold (≥10) is met. Session diversity provides evidence that the advisory system behaves consistently across different market conditions and bot restart cycles, not just a single session.

---

## 3. Unresolved Current Candidate Policy Approved

- [ ] **3.1** Recommended policy (Option B — approved source label required) reviewed by Amit
- [ ] **3.2** Policy formally approved or alternative selected
- [ ] **3.3** Policy reflected in Sprint 7B handoff reader implementation
- [ ] **3.4** Test Group 4 (candidate field tests) written and passing for chosen policy

**Status:** Policy designed (Sprint 7A). Awaiting Amit approval.

**Policy summary:** Symbols are admitted only if they appear in the handoff file with a valid `reason_to_care` and approved `source_labels`. Symbols in the current scanner pool with no handoff file entry are not admitted until the Universe Builder covers them.

---

## 4. Missing Shadow Candidate Policy Approved

- [ ] **4.1** 23 stable missing shadow symbols reviewed (VRT, ETN, PWR, CEG, XLU, TSM, AVGO, ASML, SMH, BAC, WFC, XLF, SLB, XLE, LMT, NOC, RTX, GD, ITA, QUAL, XLP, XLV, SPLV)
- [ ] **4.2** Policy formally approved: eligible if Universe Builder assigns them valid fields; no scanner presence required
- [ ] **4.3** Universe Builder confirmed to cover these symbols in the handoff file before Phase 2

**Status:** Policy designed (Sprint 7A). Awaiting Amit approval.

---

## 5. Tier D Policy Approved

- [ ] **5.1** Tier D → source label policy reviewed by Amit
- [ ] **5.2** REG-001 (`apex_cap_score.py`) retirement timeline reviewed
- [ ] **5.3** Structural quota cap (20 slots) retention decision confirmed (no change in Sprint 7A–7B)
- [ ] **5.4** Test Group 11 (Tier D source label tests) written and passing

**Status:** Policy designed (Sprint 7A). Structural cap retained at 20. REG-001 retirement condition advances when production handoff is stable. Awaiting Amit approval.

---

## 6. Route Vocabulary Policy Approved

- [ ] **6.1** Route disagreement breakdown reviewed (63% vocabulary, 37% meaningful)
- [ ] **6.2** `manual_conviction` route vocabulary harmonisation approach approved
- [ ] **6.3** `intraday_swing` / `swing` normalisation table defined and approved
- [ ] **6.4** `intraday_swing → watchlist` disagreement handling policy approved
- [ ] **6.5** Test Group 12 (route integrity tests) written and passing

**Status:** Policy designed (Sprint 7A). Normalisation implementation deferred to Sprint 7B.

---

## 7. Fail-Closed Tests Passing

- [ ] **7.1** Test Group 5 (fail-closed tests) implemented and passing
- [ ] **7.2** All fail-closed triggers produce `candidates = []` with log entry
- [ ] **7.3** No fallback to scanner, LLM, raw news, or broad scan on any fail-closed path
- [ ] **7.4** PM Track B path confirmed unaffected by all fail-closed triggers

**Status:** Not started. Tests not yet implemented (Sprint 7B).

---

## 8. Full Suite Passing or Critical Failures Waived

- [ ] **8.1** Full test suite run against Sprint 7B implementation
- [ ] **8.2** All new Sprint 7B tests passing (Test Groups 1–17)
- [ ] **8.3** Intelligence regression suite (Day2–Sprint6C) passing
- [ ] **8.4** Smoke suite (4 tests) passing
- [ ] **8.5** Pre-existing failures from Sprint 3 baseline (30 failures) documented and waived by Amit, OR resolved
- [ ] **8.6** No new failures introduced by Sprint 7B implementation

**Status:** Not started (Sprint 7B).

---

## 9. Production Simplification Audit Updated

- [ ] **9.1** Sprint 7A section added to `docs/intelligence_first_production_simplification_audit.md` ✅
- [ ] **9.2** Sprint 7B section added after implementation (all new files classified)
- [ ] **9.3** Handoff reader module classified as `production_runtime`
- [ ] **9.4** No new `deprecated_ready_to_remove` files created by Sprint 7B without explicit approval

**Status:** Sprint 7A section added. Sprint 7B section deferred to Sprint 7B.

---

## 10. Retirement Register Updated

- [ ] **10.1** Sprint 7A entries added to `docs/intelligence_first_retirement_register.md` ✅
- [ ] **10.2** REG-002 (`scanner.py` universe construction) retirement timeline updated after Phase 5 approval
- [ ] **10.3** REG-001 (`apex_cap_score.py`) retirement condition status updated
- [ ] **10.4** No entries removed from retirement register

**Status:** Sprint 7A entries added. REG-002 and REG-001 retirement conditions not yet met.

---

## 11. Rollback Tested

- [ ] **11.1** `enable_active_opportunity_universe_handoff = False` fully restores pre-handoff scanner path
- [ ] **11.2** Flag off requires no code change and no restart
- [ ] **11.3** Test Group 15 (rollback flag tests) written and passing
- [ ] **11.4** Rollback tested in paper environment before production cutover

**Status:** Not started (Sprint 7B).

---

## 12. Cloud Runtime Plan Defined

- [ ] **12.1** Deployment environment for `active_opportunity_universe.json` defined (local file, S3, GCS, or mounted volume)
- [ ] **12.2** File freshness SLA defined (Universe Builder must write at least every N minutes during market hours)
- [ ] **12.3** File monitoring alert defined (stale file → alert before bot reaches fail-closed)
- [ ] **12.4** Advisory log retention policy defined (`advisory_runtime_log.jsonl` rotation or forwarding)
- [ ] **12.5** Cloud deployment of Universe Builder scheduled job defined (runs before market open)

**Status:** Not started. Cloud runtime plan is a prerequisite for Phase 4 (controlled production switch), not Phase 1–2.

---

## 13. Log Retention Plan Defined

- [ ] **13.1** `advisory_runtime_log.jsonl` retention policy defined (max size, rotation interval, or log aggregator forwarding)
- [ ] **13.2** Handoff reader logs forwarded to structured log aggregator (or local rotation confirmed)
- [ ] **13.3** `advisory_log_review.json` archival policy defined (keep last N reviews, or archive to cold storage)
- [ ] **13.4** Log retention plan does not block Phase 1–3 (acceptable to define before Phase 4)

**Status:** Not started. Retention policy required before cloud deployment.

---

## 14. Owner Approval Recorded

- [ ] **14.1** Sprint 7A design document reviewed and approved by Amit
- [ ] **14.2** Handoff test plan reviewed and approved by Amit
- [ ] **14.3** Sprint 7B implementation plan approved by Amit before implementation starts
- [ ] **14.4** Phase 1 entry approved by Amit (file validation only, no consumption)
- [ ] **14.5** Phase 2 entry approved by Amit (paper path only)
- [ ] **14.6** Phase 3 entry approved by Amit (dry-run candidate source)
- [ ] **14.7** Phase 4 entry approved by Amit (controlled production switch)
- [ ] **14.8** Phase 5 entry approved by Amit (retirement of scanner-led path)
- [ ] **14.9** All approval decisions recorded with date in this document or session log

**Status:** Items 14.1–14.2 pending Amit review of Sprint 7A output. All Phase entries not yet reached.

---

## Approval Record

| Item | Approved | Date | Notes |
|------|----------|------|-------|
| Sprint 7A design | — | — | |
| Sprint 7B implementation | — | — | |
| Phase 1 entry | — | — | |
| Phase 2 entry | — | — | |
| Phase 3 entry | — | — | |
| Phase 4 entry | — | — | |
| Phase 5 entry | — | — | |
| Session diversity waiver (if applicable) | — | — | |
| Pre-existing test failure waiver | — | — | |
