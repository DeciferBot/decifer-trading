#!/usr/bin/env python3
"""
sync_labels.py — propagate data/intelligence/label_registry.json to all consumers.

Usage:
    python3 scripts/sync_labels.py

Destinations:
    map/data/label_registry.json
    mobile/src/data/label_registry.json
"""

import json
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CANONICAL = REPO_ROOT / "data" / "intelligence" / "label_registry.json"

DESTINATIONS = [
    REPO_ROOT / "map" / "data" / "label_registry.json",
    REPO_ROOT / "mobile" / "src" / "data" / "label_registry.json",
    REPO_ROOT / "options" / "src" / "data" / "label_registry.json",
    REPO_ROOT / "symbol" / "src" / "data" / "label_registry.json",
    REPO_ROOT / "macro" / "src" / "data" / "label_registry.json",
]

def main():
    if not CANONICAL.exists():
        print(f"ERROR: canonical not found at {CANONICAL}")
        raise SystemExit(1)

    registry = json.loads(CANONICAL.read_text())
    print(f"Canonical loaded — {sum(len(v) for v in registry.values() if isinstance(v, dict))} total labels")

    for dest in DESTINATIONS:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CANONICAL, dest)
        print(f"  Synced → {dest.relative_to(REPO_ROOT)}")

    print("Done.")

if __name__ == "__main__":
    main()
