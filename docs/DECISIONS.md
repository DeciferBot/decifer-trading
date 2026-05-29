# Decifer Trading — Decision Log

> Every significant design decision, parameter change, or architectural choice gets logged here with the reasoning. This is the "why" behind the "what."
>
> Format: Date → Decision → Context / Reasoning

---

## 2026-05-30 — Signal dimension scorer fixes: momentum, flow, breakout, MTF, overnight_drift weight

### Decision

Four dimension scorers corrected to score signal initiation rather than confirmation. `BASELINE_WEIGHTS["overnight_drift"]` set to 0.00.

**Momentum**: Added exhaustion zone (MFI > 72 or < 28 → max 3 pts). Removed score-8 path for `mfi_dist > 15` without RSI slope confirmation (was 8, now 4). RSI slope confirmation is now required for scores above 4.

**Flow**: Replaced absolute VWAP distance threshold (`abs_vwap > 0.3% → +4`) with SD-normalized distance (`vwap_sds < 0.5 → +4`). Near VWAP (accumulation zone) now scores high; far from VWAP (chase zone) scores low. Uses `vwap_sd_pct` already computed by `compute_indicators()`.

**Breakout**: Pre-breakout proximity now scores higher (5–7 pts) than confirmed intraday breach (2–4 pts). Gap-day breach retains original 6–10 pts. `_gap_mult > 1.0` is the gap indicator already in scope.

**MTF**: Daily EMA alignment now gated by daily ADX. ADX 20–40 (building trend) → 8 pts; ADX > 40 (mature/extended) → 5 pts; ADX < 20 (early) → 5 pts. Weekly+daily confirmation → 10 pts (unchanged).

**overnight_drift BASELINE weight**: Set to 0.00 (was 0.03). Dimension is already BLOCKED CRITICAL in walk-forward calibration (negative IC in both candidate p=0.009 and execution sources). The 0.03 weight redistributed to trend (0.12→0.13), flow (0.07→0.08), mtf (0.07→0.08). Sum = 1.00.

### Reasoning

Execution IC report (`data/signal_validation_report.json`) showed all four dimensions producing negative IC on the full 177-trade sample, but the quantile breakdown revealed the dimensions *work at the top score end* — the IC was being dragged negative by mid-range scores (e.g. momentum bucket 3: score 7–8, pnl −9.5% vs bucket 5: score ≥10, pnl +45.8%). The root cause was the same for all four: the scorers measured *confirmed* signal state (already extended) rather than *initiating* signal state (early in the move). Each fix targets the specific indicator causing this: exhaustion thresholds for momentum, SD normalization for flow, proximity-first for breakout, ADX maturity gating for MTF. 22 new tests added to `tests/test_signals.py`.

### Constraints

These are scorer changes, not weight changes. IC weights are still accumulated and governed by the existing IC calculator. BASELINE_WEIGHTS is the only baseline file changed.

---

## 2026-05-26 — yfinance carve-out for ES=F / NQ=F futures (v4.49.0)

### Decision

yfinance is approved ONLY in `futures_data.py` for ES=F and NQ=F 5-day returns. All other runtime yfinance uses were removed in v4.31.1 and are prohibited. `tests/test_no_yfinance_runtime.py::_YFINANCE_APPROVED` is the authoritative exception list.

### Reasoning

Alpaca has no futures support (no futures client in `alpaca.data.historical`). FMP Premium has ESUSD but blocks NQUSD. yfinance has both freely. The futures sensor is advisory only — it never affects the core 11-sensor fail-closed count. `futures_data.py` wraps all yfinance calls in `try/except`, returning `(None, None)` on any failure. `live_driver_resolver.py` also wraps the import so it degrades gracefully if the module is missing or the package is uninstalled (e.g. on the intelligence cloud where `requirements.intelligence.txt` excludes yfinance).

### Constraints

- Only `futures_data.py` may import yfinance. Any other file that adds `import yfinance` will fail the broad scan in `test_no_yfinance_runtime.py`.
- Futures sensors must always be fetched AFTER `fetch_ok` is computed so they cannot trigger degraded mode.
- If yfinance breaks (Yahoo API change), the sensor silently returns None. No alert needed — it's advisory.

---

## 2026-05-26 — Customer Event Tape is customer-only, never feeds execution (Sprint M11A)

### Decision

Fresh real-time event evidence (news/catalysts) is captured in a customer-only
artefact, `data/intelligence/customer_event_tape.json`, and reconciled with
price drivers ONLY at the customer Market Map layer. The Event Tape does not
feed `live_driver_resolver.py`, `universe_builder.py`, `handoff_reader.py`,
`bot_trading.py`, PM actions, or any execution path.

### Why this matters

Layers 1–4 of the intelligence pipeline (price drivers → candidates → themes →
universe handoff) must remain deterministic and replayable. If event
intelligence rewrote price drivers, two systems would compete to define what
"the market regime is," and re-running the pipeline against the same prices
would no longer reproduce. Reconciliation belongs at the publisher (the
Market Map), not at the driver layer.

### Implementation

- **NEW** customer-only modules (all SAAS_OUTPUT layer, registered in
  `architecture/layer_boundary.py`):
  - `customer_event_classifier.py` — deterministic 15-category classifier
    (geopolitics, commodities, earnings, M&A, central bank, macro, China/India
    policy, regulation, credit, technology, company shock). Pure function.
    No LLM dependency so golden tests are reproducible.
  - `customer_event_tape.py` — fail-soft writer. Exposes
    `maybe_record_customer_event(...)`.
  - `market_now_reconciler.py` — helper for `market_now_builder.py` only;
    encodes the `(driver, event_type) → conflict_message` matrix.
- **MODIFIED**:
  - `saas_intelligence_output.py` — 10 new approved customer fields
    (`key_events`, `what_changed`, `known_conflicts`, `section_freshness`,
    `sectors`, `themes`, `radar`, `watch_next`, `market_mood`,
    `source_notes`) + new nested-blocked-field guard that rejects banned
    keys anywhere in the payload (e.g. `radar[0].position_size`).
  - `market_now_builder.py` — always reconciles; degraded path still surfaces
    fresh events.
  - `news.py` / `alpaca_news.py` / `catalyst_engine.py` — single fail-soft
    emit hook each. No scoring or dispatch change.

### Boundaries enforced by `scripts/verify_customer_event_tape_safety.py`

- E1: customer_event_tape not imported by execution modules.
- E2: customer_event_tape not imported by universe_builder.
- E3: customer_event_tape not imported by handoff_reader.
- E4: market_now_reconciler imported only by market_now_builder.
- E5: customer_event_classifier imported only by customer_event_tape.
- E6: no yfinance in any M11A module.
- E7: no Mac-only paths in any M11A module.
- E8: persisted tape file contains no banned nested keys.

### What known_conflicts looks like (runtime proof)

Headline: "US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise."

Active price driver: `geopolitical_risk_rising`.

Reconciled Market Map emits:
- "Defence and energy still reflect recent geopolitical risk, but fresh
  de-escalation headlines suggest the risk premium may be fading."
- "Price-based geopolitical risk drivers are still active, but oil is falling
  on de-escalation or peace hopes — the risk premium may be unwinding."

This is the new product behaviour. Previously the Market Map only saw the
price driver — the contradicting event was invisible to customers.

### Why three new files and not two

`customer_event_classifier` is split from the tape so golden tests run with
pure-function determinism (no I/O, no file system). The reconciler is split
from `market_now_builder` so the publisher stays under the 200-line module
guideline. The split was approved by Amit before any code was written.

### Allowlist expansion approval

`saas_intelligence_output._ALLOWED_FIELDS` was tight (11 fields) and its own
comment requires Amit's explicit approval to add fields. Approved scope is
exactly the 10 Sprint M11A names above and no others. The expansion comment
in the file reads: "Approved by Amit for customer-only Market Map fields in
Sprint M11A."

### Ask Decifer

Deferred to a follow-up sprint. `section_freshness.ask_context` carries a
note: "Ask grounding is deferred to a follow-up sprint." When wired,
`voice_context_builder.py` will gain a customer-only path that loads the
Market Map.

---

## 2026-05-24 — yfinance final hardening (v4.31.4)

**Closes the remaining 3 active yfinance import paths that were not removed in v4.31.3.**

**Files hardened**:
- `signals/catalyst_screen.py`: Removed yfinance fallback from `_fetch_info()`. FMP-only. Fail closed.
- `signals/options_anomaly.py`: Removed yfinance fallbacks from `_fetch_chain()` and `_current_price()`. Alpaca-only. Fail closed.
- `scripts/factor_analysis.py`: Replaced `yf.download()` with `alpaca_data.fetch_bars_range()`. Missing symbols logged and skipped, not silently absent.

**Risk-sizing fail-closed hardening** (`portfolio_optimizer.py`):
- `_calculate_volatility()` now returns `None` instead of `0.20` default when data unavailable.
- A `0.20` default could understate volatility for high-risk symbols, silently increasing their position size.
- `calculate_weights()` excludes symbols with `None` volatility from the output dict entirely. The caller receives only symbols for which a real volatility was measured. Empty dict if all symbols missing.
- Missing symbols are logged at WARNING level with explicit message.

**Broad guard test added** (`test_no_yfinance_runtime.py`):
- Now walks all active source directories (excl. archive, tests, docs, Chief-Decifer-recovered, venv) and asserts no file contains an active `import yfinance` or `from yfinance` statement.
- Covers future re-introduction automatically — not just the 4 specific files from v4.31.3.
- 9 specific file checks + 1 broad scan + 1 module attribute binding check = 11 tests total.

**`_safe_download` test rewrite** (`test_safe_download.py`):
- Old tests described a 3-layer contract (Alpaca → yfinance → fail). yfinance layer was removed in v4.31.3.
- Rewritten to document the 2-layer contract: Alpaca → fail closed. Includes test asserting no `yf` attribute on the signals module.

**New portfolio optimizer tests** (5 new tests in `test_portfolio_optimizer.py`):
- `_calculate_volatility` returns None on Alpaca failure (not 0.20)
- `_calculate_volatility` returns None on empty DataFrame
- `calculate_weights` excludes symbol with None volatility
- `calculate_weights` returns empty dict when all volatility missing
- Confirms no silent sizing increase from missing data

**Task C verification** (`orders_core.py`):
- `prices["Alpaca"] = yf_price` — confirmed correct. `yf_price` is a retained local variable name bound to `price` (the Alpaca-sourced price passed to `execute_buy`). The "Alpaca" label is accurate. No source confusion.

---

## 2026-05-23 — yfinance removed from all production paths (v4.31.3)

**Decision**: yfinance is not approved for Decifer Trading runtime, fallback, enrichment, analytics, or validation paths. Removed entirely. Fail closed when data unavailable.

**Root cause of removal**: yfinance had accumulated in 10 runtime usages across 4 files (`signals/__init__.py`, `portfolio_optimizer.py`, `bot.py`, `orders_core.py`). As a free, unofficial library it has no SLA, rate limits, and data structure changes without notice. The system already has two paid, reliable alternatives (Alpaca, FMP) that are preferred for all data needs.

