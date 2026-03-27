# Decifer Trading — Decision Log

> Every significant design decision, parameter change, or architectural choice gets logged here with the reasoning. This is the "why" behind the "what."
>
> Format: Date → Decision → Context / Reasoning

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
