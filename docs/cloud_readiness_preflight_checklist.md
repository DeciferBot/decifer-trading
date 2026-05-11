# Cloud Readiness Preflight Checklist

> ⚠️  **READINESS CHECKLIST ONLY — NOT A DEPLOYMENT RUNBOOK**
>
> This document lists every condition that must be confirmed TRUE before
> a Decifer cloud deployment can proceed. It does not describe how to deploy.
> See `docs/future_cloud_deployment_runbook.md` for the deployment sequence.
>
> Every item must be individually confirmed by Amit before the deployment
> gate opens. No exceptions.

**Branch:** `cloud/cloud-readiness-validation-hardening`
**Status:** Pre-deployment reference — not yet executable (blockers open)
**Date:** 2026-05-11

---

## Section 1 — Local Validation (run before any cloud work)

These checks run on the developer's machine against the current branch.
All must pass before anything is pushed to the cloud.

### 1.1 — Directory Bootstrap

```bash
python3 scripts/bootstrap_runtime_dirs.py
```

Expected: All required directories exist or are created. No `[ERROR]` lines.

- [ ] `bootstrap_runtime_dirs.py` exits 0 with no errors

---

### 1.2 — Health Check (local mode)

```bash
python3 scripts/healthcheck.py
```

Expected: `PASS` — all blocking checks green. Env var warnings are expected
locally if `.env` is not populated.

- [ ] `healthcheck.py` exits 0 in local mode

---

### 1.3 — Health Check (strict mode)

```bash
python3 scripts/healthcheck.py --strict
```

Expected: `PASS` — all blocking checks AND recommended env vars green.
This must pass before cloud deployment. If recommended vars are missing
locally, the strict check will fail — this is correct and expected.

- [ ] `healthcheck.py --strict` exits 0 (requires `.env` fully populated)

---

### 1.4 — NLTK vader_lexicon

```bash
python3 -c "from nltk.sentiment import SentimentIntensityAnalyzer; SentimentIntensityAnalyzer(); print('vader_lexicon OK')"
```

Expected: `vader_lexicon OK`. If this fails locally, run:
```bash
python3 -m nltk.downloader vader_lexicon
```
The Docker image pre-downloads this via `NLTK_DATA=/usr/share/nltk_data` —
no internet access needed at container runtime.

- [ ] vader_lexicon available in local Python environment

---

### 1.5 — Intelligence File Validation

```bash
python3 scripts/validate_intelligence_files.py
```

Expected: All intelligence files valid. No `[FAIL]` lines.

- [ ] `validate_intelligence_files.py` exits 0

---

### 1.6 — Smoke Tests

```bash
python3 -m pytest -m smoke -q
```

Expected: All smoke tests pass. Pre-existing trailing stop failures (2)
are unrelated to cloud readiness and are acceptable.

- [ ] Smoke tests pass (exit 0)

---

### 1.7 — Handoff Tests

```bash
python3 -m pytest tests/test_handoff_publisher.py tests/test_handoff_wiring_integration.py -q
```

Expected: All handoff publisher and wiring integration tests pass.

- [ ] Handoff tests pass (exit 0)

---

### 1.8 — Risk and Orders Tests

```bash
python3 -m pytest tests/test_orders_core.py tests/test_risk.py -q
```

Expected: All risk and order core tests pass.

- [ ] Risk/orders tests pass (exit 0)

---

### 1.9 — Activation Tests

```bash
python3 -m pytest tests/test_activation_gate.py -q 2>/dev/null || python3 -m pytest -k "activation" -q
```

Expected: Activation gate tests pass.

- [ ] Activation tests pass (exit 0)

---

### 1.10 — Secrets Not Committed

```bash
git diff HEAD | grep -E "(ANTHROPIC_API_KEY|ALPACA_SECRET|FMP_API_KEY|FRED_API_KEY)" | grep -v "example\|template\|#\|<from" | grep "="
```

Expected: No output. Any line with an actual key value (not a placeholder)
is a blocker — do not proceed.

- [ ] No secrets present in any committed or staged file

---

### 1.11 — .env Not Tracked

```bash
git ls-files .env .env.local .env.production 2>/dev/null | wc -l
```

Expected: `0`. Any non-zero output means a secrets file is git-tracked —
hard stop, remove immediately.

