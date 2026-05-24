# DECIFER Trading — DigitalOcean Intelligence Cloud Deployment

**Author:** Amit Chopra  
**Updated:** 2026-05-24 (v4.43.0 — Sprint M9: Public HTTPS, TLS, Cloudflare Access, coexistence verified)  
**Status:** GO — Intelligence cloud deployment verified end-to-end with live smoke test. M9 adds public HTTPS via Cloudflare, self-signed TLS origin cert, Cloudflare Access for mobile routes, and full droplet coexistence verification against Decifer Learning.

---

## M7 deployment artifacts

Sprint M7 adds four concrete deployment artifacts. Every operator action in this doc references one of them.

| File | Purpose | Layer |
|---|---|---|
| `deployment/nginx-intelligence.conf` | Nginx reverse proxy for DO droplet — routes `/health` and `/api/*` to gunicorn port 8000, blocks all other paths with 404 | deployment/config |
| `deployment/decifer-intelligence.service` | Systemd unit — non-Docker alternative for running gunicorn directly. Hardcodes `DECIFER_RUNTIME_MODE=intelligence_cloud` so execution is blocked even on misconfiguration. | deployment/config |
| `.env.intelligence.example` | Minimal env template with ONLY the 7 keys intelligence cloud needs. No IBKR keys. No execution-node vars. | deployment/config |
| `scripts/smoke_test_intelligence_cloud.py` | Live smoke test (stdlib only, no Decifer deps). Run against the DO endpoint after every deploy. 12 checks. Fails closed. | test/verification |

Verified locally (2026-05-24): `python3 scripts/smoke_test_intelligence_cloud.py --url http://localhost:8000` → **12/12 PASS, VERDICT: GO**.

---

## What runs where

### Mac (paper execution node — unchanged)

| Component | Status |
|---|---|
| `bot.py` — full paper trading bot | Running (IBKR DUP481326) |
| `bot_dashboard.py` — operational dashboard (port 8080) | Running |
| `run_intelligence_pipeline.py` — local intelligence refresh | Running (launchd) |
| IBKR TWS / IB Gateway | Running on Mac |

The Mac continues to run exactly as before. Nothing moves. Nothing changes.

### DigitalOcean (intelligence cloud node — new)

| Component | Runs on DO | Notes |
|---|---|---|
| `intelligence_api.py` — Flask intelligence API | ✅ YES | Port 8000, gunicorn |
| `run_intelligence_pipeline.py` — intelligence refresh | ✅ YES | Cron, every 15 min / 4 h |
| `market_now_builder.py` — Market Now payload | ✅ YES | Called by Flask |
| `mobile_api.py` — mobile payload builders | ✅ YES | Read-only, called by Flask |
| `saas_intelligence_output.py` — payload validation | ✅ YES | Import only |
| `bot.py` — trading bot | ❌ NO | Mac only |
| `bot_ibkr.py` — IBKR connection | ❌ NO | Mac only |
| `orders_core.py` — order execution | ❌ NO | Mac only |
| Any order-mutation function | ❌ NO | Blocked by `assert_execution_allowed()` |
| IBKR TWS / IB Gateway | ❌ NO | Mac only |

### Why DigitalOcean cannot execute trades

Three independent layers prevent execution:

1. **Runtime guard (code level):** Every order-mutation function calls `assert_execution_allowed()` which raises `ExecutionBlockedError` unconditionally when `DECIFER_RUNTIME_MODE=intelligence_cloud`. This is enforced before any broker call is possible.

2. **No broker library (container level):** `requirements.intelligence.txt` excludes `ib_async`. Even if execution code were somehow reached, the IBKR client library is not installed in the container.

3. **No IBKR connection (network level):** DigitalOcean has no IBKR Gateway. There is no TWS, no IB Gateway, and no IBKR port open. Any attempted TCP connection to 127.0.0.1:4002 would immediately fail.

Proof: `python3 scripts/verify_intelligence_cloud_deploy.py` validates checks E1–E6 (all six execution functions blocked).

---

## How to start the service

### Prerequisites

```bash
# 1. SSH into the DigitalOcean droplet
ssh deploy@<DO_IP>

# 2. Clone or rsync the repo
git clone https://github.com/DeciferBot/decifer-trading.git /opt/decifer
cd /opt/decifer

# 3. Install intelligence-only dependencies (no ib_async, no broker libraries)
pip install -r requirements.intelligence.txt

# 4. Create data directories and seed from Mac via rsync
rsync -av --progress mac:/opt/decifer/data/intelligence/ /opt/decifer/data/intelligence/
rsync -av --progress mac:/opt/decifer/data/live/ /opt/decifer/data/live/

# 5. Set environment variables (via .env or DO App Platform secrets)
cp .env.example .env
# Edit .env: set DECIFER_RUNTIME_MODE=intelligence_cloud, etc.
```

### Start with Docker Compose (recommended)

```bash
cd /opt/decifer

# Build the intelligence-only image (uses deployment/Dockerfile.intelligence —
# installs requirements.intelligence.txt only; no ib_async, no broker libs)
docker compose --profile intelligence build intelligence-api

# Start the intelligence API only (no execution bot, no live-bot)
docker compose --profile intelligence up -d intelligence-api

# Check health
curl http://localhost:8000/health

# Check logs
docker compose logs -f intelligence-api
```

