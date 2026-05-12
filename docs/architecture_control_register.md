# Architecture Control Register
**Sprint:** Architecture Audit — documentation only  
**Branch:** audit/architecture-control-register  
**Date:** 2026-05-12  
**Author:** Production Architecture Auditor (Claude Code)  
**Status:** Authoritative as of audit date — must be updated when runtime changes

> **Claim rules used throughout this document:**  
> - **Observed** — directly proven from code or config  
> - **Inferred** — reasonable conclusion from evidence  
> - **Unverified** — plausible but needs runtime confirmation  
> - **Historical** — mentioned in docs/history but not proven active now

---

## A. Runtime Entry Points Register

### A1. bot.py — Main Orchestrator

| Field | Value |
|-------|-------|
| Path | `bot.py` |
| Purpose | Always-on process: dashboard, scan loop, IBKR/Alpaca streams, sentinel, news handler |
| Trigger | Manual: `python3 bot.py`; expected to restart via macOS launchd or Docker (not yet configured) |
| Owner process | Foreground process on Amit's machine |
| Reads | `.env`, `config.py`, `data/committed_universe.json`, `data/daily_promoted.json`, `data/position_research_universe.json`, `data/trades.json`, `data/ic_weights.json`, `data/live/active_opportunity_universe.json` (if handoff enabled) |
| Writes | `data/decifer.log`, `data/audit_log.jsonl`, dashboard state (in-memory), delegates to `bot_trading.py`, `event_log.py`, `training_store.py` |
| Downstream consumers | Dashboard (`bot_dashboard.py`), all scan-cycle subsystems |
| Freshness expectation | Always-on; failure = immediate production outage |
| Runtime status | **production_runtime** |
| Evidence | Observed: `bot.py` exists, `schedule` calls at lines 555–627 and 835, IBKR/Alpaca stream setup, dashboard server |
| Confidence | **High** |
| Recommended action | Add restart-on-failure launchd plist for bot.py itself (currently manual restart only) |

**Internal schedule library jobs (Observed — bot.py lines 555–835):**

| Tag | Schedule | Function | Conflict with launchd? |
|-----|----------|----------|----------------------|
| `scan` | Every N seconds (config: `heartbeat_interval_secs`) | `scheduled_scan` | No |
| `presession` | Daily configurable time | `presession_catalyst_pipeline` | No |
| `promoter_eod` | Daily 16:15 | `run_promoter` | **YES — duplicate of com.decifer.universe-promoter-eod.plist** |
| `promoter_premarket` | Daily 08:00 | `run_promoter` | **YES — duplicate of com.decifer.universe-promoter-preopen.plist** |
| `universe_refresh` | Sunday 23:00 | `refresh_committed_universe` | **YES — duplicate of com.decifer.universe-committed.plist** |
| iCloud sync | Every 5 min | `_run_icloud_sync` | No |

---

### A2. bot_trading.py — Scan Loop (called by bot.py)

| Field | Value |
|-------|-------|
| Path | `bot_trading.py` |
| Purpose | Orchestrates one scan cycle: signal scoring, Apex synthesizer (Track A + Track B), PM exits, options review |
| Trigger | Called by `scheduled_scan` inside bot.py every N seconds |
| Owner process | bot.py subprocess |
| Reads | Committed/promoted universe, position state (`orders_state.py`), regime detector, IC weights, handoff manifest (if enabled) |
| Writes | `event_log.py` fills/closes, `training_store.py` records, `data/apex_shadow_log.jsonl`, `data/apex_decision_audit.jsonl` |
| Downstream consumers | `orders_core.py` (execution), `event_log.py`, `training_store.py` |
| Runtime status | **production_runtime** |
| Evidence | Observed: `run_scan()` called by bot.py; `apex_orchestrator._run_apex_pipeline(execute=True)` at line 3073 |
| Confidence | **High** |
| Recommended action | None — protected file |

---

### A3. apex_orchestrator.py — Apex Synthesizer (Track A: live; Track B PM: shadow)

| Field | Value |
|-------|-------|
| Path | `apex_orchestrator.py` |
| Purpose | Calls `market_intelligence.apex_call()` (one Sonnet call), runs guardrails, dispatches or logs result |
| Trigger | Called by `bot_trading.py` per scan cycle |
| Owner process | bot_trading.py scan cycle |
| Reads | Candidates, regime, portfolio state, IC weights, overnight research |
| Writes | `data/apex_shadow_log.jsonl`, `data/apex_decision_audit.jsonl`, `data/apex_prompt_snapshot.jsonl`, `data/apex_response_snapshot.jsonl` |
| Runtime status | **production_runtime** (Track A: `execute=True`); **shadow_only** (Track B PM: `execute=False`) |
| Evidence | Observed: bot_trading.py line 3073 `execute=True` (Track A); line 2091 `execute=False` (Track B PM) |
| Confidence | **High** |
| ⚠️ Gap | Docstring says "This module does NOT submit orders" — **this is stale**. Track A submits orders via `execute=True` since Decifer 3.0 cutover. The module-level docstring has not been updated. |
| Recommended action | Update apex_orchestrator.py docstring to reflect live status (**non-trivial — requires Amit approval to touch protected file**) |

