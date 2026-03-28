# Decifer Trading — Product Definition

**Version:** 3.0 (Phase A complete)
**Last verified:** 2026-03-28
**Source of truth:** This document reflects what is actually running in the codebase. Every claim here was verified against git history and source code on 2026-03-28.

---

## What Decifer Is

Decifer is an autonomous paper-trading system that uses a 9-dimension signal engine and a 6-agent Claude AI pipeline to scan, score, and execute trades on Interactive Brokers. It runs on a free data stack (TradingView Screener, yfinance, Yahoo RSS, Reddit JSON API) and is designed for paper trading on the IBKR account DUP481326.

**It is not:**
- A live trading system (paper account only, confirmed)
- A black box — every decision is logged with reasoning
- A simple rule engine — agents debate every trade before execution

---

## System Architecture

### Three Actors

| Actor | Role |
|-------|------|
| **Amit** | Decision maker, domain expert, reviewer |
| **Cowork (Claude)** | Writes code, runs research, builds features |
| **Chief Decifer** | Read-only dashboard on port 8181. Never writes code. |

### The Scan Pipeline (every 3-5 minutes during market hours)

```
TradingView Screener (8 regime-weighted queries)
    ↓
Universe: 50-100 symbols (long + short candidates)
    ↓
TV Pre-Filter: rank by |signal| × relative volume
  Thresholds: RSI dead zone 47-53, volume floor 0.5×, change floor 0.1%
  Output: top 25 candidates
    ↓
News Sentiment (news.py): Yahoo RSS + keyword scoring + Claude confirmation
    ↓
Social Sentiment (social_sentiment.py): Reddit JSON + ApeWisdom + VADER
    ↓
9-Dimension Signal Scoring (signals.py): ProcessPoolExecutor, 5m/1D/1W timeframes
    ↓
Options Scan (options_scanner.py): unusual volume, IV rank, earnings plays
    ↓
6-Agent Claude Pipeline (agents.py): consensus gate → trade decision
    ↓
Execution (orders.py): multi-source price validation → IBKR bracket order
```

### The News Sentinel (independent, runs every 45 seconds)

A secondary pipeline that monitors 80 symbols + 9 themes for breaking news:

1. **Catalyst Analyst** — evaluates news materiality
2. **Risk Gate** — checks portfolio impact
3. **Instant Decision** — go/no-go in 15-30 seconds

Constraints: 10-minute cooldown between triggers, max 3 trades/hour, 0.75× position sizing.

---

## Signal Engine — 9 Dimensions (as of 2026-03-28)

**Architecture: Direction-Agnostic (Phase A, shipped 2026-03-28)**

Each dimension scores *conviction* (0-10, how strong is this setup?) and *direction* (+1 long / −1 short) independently. A weighted majority vote across all 9 dimensions determines the final trade direction. A clean bearish breakdown scores identically to the equivalent bullish setup.

| # | Dimension | Indicator | Max Score | Direction Logic |
|---|-----------|-----------|-----------|-----------------|
| 1 | **TREND** | EMA(9/21/50) alignment × ADX strength | 10 | Bull EMA alignment = long; bear alignment = short |
| 2 | **MOMENTUM** | MFI distance from 50 (symmetric) | 10 | MFI > 50 = long; MFI < 50 = short |
| 3 | **SQUEEZE** | Bollinger Bands inside Keltner Channels | 10 | Post-squeeze direction from BB position |
| 4 | **FLOW** | VWAP position + OBV divergence | 10 | Price > VWAP + rising OBV = long; inverse = short |
| 5 | **BREAKOUT** | Donchian channel breach + volume | 10 | 20-day high breach = long; 20-day low breach = short |
| 6 | **CONFLUENCE** | Multi-timeframe agreement (5m + 1D + 1W) | 10 | Agreement across timeframes amplifies direction |
| 7 | **NEWS** | Yahoo RSS keyword score + Claude sentiment | 10 | Positive news = long bias; negative = short bias |
| 8 | **SOCIAL** | Reddit mention velocity + VADER | 10 | Positive VADER + velocity = long; negative = short |
| 9 | **REVERSION** | Variance Ratio + OU Half-Life + Z-Score (ADF-gated) | 10 | Z-score deviation direction |
| + | Candlestick bonus | Pattern recognition | 3 | Direction-agnostic — adds to conviction |