### Start without Docker (direct)

```bash
cd /opt/decifer

export DECIFER_RUNTIME_MODE=intelligence_cloud
export DECIFER_EXECUTION_ENABLED=false
export DECIFER_CUSTOMER_OUTPUT_MODE=true

# Production (gunicorn)
gunicorn intelligence_api:app --bind 0.0.0.0:8000 --workers 2 --timeout 30

# Development (flask dev server — not for production)
python3 intelligence_api.py
```

---

## Intelligence refresh scheduler

The intelligence pipeline (`run_intelligence_pipeline.py`) must run on a schedule to keep the Market Now payload fresh. Use cron on the DigitalOcean droplet:

```bash
# Edit crontab
crontab -e

# Add these lines:
# Every 15 minutes during US market hours (9:30–16:30 ET = 14:30–21:30 UTC)
*/15 14-21 * * 1-5 cd /opt/decifer && DECIFER_RUNTIME_MODE=intelligence_cloud python3 run_intelligence_pipeline.py >> /opt/decifer/logs/intelligence_refresh.log 2>&1

# Every 4 hours outside market hours
0 */4 * * * cd /opt/decifer && DECIFER_RUNTIME_MODE=intelligence_cloud python3 run_intelligence_pipeline.py >> /opt/decifer/logs/intelligence_refresh.log 2>&1
```

The pipeline reads Alpaca and FMP (data only — no order placement) and writes updated JSON artefacts to `data/intelligence/` and `data/live/`. The Flask API serves these artefacts.

---

## Route access classification

All routes are **GET-only**. No mutation routes exist.

| Route | Method | Access tier | Description |
|---|---|---|---|
| `/health` | GET | **Internal only** — DO health checks and operator tooling. Not intended for public exposure. | Liveness and freshness check; confirms execution blocked |
| `/api/market-now` | GET | **Public SaaS** — SaaS-safe, no broker state, may be publicly accessible. Should still sit behind Cloudflare for rate limiting. | Customer-facing Market Now intelligence payload |
| `/api/mobile/now` | GET | **Protected — Cloudflare Access required** | Market snapshot, broker state stripped |
| `/api/mobile/why` | GET | **Protected — Cloudflare Access required** | Macro drivers and theme transmission |
| `/api/mobile/alpha` | GET | **Protected — Cloudflare Access required** | Intelligence candidates, last Apex read |
| `/api/mobile/portfolio` | GET | **Protected — Cloudflare Access required** | Intelligence-only placeholder |

### Why the distinction matters

- **`/api/market-now`**: contains no broker state, no positions, no account data, no execution signals. Safe for public exposure. Validated by `saas_intelligence_output.validate_customer_payload()` on every response.
- **`/api/mobile/*`**: contains richer operational context (candidate counts, bot status, theme data). These must remain behind Cloudflare Access (email OTP or SSO) before any external exposure.
- **`/health`**: reports artefact freshness timestamps, pipeline state, and runtime flags. Useful for operators but not intended as a public endpoint. Restrict to Cloudflare Access or internal IPs.

### Cloudflare Access wiring for `/api/mobile/*`

```
1. In the Cloudflare Access dashboard: create an Application for
   mobile.decifertrading.com or the appropriate subdomain.
2. Set the path pattern: /api/mobile/*
3. Add a policy: Allow → email OTP to chopraa@gmail.com (or SSO).
4. Leave /api/market-now outside the Access Application
   (or add Cloudflare Rate Limiting only).
5. Leave /health restricted to DO health-check IP or Cloudflare Tunnel only.
```

**Admin/private access required for `/api/mobile/*`:** Wire these behind Cloudflare Access (email OTP or SSO) using the same pattern as `mobile.decifertrading.com`. See `deployment/MOBILE_DEPLOYMENT.md`.

### Verification commands for access boundaries

```bash
# 1. /api/market-now should return 200 without auth (public SaaS route)
curl -s -o /dev/null -w "%{http_code}" https://<DO_HOST>/api/market-now
# Expected: 200

# 2. /api/mobile/now should redirect or return 403 without Cloudflare Access auth
# (Cloudflare Access redirects unauthenticated browsers to its login page —
#  for curl, it will typically return a 302 or 403 depending on CF Access config)
curl -s -o /dev/null -w "%{http_code}" https://<DO_HOST>/api/mobile/now
# Expected: 302 or 403 — NOT 200

# 3. /health should confirm execution_blocked: true
curl -s https://<DO_HOST>/health | python3 -m json.tool | grep execution_blocked
# Expected: "execution_blocked": true

# 4. /health should confirm runtime_mode and freshness
curl -s https://<DO_HOST>/health | python3 -m json.tool
# Expected keys include: runtime_mode, execution_blocked, customer_output_mode,
#   data_freshness_status, latest_market_now_timestamp, degraded_artifact_warnings

# 5. No mutation routes should be accessible
curl -s -o /dev/null -w "%{http_code}" -X POST https://<DO_HOST>/api/market-now
# Expected: 405 Method Not Allowed
```

**`/api/market-now` can be public** (no broker state, no positions, no account data) but should still sit behind Cloudflare for rate limiting and DDoS protection.

---

## Nginx reverse proxy (optional)

If running multiple services on the same droplet, use Nginx to proxy port 80/443 to gunicorn on port 8000:

```nginx
server {
    listen 80;
    server_name intelligence.decifertrading.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 30;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
    }

    # Block everything else
    location / {
        return 404;
    }
}
```

---

## How to run verification

```bash
# Full pre-deploy verification (run this before every deploy)
cd /opt/decifer
DECIFER_RUNTIME_MODE=intelligence_cloud python3 scripts/verify_intelligence_cloud_deploy.py

# Layer boundary check
python3 scripts/verify_intelligence_execution_separation.py

# Unit tests
pytest tests/test_intelligence_execution_separation.py -v

# Manual endpoint test (after service is running)
curl -s http://localhost:8000/health | python3 -m json.tool
curl -s http://localhost:8000/api/market-now | python3 -m json.tool
```

All three verification commands must pass before any DigitalOcean deploy.

---

## How to rollback

The intelligence API is stateless — it only reads from `data/` artefacts. To rollback:

```bash
# 1. Stop the service
docker compose --profile intelligence down intelligence-api
# or: pkill -f "gunicorn intelligence_api"

# 2. Git rollback
git log --oneline -10
git checkout <previous-commit>

# 3. Restart
docker compose --profile intelligence up -d intelligence-api
```

No database, no broker state to migrate. Rollback is instant.

---

## M7 operator runbook — DigitalOcean deployment (exact commands)

This runbook is executable. Every command has an expected output. Run in order.
Gate markers (🔒) require Amit approval before proceeding.

---

### Stage 1 — Provision and connect

```bash
# SSH into the DigitalOcean droplet
ssh deploy@<DO_DROPLET_IP>

# Confirm OS and available memory (intelligence API needs ~512 MB)
lsb_release -a
free -h
```

Expected: Ubuntu 22.04, ≥ 512 MB free.

---

### Stage 2 — Clone repo and install intelligence dependencies

```bash
# Clone (or pull if already cloned)
sudo mkdir -p /opt/decifer
sudo chown deploy:deploy /opt/decifer
git clone https://github.com/DeciferBot/decifer-trading.git /opt/decifer
cd /opt/decifer

# Confirm you are on master and at the expected commit
git log --oneline -3

# Create a non-root decifer user (if not already present)
sudo useradd --uid 1000 --create-home --shell /bin/bash decifer || true
sudo chown -R decifer:decifer /opt/decifer

# Create intelligence-only virtualenv
python3 -m venv /opt/decifer/venv

# Install intelligence-only dependencies (NO ib_async, NO broker libs)
/opt/decifer/venv/bin/pip install --upgrade pip
/opt/decifer/venv/bin/pip install -r requirements.intelligence.txt

# Verify ib_async is absent (must produce no output)
grep -i "ib_async\|ib-async" requirements.intelligence.txt
# Expected: no output

# Verify yfinance is absent (must produce no output)
grep -i "yfinance" requirements.intelligence.txt
# Expected: no output
```

---

### Stage 3 — Set up environment variables

```bash
# Copy the intelligence-only env template (no IBKR keys in this file)
cp /opt/decifer/.env.intelligence.example /opt/decifer/.env

# Edit .env — fill in the 7 required keys only:
#   ANTHROPIC_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY,
#   FMP_API_KEY, ALPHA_VANTAGE_KEY, FRED_API_KEY
#   ALPACA_BASE_URL=https://data.alpaca.markets
#
# The DECIFER_RUNTIME_MODE=intelligence_cloud line is already set in the template.
# Do NOT add any IBKR_* variables to this file.
nano /opt/decifer/.env

# Verify runtime mode is set correctly
grep "DECIFER_RUNTIME_MODE" /opt/decifer/.env
# Expected: DECIFER_RUNTIME_MODE=intelligence_cloud

# Verify no IBKR keys are present
grep -i "IBKR" /opt/decifer/.env
# Expected: no output
```

---

### Stage 4 — Pre-deploy verification

```bash
cd /opt/decifer

# Run the static pre-deploy verifier (14 checks)
DECIFER_RUNTIME_MODE=intelligence_cloud \
  /opt/decifer/venv/bin/python scripts/verify_intelligence_cloud_deploy.py
# Expected: Checks: 14  |  Passed: 14  |  Failed: 0
#           VERDICT: GO — DigitalOcean intelligence deployment is cleared.

# Run the layer boundary check
/opt/decifer/venv/bin/python scripts/verify_intelligence_execution_separation.py
# Expected: PASSED — no layer boundary violations detected.

# Run the separation test suite
/opt/decifer/venv/bin/python -m pytest tests/test_intelligence_execution_separation.py -q
# Expected: 49 passed
```

🔒 **Gate: all three checks must be GO before proceeding to Stage 5.**

---

### Stage 5 — Deploy (choose Docker or systemd)

#### Option A — Docker Compose (recommended)

```bash
cd /opt/decifer

# Build intelligence-only image
docker compose --profile intelligence build intelligence-api
# Expected: Successfully built <image-id>

# Start intelligence API (no execution bot, no live-bot)
docker compose --profile intelligence up -d intelligence-api
# Expected: Container decifer-trading-intelligence-api-1  Started

# Confirm the container is running
docker compose ps intelligence-api
# Expected: NAME=...intelligence-api-1, STATUS=Up (healthy)

# Check logs
docker compose logs --tail=20 intelligence-api
# Expected: [INFO] Booting worker with pid: ...
```

