# Decifer Trading — Strategy & Logic Reference

> This document tracks the **current** trading logic. When we change how Decifer makes decisions, this doc gets updated in the same commit. For the full history, use `git log docs/STRATEGY.md`.

---

## Agent Pipeline

Decifer runs **6 Claude agents** on every scan cycle (agents 1+2 run in parallel, then 3-6 sequentially). A trade requires agreement from at least **2 of 6** agents in paper mode, **4 of 6** in live mode (configurable via `agents_required_to_agree`).

| # | Agent | Role | Can Veto? |
|---|-------|------|-----------|
| 1 | Technical Analyst | Price action, volume, indicators across 5m / 1D / 1W | No |
| 2 | Macro Analyst | Regime classification, VIX, cross-asset dynamics, news | No |
| 3 | Opportunity Finder | Synthesises technical + macro + options flow → top 3 trades | No |
| 4 | Devil's Advocate | Argues against every proposed trade | No |
| 5 | Risk Manager | Position sizing, portfolio-level risk checks | **Yes** |
| 6 | Final Decision Maker | Outputs executable JSON trade instructions | No |

## Signal Dimensions (signals.py)

Each dimension measures something independent — no redundant oscillators. 8 dimensions total.

| # | Dimension | Indicator | Score Range | What It Measures |
|---|-----------|-----------|-------------|-----------------|
| 1 | **Trend** | EMA alignment (9/21/50) + ADX strength | 0-10 | Direction and strength of trend |
| 2 | **Momentum** | MFI (volume-weighted RSI) | 0-10 | Buying/selling pressure with volume confirmation |
| 3 | **Squeeze** | Bollinger Bands inside Keltner Channel | 0-10 | Volatility compression (coiled spring) |
| 4 | **Flow** | VWAP position + OBV divergence | 0-10 | Institutional money flow |
| 5 | **Breakout** | Donchian channel breach + volume | 0-7 | Price range expansion with participation |
| 6 | **Confluence** | Multi-timeframe agreement | 0-5 | Same signal across 5m, 1D, 1W |
| 7 | **News** | Yahoo RSS keyword scoring + Claude sentiment | 0-5 | Breaking news impact |
| 8 | **Social** | Reddit/ApeWisdom mention velocity + VADER | 0-10 | Social media attention acceleration |

## Scoring

- **Score range**: 0–60 (8 dimensions plus +3 candlestick bonus)
- **Minimum to trade**: 18 paper / 28 live (`min_score_to_trade`)
- **High conviction**: 30+ paper / 38+ live (`high_conviction_score`) → 1.5x position size
- **Regime-specific thresholds**: Dynamically derived from `min_score_to_trade` — not hardcoded. PANIC regime always blocks at 99.

### Dynamic Regime Thresholds

Derived automatically from `min_score_to_trade` (base):

| Regime | Formula | Paper (base=18) | Live (base=28) |
|--------|---------|-----------------|-----------------|
| BULL_TRENDING | base | 18 | 28 |
| BEAR_TRENDING | max(15, base - 3) | 15 | 25 |
| CHOPPY | max(12, base - 6) | 12 | 22 |
| PANIC | 99 (always blocked) | 99 | 99 |
| UNKNOWN | max(15, base - 3) | 15 | 25 |

### Parallel Scoring (ProcessPoolExecutor)

`score_universe()` uses a `ProcessPoolExecutor` with up to 6 worker processes. Each process gets its own memory space, completely bypassing yfinance's thread-safety bug (GitHub issue #2557 — shared `_DFS` global dict causes cross-symbol data contamination in threads). The pool is lazily initialized and reused across scan cycles. If multiprocessing fails (e.g., fork issues on some OS configurations), it falls back to sequential scoring automatically.

**Performance**: ~30–60 seconds for 25 symbols (parallel) vs. ~180–240 seconds (old sequential).

## Risk Rules (Hardcoded — Agent-Proof)

These rules cannot be overridden by any agent. Values shown for paper trading mode; live values in parentheses.

| Rule | Paper | Live | Effect |
|------|-------|------|--------|
| Risk per trade | 3% | 4% | Max capital at risk in any single position |
| Max positions | 20 | 12 | Hard cap on simultaneous open positions |
| Daily loss limit | 10% | 6% | Bot halts for the day |
| Max drawdown alert | 25% | 15% | Alert + pause trading |
| Cash reserve | 5% | 10% | Always held in cash |
| Max single position | 10% | 15% | No position exceeds this % of portfolio |
| Max sector exposure | 50% | 40% | Diversification enforcement |
| Consecutive loss pause | 8 losses | 5 losses | 2-hour trading pause |
| Min reward:risk | 1.5:1 | 1.5:1 | Below this = skip the trade |

