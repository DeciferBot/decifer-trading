# Intelligence-First Migration — Codebase Audit

**Audit date:** 2026-05-05
**Auditor:** Cowork (Claude)
**Purpose:** Locate all current wiring points before any Intelligence-First migration code is written.
**live_output_changed:** false

---

## 1. Where the Current Universe Is Built

**Primary entry:** `scanner.py:363` — `get_dynamic_universe(ib, regime)`

Called from `bot_trading.py:1440`:
```python
universe = get_dynamic_universe(ib, regime)
```

This function builds a Python `set[str]` from four tiers and returns it as a `list[str]`.

---

## 2. Tier A / B / C / D Merge Flow

### Tier A — Always-on floor (`scanner.py:395–397`)
- `CORE_SYMBOLS` (16 names): SPY, QQQ, IWM, VXX, UVXY, SVXY, SPXS, SQQQ, IBIT, BITO, MSTR, GLD, SLV, USO, COPX, (one more)
- `CORE_EQUITIES` (41 names): NVDA, AAPL, MSFT, AMD, CRM, GOOGL, META, LLY, ABBV, MRNA, BIIB, REGN, UNH, MDT, ABT, AMZN, TSLA, NKE, MCD, TGT, WMT, COST, PG, XOM, CVX, OXY, COP, CAT, GE, HON, FCX, NEM, LIN, JPM, GS, V
- Combined set: ~57 names, hardcoded, always present

### Tier B — Daily promoted (`scanner.py:400–416`)
- Source: `universe_promoter.py` `load_promoted_universe()` → reads `data/daily_promoted.json`
- Promoter runs at 16:15 ET + 08:00 ET via scheduled job
- Staleness gate: 18 hours (`promoted_max_staleness_hours` in config)
- Content: top ~50 names scored on gap% (weight 3.0) + premarket volume ratio (2.0) + catalyst score (2.0)
- Failure mode: if stale or missing, bot runs Tier A only

### Tier C — Sector rotation (`scanner.py:418–432`)
- Source: `get_sector_rotation_bias()` → 5-day relative strength of 11 sector ETFs vs SPY
- Top-3 sector ETFs added + their constituents from `_SECTOR_STOCKS` dict (6 stocks per ETF)
- Also includes: options universe (`options_scanner.OPTIONABLE_UNIVERSE`) pinned in `bot_trading.py:1494–1502`

### Tier D — Position Research Universe (`scanner.py:434–449`)
- Source: `get_position_research_universe()` from `universe_position.py` → reads `data/position_research_universe.json`
- Built weekly by `universe_position.py` — ~150 fundamental/technical discovery names
- Controlled by config key: `position_research_universe_enabled` (default True)
- Names bypass gap/premarket-volume promoter — enter universe regardless of daily movement

### Bot-level additions (`bot_trading.py:1469–1502`)
After `get_dynamic_universe()` returns, `bot_trading.py` unions in three more sources:
1. **Favourites** (`data/favourites.json` via `dash.get("favourites", [])`) — manually curated, always added
2. **Held positions** (`get_open_positions()` → `held_syms`) — pinned so PM always sees them
3. Both merged into `pipeline_favs = list(set(favs + held_syms))` — passed to signal pipeline as always-score set

**No do-not-touch list exists in the current system.** The closest equivalent is `is_failed_thesis_blocked()` in `orders_state.py` (prevents re-entry after a failed thesis, not a persistent exclusion list).

---

## 3. Where Tier D Candidates Enter the Pool

`scanner.py:437–443`:
```python
tier_d_syms, _meta = get_position_research_universe()
symbols.update(tier_d_syms)
```

Tier D names are added to the same `symbols` set as Tier A/B/C — no separate lane, no label attached at this point. The `scanner_tier="D"` label is attached later by `universe_position.py`'s metadata, but not every downstream component reads it.

---

## 4. Where the Score Adjuster Applies

**PRU score adjustment:** `signal_pipeline.py:354–395` — `_apply_pru_score_adjustment()`