---

### A4. universe_committed.py — Weekly Universe Refresh

| Field | Value |
|-------|-------|
| Path | `universe_committed.py` |
| Purpose | Enumerates all tradable US equities, ranks by dollar volume, keeps top ~1000, writes `data/committed_universe.json` |
| Trigger | **Dual-scheduled (gap):** Sunday 23:00 via `ops/launchd/com.decifer.universe-committed.plist` AND internally by bot.py `schedule.every().sunday.at("23:00")` |
| Owner process | launchd (intended authority per memory file); bot.py internal scheduler (redundant) |
| Reads | Alpaca API (all assets), `config.py` |
| Writes | `data/committed_universe.json` (atomic via temp file), `data/heartbeats/universe_committed_worker.json`, `data/runtime/universe_worker_evidence.jsonl` |
| Downstream consumers | `scanner.py`, `universe_promoter.py`, `bot_trading.py` |
| Freshness SLA | Weekly; no staleness check in consumers (gap) |
| Runtime status | **scheduled_worker** |
| Evidence | Observed: plist at `ops/launchd/com.decifer.universe-committed.plist`; schedule call at bot.py line 627 |
| Confidence | **High** |
| ⚠️ Gap | Dual scheduling — launchd is intended sole authority per memory but bot.py internal schedule not yet removed (documented as "temp proof-window redundancy — checks 26+27 not yet passed") |

---

### A5. universe_promoter.py — Daily Promotion (Pre-open + EOD)

| Field | Value |
|-------|-------|
| Path | `universe_promoter.py` |
| Purpose | Scores top 50 from committed universe by gap + pm_vol_ratio + catalyst; writes `data/daily_promoted.json` |
| Trigger | **Dual-scheduled (gap):** Mon–Fri 08:00 and 16:15 via two launchd plists AND internally by bot.py lines 625–626 |
| Owner process | launchd (intended); bot.py internal scheduler (redundant) |
| Reads | `data/committed_universe.json`, Alpaca snapshots, catalyst engine |
| Writes | `data/daily_promoted.json`, `data/heartbeats/universe_promoter_worker.json`, `data/runtime/universe_worker_evidence.jsonl` |
| Freshness SLA | 18 hours max (enforced in `scanner.py` — graceful degradation to Tier A only if stale) |
| Runtime status | **scheduled_worker** |
| Evidence | Observed: two plists in `ops/launchd/`; bot.py lines 625–626 |
| Confidence | **High** |

---

### A6. handoff_publisher.py — Live Opportunity Manifest

| Field | Value |
|-------|-------|
| Path | `handoff_publisher.py` |
| Purpose | Publishes validated Intelligence-First shadow universe as a manifest the bot can consume |
| Trigger | `ops/launchd/com.decifer.handoff-publisher.plist` — every 10 minutes (`StartInterval=600`) |
| Owner process | launchd |
| Mode | `--mode controlled_activation` (from plist — Observed) |
| Reads | Intelligence-First shadow universe, validated candidates |
| Writes | `data/live/active_opportunity_universe.json`, `data/live/current_manifest.json`, `data/live/handoff_publisher_report.json`, `data/heartbeats/handoff_publisher.json`, `data/live/publisher_run_log.jsonl` |
| Manifest TTL | 15 minutes; stale manifest causes handoff_reader to fail closed |
| Downstream consumers | `bot_trading.py` (when `enable_active_opportunity_universe_handoff=True`) |
| Runtime status | **production_runtime** (both activation keys confirmed set) |
| Evidence | Observed: plist uses `--mode controlled_activation`; config.py line 985 `enable_active_opportunity_universe_handoff: True` (approved 2026-05-09) |
| Confidence | **High** |
| ⚠️ Note | Both activation keys are now set. Handoff is live. Bot consumes manifest each scan cycle. |

---

### A7. handoff_publisher_observer.py — Publisher Monitor

| Field | Value |
|-------|-------|
| Path | `handoff_publisher_observer.py` |
| Purpose | Monitors publisher health, heartbeat freshness, manifest validity |
| Trigger | Docker compose (`handoff-observer` service) or manual |
| Runtime status | **advisory_only** |
| Evidence | Observed: docker-compose.yml references service |
| Confidence | **Medium** (Docker not confirmed running in production) |

---

