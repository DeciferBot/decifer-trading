# Intelligence-First Production Simplification Audit

**Created:** 2026-05-06
**Purpose:** Classify every file, function, and test added or modified during the Intelligence-First architecture work. Enforce the north star: one production system with clean service boundaries, no zombie tests, no duplicate runtime logic.

**North Star:**
A production-ready, cloud-hostable trading system with clean service boundaries, minimal duplicate logic, clear runtime contracts, observable logs, safe fail-closed behaviour and no zombie tests.

**Classification vocabulary:**

| Label | Meaning |
|-------|---------|
| `production_runtime` | Required in production deployment |
| `advisory_only` | Read-only observer; never touches execution |
| `shadow_only` | Shadow pipeline; not wired to live decisions |
| `backtest_only` | Offline evaluation; excluded from runtime |
| `adapter_only` | Read-only source adapter; no side effects |
| `schema_validator` | Validation tooling; production-useful for CI |
| `migration_tooling` | Temporary; removed after production handoff confirmed |
| `deprecated_ready_to_remove` | Removal conditions met or near |

**Removal gate (all 7 conditions must be met before any file is deleted):**
1. Replacement component exists
2. Replacement is tested
3. Shadow/advisory evidence proves equivalent or better behaviour
4. Production handoff no longer uses old path
5. Rollback path is clear
6. Replacement tests exist
7. Owner approval recorded

---

## Module Classification

### Production Runtime Modules
*Required in production deployment. These must be cloud-deployable, fail-safe, and observable.*

| Module | Classification | Runtime in Production | Service Layer | Cloud Impact | Notes |
|--------|---------------|----------------------|---------------|--------------|-------|
| `intelligence_engine.py` | `production_runtime` | Yes | Economic Intelligence Layer | Low | Reads local files; no live API. Must remain pure-read until Sprint 7+ |
| `candidate_resolver.py` | `production_runtime` | Yes | Economic Intelligence Layer | Low | Pure deterministic; reads transmission_rules + taxonomy |
| `macro_transmission_matrix.py` | `production_runtime` | Yes | Economic Intelligence Layer | Low | Pure logic; imported by candidate_resolver only |
| `universe_builder.py` | `production_runtime` | Yes | Opportunity Universe Builder | Low | Writes shadow universe; production handoff gate still False |
| `route_tagger.py` | `production_runtime` | Yes | Opportunity Universe Builder | Low | Pure deterministic; no I/O of its own |
| `quota_allocator.py` | `production_runtime` | Yes | Opportunity Universe Builder | Low | Pure deterministic; no I/O of its own |
| `theme_activation_engine.py` | `production_runtime` | Yes | Economic Intelligence Layer | Low | Reads local files only |
| `thesis_store.py` | `production_runtime` | Yes | Economic Intelligence Layer | Low | Deterministic template; no LLM |
| `compare_universes.py` | `production_runtime` | Yes | Opportunity Universe Builder | Low | Reporting/comparison only; not execution-critical |

### Advisory-Only Modules
*Observer pattern. Never touches candidates, Apex, scoring, risk, orders, or execution.*

| Module | Classification | Runtime in Production | Service Layer | Cloud Impact | Notes |
|--------|---------------|----------------------|---------------|--------------|-------|
| `advisory_reporter.py` | `advisory_only` | No (report-only) | Advisory Layer | None | Generates offline advisory_report.json; not on live bot execution path |
| `advisory_logger.py` | `advisory_only` | Yes, when flag=True | Advisory Layer | None | Hook in run_scan(); gated by `intelligence_first_advisory_enabled=False` |
| `advisory_log_reviewer.py` | `advisory_only` | No (offline review) | Advisory Layer | None | Reads advisory_runtime_log.jsonl; writes advisory_log_review.json; evidence gate tool |

### Adapter-Only Modules
*Read-only adapters. No side effects, no writes to source files.*

| Module | Classification | Runtime in Production | Service Layer | Cloud Impact | Notes |
|--------|---------------|----------------------|---------------|--------------|-------|
| `intelligence_adapters.py` | `adapter_only` | Yes | Economic Intelligence Layer | None | 9 read-only adapters; must never trigger source module side effects |

### Schema Validator Modules
*Validation tooling. Production-useful for CI/CD. Not on hot execution path.*

| Module | Classification | Runtime in Production | Service Layer | Cloud Impact | Notes |
|--------|---------------|----------------------|---------------|--------------|-------|
| `intelligence_schema_validator.py` | `schema_validator` | CI/CD only | Tooling | None | 20 validator functions; not imported by live bot |

### Backtest-Only Modules
*Offline evaluation. Must not be imported in production runtime.*

| Module | Classification | Runtime in Production | Service Layer | Cloud Impact | Notes |
|--------|---------------|----------------------|---------------|--------------|-------|
| `backtest_intelligence.py` | `backtest_only` | No | Backtest/Evaluation | None | Reads local files; generates 7 backtest output files; must never run on live bot path |

### Scripts
| Script | Classification | Runtime in Production | Notes |
|--------|---------------|----------------------|-------|
| `scripts/validate_intelligence_files.py` | `schema_validator` | CI/CD only | CLI wrapper for intelligence_schema_validator |

---

## Data File Classification

### Production Runtime Data Files
*Read by production runtime modules. Must exist, must be fresh, must be valid.*

| File | Classification | Owner Module | Fail-Closed When Missing? | Notes |
|------|---------------|--------------|--------------------------|-------|
| `data/intelligence/transmission_rules.json` | `production_runtime` | intelligence_engine, candidate_resolver | Yes (engine returns unavailable state) | Static; updated manually per sprint |
| `data/intelligence/theme_taxonomy.json` | `production_runtime` | candidate_resolver, theme_activation_engine | Yes | Static; updated manually per sprint |
| `data/intelligence/thematic_roster.json` | `production_runtime` | candidate_resolver, theme_activation_engine | Yes | Static; updated manually per sprint |
| `data/intelligence/economic_candidate_feed.json` | `production_runtime` | universe_builder, theme_activation_engine | Yes | Generated by candidate_resolver |
| `data/intelligence/daily_economic_state.json` | `production_runtime` | theme_activation_engine, thesis_store | Yes | Generated by intelligence_engine |
| `data/intelligence/current_economic_context.json` | `production_runtime` | compare_universes (reporting) | No (reporting only) | Generated by intelligence_engine |
| `data/intelligence/theme_activation.json` | `production_runtime` | thesis_store, advisory_reporter | Yes | Generated by theme_activation_engine |
| `data/intelligence/thesis_store.json` | `production_runtime` | advisory_reporter | No (advisory only for now) | Generated by thesis_store |
| `data/intelligence/source_adapter_snapshot.json` | `production_runtime` | universe_builder | No (degrades gracefully) | Generated by intelligence_adapters |
| `data/universe_builder/active_opportunity_universe_shadow.json` | `production_runtime` | compare_universes | Yes (when handoff enabled) | Generated by universe_builder; becomes `active_opportunity_universe.json` at handoff |

### Shadow / Comparison / Reporting Data Files
*Comparison and diagnostic outputs. Keep until production handoff is proven stable.*

| File | Classification | Remove After Cutover? | Notes |
|------|---------------|----------------------|-------|
| `data/universe_builder/current_vs_shadow_comparison.json` | `shadow_only` | No (keep for monitoring) | Comparison is useful post-handoff for regression tracking |
| `data/universe_builder/universe_builder_report.json` | `shadow_only` | No (keep for monitoring) | Sprint-level report; diagnostic value |
| `data/universe_builder/current_pipeline_snapshot.json` | `migration_tooling` | Yes (after handoff) | Describes old pipeline topology; not needed once new pipeline is live |

