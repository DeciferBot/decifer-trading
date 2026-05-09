# Codebase Bloat Retirement Audit
**Branch:** `cleanup/codebase-bloat-retirement-audit`  
**Generated:** 2026-05-09T11:49:37.217847+00:00  
**Scope:** Read-only classification — nothing deleted, no production logic touched.

---

## Audit Results

| Metric | Count |
|--------|-------|
| **Total files classified** | 290 |
| **Live runtime modules** | 114 |
| **Test files** | 108 |
| **Scripts classified** | 24 |
| **Migration tools** | 6 |
| **Advisory-only modules** | 20 |
| **Shadow-only modules** | 6 |
| **Validation-only modules** | 10 |
| **Backtest-only modules** | 4 |
| **Scheduled workers** | 5 |
| **Adapter-only modules** | 5 |
| **Production runtime candidates** | 1 |
| **Unknown / requires review** | 6 |
| **Legacy scanner path** | 1 |
| **Validator result** | PASS — all 40 intelligence files valid |
| **Smoke result** | 9 passed, 1 skipped, 0 failed (5.25s) |

---

## Section A: Top 20 Bloat Concerns

### 1. signals_log.jsonl growing unboundedly (14MB)
**Path:** `data/signals_log.jsonl`  
**Reason:** Every scan cycle appends all dimension scores. No rotation policy. Will hit disk limits before cloud launch.  
**Action:** Implement log rotation or sampling; cap at 7-day rolling window.

### 2. apex_shadow_log.jsonl growing unboundedly (11MB)
**Path:** `data/apex_shadow_log.jsonl`  
**Reason:** USE_APEX_V3_SHADOW=True logs every shadow decision. 11MB in paper mode — will be much larger live.  
**Action:** Rotate daily or cap at 10K entries. Decide shadow lifetime after activation.

### 3. intelligence_schema_validator.py is 158KB — largest single file in repo
**Path:** `intelligence_schema_validator.py`  
**Reason:** Not in live import chain. Pure validation tool. 158KB is a significant maintenance burden with no live runtime value.  
**Action:** Cloud exclusion. Move to scripts/ or a CI-only directory.

### 4. 23 intelligence sprint/day test files (test_intelligence_day2-7, sprint2-7c)
**Path:** `tests/test_intelligence_day*.py + tests/test_intelligence_sprint*.py`  
**Reason:** These files tracked weekly development progress across the intelligence-first sprint series. They are not permanent regression tests. They add ~15K+ lines to the test suite and inflate test run time.  
**Action:** Move to tests/archive/ or delete after intelligence-first activation is confirmed. Keep sprint7b and sprint7c (smoke marked).

### 5. backtest_intelligence.py is 100KB — second largest file
**Path:** `backtest_intelligence.py`  
**Reason:** Offline backtesting only. Not in live import chain. 100KB of rarely-run code inflates container size.  
**Action:** Cloud exclusion. Keep locally for research. Never deploy to live container.

### 6. 5 one-time migration scripts still in scripts/
**Path:** `scripts/migrate_trades_to_training_store.py, scripts/backfill_may5_trades.py, scripts/backfill_position_closed.py, scripts/reconcile_trades_json.py, scripts/recover_pattern_ids.py`  
**Reason:** All migrations completed (2026-04-28). These scripts serve no future purpose and create false confidence that they might be run again.  
**Action:** Delete now. One exception: scripts/rebuild_positions_from_intents.py is a disaster recovery tool — keep.

### 7. apex_decision_audit.jsonl at 4MB and growing
**Path:** `data/apex_decision_audit.jsonl`  
**Reason:** Every Apex decision is audited. No size cap. This duplicates information in apex_conversation_log.jsonl.  
**Action:** Implement rotation or deduplication with apex_conversation_log.jsonl.

### 8. test_orders_execute.py is 100% TODO stubs — no real assertions
**Path:** `tests/test_orders_execute.py`  
**Reason:** 11 test functions all contain only TODO comments. This file provides false confidence of coverage for critical execution paths (stop-loss, bracket orders, options contract selection, partial fills).  
**Action:** Implement the 11 test cases OR delete the file. Do not leave TODO-only test files in CI.

### 9. Chief-Decifer-recovered/ contains compiled .pyc bytecode in git
**Path:** `Chief-Decifer-recovered/__pycache__/, Chief-Decifer-recovered/signals/__pycache__/, Chief-Decifer-recovered/panels/__pycache__/`  
**Reason:** __pycache__ directories and .pyc files should never be committed to git. They are machine-specific Python 3.11 bytecode.  
**Action:** Add to .gitignore. Delete committed bytecode files. This is a housekeeping issue.

### 10. Two macOS app bundles checked into git (Decifer Trading.app, Decifer.app)
**Path:** `Decifer Trading.app/, Decifer.app/`  
**Reason:** Binary blobs in git inflate repo size. App bundles should be distributed separately, not version-controlled.  
**Action:** Add to .gitignore. Distribute as separate release artifacts.

### 11. apex_prompt_snapshot.jsonl + apex_response_snapshot.jsonl (3.7MB combined debug artifacts)
**Path:** `data/apex_prompt_snapshot.jsonl, data/apex_response_snapshot.jsonl`  
**Reason:** Debug snapshots only. Already have apex_conversation_log.jsonl as the canonical record. Three overlapping Apex logs.  
**Action:** Consider whether prompt/response snapshots are needed separately from conversation log. If not, disable and remove.

### 12. Advisory layer (advisory_reporter.py + advisory_logger [as advisor] + advisory_log_reviewer.py) — 64KB of flagged-off code
**Path:** `advisory_reporter.py, advisory_log_reviewer.py, intelligence_engine.py, intelligence_adapters.py`  
**Reason:** intelligence_first_advisory_enabled=False. These modules are not called in any live cycle. They represent an entire feature tier that is currently inactive.  
**Action:** Cloud exclusion after intelligence-first activation. Archive or delete advisory_log_reviewer and advisory_reporter post-cutover.

### 13. handoff_publisher_observer.py (39KB) + paper_handoff_comparator.py (37KB) — shadow observation only
**Path:** `handoff_publisher_observer.py, paper_handoff_comparator.py, paper_handoff_builder.py`  
**Reason:** These are shadow-mode only tools for comparing paper vs live handoffs. After activation is confirmed they serve no purpose.  
**Action:** Retire after activation confirmed. Cloud exclusion candidate.

### 14. 6 .fail_* files accumulating in data/live/
**Path:** `data/live/.fail_20260507T*.json`  
**Reason:** Failed handoff attempt records from 2026-05-07. No cleanup mechanism — these will accumulate indefinitely.  
**Action:** Implement automatic cleanup of .fail_* files older than 7 days. Delete the 6 existing ones now.

### 15. tier_d_funnel.jsonl at 2.9MB for Phase 1 evidence only
**Path:** `data/tier_d_funnel.jsonl`  
**Reason:** Tier D Phase 1 evidence collection. After Phase 2 gate decision, this file's evidence purpose is fulfilled. Will grow unboundedly if not capped.  
**Action:** After Phase 2 gate reviewed with Amit, cap to last 1000 entries or archive the current file.

### 16. factor_registry.py is 61KB but not in live import chain
**Path:** `factor_registry.py`  
**Reason:** 61KB factor registry only referenced by intelligence_schema_validator.py (also not in live chain). No live runtime value.  
**Action:** Cloud exclusion. Move to intelligence/offline/ or similar. Never deploy to live container.

### 17. audit_candle_gate.py not imported by entry_gate or any live chain module
**Path:** `audit_candle_gate.py`  
**Reason:** Expected to be used in entry validation, but AST import analysis shows it's not in the live transitive closure. May be a dead code path from an old pipeline.  
**Action:** Investigate: is it called dynamically (importlib) or via bot.py scheduler? If unused, retire.

### 18. candidate_resolver.py has no callers in live chain
**Path:** `candidate_resolver.py`  
**Reason:** Module exists but is not imported by scanner, entry_gate, or any live-chain module. May be a residual from old pipeline or intended for intelligence layer.  
**Action:** Investigate: if not actively called, this is dead code from the pre-Apex pipeline.

