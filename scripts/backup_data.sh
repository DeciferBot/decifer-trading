#!/bin/bash
# Daily backup of /opt/decifer/data/ to DO Spaces (sgp1).
# Runs via cron: 30 20 * * * (02:00 IST) on the droplet.
# Excludes large regenerable files. Prunes backups older than 30 days.
set -euo pipefail

DATE=$(date +%Y-%m-%d)
ARCHIVE="/tmp/decifer-data-${DATE}.tar.gz"

tar -czf "$ARCHIVE" \
  --exclude='/opt/decifer/data/archive/cold_storage' \
  --exclude='/opt/decifer/data/signals_log*.jsonl*' \
  --exclude='/opt/decifer/data/signals_typed*.jsonl*' \
  --exclude='/opt/decifer/data/audit_log.jsonl' \
  --exclude='/opt/decifer/data/apex_shadow_log*' \
  --exclude='/opt/decifer/data/apex_decision_audit*' \
  --exclude='/opt/decifer/data/apex_prompt_snapshot*' \
  --exclude='/opt/decifer/data/apex_response_snapshot*' \
  --exclude='/opt/decifer/data/raw' \
  --exclude='/opt/decifer/data/features' \
  /opt/decifer/data/

aws s3 cp "$ARCHIVE" \
  "s3://decifer-data-backup/${DATE}.tar.gz" \
  --endpoint-url https://sgp1.digitaloceanspaces.com \
  --profile spaces

rm -f "$ARCHIVE"

# Prune backups older than 30 days
aws s3 ls s3://decifer-data-backup/ \
  --endpoint-url https://sgp1.digitaloceanspaces.com \
  --profile spaces \
  | awk '{print $4}' \
  | while read key; do
    day=$(echo "$key" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
    if [[ -n "$day" ]] && [[ $(date -d "$day" +%s 2>/dev/null) -lt $(date -d '30 days ago' +%s) ]]; then
      aws s3 rm "s3://decifer-data-backup/$key" \
        --endpoint-url https://sgp1.digitaloceanspaces.com \
        --profile spaces
    fi
  done

echo "Backup complete: ${DATE}"
