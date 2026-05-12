# Retirement Register
**Sprint:** Architecture Audit — documentation only  
**Branch:** audit/architecture-control-register  
**Date:** 2026-05-12

> This register tracks code, artifacts, and documentation that is potentially legacy, duplicate, stale, shadow-only, or unclear in ownership. Each entry has a recommendation — none are deleted in this sprint.
>
> Confidence levels:  
> - **Observed** — directly confirmed from code, config, or file existence  
> - **Inferred** — reasonable conclusion from evidence  
> - **Unverified** — needs runtime confirmation

---

## Section 1: Shadow-Only Code Risks

Items that are operational-looking but do not submit orders or affect live state. Risk: operator assumes they are active.

| Item | Path | Service Layer | Runtime Purpose | Evidence | Risk of Keeping | Safe Retirement Path | Recommendation |
|------|------|--------------|----------------|----------|----------------|---------------------|----------------|
| Apex Track B PM (execute=False) | `apex_orchestrator.py` (called from bot_trading.py line 2091) | live_trading | shadow_only | Observed: `execute=False` in call | Low — clearly flagged in call | Upgrade to `execute=True` when PM Apex is approved by Amit | **keep** — intentional shadow |
| `apex_shadow_log.jsonl` | `data/apex_shadow_log.jsonl` | observability | shadow_only | Observed: file does not yet exist; created on first run | Low | N/A — will be created automatically | **keep** — will accumulate |
| `data/apex_prompt_snapshot.jsonl`, `data/apex_response_snapshot.jsonl` | `data/` | observability | shadow_only | Inferred: created by apex_orchestrator if logging enabled | Low | N/A | **keep** |

---

## Section 2: Built But Gated / Not Wired

Items with full implementation but no production caller or config gate.

| Item | Path | Service Layer | Runtime Purpose | Evidence | Risk of Keeping | Safe Retirement Path | Recommendation |
|------|------|--------------|----------------|----------|----------------|---------------------|----------------|
| ml_engine.py | `ml_engine.py` | reporting / validation | advisory_only | Observed: imported with `# noqa: F401` in bot.py line 808; no call in scan loop | High — gate met (≥50 trades); could be mistaken for active | Wire into scan cycle as post-score enhancement step with Amit approval; or add explicit "NOT ACTIVE" comment | **keep** — wire pending Amit approval |
| ML models | `data/models/classifier.pkl`, `regressor.pkl`, `features.pkl`, `scaler.pkl`, `metadata.json` | validation | advisory_only | Observed: files exist (~1.5MB total); producer is ml_engine.py offline training | Low — data only; no production impact | N/A | **keep** |
| HMM regime stub | `bot_trading.py` lines 1466–1486 | live_trading | validation_only | Observed: config gate `hmm_regime.enabled: False`; import attempted from signals.py | Low — gated off; import failure handled | Leave gated; enable only with Amit approval after Alphalens | **keep** — awaiting gate |
| Walk-forward weight calibration | `roadmap/06-weight-calibration.md` | validation | unknown | Observed: spec only; no Python implementation | Low — spec document | No action needed | **keep as spec** |
| Alphalens integration | `roadmap/05-signal-validation.md` | validation | unknown | Observed: spec only; `alpha_validation.py` uses custom IC, not Alphalens library | Low — custom implementation fills partial gap | Clarify in alpha_validation.py that output is custom IC, not Alphalens | **keep as spec** |

---

## Section 3: Duplicate Scheduling

Items scheduled by more than one mechanism simultaneously.