## Exit Strategy

- **Stop loss**: Entry − (1.5 × ATR)
- **Trailing stop**: 2 × ATR from high-water mark
- **Partial exit 1**: Sell 33% at +4%
- **Partial exit 2**: Sell 33% at +8%, trail remainder
- **Gap protection**: Exit if price opens 3% against position

## Options Logic

When `options_enabled: True` and stock score ≥ 35:

- **Target delta**: 0.50 (ATM for max leverage)
- **Delta range**: ±0.20 around target
- **DTE window**: 5–45 days
- **IV Rank cap**: 65 (IVR < 30 ideal, 30–50 fair, 50–65 acceptable for high conviction)
- **Liquidity**: min 50 volume, min 200 OI, max 25% bid-ask spread
- **Max risk**: 2.5% of portfolio per options trade (premium = max loss)
- **Profit target**: 100% premium gain
- **Stop loss**: 50% premium loss
- **Hard exit**: 2 DTE (gamma risk)

## VIX Regime

| VIX Range | Regime | Bot Behaviour |
|-----------|--------|---------------|
| < 15 | Bull trending | Normal trading |
| 15–25 | Choppy | Reduced sizing, tighter stops |
| 25–35 | Elevated | Defensive, inverse ETFs considered |
| > 35 | Panic | **No trades** — full halt |
| +20% spike in 1hr | Crisis | Exit all positions |

## Scan Intervals

| Session | Paper | Live | Time (EST) |
|---------|-------|------|------------|
| Prime | 3 min | 5 min | 9:45am–11:30am, 2pm–3:55pm |
| Standard | 5 min | 10 min | 11:30am–2pm (lunch lull) |
| Extended | 5 min | 7 min | 4am–9:30am pre-market, 4pm–8pm after-hours |
| Overnight | 30 min | 60 min | 8pm–4am (monitoring only) |

## TV Pre-Filter (bot.py)

Before the expensive multi-timeframe scoring, a pre-filter uses free TradingView Screener data to rank the universe. Current paper trading thresholds (live values in parentheses):

| Filter | Paper | Live | Purpose |
|--------|-------|------|---------|
| Recommendation | \|rec\| ≥ 0.05 | \|rec\| ≥ 0.1 | Minimum directional signal |
| Relative volume | ≥ 0.5 | ≥ 1.0 | Volume activity floor |
| RSI dead zone | 47–53 | 42–58 | Exclude flat momentum |
| Price change | ≥ 0.1% | ≥ 0.3% | Exclude totally flat stocks |
| Top-N | 25 | 15 | Candidates passed to scoring |

Symbols are ranked by `|Recommend.All| × relative_volume` with a 30% bonus for VWAP-confirmed direction. Favourites always bypass the pre-filter.

## Historical Data Collection (data_collector.py)

Standalone module for building ML training datasets from free sources:

| Source | Data Type | History | Rate Limit |
|--------|-----------|---------|------------|
| yfinance | 5m intraday | 60 days | ~1 req/sec |
| yfinance | Daily OHLCV | 20+ years | ~1 req/sec |
| Stooq | Daily OHLCV (backup) | Full history | Unlimited |
| Alpha Vantage | Daily OHLCV | 20 years | 25 calls/day (free) |

Data saved as Parquet with pre-computed features: returns (1/5/10 bar), ATR, EMA (9/21/50), RSI, MFI, Bollinger Band position, VWAP distance, volume ratio, and regime labels (BULL/BEAR/CHOPPY/PANIC).

---

## News Sentinel (Real-Time News Trigger)

The News Sentinel is an **interrupt-driven** system that runs independently of the scan loop. It monitors news in real-time and fires an immediate mini agent pipeline when material news is detected — no waiting for the next scan cycle.

### Architecture

```
Scan Loop (5-60 min)          News Sentinel (45s polls)
─────────────────────         ──────────────────────────
Full 6-agent pipeline         3-agent mini pipeline
~5-10 min per cycle           ~15-30 sec per trigger
Scores entire universe        Focuses on single symbol
Scheduled intervals           Event-driven (interrupt)
```

### News Sources

