# Decifer Trading — Session Context Brief
# Auto-loaded at every session start. Keep this current.

---

## ⛔ TERMINOLOGY — READ THIS FIRST, EVERY SESSION

**"dashboard" ALWAYS means `bot_dashboard.py`. FULL STOP.**

There are two dashboards in this system:
- **Bot dashboard** — `bot_dashboard.py` — the operational dashboard. THIS is what "dashboard" means.
- **Chief Decifer dashboard** — port 8181, read-only monitoring UI — NEVER referred to as just "dashboard".

**If Amit says "the dashboard", "dashboard bug", "fix the dashboard", "dashboard panel" — he means `bot_dashboard.py`. Do NOT touch Chief Decifer. Do NOT ask for clarification. The answer is always `bot_dashboard.py` unless Amit explicitly says "Chief" or "Chief Decifer dashboard".**

Violating this wastes time and edits the wrong system. There are no exceptions.

---

## North Star

Decifer is an autonomous paper-trading system that uses a 10-dimension signal engine and the **Apex Single-Synthesizer** (one `claude-sonnet-4-6` call) to scan, score, and execute trades on IBKR (paper account DUP481326). The goal: generate high-quality training data across market regimes to eventually validate a live system.

**We are not building a live trading system yet. Every paper trade is a data point.**

**The only objective of this project is building alpha. Every feature, fix, and decision must serve that objective. If it does not directly contribute to generating, measuring, or preserving alpha, it should not be built.**

**No assumptions allowed. If something is unclear — about data, behavior, intent, or architecture — stop and ask Amit. Never fill gaps with guesses. Verify before building.**

Three actors:
| Actor | Role |
|-------|------|
| **Amit** | Decision maker, domain expert, final approver |
| **Cowork (Claude)** | Writes code, runs research, builds features |
| **Chief Decifer** | Read-only dashboard (port 8181). Never writes code. |

---

## Current State (update this when phases change)