- [ ] `.env` is not tracked by git

---

## Section 2 — Docker Validation (requires Docker daemon)

These checks require Docker to be running. They cannot be performed in the
current local environment (Docker daemon unavailable). They MUST be completed
on the cloud VM before the first deployment.

### 2.1 — Docker Daemon Running

```bash
docker info > /dev/null 2>&1 && echo "Docker OK" || echo "Docker not running"
```

- [ ] Docker daemon is running on target machine

---

### 2.2 — Docker Build

```bash
docker build -t decifer-trading:4.0 .
```

Expected: Build completes with exit 0. Key milestones to confirm in logs:
- `ta-lib-0.4.0-src.tar.gz` downloaded and compiled
- `make install` completed without error
- Python packages installed into `/install` prefix
- TA-Lib shared library copied to runtime stage (`/usr/local/lib`)
- `ldconfig` run (dynamic linker cache refreshed)
- NLTK vader_lexicon downloaded to `/usr/share/nltk_data`
- Non-root user `decifer` (UID 1000) created
- `.env` removed (belt-and-suspenders)

- [ ] `docker build` exits 0 on target machine

---

### 2.3 — Healthcheck Inside Container (no env vars)

```bash
docker run --rm decifer-trading:4.0
```

Expected: Imports pass, dirs pass, TA-Lib loads, NLTK vader_lexicon loads.
Env var checks will fail (expected — no `.env` injected). Exit code 1 is
acceptable here IF the only failures are env var presence checks.

- [ ] TA-Lib imports cleanly inside container
- [ ] NLTK vader_lexicon loads cleanly inside container
- [ ] Non-root user `decifer` (UID 1000) is the running user

---

### 2.4 — Healthcheck Inside Container (with env vars)

```bash
docker run --rm --env-file .env decifer-trading:4.0
```

Expected: All blocking checks pass. Exit 0.

- [ ] `healthcheck.py` exits 0 inside container with `.env` injected

---

### 2.5 — Healthcheck Strict Inside Container

```bash
docker run --rm --env-file .env decifer-trading:4.0 \
  python3 scripts/healthcheck.py --strict
```

Expected: All checks pass including recommended env vars. Exit 0.

- [ ] `healthcheck.py --strict` exits 0 inside container

---

### 2.6 — Cloud Preflight Inside Container

```bash
docker run --rm \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  decifer-trading:4.0 \
  python3 scripts/cloud_preflight.py
```

Expected: All preflight checks pass. Intelligence files valid. Exit 0.

- [ ] `cloud_preflight.py` exits 0 inside container with data volume mounted

---

### 2.7 — Smoke Tests Inside Container

```bash
docker run --rm \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  decifer-trading:4.0 \
  python3 -m pytest -m smoke -q
```

Expected: All smoke tests pass inside the container image.

- [ ] Smoke tests pass inside container (exit 0)

---

### 2.8 — Non-Root User Confirmed

```bash
docker run --rm decifer-trading:4.0 whoami
```

Expected: `decifer`

- [ ] Container runs as `decifer`, not `root`

---

### 2.9 — .env Not in Image

```bash
docker run --rm decifer-trading:4.0 ls -la /app/.env 2>&1 | grep "No such"
```

Expected: `No such file or directory`

- [ ] `.env` is absent from the built image

---

### 2.10 — Volume Bind-Mount Permissions

On the VM, before `docker compose up`:

```bash
chown -R 1000:1000 ./data ./logs
```

Then verify the container can write:

```bash
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  --user 1000:1000 \
  decifer-trading:4.0 \
  python3 -c "open('/app/data/.write_probe','w').write('ok'); import os; os.remove('/app/data/.write_probe'); print('write OK')"
```

Expected: `write OK`

- [ ] Container user (UID 1000) can write to bind-mounted `data/` and `logs/`

---

## Section 3 — Infrastructure and Secrets (cloud VM)

These checks are performed on the target cloud VM, not on the developer machine.

### 3.1 — Cloud Provider and VM Selected

- [ ] Cloud provider confirmed: ___________________
- [ ] VM spec confirmed (min 4 vCPU, 8 GB RAM, 50 GB disk recommended)
- [ ] VM OS confirmed (Ubuntu 22.04 LTS or equivalent)
- [ ] Docker >= 24.0 installed on VM
- [ ] Docker Compose v2 installed on VM

