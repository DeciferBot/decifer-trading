# Decifer 3.0 — Final Convergence Handoff
**Date:** 2026-04-27  
**Tag:** `decifer-3.0-post-migration-cleanup`  
**Test suite:** 1931 passed, 1 skipped, 0 failures  
**Status:** Architecture converged. Trading live.

---

## 1. Final Architecture Truth

### What runs per scan cycle

```
run_scan()
  ├─ guardrails.filter_candidates()          # universe → scored candidates
  ├─ apex_orchestrator._run_apex_pipeline()   # Track A: new entries (execute=True)
  │     └─ market_intelligence.apex_call()   # 1× claude-sonnet-4-6
  ├─ (PM block) apex_orchestrator._run_apex_pipeline()  # Track B: TRIM/EXIT/HOLD
  │     └─ market_intelligence.apex_call()   # 1× claude-sonnet-4-6
  └─ (Shadow) apex_orchestrator._run_apex_pipeline()    # divergence log (execute=False)
        └─ market_intelligence.apex_call()   # 1× claude-sonnet-4-6
```

### What runs per NEWS_INTERRUPT

```
handle_news_trigger()
  ├─ sentinel_agents.build_news_trigger_payload()  # pure function, no LLM
  ├─ apex_orchestrator._run_apex_pipeline()         # execute=True, 0.75× size
  │     └─ market_intelligence.apex_call()         # 1× claude-sonnet-4-6
  └─ signal_dispatcher.dispatch()                  # order routing
```

### Forced exits (deterministic — never touch Apex)
- EOD flat: `check_external_closes()` / `flatten_all()`
- INTRADAY 90-min timeout: `check_external_closes()` timer
- Architecture violation: `guardrails.screen_open_positions()`
- Regime-change sells: `_apex_mode_sells()` from `positions_to_reconsider`

---

## 2. Live Operational Flags

Only two safety_overlay flags remain. Both are operational features, not migration scaffolding.

| Flag | Value | Purpose |
|------|-------|---------|
| `USE_APEX_V3_SHADOW` | `True` | Runs a 3rd Apex call (execute=False) each cycle for divergence logging. Keep ON for observability. Only reason to disable: token budget emergency. |
| `FINBERT_MATERIALITY_GATE_ENABLED` | `True` | News materiality gate uses FinBERT confidence threshold. Keep ON. |

**Removed flags (no longer exist):**
- `USE_LEGACY_PIPELINE` — deleted
- `PM_LEGACY_OPUS_REVIEW_ENABLED` — deleted
- `SENTINEL_LEGACY_PIPELINE_ENABLED` — deleted
- `TRADE_ADVISOR_ENABLED` — deleted

---

## 3. What Was Removed at Cleanup

### Deleted entirely
| File/Function | Lines | Reason |
|--------------|-------|--------|
| `agents.py` | 990 | Legacy 4-agent orchestrator — all functions unreachable |
| `trade_advisor.py` | 420 | Position advisor — replaced by deterministic ATR sizing |
| `bot_trading.py` legacy buy loop | ~512 | Bypassed by Apex Track A `return` |
| `portfolio_manager.run_portfolio_review()` | 337 | Replaced by Apex Track B |
| `sentinel_agents` — 4 pipeline functions | ~350 | Replaced by `build_news_trigger_payload()` + Apex |
| `tests/test_agents.py` | 440 | Tested deleted agents |
| `tests/test_portfolio_manager.py` | 221 | Tested deleted `run_portfolio_review()` |
| `tests/test_sentinel_agents.py` | 448 | Tested deleted sentinel pipeline |
| `tests/test_apex_phase8b_agent_bypass.py` | 106 | Tested migration wiring now gone |
| 5 Phase 7C canary test files | ~300 | Pre-cutover shadow tests |
| 9 handoff/cutover .md docs | — | Archived to `docs/archive/2026-04-cutover/` |
| `data/session_monitor_2026-04-24.json` | — | One-shot cutover monitoring artifact |

### Simplified (file kept, dead code removed)
| File | What was removed |
|------|-----------------|
| `bot_trading.py` | Phase 8B agent bypass else-branch, Track A flag check, PM legacy else-branch |
| `bot_sentinel.py` | Legacy `run_sentinel_pipeline` call + shadow divergence block |
| `presession.py` | Legacy sentinel pipeline call + flag check |
| `safety_overlay.py` | 3 migration flag defaults + 3 accessor functions |
| `config.py` | 3 migration flags from safety_overlay dict |
| `signal_dispatcher.py` | `_advise_trade_gated()` + `from trade_advisor import ...` |
| `orders_portfolio.py` | `record_outcome()` try/except block |
| `learning.py` | `record_outcome()` try/except block |
| `scripts/apex_flip_proposer.py` | 3 legacy flags from FLIP_SEQUENCE and _FLAG_ACCESSOR |

---

## 4. What Remains

### Kept as permanent live modules
- `apex_orchestrator.py` — track routing, pipeline execution
- `market_intelligence.py` — `apex_call()` (the Apex LLM call)
- `signal_dispatcher.py` — order routing from Apex decisions
- `sentinel_agents.py` — `build_news_trigger_payload()` only (pure function)
- `portfolio_manager.py` — `lightweight_cycle_check()`, `prepare_review_payload()`, `_parse_actions()` (all used by Apex Track B)
- `guardrails.py` — `filter_candidates()`, `screen_open_positions()`, `flag_positions_for_review()`
- All signal modules, risk, learning, orders_core, etc.

