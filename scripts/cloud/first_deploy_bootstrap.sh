#!/usr/bin/env bash
# scripts/cloud/first_deploy_bootstrap.sh
#
# PURPOSE:
#   First-deploy data and environment validation for the Decifer VM.
#   Runs on the VM after repo checkout, before any service is started.
#
# WHAT THIS SCRIPT DOES:
#   1. Creates /opt/decifer/current if it doesn't exist (should be a repo checkout)
#   2. Creates /opt/decifer/shared/data and /opt/decifer/shared/logs
#   3. Sets correct ownership (decifer UID 1000)
#   4. Runs scripts/bootstrap_runtime_dirs.py (creates runtime subdirectories)
#   5. Validates .env exists and has secure permissions (chmod 600)
#   6. Validates all mandatory env vars are present (values never printed)
#   7. Runs python3 scripts/healthcheck.py (or --strict if flag passed)
#
# WHAT THIS SCRIPT DOES NOT DO:
#   - Does not start bot.py
#   - Does not connect to IBKR
#   - Does not place orders
#   - Does not start Docker services
#   - Does not start IB Gateway
#
# USAGE:
#   # Standard validation:
#   bash scripts/cloud/first_deploy_bootstrap.sh
#
#   # Strict mode (all recommended env vars must also be set):
#   bash scripts/cloud/first_deploy_bootstrap.sh --strict
#
#   # Specify alternate .env location:
#   ENV_FILE=/etc/decifer/secrets.env bash scripts/cloud/first_deploy_bootstrap.sh

set -euo pipefail

BOLD=$(tput bold 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

log()  { echo "${GREEN}[bootstrap]${RESET} $*"; }
warn() { echo "${YELLOW}[warn     ]${RESET} $*"; }
err()  { echo "${RED}[ERROR    ]${RESET} $*" >&2; }
step() { echo ""; echo "${BOLD}── $* ──${RESET}"; }
ok()   { echo "${GREEN}  ✓${RESET} $*"; }
fail() { echo "${RED}  ✗${RESET} $*"; }

# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

STRICT_MODE=false
for arg in "$@"; do
  case "${arg}" in
    --strict) STRICT_MODE=true ;;
    --help|-h)
      echo "Usage: $0 [--strict]"
      echo ""
      echo "  --strict  Run healthcheck in strict mode (recommended env vars are blocking)"
      exit 0
      ;;
    *)
      err "Unknown argument: ${arg}"
      exit 1
      ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DECIFER_OPT="/opt/decifer"
SHARED_DATA="${DECIFER_OPT}/shared/data"
SHARED_LOGS="${DECIFER_OPT}/shared/logs"
DECIFER_UID=1000
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

FAILURES=0

# ─────────────────────────────────────────────────────────────────────────────
# 1. Directory structure
# ─────────────────────────────────────────────────────────────────────────────

step "Runtime directory structure"

for dir in \
  "${DECIFER_OPT}/current" \
  "${SHARED_DATA}" \
  "${SHARED_DATA}/live" \
  "${SHARED_DATA}/heartbeats" \
  "${SHARED_DATA}/intelligence" \
  "${SHARED_DATA}/universe_builder" \
  "${SHARED_DATA}/reference" \
  "${SHARED_DATA}/runtime" \
  "${SHARED_LOGS}"; do
  if [[ -d "${dir}" ]]; then
    ok "${dir}"
  else
    if mkdir -p "${dir}"; then
      ok "${dir} (created)"
    else
      fail "${dir} (could not create)"
      FAILURES=$((FAILURES + 1))
    fi
  fi
done

# Set ownership so the container (UID 1000) can write
if chown -R "${DECIFER_UID}:${DECIFER_UID}" "${DECIFER_OPT}/shared" 2>/dev/null; then
  log "Ownership set: ${DECIFER_OPT}/shared → UID ${DECIFER_UID}"
else
  warn "Could not set ownership on ${DECIFER_OPT}/shared — run as root if needed."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. bootstrap_runtime_dirs.py
# ─────────────────────────────────────────────────────────────────────────────

step "Runtime directory bootstrap (bootstrap_runtime_dirs.py)"

if [[ ! -f "${REPO_ROOT}/scripts/bootstrap_runtime_dirs.py" ]]; then
  fail "scripts/bootstrap_runtime_dirs.py not found at ${REPO_ROOT}"
  FAILURES=$((FAILURES + 1))
else
  if python3 "${REPO_ROOT}/scripts/bootstrap_runtime_dirs.py" --quiet; then
    ok "bootstrap_runtime_dirs.py exited 0"
  else
    fail "bootstrap_runtime_dirs.py exited non-zero"
    FAILURES=$((FAILURES + 1))
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. .env validation
# ─────────────────────────────────────────────────────────────────────────────

