#!/usr/bin/env bash
# sync_intelligence_to_cloud.sh
#
# Rsyncs fresh intelligence data from the Mac to the DigitalOcean droplet.
# Runs after run_intelligence_pipeline.py via com.decifer.intelligence-sync.plist.
# Safe to run any time — read-only on Mac, write to /opt/decifer/data/ on DO.
#
# Schedule: Monday–Friday 16:50 Dubai (5 min after the intelligence pipeline)
#
# Manual run:
#   bash scripts/sync_intelligence_to_cloud.sh

set -euo pipefail

REPO_DIR="/Users/amitchopra/Desktop/decifer trading"
DO_HOST="206.189.135.189"
DO_DATA_DIR="/opt/decifer/data"
LOG_FILE="/tmp/decifer-intelligence-sync.log"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "Starting intelligence data sync to $DO_HOST"

# Sync intelligence runtime files
rsync -av --delete \
  --exclude=".fail_*" \
  "$REPO_DIR/data/intelligence/live_driver_state.json" \
  "$REPO_DIR/data/intelligence/theme_activation.json" \
  "$REPO_DIR/data/intelligence/economic_candidate_feed.json" \
  "$REPO_DIR/data/intelligence/customer_event_tape.json" \
  "$REPO_DIR/data/intelligence/thematic_roster.json" \
  "$REPO_DIR/data/intelligence/theme_taxonomy.json" \
  "$REPO_DIR/data/intelligence/transmission_rules.json" \
  "root@$DO_HOST:$DO_DATA_DIR/intelligence/" \
  >> "$LOG_FILE" 2>&1

# Sync theme_graph subdirectory
rsync -av --delete \
  "$REPO_DIR/data/intelligence/theme_graph/" \
  "root@$DO_HOST:$DO_DATA_DIR/intelligence/theme_graph/" \
  >> "$LOG_FILE" 2>&1

# Sync live universe handoff files
rsync -av \
  "$REPO_DIR/data/live/active_opportunity_universe.json" \
  "$REPO_DIR/data/live/current_manifest.json" \
  "root@$DO_HOST:$DO_DATA_DIR/live/" \
  >> "$LOG_FILE" 2>&1

log "Sync complete"

# Verify health endpoint
STATUS=$(curl -s "https://intelligence.decifertrading.com/health" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data_freshness_status','unknown'))" 2>/dev/null || echo "check_failed")
log "Intelligence API health: $STATUS"