**What was removed** (all runtime usages):
- `signals/__init__.py`: `_safe_download()` Layer 2 fallback, `fetch_multi_timeframe()` Layer 3 5m-bar fallback, PEAD dimension `get_earnings_dates()` call
- `portfolio_optimizer.py`: `CorrelationTracker._fetch_returns()`, `RiskParitySizer._calculate_volatility()`, `PortfolioVaR._get_portfolio_returns()`
- `bot.py`: `logging.getLogger("yfinance").setLevel(logging.CRITICAL)` suppression line
- `orders_core.py`: price label `"yfinance"` renamed `"Alpaca"`

**Replacements** (fail-closed):
- PEAD dimension: replaced `yf.Ticker.get_earnings_dates()` with `fmp_client.get_earnings_surprise_history()` (new function using FMP `/earnings` endpoint — computes `surprise_pct = (actual - estimate) / abs(estimate) * 100`)
- Portfolio optimizer: replaced `yf.download()` with `alpaca_data.fetch_bars()` per-symbol loop; returns 0.20 volatility default on Alpaca failure (documented fallback, not silent)
- `_safe_download()`: removed Layer 2 entirely — returns None if Alpaca fails, callers handle gracefully
- `fetch_multi_timeframe()`: removed Layer 3 — 5m bars unavailable if Alpaca fails; caller gets None

**Non-production exceptions** (explicitly annotated as research-only):
- `signals/catalyst_screen.py`: yfinance fallback kept for standalone research use only. Docstring updated: "NON-PRODUCTION RESEARCH TOOL — standalone script only. Not imported by any production runtime module."
- `signals/options_anomaly.py`: same annotation applied.

**Guard test**: `tests/test_no_yfinance_runtime.py` — 4 tests verifying that `signals/__init__.py`, `portfolio_optimizer.py`, `bot.py`, and `orders_core.py` contain no yfinance strings. Runs as part of full pytest suite.

**CLAUDE.md and ARCHITECTURE.md updated** to reflect yfinance as removed.

---

## 2026-05-23 — Options pipeline rewrite: real volume, provider contract, expression router

**Root cause of 15-day non-execution**: `alpaca_options.py` used `bid_size + ask_size` (quote size) as a traded volume proxy and multiplied by 5 to fabricate open interest. This locked the unusual-volume ratio at exactly 0.200 for every symbol (below the 0.250 threshold), guaranteeing every options entry was blocked.

**FMP provider audit**: All FMP options endpoints (`/v3/option-chain/`, `/v4/options/`, `/v3/historical/options/`) returned 404 or 403 on the current API key. FMP is classified `NOT_USABLE_FOR_OPTIONS` in code. No FMP code exists in the options runtime path.

**Alpaca data contract**: The Python SDK `OptionHistoricalDataClient` objects do NOT expose `dailyBar`. Using `raw_data=True` returns raw dicts where `dailyBar.v` = real traded contracts today, `dailyBar.n` = real trade count, `prevDailyBar.v` = prior day volume. Open interest is not available from Alpaca at all — it is always `None`. Never fabricated.

**Provider decision tree** (locked):
1. FMP → skip (`NOT_USABLE_FOR_OPTIONS`)
2. Alpaca `raw_data=True` → `PARTIAL_FLOW` (real volume, no OI)
3. None → null provider (no entry)

**Unusual flow definition**:
- `OI_RATIO` path (when OI available): `volume / OI ≥ 0.25` — NOT currently available
- `VOLUME_EXPANSION` path (Alpaca PARTIAL_FLOW): `today_vol / max(prev_vol, 50) ≥ 1.75`

**Expression router decision** (locked):
- Common stock is the default expression
- Options require ALL of: directional signal (CALL_BUYER or PUT_BUYER) + confirmed unusual flow + approved provider + option score beats common score by ≥ 10 points
- If common gates pass but option gates don't: route COMMON (execute stock trade)
- If neither: route NO_TRADE

**What was removed** (fabricated data — banned):
- `oi = volume * 5` (synthetic OI — mathematically locked ratio at 0.200)
- `volume = bid_size + ask_size` (quote size used as traded volume)
- All yfinance calls from options files (`_get_nearest_expiry`, `_get_earnings_days`, max pain calc)
- `cp_ratio`, `call_oi`, `put_oi`, `max_pain` fields from scanner signal (all required fabricated data)

**New modules**:
- `options_provider.py`: single-responsibility flow data fetcher. Public API: `get_options_flow_data(symbol, min_dte, max_dte) → OptionsFlowData | None`. All fields carry explicit provenance (source labels).
- `expression_router.py`: COMMON/OPTION/NO_TRADE routing. `route_expression(signal, flow_data, regime, portfolio_state) → ExpressionRoute`.

**Test coverage**: 31 new tests (11 provider, 7 flow, 13 router). Full suite passes with pre-existing 17 failures unchanged.

---

## 2026-05-22 — Migration: rotation_live_v1 → Portfolio Management Engine

**Decision**: Retire the G1-G9 rotation waterfall entirely and replace it with a deterministic Portfolio Management Engine (`pm_engine.py`, `pm_thesis.py`, `pm_rails.py`).

**Root cause of old system failure**: `rotation_live_v1` never fired because it framed the entire PM problem as "exit one weak position to fund one blocked buy." This is too narrow. The correct framing is: continuously evaluate each held position across multiple possible actions using thesis state, scoring, and safety rails.

**Critical G7 fix**: Old G7 blocked any action on a position whose full notional exceeded 2% NLV. New Rail 7 checks the *proposed action notional* (e.g. a trim amount), not the full position. A 5% NLV position can provide a 1% trim without being blocked.

**What was built**:
- `pm_thesis.py` (~130 lines): PMPosition dataclass, ThesisStatus enum (STRENGTHENING/INTACT/PLAYED_OUT/DECAYING/BROKEN/UNKNOWN), `build_position()`, `_classify()`. Single responsibility: position enrichment.
- `pm_rails.py` (~120 lines): 10 safety rails applied *after* action selection. DO_NOTHING bypasses all rails. Rail 7 checks proposed_notional, not market_value.
- `pm_engine.py` (~250 lines): Public `evaluate()` entry point, ActionType enum (HOLD/ADD/DCA/TRIM/FULL_EXIT/ROTATE/DO_NOTHING), PMAction dataclass, action generation, action scoring with churn penalty and cost hurdle, execute_sell/qty_override for TRIM, decision log at `data/pm_engine/decisions.jsonl`.
- `pm_observability.py`: Migrated from `rotation_observability.py`. Writes to `data/pm_engine/margin_blocks.jsonl` and `data/pm_engine/position_snapshots.jsonl`.
- `tests/test_pm_engine.py`: 17 tests — all 11 spec acceptance tests + 5 safety rail unit tests + import guard test.
- `config.py`: `ROTATION_LIVE_*` constants replaced with `PM_ENGINE_*` constants.
- `orders_core.py`: `_rlv1_info` / `rotation_live_v1` call site replaced with `_pm_info` / `pm_engine`. Both LONG and SHORT paths migrated from `rotation_observability` to `pm_observability`.
- `bot_trading.py`: Scan cycle call to `pm_engine.evaluate(trigger="scan_cycle", ...)` added after Track B and position refresh.
- `bot_dashboard.py`: `/api/pm` endpoint added. `/api/rotation` tombstoned (returns `{"retired": true}`).
- `static/dashboard.html`: "Rotation" tab renamed "Portfolio Mgmt". View, JS function, API call, table schema all updated.

**What was archived** (not deleted — historical reference):
- `rotation_live_v1.py`, `rotation_observability.py` → `archive/`
- `tests/test_rotation_live_v1.py`, `test_rotation_paper_validation.py`, `test_rotation_shadow_report.py`, `test_rotation_observability.py` → `archive/tests/`
- `scripts/rotation_paper_validation.py`, `scripts/rotation_shadow_report.py` → `archive/scripts/`

**Call sites**: `pm_engine` is triggered from two places:
1. `orders_core.execute_buy()` on `margin_gross_cap_block` (reactive — same deadlock-safe pattern as old _rlv1 call)
2. `bot_trading.py` scan cycle after Track B + position refresh (proactive)

**Feature flag**: `ENABLE_PM_ENGINE=False` — runs in HYPOTHETICAL mode until Amit activates it.

## 2026-05-22 — PME Post-Migration Validation Audit

**Trigger**: Full audit of the PME migration after retirement of rotation_live_v1.

**Bugs found and fixed**:

1. **`feature_flag_off` was a safety rail (wrong layer)** — Rail 1 of `pm_rails.py` blocked all actions with `safety_blocked=True, reason="feature_flag_off"` when the feature flag was off. This caused the next-best fallback loop in `evaluate()` to cascade to `DO_NOTHING` for every position in HYPOTHETICAL mode, making the decision log useless. Root cause: the feature flag is an activation gate, not a market-condition safety check.
   - **Fix**: Removed rail 1 from `pm_rails.py` entirely. The flag check lives only in `_execute()`. Rails now check market conditions only (9 rails, down from 10). The fallback loop in `evaluate()` only runs when `ENABLE_PM_ENGINE=True`. In HYPOTHETICAL mode, the top-scoring action is logged as HYPOTHETICAL regardless of market conditions — stale quote etc. still show as SAFETY_BLOCKED correctly.

2. **`_log()` final_status misclassification** — When flag was off and rail 1 fired, every decision logged as SAFETY_BLOCKED. After removing rail 1, `_log()` simplifies cleanly: SAFETY_BLOCKED = real rail fired, HYPOTHETICAL = flag off or action needs no execution (HOLD/DO_NOTHING), EXECUTED = flag on + rails passed + execution action.

3. **PLAYED_OUT thesis did not generate TRIM** — Spec says `PLAYED_OUT → FULL_EXIT, TRIM`. Code only generated TRIM for DECAYING or oversized. HOLD was generated for PLAYED_OUT, which then outscored FULL_EXIT (20 vs 5-8) causing the engine to recommend HOLD on a thesis that has run its course.
   - **Fix**: `TRIM` generation condition expanded to include `ThesisStatus.PLAYED_OUT`. `HOLD` generation explicitly excludes `PLAYED_OUT` (alongside `BROKEN`). After fix: AMZN (PLAYED_OUT, 60h, score stable) correctly generates FULL_EXIT + TRIM, and FULL_EXIT is selected.

**Design decisions locked by audit**:

4. **FULL_EXIT on large positions blocked by rail 6 (notional cap)** — A BROKEN thesis position at 5% NLV proposes FULL_EXIT with `proposed_notional = market_value = $45,500 > 2% NLV cap ($20k)`. Rail 6 blocks it; in live mode the fallback selects TRIM. This is intentional: even for broken positions, the 2% NLV cap applies to force gradual exits. Rationale: a sudden full exit on a large position is a large market impact. Trim down over successive cycles. This is NOT changed.

5. **archive/ is namespace-importable if sys.path is manipulated** — Python 3 treats directories as namespace packages. `sys.path.insert(0, 'archive'); import rotation_live_v1` works. The production runtime never adds `archive/` to sys.path, so this is low risk. The import guard test (`test_rotation_live_v1_not_imported_by_live_runtime`) remains the primary enforcement mechanism.

