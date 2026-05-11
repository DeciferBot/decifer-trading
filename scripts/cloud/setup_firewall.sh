#!/usr/bin/env bash
# scripts/cloud/setup_firewall.sh
#
# PURPOSE:
#   Configure ufw (Uncomplicated Firewall) for the Decifer Trading VM.
#
# SECURITY DESIGN:
#   - SSH (22): allowed from any IP (restrict further to your IP if possible)
#   - Dashboard (8080): disabled by default; opt-in via ALLOW_DASHBOARD=true
#   - IBKR Gateway (4002, 7496, 7497): NEVER exposed publicly — loopback only
#   - All other inbound: denied by default
#
# IBKR GATEWAY ISOLATION:
#   IB Gateway binds exclusively to 127.0.0.1:4002 on this VM.
#   The firewall must NOT open port 4002 (or 7496/7497) to any external address.
#   The bot container reaches Gateway via network_mode: host + loopback — no
#   external routing is required or permitted.
#
# SAFE TO RE-RUN:
#   ufw rules are idempotent. Re-running adjusts rules to match this script.
#
# USAGE:
#   # As root on the Droplet:
#   bash scripts/cloud/setup_firewall.sh
#
#   # To also allow dashboard port 8080 (only if you have IP restriction or VPN):
#   ALLOW_DASHBOARD=true bash scripts/cloud/setup_firewall.sh
#
#   # To restrict SSH to a specific IP:
#   SSH_ALLOWED_FROM=203.0.113.10 bash scripts/cloud/setup_firewall.sh

set -euo pipefail

BOLD=$(tput bold 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

log()  { echo "${GREEN}[firewall]${RESET} $*"; }
warn() { echo "${YELLOW}[warn   ]${RESET} $*"; }
err()  { echo "${RED}[ERROR  ]${RESET} $*" >&2; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    err "This script must be run as root (use sudo)."
    exit 1
  fi
}

require_root

# ─────────────────────────────────────────────────────────────────────────────
# Configuration (override via environment variables)
# ─────────────────────────────────────────────────────────────────────────────

# Set to "true" to allow dashboard port 8080 (off by default — security risk)
ALLOW_DASHBOARD="${ALLOW_DASHBOARD:-false}"

# Dashboard port
DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"

# SSH source IP — "any" means any IP (tighten this if you have a static IP)
SSH_ALLOWED_FROM="${SSH_ALLOWED_FROM:-any}"

echo ""
echo "${BOLD}── Decifer VM Firewall Setup ──${RESET}"
echo ""
echo "  ALLOW_DASHBOARD=${ALLOW_DASHBOARD}"
echo "  SSH_ALLOWED_FROM=${SSH_ALLOWED_FROM}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Install ufw if absent
# ─────────────────────────────────────────────────────────────────────────────

if ! command -v ufw &>/dev/null; then
  log "Installing ufw..."
  apt-get install -y -qq ufw
fi

# ─────────────────────────────────────────────────────────────────────────────
# Reset to clean state (keeps SSH rule to avoid lockout)
# ─────────────────────────────────────────────────────────────────────────────

log "Resetting ufw rules..."
ufw --force reset > /dev/null

# ─────────────────────────────────────────────────────────────────────────────
# Default policy: deny all inbound, allow all outbound
# ─────────────────────────────────────────────────────────────────────────────

ufw default deny incoming
ufw default allow outgoing
log "Default policy: deny incoming / allow outgoing."

# ─────────────────────────────────────────────────────────────────────────────
# SSH — always allowed (without this the VM becomes unreachable)
# ─────────────────────────────────────────────────────────────────────────────

if [[ "${SSH_ALLOWED_FROM}" == "any" ]]; then
  ufw allow 22/tcp comment "SSH — all sources (tighten via SSH_ALLOWED_FROM env var)"
  warn "SSH allowed from ANY IP. Consider restricting: SSH_ALLOWED_FROM=<your-ip>"
else
  ufw allow from "${SSH_ALLOWED_FROM}" to any port 22 proto tcp \
    comment "SSH — restricted to ${SSH_ALLOWED_FROM}"
  log "SSH allowed from: ${SSH_ALLOWED_FROM}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard port (8080) — OFF by default, opt-in only
# ─────────────────────────────────────────────────────────────────────────────

if [[ "${ALLOW_DASHBOARD}" == "true" ]]; then
  warn "ALLOW_DASHBOARD=true — opening port ${DASHBOARD_PORT}."
  warn "Ensure this port is NOT accessible from the public internet without"
  warn "additional authentication (VPN, IP allowlist, or reverse proxy with auth)."
  ufw allow "${DASHBOARD_PORT}/tcp" comment "Decifer dashboard — ENABLE ONLY WITH IP RESTRICTION OR VPN"
  log "Dashboard port ${DASHBOARD_PORT} opened."
else
  log "Dashboard port ${DASHBOARD_PORT} NOT opened (ALLOW_DASHBOARD=false — default)."
  log "To access dashboard: use an SSH tunnel:"
  log "  ssh -L 8080:localhost:8080 user@<vm-ip>"
fi

# ─────────────────────────────────────────────────────────────────────────────
# IBKR Gateway ports — EXPLICITLY BLOCKED on public interfaces
#
# These ports must NEVER be exposed to the internet. IB Gateway binds to
# 127.0.0.1 (loopback) only. The firewall adds a belt-and-suspenders block.
#
# Port reference:
#   4002 — IB Gateway paper  (Decifer Phase 1 target)
#   4001 — IB Gateway live   (NEVER open on Phase 1)
#   7497 — TWS paper
#   7496 — TWS live
# ─────────────────────────────────────────────────────────────────────────────

log "Explicitly denying IBKR Gateway ports on all public interfaces..."

for port in 4002 4001 7497 7496; do
  ufw deny in "${port}/tcp" comment "IBKR Gateway — LOOPBACK ONLY, NEVER PUBLIC"
done

log "IBKR Gateway ports 4001/4002/7496/7497 blocked on all public interfaces."
log "IB Gateway must bind to 127.0.0.1 only (configured in IBC config.ini)."

# ─────────────────────────────────────────────────────────────────────────────
# Enable ufw
# ─────────────────────────────────────────────────────────────────────────────

log "Enabling ufw..."
ufw --force enable

# ─────────────────────────────────────────────────────────────────────────────
# Print status
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "${BOLD}── Firewall Status ──${RESET}"
ufw status verbose
echo ""

echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo "${GREEN}  Firewall configured.${RESET}"
echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo ""
echo "  SSH:      allowed (port 22)"
echo "  Dashboard: $([ "${ALLOW_DASHBOARD}" == "true" ] && echo "allowed (port ${DASHBOARD_PORT}) — VERIFY IP RESTRICTION" || echo "blocked — use SSH tunnel")"
echo "  IBKR 4002: BLOCKED on public interfaces (loopback only)"
echo "  IBKR 4001: BLOCKED on public interfaces"
echo "  IBKR 7496: BLOCKED on public interfaces"
echo "  IBKR 7497: BLOCKED on public interfaces"
echo ""
echo "  To access dashboard via SSH tunnel:"
echo "    ssh -L ${DASHBOARD_PORT}:localhost:${DASHBOARD_PORT} user@<vm-ip>"
echo "    Then open: http://localhost:${DASHBOARD_PORT}"
echo ""
