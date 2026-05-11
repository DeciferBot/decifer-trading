#!/usr/bin/env bash
# scripts/cloud/setup_digitalocean_vm_base.sh
#
# PURPOSE:
#   Baseline system setup for a DigitalOcean Ubuntu 22.04 Droplet that will
#   run the Decifer Trading stack (Docker) and IBKR IB Gateway (IBC + Xvfb).
#
# SAFE TO RE-RUN:
#   All steps are idempotent. Re-running is safe — already-installed packages
#   are skipped, already-created users/dirs are preserved.
#
# WHAT THIS SCRIPT DOES:
#   1. Updates apt package lists and upgrades installed packages
#   2. Installs Docker Engine (official Docker GPG + repo)
#   3. Installs Docker Compose plugin (v2)
#   4. Installs git, curl, unzip, wget, jq
#   5. Installs OpenJDK 17 (required by IB Gateway and IBC)
#   6. Installs Xvfb and required headless X11 dependencies
#   7. Creates the 'decifer' system user (UID 1000) and group if absent
#   8. Creates /opt/decifer directory structure
#
# WHAT THIS SCRIPT DOES NOT DO:
#   - Does not clone the repo (Amit does that manually — git credentials required)
#   - Does not create .env (Amit does that manually — secrets required)
#   - Does not start any service (run install_systemd_services.sh --enable --start)
#   - Does not install IBC (documented in ops/ibc/README.md)
#   - Does not start bot.py or connect to IBKR
#   - Does not open any firewall ports (run setup_firewall.sh)
#
# USAGE:
#   # As root on the Droplet:
#   bash scripts/cloud/setup_digitalocean_vm_base.sh
#
# TESTED ON: Ubuntu 22.04 LTS (Jammy)
# REQUIRES:  Root or sudo access

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

