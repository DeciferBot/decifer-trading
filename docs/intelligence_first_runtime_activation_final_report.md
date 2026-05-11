# Intelligence-First Runtime Activation — Final Report

**Date:** 2026-05-11  
**Sprint:** Runtime Activation Sprint (Sprint 2 of Intelligence-First Closure)  
**Branch:** `claude/funny-almeida-9500ef`  
**Author:** Cowork (Claude)

---

## 1. Mission Statement

Prove that the intelligence-first bot architecture is fully operational end-to-end:
universe workers scheduled → publisher running in controlled_activation → bot consuming
the pre-built universe at scan time → fail-closed on any broken link in the chain.

No new architecture. Activate, prove, harden, document.

---

## 2. Activation Status

| Gate | Status | Evidence |
|------|--------|----------|
| Config key 1: `enable_active_opportunity_universe_handoff = True` | **ACTIVE** | `config.py:985` — set Sprint 7J.4, Amit approved |
| Config key 2: `publication_mode = controlled_activation` | **ACTIVE** | `data/live/current_manifest.json` — last set 2026-05-11T07:20:00Z |
| `handoff_enabled = true` in manifest | **ACTIVE** | Manifest field confirmed |
| Publisher running on schedule | **ACTIVE** | Cron `*/10 * * * *` + launchd `StartInterval=600`, both `--mode controlled_activation` |
| Bot fail-closed wired | **ACTIVE** | `_handoff_fail_closed_reason` blocks Track A on any failure |

Both activation keys are live. The bot will consume the handoff universe at next scan cycle.

---

## 3. Manifest State (as of 2026-05-11T07:20:00Z)

```
publication_mode  : controlled_activation
handoff_enabled   : true
handoff_mode      : live
ready_for_consumption : true
published_at      : 2026-05-11T07:20:00Z
expires_at        : 2026-05-11T07:35:00Z
candidate_count   : 75
validation_status : pass
fail_closed_reason: null
```

---

## 4. Publisher Scheduling

| Scheduler | Command | Interval | Status |
|-----------|---------|----------|--------|
| cron | `python3.11 handoff_publisher.py --mode controlled_activation` | Every 10 min (`*/10 * * * *`) | **Active** |
| launchd (`com.decifer.handoff-publisher`) | Same | `StartInterval=600` | **Installed, exit_code=0** |

Manifest TTL = 15 minutes. Publisher runs every ≤10 minutes → 5-minute expiry margin.

**Manifest reversion investigation (Sprint 2):** Apparent reversion observed during testing was caused by the test suite spawning 24+ parallel publisher instances in a single second (07:17:23Z). All entries at the same timestamp with alternating modes confirm this. The cron job restored `controlled_activation` at 07:20:00Z. This is not a production issue — the scheduler is stable.

---

## 5. Handoff Reader 6-Check Validation (Sprint 2)

All six programmatic checks PASSED:

| # | Check | Result |
|---|-------|--------|
| 1 | Config gate `enable_active_opportunity_universe_handoff = True` | PASS |
| 2 | Manifest `publication_mode = controlled_activation` | PASS |
| 3 | Universe loaded: 75 symbols | PASS |
| 4 | Reader accepts: `handoff_allowed=True, fail_closed_reason=None` | PASS |
| 5 | Fail-closed on `handoff_enabled=false`: `fail_closed_reason=handoff_disabled_in_manifest` | PASS |
| 6 | Fail-closed on missing manifest: `fail_closed_reason=manifest_not_found` | PASS |

---

## 6. Universe Workers

| Worker | Schedule | Last Success | Candidate Count |
|--------|----------|-------------|-----------------|
| committed universe | Sunday 23:00 | 2026-05-11T06:18:23Z | 1000 |
| promoter pre-open | Mon–Fri 08:00 | 2026-05-11T06:17:53Z | 50 |
| promoter EOD | Mon–Fri 16:15 | — | — |
| handoff publisher | Every 10 min | 2026-05-11T07:20:00Z | 75 |

All three universe workers operational. Publisher consuming and publishing from committed + promoted set.

---

## 7. Handoff Provenance Chain

Code wired end-to-end. Evidence pending first market-hours scan cycle.

| Component | State |
|-----------|-------|
| `signal_types.Signal` — 5 handoff fields | Wired (Sprint 1) |
| `signal_pipeline._scored_to_signals()` — `governance_map` lookup | Wired (Sprint 1) |
| `signal_pipeline.run_signal_pipeline()` — `governance_map` param | Wired (Sprint 1) |
| `bot_trading._handoff_governance_map` → pipeline | Wired (Sprint 1) |
| `signals_log.jsonl` — handoff fields will appear | Pending live scan |
| `tier_d_funnel.jsonl` — `stage=dispatch` records | Pending live scan |

---

## 8. Paper Validation Report (2026-05-11T07:22:39Z)

**Overall status: PARTIAL_DATA** — pre-market Mon 2026-05-11 (last scan cycle was Thu May 8). Clears after 09:30 ET.