Applied after `score_universe()` runs, before candidates reach the Apex cap. For Tier D candidates that are also in the position research universe (`position_research_universe_member=True`):
- Raw signal score + bounded research bonus → `effective_score`
- Bonus drawn from `adjusted_discovery_score`, `primary_archetype`, `universe_bucket`

**Apex cap score adjuster:** `apex_cap_score.py` — `compute_apex_cap_score()`

Applied at `bot_trading.py:2410–2411` — separate from the PRU adjustment above:
- Non-Tier-D: `apex_cap_score = raw signal score` (unchanged)
- Tier D with signal score ≥ 18: bonus up to +8.0 (discovery_bonus max 5.0 + archetype_bonus 2.0 + bucket_bonus 1.0)
- Tier D with signal score < 18: `apex_cap_score = raw signal score` (no bonus — guardrail)

**This is a sort-key adjustment only. It provides no quota protection.**

---

## 5. Where Apex Cap Logic Applies

`bot_trading.py:2394–2475`

**Full flow:**

1. `guardrails.filter_candidates()` called at `bot_trading.py:2401` — drops: already-held, cooldown (`_is_recently_closed`), failed-thesis blocked, open order exists, earnings_days_away == 0
2. `compute_apex_cap_score()` attached to every remaining candidate (`bot_trading.py:2410–2411`)
3. Dedup by symbol — if duplicate rows, best `apex_cap_score` wins (`bot_trading.py:2419–2455`)
4. Sort by `apex_cap_score` descending (`bot_trading.py:2465–2468`)
5. Top 30 unconditional (`_CORE_LIMIT = 30`) (`bot_trading.py:2470`)
6. Slots 31–50 gated by `apex_expanded_band_floor = 20` (`bot_trading.py:2471–2474`)
7. Final cap at 50 (`_CAP_LIMIT = 50`)

**Tier D funnel telemetry** written to `data/tier_d_funnel.jsonl` at `bot_trading.py:2558–2647` — stage="apex_cap" records how many Tier D names entered, survived, and were dropped.

**Position candidate disadvantage (root cause):**
- No reason_to_care label exists on any candidate when it reaches this sort
- Structural Tier D names and 5-minute tactical movers compete in the same sorted pool
- The +8 Apex cap bonus is a sort-key nudge, not quota protection — it can still be beaten by a non-Tier-D name scoring 30+ on a gap day
- No minimum reserved slots for structural/position candidates

---

## 6. Where Apex Receives Candidates

`market_intelligence.py:1074` — `_build_apex_user_prompt(apex_input, sctx)`

The `apex_input` dict is built in `bot_trading.py` before calling `apex_call()` at `market_intelligence.py:1226`.

**Current Apex input includes:**
- `track_a.candidates` — scored/filtered/capped candidate list (max 50)
- `track_b.positions` — open positions for PM review
- Session context via `SessionContext` (`sctx`): regime, session character, overnight_notes, news, macro calendar, thesis performance, setup patterns, IC weights

**Current Apex input does NOT include:**
- `reason_to_care` (structural / catalyst / attention)
- `bucket_id` or `transmission_direction`
- `route` pre-tag (position / swing / intraday_swing)
- `source_labels` (which tier/source contributed the candidate)
- `macro_rules_fired` (which deterministic rules activated this candidate)
- `thesis_intact` (company-specific validation status)
- `risk_notes` from universe builder

Apex must infer all of this from signal scores, pattern_library, and session context alone.

---

## 7. Where Candidates Are Filtered Before Scoring

**Before `score_universe()` runs** (`signal_pipeline.py:521–545` — PRU staging):
- Tier D names staged to prevent PRU metadata bloat during scoring

**After `score_universe()` runs:**
- `guardrails.filter_candidates()` (`bot_trading.py:2401`) — drops held, cooldown, failed-thesis, open-order, earnings-day candidates
- Note: `guardrails.filter_candidates()` runs on `pipeline.all_scored` — candidates that scored on any dimension