| Source | Method | Coverage | Latency |
|--------|--------|----------|---------|
| Yahoo Finance RSS | HTTP polling | All US equities | ~30s |
| Finviz | HTML scraping | All US equities | ~60s |
| IBKR News API | `reqHistoricalNews` | Benzinga, FlyOnTheWall, DowJones, MT, Briefing | Real-time |

### Materiality Filter

Not all news triggers a trade. A headline must pass the materiality filter:

1. **Keyword score ≥ 3** (absolute): Strong bullish or bearish language detected
2. **Strong keywords present**: Words like "surges", "crashes", "FDA approval", "bankruptcy"
3. **News cluster**: 3+ headlines about the same symbol within 2 hours = something is happening
4. **Claude deep-read**: Confirmed triggers get Claude sentiment analysis. Confidence ≥ 7 auto-upgrades urgency

Headlines older than 2 hours are ignored. Each symbol has a 10-minute cooldown to prevent rapid re-triggering.

### Sentinel Agent Pipeline (3 agents)

| # | Agent | Role | Time |
|---|-------|------|------|
| 1 | Catalyst Analyst | Assess news materiality, price impact direction/magnitude, durability | ~5-10s |
| 2 | Risk Gate | Portfolio-level risk check: slots, daily loss budget, sector exposure, regime | ~5-10s |
| 3 | Instant Decision | Synthesise into executable JSON: BUY / SELL / HOLD / SKIP | ~5-10s |

A sentinel trade executes only if the final decision confidence ≥ 5/10.

### Sentinel Trade Sizing

Sentinel trades use **0.75x** the normal position sizing (configurable via `sentinel_risk_multiplier`). This reflects higher uncertainty in news-driven trades vs. full technical analysis. All hardcoded risk limits still apply — the sentinel cannot bypass position caps, daily loss limits, or cash reserve requirements.

### Rate Limits

- **Max 3 sentinel trades per hour** (`sentinel_max_trades_per_hour`)
- **10-minute cooldown per symbol** (`sentinel_cooldown_minutes`)
- **Bot pause / kill switch respected** — sentinel stops trading if bot is paused

---

## Theme Tracker (Sector & Narrative Universe)

The Theme Tracker determines which symbols the News Sentinel monitors. It builds a prioritised universe of ~80 symbols using three layers:

### Layer 1: Auto-Detect from Holdings (highest priority)

Current open positions and favourites/watchlist symbols are always monitored. The system automatically detects which predefined themes overlap with holdings and adds related stocks.

### Layer 2: Predefined Themes

| Theme | Key Stocks | Keywords |
|-------|-----------|----------|
| AI & Data Infrastructure | NVDA, AMD, MSFT, GOOGL, AMZN, PLTR, SMCI | ai, gpu, data center, llm |
| Semiconductor Cycle | NVDA, AMD, INTC, MU, QCOM, AMAT, ASML | semiconductor, chip, wafer, memory |
| EV & Battery Tech | TSLA, RIVN, LCID, NIO, ALB, CHPT | electric vehicle, battery, lithium |
| Biotech & Pharma | ABBV, AMGN, MRNA, LLY, NVO, XBI | fda, clinical trial, drug, phase 3 |
| Fintech & Payments | V, MA, PYPL, SQ, COIN, SOFI | fintech, payment, crypto, bitcoin |
| Defense & Aerospace | LMT, RTX, NOC, PLTR, RKLB, ASTS | defense, military, pentagon, satellite |
| Energy Transition | FSLR, ENPH, CEG, VST, OKLO | solar, nuclear, renewable, hydrogen |
| Tariffs & Trade War | AAPL, TSLA, NKE, CAT, BABA, X | tariff, trade war, sanctions, reshoring |
| Healthcare Disruptors | HIMS, OSCR, TDOC, LLY, NVO | glp-1, telehealth, drug pricing |

### Layer 3: Trending Theme Discovery

Recent market headlines are scanned for theme keywords. The top 3 trending themes get their symbols boosted in the monitoring universe. This allows the sentinel to dynamically shift attention toward whatever narrative the market is focused on.

### Custom Themes

New themes can be added at runtime via `add_custom_theme()` and are persisted to `data/custom_themes.json`. Themes can be toggled on/off via `toggle_theme()`.

---

## Social Sentiment Engine (social_sentiment.py)

Tracks social media mention velocity and sentiment to feed dimension #8 of the signal engine.

### Data Sources (all free, no API key)

