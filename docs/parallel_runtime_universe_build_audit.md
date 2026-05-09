# Parallel Runtime Universe Build Audit

**Date:** 2026-05-09
**Branch:** audit/parallel-runtime-universe-build
**Author:** Cowork (Claude)
**Scope:** Read-only inspection — no production code changed, no flags flipped, no tests added.

---

## 15 Questions Answered

| # | Question | Answer |
|---|---------|--------|
| 1 | What is live today? | See Section A + D |
| 2 | What is built but disabled? | See Section E |
| 3 | What currently runs manually? | See Section F |
| 4 | What can run on weekend / after-hours? | See Section G |
| 5 | What is truly parallel or independently runnable? | See Section H |
| 6 | What is still sequential script chaining? | See Section I |
| 7 | What files produce the governed universe? | See Section B |
| 8 | What files consume the governed universe? | See Section B |
| 9 | What is the real current data flow? | See Section B |
| 10 | What is missing for true multi-process cloud operation? | See Section M |
| 11 | Which layer outputs are durable files? | See Section D |
| 12 | Which layer outputs are only reports or diagnostics? | See Section D (diagnostic column) |
| 13 | Which processes should become scheduled workers? | See Section J + L |
| 14 | Which modules must never run inside the live bot process? | See Section K |
| 15 | What is the shortest practical path to parallel runtime? | See Section N |

---

## A. Current Runtime Map

**Process model today:** Single `bot.py` process. One Python interpreter. `schedule` library (not apscheduler, celery, asyncio). All scheduled jobs run **inside the bot process** via `schedule.run_pending()` polled every ~1 second in the main loop. If the bot is down, no scheduled job runs.

```
bot.py (PID: one process)
│
├── schedule.run_pending() — main loop
│   ├── presession_catalyst_pipeline    @ 08:00 ET daily
│   ├── run_promoter                    @ 08:00 ET daily
│   ├── run_promoter                    @ 16:15 ET daily
│   └── refresh_committed_universe      @ 23:00 ET Sunday
│
├── scan cycle (interval adapts to market session)
│   ├── universe_builder.build()        ← assembles candidate list
│   ├── signal_pipeline.run()           ← scores candidates (10-14 dims)
│   ├── entry_gate filters
│   ├── apex_orchestrator.run_track_a() ← new entries (live execute)
│   ├── apex_orchestrator.run_track_b() ← PM review (live execute)
│   └── apex_orchestrator.run_shadow()  ← divergence log only
│
├── news_sentinel (background thread)   ← polls every 45s
├── catalyst_engine threads             ← news 60s, EDGAR 600s
├── IBKR streaming (background thread)
└── Flask dashboard server (implicit thread)
```

**Concurrency:** Thread-based only. `ThreadPoolExecutor` used in two places:
- `signal_pipeline.py` — `max_workers=1` (news fetch, effectively serial)
- `options_scanner.py` — `max_workers=6` (option contract scoring)

No asyncio. No multiprocessing. No distributed queue. No subprocess-per-worker.

---

## B. Actual Data Flow Today

### Universe production chain

```
COMMITTED UNIVERSE (weekly)
  universe_committed.refresh_committed_universe()
    → Alpaca: enumerate ~12k tradable US equities
    → filter: price ≥ $1, prev_volume ≥ 50k, dollar_volume ≥ $1M
    → keep top 1000 by dollar_volume
    → write: data/committed_universe.json   [atomic]

DAILY PROMOTER (twice-daily: 08:00 + 16:15 ET)
  universe_promoter.run_promoter()
    → load data/committed_universe.json
    → snapshot all symbols via Alpaca
    → score: gap_pct × w + pm_vol_ratio × w + catalyst_score × w
    → keep top 50
    → write: data/daily_promoted.json   [atomic tempfile→replace]

POSITION RESEARCH (Tier D — on-demand)
  universe_position.run()
    → FMP: fundamental screening
    → write: data/position_research_universe.json   [atomic]

ECONOMIC INTELLIGENCE (disabled today — intelligence_first_candidate_feed_enabled=False)
  intelligence_engine.py
    → reads local JSON files only (no live API, no LLM)
    → write: data/intelligence/economic_candidate_feed.json
    → STATUS: flag disabled; file may be stale or missing
```

### Universe assembly (every scan cycle, inline in bot process)

