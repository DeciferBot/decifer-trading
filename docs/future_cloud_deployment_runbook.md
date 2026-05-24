# Future Cloud Deployment Runbook

> ⛔  **RETIRED — DO NOT USE**
>
> This document was a pre-M7 draft for a full-stack cloud deployment (IBKR + bot).
> It was never executed and its cloud provider selection was undecided at the time.
>
> **Superseded by (Sprint M7, 2026-05-24):**
> - `docs/DIGITALOCEAN_INTELLIGENCE_CLOUD_DEPLOYMENT.md` — the live, executable operator runbook for the DigitalOcean intelligence cloud
> - `deployment/nginx-intelligence.conf` — Nginx config for the DO droplet
> - `deployment/decifer-intelligence.service` — systemd unit for the intelligence API
> - `scripts/smoke_test_intelligence_cloud.py` — live smoke test (12 checks, all pass)
>
> This file is kept for historical reference only. Do not execute any commands from it.

> ~~⚠️  DRAFT ONLY — DO NOT USE FOR LIVE DEPLOYMENT YET~~
>
> This document describes the *intended* future deployment sequence.
> It has not been executed. No cloud infrastructure has been provisioned.
> Several blockers listed at the end must be resolved before this runbook
> can be used to perform a real deployment.
>
> All commands below are illustrative. Verify against the current state of
> the repository before executing any step.

**Branch:** `cloud/cloud-readiness-preparation`
**Status:** Draft — pre-migration reference only
**Applicable to:** Decifer 4.0+ with Intelligence-First handoff active

---

## 0. Prerequisites Checklist

Before attempting any cloud deployment, confirm ALL of the following:

- [ ] Cloud provider selected (AWS / GCP / Hetzner / DigitalOcean)
- [ ] IBKR Gateway cloud access model decided (same VM vs separate VM; IBC configured)
- [ ] Secrets manager selected and configured (AWS SSM / GCP Secret Manager / Vault)
- [ ] All mandatory env vars stored in secrets manager (never in source)
- [ ] `scripts/cloud_preflight.py` passes locally against target env vars
- [ ] `python3 -m pytest -m smoke -q` passes on current master
- [ ] IBKR paper account ID confirmed
- [ ] Docker installed on target VM (>= 24.0)
- [ ] Docker Compose v2 installed on target VM
- [ ] Git access to `DeciferBot/decifer-trading` configured on target VM
- [ ] Amit has approved the deployment
- [ ] Market hours (09:30–16:00 ET): do NOT start for the first time during market hours

---

## 1. Future Build Command

```bash
# Clone repository on cloud VM
git clone https://github.com/DeciferBot/decifer-trading.git /opt/decifer
cd /opt/decifer

# Build production image
docker build -t decifer-trading:4.0 .

# Verify build — run lightweight health check (no broker, no orders)
docker run --rm decifer-trading:4.0
```

Expected output: `PASS` table with all checks green. If TA-Lib fails,
the source build in the Dockerfile did not complete — check Docker build logs.

---

## 2. Future Environment Variable Setup

**Never store secrets in files committed to git or in docker-compose.yml.**

```bash
# Create .env on the VM (never commit this file)
# Retrieve each value from the secrets manager

cat > /opt/decifer/.env << 'EOF'
ANTHROPIC_API_KEY=<from secrets manager>
ALPACA_API_KEY=<from secrets manager>
ALPACA_SECRET_KEY=<from secrets manager>
ALPACA_BASE_URL=https://paper-api.alpaca.markets
FMP_API_KEY=<from secrets manager>
ALPHA_VANTAGE_KEY=<from secrets manager>
FRED_API_KEY=<from secrets manager>
IBKR_PAPER_ACCOUNT=<from secrets manager>
IBKR_ACTIVE_ACCOUNT=<from secrets manager>
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
EOF

# Lock permissions — only owner can read
chmod 600 /opt/decifer/.env
```

---

## 3. Future Secrets Handling

| Secret | Storage | Rotation |
|--------|---------|---------|
| `ANTHROPIC_API_KEY` | Cloud secrets manager | On Anthropic schedule |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Cloud secrets manager | As needed |
| `FMP_API_KEY` | Cloud secrets manager | Annual |
| `IBKR credentials (IBC)` | Cloud secrets manager | On IBKR password policy |
| `.env` on VM | Generated from secrets manager at deploy time | Re-pull on rotation |

Rotation procedure:
1. Update value in secrets manager.
2. Re-pull `.env` on VM: `secrets-manager-cli pull > /opt/decifer/.env`
3. Restart affected containers: `docker compose restart <service>`
4. Verify health: `docker run --rm --env-file .env decifer-trading:4.0`

---

## 4. Future Data Bootstrap

The `data/` directory is bind-mounted from the VM. On first deploy it must
contain the intelligence files that have been curated locally.