- **Phase A — Complete ✅** (shipped 2026-03-28): Direction-agnostic signals, short-candidate scanner, directional skew tracking, mean-reversion dimension (10th signal)
- **IC scoring — Phase 2 Complete ✅** (2026-04-28): All three IC validation gates pass. Sample gate (60 valid records ≥ 50), IC gate (mean positive IC = 0.1728, 5 dims positive), Sharpe gate (walk-forward Sharpe 6.69 ≥ 0.8). Result persisted to `data/ic_validation_result.json`. Phase C gate (200 closed trades) also met — 358 trades. HMM and walk-forward weight calibration are now **unlocked**.
- **Three-tier universe — Active ✅**: TV Screener ripped out. Universe is now: committed universe (top-1000 by dollar volume, weekly refresh) + dynamic adds (catalyst hits, held positions, favourites, sympathy plays, news-driven).
- **Catalyst screener — Active ✅**: `catalyst_engine.py` scores EDGAR filings, earnings surprises, and analyst actions in real-time. High-conviction catalyst hits get a flat score boost to clear `min_score_to_trade`.
- **Full architecture audit — Complete ✅** (2026-04-22): 27-issue audit, 24 fixes shipped.
- **Decifer 3.0 "Apex" — Live ✅** (cutover 2026-04-24): The 4-agent pipeline is replaced by the **Apex Single-Synthesizer** — one `apex_call()` via `claude-sonnet-4-6`. Three Sonnet calls per cycle: Track A (new entries), Track B PM (TRIM/EXIT/HOLD), Shadow (divergence log). Legacy code (`agents.py`, `run_portfolio_review()`, buy loop, and the 3-agent pipeline inside `sentinel_agents.py`) was deleted. `sentinel_agents.py` itself still exists — it was gutted to contain only `build_news_trigger_payload()`, a pure function that shapes trigger data for `apex_call()`. No rollback path — forward only.
- **Post-migration cleanup — Complete ✅** (2026-04-27): `agents.py` deleted, legacy buy loop deleted, 3 migration flags collapsed, 5 Phase 8A test files renamed to permanent regression names. Test suite: **2623 passing** (updated 2026-05-19). Tag: `decifer-3.0-post-migration-cleanup`.
- **JSONL persistence migration — Complete ✅** (2026-04-28): `trade_log.py` (SQLite WAL) and `trade_store.py` deleted. Replaced with `event_log.py` (ORDER_INTENT → ORDER_FILLED → POSITION_CLOSED write-ahead log) and `training_store.py` (ML training records). Eliminates UNKNOWN trade_type bug caused by SQLite WAL corruption. 349 closed trades migrated to `data/training_records.jsonl`. Phase C gate now reads from `training_store.count()`.
- **Phase B — HMM Advisory Active ✅** (2026-05-20): Gate met (406 eligible trades ≥ 200). `hmm_regime.enabled=True`. `get_hmm_regime_spy()` now checks `training_store.count_eligible() >= gate_min_eligible_trades(200)` before fitting — degraded records (ml_eligible=False) excluded from gate count. HMM is 3rd vote in `_resolve_regime_router(vix, hurst, hmm)` for signal-weight routing. `config["regime_detector"]` stays `"vix_proxy"` — scanner top-level regime NOT replaced. Scanner-level HMM replacement closed as not recommended. See `docs/DECISIONS.md` 2026-05-20 entry.
- **Signal validation report — Complete ✅** (2026-05-20): `scripts/signal_validation_report.py`. 177 usable records (406 eligible, 229 excluded for missing pnl_pct or signal scores). 16 dims tested. No dimension has statistically significant positive execution IC. squeeze MARGINAL (+0.100, p=0.185). overnight_drift NEGATIVE significant (−0.199, p=0.009). Sign flips vs candidate IC are expected — selection bias on 177 trades entered vs 36k+ scanned. Report: `data/signal_validation_report.json`. Execution IC treated as advisory only throughout. 29 tests.
- **Walk-forward weight calibration — Proposal complete ✅** (2026-05-20): `scripts/walkforward_calibration_report.py`. Candidate IC (ic_weights.json, 36k+ scanned candidates, no selection bias) is PRIMARY. Execution IC is ADVISORY — may cap or flag, must not increase any weight. overnight_drift BLOCKED CRITICAL (negative in both sources, p=0.009 in execution). Proposed weight delta: NONE — no execution IC result strong enough to require adjustment. Proposal output: `data/proposed_calibrated_weights.json`. Does NOT touch `ic_weights.json`. Activation requires explicit Amit approval. 35 tests.
- **ML Controlled Learning — Active, pre-gate ✅** (2026-05-21): Sprint 3.7 shipped. 4,118 observations in `ml_observations.jsonl`. `schema_version=sprint37_v1`. `candidate_source` accurate for post-Sprint-3.7 records. Old 50-trade ML activation gate **RETIRED** — `ml_engine.py` was deleted. New training-readiness gate: `canonical_learning_dataset.jsonl` must contain ≥200 `ml_eligible=true` exact closed-trade records with regime diversity (≥2 regimes, no regime >75%) before any model training or activation. Current status: 0 exact-joined closed records. Gate not met.
- **Position Research Universe Phase 1 — Shadow observation only ✅** (2026-05-03): Tier D discovery path. Full pipeline instrumentation in place: `tier_d_funnel.jsonl` records stage=pipeline (stages 1-6 attrition), stage=dispatch (Apex classification breakdown), and stage=apex_cap (top-30 hard cap before Apex — whether Tier D is being killed before Apex sees them). Evidence script: `scripts/tier_d_evidence_report.py`. **Phase 2 gate: NOT MET** — awaiting real scan-cycle evidence. Must run report and review Section 0b (Apex Cap Analysis) with Amit before ANY of: gate softening, stratified cap, live entries, or Phase 2 work. The next fix decision (A=tier-aware shortlist / B=scoring threshold / C=Apex prompt / D=gate softening) depends entirely on what Section 0b shows. 30 tests, all passing (2029/2031 suite).
- **Health Tab — Active ✅** (2026-05-19): 7-stage pipeline funnel in `bot_dashboard.py` Health tab (`bot_health.py`). Monitors: Market Map (Alpaca stream), Economic Intelligence (candidate feed age), Theme Activation (theme_activation.json age), Universe Builder (handoff age + manifest validity), Live Bot (last_scan timestamp), Trade Execution (IBKR connection), Signal Engine (worker heartbeats). `last_scan` parses HH:MM:SS format correctly.
- **Intelligence Layer v4.0 — Live ✅** (2026-05-19): Root-cause fix for circular inference bug. `live_driver_resolver.py` (NEW) fetches 9 real market symbols (SPY/IEF/HYG/LQD/USO/ITA/SMH/NVDA/UVXY) and applies 7 deterministic rules to produce live driver state. `candidate_resolver.py` now reads live driver state instead of hard-coded drivers. 13 dead modules deleted (~13k lines). `enable_active_opportunity_universe_handoff = True`. All 11 architecture layers now wired end-to-end. Tag: `feat(intelligence)`. Test suite: 2623 passing, 7 pre-existing failures.
- **Intelligence Layer v5.0 — Full Market Coverage ✅** (2026-05-22, v4.16.0): Expanded from narrow AI/rates/oil/defence prototype to full market-map engine. **Bugs fixed:** fallback changed from `[ai_capex_growth, ai_compute_demand]` to `[]` (fail closed — no AI defaults on data failure); `sensor is None or sensor > threshold` pattern fixed (AI no longer activates on missing sensor data); `generate_feed()` fallback also fixed; `smh_tactical_weakness` blocked condition added (SMH −4% to −8% blocks data_centre_power and memory_storage but NOT semiconductors — closest to SMH signal); STX resolved (moved from review_required to approved in memory_storage); ai_compute_infrastructure expanded with SMCI + DELL. **New drivers (5):** `yields_falling`, `risk_on_rotation`, `gold_safe_haven_bid`, `credit_stress_easing`, `small_cap_risk_on`. **New sensors (2):** GLD, IWM. **Themes:** 10 → 23. **Rules:** 10 → 31. **Rosters:** 10 → 23. New sector coverage: software/cloud, cybersecurity, mega-cap platforms, consumer discretionary, travel/leisure, defensive healthcare, biotech, regional banks, infrastructure/reshoring, gold/precious metals, copper/electrification, REITs, small-cap risk-on. **Headwind logic:** `yields_rising → reits_falling_yield` headwind (pressure_candidate), `oil_supply_shock → travel_leisure` headwind, risk-off → defensive_healthcare + gold tailwinds. **Deduplication:** `_deduplicate_candidates()` added — one symbol per feed, highest confidence wins, multi-theme reasons merged. `max_candidates` per theme enforced. Files modified: `live_driver_resolver.py`, `candidate_resolver.py`, `data/intelligence/transmission_rules.json`, `data/intelligence/theme_taxonomy.json`, `data/intelligence/thematic_roster.json`. Test suite: **3031 passing**, 11 pre-existing failures. **Runtime proof (2026-05-22):** Pipeline executed end-to-end — 11/11 sensors, `live_market_data` mode; 23 themes evaluated (3 activated, 5 crowded, 1 headwind, 14 dormant); `economic_candidate_feed.json` generated with 39 candidates (`mode=intelligence_advisory_feed`, non-executable); `data/live/active_opportunity_universe.json` generated in `production_handoff_universe` mode with 75 candidates; `current_manifest.json` `handoff_enabled=true`; fallback fail-closed confirmed; zero duplicate symbols; headwind candidates are `pressure_candidate`/`route=watchlist` only. Production handoff path confirmed: `data/live/current_manifest.json` → `handoff_reader.load_production_handoff()` → `bot_trading.py` (line 150). The shadow file (`active_opportunity_universe_shadow.json`) is internal pipeline-only — the live bot never reads it. 11 pytest failures are pre-existing and unrelated to this work.
- **Dashboard stale message cleanup — Complete ✅** (2026-05-19): All legacy agent/Opus/pipeline labels purged from `static/dashboard.html`. Tab renamed `🧠 Apex`, "Opus Market View" → "Apex Synthesis View", "Agent Live Conversation" → "Apex Live Conversation", "Scoring & Agents" → "Scoring & Apex". Removed "Agents required to agree" settings row (legacy field, not used by Apex). Fixed sidebar "Min score:" display to read `min_score_to_trade` instead of the undefined `agents_required`. API key renamed `agent_conversation_history` → `apex_conversation_history` in `bot_dashboard.py`.
- **Metadata Preservation — Active ✅** (2026-05-19): Durable metadata identity across restart. `training_store.classify_record_quality()` is the single authority: marks UNKNOWN trade_type / MISSING metadata_status / unknown_trade_type exit reason / _EXT_ trade_ids as `ml_eligible=False, ic_eligible=False, metadata_quality=degraded_metadata_loss`. Applied to all 4 training_store write sites (execute_sell, _close_position_record, execute_sell_option, deferred EXITING path). `training_store.count_eligible()` added; `phase_gate._count_closed_trades()` now uses it. `execute_buy_option` ORDER_INTENT failure now returns False (was log.warning + continue). Reconciliation summary log added. 14 preservation tests + 5 restart recovery proof tests. Migration script: `scripts/migrate_training_records_quality.py` — dry-run shows 38/422 legacy records obviously degraded (29 UNKNOWN trade_type, 9 _EXT_ trade_id). Run `python3 scripts/migrate_training_records_quality.py --apply` to tag them (awaiting Amit approval).
- **Overnight Synthesis — Active ✅** (2026-05-21, v4.11.0): `_run_closed_synthesis()` added to `bot_trading.py`. Fires when `session == CLOSED`, rate-limited to once every 20 minutes. Runs a positions-only Apex call (`execute=False`) to keep the Apex Synthesis View in the dashboard fresh overnight — prevents 8+ hour staleness after market close. Dashboard stale-warning suppressed for `CLOSED` and `WEEKEND` sessions (shows "last session" label instead of misleading age warning). `static/dashboard.html` updated.
- **Portfolio Management Engine — Active (HYPOTHETICAL → Pending first EXECUTED action) ✅** (2026-05-22): `rotation_live_v1.py` G1-G9 waterfall retired. PME: `pm_engine.py`, `pm_thesis.py`, `pm_rails.py`, `pm_observability.py`, `pm_score_resolver.py`. 7 action types: HOLD, ADD, DCA, TRIM, FULL_EXIT, ROTATE, DO_NOTHING. ThesisStatus: STRENGTHENING/INTACT/**INTACT_DEGRADED**/PLAYED_OUT/DECAYING/BROKEN/UNKNOWN. **Score resolver**: 3-tier score resolution (CYCLE_CANDIDATES → PM_SCORE_CACHE → ENTRY_SCORE_FALLBACK). ENTRY_SCORE_FALLBACK → `INTACT_DEGRADED` (not silently INTACT). **NLV guard**: skips evaluation if `account_values_updated_at=None`. **4 final_status values**: EXECUTED (TRIM/FULL_EXIT/ROTATE), RECOMMENDATION (ADD/DCA — advisory only), SAFETY_BLOCKED, HYPOTHETICAL. **Enriched log fields**: entry_price, current_price, position_pct_nlv, action_pct_nlv, score_source, data_quality, market_regime, candidate_count, candidate_source_summary. `ENABLE_PM_ENGINE=True` — running LIVE in paper. Dashboard tab "Portfolio Mgmt", `/api/pm` active, `/api/rotation` tombstoned. **27 PM engine tests, all passing.** Archived: rotation files. PM_MAX_ACTION_NLV_PCT=0.20, PM_COOLDOWN_HOURS=0.25, PM_MAX_ACTIONS_PER_DAY=5.
- **Data Cleanup Sprint — Complete ✅** (2026-05-21, v4.14.1): 5 dead data artifacts deleted (`skip_log.jsonl`, `pending_order_cleanup_reconciliation_20260519.json`, `advisor_log.json`, `rotation_shadow_reports/`, `factor_analysis_price_cache.json` — 1.6MB total). `signals_log_historical.jsonl` (475MB) cold-stored to `data/archive/cold_storage/` (local only, gitignored — referenced scripts degrade gracefully via `os.path.exists()` guards). `_get_held_symbols()` in `bot_dashboard.py` fixed: was reading stale `trades.json` with broken action-field logic (always returned empty set); now uses `get_open_positions()` — the in-memory `active_trades` dict reconciled against IBKR at startup. 5 new tests in `test_bot_dashboard_data.py`.
- **Options pipeline rewrite — Active ✅** (2026-05-23, v4.23.1): Root-cause fix for 15-day options non-execution. Root cause: `alpaca_options.py` used `bid_size+ask_size` as volume proxy and multiplied by 5 for OI, permanently locking unusual-volume ratio at 0.200 (threshold 0.250). **NEW**: `options_provider.py` — single-responsibility flow data fetcher. FMP audit: NOT_USABLE_FOR_OPTIONS (all endpoints 404/403). Alpaca: PARTIAL_FLOW (real volume via `dailyBar.v`, no OI). Unusual flow = volume expansion (today/prev ≥ 1.75×). **NEW**: `expression_router.py` — COMMON vs OPTION vs NO_TRADE. Common is default. Options requires: directional signal + confirmed unusual flow + approved provider + option score beats common by ≥ 10 pts. **MODIFIED**: `alpaca_options.py` uses `raw_data=True` SDK client; `dailyBar.v` = real volume, OI is always None, quote_size labeled as quote liquidity only. **MODIFIED**: `options_scanner.py` — yfinance fully removed (earnings via FMP calendar), `_analyse_symbol()` uses `get_options_flow_data()`, removed fabricated fields (cp_ratio, call_oi, put_oi, max_pain). **MODIFIED**: `options_entries.py` — provider gate + expression router + 13 granular skip telemetry reasons. 31 new tests (test_options_provider.py, test_options_flow.py, test_expression_router.py). Full suite: 3063 passing, 17 pre-existing failures unchanged.
- **Sprint M11A — Customer Event Tape + Market Map reconciliation ✅** (2026-05-26): Customer-intelligence-only upgrade. **No trading, execution, PM, universe scoring, handoff eligibility, or Decifer Learning changed.** **NEW**: `customer_event_classifier.py` (deterministic, 15 event categories: geopolitics, commodities, earnings, M&A, central bank, macro data, China/India policy, regulation, credit/banking, technology, company shock; directional exposures; channel taxonomy of 21 transmission mechanisms). `customer_event_tape.py` (writer/reader of `data/intelligence/customer_event_tape.json`; fail-soft; freshness windows; max 200 events). `market_now_reconciler.py` (helper for market_now_builder only; produces market_mood, what_changed, key_events, sectors, themes, radar, watch_next, known_conflicts, section_freshness, source_notes; conflict matrix between price drivers and event evidence). `scripts/verify_customer_event_tape_safety.py` (8 invariants: tape not imported by execution/universe/handoff, reconciler only by market_now_builder, classifier only by tape, no yfinance, no Mac-only paths, persisted tape file customer-safe). **MODIFIED**: `saas_intelligence_output.py` (+10 allowlist fields with explicit Amit Sprint M11A approval comment; new `_FORBIDDEN_NESTED_FIELD_SUBSTRINGS` registry; `_validate_no_nested_blocked()` walker rejects blocked field names anywhere in the payload including inside radar/key_events/sectors). `market_now_builder.py` (always reconciles; degraded path still surfaces fresh events with "price confirmation pending" copy; per-source freshness exposed in `section_freshness`). `news.py` (+ `record_article_for_customer_tape` fail-soft bridge; Yahoo-RSS articles with recency<4h emitted into tape). `alpaca_news.py` (single emit call BEFORE universe filter so macro-only headlines reach the customer tape; alpaca_news does NOT import customer_event_tape directly — routed via news for clean execution-layer boundary). `catalyst_engine.py` (emit hook after `_fire()` log, before risk gate). `architecture/layer_boundary.py` (3 new SAAS_OUTPUT modules registered). **Ask Decifer**: deferred to next sprint per approval. **Tests**: 100 new (39 classifier golden + 16 tape + 15 reconciler + 20 allowlist + 10 integration). **Verifiers**: M11A safety verifier PASSES; existing intelligence/execution separation PASSES; intelligence cloud deploy verifier PASSES 14/14. **Ceasefire runtime proof**: tape records BOTH `de_escalation` + `oil_risk_premium_unwind`; with `geopolitical_risk_rising` driver active, Market Map publishes 2 `known_conflicts` ("Defence and energy still reflect recent geopolitical risk, but fresh de-escalation headlines suggest the risk premium may be fading"; "Price-based geopolitical risk drivers are still active, but oil is falling on de-escalation or peace hopes — the risk premium may be unwinding"); `market_mood` flips to "Risk-on — fresh de-escalation or risk-premium unwind"; all 6 section_freshness sections report status. **NVDA runtime proof**: classifier emits `positive_surprise_market_rejecting` with `status=under_review` and known_conflicts: "Headline read is positive (beat + raise) but market reaction is negative."
- **Test suite**: 3063 passing (2026-05-23) + 100 new Sprint M11A tests = 3163+ (2026-05-26). 17 pre-existing failures unchanged.
- **Regime detector**: VIX-proxy + SPY EMA for `scanner.get_market_regime()` (scanner-level HMM replacement closed as not recommended). HMM advisory active in signal weight router only.
- **Cloud deployment — Live ✅** (2026-05-31): Bot and intelligence layers are running on DigitalOcean droplet `206.189.135.189` (Ubuntu 22.04, 2 vCPU / 4 GB RAM, BLR1). Decifer Trading at `/opt/decifer/`, Decifer Learning at `/opt/decifer-pipeline/`. Intelligence pipeline runs as a host cron every 30 min; intelligence-api and options-flow-monitor run in Docker. Bot itself (`bot_trading.py`) runs via `decifer-trading.service` systemd unit. Full layout: `docs/DROPLET_ISOLATION.md`.
- **Futures advisory sensors — Active ✅** (2026-05-26, v4.49.0): ES=F and NQ=F 5-day returns added as advisory sensors via `futures_data.py` (isolated yfinance carve-out — the only approved runtime yfinance use). Fetched AFTER `fetch_ok` computation so futures failure never affects the core 11-sensor fail-closed count. Activates `futures_risk_on` (ES > +0.75%) or `futures_risk_off` (ES < -0.75%) drivers. Proximity warnings embedded in evidence strings (e.g. "12bp above threshold, marginal"). `_load_driver_notes()` in `apex_orchestrator.py` cross-references driver state + theme activation to generate plain-English `[INTELLIGENCE NOTES]` for the Apex prompt (active drivers, futures returns, threshold proximity alerts, weakening-theme conflicts). Dashboard Market Forces tab shows `futures_risk_on`/`futures_risk_off` cards with full `_FORCE_DETAIL`. Intelligence panels auto-refresh every 5 min. Guard test: `tests/test_no_yfinance_runtime.py` `_YFINANCE_APPROVED` carves out `futures_data.py`. **Test suite: 3233 passing, 4 pre-existing failures (test_signal_validation_report.py ordering issue).**
- **Theme Transmission Graph — Active (shadow/read-only) ✅** (2026-05-26, Sprint M12A): Customer-facing intelligence engine mapping driver → theme → subtheme → bucket → symbol with evidence gating and reason paths. **NOT execution-active in this sprint** — TTG candidates carry `candidate_source=theme_transmission_graph` and must not trigger live trading, broker logic, or portfolio mutation. **10 theme packs**: AI Energy/Nuclear, GLP-1/Metabolic Health, Defence Rearmament, Cybersecurity, Reshoring/Industrial Capex, Housing/Rate Sensitivity, Water Infrastructure, Critical Minerals/Copper, Gold/Real Assets, Digital Assets Infrastructure. **125 symbol exposures** (122 active/monitor-only, 3 needs-review suppressed). **Evidence gate**: accepted = {curated_reference, company_profile, official_source, filing, ETF_holding, news_catalyst, internal_symbol_master}; rejected = {LLM_only, keyword_only, popular_online, weak_co_mention, generic_sector_match}. **New files**: `theme_graph.py` (INTELLIGENCE layer), `theme_graph_api.py` (SAAS_OUTPUT — Flask blueprint with 4 routes: GET /api/intelligence/themes, /themes/{id}, /search?q=, /symbol/{ticker}), `data/intelligence/theme_graph/` (4 JSON data files), `scripts/theme_coverage_report.py`, `docs/THEME_TRANSMISSION_GRAPH.md`. **Wired into**: `saas_intelligence_output._ALLOWED_FIELDS` (+5 fields: theme_graph_themes/buckets/symbol_card/reason_path/search_results), `intelligence_api.py` (blueprint registered), `architecture/layer_boundary.py` (theme_graph → INTELLIGENCE, theme_graph_api → SAAS_OUTPUT). **Test suite: 47 new tests, all passing. Total: 3280+ passing.**
- **Sprint M13A — Customer Journey Refactor — Live ✅** (2026-05-26): In-place refactor of the mobile customer app at `/customer`. **No execution, trading, PM, or intelligence backend changed.** 5-tab bottom navigation (Today, Discover, Ask Decifer, Signals, Universe) with hamburger menu for secondary areas. **NEW**: `useCustomerBriefing.ts` (shared hook — centralises data fetching, market clock, freshness state, since-you-were-away logic, CustomerStory + MarketCauseCard synthesis; clock uses `Intl.DateTimeFormat` with `America/New_York` for session detection: pre_market/open/after_hours/closed/weekend; since-away reads `decifer:lastSeenAt` from localStorage, surfaces fresh key_events + what_changed + strengthening themes, capped at 5 items, computed once per session before updating lastSeenAt). **NEW**: `CustomerBottomNav.tsx` (5-tab nav, orange pill on Ask centre tab). **NEW**: `AskDeciferView.tsx` (8 suggested questions, `onAskContext` prop for deep-link from other tabs, safe placeholder copy — no execution/broker language; `SUGGESTED_QUESTIONS` exported for safety audit). **REFACTORED**: `CustomerApp.tsx` (uses `useCustomerBriefing()` hook, `HamburgerMenu` bottom sheet, `handleAskAbout()` wires Universe/Today → Ask tab). **REFACTORED**: `TodayTab.tsx` (receives story/causeCards/clock/sinceAway/freshnessState as props; since-away section with colour-coded change items; market cause cards with Ask Decifer CTA). **MINOR REFACTOR**: `UniverseTab.tsx` (`onAskAbout` prop, "Ask Decifer about this" button on TTG + radar cards, "Price data unavailable from approved source." note). `ThemeMapTab.tsx` reused as-is for Discover tab — no duplication. **SSR guard**: `typeof localStorage === "undefined"` (not `typeof window`) so vitest node environment can mock localStorage correctly. **Tests**: 30 new tests in `mobile/src/lib/useCustomerBriefing.test.ts` (formatDuration ×6, computeFreshnessState ×7, FRESHNESS_LABELS safety ×2, buildSinceAwaySummary ×11, since-away safety ×1, CustomerTab safety ×1, AskDecifer question safety ×1, session label safety ×2). All 173 mobile tests passing. **Deployed**: live at https://mobile.decifertrading.com.
- **Sprint M16 — Today Tab Actionable Intelligence Redesign — Live ✅** (2026-05-28): Full Today tab Sprint M16A+B in one session. **No execution, trading, PM, or intelligence backend changed.** **NEW**: `/api/morning-brief/route.ts` — parallel FMP fetches for economic calendar (today, US + High/Medium impact only, 20 max), earnings calendar (today through +7 days, 200 max), and analyst upgrades/downgrades (100 recent); typed exports `EconEvent`, `EarningsItem`, `AnalystItem`, `MorningBriefPayload`; 10-min Vercel cache; graceful per-section fallback — never 502. **Market tape expanded**: DIA (Dow Jones ETF) added to `/api/market-tape/route.ts`. `TapeSnapshot` gains `dia_pct`. Hero header redesigned: SPY as big primary number, secondary strip (Nasdaq · Dow · VIX). **TTG fetch lifted to main component**: single `useEffect` builds both `names: NameEntry[]` (for WhereLooking) and `symbolMap: Map<string, { theme_label }>` (for Agenda + Analyst sections) — eliminates three independent TTG fetches. **NEW**: `TodayAgendaSection` component — economic releases with active-driver annotation chips (8 keyword groups mapping event names to driver IDs) + earnings this week filtered to TTG symbols; shows today/tomorrow/date label + pre-market/after-close timing. **NEW**: `AnalystMovesSection` component — upgrades/downgrades from today and yesterday filtered to TTG symbols; action display (Upgraded/Downgraded/Initiated/Target ↑/↓) with icon + grade transition + theme chip. **WhereLookingSection** refactored: internal TTG fetch removed; now accepts `ttgNames: NameEntry[] | null` prop, exposure-type chips (Direct/Supply chain/ETF), theme bucketing with 2-per-theme cap, day-based rotation. **Sections removed**: SinceYouWereAway, SectorGrid (+ SectorTile dead code), EventsBehindMoves, WhatCouldChange standalone. **Section order**: Narrative → Agenda → WhatIsMoving → Movers → AnalystMoves → WhereLooking → News. **Deleted**: `buildWhatCouldChange` import (no longer used in TodayTab), `KeyEvent` type import, `Eye`/`ChevronDown`/`ChevronUp` icon imports, `SectorTile`/`SectorGrid` component definitions (referenced undefined `SectorEntry`), `EventCard` component definition. TypeScript clean. **Total: 458 mobile tests passing.** **Deployed**: live at https://mobile.decifertrading.com.
- **Sprint M14B — Customer Market Story Repair — Live ✅** (2026-05-27): Targeted narrative quality fixes for the Today screen. **No execution, trading, PM, or intelligence backend changed.** **Market tape expanded**: IWM (small-cap breadth) and UUP/DXY (US dollar proxy) added to `/api/market-tape/route.ts`. `TapeSnapshot` gains `iwm_pct` and `dxy_pct` fields. `deriveTapeSnapshot()` in `TodayTab.tsx` maps `UUP → dxy_pct`, `IWM → iwm_pct`. **Real narrative logic**: `buildTapeOpener()` replaced with `detectTapeScenario()` + 7-case switch — detects narrow rally (QQQ up, IWM lagging), tech-led (QQQ outperforms SPY ≥0.5%), broad risk-on, defensive (SPY weak + bonds/gold bid), broad risk-off, quiet tape, basic SPY directional. `TapeScenario` type threads through `buildBreadthSentence()` to avoid repeating breadth already covered in the opener. `buildBreadthSentence()` adds real DXY/oil/bond/IWM breadth context. **AI Infrastructure deduplication**: `macroLabelMentionsAI()` detects when macro_label already names AI infrastructure; `buildDriverMiddleSentence()` swaps to sector-focused alternatives (`AI_SECTOR_ALTERNATIVE`, `AI_CLUSTER_ALTERNATIVE`) to prevent the same concept appearing twice in adjacent sections. **Headlines fallback**: `TodayTab.tsx` fetches `/api/headlines` when `key_events[]` is empty; shows up to 4 headlines (title + source + timestamp) in a "Latest market headlines" section; shows nothing if fetch fails or returns empty. **Nav truncation fix**: `CustomerBottomNav.tsx` — removed `tracking-wide` from non-center tab labels, added `whitespace-nowrap`; `NAV_ITEMS` exported for testability. **Tests**: 41 new tests across 5 describe blocks (8 tape scenarios, 4 AI dedup, 2 safety, 5 nav, + 1 pre-existing AI cluster test updated to match dedup behavior). **Total: 414 mobile tests passing**. TypeScript clean. Mobile package version bumped 0.3.0 → 0.4.0.
- **Sprint M14A — Customer Home Market Story Hero — Live ✅** (2026-05-27): `buildNarrativeParagraph`, `buildWhereLooking`, `buildWhatCouldChange` added to `customerBriefingModel.ts`. Replaces the robotic bullet-list opening with a 2–3 sentence synthesised market briefing paragraph (regime opener → driver middle → breadth sentence). Optional `TapeSnapshot` integration: when SPY/VIX tape data is available, the paragraph leads with a real price-action opener ("Broad equities are gaining today — the S&P 500 is +0.8%, and volatility is contained at 13"). API `plain_english_summary` is used verbatim if it passes prohibited-term and fallback-phrase guards; otherwise synthesised from active drivers. `buildWhereLooking` derives sector labels (FORCE_THEMES) + up to 5 connected names from radar/universe_snapshot, deduplicates both, returns `empty=true` when no active drivers. `buildWhatCouldChange` returns 2–3 driver-specific risk bullets, appending `known_conflicts` as a risk item; falls back to generic watchpoints. `TodayTab.tsx` uses `buildNarrativeParagraph` in `MarketStoryHero` and `buildWhereLooking` / `buildWhatCouldChange` for the corresponding sections (wired into `19b45d7` contrast pass — committed without model file). **Uncommitted deploy risk resolved** — this commit brings git state into alignment with the live Vercel deployment. **No execution, trading, PM, or intelligence backend changed.** **Tests**: 83 customerBriefingModel tests (buildNarrativeParagraph ×12, buildWhereLooking ×9, buildWhatCouldChange ×7 + pre-existing 55); 394 total mobile tests passing. TypeScript clean, lint clean (3 pre-existing warnings). **Deployed**: live at https://mobile.decifertrading.com/customer. **Non-blocker**: React #418 hydration mismatch on clock time rendering — pre-existing since M13A (`useState(computeMarketClock)` calls `new Date()` at SSR time); `NEXT_PUBLIC_APP_VERSION` env var not set in Vercel (shows `vdev`) — pre-existing since `414c100`.
- **Sprint M13G — Company-Aware Name Intelligence Copy — Live ✅** (2026-05-27): NameResearchSheet now synthesizes company-specific authored copy instead of rendering raw data fields. **No execution, trading, PM, or intelligence backend changed.** **NEW**: `buildWhyItMattersNow(symbol, storyGroup, reasonToCare, options)` — 1-2 authored sentences using company name + connection type ("appears as a direct exposure" / "has supply chain exposure" / etc.) + driver active note + watch type note; replaces raw `reasonToCare` render in sheet. **NEW**: `buildRiskNoteLine(symbol, riskNote, companyName?)` — prefixes risk note with "For [Company]:" when company name is available, distinct from symbol, and not already in the note. **UPDATED**: `buildFundamentalsLine` — items reordered (revenue growth first, then margin, EPS, PE); natural "X, Y, and Z" joining; shorter caveat "Trailing figures — may not reflect recent guidance.". **UPDATED**: `buildAnalystLine` — two-sentence structure: "Analyst coverage shows X, sentiment Y." + "Price context is around $Z." + "Market context only — not a recommendation." **MODIFIED**: `NameResearchSheet.tsx` (uses `buildWhyItMattersNow` and `buildRiskNoteLine`; no new state or effects). **Tests**: 18 new in `nameResearchModel.test.ts` (buildWhyItMattersNow ×12, buildRiskNoteLine ×6). **Total**: 366 mobile tests passing. TypeScript clean, lint clean. **Deployed**: live at https://mobile.decifertrading.com.
- **Sprint M13F — Name Detail Sheet Freshness, Revenue Context & Copy Polish — Live ✅** (2026-05-26): Detail sheet now refreshes live price on open via `/api/name-prices` with `mergeFreshPrice()` fallback and "Live"/"Xm ago" freshness label. **No execution, trading, PM, or intelligence backend changed.** **EXTENDED**: `/api/name-fundamentals/route.ts` (4th fetch — `financial-growth?period=annual` for `revenueGrowth`; extracts `epsTTM` from existing `key-metrics-ttm` response; each section still fails independently). **EXTENDED**: `nameResearchModel.ts` (+`mergeFreshPrice(fresh, existing)` merges a fresh `NamePriceEntry` into an existing `ResearchPriceAction`, falling back if changePct null; +`buildPriceFreshnessLabel(ts)` returns "Live" if < 2 min, "Xm ago" otherwise, "" for null/invalid; `buildFundamentalsLine` adds EPS and revenue growth lines when available; `buildDetailQuestions` gains optional `companyName?` param — generates company-specific first question, falls back to symbol). **MODIFIED**: `NameResearchSheet.tsx` (second useEffect for price refresh with cancellation guard; section labels polished to "Why it matters now", "Market view", "Risk to watch", "Ask Decifer"; company name passed to `buildDetailQuestions`). **Tests**: 23 new in `nameResearchModel.test.ts` (mergeFreshPrice ×5, buildPriceFreshnessLabel ×6, buildFundamentalsLine EPS/revenueGrowth ×7, buildDetailQuestions companyName ×5). **Total**: 348 mobile tests passing. TypeScript clean, lint clean. **Deployed**: live at https://mobile.decifertrading.com.
- **Sprint M13E — Name Detail Intelligence Sheet — Live ✅** (2026-05-26, v4.63.0): Tapping a name card in the Names tab now opens a bottom sheet with company identity, price action, financial context, analyst context, risk note, and 4 contextual Ask questions. **No execution, trading, PM, or intelligence backend changed.** **NEW**: `mobile/src/app/api/name-fundamentals/route.ts` (GET handler — FMP `stable/profile` + `stable/key-metrics-ttm` + `stable/analyst-consensus`, single symbol, 5-min Vercel cache, graceful per-section fallback — never 502; returns `{ symbol, ts, profile?, fundamentals?, analyst?, available, source }`). **NEW**: `mobile/src/views/NameResearchSheet.tsx` (bottom sheet — async fundamentals fetch with cancellation guard, `key={card.symbol}` in parent forces fresh mount per symbol; sections: story chip, why connected, company context skeleton → company line, financial context skeleton → fundamentals line, analyst context shown after load, risk note amber box, 4 tappable Ask questions, disclaimer). **EXTENDED**: `nameResearchModel.ts` (+`NameFundamentalsResponse` type; +`formatMarketCap`, `buildCompanyLine`, `buildFundamentalsLine`, `buildAnalystLine`, `buildDetailQuestions` pure functions; `normaliseConsensus` private helper; `buildDetailQuestions` defensively translates raw theme IDs via `TTG_STORY_LABELS`). **MODIFIED**: `UniverseTab.tsx` (`ResearchCard` gains `onTap?` prop and renders as `<button>` when provided; `StoryGroupSection` gains `onCardTap` prop; `selectedCard` state + `NameResearchSheet` rendered at fragment root). **Tests**: 41 new in `nameResearchModel.test.ts` (formatMarketCap ×6, buildCompanyLine ×8, buildFundamentalsLine ×9, buildAnalystLine ×8, buildDetailQuestions ×7, forbidden language/raw-theme-ID guards on all). **Total**: 325 mobile tests passing. TypeScript clean, lint clean. **Deployed**: live at https://mobile.decifertrading.com.
- **Sprint M13D — Name-Level Research Intelligence — Live ✅** (2026-05-26, v4.62.0): In-place upgrade of the Names tab and new price route. **No execution, trading, PM, or intelligence backend changed.** **NEW**: `mobile/src/app/api/name-prices/route.ts` (GET handler — FMP `stable/batch-quote-short`, `?symbols=` query param, max 50 symbols via `parseSymbols`, returns `{ prices: NamePriceEntry[], ts }`, 2-min Vercel cache, graceful empty-prices fallback on provider failure — never 502). **NEW**: `mobile/src/lib/namePriceUtils.ts` (pure TS utilities — `NamePriceEntry`, `parseSymbols`, `chunkArray`, `MAX_SYMBOLS=50`; no Next.js deps). **NEW**: `mobile/src/lib/nameResearchModel.ts` (pure-function model layer — `ResearchNameCard`, `ResearchStoryGroup`, `ResearchPriceAction` types; `TTG_STORY_LABELS` (10 story groups); `derivePriceActionTone`, `buildPriceAction`, `resolveWatchType`, `buildResearchCard`, `buildStoryGroups`, `buildRadarCards`, `prioritySymbols` functions; customer-safe language throughout). **REWRITTEN**: `UniverseTab.tsx` (filter chips removed; two-phase loading: Phase 1 TTG structure → Phase 2 prices for top-50 priority symbols in a single combined effect; story-grouped sections with "In play" driver-active badge; `ResearchCard` shows price action tone, watch type badge, reason to care, risk note, Ask CTA; radar items tappable via `onNameSelect`; "Show more" expand when group > 5 cards; backward-compat Props interface preserved). **Tests**: 43 new in `nameResearchModel.test.ts` (tone mapping, fallbacks, watch type, grouping, priority, forbidden language, parseSymbols). **Total**: 284 mobile tests passing. TypeScript clean. **Deployed**: live at https://mobile.decifertrading.com.
- **Sprint M13B — Customer Intelligence Experience — Live ✅** (2026-05-26, v4.61.0): In-place product upgrade of the mobile customer app. **No execution, trading, PM, or intelligence backend changed.** Tab set renamed and reordered: Today / Forces / Ask / Themes / Names (was Today / Discover / Ask / Signals / Universe). **NEW**: `customerBriefingModel.ts` (pure-function model layer — `CustomerMarketRegime`, `CustomerMarketStory`, `CustomerMarketForce`, `CustomerConnectionNode` types; `buildCustomerRegime()`, `buildCustomerMarketStory()`, `buildCustomerForces()`, `buildConnectionTree()`, `buildContextualSuggestions()`, `containsProhibitedTerm()`, `normalizeForceId()` functions; 7-term PROHIBITED_RENDERED_TERMS list; FORCE_THEMES map; 55+ tests). **NEW**: `ForcesTab.tsx` (active forces with green badge + evidence chip + connection tree + expandable risk_to_monitor + Ask CTA; dormant forces section collapsed behind toggle; "See connected names" CTA routes to Names tab). **MODIFIED**: `TodayTab.tsx` (MarketStoryHero added as first section: regime badge, macro label, headline, summary bullets, caution from known_conflicts, watch-next, CTAs; "See forces" button wires to Forces tab). **MODIFIED**: `AskDeciferView.tsx` (live contextual questions from `buildContextualSuggestions(data)` when data available; "Live context" badge; falls back to static list; `SUGGESTED_QUESTIONS` alias preserved for M13A test). **MODIFIED**: `CustomerBottomNav.tsx` (CustomerTab type: `"today"|"forces"|"ask"|"themes"|"names"`; Zap/Layers/Search icons). **MODIFIED**: `useCustomerBriefing.ts` (TTG fetch via separate useEffect; `activeForces`, `dormantForces`, `connectionTree` derived and exposed). **MODIFIED**: `CustomerApp.tsx` (ForcesTab wired; `handleTabChange` guard updated; HamburgerMenu updated). **Safety**: PROHIBITED_RENDERED_TERMS blocks operator-only terms; "apex" excluded (substring of "capex"); all 7 prohibited terms verified. **Tests**: 55 new tests in `customerBriefingModel.test.ts` (63 total including model safety). All 228 mobile tests passing. TypeScript clean. **Deployed**: live at https://mobile.decifertrading.com.

