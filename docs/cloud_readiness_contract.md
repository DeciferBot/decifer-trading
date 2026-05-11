# Decifer Cloud Readiness Contract

**Branch:** `cloud/cloud-readiness-preparation`
**Status:** Readiness preparation only. No cloud migration has been performed.
**Version:** Decifer 4.0.1 (post-closure sprint)
**Date:** 2026-05-11

---

## Purpose

This document is the authoritative contract between the codebase as it exists today and any
future cloud deployment. It defines exactly what runs, what is required, what must be mounted,
and what the system expects before it can be considered cloud-ready.

This document does **not** authorise a cloud deployment. Refer to
`docs/future_cloud_deployment_runbook.md` for the draft execution sequence.

---

## 1. Production Entry Point

| Item | Value |
|------|-------|
| **Main process** | `bot.py` |
| **Runtime command** | `python3 bot.py` |
| **Python version** | 3.11 (pinned; 3.12+ untested) |
| **Working directory** | Repo root (config.py auto-detects via `__file__`) |
| **Dashboard port** | 8080 (configurable via `PORT` env var) |
| **Handoff publisher** | `python3 handoff_publisher.py --mode controlled_activation` |
| **Preflight check** | `python3 scripts/cloud_preflight.py` |
| **Health check** | `python3 scripts/healthcheck.py` |

---

## 2. Required Environment Variables

All secrets and configuration are injected as environment variables.
**No value is ever hard-coded in source.** Never print values; only check presence.

### Mandatory — bot will not start without these

| Variable | Purpose | Required by |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Apex LLM synthesizer (claude-sonnet-4-6) | `apex_orchestrator.py`, `market_intelligence.py` |
| `ALPACA_API_KEY` | Market data — real-time quotes, options chains | `alpaca_data.py`, `alpaca_stream.py`, `alpaca_options.py` |
| `ALPACA_SECRET_KEY` | Alpaca authentication | same as above |
| `ALPACA_BASE_URL` | Alpaca endpoint (`https://paper-api.alpaca.markets`) | same as above |
| `IBKR_PAPER_ACCOUNT` | Paper account ID for order routing | `config.py`, `orders_core.py`, `bot_ibkr.py` |
| `IBKR_ACTIVE_ACCOUNT` | Which account is active for this session | `config.py` |

### Strongly recommended — degraded operation without these

| Variable | Purpose | Fallback behaviour |
|----------|---------|-------------------|
| `FMP_API_KEY` | Fundamentals, earnings, analyst actions | FMP calls silently skipped |
| `ALPHA_VANTAGE_KEY` | Macro indicators, earnings calendar | AV calls silently skipped |
| `FRED_API_KEY` | Federal Reserve economic data | FRED calls silently skipped |

### Cloud deployment additions (deployment plumbing only)

| Variable | Purpose | Default |
|----------|---------|---------|
| `IBKR_HOST` | IBKR Gateway host | `127.0.0.1` |
| `IBKR_PORT` | IBKR Gateway port | `7496` (TWS/Gateway) |
| `PORT` | Dashboard HTTP port | `8080` |
| `HANDOFF_PUBLISHER_MODE` | Publisher safety mode | `validation_only` |
| `HANDOFF_PUBLISHER_INTERVAL_SECONDS` | Publisher loop cadence | `900` |

### Never required (no value needed)

| Variable | Note |
|----------|------|
| `DECIFER_REPO_PATH` | Auto-detected via `config.py __file__`; only needed if auto-detect fails |
| `IBKR_LIVE_1_ACCOUNT` | Reserved for future live trading; never populated in paper |
| `IBKR_LIVE_2_ACCOUNT` | Reserved for future live trading |
| `IBKR_FLEX_TOKEN` | Optional backfill; not required for normal operation |
| `IBKR_FLEX_QUERY_ID` | Optional |
| `IBKR_FLEX_TRADES_QUERY_ID` | Optional |

---

## 3. Required Data Directories

These directories **must exist and be writable** before the bot starts.
In a cloud deployment they must be on a persistent mounted volume.

