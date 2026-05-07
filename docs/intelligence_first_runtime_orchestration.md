# Intelligence-First Runtime Orchestration

**Created:** 2026-05-07
**Sprint:** 7A.4
**Status:** Design / Pre-production — no handoff enabled
**Owner:** Cowork (Claude)
**Approver:** Amit

---

## 1. Runtime Philosophy

### 1.1 Core Principles

**Parallel ingestion, sequential publication.**
Intelligence jobs that ingest from independent providers (Alpaca, FMP, Alpha Vantage, yfinance) run in parallel. They produce isolated, validated, versioned snapshots. The Universe Builder and Handoff Publisher run after ingestion is complete and validated — never concurrently.

**Validated snapshots only.**
No worker publishes a live manifest until every upstream snapshot it depends on has passed schema validation, freshness checks, and content invariants. A worker that fails its own validation writes nothing to the live path.

**Live bot consumes one approved manifest.**
The live trading bot reads `data/live/current_manifest.json` only. It follows file references in that manifest. It never searches for alternate universe files. It never falls back to scanner-led discovery when handoff is enabled.

**No worker directly mutates live bot state.**
Intelligence workers write to `data/intelligence/`, `data/reference/`, and staging areas only. Only the Handoff Publisher writes to `data/live/`. No intelligence job touches `active_trades`, `positions`, `orders`, or `event_log`.

**No intelligence layer creates executable trades.**
Every snapshot, candidate, and manifest produced by the intelligence architecture carries `no_executable_trade_instructions = true` and `live_output_changed = false`. Any snapshot where this invariant is false must be rejected by the Handoff Publisher and never published.

**No fallback discovery when production handoff is enabled.**
When `enable_active_opportunity_universe_handoff = True`, the live bot does not call `score_universe()`, does not consult `favourites.json` for discovery, does not run catalyst screening, and does not call Apex for symbol discovery. The only input to the bot's candidate set is the manifest-referenced active universe file.

**Fail closed on missing, stale, or invalid snapshots.**
Every consumer checks its inputs before consuming them. If an upstream snapshot is missing, expired, schema-invalid, or contains a rejected invariant, the consuming process logs a structured fail-closed reason and takes no further action. It does not degrade silently.

### 1.2 What This Architecture Is Not

- It is not a second bot running beside the trading bot with its own trade decisions.
- It is not a pipeline that bypasses existing risk, execution, or order logic.
- It is not a system that calls order/account/position endpoints outside the Execution/Risk boundary.
- It is not a way to inject candidates into Apex without going through the validated handoff manifest.
- It is not a system where intelligence workers directly call `bot_trading.py` functions.

---

## 2. Process Architecture

### 2.1 Process Registry

#### `reference_data_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Build static reference files: sector schema, symbol master, theme overlay map, factor registry, provider capability matrix. |
| **Owner layer** | Reference Data Layer |
| **Production runtime required** | TBD — weekly schedule, not intraday |
| **Input files** | `data/intelligence/thematic_roster.json`, `data/intelligence/theme_taxonomy.json`, `data/intelligence/transmission_rules.json`, Alpaca symbol universe (API), FMP company directory (API, optional) |
| **Output files** | `data/reference/sector_schema.json`, `data/reference/symbol_master.json`, `data/reference/theme_overlay_map.json`, `data/reference/factor_registry.json`, `data/reference/provider_capability_matrix.json`, `data/reference/layer_factor_map.json`, `data/reference/data_quality_report.json` |
| **Schedule** | Weekly (Sunday 02:00 UTC) or on demand |
| **Dependencies** | None (most inputs are static JSON) |
| **Freshness SLA** | 7 days |
| **Retry policy** | 3 retries with exponential backoff; fail permanently after 3 |
| **Fail-closed behaviour** | On failure: do not overwrite existing reference files; log fail reason; alert if > 14 days stale |
| **Healthcheck** | `data/reference/sector_schema.json` exists and `generated_at` < 8 days ago |
| **Heartbeat file** | `data/heartbeats/reference_data_worker.json` |
| **Logs emitted** | `logs/reference_data_worker.log` — structured JSON |
| **Safe shutdown** | Writes are atomic (temp→rename); partial writes are safe to interrupt |
| **May import** | `reference_data_builder.py`, `intelligence_schema_validator.py`, `factor_registry.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_options.py`, `orders_state.py`, `event_log.py`, `training_store.py`, `provider_fetch_tester.py`, any broker/IBKR module |
| **Cloud container** | `decifer-reference-data` |
| **Cloud runtime impact** | Negligible — weekly batch; no persistent compute required |