---

## Architectural Decisions — The "Why" (read before touching anything)

These decisions are LOCKED. Do not second-guess them without reading `docs/DECISIONS.md` first and flagging Amit.

### Signal Engine: 10 Independent Dimensions, Not Overlapping Oscillators
RSI + Stochastic + CCI all measure momentum — using all three is one signal dressed up as three. Each of Decifer's 10 dimensions (Directional, Momentum, Squeeze, Flow, Breakout, PEAD, News, Short Squeeze, Reversion, Overnight Drift) measures something fundamentally different. Two optional dimensions (Social, IV Skew) are config-gated. Adding a new dimension requires the same standard: it must be orthogonal to the existing ones.

### Direction-Agnostic Scoring, Not Regime-Switched Prompts
We do not tell Apex "you're in a bear market, be more bearish." That replaces bullish groupthink with regime-driven groupthink — one bad regime call cascades through all synthesis decisions. Instead, the signal engine scores setup *conviction* independently of direction. Bearish setups score identically to equivalent bullish setups. The market determines the long/short ratio naturally.

### Regime Detection: VIX-Proxy Locked, HMM Deferred
Hard classifier (BULL_TRENDING / BEAR_TRENDING / CHOPPY / PANIC) via VIX levels + SPY EMA. HMM is NOT running in production — `PRODUCTION_LOCKED = True`. Gate to reopen HMM: ≥200 closed trades AND IC Phase 2 review complete. Running two regime detectors in parallel is architecturally incoherent. HMM replaces VIX-proxy entirely when the gate is met, does not run alongside it.