### Kept as operational scaffolding (not migration)
- `apex_shadow_report.py` — divergence analysis tool
- `scripts/apex_flip_proposer.py` — flag audit tool (now 2 flags)
- `apex_divergence.py` — divergence event classifier

### Renamed Phase 8A tests (now permanent regression names)
| Old name | New name | What it tests |
|----------|----------|---------------|
| `test_apex_phase8a_execute_path.py` | `test_apex_live_execute_path.py` | `_run_apex_pipeline(execute=True)` contract |
| `test_apex_phase8a_pm_trackb_execute.py` | `test_apex_pm_trackb.py` | PM Track B always executes with Apex |
| `test_apex_phase8a_scan_cycle_cutover.py` | `test_apex_scan_cycle.py` | Scan cycle Track A unconditional Apex |
| `test_apex_phase8a_sentinel_ni_execute.py` | `test_apex_sentinel_ni.py` | Sentinel NI Apex path |
| `test_apex_phase8a_finbert_gate.py` | `test_finbert_gate.py` | FinBERT materiality gate |

---

## 5. agents_agreed Field — Audit Decision

**Current state:** `agents_agreed` field still exists in:
- `orders_core.py` function signatures (parameter, default=0)
- `signal_dispatcher.py` — passes `len(signal.source_agents or [])` → always 0 for Apex trades
- `learning.py` — copies field from trade records
- `ml_engine.py` — used as training feature; always 0 for Apex trades (no match on `source_agents`)
- `brain.py:372` — reads `agents_agreed` from last trade data but does NOT display it (display was fixed to "Apex Synthesizer" in Step 3)

**Decision: DEFER**

Reasons:
1. The value is always 0 for all new Apex trades — harmless, not confusing to operators
2. Removal touches 5 files and the ML feature vector — meaningful scope for a routine cleanup pass
3. ML is gated at 50 closed trades and not actively training — no urgency
4. `brain.py` already ignores it for display; `agents_agreed=0` in `trades.json` is invisible to operators

**When to remove:** Immediately before the first ML retraining run. Remove from:
- `orders_core.py` — drop parameter from `execute_buy()` and `execute_short()`
- `signal_dispatcher.py` — remove `agents_agreed=` kwarg from both call sites
- `learning.py` — drop from both `log_trade()` record shapes
- `ml_engine.py` — remove from `_extract_agents_count`, feature list, and `SignalEnhancer`
- `brain.py` — remove `agents = data.get("agents_agreed", 0)` (unused variable)
- `signal_types.py` — remove `source_agents` field entirely

**Amit must confirm** before touching `ml_engine.py` — it affects training feature quality.

---

## 6. Remaining Minor Stale References (Non-Blocking)

These are comments/labels in non-runtime code. Safe to defer indefinitely.

| Location | Stale content | Action |
|----------|--------------|--------|
| `config.py:266-270` | `agents_required_to_agree` comment updated to note it's a legacy dashboard field; value still surfaced in dashboard settings panel but not used by Apex | Updated comment — dashboard UI cleanup deferred |
| `dashboard.py:3484,4398` | `agree-select` settings UI still reads/writes `agents_required_to_agree` | Cosmetic — defer until dashboard refresh |
| `bot.py:99,308` | reads `agents_required_to_agree` for dash state | Reads but Apex ignores — defer |
| `tests/test_bot.py:225` | `agents_stub.run_all_agents` set on stub but never called | Harmless dead stub attr — defer |
| `tests/test_reconnect.py:120` | `_stub.run_all_agents` set on stub but never called | Harmless dead stub attr — defer |
| `docs/PROCESS_ARCHITECTURE.md` | Describes "Agent 4 — Trade Synthesiser" from Decifer 2.0 | Historical doc — archive or update at doc sprint |

---

## 7. Phase Gate Status

| Gate | Status | Notes |
|------|--------|-------|
| IC Phase A (50+ closed trades for ML) | Not yet met | Counting from live execution |
| IC Phase C (200 closed trades) | Not yet met | Full signal validation gate |
| HMM regime detection | Deferred | Requires Phase C gate |
| Walk-forward calibration | Deferred | Requires HMM + Alphalens |
| `agents_agreed` field removal | Deferred | Before first ML retraining |

---

## 8. What Verified Clean

**Cold start:**
- `agents.py` → `ModuleNotFoundError` (deleted) ✓
- `sentinel_agents` public API: `['CONFIG', 'build_news_trigger_payload', 'log', 'logging']` ✓
- `safety_overlay` — no legacy accessors (`should_use_legacy_pipeline`, etc.) ✓
- `portfolio_manager` — no `run_portfolio_review` ✓
- `config.safety_overlay` — only `USE_APEX_V3_SHADOW: True`, `FINBERT_MATERIALITY_GATE_ENABLED: True` ✓

**Smoke:**
- `signal_dispatcher.dispatch({}, ...)` returns `{new_entries:[], portfolio_actions:[], forced_exits:[], errors:[]}` ✓
- `build_news_trigger_payload(trigger={symbol:'NVDA',...})` returns `{trigger_type:'NEWS_INTERRUPT',...}` ✓
- `apex_orchestrator` — `_run_apex_pipeline`, `build_scan_cycle_apex_input`, `log_shadow_result` all present ✓
- `signal_dispatcher._formula_advice` present (local, no trade_advisor dependency) ✓

**Test suite:** 1931 passed, 1 skipped, 0 failures (2026-04-27)

---

*This document is the terminal state record for the Decifer 3.0 migration. Next session should focus on trading operation and IC data accumulation.*