---

#### `provider_ingestion_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Fetch price/volume/quote data from Alpaca and FMP. Validate freshness and schema. Write to staging snapshots. |
| **Owner layer** | Market Sensor / Technical Layer (data ingestion sub-layer) |
| **Production runtime required** | Yes — runs before each bot scan cycle |
| **Input files** | `data/live/current_manifest.json` (for approved symbol list), `data/reference/symbol_master.json` |
| **Output files** | `data/staging/provider_snapshot_{timestamp}.json` |
| **Schedule** | Every scan cycle (configurable interval, e.g. 5 min during market hours) |
| **Dependencies** | `symbol_master.json` must be present and fresh |
| **Freshness SLA** | 10 minutes intraday |
| **Retry policy** | 2 retries on transient failure; provider-level rate-limit backoff; partial data written with `data_completeness` flag |
| **Fail-closed behaviour** | On full failure: do not publish staging snapshot; log fail reason; bot falls back to prior snapshot if within SLA |
| **Healthcheck** | Latest `provider_snapshot_{timestamp}.json` exists and `generated_at` < 15 min |
| **Heartbeat file** | `data/heartbeats/provider_ingestion_worker.json` |
| **Logs emitted** | `logs/provider_ingestion_worker.log` |
| **Safe shutdown** | Atomic writes; in-flight requests drained before exit |
| **May import** | Alpaca SDK, FMP client (`fmp_client.py`), `intelligence_schema_validator.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, `event_log.py`, `provider_fetch_tester.py`, `backtest_intelligence.py` |
| **Cloud container** | `decifer-provider-ingestion` |
| **Cloud runtime impact** | Moderate — runs intraday on scan cadence; Alpaca/FMP API calls; minimal CPU |

---

#### `economic_intelligence_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Run the MacroTransmissionMatrix, generate daily economic context, economic candidate feed, theme activation, current economic context. |
| **Owner layer** | Economic Intelligence Layer |
| **Production runtime required** | Yes — once per trading day (pre-market) |
| **Input files** | `data/intelligence/transmission_rules.json`, `data/intelligence/theme_taxonomy.json`, `data/intelligence/thematic_roster.json`, macro data from Alpha Vantage / FMP |
| **Output files** | `data/intelligence/daily_economic_state.json`, `data/intelligence/current_economic_context.json`, `data/intelligence/theme_activation.json`, `data/intelligence/economic_candidate_feed.json` |
| **Schedule** | Daily 06:00 UTC (pre-market) |
| **Dependencies** | Transmission rules, theme taxonomy — must pass schema validation |
| **Freshness SLA** | 1 trading day |
| **Retry policy** | 3 retries; on macro data unavailability, use prior day context with `freshness_status=stale_fallback` |
| **Fail-closed behaviour** | On failure: retain prior validated economic context; log warning; bot continues with stale-but-valid context if within SLA |
| **Healthcheck** | `current_economic_context.json` exists and `generated_at` < 26 hours |
| **Heartbeat file** | `data/heartbeats/economic_intelligence_worker.json` |
| **Logs emitted** | `logs/economic_intelligence_worker.log` |
| **Safe shutdown** | Atomic writes; safe to interrupt mid-run |
| **May import** | `candidate_resolver.py`, `macro_transmission_matrix.py`, `fmp_client.py`, `intelligence_schema_validator.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, `event_log.py`, `provider_fetch_tester.py` |
| **Cloud container** | `decifer-economic-intelligence` |
| **Cloud runtime impact** | Low — once-daily batch; FMP/AV API calls for macro data |

---

#### `company_quality_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Fetch and score company quality/fundamentals factors: key metrics TTM, earnings acceleration, revenue growth, DCF, analyst consensus. |
| **Owner layer** | Company Quality / Fundamentals Layer |
| **Production runtime required** | TBD — semi-weekly or on earnings event |
| **Input files** | `data/reference/symbol_master.json`, FMP fundamentals endpoints, `data/intelligence/economic_candidate_feed.json` |
| **Output files** | `data/intelligence/company_quality_snapshot_{date}.json` |
| **Schedule** | Weekly or on earnings event trigger |
| **Dependencies** | `symbol_master.json`, FMP API (paid) |
| **Freshness SLA** | 7 days (or 1 day after earnings event) |
| **Retry policy** | 3 retries per symbol; skip symbol on persistent failure; log missing symbols |
| **Fail-closed behaviour** | On failure: retain prior snapshot if within SLA; log warning; handoff publisher does not use stale fundamentals beyond SLA |
| **Healthcheck** | Latest `company_quality_snapshot_{date}.json` < 8 days old |
| **Heartbeat file** | `data/heartbeats/company_quality_worker.json` |
| **Logs emitted** | `logs/company_quality_worker.log` |
| **Safe shutdown** | Atomic writes per symbol batch |
| **May import** | `fmp_client.py`, `intelligence_schema_validator.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, `event_log.py` |
| **Cloud container** | `decifer-company-quality` |
| **Cloud runtime impact** | Moderate — FMP API calls; weekly batch |

---

#### `catalyst_event_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Score catalyst events: earnings surprises, analyst upgrades, Form 4 insider trades, press releases, congressional trades. |
| **Owner layer** | Catalyst / Event Intelligence Layer |
| **Production runtime required** | Yes — runs intraday during earnings season; daily otherwise |
| **Input files** | `data/reference/symbol_master.json`, FMP earnings/analyst/news endpoints, `catalyst_engine.py` outputs |
| **Output files** | `data/intelligence/catalyst_snapshot_{timestamp}.json` |
| **Schedule** | Daily 07:00 UTC; real-time on `NEWS_INTERRUPT` signal |
| **Dependencies** | `symbol_master.json`, FMP API |
| **Freshness SLA** | 4 hours intraday |
| **Retry policy** | 2 retries; partial failures acceptable with `data_completeness` flag |
| **Fail-closed behaviour** | On failure: retain prior catalyst snapshot if within SLA; log warning; handoff publisher notes missing catalyst data |
| **Healthcheck** | Latest catalyst snapshot < 5 hours old during market hours |
| **Heartbeat file** | `data/heartbeats/catalyst_event_worker.json` |
| **Logs emitted** | `logs/catalyst_event_worker.log` |
| **Safe shutdown** | Atomic writes; in-flight FMP calls drained |
| **May import** | `catalyst_engine.py`, `fmp_client.py`, `intelligence_schema_validator.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, `event_log.py` |
| **Cloud container** | `decifer-catalyst-event` |
| **Cloud runtime impact** | Moderate — intraday on catalyst cadence |

---

#### `technical_market_sensor_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Compute technical/market sensor signals: OHLCV bars, RSI, ATR, volume ratio, squeeze, breakout, reversion. Read-only market data. |
| **Owner layer** | Market Sensor / Technical Layer |
| **Production runtime required** | Yes — runs per scan cycle |
| **Input files** | Alpaca OHLCV bars, `data/reference/symbol_master.json`, provider snapshot from `provider_ingestion_worker` |
| **Output files** | `data/staging/technical_snapshot_{timestamp}.json` |
| **Schedule** | Every scan cycle |
| **Dependencies** | `provider_ingestion_worker` snapshot must be present and valid |
| **Freshness SLA** | 10 minutes |
| **Retry policy** | 1 retry on data fetch failure; skip stale symbols; log coverage gaps |
| **Fail-closed behaviour** | On failure: do not publish technical snapshot; Universe Builder falls back to prior snapshot if within SLA |
| **Healthcheck** | Latest technical snapshot < 15 min during market hours |
| **Heartbeat file** | `data/heartbeats/technical_market_sensor_worker.json` |
| **Logs emitted** | `logs/technical_market_sensor_worker.log` |
| **Safe shutdown** | Atomic writes; safe to interrupt |
| **May import** | `signals.py`, `alpaca_client.py` or Alpaca SDK, `intelligence_schema_validator.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, `event_log.py`, `provider_fetch_tester.py` |
| **Cloud container** | `decifer-technical-sensor` |
| **Cloud runtime impact** | Moderate — scan-cycle cadence; Alpaca API calls |

---

#### `universe_builder_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Build the active opportunity universe: merge economic candidates, technical scores, catalyst scores, quality filters. Apply route tags and quota allocation. Produce a validated, route-tagged, quota-allocated active universe snapshot. |
| **Owner layer** | Universe Builder Layer |
| **Production runtime required** | Yes — runs after all upstream workers complete |
| **Input files** | `data/intelligence/economic_candidate_feed.json`, technical snapshot, catalyst snapshot, company quality snapshot, `data/reference/theme_overlay_map.json`, `data/reference/sector_schema.json` |
| **Output files** | `data/staging/active_universe_{timestamp}.json` |
| **Schedule** | Every scan cycle, after provider/technical/catalyst workers complete |
| **Dependencies** | All upstream snapshots must pass `validate_all()`. If any required input fails validation: fail closed. |
| **Freshness SLA** | 10 minutes |
| **Retry policy** | No retry — re-runs on next scan cycle with fresh inputs |
| **Fail-closed behaviour** | On any invalid input: write `validation_status=fail` to staging; do not pass to Handoff Publisher |
| **Healthcheck** | Latest `active_universe_{timestamp}.json` < 15 min and `validation_status=pass` |
| **Heartbeat file** | `data/heartbeats/universe_builder_worker.json` |
| **Logs emitted** | `logs/universe_builder_worker.log` |
| **Safe shutdown** | Atomic writes; safe to interrupt |
| **May import** | `universe_builder.py`, `route_tagger.py`, `compare_universes.py`, `intelligence_schema_validator.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, `event_log.py`, `provider_fetch_tester.py` |
| **Cloud container** | `decifer-universe-builder` |
| **Cloud runtime impact** | Low CPU — purely computational, no API calls |

---

#### `handoff_validator_publisher`
| Field | Value |
|-------|-------|
| **Purpose** | Final gate before live bot consumption. Validate all staged snapshots. Check invariants (no executable trades, no unapproved sources, no stale inputs). Atomically publish `data/live/current_manifest.json`. |
| **Owner layer** | Handoff / Publication Layer |
| **Production runtime required** | Yes — runs after universe_builder_worker |
| **Input files** | All staged snapshots from upstream workers, `intelligence_schema_validator.py` |
| **Output files** | `data/live/current_manifest.json` (only on full pass) |
| **Schedule** | Every scan cycle, after universe_builder_worker |
| **Dependencies** | All staged snapshots must pass schema validation. `enable_active_opportunity_universe_handoff` must be True before publishing. |
| **Freshness SLA** | 10 minutes |
| **Retry policy** | No retry — fail closed; new manifest published on next cycle |
| **Fail-closed behaviour** | On any validation failure: do NOT overwrite current_manifest.json; write `data/live/manifest_fail_{timestamp}.json` with fail reason; log structured error |
| **Healthcheck** | `current_manifest.json` exists, `validation_status=pass`, `published_at` < 15 min |
| **Heartbeat file** | `data/heartbeats/handoff_validator_publisher.json` |
| **Logs emitted** | `logs/handoff_validator_publisher.log` |
| **Safe shutdown** | Atomic rename for manifest publication; safe to interrupt before rename |
| **May import** | `intelligence_schema_validator.py`, all validator functions |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, `event_log.py`, any LLM caller, `provider_fetch_tester.py` |
| **Cloud container** | `decifer-handoff-publisher` |
| **Cloud runtime impact** | Negligible — CPU-only validation gate |

---

#### `live_trading_bot`
| Field | Value |
|-------|-------|
| **Purpose** | Execute the scan-score-decide-execute loop. When handoff is enabled, consume active universe from manifest only. Apex synthesiser calls. Position management. |
| **Owner layer** | Trading Bot / Entry Readiness Layer + Execution / Risk Layer |
| **Production runtime required** | Yes — the primary production process |
| **Input files** | `data/live/current_manifest.json` (when handoff enabled), `data/active_opportunity_universe.json` (current, pre-handoff), `data/trades.json`, `data/positions.json` |
| **Output files** | `data/event_log.jsonl`, `data/training_records.jsonl`, `data/active_trades.json` |
| **Schedule** | Continuous during market hours |
| **Dependencies** | IBKR TWS/Gateway (paper account), Alpaca (market data), FMP (news), Anthropic API (Apex) |
| **Freshness SLA** | N/A — real-time |
| **Retry policy** | IBKR reconnect with exponential backoff; scan cycle retried on transient failure |
| **Fail-closed behaviour** | On manifest missing/stale/invalid: continue scanning from prior source; log structured warning; do not halt (graceful degrade) |
| **Healthcheck** | `bot_trading.py` heartbeat; last scan timestamp < 10 min |
| **Heartbeat file** | `data/heartbeats/live_trading_bot.json` |
| **Logs emitted** | `logs/bot_trading.log` |
| **Safe shutdown** | EOD flat on signal; drain in-flight orders; write final event_log entries |
| **May import** | `orders_core.py`, `orders_options.py`, `orders_state.py`, `event_log.py`, `market_intelligence.py`, `signals.py`, `config.py` |
| **Must not import** | `factor_registry.py`, `provider_fetch_tester.py`, `backtest_intelligence.py`, `advisory_reporter.py`, `advisory_log_reviewer.py`, `reference_data_builder.py` (directly), raw news ingesters (directly), provider ingestion workers (directly) |
| **Cloud container** | `decifer-live-bot` |
| **Cloud runtime impact** | High — always-on during market hours; IBKR TWS dependency; Anthropic API calls |

---

#### `execution_risk_gateway`
| Field | Value |
|-------|-------|
| **Purpose** | Isolated execution boundary. Only process permitted to call IBKR order, account, and position APIs. Enforces pre-trade risk checks before order submission. |
| **Owner layer** | Execution / Risk Layer |
| **Production runtime required** | Yes |
| **Input files** | Orders from `live_trading_bot` (internal call or IPC) |
| **Output files** | Order confirmations, `data/event_log.jsonl` (ORDER_FILLED events) |
| **Schedule** | Continuous during market hours |
| **Dependencies** | IBKR TWS/Gateway (paper: DUP481326) |
| **Freshness SLA** | Real-time |
| **Retry policy** | Order submission: 1 retry on timeout; do not duplicate orders |
| **Fail-closed behaviour** | On IBKR disconnect: halt order submission; cancel pending; log disconnect |
| **Healthcheck** | IBKR connection alive; last heartbeat < 30s |
| **Heartbeat file** | `data/heartbeats/execution_risk_gateway.json` |
| **Logs emitted** | `logs/execution_risk.log` |
| **Safe shutdown** | Cancel open limit orders; drain fills; write final event_log entries |
| **May import** | `bot_ibkr.py`, `orders_core.py`, `orders_state.py`, `event_log.py` |
| **Must not import** | Any intelligence layer module; `advisory_reporter.py`; `provider_fetch_tester.py`; `reference_data_builder.py` |
| **Cloud container** | `decifer-execution-risk` (co-located with live bot in Phase 1) |
| **Cloud runtime impact** | High — always-on; IBKR TWS persistent connection |

---

#### `observability_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Aggregate heartbeats, log volumes, snapshot freshness, and advisor/registry metrics. Write structured health summaries. Feed Chief Decifer dashboard. |
| **Owner layer** | Advisory / Observability Layer |
| **Production runtime required** | Yes (low priority) |
| **Input files** | All `data/heartbeats/*.json`, all logs, snapshot files |
| **Output files** | `data/observability/health_summary.json`, `chief-decifer/state/` |
| **Schedule** | Every 60s |
| **Dependencies** | None — advisory only; never blocks other workers |
| **Freshness SLA** | 2 minutes |
| **Retry policy** | No retry; skip on failure; log failure |
| **Fail-closed behaviour** | On failure: do nothing; observability failure does not block production |
| **Healthcheck** | Self-monitored; alerting via external check if needed |
| **Heartbeat file** | `data/heartbeats/observability_worker.json` |
| **Logs emitted** | `logs/observability_worker.log` |
| **Safe shutdown** | Immediate; no state persistence required |
| **May import** | Read-only file access; `intelligence_schema_validator.py` |
| **Must not import** | Any execution/order/position module |
| **Cloud container** | `decifer-observability` |
| **Cloud runtime impact** | Negligible |

