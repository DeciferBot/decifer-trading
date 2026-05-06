# Intelligence-First Migration — Retirement Register

**Created:** 2026-05-05
**Purpose:** Track legacy components that will be retired or replaced as the Intelligence-First architecture matures.
**Rule:** No entry is retired until all removal conditions are met (see plan). No safety tests are deleted.

---

## Controlled Retirement Order

1. Old duplicated theme rosters
2. Old duplicated candidate labelling logic
3. Old flat-pool route assumptions
4. Old Tier-only priority assumptions
5. Old Apex cap logic that ignores route quotas
6. Old scanner-led universe construction — only after production handoff is proven stable

---

## Register

### REG-007 — Inline route assignment inside `universe_builder.py` (pre-Sprint 2)

| Field | Value |
|-------|-------|
| **Legacy location** | `universe_builder.py` `build()` — inline route selection from `route_hint[0]` per constructor |
| **Legacy responsibility** | Each `_from_*` constructor hard-coded the route; `build()` assigned routes without a dedicated module |
| **Replacement component** | `route_tagger.py` — `assign_route(RouteContext) -> RouteDecision`; pure deterministic function, 10 ordered rules |
| **Replacement status** | Complete (Sprint 2) |
| **Shadow/advisory proof** | Shadow pipeline uses route_tagger.py; all 413 intelligence tests pass; live_output_changed=false |
| **Production impact** | None — shadow only; live bot not wired |
| **Removal status** | Internal shadow refactor complete. Old constructors retained as payload builders; route field overridden by tagger |
| **Owner notes** | Constructors (`_from_tier_b`, `_from_economic_candidate`, etc.) still exist as payload builders and should be retained until Sprint 2 is production-stable |

---

### REG-008 — Inline quota counters / allocation inside `universe_builder.py` (pre-Sprint 2)

| Field | Value |
|-------|-------|
| **Legacy location** | `universe_builder.py` `build()` — `_add()` closure, per-group counters (structural_used, attention_used, etf_used), inline cap checks |
| **Legacy responsibility** | All quota enforcement, dedup, collision tracking, and pressure diagnostics were embedded inside `build()` |
| **Replacement component** | `quota_allocator.py` — `allocate(list[QuotaCandidate]) -> AllocationResult`; pure function, handles dedup, caps, logs, diagnostics, collision report |
| **Replacement status** | Complete (Sprint 2) |
| **Shadow/advisory proof** | Shadow pipeline uses quota_allocator.py; structural quota binding confirmed 20/20; attention cap confirmed ≤15; all 413 tests pass |
| **Production impact** | None — shadow only; live bot not wired |
| **Removal status** | Internal shadow refactor complete. `universe_builder.py` now delegates all quota logic to `quota_allocator.py` |
| **Owner notes** | Do not re-embed quota counters in universe_builder.py. If quota rules change, update quota_allocator.py only |

---

### REG-001 — `apex_cap_score.py` `compute_apex_cap_score()`

| Field | Value |
|-------|-------|
| **Legacy file** | `apex_cap_score.py` |
| **Legacy function** | `compute_apex_cap_score()` |
| **Current responsibility** | Sort-key bonus for Tier D candidates (max +8) to help them compete in the flat Apex cap |
| **Replacement component** | `quota_allocator.py` — route-aware quota groups; structural candidates get protected slots, not just a score nudge |
| **Replacement status** | Not started (Sprint 2) |
| **Shadow/advisory proof** | Not started |
| **Test coverage** | `test_tier_d_visibility.py` tests the bonus logic — must be rewritten for quota model |
| **Retirement decision** | Retire when quota allocator is production-stable and shadow comparison shows equivalent structural candidate survival |
| **Safe removal phase** | After Sprint 2 shadow comparison passes |
| **Rollback risk** | Medium — removing the bonus without quota will drop Tier D survival rate immediately |
| **Owner notes** | Do not touch during Day 1–7. The bonus is the only current protection for position candidates. |

---

### REG-002 — `scanner.py` `get_dynamic_universe()` (Tier A/B/C/D merge)

| Field | Value |
|-------|-------|
| **Legacy file** | `scanner.py:363–462` |
| **Legacy function** | `get_dynamic_universe()` |
| **Current responsibility** | Merges Tier A (hardcoded floor), Tier B (promoter), Tier C (sector rotation), Tier D (position research) into one unlabelled symbol set |
| **Replacement component** | `universe_builder.py` → reads `active_opportunity_universe.json`; `intelligence_adapters.py` reads existing tiers as inputs |
| **Replacement status** | Not started (Day 4) |
| **Shadow/advisory proof** | Not started |
| **Test coverage** | `test_scanner.py` — tests universe composition and tier logic; must be updated to reflect adapter pattern |
| **Retirement decision** | Retire tier merge logic only when `enable_active_opportunity_universe_handoff=True` and production handoff is stable |
| **Safe removal phase** | Production handoff phase (not in this work packet) |
| **Rollback risk** | High — this is the core live execution path. Never remove before production handoff is fully proven. |
| **Owner notes** | Do not modify during Day 1–7. Read as adapter input only. |

