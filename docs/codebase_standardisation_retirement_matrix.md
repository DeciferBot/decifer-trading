# Codebase Standardisation Retirement Matrix
**Branch:** `cleanup/global-production-codebase-standard`  
**Date:** 2026-05-10  
**Scope:** 119 Python modules, 111 test files, 12 scripts

---

## Summary

| Category | Module Count | Test Files | Action |
|----------|-------------|-----------|--------|
| Live runtime (bot closure) | 88 | — | PROTECT |
| Scheduled workers (operational) | 4 | — | PROTECT |
| Feature-gated (flags OFF) | 4 | — | KEEP (future) |
| Intelligence pipeline (future) | 11 | — | KEEP (future) |
| Non-production tooling | 10 | 1 | ARCHIVE this session |
| Advisory (gated) | 2 | — | KEEP (flagged off) |
| **Total classified** | **119** | — | — |

| Test Category | Count | Action |
|---------------|-------|--------|
| Permanent regression (keep) | ~70 | PROTECT |
| Intelligence sprint/day dev checkpoints | 20 | ARCHIVE this session |
| Apex divergence (module archived) | 1 | ARCHIVE this session |
| Remaining active tests | ~20 | PROTECT |

---

## Section A — Live Runtime Modules (88) — PROTECT ALL

These are in the transitive import closure of `bot.py`, `bot_trading.py`, `scanner.py`. See `docs/production_runtime_surface.md` for the full annotated list.

**Critical protection rule:** No deletion, renaming, or signature change to any of these without tracing the full call chain.

Modules include: `bot_trading`, `scanner`, `signal_pipeline`, `signal_dispatcher`, `entry_gate`, `phase_gate`, `apex_orchestrator`, `market_intelligence`, `orders_core`, `orders_portfolio`, `risk`, `risk_gates`, `event_log`, `training_store`, `alpaca_data`, `ibkr_reconciler`, `config`, and 71 others. Full list in `production_runtime_surface.md`.

---

## Section B — Scheduled Workers (4) — PROTECT

Not imported by the bot process but are operational pipeline components.

| Module | Role | Protected Because |
|--------|------|------------------|
| `handoff_publisher.py` | Publishes active opportunity universe handoff | Active two-key activation pipeline |
| `universe_builder.py` | Weekly committed universe refresh | Core data pipeline |
| `reference_data_builder.py` | Reference data builder | Required by intelligence pipeline |
| `quota_allocator.py` | Quota policy (dependency of handoff_publisher) | Active quota enforcement |

---

## Section C — Feature-Gated Modules (4) — KEEP

Disabled by config flags. Retain for when flags are enabled.

| Module | Config Flag | State | Action |
|--------|-------------|-------|--------|
| `advisory_reporter.py` | `intelligence_first_advisory_enabled: False` | OFF | KEEP |
| `advisory_log_reviewer.py` | `intelligence_first_advisory_enabled: False` | OFF | KEEP |
| `handoff_publisher_observer.py` | Shadow observation mode | Shadow only | KEEP (active test suite) |
| `quota_capacity_calibrator.py` | One-time calibration | Not scheduled | KEEP |

---

## Section D — Intelligence Pipeline (11) — KEEP (Future Production)

Not yet active in production but gate conditions may be met soon. Retain in place.

| Module | Role |
|--------|------|
| `intelligence_engine.py` | ML intelligence engine |
| `intelligence_adapters.py` | Intelligence data adapters |
| `factor_registry.py` | Alpha factor registry |
| `theme_activation_engine.py` | Theme activation logic |
| `thesis_store.py` | Thesis persistence |
| `candidate_resolver.py` | Intelligence candidate resolution |
| `macro_transmission_matrix.py` | Macro→sector transmission model |
| `iv_skew.py` | IV skew signal dimension |
| `alpha_validation.py` | Alpha signal validation |
| `ic_validator.py` | IC validation pipeline |
| `intelligence_schema_validator.py` | Intelligence schema validation |

---

## Section E — Non-Production Tooling Archived This Session

Moved to `archive/` (Python modules) and `tests/archive/` (test files). These had no references in the permanent test suite.

### Python Modules → `archive/`

| Module | Reason | Test File Archived With It |
|--------|--------|---------------------------|
| `backtester.py` | Backtest only; production guards assert NOT imported | — |
| `backtest_intelligence.py` | Backtest only; production guards assert NOT imported | — |
| `build_brain.py` | ML model build tool; not a runtime component | — |
| `compare_universes.py` | One-shot comparison script | — |
| `paper_handoff_builder.py` | Pre-cutover shadow comparator; no production role | — |
| `paper_handoff_comparator.py` | Pre-cutover shadow comparator; no production role | — |
| `apex_divergence.py` | Pre-cutover shadow divergence logger; USE_APEX_V3_SHADOW pattern retired | `tests/test_apex_divergence.py` |
| `reachability.py` | Dead-code analysis tool; itself unused in production | — |
| `audit_candle_gate.py` | Standalone audit utility; zero live references | — |
| `daily_journal.py` | Standalone journal generator; not in bot runtime | — |

### Test Files → `tests/archive/intelligence_sprint_tests/`

Sprint-phase development checkpoints. These tested in-progress intelligence pipeline feature work, not permanent production invariants.