| Directory | Created by | Purpose |
|-----------|-----------|---------|
| `data/` | bootstrap or volume | Root state directory |
| `data/live/` | `handoff_publisher.py` | Live manifests, publisher output |
| `data/heartbeats/` | `handoff_publisher.py` | Publisher health signals |
| `data/intelligence/` | `universe_builder.py`, `intelligence_engine.py` | Intelligence layer state |
| `data/universe_builder/` | `universe_builder.py` | Shadow universe files |
| `data/reference/` | Reference data builders (offline) | Static lookup tables |
| `data/runtime/` | `scripts/cloud_preflight.py` | Preflight/runtime reports |
| `logs/` | `bot.py` | Application logs |

---

## 4. Persistent Files (must survive container restarts)

These files contain irreplaceable state. They **must** be on the persistent volume.

| File | Written by | Why persistent |
|------|-----------|---------------|
| `data/training_records.jsonl` | `training_store.py` | ML training corpus — core product output |
| `data/trade_events.jsonl` | `event_log.py` | Write-ahead log — source of truth |
| `data/trades.json` | `orders_state.py` | Active and closed position state |
| `data/ic_weights.json` | `ic_calculator.py` | IC-weighted signal dimension scores |
| `data/ic_validation_result.json` | `ic_validator.py` | Phase gate validation record |
| `data/equity_history.json` | `bot_state.py` | NAV history for performance tracking |
| `data/capital_base.json` | `bot_state.py` | Starting portfolio value reference |
| `data/committed_universe.json` | `universe_committed.py` | Weekly top-1000 trading universe |
| `data/intelligence/transmission_rules.json` | Manual / intelligence workers | Macro rule definitions |
| `data/intelligence/theme_taxonomy.json` | Manual / intelligence workers | Theme definitions |
| `data/intelligence/thematic_roster.json` | Manual / intelligence workers | Symbol rosters per theme |
| `data/intelligence/economic_candidate_feed.json` | `intelligence_engine.py` | Latest intelligence candidates |
| `data/universe_builder/active_opportunity_universe_shadow.json` | `universe_builder.py` | Shadow universe for handoff |

---

## 5. Generated Runtime Files (safe to lose on restart, regenerated)

| File | Regenerated by |
|------|---------------|
| `data/live/current_manifest.json` | `handoff_publisher.py` on next cycle |
| `data/live/active_opportunity_universe.json` | `handoff_publisher.py` on next cycle |
| `data/live/handoff_publisher_report.json` | `handoff_publisher.py` on next cycle |
| `data/heartbeats/handoff_publisher.json` | `handoff_publisher.py` on next cycle |
| `data/intelligence/daily_economic_state.json` | `intelligence_engine.py` on next run |
| `data/intelligence/current_economic_context.json` | `intelligence_engine.py` on next run |
| `data/runtime/cloud_preflight_report.json` | `scripts/cloud_preflight.py` on next run |

---

## 6. Log Files

| File | Rotation | Managed by |
|------|---------|-----------|
| `logs/decifer.log` | Python `RotatingFileHandler` — 5 MB × 5 backups | `bot.py` |
| `logs/handoff_publisher.log` | Shell redirection; rotate manually | `handoff_publisher.py` |

JSONL append-only logs in `data/` are rotated at configurable size thresholds
via `utils/log_rotation.py` → `rotate_jsonl_if_needed()`.

Key JSONL logs with rotation:
- `data/apex_shadow_log.jsonl` (rotated by `apex_orchestrator.py`)
- `data/audit_log.jsonl`
- `data/signals_log.jsonl`
- `data/intelligence/advisory_runtime_log.jsonl` (90-day / 100 MB retention)

---

## 7. Mounted Volumes (cloud deployment)

| Host path | Container path | Mode | Notes |
|-----------|---------------|------|-------|
| `./data` | `/app/data` | read-write | All persistent state |
| `./logs` | `/app/logs` | read-write | Application logs |
| `./chief-decifer/state` | `/app/chief-decifer/state` | read-write | Chief Decifer panels (optional) |

---

## 8. Cloud-Excluded Directories

These directories must **never** be copied into a production runtime image.

| Directory | Reason |
|-----------|--------|
| `archive/` | Retired modules; not part of active runtime |
| `tests/` | Test suite; not part of production runtime |
| `tests/archive/` | Archived sprint tests |
| `backtest_results/` | Offline research data |
| `data/intelligence/backtest/` | Backtesting fixtures; not needed at runtime |
| `Chief-Decifer-recovered/` | Development recovery artefacts |
| `graphify-out/` | Local knowledge graph; not runtime |
| `.claude/` | Claude Code internal state |
| `*.app/`, `*.command` | macOS app bundles; not valid in Linux container |

