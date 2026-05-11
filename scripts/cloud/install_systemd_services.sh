#!/usr/bin/env bash
# scripts/cloud/install_systemd_services.sh
#
# PURPOSE:
#   Copies Decifer systemd service files into /etc/systemd/system/ and
#   optionally enables and starts them.
#
# SAFETY DESIGN:
#   - Default mode: copies files only — no enable, no start
#   - --enable: enables services for boot (systemctl enable) — does not start
#   - --start: starts services — requires --confirm-start to prevent accidents
#   - Prints exactly what it will do before doing it
#   - Refuses to run without root
#
# USAGE:
#   # Dry run — inspect only, do nothing:
#   bash scripts/cloud/install_systemd_services.sh
#
#   # Copy service files to /etc/systemd/system (no enable, no start):
#   bash scripts/cloud/install_systemd_services.sh --install
#
#   # Copy + enable for boot (no start):
#   bash scripts/cloud/install_systemd_services.sh --install --enable
#
#   # Copy + enable + start (requires explicit confirmation flag):
#   bash scripts/cloud/install_systemd_services.sh --install --enable --start --confirm-start
#
# ⚠️  --start without --confirm-start is REJECTED.
# ⚠️  This script NEVER starts live-bot. Live-bot requires a separate
#     manual command with Amit's explicit approval (see runbook §20).

set -euo pipefail

BOLD=$(tput bold 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

log()  { echo "${GREEN}[install]${RESET} $*"; }
warn() { echo "${YELLOW}[warn  ]${RESET} $*"; }
err()  { echo "${RED}[ERROR ]${RESET} $*" >&2; }
step() { echo ""; echo "${BOLD}── $* ──${RESET}"; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    err "This script must be run as root (use sudo)."
    exit 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

DO_INSTALL=false
DO_ENABLE=false
DO_START=false
CONFIRM_START=false

for arg in "$@"; do
  case "${arg}" in
    --install)       DO_INSTALL=true ;;
    --enable)        DO_ENABLE=true ;;
    --start)         DO_START=true ;;
    --confirm-start) CONFIRM_START=true ;;
    --help|-h)
      echo "Usage: $0 [--install] [--enable] [--start --confirm-start]"
      echo ""
      echo "  --install        Copy service files to /etc/systemd/system/"
      echo "  --enable         Enable services at boot (requires --install)"
      echo "  --start          Start services now (requires --enable + --confirm-start)"
      echo "  --confirm-start  Required safety flag to allow --start"
      echo ""
      echo "  Default (no flags): print plan only, do nothing."
      exit 0
      ;;
    *)
      err "Unknown argument: ${arg}"
      exit 1
      ;;
  esac
done

# Safety gate: --start requires --confirm-start
if [[ "${DO_START}" == "true" && "${CONFIRM_START}" != "true" ]]; then
  err "--start requires --confirm-start as an additional safety flag."
  err "This prevents accidentally starting services."
  err ""
  err "If you are sure, run:"
  err "  $0 --install --enable --start --confirm-start"
  exit 1
fi

# --enable and --start require --install
if [[ "${DO_ENABLE}" == "true" && "${DO_INSTALL}" != "true" ]]; then
  err "--enable requires --install."
  exit 1
fi

require_root

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE_SRC_DIR="${REPO_ROOT}/ops/systemd"
SERVICE_DST_DIR="/etc/systemd/system"

# Services to manage — in dependency order (xvfb first, docker-stack last)
# ⚠️  live-bot is NOT in this list — it is NEVER started by this script
SERVICES=(
  "decifer-xvfb.service"
  "decifer-ibgateway.service"
  "decifer-docker-stack.service"
)

# ─────────────────────────────────────────────────────────────────────────────
# Preflight checks
# ─────────────────────────────────────────────────────────────────────────────

step "Preflight"

for svc in "${SERVICES[@]}"; do
  src="${SERVICE_SRC_DIR}/${svc}"
  if [[ ! -f "${src}" ]]; then
    err "Service file not found: ${src}"
    err "Run this script from the repo root or verify ops/systemd/ exists."
    exit 1
  fi
  log "Found: ${src}"
done

# ─────────────────────────────────────────────────────────────────────────────
# Print plan
# ─────────────────────────────────────────────────────────────────────────────

step "Plan"
echo ""
echo "  Services to manage (in order):"
for svc in "${SERVICES[@]}"; do
  echo "    - ${svc}"
done
echo ""
echo "  ⚠️  live-bot is NOT included. It requires separate manual start."
echo ""
echo "  Actions:"
echo "    Copy to ${SERVICE_DST_DIR}:  $([ "${DO_INSTALL}" == "true" ] && echo "YES" || echo "NO (--install not passed)")"
echo "    Enable at boot:               $([ "${DO_ENABLE}" == "true" ] && echo "YES" || echo "NO (--enable not passed)")"
echo "    Start now:                    $([ "${DO_START}" == "true" ] && echo "YES" || echo "NO (--start not passed)")"
echo ""

if [[ "${DO_INSTALL}" != "true" ]]; then
  warn "Dry run — no changes made."
  warn "Pass --install to copy service files."
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Install (copy service files)
# ─────────────────────────────────────────────────────────────────────────────

step "Installing service files"

for svc in "${SERVICES[@]}"; do
  src="${SERVICE_SRC_DIR}/${svc}"
  dst="${SERVICE_DST_DIR}/${svc}"
  cp "${src}" "${dst}"
  chmod 644 "${dst}"
  log "Installed: ${dst}"
done

systemctl daemon-reload
log "systemctl daemon-reload complete."

# ─────────────────────────────────────────────────────────────────────────────
# Enable (boot persistence)
# ─────────────────────────────────────────────────────────────────────────────

if [[ "${DO_ENABLE}" == "true" ]]; then
  step "Enabling services"
  for svc in "${SERVICES[@]}"; do
    systemctl enable "${svc}"
    log "Enabled: ${svc}"
  done
fi

# ─────────────────────────────────────────────────────────────────────────────
# Start
# ─────────────────────────────────────────────────────────────────────────────

if [[ "${DO_START}" == "true" && "${CONFIRM_START}" == "true" ]]; then
  step "Starting services"
  warn "Starting services in dependency order..."
  warn "Ensure IBC config.ini exists and has chmod 600 before proceeding."
  echo ""

  for svc in "${SERVICES[@]}"; do
    log "Starting: ${svc}"
    systemctl start "${svc}"
    sleep 2
    status=$(systemctl is-active "${svc}" 2>/dev/null || echo "unknown")
    if [[ "${status}" == "active" ]]; then
      log "  Status: ${GREEN}active${RESET}"
    else
      err "  Status: ${status} — check: journalctl -u ${svc} -n 50"
    fi
  done
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo "${GREEN}  Service installation complete.${RESET}"
echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo ""

for svc in "${SERVICES[@]}"; do
  status=$(systemctl is-active "${svc}" 2>/dev/null || echo "inactive")
  enabled=$(systemctl is-enabled "${svc}" 2>/dev/null || echo "disabled")
  echo "  ${svc}: status=${status}, enabled=${enabled}"
done

echo ""
echo "  ⚠️  live-bot was NOT started."
echo "  To start live-bot (Amit approval required, never during market hours):"
echo "    cd /opt/decifer/current"
echo "    docker compose --profile live up -d live-bot"
echo ""
echo "  Check logs: journalctl -u decifer-ibgateway -f"
echo "  Check logs: docker compose logs -f handoff-publisher"
echo ""
