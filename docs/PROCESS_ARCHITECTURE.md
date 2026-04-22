# Decifer Trading — True Step-by-Step Process Architecture

*Derived from direct code trace. No assumptions. Line numbers included where key.*
*Last updated: 2026-04-22*

---

## STEP 0 — SYSTEM BOOT & STATE HOUSEKEEPING
**File:** `bot_trading.py` — `run_scan()` entry point

- Kill switch checked → if set, entire scan aborts immediately
- Pause flag checked → if set, scan skips
- Hot-reload: config re-loaded from `config.py` in memory (no restart needed)
- `recently_closed` deque: removes symbols that exited < 30 min ago from re-entry eligibility
- `active_trades` dict: synced with IBKR positions (ground truth)

---

## STEP 1 — UNIVERSE ASSEMBLY
**Files:** `bot_trading.py` lines ~1270-1322, `scanner.py`

Three tiers assembled and deduplicated every scan:

### Tier A — Committed Floor (static, always scored)
- `scanner.CORE_SYMBOLS`: 13 macro/vol ETFs (SPY, QQQ, IWM, VXX, UVXY, GLD, TLT, etc.)
- `scanner.CORE_EQUITIES`: 38 mega-caps + sector leaders (AAPL, NVDA, MSFT, AMZN, etc.)

### Tier B — Daily Promoted (weekly refresh)
- Source: `data/daily_promoted.json` (top-50 symbols, built by `universe_promoter.py` hourly)
- Scoring weights: overnight gap (3.0x), pre-market volume surge (2.0x), catalyst score (2.0x via FMP)
- Max staleness: 18 hours — if file is stale, Tier B is skipped, not substituted
- Minimum committed universe: 1,000 symbols (config: `committed_universe_size`)

