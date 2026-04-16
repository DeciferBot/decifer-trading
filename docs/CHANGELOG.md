# Decifer Trading — Changelog

All notable changes to the Decifer trading system are documented here.
Format: newest entries at the top. Each entry includes the date, what changed, and why.

---

## v2.0.0 — 2026-04-16 — "Paper Alpha" (Version 2 baseline)

This release marks the official v2 baseline. The system has been rebuilt from first principles since
the v1 series. The architecture, signal engine, agent pipeline, and data infrastructure are now stable
enough to begin treating paper trades as real training data.

### Signal Engine — 10 Dimensions (up from 8)
- **Added**: Mean-Reversion dimension (#9) — ADF-gated (p < 0.05); scores VR, OU speed, Z-score. Zero score if ADF fails — no exceptions.
- **Added**: Overnight Drift dimension (#10) — captures gap opens and pre-market momentum.
- **Changed**: All 10 dimensions are orthogonal (no overlapping oscillators). Each measures a fundamentally different market phenomenon.

### Direction-Agnostic Scoring
- **Changed**: Signal engine scores setup *conviction*, not direction. Bearish and bullish setups score identically. The long/short ratio is determined by the market, not by regime-switched prompts.
- **Added**: Short-candidate scanner. Bearish exposure uses inverse ETFs (SPXS, SQQQ, UVXY) — no borrow, no margin.

### 4-Agent Pipeline
- **Changed**: Devil's Advocate removed. Trading Analyst (Opus) sees all data simultaneously — eliminates anchoring bias.
- **Changed**: Paper threshold = 3/4 agents agree (aggressive, for data generation). Live threshold stays 4/4.
- **Added**: Risk Manager hardcoded veto — earnings-48h block, single-position cap clamp, check_risk_conditions gate.
- **Added**: PM ADD verb — Portfolio Manager can now emit ADD/TRIM/EXIT/HOLD. Opus decides; `calculate_position_size()` sizes ADDs.

### Three-Tier Universe
- **Removed**: TradingView Screener dependency ripped out entirely.
- **Added**: Committed universe — top-1000 symbols by dollar volume, weekly refresh.
- **Added**: Dynamic tier — catalyst hits, held positions, favourites, sympathy plays bypass the committed-universe gate.

### Catalyst Engine
- **Added**: `catalyst_engine.py` — scores EDGAR filings, earnings surprises, analyst actions in real-time.
- **Added**: High-conviction catalyst hits receive a flat score boost to clear `min_score_to_trade`.
- **Wired into**: main signal engine + Chief Decifer dashboard.

### Regime Detector (locked)
- **Added**: VIX-proxy + SPY EMA hard classifier: BULL_TRENDING / BEAR_TRENDING / CHOPPY / PANIC.
- **Locked**: HMM explicitly deferred. Gate: ≥200 closed trades + IC Phase 2 review complete. `PRODUCTION_LOCKED = True`.

### IC Scoring (active)
- **Added**: Information Coefficient tracking is running. Gate for Phase C = 200 closed trades.

### Data Infrastructure
- **Replaced**: yfinance → Alpaca Algo Trader Plus (primary, real-time).
- **Added**: FMP Financial Modeling Prep (primary for fundamentals/events — analyst consensus, insider trades, earnings, DCF).
- **Added**: Three-tier data source priority enforced in code: Alpaca → FMP → Alpha Vantage → IBKR → yfinance.

### Execution
- **Added**: Smart execution threshold — TWAP/VWAP/Iceberg for orders >$10K or >500 shares. Simple limit below that.
- **Added**: News Sentinel — 3-agent fast pipeline (15-30s window). Position sizing 0.75× to compensate for lighter analysis.

### Stability
- **Added**: Config validation on startup — all required keys checked before any trading logic runs.
- **Added**: Rotating log handler — 50MB per file, 10 backups (500MB ceiling). Prevents log OOM.
- **Added**: fd soft limit raised to 4096 on boot.
- **Fixed**: VIX 1h change unit mismatch in regime detector.
- **Fixed**: EXTREME_STRESS circuit breaker removed (was incorrectly blocking valid PANIC-regime trades).

### Release Infrastructure
- **Added**: `scripts/bump-version.sh` — one-command release: updates version.py, gates on CHANGELOG edit, commits, tags, pushes.
- **Added**: Version logged to `decifer.log` on every startup.

---

## 2026-03-26 — Documentation Overhaul (Rebuild Guide)

### README.md — Complete Rewrite
- **Changed**: README.md rewritten as the master rebuild guide. Now covers every prerequisite, dependency, file, startup sequence, and troubleshooting step needed to reconstruct the entire system from scratch.
- **Added**: Complete dependency table with package → file → purpose mapping
- **Added**: System-level dependencies (brew install ta-lib)
- **Added**: Bot startup sequence (10-step boot order)
- **Added**: IBKR connection details and paper-to-live switching guide
- **Added**: Troubleshooting section (scikit-learn path issues, yfinance bugs, IBKR reconnection, TA-Lib install)

### requirements.txt — Annotated with Full Context
- **Changed**: Every dependency now has a comment showing which files use it and why
- **Added**: Header with install command and system prerequisite note
- **Organized**: Grouped by category (IBKR, AI, Market Data, Numerical, ML, NLP, HTTP, Utilities)

### docs/CONFIG_GUIDE.md — Complete Parameter Reference
- **Added**: ML Engine section (ml_enabled, ml_min_trades, ml_retrain_interval, ml_confidence_weight, ml_models_dir)
- **Added**: Social Sentiment section (data sources, scoring method, VADER lexicon)
- **Added**: Data Collector section (symbols, storage, features)
- **Added**: Logging section (log_file, trade_log, order_log paths)
- **Added**: Market Hours section (all EST time boundaries)
- **Added**: Inverse ETFs section (SPXS, SQQQ, UVXY)
- **Fixed**: Options values updated to match actual config.py (delta_range 0.35, min_volume 25, min_oi 100, max_spread 35%)

### docs/STRATEGY.md — Full System Behavior
- **Changed**: Signal dimensions updated from 7 to 8 (added Social dimension #8)
- **Changed**: Score range updated from 0-50 to 0-60
- **Added**: Dynamic regime thresholds table with formulas
- **Added**: Social Sentiment Engine section (data sources, velocity tracking, VADER lexicon)
- **Added**: ML Learning Loop section (components, training pipeline, score enhancement)
- **Added**: Smart Execution section (TWAP, VWAP, Iceberg)
- **Added**: Portfolio Optimizer section (correlation, VaR, sector monitoring)
- **Added**: IBKR Streaming section (quotes, bar aggregation, smart routing)
- **Added**: Backtester section (walk-forward, parameter sweeps)

### docs/DECISIONS.md — Architectural Rationale
- **Added**: Phase 2-5 decisions (social sentiment velocity vs raw count, ML walk-forward validation, TWAP/VWAP thresholds, correlation-aware sizing, IBKR streaming connection sharing)

---

## 2026-03-26 — Phase 2-5: Full Feature Build

### Backtesting Engine (backtester.py) — NEW FILE (844 lines)
- **Added**: Walk-forward backtesting engine that replays signals against historical data from `data_collector.py`.
- **Features**: Bar-by-bar signal scoring, ATR-based stops/trailing stops/partial exits, portfolio state tracking, performance reporting (Sharpe, max drawdown, profit factor, win rate, regime breakdown, monthly returns), and parameter sweeps for optimization.
- **Usage**: `python3 backtester.py --symbols AAPL TSLA --start 2024-01-01 --end 2025-12-31`

### Social Sentiment Module (social_sentiment.py) — NEW FILE (744 lines)
- **Added**: Reddit + ApeWisdom + VADER sentiment analysis for trading signals. Tracks mention VELOCITY (acceleration), not raw count. A spike from 5→50 mentions/hr is a signal; 50 steady mentions is not.
- **Sources**: Reddit JSON API (no auth, r/wallstreetbets + r/stocks + r/options), ApeWisdom pre-computed mentions, VADER with 90-word finance lexicon.
- **Integration**: Plugged into signals.py as 8th scoring dimension (social_score 0-10). Background polling thread updates every 60 seconds.
- **Fallback**: If NLTK not installed, uses keyword-based sentiment scorer.

### ML Learning Loop (ml_engine.py) — NEW FILE (949 lines)
- **Added**: Machine learning pipeline using scikit-learn (no GPU, no paid APIs).
- **Components**: TradeLabeler (labels WIN/LOSS from trades.json with 17+ features), DeciferML (RandomForest classifier + GradientBoosting regressor), SignalEnhancer (0.5x–1.5x multiplier on base score), RegimeClassifier (ML-based regime detection), WeeklyReportGenerator.
- **Walk-forward cross-validation** prevents lookahead bias. Models saved to `data/models/`.
- **Config**: `ml_enabled`, `ml_min_trades` (50), `ml_retrain_interval` (168h), `ml_confidence_weight` (0.3).

### Smart Execution (smart_execution.py) — NEW FILE (834 lines)
- **Added**: TWAP, VWAP, and Iceberg order execution for large orders.
- **TWAP**: Splits orders into N slices over time, auto-adjusts price if unfilled after 30s.
- **VWAP**: Weights slices by historical volume profile (heavier at open/close).
- **Iceberg**: Shows 10-20% of total order, auto-refills on fill.
- **Execution Analytics**: Tracks slippage vs arrival price, implementation shortfall, fill rates.
- **Threshold**: Orders >$10K notional or >500 shares use smart execution; smaller use simple limit.

### Portfolio Optimizer (portfolio_optimizer.py) — NEW FILE (871 lines)
- **Added**: Correlation-aware position sizing and portfolio construction.
- **CorrelationTracker**: Rolling 60-day correlation matrix with 30-min cache, clusters correlated stocks (>0.7).
- **RiskParitySizer**: Inverse-volatility weighting for equal risk contribution.
- **PortfolioVaR**: Historical VaR, parametric VaR, Conditional VaR (Expected Shortfall).
- **SectorMonitor**: Auto-detects sectors via yfinance, dynamic caps by regime (30% normal, 20% choppy, 15% panic).
- **Rebalancing**: Trims positions >2x target weight, adds to <0.5x, closes negligible.

### IBKR Streaming Data (ibkr_streaming.py) — NEW FILE (665 lines)
- **Added**: Real-time streaming market data from IBKR to supplement/replace yfinance polling.
- **StreamingQuotes**: `reqMktData()` with free delayed data (reqMarketDataType=3).
- **BarAggregator**: Converts 5-second real-time bars into 1-min and 5-min bars.
- **SmartDataRouter**: Transparent routing — IBKR streaming → IBKR historical → yfinance fallback.
- **Subscription management**: Max 100 concurrent (IBKR paper limit), LRU eviction, auto-resubscribe on reconnect.

### Signal Engine Upgrade (signals.py)
- **Changed**: Scoring engine upgraded from 7 to 8 dimensions. Added social_score (0-10) as dimension #8.
- **Changed**: `compute_confluence()`, `fetch_multi_timeframe()`, `_fetch_one_process()`, `score_universe()` all updated to pass social_data through the pipeline.

### Bot Integration (bot.py)
- **Changed**: Scan loop now fetches social sentiment before scoring, passes to `score_universe()`.
- **Added**: Social sentiment polling startup on boot.
- **Added**: ML engine initialization on boot (if ml_enabled=True).
- **Changed**: Log messages updated to reflect 8-dimension scoring.

### Dependencies (requirements.txt)
- **Added**: `scikit-learn>=1.3.0`, `joblib>=1.3.0`, `nltk>=3.8.0` (optional, has fallback).

### Documentation
- **Added**: `docs/ML_ENGINE_GUIDE.md`, `docs/ML_ENGINE_SUMMARY.md`, `docs/ML_ENGINE_QUICKSTART.txt`
- **Added**: `docs/SOCIAL_SENTIMENT_README.md`, `docs/SOCIAL_SENTIMENT_QUICKSTART.md`, `docs/SOCIAL_SENTIMENT_INTEGRATION.md`

---

## 2026-03-26 — Phase 1: Speed + Data Generation + Historical Collector

### Scan Speed Overhaul (signals.py)
- **Changed**: `score_universe()` now uses `ProcessPoolExecutor` instead of `ThreadPoolExecutor(max_workers=1)`. Each worker is a separate process with its own memory space, completely bypassing yfinance's thread-safety bug (GitHub issue #2557). Lazy-initialized reusable pool with automatic fallback to sequential if multiprocessing fails.
- **Impact**: Scoring time drops from ~180–240 seconds (sequential) to ~30–60 seconds (parallel) — a 3–5x speedup on the single biggest bottleneck.
- **Changed**: Regime-specific thresholds in `score_universe()` are now dynamically derived from `min_score_to_trade` config instead of being hardcoded. Paper trading config automatically loosens all regime gates.

### TV Pre-Filter Widened (bot.py)
- **Changed**: Hard kill thresholds loosened for paper trading data generation:
  - RSI dead zone narrowed from 42–58 → 47–53 (allows mean-reversion setups)
  - Relative volume floor from 1.0 → 0.5 (allows early breakouts before volume confirms)
  - Price change floor from 0.3% → 0.1% (allows slow accumulation plays)
  - Top-N candidates increased from 15 → 25 (wider funnel into scoring)
- **Why**: Original thresholds were designed for conservative live trading. For paper trading, we need maximum trade volume across different setups and regimes to build ML training data.

### Paper Trading Config (config.py)
- **Changed**: All risk/scoring/scan parameters tuned for maximum trade generation:
  - `min_score_to_trade`: 28 → 18 (captures weaker setups for training data)
  - `agents_required_to_agree`: 3 → 2 (more trades pass consensus)
  - `max_positions`: 12 → 20 (more concurrent positions)
  - `scan_interval_prime`: 5 → 3 min (faster scan cycles)
  - `scan_interval_standard`: 10 → 5 min
  - `daily_loss_limit`: 6% → 10% (paper can absorb more)
  - `consecutive_loss_pause`: 5 → 8 (fewer pauses)
  - `risk_pct_per_trade`: 4% → 3% (smaller per-trade with more positions)
  - `min_cash_reserve`: 10% → 5%
- **All original live values preserved in inline comments** for easy revert.

### Historical Data Collector (data_collector.py) — NEW FILE
- **Added**: Standalone module for downloading historical OHLCV data from free sources (yfinance daily + 5m intraday, Stooq daily backup, optional Alpha Vantage).
- **Features**: 60+ symbol default universe across all sectors, Parquet storage with append/dedup, pre-computed ML features (returns, ATR, EMAs, RSI, MFI, BB position, VWAP distance, volume ratio, regime labels), CLI with `--symbols`, `--daily-only`, `--intraday-only` flags.
- **Why**: Decifer had no backtesting data. This module builds the training dataset needed for future ML model development.

### Documentation & Dependencies
- **Updated**: README.md fully rewritten with current architecture, parallel scoring docs, data collector usage, paper-vs-live config table.
- **Updated**: All docs/ markdown files (CHANGELOG, CONFIG_GUIDE, STRATEGY, DECISIONS).
- **Added**: `pyarrow>=14.0.0` to requirements.txt for Parquet format support.

---

## 2026-03-25 — News Sentinel & Theme Tracker

- **Added**: `news_sentinel.py` — Real-time news monitoring engine running as an independent background thread. Polls Yahoo RSS, Finviz, and IBKR news API every 45 seconds. Includes headline deduplication, per-symbol cooldowns, and a materiality filter (keyword scoring + Claude deep-read) to determine which headlines warrant immediate action.
- **Added**: `sentinel_agents.py` — Lightweight 3-agent pipeline (Catalyst Analyst → Risk Gate → Instant Decision) for news-triggered trades. Runs in ~15–30 seconds vs. 5–10 minutes for the full 6-agent pipeline. Outputs BUY/SELL/HOLD/SKIP with confidence scoring.
- **Added**: `theme_tracker.py` — Three-layer universe builder for the sentinel. Auto-detects themes from current holdings, provides 9 predefined market themes (AI/Semis, EV, Biotech, Fintech, Defense, Clean Energy, Tariffs, Healthcare Disruptors), and dynamically discovers trending themes from market headlines. Supports custom themes via `add_custom_theme()` with persistence to `data/custom_themes.json`.
- **Added**: 12 new `sentinel_*` settings in `config.py` covering poll frequency, cooldowns, materiality thresholds, rate limits, position sizing, and source toggles.
- **Modified**: `bot.py` — Sentinel launches as a daemon thread after the first scan completes. Trigger handler runs the 3-agent pipeline and executes via existing `execute_buy`/`execute_sell`. Sentinel state synced to dashboard. Hot-reload support added for all new modules.
- **Why**: The scan loop runs every 5–60 minutes, which means material news (earnings beats, FDA approvals, tariff announcements) could move a stock significantly before the next scan picks it up. The sentinel eliminates this latency by monitoring news continuously and firing immediately when it detects a material catalyst.

---

## 2026-03-25 — Git Initialized & Documentation System

- **Added**: Git version control with full history tracking
- **Added**: Markdown-based documentation system alongside existing Word docs
- **Why**: Enables rollback to any prior state, diffable doc history, and a single source of truth that stays in sync with code changes

## 2026-03-25 — v3 Baseline (Initial Commit)

This is the rollback baseline. The codebase at this point includes:

- 6-agent Claude pipeline (Technical, Macro, Opportunity, Devil's Advocate, Risk, Decision Maker)
- Signal engine: 6 dimensions (Trend, Momentum, Squeeze, Flow, Breakout, Confluence)
- IBKR integration: stocks + options execution with OCO brackets
- Risk management: 5-layer system (position sizing, daily loss limit, drawdown alerts, sector caps, cash reserve)
- Live dashboard on port 8080
- Options trading: delta targeting, IV rank filtering, Greeks analysis
- Dynamic universe scanning via TradingView Screener
- Learning module: trade logging, performance tracking, weekly review
- Hot-patch utility for zero-downtime updates