**Tests added**: `test_hypothetical_status_when_flag_off`, `test_do_nothing_rationale_includes_thesis_context` — bringing PM engine test count to 19.

**Final rail count**: 9 (was 10 — rail 1 `feature_flag_off` removed).

---

## 2026-05-22 — PME Score Wiring Fix + Decision Log Enrichment

**Trigger**: Post-activation audit found all 103 post-restart records are `DO_NOTHING HYPOTHETICAL` with `score_delta=0`. Root cause: `build_position()` was defaulting `current_score = candidate_scores.get(symbol, entry_score)` — if `candidate_scores` is empty (no scan output available at evaluate() time), every position gets `score_delta=0` and classifies as `THESIS_INTACT`.

Additionally, found that `test_hypothetical_mode_no_execute_sell` was writing AAPL/NLV=100k records to the production `data/pm_engine/decisions.jsonl` because it never redirected the log file path. This was the root cause of the "NLV=100k pollution" in the live log.

**Bug 1 — Score resolver missing**: All current-score lookups went directly to the passed `candidate_scores` dict. No fallback to a persistent cache of last-known scores. When `pipeline.all_scored` is empty, every position gets `score_delta=0` even if a prior scan cycle had a valid score for that symbol.
- **Fix**: New `pm_score_resolver.py` — 3-tier score resolution (CYCLE_CANDIDATES → PM_SCORE_CACHE → ENTRY_SCORE_FALLBACK). Cache is in-memory + persistent (`data/pm_engine/score_cache.json`). `evaluate()` calls `pm_score_resolver.update_cache()` whenever candidate_scores is non-empty. `build_position()` calls `pm_score_resolver.resolve()` instead of the dict lookup.

**Bug 2 — Silent INTACT classification on stale data**: When score_source == ENTRY_SCORE_FALLBACK, `score_delta` is always 0 and thesis classification is always INTACT (since no condition fires on delta=0). This is misleading — INTACT implies the thesis is actively confirmed; ENTRY_SCORE_FALLBACK means we simply don't know.
- **Fix**: Added `INTACT_DEGRADED = "THESIS_INTACT_DEGRADED"` to `ThesisStatus`. `build_position()` demotes INTACT → INTACT_DEGRADED when `score_source == "ENTRY_SCORE_FALLBACK"`. INTACT_DEGRADED generates HOLD (safe conservative action) but NOT DCA or ADD (conviction cannot be confirmed on degraded data).

**Bug 3 — NLV startup race not guarded**: `evaluate()` skipped on `nlv is None` but not on `account_values_updated_at is None` (IBKR not yet connected). A valid-looking NLV of unknown provenance could slip through.
- **Fix**: Added `_nlv_is_ready(nlv, cfg)` guard in `evaluate()`. Requires both `nlv > 0` AND `account_values_updated_at is not None` AND freshness within `PM_ACCOUNT_MAX_AGE_S`. If not ready, writes a single `PM_SKIPPED` record (event, not position) and returns. No position decision records are written until IBKR confirms account values.

**Bug 4 — Test file pollution**: `test_hypothetical_mode_no_execute_sell` wrote to the real `data/pm_engine/decisions.jsonl` because it never monkeypatched `_DECISIONS_FILE`. This produced AAPL/NLV=100k records in the production log, misidentified as a live NLV bug.
- **Fix**: Added `tmp_path` and `monkeypatch.setattr(pm_engine, "_DECISIONS_FILE", ...)` to the test.

**ADD/DCA = RECOMMENDATION (not EXECUTED)**: Locked design decision. ADD and DCA are advisory actions — no broker call is made. `_log()` now uses four statuses: EXECUTED (TRIM/FULL_EXIT/ROTATE), RECOMMENDATION (ADD/DCA), SAFETY_BLOCKED (rail fired), HYPOTHETICAL (flag off or HOLD/DO_NOTHING). See prior audit entry for reasoning.

**Decision log enriched**: Each record now includes:
- `score_source` — CYCLE_CANDIDATES | PM_SCORE_CACHE | ENTRY_SCORE_FALLBACK
- `data_quality` — OK | DEGRADED_SCORE
- `entry_price`, `current_price` — for breakeven and drift analysis
- `position_pct_nlv` — for sizing correlation with outcome
- `action_pct_nlv` — proposed_notional / nlv for relative action sizing
- `market_regime` — from bot_state.dash["regime"]["regime"]
- `candidate_count` — how many candidates were in the current cycle
- `candidate_source_summary` — "cycle_N" or "cycle_0_cache_used"

**DO_NOTHING now self-describing**: Rationale includes `[DEGRADED: score source is entry fallback — score_delta unreliable]` tag when score_source == ENTRY_SCORE_FALLBACK, and `[score from PM cache, not current cycle]` when using PM_SCORE_CACHE.

**Files created**: `pm_score_resolver.py`.
**Files modified**: `pm_thesis.py`, `pm_engine.py`, `tests/test_pm_engine.py`.
**Tests added**: 8 new tests — bringing PM engine test count to 27.
**Tests fixed**: `test_hypothetical_mode_no_execute_sell` — now properly isolated (no production file writes).

---

## 2026-05-21 — ML Sprint 3.7: Candidate Source Accuracy + Canary Baseline + Old 50-Trade Gate Retired

**Decision**: Move `write_observations()` to `bot_trading.py` after handoff enrichment so `candidate_source` is accurate. Expose `rank_map`, `ranking_total`, `vix` on `SignalPipelineResult`. Update `SCHEMA_VERSION` to `sprint37_v1`. Add `--since-scan-id` baseline to canary mode. Explicitly retire the legacy 50-trade ML activation gate.

**What was built**:
- `signal_pipeline.py`: Removed `write_observations()` call. Added `rank_map: dict`, `ranking_total: int`, `vix: float` to `SignalPipelineResult` so callers have all fields without re-reading config.
- `bot_trading.py`: Added `write_observations()` call immediately after the handoff enrichment loop that promotes `candidate_source` to `"handoff_reader"`. This is the correct location — observations now record the final promoted source rather than the conservative `"scanner"` stamp that `signal_pipeline` applies.
- `ml_observation_writer.py`: `SCHEMA_VERSION = "sprint37_v1"`.
- `scripts/ml_observation_health_check.py`: `--since-scan-id SCAN_ID` argument added. Canary duplicate check is scoped to records with `scan_id >= SCAN_ID`. Integrity checks (missing fields, score mutation) still run on ALL records. Full summary unchanged. Fixes permanent CANARY FAIL caused by 2026-05-20 startup artifact `20260520T133247_AAPL`.
- `tests/test_ml_sprint37_source_accuracy.py`: 16 tests (T1–T16). All pass.

**Legacy 50-trade ML activation gate — RETIRED**:
The old `phase_gate.py` gate (≥50 closed trades → activate `ml_engine.py`) is retired. `ml_engine.py` was deleted in Sprint 3 (ML Clean-Slate Sprint 1). The 50-trade count gate and the `phase_gate.py` gating mechanism no longer exist in the codebase. Any documentation or summary language referencing "ML engine activation (gate met: 50+ trades)" is incorrect and must be replaced with the canonical training-readiness gate below.

**Canonical training-readiness gate (replaces old 50-trade gate)**:
ML activation is not yet eligible. The new gate requires `canonical_learning_dataset.jsonl` to contain at least 200 `ml_eligible=true` exact closed-trade records satisfying ALL of:
- `join_quality="exact"` — observation_id linked from observation → ORDER_INTENT → closed outcome
- `trade_taken=true`, `order_filled=true`, `position_closed=true`
- `realised_pnl_pct` present and not null
- At least 2 distinct regimes represented
- No single regime above 75% of eligible records
- WIN / LOSS / BREAKEVEN distribution reported and non-degenerate
- No leakage fields in model input features
- `candidate_source` accuracy validated (no `"unknown"` records in post-Sprint-3.7 scans)
- `observation_id` linkage validated end-to-end (observation → ORDER_INTENT → closed trade)

Research-only experiments may be allowed earlier but must be explicitly labelled research-only, not production, not shadow, not eligible for live influence. No model training. No model loading. No prediction. No advisory scoring. No live trading behaviour changes.

**Current status**: Pending. Post-Sprint-3.7 live bot cycles needed to generate `sprint37_v1` records with accurate `candidate_source`. No exact-joined closed-trade records exist yet.

**Files changed**: `signal_pipeline.py`, `bot_trading.py`, `ml_observation_writer.py`, `scripts/ml_observation_health_check.py`, `tests/test_ml_sprint36_identity_linkage.py`.
**Files created**: `tests/test_ml_sprint37_source_accuracy.py`.
**Live trading impact**: None. All changes are observation-side only. No scoring path, order path, or execution path touched.

---

## 2026-05-20 — ML Clean-Slate Sprint 3: Offline Outcome Joiner and Canonical Learning Dataset Builder

**Decision**: Build the offline outcome joiner that links signal observations to realised trade outcomes, producing the canonical learning dataset. No model training, no model loading, no live influence.

**What was built**:
- `scripts/ml_outcome_joiner.py` — stdlib-only offline script (~370 lines). Reads `data/ml/ml_observations.jsonl` + `data/trade_events.jsonl` + `data/training_records.jsonl` + `data/ml/closed_trade_training_ledger.jsonl`. Writes `data/ml/canonical_learning_dataset.jsonl` + `data/ml/canonical_learning_dataset_summary.json`.
- `tests/test_ml_outcome_joiner.py` — 20 tests (T1–T20). All pass.

**Join key hierarchy**:
1. **Exact join** (`join_quality="exact"`): ORDER_INTENT has `observation_id` matching the observation record. This is the Sprint 2 linkage field added to ORDER_INTENT as a top-level key.
2. **Fallback join** (`join_quality="fallback"`): No `observation_id` in ORDER_INTENT (pre-Sprint 2 records). Match by symbol + direction + ORDER_INTENT timestamp within ±300 seconds of observation timestamp.
3. **No match** (`join_quality="no_match"`): Observation with no trade. Written as a pass row (`trade_taken=False`, `outcome_label=None`).

**ml_eligible=True** requires ALL: observation_id exists, signal_scores not empty, direction LONG/SHORT, trade_taken=True, order_filled=True, position_closed=True, realised_pnl_pct not null, join_quality="exact". Fallback-joined records are stored but never eligible for training (origin cannot be verified with certainty).

**Outcome label rules**: `pnl_pct > 0 → WIN`, `pnl_pct < 0 → LOSS`, `pnl_pct == 0.0 → BREAKEVEN`. BREAKEVEN is not WIN. Non-traded pass rows always have `outcome_label=None`.

**LEAKAGE_FIELDS** (stored in output but never model inputs): `hold_minutes`, `exit_price`, `exit_reason`, `realised_pnl`, `realised_pnl_pct`, `outcome_label`, `position_closed`, `exit_timestamp`. These are post-outcome fields — using them as model inputs would replicate the leakage bug in the deleted legacy engine.

**Outcome source priority**: `closed_trade_training_ledger.jsonl` (richer schema, newer) takes precedence over `training_records.jsonl` when both have the same `trade_id`.