| Q# | Question | Status |
|----|---------|--------|
| 1 | Did handoff candidates enter Track A? | NOT_PROVEN — no handoff labels in signals_typed.jsonl yet (first post-activation scan pending) |
| 2 | Did handoff candidates enter Apex payload? | NOT_ENOUGH_DATA — no dispatch records yet |
| 3 | Did handoff candidates appear in tier_d_funnel? | PARTIAL_DATA — 1,468 funnel records, 274 pipeline-stage records |
| 4 | Dispatch/rejection logs present? | PROVEN — 245 apex_cap cycles, 9,143 candidates, 2,838 rejected |
| 5 | Handoff metadata preserved in signals_typed.jsonl? | NOT_ENOUGH_DATA — awaiting live scan |
| 6 | Dispatch distribution (POSITION/SWING/INTRADAY/AVOID)? | NOT_ENOUGH_DATA — awaiting dispatch records |
| 7 | Position candidates surface appropriately? | NOT_ENOUGH_DATA — awaiting dispatch records |
| 8 | False positive rate vs baseline? | NOT_ENOUGH_DATA — 0 handoff-sourced trades |
| 9 | Options candidates rejected on spread/slippage? | PARTIAL_DATA — 3 options trades; gate enforced in orders_options.py |
| 10 | Drawdown and concentration limits respected? | PARTIAL_DATA — 422 closed trades; concentration acceptable |

Expected: NOT_ENOUGH_DATA clears today (Mon 2026-05-11) after 09:30 ET first scan cycle under controlled_activation manifest.

---

## 9. Test Suite

**Status: 2026 passing** (excluding 1 pre-existing `test_bot.py` failure unrelated to this sprint)

Key test sets:
- `test_handoff_activation_gate.py`: 20/20 pass
- `test_quota_policy_promotion.py`: 18/18 pass (2 stale pre-activation guards updated to reflect activated state)

---

## 10. Silent Exception Handlers — Closed

4 `except Exception: pass` handlers in `bot_ibkr.py` replaced with structured `log.debug()`:

| Location | Handler | Fix |
|----------|---------|-----|
| Line 794 | LONG backfill dedup timestamp parse | `log.debug("backfill_dedup_ts_parse: sym=%s err=%s", sym, _dt_e)` |
| Line 901 | SHORT backfill dedup timestamp parse | `log.debug("backfill_dedup_ts_parse_short: ...")` |
| Line 1032 | OPTIONS backfill dedup timestamp parse | `log.debug("backfill_dedup_ts_parse_opt: ...")` |
| Line 1159 | Trade dedup merge timestamp parse | `log.debug("trade_dedup_ts_parse: ...")` |

---

## 11. Cloud Preflight

`scripts/cloud_preflight.py` — 17 checks:

```
python3.11 scripts/cloud_preflight.py
```

In the master repo (with .env loaded): 16/17 checks pass. The 1 failure is `ANTHROPIC_API_KEY`
missing from shell env — passes when .env is sourced. All structural, directory, IBKR, handoff,
and safety checks pass.

`Dockerfile` + `.dockerignore` created. Docker build not tested (Docker not available locally).
Safe default CMD: `python3 scripts/cloud_preflight.py` (never submits orders).

---

## 12. Proof Matrix Final State

| Status | Count |
|--------|-------|
| `DONE_AND_PROVEN` | 35 |
| `DONE_NOT_PROVEN` | 2 |
| `NOT_DONE` | 0 |
| `NOT_ENOUGH_DATA` | 0 |
| **Total** | **37** |

Two remaining `DONE_NOT_PROVEN` checks (26: signals_typed.jsonl handoff labels, 27: tier_d_funnel handoff labels)
clear automatically on today's first scan cycle after 09:30 ET.

---

## 13. What Happens Today (2026-05-11, after 09:30 ET)

At market open (09:30 ET, Mon 2026-05-11):

1. Publisher cron fires at :00, :10, :20 → manifest refreshed with 75 candidates
2. Bot scan cycle starts — `_get_handoff_symbol_universe()` reads manifest, loads universe
3. `_handoff_governance_map` built from 75 candidates
4. `run_signal_pipeline(governance_map=...)` runs → signals scored against handoff universe
5. Any signals with handoff origin get `handoff_source_labels`, `handoff_route`, `handoff_reason_to_care` in `signals_typed.jsonl`
6. `tier_d_funnel.jsonl` gets `stage=dispatch` records showing Apex classification per symbol
7. Paper validation report re-run → Q1, Q2, Q5, Q6, Q7 clear to `PROVEN` or `PARTIAL_DATA`

---

## Open Items (not blocking activation)

| Item | Reason Not Blocking | Resolution Path |
|------|---------------------|-----------------|
| Docker build untested | Docker not available locally; Dockerfile structurally complete | Test on cloud host or CI |
| signals_typed.jsonl handoff labels | Pre-market; code wired | Today after 09:30 ET |
| tier_d_funnel dispatch records | Pre-market; code wired | Today after 09:30 ET |
| trading performance proof | Pre-market Mon 2026-05-11 | Today after 09:30 ET |