```
universe_builder.build()
  sources (priority order):
    1. data/favourites.json              ← manual conviction (protected)
    2. data/intelligence/economic_candidate_feed.json  ← disabled today
    3. data/position_research_universe.json            ← Tier D
    4. catalyst_engine.get_snapshot()    ← live catalyst scores
    5. data/daily_promoted.json          ← Tier B top-50
    6. scanner.CORE_SYMBOLS              ← Tier A hardcoded floor

  route_tagger.assign_route(candidate)   ← pure function, no I/O
  quota_allocator.allocate(candidates)   ← pure function, 75-cap policy

  → write: data/universe_builder/active_opportunity_universe_shadow.json
```

### Handoff to live bot (Sprint 7J.4 — LIVE)

```
enable_active_opportunity_universe_handoff = True  (config.py)

handoff_publisher.publish()
  → validates active_opportunity_universe_shadow.json
  → writes: data/live/current_manifest.json  [or equivalent manifest path]

handoff_reader.read_manifest()
  → read by bot_trading.py at each scan cycle start
  → replaces scanner-led discovery when handoff is enabled
```

**Current status of handoff:** Flag is True (Sprint 7J.4). Runtime confirmation from live bot logs is pending — this audit cannot verify actual manifest consumption without a live scan cycle log.

### Scoring and execution

```
signal_pipeline.run(candidates)
  → 10-14 dimensions scored per candidate
  → IC weights applied (rolling 200-day window, Sprint IC Phase 2)
  → writes: data/signals_log.jsonl, data/tier_d_funnel.jsonl   [append]

apex_orchestrator.run_track_a()
  → claude-sonnet-4-6 (one call per cycle)
  → ApexDecision → new_entries[]

orders_core.execute_buy() / execute_short()
  → writes: data/trade_events.jsonl   [WAL, fsync-protected]
  → writes: data/training_records.jsonl on POSITION_CLOSED
```

---

## C. Intended Data Flow

Documented in:
- [`docs/intelligence_first_runtime_orchestration.md`](intelligence_first_runtime_orchestration.md)
- [`docs/intelligence_first_cloud_process_map.md`](intelligence_first_cloud_process_map.md)

The intended architecture (Phase 1 — Single VM/Docker Compose):

```
INDEPENDENT WORKERS (run in parallel, produce isolated file snapshots)
  ┌─────────────────────────────────────────────────────────────┐
  │  reference_data_worker    (weekly, Sunday 02:00 UTC)        │
  │    → data/reference/sector_schema.json                      │
  │    → data/reference/symbol_master.json                      │
  │    → data/reference/theme_overlay_map.json                  │
  │    → data/reference/factor_registry.json                    │
  ├─────────────────────────────────────────────────────────────┤
  │  economic_intelligence_worker  (daily pre-market)           │
  │    → data/intelligence/economic_candidate_feed.json         │
  │    → data/intelligence/theme_activation.json                │
  ├─────────────────────────────────────────────────────────────┤
  │  provider_ingestion_worker  (each scan cycle)               │
  │    → data/intelligence/provider_snapshot_*.json             │
  ├─────────────────────────────────────────────────────────────┤
  │  catalyst_event_worker  (daily + NEWS_INTERRUPT)            │
  │    → catalyst scores (internal store)                       │
  ├─────────────────────────────────────────────────────────────┤
  │  company_quality_worker  (weekly or on earnings)            │
  │    → fundamental quality scores                             │
  ├─────────────────────────────────────────────────────────────┤
  │  technical_market_sensor_worker  (each scan cycle)          │
  │    → technical signal scores                                │
  └─────────────────────────────────────────────────────────────┘
            │  (all upstream validated)
            ▼
  universe_builder_worker
    → data/universe_builder/active_opportunity_universe_shadow.json
            │
            ▼
  handoff_publisher (validates schema + freshness + invariants)
    → data/live/current_manifest.json
            │
            ▼
  LIVE TRADING BOT
    reads current_manifest.json
    never calls score_universe() when handoff is enabled
    never searches for alternate universe files
    fail-closed if manifest missing, stale, or schema-invalid
```

**Key design invariant (from `intelligence_first_runtime_orchestration.md`):**
> When `enable_active_opportunity_universe_handoff = True`, the live bot does not call `score_universe()`, does not consult `favourites.json` for discovery, does not run catalyst screening, and does not call Apex for symbol discovery.

**Status of intended vs actual:** The file-based contract is partially in place (handoff_publisher, handoff_reader, manifest path). The independent workers do not exist as separate processes — their logic lives in modules callable from within bot.py.

---

## D. Built Components

### Universe layer modules

