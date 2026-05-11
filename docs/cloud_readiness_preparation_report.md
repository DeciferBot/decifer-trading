# Cloud Readiness Preparation Report

**Branch:** `cloud/cloud-readiness-preparation`
**Base:** `master` (commit `9efa2e1`)
**Date:** 2026-05-11
**Prepared by:** Cowork (Claude)
**Approved by:** Amit (required before merge)

---

## Explicit Confirmations

> **No cloud migration was performed.**
> **No cloud infrastructure was provisioned.**
> **No deployment was executed.**
> **No trading behaviour was intentionally changed.**
> **No broker calls were made.**
> **No activation flags were modified.**

This branch is cloud readiness preparation only: packaging discipline,
operational design, dependency documentation, and health check tooling.

---

## 1. Files Created

| File | Task | Purpose |
|------|------|---------|
| `docs/cloud_readiness_contract.md` | A | Authoritative contract: what runs, what is required, what must be mounted, blockers |
| `requirements-prod.txt` | D | Curated production dependency list with classification and review notes |
| `scripts/healthcheck.py` | E | Lightweight Docker health check — imports, dirs, env var presence, no broker |
| `docs/ibkr_gateway_future_cloud_design.md` | F | IBKR Gateway cloud architecture options, security considerations, open questions |
| `docs/future_cloud_deployment_runbook.md` | G | DRAFT runbook — build, env setup, startup, shutdown, rollback, blockers |
| `docs/cloud_readiness_preparation_report.md` | I | This report |

---

## 2. Files Modified

| File | Task | Change |
|------|------|--------|
| `Dockerfile` | B | Added non-root user `decifer` (UID 1000); multi-stage build (builder + runtime stages); TA-Lib installed to `/usr/local`; belt-and-suspenders `.env` removal; CMD updated to `scripts/healthcheck.py` |
| `.dockerignore` | C | Added `archive/`, `tests/archive/`, `data/intelligence/backtest/`, `.fail_*` patterns, `*.app/`, `*.command`, `graphify-out/`, rotated log patterns, office docs, local image files; reorganised with clear section comments |

---

## 3. What Was Prepared

### Packaging
- **Dockerfile** is production-oriented, multi-stage, non-root, no secrets, no archive, no tests.
- **`.dockerignore`** covers all exclusion categories: secrets, runtime state, archive, tests, backtest data, macOS artefacts, Python caches, office documents.
- **`requirements-prod.txt`** documents every production dependency by category (CORE / MARKET / SIGNALS / ML / NLP / UTIL / BROKER) with usage context and pre-production review notes.

### Operational Design
- **Cloud Readiness Contract** defines the full runtime contract: entry point, env vars, persistent files, generated files, log files, volumes, startup/shutdown expectations, fail-closed behaviour, health check expectations, and 10 known blockers.
- **IBKR Gateway Future Design** documents all three architecture options (same VM, separate VM, Docker IBC), security considerations, restart/reconnect expectations, what must never be hardcoded, and 10 open questions.
- **Future Deployment Runbook** (DRAFT) provides the intended deployment sequence from VM provisioning through startup, shutdown, log inspection, rollback, and emergency stop — with explicit blocker list.

### Health Check Tooling
- **`scripts/healthcheck.py`** is a fast (< 5 seconds), safe health check suitable for Docker `HEALTHCHECK` instruction. Checks: Python version, 7 key package imports (`anthropic`, `pandas`, `numpy`, `ib_async`, `talib`, `alpaca-py`), 3 Decifer module imports (`config`, `handoff_reader`, `utils.log_rotation`), 6 required directories, 4 required + 3 recommended env var presence (values never printed), recent `.fail_*` sentinel detection.

### Production Runtime Classification
The following table answers "what belongs where" for cloud Phase 1:

| Category | Runs in cloud container | Notes |
|----------|------------------------|-------|
| `bot.py` + `bot_trading.py` | ✅ Production | Requires IBKR Gateway |
| `handoff_publisher.py` | ✅ Production | No IBKR needed |
| `handoff_publisher_observer.py` | ✅ Production | No IBKR needed |
| `scripts/cloud_preflight.py` | ✅ Production | Pre-start validation |
| `scripts/healthcheck.py` | ✅ Production | Docker HEALTHCHECK |
| `scanner.py`, `signals.py`, `risk.py` | ✅ Imported by bot | Runtime modules |
| `archive/` | ❌ Excluded | Retired modules |
| `tests/` | ❌ Excluded | Test suite |
| `backtest_results/` | ❌ Excluded | Offline research |
| `data/intelligence/backtest/` | ❌ Excluded | Offline research |
| `scripts/cloud_preflight.py` (existing) | ✅ Validation | Comprehensive preflight |
| `Chief-Decifer-recovered/` | ❌ Excluded | Recovery artefact |
| `*.app/`, `*.command` | ❌ Excluded | macOS only |
| `advisory_reporter.py`, `advisory_log_reviewer.py` | ❌ Shadow pipeline | Not in live-bot import path |
| `backtest_intelligence.py`, `backtester.py` | ❌ Archive | Excluded from runtime image |

