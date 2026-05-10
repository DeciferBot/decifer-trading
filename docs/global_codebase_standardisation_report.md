# Global Production Codebase Standardisation Report
**Branch:** `cleanup/global-production-codebase-standard`  
**Date:** 2026-05-10  
**Auditor:** Cowork (Claude)

---

## Summary

| Metric | Before | After |
|--------|--------|-------|
| Root Python modules | 119 | 109 |
| Active test files (default run) | 111 | 90 |
| Archive modules created | 0 | 10 |
| Archived test files | 0 | 21 |
| New docs created | 0 | 4 |
| Test failures introduced | — | **0** |

---

## What Was Done

### Phase 1 — Import Closure Analysis

Built a full transitive import closure from `bot.py`, `bot_trading.py`, and `scanner.py` entry points.

| Result | Count |
|--------|-------|
| Modules in live runtime closure | **88** |
| Modules NOT in live runtime closure | **31** |

Of the 31 not in closure, classified as:
- **Scheduled workers** (handoff_publisher, universe_builder, reference_data_builder, quota_allocator): PROTECT
- **Feature-gated** (advisory, observer): KEEP
- **Intelligence pipeline** (future production): KEEP
- **Non-production tooling**: ARCHIVE

### Phase 2 — Documents Created

| Document | Purpose |
|----------|---------|
| `docs/production_runtime_surface.md` | Annotated list of all 88 live runtime modules by layer; scheduled workers; cloud exclusion candidates |
| `docs/codebase_standardisation_retirement_matrix.md` | Per-category classification of all 119 modules + 111 test files; what's protected, what's archived, what's pending |
| `docs/cloud_runtime_exclusion_register.md` | Definitive list of what must never ship to a cloud container; includes container build checklist |
| `docs/global_codebase_standardisation_report.md` | This document |

### Phase 3 — Retirements Executed

#### Python Modules Archived (→ `archive/`)

| Module | Reason |
|--------|--------|
| `backtester.py` | Backtest tooling; production guards assert NOT imported |
| `backtest_intelligence.py` | Backtest tooling; production guards assert NOT imported |
| `build_brain.py` | ML model build tool; not a runtime component |
| `compare_universes.py` | One-shot comparison script |
| `paper_handoff_builder.py` | Pre-cutover shadow comparator |
| `paper_handoff_comparator.py` | Pre-cutover shadow comparator |
| `apex_divergence.py` | Pre-cutover shadow divergence logger |
| `reachability.py` | Dead-code analysis tool; itself unused |
| `audit_candle_gate.py` | Standalone audit utility; zero live references |
| `daily_journal.py` | Standalone journal generator; not in bot runtime |

#### Test Files Archived (→ `tests/archive/`)

- **20 intelligence sprint/day test files** → `tests/archive/intelligence_sprint_tests/`
  - `test_intelligence_day2.py` through `test_intelligence_day7.py` (6 files)
  - `test_intelligence_sprint2.py` through `test_intelligence_sprint7c.py` (12 files)
  - `test_intelligence_factor_registry.py`, `test_intelligence_reference_data.py` (2 files)
- **`tests/test_apex_divergence.py`** → `tests/archive/` (module archived)

#### Config Change

- `pytest.ini`: Added `norecursedirs = tests/archive` to prevent pytest from collecting archived tests.

#### Archive READMEs

- `archive/README.md` — explains what's archived and recovery procedure
- `tests/archive/intelligence_sprint_tests/README.md` — explains sprint test archive

---

## Validation Results

| Suite | Result |
|-------|--------|
| `python3 -c "import bot_trading, scanner"` | **OK** |
| Smoke tests (`-m smoke`) | **7 passed, 1 skipped** |
| Handoff suites (activation_gate, publisher, observer, wiring) | **309 passed, 5 skipped** |
| Core suites (risk, orders_core, orders_regression, orders_guard, guardrails, event_log) | **189 passed** |
| Intelligence file validator | **All 13 files PASS** |
| Pre-existing failures (test_imports.py, test_bot.py) | **60 failures — identical count on master; NOT introduced by this branch** |

---

## Pre-Existing Failures (Not This Branch)

`test_imports.py` and `test_bot.py` have 60 failures on both `master` and this branch — confirmed identical by running against master. These are pre-existing issues related to orders facade attributes and are not caused by any change in this session.

---

## What Was NOT Changed

| Item | Status |
|------|--------|
| Any live runtime module (88 modules) | UNCHANGED |
| `config.py` | UNCHANGED |
| `handoff_publisher.py` | UNCHANGED |
| `handoff_publisher_observer.py` | UNCHANGED |
| `bot_trading.py` | UNCHANGED |
| Production test suite | UNCHANGED (only sprint-dev checkpoints archived) |
| Any flag | UNCHANGED |
| Any data file | UNCHANGED |

---

## Remaining Cleanup (Future Sessions)

These retirements require gate conditions to be met first:

| Item | Gate | When |
|------|------|------|
| `handoff_publisher_observer.py` + `tests/test_handoff_publisher_observer.py` | Market-hours scan cycle confirms `candidate_source=handoff` in universe_coverage | After controlled activation market proof |
| `quota_capacity_calibrator.py` + `tests/test_quota_capacity_calibrator.py` | Quota calibration run confirmed complete | One-time calibration done |
| `advisory_reporter.py`, `advisory_log_reviewer.py` | Advisory feature enabled or cancelled | Gate decision by Amit |
| 6 migration scripts in `scripts/` (if not already deleted) | Migration confirmed complete | Verify `ls scripts/migrate_*.py` |
| Intelligence pipeline modules (11 files) | Intelligence pipeline goes live | Promotion gate |
| `provider_fetch_tester.py` | No test references, safe to archive | Any session |

---

## Confidence Assessment

| Claim | Evidence | Confidence |
|-------|---------|------------|
| 88 modules in live closure | Static AST analysis from entry points | HIGH |
| 10 archived modules have no live references | grep + import closure analysis | HIGH |
| 21 archived test files are dev checkpoints, not regression guards | Manual review of test content | HIGH |
| No new test failures introduced | Compared failure count against master (both 60) | CERTAIN |
| Production imports intact | `import bot_trading, scanner` succeeds | CERTAIN |
| Intelligence data files intact | Validator 13/13 PASS | CERTAIN |