**Total theoretical maximum:** 93 points
**Minimum to trade:** 18 (paper) / 28 (live)
**Regime adjustments:** threshold shifts by regime (see Regime Detection below)

### REVERSION Dimension Detail (shipped 2026-03-26)

ADF gate is the critical safety mechanism:
- ADF p < 0.05 → series is statistically mean-reverting → score sub-metrics
- ADF p ≥ 0.05 → REVERSION scores 0 regardless (prevents 32% false positive rate on random walks)

Sub-metrics (only scored if ADF passes):
- Variance Ratio (Lo-MacKinlay, k=5): VR < 0.55 = 3pts, < 0.70 = 2pts, < 0.80 = 1pt
- OU Half-Life (Ernie Chan method): < 5 bars = 4pts, < 10 = 3pts, < 20 = 2pts, < 30 = 1pt
- Z-Score from mean: |z| > 2.5 = 3pts, > 2.0 = 2pts, > 1.5 = 1pt

---

## Scanner — What Gets Scanned (as of 2026-03-28)

**8 TradingView Screener queries per scan cycle** (regime-weighted limits):

**Long candidate scans:**
1. Momentum longs — RSI 1h 50-70, MACD positive, EMA bullish
2. Volume breakouts — volume spike 2×+ average, price near highs
3. Pre-market movers — gap-up plays
4. Momentum shorts (RSI declining, MACD negative) ← also in long filter zone

**Short candidate scans (Phase A, shipped 2026-03-28):**
5a. Breakdown — price below EMA20 AND EMA50, bearish EMA alignment, ADX > 20
6a. Volume distribution — price down on 2×+ average volume
7a. Bearish momentum — RSI < 40, MACD bearish crossover (60m)
8a. Intraday breakdown — down > 3% from open

**Regime-weighted limits:**
- BEAR_TRENDING: short/breakdown scans run at full capacity (25-30 symbols each)
- CHOPPY: breakdown + distribution scans active (10-15 symbols each)
- BULL_TRENDING: short scans reduced (8-12 symbols each)

Also scans: inverse ETFs (SH, PSQ, SDS, SPXU, QID, SQQQ, SDOW, SRTY) for macro hedges.

---

## 6-Agent Claude Pipeline

Every scored candidate that clears the score threshold goes through all 6 agents:

| Agent | Role |
|-------|------|
| **Technical Analyst** | Price action, volume, indicators across 5m / 1D / 1W |
| **Macro Analyst** | Regime, VIX, cross-asset dynamics, news flow |
| **Opportunity Finder** | Synthesises tech + macro + options → top 3 trades |
| **Devil's Advocate** | Argues against every proposed trade |
| **Risk Manager** | Veto power, position sizing, portfolio-level risk |
| **Final Decision Maker** | Outputs executable JSON trade instructions |

**Model:** claude-sonnet-4-6
**Max tokens:** 800 per agent call
**Consensus gate:** 3/6 agents must agree to trade (paper). 4/6 for live. *(Changed from 2/6 — Phase A, 2026-03-28)*

---

## Regime Detection

Rule-based (VIX levels + SPY trend). HMM is not yet built.

| Regime | Condition | Score Threshold (paper) |
|--------|-----------|------------------------|
| BULL_TRENDING | VIX < 15 | 18 (base) |
| BEAR_TRENDING | VIX 15-35, SPY below EMA | 15 (base − 3) |
| CHOPPY | VIX 15-25, SPY near EMA | 12 (base − 6) |
| PANIC | VIX > 35 | 99 (no trades) |
| UNKNOWN | — | 15 (base − 3) |

---

## Risk Management (risk.py — 6 layers)

1. **Session loss limit** — halt if daily P&L < threshold
2. **Consecutive losses** — pause after N consecutive losses
3. **Position count** — max open positions cap
4. **Correlation check** — don't add correlated positions
5. **Combined exposure** — sector + total exposure limits
6. **Drawdown from peak** — circuit breaker on equity drawdown

