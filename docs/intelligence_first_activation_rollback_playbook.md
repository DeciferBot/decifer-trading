# Intelligence-First Activation and Rollback Playbook

**Sprint:** 7H.1 — Operations readiness
**Status:** Pre-activation playbook. Activation flag is False. Do not execute Section 3 until Amit approves controlled activation sprint and all checklist items in `docs/intelligence_first_handoff_activation_checklist.md` are verified.
**Classification:** Advisory/design document. No production code changed.
**Reference:** See `docs/intelligence_first_handoff_activation_checklist.md` for the pre-activation checklist. See `docs/intelligence_first_cloud_deployment_runbook.md` for infrastructure context.

---

## 1. Pre-Activation Conditions

All of the following must be true before activation begins. These are not aspirational — they are hard gates. If any condition is false, stop.

| # | Condition | Verification |
|---|-----------|-------------|
| 1.1 | `enable_active_opportunity_universe_handoff = False` in `config.py` | `grep enable_active_opportunity_universe_handoff config.py` |
| 1.2 | `handoff_enabled = false` in `data/live/current_manifest.json` | Read manifest, check field |
| 1.3 | `publisher_run_log.jsonl` has ≥10 successful runs (`validation_status=pass`) | `wc -l data/live/publisher_run_log.jsonl` + inspect |
| 1.4 | `distinct_utc_sessions ≥ 3` in observer report | Run `python3 handoff_publisher_observer.py` and read report |
| 1.5 | `readiness_gate = validation_only_stable` in observer report | Same run as 1.4 |
| 1.6 | `threshold_met = true`, `threshold_basis = distinct_sessions` in observer report | Same run as 1.4 |
| 1.7 | `validate_intelligence_files.py` exits 0 with 40/40 pass | `python3 scripts/validate_intelligence_files.py` |
| 1.8 | `pytest -m smoke -q` exits 0, all 9 smoke tests pass | `python3 -m pytest -m smoke -q` |
| 1.9 | Bot is running on paper account DUP481326 | Confirm in TWS; confirm in `config.py` |
| 1.10 | No live account active | Confirm no live IBKR account is wired |
| 1.11 | Amit has reviewed the activation checklist and set approval in Section 14 | `docs/intelligence_first_handoff_activation_checklist.md` Section 14 |
| 1.12 | Manifest age < 600s at time of activation | Check `manifest_age_seconds` from observer report immediately before flag flip |
| 1.13 | No open positions in an uncertain state (stale EXITING, missing metadata) | Check bot log + dashboard before activating |

**If any condition is not met:** stop. Do not flip the flag. Resolve the condition, re-run verification, and restart from 1.1.

---

## 2. Activation Window

### Definition

The activation window begins when `enable_active_opportunity_universe_handoff` is set to `True` and ends when the flag is set back to `False` (rollback) or when Amit explicitly closes the window after confirming success.

### Recommended window design

- Activate at the **start of a scan cycle**, not mid-cycle.
- Minimum window: 2 complete scan cycles (confirm handoff wiring fires twice).
- Maximum recommended initial window: 1 trading session.
- Do not activate within 30 minutes of market close (insufficient time for a clean observation window).
- Do not activate if the publisher has not run successfully within the last 15 minutes (manifest may be near-stale at the point the bot reads it).

### Who may activate

- Amit only.
- No automated activation.
- No scheduled activation.
- If Amit is unavailable, do not activate. There is no escalation path.

### Monitoring during window

During the activation window, the following must be monitored continuously:

| Signal | Tool | Alert threshold |
|--------|------|----------------|
| `[handoff_wiring] flag_state=True` appears in bot log | Bot log tail | Must appear within first cycle post-activation |
| `candidate_source=handoff_reader` in Apex Track A input | Bot log | Must appear; absence = wiring failure |
| `_handoff_fail_closed_reason` non-null | Bot log | Any non-null value triggers immediate rollback |
| Manifest freshness | Observer report | Age > 900s triggers rollback consideration |
| `fail_closed_reason` in heartbeat | `data/heartbeats/handoff_publisher.json` | Non-null = publisher degraded; evaluate rollback |
| PM Track B continues to fire | Bot log | Track B must run independently of handoff flag |

---

## 3. Activation Steps

Execute in exact order. Do not skip. Record timestamp for each step.

| Step | Action | Verification |
|------|--------|-------------|
| 3.1 | Run `python3 handoff_publisher.py` — fresh publish cycle | Confirm `publish_cycle=success` in output |
| 3.2 | Run `python3 handoff_publisher_observer.py` — confirm stable gate | Confirm `readiness_gate=validation_only_stable` |
| 3.3 | Confirm manifest age < 600s in observer output | Read `manifest_age_seconds` |
| 3.4 | Open `config.py` — locate `enable_active_opportunity_universe_handoff` | Confirm value is `False` before edit |
| 3.5 | Set `enable_active_opportunity_universe_handoff = True` | Single-line change |
| 3.6 | Do NOT commit yet — wait for first cycle confirmation | See step 3.8 |
| 3.7 | If bot does not hot-reload config, restart bot: `supervisorctl restart live_trading_bot` or `docker-compose restart bot` | Confirm restart completes |
| 3.8 | Wait for first scan cycle to complete | Observe bot log |
| 3.9 | Confirm in bot log: `[handoff_wiring] flag_state=True` | Must be present |
| 3.10 | Confirm in bot log: `candidate_source=handoff_reader` | Must be present |
| 3.11 | Confirm in bot log: `_handoff_fail_closed_reason=None` | No fail-closed event |
| 3.12 | Confirm Track B continues to fire independently | Check Track B log entries |
| 3.13 | Record activation timestamp in `docs/intelligence_first_handoff_activation_checklist.md` Section 11 | |
| 3.14 | Commit the config change: `git commit -m "feat(config): activate handoff flag — controlled activation sprint"` | |
| 3.15 | Push to master | |