### Advisory Data Files
*Generated by advisory layer. Never read by execution path.*

| File | Classification | Remove After Cutover? | Notes |
|------|---------------|----------------------|-------|
| `data/intelligence/advisory_report.json` | `advisory_only` | No — keep only if useful for review/diagnostics | Offline advisory report output; not on execution path; retain while it provides review value |
| `data/intelligence/advisory_runtime_log.jsonl` | `advisory_only` | No — keep during advisory observation phase | Per-scan advisory runtime log; define retention/rotation policy before cloud deployment |
| `data/intelligence/advisory_log_review.json` | `advisory_only` | No — offline review output | Generated by advisory_log_reviewer.py; evidence gate output; not on execution path |

### Backtest Data Files
*Offline evaluation results. Must never be read by production runtime.*

| File | Classification | Remove After Cutover? | Notes |
|------|---------------|----------------------|-------|
| `data/intelligence/backtest/regime_fixture_results.json` | `backtest_only` | No (keep for audit trail) | Sprint 5A fixture results |
| `data/intelligence/backtest/theme_activation_fixture_results.json` | `backtest_only` | No (keep for audit trail) | Sprint 5A fixture results |
| `data/intelligence/backtest/candidate_feed_ablation_results.json` | `backtest_only` | No (keep for audit trail) | Sprint 5A ablation results |
| `data/intelligence/backtest/risk_overlay_fixture_results.json` | `backtest_only` | No (keep for audit trail) | Sprint 5A risk overlay results |
| `data/intelligence/backtest/intelligence_backtest_summary.json` | `backtest_only` | No (keep for audit trail) | Sprint 5A/5B summary |
| `data/intelligence/backtest/historical_replay_fixtures.json` | `backtest_only` | No (keep for audit trail) | Sprint 5B historical scenarios |
| `data/intelligence/backtest/historical_replay_results.json` | `backtest_only` | No (keep for audit trail) | Sprint 5B replay results |

---

## Test File Classification

### Tests to Keep — Production Safety Tests
*These assert correct behaviour of production execution paths. Never delete.*

| Test File | Classification | What It Protects |
|-----------|---------------|-----------------|
| `tests/test_intelligence_day2.py` | `production_runtime` | Transmission rules, theme taxonomy, thematic roster schemas |
| `tests/test_intelligence_day3.py` | `production_runtime` | Economic candidate feed schema and roster-only constraint |
| `tests/test_intelligence_day4.py` | `production_runtime` | Shadow universe schema, route tags, quota groups |
| `tests/test_intelligence_day5.py` | `production_runtime` | Comparison schema, structural candidate survival |
| `tests/test_intelligence_day6.py` | `production_runtime` | 5-slice coverage, quota pressure diagnostics, source collision |
| `tests/test_intelligence_day7.py` | `production_runtime` | Adapter safety contract (side_effects=false, live_data=false) |
| `tests/test_intelligence_sprint2.py` | `production_runtime` | Route tagger rules, quota allocator correctness |
| `tests/test_intelligence_sprint3.py` | `production_runtime` | Headwind handling, route metric distinction |
| `tests/test_intelligence_sprint4a.py` | `production_runtime` | Daily economic state and economic context schemas |
| `tests/test_intelligence_sprint4b.py` | `production_runtime` | Theme activation and thesis store schemas |
| `tests/test_intelligence_sprint6b.py` | `production_runtime` | Advisory hook isolation, flag gate, no-mutation, no-production-imports |

### Tests to Keep — Advisory / Shadow Validation Tests
*Assert advisory layer correctness. Keep permanently as advisory observer-layer contract.*

| Test File | Classification | What It Protects |
|-----------|---------------|-----------------|
| `tests/test_intelligence_sprint6a.py` | `advisory_only` | Advisory report structure, non-executable invariant, forbidden paths |
| `tests/test_intelligence_sprint6c.py` | `advisory_only` | Log reviewer output structure, decision gate validity, safety invariant detection, forbidden imports |

### Tests to Keep — Backtest Validation Tests
*Assert backtest output correctness. Keep for CI audit trail. Not on hot path.*

| Test File | Classification | What It Protects |
|-----------|---------------|-----------------|
| `tests/test_intelligence_sprint5a.py` | `backtest_only` | Backtest fixtures, ablation variants, risk overlay |
| `tests/test_intelligence_sprint5b.py` | `backtest_only` | Historical replay fixtures and results |

---

## Duplicate Logic Audit

| Area | Verdict | Detail |
|------|---------|--------|
| Regime selection logic | **Justified** | `backtest_intelligence.py` has a local copy (`_select_regime_local`) — this is intentional to prevent backtest from importing production engine; no runtime duplication |
| Theme evaluation logic | **Justified** | `backtest_intelligence.py` has `_evaluate_theme_for_driver_states` — same rationale; backtest isolation requires local copies |
| Posture logic | **Justified** | Same rationale |
| Route assignment | **None** | `route_tagger.py` is the single source; `universe_builder.py` delegates to it |
| Quota allocation | **None** | `quota_allocator.py` is the single source; `universe_builder.py` delegates to it |
| Driver state inference | **None** | `intelligence_engine.py` is the single source |
| Advisory report reading | **None** | Only `advisory_logger.py` reads `advisory_report.json` at runtime |

---

## Modules That Must NOT Be Imported in Production Runtime

| Module | Why |
|--------|-----|
| `backtest_intelligence.py` | Contains local copies of regime/theme logic; importing it would create confusion about authority; has no production output path |
| `advisory_reporter.py` | Generates the static advisory_report.json offline; must not run on live bot execution path |
| `scanner.py` | Only referenced as a read-only adapter source; must not be imported by intelligence modules |
| `bot_trading.py` | Live bot; must not be imported by intelligence modules |
| `market_intelligence.py` | Apex orchestration; must not be imported by intelligence modules |

---

## Production Handoff Readiness (Not Yet Enabled)

| Gate | Status |
|------|--------|
| `enable_active_opportunity_universe_handoff` | **False** (locked — never enabled during this phase) |
| Shadow universe file stable | Yes |
| Comparison proves structural survival | Yes — 20/20 structural slots filled |
| Advisory layer logging accumulation | **Active — Real-Session Observation Phase begun** |
| Advisory reviewer gate | `insufficient_live_observation` (1 demo record; need ≥10 records or ≥3 sessions) |
| `intelligence_first_advisory_enabled` | **True** (enabled for real-session observation) |
| Full suite baseline | Partially fixed (v3.7.9 — test_orders_core, test_reconnect, test_atr_sizer, test_tranche_exits patch applied) |
| Production simplification audit | **This document** |

**Handoff will require (not started):**
1. `advisory_log_reviewer.py` returns `advisory_ready_for_handoff_design`
2. Amit approves reviewer output
3. `active_opportunity_universe_shadow.json` → renamed to `active_opportunity_universe.json`
4. `enable_active_opportunity_universe_handoff = True`
5. `current_pipeline_snapshot.json` retired (migration_tooling — no longer needed)
6. Apex input change to read from production universe file
7. Full suite clean run with all pre-existing failures documented

---

## Shadow-Only Files — Disposition After Cutover

### Keep after cutover (monitoring value)
- `data/universe_builder/current_vs_shadow_comparison.json` — regression monitoring
- `data/universe_builder/universe_builder_report.json` — sprint-level diagnostics
- All backtest data files — audit trail
- All advisory data files — observer logs

