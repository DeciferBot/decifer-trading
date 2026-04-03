#!/usr/bin/env bash
# Decifer iCloud backup sync — runs every 5 min via launchd
# Syncs working directory to iCloud Drive so uncommitted changes and large
# files (data/historical/) survive a machine loss.
#
# NOTE: If you see "Operation not permitted" errors, grant Full Disk Access to
# /bin/bash in System Settings → Privacy & Security → Full Disk Access.

DECIFER_SRC="/Users/amitchopra/Documents/Claude/Projects/decifer trading"
CHIEF_SRC="/Users/amitchopra/Documents/Claude/Projects/Chief Designer"
ICLOUD_BASE="/Users/amitchopra/Library/Mobile Documents/com~apple~CloudDocs/Decifer-Backup"

# Ensure destination directories exist
mkdir -p "$ICLOUD_BASE/decifer-trading"
mkdir -p "$ICLOUD_BASE/chief-designer"

# Sync Decifer Trading (exclude large runtime artifacts)
rsync -a --delete \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='*.pyc' \
    --exclude='*.egg-info/' \
    --exclude='*.app/' \
    "$DECIFER_SRC/" "$ICLOUD_BASE/decifer-trading/"

STATUS1=$?

# Sync Chief Designer
rsync -a --delete \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    "$CHIEF_SRC/" "$ICLOUD_BASE/chief-designer/"

STATUS2=$?

if [ $STATUS1 -eq 0 ] && [ $STATUS2 -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') — sync OK"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') — sync ERROR (decifer=$STATUS1 chief=$STATUS2)" >&2
fi