step ".env file validation"

if [[ ! -f "${ENV_FILE}" ]]; then
  fail ".env not found at ${ENV_FILE}"
  err ""
  err "Create it:"
  err "  cp ${REPO_ROOT}/.env.example ${ENV_FILE}"
  err "  vim ${ENV_FILE}   # populate all values"
  err "  chmod 600 ${ENV_FILE}"
  FAILURES=$((FAILURES + 1))
else
  ok ".env exists: ${ENV_FILE}"

  # Check permissions — must be 600 or 400 (readable only by owner)
  perms=$(stat -c "%a" "${ENV_FILE}" 2>/dev/null || stat -f "%A" "${ENV_FILE}" 2>/dev/null || echo "unknown")
  if [[ "${perms}" == "600" || "${perms}" == "400" ]]; then
    ok ".env permissions: ${perms} (secure)"
  else
    fail ".env permissions: ${perms} (INSECURE — must be 600)"
    err "Fix: chmod 600 ${ENV_FILE}"
    FAILURES=$((FAILURES + 1))
  fi

  # Check mandatory env vars are present (presence only — values never printed)
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}" 2>/dev/null || true; set +a

  MANDATORY_VARS=(
    "ANTHROPIC_API_KEY"
    "ALPACA_API_KEY"
    "ALPACA_SECRET_KEY"
    "IBKR_PAPER_ACCOUNT"
    "IBKR_ACTIVE_ACCOUNT"
  )

  for var in "${MANDATORY_VARS[@]}"; do
    if [[ -n "${!var:-}" ]]; then
      ok "env: ${var} is set"
    else
      fail "env: ${var} is MISSING or empty"
      FAILURES=$((FAILURES + 1))
    fi
  done

  RECOMMENDED_VARS=(
    "ALPACA_BASE_URL"
    "FMP_API_KEY"
    "FRED_API_KEY"
    "ALPHA_VANTAGE_KEY"
  )

  for var in "${RECOMMENDED_VARS[@]}"; do
    if [[ -n "${!var:-}" ]]; then
      ok "env: ${var} is set (recommended)"
    else
      if [[ "${STRICT_MODE}" == "true" ]]; then
        fail "env: ${var} is MISSING (required in --strict mode)"
        FAILURES=$((FAILURES + 1))
      else
        warn "env: ${var} is not set (recommended — degraded operation)"
      fi
    fi
  done
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. healthcheck.py
# ─────────────────────────────────────────────────────────────────────────────

step "Healthcheck (scripts/healthcheck.py)"

HEALTHCHECK_ARGS=()
if [[ "${STRICT_MODE}" == "true" ]]; then
  HEALTHCHECK_ARGS+=("--strict")
  log "Running in --strict mode"
fi

if python3 "${REPO_ROOT}/scripts/healthcheck.py" "${HEALTHCHECK_ARGS[@]:-}"; then
  ok "healthcheck.py passed"
else
  # Healthcheck failing on env vars is expected if run without a populated .env
  # In strict mode, all recommended vars must also pass
  if [[ "${STRICT_MODE}" == "true" ]]; then
    fail "healthcheck.py --strict failed (all blocking checks must pass)"
    FAILURES=$((FAILURES + 1))
  else
    warn "healthcheck.py failed (likely env vars — acceptable if .env not yet populated)"
    warn "Run with --strict after populating .env to confirm all checks pass."
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. Summary
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"

if [[ "${FAILURES}" -eq 0 ]]; then
  echo "${GREEN}  Bootstrap complete — ${FAILURES} failures.${RESET}"
  echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
  echo ""
  echo "  Next steps:"
  echo "  1. rsync data/ from dev machine (first deploy only):"
  echo "     rsync -avz /path/to/local/decifer/data/ ${SHARED_DATA}/"
  echo "     chown -R ${DECIFER_UID}:${DECIFER_UID} ${SHARED_DATA}"
  echo ""
  echo "  2. Install IBC (see ops/ibc/README.md)"
  echo ""
  echo "  3. Install and enable systemd services:"
  echo "     bash scripts/cloud/install_systemd_services.sh --install --enable"
  echo ""
  echo "  4. Start non-trading services (after IBC is ready):"
  echo "     bash scripts/cloud/install_systemd_services.sh --install --enable --start --confirm-start"
  echo ""
  echo "  5. Follow docs/cloud_phase1_vm_deployment_runbook.md §17 onwards."
  echo ""
  exit 0
else
  echo "${RED}  Bootstrap FAILED — ${FAILURES} failure(s) above.${RESET}"
  echo "${BOLD}═══════════════════════════════════════════════════════${RESET}"
  echo ""
  echo "  Fix all failures before proceeding."
  echo "  Do not start any service until bootstrap exits 0."
  echo ""
  exit 1
fi