### Remove after cutover (migration tooling only)
- `data/universe_builder/current_pipeline_snapshot.json` — describes old topology; obsolete once new pipeline is live

---

## Cloud Runtime Impact Assessment

| Module | Cloud Impact | Reason |
|--------|-------------|--------|
| `intelligence_engine.py` | Low | Pure file reads; no network calls |
| `candidate_resolver.py` | Low | Pure computation; no network calls |
| `macro_transmission_matrix.py` | Low | Pure logic; no I/O |
| `universe_builder.py` | Low | File reads/writes; no network calls |
| `route_tagger.py` | None | Pure function; no I/O |
| `quota_allocator.py` | None | Pure function; no I/O |
| `theme_activation_engine.py` | Low | Pure file reads/writes |
| `thesis_store.py` | Low | Pure file reads/writes |
| `intelligence_adapters.py` | None | Reads static files only |
| `advisory_logger.py` | None | Reads advisory_report.json + appends to JSONL |
| `advisory_reporter.py` | None | Offline report generation only |
| `advisory_log_reviewer.py` | None | Offline review only; no network calls |
| `backtest_intelligence.py` | None | Excluded from production runtime |
| `intelligence_schema_validator.py` | None | CI/CD only |

---

## Sprint 6B File Classifications

### advisory_logger.py

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Advisory / Observability |
| Runtime needed in production | Yes, only if advisory logging remains part of operations |
| Temporary or permanent | TBD — retain until real-session advisory log review confirms value |
| Production purpose | Read advisory_report.json; append one observer-only record per scan to advisory_runtime_log.jsonl |
| Must not affect execution | Yes — no candidate mutation, no Apex input, no risk/order/execution change |
| Must not be imported by | scanner.py, market_intelligence.py, orders_core.py, guardrails.py |
| Retirement condition | Remove if advisory logs prove low value, or if production observability is consolidated in an external service (Datadog, CloudWatch, etc.) |
| Rollback | Set `intelligence_first_advisory_enabled = False` in config.py; module is never loaded when flag is off |

### data/intelligence/advisory_runtime_log.jsonl

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Observability output |
| Runtime needed in production | Yes, if advisory mode is active |
| Temporary or permanent | Permanent while advisory hook is active; retention policy TBD |
| Cloud runtime impact | Low — JSONL append; but log rotation and size management required before cloud deployment |
| Retention policy | Not yet defined. Before cloud deployment: establish max size, rotation, or forwarding to log aggregator |
| Must not be read by execution path | Yes — never read by bot_trading.py, market_intelligence.py, orders_core.py, or any live decision module |

### tests/test_intelligence_sprint6b.py

| Field | Value |
|-------|-------|
| Classification | `advisory_only` (test) |
| Service layer | Test / CI |
| Runtime needed in production | No |
| Must remain in CI | Yes — while advisory hook exists in bot_trading.py, these tests are the primary safety net for the hook |
| Retirement condition | Remove only if advisory hook is removed from bot_trading.py |

### bot_trading.py — advisory hook (13-line addition in run_scan())

| Field | Value |
|-------|-------|
| Classification | `advisory_only` hook inside a `production_runtime` file |
| Service layer | Live bot observability |
| Runtime needed in production | Yes, only if advisory mode is retained |
| Production risk | Low when `intelligence_first_advisory_enabled = False` (default) |
| Live-bot file touched | Yes — `bot_trading.py` is a production runtime file |
| Full suite waiver | Explicitly waived by Amit; confirmed clean in background run (30 failed / 2855 passed — Sprint 3 baseline) |
| Removal condition | Remove hook if advisory mode is retired or replaced by external observability service |
| Rollback | `intelligence_first_advisory_enabled = False` disables hook with zero code change; or remove the 13-line try/except block |
| Required before future handoff | Full suite must be run before any production handoff or live decision-path change involving bot_trading.py |

---

## Sprint 6C File Classifications

### advisory_log_reviewer.py

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Advisory / Evidence Gate |
| Runtime needed in production | No — offline review tool only |
| Temporary or permanent | Permanent while advisory logging is active |
| Production purpose | Reads advisory_runtime_log.jsonl; analyses safety invariants; classifies decision_gate; writes advisory_log_review.json |
| Must not affect execution | Yes — no production imports, no live API, no broker, no .env |
| Must not be imported by | bot_trading.py, market_intelligence.py, scanner.py, orders_core.py |
| Retirement condition | Remove if advisory logging is retired or replaced by external observability |
| Rollback | None needed — offline tool; removing it has zero runtime impact |

### data/intelligence/advisory_log_review.json

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Observability evidence output |
| Runtime needed in production | No — Amit/operator review only |
| Temporary or permanent | Per-review output; can be regenerated on demand |
| Cloud runtime impact | None — not read by any live decision module |
| Decision gate values | `insufficient_live_observation` / `advisory_safe_continue_logging` / `advisory_ready_for_handoff_design` / `advisory_needs_fix` |

### tests/test_intelligence_sprint6c.py

| Field | Value |
|-------|-------|
| Classification | `advisory_only` (test) |
| Service layer | Test / CI |
| Runtime needed in production | No |
| Must remain in CI | Yes — while advisory_log_reviewer.py exists and the evidence gate is part of handoff readiness |
| Retirement condition | Remove only if advisory log reviewer is retired |

---

## Anti-Bloat Confirmation — Sprint 6B

| Check | Status |
|-------|--------|
| New files added | 3 (advisory_logger.py, advisory_runtime_log.jsonl, test_intelligence_sprint6b.py) |
| All net-new (no duplicates of existing) | Yes |
| Existing files made obsolete | None |
| Duplicate logic introduced | None |
| Production handoff triggered | No — `enable_active_opportunity_universe_handoff = False` |
| active_opportunity_universe.json consumed by bot | No |
| Advisory log creates execution pressure | No — advisory_only=true, executable=false, order_instruction=null |
| Apex input enriched | No |
| Candidate source replaced | No |
| Order/risk/execution changed | No |
| live_output_changed | False |

---

## Real-Session Advisory Observation Phase

**Status: ACTIVE — `intelligence_first_advisory_enabled = True` as of v3.7.10**

**Objective:** Collect real advisory_runtime_log.jsonl data across multiple sessions with `intelligence_first_advisory_enabled = True` and `enable_active_opportunity_universe_handoff = False`.

**Metrics to collect per session:**
- advisory_include / watch / defer / unresolved counts per scan
- Route disagreements: how many current candidates have a different shadow route?
- Unsupported current candidates: how many have no intelligence backing?
- Missing shadow candidates: how many shadow candidates is the current pipeline missing?
- Tier D preservation/loss across scans
- Structural quota overflow per scan
- Attention cap pressure per scan
- Hook latency: does advisory logging add observable latency to run_scan()?
- Hook error rate: does advisory_report.json ever go missing or stale mid-session?
- Execution pressure check: does advisory status ever influence a trade? (Expected: never)

**Gate for next phase:** No production handoff decision until `advisory_log_reviewer.py` returns `advisory_ready_for_handoff_design` AND Amit explicitly approves after reviewing the full real-session report.

**Advisory file classifications for cutover:**

| File | Keep After Cutover? | Reason |
|------|--------------------|----|
| `advisory_logger.py` | TBD — if advisory logging retained | Depends on whether advisory observability continues in production |
| `advisory_log_reviewer.py` | No — offline review tool | Not production runtime; run on demand |
| `advisory_runtime_log.jsonl` | Yes — with retention/rotation policy | Runtime observability output; cloud deployment requires log rotation |
| `advisory_log_review.json` | No — regenerated on demand | Offline evidence gate output; not execution input |
| `advisory_report.json` | TBD — keep only if diagnostic value remains | Offline advisory report; not on execution path |

