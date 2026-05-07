# Intelligence-First: Definitions and Runtime Contract

**Created:** 2026-05-07
**Sprint:** 7A.4
**Status:** Living document — update when architecture changes
**Owner:** Cowork (Claude)
**Approver:** Amit

This document is the authoritative definitions source for all Intelligence-First architecture terms. When a term is used in code, tests, or documentation, its meaning must be consistent with this document.

---

## 1. North Star

Decifer is an autonomous paper-trading system generating high-quality training data across market regimes to eventually validate a live trading system.

The Intelligence-First architecture separates the intelligence-gathering, synthesis, and decision-support layers from the execution layer. Intelligence workers produce validated, versioned snapshots. The live bot consumes one approved manifest. Execution and risk remain isolated.

**Every feature, file, and process must be classifiable as one of:**
- production runtime
- advisory-only
- shadow-only
- backtest-only
- adapter-only
- temporary migration tool
- deprecated / ready-to-remove

Anything that cannot be classified belongs to one of these categories or should not exist.

---

## 2. Layer Definitions

### Economic Intelligence Layer (EIL)
The layer responsible for transforming macro drivers, geopolitical signals, credit conditions, and sector rotation themes into a scored, structured economic candidate feed. The EIL does not score individual stocks — it identifies which themes and sectors are receiving macro tailwinds or headwinds. Components: `macro_transmission_matrix.py`, `candidate_resolver.py`, `economic_candidate_feed.json`, `current_economic_context.json`, `theme_activation.json`, `daily_economic_state.json`.

### Reference Data Layer
The layer responsible for building and maintaining static reference artifacts: sector taxonomy, symbol identity, theme overlay maps, factor registry, and provider capability matrix. Updated weekly or on demand. Not a runtime layer — it runs as an offline scheduled job. Components: `reference_data_builder.py`, `factor_registry.py`, `intelligence_schema_validator.py` (in reference mode), all files in `data/reference/`.

### Symbol Master
A static reference file (`data/reference/symbol_master.json`) listing all symbols in the committed universe with their canonical identity: ticker, sector, industry, classification, proxy type (if applicable), and approved data sources. Updated weekly. The symbol master is the source of truth for "what symbols does this system know about." It is not a live candidate list.

### Sector Schema
A static reference file (`data/reference/sector_schema.json`) defining the sector and classification taxonomy used across the intelligence architecture. Includes 7 proxy classification types: etf_proxy, index_proxy, commodity_proxy, crypto_proxy, volatility_proxy, macro_proxy, unknown. Updated weekly.

### Theme Overlay Map
A static reference file (`data/reference/theme_overlay_map.json`) mapping every intelligence-layer theme to its canonical and proxy symbols. Contains 84 themes (as of Sprint 7A.2). Used by the Universe Builder to resolve theme membership. The overlay map is the single authoritative source for "which symbols belong to which theme." It does not make route decisions — route decisions are made by `route_tagger.py`.

### Factor Registry
A static reference file (`data/reference/factor_registry.json`) defining every data factor used or planned across the intelligence architecture: 73 factors, 13 categories, 10 layers. Defines which providers own each factor, which layers consume it, and whether it is production-runtime-allowed. The factor registry is a design and audit document — it is not loaded by the live bot.

### Provider Capability Matrix
A static reference file (`data/reference/provider_capability_matrix.json`) defining what each data provider can supply and at what suitability tier (primary_candidate, secondary_candidate, fallback_only, research_only, not_suitable). 6 providers: alpaca, fmp, alpha_vantage, yfinance, ibkr, local_files.

### Company Quality / Fundamentals Layer
The layer responsible for scoring company-level quality factors: earnings acceleration, revenue growth, balance sheet strength, key metrics TTM, analyst consensus, DCF. Produced by `company_quality_worker`. Not yet wired to production — TBD Phase 1.

### Catalyst / Event Intelligence Layer
The layer responsible for detecting and scoring high-conviction catalysts: earnings surprises, analyst upgrades/downgrades, Form 4 insider buys, press releases, congressional trades. Powered by `catalyst_engine.py` and FMP event feeds. Produces `catalyst_snapshot_{ts}.json`. Wired to live bot via the catalyst screener (pre-handoff) and will be referenced by manifest in Phase 1 handoff.

### Market Sensor / Technical Layer
The layer responsible for computing technical/price-action signals on market data: OHLCV bars, momentum, squeeze, breakout, reversion, volume ratio. Distinct from the Scanner (see Section 10). Reads Alpaca OHLCV. Produces `technical_snapshot_{ts}.json`. This layer provides structured signal scores — it does not decide which symbols to trade.