---

### REG-003 — `universe_position.py` as a direct universe source

| Field | Value |
|-------|-------|
| **Legacy file** | `universe_position.py` |
| **Legacy function** | `get_position_research_universe()` — called directly from `scanner.py:437` |
| **Current responsibility** | Provides Tier D symbols directly into the scan universe |
| **Replacement component** | `intelligence_adapters.py` reads `position_research_universe.json` and contributes Tier D names with `reason_to_care=structural` and `route=position` labels to the Universe Builder |
| **Replacement status** | Not started (Day 7) |
| **Shadow/advisory proof** | Not started |
| **Test coverage** | `test_universe_position.py` — tests discovery scoring, archetype matching; preserves after adapter |
| **Retirement decision** | Convert to adapter-only pattern when production handoff is enabled |
| **Safe removal phase** | After production handoff is stable |
| **Rollback risk** | Medium — currently Tier D's only path to the scan universe |
| **Owner notes** | Read-only adapter in Day 7. Do not modify the module itself. |

---

### REG-004 — `universe_promoter.py` as a direct universe source

| Field | Value |
|-------|-------|
| **Legacy file** | `universe_promoter.py` |
| **Legacy function** | `load_promoted_universe()` — called from `scanner.py:402` |
| **Current responsibility** | Provides Tier B (daily promoted) symbols directly into the scan universe |
| **Replacement component** | `intelligence_adapters.py` reads `daily_promoted.json` and contributes catalyst/attention candidates with appropriate labels to the Universe Builder |
| **Replacement status** | Not started (Day 7) |
| **Shadow/advisory proof** | Not started |
| **Test coverage** | `test_universe_promoter.py` — tests promotion scoring; preserves after adapter |
| **Retirement decision** | Convert to adapter-only pattern when production handoff is enabled |
| **Safe removal phase** | After production handoff is stable |
| **Rollback risk** | Medium — provides the daily-active catalyst layer |
| **Owner notes** | Read-only adapter in Day 7. Promoter's own scheduled job continues unchanged. |

---

### REG-005 — `theme_tracker.py` symbol lists as universe source

| Field | Value |
|-------|-------|
| **Legacy file** | `theme_tracker.py` |
| **Current responsibility** | Defines 9 themes with symbol lists; used by catalyst engine for news monitoring; no direct universe construction role |
| **Replacement component** | `data/intelligence/thematic_roster.json` — consolidates theme symbol lists with route biases and liquidity classes; `intelligence_adapters.py` reads `theme_tracker.py` as an additional approved source |
| **Replacement status** | Not started (Day 2 for thematic_roster.json; Day 7 for adapter) |
| **Shadow/advisory proof** | Not started |
| **Test coverage** | `test_theme_tracker.py` — preserves; theme_tracker.py itself is not retired, only its symbol lists are consolidated into thematic_roster.json |
| **Retirement decision** | theme_tracker.py is not retired — it serves the catalyst engine. Symbol list duplication is resolved by having thematic_roster.json as the canonical source and the adapter reading both. |
| **Safe removal phase** | N/A — theme_tracker.py is not being retired, only its universe-source role is removed |
| **Rollback risk** | Low — adapter pattern, no production change |
| **Owner notes** | Do not modify theme_tracker.py during Day 1–7. |

---

### REG-006 — `bot_trading.py` favourites-as-manual-conviction

| Field | Value |
|-------|-------|
| **Legacy file** | `bot_trading.py:1469–1488` |
| **Current responsibility** | `data/favourites.json` serves as the manual conviction list — added to universe before scoring |
| **Replacement component** | Universe Builder will have an explicit `manual_conviction` quota group reading from the same source; route tag = `manual_conviction`; always protected |
| **Replacement status** | Not started (Sprint 2) |
| **Shadow/advisory proof** | Not started |
| **Test coverage** | No dedicated test for favourites mechanism — covered implicitly in pipeline tests |
| **Retirement decision** | Favourites pin logic in bot_trading.py stays until production handoff |
| **Safe removal phase** | After production handoff is stable |
| **Rollback risk** | Low — data source (`favourites.json`) unchanged |
| **Owner notes** | Current 13 favourites: ASTS, GLD, IBIT, USO, SPY, QQQ, NVDA, TSLA, AAPL, HIMS, NBIS, MU, ONDS |

