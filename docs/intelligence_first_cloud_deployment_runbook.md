# Intelligence-First Cloud Deployment Runbook

**Sprint:** 7H.1 — Operations readiness
**Status:** Pre-activation runbook. Activation flag is False. Do not execute Section 7 until Amit approves controlled activation sprint.
**Classification:** Advisory/design document. No production code changed.
**Reference:** See `docs/intelligence_first_cloud_process_map.md` for Phase 1/2/3 architecture. This runbook covers operational steps and runtime configuration.

---

## 1. Recommended Phase 1 Deployment Model

Phase 1: single VM or cloud instance, Docker Compose or supervisor/systemd. All processes share a single working directory and volume. No container isolation yet.

**Instance requirements:**
- 2 vCPU / 4 GB RAM minimum
- 20 GB persistent storage (data/, logs/, heartbeats/)
- Python 3.11 runtime
- IBKR TWS/Gateway accessible (local or via internal network — execution endpoint only)
- Outbound HTTPS for Alpaca, FMP, Alpha Vantage, Anthropic APIs

**Working directory:** `/opt/decifer-trading/` (or equivalent on the target instance)

**Shared data volume:** `/opt/decifer-trading/data/` — all workers read and write here via relative paths. Do not use absolute paths in code; `config.py` auto-detects repo root via `__file__`.

**Logs directory:** `/opt/decifer-trading/data/logs/` (rotate; do not commit to repo)

**Heartbeats directory:** `/opt/decifer-trading/data/heartbeats/` (committed to repo during validation phase; rotate in production)

**Diagnostics directory:** `/opt/decifer-trading/data/live/` — `.fail_*.json` files land here. Rotate before unbounded growth.

### Startup Order

1. Ensure `.env` (or cloud secret injection) is loaded
2. Start IBKR Gateway / TWS (external; not managed by Docker Compose)
3. Start `handoff_publisher` (scheduled worker) — produces `data/live/` outputs
4. Start `handoff_publisher_observer` (validation worker) — reads publisher outputs
5. Start `live_trading_bot` — reads `data/live/` when flag is True; otherwise runs scanner path
6. Optional intelligence workers start after live bot is stable

### Shutdown Order

1. Stop `live_trading_bot` first (wait for any in-flight scan cycle to complete)
2. Stop `handoff_publisher` and `handoff_publisher_observer`
3. Stop optional workers
4. Leave IBKR Gateway running if open positions exist that need overnight management

### Restart Policy

| Process | Restart policy | Notes |
|---------|---------------|-------|
| `live_trading_bot` | On failure, max 3 attempts with 30s delay | Alert on restart |
| `handoff_publisher` | On failure, max 3 attempts with 60s delay | Manifest staleness will be detected by observer |
| `handoff_publisher_observer` | On failure, retry | Observer failure does not block live bot |
| Optional workers | On failure, retry | Non-critical for live bot operation |

---

## 2. Process List

| Process | Script | Schedule | Classification |
|---------|--------|----------|----------------|
| `handoff_publisher` | `handoff_publisher.py` | Every 15 minutes (before manifest expires) | Production runtime candidate |
| `handoff_publisher_observer` | `handoff_publisher_observer.py` | After each publisher run, or every 15 minutes | Advisory/shadow-only |
| `live_trading_bot` | `bot_trading.py` | Continuous (scans every N minutes per config) | Production runtime |
| *(future)* Economic intelligence worker | `economic_intelligence_worker.py` | Hourly or on EDGAR event | Production runtime (future) |
| *(future)* Technical market sensor | `market_sensor_worker.py` | Every 5–15 minutes | Production runtime (future) |
| *(future)* Provider ingestion workers | per-provider | Scheduled | Advisory/adapter (future) |
| IBKR Gateway | External process | Persistent | External dependency |

**Current Phase 1 minimum:** `handoff_publisher` + `live_trading_bot`. Observer is strongly recommended but not load-bearing.

---

## 3. Required Runtime Directories

| Directory | Contents | Created by | Notes |
|-----------|----------|-----------|-------|
| `data/live/` | `current_manifest.json`, `active_opportunity_universe.json`, `handoff_publisher_report.json`, `.fail_*.json`, `publisher_run_log.jsonl`, observation report | `handoff_publisher.py` | Live bot reads `current_manifest.json` when flag True |
| `data/heartbeats/` | `handoff_publisher.json` | `handoff_publisher.py` | Latest-run health only; rotate in production |
| `data/reference/` | `sector_schema.json`, `symbol_master.json`, `theme_overlay_map.json`, `factor_registry.json`, etc. | Reference data builders (offline) | Read-only at runtime |
| `data/intelligence/` | `economic_candidate_feed.json`, `advisory_runtime_log.jsonl`, `advisory_log_review.json`, shadow universe | Intelligence workers | Read-only for live bot |
| `data/universe_builder/` | `active_opportunity_universe_shadow.json`, `universe_builder_report.json`, comparison reports | `universe_builder.py` | Publisher reads shadow universe from here |
| `data/logs/` | Bot logs, scan logs | `bot_trading.py` | Rotate; never commit |
| `data/live/snapshots/` | *(future)* Per-run universe snapshots | `handoff_publisher.py` (future) | See snapshot archive design doc |
| `data/live/diagnostics/` | *(optional migration target)* `.fail_*.json` | `handoff_publisher.py` | Consider moving diagnostics here before cloud; avoids clutter in `data/live/` |