---

#### `backtest_research_worker`
| Field | Value |
|-------|-------|
| **Purpose** | Run Alphalens factor analysis, IC validation, walk-forward backtests, HMM training. Offline only. Never interacts with live bot. |
| **Owner layer** | Backtest / Research Layer |
| **Production runtime required** | No — offline only |
| **Input files** | `data/training_records.jsonl`, `data/ic_validation_result.json`, historical snapshots |
| **Output files** | `data/backtest/`, `data/ic_validation_result.json`, `data/walk_forward_results/` |
| **Schedule** | On demand; never intraday |
| **Dependencies** | Sufficient closed trades (gate: ≥200 for HMM, ≥50 for ML) |
| **Freshness SLA** | N/A |
| **Retry policy** | N/A |
| **Fail-closed behaviour** | Failure is isolated; no production impact |
| **Healthcheck** | N/A |
| **Heartbeat file** | None |
| **Logs emitted** | `logs/backtest_research.log` |
| **Safe shutdown** | Immediate |
| **May import** | `backtest_intelligence.py`, `ml_engine.py`, `learning.py`, `training_store.py` |
| **Must not import** | `bot_trading.py`, `orders_core.py`, `orders_state.py`, execution modules |
| **Cloud container** | Not in production containers |
| **Cloud runtime impact** | None — developer workstation or offline job only |

