#!/usr/bin/env bash
# scripts/cloud/m9_install_tls.sh
#
# Sprint M9 — TLS installation for intelligence.decifertrading.com
#
# Runs on the DigitalOcean droplet as root (or sudo).
# Safe to re-run — all steps are idempotent.
#
# What this script does:
#   1. Creates /etc/ssl/decifer/
#   2. Generates a 2048-bit RSA self-signed certificate (3 years)
#      for intelligence.decifertrading.com
#   3. Installs the updated nginx config (deployment/nginx-intelligence.conf)
#      to /etc/nginx/sites-available/decifer-intelligence
#   4. Validates nginx config syntax
#   5. Reloads nginx
#   6. Curls https://intelligence.decifertrading.com/health to confirm 200
#
# What this script does NOT do:
#   - Does not touch decifer-pipeline or its nginx site
#   - Does not modify bot.py, execution, or broker connectivity
#   - Does not change any Python files
#   - Does not restart the decifer-intelligence container
#
# TLS note:
#   This installs a self-signed cert. Cloudflare SSL mode is set to "Full"
#   (not Strict), which accepts self-signed certs on the origin.
#   Upgrade path: replace the cert with a Cloudflare Origin Certificate and
#   set the zone to "Full (Strict)" — see M9 sprint report.
#
# Usage (run from /opt/decifer/current on the droplet):
#   sudo bash scripts/cloud/m9_install_tls.sh
#
# Expected output on success:
#   [m9] nginx syntax: ok
#   [m9] nginx reloaded
#   [m9] HTTPS health check: HTTP 200

set -euo pipefail

GREEN=$(tput setaf 2 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

ok()  { echo "${GREEN}[m9]${RESET} $*"; }
err() { echo "${RED}[m9-ERROR]${RESET} $*" >&2; }

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        err "Run as root: sudo bash $0"
        exit 1
    fi
}

require_root

CERT_DIR="/etc/ssl/decifer"
CERT_FILE="${CERT_DIR}/intelligence-selfsigned.crt"
KEY_FILE="${CERT_DIR}/intelligence-selfsigned.key"
NGINX_AVAILABLE="/etc/nginx/sites-available/decifer-intelligence"
NGINX_ENABLED="/etc/nginx/sites-enabled/decifer-intelligence"
REPO_NGINX="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/deployment/nginx-intelligence.conf"

# ── 1. Certificate directory ──────────────────────────────────────────────────
if [[ ! -d "${CERT_DIR}" ]]; then
    mkdir -p "${CERT_DIR}"
    chmod 700 "${CERT_DIR}"
    ok "Created ${CERT_DIR}"
else
    ok "${CERT_DIR} already exists"
fi

# ── 2. Self-signed certificate ────────────────────────────────────────────────
if [[ -f "${CERT_FILE}" ]] && [[ -f "${KEY_FILE}" ]]; then
    ok "Cert already exists at ${CERT_FILE} — skipping generation"
    ok "  Delete and re-run to regenerate: rm ${CERT_FILE} ${KEY_FILE}"
else
    ok "Generating self-signed certificate (3 years)..."
    openssl req -x509 -nodes -days 1095 \
        -newkey rsa:2048 \
        -keyout "${KEY_FILE}" \
        -out "${CERT_FILE}" \
        -subj "/C=CA/ST=Ontario/L=Toronto/O=Decifer/CN=intelligence.decifertrading.com" \
        -addext "subjectAltName=DNS:intelligence.decifertrading.com,DNS:*.decifertrading.com" \
        2>/dev/null
    chmod 600 "${KEY_FILE}"
    chmod 644 "${CERT_FILE}"
    ok "Certificate written to ${CERT_FILE}"
    ok "Private key written to ${KEY_FILE} (mode 600)"
fi

# ── 3. Install nginx config ───────────────────────────────────────────────────
if [[ ! -f "${REPO_NGINX}" ]]; then
    err "Repo nginx config not found at ${REPO_NGINX}"
    err "Pull the latest repo changes and retry."
    exit 1
fi

cp "${REPO_NGINX}" "${NGINX_AVAILABLE}"
ok "Installed nginx config → ${NGINX_AVAILABLE}"

# Ensure symlink exists in sites-enabled
if [[ ! -L "${NGINX_ENABLED}" ]]; then
    ln -s "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
    ok "Symlink created: ${NGINX_ENABLED}"
else
    ok "Symlink already exists: ${NGINX_ENABLED}"
fi

# ── 4. Validate nginx syntax ──────────────────────────────────────────────────
if nginx -t 2>&1 | grep -q "syntax is ok"; then
    ok "nginx syntax: ok"
else
    err "nginx -t FAILED. Rolling back nginx config."
    err "Check: nginx -t"
    # Restore a minimal safe fallback (port 80 only) so the service stays up
    cat > "${NGINX_AVAILABLE}" <<'FALLBACK'
server {
    listen 80;
    server_name intelligence.decifertrading.com;
    location = /health { proxy_pass http://127.0.0.1:8001; }
    location /api/     { limit_except GET { deny all; } proxy_pass http://127.0.0.1:8001; }
    location /         { return 404; }
}
FALLBACK
    nginx -t 2>/dev/null && systemctl reload nginx || true
    err "Fallback config restored. Fix the M9 config and retry."
    exit 1
fi

# ── 5. Reload nginx ───────────────────────────────────────────────────────────
systemctl reload nginx
ok "nginx reloaded"

# ── 6. Confirm HTTPS health check ────────────────────────────────────────────
echo ""
ok "Waiting 3s for nginx to settle..."
sleep 3

# Test via direct HTTPS to localhost (skip cert verification — self-signed)
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" \
    --resolve "intelligence.decifertrading.com:443:127.0.0.1" \
    "https://intelligence.decifertrading.com/health" || echo "000")

if [[ "${HTTP_CODE}" == "200" ]]; then
    ok "Local HTTPS health check: HTTP ${HTTP_CODE} — PASS"
else
    err "Local HTTPS health check: HTTP ${HTTP_CODE} — FAIL"
    err "Check: curl -vk --resolve intelligence.decifertrading.com:443:127.0.0.1 https://intelligence.decifertrading.com/health"
    exit 1
fi

# ── 7. Confirm HTTP redirect ─────────────────────────────────────────────────
REDIRECT_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1/health" \
    -H "Host: intelligence.decifertrading.com" || echo "000")
if [[ "${REDIRECT_CODE}" == "301" ]]; then
    ok "HTTP → HTTPS redirect: ${REDIRECT_CODE} — PASS"
else
    ok "HTTP redirect check returned ${REDIRECT_CODE} (may differ depending on local port binding)"
fi

echo ""
ok "─────────────────────────────────────────────────"
ok "M9 TLS install complete."
ok ""
ok "Next: verify from outside the droplet:"
ok "  curl -i https://intelligence.decifertrading.com/health"
ok "  python3 scripts/smoke_test_intelligence_cloud.py \\"
ok "      --url https://intelligence.decifertrading.com --verbose"
ok "─────────────────────────────────────────────────"
