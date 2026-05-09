# Deployment — Standalone Universe Workers

Classification: documentation only

This directory contains launchd plist templates for the Tier-B universe refresh
workers. These daemons run **independently of bot.py** — universe refresh survives
bot restarts, crashes, and weekends.

---

## Workers

| Daemon label | Module | Schedule | Output |
|---|---|---|---|
| `com.decifer.universe-committed` | `universe_committed.py` | Sunday 23:00 | `data/committed_universe.json` |
| `com.decifer.universe-promoter-eod` | `universe_promoter.py` | Mon–Fri 16:15 | `data/daily_promoted.json` |
| `com.decifer.universe-promoter-preopen` | `universe_promoter.py` | Mon–Fri 08:00 | `data/daily_promoted.json` |

---

## Runtime commands

```bash
# Committed universe (safe any time including weekends)
python3 universe_committed.py --run-once

# Promoter (safe pre/post-market — uses prior-session bars)
python3 universe_promoter.py --run-once

# Handoff publisher (requires universe_builder shadow output to exist)
python3 handoff_publisher.py --mode validation_only
python3 handoff_publisher.py --mode controlled_activation
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

---

## Installation

```bash
# 1. Copy plists (adjust path if your repo is elsewhere)
cp deployment/com.decifer.universe-committed.plist ~/Library/LaunchAgents/
cp deployment/com.decifer.universe-promoter-eod.plist ~/Library/LaunchAgents/
cp deployment/com.decifer.universe-promoter-preopen.plist ~/Library/LaunchAgents/

# 2. Edit WorkingDirectory in each plist if your repo path differs from:
#    /Users/amitchopra/Desktop/decifer trading

# 3. Load all three
launchctl load ~/Library/LaunchAgents/com.decifer.universe-committed.plist
launchctl load ~/Library/LaunchAgents/com.decifer.universe-promoter-eod.plist
launchctl load ~/Library/LaunchAgents/com.decifer.universe-promoter-preopen.plist

# 4. Verify
launchctl list | grep decifer.universe
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