### A8. run_intelligence_pipeline.py — Intelligence File Regenerator

| Field | Value |
|-------|-------|
| Path | `run_intelligence_pipeline.py` |
| Purpose | Regenerates all 4 intelligence output files in sequence: economic state, theme activation, thesis store, candidate feed |
| Trigger | **Manual only** — no launchd plist, no cron, no bot.py call |
| Owner process | None — operator must run manually |
| Writes | `data/intelligence/daily_economic_state.json`, `data/intelligence/current_economic_context.json`, `data/intelligence/theme_activation.json`, `data/intelligence/thesis_store.json` |
| Downstream consumers | `intelligence_adapters.py`, `handoff_publisher.py`, `signal_dispatcher.py` |
| Runtime status | **manual_only** |
| Evidence | Observed: no import or call in bot.py, bot_trading.py, or any scheduler |
| Confidence | **High** |
| ⚠️ Critical Gap | Intelligence files powering the handoff pipeline have no automated refresh schedule. Stale intelligence = stale opportunity universe. See Gap Register section H. |

---

### A9. Docker Compose Stack

| Field | Value |
|-------|-------|
| Path | `docker-compose.yml` |
| Purpose | Container orchestration for handoff-publisher + handoff-observer; live-bot behind `--profile live` |
| Trigger | Manual `docker compose up -d handoff-publisher handoff-observer` |
| Services | `handoff-publisher` (restart on-failure:3), `handoff-observer` (read-only), `live-bot` (gated) |
| Runtime status | **unknown** — configured but not confirmed running in production on Amit's machine (launchd plists may be used instead) |
| Evidence | Observed: docker-compose.yml exists; cloud deployment docs reference it |
| Confidence | **Low** (runtime confirmation needed) |

---

### A10. Systemd Services (Linux Cloud — Not Yet Deployed)

| Field | Value |
|-------|-------|
| Path | `ops/systemd/` |
| Services | `decifer-xvfb.service`, `decifer-ibgateway.service`, `decifer-docker-stack.service` |
| Purpose | Cloud (DigitalOcean VM) infrastructure: Xvfb → IB Gateway → Docker stack |
| Runtime status | **validation_only** — not deployed; runbook exists at `docs/cloud_phase1_vm_deployment_runbook.md` |
| Evidence | Observed: files exist, install script at `scripts/cloud/install_systemd_services.sh`; docs confirm "not yet deployed" |
| Confidence | **High** (not deployed) |

---

## B. Data Artifact Register

### B1. Critical Trading Data

| Path | Producer | Consumer | Freshness SLA | Stale Detection | Missing Behaviour | Importance | Evidence |
|------|----------|----------|--------------|----------------|-----------------|-----------|---------|
| `data/trades.json` | `orders_state.py` / `bot_trading.py` | `bot_dashboard.py`, `learning.py`, `ml_engine.py` (offline) | Continuous | None | Dashboard shows empty history | **critical** | Observed: file 1.6MB, referenced throughout |
| `data/trade_events.jsonl` | `event_log.py` (fsync per write) | `bot_dashboard.py`, diagnostic scripts | Continuous | None | Dashboard loses today's closed trades | **critical** | Observed: file 523KB, fsync guaranteed |
| `data/training_records.jsonl` | `training_store.py` | `alpha_validation.py`, dashboards, diagnostic scripts | Per-close | None | Phase C gate count wrong | **critical** | Observed: 346KB, 349+ records migrated |
| `data/reconciled_trades.jsonl` | `ibkr_reconciler.py` | `bot_trading.py`, `overnight_research.py` | 60s cache | 60s TTL (Observed) | Live P&L validation degrades | **critical** | Observed: 86KB, cache constant in reconciler |
| `data/audit_log.jsonl` | `bot.py`, `learning.py`, `fill_watcher.py`, `bot_voice.py` | `learning.py`, diagnostic scripts | Continuous | None | IC analysis incomplete | **important** | Observed: 2.0MB |

### B2. Universe & Selection Data

| Path | Producer | Consumer | Freshness SLA | Stale Detection | Missing Behaviour | Importance | Evidence |
|------|----------|----------|--------------|----------------|-----------------|-----------|---------|
| `data/committed_universe.json` | `universe_committed.py` (weekly) | `scanner.py`, `universe_promoter.py` | Weekly | **None** | Scanner falls back to smaller universe | **critical** | Observed: 201KB; no freshness check in scanner |
| `data/daily_promoted.json` | `universe_promoter.py` (daily 2x) | `scanner.py` | 18 hours | **Yes** — scanner.py logs warning and falls back to Tier A only | **important** | Observed: 9.7KB; staleness gate confirmed |
| `data/position_research_universe.json` | `universe_position.py` | `signal_dispatcher.py`, `signal_pipeline.py` | 8 days (warning) | **Partial** — 8-day warning threshold; not fail-closed | **important** | Observed: 216KB; `position_research_max_staleness_days=8` in config |

