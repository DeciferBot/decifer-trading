# Architecture Audit Report — Executive Summary
**Sprint:** Architecture Audit — documentation only  
**Branch:** audit/architecture-control-register  
**Date:** 2026-05-12  
**Auditor:** Production Architecture Auditor (Claude Code)

---

## SECTION 1: Executive Verdict

**Classification: Mostly controlled with gaps**

The Decifer system has a well-structured core runtime (live bot, Apex synthesizer, handoff pipeline, universe workers) with clear ownership and operational evidence. The system is paper-trading safe and generates reliable training data. However, three categories of gaps prevent a "production clear" verdict:

1. **Intelligence pipeline is unscheduled** — the inputs driving the live handoff have no automated refresh. Stale intelligence flows into live scoring silently.
2. **Dual scheduling conflict** — launchd plists and bot.py internal schedule library fire the same universe jobs simultaneously. Intended sole authority (launchd) has not been activated yet.
3. **Several high-confidence code paths have stale docstrings or no freshness gates** — most critically, `apex_orchestrator.py` claims to be shadow-only while Track A is live production.

The system is **not yet ready for cloud deployment** without resolving the intelligence scheduler gap and dual scheduling conflict. No trading behaviour change is required to fix these — they are operational/scheduling gaps only.

---

## SECTION 2: Top 10 Findings

Ranked by production risk.

| Rank | Finding | Severity | Evidence Basis | Code Change? |
|------|---------|----------|---------------|-------------|
| 1 | **Intelligence pipeline has no scheduler** — `run_intelligence_pipeline.py` has zero production callers; live handoff operates on manually-refreshed intelligence | Critical | Observed | No (plist only) |
| 2 | **apex_orchestrator.py docstring is stale** — claims "does NOT submit orders" but Track A is live with `execute=True` since Decifer 3.0 cutover | Critical | Observed | Yes (docstring — protected file) |
| 3 | **Dual scheduling: launchd + bot.py schedule library** — universe refresh and promoter jobs fire from both systems simultaneously | High | Observed | Yes (remove internal tags from bot.py) |
| 4 | **committed_universe.json has no freshness gate in consumer** — scanner uses file regardless of age; silent failure if weekly worker fails | High | Observed | Yes |
| 5 | **ic_weights.json has no staleness gate** — Apex receives potentially weeks-old signal weights with no warning | High | Observed | Yes |
| 6 | **Intelligence files have no freshness gate at consumption** — `handoff_publisher.py` publishes stale economic state/themes without validation | High | Observed | Yes |
| 7 | **ml_engine.py is not called in production loop** — gate met (≥50 trades) but wiring pending approval; risk of false confidence that ML enhancement is active | High | Observed | Requires Amit decision |
| 8 | **tier_d_funnel.jsonl does not exist** — Phase 2 evidence gate cannot be evaluated; `tier_d_evidence_report.py` produces empty output | High | Observed (`ls` confirms absent) | No (accumulates with live trading) |
| 9 | **apex_shadow_log.jsonl does not exist** — divergence audit trail between legacy and Apex decisions unavailable until first shadow run | Medium | Observed (`ls` confirms absent) | No |
| 10 | **universe heartbeats not monitored by automated consumer** — heartbeat files written but never read by any scheduled process | Medium | Observed | Partial |

---

## SECTION 3: Built But Not Operational Findings

The following capabilities exist in code but do not run in the live production cycle.

