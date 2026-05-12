# Production Runtime Map
**Sprint:** Architecture Audit — documentation only  
**Branch:** audit/architecture-control-register  
**Date:** 2026-05-12  
**Status:** Authoritative as of audit date

> Confidence levels: **High** = directly observed in code/config. **Medium** = inferred from multiple sources. **Low** = unverified or runtime-dependent.

---

## 1. Live Bot Runtime

**What actually runs:** `bot.py` (always-on process, manual start)

**Trigger:** Manual `python3 bot.py` on Amit's machine. No restart-on-failure launchd plist for bot.py itself.

**Reads on startup:**
- `.env` (API keys)
- `config.py` (all thresholds)
- `data/committed_universe.json` (Tier B universe)
- `data/daily_promoted.json` (Tier A promoted — 18h staleness gate enforced)
- `data/ic_weights.json` (signal weights — no staleness gate)
- `data/trades.json` (position history)
- `data/ic_validation_result.json` (phase gate check)
- `data/live/current_manifest.json` (handoff manifest, if `enable_active_opportunity_universe_handoff=True`)

**Writes on each scan cycle:**
- `data/trade_events.jsonl` (via `event_log.py`, fsync per write)
- `data/training_records.jsonl` (via `training_store.py`, on position close)
- `data/audit_log.jsonl` (all scan events, voice commands, fills)
- `data/apex_shadow_log.jsonl` (created on first run — does not exist yet)
- `data/apex_decision_audit.jsonl` (created on first run — does not exist yet)
- Dashboard in-memory state (served at localhost:8080)

**Freshness verified by:** Daily promoter 18h gate (enforced). Committed universe: **no gate** (gap). IC weights: **no gate** (gap). Intelligence files: **no gate** (gap).

**What is not yet production-grade:**
- No restart-on-failure mechanism for bot.py itself
- No automated alert if bot.py crashes
- IBKR connection auto-reconnect exists but cloud restart not configured

**Confidence: High**

---

## 2. Intelligence Pipeline Runtime

**What actually runs:** Nothing automated.

**Trigger:** Manual operator run of `run_intelligence_pipeline.py` and/or `theme_activation_engine.py`.

**Reads:** Alpaca API, FMP API, Alpha Vantage, `data/intelligence/` existing files, `data/reference/`.

**Writes:**
- `data/intelligence/daily_economic_state.json`
- `data/intelligence/current_economic_context.json`
- `data/intelligence/theme_activation.json`
- `data/intelligence/thesis_store.json`

**Freshness verified by:** `theme_activation_engine.py` writes a `freshness_status` field — but it is not enforced at read time by `handoff_publisher.py` or `intelligence_adapters.py`. The three other intelligence files have no freshness field or check.

**What is not yet production-grade:**
- **No scheduler.** Intelligence pipeline has no launchd plist, no cron, no bot.py call.
- No staleness gate at consumption point.
- If operator forgets to run, the handoff publisher silently uses day-old (or older) intelligence data.

**Confidence: High** (gap confirmed via code search)

---

## 3. Universe Publication Runtime

**What actually runs:** Two parallel scheduling authorities — **both are active**.

### 3a. Committed Universe (Tier B)
- **launchd plist:** `com.decifer.universe-committed.plist` — Sunday 23:00
- **bot.py internal:** `schedule.every().sunday.at("23:00").do(refresh_committed_universe)` — same time
- **Reads:** Alpaca API (all assets)
- **Writes:** `data/committed_universe.json` (atomic), `data/heartbeats/universe_committed_worker.json`
- **Freshness verified by consumer:** **None** — scanner uses whatever file exists

### 3b. Daily Promoter (Tier A)
- **launchd plists:** `com.decifer.universe-promoter-preopen.plist` (08:00) and `com.decifer.universe-promoter-eod.plist` (16:15)
- **bot.py internal:** `schedule.every().day.at("08:00")` and `schedule.every().day.at("16:15")` — same times
- **Reads:** `data/committed_universe.json`, Alpaca snapshots, catalyst engine
- **Writes:** `data/daily_promoted.json`, `data/heartbeats/universe_promoter_worker.json`
- **Freshness verified by consumer:** Yes — scanner enforces 18h gate; graceful fallback to Tier A only

**What is not yet production-grade:**
- **Dual scheduling creates a race condition** when both launchd and bot.py fire simultaneously. Memory file documents this as "temp proof-window redundancy" with launchd as intended sole authority after checks 26+27 — but the internal schedule tags have not been removed.
- Heartbeat files are written but not automatically monitored.

**Confidence: High** (both schedules confirmed in code and plists)

---

## 4. Handoff Runtime

**What actually runs:** `handoff_publisher.py` every 10 minutes via launchd plist.

**Mode:** `--mode controlled_activation` (Observed from plist)