### B3. Validation & IC Data

| Path | Producer | Consumer | Freshness SLA | Stale Detection | Missing Behaviour | Importance | Evidence |
|------|----------|----------|--------------|----------------|-----------------|-----------|---------|
| `data/ic_validation_result.json` | `ic_validator.py` (manual run) | `phase_gate.py` (bot startup) | State-based gate | **Partial** — gate reads state but no timestamp check | Gate fails open or closed depending on file state | **critical** | Observed: 324B; checked at bot startup |
| `data/ic_weights.json` | Offline IC analysis (manual) | `ic_validator.py` | Weekly (expected) | **None** | IC validator cannot compute weighted scores | **critical** | Observed: 1.2KB; no staleness enforcement |
| `data/live_ic_report.json` | `learning.py` | `bot_dashboard.py` | Per-review cycle | **None** | Dashboard shows stale IC numbers | **advisory** | Observed: 1.8KB; no check |

### B4. Intelligence Pipeline Data

| Path | Producer | Consumer | Freshness SLA | Stale Detection | Missing Behaviour | Importance | Evidence |
|------|----------|----------|--------------|----------------|-----------------|-----------|---------|
| `data/intelligence/daily_economic_state.json` | `run_intelligence_pipeline.py` (manual) | `intelligence_adapters.py`, `handoff_publisher.py` | Daily (expected) | **None** | Handoff publisher uses stale economic context | **important** | Observed: file exists; producer is manual-only |
| `data/intelligence/current_economic_context.json` | `run_intelligence_pipeline.py` (manual) | `intelligence_adapters.py` | Daily (expected) | **None** | Intelligence-First scoring uses stale context | **important** | Inferred: same producer |
| `data/intelligence/theme_activation.json` | `theme_activation_engine.py` (manual) | `signal_dispatcher.py`, `handoff_publisher.py` | Daily (expected) | **Partial** — `freshness_status` field exists but not enforced | Stale themes silently score | **important** | Observed: `freshness_status` field in theme_activation_engine.py |
| `data/intelligence/thesis_store.json` | `run_intelligence_pipeline.py` (manual) | `candidate_resolver.py`, `handoff_publisher.py` | Daily (expected) | **None** | Candidate rationales stale | **advisory** | Inferred |

### B5. Handoff Live Data

| Path | Producer | Consumer | Freshness SLA | Stale Detection | Missing Behaviour | Importance | Evidence |
|------|----------|----------|--------------|----------------|-----------------|-----------|---------|
| `data/live/active_opportunity_universe.json` | `handoff_publisher.py` (every 10 min) | `bot_trading.py` (scan cycle) | 15-minute TTL | **Yes** — `handoff_reader` fails closed on stale | Bot falls back to scanner-led mode | **critical** | Observed: plist + manifest TTL constant |
| `data/live/current_manifest.json` | `handoff_publisher.py` | `handoff_reader.py` | 15-minute TTL | **Yes** — TTL enforced at read time | Bot falls back to scanner-led mode | **critical** | Observed |
| `data/live/handoff_publisher_report.json` | `handoff_publisher.py` | Operator review | Per run | **None** | No visibility into publisher health | **advisory** | Observed |
| `data/heartbeats/handoff_publisher.json` | `handoff_publisher.py` | `handoff_publisher_observer.py` | Every 10 min | **Yes** — observer checks TTL | Observer alerts | **important** | Observed |
| `data/heartbeats/universe_committed_worker.json` | `universe_committed.py` | Manual review | Weekly | **None** — no automated check | Silent staleness | **advisory** | Observed |
| `data/heartbeats/universe_promoter_worker.json` | `universe_promoter.py` | Manual review | Daily | **None** — no automated check | Silent staleness | **advisory** | Observed |

### B6. Pending / Not Yet Created Artifacts

| Path | Producer | Consumer | Status | Impact if Assumed Present |
|------|----------|----------|--------|--------------------------|
| `data/tier_d_funnel.jsonl` | `signal_pipeline.py`, `signal_dispatcher.py` | `scripts/tier_d_evidence_report.py` | **Does not exist yet** — created on first scan cycle write | Phase 2 gate evidence cannot be generated yet |
| `data/rotation_observability/margin_blocks.jsonl` | `rotation_observability.py` | `scripts/rotation_shadow_report.py` | **Does not exist yet** — created on first margin block event | Rotation block analysis has no data |
| `data/rotation_observability/position_snapshots.jsonl` | `rotation_observability.py` | `scripts/rotation_shadow_report.py` | **Does not exist yet** | Same |
| `data/apex_shadow_log.jsonl` | `apex_orchestrator.py` | Operator review, divergence analysis | **Does not exist yet** — created on first Apex shadow run | Shadow audit trail missing |