| Module | File | Output | Durable? | Standalone? |
|--------|------|--------|---------|------------|
| Committed universe | `universe_committed.py` | `data/committed_universe.json` | YES | YES (API-safe weekends) |
| Daily promoter | `universe_promoter.py` | `data/daily_promoted.json` | YES | YES (Alpaca API required) |
| Universe builder | `universe_builder.py` | `data/universe_builder/active_opportunity_universe_shadow.json` | YES | PARTIAL (called inline) |
| Position research (Tier D) | `universe_position.py` | `data/position_research_universe.json` | YES | YES |
| Quota allocator | `quota_allocator.py` | none (pure function) | N/A | N/A |
| Route tagger | `route_tagger.py` | none (pure function) | N/A | N/A |
| Handoff publisher | `handoff_publisher.py` | manifest | YES | PARTIAL |
| Handoff observer | `handoff_publisher_observer.py` | none (read-only) | N/A | YES |
| Handoff reader | `handoff_reader.py` | none (reads manifest) | N/A | N/A |
| Handoff adapter | `handoff_candidate_adapter.py` | none (transforms) | N/A | N/A |

### Intelligence layer modules

| Module | File | Output | Durable? | Standalone? |
|--------|------|--------|---------|------------|
| Intelligence engine | `intelligence_engine.py` | `data/intelligence/economic_candidate_feed.json` | SEMI | YES (no live API, no LLM) |
| Theme activation | `theme_activation_engine.py` | `data/intelligence/theme_activation.json` | SEMI | YES (local files only) |
| Catalyst engine | `catalyst_engine.py` | internal store | SEMI | YES (can run standalone) |
| Factor registry | `factor_registry.py` | `data/reference/factor_registry.json` | YES | YES |

### Reference data modules (built, partial outputs)

| Module | File | Output | Status |
|--------|------|--------|--------|
| Reference data builder | `reference_data_builder.py` | `data/reference/sector_schema.json`, `symbol_master.json`, `theme_overlay_map.json` | Built; not scheduled as standalone |
| Provider fetch tester | `provider_fetch_tester.py` | `data/reference/provider_fetch_test_results.json` | Manual/diagnostic |

### Durable file outputs (ML-critical, must never be lost)

| File | Writer | Format | Protection |
|------|--------|--------|-----------|
| `data/trade_events.jsonl` | `event_log.py` | WAL append-only | fsync after every write |
| `data/training_records.jsonl` | `training_store.py` | append-only | fsync |
| `data/tier_d_funnel.jsonl` | `signal_pipeline.py` | append-only | append |
| `data/signals_log.jsonl` | `signal_pipeline.py` | append-only | append |
| `data/execution_ic.jsonl` | `bot_trading.py` | append-only | append |
| `data/committed_universe.json` | `universe_committed.py` | full rewrite | atomic (tempfile+replace) |
| `data/daily_promoted.json` | `universe_promoter.py` | full rewrite | atomic (tempfile+replace) |
| `data/position_research_universe.json` | `universe_position.py` | full rewrite | atomic |
| `data/ic_weights.json` | `ic/storage.py` | full rewrite | atomic + file lock |

### Diagnostic outputs only (safe to lose, not ML-critical)

| File | Purpose |
|------|---------|
| `data/apex_shadow_log.jsonl` | Shadow execution comparison |
| `data/apex_prompt_snapshot.jsonl` | Apex prompt debugging |
| `data/apex_conversation_log.jsonl` | Full Apex conversation traces |
| `data/apex_divergence_log.jsonl` | Apex vs PM disagreements |
| `data/audit_log.jsonl` | General system event log |
| `data/live_ic_report.json` | Real-time IC per dimension |
| `data/factor_analysis_report.json` | Factor correlation (script output) |
| `data/universe_builder/universe_builder_report.json` | Builder run diagnostics |
| `data/intelligence/*.json` (agent decision logs) | Per-agent decision traces |

---

## E. Disabled Components