**`guardrails.py:filter_candidates()` gates:**
1. No symbol → skip
2. Already held (`sym in open_symbols`) → drop
3. Recently closed (`_is_recently_closed`) → drop (cooldown)
4. Failed thesis blocked (`is_failed_thesis_blocked`) → drop
5. Open order exists (`has_open_order_for`) → drop
6. `earnings_days_away == 0` → drop (earnings today)
7. Allowed trade types computed: `compute_allowed_trade_types(symbol, regime, minutes_to_close)`

---

## 8. Where Live Intraday Data Is Pulled

**Signal scoring** (`signals.py` `score_universe()` via `signal_pipeline.py:767`):
- Per-symbol: Alpaca historical bars (1m, 5m, 15m, 1h, 1d) via `alpaca_data.get_bars()`
- Parallel fetch via `ThreadPoolExecutor`
- This is the primary broad intraday data pull — ALL universe symbols receive bar fetches here

**Regime detection** (`scanner.py` `get_market_regime()`):
- SPY, QQQ, VIX, UUP (DXY), HYG, LQD bars — narrow set (regime symbols only), not broad universe

**News sentiment** (`signal_pipeline.py:155` `_fetch_news()`):
- Yahoo RSS per symbol — runs AFTER the universe is capped to filtered set (`signal_pipeline.py:714`)
- 8-second timeout

**Order execution** (`orders_core.py`):
- IBKR live bid/ask via `_get_ibkr_bid_ask()` — only for candidates passing all gates (final execution check)
- Alpaca quote cache (`QUOTE_CACHE.get_spread_pct()`) — spread check before order

**Background boundary:** `get_market_regime()` pulls ~6 regime-specific symbols. `score_universe()` pulls bars for the full universe (up to ~200 symbols). This broad `score_universe()` pull is what the new architecture seeks to constrain — the live bot should only pull bars for symbols in `active_opportunity_universe.json`.

---

## 9. Where Execution and Risk Gates Live

**Primary execution gates:** `orders_core.py` `execute_buy()` — 11 deterministic gates

1. Safety overlay circuit breaker (`can_submit_order()`)
2. Trading halts check (Alpaca)
3. Bid-ask spread check (`max_spread_pct`, default 0.3%)
4. Active trades duplicate guard (already held)
5. Recently closed cooldown (30 minutes)
6. Failed thesis gate (recent loss threshold)
7. Max positions check
8. Correlation check (new position vs portfolio)
9. Combined exposure check (notional across similar-beta)
10. Sector concentration check
11. IBKR duplicate order guard

**Pre-flight validation:** `orders_core.py:87–159` `_validate_order_context()`:
- Inverse ETF short guard (SPXS/SQQQ/UVXY cannot be shorted)
- INTRADAY timing rule (blocked within 30 min of close)
- Zero-conviction orphan guard (score=0 + trade_type=UNKNOWN)

**Risk conditions:** `risk.py` `check_risk_conditions()` — portfolio-level gate (max daily loss, position count, etc.)

---

## 10. Where Feature Flags Live

`config.py` — single dict `CONFIG` loaded at import time.

**Existing shadow/mode flags:**
- `USE_APEX_V3_SHADOW: True` (`config.py:174`) — shadow + divergence logging
- `position_research_universe_enabled` — Tier D on/off
- `swing_news_alone_blocks: False` — Phase 1 shadow mode comment
- IC `auto_enable_threshold` / `auto_enable_weeks` — IC-driven re-enable

**No `intelligence_first_*` flags exist yet.** Adding in this PR.

---

## 11. Where JSON Files Are Written

| File | Writer | Frequency |
|------|--------|-----------|
| `data/daily_promoted.json` | `universe_promoter.py` | 08:00 + 16:15 ET |
| `data/committed_universe.json` | `universe_committed.py` | Weekly |
| `data/position_research_universe.json` | `universe_position.py` | Weekly |
| `data/overnight_notes.json` | `overnight_research.py` | 06:00 ET daily |
| `data/tier_d_funnel.jsonl` | `bot_trading.py:2558–2693` | Each scan cycle |
| `data/universe_coverage.jsonl` | `bot_trading.py` | Each scan cycle |
| `data/signals_log.jsonl` | `signal_pipeline.py` | Each scan cycle |
| `data/execution_ic.jsonl` | `learning.py` | Per trade open/close |
| `data/training_records.jsonl` | `training_store.py` | Per closed trade |
| `data/trade_events.jsonl` | `event_log.py` | Per order state change |
| `data/favourites.json` | `bot_dashboard.py` (user edits via UI) | On demand |
| `data/apex_decision_audit.jsonl` | `bot_trading.py` | Per Apex call |
| `data/apex_divergence_log.jsonl` | Shadow Apex | Per scan cycle |