BOLD=$(tput bold 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

log()  { echo "${GREEN}[setup]${RESET} $*"; }
warn() { echo "${YELLOW}[warn ]${RESET} $*"; }
step() { echo ""; echo "${BOLD}── $* ──${RESET}"; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DECIFER_USER="decifer"
DECIFER_UID=1000
DECIFER_HOME="/home/decifer"
DECIFER_OPT="/opt/decifer"

JAVA_PACKAGE="openjdk-17-jre-headless"

# ─────────────────────────────────────────────────────────────────────────────
# 0. Preflight
# ─────────────────────────────────────────────────────────────────────────────

require_root

step "Preflight checks"
log "Running on: $(lsb_release -ds 2>/dev/null || uname -a)"
log "Architecture: $(dpkg --print-architecture)"

if ! lsb_release -cs 2>/dev/null | grep -q "jammy"; then
  warn "This script is tested on Ubuntu 22.04 (jammy). Other versions may work but are untested."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 1. System update
# ─────────────────────────────────────────────────────────────────────────────

step "System update"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
log "System packages updated."

# ─────────────────────────────────────────────────────────────────────────────
# 2. Base utilities
# ─────────────────────────────────────────────────────────────────────────────

step "Base utilities"
apt-get install -y -qq \
  git \
  curl \
  wget \
  unzip \
  jq \
  ca-certificates \
  gnupg \
  lsb-release \
  software-properties-common \
  apt-transport-https \
  htop \
  rsync \
  ufw
log "Base utilities installed."

# ─────────────────────────────────────────────────────────────────────────────
# 3. Docker Engine (official Docker repo — not the Ubuntu snap)
# ─────────────────────────────────────────────────────────────────────────────

step "Docker Engine"

if command -v docker &>/dev/null; then
  log "Docker already installed: $(docker --version)"
else
  log "Installing Docker Engine from official Docker repo..."
  # Add Docker's official GPG key
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  # Add Docker apt repository
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -qq
  apt-get install -y -qq \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

  log "Docker Engine installed: $(docker --version)"
fi

# Ensure Docker daemon is enabled (not started yet — will start with systemd)
systemctl enable docker --quiet
log "Docker daemon enabled at boot."

# ─────────────────────────────────────────────────────────────────────────────
# 4. Java runtime (required by IB Gateway and IBC)
# ─────────────────────────────────────────────────────────────────────────────

step "Java runtime (${JAVA_PACKAGE})"

if java -version &>/dev/null 2>&1; then
  log "Java already installed: $(java -version 2>&1 | head -1)"
else
  apt-get install -y -qq "${JAVA_PACKAGE}"
  log "Java installed: $(java -version 2>&1 | head -1)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. Xvfb and headless X11 dependencies (required by IB Gateway GUI)
# ─────────────────────────────────────────────────────────────────────────────

step "Xvfb + headless X11 dependencies"

apt-get install -y -qq \
  xvfb \
  libxrender1 \
  libxtst6 \
  libxi6 \
  libxft2 \
  fonts-liberation \
  x11-utils
log "Xvfb and X11 dependencies installed."

# ─────────────────────────────────────────────────────────────────────────────
# 6. Create 'decifer' system user and group (UID 1000 — matches Docker image)
# ─────────────────────────────────────────────────────────────────────────────

step "decifer user (UID ${DECIFER_UID})"

if id -u "${DECIFER_USER}" &>/dev/null; then
  log "User '${DECIFER_USER}' already exists (uid=$(id -u ${DECIFER_USER}))."
else
  groupadd --gid "${DECIFER_UID}" "${DECIFER_USER}" 2>/dev/null || true
  useradd \
    --uid "${DECIFER_UID}" \
    --gid "${DECIFER_UID}" \
    --home "${DECIFER_HOME}" \
    --create-home \
    --shell /bin/bash \
    "${DECIFER_USER}"
  log "User '${DECIFER_USER}' created (uid=${DECIFER_UID})."
fi

# Add decifer user to the docker group so it can run docker commands
usermod -aG docker "${DECIFER_USER}" 2>/dev/null || true
log "User '${DECIFER_USER}' added to docker group."

# ─────────────────────────────────────────────────────────────────────────────
# 7. /opt/decifer directory structure
# ─────────────────────────────────────────────────────────────────────────────

step "/opt/decifer directory structure"

# /opt/decifer/current  — repo checkout (symlink or direct clone)
# /opt/decifer/shared/data  — persistent runtime data (bind-mounted into containers)
# /opt/decifer/shared/logs  — persistent application logs
# /opt/decifer/ibc  — IBC installation and config (never in repo)

for dir in \
  "${DECIFER_OPT}" \
  "${DECIFER_OPT}/current" \
  "${DECIFER_OPT}/shared" \
  "${DECIFER_OPT}/shared/data" \
  "${DECIFER_OPT}/shared/data/live" \
  "${DECIFER_OPT}/shared/data/heartbeats" \
  "${DECIFER_OPT}/shared/data/intelligence" \
  "${DECIFER_OPT}/shared/data/universe_builder" \
  "${DECIFER_OPT}/shared/data/reference" \
  "${DECIFER_OPT}/shared/data/runtime" \
  "${DECIFER_OPT}/shared/logs" \
  "${DECIFER_OPT}/ibc" \
  "${DECIFER_OPT}/ibc/logs"; do
  if [[ ! -d "${dir}" ]]; then
    mkdir -p "${dir}"
    log "Created: ${dir}"
  else
    log "Exists:  ${dir}"
  fi
done

# Ownership: decifer user owns /opt/decifer (matches container UID 1000)
chown -R "${DECIFER_UID}:${DECIFER_UID}" "${DECIFER_OPT}"
log "Ownership set: ${DECIFER_OPT} → ${DECIFER_USER}:${DECIFER_USER}"

# ─────────────────────────────────────────────────────────────────────────────
# 8. Summary and next steps
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo "${GREEN}  VM base setup complete.${RESET}"
echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo ""
echo "  Installed: Docker $(docker --version 2>/dev/null | cut -d' ' -f3 | tr -d ',')"
echo "  Installed: Java $(java -version 2>&1 | head -1 | awk '{print $3}' | tr -d '"')"
echo "  Installed: Xvfb $(dpkg -l xvfb 2>/dev/null | awk '/^ii/{print $3}')"
echo "  Directory: ${DECIFER_OPT}/"
echo "  User:      ${DECIFER_USER} (uid=${DECIFER_UID})"
echo ""
echo "  NEXT MANUAL STEPS (in order):"
echo ""
echo "  1. Harden SSH:"
echo "     # Edit /etc/ssh/sshd_config:"
echo "     # PasswordAuthentication no"
echo "     # PermitRootLogin prohibit-password"
echo "     systemctl restart sshd"
echo ""
echo "  2. Set up firewall:"
echo "     bash scripts/cloud/setup_firewall.sh"
echo ""
echo "  3. Clone the Decifer repo:"
echo "     cd /opt/decifer"
echo "     git clone https://github.com/DeciferBot/decifer-trading.git current"
echo "     chown -R ${DECIFER_UID}:${DECIFER_UID} current"
echo ""
echo "  4. Create .env (secrets — never commit this file):"
echo "     cp /opt/decifer/current/.env.example /opt/decifer/current/.env"
echo "     vim /opt/decifer/current/.env   # populate all values"
echo "     chmod 600 /opt/decifer/current/.env"
echo "     chown root:root /opt/decifer/current/.env"
echo ""
echo "  5. Install IBC (see ops/ibc/README.md)."
echo ""
echo "  6. Install systemd services:"
echo "     bash /opt/decifer/current/scripts/cloud/install_systemd_services.sh"
echo ""
echo "  7. Run first-deploy bootstrap:"
echo "     bash /opt/decifer/current/scripts/cloud/first_deploy_bootstrap.sh"
echo ""
echo "  8. Follow docs/cloud_phase1_vm_deployment_runbook.md for the remaining steps."
echo ""