---

## 3. Dependency Model

### 3.1 Parallel vs Sequential

**Can run in parallel (no mutual dependency):**
- `reference_data_worker`
- `provider_ingestion_worker`
- `economic_intelligence_worker`
- `company_quality_worker`
- `catalyst_event_worker`

**Must wait for validated upstream snapshots:**
- `technical_market_sensor_worker` ← requires `provider_ingestion_worker` snapshot valid
- `universe_builder_worker` ← requires all of: economic_candidate_feed, technical_snapshot, catalyst_snapshot, company_quality_snapshot, all valid
- `handoff_validator_publisher` ← requires `universe_builder_worker` output valid

**Runs independently of all intelligence workers:**
- `live_trading_bot` (reads `current_manifest.json` — produced upstream by handoff_validator_publisher)
- `execution_risk_gateway` (receives orders from live_trading_bot only)
- `observability_worker` (advisory; reads all; blocks nothing)
- `backtest_research_worker` (offline; reads training data only)

### 3.2 File Classification

**Handoff candidates** (may be referenced by `current_manifest.json` when handoff is enabled):
- `data/staging/active_universe_{timestamp}.json`
- `data/intelligence/economic_candidate_feed.json`
- `data/intelligence/current_economic_context.json`
- `data/staging/technical_snapshot_{timestamp}.json`
- `data/staging/catalyst_snapshot_{timestamp}.json`