| Capability | Location | Status | Why Not Operational | Evidence | Risk |
|-----------|---------|--------|--------------------|---------|----|
| **ml_engine.py — ML inference** | `ml_engine.py` | advisory_only | No production caller; gated behind availability probe only (`# noqa: F401` import in bot.py line 808) | Observed: no call in bot_trading.py, scanner.py, or signals.py | High — gate met; assumed active without verification |
| **HMM regime detection** | `bot_trading.py` lines 1466–1486; config gate | validation_only | `hmm_regime.enabled: False` in config; infrastructure exists but feature off | Observed: config.py line 635 | Medium — VIX-proxy is sole active regime detector; HMM conflict logic ready but unused |
| **Walk-forward weight calibration** | `roadmap/06-weight-calibration.md` | unknown — spec only | No Python implementation; blocked on HMM + Alphalens | Observed: spec only | Medium — static ic_weights.json is authoritative; calibration not running |
| **Alphalens signal validation** | `roadmap/05-signal-validation.md`; `alpha_validation.py` (partial) | advisory_only | Alphalens library not integrated; `alpha_validation.py` uses custom IC math | Observed: no `alphalens` import in codebase | Medium — `data/alpha_validation_report.json` may be misread as Alphalens output |
| **Track B PM Apex execution** | `apex_orchestrator.py`; `bot_trading.py` line 2091 | shadow_only | Called with `execute=False`; logs result but does not submit PM actions via Apex | Observed | Low — intentional shadow per architecture |
| **run_intelligence_pipeline.py** | `run_intelligence_pipeline.py` | manual_only | No scheduler, no production caller | Observed: no import or call in any scheduler | Critical — this powers the live handoff |
| **handoff_publisher_observer.py monitoring** | `handoff_publisher_observer.py` | unknown | Depends on Docker compose being active; not confirmed running | Unverified | Medium |
| **Rotation observability data** | `data/rotation_observability/` | pending_first_event | Directory and files do not exist yet; will be created on first margin block | Observed: `ls` confirms absent | Low — accumulates organically |

---

## SECTION 4: Stale or Untrusted Outputs

| Output | Producer | Scheduled? | Last Verified | Freshness Check at Consumer | Risk |
|--------|---------|-----------|--------------|---------------------------|------|
| `data/intelligence/daily_economic_state.json` | `run_intelligence_pipeline.py` (manual) | **No** | Unknown | **None** | High — handoff publisher uses this directly |
| `data/intelligence/current_economic_context.json` | Same | **No** | Unknown | **None** | High |
| `data/intelligence/theme_activation.json` | `theme_activation_engine.py` (manual) | **No** | Unknown | Partial (`freshness_status` field not enforced) | High |
| `data/intelligence/thesis_store.json` | `run_intelligence_pipeline.py` (manual) | **No** | Unknown | **None** | Medium |
| `data/ic_weights.json` | Offline manual | **No** | Unknown | **None** | High — Apex receives stale weights silently |
| `data/live_ic_report.json` | `learning.py` (embedded in bot) | Yes (bot runtime) | Unknown — no timestamp field | **None visible** | Medium — dashboard shows IC with no freshness indicator |
| `data/alpha_validation_report.json` | `alpha_validation.py` (manual) | **No** | 2026-05-12 (from trade_quality_reports timestamps) | **None** | Medium |
| `data/reference/symbol_master.json` | Unknown producer | Unknown | Unknown | **None** | Medium |
| `data/reference/theme_overlay_map.json` | Unknown producer | Unknown | Unknown | **None** | Medium |
| `data/heartbeats/universe_committed_worker.json` | `universe_committed.py` | Yes (launchd) | Depends on last Sunday | No automated reader | Low |
| `data/heartbeats/universe_promoter_worker.json` | `universe_promoter.py` | Yes (launchd) | Depends on last run | No automated reader | Low |

---

## SECTION 5: Runtime Ownership Gaps

| Component | Gap | Impact | Evidence |
|-----------|-----|--------|---------|
| Intelligence pipeline | No owner process; no scheduler; manual only | Stale intelligence in live handoff | Observed |
| ic_weights.json | No scheduled producer; no consumer gate | Apex receives potentially outdated weights | Observed |
| universe heartbeats | Written but not read by any automated consumer | Silent worker failure goes undetected | Observed |
| symbol_master.json | Producer unknown; update cadence unknown | Unknown data quality over time | Unverified |
| theme_overlay_map.json | Producer unknown; update cadence unknown | Stale theme mappings affect signal classification | Unverified |
| alpha_validation_report.json | Manual producer; no schedule; not labeled as non-Alphalens | Could be misread as Alphalens factor analysis | Observed |
| Docker stack (handoff-observer) | Not confirmed running; launchd plists may supersede Docker | Publisher health monitoring may be inactive | Unverified |
| bot.py restart | No restart-on-failure mechanism | Bot crash requires manual restart | Inferred |

---

## SECTION 6: Fail-Closed Gaps