**Modules that must never be imported by advisory layer:**
`scanner.py`, `bot_trading.py`, `market_intelligence.py`, `orders_core.py`, `guardrails.py`, `catalyst_engine.py`, `overnight_research.py`, `agents.py`, `sentinel_agents.py`, `learning.py`, `bot_ibkr.py`

**Backtest-only modules excluded from production runtime:**
`backtest_intelligence.py` — contains local regime/theme copies; has no production output path; must not be imported in live runtime.

---

## Sprint 7A.1 File Classifications

**Sprint 7A.1 — Exhaustive Reference Data Layer**
**Objective:** Build a local, static, API-free symbol classification layer that supports the production handoff reader. No production code touched. No external calls. No .env inspection.

### reference_data_builder.py

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Reference Data / Build Tool |
| Runtime needed in production | No — offline build tool only; run to regenerate reference files |
| Temporary or permanent | Permanent while reference data layer is active |
| Production purpose | Reads all approved local source files; classifies symbols; writes sector_schema.json, symbol_master.json, theme_overlay_map.json, coverage_gap_review.json |
| Must not affect execution | Yes — no production imports, no live API, no broker, no .env, no LLM |
| Safety invariants | `favourites_used_as_discovery=false`, `live_api_called=false`, `llm_called=false`, `env_inspected=false` embedded in outputs |
| Removal condition | Remove if reference data layer is replaced by external provider integration |

### data/reference/sector_schema.json

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Reference Data |
| Runtime needed in production | No — static reference file; read only by advisors and offline tools |
| Temporary or permanent | Permanent — regenerate via reference_data_builder when sector hierarchy changes |
| Production purpose | GICS-like sector/industry hierarchy + proxy classification definitions; used by symbol_master and validators |

### data/reference/symbol_master.json

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Reference Data |
| Runtime needed in production | No — static reference; feeds coverage analysis and handoff reader design |
| Temporary or permanent | Permanent — regenerate via reference_data_builder when source files change |
| Production purpose | Per-symbol sector, industry, classification_status, approval_status, sources; ~1000+ symbols |
| Safety invariant | `favourites_used_as_discovery: false` enforced in schema and validated by validator |

### data/reference/theme_overlay_map.json

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Reference Data |
| Runtime needed in production | No — static reference; used by handoff design and coverage gap analysis |
| Temporary or permanent | Permanent — add new overlays as new themes emerge |
| Production purpose | 82+ theme overlays with supply_chain_role, risk_flags, canonical_symbols, proxy_symbols; covers all sectors |
| Anti-bloat note | Does NOT automatically approve symbols — approval requires separate action in symbol_master |

### data/intelligence/coverage_gap_review.json

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Evidence / Coverage Analysis |
| Runtime needed in production | No — offline gap analysis output |
| Temporary or permanent | Regenerated on demand; keep as evidence artefact |
| Production purpose | Recurring missing shadow + unsupported current symbols from advisory log; recommended_action per symbol |

### tests/test_intelligence_reference_data.py

| Field | Value |
|-------|-------|
| Classification | `advisory_only` |
| Service layer | Test / Evidence |
| Runtime needed in production | No |
| Temporary or permanent | Permanent — 42 tests covering all 4 validators and builder safety invariants |
| Test coverage | Group A: sector_schema (6), Group B: symbol_master (10), Group C: theme_overlay_map (9), Group D: coverage_gap_review (7), Group E: builder safety invariants (6), integration (4) |

### intelligence_schema_validator.py (Sprint 7A.1 changes)

| Change | Detail |
|--------|--------|
| 4 new validators added | `validate_sector_schema`, `validate_symbol_master`, `validate_theme_overlay_map`, `validate_coverage_gap_review` |
| 1 existing bug fixed | `validate_advisory_log_review` was missing `return result` — added |
| `validate_all()` extended | 4 new optional file checks added; all guarded by `os.path.exists()` |
| No new production imports | Validator reads local files only; no live API, no broker, no .env |

### Sprint 7A.1 Anti-Bloat Confirmation

| Check | Status |
|-------|--------|
| New production runtime modules? | No — all new files are `advisory_only` or reference data |
| New live API paths? | No |
| New broker calls? | No |
| New LLM calls? | No |
| New .env reads? | No |
| Duplicate logic introduced? | No — reference builder is the single source; validator extends existing validate_all() pattern |
| Production handoff flag changed? | No — `enable_active_opportunity_universe_handoff = False` |

---

## Sprint 7A.2 File Classifications

**Sprint 7A.2 — Approved Theme Overlay / Roster Governance**
**Objective:** Convert coverage_gap_review.json recommendations into governed intelligence-layer coverage through the full architecture. No production modules touched. No external calls. No .env inspection. No favourites workaround. No production handoff.

### New / Modified Intelligence-Layer Files

| File | Classification | Runtime needed | Notes |
|------|---------------|----------------|-------|
| `data/intelligence/theme_taxonomy.json` | shadow-only | No | Extended with memory_storage and ai_compute_infrastructure themes |
| `data/intelligence/transmission_rules.json` | shadow-only | No | Extended with 2 new rules (ai_capex_to_memory_storage, ai_compute_demand_to_ai_compute_infrastructure) |
| `data/intelligence/thematic_roster.json` | shadow-only | No | Extended with 2 new roster entries (memory_storage, ai_compute_infrastructure) |
| `data/reference/theme_overlay_map.json` | advisory-only | No | Regenerated: 84 themes (was 82) |
| `data/intelligence/economic_candidate_feed.json` | shadow-only | No | Regenerated: 43 candidates (was 26); SNDK/WDC/IREN now present |
| `candidate_resolver.py` | shadow-only | No | ai_compute_demand added to default active_drivers list |
| `reference_data_builder.py` | advisory-only | No | 2 new governed overlay entries in _build_theme_overlay_map() |
| `intelligence_schema_validator.py` | advisory-only | No | _VALID_DIRECTIONS extended; _VALID_ROUTE_BIASES extended |
| `tests/test_intelligence_sprint7a2.py` | advisory-only test | No | 30 tests covering governance, feed presence, exclusion invariants, safety flags |

### Sprint 7A.2 Anti-Bloat Confirmation

| Check | Status |
|-------|--------|
| New production runtime modules? | No — all changes in shadow/advisory layer only |
| New live API paths? | No |
| New broker calls? | No |
| New LLM calls? | No |
| New .env reads? | No |
| Favourites workaround used? | No — all symbols approved through full governance chain |
| Duplicate logic introduced? | No — new themes extend existing taxonomy/roster/rule pattern |
| Production handoff flag changed? | No — `enable_active_opportunity_universe_handoff = False` |
| IREN caution observed? | Yes — lower confidence (0.60 vs 0.72/0.82), watchlist_or_swing route bias, review_required_symbols=[] (IREN individually approved, others not implied) |

---

## Sprint 7A.3 File Classifications

**Sprint 7A.3 — Factor Registry + Provider Capability Audit**
**Objective:** Define the data-factor contract for the final production system. Determine what factors can be fetched, from which providers, which are production-suitable, and which architecture layer owns them. No production code modified. No live API calls during generation. No .env inspection.

### New / Modified Files

