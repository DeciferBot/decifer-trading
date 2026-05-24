# DECIFER Trading — DigitalOcean Intelligence Cloud Deployment

**Author:** Amit Chopra  
**Updated:** 2026-05-24 (v4.37.0)  
**Status:** GO — Cleared for intelligence deployment. Execution remains on Mac.

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

# Build the image (uses existing Dockerfile — includes TA-Lib for pipeline)
docker compose build

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

## Public routes

All routes are **GET-only**. No mutation routes exist.

| Route | Method | Access | Description |
|---|---|---|---|
| `/health` | GET | Internal (DO health checks) | Liveness check, confirms execution blocked |
| `/api/market-now` | GET | Customer-facing | SaaS-safe Market Now payload |
| `/api/mobile/now` | GET | Authenticated (Cloudflare Access) | Market snapshot, broker state stripped |
| `/api/mobile/why` | GET | Authenticated | Macro drivers and theme transmission |
| `/api/mobile/alpha` | GET | Authenticated | Intelligence candidates, last Apex read |
| `/api/mobile/portfolio` | GET | Authenticated | Intelligence-only placeholder |

**Admin/private access required for `/api/mobile/*`:** Wire these behind Cloudflare Access (email OTP or SSO) using the same pattern as `mobile.decifertrading.com`. See `deployment/MOBILE_DEPLOYMENT.md`.

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

## What is still HOLD

| Feature | Status | Reason |
|---|---|---|
| User broker connection | **HOLD** | Requires auth, compliance review, user-facing broker infrastructure |
| User live execution | **HOLD** | Future phase after intelligence SaaS validated |
| Public SaaS sign-up | **HOLD** | Requires auth layer, billing, and user onboarding |
| Live order webhook | **HOLD** | Not in v1 scope |