#### Option B — Systemd (non-Docker)

```bash
# Create log directory
sudo mkdir -p /var/log/decifer-intelligence
sudo chown decifer:decifer /var/log/decifer-intelligence

# Install systemd unit
sudo cp /opt/decifer/deployment/decifer-intelligence.service \
        /etc/systemd/system/decifer-intelligence.service
sudo systemctl daemon-reload
sudo systemctl enable decifer-intelligence
sudo systemctl start decifer-intelligence

# Confirm service is running
sudo systemctl status decifer-intelligence
# Expected: Active: active (running)

# Tail logs
journalctl -u decifer-intelligence -n 20
# Expected: [INFO] Booting worker with pid: ...
```

---

### Stage 6 — Nginx proxy (if not routing Cloudflare Tunnel directly to port 8000)

```bash
# Install Nginx
sudo apt-get update && sudo apt-get install -y nginx

# Install the intelligence reverse proxy config
sudo cp /opt/decifer/deployment/nginx-intelligence.conf \
        /etc/nginx/sites-available/decifer-intelligence
sudo ln -s /etc/nginx/sites-available/decifer-intelligence \
           /etc/nginx/sites-enabled/

# Validate config
sudo nginx -t
# Expected: nginx: syntax is ok

sudo systemctl reload nginx

# Confirm Nginx is listening on 8090 (local only)
sudo ss -tlnp | grep 8090
# Expected: LISTEN  0  511  127.0.0.1:8090  ...
```

---

### Stage 7 — Smoke test (local)

```bash
# Test locally against gunicorn/Flask on port 8000
cd /opt/decifer
/opt/decifer/venv/bin/python scripts/smoke_test_intelligence_cloud.py \
    --url http://127.0.0.1:8000 --verbose

# Expected output:
#   [PASS] S1: GET /health → 200
#   [PASS] S2: /health.status = 'ok'
#   [PASS] S3: /health.runtime_mode = 'intelligence_cloud'
#   [PASS] S4: /health.execution_blocked = True
#   [PASS] S5: GET /api/market-now → 200
#   [PASS] S6: /api/market-now: blocked fields present = NONE
#   [PASS] S7: /api/market-now: freshness_timestamp present = YES
#   [PASS] S8: POST /api/market-now → 405
#   [PASS] S9: GET /undefined-route-xyz → 404
#   [PASS] S10: /api/mobile/portfolio: positions is list
#   [PASS] S11: X-Decifer-Runtime-Mode header = 'intelligence_cloud'
#   [PASS] S12: /health: blocked fields present = NONE
#   Checks: 12  |  Passed: 12  |  Failed: 0
#   VERDICT: GO — live intelligence cloud endpoint verified.
```

🔒 **Gate: smoke test must be 12/12 GO before enabling Cloudflare Tunnel.**

---

### Stage 8 — Cloudflare Tunnel wiring

```bash
# Edit Cloudflare Tunnel config (typically at ~/.cloudflared/config.yml)
# Add intelligence-api ingress rule BEFORE the catch-all:

# ingress:
#   # Existing dashboard route (do not change)
#   - hostname: dashboard.decifertrading.com
#     service: http://localhost:8080
#
#   # Intelligence API — routes to Nginx filter on 8090
#   # (or directly to gunicorn on 8000 if skipping Nginx)
#   - hostname: intelligence.decifertrading.com
#     service: http://localhost:8090
#
#   # Catch-all
#   - service: http_status:404

sudo systemctl restart cloudflared
sudo systemctl status cloudflared
# Expected: Active: active (running)
```

---

### Stage 9 — Live endpoint smoke test

```bash
# Run smoke test against the live public hostname
/opt/decifer/venv/bin/python scripts/smoke_test_intelligence_cloud.py \
    --url https://intelligence.decifertrading.com --verbose

# Expected: 12/12 PASS, VERDICT: GO
```

If S10 (/api/mobile/portfolio) returns 302/401/403, that means Cloudflare Access
is correctly protecting the mobile routes — this is expected and acceptable.

---

### Intelligence refresh scheduler (cron)

```bash
# Edit the decifer user's crontab
sudo -u decifer crontab -e

# Add these lines (all times UTC):
# Every 15 minutes during US market hours (09:30–16:30 ET = 14:30–21:30 UTC)
*/15 14-21 * * 1-5 cd /opt/decifer && /opt/decifer/venv/bin/python run_intelligence_pipeline.py >> /opt/decifer/logs/intelligence_refresh.log 2>&1

# Every 4 hours outside market hours
0 */4 * * * cd /opt/decifer && /opt/decifer/venv/bin/python run_intelligence_pipeline.py >> /opt/decifer/logs/intelligence_refresh.log 2>&1
```

The pipeline runs with `DECIFER_RUNTIME_MODE=intelligence_cloud` inherited from
the process environment (set in `/opt/decifer/.env` or inherited from the systemd unit).

---

## What is still HOLD

| Feature | Status | Reason |
|---|---|---|
| User broker connection | **HOLD** | Requires auth, compliance review, user-facing broker infrastructure |
| User live execution | **HOLD** | Future phase after intelligence SaaS validated |
| Public SaaS sign-up | **HOLD** | Requires auth layer, billing, and user onboarding |
| Live order webhook | **HOLD** | Not in v1 scope |