| File | Classification | Runtime needed | Notes |
|------|---------------|----------------|-------|
| `factor_registry.py` | advisory/reference-only | No | Static generator — no live API, no .env, no broker. Writes to data/reference/ only. |
| `provider_fetch_tester.py` | advisory/reference-only | No | Safe connectivity tester — read-only market data fetches only. No positions/orders/account calls. |
| `data/reference/factor_registry.json` | reference-data | No | 73 factors, 13 categories, 10 layers. All factors have must_not_trigger_trade_directly=True. |
| `data/reference/provider_capability_matrix.json` | reference-data | No | 6 providers, per-category suitability tiers. |
| `data/reference/layer_factor_map.json` | reference-data | No | Factor ownership by architecture layer. |
| `data/reference/data_quality_report.json` | reference-data | No | 9 production-ready categories, 2 partial, 2 unavailable. |
| `data/reference/provider_fetch_test_results.json` | reference-data | No | 12/15 passed. Alpaca 3/3, FMP 5/5, AV 2/4, yfinance 2/2, IBKR 0/1 (expected). |
| `intelligence_schema_validator.py` | advisory-only | No | 5 new validators added and wired into validate_all(). Stale duplicate return removed. |
| `tests/test_intelligence_factor_registry.py` | advisory-only test | No | 32 tests covering all 5 new files and validators. |

### Sprint 7A.3 Anti-Bloat Confirmation

| Check | Status |
|-------|--------|
| New production runtime modules? | No — factor_registry.py and provider_fetch_tester.py are offline build tools only |
| New live API paths in production bot? | No |
| New broker calls? | No |
| New LLM calls? | No |
| New .env reads? | No — dotenv loaded for testing only; env_inspected=false in all outputs |
| Secrets in output files? | No — secrets_exposed=false in all results |
| Production handoff flag changed? | No — `enable_active_opportunity_universe_handoff = False` |

---

## Sprint 7A.4 File Classifications

**Sprint 7A.4 — Runtime Orchestration and Cloud Process Architecture**
**Objective:** Produce the authoritative design documents that govern runtime process contracts, cloud deployment, snapshot validity, failure modes, and terminology. Design sprint only — no production code, no handoff.

### New Documentation Files

| File | Classification | Runtime needed | Notes |
|------|---------------|----------------|-------|
| `docs/intelligence_first_runtime_orchestration.md` | documentation | No | 12 processes defined with schedules, dependencies, outputs, restart policies, SLAs |
| `docs/intelligence_first_cloud_process_map.md` | documentation | No | 3-phase cloud deployment model; Phase 1 Docker Compose YAML; secrets + env var policy |
| `docs/intelligence_first_snapshot_contract.md` | documentation | No | Universal Snapshot Schema (18 fields); 9 fail-closed conditions; freshness SLA table; live manifest contract |
| `docs/intelligence_first_runtime_failure_modes.md` | documentation | No | 30 failure modes across all 12 processes with detection, response, log, alert, risk classification |
| `docs/intelligence_first_definitions_and_runtime_contract.md` | documentation | No | Authoritative terminology whitepaper; 16 sections; 30+ terms; 13 deprecated terms formalised |

### Modules Excluded from Live Trading Bot Import Path (formalised in Sprint 7A.4)

These modules must not be imported by the live trading bot (`bot_trading.py` or any module it imports at runtime). Note: some may run in separate offline or scheduled production workers — they are excluded from the **live bot's import path**, not necessarily from all production deployment.

| Module | Exclusion scope | Reason |
|--------|-----------------|--------|
| `provider_fetch_tester.py` | Excluded from all runtime containers | Diagnostic connectivity tool only |
| `factor_registry.py` | Excluded from all runtime containers | Reference build tool; run offline only |
| `backtest_intelligence.py` | Excluded from all runtime containers | Offline research; local regime/theme copies |
| `advisory_reporter.py` | Excluded from live-bot import path | Shadow pipeline; offline report generation |
| `advisory_log_reviewer.py` | Excluded from live-bot import path | Shadow pipeline; offline evidence gate |
| `reference_data_builder.py` | Excluded from live-bot import path; runs as a separate scheduled offline production worker | Must never be imported by live bot at runtime |

### Key Architecture Decisions Formalised

| Decision | Document Section |
|----------|----------------|
| `data/live/current_manifest.json` is written by `handoff_validator_publisher` only — no other process writes to this path | snapshot_contract.md §6.1 |
| When `handoff_enabled=True` and manifest fails any check, bot must NOT fall back to scanner discovery — degrade gracefully instead | snapshot_contract.md §6.3 rule 6 |
| Scanner discovers AND scores in live bot (pre-handoff); Market Sensor scores pre-approved symbols as independent worker (post-handoff) | definitions_and_runtime_contract.md §10 |
| Fail-closed means safest action when input missing/stale/invalid — not a crash; bot continues managing existing positions | definitions_and_runtime_contract.md §14 |
| Advisory layer must never be imported by `bot_trading.py` at the module level — only the hook imports it conditionally | runtime_orchestration.md §5 |

### Sprint 7A.4 Anti-Bloat Confirmation

| Check | Status |
|-------|--------|
| New production runtime modules? | No — documentation only |
| New live API paths in production bot? | No |
| New broker calls? | No |
| New LLM calls? | No |
| New .env reads? | No |
| Production handoff flag changed? | No — `enable_active_opportunity_universe_handoff = False` |
| Production code modified? | No — zero production file changes |
| Tests added? | No — design sprint; testing deferred to implementation sprint |
| Duplicate logic introduced? | No |
| live_output_changed | False |

---

## Sprint 7C File Classifications

**Sprint 7C — Paper Handoff Comparison and Dry-Run Decision Evidence**

| File | Classification | Included in live-bot container? | Notes |
|------|---------------|--------------------------------|-------|
| `paper_handoff_comparator.py` | Temporary migration / dry-run validation tool | No | One-shot comparison tool. Not a runtime process. Remove or retire after controlled production handoff is stable. |
| `data/live/paper_handoff_comparison_report.json` | Dry-run evidence artefact | No | Produced by comparator. Not a live input. May be retained as cutover audit evidence. |
| `tests/test_intelligence_sprint7c.py` | Test — advisory pipeline | No | 48 tests across 7 classes. |

### Sprint 7C Key Findings

1. **Paper candidates are a governed subset of the current pipeline** — 50 paper candidates vs 235 current candidates. The gap is expected: paper contains only governed intelligence-layer candidates; the current pipeline also includes all scanner-sourced tier_a/b/d attention candidates.
2. **SNDK/WDC/IREN are correctly governed but excluded by structural quota** — All three are in thematic_roster, transmission_rules, theme_taxonomy, and symbol_master. Exclusion is due to structural quota cap (20). Pipeline wiring is correct; quota is the binding constraint, not a coverage failure.
3. **Route disagreements are vocabulary-only** — 17 disagreements total; 14 are swing/intraday normalisation or manual_conviction normalisation. 3 meaningful watchlist demotions are expected (paper routes are more conservative).
4. **Recommendation: `ready_for_controlled_handoff_design`** — All safety invariants hold, governed symbols are correctly handled, quota constraint is documented. Next step is Sprint 7D controlled handoff wiring design (not implementation).
5. **`handoff_reader.py` is the future live-bot / candidate-source boundary reader, not a `universe_builder.py` dependency** — `universe_builder.py` produces universe files; it must not consume the handoff reader.

### Sprint 7C Anti-Bloat Confirmation