### Tier C — Dynamic Per-Cycle (runtime adds)
- **Sector leaders:** Top 6 stocks from each of 11 SPDR sector ETFs when sector RS is in top-3
- **Held positions:** All symbols with open IBKR positions (always re-scored, can't be gated out)
- **Favourites:** User-pinned symbols from dashboard `favourites` list
- **Sympathy plays:** Peer stocks added when an earnings event is within 48h on a related name (`sympathy_scanner.get_sympathy_candidates()`)

**Output:** Deduplicated union — typically ~120–180 symbols per cycle
**Coverage audit:** Written to `data/universe_coverage.jsonl` with per-tier composition counts and regime label

---

## STEP 2 — MARKET REGIME DETECTION
**File:** `scanner.get_market_regime()`, called from `bot_trading.py` ~line 1194

- Fetches SPY 1h bars → checks price vs 200d MA, reads VIX, QQQ price
- Output: `regime` label ∈ {BULL_TRENDING, BULL_CHOPPY, BEAR_TRENDING, BEAR_CHOPPY, CAPITULATION}

### Regime Router (3-way vote, optional components)
1. **VIX-proxy** (always active): VIX < 20 → "momentum" | VIX ≥ 20 → "mean_reversion"
2. **Hurst DFA** (config-gated): SPY return exponent → "momentum"/"mean_reversion"
3. **HMM** (locked: `PRODUCTION_LOCKED = True`): 2-state Gaussian HMM on 60-day SPY returns — deferred until ≥200 closed trades + IC Phase 2

Majority vote → `regime["regime_router"]`

**→ Output used by:** signal scoring multipliers, threshold calculator, agent prompts, position sizing

---

## STEP 3 — RISK GATE (pre-scan)
**Function:** `check_risk_conditions()`, `bot_trading.py` ~line 1234

Checks in order:
1. Portfolio drawdown vs `max_drawdown_alert`
2. Daily P&L vs `daily_loss_limit`
3. Cash remaining vs `min_cash_reserve` (10% floor)
4. PDT rule (< 3 day trades remaining → no new intraday)

If any gate fails:
- Tries `auto_rebalance_cash()` (trim largest position to restore cash floor)
- If still failing → **entire scan aborted, no signals scored, no agents run**

---

## STEP 4 — STRATEGY MODE CALCULATION
**Function:** `get_intraday_strategy_mode()`, `bot_trading.py` ~line 1251

Based on daily P&L vs two configurable thresholds:
- **NORMAL:** Trade as usual
- **DEFENSIVE:** Score threshold raised, sizing unchanged
- **RECOVERY:** Score threshold raised significantly (+10 pts), size multiplier 0.5×

Output: `{mode, score_threshold_adj, size_multiplier}` — carried through all subsequent steps

---

## STEP 5 — POSITION LIFECYCLE (runs at top of every scan, BEFORE scoring)
**Bot_trading.py** ~line 1179

1. **`update_trailing_stops(ib)`** — moves SL up as price advances (ATR-based trailing)
2. **`update_tranche_status(ib)`** — tracks partial exit fills, scales remaining position, adjusts SL
3. **`flush_pending_option_exits(ib)`** — executes deferred option exits
4. **`update_position_prices()`** — marks all open positions to latest scored prices

---

## STEP 6 — PRE-SCORING SENTIMENT LAYERS
**File:** `signal_pipeline.py`, sequential (not parallelised)

### 6a. Sympathy scanner
- `sympathy_scanner.get_sympathy_candidates()` → adds peer stocks to universe dynamically before scoring begins

### 6b. News sentiment (all symbols, timeout 8s)
- `news.batch_news_sentiment()` → Yahoo RSS keyword scoring + 2-tier Claude sentiment
- Per symbol: `keyword_score` (-10 to +10), `claude_sentiment` (bullish/bearish/neutral), `claude_catalyst` (text)

### 6c. Social sentiment (market-hours only, skipped pre/after market)
- `social_sentiment.get_social_sentiment()` → Reddit/ApeWisdom velocity + VADER
- Per symbol: `social_score` (0–10)

**→ Both outputs are injected as inputs into the 10-dimension scoring below**

---

## STEP 7 — 10-DIMENSION SIGNAL SCORING (PARALLEL)
**Files:** `signals/__init__.py` (`score_universe()` line ~2873), `signals/compute_confluence()`

**Execution:** ThreadPoolExecutor, `_SCORE_WORKERS` threads (typically 8–16), one task per symbol

### Per-symbol scoring: `fetch_multi_timeframe(sym, news_score, social_score, regime_router)`

Each dimension scores 0–10. All 10 sum to a raw score (theoretical max 100, hard cap at 50).

| # | Dimension | What it measures |
|---|-----------|-----------------|
| 1 | **DIRECTIONAL** | EMA alignment (9/21/50) × ADX + multi-timeframe vote. Gap-boost ×1.5 in OPEN_BUFFER (9:30–9:45 ET) if gap ≥ 2% |
| 2 | **MOMENTUM** | MFI distance from 50 (symmetric) + RSI slope confirmation |
| 3 | **SQUEEZE** | Bollinger Band width vs Keltner Channel (BB inside KC = spring-loaded setup) |
| 4 | **FLOW** | VWAP distance from price + OBV slope confirmation |
| 5 | **BREAKOUT** | Donchian high/low breach (binary 6 pts, +2–4 for volume ratio ≥ 1.5×–2.0×). Gap-boost applied |
| 6 | **MTF** | Multi-timeframe alignment (daily + weekly confirm 5m direction): 0/8/10 pts |
| 7 | **NEWS** | Clamped `keyword_score` to 0–10 with regime router multiplier |
| 8 | **SOCIAL** | Clamped `social_score` to 0–10 with regime router multiplier. IC auto-disable gated |
| 9 | **REVERSION** | ADF gate (p < 0.05 mandatory — if fails, score = 0). Sub-metrics: Variance Ratio (0–3), OU half-life (0–4), Z-score magnitude (0–3) |
| 10 | **OVERNIGHT_DRIFT** | 90-day close-to-open drift z-score (edge on overnight gaps) |

**Optional (config-gated):**
- **IV Skew:** Alpaca options chain put/call IV ratio (silently skipped if no options data)

### Sentiment Consensus Gate (applied after scoring)
- Both news AND social must carry signal (≥3 pts absolute value each)
- Agree on direction: +15% score boost
- Disagree: −20% score penalty

### Direction Resolution
- Weighted majority vote across per-dimension directions → final `direction` (LONG/SHORT)

### MTF Gate (hard/soft/off, config)
- **Hard mode:** 5m vs daily/weekly misalignment → score forced to 0, signal = HOLD
- **Soft mode:** Misalignment → penalty deducted
- **Off:** Legacy behavior (only Dimension 6 scores MTF)

**→ Output per symbol:** `{score, direction, score_breakdown dict, signal_type (STRONG_BUY/BUY/SELL/etc), atr, pattern_id}`

---

## STEP 8 — SCORE FILTERING & GATES
**File:** `signal_pipeline.py` lines ~484–500

Applied in order:

1. **Regime threshold:** `get_regime_threshold(regime)` → base min score (14 paper / 28 live)
2. **CAPITULATION override:** threshold → 99 (blocks all new trades)
3. **Strategy mode adjustment:** +0 to +10 pts from Step 4
4. **IC edge gate:** +5 to +12 pts when system IC health is poor (config: `ic_calculator.edge_gate_*`)
5. **Short quality gate:** If SHORT IC < 0.03, SHORT signals must meet full threshold (no discount)
6. **Score persistence gate:** Signal must exceed threshold for N consecutive scans (config: `score_persistence_scans` — 0 paper / 2 live). Tracked via per-symbol 4-cycle deque `_THRESHOLD_HISTORY`
7. **Candlestick gate:** TA-Lib pattern reliability check (hammer, morning star, engulfing, etc.). Non-blocking — failure sets `candle_gate = "SKIPPED"`, does not zero score

**Output split:**
- `scored` list: symbols above threshold, sorted descending by score
- `all_scored` list: full universe regardless of threshold (written to IC audit)

---

## STEP 9 — IC AUDIT LOGGING
**File:** `learning.log_signal_scan()`, `signal_pipeline.py` ~line 497

- **Full universe** (including below-threshold) written to `data/ic_audit.jsonl`
- Per entry: symbol, score, per-dimension breakdown, direction, regime, timestamp
- Used by `ic_calculator.py` to compute rolling Information Coefficients per dimension
- Typed `Signal` objects also written to `data/signals_log.jsonl`

**This runs BEFORE the agent pipeline — IC data is captured regardless of whether a trade fires**

---

## STEP 10 — LIGHTWEIGHT CYCLE CHECK (DETERMINISTIC, EVERY SCAN)
**File:** `bot_trading.py` lines ~1430–1490

For each open position — deterministic, no LLM:
- **Thesis invalidation check:** If the scored signal for this symbol has reversed direction significantly → flag for EXIT
- **Regime flip check:** If market regime has changed since entry → flag for REVIEW
- Gate: if position is already in EXITING state → skip

Does not execute trades directly — sets flags that feed into PM review or execution loop

---

## STEP 11 — PORTFOLIO MANAGER REVIEW (EVENT-TRIGGERED, OPUS LLM)
**Files:** `bot_trading.py` lines ~1492–1881, `portfolio_manager.py`

### Trigger Conditions (`_should_run_portfolio_review()`)
Only fires when at least one is true:
| Trigger | Condition |
|---------|-----------|
| `pre_market` | Once per day before 10:00 ET |
| `regime_change` | Market regime flipped vs `session_opening_regime` |
| `score_collapse` | Held symbol score dropped ≥ 15 pts since entry |
| `held_score_rise` | Held symbol score rose ≥ 15 pts AND current score ≥ 45 (ADD trigger) |
| `news_hit` | `keyword_score` on held symbol ≥ 5 |
| `earnings_risk` | Earnings within `portfolio_manager.earnings_lookahead_hours` (default 48h) |
| `cascade` | ≥ 2 stop-losses hit in same session |
| `drawdown` | Daily P&L < −1.5% of portfolio |

### Opus Review Call (`portfolio_manager.run_portfolio_review()`)
- **Model:** `claude_model_alpha` (Opus, 8192 tokens)
- **Input per position:** entry thesis, per-dimension signal deltas (entry → current) with IC-weight annotations, setup type, pattern, regime at entry vs now, P&L %, open time, news context, earnings proximity
- **Output per position:** `{symbol, action ∈ {HOLD, TRIM, EXIT, ADD}, reasoning}`
- TRIM includes `trim_pct` (25/50/75)

### PM Action Execution
- **EXIT:** `execute_sell()` (stock) or `execute_sell_option()` → market sell, logged to `learning.log_trade()`
- **TRIM:** Partial sell — reduces qty/contracts by `trim_pct`
- **ADD:** `execute_add_to_position()` → position sizing via `calculate_position_size()` (same function entries use). Hard gates: risk conditions, earnings 48h window, single-position cap. If any gate fails → downgrade to HOLD
- **HOLD:** No action, logged to audit

### PM State Tracking (dedup)
- `_pm_reviewed_regime[symbol]` → prevents re-reviewing same position in same regime
- `_last_pm_review_ts_by_symbol[symbol]` → cooldown timer
- `_trimmed_today` → once-only TRIM gate per symbol per session

---

## STEP 12 — OPTIONS SCANNING
**File:** `options_scanner.py`, `bot_trading.py` ~lines 1410–1420

- **Scope:** Top 20 scored symbols + favourites
- **Per contract:** IV rank, earnings proximity, put/call ratio, unusual volume flags
- **Output:** `options_signals` list — injected into Trading Analyst (Opus) prompt

*Runs before the agent pipeline — feeds Opus context, not the execution layer directly*

---

## STEP 13 — 4-AGENT PIPELINE
**File:** `agents.py`, entry `run_all_agents()` line 79

### Early Gates (lines 119–142)
- If max positions reached → agents run for EXIT/ROTATION review only (no new buys)
- If 0 signals above threshold AND no positions to reconsider → agents skipped entirely (saves LLM call)

### Signal Ordering (lines 144–156)
- Unheld candidates surfaced before held ones in the signal list
- Prevents Opus from re-proposing already-held names

### Agent 1 — Technical Analyst (deterministic)
**Function:** `agent_technical()` line 511
- Input: top 15 scored signals + regime (VIX, SPY price, above/below 200d MA)
- Output: text report, per-symbol technical conviction ranking (HIGH/MEDIUM/LOW)

### Agent 2 — Trading Analyst / Opus (LLM) — PARALLEL WITH AGENT 1
**Function:** `agent_trading_analyst()` line 665
- **Model:** `claude_model_alpha` (Opus, 8192 tokens)
- **Input:** regime, account state, open positions, top 50 scored signals (fresh-first), options flow (top 8), catalyst candidates, news headlines, overnight research, voice memos (`data/voice_memos.md`)
- **Output:** MACRO vote (BULLISH/BEARISH/NEUTRAL), OPPORTUNITY list, CAUTION list
- **Hard constraint:** Only recommend symbols in the scored universe. Do not recommend held positions.

### Agent 3 — Risk Manager (deterministic, VETO POWER)
**Function:** `agent_risk_manager()` line 861
- Per symbol: APPROVE (+1) / REJECT (−1)
- Gates: single-position cap (15%), sector cap (50%), correlation (0.75), cash floor (10%), earnings 48h block

### Agent 4 — Trade Synthesiser (deterministic)
**Function:** `agent_final_decision()` line 224
- Vote tally: Technical (+1) + Macro (direction-aware) + Opportunity (+1) + Risk Manager (+1/−1)
- Threshold: ≥ 3 (paper) / ≥ 4 (live) votes to proceed
- Hard gates: already held → SKIP; direction mismatch → SKIP
- Instrument: score ≥ 35 → options (ATM δ~0.50, 30–45 DTE); score < 35 → stock
- Sizing: `(portfolio × risk_pct) / (ATR × atr_stop_multiplier)`, clamped to 15%, macro halving (0.5× within 24h of FOMC/CPI/NFP)
- Stops: SL = entry − (ATR × 1.0), TP = entry + (~2.67× ATR)

---

## STEP 14 — ORDER EXECUTION
**Files:** `bot_trading.py` ~lines 1938–2040+, `orders_core.py`

### BUY pre-flight checks (in order):
1. Alpaca halt status check
2. Bid-ask spread < 0.3%
3. Per-symbol thread lock
4. Already in `active_trades`? → skip
5. In `recently_closed` (< 30 min)? → skip
6. Failed thesis cooldown (< 4h)? → skip
7. Duplicate open-order check via IBKR

### BUY execution:
- Contract qualification → real-time price from IBKR
- **Bracket OCO:** Entry limit at ask + Stop at SL + Target limit at TP (all linked)
- Insert to `active_trades[symbol]` → log to `data/trades.jsonl` + `learning.log_trade()`

### SELL execution:
- Cancel SL → market sell → log P&L + exit reason

---

## STEP 15 — BACKGROUND THREADS (always running)

| Thread | Trigger | Action |
|--------|---------|--------|
| **Momentum Sentinel** | SPY > 0.3% in 3 min or > 0.6% in 10 min | Forces immediate scan (15 min cooldown) |
| **News Sentinel** | Held symbol keyword_score spike ≥ 5 | Triggers PM review flag |
| **Catalyst Engine** | Every 4h | Screens ~3,000 symbols → `catalyst/candidates_*.json` |
| **Catalyst Sentinel** | Polls every 4h | Routes high-conviction hits to Risk Manager |

---

## STEP 16 — EOD / WEEKLY OPERATIONS (time-triggered)

- **EOD (once/day at close):** Options exit review — expired or gain-target contracts
- **Pre-close:** `_maybe_generate_overnight_research()` → FMP earnings calendar, analyst changes, macro releases → `data/overnight_research.json` (injected into next-day Opus prompt)
- **Weekly (Monday):** Rolling Sharpe, win rate, directional skew per trade type → `data/weekly_review.txt` injected into Opus memory

---

## COMPLETE EXECUTION SEQUENCE (ONE SCAN CYCLE)

```
[BOOT] Housekeeping: kill switch, pause, hot-reload, recently_closed cleanup

[S1]  Universe assembly: Tier A (ETFs+mega-caps) + Tier B (promoted-50) + Tier C (sectors+held+favs+sympathy)
      ↓
[S2]  Market regime detection (VIX-proxy + optional Hurst/HMM vote)
      ↓
[S3]  Risk gate check → ABORT SCAN if failed
      ↓
[S4]  Strategy mode calculation (NORMAL/DEFENSIVE/RECOVERY)
      ↓
[S5]  Position lifecycle: trailing stops, tranches, option exits, mark-to-market
      ↓
[S6]  Pre-scoring sentiment: news sentiment (all symbols) → social sentiment (market hours only)
      ↓
[S7]  10-dimension scoring, parallel (ThreadPoolExecutor) across all universe symbols
      ↓
[S8]  Score filtering: regime threshold + strategy adj + IC gate + short quality + persistence + candle gate
      ↓
[S9]  IC audit log: full universe written to ic_audit.jsonl BEFORE agent pipeline
      ↓
[S10] Lightweight cycle check: deterministic per-position thesis validity and regime flip flags
      ↓
[S11] Portfolio Manager (EVENT-TRIGGERED, Opus): HOLD / TRIM / EXIT / ADD on open positions
      ↓
[S12] Options scanning (top 20 scored + favs) — output fed to Opus prompt
      ↓
[S13] 4-Agent pipeline:
       ├─ Agent 1 (Technical, deterministic) ─┐  PARALLEL
       ├─ Agent 2 (Opus, LLM)                ─┘
       ├─ Agent 3 (Risk Manager, deterministic, VETO)
       └─ Agent 4 (Trade Synthesiser: vote tally, sizing, instrument selection)
      ↓
[S14] Order execution:
       ├─ BUYs: pre-flight checks → contract qual → sizing → bracket OCO to IBKR → log
       └─ SELLs: cancel SL → market sell → log P&L

[BACKGROUND] Catalyst engine (4h), Momentum sentinel, News sentinel, Catalyst sentinel
[EOD/WEEKLY] Options review, overnight research, weekly IC/Sharpe summary
```

---

## KEY THRESHOLDS (paper vs live)

| Config key | Paper | Live | Purpose |
|------------|-------|------|---------|
| `min_score_to_trade` | 14 | 28 | Base signal gate |
| `agents_required_to_agree` | 3 | 4 | Consensus votes needed |
| `score_persistence_scans` | 0 | 2 | Persistence cycles |
| `risk_pct_per_trade` | 0.5% | 0.5% | Position sizing |
| `max_single_position` | 15% | 10% | Name cap |
| `max_sector_exposure` | 50% | 40% | Sector cap |
| `min_cash_reserve` | 10% | 10% | Cash floor |
| `daily_loss_limit` | 10% | 10% | Circuit breaker |
| `atr_stop_multiplier` | 1.0 | 1.0 | Stop distance |
| `correlation_threshold` | 0.75 | 0.75 | Corr gate |
| `reentry_cooldown_minutes` | 30 | 30 | Re-entry cooldown |
| `scan_interval_prime` | 3 min | 5 min | Scan frequency |
| Options score threshold | ≥ 35 | ≥ 35 | Options vs stock routing |

---

## KNOWN OUT-OF-ORDER OBSERVATIONS

1. **PM runs BEFORE new-signal agents** — existing positions managed first; agents then operate only on fresh candidates
2. **IC audit runs before agents** — captures all signal data regardless of trade outcome
3. **Position lifecycle runs at scan TOP** — before scoring, so prices are fresh for PM review inputs
4. **Options scan runs before agents** — feeds the Opus prompt; not the execution layer directly
5. **Sentiment runs before scoring** — it is input to Dimensions 7/8, not a post-score overlay
6. **Catalyst engine is fully decoupled** — publishes on its own 4h thread; main loop ingests the file at prompt assembly time only
7. **Sympathy scanner fires in pre-scoring phase** — dynamically extends the universe before scoring begins