---

## Tests to Monitor (Not Delete)

These tests assert behaviour tied to the current architecture. They will need to be rewritten (not deleted) when the new architecture replaces the component they test. Until then, they must continue to pass.

| Test File | Current Role | Migration Trigger |
|-----------|-------------|-------------------|
| `test_tier_d_visibility.py` | Tests Tier D survival through Apex cap (bonus logic) | Rewrite when quota_allocator.py replaces apex_cap_score.py |
| `test_tier_d_evidence_report.py` | Tests tier_d_funnel.jsonl telemetry | Update when funnel telemetry is extended with shadow comparison |
| `test_scanner.py` | Tests get_dynamic_universe() tier composition | Update when universe_builder.py takes over |
| `test_universe_promoter.py` | Tests Tier B promotion | Update when promoter becomes an adapter input |
| `test_universe_position.py` | Tests Tier D discovery/archetype | Update when position research becomes an adapter input |
| `test_theme_tracker.py` | Tests theme definitions | Update when thematic_roster.json is canonical |

---

## Tests That Must Be Preserved (Safety Tests)

These tests protect production execution paths. They must never be deleted, only updated if the underlying execution path changes.

| Test File | What It Protects |
|-----------|-----------------|
| `test_orders.py` | Order placement gates |
| `test_orders_core.py` | Pre-execution validation |
| `test_orders_execute.py` | Order execution flow |
| `test_orders_guard.py` | Duplicate order prevention |
| `test_orders_regression.py` | Regression on execution path |
| `test_apex_live_execute_path.py` | Apex → execution wiring |
| `test_apex_migration_guards.py` | Apex migration safety |
| `test_risk.py` | Risk condition gates |
| `test_entry_gate.py` | Entry gate logic |
| `test_flatten_all_hardened.py` | EOD forced flat |
| `test_ibkr_reconciler.py` | Broker reconciliation |
| `test_event_log_and_training_store.py` | Persistence integrity |
| `test_positions_persistence.py` | Position state |
| `test_position_closed_completeness.py` | Position close completeness |
| `test_fill_watcher.py` | Fill handling |
| `test_duplicate_order_guard.py` | Duplicate prevention |
| `test_drawdown_brake.py` | Drawdown protection |
| `test_trailing_stop.py` | Trailing stop logic |
| `test_sl_lifecycle.py` | Stop loss lifecycle |
| `test_tranche_exits.py` | Tranche exit logic |

---

## Dead Code Identified (Day 1)

No dead code identified yet. Will be populated as audit deepens and new modules are built.

---

## Register Update Log

