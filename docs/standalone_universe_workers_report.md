# Standalone Universe Workers — Sprint 7K.2 Report

**Branch:** `feat/standalone-universe-refresh-workers`
**Sprint:** 7K.1 (basic CLI) + 7K.2 (extended spec — evidence JSONL, ops plists, verification)
**Date completed:** 2026-05-09
**Test results:** 24 / 24 passing

---

## Files Changed

### New files

| File | Description |
|------|-------------|
| `worker_evidence.py` | Shared JSONL evidence module — appends one record per worker run to `data/runtime/universe_worker_evidence.jsonl` |
| `ops/launchd/com.decifer.universe-committed.plist` | Canonical launchd plist — Sunday 23:00 |
| `ops/launchd/com.decifer.universe-promoter-eod.plist` | Canonical launchd plist — Mon–Fri 16:15 |
| `ops/launchd/com.decifer.universe-promoter-preopen.plist` | Canonical launchd plist — Mon–Fri 08:00 |
| `deployment/com.decifer.universe-committed.plist` | Legacy location (Sprint 7K.1) — kept for reference |
| `deployment/com.decifer.universe-promoter-eod.plist` | Legacy location (Sprint 7K.1) — kept for reference |
| `deployment/com.decifer.universe-promoter-preopen.plist` | Legacy location (Sprint 7K.1) — kept for reference |
| `deployment/README.md` | Legacy installation guide (Sprint 7K.1) |
| `scripts/verify_standalone_workers.sh` | 6-section bash verification script |
| `docs/standalone_universe_workers.md` | Operations guide |
| `docs/parallel_runtime_universe_build_audit.md` | Parallel runtime architecture audit (Sprint audit) |
| `tests/test_worker_cli.py` | 24 targeted tests for CLI independence |

### Modified files

| File | What changed |
|------|-------------|
| `universe_committed.py` | Added `_main()` with argparse, `_write_heartbeat()`, `worker_evidence` calls, `sys.exit(_main())` |
| `universe_promoter.py` | Added `_main()` with argparse, `_write_heartbeat()`, `worker_evidence` calls, `sys.exit(_main())` |

---

## Worker Commands Added

```bash
# Committed universe (safe any time including weekends)
python3.11 universe_committed.py --run-once
python3.11 universe_committed.py --run-once --top-n 500

# Promoter universe (safe pre/post-market)
python3.11 universe_promoter.py --run-once

# As modules
python3.11 -m universe_committed --run-once
python3.11 -m universe_promoter --run-once
```

Exit codes: `0` = success, `1` = failure.

---

## Launchd Plist Paths Created

Canonical location (`ops/launchd/`):

| File | Schedule | Worker |
|------|----------|--------|
| `ops/launchd/com.decifer.universe-committed.plist` | Sunday 23:00 | `universe_committed.py --run-once` |
| `ops/launchd/com.decifer.universe-promoter-eod.plist` | Mon–Fri 16:15 | `universe_promoter.py --run-once` |
| `ops/launchd/com.decifer.universe-promoter-preopen.plist` | Mon–Fri 08:00 | `universe_promoter.py --run-once` |

Legacy location (`deployment/`) kept from Sprint 7K.1 — not canonical.

---

## Evidence and Log Paths

| Path | Type | Written by | Purpose |
|------|------|-----------|---------|
| `data/runtime/universe_worker_evidence.jsonl` | JSONL append | Both workers via `worker_evidence.py` | Full structured evidence per run |
| `data/heartbeats/universe_committed_worker.json` | JSON overwrite | committed worker | Quick status — last run result |
| `data/heartbeats/universe_promoter_worker.json` | JSON overwrite | promoter worker | Quick status — last run result |
| `/tmp/decifer-universe-committed.log` | stdout (launchd only) | OS | Terminal output when running under launchd |
| `/tmp/decifer-universe-committed.err` | stderr (launchd only) | OS | Error output when running under launchd |
| `/tmp/decifer-universe-promoter-eod.log` | stdout (launchd only) | OS | Terminal output when running under launchd |
| `/tmp/decifer-universe-promoter-preopen.log` | stdout (launchd only) | OS | Terminal output when running under launchd |

### Evidence record schema

```json
{
  "worker_name": "universe_committed_worker",
  "started_at": "2026-05-09T23:00:01.234Z",
  "finished_at": "2026-05-09T23:00:39.812Z",
  "duration_seconds": 38.58,
  "success": true,
  "failure_reason": null,
  "output_artifact_path": "data/committed_universe.json",
  "output_artifact_exists": true,
  "output_artifact_mtime": "2026-05-09T23:00:39.500Z",
  "output_artifact_age_seconds": 0.3,
  "run_mode": "run_once",
  "git_branch": "feat/standalone-universe-refresh-workers",
  "source": "standalone_cli",
  "live_output_changed": false,
  "broker_called": false,
  "order_placed": false,
  "symbol_count": 1000
}
```

---

## Tests Run and Results

```
tests/test_worker_cli.py — 24 passed in 3.39s
```