Places where stale or missing data should fail closed but may warn, fallback, or silently continue.

| Component | Data Input | Current Behavior if Stale/Missing | Should Be | Gap |
|-----------|-----------|----------------------------------|-----------|-----|
| `scanner.py` consuming `committed_universe.json` | Committed universe | **Silent continue** — uses whatever file exists, no age check | Should warn if >8 days old | Yes |
| `handoff_publisher.py` consuming intelligence files | 4 intelligence JSON files | **Silent publish** — no freshness gate before publishing | Should reject files older than N hours | Yes |
| `ic_validator.py` consuming `ic_weights.json` | IC weights | **Silent use** — no staleness check | Should warn if weights older than 14 days | Yes |
| `apex_call()` receiving IC weights | IC weights from ic_validator | **Silent use** — receives weights at call time | Should include weights `produced_at` in Apex context | Yes |
| `bot_dashboard.py` showing IC | `live_ic_report.json` | **Silent display** — shows numbers with no freshness indicator | Should show `generated_at` timestamp | Yes |
| `universe_promoter.py` if committed universe is stale | `committed_universe.json` | Promotes from a stale base | Should check committed universe age before running | Medium |

**Items that already fail closed (well-designed):**

| Component | Behavior |
|-----------|---------|
| `handoff_reader.py` consuming manifest | Fails closed if TTL (15 min) expired |
| `scanner.py` consuming `daily_promoted.json` | Graceful fallback + warning if >18h old |
| `orders_state.py` metadata guard | `_safe_set_trade` prevents metadata overwrite — hard fail |
| `event_log.py` ORDER_INTENT before submit | Write-ahead enforced — order rejected if write fails |

---

## SECTION 7: Cloud Shipping Impact

The following must be resolved before cloud deployment. None require trading behaviour changes.

| Issue | Severity | What Must Change | Files Allowed |
|-------|----------|-----------------|--------------|
| **Intelligence pipeline scheduler** | Blocker | Add launchd plist (macOS) or systemd timer (Linux cloud) for `run_intelligence_pipeline.py` — daily pre-market run | `ops/launchd/` or `ops/systemd/` — new plist/timer only |
| **Dual scheduling conflict** | Blocker | Remove bot.py internal schedule tags for universe refresh and promotion; verify launchd is sole authority | `bot.py` — remove 3 schedule calls (requires Amit approval, protected file) |
| **bot.py restart-on-failure** | Blocker | Add launchd plist or systemd service for bot.py itself with restart-on-failure | `ops/launchd/` — new plist |
| **apex_orchestrator.py stale docstring** | High | Update docstring to reflect live Track A status | `apex_orchestrator.py` — protected; requires Amit approval |
| **Intelligence freshness gates** | High | Add staleness check in handoff_publisher.py before publishing intelligence-derived universe | `handoff_publisher.py` — protected; requires Amit approval |
| **committed_universe.json freshness gate** | Medium | Add age check in scanner.py | `scanner.py` — protected |
| **ic_weights.json staleness gate** | Medium | Add staleness check in ic_validator.py | `ic_validator.py` |
| **Docker vs launchd authority confirmed** | Medium | Decide and document which orchestrator is authoritative in cloud | Documentation only |

**Items that are already cloud-ready:**
- Handoff manifest TTL enforcement (fail-closed)
- event_log.py write-ahead guarantee
- Atomic writes in universe workers and handoff_publisher
- Bracket order / stop-loss deterministic enforcement
- Paper account isolation (no live account credentials)

---

## SECTION 8: Recommended Next 3 Sprints

### Sprint 1: Intelligence Scheduler

**Goal:** Ensure intelligence files powering the live handoff are automatically refreshed daily before market open.

| Field | Value |
|-------|-------|
| Files allowed | `ops/launchd/com.decifer.intelligence-pipeline.plist` (new), `ops/systemd/decifer-intelligence-pipeline.service` (new), `docs/` |
| Files forbidden | `run_intelligence_pipeline.py`, `handoff_publisher.py`, `intelligence_adapters.py`, any protected runtime file |
| Tests required | Verify plist loads without error; verify intelligence files have fresher timestamps than 24h; verify handoff publisher picks up fresh output |
| Success evidence | `data/intelligence/daily_economic_state.json` has `generated_at` within 24h of market open; handoff_publisher_report.json shows no intelligence-freshness warnings |

