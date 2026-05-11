# Cloud Phase 1 VM Deployment Runbook

**Status:** EXECUTABLE — pending Amit approval of each section gate
**Date:** 2026-05-11
**Architecture decision reference:** `docs/cloud_phase1_decision_record.md`
**Supersedes:** `docs/future_cloud_deployment_runbook.md` (now retired as DRAFT)

---

## 1. Purpose

This runbook is the step-by-step execution guide for deploying the Decifer Trading
system on a DigitalOcean Ubuntu 22.04 Droplet.

It covers: VM provisioning, SSH hardening, firewall, Docker, repo checkout, secrets,
IBC and IB Gateway headless setup, Xvfb, systemd services, data bootstrap, preflight
healthcheck, starting non-trading services, verifying IBKR connectivity, the explicit
Amit approval gate, and starting the live bot.

Every numbered step must be completed in order. Gates are marked clearly.

---

## 2. What This Runbook Does Not Do

- Does not change trading logic, scoring, Apex calls, or manifest semantics
- Does not provision the Droplet automatically (Amit does that via the DigitalOcean dashboard)
- Does not start the live bot without Amit's explicit approval (§20)
- Does not activate `HANDOFF_PUBLISHER_MODE=controlled_activation` (default: `validation_only`)
- Does not expose IBKR Gateway to any public network interface
- Does not commit secrets to git

---

## 3. Phase 1 Architecture

```
DigitalOcean Droplet (Ubuntu 22.04, 4 vCPU / 8 GB, NYC1)
┌─────────────────────────────────────────────────────────┐
│ /opt/decifer/                                           │
│   current/          ← git checkout of repo             │
│   shared/data/      ← bind-mounted into containers     │
│   shared/logs/      ← bind-mounted into containers     │
│   ibc/              ← IBC installation + config.ini    │
│                                                         │
│ Systemd services (host):                                │
│   decifer-xvfb.service      (starts first)             │
│   decifer-ibgateway.service (depends on xvfb)          │
│   decifer-docker-stack.service (publisher + observer)  │
│                                                         │
│ Docker Compose:                                         │
│   handoff-publisher  (no IBKR) ─┐                      │
│   handoff-observer   (no IBKR) ─┤ started by systemd   │
│   live-bot [profile: live]  ────┘ NOT started until §20│
│                                                         │
│ IB Gateway (host process):                              │
│   Binds to 127.0.0.1:4002 ONLY                         │
│   Bot reaches via network_mode: host                    │
│   Never exposed to public internet                      │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Prerequisites

Before starting this runbook, confirm:

- [ ] `docs/cloud_phase1_decision_record.md` has been read and Amit has approved the decisions
- [ ] `docs/cloud_phase1_vm_go_no_go_checklist.md` is open alongside this runbook
- [ ] You have a DigitalOcean account and billing is configured
- [ ] You have your IBKR paper account credentials (username + password) available
- [ ] You have all mandatory env var values available from a password manager
- [ ] Time is NOT between 09:30–16:00 ET (do not start live-bot during market hours)
- [ ] You have at least 90 minutes for this first deployment

---

## 5. DigitalOcean Droplet Specification

Create via the DigitalOcean dashboard (https://cloud.digitalocean.com/droplets/new):

| Parameter | Value |
|-----------|-------|
| Image | Ubuntu 22.04 (LTS) x64 |
| Plan | General Purpose — 4 vCPU / 8 GB RAM / 80 GB SSD |
| Datacenter | NYC1 (or AMS3 for Europe) |
| Authentication | SSH key (add your public key — password auth will be disabled) |
| Hostname | `decifer-trading-paper` |
| Backups | Enable weekly backup (+$5/mo) |
| Monitoring | Enable DigitalOcean agent (free) |
| Reserved IP | Assign a Reserved IP after creation (static IP required) |

**Estimated cost:** ~$35/mo (Droplet $30 + backup $5)

---

## 6. DNS and Static IP

After Droplet creation:

1. Go to Networking → Reserved IPs → Create Reserved IP → assign to this Droplet
2. Note the Reserved IP address — this is your VM's permanent public IP
3. No DNS is required for Phase 1 (SSH and dashboard access via IP directly)
4. If you add a domain later, point the A record at this Reserved IP

---

## 7. SSH Hardening

```bash
# SSH into the VM as root with your SSH key:
ssh root@<reserved-ip>

# Edit SSH config:
vim /etc/ssh/sshd_config

# Required settings:
# PasswordAuthentication no
# PermitRootLogin prohibit-password
# PubkeyAuthentication yes

# Apply:
systemctl restart sshd