| Date | Action | Notes |
|------|--------|-------|
| 2026-05-05 | Created | Day 1 audit — 6 components identified, safety test list compiled |
| 2026-05-05 | Day 2 update | REG-005 replacement status updated: thematic_roster.json created (canonical). No new duplicate logic introduced. `macro_transmission_matrix.py` has no legacy counterpart — this is net-new architecture. |
| 2026-05-05 | Day 3 update | `candidate_resolver.py` is net-new — no legacy counterpart. `economic_candidate_feed.json` is net-new output file. No duplicate logic introduced. Validator extended to cover feed; existing validator tests unaffected. No production code or tests removed. |
| 2026-05-05 | Day 4 update | `universe_builder.py` is net-new shadow builder — reads scanner.py constants + daily_promoted.json + position_research_universe.json + favourites.json read-only. No mutations. `active_opportunity_universe_shadow.json` is net-new shadow file. REG-001/REG-002 retirement criteria not yet met. No duplicate logic. No production code or tests removed. |
| 2026-05-05 | Day 5 update | `compare_universes.py` is net-new comparison module — reads shadow + current sources read-only, writes to data/universe_builder/ only. No legacy counterpart. Validator extended with `validate_comparison()` and `validate_report()`. No production code or tests removed. |
| 2026-05-05 | Day 6 update | 4 new slices added: semiconductors, banks (conditional), energy, defence. `transmission_rules.json` → 5 rules. `theme_taxonomy.json` → 5 themes. `thematic_roster.json` → 5 rosters. `candidate_resolver.py` now fires all 5 drivers by default with per-theme confidence threading (banks = 0.62 conditional). `universe_builder.py` adds `quota_pressure_diagnostics` (demand vs capacity by theme/source) and `source_collision_report` (per-symbol source priority tracking with `source_path_excluded_but_symbol_preserved` flag). `compare_universes.py` adds `quota_pressure_analysis`, `source_collision_analysis`, `economic_slice_analysis`. Watch Item 1 resolved: structural quota pressure now fully instrumented (21 economic demand + 150 Tier D = 171 total, 151 overflow). Watch Item 2 resolved: NVDA/ASTS/MU/NBIS tracked as `source_path_excluded_but_symbol_preserved=true`. 5 stale test assertions updated across test_intelligence_day2/4/5 (changed counts now reflect 5-rule reality). 296 tests passing. No production code or tests removed. |
| 2026-05-05 | Day 7 update | `intelligence_adapters.py` is net-new — 9 read-only adapters for existing bot sources. Adapter safety contract: `side_effects_triggered=false`, `live_data_called=false`, no network calls, no spawned threads, no source file mutations. `source_adapter_snapshot.json` is net-new output file (adapters_total=9, adapters_available=7, adapters_unavailable=1, adapters_skipped=1, adapter_symbols_read_total=1376, adapter_unique_symbols_read=1028). `universe_builder.py` updated: reads adapter snapshot (pure JSON read — no adapter module imports triggered), step 4.5 catalyst ingestion with approved-source guard (`catalyst_symbol_not_in_approved_source` exclusion for unapproved symbols), post-processing enrichment loop (adds legacy_theme_tracker_read_only, overnight_research_read_only, committed_universe_read_only labels), `adapter_usage_summary` in shadow universe, `freshness_status` → `static_bootstrap_day7`. `compare_universes.py` updated: `adapter_impact_analysis` with real symbol counts (total=1376, unique=1028) — not fake adapter count. Report title → Day 7. `intelligence_schema_validator.py` updated: `validate_adapter_snapshot()` function, safety flag checks on `adapter_usage_summary` and `adapter_impact_analysis`. No locked production files modified (scanner.py, theme_tracker.py, catalyst_engine.py, overnight_research.py, universe_position.py, universe_committed.py, market_intelligence.py, bot_trading.py, guardrails.py, orders_core.py all confirmed unmodified). 2 stale test assertions updated in test_intelligence_day6.py (freshness_status and report_title now accept Day 6 or Day 7). 350 intelligence tests passing. live_output_changed=false across all files. No production code or tests removed. |
| 2026-05-05 | Sprint 2 update | `route_tagger.py` is net-new — pure deterministic route assignment (RouteContext, RouteDecision, assign_route). 10 ordered rules: held→held, manual_conviction→manual_conviction, etf_proxy→watchlist, direct_beneficiary→route_hint[0] (position or swing per theme), second_order→swing, catalyst→swing, Tier B→intraday_swing, Tier A→watchlist, do_not_touch, fallback watchlist. No live data, no LLMs, no side effects. `quota_allocator.py` is net-new — pure quota allocation (QuotaCandidate, AllocationResult, allocate). Enforces all quota groups: held/manual protected, structural max 20, catalyst max 30, attention+current_source_unclassified shared cap 15, etf_proxy max 10, total 50. Priority-ordered allocation, first-claim dedup, full inclusion/exclusion logs, quota pressure diagnostics, source collision report. `universe_builder.py` refactored: build() now uses route_tagger.assign_route() per candidate and quota_allocator.allocate() on the full list — inline _add() and inline quota counters removed. Approved-source guard preserved (pre-allocation). Post-processing enrichment and adapter_usage_summary preserved. `freshness_status` → `static_bootstrap_sprint2`. Route improvement: Tier B candidates now correctly tagged `intraday_swing` (was `watchlist` — shadow-only change, no production impact). `compare_universes.py` report title → Sprint 2. `tests/test_intelligence_sprint2.py` created — 63 tests. 4 stale assertions updated across test_intelligence_day6.py. 413 intelligence tests passing. Smoke 16/16. live_output_changed=false. No production code or tests removed. REG-001 (apex_cap_score replacement): quota_allocator.py is the structural protection foundation required before quota_allocator can replace the bonus — Sprint 2 complete for allocator. Shadow comparison required (Sprint 2 acceptance condition not yet met for REG-001 full retirement). |
| 2026-05-06 | Sprint 4A update | `intelligence_engine.py` is net-new — reads 9 local shadow/intelligence files (read-only), infers 16 macro driver states using conservative local-shadow logic only, writes `data/intelligence/daily_economic_state.json` and `data/intelligence/current_economic_context.json`. Safety contract: `no_live_api_called=True`, `broker_called=False`, `env_inspected=False`, `raw_news_used=False`, `llm_used=False`, `broad_intraday_scan_used=False`, `live_output_changed=False` — all hardcoded, never read from .env or config. Driver states: `active_shadow_inferred` (ai_capex_growth — local rule + candidate feed evidence), `watch_shadow_inferred` (corporate_capex, credit, risk_appetite, geopolitics, interest_rates, bonds_yields, oil_energy, volatility, sector_rotation), `unavailable` with `unavailable_reason` (inflation, growth, usd, valuation, liquidity, consumer_behaviour — no local shadow evidence). `intelligence_schema_validator.py` extended: `validate_daily_economic_state()` and `validate_current_economic_context()` functions added; both added to `validate_all()`; safety flag validation fails on wrong values; driver unavailable_reason enforcement; route_adjustments group completeness check; no executable flag in context. `scripts/validate_intelligence_files.py` docstring updated to reflect Sprint 4A files (logic unchanged — validate_all() extension handled everything). `tests/test_intelligence_sprint4a.py` created — 40 tests across 7 classes. Testing policy change: tiered testing now applies from Sprint 4A onward — full suite only before advisory/handoff/production-module changes; shadow sprints use sprint tests + intel regression + validator + smoke. No production code modified. No tests removed. 40/40 Sprint 4A tests passing. 451/451 intelligence regression tests (Day 2–7 + Sprints 1–3) passing. 10/10 validator checks passing. 4/4 smoke passing. live_output_changed=false across all files. |
| 2026-05-06 | Sprint 5A update | `backtest_intelligence.py` is net-new — local fixture-based backtest and ablation framework for the Economic Intelligence Layer. Reads 8 local shadow/intelligence files read-only; writes 5 output files to `data/intelligence/backtest/`. No live APIs, no broker, no LLM, no .env, no production modules imported. Safety contract: all 7 flags hardcoded, never from .env. **Regime fixture** (6 scenarios): ai_infrastructure_tailwind, credit_stress_watch, risk_off_rotation, oil_supply_shock, rates_rising_banks_conditional, mixed_regime — all 6 pass (23 total checks pass, 0 fail). **Theme activation fixture** (6 scenarios): ai_capex_active, credit_stress_active, risk_off_active, oil_supply_shock, geopolitical_risk, missing_evidence — all 6 pass; false_activation_count=0; headwind_handled_correctly=True; crowded_handled_correctly=True. **Candidate feed ablation** (7 variants): baseline, no_economic_candidate_feed, no_route_tagger, no_quota_allocator, no_headwind_pressure_candidates, no_manual_protection, no_attention_cap — key findings confirmed: removing quota shows flat-pool risk (attention_cap=False), removing route_tagger drops all position/swing routing, removing economic feed reduces reason-to-care by 30 candidates, removing manual protection loses 13 protected symbols, removing attention cap exposes 102 attention demand vs 15 cap. **Risk overlay fixture** (4 scenarios): credit_stress_rising, risk_off_rotation, oil_shock, broad_risk_off_crowded — all 4 pass; headwind_candidates_executable=False; structural_displaced_by_attention=False; attention_cap_respected=True; no_order_instructions=True. **Summary**: overall_status=pass, decision_gate=pass_for_next_shadow_sprint, blockers=0, warnings=0. **Validator extended**: 5 new validator functions (validate_regime_fixture_results, validate_theme_activation_fixture_results, validate_candidate_feed_ablation_results, validate_risk_overlay_fixture_results, validate_intelligence_backtest_summary) added to intelligence_schema_validator.py and wired into validate_all(). All 17 validator checks pass. `tests/test_intelligence_sprint5a.py` created — 47 tests across 9 classes. No production code modified. No tests removed. Full suite NOT run (tiered policy — shadow/report-only sprint, no production module touches). 47/47 Sprint 5A tests passing. 544/544 intelligence regression tests (Day2–Sprint4B) passing. 17/17 validator checks passing. 4/4 smoke passing. live_output_changed=false across all files. Newly obsolete assumptions: flat-pool risk was previously unmeasured — ablation now provides quantified evidence (102 attention demand vs 15 cap, 180 structural demand vs 20 capacity). This is the first evidence-based justification for quota_allocator.py's design. No code removed. No duplicate logic. **Full suite not run in Sprint 5A (tiered policy — shadow/report-only sprint). Known full-suite baseline remains Sprint 3 patch: 30 pre-existing failures (test_atr_sizer_integration.py ×8, test_orders_core.py ×3, test_reconnect.py ×10, test_tier_d_visibility.py ×2, test_tranche_exits.py ×7). No evidence of new full-suite failures because full suite was not run under tiered policy. The "2 pre-existing trailing stop test failures" referenced in the Sprint 5A end-of-sprint report was an incorrect override of the Sprint 3 baseline — corrected here. Sprint 3 patch baseline of 30 pre-existing failures stands.** |
| 2026-05-06 | Sprint 6A update | **Offline Advisory Report delivered.** `advisory_reporter.py` is net-new — reads 11 local shadow/intelligence files read-only; generates `data/intelligence/advisory_report.json`. No production modules imported (AST-verified: scanner, bot_trading, market_intelligence, orders_core, guardrails, catalyst_engine, overnight_research, agents, sentinel_agents, bot_ibkr, learning — all absent from import graph). No live APIs, no broker, no LLM, no .env, no raw news, no broad intraday scan. **Report contents:** `advisory_summary` (current=235 candidates, shadow=50, overlap=27, advisory_include=13, advisory_watch=37, advisory_defer=50, advisory_unresolved=0, route_disagreements=17, missing_shadow=23); `candidate_advisory` (100 per-symbol records — all executable=false, all order_instruction=null, non_executable_all=true); `route_disagreements` (17 disagreements with current-vs-shadow route pairs — all executable=false); `unsupported_current_candidates` (50 tracked symbols with no shadow support); `missing_shadow_candidates` (23 shadow candidates not in current pipeline); `tier_d_advisory` (150 current Tier D, 5 in shadow, 145 excluded; structural quota full at 20 is primary blocker; preserved_through_manual_or_other_source tracked); `structural_quota_advisory` (demand=180, capacity=20, overflow=160, production_change_required=false, recommendation=keep_current_shadow_cap_until_more_evidence); `risk_theme_advisory` (headwind_candidates=IWM, executable_headwind_candidates=false, short_or_hedge_instruction_generated=false); `manual_and_held_advisory` (manual_total=13, all protected; held_total=0 expected in static_bootstrap mode). **Validator extended**: `validate_advisory_report()` added to `intelligence_schema_validator.py`; wired into `validate_all()`; validates all required top-level fields, safety flags, candidate record completeness, executable=false enforcement, order_instruction=null enforcement, non_executable_all=true, production_change_required=false, executable_headwind_candidates=false. `scripts/validate_intelligence_files.py` unchanged — validate_all() extension covers advisory_report automatically. 20/20 validator checks pass. **Tests**: `tests/test_intelligence_sprint6a.py` created — 36 tests across 8 classes (TestAdvisoryReporterExists ×2, TestAdvisoryReportValidates ×2, TestCandidateAdvisory ×5, TestRequiredSections ×7, TestAdvisoryLogicConstraints ×5, TestQuotaAdvisoryConstraints ×2, TestAdvisoryForbiddenPaths ×8, TestAdvisoryReporterNoProductionImports ×1, TestIntelligenceRegressionSpotCheck ×4). 36/36 Sprint 6A tests passing. 625/625 intelligence regression (Day2–Sprint5B) passing. 20/20 validator checks passing. 4/4 smoke passing. No production code modified. No tests removed. live_output_changed=false. **Full suite not run (tiered policy — offline/shadow-only sprint, no production module touches). Known full-suite baseline remains Sprint 3 patch: 30 pre-existing failures.** |
| 2026-05-06 | Sprint 5B update | **Historical Replay Framework delivered.** `backtest_intelligence.py` extended: `_serialise_fixture()` helper added to convert Python `set` types in `expected_theme_states` to sorted JSON-serialisable lists; `_build_historical_fixtures_doc()` builds 6 date-anchored historical scenarios (2022-06 rate/inflation shock, 2022-02 Ukraine/oil geopolitical shock, 2023-05 AI infrastructure emergence, 2023-10 rate peak/growth pressure, 2024-08 rate-cut pivot/selective risk-on, 2020-03 COVID liquidity shock); `_run_historical_replay()` evaluates each scenario against local engine using the same deterministic fixtures approach (no live APIs, no LLM, no .env, no production module imports); `_build_summary()` signature extended with optional `historical_result` parameter; `generate_backtest_results()` writes 2 additional files (`historical_replay_fixtures.json`, `historical_replay_results.json`) and regenerates `intelligence_backtest_summary.json` with `historical_replay_status`. **Output summary**: `historical_replay_fixtures.json` — 6 scenarios, 15 documented engine_limitations across scenarios (inflation/liquidity/usd/growth always unavailable, no directionality in state vocab, theme taxonomy covers only 8 themes). `historical_replay_results.json` — 6/6 scenarios pass, pass_rate=1.0, all `forbidden_outputs_checked` entries = false. `intelligence_backtest_summary.json` regenerated: overall_status=pass_with_warnings (1 warning: 15 known engine limitations), decision_gate=pass_for_next_shadow_sprint, historical_replay: 6/6 passed, blockers=0. **Validator extended**: `validate_historical_replay_fixtures()` and `validate_historical_replay_results()` added to `intelligence_schema_validator.py`; `pass_but_more_replay_needed` added to `_VALID_DECISION_GATES`; `historical_replay_status` presence check added to `validate_intelligence_backtest_summary()`; both new validators wired into `validate_all()`. All 19 validator checks pass. **Tests**: `tests/test_intelligence_sprint5b.py` created — 34 tests across 9 classes (TestHistoricalReplayFixturesFile ×4, TestHistoricalReplayResultsFile ×2, TestHistoricalReplayScenarios ×6, TestHistoricalReplayForbiddenPaths ×5, TestHistoricalReplaySafetyFlags ×6, TestSummaryHistoricalReplayStatus ×2, TestSprint5ARegression ×3, TestIntelligenceRegressionSpotCheck ×4, TestSmokeSpotCheck ×2). 34/34 Sprint 5B tests passing. 591/591 intelligence regression (Day2–Sprint5A) passing. 19/19 validator checks passing. 4/4 smoke passing. No production code modified. No tests removed. live_output_changed=false across all files. **Full suite not run (tiered policy — shadow/report-only sprint). Known full-suite baseline remains Sprint 3 patch: 30 pre-existing failures. No new full-suite failures introduced (no production module touched).** **Known engine limitations documented (not defects — scope boundary):** inflation, liquidity, usd, valuation, consumer_behaviour, growth always `unavailable` in Sprint 4A/B; no directionality vocab; theme taxonomy covers 8 themes only (no high_multiple_growth, gold_safe_haven). Historical replay validates the framework operates correctly within its documented scope; expansion of driver coverage is deferred to Sprint 6+ advisory mode and Sprint 5C (if scheduled). |
| 2026-05-06 | Sprint 4B update | **Theme Activation Engine and Thesis Store delivered.** (1) `theme_activation_engine.py` is net-new — reads `transmission_rules.json`, `theme_taxonomy.json`, `thematic_roster.json`, `economic_candidate_feed.json`, `daily_economic_state.json`, `active_opportunity_universe_shadow.json` read-only; applies state-machine logic (dormant → watchlist → activated/strengthening/weakening/crowded) using driver states × rule firing × candidate evidence × quota pressure; writes `data/intelligence/theme_activation.json`. State output: activated=2 (data_centre_power, semiconductors — ai_capex_growth active + candidates present), weakening=1 (small_caps — headwind theme), crowded=2 (structural quota binding + tailwind + excluded candidates), watchlist=3 (banks, energy, defence — watch drivers or candidates only), dormant=0. Safety contract: `no_live_api_called=True`, `broker_called=False`, `env_inspected=False`, `raw_news_used=False`, `llm_used=False`, `broad_intraday_scan_used=False`, `live_output_changed=False` — hardcoded, never from .env. (2) `thesis_store.py` is net-new — reads `theme_activation.json`, `current_economic_context.json`, `economic_candidate_feed.json`, `active_opportunity_universe_shadow.json` read-only; builds per-theme thesis records using deterministic template only (no LLM); compares against prior `thesis_store.json` to compute `status_change` (created/upgraded/downgraded/no_change); writes `data/intelligence/thesis_store.json`. Deterministic template: `"Theme {theme_id} is {state} because drivers {drivers} fired rules {rules}. Candidate exposure is {candidate_count} symbols, with {candidates_in_shadow} currently in the shadow universe. Key risks are {risks}. Confirmation still required: {confirm}."` ThesisStore class provides read-only interface: `load(path)`, `get(theme_id)`, `all()`, `active()`, `count()`. First-run result: 8 theses all `new`. Second-run (same inputs): all `unchanged`. (3) `compare_universes.py` updated: `economic_context_summary` section added to both `compare()` (raw comparison dict) and `build_report()` (universe builder report); reads Sprint 4A/4B output files gracefully (no-op when files absent); economic_context_summary is reporting-only — does NOT change allocation, candidates, quotas, or live bot behaviour. (4) `intelligence_schema_validator.py` extended: `validate_theme_activation()` and `validate_thesis_store()` functions added; both added to `validate_all()`; validate_theme_activation checks safety flags, mode, per-theme required fields, valid state vocabulary, confidence range, evidence/risk_flags/invalidation lists, `used_live_data=False`; validate_thesis_store checks safety flags, mode, per-thesis required fields, valid status vocabulary, non-empty current_thesis, evidence/affected_symbols lists, `used_live_data=False`. All 12 validator checks pass. (5) `tests/test_intelligence_sprint4b.py` created — 53 tests across 10 classes: TestThemeActivationGeneration (2), TestThemeActivationSchema (6), TestThesisStoreSchema (4), TestForbiddenPathsSprint4B (14), TestThemeActivationContent (10), TestThesisStoreContent (6), TestThesisStoreSecondRun (2), TestEconomicContextSummaryInReport (4), TestProductionNoTouch (2), TestPriorSuiteRegression (4). No production code modified (scanner.py, theme_tracker.py, catalyst_engine.py, overnight_research.py, universe_position.py, universe_committed.py, market_intelligence.py, bot_trading.py, guardrails.py, orders_core.py all untouched — confirmed by AST import check). No tests removed. 53/53 Sprint 4B tests passing. 491/491 intelligence regression tests (Day 2–7 + Sprints 1–4A) passing. 12/12 validator checks passing. 4/4 smoke passing. live_output_changed=false across all files. No duplicate logic introduced — theme activation and thesis store are net-new architecture components with no legacy counterparts. |
| 2026-05-05 | Sprint 3 update | **Headwind theme support added end-to-end.** (1) `candidate_resolver.py` extended: `generate_feed()` now builds `headwind_theme_ids` from `fired_rule.output_type == "theme_headwind"` over `result.transmission_rules_fired`; `resolve()` accepts `headwind_theme_ids` param; per-theme `is_headwind` flag routes headwind themes to `role="pressure_candidate"`, `route_hint=["watchlist"]`, confidence discounted −0.30, reason_to_care="headwind_pressure_watchlist"; `feed_summary` extended with `pressure_candidates`, `headwind_themes`, `headwind_candidates_executable=False`; active drivers `credit_stress_rising` and `risk_off_rotation` added to default driver set; beneficiary type entries added for quality/defensive/small_caps roster symbol types. (2) `route_tagger.py` extended: Rule 9 added (`pressure_candidate` or `headwind_pressure_watchlist` → route=watchlist, allowed_routes=["watchlist"], required_confirmations=["headwind_monitoring_only_no_execution"]); do_not_touch demoted to Rule 10; fallback to Rule 11; docstring updated. (3) `quota_allocator.py` extended: `QuotaCandidate` gains `driver` and `reason_to_care` fields; 5 new structural overflow tracking dicts (`structural_overflow_by_theme`, `_by_driver`, `_by_reason`, `_by_route`, `_by_source`) populated on every structural overflow event and exposed in `quota_pressure_diagnostics["structural_position"]`. (4) `universe_builder.py` extended: `_from_economic_candidate()` gains `pressure_candidate` case (bucket_type=attention, quota_group=attention, route=watchlist, transmission_direction=headwind); `universe_summary` gains 6 Control 2 route-metric-distinction fields: `position_route_count`, `structural_quota_group_count`, `structural_reason_to_care_count`, `tier_d_structural_source_count`, `structural_watchlist_count`, `structural_swing_count`; `freshness_status` → `static_bootstrap_sprint3`. (5) `compare_universes.py` extended: `_RISK_OFF_THEMES = {"quality_cash_flow", "defensive_quality", "small_caps"}` sentinel; `risk_off_analysis` section added to comparison and report dicts (quality_cash_flow/defensive_quality/small_caps candidate counts, shadow counts, headwind_candidates_executable=False, risk_off_symbols_preserved/lost); `route_metric_distinction` pulled from shadow universe_summary and forwarded to report; report title → Sprint 3. (6) `intelligence_schema_validator.py` extended: `is_headwind_roster` exception — `headwind_roster=True` rosters with `core_symbols=[]` are valid (not an error). (7) `data/intelligence/thematic_roster.json` fixed: `quality_cash_flow.minimum_liquidity_class` corrected from `"very_high"` (invalid) to `"high"`. (8) **New test file**: `tests/test_intelligence_sprint3.py` — 38 tests across 9 classes covering credit stress tailwind (quality_cash_flow), credit stress headwind (small_caps/IWM), risk-off tailwind (defensive_quality), headwind constraints (non-executable/watchlist/not structural quota), route tagger Rule 9, Sprint 3 ETF proxies, no-forbidden-paths invariants, route metric distinction fields, and risk_off_analysis sections. (9) **Stale test assertions updated**: test_intelligence_day6.py (10 assertions updated for 8-rule/8-theme/8-roster reality and Control 2 fields), test_intelligence_day7.py (3 assertions updated for sprint3 freshness_status and title), test_intelligence_sprint2.py (1 assertion updated). (10) **Assumptions locked**: headwind candidates are watchlist-only, never executable, never short/hedge execution vehicles; pressure_candidate role consumes attention quota not structural_position quota; empty `core_symbols` is valid when `headwind_roster=True`; route_metric_distinction is a measurement-only distinction, not a quota change; no short exposure is introduced by headwind monitoring. No production code modified. No tests deleted. live_output_changed=false across all 4 output files. 451 intelligence tests passing (413 prior + 38 new). |
