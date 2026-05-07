# Intelligence-First Daily Operator Checklist

**Sprint:** 7H.1 — Operations readiness
**Status:** Pre-activation. Activation flag is False. Checklist applies to daily operations in validation-only observation mode and (when activated) in controlled activation mode.
**Classification:** Advisory/design document. No production code changed.
**Reference:** See `docs/intelligence_first_cloud_deployment_runbook.md` for infrastructure context. See `docs/intelligence_first_activation_rollback_playbook.md` for activation/rollback steps.

---

## Instructions

Run each section at the appropriate time. Mark result. Any FAIL result must be investigated before proceeding with that session. Do not proceed to activation if any pre-market check fails.

All commands assume working directory is the repo root (`/opt/decifer-trading/` or equivalent).

---

## Section 1 — Pre-Market (before market open, ideally 30–60 min before)

### 1a. Environment

| # | Check | Command | Expected |
|---|-------|---------|---------|
| 1.1 | Python imports healthy | `python3 -c "import anthropic, pandas, dash"` | No error |
| 1.2 | `.env` loaded | `python3 -c "import os; print(bool(os.getenv('ANTHROPIC_API_KEY')))"` | `True` |
| 1.3 | Config flags unchanged | `grep enable_active_opportunity_universe_handoff config.py` | Value matches expected state |
| 1.4 | Smoke tests pass | `python3 -m pytest -m smoke -q` | 9/9 pass |

### 1b. Publisher Health

| # | Check | Command | Expected |
|---|-------|---------|---------|
| 1.5 | Run publisher | `python3 handoff_publisher.py` | `publish_cycle=success` |
| 1.6 | Run observer | `python3 handoff_publisher_observer.py` | Exits 0 |
| 1.7 | Readiness gate | Read `data/live/handoff_publisher_observation_report.json` → `readiness_gate` | `validation_only_stable` (or `insufficient_observation` if pre-threshold) |
| 1.8 | Manifest fresh | Read `data/live/current_manifest.json` → `expires_at` | > 10 min from now |
| 1.9 | Fail-closed reason null | Read `data/heartbeats/handoff_publisher.json` → `fail_closed_reason` | `null` |
| 1.10 | No new .fail files | `ls data/live/.fail_*.json 2>/dev/null \| wc -l` | 0 (or same count as previous check if pre-existing) |

### 1c. Intelligence Files

| # | Check | Command | Expected |
|---|-------|---------|---------|
| 1.11 | Validator | `python3 scripts/validate_intelligence_files.py` | 40/40 pass, exit 0 |
| 1.12 | Shadow universe exists | `ls data/universe_builder/active_opportunity_universe_shadow.json` | File present |
| 1.13 | Run log accumulating | `wc -l data/live/publisher_run_log.jsonl` | Count ≥ previous day count |

### 1d. IBKR

| # | Check | Command / Method | Expected |
|---|-------|--------|---------|
| 1.14 | IBKR Gateway running | Check TWS/Gateway window or `ibcontroller` status | Running |
| 1.15 | Bot connects to IBKR | Bot log: search for `IB connected` | Present within 2 min of bot start |
| 1.16 | Paper account active | `config.py IBKR_PAPER_ACCOUNT` matches DUP481326 | Confirmed |

---

## Section 2 — During Market Hours (periodic checks, every 30–60 min)

### 2a. Publisher Freshness

| # | Check | Method | Alert if |
|---|-------|--------|---------|
| 2.1 | Manifest age | Read `manifest_age_seconds` from last observer report | > 900s |
| 2.2 | Publisher heartbeat | Read `last_success_age_seconds` from `data/heartbeats/handoff_publisher.json` | > 900s |
| 2.3 | Run log growing | `wc -l data/live/publisher_run_log.jsonl` | No new line in last 20 min |
| 2.4 | No new .fail files | `ls data/live/.fail_*.json 2>/dev/null \| wc -l` | Any increase |

### 2b. Bot Health (when handoff flag is False — validation-only mode)

| # | Check | Method | Alert if |
|---|-------|--------|---------|
| 2.5 | Bot scan cycles running | Bot log: `Starting scan cycle` entries | No new entry in > 15 min |
| 2.6 | Handoff wiring dormant | Bot log: `flag_state=False` | `flag_state=True` unexpected |
| 2.7 | Track A running scanner path | Bot log: `Building dynamic universe (Alpaca screening)...` | Absent while flag=False |
| 2.8 | Track B running | Bot log: `Track B PM review` | Absent for > 1 scan cycle |
| 2.9 | No order anomalies | Bot log: no `UNKNOWN trade_type` entries | Any `UNKNOWN` entry |

### 2c. Bot Health (when handoff flag is True — controlled activation mode)