# Verify you can still SSH in a new terminal before closing this session.
```

---

## 8. Firewall Setup

```bash
# On the VM as root:
cd /opt/decifer/current

# Default: SSH only, all IBKR ports blocked on public interfaces
bash scripts/cloud/setup_firewall.sh

# To access dashboard via SSH tunnel (recommended — no public 8080):
# From your laptop: ssh -L 8080:localhost:8080 root@<reserved-ip>
# Then: http://localhost:8080

# Verify:
ufw status verbose
# Expected: 22/tcp ALLOW IN, 4002/tcp DENY IN, etc.
```

**Gate:** Do not proceed until `ufw status` shows port 4002 as DENY.

---

## 9. VM Base Setup

```bash
# On the VM as root:
bash scripts/cloud/setup_digitalocean_vm_base.sh
```

This installs: Docker Engine, Docker Compose plugin, Java 17, Xvfb, git, curl,
creates the `decifer` user (UID 1000), and creates `/opt/decifer/` structure.

**Expected output:** "VM base setup complete." with next manual steps listed.

---

## 10. Repo Checkout

```bash
# On the VM as root:
cd /opt/decifer
git clone https://github.com/DeciferBot/decifer-trading.git current
chown -R 1000:1000 current
```

**Note:** If the repo is private, configure SSH deploy key first:
```bash
ssh-keygen -t ed25519 -C "decifer-vm-deploy" -f /root/.ssh/deploy_key -N ""
cat /root/.ssh/deploy_key.pub
# Add this public key as a GitHub Deploy Key (read-only) in the repo settings
echo 'Host github.com' >> /root/.ssh/config
echo '  IdentityFile /root/.ssh/deploy_key' >> /root/.ssh/config
```

---

## 11. .env Creation and chmod 600

```bash
# On the VM as root:
cp /opt/decifer/current/.env.example /opt/decifer/current/.env
vim /opt/decifer/current/.env
```

Fill in every value. Required values:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key from console.anthropic.com |
| `ALPACA_API_KEY` | Alpaca market data key |
| `ALPACA_SECRET_KEY` | Alpaca secret |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` |
| `IBKR_PAPER_ACCOUNT` | Your paper account ID (e.g. DUP481326) |
| `IBKR_ACTIVE_ACCOUNT` | Same as paper account for Phase 1 |
| `FMP_API_KEY` | Financial Modeling Prep key |
| `ALPHA_VANTAGE_KEY` | Alpha Vantage key |
| `FRED_API_KEY` | FRED API key |
| `IBKR_HOST` | `127.0.0.1` |
| `IBKR_PORT` | `4002` |
| `HANDOFF_PUBLISHER_MODE` | `validation_only` |

```bash
# Lock permissions immediately after editing:
chmod 600 /opt/decifer/current/.env
chown root:root /opt/decifer/current/.env

# Verify:
ls -la /opt/decifer/current/.env
# Expected: -rw------- 1 root root ... .env
```

**Gate:** Do not proceed if permissions are not 600. Do not proceed if any mandatory var is empty.

---

## 12. IBC and IB Gateway Setup

Follow `ops/ibc/README.md` in full. Summary:

```bash
# On the VM as root:
cd /opt/decifer/ibc

# Step 1: Download IBC (replace with latest version from GitHub releases)
IBC_VERSION="3.19.0"
wget -q "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBC-${IBC_VERSION}.zip" \
  -O ibc.zip && unzip -q ibc.zip && rm ibc.zip
chmod +x scripts/*.sh

# Step 2: Download and install IB Gateway
wget -q "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh" \
  -O ibgateway_installer.sh
chmod +x ibgateway_installer.sh
./ibgateway_installer.sh -q -dir /opt/ibgateway -DSilentInstall=true
chown -R 1000:1000 /opt/ibgateway

# Step 3: Create config.ini with real credentials
cp /opt/decifer/current/ops/ibc/config.ini.template /opt/decifer/ibc/config.ini
vim /opt/decifer/ibc/config.ini
# Fill in: IbLoginId, IbPassword, IbDir=/opt/ibgateway, IbPort=4002, TradingMode=paper
chmod 600 /opt/decifer/ibc/config.ini
chown root:root /opt/decifer/ibc/config.ini
```

---

## 13. Xvfb Setup

Xvfb is installed by `setup_digitalocean_vm_base.sh`. The systemd service manages it.
No manual configuration is needed beyond verifying the install:

```bash
which Xvfb   # should return /usr/bin/Xvfb
dpkg -l xvfb | grep '^ii'
```

---

## 14. Systemd Service Installation