### Universe Builder
The module (`universe_builder.py`) and associated worker (`universe_builder_worker`) that merges economic candidates, technical scores, catalyst scores, and quality filters into a route-tagged, quota-allocated active universe snapshot. Applies `route_tagger.py` and quota allocation. Output is validated before passing to the Handoff Publisher. The universe builder does not generate new symbol ideas — it merges and ranks.

### Route Tagger
`route_tagger.py` — a pure deterministic function that assigns a `RouteDecision` (position, swing, intraday, watchlist) to each candidate based on `RouteContext`. 10 ordered rules. No randomness. No LLM. No API calls. The route tagger is the only module that converts a theme membership + score combination into a specific route type.

### Quota Allocator
The module (within `universe_builder.py`) that enforces structural quotas by source and theme. Prevents any single source or theme from dominating the active universe. Outputs per-source demand vs capacity diagnostics.

### Handoff Reader
The component inside `bot_trading.py` that reads `data/live/current_manifest.json` when `enable_active_opportunity_universe_handoff = True`. Validates manifest freshness and schema before consuming referenced files. Not yet implemented — planned for Sprint 7B.

### Handoff Publisher
`handoff_validator_publisher` — the final gate before live bot consumption. Validates all staged snapshots, checks all invariants, and atomically publishes `data/live/current_manifest.json` only on full pass. The single authorised writer of the live manifest.

### Current Manifest
`data/live/current_manifest.json` — the live pointer consumed by the trading bot. References all approved upstream snapshot files. Contains `handoff_enabled`, `validation_status`, `expires_at`. The bot reads only this file and the files it references.

### Advisory Mode
The intelligence pipeline running in pure read-only observation mode. `intelligence_first_advisory_enabled = True` (currently False — observation phase complete). Advisory mode attaches to each scan cycle, compares intelligence candidates against actual bot candidates, and logs advisory context to `advisory_runtime_log.jsonl`. No production decision is changed. No Apex input is changed.

### Paper Handoff
The first production handoff of intelligence universe data to the live bot, operating on the paper trading account. `handoff_mode = paper`. `enable_active_opportunity_universe_handoff` set to True. The bot reads the manifest-referenced active universe instead of running scanner-led discovery. Paper handoff is designed in Sprint 7B.

### Production Handoff
The live-trading handoff, after paper handoff is validated over ≥3 months. Not planned. Requires explicit Amit approval.

### Fail-Closed
The default behaviour when a required input is missing, expired, invalid, or fails an invariant check: the consuming process takes the safest possible action (hold positions, do not enter new positions, do not publish an invalid manifest) and logs a structured reason. Fail-closed is not a crash — it is a controlled degradation.

---

## 3. Runtime Definitions

### Production Runtime
A module, file, or process that runs during live bot scan cycles and may influence trade decisions, position management, or order submission. Production runtime modules must be production-grade: fail-closed, observable, no offline tools imported.

### Advisory-Only
A module or file that exists only in the advisory/shadow pipeline. Does not influence production decisions. May be imported by advisory workers but must never be imported by `bot_trading.py` or any module it imports at runtime.

Examples: `advisory_reporter.py`, `advisory_log_reviewer.py`, `advisory_logger.py`, `advisory_runtime_log.jsonl`, `advisory_report.json`, `advisory_log_review.json`, `coverage_gap_review.json`.

### Shadow-Only
A module or file that mirrors production computation but whose output is never consumed by the live bot. Shadow computations are validated against actual bot outputs for divergence analysis.

Examples: `active_opportunity_universe_shadow.json`, `current_vs_shadow_comparison.json`, shadow mode outputs of `universe_builder.py`.

### Backtest-Only
A module or file used exclusively for offline research, factor analysis, ML training, or IC validation. Never deployed in production containers. Never imported by production runtime modules.

Examples: `backtest_intelligence.py`, `ml_engine.py`, `data/backtest/`, `data/walk_forward_results/`.

### Adapter-Only
A module or file that bridges between two systems or formats for a specific integration point. Often temporary. Examples: `advisory_log_reviewer.py` (advisory adapter), `compare_universes.py` (comparison adapter).

### Temporary Migration Tool
A module or file that exists only during a migration phase and will be deleted when migration is complete. Must be marked in the retirement register with removal conditions. Examples: Legacy pipeline flags, migration compatibility shims.

### Deprecated / Ready-to-Remove
A module or file that has been superseded and whose removal conditions are met or nearly met. Listed in the retirement register.

---

## 4. Data Contract Definitions

### Snapshot
Any JSON file produced by an intelligence worker with a `generated_at`, `expires_at`, and `validation_status` field. All snapshots follow the Universal Snapshot Schema (see `intelligence_first_snapshot_contract.md`).