**Activation status:** LIVE — both keys set:
1. `config.py`: `enable_active_opportunity_universe_handoff: True` (approved 2026-05-09)
2. Manifest: `handoff_enabled=true` (set by `controlled_activation` mode)

**Reads:**
- Intelligence-First shadow universe (reads `data/intelligence/` files — no freshness gate)
- Validated candidates

**Writes (atomic — .tmp → os.replace()):**
- `data/live/active_opportunity_universe.json`
- `data/live/current_manifest.json` (TTL: 15 minutes)
- `data/live/handoff_publisher_report.json`
- `data/heartbeats/handoff_publisher.json`
- `data/live/publisher_run_log.jsonl`

**Consumption by bot:**
- `bot_trading.py` checks handoff manifest each scan cycle
- `handoff_reader.py` validates manifest TTL; fails closed on stale or disabled manifest
- Bot falls back to scanner-led mode if manifest is expired or `handoff_enabled=false`

**Freshness verified:** Yes — manifest TTL enforced at read time. Fail-closed if expired.

**What is not yet production-grade:**
- Intelligence files feeding the publisher have no freshness gate — publisher may propagate stale economic context.
- `handoff_publisher_observer.py` health monitoring depends on Docker being active (unconfirmed).

**Confidence: High**

---

## 5. Apex Synthesizer Runtime (Track A: Live; Track B PM: Shadow)

**What actually runs:**  
`apex_orchestrator._run_apex_pipeline()` is called per scan cycle from `bot_trading.py`.

**Track A — New entries (LIVE):**
- Called with `execute=True` at `bot_trading.py` line 3073
- Calls `market_intelligence.apex_call()` (one `claude-sonnet-4-6` call)
- Runs guardrails; dispatches entries if approved
- Writes to `data/apex_decision_audit.jsonl`

**Track B PM — TRIM/EXIT/HOLD (SHADOW):**
- Called with `execute=False` at `bot_trading.py` line 2091
- Logs result via `log_shadow_result("TRACK_B_PM", ...)` to `data/apex_shadow_log.jsonl`
- Does NOT submit orders

**⚠️ Docstring mismatch:** `apex_orchestrator.py` module docstring states "does NOT submit orders" — this is **stale** since the Decifer 3.0 cutover (2026-04-24). Track A is live.

**Freshness:** Apex receives fresh scan-cycle data each call. IC weights passed at call time from `ic_weights.json` (no freshness gate on weights).

**Confidence: High**

---

## 6. Advisory Runtime

**What actually runs:** `advisory_logger.py` embedded in bot runtime (records all trade decisions and outcomes).

**Reads:** Trade decisions, fill events, position state.

**Writes:** Advisory log (path from config).

**Manual analysis tools (not scheduled):**
- `advisory_reporter.py` — generates trade advisory reports (manual run)
- `advisory_log_reviewer.py` — analyzes advisory logs (manual run)

**Freshness verified:** `advisory_logger.py` has `_ADVISORY_MAX_AGE_SECONDS` constant — Observed.

**Confidence: Medium** (advisory logger embedded in runtime; reporters are manual)

---

## 7. Reporting Runtime

**What actually runs:** No automated reporting pipeline. All reports are manual.

**Manual report scripts (not scheduled):**

| Script | Output | When Run |
|--------|--------|----------|
| `scripts/tier_d_evidence_report.py` | Evidence report for PRU phase gate | Manual |
| `scripts/trade_quality_report.py` | Trade quality diagnostics | Manual |
| `scripts/rotation_shadow_report.py` | Exposure block reconstruction | Manual |
| `alpha_validation.py` | `data/alpha_validation_report.json` | Manual |
| `scripts/phase1_session_report.py` | PRU Phase 1 session summary | Manual |

**What is not yet production-grade:**
- All reporting is manual — no automated daily summary
- `data/trade_quality_reports/` contains timestamped snapshots (last observed: 2026-05-12T03:08Z) — Observed

**Confidence: High** (confirmed no schedulers for any report script)

---

## 8. IC / Learning Runtime

**What actually runs:** IC computation embedded in bot runtime via `learning.py`.

**Trigger:** Called during scan cycles and position closes.

**Writes:** `data/live_ic_report.json` (no timestamp field — gap), `data/audit_log.jsonl`.

**ic_weights.json (critical artifact):**
- Produced by: offline manual IC analysis
- Consumed by: `ic_validator.py`, `apex_call()` (weights passed at call time)
- **No automated refresh.** Expected weekly — but no enforcement.
- **No staleness gate** at consumption.

**ic_validation_result.json:**
- Produced by: `ic_validator.py` (manual run)
- Consumed by: `phase_gate.py` at bot startup
- Current state: gates passed (sample: 60 valid records, IC: 0.1728, Sharpe: 6.69)