### B7. Model Data

| Path | Producer | Consumer | Freshness SLA | Stale Detection | Missing Behaviour | Importance |
|------|----------|----------|--------------|----------------|-----------------|-----------|
| `data/models/classifier.pkl` | `ml_engine.py` (offline training) | `ml_engine.py` (inference) — **not called in production loop** | Unknown | **None** | N/A — not called | **advisory** |
| `data/models/regressor.pkl` | `ml_engine.py` (offline training) | Same | Unknown | **None** | N/A — not called | **advisory** |
| `data/models/metadata.json` | `ml_engine.py` | Same | Unknown | **None** | N/A — not called | **advisory** |

---

## C. Producer / Consumer Map

| Artifact | Producer | Consumer | Validation at Read | Stale Handling | Risk |
|----------|----------|----------|--------------------|---------------|------|
| `committed_universe.json` | `universe_committed.py` (weekly) | `scanner.py` | **None** | None — silently uses stale file | High: week-old universe used without warning |
| `daily_promoted.json` | `universe_promoter.py` (2x daily) | `scanner.py` | Yes — 18h gate | Falls back to Tier A only; logs warning | Low |
| `position_research_universe.json` | `universe_position.py` | `signal_dispatcher.py` | Partial — 8-day warning | Warning only, not fail-closed | Medium |
| `active_opportunity_universe.json` | `handoff_publisher.py` | `bot_trading.py` | Yes — 15-min TTL | Fail-closed: bot falls back to scanner | Low |
| `ic_weights.json` | Offline (manual) | `ic_validator.py` | **None** | Silent use of stale weights | High: IC weights may be weeks old |
| `ic_validation_result.json` | `ic_validator.py` (manual) | `phase_gate.py` | State-based | Gate reads file state; no timestamp | Medium |
| `intelligence/*.json` (4 files) | `run_intelligence_pipeline.py` (manual) | `intelligence_adapters.py`, `handoff_publisher.py` | **None** | Silent use of stale files | **High**: intelligence driving live handoff may be stale with no alert |
| `training_records.jsonl` | `training_store.py` | `alpha_validation.py`, reports | None | N/A — historical append | Low |
| `data/models/*.pkl` | `ml_engine.py` (offline) | ml_engine.py (not called in prod) | None | N/A — not consumed in prod | Low (isolated) |

---

## D. Orphan / Built-But-Not-Operational Register

### D1. ml_engine.py — ML Learning Loop

| Field | Value |
|-------|-------|
| Path | `ml_engine.py` |
| Intended purpose | Trade outcome labeling, pattern recognition, signal enhancement, regime classification |
| Caller evidence | `bot.py` line 808: `from ml_engine import enhance_score as _enhance_score  # noqa: F401 — import tests availability` — availability probe only |
| Schedule evidence | **None** |
| Production call evidence | **None** — `enhance_score`, `run_training_loop`, or any ml_engine function is not called in `bot_trading.py`, `scanner.py`, or `signals.py` |
| Runtime status | **advisory_only** — code exists, models exist in `data/models/`, but not wired into live scan cycle |
| Risk if assumed active | **High** — IC Phase 2 docs say "ML engine activation gate met (≥50 trades)" suggesting ML is ready; if assumed operational, alpha attribution would be wrong |
| Recommendation | **Keep** pending Amit approval; wire into scan cycle as a post-score enhancement step only after explicit approval |

### D2. HMM Regime Detector — Gated Stub

| Field | Value |
|-------|-------|
| Path | `bot_trading.py` lines 1466–1486; `signals.py` (`get_hmm_regime_spy` import attempted) |
| Intended purpose | Hidden Markov Model regime classification as VIX-proxy replacement |
| Implementation evidence | Config hook exists (`hmm_regime.enabled`); `bot_trading.py` checks config and attempts import from `signals.py` |
| Config gate | `config.py` line 635: `"hmm_regime": {"enabled": False}` — **gated off** |
| Roadmap status | `roadmap/03-hmm-regime-detection.md` — "Needs Validation"; gate met (≥200 trades + IC Phase 2) |
| Runtime status | **validation_only** — infrastructure ready but feature disabled |
| Risk if assumed active | **Medium** — regime conflict detection logic exists but VIX-proxy is the sole active detector |
| Recommendation | **Keep** pending Amit approval to enable; do not enable without Alphalens completion |

### D3. Walk-Forward Weight Calibration