### Skew Tracking: Diagnostic Only, Never a Feedback Loop
`get_directional_skew()` in `learning.py` tracks % long vs short. This is a dashboard metric and alert for Amit — it is NOT fed back into Apex context. Feeding skew back ("you've been 80% long, correct") creates forced trades to balance a statistic. The market is structurally long-biased. Fighting that base rate is wrong.

### Apex Single-Synthesizer: One Sonnet Call, Not 4-Agent Pipeline (Decifer 3.0)
The 4-agent pipeline (Technical Analyst + Trading Analyst Opus + Risk Manager + Final Decision Maker) is replaced by `apex_call()` in `market_intelligence.py` — a single `claude-sonnet-4-6` call that receives all context (candidates, regime, portfolio state, overnight research, session character, IC weights) and returns a structured `ApexDecision` JSON with `new_entries[]` and `pm_actions[]`. Three calls per scan cycle:
1. **Track A** — new entries (live execute)
2. **Track B** — PM TRIM/EXIT/HOLD review (live execute)
3. **Shadow** — divergence logging only (`USE_APEX_V3_SHADOW=True`)

Forced exits (EOD flat, 90-min INTRADAY timeout, architecture violations) remain deterministic — they never go through Apex. Regime-change sells (`check_thesis_validity()`) are also deterministic — `_apex_mode_sells` builds directly from `positions_to_reconsider`, no LLM involved. Legacy code was deleted at post-migration cleanup — no rollback path.

