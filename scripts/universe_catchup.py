#!/usr/bin/env python3
"""
scripts/universe_catchup.py — staleness-aware catchup for universe workers.

macOS launchd silently skips StartCalendarInterval events when the machine is
asleep. This script runs on every login and every 2 hours (via
com.decifer.universe-catchup launchd service) and re-runs any worker whose
heartbeat is older than its expected refresh interval.

Workers checked:
  universe_committed_worker  — weekly (Sunday 23:00); stale after 8 days
  universe_promoter_worker   — daily weekdays; stale after 28 hours on weekdays
  intelligence-pipeline      — daily weekdays EOD; stale after 28 hours on weekdays

Run manually:
  python3.11 scripts/universe_catchup.py
  python3.11 scripts/universe_catchup.py --dry-run   # show what would run, skip execution
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

log = logging.getLogger("decifer.universe_catchup")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (heartbeat_path, max_age_hours_weekday, max_age_hours_weekend, worker_cmd)
# max_age_hours_weekend=None means "don't run on weekends"
WORKERS = [
    {
        "name": "universe_committed_worker",
        "heartbeat": "data/heartbeats/universe_committed_worker.json",
        "max_age_hours_weekday": 192,   # 8 days — weekly Sunday refresh
        "max_age_hours_weekend": 192,
        "cmd": [sys.executable, "universe_committed.py", "--run-once"],
    },
    {
        "name": "universe_promoter_worker",
        "heartbeat": "data/heartbeats/universe_promoter_worker.json",
        "max_age_hours_weekday": 28,
        "max_age_hours_weekend": None,   # weekday-only
        "cmd": [sys.executable, "universe_promoter.py", "--run-once"],
    },
    {
        "name": "intelligence_pipeline",
        "heartbeat": "data/intelligence/daily_economic_state.json",
        "heartbeat_key": "generated_at",
        "max_age_hours_weekday": 28,
        "max_age_hours_weekend": None,   # weekday-only
        "cmd": [
            "/bin/sh", "-c",
            # run_intelligence_pipeline.py includes universe_builder + live promotion
            f"{sys.executable} run_intelligence_pipeline.py",
        ],
    },
]


def _read_heartbeat_ts(path: str, key: str = "last_success_at") -> datetime | None:
    full = os.path.join(REPO_ROOT, path)
    if not os.path.exists(full):
        return None
    try:
        d = json.load(open(full))
        ts_str = d.get(key)
        if not ts_str:
            return None
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def _is_weekday() -> bool:
    return datetime.now(timezone.utc).weekday() < 5   # Mon=0 … Fri=4


def _age_hours(ts: datetime) -> float:
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


def main() -> int:
    parser = argparse.ArgumentParser(description="Universe worker catchup supervisor")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without running them")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    is_weekday = _is_weekday()
    now_str = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("universe_catchup starting — %s  weekday=%s  dry_run=%s", now_str, is_weekday, args.dry_run)

    ran: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for w in WORKERS:
        name = w["name"]
        max_age = w["max_age_hours_weekday"] if is_weekday else w["max_age_hours_weekend"]

        if max_age is None:
            log.info("%-35s SKIP (weekend, weekday-only worker)", name)
            skipped.append(name)
            continue

        key = w.get("heartbeat_key", "last_success_at")
        ts = _read_heartbeat_ts(w["heartbeat"], key)

        if ts is None:
            log.warning("%-35s STALE (no heartbeat — never run)", name)
        else:
            age = _age_hours(ts)
            if age <= max_age:
                log.info("%-35s OK    (%.1fh old, max %dh)", name, age, max_age)
                skipped.append(name)
                continue
            log.warning("%-35s STALE (%.1fh old, max %dh)", name, age, max_age)

        if args.dry_run:
            log.info("%-35s DRY-RUN — would run: %s", name, " ".join(w["cmd"][:3]))
            skipped.append(name)
            continue

        log.info("%-35s RUNNING ...", name)
        result = subprocess.run(
            w["cmd"],
            cwd=REPO_ROOT,
            capture_output=False,
        )
        if result.returncode == 0:
            log.info("%-35s SUCCESS", name)
            ran.append(name)
        else:
            log.error("%-35s FAILED (exit %d)", name, result.returncode)
            failed.append(name)

    log.info(
        "universe_catchup done — ran=%s  skipped=%s  failed=%s",
        ran, skipped, failed,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