**Hardcoded.** No agent can override risk rules.

---

## Directional Skew Tracking (shipped 2026-03-28)

`get_directional_skew(window_hours, regime)` in `learning.py` — calculates:
- `skew`: float from −1.0 (all short) to +1.0 (all long)
- `long_count`, `short_count`, `total` over rolling window
- Regime-aware alerts: heavy long in BEAR_TRENDING triggers warning; heavy short in BULL_TRENDING triggers warning

**Diagnostic only** — not fed back into agent prompts. Displayed on dashboard (port 8080) with skew bar, ratio, and alert panel.

Windows tracked: 48h and 7d.

---

## Supporting Systems

| Module | What It Does |
|--------|-------------|
| `ml_engine.py` | RandomForest + GradientBoosting trained on trade outcomes. Activates after 50 closed trades. Walk-forward cross-validation. |
| `backtester.py` | Walk-forward backtesting on historical data |
| `portfolio_optimizer.py` | Correlation tracking, risk parity sizing, VaR, sector monitoring |
| `smart_execution.py` | TWAP, VWAP, iceberg order execution for large orders |
| `ibkr_streaming.py` | Real-time IBKR data streaming |
| `data_collector.py` | Parquet-format trade feature collection for ML training |
| `social_sentiment.py` | Reddit JSON + ApeWisdom + VADER sentiment |
| `news_sentinel.py` | 3-agent interrupt pipeline for breaking news |
| `theme_tracker.py` | Tracks 9 predefined themes + emerging themes |
| `daily_journal.py` | Auto-generates daily trade summary markdown |

---

## IBKR Configuration

- **Paper account:** DUP481326
- **Port:** 4001 (IB Gateway) or 7496 (TWS)
- **Market data:** Delayed (type 3, free — 15-min delay)
- **Live accounts:** U3059777, U24093086 (not actively trading)

---

## Feature Roadmap Status (as of 2026-03-28)

### Phase A — Complete ✅

| Feature | Status | Shipped |
|---------|--------|---------|
| Direction-Agnostic Signals | ✅ Complete | 2026-03-28 |
| Short-Candidate Scanner | ✅ Complete | 2026-03-28 |
| Directional Skew Tracking | ✅ Complete | 2026-03-28 |
| Consensus Threshold → 3 | ✅ Complete | 2026-03-28 |
| Mean-Reversion Dimension (9th signal) | ✅ Complete | 2026-03-26 |

### Phase B / C / D — Not Yet Built

| Feature | Priority | Blocker |
|---------|----------|---------|
| Signal Validation (Alphalens / IC) | P2 | Needs 50+ trades per regime |
| HMM Regime Detection | P2 | Needs validation data |
| Walk-Forward Weight Calibration | P2 | Blocked on HMM + Alphalens |

---

## Known Issues (as of 2026-03-28)

1. **Test suite:** 301/815 tests failing (60% pass rate). Root cause: tests and code API diverged during rapid development. Tests use old function signatures. Does not affect runtime — trading bot works independently of test suite.

2. **bot.py:** Reconnect functions defined 3× (file appended to itself). Last definition wins — functionally correct but should be cleaned up.

3. **README.md:** Says "8 dimensions" — actually 9 (REVERSION was added). README needs updating.

4. **spec files:** Were not tracking shipped features — updated 2026-03-28 via this audit.

---

## Data Stack (all free)

| Source | Used For |
|--------|----------|
| TradingView Screener | Universe scanning (no API key) |
| yfinance | OHLCV data, options chains |
| Yahoo RSS | News headlines |
| Reddit JSON API | Social sentiment |
| ApeWisdom | Retail mention tracking |
| Finviz | News + sector data |
| IBKR paper | Order execution, portfolio data |
| Anthropic API | Claude agents (cost: ~$0.02/trade) |

---

*This document is the authoritative product definition for Decifer Trading v3. Update it whenever a feature ships or the architecture changes. Do not let it drift from the code.*
