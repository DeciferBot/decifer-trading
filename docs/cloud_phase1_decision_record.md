# Cloud Phase 1 Deployment Decision Record

**Branch:** `cloud/cloud-deployment-decision-record`
**Date:** 2026-05-11
**Prepared by:** Cowork (Claude)
**Status:** DECISION RECORD — awaiting Amit approval before any deployment action
**Supersedes:** `docs/future_cloud_deployment_runbook.md` (draft runbook, still valid as execution reference)

---

> This document records the recommended Phase 1 cloud deployment path for Decifer Trading.
> It contains no credentials, no infrastructure provisioning, and no trading logic changes.
> Every decision item requires Amit's explicit approval before any deployment begins.

---

## 1. Executive Summary

The Decifer cloud shipping package is complete and Docker-validated as of 2026-05-11.
The image builds, TA-Lib compiles inside the container, NLTK Vader runs offline,
the healthcheck passes, and the compose file validates. Three deployment decisions
remain open. This document closes them with a specific recommendation for each.

**Recommended path in one sentence:**
> Run Decifer on a DigitalOcean Droplet (4 vCPU / 8 GB), with IBKR Gateway on the
> same VM managed by IBC in headless mode, secrets stored in a `.env` file with
> 600 permissions, with a clear upgrade path to a separate Gateway VM and a secrets
> manager once paper trading proves stable.

**What this enables:** deploying handoff-publisher and handoff-observer immediately
(no IBKR required), then adding the live bot once IBKR Gateway is confirmed running
on the same VM.

---

## 2. Current Validated State

Everything in this section is confirmed working as of the Docker build on 2026-05-11.
No action required — recorded here as the deployment baseline.

| Component | State | Evidence |
|-----------|-------|---------|
| Docker image | ✅ Builds end-to-end | `docker build --no-cache` succeeded |
| TA-Lib C library | ✅ Compiles from source in container | `import talib` passes in container |
| NLTK Vader lexicon | ✅ Pre-downloaded at build time | `SentimentIntensityAnalyzer()` scores offline |
| Python imports | ✅ All 7 required imports pass | `healthcheck.py` — anthropic, pandas, numpy, ib_async, talib, alpaca-py |
| Decifer modules | ✅ config, handoff_reader, utils.log_rotation all import cleanly | `healthcheck.py` module checks |
| Runtime directories | ✅ All 7 created inside container image | `bootstrap_runtime_dirs.py --quiet` exits 0 |
| Healthcheck | ✅ All checks pass except env vars (expected without --env-file) | `healthcheck.py` output |
| Compose file | ✅ All 3 services parse correctly | `docker compose config` exits 0 |
| Env var contract | ✅ Documented in `.env.example` and `cloud_readiness_contract.md` | 6 mandatory, 3 recommended |
| .dockerignore | ✅ Excludes secrets, data, archive, tests, macOS artefacts | Reviewed |
| Non-root user | ✅ Container runs as `decifer` UID 1000 | Dockerfile USER directive |
| Secrets in image | ✅ None — belt-and-suspenders `rm -f .env` in build | Dockerfile |

**What is deliberately not deployed:**

- No cloud VM exists
- No IBKR Gateway is running in the cloud
- `live-bot` command in docker-compose.yml is `healthcheck.py`, not `bot.py`
- `HANDOFF_PUBLISHER_MODE` defaults to `validation_only` — handoff not activated
- No secrets have been committed to the repo at any point

---

## 3. Remaining Blockers (Decision Items)

Three decisions gate the deployment. All three are resolved in this document.

| # | Blocker | Severity | Decision |
|---|---------|----------|---------|
| 1 | Cloud provider not selected | High | → DigitalOcean Droplet (§5) |
| 2 | IBKR Gateway access model undefined | Critical | → Same VM, IBC headless (§4) |
| 3 | Secrets manager not selected | Medium | → `.env` file Phase 1, upgrade path defined (§6) |

Lower-priority items deferred to Phase 2 (§8):