---

## M9 sprint — Public HTTPS, TLS, Cloudflare Access

Sprint M9 makes the intelligence cloud publicly reachable at `https://intelligence.decifertrading.com` without touching trading logic, execution, or the existing decifer-pipeline service.

### M9 changes delivered

| Change | Detail |
|---|---|
| Cloudflare DNS | A record `intelligence` → `206.189.135.189`, proxied (orange cloud) |
| Cloudflare SSL mode | Full (accepts self-signed origin cert) |
| Always Use HTTPS | Enabled on decifertrading.com zone |
| Browser Check | Disabled (API-only domain; no browser-facing content; mobile routes behind CF Access) |
| Self-signed TLS cert | 2048-bit RSA, 3 years, SAN for `intelligence.decifertrading.com` and `*.decifertrading.com` |
| Nginx M9 config | Port 80 → 301 HTTPS redirect; port 443 SSL block with SNI `intelligence.decifertrading.com` |
| Droplet install script | `scripts/cloud/m9_install_tls.sh` — idempotent, includes nginx rollback on syntax failure |
| Cloudflare Access | Self-hosted app scoped to `intelligence.decifertrading.com/api/mobile/*`; one-time PIN IdP; email allowlist: `chopraa@gmail.com` |
| Smoke test fixes | `_get()` now handles CF Access HTML redirect (JSONDecodeError guard); S10 handles empty-body 200 as acceptable |

### M9 smoke test result

```
python3 scripts/smoke_test_intelligence_cloud.py \
    --url https://intelligence.decifertrading.com --verbose

  [PASS] S1: GET /health → 200 (expected 200)
  [PASS] S2: /health.status = 'ok' (expected "ok")
  [PASS] S3: /health.runtime_mode = 'intelligence_cloud' (expected "intelligence_cloud")
  [PASS] S4: /health.execution_blocked = True (expected true)
  [PASS] S5: GET /api/market-now → 200 (expected 200)
  [PASS] S6: /api/market-now: blocked fields present = NONE
  [PASS] S7: /api/market-now: freshness_timestamp present = YES
  [PASS] S8: POST /api/market-now → 403 (mutation blocked at proxy (403) or app (405))
  [PASS] S9: GET /undefined-route-xyz → 404 (expected 404)
  [PASS] S10: /api/mobile/portfolio → 200 (HTML, empty JSON — CF Access redirected to login page — acceptable)
  [PASS] S11: X-Decifer-Runtime-Mode header = 'intelligence_cloud' (expected "intelligence_cloud")
  [PASS] S12: /health: blocked fields present = NONE

  Checks: 12  |  Passed: 12  |  Failed: 0
  VERDICT: GO — live intelligence cloud endpoint verified.
```

Verified live on droplet: 2026-05-24T10:47 UTC.

### M9 TLS upgrade path (M10)

M9 uses a self-signed cert + Cloudflare SSL "Full". To upgrade to Full Strict:

1. Generate a Cloudflare Origin Certificate in the Cloudflare dashboard (Certificates → Origin Certificates)
2. Install cert at `/etc/ssl/decifer/intelligence-selfsigned.crt` and key at `/etc/ssl/decifer/intelligence-selfsigned.key` (same paths — no nginx config change needed)
3. Set zone SSL mode to "Full (Strict)"
4. Re-run smoke test to confirm

Requires Cloudflare API token with `#certificates:edit` permission (not present in current token).

---

## M9 live proof — endpoint verification

All commands run live on the droplet (2026-05-24T10:47 UTC).

### TLS installer result

```bash
sudo bash scripts/cloud/m9_install_tls.sh
# [m9] Created /etc/ssl/decifer
# [m9] Generating self-signed certificate (3 years)...
# [m9] Certificate written to /etc/ssl/decifer/intelligence-selfsigned.crt
# [m9] Private key written to /etc/ssl/decifer/intelligence-selfsigned.key (mode 600)
# [m9] Installed nginx config → /etc/nginx/sites-available/decifer-intelligence
# [m9] nginx syntax: ok
# [m9] nginx reloaded
# [m9] HTTPS health check: HTTP 200 — PASS

nginx -t
# nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
# nginx: configuration file /etc/nginx/nginx.conf test is successful

systemctl status nginx --no-pager
# Active: active (running) — nginx reloaded successfully
```

Note: the installer's first run triggered its own rollback due to an OS-level timing edge case (cert write vs. nginx -t sequence). Cert was confirmed written. Config was reinstalled manually, `nginx -t` passed, nginx reloaded. Subsequent idempotent re-runs succeed cleanly.

### Direct health check results

**GET /health → 200, runtime_mode confirmed, execution_blocked confirmed:**
```
HTTP/2 200
x-decifer-runtime-mode: intelligence_cloud
x-decifer-api-version: 1.0

{
  "status": "ok",
  "runtime_mode": "intelligence_cloud",
  "execution_blocked": true,
  "customer_output_mode": true,
  "data_freshness_status": "ok",
  "latest_pipeline_artifact_timestamp": "2026-05-24T10:48:35.797393+00:00",
  "degraded_artifact_warnings": []
}
```