---

## 4. Dockerfile Summary

| Property | Value |
|----------|-------|
| Base image | `python:3.11-slim` (Debian bookworm) |
| Build stages | 2 (builder + runtime) |
| TA-Lib install | Source build (0.4.0, prefix `/usr/local`) in builder; shared lib copied to runtime |
| Python packages | Installed into `/install` prefix in builder; copied to runtime via `COPY --from=builder` |
| Non-root user | `decifer` (UID 1000, GID 1000) |
| Default CMD | `python3 scripts/healthcheck.py` |
| Secrets | Never baked in; belt-and-suspenders `rm -f .env` in build |
| Runtime dirs | Created in image: `data/live`, `data/heartbeats`, `data/intelligence`, `data/universe_builder`, `data/reference`, `data/runtime`, `logs` |
| Image size (estimate) | ~1.2 GB (Python 3.11 slim + TA-Lib + all dependencies) |

---

## 5. .dockerignore Summary

Categories excluded:

| Category | Patterns |
|----------|---------|
| Secrets | `.env`, `.env.*`, `*.pem`, `*.key` |
| Runtime state | `data/`, `logs/` |
| Archive | `archive/`, `tests/archive/` |
| Tests | `tests/`, `.pytest_cache/`, `.coverage`, `htmlcov/` |
| Backtest | `backtest_results/`, `data/intelligence/backtest/` |
| Fail sentinels | `data/live/.fail_*`, `.fail_*` |
| Git/VCS | `.git/`, `.github/`, `.githooks/`, `.gitignore` |
| Claude Code | `.claude/` |
| Python artefacts | `__pycache__/`, `*.pyc`, `*.egg-info/`, `dist/`, `build/` |
| Lint caches | `.mypy_cache/`, `.ruff_cache/`, `ruff.toml` |
| macOS | `.DS_Store`, `*.app/`, `*.command` |
| Docs/Roadmap | `docs/`, `roadmap/` |
| Dev components | `graphify-out/`, `Chief-Decifer-recovered/`, `chief-decifer/state/` |
| Office docs | `*.docx`, `*.xlsx` |
| Rotated logs | `logs/*.log.*`, `logs/*.gz` |
| Local images | `*.png`, `*.jpg`, `*.pdf` |

`.env.example` is explicitly NOT excluded — it is documentation.

---

## 6. Dependency Summary

All packages in `requirements.txt` are production packages. There are no dev-only
packages in `requirements.txt` — those live exclusively in `requirements-test.txt`.

`requirements-prod.txt` mirrors `requirements.txt` with added:
- Per-package classification (CORE / MARKET / SIGNALS / ML / NLP / UTIL / BROKER)
- Usage context (which modules import each package)
- Optional/graceful-fallback annotations
- Pre-production review notes (colorama cosmetic, nltk post-install step, etc.)

Pre-production action items from review notes:
1. `nltk` `vader_lexicon` must be downloaded in Dockerfile or bootstrap script.
2. `colorama` can be removed for cloud (cosmetic terminal colour).
3. `pyarrow` can be deferred if ML is initially disabled.

---

## 7. Health Check Summary

`scripts/healthcheck.py`:

