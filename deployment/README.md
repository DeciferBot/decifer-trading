# Deployment — Standalone Universe Workers

Classification: documentation only

This directory contains launchd plist templates for standalone workers that run
**independently of bot.py** — refreshes survive bot restarts, crashes, and weekends.

---

## Workers

| Daemon label | Module | Schedule | Output |
|---|---|---|---|
| `com.decifer.intelligence-pipeline` | `run_intelligence_pipeline.py` + `universe_builder.py` + `handoff_publisher.py` | Mon–Fri 07:30 | `data/intelligence/`, `data/universe_builder/`, `data/live/` |
| `com.decifer.handoff-publisher` | `handoff_publisher.py` | Every 10 minutes (all days) | `data/live/current_manifest.json` |
| `com.decifer.universe-committed` | `universe_committed.py` | Sunday 23:00 | `data/committed_universe.json` |
| `com.decifer.universe-promoter-eod` | `universe_promoter.py` | Mon–Fri 16:15 | `data/daily_promoted.json` |
| `com.decifer.universe-promoter-preopen` | `universe_promoter.py` | Mon–Fri 08:00 | `data/daily_promoted.json` |

### Intelligence-First cadence (how the workers interact)

```
07:30 ET (Mon–Fri)   com.decifer.intelligence-pipeline fires:
                       1. run_intelligence_pipeline.py  → data/intelligence/
                       2. universe_builder.py            → data/universe_builder/shadow
                       3. handoff_publisher.py           → data/live/manifest (15-min TTL)

Every 10 min         com.decifer.handoff-publisher fires:
                       handoff_publisher.py              → data/live/manifest (refreshed TTL)
                       (re-publishes same universe with fresh timestamp — keeps bot unblocked)

Bot scan cycle       Reads data/live/current_manifest.json.
                       If expired → fails closed (universe=[], Track A skipped).
                       If valid   → consumes 75 prepared candidates.
```

**Critical:** `current_manifest.json` expires 15 minutes after publication. Without
`com.decifer.handoff-publisher` running, the bot fails closed on all new entries.

---

## Runtime commands

```bash
# Full daily intelligence refresh (run manually or via daemon)
python3 run_intelligence_pipeline.py
python3 universe_builder.py
python3 handoff_publisher.py --mode controlled_activation

# Universe workers
python3 universe_committed.py --run-once
python3 universe_promoter.py --run-once

# Handoff publisher only (validation mode — bot cannot consume)
python3 handoff_publisher.py --mode validation_only
```

---

## Evidence files

Each standalone run writes a heartbeat record:

| Worker | Heartbeat path |
|---|---|
| universe_committed | `data/heartbeats/universe_committed_worker.json` |
| universe_promoter | `data/heartbeats/universe_promoter_worker.json` |
| handoff_publisher | `data/heartbeats/handoff_publisher.json` |

Each record contains: `worker`, `last_attempt_at`, `last_success_at`, `status`,
`artifact_path`, `artifact_age_seconds`, `count`, `elapsed_seconds`, `error`,
and safety flags (`live_output_changed=false`, `broker_called=false`, `order_placed=false`).

Check manifest freshness at any time:
```bash
python3 -c "import json; m=json.load(open('data/live/current_manifest.json')); print(m['published_at'], '→', m['expires_at'], '|', m['handoff_enabled'])"
```

---

## Installation

```bash
# 1. Copy plists (adjust path if your repo is elsewhere)
cp deployment/com.decifer.intelligence-pipeline.plist ~/Library/LaunchAgents/
cp deployment/com.decifer.handoff-publisher.plist ~/Library/LaunchAgents/
cp deployment/com.decifer.universe-committed.plist ~/Library/LaunchAgents/
cp deployment/com.decifer.universe-promoter-eod.plist ~/Library/LaunchAgents/
cp deployment/com.decifer.universe-promoter-preopen.plist ~/Library/LaunchAgents/

# 2. Edit WorkingDirectory in each plist if your repo path differs from:
#    /Users/amitchopra/Desktop/decifer trading

# 3. Load all five
launchctl load ~/Library/LaunchAgents/com.decifer.intelligence-pipeline.plist
launchctl load ~/Library/LaunchAgents/com.decifer.handoff-publisher.plist
launchctl load ~/Library/LaunchAgents/com.decifer.universe-committed.plist
launchctl load ~/Library/LaunchAgents/com.decifer.universe-promoter-eod.plist
launchctl load ~/Library/LaunchAgents/com.decifer.universe-promoter-preopen.plist

# 4. Verify
launchctl list | grep decifer
```

---

## Transition from bot.py schedule

bot.py currently registers identical schedule jobs as fallback:

```python
# bot.py ~line 623
schedule.every().sunday.at("23:00").do(refresh_committed_universe)
schedule.every().day.at("16:15").do(run_promoter)
schedule.every().day.at("08:00").do(run_promoter)
```

**These registrations are left in place intentionally.** They act as fallback if the
launchd daemons are not installed or fail. Once the daemons have been confirmed
reliable across ≥2 full weekly cycles (check heartbeat files), the bot.py schedule
registrations can be removed. That is a separate sprint decision.

---

## Safety contract

Every worker in this directory:
- Does not import `bot_trading`, `orders_core`, `orders_options`, `bot_ibkr`, or
  any broker/IBKR module.
- Does not place orders.
- Does not connect to IBKR.
- Exits 0 on success, 1 on failure.
- Writes a heartbeat evidence file on every attempt (success and failure).
- Is idempotent — safe to run multiple times; subsequent runs overwrite the output
  atomically.