**Entry floor rule (locked):** When ≥3 candidates score ≥35 with no named systemic blocking condition, Apex MUST produce at least one new entry. `FEAR_ELEVATED` is a regime descriptor, not an AVOID mandate. `divergence_flags` restrict instrument selection to stocks only — they do NOT veto the stock trade.

**Model = Sonnet, not Opus.** Amit's explicit decision at cutover. Do not change without Amit approval.

### Intelligence Pipeline: Full End-to-End Flow (v4.0, live 2026-05-19)
The complete execution path from market data to trade:
```
Economic Intelligence  →  live_driver_resolver.py: 9 real symbols, 7 deterministic rules → live driver state
Live Driver Resolver   →  candidate_resolver.py: reads live driver state (not hard-coded drivers)
Candidate Sources      →  economic_candidate_feed.json: scored candidates with reason-to-care classification
Eligibility            →  approval_status + risk_flags on each candidate
Controlled Handoff     →  run_intelligence_pipeline.py → live/active_opportunity_universe.json
Live Bot               →  handoff_reader.py reads handoff, feeds candidates to Apex
Trade Readiness        →  signal scoring (10 dimensions) + IC-weighted direction
Risk / Sizing          →  risk.py + orders_core.py
Execution              →  IBKR via bot_ibkr.py
```
Intelligence pipeline and live bot run **separately** — the pipeline publishes a handoff file; the bot reads it. They do not share state at runtime.