**Diagnostic only** (never referenced by live manifest):
- `data/reference/provider_fetch_test_results.json`
- `data/reference/provider_capability_matrix.json`
- `data/reference/factor_registry.json`
- `data/reference/data_quality_report.json`

**Backtest-only** (never in live containers):
- `data/backtest/`
- `data/walk_forward_results/`
- `data/ic_validation_result.json`

**Advisory-only** (shadow pipeline; never in live execution path):
- `data/intelligence/advisory_runtime_log.jsonl`
- `data/intelligence/advisory_log_review.json`
- `data/intelligence/advisory_report.json`
- `data/intelligence/coverage_gap_review.json`
- `data/intelligence/active_opportunity_universe_shadow.json`
- `data/intelligence/current_vs_shadow_comparison.json`

**Must never be read by the live bot:**
- `data/reference/provider_fetch_test_results.json`
- `data/reference/factor_registry.json`
- `data/reference/data_quality_report.json`
- `data/backtest/`
- All advisory-only files
- `advisory_reporter.py`, `advisory_log_reviewer.py`, `advisory_logger.py` (may only be imported by advisory-only processes)

### 3.3 Canonical Statement

> Provider ingestion, company quality, catalyst, macro and technical jobs may run in parallel, but the Universe Builder and Handoff Publisher must only consume validated snapshots.

