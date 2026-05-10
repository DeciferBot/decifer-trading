# Production Runtime Surface Map
**Branch:** `cleanup/global-production-codebase-standard`  
**Date:** 2026-05-10  
**Method:** Static import closure analysis from `bot.py`, `bot_trading.py`, `scanner.py` entry points.

---

## Entry Points

| File | Role |
|------|------|
| `bot.py` | Primary bot process — starts all runtime actors |
| `bot_trading.py` | Core trading engine — scan→score→execute loop |
| `scanner.py` | Universe scanner — populates candidate list |

---

## Live Runtime Closure (88 modules)

These modules are transitively reachable from the entry points at import time. They are **protected** — no deletion, no renaming, no signature changes without tracing the full call chain.

### Trading Engine Core
| Module | Role |
|--------|------|
| `bot.py` | Process entrypoint, actor orchestration |
| `bot_trading.py` | Scan→score→Apex→execute loop |
| `bot_account.py` | Account state, buying power |
| `bot_state.py` | Shared runtime state |
| `bot_ibkr.py` | IBKR TWS connectivity |
| `bot_voice.py` | Voice alerts |
| `bot_hot_reload.py` | Config hot-reload without restart |
| `bot_dashboard.py` | Operational dashboard (Dash app) |
| `bot_sentinel.py` | News sentinel actor |
| `bot_voice.py` | Voice alerts |

### Signal Engine
| Module | Role |
|--------|------|
| `signal_types.py` | Signal schema and constants |
| `signal_pipeline.py` | 10-dimension scoring pipeline |
| `signal_dispatcher.py` | Tier-D candidate dispatch + funnel logging |
| `entry_gate.py` | Entry qualification gate |
| `phase_gate.py` | Intraday phase gate |
| `guardrails.py` | Hard safety limits |
| `safety_overlay.py` | Regime-aware safety overlay |
| `catalyst_engine.py` | EDGAR/earnings/analyst catalyst scoring |
| `pattern_library.py` | Setup pattern classification |

### Apex Orchestrator
| Module | Role |
|--------|------|
| `apex_orchestrator.py` | Apex Single-Synthesizer pipeline |
| `market_intelligence.py` | `apex_call()` implementation |
| `llm_client.py` | Anthropic API wrapper |
| `trade_context.py` | Trade context builder for Apex |
| `apex_cap_score.py` | Apex candidate cap scoring |

### Universe & Candidates
| Module | Role |
|--------|------|
| `scanner.py` | Universe scanner |
| `universe_committed.py` | Committed universe (top-1000 by dollar volume) |
| `universe_position.py` | Position-held universe layer |
| `universe_promoter.py` | Dynamic universe promotion |
| `momentum_sentinel.py` | Momentum-based universe additions |
| `sympathy_scanner.py` | Sympathy/sector-spread detection |
| `handoff_candidate_adapter.py` | Handoff universe governance adapter |
| `handoff_reader.py` | Manifest reader for active opportunity handoff |
| `worker_evidence.py` | Worker evidence collection |

### Orders & Risk
| Module | Role |
|--------|------|
| `orders.py` | Order routing facade |
| `orders_core.py` | Core order logic |
| `orders_portfolio.py` | Portfolio-level order management |
| `orders_options.py` | Options order management |
| `orders_state.py` | Order state + metadata immutability guard |
| `orders_contracts.py` | IBKR contract resolution |
| `orders_guards.py` | Order pre-flight guards |
| `risk.py` | Risk engine |
| `risk_gates.py` | Risk gate checks |
| `pdt_rule.py` | Pattern Day Trader rule enforcement |
| `position_sizing.py` | Kelly/risk-based position sizing |
| `portfolio.py` | Portfolio state model |
| `portfolio_manager.py` | Portfolio management actions |
| `portfolio_optimizer.py` | Portfolio allocation optimizer |
| `smart_execution.py` | TWAP/VWAP/Iceberg smart execution |
| `bracket_health.py` | Bracket order health monitoring |
| `fill_watcher.py` | Fill confirmation watcher |
| `execution_agent.py` | Execution agent (imported by orders_core) |

### Persistence
| Module | Role |
|--------|------|
| `event_log.py` | Write-ahead event log (ORDER_INTENT→FILLED→CLOSED) |
| `training_store.py` | ML training record store |
| `learning.py` | Learning engine + skew tracking |
| `schemas.py` | Data schemas |
| `route_tagger.py` | Route tagging for signal attribution |

### Brokers & Data
| Module | Role |
|--------|------|
| `alpaca_data.py` | Alpaca market data (primary) |
| `alpaca_stream.py` | Alpaca streaming |
| `alpaca_news.py` | Alpaca news feed |
| `alpaca_options.py` | Alpaca options data |
| `fmp_client.py` | FMP fundamentals client |
| `fred_client.py` | FRED macro data client |
| `alpha_vantage_client.py` | Alpha Vantage fallback |
| `ibkr_reconciler.py` | IBKR position reconciliation |
| `ibkr_streaming.py` | IBKR streaming data |
| `earnings_calendar.py` | Earnings calendar |
| `news.py` | News aggregation |
| `news_infrastructure.py` | News infrastructure layer |
| `news_sentinel.py` | News event sentinel |