**Expected output now (2026-05-20)**: 0 canonical records. The `ml_observations.jsonl` file does not yet exist — `ml_observer_enabled=False` in config. All existing trades predate Sprint 2's observation writer. The script correctly handles the empty-observations case and writes an empty dataset without error.

**Hard constraints respected**: no model training, no model loading, no score influence, no order routing changes, no runtime import by the live bot, stdlib only.

**Files created**: `scripts/ml_outcome_joiner.py`, `tests/test_ml_outcome_joiner.py`.

**Tests**: T1 (no ML imports), T2 (empty obs), T3 (missing file), T4 (pass row), T5 (exact join), T6 (fallback join), T7 (full chain ml_eligible=True), T8 (missing signal_scores), T9 (neutral direction), T10 (fallback not eligible), T11 (LEAKAGE ∩ FEATURE = ∅), T12 (WIN/LOSS/BREAKEVEN), T13 (BREAKEVEN ≠ WIN), T14 (hold_minutes not in features), T15 (summary counts), T16 (output files created), T17 (pass rows null label), T18 (null pnl_pct), T19 (training_records source), T20 (ledger precedence).

---

## 2026-05-20 — ML Clean-Slate Sprint 2: Main-Frame Signal Observation Writer

**Decision**: Build the first real component of the controlled learning loop — a lightweight main-frame signal observation writer attached to the production signal pipeline.

**What was built**:
- `ml_observation_writer.py` — stdlib-only module (~170 lines). Inert when `ml_observer_enabled=False`. No ML imports, no model loading, no score changes. Appends one JSONL record per scored candidate to `data/ml/ml_observations.jsonl`.
- `signal_pipeline.py` — observation writer attached between steps 7 (Signal objects built) and 8 (signals_log append) in `run_signal_pipeline()`. All of `all_scored` (including below-threshold candidates) is passed to the writer, eliminating selection bias.
- `signal_dispatcher.py` — `observation_id` and `scan_id` added as top-level kwargs to `execute_buy()` and `execute_short()` calls, so ORDER_INTENT records now carry `observation_id` at the top level (previously nested inside `agent_outputs`).

**Why all_scored (not just above-threshold signals)**:
The architecture doc (§2.1) requires observations for ALL candidates whether or not a trade is taken, to eliminate the selection bias that plagued the legacy engine's executed-trade-only training set. Signal objects (from `_scored_to_signals`) only cover above-threshold candidates. `all_scored` covers every scored candidate.

**Why top-level observation_id in ORDER_INTENT**:
`observation_id` was already present nested inside `agent_outputs["observation_id"]`. The Sprint 3 outcome joiner needs `record["observation_id"]` directly, not `record["agent_outputs"]["observation_id"]`. Adding it as a top-level kwarg via `**intent_extras` makes the join key directly accessible without nesting.

**Observation record schema (sprint2_v1)**:
`schema_version`, `timestamp_utc`, `session_date`, `scan_id`, `observation_id`, `symbol`, `direction`, `candidate_source`, `base_score`, `live_score_after_observer`, `live_score_unchanged=True`, `ranking_position`, `ranking_total`, `signal_scores`, `dim_*` (flattened), `regime`, `vix`, `time_of_day`, `day_of_week`, `is_after_hours`, `passed_base_threshold`, `ml_observer_enabled`, `ml_score_influence_enabled`, `ml_inference_eligible=False`, `exclusion_reason`, `order_intent_linked=False`.

**Live trading impact**: None. `ml_observer_enabled=False` by default. No scores, rankings, order eligibility, sizing, or execution paths changed.

**Tests added**: `tests/test_ml_observation_writer.py` — 20 tests (T1–T20). All pass.

**Files created**: `ml_observation_writer.py`, `tests/test_ml_observation_writer.py`.
**Files modified**: `signal_pipeline.py` (step 7b insertion), `signal_dispatcher.py` (top-level linkage kwargs).

---

## 2026-05-20 — ML Clean-Slate Sprint 1: Legacy Engine Removed, Controlled Learning Architecture Defined

**Decision**: `ml_engine.py` deleted in full. All saved model files quarantined. New controlled learning architecture defined in `docs/ml_controlled_learning_architecture.md`. No ML influence is active.

**What was removed**:
- `ml_engine.py` (1000 lines): `TradeLabeler`, `DeciferML` (RandomForest + GradientBoosting), `SignalEnhancer`, `RegimeClassifier`, `WeeklyReportGenerator`. Contained confirmed leakage (holding_minutes importance = 0.275), broken inference path (signal dims defaulted to 0 at prediction time), and a score formula that could implicitly block entries even with `ml_can_block_entries=False`.
- `tests/test_ml_engine.py`: Tests for the deleted engine.
- 8 legacy ML config keys: `ml_enabled`, `ml_min_trades`, `ml_retrain_interval`, `ml_confidence_weight`, `ml_models_dir`, `ml_live_multiplier_enabled`, `ml_can_block_entries`, `ml_can_size_positions`.
- ML startup hook in `bot.py` (lines 822–833).

**What was quarantined** (not deleted — preserved as evidence):
- `data/models/classifier.pkl`, `regressor.pkl`, `scaler.pkl`, `features.pkl`, `metadata.json` → `data/quarantine/leaky_ml_models_2026_05_20/`
- `QUARANTINE_README.md` explains leakage, prohibits any use, documents metadata confirming the contamination.

**What replaced the old config keys**:
```python
"ml_observer_enabled": False,        # Shadow evidence observer (Stage 1) — not yet built
"ml_score_influence_enabled": False, # Score adjustment from ML (Stage 3) — requires explicit Amit approval
"ml_data_dir": "data/ml",           # Root dir for canonical evidence ledgers
```

**Why the engine had to be deleted, not patched**:
1. **holding_minutes leakage is in saved models, not just code**: Even after fixing `prepare_data()`, all existing `.pkl` files were trained with the leaky feature. Any inference call would return win_prob values biased by post-outcome data.
2. **Inference gap**: `SignalEnhancer.enhance_score()` never passed `dim_*` signal scores to `predict()`. The model trained on 18 signal dimensions but always predicted with them set to 0. No patch could fix a model trained under that condition.
3. **Effective training N = 180**: Only 180 of 406 eligible records have `signal_scores`. The model was trained on a biased subset without the system knowing.
4. **Score suppression risk**: `base_score * (0.5 + win_prob)` allows 0.5× score compression regardless of the `ml_can_block_entries` config flag.

**New architecture: controlled self-improving** (not yet implemented):
- Collects evidence passively via side-effect writers (observation records before each trade decision).
- Retrains offline, never during a scan cycle.
- Shadow-validates candidate models before any live influence.
- Live score influence requires explicit Amit approval + `ml_score_influence_enabled = True` set manually.
- No model may be auto-promoted. No model may reduce a score below `base_score` until 90+ days validated.
- Full specification in `docs/ml_controlled_learning_architecture.md`.

**Proof tests (permanent regression suite — `tests/test_ml_legacy_removed.py`)**:
T1: ml_engine.py file deleted. T2: enhance_score cannot be imported. T3: no pkl in data/models/, quarantine README exists. T4: no production file imports ml_engine. T5: legacy config keys absent, new reserved keys default False. T6: legacy score formula absent from production code. T7: holding_minutes not in ML feature builder paths. T8: evidence files (training_records.jsonl, closed_trade_ledger, signals log) preserved. T9: config and training_store load without ml_engine. T10: orders_core and orders_state have no ML references.

**Evidence preserved (explicitly not deleted)**:
`data/training_records.jsonl`, `data/ml/closed_trade_training_ledger.jsonl`, `data/signals_typed.jsonl`, all trade ledgers, order records, execution records, Apex logs, IC reports, signal validation reports.

**Files changed**: `bot.py`, `config.py`, `learning.py`, `requirements.txt`, `requirements-prod.txt`, `scripts/audit_trade_ledger_data_path.py`, `tests/test_regime_router.py`, `tests/test_reconnect.py`, `tests/test_trade_data_contract.py`, `tests/test_audit_trade_ledger_data_path.py`.

**Files created**: `tests/test_ml_legacy_removed.py`, `docs/ml_controlled_learning_architecture.md`, `data/quarantine/leaky_ml_models_2026_05_20/QUARANTINE_README.md`.

**Live trading impact**: None. ML was `ml_enabled=False` before this sprint. No scoring path, order path, or execution path touched.

---

## 2026-05-20 — Walk-Forward Weight Calibration: Candidate IC Primary, Execution IC Advisory

**Decision**: Candidate IC (from `ic_weights.json` / `ic_weights_live_history.jsonl`, 36k+ scanned candidates) is the primary source for weight calibration. Execution IC (from `data/signal_validation_report.json`, 177 usable trades) is advisory only — it may cap or flag a weight, but must never increase any weight above the candidate-IC-derived level.

**Calibration rules (locked)**:
1. Candidate IC derives proposed weights via `normalize_ic_weights()`.
2. Execution IC is advisory only — cap/flag permitted, increases prohibited.
3. overnight_drift: BLOCKED CRITICAL. Negative in both sources (candidate −0.076 consistent across 23 history entries; execution −0.199, p=0.009 statistically significant). Weight locked at 0.
4. Sign-flip (candidate positive, execution negative, not significant p ≥ 0.05): FLAG for review, preserve candidate weight unchanged.
5. Sign-flip (candidate positive, execution negative, significant p < 0.05 with n ≥ 30): CAP proposed weight at BASELINE_WEIGHTS[dim].
6. Inactive (both sources zero): weight = 0, excluded from calibration.

**Current proposal result**: Proposed weights are identical to candidate IC weights. No execution IC result is strong enough to trigger an advisory cap or block (other than overnight_drift which was already 0). Flagged for review (sign-flip not significant): breakout, news, reversion, short_squeeze, social, trend. These sign flips are expected from selection bias on 177 executed trades vs 36k+ scanned candidates — they do not indicate the signals are broken.

**Why candidate IC is primary**: Executed-trade IC is structurally biased — it reflects only the 177 trades the system chose to enter, which are disproportionately trades where the entering signals scored high. This creates artificial upward bias for dimensions that drove entry decisions and artificial downward bias for dimensions that were less determinative. The candidate IC covers all scanned stocks regardless of whether Decifer traded them, giving an unbiased view of predictive power.

**Activation**: `data/proposed_calibrated_weights.json` is a proposal only. `ic_weights.json` is unchanged. Activation requires explicit Amit approval. Scripts: `scripts/signal_validation_report.py`, `scripts/walkforward_calibration_report.py`.

---

## 2026-05-20 — Scanner-Level HMM Replacement: "Replace Entirely" Directive Superseded

**Decision**: The original directive (2026-04-01) stating "HMM replaces VIX-proxy entirely when the gate is met" is formally superseded. The two-layer regime architecture now in production is intentional and locked.

**Two-layer architecture (locked)**:

- **Structural gating layer** — `scanner.get_market_regime()`: VIX-proxy 6-state classifier (TRENDING_UP / TRENDING_DOWN / RELIEF_RALLY / RANGE_BOUND / CAPITULATION / UNKNOWN). Real-time intraday VIX + SPY/QQQ 200d MA + breadth data. Hard execution gates: CAPITULATION blocks all entries (`position_size_multiplier = 0.0`), SHORT blocked in TRENDING_UP, SWING/POSITION removed in CAPITULATION, RELIEF_RALLY triggers 0.5× LONG size cap. Scanner failure modes are handled through stale fallback or UNKNOWN routing, with no-TTL stale cache retained as a future scanner hardening item.
- **Weight routing layer** — `_resolve_regime_router(vix, hurst, hmm)`: 3-signal majority vote combining VIX vote, Hurst DFA, and HMM advisory. Determines momentum vs mean_reversion weight multipliers (1.3×/0.7×) for signal dimensions. Probabilistic consensus. Latency-tolerant — daily bars only.

**Why scanner-level HMM replacement is not recommended**:

1. **Flash-crash latency**: VIX spike threshold fires intraday (1h change > 20%). HMM uses daily close data and cannot detect an intraday crash on the day it happens. CAPITULATION must remain VIX-driven.
2. **RELIEF_RALLY preservation**: RELIEF_RALLY is a real market phase (bear-market bounce) that triggers a hard 0.5× LONG size cap. With 2 HMM states, RELIEF_RALLY either disappears entirely or requires a new hybrid state. Disappearing the cap means oversized longs during bear-market bounces.
3. **Label continuity**: All 406 training records carry VIX-proxy `entry_regime` labels. Switching to HMM labels mid-stream creates a training set split that degrades ML quality when Phase C/D activates.
4. **HMM signal type mismatch**: HMM is a probabilistic slow-signal — its strength is multi-day consensus. Forcing it into hard binary execution decisions (block/allow) misuses the signal type.

**Phase B final status**: HMM advisory active in signal weight router. Scanner remains VIX-proxy. Scanner-level replacement: closed as not recommended. Roadmap item `03-hmm-regime-detection.md` updated accordingly.

---

## 2026-05-20 — Phase B: HMM Regime Gate Activation

**Gate met**: 406 eligible training records (ml_eligible=True or absent) ≥ 200 threshold.

**Decision**: Activate HMM advisory participation in the signal weight router. Add `gate_min_eligible_trades: 200` to `config["hmm_regime"]`. Add runtime check in `get_hmm_regime_spy()` calling `training_store.count_eligible()` — returns `{"regime": "unknown", "source": "gate_not_met"}` when below threshold so degraded or absent training data cannot silently activate the model.

**Architecture (advisory, not replacement)**:
The HMM participates in `_resolve_regime_router(vix, hurst, hmm)` — the 3-signal majority vote that determines whether the signal weight multipliers favour momentum or mean_reversion dimensions. This is **separate** from `scanner.get_market_regime()` which is still VIX-proxy only (`config["regime_detector"] = "vix_proxy"`). The roadmap spec says "HMM replaces VIX-proxy" which refers to the scanner-level regime (`scanner.get_market_regime()`). The advisory weight-router activation is a prerequisite step that validates the HMM signal quality before committing to scanner replacement.

**Why count_eligible() not count()**:
The gate intentionally uses `training_store.count_eligible()` which excludes records with `ml_eligible=False` (UNKNOWN trade_type, EXT orphans, MISSING metadata). These records have compromised signal/outcome linkage — they cannot be used to validate regime signal IC. Using raw `count()` would allow degraded records to satisfy the gate without providing real validation.

**Live execution impact**: None. Gate is already met (406 ≥ 200). HMM was `enabled: True` in config before this session. The gate check adds an observable no-op path for future reference when eligible count is below threshold.

**File changes**: `config.py` (+4 lines in hmm_regime block), `signals/__init__.py` (+15 lines gate check in `get_hmm_regime_spy()`), `tests/test_hmm_regime.py` (new, 20 tests).

---

## 2026-04-22 — Full Architecture Audit: 27 Issues, 24 Fixes (CP + BC + RB)

A full architecture trace and three-round deep audit identified 27 confirmed issues across three categories. All 24 implementable fixes were shipped across two sessions. The full issue list and fix rationale is in `docs/PROCESS_ARCHITECTURE.md`. Key decisions logged below.

### Cycle Position (5 fixes — CP-1 through CP-5)

- **CP-1**: Options scan now runs before `update_position_prices()` so both use the same live-price moment. Previously ~30s stale divergence between options analysis and PM sizing.
- **CP-2**: Cycle-check REVIEW flags now accumulate into `_cc_review_reasons` and are passed as the PM trigger string. Previously hardcoded to `"cycle_regime_shift"`.
- **CP-3**: Regime re-fetched immediately before `run_all_agents()`. A VIX spike mid-scan no longer causes Agent 4 to size trades at the pre-spike multiplier.
- **CP-4**: Strategy mode recomputed after PM exits complete. PM exits that tip daily P&L past a mode boundary are now reflected before agents run.
- **CP-5**: PENDING and EXITING positions excluded from PM review eligibility. A position entered this cycle cannot receive an EXIT recommendation before IBKR confirms the fill.

### Behaviour Change (9 fixes — BC-1 through BC-8, excluding BC-9 which was verified correct)

- **BC-1**: Agent 4 now validates options instrument against the `options_signals` list before building an order. Opus-proposed options for symbols with no viable contract are downgraded to stock rather than failing silently in `orders_core`.
- **BC-4**: `_extract_risk_approval()` default changed from `+1` to `0` when a symbol is absent from Risk Manager output. A symbol the Risk Manager never evaluated cannot be treated as approved — doing so silently bypassed the veto ceiling.
- **BC-5**: Catalyst Opus prompt note now explicitly warns against double-counting: the score boost is already applied upstream, so Opus must not treat the elevated score as organic signal AND the catalyst flag as additional confirmation.
- **BC-8**: `agent_trading_analyst` (Opus) now receives `fresh_qualified` only. Held positions are already visible in the OPEN POSITIONS block — showing them again in the scored list caused ADD clustering on existing positions.
- **BC-6**: News fetch failure now falls back to stale cache (with `stale: True` flag) rather than zeroing Dimension 7 for the entire batch. One bad network call can no longer flatten all news scores for a cycle.
- **BC-7**: `auto_rebalance_cash()` now calls `log_trade()` after a successful close. Force-closed positions are now in the IC training set; without this, forward return was never calculated and dimension IC was biased toward the normal execution path.
- **BC-2**: `update_positions_from_ibkr(ib)` called immediately before `run_portfolio_review()`. PM now evaluates live IBKR prices, not the pipeline snapshot frozen ~30s earlier.
- **BC-3**: New execution IC stream: every `log_trade(action="OPEN")` writes to `data/execution_ic.jsonl`. The IC calculator can now compute signal IC vs execution IC to measure agent alpha contribution.
- **BC-9**: Sympathy scanner sequencing verified correct — `get_sympathy_candidates()` is synchronous and completes before `_fetch_news()`. No code change required.

### Robustness (9 fixes — RB-1 through RB-9)

- **RB-1**: `_should_run_portfolio_review()` converted from early-return-on-first-trigger to accumulator. All active triggers are returned as a joined string — Opus receives full context instead of one arbitrarily selected trigger.
- **RB-2**: `universe_promoter.py` write to `daily_promoted.json` converted to `tempfile.mkstemp + os.replace()`. Non-atomic writes could corrupt the file and silently drop Tier B for 18 hours.
- **RB-3**: `cancel_orphan_stop_orders()` extended to also cancel LMT SELL (take-profit) orders for symbols with no active position. Previously only caught STP/TRAIL — OCO target legs were left live. Now called from `connect_ibkr()` on every startup.
- **RB-4**: `_recently_closed_lock = threading.Lock()` added to `orders_state.py`. All reads (`_is_recently_closed`, `cleanup_recently_closed`) and all writes in `orders_core.py` now hold this lock. Prevents races between concurrent executions at the cooldown boundary.
- **RB-5**: Options entries now set `transmit=True` immediately (standalone). SL/TP bracket legs are skipped — IBKR does not support OCO bracket structure for options. Options positions exit via PM only.
- **RB-6**: `_THRESHOLD_HISTORY` persisted to `data/threshold_history.json`. Loaded on module import (entries older than 30 min discarded). Saved atomically after every `_apply_persistence_gate()` call. Bot restarts no longer zero marginal signals' persistence history.
- **RB-7**: `_ic_weights_lock = threading.Lock()` added to `ic/storage.py`. `get_current_weights()` holds it during JSON read; `update_ic_weights()` holds it only during `os.replace()`. Eliminates same-process race between weekly review write thread and main scan loop.
- **RB-8**: Overnight research thread writes `data/overnight_notes.done` sentinel on success. `agent_trading_analyst` checks for sentinel before injecting notes — absent sentinel means thread incomplete; stale notes are skipped rather than silently injected.
- **RB-9**: `run_weekly_review()` now separates closed trades into complete (forward_return computed) and pending-IC. Performance metrics run on complete trades only (falls back to all if none complete). Pending count surfaced to Opus in the prompt.

### Deferred / Non-Issues
- **#22** (Config threshold cached at agent entry): CONFIG doesn't mutate mid-scan — functionally a no-op. Not implemented.
- **#26** (log_trade exit captures current scores): Requires call-site verification to confirm the bug; deferred to avoid speculative change.

---

## 2026-04-15 — PM ADD: Data-Driven, Not Rule-Driven; Code Sizes, Opus Decides

**Decision**: The Portfolio Manager's ADD verb is now fully data-driven — Opus decides **whether** to ADD based on a rich position block (entry thesis, per-dimension entry→current deltas with IC-weight annotations on load-bearing dims, setup type, pattern, regime, news, earnings). The **size** is computed in code via `calculate_position_size()` — the same function that sized the original entry — using the current signal score (not the entry score) and the current ATR. Opus no longer emits `ADD_NOTIONAL`.

**Why the split**:
- *Opus decides the verb*, because synthesizing across 13 dimensions + thesis text + regime + catalysts is the kind of judgment LLMs do well and hardcoded rules do poorly. Giving Opus more data and fewer rules is more faithful to the "9 orthogonal dimensions, synthesize" architecture than telling it "ADD when dim X +5 AND dim Y crossed threshold."
- *Code decides the size*, because sizing is a risk contract — not a judgment call. Entries flow through `calculate_position_size()` with Kelly/VIX/drawdown scalars, ATR vol cap, single-position cap, and the 20% hard cap. ADDs previously bypassed all of that and ran on Opus's dollar amount, which could violate `max_single_position` silently. That was strictly less safe than entry; now they match.

**Safety floors (hardcoded, applied before ADD execution)**:
1. `check_risk_conditions()` — daily loss limit, drawdown CB, cash reserve, market hours, PDT rule, CAPITULATION regime
2. `get_earnings_within_hours(48)` — no ADD into a binary event
3. Single-position cap clamp — if existing notional + add_qty would exceed `max_single_position`, clamp add_qty to the headroom; if headroom ≤ 0, downgrade to HOLD (logged)
4. Only LONG stocks — options / FX / SHORT not supported by `execute_add_to_position` (unchanged)

**DCA into pullbacks**: explicitly allowed when the thesis is intact and core signal dimensions have not collapsed. The distinction between "legitimate DCA on pullback" and "averaging down into a broken thesis" is made by Opus reading the data block (per-dimension deltas + thesis text), NOT by a prompt rule.