| Component | Flag | Current Value | What It Would Do |
|-----------|------|---------------|-----------------|
| Intelligence shadow output | `intelligence_first_shadow_enabled` | **False** | Enable all shadow file writes from intelligence layer |
| Macro transmission matrix | `intelligence_first_transmission_enabled` | **False** | Run transmission rules → active themes |
| Economic candidate feed | `intelligence_first_candidate_feed_enabled` | **False** | Feed economic candidates into universe_builder |
| Shadow universe builder | `intelligence_first_universe_builder_enabled` | **False** | Run universe_builder in shadow mode each cycle |
| Advisory logging | `intelligence_first_advisory_enabled` | **False** | Log advisory output alongside live decisions |
| Short selling | `ALLOW_SHORT` | **False** | Allow bearish entries (disabled after -$105K on 199 trades) |
| Intraday trading | `ALLOW_INTRADAY` | **False** | Allow same-day entry/exit (disabled after -$95.8K on 253 trades) |
| ML regime classifier | `PRODUCTION_LOCKED = True` in `ml_engine.py` | Locked | Random forest regime classification from training data |
| HMM regime detection | `hmm_regime.enabled = True` but `PRODUCTION_LOCKED = True` | **Gate not met** | 2-state Gaussian HMM on SPY daily returns (gated: needs full IC Phase 2 review) |
| Presession dry-run mode | `presession_dry_run = True` | **True** | Presession runs but generates no orders (Phase 3a observation) |
| ML engine activation | `ml_enabled = True` but `PRODUCTION_LOCKED` in engine | **Gated** | Scikit-learn pattern recognition (50+ trades met; PRODUCTION_LOCKED still True) |

**Sprint 7J.4 note:** `enable_active_opportunity_universe_handoff = True` was flipped. This is NOT in the disabled list — it is the most recently activated flag. Runtime log confirmation is pending.

---

## F. Manual-Only Scripts

Scripts in `scripts/` that require human invocation and cannot be scheduled safely:

| Script | Why Manual | What It Does |
|--------|-----------|-------------|
| `tier_d_evidence_report.py` (62KB) | Requires Amit review of output before acting | 11-stage Tier D funnel analysis; decides Phase 2 gate |
| `tier_d_test_scan.py` (21KB) | Requires live external API (FMP + Alpaca) | Tests universe builder + Tier D scoring pipeline end-to-end |
| `apex_shadow_report.py` | Diagnostic — human interprets results | Compares actual Apex vs shadow run decisions |
| `apex_cap_replay.py` | Diagnostic | Offline replay of Apex top-30 cap against most recent scan |
| `apex_flip_proposer.py` | Analytical | Generates synthetic position reversals and scores them |
| `phase1_session_report.py` | Periodic analytics | Session P&L, timing, regime breakdown for a specific day |
| `factor_analysis.py` | Periodic research | Factor correlation and IC analysis (writes cache) |
| `pru_ab_comparison.py` | Diagnostic | Compares two versions of position research universe |
| `backfill_may5_trades.py` | One-off migration | Writes to trades.json — destructive, human validation required |
| `backfill_position_closed.py` | One-off — requires live IBKR | Marks positions closed in event log |
| `cancel_orphan_orders.py` | Requires live IBKR | Cleans stale orders — broker interaction |
| `validate_intelligence_files.py` | Diagnostic | Schema validation of intelligence engine output files |
| `rebuild_positions_from_intents.py` | Diagnostic | Read-only position reconstruction from event log |
| `reconcile_trades_json.py` | Diagnostic | Read-only consistency check on trades.json |

**LaunchAgents (system daemons — always running):**

| Daemon | Interval | What It Does |
|--------|---------|-------------|
| `com.decifer.auto-push` | Every 120s | Auto-commit + push git changes (dev only) |
| `com.decifer.icloud-sync` | Every 300s | Sync `data/` + `.env` to iCloud Drive backup |

Neither LaunchAgent is related to universe building or trading logic.

---

## G. Weekend / After-Hours Readiness

### What is genuinely weekend-safe

| Module | Evidence |
|--------|---------|
| `universe_committed.py` | Uses `prior_close × prev_volume` from last regular session. Alpaca returns this even on Sunday. Safe to run anytime. |
| `universe_promoter.py` | Reads committed_universe.json, calls Alpaca snapshot API. Alpaca returns last-available bars on weekends. Safe to run pre/post-market. |
| `universe_position.py` | FMP fundamental data — not time-sensitive. Weekend-safe. |
| `intelligence_engine.py` | Reads local JSON only (no live API, no LLM). Can run anytime. |
| `theme_activation_engine.py` | Reads local files only. Can run anytime. |
| `reference_data_builder.py` | Static reference build. Weekend-safe. |

### The critical problem

**All three universe refresh jobs are triggered by `schedule` inside `bot.py`.** The bot must be running. If the bot is down Saturday through Sunday 22:59, the committed universe refresh at Sunday 23:00 does not fire.

```python
# bot.py lines 603–625 — all jobs die with the process
schedule.every().sunday.at("23:00").do(refresh_committed_universe)
schedule.every().day.at("16:15").do(run_promoter)
schedule.every().day.at("08:00").do(run_promoter)
```