| Check | Result |
|-------|--------|
| New modules added beyond spec? | No |
| New production bot imports added? | No |
| Scanner fallback introduced? | No |
| LLM called? | No |
| Broker called? | No |
| New config flags added? | No |
| Tests added? | Yes — 48 tests (spec requirement) |
| Duplicate logic introduced? | No |
| live_output_changed | False |

---

## Sprint 7B File Classifications

**Sprint 7B — Paper Handoff Reader Validation**

| File | Classification | Included in live-bot container? | Notes |
|------|---------------|--------------------------------|-------|
| `handoff_reader.py` | Production runtime candidate | **Yes (when handoff enabled)** | 7-function public API for reading and validating handoff files. Read-only. No scanner, no bot imports. Currently has no callers — wired only when `enable_active_opportunity_universe_handoff=True` (blocked). |
| `paper_handoff_builder.py` | Temporary migration / advisory-only tool | No | Transforms shadow universe → paper artefacts. Not imported by live bot. One-shot tool, not a recurring runtime process. |
| `data/live/paper_active_opportunity_universe.json` | Paper artefact | No | 50 accepted candidates, executable=false for all, mode=paper_handoff_universe. Not the production active universe file. |
| `data/live/paper_current_manifest.json` | Paper artefact | No | handoff_enabled=False, handoff_mode=paper. Not `data/live/current_manifest.json`. |
| `data/live/paper_handoff_validation_report.json` | Paper artefact | No | handoff_allowed=False always. Mode=paper_handoff_validation. |
| `tests/test_intelligence_sprint7b.py` | Test — advisory pipeline | No | 53 tests across 12 classes. |

### Sprint 7B Key Architecture Decisions

1. **`handoff_reader.py` is fail-closed with no scanner fallback** — on any failure, `handoff_allowed=False` and `candidate_count_allowed=0`. No fallback to the legacy scanner is permitted.
2. **`data/live/current_manifest.json` and `data/live/active_opportunity_universe.json` are never written** — reserved exclusively for the production handoff path when `enable_active_opportunity_universe_handoff=True`.
3. **Paper artefacts use 24-hour expiry** — validation artefacts run on-demand, not every 15 minutes; shorter SLA would cause immediate test failure.
4. **`paper_handoff_builder.py` derives approval_status, theme_ids, risk_flags, route_hint from shadow candidate fields** — shadow candidates do not carry these fields explicitly; all derived during transformation.
5. **`handoff_allowed` is always `False` in Sprint 7B** — even when all structural validation passes, because `handoff_enabled=False` in the manifest is an absolute gate.

### Sprint 7B Anti-Bloat Confirmation

| Check | Result |
|-------|--------|
| New modules added beyond spec? | No |
| New production bot imports added? | No — `handoff_reader.py` has no callers in live bot |
| Scanner fallback introduced? | No |
| New config flags added? | No |
| Tests added? | Yes — 53 tests (spec requirement) |
| Duplicate logic introduced? | No |
| live_output_changed | False |

---

## Sprint 7D File Classifications

**Sprint 7D — Controlled Handoff Wiring Design**
**Objective:** Produce all design documents required before Sprint 7E implementation. Resolve metric reconciliation anomaly. No production code changed. No handoff triggered. No tests added.

### New Documentation Files

| File | Classification | Runtime needed | Notes |
|------|---------------|----------------|-------|
| `docs/intelligence_first_paper_current_metric_reconciliation.md` | documentation | No | Resolves Sprint 7C metric anomaly; establishes locked metric definitions for Sprint 7E |
| `docs/intelligence_first_controlled_handoff_wiring_design.md` | documentation | No | 15 sections; wiring at bot_trading.py:1447; candidate shape mapping; fail-closed; rollback; Apex boundary |
| `docs/intelligence_first_controlled_handoff_implementation_test_plan.md` | documentation | No | 10 test groups; 86+ test cases; full suite required for Sprint 7E |
| `docs/intelligence_first_controlled_handoff_risk_review.md` | documentation | No | 11 risks; residual RISK-08 acknowledged; go/no-go criteria defined |

### New Modules Required by Sprint 7E (not yet created)

| File | Classification | Included in live-bot container? | Notes |
|------|---------------|--------------------------------|-------|
| `handoff_candidate_adapter.py` | Adapter-only | Yes (called by bot_trading.py when flag=True) | Pure function; no I/O; no side effects; attaches handoff_* prefixed governance fields to scored dicts |
| `tests/test_handoff_wiring_integration.py` | Test — production runtime | No | Full integration test suite (10 groups); required before Sprint 7E is declared complete |

### Key Architecture Decisions Formalised in Sprint 7D

| Decision | Document |
|----------|---------|
| Wiring point is `bot_trading.py:1447` — `get_dynamic_universe()` call | wiring_design.md §4 |
| `handoff_reader.py` is the candidate-source boundary reader — NOT a `universe_builder.py` dependency | wiring_design.md §1; definitions §8 |
| No scanner fallback on any fail-closed condition — ever | wiring_design.md §9; risk_review RISK-02 |
| 208 scanner-only removals are the defined consequence of the handoff — not a bug | metric_reconciliation.md §5; risk_review RISK-08 |
| Sprint 7C "current" was enriched — `in_shadow_not_current_symbols` must NOT be in current baseline | metric_reconciliation.md §2 |
| Governance metadata uses `handoff_*` prefix — no field collision with existing scored dicts | wiring_design.md §6; risk_review RISK-09 |
| `handoff_candidate_adapter.py` must never modify `score`, `raw_score`, or signal dimensions | wiring_design.md §6; test_plan Group 5 |
| Dry-run compare mode (`enable_handoff_dry_run_compare`) is separate from handoff flag — different semantics | wiring_design.md §8 |

### Sprint 7D Anti-Bloat Confirmation

| Check | Status |
|-------|--------|
| New production runtime modules added? | No — documentation only in Sprint 7D; handoff_candidate_adapter.py deferred to Sprint 7E |
| New live API paths in production bot? | No |
| New broker calls? | No |
| New LLM calls? | No |
| New .env reads? | No |
| Production handoff flag changed? | No — `enable_active_opportunity_universe_handoff = False` |
| Production code modified? | No — zero production file changes |
| Tests added? | No — design sprint; tests deferred to Sprint 7E |
| Duplicate logic introduced? | No |
| live_output_changed | False |

---

## Sprint 7E File Classifications

**Sprint 7E — Controlled Handoff Wiring Implementation**
**Objective:** Wire `handoff_reader.py` into `bot_trading.py` at the candidate-source boundary behind `enable_active_opportunity_universe_handoff` (remains `False`). Create `handoff_candidate_adapter.py`. Create `tests/test_handoff_wiring_integration.py`. No production handoff triggered.

### Modified / Created Files

| File | Classification | Included in live-bot container? | Notes |
|------|---------------|--------------------------------|-------|
| `handoff_reader.py` | Production runtime candidate | Yes | Extended: `_production_result()` + `load_production_handoff()` added. 5-step validation chain. Returns original candidate dicts. |
| `handoff_candidate_adapter.py` | Adapter-only | Yes (called when flag=True) | Net-new. Pure functions. No I/O. `build_governance_map()` + `attach_governance_metadata()`. Attaches 13 `handoff_*` fields. Never modifies score/signal dims. |
| `bot_trading.py` | Production runtime | Yes (already) | 4 changes: module-level state, `_log_handoff_fail_closed`, `_get_handoff_symbol_universe`, wiring conditional + governance attachment + fail-closed guard. |
| `tests/test_handoff_wiring_integration.py` | Test — production runtime | No | Net-new. 100 tests, 11 groups. Smoke marker on `TestSmokeSpotCheck`. |

