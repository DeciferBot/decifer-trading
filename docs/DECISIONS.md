# Decifer Trading — Decision Log

> Every significant design decision, parameter change, or architectural choice gets logged here with the reasoning. This is the "why" behind the "what."
>
> Format: Date → Decision → Context / Reasoning

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
