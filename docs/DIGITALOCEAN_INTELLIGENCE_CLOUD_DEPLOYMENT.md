# DECIFER Trading — DigitalOcean Intelligence Cloud Deployment

**Author:** Amit Chopra  
**Updated:** 2026-05-24 (v4.42.1 — Sprint M7 Live Deployment Proof)  
**Status:** GO — Intelligence cloud deployment verified end-to-end with live smoke test. Operator runbook is exact and executable.

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