### Files Explicitly NOT Modified in Sprint 7E

| File | Reason |
|------|--------|
| `scanner.py` | Locked — no changes |
| `signal_pipeline.py` | Locked — no changes |
| `signals/__init__.py` | Locked — no changes |
| `apex_orchestrator.py` | Locked — no changes |
| `guardrails.py` | Locked — no changes |
| `orders_core.py` | Locked — no changes |
| `bot_ibkr.py` | Locked — no changes |
| `universe_builder.py` | Locked — no changes |
| `data/live/current_manifest.json` | Not written by Sprint 7E — Handoff Publisher's responsibility |
| `data/live/active_opportunity_universe.json` | Not written by Sprint 7E |

### Key Architecture Decisions Formalised in Sprint 7E

| Decision | Rationale |
|----------|-----------|
| `load_production_handoff` returns original candidate dicts, not validation wrappers | Governance map needs original `candidate.get("symbol")` field — validation result wrappers don't expose it cleanly |
| `_get_handoff_symbol_universe` wraps `load_production_handoff` in try/except | Any exception fails closed to `([], {}, reason)` — production never crashes due to handoff reader |
| Fail-closed guard `return`s before Track A, not before `run_scan()` | PM Track B runs unconditionally above the fail-closed guard — held positions reviewed regardless of handoff state |
| `_handoff_governance_map` is module-level, reset to `{}` at start of each cycle | Prevents stale governance map from a prior cycle leaking into a cycle where flag was off or handoff failed |
| Governance attachment is inside try/except | Adapter failure is non-critical — scored dicts proceed without handoff_ fields rather than killing the cycle |

### Sprint 7E Anti-Bloat Confirmation

| Check | Status |
|-------|--------|
| New live API paths in production bot? | No |
| New broker calls? | No |
| New LLM calls? | No |
| New .env reads? | No |
| Production handoff flag changed? | No — `enable_active_opportunity_universe_handoff = False` |
| Scanner.py modified? | No |
| Guardrails modified? | No |
| Apex prompt modified? | No |
| Risk logic changed? | No |
| Order execution logic changed? | No |
| PM Track B independence preserved? | Yes — Track B runs before fail-closed guard |
| Duplicate logic introduced? | No |
| live_output_changed | False |

---

## Update Log