| Item | Path | Service Layer | Runtime Purpose | Evidence | Risk of Keeping | Safe Retirement Path | Recommendation |
|------|------|--------------|----------------|----------|----------------|---------------------|----------------|
| Universe committed refresh (internal) | `bot.py` line 627 — `schedule.every().sunday.at("23:00")` | universe_builder | production_runtime (duplicate) | Observed: launchd plist fires same task at same time | Medium — race condition; double writes possible | Remove `schedule.every().sunday.at("23:00")` call from bot.py once launchd confirmed sole authority | **mark_deprecated** — remove from bot.py when checks 26+27 pass |
| Universe promoter pre-market (internal) | `bot.py` line 626 — `schedule.every().day.at("08:00")` | universe_builder | production_runtime (duplicate) | Observed: launchd plist fires same task | Medium | Same as above | **mark_deprecated** |
| Universe promoter EOD (internal) | `bot.py` line 625 — `schedule.every().day.at("16:15")` | universe_builder | production_runtime (duplicate) | Observed: launchd plist fires same task | Medium | Same as above | **mark_deprecated** |
| iCloud sync (internal) | `bot.py` line 835 — `schedule.every(5).minutes.do(_run_icloud_sync)` | legacy | production_runtime (duplicate) | Observed: launchd plist `com.decifer.icloud-sync.plist` fires same task every 5 min | Low — two rsync calls add no harm | Remove from bot.py | **mark_deprecated** |

---

## Section 4: Legacy / Pre-Migration Artifacts

Items that existed before Decifer 3.0 migration and may have been formally replaced.

| Item | Path | Service Layer | Runtime Purpose | Evidence | Risk of Keeping | Safe Retirement Path | Recommendation |
|------|------|--------------|----------------|----------|----------------|---------------------|----------------|
| `agents.py` reference | CLAUDE.md says deleted post-migration | legacy | deprecated | Historical: deleted 2026-04-27 per CLAUDE.md | Low — confirmed deleted | N/A | **N/A** — already deleted |
| `sentinel_agents.py` pipeline reference | CLAUDE.md says deleted | legacy | deprecated | Historical | Low | N/A | **N/A** — already deleted |
| `trade_log.py` (SQLite WAL) | CLAUDE.md says deleted 2026-04-28 | legacy | deprecated | Historical | Low | N/A | **N/A** — already deleted |
| `trade_store.py` | CLAUDE.md says deleted 2026-04-28 | legacy | deprecated | Historical | Low | N/A | **N/A** — already deleted |
| `data/trades.json` (old format) | `data/trades.json` | legacy | historical_data | Observed: 1.6MB; still read by bot_dashboard.py and learning.py | Medium — post-migration, training_records.jsonl is canonical; trades.json may become stale | Confirm whether trades.json is still actively updated or is now historical-only | **needs_more_evidence** |
| `run_portfolio_review()` | bot.py / bot_trading.py | legacy | deprecated | Historical: deleted per CLAUDE.md post-migration cleanup | Low | N/A | **N/A** — already deleted |

---

## Section 5: Stale or Orphaned Documentation

| Item | Path | Evidence | Risk | Recommendation |
|------|------|----------|------|----------------|
| docs/intelligence_first_retirement_register.md | `docs/intelligence_first_retirement_register.md` | Observed: file exists; this audit supersedes it for intelligence-first-specific entries | Low — duplicate register may cause confusion | **keep** as historical record; note that this register supersedes it |
| docs/codebase_cleanup_retirement_audit.md | `docs/codebase_cleanup_retirement_audit.md` | Observed: JSON version also in data/intelligence/ | Low | **keep** |
| docs/cloud_readiness_preparation_report.md | `docs/cloud_readiness_preparation_report.md` | Inferred: pre-deployment artifact; may be superseded by go/no-go checklist | Low | **keep** as reference |
| Multiple overlapping cloud readiness docs (7 files) | `docs/cloud_phase1_*.md`, `docs/cloud_readiness_*.md` | Observed: 7 files covering similar ground | Low — informational redundancy only | **keep** — archive in docs/archive/ in later sprint |
| Multiple overlapping intelligence-first docs (15+ files) | `docs/intelligence_first_*.md` | Observed: implementation, test plans, audits, checklists all exist | Low | **keep** — archive superseded docs in later sprint |