### Intelligence / Analysis
| Module | Role |
|--------|------|
| `market_observer.py` | Market state observation (imported by market_intelligence) |
| `ic_calculator.py` | Information coefficient calculation |
| `alpha_decay.py` | Alpha decay analysis |
| `analytics.py` | Analytics (imported by bot_dashboard) |
| `fx_signals.py` | FX signal dimension |
| `sentinel_agents.py` | Sentinel agent framework |

### Scheduled / Support
| Module | Role |
|--------|------|
| `overnight_research.py` | Pre-session research generation |
| `presession.py` | Pre-session preparation |
| `macro_calendar.py` | Macro calendar |
| `social_sentiment.py` | Social sentiment dimension |
| `theme_tracker.py` | Theme tracking |
| `telegram_bot.py` | Telegram notification bot |
| `price_updater.py` | Price update service |

### Infra / ML
| Module | Role |
|--------|------|
| `advisory_logger.py` | Advisory decision logger (imported by bot_trading) |
| `ml_engine.py` | ML engine (lazy-imported by bot.py for availability check) |
| `config.py` | All runtime configuration |
| `version.py` | Version constants |
| `dashboard.py` | Dashboard HTML loader (3-line shim) |

---

## Scheduled Workers (Operational — Not in Bot Runtime Closure)

These are not imported by the bot process but are part of the operational pipeline. They run on cron or are invoked manually.

| Module | Role | Invocation |
|--------|------|-----------|
| `handoff_publisher.py` | Publishes active opportunity universe handoff | Cron / CLI |
| `universe_builder.py` | Builds committed universe (top-1000 weekly refresh) | Cron |
| `reference_data_builder.py` | Builds reference data for intelligence pipeline | Cron |
| `quota_allocator.py` | Quota allocation policy (dependency of handoff_publisher) | Via handoff_publisher |

---

## Feature-Gated Modules (Flags Currently OFF)

These modules exist in the codebase but their feature flags are disabled in production config.

| Module | Config Flag | State |
|--------|-------------|-------|
| `advisory_reporter.py` | `intelligence_first_advisory_enabled: False` | OFF |
| `advisory_log_reviewer.py` | `intelligence_first_advisory_enabled: False` | OFF |
| `handoff_publisher_observer.py` | Shadow observation mode | Shadow only |
| `quota_capacity_calibrator.py` | One-time calibration tool | Not scheduled |

---

## Intelligence Pipeline (Future Production — Not Yet Active)

These modules represent the next-generation intelligence pipeline. They are not in the live runtime yet but are protected pending promotion gate.

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

## Non-Production Tooling (Archived in This Session)

These modules were not in the live runtime, had no active test coverage in the permanent suite, and are moved to `archive/` to reduce production surface area.

| Module | Reason | Disposition |
|--------|--------|-------------|
| `backtester.py` | Backtest only, never called by bot | → `archive/` |
| `backtest_intelligence.py` | Backtest only, production guards assert NOT imported | → `archive/` |
| `build_brain.py` | ML model build tool, not a runtime component | → `archive/` |
| `compare_universes.py` | One-shot comparison script | → `archive/` |
| `paper_handoff_builder.py` | Pre-cutover shadow comparator | → `archive/` |
| `paper_handoff_comparator.py` | Pre-cutover shadow comparator | → `archive/` |
| `apex_divergence.py` | Pre-cutover shadow divergence logger | → `archive/` |
| `reachability.py` | Dead-code analysis tool (itself unused) | → `archive/` |
| `audit_candle_gate.py` | Standalone audit utility, no live references | → `archive/` |
| `daily_journal.py` | Standalone journal generator, no live references | → `archive/` |

---

## Cloud Exclusion Candidates

Modules that should **never ship to a cloud container** running the live bot:

| Module | Reason |
|--------|--------|
| All `archive/` modules | Non-production |
| `backtester.py` / `backtest_intelligence.py` | Backtest tooling |
| `build_brain.py` | Training tool |
| `quota_capacity_calibrator.py` | One-time calibration |
| `provider_fetch_tester.py` | Debug tool |
| `Chief-Decifer-recovered/` | Separate service, different requirements |
| `chief-decifer/` | Chief state directory, read-only for bot |
| `scripts/migrate_*.py`, `scripts/backfill_*.py`, `scripts/reconcile_*.py` | Migration tools |

---

*Generated by global production standardisation audit. See `docs/codebase_standardisation_retirement_matrix.md` for per-file classifications.*
