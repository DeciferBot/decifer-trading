#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║   <> DECIFER — Auto Git Push                                 ║
# ║   Runs every 2 minutes, pushes any unpushed commits          ║
# ║   Writes JSON status for Chief Decifer dashboard              ║
# ╚══════════════════════════════════════════════════════════════╝

REPO_DIR="$HOME/Documents/claude/projects/decifer-trading"
LOG_FILE="$REPO_DIR/logs/auto-push.log"
STATUS_FILE="$REPO_DIR/chief-decifer/state/git-push-status.json"

# Make sure dirs exist
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$STATUS_FILE")"

cd "$REPO_DIR" || exit 1

NOW=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
LAST_COMMIT=$(git log -1 --format='%h %s' 2>/dev/null || echo "none")
LAST_COMMIT_TIME=$(git log -1 --format='%aI' 2>/dev/null || echo "")

# Count unpushed commits
UNPUSHED=$(git log --oneline @{u}..HEAD 2>/dev/null)
UNPUSHED_COUNT=0
if [ -n "$UNPUSHED" ]; then
    UNPUSHED_COUNT=$(echo "$UNPUSHED" | wc -l | tr -d ' ')
fi

# If nothing to push, write "synced" status
if [ "$UNPUSHED_COUNT" -eq 0 ]; then
    cat > "$STATUS_FILE" << EOF
{
  "status": "synced",
  "checked_at": "$NOW",
  "branch": "$BRANCH",
  "unpushed_count": 0,
  "last_commit": "$LAST_COMMIT",
  "last_commit_time": "$LAST_COMMIT_TIME",
  "message": "Up to date with GitHub"
}
EOF
    exit 0
fi

# Try to push
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pushing $UNPUSHED_COUNT commit(s)..." >> "$LOG_FILE"
OUTPUT=$(git push origin "$BRANCH" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Push OK" >> "$LOG_FILE"
    cat > "$STATUS_FILE" << EOF
{
  "status": "pushed",
  "checked_at": "$NOW",
  "branch": "$BRANCH",
  "unpushed_count": 0,
  "pushed_count": $UNPUSHED_COUNT,
  "last_commit": "$LAST_COMMIT",
  "last_commit_time": "$LAST_COMMIT_TIME",
  "message": "Pushed $UNPUSHED_COUNT commit(s) to GitHub"
}
EOF
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Push FAILED: $OUTPUT" >> "$LOG_FILE"
    # Escape quotes in output for JSON
    SAFE_OUTPUT=$(echo "$OUTPUT" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    cat > "$STATUS_FILE" << EOF
{
  "status": "failed",
  "checked_at": "$NOW",
  "branch": "$BRANCH",
  "unpushed_count": $UNPUSHED_COUNT,
  "last_commit": "$LAST_COMMIT",
  "last_commit_time": "$LAST_COMMIT_TIME",
  "error": "$SAFE_OUTPUT",
  "message": "Push failed — $UNPUSHED_COUNT commit(s) waiting"
}
EOF
fi