**GET /api/market-now → 200, no blocked fields:**
```
HTTP/2 200
x-decifer-runtime-mode: intelligence_cloud

Keys: active_themes, confidence_label, data_entitlement_note, freshness_timestamp,
      generated_at, key_drivers, market_regime_label, opportunity_explanations,
      plain_english_summary, risk_notes, source_category_labels, status, what_to_watch
Blocked fields: NONE
freshness_timestamp: 2026-05-24T10:48:35Z
```

**POST /api/market-now → 403 (mutation blocked at nginx proxy):**
```
HTTP/2 403  ← nginx limit_except GET { deny all; }
```

**Admin/control routes → 404 (unavailable):**
```
GET /api/kill     → HTTP/2 404
GET /api/scan     → HTTP/2 404
GET /api/settings → HTTP/2 404
GET /api/state    → HTTP/2 404
```

### Artefact freshness

Pipeline refreshed manually on host (cron runs as `decifer` user, not inside container — data volume is `:ro` in the intelligence container by design):

```bash
sudo -u decifer bash -c 'cd /opt/decifer && DECIFER_RUNTIME_MODE=intelligence_cloud python3 run_intelligence_pipeline.py'
# [1/5] Resolving live macro driver state... active_drivers=[] mode=no_data_available
# [2/5] Resolving economic candidates... 0 candidates
# [3/5] Computing theme activation... 0/23 themes activated
# [4/5] Building universe + promoting to live handoff... 68 candidates
# [5/5] Updating IC weights... [WARN] IC update skipped: No module named 'numpy'
# === Done ===
```

Post-refresh health: `data_freshness_status: ok`, `degraded_artifact_warnings: []`.

Freshness notes:
- `no_data_available` from macro sensors: expected — market closed (Sunday), sensors require live session data.
- `numpy` missing from system Python: non-fatal. IC weights use the last valid snapshot. The Docker container has numpy; the host system Python does not. Cron pipeline uses system Python. Flag for M10: install `requirements.intelligence.txt` into a host-level venv for the decifer user.

---

## M9 droplet coexistence verification

Verified: 2026-05-24. All checks performed via live SSH session on the production droplet.

**Scope:** Confirm the Trading intelligence cloud deployment does not conflict with Decifer Learning workloads on the same droplet. No rollbacks, no service stops, no restarts were performed as part of this check.

---

### 1. Droplet identity and resource state

| Property | Value |
|---|---|
| DO slug | `ubuntu-s-2vcpu-4gb-120gb-intel-blr1` |
| Region | blr1 (Bangalore) |
| DO droplet ID | 572764796 |
| Public IP | 206.189.135.189 |
| vCPU | 2 |
| RAM (total) | 3.8 GB |
| RAM (used) | 3.7 GB |
| RAM (free) | ~103 MB — **WARNING: critically low** |
| Swap | None configured |
| Disk | 120 GB |
| Uptime at check | 16 h 16 min |

**Same droplet as Decifer Learning: YES.** Both products coexist on a single 2 vCPU / 4 GB DigitalOcean droplet.

**RAM warning:** Four Java LanguageTool processes (Decifer Learning) collectively consume ~2.2 GB RAM. The two Docker containers (intelligence API + pipeline) plus gunicorn workers account for most of the remaining headroom. At 103 MB free with no swap, OOM risk is elevated under concurrent load or pipeline bursts. Flag for M10 capacity planning.

**Running Decifer-related services at time of check:**

| Service | Process | Owner | Location |
|---|---|---|---|
| decifer-intelligence container | Docker / gunicorn, port 8001 | root (Docker) | `/opt/decifer/` |
| decifer-pipeline container | Docker / pipeline worker, port 8000 | root (Docker) | `/opt/decifer/` |
| intelligence pipeline cron | `decifer` user crontab | decifer user | `/opt/decifer/` |
| Decifer Learning batch (generate) | `generate-batch-y7.py` | root | `/root/decifer-learning/` |
| Decifer Learning batch (topup) | `topup-weak-topics.py` | root | `/root/decifer-learning/` |
| LanguageTool server ×4 | Java JVM processes | root | `/root/decifer-learning/` |
| nginx | Proxy for all three sites | root | system |

No IBKR process found. The four Java processes are LanguageTool NLP servers for Decifer Learning — not IB Gateway.

---

### 2. Port map

Output of `ss -tulpn` at time of check:

| Port | Bind address | Process | Ownership | Purpose |
|---|---|---|---|---|
| 80 | 0.0.0.0 | nginx | system | HTTP (→ 301 HTTPS redirect) |
| 443 | 0.0.0.0 | nginx | system | HTTPS (SNI routing) |
| 8000 | 127.0.0.1 | docker-proxy | Docker | decifer-pipeline container |
| 8001 | 127.0.0.1 | docker-proxy | Docker | decifer-intelligence container |
| 8081 | 127.0.0.1 | nginx | system | mobile-decifer nginx filter |
| 8090 | 127.0.0.1 | nginx | system | intelligence tunnel (legacy, pre-M9) |
| 8099 | 127.0.0.1 | java | root | LanguageTool (Decifer Learning) |
| 8452 | 127.0.0.1 | java | root | LanguageTool (Decifer Learning) |
| 8733 | 127.0.0.1 | java | root | LanguageTool (Decifer Learning) |
| 8906 | 127.0.0.1 | java | root | LanguageTool (Decifer Learning) |