**There are no standalone launchd plists for universe refresh.** The two existing LaunchAgents (`auto-push`, `icloud-sync`) are infrastructure utilities, not trading logic.

### After-hours session detection (exists, works)

`alpaca_data._volume_session()` correctly classifies:
- `CLOSED` — before 04:00 ET or after 20:00 ET
- `PRE_MARKET` — 04:00–09:30 ET
- `REGULAR` — 09:30–16:00 ET
- `AFTER_HOURS` — 16:00–20:00 ET

Entry gate tests confirm `AFTER_HOURS` session is recognized and volume gates adjust accordingly. **Session detection is solid.** The gap is that universe refresh is bot-process-dependent, not daemon-based.

---

## H. Parallelisation Readiness

**Assessment: High architectural readiness, zero current deployment.**

### Why parallelisation is structurally easy

1. **File-based handoff contract is already in place.** `universe_builder` writes `active_opportunity_universe_shadow.json`. `handoff_publisher` validates and publishes a manifest. `handoff_reader` reads it. The interface between workers and bot is a file — no shared memory, no sockets.

2. **Universe layer modules are already standalone-capable.** `universe_committed.py`, `universe_promoter.py`, `universe_position.py`, `intelligence_engine.py`, `theme_activation_engine.py` all have single-function entry points (`refresh_committed_universe()`, `run_promoter()`, etc.) that can be called from a separate process.

3. **quota_allocator and route_tagger are pure functions.** No side effects, no I/O, no shared state. Trivially parallelisable.

4. **`factor_registry.py` has an explicit layer model.** `L_REFERENCE`, `L_MARKET`, `L_UNIVERSE`, `L_TRADING_BOT`, etc. are formally defined with owning layers, consuming layers, and provider fallback chains. This is the service mesh contract — it just has no runtime enforcement yet.

5. **All critical writes use atomic operations.** `tempfile + os.replace` for JSON files, `fsync` for JSONL. Workers can crash-restart without corrupting consumer files.

6. **`handoff_publisher_observer.py` proves the pattern works.** Observer reads manifest state without touching production — demonstrating that read-only file consumption is correct by design.

### What is not parallel today

- All workers run as functions inside one process
- No process boundary between universe build and signal scoring
- `schedule.run_pending()` blocks: a slow universe refresh delays the scan cycle
- `ThreadPoolExecutor(max_workers=1)` in `signal_pipeline.py` news fetch is effectively serial

---

## I. Sequential Bottlenecks

Ordered by impact:

| Bottleneck | Location | Impact |
|-----------|----------|--------|
| **Bot process is prerequisite for all scheduled jobs** | `bot.py` main loop + `schedule` library | Universe refresh, promoter, presession all silently skip if bot is down |
| **Universe builder runs inline in scan cycle** | `universe_builder.build()` called from bot's scan cycle | Slow builds (Alpaca batch snapshot) delay trading decisions |
| **Signal pipeline is synchronous** | `signal_pipeline.run()` inside scan cycle | All 75 candidates scored sequentially (ThreadPoolExecutor max_workers=1 for news) |
| **Intelligence layer is entirely disabled** | `intelligence_first_*` flags all False | Economic candidate feed is not refreshing; universe_builder's source 2 is always empty |
| **Presession is dry-run only** | `presession_dry_run = True` | Catalyst pipeline runs at 08:00 but produces no orders — effectively a diagnostic pass only |
| **Apex calls are sequential within cycle** | Track A → Track B → Shadow | Three sequential LLM calls per cycle; Shadow could be deferred to async |
| **No manifest staleness check before consumption** | `handoff_reader.py` | If universe_builder fails silently, bot consumes stale manifest without error |

---

## J. Missing Workers

Workers described in `intelligence_first_runtime_orchestration.md` that do not exist as runnable processes:

| Worker | Described In | Current State | What Would Change |
|--------|-------------|--------------|------------------|
| `reference_data_worker` | `intelligence_first_cloud_process_map.md` | Module exists (`reference_data_builder.py`), no launch point, no heartbeat | Weekly reference refresh (sector, symbol master, theme overlay, factor registry) runs independently |
| `economic_intelligence_worker` | Cloud process map | Module exists (`intelligence_engine.py`), flag disabled (`intelligence_first_candidate_feed_enabled=False`) | Daily economic candidate feed; universe_builder source 2 would be live |
| `provider_ingestion_worker` | Cloud process map | No standalone module; inlined in signal pipeline | Pre-fetches Alpaca/FMP data before scan cycle; separates ingestion latency from scoring |
| `catalyst_event_worker` | Cloud process map | Module exists (`catalyst_engine.py`), runs as threads inside bot | Runs as independent process; writes file-based catalyst snapshot |
| `company_quality_worker` | Cloud process map | No standalone module | Weekly fundamental quality scores (FMP) |
| `technical_market_sensor_worker` | Cloud process map | No standalone module | Scan-cycle technical scoring (depends on provider_ingestion) |
| `universe_builder_worker` | Cloud process map | Module exists (`universe_builder.py`), called inline | Runs after upstream workers; writes manifest independently |