| File | Sprint Phase |
|------|-------------|
| `test_intelligence_day2.py` | Sprint 2 day-by-day |
| `test_intelligence_day3.py` | Sprint 3 |
| `test_intelligence_day4.py` | Sprint 4 |
| `test_intelligence_day5.py` | Sprint 5 |
| `test_intelligence_day6.py` | Sprint 6 |
| `test_intelligence_day7.py` | Sprint 7 |
| `test_intelligence_sprint2.py` | Sprint 2 |
| `test_intelligence_sprint3.py` | Sprint 3 |
| `test_intelligence_sprint4a.py` | Sprint 4a |
| `test_intelligence_sprint4b.py` | Sprint 4b |
| `test_intelligence_sprint5a.py` | Sprint 5a |
| `test_intelligence_sprint5b.py` | Sprint 5b |
| `test_intelligence_sprint6a.py` | Sprint 6a |
| `test_intelligence_sprint6b.py` | Sprint 6b |
| `test_intelligence_sprint6c.py` | Sprint 6c |
| `test_intelligence_sprint7a2.py` | Sprint 7a |
| `test_intelligence_sprint7b.py` | Sprint 7b |
| `test_intelligence_sprint7c.py` | Sprint 7c |
| `test_intelligence_factor_registry.py` | Factor registry dev checkpoint |
| `test_intelligence_reference_data.py` | Reference data dev checkpoint |

Also archived with corresponding module:
| File | Reason |
|------|--------|
| `test_apex_divergence.py` | Module `apex_divergence.py` archived |

---

## Section F — Permanent Regression Tests — PROTECT

Tests that guard production invariants. Must never be archived or deleted.

| File | What It Guards |
|------|----------------|
| `test_orders_core.py` | Core order logic |
| `test_orders_regression.py` | Order regression suite |
| `test_orders_guard.py` | Order pre-flight guard |
| `test_orders_execute.py` | Order execution paths (has real assertions; also has TODO stubs) |
| `test_risk.py` | Risk engine |
| `test_event_log_and_training_store.py` | JSONL persistence invariants |
| `test_handoff_wiring_integration.py` | Handoff wiring + production import guards |
| `test_handoff_activation_gate.py` | Two-key activation gate |
| `test_handoff_publisher.py` | Publisher contract |
| `test_handoff_publisher_observer.py` | Observer shadow contract |
| `test_apex_live_execute_path.py` | Apex live execution path |
| `test_apex_migration_guards.py` | Decifer 3.0 migration complete (reads source as text) |
| `test_apex_prompt_architecture.py` | Apex prompt structure |
| `test_pass1_robustness.py` | Scan pass robustness |
| `test_entry_gate.py` | Entry gate |
| `test_phase_gate.py` | Phase gate |
| `test_guardrails.py` | Hard safety limits |
| `test_config.py` | Config validation |
| `test_imports.py` | Import health check |
| `test_scanner.py` | Scanner |
| `test_bot.py` | Bot integration |
| `test_signal_pipeline.py` | Signal scoring |
| `test_signal_dispatcher.py` | Signal dispatch |
| `test_universe_committed.py` | Committed universe |
| (and all remaining test files not listed in Section E) | Production guards |

---

## Section G — Scripts Audit

| Script | Classification | Action |
|--------|---------------|--------|
| `scripts/validate_intelligence_files.py` | Active validator | KEEP |
| `scripts/tier_d_evidence_report.py` | Active analysis | KEEP |
| `scripts/reachability_report.py` (if exists) | Analysis tool | KEEP |
| `scripts/migrate_trades_to_training_store.py` | Migration complete | Could archive |
| `scripts/backfill_may5_trades.py` | Migration complete | Could archive |
| `scripts/backfill_position_closed.py` | Migration complete | Could archive |
| `scripts/reconcile_trades_json.py` | Migration complete | Could archive |
| `scripts/recover_pattern_ids.py` | Migration complete | Could archive |
| `scripts/rebuild_positions_from_intents.py` | Migration complete | Could archive |

Migration scripts (6) were already deleted in cleanup Step 1 (2026-05-08). Verify `ls scripts/migrate_*.py scripts/backfill_*.py` before any further action.

---

## Section H — Cleanup Sequence (Post-Activation Gates)

These retirements should happen only after the corresponding activation gate is met:

1. **After handoff cutover is proven at market hours:** Archive `handoff_publisher_observer.py` + `tests/test_handoff_publisher_observer.py` (shadow observation pre-cutover)
2. **After intelligence pipeline goes live:** Archive `alpha_validation.py`, `ic_validator.py`, `intelligence_schema_validator.py` if superseded
3. **After advisory is enabled OR cancelled:** Archive `advisory_reporter.py`, `advisory_log_reviewer.py`
4. **After quota calibration is complete:** Archive `quota_capacity_calibrator.py` + `tests/test_quota_capacity_calibrator.py`

---

## Section I — Do Not Touch

| Item | Reason |
|------|--------|
| All 88 live runtime modules | Production code |
| `config.py` | No flag changes without Amit approval |
| `bot_trading.py` | Core trading engine |
| `scanner.py` | Universe scanner |
| `risk.py`, `orders_core.py` | Risk and order primitives |
| `event_log.py`, `training_store.py` | Persistence invariants (metadata immutability) |
| All `data/live/` manifest files | Live handoff state |
| `chief-decifer/state/` | Chief state paths (sacred) |
| `chief-decifer/` | Separate service |

---

*This matrix is the source of truth for this session's retirement decisions. See `docs/production_runtime_surface.md` for the full live module list.*