**If step 3.9 or 3.10 does not confirm within 2 cycles:** trigger rollback immediately. Do not wait.

**If `_handoff_fail_closed_reason` is non-null at any point:** trigger rollback immediately.

---

## 4. Rollback Steps

Execute rollback whenever any rollback trigger fires (see Section 5). Rollback is non-destructive — publisher continues running and all `data/live/` files are preserved.

| Step | Action | Verification |
|------|--------|-------------|
| 4.1 | Open `config.py` — locate `enable_active_opportunity_universe_handoff` | Confirm current value is `True` |
| 4.2 | Set `enable_active_opportunity_universe_handoff = False` | Single-line change |
| 4.3 | If bot does not hot-reload config, restart bot | Confirm restart completes |
| 4.4 | Wait for first scan cycle post-restart | Observe bot log |
| 4.5 | Confirm in bot log: `Building dynamic universe (Alpaca screening)...` | Scanner path restored |
| 4.6 | Confirm absence of `[handoff_wiring] flag_state=True` in post-rollback logs | No residual handoff state |
| 4.7 | Confirm Track A is running scanner candidates, not handoff candidates | Bot log: universe build from Alpaca |
| 4.8 | Keep `data/live/` files intact — do not delete any files | Preserve diagnostics |
| 4.9 | Preserve `publisher_run_log.jsonl` | Do not truncate; append-only log continues |
| 4.10 | Keep publisher running — it continues to publish; bot ignores manifest | Publisher is non-blocking when flag=False |
| 4.11 | Record rollback timestamp and reason in `docs/intelligence_first_handoff_activation_checklist.md` Section 12 | |
| 4.12 | Commit rollback: `git commit -m "fix(config): rollback handoff flag — [reason]"` | |
| 4.13 | Push to master | |
| 4.14 | Run `python3 -m pytest -m smoke -q` to confirm no regression | All 9 must pass |

---

## 5. Rollback Triggers

Any of the following triggers an **immediate rollback** — no delay, no investigation during the activation window:

| Trigger | Threshold | Severity |
|---------|-----------|---------|
| `_handoff_fail_closed_reason` non-null in any bot log line | Any single occurrence | **IMMEDIATE** |
| `candidate_source` absent from Apex Track A log for 2+ consecutive cycles | 2 consecutive cycles | **IMMEDIATE** |
| Manifest age > 1200s (SLA hard-expired) at time of handoff reader call | Single event | **IMMEDIATE** |
| Publisher `fail_closed_reason` non-null in heartbeat | Any single occurrence | **IMMEDIATE** |
| Any Apex Track A call with 0 candidates when handoff is active | Single event | **IMMEDIATE** |
| Bot enters EXITING state for any position and does not self-recover within 5 minutes | 5 min | Evaluate; likely rollback |
| Any order submitted via `execute_buy` / `execute_short` with metadata anomalies | Any single event | **IMMEDIATE** |
| Bot log contains any exception trace referencing `handoff_reader` or `handoff_candidate_adapter` | Any single event | Investigate; likely rollback |
| `Building dynamic universe (Alpaca screening)...` appears while flag=True | Any single event | **IMMEDIATE** (scanner fallback fired) |
| Observer gate drops to `validation_only_unstable` or `fix_publisher_before_flag_activation` | Any observation run | Evaluate; rollback if freshness-driven |
| Amit calls rollback for any reason | Immediate | **IMMEDIATE** |

**Deferred triggers** (investigate before deciding):

| Trigger | Action |
|---------|--------|
| Candidate count drops below 40 for 3+ consecutive cycles | Investigate publisher output; consider rollback |
| Any `.fail_*.json` created in `data/live/` during activation window | Inspect; if publisher cycle failure, rollback |
| Any test failure in smoke suite post-activation | Rollback; fix; re-qualify |

---

## 6. Post-Activation Review

Complete within 24 hours of activation close (success or rollback). These fields map to `docs/intelligence_first_handoff_activation_checklist.md` Section 13.

| # | Review Item | Acceptance criteria |
|---|-------------|-------------------|
| 6.1 | Total Track A scan cycles completed under handoff | Record count |
| 6.2 | Total fail-closed events during window | Must be 0 for success classification |
| 6.3 | Candidate count stability | 50 ±0 across all cycles |
| 6.4 | No executable candidate detected | Confirmed via manifest validation each cycle |
| 6.5 | No unexpected order instructions | Confirmed via adapter output |
| 6.6 | Scanner fallback never occurred | Confirmed by absence of `Building dynamic universe` while flag=True |
| 6.7 | PM Track B ran independently each cycle | Confirmed by Track B log entries |
| 6.8 | Publisher freshness SLA met throughout window | `sla_met=true` in each observer run |
| 6.9 | `validate_intelligence_files.py` re-run post-activation | Must exit 0 with 40/40 |
| 6.10 | Activation window result | `success` or `rollback` + rollback trigger if applicable |
| 6.11 | Lessons learned | Any unexpected behaviour, timing issues, or operational gaps |
| 6.12 | Recommendation for next activation window | Continue / extend window / deferred |

**Classification of activation result:**

| Result | Definition |
|--------|-----------|
| **Success** | All cycles completed without fail-closed events; candidate count stable; scanner fallback never fired; PM Track B independent; no rollback triggered |
| **Partial** | Activation ran but was rolled back for a non-critical reason (e.g. window timing, not a data integrity failure) |
| **Rollback** | A rollback trigger fired during the window; root cause diagnosed; documented in Section 12 of checklist |
| **Abort** | Activation never started — pre-activation conditions not met |