**Missing infrastructure (no worker can be deployed without this):**
- No heartbeat check pattern implemented (described in docs: `data/heartbeats/<worker>.json`)
- No structured fail-closed validation in `handoff_reader.py` (missing staleness check)
- No launchd plists or systemd units for any universe or intelligence job

---

## K. Cloud Runtime Implications

### What is already cloud-portable

- File-based handoff contract → works on any shared volume (EFS, EBS, GCS Fuse, SMB)
- Atomic writes (tempfile+replace) → safe under NFS/EFS with proper mount options
- Append-only JSONL with fsync → crash-safe on cloud block storage
- Factor registry layer model (`L_REFERENCE`, `L_MARKET`, etc.) → ready to map to container definitions

### What blocks cloud deployment today

| Gap | Description |
|-----|-------------|
| Single-process model | All logic in `bot.py` — no container boundary between workers and bot |
| No containerisation | No Dockerfile, no docker-compose.yml for any worker |
| No worker entry points | `reference_data_builder.py`, `universe_builder.py` etc. lack `if __name__ == "__main__"` runner blocks with structured CLI |
| No healthcheck files | `data/heartbeats/<worker>.json` described in docs but not written by any module |
| iCloud sync is not cloud storage | `icloud-sync.sh` syncs to `~/Library/Mobile Documents/` — macOS-only, not a cloud volume |
| No message queue | Workers communicate only via files; no event-driven trigger (SNS, SQS, Redis Pub/Sub) — file polling is the only pattern |
| No network isolation | bot.py makes direct API calls to Alpaca, FMP, IBKR — would need credential injection per container |
| No structured CLI for workers | `universe_committed.py` has `refresh_committed_universe()` but no `main()` block with arg parsing |

### What must never run inside the live bot process (cloud or local)

| Module | Reason |
|--------|--------|
| `ml_engine.py` training | CPU-intensive training job; `PRODUCTION_LOCKED=True` is correct |
| HMM training (`hmm_regime.py`) | Requires 504+ days of SPY daily returns; compute-intensive |
| `alpha_validation.py` (Alphalens) | Long-running factor analysis; blocks bot |
| `factor_analysis.py` | Heavy computation + writes cache files |
| Any backtest run | Unpredictable runtime; must never share process with live execution |
| IC recalculation batch | Rolling 200-day window recalculation; can be expensive on large JSONL |

---

## L. Recommended Worker Split

Based on what is built and what the docs specify, this is the correct split:

### Tier 1 — Weekend-safe standalone daemons (launchd or cron, lowest risk)

| Worker | Frequency | Entry Point | Output |
|--------|-----------|------------|--------|
| `universe_committed` | Sunday 23:00 ET | `universe_committed.refresh_committed_universe()` | `data/committed_universe.json` |
| `reference_data` | Sunday 02:00 ET | `reference_data_builder.build()` | `data/reference/*.json` |
| `intelligence_engine` | Nightly (configurable) | `intelligence_engine.run()` | `data/intelligence/economic_candidate_feed.json` |

These have **no broker dependency**, **no live market data required**, and can run entirely offline on weekend.

### Tier 2 — Pre/post-market daemons (require Alpaca API)

| Worker | Frequency | Entry Point | Output |
|--------|-----------|------------|--------|
| `universe_promoter` | 08:00 + 16:15 ET | `universe_promoter.run_promoter()` | `data/daily_promoted.json` |
| `catalyst_event` | Continuous (60s/600s) | `catalyst_engine.start()` | internal store + file snapshot |
| `theme_activation` | Daily pre-market | `theme_activation_engine.run()` | `data/intelligence/theme_activation.json` |

### Tier 3 — Scan-cycle workers (require live market data, run per scan)