| Field | Value |
|-------|-------|
| Path | `roadmap/06-weight-calibration.md` (spec only) |
| Intended purpose | Dynamic signal weights based on rolling IC per dimension |
| Implementation evidence | **None** — no Python implementation found |
| Dependencies | Blocked on HMM (roadmap spec: "Requires 03 (regime probs) + 05 (IC per dimension)") |
| Runtime status | **unknown** — documented spec, no code |
| Risk if assumed active | **Critical** — IC weights are currently static from `ic_weights.json` (manually updated) |
| Recommendation | **Keep as spec** — do not implement until HMM and Alphalens are complete |

### D4. Alphalens Signal Validation

| Field | Value |
|-------|-------|
| Path | `roadmap/05-signal-validation.md` (spec only); `alpha_validation.py` (partial — uses custom IC, not Alphalens) |
| Intended purpose | Full factor analysis using Alphalens-reloaded |
| Implementation evidence | `alpha_validation.py` exists but uses custom IC math, not Alphalens library; no `alphalens` import found |
| Runtime status | **advisory_only** — partial implementation without Alphalens library integration |
| Risk if assumed active | **High** — `data/alpha_validation_report.json` exists but may not reflect true Alphalens factor analysis |
| Recommendation | **Keep** pending Amit approval; gate met (≥200 trades) |

### D5. apex_orchestrator.py — Docstring vs Reality Mismatch

| Field | Value |
|-------|-------|
| Path | `apex_orchestrator.py` |
| Issue | Module docstring says "does NOT submit orders" — stale since Decifer 3.0 cutover (2026-04-24) |
| Actual status | Track A calls `_run_apex_pipeline(execute=True)` from `bot_trading.py` line 3073 — orders ARE submitted |
| Risk if assumed shadow-only | **Critical** — operator may assume no orders are submitted when reading code; discourages protective review |
| Recommendation | **Docstring update needed** — requires Amit approval as protected file touch |

### D6. run_intelligence_pipeline.py — Unscheduled Intelligence Refresh

| Field | Value |
|-------|-------|
| Path | `run_intelligence_pipeline.py` |
| Intended purpose | Regenerate all 4 intelligence files (economic state, context, theme activation, thesis store) |
| Caller evidence | **No production caller** |
| Schedule evidence | **No launchd plist, no cron, no bot.py call** |
| Runtime status | **manual_only** |
| Risk if assumed scheduled | **High** — intelligence files powering live handoff may be days old with no automated refresh |
| Recommendation | Add launchd plist for daily pre-market run (non-trading behaviour change) |

---

## E. Stale Report Register

| Report Path | Last Updated | Expected Cadence | Producer | Producer Scheduled | Stale State Visible | Recommended Fix |
|-------------|-------------|-----------------|----------|-------------------|--------------------|----|
| `data/live_ic_report.json` | Unknown | Per review cycle | `learning.py` | No (embedded in bot runtime) | No | Add timestamp field and dashboard freshness indicator |
| `data/alpha_validation_report.json` | Unknown | After each ≥10-trade batch | `alpha_validation.py` | No (manual) | No | Add scheduled daily run |
| `data/intelligence/daily_economic_state.json` | Unknown | Daily | `run_intelligence_pipeline.py` | **No** | No | Add launchd plist |
| `data/intelligence/current_economic_context.json` | Unknown | Daily | `run_intelligence_pipeline.py` | **No** | No | Add launchd plist |
| `data/intelligence/theme_activation.json` | Unknown | Daily | `theme_activation_engine.py` | No | Partial (`freshness_status` field) | Enforce freshness at read time |
| `data/intelligence/thesis_store.json` | Unknown | Daily | `run_intelligence_pipeline.py` | **No** | No | Add launchd plist |
| `data/ic_weights.json` | Unknown | Weekly | Offline manual | No | No | Add produced_at timestamp; add staleness gate in ic_validator.py |
| `data/heartbeats/universe_committed_worker.json` | Unknown | Weekly | `universe_committed.py` | Yes (launchd) | No automated consumer | Add to operator checklist |
| `docs/rotation_shadow_report_2026_05_12.md` | 2026-05-12 | Per incident | `scripts/rotation_shadow_report.py` | No | N/A — one-time diagnostic | No action needed |

---

## F. Scheduled Job Register

### F1. macOS launchd Plists