| Check group | Checks |
|-------------|--------|
| Python version | >= 3.11 |
| Key imports | `anthropic`, `pandas`, `numpy`, `ib_async`, `talib`, `alpaca.trading.client` |
| Decifer modules | `config`, `handoff_reader`, `utils.log_rotation` |
| Directories | `data/`, `data/live/`, `data/heartbeats/`, `data/intelligence/`, `data/universe_builder/`, `logs/` |
| Env vars (blocking) | `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `IBKR_PAPER_ACCOUNT` |
| Env vars (recommended) | `ALPACA_BASE_URL`, `FMP_API_KEY`, `IBKR_ACTIVE_ACCOUNT` |
| Fail sentinels | Warns if `.fail_*` files < 1h old in `data/live/` |

Does NOT: connect to broker, place orders, mutate state, require IBKR, write any files.

---

## 8. IBKR Gateway Future Design Summary

Three options evaluated:
- **Option A (Recommended for Phase 1):** IBKR Gateway on same VM, managed by IBC/IBController with headless X11 (Xvfb). Bot connects to `127.0.0.1:4002`.
- **Option B (Phase 2):** Separate Gateway VM on private VPC subnet. Bot connects to private IP.
- **Option C (Advanced):** IBKR Gateway in Docker container using community image.

Blockers identified: 10 open questions including cloud provider selection, IBC vs Docker Gateway approach, 2FA suppression testing, credentials in secrets manager, and client ID safety for multi-process.

---

## 9. Validation Commands and Results

| Command | Result | Notes |
|---------|--------|-------|
| `python3 scripts/healthcheck.py` | ⚠️ Exit 1 | Expected in worktree: all imports pass, all dirs pass, env vars absent (no `.env` in worktree) |
| `python3 scripts/validate_intelligence_files.py` | ✅ Pass | All intelligence files valid |
| `python3 -m pytest -m smoke -q` | ✅ 7 passed, 1 skipped | Smoke tests pass |
| `python3 -m pytest tests/test_handoff_publisher.py tests/test_handoff_wiring_integration.py -q` | ✅ 213 passed, 4 skipped | Handoff tests pass |
| `python3 -m pytest tests/test_orders_core.py tests/test_risk.py -q` | ✅ 92 passed | Risk/order/broker tests pass |
| `python3 -m pytest tests/ -k trailing_stop -q` | ✅ 11 passed | Trailing stop tests pass in this environment |
| `python3 -c "import bot_trading; print('OK')"` | ✅ Pass | bot_trading import clean |
| `python3 -c "import scanner; print('OK')"` | ✅ Pass | scanner import clean |
| `docker build ...` | ⚠️ Not run | Docker daemon not running locally; static validation only |

**Healthcheck exit 1 classification:** Pre-existing environment condition.
The env var failures are expected in the worktree (no `.env` file present).
On a properly configured cloud VM with `.env` populated, healthcheck will pass.
This is not a code bug.

**Docker build not run classification:** Environment dependency.
Docker daemon is not running in this session. The Dockerfile is syntactically
correct and follows established patterns from the existing master Dockerfile.
A `docker build` must be run on a machine with Docker daemon before cloud migration.

---

## 10. Remaining Blockers

All blockers are from `docs/cloud_readiness_contract.md` section 13, reproduced here:

| # | Blocker | Severity |
|---|---------|----------|
| 1 | IBKR Gateway cloud access model undefined | **Critical** |
| 2 | IBKR Gateway restart requires manual GUI session | **High** |
| 3 | No cloud provider selected | **High** |
| 4 | TA-Lib source build must be validated on target VM image | **Medium** |
| 5 | `data/` bind-mount bootstrap procedure needed for first cloud deploy | **Medium** |
| 6 | No secrets manager selected or integrated | **Medium** |
| 7 | No automated IBKR reconnect on cold Gateway start | **Medium** |
| 8 | Docker build not validated (daemon not running in this session) | **Low** |
| 9 | No cloud monitoring/alerting wired | **Low** |
| 10 | Market-hours downstream scoring proof still pending | **Low** |

---

## 11. Exact Next Commands on a Clean Cloud VM

When a cloud VM is provisioned and all blockers above are resolved, the first
commands are:

```bash
# 1. Clone the repo
git clone https://github.com/DeciferBot/decifer-trading.git /opt/decifer
cd /opt/decifer

# 2. Populate .env from secrets manager (never commit this file)
secrets-manager-pull > .env && chmod 600 .env

# 3. Bootstrap data directory (from local rsync on first deploy)
rsync -avz user@dev-machine:/path/to/decifer/data/ /opt/decifer/data/
chown -R 1000:1000 /opt/decifer/data /opt/decifer/logs

# 4. Build image
docker build -t decifer-trading:4.0 .

# 5. Run health check (must pass before any other step)
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" decifer-trading:4.0

# 6. Run full preflight check
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" \
  decifer-trading:4.0 python3 scripts/cloud_preflight.py

# 7. Run smoke tests
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" \
  decifer-trading:4.0 python3 -m pytest -m smoke -q

# 8. Only if steps 5-7 all pass AND IBKR Gateway is running:
#    Start the full stack (requires docker-compose.yml — not yet written)
#    docker compose up -d
```