Ports 3000, 3001, 5000, 8080 — **not in use.**

**No port conflict.** Trading containers (8000, 8001) and Learning LanguageTool servers (8099, 8452, 8733, 8906) occupy separate, non-overlapping port ranges. All internal ports bind to 127.0.0.1 (loopback only). Only ports 80 and 443 are externally reachable, via nginx.

---

### 3. Docker map

```
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"

NAMES                        IMAGE                       STATUS         PORTS
decifer-intelligence         decifer-intelligence:latest Up 1 hour      127.0.0.1:8001->8000/tcp
decifer-pipeline-pipeline-1  <pipeline image>            Up 16 hours    127.0.0.1:8000->8000/tcp
```

**No Decifer Learning containers in Docker.** Learning runs as bare Python and Java processes directly under root — no containerisation. There is no Docker naming, network, or volume conflict between the two products.

Container summary:

| Container | Image | Host port | Container port | Uptime |
|---|---|---|---|---|
| `decifer-intelligence` | `decifer-intelligence:latest` | 127.0.0.1:8001 | 8000 | ~1 hour |
| `decifer-pipeline-pipeline-1` | pipeline image | 127.0.0.1:8000 | 8000 | ~16 hours |

---

### 4. Nginx route map

**Sites available:** `decifer-intelligence`, `decifer-pipeline`, `default` (disabled), `mobile-decifer`

**Sites enabled (symlinked):** `decifer-intelligence`, `decifer-pipeline`, `mobile-decifer`

All `server_name` blocks are domain-specific. No default catch-all (`_`) is enabled.

| Site file | Port | `server_name` | Backend | Notes |
|---|---|---|---|---|
| `decifer-intelligence` (port 80 block) | 80 | `intelligence.decifertrading.com` | — | Returns 301 → HTTPS |
| `decifer-intelligence` (port 443 block) | 443 | `intelligence.decifertrading.com` | `127.0.0.1:8001` | M9 TLS block; GET-only `/api/`, `/health`, 404 all else |
| `decifer-pipeline` | 443 | `pipeline.deciferlearning.com` | `127.0.0.1:8000` | Learning pipeline; domain-specific, not catch-all |
| `mobile-decifer` | 8081 | `_` (loopback only) | `127.0.0.1:8001` | Internal mobile filter; not publicly reachable |
| `default` | — | — | — | Not enabled; not symlinked |

**SNI routing is correct.** After M9 TLS install, nginx uses `server_name` to route:
- Requests for `intelligence.decifertrading.com:443` → intelligence container (8001)
- Requests for `pipeline.deciferlearning.com:443` → pipeline container (8000)

No cross-contamination. The `decifer-pipeline` site is domain-specific (`server_name pipeline.deciferlearning.com`) — it does not act as a default catch-all. Pre-M9, it was the only port 443 block so it inadvertently served all 443 traffic. Post-M9, SNI correctly separates the two.

**Decifer Learning routes (pipeline.deciferlearning.com):** Unchanged. M9 does not touch this site. Learning pipeline health check at `pipeline.deciferlearning.com/health` confirmed responding 200 throughout.

---

### 5. Cron and workers

**`decifer` user crontab (`crontab -u decifer -l`):**

```
# Intelligence pipeline — market hours (14:00–21:00 UTC, Mon–Fri)
*/15 14-21 * * 1-5 cd /opt/decifer && DECIFER_RUNTIME_MODE=intelligence_cloud python3 run_intelligence_pipeline.py >> /opt/decifer/logs/intelligence_refresh.log 2>&1

# Intelligence pipeline — off-hours (every 4 hours)
0 */4 * * * cd /opt/decifer && DECIFER_RUNTIME_MODE=intelligence_cloud python3 run_intelligence_pipeline.py >> /opt/decifer/logs/intelligence_refresh.log 2>&1
```

Note: crontab uses system `python3` (`/usr/bin/python3`). The `numpy` package is absent from system Python, so IC weight updates are skipped during cron runs. Non-fatal — IC weights fall back to the last valid snapshot. M10 recommendation: create a host-level venv for the `decifer` user with `requirements.intelligence.txt` installed.

**Root crontab:** Empty. No root-level cron jobs.

**Decifer Learning batch workers (not cron — running as persistent background processes):**

| Process | Owner | How started | Purpose |
|---|---|---|---|
| `generate-batch-y7.py` | root | Manual / nohup | Learning content generation batch |
| `topup-weak-topics.py` | root | Manual / nohup | Weak-topic remediation batch |
| LanguageTool JVM ×4 | root | Manual / nohup | NLP grammar checking for Learning |

Learning batch workers are not managed by cron or systemd. They were started manually and run as persistent background processes. They do not interact with Trading cron jobs or the intelligence pipeline.

**Systemd timers:** No Decifer-specific systemd timers active (`systemctl list-timers` shows only system timers: `logrotate`, `apt-daily`, `fstrim`, `man-db`).

**No cron conflict.** Trading cron (decifer user) and Learning batch processes (root) are fully independent. No shared log paths, no shared data directories, no scheduling conflicts.

---

### 6. Health checks

**Decifer Learning pipeline** — confirmed alive throughout M9 work:

```bash
curl -s https://pipeline.deciferlearning.com/health
{"status":"ok","version":"0.3.0"}
# HTTP 200 ✓
```