| # | Item | Deferred because |
|---|------|----------------|
| 4 | `data/` bootstrap procedure | Resolved by rsync + chown — documented in runbook |
| 5 | No cloud monitoring/alerting | Phase 1 uses log tailing; structured alerting is Phase 2 |
| 6 | Market-hours downstream scoring proof | Operational proof, not a deployment gate |
| 7 | `ibkr_client_id=10` not configurable | Fine for single-bot Phase 1 |

---

## 4. IBKR Gateway Decision

### The problem

IBKR Gateway is not a standard cloud service. It requires a GUI login session,
uses a persistent TCP socket (not a public API), and must be authenticated
interactively on first start. This is the hardest dependency in the entire
cloud migration.

### Options compared

**Option A — Same VM, IBC headless (recommended for Phase 1)**

IBC (IBController) is an open-source tool that automates IBKR Gateway login
using a pre-configured credentials file and a headless X server (Xvfb).
The Gateway process runs on the same VM as the Decifer bot stack.

```
Cloud VM (single machine)
├── Xvfb (headless X11 server — no display needed)
├── IBKR IB Gateway process (port 4002, paper)
│     managed by: IBC with auto-login config
└── Docker stack (docker-compose)
      handoff-publisher  (no IBKR dependency)
      handoff-observer   (no IBKR dependency)
      live-bot           (IBKR_HOST=127.0.0.1, IBKR_PORT=4002)
                          via network_mode: host
```

| Criterion | Assessment |
|-----------|-----------|
| Operational complexity | Medium — IBC setup is one-time, well-documented |
| Stability | Good — Gateway and bot fail/restart together; no network routing |
| Security | Good — Gateway port never touches public internet |
| Network risk | None — loopback only |
| Restart/reconnect | IBC handles daily Gateway reset automatically once configured |
| Paper trading suitability | ✅ Excellent |
| Live trading suitability | ✅ Acceptable (Phase 1 live), upgrade to Option B for production live |
| Phase 1 recommendation | **Use this** |

One known issue: IBKR paper accounts can suppress 2FA for IBC auto-login.
This must be tested on the first VM deploy. If 2FA cannot be suppressed,
the first Gateway login requires a one-time manual step via VNC, after which
IBC maintains the session.

**Option B — Separate Gateway VM (Phase 2)**

Gateway runs on a dedicated VM on a private VPC subnet. Bot connects
to Gateway via private IP. Recommended when running live trading at scale,
where independent restart of bot and Gateway matters.

| Criterion | Assessment |
|-----------|-----------|
| Operational complexity | High — two VMs, VPC networking, firewall rules |
| Stability | Better isolation — bot and Gateway restart independently |
| Security | Better — Gateway VM has no other surface |
| Network risk | Low — private VPC only, never public |
| Cost | Higher — second VM ($10–20/mo additional) |
| Phase 1 recommendation | Defer to Phase 2 |

**Option C — Community Docker Gateway image**

Community images (e.g. `ghcr.io/extrange/ibkr-gateway`) run IB Gateway in
a Docker container. Conceptually clean but carries risks:

- Not IBKR-official; may break on Gateway version updates
- IBKR credentials must be injected as env vars into the container
- 2FA suppression and session reset behaviour vary between image versions
- Adds a dependency on a community-maintained image with no SLA

| Phase 1 recommendation | Defer — evaluate in Phase 2 once IBC on same VM is proven |

### Decision: Option A — Same VM, IBC headless

IBC on the same VM is the lowest-complexity path that is production-capable.
It does not require additional infrastructure, has a clear restart model,
and the IBKR paper account 2FA suppression is well-understood.

**IBC configuration items (to complete before deployment):**

1. Install IBC on the VM: `https://github.com/IbcAlpha/IBC`
2. Create `jts.ini` with paper account credentials (stored in secrets manager or
   manually created on VM — never committed to git)
3. Configure IBC to use `TRADING_MODE=paper` and `IBKR_PORT=4002`
4. Install Xvfb: `apt-get install -y xvfb`
5. Add IBC launch to systemd so it starts on VM boot before Docker stack
6. Test: confirm Gateway accepts bot connection at `127.0.0.1:4002`

**Port setting to use:** `IBKR_PORT=4002` (IB Gateway paper, not TWS).
IB Gateway is lighter than TWS and better suited to headless operation.