| Worker | Frequency | Dependency |
|--------|-----------|-----------|
| `provider_ingestion` | Each scan cycle | Alpaca + FMP APIs |
| `technical_market_sensor` | Each scan cycle | Depends on provider_ingestion |
| `universe_builder` | Each scan cycle | Depends on catalyst + economic + technical |
| `handoff_publisher` | Each scan cycle | Depends on universe_builder |

### Bot process — consume only

When handoff is fully enabled, `bot.py` should:
- Read `data/live/current_manifest.json` at cycle start
- Fail closed (not degrade silently) if manifest is missing or stale
- Never call `score_universe()` directly
- Never call universe_committed or universe_promoter inline

---

## M. Gaps That Block Parallel Runtime

In priority order:

| # | Gap | Severity | Blocks |
|---|-----|----------|--------|
| 1 | **Universe refresh jobs are bot-process-dependent** — `schedule` library inside `bot.py`; no standalone daemon | HIGH | Weekend refresh, process isolation |
| 2 | **No staleness / fail-closed check in `handoff_reader.py`** — bot consumes manifest without verifying freshness | HIGH | Correct fail-closed behaviour per runtime contract |
| 3 | **Intelligence-first flags all disabled** — `economic_candidate_feed` is not refreshing; universe_builder source 2 always empty | MEDIUM | Live economic candidate input to universe |
| 4 | **No worker entry points (`__main__` blocks)** — modules can't be launched as standalone processes without bot.py importing them | MEDIUM | Any worker extraction |
| 5 | **No heartbeat files written** — `data/heartbeats/<worker>.json` described in docs but no module writes them | MEDIUM | Worker health monitoring |
| 6 | **No Docker / containerisation** — no Dockerfile, no docker-compose.yml | MEDIUM | Cloud deployment |
| 7 | **Presession is dry-run only** (`presession_dry_run = True`) — 08:00 catalyst pipeline generates no decisions | LOW | Presession execution (Phase 3b) |
| 8 | **Sprint 7J.4 runtime confirmation pending** — `enable_active_opportunity_universe_handoff = True` but no log evidence yet that the manifest is being consumed by the live bot | LOW | Confidence that handoff is live |
| 9 | **Shadow LLM call (Track C) is synchronous** — could be deferred async without affecting live decisions | LOW | Scan cycle latency |
| 10 | **No per-worker credential injection pattern** — all workers would need API keys if deployed as separate containers | LOW | Cloud deployment |

---

## N. Next 5 Practical Actions

Actions are ordered by impact and reversibility. None require touching bot_trading.py, scanner.py, risk, orders, execution, or broker logic.

### Action 1 — Extract universe_committed and universe_promoter as launchd daemons

**What:** Create two new launchd plist files in `scripts/`:
- `com.decifer.universe-committed.plist` — runs `python3 universe_committed.py` Sunday 23:00 ET
- `com.decifer.universe-promoter.plist` — runs `python3 universe_promoter.py` at 08:00 and 16:15 ET

Add `if __name__ == "__main__"` entry blocks to both modules (trivial one-liners calling the existing function).

