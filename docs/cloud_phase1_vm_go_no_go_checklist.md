# Cloud Phase 1 VM — Go / No-Go Checklist

**Purpose:** Pre-live-bot gate. Every item must be TRUE before Amit approves starting live-bot.
**Reference:** `docs/cloud_phase1_vm_deployment_runbook.md`
**Decision record:** `docs/cloud_phase1_decision_record.md`

> Complete this checklist in order. Do not skip items. Do not start live-bot until
> every box is checked and Amit has given explicit written approval in §6.

---

## Section 1 — VM Infrastructure

- [ ] **1.1 Droplet created** — DigitalOcean Droplet provisioned (Ubuntu 22.04, 4 vCPU / 8 GB, NYC1)
- [ ] **1.2 Reserved IP assigned** — static IP attached to the Droplet (IP noted: _____________)
- [ ] **1.3 Weekly backup enabled** — DigitalOcean automatic backup is active
- [ ] **1.4 DigitalOcean monitoring agent enabled** — basic CPU/memory/disk alerting active
- [ ] **1.5 SSH key authentication only** — password auth disabled (`PasswordAuthentication no`)
- [ ] **1.6 Root SSH login restricted** — `PermitRootLogin prohibit-password` in sshd_config

---

## Section 2 — Firewall

- [ ] **2.1 ufw installed and enabled** — `ufw status` shows Status: active
- [ ] **2.2 SSH allowed** — port 22 is ALLOW in ufw status
- [ ] **2.3 IBKR Gateway ports blocked** — ports 4002, 4001, 7496, 7497 show DENY in ufw status
- [ ] **2.4 Dashboard not public** — port 8080 is NOT open in ufw status (use SSH tunnel for access)
- [ ] **2.5 External port 4002 verified closed** — `nc -z -w 3 <vm-ip> 4002` from local machine fails (connection refused or timeout)

---

## Section 3 — Software Installation

- [ ] **3.1 Docker installed** — `docker --version` returns 24.x or higher
- [ ] **3.2 Docker Compose plugin installed** — `docker compose version` returns v2.x
- [ ] **3.3 Docker daemon enabled** — `systemctl is-enabled docker` returns enabled
- [ ] **3.4 Java 17 installed** — `java -version` returns 17.x
- [ ] **3.5 Xvfb installed** — `which Xvfb` returns /usr/bin/Xvfb
- [ ] **3.6 decifer user exists** — `id decifer` shows uid=1000

---

## Section 4 — Repository and Secrets

- [ ] **4.1 Repo checked out** — `/opt/decifer/current/` contains repo files (bot.py, config.py visible)
- [ ] **4.2 `.env` created** — `/opt/decifer/current/.env` exists
- [ ] **4.3 `.env` permissions are 600** — `stat -c "%a" /opt/decifer/current/.env` returns `600`
- [ ] **4.4 `.env` owner is root** — `stat -c "%U" /opt/decifer/current/.env` returns `root`
- [ ] **4.5 ANTHROPIC_API_KEY set** — present in `.env` (value not printed — existence only)
- [ ] **4.6 ALPACA_API_KEY set** — present in `.env`
- [ ] **4.7 ALPACA_SECRET_KEY set** — present in `.env`
- [ ] **4.8 IBKR_PAPER_ACCOUNT set** — present in `.env` (paper account, e.g. DUP...)
- [ ] **4.9 IBKR_ACTIVE_ACCOUNT set** — present in `.env` (matches paper account for Phase 1)
- [ ] **4.10 IBKR_HOST=127.0.0.1** — set in `.env`
- [ ] **4.11 IBKR_PORT=4002** — set in `.env`
- [ ] **4.12 HANDOFF_PUBLISHER_MODE=validation_only** — set in `.env` (NOT controlled_activation)
- [ ] **4.13 `.env` not in git** — `git ls-files .env` from repo root returns empty

---

## Section 5 — Runtime Data and Health

- [ ] **5.1 Runtime dirs bootstrapped** — `bash scripts/cloud/first_deploy_bootstrap.sh` exits 0
- [ ] **5.2 Intelligence files present** — `/opt/decifer/shared/data/intelligence/` is non-empty
- [ ] **5.3 Universe builder files present** — `active_opportunity_universe_shadow.json` exists
- [ ] **5.4 Committed universe present** — `data/committed_universe.json` exists
- [ ] **5.5 Data ownership correct** — `stat -c "%u" /opt/decifer/shared/data` returns `1000`
- [ ] **5.6 TA-Lib passes in container** — `docker run --rm decifer-trading:latest python3 -c "import talib; print('ok')"` prints ok
- [ ] **5.7 NLTK Vader passes in container** — `docker run --rm decifer-trading:latest python3 -c "from nltk.sentiment import SentimentIntensityAnalyzer; SentimentIntensityAnalyzer(); print('ok')"` prints ok
- [ ] **5.8 Healthcheck passes (strict, with env)** — `docker run --rm --env-file .env -v /opt/decifer/shared/data:/app/data decifer-trading:latest python3 scripts/healthcheck.py --strict` exits 0
- [ ] **5.9 docker compose config valid** — `docker compose config` exits 0 (pipe output to avoid printing secrets)
- [ ] **5.10 cloud_preflight.py passes** — `docker run --rm --env-file .env -v /opt/decifer/shared/data:/app/data decifer-trading:latest python3 scripts/cloud_preflight.py` exits 0