### Source Label
A string field on each snapshot, candidate, and rule identifying which governance path approved this data. Examples: `intelligence_first_static_rule`, `reference_data_approved_theme`, `coverage_gap_review`. Source labels enable the Handoff Publisher to verify that no unapproved source has entered the pipeline.

### Approval Status
A field indicating whether a symbol or theme has been explicitly approved for production use. Values: `approved`, `under_review`, `provisional`, `excluded`.

### Freshness Status
A field in every snapshot indicating its temporal validity relative to SLA. Values: `fresh`, `stale_fallback`, `expired`, `missing_inputs`.

---

## 5. Source Label Definitions

| Source Label | Meaning |
|---|---|
| `intelligence_first_static_rule` | Rule defined in `transmission_rules.json` as part of the core Intelligence-First architecture |
| `reference_data_approved_theme` | Theme or symbol approved through the formal coverage-gap review process |
| `coverage_gap_review` | Symbol promoted from advisory evidence (recurring unsupported) through the governance chain |
| `thematic_roster` | Symbol present in `data/intelligence/thematic_roster.json` |
| `committed_universe` | Symbol from the weekly committed top-1000 universe |
| `dynamic_add` | Symbol added dynamically (catalyst hit, news, held position) |
| `favourites` | Symbol from `data/favourites.json` — explicit Amit addition, not discovery |

---

## 6. Approval Status Definitions

| Value | Meaning |
|-------|---------|
| `approved` | Symbol or theme has passed governance review and may appear in the production universe |
| `under_review` | Symbol or theme is in observation; present in advisory/shadow only |
| `provisional` | Symbol approved with conditions (e.g. IREN: `watchlist_or_swing` bias only; no position entries) |
| `excluded` | Symbol explicitly excluded from the universe |

---

## 7. Route Definitions

### Route Types
- `position` — Full position entry (longest duration, highest conviction)
- `swing` — Swing trade (multi-day hold)
- `intraday` — Single-day only
- `watchlist` — No entry; monitor only

### Route Bias
A recommended route constraint on a theme or symbol. Examples: `position_or_swing`, `swing_only`, `watchlist_only`, `swing_or_watchlist`, `watchlist_or_swing`. Route bias does not override Apex — it is an advisory constraint that informs the route assignment.

### Route Hint
A field on a raw candidate indicating its source's suggested route. The route tagger considers route hints but applies deterministic rules to produce the final `RouteDecision`.

### Reason-to-Care
A field on a candidate explaining why it is in the universe: the macro driver, theme activation, catalyst, or technical signal that caused it to be included. Used in Apex prompts to give context.

---

## 8. Handoff Definitions

### Handoff Enabled
`enable_active_opportunity_universe_handoff = True` in `config.py`. When true, the live bot reads candidates from the manifest-referenced active universe rather than running scanner-led discovery. Currently `False`.

### Handoff Mode
The operational mode of the handoff: `paper` (paper account), `live` (live account — not planned), `shadow` (observatory only).

### Paper Handoff Reader
The component in `bot_trading.py` that reads and validates `current_manifest.json` when handoff is enabled. Not yet implemented — Sprint 7B.

---

## 9. Advisory versus Production Definitions

| Dimension | Advisory | Production |
|-----------|----------|------------|
| Influences trade decisions | No | Yes |
| Influences Apex input | No | Yes |
| Changes `active_trades` | No | Yes |
| Changes `event_log` | No | Yes |
| Changes `positions` | No | Yes |
| `live_output_changed` | Always false | May be true (intentionally) |
| `no_executable_trade_instructions` | Always true | N/A — production generates trades |
| Imported by `bot_trading.py` | No | May be |
| In production containers | No (advisory-only) | Yes |

---

## 10. Scanner versus Market Sensor

### Scanner (Legacy / Pre-Handoff)
`scanner.py` and `score_universe()` — the original symbol discovery and scoring mechanism. Runs within the live bot's scan cycle. Pulls symbols from the committed universe and dynamic adds. Scores each symbol across 10 signal dimensions. Produces `active_opportunity_universe.json`. **This is the current production path (pre-handoff).** When `enable_active_opportunity_universe_handoff = True`, the scanner is replaced by the manifest-referenced active universe for candidate discovery.

### Market Sensor (Intelligence-First)
`technical_market_sensor_worker` — the Intelligence-First equivalent of the scanner's technical computation, running as an independent worker. Produces `technical_snapshot_{ts}.json`. Does not decide which symbols to trade — it provides signals that the Universe Builder incorporates. The market sensor runs on the approved active universe, not the full committed universe.

