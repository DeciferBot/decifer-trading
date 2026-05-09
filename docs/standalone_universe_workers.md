# Standalone Universe Workers — Operations Guide

**Classification:** documentation only
**Sprint:** 7K.1 / 7K.2
**Status:** Live — workers are standalone-callable as of this sprint.

---

## What changed

Before this sprint, `universe_committed.py` and `universe_promoter.py` were scheduled
entirely inside `bot.py` via the `schedule` library. If `bot.py` was down at Sunday
23:00, the weekly committed universe refresh would silently not run.

This sprint extracted both workers into standalone-callable processes with:
- Proper CLI entry points (`_main()` + argparse)
- Structured evidence logs (JSONL append to `data/runtime/universe_worker_evidence.jsonl`)
- Heartbeat files (`data/heartbeats/*.json`) for quick status checks
- `launchd` plist templates in `ops/launchd/` for independent scheduling
- 38 passing tests proving independence from `bot.py`

`bot.py`'s `schedule.every()` registrations are preserved as fallback.

---

## Why this was needed

The committed universe is the "menu" for all Tier B promotion. If it goes stale,
the promoter has nothing to score from, and the universe_builder has no Tier B
input. One missed Sunday refresh degrades universe quality for the entire week.

The `schedule` library runs inside `bot.py`'s main loop — a single process with
many competing responsibilities. Making universe refresh process-independent reduces
fragility: the workers can run on any schedule, from any trigger, without bot.py.

---

## How to run committed worker manually

```bash
cd /path/to/decifer-trading

# Basic run (default is --run-once)
python3.11 universe_committed.py --run-once

# With explicit top-N override
python3.11 universe_committed.py --run-once --top-n 500

# As a module
python3.11 -m universe_committed --run-once
```

Exit codes: `0` = success, `1` = failure.

---

## How to run promoter worker manually

```bash
cd /path/to/decifer-trading

python3.11 universe_promoter.py --run-once

# The promoter requires a non-empty committed_universe.json to exist.
# If committed universe is stale, run committed first:
python3.11 universe_committed.py --run-once && python3.11 universe_promoter.py --run-once
```

---

## Where evidence is written

| File | Type | Written by | Purpose |
|------|------|-----------|---------|
| `data/runtime/universe_worker_evidence.jsonl` | JSONL append | Both workers | Full structured evidence — one record per run |
| `data/heartbeats/universe_committed_worker.json` | JSON overwrite | committed worker | Quick status check |
| `data/heartbeats/universe_promoter_worker.json` | JSON overwrite | promoter worker | Quick status check |

### Evidence record schema

```json
{
  "worker_name": "universe_committed_worker",
  "started_at": "2026-05-11T23:00:01.234Z",
  "finished_at": "2026-05-11T23:00:39.812Z",
  "duration_seconds": 38.58,
  "success": true,
  "failure_reason": null,
  "output_artifact_path": "data/committed_universe.json",
  "output_artifact_exists": true,
  "output_artifact_mtime": "2026-05-11T23:00:39.500Z",
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

### Reading the evidence file

```bash
# Most recent record
tail -1 data/runtime/universe_worker_evidence.jsonl | python3.11 -m json.tool

# All records for committed worker
grep "universe_committed_worker" data/runtime/universe_worker_evidence.jsonl

# All failures
python3.11 -c "
import json
with open('data/runtime/universe_worker_evidence.jsonl') as f:
    for line in f:
        r = json.loads(line)
        if not r['success']:
            print(r['started_at'], r['worker_name'], r['failure_reason'])
"
```

---

## Where stdout/stderr logs are written

When run manually, output goes to the terminal.

When run via launchd:
| Worker | Stdout | Stderr |
|--------|--------|--------|
| committed | `/tmp/decifer-universe-committed.log` | `/tmp/decifer-universe-committed.err` |
| promoter EOD | `/tmp/decifer-universe-promoter-eod.log` | `/tmp/decifer-universe-promoter-eod.err` |
| promoter pre-open | `/tmp/decifer-universe-promoter-preopen.log` | `/tmp/decifer-universe-promoter-preopen.err` |

---

## Where launchd templates are located

```
ops/launchd/
├── com.decifer.universe-committed.plist         # Sunday 23:00
├── com.decifer.universe-promoter-eod.plist      # Mon–Fri 16:15
└── com.decifer.universe-promoter-preopen.plist  # Mon–Fri 08:00
```

The earlier `deployment/` templates remain for reference but `ops/launchd/` is the
canonical location per the parallel runtime architecture.

---

## How to install launchd templates manually

```bash
cd /path/to/decifer-trading