### Sprint 2: Staleness Gates

**Goal:** Add freshness enforcement to the three critical unprotected consumers that silently use stale data.

| Field | Value |
|-------|-------|
| Files allowed | `ic_validator.py` (add `produced_at` check for ic_weights.json), `handoff_publisher.py` (add intelligence-file age check before publish — requires Amit approval), `scanner.py` (add committed_universe age check — requires Amit approval) |
| Files forbidden | `bot_trading.py`, `orders_core.py`, `market_intelligence.py`, any broker file |
| Tests required | Unit test: ic_validator rejects weights older than 14 days; handoff_publisher rejects intelligence files older than N hours; scanner warns on stale committed_universe |
| Success evidence | All three gates tested and passing; no silent stale-data use in any critical consumer |

### Sprint 3: Dual Schedule Cleanup + Restart-on-Failure

**Goal:** Establish launchd as sole scheduling authority for universe workers; add bot.py restart-on-failure.

| Field | Value |
|-------|-------|
| Files allowed | `bot.py` (remove 3 schedule library calls + iCloud sync call — requires Amit approval), `ops/launchd/com.decifer.bot.plist` (new restart-on-failure plist) |
| Files forbidden | `universe_committed.py`, `universe_promoter.py`, `bot_trading.py`, any broker or risk file |
| Tests required | Verify universe workers run on schedule via launchd only; verify no double-write on Sunday 23:00 or Mon–Fri 08:00/16:15; verify bot.py restarts within 30s of crash |
| Success evidence | `data/runtime/universe_worker_evidence.jsonl` shows single entry per scheduled run time; bot.py launchd plist installed and tested with forced crash |

---

## SECTION 9: No Trading Behaviour Change Confirmation

This sprint created four documentation files only. The following verification confirms no trading behaviour was changed.

**Files modified in this sprint:**
```
docs/architecture_control_register.md    (new — documentation)
docs/production_runtime_map.md           (new — documentation)
docs/retirement_register.md              (new — documentation)
docs/architecture_audit_report.md        (new — documentation)
```

**Verification commands (to be run post-commit):**
```bash
# 1. Confirm only docs/ files changed
git diff --name-only HEAD~1..HEAD

# 2. Confirm no protected Python files touched
git diff --name-only HEAD~1..HEAD | grep -E "\.py$" | grep -v "test_"
# Expected: zero output

# 3. Confirm no order, broker, risk, or config files changed
git diff --name-only HEAD~1..HEAD | grep -E "bot_trading|orders_|bot_ibkr|market_intelligence|config\.py|signals\.py|scanner\.py"
# Expected: zero output

# 4. Confirm all four documents exist and are non-empty
wc -l docs/architecture_control_register.md docs/production_runtime_map.md docs/retirement_register.md docs/architecture_audit_report.md
# Expected: all files show >50 lines
```

**Attestation:** No Python file was opened for editing. No config value was changed. No order logic, broker call, risk rule, sizing rule, or scanner scoring rule was modified. The audit was conducted by reading files and writing documentation only.

---

## SECTION 10: Risks and Open Questions

| Risk | Severity | Status | Owner |
|------|----------|--------|-------|
| Intelligence files may currently be stale — unknown last run date | High | Open | Amit to confirm when `run_intelligence_pipeline.py` was last run |
| ic_weights.json last updated date unknown | High | Open | Amit to confirm |
| Docker vs launchd: which is actually running handoff-publisher in production? | Medium | Open | Amit to confirm |
| apex_orchestrator.py docstring update requires Amit approval for protected file | Medium | Pending Amit | Amit |
| ml_engine.py wiring decision deferred | Medium | Pending Amit | Amit |
| `data/trades.json` — is this still actively written, or has it been superseded by `training_records.jsonl`? | Medium | Open | Needs code trace |
| symbol_master.json and theme_overlay_map.json — producer unknown | Low | Open | Needs code search |
| Tier D Phase 2 evidence gate — requires live scan cycles to accumulate tier_d_funnel.jsonl data | Low | Normal — accumulating | Automatic |