**REASON tag convention**: Opus leads its one-line REASON with a snake_case tag (e.g., `signal_strengthening`, `pullback_to_support`, `news_catalyst_confirms`, `rally_continuation`, `thesis_intact`). Post-hoc we can cluster ADDs by tag and measure which trigger types are alpha-positive, without requiring a separate `triggered_rule` field.

**What was already built and just needed wiring**: ADD vocabulary in the prompt, parser, routing in `bot_trading.py`, and `execute_add_to_position()` in `orders_core.py` were all already in place. This session expanded the data surface Opus sees, removed `ADD_NOTIONAL` as Opus's decision, and routed ADD through the same risk/sizing stack as entries.

**Files touched**: `portfolio_manager.py` (prompt + render + parser), `bot_trading.py` (ADD handler + import).

---

## 2026-04-01 — Action #9: Regime Approach Decision

### VIX-Proxy Locked as Sole Regime Detector

**Decision**: Commit to VIX-proxy + SPY/QQQ EMA as the sole market regime detector. HMM upgrade explicitly deferred until IC Phase 2 gate (≥200 closed trades).

**Rescinds**: The 2026-03-26 "Regime Probabilities (HMM) over Hard Labels" entry. That decision was premature — it was recorded before we had enough live trade data to validate any alternative. The architectural risk of building HMM alongside the existing VIX-proxy outweighs the potential accuracy gain at current trade volume.

**Gate for HMM**: Reopen when `closed_trades >= 200` AND IC Phase 2 review is complete. At that point, HMM replaces VIX-proxy entirely — it does not run alongside it. Running two regime detectors in parallel produces architectural incoherence (conflicting hard labels for the same decision point).

**What stays active**:
- `scanner.get_market_regime()` — 4-state hard classifier (BULL_TRENDING / BEAR_TRENDING / CHOPPY / PANIC)
- `signals.get_market_regime_vix()` — 2-state VIX router for dimension weighting (momentum / mean_reversion)
- `ml_engine.RegimeClassifier` — remains in codebase for future research; `PRODUCTION_LOCKED = True`, not connected to the production pipeline

---

## 2026-03-26 — Bias Removal & Regime Adaptation Roadmap

### Identified Structural Bullish Bias
**Decision**: Create a dedicated roadmap (`roadmap/`) to systematically remove directional bias from the signal engine and add regime-adaptive weighting.

**Reasoning**: Architecture review revealed three root causes of bullish bias: (1) signal scoring dimensions are asymmetric — bullish setups score higher than equivalent bearish setups, (2) the TradingView scanner only surfaces long candidates, so agents never see short opportunities, (3) paper consensus threshold of 2/6 is too low to filter bad trades. These are structural issues, not parameter tuning problems. Fixing them requires changes to the signal engine, scanner, and scoring pipeline — not just agent prompts.

### Direction-Agnostic Scoring over Regime-Switched Prompts
**Decision**: Refactor the signal engine to score setup quality independently of direction, rather than injecting regime-specific behavioral overrides into agent prompts.

**Reasoning**: The alternative (telling agents "you're in a bear market, be more bearish") replaces bullish groupthink with regime-driven groupthink. One bad regime classification cascades through all 6 agents. A direction-agnostic engine lets the data determine the ratio of long vs short signals naturally — more bearish setups score well in bearish markets, without anyone telling the system what regime it's in. Regime detection (HMM) should influence dimension weights, not agent behavior.

### Regime Probabilities (HMM) over Hard Labels
**Decision**: Replace if/else regime classification (VIX thresholds + SPY EMA) with Hidden Markov Model that outputs probability distributions over regimes.

**Reasoning**: Hard labels cause binary weight switches that are late to every transition. HMM outputs smooth probabilities (e.g., 60% bull, 30% choppy, 10% bear) that blend weights proportionally. During regime transitions, weights shift gradually instead of flipping. Academic support: Ang & Bekaert (2002) proved regime-switching strategies outperform static strategies out-of-sample. PANIC (VIX > 35) stays as a hardcoded kill switch — HMM is too slow for flash crashes.

### Skew Tracking as Diagnostic, Not Feedback Loop
**Decision**: Track directional skew (% long vs short) as a dashboard metric and alert, NOT as input to agent prompts.

**Reasoning**: Feeding skew back into agents ("you've been 80% long, correct yourselves") creates forced trades to balance a statistic. The market is structurally long-biased over time — forcing 50/50 fights the base rate. Skew is a diagnostic for humans to spot pipeline problems, not an automatic override.

### Full roadmap with sequencing: see `roadmap/README.md`

---

## 2026-03-26 — Phase 2-5: Full Feature Build

### 8 Dimensions over 7 (Social Sentiment as Dimension #8)
**Decision**: Add social sentiment from Reddit/ApeWisdom as the 8th scoring dimension rather than folding it into the existing News dimension.