---

## Section 6 — IBC and IB Gateway

- [ ] **6.1 IBC installed** — `/opt/decifer/ibc/scripts/ibcstart.sh` exists and is executable
- [ ] **6.2 IB Gateway installed** — `/opt/ibgateway/` exists with Gateway files
- [ ] **6.3 IBC config.ini present** — `/opt/decifer/ibc/config.ini` exists
- [ ] **6.4 config.ini permissions are 600** — `stat -c "%a" /opt/decifer/ibc/config.ini` returns `600`
- [ ] **6.5 TradingMode=paper in config.ini** — `grep TradingMode /opt/decifer/ibc/config.ini` shows `paper`
- [ ] **6.6 IbPort=4002 in config.ini** — `grep IbPort /opt/decifer/ibc/config.ini` shows `4002`
- [ ] **6.7 Manual IBC test passed** — Gateway started manually, accepted connection at 127.0.0.1:4002
- [ ] **6.8 Gateway binds loopback only** — `ss -tlnp | grep 4002` shows `127.0.0.1:4002` (NOT `0.0.0.0:4002`)

---

## Section 7 — Systemd Services

- [ ] **7.1 Service files installed** — `/etc/systemd/system/decifer-xvfb.service` exists
- [ ] **7.2 Service files installed** — `/etc/systemd/system/decifer-ibgateway.service` exists
- [ ] **7.3 Service files installed** — `/etc/systemd/system/decifer-docker-stack.service` exists
- [ ] **7.4 Services enabled** — `systemctl is-enabled decifer-xvfb decifer-ibgateway decifer-docker-stack` all return `enabled`
- [ ] **7.5 decifer-xvfb running** — `systemctl is-active decifer-xvfb` returns `active`
- [ ] **7.6 decifer-ibgateway running** — `systemctl is-active decifer-ibgateway` returns `active`
- [ ] **7.7 decifer-docker-stack running** — `systemctl is-active decifer-docker-stack` returns `active`

---

## Section 8 — Non-Trading Services Running

- [ ] **8.1 handoff-publisher is running** — `docker compose ps handoff-publisher` shows `running`
- [ ] **8.2 handoff-observer is running** — `docker compose ps handoff-observer` shows `running`
- [ ] **8.3 live-bot is NOT running** — `docker compose ps live-bot` shows nothing (profile not activated)
- [ ] **8.4 First publisher cycle completed** — `data/heartbeats/handoff_publisher.json` exists with recent `last_success_ts`
- [ ] **8.5 No publisher fail files** — `ls /opt/decifer/shared/data/live/.fail_*.json 2>/dev/null` returns empty
- [ ] **8.6 Manifest is fresh** — `data/live/current_manifest.json` exists and `expires_at` is in the future

---

## Section 9 — IBKR Gateway Connectivity (bot reachability)

- [ ] **9.1 Gateway port confirmed loopback only** — `ss -tlnp | grep 4002` shows `127.0.0.1:4002`
- [ ] **9.2 Bot container can reach Gateway** — `docker run --rm --network host decifer-trading:latest python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',4002)); print('OK'); s.close()"` prints OK
- [ ] **9.3 Gateway externally unreachable** — `nc -z -w 3 <vm-ip> 4002` from local machine fails

---

## Section 10 — Final Safety Checks Before Live-Bot

- [ ] **10.1 Paper trading account confirmed** — `IBKR_ACTIVE_ACCOUNT` in `.env` begins with `DU` (paper prefix)
- [ ] **10.2 Market hours check** — current ET time is NOT between 09:30 and 16:00
  - ET time at start: _____________ (fill in)
- [ ] **10.3 live-bot command is bot.py** — `docker-compose.yml` live-bot command has been changed to `python3 bot.py`
- [ ] **10.4 Rollback path understood** — you have read §22 of the runbook and know how to stop the bot immediately
- [ ] **10.5 Amit is present** — Amit is available to monitor the first scan cycle

---

## Section 6 — Amit Explicit Approval

> This section must be completed by Amit personally.
> No other team member may give this approval.

**Go / No-Go Decision:**

All items in Sections 1–10 above are confirmed: ☐ YES ☐ NO

If NO: do not proceed. Identify and fix the failing items.

**Amit's written approval:**

> "I, Amit Chopra, approve starting the Decifer live bot on the cloud VM
> in paper trading mode (account: _____________).
> The time is _____________ ET and market hours are confirmed closed.
> I have reviewed and confirmed every item in this go/no-go checklist."

**Date/time of approval:** _____________

**Signature (or typed name):** _____________

---

*After this approval is captured, proceed to `docs/cloud_phase1_vm_deployment_runbook.md` §20.*