Remove the corresponding `schedule.every()...` lines from `bot.py` (or keep them as fallback — author's choice).

**Why now:** Lowest-risk change. Both modules already write atomically. Weekend refresh is the most immediate operational gap. Zero production code change.

### Action 2 — Add manifest staleness guard in `handoff_reader.py`

**What:** Before consuming `current_manifest.json`, check:
1. File exists
2. `generated_at` timestamp < configurable threshold (e.g. 6 hours for scan-cycle, 30 hours for daily)
3. Schema invariants pass (`no_executable_trade_instructions = true`, `live_output_changed = false`)

If any check fails: log structured error, return empty candidate list (fail closed), **do not silently degrade to scanner-led discovery**.

**Why now:** The runtime contract (`intelligence_first_runtime_orchestration.md`) specifies this behaviour explicitly. Sprint 7J.4 enabled handoff but the fail-closed guard is not confirmed to be in place. This is the highest-priority correctness gap.

### Action 3 — Enable intelligence candidate feed in shadow mode

**What:** Set `intelligence_first_candidate_feed_enabled = True` and `intelligence_first_shadow_enabled = True` in config.py.

This enables `intelligence_engine.py` to write `data/intelligence/economic_candidate_feed.json` and universe_builder to use source 2 (economic candidates). No live execution impact — shadow output only.

**Why now:** The intelligence layer is built and tested. Five consecutive flags being False means the entire pre-Sprint-7J architecture is dark. Enabling in shadow first gives Amit visibility into what the economic candidate feed actually produces without touching live decisions.

### Action 4 — Confirm Sprint 7J.4 handoff is live (log evidence)

**What:** After the next live scan cycle, run:
```
grep -c "handoff" data/audit_log.jsonl | tail -5
grep "current_manifest" data/audit_log.jsonl | tail -20
```
Confirm that `handoff_reader` is being called and that `current_manifest.json` is being read (not just written).

**Why now:** Sprint 7J.4 is the most recently shipped change. Runtime confirmation is the logical next step before any further architecture work. This is observation only, not a code change.

### Action 5 — Write `__main__` entry points for the three Tier-1 worker modules

**What:** Add a minimal `if __name__ == "__main__":` block to:
- `universe_committed.py` — calls `refresh_committed_universe()`
- `universe_promoter.py` — calls `run_promoter()`
- `intelligence_engine.py` — calls `run()` or equivalent

Each block should: set up logging, call the entry function, print structured JSON result to stdout, exit 0 on success / 1 on failure.

**Why now:** This is a prerequisite for Action 1 (launchd) and Action 6 (Docker). It unlocks all worker extraction with zero production impact. Each module already has the logic — this is just the shell-callable wrapper.

---

## Summary Answers to 15 Questions

**1. What is live today?**
Three-tier universe (committed/promoter/Tier D), catalyst engine (news+EDGAR), IC-weighted 14-dimension signal pipeline, Apex orchestrator (3 calls/cycle), handoff publisher (Sprint 7J.4 flag live, runtime confirmation pending).

**2. What is built but disabled?**
Five `intelligence_first_*` flags (all False), ALLOW_SHORT, ALLOW_INTRADAY, ML PRODUCTION_LOCKED, HMM PRODUCTION_LOCKED, presession_dry_run=True.

**3. What currently runs manually?**
Tier D evidence report, factor analysis, apex shadow report, apex cap replay, all backfill/migration scripts. See Section F.

**4. What can run on weekend / after-hours?**
`universe_committed.py`, `universe_promoter.py`, `universe_position.py`, `intelligence_engine.py`, `theme_activation_engine.py`, `reference_data_builder.py`. All are weekend-safe by design. **None have standalone launchd daemons — they only run if the bot is running.**

**5. What is truly parallel or independently runnable?**
Every module in Section L (Tier 1 and Tier 2 workers) can run independently today if invoked directly. The file contract is in place. No true parallel deployment exists yet.

**6. What is still sequential script chaining?**
The entire scan cycle inside `bot.py`: universe_builder → signal_pipeline → Apex Track A → Apex Track B → Apex Shadow. All serialized in the main thread.

**7. What files produce the governed universe?**
`universe_committed.py`, `universe_promoter.py`, `universe_builder.py`, `universe_position.py`, `catalyst_engine.py`, `intelligence_engine.py`. See Section B.

**8. What files consume the governed universe?**
`handoff_publisher.py` (validates + publishes), `handoff_reader.py` (bot reads manifest), `signal_pipeline.py` (scores candidates), `apex_orchestrator.py` (makes trade decisions).

**9. What is the real current data flow?**
See Section B. Summary: committed universe built weekly (inside bot) → promoted daily (inside bot) → universe_builder assembles 6 sources per cycle (inside bot) → signal_pipeline scores → Apex decides → orders execute.

**10. What is missing for true multi-process cloud operation?**
Worker entry points, heartbeat files, staleness checks in handoff_reader, launchd/systemd units, Dockerfiles, no message queue. See Section M.

**11. Which layer outputs are durable files?**
See Section D, durable table. Nine critical JSONL/JSON files with atomic writes or fsync.

**12. Which layer outputs are only reports or diagnostics?**
See Section D, diagnostic table. Apex shadow/prompt/conversation logs, audit_log, live_ic_report, intelligence agent logs.

**13. Which processes should become scheduled workers?**
See Section L. Tier 1 (weekend-safe, lowest risk): universe_committed, reference_data, intelligence_engine. Tier 2 (pre/post-market): universe_promoter, catalyst_event, theme_activation.

**14. Which modules must never run inside the live bot process?**
See Section K: ml_engine training, HMM training, Alphalens, factor_analysis, any backtest, IC batch recalculation.

**15. What is the shortest practical path from current repo to parallel runtime?**
Action 1 (launchd for committed+promoter) + Action 2 (staleness guard in handoff_reader) + Action 3 (enable intelligence shadow flags). Three changes, zero production risk, full decoupling of universe refresh from bot uptime.

---

*This report is factual and read-only. No production files were modified. No configuration flags were changed. No tests were added.*