# 1. Edit WorkingDirectory in each plist if your repo path differs from:
#    /Users/amitchopra/Desktop/decifer trading

# 2. Copy to LaunchAgents
cp ops/launchd/com.decifer.universe-committed.plist ~/Library/LaunchAgents/
cp ops/launchd/com.decifer.universe-promoter-eod.plist ~/Library/LaunchAgents/
cp ops/launchd/com.decifer.universe-promoter-preopen.plist ~/Library/LaunchAgents/

# 3. Load
launchctl load ~/Library/LaunchAgents/com.decifer.universe-committed.plist
launchctl load ~/Library/LaunchAgents/com.decifer.universe-promoter-eod.plist
launchctl load ~/Library/LaunchAgents/com.decifer.universe-promoter-preopen.plist

# 4. Verify loaded
launchctl list | grep decifer.universe

# 5. Unload (to remove)
launchctl unload ~/Library/LaunchAgents/com.decifer.universe-committed.plist
```

---

## How to verify after-hours / weekend operation

Both workers use Alpaca's `prior_close × prev_volume` snapshot data. Alpaca returns
the last available regular-session values even on weekends and outside market hours.

```bash
# Test on a Saturday morning — should succeed
python3.11 universe_committed.py --run-once
cat data/heartbeats/universe_committed_worker.json | python3.11 -m json.tool

# Verify the output file timestamp
python3.11 -c "
import json, os
from datetime import UTC, datetime
d = json.load(open('data/committed_universe.json'))
age_hours = (datetime.now(UTC) - datetime.fromisoformat(d['refreshed_at'])).total_seconds() / 3600
print(f\"count={d['count']} age={age_hours:.1f}h\")
"
```

---

## How to confirm bot.py is not required

```bash
# Stop bot.py (if running) then run the workers
pkill -f "python.*bot\.py" 2>/dev/null || echo "bot.py not running"

python3.11 universe_committed.py --run-once
python3.11 universe_promoter.py --run-once

# Both should exit 0 and write their artifacts independently
echo "committed: $(jq -r .status data/heartbeats/universe_committed_worker.json)"
echo "promoter:  $(jq -r .status data/heartbeats/universe_promoter_worker.json)"
```

The full verification script is at `scripts/verify_standalone_workers.sh`.

---

## Sprint 7J.4 handoff consumption status

`enable_active_opportunity_universe_handoff = True` was set in Sprint 7J.4.
Runtime log confirmation is pending.

To check whether the live bot is actually consuming the manifest:

```bash
# Look for handoff reader calls in recent audit log
grep -E "handoff_reader|load_production_handoff|current_manifest" data/audit_log.jsonl 2>/dev/null | tail -10

# Check manifest state
python3.11 -c "
import json
d = json.load(open('data/live/current_manifest.json'))
print('handoff_enabled:', d.get('handoff_enabled'))
print('publication_mode:', d.get('publication_mode'))
print('handoff_mode:', d.get('handoff_mode'))
print('expires_at:', d.get('expires_at'))
"
```

Do not change handoff logic based on this check. Escalate to Amit if the manifest
is not being consumed after a confirmed live scan cycle.

---

## Known remaining gaps

| Gap | Priority | Notes |
|-----|----------|-------|
| launchd daemons not yet installed | HIGH | Requires manual `launchctl load` per README |
| bot.py schedule fallback still active | LOW | Intentional until daemons confirmed over ≥2 cycles |
| Sprint 7J.4 runtime confirmation pending | MEDIUM | Check audit_log after next live scan cycle |
| `worker_evidence.py` not yet used by `handoff_publisher.py` | LOW | Handoff publisher has its own `publisher_run_log.jsonl` |
| No cloud-native scheduling | DEFERRED | Out of scope for Branch 1 |