---

## 4. Live Bot Isolation Rule

### 4.1 What the Live Bot May Read

When `enable_active_opportunity_universe_handoff = True`:
- `data/live/current_manifest.json`
- The active universe file referenced by `manifest["active_universe_file"]`
- The economic context file referenced by `manifest["economic_context_file"]`
- The technical/market sensor snapshot referenced by `manifest["technical_snapshot_file"]` (if present)
- The risk/execution config referenced by `manifest["risk_snapshot_file"]` (if present)
- `data/active_trades.json`, `data/positions.json` (own state)
- `config.py` (read-only)

When `enable_active_opportunity_universe_handoff = False`:
- All of the above except manifest-referenced files
- `data/active_opportunity_universe.json` (legacy path, pre-handoff)
- `data/scanner_output.json` (legacy, pre-handoff)

### 4.2 What the Live Bot Must Not Import or Call

The following are explicitly forbidden from `bot_trading.py` and any module it imports:

| Module | Reason |
|--------|--------|
| `factor_registry.py` | Reference/diagnostic build tool |
| `provider_fetch_tester.py` | Diagnostic connectivity tester |
| `backtest_intelligence.py` | Offline research tool |
| `advisory_reporter.py` | Advisory shadow pipeline |
| `advisory_log_reviewer.py` | Advisory batch reviewer |
| `reference_data_builder.py` | Reference data build tool (offline) |
| Provider ingestion workers directly | Intelligence architecture boundary |
| Raw news ingestion directly | Catalyst worker boundary |
| LLM tools (beyond Apex) | Architecture boundary |
| Scanner-led discovery when handoff enabled | Replaces scanner; do not run both |