```bash
# Option A: rsync from developer machine (first deploy only)
rsync -avz --exclude '.env' \
  /path/to/local/decifer-trading/data/ \
  user@cloud-vm:/opt/decifer/data/

# Option B: restore from a snapshot or backup
# (backup procedure TBD — see Known Blockers)

# After rsync, fix ownership for non-root container user (UID 1000)
chown -R 1000:1000 /opt/decifer/data /opt/decifer/logs
```

---

## 5. Future Health Check Command

```bash
# Lightweight (import + env var presence — no broker):
docker run --rm --env-file /opt/decifer/.env decifer-trading:4.0

# Full preflight (requires data mount):
docker run --rm \
  --env-file /opt/decifer/.env \
  -v /opt/decifer/data:/app/data \
  decifer-trading:4.0 \
  python3 scripts/cloud_preflight.py

# Smoke tests (no broker):
docker run --rm \
  --env-file /opt/decifer/.env \
  -v /opt/decifer/data:/app/data \
  decifer-trading:4.0 \
  python3 -m pytest -m smoke -q
```

All three must pass before starting the live bot.

---

## 6. Future Startup Sequence

```bash
cd /opt/decifer

# 1. Ensure IBKR Gateway is running and accepting connections
#    (manual step or IBC automation — see ibkr_gateway_future_cloud_design.md)

# 2. Start handoff publisher (no IBKR dependency)
docker compose up -d handoff-publisher

# 3. Wait for first publisher cycle to complete (up to 15 min)
docker compose logs -f handoff-publisher &
# Watch for: "Publish cycle complete" or check data/heartbeats/handoff_publisher.json

# 4. Run full preflight check
docker run --rm \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  decifer-trading:4.0 \
  python3 scripts/cloud_preflight.py

# 5. Start remaining services
docker compose up -d handoff-observer
docker compose up -d live-bot

# 6. Verify all containers are running
docker compose ps

# 7. Tail bot logs for first 5 minutes
docker compose logs -f live-bot
```

---

## 7. Future Shutdown Sequence

```bash
cd /opt/decifer

# 1. Stop live bot first (allows in-flight scan cycle to complete)
docker compose stop live-bot
# Wait up to 60s for graceful shutdown. Check logs:
docker compose logs live-bot | tail -20

# 2. Stop remaining services
docker compose stop handoff-observer
docker compose stop handoff-publisher

# 3. Verify all stopped
docker compose ps

# Do NOT delete data/trades.json or data/trade_events.jsonl during shutdown.
```

---

## 8. Future Log Inspection

```bash
# Live bot logs
docker compose logs -f live-bot
# or on host
tail -f /opt/decifer/logs/decifer.log

# Publisher logs
docker compose logs -f handoff-publisher
tail -f /opt/decifer/logs/handoff_publisher.log

# Check for publisher fail files
ls /opt/decifer/data/live/.fail_*.json 2>/dev/null

# Publisher heartbeat
cat /opt/decifer/data/heartbeats/handoff_publisher.json | python3 -m json.tool

# Current manifest
cat /opt/decifer/data/live/current_manifest.json | python3 -m json.tool
```

---

## 9. Future Rollback Process

**Scenario A: Handoff caused unexpected bot behaviour**

```bash
# Disable handoff — bot reverts to scanner path immediately
# Edit config.py: enable_active_opportunity_universe_handoff = False
# Then restart bot:
docker compose restart live-bot

# Verify in logs:
docker compose logs live-bot | grep "handoff"
# Expected: "Building dynamic universe (Alpaca screening)..."
```

**Scenario B: Code regression — rollback to previous image**

```bash
# Build previous version
git checkout <previous-tag>
docker build -t decifer-trading:previous .

# Update docker-compose.yml image tag to :previous
# Restart services
docker compose up -d
```

**Scenario C: Full shutdown — broker emergency stop**

```bash
# Immediate stop of all bot activity
docker compose stop live-bot

# This stops the bot; IBKR Gateway remains running.
# Existing bracket orders on IBKR server side remain active.
# To cancel all open orders: use IBKR TWS or Gateway directly.
# Do NOT run bot_ibkr cancel-all from command line without Amit approval.
```

---

## 10. Blockers Before This Runbook Can Be Used Live

| # | Blocker | Required for |
|---|---------|-------------|
| 1 | **IBKR Gateway cloud access not designed** | Every step involving live-bot |
| 2 | **No cloud provider selected** | VM provisioning |
| 3 | **No secrets manager selected or integrated** | Step 2 (env setup) |
| 4 | **No data backup / restore procedure** | Step 4 (data bootstrap) |
| 5 | **IBC / headless Gateway not tested** | Unattended Gateway restarts |
| 6 | **docker-compose.yml for cloud not written** | Steps 6, 7 |
| 7 | **Market-hours downstream scoring proof pending** | Go-live confidence |
| 8 | **Full smoke test suite must pass on clean VM** | Step 5 (preflight) |
| 9 | **Non-root user UID 1000 ownership on host dirs** | Non-root container running |
| 10 | **Amit approval required** | Any actual cloud deployment |

---

*This runbook will be updated as blockers are resolved. Do not execute any section
without first confirming the corresponding blocker is resolved and Amit has approved.*