---

### 3.2 — Secrets Manager Selected and Populated

All mandatory env vars must be stored in a secrets manager — not in a
committed file. The secrets manager must be:
- Accessible from the VM at deploy time
- Keys rotatable without re-deploying the image

- [ ] Secrets manager selected: ___________________
- [ ] `ANTHROPIC_API_KEY` stored in secrets manager
- [ ] `ALPACA_API_KEY` stored in secrets manager
- [ ] `ALPACA_SECRET_KEY` stored in secrets manager
- [ ] `FMP_API_KEY` stored in secrets manager
- [ ] `ALPHA_VANTAGE_KEY` stored in secrets manager
- [ ] `FRED_API_KEY` stored in secrets manager
- [ ] `IBKR_PAPER_ACCOUNT` stored in secrets manager
- [ ] `IBKR_ACTIVE_ACCOUNT` stored in secrets manager

---

### 3.3 — IBKR Gateway Access Model Decided

This is the #1 cloud blocker. The bot cannot connect to IBKR without resolving
the gateway model. Three options — only one may be active at a time:

| Option | Description | Status |
|--------|-------------|--------|
| A — Same VM (IBC) | IBC + headless Xvfb on same VM; bot connects `127.0.0.1:4002` | **Recommended Phase 1** |
| B — Separate Gateway VM | Gateway on private subnet; bot connects to private IP | Phase 2 |
| C — Docker Gateway | IBKR Gateway in Docker container | Advanced |

- [ ] IBKR Gateway access model decided: ___________________
- [ ] IBC (IBController) installed and configured for headless operation
- [ ] `IBKR_HOST` and `IBKR_PORT` set correctly in `.env` (4002 for Gateway paper)
- [ ] 2FA suppression tested for unattended restart (IBKR security key / device setup)

---

### 3.4 — Data Bootstrap Completed

The `data/` directory must be present on the VM with current intelligence files
before the first container start.

```bash
# From developer machine (first deploy only):
rsync -avz --exclude '.env' ./data/ user@cloud-vm:/opt/decifer/data/
chown -R 1000:1000 /opt/decifer/data /opt/decifer/logs
```

- [ ] `data/intelligence/` files synced to VM
- [ ] `data/universe_builder/` files synced to VM
- [ ] `data/reference/` files synced to VM (if any)
- [ ] `chown -R 1000:1000 data/ logs/` completed on VM

---

## Section 4 — Final Gates (Amit approval required)

These gates must be confirmed by Amit, not automated.

- [ ] All Section 1 checks pass
- [ ] All Section 2 checks pass (Docker validation on target VM)
- [ ] All Section 3 checks pass (infrastructure and secrets)
- [ ] Deployment is NOT during market hours (09:30–16:00 ET)
- [ ] **Amit has approved the deployment**

> No deployment may proceed without Amit's explicit approval.
> This checklist is a prerequisite, not a substitute, for that approval.

---

## Blocker Summary

The following blockers from `docs/cloud_readiness_contract.md` remain open.
Each blocked item maps to a checklist section above.

| # | Blocker | Section | Severity |
|---|---------|---------|----------|
| 1 | IBKR Gateway cloud access model undefined | 3.3 | **Critical** |
| 2 | IBKR Gateway restart requires manual GUI session (2FA) | 3.3 | **High** |
| 3 | No cloud provider selected | 3.1 | **High** |
| 4 | TA-Lib source build must be validated on target VM image | 2.2 | **Medium** |
| 5 | `data/` bind-mount bootstrap procedure (first cloud deploy) | 3.4 | **Medium** |
| 6 | No secrets manager selected or integrated | 3.2 | **Medium** |
| 7 | No automated IBKR reconnect on cold Gateway start | 3.3 | **Medium** |
| 8 | Docker build not yet validated (daemon not running locally) | 2.2 | **Low** |
| 9 | No cloud monitoring/alerting wired | — | **Low** |
| 10 | Market-hours downstream scoring proof still pending | — | **Low** |

Until blockers 1–3 are resolved, the deployment cannot proceed regardless of
how many other checklist items are checked.

---

*This checklist must be completed in full, in order, before any deployment.
The word "done" means every checkbox above is ticked and Amit has approved.*