---

## 5. Execution/Risk Isolation Rule

### 5.1 Execution/Risk Boundary

`execution_risk_gateway` is the **only** process permitted to call:
- IBKR order placement APIs
- IBKR account APIs
- IBKR position APIs
- IBKR execution report APIs

### 5.2 Layers That Must Not Cross This Boundary

The following layers must never call order, account, position, or execution endpoints:

- Economic Intelligence Layer (`candidate_resolver.py`, `macro_transmission_matrix.py`)
- Reference Data Layer (`reference_data_builder.py`, `intelligence_schema_validator.py`)
- Universe Builder Layer (`universe_builder.py`, `route_tagger.py`)
- Catalyst Layer (`catalyst_engine.py`)
- Technical Sensor Layer (`signals.py` used in sensor mode)
- Advisory Layer (`advisory_reporter.py`, `advisory_logger.py`)
- Backtest / Research Layer

### 5.3 Provider Data Fetches vs Trading API Calls

Data-provider fetches (Alpaca market data, FMP fundamentals) are explicitly allowed in intelligence layers. They are read-only market data, not trading or broker calls. The distinction is preserved in the safety flag schema:

- `data_provider_api_called = true/false` — market data fetches
- `trading_api_called = false` — must always be false outside execution boundary
- `broker_order_api_called = false` — must always be false outside execution boundary
