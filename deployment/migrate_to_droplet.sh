#!/usr/bin/env bash
# deployment/migrate_to_droplet.sh
#
# Run this ON YOUR MAC to migrate the bot to the droplet.
# Prerequisites: SSH access to root@206.189.135.189 (key-based, no password prompt)
#
# Usage:
#   bash deployment/migrate_to_droplet.sh
#
# What it does:
#   1. Stops the Mac bot (launchd)
#   2. Rsyncs data/ to the droplet
#   3. SSHes in and: git pull, installs deps, starts IBeam + bot
#
# Credentials (IBKR + API keys) are NEVER written by this script.
# You will be prompted to confirm the .env on the droplet looks correct.

set -euo pipefail

DROPLET="root@206.189.135.189"
REPO_DIR="/opt/decifer"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Decifer Trading — Mac → Droplet Migration              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Stop Mac bot ───────────────────────────────────────────────────────
echo "▶ Step 1: Stopping Mac bot..."
if launchctl list | grep -q "com.decifer.bot"; then
    launchctl unload ~/Library/LaunchAgents/com.decifer.bot.plist
    echo "  ✓ Mac bot stopped"
else
    echo "  ℹ Mac bot was not running"
fi
echo ""

# ── Step 2: Rsync data to droplet ─────────────────────────────────────────────
echo "▶ Step 2: Syncing data/ to droplet..."
echo "  This transfers current positions, trades, training records, etc."
rsync -avz --progress \
    --exclude='*.log' \
    --exclude='archive/' \
    --exclude='cold_storage/' \
    --exclude='signals_log_historical.jsonl' \
    "$LOCAL_DIR/data/" \
    "$DROPLET:$REPO_DIR/data/"
echo "  ✓ Data synced"
echo ""

# ── Step 3: Droplet setup ──────────────────────────────────────────────────────
echo "▶ Step 3: Running droplet setup..."
ssh "$DROPLET" bash << 'REMOTE'
set -euo pipefail
REPO=/opt/decifer

echo "  Pulling latest code..."
cd $REPO
git pull origin master

echo "  Checking .env..."
if ! grep -q "IBEAM_ACCOUNT" $REPO/.env 2>/dev/null; then
    echo ""
    echo "  ⚠️  IBKR credentials not found in $REPO/.env"
    echo "  Add these lines to $REPO/.env before continuing:"
    echo ""
    echo "    IBEAM_ACCOUNT=amit4283-paper"
    echo "    IBEAM_PASSWORD=<your password>"
    echo "    IBKR_PORT=4002"
    echo "    DECIFER_RUNTIME_MODE=paper_execution"
    echo "    DECIFER_EXECUTION_ENABLED=true"
    echo ""
    echo "  Then re-run this script."
    exit 1
fi

echo "  Installing Python dependencies..."
if [ ! -d "$REPO/venv" ]; then
    python3 -m venv $REPO/venv
fi
$REPO/venv/bin/pip install -q -r $REPO/requirements.txt

echo "  Creating logs directory..."
mkdir -p $REPO/logs

echo "  Starting IBeam (IB Gateway)..."
docker compose --profile ibkr up -d ibeam

echo "  Waiting 90s for IB Gateway to authenticate..."
sleep 90

echo "  Checking gateway health..."
HEALTH=$(curl -sk http://localhost:5000/v1/api/tickle | python3 -c "import sys,json; d=json.load(sys.stdin); print('authenticated:', d.get('session', {}).get('authenticated', False))" 2>/dev/null || echo "health check failed")
echo "  Gateway: $HEALTH"

echo "  Installing bot systemd service..."
cp $REPO/deployment/decifer-trading.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable decifer-trading
systemctl start decifer-trading

echo ""
echo "  ✓ Bot service started"
echo "  Monitor logs:  journalctl -u decifer-trading -f"
echo "  Dashboard:     http://206.189.135.189:8080 (or dashboard.decifertrading.com)"
REMOTE

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Migration complete.                                    ║"
echo "║                                                          ║"
echo "║   Monitor:  ssh root@206.189.135.189                     ║"
echo "║             journalctl -u decifer-trading -f             ║"
echo "║             tail -f /opt/decifer/logs/bot-launchd.log    ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