---

## Section 6: Reference Data with Unclear Update Cadence

| Item | Path | Service Layer | Runtime Purpose | Evidence | Update Cadence | Risk | Recommendation |
|------|------|--------------|----------------|----------|---------------|------|----------------|
| `data/reference/symbol_master.json` | `data/reference/symbol_master.json` | reference_data | unknown | Observed: 277KB; producer unclear | Unknown | Medium — stale symbol master could cause missed opportunities or bad lookups | Add produced_at timestamp; confirm producer script | **needs_more_evidence** |
| `data/reference/provider_capability_matrix.json` | `data/reference/provider_capability_matrix.json` | reference_data | advisory_only | Observed: 24KB | Unknown | Low | **keep** |
| `data/reference/provider_fetch_test_results.json` | `data/reference/provider_fetch_test_results.json` | reference_data | advisory_only | Observed: 6.8KB | Unknown | Low — last provider health check result | **keep** |
| `data/reference/theme_overlay_map.json` | `data/reference/theme_overlay_map.json` | reference_data | production_runtime | Observed: 41KB; consumed by theme activation | Unknown | Medium — stale theme map affects signal classification | Confirm update cadence; add staleness check | **needs_more_evidence** |

---

## Section 7: Chief-Decifer State Archive

Chief-Decifer-recovered state contains 70+ JSON files accumulated 2026-03-29 to 2026-04-14. These are read-only historical records and do not affect production runtime.

| Category | Path | Count | Date Range | Status | Recommendation |
|----------|------|-------|-----------|--------|----------------|
| Analysis snapshots | `chief-decifer/state/analysis/` | 70+ | 2026-03-29 – 2026-04-14 | historical_data | **keep** |
| Feature specs | `chief-decifer/state/specs/` | 116 | Various | advisory_only | **keep** — Chief Decifer reads these |
| Research files | `chief-decifer/state/research/` | 14 | Various | advisory_only | **keep** |
| Session tracking | `chief-decifer/state/sessions/` | 38 | Various | historical_data | **keep** |
| Operational state | `chief-decifer/state/operational_state.json` | 1 | Latest | advisory_only | **keep** — Chief Decifer reads this |

No retirement action needed. This is Chief Decifer's persistent memory — data contracts in CLAUDE.md define write ownership.

---

## Section 8: Intelligence Backtest Fixtures

| Item | Path | Service Layer | Runtime Purpose | Evidence | Risk | Recommendation |
|------|------|--------------|----------------|----------|------|----------------|
| Backtest fixtures | `data/intelligence/backtest/` (8 files) | validation | test_only | Observed: created by intelligence backtest suite | Low — test data only | **keep** — needed by intelligence tests |
| `data/intelligence/codebase_cleanup_retirement_audit.json` | `data/intelligence/codebase_cleanup_retirement_audit.json` | legacy | historical_data | Observed: 138KB; mirrors docs version | Low | **keep** as reference |
| `data/intelligence/scanner_only_removals_review.json` | `data/intelligence/scanner_only_removals_review.json` | legacy | historical_data | Observed: 155KB; scanner retirement tracking | Low | **keep** as reference |
| `data/audit/training_record_quarantine_20260511.json` | `data/audit/training_record_quarantine_20260511.json` | validation | historical_data | Observed: 1.6KB; quarantined bad trades | Low | **keep** — audit trail |

---

## Retirement Summary

| Recommendation | Count | Items |
|----------------|-------|-------|
| keep | 25+ | All active code, valid docs, historical data |
| mark_deprecated | 4 | Duplicate schedule library calls in bot.py (3 universe jobs + iCloud sync) |
| needs_more_evidence | 3 | data/trades.json status, symbol_master.json, theme_overlay_map.json |
| delete_later | 0 | Nothing safe to delete this sprint |
| quarantine | 0 | Nothing requires quarantine |

**No files are deleted or modified in this sprint.**