| Section | Tests | Result |
|---------|-------|--------|
| A. Import isolation (subprocess) | 2 | PASS |
| B. CLI smoke — returns 0 on success | 2 | PASS |
| C. CLI failure — returns 1 | 4 | PASS |
| D. Heartbeat written on success and failure | 4 | PASS |
| E. Idempotency | 2 | PASS |
| F. Output artifact written | 2 | PASS |
| G. Safety flags always false in heartbeat | 1 | PASS |
| H. JSONL evidence file written on success and failure | 7 | PASS |

---

## Independence Confirmations

### Committed universe refresh is independent of bot.py

**YES.** `universe_committed.py` has a standalone `_main()` entry point. It imports only:
`alpaca_data`, `config`, `worker_evidence`, and standard library modules. It does not import
`bot_trading`, `bot_ibkr`, `orders_core`, `risk`, or any execution module. Import isolation
is verified by `test_universe_committed_does_not_import_bot_modules` which runs in a clean
subprocess. Running `python3.11 universe_committed.py --run-once` with bot.py stopped exits 0
and writes `data/committed_universe.json`.

### Promoter universe refresh is independent of bot.py

**YES.** `universe_promoter.py` has a standalone `_main()` entry point with the same clean
import profile. The only runtime dependency is `data/committed_universe.json` — if that file
exists from any previous committed run, the promoter can run without bot.py present.
Verified by `test_universe_promoter_does_not_import_bot_modules`.

### Weekend / after-hours refresh is possible without bot.py

**YES** for committed, **YES** for promoter (given committed universe already written):

- `universe_committed.py` uses Alpaca `prior_close × prev_volume` snapshot data. Alpaca
  returns the last available regular-session values on weekends and outside market hours.
  The worker will succeed on a Saturday morning with no market open.
- `universe_promoter.py` reads `committed_universe.json` and fetches Alpaca snapshots.
  Alpaca snapshot data is available 24/7. The worker is safe pre- and post-market.
- Neither worker makes IBKR connections or checks market hours at startup.

The launchd plists in `ops/launchd/` enable OS-level scheduling independent of the bot process.
Once installed, Sunday 23:00 committed refresh and Mon–Fri 08:00/16:15 promoter runs will
fire even if bot.py is not running.

### Live trading behaviour was not changed

**CONFIRMED — no live trading behaviour changed.**

- No config flags modified or enabled.
- `enable_active_opportunity_universe_handoff` not touched (remains `True` from Sprint 7J.4).
- No intelligence_first_* flags changed (all remain `False`).
- `bot.py` `schedule.every()` registrations preserved as-is (fallback path unchanged).
- No IBKR order paths touched.
- No signal scoring, quota allocation, or route tagging logic modified.
- No Apex call paths modified.
- `worker_evidence.py` hardcodes `live_output_changed=False`, `broker_called=False`, `order_placed=False`.

---

## Remaining Gaps

| Gap | Priority | Notes |
|-----|----------|-------|
| launchd daemons not yet installed | HIGH | Manual `launchctl load` required — see `docs/standalone_universe_workers.md` |
| bot.py schedule fallback still active | LOW | Intentional until daemons confirmed over ≥2 live cycles |
| Sprint 7J.4 runtime consumption not yet confirmed | MEDIUM | Check `data/audit_log.jsonl` after next scan cycle — see verification script |
| `worker_evidence.py` not used by `handoff_publisher.py` | LOW | Handoff publisher has its own `publisher_run_log.jsonl` |
| No cloud-native scheduling | DEFERRED | Out of scope for Branch 1 |
| No staleness guard in handoff_reader | LOW | Bot does not fail loud if universe file is stale — deferred to separate sprint |

---

## Anti-Bloat Gate

| Question | Answer |
|----------|--------|
| 1. Does any new module import from `bot_trading`, `bot_ibkr`, `orders_*`, `risk*`, `apex_orchestrator`, `market_intelligence`, or `execution_agent`? | **No.** `worker_evidence.py` imports only `datetime`, `json`, `os`, `subprocess`. |
| 2. Does any new code path submit an order, open a position, or call the broker? | **No.** `broker_called` and `order_placed` are hardcoded `False` in every evidence record. |
| 3. Does any new code path change config flags, enable gated features, or modify `config.py`? | **No.** No config changes. |
| 4. Are the new `_main()` entry points the only new public surface added to the worker modules? | **Yes.** `_write_heartbeat()` is module-private (underscore). `worker_evidence.append_evidence()` and `read_latest()` are the only new public functions added system-wide. |
| 5. Do the launchd plists change any existing scheduling logic in bot.py? | **No.** bot.py `schedule` registrations are unchanged. Plists are OS-level; they don't modify bot.py. |
| 6. Does `worker_evidence.py` write to any path other than `data/runtime/` and `data/heartbeats/`? | **No.** Only those two directories. |
| 7. Do the new tests import production modules that could contaminate the test session? | **No.** Import isolation tests use subprocess. All other tests use `monkeypatch` and `tmp_path`. |
| 8. Is the `deployment/` legacy directory safe to leave in place? | **Yes.** It is documentation-only. No code reads from `deployment/`. The canonical location is `ops/launchd/`. |