---

## 5. Cloud Provider Decision

### Criteria for Phase 1 paper trading

This is a single-server deployment running a trading bot against a paper account.
The requirements are modest: a stable Linux VM, static IP, Docker support, SSH access,
and straightforward billing. Enterprise-grade features (auto-scaling, multi-region,
managed Kubernetes) are unnecessary and add cost and complexity.

### Providers compared

**DigitalOcean Droplet (recommended)**

| Criterion | Assessment |
|-----------|-----------|
| VM setup | Excellent — marketplace Docker image, SSH key auth, 2-minute provisioning |
| Docker support | Native — Docker Droplet available, or install manually in < 5 min |
| Static IP | ✅ Reserved IPs included, no extra charge |
| Secrets support | DigitalOcean Managed Secrets is not a full secrets manager; use `.env` on VM (Phase 1) |
| Monitoring | Basic CPU/memory/disk graphs in dashboard; log forwarding via agent (optional) |
| Cost | $24–48/mo for 4 vCPU / 8 GB Droplet (suitable for TA-Lib + full stack) |
| Operational burden | Very low — one SSH target, no IAM complexity, no VPC config required for Phase 1 |
| IBKR suitability | ✅ Standard Linux VM — IBC and Xvfb install cleanly |
| Verdict | **Use this for Phase 1** |

**AWS EC2 / Lightsail**

| Criterion | Assessment |
|-----------|-----------|
| VM setup | More complex — security groups, IAM roles, key pairs, VPC subnets |
| Docker support | ✅ Excellent — EC2 supports Docker natively |
| Static IP | Elastic IP — small charge if detached from running instance |
| Secrets support | ✅ AWS SSM Parameter Store / Secrets Manager — best in class |
| Monitoring | CloudWatch — powerful but requires configuration |
| Cost | EC2 t3.medium ($30–40/mo) or Lightsail $20/mo — comparable to DigitalOcean |
| Operational burden | Higher — IAM, security groups, VPC all add cognitive overhead |
| Verdict | Excellent for Phase 2 if secrets manager is needed; overkill for Phase 1 paper |

**Azure VM**

| Criterion | Assessment |
|-----------|-----------|
| VM setup | Complex — resource groups, subscription hierarchy, Azure AD |
| Docker support | ✅ Supported |
| Secrets | Azure Key Vault — capable |
| Cost | Comparable to EC2 |
| Operational burden | High — highest cognitive overhead of the options |
| Verdict | Defer entirely — unnecessary complexity for a solo trading system |

**Google Cloud VM (GCE)**

| Criterion | Assessment |
|-----------|-----------|
| VM setup | Moderate — project/zone model is cleaner than AWS but less familiar |
| Docker support | ✅ Supported; Container-Optimized OS is purpose-built |
| Secrets | GCP Secret Manager — very capable |
| Cost | Comparable |
| Operational burden | Medium |
| Verdict | Reasonable Phase 2 option; no advantage over DigitalOcean for Phase 1 |

### Decision: DigitalOcean Droplet

**Recommended spec for Phase 1:**

| Parameter | Value | Reason |
|-----------|-------|--------|
| Plan | General Purpose 4 vCPU / 8 GB | TA-Lib compile, full Python stack, IBC, Docker overhead |
| Region | Choose closest to IBKR data centres | NYC1 (New York) or AMS3 (Amsterdam) |
| OS | Ubuntu 22.04 LTS | LTS, well-tested with IBC, Docker install is one command |
| Storage | 80 GB SSD | `data/` persistent volume, logs, model artefacts |
| Static IP | DigitalOcean Reserved IP | Required — IBKR API whitelisting by IP in future live config |
| Backups | Weekly automated backup ($5/mo extra) | Protects `data/` if volume is not separately snapshotted |
| Monitoring | DigitalOcean agent (free) | CPU, memory, disk graphs; basic alerting via email |

**Estimated monthly cost:** ~$30 Droplet + $5 backup = **~$35/mo for paper trading**.

---

## 6. Secrets Decision

### The constraint