**What is not yet production-grade:**
- `ic_weights.json` is not auto-refreshed; if stale, Apex receives outdated signal weights silently.
- `live_ic_report.json` has no `generated_at` field; dashboard freshness is invisible.

**Confidence: High**

---

## 9. Broker / Account Runtime

**What actually runs:** `bot_ibkr.py` — IBKR TWS connection handlers, order status callbacks, fill watchers, position reconciliation.

**Trigger:** Started by `bot.py` on initialization.

**IBKR Paper account:** DUP481326 (paper only).

**Reads:** IBKR TWS socket (real-time order status, fills, position data).

**Writes:** Position fills → `event_log.py` → `training_store.py` on close.

**Reconciliation:** `ibkr_reconciler.py` — 60s cache TTL; reconciles IBKR state with local `orders_state.py`.

**Alpaca (data-only):** Alpaca MCP tools and `alpaca_*.py` modules are data-only — never used for order placement (IBKR is the execution broker).

**Freshness verified:** 60s reconciliation cache (Observed). Price anchoring: `price_updater.py` has `_IBKR_ANCHOR_MAX_AGE = 300s`.

**Cloud note:** Cloud deployment requires IB Gateway via systemd + Docker. Not yet deployed.

**Confidence: High**

---

## 10. Risk / Exposure Runtime

**What actually runs:** Risk gates embedded in `bot_trading.py`, `orders_core.py`, and `orders_state.py`. All deterministic — no LLM involvement.

**Active risk controls:**
- EOD flat: deterministic forced close before market close
- 90-min INTRADAY timeout: deterministic
- Stop-loss / trailing stop: deterministic
- Architecture violations: deterministic
- Regime-change sells (`check_thesis_validity()`): deterministic, calls `_apex_mode_sells` directly
- Bracket order cancellation before missed-stop close: Observed in recent commit (`c0c4fec`)

**Rotation / exposure control:**
- `rotation_observability.py` — writes margin block events to `data/rotation_observability/` (directory does not exist yet — created on first event)
- `hold_protected` flag: added in recent commit (`1360e79`) — prevents rotation from closing protected holds

**What is not yet production-grade:**
- `data/rotation_observability/` files do not yet exist — no block-time reconstruction available
- `scripts/rotation_shadow_report.py` will produce empty output until data accumulates

**Confidence: High** (risk controls observed in code; observability files pending first event)

---

## 11. Data / Logging Runtime

**What actually runs:** Three write-ahead logs maintained continuously.

| System | File | Write method | Crash-safe |
|--------|------|-------------|-----------|
| `event_log.py` | `data/trade_events.jsonl` | Append + fsync per write | Yes |
| `training_store.py` | `data/training_records.jsonl` | Append + required-field validation | Yes |
| `audit_log.py` / `learning.py` | `data/audit_log.jsonl` | Append | Partial (no fsync) |

**Largest files (as of audit):**
- `data/signals_log.jsonl` — 18MB (rotation at configurable byte threshold)
- `data/audit_log.jsonl` — 2.0MB
- `data/trades.json` — 1.6MB

**Rotation:** `utils/log_rotation.py` handles JSONL rotation for apex logs. Signal log has independent rotation constant.

**Confidence: High**

---

## Runtime Summary Table

| Layer | Runtime Status | Automated | Freshness Enforced | Primary Gap |
|-------|---------------|-----------|-------------------|------------|
| Live bot | production_runtime | Yes (manual start) | Partial | No restart-on-failure |
| Intelligence pipeline | manual_only | **No** | **No** | No scheduler |
| Universe — committed | scheduled_worker | Yes (launchd + bot duplicate) | **No** at consumer | Dual schedule, no staleness gate |
| Universe — promoter | scheduled_worker | Yes (launchd + bot duplicate) | Yes (18h gate) | Dual schedule |
| Handoff | production_runtime | Yes (launchd) | Yes (15-min TTL) | Intelligence inputs not gated |
| Apex Track A | production_runtime | Yes (per scan cycle) | Per cycle | Stale docstring |
| Apex Track B PM | shadow_only | Yes (per scan cycle) | Per cycle | None |
| Advisory | production_runtime | Yes (embedded) | Partial | Reporters are manual |
| Reporting | manual_only | **No** | N/A | All reports manual |
| IC / Learning | production_runtime | Yes (embedded) | Partial | ic_weights.json not gated |
| Broker / Account | production_runtime | Yes (IBKR stream) | Yes (60s recon) | Cloud not deployed |
| Risk / Exposure | production_runtime | Yes (deterministic) | Yes | Observability files pending |
| Data / Logging | production_runtime | Yes (continuous) | Yes (event_log fsync) | audit_log lacks fsync |