### Key Distinction
The scanner discovers AND scores symbols in one process within the live bot. The market sensor scores symbols that have already been approved by the universe builder. Discovery (which symbols to consider) is now owned by the Economic Intelligence Layer and Universe Builder — not the scanner.

---

## 11. Favourites versus Approved Roster

### Favourites
`data/favourites.json` — a manually curated list of symbols that Amit wants the bot to always consider. Favourites are added to the dynamic universe (always included in candidate list). Favourites are not a discovery mechanism — they bypass normal scoring gates. Favourites must never be used as a workaround for governance gaps.

### Approved Roster
`data/intelligence/thematic_roster.json` — the governed list of symbols approved for each theme through the full governance chain: coverage_gap_review → theme_overlay_map → theme_taxonomy → transmission_rules → thematic_roster → candidate_resolver → economic_candidate_feed. Approved roster symbols appear in the intelligence pipeline through the proper governance path.

### Rule
If a symbol should be traded because it is a good fit for a macro theme, it must be approved through the roster governance chain. Adding it to `favourites.json` as a workaround is not acceptable — it bypasses governance and cannot be audited.

---

## 12. Factor Ownership Definition

Each factor in the Factor Registry has an `owning_layer` (the layer responsible for producing it) and `consuming_layers` (layers that use it). Factor ownership determines which worker fetches each factor and which snapshot it appears in. No factor may be fetched by a layer that does not own it, except via the snapshot it is published in.

---

## 13. Provider Suitability Definitions

| Tier | Meaning |
|------|---------|
| `primary_candidate` | Default provider for this factor category; production-grade; meets SLA |
| `secondary_candidate` | Usable in production with caveats; may have rate limits or partial coverage |
| `fallback_only` | Use only when primary is unavailable; may have SLA or quality limitations |
| `research_only` | Suitable for offline analysis only; not for production runtime |
| `not_suitable` | Cannot provide this factor at required quality/latency |

---

## 14. Fail-Closed Definition

Fail-closed means: when an error, missing input, or invariant breach is detected, the system takes the action that protects production integrity and real capital, not the action that tries to continue despite the problem.

In practice:
- Handoff Publisher does not publish a manifest on validation failure
- Live bot does not enter new positions if manifest is expired or invalid
- Universe Builder does not produce a universe if required inputs fail validation
- Workers do not overwrite valid existing files when they fail to produce a valid replacement

Fail-closed is not the same as halting the bot. The bot continues managing existing positions. Only new entry decisions are blocked until valid inputs are restored.

---

## 15. Classification Definitions

See Section 3 (Runtime Definitions) for full definitions of: Production Runtime, Advisory-Only, Shadow-Only, Backtest-Only, Adapter-Only, Temporary Migration Tool, Deprecated.

---

## 16. Terms Deprecated or Replaced

These terms are deprecated. They must not appear in new code, tests, or documentation.

| Deprecated Term | Replaced By | Notes |
|-----------------|-------------|-------|
| Scanner-led universe builder | Intelligence-First universe builder | Scanner still runs pre-handoff; replaced by manifest handoff on `enable_active_opportunity_universe_handoff = True` |
| Flat Tier A/B/C/D priority | Route bias + structural quota | Tier D discovery path replaced by governed thematic roster |
| Favourites as discovery | Approved roster (thematic_roster.json) | Favourites remain for explicit Amit inclusions; not for new theme coverage |
| Live bot as intelligence generator | Intelligence workers + handoff manifest | Live bot consumes intelligence, does not generate it (post-handoff) |
| Apex as symbol discovery | Universe Builder + EIL | Apex synthesises from pre-built candidate list; does not discover symbols |
| Raw news as primary discovery | Catalyst Event Layer | Raw news feeds into catalyst scoring; not used as the discovery mechanism |
| `live_api_called = false` (generic) | `data_provider_api_called`, `live_trading_api_called` (precise) | Replaced by Sprint 7A.3 patch for accuracy |
| `env_inspected = false` (generic) | `env_presence_checked`, `env_values_logged`, `env_file_read` (precise) | Replaced by Sprint 7A.3 patch |
| `agents.py` | Removed (post-Decifer 3.0) | Legacy 4-agent pipeline deleted 2026-04-27 |
| `run_portfolio_review()` | `apex_call()` Track B | Decifer 3.0 replacement |
| `trade_log.py` (SQLite WAL) | `event_log.py` (JSONL write-ahead) | Replaced 2026-04-28 |
| `trade_store.py` | `training_store.py` | Replaced 2026-04-28 |
| TradingView Screener | Three-tier committed universe | Removed; replaced by Alpaca-sourced universe |
