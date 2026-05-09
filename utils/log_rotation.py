"""
utils/log_rotation.py — Fail-safe JSONL log rotation for append-only logs.

Rotation strategy: when a file exceeds max_bytes, shift existing backups
(.1 → .2, .2 → .3, ...) then rename the live file to .1. A fresh file
is created on the next append. Semantics are identical to Python's
RotatingFileHandler but applied at the file level before an open() call.

All errors are swallowed — log rotation must never block a write.
Configurable defaults can be overridden via config.py using the pattern:
    int(CONFIG.get("some_log_max_mb", DEFAULT)) * 1_048_576
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger("decifer.log_rotation")


def rotate_jsonl_if_needed(
    path: str,
    max_bytes: int,
    backup_count: int = 3,
) -> None:
    """Rotate *path* → *path*.1, shifting existing backups, if size ≥ max_bytes.

    Args:
        path:         Absolute or relative path to the live JSONL file.
        max_bytes:    Rotate when file size reaches this threshold.
        backup_count: Number of backup files to keep (.1 through .N).
                      The oldest backup is deleted when backup_count is exceeded.
    """
    try:
        if not os.path.exists(path):
            return
        if os.path.getsize(path) < max_bytes:
            return
        size_mb = os.path.getsize(path) / 1_048_576
        # Shift existing backups: .3 → gone, .2 → .3, .1 → .2
        for i in range(backup_count - 1, 0, -1):
            src = f"{path}.{i}"
            dst = f"{path}.{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        # Rotate live file → .1
        os.replace(path, f"{path}.1")
        _log.info(
            "log_rotation: rotated %s (%.1f MB) — fresh log started",
            os.path.basename(path),
            size_mb,
        )
    except Exception as exc:
        _log.debug(
            "log_rotation: rotate_jsonl_if_needed(%s) failed — %s (non-fatal)",
            path,
            exc,
        )