| Date | Action | Notes |
|------|--------|-------|
| 2026-05-06 | Created | Initial audit covering Sprints Day2–6B. All intelligence-first modules classified. No files recommended for immediate removal — production handoff gate not yet met. |
| 2026-05-06 | Sprint 6B patch | Added explicit Sprint 6B file classifications (advisory_logger.py, advisory_runtime_log.jsonl, test_intelligence_sprint6b.py, bot_trading.py hook). Anti-bloat confirmation added. Real-session observation plan documented. |
| 2026-05-06 | Sprint 6C | Added advisory_log_reviewer.py (advisory_only, offline), advisory_log_review.json (advisory_only), test_intelligence_sprint6c.py (advisory_only test, 34 tests). Validator extended with validate_advisory_log_review(). No production modules touched. |
| 2026-05-06 | Real-Session Observation Phase | intelligence_first_advisory_enabled set True (v3.7.10). enable_active_opportunity_universe_handoff remains False. Pre-existing test failures partially fixed (v3.7.9). No candidate/Apex/scoring/risk/order/execution changes. Advisory file cutover classifications added. Advisory modules-must-not-import list formalised. |
| 2026-05-06 | Sprint 7A.1 | Added reference_data_builder.py (advisory_only, build tool), data/reference/sector_schema.json, data/reference/symbol_master.json, data/reference/theme_overlay_map.json (82 themes), data/intelligence/coverage_gap_review.json, tests/test_intelligence_reference_data.py (42 tests). Validator extended with 4 new validators; advisory_log_review missing return fixed. No production modules touched. enable_active_opportunity_universe_handoff = False. |
| 2026-05-07 | Sprint 7A.2 | Approved Theme Overlay / Roster Governance delivered. 2 new governed themes (memory_storage, ai_compute_infrastructure), 2 new transmission rules, 2 new roster entries, 2 new overlay entries. SNDK/WDC approved under memory_storage (occurrence_count=22). IREN approved under ai_compute_infrastructure with caution (occurrence_count=10, confidence=0.60). STX remains review_required. Economic candidate feed grows to 43 (was 26). candidate_resolver.py: ai_compute_demand added to default drivers. Validator: conditional_positive, swing_or_watchlist, watchlist_or_swing added as valid values. 2 stale test assertions updated (Day2 blocked_condition, Day6 valid directions). No production modules touched. No favourites workaround. enable_active_opportunity_universe_handoff=False. 804/804 regression, 25/25 validator, 4/4 smoke. live_output_changed=false. Sprint 7B blocked until Sprint 7A.2 accepted. |
| 2026-05-07 | Sprint 7C | Paper Handoff Comparison and Dry-Run Decision Evidence delivered. paper_handoff_comparator.py (temporary migration / dry-run tool); paper_handoff_comparison_report.json (dry-run evidence artefact). Comparison: 50 paper candidates, overlap 50/50 with tracked advisory set, 17 route disagreements (14 vocabulary-only), quota_binding=False, structural_overflow=0. SNDK/WDC/IREN: governed via roster+transmission_rules, excluded_due_quota=True (documented and expected), executable=False. Recommendation: ready_for_controlled_handoff_design. 1 new validator (validate_paper_handoff_comparison_report). No production files written. No bot imports changed. enable_active_opportunity_universe_handoff=False. live_output_changed=false. 48/48 Sprint 7C, 939/939 intelligence regression, 37/37 validator, 6/6 smoke. |
| 2026-05-07 | Sprint 7B | Paper Handoff Reader Validation delivered. handoff_reader.py (production runtime candidate, 7-function public API, no bot imports, fail-closed with no scanner fallback); paper_handoff_builder.py (temporary migration tool, transforms shadow → paper artefacts); 3 paper artefacts written to data/live/ (paper_active_opportunity_universe.json 50 candidates, paper_current_manifest.json handoff_enabled=False, paper_handoff_validation_report.json handoff_allowed=False); intelligence_schema_validator.py extended with 3 new validators; tests/test_intelligence_sprint7b.py (53 tests, 12 classes). data/live/current_manifest.json and data/live/active_opportunity_universe.json NOT written. No bot imports changed. No scanner fallback. enable_active_opportunity_universe_handoff=False. live_output_changed=false. 53/53 Sprint 7B, 891/891 intelligence regression, 36/36 validator, 5/5 smoke. |
| 2026-05-07 | Sprint 7A.4 | Runtime Orchestration and Cloud Process Architecture — design/documentation sprint. No production code modified. No production handoff triggered. Five documents created: intelligence_first_runtime_orchestration.md (12 processes, dependency model, live bot isolation rule), intelligence_first_cloud_process_map.md (3-phase cloud deployment, Docker Compose YAML, secrets policy, env var matrix, retention, healthcheck), intelligence_first_snapshot_contract.md (18-field universal schema, 9 fail-closed conditions, freshness SLA table, live manifest contract + example), intelligence_first_runtime_failure_modes.md (30 failure modes, all 12 processes), intelligence_first_definitions_and_runtime_contract.md (30+ terms, 13 deprecated terms, scanner vs market sensor distinction). Production container exclusions formalised: provider_fetch_tester.py, factor_registry.py, backtest_intelligence.py, advisory_reporter.py, advisory_log_reviewer.py, reference_data_builder.py. Handoff Publisher = single authorised writer of data/live/current_manifest.json. live_output_changed=false. enable_active_opportunity_universe_handoff=False. |
| 2026-05-07 | Sprint 7A.3 patch | Precise safety flag terminology applied. provider_fetch_tester.py: old generic live_api_called=false replaced with 13 precise flags — data_provider_api_called=true (fetches were made), trading_api_called=false, broker_order/account/position/execution_api_called=false, ibkr_market_data_connection_attempted=true, ibkr_order_account_position_calls=false, env_presence_checked=true, env_values_logged=false, env_file_read=true, secrets_exposed=false, live_output_changed=false. IBKR TCP probe relabelled market_data_gateway_tcp_probe with explicit "not a trading failure" detail. factor_registry.py data_quality_report flags updated: live_api_called+env_inspected → data_provider_api_called=false, live_trading_api_called=false, env_presence_checked=false, env_values_logged=false, secrets_exposed=false. Validator updated for new safety block. 34/34 tests, 30/30 validator, 4/4 smoke. live_output_changed=false. |
| 2026-05-07 | Sprint 7A.3 | Factor Registry + Provider Capability Audit delivered. factor_registry.py (73 factors, 13 categories, 10 layers, all must_not_trigger_trade_directly=True); provider_fetch_tester.py (12/15 passed: Alpaca 3/3, FMP 5/5, AV 2/4, yfinance 2/2, IBKR 0/1 gateway not running); 5 new validators in intelligence_schema_validator.py (validate_factor_registry, validate_provider_capability_matrix, validate_provider_fetch_test_results, validate_layer_factor_map, validate_data_quality_report); tests/test_intelligence_factor_registry.py (32 tests). Key provider findings: Alpaca primary for OHLCV/quotes/options (3/3), FMP primary for fundamentals/news/analyst (5/5), Alpha Vantage OVERVIEW+RSI premium-only (upgrade required), Alpha Vantage TIME_SERIES_DAILY + FEDERAL_FUNDS_RATE confirmed working (2/4). No production modules touched. env_inspected=false. secrets_exposed=false. live_output_changed=false. enable_active_opportunity_universe_handoff=False. 32/32 new tests, 30/30 validate_intelligence_files, 4/4 smoke. |
| 2026-05-07 | Sprint 7A.1 patch | 4 blockers resolved: (1) coverage_gap_review advisory evidence source corrected — now reads candidate_matches[*].advisory_status==advisory_unresolved (not empty unsupported_current_candidates.symbols). Rebuilt with 51 real records: recurring_unsupported_current_count=110. evidence_status + required_input_missing fields added. (2) intelligence_first_advisory_enabled reset to False (observation complete, gate=advisory_ready_for_handoff_design). (3) sector_schema proxy_classifications expanded to 7 (added index_proxy, crypto_proxy, macro_proxy). Validator updated to require all 7. (4) test_intelligence_reference_data.py updated: _minimal_coverage_gap + _minimal_sector_schema fixtures corrected, 2 new evidence_status/required_input_missing tests. test_intelligence_sprint6c.py: TestInsufficientObservation → TestObservationThresholdMet (assertions updated to 35-record reality). Named symbols: SNDK/WDC/IREN in recurring_unsupported_current; MU/LRCX/STX/DOCN/NBIS covered by advisory (not unresolved). 774/774 regression, 25/25 validator, 4/4 smoke. live_output_changed=false. |
| 2026-05-07 | Sprint 7E | Controlled Handoff Wiring Implementation — production code wired, flag remains False. Three files modified/created: (1) handoff_reader.py extended: _production_result() helper and load_production_handoff(manifest_path) added. 5-step validation chain: read_manifest → check handoff_enabled → validate_manifest → read_active_universe → validate_active_universe → per-candidate validation. Returns original candidate dicts in accepted_candidates (not wrappers) so governance map can be built. handoff_allowed=True only when all steps pass. (2) handoff_candidate_adapter.py net-new, adapter-only, pure, no I/O. Two functions: build_governance_map(accepted_candidates) → {symbol: candidate_dict} and attach_governance_metadata(scored_dicts, governance_map) → None (in-place, 13 handoff_* prefixed fields, never touches score/raw_score/signal dimensions). No imports of scanner/bot_trading/orders_core/guardrails/bot_ibkr/market_intelligence/apex_orchestrator/advisory_reporter/advisory_log_reviewer/provider_fetch_tester/backtest_intelligence. Classification: adapter-only / production runtime when flag=True. (3) bot_trading.py 4 changes: module-level _handoff_governance_map: dict = {} and _PRODUCTION_MANIFEST_PATH = "data/live/current_manifest.json"; _log_handoff_fail_closed(reason, manifest_path) structured warning logger; _get_handoff_symbol_universe() → (list[str], dict, str|None) — wraps load_production_handoff in try/except, fail-closed ([], {}, reason) on any failure; wiring conditional at candidate-source boundary — when flag=True calls _get_handoff_symbol_universe() instead of get_dynamic_universe(); governance attachment after run_signal_pipeline(); fail-closed guard before Track A returns early (PM Track B already ran above — fully independent). (4) tests/test_handoff_wiring_integration.py net-new: 100 tests, 11 groups. scanner_fallback_attempted=False, apex_input_changed=False, live_output_changed=False invariants verified by test suite. enable_active_opportunity_universe_handoff remains False. No scanner.py changes. No guardrails.py changes. No Apex prompt changes. No risk/order/execution logic changed. live_output_changed=false. 100/100 Sprint 7E, 939/939 intelligence regression, 34/34 validator, 7/7 smoke, 3228/3228 full suite (0 failures). |
| 2026-05-07 | Sprint 7D | Controlled Handoff Wiring Design — documentation sprint only. No production code modified. No tests added. No handoff triggered. Four new documentation files: (1) intelligence_first_paper_current_metric_reconciliation.md — resolves Sprint 7C metric anomaly. Sprint 7C built "current" as enriched set (overlap+in_current_not_shadow+in_shadow_not_current), masking 23 additive shadow-only symbols. Correct picture: 27 true overlap with scanner, 23 additions (addition_rate=0.46), 208 removals (removal_rate=0.89). Metric definitions locked for Sprint 7E: "current" = true get_dynamic_universe() output only; shadow-only symbols excluded from current baseline. (2) intelligence_first_controlled_handoff_wiring_design.md — 15 sections. Wiring at bot_trading.py:1447 (single conditional branch). _get_handoff_symbol_universe() returns list[str] — identical type to scanner. handoff_candidate_adapter.py (new, adapter-only, pure) attaches handoff_* prefixed governance fields post-scoring. Fail-closed: no scanner fallback, PM Track B independent, bot not killed. Rollback: flag flip, scanner restores next cycle. Do NOT touch: scanner.py, signal_pipeline.py, signals/__init__.py, apex_orchestrator.py, guardrails.py, orders_core.py, bot_ibkr.py. (3) intelligence_first_controlled_handoff_implementation_test_plan.md — 10 test groups covering flag=False path, flag=True valid/invalid manifest, all 21 Sprint 7B fail-closed conditions, adapter pure function tests, Apex boundary, rollback, dry-run compare mode. Full suite required (bot_trading.py will be modified). (4) intelligence_first_controlled_handoff_risk_review.md — 11 risks with likelihood/impact/mitigation/test coverage. Residual: RISK-08 (208 removals — documented architectural consequence; rollback available; Amit must acknowledge). Definitions doc updated with 10 new terms. enable_active_opportunity_universe_handoff remains False. live_output_changed=false. |