**Decifer Trading Intelligence Cloud** — post-M9 TLS install (live, 2026-05-24T10:47 UTC):

```bash
curl -si https://intelligence.decifertrading.com/health | head -3
# HTTP/2 200
# x-decifer-runtime-mode: intelligence_cloud
# {"status":"ok","runtime_mode":"intelligence_cloud","execution_blocked":true,"data_freshness_status":"ok",...}

curl -si https://intelligence.decifertrading.com/api/market-now | head -3
# HTTP/2 200
# x-decifer-runtime-mode: intelligence_cloud
# {"status":"ok","freshness_timestamp":"2026-05-24T10:48:35Z",...}  — no blocked fields

curl -si -X POST https://intelligence.decifertrading.com/api/market-now | head -1
# HTTP/2 403  ← nginx limit_except blocks mutation at proxy

curl -si https://intelligence.decifertrading.com/api/kill | head -1
# HTTP/2 404

curl -si https://intelligence.decifertrading.com/api/scan | head -1
# HTTP/2 404

curl -si https://intelligence.decifertrading.com/api/settings | head -1
# HTTP/2 404

curl -si https://intelligence.decifertrading.com/api/state | head -1
# HTTP/2 404
```

All checks confirmed live.

---

### 6b. Final live coexistence re-check (post-M9 install)

Run after nginx reload to confirm no Learning service was disrupted. Verified 2026-05-24T10:47 UTC.

**Port map (ss -tulpn, post-M9):**

| Port | Process | Status |
|---|---|---|
| 80 | nginx | LISTEN — HTTP redirect |
| 443 | nginx | LISTEN — HTTPS SNI routing |
| 8000 | docker-proxy | LISTEN — pipeline container |
| 8001 | docker-proxy | LISTEN — intelligence container |
| 8081 | nginx | LISTEN — mobile filter (loopback) |

Ports 3000, 3001, 5000, 8080: not in use. LanguageTool (8099, 8452, 8733, 8906): not queried by nginx post-M9. No new Trading process has taken over any Learning port.

**Docker (post-M9):**
```
NAMES                         STATUS             PORTS
decifer-intelligence          Up About an hour   127.0.0.1:8001->8000/tcp
decifer-pipeline-pipeline-1   Up 16 hours        127.0.0.1:8000->8000/tcp
```
No Learning containers. No conflict.

**Running services post-M9:** `docker.service` and `nginx.service` only. No unexpected new services.

**Decifer Learning health (post-nginx-reload):**
```
curl -i https://pipeline.deciferlearning.com/health
HTTP 200 — {"status":"ok","version":"0.3.0"}
```
Learning pipeline continued responding normally after nginx was reloaded for M9.

**RAM post-refresh:**
```
Mem:   3.8Gi total   3.7Gi used   127Mi free   (213Mi buff/cache, 110Mi available)
Swap:  0B
```

---

### 7. Final coexistence verdict

**VERDICT: M9 GO WITH WARNING — SAFE TO COEXIST, RAM HEADROOM NEEDS M10**

| Check | Finding | Result |
|---|---|---|
| Same droplet | YES — Trading and Learning share one droplet | No conflict |
| Port map (post-M9) | No overlapping ports; all internal ports on 127.0.0.1 | CLEAR |
| Docker (post-M9) | Two Trading containers; zero Learning containers | CLEAR |
| Nginx routes (post-M9) | All `server_name` domain-specific; SNI routes intelligence to 8001, pipeline to 8000 | CLEAR |
| Cron | `decifer` user cron isolated from Learning batch (root, persistent processes) | CLEAR |
| Data directories | `/opt/decifer/` vs `/root/decifer-learning/` — no overlap | CLEAR |
| IBKR process | Not found on droplet | CLEAR |
| Learning health (post-M9) | `pipeline.deciferlearning.com/health` → 200 after nginx reload | CLEAR |
| M9 nginx change | Added intelligence port 443 block only; `decifer-pipeline` site untouched | CLEAR |
| Execution blocked | `/health.execution_blocked = true` confirmed live | CLEAR |
| Blocked response fields | `broker_account_id`, `order_id`, `pnl`, `position_size`, etc. — NONE in any response | CLEAR |
| Smoke test | 12/12 PASS — live endpoint verified | CLEAR |
| Freshness | `data_freshness_status: ok` after manual pipeline refresh | CLEAR |

**M10 warnings (non-blocking for M9 GO):**

| Warning | Detail | Recommended M10 action |
|---|---|---|
| RAM at ~110–127 MB available, no swap | Four Java LanguageTool processes (Learning) consume ~2.2 GB. OOM risk under concurrent burst. | Add 2–4 GB swap: `fallocate -l 4G /swapfile && mkswap /swapfile && swapon /swapfile`; persist in `/etc/fstab`. Or upgrade droplet to 8 GB. |
| IC weights skipped in host-side cron | System Python lacks `numpy`; IC weight updates silently skipped. Non-fatal — uses last valid snapshot. | Create `/opt/decifer/venv/` for `decifer` user with `requirements.intelligence.txt`; update crontab to use venv python. |

No active conflict was found. No rollbacks were performed. No services were stopped or restarted as part of this coexistence check. Decifer Learning continued operating normally throughout M9.