### News Sentinel: Single Apex Call, Not 3-Agent Pipeline
Sentinel `NEWS_INTERRUPT` path builds an `ApexInput` via `build_news_trigger_payload()` in `sentinel_agents.py` and calls `apex_call()` — same synthesizer as scan cycles, not the old 3-agent (Catalyst Analyst + Risk Gate + Instant Decision). `handle_news_trigger()` routes through `apex_orchestrator._run_apex_pipeline(execute=True)`. The catalyst symbol is **pre-scored** before the Apex call so Track A always has a real candidate (not an empty list). Position sizing remains 0.75× sentinel multiplier. Hardcoded risk limits still apply.

### Paper Config: Aggressive for Data Generation
Paper trading thresholds are deliberately loose (min_score 14, max_positions 100 sanity ceiling). Cost of a bad paper trade = zero. Value = training data. Every parameter that differs from live config is preserved as an inline comment in `config.py`. When switching to live, revert ALL of them (live: min_score 28). Note: `agents_required_to_agree` remains in config as a legacy key (validation requires it) but is not used by Apex — do not treat it as a meaningful gate.

### ThreadPoolExecutor for score_universe()
`score_universe()` uses `ThreadPoolExecutor`. IBKR `reqHistoricalData` is thread-safe via a shared IB connection — the original yfinance thread-safety concern (GitHub issue #2557) no longer applies since Alpaca is the primary data source. Do not revert to ProcessPoolExecutor without verifying the data source in use.

### REVERSION Dimension: ADF Gate Is Non-Negotiable
The ADF test (p < 0.05) is the safety gate for mean-reversion scoring. Without it, 32% of random walks score positive on VR/OU/Z-score metrics. If ADF p ≥ 0.05, REVERSION scores 0 — no exceptions.

### Inverse ETFs, Not Direct Short Selling
Bearish exposure uses SPXS, SQQQ, UVXY. No borrow costs, no margin complications. Tracking error on leveraged products is acceptable for short-duration trades.

### Options: ATM Delta 0.50 Targeting
OTM options (δ 0.30–0.40) have higher leverage per dollar of premium — but ATM (δ 0.50) is the correct choice for this system for three reasons:
1. **Liquidity** — ATM options have the highest volume, tightest spreads, and most open interest. Fill quality matters more than theoretical leverage.
2. **Gamma/theta ratio** — ATM options have maximum gamma per unit of theta. OTM options at short DTE decay catastrophically fast and require a large move AND correct timing; ATM only requires directional correctness.
3. **Signal type** — Decifer's momentum/breakout signals fire when a stock is already moving. ATM captures that move immediately. OTM requires the move to exceed the strike before theta erodes the position.

### Smart Execution: $10K / 500-Share Threshold
TWAP/VWAP/Iceberg only for orders above $10K notional or 500 shares. Smaller orders use simple limit orders. Smart execution adds latency — for small orders the market impact is negligible.

---

## Data Source Priority (always check this order)

1. **Alpaca Algo Trader Plus** (PRIMARY for market data — paid, active): real-time quotes, historical bars, streaming, options Greeks. Use first for ALL price/volume/intraday data. **MCP is data-only — never use `mcp__alpaca__get_all_positions` or any Alpaca MCP position/order tool to check portfolio state. Positions and trades live in IBKR.**
2. **FMP — Financial Modeling Prep** (PRIMARY for fundamentals/events — paid premium, 750 calls/min, MCP server connected): analyst consensus, price targets, grade breakdowns, insider trades (Form 4), congressional trades (Senate/House), income statements, revenue growth, EPS acceleration, key metrics TTM, DCF valuations, earnings calendar, earnings estimates, short interest, shares float, sector performance, stock news, press releases, 30 years history. **Use FMP first for anything fundamental, event-driven, or analyst-related.** Client: `fmp_client.py`. MCP server: `fmp` (connected via `~/.claude.json`). **MCP is data-only — never use FMP MCP to infer portfolio or position state.**
3. **Alpha Vantage** (paid, active): macroeconomic indicators, economic calendar. Fallback for fundamentals if FMP is unavailable.
4. **IBKR TWS**: execution, order management, and **the source of truth for all portfolio positions and trade history**. To check current positions ask Amit to query TWS directly or read `data/trades.json`. Historical data only when Alpaca is insufficient.
5. ~~**yfinance**~~ — **REMOVED (v4.31.1)**. Not approved for runtime, fallback, enrichment, analytics, or validation paths. Production modules must not import yfinance. Fail closed if Alpaca is unavailable. See `tests/test_no_yfinance_runtime.py` for the guard test.
6. Yahoo RSS, Finviz — supplementary news only (RSS feed only, not yfinance library). TradingView Screener was removed (replaced by three-tier committed universe).

---

## What NOT to Build Without a Gate

| Deferred Feature | Gate Condition | Status |
|-----------------|----------------|--------|
| HMM Regime Detection | ≥200 closed trades + IC Phase 2 review | **GATE MET — awaiting Amit approval** |
| Walk-Forward Weight Calibration | HMM + Alphalens both complete | Blocked on HMM |
| Signal Validation (Alphalens) | ≥200 trades across regimes | **GATE MET — awaiting Amit approval** |
| ML training-readiness | ≥200 `ml_eligible=true` exact closed-trade records in `canonical_learning_dataset.jsonl`, ≥2 regimes, no regime >75% (50-trade gate **RETIRED** — `ml_engine.py` deleted) | **NOT MET — 0 exact-joined records** |

---

## Key Files

| File | Purpose |
|------|---------|
| `docs/DECISIONS.md` | Full decision log with reasoning — read before changing architecture |
| `docs/PRODUCT_DEFINITION.md` | Authoritative state of what's actually built and running |
| `ARCHITECTURE.md` | System overview and development workflow |
| `roadmap/README.md` | Feature pipeline with dependency graph |
| `roadmap/` | Individual feature specs |
| `chief-decifer/state/` | Data contracts (sessions, research, specs) — path is sacred |
| `config.py` | All thresholds — live values preserved as inline comments |

---

## Session Protocol (mandatory)

0. **CHECK ENVIRONMENT** — before anything else, verify the machine is set up:
   - Run `python3 -c "import anthropic, pandas, dash"` — if this fails, run `bash scripts/setup.sh` immediately and stop until it completes.
   - Check that `.env` exists at the repo root — if missing, run `bash scripts/setup.sh` (it will pull all secrets from iCloud Keychain automatically).
   - Do not proceed with any task until the environment check passes.

1. **LOAD CONTEXT** — read checkpoint, last 2 session logs, active specs. If a `pending-doc-update.json` warning was injected, handle it first.
2. **REVIEW PENDING** — confirm branch, what feature is in flight
3. **COMMIT TO MASTER** — push directly to master unless Tier 3 multi-session rewrite
4. **TEST** — run relevant tests before declaring done
5. **UPDATE DOCS** — before committing, always ask: did the phase change? Did a new decision get locked? If yes:
   - Update "Current State" section in this file (CLAUDE.md)
   - Add the decision + reasoning to `docs/DECISIONS.md`
   - Update `memory/project_decifer.md` if phase or gates changed
   - The Stop hook will catch misses and prompt you automatically
6. **DRAFT SUMMARY** — write session log for Amit to approve before committing. Use this format every time:

```
DATE: [today]

WHAT CHANGED:
  - [file or feature]: [what was built/fixed and why]

WHAT WAS DELETED:
  - [file or function removed, or "nothing deleted"]

DECISIONS MADE:
  - [any locked architectural decision, or "none"]

TESTS:
  - [pass/fail count, or "tests not applicable"]

WHAT IS NEXT:
  - [next logical task, or "nothing — phase gate not met"]
```

7. **COMMIT & PUSH** — only after Amit approves

---

## Governance Rules

### Complexity Tiers
- **Tier 1** — Fast (read/check/scan): no approval needed
- **Tier 2** — Standard (implement/fix): proceed, document
- **Tier 3** — Deep (multi-file refactor, new phase planning): require Amit approval of approach BEFORE any code

### Architecture Integrity (paramount)

**PATCHES ARE COMPLETELY PROHIBITED. THIS IS A HARD RULE WITH NO EXCEPTIONS.**

A patch is any change that suppresses a symptom without addressing its root cause. This includes: `try/except` blocks added to silence errors, default fallback values that mask missing data, conditional branches added to "handle" an edge case that shouldn't exist, and any fix that makes a test pass without understanding why it was failing.

**The mandatory sequence before a single line of code is written:**
1. **STOP.** Do not open any file with intent to edit.
2. **DIAGNOSE.** Trace the failure to its actual origin — not the line that raised the error, but why that condition exists at all. Read every layer involved. Follow imports. Read callers. Read the data flow.
3. **ARTICULATE.** State the root cause in one clear sentence. If you cannot do this, you do not understand it yet — keep digging.
4. **RESEARCH.** Understand what the correct design looks like from first principles. What should this code do? Why did the original design fail to do it? What invariant was violated?
5. **ONLY THEN: implement.** Fix at the root. If the root cause requires a rewrite, do the rewrite. If it requires a design decision, bring it to Amit before writing a single line.

**Violations that will not be tolerated:**
- Catching an exception to prevent a crash without removing the condition that causes it
- Adding an `if x is None: return` guard without understanding why `x` is None
- Hardcoding a value to make output correct without understanding why the computed value is wrong
- Any change described as "temporary" or "for now"
- Adjusting a test to make it pass rather than fixing the code it tests

If a request conflicts with the architecture or vision, flag it to Amit before proceeding — never work around it silently.

Functions > 30 lines are doing more than one thing. Modules > 200 lines have grown beyond scope. Stop and split.

Every module has one clearly defined responsibility. If you cannot state it in one sentence, it's doing too much.

### Before Any Implementation
1. **What is the root cause — stated in one sentence?** If this cannot be answered, stop. Do not proceed.
2. Does this belong in the existing architecture, or does it require a design decision first?
3. Is this fix correct from first principles, or does it merely suppress a symptom?
4. Does this change sustain or erode the long-term vision?

### Code Integrity
- Never invent function names, method signatures, or API behaviours without reading the actual source first.
- Any change touching signal generation, scoring, filtering, position sizing, or order submission — trace the full path from signal origin to order execution before committing.

### Hard Limits
- Paper account only: IBKR paper (DUP...). No live order submission.
- No secrets, credentials, or .env content in any commit.
- Never run `git reset --hard`, `git push --force`, or `git clean -f` without explicit Amit instruction.
- Pre-existing errors in touched files must be fixed in the same session, not silently worked around.

### ⛔ METADATA IMMUTABILITY — HARD RULE, NO EXCEPTIONS

**Trade metadata written at entry time is permanent. No process is ever allowed to delete or overwrite it.**

Metadata = `trade_type`, `conviction`, `reasoning`, `signal_scores`, `agent_outputs`, `entry_regime`, `entry_thesis`, `entry_score`, `ic_weights_at_entry`, `pattern_id`, `setup_type`, `advice_id`, `open_time`, `atr`, `high_water_mark`, `metadata_status`.

This rule exists because paper trading data IS the product. Every trade is a training record. Losing metadata is equivalent to losing the trade entirely — it cannot be reconstructed.

**Every code path that creates or updates a position must obey these invariants:**

1. **Write-ahead is mandatory**: `ORDER_INTENT` must be written to `event_log` BEFORE submitting any order to IBKR. If the intent write fails, the order must NOT be submitted. No exceptions for any entry path: `execute_buy`, `execute_short`, `execute_buy_option`, reconcile EXT paths, options EXT paths.

2. **No process may stamp `metadata_status: "MISSING"` on a position that already has real metadata** (`trade_type` set and not `"UNKNOWN"`). The `_safe_set_trade` immutability guard in `orders_state.py` enforces this. `DECISION_METADATA_FIELDS` is the authoritative list — adding a new decision field to a position requires also adding it to that frozenset.

3. **Reconcile may not overwrite decision metadata**: `reconcile_with_ibkr` and `update_positions_from_ibkr` may only update price, pnl, status, and order IDs. They must use `_safe_set_trade` or `_safe_update_trade`, never direct dict assignment.

4. **EXT and orphan paths must anchor metadata**: Any position created by the reconcile EXT path (position found in IBKR but not in local state) must write both `ORDER_INTENT` and `ORDER_FILLED` to `event_log` immediately. An unanchored position in `active_trades` is a bug.

5. **No manual intervention in paper mode**: The bot must self-recover from stuck positions, stale EXITING states, and orphaned metadata. If the bot cannot self-recover, that is a code bug — fix the code, do not intervene manually.

**Violations are never acceptable regardless of urgency. If a change would cause any of the above to be violated, stop and escalate to Amit before writing a single line.**

### Data Contracts (paths are sacred — do not change)
Chief has **one** state directory — `chief-decifer/state/`. No fallback. No split-brain.
The session-start hook reads from this path; Chief's panels read from this path; Cowork writes here.

| Data Type | Path | Written by | Read by |
|-----------|------|-----------|---------|
| Session logs | `chief-decifer/state/sessions/` | Cowork | Chief Decifer, session-start hook |
| Research | `chief-decifer/state/research/` | Cowork, `researcher.py` | Chief Decifer, session-start hook |
| Feature specs | `chief-decifer/state/specs/` | Cowork | Chief Decifer, session-start hook |
| Backlog | `chief-decifer/state/backlog.json` | Cowork | Chief Decifer, session-start hook |
| Vision | `chief-decifer/state/vision.json` | Amit | Chief Decifer, Cowork |
| Archived | `chief-decifer/state/archive/` | Cowork (on supersession) | humans only |
| Chief-internal | `chief-decifer/state/internal/` | Chief's own jobs | Chief Decifer only |

**Rule:** `research-*.json` belongs in `research/`, never in `specs/`. Specs describe
feature intent or completed work; research files are knowledge-base entries.

### Commit Format
```
<type>(<scope>): <short description>

<body — what changed and why, 2-3 sentences>

Approved-by: Amit
```
Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

---

---

## New Machine Setup

**Step 1 — Clone the repo:**
```bash
git clone https://github.com/DeciferBot/decifer-trading.git "decifer trading"
cd "decifer trading"
```

**Step 2 — Run the setup script (handles everything automatically):**
```bash
bash scripts/setup.sh
```

The script handles everything automatically:
- Installs Homebrew, `python@3.11`, `ta-lib`, `uv`, and other system deps
- Installs all Python packages from both `requirements.txt` and `Chief-Decifer-recovered/requirements.txt` via `uv` (no manual pip install needed)
- Restores `.env` from iCloud Keychain or iCloud Drive backup
- Installs NLTK data, launch daemons, etc.

**If `.env` is missing after setup** (no iCloud backup on new machine):
1. Copy the template: `cp .env.example .env`
2. Fill in all 9 keys: `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`, `FMP_API_KEY`, `ALPHA_VANTAGE_KEY`, `IBKR_ACTIVE_ACCOUNT`, `IBKR_PAPER_ACCOUNT`, `FRED_API_KEY`

**Step 3 — Verify the environment:**
```bash
python3 -c "import anthropic, pandas, dash"
```

**Signs of an unconfigured environment to watch for:**
- `ModuleNotFoundError` on import → run `bash scripts/setup.sh`
- `ANTHROPIC_API_KEY` empty → `.env` not loaded; check root `.env` exists
- Signal scripts writing to wrong paths → `config.py` auto-detects repo root via `__file__`, no `DECIFER_REPO_PATH` needed
- `alpaca-py` missing → `python3.11 -m pip install alpaca-py` (setup.sh should handle this but may fail on non-interactive terminals requiring sudo)

**⚠️ Chief Decifer requirements conflict warning:**
Running `pip install -r Chief-Decifer-recovered/requirements.txt` will downgrade `dash` and `pandas` to older versions. Always restore with:
```bash
python3.11 -m pip install "dash>=4.1.0" "pandas>=3.0" "dash-bootstrap-components>=2.0"
```
after running Chief Decifer requirements.

---

*This file is the primary session context. Update "Current State" when phases change or new decisions are locked. Full reasoning lives in `docs/DECISIONS.md`.*
