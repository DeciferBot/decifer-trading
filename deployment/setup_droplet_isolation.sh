#!/usr/bin/env bash
# deployment/setup_droplet_isolation.sh
#
# Run this once on the DigitalOcean droplet as root to establish the
# two-program directory layout and systemd isolation.
#
# What it does:
#   - Creates /opt/decifer-trading/ and /opt/decifer-learning/ as separate roots
#   - Creates a 'decifer' OS user owned by neither program individually
#   - Sets up /var/log directories for each program
#   - Installs the decifer-intelligence.service systemd unit (trading stack)
#   - Leaves a placeholder for the decifer-learning.service (you supply that)
#   - Prints what to do next
#
# Usage:
#   ssh root@<DO_IP>
#   curl -O https://raw.githubusercontent.com/... (or scp this file)
#   bash setup_droplet_isolation.sh
#
# Does NOT:
#   - Clone any repo (you do that manually — git credentials stay off this script)
#   - Write any .env file (never automate secrets)
#   - Start any service (you start after verifying config)
#   - Touch Decifer Learning internals (Learning team owns /opt/decifer-learning)

set -euo pipefail

# ── Guard: must run as root ──────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run as root (sudo bash setup_droplet_isolation.sh)"
  exit 1
fi

echo "==> Creating shared 'decifer' OS user"
if ! id -u decifer &>/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin decifer
  echo "    Created user: decifer"
else
  echo "    User already exists: decifer"
fi

# ── Decifer Trading directory tree ───────────────────────────────────────────
echo "==> Setting up /opt/decifer-trading"
mkdir -p /opt/decifer-trading/{repo,data,logs,venv}
chown -R decifer:decifer /opt/decifer-trading
chmod 750 /opt/decifer-trading

echo "==> Setting up /var/log/decifer-trading"
mkdir -p /var/log/decifer-trading
chown decifer:decifer /var/log/decifer-trading
chmod 750 /var/log/decifer-trading

# ── Decifer Learning directory tree ──────────────────────────────────────────
echo "==> Setting up /opt/decifer-learning"
mkdir -p /opt/decifer-learning/{repo,data,logs,venv}
chown -R decifer:decifer /opt/decifer-learning
chmod 750 /opt/decifer-learning

echo "==> Setting up /var/log/decifer-learning"
mkdir -p /var/log/decifer-learning
chown decifer:decifer /var/log/decifer-learning
chmod 750 /var/log/decifer-learning

# ── Existing log dir (intelligence API) ─────────────────────────────────────
mkdir -p /var/log/decifer-intelligence
chown decifer:decifer /var/log/decifer-intelligence
chmod 750 /var/log/decifer-intelligence

# ── Install trading systemd unit ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$SCRIPT_DIR/decifer-intelligence.service"

if [[ -f "$UNIT_SRC" ]]; then
  echo "==> Installing decifer-intelligence.service"
  cp "$UNIT_SRC" /etc/systemd/system/decifer-intelligence.service
  chmod 644 /etc/systemd/system/decifer-intelligence.service
  systemctl daemon-reload
  systemctl enable decifer-intelligence
  echo "    Installed and enabled (not started — do that after .env is in place)"
else
  echo "WARN: $UNIT_SRC not found — install the systemd unit manually"
fi

# ── Swap: ensure 4 GB swap exists (8 GB droplet, RAM-heavy workload) ─────────
echo "==> Checking swap"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [[ "$SWAP_TOTAL" -lt 3000 ]]; then
  echo "    Creating 4 GB swapfile at /swapfile"
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "    Swap created and persisted"
else
  echo "    Swap already sufficient: ${SWAP_TOTAL} MB"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete ==="
echo ""
echo "Directory layout:"
echo "  /opt/decifer-trading/   — Decifer Trading (6 GB RAM limit)"
echo "    repo/                 ← git clone DeciferBot/decifer-trading here"
echo "    data/                 ← rsync data/ from your Mac here"
echo "    logs/"
echo "    venv/                 ← pip install -r requirements.intelligence.txt"
echo "    .env                  ← write manually, chmod 600"
echo ""
echo "  /opt/decifer-learning/  — Decifer Learning (2 GB RAM limit)"
echo "    repo/                 ← git clone Decifer Learning repo here"
echo "    data/"
echo "    logs/"
echo "    venv/"
echo "    .env                  ← write manually, chmod 600"
echo ""
echo "Next steps for Decifer Trading:"
echo "  1. cd /opt/decifer-trading/repo && git clone <trading-repo> ."
echo "  2. python3 -m venv /opt/decifer-trading/venv"
echo "  3. /opt/decifer-trading/venv/bin/pip install -r requirements.intelligence.txt"
echo "  4. Write /opt/decifer-trading/.env && chmod 600 /opt/decifer-trading/.env"
echo "  5. systemctl start decifer-intelligence"
echo "  6. systemctl status decifer-intelligence"
echo "  7. python3 scripts/smoke_test_intelligence_cloud.py --url http://localhost:8000"
echo ""
echo "Next steps for Decifer Learning:"
echo "  1. cd /opt/decifer-learning/repo && git clone <learning-repo> ."
echo "  2. Create /etc/systemd/system/decifer-learning.service with MemoryMax=2G CPUQuota=150%"
echo "  3. Write /opt/decifer-learning/.env && chmod 600 /opt/decifer-learning/.env"
echo "  4. systemctl enable --now decifer-learning"
echo ""
echo "Verify isolation at any time:"
echo "  systemctl status decifer-intelligence"
echo "  systemctl status decifer-learning"
echo "  cat /sys/fs/cgroup/system.slice/decifer-intelligence.service/memory.current"
echo "  cat /sys/fs/cgroup/system.slice/decifer-learning.service/memory.current"