| Plist | Schedule | Command | Working Dir | Log Paths | Output Artifacts | Failure Visibility |
|-------|----------|---------|------------|----------|-----------------|-------------------|
| `ops/launchd/com.decifer.universe-committed.plist` | Sunday 23:00 | `python3.11 universe_committed.py --run-once` | Repo root | `/tmp/decifer-universe-committed.log` | `data/committed_universe.json`, heartbeat | Low — check heartbeat manually |
| `ops/launchd/com.decifer.universe-promoter-preopen.plist` | Mon–Fri 08:00 | `python3.11 universe_promoter.py --run-once` | Repo root | `/tmp/decifer-universe-promoter-preopen.log` | `data/daily_promoted.json`, heartbeat | Low |
| `ops/launchd/com.decifer.universe-promoter-eod.plist` | Mon–Fri 16:15 | `python3.11 universe_promoter.py --run-once` | Repo root | `/tmp/decifer-universe-promoter-eod.log` | `data/daily_promoted.json`, heartbeat | Low |
| `ops/launchd/com.decifer.handoff-publisher.plist` | Every 10 min | `python3.11 handoff_publisher.py --mode controlled_activation` | Repo root | `/tmp/decifer-handoff-publisher.log` | Manifest, heartbeat | Medium — observer monitors heartbeat |
| `scripts/com.decifer.auto-push.plist` | Every 2 min | `bash auto-push.sh` | Repo root | `/tmp/decifer-auto-push.log` | GitHub remote | Low |
| `scripts/com.decifer.icloud-sync.plist` | Every 5 min | `bash scripts/icloud-sync.sh` | Repo root | stdout | iCloud Drive backup | Low |

### F2. Internal bot.py Schedule Library Jobs

| Tag | Schedule | Function | Status |
|-----|----------|----------|--------|
| `scan` | Every N seconds | `scheduled_scan → run_scan()` | production_runtime |
| `presession` | Daily configurable | `presession_catalyst_pipeline` | production_runtime |
| `promoter_eod` | Daily 16:15 | `run_promoter` | **DUPLICATE of launchd plist** |
| `promoter_premarket` | Daily 08:00 | `run_promoter` | **DUPLICATE of launchd plist** |
| `universe_refresh` | Sunday 23:00 | `refresh_committed_universe` | **DUPLICATE of launchd plist** |
| (none) | Every 5 min | `_run_icloud_sync` | redundant with iCloud launchd plist |

---

## G. Trading Behaviour Protection Register

The following files must not be modified without explicit Amit approval and architecture review. Changes to these files affect live order submission, risk enforcement, or broker state.

| File | Layer | Why Protected |
|------|-------|--------------|
| `bot.py` | live_trading | Main orchestrator; scan loop scheduling; IBKR/Alpaca stream wiring |
| `bot_trading.py` | live_trading | Scan cycle logic; Apex dispatch; entry/exit decisions; PM actions |
| `bot_ibkr.py` | broker | IBKR order status callbacks; fill watchers; position reconciliation |
| `bot_dashboard.py` | reporting | Dashboard server; reads live state; naming-sensitive (see CLAUDE.md) |
| `orders_core.py` | execution_risk | Order placement (`execute_buy`, `execute_sell`) |
| `orders_state.py` | execution_risk | Shared mutable position state; immutability guard (`_safe_set_trade`) |
| `orders_portfolio.py` | execution_risk | Portfolio-level exit logic; position close records |
| `market_intelligence.py` | market_intelligence | `apex_call()` — the Sonnet synthesizer producing entry/exit decisions |
| `apex_orchestrator.py` | live_trading | Apex pipeline runner; live dispatch for Track A (`execute=True`) |
| `event_log.py` | observability | Write-ahead log; crash-safety; ORDER_INTENT before order submission |
| `training_store.py` | observability | ML training record persistence; immutable trade metadata |
| `learning.py` | validation | IC computation; audit events; capital base tracking |
| `config.py` | live_trading | All thresholds; live vs paper values; scanner parameters |
| `signals.py` | live_trading | Signal dimension scoring; ADF gate; regime detection |
| `scanner.py` | live_trading | Universe scoring; candidate selection; tier routing |
| All `*ibkr*.py`, `alpaca_*.py`, `fmp_client.py` files | broker | Broker API integration; order and data endpoints |

---

## H. Architecture Gap Register

### H1 — Critical

| # | Description | Evidence | Impact | Recommended Sprint | Code Change Required |
|---|-------------|----------|--------|-------------------|---------------------|
| C1 | **Intelligence pipeline has no scheduler** — `run_intelligence_pipeline.py` has zero production callers; intelligence files powering live handoff have no automated refresh | Observed: no caller in bot.py, bot_trading.py, or launchd | Live handoff operates on potentially stale intelligence context; economic state and themes may not reflect current market conditions | Sprint: Intelligence Scheduler — add launchd plist for daily 06:00 pre-market run | No (plist only) |
| C2 | **apex_orchestrator.py docstring is stale** — claims "does NOT submit orders" but Track A calls `execute=True` | Observed: bot_trading.py line 3073 | Operators reading code may assume Apex is shadow-only; discourages protective review of live order path | Docstring update — requires Amit approval for protected file | Yes (docstring only) |
| C3 | **tier_d_funnel.jsonl does not exist** — Phase 2 evidence gate cannot be evaluated until data accumulates from live scan cycles | Observed: `ls` confirms file absent | `scripts/tier_d_evidence_report.py` cannot generate Section 0b (Apex Cap Analysis); Phase 2 gate cannot be formally assessed | Run enough live scan cycles to accumulate data, then run evidence report | No |
| C4 | **committed_universe.json has no freshness check in consumer** — scanner uses whatever file exists, regardless of age | Observed: no staleness check in scanner.py for this file | If universe worker fails silently for >7 days, bot trades a stale universe with no warning | Add `committed_universe_max_staleness_days` gate in scanner.py | Yes |