```bash
# On the VM as root:
cd /opt/decifer/current

# Install files only (safe — no start):
bash scripts/cloud/install_systemd_services.sh --install

# Verify files are in place:
ls -la /etc/systemd/system/decifer-*.service

# Enable for boot (no start yet):
bash scripts/cloud/install_systemd_services.sh --install --enable

# Verify enabled:
systemctl is-enabled decifer-xvfb decifer-ibgateway decifer-docker-stack
```

---

## 15. Runtime Data Bootstrap

```bash
# On the VM as root:
cd /opt/decifer/current

# Step 1: rsync data/ from developer machine (first deploy only)
# Run this from your LOCAL machine, not the VM:
rsync -avz --exclude '.env' \
  /path/to/local/decifer-trading/data/ \
  root@<reserved-ip>:/opt/decifer/shared/data/

# Step 2: Fix ownership on the VM:
chown -R 1000:1000 /opt/decifer/shared/data /opt/decifer/shared/logs

# Step 3: Run bootstrap script:
bash scripts/cloud/first_deploy_bootstrap.sh

# Expected: "Bootstrap complete — 0 failures."
```

---

## 16. Preflight Healthcheck

```bash
# On the VM as root:
cd /opt/decifer/current

# Run inside Docker container (matches production environment):
docker run --rm \
  --env-file .env \
  -v /opt/decifer/shared/data:/app/data \
  -v /opt/decifer/shared/logs:/app/logs \
  decifer-trading:latest \
  python3 scripts/healthcheck.py --strict

# Expected: all checks green, exit 0.
# If env vars fail: re-check .env and source it for validation.
```

**Gate:** Do not proceed until healthcheck exits 0 in strict mode inside the container.

---

## 17. Starting Non-Trading Services Only

At this point: start handoff-publisher and handoff-observer.
These have NO IBKR dependency and are safe to start independently.

```bash
# On the VM as root:

# Test IBC manually first (see ops/ibc/README.md §Test step):
Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1
/opt/decifer/ibc/scripts/ibcstart.sh /opt/decifer/ibc/config.ini \
  --IbDir=/opt/ibgateway --LogToConsole &
sleep 60
nc -z 127.0.0.1 4002 && echo "Gateway OK" || echo "Gateway NOT ready"
pkill -f ibgateway; pkill -f Xvfb

# If Gateway test passed, start services:
bash scripts/cloud/install_systemd_services.sh --install --enable --start --confirm-start

# Verify non-trading services are up:
systemctl status decifer-xvfb decifer-ibgateway decifer-docker-stack
docker compose ps   # should show handoff-publisher and handoff-observer running
# live-bot should NOT appear — it requires --profile live

# Wait for first publisher cycle (~15 minutes):
docker compose logs -f handoff-publisher
# Watch for: "Publish cycle complete" or check heartbeat:
cat /opt/decifer/shared/data/heartbeats/handoff_publisher.json | python3 -m json.tool
```

---

## 18. Verifying IB Gateway Local Connectivity

```bash
# On the VM as root:

# Confirm Gateway is listening on loopback only:
ss -tlnp | grep 4002
# Expected: 127.0.0.1:4002 (loopback only — NOT 0.0.0.0:4002)

# Confirm port is NOT reachable from outside the VM:
# (Run this from your local machine — should time out or refuse)
nc -z -w 3 <reserved-ip> 4002 && echo "DANGER: port is public!" || echo "Correct: port is closed externally"

# Confirm bot container can reach it via network_mode: host:
docker run --rm --network host decifer-trading:latest \
  python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',4002)); print('Gateway reachable from container'); s.close()"
```

**Gate:** ss output must show 127.0.0.1:4002, NOT 0.0.0.0:4002.

---

## 19. Final Amit Approval Gate

> ⛔ DO NOT START LIVE-BOT WITHOUT COMPLETING THIS SECTION.
> This is a mandatory gate. Every item must be checked by Amit personally.

Run the go/no-go checklist: `docs/cloud_phase1_vm_go_no_go_checklist.md`

Every item must be confirmed TRUE before proceeding to §20.

Additionally, Amit must explicitly state (in writing, in this session or another):

> "I approve starting the Decifer live bot on the cloud VM in paper trading mode.
> The time is [TIME] ET and market hours are confirmed closed."

This approval must be given AFTER the go/no-go checklist is fully complete,
NOT before it.

---

## 20. Starting Live-Bot Only After Approval

After the approval gate in §19 is cleared:

```bash
# On the VM as root:
cd /opt/decifer/current

# Confirm market is closed before this step:
date -u && python3 -c "
from datetime import datetime, timezone
import zoneinfo
et = datetime.now(zoneinfo.ZoneInfo('America/New_York'))
h, m = et.hour, et.minute
market_open = (h == 9 and m >= 30) or (10 <= h <= 15) or (h == 16 and m == 0)
print(f'ET time: {et.strftime(\"%H:%M\")}')
print(f'Market open: {market_open}')
print('SAFE TO START' if not market_open else 'MARKET IS OPEN — DO NOT START NOW')
"

# Change live-bot command from healthcheck.py to bot.py:
# Edit docker-compose.yml line:
#   command: python3 scripts/healthcheck.py   # safe default — NOT bot.py
# Change to:
#   command: python3 bot.py
vim /opt/decifer/current/docker-compose.yml

# Start live-bot with explicit profile flag:
docker compose --profile live up -d live-bot

# Verify it started:
docker compose ps live-bot
docker compose logs -f live-bot

# Watch for "IB connected" in logs within 30 seconds.
# Watch for first scan cycle completion.
```

**If bot fails to start or connect to IBKR within 2 minutes:** stop it immediately.
```bash
docker compose --profile live stop live-bot
journalctl -u decifer-ibgateway -n 100   # check Gateway logs
docker compose logs live-bot             # check bot logs
```

---

## 21. Shutdown Procedure

**Graceful shutdown (bot first, then publisher):**
```bash
# Stop live-bot first (allows in-flight scan cycle to complete):
docker compose --profile live stop live-bot
# Wait up to 60 seconds. Check:
docker compose logs live-bot | tail -20

# Stop non-trading services:
docker compose stop handoff-observer
docker compose stop handoff-publisher

# Stop systemd services:
systemctl stop decifer-docker-stack decifer-ibgateway decifer-xvfb

# Verify all stopped:
docker compose ps
systemctl status decifer-docker-stack decifer-ibgateway decifer-xvfb
```

**Emergency stop (bot only):**
```bash
docker compose --profile live stop live-bot
# Existing IBKR bracket orders remain active on IBKR server side.
# To cancel all open orders: use IBKR TWS or IB Gateway directly.
# Do NOT run any cancel-all script without Amit approval.
```

---

## 22. Rollback Procedure

**Scenario A: Publisher issue**
```bash
# Check publisher logs:
docker compose logs handoff-publisher | tail -50
cat /opt/decifer/shared/data/live/.fail_*.json 2>/dev/null

# Restart publisher:
docker compose restart handoff-publisher
```

**Scenario B: Code regression — roll back to previous commit**
```bash
cd /opt/decifer/current
git log --oneline -10   # find last known good commit
git stash              # save any local changes
git checkout <commit-hash>
docker compose build
docker compose up -d handoff-publisher handoff-observer
# Only re-add live-bot after Amit approval
```

**Scenario C: Full VM recovery**
```bash
# Provision new Droplet using same Droplet spec (§5)
# Restore from weekly backup (DigitalOcean → Backups → Restore)
# Or: rsync data/ from backup and re-run this runbook from §9
```

---

## 23. Known Risks

| Risk | Mitigation |
|------|-----------|
| IBKR Gateway 2FA required on first login | Complete manually via VNC once; IBC maintains session thereafter |
| IBC loses Gateway session after daily reset | IBC handles auto-restart; monitor `journalctl -u decifer-ibgateway` |
| Bot starts during market hours accidentally | §20 market-hours check; Amit approval gate §19 |
| VM reboot mid-trading-day | Open bracket orders remain on IBKR server; bot reconciles on restart |
| `.env` file permission misconfiguration | first_deploy_bootstrap.sh validates 600 before proceeding |
| `docker compose up` starting live-bot | Compose `profiles: [live]` gate prevents this |
| IBKR Gateway port exposed publicly | `setup_firewall.sh` explicitly DENY 4002; `ss` check in §18 |
| Log files growing unbounded | `logs/decifer.log` has RotatingFileHandler (5 MB × 5); JSONL rotation active |
| VM disk full | Monitor via DigitalOcean agent; 80 GB SSD gives >6 months of typical operation |

---

## 24. Phase 2 Upgrades

These items are out of scope for Phase 1 but should be planned before live trading:

| Upgrade | Trigger |
|---------|---------|
| Separate Gateway VM (Option B) | Before live trading |
| AWS SSM / 1Password secrets manager | Before live trading or team growth |
| Structured cloud monitoring (Datadog, CloudWatch, Grafana) | After first stable week |
| Automated data backups (S3 / DO Spaces) | After first stable week |
| `docker-compose.yml` healthcheck-based startup ordering | If publisher scheduling becomes unreliable |
| `ibkr_client_id` configurable in `.env` | Before running multiple bot instances |
| IBC Docker container (Option C) | Evaluate 6 months after IBC headless is stable |
| CI/CD deployment pipeline | When codebase has more contributors |