| Source | Method | What It Provides |
|--------|--------|-----------------|
| Reddit JSON API | HTTP GET to r/wallstreetbets, r/stocks, r/investing .json endpoints | Post titles, comments, upvotes |
| ApeWisdom | HTTP GET to apewisdom.io API | Aggregated mention counts across subreddits |
| VADER (NLTK) | Local NLP model | Sentiment polarity scores per mention |

### How It Works

1. Background thread polls every 60 seconds
2. For each watched symbol, count mentions across Reddit + ApeWisdom
3. Track **mention velocity** — rate of change in mentions, not raw count
4. Apply VADER sentiment with 90-word custom finance lexicon (e.g., "moon", "tendies", "short squeeze" = bullish; "bagholding", "dump", "scam" = bearish)
5. Output: social_score 0-10 per symbol

A stock going from 5 mentions/hour to 50 mentions/hour scores higher than one with a steady 100 mentions/hour. This catches early momentum before it peaks.

---

## ML Learning Loop (ml_engine.py)

Learns from completed trades to identify winning patterns and enhance future signal scores.

### Components

| Component | Class | What It Does |
|-----------|-------|-------------|
| Trade Labeler | `TradeLabeler` | Reads trades.json, labels each as win/loss |
| ML Models | `DeciferML` | RandomForest (classifier) + GradientBoosting (regressor) |
| Signal Enhancer | `SignalEnhancer` | Multiplies live scores by 0.5x-1.5x based on ML prediction |
| Regime Classifier | `RegimeClassifier` | ML-based regime detection (supplement to VIX rules) |

### Training Pipeline

1. Loads completed trades from `data/trades.json`
2. Builds feature matrix from signal scores, regime, technical indicators
3. Trains with `TimeSeriesSplit` cross-validation (walk-forward, no lookahead bias)
4. Saves models to `data/models/` as `.pkl` files via joblib
5. Retrains automatically every 168 hours (1 week)
6. Minimum 50 completed trades before first training

### Score Enhancement

When ML models are trained, `enhance_score()` adjusts the raw signal score:
- Predicted winner with high confidence → multiply by up to 1.5x
- Predicted loser with high confidence → multiply by down to 0.5x
- Low confidence → minimal adjustment (close to 1.0x)
- ML weight controlled by `ml_confidence_weight` (default 0.3 = 30% influence)

---

## Smart Execution (smart_execution.py)

Advanced order execution strategies for large positions:

| Strategy | How It Works | When Used |
|----------|-------------|-----------|
| TWAP | Slices order into equal parts over time | Large orders in liquid stocks |
| VWAP | Weights slices by historical volume profile | Minimize market impact |
| Iceberg | Hides total quantity behind visible portion | Avoid tipping off other algos |

Includes execution analytics: slippage measurement, implementation shortfall, fill quality tracking.

---

## Portfolio Optimizer (portfolio_optimizer.py)

Portfolio-level risk management beyond individual position sizing:

| Component | What It Does |
|-----------|-------------|
| CorrelationTracker | 60-day rolling correlation matrix, cached 30 min |
| RiskParitySizer | Correlation-aware position sizing (reduces correlated positions) |
| PortfolioVaR | Value-at-Risk calculation for overall portfolio |
| SectorMonitor | Tracks sector concentration, enforces dynamic caps by regime |

---

## IBKR Streaming (ibkr_streaming.py)

Real-time market data from Interactive Brokers (supplements yfinance polling):

| Feature | Details |
|---------|---------|
| Real-time quotes | Bid/ask/last/volume/VWAP via `reqMktData()` |
| Bar aggregation | 5-second ticks aggregated into 1-min and 5-min bars |
| Smart data routing | IBKR data preferred, falls back to yfinance |
| Connection sharing | Uses same IB connection as orders (avoids Error 10197) |
| Subscription limit | Max 100 concurrent subscriptions, LRU eviction |

---

## Backtester (backtester.py)

Walk-forward backtesting engine for validating strategy changes:

| Feature | Details |
|---------|---------|
| Mode | Bar-by-bar replay of historical data |
| Signals | Uses same `compute_indicators()` + `compute_confluence()` from signals.py |
| Risk | Applies same position sizing, max positions, stop rules |
| Exits | ATR stops, partial exits, trailing stops |
| Reporting | Sharpe ratio, max drawdown, profit factor, win rate |
| Optimization | Parameter sweep across key variables |

Run: `python3 backtester.py --symbols AAPL TSLA NVDA --start 2024-01-01 --end 2025-01-01`