Decifer requires 6 mandatory secrets and 3 recommended secrets (see §2 env var contract).
These must never be committed to git, never baked into the Docker image, and must be
accessible to the running containers.

### Options compared

**Option A — `.env` file on VM with strict permissions (recommended for Phase 1)**

```bash
# On the VM:
vim /opt/decifer/.env     # populate values from your password manager
chmod 600 /opt/decifer/.env
chown root:root /opt/decifer/.env
```

Docker Compose picks up `.env` via `env_file: .env` in `docker-compose.yml`.

| Criterion | Assessment |
|-----------|-----------|
| Setup complexity | Minimal — SSH in, create file, set permissions |
| Security | Adequate for Phase 1 — file is readable only by root, not in git, not in image |
| Rotation | Manual — SSH in, edit file, restart affected service |
| Auditability | None — no access log |
| Suitable for paper trading | ✅ Yes |
| Suitable for live trading | ⚠️ Acceptable if VM is access-controlled; upgrade to secrets manager for production live |
| Phase 1 recommendation | **Use this** |

**Option B — DigitalOcean Managed Secrets (not available)**

DigitalOcean does not have a native secrets manager equivalent to AWS SSM.
The closest option is using environment variables in App Platform (not applicable
here — we are running on a Droplet, not App Platform).

**Option C — AWS SSM Parameter Store or Secrets Manager**

Best-in-class for secrets management. Supports versioning, access policies, automatic
rotation, and audit logs. The right answer for Phase 2 / live trading. Requires
switching to AWS or using SSM cross-account (complex for Phase 1).

**Option D — 1Password CLI or external secrets workflow**

1Password has a `op run` CLI that injects secrets at process start. Clean workflow
for developer machines. For a cloud VM, it requires the 1Password CLI installed on
the VM and a service account token. Viable but adds a dependency on 1Password.

| Phase 1 recommendation | Acceptable alternative to `.env` if Amit already uses 1Password |

### Decision: `.env` file on VM for Phase 1, with defined upgrade path

**Phase 1 procedure:**
1. SSH into VM after provisioning
2. `cp .env.example /opt/decifer/.env`
3. Populate values from Amit's password manager (never from this repo)
4. `chmod 600 /opt/decifer/.env && chown root:root /opt/decifer/.env`
5. Verify: `python3 scripts/healthcheck.py --strict` (all checks green)

**Phase 2 upgrade:** Migrate to AWS SSM Parameter Store if moving to AWS,
or 1Password `op run` workflow if staying on DigitalOcean. Rotation procedure
becomes: update secret in manager, re-pull on VM, `docker compose restart <service>`.

**What must never happen:**
- `.env` committed to git (enforced by `.gitignore` and belt-and-suspenders Dockerfile `rm -f .env`)
- Secrets visible in `docker compose config` output (already demonstrated as a risk — always pipe through `docker compose config 2>&1 | grep -v "KEY\|SECRET\|TOKEN\|PASSWORD"` for review)
- Secrets in container image layers (Dockerfile removes `.env` at build time)

---

## 7. Recommended Phase 1 Architecture

This is the complete recommended deployment. It answers every open decision.

### Architecture diagram

```
DigitalOcean Droplet (Ubuntu 22.04, 4 vCPU / 8 GB, NYC1)
/opt/decifer/
├── .env                         (600 root:root — secrets only, never in git)
├── docker-compose.yml           (from repo)
├── data/                        (bind-mounted from dev machine via rsync on first deploy)
│   ├── intelligence/            (populated intelligence files)
│   ├── universe_builder/        (shadow universe)
│   ├── live/                    (publisher output — regenerated each cycle)
│   └── ...                      (other data files from git-tracked state)
└── logs/                        (bind-mounted, log rotation active)

Systemd services (host level, before Docker stack):
├── xvfb.service                 (headless X11 server — required by IBC/Gateway)
└── ibcgateway.service           (IBC managing IB Gateway on port 4002, paper)

Docker Compose stack:
├── handoff-publisher            (python3 handoff_publisher.py, restart: on-failure:3)
├── handoff-observer             (python3 handoff_publisher_observer.py, restart: on-failure:3)
└── live-bot                     (network_mode: host → reaches Gateway at 127.0.0.1:4002)
                                   IBKR_HOST=127.0.0.1, IBKR_PORT=4002
```