### 19. build_brain.py (47KB) is in repo root alongside bot.py
**Path:** `build_brain.py`  
**Reason:** Offline ML training tool at top level. Should be in scripts/ or ml/ directory to make the repo root's intent clearer.  
**Action:** Move to scripts/ or an ml/ subdirectory. Cloud exclusion candidate.

### 20. quota_capacity_calibrator.py (33KB) — calibration already run, results in data/live/
**Path:** `quota_capacity_calibrator.py, tests/test_quota_capacity_calibrator.py`  
**Reason:** Quota calibration was run and 5 scenario results are persisted in data/live/. The calibrator served its purpose but remains at top level.  
**Action:** Move to scripts/ post-activation. Cloud exclusion candidate.

---

## Section B: Duplicate Logic

| Pair | Overlap | Recommendation |
|------|---------|----------------|
| `apex_conversation_log.jsonl` + `apex_decision_audit.jsonl` + `apex_prompt_snapshot.jsonl` + `apex_response_snapshot.jsonl` | Four overlapping Apex audit files; conversation log is sufficient for most purposes | Consolidate to 2: conversation log + decision audit. Remove prompt/response snapshots. |
| `advisory_logger.py` (live chain) + `advisory_reporter.py` + `advisory_log_reviewer.py` (all flagged off) | Advisory layer split across active logger and inactive reporter/reviewer | After cutover: collapse into single advisory module or delete inactive half |
| `handoff_publisher_observer.py` + `paper_handoff_comparator.py` + `paper_handoff_builder.py` | Three shadow/comparison modules with overlapping observation roles | Retire all three after activation confirmed |
| `backtester.py` + `backtest_intelligence.py` | Separate backtesting engine and intelligence system — high coupling | Move together to a `backtest/` subdirectory for clarity |
| `ic_calculator.py` (live) + `ic_validator.py` (not live) + `ic/` package | IC logic split between top-level files and `ic/` package | Consolidate: all IC logic into `ic/` package. Remove top-level duplicates. |
| `universe_committed.py` + `universe_builder.py` + `universe_position.py` + `universe_promoter.py` | Four universe modules at top level with overlapping scope | Group into `universe/` subdirectory post-activation |

---

## Section C: Dead or Stale Tests

| File | Issue | Recommended Action |
|------|-------|-------------------|
| `tests/test_orders_execute.py` | 11 TODO-only test functions — zero real assertions | Implement all 11 or delete immediately. False coverage confidence for CRITICAL execution paths. |
| `tests/test_trailing_stop.py` | 2 pre-existing failures (noted in CLAUDE.md as unrelated to current work) | Fix the 2 failures before cloud deployment. Do not ship with known failures. |

---

## Section D: Temporary Migration Tests

| File | Migration Purpose | Safe to Retire? |
|------|-----------------|----------------|
| `tests/test_backfill_direction.py` | Backfill direction logic | After cutover confirmed |
| `tests/test_safe_download.py` | Safe download: Alpaca-first bar data migration | After cutover confirmed |

---

## Section E: Permanent Regression Tests (Keep Always)

**77 tests** must remain in all CI runs.

| File | Why It's Permanent |
|------|-------------------|
| `tests/test_alpaca_data.py` | Alpaca data regression undetected |
| `tests/test_alpaca_order_guards.py` | Order guard regression undetected |
| `tests/test_alpaca_stream.py` | Stream regression undetected |
| `tests/test_alpha_vantage_client.py` | AV client regression undetected |
| `tests/test_apex_live_execute_path.py` | Execute path regression undetected — CRITICAL |
| `tests/test_apex_migration_guards.py` | Migration regression: deleted code paths could resurface |
| `tests/test_apex_prompt_architecture.py` | Prompt architecture regression undetected |
| `tests/test_atr_sizer_integration.py` | ATR sizing regression undetected |
| `tests/test_bot.py` | Bot startup regression undetected |
| `tests/test_bot_dashboard_data.py` | Dashboard data regression undetected |
| `tests/test_candle_gate.py` | Candle gate regression undetected |
| `tests/test_catalyst_pipeline.py` | Catalyst pipeline regression undetected |
| `tests/test_config.py` | Config regression undetected |
| `tests/test_cycle_check.py` | Cycle check regression undetected |
| `tests/test_dashboard.py` | Dashboard regression undetected |
| `tests/test_dim_flags.py` | Dimension flag regression undetected |
| `tests/test_drawdown_brake.py` | Drawdown brake regression — HIGH RISK if undetected |
| `tests/test_duplicate_order_guard.py` | Duplicate orders possible if regression undetected |
| `tests/test_entry_gate.py` | Entry gate regression undetected |
| `tests/test_event_log_and_training_store.py` | Event log regression — CRITICAL metadata immutability risk |
| `tests/test_execution_agent.py` | Execution monitoring regression undetected |
| `tests/test_fill_watcher.py` | Fill detection regression undetected |
| `tests/test_flatten_all_hardened.py` | EOD flatten regression — HIGH RISK if undetected |
| `tests/test_guardrails.py` | Guardrail regression — HIGH RISK |
| `tests/test_handoff_activation_gate.py` | Activation gate regression undetected |
| `tests/test_handoff_publisher.py` | Publisher regression undetected |
| `tests/test_handoff_wiring_integration.py` | Wiring regression undetected |
| `tests/test_ibkr_reconciler.py` | Reconcile regression — position drift possible |
| `tests/test_ic_calculator.py` | IC calculation regression undetected |
| `tests/test_ic_weighted_direction.py` | IC weighting regression undetected |
| `tests/test_imports.py` | Silent import breakage undetected |
| `tests/test_intelligence_sprint7b.py` | Sprint 7B smoke regression undetected |
| `tests/test_intelligence_sprint7c.py` | Sprint 7C smoke regression undetected |
| `tests/test_learning.py` | Learning module regression undetected |
| `tests/test_margin_exposure.py` | Margin exposure regression — HIGH RISK |
| `tests/test_margin_readiness.py` | Margin gate regression — HIGH RISK |
| `tests/test_ml_engine.py` | ML engine regression undetected |
| `tests/test_mtf_gate.py` | MTF gate regression undetected |
| `tests/test_news.py` | News dimension regression undetected |
| `tests/test_news_sentinel.py` | Sentinel regression undetected |
| `tests/test_options.py` | Options regression undetected |
| `tests/test_options_entries.py` | Options entry regression undetected |
| `tests/test_options_scanner.py` | Options scan regression undetected |
| `tests/test_orders.py` | Order interface regression undetected |
| `tests/test_orders_core.py` | Order execution regression — CRITICAL |
| `tests/test_orders_guard.py` | Guard regression undetected |
| `tests/test_orders_regression.py` | Regression suite regression undetected |
| `tests/test_pass1_robustness.py` | Robustness regression undetected |
| `tests/test_phase_gate.py` | Phase gate regression undetected |
| `tests/test_pm_exit_reason.py` | PM exit reason regression undetected |
| `tests/test_portfolio.py` | Portfolio regression undetected |
| `tests/test_portfolio_optimizer.py` | Optimizer regression undetected |
| `tests/test_position_closed_completeness.py` | Position closure metadata incomplete if regression |
| `tests/test_positions_persistence.py` | Position persistence regression undetected |
| `tests/test_quota_policy_promotion.py` | Quota policy regression undetected |
| `tests/test_reconnect.py` | Reconnect regression — bot goes dark if undetected |
| `tests/test_regime_router.py` | Regime routing regression undetected |
| `tests/test_risk.py` | Risk regression — HIGH RISK if undetected |
| `tests/test_scanner.py` | Scanner regression — trading universe broken |
| `tests/test_schemas.py` | Schema regression undetected |
| `tests/test_signal_dispatch.py` | Signal dispatch regression undetected |
| `tests/test_signal_pipeline.py` | Pipeline regression undetected |
| `tests/test_signals.py` | Signal scoring regression undetected |
| `tests/test_sizing_pipeline.py` | Sizing regression — position sizes wrong if undetected |
| `tests/test_sl_lifecycle.py` | Stop-loss regression — exits may not fire |
| `tests/test_system_interactions.py` | System integration regression undetected |
| `tests/test_telegram_kill_switch.py` | Kill switch regression — cannot halt bot via Telegram |
| `tests/test_theme_tracker.py` | Theme tracker regression undetected |
| `tests/test_thesis_performance.py` | Thesis performance regression undetected |
| `tests/test_tier_d_evidence_report.py` | Tier D evidence regression — Phase 2 gate blocked |
| `tests/test_tier_d_visibility.py` | Tier D visibility regression undetected |
| `tests/test_trailing_stop.py` | Trailing stop regression — 2 known pre-existing failures |
| `tests/test_tranche_exits.py` | Tranche exit regression undetected |
| `tests/test_universe_committed.py` | Universe regression — trading universe broken |
| `tests/test_universe_position.py` | Universe position regression undetected |
| `tests/test_universe_promoter.py` | Promoter regression undetected |
| `tests/test_vix_kelly.py` | VIX-Kelly sizing regression undetected |