---

## 9. Startup Expectations

The following must be true **before** `bot.py` is started:

1. IBKR Gateway or TWS is running and accepting connections on `IBKR_HOST:IBKR_PORT`.
2. All mandatory environment variables are set.
3. `data/live/current_manifest.json` exists and is not expired (handoff_publisher ran at least once).
4. `data/committed_universe.json` exists (weekly universe is populated).
5. `data/intelligence/` files are populated (intelligence workers have run at least once).
6. `scripts/cloud_preflight.py` exits 0.

If `data/live/current_manifest.json` is missing or expired, the bot falls back to scanner path (fail-closed). This is safe but loses handoff benefit.

---

## 10. Shutdown Expectations

1. `bot.py` handles `SIGTERM` — completes the in-flight scan cycle then exits.
2. Allow up to 60 seconds for graceful shutdown before `SIGKILL`.
3. Any in-flight position that was being sized/placed may be left in `EXITING` state.
4. Bot self-recovers stuck `EXITING` positions on next startup via reconciliation.
5. Do **not** delete `data/trades.json` or `data/trade_events.jsonl` during shutdown.

---

## 11. Fail-Closed Expectations

| Component | Fail-closed behaviour |
|-----------|----------------------|
| Handoff reader | If manifest missing, expired, or `handoff_enabled=false` → returns empty universe, bot uses scanner path |
| Handoff publisher | On validation failure → writes `.fail_*.json` diagnostic, does NOT overwrite valid output |
| Apex call | On LLM timeout/error → no new entries for that cycle; existing positions unaffected |
| IBKR disconnect | Bot attempts reconnect (max 10 attempts, exponential backoff) then halts |
| Missing env var | Bot logs warning; affected data source silently skipped |

---

## 12. Health Check Expectations

| Check | Method | Alert threshold |
|-------|--------|----------------|
| Process alive | `docker ps` / systemd status | Any exit |
| Publisher heartbeat | `data/heartbeats/handoff_publisher.json → last_success_ts` | > 1800 s (30 min) |
| Manifest freshness | `data/live/current_manifest.json → expires_at` | Expired |
| Publisher fail-closed | `handoff_publisher.json → fail_closed_reason` | Non-null |
| Fail files | `ls data/live/.fail_*.json` | Any new file in last cycle |
| IBKR connectivity | Bot log: `IB connected` | Absent > 5 min post-start |
| Dashboard | `GET http://localhost:8080/` | Non-200 |

Lightweight health check: `python3 scripts/healthcheck.py`
Full preflight check: `python3 scripts/cloud_preflight.py`

---

## 13. Known Blockers Before Cloud Migration

These issues must be resolved before any actual cloud migration is performed:

| # | Blocker | Severity | Notes |
|---|---------|----------|-------|
| 1 | IBKR Gateway cloud access model undefined | **Critical** | IBKR does not have a public API; Gateway requires desktop login. See `docs/ibkr_gateway_future_cloud_design.md`. |
| 2 | IBKR Gateway restart is manual | **High** | IB Gateway requires a GUI session to authenticate. `ibcontroller` or `IBC` automation is not yet set up. |
| 3 | No cloud provider selected | **High** | AWS, GCP, Hetzner, DigitalOcean — not yet evaluated. |
| 4 | TA-Lib C library requires source build | **Medium** | `libta-lib0` may be unavailable on some cloud base images. Dockerfile builds from source. |
| 5 | `data/` bind-mount is git-tracked locally | **Medium** | In cloud, volume is detached from git. A bootstrap procedure is needed on first deploy. |
| 6 | No secrets manager integration | **Medium** | Currently uses `.env` file. Cloud requires secrets manager (AWS SSM, GCP Secret Manager, etc.). |
| 7 | No automated IBKR reconnect on Gateway restart | **Medium** | Bot reconnects after disconnect, but Gateway must already be running before bot starts. |
| 8 | Trailing stop test failures (2 pre-existing) | **Low** | Pre-existing test failures in test suite; unrelated to cloud packaging. |
| 9 | No cloud monitoring/alerting wired | **Low** | Chief Decifer dashboard is local-only. Cloud needs separate alerting. |
| 10 | Market-hours downstream scoring proof pending | **Low** | Activation proof established but full market-hours test not yet completed. |