### Answers to the five architecture questions

**1. Which cloud provider?**
DigitalOcean Droplet — lowest operational burden, clean Linux VM, static IP,
adequate cost for Phase 1 paper trading (~$35/mo).

**2. IBKR Gateway on same VM or separately?**
Same VM (Option A). IBC manages Gateway headlessly via Xvfb.
Gateway port 4002 is never exposed to the public internet.
Bot reaches Gateway via `network_mode: host` at `127.0.0.1:4002`.

**3. How are secrets handled in Phase 1?**
`.env` file at `/opt/decifer/.env`, permissions 600, owned by root.
Populated manually from Amit's password manager after VM provisioning.
Never committed to git. Never in the Docker image.

**4. What is deferred to Phase 2?**
See §8 — separate Gateway VM, secrets manager, monitoring/alerting, `docker-compose.yml`
upgrade for cron scheduling, live trading approval.

**5. What must Amit approve before deployment?**
See §9 — full approval checklist.

### Service startup order (critical)

```
1. Xvfb starts (systemd)
2. IBC starts IB Gateway on port 4002 (systemd, after xvfb)
3. Wait for Gateway to accept connections (IBC handles this)
4. docker compose up -d handoff-publisher
5. Wait for first publisher cycle (~15 min)
6. docker compose up -d handoff-observer
7. python3 scripts/cloud_preflight.py   (must exit 0)
8. python3 scripts/healthcheck.py --strict  (must exit 0)
9. [ONLY with Amit approval] change live-bot command to python3 bot.py
10. docker compose up -d live-bot
```

Steps 1–8 can run without Amit present. Step 9–10 require Amit's explicit approval
and must not happen during market hours (09:30–16:00 ET).

### What runs immediately on Phase 1 VM (no IBKR required)

- `handoff-publisher` — intelligence universe publishing, 15-minute cycles
- `handoff-observer` — monitoring the publisher, reporting on handoff state
- `scripts/healthcheck.py` — container health monitoring
- `scripts/bootstrap_runtime_dirs.py` — directory initialisation

These three can be deployed and validated before IBKR Gateway is ever started.
This is the safe first step — verify the data pipeline before touching the broker.

---

## 8. Deferred Phase 2 Items

These items are not blockers for Phase 1 paper trading. They are the right next
investments once Phase 1 is stable.

| Item | Why deferred | When to implement |
|------|-------------|------------------|
| Separate Gateway VM (Option B) | Adds infrastructure complexity; same-VM is fine for paper | When moving to live trading |
| Secrets manager (AWS SSM / 1Password) | `.env` is adequate for paper; Phase 1 risk is low | Before live trading or when team grows |
| Cloud monitoring / alerting | Log tailing is sufficient for solo operator during paper | After first full week of stable cloud operation |
| `docker-compose.yml` cron scheduler | Publisher restart policy handles it adequately for now | If publisher scheduling becomes unreliable |
| `ibkr_client_id` configurable | Always 10; safe for single-bot | Before running multiple bot instances |
| Community Docker Gateway image (Option C) | Not IBKR-official; IBC is safer | Evaluate in 6 months |
| Full CI/CD pipeline | Manual deploy is fine for a solo operator | When codebase has more contributors |
| VNC remote access to Gateway | IBC headless is preferred; VNC is a fallback | Only if IBC headless fails in practice |
| log rotation and cloud log aggregation | `RotatingFileHandler` handles it locally | When logs need querying across restarts |

---

## 9. Amit Approval Checklist

Every item below requires Amit's explicit sign-off before the corresponding
deployment step proceeds. No item is optional.

### Infrastructure (before VM provisioning)
- [ ] **Cloud provider confirmed:** DigitalOcean Droplet
- [ ] **VM spec confirmed:** 4 vCPU / 8 GB / Ubuntu 22.04 / NYC1 (or alternative region)
- [ ] **Monthly cost approved:** ~$35/mo (Droplet $30 + backup $5)
- [ ] **Static IP confirmed:** DigitalOcean Reserved IP assigned to Droplet

