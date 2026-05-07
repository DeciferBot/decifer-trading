# Intelligence-First Cloud Process Map

**Created:** 2026-05-07
**Sprint:** 7A.4
**Status:** Design / Pre-production — no handoff enabled
**Owner:** Cowork (Claude)
**Approver:** Amit

---

## 1. Phase 1 — Single VM / Docker Compose

### 1.1 Deployment Model

One cloud VM (e.g. AWS EC2 t3.medium, DigitalOcean Droplet 4GB, or Hetzner CX21).

One shared Docker Compose stack or supervisor/systemd process group.

One mounted data volume (`/data/decifer/`) shared across all containers.

Processes communicate only via files on the shared volume — not via sockets, RPC, or shared memory.

No process directly modifies another process's state files.

### 1.2 Docker Compose Service Layout (Phase 1)

```yaml
services:
  reference-data:
    # Weekly batch
    # build: ./workers/reference_data_worker
    # volumes: data:/data/decifer
    # restart: on-failure
    # schedule: cron("0 2 * * 0")

  economic-intelligence:
    # Daily pre-market
    # build: ./workers/economic_intelligence_worker
    # volumes: data:/data/decifer
    # restart: on-failure

  provider-ingestion:
    # Scan-cycle (every N minutes)
    # build: ./workers/provider_ingestion_worker
    # volumes: data:/data/decifer
    # restart: on-failure

  catalyst-event:
    # Daily + on NEWS_INTERRUPT
    # build: ./workers/catalyst_event_worker
    # volumes: data:/data/decifer
    # restart: on-failure

  company-quality:
    # Weekly or on earnings event
    # build: ./workers/company_quality_worker
    # volumes: data:/data/decifer
    # restart: on-failure

  technical-sensor:
    # Scan-cycle (after provider-ingestion)
    # build: ./workers/technical_market_sensor_worker
    # volumes: data:/data/decifer
    # restart: on-failure
    # depends_on: [provider-ingestion]

  universe-builder:
    # Scan-cycle (after technical-sensor + catalyst + economic)
    # build: ./workers/universe_builder_worker
    # volumes: data:/data/decifer
    # restart: on-failure
    # depends_on: [technical-sensor, catalyst-event, economic-intelligence]

  handoff-publisher:
    # Scan-cycle (after universe-builder)
    # build: ./workers/handoff_validator_publisher
    # volumes: data:/data/decifer
    # restart: on-failure
    # depends_on: [universe-builder]

  live-bot:
    # Always-on during market hours
    # build: ./decifer_bot
    # volumes: data:/data/decifer
    # restart: unless-stopped
    # environment: [ANTHROPIC_API_KEY, ALPACA_API_KEY, ...]

  observability:
    # Always-on
    # build: ./workers/observability_worker
    # volumes: data:/data/decifer
    # restart: unless-stopped
```

### 1.3 Supervisor/Systemd Alternative

If not using Docker Compose:

```ini
[program:economic_intelligence]
command=python3 workers/economic_intelligence_worker.py
autostart=true
autorestart=on-failure
stdout_logfile=/logs/economic_intelligence.log

[program:live_bot]
command=python3 bot_trading.py
autostart=true
autorestart=on-failure
stdout_logfile=/logs/bot_trading.log
```

### 1.4 Phase 1 Constraints

- All workers co-located on one VM
- Shared data volume — no network calls between workers
- Secrets loaded from `.env` file or environment variables injected at container start
- No secrets in any JSON output file
- Heartbeat files written to `/data/decifer/heartbeats/`
- Structured JSON logs written to `/logs/`
- Atomic file writes enforced: write to `.tmp`, validate, rename to final path

---

## 2. Phase 2 — Separate Containers / Scheduled Jobs

### 2.1 Deployment Model

Workers separated into distinct containers or cloud scheduled jobs (AWS Lambda, GCP Cloud Run Jobs, or container-based cron).

Live bot and execution/risk gateway remain as persistent services.

Intelligence workers become scheduled jobs — they produce output files and exit.

### 2.2 Service Separation