---

## 12. Where Tests Live

`tests/` directory — 82 test files (80 test modules + `__init__.py` + `conftest.py`).

**Tests directly relevant to candidate flow and universe:**
- `test_scanner.py` — `get_dynamic_universe()`, tier composition
- `test_universe_promoter.py` — Tier B promotion logic
- `test_universe_position.py` — Tier D discovery, scoring, archetype matching
- `test_universe_committed.py` — committed base universe
- `test_tier_d_visibility.py` — Tier D survival through Apex cap
- `test_tier_d_evidence_report.py` — Tier D funnel telemetry
- `test_signal_pipeline.py` — pipeline stages, PRU adjustment
- `test_signal_dispatch.py` — dispatch logic

**Tests protecting production safety (must be preserved):**
- `test_orders.py`, `test_orders_core.py`, `test_orders_execute.py`, `test_orders_guard.py`, `test_orders_regression.py` — all execution gate tests
- `test_apex_live_execute_path.py`, `test_apex_migration_guards.py` — Apex execution path
- `test_risk.py` — risk condition gates
- `test_entry_gate.py` — entry gate logic
- `test_flatten_all_hardened.py` — forced EOD flat
- `test_ibkr_reconciler.py` — broker reconciliation
- `test_event_log_and_training_store.py` — persistence integrity
- `test_positions_persistence.py`, `test_position_closed_completeness.py` — position state
- `test_fill_watcher.py` — fill handling
- `test_duplicate_order_guard.py` — duplicate prevention
- `test_drawdown_brake.py` — drawdown protection
- `test_trailing_stop.py`, `test_sl_lifecycle.py`, `test_tranche_exits.py` — stop/exit logic

---

## 13. Where Held Positions and Manual Conviction Names Are Handled

**Held positions:**
- Fetched via `get_open_positions()` at `bot_trading.py:1480`
- Pinned into `universe` at `bot_trading.py:1482–1483`
- Added to `pipeline_favs` at `bot_trading.py:1488`
- Always passed to signal pipeline — receive scores even if not in Tier A/B/C/D

**Manual conviction names = Favourites:**
- Stored in `data/favourites.json` (currently: ASTS, GLD, IBIT, USO, SPY, QQQ, NVDA, TSLA, AAPL, HIMS, NBIS, MU, ONDS)
- Added to universe at `bot_trading.py:1471–1474`
- Merged into `pipeline_favs` alongside held positions

**No explicit do-not-touch list.** The closest mechanisms are:
- `is_failed_thesis_blocked()` in `orders_state.py` — prevents re-entry after recent loss
- `long_only_symbols` config key — prevents shorting specific symbols (SPXS, SQQQ, UVXY)

---

## 14. Safe Shadow Wiring Point

**Recommended attachment point:** `bot_trading.py` immediately after line 2475 (`_cut_candidates = _core + _expanded`).

At this point:
- All live execution logic is complete up to Apex input construction
- `_cut_candidates` is the exact list Apex will receive
- Shadow comparison can read `active_opportunity_universe_shadow.json`, compare it against `_cut_candidates`, and write `current_vs_shadow_comparison.json`
- No modification to `_cut_candidates`, `pipeline`, `scored`, or any downstream variable
- Gated by `intelligence_first_shadow_enabled` flag (default false)

```python
# SHADOW: Intelligence-First comparison (gated, read-only, no live effect)
if CONFIG.get("intelligence_first_shadow_enabled", False):
    try:
        from compare_universes import run_shadow_comparison
        run_shadow_comparison(_cut_candidates, open_pos, regime)
    except Exception as _e:
        log.debug("Shadow comparison skipped: %s", _e)
```