| # | Check | Method | Alert if |
|---|-------|--------|---------|
| 2.10 | Handoff wiring active | Bot log: `[handoff_wiring] flag_state=True` | Absent in post-activation cycle |
| 2.11 | Candidate source confirmed | Bot log: `candidate_source=handoff_reader` | Absent in any cycle |
| 2.12 | No fail-closed events | Bot log: `_handoff_fail_closed_reason=None` | Any non-null value → **ROLLBACK** |
| 2.13 | Candidate count stable | Bot log: candidate count for Track A | < 40 for 3+ cycles → investigate |
| 2.14 | Track B independent | Bot log: Track B runs each cycle | Absent in any cycle |
| 2.15 | No scanner fallback | Bot log: absence of `Building dynamic universe` while flag=True | Any occurrence → **ROLLBACK** |

---

## Section 3 — Post-Market (after market close)

### 3a. Session Summary

| # | Check | Method | Expected |
|---|-------|--------|---------|
| 3.1 | Run publisher (final cycle) | `python3 handoff_publisher.py` | `publish_cycle=success` |
| 3.2 | Run observer (final cycle) | `python3 handoff_publisher_observer.py` | Exits 0 |
| 3.3 | Run log line count today | `grep -c "$(date -u +%Y-%m-%d)" data/live/publisher_run_log.jsonl` | ≥ N (N = expected cycles per session) |
| 3.4 | All safety invariants hold | Read observer report → `all_safety_invariants_hold` | `true` |
| 3.5 | `live_output_changed` | Observer report | `false` |
| 3.6 | Validator clean | `python3 scripts/validate_intelligence_files.py` | 40/40, exit 0 |

### 3b. Diagnostics Rotation

| # | Action | When |
|---|--------|------|
| 3.7 | Count `.fail_*.json` in `data/live/` | Daily |
| 3.8 | If count > 20, archive oldest to `data/live/diagnostics/` and remove from `data/live/` | When > 20 |
| 3.9 | Check `data/logs/` size | Daily |
| 3.10 | Rotate bot log if > 100 MB (compress, rename with date suffix) | When > 100 MB |
| 3.11 | Check `publisher_run_log.jsonl` line count | Daily |
| 3.12 | No rotation of `publisher_run_log.jsonl` until Amit approves explicit policy | See `docs/intelligence_first_log_retention_and_diagnostics_policy.md` |

### 3c. Activation Review (when flag was True today)

| # | Check | Expected |
|---|-------|---------|
| 3.13 | Count Track A cycles under handoff | Record in activation checklist Section 13 |
| 3.14 | Count fail-closed events | Must be 0 |
| 3.15 | Confirm scanner fallback never fired | Log review |
| 3.16 | Confirm PM Track B independent | Log review |
| 3.17 | Assess next-day activation decision | Continue / rollback / extend observation |

---

## Section 4 — Emergency Procedures

### 4a. Publisher Down (manifest going stale)

| Step | Action |
|------|--------|
| 1 | Check `data/heartbeats/handoff_publisher.json` → `fail_closed_reason` |
| 2 | Check `data/live/.fail_*.json` for most recent failure — read error field |
| 3 | If recoverable (e.g. API timeout): re-run `python3 handoff_publisher.py` manually |
| 4 | If flag is True and manifest age > 1200s: rollback immediately (see `docs/intelligence_first_activation_rollback_playbook.md` Section 4) |
| 5 | If flag is False: publisher down is non-blocking; bot continues on scanner path |
| 6 | Log incident with timestamp and resolution |

### 4b. Bot Crash or Stuck Cycle

| Step | Action |
|------|--------|
| 1 | Check bot log for last scan cycle entry and exception trace |
| 2 | If in EXITING state: wait 5 min for self-recovery; if not recovered, investigate before any manual action |
| 3 | If flag is True: set `enable_active_opportunity_universe_handoff = False` before restart |
| 4 | Restart bot: `supervisorctl restart live_trading_bot` or `docker-compose restart bot` |
| 5 | Confirm `IB connected` in log within 2 min |
| 6 | Confirm scan cycle starts within 5 min |

### 4c. IBKR Gateway Disconnect

| Step | Action |
|------|--------|
| 1 | Bot reconnects automatically on disconnect — check log for `IB reconnected` or equivalent |
| 2 | If bot does not reconnect within 5 min: restart IBKR Gateway |
| 3 | Do not restart bot unless bot itself has crashed (reconnect is automatic) |
| 4 | If open positions exist: leave IBKR Gateway running through restart; do not force-close positions |

### 4d. Unexpected Flag State

| Step | Action |
|------|--------|
| 1 | If `flag_state=True` appears in logs but activation was not initiated by Amit: **stop immediately** |
| 2 | Set `enable_active_opportunity_universe_handoff = False` immediately |
| 3 | Investigate how the flag was changed (git log, process log) |
| 4 | Do not re-activate without Amit approval and full root cause diagnosis |

### 4e. Validator Failure

| Step | Action |
|------|--------|
| 1 | Read full validator output for specific file and error |
| 2 | Do not proceed with activation if validator fails |
| 3 | If flag is currently True and validator fails post-activation: rollback immediately |
| 4 | Fix the underlying cause; re-run validator before any next session |