| Service | Type | Schedule |
|---------|------|----------|
| `decifer-reference-data` | Scheduled Job | Weekly |
| `decifer-provider-ingestion` | Scheduled Job | Scan-cycle |
| `decifer-economic-intelligence` | Scheduled Job | Daily pre-market |
| `decifer-company-quality` | Scheduled Job | Weekly / on earnings |
| `decifer-catalyst-event` | Scheduled Job | Daily + on NEWS_INTERRUPT |
| `decifer-technical-sensor` | Scheduled Job | Scan-cycle |
| `decifer-universe-builder` | Scheduled Job | Scan-cycle, after sensors |
| `decifer-handoff-publisher` | Scheduled Job | Scan-cycle, final gate |
| `decifer-live-bot` | Persistent Service | Always-on market hours |
| `decifer-execution-risk` | Persistent Service | Always-on market hours |
| `decifer-observability` | Persistent Service | Always-on |

### 2.3 File Exchange Policy

Workers do not call each other. They communicate only through files on the shared volume or object storage. All published files include `generated_at`, `expires_at`, and `validation_status`. Consuming workers check these fields before using any input.

### 2.4 Container Separation Rules

**Backtest containers** are never deployed to the production stack. They run on developer workstations or separate batch environments only.

**Advisory containers** (`advisory_reporter`, `advisory_log_reviewer`) may run as scheduled jobs in the same cluster but must not receive any input from the live bot's execution state.

**Diagnostic tools** (`provider_fetch_tester.py`, `factor_registry.py`, `reference_data_builder.py`) are development/setup tools only. They are not deployed in production containers.

---

## 3. Phase 3 — Optional Managed Orchestration

Deploy only if Phase 2 operational complexity justifies it. Not required for paper trading.

### 3.1 Managed Scheduler

Airflow, Prefect, or AWS Step Functions to replace cron-based scheduling.

Benefits: dependency tracking, retry policies, SLA alerting, visual DAG.

Required only if Phase 2 scheduling becomes unreliable.

### 3.2 Queue / Event Bus

Optional: SQS, RabbitMQ, or Redis Streams to replace file-based worker signalling.

Not required in Phase 1 or 2. File-based IPC is sufficient for paper trading cadence.

### 3.3 Object Storage for Snapshots

S3 or GCS to replace local file volume for snapshot archiving.

Snapshot retention (last 7 days of active universes, 30 days of advisory logs).

Required only if local volume space becomes a constraint.

### 3.4 Database for State / History

PostgreSQL or DynamoDB for training records, positions history, and IC validation data.

Not required while `data/training_records.jsonl` remains sufficient.

### 3.5 Monitoring Dashboard

Grafana + Prometheus, or Datadog, or Chief Decifer extended.

Alert on: manifest staleness, worker heartbeat missing, IBKR disconnect, Apex call failures.

### 3.6 Phase 3 Trigger Conditions

Phase 3 is triggered only if:
- Paper trading produces reliable alpha signal over ≥3 months
- Live trading is being planned
- Phase 2 complexity justifies managed orchestration

---

## 4. Cloud Runtime Concerns

### 4.1 File Paths

| Path | Location | Notes |
|------|----------|-------|
| `data/live/current_manifest.json` | Shared data volume | Written by handoff-publisher only |
| `data/staging/active_universe_{ts}.json` | Shared data volume | Written by universe-builder; read by handoff-publisher |
| `data/intelligence/` | Shared data volume | Intelligence layer outputs |
| `data/reference/` | Shared data volume | Reference data; updated weekly |
| `data/heartbeats/` | Shared data volume | All workers write heartbeats |
| `logs/` | Shared log volume or cloud log drain | Structured JSON |

### 4.2 Atomic Write Policy

Every worker that writes a JSON file must:

1. Write to a `.tmp` file in the same directory
2. Validate the `.tmp` file (schema + invariants)
3. On validation pass: `os.rename(tmp_path, final_path)` — atomic on POSIX
4. On validation fail: delete `.tmp` file; log error; do not overwrite existing valid file

No process reads a `.tmp` file. No process reads a partially written file.

### 4.3 Snapshot Retention Policy

| File type | Retention |
|-----------|-----------|
| `active_universe_{ts}.json` | 7 days; delete older |
| `technical_snapshot_{ts}.json` | 2 days |
| `catalyst_snapshot_{ts}.json` | 3 days |
| `provider_snapshot_{ts}.json` | 1 day |
| `advisory_runtime_log.jsonl` | 90 days; rotate at 100MB |
| `training_records.jsonl` | Permanent (core training data) |
| `event_log.jsonl` | Permanent (source of truth) |