**Note:** All paths are relative to repo root. `config.py` auto-detects root. Do not hardcode absolute paths.

---

## 4. Secrets Policy

| Rule | Detail |
|------|--------|
| Secrets from environment only | `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `FMP_API_KEY`, `ALPHA_VANTAGE_KEY`, `IBKR_PAPER_ACCOUNT`, `IBKR_ACTIVE_ACCOUNT`, `FRED_API_KEY` — all via env vars |
| `.env` for local dev only | Not committed; not deployed to cloud |
| No secrets in JSON outputs | Publisher, observer, heartbeat, run log — all checked by validators for `secrets_exposed=false` |
| No secrets in logs | Validated by `env_values_logged=false` safety flag |
| No API keys committed | Git history audited; `.gitignore` must cover `.env` |
| Cloud secrets manager preferred | Use AWS Secrets Manager, GCP Secret Manager, or equivalent; inject as environment variables at container start |
| Rotation | API keys rotate on provider schedule; re-inject without code deploy |

---

## 5. IBKR Gateway Operating Model

| Aspect | Definition |
|--------|-----------|
| Where it runs | Local machine (development) or cloud VM with VPN / private networking to IBKR (production) |
| Port | TWS Paper: 7497; Gateway Paper: 4002 |
| Restart procedure | Manual restart of IB Gateway app or `ibcontroller`/`ib-gateway-docker`; bot reconnects automatically on restart |
| Connection assumptions | Bot connects via `ibkr_connection.py`; connection checked at startup; reconnect on disconnect |
| Read-only market data | Historical bars, quotes — safe from any process |
| Trading endpoints | `placeOrder`, `cancelOrder`, account queries — **live bot only**; never called from intelligence workers, publisher, observer, or any advisory process |
| Execution/risk boundary | `orders_core.py`, `bot_ibkr.py` — never imported by intelligence workers (AST-verified in tests) |
| What intelligence workers must not call | Any IBKR order submission endpoint; any account balance query that would affect real positions |

---

## 6. Healthchecks and Alerts

| Check | Method | Alert threshold | Action |
|-------|--------|----------------|--------|
| `current_manifest.json` freshness | Read `manifest_age_seconds` from observer report | > 900s (stale SLA) | Alert: publisher may be down |
| `active_opportunity_universe.json` freshness | Same | > 900s | Alert: publisher may be down |
| `handoff_publisher.json` heartbeat | Read `last_success_age_seconds` | > 900s | Alert: publisher last success was stale |
| `publisher_run_log.jsonl` growth | Line count increasing each cycle | No new line in 20 min | Alert: publisher stopped appending |
| `.fail_*.json` count | Count files in `data/live/` matching `.fail_*.json` | Any new file since last check | Alert: publisher cycle failed |
| Observer gate | Read `readiness_gate` from observation report | Not `validation_only_stable` after threshold met | Alert: gate regressed |
| Validator | Exit code of `validate_intelligence_files.py` | Non-zero | Alert: intelligence files corrupted |
| Smoke | Exit code of `pytest -m smoke -q` | Non-zero | Alert: core tests failing |
| IBKR connectivity | Bot log: `IB connected` | No connection event in > 5 min post-start | Alert: IBKR gateway unreachable |
| `fail_closed_reason` in heartbeat | `handoff_publisher.json:fail_closed_reason` | Non-null | Alert: publisher fail-closed |

**Minimum viable alert set for Phase 1:** manifest freshness + publisher heartbeat + `fail_closed_reason` + IBKR connectivity.

---

## 7. Rollback at Infrastructure Level

This section documents infrastructure-level rollback only. See `docs/intelligence_first_activation_rollback_playbook.md` for the full rollback procedure.

| Step | Command / Action |
|------|-----------------|
| 1 | Set `enable_active_opportunity_universe_handoff = False` in `config.py` |
| 2 | If bot does not reload config dynamically, restart: `supervisorctl restart live_trading_bot` or `docker-compose restart bot` |
| 3 | Verify in bot log: `Building dynamic universe (Alpaca screening)...` |
| 4 | Verify absence of `[handoff_wiring] flag_state=True` in post-restart logs |
| 5 | Keep publisher running (it continues to publish; bot just ignores manifest) |
| 6 | Keep `data/live/` files intact — do not delete |
| 7 | Preserve `publisher_run_log.jsonl` and `.fail_*.json` diagnostics |
| 8 | Log rollback reason in session log |
