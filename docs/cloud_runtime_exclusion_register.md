# Cloud Runtime Exclusion Register
**Branch:** `cleanup/global-production-codebase-standard`  
**Date:** 2026-05-10

Defines what must NEVER be included in a cloud container running the live Decifer bot. Updated when modules are added or retired.

---

## Hard Exclusions — Never Ship to Cloud

### Archived Modules (`archive/`)

| Path | Reason |
|------|--------|
| `archive/backtester.py` | Backtest tooling, no production use |
| `archive/backtest_intelligence.py` | Backtest tooling, no production use |
| `archive/build_brain.py` | ML model training tool |
| `archive/compare_universes.py` | One-shot comparison script |
| `archive/paper_handoff_builder.py` | Pre-cutover shadow artefact |
| `archive/paper_handoff_comparator.py` | Pre-cutover shadow artefact |
| `archive/apex_divergence.py` | Pre-cutover shadow artefact |
| `archive/reachability.py` | Dev analysis tool |
| `archive/audit_candle_gate.py` | Dev audit utility |
| `archive/daily_journal.py` | Standalone journal tool |

### Scripts — Migration / Backfill (if not already deleted)

| Path | Reason |
|------|--------|
| `scripts/migrate_trades_to_training_store.py` | Migration complete — run once only |
| `scripts/backfill_may5_trades.py` | Migration complete |
| `scripts/backfill_position_closed.py` | Migration complete |
| `scripts/reconcile_trades_json.py` | Migration complete |
| `scripts/recover_pattern_ids.py` | Migration complete |
| `scripts/rebuild_positions_from_intents.py` | Migration complete |

### Scripts — Dev / Analysis Tools

| Path | Reason |
|------|--------|
| `scripts/apex_shadow_report.py` | Dev report tool |
| `scripts/apex_flip_proposer.py` | Dev analysis |
| `scripts/pru_ab_comparison.py` | Dev comparison |
| `scripts/factor_analysis.py` | Research tool |
| `scripts/phase1_session_report.py` | Dev report |
| `scripts/store-secrets.sh` | Secret management — never in container |
| `scripts/icloud-sync.sh` | iCloud sync — local only |
| `provider_fetch_tester.py` | Debug tool, explicitly excluded by wiring tests |

### Chief Decifer Service

| Path | Reason |
|------|--------|
| `Chief-Decifer-recovered/` | Separate monitoring service, different Python requirements |
| `chief-decifer/` | State directory for Chief service |

### Test Suite

| Path | Reason |
|------|--------|
| `tests/` (entire directory) | Never ship tests to production container |
| `tests/archive/` | Archived sprint tests |

### Backtest / Research Notebooks

| Path | Reason |
|------|--------|
| `ml_engine.py` | Lazy-imported for availability check only; exclude if cloud bot has no model |
| `scripts/build_brain.py` (if exists) | Training only |

---

## Conditional Exclusions — Ship Only If Feature Active

| Module | Include When | Exclude When |
|--------|-------------|-------------|
| `advisory_reporter.py` | `intelligence_first_advisory_enabled: True` | Flag OFF (current state) |
| `advisory_log_reviewer.py` | `intelligence_first_advisory_enabled: True` | Flag OFF (current state) |
| `handoff_publisher_observer.py` | Shadow observation needed | Shadow mode retired |
| `quota_capacity_calibrator.py` | Quota calibration run needed | Post-calibration |
| `intelligence_engine.py` and 10 related modules | Intelligence pipeline promoted to production | Before promotion gate met |

---

## Required in Cloud Container (Minimum Production Set)

The **88 live runtime modules** listed in `docs/production_runtime_surface.md` plus:

| Addition | Reason |
|----------|--------|
| `handoff_publisher.py` | Required for two-key activation if running on cron |
| `quota_allocator.py` | Required by handoff_publisher |
| `universe_builder.py` | Required for weekly universe refresh cron |
| `reference_data_builder.py` | Required for intelligence data cron |
| `scripts/setup.sh` | Environment setup |
| `scripts/validate_intelligence_files.py` | Schema validation |
| `scripts/verify_standalone_workers.sh` | Worker health check |
| `scripts/cancel_orphan_orders.py` | Orphan order cleanup |
| `config.py` | Runtime configuration (in live closure) |
| `.env` | **Secrets — inject via secret manager, never bake into image** |

---

## Container Build Checklist

Before building a cloud image:
- [ ] `archive/` directory excluded from `COPY` in Dockerfile
- [ ] `tests/` directory excluded
- [ ] `Chief-Decifer-recovered/` excluded (or built as a separate image)
- [ ] `scripts/store-secrets.sh` excluded
- [ ] `.env` NOT copied — secrets injected via environment variables at runtime
- [ ] `ml_engine.py` excluded if no trained model is present (prevents import check failure)
- [ ] `scripts/migrate_*.py` and `scripts/backfill_*.py` excluded (migration complete)
- [ ] Verify `python3 -c "import bot_trading, scanner"` succeeds with the container image
- [ ] Run smoke tests against the image: `python3 -m pytest -m smoke -q`

---

*Update this register whenever modules are added to or removed from the production set.*