### 4.4 Advisory Log Rotation

`data/intelligence/advisory_runtime_log.jsonl` rotated at 100MB or after 90 days.

Rotation: rename to `advisory_runtime_log_{date}.jsonl.gz`; new empty log created.

Observability worker monitors log size and triggers rotation.

### 4.5 Live Trading Bot Exclusions

The following files and modules must not be loaded by the live trading bot container (`decifer-live-bot`). Note: some of these may run in other production containers (e.g. `reference_data_builder.py` may run as a scheduled offline production worker — it must never be imported by the live bot).

| Excluded from live-bot container | Reason |
|----------------------------------|--------|
| `data/reference/provider_fetch_test_results.json` | Diagnostic only — not a runtime input |
| `data/reference/factor_registry.json` | Reference build output — not a runtime input |
| `data/reference/data_quality_report.json` | Advisory documentation only |
| `data/backtest/` | Offline research only |
| `provider_fetch_tester.py` | Diagnostic connectivity tool — not deployed to any runtime container |
| `factor_registry.py` | Reference build tool — not deployed to any runtime container |
| `backtest_intelligence.py` | Offline research — not deployed to any runtime container |
| `advisory_reporter.py` | Shadow pipeline — excluded from live-bot import path |
| `advisory_log_reviewer.py` | Shadow pipeline — offline evidence gate only |
| `reference_data_builder.py` | Runs as a separate scheduled offline worker; must never be imported by the live bot |

### 4.6 Secrets Policy

- All secrets loaded from environment variables injected at container start
- No API key values written to any JSON output file
- `env_values_logged = false` enforced in all worker outputs
- `secrets_exposed = false` enforced in all worker outputs
- `.env` files never committed to git or included in Docker images
- Secrets rotated out-of-band; rotation does not require code changes

### 4.7 Environment Variable Policy

| Variable | Worker(s) that need it |
|----------|----------------------|
| `ANTHROPIC_API_KEY` | `live_trading_bot` only |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | `provider_ingestion_worker`, `technical_market_sensor_worker`, `live_trading_bot` |
| `FMP_API_KEY` | `economic_intelligence_worker`, `company_quality_worker`, `catalyst_event_worker` |
| `ALPHA_VANTAGE_KEY` | `economic_intelligence_worker` (macro data, fallback) |
| `IBKR_PAPER_ACCOUNT` | `live_trading_bot`, `execution_risk_gateway` |
| `FRED_API_KEY` | `economic_intelligence_worker` (optional macro) |

Workers that do not need a secret must not receive it (principle of least privilege).

### 4.8 Healthcheck and Alerting Requirements

Every production-runtime process must:
1. Write a heartbeat to `data/heartbeats/{worker_name}.json` on each successful cycle
2. Include `last_success_at`, `status`, and `next_expected_at` in heartbeat
3. `observability_worker` checks all heartbeats and emits structured alerts for missing/expired heartbeats

Alert thresholds:
- `handoff_validator_publisher`: alert if no valid manifest in 20 minutes
- `live_trading_bot`: alert if no scan in 15 minutes
- `economic_intelligence_worker`: alert if context > 26 hours old
- IBKR disconnect: immediate alert

---

## 5. Recommended Initial Architecture

### 5.1 Phase 1 Recommendation

Start with **Docker Compose on one VM** or **supervisor/systemd on one machine**.

Rationale:
- Paper trading does not require high availability
- One VM is sufficient for current scan cadence
- File-based IPC is simple, observable, and debuggable
- Docker Compose provides process isolation without Kubernetes complexity

### 5.2 Key Design Choices

| Choice | Rationale |
|--------|-----------|
| Atomic file publication | Prevents partially-written JSON from being consumed |
| `current_manifest.json` as single live pointer | Live bot never searches; one file to check |
| Workers write to staging first | Handoff Publisher is the only gatekeeper to live path |
| Shared data volume | No network calls between workers in Phase 1 |
| Execution/Risk co-located with live bot in Phase 1 | IBKR TWS requires persistent local connection |

### 5.3 What Not to Build Yet

- Not: Kubernetes/EKS/GKE (complexity not justified for paper trading)
- Not: Kafka/Kinesis (file IPC is sufficient)
- Not: Managed DB (JSONL files are sufficient)
- Not: Separate execution gateway container in Phase 1 (co-locate with live bot)
