# worker_evidence.py — Shared JSONL evidence writer for standalone universe workers.
# Classification: worker runtime
#
# Appends one structured record to data/runtime/universe_worker_evidence.jsonl
# on every worker attempt (success and failure). This is the machine-readable
# proof that a worker ran independently of bot.py.
#
# Consumed by: universe_committed._main(), universe_promoter._main()
# Must not import: bot_trading, orders_*, risk, broker, ibkr, apex, execution modules.

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime

_EVIDENCE_PATH = os.path.join("data", "runtime", "universe_worker_evidence.jsonl")


def _git_branch() -> str:
    """Return the current git branch, or 'unknown' if not determinable."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _artifact_mtime_iso(path: str) -> str | None:
    """Return the mtime of path as ISO-8601 UTC string, or None."""
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    except OSError:
        return None


def _artifact_age_seconds(path: str) -> float | None:
    """Return seconds since the file was last modified, or None."""
    try:
        import time
        return round(time.time() - os.path.getmtime(path), 1)
    except OSError:
        return None


def append_evidence(
    worker_name: str,
    started_at: datetime,
    finished_at: datetime,
    success: bool,
    output_artifact_path: str,
    failure_reason: str | None = None,
    run_mode: str = "run_once",
    source: str = "standalone_cli",
    extra: dict | None = None,
) -> bool:
    """
    Append one evidence record to data/runtime/universe_worker_evidence.jsonl.

    Returns True on success, False if the append failed (non-critical — the
    worker's own exit code and heartbeat are the authoritative failure signal).

    Safety contract (hardcoded):
        live_output_changed = false
        broker_called = false
        order_placed = false
    """
    artifact_exists = os.path.exists(output_artifact_path)
    record: dict = {
        "worker_name": worker_name,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "success": success,
        "failure_reason": failure_reason,
        "output_artifact_path": output_artifact_path,
        "output_artifact_exists": artifact_exists,
        "output_artifact_mtime": _artifact_mtime_iso(output_artifact_path),
        "output_artifact_age_seconds": _artifact_age_seconds(output_artifact_path),
        "run_mode": run_mode,
        "git_branch": _git_branch(),
        "source": source,
        "live_output_changed": False,
        "broker_called": False,
        "order_placed": False,
    }
    if extra:
        record.update(extra)

    try:
        os.makedirs(os.path.dirname(_EVIDENCE_PATH), exist_ok=True)
        with open(_EVIDENCE_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except OSError:
        return False


def read_latest(worker_name: str | None = None) -> dict | None:
    """
    Read the most recent evidence record, optionally filtered by worker_name.

    Returns None if the file does not exist or has no matching record.
    Useful for verification and monitoring scripts.
    """
    try:
        with open(_EVIDENCE_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if worker_name is None or record.get("worker_name") == worker_name:
            return record
    return None