### IBKR Gateway (before bot can connect)
- [ ] **IBKR Gateway model confirmed:** Same VM, IBC headless, port 4002 (paper)
- [ ] **IBC installed and tested on VM:** Gateway starts on boot, no manual login required
- [ ] **2FA suppression confirmed:** paper account 2FA disabled for IBC auto-login
- [ ] **Gateway port confirmed isolated:** 4002 is NOT open on the VM's public firewall rule

### Secrets (before any service starts)
- [ ] **Secrets sourced:** all mandatory env vars retrieved from Amit's password manager
- [ ] **`.env` populated on VM:** `/opt/decifer/.env` has all 6 mandatory + 3 recommended vars
- [ ] **`.env` permissions confirmed:** `chmod 600`, `chown root:root`
- [ ] **`healthcheck.py --strict` passes:** all checks green inside container with `--env-file`

### Data bootstrap (before publisher starts)
- [ ] **`data/` rsync confirmed:** intelligence files, universe builder, reference data transferred to VM
- [ ] **`chown -R 1000:1000 data/ logs/` confirmed:** container user can write to bind mounts
- [ ] **`bootstrap_runtime_dirs.py` passes:** all directories exist on VM

### Publisher validation (before bot starts)
- [ ] **`handoff-publisher` and `handoff-observer` running on VM:** first cycle completes
- [ ] **`cloud_preflight.py` exits 0:** intelligence files valid, manifest fresh
- [ ] **`healthcheck.py --strict` exits 0 inside container on VM:** all checks green

### Live bot (final gate — Amit must be present)
- [ ] **`live-bot` command changed to `python3 bot.py`:** Amit approves the compose edit
- [ ] **`HANDOFF_PUBLISHER_MODE` confirmed:** `validation_only` for Phase 1 start
- [ ] **Time confirmed:** NOT during market hours (09:30–16:00 ET)
- [ ] **Amit explicitly approves bot start:** verbal or written approval in this session

---

## 10. Exact Next Deployment Branch After Approval

Once Amit approves the decisions in this document, the next branch is:

**Branch:** `cloud/cloud-phase1-vm-deployment`

That branch will:
1. Write the VM provisioning script (`scripts/provision_vm.sh` or equivalent)
2. Write the systemd service files for Xvfb and IBC (`scripts/systemd/`)
3. Write the IBC configuration template (`config/ibc/config.ini.example`)
4. Write the first-deploy data bootstrap script (`scripts/bootstrap_first_deploy.sh`)
5. Update `docs/future_cloud_deployment_runbook.md` from DRAFT to executable
6. Run the full deployment checklist against the live VM
7. Confirm publisher and observer running cleanly before touching the bot

**What that branch does NOT do:**
- Does not start `bot.py`
- Does not activate `HANDOFF_PUBLISHER_MODE=controlled_activation`
- Does not change any trading logic
- Does not provision the VM itself (Amit does that via DigitalOcean dashboard)

The bot start is a separate approval gate, documented in the runbook, and requires
Amit to be present.

---

## Appendix: Validation Results (this session)

| Command | Result |
|---------|--------|
| `python3 scripts/healthcheck.py` | ✅ Exit 1 expected — env vars absent in worktree; all imports, dirs, NLTK pass |
| `python3 scripts/bootstrap_runtime_dirs.py --quiet` | ✅ Exit 0 |
| `docker compose config` | ✅ All 3 services valid — note: prints secrets from `.env` to stdout, pipe carefully |
| `docker build --no-cache` | ✅ Succeeded (sequential `make` fix for TA-Lib gen_code race) |
| `docker run healthcheck.py` | ✅ All non-env checks pass inside container |
| `docker run bootstrap_runtime_dirs.py --quiet` | ✅ Exit 0 inside container |
| NLTK vader inside container | ✅ `NLTK_DATA=/usr/share/nltk_data`, `SentimentIntensityAnalyzer` scores correctly |

---

*No infrastructure was provisioned. No deployment was performed. No trading logic was changed.*
*This document is a decision record only. Every action item requires Amit's approval.*