This is the only safe wiring point during Day 1–7. All other wiring points involve live execution paths.

---

## 15. Position Candidate Disadvantage — Diagnosis

**Root cause:** No upstream label distinguishes structural candidates from tactical attention movers before Apex sees them.

**Symptom:** Tier D position research names (structural, multi-week thesis) compete on equal footing against gap movers (tactical, intraday) in the Apex cap sort. On high-gap days, 30+ non-Tier-D names score above Tier D names even with the +8 bonus, leaving the structural candidate list empty.

**Evidence:** `data/tier_d_funnel.jsonl` stage="apex_cap" records show Tier D candidates being dropped by the hard cap. The evidence report (`scripts/tier_d_evidence_report.py` Section 0b) documents the extent.

**What the new architecture fixes:**
- `reason_to_care = "structural"` pre-labelled in universe file → Apex knows what it is
- Structural quota (min 8, max 20) enforced in Universe Builder → structural names cannot be crowded out
- Attention names capped (max 15) → gap movers cannot consume the entire candidate pool
- Route pre-assigned (`position`) → Apex cannot demote a structural name to intraday without advisory log

---

## Summary

| Audit Point | Location | Notes |
|------------|----------|-------|
| Universe build | `scanner.py:363` `get_dynamic_universe()` | 4 tiers merged into one set |
| Tier A/B/C/D merge | `scanner.py:395–449` | No labels survive the merge |
| Tier D entry | `scanner.py:437–443` | Union into shared symbol set |
| Score adjuster | `signal_pipeline.py:354` + `apex_cap_score.py` | PRU bonus + sort-key bonus (not quota) |
| Apex cap | `bot_trading.py:2394–2475` | Top 30 unconditional + 31-50 gated, max 50 |
| Apex input | `market_intelligence.py:1074` | No reason_to_care / route / bucket labels |
| Candidate filter | `guardrails.py` `filter_candidates()` at `bot_trading.py:2401` | Deterministic gates only |
| Live intraday pull | `signals.py` `score_universe()` via ThreadPoolExecutor | Full universe bar fetch every cycle |
| Execution gates | `orders_core.py` `execute_buy()` 11 gates | Solid, no changes needed |
| Feature flags | `config.py` | No `intelligence_first_*` flags yet |
| JSON outputs | `data/` various | See table in section 11 |
| Tests | `tests/` 82 files | See safety tests list in section 12 |
| Held positions | `bot_trading.py:1480–1488` | Pinned before signal pipeline |
| Manual conviction | `data/favourites.json` via `dash` | No separate mechanism |
| Shadow wiring point | `bot_trading.py` after line 2475 | Safe, no live effect |

---

## Known Pre-Existing Test Failures (tracked, not caused by Intelligence-First work)

These failures existed on master before Sprint 2 (confirmed by running them against the pre-Sprint-2 stash baseline). They are not caused by any intelligence-first change and do not block shadow work.

| Test Module | Failure Type | Failure Summary | Present before Sprint 2 | Caused by Sprint 2 | Blocks intelligence-first shadow work | Blocks production handoff |
|-------------|-------------|-----------------|-------------------------|-------------------|---------------------------------------|--------------------------|
| `test_reconnect.py` | Parametrised assertion failure | `test_backoff_parametrized` — backoff timing values don't match expected params (20 failures) | Yes | No | No | To be reviewed before production |
| `test_tranche_exits.py` | ImportError | `cannot import name 'log_order' from 'learning'` — orders.py line 28 tries to import a removed function (8 failures) | Yes | No | No | Yes — orders pipeline must be clean before production handoff |

**Remediation owner/status:** TBD — not assigned in this sprint. Both failures pre-date the intelligence-first work packet.

**Rule:** Before any production handoff (`enable_active_opportunity_universe_handoff = True`), the full suite must be clean. These failures must be resolved or documented as known-acceptable before that gate is opened.

**Verification method:** Run `pytest tests/test_reconnect.py tests/test_tranche_exits.py -q` — failures are pre-existing and unchanged by Sprint 2 or Sprint 3.