---

## Section F: Tests to Move to Release-Only Regression

**28 tests** can be excluded from the default `pytest` run and run only on release branches.

| File | Reason for Release-Only |
|------|------------------------|
| `tests/test_alpha_decay.py` | Feature flagged off or inactive |
| `tests/test_alpha_validation.py` | Feature flagged off or inactive |
| `tests/test_apex_divergence.py` | Feature flagged off or inactive |
| `tests/test_apex_flip_proposer.py` | Feature flagged off or inactive |
| `tests/test_apex_shadow_report.py` | Feature flagged off or inactive |
| `tests/test_handoff_publisher_observer.py` | Feature flagged off or inactive |
| `tests/test_ic_validator.py` | IC gate already passed |
| `tests/test_intelligence_day2.py` | Intelligence sprint development test |
| `tests/test_intelligence_day3.py` | Intelligence sprint development test |
| `tests/test_intelligence_day4.py` | Intelligence sprint development test |
| `tests/test_intelligence_day5.py` | Intelligence sprint development test |
| `tests/test_intelligence_day6.py` | Intelligence sprint development test |
| `tests/test_intelligence_day7.py` | Intelligence sprint development test |
| `tests/test_intelligence_factor_registry.py` | Intelligence sprint development test |
| `tests/test_intelligence_reference_data.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint2.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint3.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint4a.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint4b.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint5a.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint5b.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint6a.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint6b.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint6c.py` | Intelligence sprint development test |
| `tests/test_intelligence_sprint7a2.py` | Intelligence sprint development test |
| `tests/test_iv_skew.py` | Feature flagged off or inactive |
| `tests/test_quota_capacity_calibrator.py` | Feature flagged off or inactive |
| `tests/test_social_sentiment.py` | Feature flagged off or inactive |

---

## Section G: Cloud Container Exclusions (Never Deploy to Live)

These files must **never** enter the live cloud container:

| File | Reason |
|------|--------|
| `.fail_* files in data/live/` | Failed handoff attempt records (6 files from 2026-05-07) (unknown_requires_review) |
| `Chief-Decifer-recovered/` | Recovered Chief Decifer app from earlier version; contains __pycache__ (legacy_scanner_path) |
| `Decifer Trading.app/` | macOS app bundle in repo (binary) (unknown_requires_review) |
| `Decifer.app/` | Alternative macOS app bundle (binary) (unknown_requires_review) |
| `advisory_log_reviewer.py` | Reads and summarizes advisory runtime logs; intelligence_first_advisor (advisory_only) |
| `advisory_reporter.py` | Generates advisory analysis reports (35KB); flagged off (advisory_only) |
| `alpha_validation.py` | Validates alpha signal quality metrics offline (17KB) (validation_only) |
| `apex_divergence.py` | Logs divergence between Track A and Shadow Apex decisions (17KB) (shadow_only) |
| `backtest_intelligence.py` | Intelligence system for backtests: factor replay, regime simulation (1 (backtest_only) |
| `backtest_results/` | Backtesting engine results directory (1 result file) (backtest_only) |
| `backtester.py` | Core backtesting engine (38KB) (backtest_only) |
| `build_brain.py` | Offline ML model training: builds classifier/regressor/scaler (47KB) (validation_only) |
| `compare_universes.py` | Universe comparison utilities (offline analysis) (validation_only) |
| `data/apex_prompt_snapshot.jsonl` | Apex prompt debug snapshots (2.9MB) (advisory_only) |
| `data/apex_response_snapshot.jsonl` | Apex response debug snapshots (783KB) (advisory_only) |
| `data/apex_shadow_log.jsonl` | Apex shadow divergence log (11MB — LARGEST DATA FILE) (shadow_only) |
| `data/intelligence/backtest/` | Intelligence backtest fixture results (backtest_only) |
| `data/universe_builder/` | Universe builder pipeline snapshots and shadow comparisons (advisory_only) |
| `factor_registry.py` | Factor definitions and registry (61KB); only referenced by intelligenc (validation_only) |
| `handoff_publisher_observer.py` | Observes handoff publisher for discrepancies (39KB); shadow only (shadow_only) |
| `ic_validator.py` | IC gate validation for Phase B unlock (15KB) (validation_only) |
| `intelligence_adapters.py` | Intelligence source adapters for flagged-off intelligence layer (29KB) (adapter_only) |
| `intelligence_engine.py` | Intelligence engine core (34KB); intelligence_first_advisory_enabled=F (advisory_only) |
| `intelligence_schema_validator.py` | Schema validation for intelligence files (158KB — LARGEST FILE) (validation_only) |
| `iv_skew.py` | IV skew analysis; IV Skew dimension is config-gated (adapter_only) |
| `macro_transmission_matrix.py` | Macro signal transmission rules (not in live chain) (adapter_only) |
| `paper_handoff_builder.py` | Builds paper trading handoff for comparison (16KB) (shadow_only) |
| `paper_handoff_comparator.py` | Compares paper vs live handoffs (37KB); shadow only (shadow_only) |
| `provider_fetch_tester.py` | Tests data provider connectivity (21KB); offline validation (validation_only) |
| `quota_allocator.py` | Quota allocation for intelligence layer (not in live bot chain) (adapter_only) |
| `quota_capacity_calibrator.py` | Quota capacity calibration scenarios (33KB); offline analysis (validation_only) |
| `reachability.py` | API reachability checks (10KB); standalone probe tool (validation_only) |
| `route_tagger.py` | Signal/route tagging for intelligence layer (not in live chain) (adapter_only) |
| `scripts/analyze_entry_timing.py` | Analyzes entry timing quality vs outcomes (post-hoc analysis) (advisory_only) |
| `scripts/apex_cap_replay.py` | Replays Apex capacity scenarios for analysis (advisory_only) |
| `scripts/apex_flip_proposer.py` | Proposes Apex strategy direction flips based on divergence (advisory_only) |
| `scripts/apex_shadow_report.py` | Generates shadow trading report comparing Track A vs Shadow (shadow_only) |
| `scripts/backfill_may5_trades.py` | One-time backfill of May 5 trades missing from training store (migration_tool) |
| `scripts/backfill_position_closed.py` | One-time backfill of position_closed records (migration_tool) |
| `scripts/bump-version.sh` | Version bump automation (documentation_only) |
| `scripts/factor_analysis.py` | Factor performance analysis (offline, post-hoc) (advisory_only) |
| `scripts/migrate_trades_to_training_store.py` | One-time migration of trades.json to training_store JSONL (migration_tool) |
| `scripts/phase1_session_report.py` | Session reporting for Phase 1 Tier D analysis (advisory_only) |
| `scripts/pru_ab_comparison.py` | A/B comparison for position research universe (advisory_only) |
| `scripts/reconcile_trades_json.py` | Reconciles trade JSON files post-migration (migration_tool) |
| `scripts/recover_pattern_ids.py` | Recovers lost pattern IDs in trade metadata (migration_tool) |
| `theme_activation_engine.py` | Theme activation logic (19KB); only referenced by backtest_intelligenc (advisory_only) |
| `thesis_store.py` | Thesis storage and management (16KB); not in live chain (advisory_only) |
| `tools/signal_correlation.py` | Signal correlation analysis tool (offline) (advisory_only) |
| `Chief-Decifer-recovered/` | Contains .pyc bytecode, dev artifacts, legacy recovered code |
| `Decifer Trading.app/` + `Decifer.app/` | Binary app bundles — distribute separately |
| `backtest_results/` + `data/intelligence/backtest/` | Backtest fixtures and results — offline only |
| `data/models/*.pkl` | ML model files should be versioned separately (not as git blobs) |

---

## Section H: Required for Production Runtime

**114 modules** are in the live transitive import closure.

| File | Purpose | Owner |
|------|---------|-------|
| `advisory_logger.py` | Low-level advisory log writer used by apex_orchestrator for … | trading_engine |
| `alpaca_data.py` | Primary market data source: bars, quotes, snapshots via Alpa… | trading_engine |
| `alpaca_news.py` | Real-time news feed from Alpaca; feeds news_sentinel | trading_engine |
| `alpaca_options.py` | Alpaca options chain data and Greeks | trading_engine |
| `alpaca_stream.py` | WebSocket streaming for real-time quotes/trades from Alpaca | trading_engine |
| `alpha_decay.py` | Alpha decay analytics; imported by analytics pipeline | intelligence |
| `alpha_vantage_client.py` | Alpha Vantage macro/economic data client; fallback for funda… | trading_engine |
| `analytics.py` | Performance analytics: PnL, drawdown, alpha metrics | trading_engine |
| `apex_cap_score.py` | Scores candidate capacity for Apex shortlist (2KB, lean) | trading_engine |
| `apex_orchestrator.py` | Orchestrates Apex Single-Synthesizer calls (Track A/B/Shadow… | trading_engine |
| `bot.py` | Main process entry point: scheduling, IPC, log setup | trading_engine |
| `bot_account.py` | Account data retrieval: equity, cash, FX, news headlines | trading_engine |
| `bot_dashboard.py` | Operational Dash dashboard (port configurable); 'the dashboa… | trading_engine |
| `bot_hot_reload.py` | Hot-reload mechanism for live code updates without restart | trading_engine |
| `bot_ibkr.py` | IBKR TWS connection, order sync, disconnect handling | trading_engine |
| `bot_sentinel.py` | Background surveillance: margin, drawdown, regime alerts | trading_engine |
| `bot_state.py` | Global state: active_trades, clog, dash, subscription regist… | trading_engine |
| `bot_trading.py` | Core scan/trade cycle: run_scan, PM review, cash rebalance | trading_engine |
| `bot_voice.py` | Text-to-speech for trade events via macOS say | trading_engine |
| `bracket_health.py` | Monitors and syncs bracket orders (OCO) with IBKR | trading_engine |
| `catalyst_engine.py` | Scores EDGAR filings, earnings surprises, analyst actions fo… | trading_engine |
| `chief-decifer/` | Chief Decifer read-only monitoring dashboard (port 8181) | Chief |
| `config.py` | All thresholds, flags, feature gates — authoritative config | trading_engine |
| `data/apex_decision_audit.jsonl` | Apex decision audit trail (4MB) | trading_engine |
| `data/audit_log.jsonl` | Operational audit log (1.9MB) | trading_engine |
| `data/committed_universe.json` | Top-1000 by dollar volume universe (201KB) | trading_engine |
| `data/ic_validation_result.json` | IC gate result (Phase B unlock proof) | intelligence |
| `data/ic_weights.json` | Current IC signal weights (1.2KB) | trading_engine |
| `data/live/` | Live handoff manifests directory (paper/prod manifests) | handoff |
| `data/models/` | ML model files: classifier, regressor, scaler, features (pkl… | intelligence |
| `data/reference/` | Symbol master, factor registry, sector schema, provider matr… | intelligence |
| `data/signals_log.jsonl` | Signal scoring history (14MB — LARGEST JSONL) | trading_engine |
| `data/trade_events.jsonl` | ORDER_INTENT/FILLED/CLOSED write-ahead log (470KB) | trading_engine |
| `data/trades.json` | Active position state and trade history (1.6MB) | trading_engine |
| `data/training_records.jsonl` | ML training records (JSONL); Phase C gate source (336KB) | trading_engine |
| `earnings_calendar.py` | Fetches earnings dates; used by entry gate to avoid earnings… | trading_engine |
| `entry_gate.py` | Final binary entry gate: aggregates all dimension signals | trading_engine |
| `event_log.py` | Write-ahead ORDER_INTENT/FILLED/CLOSED log (JSONL WAL) | trading_engine |
| `execution_agent.py` | Execution monitoring: fill detection, order state tracking | trading_engine |
| `fill_watcher.py` | Watches for order fills from IBKR callbacks | trading_engine |
| `fmp_client.py` | Financial Modeling Prep client: fundamentals, events, analys… | trading_engine |
| `fred_client.py` | FRED macroeconomic indicators client | trading_engine |
| `fx_signals.py` | FX signal generation; currently disabled (fx_enabled=False) | trading_engine |
| `guardrails.py` | Hard-stop safety guardrails: max position, drawdown, margin … | trading_engine |
| `handoff_candidate_adapter.py` | Adapts scored candidates to handoff format (4KB, lean adapte… | handoff |
| `handoff_reader.py` | Reads live handoff manifests from data/live/; used by scanne… | handoff |
| `ibkr_reconciler.py` | Reconciles local position state with IBKR TWS | trading_engine |
| `ibkr_streaming.py` | IBKR streaming market data (mktData callbacks) | trading_engine |
| `ic/__init__.py` | IC package init | trading_engine |
| `ic/calculator.py` | IC per-dimension calculator | trading_engine |
| `ic/engine.py` | IC calculation engine | trading_engine |
| `ic/gates.py` | IC phase gates | trading_engine |
| `ic/reporter.py` | IC report generation | trading_engine |
| `ic/store.py` | IC record store | trading_engine |
| `ic/validator.py` | IC validation logic | trading_engine |
| `ic/weights.py` | IC-based signal weight computation | trading_engine |
| `ic_calculator.py` | Calculates Information Coefficients for signal weighting | trading_engine |
| `learning.py` | Trade logging, equity history, IC data persistence | trading_engine |
| `lib/` | Frontend JS libraries: tom-select, vis-9.1.2, dashboard bind… | trading_engine |
| `llm_client.py` | Claude API client wrapper (anthropic SDK calls) | trading_engine |
| `macro_calendar.py` | Economic calendar: FOMC, CPI, NFP event schedule | trading_engine |
| `market_intelligence.py` | Apex call builder: classify_signals, apex_call, session cont… | trading_engine |
| `market_observer.py` | MarketObservation cache: regime, session character, VIX | trading_engine |
| `ml_engine.py` | ML inference engine (ml_enabled=True); pattern scoring | intelligence |
| `momentum_sentinel.py` | Background momentum monitoring for breakout detection | trading_engine |
| `news.py` | News aggregation from Alpaca, FMP, RSS sources | trading_engine |
| `news_infrastructure.py` | News infrastructure: dedup, materiality gating, caching | trading_engine |
| `news_sentinel.py` | NEWS_INTERRUPT sentinel: watches for breaking news triggers | trading_engine |
| `options.py` | Options position monitoring and exit logic | trading_engine |
| `options_entries.py` | Options entry execution logic | trading_engine |
| `options_scanner.py` | Options universe screener for ATM delta-0.50 candidates | trading_engine |
| `orders.py` | Order wrapper interface (thin layer over orders_core) | trading_engine |
| `orders_contracts.py` | IBKR contract qualification and selection logic | trading_engine |
| `orders_core.py` | Core order execution: execute_buy, execute_sell, execute_sho… | trading_engine |
| `orders_guards.py` | Order validation guards: PDT, margin, duplicate, size checks | trading_engine |
| `orders_options.py` | Options-specific order execution (53KB) | trading_engine |
| `orders_portfolio.py` | Portfolio-level order management: flatten, trim, rebalance (… | trading_engine |
| `orders_state.py` | Order and position state tracking; _safe_set_trade immutabil… | trading_engine |
| `overnight_research.py` | Pre-market research tasks: macro, catalysts, regime prep (41… | trading_engine |
| `pattern_library.py` | Trading pattern definitions and performance lookup | trading_engine |
| `pdt_rule.py` | Pattern Day Trader rule enforcement | trading_engine |
| `phase_gate.py` | Phase-based feature gating (blocks features until phase crit… | trading_engine |
| `portfolio.py` | Portfolio abstraction layer | trading_engine |
| `portfolio_manager.py` | Portfolio lifecycle: lightweight_cycle_check, thesis validat… | trading_engine |
| `portfolio_optimizer.py` | Portfolio optimization: position sizing, correlation managem… | trading_engine |
| `position_sizing.py` | Position size calculations based on Kelly, ATR, account size | trading_engine |
| `presession.py` | Pre-market session setup: universe warm-up, data prefetch (1… | trading_engine |
| `price_updater.py` | Real-time price update distribution to positions | trading_engine |
| `risk.py` | Position sizing, drawdown limits, HWM tracking (50KB) | trading_engine |
| `risk_gates.py` | Risk check gates: cash rebalance, margin, exposure ceiling | trading_engine |
| `safety_overlay.py` | Additional safety overlay: circuit breakers, kill switches | trading_engine |
| `scanner.py` | Three-tier universe assembler + regime classification | trading_engine |
| `schemas.py` | Shared data schemas and type definitions (9KB) | trading_engine |
| `sentinel_agents.py` | Sentinel agent stub (3KB); minimal remaining code post-migra… | trading_engine |
| `signal_dispatcher.py` | Routes signal results to appropriate handlers | trading_engine |
| `signal_pipeline.py` | Signal processing pipeline: batch scoring, aggregation | trading_engine |
| `signal_types.py` | Signal type enum definitions (lightweight) | trading_engine |
| `signals/__init__.py` | Signals package init; exports fetch_multi_timeframe | trading_engine |
| `signals/dimensions.py` | Individual dimension scoring functions (10 dimensions) | trading_engine |
| `signals/fetch.py` | Multi-timeframe signal fetch (imported as signals.fetch_mult… | trading_engine |
| `signals/filters.py` | Pre-scoring candidate filters | trading_engine |
| `signals/scoring.py` | Signal aggregation and composite scoring | trading_engine |
| `smart_execution.py` | TWAP/VWAP/Iceberg execution for orders >$10K/500 shares | trading_engine |
| `social_sentiment.py` | Social sentiment scoring (Reddit/Twitter); social dimension … | trading_engine |
| `static/dashboard.html` | Dashboard HTML template | trading_engine |
| `sympathy_scanner.py` | Sympathy/correlation scanner for sector moves | trading_engine |
| `telegram_bot.py` | Telegram notification integration for trade alerts | trading_engine |
| `theme_tracker.py` | Theme performance tracking in live chain | trading_engine |
| `trade_context.py` | Trade context and metadata builder (24KB) | trading_engine |
| `training_store.py` | ML training records persistence (JSONL); Phase C gate source | trading_engine |
| `universe_committed.py` | Committed universe (top-1000 by dollar volume, weekly refres… | trading_engine |
| `universe_position.py` | Position-level universe management (in live chain) | trading_engine |
| `universe_promoter.py` | Daily promoted list generator (promoter_enabled=True) | trading_engine |
| `version.py` | Version and codename constants (lightweight) | trading_engine |

---

## Section I: Cleanup Sequence (Post-Activation)

| Step | When | Action | Risk |
|------|------|--------|------|
| 1 | immediately | Delete 5 completed migration scripts — `scripts/migrate_trades_to_training_store.py`, `scripts/backfill_may5_trades.py`, `scripts/backfill_position_closed.py` + 2 more | low |
| 2 | immediately | Add .gitignore entries for __pycache__, *.pyc, *.app/ bundles — `.gitignore` | low |
| 3 | immediately | Implement log rotation for signals_log.jsonl and apex_shadow_log.jsonl — `data/signals_log.jsonl`, `data/apex_shadow_log.jsonl` | low |
| 4 | immediately | Clean up 6 .fail_* files in data/live/ — `data/live/.fail_*.json` | low |
| 5 | before_cloud_deploy | Implement or delete test_orders_execute.py (11 TODOs) — `tests/test_orders_execute.py` | medium |
| 6 | after_activation_confirmed | Retire handoff shadow modules — `handoff_publisher_observer.py`, `paper_handoff_builder.py`, `paper_handoff_comparator.py` + 2 more | low |
| 7 | after_activation_confirmed | Retire advisory layer (flagged-off modules) — `advisory_reporter.py`, `advisory_log_reviewer.py`, `intelligence_engine.py` + 2 more | low |
| 8 | after_activation_confirmed | Archive 21 intelligence sprint/day dev tests (keep sprint7b, sprint7c) — `tests/test_intelligence_day2-7.py`, `tests/test_intelligence_sprint2-7a2.py`, `tests/test_intelligence_factor_registry.py` + 1 more | low |
| 9 | after_activation_confirmed | Move build_brain.py, backtester.py, backtest_intelligence.py to scripts/ml/ or offline/ — `build_brain.py`, `backtester.py`, `backtest_intelligence.py` | medium |
| 10 | before_cloud_deploy | Build cloud container exclusion list and add to Dockerfile / deploy config — `.dockerignore or deploy config` | medium |

---

## Section J: Do Not Touch Yet

| File/Path | Reason |
|-----------|--------|
| `bot_trading.py` | Live trading cycle — LOCKED |
| `scanner.py` | Universe assembly and regime — LOCKED |
| `risk.py` | Risk limits — LOCKED |
| `orders_core.py` | Order execution — LOCKED |
| `orders_portfolio.py` | Portfolio orders — LOCKED |
| `orders_options.py` | Options orders — LOCKED |
| `config.py` | No flag changes — LOCKED |
| `handoff_publisher.py` | Active production pipeline — do not touch until activation confirmed |
| `data/live/` | Live manifest directory — sacred path |
| `chief-decifer/state/` | Chief state path — sacred path |
| `data/trades.json` | Active position state — CATASTROPHIC if deleted |
| `data/training_records.jsonl` | ML training data — loss is irreversible |
| `data/trade_events.jsonl` | Trade event WAL — metadata immutability depends on this |
| `tests/test_apex_migration_guards.py` | Guards against re-introduction of deleted Decifer 3.0 code |
| `scripts/rebuild_positions_from_intents.py` | Disaster recovery tool — keep despite being a migration-class script |
| `scripts/tier_d_evidence_report.py` | Phase 2 gate depends on this — active Phase 1 work |
| `scripts/validate_intelligence_files.py` | CI gate — must remain for every branch |

---

## Full Classification Tables

### Live Runtime Modules

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `advisory_logger.py` | Low-level advisory log writer used by apex_orchestrator for … | yes | no | no | Removes structured advisory logging from live scan… |
| `alpaca_data.py` | Primary market data source: bars, quotes, snapshots via Alpa… | yes | no | no | Breaks all price/bar fetching in live cycle |
| `alpaca_news.py` | Real-time news feed from Alpaca; feeds news_sentinel | yes | no | no | Kills news dimension input |
| `alpaca_options.py` | Alpaca options chain data and Greeks | yes | no | no | Breaks options scanning and entry |
| `alpaca_stream.py` | WebSocket streaming for real-time quotes/trades from Alpaca | yes | no | no | Breaks real-time quote streaming |
| `alpha_decay.py` | Alpha decay analytics; imported by analytics pipeline | yes | no | no | Breaks alpha performance tracking |
| `alpha_vantage_client.py` | Alpha Vantage macro/economic data client; fallback for funda… | yes | no | no | Removes macro data fallback |
| `analytics.py` | Performance analytics: PnL, drawdown, alpha metrics | yes | no | no | Breaks performance analytics and IC feed |
| `apex_cap_score.py` | Scores candidate capacity for Apex shortlist (2KB, lean) | yes | no | no | Breaks Apex candidate ranking |
| `apex_orchestrator.py` | Orchestrates Apex Single-Synthesizer calls (Track A/B/Shadow… | yes | no | no | Kills all AI-driven entry and PM decisions |
| `bot.py` | Main process entry point: scheduling, IPC, log setup | yes | no | no | Bot does not start |
| `bot_account.py` | Account data retrieval: equity, cash, FX, news headlines | yes | no | no | Breaks account state refresh each cycle |
| `bot_dashboard.py` | Operational Dash dashboard (port configurable); 'the dashboa… | yes | no | no | Removes operational monitoring UI |
| `bot_hot_reload.py` | Hot-reload mechanism for live code updates without restart | yes | no | no | Disables hot-reload; requires full restart for upd… |
| `bot_ibkr.py` | IBKR TWS connection, order sync, disconnect handling | yes | no | no | Breaks all broker connectivity |
| `bot_sentinel.py` | Background surveillance: margin, drawdown, regime alerts | yes | no | no | Removes margin/drawdown monitoring |
| `bot_state.py` | Global state: active_trades, clog, dash, subscription regist… | yes | no | no | Breaks all state management |
| `bot_trading.py` | Core scan/trade cycle: run_scan, PM review, cash rebalance | yes | no | no | Kills all trading logic |
| `bot_voice.py` | Text-to-speech for trade events via macOS say | yes | no | no | Removes voice alerts only; non-critical |
| `bracket_health.py` | Monitors and syncs bracket orders (OCO) with IBKR | yes | no | no | Bracket orders drift without health checks |
| `catalyst_engine.py` | Scores EDGAR filings, earnings surprises, analyst actions fo… | yes | no | no | Disables catalyst-driven score boost for entries |
| `config.py` | All thresholds, flags, feature gates — authoritative config | yes | no | no | Total system failure |
| `earnings_calendar.py` | Fetches earnings dates; used by entry gate to avoid earnings… | yes | no | no | Entries may fire into earnings windows |
| `entry_gate.py` | Final binary entry gate: aggregates all dimension signals | yes | no | no | No entries are generated |
| `event_log.py` | Write-ahead ORDER_INTENT/FILLED/CLOSED log (JSONL WAL) | yes | no | no | Breaks trade metadata persistence — CRITICAL |
| `execution_agent.py` | Execution monitoring: fill detection, order state tracking | yes | no | no | Fills may not be detected |
| `fill_watcher.py` | Watches for order fills from IBKR callbacks | yes | no | no | Fills not confirmed in local state |
| `fmp_client.py` | Financial Modeling Prep client: fundamentals, events, analys… | yes | no | no | Breaks fundamental data fetching for signals |
| `fred_client.py` | FRED macroeconomic indicators client | yes | no | no | Removes macro indicator input to regime detection |
| `fx_signals.py` | FX signal generation; currently disabled (fx_enabled=False) | yes | no | yes | No FX signals generated, but already inactive |
| `guardrails.py` | Hard-stop safety guardrails: max position, drawdown, margin … | yes | no | no | Safety limits unenforced — HIGH RISK |
| `handoff_candidate_adapter.py` | Adapts scored candidates to handoff format (4KB, lean adapte… | yes | no | no | Handoff candidate format broken |
| `handoff_reader.py` | Reads live handoff manifests from data/live/; used by scanne… | yes | no | no | Scanner cannot read opportunity universe from hand… |
| `ibkr_reconciler.py` | Reconciles local position state with IBKR TWS | yes | no | no | Position state diverges from broker — HIGH RISK |
| `ibkr_streaming.py` | IBKR streaming market data (mktData callbacks) | yes | no | no | Real-time price stream broken |
| `ic_calculator.py` | Calculates Information Coefficients for signal weighting | yes | no | no | IC-weighted entries fall back to equal weights |
| `learning.py` | Trade logging, equity history, IC data persistence | yes | no | no | Trade records not persisted |
| `llm_client.py` | Claude API client wrapper (anthropic SDK calls) | yes | no | no | All Apex calls fail |
| `macro_calendar.py` | Economic calendar: FOMC, CPI, NFP event schedule | yes | no | no | Macro event context missing from Apex prompts |
| `market_intelligence.py` | Apex call builder: classify_signals, apex_call, session cont… | yes | no | no | All Apex-driven decisions fail |
| `market_observer.py` | MarketObservation cache: regime, session character, VIX | yes | no | no | Apex receives stale or empty market context |
| `ml_engine.py` | ML inference engine (ml_enabled=True); pattern scoring | yes | no | no | ML pattern scoring disabled at runtime |
| `momentum_sentinel.py` | Background momentum monitoring for breakout detection | yes | no | no | Momentum breakout signals delayed |
| `news.py` | News aggregation from Alpaca, FMP, RSS sources | yes | no | no | News dimension has no input data |
| `news_infrastructure.py` | News infrastructure: dedup, materiality gating, caching | yes | no | no | News pipeline broken |
| `news_sentinel.py` | NEWS_INTERRUPT sentinel: watches for breaking news triggers | yes | no | no | News interrupt events not handled |
| `options.py` | Options position monitoring and exit logic | yes | no | no | Options positions not managed |
| `options_entries.py` | Options entry execution logic | yes | no | no | Options entries fail |
| `options_scanner.py` | Options universe screener for ATM delta-0.50 candidates | yes | no | no | Options universe empty |
| `orders.py` | Order wrapper interface (thin layer over orders_core) | yes | no | no | Order interface broken |
| `orders_contracts.py` | IBKR contract qualification and selection logic | yes | no | no | Orders cannot be qualified with IBKR |
| `orders_core.py` | Core order execution: execute_buy, execute_sell, execute_sho… | yes | no | no | All order execution fails — CRITICAL |
| `orders_guards.py` | Order validation guards: PDT, margin, duplicate, size checks | yes | no | no | Orders bypass safety validation |
| `orders_options.py` | Options-specific order execution (53KB) | yes | no | no | All options order execution fails |
| `orders_portfolio.py` | Portfolio-level order management: flatten, trim, rebalance (… | yes | no | no | Portfolio management order execution fails |
| `orders_state.py` | Order and position state tracking; _safe_set_trade immutabil… | yes | no | no | Position state and metadata immutability broken — … |
| `overnight_research.py` | Pre-market research tasks: macro, catalysts, regime prep (41… | yes | no | no | Apex starts with no overnight context |
| `pattern_library.py` | Trading pattern definitions and performance lookup | yes | no | no | Apex prompt loses pattern context |
| `pdt_rule.py` | Pattern Day Trader rule enforcement | yes | no | no | PDT violations possible in paper account |
| `phase_gate.py` | Phase-based feature gating (blocks features until phase crit… | yes | no | no | Phase gates bypass — features activate before read… |
| `portfolio.py` | Portfolio abstraction layer | yes | no | no | Portfolio state unavailable |
| `portfolio_manager.py` | Portfolio lifecycle: lightweight_cycle_check, thesis validat… | yes | no | no | PM cycle check and thesis validation disabled |
| `portfolio_optimizer.py` | Portfolio optimization: position sizing, correlation managem… | yes | no | no | Position sizing reverts to baseline |
| `position_sizing.py` | Position size calculations based on Kelly, ATR, account size | yes | no | no | Position sizes miscalculated |
| `presession.py` | Pre-market session setup: universe warm-up, data prefetch (1… | yes | no | no | Session starts cold — slower first cycle |
| `price_updater.py` | Real-time price update distribution to positions | yes | no | no | Position P&L stale between cycles |
| `risk.py` | Position sizing, drawdown limits, HWM tracking (50KB) | yes | no | no | Risk limits unenforced — CRITICAL |
| `risk_gates.py` | Risk check gates: cash rebalance, margin, exposure ceiling | yes | no | no | Cash rebalance and margin gates disabled |
| `safety_overlay.py` | Additional safety overlay: circuit breakers, kill switches | yes | no | no | Circuit breakers inactive |
| `scanner.py` | Three-tier universe assembler + regime classification | yes | no | no | No candidates — trading stops |
| `schemas.py` | Shared data schemas and type definitions (9KB) | yes | no | no | Type validation broken across modules |
| `sentinel_agents.py` | Sentinel agent stub (3KB); minimal remaining code post-migra… | yes | no | no | Sentinel integration broken |
| `signal_dispatcher.py` | Routes signal results to appropriate handlers | yes | no | no | Signal dispatch broken |
| `signal_pipeline.py` | Signal processing pipeline: batch scoring, aggregation | yes | no | no | All dimension scoring fails |
| `signal_types.py` | Signal type enum definitions (lightweight) | yes | no | no | Signal type references break |
| `smart_execution.py` | TWAP/VWAP/Iceberg execution for orders >$10K/500 shares | yes | no | no | Large orders use simple limit instead of smart rou… |
| `social_sentiment.py` | Social sentiment scoring (Reddit/Twitter); social dimension … | yes | no | yes | Social dimension inactive — no trading impact |
| `sympathy_scanner.py` | Sympathy/correlation scanner for sector moves | yes | no | no | Sympathy plays not identified |
| `telegram_bot.py` | Telegram notification integration for trade alerts | yes | no | yes | Removes Telegram alerts only; non-critical |
| `theme_tracker.py` | Theme performance tracking in live chain | yes | no | no | Theme performance metrics unavailable |
| `trade_context.py` | Trade context and metadata builder (24KB) | yes | no | no | Trade metadata incomplete at entry |
| `training_store.py` | ML training records persistence (JSONL); Phase C gate source | yes | no | no | Training records not written — ML data generation … |
| `universe_committed.py` | Committed universe (top-1000 by dollar volume, weekly refres… | yes | no | no | Trading universe empty |
| `universe_position.py` | Position-level universe management (in live chain) | yes | no | no | Position universe state broken |
| `universe_promoter.py` | Daily promoted list generator (promoter_enabled=True) | yes | no | no | Daily promotions not generated |
| `version.py` | Version and codename constants (lightweight) | yes | no | yes | Version display broken; non-critical |
| `signals/__init__.py` | Signals package init; exports fetch_multi_timeframe | yes | no | no | Signal fetching broken |
| `signals/fetch.py` | Multi-timeframe signal fetch (imported as signals.fetch_mult… | yes | no | no | Signal fetching broken |
| `signals/dimensions.py` | Individual dimension scoring functions (10 dimensions) | yes | no | no | Dimension scoring broken |
| `signals/scoring.py` | Signal aggregation and composite scoring | yes | no | no | Score aggregation broken |
| `signals/filters.py` | Pre-scoring candidate filters | yes | no | no | Candidate filtering broken |
| `ic/__init__.py` | IC package init | yes | no | no | IC framework broken |
| `ic/engine.py` | IC calculation engine | yes | no | no | IC calculations broken |
| `ic/store.py` | IC record store | yes | no | no | IC records not persisted |
| `ic/validator.py` | IC validation logic | yes | no | no | IC validation broken |
| `ic/weights.py` | IC-based signal weight computation | yes | no | no | IC-weighted entries fall back to equal weights |
| `ic/calculator.py` | IC per-dimension calculator | yes | no | no | Per-dimension IC unavailable |
| `ic/reporter.py` | IC report generation | yes | no | no | IC reports unavailable |
| `ic/gates.py` | IC phase gates | yes | no | no | IC gates unavailable |
| `data/trades.json` | Active position state and trade history (1.6MB) | yes | no | no | All position state lost — CATASTROPHIC |
| `data/training_records.jsonl` | ML training records (JSONL); Phase C gate source (336KB) | yes | no | no | Training data generation stops — ML engine starved |
| `data/trade_events.jsonl` | ORDER_INTENT/FILLED/CLOSED write-ahead log (470KB) | yes | no | no | Trade event history lost — metadata immutability v… |
| `data/ic_validation_result.json` | IC gate result (Phase B unlock proof) | yes | no | no | Phase B gate re-verification fails |
| `data/ic_weights.json` | Current IC signal weights (1.2KB) | yes | no | no | IC weighting reverts to equal weights |
| `data/committed_universe.json` | Top-1000 by dollar volume universe (201KB) | yes | no | no | Trading universe collapses to dynamic-only |
| `data/signals_log.jsonl` | Signal scoring history (14MB — LARGEST JSONL) | yes | no | no | Signal audit trail lost |
| `data/apex_decision_audit.jsonl` | Apex decision audit trail (4MB) | yes | no | no | Apex decision history lost |
| `data/audit_log.jsonl` | Operational audit log (1.9MB) | yes | no | no | Audit trail incomplete |
| `data/live/` | Live handoff manifests directory (paper/prod manifests) | yes | no | no | Handoff system loses current manifest state |
| `data/models/` | ML model files: classifier, regressor, scaler, features (pkl… | yes | no | no | ML inference fails without model files |
| `data/reference/` | Symbol master, factor registry, sector schema, provider matr… | yes | no | no | Reference data unavailable for signal scoring |
| `chief-decifer/` | Chief Decifer read-only monitoring dashboard (port 8181) | no | no | no | Monitoring dashboard loses state |
| `lib/` | Frontend JS libraries: tom-select, vis-9.1.2, dashboard bind… | yes | no | no | Dashboard JS components broken |
| `static/dashboard.html` | Dashboard HTML template | yes | no | no | Dashboard renders blank |

### Advisory-Only Modules (Flagged Off)

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `advisory_log_reviewer.py` | Reads and summarizes advisory runtime logs; intelligence_fir… | no | no | yes | Advisory log review unavailable — already inactive |
| `advisory_reporter.py` | Generates advisory analysis reports (35KB); flagged off | no | no | yes | Advisory reports not generated — already inactive |
| `intelligence_engine.py` | Intelligence engine core (34KB); intelligence_first_advisory… | no | no | yes | Intelligence engine inactive — already flagged off |
| `theme_activation_engine.py` | Theme activation logic (19KB); only referenced by backtest_i… | no | no | yes | Theme activation engine inactive — intelligence la… |
| `thesis_store.py` | Thesis storage and management (16KB); not in live chain | no | no | yes | Thesis store unavailable — advisory layer only |
| `scripts/analyze_entry_timing.py` | Analyzes entry timing quality vs outcomes (post-hoc analysis… | no | yes | yes | Entry timing analysis unavailable — offline tool |
| `scripts/apex_cap_replay.py` | Replays Apex capacity scenarios for analysis | no | yes | yes | Apex cap replay unavailable — offline tool |
| `scripts/apex_flip_proposer.py` | Proposes Apex strategy direction flips based on divergence | no | yes | yes | Flip proposals unavailable — advisory only |
| `scripts/cancel_orphan_orders.py` | Cancels orphaned orders in IBKR on-demand | no | no | no | Orphan cleanup must be done manually via TWS |
| `scripts/factor_analysis.py` | Factor performance analysis (offline, post-hoc) | no | yes | yes | Factor analysis unavailable — offline tool |
| `scripts/phase1_session_report.py` | Session reporting for Phase 1 Tier D analysis | no | yes | yes | Phase 1 session reports unavailable |
| `scripts/pru_ab_comparison.py` | A/B comparison for position research universe | no | yes | yes | PRU A/B comparison unavailable — offline tool |
| `scripts/tier_d_evidence_report.py` | Tier D funnel evidence compilation (active Phase 1 analysis) | no | no | no | Tier D Phase 2 gate assessment blocked |
| `scripts/tier_d_test_scan.py` | Test scan for Tier D visibility | no | no | no | Tier D test scans unavailable |
| `data/apex_prompt_snapshot.jsonl` | Apex prompt debug snapshots (2.9MB) | no | no | yes | Debug prompt snapshots lost — non-critical |
| `data/apex_response_snapshot.jsonl` | Apex response debug snapshots (783KB) | no | no | yes | Debug response snapshots lost — non-critical |
| `data/tier_d_funnel.jsonl` | Tier D funnel evidence log (2.9MB) | no | no | no | Phase 2 gate evidence lost — Phase 1 active |
| `data/intelligence/` | Intelligence layer outputs: advisory, thematic, economic con… | no | no | no | Intelligence layer has no cached state |
| `data/universe_builder/` | Universe builder pipeline snapshots and shadow comparisons | no | no | yes | Universe builder analysis unavailable |
| `tools/signal_correlation.py` | Signal correlation analysis tool (offline) | no | yes | yes | Signal correlation analysis unavailable |

### Shadow-Only Modules

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `apex_divergence.py` | Logs divergence between Track A and Shadow Apex decisions (1… | no | no | yes | Divergence tracking disabled — shadow analysis onl… |
| `handoff_publisher_observer.py` | Observes handoff publisher for discrepancies (39KB); shadow … | no | no | yes | Observer data unavailable — shadow analysis only |
| `paper_handoff_builder.py` | Builds paper trading handoff for comparison (16KB) | no | no | yes | Paper handoff comparison unavailable — shadow only |
| `paper_handoff_comparator.py` | Compares paper vs live handoffs (37KB); shadow only | no | no | yes | Paper/live comparison unavailable — shadow only |
| `scripts/apex_shadow_report.py` | Generates shadow trading report comparing Track A vs Shadow | no | no | yes | Shadow report unavailable — USE_APEX_V3_SHADOW=Tru… |
| `data/apex_shadow_log.jsonl` | Apex shadow divergence log (11MB — LARGEST DATA FILE) | no | no | yes | Shadow comparison data lost — USE_APEX_V3_SHADOW=T… |

### Validation-Only Modules

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `alpha_validation.py` | Validates alpha signal quality metrics offline (17KB) | no | yes | yes | Alpha validation reports unavailable — offline too… |
| `build_brain.py` | Offline ML model training: builds classifier/regressor/scale… | no | yes | yes | ML models cannot be retrained — offline tool only |
| `compare_universes.py` | Universe comparison utilities (offline analysis) | no | yes | yes | Universe diff analysis unavailable — offline only |
| `factor_registry.py` | Factor definitions and registry (61KB); only referenced by i… | no | yes | yes | Factor registry unavailable — offline validation o… |
| `ic_validator.py` | IC gate validation for Phase B unlock (15KB) | no | yes | yes | IC validation reports unavailable — gate already p… |
| `intelligence_schema_validator.py` | Schema validation for intelligence files (158KB — LARGEST FI… | no | yes | yes | Intelligence schema validation unavailable — offli… |
| `provider_fetch_tester.py` | Tests data provider connectivity (21KB); offline validation | no | yes | yes | Provider connectivity testing unavailable — offlin… |
| `quota_capacity_calibrator.py` | Quota capacity calibration scenarios (33KB); offline analysi… | no | yes | yes | Quota calibration unavailable — already run, resul… |
| `reachability.py` | API reachability checks (10KB); standalone probe tool | no | yes | yes | Reachability probe unavailable — offline tool only |
| `scripts/validate_intelligence_files.py` | Validates all data/intelligence/*.json schemas (CI gate) | no | no | no | Intelligence file validation breaks — needed for C… |

### Backtest-Only Modules

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `backtest_intelligence.py` | Intelligence system for backtests: factor replay, regime sim… | no | yes | yes | Backtest intelligence runs unavailable — offline o… |
| `backtester.py` | Core backtesting engine (38KB) | no | yes | yes | Backtests unavailable — offline only |
| `data/intelligence/backtest/` | Intelligence backtest fixture results | no | yes | yes | Intelligence backtests unavailable — offline only |
| `backtest_results/` | Backtesting engine results directory (1 result file) | no | yes | yes | Backtest results lost — offline only |

### Scheduled Workers

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `reference_data_builder.py` | Builds reference data: symbol master, factor registry, secto… | no | no | no | Reference data not refreshed on schedule |
| `universe_builder.py` | Active opportunity universe construction (scheduled, not imp… | no | no | no | Opportunity universe not refreshed |
| `scripts/icloud-sync.sh` | iCloud sync automation | no | no | no | iCloud sync for backups disabled |
| `scripts/com.decifer.auto-push.plist` | Launch agent for auto-push daemon | no | no | no | Auto-push daemon not installed |
| `scripts/com.decifer.icloud-sync.plist` | Launch agent for iCloud sync daemon | no | no | no | iCloud sync daemon not installed |

### Adapter-Only Modules

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `intelligence_adapters.py` | Intelligence source adapters for flagged-off intelligence la… | no | no | yes | Intelligence adapters unavailable — already inacti… |
| `iv_skew.py` | IV skew analysis; IV Skew dimension is config-gated | no | no | yes | IV Skew dimension unavailable — already config-gat… |
| `macro_transmission_matrix.py` | Macro signal transmission rules (not in live chain) | no | no | yes | Macro transmission inactive — intelligence layer n… |
| `quota_allocator.py` | Quota allocation for intelligence layer (not in live bot cha… | no | no | yes | Intelligence quota allocation inactive |
| `route_tagger.py` | Signal/route tagging for intelligence layer (not in live cha… | no | no | yes | Route tagging inactive — intelligence layer disabl… |

### Migration Tools

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `scripts/backfill_may5_trades.py` | One-time backfill of May 5 trades missing from training stor… | no | yes | yes | One-time migration already run; no ongoing use |
| `scripts/backfill_position_closed.py` | One-time backfill of position_closed records | no | yes | yes | One-time migration already run; no ongoing use |
| `scripts/migrate_trades_to_training_store.py` | One-time migration of trades.json to training_store JSONL | no | yes | yes | One-time migration already run; training_records.j… |
| `scripts/rebuild_positions_from_intents.py` | Reconstructs positions from ORDER_INTENT events (disaster re… | no | no | no | Cannot reconstruct positions from event log if nee… |
| `scripts/reconcile_trades_json.py` | Reconciles trade JSON files post-migration | no | yes | yes | One-time reconciliation already complete |
| `scripts/recover_pattern_ids.py` | Recovers lost pattern IDs in trade metadata | no | yes | yes | One-time recovery already run |

### Production Runtime Candidates (Separate Process)

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `handoff_publisher.py` | Publishes opportunity universe to data/live/ manifests (36KB… | no | no | no | Live manifests not updated — scanner falls back to… |

### Unknown / Requires Review

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `audit_candle_gate.py` | Candle audit for entry confirmation; not in live import chai… | no | no | no | Unclear — may be called dynamically; investigate b… |
| `candidate_resolver.py` | Candidate resolution logic; not imported by any live chain m… | no | no | no | Unclear purpose; investigate before deleting |
| `daily_journal.py` | Daily trading journal (20KB); not in live import chain | no | no | no | Daily journal entries not written if active |
| `Decifer Trading.app/` | macOS app bundle in repo (binary) | no | yes | yes | App bundle loses its source repo reference — offli… |
| `Decifer.app/` | Alternative macOS app bundle (binary) | no | yes | yes | App bundle loses reference — offline only |
| `.fail_* files in data/live/` | Failed handoff attempt records (6 files from 2026-05-07) | no | yes | yes | Failed handoff history lost — debug value only |

### Legacy Scanner Path

| File | Purpose | Live? | Delete Now? | Delete After Cutover? | Risk |
|------|---------|-------|-------------|----------------------|------|
| `Chief-Decifer-recovered/` | Recovered Chief Decifer app from earlier version; contains _… | no | yes | yes | Recovery reference lost — historical only |

---

## Test Suite Summary

Total test files: 108  
Total test functions (approximate): ~3,269  

| Category | Count | Action |
|----------|-------|--------|
| Permanent regression | 77 | Keep in all CI runs |
| Release-only / dev sprint | 28 | Move to release-only gate or archive |
| Migration tests | 2 | Retire after cutover confirmed |
| Dead/stale (TODO debt) | 1 | Implement or delete |
| Smoke-marked | 9 functions in 7 files | Fast pre-commit gate |

---

*Generated by `_generate_audit.py` on 2026-05-09T11:49:37.217847+00:00. Do not edit manually — re-run the generator.*