**Reasoning**: News (dimension #7) measures editorial/institutional news flow (Yahoo RSS, Finviz, IBKR). Social sentiment measures retail crowd behavior. These are independent signals — a stock can have no news but massive Reddit attention (e.g., meme stocks), or major news with zero social buzz (e.g., utility earnings). Keeping them separate preserves signal independence, which is a core design principle (no redundant oscillators).

### Mention Velocity over Raw Count
**Decision**: Track mention **acceleration** (rate of change in mentions per hour) rather than raw mention count.

**Reasoning**: A stock with 100 steady mentions/hour on Reddit is old news. A stock going from 5 to 50 mentions/hour is new attention — that's the signal. Velocity catches emerging momentum before it peaks. Raw counts are biased toward large-cap / meme stocks that always have high mention volume.

### ML Walk-Forward Cross-Validation (TimeSeriesSplit)
**Decision**: Use `TimeSeriesSplit` from scikit-learn instead of random k-fold cross-validation.

**Reasoning**: Financial time series have temporal dependencies. Random k-fold would allow the model to train on future data and test on past data (lookahead bias), producing inflated accuracy that doesn't generalize. Walk-forward validation always trains on past → tests on future, matching real-world deployment.

### ML Score Multiplier (0.5x-1.5x) over Additive Adjustment
**Decision**: ML enhances scores by multiplying by 0.5x to 1.5x rather than adding/subtracting points.

**Reasoning**: Multiplicative adjustment preserves the relative ranking of signals. A strong signal (score 40) enhanced by 1.3x becomes 52, while a weak signal (score 20) at 1.3x becomes 26. Additive adjustment (+5 to both) would disproportionately help weak signals and could push garbage above the trading threshold.

### IBKR Streaming: Shared Connection over Separate Connection
**Decision**: Use the same IB connection for streaming data that orders.py uses for execution, rather than opening a second connection.

**Reasoning**: IBKR limits paper accounts to a small number of simultaneous API connections. Opening a second connection for streaming would either consume a slot or cause Error 10197 (duplicate client ID). Sharing the connection avoids this. The trade-off is that heavy streaming could slow order execution, but the 100-subscription limit and LRU eviction keep the load manageable.

### Smart Execution: $10K / 500-Share Threshold
**Decision**: Only use TWAP/VWAP/Iceberg for orders above $10K notional or 500 shares. Smaller orders use simple limit orders.

**Reasoning**: Smart execution adds latency (order is sliced over minutes). For small orders, the market impact is negligible, so the added complexity and time aren't worth it. The threshold is conservative — in practice, most paper-trading positions at 3% of $1M = $30K would qualify.

### Portfolio Optimizer: 30-Minute Correlation Cache
**Decision**: Cache the correlation matrix for 30 minutes rather than computing it on every scoring cycle.

**Reasoning**: Computing a 60-day rolling correlation matrix for 20+ positions requires downloading historical data for all positions and performing matrix math. This takes 10-30 seconds. Since correlations change slowly (daily, not per-minute), a 30-minute cache provides near-identical accuracy at 1/10th the compute cost.

### Parquet over CSV for ML Training Data
**Decision**: Store all historical data as Parquet files (via pyarrow) rather than CSV.

**Reasoning**: Parquet is columnar, compressed, and 10-100x faster than CSV for the bulk reads that ML training requires. It preserves column types (datetime, float64) without the parsing overhead of CSV. Supports append-with-dedup workflow (read existing, concat, deduplicate, write back). The pyarrow dependency is lightweight.

---

## 2026-03-26 — Phase 1: Speed + Data Generation

### ProcessPoolExecutor over ThreadPoolExecutor
**Decision**: Replace `ThreadPoolExecutor(max_workers=1)` with `ProcessPoolExecutor(max_workers=N)` for `score_universe()`.

**Context**: yfinance.download() is not thread-safe (GitHub issue #2557). Concurrent threads share a global `_DFS` dict, causing cross-symbol data contamination. The previous fix was to force `max_workers=1` (sequential), making scoring the single biggest bottleneck at 180–240 seconds per scan.

**Solution**: Separate processes each get their own copy of Python globals, so yfinance's `_DFS` never collides. A lazily-initialized `ProcessPoolExecutor` with `min(6, cpu_count - 1)` workers provides 3–5x speedup. Automatic fallback to sequential if fork fails.

**Alternatives considered**: (1) Migrate to IBKR streaming data — correct long-term fix but requires significant plumbing and doesn't give historical multi-timeframe data. (2) Patch yfinance internals — fragile, breaks on library updates. (3) Pre-download all data in one batch call — yfinance batch download has its own bugs with different intervals.

### Dynamic Regime Thresholds
**Decision**: Replace hardcoded regime thresholds (28/25/22/99/25) with values derived from `min_score_to_trade` config.

**Reasoning**: The hardcoded thresholds meant changing `min_score_to_trade` in config had limited effect — regimes still used their own fixed values. Now all regime gates scale proportionally, so paper trading config (`min_score=18`) automatically loosens everything.

### TV Pre-Filter Widening for Paper Trading
**Decision**: Loosen RSI dead zone (42–58 → 47–53), volume floor (1.0 → 0.5), change floor (0.3% → 0.1%), and expand top-N (15 → 25).

**Reasoning**: The original pre-filter was designed to minimize yfinance calls in live trading. For paper trading, the goal is maximum trade diversity. Mean-reversion setups (RSI 42–47, 53–58), early breakouts (volume 0.5–1.0x before confirmation), and slow accumulation plays (0.1–0.3% change) are all valid training data that the old filter was dropping. More candidates × parallel scoring = minimal time cost.

### Paper Trading Config: Aggressive Data Generation
**Decision**: Lower thresholds across the board — min_score 18, agents_required 2, max_positions 20, faster scan intervals.

**Reasoning**: On a paper account with $1M simulated capital, the cost of a bad trade is zero. The value of each trade (win or lose) is training data across different market regimes, signal strengths, and setup types. The configuration maximizes trade count while maintaining enough structure (scoring, agents, risk checks) that each trade is still a meaningful signal — not random noise.

**Risk**: When switching to live, every changed parameter must be reverted. All live values are preserved as inline comments in config.py.

### Parquet Format for Historical Data
**Decision**: Store collected historical data as Parquet files rather than CSV or SQLite.

**Reasoning**: Parquet is columnar, compressed, and fast to read for ML workloads (10–100x faster than CSV for large datasets). Supports append-with-dedup (read existing, concat, deduplicate, write). Native pandas/pyarrow integration. The `pyarrow` dependency is lightweight and widely available.

**Alternatives considered**: (1) CSV — simple but slow for large datasets, no type preservation, no compression. (2) SQLite — good for queries but overkill for time-series bulk reads, adds complexity. (3) HDF5 — good performance but less ecosystem support than Parquet.

---

## 2026-03-25 — Established Documentation System

**Decision**: Use git + Markdown docs as the primary version control and documentation system.

**Context**: The codebase is evolving daily through brainstorming and programming sessions. Word docs in `docs/` serve as polished references but can't be diffed in git. Markdown companions track the living, changing logic while Word docs get regenerated periodically.

**Alternatives considered**: Notion (too disconnected from code), Wiki (overkill for solo/small team), inline comments only (can't see the big picture).

---

## Pre-2026-03-25 — Historical Decisions (Reconstructed)

These decisions are inferred from the current codebase. Future entries will be logged as they happen.

### 6-Agent Architecture
**Decision**: Use 6 specialised Claude agents rather than a single monolithic prompt.

**Reasoning**: Each agent has a focused role and can be tuned independently. The Devil's Advocate agent specifically exists to counterbalance confirmation bias. The Risk Manager has veto power to prevent the other agents from overriding safety limits.

### Agent Agreement Threshold
**Decision**: Configurable via `agents_required_to_agree`. Paper = 2 (aggressive for data generation), Live = 4 (conservative).

**Reasoning**: Lower threshold = more trades taken. For paper trading, more trades = more ML training data. For live, higher threshold reduces false positives. The value 2 in paper means any two of six agents agreeing is enough, which dramatically increases trade volume.

### Signal Engine: 8 Independent Dimensions
**Decision**: One indicator per dimension, no overlapping oscillators. Extended from 6 to 8 dimensions (added News + Social).

**Reasoning**: Avoid the common trap of using RSI + Stochastic + CCI which all measure the same thing (momentum). Each of the 8 dimensions (Trend, Momentum, Squeeze, Flow, Breakout, Confluence, News, Social) measures something fundamentally different.

### Options: ATM Delta Targeting (0.50)
**Decision**: Target delta 0.50 instead of the more common 0.30–0.40 for directional trades.

**Reasoning**: ATM options provide maximum leverage per dollar of premium. The slightly higher premium cost is offset by better probability and more responsive Greeks.

### Inverse ETFs Instead of Short Selling
**Decision**: Use inverse ETFs (SPXS, SQQQ, UVXY) for bearish exposure rather than direct shorting.

**Reasoning**: Simpler execution, no borrow costs, no margin complications. Trade-off is tracking error on leveraged products, but acceptable for short-duration trades.

---

## 2026-03-25 — News Sentinel Architecture

### Interrupt-Style vs. Priority Queue
**Decision**: News triggers run as an independent async loop that immediately fires a mini agent pipeline, rather than boosting priority in the next scheduled scan.

**Alternatives considered**: (1) Priority queue — news events get queued and the next scan picks them up first with boosted scores. Rejected because scan intervals can be up to 60 minutes overnight, and material news (earnings beats, FDA approvals) can move a stock 5–10% in minutes. (2) Both modes — critical news triggers immediately, moderate news boosts priority. Rejected for complexity; the materiality filter already handles the severity distinction.

**Trade-off**: Interrupt-style means Claude API costs increase slightly (3 extra calls per trigger). Mitigated by rate limiting (max 3 triggers/hour) and cooldowns (10 min per symbol).

### 3-Agent Pipeline vs. Full 6-Agent Pipeline
**Decision**: Use a lightweight 3-agent pipeline (Catalyst Analyst, Risk Gate, Instant Decision) for sentinel trades instead of the full 6 agents.

**Reasoning**: Speed. The full pipeline takes 5–10 minutes (6 sequential Claude calls with rich context). The sentinel needs to act in 15–30 seconds. Three agents cover the essentials: (1) is this news material and what direction? (2) can we afford this trade right now? (3) execute or skip. The missing agents (Technical Analyst, Macro Analyst, Devil's Advocate) are acceptable losses because the news itself is the primary signal — we don't need full technical confirmation for a catalyst-driven trade.

**Risk mitigation**: Sentinel trades use 0.75x position sizing to compensate for the lighter analysis. All hardcoded risk limits still apply.

### Sentinel Position Sizing at 0.75x
**Decision**: Sentinel trades use 75% of normal position sizing.

**Reasoning**: News-driven trades have higher uncertainty than technically-confirmed scan trades. The lighter 3-agent analysis means less validation. Reducing size limits downside while still capturing the move. Can be tuned via `sentinel_risk_multiplier`.

### Theme-Based Universe (3 Layers)
**Decision**: Combine auto-detection from holdings, predefined themes, and trending theme discovery to build the sentinel monitoring universe.

**Alternatives considered**: (1) Monitor only current holdings — too narrow, misses new entry opportunities. (2) Monitor everything in the scan universe (~100 symbols) — too broad, wastes API calls on symbols with no relevance to current market narratives. (3) Fixed watchlist only — doesn't adapt to changing market themes.

**Reasoning**: The 3-layer approach prioritises what matters most (holdings first), provides broad thematic coverage (9 predefined themes), and adapts dynamically (trending themes detected from headlines). The 80-symbol cap keeps API costs manageable while covering all major market narratives.

### Finviz + Yahoo RSS + IBKR (3 Sources)
**Decision**: Use three news sources rather than relying on a single feed.

**Reasoning**: No single free news source has both speed and coverage. Yahoo RSS is fast but sometimes delayed. Finviz scraping catches stories Yahoo misses. IBKR's news API (Benzinga, DowJones, FlyOnTheWall) provides professional-grade feeds that are already included with the IBKR subscription — no additional cost. Multiple sources also serve as cross-validation: if 2+ sources report the same story, it's more likely to be material.

### 10-Minute Per-Symbol Cooldown
**Decision**: After a sentinel trigger fires for a symbol, block re-triggering for 10 minutes.

**Reasoning**: Breaking news generates cascading headlines — the same story gets reported by multiple outlets over several minutes. Without a cooldown, the sentinel would fire repeatedly on the same event, wasting Claude API calls and potentially entering the same trade multiple times. 10 minutes is long enough to let the news cycle pass but short enough to catch genuinely new developments.

---

## 2026-04-13

### Trade Metadata Immutability — IBKR Re-sync Must Never Overwrite Decision Metadata
**Decision**: Decision metadata (trade_type, conviction, reasoning, signal_scores, agent_outputs, entry_regime, entry_thesis, entry_score, ic_weights_at_entry, pattern_id, setup_type, advice_id, open_time, atr, high_water_mark) is immutable once written. No reconciliation function may overwrite it.

**Context**: IBKR position re-sync was overwriting local trade metadata with stub values ("Re-synced from IBKR — metadata not found"), erasing the entire decision context for the trade. This is fatal to the learning system — a closed trade without its decision metadata cannot contribute to IC calculation or pattern library training.

**Implementation**: `_safe_set_trade()` in `orders_state.py` enforces this at the storage layer. If an existing position already has a non-UNKNOWN `trade_type`, the 15 protected fields from `DECISION_METADATA_FIELDS` are preserved regardless of what the caller passes. IBKR is allowed to update only: `current`, `current_premium`, `pnl`, `_price_sources`, `status` (defined in `trade_store.IBKR_RECONCILE_FIELDS`). Positions without metadata (reconciled from IBKR cold, no local record) are flagged `metadata_status: "MISSING"` and shown with a red banner in the dashboard.

**Why the storage layer**: Enforcing at `_safe_set_trade` means no caller — no matter how it reaches the function — can bypass the guard. Enforcing at the call sites would require auditing every future code path.

### log_trade Deduplication Uses pattern_id, Not Symbol Alone
**Decision**: CLOSE record deduplication in `learning.py` checks pattern_id before applying the 24h same-symbol window. Two CLOSE records with different pattern_ids are always different trade cycles, never duplicates.

**Reasoning**: The original 24h same-symbol dedup was correct for partial fills of a single sell order, but silently dropped legitimate second closes when a symbol was traded, fully closed, reopened, and closed again within 24 hours. Since each trade entry gets a unique pattern_id from the pattern library, differing pattern_ids are definitive proof of distinct trades. The guard falls back gracefully: if either record lacks a pattern_id (pre-pattern-tracking data), the old 24h logic applies.

### pnl_pct Stored in trades.json per Trade Record
**Decision**: Every CLOSE record in trades.json now includes `pnl_pct` alongside `pnl`.

**Reasoning**: pnl_pct (return on capital including the ×100 options contract multiplier) is the normalised metric for comparing performance across different position sizes and instruments. Storing it at close time means IC analysis, pattern library retrospectives, and any future Alphalens integration can use it directly without recomputing.

### IC using_equal_weights Detection via Tolerance, Not Exact Float Equality
**Decision**: `using_equal_weights` in `ic_calculator.py` uses `abs(w - 1/N) < 1e-9` tolerance check plus an explicit `CONFIG.get("force_equal_weights")` flag, not `weights == {d: round(1/N, 10)}`.

**Reasoning**: `1/12 = 0.08333…3` (16 sig figs) and `round(1/12, 10) = 0.0833333333` (10 sig figs) are not equal under Python `==`, so the old check always returned False. The dashboard incorrectly showed IC weights as "active" even when `force_equal_weights=True`. This is a cosmetic bug but misleading for learning system diagnostics.

---

## 2026-04-14

### Chief Decifer Has One Sacred State Path — No Fallback, No Split-Brain
**Decision**: `chief-decifer/state/` is the single authoritative directory for all Cowork↔Chief data contracts. Chief's `config.py` no longer falls back to a local `state/` inside `Chief-Decifer-recovered/`. The session-start hook no longer reads from a configurable `CHIEF_STATE_PATH` env var pointing elsewhere. One path, one source of truth.

**Context**: The brain was wired wrong in three places at once:
1. `.claude/settings.json` set `CHIEF_STATE_PATH` to `/Users/amitchopra/Documents/Claude/Projects/Chief Designer/Chief-Decifer/state` — a directory that did not exist. The session-start hook silently `safeRead`-nulled everything. **249 sessions started with zero memory injection from Chief.** Cowork's apparent continuity came entirely from CLAUDE.md — not from session logs, specs, research, or the backlog.
2. `Chief-Decifer-recovered/config.py` split reads between a local `state/` and the project's `chief-decifer/state/`, so `RESEARCH_DIR` pointed at recovered/state/research/ while `SESSIONS_DIR` pointed at chief-decifer/state/sessions/. Research files Cowork wrote never showed up in Chief's Research panel.
3. The session-start hook's fallback default resolved to `../chief-decifer/state` relative to the repo root — i.e. *outside* the repo at `/Users/amitchopra/Desktop/chief-decifer/state`.

**Implementation**:
- Removed `env.CHIEF_STATE_PATH` from `.claude/settings.json`.
- Hook default at `.claude/hooks/session-start-hook.mjs:26` now resolves to `REPO_ROOT/chief-decifer/state`.
- `Chief-Decifer-recovered/config.py` collapsed to a single `STATE_DIR = DECIFER_REPO_PATH / "chief-decifer" / "state"`. Chief-only compute artifacts (catalyst, analysis, activity.jsonl, docs) moved under `state/internal/`.
- Research files misfiled as specs (72 `research-*.json` files inside `chief-decifer/state/specs/`) moved to `chief-decifer/state/research/`.
- Stale recovered backlog (`feat-019..026`, multi-account focus) archived to `chief-decifer/state/archive/backlog-recovered-2026-03-31.json`. The Phase A–E `BACK-*` backlog is canonical.
- Older sessions (pre-2026-04-02) from recovered merged into sacred `sessions/`. 19 historical feat-specs + 14 dated research files copied from recovered to sacred.

**Why one path**: A memory substrate with two locations is not memory — it is ambiguity. If the hook reads one place and Cowork writes another, the brain drifts and is silently stale. Chief's whole purpose is to be the single source of truth about bot state, past work, and intent. Two paths = two truths = no truth.

**Rule**: `research-*.json` belongs in `research/`, never in `specs/`. Specs describe feature intent or completed work; research files are knowledge-base entries from `researcher.py` or Cowork investigations. Mixing them collapses the contract.

---

### Publisher Scheduler: launchd Is the Single Authority After Proof Window (Local Mac)
**Decision (2026-05-11, Amit):**

This sprint was executed in local Mac laptop testing mode, not cloud mode.

Both cron (`*/10 * * * *`) and launchd (`com.decifer.handoff-publisher`, `StartInterval=600`) are currently running the handoff publisher every 10 minutes as temporary activation redundancy. Code inspection confirms that overlapping runs are possible because the two intervals are not synchronised.

Manifest writes are atomic through `_write_atomic()`, which writes to a temporary file and then uses `os.replace()`. Therefore, the final manifest is protected from partial writes. Because both schedulers currently produce the same `controlled_activation` manifest, overlapping runs are low risk during the activation proof window. The main side effect is duplicated run-log evidence.

There is currently no lock, flock, fcntl guard, or pidfile enforcing a single writer. For this reason, dual scheduling should not remain the steady-state local runtime.

After the first successful market-hours handoff-consumption proof (proof matrix checks 26 + 27 confirmed), cron should be disabled and launchd should remain the single local Mac scheduler authority.

```bash
crontab -l | grep -v "handoff_publisher" | crontab -   # disable cron
launchctl list com.decifer.handoff-publisher            # confirm launchd remains
# Expected: LastExitStatus = 0; ProgramArguments includes --mode controlled_activation
```

Cloud scheduling is out of scope for this sprint and will be handled later during the cloud deployment phase.


---

### ML Observation Logging Activated — Sprint 3.5 (2026-05-20)
**Decision (2026-05-20, Amit):**

`ml_observer_enabled` set to `True` in `config.py`. This is evidence collection activation only — not ML activation.

**What is active:**
- `ml_observation_writer.write_observations()` is called from `signal_pipeline.run_signal_pipeline()` between steps 7 and 8 (after scoring + ranking complete, before signals_log append).
- One observation record is written per scored candidate (including below-threshold candidates) to `data/ml/ml_observations.jsonl`.
- Each record carries: `scan_id`, `observation_id`, `symbol`, `base_score`, `live_score_after_observer` (== `base_score`), `live_score_unchanged=True`, `signal_scores` (score_breakdown dict), `ranking_position`, `ranking_total`, `regime`, `vix`, `ml_observer_enabled=True`, `ml_score_influence_enabled=False`.

**What is NOT active (must remain False):**
- `ml_score_influence_enabled` — score adjustment from ML is not activated and requires explicit Amit approval after shadow validation.
- No model training, model loading, prediction, win_prob, enhanced score, sklearn, or joblib.

**Why observation-only first:**
Sprint 3 built the outcome joiner and canonical dataset builder but had 0 live observations because the observer was never enabled. We cannot build a learning dataset from zero. Activating the observer is the minimum intervention: write evidence from the real pipeline without touching scores, ranking, eligibility, sizing, or execution.

**Architecture invariants this change preserves:**
- `live_score_after_observer == base_score` always — recorded in every observation record.
- `live_score_unchanged = True` always — recorded in every observation record.
- Writer is non-blocking: any failure is caught by `signal_pipeline.py`'s `try/except` and logged at DEBUG; trading never stops.
- No third-party ML dependencies introduced: stdlib only in `ml_observation_writer.py`.

**Next sprint gate:**
After one live scan cycle writes observations, run `scripts/ml_observation_health_check.py --canary` to validate data integrity. Run `scripts/ml_outcome_joiner.py` to join observations to outcomes. The canonical learning dataset will have `ml_eligible=False` for all pass rows (no trade taken) and will have `ml_eligible=True` only for exactly-joined, closed, directional trades with signal scores — this set grows with each trading day.

---

### Scoring Pipeline Fetch Overload — Production Incident 2026-05-21 (Alpaca Historical Bars REST)
**Decision (2026-05-21, Amit):**

**Incident:** At 14:23 ET, `score_universe()` reported "72/72 symbols failed data fetch" and aborted the scan cycle. The live quote stream and position reconciliation remained healthy throughout.

**Root cause:** `_SCORE_WORKERS=16` with `_ALPACA_SEM(16)` created up to 48 simultaneous Alpaca historical bars REST calls per scan cycle (3 bar fetches per symbol × 16 concurrent workers on first/cache-cold scan). urllib3 has a 10-connection pool to data.alpaca.markets; 38 excess connections opened fresh TCP sockets. Combined with no retry for HTTP 429 or read timeouts, a transient API slowdown cascaded into a complete scan failure.

**Fix (targeted, no new architecture layer):**

1. **Batch prefetch**: Before the ThreadPoolExecutor, `fetch_bars_batch()` fetches 1d and 1wk bars for all universe symbols in 2 API calls (vs N×2 individual calls). Results populate an in-cycle cache (3-minute TTL). Workers hit the cache instead of the network for daily/weekly bars.

2. **Reduced concurrency**: `_SCORE_WORKERS` and `_ALPACA_SEM` reduced from 16 to 6. Workers only need to fetch 5m bars (usually served from Alpaca stream cache). 6 concurrent calls stay within the 10-connection pool with headroom.

3. **Retry with backoff+jitter**: `fetch_bars()` and `fetch_bars_batch()` retry HTTP 429, 5xx, and transient connection/timeout errors with exponential backoff + jitter (base delay × 2^attempt + uniform jitter).

4. **Partial success**: Scan continues when successful symbols are above 20% threshold (80% failure triggers abort, unchanged). Partial candidates are scored and surfaced.

5. **Circuit breaker**: After 3 consecutive full-failure cycles, new entries are paused. Portfolio management, exits, and risk monitoring are never blocked. Auto-closes after 300 seconds. Logged as `DATA_FETCH_BLOCKED`, never `RISK_BLOCKED`.

6. **Structured telemetry**: Each scan logs `requested=N successful=M failed=K fetch_mode=batched|bounded_parallel|fallback elapsed_ms=T`.

**What was NOT changed:** Signal scoring logic, trading thresholds, risk gates, execution pipeline, IC weights, Apex call count.

---

### PME Outcome Tracking — Sprint (2026-05-22)

**Decision (2026-05-22, Amit):**

Add outcome tracking for Portfolio Management Engine decisions without altering PME execution behaviour or thresholds.

**Why:**
PME is live and executing FULL_EXIT actions. Without outcome measurement, there is no way to know whether exits avoided loss, cut winners early, or whether HOLD decisions were justified by subsequent price action. Training data quality depends on knowing whether PME added alpha.

**What was built:**
- `pm_outcome_tracker.py` — links each PME decision to future market price outcomes across 6 time windows (30 min, 1 h, EOD, 1 d, 3 d, 5 d).
- `data/pm_engine/outcomes.jsonl` — append-only outcome log; one record per (decision, window) pair.
- `/api/pm_outcomes` endpoint in `bot_dashboard.py` — returns `get_summary()` payload.
- Outcomes panel in the Portfolio Mgmt dashboard tab — summary banner + recent outcomes table.
- `resolve_pending()` wired into `bot_trading.py` scan cycle (non-blocking, max 20 Alpaca fetches per cycle).
- 35 tests in `tests/test_pm_outcome_tracker.py`.

**Outcome classification thresholds (for evaluation only — do NOT conflate with PME execution thresholds):**
- `> +2%` return after exit → BAD (`cut_winner_early`)
- `< −2%` return after exit → GOOD (`caught_decline`)
- Within ±2% → NEUTRAL
- HOLD: opposite sign convention — price rising is GOOD (`justified_hold`), falling is BAD (`held_too_long`)
- SAFETY_BLOCKED exits: price falling → BAD (`rail_too_strict`), rising → GOOD (`rail_correct`)

**What was NOT changed:** PME thresholds, execution logic, signal weights, Apex prompts, safety rails, or any trading behaviour.

---

### Sprint M13B — Customer Intelligence Experience (2026-05-26)

**Decision (2026-05-26, Amit):**

Upgrade the `/customer` mobile app from M13A's technically-complete-but-product-incomplete shell into a genuine customer fintech experience that translates the operator dashboard's real intelligence flow into plain-English customer language.

**Why:**
M13A built the structural scaffolding (5-tab nav, shared hook, safety layer) but the content was still raw operator data. The customer-facing surface needed: a Market Story Hero that leads with the "so what", a Forces tab that surfaces active market forces with connection trees, and Ask Decifer with live-context questions rather than static placeholders.

**Locked decisions:**

1. **`customerBriefingModel.ts` is the single translation layer** — all operator-to-customer language conversion happens in pure functions here. Views receive pre-translated structs; they never read raw `MarketNowPayload` fields directly for rendered copy.

2. **PROHIBITED_RENDERED_TERMS policy**: 7 terms blocked (`trade-ready`, `entry candidate`, `position entry`, `preferred trade mode`, `scanner`, `payload`, `market_now_id`). "apex" deliberately excluded — it is a substring of "capex" and would produce false positives. "activation" excluded — too broad, matches common English compound words. Primary safety enforcement lives at the Python `saas_intelligence_output` layer.

3. **Force→Theme connection tree stops at theme labels** — the tree does not drill to individual symbol names. Symbol detail lives in the Names tab (UniverseTab). Drilling to symbols would require N additional API calls per force and duplicate content the Names tab already shows.

4. **TTG fetch is a separate `useEffect`** (not merged into the main `fetchMarketNow` effect) — keeps concerns separated and does not touch the 30 existing M13A tests that mock `fetchMarketNow` only.

5. **`SUGGESTED_QUESTIONS` alias preserved** for backward compatibility with the M13A safety audit test that imports it by name. `STATIC_SUGGESTED_QUESTIONS` is the canonical name; `SUGGESTED_QUESTIONS` is the alias.

6. **No new backend endpoints** — M13B is entirely frontend. The TTG data comes from the existing `/api/intelligence/themes` endpoint. The Market Story Hero is synthesised client-side from `MarketNowPayload` fields that are already fetched.

**What was NOT changed:** Signal scoring, trading thresholds, execution pipeline, IC weights, Apex call count, any Python backend, intelligence API, TTG data, operator dashboard.