### H2 — High

| # | Description | Evidence | Impact | Recommended Sprint | Code Change Required |
|---|-------------|----------|--------|-------------------|---------------------|
| H1 | **Dual scheduling: launchd + bot.py internal schedule for universe jobs** — three jobs scheduled in both systems simultaneously | Observed: bot.py lines 625–627 AND three launchd plists | Race condition if both fire at the same time; double writes to committed_universe.json and daily_promoted.json; non-deterministic behavior | Remove bot.py internal schedule tags `promoter_eod`, `promoter_premarket`, `universe_refresh` once launchd is confirmed sole authority | Yes |
| H2 | **ic_weights.json has no staleness gate** — IC weights used by ic_validator.py are produced by offline manual process with no timestamp enforcement | Observed: no produced_at field or check in ic_validator.py | Apex receives weights from a potentially outdated IC run; affects signal weighting quality | Add `produced_at` timestamp to ic_weights.json; add staleness check (>14 days → warn) in ic_validator.py | Yes |
| H3 | **intelligence/*.json freshness not enforced at handoff_publisher** — four files (economic state, context, themes, thesis) have no freshness gate before being published | Observed: `freshness_status` field in theme_activation but not enforced; no timestamp check in handoff_publisher for other files | Stale intelligence silently flows into live opportunity universe | Add freshness check in handoff_publisher.py: reject intelligence files older than `intelligence_max_staleness_hours` | Yes |
| H4 | **apex_shadow_log.jsonl does not exist** — divergence audit trail has no data until first shadow run | Observed: `ls` confirms file absent | No offline comparison of legacy vs Apex decisions available | File will be created automatically on first run; ensure bot has been run in shadow mode | No |
| H5 | **ml_engine.py not called in production loop** — gate met (≥50 trades) but wiring pending Amit approval | Observed: import probe only in bot.py; no call in bot_trading.py or scanner.py | False confidence that ML enhancement is active; alpha attribution assumes raw signal scores | Explicit Amit decision required: wire or formally defer | Requires Amit decision |

### H3 — Medium

| # | Description | Evidence | Impact | Recommended Sprint | Code Change Required |
|---|-------------|----------|--------|-------------------|---------------------|
| M1 | **universe heartbeats not monitored by automated consumer** — heartbeat files exist but no process checks their freshness | Observed: heartbeat files exist; no consumer in bot.py or any scheduler | Silent universe worker failure goes undetected until manual check | Add heartbeat check to bot.py startup or operator checklist | Partial |
| M2 | **alpha_validation_report.json is not Alphalens** — `alpha_validation.py` uses custom IC math; Alphalens library not integrated | Observed: no `alphalens` import in codebase | Report may be presented as Alphalens analysis when it is custom code | Add explicit disclaimer to report; rename function to clarify scope | Yes (documentation) |
| M3 | **rotation_observability artifacts not created yet** — feature code exists (`rotation_observability.py`) but no data until margin block events occur | Observed: `ls` confirms directory absent | `rotation_shadow_report.py` produces empty report; rotation analysis cannot be done | Normal — accumulates with live trading; no action needed | No |
| M4 | **Docker stack runtime status unconfirmed** — docker-compose.yml configured but not confirmed running in production | Unverified | If launchd plists supersede Docker, Docker may not be running | Confirm with Amit which runtime is active | No |

### H4 — Low

| # | Description | Evidence | Impact | Recommended Sprint | Code Change Required |
|---|-------------|----------|--------|-------------------|---------------------|
| L1 | **live_ic_report.json has no timestamp field** — dashboard shows IC numbers with no freshness indicator | Observed: no timestamp in file | Operator cannot tell if displayed IC is from today or last week | Add `generated_at` timestamp to live_ic_report.json | Yes |
| L2 | **docs/ contains 70+ files** — some may be superseded by newer versions | Observed: docs listing shows overlapping cloud readiness, intelligence activation, retirement registers | Risk of consulting stale docs | Index or archive superseded docs | No (docs only) |
| L3 | **`auto-push.sh` pushes every 2 minutes** — branches with work-in-progress may be pushed | Observed: plist config | Unreviewed commits may reach GitHub | No action needed given paper-only mode; review before cloud deployment | No |
